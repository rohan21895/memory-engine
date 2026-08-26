import {
  chooseHeavyAnalysisCandidates,
  HEAVY_ANALYSIS_CANDIDATE_LIMIT,
  type ProbedCandidate,
  // @ts-expect-error Node's TypeScript runner requires the source extension.
} from "./candidate-prepass.ts";

function assert(value: unknown, message: string): void {
  if (!value) throw new Error(`candidate-prepass people self-check failed: ${message}`);
}

/**
 * The 64-photo gate used to be built blind to people.
 *
 * Everything downstream -- the planner's per-person floor, the rare-moment
 * rescue, the final ranker -- can only work over photos that survived this cap,
 * so a person dropped here is dropped from the album no matter what any later
 * stage intends. Measured on the production path, eight photos each carrying a
 * different face were all discarded before the planner ran, and every one of
 * the 24 album slots went to one person's burst.
 *
 * The fixture is the same shape: one very sharp burst of a single person, and a
 * handful of duller frames that are the only photographs of anybody else.
 */

const BURST = 90;
const STORY_PEOPLE = 8;

function quality(sharpness: number) {
  return {
    sharpness,
    exposure: 0.5,
    clippedFraction: 0,
    // One shared blurhash: within a single session the content axis goes flat,
    // which is exactly the condition under which the cap falls back to raw
    // sharpness and the burst wins everything.
    blurhash: undefined,
  } as unknown as ProbedCandidate["quality"];
}

const photos: ProbedCandidate[] = [];
// The burst: same person, same moment, uniformly sharper than anything else.
for (let i = 0; i < BURST; i += 1) {
  photos.push({
    photo: {
      id: `burst-${String(i).padStart(3, "0")}`,
      uri: `file:///burst-${i}.jpg`,
      filename: `burst-${i}.jpg`,
      source: "device-gallery" as const,
      width: 4000,
      height: 3000,
      personIds: ["person-main"],
    },
    quality: quality(0.97),
  });
}
// The story beats: each the ONLY photo of someone, and each duller than every
// burst frame, so nothing but a person-aware rule can save them.
for (let i = 0; i < STORY_PEOPLE; i += 1) {
  photos.push({
    photo: {
      id: `story-${i}`,
      uri: `file:///story-${i}.jpg`,
      filename: `story-${i}.jpg`,
      source: "device-gallery" as const,
      width: 4000,
      height: 3000,
      personIds: [`person-${i}`],
    },
    quality: quality(0.74),
  });
}

const storyIds = new Set(Array.from({ length: STORY_PEOPLE }, (_v, i) => `story-${i}`));
const survived = (picked: ProbedCandidate[]) =>
  picked.filter(({ photo }) => storyIds.has(photo.id)).length;

// Everyone in this fixture recurs. The stranger case is covered separately.
const everyoneRecurs = () => true;

// --- 1. The regression this exists to prevent -------------------------------
// Without the person axis the gate is blind, and the sharper burst takes every
// slot. This asserts the OLD behaviour so the fixture is proven to be a real
// trap rather than one the ranker would have survived anyway.
{
  const blind = chooseHeavyAnalysisCandidates(photos, HEAVY_ANALYSIS_CANDIDATE_LIMIT);
  assert(
    blind.length === HEAVY_ANALYSIS_CANDIDATE_LIMIT,
    `the cap must actually bind (got ${blind.length})`,
  );
  assert(
    survived(blind) === 0,
    `fixture is not a trap: ${survived(blind)} story photos survived a people-blind ` +
      `gate, so this test could pass without the fix`,
  );
}

// --- 2. The fix -------------------------------------------------------------
{
  const aware = chooseHeavyAnalysisCandidates(
    photos,
    HEAVY_ANALYSIS_CANDIDATE_LIMIT,
    { isFamiliar: everyoneRecurs },
  );
  assert(
    aware.length === HEAVY_ANALYSIS_CANDIDATE_LIMIT,
    `the safety cap is still hard (got ${aware.length})`,
  );
  assert(
    survived(aware) === STORY_PEOPLE,
    `every person must reach the planner: ${survived(aware)}/${STORY_PEOPLE} survived`,
  );
  // The burst is not banished, only bounded -- it still fills what is left.
  assert(
    aware.length - survived(aware) === HEAVY_ANALYSIS_CANDIDATE_LIMIT - STORY_PEOPLE,
    "the remaining capacity still goes to the best available frames",
  );
}

// --- 3. Strangers must NOT be protected -------------------------------------
// This is the whole reason the axis keys on recurrence rather than on rarity.
// person-0..7 here are one-off passers-by; only person-main recurs. Protecting
// the rare ones would fill the album with people the owner has never met.
{
  const onlyMainRecurs = (personId: string) => personId === "person-main";
  const aware = chooseHeavyAnalysisCandidates(
    photos,
    HEAVY_ANALYSIS_CANDIDATE_LIMIT,
    { isFamiliar: onlyMainRecurs },
  );
  assert(
    survived(aware) === 0,
    `a one-off passer-by must not displace a family member: ${survived(aware)} ` +
      `stranger photos took protected slots`,
  );
}

// --- 4. Inert without the predicate -----------------------------------------
// Callers that pass nothing must get byte-identical behaviour to before.
{
  const before = chooseHeavyAnalysisCandidates(photos, HEAVY_ANALYSIS_CANDIDATE_LIMIT);
  const withEmptyOptions = chooseHeavyAnalysisCandidates(
    photos,
    HEAVY_ANALYSIS_CANDIDATE_LIMIT,
    {},
  );
  assert(
    before.map(({ photo }) => photo.id).join() ===
      withEmptyOptions.map(({ photo }) => photo.id).join(),
    "an empty options object must not change a single pick",
  );
}

// --- 5. A photo is worth its LEAST-covered person, not its most --------------
//
// `solo-a` is sharper, so person-a is already covered by the time `group` is
// considered. `group` still carries person-b and person-c, who have no other
// photograph at all, and must be taken on their account. Scoring the frame by
// its most-covered person instead would read it as a redundant picture of
// person-a and drop the only evidence those two were ever there.
//
// An earlier draft left all three uncovered, where least and most are both
// zero -- so it passed whichever rule was in force and proved nothing.
{
  const together: ProbedCandidate[] = [
    ...photos.slice(0, BURST),
    {
      photo: {
        id: "solo-a",
        uri: "file:///solo-a.jpg",
        filename: "solo-a.jpg",
        source: "device-gallery" as const,
        width: 4000,
        height: 3000,
        personIds: ["person-a"],
      },
      quality: quality(0.8),
    },
    {
      photo: {
        id: "group",
        uri: "file:///group.jpg",
        filename: "group.jpg",
        source: "device-gallery" as const,
        width: 4000,
        height: 3000,
        personIds: ["person-a", "person-b", "person-c"],
      },
      quality: quality(0.7),
    },
  ];
  const aware = chooseHeavyAnalysisCandidates(together, 4, {
    isFamiliar: everyoneRecurs,
  });
  const ids = aware.map(({ photo }) => photo.id);
  assert(
    ids.includes("solo-a"),
    `the sharper single portrait is taken first (got ${ids.join(",")})`,
  );
  assert(
    ids.includes("group"),
    `the dullest frame in the set must still be taken, because it is the only ` +
      `photograph of person-b and person-c (got ${ids.join(",")})`,
  );
}

// --- 6. Pins still outrank everything ---------------------------------------
{
  const pinned: ProbedCandidate[] = photos.map((candidate, index) =>
    index === 0
      ? { ...candidate, photo: { ...candidate.photo, pinned: true } }
      : candidate,
  );
  const aware = chooseHeavyAnalysisCandidates(
    pinned,
    HEAVY_ANALYSIS_CANDIDATE_LIMIT,
    { isFamiliar: everyoneRecurs },
  );
  assert(
    aware.some(({ photo }) => photo.id === "burst-000"),
    "a pinned photo survives the people-aware gate",
  );
  assert(
    survived(aware) === STORY_PEOPLE,
    "pinning one frame must not cost anybody their only photograph",
  );
}

// --- 7. Determinism ---------------------------------------------------------
{
  const once = chooseHeavyAnalysisCandidates(photos, HEAVY_ANALYSIS_CANDIDATE_LIMIT, {
    isFamiliar: everyoneRecurs,
  });
  const twice = chooseHeavyAnalysisCandidates(photos, HEAVY_ANALYSIS_CANDIDATE_LIMIT, {
    isFamiliar: everyoneRecurs,
  });
  assert(
    once.map(({ photo }) => photo.id).join() === twice.map(({ photo }) => photo.id).join(),
    "the same input must give the same shortlist",
  );
}

console.log("candidate-prepass people self-check passed");
