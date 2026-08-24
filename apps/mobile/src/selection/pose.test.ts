// @ts-expect-error Node requires the extension while Metro resolves it too.
import { clusterPoses, makePose, poseDistance, signature, type PoseKeypoint } from "./pose.ts";

function assert(value: unknown, message: string): asserts value {
  if (!value) throw new Error(`Pose self-check failed: ${message}`);
}

function body(mirror = false): PoseKeypoint[] {
  const points: PoseKeypoint[] = [
    [50, 10], [47, 8], [53, 8], [44, 10], [56, 10],
    [40, 30], [60, 30], [32, 48], [68, 48], [25, 65], [75, 65],
    [44, 65], [56, 65], [42, 88], [58, 88], [40, 112], [60, 112],
  ];
  return mirror ? points.map(([x, y]) => [100 - x, y] as PoseKeypoint) : points;
}

const scores = Array<number>(17).fill(0.99);
const straight = makePose(body(), scores);
const mirrored = makePose(body(true), scores);
assert(straight && mirrored, "a fully visible COCO-17 body must produce a signature");
assert(poseDistance(straight, mirrored) < 1e-6, "distance must be mirror invariant");
assert(signature(body(), scores).torso_lean === 0, "an upright torso must have zero lean");

const hidden = scores.slice();
hidden.fill(0, 5);
assert(!makePose(body(), hidden), "a signature with fewer than four dimensions is unusable");

const bentPoints = body();
bentPoints[9] = [38, 43];
const bent = makePose(bentPoints, scores);
assert(bent && poseDistance(straight, bent) > 0, "joint geometry must change pose distance");

const clustered = clusterPoses([
  ["straight", straight],
  ["mirror", mirrored],
  ["missing", undefined],
] as const);
assert(clustered.labels.get("straight") === 0, "first usable pose starts cluster zero");
assert(clustered.labels.get("mirror") === 0, "a mirror joins the same pose cluster");
assert(clustered.labels.get("missing") === -1, "unmeasured pose receives the sentinel label");

