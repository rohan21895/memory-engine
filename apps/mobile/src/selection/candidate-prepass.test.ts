// Pure module self-checks; Node 22's native TypeScript runner executes this
// file directly and treats any failed assertion as a failed test.
// @ts-expect-error Node requires the extension; Metro resolves this path too.
import { chooseHeavyAnalysisCandidates, HEAVY_ANALYSIS_CANDIDATE_LIMIT, type ProbedCandidate } from "./candidate-prepass.ts";

function assert(condition: unknown, message: string): asserts condition {
  if (!condition) throw new Error(`Candidate pre-pass self-check failed: ${message}`);
}

function candidate(
  id: string,
  index: number,
  overrides: Partial<ProbedCandidate["photo"]> = {},
  sharpness = 0.6,
): ProbedCandidate {
  return {
    photo: {
      id,
      uri: `asset://${id}`,
      filename: `${id}.jpg`,
      source: "device-gallery",
      creationTime: Date.UTC(2025, 0, 1) + index * 24 * 60 * 60 * 1_000,
      width: 4_000,
      height: 3_000,
      ...overrides,
    },
    quality: { sharpness, exposure: 0.5, clippedFraction: 0 },
  };
}

const small = Array.from({ length: 8 }, (_, index) => candidate(`small-${index}`, index));
assert(
  chooseHeavyAnalysisCandidates(small, 10).map(({ photo }) => photo.id).join(",") ===
    small.map(({ photo }) => photo.id).join(","),
  "an uncapped selection must retain every photo and its input order",
);

const longTrip = Array.from({ length: 120 }, (_, index) =>
  candidate(`trip-${String(index).padStart(3, "0")}`, index, {
    placeKey: index < 100 ? "home" : `place-${index % 4}`,
  }, index < 20 ? 0.98 : 0.55),
);
const tripPicks = chooseHeavyAnalysisCandidates(longTrip, 12);
assert(tripPicks.length === 12, "the candidate cap must be exact");
assert(
  tripPicks.some(({ photo }) => Number(photo.id.slice(-3)) >= 110),
  "late time windows must survive an early high-quality burst",
);
assert(
  new Set(tripPicks.map(({ photo }) => photo.placeKey)).size > 1,
  "place coverage must survive a dominant location",
);
assert(
  tripPicks.some(({ photo }) => photo.id === "trip-000") &&
    tripPicks.some(({ photo }) => Number(photo.id.slice(-3)) >= 100),
  "quality and coverage should both contribute candidates",
);

const pin = candidate("pinned", 200, { pinned: true }, 0);
assert(
  chooseHeavyAnalysisCandidates([...longTrip, pin], 8).some(
    ({ photo }) => photo.id === "pinned",
  ),
  "a pinned photo must survive the pre-pass",
);

const forward = chooseHeavyAnalysisCandidates(longTrip, 15)
  .map(({ photo }) => photo.id)
  .sort();
const reverse = chooseHeavyAnalysisCandidates(longTrip.slice().reverse(), 15)
  .map(({ photo }) => photo.id)
  .sort();
assert(
  JSON.stringify(forward) === JSON.stringify(reverse),
  "candidate membership must not depend on picker order",
);

// --- The pre-pass can never hand the planner an empty candidate set ----------
// A blurhash-derived probe reads the SAME ~0.05 sharpness for every photo by
// construction, so a library can arrive with no quality spread at all. That
// must degrade to "rank on coverage" and still return a full pool, never to an
// empty one - an empty candidate set is an empty album.
const flat = Array.from({ length: 400 }, (_, index) =>
  candidate(`flat-${String(index).padStart(3, "0")}`, index, {}, 0.05),
);
assert(
  chooseHeavyAnalysisCandidates(flat, 16).length === 16,
  "an unmeasurable-quality library still fills the candidate pool",
);
// Probes that failed outright leave no quality fields at all.
const unmeasured: ProbedCandidate[] = flat.map(({ photo }) => ({ photo, quality: {} }));
assert(
  chooseHeavyAnalysisCandidates(unmeasured, 16).length === 16,
  "a library whose quality probe failed entirely still fills the candidate pool",
);
assert(
  chooseHeavyAnalysisCandidates([candidate("only", 0, {}, 0)], 16).length === 1,
  "a single candidate survives its own ranking",
);
assert(
  chooseHeavyAnalysisCandidates([], 16).length === 0,
  "an empty input stays empty",
);

// --- "Select all" over a whole library cannot bypass the cap ----------------
const library = Array.from({ length: 11_793 }, (_, index) =>
  candidate(`lib-${String(index).padStart(5, "0")}`, index, {
    placeKey: `place-${index % 7}`,
    // Every photo pinned is the worst case: pins are sovereign, but the safety
    // cap still has to win, or a "Select all" would run heavy models on 11,793
    // photos and never finish.
    pinned: true,
  }),
);
const capped = chooseHeavyAnalysisCandidates(library);
assert(
  capped.length === HEAVY_ANALYSIS_CANDIDATE_LIMIT,
  `a full-library pick is capped at ${HEAVY_ANALYSIS_CANDIDATE_LIMIT} (got ${capped.length})`,
);
assert(
  new Set(capped.map(({ photo }) => photo.id)).size === capped.length,
  "the capped set contains no duplicates",
);

// eslint-disable-next-line no-console
console.log("candidate-prepass self-check passed");
