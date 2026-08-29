// @ts-expect-error Node's TypeScript runner requires the source extension.
import { planConsolidationSweep, sameConsolidationBars, scanEndNeedsRecluster } from "./face-index.ts";
// @ts-expect-error Node's TypeScript runner requires the source extension.
import { clusterFaces, extendFaceClusters } from "./face-cluster.ts";
// A type-only import is erased before the extension can matter, so unlike the
// value imports above it needs no suppression.
import type { FaceObservation, Person } from "./types.ts";

function assert(condition: unknown, message: string): asserts condition {
  if (!condition) {
    throw new Error(`face-index recluster-cost self-check failed: ${message}`);
  }
}

/**
 * The measured bug, as a test.
 *
 * On the owner's phone a scan that found ONE new photo re-clustered all 17,768
 * faces: `rebuildPeople 175747ms ... -> 2244 people`. Three minutes of frozen JS
 * thread, during which the photo picker span on "Looking through your photos..."
 * and then bailed back to Albums. The guard at the time excluded only the
 * zero-new-photos case, so one photo paid what ten thousand pay.
 *
 * Two halves, and the second is the one that keeps the first honest: a fix that
 * simply stopped re-clustering would pass the regression test and silently leave
 * the library un-clustered forever.
 */

const settled = {
  wantedRule: "avg-linkage-w600k-mbf-calibrated-1",
  storedRule: "avg-linkage-w600k-mbf-calibrated-1",
  storedThreshold: 0.55,
  measuredThreshold: 0.55,
  observationsPruned: false,
};

// --- 1. The regression. One new photo must NOT re-cluster the library. -------

assert(
  !scanEndNeedsRecluster({ ...settled, newlyProcessed: 1 }),
  "ONE new photo must not trigger the full rebuild -- this is the measured 176s hang",
);
assert(
  !scanEndNeedsRecluster({ ...settled, newlyProcessed: 32 }),
  "a single scan batch must not trigger the full rebuild",
);
// The bar drifts a little every time the library grows. A drift smaller than
// the hysteresis cannot reassign a face, so it must not buy a rebuild either.
assert(
  !scanEndNeedsRecluster({
    ...settled,
    newlyProcessed: 4,
    measuredThreshold: 0.5501,
  }),
  "a bar that moved by 0.0001 must not trigger the full rebuild",
);

// --- 2. Vacuity guard. The full path must still exist and still be taken. ----
//
// Without these, a fix that returned false unconditionally would pass
// everything above while never re-clustering the library again.

assert(
  scanEndNeedsRecluster({ ...settled, newlyProcessed: 5000 }),
  "a library-sized batch must still take the full rebuild",
);
assert(
  scanEndNeedsRecluster({ ...settled, newlyProcessed: 257 }),
  "a scan past the consolidation window must still take the full rebuild",
);
assert(
  !scanEndNeedsRecluster({ ...settled, newlyProcessed: 256 }),
  "...and one exactly at the window must not, so the boundary is the stated one",
);
assert(
  scanEndNeedsRecluster({
    ...settled,
    newlyProcessed: 1,
    forcedThreshold: 0.42,
  }),
  "a caller naming an explicit bar must still get the full rebuild -- nothing else honours it",
);
assert(
  scanEndNeedsRecluster({
    ...settled,
    newlyProcessed: 1,
    observationsPruned: true,
  }),
  "deleted photos must still get the full rebuild -- people still list their assets",
);
assert(
  scanEndNeedsRecluster({
    ...settled,
    newlyProcessed: 1,
    storedRule: "centered-avg-linkage-w600k-mbf-1",
  }),
  "a changed clustering rule must still get the full rebuild -- the centroids are in another space",
);
assert(
  scanEndNeedsRecluster({ ...settled, newlyProcessed: 1, storedRule: undefined }),
  "a legacy index with no recorded rule must still get the full rebuild",
);
assert(
  scanEndNeedsRecluster({
    ...settled,
    newlyProcessed: 1,
    measuredThreshold: 0.57,
  }),
  "a bar that genuinely moved must still get the full rebuild",
);

// The diagnostic comparison remains exact, so the log proves whether a bar
// moved even when the sweep planner safely holds a stricter persisted value.
const mergeBars = {
  identity: 0.55,
  perceptual: 0.72,
  evidenced: 0.43,
  temporal: 0.41,
};
assert(
  sameConsolidationBars(mergeBars, { ...mergeBars }),
  "identical persisted merge bars permit a touched-only sweep",
);
assert(
  !sameConsolidationBars(undefined, mergeBars),
  "a legacy index with no persisted merge bars must take the full sweep",
);
for (const field of ["identity", "perceptual", "evidenced", "temporal"] as const) {
  assert(
    !sameConsolidationBars(mergeBars, {
      ...mergeBars,
      [field]: mergeBars[field] + Number.EPSILON,
    }),
    `VACUITY: moving ${field} by one representable step must be observable`,
  );
}

const legacyPlan = planConsolidationSweep(undefined, mergeBars);
assert(
  !legacyPlan.restricted &&
    JSON.stringify(legacyPlan.bars) === JSON.stringify(mergeBars),
  "legacy state must run the full sweep at one measured bar snapshot",
);
const relaxedMeasurement = {
  identity: 0.54,
  perceptual: 0.71,
  evidenced: 0.42,
  temporal: 0.40,
};
const heldPlan = planConsolidationSweep(mergeBars, relaxedMeasurement);
assert(
  heldPlan.restricted &&
    JSON.stringify(heldPlan.bars) === JSON.stringify(mergeBars),
  "a relaxed measurement must hold the persisted bars for the touched-only sweep",
);
const tightenedMeasurement = {
  identity: 0.56,
  perceptual: 0.73,
  evidenced: 0.44,
  temporal: 0.42,
};
const tightenedPlan = planConsolidationSweep(mergeBars, tightenedMeasurement);
assert(
  tightenedPlan.restricted &&
    JSON.stringify(tightenedPlan.bars) === JSON.stringify(tightenedMeasurement),
  "a stricter measurement keeps every old rejection valid and may use the fast sweep",
);

// --- 3. Equivalence: the cheap path may split, it may never fuse. ------------

function atDegrees(degrees: number): number[] {
  const radians = (degrees * Math.PI) / 180;
  return [Math.cos(radians), Math.sin(radians)];
}

const face = (assetId: string, degrees: number): FaceObservation => ({
  assetId,
  embedding: atDegrees(degrees),
  embeddingKind: "identity",
});

// cos(8.1 degrees) -- faces within about eight degrees of a centroid join it.
const options = { threshold: 0.99, constraints: [] };

/** Grouping as a set of comma-joined asset lists, so ids and order cannot leak in. */
function partition(people: Person[]): string[] {
  return people
    .map((person) => [...person.assetIds].sort().join(","))
    .sort();
}

/**
 * True when every group of `candidate` sits entirely inside one group of
 * `reference` -- i.e. candidate may have SPLIT a reference person, but has
 * never put two of reference's people into one tile.
 *
 * This is the only difference direction the product can survive. Fusing a
 * parent with their child is not recoverable by any later pass; a split is
 * repaired by the next consolidation.
 */
function isRefinementOf(candidate: Person[], reference: Person[]): boolean {
  const owner = new Map<string, string>();
  for (const person of reference) {
    for (const assetId of person.assetIds) owner.set(assetId, person.id);
  }
  return candidate.every((person) => {
    const owners = new Set(person.assetIds.map((assetId) => owner.get(assetId)));
    return owners.size <= 1;
  });
}

// Three identities, well apart, plus one arriving face that belongs to the first.
const existingFaces: FaceObservation[] = [
  face("a-1", 0), face("a-2", 1), face("a-3", 2), face("a-4", 3), face("a-5", 4),
  face("b-1", 30), face("b-2", 31), face("b-3", 32), face("b-4", 33), face("b-5", 34),
  face("c-1", 60), face("c-2", 61), face("c-3", 62), face("c-4", 63), face("c-5", 64),
];
const arriving: FaceObservation[] = [face("new-1", 2.5)];
const everything = [...existingFaces, ...arriving];

// The full rebuild: what `rebuildPeople` does -- cluster every face from scratch.
const rebuilt = clusterFaces(everything, options);

// The cheap path: the settled library, then the scan's own incremental append
// (skipMerge, exactly as `appendPeople` runs it off-cadence), then the
// consolidation `consolidatePeople` runs at the end in place of the rebuild.
const settledPeople = clusterFaces(existingFaces, options);
const appended = extendFaceClusters(settledPeople, arriving, {
  ...options,
  skipMerge: true,
});
const touchedPersonId = appended.find((person) =>
  person.assetIds.includes("new-1"),
)?.id;
assert(
  touchedPersonId !== undefined,
  "VACUITY: the arriving face must actually touch a person before the restricted sweep",
);
const cheap = extendFaceClusters(appended, [], {
  ...options,
  mergeSeedPersonIds: new Set([touchedPersonId]),
});

const faceTotal = (people: Person[]): number =>
  people.reduce((sum, person) => sum + person.faceCount, 0);

assert(
  faceTotal(cheap) === everything.length,
  "the cheap path must still account for every face",
);
assert(
  isRefinementOf(cheap, rebuilt),
  "the cheap path may leave a person SPLIT, but must never fuse two of the rebuild's people",
);
assert(
  cheap.length >= rebuilt.length,
  "more tiles than the rebuild is a split; fewer would mean a fusion",
);
assert(
  JSON.stringify(partition(cheap)) === JSON.stringify(partition(rebuilt)),
  "on this small addition the two paths agree on the grouping exactly",
);

// Sabotage. `isRefinementOf` is the whole equivalence claim, so it has to be
// shown capable of failing -- a predicate that returns true for everything
// would have passed every assertion above while proving nothing at all.
const fused: Person[] = [
  {
    ...rebuilt[0],
    assetIds: [...rebuilt[0].assetIds, ...rebuilt[1].assetIds],
    faceCount: rebuilt[0].faceCount + rebuilt[1].faceCount,
  },
  ...rebuilt.slice(2),
];
assert(
  !isRefinementOf(fused, rebuilt),
  "VACUITY: a partition that fused two of the rebuild's people must FAIL the refinement check",
);
const split: Person[] = [
  { ...rebuilt[0], assetIds: rebuilt[0].assetIds.slice(0, 2), faceCount: 2 },
  {
    ...rebuilt[0],
    id: "person-split",
    assetIds: rebuilt[0].assetIds.slice(2),
    faceCount: rebuilt[0].faceCount - 2,
  },
  ...rebuilt.slice(1),
];
assert(
  isRefinementOf(split, rebuilt),
  "VACUITY: a partition that merely split one of the rebuild's people must PASS",
);

// --- 4. The cheap path must not renumber people or drop their avatars. ------
//
// `rebuildPeople` renumbers from `person-1` and re-attaches avatars by
// similarity, losing the near-ties. The cheap path never renumbers, so a person
// the user has already learned to recognise keeps both their id and their face.

const withAvatars: Person[] = settledPeople.map((person, position) => ({
  ...person,
  avatarUri: `file:///avatar-${position}.jpg`,
  avatarAssetId: person.assetIds[0],
}));
const consolidated = extendFaceClusters(withAvatars, [], options);
assert(
  consolidated.every((person) =>
    withAvatars.some((before) => before.id === person.id),
  ),
  "consolidation must not invent person ids -- a survivor keeps the id it had",
);
assert(
  consolidated.every((person) => person.avatarUri !== undefined),
  "consolidation must not drop an avatar",
);
assert(
  consolidated.length === withAvatars.length &&
    consolidated.every(
      (person, position) =>
        person.id === withAvatars[position].id &&
        person.avatarUri === withAvatars[position].avatarUri,
    ),
  "a settled library consolidates to itself, ids and avatars untouched",
);

// --- 5. A touched row must propagate through indirect merges. ---------------
//
// The arriving face moves A just far enough to merge B at the strict bar. That
// absorb raises the survivor above the four-face evidence gate, which lowers
// its bar against C and enables a SECOND merge. C was not directly touched;
// reaching it proves that an absorb keeps the survivor active and refreshes its
// entire row instead of stopping at direct neighbours of the new face.

const person = (
  id: string,
  degrees: number,
  faceCount: number,
): Person => ({
  id,
  faceCount,
  assetIds: Array.from({ length: faceCount }, (_, index) => `${id}-${index}`),
  centroid: atDegrees(degrees),
  embeddingKind: "identity",
});

const chainOptions = {
  threshold: 0.95,
  identityMergeThreshold: 0.95,
  evidencedMergeThreshold: 0.825,
  temporalMergeThreshold: 0.825,
};
const chainStart = [
  person("person-a", 0, 3),
  person("person-b", 20, 1),
  person("person-c", 39, 4),
];
const chainAppended = extendFaceClusters(
  chainStart,
  [face("chain-new", 9.4)],
  { ...chainOptions, skipMerge: true },
);
const chainFull = extendFaceClusters(chainAppended, [], chainOptions);
const chainRestricted = extendFaceClusters(chainAppended, [], {
  ...chainOptions,
  mergeSeedPersonIds: new Set(["person-a"]),
});

assert(
  chainFull.length === 1,
  "VACUITY: the full sweep fixture must actually contain the two-link merge chain",
);
assert(
  JSON.stringify(partition(chainRestricted)) === JSON.stringify(partition(chainFull)),
  "the touched-only sweep must reach the full sweep's fixed point through an indirect absorb",
);

// Sabotage the touch bookkeeping itself. Naming only C omits the row whose
// centroid moved, so the fixture must remain split; otherwise the equivalence
// assertion above could pass without the seed restriction doing real work.
const wrongSeed = extendFaceClusters(chainAppended, [], {
  ...chainOptions,
  mergeSeedPersonIds: new Set(["person-c"]),
});
assert(
  wrongSeed.length > chainFull.length &&
    JSON.stringify(partition(wrongSeed)) !== JSON.stringify(partition(chainFull)),
  "VACUITY: sabotaging the touched id must make this chain test fail",
);

console.log("face-index recluster-cost self-check passed");
