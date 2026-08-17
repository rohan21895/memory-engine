import { mkdtemp } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import type {
  Clip,
  EDL,
  EdlValidationChecksItemCheckId,
  JobSpec,
  MediaRef,
  RationalTime,
  ReframeTrack,
  TimeRange,
  Track,
} from "../../../contracts/codegen/generated/typescript/index.js";

import { canonicalJson, digestBytes, digestFile, spanAssemblyId } from "../src/digest.js";
import { run, type ToolPaths } from "../src/ffmpeg.js";

/**
 * 30000/1001. The whole point of the contract's RationalTime is that this rate does not
 * survive a trip through float seconds, so the fixtures used here run at it rather than at
 * a comfortable 30.
 */
export const NTSC_30 = 30000 / 1001;
export const SOURCE_WIDTH = 640;
export const SOURCE_HEIGHT = 360;
export const SOURCE_FRAMES = 300;
/** A non-zero source origin, so "file offset = source time - available_range start" is exercised. */
export const SOURCE_ORIGIN = 90_000;

/**
 * Resolved from PATH so the same tests run on a developer's machine and on a CI runner.
 * The suite fails rather than skips when they are absent: a renderer whose tests quietly
 * do not run is worse than one with no tests.
 */
export const TOOLS: ToolPaths = {
  ffmpeg: process.env.MEMORY_ENGINE_FFMPEG ?? "ffmpeg",
  ffprobe: process.env.MEMORY_ENGINE_FFPROBE ?? "ffprobe",
};

export function t(value: number, rate = NTSC_30): RationalTime {
  return { value, rate };
}

export function range(start: number, duration: number, rate = NTSC_30): TimeRange {
  return { start_time: t(start, rate), duration: t(duration, rate) };
}

export interface Fixture {
  directory: string;
  /** Single-file 300-frame source with picture and sound. */
  videoPath: string;
  videoMediaId: string;
  /** The same 300 frames split into two 150-frame chapters. */
  chapterPaths: string[];
  chapterAssemblyId: string;
  musicPath: string;
  musicMediaId: string;
}

let fixturePromise: Promise<Fixture> | null = null;

async function makeVideo(path: string, startFrame: number, frames: number): Promise<void> {
  // testsrc2 and sine are deterministic generators; ffv1 in matroska is lossless, so what
  // the renderer decodes is exactly what was written and no codec noise enters the test.
  await run(TOOLS.ffmpeg, [
    "-nostdin",
    "-hide_banner",
    "-nostats",
    "-fflags",
    "+bitexact",
    "-flags",
    "+bitexact",
    "-f",
    "lavfi",
    "-i",
    `testsrc2=size=${SOURCE_WIDTH}x${SOURCE_HEIGHT}:rate=30000/1001:duration=20`,
    "-f",
    "lavfi",
    "-i",
    "sine=frequency=330:sample_rate=48000:duration=20",
    "-filter_complex",
    `[0:v]trim=start_frame=${startFrame}:end_frame=${startFrame + frames},setpts=PTS-STARTPTS[v];` +
      `[1:a]atrim=start_sample=${Math.round((startFrame * 48000) / NTSC_30)}:` +
      `end_sample=${Math.round(((startFrame + frames) * 48000) / NTSC_30)},asetpts=PTS-STARTPTS[a]`,
    "-map",
    "[v]",
    "-map",
    "[a]",
    "-c:v",
    "ffv1",
    "-pix_fmt",
    "yuv420p",
    "-c:a",
    "pcm_s16le",
    "-map_metadata",
    "-1",
    "-f",
    "matroska",
    "-y",
    path,
  ]);
}

export async function fixture(): Promise<Fixture> {
  fixturePromise ??= (async () => {
    const directory = await mkdtemp(join(tmpdir(), "render-video-fixture-"));
    const videoPath = join(directory, "source.mkv");
    await makeVideo(videoPath, 0, SOURCE_FRAMES);

    const chapterPaths = [join(directory, "chapter-01.mkv"), join(directory, "chapter-02.mkv")];
    await makeVideo(chapterPaths[0]!, 0, SOURCE_FRAMES / 2);
    await makeVideo(chapterPaths[1]!, SOURCE_FRAMES / 2, SOURCE_FRAMES / 2);

    const musicPath = join(directory, "music.wav");
    await run(TOOLS.ffmpeg, [
      "-nostdin",
      "-hide_banner",
      "-nostats",
      "-fflags",
      "+bitexact",
      "-flags",
      "+bitexact",
      "-f",
      "lavfi",
      "-i",
      "sine=frequency=220:sample_rate=48000:duration=20",
      "-c:a",
      "pcm_s16le",
      "-map_metadata",
      "-1",
      "-y",
      musicPath,
    ]);

    const chapterDigests = [await digestFile(chapterPaths[0]!), await digestFile(chapterPaths[1]!)];
    return {
      directory,
      videoPath,
      videoMediaId: await digestFile(videoPath),
      chapterPaths,
      chapterAssemblyId: spanAssemblyId(chapterDigests),
      musicPath,
      musicMediaId: await digestFile(musicPath),
    };
  })();
  return fixturePromise;
}

export async function workspace(prefix: string): Promise<string> {
  return mkdtemp(join(tmpdir(), `render-video-${prefix}-`));
}

const HASH = (seed: string): string => digestBytes(new TextEncoder().encode(seed));

const ALL_PASSING: EdlValidationChecksItemCheckId[] = [
  "source_range_within_available",
  "media_refs_resolvable",
  "timeline_contiguous",
  "transition_handles_available",
  "reframe_aspect_matches_target",
  "reframe_keyframes_ordered",
  "duration_within_max",
  "music_license_covers_destination",
  "beat_alignment_within_tolerance",
  "determinism_digest_present",
];

export function videoRef(mediaId: string, isSpan = false): MediaRef {
  return {
    media_ref_id: isSpan ? "src-span" : "src-a",
    media_id: mediaId,
    media_kind: "video",
    available_range: range(SOURCE_ORIGIN, SOURCE_FRAMES),
    is_span_assembly: isSpan,
    expected_frame_rate: NTSC_30,
    label: null,
  };
}

export function musicRef(mediaId: string): MediaRef {
  return {
    media_ref_id: "src-music",
    media_id: mediaId,
    media_kind: "music",
    available_range: range(0, 560),
    is_span_assembly: false,
    expected_frame_rate: null,
    label: null,
  };
}

export function clip(
  clipId: string,
  mediaRefId: string,
  sourceStart: number,
  duration: number,
  overrides: Partial<Clip> = {},
): Clip {
  return {
    item_type: "clip",
    clip_id: clipId,
    name: clipId,
    media_ref_id: mediaRefId,
    source_range: range(sourceStart, duration),
    moment_id: null,
    enabled: true,
    time_effect: null,
    reframe_track_id: null,
    color_ops: [],
    audio: null,
    beat_lock: null,
    story_beat_id: null,
    markers: [],
    ...overrides,
  };
}

/** A 9:16 crop of a 16:9 source that pans left to right and then holds. */
export function reframeTrack(id: string, sourceStart: number, sourceEnd: number): ReframeTrack {
  const width = 0.31640625;
  return {
    reframe_track_id: id,
    target_aspect_ratio: { numerator: 9, denominator: 16 },
    keyframes: [
      {
        time: t(sourceStart),
        crop: { x: 0.2, y: 0, w: width, h: 1, rotation_deg: 0 },
        interpolation: "linear",
        bezier_control: null,
        confidence: 0.9,
      },
      {
        time: t(Math.round((sourceStart + sourceEnd) / 2)),
        crop: { x: 0.44, y: 0, w: width, h: 1, rotation_deg: 0 },
        interpolation: "hold",
        bezier_control: null,
        confidence: 0.9,
      },
      {
        time: t(sourceEnd),
        crop: { x: 0.44, y: 0, w: width, h: 1, rotation_deg: 0 },
        interpolation: "hold",
        bezier_control: null,
        confidence: 0.9,
      },
    ],
    subject_lock: { source: "sam2_track", subject_ref: "obj-1", person_id: null, keep_in_frame: "head", headroom: 0.1 },
    smoothing: { method: "savitzky_golay", window_frames: 9, max_velocity_per_second: 0.4, deadzone: 0.01 },
    fallback: "hold_last_keyframe",
  };
}

export interface EdlOptions {
  tracks: Track[];
  reframeTracks?: ReframeTrack[];
  mediaRefs: MediaRef[];
  audioPlan?: EDL["audio_plan"];
  beatGrid?: EDL["beat_grid"];
  resolution?: { width: number; height: number };
  aspect?: { numerator: number; denominator: number };
  maxDurationFrames?: number;
}

export function makeEdl(options: EdlOptions): EDL {
  const resolution = options.resolution ?? { width: 360, height: 640 };
  const aspect = options.aspect ?? { numerator: 9, denominator: 16 };
  const edl: EDL = {
    schema_version: "v0",
    edl_id: HASH("edl"),
    name: "render-video test",
    kind: "reel",
    rate: NTSC_30,
    global_start_time: t(0),
    target: {
      destination: "instagram_reel",
      resolution,
      aspect_ratio: aspect,
      target_duration: null,
      max_duration: options.maxDurationFrames ? t(options.maxDurationFrames) : null,
      loudness_target_lufs: -14,
    },
    media_refs: options.mediaRefs,
    tracks: options.tracks,
    reframe_tracks: options.reframeTracks ?? [],
    audio_plan: options.audioPlan ?? null,
    beat_grid: options.beatGrid ?? null,
    story_arc: null,
    color_pipeline: {
      input_transform: "auto",
      working_space: "rec709",
      output_transform: "rec709",
      tone_map_hdr_to_sdr: true,
    },
    variant: null,
    determinism: {
      planner: "test-planner",
      planner_version: "1.0.0",
      seed: 7,
      inputs_digest: HASH("inputs"),
      generated_at: null,
    },
    validation: {
      status: "pass",
      checks: ALL_PASSING.map((check_id) => ({ check_id, passed: true, severity: "error", detail: "", clip_id: null })),
      validated_at: null,
      validator_version: "test/1",
    },
    otio: null,
  };
  return edl;
}

export const H264_MP4 = {
  container: "mp4",
  scale_flags: "bicubic",
  video: { codec: "libx264", pix_fmt: "yuv420p", args: ["-crf", "26", "-preset", "veryfast", "-g", "30"] },
  audio: { codec: "aac", sample_fmt: "fltp", args: ["-b:a", "128k"] },
  threads: 1,
} as const;

export const FFV1_MKV = {
  container: "matroska",
  scale_flags: "bicubic",
  video: { codec: "ffv1", pix_fmt: "yuv420p", args: [] },
  audio: { codec: "pcm_s16le", sample_fmt: "s16", args: [] },
  threads: 1,
} as const;

export function makeJob(edlId: string, params: Record<string, unknown>): JobSpec {
  return {
    schema_version: "v0",
    job_id: HASH("job"),
    job_type: "render_video",
    inputs: { edl_id: edlId },
    params,
    params_digest: digestBytes(new TextEncoder().encode(canonicalJson(params))),
    scope: "test",
    requirements: { compute: "cpu", requires_source_file: true },
    egress: { requires_egress: false },
    state: { status: "pending", attempts: 0 },
    checkpoint: {
      resumable: true,
      cursor: null,
      checkpoint_version: 1,
      completed_input_ids: [],
      partial_output_ids: [],
    },
    outputs: [],
  };
}
