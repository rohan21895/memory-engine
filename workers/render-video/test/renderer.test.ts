import { readFile, writeFile } from "node:fs/promises";
import { join } from "node:path";

import { beforeAll, describe, expect, it } from "vitest";

import type { AudioPlan, EDL, ReframeTrack, Track } from "../../../contracts/codegen/generated/typescript/index.js";

import { digestFile } from "../src/digest.js";
import { run } from "../src/ffmpeg.js";
import { decodedFrameDigest, renderVideo, type RenderVideoResult } from "../src/renderer.js";
import {
  clip,
  FFV1_MKV,
  fixture,
  H264_MP4,
  makeEdl,
  NTSC_30,
  range,
  reframeTrack,
  SOURCE_FRAMES,
  SOURCE_ORIGIN,
  t,
  TOOLS,
  videoRef,
  musicRef,
  workspace,
  type Fixture,
} from "./helpers.js";

let source: Fixture;

beforeAll(async () => {
  // Fail loudly if the media tools are absent rather than skipping: a renderer whose tests
  // silently do not run is worse than one with no tests.
  await run(TOOLS.ffmpeg, ["-hide_banner", "-version"]);
  source = await fixture();
}, 180_000);

function encode(profile: typeof FFV1_MKV | typeof H264_MP4, withAudio: boolean) {
  return {
    container: profile.container,
    scale_flags: profile.scale_flags,
    video: { codec: profile.video.codec, pix_fmt: profile.video.pix_fmt, args: [...profile.video.args] },
    audio: withAudio ? { ...profile.audio, args: [...profile.audio.args] } : null,
    threads: profile.threads,
  };
}

function videoOnlyEdl(reframe: ReframeTrack | null = reframeTrack("rf-1", SOURCE_ORIGIN, SOURCE_ORIGIN + 60)): EDL {
  const items: Track["items"] = [
    clip("clip-01", "src-a", SOURCE_ORIGIN, 60, reframe ? { reframe_track_id: reframe.reframe_track_id } : {}),
    { item_type: "gap", gap_id: "hold", duration: t(10), fill: "black" },
    clip("clip-02", "src-a", SOURCE_ORIGIN + 100, 40),
  ];
  return makeEdl({
    mediaRefs: [videoRef(source.videoMediaId)],
    reframeTracks: reframe ? [reframe] : [],
    tracks: [{ track_id: "v1", kind: "video", name: "V1", role: "primary", enabled: true, items }],
  });
}

async function render(edl: EDL, options: { profile?: typeof FFV1_MKV | typeof H264_MP4; audio?: boolean; sources?: Record<string, { paths: string[] }>; prefix?: string }): Promise<RenderVideoResult> {
  return renderVideo(edl, {
    sources: options.sources ?? { [source.videoMediaId]: { paths: [source.videoPath] } },
    encode: encode(options.profile ?? FFV1_MKV, options.audio ?? false),
    workDirectory: await workspace(options.prefix ?? "run"),
    tools: TOOLS,
  });
}

describe("determinism", () => {
  it("renders one EDL to byte-identical FFV1 twice, from two independent work directories", async () => {
    const first = await render(videoOnlyEdl(), { prefix: "det-a" });
    const second = await render(videoOnlyEdl(), { prefix: "det-b" });

    expect(first.commandGraphDigest).toBe(second.commandGraphDigest);
    expect(first.id).toBe(second.id);
    expect(first.byteSize).toBe(second.byteSize);
    expect((await readFile(first.path)).equals(await readFile(second.path))).toBe(true);
  }, 180_000);

  it("renders one EDL to byte-identical H.264/MP4 twice", async () => {
    const first = await render(videoOnlyEdl(), { profile: H264_MP4, prefix: "det-h264-a" });
    const second = await render(videoOnlyEdl(), { profile: H264_MP4, prefix: "det-h264-b" });

    expect(first.id).toBe(second.id);
    expect((await readFile(first.path)).equals(await readFile(second.path))).toBe(true);
  }, 180_000);

  it("produces the same decoded picture from both codecs' runs", async () => {
    const work = await workspace("decoded");
    const lossless = await render(videoOnlyEdl(), { prefix: "dec-a" });
    const again = await render(videoOnlyEdl(), { prefix: "dec-b" });
    expect(await decodedFrameDigest(TOOLS, lossless.path, work)).toBe(
      await decodedFrameDigest(TOOLS, again.path, work),
    );
  }, 180_000);

  it("changes the command-graph digest when the plan changes", async () => {
    const first = await render(videoOnlyEdl(), { prefix: "digest-a" });
    const moved = videoOnlyEdl();
    moved.tracks[0]!.items[2] = clip("clip-02", "src-a", SOURCE_ORIGIN + 101, 40);
    const second = await render(moved, { prefix: "digest-b" });
    expect(second.commandGraphDigest).not.toBe(first.commandGraphDigest);
    expect(second.id).not.toBe(first.id);
  }, 180_000);
});

describe("the picture the plan asked for", () => {
  it("lands the exact frame count and target size the EDL declares", async () => {
    const result = await render(videoOnlyEdl(), { prefix: "count" });
    expect(result.verification).toMatchObject({
      frameCount: 110,
      expectedFrameCount: 110,
      width: 360,
      height: 640,
    });
    expect(result.verification.frameRate).toBeCloseTo(NTSC_30, 6);
  }, 120_000);

  it("moves the crop between keyframes rather than holding the first one", async () => {
    const work = await workspace("crop");
    const panning = await render(videoOnlyEdl(), { prefix: "crop-pan" });

    const staticTrack = reframeTrack("rf-1", SOURCE_ORIGIN, SOURCE_ORIGIN + 60);
    for (const keyframe of staticTrack.keyframes) keyframe.crop.x = 0.2;
    const stationary = await render(videoOnlyEdl(staticTrack), { prefix: "crop-static" });

    expect(await decodedFrameDigest(TOOLS, panning.path, work)).not.toBe(
      await decodedFrameDigest(TOOLS, stationary.path, work),
    );
    expect(panning.filterGraph).toMatch(/crop=w=203:h=360:x='if\(lt\(n,/);
  }, 240_000);

  it("keeps a transition inside the timeline it was planned into", async () => {
    const withCut = makeEdl({
      mediaRefs: [videoRef(source.videoMediaId)],
      tracks: [
        {
          track_id: "v1",
          kind: "video",
          name: "V1",
          role: "primary",
          enabled: true,
          items: [
            clip("clip-01", "src-a", SOURCE_ORIGIN + 20, 60),
            clip("clip-02", "src-a", SOURCE_ORIGIN + 150, 60),
          ],
        },
      ],
    });
    const withDissolve = makeEdl({
      mediaRefs: [videoRef(source.videoMediaId)],
      tracks: [
        {
          track_id: "v1",
          kind: "video",
          name: "V1",
          role: "primary",
          enabled: true,
          items: [
            clip("clip-01", "src-a", SOURCE_ORIGIN + 20, 60),
            {
              item_type: "transition",
              transition_id: "x1",
              transition_type: "dissolve",
              in_offset: t(6),
              out_offset: t(6),
              easing: "linear",
              parameters: {},
            },
            clip("clip-02", "src-a", SOURCE_ORIGIN + 150, 60),
          ],
        },
      ],
    });

    const cut = await render(withCut, { prefix: "cut" });
    const dissolve = await render(withDissolve, { prefix: "dissolve" });
    const work = await workspace("transition");

    // A transition borrows handle frames from either side; it must not lengthen the cut.
    expect(cut.verification.frameCount).toBe(120);
    expect(dissolve.verification.frameCount).toBe(120);
    expect(dissolve.program.segments).toBe(3);
    expect(await decodedFrameDigest(TOOLS, cut.path, work)).not.toBe(
      await decodedFrameDigest(TOOLS, dissolve.path, work),
    );
  }, 240_000);
});

describe("span assemblies", () => {
  function spanEdl(): EDL {
    return makeEdl({
      mediaRefs: [videoRef(source.chapterAssemblyId, true)],
      tracks: [
        {
          track_id: "v1",
          kind: "video",
          name: "V1",
          role: "primary",
          enabled: true,
          // Frames 140-170 of the recording, which straddles the chapter split at 150.
          items: [clip("clip-01", "src-span", SOURCE_ORIGIN + 140, 30)],
        },
      ],
    });
  }

  it("renders a clip across a chapter boundary identically to the same frames from one file", async () => {
    const work = await workspace("span");
    const fromSpan = await render(spanEdl(), {
      sources: { [source.chapterAssemblyId]: { paths: source.chapterPaths } },
      prefix: "span-a",
    });

    const single = makeEdl({
      mediaRefs: [videoRef(source.videoMediaId)],
      tracks: [
        {
          track_id: "v1",
          kind: "video",
          name: "V1",
          role: "primary",
          enabled: true,
          items: [clip("clip-01", "src-a", SOURCE_ORIGIN + 140, 30)],
        },
      ],
    });
    const fromFile = await render(single, { prefix: "span-b" });

    expect(fromSpan.verification.frameCount).toBe(30);
    expect(await decodedFrameDigest(TOOLS, fromSpan.path, work)).toBe(
      await decodedFrameDigest(TOOLS, fromFile.path, work),
    );
  }, 240_000);

  it("refuses chapters handed to it in the wrong order", async () => {
    await expect(
      render(spanEdl(), {
        sources: { [source.chapterAssemblyId]: { paths: [...source.chapterPaths].reverse() } },
        prefix: "span-order",
      }),
    ).rejects.toThrow(/chapters are in the wrong order/);
  }, 120_000);
});

describe("a source that is not what the plan believes fails loudly", () => {
  it("refuses a missing file", async () => {
    await expect(
      render(videoOnlyEdl(), {
        sources: { [source.videoMediaId]: { paths: [join(source.directory, "absent.mkv")] } },
        prefix: "missing",
      }),
    ).rejects.toThrow(/does not exist/);
  }, 60_000);

  it("refuses a zero-byte file", async () => {
    const empty = join(await workspace("empty"), "empty.mkv");
    await writeFile(empty, "");
    await expect(
      render(videoOnlyEdl(), { sources: { [source.videoMediaId]: { paths: [empty] } }, prefix: "zero" }),
    ).rejects.toThrow(/zero bytes/);
  }, 60_000);

  it("refuses a readable file whose content is not the footage the plan was made against", async () => {
    await expect(
      render(videoOnlyEdl(), {
        sources: { [source.videoMediaId]: { paths: [source.chapterPaths[0]!] } },
        prefix: "wrong",
      }),
    ).rejects.toThrow(/This is the wrong footage/);
  }, 60_000);

  it("refuses a source that is shorter than its declared available_range", async () => {
    const edl = videoOnlyEdl();
    edl.media_refs[0]!.available_range = range(SOURCE_ORIGIN, SOURCE_FRAMES + 1);
    await expect(render(edl, { prefix: "short" })).rejects.toThrow(/how a clip becomes black frames/);
  }, 60_000);

  it("refuses a source whose declared frame rate is not the timeline rate", async () => {
    const edl = videoOnlyEdl();
    edl.media_refs[0]!.expected_frame_rate = 30;
    await expect(render(edl, { prefix: "rate" })).rejects.toThrow(/will not do it silently/);
  }, 60_000);

  it("refuses a media_ref the timeline never reads", async () => {
    const edl = videoOnlyEdl();
    edl.media_refs.push(musicRef(source.musicMediaId));
    await expect(
      render(edl, {
        sources: {
          [source.videoMediaId]: { paths: [source.videoPath] },
          [source.musicMediaId]: { paths: [source.musicPath] },
        },
        prefix: "unused",
      }),
    ).rejects.toThrow(/declared and never read/);
  }, 60_000);
});

describe("the audio plan", () => {
  function audioPlan(limiter: boolean): AudioPlan {
    return {
      music: [
        {
          cue_id: "cue-01",
          media_ref_id: "src-music",
          source_range: range(0, 100),
          timeline_range: range(0, 100),
          license: {
            provider: "catalog_partner",
            license_id: "TEST-1",
            track_title: "Test bed",
            attribution_required: false,
            attribution_text: null,
            license_type: "royalty_free",
            cleared_for: ["private_playback", "social_share"],
          },
          gain_db: 0,
          fade_in: t(6),
          fade_out: t(6),
          loop: false,
        },
      ],
      ambient: {
        enabled: true,
        default_gain_db: -12,
        preserve_speech: true,
        high_pass_hz: null,
        noise_suppression: "none",
        per_clip_gain_db: [],
      },
      ducking: [
        {
          rule_id: "duck-music",
          target: "music",
          trigger: "explicit_ranges",
          reduction_db: 9,
          threshold_db: null,
          ratio: null,
          attack_ms: 0,
          release_ms: 0,
          ranges: [range(40, 30)],
        },
      ],
      mix: {
        master_gain_db: 0,
        loudness_target_lufs: -14,
        true_peak_ceiling_db: -1,
        limiter,
        channels: "stereo",
        sample_rate: 48000,
      },
    };
  }

  function audioEdl(limiter: boolean): EDL {
    return makeEdl({
      mediaRefs: [videoRef(source.videoMediaId), musicRef(source.musicMediaId)],
      audioPlan: audioPlan(limiter),
      tracks: [
        {
          track_id: "v1",
          kind: "video",
          name: "V1",
          role: "primary",
          enabled: true,
          items: [
            clip("clip-01", "src-a", SOURCE_ORIGIN, 50, {
              audio: { gain_db: 0, muted: false, fade_in: null, fade_out: null, audio_extends_past_out: t(5) },
            }),
            clip("clip-02", "src-a", SOURCE_ORIGIN + 120, 50, {
              audio: { gain_db: 0, muted: false, fade_in: null, fade_out: null, audio_extends_past_out: null },
            }),
          ],
        },
        {
          track_id: "a2",
          kind: "audio",
          name: "A2 music",
          role: "music",
          enabled: true,
          items: [
            clip("music-01", "src-music", 0, 100, {
              audio: { gain_db: 0, muted: false, fade_in: t(6), fade_out: t(6), audio_extends_past_out: null },
            }),
          ],
        },
      ],
    });
  }

  const audioSources = () => ({
    [source.videoMediaId]: { paths: [source.videoPath] },
    [source.musicMediaId]: { paths: [source.musicPath] },
  });

  it("mixes beds, music, an L-cut and a duck, and lands on the declared loudness with a static gain", async () => {
    const result = await render(audioEdl(false), {
      audio: true,
      sources: audioSources(),
      prefix: "audio-nolimit",
    });
    expect(result.program.audioContributions).toBe(3);
    expect(result.verification.loudness).not.toBeNull();
    expect(result.verification.loudness!.integratedLufs).toBeCloseTo(-14, 0);
    expect(result.verification.loudness!.truePeakDb).toBeLessThanOrEqual(-1 + 0.3);
    expect(result.filterGraph).toContain("adelay=delays=");
    expect(result.filterGraph).toMatch(/volume=volume=0\.354813389:enable=/);
  }, 240_000);

  it("lands on the declared loudness with a limiter, and verifies it on the finished file", async () => {
    const result = await render(audioEdl(true), { audio: true, sources: audioSources(), prefix: "audio-limit" });
    expect(result.verification.loudness!.integratedLufs).toBeCloseTo(-14, 0);
    expect(result.verification.loudness!.truePeakDb).toBeLessThanOrEqual(-1 + 0.3);
  }, 240_000);

  it("records the fade curve and the duck edge as stated interpretations, not as silence", async () => {
    const result = await render(audioEdl(false), { audio: true, sources: audioSources(), prefix: "audio-notes" });
    const fields = result.interpretations.map((entry) => entry.field);
    expect(fields).toContain("ClipAudio.fade_in / fade_out, MusicCue.fade_in / fade_out");
    expect(fields).toContain("audio_plan.ducking[].ranges");
    expect(result.unacted.map((entry) => entry.field)).toContain("audio_plan.ambient.preserve_speech");
  }, 240_000);

  it("refuses a music cue that disagrees with the clip placing the same bed", async () => {
    const edl = audioEdl(false);
    edl.audio_plan!.music![0]!.gain_db = -3;
    await expect(render(edl, { audio: true, sources: audioSources(), prefix: "audio-cue" })).rejects.toThrow(
      /different gain or fades/,
    );
  }, 60_000);

  it("refuses a duck aimed at a role no track carries", async () => {
    const edl = audioEdl(false);
    edl.audio_plan!.ducking![0]!.target = "sfx";
    await expect(render(edl, { audio: true, sources: audioSources(), prefix: "audio-duck" })).rejects.toThrow(
      /audio that does not exist/,
    );
  }, 60_000);

  it("refuses clip audio when there is no mix to render it into", async () => {
    const edl = audioEdl(false);
    edl.audio_plan = null;
    edl.media_refs = [edl.media_refs[0]!];
    edl.tracks = [edl.tracks[0]!];
    await expect(render(edl, { prefix: "audio-nomix" })).rejects.toThrow(/no audio_plan/);
  }, 60_000);
});

describe("resumption", () => {
  it("reuses a verified render instead of decoding again", async () => {
    const work = await workspace("resume");
    const edl = videoOnlyEdl();
    const options = {
      sources: { [source.videoMediaId]: { paths: [source.videoPath] } },
      encode: encode(FFV1_MKV, false),
      workDirectory: work,
      tools: TOOLS,
    };
    const first = await renderVideo(edl, options);
    const second = await renderVideo(edl, options);
    expect(second.path).toBe(first.path);
    expect(second.id).toBe(first.id);
    expect(await digestFile(second.path)).toBe(first.id);
  }, 240_000);
});
