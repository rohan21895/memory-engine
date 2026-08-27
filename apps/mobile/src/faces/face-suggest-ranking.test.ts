// @ts-expect-error Node's TypeScript runner requires the source extension.
import { RARE_MERGE_CO_OCCURRENCE_RATE, suggestMerges } from "./face-cluster.ts";
import type { Person } from "./types.ts";

function assert(value: unknown, message: string): asserts value {
  if (!value) throw new Error(`suggest-ranking self-check failed: ${message}`);
}

/**
 * What the "Fix" button asks first.
 *
 * The owner reported the same person occupying several tiles -- "avika has many
 * thumbnails, rohan has many, aastha has multiple". Merging them automatically
 * is not available: measured on his own library, every bar low enough to join
 * those tiles also lets more known-different-people pairs through than it
 * gains real merges. So the answer has to come from him, and the only thing
 * this code controls is which questions are worth his taps.
 *
 * It was spending them badly. The useful first population is the one that
 * rarely appears together (the signature of one face found twice), and within
 * that population every question costs the same tap while the size of the
 * repair varies enormously.
 */

const at = (degrees: number): number[] => {
  const radians = (degrees * Math.PI) / 180;
  return [Math.cos(radians), Math.sin(radians)];
};

const person = (
  id: string,
  faceCount: number,
  degrees: number,
  assetIds: string[],
): Person => ({
  id,
  faceCount,
  assetIds,
  centroid: at(degrees),
  embeddingKind: "identity",
});

// Two pairs, both over their merge bar and held apart ONLY by a single shared
// photo -- the state the ranking is about. The angles put them between the bar
// (0.6) and the same-photo escape (0.72), which is where the owner's real pairs
// sit: person-16 ~ person-745 at 0.628, person-247 ~ person-960 at 0.699.
//
// One would repair a 150-photo tile; the other, two-photo tiles. The small pair
// is deliberately the MORE similar of the two, which is exactly the shape that
// used to sink the big one.
//
// The small pair carries a photo of its own on each side ON PURPOSE. One face
// each whose ONLY photo is the shared one is now withheld from the review
// entirely -- measured to be worth nothing, see the block at the end of this
// file -- so a fixture built that way would test the withholding rule instead
// of the ranking it is here for.
const big = [
  person("big-a", 257, 0, ["shared-1", "b1", "b2"]),
  person("big-b", 150, 51, ["shared-1", "b3", "b4"]),
];
const tiny = [
  person("tiny-a", 2, 0, ["shared-2", "t1"]),
  person("tiny-b", 2, 45.5, ["shared-2", "t2"]),
];
const options = { threshold: 0.5, identityMergeThreshold: 0.6 };

{
  const out = suggestMerges([...tiny, ...big], { ...options, limit: 10 });
  const rank = (id: string) => out.findIndex((s: any) => s.a === id || s.b === id);
  assert(out.length >= 2, `both pairs must be offered, got ${out.length}`);
  // Both must be in the vetoed group, or this is testing a different code path
  // than the one the owner's library exercises.
  assert(
    out.every((s: any) => s.blockedByCoOccurrence),
    "both pairs must be over their bar and vetoed by the shared photo",
  );
  assert(
    rank("big-a") < rank("tiny-a"),
    `the 150-photo repair must be asked before the one-photo one, ` +
      `got ranks big=${rank("big-a")} tiny=${rank("tiny-a")}`,
  );
  // Vacuity guard: the tiny pair really is the more similar of the two, so
  // similarity alone would have put it first and this is not a coincidence of
  // the fixture.
  const simOf = (id: string): number => {
    const hit = out.find((s: any) => s.a === id || s.b === id);
    assert(hit, `${id} must be in the offered list`);
    return hit.similarity;
  };
  assert(
    simOf("tiny-a") > simOf("big-a"),
    `the small pair must score higher, or the ordering proves nothing ` +
      `(tiny ${simOf("tiny-a").toFixed(3)} vs big ${simOf("big-a").toFixed(3)})`,
  );
}

/**
 * Rare co-occurrence outranks a larger but usually-two-people pattern.
 *
 * Modelled on the measured pair person-27 (463 faces) x person-729 (24 faces):
 * one shared photo out of 24, or 4.2%. A larger pair that shares a quarter of
 * its photos is more likely to earn "not the same" than to repair anything, so
 * raw size alone must not put it first.
 */
{
  const sharedRare = ["rare-shared"];
  const sharedOften = Array.from({ length: 20 }, (_unused, i) => `often-shared-${i}`);
  const rareRepair = [
    person(
      "person-27",
      463,
      0,
      [...sharedRare, ...Array.from({ length: 462 }, (_unused, i) => `r-big-${i}`)],
    ),
    person(
      "person-729",
      24,
      51,
      [...sharedRare, ...Array.from({ length: 23 }, (_unused, i) => `r-small-${i}`)],
    ),
  ];
  const oftenTogether = [
    person(
      "often-a",
      300,
      150,
      [...sharedOften, ...Array.from({ length: 280 }, (_unused, i) => `o-big-${i}`)],
    ),
    person(
      "often-b",
      80,
      201,
      [...sharedOften, ...Array.from({ length: 60 }, (_unused, i) => `o-small-${i}`)],
    ),
  ];
  const out = suggestMerges([...oftenTogether, ...rareRepair], {
    ...options,
    limit: 10,
  });
  assert(out.length === 2, `only the two intended pairs should clear the floor, got ${out.length}`);
  const rare = out.find((candidate: any) => candidate.a === "person-27");
  const often = out.find((candidate: any) => candidate.a === "often-a");
  assert(rare !== undefined && often !== undefined, "both measured patterns must be offered");
  assert(
    out[0] === rare,
    `the 4.2% pair must rank first, got ${out[0]?.a}+${out[0]?.b}`,
  );
  // Sabotage guard: raw repair size really points the opposite way. If the
  // rare-rate tier is removed, the 80-photo pair wins and the assertion above
  // fails rather than passing by fixture coincidence.
  assert(
    often.photosFixed > rare.photosFixed,
    `the competing pair must be the larger raw repair (${often.photosFixed} vs ${rare.photosFixed})`,
  );
  assert(
    rare.sharedAssets / rare.appearances <= RARE_MERGE_CO_OCCURRENCE_RATE &&
      often.sharedAssets / often.appearances > RARE_MERGE_CO_OCCURRENCE_RATE,
    "the sabotage must actually place the pairs on opposite sides of the rare-rate tier",
  );
}

// The payoff is the SMALLER cluster: that is the one absorbed, so that is how
// many photos change hands.
{
  const [suggestion] = suggestMerges(big, { ...options, limit: 1 });
  assert(
    suggestion.photosFixed === 150,
    `a 257-and-150 merge repairs 150 photos, got ${suggestion.photosFixed}`,
  );
}

/**
 * Below the bar, confidence still leads.
 *
 * These pairs have NOT passed the similarity test, and a wrong merge cannot be
 * undone by another tap -- it rewrites who is who. So a big tile is a reason
 * for care, not for haste, and the ordering must not promote a doubtful pair
 * just because it is large.
 */
{
  // Placed on opposite sides of the circle so no cross pair between the two
  // fixtures clears the floor -- otherwise `huge-b ~ sure-a` lands in the list
  // and the rank lookup below matches it instead of the pair under test.
  const hugeDoubtful = [
    person("huge-a", 900, 0, ["h1"]),
    person("huge-b", 800, 40, ["h2"]),
  ];
  const smallConfident = [
    person("sure-a", 4, 150, ["s1"]),
    person("sure-b", 3, 175, ["s2"]),
  ];
  const out = suggestMerges([...hugeDoubtful, ...smallConfident], {
    threshold: 0.5,
    identityMergeThreshold: 0.99,
    limit: 10,
  });
  const rank = (id: string) => out.findIndex((s: any) => s.a === id || s.b === id);
  assert(rank("sure-a") >= 0 && rank("huge-a") >= 0, "both pairs offered");
  assert(out.length === 2, `only the two intended pairs, got ${out.length}`);
  assert(
    out.every((s: any) => !s.blockedByCoOccurrence),
    "neither pair shares a photo, so both must sit in the below-the-bar group",
  );
  assert(
    rank("sure-a") < rank("huge-a"),
    `below the bar the closer pair must come first however large the other is, ` +
      `got ranks sure=${rank("sure-a")} huge=${rank("huge-a")}`,
  );
}

// A pair the app would merge on its own is not a question. Asking it back would
// spend a tap on an answer already given.
{
  const easy = [
    person("easy-a", 10, 0, ["e1"]),
    person("easy-b", 8, 1, ["e2"]),
  ];
  const out = suggestMerges(easy, { threshold: 0.5, identityMergeThreshold: 0.5, limit: 5 });
  assert(
    out.length === 0,
    `a pair over its bar and free to merge must not be offered, got ${out.length}`,
  );
}

/**
 * Two faces that exist nowhere but one shared photo are never asked about.
 *
 * The owner hit this and pushed back on being asked at all — "this cannot be
 * automated?" — so it was measured on his live index rather than argued. All 48
 * such pairs come from NINE images: two screenshots of this app's own photo
 * grid, one ChatGPT download, and six WhatsApp pictures that are photographs OF
 * PRINTED PHOTO ALBUMS, where the same relatives appear in each printed photo
 * inside the frame. One grid screenshot holds 20 detected "faces" and produced
 * 39 of the 48 pairs by itself. Not one of the nine is an ordinary photograph.
 *
 * With no history on either side, both answers move one composite image and
 * nothing else — while the question cannot be answered, because if it is one
 * face found twice then the two crops are the same pixels.
 */
{
  const strangers = [
    person("only-a", 1, 0, ["one-and-only"]),
    person("only-b", 1, 45.5, ["one-and-only"]),
  ];
  assert(
    suggestMerges(strangers, { ...options, limit: 10 }).length === 0,
    "one face each, one shared photo, no history: not a question",
  );

  // VACUITY: the identical pair IS offered the moment either side has a photo
  // of its own. Without this, deleting the whole vetoed branch would pass the
  // assertion above while silencing every question in the library.
  const withHistory = [
    person("only-a", 1, 0, ["one-and-only"]),
    person("only-b", 2, 45.5, ["one-and-only", "elsewhere"]),
  ];
  const offered = suggestMerges(withHistory, { ...options, limit: 10 });
  assert(
    offered.length === 1 && offered[0].blockedByCoOccurrence,
    `a side with history elsewhere must still be asked, got ${offered.length}`,
  );
}

/**
 * A crowded photo is not a question.
 *
 * The owner was shown a party photograph holding five or six people and asked
 * whether two of the faces in it were one person: "of course there are more
 * than one people in the image, any basic ML model will tell that". The app
 * already knows -- it has placed several distinct people in that frame.
 *
 * Measured on his live index: of the 4,073 pairs held apart by exactly one
 * shared photo, that photo holds 3+ people in 92.2% of cases. Run against his
 * real sixty-question queue, 7 were co-occurrence-blocked, 6 are suppressed
 * here, and the survivor is the doubtful shape -- two people in frame at 0.899.
 */
{
  const crowd = ["party"];
  const guest = (id: string, degrees: number) =>
    person(id, 3, degrees, [...crowd, `${id}-own`]);
  // Two of the party's faces, plus four other people who are also in it.
  const partygoers = [
    guest("guest-a", 0),
    guest("guest-b", 51),
    guest("guest-c", 140),
    guest("guest-d", 160),
    guest("guest-e", 175),
  ];
  assert(
    suggestMerges(partygoers, { ...options, limit: 10 }).length === 0,
    "two faces in a photo the library already fills with people is not a question",
  );

  // VACUITY: the SAME two people, in a photo holding only them, must still be
  // asked. Otherwise this rule would have silenced the doubtful case too, which
  // is the only one left worth his attention.
  const justTwo = [guest("guest-a", 0), guest("guest-b", 51)];
  const asked = suggestMerges(justTwo, { ...options, limit: 10 });
  assert(
    asked.length === 1 && asked[0].blockedByCoOccurrence,
    `a two-person frame carries real doubt and must still be asked, got ${asked.length}`,
  );

  // The MIN, not the max: sharing one crowded frame AND one quiet frame leaves
  // the quiet one as genuine evidence, so the pair is still asked.
  const alsoAlone = [
    person("guest-a", 3, 0, [...crowd, "quiet", "guest-a-own"]),
    person("guest-b", 3, 51, [...crowd, "quiet", "guest-b-own"]),
    guest("guest-c", 140),
    guest("guest-d", 160),
    guest("guest-e", 175),
  ];
  assert(
    suggestMerges(alsoAlone, { ...options, limit: 10 }).length === 1,
    "a quiet shared frame still counts even when another shared frame is crowded",
  );
}

console.log("suggest-ranking self-check passed");
