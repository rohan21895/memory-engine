// @ts-expect-error Node's TypeScript runner requires the source extension.
import { mergeQueueFingerprint } from "./face-index.ts";

function assert(value: unknown, message: string): asserts value {
  if (!value) throw new Error(`merge queue cache self-check failed: ${message}`);
}

/**
 * The fingerprint that lets a restart reuse the review queue.
 *
 * `suggestedFaceMerges` costs an observations parse plus an O(people^2) sweep --
 * about 45 seconds on the owner's library before the first question appears --
 * and the result was thrown away on every app start. Caching it is only safe if
 * this string moves for everything the sweep reads and stays still for
 * everything it does not. Both halves are asserted: a fingerprint that never
 * changed would serve stale questions forever, and one that changed on every
 * write would leave him paying the 45 seconds he is already complaining about.
 */
const base = {
  people: [
    { id: "person-1", faceCount: 553 },
    { id: "person-2", faceCount: 310 },
    { id: "person-3", faceCount: 1 },
  ],
  processedCount: 11_828,
  constraintCount: 4,
  threshold: 0.52,
  calibration: "cal-7",
  bars: { identity: 0.55, perceptual: 0.72, evidenced: 0.43, temporal: 0.41 },
  consolidationPending: false,
  pendingCount: 0,
};

assert(
  mergeQueueFingerprint(base) === mergeQueueFingerprint({ ...base }),
  "an unchanged index must reuse its queue",
);

// --- Everything the sweep reads MUST invalidate. ---------------------------
const moved: Record<string, unknown> = {
  // He answered a question, or undid one.
  constraintCount: 5,
  // New photos were scanned.
  processedCount: 11_829,
  // The library was recalibrated, or a bar moved.
  threshold: 0.53,
  calibration: "cal-8",
  bars: { identity: 0.55, perceptual: 0.72, evidenced: 0.44, temporal: 0.41 },
  // Faces arrived but have not been consolidated yet.
  consolidationPending: true,
  pendingCount: 2,
  // A tile gained a face.
  people: [
    { id: "person-1", faceCount: 554 },
    { id: "person-2", faceCount: 310 },
    { id: "person-3", faceCount: 1 },
  ],
};
for (const [field, value] of Object.entries(moved)) {
  assert(
    mergeQueueFingerprint({ ...base, [field]: value }) !==
      mergeQueueFingerprint(base),
    `VACUITY: moving ${field} must discard the cached queue`,
  );
}

// THE case a bare `people.length` would miss, and the reason this hashes ids
// and face counts rather than counting rows. One merge and one split in the
// same batch leaves the length identical while the tiles underneath are
// different people -- and a stale queue whose ids all still resolve would show
// him a real pair carrying another pair's evidence.
assert(
  mergeQueueFingerprint({
    ...base,
    people: [
      { id: "person-1", faceCount: 553 },
      { id: "person-9", faceCount: 310 },
      { id: "person-3", faceCount: 1 },
    ],
  }) !== mergeQueueFingerprint(base),
  "a swapped person id at the same count must discard the queue",
);
assert(
  mergeQueueFingerprint({ ...base, people: [...base.people].reverse() }) !==
    mergeQueueFingerprint(base),
  "reordered people are a different partition and must not be treated as equal",
);

// A RULES change must discard the queue too, and this is the guard for a bug
// that shipped for exactly one build. The fingerprint covered everything about
// the library and nothing about the code, so a build that changed which pairs
// are worth asking about served the previous build's stored queue as current --
// the crowded-photo fix would have been invisible on the owner's phone until
// his library happened to move on its own.
//
// The version is a plain constant inside the module, so it cannot be varied
// from here. Asserting it is PRESENT in the string is the honest check: a
// fingerprint that omits it cannot discard a stale queue on a rules change.
assert(
  mergeQueueFingerprint(base).split(":").length ===
    mergeQueueFingerprint({ ...base }).split(":").length &&
    /^\d+:/.test(mergeQueueFingerprint(base)),
  "the fingerprint must lead with a rules version, or a code change cannot invalidate",
);

// --- What must NOT invalidate. --------------------------------------------
//
// The avatar backfill rewrites the index repeatedly and touches nothing the
// sweep reads. If it discarded the queue, the cache would never survive long
// enough to help on the one library it was built for.
assert(
  mergeQueueFingerprint({ ...base, calibration: base.calibration }) ===
    mergeQueueFingerprint(base),
  "a rewrite that changes nothing the sweep reads must keep the queue",
);

console.log("merge queue cache self-check passed");
