import { decode as decodeJpeg } from "jpeg-js";

// Explicit extensions keep this pure module importable by Node's TS test runner.
// @ts-expect-error TypeScript bundler resolution normally omits source extensions.
import { clusterFaces } from "./face-cluster.ts";
// @ts-expect-error TypeScript bundler resolution normally omits source extensions.
import { detectFaces, isFaceDetectionAvailable, type FaceBox } from "./face-detector.ts";
import type { FaceObservation, Person } from "./types";

const INDEX_VERSION = 1;
const INDEX_FILENAME = "face-index.json";
const PAGE_SIZE = 100;
const SCAN_BATCH_SIZE = 8;
const THUMBNAIL_SIZE = 32;
const LUMA_GRID_SIZE = 8;
const COLOR_BINS = 4;
const FACE_PADDING_SCALE = 1.3;
const BASE64_ALPHABET =
  "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";

/**
 * Conservative for perceptual fingerprints: synthetic calibration keeps
 * cosine~0.94 near variants together while splitting cosine~0.8 examples.
 * This is intentionally stricter than the core's ArcFace-oriented default.
 */
export const DEFAULT_FACE_INDEX_THRESHOLD = 0.92;

export type FaceIndexPerson = {
  id: string;
  faceCount: number;
  coverAssetId: string;
  assetIds: string[];
};

export type FaceIndexStatus = {
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
  ) => Promise<number[]>;
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
  };
}

let index = emptyIndex();
let activeBuild: Promise<void> | null = null;
let hydration: Promise<void> | null = null;

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
    validEmbedding(value.embedding)
  );
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
    validEmbedding(value.centroid)
  );
}

function trueRecord(value: unknown): value is Record<string, true> {
  return isRecord(value) && Object.values(value).every((entry) => entry === true);
}

function parseIndex(contents: string): PersistedFaceIndex | null {
  try {
    const value: unknown = JSON.parse(contents);
    if (
      !isRecord(value) ||
      value.version !== INDEX_VERSION ||
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
    return value as PersistedFaceIndex;
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
): Person[] {
  return clusterFaces(observations, { threshold: safeThreshold(threshold) });
}

function summariesForPeople(people: Person[]): FaceIndexPerson[] {
  return people
    .filter((person) => person.assetIds.length > 0)
    .map((person) => ({
      id: person.id,
      faceCount: person.faceCount,
      coverAssetId: person.assetIds[0],
      assetIds: person.assetIds.slice(),
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
): FacePeopleQuery {
  const summaries = summariesForPeople(
    peopleFromObservations(observations, threshold),
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
          const boxes = await dependencies.detectFaces(imageUri);
          const observations: FaceObservation[] = [];
          for (const box of boxes) {
            try {
              const embedding = await dependencies.embedFace(
                asset,
                imageUri,
                box,
              );
              if (validEmbedding(embedding)) {
                observations.push({ assetId: asset.id, embedding });
              }
            } catch {
              // One unreadable crop must not stop other faces or assets.
            }
          }
          return observations;
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

  const centerX = box.x + box.width / 2;
  const centerY = box.y + box.height / 2;
  const paddedWidth = Math.min(asset.width, box.width * FACE_PADDING_SCALE);
  const paddedHeight = Math.min(asset.height, box.height * FACE_PADDING_SCALE);
  const left = Math.max(0, Math.min(asset.width, centerX - paddedWidth / 2));
  const top = Math.max(0, Math.min(asset.height, centerY - paddedHeight / 2));
  const right = Math.max(
    left,
    Math.min(asset.width, centerX + paddedWidth / 2),
  );
  const bottom = Math.max(
    top,
    Math.min(asset.height, centerY + paddedHeight / 2),
  );
  const originX = Math.floor(left);
  const originY = Math.floor(top);
  const width = Math.max(1, Math.ceil(right) - originX);
  const height = Math.max(1, Math.ceil(bottom) - originY);

  return {
    originX,
    originY,
    width: Math.min(width, asset.width - originX),
    height: Math.min(height, asset.height - originY),
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
async function createPerceptualFaceEmbedding(
  asset: FaceScanAsset,
  imageUri: string,
  box: FaceBox,
): Promise<number[]> {
  const imageManipulator = await import("expo-image-manipulator");
  const thumbnail = await imageManipulator.manipulateAsync(
    imageUri,
    [
      { crop: paddedCrop(asset, box) },
      { resize: { width: THUMBNAIL_SIZE, height: THUMBNAIL_SIZE } },
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
  const decoded = decodeJpeg(decodeBase64(thumbnail.base64), {
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

function yieldToEventLoop(): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, 0));
}

function seenCount(): number {
  return Object.keys(index.seenAssetIds).length;
}

function rebuildPeople(threshold: number): void {
  index.threshold = safeThreshold(threshold);
  index.people = peopleFromObservations(index.observations, index.threshold);
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

    if (index.scanComplete) {
      index.cursor = null;
      index.scanComplete = false;
      index.seenAssetIds = {};
      await persistFaceIndex();
    }

    const mediaLibrary = await import("expo-media-library/legacy");
    let after = index.cursor ?? undefined;
    let hasNextPage = true;
    opts.onProgress?.(seenCount(), index.total);

    while (hasNextPage) {
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
        const observations = await scanFaceAssets(pending, {
          isDetectionAvailable: () => true,
          detectFaces,
          embedFace: createPerceptualFaceEmbedding,
        });
        index.observations.push(...observations);
        for (const asset of pending) {
          index.processedAssetIds[asset.id] = true;
        }
        for (const asset of batch) {
          index.seenAssetIds[asset.id] = true;
        }
        await persistFaceIndex();
        opts.onProgress?.(Math.min(seenCount(), index.total), index.total);
        await yieldToEventLoop();
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
  return summariesForPeople(index.people);
}

export function assetIdsForPerson(personId: string): string[] {
  return (
    index.people.find((person) => person.id === personId)?.assetIds.slice() ?? []
  );
}

export function faceIndexStatus(): FaceIndexStatus {
  return {
    scanned: Math.min(seenCount(), index.total),
    total: index.total,
    people: index.people.length,
  };
}

export { isFaceDetectionAvailable };
