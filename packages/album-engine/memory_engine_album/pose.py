"""Pose signatures from RTMO keypoints: the geometry behind "find the poses".

17 COCO keypoints per person -> a scale/translation-free joint-angle vector ->
mirror-invariant distance -> conservative greedy clustering. The same shape as
the face clustering, computed on geometry instead of embeddings.

Deliberately dependency-free: pure Python + `math`, no numpy/cv2. The stage that
runs RTMO (services/pipeline) owns the model and hands this module plain lists,
so this half stays importable, testable and deterministic on its own -- and the
selection engine that consumes the resulting clusters never drags in an
inference runtime to read them.
"""
from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Optional

__all__ = [
    "KP",
    "MIRROR",
    "ANGLES",
    "signature",
    "make",
    "distance",
    "cluster",
]

# COCO-17 keypoint order. The index every RTMO person tensor uses.
KP = {
    "nose": 0, "l_eye": 1, "r_eye": 2, "l_ear": 3, "r_ear": 4,
    "l_sho": 5, "r_sho": 6, "l_elb": 7, "r_elb": 8, "l_wri": 9, "r_wri": 10,
    "l_hip": 11, "r_hip": 12, "l_kne": 13, "r_kne": 14, "l_ank": 15, "r_ank": 16,
}
# Left<->right index swap, so a pose and its mirror can be compared without
# re-running the detector on a flipped image.
MIRROR = {0: 0, 1: 2, 2: 1, 3: 4, 4: 3, 5: 6, 6: 5, 7: 8, 8: 7,
          9: 10, 10: 9, 11: 12, 12: 11, 13: 14, 14: 13, 15: 16, 16: 15}

# Each signature dim is the interior angle at joint `b` formed by (a, b, c).
ANGLES = [
    ("l_elbow", (5, 7, 9)),
    ("r_elbow", (6, 8, 10)),
    ("l_shoulder", (7, 5, 11)),
    ("r_shoulder", (8, 6, 12)),
    ("l_hip", (5, 11, 13)),
    ("r_hip", (6, 12, 14)),
    ("l_knee", (11, 13, 15)),
    ("r_knee", (12, 14, 16)),
]

# A keypoint below this detector confidence is treated as unseen: its angle is
# simply absent from the signature rather than contributing a guessed position.
_MIN_KP_SCORE = 0.3
# A signature with fewer than this many measured dims is too thin to compare --
# make() returns None and the frame carries no pose.
_MIN_DIMS = 4


def _angle(a, b, c) -> Optional[float]:
    """Interior angle at b in degrees, or None if either arm is degenerate."""
    v1 = (a[0] - b[0], a[1] - b[1])
    v2 = (c[0] - b[0], c[1] - b[1])
    n1 = math.hypot(*v1)
    n2 = math.hypot(*v2)
    if n1 < 1e-6 or n2 < 1e-6:
        return None
    cos = max(-1.0, min(1.0, (v1[0] * v2[0] + v1[1] * v2[1]) / (n1 * n2)))
    return math.degrees(math.acos(cos))


def _mid(kpts, i, j):
    return ((kpts[i][0] + kpts[j][0]) / 2, (kpts[i][1] + kpts[j][1]) / 2)


def signature(kpts: Sequence[Sequence[float]], scores: Sequence[float]) -> dict[str, float]:
    """{name: angle-in-degrees} over the confidently-seen joints, plus torso lean.

    Missing dims are simply absent; `distance()` compares only the dims both
    signatures carry, so a partially-occluded body still contributes what it has.
    """
    ok = [s >= _MIN_KP_SCORE for s in scores]
    sig: dict[str, float] = {}
    for name, (a, b, c) in ANGLES:
        if ok[a] and ok[b] and ok[c]:
            ang = _angle(kpts[a], kpts[b], kpts[c])
            if ang is not None:
                sig[name] = ang
    # Torso lean: mid-shoulder -> mid-hip against the vertical (image y grows
    # down). 0 = upright, 90 = horizontal. This is the sit/stand/lie axis the
    # image embedding cannot see through a change of backdrop.
    if ok[5] and ok[6] and ok[11] and ok[12]:
        msho = _mid(kpts, 5, 6)
        mhip = _mid(kpts, 11, 12)
        dx, dy = mhip[0] - msho[0], mhip[1] - msho[1]
        if math.hypot(dx, dy) > 1e-6:
            sig["torso_lean"] = abs(math.degrees(math.atan2(dx, dy)))
    return sig


def _mirror_signature(kpts, scores) -> dict[str, float]:
    mk = [kpts[MIRROR[i]] for i in range(17)]
    ms = [scores[MIRROR[i]] for i in range(17)]
    return signature(mk, ms)


def make(kpts: Sequence[Sequence[float]], scores: Sequence[float]) -> Optional[dict]:
    """Both the signature and its mirror, so distance is mirror-invariant without
    recomputing. Returns None when too little of the body is visible to judge."""
    base = signature(kpts, scores)
    if len(base) < _MIN_DIMS:
        return None
    return {"sig": base, "mirror": _mirror_signature(kpts, scores)}


def _rms(a: dict, b: dict) -> Optional[float]:
    keys = a.keys() & b.keys()
    if len(keys) < _MIN_DIMS:  # too little overlap to judge
        return None
    return math.sqrt(sum((a[k] - b[k]) ** 2 for k in keys) / len(keys))


def distance(pa: dict, pb: dict) -> float:
    """Mirror-invariant RMS joint-angle distance in degrees.

    A pose and its left-right mirror are the same pose for variety purposes, so
    the distance is the smaller of (sig vs sig) and (sig vs mirror). 999.0 stands
    for "no comparable overlap" -- above any sane threshold, so such a pair never
    merges.
    """
    cands = [
        _rms(pa["sig"], pb["sig"]),
        _rms(pa["sig"], pb["mirror"]),
    ]
    cands = [c for c in cands if c is not None]
    return min(cands) if cands else 999.0


def cluster(items, threshold: float = 22.0):
    """Greedy single-pass clustering in the caller's order (caller sorts for
    determinism).

    A frame joins the nearest existing cluster whose single-linkage distance is
    within `threshold` degrees, else it starts a new cluster. Conservative on
    purpose: over-splitting beats under-splitting, because a hidden pose is
    invisible while two near-identical clusters are a one-tap merge in review.

    items: list of (key, pose) where pose is make()'s output (or None).
    Returns: ({key: cluster_id}, [clusters as member-key lists]).
    A key whose pose is None gets label -1 (no usable body).
    """
    clusters: list[dict] = []  # each: {"members": [key], "reps": [pose]}
    labels: dict = {}
    for key, pose in items:
        if pose is None:
            labels[key] = -1
            continue
        best_i, best_d = None, threshold
        for i, cl in enumerate(clusters):
            # Distance to a cluster is the min distance to any of its members
            # (single-linkage), capped at a small sample for speed.
            d = min(distance(pose, r) for r in cl["reps"][:8])
            if d < best_d:
                best_i, best_d = i, d
        if best_i is None:
            clusters.append({"members": [key], "reps": [pose]})
            labels[key] = len(clusters) - 1
        else:
            clusters[best_i]["members"].append(key)
            clusters[best_i]["reps"].append(pose)
            labels[key] = best_i
    return labels, [c["members"] for c in clusters]
