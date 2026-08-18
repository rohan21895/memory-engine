import type {
  Clip,
  EDL,
  EdlValidationChecksItemCheckId,
  Gap,
  Track,
  Transition,
} from "../../../contracts/codegen/generated/typescript/index.js";

import { HDR_ENCODINGS, opMatrix } from "./color.js";
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
  /**
   * A non-zero timeline origin is a broadcast start timecode. contracts#56 settled the
   * encode profile and deliberately did not settle this: a delivered file's timecode
   * track and its drop-frame convention are a separate decision, and nobody has needed
   * one yet.
   */
  startTimecode: "contracts/edl: delivery timecode track (unfiled)",
  /**
   * contracts#52 settled what a transition IS. What it does not settle is a transition
   * that sits against a gap or a track edge, which has no handle source on one side.
   * No planner emits one and no issue exists for it.
   */
  transitionGeometry: "contracts/edl: transition against a gap (unfiled)",
  /** A `transparent` fill has no realisation in an opaque delivery file. */
  gapFill: "contracts/edl: transparent gap fill (unfiled)",
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
  const assemblies = edl.media_refs.filter((ref) => ref.is_span_assembly === true);
  for (const ref of assemblies) {
    const members = ref.member_media_ids ?? [];
    if (members.length < 2) {
      fail(
        `media_ref ${ref.media_ref_id} is a span assembly naming ${members.length} member(s). ` +
          "An assembly is what the renderer expands, and it cannot expand a list it does not have.",
      );
    }
    if (ref.continuity !== "verified_gapless") {
      fail(
        `media_ref ${ref.media_ref_id} declares continuity ${ref.continuity}. Only ` +
          "verified_gapless may be concatenated (contracts#55): a gap at a chapter split puts " +
          "every source timecode after it wrong by the length of the gap, and nothing in the " +
          "plan carries that length to compensate with.",
      );
    }
  }
  if (assemblies.length > 0 && !validation.checks.some((c) => c.check_id === "span_continuity_verified" && c.passed)) {
    fail(
      "the EDL names a span assembly but validation has no passing span_continuity_verified " +
        "finding. The member list IS the assembly's identity, and an unchecked order is a " +
        "different recording.",
    );
  }
  /**
   * contracts#58. The colour path is CONTRACT data now, so what is left here is checking
   * that the plan agrees with itself. Both directions matter and only one of them is
   * loud: an HDR source with no tone map renders washed out and exits zero, and a tone map
   * over all-SDR sources is a grade nobody asked for applied to every frame.
   */
  const pipeline = edl.color_pipeline;
  const hdrRefs = edl.media_refs.filter(
    (ref) => ref.color_encoding != null && HDR_ENCODINGS.has(ref.color_encoding),
  );
  if (hdrRefs.length > 0 && !pipeline.tone_map) {
    fail(
      `media_ref(s) ${hdrRefs.map((ref) => ref.media_ref_id).join(", ")} carry an HDR encoding ` +
        `and color_pipeline names no tone_map. Fitting HDR light into ${pipeline.output_encoding} ` +
        "without an operator clips the highlights and washes the picture out — and succeeds, " +
        "which is why the contract requires the operator rather than a boolean.",
    );
  }
  if (hdrRefs.length === 0 && pipeline.tone_map) {
    fail(
      `color_pipeline carries a ${pipeline.tone_map.operator} tone map and no source is HDR. ` +
        "Every operator maps a source's peak onto reference white, so applying one to footage " +
        "that is already inside the output volume compresses it for nothing.",
    );
  }
  for (const ref of hdrRefs) {
    if (ref.source_peak_nits == null || !(ref.source_peak_nits > 0)) {
      fail(
        `media_ref ${ref.media_ref_id} is ${ref.color_encoding} and states no source_peak_nits. ` +
          "The tone map fits THAT source's peak onto reference white; without it the renderer " +
          "would be choosing how bright the brightest thing in the shot is.",
      );
    }
  }
  if (!validation.checks.some((c) => c.check_id === "color_pipeline_resolves" && c.passed)) {
    fail(
      "validation has no passing color_pipeline_resolves finding. Every EDL states its colour " +
        "path, including the ordinary all-SDR one, and the renderer requires the validator's " +
        "verdict rather than re-deriving it (contracts#58).",
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

function gapsFromClip(clip: Clip, out: ContractGap[]): void {
  // contracts#49 gave every op in the enum a transfer function from `amount` to a physical
  // quantity — exposure buys +/- 2 stops, saturation a chroma scale of 1 + a — and stated
  // that the list fuses into one matrix applied once. Nothing left to refuse: color.ts
  // executes the contract's own formulas. An op the enum grows and this worker has not
  // implemented is a WORKER gap, and is reported as one below.
  //
  // The gain conflict that used to be refused here is gone too: contracts#53 removed
  // AmbientPlan's own gains, so a clip's bed has exactly one level, on the clip, and
  // `muted` outranks it — both now stated in ClipAudio.
  void clip;
  void out;
}

function gapsFromTransitions(edl: EDL, out: ContractGap[]): void {
  for (const track of edl.tracks) {
    track.items.forEach((item, index) => {
      if (!isTransition(item)) return;
      const id = item.transition_id ?? `${track.track_id}[${index}]`;

      // contracts#52 closed all three holes here. The easing names are polynomials in the
      // linear progress, written out in the schema and implemented in filtergraph.ts; the
      // transition types that carried an un-enumerated `parameters` bag are out of the enum
      // entirely; and the beds under a transition are stated to butt-cut, because a
      // cross-fade is already expressible exactly once as ClipAudio fades. What remains is
      // geometry, which is structural rather than a contract gap.
      const before = track.items[index - 1];
      const after = track.items[index + 1];
      if (!before || !after || !isClip(before) || !isClip(after)) {
        out.push({
          field: `transitions.${id}`,
          declared: "not between two clips",
          detail: "A transition against a gap or a track edge has no defined handle source.",
          issue: GAP_ISSUES.transitionGeometry,
        });
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

  // contracts#53 replaced the ambient DSP labels with one linear filter whose response,
  // order and section Q values the schema states, so there is nothing left to refuse here:
  // the orders the contract allows (2 and 4) are the orders filtergraph.ts builds.
  // contracts#54 pinned the ducking envelope — linear in dB, attack ending at the range
  // start, release beginning at the range end — and removed the detection triggers that
  // would have had the renderer analyse the mix. Both are executed rather than refused.
  //
  // A MusicCue no longer places anything (contracts#59): every position, gain and fade it
  // used to duplicate now exists exactly once, on an audio-track clip. A bed that repeats
  // is several clips the cue claims, so there is no loop flag left to refuse either
  // (contracts#57). program.ts checks that every cue resolves to clips and every
  // music-role clip is claimed.
  void plan;
  void out;
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
  /**
   * A colour op the contract has given a transfer function and this worker has no matrix
   * for. Today the two lists are the same two ops, so this reports nothing — it exists so
   * that GROWING the enum fails here, loudly and as a worker gap, instead of falling
   * through to a render that quietly skipped an adjustment.
   */
  for (const track of edl.tracks) {
    for (const item of track.items) {
      if (!isClip(item)) continue;
      for (const op of item.color_ops ?? []) {
        try {
          opMatrix(op, edl.color_pipeline.working_space);
        } catch {
          unimplemented.push({
            field: `clips.${item.clip_id}.color_ops[${op.op}]`,
            declared: `amount ${op.amount}`,
            detail:
              "The contract states a transfer function for this op and this worker has no " +
              "matrix for it. Nothing here is waiting on the planner side.",
          });
        }
      }
    }
  }
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
      issue: GAP_ISSUES.startTimecode,
    });
  }

  for (const track of edl.tracks) {
    for (const item of track.items) {
      if (isClip(item)) gapsFromClip(item, gaps);
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
            issue: GAP_ISSUES.gapFill,
          });
        }
      }
    }
  }

  gapsFromTransitions(edl, gaps);
  gapsFromReframe(edl, gaps);
  gapsFromAudioPlan(edl, gaps);

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
