import type { PickedPhoto } from "../import/picked-photo";
// @ts-expect-error Node requires the extension; Metro resolves this path too.
import { candidateProbeKey, isCandidateProbeCacheable, parseCandidateProbeCache, probeCandidateWithCache, serializeCandidateProbeCache, type CandidateProbeCache } from "./candidate-probe-cache.ts";
// @ts-expect-error Node requires the extension; Metro resolves this path too.
import { CANDIDATE_PROBE_SIGNAL_VERSION } from "./candidate-quality-probe.ts";

function assert(condition: unknown, message: string): asserts condition {
  if (!condition) throw new Error(`Candidate probe cache self-check failed: ${message}`);
}

const photo: PickedPhoto = {
  id: "asset-1",
  uri: "file:///private/family/photo.jpg",
  filename: "photo.jpg",
  source: "device-gallery",
  creationTime: 123,
  width: 4_000,
  height: 3_000,
};
const key = candidateProbeKey(photo);
assert(!key.includes("private") && !key.includes("photo.jpg"), "cache key must not persist paths or filenames");
assert(candidateProbeKey({ ...photo, id: "asset-2" }) !== key, "different asset ids must not collide in the fixture");
assert(
  !isCandidateProbeCacheable({ ...photo, source: "local-folder" }),
  "a mutable folder path without a content hash must never reuse stale quality",
);
assert(
  candidateProbeKey({ ...photo, width: 4_001 }) !== key,
  "changed asset metadata must invalidate derived evidence",
);

const entries = new Map([
  [key, { sharpness: 0.4, exposure: 0.5, clippedFraction: 0, blurhash: "hash" }],
]);
const serialized = serializeCandidateProbeCache(entries);
const restored = parseCandidateProbeCache(serialized);
assert(restored.size === 1, "one valid checkpoint entry must round-trip");
assert(restored.get(key)?.blurhash === "hash", "round-trip must retain content evidence");
// Regression check: the current signal version reuses the stored entry (the
// vacuity guard), while the same checkpoint is a miss after an algorithm bump.
assert(restored.has(key), "the same signal version must reuse cached evidence");
const afterSignalChange = parseCandidateProbeCache(
  serialized,
  CANDIDATE_PROBE_SIGNAL_VERSION + 1,
);
assert(
  !afterSignalChange.has(key),
  "a signal version bump must not reuse pre-change evidence",
);

const legacyCheckpoint = JSON.stringify({
  version: 1,
  entries: [{ key, quality: entries.get(key) }],
});
assert(
  !parseCandidateProbeCache(legacyCheckpoint).has(key),
  "a checkpoint entry without a signal version must be stale",
);

// Vacuity guards: malformed storage and a transient empty native result are
// both plausible inputs, but neither may become a cache hit.
assert(parseCandidateProbeCache("not json").size === 0, "corrupt checkpoints fail empty");
const emptyResult = JSON.stringify({
  version: 1,
  entries: [{
    key: "failed-probe",
    signalVersion: CANDIDATE_PROBE_SIGNAL_VERSION,
    quality: {},
  }],
});
assert(
  parseCandidateProbeCache(emptyResult).size === 0,
  "a transient probe failure must be retried next build",
);

let probes = 0;
let writes = 0;
const hitCache: CandidateProbeCache = {
  get: () => ({ sharpness: 0.9, blurhash: "cached" }),
  set: () => { writes += 1; },
  persist: async () => undefined,
};
const hit = await probeCandidateWithCache(photo, hitCache, async () => {
  probes += 1;
  return { blurhash: "fresh" };
});
assert(hit.cacheHit && hit.quality.blurhash === "cached", "a cache hit must return exact saved evidence");
assert(probes === 0 && writes === 0, "a cache hit must skip native probe and rewrite");

const missCache: CandidateProbeCache = {
  ...hitCache,
  get: () => undefined,
};
const miss = await probeCandidateWithCache(photo, missCache, async () => {
  probes += 1;
  return { blurhash: "fresh" };
});
// Vacuity guard for the speed decision: the same fixture can and does enter
// the expensive branch when the key is absent.
assert(!miss.cacheHit && Number(probes) === 1, "a miss must execute the native probe once");
assert(Number(writes) === 1, "fresh evidence must become the next build's checkpoint");

console.log("candidate-probe-cache self-check passed");
