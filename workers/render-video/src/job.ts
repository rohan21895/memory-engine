import { stat } from "node:fs/promises";
import { resolve } from "node:path";

import type { EDL, JobError, JobOutput, JobSpec } from "../../../contracts/codegen/generated/typescript/index.js";

import { canonicalJson, digestBytes, digestFile } from "./digest.js";
import { parseEncodeProfile, type EncodeProfile } from "./encode.js";
import { asRenderVideoError, RenderVideoError } from "./errors.js";
import { buildProgram } from "./program.js";
import { publishRenderOnce, renderVideo, type RenderVideoResult, verifyPublishedRender } from "./renderer.js";
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

function recordedCompletedOutput(job: JobSpec, expectedPath: string): JobOutput {
  if (job.outputs?.length !== 1 || job.outputs[0]?.kind !== "rendered_video") {
    throw new RenderVideoError(
      "validation_failed",
      "A completed render_video job must record exactly one rendered_video output.",
    );
  }
  const output = job.outputs[0];
  if (output.path !== expectedPath) {
    throw new RenderVideoError(
      "validation_failed",
      "The completed render_video output path does not match params.output_path.",
    );
  }
  if (!/^[0-9a-f]{64}$/.test(output.id)) {
    throw new RenderVideoError("validation_failed", "The completed render_video output records no valid BLAKE3.");
  }
  if (!Number.isSafeInteger(output.byte_size) || (output.byte_size ?? 0) <= 0) {
    throw new RenderVideoError(
      "validation_failed",
      "The completed render_video output records no positive integer byte size.",
    );
  }
  return output;
}

async function verifyCompletedOutput(
  job: JobSpec,
  edl: EDL,
  params: RenderVideoJobParams,
  tools: { ffmpeg?: string; ffprobe?: string },
): Promise<void> {
  const output = recordedCompletedOutput(job, params.output_path);
  let info;
  try {
    info = await stat(params.output_path);
  } catch (error) {
    const code = (error as NodeJS.ErrnoException).code;
    if (code === "ENOENT") {
      throw new RenderVideoError("file_not_found", "The completed render_video output file is missing.");
    }
    if (code === "EACCES" || code === "EPERM") {
      throw new RenderVideoError("permission_denied", "The completed render_video output file cannot be inspected.");
    }
    throw error;
  }
  if (!info.isFile()) {
    throw new RenderVideoError("validation_failed", "The completed render_video output path is not a regular file.");
  }
  if (info.size === 0) {
    throw new RenderVideoError("zero_byte_file", "The completed render_video output file is empty.");
  }
  if (info.size !== output.byte_size) {
    throw new RenderVideoError(
      "file_corrupt",
      `The completed render_video output byte size is ${info.size}; the job records ${output.byte_size}.`,
    );
  }

  let actualDigest: string;
  try {
    actualDigest = await digestFile(params.output_path);
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") {
      throw new RenderVideoError("file_not_found", "The completed render_video output disappeared during verification.");
    }
    throw error;
  }
  if (actualDigest !== output.id) {
    throw new RenderVideoError(
      "file_corrupt",
      `The completed render_video output BLAKE3 is ${actualDigest}; the job records ${output.id}.`,
    );
  }
  await verifyPublishedRender(edl, params.output_path, tools);
}

export async function runRenderVideoJob(
  original: JobSpec,
  edl: EDL,
  dependencies: RenderVideoJobDependencies,
): Promise<RenderVideoJobOutcome> {
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
    const tools: { ffmpeg?: string; ffprobe?: string } = {};
    if (params.ffmpeg_path) tools.ffmpeg = params.ffmpeg_path;
    if (params.ffprobe_path) tools.ffprobe = params.ffprobe_path;

    if (job.state.status === "completed") {
      await verifyCompletedOutput(job, edl, params, tools);
      return { job: original, result: null };
    }
    const totalFrames = buildProgram(edl).totalFrames;

    job.state.status = "running";
    job.state.attempts += 1;
    job.state.started_at ??= now();
    job.state.heartbeat_at = now();
    job.state.progress = { units_done: 0, units_total: totalFrames, unit: "frames" };
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

    const result = await renderVideo(edl, {
      sources: params.sources,
      encode: params.encode,
      workDirectory,
      tools,
      onProgress: async (progress) => {
        const heartbeatAt = now();
        job.state.heartbeat_at = heartbeatAt;
        job.state.progress = {
          units_done: progress.framesDone,
          units_total: progress.totalFrames,
          unit: "frames",
        };
        if (job.checkpoint) job.checkpoint.updated_at = heartbeatAt;
        await dependencies.persist(job);
      },
    });
    await publishRenderOnce(params.output_path, result);

    const finishedAt = now();
    job.state.status = "completed";
    job.state.heartbeat_at = finishedAt;
    job.state.finished_at = finishedAt;
    job.state.progress = {
      units_done: result.program.totalFrames,
      units_total: result.program.totalFrames,
      unit: "frames",
    };
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
