import { relative, resolve, sep } from "node:path";

import type {
  AlbumSpec,
  JobError,
  JobSpec,
} from "../../../contracts/codegen/generated/typescript/index.js";

import { guardPrint, PublicationBlocked } from "./clearance.js";
import { canonicalJson, digestBytes } from "./digest.js";
import { asRenderPrintError, RenderPrintError } from "./errors.js";
import type { IccProfileInput } from "./icc.js";
import type { FontResolver, PlacementAssetResolver } from "./page.js";
import {
  renderAlbum,
  writePdfOnce,
  type PageArtifact,
} from "./renderer.js";

export const RENDER_PRINT_CHECKPOINT_VERSION = 1;

export interface RenderPrintJobParams {
  output_path: string;
  work_directory: string;
  icc_profile: IccProfileInput;
  asset_paths: Record<string, string>;
  font_paths: Record<string, string>;
  /**
   * The `SafetyClearance` manifest for this exact book (issue #21).
   *
   * A job param rather than a separate file argument, so it is covered by
   * `params_digest` and therefore by the job's own identity: a book cannot be
   * re-run with a different clearance without becoming a different job.
   *
   * `unknown` rather than a typed shape on purpose. Everything about it is
   * checked by `guardPrint`, which parses deny-by-default, and typing it here
   * would invite a caller to believe the type checker had validated it.
   */
  safety_clearance: unknown;
}

/**
 * The media ids this book will publish, in publication order.
 *
 * First appearance wins for a photograph placed on more than one page: the
 * clearance is about a set of photographs and the order fixes the identity of
 * that set, so listing a repeat twice would only mean the same verdict has to
 * be present twice. Deduplicating HERE rather than in the verifier is
 * deliberate -- the verifier still refuses duplicates, so a producer that
 * builds a manifest with a repeated id is still caught.
 */
export function publicationMediaIds(album: AlbumSpec): string[] {
  const seen = new Set<string>();
  const ordered: string[] = [];
  for (const page of album.pages) {
    for (const placement of page.placements) {
      if (seen.has(placement.media_id)) continue;
      seen.add(placement.media_id);
      ordered.push(placement.media_id);
    }
  }
  return ordered;
}

export interface RenderPrintJobDependencies {
  persist: (job: JobSpec) => Promise<void>;
  now?: () => string;
}

interface CursorV1 {
  version: 1;
  pages: PageArtifact[];
}

function paramsDigest(params: Record<string, unknown>): string {
  return digestBytes(new TextEncoder().encode(canonicalJson(params)));
}

function parseParams(value: Record<string, unknown> | undefined): RenderPrintJobParams {
  const params = value ?? {};
  const icc = params.icc_profile;
  if (
    typeof params.output_path !== "string" ||
    typeof params.work_directory !== "string" ||
    typeof icc !== "object" ||
    icc === null ||
    typeof (icc as Record<string, unknown>).name !== "string" ||
    !(
      typeof (icc as Record<string, unknown>).path === "string" ||
      ["srgb", "p3", "cmyk"].includes(String((icc as Record<string, unknown>).builtin))
    ) ||
    typeof params.asset_paths !== "object" ||
    params.asset_paths === null ||
    Array.isArray(params.asset_paths) ||
    typeof params.font_paths !== "object" ||
    params.font_paths === null ||
    Array.isArray(params.font_paths)
  ) {
    throw new RenderPrintError("validation_failed", "render_print params do not match the worker's local schema.");
  }
  const assetPaths = params.asset_paths as Record<string, unknown>;
  const fontPaths = params.font_paths as Record<string, unknown>;
  if (
    Object.values(assetPaths).some((path) => typeof path !== "string") ||
    Object.values(fontPaths).some((path) => typeof path !== "string")
  ) {
    throw new RenderPrintError("validation_failed", "render_print resolver maps must contain only local paths.");
  }
  const iccRecord = icc as { name: string; path?: string; builtin?: "srgb" | "p3" | "cmyk" };
  const iccProfile: IccProfileInput = iccRecord.path
    ? { name: iccRecord.name, path: iccRecord.path }
    : { name: iccRecord.name, builtin: iccRecord.builtin! };
  return {
    output_path: params.output_path,
    work_directory: params.work_directory,
    icc_profile: iccProfile,
    asset_paths: assetPaths as Record<string, string>,
    font_paths: fontPaths as Record<string, string>,
    // Read through with no shape check and no default. `undefined` here is a
    // job that presented no clearance, and `guardPrint` denies it -- which is
    // the correct handling and the reason there is no `?? {}`.
    safety_clearance: params.safety_clearance,
  };
}

function parseCursor(job: JobSpec, pageStore: string): Map<number, PageArtifact> {
  if (job.checkpoint?.checkpoint_version !== RENDER_PRINT_CHECKPOINT_VERSION || !job.checkpoint.cursor) {
    return new Map();
  }
  try {
    const cursor = JSON.parse(job.checkpoint.cursor) as CursorV1;
    if (cursor.version !== 1 || !Array.isArray(cursor.pages)) return new Map();
    const root = resolve(pageStore);
    const entries: [number, PageArtifact][] = [];
    for (const artifact of cursor.pages) {
      if (
        !Number.isInteger(artifact.index) ||
        typeof artifact.id !== "string" ||
        !/^[0-9a-f]{64}$/.test(artifact.id) ||
        typeof artifact.path !== "string"
      ) {
        return new Map();
      }
      const child = resolve(artifact.path);
      const traversal = relative(root, child);
      if (traversal === ".." || traversal.startsWith(`..${sep}`) || traversal.startsWith(sep)) return new Map();
      entries.push([artifact.index, artifact]);
    }
    return new Map(entries);
  } catch {
    return new Map();
  }
}

function jobError(error: RenderPrintError, attempt: number, occurredAt: string): JobError {
  return {
    code: error.code,
    message: error.message,
    retryable: error.retryable,
    attempt,
    occurred_at: occurredAt,
    failed_input_id: null,
  };
}

export async function runRenderPrintJob(
  original: JobSpec,
  album: AlbumSpec,
  dependencies: RenderPrintJobDependencies,
): Promise<JobSpec> {
  if (original.state.status === "completed") return original;
  const job = structuredClone(original);
  const now = dependencies.now ?? (() => new Date().toISOString());

  try {
    if (job.job_type !== "render_print") {
      throw new RenderPrintError("validation_failed", "The print worker only accepts render_print jobs.");
    }
    if (job.egress.requires_egress) {
      throw new RenderPrintError("validation_failed", "The local print worker does not accept egress-enabled jobs.");
    }
    if (job.requirements?.requires_source_file !== true) {
      throw new RenderPrintError("validation_failed", "A print job must declare its final source-file read.");
    }
    if (job.inputs.album_id !== album.album_id) {
      throw new RenderPrintError("validation_failed", "The job album input does not match the AlbumSpec.");
    }
    if (paramsDigest(job.params ?? {}) !== job.params_digest) {
      throw new RenderPrintError("validation_failed", "The render_print params digest does not match the job.");
    }
    const params = parseParams(job.params);

    // Fail fast, before anything is rendered. This is NOT the enforcement --
    // see the second call below, immediately before the PDF is written -- it is
    // here so that a job with no clearance does not burn an hour of compositing
    // to be refused at the end.
    const mediaIds = publicationMediaIds(album);
    guardPrint(params.safety_clearance, { mediaIds });

    const pageStore = resolve(params.work_directory, job.job_id, "pages");
    const resumedPages = parseCursor(job, pageStore);
    const resolvePlacementAsset: PlacementAssetResolver = async (placement) => {
      const path = params.asset_paths[placement.media_id];
      if (!path) throw new Error("missing");
      return path;
    };
    const resolveFont: FontResolver = async (family) => {
      const path = params.font_paths[family];
      if (!path) throw new Error("missing");
      return path;
    };

    job.state.status = "running";
    job.state.attempts += 1;
    job.state.started_at ??= now();
    job.state.heartbeat_at = now();
    job.state.progress = { units_done: resumedPages.size, units_total: album.pages.length, unit: "pages" };
    job.error = null;
    job.checkpoint = {
      resumable: true,
      cursor: JSON.stringify({ version: 1, pages: [...resumedPages.values()] } satisfies CursorV1),
      checkpoint_version: RENDER_PRINT_CHECKPOINT_VERSION,
      updated_at: now(),
      completed_input_ids: [],
      partial_output_ids: [...resumedPages.values()].map((page) => page.id),
    };
    await dependencies.persist(job);

    const result = await renderAlbum(album, {
      iccProfile: params.icc_profile,
      resolvePlacementAsset,
      resolveFont,
      pageStoreDirectory: pageStore,
      resumedPages,
      onPageComplete: async (artifact) => {
        resumedPages.set(artifact.index, artifact);
        job.checkpoint = {
          resumable: true,
          cursor: JSON.stringify({
            version: 1,
            pages: [...resumedPages.values()].sort((left, right) => left.index - right.index),
          } satisfies CursorV1),
          checkpoint_version: RENDER_PRINT_CHECKPOINT_VERSION,
          updated_at: now(),
          completed_input_ids: [],
          partial_output_ids: [...resumedPages.values()].map((page) => page.id),
        };
        job.state.heartbeat_at = now();
        job.state.progress = { units_done: resumedPages.size, units_total: album.pages.length, unit: "pages" };
        await dependencies.persist(job);
      },
    });
    // THE ENFORCEMENT. Verified against the ids of the book that was actually
    // composited, in the same operation that writes the file, so there is no
    // window in which the checked set and the exported set can differ. A book
    // cannot be patched once it is in the post.
    //
    // `writePdfOnce` is the irreversible act; nothing may go between these two
    // lines. If you are adding a step here, it belongs above the guard.
    guardPrint(params.safety_clearance, { mediaIds: publicationMediaIds(album) });
    await writePdfOnce(params.output_path, result);

    const finishedAt = now();
    job.state.status = "completed";
    job.state.heartbeat_at = finishedAt;
    job.state.finished_at = finishedAt;
    job.state.progress = { units_done: album.pages.length, units_total: album.pages.length, unit: "pages" };
    job.checkpoint = {
      resumable: true,
      cursor: null,
      checkpoint_version: RENDER_PRINT_CHECKPOINT_VERSION,
      updated_at: finishedAt,
      completed_input_ids: [album.album_id],
      partial_output_ids: [],
    };
    job.outputs = [
      {
        kind: "rendered_pdf",
        id: result.id,
        path: params.output_path,
        byte_size: result.pdf.length,
        produced_at: finishedAt,
      },
    ];
    await dependencies.persist(job);
    return job;
  } catch (unknownError) {
    const error = asRenderPrintError(unknownError);
    const occurredAt = now();
    job.state.status = "failed";
    job.state.heartbeat_at = occurredAt;
    job.state.finished_at = occurredAt;
    job.error = jobError(error, job.state.attempts, occurredAt);
    await dependencies.persist(job);
    return job;
  }
}
