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

// v17 applies the final device-calibrated overlap boundary to v16 locally.
const INDEX_VERSION = 17;
const MIGRATABLE_INDEX_VERSION = 16;
const INDEX_FILENAME = "face-index.json";
const FACE_THUMB_DIRECTORY = "face-thumbnails";
const PAGE_SIZE = 100;
// Face crop fingerprinting runs on the JS thread after native detection. Keep
// batches small so a large first scan cannot starve taps and navigation.
const SCAN_BATCH_SIZE = 1;
const FACE_THUMBNAIL_SIZE = 128;
const LUMA_GRID_SIZE = 8;
const COLOR_BINS = 4;
const FACE_PADDING_SCALE = 1.3;
const BASE64_ALPHABET =
  "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";

/** ArcFace/MobileFaceNet-space cosine threshold for high-precision identity. */
export const DEFAULT_FACE_INDEX_THRESHOLD = 0.5;
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
  detectFaces: (imageUri: string) => Promise<FaceBox[]>;
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

function validObservation(value: unknown): value is FaceObservation {
  return (
    isRecord(value) &&
    typeof value.assetId === "string" &&
    validEmbedding(value.embedding) &&
    (value.embeddingKind === "identity" || value.embeddingKind === "perceptual")
  );
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
      (value.version !== INDEX_VERSION &&
        value.version !== MIGRATABLE_INDEX_VERSION) ||
      !Array.isArray(value.observations) ||
      !value.observations.every(validObservation) ||
      !Array.isArray(value.people) ||
      !value.people.every(validPerson) ||
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
    const loaded = {
      ...(value as Omit<PersistedFaceIndex, "faceThumbUris">),
      faceThumbUris: stringRecord(value.faceThumbUris)
        ? value.faceThumbUris
        : {},
    };
    if (value.version === MIGRATABLE_INDEX_VERSION) {
      const observations = dedupeFaceObservations(loaded.observations);
      return {
        ...loaded,
        version: INDEX_VERSION,
        observations,
        people: peopleFromObservations(
          observations,
          loaded.threshold,
          FACE_INDEX_IDENTITY_MERGE_THRESHOLD,
        ),
      };
    }
    return loaded;
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
      return;
    }
    const saved = await readPersistedIndex(fileSystem, uri);
    if (saved) {
      index = saved;
    }
  } catch {
    // An in-memory index is still usable when durable storage is unavailable.
  }
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
    await fileSystem.writeAsStringAsync(temporaryUri, JSON.stringify(index));
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
    if (!dependencies.isDetectionAvailable()) {
      return [];
    }

    const perAsset = await Promise.all(
      assets.map(async (asset): Promise<FaceObservation[]> => {
        const imageUri = contentUri(asset.id);
        try {
          const detectedBoxes = await dependencies.detectFaces(imageUri);
          const boxes = dedupeFaceBoxes(detectedBoxes);
          duplicateDetectionsDropped += detectedBoxes.length - boxes.length;
          const observations: FaceObservation[] = [];
          for (const box of boxes) {
            try {
              const result = await dependencies.embedFace(
                asset,
                imageUri,
                box,
              );
              if (
                validEmbedding(result.embedding) &&
                (result.kind === "identity" || result.kind === "perceptual")
              ) {
                const observation: FaceObservation = {
                  assetId: asset.id,
                  embedding: result.embedding,
                  embeddingKind: result.kind,
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
          return dedupeFaceObservations(observations);
        } catch {
          return [];
        }
      }),
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
  if (!thumbnail.base64) {
    throw new Error("Image manipulator returned no face pixels.");
  }
  return { uri: thumbnail.uri, base64: thumbnail.base64 };
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
  const crop = await prepareFaceCrop(asset, imageUri, box);
  const identity = await embedFaceIdentity(
    { width: FACE_THUMBNAIL_SIZE, height: FACE_THUMBNAIL_SIZE },
    crop.uri,
    {
      x: 0,
      y: 0,
      width: FACE_THUMBNAIL_SIZE,
      height: FACE_THUMBNAIL_SIZE,
    },
  );
  if (identity && validEmbedding(identity)) {
    return { embedding: identity, kind: "identity", cropUri: crop.uri };
  }
  return {
    embedding: createPerceptualFaceEmbedding(crop),
    kind: "perceptual",
    cropUri: crop.uri,
  };
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
  return assignments;
}

async function runBuild(opts: BuildFaceIndexOptions): Promise<void> {
  try {
    await loadFaceIndex();
    if (!isFaceDetectionAvailable()) {
      index = { ...emptyIndex(), scanComplete: true };
      await persistFaceIndex();
      opts.onProgress?.(0, 0);
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
    opts.onProgress?.(seenCount(), index.total);

    while (hasNextPage && !targetReached) {
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
          detectFaces,
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
        await persistFaceIndex();
        opts.onProgress?.(Math.min(seenCount(), index.total), index.total);
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
      await persistFaceIndex();
      if (page.assets.length === 0 && hasNextPage) {
        return;
      }
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
    opts.onProgress?.(index.total, index.total);
  } catch {
    await persistFaceIndex();
  }
}

/**
 * Resumably scans every library photo. No detector, asset, crop, paging, or
 * persistence failure is allowed to reject this promise.
 */
export function buildFaceIndex(
  opts: BuildFaceIndexOptions = {},
): Promise<void> {
  if (activeBuild) {
    return activeBuild;
  }
  activeBuild = runBuild(opts).finally(() => {
    activeBuild = null;
  });
  return activeBuild;
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
  return index.people
    .filter((person) => person.assetIds.includes(assetId))
    .map((person) => person.id)
    .sort();
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
