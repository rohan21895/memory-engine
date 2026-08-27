// Integration glue (Claude): picked photos -> on-device model + face detection +
// image quality -> selection -> the review UI's data shape.
import { detectFaces, type FaceBox } from "./faces/face-detector";
import type { PickedPhoto } from "./import/picked-photo";
import { checkModelHealth, getModel } from "./ml";
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
  chooseHeavyAnalysisCandidates,
  HEAVY_ANALYSIS_CANDIDATE_LIMIT,
  type ProbedCandidate,
} from "./selection/candidate-prepass";
import {
  mapLimit,
  throwIfCancelled,
  yieldToEventLoop,
} from "./selection/concurrent-map";
import { watchAlbumBuildLifecycle } from "./selection/album-build-lifecycle";
import {
  loadCandidateProbeCache,
  probeCandidateWithCache,
  type CandidateProbeCache,
} from "./selection/candidate-probe-cache";
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
 * This is a real bound on native work now. It used to be decorative twice over:
 * every photo held up to five INDEPENDENT full-resolution decodes of the
 * original (expo-image-manipulator loads via Glide at SIZE_ORIGINAL and has no
 * subsampling hint, so a 12MP frame is a ~48MB ARGB bitmap - six photos in
 * flight could ask for over a gigabyte against a 192-256MB heap), while the
 * MoveNet and TinyCLIP wrappers serialized their preprocessing on a module-level
 * queue, so the photos that survived that queued up single file anyway. Both are
 * fixed: one bounded proxy per photo feeds every model, and the wrappers now
 * serialize only the inference itself.
 */
const ANALYZE_CONCURRENCY = 6;

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
};

export type BuildAlbumOptions = {
  signal?: AbortSignal;
  onProgress?: (progress: BuildAlbumProgress) => void;
  /** Always-on console trace also emits through here for device benchmarks. */
  onTiming?: (timing: BuildAlbumTiming) => void;
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

function formatTiming(timing: BuildAlbumTiming): string {
  if (timing.stage === "analysis-degraded") {
    return formatDegradation(timing);
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
  const lifecycle = await watchAlbumBuildLifecycle(
    options.signal,
    async () => probeCache?.persist(),
  );
  try {
    return await buildAlbumImpl(
      photos,
      count,
      options,
      timings,
      probeCache,
      lifecycle.waitUntilForeground,
    );
  } finally {
    await probeCache?.persist();
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
      `[album-build-timing] ${timings
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
  const expectedCandidateCount = capEngaged
    ? Math.min(HEAVY_ANALYSIS_CANDIDATE_LIMIT, photos.length)
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
      HEAVY_ANALYSIS_CANDIDATE_LIMIT,
      { isFamiliar: await familiarPersonPredicate() },
    ).map(({ photo, quality }) => ({ photo, quality }));
    reportTiming(options, timings, {
      stage: "candidate-rank",
      elapsedMs: Date.now() - rankStartedAt,
      itemCount: probed.length,
    });
    console.info(
      `[album-build] Candidate cap engaged: analyzing the best ${analysisInputs.length} of ${photos.length} photos.`,
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
  const analysisStartedAt = Date.now();
  const deepAnalysisTiming = new DeepAnalysisTimingCollector();
  // Photos that lost at least one signal, counted once however many they lost.
  // The per-phase totals below can exceed this: one OOM tends to take several
  // phases of the SAME photo, and reporting only the phase sum would read as
  // several bad photos instead of one bad moment for the heap.
  let degradedPhotos = 0;
  const analyzed = await mapLimit(analysisInputs, ANALYZE_CONCURRENCY, async ({
    photo,
    quality: probedQuality,
  }) => {
    throwIfCancelled(options.signal);
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
  });
  reportTiming(options, timings, {
    stage: "deep-analysis",
    elapsedMs: Date.now() - analysisStartedAt,
    itemCount: analysisInputs.length,
  });
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
      `[album-build-degraded] ${degradation.phase} count=${degradation.count} ` +
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
  });
  const album: AlbumData = selection.album;
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
