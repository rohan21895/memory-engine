import { RenderVideoError } from "./errors.js";

/**
 * The encode profile is NOT in the EDL. `RenderTarget` gives a destination, a resolution,
 * an aspect ratio and a loudness target, and stops — no codec, no container, no pixel
 * format, no rate control, no GOP, no scaler. See contracts#56.
 *
 * Rather than keep a destination-to-codec table inside the renderer, where it would be a
 * delivery decision made invisibly, the profile arrives as a required, fully explicit
 * `JobSpec.params.encode` block. Every field is mandatory. There are no defaults and no
 * fallbacks: a job that omits one fails instead of getting somebody's opinion.
 *
 * This mirrors what `workers/render-print` does with the ICC profile, which is the same
 * shape of problem — a delivery fact the plan does not carry.
 */

export interface EncodeVideoProfile {
  codec: string;
  pix_fmt: string;
  /** Passed to ffmpeg verbatim, after the codec and pixel format. */
  args: string[];
}

export interface EncodeAudioProfile {
  codec: string;
  sample_fmt: string;
  args: string[];
}

export interface EncodeProfile {
  /** ffmpeg muxer name, used as `-f`. */
  container: string;
  /** ffmpeg scaler, used as scale=flags=. Pinned because the scaler changes every pixel. */
  scale_flags: string;
  video: EncodeVideoProfile;
  audio: EncodeAudioProfile | null;
  /**
   * Encoder thread count. Not a creative decision, but it is a determinism input for some
   * encoders, so it is stated rather than left to the machine's core count.
   */
  threads: number;
}

function fail(detail: string): never {
  throw new RenderVideoError("validation_failed", `render_video params are not usable: ${detail}`);
}

function stringArray(value: unknown, name: string): string[] {
  if (!Array.isArray(value) || value.some((entry) => typeof entry !== "string")) {
    fail(`${name} must be an array of strings.`);
  }
  return value as string[];
}

function parseVideo(value: unknown): EncodeVideoProfile {
  if (typeof value !== "object" || value === null) fail("encode.video is missing.");
  const record = value as Record<string, unknown>;
  if (typeof record.codec !== "string" || record.codec.length === 0) fail("encode.video.codec is missing.");
  if (typeof record.pix_fmt !== "string" || record.pix_fmt.length === 0) fail("encode.video.pix_fmt is missing.");
  return { codec: record.codec, pix_fmt: record.pix_fmt, args: stringArray(record.args, "encode.video.args") };
}

function parseAudio(value: unknown): EncodeAudioProfile | null {
  if (value === null || value === undefined) return null;
  if (typeof value !== "object") fail("encode.audio must be an object or null.");
  const record = value as Record<string, unknown>;
  if (typeof record.codec !== "string" || record.codec.length === 0) fail("encode.audio.codec is missing.");
  if (typeof record.sample_fmt !== "string" || record.sample_fmt.length === 0) {
    fail("encode.audio.sample_fmt is missing.");
  }
  return { codec: record.codec, sample_fmt: record.sample_fmt, args: stringArray(record.args, "encode.audio.args") };
}

export function parseEncodeProfile(value: unknown): EncodeProfile {
  if (typeof value !== "object" || value === null) {
    fail("encode is missing. There is no default encode profile; see contracts#56.");
  }
  const record = value as Record<string, unknown>;
  if (typeof record.container !== "string" || record.container.length === 0) fail("encode.container is missing.");
  if (typeof record.scale_flags !== "string" || record.scale_flags.length === 0) {
    fail("encode.scale_flags is missing. The scaler changes every pixel and is not the renderer's to pick.");
  }
  if (!Number.isInteger(record.threads) || (record.threads as number) < 1) {
    fail("encode.threads must be a positive integer.");
  }
  return {
    container: record.container,
    scale_flags: record.scale_flags,
    video: parseVideo(record.video),
    audio: parseAudio(record.audio ?? null),
    threads: record.threads as number,
  };
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
