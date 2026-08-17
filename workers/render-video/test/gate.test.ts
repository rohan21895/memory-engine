import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import type { Clip, EDL, Transition } from "../../../contracts/codegen/generated/typescript/index.js";

import { assertRenderable, assertStructurallySound, collectGaps } from "../src/gate.js";
import { buildProgram } from "../src/program.js";
import {
  clip,
  makeEdl,
  NTSC_30,
  range,
  reframeTrack,
  SOURCE_FRAMES,
  SOURCE_ORIGIN,
  t,
  videoRef,
} from "./helpers.js";

const GOLDEN = fileURLToPath(
  new URL("../../../contracts/fixtures/edl/valid/reel-beat-locked-vertical-reframe.json", import.meta.url),
);

function simpleEdl(): EDL {
  return makeEdl({
    mediaRefs: [videoRef("a".repeat(64))],
    reframeTracks: [reframeTrack("rf-1", SOURCE_ORIGIN, SOURCE_ORIGIN + 60)],
    tracks: [
      {
        track_id: "v1",
        kind: "video",
        name: "V1",
        role: "primary",
        enabled: true,
        items: [
          clip("clip-01", "src-a", SOURCE_ORIGIN, 60, { reframe_track_id: "rf-1" }),
          clip("clip-02", "src-a", SOURCE_ORIGIN + 100, 60),
        ],
      },
    ],
  });
}

describe("the gate refuses what the contract does not pin", () => {
  it("passes a plan built only from pinned declarations", () => {
    const report = assertRenderable(simpleEdl());
    expect(report.gaps).toEqual([]);
  });

  it("refuses every colour op, because ColorOp.amount has no transfer function", () => {
    const edl = simpleEdl();
    (edl.tracks[0]!.items[0] as Clip).color_ops = [
      { op: "exposure", amount: 0.12, lut_id: null, reference_clip_id: null },
    ];
    const { gaps } = collectGaps(edl);
    expect(gaps).toHaveLength(1);
    expect(gaps[0]).toMatchObject({ issue: "contracts#49" });
    expect(() => assertRenderable(edl)).toThrow(/transfer function/);
  });

  it("accepts `smooth`, because contracts#51 pinned it to a curve", () => {
    const edl = simpleEdl();
    for (const keyframe of edl.reframe_tracks![0]!.keyframes) keyframe.interpolation = "smooth";
    const report = collectGaps(edl);
    expect(report.gaps).toEqual([]);
    expect(() => assertRenderable(edl)).not.toThrow();
  });

  it("reports a speed change as unimplemented by this worker, not as a contract gap", () => {
    const edl = simpleEdl();
    // 60 timeline frames at 0.5x reads 30 frames of media, per contracts#50.
    const clip = edl.tracks[0]!.items[0] as Clip;
    clip.source_range = range(SOURCE_ORIGIN, 30);
    clip.time_effect = {
      kind: "linear_speed",
      time_scalar: 0.5,
      freeze_at: null,
      hold_duration: null,
      audio_handling: "mute",
    };
    const report = collectGaps(edl);
    expect(report.gaps).toEqual([]);
    expect(report.unimplemented).toHaveLength(1);
    expect(report.unimplemented[0]).toMatchObject({ declared: "linear_speed 0.5x" });
    expect(() => assertRenderable(edl)).toThrow(/worker gap, not a contract gap/);
  });

  it("reports every gap at once rather than the first", () => {
    const edl = simpleEdl();
    (edl.tracks[0]!.items[0] as Clip).color_ops = [
      { op: "exposure", amount: 0.12, lut_id: null, reference_clip_id: null },
    ];
    edl.reframe_tracks![0]!.keyframes[0]!.interpolation = "bezier";
    edl.reframe_tracks![0]!.keyframes[1]!.crop = {
      ...edl.reframe_tracks![0]!.keyframes[1]!.crop,
      rotation_deg: 3,
    };
    const { gaps } = collectGaps(edl);
    expect(gaps.map((gap) => gap.issue).sort()).toEqual([
      "contracts#49",
      "contracts/edl: crop resampling (unfiled)",
      "contracts/edl: crop resampling (unfiled)",
    ]);
  });

  it("records planner provenance as not acted upon instead of ignoring it quietly", () => {
    const report = collectGaps(simpleEdl());
    const fields = report.unacted.map((entry) => entry.field);
    expect(fields).toContain("reframe_tracks.rf-1.subject_lock");
    expect(fields).toContain("reframe_tracks.rf-1.smoothing");
    expect(fields).toContain("reframe_tracks.rf-1.fallback");
  });
});

describe("the gate refuses a plan that contradicts itself", () => {
  it("refuses a resolution that is not the declared aspect ratio", () => {
    const edl = simpleEdl();
    edl.target.resolution = { width: 1080, height: 1080 };
    expect(() => assertStructurallySound(edl)).toThrow(/disagrees with itself/);
  });

  it("refuses an EDL that has not been validated", () => {
    const edl = simpleEdl();
    edl.validation = null;
    expect(() => assertStructurallySound(edl)).toThrow(/not also be trusting/);
  });

  it("refuses when a required validation check is missing", () => {
    const edl = simpleEdl();
    edl.validation!.checks = edl.validation!.checks.filter((check) => check.check_id !== "timeline_contiguous");
    expect(() => assertStructurallySound(edl)).toThrow(/timeline_contiguous/);
  });

  it("refuses a retimed plan whose validator never checked the derived extent", () => {
    const edl = simpleEdl();
    const clip = edl.tracks[0]!.items[0] as Clip;
    clip.source_range = range(SOURCE_ORIGIN, 30);
    clip.time_effect = {
      kind: "linear_speed",
      time_scalar: 0.5,
      freeze_at: null,
      hold_duration: null,
      audio_handling: "mute",
    };
    edl.validation!.checks = edl.validation!.checks.filter(
      (check) => check.check_id !== "time_effect_extent_derived",
    );
    expect(() => assertStructurallySound(edl)).toThrow(/time_effect_extent_derived/);
  });

  it("refuses a second video layer rather than compositing on a guess", () => {
    const edl = simpleEdl();
    edl.tracks.push({ ...edl.tracks[0]!, track_id: "v2" });
    expect(() => assertStructurallySound(edl)).toThrow(/single layer/);
  });

  it("refuses a clip whose declared timeline_range is not where the items put it", () => {
    const edl = simpleEdl();
    (edl.tracks[0]!.items[1] as Clip).timeline_range = range(59, 60);
    expect(() => buildProgram(edl)).toThrow(/beat-locked cut drifts/);
  });

  it("refuses a zero-length transition, because a hard cut is the absence of one", () => {
    const edl = simpleEdl();
    const transition: Transition = {
      item_type: "transition",
      transition_id: "x1",
      transition_type: "dissolve",
      in_offset: t(0),
      out_offset: t(0),
      easing: "linear",
      parameters: {},
    };
    edl.tracks[0]!.items.splice(1, 0, transition);
    expect(() => buildProgram(edl)).toThrow(/ABSENCE of a transition/);
  });

  it("refuses a transition whose handles run off the end of the source", () => {
    const edl = makeEdl({
      mediaRefs: [videoRef("a".repeat(64))],
      tracks: [
        {
          track_id: "v1",
          kind: "video",
          name: "V1",
          role: "primary",
          enabled: true,
          items: [
            clip("clip-01", "src-a", SOURCE_ORIGIN, 60),
            {
              item_type: "transition",
              transition_id: "x1",
              transition_type: "dissolve",
              in_offset: t(6),
              out_offset: t(6),
              easing: "linear",
              parameters: {},
            },
            // Starts at the very first frame of the source, so there is no handle before it.
            clip("clip-02", "src-a", SOURCE_ORIGIN, 60),
          ],
        },
      ],
    });
    expect(() => buildProgram(edl)).toThrow(/handle/);
  });
});

describe("beat locks are re-derived, not trusted", () => {
  function lockedEdl(clipStartFrame: number, recordedErrorMs: number): EDL {
    const edl = makeEdl({
      mediaRefs: [videoRef("a".repeat(64))],
      tracks: [
        {
          track_id: "v1",
          kind: "video",
          name: "V1",
          role: "primary",
          enabled: true,
          items: [
            clip("clip-01", "src-a", SOURCE_ORIGIN, clipStartFrame),
            clip("clip-02", "src-a", SOURCE_ORIGIN + 120, 60, {
              beat_lock: {
                beat_index: 1,
                is_downbeat: true,
                alignment_error_ms: recordedErrorMs,
                snap_point_kind: "motion_onset",
              },
            }),
          ],
        },
      ],
      beatGrid: {
        source_cue_id: "cue-01",
        bpm: 120,
        bpm_confidence: 0.9,
        beats: [
          { index: 0, time: t(0), is_downbeat: true, bar: 0, beat_in_bar: 1, strength: 1, section: "intro" },
          { index: 1, time: t(60.2), is_downbeat: true, bar: 1, beat_in_bar: 1, strength: 1, section: "verse" },
        ],
        analyzer: null,
        tolerance_ms: 50,
      },
    });
    return edl;
  }

  it("accepts a lock whose recorded error matches the frame positions", () => {
    const expected = ((60 - 60.2) * 1000) / NTSC_30;
    expect(() => buildProgram(lockedEdl(60, Number(expected.toFixed(4))))).not.toThrow();
  });

  it("catches a one-frame drift between the plan's audit trail and the actual cut", () => {
    const expected = ((60 - 60.2) * 1000) / NTSC_30;
    // The clip now starts a frame later, so the recorded error is a frame's worth of lie.
    expect(() => buildProgram(lockedEdl(61, Number(expected.toFixed(4))))).toThrow(
      /does not describe the cut it is asking for/,
    );
  });

  it("refuses a lock that exceeds the grid's own tolerance", () => {
    const edl = lockedEdl(60, 0);
    edl.beat_grid!.beats[1]!.time = t(55);
    edl.tracks[0]!.items[1] = clip("clip-02", "src-a", SOURCE_ORIGIN + 120, 60, {
      beat_lock: {
        beat_index: 1,
        is_downbeat: true,
        alignment_error_ms: Number((((60 - 55) * 1000) / NTSC_30).toFixed(4)),
        snap_point_kind: "motion_onset",
      },
    });
    expect(() => buildProgram(edl)).toThrow(/outside the grid's 50 ms tolerance/);
  });
});

describe("the golden fixture", () => {
  it("is refused, and the refusal names every unpinned declaration in it", async () => {
    const edl = JSON.parse(await readFile(GOLDEN, "utf8")) as EDL;
    assertStructurallySound(edl);
    const { gaps, unacted } = collectGaps(edl);

    const issues = new Set(gaps.map((gap) => gap.issue));
    expect(issues).toEqual(
      new Set([
        "contracts#49", // exposure and match_to_reference on clip-07
        "contracts#52", // ease_in_out, and ambient beds across the dissolve
        "contracts#53", // noise_suppression moderate, high_pass 120 Hz, two gains per bed
        "contracts#54", // ducking attack 60 ms / release 320 ms
      ]),
    );
    // #51 closed: every reframe keyframe in the fixture is `smooth`, and `smooth` is now a
    // stated curve. #50 closed: the retime on clip-05 is pinned, and what is left is this
    // worker's, not the contract's.
    expect(collectGaps(edl).unimplemented.map((entry) => entry.field)).toEqual([
      "clips.clip-05.time_effect",
    ]);

    expect(unacted.map((entry) => entry.field)).toContain("audio_plan.ambient.preserve_speech");
    expect(unacted.map((entry) => entry.field)).toContain("beat_grid");
    expect(unacted.map((entry) => entry.field)).toContain("story_arc");
  });

  it("has a timeline that lays out to the 899 frames its own validation report claims", async () => {
    const edl = JSON.parse(await readFile(GOLDEN, "utf8")) as EDL;
    // The program builder cannot run on it (the gaps above), but the layout arithmetic can,
    // and it is what decides whether the transition reading in program.ts is the right one.
    const rate = edl.rate;
    const video = edl.tracks.find((track) => track.kind === "video")!;
    const clips = video.items.filter((item): item is Clip => item.item_type === "clip");

    // The timeline extent is DERIVED from source_range and any time effect (contracts#50).
    // Summing source durations instead gives 843 — the 56 frames of media that clip-05
    // stretches over 112 — which is the arithmetic this rule exists to stop anyone doing.
    const extents = clips.map((item) =>
      item.time_effect?.kind === "linear_speed"
        ? item.source_range.duration.value / item.time_effect.time_scalar!
        : item.source_range.duration.value,
    );
    expect(extents.reduce((sum, value) => sum + value, 0)).toBe(899);
    expect(clips.reduce((sum, item) => sum + item.source_range.duration.value, 0)).toBe(843);
    expect(rate).toBe(60_000 / 1001);
    const last = clips[clips.length - 1]!;
    expect(last.timeline_range!.start_time.value + last.timeline_range!.duration.value).toBe(899);

    // Every declared timeline_range agrees with the derived extent, which is what makes
    // the six beat-locked downbeats land where beat_lock says they do.
    clips.forEach((item, index) => {
      expect(item.timeline_range!.duration.value).toBe(extents[index]);
    });
  });
});

describe("source frame count", () => {
  it("keeps the whole source addressable", () => {
    const edl = simpleEdl();
    (edl.tracks[0]!.items[1] as Clip).source_range = range(SOURCE_ORIGIN + SOURCE_FRAMES - 10, 10);
    expect(() => buildProgram(edl)).not.toThrow();
  });

  it("refuses a clip that reads past the declared end of its source", () => {
    const edl = simpleEdl();
    (edl.tracks[0]!.items[1] as Clip).source_range = range(SOURCE_ORIGIN + SOURCE_FRAMES - 10, 11);
    expect(() => buildProgram(edl)).toThrow(/only offers/);
  });
});
