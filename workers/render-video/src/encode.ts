import type { EncodeProfile } from "../../../contracts/codegen/generated/typescript/index.js";

import { RenderVideoError } from "./errors.js";

/**
 * The encode profile arrives in the PLAN, on `RenderTarget.encode` (contracts#56).
 *
 * It used to arrive as a required `JobSpec.params.encode` block, because RenderTarget said
 * nothing about the codec and this worker refuses to invent one. That kept the renderer
 * honest and left the plan unable to describe its own output: two renders of one EDL could
 * differ by an entire codec and nothing recorded which was used, while
 * `Determinism.inputs_digest` claimed that two plans with the same digest are the same cut.
 *
 * Nothing here fills anything in. The contract makes every field mandatory; this module
 * maps the declared profile onto ffmpeg arguments and refuses the combinations it cannot
 * produce — an encoder that does not emit the declared codec, or an encoder this build has
 * no mapping for.
 */

/** Encoder implementation -> the codec it produces. Mirrors the contract's own pairing. */
const ENCODER_CODEC: Readonly<Record<string, string>> = Object.freeze({
  libx264: "h264",
  h264_videotoolbox: "h264",
  libx265: "hevc",
  hevc_videotoolbox: "hevc",
  libsvtav1: "av1",
  "libvpx-vp9": "vp9",
  ffv1: "ffv1",
  prores_ks: "prores",
});

/**
 * Which ffmpeg flag each encoder takes its quality on. `-crf` is x264/x265/VP9/SVT-AV1;
 * `-q:v` is what prores_ks and the VideoToolbox encoders understand. Getting this wrong is
 * not silent — ffmpeg rejects an unknown private option — but stating it here keeps the
 * mapping in one readable place rather than inside a conditional.
 */
const QUALITY_FLAG: Readonly<Record<string, string>> = Object.freeze({
  libx264: "-crf",
  libx265: "-crf",
  libsvtav1: "-crf",
  "libvpx-vp9": "-crf",
  h264_videotoolbox: "-q:v",
  hevc_videotoolbox: "-q:v",
  prores_ks: "-q:v",
});

/**
 * Contract container name -> ffmpeg muxer name. The contract names the CONTAINER, which is
 * what a player and a vendor care about; ffmpeg's `-f` takes a muxer, and the two differ
 * for Matroska. Mapping here rather than putting "matroska" in the schema keeps one
 * worker's command-line vocabulary out of a document both sides read.
 */
const CONTAINER_MUXER: Readonly<Record<string, string>> = Object.freeze({
  mp4: "mp4",
  mov: "mov",
  mkv: "matroska",
  webm: "webm",
});

export function muxerFor(profile: EncodeProfile): string {
  const muxer = CONTAINER_MUXER[profile.container];
  if (muxer === undefined) fail(`container ${profile.container} has no muxer in this worker.`);
  return muxer;
}

/** Encoders whose output is byte-reproducible on one build given the same thread count. */
const SOFTWARE_ENCODERS = new Set(["libx264", "libx265", "libsvtav1", "libvpx-vp9", "ffv1", "prores_ks"]);

function fail(detail: string): never {
  throw new RenderVideoError("validation_failed", `The video renderer refused the encode profile: ${detail}`);
}

export function assertEncodable(profile: EncodeProfile): void {
  const video = profile.video;
  const produced = ENCODER_CODEC[video.encoder];
  if (produced === undefined) {
    fail(`encoder ${video.encoder} has no mapping in this worker.`);
  }
  if (produced !== video.codec) {
    fail(
      `encoder ${video.encoder} produces ${produced} and the profile declares codec ` +
        `${video.codec}. The pair is a planner error, not something to reconcile here.`,
    );
  }
  const mode = video.rate_control.mode;
  if ((mode === "crf" || mode === "cqp") && QUALITY_FLAG[video.encoder] === undefined) {
    fail(`encoder ${video.encoder} takes no quality value, and rate_control is ${mode}.`);
  }
  if (profile.encoder_threads > 1 && SOFTWARE_ENCODERS.has(video.encoder)) {
    fail(
      `profile ${profile.profile_id} asks for ${profile.encoder_threads} encoder threads on ` +
        `${video.encoder}. Software encoders slice a frame across threads and the slice ` +
        "boundaries move with the count, so the render would only be reproducible on a " +
        "machine with the same one. State 1, or state a hardware encoder and accept that " +
        "its bytes are a property of the driver.",
    );
  }
}

/** Video encoder arguments, in a fixed order so two runs build the same command. */
export function videoEncodeArgs(profile: EncodeProfile): string[] {
  const video = profile.video;
  const args = ["-c:v", video.encoder, "-pix_fmt", video.pixel_format];
  if (video.preset) args.push("-preset", video.preset);

  const rate = video.rate_control;
  switch (rate.mode) {
    case "crf":
    case "cqp":
      args.push(QUALITY_FLAG[video.encoder]!, String(rate.quality));
      break;
    case "abr":
      args.push("-b:v", `${rate.bit_rate_kbps}k`);
      break;
    case "cbr":
      args.push(
        "-b:v",
        `${rate.bit_rate_kbps}k`,
        "-minrate",
        `${rate.bit_rate_kbps}k`,
        "-maxrate",
        `${rate.bit_rate_kbps}k`,
        "-bufsize",
        `${(rate.bit_rate_kbps as number) * 2}k`,
      );
      break;
    case "lossless":
      break;
  }

  if (video.profile) args.push("-profile:v", video.profile);
  if (video.level) args.push("-level:v", video.level);
  args.push("-g", String(video.keyframe_interval_frames));
  return args;
}

export function audioEncodeArgs(profile: EncodeProfile): string[] {
  const audio = profile.audio;
  if (!audio) {
    fail("the program carries audio and the profile's audio block is null.");
  }
  const args = ["-c:a", audio.encoder, "-sample_fmt", audio.sample_format];
  if (audio.bit_rate_kbps != null) args.push("-b:a", `${audio.bit_rate_kbps}k`);
  return args;
}

/**
 * Flags that strip everything an encoder would otherwise write about *when* and *with
 * what* the file was made. Without these, two renders of one EDL differ in the muxer's
 * creation time, the encoder version string and the MP4 uuid box, and the determinism
 * claim becomes untestable.
 */
export const GLOBAL_ARGS: readonly string[] = Object.freeze(["-nostdin", "-hide_banner", "-nostats"]);

/**
 * Output-side flags. These have to sit after the last input: `-map_metadata` and friends
 * are per-file options, and ffmpeg reads them as applying to whatever file follows.
 *
 * `-fps_mode passthrough` is here for a different reason than the rest. It forbids ffmpeg
 * from duplicating or dropping a frame to hit a target rate. If the filtergraph produces
 * the wrong number of frames, the file gets the wrong number of frames and the post-render
 * frame-count check catches it — rather than ffmpeg quietly padding the difference and the
 * cut after it landing a frame late for ever.
 */
export const BITEXACT_ARGS: readonly string[] = Object.freeze([
  "-fflags",
  "+bitexact",
  "-flags",
  "+bitexact",
  "-flags:v",
  "+bitexact",
  "-flags:a",
  "+bitexact",
  "-map_metadata",
  "-1",
  "-map_chapters",
  "-1",
  "-fps_mode",
  "passthrough",
]);

export type { EncodeProfile };
