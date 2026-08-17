import { constants } from "node:fs";
import { access, mkdir, stat, writeFile } from "node:fs/promises";
import { join, resolve } from "node:path";

import type { EDL, MediaRef } from "../../../contracts/codegen/generated/typescript/index.js";

import { digestFile, spanAssemblyId } from "./digest.js";
import { RenderVideoError } from "./errors.js";
import { probe, type ProbedAudioStream, type ProbedVideoStream, type ToolPaths } from "./ffmpeg.js";
import { frameRange, rangeLength } from "./time.js";

/**
 * Source resolution, and the one thing this worker treats as non-negotiable: a source that
 * is missing, unreadable, the wrong file, variable frame rate, or shorter than the plan
 * believes is a hard, up-front failure.
 *
 * The failure this guards against is the worst one available to a video renderer: a render
 * that succeeds and contains black where a shot should be. Nobody reviews a render that
 * exited zero. So every source is opened, hashed and measured BEFORE a single frame is
 * encoded, and the hash is checked against `media_id` — which for a physical file IS the
 * BLAKE3 of its bytes, so "the resolver handed me the wrong file" is detectable rather
 * than merely unlikely.
 *
 * Nothing here scans a directory or guesses a sibling path. The only paths ever opened are
 * the ones the job named for a media_id the EDL declared.
 */

export interface SourceResolution {
  /** Ordered local paths. More than one only for a span assembly, in member index order. */
  paths: string[];
}

export type SourceResolver = Record<string, SourceResolution>;

export interface ResolvedSource {
  mediaRefId: string;
  mediaId: string;
  paths: string[];
  /** ffmpeg input arguments for this source, including the concat demuxer where needed. */
  inputArgs: string[];
  video: ProbedVideoStream | null;
  audio: ProbedAudioStream | null;
  /** Per-member BLAKE3, in member order. For a single file this is [media_id]. */
  memberDigests: string[];
  /** Total frames across members, for a video source. */
  frameCount: number;
}

function fail(code: "file_not_found" | "file_unreadable" | "file_corrupt" | "validation_failed" | "unsupported_format", detail: string): never {
  throw new RenderVideoError(code, detail);
}

async function assertReadableFile(path: string): Promise<void> {
  let info;
  try {
    info = await stat(path);
  } catch (error) {
    const code = (error as NodeJS.ErrnoException).code;
    if (code === "ENOENT") fail("file_not_found", "A source named by the render job does not exist.");
    if (code === "EACCES") fail("file_unreadable", "A source named by the render job is not readable.");
    fail("file_unreadable", "A source named by the render job could not be inspected.");
  }
  if (!info.isFile()) fail("file_unreadable", "A source named by the render job is not a regular file.");
  if (info.size === 0) fail("file_corrupt", "A source named by the render job is zero bytes.");
  try {
    await access(path, constants.R_OK);
  } catch {
    fail("file_unreadable", "A source named by the render job is not readable.");
  }
}

/** The concat demuxer list for a span assembly. Written from EDL-named paths only. */
async function writeConcatList(directory: string, mediaRefId: string, paths: readonly string[]): Promise<string> {
  await mkdir(directory, { recursive: true });
  const listPath = join(directory, `${mediaRefId}.concat`);
  const body = paths.map((path) => `file '${resolve(path).replaceAll("'", "'\\''")}'\n`).join("");
  await writeFile(listPath, body, { mode: 0o600 });
  return listPath;
}

const HDR_TRANSFERS = new Set(["arib-std-b67", "smpte2084", "smpte428", "bt2020-10", "bt2020-12"]);

export async function resolveSources(
  edl: EDL,
  resolver: SourceResolver,
  tools: ToolPaths,
  workDirectory: string,
): Promise<Map<string, ResolvedSource>> {
  const resolved = new Map<string, ResolvedSource>();

  for (const ref of edl.media_refs) {
    const entry = resolver[ref.media_id];
    if (!entry || entry.paths.length === 0) {
      fail(
        "validation_failed",
        `The render job resolves no path for media_ref ${ref.media_ref_id}. The EDL addresses ` +
          "content by hash and the job must say where that content is; the renderer will not " +
          "search for it.",
      );
    }
    const paths = entry.paths;
    const isAssembly = ref.is_span_assembly === true;
    if (!isAssembly && paths.length !== 1) {
      fail(
        "validation_failed",
        `media_ref ${ref.media_ref_id} is not a span assembly but the job resolves ${paths.length} paths.`,
      );
    }

    for (const path of paths) await assertReadableFile(path);

    const memberDigests: string[] = [];
    for (const path of paths) memberDigests.push(await digestFile(path));

    const identity = isAssembly ? spanAssemblyId(memberDigests) : memberDigests[0]!;
    if (identity !== ref.media_id) {
      fail(
        "validation_failed",
        `media_ref ${ref.media_ref_id} resolves to content whose ${isAssembly ? "span assembly id" : "BLAKE3"} ` +
          `is ${identity}, not the ${ref.media_id} the plan was made against. This is the wrong ` +
          `footage${isAssembly ? ", or the chapters are in the wrong order" : ""}.`,
      );
    }

    const probed = [];
    for (const path of paths) probed.push(await probe(tools, path));

    const available = frameRange(ref.available_range, edl.rate, `media_ref ${ref.media_ref_id} available_range`);
    const kind = ref.media_kind ?? "video";

    let video: ProbedVideoStream | null = null;
    let frameCount = 0;
    if (kind === "video") {
      const first = probed[0]!.video;
      if (!first) fail("unsupported_format", `media_ref ${ref.media_ref_id} declares video and has no video stream.`);
      if (ref.expected_frame_rate == null) {
        fail(
          "validation_failed",
          `media_ref ${ref.media_ref_id} declares no expected_frame_rate. Frame-indexed trims need ` +
            "one, and inferring it from the file would let a re-encoded source change every cut.",
        );
      }
      if (ref.expected_frame_rate !== edl.rate) {
        fail(
          "validation_failed",
          `media_ref ${ref.media_ref_id} runs at ${ref.expected_frame_rate} against a timeline at ` +
            `${edl.rate}. Rate conversion changes which frame every cut lands on and this worker ` +
            "will not do it silently.",
        );
      }
      for (const file of probed) {
        const stream = file.video;
        if (!stream) fail("unsupported_format", `A member of ${ref.media_ref_id} has no video stream.`);
        if (Math.abs(stream.frameRate - ref.expected_frame_rate) > 1e-6) {
          fail(
            "validation_failed",
            `A source for ${ref.media_ref_id} runs at ${stream.frameRate} fps and the plan expects ` +
              `${ref.expected_frame_rate}.`,
          );
        }
        if (stream.width !== first.width || stream.height !== first.height || stream.pixelFormat !== first.pixelFormat) {
          fail("validation_failed", `The members of ${ref.media_ref_id} are not the same format.`);
        }
        if (stream.rotation !== 0) {
          fail(
            "unsupported_format",
            `A source for ${ref.media_ref_id} carries a ${stream.rotation} degree rotation. Crop ` +
              "coordinates are normalised against the ORIENTED image, and applying the rotation " +
              "here would need a convention the contract does not state.",
          );
        }
        if (stream.colorTransfer && HDR_TRANSFERS.has(stream.colorTransfer)) {
          fail(
            "validation_failed",
            `A source for ${ref.media_ref_id} is ${stream.colorTransfer}. ColorPipeline names no ` +
              "tone-mapping operator, so an HDR source cannot be rendered to SDR without the " +
              "renderer choosing the look (contracts#58).",
          );
        }
        frameCount += stream.frameCount;
      }
      video = first;

      if (frameCount !== rangeLength(available)) {
        fail(
          "validation_failed",
          `media_ref ${ref.media_ref_id} declares an available_range of ${rangeLength(available)} ` +
            `frames and the resolved source holds ${frameCount}. Rendering against a source that ` +
            "is not the length the plan believes is how a clip becomes black frames" +
            (ref.is_span_assembly ? ", and a dropped frame at a chapter split looks exactly like this (contracts#55)" : "") +
            ".",
        );
      }
    }

    let audio: ProbedAudioStream | null = null;
    if (kind === "music" || kind === "audio") {
      const stream = probed[0]!.audio;
      if (!stream) fail("unsupported_format", `media_ref ${ref.media_ref_id} declares audio and has no audio stream.`);
      const neededSeconds = available.end / edl.rate;
      const total = probed.reduce((sum, file) => sum + file.durationSeconds, 0);
      if (total + 1e-3 < neededSeconds) {
        fail(
          "validation_failed",
          `media_ref ${ref.media_ref_id} needs ${neededSeconds.toFixed(3)} s of audio and the ` +
            `resolved source holds ${total.toFixed(3)} s.`,
        );
      }
      audio = stream;
    } else if (probed[0]!.audio) {
      audio = probed[0]!.audio;
    }

    /**
     * `-r` on a video INPUT declares the rate the demuxer should assume. The source has
     * already been proved constant-rate at exactly this value, so it re-times nothing —
     * but it does give the filter graph a known frame rate, which xfade requires and which
     * Matroska (a variable-rate container by design) otherwise leaves unset.
     */
    const rateArgs = kind === "video" ? ["-r", String(edl.rate)] : [];
    const inputArgs = isAssembly
      ? [...rateArgs, "-f", "concat", "-safe", "0", "-i", await writeConcatList(workDirectory, ref.media_ref_id, paths)]
      : [...rateArgs, "-i", paths[0]!];

    resolved.set(ref.media_ref_id, {
      mediaRefId: ref.media_ref_id,
      mediaId: ref.media_id,
      paths: [...paths],
      inputArgs,
      video,
      audio,
      memberDigests,
      frameCount,
    });
  }

  return resolved;
}

/** Every source a clip actually reads, so an unused media_ref is not silently accepted. */
export function assertEveryRefIsUsed(edl: EDL, used: ReadonlySet<string>): void {
  const declared = edl.media_refs.map((ref: MediaRef) => ref.media_ref_id);
  const unused = declared.filter((id) => !used.has(id));
  if (unused.length > 0) {
    throw new RenderVideoError(
      "validation_failed",
      `media_refs ${unused.join(", ")} are declared and never read. An EDL that names a source it ` +
        "does not use is a plan that changed without its media list changing with it.",
    );
  }
}
