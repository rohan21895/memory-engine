// Integration glue (Claude): picked photos -> on-device model + face detection +
// image quality -> selection -> the review UI's data shape.
import { detectFaces, type FaceBox } from "./faces/face-detector";
import type { PickedPhoto } from "./import/picked-photo";
import { getModel } from "./ml";
import { detectBodyPose } from "./ml/movenet";
import {
  analyzeSemanticImage,
  SEMANTIC_SCREENSHOT_THRESHOLD,
} from "./ml/tinyclip";
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
  prepareCandidateAnalysisProxy,
  probeCandidateQuality,
  removeCandidateAnalysisProxy,
} from "./selection/candidate-quality-probe";
import { measureImageQuality } from "./selection/image-quality";
import { clusterPoses, makePose } from "./selection/pose";
import {
  classifyCategory,
  isScreenshotOrDocument,
  type FaceSignal,
  type QualitySignals,
} from "./selection/quality-signals";
import { selectBestShots } from "./selection/select-best-shots";
import type { AlbumData } from "./selection/types";

// A face whose box sits within 1% of any border is treated as cut off.
const EDGE_FRACTION = 0.01;
// ML Kit detection + two image-manipulator passes per photo is heavy; cap
// concurrency so a large pick set can't spawn hundreds of native ops at once.
// ponytail: fixed batch of 6; make adaptive only if build time becomes a problem.
const ANALYZE_CONCURRENCY = 6;
// The 32 px platform thumbnail is substantially smaller than any model input. A little
// extra concurrency keeps large library screening I/O-bound without allowing
// hundreds of image-manipulator operations to accumulate.
const PREPASS_CONCURRENCY = 32;
const MAX_PREPASS_PROGRESS_UPDATES = 200;

export type BuildAlbumProgress = {
  done: number;
  total: number;
  phase: string;
};

export type BuildAlbumOptions = {
  signal?: AbortSignal;
  onProgress?: (progress: BuildAlbumProgress) => void;
};

export class AlbumBuildCancelledError extends Error {
  constructor() {
    super("Album build was cancelled.");
    this.name = "AlbumBuildCancelledError";
  }
}

async function mapLimit<T, R>(
  items: readonly T[],
  limit: number,
  fn: (item: T, index: number) => Promise<R>,
  options: {
    signal?: AbortSignal;
    onComplete?: (completed: number) => void;
  } = {},
): Promise<R[]> {
  const results = new Array<R>(items.length);
  let cursor = 0;
  let completed = 0;
  const workers = Array.from({ length: Math.min(limit, items.length) }, async () => {
    while (cursor < items.length) {
      throwIfCancelled(options.signal);
      const index = cursor++;
      results[index] = await fn(items[index], index);
      throwIfCancelled(options.signal);
      completed += 1;
      options.onComplete?.(completed);
    }
  });
  await Promise.all(workers);
  return results;
}

function throwIfCancelled(signal: AbortSignal | undefined): void {
  if (signal?.aborted) throw new AlbumBuildCancelledError();
}

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

// Assemble the selection quality contract from a photo + its detected faces +
// measured pixel quality. All inputs are best-effort; missing data stays neutral.
function analyzePhoto(
  photo: PickedPhoto,
  boxes: FaceBox[],
  quality: { sharpness?: number; exposure?: number; clippedFraction?: number },
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
  throwIfCancelled(options.signal);
  const model = getModel();
  const capEngaged = photos.length > CANDIDATE_PREPASS_THRESHOLD;
  const expectedCandidateCount = capEngaged
    ? Math.min(HEAVY_ANALYSIS_CANDIDATE_LIMIT, photos.length)
    : photos.length;
  const totalWork =
    (capEngaged ? photos.length : 0) + expectedCandidateCount + 1;

  let analysisInputs: Array<{
    photo: PickedPhoto;
    quality?: ProbedCandidate["quality"];
  }>;
  let completedWork = 0;

  if (capEngaged) {
    emitProgress(options.onProgress, {
      done: 0,
      total: totalWork,
      phase: lookingAtPhase(0, photos.length),
    });
    const probed = await mapLimit(
      photos,
      PREPASS_CONCURRENCY,
      async (photo): Promise<ProbedCandidate> => ({
        photo,
        quality: await probeCandidateQuality(photo.uri),
      }),
      {
        signal: options.signal,
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
    throwIfCancelled(options.signal);
    analysisInputs = chooseHeavyAnalysisCandidates(probed).map(
      ({ photo, quality }) => ({ photo, quality }),
    );
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

  const analyzed = await mapLimit(analysisInputs, ANALYZE_CONCURRENCY, async ({
    photo,
    quality: probedQuality,
  }) => {
    throwIfCancelled(options.signal);
    const proxy = capEngaged
      ? await prepareCandidateAnalysisProxy(photo.uri)
      : undefined;
    try {
      throwIfCancelled(options.signal);
      // A failed large-photo proxy is treated like any guarded native failure;
      // do not fall back to decoding the original and risk the Java heap.
      if (capEngaged && !proxy) {
        return {
          photo,
          result: { embedding: [], faces: 0 },
          boxes: [] as FaceBox[],
          quality: probedQuality ?? {},
          pose: undefined,
          semantic: undefined,
          analysisWidth: photo.width,
          analysisHeight: photo.height,
        };
      }

      const analysisUri = proxy?.uri ?? photo.uri;
      const analysisWidth = proxy?.width ?? photo.width;
      const analysisHeight = proxy?.height ?? photo.height;
      const [result, boxes, quality, detectedPose, semantic] = await Promise.all([
        capEngaged
          ? Promise.resolve({ embedding: [], faces: 0 })
          : model.run(analysisUri),
        detectFaces(analysisUri).catch(() => [] as FaceBox[]),
        probedQuality
          ? Promise.resolve(probedQuality)
          : measureImageQuality(analysisUri).catch(() => ({})),
        detectBodyPose(analysisUri),
        analyzeSemanticImage(analysisUri, analysisWidth, analysisHeight),
      ]);
      throwIfCancelled(options.signal);
      return {
        photo,
        result,
        boxes,
        quality,
        pose: detectedPose
          ? makePose(detectedPose.keypoints, detectedPose.scores)
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
    onComplete: (done) => {
      completedWork = (capEngaged ? photos.length : 0) + done;
      emitProgress(options.onProgress, {
        done: completedWork,
        total: totalWork,
        phase: capEngaged
          ? cappedAnalysisPhase(done, analysisInputs.length, photos.length)
          : lookingAtPhase(done, analysisInputs.length),
      });
    },
  });
  throwIfCancelled(options.signal);

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
      perceptualEmbedding: result.embedding,
      embedding: semantic?.embedding ?? result.embedding,
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

  emitProgress(options.onProgress, {
    done: completedWork,
    total: totalWork,
    phase: "Choosing the best shots",
  });
  const album: AlbumData = selectBestShots(enriched, {
    count: Math.min(count, Math.max(1, enriched.length)),
  });
  throwIfCancelled(options.signal);

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
  emitProgress(options.onProgress, {
    done: totalWork,
    total: totalWork,
    phase: "Choosing the best shots",
  });
  return review;
}
