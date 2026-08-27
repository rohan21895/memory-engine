const STORE_VERSION = 1;
const STORE_FILENAME = "photo-preference-labels-v1.json";

/** A rolling local ledger: bounded by both count and encoded disk size. */
export const MAX_PREFERENCE_LABEL_RECORDS = 2_000;
export const MAX_PREFERENCE_LABEL_BYTES = 16 * 1024 * 1024;

export const PHOTO_SELECTOR_NAME = "mobile-select-best-shots";
export const PHOTO_SELECTOR_CONFIG_VERSION = "cx26-2026-08-27-v1";
export const PHOTO_FEATURE_SCHEMA_VERSION = 1;

export type PhotoPreferenceFeatures = {
  qualityScore: number;
  qualityBand: number;
  smileTieRank: number;
  sourcePixelCount: number;
  tieBreakInputIndex: number;
  detailScore?: number;
  sharpness?: number;
  eyesOpen?: number;
  smile?: number;
  cutFace: boolean;
  category?: "portrait" | "couple" | "group" | "detail" | "scene";
  exposure?: number;
  clippedFraction?: number;
  faceSharpness?: number;
  subjectSharpness?: number;
  subjectBackgroundRatio?: number;
  faceCount?: number;
  largestFaceAreaRatio?: number;
  anyFaceCutAtEdge?: boolean;
  semantic?: {
    aesthetic: number;
    composed: number;
    cleanFrame: number;
    sleeping: number;
    awake: number;
    embraceContext: number;
    screenshotDocument: number;
  };
  /** Derived vectors only. No pixels, paths, filenames, EXIF, or URIs. */
  groupingEmbedding?: number[];
  groupingEmbeddingSpace?: string;
  perceptualEmbedding?: number[];
  semanticEmbedding?: number[];
};

export type ObservedPreferenceCandidate = {
  assetId: string;
  features: PhotoPreferenceFeatures;
};

export type NearDuplicateGroupObservation = {
  groupId: string;
  winnerAssetId: string;
  candidates: ObservedPreferenceCandidate[];
  blinkGateEnabled: boolean;
  blinkRejectedAssetIds: string[];
  cutFaceRejectedAssetIds: string[];
};

type SelectorDescriptor = {
  name: string;
  configVersion: string;
  featureSchemaVersion: number;
};

export type NearDuplicateRankingLabel = {
  eventId: string;
  type: "near_duplicate_ranking";
  capturedAt: number;
  albumId: string;
  selector: SelectorDescriptor;
  group: NearDuplicateGroupObservation;
};

export type AlbumEditPreferenceLabel = {
  eventId: string;
  type: "album_edit_pairwise";
  capturedAt: number;
  albumId: string;
  selector: SelectorDescriptor;
  groupId: string;
  groupAssetIds: string[];
  originalWinnerAssetId: string;
  slotAssetId: string;
  rejected: ObservedPreferenceCandidate;
  chosen: ObservedPreferenceCandidate;
  decisionSurface: "swap-sheet" | "lightbox";
};

export type PhotoPreferenceLabel =
  | NearDuplicateRankingLabel
  | AlbumEditPreferenceLabel;

export type PreferenceLabelFileSystem = {
  readAsStringAsync: (uri: string) => Promise<string>;
  writeAsStringAsync: (uri: string, contents: string) => Promise<void>;
  deleteAsync: (uri: string, options: { idempotent: boolean }) => Promise<void>;
  moveAsync: (options: { from: string; to: string }) => Promise<void>;
};

export type PhotoPreferenceLabelStore = {
  append: (records: readonly PhotoPreferenceLabel[]) => Promise<void>;
  snapshot: () => PhotoPreferenceLabel[];
};

type StoreBounds = { maxRecords: number; maxBytes: number };

const DEFAULT_BOUNDS: StoreBounds = {
  maxRecords: MAX_PREFERENCE_LABEL_RECORDS,
  maxBytes: MAX_PREFERENCE_LABEL_BYTES,
};

const SELECTOR: SelectorDescriptor = {
  name: PHOTO_SELECTOR_NAME,
  configVersion: PHOTO_SELECTOR_CONFIG_VERSION,
  featureSchemaVersion: PHOTO_FEATURE_SCHEMA_VERSION,
};

/** Stable pseudonymous id: some fallback picker ids contain a full local URI. */
export function preferenceAssetId(assetId: string): string {
  return `asset:${identityHash(assetId)}`;
}

/** Parse a complete checkpoint. Corrupt or unknown versions are never salvaged. */
export function parsePhotoPreferenceLabels(raw: string): PhotoPreferenceLabel[] | undefined {
  try {
    const parsed: unknown = JSON.parse(raw);
    if (
      !isRecord(parsed) ||
      parsed.version !== STORE_VERSION ||
      !Array.isArray(parsed.records) ||
      !parsed.records.every(isPhotoPreferenceLabel)
    ) {
      return undefined;
    }
    return parsed.records;
  } catch {
    return undefined;
  }
}

/**
 * Serialize the newest records that fit both bounds.
 *
 * The ledger is append-only while records are retained. Crossing a bound is a
 * checkpoint compaction that drops whole oldest events; retained events are
 * never edited or partially truncated.
 */
export function serializePhotoPreferenceLabels(
  records: readonly PhotoPreferenceLabel[],
  bounds: StoreBounds = DEFAULT_BOUNDS,
): string {
  const maxRecords = Math.max(0, Math.floor(bounds.maxRecords));
  const maxBytes = Math.max(0, Math.floor(bounds.maxBytes));
  let bounded = maxRecords === 0 ? [] : records.slice(-maxRecords);
  let serialized = JSON.stringify({ version: STORE_VERSION, records: bounded });
  while (bounded.length > 0 && utf8ByteLength(serialized) > maxBytes) {
    bounded = bounded.slice(1);
    serialized = JSON.stringify({ version: STORE_VERSION, records: bounded });
  }
  return serialized;
}

/** Open a crash-safe store against an injected filesystem (used by off-device checks). */
export async function openPhotoPreferenceLabelStore(
  fileSystem: PreferenceLabelFileSystem,
  uri: string,
  bounds: StoreBounds = DEFAULT_BOUNDS,
): Promise<PhotoPreferenceLabelStore> {
  let records = await readNewestCheckpoint(fileSystem, uri);
  let pendingWrite = Promise.resolve();

  return {
    append: async (nextRecords) => {
      if (nextRecords.length === 0) return;
      const knownIds = new Set(records.map((record) => record.eventId));
      const additions = nextRecords.filter((record) => {
        if (!isPhotoPreferenceLabel(record) || knownIds.has(record.eventId)) return false;
        knownIds.add(record.eventId);
        return true;
      });
      if (additions.length === 0) return;

      const checkpoint = serializePhotoPreferenceLabels(
        [...records, ...additions],
        bounds,
      );
      records = parsePhotoPreferenceLabels(checkpoint) ?? [];
      // A failed older checkpoint must not poison the queue. This snapshot
      // includes every retained in-memory event, so the next append retries it.
      pendingWrite = pendingWrite.catch(() => undefined).then(async () => {
        const temporaryUri = `${uri}.tmp`;
        await fileSystem.writeAsStringAsync(temporaryUri, checkpoint);
        await fileSystem.deleteAsync(uri, { idempotent: true });
        await fileSystem.moveAsync({ from: temporaryUri, to: uri });
      });
      await pendingWrite;
    },
    snapshot: () => records.slice(),
  };
}

let defaultStorePromise: Promise<PhotoPreferenceLabelStore | undefined> | undefined;

async function defaultStore(): Promise<PhotoPreferenceLabelStore | undefined> {
  if (!defaultStorePromise) {
    defaultStorePromise = (async () => {
      try {
        const fileSystem = await import("expo-file-system/legacy");
        if (!fileSystem.documentDirectory) return undefined;
        return openPhotoPreferenceLabelStore(
          fileSystem,
          `${fileSystem.documentDirectory}${STORE_FILENAME}`,
        );
      } catch {
        return undefined;
      }
    })();
  }
  return defaultStorePromise;
}

/** Persist automatic winner-over-loser labels without influencing selection. */
export async function captureNearDuplicateRankingLabels(input: {
  albumId: string;
  groups: readonly NearDuplicateGroupObservation[];
  capturedAt?: number;
}): Promise<void> {
  try {
    const store = await defaultStore();
    if (!store) return;
    const capturedAt = finiteTimestamp(input.capturedAt) ?? Date.now();
    await store.append(
      input.groups.map((group): NearDuplicateRankingLabel => ({
        eventId: stableEventId("near", [input.albumId, group.groupId]),
        type: "near_duplicate_ranking",
        capturedAt,
        albumId: input.albumId,
        selector: SELECTOR,
        group,
      })),
    );
  } catch {
    // Preference capture is an observer. It must never fail an album build.
  }
}

let editEventSequence = 0;

export type AlbumEditPreferenceInput = {
  albumId: string;
  slotAssetId: string;
  rejectedAssetId: string;
  chosenAssetId: string;
  decisionSurface: AlbumEditPreferenceLabel["decisionSurface"];
  capturedAt?: number;
};

/** Copy a live replacement into a self-contained pairwise training record. */
export async function captureAlbumEditPreference(
  input: AlbumEditPreferenceInput,
): Promise<void> {
  if (input.rejectedAssetId === input.chosenAssetId) return;
  try {
    const store = await defaultStore();
    if (!store) return;
    await appendAlbumEditPreference(store, input);
  } catch {
    // UI preference capture is fire-and-forget and cannot change the swap.
  }
}

/** Injectable core used by the off-device durability check. */
export async function appendAlbumEditPreference(
  store: PhotoPreferenceLabelStore,
  input: AlbumEditPreferenceInput,
): Promise<void> {
  if (input.rejectedAssetId === input.chosenAssetId) return;
  const rejectedAssetId = preferenceAssetId(input.rejectedAssetId);
  const chosenAssetId = preferenceAssetId(input.chosenAssetId);
  const slotAssetId = preferenceAssetId(input.slotAssetId);
  const records = store.snapshot();
  let source: NearDuplicateRankingLabel | undefined;
  for (let index = records.length - 1; index >= 0; index -= 1) {
    const record = records[index];
    if (
      record.type === "near_duplicate_ranking" &&
      record.albumId === input.albumId &&
      record.group.candidates.some(({ assetId }) => assetId === rejectedAssetId) &&
      record.group.candidates.some(({ assetId }) => assetId === chosenAssetId)
    ) {
      source = record;
      break;
    }
  }
  if (!source) return;
  const rejected = source.group.candidates.find(
    ({ assetId }) => assetId === rejectedAssetId,
  );
  const chosen = source.group.candidates.find(
    ({ assetId }) => assetId === chosenAssetId,
  );
  if (!rejected || !chosen) return;

  const capturedAt = finiteTimestamp(input.capturedAt) ?? Date.now();
  editEventSequence = (editEventSequence + 1) % Number.MAX_SAFE_INTEGER;
  await store.append([{
    eventId: stableEventId("edit", [
      input.albumId,
      source.group.groupId,
      rejectedAssetId,
      chosenAssetId,
      String(capturedAt),
      String(editEventSequence),
    ]),
    type: "album_edit_pairwise",
    capturedAt,
    albumId: input.albumId,
    selector: source.selector,
    groupId: source.group.groupId,
    groupAssetIds: source.group.candidates.map(({ assetId }) => assetId),
    originalWinnerAssetId: source.group.winnerAssetId,
    slotAssetId,
    rejected,
    chosen,
    decisionSurface: input.decisionSurface,
  }]);
}

async function readNewestCheckpoint(
  fileSystem: PreferenceLabelFileSystem,
  uri: string,
): Promise<PhotoPreferenceLabel[]> {
  for (const candidateUri of [`${uri}.tmp`, uri]) {
    try {
      const parsed = parsePhotoPreferenceLabels(
        await fileSystem.readAsStringAsync(candidateUri),
      );
      if (parsed) return parsed;
    } catch {
      // A missing/truncated temporary checkpoint falls through to durable data.
    }
  }
  return [];
}

function isPhotoPreferenceLabel(value: unknown): value is PhotoPreferenceLabel {
  if (
    !isRecord(value) ||
    containsForbiddenMediaField(value) ||
    typeof value.eventId !== "string" ||
    typeof value.albumId !== "string" ||
    finiteTimestamp(value.capturedAt) === undefined ||
    !isSelector(value.selector)
  ) {
    return false;
  }
  if (value.type === "near_duplicate_ranking") {
    return isNearDuplicateGroup(value.group);
  }
  return (
    value.type === "album_edit_pairwise" &&
    typeof value.groupId === "string" &&
    Array.isArray(value.groupAssetIds) &&
    value.groupAssetIds.every((id) => typeof id === "string") &&
    typeof value.originalWinnerAssetId === "string" &&
    typeof value.slotAssetId === "string" &&
    isObservedCandidate(value.rejected) &&
    isObservedCandidate(value.chosen) &&
    (value.decisionSurface === "swap-sheet" || value.decisionSurface === "lightbox")
  );
}

function isSelector(value: unknown): value is SelectorDescriptor {
  return (
    isRecord(value) &&
    typeof value.name === "string" &&
    value.name.length > 0 &&
    typeof value.configVersion === "string" &&
    value.configVersion.length > 0 &&
    isFiniteNumber(value.featureSchemaVersion) &&
    Number.isInteger(value.featureSchemaVersion) &&
    value.featureSchemaVersion > 0
  );
}

function isNearDuplicateGroup(value: unknown): value is NearDuplicateGroupObservation {
  return (
    isRecord(value) &&
    typeof value.groupId === "string" &&
    typeof value.winnerAssetId === "string" &&
    Array.isArray(value.candidates) &&
    value.candidates.length > 1 &&
    value.candidates.every(isObservedCandidate) &&
    value.candidates.some((candidate) => candidate.assetId === value.winnerAssetId) &&
    typeof value.blinkGateEnabled === "boolean" &&
    isStringArray(value.blinkRejectedAssetIds) &&
    isStringArray(value.cutFaceRejectedAssetIds)
  );
}

function isObservedCandidate(value: unknown): value is ObservedPreferenceCandidate {
  return (
    isRecord(value) &&
    typeof value.assetId === "string" &&
    isPhotoPreferenceFeatures(value.features)
  );
}

function isPhotoPreferenceFeatures(value: unknown): value is PhotoPreferenceFeatures {
  if (!isRecord(value)) return false;
  for (const key of [
    "qualityScore",
    "qualityBand",
    "smileTieRank",
    "sourcePixelCount",
    "tieBreakInputIndex",
  ]) {
    if (!isFiniteNumber(value[key])) return false;
  }
  if (typeof value.cutFace !== "boolean") return false;
  if (
    value.anyFaceCutAtEdge !== undefined &&
    typeof value.anyFaceCutAtEdge !== "boolean"
  ) return false;
  if (
    value.category !== undefined &&
    !["portrait", "couple", "group", "detail", "scene"].includes(String(value.category))
  ) return false;
  if (
    value.groupingEmbeddingSpace !== undefined &&
    typeof value.groupingEmbeddingSpace !== "string"
  ) return false;
  for (const optional of [
    "detailScore",
    "sharpness",
    "eyesOpen",
    "smile",
    "exposure",
    "clippedFraction",
    "faceSharpness",
    "subjectSharpness",
    "subjectBackgroundRatio",
    "faceCount",
    "largestFaceAreaRatio",
  ]) {
    if (value[optional] !== undefined && !isFiniteNumber(value[optional])) return false;
  }
  for (const vector of ["groupingEmbedding", "perceptualEmbedding", "semanticEmbedding"]) {
    if (value[vector] !== undefined && !isFiniteNumberArray(value[vector])) return false;
  }
  const semantic = value.semantic;
  return semantic === undefined || (
    isRecord(semantic) &&
    [
      "aesthetic",
      "composed",
      "cleanFrame",
      "sleeping",
      "awake",
      "embraceContext",
      "screenshotDocument",
    ].every((key) => isFiniteNumber(semantic[key]))
  );
}

function containsForbiddenMediaField(value: unknown): boolean {
  if (Array.isArray(value)) return value.some(containsForbiddenMediaField);
  if (!isRecord(value)) return false;
  const forbidden = new Set(["uri", "path", "paths", "filename", "image", "thumbnail", "pixels"]);
  return Object.entries(value).some(
    ([key, child]) => forbidden.has(key.toLowerCase()) || containsForbiddenMediaField(child),
  );
}

function isStringArray(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((item) => typeof item === "string");
}

function isFiniteNumberArray(value: unknown): value is number[] {
  return Array.isArray(value) && value.every(isFiniteNumber);
}

function isFiniteNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function finiteTimestamp(value: unknown): number | undefined {
  return isFiniteNumber(value) && value >= 0 ? value : undefined;
}

function stableEventId(prefix: string, parts: string[]): string {
  return `${prefix}:${identityHash(parts.join("\0"))}`;
}

function identityHash(value: string): string {
  const hash = (seed: number, prime: number) => {
    let output = seed >>> 0;
    for (let index = 0; index < value.length; index += 1) {
      output ^= value.charCodeAt(index);
      output = Math.imul(output, prime) >>> 0;
    }
    return output.toString(16).padStart(8, "0");
  };
  return hash(0x811c9dc5, 0x01000193) + hash(0x9e3779b9, 0x85ebca6b);
}

function utf8ByteLength(value: string): number {
  let bytes = 0;
  for (let index = 0; index < value.length; index += 1) {
    const codePoint = value.codePointAt(index)!;
    if (codePoint > 0xffff) index += 1;
    bytes += codePoint <= 0x7f ? 1 : codePoint <= 0x7ff ? 2 : codePoint <= 0xffff ? 3 : 4;
  }
  return bytes;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
