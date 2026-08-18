import { join } from "node:path";

import { beforeAll, describe, expect, it } from "vitest";

import type { EDL, MediaRef, Track } from "../../../contracts/codegen/generated/typescript/index.js";

import { digestFile, spanAssemblyId } from "../src/digest.js";
import type { EncodeProfile } from "../src/encode.js";
import { run } from "../src/ffmpeg.js";
import { decodedFrameDigest, renderVideo, type RenderVideoResult } from "../src/renderer.js";
import { clip, FFV1_MKV, makeEdl, range, t, TOOLS, videoRef, workspace } from "./helpers.js";

interface EncodedSource {
  path: string;
  mediaId: string;
  frames: number;
  rate: number;
}

const RATE_30 = 30;
const RATE_NTSC = 30_000 / 1_001;
let fixtureDirectory: string;
let source30: EncodedSource;
let sourceNtsc: EncodedSource;
let sourceCoarseTimeBase: EncodedSource;
let sourceDeceptiveVfr: EncodedSource;
let spanPaths: string[];
let spanId: string;

async function makeH264(
  name: string,
  rate: number,
  rateArg: string,
  startFrame: number,
  frames: number,
  withAudio: boolean,
  trackTimescale?: number,
): Promise<EncodedSource> {
  const path = join(fixtureDirectory, name);
  const endFrame = startFrame + frames;
  const duration = endFrame / rate + 1;
  const audioStart = Math.round((startFrame * 48_000) / rate);
  const audioEnd = Math.round((endFrame * 48_000) / rate);
  const args = [
    "-nostdin", "-hide_banner", "-nostats",
    "-f", "lavfi", "-i", `testsrc2=size=320x180:rate=${rateArg}:duration=${duration}`,
    ...(withAudio ? ["-f", "lavfi", "-i", `sine=frequency=330:sample_rate=48000:duration=${duration}`] : []),
    "-filter_complex",
    withAudio
      ? `[0:v]trim=start_frame=${startFrame}:end_frame=${endFrame},setpts=PTS-STARTPTS[v];` +
        `[1:a]atrim=start_sample=${audioStart}:end_sample=${audioEnd},asetpts=PTS-STARTPTS[a]`
      : `[0:v]trim=start_frame=${startFrame}:end_frame=${endFrame},setpts=PTS-STARTPTS[v]`,
    "-map", "[v]",
    ...(withAudio ? ["-map", "[a]"] : []),
    "-r", rateArg,
    "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
    "-profile:v", "high", "-flags:v", "+cgop",
    "-g", "120", "-keyint_min", "120", "-sc_threshold", "0",
    ...(withAudio ? ["-c:a", "aac", "-b:a", "128k"] : ["-an"]),
    ...(trackTimescale ? ["-video_track_timescale", String(trackTimescale)] : []),
    "-map_metadata", "-1", "-frames:v", String(frames), "-y", path,
  ];
  await run(TOOLS.ffmpeg, args);
  return { path, mediaId: await digestFile(path), frames, rate };
}

async function makeDeceptiveVfr(name: string): Promise<EncodedSource> {
  const path = join(fixtureDirectory, name);
  const staged = join(fixtureDirectory, `staged-${name}`);
  const frames = 600;
  // Across the full file both reported rates are exactly 30/1 and the time base
  // admits an integral 3000 ticks/frame. Individual packets are nevertheless VFR.
  // Encode one sentinel frame so packet 599 receives an explicit duration, then
  // copy only the first 600 packets. That keeps the 20-second average portable
  // across ffprobe versions that differ in how they infer a final packet duration.
  await run(TOOLS.ffmpeg, [
    "-nostdin", "-hide_banner", "-nostats",
    "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=30:duration=21",
    "-vf", "settb=expr=1/90000,setpts=if(eq(N\\,599)\\,1797000\\,if(eq(N\\,600)\\,1800000\\,floor(N/2)*6000))",
    "-fps_mode", "passthrough",
    "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
    "-profile:v", "high", "-flags:v", "+cgop",
    "-g", "120", "-keyint_min", "120", "-sc_threshold", "0",
    "-pix_fmt", "yuv420p", "-video_track_timescale", "90000",
    "-map_metadata", "-1", "-frames:v", String(frames + 1), "-y", staged,
  ]);
  await run(TOOLS.ffmpeg, [
    "-nostdin", "-hide_banner", "-nostats", "-i", staged,
    "-map", "0:v:0", "-c", "copy", "-frames:v", String(frames),
    "-map_metadata", "-1", "-y", path,
  ]);
  return { path, mediaId: await digestFile(path), frames, rate: RATE_30 };
}

beforeAll(async () => {
  fixtureDirectory = await workspace("input-seek-fixtures");
  source30 = await makeH264("source-30.mp4", RATE_30, "30", 0, 1_800, true);
  sourceNtsc = await makeH264("source-ntsc.mp4", RATE_NTSC, "30000/1001", 0, 600, false);
  sourceCoarseTimeBase = await makeH264("source-coarse-time-base.mp4", RATE_30, "30", 0, 600, false, 1_000);
  sourceDeceptiveVfr = await makeDeceptiveVfr("source-vfr-reported-cfr.mp4");
  const first = await makeH264("span-01.mp4", RATE_NTSC, "30000/1001", 0, 300, false);
  const second = await makeH264("span-02.mp4", RATE_NTSC, "30000/1001", 300, 300, false);
  spanPaths = [first.path, second.path];
  spanId = spanAssemblyId([first.mediaId, second.mediaId]);
}, 180_000);

function ref(source: EncodedSource, isSpan = false): MediaRef {
  const media = videoRef(source.mediaId, isSpan);
  media.available_range = range(0, source.frames, source.rate);
  media.expected_frame_rate = source.rate;
  return media;
}

function plan(media: MediaRef, rate: number, items: Track["items"], withAudio: boolean): EDL {
  const edl = makeEdl({
    mediaRefs: [media],
    resolution: { width: 160, height: 90 },
    aspect: { numerator: 16, denominator: 9 },
    audioPlan: withAudio ? {
      music: [],
      ambient: {
        enabled: true,
        default_gain_db: 0,
        preserve_speech: true,
        high_pass_hz: null,
        noise_suppression: "none",
        per_clip_gain_db: [],
      },
      ducking: [],
      mix: {
        master_gain_db: 0,
        loudness_target_lufs: -14,
        true_peak_ceiling_db: -1,
        limiter: true,
        channels: "stereo",
        sample_rate: 48_000,
      },
    } : undefined,
    tracks: [{ track_id: "v1", kind: "video", name: "V1", role: "primary", enabled: true, items }],
  });
  edl.kind = "film";
  edl.rate = rate;
  edl.global_start_time = t(0, rate);
  edl.target.destination = "master";
  return edl;
}

function sourceClip(id: string, mediaRefId: string, start: number, duration: number, rate: number, audio: boolean) {
  return clip(id, mediaRefId, start, duration, {
    source_range: range(start, duration, rate),
    audio: audio
      ? { gain_db: 0, muted: false, fade_in: null, fade_out: null, audio_extends_past_out: null }
      : null,
  });
}

function encode(withAudio: boolean): EncodeProfile {
  return {
    container: FFV1_MKV.container,
    scale_flags: FFV1_MKV.scale_flags,
    threads: FFV1_MKV.threads,
    video: { ...FFV1_MKV.video, args: [...FFV1_MKV.video.args] },
    audio: withAudio ? { ...FFV1_MKV.audio, args: [...FFV1_MKV.audio.args] } : null,
  };
}

function deliveryEncode(): EncodeProfile {
  return {
    container: "mp4",
    scale_flags: "bicubic",
    threads: 1,
    video: {
      codec: "libx264",
      pix_fmt: "yuv420p",
      args: [
        "-preset", "medium", "-crf", "18", "-profile:v", "high", "-flags:v", "+cgop",
        "-g", "60", "-keyint_min", "60", "-sc_threshold", "0",
      ],
    },
    audio: null,
  };
}

async function render(
  edl: EDL,
  source: { mediaId: string; paths: string[] },
  prefix: string,
  withAudio: boolean,
  inputSeeking?: "disabled",
  profile: EncodeProfile = encode(withAudio),
): Promise<RenderVideoResult> {
  return renderVideo(edl, {
    sources: { [source.mediaId]: { paths: source.paths } },
    encode: profile,
    workDirectory: await workspace(prefix),
    tools: TOOLS,
    ...(inputSeeking ? { inputSeeking } : {}),
  });
}

interface PacketTiming {
  streams: Array<{ avg_frame_rate?: string; r_frame_rate?: string; time_base?: string }>;
  packets: Array<{ dts?: number; pts?: number; duration?: number }>;
}

async function packetTiming(path: string): Promise<PacketTiming> {
  const { stdout } = await run(TOOLS.ffprobe, [
    "-v", "error",
    "-select_streams", "v:0",
    "-show_packets",
    "-show_streams",
    "-show_entries", "stream=avg_frame_rate,r_frame_rate,time_base:packet=dts,pts,duration",
    "-of", "json",
    path,
  ]);
  return JSON.parse(stdout) as PacketTiming;
}

function lateClips(mediaRefId: string): Track["items"] {
  return [1, 119, 120, 121, 257, 401, 557].map((offset, index) =>
    sourceClip(`late-${index}`, mediaRefId, offset, 30, RATE_30, false),
  );
}

async function decodedAudioDigest(path: string, prefix: string): Promise<string> {
  const work = await workspace(prefix);
  const raw = join(work, "decoded-audio.pcm");
  await run(TOOLS.ffmpeg, [
    "-nostdin", "-hide_banner", "-nostats", "-i", path,
    "-map", "0:a", "-c:a", "pcm_s16le", "-f", "s16le", "-y", raw,
  ]);
  return digestFile(raw);
}

describe("bounded H.264 input seeking", () => {
  it("matches non-keyframe picture and audio at 30 fps while leaving audio inputs unseeked", async () => {
    const media = ref(source30);
    const audio = true;
    const edl = plan(media, RATE_30, [
      sourceClip("near-start", media.media_ref_id, 1, 30, RATE_30, audio),
      sourceClip("late-a", media.media_ref_id, 367, 60, RATE_30, audio),
      sourceClip("late-b", media.media_ref_id, 557, 60, RATE_30, audio),
      sourceClip("late-c", media.media_ref_id, 721, 30, RATE_30, audio),
    ], audio);

    const baseline = await render(edl, { mediaId: source30.mediaId, paths: [source30.path] }, "seek-30-off", audio, "disabled");
    const sought = await render(edl, { mediaId: source30.mediaId, paths: [source30.path] }, "seek-30-on", audio);
    const digestWork = await workspace("seek-30-digest");

    expect(await decodedFrameDigest(TOOLS, sought.path, digestWork)).toBe(
      await decodedFrameDigest(TOOLS, baseline.path, digestWork),
    );
    expect(await decodedAudioDigest(sought.path, "seek-30-audio-on")).toBe(
      await decodedAudioDigest(baseline.path, "seek-30-audio-off"),
    );
    expect(sought.command.filter((arg) => arg === "-ss").length).toBeGreaterThan(0);
    expect(sought.command.filter((arg) => arg === "-ss").length).toBeLessThan(
      sought.command.filter((arg) => arg === source30.path).length,
    );
    expect(baseline.command).not.toContain("-ss");
  }, 240_000);

  it("matches non-keyframe cuts at 30000/1001 without accumulating fractional-rate error", async () => {
    const media = ref(sourceNtsc);
    const edl = plan(media, RATE_NTSC, [
      sourceClip("ntsc-a", media.media_ref_id, 257, 60, RATE_NTSC, false),
      {
        item_type: "transition",
        transition_id: "ntsc-dissolve",
        transition_type: "dissolve",
        in_offset: t(6, RATE_NTSC),
        out_offset: t(6, RATE_NTSC),
        easing: "linear",
        parameters: {},
      },
      sourceClip("ntsc-b", media.media_ref_id, 401, 60, RATE_NTSC, false),
    ], false);

    const baseline = await render(edl, { mediaId: sourceNtsc.mediaId, paths: [sourceNtsc.path] }, "seek-ntsc-off", false, "disabled");
    const sought = await render(edl, { mediaId: sourceNtsc.mediaId, paths: [sourceNtsc.path] }, "seek-ntsc-on", false);
    const digestWork = await workspace("seek-ntsc-digest");

    expect(await decodedFrameDigest(TOOLS, sought.path, digestWork)).toBe(
      await decodedFrameDigest(TOOLS, baseline.path, digestWork),
    );
    expect(sought.command).toContain((257 / RATE_NTSC).toFixed(9));
    expect(sought.command).toContain((395 / RATE_NTSC).toFixed(9));
  }, 240_000);

  it("falls back when a 30 fps MP4 time base cannot represent an integral frame tick", async () => {
    const media = ref(sourceCoarseTimeBase);
    const edl = plan(media, RATE_30, lateClips(media.media_ref_id), false);
    const resolver = { mediaId: sourceCoarseTimeBase.mediaId, paths: [sourceCoarseTimeBase.path] };
    const baseline = await render(edl, resolver, "seek-coarse-off", false, "disabled", deliveryEncode());
    const safe = await render(edl, resolver, "seek-coarse-safe", false, undefined, deliveryEncode());
    const sourceTiming = await packetTiming(sourceCoarseTimeBase.path);

    expect(sourceTiming.streams[0]).toMatchObject({
      avg_frame_rate: "30/1",
      r_frame_rate: "30/1",
      time_base: "1/1000",
    });
    const sourceDts = sourceTiming.packets
      .map((packet) => packet.dts)
      .filter((value): value is number => value !== undefined);
    expect(new Set(sourceDts.slice(1).map((value, index) => value - sourceDts[index]!))).toEqual(new Set([33, 34]));
    expect(safe.command).not.toContain("-ss");
    expect(safe.commandGraphDigest).toBe(baseline.commandGraphDigest);
    expect(safe.id).toBe(baseline.id);
    expect(await packetTiming(safe.path)).toEqual(await packetTiming(baseline.path));
    expect(await decodedFrameDigest(TOOLS, safe.path, await workspace("seek-coarse-digest"))).toBe(
      await decodedFrameDigest(TOOLS, baseline.path, await workspace("seek-coarse-baseline-digest")),
    );
  }, 240_000);

  it("falls back when equal reported rates hide variable packet cadence", async () => {
    const media = ref(sourceDeceptiveVfr);
    const edl = plan(media, RATE_30, lateClips(media.media_ref_id), false);
    const resolver = { mediaId: sourceDeceptiveVfr.mediaId, paths: [sourceDeceptiveVfr.path] };
    const baseline = await render(edl, resolver, "seek-vfr-off", false, "disabled", deliveryEncode());
    const safe = await render(edl, resolver, "seek-vfr-safe", false, undefined, deliveryEncode());
    const sourceTiming = await packetTiming(sourceDeceptiveVfr.path);

    expect(sourceTiming.streams[0]).toMatchObject({
      avg_frame_rate: "30/1",
      r_frame_rate: "30/1",
      time_base: "1/90000",
    });
    const dts = sourceTiming.packets.map((packet) => packet.dts).filter((value): value is number => value !== undefined);
    const steps = new Set(dts.slice(1).map((value, index) => value - dts[index]!));
    expect(steps.size).toBeGreaterThan(1);
    expect(safe.command).not.toContain("-ss");
    expect(safe.commandGraphDigest).toBe(baseline.commandGraphDigest);
    expect(safe.id).toBe(baseline.id);
    expect(await packetTiming(safe.path)).toEqual(await packetTiming(baseline.path));
    expect(await decodedFrameDigest(TOOLS, safe.path, await workspace("seek-vfr-digest"))).toBe(
      await decodedFrameDigest(TOOLS, baseline.path, await workspace("seek-vfr-baseline-digest")),
    );
  }, 240_000);

  it("keeps H.264 span assemblies on the proven no-seek path", async () => {
    const span: EncodedSource = { path: spanPaths[0]!, mediaId: spanId, frames: 600, rate: RATE_NTSC };
    const media = ref(span, true);
    const edl = plan(media, RATE_NTSC, [
      sourceClip("across-span", media.media_ref_id, 280, 60, RATE_NTSC, false),
    ], false);
    const resolver = { mediaId: spanId, paths: spanPaths };

    const baseline = await render(edl, resolver, "seek-span-off", false, "disabled");
    const safe = await render(edl, resolver, "seek-span-safe", false);
    const digestWork = await workspace("seek-span-digest");

    expect(safe.command).not.toContain("-ss");
    expect(safe.commandGraphDigest).toBe(baseline.commandGraphDigest);
    expect(await decodedFrameDigest(TOOLS, safe.path, digestWork)).toBe(
      await decodedFrameDigest(TOOLS, baseline.path, digestWork),
    );
  }, 240_000);

  it("benchmarks repeated late-offset cuts", async () => {
    const media = ref(source30);
    const offsets = [1_200, 1_260, 1_320, 1_380, 1_440, 1_500, 1_560, 1_620];
    const edl = plan(
      media,
      RATE_30,
      offsets.map((offset, index) => sourceClip(`late-${index}`, media.media_ref_id, offset, 30, RATE_30, false)),
      false,
    );

    const baselineStarted = performance.now();
    const baseline = await render(
      edl,
      { mediaId: source30.mediaId, paths: [source30.path] },
      "seek-benchmark-off",
      false,
      "disabled",
    );
    const baselineMs = performance.now() - baselineStarted;
    const soughtStarted = performance.now();
    const sought = await render(
      edl,
      { mediaId: source30.mediaId, paths: [source30.path] },
      "seek-benchmark-on",
      false,
    );
    const soughtMs = performance.now() - soughtStarted;
    const digestWork = await workspace("seek-benchmark-digest");

    expect(await decodedFrameDigest(TOOLS, sought.path, digestWork)).toBe(
      await decodedFrameDigest(TOOLS, baseline.path, digestWork),
    );
    console.info(
      `INPUT_SEEK_BENCHMARK no_seek_ms=${baselineMs.toFixed(1)} seek_ms=${soughtMs.toFixed(1)} ` +
      `speedup=${(baselineMs / soughtMs).toFixed(2)}x`,
    );
  }, 240_000);
});
