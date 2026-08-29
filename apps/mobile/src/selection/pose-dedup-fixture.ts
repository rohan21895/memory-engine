import type { AlbumFixture } from "./album-fixtures";
import type { PlannerCandidate, PlannerPolicy } from "./album-planner";

/**
 * A pose fixture whose cap can actually hold.
 *
 * The production album target is 24. Thirteen raw MoveNet clusters become
 * fifteen identity-scoped pose buckets: the arms-crossed cluster belongs to
 * three different people, while the hands-on-hips cluster repeats for Bo.
 * Their capacity is 15 * 2 = 30, so unlike the event fixtures' four-word pose
 * vocabulary, this pool does not force the planner to relax `maxPerBodyPose`
 * merely to fill the album.
 *
 * Three deliberately awkward shapes make the current key observable:
 *   - three different people have the same arms-crossed pose;
 *   - three photos repeat Bo's same hands-on-hips pose;
 *   - three photos of Ava have no readable pose at all.
 * The remaining clusters are two separate takes of one subject in the same
 * posture, as a real portrait session commonly produces.
 */

export const POSE_DEDUP_CAP = 2;

export const DIFFERENT_PEOPLE_SAME_POSE_IDS = [
  "pose-arms-crossed-ava",
  "pose-arms-crossed-bo",
  "pose-arms-crossed-cy",
] as const;

export const SAME_PERSON_SAME_POSE_IDS = [
  "pose-hands-on-hips-bo-a",
  "pose-hands-on-hips-bo-b",
  "pose-hands-on-hips-bo-c",
] as const;

export const NO_POSE_SAME_PERSON_IDS = [
  "nopose-ava-00",
  "nopose-ava-01",
  "nopose-ava-02",
] as const;

const POSTURES = [
  "arms-crossed",
  "hands-on-hips",
  "one-hand-wave",
  "both-hands-raised",
  "left-lunge",
  "right-lunge",
  "kneeling",
  "seated-forward",
  "seated-reclined",
  "walking-stride",
  "looking-over-shoulder",
  "leaning-on-railing",
  "holding-an-object",
] as const;

function portrait(
  mediaId: string,
  quality: number,
  personId: string,
  poseCluster?: string,
): PlannerCandidate {
  return {
    mediaId,
    quality,
    personIds: [personId],
    poseCluster,
    comparisonClass: "portrait",
    shotGroup: `take:${mediaId}`,
    poseFamily: `take:${mediaId}`,
  };
}

export function poseDedupFixture(): AlbumFixture {
  const candidates: PlannerCandidate[] = [
    portrait(DIFFERENT_PEOPLE_SAME_POSE_IDS[0], 0.999, "ava", "movenet:0"),
    portrait(DIFFERENT_PEOPLE_SAME_POSE_IDS[1], 0.998, "bo", "movenet:0"),
    portrait(DIFFERENT_PEOPLE_SAME_POSE_IDS[2], 0.997, "cy", "movenet:0"),
    portrait(SAME_PERSON_SAME_POSE_IDS[0], 0.996, "bo", "movenet:1"),
    portrait(SAME_PERSON_SAME_POSE_IDS[1], 0.995, "bo", "movenet:1"),
    portrait(SAME_PERSON_SAME_POSE_IDS[2], 0.994, "bo", "movenet:1"),
  ];

  for (let index = 2; index < POSTURES.length; index += 1) {
    const posture = POSTURES[index];
    const person = ["ava", "bo", "cy"][index % 3];
    candidates.push(
      portrait(`pose-${posture}-${person}-a`, 0.98 - index * 0.001, person, `movenet:${index}`),
      portrait(`pose-${posture}-${person}-b`, 0.82 - index * 0.001, person, `movenet:${index}`),
    );
  }

  candidates.push(
    portrait(NO_POSE_SAME_PERSON_IDS[0], 0.96, "ava"),
    portrait(NO_POSE_SAME_PERSON_IDS[1], 0.959, "ava"),
    portrait(NO_POSE_SAME_PERSON_IDS[2], 0.958, "ava"),
  );

  return { name: "pose-dedup-capacity", target: 24, candidates };
}

/**
 * Remove every competing planner preference so the fixture measures the pose
 * key and cap, not time/place/person coverage or semantic redundancy.
 */
export const POSE_DEDUP_ISOLATION_POLICY: Partial<PlannerPolicy> = {
  qualityFloor: 0,
  minPerPerson: 0,
  maxPerPersonFraction: 1,
  minNonPeopleFraction: 0,
  maxPerPoseFamily: 99,
  weightTime: 0,
  weightPlace: 0,
  weightMoment: 0,
  weightPose: 0,
  weightPerson: 0,
  weightRedundancy: 0,
  weightSmile: 0,
  weightComposed: 0,
  weightAesthetic: 0,
  weightCleanFrame: 0,
  midBlinkPenalty: 0,
};
