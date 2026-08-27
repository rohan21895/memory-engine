/**
 * Does the pose cap actually stop the same person appearing in the same pose?
 *
 * That is the stated product goal for pose detection, and it is NOT the framing
 * tie-break that was deleted -- that one ranked how much of a body a frame held.
 * This is the deduplication path: `clusterPoses` -> `poseCluster` -> the
 * planner's `poseKey` -> `maxPerBodyPose`.
 *
 * Three things could each break it independently, so all three are counted:
 *   1. photos with no readable pose get `nopose:<mediaId>`, a key unique to that
 *      photo, so they are never capped at all;
 *   2. the key is the pose ALONE, so it neither targets one person's repeats nor
 *      spares two different people who happen to stand alike;
 *   3. the cap relaxes (`allowedPerPose += 1`) whenever nothing else is
 *      eligible, so it is a preference under pressure, not a limit.
 */
// @ts-expect-error Node's TypeScript runner requires the source extension.
import { albumFixtures } from "../../apps/mobile/src/selection/album-fixtures.ts";
// @ts-expect-error Node's TypeScript runner requires the source extension.
import { planAlbum } from "../../apps/mobile/src/selection/album-planner.ts";

function tally<T>(values: readonly T[]): Map<T, number> {
  const counts = new Map<T, number>();
  for (const value of values) counts.set(value, (counts.get(value) ?? 0) + 1);
  return counts;
}

const MAX_PER_BODY_POSE = 2;

for (const fixture of albumFixtures()) {
  const plan = planAlbum(fixture.candidates, fixture.target);
  const byId = new Map(fixture.candidates.map((c: any) => [c.mediaId, c]));
  const chosen = plan.selectedIds.map((id: string) => byId.get(id));

  // What the planner actually keys on.
  const poseKeyOf = (c: any): string =>
    c.poseCluster ? `pose:${c.poseCluster}` : `nopose:${c.mediaId}`;

  const readable = chosen.filter((c: any) => c.poseCluster);
  const poseCounts = tally(readable.map(poseKeyOf));
  const overCap = [...poseCounts.entries()].filter(([, n]) => n > MAX_PER_BODY_POSE);

  // The stated goal: the SAME PERSON, twice, in the same pose. `personIds` is
  // what the planner already carries, so this needs no face work.
  const personPose: string[] = [];
  for (const c of readable) {
    for (const person of c.personIds ?? []) personPose.push(`${person}|${poseKeyOf(c)}`);
  }
  const personPoseCounts = tally(personPose);
  const repeats = [...personPoseCounts.entries()].filter(([, n]) => n > 1);

  // And in the whole candidate pool, so an empty result above can be told apart
  // from "the pool never offered one".
  const poolPersonPose: string[] = [];
  for (const c of fixture.candidates as any[]) {
    if (!c.poseCluster) continue;
    for (const person of c.personIds ?? []) poolPersonPose.push(`${person}|${poseKeyOf(c)}`);
  }
  const poolRepeats = [...tally(poolPersonPose).entries()].filter(([, n]) => n > 1);

  // WITHOUT THIS THE NUMBERS ABOVE MEAN NOTHING. These fixtures label pose per
  // moment from a four-word vocabulary (standing / seated / close / held), so
  // the album cannot be filled without exceeding the cap: 24 photos into 4
  // buckets at 2 each tops out at 8. Relaxation is then forced and CORRECT, and
  // an "over the cap" count measures the fixture, not the planner. Any run
  // where `capacity < chosen` should be read as unmeasurable, not as a defect.
  const distinctPoses = new Set(readable.map(poseKeyOf)).size;
  const capacity = distinctPoses * MAX_PER_BODY_POSE;
  const meaningful = capacity >= chosen.length;

  console.log(
    `${fixture.name}: ${chosen.length} chosen | ` +
      `no readable pose ${chosen.length - readable.length}/${chosen.length} (never capped) | ` +
      `${distinctPoses} distinct poses -> capacity ${capacity} at a cap of ${MAX_PER_BODY_POSE}` +
      (meaningful ? "" : "  <-- BELOW the album size, so the cap MUST relax; verdict below is unmeasurable here") +
      `\n    pose buckets over the cap: ${overCap.length}` +
      (overCap.length ? ` (worst ${Math.max(...overCap.map(([, n]) => n))})` : "") +
      ` | SAME PERSON in the same pose twice+: ${repeats.length} in the album, ` +
      `${poolRepeats.length} available in the pool`,
  );
}
