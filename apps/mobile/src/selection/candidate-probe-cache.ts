import type { PickedPhoto } from "../import/picked-photo";
import type { MeasuredImageQuality } from "./image-quality";
// @ts-expect-error Node's native TypeScript runner requires the extension.
import { CANDIDATE_PROBE_SIGNAL_VERSION } from "./candidate-quality-probe.ts";

const CACHE_VERSION = 1;
const CACHE_FILENAME = "album-candidate-probes.json";
const MAX_CACHE_ENTRIES = 20_000;

type CacheEntry = {
  key: string;
  signalVersion: number;
  quality: MeasuredImageQuality;
};

type StoredCache = {
  version: typeof CACHE_VERSION;
  entries: CacheEntry[];
};

type LegacyFileSystem = typeof import("expo-file-system/legacy");

export type CandidateProbeCache = {
  get: (photo: PickedPhoto) => MeasuredImageQuality | undefined;
  set: (photo: PickedPhoto, quality: MeasuredImageQuality) => void;
  persist: () => Promise<void>;
};

export async function probeCandidateWithCache(
  photo: PickedPhoto,
  cache: CandidateProbeCache | undefined,
  probe: (uri: string) => Promise<MeasuredImageQuality>,
): Promise<{ quality: MeasuredImageQuality; cacheHit: boolean }> {
  const cached = cache?.get(photo);
  if (cached) return { quality: cached, cacheHit: true };
  const quality = await probe(photo.uri);
  cache?.set(photo, quality);
  return { quality, cacheHit: false };
}

/**
 * A stable identity for derived 32 px evidence. Paths and filenames are not
 * persisted: a hashed source/id plus capture metadata invalidates the entry if
 * a local asset is replaced while keeping the cache privacy-safe.
 */
export function candidateProbeKey(photo: PickedPhoto): string {
  return JSON.stringify([
    hashIdentity(`${photo.source}\0${photo.id}`),
    finiteOrNull(photo.creationTime),
    finiteOrNull(photo.width),
    finiteOrNull(photo.height),
    photo.mimeType ?? null,
  ]);
}

/** Folder picks expose no mtime/content hash, so a same-path replacement is unsafe to reuse. */
export function isCandidateProbeCacheable(photo: PickedPhoto): boolean {
  return photo.source !== "local-folder";
}

export function parseCandidateProbeCache(
  raw: string,
  signalVersion = CANDIDATE_PROBE_SIGNAL_VERSION,
): Map<string, MeasuredImageQuality> {
  try {
    const parsed: unknown = JSON.parse(raw);
    if (!isRecord(parsed) || parsed.version !== CACHE_VERSION || !Array.isArray(parsed.entries)) {
      return new Map();
    }
    const entries = new Map<string, MeasuredImageQuality>();
    for (const value of parsed.entries) {
      if (
        !isRecord(value) ||
        typeof value.key !== "string" ||
        value.signalVersion !== signalVersion
      ) continue;
      const quality = parseQuality(value.quality);
      if (quality?.blurhash) entries.set(value.key, quality);
    }
    return entries;
  } catch {
    return new Map();
  }
}

export function serializeCandidateProbeCache(
  entries: ReadonlyMap<string, MeasuredImageQuality>,
  signalVersion = CANDIDATE_PROBE_SIGNAL_VERSION,
): string {
  const storedEntries = Array.from(entries, ([key, quality]) => ({
    key,
    signalVersion,
    quality,
  })).slice(-MAX_CACHE_ENTRIES);
  const stored: StoredCache = { version: CACHE_VERSION, entries: storedEntries };
  return JSON.stringify(stored);
}

/** Load a crash-safe, local-only checkpoint. Every failure degrades to empty. */
export async function loadCandidateProbeCache(): Promise<CandidateProbeCache> {
  let fileSystem: LegacyFileSystem | undefined;
  let uri: string | undefined;
  let entries = new Map<string, MeasuredImageQuality>();
  try {
    fileSystem = await import("expo-file-system/legacy");
    if (fileSystem.documentDirectory) {
      uri = `${fileSystem.documentDirectory}${CACHE_FILENAME}`;
      entries = await readNewestCache(fileSystem, uri);
    }
  } catch {
    // Selection remains correct; it simply probes again.
  }

  let dirty = false;
  let pendingWrite = Promise.resolve();
  return {
    get: (photo) =>
      isCandidateProbeCacheable(photo)
        ? entries.get(candidateProbeKey(photo))
        : undefined,
    set: (photo, quality) => {
      // Do not make a transient native failure sticky across future builds.
      if (!quality.blurhash || !isCandidateProbeCacheable(photo)) return;
      const key = candidateProbeKey(photo);
      entries.delete(key);
      entries.set(key, quality);
      dirty = true;
    },
    persist: async () => {
      if (!dirty || !fileSystem || !uri) return;
      const snapshot = serializeCandidateProbeCache(entries);
      dirty = false;
      pendingWrite = pendingWrite.then(async () => {
        const temporaryUri = `${uri}.tmp`;
        try {
          await fileSystem!.writeAsStringAsync(temporaryUri, snapshot);
          await fileSystem!.deleteAsync(uri!, { idempotent: true });
          await fileSystem!.moveAsync({ from: temporaryUri, to: uri! });
        } catch {
          dirty = true;
        }
      });
      await pendingWrite;
    },
  };
}

async function readNewestCache(
  fileSystem: LegacyFileSystem,
  uri: string,
): Promise<Map<string, MeasuredImageQuality>> {
  for (const candidateUri of [`${uri}.tmp`, uri]) {
    try {
      const parsed = parseCandidateProbeCache(
        await fileSystem.readAsStringAsync(candidateUri),
      );
      if (parsed.size > 0) return parsed;
    } catch {
      // Try the durable file after an absent/truncated temporary checkpoint.
    }
  }
  return new Map();
}

function parseQuality(value: unknown): MeasuredImageQuality | undefined {
  if (!isRecord(value)) return undefined;
  const quality: MeasuredImageQuality = {};
  for (const key of ["sharpness", "exposure", "clippedFraction"] as const) {
    const signal = value[key];
    if (typeof signal === "number" && Number.isFinite(signal)) quality[key] = signal;
  }
  if (typeof value.blurhash === "string") quality.blurhash = value.blurhash;
  return quality;
}

function finiteOrNull(value: number | undefined): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

/** Two independent 32-bit FNV passes avoid persisting path-shaped asset ids. */
function hashIdentity(value: string): string {
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

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
