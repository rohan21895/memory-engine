// Integration glue (Claude): picked photos -> on-device model + face detection +
// image quality -> selection -> the review UI's data shape.
import { detectFaces, type FaceBox } from "./faces/face-detector";
import type { PickedPhoto } from "./import/picked-photo";
import { benchmarkInferenceModels, checkModelHealth, getModel } from "./ml";
import type { InferenceBenchmark, InferenceBenchmarks } from "./ml";
import { bodyPoseModelLoadStats, detectBodyPose } from "./ml/movenet";
import {
  analyzeSemanticImage,
  SEMANTIC_SCREENSHOT_THRESHOLD,
  semanticModelLoadStats,
} from "./ml/tinyclip";
import type { ModelCacheLoadStats, ModelLoadEvent } from "./ml/model-cache";
import type {
  ReviewAlternative,
  ReviewData,
  ReviewPoolItem,
  ReviewSelected,
} from "./review/mock-data";
import {
  CANDIDATE_PREPASS_THRESHOLD,
  candidateBudget,
  chooseHeavyAnalysisCandidates,
  type ProbedCandidate,
} from "./selection/candidate-prepass";
import {
  mapLimit,
  throwIfCancelled,
  yieldToEventLoop,
} from "./selection/concurrent-map";
import { watchAlbumBuildLifecycle } from "./selection/album-build-lifecycle";
import {
  formatJsThreadProfile,
  resetJsThreadProfile,
  startEventLoopLagSampler,
  type EventLoopLagReport,
} from "./selection/js-thread-profile";
import {
  ANALYSIS_PRIORITY,
  createAnalysisQueue,
  isPermutationOf,
  restoreInputOrder,
  type AnalysisJob,
} from "./selection/analysis-tiers";
import {
  loadCandidateProbeCache,
  probeCandidateWithCache,
  type CandidateProbeCache,
} from "./selection/candidate-probe-cache";
import {
  defaultDeepSignalStore,
  type DeepSignalRecord,
  type DeepSignalStore,
} from "./selection/deep-signal-store";
import {
  prepareCandidateAnalysisProxy,
  probeCandidateQuality,
  removeCandidateAnalysisProxy,
} from "./selection/candidate-quality-probe";
import {
  measureImageQuality,
  type MeasuredImageQuality,
  type NormalizedBox,
} from "./selection/image-quality";
import {
  DeepAnalysisTimingCollector,
  summarizeDurations,
  type DeepAnalysisDegradation,
  type DeepAnalysisMeasurement,
  type DeepAnalysisPhase,
} from "./selection/deep-analysis-timing";
import { clusterPoses, makePose } from "./selection/pose";
import { bodyCoverage } from "./selection/pose-framing";
import {
  captureNearDuplicateRankingLabels,
  preferenceAssetId,
} from "./selection/preference-label-store";
import {
  classifyCategory,
  isScreenshotOrDocument,
  type FaceSignal,
  type QualitySignals,
} from "./selection/quality-signals";
import { selectBestShotsWithObservations } from "./selection/select-best-shots";
import type { AlbumData } from "./selection/types";

// A face whose box sits within 1% of any border is treated as cut off.
const EDGE_FRACTION = 0.01;
/**
 * Photos analyzed at once.
 *
 * One, because the measured OOM is Java byte-array pressure AFTER the bounded
 * proxy was added, not the old full-resolution bitmap hypothesis:
 *
 * - the device attributed the OOM to `quality-decode` and ART reported a
 *   28,975,795-byte object allocation;
 * - ART's byte-array object size is its 12-byte header plus payload, leaving an
 *   exact 28,975,783-byte JPEG payload. That is not a bitmap, and it cannot be
 *   Android Base64's output either: NO_WRAP output has a multiple-of-four byte
 *   length, while this payload is 3 mod 4;
 * - source tracing (not a heap measurement) shows that each photo starts four
 *   Base64 preprocessing paths under one Promise.all: perceptual, quality,
 *   MoveNet, and TinyCLIP. The previous outer limit of six therefore allowed up
 *   to 24 Java-array pipelines to overlap.
 *
 * WHAT PRODUCES THE 27.63 MiB PAYLOAD IS STILL UNKNOWN. An earlier revision of
 * this comment named expo-image-manipulator's `byteOut.toByteArray()` in
 * `saveAsync` as the allocation. That call is real (ImageManipulatorModule.kt
 * line 127) and it is the right SHAPE of allocation, but it cannot be this
 * one, and the arithmetic that rules it out is the same arithmetic that found
 * it: the quality proxy is capped at ANALYSIS_PROXY_SIZE = 1280 px, so at most
 * 1.6 MP. No JPEG encoder emits 27.63 MiB from 1.6 MP at any quality -- that
 * is 9-18x more bytes than the bound permits, and a 27.63 MiB JPEG implies
 * roughly 15-29 MP. So the failed allocation comes from something that still
 * sees a full-size image, not from the proxy that was added to prevent exactly
 * that. A 27.63 MiB read of an ORIGINAL file is the obvious next suspect and
 * has NOT been checked. Do not treat #41's allocation as identified.
 *
 * The `quality-decode` label is not evidence of the culprit either, and this is
 * why the whole attribution came apart. `measureImageQuality` is only ever
 * called on `analysisUri` -- the proxy -- and it resamples to
 * QUALITY_SAMPLE_WIDTH = 512, so the path that phase names is the most tightly
 * bounded one in the build. What the label actually records is which timing
 * bucket was OPEN on one photo's timeline, and at concurrency six, with four
 * coroutine-dispatched paths per photo, up to 24 allocations from other photos
 * were in flight against the same heap. The bucket names the bystander, not the
 * allocator.
 *
 * Serializing at the photo boundary reduces the source-derived maximum from 24
 * pipelines to four. Keep it at one until the payload above is explained: if a
 * single photo really can allocate 27.63 MiB plus its ~38.6 MiB Base64 copy,
 * that is ~66 MiB transient against a 192-256 MB heap, and two photos would
 * likely be fatal. This is a stopgap that bounds the blast radius, NOT a fix --
 * the fix is to stop producing a 27.63 MiB payload at all.
 *
 * It also buys the diagnosis. One photo at a time means one photo's allocations
 * in flight, so the NEXT OOM's phase label finally names the path that
 * allocated instead of whichever bucket happened to be open. Read the label
 * from a concurrency-one run before believing any successor to this comment.
 *
 * The cost is real and unmeasured. `saveAsync` is declared
 * `AsyncFunction(...) Coroutine`, so it runs on a coroutine dispatcher rather
 * than the single expo AsyncFunctionQueue -- these calls genuinely did run in
 * parallel, and going 6 -> 1 genuinely serializes them. Measure album build on
 * the phone before assuming this is cheap; do not infer it from the old
 * six-photo trace.
 */
const ANALYZE_CONCURRENCY = 1;

/**
 * M2: reuse Tier-B signals across album builds instead of recomputing them.
 *
 * OFF, so the shipped path is byte-for-byte today's. What flipping it changes,
 * measured rather than assumed:
 *
 *  - A candidate whose record is already durable skips the proxy and all five
 *    models. That is the 2.33 s/photo the deep stage costs, so a build whose
 *    64 candidates are all cached loses ~148 s of its ~207 s.
 *  - A candidate with no record costs exactly what it costs today, plus one
 *    3.9 KB encode. Nothing here makes the first analysis of a photograph
 *    cheaper — see `docs/DEEP-ANALYSIS-TIMING.md`, where 98% of the stage is a
 *    span that already includes JS-thread contention. This converts a REPEATED
 *    cost into a once-per-photograph one; only M3 attacks the once.
 *  - The stored values are float32, drifting a vector component by at most
 *    2.7e-8. `deep-signal-parity.test.ts` is the standing gate that this moves
 *    none of the pinned albums, and it uses the rejected int8 encoding — which
 *    DOES move one — as its sabotage.
 *
 * What is still unmeasured, and what should decide the flip: the hit rate on a
 * real library. A repeat of the same filter hits everything; a different filter
 * re-ranks the cheap probes and can pick a largely different 64.
 * `deep-signal-store.benchmark.ts` reports that overlap for nested and
 * disjoint filters, but only against synthetic corpora.
 */
const USE_DEEP_SIGNAL_CACHE = false;

/**
 * Who recurs across the whole library, for the candidate cap to protect.
 *
 * Library-wide on purpose. Judged within one album a wedding guest and a
 * grandparent both appear on a single day, so the distinction this exists to
 * draw would vanish exactly where it is needed.
 *
 * Imported lazily, matching how this file already reaches native-backed
 * modules: `face-index` and `photo-index` pull in expo-media-library at module
 * scope, which the offline test runner cannot load. Any failure degrades to
 * "nobody is protected", which is the previous behaviour rather than a broken
 * album.
 */
async function familiarPersonPredicate(): Promise<
  ((personId: string) => boolean) | undefined
> {
  try {
    const [{ getPeople }, { monthIdForAsset }, { buildPersonRecurrence, monthStartMs }] =
      await Promise.all([
        import("./faces/face-index"),
        import("./import/photo-index"),
        import("./faces/person-recurrence"),
      ]);
    const people = getPeople();
    if (people.length === 0) return undefined;
    const recurrence = buildPersonRecurrence(people, (assetId) =>
      monthStartMs(monthIdForAsset(assetId)),
    );
    return (personId: string) => recurrence.isFamiliar(personId);
  } catch {
    return undefined;
  }
}
// The 32 px platform thumbnail is substantially smaller than any model input. A little
// extra concurrency keeps large library screening I/O-bound without allowing
// hundreds of image-manipulator operations to accumulate.
const PREPASS_CONCURRENCY = 32;
const MAX_PREPASS_PROGRESS_UPDATES = 200;
const PREPASS_YIELD_ITEMS = 32;
const PREPASS_CHECKPOINT_ITEMS = 128;
const ANALYSIS_YIELD_ITEMS = 4;
/**
 * How much one deep-analysis photo costs relative to one prepass photo, used
 * only to weight the progress bar.
 *
 * Counting both stages as one unit each made the bar lie badly on a large
 * library: 11,793 prepass units against 64 analysis units puts the bar at 99.5%
 * before the expensive stage has begun, so it appears to hang for the last
 * fifth of the build. A prepass item reads a 32px platform thumbnail and hashes
 * it; an analysis item renders a bounded proxy and runs perceptual, face,
 * quality, MoveNet, and TinyCLIP analysis over it.
 * They are at least an order of magnitude apart.
 *
 * This is a calibration knob, not a measurement — the phase text carries the
 * real counts, so a wrong value here only mis-shapes the bar. Tune it against a
 * stopwatch on the beta device.
 */
const ANALYSIS_WORK_UNITS = 20;

export type BuildAlbumProgress = {
  done: number;
  total: number;
  phase: string;
};

export type BuildAlbumTiming = {
  stage:
    | "cache-load"
    | "candidate-probe"
    | "candidate-rank"
    | "model-ready-wait"
    | "deep-signal-load"
    | "deep-signal-store"
    | "deep-analysis"
    | "deep-analysis-phase"
    | "analysis-degraded"
    | "model-load"
    | "pose-and-enrich"
    | "choose-best-shots"
    | "review-assembly"
    | "total";
  elapsedMs: number;
  itemCount: number;
  cacheHits?: number;
  phase?: DeepAnalysisPhase | "concurrent-model-group";
  model?: "movenet" | "tinyclip";
  measurement?:
    | DeepAnalysisMeasurement
    | "model-cold-load"
    | "model-reload";
  meanMs?: number;
  p50Ms?: number;
  p95Ms?: number;
  reloadCount?: number;
  /** `analysis-degraded` only: photos that lost at least one signal. */
  degradedPhotos?: number;
  /** `analysis-degraded` only: every phase, including the ones that lost none. */
  degraded?: readonly DeepAnalysisDegradation[];
  /** `deep-signal-store` only: what the Tier-B cache did this build. */
  deepSignals?: DeepSignalReport;
};

/** Everything needed to say how much inference the store avoided, and its cost. */
export type DeepSignalReport = {
  hits: number;
  misses: number;
  writes: number;
  refusedWrites: number;
  shardsLoaded: number;
  recordsLoaded: number;
  shardsEvicted: number;
  bytes: number;
  /** Candidates still without a durable record when the build finished. */
  stillPending: number;
};

// -expect-error TypeScript bundler resolution normally omits source extensions.
import type { AlbumBuildPreferences } from "./selection/album-build-preferences.ts";

export type BuildAlbumOptions = {
  signal?: AbortSignal;
  onProgress?: (progress: BuildAlbumProgress) => void;
  /** Always-on console trace also emits through here for device benchmarks. */
  onTiming?: (timing: BuildAlbumTiming) => void;
  /** Overrides `USE_DEEP_SIGNAL_CACHE` for benchmarks and A/B runs. */
  deepSignalCache?: boolean;
  /**
   * What the user answered before the build: how many photos, and who the album
   * is for.
   *
   * The single channel between the setup questionnaire and the selector. The
   * planner's priority gate and caps existed and were tested long before this
   * field did, and were unreachable the whole time -- there was no argument to
   * carry an answer from the app, so every album was planned as though the
   * question had never been asked. Omitting it still means exactly that.
   */
  preferences?: AlbumBuildPreferences;
};

/**
 * Which on-device graphs actually loaded. Re-exported here so a debug
 * affordance can report model health without reaching into `src/ml`. Idempotent
 * and never throws; `buildAlbum()` already calls it once per session.
 */
export { checkModelHealth, type ModelProbe } from "./ml";
export { AlbumBuildCancelledError } from "./selection/concurrent-map";

function formatCount(value: number): string {
  return Math.max(0, Math.floor(value)).toLocaleString();
}

function lookingAtPhase(done: number, total: number): string {
  return `Looking at ${formatCount(done)} of ${formatCount(total)} photos`;
}

function cappedAnalysisPhase(
  done: number,
  candidateTotal: number,
  sourceTotal: number,
): string {
  if (done === 0) {
    return `Looking at the best ${formatCount(candidateTotal)} of ${formatCount(sourceTotal)} photos`;
  }
  return `Looking at ${formatCount(done)} of the best ${formatCount(candidateTotal)} photos (from ${formatCount(sourceTotal)})`;
}

function emitProgress(
  onProgress: BuildAlbumOptions["onProgress"],
  progress: BuildAlbumProgress,
): void {
  try {
    onProgress?.(progress);
  } catch {
    // UI reporting must never be able to fail the on-device album job.
  }
}

function shouldReportPrepass(done: number, total: number): boolean {
  const step = Math.max(1, Math.ceil(total / MAX_PREPASS_PROGRESS_UPDATES));
  return done === 1 || done === total || done % step === 0;
}

function reportTiming(
  options: BuildAlbumOptions,
  timings: BuildAlbumTiming[],
  timing: BuildAlbumTiming,
): void {
  timings.push(timing);
  options.onTiming?.(timing);
}

/**
 * `analysis-degraded={photos:3/64,proxy-create:0,...,tinyclip:1,oom:1}`
 *
 * Every phase is printed, zeros included: a build that degraded nothing has to
 * SAY it degraded nothing, or the next person reading a log cannot tell a clean
 * pass from a counter that never ran.
 *
 * `photos` is the number that matters to a user — how many frames the planner
 * judged on less evidence than it should have. The phase totals can sum above
 * it because one failure often takes several phases of the same photo.
 */
function formatDegradation(timing: BuildAlbumTiming): string {
  const phases = (timing.degraded ?? [])
    .map((entry) => `,${entry.phase}:${entry.count}`)
    .join("");
  const outOfMemory = (timing.degraded ?? []).reduce(
    (total, entry) => total + entry.outOfMemory,
    0,
  );
  return (
    `analysis-degraded={photos:${timing.degradedPhotos ?? 0}/${timing.itemCount}` +
    `${phases},oom:${outOfMemory}}`
  );
}

/**
 * Whether the pose cap actually stopped one person recurring in one pose.
 *
 * That is the product goal for pose detection, and nothing has ever checked it
 * on a real library. It cannot be checked off-device: the synthetic fixtures
 * label pose per moment from a four-word vocabulary, so three or four distinct
 * poses have to cover a 24-photo album and `maxPerBodyPose` is FORCED to relax.
 * Any "over the cap" count measured there describes the fixture.
 *
 * `capacity` is printed for exactly that reason. When `distinctPoses x cap` is
 * below the album size the cap could not have held whatever the planner did, so
 * `worstBucket` says nothing about the planner and the line marks itself
 * unmeasurable rather than inviting the wrong conclusion.
 *
 * `noPoseOrIdentity` is the other half, and it is the one this library is
 * likely to fail on. A photo whose pose MoveNet could not read, or whose fitted
 * body cannot be assigned to a known identity, is keyed
 * `nopose:<mediaId>`, unique to itself and never capped.
 *
 * Reporting only: it observes a finished decision and changes nothing.
 */
function reportPoseDiversity(
  enriched: readonly { id: string; poseCluster?: string; personIds?: readonly string[] }[],
  album: AlbumData,
): void {
  try {
    const byId = new Map(enriched.map((photo) => [photo.id, photo]));
    const chosen = album.selected
      .map((selected) => byId.get(selected.media_id))
      .filter((photo): photo is (typeof enriched)[number] => photo !== undefined);
    // A single-person pose has no safe owner without a known identity. For a
    // group, retain the exact sorted identity set: MoveNet fits only one body,
    // and guessing which face it followed would make this diagnostic claim a
    // deduplication the planner deliberately refuses to make.
    const readable = chosen.filter(
      (photo) => photo.poseCluster && (photo.personIds?.length ?? 0) > 0,
    );
    const buckets = new Map<string, number>();
    for (const photo of readable) {
      const people = [...new Set(photo.personIds ?? [])].sort();
      const key = `${JSON.stringify(people)}|${photo.poseCluster}`;
      buckets.set(key, (buckets.get(key) ?? 0) + 1);
    }
    const repeats = [...buckets.values()].filter((count) => count > 1).length;
    const capacity = buckets.size * POSE_DIVERSITY_CAP;
    console.info(
      `[PhoteoAlbumPoseDiversity] chosen=${chosen.length} noPoseOrIdentity=${chosen.length - readable.length} ` +
        `distinctPoses=${buckets.size} capacity=${capacity} ` +
        `worstBucket=${buckets.size > 0 ? Math.max(...buckets.values()) : 0} ` +
        `samePersonSamePose=${repeats}` +
        (capacity < chosen.length
          ? ' note="capacity below album size, so the cap had to relax; worstBucket is unmeasurable here"'
          : ""),
    );
  } catch {
    // Diagnostics must never fail an album the user is waiting for.
  }
}

/** Mirrors the planner's `maxPerBodyPose` default, for the diagnostic above. */
const POSE_DIVERSITY_CAP = 2;

/**
 * `[PhoteoAlbumRuntime]` — the line that decides whether M3 is a runtime problem.
 *
 * Three numbers, and the argument only works with all three:
 *
 * - `bench` is each graph's invoke cost with the JS thread quiet. It is the
 *   control. `model-inference` is `Date.now()` around an `await` that resolves
 *   ON the JS thread, so its 2,280 ms could be a slow kernel or a busy thread
 *   and no amount of re-reading the old logs can separate them.
 * - `cpu:<x>ms/<wall>ms` is JS-thread CPU inside measured synchronous blocks
 *   against the stage's own wall clock. These DO sum — the thread is single and
 *   the blocks cannot yield — so a share near 100% means the pass is JS-bound
 *   and outer photo concurrency is buying nothing but peak memory.
 * - `blocked` is how much of the stage a trivial timer could not run, i.e. how
 *   long a resolution arriving from the Nitro thread pool waits to be seen.
 *
 * How to read the result:
 *   bench ~2,000 ms  -> the runtime is genuinely that slow; delegate/thread
 *                       work is justified and the JS thread is a side issue.
 *   bench ~100-300 ms and blocked > 80% -> `model-inference` was measuring the
 *                       thread. Move the pixel work or lower the concurrency;
 *                       a native module would change nothing.
 *   bench ~100-300 ms and blocked < 30% -> neither; look at the queue and at
 *                       what happens between the spans.
 *
 * Reporting only. Never throws.
 */
function reportRuntimeProfile(
  stageWallMs: number,
  photoCount: number,
  bench: InferenceBenchmarks,
  stopLagSampler: () => EventLoopLagReport,
): void {
  try {
    const lag = stopLagSampler();
    const describe = (
      name: string,
      measured: InferenceBenchmark | undefined,
    ): string =>
      measured
        ? `${name}:${formatMilliseconds(measured.meanMs)}` +
          `(${measured.runs}x,${formatMilliseconds(measured.minMs)}..${formatMilliseconds(measured.maxMs)})`
        : `${name}:unavailable`;
    console.info(
      `[PhoteoAlbumRuntime] photos=${photoCount} ` +
        `idle-bench={${describe("tinyclip", bench.tinyclip)},${describe("movenet", bench.movenet)}} ` +
        formatJsThreadProfile(stageWallMs, lag),
    );
  } catch {
    // A diagnostic must never fail the album it is diagnosing.
  }
}

/**
 * `deep-signal-store={hits:64/64,writes:0,pending:0,shards:2,rows:1042,disk:4.1MB,evicted:0}`
 *
 * `hits` over the candidate count is the number that matters: it is how many
 * photographs did NOT pay 2.33 s of deep analysis. `pending` is its honest
 * other half — candidates that finished the build still without a durable
 * record, because their analysis degraded and a degraded record must never be
 * cached. `refused` should always be zero; it counts writes aimed at a shard
 * the build never read, which would have overwritten a month.
 */
function formatDeepSignals(timing: BuildAlbumTiming): string {
  const report = timing.deepSignals;
  if (!report) return "deep-signal-store={off}";
  return (
    `deep-signal-store={hits:${report.hits}/${timing.itemCount},` +
    `writes:${report.writes},pending:${report.stillPending},` +
    `shards:${report.shardsLoaded},rows:${report.recordsLoaded},` +
    `disk:${(report.bytes / (1024 * 1024)).toFixed(1)}MB,` +
    `evicted:${report.shardsEvicted},refused:${report.refusedWrites}}`
  );
}

function formatTiming(timing: BuildAlbumTiming): string {
  if (timing.stage === "analysis-degraded") {
    return formatDegradation(timing);
  }
  if (timing.stage === "deep-signal-store") {
    return formatDeepSignals(timing);
  }
  if (timing.measurement) {
    const subject = timing.model ?? timing.phase ?? timing.stage;
    return `${subject}.${timing.measurement}={` +
      `count:${timing.itemCount},total:${timing.elapsedMs}ms,` +
      `mean:${formatMilliseconds(timing.meanMs)},` +
      `p50:${formatMilliseconds(timing.p50Ms)},` +
      `p95:${formatMilliseconds(timing.p95Ms)}` +
      (timing.reloadCount === undefined
        ? ""
        : `,reloads:${timing.reloadCount}`) +
      "}";
  }
  return `${timing.stage}=${timing.elapsedMs}ms/${timing.itemCount}` +
    (timing.cacheHits === undefined ? "" : `/hits:${timing.cacheHits}`);
}

function formatMilliseconds(value: number | undefined): string {
  if (value === undefined) return "0ms";
  return `${Math.round(value * 10) / 10}ms`;
}

type AnalysisModelLoadSnapshot = {
  movenet: ModelCacheLoadStats;
  tinyclip: ModelCacheLoadStats;
};

function analysisModelLoadSnapshot(): AnalysisModelLoadSnapshot {
  return {
    movenet: bodyPoseModelLoadStats(),
    tinyclip: semanticModelLoadStats(),
  };
}

function modelLoadsSince(
  before: ModelCacheLoadStats,
  after: ModelCacheLoadStats,
): ModelLoadEvent[] {
  return after.recent.filter((event) => event.sequence > before.sequence);
}

function reportModelLoads(
  options: BuildAlbumOptions,
  timings: BuildAlbumTiming[],
  before: AnalysisModelLoadSnapshot,
  after: AnalysisModelLoadSnapshot,
): void {
  for (const model of ["movenet", "tinyclip"] as const) {
    const events = modelLoadsSince(before[model], after[model]);
    for (const kind of ["cold", "reload"] as const) {
      const matching = events.filter((event) => event.kind === kind);
      const stats = summarizeDurations(matching.map((event) => event.elapsedMs));
      reportTiming(options, timings, {
        stage: "model-load",
        model,
        measurement: kind === "cold" ? "model-cold-load" : "model-reload",
        elapsedMs: stats.totalMs,
        itemCount: matching.length,
        meanMs: stats.meanMs,
        p50Ms: stats.p50Ms,
        p95Ms: stats.p95Ms,
        reloadCount: kind === "reload" ? matching.length : undefined,
      });
    }
  }
}

/**
 * Pick the largest usable detected face and map its proxy-pixel box to the
 * normalized coordinates expected by `measureImageQuality`.
 *
 * Returning undefined is intentional: a photo without a reliable face region
 * must not acquire face-quality gates from whole-frame measurements.
 */
function dominantFaceSubjectBox(
  boxes: readonly FaceBox[],
  imageWidth: number | undefined,
  imageHeight: number | undefined,
): NormalizedBox | undefined {
  if (
    imageWidth === undefined ||
    imageHeight === undefined ||
    !Number.isFinite(imageWidth) ||
    !Number.isFinite(imageHeight) ||
    imageWidth <= 0 ||
    imageHeight <= 0
  ) {
    return undefined;
  }

  let dominant:
    | { x: number; y: number; width: number; height: number; area: number }
    | undefined;

  for (const box of boxes) {
    if (
      ![box.x, box.y, box.width, box.height].every(Number.isFinite) ||
      box.width <= 0 ||
      box.height <= 0
    ) {
      continue;
    }
    const x0 = Math.max(0, Math.min(imageWidth, box.x));
    const y0 = Math.max(0, Math.min(imageHeight, box.y));
    const x1 = Math.max(x0, Math.min(imageWidth, box.x + box.width));
    const y1 = Math.max(y0, Math.min(imageHeight, box.y + box.height));
    const area = (x1 - x0) * (y1 - y0);
    if (area > 0 && (dominant === undefined || area > dominant.area)) {
      dominant = { x: x0, y: y0, width: x1 - x0, height: y1 - y0, area };
    }
  }

  return dominant
    ? {
        x: dominant.x / imageWidth,
        y: dominant.y / imageHeight,
        width: dominant.width / imageWidth,
        height: dominant.height / imageHeight,
      }
    : undefined;
}

/**
 * Rebuild the deep-analysis result from a durable Tier-B record.
 *
 * The shape returned here must match the live path's exactly, field for field,
 * because `enriched` cannot tell them apart. Two of those fields are NOT stored
 * and are re-derived: `makePose` and `bodyCoverage` are pure functions of the
 * MoveNet keypoints, costing microseconds, and keeping them out of the record
 * means an edit to `pose.ts` or `pose-framing.ts` never invalidates an hour of
 * inference.
 *
 * `bodyCoverage` is handed the record's own analysis dimensions. Those are the
 * proxy's, which is what the keypoints were letterboxed against — passing the
 * original photo's would invert every in-frame test on a non-square photo.
 */
function replayDeepSignals(photo: PickedPhoto, record: DeepSignalRecord) {
  const analysisWidth = record.analysisWidth ?? photo.width;
  const analysisHeight = record.analysisHeight ?? photo.height;
  return {
    photo,
    result: record.perceptual,
    boxes: record.boxes,
    quality: record.quality,
    pose: record.pose
      ? makePose(record.pose.keypoints, record.pose.scores)
      : undefined,
    coverage:
      record.pose && analysisWidth !== undefined && analysisHeight !== undefined
        ? bodyCoverage(
            record.pose.keypoints,
            record.pose.scores,
            analysisWidth,
            analysisHeight,
          )
        : undefined,
    semantic: record.semantic,
    analysisWidth,
    analysisHeight,
  };
}

/**
 * The hard 0.92 take-collapse threshold is for visual near-copies, not shared
 * semantics. Prefer the phone's perceptual fingerprint when it exists; capped
 * builds skip that model, so TinyCLIP remains a useful fail-open fallback.
 */
function embeddingForNearDuplicateGrouping(
  perceptualEmbedding: number[],
  semanticEmbedding: number[] | undefined,
): number[] {
  const hasPerceptualEmbedding =
    perceptualEmbedding.length > 0 &&
    perceptualEmbedding.every((value) => Number.isFinite(value));
  return hasPerceptualEmbedding
    ? perceptualEmbedding
    : semanticEmbedding ?? [];
}

// Assemble the selection quality contract from a photo + its detected faces +
// measured pixel quality. All inputs are best-effort; missing data stays neutral.
function analyzePhoto(
  photo: PickedPhoto,
  boxes: FaceBox[],
  quality: MeasuredImageQuality,
  analysisWidth = photo.width,
  analysisHeight = photo.height,
): QualitySignals {
  const width = analysisWidth;
  const height = analysisHeight;
  const haveDims =
    typeof width === "number" &&
    typeof height === "number" &&
    width > 0 &&
    height > 0;
  const imageArea = haveDims ? width * height : 0;
  const edgeX = haveDims ? width * EDGE_FRACTION : 0;
  const edgeY = haveDims ? height * EDGE_FRACTION : 0;

  const faces: FaceSignal[] = haveDims
    ? boxes.map((box) => {
        const eyes = [box.leftEyeOpen, box.rightEyeOpen].filter(
          (value): value is number => typeof value === "number",
        );
        return {
          areaRatio: imageArea > 0 ? (box.width * box.height) / imageArea : 0,
          eyesOpen: eyes.length > 0 ? Math.min(...eyes) : undefined,
          smile: typeof box.smiling === "number" ? box.smiling : undefined,
          cutAtEdge:
            box.x <= edgeX ||
            box.y <= edgeY ||
            box.x + box.width >= width - edgeX ||
            box.y + box.height >= height - edgeY,
        };
      })
    : [];

  const largestFaceAreaRatio = faces.reduce(
    (max, face) => Math.max(max, face.areaRatio),
    0,
  );

  return {
    // Preserve additive subject-region fields from measureImageQuality for the
    // planner bridge. Whole-frame fields retain their existing scoring role;
    // they are never substituted for an absent subject-region field.
    ...quality,
    sharpness: quality.sharpness,
    exposure: quality.exposure,
    clippedFraction: quality.clippedFraction,
    faces,
    faceCount: faces.length,
    largestFaceAreaRatio,
    anyFaceCutAtEdge: faces.some((face) => face.cutAtEdge),
    isScreenshotOrDocument: isScreenshotOrDocument({
      filename: photo.filename,
      width: photo.width,
      height: photo.height,
    }),
    category: classifyCategory(faces.length, largestFaceAreaRatio),
  };
}

/**
 * Build the review album from imported photos, entirely on-device:
 *  1. run the on-device model per photo (stub today; real SigLIP/YuNet via CL-1)
 *     to get a face count the ranker can use,
 *  2. rank into a best-shots AlbumData (placeholder engine; real TS port is CL-2),
 *  3. join with each photo's uri so the review UI can render it.
 */
export async function buildAlbum(
  photos: PickedPhoto[],
  count = 24,
  options: BuildAlbumOptions = {},
): Promise<ReviewData> {
  const totalStartedAt = Date.now();
  const timings: BuildAlbumTiming[] = [];
  // `processPhotos` has just navigated to BuildingScreen. A macrotask boundary
  // lets React commit that screen before any cache parsing or native image work
  // begins, so the confirm button can never look frozen.
  await yieldToEventLoop();
  throwIfCancelled(options.signal);
  const capEngaged = photos.length > CANDIDATE_PREPASS_THRESHOLD;
  const cacheStartedAt = Date.now();
  const probeCache = capEngaged ? await loadCandidateProbeCache() : undefined;
  if (probeCache) {
    reportTiming(options, timings, {
      stage: "cache-load",
      elapsedMs: Date.now() - cacheStartedAt,
      itemCount: photos.length,
    });
  }
  // Opened here rather than in the impl so the SAME checkpoint rule covers both
  // tiers: backgrounding and cancellation each commit whatever finished. No
  // per-N checkpoint, unlike the probe cache -- a deep shard is two megabytes
  // and the deep pass is only 64 items, so mid-pass rewrites would cost more
  // than the work they protect.
  const deepStore = (options.deepSignalCache ?? USE_DEEP_SIGNAL_CACHE)
    ? await defaultDeepSignalStore()
    : undefined;
  const lifecycle = await watchAlbumBuildLifecycle(options.signal, async () => {
    await probeCache?.persist();
    await deepStore?.persist();
  });
  try {
    return await buildAlbumImpl(
      photos,
      count,
      options,
      timings,
      probeCache,
      deepStore,
      lifecycle.waitUntilForeground,
    );
  } finally {
    await probeCache?.persist();
    await deepStore?.persist();
    lifecycle.dispose();
    const total: BuildAlbumTiming = {
      stage: "total",
      elapsedMs: Date.now() - totalStartedAt,
      itemCount: photos.length,
    };
    reportTiming(options, timings, total);
    const hasDeepAnalysisBreakdown = timings.some(
      (timing) => timing.stage === "deep-analysis-phase",
    );
    console.info(
      `[PhoteoAlbumBuildTiming] ${timings
        .map(formatTiming)
        .join(" ")}` +
        (hasDeepAnalysisBreakdown
          ? ' analysis-note="awaited phases overlap and include queue wait; model-inference excludes preprocessing/queue/load; concurrent-model-group.concurrent-wall is per-photo Promise.all wall time; do not sum awaited phases"'
          : ""),
    );
  }
}

async function buildAlbumImpl(
  photos: PickedPhoto[],
  count: number,
  options: BuildAlbumOptions,
  timings: BuildAlbumTiming[],
  probeCache: CandidateProbeCache | undefined,
  deepStore: DeepSignalStore | undefined,
  waitUntilForeground: () => Promise<void>,
): Promise<ReviewData> {
  throwIfCancelled(options.signal);
  const model = getModel();
  const modelLoadsBefore = analysisModelLoadSnapshot();
  // Started here so it overlaps the prepass, awaited before the heavy pass.
  // Every build then leaves one "[photeo-models] ..." line naming the graphs
  // that actually loaded, and each wrapper knows up front whether its graph is
  // usable instead of preprocessing every photo for an answer it cannot give.
  const modelHealth = checkModelHealth();
  const capEngaged = photos.length > CANDIDATE_PREPASS_THRESHOLD;
  // The budget the album asks for, clamped by what the deep stage can afford at
  // its measured per-photo price. 64 today; it rises on its own when M2/M3 make
  // a candidate cheaper. See `candidateBudget`.
  const budget = candidateBudget(count);
  const expectedCandidateCount = capEngaged
    ? Math.min(budget, photos.length)
    : photos.length;
  // Progress is measured in work units, not photos, so the two stages are
  // weighted by roughly what they cost. The trailing unit is the planner.
  const analysisWork = expectedCandidateCount * ANALYSIS_WORK_UNITS;
  const prepassWork = capEngaged ? photos.length : 0;
  const totalWork = prepassWork + analysisWork + 1;

  let analysisInputs: Array<{
    photo: PickedPhoto;
    quality?: ProbedCandidate["quality"];
  }>;
  let completedWork = 0;

  if (capEngaged) {
    const probeStartedAt = Date.now();
    let cacheHits = 0;
    emitProgress(options.onProgress, {
      done: 0,
      total: totalWork,
      phase: lookingAtPhase(0, photos.length),
    });
    const probed = await mapLimit(
      photos,
      PREPASS_CONCURRENCY,
      async (photo): Promise<ProbedCandidate> => {
        const result = await probeCandidateWithCache(
          photo,
          probeCache,
          probeCandidateQuality,
        );
        if (result.cacheHit) cacheHits += 1;
        return { photo, quality: result.quality };
      },
      {
        signal: options.signal,
        waitUntilRunnable: waitUntilForeground,
        yieldEvery: PREPASS_YIELD_ITEMS,
        checkpointEvery: PREPASS_CHECKPOINT_ITEMS,
        onCheckpoint: async () => probeCache?.persist(),
        onComplete: (done) => {
          completedWork = done;
          if (shouldReportPrepass(done, photos.length)) {
            emitProgress(options.onProgress, {
              done: completedWork,
              total: totalWork,
              phase: lookingAtPhase(done, photos.length),
            });
          }
        },
      },
    );
    reportTiming(options, timings, {
      stage: "candidate-probe",
      elapsedMs: Date.now() - probeStartedAt,
      itemCount: photos.length,
      cacheHits,
    });
    throwIfCancelled(options.signal);
    // The cap is where an album silently loses people: nothing downstream can
    // recover a photo that never reached heavy analysis, including the
    // planner's own per-person floor. Recurrence decides who is worth a
    // protected seat -- somebody who turns up across separate occasions rather
    // than somebody who was merely also at one event.
    const rankStartedAt = Date.now();
    analysisInputs = chooseHeavyAnalysisCandidates(
      probed,
      budget,
      { isFamiliar: await familiarPersonPredicate() },
    ).map(({ photo, quality }) => ({ photo, quality }));
    reportTiming(options, timings, {
      stage: "candidate-rank",
      elapsedMs: Date.now() - rankStartedAt,
      itemCount: probed.length,
    });
    console.info(
      `[PhoteoAlbumBuild] Candidate cap engaged: analyzing the best ${analysisInputs.length} of ${photos.length} photos.`,
    );
    emitProgress(options.onProgress, {
      done: completedWork,
      total: totalWork,
      phase: cappedAnalysisPhase(0, analysisInputs.length, photos.length),
    });
  } else {
    analysisInputs = photos.map((photo) => ({ photo }));
    emitProgress(options.onProgress, {
      done: 0,
      total: totalWork,
      phase: lookingAtPhase(0, photos.length),
    });
  }

  const modelWaitStartedAt = Date.now();
  await modelHealth;
  reportTiming(options, timings, {
    stage: "model-ready-wait",
    elapsedMs: Date.now() - modelWaitStartedAt,
    itemCount: 1,
  });
  // The control for every span the deep-analysis pass is about to report.
  // Runs here on purpose: the models are loaded, the prepass is finished, and
  // nothing else is in flight, so this is the only moment in a build when the
  // JS thread is quiet enough to measure a graph rather than a queue.
  throwIfCancelled(options.signal);
  const inferenceBench = await benchmarkInferenceModels();

  // Tier B, read side. Only now is the candidate set known, so this is the
  // earliest point at which the store can be told which months to open -- and
  // opening only those is the reason the store is sharded at all.
  if (deepStore) {
    const loadStartedAt = Date.now();
    await deepStore.load(analysisInputs.map(({ photo }) => photo));
    reportTiming(options, timings, {
      stage: "deep-signal-load",
      elapsedMs: Date.now() - loadStartedAt,
      itemCount: deepStore.stats().shardsLoaded,
    });
    throwIfCancelled(options.signal);
  }

  // The queue decides the ORDER, never the membership: every candidate is
  // analysed either way, and `mapLimit` preserves the order of the array it is
  // given, so the results are scattered back into candidate order before
  // anything downstream sees them. Selection cannot move because of this.
  const analysisQueue = createAnalysisQueue();
  analysisQueue.enqueue(
    analysisInputs.map(({ photo }): AnalysisJob => ({
      photoId: photo.id,
      capturedAt: photo.creationTime,
      priority: ANALYSIS_PRIORITY.userCandidate,
    })),
  );
  const leased = analysisQueue.lease(analysisInputs.length);
  const inputIndexById = new Map(
    analysisInputs.map(({ photo }, index) => [photo.id, index]),
  );
  // A picker that returned the same asset twice would collapse in the queue and
  // silently drop a candidate. Checked as a permutation, before any photograph
  // is decoded, because this is the last moment a fallback exists: array order
  // gives an album one photo short of nothing at all.
  const queueOrder = leased.map((job) => inputIndexById.get(job.photoId) ?? -1);
  const analysisOrder =
    deepStore && isPermutationOf(queueOrder, analysisInputs.length)
      ? queueOrder
      : analysisInputs.map((_, index) => index);

  // Prepass costs are the prepass's, and so is the store load above; this
  // window is the heavy pass alone. Started last so the profile measures what
  // it claims to.
  resetJsThreadProfile();
  const stopLagSampler = startEventLoopLagSampler();
  const analysisStartedAt = Date.now();
  const deepAnalysisTiming = new DeepAnalysisTimingCollector();
  // Photos that lost at least one signal, counted once however many they lost.
  // The per-phase totals below can exceed this: one OOM tends to take several
  // phases of the SAME photo, and reporting only the phase sum would read as
  // several bad photos instead of one bad moment for the heap.
  let degradedPhotos = 0;
  const orderedAnalyses = await mapLimit(analysisOrder, ANALYZE_CONCURRENCY, async (
    inputIndex,
  ) => {
    const { photo, quality: probedQuality } = analysisInputs[inputIndex];
    const job: AnalysisJob = {
      photoId: photo.id,
      capturedAt: photo.creationTime,
      priority: ANALYSIS_PRIORITY.userCandidate,
    };
    throwIfCancelled(options.signal);
    // Tier B, already durable. This is the entire saving: no proxy, no
    // perceptual fingerprint, no ML Kit, no quality decode, no MoveNet, no
    // TinyCLIP -- the 2.33 s/photo stage becomes one JSON.parse of 3.9 KB plus
    // the pure-arithmetic re-derivation of pose and body coverage.
    const cached = deepStore?.get(photo);
    if (cached) {
      analysisQueue.commit(job);
      return replayDeepSignals(photo, cached);
    }
    let photoDegraded = false;
    const markPhotoDegraded = (): void => {
      if (photoDegraded) return;
      photoDegraded = true;
      degradedPhotos += 1;
    };
    /** Route a swallowed failure to both the phase total and the photo count. */
    const degraded =
      (phase: DeepAnalysisPhase) =>
      (error: unknown): void => {
        deepAnalysisTiming.recordDegraded(phase, error);
        markPhotoDegraded();
      };
    // ONE bounded proxy per photo, on every path — not just the capped one.
    // expo-image's loadAsync subsamples during decode (Glide submit(w,h)), so
    // the original is never fully materialized; everything downstream then works
    // from a file:// JPEG of at most ANALYSIS_PROXY_SIZE. Before this, a normal
    // sub-500-photo pick — the beta's whole usage — sent the original
    // content:// URI to five preprocessors that each decoded it at full
    // resolution, which is the heap ceiling times several.
    const proxy = await deepAnalysisTiming.measureAwaited(
      "proxy-create",
      (timing) =>
        prepareCandidateAnalysisProxy(photo.uri, (error) =>
          timing.recordDegraded(error),
        ),
      markPhotoDegraded,
    );
    try {
      throwIfCancelled(options.signal);
      // A failed proxy is treated like any guarded native failure; do not fall
      // back to decoding the original and risk the Java heap. The photo still
      // reaches the planner, scored on its metadata alone.
      if (!proxy) {
        analysisQueue.release(job);
        return {
          photo,
          result: { embedding: [], faces: 0 },
          boxes: [] as FaceBox[],
          quality: probedQuality ?? {},
          pose: undefined,
          coverage: undefined,
          semantic: undefined,
          analysisWidth: photo.width,
          analysisHeight: photo.height,
        };
      }

      const analysisUri = proxy.uri;
      const analysisWidth = proxy.width;
      const analysisHeight = proxy.height;
      // Start face detection alongside the other models, then let only quality
      // measurement wait for its result. The quality API owns the single pixel
      // decode and can produce face-region signals only when given this box.
      const [result, boxes, quality, detectedPose, semantic] =
        await deepAnalysisTiming.measureConcurrentWall(async () => {
          const boxesPromise = deepAnalysisTiming
            .measureAwaited(
              "face-detect",
              (timing) =>
                detectFaces(analysisUri, {
                  width: analysisWidth,
                  height: analysisHeight,
                }, (error) => timing.recordDegraded(error)),
              markPhotoDegraded,
            )
            .catch((error) => {
              degraded("face-detect")(error);
              return [] as FaceBox[];
            });
          const qualityPromise = boxesPromise
            .then((detectedBoxes) => {
              const subjectBox = dominantFaceSubjectBox(
                detectedBoxes,
                analysisWidth,
                analysisHeight,
              );
              return deepAnalysisTiming.measureAwaited(
                "quality-decode",
                (timing) => {
                  const onDegraded = (error: unknown): void => {
                    timing.recordDegraded(error);
                  };
                  return subjectBox
                    ? measureImageQuality(analysisUri, { subjectBox, onDegraded })
                    : measureImageQuality(analysisUri, { onDegraded });
                },
                markPhotoDegraded,
              );
            })
            .catch((error) => {
              // The fallback this reaches for is the blurhash prior, which reads
              // ~0.05 sharpness on every photo ever taken. Falling back to it is
              // still right — an album with a mis-scored photo beats no album —
              // but it must never again happen without a number attached.
              degraded("quality-decode")(error);
              return probedQuality ?? {};
            });
          return Promise.all([
            // CX-16's orientation/reframe duplicate logic requires this documented
            // 76-value perceptual fingerprint. The capped path used to inject []
            // here, so its regression fixture passed only because it began after the
            // real bridge. Running it for the already-capped 64 restores that path.
            deepAnalysisTiming.measureAwaited(
              "perceptual",
              (timing) =>
                model.run(analysisUri, (error) => timing.recordDegraded(error)),
              markPhotoDegraded,
            ),
            // Dimensions supplied so the detector neither re-measures nor
            // re-manipulates: the proxy is already a file:// image inside its
            // detection bound, so boxes come back 1:1 in proxy coordinates — the
            // same space as analysisWidth/analysisHeight below.
            boxesPromise,
            // Always measure properly here, even when the prepass already produced a
            // probedQuality. That probe comes from a 4x3 blurhash decoded to 16x12,
            // which by construction holds no high frequencies — it reads ~0.05
            // sharpness no matter how well focused the photo is. It is a fine
            // ranking prior for choosing candidates, but feeding it onward as the
            // final quality signal drives every photo under the planner's quality
            // floor, so large libraries produce an empty album.
            qualityPromise,
            deepAnalysisTiming.measureAwaited(
              "movenet",
              (timing) =>
                detectBodyPose(
                  analysisUri,
                  analysisWidth,
                  analysisHeight,
                  timing,
                ),
              markPhotoDegraded,
            ),
            deepAnalysisTiming.measureAwaited(
              "tinyclip",
              (timing) =>
                analyzeSemanticImage(
                  analysisUri,
                  analysisWidth,
                  analysisHeight,
                  timing,
                ),
              markPhotoDegraded,
            ),
          ]);
        });
      throwIfCancelled(options.signal);
      // A degraded photo is never cached. Its signals are wrong in ways that do
      // not repeat -- the perceptual fallback is SEEDED FROM THE URI, and the
      // proxy uri is a fresh temp file every build -- so storing one would pin
      // a valid-looking embedding unrelated to the pixels for as long as the
      // shard survives. The job goes back to pending instead, which is what the
      // `pending:` counter in the log line reports.
      if (photoDegraded) {
        analysisQueue.release(job);
      } else {
        deepStore?.set(photo, {
          analysisWidth,
          analysisHeight,
          perceptual: result,
          boxes,
          quality,
          pose: detectedPose
            ? { keypoints: detectedPose.keypoints, scores: detectedPose.scores }
            : undefined,
          semantic,
        });
        analysisQueue.commit(job);
      }
      return {
        photo,
        result,
        boxes,
        quality,
        pose: detectedPose
          ? makePose(detectedPose.keypoints, detectedPose.scores)
          : undefined,
        // The SAME keypoints read positionally instead of as angles. The
        // dimensions must be the ones `detectBodyPose` letterboxed with —
        // analysisWidth/analysisHeight, not the original photo's — or every
        // in-frame test silently inverts on a non-square photo.
        coverage: detectedPose
          ? bodyCoverage(
              detectedPose.keypoints,
              detectedPose.scores,
              analysisWidth,
              analysisHeight,
            )
          : undefined,
        semantic,
        analysisWidth,
        analysisHeight,
      };
    } finally {
      await removeCandidateAnalysisProxy(proxy);
    }
  }, {
    signal: options.signal,
    waitUntilRunnable: waitUntilForeground,
    yieldEvery: ANALYSIS_YIELD_ITEMS,
    onComplete: (done) => {
      completedWork = prepassWork + done * ANALYSIS_WORK_UNITS;
      emitProgress(options.onProgress, {
        done: completedWork,
        total: totalWork,
        phase: capEngaged
          ? cappedAnalysisPhase(done, analysisInputs.length, photos.length)
          : lookingAtPhase(done, analysisInputs.length),
      });
    },
  }).catch((error: unknown) => {
    // A cancelled build unwinds past the report below, and the lag sampler is a
    // self-rescheduling timer: leaving it running would keep a chained
    // setTimeout alive for the life of the process. `stop` is idempotent.
    stopLagSampler();
    throw error;
  });
  const analysisWallMs = Date.now() - analysisStartedAt;
  reportRuntimeProfile(
    analysisWallMs,
    analysisInputs.length,
    inferenceBench,
    stopLagSampler,
  );
  // Undo the queue's ordering. Everything downstream — pose clustering, the
  // planner, the near-duplicate observations — reads this array, and it must be
  // in candidate order whether or not the store was consulted.
  const analyzed = restoreInputOrder(analysisOrder, orderedAnalyses);
  reportTiming(options, timings, {
    stage: "deep-analysis",
    elapsedMs: analysisWallMs,
    itemCount: analysisInputs.length,
    cacheHits: deepStore ? deepStore.stats().hits : undefined,
  });
  if (deepStore) {
    // Persisted before it is reported, so `disk` and `evicted` describe the
    // store as it now IS rather than as it was when the build opened it.
    await deepStore.persist();
    const stats = deepStore.stats();
    reportTiming(options, timings, {
      stage: "deep-signal-store",
      elapsedMs: 0,
      itemCount: analysisInputs.length,
      deepSignals: { ...stats, stillPending: analysisQueue.pending() },
    });
  }
  for (const aggregate of deepAnalysisTiming.summarize()) {
    reportTiming(options, timings, {
      stage: "deep-analysis-phase",
      phase: aggregate.phase,
      measurement: aggregate.measurement,
      elapsedMs: aggregate.totalMs,
      itemCount: aggregate.count,
      meanMs: aggregate.meanMs,
      p50Ms: aggregate.p50Ms,
      p95Ms: aggregate.p95Ms,
      reloadCount:
        aggregate.phase === "movenet" || aggregate.phase === "tinyclip"
          ? aggregate.reloadCount
          : undefined,
    });
  }
  const degradations = deepAnalysisTiming.degradations();
  reportTiming(options, timings, {
    stage: "analysis-degraded",
    // Not a duration. The stage exists to carry counters, and `elapsedMs` is
    // required by the shared shape rather than meaningful here.
    elapsedMs: 0,
    itemCount: analysisInputs.length,
    degradedPhotos,
    degraded: degradations,
  });
  // The counters say HOW MANY; only the message says what. Printed separately
  // and once per phase, because a per-photo warning across 3,000 photos is a
  // log nobody reads and the first failure is the one worth keeping.
  for (const degradation of degradations) {
    if (degradation.count === 0) continue;
    console.warn(
      `[PhoteoAlbumBuildDegraded] ${degradation.phase} count=${degradation.count} ` +
        `oom=${degradation.outOfMemory} first="${degradation.firstMessage}"`,
    );
  }
  reportModelLoads(
    options,
    timings,
    modelLoadsBefore,
    analysisModelLoadSnapshot(),
  );
  throwIfCancelled(options.signal);

  const enrichStartedAt = Date.now();
  const poseLabels = clusterPoses(
    analyzed
      .map(({ photo, pose }) => [photo.id, pose] as const)
      .sort(([left], [right]) => left.localeCompare(right)),
  ).labels;
  const enriched = analyzed.map(
    ({
      photo,
      result,
      boxes,
      quality,
      coverage,
      semantic,
      analysisWidth,
      analysisHeight,
    }) => {
    const poseLabel = poseLabels.get(photo.id);
    const analysis = analyzePhoto(
      photo,
      boxes,
      quality,
      analysisWidth,
      analysisHeight,
    );
    return {
      ...photo,
      poseCluster:
        poseLabel !== undefined && poseLabel >= 0
          ? `movenet:${poseLabel}`
          : photo.poseCluster,
      faces: result.faces,
      // Tie-break only: `selectBestShots` reads this to settle near-duplicate
      // frames of one take that every measured signal scored exactly alike.
      bodyCoverage: coverage,
      perceptualEmbedding: result.embedding,
      // `selectBestShots` uses this field for hard take collapse. Semantic
      // similarity is too broad for that job (two different beach moments can
      // be close), so it is only the fallback when no perceptual vector exists.
      embedding: embeddingForNearDuplicateGrouping(
        result.embedding,
        semantic?.embedding,
      ),
      semantic,
      analysis: {
        ...analysis,
        isScreenshotOrDocument:
          analysis.isScreenshotOrDocument ||
          (semantic?.screenshotDocument ?? 0) >
            SEMANTIC_SCREENSHOT_THRESHOLD,
      },
    };
    },
  );
  reportTiming(options, timings, {
    stage: "pose-and-enrich",
    elapsedMs: Date.now() - enrichStartedAt,
    itemCount: analyzed.length,
  });

  emitProgress(options.onProgress, {
    done: completedWork,
    total: totalWork,
    phase: "Choosing the best shots",
  });
  const selectionStartedAt = Date.now();
  const selection = selectBestShotsWithObservations(enriched, {
    count: Math.min(count, Math.max(1, enriched.length)),
    // Who the user said the album is for. Absent means unasked, which the
    // planner treats as "no preference" rather than "everyone is low priority".
    personPriority: options.preferences?.personPriority,
  });
  const album: AlbumData = selection.album;
  reportPoseDiversity(enriched, album);
  reportTiming(options, timings, {
    stage: "choose-best-shots",
    elapsedMs: Date.now() - selectionStartedAt,
    itemCount: enriched.length,
  });
  throwIfCancelled(options.signal);

  // This write observes a completed decision. It is deliberately outside the
  // selector and fail-neutral, so persistence can never change selectedIds.
  // Selected groups go last so bounded compaction preserves the context the
  // live swap UI can turn into a human pairwise preference.
  const selectedPreferenceIds = new Set(
    album.selected.map(({ media_id }) => preferenceAssetId(media_id)),
  );
  const observedGroups = selection.observations.nearDuplicateGroups;
  await captureNearDuplicateRankingLabels({
    albumId: album.album_id,
    groups: [
      ...observedGroups.filter(({ winnerAssetId }) => !selectedPreferenceIds.has(winnerAssetId)),
      ...observedGroups.filter(({ winnerAssetId }) => selectedPreferenceIds.has(winnerAssetId)),
    ],
    capturedAt: Date.now(),
  });

  const reviewStartedAt = Date.now();
  const uriById = new Map(photos.map((photo) => [photo.id, photo.uri]));
  const uri = (id: string) => uriById.get(id) ?? "";

  const selected: ReviewSelected[] = album.selected.map((item) => ({
    media_id: item.media_id,
    uri: uri(item.media_id),
    page: item.page,
    chosen_because: item.chosen_because,
    // The planner ranks every runner-up; the review contract surfaces the four
    // strongest so the swap sheet stays useful without becoming another grid.
    alternatives: item.alternatives.slice(0, 4).map<ReviewAlternative>((alt) => ({
      media_id: alt.media_id,
      uri: uri(alt.media_id),
      not_chosen_because: alt.not_chosen_because,
      fits_slot: alt.fits_slot ?? true,
    })),
  }));

  const pool: ReviewPoolItem[] = album.pool.map((item) => ({
    media_id: item.media_id,
    uri: uri(item.media_id),
    quality: item.quality,
    reasons: item.reasons,
  }));

  const review = { album_id: album.album_id, selected, pool };
  reportTiming(options, timings, {
    stage: "review-assembly",
    elapsedMs: Date.now() - reviewStartedAt,
    itemCount: album.selected.length + album.pool.length,
  });
  emitProgress(options.onProgress, {
    done: totalWork,
    total: totalWork,
    phase: "Choosing the best shots",
  });
  return review;
}
