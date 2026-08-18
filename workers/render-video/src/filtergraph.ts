import type {
  Clip,
  ColorOp,
  EDL,
  EncodeProfile,
  MediaRef,
  MixPlan,
  ReframeTrack,
} from "../../../contracts/codegen/generated/typescript/index.js";

import { colorChain } from "./color.js";
import { RenderVideoError } from "./errors.js";
import type { AudioContribution, DuckingWindow, Program, VideoSegment } from "./program.js";
import { frameSeriesExpression, planCrop } from "./reframe.js";
import type { ResolvedSource } from "./sources.js";
import { framesToSamples, framesToSeconds, secondsArg } from "./time.js";

/**
 * The filtergraph, built once from integer frame positions.
 *
 * Every source use gets its own ffmpeg input rather than a `split` of a shared one. A
 * split forces the framework to buffer frames until the slowest branch consumes them,
 * which on a seventeen-minute source with eight widely-spaced clips is a memory profile
 * nobody wants. The cost is that a long source is decoded once per use; see the README's
 * note on seeking, which is the obvious next optimisation and the one place where getting
 * it wrong would silently move a cut.
 */

export interface GraphInput {
  args: string[];
  mediaRefId: string;
}

export interface BuiltGraph {
  /** Video inputs first, then audio inputs; audio chain indices are offset to match. */
  inputs: GraphInput[];
  /** Filter graph text, ending at [vout] and, when there is audio, [amix]. */
  filter: string;
  videoLabel: string;
  audioLabel: string | null;
  /**
   * The audio half on its own, numbered from input 0. The loudness measurement pass runs
   * this alone: ffmpeg requires every filter_complex output to be mapped, and measuring the
   * mix should not mean encoding the picture twice.
   */
  audioOnly: { inputs: GraphInput[]; filter: string; label: string } | null;
  /** Documented conventions applied where the contract stops short of the last detail. */
  interpretations: Interpretation[];
}

export interface Interpretation {
  field: string;
  convention: string;
  issue: string;
}

function fail(detail: string): never {
  throw new RenderVideoError("validation_failed", `The video renderer could not build a filtergraph: ${detail}`);
}

function decibelsToLinear(db: number): number {
  return 10 ** (db / 20);
}

class GraphBuilder {
  readonly inputs: GraphInput[] = [];
  readonly chains: string[] = [];
  readonly interpretations: Interpretation[] = [];

  constructor(private readonly baseIndex = 0) {}

  addInput(source: ResolvedSource): number {
    this.inputs.push({ args: [...source.inputArgs], mediaRefId: source.mediaRefId });
    return this.baseIndex + this.inputs.length - 1;
  }

  add(chain: string): void {
    this.chains.push(chain);
  }

  note(field: string, convention: string, issue: string): void {
    if (this.interpretations.some((entry) => entry.field === field)) return;
    this.interpretations.push({ field, convention, issue });
  }
}

function cropChain(
  builder: GraphBuilder,
  track: ReframeTrack,
  rate: number,
  sourceStart: number,
  length: number,
  source: ResolvedSource,
): string {
  if (!source.video) fail(`${source.mediaRefId} has no video stream to crop.`);
  const plan = planCrop(track, rate, sourceStart, length, source.video.width, source.video.height);
  builder.note(
    "reframe_tracks[].keyframes[].crop",
    "Normalised crop coordinates are resolved to whole pixels with round-half-up, then bounds " +
      "checked against the source frame; a crop that rounds outside the frame is a hard failure " +
      "rather than a clamp. Now stated by the contract rather than chosen here — see the " +
      "NormalizedBox $comment in common.schema.json.",
    "contracts#60 (closed)",
  );
  const x = frameSeriesExpression(plan.x);
  const y = frameSeriesExpression(plan.y);
  return `,crop=w=${plan.width}:h=${plan.height}:x='${x}':y='${y}'`;
}

/**
 * The four easing curves of contracts#52, as ffmpeg expressions in the linear progress
 * `u`. Written with only + - and * so the arithmetic is bit-identical everywhere, which is
 * the same reason ReframeKeyframe's `smooth` is a uniform rather than a centripetal
 * spline. `u` is substituted textually and is always a parenthesised sub-expression.
 */
export function easingExpression(easing: string | null | undefined, u: string): string {
  switch (easing ?? "linear") {
    case "linear":
      return u;
    case "ease_in":
      return `${u}*${u}*${u}`;
    case "ease_out":
      return `1-(1-${u})*(1-${u})*(1-${u})`;
    case "ease_in_out":
      return `if(lt(${u},0.5),4*${u}*${u}*${u},1-4*(1-${u})*(1-${u})*(1-${u}))`;
    default:
      return fail(`transition easing ${easing} has no curve in this worker.`);
  }
}

/** A constant-colour clip of `length` frames, normalised like every other segment. */
function colourSource(
  builder: GraphBuilder,
  edl: EDL,
  profile: EncodeProfile,
  colour: "black" | "white",
  length: number,
  label: string,
): string {
  builder.add(
    `color=c=${colour}:s=${edl.target.resolution.width}x${edl.target.resolution.height}:r=${edl.rate}` +
      `,trim=end_frame=${length},setpts=PTS-STARTPTS,setsar=1,format=${profile.video.pixel_format}[${label}]`,
  );
  return label;
}

/**
 * Size, aspect and pixel format, plus the colour path for this source (contracts#58).
 *
 * Geometry runs FIRST, in the source's own encoding, because that is what the contract
 * states: scaling in linear light and scaling in a gamma-encoded space give different
 * edges, and which one it is must not be a renderer's habit. The colour chain then ends in
 * the pixel format, so an identity chain and a converting one leave the same shape behind
 * for concat and blend to work on.
 */
function normaliseChain(
  builder: GraphBuilder,
  edl: EDL,
  profile: EncodeProfile,
  ref: MediaRef,
  ops: readonly ColorOp[],
): string {
  const { width, height } = edl.target.resolution;
  const geometry = `,scale=${width}:${height}:flags=${profile.scaler},setsar=1`;
  const colour = colorChain(edl.color_pipeline, ref, ops, profile);
  if (colour.identity) {
    builder.note(
      "color_pipeline (identity)",
      `${ref.color_encoding} source delivered as ${edl.color_pipeline.output_encoding} with no ` +
        "colour op: the code values are passed through untouched. Stated by the contract as " +
        "normative rather than chosen here — a round trip through linear light is not lossless " +
        "at 8 bits per channel, so inserting one would change every frame to no purpose.",
      "contracts#58 (closed)",
    );
    return `${geometry},format=${profile.video.pixel_format}`;
  }
  builder.note(
    "color_pipeline",
    `${ref.color_encoding} linearised into ${edl.color_pipeline.working_space}, ` +
      `${ops.length} colour op(s) applied as one fused 3x3 matrix, ` +
      `${edl.color_pipeline.tone_map ? `${edl.color_pipeline.tone_map.operator} tone map, ` : ""}` +
      `delivered as ${edl.color_pipeline.output_encoding}. Every quantity is the contract's ` +
      "own formula (contracts#49, contracts#58); nothing here is a default.",
    "contracts#49, contracts#58 (closed)",
  );
  return `${geometry},${colour.filter}`;
}

/**
 * A transition blends two clips that have BOTH already reached `output_encoding`, so the
 * blend happens in the delivered encoding rather than in linear light. Stated in
 * ColorPipeline's $comment: a dissolve between two differently-encoded sources otherwise
 * has no defined value space at all, and picking one silently is how two renderers get
 * different mid-dissolve frames.
 */
function colorOpsByClip(edl: EDL): ReadonlyMap<string, readonly ColorOp[]> {
  const map = new Map<string, readonly ColorOp[]>();
  for (const track of edl.tracks) {
    for (const item of track.items) {
      if (item.item_type !== "clip") continue;
      map.set((item as Clip).clip_id, (item as Clip).color_ops ?? []);
    }
  }
  return map;
}

function mediaRefsById(edl: EDL): ReadonlyMap<string, MediaRef> {
  return new Map(edl.media_refs.map((ref) => [ref.media_ref_id, ref]));
}

/**
 * One video segment, normalised to the target size and pixel format so that concat and
 * xfade see identical streams.
 */
function videoSegmentChain(
  builder: GraphBuilder,
  edl: EDL,
  profile: EncodeProfile,
  sources: ReadonlyMap<string, ResolvedSource>,
  segment: VideoSegment,
  index: number,
): string {
  const label = `v${index}`;
  const rate = edl.rate;
  const reframeById = new Map((edl.reframe_tracks ?? []).map((track) => [track.reframe_track_id, track]));
  const refById = mediaRefsById(edl);
  const opsByClip = colorOpsByClip(edl);
  const refFor = (mediaRefId: string): MediaRef => {
    const ref = refById.get(mediaRefId);
    if (!ref) fail(`media_ref ${mediaRefId} is not declared.`);
    return ref;
  };

  if (segment.kind === "gap") {
    const colour = segment.fill === "white" ? "white" : "black";
    builder.add(
      `color=c=${colour}:s=${edl.target.resolution.width}x${edl.target.resolution.height}:r=${rate}` +
        `,trim=end_frame=${segment.length},setpts=PTS-STARTPTS,setsar=1,format=${profile.video.pixel_format}[${label}]`,
    );
    return label;
  }

  if (segment.kind === "clip") {
    const source = sources.get(segment.mediaRefId);
    if (!source) fail(`no resolved source for ${segment.mediaRefId}.`);
    const input = builder.addInput(source);
    let chain = `[${input}:v]trim=start_frame=${segment.sourceOffset}:end_frame=${segment.sourceOffset + segment.length},setpts=PTS-STARTPTS`;
    if (segment.reframeTrackId) {
      const track = reframeById.get(segment.reframeTrackId);
      if (!track) fail(`reframe track ${segment.reframeTrackId} is not declared.`);
      chain += cropChain(builder, track, rate, segment.sourceAbsoluteStart, segment.length, source);
    }
    chain += normaliseChain(
      builder,
      edl,
      profile,
      refFor(segment.mediaRefId),
      opsByClip.get(segment.clipId) ?? [],
    );
    builder.add(`${chain}[${label}]`);
    return label;
  }

  const sides = [segment.from, segment.to] as const;
  const sideLabels: string[] = [];
  sides.forEach((side, position) => {
    const source = sources.get(side.mediaRefId);
    if (!source) fail(`no resolved source for ${side.mediaRefId}.`);
    const input = builder.addInput(source);
    const sideLabel = `x${index}_${position}`;
    let chain = `[${input}:v]trim=start_frame=${side.sourceOffset}:end_frame=${side.sourceOffset + segment.length},setpts=PTS-STARTPTS`;
    if (side.reframeTrackId) {
      const track = reframeById.get(side.reframeTrackId);
      if (!track) fail(`reframe track ${side.reframeTrackId} is not declared.`);
      chain += cropChain(builder, track, rate, side.sourceAbsoluteStart, segment.length, source);
    }
    chain += normaliseChain(
      builder,
      edl,
      profile,
      refFor(side.mediaRefId),
      opsByClip.get(side.clipId) ?? [],
    );
    builder.add(`${chain}[${sideLabel}]`);
    sideLabels.push(sideLabel);
  });

  builder.note(
    "transitions[].easing",
    "The blend weight is easing(N/L), with the four curves written out as polynomials in the " +
      "schema's Transition $comment. Indexed by frame number rather than by seconds, so it " +
      "cannot drift; the polynomials use only + - and *, so every implementation lands on the " +
      "same bits.",
    "contracts#52 (closed)",
  );

  /**
   * The blend is driven by `blend`'s frame counter rather than by `xfade`'s seconds. Both
   * would work, but `xfade` needs a declared constant frame rate on its inputs, and `trim`
   * marks its output rate unknown by design — conforming it back with `fps` would put a
   * filter that is allowed to duplicate or drop a frame in the middle of a beat-locked
   * cut. Frame numbers cannot drift.
   */
  if (segment.transitionType === "dissolve") {
    const weight = easingExpression(segment.easing, `(N/${segment.length})`);
    builder.add(`[${sideLabels[0]}][${sideLabels[1]}]blend=all_expr='A*(1-(${weight}))+B*(${weight})'[${label}]`);
    return label;
  }

  // A dip goes to a colour at the cut and comes back out of it, so it is two ramps rather
  // than one blend — but each ramp is a blend against a constant-colour source driven by
  // the SAME weight polynomial as a dissolve. `fade` was what this used before and it only
  // ramps linearly, which would have made `easing` mean one thing on a dissolve and
  // another on a dip.
  const colour = segment.transitionType === "dip_to_black" ? "black" : "white";
  const halves: string[] = [];
  if (segment.inOffset > 0) {
    const out = `${label}_out`;
    const dip = colourSource(builder, edl, profile, colour, segment.inOffset, `${label}_c0`);
    const weight = easingExpression(segment.easing, `(N/${segment.inOffset})`);
    builder.add(
      `[${sideLabels[0]}]trim=end_frame=${segment.inOffset},setpts=PTS-STARTPTS[${label}_a0]`,
    );
    builder.add(
      `[${label}_a0][${dip}]blend=all_expr='A*(1-(${weight}))+B*(${weight})'[${out}]`,
    );
    halves.push(out);
  }
  if (segment.outOffset > 0) {
    const into = `${label}_in`;
    const dip = colourSource(builder, edl, profile, colour, segment.outOffset, `${label}_c1`);
    const weight = easingExpression(segment.easing, `(N/${segment.outOffset})`);
    builder.add(
      `[${sideLabels[1]}]trim=start_frame=${segment.inOffset},setpts=PTS-STARTPTS[${label}_a1]`,
    );
    builder.add(
      `[${dip}][${label}_a1]blend=all_expr='A*(1-(${weight}))+B*(${weight})'[${into}]`,
    );
    halves.push(into);
  }
  if (halves.length === 1) {
    builder.add(`[${halves[0]}]null[${label}]`);
  } else {
    builder.add(`${halves.map((entry) => `[${entry}]`).join("")}concat=n=2:v=1:a=0[${label}]`);
  }
  return label;
}

function channelLayout(mix: MixPlan): string {
  return (mix.channels ?? "stereo") === "mono" ? "mono" : "stereo";
}

function audioContributionChain(
  builder: GraphBuilder,
  rate: number,
  mix: MixPlan,
  sources: ReadonlyMap<string, ResolvedSource>,
  contribution: AudioContribution,
  index: number,
): string {
  const source = sources.get(contribution.mediaRefId);
  if (!source) fail(`no resolved source for ${contribution.mediaRefId}.`);
  if (!source.audio) {
    fail(`${contribution.mediaRefId} carries no audio stream but the plan reads audio from it.`);
  }
  const sampleRate = mix.sample_rate ?? 48000;
  const input = builder.addInput(source);
  const label = `a${index}`;

  const startSample = framesToSamples(contribution.sourceOffset, rate, sampleRate);
  const endSample = framesToSamples(contribution.sourceOffset + contribution.lengthFrames, rate, sampleRate);
  const delaySamples = framesToSamples(contribution.timelineStart, rate, sampleRate);

  let chain =
    `[${input}:a]aformat=sample_fmts=fltp:sample_rates=${sampleRate}:channel_layouts=${channelLayout(mix)}` +
    `,atrim=start_sample=${startSample}:end_sample=${endSample},asetpts=PTS-STARTPTS`;

  if (contribution.fadeInFrames > 0 || contribution.fadeOutFrames > 0) {
    builder.note(
      "ClipAudio.fade_in / fade_out",
      "A fade is executed as a linear ramp in amplitude over the declared frame count — " +
        "ffmpeg's `tri` curve — which is what the field descriptions now state, rather than " +
        "the equal-power curve a mixer might otherwise reach for.",
      "contracts#60 (closed)",
    );
  }
  if (contribution.fadeInFrames > 0) {
    const samples = framesToSamples(contribution.fadeInFrames, rate, sampleRate);
    chain += `,afade=type=in:curve=tri:start_sample=0:nb_samples=${samples}`;
  }
  if (contribution.fadeOutFrames > 0) {
    const samples = framesToSamples(contribution.fadeOutFrames, rate, sampleRate);
    chain += `,afade=type=out:curve=tri:start_sample=${endSample - startSample - samples}:nb_samples=${samples}`;
  }
  if (contribution.gainDb !== 0) chain += `,volume=${contribution.gainDb}dB`;
  if (delaySamples > 0) chain += `,adelay=delays=${delaySamples}S:all=1`;

  builder.add(`${chain}[${label}]`);
  return label;
}

/**
 * One rule's reduction, in dB, as an ffmpeg expression in `t` (contracts#54).
 *
 * Per range [s, e): the reduction ramps linearly IN dB from 0 to reduction_db over
 * [s-a, s), holds reduction_db over [s, e), and ramps back to 0 over [e, e+r). Within one
 * rule the envelope is the maximum over its own ranges, which is what makes two ranges
 * closer together than attack+release well defined rather than a race between a release
 * and the next attack.
 */
/** ffmpeg's `max` takes exactly two arguments, so an n-way maximum is folded. */
function foldMax(terms: readonly string[]): string {
  return terms.reduce((left, right) => `max(${left},${right})`);
}

function reductionExpression(window: DuckingWindow, rate: number): string {
  const attack = framesToSeconds(window.attackFrames, rate);
  const release = framesToSeconds(window.releaseFrames, rate);
  const reduction = window.reductionDb;

  const perRange = window.ranges.map((range) => {
    const s = framesToSeconds(range.start, rate);
    const e = framesToSeconds(range.end, rate);
    const held = `between(t,${secondsArg(s)},${secondsArg(e)})*${reduction}`;
    const parts = [held];
    if (attack > 0) {
      parts.push(
        `between(t,${secondsArg(s - attack)},${secondsArg(s)})*${reduction}*` +
          `((t-${secondsArg(s - attack)})/${secondsArg(attack)})`,
      );
    }
    if (release > 0) {
      parts.push(
        `between(t,${secondsArg(e)},${secondsArg(e + release)})*${reduction}*` +
          `(1-(t-${secondsArg(e)})/${secondsArg(release)})`,
      );
    }
    // The three pieces are disjoint except at their shared endpoints, where `between` is
    // inclusive on both sides and each neighbour contributes the same value, so max() and
    // not a sum: adding them would double the gain on exactly the two sample boundaries a
    // listener would hear as a click. Folded pairwise because ffmpeg's `max` is binary.
    return foldMax(parts);
  });

  return foldMax(perRange);
}

function duckingChain(builder: GraphBuilder, rate: number, windows: readonly DuckingWindow[], label: string): string {
  if (windows.length === 0) return label;
  let current = label;
  windows.forEach((window, index) => {
    if (window.ranges.length === 0) return;
    const next = `${current}_d${index}`;
    const stepped = window.attackFrames === 0 && window.releaseFrames === 0;
    if (stepped) {
      const gain = decibelsToLinear(-window.reductionDb);
      const enable = window.ranges
        .map((range) => {
          const start = secondsArg(framesToSeconds(range.start, rate));
          const end = secondsArg(framesToSeconds(range.end, rate));
          return `between(t,${start},${end})`;
        })
        .join("+");
      builder.add(`[${current}]volume=volume=${gain.toFixed(9)}:enable='${enable}'[${next}]`);
    } else {
      builder.note(
        "audio_plan.ducking[].attack_ms / release_ms",
        "The envelope is the one contracts#54 states: linear in dB, the ramp down ending at the " +
          "range start and the ramp up beginning at the range end, so the declared range is fully " +
          "ducked for its whole declared extent. `volume` re-evaluates the expression once per " +
          "audio frame, so the ramp is a staircase of 1024-sample treads (21 ms at 48 kHz) rather " +
          "than a continuous line — the same quantisation the stepped form has always had at its " +
          "edges, and the reason `eval=frame` is stated here rather than assumed.",
        "contracts#54",
      );
      builder.add(
        `[${current}]volume=volume='pow(10,-(${reductionExpression(window, rate)})/20)':eval=frame[${next}]`,
      );
    }
    current = next;
  });
  return current;
}

/**
 * The ambient high-pass (contracts#53), applied ONCE to the summed ambient group — after
 * each clip's gain, fades and L-cut tail, and before any duck — because that is the order
 * the contract states.
 *
 * Butterworth, realised as a cascade of RBJ-cookbook high-pass biquads at one corner, one
 * section per two poles, with the standard Butterworth Q values. ffmpeg's `highpass` is
 * exactly that biquad, and `width_type=q` takes the Q directly.
 */
const BUTTERWORTH_Q: Readonly<Record<number, readonly number[]>> = Object.freeze({
  2: [0.70710678118654752],
  4: [0.54119610014619698, 1.30656296487637652],
});

function highPassChain(builder: GraphBuilder, edl: EDL, label: string): string {
  const filter = edl.audio_plan?.ambient?.high_pass;
  if (!filter) return label;
  const sections = BUTTERWORTH_Q[filter.order];
  if (!sections) {
    fail(
      `AmbientPlan.high_pass declares order ${filter.order}, and the contract's Butterworth ` +
        "cascade is defined for 2 and 4.",
    );
  }
  const chain = sections
    .map((q) => `highpass=f=${filter.corner_hz}:poles=2:width_type=q:width=${q}`)
    .join(",");
  const next = `${label}_hp`;
  builder.note(
    "audio_plan.ambient.high_pass",
    `Butterworth order ${filter.order} at ${filter.corner_hz} Hz, built as ${sections.length} ` +
      `RBJ biquad section(s) at Q ${sections.join(", ")} — the values the contract's $comment ` +
      "names. Applied to the summed ambient group, before ducking.",
    "contracts#53 (closed)",
  );
  builder.add(`[${label}]${chain}[${next}]`);
  return next;
}

function mergeAudio(builder: GraphBuilder, labels: readonly string[], label: string): string {
  if (labels.length === 0) fail("no audio to merge.");
  if (labels.length === 1) {
    builder.add(`[${labels[0]}]anull[${label}]`);
    return label;
  }
  builder.add(
    `${labels.map((entry) => `[${entry}]`).join("")}amix=inputs=${labels.length}` +
      `:normalize=0:duration=longest:dropout_transition=0[${label}]`,
  );
  return label;
}

function buildAudioGraph(
  edl: EDL,
  program: Program,
  sources: ReadonlyMap<string, ResolvedSource>,
  baseIndex: number,
): { builder: GraphBuilder; label: string } {
  const mix = edl.audio_plan?.mix;
  if (!mix) fail("the program carries audio and the EDL declares no MixPlan.");
  const builder = new GraphBuilder(baseIndex);
  const sampleRate = mix.sample_rate ?? 48000;

  const byRole = new Map<string, string[]>();
  program.audio.forEach((contribution, index) => {
    const label = audioContributionChain(builder, edl.rate, mix, sources, contribution, index);
    const bucket = byRole.get(contribution.role) ?? [];
    bucket.push(label);
    byRole.set(contribution.role, bucket);
  });

  for (const window of program.ducking) {
    if (!byRole.has(window.target)) {
      fail(
        `ducking rule ${window.ruleId} turns down "${window.target}" and no track carries that ` +
          "role, so the rule states an intent about audio that does not exist.",
      );
    }
  }

  const groupLabels: string[] = [];
  for (const [role, labels] of [...byRole.entries()].sort(([left], [right]) => left.localeCompare(right))) {
    let merged = mergeAudio(builder, labels, `g_${role}`);
    if (role === "ambient") merged = highPassChain(builder, edl, merged);
    const windows = program.ducking.filter((window) => window.target === role);
    groupLabels.push(duckingChain(builder, edl.rate, windows, merged));
  }

  let mixed = mergeAudio(builder, groupLabels, "amixed");
  const masterGain = mix.master_gain_db ?? 0;
  if (masterGain !== 0) {
    builder.add(`[${mixed}]volume=${masterGain}dB[amaster]`);
    mixed = "amaster";
  }
  const totalSamples = framesToSamples(program.totalFrames, edl.rate, sampleRate);
  builder.add(`[${mixed}]apad=whole_len=${totalSamples},atrim=end_sample=${totalSamples},asetpts=PTS-STARTPTS[amix]`);
  return { builder, label: "amix" };
}

export function buildGraph(
  edl: EDL,
  program: Program,
  sources: ReadonlyMap<string, ResolvedSource>,
  profile: EncodeProfile,
): BuiltGraph {
  const video = new GraphBuilder(0);

  const videoLabels = program.video.map((segment, index) =>
    videoSegmentChain(video, edl, profile, sources, segment, index),
  );
  if (videoLabels.length === 1) {
    video.add(`[${videoLabels[0]}]null[vout]`);
  } else {
    video.add(`${videoLabels.map((label) => `[${label}]`).join("")}concat=n=${videoLabels.length}:v=1:a=0[vout]`);
  }

  if (program.audio.length === 0) {
    return {
      inputs: video.inputs,
      filter: video.chains.join(";\n"),
      videoLabel: "vout",
      audioLabel: null,
      audioOnly: null,
      interpretations: video.interpretations,
    };
  }

  const combined = buildAudioGraph(edl, program, sources, video.inputs.length);
  const standalone = buildAudioGraph(edl, program, sources, 0);

  return {
    inputs: [...video.inputs, ...combined.builder.inputs],
    filter: [...video.chains, ...combined.builder.chains].join(";\n"),
    videoLabel: "vout",
    audioLabel: combined.label,
    audioOnly: {
      inputs: standalone.builder.inputs,
      filter: standalone.builder.chains.join(";\n"),
      label: standalone.label,
    },
    interpretations: [...video.interpretations, ...combined.builder.interpretations],
  };
}
