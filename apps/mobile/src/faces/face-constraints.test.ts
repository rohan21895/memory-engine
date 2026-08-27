// @ts-expect-error TypeScript bundler resolution normally omits source extensions.
import { MIN_ANCHOR_MARGIN, anchorFor, pruneConstraints, resolveConstraints, sameAnchor, type FaceConstraint } from "./face-constraints.ts";
// @ts-expect-error TypeScript bundler resolution normally omits source extensions.
import { DEFAULT_IDENTITY_THRESHOLD, DEFAULT_MERGE_THRESHOLD, DEFAULT_PERCEPTUAL_THRESHOLD, cosine, extendFaceClusters } from "./face-cluster.ts";
import type { FaceObservation, Person } from "./types";

function assert(value: unknown, message: string): asserts value {
  if (!value) throw new Error(`face-constraints self-check failed: ${message}`);
}

const bars = {
  assignment: DEFAULT_IDENTITY_THRESHOLD,
  perceptual: DEFAULT_PERCEPTUAL_THRESHOLD,
};

function unit(values: number[]): number[] {
  const magnitude = Math.sqrt(values.reduce((sum, value) => sum + value * value, 0));
  return values.map((value) => value / magnitude);
}

/** A unit vector whose cosine against MOTHER is exactly `similarity`. */
function near(similarity: number): number[] {
  return unit([similarity, Math.sqrt(1 - similarity * similarity), 0, 0]);
}

/** `base` rotated `amount` of the way toward an independent direction. */
function drifted(base: number[], direction: number[], amount: number): number[] {
  return unit(base.map((value, axis) => value + amount * direction[axis]));
}

const identity = (embedding: number[], assetId: string): FaceObservation => ({
  assetId,
  embedding,
  embeddingKind: "identity",
});

// ---------------------------------------------------------------------------
// 0. THE FIXTURE, and the reason this file changed.
//
// A mother and her baby, photographed together twice and never apart. Four
// independent directions: one per person, one per person's appearance drift.
// The two shots are far enough apart in the mother's appearance that the
// clusterer splits her in two -- which is exactly the pair the merge review
// exists to ask about.
//
// Every assertion below depends on this library having NO unshared photo, so
// that is asserted first rather than assumed. If a later change gives anybody
// a solo shot, the anchoring tests would start passing through the old path
// and would be proving nothing.
// ---------------------------------------------------------------------------

const MOTHER = [1, 0, 0, 0];
const BABY = [0, 1, 0, 0];
const MOTHER_DRIFT = [0, 0, 1, 0];
const BABY_DRIFT = [0, 0, 0, 1];
// cos = (1 - t^2) / (1 + t^2) = 0.30 at t = 0.7338: two shots of one person a
// year apart, below the bar a face has to clear to join her own cluster.
const DRIFT = 0.7338;

const motherAtBirth = drifted(MOTHER, MOTHER_DRIFT, DRIFT);
const motherAtTwo = drifted(MOTHER, MOTHER_DRIFT, -DRIFT);
const babyAtBirth = drifted(BABY, BABY_DRIFT, DRIFT);
const babyAtTwo = drifted(BABY, BABY_DRIFT, -DRIFT);

assert(
  cosine(motherAtBirth, motherAtTwo) < DEFAULT_IDENTITY_THRESHOLD,
  "the two shots of the mother must be too far apart to cluster on their own",
);
assert(
  Math.abs(cosine(motherAtBirth, babyAtBirth)) < 1e-9,
  "mother and baby must be independent directions",
);

const library: FaceObservation[] = [
  identity(motherAtBirth, "newborn"),
  identity(babyAtBirth, "newborn"),
  identity(motherAtTwo, "birthday"),
  identity(babyAtTwo, "birthday"),
];

const facesIn = (assetId: string): number[][] =>
  library.filter((face) => face.assetId === assetId).map((face) => face.embedding);

const clusterOptions = {
  threshold: DEFAULT_IDENTITY_THRESHOLD,
  identityMergeThreshold: DEFAULT_MERGE_THRESHOLD,
  perceptualThreshold: DEFAULT_PERCEPTUAL_THRESHOLD,
};

const split = extendFaceClusters([], library, clusterOptions);
assert(split.length === 4, `the fixture must split both people (got ${split.length})`);
assert(
  split.every((person) => person.assetIds.length === 1),
  "every cluster here lives in exactly one photo",
);
// The old rule, run without face lookup: nobody in this library can be anchored.
assert(
  split.every((person) => anchorFor(split, person.id, bars) === undefined),
  "the fixture is only interesting because a photo-only anchor refuses everyone",
);

const motherFragments = split.filter(
  (person) => cosine(person.centroid, MOTHER) > cosine(person.centroid, BABY),
);
assert(motherFragments.length === 2, "the mother must be the split one");

// ---------------------------------------------------------------------------
// 1. ANCHORING A FACE. With the photo's faces available, each fragment is
//    anchored on the mother's face rather than on the photo, and the anchor
//    resolves back to the fragment that owns it.
// ---------------------------------------------------------------------------

const [firstFragment, secondFragment] = motherFragments;
const anchorA = anchorFor(split, firstFragment.id, bars, facesIn);
const anchorB = anchorFor(split, secondFragment.id, bars, facesIn);
assert(anchorA !== undefined && anchorB !== undefined, "a shared photo must still yield an anchor");
assert(anchorA.face !== undefined && anchorB.face !== undefined, "the anchor has to name WHICH face");
assert(
  cosine(anchorA.face, firstFragment.centroid) > 0.99 &&
    cosine(anchorB.face, secondFragment.centroid) > 0.99,
  "the anchored face must be the person's own face, not their co-subject's",
);

const mustLink: FaceConstraint = {
  kind: "must",
  a: anchorA.assetId,
  b: anchorB.assetId,
  aFace: anchorA.face,
  bFace: anchorB.face,
};

{
  const resolved = resolveConstraints(split, [mustLink], bars);
  assert(resolved.must.length === 1, "the anchor must resolve against the people it came from");
  const [left, right] = resolved.must[0];
  assert(
    split[left].id === firstFragment.id && split[right].id === secondFragment.id,
    "an anchor must resolve to the person it was taken from",
  );
}

// ---------------------------------------------------------------------------
// 2. SURVIVING A RECLUSTER. Person ids are renumbered from person-1 every time,
//    so the same faces arriving in a different order produce different ids for
//    the same people. The constraint has to follow the FACES.
// ---------------------------------------------------------------------------
{
  const shuffled = extendFaceClusters(
    [],
    [library[1], library[2], library[3], library[0]],
    clusterOptions,
  );
  assert(shuffled.length === 4, "the shuffled library still splits four ways");
  const holderOf = (people: Person[], face: number[]): Person => {
    const found = people.find((person) => cosine(person.centroid, face) > 0.99);
    assert(found !== undefined, "every face must end up in some cluster");
    return found;
  };
  assert(
    holderOf(shuffled, motherAtBirth).id !== holderOf(split, motherAtBirth).id,
    "the shuffle has to actually renumber, or this proves nothing",
  );
  const resolved = resolveConstraints(shuffled, [mustLink], bars);
  assert(resolved.must.length === 1, "a face anchor survives renumbering");
  const [left, right] = resolved.must[0];
  assert(
    cosine(shuffled[left].centroid, motherAtBirth) > 0.99 &&
      cosine(shuffled[right].centroid, motherAtTwo) > 0.99,
    "the constraint must land on the MOTHER's clusters, not on the baby's",
  );
}

// ---------------------------------------------------------------------------
// 3. IT ACTUALLY MERGES -- and the same call without the constraint does not.
//    A test that only asserted "four clusters became three" would pass just as
//    happily if the constraint were dropped and something else merged, and a
//    test that only ran the constrained case would pass if the clusterer
//    merged them anyway.
// ---------------------------------------------------------------------------
{
  const forced = extendFaceClusters([], library, {
    ...clusterOptions,
    constraints: [mustLink],
  });
  const unforced = extendFaceClusters([], library, clusterOptions);
  assert(unforced.length === 4, "without the user's answer these stay four people");
  assert(forced.length === 3, `the must-link has to join the mother (got ${forced.length})`);
  const joined = forced.find((person) => person.assetIds.length === 2);
  assert(joined !== undefined, "one cluster must now hold both photos");
  assert(
    joined.faceCount === 2 && cosine(joined.centroid, MOTHER) > cosine(joined.centroid, BABY),
    "the joined cluster must be the mother's two faces",
  );
}

// ---------------------------------------------------------------------------
// 4. A CANNOT-LINK, anchored the same way, blocks a merge the bars would make.
// ---------------------------------------------------------------------------
{
  const guest = unit([DEFAULT_MERGE_THRESHOLD + 0.1, Math.sqrt(1 - (DEFAULT_MERGE_THRESHOLD + 0.1) ** 2), 0, 0]);
  const twins = (constraints: FaceConstraint[]): Person[] =>
    extendFaceClusters(
      [
        { id: "person-1", faceCount: 6, assetIds: ["party"], centroid: MOTHER, embeddingKind: "identity" },
        { id: "person-2", faceCount: 6, assetIds: ["dinner"], centroid: guest, embeddingKind: "identity" },
        { id: "person-3", faceCount: 6, assetIds: ["party"], centroid: MOTHER_DRIFT, embeddingKind: "identity" },
        { id: "person-4", faceCount: 6, assetIds: ["dinner"], centroid: BABY_DRIFT, embeddingKind: "identity" },
      ],
      [],
      { ...clusterOptions, constraints },
    );
  assert(
    cosine(MOTHER, guest) > DEFAULT_MERGE_THRESHOLD,
    "the fixture only tests anything if these two would otherwise merge",
  );
  assert(twins([]).length === 3, "unconstrained, the bars merge this pair");
  const kept = twins([
    { kind: "cannot", a: "party", b: "dinner", aFace: MOTHER, bFace: guest },
  ]);
  assert(kept.length === 4, `a face-anchored cannot-link must block the merge (got ${kept.length})`);
}

// ---------------------------------------------------------------------------
// 5. A NEAR TIE DECLINES. Two relatives in one frame is the case a wrong
//    answer ruins, so the margin is enforced in both directions: choosing a
//    face for a person, and choosing a person for a face.
// ---------------------------------------------------------------------------
{
  const sisterFace = MOTHER;
  const clear = DEFAULT_IDENTITY_THRESHOLD + 2 * MIN_ANCHOR_MARGIN;
  const tie: Person[] = [
    { id: "sister", faceCount: 4, assetIds: ["reunion"], centroid: near(clear), embeddingKind: "identity" },
    // Close enough behind that nothing here can say which of them owns the face.
    { id: "cousin", faceCount: 4, assetIds: ["reunion"], centroid: near(clear - MIN_ANCHOR_MARGIN / 2), embeddingKind: "identity" },
  ];
  const constraint: FaceConstraint = {
    kind: "must",
    a: "reunion",
    b: "reunion",
    aFace: sisterFace,
    bFace: sisterFace,
  };
  assert(
    resolveConstraints(tie, [constraint], bars).must.length === 0,
    "a face two clusters claim equally must not resolve",
  );
  assert(
    anchorFor(tie, "sister", bars, () => [sisterFace]) === undefined,
    "a face two clusters claim equally must not be stored as an anchor either",
  );

  // The same shape, one margin wider, does resolve -- so the refusal above is
  // the margin talking and not a fixture that could never work.
  const decided: Person[] = [
    tie[0],
    { ...tie[1], centroid: near(clear - 2 * MIN_ANCHOR_MARGIN) },
  ];
  const anchor = anchorFor(decided, "sister", bars, () => [sisterFace]);
  assert(anchor?.face !== undefined, "a decisive winner still anchors");
}

// ---------------------------------------------------------------------------
// 6. NEVER THE WRONG FACE. The anchored face's own cluster can disappear -- a
//    low-quality face that seeds nothing leaves no cluster behind. What must
//    not happen is the correction being handed to whoever else is in the
//    photo. The bar, not the margin, is what stops that: these two strangers
//    are far apart, so a margin-only rule would have declared a winner.
// ---------------------------------------------------------------------------
{
  const orphan = MOTHER;
  const strangers: Person[] = [
    { id: "stranger", faceCount: 9, assetIds: ["party"], centroid: near(DEFAULT_IDENTITY_THRESHOLD - 0.09), embeddingKind: "identity" },
    { id: "other", faceCount: 9, assetIds: ["party"], centroid: BABY_DRIFT, embeddingKind: "identity" },
    // The other end of the constraint, and deliberately resolvable: if this
    // side failed too, the assertion below would pass for the wrong reason.
    { id: "elsewhere", faceCount: 9, assetIds: ["solo"], centroid: BABY, embeddingKind: "identity" },
  ];
  assert(
    resolveConstraints(
      strangers,
      [{ kind: "must", a: "solo", b: "party", bFace: strangers[0].centroid }],
      bars,
    ).must.length === 1,
    "the control: an anchor whose owner IS present resolves",
  );
  const best = Math.max(...strangers.map((person) => cosine(orphan, person.centroid)));
  const runnerUp = Math.min(...strangers.map((person) => cosine(orphan, person.centroid)));
  assert(best < DEFAULT_IDENTITY_THRESHOLD, "the fixture's best claimant must be under the bar");
  assert(
    best - runnerUp > MIN_ANCHOR_MARGIN,
    "and must beat the runner-up, or the margin would be doing the work",
  );
  assert(
    resolveConstraints(
      strangers,
      [{ kind: "must", a: "solo", b: "party", bFace: orphan }],
      bars,
    ).must.length === 0,
    "an anchor whose owner is gone must be dropped, never reattached",
  );
}

// ---------------------------------------------------------------------------
// 7. MIGRATION. Constraints written before face anchors carry no face at all.
//    They must behave EXACTLY as they did: resolved where the photo has one
//    cluster, dropped where it has several. This is the original fixture and
//    the original expectations, unchanged.
// ---------------------------------------------------------------------------
{
  const people = [
    { id: "p1", assetIds: ["solo-a", "group"] },
    { id: "p2", assetIds: ["solo-b", "group"] },
    { id: "p3", assetIds: ["solo-c"] },
  ];

  assert(anchorFor(people, "p1", bars)?.assetId === "solo-a", "anchor must be the unshared photo");
  assert(anchorFor(people, "p2", bars)?.assetId === "solo-b", "anchor must be the unshared photo");
  assert(
    anchorFor(people, "p1", bars)?.face === undefined,
    "an unshared photo needs no face, and must not grow the stored index with one",
  );
  assert(
    anchorFor([{ id: "only", assetIds: ["group"] }, { id: "other", assetIds: ["group"] }], "only", bars) === undefined,
    "without the photo's faces, a person whose every photo is shared still has no anchor",
  );

  const resolved = resolveConstraints(
    people,
    [
      { kind: "must", a: "solo-a", b: "solo-c" },
      { kind: "cannot", a: "solo-b", b: "solo-c" },
    ],
    bars,
  );
  assert(resolved.must.length === 1 && resolved.must[0][0] === 0 && resolved.must[0][1] === 2, "must-link resolves to indices");
  assert(resolved.cannot.length === 1 && resolved.cannot[0][0] === 1, "cannot-link resolves to indices");

  const dropped = resolveConstraints(
    people,
    [
      { kind: "must", a: "group", b: "solo-c" },
      { kind: "must", a: "solo-a", b: "solo-a" },
      { kind: "cannot", a: "solo-a", b: "missing" },
    ],
    bars,
  );
  assert(
    dropped.must.length === 0 && dropped.cannot.length === 0,
    "a faceless anchor on a shared photo is still ambiguous, and still dropped",
  );

  const kept = pruneConstraints(
    [{ kind: "must", a: "solo-a", b: "solo-c" }, { kind: "must", a: "solo-a", b: "gone" }],
    new Set(["solo-a", "solo-c"]),
  );
  assert(kept.length === 1, `only resolvable constraints survive (got ${kept.length})`);
}

// ---------------------------------------------------------------------------
// 8. TWO FACES IN ONE PHOTO ARE TWO ANCHORS. `recordConstraint` replaces an
//    earlier judgement about the same pair by comparing anchors; if that
//    comparison ignored the face, answering about one couple in a photo would
//    silently delete the answer about another.
// ---------------------------------------------------------------------------
{
  assert(sameAnchor({ assetId: "g" }, { assetId: "g" }), "the same photo, no faces, is one anchor");
  assert(!sameAnchor({ assetId: "g" }, { assetId: "h" }), "different photos are different anchors");
  assert(
    !sameAnchor({ assetId: "g", face: MOTHER }, { assetId: "g", face: BABY }),
    "two faces in one photo are two different anchors",
  );
  assert(
    !sameAnchor({ assetId: "g", face: MOTHER }, { assetId: "g" }),
    "a face anchor is not the same as the photo it sits in",
  );
  assert(
    sameAnchor({ assetId: "g", face: [...MOTHER] }, { assetId: "g", face: [...MOTHER] }),
    "the same face in the same photo is one anchor",
  );
}

// eslint-disable-next-line no-console
console.log("face-constraints self-check passed");
