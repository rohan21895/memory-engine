import {
  clusterFaces,
  extendFaceClusters,
  // @ts-expect-error Node's TypeScript runner requires the source extension.
} from "./face-cluster.ts";
import type { FaceObservation, Person } from "./types.ts";

function assert(value: unknown, message: string): asserts value {
  if (!value) throw new Error(`empty-batch self-check failed: ${message}`);
}

/**
 * A batch that found nothing must cost nothing.
 *
 * Measured on the owner's device, mid-scan of an already-complete library:
 *
 *   photos=0 faces=0 calibrate=994ms cluster=35463ms consolidated=1
 *   persistObservations=5580ms persist=6248ms
 *
 * Thirty-five seconds of frozen JS thread, plus a 13.8MB file rewritten, to
 * re-derive an answer that could not have changed -- no face arrived, so no
 * cluster moved, so the O(people^2) merge sweep had nothing new to find. A
 * re-scan walks hundreds of such batches.
 *
 * The guard lives in `appendPeople`, which needs the module singleton and a
 * filesystem. What CAN be pinned down here is the property the guard relies on:
 * that an empty batch genuinely changes nothing, so skipping the sweep cannot
 * change the grouping. If that ever stops being true, this fails and the guard
 * has to go.
 */

const at = (degrees: number): number[] => {
  const radians = (degrees * Math.PI) / 180;
  return [Math.cos(radians), Math.sin(radians)];
};
const face = (assetId: string, degrees: number): FaceObservation => ({
  assetId,
  embedding: at(degrees),
  embeddingKind: "identity",
});

// Two groups close enough that a merge sweep has real work to do, so "nothing
// changed" is a measured outcome rather than a vacuous one.
const library = [
  ...[0, 1, 2, 3].map((d, i) => face(`ana-${i}`, d)),
  ...[24, 25, 26, 27].map((d, i) => face(`ben-${i}`, d)),
];
const options = { threshold: 0.995, evidencedMergeThreshold: 0.995 };

const shape = (people: readonly Person[]): string =>
  people
    .map((person) => `${person.id}:${person.faceCount}:${person.assetIds.join(",")}`)
    .sort()
    .join("|");

const settled = clusterFaces(library, options);
assert(settled.length === 2, `two people to sweep between, got ${settled.length}`);
const before = shape(settled);

// The empty batch, run WITH the merge sweep enabled -- exactly what the scan
// used to do every eighth time.
const afterSweep = extendFaceClusters(settled, [], options);
assert(
  shape(afterSweep) === before,
  `an empty batch must not change the grouping, ` +
    `got ${shape(afterSweep)} from ${before}`,
);

// And with the sweep skipped, which is what the guard now does. Same answer, so
// skipping is free rather than a trade.
const afterSkip = extendFaceClusters(clusterFaces(library, options), [], {
  ...options,
  skipMerge: true,
});
assert(
  shape(afterSkip) === before,
  `skipping the sweep on an empty batch must give the same grouping, ` +
    `got ${shape(afterSkip)}`,
);

// Vacuity guard: the sweep is not a no-op in general. At a bar these two DO
// clear, it merges them -- so the equality above is a fact about empty batches,
// not a sweep that never does anything.
{
  const loose = clusterFaces(library, {
    threshold: 0.995,
    evidencedMergeThreshold: 0.5,
    identityMergeThreshold: 0.5,
  });
  assert(
    loose.length === 1,
    `the sweep must be capable of merging these two, got ${loose.length} people`,
  );
}

// A batch that DOES bring faces still changes things, or the guard would be
// skipping real work.
{
  const grown = extendFaceClusters(clusterFaces(library, options), [face("cal-0", 90)], options);
  assert(
    shape(grown) !== before,
    "a non-empty batch must still change the grouping",
  );
}

console.log("empty-batch self-check passed");
