import { extname } from "node:path";

import type { EDL, MixPlan, ReframeTrack } from "../../../contracts/codegen/generated/typescript/index.js";
import { RenderVideoError } from "./errors.js";
import type { EncodeProfile } from "./encode.js";
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

export type InputSeeking = "safe" | "disabled";

// Narrow on purpose. FFprobe reports one MOV-family demuxer alias for actual MP4,
// QuickTime and 3GP files, and a filename extension is not container evidence.
const PROVEN_MP4_MAJOR_BRANDS = new Set(["isom", "iso2", "mp41", "mp42", "avc1"]);

export function canSeekVideoInput(source: ResolvedSource, rate: number): boolean {
  const provenRate = rate === 30 || rate === 30_000 / 1_001;
  return (
    source.paths.length === 1 &&
    source.video?.codecName === "h264" &&
    source.formatName.split(",").includes("mp4") &&
    source.formatMajorBrand !== null &&
    PROVEN_MP4_MAJOR_BRANDS.has(source.formatMajorBrand) &&
    extname(source.paths[0]!).toLowerCase() === ".mp4" &&
    source.video.startTimeSeconds === 0 &&
    source.video.packetCadence !== null &&
    provenRate
  );
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

  addVideoInput(
    source: ResolvedSource,
    sourceOffset: number,
    rate: number,
    inputSeeking: InputSeeking,
  ): { index: number; trimStart: number } {
    const canSeek =
      inputSeeking === "safe" &&
      sourceOffset > 0 &&
      canSeekVideoInput(source, rate);
    if (!canSeek) return { index: this.addInput(source), trimStart: sourceOffset };

    const args = [...source.inputArgs];
    // `-r` rewrites input timestamps from frame zero. After `-ss` that makes the
    // keyframe preroll look like new timeline frames and defeats accurate seeking.
    // The source probe already proved the H.264 stream's CFR, so let the demuxer keep
    // its native timestamps on sought inputs.
    const declaredRate = args.indexOf("-r");
    if (declaredRate >= 0) args.splice(declaredRate, 2);
    const inputIndex = args.lastIndexOf("-i");
    if (inputIndex < 0) return { index: this.addInput(source), trimStart: sourceOffset };
    args.splice(
      inputIndex,
      0,
      "-accurate_seek",
      "-ss",
      secondsArg(framesToSeconds(sourceOffset, rate)),
    );
    this.inputs.push({ args, mediaRefId: source.mediaRefId });
    return { index: this.baseIndex + this.inputs.length - 1, trimStart: 0 };
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

function normaliseChain(target: EDL["target"], profile: EncodeProfile): string {
  const { width, height } = target.resolution;
  return `,scale=${width}:${height}:flags=${profile.scale_flags},setsar=1,format=${profile.video.pix_fmt}`;
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
  inputSeeking: InputSeeking,
): string {
  const label = `v${index}`;
  const rate = edl.rate;
  const reframeById = new Map((edl.reframe_tracks ?? []).map((track) => [track.reframe_track_id, track]));

  if (segment.kind === "gap") {
    const colour = segment.fill === "white" ? "white" : "black";
    builder.add(
      `color=c=${colour}:s=${edl.target.resolution.width}x${edl.target.resolution.height}:r=${rate}` +
        `,trim=end_frame=${segment.length},setpts=PTS-STARTPTS,setsar=1,format=${profile.video.pix_fmt}[${label}]`,
    );
    return label;
  }

  if (segment.kind === "clip") {
    const source = sources.get(segment.mediaRefId);
    if (!source) fail(`no resolved source for ${segment.mediaRefId}.`);
    const input = builder.addVideoInput(source, segment.sourceOffset, rate, inputSeeking);
    let chain = `[${input.index}:v]trim=start_frame=${input.trimStart}:end_frame=${input.trimStart + segment.length},setpts=PTS-STARTPTS`;
    if (segment.reframeTrackId) {
      const track = reframeById.get(segment.reframeTrackId);
      if (!track) fail(`reframe track ${segment.reframeTrackId} is not declared.`);
      chain += cropChain(builder, track, rate, segment.sourceAbsoluteStart, segment.length, source);
    }
    chain += normaliseChain(edl.target, profile);
    builder.add(`${chain}[${label}]`);
    return label;
  }

  const sides = [segment.from, segment.to] as const;
  const sideLabels: string[] = [];
  sides.forEach((side, position) => {
    const source = sources.get(side.mediaRefId);
    if (!source) fail(`no resolved source for ${side.mediaRefId}.`);
    const input = builder.addVideoInput(source, side.sourceOffset, rate, inputSeeking);
    const sideLabel = `x${index}_${position}`;
    let chain = `[${input.index}:v]trim=start_frame=${input.trimStart}:end_frame=${input.trimStart + segment.length},setpts=PTS-STARTPTS`;
    if (side.reframeTrackId) {
      const track = reframeById.get(side.reframeTrackId);
      if (!track) fail(`reframe track ${side.reframeTrackId} is not declared.`);
      chain += cropChain(builder, track, rate, side.sourceAbsoluteStart, segment.length, source);
    }
    chain += normaliseChain(edl.target, profile);
    builder.add(`${chain}[${sideLabel}]`);
    sideLabels.push(sideLabel);
  });

  builder.note(
    "transitions[].easing",
    "Only linear easing is executed; the blend is a straight ramp indexed by frame number " +
      "across the declared handle window, and any other easing value is refused rather than " +
      "approximated.",
    "contracts#52",
  );

  /**
   * The blend is driven by `blend`'s frame counter rather than by `xfade`'s seconds. Both
   * would work, but `xfade` needs a declared constant frame rate on its inputs, and `trim`
   * marks its output rate unknown by design — conforming it back with `fps` would put a
   * filter that is allowed to duplicate or drop a frame in the middle of a beat-locked
   * cut. Frame numbers cannot drift.
   */
  if (segment.transitionType === "dissolve") {
    const length = segment.length;
    builder.add(
      `[${sideLabels[0]}][${sideLabels[1]}]blend=all_expr='A*(1-N/${length})+B*(N/${length})'[${label}]`,
    );
    return label;
  }

  // A dip goes to a colour at the cut and comes back out of it, so it is two fades rather
  // than a blend, and `fade` knows what "black" and "white" mean in the working pixel
  // format — which an arithmetic blend against a constant would not.
  const colour = segment.transitionType === "dip_to_black" ? "black" : "white";
  const halves: string[] = [];
  if (segment.inOffset > 0) {
    const out = `${label}_out`;
    builder.add(
      `[${sideLabels[0]}]trim=end_frame=${segment.inOffset},setpts=PTS-STARTPTS` +
        `,fade=type=out:start_frame=0:nb_frames=${segment.inOffset}:color=${colour}[${out}]`,
    );
    halves.push(out);
  }
  if (segment.outOffset > 0) {
    const into = `${label}_in`;
    builder.add(
      `[${sideLabels[1]}]trim=start_frame=${segment.inOffset},setpts=PTS-STARTPTS` +
        `,fade=type=in:start_frame=0:nb_frames=${segment.outOffset}:color=${colour}[${into}]`,
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

function duckingChain(builder: GraphBuilder, rate: number, windows: readonly DuckingWindow[], label: string): string {
  if (windows.length === 0) return label;
  let current = label;
  windows.forEach((window, index) => {
    if (window.ranges.length === 0) return;
    const gain = decibelsToLinear(-window.reductionDb);
    const enable = window.ranges
      .map((range) => {
        const start = secondsArg(framesToSeconds(range.start, rate));
        const end = secondsArg(framesToSeconds(range.end, rate));
        return `between(t,${start},${end})`;
      })
      .join("+");
    const next = `${current}_d${index}`;
    builder.note(
      "audio_plan.ducking[].ranges",
      "A duck is a step of exactly reduction_db across the declared range. Its edges land on an " +
        "ffmpeg audio-frame boundary, so the transition is within one frame of the stated time; " +
        "attack and release are refused rather than approximated.",
      "contracts#54",
    );
    builder.add(`[${current}]volume=volume=${gain.toFixed(9)}:enable='${enable}'[${next}]`);
    current = next;
  });
  return current;
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
    const merged = mergeAudio(builder, labels, `g_${role}`);
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
  inputSeeking: InputSeeking = "safe",
): BuiltGraph {
  const video = new GraphBuilder(0);

  const videoLabels = program.video.map((segment, index) =>
    videoSegmentChain(video, edl, profile, sources, segment, index, inputSeeking),
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
