/**
 * What the USER said about who is who, outranking anything measured.
 *
 * Unsupervised face clustering has a ceiling that no threshold reaches. Two
 * relatives can sit closer than one person photographed a year apart, and no
 * single number separates those cases — the information simply is not in the
 * embeddings. A few taps supply what the statistics cannot, which is why every
 * face grouper people rate highly asks its user questions.
 *
 * Anchoring is the hard part. Person ids are rebuilt from scratch on every
 * recluster, so a constraint stored against `person-7` means nothing an hour
 * later. Embeddings are stable but are re-derived when the model changes.
 * Asset ids are the one identifier that outlives both, so a constraint is
 * stored as a pair of ANCHOR assets and re-resolved to whichever clusters now
 * hold them.
 *
 * The ambiguity that buys is a photo containing several faces: "the person in
 * asset X" is under-specified there. Anchors therefore prefer assets whose
 * cluster is the only one present in that photo, and a constraint that cannot
 * be resolved unambiguously is dropped rather than guessed.
 */

/** A user's judgement about two people. Anchors are asset ids, not person ids. */
export type FaceConstraint = {
  kind: "must" | "cannot";
  a: string;
  b: string;
};

type ConstrainedPerson = {
  id: string;
  assetIds: readonly string[];
};

/** Resolved to positions in the CURRENT people array. */
export type ResolvedConstraints = {
  must: Array<[number, number]>;
  cannot: Array<[number, number]>;
};

/**
 * Which cluster owns an anchor asset, or -1 when the answer is not unique.
 *
 * A photo of three people belongs to three clusters, so it cannot anchor a
 * statement about one of them. Rather than pick arbitrarily — which would
 * silently attach the user's correction to the wrong face — such an anchor is
 * refused.
 */
function ownerOf(
  people: readonly ConstrainedPerson[],
  assetId: string,
): number {
  let found = -1;
  for (let index = 0; index < people.length; index += 1) {
    if (!people[index].assetIds.includes(assetId)) continue;
    if (found !== -1) return -1;
    found = index;
  }
  return found;
}

export function resolveConstraints(
  people: readonly ConstrainedPerson[],
  constraints: readonly FaceConstraint[],
): ResolvedConstraints {
  const resolved: ResolvedConstraints = { must: [], cannot: [] };
  for (const constraint of constraints) {
    const a = ownerOf(people, constraint.a);
    const b = ownerOf(people, constraint.b);
    // -1 is unresolvable; a === b is already satisfied for `must` and is a
    // contradiction for `cannot` that the user can only fix by splitting.
    if (a === -1 || b === -1 || a === b) continue;
    (constraint.kind === "must" ? resolved.must : resolved.cannot).push([a, b]);
  }
  return resolved;
}

/**
 * Picks the asset that best identifies a person for use as an anchor.
 *
 * Prefers an asset no other cluster claims, so the resulting constraint is
 * resolvable later. Returns undefined when every one of this person's photos
 * also contains somebody else, in which case no honest anchor exists.
 */
export function anchorAssetFor(
  people: readonly ConstrainedPerson[],
  personId: string,
): string | undefined {
  const person = people.find((candidate) => candidate.id === personId);
  if (!person) return undefined;
  for (const assetId of person.assetIds) {
    if (ownerOf(people, assetId) !== -1) return assetId;
  }
  return undefined;
}

/** Drops constraints that name assets no longer in the library. */
export function pruneConstraints(
  constraints: readonly FaceConstraint[],
  knownAssetIds: ReadonlySet<string>,
): FaceConstraint[] {
  return constraints.filter(
    (constraint) =>
      knownAssetIds.has(constraint.a) && knownAssetIds.has(constraint.b),
  );
}

export function isFaceConstraint(value: unknown): value is FaceConstraint {
  if (typeof value !== "object" || value === null) return false;
  const candidate = value as Partial<FaceConstraint>;
  return (
    (candidate.kind === "must" || candidate.kind === "cannot") &&
    typeof candidate.a === "string" &&
    typeof candidate.b === "string"
  );
}
