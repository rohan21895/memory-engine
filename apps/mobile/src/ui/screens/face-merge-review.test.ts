// @ts-expect-error Node's TypeScript runner requires the source extension.
import { advanceFaceMergeReviewProgress, coOccurrenceEvidence, faceMergeReviewPair, remainingFaceMergeSuggestions } from "./face-merge-review.ts";
import type { MergeSuggestion } from "../../faces/face-cluster";
import type { FaceIndexPerson } from "../../faces/face-index";

function assert(value: unknown, message: string): asserts value {
  if (!value) throw new Error(`face merge review self-check failed: ${message}`);
}

const people: FaceIndexPerson[] = [
  { id: "person-a", faceCount: 310, coverAssetId: "a", assetIds: ["a", "shared"] },
  { id: "person-b", faceCount: 180, coverAssetId: "b", assetIds: ["b", "shared"] },
  { id: "person-c", faceCount: 12, coverAssetId: "c", assetIds: ["c"] },
];
const byId = new Map(people.map((person) => [person.id, person]));
const suggestion: MergeSuggestion = {
  a: "person-a",
  b: "person-b",
  similarity: 0.539,
  bar: 0.509,
  sharedAssets: 1,
  appearances: 24,
  photosFixed: 180,
  blockedByCoOccurrence: true,
};

const pair = faceMergeReviewPair(suggestion, (personId) => byId.get(personId));
assert(pair?.first.faceCount === 310, "the larger suggested group resolves for the first tile");
assert(pair?.second.faceCount === 180, "the second tile keeps its own face count");
assert(
  faceMergeReviewPair({ ...suggestion, b: "missing" }, (personId) => byId.get(personId)) === undefined,
  "a stale person id is not rendered as the wrong face",
);

const related: MergeSuggestion = { ...suggestion, b: "person-c" };
const unrelated: MergeSuggestion = { ...suggestion, a: "person-c", b: "person-d" };
assert(
  remainingFaceMergeSuggestions([suggestion, related, unrelated], suggestion, false).length === 2,
  "not-the-same removes only the answered pair",
);
const afterMerge = remainingFaceMergeSuggestions([suggestion, related, unrelated], suggestion, true);
assert(
  afterMerge.length === 1 && afterMerge[0] === unrelated,
  "same-person drops every suggestion made stale by the merge",
);

/**
 * The evidence line, which exists because "1 shared photo" is not a fact the
 * user can act on without its denominator.
 *
 * The real pair this is modelled on: person-27 (463 faces) and person-729 (24
 * faces) share exactly one photo out of 24 — 4.2%.
 */
{
  const line = coOccurrenceEvidence(suggestion);
  assert(line !== undefined, "a co-occurrence-blocked pair must explain itself");
  assert(
    line.includes("1 photo") && line.includes("24") && line.includes("4.2%"),
    `both halves of the rate must be shown, got: ${line}`,
  );
  assert(
    line.includes("counted twice"),
    `a 0.6% rate must say what that usually means, got: ${line}`,
  );
}

// Progress distinguishes answers saved from photos actually brought together.
// "Not the same" is valuable protection, but must never inflate the repair
// total merely because it advanced the queue.
{
  const start = { answered: 0, photosRepaired: 0 };
  const keptApart = advanceFaceMergeReviewProgress(start, suggestion, false);
  assert(
    keptApart.answered === 1 && keptApart.photosRepaired === 0,
    `a safe separate answer advances only the answer count, got ${JSON.stringify(keptApart)}`,
  );
  const broughtTogether = advanceFaceMergeReviewProgress(keptApart, suggestion, true);
  assert(
    broughtTogether.answered === 2 &&
      broughtTogether.photosRepaired === suggestion.photosFixed,
    `a same-person answer must report its repair, got ${JSON.stringify(broughtTogether)}`,
  );
  // Sabotage guard: run the opposite answer through the same function and
  // verify it produces a different total. A constant or inert implementation
  // cannot satisfy both sides of this check.
  const sabotaged = advanceFaceMergeReviewProgress(keptApart, suggestion, false);
  assert(suggestion.photosFixed > 0, "the sabotage needs a non-zero repair to suppress");
  assert(
    sabotaged.photosRepaired !== broughtTogether.photosRepaired,
    "changing Same person to Not the same must actually suppress the repair count",
  );
}

// THE case the denominator exists for. The same single shared photo reads the
// opposite way when it is ten of nineteen rather than one of 24 — and a version
// that only ever printed "1 shared photo" could not tell these apart at all.
{
  const alwaysTogether = coOccurrenceEvidence({
    ...suggestion,
    sharedAssets: 10,
    appearances: 19,
  });
  assert(
    alwaysTogether?.includes("two ") === true &&
      alwaysTogether.includes("different people"),
    `a 53% rate must read as two people, got: ${alwaysTogether}`,
  );
  // Vacuity guard: the low-rate case above produced the OPPOSITE reading from
  // the same function, so this is the rate being read and not a constant string.
  assert(
    coOccurrenceEvidence(suggestion) !== alwaysTogether,
    "the two rates must not produce the same sentence",
  );
}

// The middle band commits to nothing. Between 5% and 15% the measurement does
// not separate the two populations, and inventing a lean would be dishonest.
{
  const middling = coOccurrenceEvidence({
    ...suggestion,
    sharedAssets: 1,
    appearances: 10,
  });
  assert(
    middling !== undefined &&
      !middling.includes("counted twice") &&
      !middling.includes("different people"),
    `a 10% rate must state the fact and stop, got: ${middling}`,
  );
}

// Pairs held back by similarity rather than by co-occurrence are a different
// question, so this must stay silent rather than volunteer irrelevant evidence.
{
  assert(
    coOccurrenceEvidence({ ...suggestion, blockedByCoOccurrence: false }) ===
      undefined,
    "only co-occurrence-blocked pairs get the co-occurrence line",
  );
  assert(
    coOccurrenceEvidence({ ...suggestion, sharedAssets: 0 }) === undefined,
    "a pair sharing no photos has no co-occurrence evidence to show",
  );
  // Vacuity guard: the same suggestion WITH the flag does produce a line, so
  // these undefineds are the guard working rather than the function being inert.
  assert(
    coOccurrenceEvidence(suggestion) !== undefined,
    "the blocked case must still produce a line",
  );
}

// The case the owner actually hit, and which every test above sailed past.
//
// One face found twice becomes two one-face people whose only photo is the same
// photo: sharedAssets 1, appearances 1. The rate is then 1/1 = 100%, which sails
// past LIKELY_TWO_PEOPLE, so the screen showed him a picture of his wife beside
// the identical picture of his wife and told him underneath that people
// photographed together this often are usually two different people. Exactly
// backwards, stated with total confidence, on ten of his top sixty questions.
{
  const doubleDetection = { ...suggestion, sharedAssets: 1, appearances: 1 };
  const line = coOccurrenceEvidence(doubleDetection);
  assert(
    line !== undefined && !line.includes("different people"),
    `1 shared photo of 1 must NOT be called two different people, got: ${line}`,
  );
  assert(
    line !== undefined && line.includes("counted twice"),
    `1 shared photo of 1 is the double-detection signature, got: ${line}`,
  );

  // A denominator too small to separate the two populations must state the fact
  // and stop rather than pick a side.
  const thin = coOccurrenceEvidence({ ...suggestion, sharedAssets: 1, appearances: 3 });
  assert(
    thin !== undefined &&
      !thin.includes("different people") &&
      !thin.includes("counted twice"),
    `1 of 3 cannot support a conclusion either way, got: ${thin}`,
  );

  // VACUITY: the "two different people" sentence must still be reachable when
  // the denominator IS big enough. Without this, deleting that branch entirely
  // would pass every assertion above while destroying the function's purpose.
  const genuine = coOccurrenceEvidence({ ...suggestion, sharedAssets: 12, appearances: 30 });
  assert(
    genuine !== undefined && genuine.includes("different people"),
    `40% of a real denominator must still read as two people, got: ${genuine}`,
  );
  // VACUITY: and so must the low-rate reading, for the same reason.
  const sparse = coOccurrenceEvidence({ ...suggestion, sharedAssets: 1, appearances: 400 });
  assert(
    sparse !== undefined && sparse.includes("counted twice"),
    `1 of 400 must still read as a duplicate detection, got: ${sparse}`,
  );
}

console.log("face merge review self-check passed");
