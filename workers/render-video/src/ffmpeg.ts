import { spawn } from "node:child_process";

import { RenderVideoError } from "./errors.js";

export interface ToolPaths {
  ffmpeg: string;
  ffprobe: string;
}

export interface CommandResult {
  stdout: string;
  stderr: string;
}

export async function run(command: string, args: readonly string[]): Promise<CommandResult> {
  return new Promise((resolve, reject) => {
    const child = spawn(command, [...args], { stdio: ["ignore", "pipe", "pipe"] });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (chunk: Buffer) => {
      stdout += chunk.toString("utf8");
    });
    child.stderr.on("data", (chunk: Buffer) => {
      stderr += chunk.toString("utf8");
    });
    child.on("error", (error: NodeJS.ErrnoException) => {
      reject(
        new RenderVideoError(
          error.code === "ENOENT" ? "dependency_failed" : "internal_error",
          `The renderer could not start its media tool (${error.code ?? "spawn failed"}).`,
        ),
      );
    });
    child.on("close", (code) => {
      if (code === 0) resolve({ stdout, stderr });
      else {
        const tail = stderr.trim().split("\n").slice(-12).join("\n");
        reject(
          new RenderVideoError(
            "internal_error",
            `A media tool exited with status ${code}. Last output:\n${tail}`,
          ),
        );
      }
    });
  });
}

export interface ProbedVideoStream {
  width: number;
  height: number;
  /** avg_frame_rate and r_frame_rate as exact rationals; both are required to agree. */
  frameRate: number;
  frameCount: number;
  pixelFormat: string;
  colorTransfer: string | null;
  rotation: number;
}

export interface ProbedAudioStream {
  sampleRate: number;
  channels: number;
}

export interface ProbedFile {
  path: string;
  video: ProbedVideoStream | null;
  audio: ProbedAudioStream | null;
  /** Container duration in seconds. Used only where an exact frame count does not exist. */
  durationSeconds: number;
}

function parseRational(value: string | undefined): number | null {
  if (!value) return null;
  const [numerator, denominator] = value.split("/");
  const top = Number(numerator);
  const bottom = denominator === undefined ? 1 : Number(denominator);
  if (!Number.isFinite(top) || !Number.isFinite(bottom) || bottom === 0) return null;
  return top / bottom;
}

/**
 * Probes one file. `-count_packets` is deliberate: nb_frames is absent or wrong in plenty
 * of containers, and a frame count guessed from a float duration is exactly the kind of
 * off-by-one that shows up as a black frame at the end of a clip.
 */
export interface ProbeOptions {
  /**
   * Sources must be constant rate or a frame-indexed trim is meaningless. The renderer's
   * own output is checked against its nominal rate instead: a container's average rate is
   * derived from the file's duration, and the last frame's displayed duration is a muxer
   * detail rather than a timing error.
   */
  requireConstantFrameRate?: boolean;
}

export async function probe(tools: ToolPaths, path: string, options: ProbeOptions = {}): Promise<ProbedFile> {
  const requireConstantFrameRate = options.requireConstantFrameRate !== false;
  const { stdout } = await run(tools.ffprobe, [
    "-v",
    "error",
    "-count_packets",
    "-show_streams",
    "-show_format",
    "-show_entries",
    "format=duration:stream=index,codec_type,width,height,avg_frame_rate,r_frame_rate,nb_read_packets,pix_fmt,color_transfer,sample_rate,channels:stream_side_data=rotation:stream_tags=rotate",
    "-of",
    "json",
    path,
  ]);

  let parsed: { streams?: Record<string, unknown>[]; format?: { duration?: string } };
  try {
    parsed = JSON.parse(stdout) as { streams?: Record<string, unknown>[]; format?: { duration?: string } };
  } catch {
    throw new RenderVideoError("file_corrupt", "A source file could not be probed.");
  }

  let video: ProbedVideoStream | null = null;
  let audio: ProbedAudioStream | null = null;

  for (const stream of parsed.streams ?? []) {
    if (stream.codec_type === "video" && video === null) {
      const average = parseRational(stream.avg_frame_rate as string | undefined);
      const real = parseRational(stream.r_frame_rate as string | undefined);
      if (average === null || real === null || average <= 0) {
        throw new RenderVideoError("unsupported_format", "A source video stream declares no frame rate.");
      }
      if (requireConstantFrameRate && Math.abs(average - real) > 1e-9) {
        throw new RenderVideoError(
          "unsupported_format",
          `A source is variable frame rate (avg ${average}, r ${real}). Frame-indexed trims are ` +
            "meaningless against a VFR stream, and the drift would be silent.",
        );
      }
      const rotationValue = (stream as { side_data_list?: { rotation?: number }[] }).side_data_list?.[0]?.rotation;
      const tagRotate = (stream as { tags?: { rotate?: string } }).tags?.rotate;
      video = {
        width: Number(stream.width),
        height: Number(stream.height),
        frameRate: requireConstantFrameRate ? average : real,
        frameCount: Number(stream.nb_read_packets ?? 0),
        pixelFormat: String(stream.pix_fmt ?? ""),
        colorTransfer: stream.color_transfer ? String(stream.color_transfer) : null,
        rotation: Number(rotationValue ?? (tagRotate ? Number(tagRotate) : 0)) || 0,
      };
    }
    if (stream.codec_type === "audio" && audio === null) {
      audio = {
        sampleRate: Number(stream.sample_rate ?? 0),
        channels: Number(stream.channels ?? 0),
      };
    }
  }

  const durationSeconds = Number(parsed.format?.duration ?? Number.NaN);
  return { path, video, audio, durationSeconds: Number.isFinite(durationSeconds) ? durationSeconds : 0 };
}

/** Integrated loudness and true peak, from ffmpeg's own R128 meter. */
export interface LoudnessMeasurement {
  integratedLufs: number;
  truePeakDb: number;
  loudnessRange: number;
  threshold: number;
}

export function parseLoudnormJson(stderr: string): LoudnessMeasurement {
  const start = stderr.lastIndexOf("{");
  const end = stderr.lastIndexOf("}");
  if (start === -1 || end === -1 || end < start) {
    throw new RenderVideoError("internal_error", "The loudness measurement pass produced no report.");
  }
  const report = JSON.parse(stderr.slice(start, end + 1)) as Record<string, string>;
  const integrated = Number(report.input_i);
  const truePeak = Number(report.input_tp);
  const loudnessRange = Number(report.input_lra);
  const threshold = Number(report.input_thresh);
  if (![integrated, truePeak, loudnessRange, threshold].every((value) => Number.isFinite(value))) {
    throw new RenderVideoError("internal_error", "The loudness measurement pass produced no usable numbers.");
  }
  return { integratedLufs: integrated, truePeakDb: truePeak, loudnessRange, threshold };
}

/** Reads the summary block ffmpeg's ebur128 filter writes when it shuts down. */
export function parseEbur128Summary(stderr: string): LoudnessMeasurement {
  const summary = stderr.slice(stderr.lastIndexOf("Summary:"));
  const integrated = /I:\s*(-?\d+(?:\.\d+)?)\s*LUFS/.exec(summary);
  const truePeak = /Peak:\s*(-?\d+(?:\.\d+)?|-inf)\s*dBFS/.exec(summary);
  if (!integrated || !truePeak) {
    throw new RenderVideoError("internal_error", "The loudness verification pass produced no summary.");
  }
  return {
    integratedLufs: Number(integrated[1]),
    truePeakDb: truePeak[1] === "-inf" ? Number.NEGATIVE_INFINITY : Number(truePeak[1]),
    loudnessRange: 0,
    threshold: 0,
  };
}
