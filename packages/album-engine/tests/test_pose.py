"""Deterministic unit tests for the pure pose geometry.

No RTMO, no images: known keypoints in, known angles/clusters out. The stage
that runs the model is tested end-to-end elsewhere; this proves the arithmetic
the selection engine's pose-diversity term ultimately depends on.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

# Plain source tree rather than an installed distribution, so the package dir is
# put on the path the same way the rest of this suite does it (see test_selection).
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from memory_engine_album import pose  # noqa: E402


def _full_scores(value: float = 1.0) -> list[float]:
    return [value] * 17


def _straight_arm_kpts() -> list[list[float]]:
    """A skeleton with a right-angle left elbow and known shoulder/hip angles.

    Coordinates are in image space (y grows down). Only the joints the ANGLES
    table reads need to be sensible; the rest are placed so the torso is upright.
    """
    kpts = [[0.0, 0.0] for _ in range(17)]
    # Left arm: shoulder (5), elbow (7), wrist (9) forming a 90-degree elbow.
    kpts[pose.KP["l_sho"]] = [100.0, 100.0]
    kpts[pose.KP["l_elb"]] = [100.0, 150.0]   # straight down from shoulder
    kpts[pose.KP["l_wri"]] = [150.0, 150.0]   # straight right from elbow -> 90 deg
    # Right arm mirrored across x = 100.
    kpts[pose.KP["r_sho"]] = [100.0, 100.0]
    kpts[pose.KP["r_elb"]] = [100.0, 150.0]
    kpts[pose.KP["r_wri"]] = [50.0, 150.0]
    # Hips straight below shoulders -> upright torso, straight (180 deg) hips.
    kpts[pose.KP["l_hip"]] = [100.0, 200.0]
    kpts[pose.KP["r_hip"]] = [100.0, 200.0]
    kpts[pose.KP["l_kne"]] = [100.0, 250.0]
    kpts[pose.KP["r_kne"]] = [100.0, 250.0]
    kpts[pose.KP["l_ank"]] = [100.0, 300.0]
    kpts[pose.KP["r_ank"]] = [100.0, 300.0]
    return kpts


def test_known_keypoints_yield_expected_angles():
    sig = pose.signature(_straight_arm_kpts(), _full_scores())
    # 90-degree elbow.
    assert math.isclose(sig["l_elbow"], 90.0, abs_tol=1e-6)
    assert math.isclose(sig["r_elbow"], 90.0, abs_tol=1e-6)
    # Shoulder->elbow is straight down, shoulder->hip is straight down: the
    # interior angle at the shoulder between arm and torso is 0.
    assert math.isclose(sig["l_shoulder"], 0.0, abs_tol=1e-6)
    # hip->shoulder is up, hip->knee is down: a straight 180-degree hip.
    assert math.isclose(sig["l_hip"], 180.0, abs_tol=1e-6)
    # Upright torso: mid-shoulder directly above mid-hip -> ~0 lean.
    assert math.isclose(sig["torso_lean"], 0.0, abs_tol=1e-6)


def test_degenerate_angle_is_absent_not_zero():
    kpts = _straight_arm_kpts()
    # Collapse the wrist onto the elbow: the elbow angle is undefined and must
    # simply not appear, rather than being reported as some default.
    kpts[pose.KP["l_wri"]] = list(kpts[pose.KP["l_elb"]])
    sig = pose.signature(kpts, _full_scores())
    assert "l_elbow" not in sig


def test_low_confidence_keypoints_drop_their_dims():
    scores = _full_scores()
    scores[pose.KP["l_wri"]] = 0.1  # below _MIN_KP_SCORE
    sig = pose.signature(_straight_arm_kpts(), scores)
    assert "l_elbow" not in sig  # needs the wrist
    assert "r_elbow" in sig      # unaffected


def test_make_returns_none_below_four_valid_joints():
    # Only a right-angle elbow is measurable; everything else has zero score.
    scores = [0.0] * 17
    for j in ("l_sho", "l_elb", "l_wri"):
        scores[pose.KP[j]] = 1.0
    assert pose.make(_straight_arm_kpts(), scores) is None
    # With the full body visible there are >= 4 dims and a pose is produced.
    assert pose.make(_straight_arm_kpts(), _full_scores()) is not None


def test_distance_is_mirror_invariant():
    kpts = _straight_arm_kpts()
    scores = _full_scores()
    base = pose.make(kpts, scores)
    # Physically mirror the body: swap every left/right keypoint AND its score.
    mk = [kpts[pose.MIRROR[i]] for i in range(17)]
    ms = [scores[pose.MIRROR[i]] for i in range(17)]
    flipped = pose.make(mk, ms)
    # A pose and its left-right mirror are the same pose for variety.
    assert pose.distance(base, flipped) < 1e-6
    # ...and a pose against itself is distance zero.
    assert pose.distance(base, base) < 1e-6


def test_distance_grows_with_a_real_pose_change():
    base = pose.make(_straight_arm_kpts(), _full_scores())
    bent = _straight_arm_kpts()
    # Straighten the left elbow from 90 to ~180 degrees.
    bent[pose.KP["l_wri"]] = [100.0, 200.0]
    other = pose.make(bent, _full_scores())
    assert pose.distance(base, other) > 10.0


def test_clustering_is_deterministic_and_conservative():
    a = pose.make(_straight_arm_kpts(), _full_scores())
    # A near-identical copy (tiny jitter) should land in the same cluster.
    near_kpts = _straight_arm_kpts()
    near_kpts[pose.KP["l_wri"]] = [151.0, 150.0]
    near = pose.make(near_kpts, _full_scores())
    # A clearly different pose (straight left arm) starts its own cluster.
    far_kpts = _straight_arm_kpts()
    far_kpts[pose.KP["l_wri"]] = [100.0, 200.0]
    far = pose.make(far_kpts, _full_scores())

    items = [("a", a), ("near", near), ("far", far), ("none", None)]
    labels, groups = pose.cluster(items, threshold=15.0)

    assert labels["a"] == labels["near"]      # merged: within threshold
    assert labels["a"] != labels["far"]       # split: distinct pose
    assert labels["none"] == -1               # no usable body
    # Two real clusters, and the run is repeatable to the same labels.
    assert len([g for g in groups]) == 2
    labels2, _ = pose.cluster(items, threshold=15.0)
    assert labels == labels2


def test_clustering_over_splits_rather_than_merges():
    # Three poses each ~12 degrees apart in elbow angle at threshold 8 must NOT
    # all collapse into one bucket -- the conservative property.
    poses = []
    for i, wrist_y in enumerate((150.0, 165.0, 180.0)):
        k = _straight_arm_kpts()
        k[pose.KP["l_wri"]] = [130.0, wrist_y]
        poses.append((f"p{i}", pose.make(k, _full_scores())))
    labels, groups = pose.cluster(poses, threshold=8.0)
    assert len(groups) >= 2  # did not merge everything


if __name__ == "__main__":  # pragma: no cover - runnable self-check
    import sys

    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
    print(f"ok: {len(fns)} pose self-checks passed")
    sys.exit(0)
