// @ts-expect-error Node's TypeScript runner requires the source extension.
import { __constraintStorageForTest as storage, __observationsFileForTest as file, clearFaceConstraints, faceConstraintCount, markSamePerson } from "./face-index.ts";
import type { FaceObservation, Person } from "./types.ts";

function assert(value: unknown, message: string): asserts value {
  if (!value) throw new Error(`anchor-record self-check failed: ${message}`);
}

/**
 * The whole promise, end to end: answering "these two are the same person"
 * about somebody who is never photographed alone.
 *
 * The pure tests in face-constraints.test.ts prove the anchoring rules. This
 * one proves they are actually WIRED: that `recordConstraint` finds the faces
 * in the observations file, stores an anchor that names one of them, and that
 * the answer survives the full recluster which renumbers every person id.
 * Every earlier version of this feature refused this exact case, and the
 * refusal was invisible from anywhere except the phone.
 */

const SIZE = 6;
const direction = (axis: number): number[] =>
  Array.from({ length: SIZE }, (_unused, index) => (index === axis ? 1 : 0));

function unit(values: number[]): number[] {
  const magnitude = Math.sqrt(values.reduce((sum, value) => sum + value * value, 0));
  return values.map((value) => value / magnitude);
}

/** `base` rotated toward an independent direction: two years of appearance. */
function drifted(base: number[], drift: number[], amount: number): number[] {
  return unit(base.map((value, axis) => value + amount * drift[axis]));
}

/** A near-repeat of one face, close enough to join its own cluster. */
function again(face: number[]): number[] {
  return unit(face.map((value, axis) => value + (axis === SIZE - 1 ? 0.02 : 0)));
}

const MOTHER = direction(0);
const BABY = direction(1);
// cos = (1 - t^2) / (1 + t^2) = 0.30: below any bar that would group them.
const DRIFT = 0.7338;

const motherEarly = drifted(MOTHER, direction(2), DRIFT);
const motherLate = drifted(MOTHER, direction(2), -DRIFT);
const babyEarly = drifted(BABY, direction(3), DRIFT);
const babyLate = drifted(BABY, direction(3), -DRIFT);

const face = (embedding: number[], assetId: string, capturedAt: number): FaceObservation => ({
  assetId,
  embedding,
  embeddingKind: "identity",
  seedable: true,
  capturedAt,
});

// A library where NOBODY is ever photographed alone, and where each person is
// identifiable by their photos alone so the test never has to guess who is who.
const library: FaceObservation[] = [
  face(motherEarly, "newborn", 1_700_000_000_000),
  face(babyEarly, "newborn", 1_700_000_000_000),
  face(again(motherEarly), "park", 1_700_000_100_000),
  face(direction(4), "park", 1_700_000_100_000),
  face(motherLate, "birthday", 1_760_000_000_000),
  face(babyLate, "birthday", 1_760_000_000_000),
  face(again(babyLate), "creche", 1_760_000_100_000),
  face(direction(5), "creche", 1_760_000_100_000),
];

file.setObservations(library);
file.setLoaded(true);
file.setDirty(false);
file.rebuild();

const withAssets = (people: readonly Person[], assetIds: string[]): Person =>
  people.find(
    (person) =>
      person.assetIds.length === assetIds.length &&
      assetIds.every((assetId) => person.assetIds.includes(assetId)),
  ) as Person;

{
  const people = file.people();
  assert(people.length === 6, `the fixture must split the mother in two (got ${people.length})`);
  assert(
    people.every((person) =>
      person.assetIds.every(
        (assetId) =>
          people.filter((other) => other.assetIds.includes(assetId)).length > 1,
      ),
    ),
    "the fixture is only interesting while every photo holds two people",
  );
}

const early = withAssets(file.people(), ["newborn", "park"]);
const late = withAssets(file.people(), ["birthday"]);
assert(early !== undefined && late !== undefined, "both halves of the mother must be findable");

// ---------------------------------------------------------------------------
// The answer is accepted, stored against a FACE, and applied immediately.
// ---------------------------------------------------------------------------
const recorded = await markSamePerson(early.id, late.id);
assert(recorded, "a person with no unshared photo must still be answerable");
assert(faceConstraintCount() === 1, "the answer has to be stored, not just applied");

const [constraint] = storage.current();
assert(
  constraint.aFace !== undefined && constraint.bFace !== undefined,
  "both anchors sit in shared photos, so both must name their face",
);
assert(
  constraint.aFace.length === SIZE && constraint.bFace.length === SIZE,
  "an anchor face is a whole embedding",
);

{
  const joined = withAssets(file.people(), ["newborn", "park", "birthday"]);
  assert(joined !== undefined, "the merge must be applied at once, not at the next rebuild");
  assert(joined.faceCount === 3, `the mother's three faces are now one person (got ${joined?.faceCount})`);
}

// ---------------------------------------------------------------------------
// And it survives the rebuild that renumbers every id -- the thing person-id
// anchoring could never do, and the reason any of this is stored as anchors.
// ---------------------------------------------------------------------------
file.rebuild();
{
  const people = file.people();
  assert(people.length === 5, `the constraint must re-apply on a rebuild (got ${people.length})`);
  const joined = withAssets(people, ["newborn", "park", "birthday"]);
  assert(joined !== undefined && joined.faceCount === 3, "the mother stays whole across a recluster");
  assert(
    people.some((person) => person.id === "person-1"),
    "a rebuild renumbers from person-1, so the ids the answer was given about are gone",
  );
}

// ---------------------------------------------------------------------------
// Vacuity guard. If the constraint were being silently ignored, the rebuild
// above would have produced six people again -- so forget it and check that
// six is exactly what comes back.
// ---------------------------------------------------------------------------
await clearFaceConstraints();
{
  const people = file.people();
  assert(faceConstraintCount() === 0, "clearing forgets the answer");
  assert(
    people.length === 6,
    `without the answer the mother splits again (got ${people.length}) -- if this is 5, ` +
      "the merge above was the clusterer's doing and the constraint proved nothing",
  );
}

// eslint-disable-next-line no-console
console.log("anchor-record self-check passed");
