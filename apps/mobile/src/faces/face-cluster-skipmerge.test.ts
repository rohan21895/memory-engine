// @ts-expect-error Node's TypeScript runner requires the source extension.
import { clusterFaces, extendFaceClusters } from "./face-cluster.ts";

function assert(value: unknown, message: string): void {
  if (!value) throw new Error(`face-cluster skipMerge self-check failed: ${message}`);
}

/**
 * Deferring consolidation is only safe if it can leave people SPLIT and never
 * fuse two of them, and if the eventual full pass lands where it always did.
 * Both are asserted here rather than assumed, because the merge path is the one
 * this codebase has broken before: a past relaxation produced a single tile
 * holding 2,164 photos.
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

// Two tight groups close enough to each other that consolidation joins them.
const observations = [
  face("a-1", 0), face("a-2", 0.1), face("a-3", 0.2), face("a-4", 0.3), face("a-5", 0.4),
  face("b-1", 8), face("b-2", 8.1), face("b-3", 8.2), face("b-4", 8.3), face("b-5", 8.4),
];

const options = { threshold: 0.9999, evidencedMergeThreshold: 0.98 };
const merged = clusterFaces(observations, options);
const deferred = extendFaceClusters([], observations, { ...options, skipMerge: true });

const total = (people: { faceCount: number }[]) =>
  people.reduce((sum, person) => sum + person.faceCount, 0);

assert(total(deferred) === observations.length, "every face is still assigned when merging is skipped");
assert(
  deferred.length === 2 && merged.length === 1,
  `sabotage guard: this fixture must leave two deferred tiles and consolidate them to one (got ${deferred.length} and ${merged.length})`,
);
assert(
  deferred.every((person) => new Set(person.assetIds).size === person.assetIds.length),
  "a deferred cluster must not collect duplicate assets",
);

// The consolidating pass over the same faces is what the scan runs at the end,
// and it must be unaffected by the batches that skipped merging.
const consolidatedLater = extendFaceClusters(deferred, [], { threshold: 0.9 });
assert(
  consolidatedLater.length === merged.length &&
    total(consolidatedLater) === total(merged),
  "consolidating afterwards reaches the same grouping as never having skipped",
);

console.log("face-cluster skipMerge self-check passed");
