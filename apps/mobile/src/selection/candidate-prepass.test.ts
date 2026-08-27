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

// --- content coverage -------------------------------------------------------
//
// The case the other fixtures cannot express: ONE session. Every candidate
// shares a place and carries no usable timestamp, so the time and place terms
// are both constant and the cap has nothing left to spread on but quality --
// and quality is highest inside bursts, which is how an album ends up as sixty
// photos of the same pose. The blurhash is the only thing that still tells two
// moments apart at this stage.

const BASE83 =
  "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz#$%*+,-.:;=?@[]^_{|}~";

function encode83(value: number, length: number): string {
  let out = "";
  for (let position = 1; position <= length; position += 1) {
    const digit = Math.floor(value / 83 ** (length - position)) % 83;
    out += BASE83[digit];
  }
  return out;
}

/**
 * A valid 4x3 blurhash of a FLAT frame at grey level `level`.
 *
 * Flat because the test is about whether two frames are told apart, not about
 * decode fidelity: every AC component is pinned to its zero point (9,9,9 in
 * blurhash's 19-level quantisation, i.e. 9*361 + 9*19 + 9) so only the DC term
 * survives and the decoded grid is uniformly `level`.
 */
function flatBlurhash(level: number): string {
  const sizeFlag = 4 - 1 + (3 - 1) * 9;
  const dc = level * 65_793; // r == g == b == level
  const acZero = encode83(9 * 361 + 9 * 19 + 9, 2);
  return (
    BASE83[sizeFlag] + BASE83[0] + encode83(dc, 4) + acZero.repeat(4 * 3 - 1)
  );
}

function sessionCandidate(
  id: string,
  level: number,
  sharpness: number,
): ProbedCandidate {
  return {
    photo: {
      id,
      uri: `asset://${id}`,
      filename: `${id}.jpg`,
      source: "device-gallery",
      // No creationTime on purpose: kills the time axis outright.
      width: 4_000,
      height: 3_000,
      placeKey: "studio",
    },
    quality: {
      sharpness,
      exposure: 0.5,
      clippedFraction: 0,
      blurhash: flatBlurhash(level),
    },
  };
}

// Sanity: the fixture must actually produce two DISTINGUISHABLE looks, or the
// assertion below would pass for the wrong reason.
assert(
  flatBlurhash(30) !== flatBlurhash(220),
  "the two fixture looks must differ as blurhashes",
);

// Twelve frames of one look, all sharper than the four frames of the other.
// On quality alone every slot goes to the burst.
const burst = Array.from({ length: 12 }, (_, index) =>
  sessionCandidate(`burst-${index}`, 30, 0.95),
);
const otherLook = Array.from({ length: 4 }, (_, index) =>
  sessionCandidate(`other-${index}`, 220, 0.5),
);
const session = chooseHeavyAnalysisCandidates([...burst, ...otherLook], 4);
assert(
  session.length === 4,
  `the session pick must respect the limit (got ${session.length})`,
);
assert(
  session.some(({ photo }) => photo.id.startsWith("other-")),
  "a second look must reach the planner even when every frame of it is the " +
    "least sharp photo in the pick — otherwise the album is one pose",
);
assert(
  session.filter(({ photo }) => photo.id.startsWith("burst-")).length < 4,
  "the burst must not take every slot",
);

// --- content coverage meets moment reservations -----------------------------
//
// The two gates have to hold at once, and the case that tells them apart is a
// scene REVISITED: a look the pool has already covered, photographed again an
// hour later. The content axis is right to say "seen that", the time axis is a
// quantile a burst has already ticked off, and between them a whole later
// moment disappears without leaving a trace. That is the coverage loss M5 is
// about, and it is the one a reservation has to fix WITHOUT becoming a way for
// a thirteenth frame of the burst to get in.

function timedSessionCandidate(
  id: string,
  level: number,
  sharpness: number,
  minutes: number,
): ProbedCandidate {
  const timed = sessionCandidate(id, level, sharpness);
  return {
    ...timed,
    photo: { ...timed.photo, creationTime: Date.UTC(2025, 4, 9, 9, 0, 0) + minutes * 60_000 },
  };
}

// Twelve frames of one look; four of a second look forty minutes later; and one
// LAST frame of that same second look an hour after that — the worst photograph
// in the set, and the only evidence that the evening happened at all.
const revisitBurst = Array.from({ length: 12 }, (_, index) =>
  timedSessionCandidate(`revisit-burst-${index}`, 30, 0.95, index / 3),
);
const revisitSecond = Array.from({ length: 4 }, (_, index) =>
  timedSessionCandidate(`revisit-second-${index}`, 220, 0.5, 40 + index / 3),
);
const revisitLate = timedSessionCandidate("revisit-late-00", 220, 0.3, 100);
const revisitCorpus = [...revisitBurst, ...revisitSecond, revisitLate];

const REVISIT_BUDGET = 6;
const revisitBlind = chooseHeavyAnalysisCandidates(revisitCorpus, REVISIT_BUDGET, {
  reserveMoments: false,
});
const revisitReserved = chooseHeavyAnalysisCandidates(revisitCorpus, REVISIT_BUDGET);
const burstCount = (pool: ProbedCandidate[]) =>
  pool.filter(({ photo }) => photo.id.startsWith("revisit-burst-")).length;

// The foil. If the old gate already keeps the late moment, nothing below is
// evidence of anything the reservation did.
assert(
  !revisitBlind.some(({ photo }) => photo.id === "revisit-late-00"),
  "VACUITY: the unreserved gate already keeps the revisited late moment, so the reservation " +
    "assertion below proves nothing — this corpus no longer starves anything",
);
assert(
  revisitReserved.some(({ photo }) => photo.id === "revisit-late-00"),
  "a moment nothing else covers must reach deep analysis even when its look is already in " +
    "the pool and its frame is the worst in the set",
);
// ...and the reservation must not have bought that seat by loosening the burst.
assert(
  burstCount(revisitReserved) <= burstCount(revisitBlind),
  `reserving a moment must not deepen the burst (${burstCount(revisitBlind)} -> ${burstCount(revisitReserved)})`,
);
assert(
  revisitReserved.length === REVISIT_BUDGET && burstCount(revisitReserved) < REVISIT_BUDGET,
  "the burst must still not take the whole pool once reservations are in play",
);

// The axis must stay inert rather than throw when no blurhash was probed, which
// is the uncapped path and any frame whose proxy failed.
const hashless = Array.from({ length: 6 }, (_, index) =>
  candidate(`hashless-${index}`, index),
);
assert(
  chooseHeavyAnalysisCandidates(hashless, 3).length === 3,
  "candidates without a blurhash must still be selectable",
);

// eslint-disable-next-line no-console
console.log("candidate-prepass self-check passed");
