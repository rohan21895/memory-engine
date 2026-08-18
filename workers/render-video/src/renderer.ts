import { constants } from "node:fs";
import { access, link, mkdir, readFile, rename, stat, unlink, writeFile } from "node:fs/promises";
import { dirname, join, resolve } from "node:path";

import type { EDL, JobErrorCode } from "../../../contracts/codegen/generated/typescript/index.js";

import { canonicalJson, digestFile, digestJson } from "./digest.js";
import { BITEXACT_ARGS, GLOBAL_ARGS, type EncodeProfile } from "./encode.js";
import { RenderVideoError } from "./errors.js";
import {
  parseEbur128Summary,
  parseLoudnormJson,
  probe,
  run,
  type LoudnessMeasurement,
  type RunOptions,
  type ToolPaths,
} from "./ffmpeg.js";
import { buildGraph, type Interpretation } from "./filtergraph.js";
import { assertRenderable, type ContractGap, type UnactedDeclaration } from "./gate.js";
import { buildProgram, type Program } from "./program.js";
import { assertEveryRefIsUsed, resolveSources, type ResolvedSource, type SourceResolver } from "./sources.js";
import { framesToSamples, framesToSeconds } from "./time.js";

export interface RenderVideoOptions {
  sources: SourceResolver;
  encode: EncodeProfile;
  workDirectory: string;
  tools?: Partial<ToolPaths>;
  onProgress?: (progress: RenderProgress) => Promise<void> | void;
}

export interface RenderProgress {
  framesDone: number;
  totalFrames: number;
  status: "continue" | "end";
}

export interface RenderVerification {
  frameCount: number;
  expectedFrameCount: number;
  width: number;
  height: number;
  frameRate: number;
  loudness: (LoudnessMeasurement & { targetLufs: number; truePeakCeilingDb: number }) | null;
}

export interface RenderVideoResult {
  /** BLAKE3 of the delivered file. */
  id: string;
  path: string;
  byteSize: number;
  /**
   * BLAKE3 over the canonical JSON of everything that determines the output: the resolved
   * source digests, the whole ffmpeg command, and the filtergraph text. Equal digests mean
   * two runs asked ffmpeg for exactly the same thing.
   */
  commandGraphDigest: string;
  command: readonly string[];
  filterGraph: string;
  program: { totalFrames: number; segments: number; audioContributions: number };
  verification: RenderVerification;
  gaps: ContractGap[];
  unacted: UnactedDeclaration[];
  interpretations: Interpretation[];
}

const DEFAULT_TOOLS: ToolPaths = { ffmpeg: "ffmpeg", ffprobe: "ffprobe" };

async function exists(path: string): Promise<boolean> {
  try {
    await access(path, constants.F_OK);
    return true;
  } catch {
    return false;
  }
}

function fail(code: JobErrorCode, detail: string): never {
  throw new RenderVideoError(code, detail);
}

/**
 * Loudness. `MixPlan` specifies the mix by OUTCOME — an integrated LUFS target and a true
 * peak ceiling — which is the one kind of audio specification a renderer can honour
 * without inventing anything, because any correct implementation lands on the same
 * measurement. So it is executed, and then MEASURED on the finished file rather than
 * assumed.
 */
interface LoudnessStage {
  measure: string;
  apply: (measured: LoudnessMeasurement) => string;
  targetLufs: number;
  truePeakCeilingDb: number;
  limiter: boolean;
}

function loudnessStage(edl: EDL): LoudnessStage | null {
  const mix = edl.audio_plan?.mix;
  if (!mix) return null;
  const targetLufs = mix.loudness_target_lufs;
  const truePeakCeilingDb = mix.true_peak_ceiling_db ?? -1;
  const limiter = mix.limiter !== false;
  return {
    targetLufs,
    truePeakCeilingDb,
    limiter,
    measure: `loudnorm=I=${targetLufs}:TP=${truePeakCeilingDb}:print_format=json`,
    apply: (measured) => {
      if (limiter) {
        return (
          `loudnorm=I=${targetLufs}:TP=${truePeakCeilingDb}:linear=true` +
          `:measured_I=${measured.integratedLufs}:measured_TP=${measured.truePeakDb}` +
          `:measured_LRA=${measured.loudnessRange}:measured_thresh=${measured.threshold}:print_format=none`
        );
      }
      const gain = targetLufs - measured.integratedLufs;
      return `volume=${gain.toFixed(6)}dB`;
    },
  };
}

function encodeArgs(profile: EncodeProfile, hasAudio: boolean): string[] {
  const args = ["-c:v", profile.video.codec, "-pix_fmt", profile.video.pix_fmt, ...profile.video.args];
  if (hasAudio) {
    if (!profile.audio) {
      fail("validation_failed", "The program carries audio and encode.audio is null.");
    }
    args.push("-c:a", profile.audio.codec, "-sample_fmt", profile.audio.sample_fmt, ...profile.audio.args);
  }
  return args;
}

async function writeFilterScript(directory: string, name: string, filter: string): Promise<string> {
  await mkdir(directory, { recursive: true });
  const path = join(directory, name);
  await writeFile(path, `${filter}\n`, { mode: 0o600 });
  return path;
}

function sourceDigestManifest(sources: ReadonlyMap<string, ResolvedSource>): Record<string, string[]> {
  const manifest: Record<string, string[]> = {};
  for (const [mediaRefId, source] of sources) manifest[mediaRefId] = [...source.memberDigests];
  return manifest;
}

export async function renderVideo(edl: EDL, options: RenderVideoOptions): Promise<RenderVideoResult> {
  const tools: ToolPaths = { ...DEFAULT_TOOLS, ...options.tools };
  const gate = assertRenderable(edl);
  const program: Program = buildProgram(edl);

  const workDirectory = resolve(options.workDirectory);
  const sources = await resolveSources(edl, options.sources, tools, workDirectory);

  const used = new Set<string>();
  for (const segment of program.video) {
    if (segment.kind === "clip") used.add(segment.mediaRefId);
    if (segment.kind === "transition") {
      used.add(segment.from.mediaRefId);
      used.add(segment.to.mediaRefId);
    }
  }
  for (const contribution of program.audio) used.add(contribution.mediaRefId);
  assertEveryRefIsUsed(edl, used);

  const graph = buildGraph(edl, program, sources, options.encode);
  const stage = graph.audioLabel ? loudnessStage(edl) : null;
  if (graph.audioLabel && !stage) {
    fail("validation_failed", "The program carries audio and the EDL declares no MixPlan.");
  }

  let measured: LoudnessMeasurement | null = null;
  if (graph.audioOnly && stage) {
    const measureFilter = `${graph.audioOnly.filter};\n[${graph.audioOnly.label}]${stage.measure}[aprobe]`;
    const scriptPath = await writeFilterScript(workDirectory, "measure.filter", measureFilter);
    const measureArgs = [
      ...GLOBAL_ARGS,
      ...graph.audioOnly.inputs.flatMap((input) => input.args),
      "-filter_complex_script",
      scriptPath,
      "-map",
      "[aprobe]",
      ...BITEXACT_ARGS,
      "-f",
      "null",
      "-",
    ];
    const { stderr } = await run(tools.ffmpeg, measureArgs);
    measured = parseLoudnormJson(stderr);
  }

  const filter = stage && measured && graph.audioLabel
    ? `${graph.filter};\n[${graph.audioLabel}]${stage.apply(measured)}[aout]`
    : graph.filter;
  const audioOut = stage && measured && graph.audioLabel ? "aout" : graph.audioLabel;

  const scriptPath = await writeFilterScript(workDirectory, "render.filter", filter);
  const command = [
    ...GLOBAL_ARGS,
    "-threads",
    String(options.encode.threads),
    ...graph.inputs.flatMap((input) => input.args),
    "-filter_complex_script",
    scriptPath,
    "-map",
    `[${graph.videoLabel}]`,
    ...(audioOut ? ["-map", `[${audioOut}]`] : []),
    ...encodeArgs(options.encode, audioOut !== null),
    "-threads:v",
    String(options.encode.threads),
    ...BITEXACT_ARGS,
    "-stats_period",
    "1",
    "-progress",
    "pipe:1",
    "-f",
    options.encode.container,
    "-y",
  ];

  /**
   * The determinism digest deliberately covers the RESOLVED source hashes rather than the
   * paths: the same EDL rendered from the same footage on a different machine, in a
   * different work directory, must produce the same digest, or the digest is measuring the
   * filesystem instead of the render. So every path in the command is replaced by the
   * media_ref it stands for, and the filter script's path by a constant — the filter TEXT
   * is hashed in full, which is the part that decides what comes out.
   */
  const pathsToRefs = new Map<string, string>();
  for (const source of sources.values()) {
    for (const path of source.paths) pathsToRefs.set(resolve(path), `media:${source.mediaRefId}`);
  }
  const portableCommand = command.map((argument) => {
    const asPath = pathsToRefs.get(resolve(argument));
    if (asPath) return asPath;
    if (argument === scriptPath) return "filter-script";
    if (argument.startsWith(workDirectory)) return `work:${argument.slice(workDirectory.length)}`;
    return argument;
  });

  const commandGraphDigest = digestJson({
    edl_id: edl.edl_id,
    command: portableCommand,
    filter: filter.split(workDirectory).join("work:"),
    encode: options.encode,
    sources: sourceDigestManifest(sources),
    total_frames: program.totalFrames,
  });

  /**
   * The partial is named by the command-graph digest, which makes resumption content
   * addressed and free: a job killed after the encode finished finds its own output on
   * relaunch, re-verifies it, and does not decode a frame. A job killed mid-encode finds a
   * file that fails verification and renders again.
   */
  const partial = join(workDirectory, `render-${commandGraphDigest.slice(0, 16)}.${options.encode.container}`);
  await mkdir(dirname(partial), { recursive: true });
  let verification: RenderVerification | null = null;
  if (await exists(partial)) {
    verification = await verifyOutput(tools, edl, program, partial, stage).catch(() => null);
    if (!verification) await unlink(partial).catch(() => undefined);
  }
  if (!verification) {
    const runOptions: RunOptions = { stdoutLimitBytes: 0 };
    if (options.onProgress) {
      runOptions.onProgress = (progress) => {
        const fromClock = progress.outTimeUs === null
          ? 0
          : Math.floor((progress.outTimeUs * edl.rate) / 1_000_000);
        const framesDone = Math.min(program.totalFrames, Math.max(0, progress.frame ?? fromClock));
        return options.onProgress!({ framesDone, totalFrames: program.totalFrames, status: progress.status });
      };
    }
    await run(tools.ffmpeg, [...command, partial], runOptions);
    verification = await verifyOutput(tools, edl, program, partial, stage);
  }
  const id = await digestFile(partial);
  const info = await stat(partial);

  return {
    id,
    path: partial,
    byteSize: info.size,
    commandGraphDigest,
    command: [...command, partial],
    filterGraph: filter,
    program: {
      totalFrames: program.totalFrames,
      segments: program.video.length,
      audioContributions: program.audio.length,
    },
    verification,
    gaps: gate.gaps,
    unacted: gate.unacted,
    interpretations: graph.interpretations,
  };
}

/**
 * Reads the finished file back. This is the guard against the failure mode that matters
 * most here: a render that exits zero and is wrong. A dropped or duplicated frame moves
 * every subsequent cut, and a frame count is the cheapest possible way to notice.
 */
async function verifyOutput(
  tools: ToolPaths,
  edl: EDL,
  program: Program,
  path: string,
  stage: LoudnessStage | null,
): Promise<RenderVerification> {
  const probed = await probe(tools, path, { requireConstantFrameRate: false });
  if (!probed.video) fail("internal_error", "The rendered file has no video stream.");

  if (probed.video.frameCount !== program.totalFrames) {
    fail(
      "internal_error",
      `The render holds ${probed.video.frameCount} frames and the timeline is ${program.totalFrames}. ` +
        "A frame count that does not match the plan means every cut after the discrepancy is in " +
        "the wrong place.",
    );
  }
  if (probed.video.width !== edl.target.resolution.width || probed.video.height !== edl.target.resolution.height) {
    fail(
      "internal_error",
      `The render is ${probed.video.width}x${probed.video.height} and the target is ` +
        `${edl.target.resolution.width}x${edl.target.resolution.height}.`,
    );
  }
  if (Math.abs(probed.video.frameRate - edl.rate) > 1e-6) {
    fail("internal_error", `The render runs at ${probed.video.frameRate} fps and the timeline is ${edl.rate}.`);
  }

  let loudness: RenderVerification["loudness"] = null;
  if (stage && program.audio.length > 0) {
    const { stderr } = await run(tools.ffmpeg, [
      "-nostdin",
      "-hide_banner",
      "-nostats",
      "-i",
      path,
      "-map",
      "0:a",
      "-af",
      "ebur128=peak=true:framelog=quiet",
      "-f",
      "null",
      "-",
    ]);
    const measurement = parseEbur128Summary(stderr);
    if (measurement.truePeakDb > stage.truePeakCeilingDb + 0.3) {
      fail(
        "internal_error",
        `The render peaks at ${measurement.truePeakDb} dBTP against a ceiling of ` +
          `${stage.truePeakCeilingDb} dBTP.` +
          (stage.limiter
            ? ""
            : " The mix declares limiter:false, so the only way to hit the loudness target was a " +
              "static gain, and it does not fit under the ceiling."),
      );
    }
    if (Math.abs(measurement.integratedLufs - stage.targetLufs) > 1) {
      fail(
        "internal_error",
        `The render measures ${measurement.integratedLufs} LUFS against a target of ${stage.targetLufs}.`,
      );
    }
    loudness = { ...measurement, targetLufs: stage.targetLufs, truePeakCeilingDb: stage.truePeakCeilingDb };
  }

  return {
    frameCount: probed.video.frameCount,
    expectedFrameCount: program.totalFrames,
    width: probed.video.width,
    height: probed.video.height,
    frameRate: probed.video.frameRate,
    loudness,
  };
}

/**
 * Re-validates a published render before a completed JobSpec is trusted. This deliberately
 * calls the same verifier as a fresh encode so replay cannot turn a missing frame, changed
 * geometry, wrong frame rate, or out-of-spec mix into a successful cache hit.
 */
export async function verifyPublishedRender(
  edl: EDL,
  path: string,
  configuredTools: Partial<ToolPaths> = {},
): Promise<RenderVerification> {
  assertRenderable(edl);
  const program = buildProgram(edl);
  const stage = loudnessStage(edl);
  if (program.audio.length > 0 && !stage) {
    fail("validation_failed", "The program carries audio and the EDL declares no MixPlan.");
  }
  return verifyOutput({ ...DEFAULT_TOOLS, ...configuredTools }, edl, program, path, stage);
}

/**
 * Publishes the render to its final path. Content addressed, so a replay of a completed
 * job is a no-op and an output target holding different bytes is a refusal rather than an
 * overwrite. Same shape as `workers/render-print`'s `writePdfOnce`.
 */
export async function publishRenderOnce(path: string, result: RenderVideoResult): Promise<void> {
  await mkdir(dirname(path), { recursive: true });
  try {
    await access(path, constants.F_OK);
    if ((await digestFile(path)) === result.id) return;
    throw new RenderVideoError("validation_failed", "The output target already contains different bytes.");
  } catch (error) {
    if (error instanceof RenderVideoError) throw error;
    if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;
  }

  try {
    await link(result.path, path);
  } catch (error) {
    const code = (error as NodeJS.ErrnoException).code;
    if (code === "EEXIST") {
      if ((await digestFile(path)) !== result.id) {
        throw new RenderVideoError("validation_failed", "The output target changed during publication.");
      }
      return;
    }
    if (code !== "EXDEV") throw error;
    await rename(result.path, path);
  }
}

/** For diagnostics and tests: the decoded picture, independent of container framing. */
export async function decodedFrameDigest(tools: ToolPaths, path: string, workDirectory: string): Promise<string> {
  await mkdir(workDirectory, { recursive: true });
  const raw = join(workDirectory, `decoded-${Date.now()}-${Math.random().toString(36).slice(2)}.rgb`);
  await run(tools.ffmpeg, [
    "-nostdin",
    "-hide_banner",
    "-nostats",
    "-i",
    path,
    "-map",
    "0:v",
    "-f",
    "rawvideo",
    "-pix_fmt",
    "rgb24",
    "-y",
    raw,
  ]);
  const digest = await digestFile(raw);
  await unlink(raw).catch(() => undefined);
  return digest;
}

export { canonicalJson, framesToSamples, framesToSeconds };

export async function readIfPresent(path: string): Promise<Buffer | null> {
  try {
    return await readFile(path);
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") return null;
    throw error;
  }
}
