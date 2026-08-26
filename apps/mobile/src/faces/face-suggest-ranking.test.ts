import {
  suggestMerges,
  // @ts-expect-error Node's TypeScript runner requires the source extension.
} from "./face-cluster.ts";
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
 * It was spending them badly. Ranked by fewest-shared-photos, six of the first
 * twenty slots on his index went to pairs of single-face strangers -- one photo
 * each -- while person-16 (257 faces) split from person-745 (150 faces) sat at
 * rank fourteen. Every question costs the same tap; what differs is the size of
 * the repair.
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
// One would repair a 150-photo tile; the other, two strangers with one photo
// each. The small pair is deliberately the MORE similar of the two, which is
// exactly the shape that used to sink the big one.
const big = [
  person("big-a", 257, 0, ["shared-1", "b1", "b2"]),
  person("big-b", 150, 51, ["shared-1", "b3", "b4"]),
];
const tiny = [
  person("tiny-a", 1, 0, ["shared-2"]),
  person("tiny-b", 1, 45.5, ["shared-2"]),
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

console.log("suggest-ranking self-check passed");
