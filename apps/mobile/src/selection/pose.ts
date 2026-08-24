/** Dependency-free port of album-engine/pose.py. */

export const KP = {
  nose: 0,
  l_eye: 1,
  r_eye: 2,
  l_ear: 3,
  r_ear: 4,
  l_sho: 5,
  r_sho: 6,
  l_elb: 7,
  r_elb: 8,
  l_wri: 9,
  r_wri: 10,
  l_hip: 11,
  r_hip: 12,
  l_kne: 13,
  r_kne: 14,
  l_ank: 15,
  r_ank: 16,
} as const;

export const MIRROR = [0, 2, 1, 4, 3, 6, 5, 8, 7, 10, 9, 12, 11, 14, 13, 16, 15] as const;

export const ANGLES = [
  ["l_elbow", [5, 7, 9]],
  ["r_elbow", [6, 8, 10]],
  ["l_shoulder", [7, 5, 11]],
  ["r_shoulder", [8, 6, 12]],
  ["l_hip", [5, 11, 13]],
  ["r_hip", [6, 12, 14]],
  ["l_knee", [11, 13, 15]],
  ["r_knee", [12, 14, 16]],
] as const;

const MIN_KEYPOINT_SCORE = 0.3;
const MIN_DIMS = 4;

export type PoseKeypoint = readonly [number, number];
export type PoseAngles = Record<string, number>;
export type Pose = { sig: PoseAngles; mirror: PoseAngles };

function angle(a: PoseKeypoint, b: PoseKeypoint, c: PoseKeypoint) {
  const first: PoseKeypoint = [a[0] - b[0], a[1] - b[1]];
  const second: PoseKeypoint = [c[0] - b[0], c[1] - b[1]];
  const firstNorm = Math.hypot(...first);
  const secondNorm = Math.hypot(...second);
  if (firstNorm < 1e-6 || secondNorm < 1e-6) return undefined;
  const cosine = Math.max(
    -1,
    Math.min(1, (first[0] * second[0] + first[1] * second[1]) / (firstNorm * secondNorm)),
  );
  return (Math.acos(cosine) * 180) / Math.PI;
}

function midpoint(keypoints: readonly PoseKeypoint[], left: number, right: number): PoseKeypoint {
  return [
    (keypoints[left][0] + keypoints[right][0]) / 2,
    (keypoints[left][1] + keypoints[right][1]) / 2,
  ];
}

export function signature(
  keypoints: readonly PoseKeypoint[],
  scores: readonly number[],
): PoseAngles {
  if (keypoints.length !== 17 || scores.length !== 17) return {};
  const visible = scores.map((score) => Number.isFinite(score) && score >= MIN_KEYPOINT_SCORE);
  const result: PoseAngles = {};
  for (const [name, [a, b, c]] of ANGLES) {
    if (!visible[a] || !visible[b] || !visible[c]) continue;
    const measured = angle(keypoints[a], keypoints[b], keypoints[c]);
    if (measured !== undefined) result[name] = measured;
  }
  if (visible[5] && visible[6] && visible[11] && visible[12]) {
    const shoulder = midpoint(keypoints, 5, 6);
    const hip = midpoint(keypoints, 11, 12);
    const dx = hip[0] - shoulder[0];
    const dy = hip[1] - shoulder[1];
    if (Math.hypot(dx, dy) >= 1e-6) {
      result.torso_lean = Math.abs((Math.atan2(dx, dy) * 180) / Math.PI);
    }
  }
  return result;
}

function mirrorSignature(keypoints: readonly PoseKeypoint[], scores: readonly number[]) {
  return signature(
    MIRROR.map((index) => keypoints[index]),
    MIRROR.map((index) => scores[index]),
  );
}

export function makePose(
  keypoints: readonly PoseKeypoint[],
  scores: readonly number[],
): Pose | undefined {
  const base = signature(keypoints, scores);
  if (Object.keys(base).length < MIN_DIMS) return undefined;
  return { sig: base, mirror: mirrorSignature(keypoints, scores) };
}

function rms(left: PoseAngles, right: PoseAngles) {
  const keys = Object.keys(left).filter((key) => Object.hasOwn(right, key));
  if (keys.length < MIN_DIMS) return undefined;
  return Math.sqrt(
    keys.reduce((sum, key) => sum + (left[key] - right[key]) ** 2, 0) / keys.length,
  );
}

export function poseDistance(left: Pose, right: Pose) {
  const values = [rms(left.sig, right.sig), rms(left.sig, right.mirror)].filter(
    (value): value is number => value !== undefined,
  );
  return values.length > 0 ? Math.min(...values) : 999;
}

export function clusterPoses<Key>(
  items: readonly (readonly [Key, Pose | undefined])[],
  threshold = 22,
) {
  const clusters: Array<{ members: Key[]; reps: Pose[] }> = [];
  const labels = new Map<Key, number>();
  for (const [key, pose] of items) {
    if (!pose) {
      labels.set(key, -1);
      continue;
    }
    let bestCluster = -1;
    let bestDistance = Number.POSITIVE_INFINITY;
    clusters.forEach((cluster, index) => {
      const distance = Math.min(...cluster.reps.map((representative) => poseDistance(pose, representative)));
      if (distance <= threshold && distance < bestDistance) {
        bestCluster = index;
        bestDistance = distance;
      }
    });
    if (bestCluster < 0) {
      clusters.push({ members: [key], reps: [pose] });
      labels.set(key, clusters.length - 1);
    } else {
      clusters[bestCluster].members.push(key);
      clusters[bestCluster].reps.push(pose);
      labels.set(key, bestCluster);
    }
  }
  return { labels, clusters: clusters.map((cluster) => cluster.members.slice()) };
}

