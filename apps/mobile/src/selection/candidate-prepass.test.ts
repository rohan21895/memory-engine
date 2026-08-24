// Pure module self-checks; Node 22's native TypeScript runner executes this
// file directly and treats any failed assertion as a failed test.
// @ts-expect-error Node requires the extension; Metro resolves this path too.
import { chooseHeavyAnalysisCandidates, type ProbedCandidate } from "./candidate-prepass.ts";

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
