import type {
  Clip,
  EDL,
  MediaRef,
  Track,
  Transition,
} from "../../../contracts/codegen/generated/typescript/index.js";

import { RenderVideoError } from "./errors.js";
import { audioTracks, isClip, isGap, isTransition, videoTracks } from "./gate.js";
import {
  contains,
  frameRange,
  frames,
  fractionalFrames,
  framesToMilliseconds,
  rangeLength,
  type FrameRange,
} from "./time.js";

/**
 * The timeline, resolved to integer frame counts. Nothing downstream of this module ever
 * sees a RationalTime; the conversion happens once, here, and refuses anything that would
 * need rounding.
 */

function fail(detail: string): never {
  throw new RenderVideoError("validation_failed", `The video renderer refused the EDL: ${detail}`);
}

export interface ClipPlacement {
  clip: Clip;
  track: Track;
  /** Full extent on the timeline, before any transition borrows from either end. */
  timeline: FrameRange;
  /** Source extent, in the source's own frame numbering as declared by available_range. */
  source: FrameRange;
  mediaRef: MediaRef;
}

export interface VideoClipSegment {
  kind: "clip";
  clipId: string;
  mediaRefId: string;
  /** First source frame, relative to the start of available_range. */
  sourceOffset: number;
  length: number;
  reframeTrackId: string | null;
  /** Source frame of this segment's first frame, used to evaluate reframe keyframes. */
  sourceAbsoluteStart: number;
}

export interface VideoGapSegment {
  kind: "gap";
  fill: "black" | "white";
  length: number;
}

export interface TransitionSide {
  clipId: string;
  mediaRefId: string;
  sourceOffset: number;
  reframeTrackId: string | null;
  sourceAbsoluteStart: number;
}

export interface VideoTransitionSegment {
  kind: "transition";
  transitionType: "dissolve" | "dip_to_black" | "dip_to_white";
  /** in_offset + out_offset: the whole blend window, straddling the cut. */
  length: number;
  /** Frames the transition reaches back into the outgoing clip. */
  inOffset: number;
  /** Frames it reaches forward into the incoming clip. */
  outOffset: number;
  from: TransitionSide;
  to: TransitionSide;
}

export type VideoSegment = VideoClipSegment | VideoGapSegment | VideoTransitionSegment;

export interface AudioContribution {
  id: string;
  role: "ambient" | "music" | "sfx" | "voiceover" | "primary" | "overlay" | "titles";
  mediaRefId: string;
  sourceOffset: number;
  lengthFrames: number;
  timelineStart: number;
  gainDb: number;
  fadeInFrames: number;
  fadeOutFrames: number;
}

export interface DuckingWindow {
  ruleId: string;
  target: "music" | "ambient" | "sfx";
  reductionDb: number;
  ranges: FrameRange[];
}

export interface Program {
  rate: number;
  totalFrames: number;
  video: VideoSegment[];
  audio: AudioContribution[];
  ducking: DuckingWindow[];
  placements: ClipPlacement[];
}

function mediaRefFor(edl: EDL, mediaRefId: string): MediaRef {
  const ref = edl.media_refs.find((candidate) => candidate.media_ref_id === mediaRefId);
  if (!ref) fail(`media_ref ${mediaRefId} is not declared.`);
  return ref;
}

/**
 * How many timeline frames a clip occupies, derived from source_range and any time effect
 * exactly as contracts#50 settled it: source_range is the media read, the timeline extent
 * is derived from it, and `timeline_range` is checked against the result rather than
 * believed. A retime that does not divide into whole frames is refused — rounding here is
 * how a downbeat moves by half a frame per retimed clip, which is invisible until six of
 * them have accumulated.
 */
function timelineExtent(edl: EDL, clip: Clip, mediaFrames: number, source: FrameRange): number {
  const effect = clip.time_effect;
  if (!effect) return mediaFrames;

  if (effect.kind === "linear_speed") {
    const scalar = effect.time_scalar;
    if (typeof scalar !== "number" || !(scalar > 0)) {
      fail(`clip ${clip.clip_id} declares a linear_speed effect with no usable time_scalar.`);
    }
    const extent = mediaFrames / scalar;
    if (!Number.isInteger(extent)) {
      fail(
        `clip ${clip.clip_id} reads ${mediaFrames} source frames at ${scalar}x, which is ` +
          `${extent} timeline frames rather than a whole number.`,
      );
    }
    return extent;
  }

  const freezeAt = effect.freeze_at;
  const hold = effect.hold_duration;
  if (!freezeAt) fail(`clip ${clip.clip_id} is a freeze_frame with no freeze_at.`);
  if (!hold) fail(`clip ${clip.clip_id} is a freeze_frame with no hold_duration, so it has no extent.`);
  if (frames(freezeAt, edl.rate, `clip ${clip.clip_id} freeze_at`) !== source.start) {
    fail(
      `clip ${clip.clip_id} freezes a frame other than the one it reads; freeze_at must equal ` +
        "source_range.start_time.",
    );
  }
  if (mediaFrames !== 1) {
    fail(`clip ${clip.clip_id} is a freeze_frame reading ${mediaFrames} source frames; it reads one.`);
  }
  const extent = frames(hold, edl.rate, `clip ${clip.clip_id} hold_duration`);
  if (extent <= 0) fail(`clip ${clip.clip_id} holds a frozen frame for ${extent} frames.`);
  return extent;
}

/** Lay a track out, giving every clip and gap its integer timeline extent. */
function layoutTrack(edl: EDL, track: Track): { placements: ClipPlacement[]; end: number } {
  const placements: ClipPlacement[] = [];
  let cursor = 0;

  for (const item of track.items) {
    if (isTransition(item)) continue;
    if (isGap(item)) {
      const length = frames(item.duration, edl.rate, `gap ${item.gap_id ?? track.track_id} duration`);
      if (length <= 0) fail(`gap ${item.gap_id ?? track.track_id} has a non-positive duration.`);
      cursor += length;
      continue;
    }

    const clip = item;
    if (clip.enabled === false) {
      fail(
        `clip ${clip.clip_id} is disabled. A disabled clip either occupies its timeline extent ` +
          "as black or collapses it, and the contract says which nowhere; the planner should " +
          "emit a Gap instead.",
      );
    }
    const mediaRef = mediaRefFor(edl, clip.media_ref_id);
    const source = frameRange(clip.source_range, edl.rate, `clip ${clip.clip_id} source_range`);
    const available = frameRange(mediaRef.available_range, edl.rate, `media_ref ${mediaRef.media_ref_id} available_range`);
    if (!contains(available, source)) {
      fail(
        `clip ${clip.clip_id} reads source frames [${source.start}, ${source.end}) but ` +
          `${mediaRef.media_ref_id} only offers [${available.start}, ${available.end}).`,
      );
    }
    const length = timelineExtent(edl, clip, rangeLength(source), source);
    const timeline: FrameRange = { start: cursor, end: cursor + length };

    if (clip.timeline_range) {
      const declared = frameRange(clip.timeline_range, edl.rate, `clip ${clip.clip_id} timeline_range`);
      if (declared.start !== timeline.start || declared.end !== timeline.end) {
        fail(
          `clip ${clip.clip_id} declares timeline_range [${declared.start}, ${declared.end}) but ` +
            `the items before it sum to [${timeline.start}, ${timeline.end}). The planner and the ` +
            "renderer disagree about where this clip is, and absorbing that silently is how a " +
            "beat-locked cut drifts.",
        );
      }
    }

    placements.push({ clip, track, timeline, source, mediaRef });
    cursor += length;
  }

  return { placements, end: cursor };
}

interface TransitionBinding {
  transition: Transition;
  inOffset: number;
  outOffset: number;
  fromIndex: number;
  toIndex: number;
}

function bindTransitions(edl: EDL, track: Track, placements: readonly ClipPlacement[]): TransitionBinding[] {
  const bindings: TransitionBinding[] = [];
  let clipIndex = -1;

  track.items.forEach((item, index) => {
    if (isClip(item) || isGap(item)) {
      if (isClip(item)) clipIndex += 1;
      return;
    }
    const transition = item;
    const id = transition.transition_id ?? `${track.track_id}[${index}]`;
    const before = track.items[index - 1];
    const after = track.items[index + 1];
    if (!before || !after || !isClip(before) || !isClip(after)) {
      fail(`transition ${id} does not sit between two clips.`);
    }
    const inOffset = frames(transition.in_offset, edl.rate, `transition ${id} in_offset`);
    const outOffset = frames(transition.out_offset, edl.rate, `transition ${id} out_offset`);
    if (inOffset < 0 || outOffset < 0) fail(`transition ${id} has a negative offset.`);
    if (inOffset + outOffset === 0) {
      fail(
        `transition ${id} is zero length. A hard cut is the ABSENCE of a transition; OTIO's own ` +
          "convention, and some NLEs render a zero-length transition as a one-frame dissolve.",
      );
    }

    const from = placements[clipIndex];
    const to = placements[clipIndex + 1];
    if (!from || !to) fail(`transition ${id} has no laid-out neighbours.`);
    bindings.push({ transition, inOffset, outOffset, fromIndex: clipIndex, toIndex: clipIndex + 1 });
  });

  return bindings;
}

/**
 * Transition geometry.
 *
 * The schema's field descriptions and the golden fixture agree on this shape and the
 * top-of-file $comment does not (see contracts#52): a transition does NOT change either
 * neighbour's timeline extent. The blend region straddles the cut, covering the outgoing
 * clip's last `in_offset` frames and the incoming clip's first `out_offset` frames, and
 * the frames it needs beyond each clip's source_range come from available_range — the
 * handles. That is the only reading under which the fixture's timeline_ranges sum to the
 * 899 frames its own validation report claims, which is also the only reading under which
 * its recorded beat alignments are true.
 */
function transitionSegment(
  edl: EDL,
  binding: TransitionBinding,
  placements: readonly ClipPlacement[],
): VideoTransitionSegment {
  const { transition, inOffset, outOffset } = binding;
  const id = transition.transition_id ?? `${binding.fromIndex}->${binding.toIndex}`;
  const from = placements[binding.fromIndex]!;
  const to = placements[binding.toIndex]!;

  const fromAvailable = frameRange(from.mediaRef.available_range, edl.rate, "available_range");
  const toAvailable = frameRange(to.mediaRef.available_range, edl.rate, "available_range");

  const fromBlendStart = from.source.end - inOffset;
  const fromBlendEnd = from.source.end + outOffset;
  const toBlendStart = to.source.start - inOffset;
  const toBlendEnd = to.source.start + outOffset;

  if (fromBlendStart < fromAvailable.start || fromBlendEnd > fromAvailable.end) {
    fail(
      `transition ${id} needs source frames [${fromBlendStart}, ${fromBlendEnd}) of ` +
        `${from.mediaRef.media_ref_id} for ${from.clip.clip_id}'s handle, and only ` +
        `[${fromAvailable.start}, ${fromAvailable.end}) exists.`,
    );
  }
  if (toBlendStart < toAvailable.start || toBlendEnd > toAvailable.end) {
    fail(
      `transition ${id} needs source frames [${toBlendStart}, ${toBlendEnd}) of ` +
        `${to.mediaRef.media_ref_id} for ${to.clip.clip_id}'s handle, and only ` +
        `[${toAvailable.start}, ${toAvailable.end}) exists.`,
    );
  }

  const kind = transition.transition_type;
  if (kind !== "dissolve" && kind !== "dip_to_black" && kind !== "dip_to_white") {
    fail(`transition ${id} of type ${kind} reached the program builder; the gate should have refused it.`);
  }

  return {
    kind: "transition",
    transitionType: kind,
    length: inOffset + outOffset,
    inOffset,
    outOffset,
    from: {
      clipId: from.clip.clip_id,
      mediaRefId: from.mediaRef.media_ref_id,
      sourceOffset: fromBlendStart - fromAvailable.start,
      reframeTrackId: from.clip.reframe_track_id ?? null,
      sourceAbsoluteStart: fromBlendStart,
    },
    to: {
      clipId: to.clip.clip_id,
      mediaRefId: to.mediaRef.media_ref_id,
      sourceOffset: toBlendStart - toAvailable.start,
      reframeTrackId: to.clip.reframe_track_id ?? null,
      sourceAbsoluteStart: toBlendStart,
    },
  };
}

function buildVideoProgram(edl: EDL, track: Track, placements: readonly ClipPlacement[]): VideoSegment[] {
  const bindings = bindTransitions(edl, track, placements);
  const incoming = new Map<number, TransitionBinding>();
  const outgoing = new Map<number, TransitionBinding>();
  for (const binding of bindings) {
    if (outgoing.has(binding.fromIndex) || incoming.has(binding.toIndex)) {
      fail("two transitions meet on the same clip edge.");
    }
    outgoing.set(binding.fromIndex, binding);
    incoming.set(binding.toIndex, binding);
  }

  const segments: VideoSegment[] = [];
  let clipIndex = -1;

  for (const item of track.items) {
    if (isTransition(item)) continue;

    if (isGap(item)) {
      const fill = item.fill ?? "black";
      if (fill !== "black" && fill !== "white") {
        fail(`gap ${item.gap_id ?? track.track_id} has fill ${fill} on a video track.`);
      }
      segments.push({
        kind: "gap",
        fill,
        length: frames(item.duration, edl.rate, "gap duration"),
      });
      continue;
    }

    clipIndex += 1;
    const placement = placements[clipIndex]!;
    const incomingBinding = incoming.get(clipIndex);
    const outgoingBinding = outgoing.get(clipIndex);
    const trimFront = incomingBinding?.outOffset ?? 0;
    const trimBack = outgoingBinding?.inOffset ?? 0;
    // Timeline frames, not source frames: on a retimed clip the two differ, and a
    // transition consumes timeline. (A transition on a retimed clip is refused before
    // here — the handle would have to be converted into media too — but the length this
    // segment shows is a timeline length either way.)
    const extent = rangeLength(placement.timeline);
    const length = extent - trimFront - trimBack;
    if (length <= 0) {
      fail(
        `clip ${placement.clip.clip_id} is ${extent} frames long and its ` +
          `transitions consume ${trimFront + trimBack} of them, leaving nothing to show.`,
      );
    }

    const available = frameRange(placement.mediaRef.available_range, edl.rate, "available_range");
    const sourceAbsoluteStart = placement.source.start + trimFront;
    segments.push({
      kind: "clip",
      clipId: placement.clip.clip_id,
      mediaRefId: placement.mediaRef.media_ref_id,
      sourceOffset: sourceAbsoluteStart - available.start,
      length,
      reframeTrackId: placement.clip.reframe_track_id ?? null,
      sourceAbsoluteStart,
    });

    if (outgoingBinding) {
      segments.push(transitionSegment(edl, outgoingBinding, placements));
    }
  }

  return segments;
}

function buildAudioProgram(
  edl: EDL,
  videoPlacements: readonly ClipPlacement[],
): { audio: AudioContribution[]; audioPlacements: ClipPlacement[] } {
  const plan = edl.audio_plan;
  const contributions: AudioContribution[] = [];
  const audioPlacements: ClipPlacement[] = [];

  const ambient = plan?.ambient;
  const ambientEnabled = ambient ? ambient.enabled !== false : plan != null;
  const perClipGain = new Map<string, number>();
  for (const entry of ambient?.per_clip_gain_db ?? []) perClipGain.set(entry.clip_id, entry.gain_db);

  for (const placement of videoPlacements) {
    const audio = placement.clip.audio;
    if (!audio || audio.muted === true) continue;
    // contracts#50: `audio_handling: mute` suppresses the clip's bed entirely, whatever
    // gain the AmbientPlan would otherwise give it. Pitch-shifted ambient sounds broken,
    // and the plan saying "mute" outranks the plan saying "-6 dB".
    if ((placement.clip.time_effect?.audio_handling ?? "mute") === "mute" && placement.clip.time_effect) {
      continue;
    }
    if (!plan) {
      fail(
        `clip ${placement.clip.clip_id} declares audio but the EDL has no audio_plan, so there ` +
          "is no mix to render it into — no sample rate, no channel count, no loudness target.",
      );
    }
    if (!ambientEnabled) continue;

    const gainDb = perClipGain.get(placement.clip.clip_id) ?? ambient?.default_gain_db ?? audio.gain_db ?? 0;
    const tail = audio.audio_extends_past_out
      ? frames(audio.audio_extends_past_out, edl.rate, `clip ${placement.clip.clip_id} audio_extends_past_out`)
      : 0;
    const available = frameRange(placement.mediaRef.available_range, edl.rate, "available_range");
    if (placement.source.end + tail > available.end) {
      fail(
        `clip ${placement.clip.clip_id} holds its audio ${tail} frames past its out point and the ` +
          "source does not have them.",
      );
    }

    contributions.push({
      id: `ambient-${placement.clip.clip_id}`,
      role: "ambient",
      mediaRefId: placement.mediaRef.media_ref_id,
      sourceOffset: placement.source.start - available.start,
      lengthFrames: rangeLength(placement.source) + tail,
      timelineStart: placement.timeline.start,
      gainDb,
      fadeInFrames: audio.fade_in ? frames(audio.fade_in, edl.rate, "fade_in") : 0,
      fadeOutFrames: audio.fade_out ? frames(audio.fade_out, edl.rate, "fade_out") : 0,
    });
  }

  for (const track of audioTracks(edl)) {
    const layout = layoutTrack(edl, track);
    audioPlacements.push(...layout.placements);
    const role = track.role ?? "primary";
    for (const placement of layout.placements) {
      const audio = placement.clip.audio;
      if (audio?.muted === true) continue;
      const available = frameRange(placement.mediaRef.available_range, edl.rate, "available_range");
      contributions.push({
        id: `track-${track.track_id}-${placement.clip.clip_id}`,
        role,
        mediaRefId: placement.mediaRef.media_ref_id,
        sourceOffset: placement.source.start - available.start,
        lengthFrames: rangeLength(placement.source),
        timelineStart: placement.timeline.start,
        gainDb: audio?.gain_db ?? 0,
        fadeInFrames: audio?.fade_in ? frames(audio.fade_in, edl.rate, "fade_in") : 0,
        fadeOutFrames: audio?.fade_out ? frames(audio.fade_out, edl.rate, "fade_out") : 0,
      });
    }
  }

  return { audio: contributions, audioPlacements };
}

/**
 * The bed is placed once, on a track, and the cue names that placement (contracts#59).
 * This used to be a field-by-field reconciliation of two copies of the same bed; the
 * contract now has only one copy, so all that is left is resolution — and the one thing
 * resolution has to guarantee, which is that no music plays without a licence attached to
 * it and no clip is licensed twice.
 */
function reconcileMusicCues(edl: EDL, audioPlacements: readonly ClipPlacement[]): void {
  const musicClips = new Map<string, ClipPlacement>();
  for (const placement of audioPlacements) {
    if ((placement.track.role ?? "primary") !== "music") continue;
    musicClips.set(placement.clip.clip_id, placement);
  }

  const claimedBy = new Map<string, string>();
  for (const cue of edl.audio_plan?.music ?? []) {
    if (cue.clip_ids.length === 0) {
      fail(`music cue ${cue.cue_id} places itself on no clips, so the bed it licenses never plays.`);
    }
    for (const clipId of cue.clip_ids) {
      const placement = musicClips.get(clipId);
      if (!placement) {
        fail(
          `music cue ${cue.cue_id} names clip ${clipId}, which is not a clip on a music-role ` +
            "audio track. A cue is a licence attached to a placement, not a placement.",
        );
      }
      const already = claimedBy.get(clipId);
      if (already) {
        fail(`clip ${clipId} is claimed by music cues ${already} and ${cue.cue_id}.`);
      }
      claimedBy.set(clipId, cue.cue_id);
      if (placement.mediaRef.media_ref_id !== cue.media_ref_id) {
        fail(
          `music cue ${cue.cue_id} licenses ${cue.media_ref_id} but clip ${clipId} plays ` +
            `${placement.mediaRef.media_ref_id}.`,
        );
      }
    }
  }

  for (const clipId of musicClips.keys()) {
    if (!claimedBy.has(clipId)) {
      fail(
        `music clip ${clipId} is on a music-role track and no cue claims it, so it would play ` +
          "with no licence attached.",
      );
    }
  }
}

function buildDucking(edl: EDL): DuckingWindow[] {
  const windows: DuckingWindow[] = [];
  for (const rule of edl.audio_plan?.ducking ?? []) {
    windows.push({
      ruleId: rule.rule_id,
      target: rule.target,
      reductionDb: rule.reduction_db,
      ranges: (rule.ranges ?? []).map((range) =>
        frameRange(range, edl.rate, `ducking rule ${rule.rule_id} range`),
      ),
    });
  }
  return windows;
}

/**
 * Re-derives every beat_lock from integer frame positions and checks it against the grid.
 * The plan claims a <50 ms downbeat error; this is what makes the claim true of the thing
 * actually being rendered rather than of the thing the planner believed it emitted.
 */
export function verifyBeatLocks(edl: EDL, placements: readonly ClipPlacement[]): void {
  const grid = edl.beat_grid;
  const locked = placements.filter((placement) => placement.clip.beat_lock);
  if (locked.length === 0) return;
  if (!grid) fail("clips declare beat_lock but the EDL carries no beat_grid to check them against.");

  const tolerance = grid.tolerance_ms ?? 50;
  for (const placement of locked) {
    const lock = placement.clip.beat_lock!;
    const beat = grid.beats[lock.beat_index];
    if (!beat) {
      fail(`clip ${placement.clip.clip_id} locks to beat ${lock.beat_index}, which the grid does not have.`);
    }
    if (beat.index !== lock.beat_index) {
      fail(
        `beat_grid.beats[${lock.beat_index}] carries index ${beat.index}. The grid is not in index ` +
          "order and a lock cannot be resolved.",
      );
    }
    if ((lock.is_downbeat ?? false) !== beat.is_downbeat) {
      fail(
        `clip ${placement.clip.clip_id} says beat ${lock.beat_index} is ` +
          `${lock.is_downbeat ? "" : "not "}a downbeat and the grid says otherwise.`,
      );
    }

    const beatFrames = fractionalFrames(beat.time, edl.rate, `beat ${lock.beat_index} time`);
    const measured = framesToMilliseconds(placement.timeline.start - beatFrames, edl.rate);
    if (Math.abs(measured - lock.alignment_error_ms) > 0.5) {
      fail(
        `clip ${placement.clip.clip_id} records an alignment error of ${lock.alignment_error_ms} ms ` +
          `against beat ${lock.beat_index}, but its in-point at frame ${placement.timeline.start} is ` +
          `${measured.toFixed(4)} ms from that beat. The plan's own audit trail does not describe ` +
          "the cut it is asking for.",
      );
    }
    if (Math.abs(measured) > tolerance) {
      fail(
        `clip ${placement.clip.clip_id} is ${measured.toFixed(4)} ms from beat ${lock.beat_index}, ` +
          `outside the grid's ${tolerance} ms tolerance.`,
      );
    }
  }
}

export function buildProgram(edl: EDL): Program {
  const track = videoTracks(edl)[0]!;
  const layout = layoutTrack(edl, track);
  const video = buildVideoProgram(edl, track, layout.placements);
  const totalFrames = video.reduce((sum, segment) => sum + segment.length, 0);

  if (totalFrames !== layout.end) {
    fail(
      `the rendered program is ${totalFrames} frames but the track lays out to ${layout.end}. A ` +
        "transition must not change the timeline's length.",
    );
  }
  if (totalFrames <= 0) fail("the timeline is empty.");

  const maxDuration = edl.target.max_duration;
  if (maxDuration) {
    const ceiling = frames(maxDuration, edl.rate, "target.max_duration");
    if (totalFrames > ceiling) {
      fail(`the timeline is ${totalFrames} frames and the platform ceiling is ${ceiling}.`);
    }
  }

  verifyBeatLocks(edl, layout.placements);

  const { audio, audioPlacements } = buildAudioProgram(edl, layout.placements);
  reconcileMusicCues(edl, audioPlacements);

  for (const contribution of audio) {
    if (contribution.timelineStart < 0 || contribution.timelineStart > totalFrames) {
      fail(`audio contribution ${contribution.id} starts outside the timeline.`);
    }
  }

  const ducking = buildDucking(edl);
  for (const window of ducking) {
    for (const range of window.ranges) {
      if (range.start < 0 || range.end > totalFrames) {
        fail(`ducking rule ${window.ruleId} covers frames outside the timeline.`);
      }
    }
  }

  return {
    rate: edl.rate,
    totalFrames,
    video,
    audio,
    ducking,
    placements: [...layout.placements, ...audioPlacements],
  };
}
