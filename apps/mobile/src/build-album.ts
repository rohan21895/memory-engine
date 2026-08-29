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
  candidateBudget,
  chooseHeavyAnalysisCandidates,
  shouldCapCandidates,
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
 * MEASURED ON THE PHONE, 36-photo build, after the native proxy landed:
 *
 *   analysis-degraded={photos:0/36, proxy-create:0, ..., oom:0}
 *   proxy-create      mean 11.1ms   p95 26ms
 *   quality-decode    mean 1299ms   p95 1340ms
 *   movenet wall      mean 1347ms   (inference 1013ms)
 *   tinyclip wall     mean 1400ms   (inference 630ms)
 *   total             51833ms / 36 photos, at concurrency 1
 *
 * Zero OOM, zero degraded photos. That build is why this is three and not one.
 *
 * HISTORY, kept short because it cost two wrong answers. The OOM was a failed
 * 28,975,795-byte allocation. It was once attributed to expo-image-manipulator's
 * `byteOut.toByteArray()`; that is refuted twice over and must not be
 * re-asserted. The proxy caps at ANALYSIS_PROXY_SIZE = 1280 px (1.6 MP), and no
 * JPEG encoder emits 27.63 MiB from 1.6 MP; and that call sits inside
 * `if (base64)`, so it only runs for `saveAsync({base64: true})`, whose call
 * sites here top out at 1280 px and are mostly 224-512 px. The `quality-decode`
 * phase label was not evidence either: `measureImageQuality` only ever runs on
 * the proxy, resampled to QUALITY_SAMPLE_WIDTH = 512, so it named the most
 * bounded path in the build. At concurrency six with four coroutine-dispatched
 * paths per photo, up to 24 allocations were live against one heap; the bucket
 * named a bystander, not the allocator.
 *
 * What the number matched was an ORIGINAL -- his library holds DSLR JPEGs of
 * 25-27 MiB. The fix followed from that and not from any stack frame:
 * `prepareCandidateAnalysisProxy` now takes a MediaStore id, not a URI, and goes
 * through the native `albumAnalysisProxy`, which asks
 * ContentResolver.loadThumbnail first and otherwise hands ImageDecoder the
 * 1280 px target BEFORE pixel allocation. The `compress: 0.94` full-quality
 * re-encode went with it. The exact allocating frame was never identified, and
 * did not need to be once the route was gone -- but do not let that absence
 * invite the refuted answer back.
 *
 * WHY THREE -- and why the number barely matters. A 300-photo build on the
 * owner's phone (08-29 17:30) settled this:
 *
 *   tinyclip     awaited mean 3105.7ms   inference mean 1042.9ms   total 312856ms
 *   movenet      awaited mean 2462.8ms   inference mean  527.3ms   total 158176ms
 *   quality-decode  awaited mean 2660.5ms
 *   deep-analysis 415114ms / 300         analysis-degraded 0/300, oom:0
 *
 * Read the first row twice: 3105.7 / 1042.9 = 2.98, against a concurrency of 3.
 * TinyCLIP is serialised to the last decimal -- each admitted photo waits behind
 * exactly the other two, so raising this number raises the queue by the same
 * factor and finishes no sooner. And TinyCLIP inference alone is 312856ms of a
 * 415114ms wall: 75% of an album build is one model, computing one photo at a
 * time. This constant cannot reach that.
 *
 * So the earlier claim here -- that quality-decode is the thing that scales --
 * was half wrong and is corrected: its mean went 1299ms at concurrency 1 to
 * 2660.5ms at three, so it gains about 1.46x, not 3x. Real, but it is not the
 * wall. Per-photo wall moved 1439.8ms to 1390.7ms across the two builds (3.4%,
 * and different photo sets, so treat it as "no worse" rather than a speedup).
 *
 * Three stays because it is measured-safe -- oom:0 across 300 photos, eight
 * times the evidence the previous value had -- not because it is fast. If an
 * album build has to get faster, this constant is the wrong lever; the levers
 * are fewer photos reaching TinyCLIP, a second interpreter to break the lock,
 * or a GPU/NNAPI delegate.
 *
 * THEN THE MODELS WERE UNPINNED FROM ONE THREAD, AND THIS INVERTED. Same phone,
 * 08-29 18:35, 645 picked / 64 analysed:
 *
 *   tinyclip  inference mean  558.9ms  p50  322ms   (was 1042.9 / 1325 at 1 thread)
 *   movenet   inference mean  388.0ms  p50   41ms   (was  527.3 /   35)
 *   quality-decode awaited mean 1632.9ms
 *   concurrent-model-group wall mean 2523.6ms
 *   deep-analysis 65710ms / 64        total 74885ms / 645, oom:0, 0/64 degraded
 *
 * TinyCLIP inference fell 1.87x on the mean and 4.1x on the median. So the
 * "4t ~= 1t" result quoted above is a MAC result and does not hold on this
 * device; it should not be cited against threading again.
 *
 * And the shape of the build changed with it. TinyCLIP was 75% of the wall; now
 * the two models together are about 947ms of a 2523.6ms concurrent group, and
 * the largest single phase is `quality-decode` at 1632.9ms -- JS-side jpeg-js
 * decode and pixel loops, not model compute. THE BUILD IS NO LONGER
 * TINYCLIP-BOUND. Anyone optimising from here should re-measure before assuming
 * it is: a second interpreter or a GPU delegate now attacks a third of the
 * cost, and the JS decode path is the bigger half.
 *
 * Do not raise this without re-reading `analysis-degraded` from a real build.
 * `oom:0` is the number that matters; if it stops being zero, this went too far.
 */
const ANALYZE_CONCURRENCY = 3;

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
 * NOW ON. What decided it: the risk of flipping it is a MISS, not a wrong
 * album. `deep-signal-parity.test.ts` runs 6 plans over 6,048 pairs and reports
 * cosine drift of 1.98e-8, with the nearest pair sitting 2.48e-4 from the 0.92
 * bar -- a 12,562x margin -- and no plan moving. It is not a vacuous gate
 * either: the rejected int8 encoding, used as its sabotage, crosses that bar
 * twice. So a hit returns the same album the slow path would have, and a miss
 * costs one 3.9 KB encode on top of work that was happening anyway.
 *
 * Against that, the measured win is 156 s to 7.3 s on a repeat build, and the
 * candidate cap now engages far more often, so the 64 photos a build analyses
 * are exactly the ones most likely to be asked for again.
 *
 * Still unmeasured, and worth watching rather than blocking on: the hit rate on
 * a real library. A repeat of the same filter hits everything; a different
 * filter re-ranks the cheap probes and can pick a largely different 64.
 * `deep-signal-store.benchmark.ts` reports that overlap for nested and disjoint
 * filters, but only against synthetic corpora.
 */
const USE_DEEP_SIGNAL_CACHE = true;

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
  const capEngaged = shouldCapCandidates(photos.length, count);
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
      lifecycle.waitUntilRunnable,
      lifecycle.report,
    );
  } finally {
    await probeCache?.persist();
    await deepStore?.persist();
    // Awaited: this stops the foreground service and clears its notification.
    // Leaving it running would leave "Building your album" on his phone after
    // the album is already on screen.
    await lifecycle.dispose();
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
  waitUntilRunnable: () => Promise<void>,
  reportProgress: (text: string) => void = () => undefined,
): Promise<ReviewData> {
  throwIfCancelled(options.signal);
  const model = getModel();
  const modelLoadsBefore = analysisModelLoadSnapshot();
  // Started here so it overlaps the prepass, awaited before the heavy pass.
  // Every build then leaves one "[photeo-models] ..." line naming the graphs
  // that actually loaded, and each wrapper knows up front whether its graph is
  // usable instead of preprocessing every photo for an answer it cannot give.
  const modelHealth = checkModelHealth();
  // The cap now engages whenever the pick costs more than the deep stage can
  // afford, rather than above a fixed 500 photos. Under the old rule his
  // 300-photo pick ran uncapped at 417s while a 600-photo pick would have been
  // capped to 64 and finished far sooner -- picking more photos made the album
  // faster. See `shouldCapCandidates`.
  const capEngaged = shouldCapCandidates(photos.length, count);
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
        waitUntilRunnable,
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
        prepareCandidateAnalysisProxy(photo.id, (error) =>
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
    waitUntilRunnable,
    yieldEvery: ANALYSIS_YIELD_ITEMS,
    onComplete: (done) => {
      completedWork = prepassWork + done * ANALYSIS_WORK_UNITS;
      const phase = capEngaged
        ? cappedAnalysisPhase(done, analysisInputs.length, photos.length)
        : lookingAtPhase(done, analysisInputs.length);
      emitProgress(options.onProgress, {
        done: completedWork,
        total: totalWork,
        phase,
      });
      // This is the stage that takes the minutes -- 415s of a 417s build on the
      // 300-photo run. It is also the only thing he can see while the app is in
      // his pocket, so the notification must carry it rather than sit unchanged.
      reportProgress(phase);
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
