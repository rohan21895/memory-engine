// @ts-expect-error The Expo app deliberately does not ship Node declarations.
import { readFileSync } from "node:fs";

import {
  clusterFaces,
  clusterFacesByGraph,
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

// ---------------------------------------------------------------------------
// THE SAME PROMISE, ON THE PATH THAT ACTUALLY SHIPS.
//
// Everything above calls `clusterFaces`. That was the whole clustering when
// this file was written, and it is not any more: `peopleFromObservations`
// routes to `clusterFacesByGraph` whenever the flag is on and the library is
// affordable, which is every real library. The graph pass runs no merge sweep,
// and the merge sweep is where `clusterFaces` applies the user's answers -- so
// for a while every assertion above passed while the shipping path silently
// forgot every answer on the next recluster.
//
// The lesson is the file's, not the feature's: a gate anchored to a function
// nobody calls proves nothing, and it fails silently, which is the worst way to
// fail. So the shipping path is exercised here directly, with the flag itself
// checked so this cannot quietly become the dead half in its turn.
// ---------------------------------------------------------------------------

{
  const indexSource = readFileSync(
    new URL("./face-index.ts", import.meta.url),
    "utf8",
  );
  assert(
    indexSource.includes("const GRAPH_CLUSTERING = true;") &&
      indexSource.includes(
        "return clusterFacesByGraph(centeredForClustering(observations), options);",
      ),
    "VACUITY: the graph pass must still be the one that ships, or this section " +
      "is testing the dead half and the sections above are testing the live one",
  );
  assert(
    indexSource.includes("constraints: index.constraints ?? []"),
    "VACUITY: ...and the answers must still be handed to it",
  );
}

{
  // Same vacuity guard as the greedy half: unanswered, these are two tiles.
  const unconstrained = clusterFacesByGraph(faces, options);
  assert(
    unconstrained.length === 2,
    `the graph pass must refuse this pair on its own, or the case is vacuous ` +
      `(got ${unconstrained.length} tiles)`,
  );

  const answered = clusterFacesByGraph(faces, {
    ...options,
    constraints: [{ kind: "must" as const, a: "avika-1", b: "avika-other-1" }],
  });
  assert(
    answered.length === 1,
    `a recorded "same person" must survive a graph rebuild too ` +
      `(got ${answered.length} tiles)`,
  );
  assert(
    answered[0].faceCount === faces.length,
    `and bring both whole groups with it ` +
      `(got ${answered[0].faceCount} of ${faces.length} faces)`,
  );

  // The same two stale cases, because a constraint that resolves to the wrong
  // person is worse on this path: there is no sweep afterwards to disagree.
  assert(
    clusterFacesByGraph(faces, {
      ...options,
      constraints: [{ kind: "must" as const, a: "deleted-photo", b: "also-gone" }],
    }).length === 2,
    "a constraint naming photos that no longer exist must change nothing",
  );
  assert(
    clusterFacesByGraph(faces, {
      ...options,
      constraints: [{ kind: "must" as const, a: "avika-1", b: "deleted-photo" }],
    }).length === 2,
    "a constraint with one vanished side must not merge anything",
  );
}

// The perceptual fallback must still produce people. The graph pass keeps only
// identity embeddings -- correctly, since label propagation along an 8x8 luma
// grid would walk between strangers -- but for a while it DROPPED the rest, so
// a phone whose identity model failed to load showed an empty People screen
// instead of a conservatively-grouped one.
{
  const fallbackOnly: FaceObservation[] = [0, 1].map((index) => ({
    assetId: `fallback-${index + 1}`,
    embedding: atDegrees(index * 60),
    embeddingKind: "perceptual" as const,
  }));
  assert(
    clusterFacesByGraph(fallbackOnly, options).length === 2,
    "faces with no identity embedding must still appear as people",
  );
  const mixedKinds = clusterFacesByGraph([...faces, ...fallbackOnly], options);
  assert(
    mixedKinds.length === 4,
    `identity and fallback faces must both survive and stay apart ` +
      `(got ${mixedKinds.length} tiles)`,
  );
  const ids = mixedKinds.map((person: { id: string }) => person.id);
  assert(
    ids.length === new Set(ids).size,
    "...and the two halves must not hand out the same person id",
  );
}

console.log("must-link survives self-check passed");
