import { decode as decodeJpeg } from "jpeg-js";

// Explicit extensions keep this pure module importable by Node's TS test runner.
// @ts-expect-error TypeScript bundler resolution normally omits source extensions.
import { faceEmbeddingPathCounts } from "../ml/facenet.ts";
// @ts-expect-error TypeScript bundler resolution normally omits source extensions.
import { captureAlignedSamples, faceAlignmentShapeCounts, takeAlignedSamples } from "../ml/face-align.ts";
// @ts-expect-error TypeScript bundler resolution normally omits source extensions.
import { DEFAULT_MERGE_THRESHOLD, DEFAULT_PERCEPTUAL_THRESHOLD, SAME_PHOTO_EXCEPTION_SIMILARITY, clusterFaces, cosine, extendFaceClusters } from "./face-cluster.ts";
// @ts-expect-error TypeScript bundler resolution normally omits source extensions.
import { calibrateThreshold } from "./face-calibration.ts";

// @ts-expect-error TypeScript bundler resolution normally omits source extensions.
import { closeFaceFrame, deleteImageFile, frameOrientationCounts, landmarkRejectCounts, detectFacesInFrame, isFaceDetectionAvailable, openFaceFrame, scaleFaceBox, takeScanTrace, traceScanCount, traceScanStage, type FaceBox, type FaceFrame } from "./face-detector.ts";
// @ts-expect-error Node's TypeScript runner requires the source extension.
import { embedFaceIdentity, traceNextAlignments, type FaceImageSource } from "../ml/facenet.ts";
// @ts-expect-error Node's TypeScript runner requires the source extension.
import { incrementalScanTarget } from "../import/incremental-index.ts";
import type { FaceEmbeddingKind, FaceObservation, Person } from "./types";

// v18 stores aligned embeddings as int8/base64. Older versions contain
// unaligned embeddings and must be rebuilt rather than migrated across spaces.
// v19 recalibrated the DEFAULT thresholds (0.5 -> 0.62/0.72) but every shipped
// call site was still passing explicit overrides of 0.37/0.30, so the merge bar
// never actually moved and the library stayed collapsed into one identity.
// v20 deletes those overrides. Clusters persisted under any earlier version were
// built by the runaway merge and must be discarded, not carried forward —
// otherwise an upgrading user keeps the broken grouping.
// 21: every embedding before this was computed from a mis-aligned crop.
// ML Kit names eye/mouth landmarks by PICTURE side, not by the subject's, so
// crossing them onto the ArcFace template asked for a reflection a similarity
// transform cannot express. Instead of failing it collapsed the scale to 0.156
// and the "aligned face" became the entire photograph squeezed into 112x112
// (verified by dumping the actual crops). Landmark residual was 25.9px on a
// 112px template; it is 3.8px now. Those embeddings cannot be salvaged by
// re-clustering, so this is the rare case where discarding them and re-scanning
// is the correct call rather than the lazy one.
const INDEX_VERSION = 22;
const INDEX_FILENAME = "face-index.json";
const FACE_THUMB_DIRECTORY = "face-thumbnails";
const PAGE_SIZE = 100;
/**
 * Calibration instrumentation: the impostor sweep and the alignment probe.
 *
 * These are how the mirrored-alignment collapse was found, so they stay in the
 * tree — but the sweep is O(n^2) over a sample of the whole index and the probe
 * re-runs the real pipeline over fresh photos. Measured on device they cost
 * minutes, during which nothing else on the JS thread makes progress. Flip to
 * true and rebuild to re-measure; it must never ship on.
 *
 * Typed `boolean` rather than left as a literal so the guarded bodies below do
 * not narrow to unreachable code.
 */
const FACE_DIAGNOSTICS: boolean = false;
const SCAN_BATCH_SIZE = 32;
/**
 * Held at 2 on purpose, and not a guess.
 *
 * `@infinitered/react-native-mlkit-face-detection` implements `detectFaces` as
 * an Expo `AsyncFunction` whose body is a `runBlocking { ... }` on a single
 * detector instance, so native detection is serialized however many callers ask
 * for it. Two workers is exactly enough to keep that serialized detect busy
 * while the other worker does JS-side crop/decode/inference work; a third only
 * adds a live frame bitmap to the heap and waits in the same native queue.
 */
const SCAN_CONCURRENCY = 2;
/**
 * Box short side, in FRAME pixels, below which a face is cropped from the
 * original instead of the shared frame.
 *
 * Alignment warps the patch onto a 112x112 ArcFace template, so once a face is
 * at least 112px across, the aligned crop is a downscale and any extra source
 * resolution is discarded anyway. At or above this bar the shared frame carries
 * provably enough detail; below it, reusing the frame would UPSCALE into the
 * template — no error, no artifact, just quietly worse identities — so those
 * faces pay the old full-resolution decode instead. Quality here is preserved
 * by construction rather than by hope, which is why the bar is the tensor size
 * and not a tuned number.
 *
 * It is deliberately conservative. The real no-loss point is nearer 100px (the
 * warp maps interocular distance, roughly a third of the box, onto the
 * template's 35px eye spacing), and ML Kit's `minFaceSize: 0.08` already puts
 * most detections just above 100 frame px — so a bar of 112 may send a band of
 * faces down the slow path for a 10% resolution difference nobody can measure.
 * The per-batch `smallFaceFullRes` counter in logcat says how often that
 * happens; lower this to ~96 if it turns out to be a large share of faces.
 */
const MIN_FRAME_EMBED_FACE_PX = 112;
const CHECKPOINT_ASSETS = 50;
const CHECKPOINT_INTERVAL_MS = 10_000;
const FACE_THUMBNAIL_SIZE = 128;
const LUMA_GRID_SIZE = 8;
const COLOR_BINS = 4;
const FACE_PADDING_SCALE = 1.3;
const BASE64_ALPHABET =
  "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
/** Reverse table; `indexOf` per character is a 64-wide scan per decoded byte. */
const BASE64_VALUES = (() => {
  const table = new Int8Array(128).fill(-1);
  for (let position = 0; position < BASE64_ALPHABET.length; position += 1) {
    table[BASE64_ALPHABET.charCodeAt(position)] = position;
  }
  return table;
})();

/**
 * COLD-START cosine bar, held only until this library calibrates its own.
 *
 * This was 0.20, ported by matching FAR against LFW, where the same bundled
 * TFLite build puts the impostor p99 at 0.169. That benchmark is the wrong
 * yardstick for this product: LFW pairs are strangers, and a family library is
 * mostly relatives, who genuinely resemble each other. Measured through this
 * exact model on two real libraries, the impostor p99 came out at 0.264 and
 * 0.427, and 0.20 admitted 5.3% and 17.5% of different-person pairs — roughly
 * one in six in the second. That is what "my brother-in-law's album has my
 * mother in it" looks like as a number.
 *
 * No constant fixes that, because the two libraries need bars 36% apart. The
 * bar is therefore MEASURED per library by `calibrateThreshold`, and this value
 * only covers the window before enough same-photo pairs exist to measure with.
 *
 * It sits at the harder of the two libraries rather than between them, so the
 * uncalibrated case fails toward splitting: a person arriving as two groups is
 * one merge away from correct, while two people fused is unrecoverable. Real
 * calibration RELAXES it where a library turns out to be easy. Provisional
 * until re-derived on a demographically balanced set (RFW/BUPT-Balancedface)
 * rather than on the two libraries that happened to be at hand.
 */
export const DEFAULT_FACE_INDEX_THRESHOLD = 0.44;

/**
 * Identifies HOW the persisted people were grouped, not just at what bar.
 *
 * A threshold change is not the only thing that invalidates a stored grouping.
 * The clusterer previously scored a face against a cluster with
 * `cosine(face, centroid)`, which is not a similarity: a centroid is the mean of
 * unit vectors, so that value is the mean cosine to members DIVIDED by the
 * centroid's length, and it therefore rises as a cluster gets sloppier. Absorb
 * junk, get shorter, look closer to everything, absorb more. Switching to true
 * average linkage changes every score in the index, so groupings computed under
 * the old rule have to be rebuilt even though the threshold is unchanged.
 *
 * Bump this whenever the clustering RULE changes. It is deliberately not
 * INDEX_VERSION: that would make `parseIndex` reject the file and discard every
 * embedding, re-scanning the whole library for what is a cheap recomputation
 * over data already on disk.
 */
export const CLUSTER_CALIBRATION = "avg-linkage-w600k-mbf-calibrated-1";

/**
 * Bar for the CENTERED space, and only valid there.
 *
 * Centering shifts the whole distribution, so a bar that is sane in one space
 * is badly wrong in the other: a raw-space bar applied to centered embeddings
 * shatters identities, and a centered bar applied to RAW embeddings merges
 * strangers. These two constants and CLUSTER_CALIBRATION move together or not
 * at all.
 *
 * Ported alongside the model swap by the same ratio as the raw bar. Unlike the
 * raw bar this one is NOT independently measured in w600k_mbf space — centered
 * clustering is still behind USE_CENTERED_CLUSTERING = false, so nothing runs
 * on it. Measure before switching it on.
 */
export const CENTERED_FACE_INDEX_THRESHOLD = 0.17;
export const CENTERED_CLUSTER_CALIBRATION = "centered-avg-linkage-w600k-mbf-1";

/**
 * How far the calibrated bar must move before the whole grouping is rebuilt.
 *
 * The bar stopped being a constant, so it now drifts slightly every time the
 * library grows and the impostor sample gets one pair longer. Without a dead
 * band, `reclusterIfCalibrationChanged` would re-cluster every persisted face
 * on every scan to chase a move of 0.001 that reassigns nobody. Set below the
 * smallest move that can actually change a decision, so real recalibration
 * still lands promptly.
 */
const RECALIBRATION_HYSTERESIS = 0.01;

/**
 * Centering needs a POPULATION to estimate a mean from. With a handful of faces
 * the mean sits between them, so subtracting it drives them apart artificially
 * — two faces centered against their own midpoint land at cosine -1 and can
 * never group. Below this count the raw space and the raw bar are used.
 */
const CENTERING_MIN_OBSERVATIONS = 200;

/**
 * Centering is OFF now that alignment is fixed, and that is a measurement, not
 * a preference.
 *
 * It was added when the raw impostor median was 0.725 and the population mean
 * had norm 0.845 — but both were artefacts of the alignment bug, which fed the
 * model near-identical wide shots. With crops corrected, the on-device raw
 * distribution reproduces this model's offline benchmark almost exactly
 * (impostor median 0.177 measured here vs 0.180 offline), so the RAW space is
 * the one with a real calibration behind it: 0.35 -> TAR 92.4% / FAR 1.8%, and
 * 0.40 -> TAR 88.0% / FAR 0.63%, measured on labelled faces.
 *
 * Centering still reports lower impostor percentiles, but nothing measures what
 * it does to GENUINE pairs, so choosing it would trade a calibrated bar for an
 * uncalibrated one. Flip this to true only alongside a genuine/impostor
 * measurement in the centered space.
 */
const USE_CENTERED_CLUSTERING = false;

/**
 * Cluster-to-cluster merge bar. Held at the calibrated post-alignment default,
 * and never below the bar a single face had to clear to join a person.
 *
 * This constant used to be 0.37, with a second "large cluster" constant of 0.30
 * that took over once BOTH clusters held 10+ faces. Both numbers were calibrated
 * against unaligned bounding-box crops and were never revisited when 5-point
 * alignment tightened the space. src/faces/face-cluster-recovery.test.ts
 * measures what they actually did to aligned embeddings: on eight synthetic
 * identities whose centroids sit at cosine 0.153-0.398 from each other, a merge
 * bar of 0.37 fuses them into six tiles (one holding three people) and 0.30
 * fuses all eight into a single 112-face tile. The large-cluster rule was the
 * worse of the two, because relaxing the bar for clusters that have already
 * absorbed 10 faces is a positive feedback loop: every merge pulls a centroid
 * toward the population mean, which raises its similarity to every remaining
 * cluster, which licenses the next merge. That is the 2,164-photo tile.
 */
export const FACE_INDEX_IDENTITY_MERGE_THRESHOLD = DEFAULT_MERGE_THRESHOLD;
export const PERCEPTUAL_FACE_INDEX_THRESHOLD = DEFAULT_PERCEPTUAL_THRESHOLD;

/**
 * Faces below this are withheld from the People UI as probable fragments.
 *
 * Absolute, not relative. The previous floor was `max(3, largest * 0.1)`, which
 * makes the most-photographed person censor everyone else: beside a 2,164-face
 * tile it demanded 217 faces before a person was allowed to appear at all, so a
 * real family member with fifty photos was silently invisible. A dominant
 * cluster is evidence about that cluster, never about anybody else.
 */
const MIN_VISIBLE_FACE_COUNT = 2;

export type FaceIndexPerson = {
  id: string;
  faceCount: number;
  coverAssetId: string;
  assetIds: string[];
  /**
   * Persisted circular face-crop thumbnail (file:// or data: URI) for the Face
   * filter avatars. Optional: undefined until the face index has cropped one;
   * the UI falls back to `coverAssetId`'s full-frame content URI.
   */
  faceThumbUri?: string;
};

export type FaceIndexStatus = {
  identityObservations: number;
  perceptualObservations: number;
  scanned: number;
  total: number;
  people: number;
};

export type BuildFaceIndexOptions = {
  onProgress?: (done: number, total: number) => void;
  threshold?: number;
};

export type FaceScanAsset = {
  id: string;
  width: number;
  height: number;
};

export type FaceScanDependencies = {
  isDetectionAvailable: () => boolean;
  /**
   * Opens the one decode that detection, embedding and the thumbnail crop all
   * share. Optional so offline tests can inject plain per-call fakes; when it is
   * absent every stage falls back to working from the URI.
   */
  openFrame?: (
    imageUri: string,
    asset: FaceScanAsset,
  ) => Promise<FaceFrame | null>;
  closeFrame?: (frame: FaceFrame) => Promise<void>;
  detectFaces: (
    imageUri: string,
    source?: FaceScanAsset,
    frame?: FaceFrame | null,
  ) => Promise<FaceBox[]>;
  embedFace: (
    asset: FaceScanAsset,
    imageUri: string,
    box: FaceBox,
    frame?: FaceFrame | null,
  ) => Promise<FaceEmbedding>;
  onFaceCrop?: (observation: FaceObservation, cropUri: string) => void;
};

export type FaceEmbedding = {
  embedding: number[];
  kind: FaceEmbeddingKind;
  cropUri?: string;
};

export type FacePeopleQuery = {
  getPeople: () => FaceIndexPerson[];
  assetIdsForPerson: (personId: string) => string[];
};

type PersistedFaceIndex = {
  version: typeof INDEX_VERSION;
  observations: FaceObservation[];
  people: Person[];
  processedAssetIds: Record<string, true>;
  seenAssetIds: Record<string, true>;
  cursor: string | null;
  scanComplete: boolean;
  total: number;
  threshold: number;
  /** Which clustering rule produced `people`; see CLUSTER_CALIBRATION. */
  calibration?: string;
  /** Frozen population mean the stored centroids are centered against. */
  embeddingMean?: number[];
  faceThumbUris: Record<string, string>;
};

type StoredFaceObservation = Omit<FaceObservation, "embedding"> & {
  embedding: string;
};

type StoredPerson = Omit<Person, "centroid"> & { centroid: string };

type StoredFaceIndex = Omit<
  PersistedFaceIndex,
  "observations" | "people"
> & {
  observations: StoredFaceObservation[];
  people: StoredPerson[];
};

function emptyIndex(): PersistedFaceIndex {
  return {
    version: INDEX_VERSION,
    observations: [],
    people: [],
    processedAssetIds: {},
    seenAssetIds: {},
    cursor: null,
    scanComplete: false,
    total: 0,
    threshold: DEFAULT_FACE_INDEX_THRESHOLD,
    calibration: CLUSTER_CALIBRATION,
    faceThumbUris: {},
  };
}

let index = emptyIndex();
let activeBuild: Promise<void> | null = null;
let hydration: Promise<void> | null = null;
let duplicateDetectionsDropped = 0;
let personIdsByAsset = new Map<string, string[]>();
let progressSubscribers = new Set<(done: number, total: number) => void>();
let activeScanControl: { cancelled: boolean; foreground: boolean } | null = null;

function observationCounts(): Pick<
  FaceIndexStatus,
  "identityObservations" | "perceptualObservations"
> {
  const identityObservations = index.observations.filter(
    (observation) => observation.embeddingKind === "identity",
  ).length;
  return {
    identityObservations,
    perceptualObservations: index.observations.length - identityObservations,
  };
}

function logEmbeddingPath(context: string): void {
  const counts = observationCounts();
  const closestPairs: Array<{
    a: string;
    b: string;
    shared: number;
    similarity: number;
  }> = [];
  // The closest-pair scan is O(people^2) and allocates a Set of one person's
  // asset ids per pair. On a real library that is ~500 people, so ~125k pairs,
  // each doing a 192-dim cosine and building a Set: measured at 2.8 SECONDS of
  // blocked JS thread, at app start, to print a single log line. It answers a
  // calibration question — is some pair of clusters obviously the same person —
  // so it belongs behind the same switch as the rest of the calibration work.
  if (FACE_DIAGNOSTICS) {
    for (let first = 0; first < index.people.length; first += 1) {
      for (let second = first + 1; second < index.people.length; second += 1) {
        const a = index.people[first];
        const b = index.people[second];
        if (a.embeddingKind !== b.embeddingKind) continue;
        const bAssets = new Set(b.assetIds);
        closestPairs.push({
          a: a.id,
          b: b.id,
          shared: a.assetIds.reduce(
            (count, assetId) => count + Number(bAssets.has(assetId)),
            0,
          ),
          similarity: cosine(a.centroid, b.centroid),
        });
      }
    }
  }
  const pairSummary = closestPairs
    .sort((a, b) => b.similarity - a.similarity)
    .slice(0, 6)
    .map(
      (pair) =>
        `${pair.a}/${pair.b}:${pair.similarity.toFixed(3)}~${pair.shared}`,
    )
    .join(",");
  const clusterSummary = index.people
    .slice()
    .sort((a, b) => b.faceCount - a.faceCount || a.id.localeCompare(b.id))
    .slice(0, 12)
    .map(
      (person) =>
        `${person.id}:${person.faceCount}/${new Set(person.assetIds).size}`,
    )
    .join(",");
  console.warn(
    `[PhoteoFaceIndex] ${context} identity=${counts.identityObservations} perceptual=${counts.perceptualObservations} duplicateBoxes=${duplicateDetectionsDropped} clusters=${clusterSummary || "none"} closest=${pairSummary || "none"}`,
  );
}

/**
 * Re-runs the REAL detect+align+embed path over a few photos purely to look at
 * what the embedder is actually being fed.
 *
 * Every alignment counter can read healthy while the warp samples the wrong
 * pixels entirely: a mis-scaled landmark set still produces a finite,
 * invertible transform, so nothing reports a failure. And on a library whose
 * scan is complete, no face is ever embedded again, so the counters sit at zero
 * and answer nothing. This forces a handful of embeddings and streams the
 * resulting 112x112 faces to logcat, where they can be reassembled and viewed.
 *
 * Diagnostic only: the observations produced here are DISCARDED, never added to
 * the index, and the photos are not marked processed.
 */
let alignmentProbeDone = false;

export async function probeFaceAlignment(photos = 6, faces = 3): Promise<void> {
  if (!FACE_DIAGNOSTICS) return;
  if (alignmentProbeDone || !isFaceDetectionAvailable()) return;
  alignmentProbeDone = true;
  try {
    const mediaLibrary = await import("expo-media-library/legacy");
    const page = await mediaLibrary.getAssetsAsync({
      first: photos,
      mediaType: [mediaLibrary.MediaType.photo],
      sortBy: [mediaLibrary.SortBy.creationTime],
    });
    captureAlignedSamples(faces);
    traceNextAlignments(faces + 4);
    await scanFaceAssets(page.assets as unknown as FaceScanAsset[], {
      isDetectionAvailable: () => true,
      openFrame: (uri, asset) => openFaceFrame(uri, asset),
      closeFrame: (frame) => closeFaceFrame(frame),
      detectFaces: async (_uri, _asset, frame) =>
        frame ? detectFacesInFrame(frame) : [],
      embedFace: createFaceEmbedding,
    });

    const samples = takeAlignedSamples();
    console.warn(
      `[PhoteoCropProbe] photos=${page.assets.length} samples=${samples.length} ` +
        `path=${JSON.stringify(faceEmbeddingPathCounts())} ` +
        `landmarks=${JSON.stringify(landmarkRejectCounts())} ` +
        `align=${JSON.stringify(faceAlignmentShapeCounts())} ` +
        `frameOrient=${JSON.stringify(frameOrientationCounts())}`,
    );
    // Chunked well under logcat's ~4KB per-entry payload cap.
    const CHUNK = 3000;
    samples.forEach((sample, sampleIndex) => {
      const encoded = encodeBase64(sample);
      const parts = Math.ceil(encoded.length / CHUNK);
      for (let part = 0; part < parts; part += 1) {
        console.warn(
          `[PhoteoCropPix] i=${sampleIndex} bytes=${sample.length} ` +
            `part=${part}/${parts} ${encoded.slice(part * CHUNK, (part + 1) * CHUNK)}`,
        );
      }
    });
  } catch (error) {
    console.warn(`[PhoteoCropProbe] failed: ${String(error).slice(0, 160)}`);
  }
}

/** Emits anonymous centroid diagnostics for on-device clustering calibration. */
export function logFaceIndexDiagnostics(context = "status"): void {
  logEmbeddingPath(context);
  logSimilarityStructure(context);
}

/**
 * Answers the one question a cluster count cannot: are these embeddings
 * discriminative at all?
 *
 * On the owner's real library every face collapsed into ONE identity holding
 * 96.6% of all observations, which means genuinely different people are landing
 * above the 0.62 assignment bar. Two very different causes produce that same
 * symptom, and they need opposite fixes:
 *
 *   - the bar is simply too loose for this embedding space  -> raise it
 *   - the embeddings are degenerate (every face maps to nearly the same vector,
 *     e.g. broken alignment or preprocessing) -> no threshold can ever help
 *
 * A percentile sweep of pairwise cosine separates them immediately. Healthy
 * ArcFace-family embeddings put most random face pairs well below 0.4 with a
 * long tail; a median above ~0.8 means the model is returning near-constant
 * vectors and clustering is not the problem.
 *
 * Runs off the PERSISTED observations, so it needs no rescan, and samples so it
 * stays O(SAMPLE^2) rather than O(n^2) on the JS thread.
 */
const SIMILARITY_SAMPLE = 220;
const SWEEP_SAMPLE = 900;

function sampleObservations(limit: number): FaceObservation[] {
  const identity = index.observations.filter(
    (observation) => observation.embeddingKind === "identity",
  );
  if (identity.length <= limit) return identity;
  // Even stride rather than random: the sample must be identical between runs,
  // or two consecutive diagnostics look like a change in the data.
  const stride = identity.length / limit;
  const sampled: FaceObservation[] = [];
  for (let index = 0; index < limit; index += 1) {
    sampled.push(identity[Math.floor(index * stride)]);
  }
  return sampled;
}

function percentile(sorted: number[], fraction: number): number {
  if (sorted.length === 0) return Number.NaN;
  const position = Math.min(
    sorted.length - 1,
    Math.max(0, Math.round(fraction * (sorted.length - 1))),
  );
  return sorted[position];
}

/**
 * Pairwise cosines between faces from DIFFERENT photos. Two faces in one frame
 * are usually two people, but a repeat detection of one face would bias the low
 * end, so same-photo pairs are skipped.
 */
function impostorSimilarities<T extends { assetId: string; embedding: number[] }>(
  sample: readonly T[],
): number[] {
  const similarities: number[] = [];
  for (let first = 0; first < sample.length; first += 1) {
    for (let second = first + 1; second < sample.length; second += 1) {
      if (sample[first].assetId === sample[second].assetId) continue;
      similarities.push(cosine(sample[first].embedding, sample[second].embedding));
    }
  }
  return similarities.sort((a, b) => a - b);
}

/**
 * Removes the population mean, then re-normalizes.
 *
 * A collapsed-looking embedder has two very different causes that raw cosine
 * cannot tell apart. If every vector carries one large SHARED direction, cosine
 * is dominated by that constant and everyone looks alike even though identity
 * information is still present underneath. If the embedder genuinely encodes
 * nothing, removing the mean leaves noise and the distribution stays unimodal.
 * Centering separates the two: it is the difference between "fixable by
 * whitening" and "the tensors fed to the model are wrong".
 */
function centerObservations<T extends { embedding: number[] }>(
  sample: readonly T[],
): { centered: T[]; meanNorm: number } {
  const dimensions = sample[0].embedding.length;
  const mean = new Array<number>(dimensions).fill(0);
  for (const observation of sample) {
    for (let axis = 0; axis < dimensions; axis += 1) {
      mean[axis] += observation.embedding[axis];
    }
  }
  let meanSquared = 0;
  for (let axis = 0; axis < dimensions; axis += 1) {
    mean[axis] /= sample.length;
    meanSquared += mean[axis] * mean[axis];
  }

  const centered = sample.map((observation) => {
    const shifted = new Array<number>(dimensions);
    let squared = 0;
    for (let axis = 0; axis < dimensions; axis += 1) {
      const value = observation.embedding[axis] - mean[axis];
      shifted[axis] = value;
      squared += value * value;
    }
    const magnitude = Math.sqrt(squared);
    if (!Number.isFinite(magnitude) || magnitude <= Number.EPSILON) {
      return { ...observation, embedding: shifted };
    }
    for (let axis = 0; axis < dimensions; axis += 1) {
      shifted[axis] /= magnitude;
    }
    return { ...observation, embedding: shifted };
  });

  return { centered, meanNorm: Math.sqrt(meanSquared) };
}

function sweepThresholds(
  sample: readonly { assetId: string; embedding: number[] }[],
): string {
  // Spans BOTH spaces on purpose. Raw embeddings carry a shared direction of
  // norm ~0.845, which alone contributes ~0.71 to every cosine, so the useful
  // raw bar sits high (0.8-0.95). Centering removes that offset and drops the
  // impostor median to ~0, so the useful centered bar sits far lower (0.3-0.55,
  // where the measured centered impostor p99 is 0.533). A sweep that starts at
  // 0.62 cannot see the centered optimum at all, and reports over-splitting at
  // every point it samples.
  return [0.3, 0.35, 0.4, 0.45, 0.5, 0.55, 0.62, 0.7, 0.8, 0.9]
    .map((threshold) => {
      const people = clusterFaces(sample as never, {
        threshold,
        identityMergeThreshold: threshold,
      });
      const largest = people.reduce(
        (most, person) => Math.max(most, person.faceCount),
        0,
      );
      return `${threshold}:${people.length}/${largest}`;
    })
    .join(",");
}

export function logSimilarityStructure(context = "status"): void {
  if (!FACE_DIAGNOSTICS) return;
  const sample = sampleObservations(SIMILARITY_SAMPLE);
  if (sample.length < 8) {
    console.warn(`[PhoteoFaceSim] ${context} too few observations (${sample.length})`);
    return;
  }

  const similarities = impostorSimilarities(sample);

  const dimensions = sample[0].embedding.length;
  // Spread of each component across the sample: a degenerate embedder returns
  // nearly the same vector every time, so this collapses toward zero.
  let meanComponentSpread = 0;
  for (let axis = 0; axis < dimensions; axis += 1) {
    let min = Number.POSITIVE_INFINITY;
    let max = Number.NEGATIVE_INFINITY;
    for (const observation of sample) {
      const value = observation.embedding[axis];
      if (value < min) min = value;
      if (value > max) max = value;
    }
    meanComponentSpread += max - min;
  }
  meanComponentSpread /= dimensions || 1;

  const sweep = sampleObservations(SWEEP_SAMPLE);

  console.warn(
    `[PhoteoFaceSim] ${context} pairs=${similarities.length} dims=${dimensions} ` +
      `path=${JSON.stringify(faceEmbeddingPathCounts())} ` +
      `landmarks=${JSON.stringify(landmarkRejectCounts())} ` +
      `align=${JSON.stringify(faceAlignmentShapeCounts())} ` +
      `frameOrient=${JSON.stringify(frameOrientationCounts())} ` +
      `spread=${meanComponentSpread.toFixed(4)} ` +
      `p05=${percentile(similarities, 0.05).toFixed(3)} ` +
      `p50=${percentile(similarities, 0.5).toFixed(3)} ` +
      `p90=${percentile(similarities, 0.9).toFixed(3)} ` +
      `p99=${percentile(similarities, 0.99).toFixed(3)} ` +
      `max=${similarities[similarities.length - 1]?.toFixed(3)} ` +
      `sweep[thr:people/largest]=${sweepThresholds(sweep)}`,
  );

  // Same population with the shared direction removed. If identity survives
  // here, the embedder works and the fix is whitening; if this stays as flat as
  // the raw line, the pixels reaching the model are wrong.
  const { centered, meanNorm } = centerObservations(sample);
  const centeredSimilarities = impostorSimilarities(centered);
  console.warn(
    `[PhoteoFaceSimCentered] ${context} meanNorm=${meanNorm.toFixed(3)} ` +
      `p05=${percentile(centeredSimilarities, 0.05).toFixed(3)} ` +
      `p50=${percentile(centeredSimilarities, 0.5).toFixed(3)} ` +
      `p90=${percentile(centeredSimilarities, 0.9).toFixed(3)} ` +
      `p99=${percentile(centeredSimilarities, 0.99).toFixed(3)} ` +
      `max=${centeredSimilarities[centeredSimilarities.length - 1]?.toFixed(3)} ` +
      `sweep[thr:people/largest]=${sweepThresholds(centerObservations(sweep).centered)}`,
  );
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function validEmbedding(value: unknown): value is number[] {
  return (
    Array.isArray(value) &&
    value.length > 0 &&
    value.every(
      (component) =>
        typeof component === "number" && Number.isFinite(component),
    )
  );
}

function encodeBase64(bytes: Uint8Array): string {
  let result = "";
  for (let offset = 0; offset < bytes.length; offset += 3) {
    const first = bytes[offset];
    const second = bytes[offset + 1];
    const third = bytes[offset + 2];
    result += BASE64_ALPHABET[first >> 2];
    result += BASE64_ALPHABET[((first & 3) << 4) | ((second ?? 0) >> 4)];
    result += offset + 1 < bytes.length
      ? BASE64_ALPHABET[((second & 15) << 2) | ((third ?? 0) >> 6)]
      : "=";
    result += offset + 2 < bytes.length
      ? BASE64_ALPHABET[third & 63]
      : "=";
  }
  return result;
}

/** Symmetric fixed-scale int8 quantization for normalized face embeddings. */
export function quantizeEmbedding(embedding: number[]): string {
  if (!validEmbedding(embedding)) {
    throw new Error("Cannot quantize an invalid face embedding.");
  }
  const bytes = new Uint8Array(embedding.length);
  for (let index = 0; index < embedding.length; index += 1) {
    const quantized = Math.round(Math.max(-1, Math.min(1, embedding[index])) * 127);
    bytes[index] = quantized & 0xff;
  }
  return encodeBase64(bytes);
}

export function dequantizeEmbedding(value: string): number[] {
  const bytes = decodeBase64(value);
  const signed = new Int8Array(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  return Array.from(signed, (component) => component / 127);
}

function storedObservation(value: unknown): value is StoredFaceObservation {
  return (
    isRecord(value) &&
    typeof value.assetId === "string" &&
    typeof value.embedding === "string" &&
    value.embedding.length > 0 &&
    (value.embeddingKind === "identity" || value.embeddingKind === "perceptual") &&
    (value.seedable === undefined || typeof value.seedable === "boolean")
  );
}

function storedPerson(value: unknown): value is StoredPerson {
  return (
    isRecord(value) &&
    typeof value.id === "string" &&
    typeof value.faceCount === "number" &&
    Number.isFinite(value.faceCount) &&
    value.faceCount >= 0 &&
    Array.isArray(value.assetIds) &&
    value.assetIds.every((assetId) => typeof assetId === "string") &&
    typeof value.centroid === "string" &&
    value.centroid.length > 0 &&
    (value.embeddingKind === "identity" || value.embeddingKind === "perceptual")
  );
}

/**
 * Quantized form of every observation already written to disk.
 *
 * The scan checkpoints on a 10-second timer, and each checkpoint re-quantized
 * the ENTIRE growing observation list: 192 floats rounded and base64-packed per
 * face, for every face found so far, several hundred times over a full library.
 * That is quadratic in library size and it is pure repetition — an observation
 * is frozen the moment it is created. Keyed weakly so pruning at the end of a
 * scan drops the cached strings with the observations.
 */
const quantizedObservations = new WeakMap<FaceObservation, string>();

function storedIndex(): StoredFaceIndex {
  return {
    ...index,
    observations: index.observations.map((observation) => {
      let embedding = quantizedObservations.get(observation);
      if (embedding === undefined) {
        embedding = quantizeEmbedding(observation.embedding);
        quantizedObservations.set(observation, embedding);
      }
      return { ...observation, embedding };
    }),
    // Centroids are NOT cached: a person's centroid is recomputed on every
    // assignment and merge, so the object is live where observations are not.
    people: index.people.map((person) => ({
      ...person,
      centroid: quantizeEmbedding(person.centroid),
    })),
  };
}

function validObservation(value: unknown): value is FaceObservation {
  return (
    isRecord(value) &&
    typeof value.assetId === "string" &&
    validEmbedding(value.embedding) &&
    (value.embeddingKind === "identity" || value.embeddingKind === "perceptual") &&
    (value.seedable === undefined || typeof value.seedable === "boolean")
  );
}

export type FaceQualityTier = "seedable" | "assignable";

/**
 * Two-tier identity gate. Discarding the worst 10-20% of faces raises
 * clustering F-score more than any threshold tuning does, because low-quality
 * faces are the BRIDGES that chain two identities into one tile: a blurry
 * 30px head lands near everything, so it links whoever it touches.
 *
 * Yaw is the sharpest of these. ML Kit only returns the full frontal landmark
 * set within a modest yaw range; past roughly +/-36 degrees an eye and a mouth
 * corner disappear, the ArcFace template alignment in ../ml/face-align.ts has
 * nothing to fit, and the resulting embedding is noise.
 */
/** Box short side, in source pixels, required to seed a new person. */
const SEEDABLE_MIN_FACE_PX = 64;
/** Box short side as a fraction of the image's min dimension, to seed. */
const SEEDABLE_MIN_IMAGE_RATIO = 0.04;
/** Max |yaw| (degrees) that reliably keeps all five landmarks in frame. */
const SEEDABLE_MAX_YAW_DEGREES = 30;
/** Assignable faces may join an existing person but never create a tile. */
const ASSIGNABLE_MIN_FACE_PX = 40;
const ASSIGNABLE_MIN_IMAGE_RATIO = 0.03;
/** Past this yaw alignment is impossible, so the face is dropped entirely. */
const ASSIGNABLE_MAX_YAW_DEGREES = 45;

/**
 * Ceiling on what the fraction-of-frame rules may demand in real pixels.
 *
 * The ratio rules are a proxy for "is this person a subject of the photo", but
 * what actually determines embedding quality is pixels on the face and pose.
 * On a 4032x3024 phone photo the 4% seed rule demanded a 121px face, i.e. a
 * subject within about four metres of the camera; someone who only ever appears
 * in wider group shots cleared the assignable bar, contributed nothing, and
 * never got a tile of their own. That is a permanent, silent omission from the
 * People UI, and no amount of rescanning fixes it.
 *
 * MobileFaceNet consumes a 112x112 aligned patch, so past roughly a hundred
 * source pixels the extra resolution is discarded anyway. Cap the ratio rules
 * there: a 96px face is judged on its own merits no matter how large the frame
 * around it is. Phone-photo seed floor moves 121px -> 96px (about 1.6x the face
 * area, roughly four metres -> five and a half). Raise this constant if beta
 * feedback shows background strangers seeding tiles.
 */
const RATIO_RULE_MAX_FACE_PX = 96;

function minimumFacePx(
  absoluteFloor: number,
  ratio: number,
  imageMin: number,
): number {
  return Math.max(
    absoluteFloor,
    Math.min(ratio * imageMin, RATIO_RULE_MAX_FACE_PX),
  );
}

export function faceQualityTier(
  asset: FaceScanAsset,
  box: FaceBox,
): FaceQualityTier | null {
  const shortSide = Math.min(box.width, box.height);
  const imageMin = Math.min(asset.width, asset.height);
  if (
    !Number.isFinite(shortSide) ||
    !Number.isFinite(imageMin) ||
    imageMin <= 0
  ) {
    return null;
  }
  // Head angles are optional metadata. A detector build that omits them must
  // degrade to "unknown pose, judge on size alone" rather than reject the
  // whole library, so an absent yaw reads as frontal.
  const reportedYaw = box.headEulerAngleY;
  const yaw = Number.isFinite(reportedYaw) ? Math.abs(reportedYaw as number) : 0;
  if (
    shortSide <
      minimumFacePx(ASSIGNABLE_MIN_FACE_PX, ASSIGNABLE_MIN_IMAGE_RATIO, imageMin) ||
    yaw > ASSIGNABLE_MAX_YAW_DEGREES
  ) {
    return null;
  }
  return shortSide >=
      minimumFacePx(SEEDABLE_MIN_FACE_PX, SEEDABLE_MIN_IMAGE_RATIO, imageMin) &&
    yaw <= SEEDABLE_MAX_YAW_DEGREES
    ? "seedable"
    : "assignable";
}

/**
 * Removes repeat detections of one face while preserving distinct co-faces.
 *
 * Tied to SAME_PHOTO_EXCEPTION_SIMILARITY on purpose: this rule and the
 * clustering cannot-link both answer "are these two boxes in one photo the same
 * person?", and they must not answer it differently. At the old 0.75 they
 * disagreed across a whole band — clustering treated a same-photo pair at
 * cosine 0.80 as two people who merely posed together, while this function had
 * already deleted one of them as a duplicate detection. Siblings and
 * parent/child pairs land in exactly that band, so the second person was
 * destroyed before clustering ever saw them, which no threshold could undo.
 * Genuine repeat detections of one face (ML Kit re-firing on the same head,
 * a mirror, a photo of a photo) sit far above 0.85.
 */
export function dedupeFaceObservations(
  observations: FaceObservation[],
  similarityThreshold = SAME_PHOTO_EXCEPTION_SIMILARITY,
): FaceObservation[] {
  const kept: FaceObservation[] = [];
  const byAsset = new Map<string, FaceObservation[]>();
  for (const observation of observations) {
    const siblings = byAsset.get(observation.assetId) ?? [];
    const duplicate = siblings.some(
      (candidate) =>
        candidate.embeddingKind === observation.embeddingKind &&
        cosine(candidate.embedding, observation.embedding) >=
          similarityThreshold,
    );
    if (duplicate) continue;
    siblings.push(observation);
    byAsset.set(observation.assetId, siblings);
    kept.push(observation);
  }
  return kept;
}

/**
 * Unit-norms an embedding as it enters the index.
 *
 * MobileFaceNet output is already normalized in ../ml/parseFaceEmbeddingOutput
 * and the perceptual fallback normalizes its own fingerprint, so on the shipped
 * paths this is idempotent — it is a trust boundary, not a fix. It matters
 * because `updateCentroid` takes an unweighted MEAN of whatever arrives: one
 * embedder returning larger-magnitude vectors would drag every centroid toward
 * its faces and inflate cosine against the whole library. `embedFace` is
 * dependency-injected, and a persisted embedding comes back through int8
 * dequantization with its norm slightly off, so the invariant is worth
 * enforcing here rather than assuming it upstream.
 */
function unitEmbedding(embedding: number[]): number[] {
  const magnitude = Math.sqrt(
    embedding.reduce((sum, value) => sum + value * value, 0),
  );
  return Number.isFinite(magnitude) && magnitude > Number.EPSILON
    ? embedding.map((value) => value / magnitude)
    : embedding;
}

function boxIntersection(a: FaceBox, b: FaceBox): number {
  const width = Math.max(
    0,
    Math.min(a.x + a.width, b.x + b.width) - Math.max(a.x, b.x),
  );
  const height = Math.max(
    0,
    Math.min(a.y + a.height, b.y + b.height) - Math.max(a.y, b.y),
  );
  return width * height;
}

/** Suppresses repeated ML Kit boxes without conflating neighboring faces. */
export function dedupeFaceBoxes(boxes: FaceBox[]): FaceBox[] {
  const kept: FaceBox[] = [];
  for (const box of boxes) {
    const area = box.width * box.height;
    const duplicate = kept.some((candidate) => {
      const candidateArea = candidate.width * candidate.height;
      const intersection = boxIntersection(box, candidate);
      const union = area + candidateArea - intersection;
      const iou = union > 0 ? intersection / union : 0;
      const containment =
        Math.min(area, candidateArea) > 0
          ? intersection / Math.min(area, candidateArea)
          : 0;
      const centerDistance = Math.hypot(
        box.x + box.width / 2 - (candidate.x + candidate.width / 2),
        box.y + box.height / 2 - (candidate.y + candidate.height / 2),
      );
      const centerTolerance =
        Math.min(
          Math.max(box.width, box.height),
          Math.max(candidate.width, candidate.height),
        ) * 0.7;
      return (
        iou >= 0.65 ||
        containment >= 0.85 ||
        centerDistance <= centerTolerance
      );
    });
    if (!duplicate) kept.push(box);
  }
  return kept;
}

function validPerson(value: unknown): value is Person {
  return (
    isRecord(value) &&
    typeof value.id === "string" &&
    typeof value.faceCount === "number" &&
    Number.isFinite(value.faceCount) &&
    value.faceCount >= 0 &&
    Array.isArray(value.assetIds) &&
    value.assetIds.every((assetId) => typeof assetId === "string") &&
    validEmbedding(value.centroid) &&
    (value.embeddingKind === "identity" || value.embeddingKind === "perceptual")
  );
}

function trueRecord(value: unknown): value is Record<string, true> {
  return isRecord(value) && Object.values(value).every((entry) => entry === true);
}

function stringRecord(value: unknown): value is Record<string, string> {
  return (
    isRecord(value) &&
    Object.values(value).every((entry) => typeof entry === "string")
  );
}

function parseIndex(contents: string): PersistedFaceIndex | null {
  try {
    const value: unknown = JSON.parse(contents);
    if (
      !isRecord(value) ||
      value.version !== INDEX_VERSION ||
      !Array.isArray(value.observations) ||
      !value.observations.every(storedObservation) ||
      !Array.isArray(value.people) ||
      !value.people.every(storedPerson) ||
      !trueRecord(value.processedAssetIds) ||
      !trueRecord(value.seenAssetIds) ||
      (typeof value.cursor !== "string" && value.cursor !== null) ||
      typeof value.scanComplete !== "boolean" ||
      typeof value.total !== "number" ||
      !Number.isFinite(value.total) ||
      typeof value.threshold !== "number" ||
      !Number.isFinite(value.threshold)
    ) {
      return null;
    }
    const stored = value as unknown as StoredFaceIndex;
    const loaded: PersistedFaceIndex = {
      ...stored,
      observations: stored.observations.map((observation) => ({
        ...observation,
        embedding: dequantizeEmbedding(observation.embedding),
      })),
      people: stored.people.map((person) => ({
        ...person,
        centroid: dequantizeEmbedding(person.centroid),
      })),
      faceThumbUris: stringRecord(value.faceThumbUris)
        ? value.faceThumbUris
        : {},
    };
    return loaded.observations.every(validObservation) &&
      loaded.people.every(validPerson)
      ? loaded
      : null;
  } catch {
    return null;
  }
}

async function fileSystemModule(): Promise<
  typeof import("expo-file-system/legacy")
> {
  return import("expo-file-system/legacy");
}

async function readPersistedIndex(
  fileSystem: typeof import("expo-file-system/legacy"),
  uri: string,
): Promise<PersistedFaceIndex | null> {
  try {
    const readAt = Date.now();
    const raw = await fileSystem.readAsStringAsync(uri);
    const parsedAt = Date.now();
    const parsed = parseIndex(raw);
    // Hydration blocks the JS thread, so the split between reading bytes and
    // parsing them is what says whether the file is too big or the shape is.
    console.warn(
      `[PhoteoFaceIndex] read bytes=${raw.length} readMs=${parsedAt - readAt} ` +
        `parseMs=${Date.now() - parsedAt}`,
    );
    return parsed;
  } catch {
    return null;
  }
}

async function hydrateFaceIndex(): Promise<void> {
  try {
    const fileSystem = await fileSystemModule();
    if (!fileSystem.documentDirectory) {
      return;
    }
    const uri = `${fileSystem.documentDirectory}${INDEX_FILENAME}`;
    const temporary = await readPersistedIndex(fileSystem, `${uri}.tmp`);
    if (temporary) {
      // Recovering from a `.tmp` means the last write was interrupted, so the
      // real file is missing or stale. Stay dirty and rewrite it.
      index = temporary;
      rebuildPersonIdsByAsset();
      return;
    }
    const saved = await readPersistedIndex(fileSystem, uri);
    if (saved) {
      index = saved;
      rebuildPersonIdsByAsset();
      // Just read from disk, so by definition it matches disk. This is what
      // lets an app open that scans nothing skip the multi-second rewrite.
      indexDirty = false;
      persistedShape = indexShape();
    }
  } catch {
    // An in-memory index is still usable when durable storage is unavailable.
  }
}

function rebuildPersonIdsByAsset(): void {
  personIdsByAsset = createPersonIdsByAsset(index.people);
}

/** Builds the reverse lookup once so selecting thousands of assets stays O(N). */
export function createPersonIdsByAsset(
  people: readonly Pick<Person, "id" | "assetIds">[],
): Map<string, string[]> {
  const next = new Map<string, string[]>();
  for (const person of people) {
    for (const assetId of person.assetIds) {
      const personIds = next.get(assetId) ?? [];
      personIds.push(person.id);
      next.set(assetId, personIds);
    }
  }
  for (const personIds of next.values()) personIds.sort();
  return next;
}

/** Hydrates the last crash-safe checkpoint without loading native ML bindings. */
export function loadFaceIndex(): Promise<void> {
  hydration ??= hydrateFaceIndex();
  return hydration;
}

/**
 * Whether the in-memory index has diverged from the file on disk.
 *
 * Persisting is `JSON.stringify` over the whole index: measured at 3161ms for a
 * 3MB index, on the same JS thread that paints the photo grid. A scan pass that
 * finds nothing new used to pay that on every single app open, which is most of
 * what made the Photos tab feel stuck.
 *
 * Skipping a write that was needed loses the user's scan, so this fails safe:
 * it starts dirty, is cleared ONLY by a write that completed, and is re-set by
 * anything that touches the index — including a mutation that lands while a
 * write is already in flight.
 */
let indexDirty = true;
let persistedShape = "";

/**
 * A cheap fingerprint of everything persisted, as a backstop: if a future
 * mutation site forgets to call `markIndexDirty`, a changed count still forces
 * the write. It is a second line of defence, not the primary signal — an
 * in-place edit that preserves every count is exactly what the dirty flag is
 * for.
 */
function indexShape(): string {
  return [
    index.observations.length,
    index.people.length,
    Object.keys(index.processedAssetIds).length,
    Object.keys(index.seenAssetIds).length,
    Object.keys(index.faceThumbUris).length,
    index.threshold,
    index.calibration,
    index.scanComplete,
    index.cursor,
    index.total,
  ].join(":");
}

function markIndexDirty(): void {
  indexDirty = true;
}

/**
 * Whether a persist must actually run. Fails safe in both directions: a dirty
 * index always writes, and an index whose shape moved writes even if nothing
 * called `markIndexDirty` — so the only skipped write is one where the flag and
 * the fingerprint agree that disk is already correct.
 */
export function shouldPersistIndex(
  dirty: boolean,
  shape: string,
  writtenShape: string,
): boolean {
  return dirty || shape !== writtenShape;
}

/**
 * Floor on how often a scan pass rewrites the whole index.
 *
 * Persisting is `JSON.stringify` over everything: measured between 1477ms and
 * 4278ms as the index grew past 4MB, on the thread that paints. Doing it once
 * per 32-photo batch is most of what the scan costs the UI.
 *
 * Dropping a throttled write is safe in a way that dropping most writes is not:
 * scanning is idempotent, so the only thing lost is the `processedAssetIds`
 * marks for the last few batches, and those photos are simply scanned again.
 * Nothing is corrupted and no user-visible state is destroyed. Terminal
 * moments — finishing, cancelling, failing, backgrounding — force the write.
 */
const PERSIST_MIN_INTERVAL_MS = 15000;
let lastPersistAt = 0;

async function persistFaceIndex(force = false): Promise<void> {
  if (!shouldPersistIndex(indexDirty, indexShape(), persistedShape)) {
    traceScanCount("persistSkipped");
    return;
  }
  if (!force && Date.now() - lastPersistAt < PERSIST_MIN_INTERVAL_MS) {
    traceScanCount("persistThrottled");
    return;
  }
  lastPersistAt = Date.now();
  const startedAt = Date.now();
  // Cleared before the await, so a mutation arriving mid-write re-dirties the
  // index and is picked up by the next pass rather than being swallowed.
  indexDirty = false;
  const shapeAtWrite = indexShape();
  try {
    const fileSystem = await fileSystemModule();
    if (!fileSystem.documentDirectory) {
      indexDirty = true;
      return;
    }
    const uri = `${fileSystem.documentDirectory}${INDEX_FILENAME}`;
    const temporaryUri = `${uri}.tmp`;
    await fileSystem.writeAsStringAsync(
      temporaryUri,
      JSON.stringify(storedIndex()),
    );
    await fileSystem.deleteAsync(uri, { idempotent: true });
    await fileSystem.moveAsync({ from: temporaryUri, to: uri });
    persistedShape = shapeAtWrite;
  } catch {
    // A later batch retries; the in-memory query index remains available.
    indexDirty = true;
  } finally {
    traceScanStage("persist", startedAt);
  }
}

export function contentUri(assetId: string): string {
  return `content://media/external/images/media/${assetId}`;
}

function safeThreshold(value: number | undefined): number {
  return Number.isFinite(value)
    ? (value as number)
    : DEFAULT_FACE_INDEX_THRESHOLD;
}

/**
 * The single clustering policy this app ships.
 *
 * Every path that produces people — the full rebuild, the incremental append,
 * and the pure query projection — routes through here, so an offline test that
 * imports it is exercising exactly what runs on the phone. Three call sites
 * each passing their own thresholds is how the merge bar silently drifted
 * below the assignment bar in the first place.
 */
export function faceClusterOptions(
  threshold: number = DEFAULT_FACE_INDEX_THRESHOLD,
): {
  identityMergeThreshold: number;
  perceptualThreshold: number;
  threshold: number;
} {
  const identityThreshold = safeThreshold(threshold);
  return {
    // Never easier than assignment. Assignment errors are transitive and merge
    // errors are unrecoverable, so two averaged centroids at some distance are
    // strictly weaker evidence than two raw faces at that same distance.
    identityMergeThreshold: Math.max(
      identityThreshold,
      FACE_INDEX_IDENTITY_MERGE_THRESHOLD,
    ),
    perceptualThreshold: PERCEPTUAL_FACE_INDEX_THRESHOLD,
    threshold: identityThreshold,
  };
  // Deliberately omits identityLargeClusterMergeThreshold /
  // identityLargeClusterMinFaces: nothing shipped may relax the merge bar for a
  // cluster that is already large. See FACE_INDEX_IDENTITY_MERGE_THRESHOLD.
}

/**
 * Removes the shared direction every embedding carries, then re-normalizes.
 *
 * Measured on a real 13,459-face library: the population mean has norm 0.845,
 * so it alone contributes 0.845^2 = 0.714 to EVERY pairwise cosine — against a
 * measured raw impostor median of 0.725. Virtually all of the apparent
 * similarity between two strangers was that one constant. Subtracting it drops
 * the impostor median from +0.725 to -0.015, which is what a healthy impostor
 * distribution looks like.
 *
 * The mean is FROZEN in the index once computed, and every later face is
 * centered by that same stored vector. Recomputing it as the library grows
 * would silently move every previously stored centroid out of the space its
 * cluster was built in, which is a far worse bug than a slightly stale mean.
 */
function embeddingMean(observations: readonly FaceObservation[]): number[] {
  const dimensions = observations[0]?.embedding.length ?? 0;
  const mean = new Array<number>(dimensions).fill(0);
  if (dimensions === 0 || observations.length === 0) return mean;
  for (const observation of observations) {
    if (observation.embedding.length !== dimensions) continue;
    for (let axis = 0; axis < dimensions; axis += 1) {
      mean[axis] += observation.embedding[axis];
    }
  }
  for (let axis = 0; axis < dimensions; axis += 1) mean[axis] /= observations.length;
  return mean;
}

/** Centers one embedding by the stored mean and re-normalizes to unit length. */
export function centerEmbedding(
  embedding: readonly number[],
  mean: readonly number[] | undefined,
): number[] {
  if (!mean || mean.length !== embedding.length) return embedding.slice();
  const shifted = new Array<number>(embedding.length);
  let squared = 0;
  for (let axis = 0; axis < embedding.length; axis += 1) {
    const value = embedding[axis] - mean[axis];
    shifted[axis] = value;
    squared += value * value;
  }
  const magnitude = Math.sqrt(squared);
  // A face sitting exactly on the population mean has no direction left; keep
  // it as-is rather than dividing by zero and producing NaNs that would poison
  // every centroid it ever joins.
  if (!Number.isFinite(magnitude) || magnitude <= Number.EPSILON) {
    return embedding.slice();
  }
  for (let axis = 0; axis < shifted.length; axis += 1) shifted[axis] /= magnitude;
  return shifted;
}

/** Observations mapped into the centered space the clusterer works in. */
function centeredForClustering(
  observations: readonly FaceObservation[],
): FaceObservation[] {
  const mean = index.embeddingMean;
  if (!mean) return observations.slice();
  return observations.map((observation) => ({
    ...observation,
    embedding: centerEmbedding(observation.embedding, mean),
  }));
}

function peopleFromObservations(
  observations: FaceObservation[],
  threshold = DEFAULT_FACE_INDEX_THRESHOLD,
): Person[] {
  return clusterFaces(centeredForClustering(observations), faceClusterOptions(threshold));
}

export function summariesForPeople(
  people: Person[],
  faceThumbUris: Readonly<Record<string, string>> = {},
  suppressLowSupport = false,
): FaceIndexPerson[] {
  return people
    .filter(
      (person) =>
        person.assetIds.length > 0 &&
        (!suppressLowSupport || person.faceCount >= MIN_VISIBLE_FACE_COUNT),
    )
    .map((person) => ({
      id: person.id,
      faceCount: person.faceCount,
      coverAssetId: person.assetIds[0],
      assetIds: person.assetIds.slice(),
      ...(faceThumbUris[person.id]
        ? { faceThumbUri: faceThumbUris[person.id] }
        : {}),
    }))
    .sort(
      (a, b) =>
        b.faceCount - a.faceCount ||
        a.coverAssetId.localeCompare(b.coverAssetId) ||
        a.id.localeCompare(b.id),
    );
}

/** Pure query projection used by Node tests and the in-memory singleton API. */
export function createFacePeopleQuery(
  observations: FaceObservation[],
  threshold = DEFAULT_FACE_INDEX_THRESHOLD,
  faceThumbUris: Readonly<Record<string, string>> = {},
): FacePeopleQuery {
  const summaries = summariesForPeople(
    peopleFromObservations(observations, threshold),
    faceThumbUris,
  );
  const byId = new Map(summaries.map((person) => [person.id, person]));
  return {
    getPeople: () =>
      summaries.map((person) => ({
        ...person,
        assetIds: person.assetIds.slice(),
      })),
    assetIdsForPerson: (personId) =>
      byId.get(personId)?.assetIds.slice() ?? [],
  };
}

/**
 * Pure, dependency-injected scan unit. Native Expo modules are not evaluated
 * when this helper is imported or tested in Node.
 */
export async function scanFaceAssets(
  assets: FaceScanAsset[],
  dependencies: FaceScanDependencies,
): Promise<FaceObservation[]> {
  try {
    if (!dependencies.isDetectionAvailable() || assets.length === 0) return [];
    const perAsset = Array.from<FaceObservation[]>({ length: assets.length });
    let nextAsset = 0;
    const worker = async (): Promise<void> => {
      for (;;) {
        const assetIndex = nextAsset;
        nextAsset += 1;
        if (assetIndex >= assets.length) return;
        const asset = assets[assetIndex];
        const imageUri = contentUri(asset.id);
        let frame: FaceFrame | null = null;
        try {
          const frameStartedAt = Date.now();
          frame = (await dependencies.openFrame?.(imageUri, asset)) ?? null;
          if (dependencies.openFrame) traceScanStage("frame", frameStartedAt);
          const detectStartedAt = Date.now();
          const detectedBoxes = await dependencies.detectFaces(
            imageUri,
            asset,
            frame,
          );
          traceScanStage("detect", detectStartedAt);
          const boxes = dedupeFaceBoxes(detectedBoxes);
          duplicateDetectionsDropped += detectedBoxes.length - boxes.length;
          const observations: FaceObservation[] = [];
          for (const box of boxes) {
            const qualityTier = faceQualityTier(asset, box);
            if (!qualityTier) continue;
            try {
              const embedStartedAt = Date.now();
              const result = await dependencies.embedFace(
                asset,
                imageUri,
                box,
                frame,
              );
              traceScanStage("embed", embedStartedAt);
              if (
                validEmbedding(result.embedding) &&
                (result.kind === "identity" || result.kind === "perceptual")
              ) {
                const observation: FaceObservation = {
                  assetId: asset.id,
                  embedding: unitEmbedding(result.embedding),
                  embeddingKind: result.kind,
                  seedable: qualityTier === "seedable",
                };
                observations.push(observation);
                if (result.cropUri) {
                  try {
                    dependencies.onFaceCrop?.(observation, result.cropUri);
                  } catch {
                    // Thumbnail bookkeeping is optional scan metadata.
                  }
                }
              } else if (result.cropUri) {
                // Nothing downstream will ever hear about this crop, so the
                // caller's batch cleanup will not see it either. Drop it here.
                await deleteImageFile(result.cropUri);
              }
            } catch {
              // One unreadable crop must not stop other faces or assets.
            }
          }
          perAsset[assetIndex] = dedupeFaceObservations(observations);
        } catch {
          perAsset[assetIndex] = [];
        } finally {
          if (frame) await dependencies.closeFrame?.(frame);
        }
      }
    };
    await Promise.all(
      Array.from(
        { length: Math.min(SCAN_CONCURRENCY, assets.length) },
        worker,
      ),
    );
    return perAsset.flat();
  } catch {
    return [];
  }
}

function paddedCrop(
  asset: FaceScanAsset,
  box: FaceBox,
): { originX: number; originY: number; width: number; height: number } {
  if (
    !Number.isFinite(asset.width) ||
    !Number.isFinite(asset.height) ||
    asset.width < 1 ||
    asset.height < 1
  ) {
    throw new Error("Face crop requires source image dimensions.");
  }

  if (
    !Number.isFinite(box.x) ||
    !Number.isFinite(box.y) ||
    !Number.isFinite(box.width) ||
    !Number.isFinite(box.height) ||
    box.width <= 0 ||
    box.height <= 0
  ) {
    throw new Error("Face crop requires a finite positive box.");
  }

  const assetWidth = Math.floor(asset.width);
  const assetHeight = Math.floor(asset.height);
  const side = Math.max(
    1,
    Math.min(
      assetWidth,
      assetHeight,
      Math.ceil(Math.max(box.width, box.height) * FACE_PADDING_SCALE),
    ),
  );
  const centerX = box.x + box.width / 2;
  const centerY = box.y + box.height / 2;
  const originX = Math.max(
    0,
    Math.min(assetWidth - side, Math.round(centerX - side / 2)),
  );
  const originY = Math.max(
    0,
    Math.min(assetHeight - side, Math.round(centerY - side / 2)),
  );
  return {
    originX,
    originY,
    width: side,
    height: side,
  };
}

function l2Normalize(values: number[]): number[] {
  const magnitude = Math.sqrt(
    values.reduce((sum, value) => sum + value * value, 0),
  );
  if (!Number.isFinite(magnitude) || magnitude <= Number.EPSILON) {
    throw new Error("Cannot normalize an empty perceptual signal.");
  }
  return values.map((value) => value / magnitude);
}

function fingerprintPixels(
  pixels: Uint8Array,
  width: number,
  height: number,
): number[] {
  const embedding: number[] = [];
  for (let gridY = 0; gridY < LUMA_GRID_SIZE; gridY += 1) {
    for (let gridX = 0; gridX < LUMA_GRID_SIZE; gridX += 1) {
      const startX = Math.floor((gridX * width) / LUMA_GRID_SIZE);
      const endX = Math.max(
        startX + 1,
        Math.floor(((gridX + 1) * width) / LUMA_GRID_SIZE),
      );
      const startY = Math.floor((gridY * height) / LUMA_GRID_SIZE);
      const endY = Math.max(
        startY + 1,
        Math.floor(((gridY + 1) * height) / LUMA_GRID_SIZE),
      );
      let luma = 0;
      let count = 0;
      for (let y = startY; y < Math.min(endY, height); y += 1) {
        for (let x = startX; x < Math.min(endX, width); x += 1) {
          const offset = (y * width + x) * 4;
          luma +=
            pixels[offset] * 0.2126 +
            pixels[offset + 1] * 0.7152 +
            pixels[offset + 2] * 0.0722;
          count += 1;
        }
      }
      embedding.push(luma / Math.max(1, count));
    }
  }

  const mean =
    embedding.reduce((sum, value) => sum + value, 0) / embedding.length;
  for (let index = 0; index < embedding.length; index += 1) {
    embedding[index] = (embedding[index] - mean) / 128;
  }

  const histograms = Array.from({ length: 3 }, () =>
    Array<number>(COLOR_BINS).fill(0),
  );
  const pixelCount = width * height;
  for (let offset = 0; offset < pixels.length; offset += 4) {
    for (let channel = 0; channel < 3; channel += 1) {
      const bin = Math.min(
        COLOR_BINS - 1,
        Math.floor((pixels[offset + channel] * COLOR_BINS) / 256),
      );
      histograms[channel][bin] += 1;
    }
  }
  for (const histogram of histograms) {
    for (const count of histogram) {
      embedding.push((count / pixelCount - 1 / COLOR_BINS) * 0.5);
    }
  }
  return l2Normalize(embedding);
}

export function decodeBase64(value: string): Uint8Array {
  const encoded = value.replace(/^data:[^,]*,/u, "").replace(/\s/gu, "");
  const padding = encoded.endsWith("==") ? 2 : encoded.endsWith("=") ? 1 : 0;
  const bytes = new Uint8Array(
    Math.max(0, Math.floor((encoded.length * 3) / 4) - padding),
  );
  let accumulator = 0;
  let availableBits = 0;
  let byteIndex = 0;
  for (let position = 0; position < encoded.length; position += 1) {
    const code = encoded.charCodeAt(position);
    if (code === 0x3d) {
      break; // '='
    }
    const digit = code < 128 ? BASE64_VALUES[code] : -1;
    if (digit < 0) {
      throw new Error("Face thumbnail contains invalid base64 data.");
    }
    accumulator = (accumulator << 6) | digit;
    availableBits += 6;
    if (availableBits >= 8) {
      availableBits -= 8;
      bytes[byteIndex] = (accumulator >>> availableBits) & 0xff;
      byteIndex += 1;
      accumulator &= availableBits === 0 ? 0 : (1 << availableBits) - 1;
    }
  }
  if (byteIndex !== bytes.length || bytes.length === 0) {
    throw new Error("Face thumbnail base64 data is incomplete.");
  }
  return bytes;
}

/**
 * Interim perceptual fingerprint, not an identity-grade face embedding.
 * Upgrade this isolated helper to ArcFace/AdaFace when a safe runtime lands.
 */
type PreparedFaceCrop = {
  uri: string;
  base64?: string;
};

/**
 * Crops the 128px avatar candidate for this face.
 *
 * `base64` is requested only when the identity embedder failed and the
 * perceptual fingerprint actually has to read pixels. It is not a free flag:
 * expo-image-manipulator's `saveAsync` JPEG-encodes the bitmap a SECOND time to
 * produce it, and the caller then pays a base64 decode and a jpeg-js decode in
 * JS. On the healthy path — MobileFaceNet available, which is every shipped
 * build — none of that is needed, because this crop exists purely so a person
 * tile can have a face on it.
 */
async function prepareFaceCrop(
  asset: FaceScanAsset,
  imageUri: FaceImageSource,
  box: FaceBox,
  withPixels: boolean,
): Promise<PreparedFaceCrop> {
  const { ImageManipulator, SaveFormat } = await import("expo-image-manipulator");
  const context = ImageManipulator.manipulate(imageUri);
  try {
    const rendered = await context
      .crop(paddedCrop(asset, box))
      .resize({ width: FACE_THUMBNAIL_SIZE, height: FACE_THUMBNAIL_SIZE })
      .renderAsync();
    try {
      const thumbnail = await rendered.saveAsync({
        base64: withPixels,
        compress: 0.85,
        format: SaveFormat.JPEG,
      });
      if (withPixels && !thumbnail.base64) {
        await deleteFaceCrop(thumbnail.uri);
        throw new Error("Image manipulator returned no face pixels.");
      }
      return { uri: thumbnail.uri, base64: thumbnail.base64 };
    } finally {
      rendered.release();
    }
  } finally {
    context.release();
  }
}

async function deleteFaceCrop(uri: string): Promise<void> {
  try {
    const fileSystem = await fileSystemModule();
    await fileSystem.deleteAsync(uri, { idempotent: true });
  } catch {
    // Image-manipulator cache cleanup is best-effort.
  }
}

function createPerceptualFaceEmbedding(crop: PreparedFaceCrop): number[] {
  if (!crop.base64) {
    throw new Error("The perceptual fallback needs decoded face pixels.");
  }
  const decoded = decodeJpeg(decodeBase64(crop.base64), {
    useTArray: true,
    formatAsRGBA: true,
    tolerantDecoding: true,
    maxResolutionInMP: 1,
    maxMemoryUsageInMB: 8,
  });
  if (decoded.width < 1 || decoded.height < 1 || decoded.data.length < 4) {
    throw new Error("Decoded face thumbnail is empty.");
  }
  return fingerprintPixels(decoded.data, decoded.width, decoded.height);
}

type FaceCropSpace = {
  source: FaceImageSource;
  asset: FaceScanAsset;
  box: FaceBox;
};

/**
 * The same face expressed in the shared frame's pixel space.
 *
 * Every crop helper downstream is scale-covariant, so the image dimensions, the
 * box and the landmarks must be rescaled by ONE factor together — see
 * `scaleFaceBox` and face-detector.test.ts. Rescaling the box but not the
 * landmarks (or vice versa) produces an alignment that is wrong by a constant
 * and looks like nothing at all.
 */
function frameSpaceFace(
  frame: FaceFrame | null | undefined,
  asset: FaceScanAsset,
  box: FaceBox,
): FaceCropSpace | undefined {
  if (!frame) return undefined;
  return {
    // The bitmap makes a crop free; without one the frame's own bounded JPEG is
    // still the same pixels and still far cheaper than the original.
    source: frame.image ?? frame.uri,
    asset: { id: asset.id, width: frame.width, height: frame.height },
    box: scaleFaceBox(box, frame.scale),
  };
}

/** Uses identity-grade MobileFaceNet first, then the legacy visual fallback. */
async function createFaceEmbedding(
  asset: FaceScanAsset,
  imageUri: string,
  box: FaceBox,
  frame?: FaceFrame | null,
): Promise<FaceEmbedding> {
  const original: FaceCropSpace = { source: imageUri, asset, box };
  const framed = frameSpaceFace(frame, asset, box);
  // Identity is the only consumer that needs real resolution: a face already
  // wider than the 112px alignment target loses nothing to the frame, a smaller
  // one would be upscaled into the template and lose detail it will never get
  // back, so that face alone falls back to decoding the original.
  const frameKeepsDetail =
    framed !== undefined &&
    ((frame?.scale ?? 0) >= 1 ||
      Math.min(framed.box.width, framed.box.height) >= MIN_FRAME_EMBED_FACE_PX);
  let embedSpace = frameKeepsDetail && framed ? framed : original;
  // A face too small to embed from the shared 1280px frame used to fall back to
  // the raw content:// URI, which ImageManipulator loads at SIZE_ORIGINAL — a
  // full 12MP decode (~46MB) per face. Measured on device, those decodes also
  // saturate Glide, and since the photo grid is served by the same pipeline the
  // user's thumbnails queue behind them and the tab appears not to load.
  //
  // Nothing here needs the original: it needs the FACE at >=112px. Opening a
  // second bounded frame, sized so the face just clears the template, gets the
  // same pixels for a fraction of the decode. A 200px face in a 4032px photo
  // needs a ~2258px bound, not 4032.
  let detailFrame: FaceFrame | null = null;
  if (!frameKeepsDetail && framed) {
    traceScanCount("smallFaceFullRes");
    const faceSide = Math.max(1, Math.min(box.width, box.height));
    const sourceLong = Math.max(asset.width, asset.height);
    const bound = Math.min(
      sourceLong,
      Math.ceil((sourceLong * MIN_FRAME_EMBED_FACE_PX) / faceSide),
    );
    detailFrame = await openFaceFrame(imageUri, asset, bound);
    const detailSpace = frameSpaceFace(detailFrame, asset, box);
    if (detailSpace) {
      embedSpace = detailSpace;
      traceScanCount("smallFaceBounded");
    }
  }
  const identity = await embedFaceIdentity(
    embedSpace.asset,
    embedSpace.source,
    embedSpace.box,
  );
  const hasIdentity = identity !== undefined && validEmbedding(identity);
  // Deliberately the SAME space as the embedding, not simply the frame. The
  // avatar is a 128px crop, so a face that was too small to embed from the frame
  // is also too small to make a sharp avatar from it, and this crop is the only
  // source a person tile ever gets.
  const cropStartedAt = Date.now();
  // The AVATAR always comes from the frame, never the original.
  //
  // It used to share `embedSpace`, so a face too small to embed from the frame
  // made this decode the full-resolution original a SECOND time — two
  // SIZE_ORIGINAL decodes per face on the small-face path. That is ~93MB of
  // bitmap per face at 12MP, and because Glide serves the photo grid from the
  // same pipeline, it also starved the UI: thumbnails queued behind full-res
  // decodes and the grid appeared not to load at all during a scan.
  //
  // The identity embedding genuinely needs real resolution; a 128px circular
  // tile does not. Upscaling a small face into a small tile is a cosmetic
  // difference, and it is the only thing given up here.
  const cropSpace = framed ?? embedSpace;
  // Released below in the same finally that guards the crop.
  const crop = await prepareFaceCrop(
    cropSpace.asset,
    cropSpace.source,
    cropSpace.box,
    !hasIdentity,
  );
  traceScanStage("crop", cropStartedAt);
  let returned = false;
  try {
    if (hasIdentity) {
      returned = true;
      return {
        embedding: identity as number[],
        kind: "identity",
        cropUri: crop.uri,
      };
    }
    const fallback = createPerceptualFaceEmbedding(crop);
    returned = true;
    return {
      embedding: fallback,
      kind: "perceptual",
      cropUri: crop.uri,
    };
  } finally {
    if (!returned) await deleteFaceCrop(crop.uri);
    // The bounded detail frame is ours alone: the shared frame belongs to the
    // caller, but this one was opened here and must not outlive the face.
    if (detailFrame) await closeFaceFrame(detailFrame);
  }
}

async function persistCoverFaceThumbs(
  candidates: Array<{ observation: FaceObservation; cropUri: string }>,
  assignments: ReadonlyMap<FaceObservation, string>,
): Promise<void> {
  try {
    const fileSystem = await fileSystemModule();
    if (!fileSystem.documentDirectory) {
      return;
    }
    const directoryUri = `${fileSystem.documentDirectory}${FACE_THUMB_DIRECTORY}`;
    await fileSystem.makeDirectoryAsync(directoryUri, { intermediates: true });
    for (const candidate of candidates) {
      try {
        const personId = assignments.get(candidate.observation);
        if (
          !personId ||
          index.faceThumbUris[personId]
        ) {
          continue;
        }
        const destination = `${directoryUri}/${encodeURIComponent(personId)}.jpg`;
        await fileSystem.deleteAsync(destination, { idempotent: true });
        await fileSystem.copyAsync({
          from: candidate.cropUri,
          to: destination,
        });
        index.faceThumbUris[personId] = destination;
        markIndexDirty();
      } catch {
        // A missing cache crop or filesystem failure must not stop the scan.
      }
    }
  } catch {
    // Face avatars are optional; full-frame cover images remain available.
  } finally {
    await Promise.all(candidates.map((candidate) => deleteFaceCrop(candidate.cropUri)));
  }
}

function yieldToEventLoop(): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, 0));
}

function seenCount(): number {
  return Object.keys(index.seenAssetIds).length;
}

/** The space and bar this library should be clustered in, given its size. */
function calibrationForLibrary(): { rule: string; threshold: number; centered: boolean } {
  const centered =
    USE_CENTERED_CLUSTERING &&
    index.observations.length >= CENTERING_MIN_OBSERVATIONS;
  if (centered) {
    // The centered bar has never been measured in this space, so it does not
    // get to claim a calibration it does not have. Nothing runs on this path
    // while USE_CENTERED_CLUSTERING is false.
    return {
      rule: CENTERED_CLUSTER_CALIBRATION,
      threshold: CENTERED_FACE_INDEX_THRESHOLD,
      centered,
    };
  }
  // Measured from this library's own same-photo pairs, falling back to the
  // cold-start bar until there are enough of them to be a measurement.
  const calibrated = calibrateThreshold(
    index.observations,
    DEFAULT_FACE_INDEX_THRESHOLD,
  );
  return { rule: CLUSTER_CALIBRATION, threshold: calibrated.threshold, centered };
}

function rebuildPeople(requested?: number): void {
  const calibration = calibrationForLibrary();
  index.calibration = calibration.rule;
  markIndexDirty();
  // Recomputed only here, where every person is rebuilt in the same pass, so no
  // stored centroid is ever left in a different space than the mean it used.
  index.embeddingMean = calibration.centered
    ? embeddingMean(index.observations)
    : undefined;
  index.threshold = safeThreshold(requested ?? calibration.threshold);
  index.people = peopleFromObservations(index.observations, index.threshold);
  markIndexDirty();
  rebuildPersonIdsByAsset();
}

/**
 * Re-clusters the faces already on disk when a new build changes the clustering
 * calibration, WITHOUT forcing a re-scan.
 *
 * The tempting way to make a threshold change take effect is to bump
 * INDEX_VERSION. That is a trap: `parseIndex` rejects the entire file on a
 * version mismatch, so it would throw away all 11k persisted embeddings and
 * re-scan the whole library — hours of work to apply a one-line constant. The
 * observations are the expensive artifact; the clustering over them is cheap and
 * entirely derived, so it can simply be recomputed on load.
 *
 * Returns true when it re-clustered, so the caller can persist the new grouping.
 */
function reclusterIfCalibrationChanged(threshold?: number): boolean {
  if (index.observations.length === 0) return false;
  const calibration = calibrationForLibrary();
  const wanted = safeThreshold(threshold ?? calibration.threshold);
  const previousThreshold = index.threshold;
  const previousCalibration = index.calibration;
  // Hysteresis, not float tolerance. The bar is now a measurement that drifts a
  // little every time the library grows, and re-clustering every face because
  // it moved by 0.001 would rebuild the whole grouping on each scan for a
  // change no one can see. Only a move big enough to reassign a face counts.
  const sameBar = Math.abs(previousThreshold - wanted) < RECALIBRATION_HYSTERESIS;
  const sameRule = previousCalibration === calibration.rule;
  if (sameBar && sameRule) return false;

  const previousPeople = index.people.length;
  rebuildPeople(wanted);
  console.warn(
    `[PhoteoFaceIndex] recalibrated bar ${previousThreshold.toFixed(3)}->` +
      `${index.threshold.toFixed(3)} rule ${previousCalibration ?? "legacy"}->` +
      `${index.calibration} people ${previousPeople}->${index.people.length} ` +
      `over ${index.observations.length} persisted faces (no re-scan)`,
  );
  return true;
}

function appendPeople(observations: FaceObservation[]): Map<FaceObservation, string> {
  const assignments = new Map<FaceObservation, string>();
  // Marked before the call, not after: onMerge mutates faceThumbUris as it
  // goes, so a throw partway through still leaves the index dirty.
  markIndexDirty();
  index.people = extendFaceClusters(index.people, centeredForClustering(observations), {
    ...faceClusterOptions(index.threshold),
    onAssign: (observation, personId) => assignments.set(observation, personId),
    onMerge: (absorbedPersonId, survivingPersonId) => {
      for (const [observation, personId] of assignments) {
        if (personId === absorbedPersonId) {
          assignments.set(observation, survivingPersonId);
        }
      }
      if (
        !index.faceThumbUris[survivingPersonId] &&
        index.faceThumbUris[absorbedPersonId]
      ) {
        index.faceThumbUris[survivingPersonId] =
          index.faceThumbUris[absorbedPersonId];
      }
      delete index.faceThumbUris[absorbedPersonId];
    },
    threshold: index.threshold,
    perceptualThreshold: PERCEPTUAL_FACE_INDEX_THRESHOLD,
  });
  rebuildPersonIdsByAsset();
  return assignments;
}

function notifyFaceProgress(done: number, total: number): void {
  for (const subscriber of progressSubscribers) {
    try {
      subscriber(done, total);
    } catch {
      // A screen callback cannot interrupt the shared scan.
    }
  }
}

async function watchAppState(
  control: { cancelled: boolean; foreground: boolean },
): Promise<() => void> {
  try {
    const { AppState } = await import("react-native");
    control.foreground = AppState.currentState === "active";
    const subscription = AppState.addEventListener("change", (state) => {
      const wasForeground = control.foreground;
      control.foreground = state === "active";
      // Leaving the app is the moment a throttled write has to land: the
      // process can be killed while backgrounded, and everything scanned since
      // the last write would otherwise have to be scanned again.
      if (wasForeground && !control.foreground) void persistFaceIndex(true);
    });
    return () => subscription.remove();
  } catch {
    control.foreground = true;
    return () => undefined;
  }
}

async function waitForForeground(
  control: { cancelled: boolean; foreground: boolean },
): Promise<boolean> {
  while (!control.cancelled && !control.foreground) {
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  return !control.cancelled;
}

async function runBuild(
  opts: BuildFaceIndexOptions,
  control: { cancelled: boolean; foreground: boolean },
): Promise<void> {
  const stopWatching = await watchAppState(control);
  try {
    await loadFaceIndex();
    // A build can ship a different clustering calibration (a new threshold, or
    // a corrected linkage). Apply it to the faces already on disk rather than
    // bumping INDEX_VERSION, which would discard every embedding and re-scan
    // the whole library to change one constant.
    if (reclusterIfCalibrationChanged(opts.threshold)) {
      await persistFaceIndex();
    }
    if (!(await waitForForeground(control))) {
      await persistFaceIndex();
      return;
    }
    if (!isFaceDetectionAvailable()) {
      index = { ...emptyIndex(), scanComplete: true };
      markIndexDirty();
      rebuildPersonIdsByAsset();
      await persistFaceIndex();
      notifyFaceProgress(0, 0);
      return;
    }

    const mediaLibrary = await import("expo-media-library/legacy");
    let incrementalTarget: number | null = null;
    if (index.scanComplete) {
      let head: Awaited<ReturnType<typeof mediaLibrary.getAssetsAsync>>;
      try {
        head = await mediaLibrary.getAssetsAsync({
          first: PAGE_SIZE,
          mediaType: [mediaLibrary.MediaType.photo],
          sortBy: [mediaLibrary.SortBy.creationTime],
        });
      } catch {
        return;
      }
      const processed = Object.keys(index.processedAssetIds).length;
      incrementalTarget = incrementalScanTarget(
        head.totalCount,
        processed,
        head.assets.map((asset) => asset.id),
        (assetId) => Object.hasOwn(index.processedAssetIds, assetId),
      );
      index.total = head.totalCount;
      if (incrementalTarget === 0) {
        // Nothing new to scan is exactly when the FULL diagnostics are cheapest
        // and most useful: every embedding is already on disk, so the similarity
        // structure and the threshold sweep can be computed without touching a
        // single photo. Logging only the cheap line here meant the one run that
        // could calibrate for free was the one run that reported nothing.
        await probeFaceAlignment();
        logFaceIndexDiagnostics("hydrated");
        return;
      }
      index.cursor = null;
      index.scanComplete = false;
      markIndexDirty();
      await persistFaceIndex();
    }

    let after = index.cursor ?? undefined;
    let hasNextPage = true;
    let newlyProcessed = 0;
    let targetReached = false;
    let assetsSinceCheckpoint = 0;
    let lastCheckpointAt = Date.now();
    notifyFaceProgress(seenCount(), index.total);

    while (hasNextPage && !targetReached) {
      if (!(await waitForForeground(control))) {
        await persistFaceIndex();
        return;
      }
      let page: Awaited<ReturnType<typeof mediaLibrary.getAssetsAsync>>;
      try {
        page = await mediaLibrary.getAssetsAsync({
          first: PAGE_SIZE,
          after,
          mediaType: [mediaLibrary.MediaType.photo],
          sortBy: [mediaLibrary.SortBy.creationTime],
        });
      } catch {
        await persistFaceIndex();
        return;
      }

      index.total = page.totalCount;
      for (let start = 0; start < page.assets.length; start += SCAN_BATCH_SIZE) {
        if (!(await waitForForeground(control))) {
          await persistFaceIndex();
          return;
        }
        const batch = page.assets.slice(start, start + SCAN_BATCH_SIZE);
        const pending = batch.filter(
          (asset) => !Object.hasOwn(index.processedAssetIds, asset.id),
        );
        newlyProcessed += pending.length;
        const faceCropCandidates: Array<{
          observation: FaceObservation;
          cropUri: string;
        }> = [];
        const observations = await scanFaceAssets(pending, {
          isDetectionAvailable: () => true,
          openFrame: (uri, asset) => openFaceFrame(uri, asset),
          closeFrame: (frame) => closeFaceFrame(frame),
          // A frame that would not open is an unreadable asset: the previous
          // code reached the same empty result through detectFaces' own guard.
          detectFaces: async (_uri, _asset, frame) =>
            frame ? detectFacesInFrame(frame) : [],
          embedFace: createFaceEmbedding,
          onFaceCrop: (observation, cropUri) => {
            faceCropCandidates.push({ observation, cropUri });
          },
        });
        traceScanCount("photos", pending.length);
        traceScanCount("faces", observations.length);
        index.observations.push(...observations);
        markIndexDirty();
        const assignments = appendPeople(observations);
        await persistCoverFaceThumbs(faceCropCandidates, assignments);
        for (const asset of pending) {
          index.processedAssetIds[asset.id] = true;
          markIndexDirty();
        }
        for (const asset of batch) {
          index.seenAssetIds[asset.id] = true;
        }
        assetsSinceCheckpoint += batch.length;
        if (
          assetsSinceCheckpoint >= CHECKPOINT_ASSETS ||
          Date.now() - lastCheckpointAt >= CHECKPOINT_INTERVAL_MS
        ) {
          await persistFaceIndex();
          assetsSinceCheckpoint = 0;
          lastCheckpointAt = Date.now();
        }
        notifyFaceProgress(Math.min(seenCount(), index.total), index.total);
        const trace = takeScanTrace();
        if (trace) console.warn(`[PhoteoFaceScan] ${trace}`);
        await yieldToEventLoop();
        if (
          incrementalTarget !== null &&
          newlyProcessed >= incrementalTarget
        ) {
          targetReached = true;
          break;
        }
      }

      after = page.endCursor;
      index.cursor = after;
      hasNextPage = page.hasNextPage;
      if (page.assets.length === 0 && hasNextPage) {
        await persistFaceIndex(true);
        return;
      }
    }

    if (control.cancelled) {
      await persistFaceIndex(true);
      return;
    }

    index.observations = index.observations.filter((observation) =>
      Object.hasOwn(index.seenAssetIds, observation.assetId),
    );
    index.processedAssetIds = Object.fromEntries(
      Object.keys(index.processedAssetIds)
        .filter((assetId) => Object.hasOwn(index.seenAssetIds, assetId))
        .map((assetId) => [assetId, true] as const),
    );
    rebuildPeople(opts.threshold ?? index.threshold);
    index.cursor = null;
    index.scanComplete = true;
    markIndexDirty();
    index.total = seenCount();
    await persistFaceIndex(true);
    logEmbeddingPath("scan complete");
    notifyFaceProgress(index.total, index.total);
  } catch {
    await persistFaceIndex(true);
  } finally {
    stopWatching();
  }
}

/**
 * Resumably scans every library photo. No detector, asset, crop, paging, or
 * persistence failure is allowed to reject this promise.
 */
export function buildFaceIndex(
  opts: BuildFaceIndexOptions = {},
): Promise<void> {
  if (opts.onProgress) progressSubscribers.add(opts.onProgress);
  if (activeBuild) {
    if (opts.onProgress) {
      opts.onProgress(Math.min(seenCount(), index.total), index.total);
    }
    return activeBuild;
  }
  const control = { cancelled: false, foreground: true };
  activeScanControl = control;
  activeBuild = runBuild(opts, control).finally(() => {
    activeBuild = null;
    activeScanControl = null;
    progressSubscribers.clear();
  });
  return activeBuild;
}

/** Stops the active face scan after its current bounded batch is settled. */
export function stopFaceIndexBuild(): void {
  if (activeScanControl) activeScanControl.cancelled = true;
}

export function getPeople(): FaceIndexPerson[] {
  return summariesForPeople(index.people, index.faceThumbUris, true);
}

export function assetIdsForPerson(personId: string): string[] {
  return (
    index.people.find((person) => person.id === personId)?.assetIds.slice() ?? []
  );
}

/** High-confidence local person clusters present in one asset. */
export function personIdsForAsset(assetId: string): string[] {
  return personIdsByAsset.get(assetId)?.slice() ?? [];
}

export function faceIndexStatus(): FaceIndexStatus {
  return {
    ...observationCounts(),
    scanned: Math.min(seenCount(), index.total),
    total: index.total,
    people: index.people.length,
  };
}

export { isFaceDetectionAvailable };
