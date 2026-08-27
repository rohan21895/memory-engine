/**
 * The Tier-B store, self-checked off-device.
 *
 * Reading order:
 *   1. the codec — does a record survive the round trip, and by how much does
 *      float32 move a vector;
 *   2. the shard — does reading one record and writing the shard back keep the
 *      other five hundred (this is the bug the lazy-parse layout invites);
 *   3. the bounds — what gets dropped when the month or the store is full;
 *   4. the store, end to end against an in-memory filesystem;
 *   5. vacuity, inline, next to each claim it keeps honest.
 */

// The suppression must stay on ONE line with its import: `@ts-expect-error`
// covers only the next line, and above a multi-line import it lands on the
// opening brace while TS5097 lands on the `from` clause.
// @ts-expect-error Node's native TypeScript runner requires the source extension.
import { DEEP_SIGNAL_VERSION, decodeDeepSignalRecord, decodeFloat32, deepSignalShardId, encodeDeepSignalRecord, encodeFloat32, isValidShardId, openDeepSignalStore, parseDeepSignalShard, planShardEviction, serializeDeepSignalShard, utf8ByteLength } from "./deep-signal-store.ts";
import type {
  DeepSignalFileSystem,
  DeepSignalRecord,
} from "./deep-signal-store";
import type { PickedPhoto } from "../import/picked-photo";

function assert(condition: unknown, message: string): asserts condition {
  if (!condition) throw new Error(`deep-signal-store self-check failed: ${message}`);
}

const JANUARY = Date.UTC(2026, 0, 15, 12);

function photoAt(id: string, capturedAt = JANUARY): PickedPhoto {
  return {
    id,
    uri: `content://media/external/images/media/${id}`,
    filename: `IMG_${id}.jpg`,
    source: "device-gallery",
    creationTime: capturedAt,
    width: 4_000,
    height: 3_000,
    mimeType: "image/jpeg",
  };
}

function vector(length: number, seed: number): number[] {
  return Array.from({ length }, (_, index) => Math.sin(seed * 3.1 + index * 0.37) * 0.4);
}

function recordFor(seed: number): DeepSignalRecord {
  return {
    analysisWidth: 1_280,
    analysisHeight: 960,
    perceptual: { embedding: vector(76, seed), faces: 2 },
    boxes: [
      {
        x: 100.5,
        y: 220.25,
        width: 180,
        height: 190,
        leftEyeOpen: 0.93,
        rightEyeOpen: 0.88,
        smiling: 0.42,
        headEulerAngleY: -3.5,
      },
    ],
    quality: {
      sharpness: 0.6123456789012345,
      exposure: 0.48,
      clippedFraction: 0.012,
      faceSharpness: 0.71,
      subjectSharpness: 0.66,
      subjectBackgroundRatio: 0.58,
      blurhash: "LEHV6nWB2yk8pyo0adR*.7kCMdnj",
    },
    pose: {
      keypoints: Array.from({ length: 17 }, (_, index) => [index / 17, 1 - index / 17] as const).map(
        ([x, y]) => [x, y] as [number, number],
      ),
      scores: vector(17, seed + 1).map(Math.abs),
    },
    semantic: {
      embedding: vector(512, seed + 2),
      aesthetic: 0.5123,
      composed: 0.44,
      cleanFrame: 0.61,
      sleeping: 0.02,
      awake: 0.98,
      embraceContext: 0.31,
      screenshotDocument: 0.004,
    },
  };
}

// --- 1. The codec ----------------------------------------------------------

const FLOAT32_EPS = 1e-6;

/** Every vector within eps, every scalar EXACT. Returns the worst drift seen. */
function driftBetween(
  left: DeepSignalRecord,
  right: DeepSignalRecord,
): number | undefined {
  const vectors: Array<[readonly number[], readonly number[]]> = [
    [left.perceptual.embedding, right.perceptual.embedding],
    [left.pose?.scores ?? [], right.pose?.scores ?? []],
    [left.semantic?.embedding ?? [], right.semantic?.embedding ?? []],
    [
      (left.pose?.keypoints ?? []).flatMap(([x, y]) => [x, y]),
      (right.pose?.keypoints ?? []).flatMap(([x, y]) => [x, y]),
    ],
  ];
  let worst = 0;
  for (const [a, b] of vectors) {
    if (a.length !== b.length) return undefined;
    for (let index = 0; index < a.length; index += 1) {
      worst = Math.max(worst, Math.abs(a[index] - b[index]));
    }
  }
  if (worst > FLOAT32_EPS) return undefined;
  const scalarsMatch =
    JSON.stringify(left.quality) === JSON.stringify(right.quality) &&
    JSON.stringify(left.boxes) === JSON.stringify(right.boxes) &&
    left.perceptual.faces === right.perceptual.faces &&
    left.analysisWidth === right.analysisWidth &&
    left.analysisHeight === right.analysisHeight &&
    left.semantic?.aesthetic === right.semantic?.aesthetic &&
    left.semantic?.screenshotDocument === right.semantic?.screenshotDocument;
  return scalarsMatch ? worst : undefined;
}

const original = recordFor(1);
const encoded = encodeDeepSignalRecord(original);
const decoded = decodeDeepSignalRecord(encoded);
assert(decoded !== undefined, "a record this build wrote must be readable by this build");
const codecDrift = driftBetween(original, decoded);
assert(
  codecDrift !== undefined,
  "the round trip must preserve every scalar exactly and every vector within float32",
);
assert(
  codecDrift < 1e-7,
  `float32 drift must stay in the eighth decimal, measured ${codecDrift}`,
);

// VACUITY. `driftBetween` is the whole correctness claim of the codec, so it has
// to be shown capable of failing. A comparison that returned a number for
// anything would have passed the assertion above while proving nothing.
const nudgedVector = {
  ...original,
  perceptual: {
    ...original.perceptual,
    embedding: original.perceptual.embedding.map((value, index) =>
      index === 3 ? value + 1e-3 : value,
    ),
  },
};
assert(
  driftBetween(original, nudgedVector) === undefined,
  "VACUITY: one component moved by 1e-3 must FAIL the drift check",
);
const nudgedScalar = {
  ...original,
  quality: { ...original.quality, sharpness: 0.6123456789012346 },
};
assert(
  driftBetween(original, nudgedScalar) === undefined,
  "VACUITY: a scalar that changed in its last bit must FAIL -- scalars are stored exactly",
);
assert(
  driftBetween(original, { ...original, pose: undefined }) === undefined,
  "VACUITY: a record that lost its pose must FAIL rather than compare zero vectors",
);

// The record size the store's whole design rests on.
const recordBytes = utf8ByteLength(encoded);
assert(
  recordBytes > 3_000 && recordBytes < 4_500,
  `a deep record must be about 3.9 KB, measured ${recordBytes} B -- the shard and ` +
    "store budgets are derived from this number and a large move invalidates them",
);

assert(
  decodeDeepSignalRecord(encoded, DEEP_SIGNAL_VERSION + 1) === undefined,
  "a record written by another signal version must read as a MISS, never as data",
);
assert(
  decodeDeepSignalRecord(encoded, DEEP_SIGNAL_VERSION) !== undefined,
  "VACUITY: ...and the matching version must still read, or the gate is just 'never hit'",
);
assert(
  decodeDeepSignalRecord("{not json") === undefined &&
    decodeDeepSignalRecord("null") === undefined,
  "a truncated or corrupt line must be a miss, not a throw",
);

// Half a pose is worse than none: `bodyCoverage` reads keypoints positionally.
const halfPose = JSON.parse(encoded);
halfPose.ps = encodeFloat32([1, 2, 3]);
assert(
  decodeDeepSignalRecord(JSON.stringify(halfPose))?.pose === undefined,
  "keypoints and scores of disagreeing length must drop the pose entirely",
);

assert(
  decodeFloat32(encodeFloat32([]))?.length === 0,
  "an empty vector must survive the codec as an empty vector",
);
assert(decodeFloat32("!!!!") === undefined, "a non-base64 vector must decode to nothing");

// --- 2. Shard identity -----------------------------------------------------

assert(
  deepSignalShardId(photoAt("a", Date.UTC(2026, 0, 31, 23, 59))) === "2026-01" &&
    deepSignalShardId(photoAt("b", Date.UTC(2026, 1, 1, 0, 0))) === "2026-02",
  "the shard boundary is the UTC month boundary",
);
assert(
  deepSignalShardId({ ...photoAt("c"), creationTime: undefined }) === "undated",
  "a photo with no capture time still needs somewhere to live",
);
assert(
  deepSignalShardId({ ...photoAt("d"), creationTime: Number.NaN }) === "undated",
  "an unparseable capture time must not produce a NaN filename",
);
// VACUITY: without this, an implementation that answered "undated" to everything
// would satisfy every assertion above and put the whole library in one shard.
assert(
  deepSignalShardId(photoAt("e", Date.UTC(2024, 5, 2))) !==
    deepSignalShardId(photoAt("f", Date.UTC(2026, 5, 2))),
  "VACUITY: two different months must be two different shards",
);
assert(
  isValidShardId("2026-01") && isValidShardId("undated"),
  "the ids the store itself produces must be accepted",
);
assert(
  !isValidShardId("../../face-observations") && !isValidShardId("2026-1"),
  "shard ids reach the filesystem, so anything path-shaped must be refused",
);

// --- 3. The shard, and the record it must not eat --------------------------

const shardEntries = new Map(
  Array.from({ length: 6 }, (_, index) => [
    `key-${index}`,
    encodeDeepSignalRecord(recordFor(index)),
  ]),
);
const shardText = serializeDeepSignalShard(shardEntries);
const reparsed = parseDeepSignalShard(shardText);
assert(
  reparsed.size === shardEntries.size &&
    [...shardEntries].every(([key, value]) => reparsed.get(key) === value),
  "a shard must round-trip every record it holds",
);
assert(
  parseDeepSignalShard("garbage\nkey\tvalue").size === 0,
  "a shard with an unknown format header must read as empty, not as records",
);

/**
 * The regression this layout invites.
 *
 * The shard is read as lines and only the records the build asks for are ever
 * JSON-parsed. An earlier draft filtered the PARSE by the wanted keys, which is
 * cheaper by a Map entry and silently deletes the rest of the month on the next
 * write. Reading one record and persisting must keep all six.
 */
const afterOneRead = parseDeepSignalShard(shardText);
assert(
  decodeDeepSignalRecord(afterOneRead.get("key-2")!) !== undefined,
  "the record being read must decode",
);
assert(
  parseDeepSignalShard(serializeDeepSignalShard(afterOneRead)).size === 6,
  "reading ONE record and writing the shard back must keep the other five",
);

// The month bound drops the oldest, keeps the newest.
const bounded = serializeDeepSignalShard(shardEntries, 3 * recordBytes);
const boundedKeys = [...parseDeepSignalShard(bounded).keys()];
assert(
  boundedKeys.length >= 1 && boundedKeys.length <= 3,
  `a bound of three records must keep at most three, kept ${boundedKeys.length}`,
);
assert(
  boundedKeys.includes("key-5") && !boundedKeys.includes("key-0"),
  "truncation must drop the least recently used end, not an arbitrary one",
);
// VACUITY: a bound generous enough for everything must drop nothing, or
// "always truncate" would pass the assertion above.
assert(
  parseDeepSignalShard(serializeDeepSignalShard(shardEntries, 10 * recordBytes)).size === 6,
  "VACUITY: under the bound, nothing is dropped",
);

// --- 4. Store eviction -----------------------------------------------------

const manifest = new Map([
  ["2026-01", { bytes: 2_000_000, usedAt: 100 }],
  ["2026-02", { bytes: 2_000_000, usedAt: 300 }],
  ["2026-03", { bytes: 2_000_000, usedAt: 200 }],
]);
assert(
  planShardEviction(manifest, 10_000_000).length === 0,
  "VACUITY: a store inside its budget must evict nothing",
);
const evicted = planShardEviction(manifest, 5_000_000);
assert(
  JSON.stringify(evicted) === JSON.stringify(["2026-01"]),
  `the least recently used month goes first, got ${JSON.stringify(evicted)}`,
);
assert(
  JSON.stringify(planShardEviction(manifest, 1_500_000)) ===
    JSON.stringify(["2026-01", "2026-03", "2026-02"]),
  "eviction continues in recency order until the budget is met",
);
assert(
  !planShardEviction(manifest, 5_000_000).includes("2026-02"),
  "VACUITY: the most recently used month must NOT be the one dropped",
);

// --- 5. The store, end to end ----------------------------------------------

function memoryFileSystem() {
  const files = new Map<string, string>();
  const fileSystem: DeepSignalFileSystem = {
    readAsStringAsync: async (uri) => {
      const value = files.get(uri);
      if (value === undefined) throw new Error(`ENOENT ${uri}`);
      return value;
    },
    writeAsStringAsync: async (uri, contents) => {
      files.set(uri, contents);
    },
    deleteAsync: async (uri) => {
      files.delete(uri);
    },
    moveAsync: async ({ from, to }) => {
      const value = files.get(from);
      if (value === undefined) throw new Error(`ENOENT ${from}`);
      files.set(to, value);
      files.delete(from);
    },
    makeDirectoryAsync: async () => undefined,
  };
  return { files, fileSystem };
}

const disk = memoryFileSystem();
const january = photoAt("jan-1", JANUARY);
const march = photoAt("mar-1", Date.UTC(2026, 2, 3));

{
  const store = await openDeepSignalStore(disk.fileSystem, "file:///docs/deep", {
    now: () => 1_000,
  });
  assert(
    store.get(january) === undefined && store.stats().misses === 1,
    "a cold store must miss and must say it missed",
  );
  // The guard: a write into a month this build never read would replace that
  // month's file with this build's records alone.
  store.set(january, recordFor(7));
  assert(
    store.stats().refusedWrites === 1 && store.stats().writes === 0,
    "a write before the shard is loaded must be refused and counted",
  );
  await store.load([january, march]);
  store.set(january, recordFor(7));
  assert(
    store.stats().writes === 1 && store.stats().refusedWrites === 1,
    "VACUITY: ...and the same write AFTER the load must be accepted",
  );
  await store.persist();
}

assert(
  [...disk.files.keys()].some((uri) => uri.endsWith("2026-01.ndjson")),
  "the shard must be written under its own month",
);
assert(
  !disk.files.has("file:///docs/deep/2026-03.ndjson"),
  "a month that received no records must not be written",
);
assert(
  [...disk.files.values()].every(
    (contents) =>
      !contents.includes("content://") &&
      !contents.includes("IMG_") &&
      !contents.includes(".jpg"),
  ),
  "no uri, filename or path may ever reach disk -- these are derived signals only",
);

{
  const store = await openDeepSignalStore(disk.fileSystem, "file:///docs/deep", {
    now: () => 2_000,
  });
  await store.load([january]);
  const hit = store.get(january);
  assert(hit !== undefined, "a record persisted by the previous session must be found");
  assert(
    driftBetween(recordFor(7), hit) !== undefined,
    "and it must be the same record, within float32",
  );
  assert(
    store.get(photoAt("jan-2", JANUARY)) === undefined,
    "VACUITY: a photo that was never analysed must still miss in the same shard",
  );
  assert(
    store.get({ ...january, creationTime: JANUARY + 1 }) === undefined,
    "a changed asset revision must miss: capture time is part of the key",
  );
  assert(
    store.get({ ...january, source: "local-folder" }) === undefined,
    "folder picks expose no revision, so they are never served from cache",
  );
}

// Crash safety: a temporary checkpoint that survived a kill is the newer truth.
{
  const crashed = memoryFileSystem();
  const good = serializeDeepSignalShard(
    new Map([["key-a", encodeDeepSignalRecord(recordFor(9))]]),
  );
  crashed.files.set("file:///docs/deep/2026-01.ndjson", "corrupt-header\nnonsense");
  crashed.files.set("file:///docs/deep/2026-01.ndjson.tmp", good);
  const store = await openDeepSignalStore(crashed.fileSystem, "file:///docs/deep");
  await store.load([january]);
  assert(
    store.stats().recordsLoaded === 1,
    "the .tmp checkpoint must be preferred over a durable file that cannot be parsed",
  );
}
{
  const broken = memoryFileSystem();
  broken.files.set("file:///docs/deep/2026-01.ndjson", "{{{ not a shard");
  const store = await openDeepSignalStore(broken.fileSystem, "file:///docs/deep");
  await store.load([january]);
  assert(
    store.get(january) === undefined && store.stats().recordsLoaded === 0,
    "a corrupt shard degrades to empty rather than failing the album",
  );
}

// Whole-shard eviction, driven end to end by the byte budget.
{
  const small = memoryFileSystem();
  let clock = 0;
  const store = await openDeepSignalStore(small.fileSystem, "file:///docs/deep", {
    maxStoreBytes: 2 * recordBytes,
    now: () => (clock += 1_000),
  });
  const months = [
    photoAt("m1", Date.UTC(2026, 0, 2)),
    photoAt("m2", Date.UTC(2026, 1, 2)),
    photoAt("m3", Date.UTC(2026, 2, 2)),
  ];
  for (const photo of months) {
    await store.load([photo]);
    store.set(photo, recordFor(3));
    await store.persist();
  }
  assert(
    store.stats().shardsEvicted >= 1,
    "three months against a two-record budget must evict",
  );
  assert(
    !small.files.has("file:///docs/deep/2026-01.ndjson") &&
      small.files.has("file:///docs/deep/2026-03.ndjson"),
    "the month written longest ago is the one that goes",
  );
  assert(
    store.stats().bytes <= 2 * recordBytes + 128,
    `the store must end inside its budget, ended at ${store.stats().bytes} B`,
  );
}

console.log(
  `deep-signal-store self-check passed ` +
    `(record ${recordBytes} B, float32 drift ${codecDrift.toExponential(2)}, ` +
    `full 11,854-photo library ${((recordBytes * 11854) / 1e6).toFixed(1)} MB)`,
);
