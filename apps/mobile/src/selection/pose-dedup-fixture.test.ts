// @ts-expect-error Node's TypeScript runner requires the source extension.
import { planAlbum } from "./album-planner.ts";
// @ts-expect-error Node's TypeScript runner requires the source extension.
import { DIFFERENT_PEOPLE_SAME_POSE_IDS, NO_POSE_SAME_PERSON_IDS, POSE_DEDUP_CAP, POSE_DEDUP_ISOLATION_POLICY, poseDedupFixture } from "./pose-dedup-fixture.ts";
import type { PlannerCandidate } from "./album-planner";

function assert(value: unknown, message: string): asserts value {
  if (!value) throw new Error(`Pose dedup fixture self-check failed: ${message}`);
}

function selectedCount(ids: readonly string[], wanted: readonly string[]) {
  const selected = new Set(ids);
  return wanted.filter((mediaId) => selected.has(mediaId)).length;
}

function poseAudit(ids: readonly string[], candidates: readonly PlannerCandidate[]) {
  const byId = new Map(candidates.map((candidate) => [candidate.mediaId, candidate]));
  const poseCounts = new Map<string, number>();
  const readable = ids
    .map((mediaId) => byId.get(mediaId))
    .filter((candidate): candidate is PlannerCandidate => candidate?.poseCluster !== undefined);
  for (const candidate of readable) {
    const pose = candidate.poseCluster!;
    poseCounts.set(pose, (poseCounts.get(pose) ?? 0) + 1);
  }
  return {
    distinctPoses: poseCounts.size,
    capacity: poseCounts.size * POSE_DEDUP_CAP,
    worstBucket: poseCounts.size > 0 ? Math.max(...poseCounts.values()) : 0,
  };
}

const fixture = poseDedupFixture();
const poolPoses = new Set(
  fixture.candidates.flatMap((candidate) => candidate.poseCluster ? [candidate.poseCluster] : []),
);
const poolCapacity = poolPoses.size * POSE_DEDUP_CAP;

assert(fixture.candidates.length === 30, "the fixture must contain all thirty intended candidates");
assert(poolPoses.size === 13, "the fixture must carry thirteen distinct readable postures");
assert(
  poolCapacity > fixture.target,
  `fixture capacity ${poolCapacity} must exceed its ${fixture.target}-photo album target`,
);
assert(
  DIFFERENT_PEOPLE_SAME_POSE_IDS.every((mediaId) =>
    fixture.candidates.some((candidate) => candidate.mediaId === mediaId),
  ),
  "all three different-person pose collisions must exist",
);
assert(
  NO_POSE_SAME_PERSON_IDS.every((mediaId) =>
    fixture.candidates.some(
      (candidate) =>
        candidate.mediaId === mediaId &&
        candidate.poseCluster === undefined &&
        candidate.personIds?.length === 1 &&
        candidate.personIds[0] === "ava",
    ),
  ),
  "all three no-pose Ava photos must exist and really lack a pose",
);

const plan = planAlbum(fixture.candidates, fixture.target, {
  policy: POSE_DEDUP_ISOLATION_POLICY,
});
const audit = poseAudit(plan.selectedIds, fixture.candidates);

assert(plan.selectedIds.length === fixture.target, "the fixture must fill the requested album");
assert(
  audit.capacity > plan.selectedIds.length,
  "the selected readable poses must still have enough capacity for the cap to hold",
);
assert(audit.worstBucket === POSE_DEDUP_CAP, "the body-pose cap must bind without relaxing");
assert(
  selectedCount(plan.selectedIds, DIFFERENT_PEOPLE_SAME_POSE_IDS) === POSE_DEDUP_CAP,
  "the pose-only key must make three different people in one posture compete for two slots",
);
assert(
  selectedCount(plan.selectedIds, NO_POSE_SAME_PERSON_IDS) === NO_POSE_SAME_PERSON_IDS.length,
  "unique nopose:<mediaId> keys must exempt all three same-person photos from the cap",
);

const byId = new Map(fixture.candidates.map((candidate) => [candidate.mediaId, candidate]));
const omittedCollision = DIFFERENT_PEOPLE_SAME_POSE_IDS.find(
  (mediaId) => !plan.selectedIds.includes(mediaId),
);
assert(omittedCollision !== undefined, "one high-quality different-person collision must be omitted");
assert(
  plan.selectedIds.some((mediaId) => byId.get(mediaId)!.quality < byId.get(omittedCollision)!.quality),
  "the collision must lose to a lower-quality photo because the pose-only cap bound",
);

// SABOTAGE 1: widen only the cap. The same assertion above must now fail, and
// selecting the third collision proves the sabotage reached the planner.
const widened = planAlbum(fixture.candidates, fixture.target, {
  policy: { ...POSE_DEDUP_ISOLATION_POLICY, maxPerBodyPose: POSE_DEDUP_CAP + 1 },
});
const widenedAudit = poseAudit(widened.selectedIds, fixture.candidates);
assert(
  selectedCount(widened.selectedIds, DIFFERENT_PEOPLE_SAME_POSE_IDS) === POSE_DEDUP_CAP + 1,
  "SABOTAGE must actually admit the third different-person pose collision",
);
assert(
  widenedAudit.worstBucket > POSE_DEDUP_CAP,
  "VACUITY: widening the cap must make the original worst-bucket assertion fail",
);

// SABOTAGE 2: give the three formerly-unreadable photos one shared readable
// pose. If their unique fallback keys caused the exemption, one must now lose.
const noPoseIds = new Set<string>(NO_POSE_SAME_PERSON_IDS);
const readableFallback = fixture.candidates.map((candidate) =>
  noPoseIds.has(candidate.mediaId)
    ? { ...candidate, poseCluster: "movenet:nopose-sabotage" }
    : candidate,
);
assert(
  readableFallback.filter((candidate) => noPoseIds.has(candidate.mediaId)).every(
    (candidate) => candidate.poseCluster === "movenet:nopose-sabotage",
  ),
  "SABOTAGE must actually assign one common pose to all three fallback photos",
);
const cappedFallback = planAlbum(readableFallback, fixture.target, {
  policy: POSE_DEDUP_ISOLATION_POLICY,
});
assert(
  selectedCount(cappedFallback.selectedIds, NO_POSE_SAME_PERSON_IDS) <
    selectedCount(plan.selectedIds, NO_POSE_SAME_PERSON_IDS),
  "VACUITY: a common readable pose must make the original no-pose assertion fail",
);

// eslint-disable-next-line no-console
console.log("pose dedup fixture self-check passed");
