import { decode as decodeJpeg } from "jpeg-js";

// Explicit extensions keep this pure module importable by Node's TS test runner.
// @ts-expect-error TypeScript bundler resolution normally omits source extensions.
import { DEFAULT_PERCEPTUAL_THRESHOLD, clusterFaces, cosine, extendFaceClusters } from "./face-cluster.ts";
// @ts-expect-error TypeScript bundler resolution normally omits source extensions.
import { detectFaces, isFaceDetectionAvailable, type FaceBox } from "./face-detector.ts";
// @ts-expect-error Node's TypeScript runner requires the source extension.
import { embedFaceIdentity } from "../ml/facenet.ts";
// @ts-expect-error Node's TypeScript runner requires the source extension.
import { incrementalScanTarget } from "../import/incremental-index.ts";
import type { FaceEmbeddingKind, FaceObservation, Person } from "./types";

// v18 stores aligned embeddings as int8/base64. Older versions contain
// unaligned embeddings and must be rebuilt rather than migrated across spaces.
const INDEX_VERSION = 18;
const INDEX_FILENAME = "face-index.json";
const FACE_THUMB_DIRECTORY = "face-thumbnails";
const PAGE_SIZE = 100;
const SCAN_BATCH_SIZE = 32;
const SCAN_CONCURRENCY = 2;
const CHECKPOINT_ASSETS = 50;
const CHECKPOINT_INTERVAL_MS = 10_000;
const FACE_THUMBNAIL_SIZE = 128;
const LUMA_GRID_SIZE = 8;
const COLOR_BINS = 4;
const FACE_PADDING_SCALE = 1.3;
const BASE64_ALPHABET =
  "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";

/** ArcFace/MobileFaceNet-space cosine threshold for high-precision identity. */
export const DEFAULT_FACE_INDEX_THRESHOLD = 0.62;
export const FACE_INDEX_IDENTITY_MERGE_THRESHOLD = 0.37;
export const FACE_INDEX_LARGE_CLUSTER_MERGE_THRESHOLD = 0.3;
export const FACE_INDEX_LARGE_CLUSTER_MIN_FACES = 10;
export const PERCEPTUAL_FACE_INDEX_THRESHOLD = DEFAULT_PERCEPTUAL_THRESHOLD;

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
  detectFaces: (
    imageUri: string,
    source?: FaceScanAsset,
  ) => Promise<FaceBox[]>;
  embedFace: (
    asset: FaceScanAsset,
    imageUri: string,
    box: FaceBox,
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

/** Emits anonymous centroid diagnostics for on-device clustering calibration. */
export function logFaceIndexDiagnostics(context = "status"): void {
  logEmbeddingPath(context);
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

function storedIndex(): StoredFaceIndex {
  return {
    ...index,
    observations: index.observations.map((observation) => ({
      ...observation,
      embedding: quantizeEmbedding(observation.embedding),
    })),
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
  const imageRatio = shortSide / imageMin;
  // Head angles are optional metadata. A detector build that omits them must
  // degrade to "unknown pose, judge on size alone" rather than reject the
  // whole library, so an absent yaw reads as frontal.
  const reportedYaw = box.headEulerAngleY;
  const yaw = Number.isFinite(reportedYaw) ? Math.abs(reportedYaw as number) : 0;
  if (
    shortSide < ASSIGNABLE_MIN_FACE_PX ||
    imageRatio < ASSIGNABLE_MIN_IMAGE_RATIO ||
    yaw > ASSIGNABLE_MAX_YAW_DEGREES
  ) {
    return null;
  }
  return shortSide >= SEEDABLE_MIN_FACE_PX &&
    imageRatio >= SEEDABLE_MIN_IMAGE_RATIO &&
    yaw <= SEEDABLE_MAX_YAW_DEGREES
    ? "seedable"
    : "assignable";
}

/** Removes repeat detections of one face while preserving distinct co-faces. */
export function dedupeFaceObservations(
  observations: FaceObservation[],
  similarityThreshold = 0.75,
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
    return parseIndex(await fileSystem.readAsStringAsync(uri));
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
      index = temporary;
      rebuildPersonIdsByAsset();
      return;
    }
    const saved = await readPersistedIndex(fileSystem, uri);
    if (saved) {
      index = saved;
      rebuildPersonIdsByAsset();
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

async function persistFaceIndex(): Promise<void> {
  try {
    const fileSystem = await fileSystemModule();
    if (!fileSystem.documentDirectory) {
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
  } catch {
    // A later batch retries; the in-memory query index remains available.
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

function peopleFromObservations(
  observations: FaceObservation[],
  threshold = DEFAULT_FACE_INDEX_THRESHOLD,
  identityMergeThreshold = threshold,
): Person[] {
  return clusterFaces(observations, {
    identityLargeClusterMergeThreshold:
      FACE_INDEX_LARGE_CLUSTER_MERGE_THRESHOLD,
    identityLargeClusterMinFaces: FACE_INDEX_LARGE_CLUSTER_MIN_FACES,
    identityMergeThreshold,
    threshold: safeThreshold(threshold),
    perceptualThreshold: PERCEPTUAL_FACE_INDEX_THRESHOLD,
  });
}

function summariesForPeople(
  people: Person[],
  faceThumbUris: Readonly<Record<string, string>> = {},
  suppressLowSupport = false,
): FaceIndexPerson[] {
  const largestCluster = people.reduce(
    (largest, person) => Math.max(largest, person.faceCount),
    0,
  );
  const visibleFloor = Math.max(3, Math.ceil(largestCluster * 0.1));
  return people
    .filter(
      (person) =>
        person.assetIds.length > 0 &&
        (!suppressLowSupport || person.faceCount >= visibleFloor),
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
        try {
          const detectedBoxes = await dependencies.detectFaces(imageUri, asset);
          const boxes = dedupeFaceBoxes(detectedBoxes);
          duplicateDetectionsDropped += detectedBoxes.length - boxes.length;
          const observations: FaceObservation[] = [];
          for (const box of boxes) {
            const qualityTier = faceQualityTier(asset, box);
            if (!qualityTier) continue;
            try {
              const result = await dependencies.embedFace(asset, imageUri, box);
              if (
                validEmbedding(result.embedding) &&
                (result.kind === "identity" || result.kind === "perceptual")
              ) {
                const observation: FaceObservation = {
                  assetId: asset.id,
                  embedding: result.embedding,
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
              }
            } catch {
              // One unreadable crop must not stop other faces or assets.
            }
          }
          perAsset[assetIndex] = dedupeFaceObservations(observations);
        } catch {
          perAsset[assetIndex] = [];
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

function decodeBase64(value: string): Uint8Array {
  const encoded = value.replace(/^data:[^,]*,/u, "").replace(/\s/gu, "");
  const padding = encoded.endsWith("==") ? 2 : encoded.endsWith("=") ? 1 : 0;
  const bytes = new Uint8Array(
    Math.max(0, Math.floor((encoded.length * 3) / 4) - padding),
  );
  let accumulator = 0;
  let availableBits = 0;
  let byteIndex = 0;
  for (const character of encoded) {
    if (character === "=") {
      break;
    }
    const digit = BASE64_ALPHABET.indexOf(character);
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
  base64: string;
};

async function prepareFaceCrop(
  asset: FaceScanAsset,
  imageUri: string,
  box: FaceBox,
): Promise<PreparedFaceCrop> {
  const imageManipulator = await import("expo-image-manipulator");
  let thumbnailUri: string | undefined;
  try {
    const thumbnail = await imageManipulator.manipulateAsync(
      imageUri,
      [
        { crop: paddedCrop(asset, box) },
        {
          resize: {
            width: FACE_THUMBNAIL_SIZE,
            height: FACE_THUMBNAIL_SIZE,
          },
        },
      ],
      {
        base64: true,
        compress: 0.85,
        format: imageManipulator.SaveFormat.JPEG,
      },
    );
    thumbnailUri = thumbnail.uri;
    if (!thumbnail.base64) {
      throw new Error("Image manipulator returned no face pixels.");
    }
    return { uri: thumbnail.uri, base64: thumbnail.base64 };
  } catch (error) {
    if (thumbnailUri) await deleteFaceCrop(thumbnailUri);
    throw error;
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

/** Uses identity-grade MobileFaceNet first, then the legacy visual fallback. */
async function createFaceEmbedding(
  asset: FaceScanAsset,
  imageUri: string,
  box: FaceBox,
): Promise<FaceEmbedding> {
  const identity = await embedFaceIdentity(asset, imageUri, box);
  const crop = await prepareFaceCrop(asset, imageUri, box);
  let returned = false;
  try {
    if (identity && validEmbedding(identity)) {
      returned = true;
      return { embedding: identity, kind: "identity", cropUri: crop.uri };
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

function rebuildPeople(threshold: number): void {
  index.threshold = safeThreshold(threshold);
  index.people = peopleFromObservations(
    index.observations,
    index.threshold,
    FACE_INDEX_IDENTITY_MERGE_THRESHOLD,
  );
  rebuildPersonIdsByAsset();
}

function appendPeople(observations: FaceObservation[]): Map<FaceObservation, string> {
  const assignments = new Map<FaceObservation, string>();
  index.people = extendFaceClusters(index.people, observations, {
    identityLargeClusterMergeThreshold:
      FACE_INDEX_LARGE_CLUSTER_MERGE_THRESHOLD,
    identityLargeClusterMinFaces: FACE_INDEX_LARGE_CLUSTER_MIN_FACES,
    identityMergeThreshold: FACE_INDEX_IDENTITY_MERGE_THRESHOLD,
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
      control.foreground = state === "active";
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
    if (!(await waitForForeground(control))) {
      await persistFaceIndex();
      return;
    }
    if (!isFaceDetectionAvailable()) {
      index = { ...emptyIndex(), scanComplete: true };
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
        logEmbeddingPath("hydrated");
        return;
      }
      index.cursor = null;
      index.scanComplete = false;
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
          detectFaces: (uri, asset) => detectFaces(uri, asset),
          embedFace: createFaceEmbedding,
          onFaceCrop: (observation, cropUri) => {
            faceCropCandidates.push({ observation, cropUri });
          },
        });
        index.observations.push(...observations);
        const assignments = appendPeople(observations);
        await persistCoverFaceThumbs(faceCropCandidates, assignments);
        for (const asset of pending) {
          index.processedAssetIds[asset.id] = true;
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
        await persistFaceIndex();
        return;
      }
    }

    if (control.cancelled) {
      await persistFaceIndex();
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
    index.total = seenCount();
    await persistFaceIndex();
    logEmbeddingPath("scan complete");
    notifyFaceProgress(index.total, index.total);
  } catch {
    await persistFaceIndex();
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
