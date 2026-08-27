/**
 * Tier B: the deep per-photo signals, made durable.
 *
 * This is the second tier of the SAME cache as `candidate-probe-cache.ts`, not
 * a rival to it. It shares that file's key (`candidateProbeKey`), its
 * cacheability rule, its signal-version discipline and its crash-safe
 * tmp-then-move write. What it does not share is the file layout, and the
 * reason is arithmetic: a cheap probe record is a blurhash and three numbers,
 * about 60 bytes, and 20,000 of them parse in 21 ms. A deep record carries a
 * 512-value semantic embedding, a 76-value perceptual fingerprint, 17 pose
 * keypoints, the face boxes and the measured quality — 3,850 B, sixty times
 * bigger. One file holding the whole library would be 45.6 MB of atomic
 * `JSON.parse` on Hermes, which is the exact shape of the 6,694 ms JSONL parse
 * the plan already indicts.
 *
 * So the layout is: one line-delimited shard per capture month, read by
 * splitting on newlines and slicing the key off each line, with `JSON.parse`
 * spent only on the records the caller actually asks for. A 3,000-photo event
 * spans one or two months, so an album build opens one or two shards and
 * decodes the ~64 records it wants out of the ~1,000 it read.
 *
 * WHAT IS STORED, AND WHY IT IS FLOAT32
 *
 *   encoding      record      11,854-photo library   pinned albums
 *   float64 JSON  13,225 B    156.8 MB               unchanged
 *   float32 b64    3,850 B     45.6 MB               unchanged
 *   int8 b64       1,394 B     16.5 MB               MOVED
 *
 * Measured 2026-08-27, the record sizes through this codec and the albums
 * through `planAlbum` over `albumFixtures()` on both selectors. int8 is 2.8x
 * smaller and it is not available: it shifts a vector component by up to
 * 2.1e-3, enough to walk a reframe pair across the 0.92 duplicate bar, and it
 * drops a photograph out of the `twoyears` album. That is the trap
 * `docs/EMBEDDING-MEMORY.md` flags for face embeddings, measured here for
 * albums. float32 shifts a component by at most 2.8e-8 and moves none of the
 * six pinned plans; `deep-signal-parity.test.ts` is the standing gate, and it
 * uses the rejected int8 encoding as its own sabotage.
 *
 * Raw model outputs are stored, never derived ones: pose keypoints rather than
 * `makePose`/`bodyCoverage` results, so a change to `pose.ts` costs a recompute
 * of pure arithmetic instead of an invalidation of an hour of inference.
 *
 * Privacy: derived vectors and numbers only. No uri, path, filename or pixels
 * ever enter a record — the same rule `preference-label-store.ts` enforces.
 */

import type { PickedPhoto } from "../import/picked-photo";
import type { FaceBox } from "../faces/face-detector";
import type { SemanticSignals } from "../ml/tinyclip";
import type { MeasuredImageQuality } from "./image-quality";
import type { PoseKeypoint } from "./pose";
// @ts-expect-error Node's native TypeScript runner requires the extension.
import { candidateProbeKey, isCandidateProbeCacheable } from "./candidate-probe-cache.ts";

/**
 * Bump when any input to a deep signal changes its value.
 *
 * That means: the perceptual fingerprint in `ml/stub-model.ts`, the ML Kit
 * detector options in `faces/face-detector.ts`, `ANALYSIS_PROXY_SIZE` or the
 * proxy's JPEG quality in `candidate-quality-probe.ts`, anything inside
 * `measureImageQuality`, the MoveNet graph or its letterbox, and the TinyCLIP
 * graph, its preprocessing or its text-axis sidecar.
 *
 * One version covers the whole record on purpose. Splitting it per signal would
 * let an M3 TinyCLIP swap keep the pose and quality rows, which is a real
 * saving — but it also requires the deep pass to be able to run one model for
 * one photo and five for the next. That branch is worth adding the day a model
 * actually changes independently, not before.
 */
export const DEEP_SIGNAL_VERSION = 1;

const SHARD_FORMAT = "photeo-deep-signals/1";
const STORE_DIRNAME = "deep-signals";
const MANIFEST_FILENAME = "manifest.json";
const MANIFEST_VERSION = 1;

/**
 * One shard is one capture month. At this library's density (11,854 photos
 * across roughly two years) a month holds ~500 photos; at 3,850 B a record this
 * bound holds 544, so an average month fits with room to spare.
 *
 * It is a heap decision, not a disk one. A two-week holiday can put 3,000
 * photos in one bucket, which unbounded would be an 11.5 MB string handed to
 * `readAsStringAsync` and then split into ~3,000 substrings;
 * `docs/DEEP-ANALYSIS-TIMING.md` traces a live `OutOfMemoryError` to a `byte[]`
 * of exactly a file's length. Two megabytes reads as roughly 6 MB of transient
 * UTF-16 during the split, which is the number this bound is actually chosen
 * against.
 */
export const MAX_SHARD_BYTES = 2 * 1024 * 1024;

/**
 * The whole store, across every month.
 *
 * A fully backfilled library is 45.6 MB (see the table above), so this budget
 * deliberately does NOT hold one. It holds 6,536 photographs — up to twelve
 * full months, i.e. roughly the last year the user has actually built albums
 * from, or 55% of this library. Eviction drops whole least-recently-used
 * shards, because the unit anyone would ever want back is "that trip", not
 * "these 40 photographs".
 *
 * Raise it only against a measured number. `docs/EMBEDDING-MEMORY.md` is the
 * standing reminder that a store with no release path becomes 89.5 MB of
 * resident set that nothing ever gives back.
 */
export const MAX_STORE_BYTES = 24 * 1024 * 1024;

export type DeepSignalRecord = {
  analysisWidth?: number;
  analysisHeight?: number;
  /** The 76-value perceptual fingerprint plus its face count. */
  perceptual: { embedding: number[]; faces: number };
  boxes: FaceBox[];
  quality: MeasuredImageQuality;
  /** MoveNet's raw output. `makePose`/`bodyCoverage` are re-derived on read. */
  pose?: { keypoints: PoseKeypoint[]; scores: number[] };
  semantic?: SemanticSignals;
};

export type DeepSignalStoreStats = {
  hits: number;
  misses: number;
  writes: number;
  /** `set` calls refused because the photo's shard was never loaded. */
  refusedWrites: number;
  shardsLoaded: number;
  recordsLoaded: number;
  shardsEvicted: number;
  bytes: number;
};

export type DeepSignalFileSystem = {
  readAsStringAsync: (uri: string) => Promise<string>;
  writeAsStringAsync: (uri: string, contents: string) => Promise<void>;
  deleteAsync: (uri: string, options: { idempotent: boolean }) => Promise<void>;
  moveAsync: (options: { from: string; to: string }) => Promise<void>;
  makeDirectoryAsync: (
    uri: string,
    options: { intermediates: boolean },
  ) => Promise<void>;
};

export type DeepSignalStore = {
  /** Read the shards these photos live in. Everything else stays on disk. */
  load: (photos: readonly PickedPhoto[]) => Promise<void>;
  get: (photo: PickedPhoto) => DeepSignalRecord | undefined;
  set: (photo: PickedPhoto, record: DeepSignalRecord) => void;
  persist: () => Promise<void>;
  stats: () => DeepSignalStoreStats;
};

type ShardState = {
  /** Insertion order is recency: `get` and `set` both re-insert. */
  records: Map<string, string>;
  dirty: boolean;
};

type ManifestEntry = { bytes: number; usedAt: number };

// ---------------------------------------------------------------------------
// Shard identity
// ---------------------------------------------------------------------------

const SHARD_ID_PATTERN = /^(?:\d{4}-\d{2}|undated)$/;
export const UNDATED_SHARD = "undated";

/**
 * Capture month, UTC, as the shard a photo belongs to.
 *
 * UTC rather than local time so a device that travels does not file the same
 * photograph in two different shards and pay for it twice.
 */
export function deepSignalShardId(photo: PickedPhoto): string {
  const capturedAt = photo.creationTime;
  if (typeof capturedAt !== "number" || !Number.isFinite(capturedAt)) {
    return UNDATED_SHARD;
  }
  const date = new Date(capturedAt);
  const year = date.getUTCFullYear();
  if (!Number.isFinite(year) || year < 1970 || year > 9999) return UNDATED_SHARD;
  return `${year}-${String(date.getUTCMonth() + 1).padStart(2, "0")}`;
}

/** Shard ids reach the filesystem, and they are derived from asset metadata. */
export function isValidShardId(shardId: string): boolean {
  return SHARD_ID_PATTERN.test(shardId);
}

// ---------------------------------------------------------------------------
// Record codec
// ---------------------------------------------------------------------------

type StoredRecord = {
  v: number;
  w?: number;
  h?: number;
  /** base64 little-endian float32. */
  pe: string;
  pf: number;
  bx: FaceBox[];
  q: MeasuredImageQuality;
  pk?: string;
  ps?: string;
  se?: string;
  /** The seven zero-shot axes, exact: a JSON double round-trips bit for bit. */
  sa?: number[];
};

const SEMANTIC_AXES = [
  "aesthetic",
  "composed",
  "cleanFrame",
  "sleeping",
  "awake",
  "embraceContext",
  "screenshotDocument",
] as const;

export function encodeDeepSignalRecord(record: DeepSignalRecord): string {
  const stored: StoredRecord = {
    v: DEEP_SIGNAL_VERSION,
    pe: encodeFloat32(record.perceptual.embedding),
    pf: record.perceptual.faces,
    bx: record.boxes,
    q: record.quality,
  };
  if (finite(record.analysisWidth)) stored.w = record.analysisWidth;
  if (finite(record.analysisHeight)) stored.h = record.analysisHeight;
  if (record.pose) {
    stored.pk = encodeFloat32(record.pose.keypoints.flatMap(([x, y]) => [x, y]));
    stored.ps = encodeFloat32(record.pose.scores);
  }
  if (record.semantic) {
    stored.se = encodeFloat32(record.semantic.embedding);
    stored.sa = SEMANTIC_AXES.map((axis) => record.semantic![axis]);
  }
  return JSON.stringify(stored);
}

export function decodeDeepSignalRecord(
  line: string,
  signalVersion = DEEP_SIGNAL_VERSION,
): DeepSignalRecord | undefined {
  let stored: unknown;
  try {
    stored = JSON.parse(line);
  } catch {
    return undefined;
  }
  if (!isRecord(stored) || stored.v !== signalVersion) return undefined;
  if (typeof stored.pe !== "string" || !isFiniteNumber(stored.pf)) return undefined;
  const embedding = decodeFloat32(stored.pe);
  if (!embedding) return undefined;
  const boxes = Array.isArray(stored.bx) ? (stored.bx as FaceBox[]) : undefined;
  if (!boxes || !isRecord(stored.q)) return undefined;

  const record: DeepSignalRecord = {
    perceptual: { embedding, faces: stored.pf },
    boxes,
    quality: stored.q as MeasuredImageQuality,
  };
  if (isFiniteNumber(stored.w)) record.analysisWidth = stored.w;
  if (isFiniteNumber(stored.h)) record.analysisHeight = stored.h;

  if (typeof stored.pk === "string" && typeof stored.ps === "string") {
    const flat = decodeFloat32(stored.pk);
    const scores = decodeFloat32(stored.ps);
    // A half-written pose is worse than none: `bodyCoverage` reads keypoints
    // positionally and would silently describe a different body.
    if (flat && scores && flat.length === scores.length * 2) {
      const keypoints: PoseKeypoint[] = [];
      for (let index = 0; index < flat.length; index += 2) {
        keypoints.push([flat[index], flat[index + 1]]);
      }
      record.pose = { keypoints, scores };
    }
  }

  if (typeof stored.se === "string" && Array.isArray(stored.sa)) {
    const semanticEmbedding = decodeFloat32(stored.se);
    const axes = stored.sa;
    if (
      semanticEmbedding &&
      axes.length === SEMANTIC_AXES.length &&
      axes.every(isFiniteNumber)
    ) {
      const semantic = { embedding: semanticEmbedding } as SemanticSignals;
      SEMANTIC_AXES.forEach((axis, index) => {
        semantic[axis] = axes[index] as number;
      });
      record.semantic = semantic;
    }
  }
  return record;
}

// ---------------------------------------------------------------------------
// Shard codec
// ---------------------------------------------------------------------------

/**
 * Split a shard into key -> still-encoded record. Nothing is JSON-parsed here.
 *
 * That is the whole point of the line format. `JSON.parse` runs once per record
 * the caller actually reads, inside `decodeDeepSignalRecord`; the other ~500
 * records in the month stay as untouched substrings, costing a slice each.
 *
 * Every line is retained, including the ones this build will never look at.
 * Keeping only the wanted keys would be cheaper by a Map entry and would make
 * the next `serializeDeepSignalShard` silently delete the rest of the month.
 */
export function parseDeepSignalShard(raw: string): Map<string, string> {
  const entries = new Map<string, string>();
  const lines = raw.split("\n");
  if (lines[0] !== SHARD_FORMAT) return entries;
  for (let index = 1; index < lines.length; index += 1) {
    const line = lines[index];
    const separator = line.indexOf("\t");
    if (separator <= 0) continue;
    entries.set(line.slice(0, separator), line.slice(separator + 1));
  }
  return entries;
}

/**
 * Serialize the most recently used records that fit the byte bound.
 *
 * Recency is Map insertion order; `get` and `set` both re-insert. Truncation
 * here only ever fires for a month that holds more photographs than the bound,
 * so the important recency question — "which MONTHS does the user still build
 * albums from" — is answered a level up, by `planShardEviction` reading the
 * `usedAt` that `load` stamps.
 */
export function serializeDeepSignalShard(
  entries: ReadonlyMap<string, string>,
  maxBytes = MAX_SHARD_BYTES,
): string {
  const lines: string[] = [];
  let bytes = utf8ByteLength(SHARD_FORMAT) + 1;
  const ordered = [...entries].reverse();
  for (const [key, encoded] of ordered) {
    const line = `${key}\t${encoded}`;
    const cost = utf8ByteLength(line) + 1;
    if (bytes + cost > maxBytes) break;
    bytes += cost;
    lines.push(line);
  }
  // Written oldest-first so the file's own order still means "recency
  // ascending", which is what `parseDeepSignalShard` hands back to the Map.
  lines.reverse();
  return [SHARD_FORMAT, ...lines].join("\n");
}

// ---------------------------------------------------------------------------
// Eviction
// ---------------------------------------------------------------------------

/**
 * Which whole shards to drop so the store fits its budget, least recently used
 * first. Returned rather than performed so the decision is testable without a
 * filesystem.
 */
export function planShardEviction(
  manifest: ReadonlyMap<string, ManifestEntry>,
  maxBytes = MAX_STORE_BYTES,
): string[] {
  let total = 0;
  for (const entry of manifest.values()) total += entry.bytes;
  if (total <= maxBytes) return [];
  const byAge = [...manifest].sort(
    ([leftId, left], [rightId, right]) =>
      left.usedAt - right.usedAt || leftId.localeCompare(rightId),
  );
  const evicted: string[] = [];
  for (const [shardId, entry] of byAge) {
    if (total <= maxBytes) break;
    evicted.push(shardId);
    total -= entry.bytes;
  }
  return evicted;
}

// ---------------------------------------------------------------------------
// The store
// ---------------------------------------------------------------------------

export type DeepSignalStoreOptions = {
  maxShardBytes?: number;
  maxStoreBytes?: number;
  now?: () => number;
};

/**
 * Open a crash-safe store against an injected filesystem.
 *
 * Injected on purpose: `preference-label-store.ts` does the same, and it is
 * what lets the durability and eviction rules be self-checked off-device
 * instead of asserted in a comment.
 */
export async function openDeepSignalStore(
  fileSystem: DeepSignalFileSystem,
  directoryUri: string,
  options: DeepSignalStoreOptions = {},
): Promise<DeepSignalStore> {
  const maxShardBytes = options.maxShardBytes ?? MAX_SHARD_BYTES;
  const maxStoreBytes = options.maxStoreBytes ?? MAX_STORE_BYTES;
  const now = options.now ?? (() => Date.now());
  const root = directoryUri.endsWith("/") ? directoryUri : `${directoryUri}/`;
  const manifestUri = `${root}${MANIFEST_FILENAME}`;
  const shardUri = (shardId: string) => `${root}${shardId}.ndjson`;

  const manifest = await readManifest(fileSystem, manifestUri);
  const shards = new Map<string, ShardState>();
  const stats: DeepSignalStoreStats = {
    hits: 0,
    misses: 0,
    writes: 0,
    refusedWrites: 0,
    shardsLoaded: 0,
    recordsLoaded: 0,
    shardsEvicted: 0,
    bytes: [...manifest.values()].reduce((sum, entry) => sum + entry.bytes, 0),
  };
  let pendingWrite = Promise.resolve();

  const cacheable = (photo: PickedPhoto): boolean =>
    isCandidateProbeCacheable(photo) && isValidShardId(deepSignalShardId(photo));

  return {
    load: async (photos) => {
      const wanted = new Set<string>();
      for (const photo of photos) {
        if (!cacheable(photo)) continue;
        const shardId = deepSignalShardId(photo);
        if (!shards.has(shardId)) wanted.add(shardId);
      }
      for (const shardId of wanted) {
        const records = await readNewestShard(fileSystem, shardUri(shardId));
        shards.set(shardId, { records, dirty: false });
        stats.shardsLoaded += 1;
        stats.recordsLoaded += records.size;
        const entry = manifest.get(shardId);
        if (entry) manifest.set(shardId, { ...entry, usedAt: now() });
      }
    },

    get: (photo) => {
      if (!cacheable(photo)) return undefined;
      const shard = shards.get(deepSignalShardId(photo));
      const key = candidateProbeKey(photo);
      const encoded = shard?.records.get(key);
      if (!encoded) {
        stats.misses += 1;
        return undefined;
      }
      const record = decodeDeepSignalRecord(encoded);
      if (!record) {
        // A record this build cannot read is a record no future build can read
        // either: drop it so the shard compacts instead of carrying it forever.
        shard!.records.delete(key);
        shard!.dirty = true;
        stats.misses += 1;
        return undefined;
      }
      // Refresh recency, but do NOT dirty the shard for it. A repeat build is
      // 64 hits and zero writes; rewriting two megabytes of month to record
      // that they were read would make the cheapest possible build the one that
      // does the most I/O.
      shard!.records.delete(key);
      shard!.records.set(key, encoded);
      stats.hits += 1;
      return record;
    },

    set: (photo, record) => {
      if (!cacheable(photo)) return;
      const shardId = deepSignalShardId(photo);
      const shard = shards.get(shardId);
      // Writing into a shard that was never read would overwrite the file with
      // this session's records alone. Callers load the exact set they analyse,
      // so this counter is expected to stay at zero; it exists to say so.
      if (!shard) {
        stats.refusedWrites += 1;
        return;
      }
      const key = candidateProbeKey(photo);
      shard.records.delete(key);
      shard.records.set(key, encodeDeepSignalRecord(record));
      shard.dirty = true;
      stats.writes += 1;
    },

    persist: async () => {
      const dirty = [...shards].filter(([, shard]) => shard.dirty);
      if (dirty.length === 0) return;
      for (const [, shard] of dirty) shard.dirty = false;
      pendingWrite = pendingWrite.catch(() => undefined).then(async () => {
        try {
          await fileSystem.makeDirectoryAsync(root, { intermediates: true });
        } catch {
          // Already present, or the directory cannot be made; the writes below
          // fail loudly enough into the same catch.
        }
        for (const [shardId, shard] of dirty) {
          const serialized = serializeDeepSignalShard(shard.records, maxShardBytes);
          try {
            await writeAtomically(fileSystem, shardUri(shardId), serialized);
            manifest.set(shardId, {
              bytes: utf8ByteLength(serialized),
              usedAt: now(),
            });
          } catch {
            shard.dirty = true;
          }
        }
        for (const shardId of planShardEviction(manifest, maxStoreBytes)) {
          try {
            await fileSystem.deleteAsync(shardUri(shardId), { idempotent: true });
            manifest.delete(shardId);
            shards.delete(shardId);
            stats.shardsEvicted += 1;
          } catch {
            // A shard that will not delete stays counted, so the budget is not
            // silently overspent by pretending it went away.
          }
        }
        stats.bytes = [...manifest.values()].reduce(
          (sum, entry) => sum + entry.bytes,
          0,
        );
        try {
          await writeAtomically(
            fileSystem,
            manifestUri,
            JSON.stringify({
              version: MANIFEST_VERSION,
              shards: Object.fromEntries(manifest),
            }),
          );
        } catch {
          // The manifest is an accounting convenience. Losing it costs one
          // build's eviction accuracy, never a record.
        }
      });
      await pendingWrite;
    },

    stats: () => ({ ...stats }),
  };
}

let defaultStorePromise: Promise<DeepSignalStore | undefined> | undefined;

/** The production store, under Expo's document directory. Never throws. */
export async function defaultDeepSignalStore(): Promise<DeepSignalStore | undefined> {
  if (!defaultStorePromise) {
    defaultStorePromise = (async () => {
      try {
        const fileSystem = await import("expo-file-system/legacy");
        if (!fileSystem.documentDirectory) return undefined;
        return await openDeepSignalStore(
          fileSystem as unknown as DeepSignalFileSystem,
          `${fileSystem.documentDirectory}${STORE_DIRNAME}`,
        );
      } catch {
        return undefined;
      }
    })();
  }
  return defaultStorePromise;
}

async function readManifest(
  fileSystem: DeepSignalFileSystem,
  uri: string,
): Promise<Map<string, ManifestEntry>> {
  for (const candidateUri of [`${uri}.tmp`, uri]) {
    try {
      const parsed: unknown = JSON.parse(
        await fileSystem.readAsStringAsync(candidateUri),
      );
      if (
        !isRecord(parsed) ||
        parsed.version !== MANIFEST_VERSION ||
        !isRecord(parsed.shards)
      ) continue;
      const manifest = new Map<string, ManifestEntry>();
      for (const [shardId, entry] of Object.entries(parsed.shards)) {
        if (!isValidShardId(shardId) || !isRecord(entry)) continue;
        if (!isFiniteNumber(entry.bytes) || !isFiniteNumber(entry.usedAt)) continue;
        manifest.set(shardId, { bytes: entry.bytes, usedAt: entry.usedAt });
      }
      if (manifest.size > 0) return manifest;
    } catch {
      // Fall through to the durable file, then to an empty manifest.
    }
  }
  return new Map();
}

async function readNewestShard(
  fileSystem: DeepSignalFileSystem,
  uri: string,
): Promise<Map<string, string>> {
  for (const candidateUri of [`${uri}.tmp`, uri]) {
    try {
      const parsed = parseDeepSignalShard(
        await fileSystem.readAsStringAsync(candidateUri),
      );
      if (parsed.size > 0) return parsed;
    } catch {
      // Absent or truncated checkpoint; try the durable file.
    }
  }
  return new Map();
}

async function writeAtomically(
  fileSystem: DeepSignalFileSystem,
  uri: string,
  contents: string,
): Promise<void> {
  const temporaryUri = `${uri}.tmp`;
  await fileSystem.writeAsStringAsync(temporaryUri, contents);
  await fileSystem.deleteAsync(uri, { idempotent: true });
  await fileSystem.moveAsync({ from: temporaryUri, to: uri });
}

// ---------------------------------------------------------------------------
// float32 <-> base64
// ---------------------------------------------------------------------------

const BASE64_ALPHABET =
  "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";

/** Little-endian explicitly, so a stored shard is portable between engines. */
export function encodeFloat32(values: readonly number[]): string {
  const buffer = new ArrayBuffer(values.length * 4);
  const view = new DataView(buffer);
  for (let index = 0; index < values.length; index += 1) {
    view.setFloat32(index * 4, values[index], true);
  }
  return encodeBase64(new Uint8Array(buffer));
}

export function decodeFloat32(encoded: string): number[] | undefined {
  const bytes = decodeBase64(encoded);
  if (!bytes || bytes.length % 4 !== 0) return undefined;
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const values = new Array<number>(bytes.length / 4);
  for (let index = 0; index < values.length; index += 1) {
    values[index] = view.getFloat32(index * 4, true);
  }
  return values;
}

function encodeBase64(bytes: Uint8Array): string {
  let output = "";
  for (let index = 0; index < bytes.length; index += 3) {
    const remaining = bytes.length - index;
    const chunk =
      (bytes[index] << 16) |
      ((remaining > 1 ? bytes[index + 1] : 0) << 8) |
      (remaining > 2 ? bytes[index + 2] : 0);
    output += BASE64_ALPHABET[(chunk >> 18) & 63];
    output += BASE64_ALPHABET[(chunk >> 12) & 63];
    output += remaining > 1 ? BASE64_ALPHABET[(chunk >> 6) & 63] : "=";
    output += remaining > 2 ? BASE64_ALPHABET[chunk & 63] : "=";
  }
  return output;
}

function decodeBase64(encoded: string): Uint8Array | undefined {
  const clean = encoded.endsWith("==")
    ? encoded.slice(0, -2)
    : encoded.endsWith("=")
      ? encoded.slice(0, -1)
      : encoded;
  const padding = encoded.length - clean.length;
  if (encoded.length % 4 !== 0) return undefined;
  const bytes = new Uint8Array(Math.max(0, (encoded.length / 4) * 3 - padding));
  let accumulator = 0;
  let bits = 0;
  let cursor = 0;
  for (let index = 0; index < clean.length; index += 1) {
    const digit = BASE64_ALPHABET.indexOf(clean[index]);
    if (digit < 0) return undefined;
    accumulator = (accumulator << 6) | digit;
    bits += 6;
    if (bits >= 8) {
      bits -= 8;
      bytes[cursor] = (accumulator >>> bits) & 0xff;
      cursor += 1;
    }
  }
  return cursor === bytes.length ? bytes : undefined;
}

export function utf8ByteLength(value: string): number {
  let bytes = 0;
  for (let index = 0; index < value.length; index += 1) {
    const codePoint = value.codePointAt(index)!;
    if (codePoint > 0xffff) index += 1;
    bytes += codePoint <= 0x7f ? 1 : codePoint <= 0x7ff ? 2 : codePoint <= 0xffff ? 3 : 4;
  }
  return bytes;
}

function finite(value: number | undefined): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function isFiniteNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
