import {
  clusterFaces,
  // @ts-expect-error Node's TypeScript runner requires the source extension.
} from "./face-cluster.ts";
import type { FaceObservation } from "./types.ts";

function assert(value: unknown, message: string): asserts value {
  if (!value) throw new Error(`must-link self-check failed: ${message}`);
}

/**
 * The promise the merge review makes, in the one place it can be broken.
 *
 * The owner's split tiles cannot be merged automatically -- measured on his own
 * library, every bar low enough to join them lets more known-different-people
 * pairs through than it gains merges. So the app asks him, and the screen tells
 * him "nothing is combined until you say so". The unstated other half is that
 * once he HAS said so, it stays said: the app reclusters after every scan, and
 * an answer that evaporated on the next recluster would quietly undo an
 * evening's work with no error anywhere.
 *
 * `applyConstraintToPeople` already covers the immediate merge. What is pinned
 * here is the durable half -- a recorded "Same person" surviving a cluster built
 * from raw observations, where the measured bars would refuse it.
 */

const atDegrees = (degrees: number): number[] => {
  const radians = (degrees * Math.PI) / 180;
  return [Math.cos(radians), Math.sin(radians)];
};

// Two groups far enough apart that the bars genuinely refuse them -- the same
// shape as person-16 and person-745 on his phone, which sit below the merge bar
// and so stay two tiles forever without an answer.
const faces: FaceObservation[] = [
  ...[0, 1, 2, 3, 4].map((degrees, index) => ({
    assetId: `avika-${index + 1}`,
    embedding: atDegrees(degrees),
    embeddingKind: "identity" as const,
  })),
  ...[70, 71, 72, 73, 74].map((degrees, index) => ({
    assetId: `avika-other-${index + 1}`,
    embedding: atDegrees(degrees),
    embeddingKind: "identity" as const,
  })),
];
const options = { threshold: 0.9, identityMergeThreshold: 0.9 };

// Vacuity guard FIRST: without the answer these really are two tiles. If they
// merged on their own, everything below would pass while proving nothing.
{
  const unconstrained = clusterFaces(faces, options);
  assert(
    unconstrained.length === 2,
    `the bars must refuse this pair on their own, or the case is vacuous ` +
      `(got ${unconstrained.length} tiles)`,
  );
}

// The answer, applied to a cluster built from scratch — which is what every
// scan does.
{
  const answered = clusterFaces(faces, {
    ...options,
    constraints: [{ kind: "must" as const, a: "avika-1", b: "avika-other-1" }],
  });
  assert(
    answered.length === 1,
    `a recorded "same person" must survive a rebuild (got ${answered.length} tiles)`,
  );
  assert(
    answered[0].faceCount === faces.length,
    `and bring both whole groups, not just the two named photos ` +
      `(got ${answered[0].faceCount} of ${faces.length} faces)`,
  );
  // The constraint names two PHOTOS. Every face of both groups has to come
  // with them, or the tile he confirmed would come back half empty.
  for (const face of faces) {
    assert(
      answered[0].assetIds.includes(face.assetId),
      `${face.assetId} must land in the merged tile`,
    );
  }
}

// An answer about people who are not in the library any more must not take
// anything else with it. A dropped photo is normal; a constraint that resolves
// to the wrong person is the original wrong-face bug in another costume.
{
  const stale = clusterFaces(faces, {
    ...options,
    constraints: [{ kind: "must" as const, a: "deleted-photo", b: "also-gone" }],
  });
  assert(
    stale.length === 2,
    `a constraint naming photos that no longer exist must change nothing ` +
      `(got ${stale.length} tiles)`,
  );
  // The HALF-resolvable case, which is the one that actually bites: one photo
  // still in the library, the other deleted. Both sides missing resolves to
  // nothing and is harmless either way; a single unresolved side is what can
  // bind a real person to a stray index and hand them somebody else's faces.
  const halfStale = clusterFaces(faces, {
    ...options,
    constraints: [{ kind: "must" as const, a: "avika-1", b: "deleted-photo" }],
  });
  assert(
    halfStale.length === 2,
    `a constraint with one vanished side must not merge anything ` +
      `(got ${halfStale.length} tiles)`,
  );
  const avikaTile = halfStale.find((p: { assetIds: string[] }) =>
    p.assetIds.includes("avika-1"),
  );
  assert(
    avikaTile !== undefined && avikaTile.faceCount === 5,
    `and must leave that person exactly as they were ` +
      `(got ${avikaTile?.faceCount} faces)`,
  );
}

// Both kinds at once, since he will answer some pairs each way in one sitting
// and they must not interfere.
{
  const mixed = clusterFaces(faces, {
    ...options,
    constraints: [
      { kind: "must" as const, a: "avika-1", b: "avika-other-1" },
      { kind: "cannot" as const, a: "avika-2", b: "avika-3" },
    ],
  });
  // The must-link is the stronger statement and the two constraints are in
  // direct conflict, so what matters is that the result is coherent -- every
  // face placed, nothing duplicated -- rather than which rule wins.
  const placed = mixed.flatMap((person: { assetIds: string[] }) => person.assetIds);
  assert(
    placed.length === new Set(placed).size,
    "no face may end up in two tiles",
  );
  assert(
    placed.length === faces.length,
    `every face must still be placed (got ${placed.length} of ${faces.length})`,
  );
}

console.log("must-link survives self-check passed");
