// @ts-expect-error Node's TypeScript runner requires the source extension.
import { faceMergeReviewPair, remainingFaceMergeSuggestions } from "./face-merge-review.ts";
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

console.log("face merge review self-check passed");
