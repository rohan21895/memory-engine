import { resolve } from "node:path";

import type { EDL, JobError, JobSpec } from "../../../contracts/codegen/generated/typescript/index.js";

import { canonicalJson, digestBytes } from "./digest.js";
import { parseEncodeProfile, type EncodeProfile } from "./encode.js";
import { asRenderVideoError, RenderVideoError } from "./errors.js";
import { publishRenderOnce, renderVideo, type RenderVideoResult } from "./renderer.js";
import type { SourceResolver } from "./sources.js";

export const RENDER_VIDEO_CHECKPOINT_VERSION = 1;

export interface RenderVideoJobParams {
  output_path: string;
  work_directory: string;
  /** media_id -> ordered local paths. One path, unless the ref is a span assembly. */
  sources: SourceResolver;
  encode: EncodeProfile;
  ffmpeg_path?: string;
  ffprobe_path?: string;
}

export interface RenderVideoJobDependencies {
  persist: (job: JobSpec) => Promise<void>;
  now?: () => string;
}

function paramsDigest(params: Record<string, unknown>): string {
  return digestBytes(new TextEncoder().encode(canonicalJson(params)));
}

function parseSources(value: unknown): SourceResolver {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new RenderVideoError("validation_failed", "render_video params.sources must be a media_id map.");
  }
  const resolver: SourceResolver = {};
  for (const [mediaId, entry] of Object.entries(value as Record<string, unknown>)) {
    if (!/^[0-9a-f]{64}$/.test(mediaId)) {
      throw new RenderVideoError("validation_failed", "render_video params.sources is keyed by something other than a BLAKE3 media_id.");
    }
    const paths = (entry as { paths?: unknown })?.paths;
    if (!Array.isArray(paths) || paths.length === 0 || paths.some((path) => typeof path !== "string" || path.length === 0)) {
      throw new RenderVideoError("validation_failed", "render_video params.sources entries must carry a non-empty ordered path list.");
    }
    resolver[mediaId] = { paths: paths as string[] };
  }
  return resolver;
}

export function parseParams(value: Record<string, unknown> | undefined): RenderVideoJobParams {
  const params = value ?? {};
  if (typeof params.output_path !== "string" || params.output_path.length === 0) {
    throw new RenderVideoError("validation_failed", "render_video params.output_path is missing.");
  }
  if (typeof params.work_directory !== "string" || params.work_directory.length === 0) {
    throw new RenderVideoError("validation_failed", "render_video params.work_directory is missing.");
  }
  const parsed: RenderVideoJobParams = {
    output_path: params.output_path,
    work_directory: params.work_directory,
    sources: parseSources(params.sources),
    encode: parseEncodeProfile(params.encode),
  };
  if (typeof params.ffmpeg_path === "string") parsed.ffmpeg_path = params.ffmpeg_path;
  if (typeof params.ffprobe_path === "string") parsed.ffprobe_path = params.ffprobe_path;
  return parsed;
}

function jobError(error: RenderVideoError, attempt: number, occurredAt: string): JobError {
  return {
    code: error.code,
    message: error.message,
    retryable: error.retryable,
    attempt,
    occurred_at: occurredAt,
    failed_input_id: null,
  };
}

export interface RenderVideoJobOutcome {
  job: JobSpec;
  result: RenderVideoResult | null;
}

export async function runRenderVideoJob(
  original: JobSpec,
  edl: EDL,
  dependencies: RenderVideoJobDependencies,
): Promise<RenderVideoJobOutcome> {
  if (original.state.status === "completed") return { job: original, result: null };
  const job = structuredClone(original);
  const now = dependencies.now ?? (() => new Date().toISOString());

  try {
    if (job.job_type !== "render_video") {
      throw new RenderVideoError("validation_failed", "The video worker only accepts render_video jobs.");
    }
    if (job.egress.requires_egress) {
      throw new RenderVideoError("validation_failed", "The local video worker does not accept egress-enabled jobs.");
    }
    if (job.requirements?.requires_source_file !== true) {
      throw new RenderVideoError(
        "validation_failed",
        "A render job must declare its final source-file read; it is the second and last time a " +
          "source is opened in its life.",
      );
    }
    if (job.inputs.edl_id !== edl.edl_id) {
      throw new RenderVideoError("validation_failed", "The job EDL input does not match the EDL.");
    }
    if (paramsDigest(job.params ?? {}) !== job.params_digest) {
      throw new RenderVideoError("validation_failed", "The render_video params digest does not match the job.");
    }
    const params = parseParams(job.params);
    const workDirectory = resolve(params.work_directory, job.job_id);

    job.state.status = "running";
    job.state.attempts += 1;
    job.state.started_at ??= now();
    job.state.heartbeat_at = now();
    job.state.progress = { units_done: 0, units_total: 1, unit: "files" };
    job.error = null;
    job.checkpoint = {
      resumable: true,
      cursor: JSON.stringify({ version: RENDER_VIDEO_CHECKPOINT_VERSION, work_directory: workDirectory }),
      checkpoint_version: RENDER_VIDEO_CHECKPOINT_VERSION,
      updated_at: now(),
      completed_input_ids: [],
      partial_output_ids: [],
    };
    await dependencies.persist(job);

    const tools: { ffmpeg?: string; ffprobe?: string } = {};
    if (params.ffmpeg_path) tools.ffmpeg = params.ffmpeg_path;
    if (params.ffprobe_path) tools.ffprobe = params.ffprobe_path;

    const result = await renderVideo(edl, {
      sources: params.sources,
      encode: params.encode,
      workDirectory,
      tools,
    });
    await publishRenderOnce(params.output_path, result);

    const finishedAt = now();
    job.state.status = "completed";
    job.state.heartbeat_at = finishedAt;
    job.state.finished_at = finishedAt;
    job.state.progress = { units_done: 1, units_total: 1, unit: "files" };
    job.checkpoint = {
      resumable: true,
      cursor: null,
      checkpoint_version: RENDER_VIDEO_CHECKPOINT_VERSION,
      updated_at: finishedAt,
      completed_input_ids: [edl.edl_id],
      partial_output_ids: [],
    };
    job.outputs = [
      {
        kind: "rendered_video",
        id: result.id,
        path: params.output_path,
        byte_size: result.byteSize,
        produced_at: finishedAt,
      },
    ];
    await dependencies.persist(job);
    return { job, result };
  } catch (unknownError) {
    const error = asRenderVideoError(unknownError);
    const occurredAt = now();
    job.state.status = "failed";
    job.state.heartbeat_at = occurredAt;
    job.state.finished_at = occurredAt;
    job.error = jobError(error, job.state.attempts, occurredAt);
    await dependencies.persist(job);
    return { job, result: null };
  }
}
