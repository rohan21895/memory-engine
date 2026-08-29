/**
 * Phone port of album-engine/selection.py's coverage objective.
 *
 * The planner deliberately knows nothing about React Native or model loading.
 * It consumes plain, already-measured signals and returns stable media ids, so
 * Phase 2 models can improve the evidence without changing the decision rule.
 *
 * Two decision rules live here, chosen by `PlannerPolicy.selector`:
 *   - `coverage-keys` (default, shipped): greedy on a per-photo score plus a
 *     0.5^count bonus for each discrete key — time bucket, place, moment, pose,
 *     person — with an MMR redundancy penalty.
 *   - `submodular` (M6): the same gates and caps, but the pick is the argmax of
 *     a monotone submodular F(S) = quality + facility location + saturating
 *     coverage, maximized by lazy greedy plus a bounded swap pass.
 * Everything either side of the pick — gates, rescues, rare-moment and
 * scarce-person waivers, chronological ordering, reasons — is shared, so an A/B
 * between them isolates the decision rule.
 */

// @ts-expect-error Node requires the extension; Metro resolves this path too.
import { COVERAGE_SATURATION, applyPick, emptyState, gainBreakdown, lazyGreedy, objectiveValue, validateProblem } from "./album-objective.ts";
import type { CoverageCategory, SubmodularProblem } from "./album-objective";

const BUCKET_DECAY = 0.5;
const UNKNOWN = "unknown";
const NO_PEOPLE = "";
const STANDING_PRIOR = 4;

export type PlannerCandidate = {
  mediaId: string;
  quality: number;
  capturedAt?: number;
  placeKey?: string;
  personIds?: readonly string[];
  embedding?: readonly number[];
  embeddingSpace?: string;
  /**
   * TinyCLIP's image embedding, kept SEPARATE from `embedding`.
   *
   * `embedding` is the phone's 76-dim perceptual fingerprint -- an 8x8 luma grid
   * and a coarse colour histogram. That is a near-duplicate detector: it answers
   * "is this the same frame twice", and it is what the calibrated 0.93 shot
   * threshold is calibrated against. It cannot answer "same person, same outfit,
   * same room, ten minutes apart", because at 8x8 those two photos are simply
   * two different arrangements of light.
   *
   * TinyCLIP can, which is why the owner asked for "background detection,
   * clothes detection" by name. It rides alongside rather than replacing, so the
   * duplicate constraint keeps the signal it was measured on.
   */
  semanticEmbedding?: readonly number[];
  comparisonClass?: string;
  shotGroup?: string;
  poseFamily?: string;
  poseCluster?: string;
  category?: string;
  pinned?: boolean;
  excluded?: boolean;
  hardRejected?: boolean;
  hardRejectionReason?: string;
  screenshotDocument?: boolean;
  cutFace?: boolean;
  clippedFraction?: number;
  faceExposure?: number;
  faceSharpness?: number;
  headSharpness?: number;
  smile?: number;
  composed?: number;
  aesthetic?: number;
  cleanFrame?: number;
  awake?: number;
  sleeping?: number;
  embraceContext?: number;
  eyesOpen?: number;
  naturalExpression?: number;
};

export type PlannerPolicy = {
  qualityFloor: number;
  faceSharpnessFloor: number;
  headSharpnessFloor: number;
  rejectCutFaces: boolean;
  maxClippedFraction: number;
  faceExposureFloor: number;
  minPerPerson: number;
  maxPerPersonFraction: number;
  minNonPeopleFraction: number;
  maxPerPoseFamily: number;
  maxPerBodyPose: number;
  weightQuality: number;
  weightTime: number;
  weightPlace: number;
  weightMoment: number;
  weightPose: number;
  weightPerson: number;
  weightRedundancy: number;
  weightSmile: number;
  weightComposed: number;
  weightAesthetic: number;
  weightCleanFrame: number;
  midBlinkPenalty: number;
  redundancyFreeSimilarity: number;
  shotSimilarity: number;
  maxSelectedSimilarity: number;
  momentSimilarity: number;
  momentWindowMs: number;
  rareMomentIsolationMs: number;
  timeBins: number;
  maxTimeBins: number;
  maxSleepingFraction: number;
  sleepingMinContrast: number;
  embraceSuppressPercentile: number;
  pinnedMediaIds: readonly string[];
  excludedMediaIds: readonly string[];
  /**
   * Who the album is for, as the user answered it. Anyone absent is LOW
   * priority, which is a real answer and not a missing one.
   *
   * An EMPTY map means the question was never asked, and every gate and cap
   * below then behaves exactly as it did before priorities existed. That
   * distinction matters: "no preference" must not silently become "everyone is
   * low priority" and empty the album.
   */
  personPriority: Readonly<Record<string, "high" | "medium">>;
  /**
   * A medium-priority person's cap, as a fraction of the ordinary per-person
   * cap. Below 1 because the requirement is comparative -- most of the album is
   * the high-priority people, and medium appears less -- so the two caps have to
   * differ, and the medium one is the one that gives way.
   *
   * Derived from the live `personCap` rather than fixed, so that when the
   * planner relaxes that cap to reach the requested count, this relaxes with it
   * instead of becoming the new thing that blocks.
   */
  mediumPriorityCapFraction: number;
  /**
   * M6 rollback switch. `coverage-keys` is the shipped discrete-key greedy;
   * `submodular` is the facility-location + saturating-coverage objective from
   * EXPERT-PLAN section 15. Both run the SAME gates, rescues, caps and
   * relaxation order, so switching this changes the decision rule and nothing
   * else. Default stays on the shipped rule until fixtures say otherwise.
   */
  selector: "coverage-keys" | "submodular";
};

export const DEFAULT_PLANNER_POLICY: PlannerPolicy = {
  qualityFloor: 0.35,
  faceSharpnessFloor: 0.12,
  headSharpnessFloor: 0.08,
  rejectCutFaces: true,
  maxClippedFraction: 0.15,
  faceExposureFloor: 0.06,
  minPerPerson: 1,
  maxPerPersonFraction: 0.5,
  minNonPeopleFraction: 0.15,
  maxPerPoseFamily: 2,
  maxPerBodyPose: 2,
  weightQuality: 1,
  weightTime: 0.85,
  weightPlace: 0.5,
  weightMoment: 0.35,
  weightPose: 0.55,
  weightPerson: 0.6,
  weightRedundancy: 0.8,
  weightSmile: 0.35,
  weightComposed: 0.25,
  weightAesthetic: 0.55,
  weightCleanFrame: 0.35,
  midBlinkPenalty: 0.6,
  redundancyFreeSimilarity: 0.6,
  shotSimilarity: 0.93,
  maxSelectedSimilarity: 0.92,
  momentSimilarity: 0.8,
  momentWindowMs: 6 * 60 * 60 * 1_000,
  rareMomentIsolationMs: 30 * 60 * 1_000,
  timeBins: 0,
  maxTimeBins: 24,
  maxSleepingFraction: 0.2,
  sleepingMinContrast: 0.04,
  embraceSuppressPercentile: 0.85,
  pinnedMediaIds: [],
  excludedMediaIds: [],
  personPriority: {},
  mediumPriorityCapFraction: 0.5,
  selector: "coverage-keys",
};

/**
 * Knobs for the M6 objective. Separate from `PlannerPolicy` because they are
 * the objective's units, not the product's gates, and because a rollback is
 * `selector: "coverage-keys"` — not a re-tune.
 */
export type AlbumObjectiveTuning = {
  /** λf, on an FL term normalised to [0,1] so it is comparable to a category. */
  facilityWeight: number;
  /** How much more it costs to leave a photo WITH PEOPLE unrepresented. */
  peopleImportance: number;
  coverageMoment: number;
  coveragePerson: number;
  coverageTime: number;
  coveragePlace: number;
  coveragePose: number;
  /** Blend weights for sim(i,j); each component is skipped when unmeasured. */
  simSemantic: number;
  simPeople: number;
  simPose: number;
  simPlace: number;
  simTime: number;
  simTimeDecayMs: number;
  /** Bounded 1-swap repair rounds after the greedy fill. */
  swapRounds: number;
};

export const DEFAULT_ALBUM_OBJECTIVE: AlbumObjectiveTuning = {
  facilityWeight: 3,
  peopleImportance: 1,
  // Twice the shipped coverage weights, on purpose. With τ = ln 2 the marginal
  // of h(n) = 1 − e^(−τn) is 0.5^(n+1) — exactly HALF the planner's own
  // bucketGain(n) = 0.5^n. Doubling reproduces today's coverage magnitudes, so
  // the A/B measures facility location and summed-vs-averaged people, not a
  // silent re-tune of five weights.
  coverageTime: 1.7,
  coveragePlace: 1.0,
  coverageMoment: 0.7,
  coveragePose: 1.1,
  coveragePerson: 1.2,
  simSemantic: 0.34,
  simPeople: 0.28,
  simPose: 0.14,
  simPlace: 0.12,
  simTime: 0.12,
  simTimeDecayMs: 6 * 60 * 60 * 1_000,
  swapRounds: 2,
};

export type PlannerReasonCode =
  | "below_quality_floor"
  | "coverage_moment"
  | "coverage_time"
  | "different_place"
  | "different_pose"
  | "distinct_take"
  | "eyes_open"
  | "exposure_clipped"
  | "face_cut"
  | "face_out_of_focus"
  | "face_too_dark"
  | "hard_image_gate"
  | "low_priority_people"
  | "natural_expression"
  | "only_shot_of_person"
  | "person_cap"
  | "screenshot"
  | "sharpest_of_take"
  | "smiling"
  | "smiling_sharp"
  | "strong_photo"
  | "subject_out_of_focus"
  | "user_choice"
  | "user_excluded";

export type PlannerReason = {
  reasonCode: PlannerReasonCode;
  message: string;
};

export type PlannerRejection = PlannerReason & {
  mediaId: string;
  /** Compatibility text for consumers that have not adopted reasonCode yet. */
  reason: string;
};

export type AlbumPlan = {
  /** Chronological story order; unknown capture times are placed last. */
  selectedIds: string[];
  /** Selection order before chronological presentation. */
  byGain: string[];
  unselectedIds: string[];
  rejected: PlannerRejection[];
  rescuedIds: string[];
  missingPersonIds: string[];
  personCounts: Record<string, number>;
  reasonDetailsByMediaId: Record<string, PlannerReason[]>;
  reasonsByMediaId: Record<string, string[]>;
  /**
   * Per-photo marginal gain at the moment it entered, and the objective's own
   * bookkeeping. Only the submodular selector fills this; the discrete-key
   * greedy leaves it undefined.
   */
  objectiveTrace?: AlbumObjectiveTrace;
};

export type AlbumObjectiveTrace = {
  marginalGainByMediaId: Record<string, number>;
  facilityGainByMediaId: Record<string, number>;
  /** F(S) of the album that was returned. */
  value: number;
  /** True marginal-gain evaluations the lazy greedy actually paid for. */
  evaluations: number;
  swaps: { out: string; in: string; delta: number }[];
};

type NormalizedCandidate = Omit<PlannerCandidate, "personIds"> & {
  personIds: string[];
};

type MutableCounts = Record<string, number>;

const CATEGORY_OVERRIDES: Record<string, Partial<PlannerPolicy>> = {
  portrait: { weightSmile: 0.45 },
  couple: { weightComposed: 0.35 },
  group: { midBlinkPenalty: 0.75 },
  detail: { weightAesthetic: 0.7, weightComposed: 0.35 },
};

export function planAlbum(
  input: readonly PlannerCandidate[],
  targetCount: number,
  options: {
    policy?: Partial<PlannerPolicy>;
    requiredPersonIds?: readonly string[];
    objective?: Partial<AlbumObjectiveTuning>;
  } = {},
): AlbumPlan {
  if (!Number.isFinite(targetCount) || targetCount < 0) {
    throw new Error(`targetCount=${targetCount} must be a finite non-negative number`);
  }
  const target = Math.floor(targetCount);
  const policy = policyFrom(options.policy);
  validatePolicy(policy);
  const objective: AlbumObjectiveTuning = {
    ...DEFAULT_ALBUM_OBJECTIVE,
    ...options.objective,
  };

  const ordered = input.map(normalizeCandidate).sort(byMediaId);
  if (new Set(ordered.map((candidate) => candidate.mediaId)).size !== ordered.length) {
    throw new Error("duplicate mediaId in planner candidates");
  }
  for (const candidate of ordered) validateCandidate(candidate);

  const allIds = new Set(ordered.map((candidate) => candidate.mediaId));
  const pinned = new Set([
    ...policy.pinnedMediaIds,
    ...ordered.filter((candidate) => candidate.pinned).map((candidate) => candidate.mediaId),
  ]);
  const excluded = new Set([
    ...policy.excludedMediaIds,
    ...ordered.filter((candidate) => candidate.excluded).map((candidate) => candidate.mediaId),
  ]);
  for (const id of pinned) {
    if (!allIds.has(id)) throw new Error(`pinned media id is not a candidate: ${id}`);
    if (excluded.has(id)) throw new Error(`media id is both pinned and excluded: ${id}`);
  }

  const rejected: PlannerRejection[] = [];
  const absoluteSurvivors = ordered.filter((candidate) => {
    if (pinned.has(candidate.mediaId)) return true;
    const reason = absoluteRejection(candidate, excluded, policy);
    if (reason) rejected.push({ mediaId: candidate.mediaId, reason: reason.message, ...reason });
    return !reason;
  });

  const rareIds = rareMomentIds(absoluteSurvivors, policy);
  const softFailures = new Map<string, PlannerReason>();
  for (const candidate of absoluteSurvivors) {
    if (pinned.has(candidate.mediaId)) continue;
    const failure = softFailure(candidate, policy);
    if (failure) softFailures.set(candidate.mediaId, failure);
  }
  const scarceBest = scarcePersonRescues(absoluteSurvivors, softFailures);
  const rescued = new Set<string>();
  const survivors = absoluteSurvivors.filter((candidate) => {
    const failure = softFailures.get(candidate.mediaId);
    if (!failure || pinned.has(candidate.mediaId)) return true;
    if (rareIds.has(candidate.mediaId) || scarceBest.has(candidate.mediaId)) {
      rescued.add(candidate.mediaId);
      return true;
    }
    rejected.push({ mediaId: candidate.mediaId, reason: failure.message, ...failure });
    return false;
  });

  const byId = new Map(survivors.map((candidate) => [candidate.mediaId, candidate]));
  const standing = qualityStanding(survivors);
  const personUniverse = Array.from(
    new Set([
      ...ordered.flatMap((candidate) => candidate.personIds),
      ...(options.requiredPersonIds ?? []),
    ]),
  ).sort();

  const context = buildSelectionContext(survivors, target, policy);
  const select = policy.selector === "submodular" ? submodularSelect : greedySelect;
  const greedy = select(
    survivors,
    byId,
    standing,
    target,
    policy,
    personUniverse,
    pinned,
    context,
    objective,
  );
  for (const mediaId of greedy.capBlocked) {
    const rejection = plannerReason("person_cap", "per-person cap reached");
    rejected.push({ mediaId, reason: rejection.message, ...rejection });
  }

  const selectedSet = new Set(greedy.selected);
  const blockedSet = new Set(greedy.capBlocked);
  const unselectedIds = survivors
    .map((candidate) => candidate.mediaId)
    .filter((mediaId) => !selectedSet.has(mediaId) && !blockedSet.has(mediaId));
  const personCounts = countPeople(greedy.selected, byId);
  const missingPersonIds = personUniverse.filter(
    (personId) => (personCounts[personId] ?? 0) < policy.minPerPerson,
  );
  const selectedIds = greedy.selected.slice().sort((left, right) => {
    const a = byId.get(left)!;
    const b = byId.get(right)!;
    const at = validTime(a.capturedAt);
    const bt = validTime(b.capturedAt);
    if (at === undefined && bt !== undefined) return 1;
    if (at !== undefined && bt === undefined) return -1;
    return (at ?? 0) - (bt ?? 0) || left.localeCompare(right);
  });

  return {
    selectedIds,
    byGain: greedy.selected,
    unselectedIds,
    rejected: rejected.sort((a, b) => a.mediaId.localeCompare(b.mediaId)),
    rescuedIds: Array.from(rescued).sort(),
    missingPersonIds,
    personCounts,
    reasonDetailsByMediaId: greedy.reasonDetails,
    reasonsByMediaId: greedy.reasons,
    ...(greedy.objectiveTrace ? { objectiveTrace: greedy.objectiveTrace } : {}),
  };
}

/**
 * Everything both selectors measure BEFORE anything is chosen: the discrete
 * keys, the per-axis midrank percentiles, and which frames are sleeping shots.
 *
 * Extracted rather than duplicated so an A/B between the two selectors changes
 * the decision rule and nothing else. The arithmetic inside `greedySelect`'s
 * `gain()` is deliberately NOT extracted with it: re-associating that sum would
 * move it by an ULP or two, which is enough to flip a tie and make the shipped
 * path's pinned fixtures drift for a reason that has nothing to do with M6.
 */
function buildSelectionContext(
  survivors: NormalizedCandidate[],
  target: number,
  policy: PlannerPolicy,
) {
  const knownTimes = survivors
    .map((candidate) => validTime(candidate.capturedAt))
    .filter((value): value is number => value !== undefined)
    .sort((a, b) => a - b);
  const start = knownTimes[0] ?? 0;
  const span = knownTimes.length > 0 ? knownTimes[knownTimes.length - 1] - start : 0;
  const bins = policy.timeBins || Math.max(1, Math.min(target, policy.maxTimeBins));
  const momentOf = groupMoments(survivors, policy);
  return {
    momentOf,
    timeKey: new Map(
      survivors.map((candidate) => [
        candidate.mediaId,
        timeBucket(validTime(candidate.capturedAt), start, span, bins),
      ]),
    ),
    placeKey: new Map(
      survivors.map((candidate) => [candidate.mediaId, candidate.placeKey || UNKNOWN]),
    ),
    momentKey: new Map(
      survivors.map((candidate) => [
        candidate.mediaId,
        `${momentOf.get(candidate.mediaId)}|faces:${Math.min(candidate.personIds.length, 3)}`,
      ]),
    ),
    poseKey: new Map(
      survivors.map((candidate) => [candidate.mediaId, bodyPoseKey(candidate)]),
    ),
    shotKey: new Map(
      survivors.map((candidate) => [candidate.mediaId, candidate.shotGroup || candidate.mediaId]),
    ),
    familyKey: new Map(
      survivors.map((candidate) => [
        candidate.mediaId,
        candidate.poseFamily || candidate.shotGroup || candidate.mediaId,
      ]),
    ),
    smilePercentile: axisPercentiles(survivors, (candidate) => candidate.smile),
    composedPercentile: axisPercentiles(survivors, (candidate) => candidate.composed),
    aestheticPercentile: axisPercentiles(survivors, (candidate) => candidate.aesthetic),
    cleanPercentile: axisPercentiles(survivors, (candidate) => candidate.cleanFrame),
    awakePercentile: axisPercentiles(survivors, (candidate) => candidate.awake),
    eyesPercentile: axisPercentiles(survivors, (candidate) => candidate.eyesOpen),
    embracePercentile: axisPercentiles(survivors, (candidate) => candidate.embraceContext),
    sleeping: new Map(
      survivors.map((candidate) => [
        candidate.mediaId,
        candidate.sleeping !== undefined &&
          candidate.awake !== undefined &&
          candidate.sleeping > candidate.awake &&
          candidate.sleeping > policy.sleepingMinContrast,
      ]),
    ),
  };
}

/**
 * The body-pose cap prevents one person recurring in one posture. A MoveNet
 * cluster by itself describes only joint geometry; using it as a global key
 * makes unrelated people compete merely because both crossed their arms.
 *
 * No known person means no safe owner for the single-person MoveNet result, so
 * the photo gets a unique key. Multi-person sets are kept exact and sorted by
 * normalization: MoveNet does not say which detected identity it followed,
 * and guessing would turn a conservative split into an irreversible fusion.
 */
function bodyPoseKey(candidate: NormalizedCandidate): string {
  if (!candidate.poseCluster || candidate.personIds.length === 0) {
    return `nopose:${candidate.mediaId}`;
  }
  return `pose:${JSON.stringify(candidate.personIds)}|${candidate.poseCluster}`;
}

type SelectionContext = ReturnType<typeof buildSelectionContext>;

/** What both selectors hand back to `planAlbum`. */
type SelectionOutcome = {
  selected: string[];
  capBlocked: string[];
  reasonDetails: Record<string, PlannerReason[]>;
  reasons: Record<string, string[]>;
  objectiveTrace?: AlbumObjectiveTrace;
};

function greedySelect(
  survivors: NormalizedCandidate[],
  byId: Map<string, NormalizedCandidate>,
  standing: Map<string, number>,
  target: number,
  policy: PlannerPolicy,
  personUniverse: string[],
  pinned: Set<string>,
  context: SelectionContext,
  _objective: AlbumObjectiveTuning,
): SelectionOutcome {
  const selected: string[] = [];
  const remaining = survivors.map((candidate) => candidate.mediaId);
  const reasonDetails: Record<string, PlannerReason[]> = {};
  const reasons: Record<string, string[]> = {};
  if (target === 0 || survivors.length === 0) {
    return { selected, capBlocked: [] as string[], reasonDetails, reasons };
  }

  const { timeKey, placeKey, momentKey, poseKey, shotKey, familyKey } = context;

  const timeCounts: MutableCounts = {};
  const placeCounts: MutableCounts = {};
  const momentCounts: MutableCounts = {};
  const poseCounts: MutableCounts = {};
  const personCounts: MutableCounts = {};
  const shotCounts: MutableCounts = {};
  const familyCounts: MutableCounts = {};
  const closest: MutableCounts = {};
  const closestSemantic: MutableCounts = {};
  let personCap = perPersonCap(survivors, target, policy);
  const [redundancyFree, redundancyDenominator] = calibratedRedundancy(
    survivors,
    policy,
  );
  // Ceiling 1 rather than `shotSimilarity`: that constant is calibrated against
  // the perceptual fingerprint's scale and means nothing in TinyCLIP's space.
  const [semanticFree, semanticDenominator] = calibratedRedundancy(
    survivors,
    policy,
    semanticSimilarity,
    (candidate) => Boolean(candidate.semanticEmbedding),
    1,
  );
  const {
    smilePercentile,
    composedPercentile,
    aestheticPercentile,
    cleanPercentile,
    sleeping,
  } = context;
  const sleepingCap = Math.max(1, Math.floor(target * policy.maxSleepingFraction));
  let sleepingCount = 0;

  const commit = (mediaId: string, why: PlannerReason[]) => {
    const candidate = byId.get(mediaId)!;
    const details = dedupeReasons([
      ...selectionSignalReasons(candidate, standing.get(mediaId) ?? 0),
      ...why,
    ]);
    selected.push(mediaId);
    remaining.splice(remaining.indexOf(mediaId), 1);
    reasonDetails[mediaId] = details;
    reasons[mediaId] = details.map(({ message }) => message);
    increment(shotCounts, shotKey.get(mediaId)!);
    increment(familyCounts, familyKey.get(mediaId)!);
    increment(timeCounts, timeKey.get(mediaId)!);
    increment(placeCounts, placeKey.get(mediaId)!);
    increment(momentCounts, momentKey.get(mediaId)!);
    increment(poseCounts, poseKey.get(mediaId)!);
    for (const personId of peopleKey(candidate)) increment(personCounts, personId);
    if (sleeping.get(mediaId)) sleepingCount += 1;
    if (candidate.embedding) {
      for (const otherId of remaining) {
        const other = byId.get(otherId)!;
        const similarity = candidateSimilarity(candidate, other);
        if (similarity !== undefined && similarity > (closest[otherId] ?? 0)) {
          closest[otherId] = similarity;
        }
      }
    }
    if (candidate.semanticEmbedding) {
      for (const otherId of remaining) {
        const other = byId.get(otherId)!;
        const similarity = semanticSimilarity(candidate, other);
        if (similarity !== undefined && similarity > (closestSemantic[otherId] ?? 0)) {
          closestSemantic[otherId] = similarity;
        }
      }
    }
  };

  const gain = (mediaId: string) => {
    const candidate = byId.get(mediaId)!;
    const weights = weightsFor(candidate, policy);
    let value = weights.weightQuality * (standing.get(mediaId) ?? candidate.quality);
    value += weights.weightTime * bucketGain(timeCounts[timeKey.get(mediaId)!] ?? 0);
    value += weights.weightPlace * bucketGain(placeCounts[placeKey.get(mediaId)!] ?? 0);
    value += weights.weightMoment * bucketGain(momentCounts[momentKey.get(mediaId)!] ?? 0);
    value += weights.weightPose * bucketGain(poseCounts[poseKey.get(mediaId)!] ?? 0);
    const people = peopleKey(candidate);
    value +=
      weights.weightPerson *
      (people.reduce((sum, personId) => sum + bucketGain(personCounts[personId] ?? 0), 0) /
        people.length);
    if (candidate.embedding) {
      value -=
        weights.weightRedundancy *
        Math.min(
          1,
          Math.max(0, (closest[mediaId] ?? 0) - redundancyFree) /
            redundancyDenominator,
        );
    }
    // The same penalty, charged on scene rather than on frame. This is what
    // separates "another photo of the same afternoon in the same clothes in the
    // same room" from "a different moment" -- a distinction the 8x8 perceptual
    // grid above is structurally incapable of drawing. It is deliberately a
    // soft cost and never a hard exclusion: TinyCLIP is good enough to rank
    // with and not good enough to throw a photograph away on.
    if (candidate.semanticEmbedding) {
      value -=
        weights.weightRedundancy *
        Math.min(
          1,
          Math.max(0, (closestSemantic[mediaId] ?? 0) - semanticFree) /
            semanticDenominator,
        );
    }
    value += weights.weightSmile * (smilePercentile.get(mediaId) ?? 0.5);
    value += weights.weightComposed * (composedPercentile.get(mediaId) ?? 0.5);
    value += weights.weightAesthetic * (aestheticPercentile.get(mediaId) ?? 0.5);
    value += weights.weightCleanFrame * (cleanPercentile.get(mediaId) ?? 0.5);
    if (isMidBlink(candidate, policy, context)) value -= weights.midBlinkPenalty;
    return quantize(value);
  };

  for (const mediaId of remaining.filter((id) => pinned.has(id)).sort()) {
    commit(mediaId, [plannerReason("user_choice", "Kept because you chose it.")]);
  }

  if (policy.minPerPerson > 0) {
    while (selected.length < target) {
      const uncovered = new Set(
        personUniverse.filter(
          (personId) => (personCounts[personId] ?? 0) < policy.minPerPerson,
        ),
      );
      if (uncovered.size === 0) break;
      const options = remaining
        .map((mediaId) => ({
          mediaId,
          newPeople: byId
            .get(mediaId)!
            .personIds.filter((personId) => uncovered.has(personId)).length,
          gain: gain(mediaId),
        }))
        .filter((entry) => entry.newPeople > 0)
        .sort(
          (left, right) =>
            right.newPeople - left.newPeople ||
            right.gain - left.gain ||
            left.mediaId.localeCompare(right.mediaId),
        );
      if (options.length === 0) break;
      commit(options[0].mediaId, [plannerReason("only_shot_of_person", "Keeps everyone in the story.")]);
    }
  }

  const nonPeopleAvailable = survivors.filter((candidate) => candidate.personIds.length === 0).length;
  const reserve = Math.min(
    Math.floor(target * policy.minNonPeopleFraction),
    nonPeopleAvailable,
    target,
  );
  const blocked = new Set<string>();
  let allowedPerShot = 1;
  let allowedPerFamily = policy.maxPerPoseFamily;
  let allowedPerPose = policy.maxPerBodyPose;

  while (selected.length < target && remaining.length > 0) {
    const slotsLeft = target - selected.length;
    const nonPeopleSelected = selected.filter(
      (mediaId) => byId.get(mediaId)!.personIds.length === 0,
    ).length;
    const reservedNow = reserve - nonPeopleSelected >= slotsLeft;
    const eligible: string[] = [];
    let personCapBlocked = false;
    for (const mediaId of remaining) {
      const candidate = byId.get(mediaId)!;
      const atCap =
        candidate.personIds.length > 0 &&
        candidate.personIds.some(
          (personId) =>
            (personCounts[personId] ?? 0) >=
            priorityPersonCap(personId, personCap, policy),
        );
      if (atCap) blocked.add(mediaId);
      if (reservedNow && candidate.personIds.length > 0) continue;
      if (atCap) {
        personCapBlocked = true;
        continue;
      }
      if (sleeping.get(mediaId) && sleepingCount >= sleepingCap) continue;
      eligible.push(mediaId);
    }
    if (eligible.length === 0) {
      // The per-person cap keeps one face from taking the whole book. It must
      // not SHORTEN the book, so relax it rather than stopping — the same way
      // the shot/family/pose caps relax below. An album of one person is the
      // case that forces this: every photo holds the same face, so a second
      // incidental face anywhere in the set flips perPersonCap() off `target`
      // and onto maxPerPersonFraction, and 24 requested photos came back as 12.
      if (personCapBlocked) {
        personCap += 1;
        continue;
      }
      break;
    }

    const fresh = eligible.filter(
      (mediaId) =>
        (shotCounts[shotKey.get(mediaId)!] ?? 0) < allowedPerShot &&
        (familyCounts[familyKey.get(mediaId)!] ?? 0) < allowedPerFamily &&
        (poseCounts[poseKey.get(mediaId)!] ?? 0) < allowedPerPose,
    );
    if (fresh.length === 0) {
      const poseReachable = eligible.filter(
        (mediaId) => (poseCounts[poseKey.get(mediaId)!] ?? 0) < allowedPerPose,
      );
      if (poseReachable.length > 0) {
        const shotReachable = poseReachable.filter(
          (mediaId) => (shotCounts[shotKey.get(mediaId)!] ?? 0) < allowedPerShot,
        );
        if (shotReachable.length > 0) allowedPerFamily += 1;
        else allowedPerShot += 1;
      } else {
        allowedPerPose += 1;
      }
      continue;
    }

    const distinct = fresh.filter(
      (mediaId) => (closest[mediaId] ?? 0) < policy.maxSelectedSimilarity,
    );
    // The caps are HARD -- an empty `fresh` relaxes one rather than admitting a
    // blocked photo -- but the near-duplicate bar below was only a preference,
    // and the asymmetry put nine of a ten-frame sunset burst into a 24-photo
    // album. A burst with no pose cluster is never blocked by `allowedPerPose`,
    // so once the pose cap saturated on the people shots, `fresh` collapsed to
    // the burst alone: `distinct` was empty, the fallback took a near-duplicate,
    // and because `fresh` was never empty the cap never relaxed to let anything
    // else back in. Every extra sunset made the next one likelier.
    //
    // So try the same relaxation ladder first, over the candidates that are NOT
    // near-duplicates. This terminates for the same reason the ladder above
    // does: each rung raises a cap, and once the caps exceed every count
    // `fresh` is all of `eligible` and `distinct` is this non-empty set.
    if (distinct.length === 0) {
      const distinctEligible = eligible.filter(
        (mediaId) => (closest[mediaId] ?? 0) < policy.maxSelectedSimilarity,
      );
      if (distinctEligible.length > 0) {
        const poseReachable = distinctEligible.filter(
          (mediaId) => (poseCounts[poseKey.get(mediaId)!] ?? 0) < allowedPerPose,
        );
        if (poseReachable.length > 0) {
          const shotReachable = poseReachable.filter(
            (mediaId) => (shotCounts[shotKey.get(mediaId)!] ?? 0) < allowedPerShot,
          );
          if (shotReachable.length > 0) allowedPerFamily += 1;
          else allowedPerShot += 1;
        } else {
          allowedPerPose += 1;
        }
        continue;
      }
      // Everything left really is a near-duplicate. Take the least similar,
      // exactly as before -- an album short of its target is worse than one
      // holding a second frame of the only thing still available.
    }
    const bestId =
      distinct.length > 0
        ? distinct.sort((left, right) => gain(right) - gain(left) || left.localeCompare(right))[0]
        : fresh.sort(
            (left, right) =>
              roundSimilarity(closest[left] ?? 0) - roundSimilarity(closest[right] ?? 0) ||
              gain(right) - gain(left) ||
              left.localeCompare(right),
          )[0];
    commit(bestId, coverageReasons(bestId, standing, timeCounts, placeCounts, momentCounts, poseCounts, timeKey, placeKey, momentKey, poseKey));
  }

  // A relaxed cap can admit a photo that an earlier pass had blocked, so a
  // "per-person cap reached" rejection must not be reported for one that ended
  // up in the album.
  const chosen = new Set(selected);
  return {
    selected,
    capBlocked: Array.from(blocked).filter((mediaId) => !chosen.has(mediaId)).sort(),
    reasonDetails,
    reasons,
  };
}

/** Which hard constraint a candidate set violates, in relaxation order. */
type ConstraintCode =
  | "pose"
  | "family"
  | "shot"
  | "person_cap"
  | "sleeping"
  | "non_people_reserve"
  | "near_duplicate";

/**
 * M6: the same album, chosen by maximizing F(S) instead of ranking photos.
 *
 * Structure is deliberately identical to `greedySelect` — pins, then the hard
 * people floor, then the fill — so the only thing under test is the pick. What
 * changes inside the fill:
 *
 *   1. Coverage is a set function. `greedySelect` averages the per-person bonus
 *      over a photo's faces, so a frame holding five people who are all missing
 *      scores exactly what a solo portrait of one missing person scores. The
 *      submodular person category SUMS, so in a library that is mostly group
 *      shots the group shot wins — which is the product's own stated ordering.
 *   2. Facility location replaces the MMR redundancy penalty. Instead of
 *      docking a photo for resembling something already chosen, F(S) pays for
 *      how well S represents the WHOLE candidate pool. A frame nothing else
 *      resembles is worth its full importance; the second frame of a burst is
 *      worth almost nothing. That is the same repulsion a DPP would give, from
 *      a term that needs only sim ≥ 0 rather than a PSD kernel.
 *   3. A near-duplicate is a hard constraint, not a tiebreak. It is also the
 *      LAST thing relaxed, and even then only far enough to admit the single
 *      most distinct blocked frame — so the album never comes back short and
 *      never silently gains a second copy of a burst.
 */
function submodularSelect(
  survivors: NormalizedCandidate[],
  byId: Map<string, NormalizedCandidate>,
  standing: Map<string, number>,
  target: number,
  policy: PlannerPolicy,
  personUniverse: string[],
  pinned: Set<string>,
  context: SelectionContext,
  tuning: AlbumObjectiveTuning,
): SelectionOutcome {
  const reasonDetails: Record<string, PlannerReason[]> = {};
  const reasons: Record<string, string[]> = {};
  const selected: string[] = [];
  if (target === 0 || survivors.length === 0) {
    return { selected, capBlocked: [], reasonDetails, reasons };
  }

  const { timeKey, placeKey, momentKey, poseKey, shotKey, familyKey, sleeping } = context;
  const ids = survivors.map((candidate) => candidate.mediaId);
  const indexOf = new Map(ids.map((mediaId, index) => [mediaId, index]));

  const similarity = survivors.map((left) =>
    survivors.map((right) => blendedSimilarity(left, right, tuning)),
  );
  // The raw perceptual/semantic cosine, kept separate from the blend: the
  // 0.92 near-duplicate bar is calibrated on THAT number, not on a mixture
  // that a shared place and a shared minute can push over the line on their own.
  const rawSimilarity = survivors.map((left) =>
    survivors.map((right) => candidateSimilarity(left, right) ?? 0),
  );

  const problem: SubmodularProblem = {
    quality: survivors.map((candidate) => photoQuality(candidate, policy, standing, context)),
    similarity,
    importance: survivors.map(
      (candidate) =>
        1 +
        (tuning.peopleImportance * Math.min(candidate.personIds.length, 3)) / 3,
    ),
    facilityWeight: tuning.facilityWeight,
    categories: [
      categoryOf(tuning.coverageMoment, ids, (mediaId) => [momentKey.get(mediaId)!]),
      categoryOf(tuning.coveragePerson, ids, (mediaId) => peopleKey(byId.get(mediaId)!)),
      categoryOf(tuning.coverageTime, ids, (mediaId) => [timeKey.get(mediaId)!]),
      categoryOf(tuning.coveragePlace, ids, (mediaId) => [placeKey.get(mediaId)!]),
      categoryOf(tuning.coveragePose, ids, (mediaId) => [poseKey.get(mediaId)!]),
    ],
    saturation: COVERAGE_SATURATION,
  };
  validateProblem(problem);

  const state = emptyState(problem);
  const personCounts: MutableCounts = {};
  const capBlocked = new Set<string>();

  let personCap = perPersonCap(survivors, target, policy);
  let allowedPerShot = 1;
  let allowedPerFamily = policy.maxPerPoseFamily;
  let allowedPerPose = policy.maxPerBodyPose;
  let duplicateCeiling = policy.maxSelectedSimilarity;
  let sleepingCap = Math.max(1, Math.floor(target * policy.maxSleepingFraction));
  const nonPeopleAvailable = survivors.filter(
    (candidate) => candidate.personIds.length === 0,
  ).length;
  let reserve = Math.min(
    Math.floor(target * policy.minNonPeopleFraction),
    nonPeopleAvailable,
    target,
  );

  /**
   * The hard constraints, as one whole-set predicate. Both the greedy's
   * feasibility test and the swap pass call it, so a swap can never quietly
   * produce a set the greedy would have refused.
   */
  const violation = (album: readonly string[]): ConstraintCode | undefined => {
    const shots: MutableCounts = {};
    const families: MutableCounts = {};
    const poses: MutableCounts = {};
    const people: MutableCounts = {};
    let sleepers = 0;
    let withoutPeople = 0;
    for (const mediaId of album) {
      const candidate = byId.get(mediaId)!;
      if (pinned.has(mediaId)) {
        // A pin bypasses every gate upstream; it must bypass the caps too, or
        // the swap pass would report the user's own choice as infeasible.
        if (candidate.personIds.length === 0) withoutPeople += 1;
        continue;
      }
      increment(shots, shotKey.get(mediaId)!);
      increment(families, familyKey.get(mediaId)!);
      increment(poses, poseKey.get(mediaId)!);
      for (const personId of candidate.personIds) increment(people, personId);
      if (sleeping.get(mediaId)) sleepers += 1;
      if (candidate.personIds.length === 0) withoutPeople += 1;
      if (poses[poseKey.get(mediaId)!] > allowedPerPose) return "pose";
      if (families[familyKey.get(mediaId)!] > allowedPerFamily) return "family";
      if (shots[shotKey.get(mediaId)!] > allowedPerShot) return "shot";
      if (
        candidate.personIds.some(
          (personId) =>
            people[personId] > priorityPersonCap(personId, personCap, policy),
        )
      ) {
        return "person_cap";
      }
    }
    if (sleepers > sleepingCap) return "sleeping";
    // Forward-looking, exactly as the shipped reserve is: scenery must still be
    // able to fill its share of the slots that are left.
    if (withoutPeople + Math.max(0, target - album.length) < reserve) {
      return "non_people_reserve";
    }
    for (let left = 0; left < album.length; left += 1) {
      for (let right = left + 1; right < album.length; right += 1) {
        const a = indexOf.get(album[left])!;
        const b = indexOf.get(album[right])!;
        if (rawSimilarity[a][b] >= duplicateCeiling) return "near_duplicate";
      }
    }
    return undefined;
  };

  const chosen = new Set<string>();
  const commitById = (mediaId: string) => {
    chosen.add(mediaId);
    selected.push(mediaId);
    for (const personId of peopleKey(byId.get(mediaId)!)) increment(personCounts, personId);
    applyPick(problem, state, indexOf.get(mediaId)!);
  };

  for (const mediaId of ids.filter((id) => pinned.has(id)).sort()) commitById(mediaId);

  // People are a hard floor, not a weight — unchanged from the shipped rule.
  // Only the tiebreak among equally-covering frames now comes from F(S).
  const personFloorPicks = new Set<string>();
  if (policy.minPerPerson > 0) {
    while (selected.length < target) {
      const uncovered = new Set(
        personUniverse.filter(
          (personId) => (personCounts[personId] ?? 0) < policy.minPerPerson,
        ),
      );
      if (uncovered.size === 0) break;
      const options = ids
        .filter((mediaId) => !chosen.has(mediaId))
        .map((mediaId) => ({
          mediaId,
          newPeople: byId
            .get(mediaId)!
            .personIds.filter((personId) => uncovered.has(personId)).length,
          gain: gainBreakdown(problem, state, indexOf.get(mediaId)!).total,
        }))
        .filter((entry) => entry.newPeople > 0)
        .sort(
          (left, right) =>
            right.newPeople - left.newPeople ||
            right.gain - left.gain ||
            left.mediaId.localeCompare(right.mediaId),
        );
      if (options.length === 0) break;
      commitById(options[0].mediaId);
      personFloorPicks.add(options[0].mediaId);
    }
  }

  let relaxations = 0;
  const relaxationLimit = 4 * survivors.length + 8;
  const blockedCodes = () => {
    const codes = new Set<ConstraintCode>();
    for (const mediaId of ids) {
      if (chosen.has(mediaId)) continue;
      const code = violation([...selected, mediaId]);
      if (code) codes.add(code);
    }
    return codes;
  };

  const greedy = lazyGreedy({
    // Pins and the people floor have already spent slots. `lazyGreedy` counts
    // its own picks from zero, so the budget it gets is what is LEFT.
    budget: Math.max(0, target - selected.length),
    order: ids.map((_, index) => index),
    marginal: (index) => gainBreakdown(problem, state, index).total,
    blocked: (index) => {
      const mediaId = ids[index];
      if (chosen.has(mediaId)) return true;
      const code = violation([...selected, mediaId]);
      if (code === "person_cap") capBlocked.add(mediaId);
      return code !== undefined;
    },
    commit: (index) => commitById(ids[index]),
    // The documented soft-relaxation order. Nothing here can shorten an album;
    // each rung widens exactly one cap and the loop retries.
    relax: () => {
      if (relaxations >= relaxationLimit) return false;
      relaxations += 1;
      const codes = blockedCodes();
      if (codes.size === 0) return false;
      if (codes.has("person_cap") && codes.size === 1) {
        personCap += 1;
        return true;
      }
      if (codes.has("family") && !codes.has("pose")) {
        allowedPerFamily += 1;
        return true;
      }
      if (codes.has("shot") && !codes.has("pose")) {
        allowedPerShot += 1;
        return true;
      }
      if (codes.has("pose")) {
        allowedPerPose += 1;
        return true;
      }
      if (codes.has("person_cap")) {
        personCap += 1;
        return true;
      }
      if (codes.has("sleeping")) {
        sleepingCap += 1;
        return true;
      }
      if (codes.has("non_people_reserve") && reserve > 0) {
        reserve -= 1;
        return true;
      }
      if (codes.has("near_duplicate")) {
        // Admit the single most DISTINCT blocked frame and no more. Raising the
        // bar to 1 here would let a whole burst back in at once.
        const next = ids
          .filter((mediaId) => !chosen.has(mediaId))
          .map((mediaId) =>
            Math.max(
              ...selected.map(
                (other) => rawSimilarity[indexOf.get(mediaId)!][indexOf.get(other)!],
              ),
              0,
            ),
          )
          .filter((value) => value >= duplicateCeiling)
          .sort((left, right) => left - right)[0];
        if (next === undefined) return false;
        duplicateCeiling = next + 1e-9;
        return true;
      }
      return false;
    },
  });

  const swaps = boundedSwapPass({
    problem, ids, indexOf, selected, pinned, violation, rounds: tuning.swapRounds,
  });
  for (const swap of swaps) {
    chosen.delete(swap.out);
    chosen.add(swap.in);
  }

  // Reasons and marginal gains are produced by REPLAYING the album that was
  // actually returned, not by recording them as the greedy went. A swap can
  // put a photo in that the greedy never committed, and a user staring at
  // "Adds another moment to the story." deserves it to be true of the album
  // they can see rather than of an intermediate set nobody kept.
  const replay = emptyState(problem);
  const timeCounts: MutableCounts = {};
  const placeCounts: MutableCounts = {};
  const momentCounts: MutableCounts = {};
  const poseCounts: MutableCounts = {};
  const marginalGainByMediaId: Record<string, number> = {};
  const facilityGainByMediaId: Record<string, number> = {};
  for (const mediaId of selected) {
    const index = indexOf.get(mediaId)!;
    const breakdown = gainBreakdown(problem, replay, index);
    marginalGainByMediaId[mediaId] = breakdown.total;
    facilityGainByMediaId[mediaId] = quantize(breakdown.facility);
    const why = pinned.has(mediaId)
      ? [plannerReason("user_choice", "Kept because you chose it.")]
      : personFloorPicks.has(mediaId)
        ? [plannerReason("only_shot_of_person", "Keeps everyone in the story.")]
        : coverageReasons(
            mediaId, standing, timeCounts, placeCounts, momentCounts, poseCounts,
            timeKey, placeKey, momentKey, poseKey,
          );
    const details = dedupeReasons([
      ...selectionSignalReasons(byId.get(mediaId)!, standing.get(mediaId) ?? 0),
      ...why,
    ]);
    reasonDetails[mediaId] = details;
    reasons[mediaId] = details.map(({ message }) => message);
    increment(timeCounts, timeKey.get(mediaId)!);
    increment(placeCounts, placeKey.get(mediaId)!);
    increment(momentCounts, momentKey.get(mediaId)!);
    increment(poseCounts, poseKey.get(mediaId)!);
    applyPick(problem, replay, index);
  }

  return {
    selected,
    capBlocked: Array.from(capBlocked).filter((mediaId) => !chosen.has(mediaId)).sort(),
    reasonDetails,
    reasons,
    objectiveTrace: {
      marginalGainByMediaId,
      facilityGainByMediaId,
      value: objectiveValue(problem, selected.map((mediaId) => indexOf.get(mediaId)!)),
      evaluations: greedy.evaluations,
      swaps,
    },
  };
}

/**
 * The bounded, deterministic 1-swap repair M6 asks for.
 *
 * Greedy commits early with a nearly empty set, so its first few picks are
 * chosen against almost no context. A swap pass is the cheap correction: for
 * every (selected, unselected) pair, take the single best exchange that raises
 * F(S) by more than ε and stays feasible, then repeat a bounded number of
 * times. Deterministic — the best delta wins, ties go to the lower media id.
 *
 * A pinned photo is never swapped out: the user chose it.
 */
function boundedSwapPass(args: {
  problem: SubmodularProblem;
  ids: string[];
  indexOf: Map<string, number>;
  selected: string[];
  pinned: Set<string>;
  rounds: number;
  violation: (album: readonly string[]) => ConstraintCode | undefined;
}) {
  const { problem, ids, indexOf, selected, pinned, rounds, violation } = args;
  const swaps: { out: string; in: string; delta: number }[] = [];
  const epsilon = 1e-6;
  const asIndices = (album: readonly string[]) =>
    album.map((mediaId) => indexOf.get(mediaId)!);

  for (let round = 0; round < rounds; round += 1) {
    const current = objectiveValue(problem, asIndices(selected));
    let best: { out: string; in: string; delta: number } | undefined;
    for (const outgoing of selected.slice().sort()) {
      if (pinned.has(outgoing)) continue;
      for (const incoming of ids) {
        if (selected.includes(incoming)) continue;
        const proposal = selected.map((mediaId) =>
          mediaId === outgoing ? incoming : mediaId,
        );
        if (violation(proposal) !== undefined) continue;
        const delta = quantize(objectiveValue(problem, asIndices(proposal)) - current);
        if (delta <= epsilon) continue;
        if (
          !best ||
          delta > best.delta ||
          (delta === best.delta &&
            `${incoming}<${outgoing}`.localeCompare(`${best.in}<${best.out}`) < 0)
        ) {
          best = { out: outgoing, in: incoming, delta };
        }
      }
    }
    if (!best) break;
    selected[selected.indexOf(best.out)] = best.in;
    swaps.push(best);
  }
  return swaps;
}

function categoryOf(
  weight: number,
  ids: readonly string[],
  groupsOf: (mediaId: string) => readonly string[],
): CoverageCategory {
  const groupIndex = new Map<string, number>();
  const membership = ids.map((mediaId) =>
    groupsOf(mediaId).map((key) => {
      const existing = groupIndex.get(key);
      if (existing !== undefined) return existing;
      const next = groupIndex.size;
      groupIndex.set(key, next);
      return next;
    }),
  );
  return { weight, groupWeight: Array(groupIndex.size).fill(1), membership };
}

/**
 * Mid-blink, as both selectors judge it. A boolean, so lifting it out of
 * `greedySelect`'s `gain()` cannot move that sum by a floating-point ulp.
 */
function isMidBlink(
  candidate: NormalizedCandidate,
  policy: PlannerPolicy,
  context: SelectionContext,
) {
  const mediaId = candidate.mediaId;
  const suppressed =
    candidate.embraceContext !== undefined &&
    candidate.embraceContext > 0 &&
    (context.embracePercentile.get(mediaId) ?? 0.5) >= policy.embraceSuppressPercentile;
  return (
    !suppressed &&
    !context.sleeping.get(mediaId) &&
    ((candidate.eyesOpen !== undefined &&
      candidate.eyesOpen < 0.5 &&
      (context.eyesPercentile.get(mediaId) ?? 0.5) < 0.25) ||
      (candidate.eyesOpen === undefined &&
        candidate.awake !== undefined &&
        candidate.awake < 0 &&
        (context.awakePercentile.get(mediaId) ?? 0.5) < 0.25))
  );
}

/**
 * q_i — everything the objective knows about ONE photo, before any set is
 * formed: how it stands against its comparison class, plus the four semantic
 * axes (smile, composed, TinyCLIP aesthetic, clean frame), minus a mid-blink.
 *
 * These are the same terms and weights the shipped `gain()` applies. Q(S) is
 * modular, so the submodular pick is not competing with a different idea of
 * what makes one photograph better than another — only with a different idea
 * of what makes a SET better than another.
 */
function photoQuality(
  candidate: NormalizedCandidate,
  policy: PlannerPolicy,
  standing: Map<string, number>,
  context: SelectionContext,
) {
  const mediaId = candidate.mediaId;
  const weights = weightsFor(candidate, policy);
  let value = weights.weightQuality * (standing.get(mediaId) ?? candidate.quality);
  value += weights.weightSmile * (context.smilePercentile.get(mediaId) ?? 0.5);
  value += weights.weightComposed * (context.composedPercentile.get(mediaId) ?? 0.5);
  value += weights.weightAesthetic * (context.aestheticPercentile.get(mediaId) ?? 0.5);
  value += weights.weightCleanFrame * (context.cleanPercentile.get(mediaId) ?? 0.5);
  if (isMidBlink(candidate, policy, context)) value -= weights.midBlinkPenalty;
  return quantize(value);
}

/**
 * sim(i,j) ∈ [0,1] — the blend from EXPERT-PLAN section 15, restricted to the
 * five things this phone actually measures. Any component whose evidence is
 * missing on either side is dropped and the remaining weights are renormalised,
 * so a photo with no GPS is not thereby "dissimilar to everything".
 *
 * Non-negativity is load-bearing, not cosmetic: it is what lets facility
 * location stand in for a DPP without a positive semi-definite kernel.
 */
export function blendedSimilarity(
  left: NormalizedCandidate,
  right: NormalizedCandidate,
  tuning: AlbumObjectiveTuning,
): number {
  if (left.mediaId === right.mediaId) return 1;
  let weighted = 0;
  let available = 0;
  const add = (weight: number, value: number) => {
    if (weight <= 0) return;
    weighted += weight * value;
    available += weight;
  };

  const semantic = candidateSimilarity(left, right);
  if (semantic !== undefined) add(tuning.simSemantic, Math.min(1, Math.max(0, semantic)));

  if (left.personIds.length > 0 || right.personIds.length > 0) {
    const shared = left.personIds.filter((personId) => right.personIds.includes(personId)).length;
    const union = new Set([...left.personIds, ...right.personIds]).size;
    add(tuning.simPeople, union === 0 ? 0 : shared / union);
  }

  if (left.poseCluster && right.poseCluster) {
    // MoveNet describes a person's posture, not a globally scarce posture.
    // Requiring the same high-confidence person set prevents two different
    // people with the same stance from becoming similar merely because their
    // elbow angles match. Multi-person sets must match exactly: MoveNet fits
    // only one body and guessing which face owns it would violate the
    // split-first identity rule.
    add(tuning.simPose, bodyPoseKey(left) === bodyPoseKey(right) ? 1 : 0);
  }

  const leftPlace = left.placeKey || UNKNOWN;
  const rightPlace = right.placeKey || UNKNOWN;
  if (leftPlace !== UNKNOWN && rightPlace !== UNKNOWN) {
    add(tuning.simPlace, leftPlace === rightPlace ? 1 : 0);
  }

  const leftTime = validTime(left.capturedAt);
  const rightTime = validTime(right.capturedAt);
  if (leftTime !== undefined && rightTime !== undefined && tuning.simTimeDecayMs > 0) {
    add(tuning.simTime, Math.exp(-Math.abs(leftTime - rightTime) / tuning.simTimeDecayMs));
  }

  if (available <= 0) return 0;
  return Math.min(1, Math.max(0, quantize(weighted / available)));
}

function coverageReasons(
  mediaId: string,
  standing: Map<string, number>,
  timeCounts: MutableCounts,
  placeCounts: MutableCounts,
  momentCounts: MutableCounts,
  poseCounts: MutableCounts,
  timeKey: Map<string, string>,
  placeKey: Map<string, string>,
  momentKey: Map<string, string>,
  poseKey: Map<string, string>,
) {
  const reasons: PlannerReason[] = [];
  if ((momentCounts[momentKey.get(mediaId)!] ?? 0) === 0) reasons.push(plannerReason("coverage_moment", "Adds another moment to the story."));
  if ((timeCounts[timeKey.get(mediaId)!] ?? 0) === 0) reasons.push(plannerReason("coverage_time", "Keeps the album spread across the whole event."));
  if (placeKey.get(mediaId) !== UNKNOWN && (placeCounts[placeKey.get(mediaId)!] ?? 0) === 0) {
    reasons.push(plannerReason("different_place", "Shows a different place from the memory."));
  }
  if (!poseKey.get(mediaId)!.startsWith("nopose:") && (poseCounts[poseKey.get(mediaId)!] ?? 0) === 0) {
    reasons.push(plannerReason("different_pose", "Adds a different pose."));
  }
  if ((standing.get(mediaId) ?? 0) >= 0.7) reasons.push(plannerReason("sharpest_of_take", "One of the clearest photos in its group."));
  return reasons.length > 0 ? reasons : [plannerReason("strong_photo", "A strong photo that fits the story.")];
}

function selectionSignalReasons(candidate: NormalizedCandidate, standing: number): PlannerReason[] {
  const reasons: PlannerReason[] = [];
  if (candidate.smile !== undefined && candidate.smile >= 0.7) {
    reasons.push(standing >= 0.7
      ? plannerReason("smiling_sharp", "Everyone looks happy, and this is one of the clearest photos in its group.")
      : plannerReason("smiling", "Everyone looks happy in this moment."));
  }
  if (candidate.eyesOpen !== undefined && candidate.eyesOpen >= 0.75) {
    reasons.push(plannerReason("eyes_open", "Everyone's eyes are open and easy to see."));
  }
  if (candidate.naturalExpression !== undefined && candidate.naturalExpression >= 0.7) {
    reasons.push(plannerReason("natural_expression", "The expression feels natural and warm."));
  }
  return reasons;
}

function plannerReason(reasonCode: PlannerReasonCode, message: string): PlannerReason {
  return { reasonCode, message };
}

function dedupeReasons(reasons: PlannerReason[]): PlannerReason[] {
  const seen = new Set<PlannerReasonCode>();
  return reasons.filter(({ reasonCode }) => {
    if (seen.has(reasonCode)) return false;
    seen.add(reasonCode);
    return true;
  });
}

function normalizeCandidate(candidate: PlannerCandidate): NormalizedCandidate {
  return {
    ...candidate,
    personIds: Array.from(new Set(candidate.personIds ?? [])).sort(),
  };
}

function validateCandidate(candidate: NormalizedCandidate) {
  if (!candidate.mediaId) throw new Error("planner candidate mediaId is empty");
  if (!Number.isFinite(candidate.quality) || candidate.quality < 0 || candidate.quality > 1) {
    throw new Error(`${candidate.mediaId}: quality must be finite in [0,1]`);
  }
  if (candidate.embedding?.some((value) => !Number.isFinite(value))) {
    throw new Error(`${candidate.mediaId}: embedding contains a non-finite value`);
  }
}

function policyFrom(overrides?: Partial<PlannerPolicy>): PlannerPolicy {
  return { ...DEFAULT_PLANNER_POLICY, ...overrides };
}

function validatePolicy(policy: PlannerPolicy) {
  const unitValues = [
    policy.qualityFloor,
    policy.faceSharpnessFloor,
    policy.headSharpnessFloor,
    policy.maxClippedFraction,
    policy.faceExposureFloor,
    policy.maxPerPersonFraction,
    policy.minNonPeopleFraction,
    policy.redundancyFreeSimilarity,
    policy.shotSimilarity,
    policy.maxSelectedSimilarity,
    policy.momentSimilarity,
    policy.maxSleepingFraction,
    policy.embraceSuppressPercentile,
  ];
  if (unitValues.some((value) => !Number.isFinite(value) || value < 0 || value > 1)) {
    throw new Error("planner unit policy values must be finite in [0,1]");
  }
  if (policy.maxPerPoseFamily < 1 || policy.maxPerBodyPose < 1 || policy.maxTimeBins < 1) {
    throw new Error("planner caps must be at least one");
  }
  const pins = new Set(policy.pinnedMediaIds);
  for (const id of policy.excludedMediaIds) {
    if (pins.has(id)) throw new Error(`media id is both pinned and excluded: ${id}`);
  }
}

function absoluteRejection(
  candidate: NormalizedCandidate,
  excluded: Set<string>,
  policy: PlannerPolicy,
) {
  if (excluded.has(candidate.mediaId)) return plannerReason("user_excluded", "excluded by user");
  if (candidate.hardRejected) return plannerReason("hard_image_gate", candidate.hardRejectionReason || "failed a hard image gate");
  if (candidate.screenshotDocument) return plannerReason("screenshot", "screenshot or document");
  if (lowPriorityOnly(candidate, policy)) {
    return plannerReason("low_priority_people", "only lower-priority people are in it");
  }
  return undefined;
}

/**
 * A photograph whose people the user did NOT pick, and which has no one they
 * did pick to earn it a place.
 *
 * ABSOLUTE rather than a score penalty, because the requirement is absolute:
 * lower-priority people belong in the album only alongside someone chosen. A
 * penalty would let a run of very good photographs of unchosen people outscore
 * the chosen ones and quietly take over the album, which is the exact outcome
 * asking the question was meant to prevent. Being absolute also puts it out of
 * reach of `scarcePersonRescues`, which only ever sees candidates that already
 * survived this.
 *
 * Photographs with NO people in them are never caught here. Scenery is not
 * low-priority company; it is the album breathing, and it has its own reserve.
 */
function lowPriorityOnly(candidate: NormalizedCandidate, policy: PlannerPolicy) {
  // No answer recorded means the question was never asked. Everything stays.
  if (Object.keys(policy.personPriority).length === 0) return false;
  if (candidate.personIds.length === 0) return false;
  return !candidate.personIds.some((personId) => policy.personPriority[personId]);
}

/**
 * How many photographs one person may hold, given how much the user wants them.
 *
 * Anyone not named medium keeps the ordinary cap, so a library with no answers
 * recorded behaves exactly as it did before.
 */
function priorityPersonCap(personId: string, baseCap: number, policy: PlannerPolicy) {
  if (policy.personPriority[personId] !== "medium") return baseCap;
  return Math.max(
    policy.minPerPerson,
    Math.ceil(baseCap * policy.mediumPriorityCapFraction),
  );
}

function softFailure(candidate: NormalizedCandidate, policy: PlannerPolicy) {
  if (candidate.quality < policy.qualityFloor) return plannerReason("below_quality_floor", "below the quality floor");
  if (policy.rejectCutFaces && candidate.cutFace) return plannerReason("face_cut", "a face is cut by the frame");
  if (candidate.clippedFraction !== undefined && candidate.clippedFraction > policy.maxClippedFraction) {
    return plannerReason("exposure_clipped", "face exposure is clipped");
  }
  if (candidate.faceExposure !== undefined && candidate.faceExposure < policy.faceExposureFloor) {
    return plannerReason("face_too_dark", "a face is too dark");
  }
  if (candidate.faceSharpness !== undefined && candidate.faceSharpness < policy.faceSharpnessFloor) {
    return plannerReason("face_out_of_focus", "a face is out of focus");
  }
  if (candidate.headSharpness !== undefined && candidate.headSharpness < policy.headSharpnessFloor) {
    return plannerReason("subject_out_of_focus", "the subject is out of focus");
  }
  return undefined;
}

function scarcePersonRescues(
  candidates: NormalizedCandidate[],
  failures: Map<string, PlannerReason>,
) {
  const result = new Set<string>();
  const people = Array.from(new Set(candidates.flatMap((candidate) => candidate.personIds)));
  for (const personId of people) {
    const personCandidates = candidates.filter((candidate) => candidate.personIds.includes(personId));
    if (personCandidates.some((candidate) => !failures.has(candidate.mediaId))) continue;
    const best = personCandidates.sort(
      (left, right) => right.quality - left.quality || left.mediaId.localeCompare(right.mediaId),
    )[0];
    if (best) result.add(best.mediaId);
  }
  return result;
}

function rareMomentIds(candidates: NormalizedCandidate[], policy: PlannerPolicy) {
  const rare = new Set<string>();
  const groupSizes: MutableCounts = {};
  for (const candidate of candidates) {
    increment(groupSizes, candidate.shotGroup || candidate.mediaId);
  }
  for (const candidate of candidates) {
    if ((groupSizes[candidate.shotGroup || candidate.mediaId] ?? 0) > 1) continue;
    const time = validTime(candidate.capturedAt);
    if (time === undefined) continue;
    const hasNeighbour = candidates.some((other) => {
      if (other.mediaId === candidate.mediaId) return false;
      const otherTime = validTime(other.capturedAt);
      return otherTime !== undefined && Math.abs(otherTime - time) <= policy.rareMomentIsolationMs;
    });
    if (!hasNeighbour) rare.add(candidate.mediaId);
  }
  return rare;
}

function qualityStanding(candidates: NormalizedCandidate[]) {
  const classes = new Map<string, NormalizedCandidate[]>();
  for (const candidate of candidates) {
    const key = candidate.comparisonClass || candidate.category || "default";
    const members = classes.get(key) ?? [];
    members.push(candidate);
    classes.set(key, members);
  }
  const standing = new Map<string, number>();
  for (const members of classes.values()) {
    const values = members.map((candidate) => candidate.quality).sort((a, b) => a - b);
    members.forEach((candidate) => {
      const below = values.findIndex((value) => value >= candidate.quality);
      const first = below < 0 ? values.length : below;
      let after = first;
      while (after < values.length && values[after] === candidate.quality) after += 1;
      // Midrank, exactly as axisPercentiles computes it: ties share the middle of
      // the range they span. Without the halving this is a CDF, which hands the
      // top of every class a full 1.0 -- and a comparison class with a single
      // member is ALWAYS the top of its class, so one lone "detail" frame scored
      // 0.42 outranked eight portraits scored 0.85 on the dominant gain term.
      const raw = (first + (after - first) / 2) / members.length;
      standing.set(
        candidate.mediaId,
        quantize((raw * members.length + candidate.quality * STANDING_PRIOR) / (members.length + STANDING_PRIOR)),
      );
    });
  }
  return standing;
}

function groupMoments(candidates: NormalizedCandidate[], policy: PlannerPolicy) {
  const parent = candidates.map((_, index) => index);
  const root = (index: number): number => {
    while (parent[index] !== index) {
      parent[index] = parent[parent[index]];
      index = parent[index];
    }
    return index;
  };
  const union = (left: number, right: number) => {
    const a = root(left);
    const b = root(right);
    if (a !== b) parent[Math.max(a, b)] = Math.min(a, b);
  };
  for (let left = 0; left < candidates.length; left += 1) {
    for (let right = left + 1; right < candidates.length; right += 1) {
      const a = candidates[left];
      const b = candidates[right];
      const at = validTime(a.capturedAt);
      const bt = validTime(b.capturedAt);
      if (at === undefined || bt === undefined || Math.abs(at - bt) > policy.momentWindowMs) continue;
      const similarity = candidateSimilarity(a, b);
      if (similarity !== undefined && similarity >= policy.momentSimilarity) union(left, right);
    }
  }
  const firstId = new Map<number, string>();
  candidates.forEach((candidate, index) => {
    const group = root(index);
    const current = firstId.get(group);
    if (!current || candidate.mediaId < current) firstId.set(group, candidate.mediaId);
  });
  return new Map(
    candidates.map((candidate, index) => [candidate.mediaId, `moment:${firstId.get(root(index))}`]),
  );
}

/**
 * Derives the "this much similarity is normal here" band from the set itself.
 *
 * Photos of one afternoon are all alike; a year of photos is not. A fixed
 * threshold would either punish every photo in a single-event album or excuse
 * every photo in a year's worth, so the median pairwise similarity of the
 * actual candidates becomes the free band and only what exceeds it is charged.
 *
 * This matters twice as much for `semanticSimilarity`, whose raw cosines sit
 * high between any two photographs of people -- an absolute number picked by
 * hand would have been wrong on the first library that disagreed with it.
 */
function calibratedRedundancy(
  candidates: NormalizedCandidate[],
  policy: PlannerPolicy,
  similarityOf: (
    left: NormalizedCandidate,
    right: NormalizedCandidate,
  ) => number | undefined = candidateSimilarity,
  hasSignal: (candidate: NormalizedCandidate) => boolean = (candidate) =>
    Boolean(candidate.embedding),
  ceiling: number = policy.shotSimilarity,
): [number, number] {
  const embedded = candidates.filter(hasSignal).sort(byMediaId);
  let free = policy.redundancyFreeSimilarity;
  if (embedded.length >= 8) {
    const stride = Math.max(1, Math.floor(embedded.length / 48));
    const sample = embedded.filter((_, index) => index % stride === 0).slice(0, 48);
    const similarities: number[] = [];
    for (let left = 0; left < sample.length; left += 1) {
      for (let right = left + 1; right < sample.length; right += 1) {
        const similarity = similarityOf(sample[left], sample[right]);
        if (similarity !== undefined) similarities.push(similarity);
      }
    }
    similarities.sort((a, b) => a - b);
    const median = similarities[Math.floor(similarities.length / 2)];
    if (Number.isFinite(median)) free = Math.max(free, Math.min(median, ceiling));
  }
  return [free, Math.max(ceiling - free, 0.02)];
}

function axisPercentiles(
  candidates: NormalizedCandidate[],
  getValue: (candidate: NormalizedCandidate) => number | undefined,
) {
  const values = candidates
    .map(getValue)
    .filter((value): value is number => value !== undefined && Number.isFinite(value))
    .sort((a, b) => a - b);
  const result = new Map<string, number>();
  for (const candidate of candidates) {
    const value = getValue(candidate);
    if (value === undefined || !Number.isFinite(value) || values.length === 0) {
      result.set(candidate.mediaId, 0.5);
      continue;
    }
    const below = values.findIndex((entry) => entry >= value);
    const first = below < 0 ? values.length : below;
    let after = first;
    while (after < values.length && values[after] === value) after += 1;
    result.set(candidate.mediaId, quantize((first + (after - first) / 2) / values.length));
  }
  return result;
}

function weightsFor(candidate: NormalizedCandidate, policy: PlannerPolicy) {
  return { ...policy, ...(candidate.category ? CATEGORY_OVERRIDES[candidate.category] : undefined) };
}

function perPersonCap(candidates: NormalizedCandidate[], target: number, policy: PlannerPolicy) {
  const people = new Set(candidates.flatMap((candidate) => candidate.personIds));
  if (people.size <= 1) return target;
  return Math.max(policy.minPerPerson, Math.ceil(target * policy.maxPerPersonFraction));
}

function countPeople(selected: string[], byId: Map<string, NormalizedCandidate>) {
  const counts: MutableCounts = {};
  for (const mediaId of selected) {
    for (const personId of byId.get(mediaId)?.personIds ?? []) increment(counts, personId);
  }
  return Object.fromEntries(Object.entries(counts).sort(([left], [right]) => left.localeCompare(right)));
}

function candidateSimilarity(left: NormalizedCandidate, right: NormalizedCandidate) {
  if (!left.embedding || !right.embedding) return undefined;
  if ((left.embeddingSpace || "default") !== (right.embeddingSpace || "default")) return undefined;
  return cosine(left.embedding, right.embedding);
}

/**
 * Scene/background/clothing similarity, from TinyCLIP.
 *
 * No embedding-space check: unlike `embedding`, this field only ever holds one
 * model's output, so there is no second space to confuse it with. That was not
 * true of `embedding`, where the space tag is what stops a 76-dim perceptual
 * vector being compared against a 512-dim semantic one.
 */
function semanticSimilarity(left: NormalizedCandidate, right: NormalizedCandidate) {
  if (!left.semanticEmbedding || !right.semanticEmbedding) return undefined;
  return cosine(left.semanticEmbedding, right.semanticEmbedding);
}

export function cosine(left: readonly number[], right: readonly number[]) {
  if (left.length === 0 || left.length !== right.length) return 0;
  let dot = 0;
  let leftNorm = 0;
  let rightNorm = 0;
  for (let index = 0; index < left.length; index += 1) {
    if (!Number.isFinite(left[index]) || !Number.isFinite(right[index])) return 0;
    dot += left[index] * right[index];
    leftNorm += left[index] * left[index];
    rightNorm += right[index] * right[index];
  }
  if (leftNorm <= Number.EPSILON || rightNorm <= Number.EPSILON) return 0;
  return dot / Math.sqrt(leftNorm * rightNorm);
}

function timeBucket(time: number | undefined, start: number, span: number, bins: number) {
  if (time === undefined) return UNKNOWN;
  if (span <= 0 || bins <= 1) return "time:0";
  const index = Math.min(bins - 1, Math.floor(((time - start) / span) * bins));
  return `time:${Math.max(0, index)}`;
}

function validTime(value?: number) {
  return typeof value === "number" && Number.isFinite(value) && value > 0 ? value : undefined;
}

function peopleKey(candidate: NormalizedCandidate) {
  return candidate.personIds.length > 0 ? candidate.personIds : [NO_PEOPLE];
}

function bucketGain(count: number) {
  return BUCKET_DECAY ** count;
}

function increment(counts: MutableCounts, key: string) {
  counts[key] = (counts[key] ?? 0) + 1;
}

function quantize(value: number) {
  return Math.round(value * 1_000_000) / 1_000_000;
}

function roundSimilarity(value: number) {
  return Math.round(value * 100) / 100;
}

function byMediaId(left: { mediaId: string }, right: { mediaId: string }) {
  return left.mediaId.localeCompare(right.mediaId);
}
