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
 * asset X" is under-specified there. So an anchor prefers an asset whose
 * cluster is the only one present in that photo, and when the anchor names a
 * shared photo it must also name WHICH FACE in it — see `FaceConstraint.aFace`.
 * An anchor that cannot be resolved to exactly one cluster is dropped rather
 * than guessed, in both forms.
 *
 * Why the face has to be carried as an embedding rather than as anything
 * cheaper: the target case is a person who appears ONLY alongside the same
 * people — a parent who is never photographed without the baby. No description
 * built from asset ids can separate those two, because their asset sets are
 * identical. The embedding is the only thing that distinguishes them, so it is
 * what gets stored.
 */

/** A user's judgement about two people. Anchors are asset ids, not person ids. */
export type FaceConstraint = {
  kind: "must" | "cannot";
  a: string;
  b: string;
  /**
   * The anchored FACE inside photo `a`, when that photo holds several.
   *
   * Absent means "this photo has only one cluster in it", which is how every
   * constraint written before face anchors existed is shaped — those keep
   * resolving exactly as they did, see `ownerOf`.
   */
  aFace?: number[];
  /** The anchored face inside photo `b`. */
  bFace?: number[];
};

/** One anchor: a photo, and which face in it when that is not obvious. */
export type FaceAnchor = { assetId: string; face?: number[] };

/**
 * The bars a face must clear to belong to a cluster, from `mergeBars`.
 *
 * Passed in rather than redeclared here because they are calibrated per
 * library and a second copy of a threshold in this codebase has already gone
 * stale once. Resolution uses the ASSIGNMENT bar, since the question it asks —
 * "is this face one of this cluster's faces?" — is the question assignment
 * answers, not the tighter one merging answers.
 */
export type AnchorBars = {
  assignment: number;
  perceptual: number;
};

/**
 * How far the best claimant must beat the runner-up before an anchor resolves.
 *
 * A photo of two sisters is exactly the case where a wrong answer is worst, so
 * the runner-up being anywhere near as good as the winner means the anchor is
 * refused, not guessed.
 *
 * Measured rather than picked. scratch/face-anchor-coverage builds a 17,766-
 * face family library, clusters it with the shipped policy, and takes ground
 * truth from `onAssign` -- which cluster each face actually landed in. Its
 * cast is drawn around a shared family direction so relatives are genuinely
 * confusable: at the hardest setting the different-person mean is 0.245 and
 * the worst impostor pair 0.642, harder than the owner's own library, where
 * 4.1% of different-person pairs beat 0.20. On that fixture:
 *
 *   - every anchor this rule accepts resolves to the right person, and the
 *     tightest margin any correct decision needed was 0.306;
 *   - the choices a rule with NO bar and NO margin gets wrong are near-ties --
 *     the most confident wrong choice was decisive by 0.016.
 *
 * The two bands are an order of magnitude apart, and 0.15 sits between them:
 * it refuses every error measured while costing no coverage at all (100% of
 * people stayed anchorable, because a near-tie in one photo simply moves the
 * anchor to the next photo). It is not tuned to the edge of either band, which
 * is the point -- the space between them is the safety, not the constant.
 */
export const MIN_ANCHOR_MARGIN = 0.15;

type ConstrainedPerson = {
  id: string;
  assetIds: readonly string[];
  /** Needed only to resolve a face anchor; people without one still work. */
  centroid?: readonly number[];
  embeddingKind?: "identity" | "perceptual";
};

/** Resolved to positions in the CURRENT people array. */
export type ResolvedConstraints = {
  must: Array<[number, number]>;
  cannot: Array<[number, number]>;
};

/**
 * Average linkage between one face and one cluster.
 *
 * The same quantity `scaledSimilarity` computes in face-cluster.ts — a dot
 * product divided by max(1,|v|) on each side, which for a unit face and a
 * centroid of unit vectors is the MEAN cosine between the face and every face
 * in the cluster. Duplicated rather than imported because face-cluster.ts
 * imports this module, and closing that cycle to share nine lines of
 * arithmetic would be the more expensive mistake. It must stay in step with
 * that function; face-constraints.test.ts asserts the two agree.
 */
function linkage(
  face: readonly number[],
  centroid: readonly number[] | undefined,
): number {
  if (!centroid || centroid.length === 0 || centroid.length !== face.length) {
    return Number.NEGATIVE_INFINITY;
  }
  let dot = 0;
  let faceSquared = 0;
  let centroidSquared = 0;
  for (let axis = 0; axis < face.length; axis += 1) {
    const left = face[axis];
    const right = centroid[axis];
    if (!Number.isFinite(left) || !Number.isFinite(right)) {
      return Number.NEGATIVE_INFINITY;
    }
    dot += left * right;
    faceSquared += left * left;
    centroidSquared += right * right;
  }
  const scale =
    Math.max(1, Math.sqrt(faceSquared)) * Math.max(1, Math.sqrt(centroidSquared));
  return scale > 0 ? dot / scale : Number.NEGATIVE_INFINITY;
}

function barFor(person: ConstrainedPerson, bars: AnchorBars): number {
  return person.embeddingKind === "perceptual" ? bars.perceptual : bars.assignment;
}

/**
 * Which of a photo's clusters holds this particular face, or -1 when the
 * answer is not decisive.
 *
 * Two guards, and both are load-bearing:
 *
 *   - the winner must clear the bar a face has to clear to JOIN a cluster. A
 *     face whose own cluster no longer exists (its only member was a
 *     low-quality face that seeds nothing) would otherwise be handed to
 *     whichever stranger in the photo happened to be nearest.
 *   - the winner must beat the runner-up by `MIN_ANCHOR_MARGIN`. Two relatives
 *     in one frame is the case this whole module exists for and the case a
 *     coin-flip would ruin, so a near-tie declines.
 */
function decisiveOwner(
  people: readonly ConstrainedPerson[],
  claimants: readonly number[],
  face: readonly number[],
  bars: AnchorBars,
): number {
  let best = -1;
  let bestScore = Number.NEGATIVE_INFINITY;
  let runnerUp = Number.NEGATIVE_INFINITY;
  for (const index of claimants) {
    const score = linkage(face, people[index].centroid);
    if (score > bestScore) {
      runnerUp = bestScore;
      bestScore = score;
      best = index;
    } else if (score > runnerUp) {
      runnerUp = score;
    }
  }
  if (best === -1 || bestScore < barFor(people[best], bars)) return -1;
  if (runnerUp > Number.NEGATIVE_INFINITY && bestScore - runnerUp < MIN_ANCHOR_MARGIN) {
    return -1;
  }
  return best;
}

/**
 * Which cluster owns an anchor, or -1 when the answer is not unique.
 *
 * A photo of three people belongs to three clusters. When the anchor names
 * which of those faces it means, that is answered by comparing the face to
 * each of them; when it does not — every constraint stored before face anchors
 * existed — the anchor is refused exactly as it always was. Rather than pick
 * arbitrarily, which would silently attach the user's correction to the wrong
 * face, such an anchor is dropped.
 */
function ownerOf(
  people: readonly ConstrainedPerson[],
  anchor: FaceAnchor,
  bars: AnchorBars,
): number {
  const claimants: number[] = [];
  for (let index = 0; index < people.length; index += 1) {
    if (people[index].assetIds.includes(anchor.assetId)) claimants.push(index);
  }
  if (claimants.length === 0) return -1;
  // One claimant needs no face: the anchored face is in this photo, and only
  // this cluster has a face in this photo.
  if (claimants.length === 1) return claimants[0];
  if (!anchor.face) return -1;
  return decisiveOwner(people, claimants, anchor.face, bars);
}

export function resolveConstraints(
  people: readonly ConstrainedPerson[],
  constraints: readonly FaceConstraint[],
  bars: AnchorBars,
): ResolvedConstraints {
  const resolved: ResolvedConstraints = { must: [], cannot: [] };
  for (const constraint of constraints) {
    const a = ownerOf(people, { assetId: constraint.a, face: constraint.aFace }, bars);
    const b = ownerOf(people, { assetId: constraint.b, face: constraint.bFace }, bars);
    // -1 is unresolvable; a === b is already satisfied for `must` and is a
    // contradiction for `cannot` that the user can only fix by splitting.
    if (a === -1 || b === -1 || a === b) continue;
    (constraint.kind === "must" ? resolved.must : resolved.cannot).push([a, b]);
  }
  return resolved;
}

/**
 * Picks the anchor that best identifies a person, or nothing when none does.
 *
 * Two chances, in order of how little they can go wrong:
 *
 *   1. A photo no other cluster claims. Nothing to confuse, nothing to store
 *      beyond the asset id — this is what shipped before and it stays first.
 *   2. One face inside a shared photo. Used only when every photo this person
 *      appears in also holds somebody else, which is the position of anyone
 *      photographed only alongside others.
 *
 * The second form is accepted only when the choice is decisive in BOTH
 * directions: the face must be this person's best face in that photo by
 * `MIN_ANCHOR_MARGIN`, and re-resolving the anchor through `ownerOf` — the
 * exact call a future recluster will make — must hand it back to this same
 * person. An anchor that does not survive its own resolver is never stored.
 *
 * `facesInAsset` supplies the faces detected in one photo. It is optional
 * because the embeddings live in a 13.8MB file that is loaded on demand: a
 * caller can ask for the cheap answer first and only pay the load when this
 * returns undefined without it.
 */
export function anchorFor(
  people: readonly ConstrainedPerson[],
  personId: string,
  bars: AnchorBars,
  facesInAsset?: (assetId: string) => readonly (readonly number[])[],
): FaceAnchor | undefined {
  const personIndex = people.findIndex((candidate) => candidate.id === personId);
  if (personIndex === -1) return undefined;
  const person = people[personIndex];
  for (const assetId of person.assetIds) {
    if (ownerOf(people, { assetId }, bars) !== -1) return { assetId };
  }
  if (!facesInAsset) return undefined;
  for (const assetId of person.assetIds) {
    let best: readonly number[] | undefined;
    let bestScore = Number.NEGATIVE_INFINITY;
    let runnerUp = Number.NEGATIVE_INFINITY;
    for (const face of facesInAsset(assetId)) {
      const score = linkage(face, person.centroid);
      if (score > bestScore) {
        runnerUp = bestScore;
        bestScore = score;
        best = face;
      } else if (score > runnerUp) {
        runnerUp = score;
      }
    }
    if (!best || bestScore < barFor(person, bars)) continue;
    // Two faces in this photo that both look like this person: one of them is
    // somebody else, and nothing here can say which. Next photo.
    if (
      runnerUp > Number.NEGATIVE_INFINITY &&
      bestScore - runnerUp < MIN_ANCHOR_MARGIN
    ) {
      continue;
    }
    const face = [...best];
    if (ownerOf(people, { assetId, face }, bars) !== personIndex) continue;
    return { assetId, face };
  }
  return undefined;
}

/** Whether two anchors name the same face in the same photo. */
export function sameAnchor(left: FaceAnchor, right: FaceAnchor): boolean {
  if (left.assetId !== right.assetId) return false;
  if (!left.face || !right.face) return !left.face && !right.face;
  // Exact equality is safe across a save and load: a stored embedding is int8,
  // and dequantize-then-quantize is the identity on every byte this codebase
  // can write (`quantizeEmbedding` never emits 0x80).
  return (
    left.face.length === right.face.length &&
    left.face.every((value, axis) => value === (right.face as number[])[axis])
  );
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
