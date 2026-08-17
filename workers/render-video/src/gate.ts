import type {
  AudioPlan,
  Clip,
  EDL,
  EdlValidationChecksItemCheckId,
  Gap,
  Track,
  Transition,
} from "../../../contracts/codegen/generated/typescript/index.js";

import { RenderVideoError } from "./errors.js";

/**
 * THE RULE THIS WORKER IS BUILT ON
 *
 * Hard rule 3: "All creative decisions live in the plan, never in the renderer." Applying
 * that honestly needs a test sharper than "does this feel creative", because almost every
 * gap is dressed as a technical detail. The test used here:
 *
 *   A declaration is renderable when the contract pins it either
 *     (a) STRUCTURALLY  — a time, a range, an id, a geometry, a gain in dB; or
 *     (b) BY OUTCOME    — a measurable result any correct implementation lands on,
 *                         such as an integrated LUFS target or a true-peak ceiling.
 *
 *   A declaration is REFUSED when realising it would make the renderer choose a curve, a
 *   scale, a filter design or a tie-break that the contract does not state — even where a
 *   plausible default exists, and especially where one does. A plausible default is what
 *   a silent defect looks like from the inside.
 *
 * Every refusal below names the issue that has to close before it can be lifted. Nothing
 * is skipped quietly: declarations this worker does not act on are returned as
 * `unacted` and travel with the render result.
 */

export interface ContractGap {
  /** Dotted path to the offending field, with clip/track ids where they disambiguate. */
  field: string;
  /** What was declared. */
  declared: string;
  /** Why executing it would mean the renderer decided something. */
  detail: string;
  /** The contracts issue that has to close before this becomes renderable. */
  issue: string;
}

export interface UnactedDeclaration {
  field: string;
  detail: string;
}

/**
 * A declaration the CONTRACT now pins completely and this worker has not built yet. It is
 * kept apart from `gaps` deliberately: a contract gap is a question for the planner side,
 * an unimplemented declaration is a question for this worker, and reporting the second as
 * the first is how a closed issue looks open for ever.
 */
export interface UnimplementedDeclaration {
  field: string;
  declared: string;
  detail: string;
}

export interface GateReport {
  gaps: ContractGap[];
  unacted: UnactedDeclaration[];
  unimplemented: UnimplementedDeclaration[];
}

export const GAP_ISSUES = Object.freeze({
  colorOps: "contracts#49",
  transition: "contracts#52",
  ambientDsp: "contracts#53",
  ducking: "contracts#54",
  spanAssembly: "contracts#55",
  encodeProfile: "contracts#56",
  colorPipeline: "contracts#58",
  /**
   * contracts#51 pinned the interpolation curve and nothing else about crop geometry. A
   * rotated crop and a crop that changes size still name no resampling convention, and
   * neither has an issue of its own yet.
   */
  cropGeometry: "contracts/edl: crop resampling (unfiled)",
});

/** Video checks whose failure means the picture or the timing would be wrong. */
const REQUIRED_ERROR_CHECKS: readonly EdlValidationChecksItemCheckId[] = [
  "source_range_within_available",
  "media_refs_resolvable",
  "timeline_contiguous",
  "reframe_aspect_matches_target",
  "reframe_keyframes_ordered",
  "determinism_digest_present",
];

export function isClip(item: Clip | Gap | Transition): item is Clip {
  return item.item_type === "clip";
}

export function isGap(item: Clip | Gap | Transition): item is Gap {
  return item.item_type === "gap";
}

export function isTransition(item: Clip | Gap | Transition): item is Transition {
  return item.item_type === "transition";
}

export function videoTracks(edl: EDL): Track[] {
  return edl.tracks.filter((track) => track.kind === "video" && track.enabled !== false);
}

export function audioTracks(edl: EDL): Track[] {
  return edl.tracks.filter((track) => track.kind === "audio" && track.enabled !== false);
}

function fail(detail: string): never {
  throw new RenderVideoError("validation_failed", `The video renderer refused the EDL: ${detail}`);
}

/**
 * Structural checks: things that are simply wrong rather than under-specified. These
 * throw immediately, because the plan contradicts itself and no list of gaps helps.
 */
export function assertStructurallySound(edl: EDL): void {
  if (edl.schema_version !== "v0") fail(`schema_version ${edl.schema_version} is not recognised.`);
  if (!Number.isFinite(edl.rate) || edl.rate <= 0) fail(`timeline rate ${edl.rate} is not usable.`);

  const { width, height } = edl.target.resolution;
  if (!Number.isInteger(width) || !Number.isInteger(height) || width <= 0 || height <= 0) {
    fail(`target resolution ${width}x${height} is not a positive integer size.`);
  }
  const { numerator, denominator } = edl.target.aspect_ratio;
  if (width * denominator !== height * numerator) {
    fail(
      `target resolution ${width}x${height} does not have the declared aspect ratio ` +
        `${numerator}:${denominator}; the plan disagrees with itself.`,
    );
  }

  const refIds = new Set<string>();
  for (const ref of edl.media_refs) {
    if (refIds.has(ref.media_ref_id)) fail(`media_ref_id ${ref.media_ref_id} is declared twice.`);
    refIds.add(ref.media_ref_id);
  }

  const trackIds = new Set<string>();
  for (const track of edl.tracks) {
    if (trackIds.has(track.track_id)) fail(`track_id ${track.track_id} is declared twice.`);
    trackIds.add(track.track_id);
  }

  const video = videoTracks(edl);
  if (video.length === 0) fail("no enabled video track.");
  if (video.length > 1) {
    fail(
      `${video.length} enabled video tracks. Compositing more than one layer needs a blend ` +
        "and an alpha convention that this worker does not implement; it renders a single layer.",
    );
  }

  const clipIds = new Set<string>();
  for (const track of edl.tracks) {
    for (const item of track.items) {
      if (!isClip(item)) continue;
      if (clipIds.has(item.clip_id)) fail(`clip_id ${item.clip_id} is declared twice.`);
      clipIds.add(item.clip_id);
      if (!refIds.has(item.media_ref_id)) {
        fail(`clip ${item.clip_id} names media_ref ${item.media_ref_id}, which is not declared.`);
      }
      if (item.reframe_track_id) {
        const known = (edl.reframe_tracks ?? []).some(
          (candidate) => candidate.reframe_track_id === item.reframe_track_id,
        );
        if (!known) {
          fail(`clip ${item.clip_id} names reframe track ${item.reframe_track_id}, which is not declared.`);
        }
      }
    }
  }

  const validation = edl.validation;
  if (!validation) {
    fail(
      "no validation block. EdlValidation exists so the renderer can refuse a plan that has " +
        "not been checked; a renderer that is dumb must not also be trusting.",
    );
  }
  if (validation.status !== "pass") fail(`validation status is ${validation.status}.`);
  const failedCheck = validation.checks.find((check) => !check.passed && check.severity === "error");
  if (failedCheck) fail(`validation check ${failedCheck.check_id} failed.`);
  for (const required of REQUIRED_ERROR_CHECKS) {
    if (!validation.checks.some((check) => check.check_id === required && check.passed)) {
      fail(`validation has no passing finding for ${required}.`);
    }
  }
  const hasMusic = (edl.audio_plan?.music ?? []).length > 0;
  if (hasMusic && !validation.checks.some((c) => c.check_id === "music_license_covers_destination" && c.passed)) {
    fail(
      "the EDL carries music but validation has no passing music_license_covers_destination " +
        "finding. The renderer does not re-derive the destination-to-clearance mapping; it " +
        "requires the validator's verdict.",
    );
  }
  if (hasMusic && !validation.checks.some((c) => c.check_id === "music_cues_placed_once" && c.passed)) {
    fail(
      "the EDL carries music but validation has no passing music_cues_placed_once finding. " +
        "The bed is placed on a track and licensed by a cue (contracts#59); the validator has " +
        "to have checked that the two agree before the mixer sums anything.",
    );
  }
  const retimed = edl.tracks.some((track) =>
    track.items.some((item) => isClip(item) && item.time_effect != null),
  );
  if (retimed && !validation.checks.some((c) => c.check_id === "time_effect_extent_derived" && c.passed)) {
    fail(
      "the EDL carries a time effect but validation has no passing time_effect_extent_derived " +
        "finding. A retimed clip's timeline extent is derived from source_range (contracts#50), " +
        "and an unchecked plan is where the two readings of that field diverge.",
    );
  }
}

function gapsFromClip(clip: Clip, audioPlan: AudioPlan | null | undefined, out: ContractGap[]): void {
  for (const op of clip.color_ops ?? []) {
    out.push({
      field: `clips.${clip.clip_id}.color_ops[${op.op}]`,
      declared: `amount ${op.amount}`,
      detail:
        "ColorOp.amount is normalised to [-1,1] with no transfer function, so the renderer " +
        "would have to invent the physical size of the adjustment.",
      issue: GAP_ISSUES.colorOps,
    });
  }

  const ambient = audioPlan?.ambient;
  const clipGain = clip.audio?.gain_db ?? 0;
  if (ambient && ambient.enabled !== false && clipGain !== 0) {
    const perClip = (ambient.per_clip_gain_db ?? []).find((entry) => entry.clip_id === clip.clip_id);
    const ambientGain = perClip ? perClip.gain_db : (ambient.default_gain_db ?? -12);
    out.push({
      field: `clips.${clip.clip_id}.audio.gain_db`,
      declared: `${clipGain} dB against an ambient gain of ${ambientGain} dB`,
      detail:
        "Two gains apply to one bed and the contract states no composition rule. Summing, " +
        "overriding and ignoring are all defensible and are dB apart.",
      issue: GAP_ISSUES.ambientDsp,
    });
  }

  if (clip.audio?.muted === true && (clip.audio.gain_db ?? 0) !== 0) {
    out.push({
      field: `clips.${clip.clip_id}.audio`,
      declared: `muted with gain_db ${clip.audio.gain_db}`,
      detail: "A muted clip that also carries a gain is a contradiction the renderer will not resolve.",
      issue: GAP_ISSUES.ambientDsp,
    });
  }
}

function gapsFromTransitions(edl: EDL, out: ContractGap[]): void {
  const ambientEnabled = (edl.audio_plan?.ambient?.enabled ?? true) && edl.audio_plan != null;
  for (const track of edl.tracks) {
    track.items.forEach((item, index) => {
      if (!isTransition(item)) return;
      const id = item.transition_id ?? `${track.track_id}[${index}]`;

      if ((item.easing ?? "linear") !== "linear") {
        out.push({
          field: `transitions.${id}.easing`,
          declared: item.easing ?? "linear",
          detail: "The easing names carry no curve, and a cubic and a sine ease are different shots.",
          issue: GAP_ISSUES.transition,
        });
      }

      const supported = new Set(["dissolve", "dip_to_black", "dip_to_white"]);
      if (!supported.has(item.transition_type)) {
        out.push({
          field: `transitions.${id}.transition_type`,
          declared: item.transition_type,
          detail:
            "Only the three types whose meaning is fixed by their names are renderable. The " +
            "rest carry a free-form `parameters` object whose keys are not enumerated.",
          issue: GAP_ISSUES.transition,
        });
      }

      const before = track.items[index - 1];
      const after = track.items[index + 1];
      if (!before || !after || !isClip(before) || !isClip(after)) {
        out.push({
          field: `transitions.${id}`,
          declared: "not between two clips",
          detail: "A transition against a gap or a track edge has no defined handle source.",
          issue: GAP_ISSUES.transition,
        });
        return;
      }

      if (track.kind === "video" && ambientEnabled) {
        const carriesAudio = [before, after].some(
          (clip) => clip.audio != null && clip.audio.muted !== true,
        );
        if (carriesAudio) {
          out.push({
            field: `transitions.${id}`,
            declared: `${item.transition_type} between clips that carry ambient audio`,
            detail:
              "The contract describes the transition as a picture operation only and never says " +
              "whether the beds cross-fade with it or hard-cut at the cut point.",
            issue: GAP_ISSUES.transition,
          });
        }
      }
    });
  }
}

function gapsFromReframe(edl: EDL, out: ContractGap[]): void {
  for (const track of edl.reframe_tracks ?? []) {
    const id = track.reframe_track_id;
    const first = track.keyframes[0];
    if (!first) continue;

    track.keyframes.forEach((keyframe, index) => {
      const interpolation = keyframe.interpolation ?? "smooth";
      const isLast = index === track.keyframes.length - 1;
      // `smooth` is a clamped uniform Catmull-Rom spline, stated as a formula in
      // ReframeKeyframe.interpolation's $comment and implemented in reframe.ts
      // (contracts#51). `bezier` still needs its control points to name a curve.
      if (interpolation === "bezier" && !isLast && (keyframe.bezier_control ?? null) === null) {
        out.push({
          field: `reframe_tracks.${id}.keyframes[${index}].bezier_control`,
          declared: "null",
          detail: "bezier interpolation without control points has no curve.",
          issue: GAP_ISSUES.cropGeometry,
        });
      }
      if ((keyframe.crop.rotation_deg ?? 0) !== 0) {
        out.push({
          field: `reframe_tracks.${id}.keyframes[${index}].crop.rotation_deg`,
          declared: String(keyframe.crop.rotation_deg),
          detail: "A rotated crop has no pinned resampling convention.",
          issue: GAP_ISSUES.cropGeometry,
        });
      }
      if (keyframe.crop.w !== first.crop.w || keyframe.crop.h !== first.crop.h) {
        out.push({
          field: `reframe_tracks.${id}.keyframes[${index}].crop`,
          declared: `${keyframe.crop.w}x${keyframe.crop.h} against ${first.crop.w}x${first.crop.h}`,
          detail:
            "A crop window that changes size is a zoom, and the contract pins neither the " +
            "resampling nor the per-frame rounding of a moving crop size.",
          issue: GAP_ISSUES.cropGeometry,
        });
      }
    });
  }
}

function gapsFromAudioPlan(edl: EDL, out: ContractGap[]): void {
  const plan = edl.audio_plan;
  if (!plan) return;

  const ambient = plan.ambient;
  if (ambient && ambient.enabled !== false) {
    if ((ambient.noise_suppression ?? "light") !== "none") {
      out.push({
        field: "audio_plan.ambient.noise_suppression",
        declared: ambient.noise_suppression ?? "light",
        detail:
          "A strength label with no algorithm and no measurable target behind it. " +
          "\"Moderate\" is a slider caption, not a specification.",
        issue: GAP_ISSUES.ambientDsp,
      });
    }
    if ((ambient.high_pass_hz ?? null) !== null) {
      out.push({
        field: "audio_plan.ambient.high_pass_hz",
        declared: `${ambient.high_pass_hz} Hz`,
        detail:
          "The corner frequency is pinned; the filter order and response are not, and they " +
          "decide how much of the rumble actually goes.",
        issue: GAP_ISSUES.ambientDsp,
      });
    }
  }

  for (const rule of plan.ducking ?? []) {
    if (rule.trigger !== "explicit_ranges") {
      out.push({
        field: `audio_plan.ducking.${rule.rule_id}.trigger`,
        declared: rule.trigger,
        detail:
          "A detection trigger asks the renderer to analyse the mix at render time, which is " +
          "both a decision and not reproducible. The planner must resolve it to ranges.",
        issue: GAP_ISSUES.ducking,
      });
    }
    if ((rule.attack_ms ?? 80) !== 0 || (rule.release_ms ?? 300) !== 0) {
      out.push({
        field: `audio_plan.ducking.${rule.rule_id}`,
        declared: `attack ${rule.attack_ms ?? 80} ms, release ${rule.release_ms ?? 300} ms`,
        detail:
          "No envelope shape is stated, so the gain in the middle of the ramp is the " +
          "renderer's invention — and that is exactly where the voice is.",
        issue: GAP_ISSUES.ducking,
      });
    }
    if (rule.trigger === "explicit_ranges" && (rule.ranges ?? []).length === 0) {
      out.push({
        field: `audio_plan.ducking.${rule.rule_id}.ranges`,
        declared: "empty",
        detail: "explicit_ranges with no ranges states an intent with no extent.",
        issue: GAP_ISSUES.ducking,
      });
    }
  }

  // A MusicCue no longer places anything (contracts#59): every position, gain and fade it
  // used to duplicate now exists exactly once, on an audio-track clip. A bed that repeats
  // is several clips the cue claims, so there is no loop flag left to refuse either
  // (contracts#57). program.ts checks that every cue resolves to clips and every
  // music-role clip is claimed.
}

function gapsFromColorPipeline(edl: EDL, out: ContractGap[]): void {
  const pipeline = edl.color_pipeline;
  if (!pipeline) return;
  const working = pipeline.working_space ?? "rec709";
  const output = pipeline.output_transform ?? "rec709";
  if (working !== output) {
    out.push({
      field: "color_pipeline",
      declared: `working ${working} -> output ${output}`,
      detail:
        "A working-to-output conversion needs a named transform, and the transform value " +
        "space is not enumerated in the contract.",
      issue: GAP_ISSUES.colorPipeline,
    });
  }
}

function unactedDeclarations(edl: EDL): UnactedDeclaration[] {
  const unacted: UnactedDeclaration[] = [];
  const note = (field: string, detail: string): void => {
    unacted.push({ field, detail });
  };

  if (edl.story_arc) {
    note("story_arc", "Narrative provenance. It constrains the planner, not the filtergraph.");
  }
  if (edl.variant) {
    note("variant", "Variant bookkeeping. Two variants of one cut are two EDLs, not two renders.");
  }
  if (edl.beat_grid) {
    note(
      "beat_grid",
      "Verified, not executed: every beat_lock is re-derived from integer frame positions and " +
        "checked against the grid before the render starts. No cut is moved.",
    );
  }
  if (edl.otio) {
    note("otio", "OTIO export bookkeeping, produced by the exporter rather than consumed here.");
  }
  if (edl.target.target_duration) {
    note("target.target_duration", "What the planner was asked for; the realised timeline governs.");
  }

  for (const track of edl.reframe_tracks ?? []) {
    if (track.subject_lock) {
      note(
        `reframe_tracks.${track.reframe_track_id}.subject_lock`,
        "Tracker provenance. The keyframes it produced are what the renderer applies.",
      );
    }
    if (track.smoothing) {
      note(
        `reframe_tracks.${track.reframe_track_id}.smoothing`,
        "Describes how the planner smoothed the keyframes it emitted. Re-smoothing here would " +
          "filter the signal twice.",
      );
    }
    note(
      `reframe_tracks.${track.reframe_track_id}.fallback`,
      "Never reached: this renderer does not track subjects, so tracking cannot fail here. It " +
        "will not silently centre-crop — a crop it cannot evaluate is a hard failure.",
    );
  }

  const ambient = edl.audio_plan?.ambient;
  if (ambient && ambient.preserve_speech !== false && ambient.enabled !== false) {
    note(
      "audio_plan.ambient.preserve_speech",
      `Not executable by a renderer: nothing in the EDL says which clips contain speech. In a ` +
        `well-formed plan the intent already arrives as per-clip gains and explicit ducking ` +
        `ranges. See ${GAP_ISSUES.ambientDsp}.`,
    );
  }

  for (const track of edl.tracks) {
    for (const item of track.items) {
      if (isClip(item) && (item.markers ?? []).length > 0) {
        note(`clips.${item.clip_id}.markers`, "Editor-facing annotation; exported to OTIO, not rendered.");
      }
    }
  }

  return unacted;
}

/**
 * Declarations the contract pins and this worker cannot yet execute.
 *
 * `time_effect` is the whole list. contracts#50 settled the authority question — a
 * retimed clip reads source_range.duration frames of media and holds
 * source_range.duration / time_scalar timeline frames, and output frame k draws source
 * frame start + floor(k * time_scalar) — so program.ts now lays a retimed clip out on the
 * correct extent and validates it. What is missing is the picture: emitting the frames at
 * that sampling needs a filter chain that neither duplicates nor drops a frame of its own
 * accord, which is the one thing the transition and concat design here is built to
 * prevent. Refusing until that is built and measured is the safe direction; the number
 * this would get wrong is which frame, and a wrong frame looks exactly like a right one.
 */
function unimplementedDeclarations(edl: EDL): UnimplementedDeclaration[] {
  const unimplemented: UnimplementedDeclaration[] = [];
  for (const track of edl.tracks) {
    for (const item of track.items) {
      if (!isClip(item) || !item.time_effect) continue;
      const effect = item.time_effect;
      unimplemented.push({
        field: `clips.${item.clip_id}.time_effect`,
        declared:
          effect.kind === "linear_speed"
            ? `linear_speed ${effect.time_scalar}x`
            : `freeze_frame holding ${effect.hold_duration?.value ?? "?"} frames`,
        detail:
          "The contract pins the extent and the frame sampling (contracts#50); this worker " +
          "lays the clip out and checks it, and does not yet emit the retimed picture.",
      });
    }
  }
  return unimplemented;
}

export function collectGaps(edl: EDL): GateReport {
  const gaps: ContractGap[] = [];

  if (edl.global_start_time && edl.global_start_time.value !== 0) {
    gaps.push({
      field: "global_start_time",
      declared: `${edl.global_start_time.value} @ ${edl.global_start_time.rate}`,
      detail:
        "A non-zero timeline origin means a broadcast start timecode, and neither the timecode " +
        "track nor its drop-frame convention is specified for the delivered file.",
      issue: GAP_ISSUES.encodeProfile,
    });
  }

  for (const track of edl.tracks) {
    for (const item of track.items) {
      if (isClip(item)) gapsFromClip(item, edl.audio_plan, gaps);
      if (isGap(item)) {
        const fill = item.fill ?? "black";
        const valid = track.kind === "video" ? ["black", "white"] : ["silence"];
        if (!valid.includes(fill)) {
          gaps.push({
            field: `gaps.${item.gap_id ?? track.track_id}.fill`,
            declared: fill,
            detail:
              `A ${fill} fill on a ${track.kind} track has no defined realisation in an opaque ` +
              "delivery file.",
            issue: GAP_ISSUES.encodeProfile,
          });
        }
      }
    }
  }

  gapsFromTransitions(edl, gaps);
  gapsFromReframe(edl, gaps);
  gapsFromAudioPlan(edl, gaps);
  gapsFromColorPipeline(edl, gaps);

  return {
    gaps,
    unacted: unactedDeclarations(edl),
    unimplemented: unimplementedDeclarations(edl),
  };
}

export function formatGaps(gaps: readonly ContractGap[]): string {
  return gaps
    .map((gap) => `  - ${gap.field}: declared ${gap.declared}. ${gap.detail} (${gap.issue})`)
    .join("\n");
}

/**
 * Refuses with the COMPLETE list rather than the first offender: the planner side needs to
 * know everything that has to change, and a renderer that reports one gap per run turns a
 * contract review into a guessing game.
 */
export function assertRenderable(edl: EDL): GateReport {
  assertStructurallySound(edl);
  const report = collectGaps(edl);
  if (report.gaps.length > 0) {
    throw new RenderVideoError(
      "validation_failed",
      `The video renderer refused the EDL because ${report.gaps.length} declaration(s) are not ` +
        `pinned by the contract. Rendering them would mean the renderer decided them.\n` +
        `${formatGaps(report.gaps)}`,
    );
  }
  if (report.unimplemented.length > 0) {
    throw new RenderVideoError(
      "validation_failed",
      `The video renderer refused the EDL because ${report.unimplemented.length} declaration(s) ` +
        "are pinned by the contract and not implemented by this worker. This is a worker gap, " +
        "not a contract gap — nothing here is waiting on the planner side.\n" +
        report.unimplemented
          .map((entry) => `  - ${entry.field}: declared ${entry.declared}. ${entry.detail}`)
          .join("\n"),
    );
  }
  return report;
}
