/**
 * M5: the candidate budget as a measured price, and moment reservations as a
 * candidate guarantee (EXPERT-PLAN §14).
 *
 * The corpora are the three PINNED album fixtures, run through the prepass the
 * way production runs it — as an event whose photographs outnumber the budget —
 * and then through the planner. That is deliberate: `album-fixtures.test.ts`
 * pins what the planner does with a pool of 64, and this file measures what the
 * pool ITSELF loses before the planner ever sees it. Nothing here can be
 * satisfied by a number invented for the occasion.
 *
 * One honest limitation, stated up front so no assertion below is read as
 * stronger than it is: the fixtures carry embeddings, not blurhashes, so the
 * prepass's content axis is inert here and cannot suppress a burst. This makes
 * every coverage claim CONSERVATIVE (the gate is measured with one of its five
 * axes switched off) and it means the near-duplicate counts in this file are an
 * upper bound on the real thing, not a prediction of it.
 */

// @ts-expect-error Node requires the extension; Metro resolves this path too.
import { albumFixtures } from "./album-fixtures.ts";
// @ts-expect-error Node requires the extension; Metro resolves this path too.
import { planAlbum } from "./album-planner.ts";
// @ts-expect-error Node requires the extension; Metro resolves this path too.
import { candidateBudget, chooseHeavyAnalysisCandidates, CANDIDATE_BUDGET_MAX, CANDIDATE_BUDGET_MIN, DEEP_ANALYSIS_BUDGET_MS, DEEP_ANALYSIS_MS_PER_CANDIDATE, HEAVY_ANALYSIS_CANDIDATE_LIMIT, MOMENT_RESERVE_FRACTION } from "./candidate-prepass.ts";
import type { ProbedCandidate } from "./candidate-prepass";
import type { AlbumFixture } from "./album-fixtures";
import type { PlannerCandidate } from "./album-planner";

function assert(condition: unknown, message: string): asserts condition {
  if (!condition) throw new Error(`Candidate budget/reserve self-check failed: ${message}`);
}

// --- 1. The budget is a price, not a constant -------------------------------
//
// docs/DEEP-ANALYSIS-TIMING.md: 148,837 ms of deep analysis for 64 photos. The
// two constants have to keep saying that, or the budget arithmetic is decoration.

assert(
  Math.abs(DEEP_ANALYSIS_BUDGET_MS / HEAVY_ANALYSIS_CANDIDATE_LIMIT - DEEP_ANALYSIS_MS_PER_CANDIDATE) < 1,
  `the per-candidate price must remain the measured stage divided by its 64 photos ` +
    `(${DEEP_ANALYSIS_BUDGET_MS} / ${HEAVY_ANALYSIS_CANDIDATE_LIMIT} vs ${DEEP_ANALYSIS_MS_PER_CANDIDATE})`,
);

// Today's device: the plan's 5K = 120 is unaffordable, so the budget is 64 and
// the shipped build is byte-for-byte what it was.
assert(
  candidateBudget(24) === HEAVY_ANALYSIS_CANDIDATE_LIMIT,
  `at the measured 2.33 s per candidate a 24-photo album must still get exactly ` +
    `${HEAVY_ANALYSIS_CANDIDATE_LIMIT} candidates (got ${candidateBudget(24)})`,
);

// The plan's own targets, priced. Nobody should propose 96–192 candidates
// without this number attached to it.
const secondsFor = (candidates: number) =>
  Math.round((candidates * DEEP_ANALYSIS_MS_PER_CANDIDATE) / 1_000);
assert(
  secondsFor(CANDIDATE_BUDGET_MIN) > 200 && secondsFor(CANDIDATE_BUDGET_MAX) > 400,
  `§14's 96–192 candidates cost ${secondsFor(CANDIDATE_BUDGET_MIN)}–${secondsFor(CANDIDATE_BUDGET_MAX)} s ` +
    "of deep analysis at today's price; if this ever reads as cheap, re-measure the stage",
);
console.log(
  `M5 budget price ${JSON.stringify({
    msPerCandidate: DEEP_ANALYSIS_MS_PER_CANDIDATE,
    today: candidateBudget(24),
    secondsAt96: secondsFor(96),
    secondsAt120: secondsFor(120),
    secondsAt192: secondsFor(192),
    // What a candidate must cost for the plan's budget to fit today's wall.
    msPerCandidateFor120: Math.floor(DEEP_ANALYSIS_BUDGET_MS / 120),
    msPerCandidateFor192: Math.floor(DEEP_ANALYSIS_BUDGET_MS / 192),
  })}`,
);

// VACUITY. Every assertion above is also true of a function that ignores its
// arguments and returns 64. The budget only means something if a cheaper
// candidate actually buys more of them.
assert(
  candidateBudget(24, 1_000) === 120 && candidateBudget(24, 500) === 120,
  `VACUITY: at 1.0 s per candidate a 24-photo album must get its full 5K = 120 ` +
    `(got ${candidateBudget(24, 1_000)}); if this is 64 the budget is a constant wearing a function`,
);
assert(
  candidateBudget(60, 100) === CANDIDATE_BUDGET_MAX,
  "a large album at a cheap price must reach the 192 ceiling and stop there",
);
assert(
  candidateBudget(4, 100) === CANDIDATE_BUDGET_MIN,
  "a tiny album still gets the 96 floor: 5K would starve the coverage it needs",
);
// ...and the floor holds in the other direction. A device that measures SLOWER
// than the baseline must not quietly ship a thinner album.
assert(
  candidateBudget(24, 10_000) === HEAVY_ANALYSIS_CANDIDATE_LIMIT,
  "a slow device must still get today's 64 candidates; latency is not fixed by a worse album",
);
// A NaN must not become a NaN budget, which `chooseHeavyAnalysisCandidates`
// would read as "select nothing" and return an empty album for.
assert(
  candidateBudget(Number.NaN) === HEAVY_ANALYSIS_CANDIDATE_LIMIT &&
    candidateBudget(24, Number.NaN) === HEAVY_ANALYSIS_CANDIDATE_LIMIT,
  "a non-finite album size or price must fall back to today's budget, not to NaN",
);

// --- 2. The corpora, as the prepass sees them -------------------------------

const MOMENT_WINDOW_MS = 6 * 60 * 60 * 1_000;

/**
 * The fixture's own notion of a moment — close in time and visually alike —
 * copied from `album-fixtures.test.ts` on purpose. The prepass groups by time
 * gap alone and never sees an embedding, so scoring it against a definition it
 * shares would be marking its own homework.
 */
function momentOf(candidates: readonly PlannerCandidate[]) {
  const parent = candidates.map((_, index) => index);
  const root = (index: number): number => {
    while (parent[index] !== index) {
      parent[index] = parent[parent[index]];
      index = parent[index];
    }
    return index;
  };
  for (let left = 0; left < candidates.length; left += 1) {
    for (let right = left + 1; right < candidates.length; right += 1) {
      const a = candidates[left];
      const b = candidates[right];
      if (Math.abs((a.capturedAt ?? 0) - (b.capturedAt ?? 0)) > MOMENT_WINDOW_MS) continue;
      if (cosine(a.embedding ?? [], b.embedding ?? []) < 0.8) continue;
      const rootA = root(left);
      const rootB = root(right);
      if (rootA !== rootB) parent[Math.max(rootA, rootB)] = Math.min(rootA, rootB);
    }
  }
  return new Map(candidates.map((candidate, index) => [candidate.mediaId, root(index)]));
}

function cosine(left: readonly number[], right: readonly number[]) {
  let dot = 0;
  let leftMagnitude = 0;
  let rightMagnitude = 0;
  for (let index = 0; index < Math.min(left.length, right.length); index += 1) {
    dot += left[index] * right[index];
    leftMagnitude += left[index] * left[index];
    rightMagnitude += right[index] * right[index];
  }
  const denominator = Math.sqrt(leftMagnitude) * Math.sqrt(rightMagnitude);
  return denominator === 0 ? 0 : dot / denominator;
}

/** The fixture's photographs as the cheap probe would have handed them over. */
function probedFrom(fixture: AlbumFixture): ProbedCandidate[] {
  return fixture.candidates.map((candidate) => ({
    photo: {
      id: candidate.mediaId,
      uri: `asset://${candidate.mediaId}`,
      filename: `${candidate.mediaId}.jpg`,
      source: "device-gallery" as const,
      creationTime: candidate.capturedAt,
      placeKey: candidate.placeKey,
      personIds: candidate.personIds ? [...candidate.personIds] : undefined,
      width: 4_000,
      height: 3_000,
    },
    quality: { sharpness: candidate.quality, exposure: 0.5, clippedFraction: 0 },
  }));
}

/** The bar the planner and the fixture pins both use for "too alike". */
const DUPLICATE_BAR = 0.92;

/**
 * The closest anything else in `pool` sits to `mediaId`.
 *
 * This is the check that keeps a reservation from becoming what `fd97b18`
 * fixed. A reserved pick is by construction the FIRST frame of its moment, so
 * it cannot be a second copy of something the same moment already contributed —
 * but "moment" is the prepass's own definition, and a mechanism must not be
 * scored against the definition it uses. So the property is asserted on the
 * photographs instead: whatever the reservation adds must be a genuinely
 * different picture from everything already in the pool, measured on the same
 * 0.92 cosine the selector refuses duplicates at.
 */
function nearestInPool(
  mediaId: string,
  pool: readonly string[],
  byMediaId: ReadonlyMap<string, PlannerCandidate>,
): number {
  let worst = 0;
  for (const other of pool) {
    if (other === mediaId) continue;
    const value = cosine(
      byMediaId.get(mediaId)!.embedding ?? [],
      byMediaId.get(other)!.embedding ?? [],
    );
    if (value > worst) worst = value;
  }
  return worst;
}

// --- 3. What the pool loses, and what the reservation puts back -------------

const BUDGETS = [24, 32, 40, 48];
let momentsRecovered = 0;
let budgetsWhereNothingWasLost = 0;
let framesAdded = 0;
let duplicatePairsInReservedPools = 0;

for (const fixture of albumFixtures()) {
  const moments = momentOf(fixture.candidates);
  const total = new Set(moments.values()).size;
  const probed = probedFrom(fixture);
  const byMediaId = new Map(fixture.candidates.map((candidate) => [candidate.mediaId, candidate]));

  for (const budget of BUDGETS) {
    const unreserved = chooseHeavyAnalysisCandidates(probed, budget, { reserveMoments: false });
    const reserved = chooseHeavyAnalysisCandidates(probed, budget);
    assert(
      unreserved.length === budget && reserved.length === budget,
      `${fixture.name}@${budget}: both pools must be exactly the budget`,
    );

    const beforeIds = unreserved.map(({ photo }) => photo.id);
    const afterIds = reserved.map(({ photo }) => photo.id);
    const before = new Set(beforeIds.map((id) => moments.get(id)));
    const after = new Set(afterIds.map((id) => moments.get(id)));

    assert(
      after.size >= before.size,
      `${fixture.name}@${budget}: reservations may never reduce moment coverage ` +
        `(${before.size} -> ${after.size})`,
    );
    // The promise, in the terms the policy states it: at least
    // `min(moments, floor(budget/2))` distinct moments. Where the event's
    // moments all fit inside that, none of them may be missing.
    if (total <= Math.floor(budget * MOMENT_RESERVE_FRACTION)) {
      assert(
        after.size === total,
        `${fixture.name}@${budget}: ${total} moments fit inside the promise, so all of them ` +
          `must reach deep analysis (got ${after.size})`,
      );
    }

    // Nothing the reservation added may be a near-duplicate of the pool it
    // joined. This is the assertion the near-duplicate warning is about.
    const added = afterIds.filter((id) => !beforeIds.includes(id));
    framesAdded += added.length;
    for (const mediaId of added) {
      const nearest = nearestInPool(mediaId, afterIds, byMediaId);
      assert(
        nearest < DUPLICATE_BAR,
        `${fixture.name}@${budget}: the reservation admitted ${mediaId} at cosine ` +
          `${nearest.toFixed(3)} to something already in the pool — a reservation must not be ` +
          "the route by which a near-identical frame reaches deep analysis",
      );
    }
    for (const mediaId of afterIds) {
      if (nearestInPool(mediaId, afterIds, byMediaId) >= DUPLICATE_BAR) {
        duplicatePairsInReservedPools += 1;
      }
    }

    if (before.size < total) momentsRecovered += total - before.size;
    else budgetsWhereNothingWasLost += 1;
    console.log(
      `M5 pool coverage ${fixture.name}@${budget} ${JSON.stringify({
        moments: total,
        withoutReservations: before.size,
        withReservations: after.size,
        added,
      })}`,
    );
  }
}

// VACUITY, three ways. The coverage assertions are also true of a corpus the
// unreserved gate never dropped anything from, and of a `reserveMoments: false`
// that quietly still reserves. The duplicate assertion is also true of a pool
// the reservation never touched, and of a corpus with no duplicates in it.
assert(
  momentsRecovered > 0,
  "VACUITY: the unreserved pool never dropped a single moment across any fixture or budget, " +
    "so the coverage assertions above are measuring nothing. Either the corpus no longer " +
    "contains a starvable moment or the off switch is not off.",
);
assert(
  framesAdded > 0,
  "VACUITY: reservations changed no pool at all, so the near-duplicate assertion never ran",
);
assert(
  duplicatePairsInReservedPools > 0,
  `VACUITY: not one frame in any reserved pool sits within ${DUPLICATE_BAR} of another, so the ` +
    "bar above could not have caught a reservation that did admit one. This corpus is supposed " +
    "to contain reframes the take-grouper could not collapse.",
);
console.log(
  `M5 moments recovered ${JSON.stringify({
    momentsRecovered,
    budgetsWhereNothingWasLost,
    framesAdded,
    poolFramesWithinTheBar: duplicatePairsInReservedPools,
  })}`,
);

// --- 4. People, not just moments --------------------------------------------
//
// The measured warning this policy had to clear: a coverage scheme that buys
// breadth by spending the infant's close-ups is a regression whatever its
// coverage numbers say. So the person-level effect is asserted, not reported.

const birthday = albumFixtures().find((fixture) => fixture.name === "birthday")!;
const birthdayProbed = probedFrom(birthday);
const byId = new Map(birthday.candidates.map((candidate) => [candidate.mediaId, candidate]));

const granFrames = birthday.candidates.filter((candidate) =>
  (candidate.personIds ?? []).includes("gran"),
);
assert(
  granFrames.length === 1,
  `the birthday fixture must still hold exactly one frame of the scarce relative (got ${granFrames.length})`,
);

function albumFrom(pool: readonly ProbedCandidate[]) {
  const ids = new Set(pool.map(({ photo }) => photo.id));
  return planAlbum(
    birthday.candidates.filter((candidate) => ids.has(candidate.mediaId)),
    birthday.target,
  ).selectedIds;
}

/** Frames of the infant alone or with one other person — the intimate ones. */
function intimate(ids: readonly string[]) {
  return ids.filter((id) => {
    const people = byId.get(id)!.personIds ?? [];
    return people.includes("avu") && people.length <= 2;
  }).length;
}

const RESERVE_BUDGET = 32;
const withoutReservations = albumFrom(
  chooseHeavyAnalysisCandidates(birthdayProbed, RESERVE_BUDGET, { reserveMoments: false }),
);
const withReservations = albumFrom(
  chooseHeavyAnalysisCandidates(birthdayProbed, RESERVE_BUDGET),
);
console.log(
  `M5 person effect birthday@${RESERVE_BUDGET} ${JSON.stringify({
    intimateWithout: intimate(withoutReservations),
    intimateWith: intimate(withReservations),
    granWithout: withoutReservations.includes(granFrames[0].mediaId),
    granWith: withReservations.includes(granFrames[0].mediaId),
    entered: withReservations.filter((id) => !withoutReservations.includes(id)),
    left: withoutReservations.filter((id) => !withReservations.includes(id)),
  })}`,
);

assert(
  intimate(withReservations) >= intimate(withoutReservations),
  `reservations must not cost the infant her close-ups ` +
    `(${intimate(withoutReservations)} -> ${intimate(withReservations)})`,
);
assert(
  withReservations.includes(granFrames[0].mediaId),
  "the one frame of the scarce relative must survive the budget once her moment is reserved",
);

// VACUITY, twice over. The comparison proves nothing if the two albums are the
// same album, and the gran assertion proves nothing if she was never at risk.
assert(
  JSON.stringify(withReservations) !== JSON.stringify(withoutReservations),
  "VACUITY: the two pools produced identical albums, so the person-level comparison above " +
    "is comparing an album with itself",
);
assert(
  !withoutReservations.includes(granFrames[0].mediaId),
  "VACUITY: the scarce relative survives the unreserved budget too, so her presence in the " +
    "reserved album is not evidence of anything the reservation did",
);
assert(
  intimate(withReservations) > 0,
  "VACUITY: an album with no intimate frames at all makes the close-up assertion unfalsifiable",
);

console.log("candidate budget/reserve self-check passed");
