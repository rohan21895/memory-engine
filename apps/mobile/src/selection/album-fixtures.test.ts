/**
 * The pinned albums (EXPERT-PLAN M0: "≥3 event fixtures with expected
 * selected-photo IDs"). This is the file that makes a selection change show up
 * as a diff of photograph names instead of as an opinion.
 *
 * Both selectors are pinned, not just the shipped one. A rollback switch whose
 * other position is unmeasured is not a rollback switch, it is a coin.
 *
 * Reading order:
 *   1. the corpus digest — did the photographs themselves change?
 *   2. the fixture SHAPE — are these still the kind of photographs the owner
 *      actually has (group shots, reframes above the duplicate bar, a scarce
 *      person, an isolated moment)? Measured and printed, never assumed.
 *   3. the pinned albums, exactly.
 *   4. vacuity: the pin is shown to be capable of failing.
 */

// @ts-expect-error The Expo app deliberately does not ship Node declarations.
import { readFileSync } from "node:fs";
// @ts-expect-error Node requires the extension; Metro resolves this path too.
import { albumFixtures, fixtureDigest } from "./album-fixtures.ts";
// @ts-expect-error Node requires the extension; Metro resolves this path too.
import { cosine, planAlbum } from "./album-planner.ts";
import type { AlbumFixture } from "./album-fixtures";
import type { PlannerCandidate, PlannerPolicy } from "./album-planner";

function assert(value: unknown, message: string): asserts value {
  if (!value) throw new Error(`Album fixture self-check failed: ${message}`);
}

type PinnedAlbum = {
  name: string;
  target: number;
  corpusDigest: string;
  selectors: Record<
    string,
    {
      selectedIds: string[];
      rescuedIds: string[];
      rejectedCount: number;
      missingPersonIds: string[];
    }
  >;
};

const pinned: { albums: PinnedAlbum[] } = JSON.parse(
  readFileSync(new URL("../../fixtures/album-plans/expected.json", import.meta.url), "utf8"),
);
const SELECTORS: PlannerPolicy["selector"][] = ["coverage-keys", "submodular"];
const DUPLICATE_BAR = 0.92;
const fixtures = albumFixtures();

/** An independent notion of "moment": close in time and visually alike. */
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
      if (Math.abs((a.capturedAt ?? 0) - (b.capturedAt ?? 0)) > 6 * 60 * 60 * 1_000) continue;
      if (cosine(a.embedding ?? [], b.embedding ?? []) < 0.8) continue;
      const rootA = root(left);
      const rootB = root(right);
      if (rootA !== rootB) parent[Math.max(rootA, rootB)] = Math.min(rootA, rootB);
    }
  }
  return new Map(candidates.map((candidate, index) => [candidate.mediaId, root(index)]));
}

function worstPair(fixture: AlbumFixture, ids: readonly string[]) {
  const byId = new Map(fixture.candidates.map((candidate) => [candidate.mediaId, candidate]));
  let worst = 0;
  let pairs = 0;
  for (let left = 0; left < ids.length; left += 1) {
    for (let right = left + 1; right < ids.length; right += 1) {
      const value = cosine(
        byId.get(ids[left])!.embedding ?? [],
        byId.get(ids[right])!.embedding ?? [],
      );
      if (value > worst) worst = value;
      if (value >= DUPLICATE_BAR) pairs += 1;
    }
  }
  return { worst: Math.round(worst * 1_000) / 1_000, pairs };
}

// --- 1 & 2. The corpus is what it claims to be -----------------------------

assert(fixtures.length >= 3, "M0 asks for at least three event fixtures");
assert(pinned.albums.length === fixtures.length, "every fixture must be pinned");
const scarcest: number[] = [];

for (const fixture of fixtures) {
  const pin = pinned.albums.find((album) => album.name === fixture.name);
  assert(pin, `fixture ${fixture.name} has no pinned album`);
  assert(
    fixtureDigest(fixture) === pin.corpusDigest,
    `${fixture.name}: the PHOTOGRAPHS changed (digest ${fixtureDigest(fixture)} vs pinned ${pin.corpusDigest}). ` +
      "Rerun scripts/pin-album-fixtures.ts on purpose, and read the album diff.",
  );

  const candidates = fixture.candidates;
  const qualities = candidates.map((candidate) => candidate.quality);
  const withPeople = candidates.filter((candidate) => (candidate.personIds ?? []).length > 0);
  const perPerson = new Map<string, number>();
  for (const candidate of candidates) {
    for (const personId of candidate.personIds ?? []) {
      perPerson.set(personId, (perPerson.get(personId) ?? 0) + 1);
    }
  }
  const familyPairs: number[] = [];
  const allPairs: number[] = [];
  for (let left = 0; left < candidates.length; left += 1) {
    for (let right = left + 1; right < candidates.length; right += 1) {
      const value = cosine(candidates[left].embedding ?? [], candidates[right].embedding ?? []);
      allPairs.push(value);
      if (candidates[left].poseFamily === candidates[right].poseFamily) familyPairs.push(value);
    }
  }
  allPairs.sort((a, b) => a - b);
  const shape = {
    candidates: candidates.length,
    exactQualityTies: qualities.length - new Set(qualities).size,
    twoOrMorePeople: candidates.filter((candidate) => (candidate.personIds ?? []).length >= 2).length,
    scarcestPerson: Math.min(...perPerson.values()),
    medianPairCosine: Math.round(allPairs[Math.floor(allPairs.length / 2)] * 1_000) / 1_000,
    reframePairs: familyPairs.length,
    minReframeCosine: Math.round(Math.min(...familyPairs) * 1_000) / 1_000,
    pairsOverDuplicateBar: allPairs.filter((value) => value >= DUPLICATE_BAR).length,
    moments: new Set(momentOf(candidates).values()).size,
  };
  console.log(`M6 fixture shape ${fixture.name} ${JSON.stringify(shape)}`);

  assert(shape.candidates === 64, `${fixture.name}: production shape is 64 candidates`);
  assert(fixture.target === 24, `${fixture.name}: production shape is a 24-photo album`);
  // A coarse quality grid manufactures exact ties, and every gain comparison in
  // the planner falls through to a media-id sort when two values are equal —
  // so a tied corpus measures the rounding, not the selector.
  assert(shape.exactQualityTies === 0, `${fixture.name}: exact quality ties poison every tiebreak`);
  assert(
    shape.twoOrMorePeople > candidates.length / 2,
    `${fixture.name}: this library is mostly group shots, the fixture must be too`,
  );
  assert(
    shape.reframePairs > 0 && shape.minReframeCosine > DUPLICATE_BAR,
    `${fixture.name}: reframes must actually sit above the ${DUPLICATE_BAR} duplicate bar`,
  );
  assert(
    shape.medianPairCosine > 0.3 && shape.medianPairCosine < 0.7,
    `${fixture.name}: unrelated frames must look like CLIP embeddings (~0.5), not like random vectors`,
  );
  assert(shape.moments >= 5, `${fixture.name}: an event needs several distinct moments`);
  assert(withPeople.length > 0, `${fixture.name}: an event needs people in it`);
  scarcest.push(shape.scarcestPerson);
}

// The scarce-person rescue only has something to rescue if SOME fixture holds a
// person with a single frame. Requiring it of every fixture would be false to
// the library: two years of an infant has no one-frame relative in it.
assert(
  Math.min(...scarcest) === 1,
  `at least one fixture must hold a person with exactly one frame (scarcest per fixture: ${scarcest.join(", ")})`,
);

// --- 3. The pinned albums, photograph by photograph -------------------------

for (const fixture of fixtures) {
  const pin = pinned.albums.find((album) => album.name === fixture.name)!;
  for (const selector of SELECTORS) {
    const expected = pin.selectors[selector];
    assert(expected, `${fixture.name}: no pin for selector ${selector}`);
    const plan = planAlbum(fixture.candidates, fixture.target, { policy: { selector } });
    const missing = expected.selectedIds.filter((id) => !plan.selectedIds.includes(id));
    const extra = plan.selectedIds.filter((id) => !expected.selectedIds.includes(id));
    assert(
      JSON.stringify(plan.selectedIds) === JSON.stringify(expected.selectedIds),
      `${fixture.name}/${selector}: album changed. left=[${missing.join(", ")}] entered=[${extra.join(", ")}]`,
    );
    assert(
      plan.selectedIds.length === fixture.target,
      `${fixture.name}/${selector}: asked for ${fixture.target}, got ${plan.selectedIds.length}`,
    );
    assert(
      JSON.stringify(plan.rescuedIds) === JSON.stringify(expected.rescuedIds) &&
        plan.rejected.length === expected.rejectedCount &&
        JSON.stringify(plan.missingPersonIds) === JSON.stringify(expected.missingPersonIds),
      `${fixture.name}/${selector}: rescues/rejections/missing people changed`,
    );

    // Diversity may never erase a moment outright. A selector that answered
    // "fewer photographs, all different" would be optimizing the wrong thing.
    const groups = momentOf(fixture.candidates);
    const covered = new Set(plan.selectedIds.map((id) => groups.get(id)));
    assert(
      covered.size === new Set(groups.values()).size,
      `${fixture.name}/${selector}: dropped a moment entirely (${covered.size} of ${new Set(groups.values()).size})`,
    );

    const duplicates = worstPair(fixture, plan.selectedIds);
    console.log(
      `M6 pinned album ${fixture.name}/${selector} ${JSON.stringify({
        photos: plan.selectedIds.length,
        momentsCovered: covered.size,
        nearDuplicatePairs: duplicates.pairs,
        worstPairCosine: duplicates.worst,
        rescued: plan.rescuedIds.length,
      })}`,
    );
  }
}

// The near-duplicate claim, stated where it can be checked.
//
// This assertion used to read the other way round: submodular admits none, the
// shipped selector admits some, and the gap between them was the finding. The
// gap is gone because the shipped selector was fixed — it used to treat the bar
// as a preference a starved pool could overrule, so on this fixture a ten-frame
// sunset burst meeting a binding pose cap put eight of its frames into a
// twenty-four photo album. Both selectors now hold the line.
//
// That makes the old guard genuinely vacuous, and it said so rather than
// passing quietly. The anchor is now the bar itself: lift it and the duplicates
// come straight back. A fixture that simply had no near-duplicates to admit
// would fail that, which is the failure the guard exists to catch.
const tripPin = pinned.albums.find((album) => album.name === "trip")!;
const tripFixture = fixtures.find((fixture) => fixture.name === "trip")!;
const tripKeys = worstPair(tripFixture, tripPin.selectors["coverage-keys"].selectedIds);
const tripSubmodular = worstPair(tripFixture, tripPin.selectors.submodular.selectedIds);
console.log(`M6 duplicate discipline ${JSON.stringify({ keys: tripKeys, submodular: tripSubmodular })}`);
assert(
  tripSubmodular.pairs === 0,
  `submodular must admit no pair at or above ${DUPLICATE_BAR} (${tripSubmodular.pairs})`,
);
assert(
  tripKeys.pairs === 0,
  `the shipped selector must admit no pair at or above ${DUPLICATE_BAR} either (${tripKeys.pairs})`,
);
const unbarred = worstPair(
  tripFixture,
  planAlbum(tripFixture.candidates, tripFixture.target, {
    policy: { maxSelectedSimilarity: 1 },
  }).selectedIds,
);
console.log(`M6 duplicate discipline unbarred ${JSON.stringify(unbarred)}`);
assert(
  unbarred.pairs > 0,
  "VACUITY: with the duplicate bar lifted the shipped selector must admit near-duplicates; " +
    "if it does not, this fixture has none to admit and the two zeros above prove nothing",
);

// --- 4. Vacuity: the pin can fail -------------------------------------------

const sabotageTarget = fixtures[0];
const sabotagePin = pinned.albums[0].selectors["coverage-keys"].selectedIds;

// Hand every REJECTED photograph a perfect score. If the album still came back
// identical, the assertions in section 3 would be pinning something other than
// a decision.
const promoted = sabotageTarget.candidates.map((candidate) =>
  sabotagePin.includes(candidate.mediaId) ? candidate : { ...candidate, quality: 1 },
);
assert(
  JSON.stringify(planAlbum(promoted, sabotageTarget.target).selectedIds) !==
    JSON.stringify(sabotagePin),
  "VACUITY: promoting every unselected photograph must change the album",
);

// And the same pin has to be sensitive to the TinyCLIP aesthetic axis, which is
// the baseline any new objective has to beat. If turning that weight off left
// the album untouched, the axis would not be participating in selection at all
// and "we beat zero-shot CLIP" would be an untestable claim.
assert(
  JSON.stringify(
    planAlbum(sabotageTarget.candidates, sabotageTarget.target, {
      policy: { weightAesthetic: 0 },
    }).selectedIds,
  ) !== JSON.stringify(sabotagePin),
  "VACUITY: the pinned album must depend on the TinyCLIP aesthetic weight",
);

// ...and the digest has to notice a changed photograph, or step 1 is theatre.
assert(
  fixtureDigest({ ...sabotageTarget, candidates: promoted }) !== pinned.albums[0].corpusDigest,
  "VACUITY: the corpus digest must change when a photograph changes",
);

// The two pins must not be the same list. Pinning one selector twice would pass
// every assertion in section 3 while measuring nothing about M6.
assert(
  pinned.albums.some(
    (album) =>
      JSON.stringify(album.selectors["coverage-keys"].selectedIds) !==
      JSON.stringify(album.selectors.submodular.selectedIds),
  ),
  "VACUITY: at least one fixture must distinguish the two selectors",
);

console.log("album-fixtures self-check passed");
