/**
 * Phone port of album-engine/selection.py's coverage objective.
 *
 * The planner deliberately knows nothing about React Native or model loading.
 * It consumes plain, already-measured signals and returns stable media ids, so
 * Phase 2 models can improve the evidence without changing the decision rule.
 */

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
  } = {},
): AlbumPlan {
  if (!Number.isFinite(targetCount) || targetCount < 0) {
    throw new Error(`targetCount=${targetCount} must be a finite non-negative number`);
  }
  const target = Math.floor(targetCount);
  const policy = policyFrom(options.policy);
  validatePolicy(policy);

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
    const reason = absoluteRejection(candidate, excluded);
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

  const greedy = greedySelect(
    survivors,
    byId,
    standing,
    target,
    policy,
    personUniverse,
    pinned,
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
  };
}

function greedySelect(
  survivors: NormalizedCandidate[],
  byId: Map<string, NormalizedCandidate>,
  standing: Map<string, number>,
  target: number,
  policy: PlannerPolicy,
  personUniverse: string[],
  pinned: Set<string>,
) {
  const selected: string[] = [];
  const remaining = survivors.map((candidate) => candidate.mediaId);
  const reasonDetails: Record<string, PlannerReason[]> = {};
  const reasons: Record<string, string[]> = {};
  if (target === 0 || survivors.length === 0) {
    return { selected, capBlocked: [] as string[], reasonDetails, reasons };
  }

  const knownTimes = survivors
    .map((candidate) => validTime(candidate.capturedAt))
    .filter((value): value is number => value !== undefined)
    .sort((a, b) => a - b);
  const start = knownTimes[0] ?? 0;
  const span = knownTimes.length > 0 ? knownTimes[knownTimes.length - 1] - start : 0;
  const bins = policy.timeBins || Math.max(1, Math.min(target, policy.maxTimeBins));
  const timeKey = new Map(
    survivors.map((candidate) => [
      candidate.mediaId,
      timeBucket(validTime(candidate.capturedAt), start, span, bins),
    ]),
  );
  const placeKey = new Map(
    survivors.map((candidate) => [candidate.mediaId, candidate.placeKey || UNKNOWN]),
  );
  const momentOf = groupMoments(survivors, policy);
  const momentKey = new Map(
    survivors.map((candidate) => [
      candidate.mediaId,
      `${momentOf.get(candidate.mediaId)}|faces:${Math.min(candidate.personIds.length, 3)}`,
    ]),
  );
  const poseKey = new Map(
    survivors.map((candidate) => [
      candidate.mediaId,
      candidate.poseCluster ? `pose:${candidate.poseCluster}` : `nopose:${candidate.mediaId}`,
    ]),
  );
  const shotKey = new Map(
    survivors.map((candidate) => [candidate.mediaId, candidate.shotGroup || candidate.mediaId]),
  );
  const familyKey = new Map(
    survivors.map((candidate) => [
      candidate.mediaId,
      candidate.poseFamily || candidate.shotGroup || candidate.mediaId,
    ]),
  );

  const timeCounts: MutableCounts = {};
  const placeCounts: MutableCounts = {};
  const momentCounts: MutableCounts = {};
  const poseCounts: MutableCounts = {};
  const personCounts: MutableCounts = {};
  const shotCounts: MutableCounts = {};
  const familyCounts: MutableCounts = {};
  const closest: MutableCounts = {};
  const personCap = perPersonCap(survivors, target, policy);
  const [redundancyFree, redundancyDenominator] = calibratedRedundancy(
    survivors,
    policy,
  );
  const smilePercentile = axisPercentiles(survivors, (candidate) => candidate.smile);
  const composedPercentile = axisPercentiles(survivors, (candidate) => candidate.composed);
  const aestheticPercentile = axisPercentiles(survivors, (candidate) => candidate.aesthetic);
  const cleanPercentile = axisPercentiles(survivors, (candidate) => candidate.cleanFrame);
  const awakePercentile = axisPercentiles(survivors, (candidate) => candidate.awake);
  const eyesPercentile = axisPercentiles(survivors, (candidate) => candidate.eyesOpen);
  const embracePercentile = axisPercentiles(
    survivors,
    (candidate) => candidate.embraceContext,
  );
  const sleeping = new Map(
    survivors.map((candidate) => [
      candidate.mediaId,
      candidate.sleeping !== undefined &&
        candidate.awake !== undefined &&
        candidate.sleeping > candidate.awake &&
        candidate.sleeping > policy.sleepingMinContrast,
    ]),
  );
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
    value += weights.weightSmile * (smilePercentile.get(mediaId) ?? 0.5);
    value += weights.weightComposed * (composedPercentile.get(mediaId) ?? 0.5);
    value += weights.weightAesthetic * (aestheticPercentile.get(mediaId) ?? 0.5);
    value += weights.weightCleanFrame * (cleanPercentile.get(mediaId) ?? 0.5);
    const blinkSuppressed =
      candidate.embraceContext !== undefined &&
      candidate.embraceContext > 0 &&
      (embracePercentile.get(mediaId) ?? 0.5) >= policy.embraceSuppressPercentile;
    const blink =
      !blinkSuppressed &&
      !sleeping.get(mediaId) &&
      ((candidate.eyesOpen !== undefined &&
        candidate.eyesOpen < 0.5 &&
        (eyesPercentile.get(mediaId) ?? 0.5) < 0.25) ||
        (candidate.eyesOpen === undefined &&
          candidate.awake !== undefined &&
          candidate.awake < 0 &&
          (awakePercentile.get(mediaId) ?? 0.5) < 0.25));
    if (blink) value -= weights.midBlinkPenalty;
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
    for (const mediaId of remaining) {
      const candidate = byId.get(mediaId)!;
      const atCap =
        candidate.personIds.length > 0 &&
        candidate.personIds.some((personId) => (personCounts[personId] ?? 0) >= personCap);
      if (atCap) blocked.add(mediaId);
      if (reservedNow && candidate.personIds.length > 0) continue;
      if (atCap) continue;
      if (sleeping.get(mediaId) && sleepingCount >= sleepingCap) continue;
      eligible.push(mediaId);
    }
    if (eligible.length === 0) break;

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

  return { selected, capBlocked: Array.from(blocked).sort(), reasonDetails, reasons };
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

function absoluteRejection(candidate: NormalizedCandidate, excluded: Set<string>) {
  if (excluded.has(candidate.mediaId)) return plannerReason("user_excluded", "excluded by user");
  if (candidate.hardRejected) return plannerReason("hard_image_gate", candidate.hardRejectionReason || "failed a hard image gate");
  if (candidate.screenshotDocument) return plannerReason("screenshot", "screenshot or document");
  return undefined;
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
      const raw = (first + (after - first)) / members.length;
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

function calibratedRedundancy(candidates: NormalizedCandidate[], policy: PlannerPolicy): [number, number] {
  const embedded = candidates.filter((candidate) => candidate.embedding).sort(byMediaId);
  let free = policy.redundancyFreeSimilarity;
  if (embedded.length >= 8) {
    const stride = Math.max(1, Math.floor(embedded.length / 48));
    const sample = embedded.filter((_, index) => index % stride === 0).slice(0, 48);
    const similarities: number[] = [];
    for (let left = 0; left < sample.length; left += 1) {
      for (let right = left + 1; right < sample.length; right += 1) {
        const similarity = candidateSimilarity(sample[left], sample[right]);
        if (similarity !== undefined) similarities.push(similarity);
      }
    }
    similarities.sort((a, b) => a - b);
    const median = similarities[Math.floor(similarities.length / 2)];
    if (Number.isFinite(median)) free = Math.max(free, Math.min(median, policy.shotSimilarity));
  }
  return [free, Math.max(policy.shotSimilarity - free, 0.02)];
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
