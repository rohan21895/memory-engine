import {
  SAME_PHOTO_EXCEPTION_SIMILARITY,
  clusterFaces,
  suggestMerges,
  // @ts-expect-error Node's TypeScript runner requires the source extension.
} from "./face-cluster.ts";

function assert(value: unknown, message: string): void {
  if (!value) throw new Error(`face-cluster suggest self-check failed: ${message}`);
}

/**
 * `suggestMerges` exists to hand the undecidable pairs to the person who can
 * actually decide them. It is only useful if it offers QUESTIONS -- so the
 * cases below are mostly about what it must refuse to ask.
 */

function atDegrees(degrees: number): number[] {
  const radians = (degrees * Math.PI) / 180;
  return [Math.cos(radians), Math.sin(radians)];
}
const face = (assetId: string, degrees: number) => ({
  assetId,
  embedding: atDegrees(degrees),
  embeddingKind: "identity" as const,
});

// Three well-separated groups of five. Tight assignment keeps them apart, and
// the merge bar is set so that NO pair merges on its own -- every pair is
// therefore a candidate question, which is the state this feature is for.
const faces = [
  ...[0, 0.5, 1, 1.5, 2].map((d, i) => face(`ana-${i}`, d)),
  ...[14, 14.5, 15, 15.5, 16].map((d, i) => face(`ben-${i}`, d)),
  ...[70, 70.5, 71, 71.5, 72].map((d, i) => face(`cal-${i}`, d)),
];
const options = { threshold: 0.999, evidencedMergeThreshold: 0.999 };
const people = clusterFaces(faces, options);
assert(people.length === 3, `three tiles to reason about, got ${people.length}`);

const suggestions = suggestMerges(people, { ...options, floor: 0 });
const named = suggestions.map((s) => `${s.a}+${s.b}`);

// ana and ben are 14 degrees apart (cosine ~0.97); cal is 70 degrees from ana
// (~0.34). The close pair must be offered, and offered FIRST.
assert(suggestions.length > 0, "a near-miss pair must be offered");
const top = suggestions[0];
const anaId = people.find((p) => p.assetIds.includes("ana-0"))?.id;
const benId = people.find((p) => p.assetIds.includes("ben-0"))?.id;
const calId = people.find((p) => p.assetIds.includes("cal-0"))?.id;
assert(
  (top.a === anaId && top.b === benId) || (top.a === benId && top.b === anaId),
  `the closest pair must rank first, got ${top.a}+${top.b}`,
);
assert(
  top.similarity > 0.9 && top.similarity < top.bar,
  `a suggestion must sit UNDER the bar it failed (${top.similarity} vs ${top.bar})`,
);

// Ranking: the far pair, if offered at all, must come after the close one.
const farIndex = suggestions.findIndex(
  (s) => (s.a === calId || s.b === calId),
);
assert(farIndex !== 0, "a distant pair must never outrank a near one");

// A pair already OVER its bar is not a question -- the next consolidation
// merges it without asking.
//
// Reached by clustering at a strict bar (so all three tiles survive) and then
// asking at a looser one, which is exactly what happens when stored people were
// grouped under an earlier calibration. An earlier draft merged the pair first
// and then asserted nothing was over the bar, which was vacuously true whether
// or not the exclusion existed.
{
  const asked = suggestMerges(people, {
    threshold: 0.999,
    evidencedMergeThreshold: 0.9,
    floor: 0,
  });
  const overTheBar = asked.filter((s) => s.similarity >= s.bar);
  assert(
    overTheBar.length === 0,
    `nothing already over its bar may be offered, got ${overTheBar
      .map((s) => `${s.a}+${s.b} at ${s.similarity.toFixed(3)} >= ${s.bar}`)
      .join(", ")}`,
  );
  // Vacuity guard: the ana/ben pair really is above 0.9, so the exclusion had
  // something to exclude.
  assert(
    !asked.some(
      (s) => (s.a === anaId && s.b === benId) || (s.a === benId && s.b === anaId),
    ),
    "the ana/ben pair sits at ~0.97 and must be withheld at a 0.9 bar",
  );
}

// Two faces in ONE photo below the mirror/panorama exception are two different
// people. That is evidence, and the merge pass treats it as a hard cannot-link,
// so it must never come back as a question.
{
  // 50 degrees apart is cosine ~0.64, BELOW the 0.72 exception -- which is what
  // makes them two people rather than one face captured twice. At 14 degrees
  // (cosine 0.97) the merge pass reads a mirror or a panorama, and excluding
  // that would be wrong; an earlier draft of this case had exactly that bug.
  const groupShot = [
    { assetId: "group", embedding: atDegrees(0), embeddingKind: "identity" as const },
    { assetId: "group", embedding: atDegrees(50), embeddingKind: "identity" as const },
    ...[0.5, 1, 1.5, 2].map((d, i) => face(`ana-solo-${i}`, d)),
    ...[50.5, 51, 51.5, 52].map((d, i) => face(`ben-solo-${i}`, d)),
  ];
  const two = clusterFaces(groupShot, options);
  assert(two.length === 2, `the group shot must stay two people, got ${two.length}`);
  const asked = suggestMerges(two, { ...options, floor: 0 });
  assert(
    asked.length === 0,
    `two people photographed together must not be offered as a merge ` +
      `(similarity ${asked[0]?.similarity.toFixed(3)} is under the ` +
      `${SAME_PHOTO_EXCEPTION_SIMILARITY} exception, so it is evidence, not doubt)`,
  );
}

// An answer the user already gave must not be asked again.
{
  const withRuling = suggestMerges(people, {
    ...options,
    floor: 0,
    constraints: [{ kind: "cannot" as const, a: "ana-0", b: "ben-0" }],
  });
  assert(
    !withRuling.some(
      (s) =>
        (s.a === anaId && s.b === benId) || (s.a === benId && s.b === anaId),
    ),
    "a pair the user already ruled out must never be offered again",
  );
  assert(
    withRuling.length < suggestions.length,
    "ruling a pair out must actually remove it, not merely reorder",
  );
}

// The floor keeps the list worth reading rather than exhaustive.
{
  const floored = suggestMerges(people, { ...options, floor: 0.9 });
  assert(
    floored.every((s) => s.similarity >= 0.9),
    "nothing below the floor may be offered",
  );
  assert(
    floored.length < suggestMerges(people, { ...options, floor: 0 }).length,
    "raising the floor must shorten the list",
  );
}

// Determinism and bounds.
{
  assert(suggestMerges(people, { ...options, floor: 0, limit: 1 }).length === 1, "limit is honoured");
  assert(suggestMerges(people, { ...options, limit: 0 }).length === 0, "a zero limit asks nothing");
  assert(suggestMerges([], options).length === 0, "no people, no questions");
  assert(
    suggestMerges(people, { ...options, floor: 0 }).map((s) => `${s.a}+${s.b}`).join() ===
      named.join(),
    "the same library must produce the same list in the same order",
  );
}

// The bigger cluster leads, so the UI shows the more recognisable face first.
assert(
  people.find((p) => p.id === top.a)!.faceCount >=
    people.find((p) => p.id === top.b)!.faceCount,
  "the larger cluster is named first",
);

console.log(`face-cluster suggest self-check passed (${suggestions.length} suggestions)`);
