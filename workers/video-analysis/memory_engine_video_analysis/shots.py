"""Shot boundaries: a classical detector that runs today, and the TransNetV2 seam.

WHICH ONE RAN IS RECORDED IN THE RESULT, AND THE CLASSICAL ONE IS THE WEAKER.

`models/registry.json` names TransNetV2 as the shot detector and lists it as
`required_for: [moment_scoring, reel_planning]`. Its weights are not in this
environment — `models/configs/transnetv2.json` pins `blake3: null` and no
`.onnx` file exists — so the model load gate refuses it, in release mode and in
development mode alike, with `UNLOADABLE_REASON_WEIGHTS_MISSING`. That refusal
is consulted, recorded and reported; it is not routed around.

What runs instead is `ContentHysteresisDetector`: frame-to-frame CIE76 colour
distance over a coarse CIELAB grid, with an absolute floor, an adaptive
baseline, non-maximum suppression, flash rejection and a minimum shot length.

BE CLEAR ABOUT THE GAP. The classical detector finds HARD CUTS between
visually different shots. Compared with TransNetV2 it is materially weaker at:

  * gradual transitions. A 0.4s cross dissolve IS found (measured: it peaks at
    dE 10.2 and lands one boundary at its midpoint). A 1.5s one is NOT: the
    same change spread over 45 frames peaks at dE 3.2, under every threshold
    here, and the detector reports one continuous shot across it. Both numbers
    are pinned in `tests/test_shots.py`, the second as a KNOWN LIMITATION test
    — so it is visible, and so it will be noticed if it ever changes;
  * cuts between shots that look alike — the reverse angle of the same room,
    two consecutive drone passes over the same ridge. Colour distance is small,
    so the cut is missed;
  * fast camera motion, where the baseline rises and a real cut inside it can
    fall under the ratio test;
  * anything learned. TransNetV2 was trained on real transitions; this is six
    constants chosen by hand.

A MISSED CUT IS NOT NEUTRAL. `_segments` in `moments.py` intersects clean runs
with shots so that a moment structurally cannot cross a boundary. A missed
boundary therefore permits a moment that spans a cut — a clip that changes
scene halfway through — which is a visible defect in a finished reel. A FALSE
boundary merely fragments a moment, costing continuity. The constants below are
set to prefer the cheaper error, which is the false boundary.

Every clip gets at least one shot: with no cuts detected, the whole stream is
one shot spanning [0, n). Emitting no shots at all would make `_segments` fall
back to its no-shots branch, and "the detector found one continuous shot" and
"nobody ran a detector" would look identical downstream.
"""

from __future__ import annotations

import math
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

__all__ = [
    "DETECTOR_ID",
    "DETECTOR_VERSION",
    "SeamStatus",
    "ShotBackend",
    "ShotDetection",
    "detect_shots",
    "transnetv2_seam",
]

DETECTOR_ID = "content-hysteresis"
DETECTOR_VERSION = "1.0.0"
TRANSNETV2_MODEL_ID = "transnetv2"

# CIE76 dE between consecutive frames' coarse Lab signatures at which a
# boundary is DECLARED. dE of 2.3 is the classic just-noticeable difference for
# adjacent patches; averaged over a 16x9 grid, a whole-frame mean of 8 means
# the picture changed substantially rather than moved.
#
# MEASURED, on the four cases `tests/test_shots.py` generates with FFmpeg and
# on the ten clips of the demo library:
#
#   hard cut (testsrc2 -> smptebars)      102.4  at the cut frame
#   0.4s cross dissolve                    10.2  at its midpoint
#   fast whip pan over mandelbrot          12.9  sustained, baseline ~11
#   white flash frame                      56.6  and again on the way out
#   ten continuous demo-library clips     <=2.9  for their whole length
#
# 12.0 was the first choice and it MISSED the dissolve entirely, which is the
# expensive error: a missed boundary lets a moment span a scene change. 8.0
# catches it while leaving a 2.8x margin over the busiest continuous footage
# available here. The whip pan sits ABOVE this threshold and is rejected by the
# baseline ratio instead — which is what that test is for, and why lowering
# this constant does not turn every pan into a cut.
HIGH_DELTA_E = 8.0

# The hysteresis low threshold. A candidate this large, close behind a declared
# boundary, is absorbed into it rather than becoming a second boundary — which
# is what a two- or three-frame dissolve looks like from here.
LOW_DELTA_E = 5.0
MERGE_SECONDS = 0.20

# A boundary must also stand this far above the local baseline (the median
# distance in a window around it). This is what separates a cut from continuous
# fast motion: a whip pan produces large distances CONTINUOUSLY, so its
# baseline is large too and the ratio test fails.
BASELINE_RATIO = 3.0
BASELINE_SECONDS = 1.0

# Floor under the baseline, in dE. Without it, a locked-off shot of a still
# subject has a baseline near zero and ANY movement clears BASELINE_RATIO.
BASELINE_FLOOR_DELTA_E = 1.0

# Non-maximum suppression radius, and the shortest shot that may be emitted.
NMS_SECONDS = 0.20
MIN_SHOT_SECONDS = 0.40

# Flash rejection. A camera flash, a firework, a police light: the picture
# changes hugely and then RETURNS. If within this many seconds the content
# comes back to within FLASH_RETURN_DELTA_E of what preceded the spike, it was
# an event in the shot, not a boundary between two.
FLASH_MAX_SECONDS = 0.40
FLASH_RETURN_DELTA_E = 6.0


class ShotError(ValueError):
    """Shot detection cannot run on this input."""


class ShotBackend(Protocol):
    """A learned shot detector, when one is available.

    Deliberately takes the PROXY PATH and not the signatures the classical
    detector uses: TransNetV2 consumes 48x27 RGB frame sequences, not colour
    statistics, so a seam shaped around the classical detector's inputs would
    be a seam nothing could ever be plugged into. Returns the frame indices at
    which a new shot BEGINS (never 0).
    """

    model_id: str
    version: str

    def detect(self, proxy_path: str, frame_count: int) -> Sequence[int]:
        ...


@dataclass(frozen=True, slots=True)
class SeamStatus:
    """Whether the learned detector could be used, and if not, why not."""

    model_id: str
    available: bool
    reason: str
    checked: bool

    def describe(self) -> str:
        if self.available:
            return f"{self.model_id}: loadable"
        return f"{self.model_id}: unavailable ({self.reason})"


@dataclass(frozen=True, slots=True)
class ShotDetection:
    shots: tuple[Any, ...]
    cut_frames: tuple[int, ...]
    distances: tuple[float, ...]
    detector_id: str
    detector_version: str
    seam: SeamStatus
    suppressed_flashes: tuple[int, ...] = field(default_factory=tuple)

    @property
    def is_learned(self) -> bool:
        return self.detector_id != DETECTOR_ID


def transnetv2_seam(
    *, repo_root: Path | None = None, environ: dict[str, str] | None = None
) -> SeamStatus:
    """Ask the model load gate whether TransNetV2 may be loaded.

    The gate is `models/policy/load_gate.py` by way of
    `workers/ml-runtime`'s `ModelCatalog`, which is the one place that turns a
    registry entry into a Candidate. This does NOT reimplement that: a private
    copy of the gate reasoning here would be a second policy, and the second
    one is always the one that is out of date when it matters.

    `checked` False means the gate could not even be consulted (the catalog is
    not importable in this environment). That is reported as its own state
    rather than folded into "unavailable", because "the gate said no" and
    "nobody asked the gate" are different claims — the same distinction the
    safety clearance draws between a negative result and a missing one.
    """
    root = repo_root or Path(__file__).resolve().parents[3]
    for candidate in (root, root / "workers" / "ml-runtime"):
        text = str(candidate)
        if candidate.is_dir() and text not in sys.path:
            sys.path.append(text)
    try:
        from memory_engine_ml_runtime.catalog import ModelCatalog  # noqa: PLC0415
    except Exception as error:  # noqa: BLE001 - any import failure is the same state
        return SeamStatus(
            model_id=TRANSNETV2_MODEL_ID,
            available=False,
            checked=False,
            reason=(
                "the model catalog (workers/ml-runtime) could not be imported, so "
                f"the load gate was never consulted: {type(error).__name__}: {error}"
            ),
        )
    try:
        # repo_root MUST be passed. ModelCatalog's default is derived from
        # catalog.py's own location, which is correct only when ml-runtime is
        # on sys.path from the source tree. CI pip-installs it, so the default
        # resolves into site-packages and models/registry.json is not there --
        # and the gate then reports "nobody asked" for what is really a path
        # bug on our side. `root` was already computed above; use it.
        catalog = ModelCatalog(repo_root=root, environ=environ)
        inspection = catalog.inspect(TRANSNETV2_MODEL_ID)
    except Exception as error:  # noqa: BLE001
        return SeamStatus(
            model_id=TRANSNETV2_MODEL_ID,
            available=False,
            checked=False,
            reason=f"the model registry could not be read: {type(error).__name__}: {error}",
        )
    if inspection is None:
        return SeamStatus(
            model_id=TRANSNETV2_MODEL_ID,
            available=False,
            checked=True,
            reason="not registered in models/registry.json",
        )
    if inspection.unloadable_reason:
        return SeamStatus(
            model_id=TRANSNETV2_MODEL_ID,
            available=False,
            checked=True,
            reason=f"{inspection.unloadable_reason} in {catalog.mode} mode",
        )
    return SeamStatus(
        model_id=TRANSNETV2_MODEL_ID,
        available=True,
        checked=True,
        reason=(
            "the load gate permits it, but this worker holds no execution "
            "backend: TransNetV2 runs in the model host, and nothing here "
            "speaks to the host yet. Pass backend=... to use it."
        ),
    )


def _distance(first: Sequence[float], second: Sequence[float]) -> float:
    """Mean CIE76 dE over the block grid.

    Mean rather than max: one block changing completely is a person walking
    past the lens, and taking the max would call that a cut on every clip with
    a foreground subject.
    """
    if len(first) != len(second) or len(first) % 3:
        raise ShotError("signatures must be equal-length triples of L*a*b* values")
    total = 0.0
    for index in range(0, len(first), 3):
        total += math.sqrt(
            (first[index] - second[index]) ** 2
            + (first[index + 1] - second[index + 1]) ** 2
            + (first[index + 2] - second[index + 2]) ** 2
        )
    return total / (len(first) / 3)


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return 0.5 * (ordered[middle - 1] + ordered[middle])


def _frames(seconds: float, rate: float) -> int:
    """Seconds -> whole frames, at least one, halves away from zero.

    The same rule and the same reason as `moments._samples`: Python's round()
    is half-to-even, so a policy value would change meaning with the frame
    rate.
    """
    return max(1, int(seconds * rate + 0.5))


def detect_shots(
    signatures: Sequence[Sequence[float]],
    *,
    rate: float,
    proxy_path: str | None = None,
    backend: ShotBackend | None = None,
    seam: SeamStatus | None = None,
) -> ShotDetection:
    """Shot spans covering [0, len(signatures)), contiguous and non-overlapping.

    With `backend` supplied, the learned detector decides the cuts and the
    classical distances are still reported (they cost nothing and make the two
    comparable). Without one, the classical detector decides.
    """
    from memory_engine_story.moments import Shot  # noqa: PLC0415

    count = len(signatures)
    if count == 0:
        raise ShotError("cannot detect shots in an empty stream")
    if not (isinstance(rate, (int, float)) and math.isfinite(rate) and rate > 0):
        raise ShotError(f"rate={rate!r} must be a positive finite number")

    distances = [0.0]
    for index in range(1, count):
        distances.append(_distance(signatures[index - 1], signatures[index]))

    status = seam or SeamStatus(
        model_id=TRANSNETV2_MODEL_ID,
        available=False,
        checked=False,
        reason="the seam was not consulted for this call",
    )
    flashes: tuple[int, ...] = ()
    if backend is not None:
        if proxy_path is None:
            raise ShotError("a learned backend needs the proxy path it reads from")
        cuts = tuple(sorted({int(value) for value in backend.detect(proxy_path, count)}))
        if any(cut <= 0 or cut >= count for cut in cuts):
            raise ShotError(
                f"{backend.model_id} returned a cut outside (0,{count}); a shot "
                "boundary at or past the end of the stream would produce an "
                "empty or out-of-range shot"
            )
        detector_id, detector_version = backend.model_id, backend.version
    else:
        cuts, flashes = _classical_cuts(signatures, distances, rate)
        detector_id, detector_version = DETECTOR_ID, DETECTOR_VERSION

    bounds = [0, *cuts, count]
    shots = tuple(
        Shot(shot_id=f"shot-{number:04d}", start=bounds[number], end=bounds[number + 1])
        for number in range(len(bounds) - 1)
    )
    return ShotDetection(
        shots=shots,
        cut_frames=tuple(cuts),
        distances=tuple(round(value, 6) for value in distances),
        detector_id=detector_id,
        detector_version=detector_version,
        seam=status,
        suppressed_flashes=flashes,
    )


def _classical_cuts(
    signatures: Sequence[Sequence[float]], distances: list[float], rate: float
) -> tuple[tuple[int, ...], tuple[int, ...]]:
    """Cut frames and rejected flashes, by the rules in the module docstring."""
    count = len(distances)
    baseline_half = _frames(BASELINE_SECONDS, rate)
    nms = _frames(NMS_SECONDS, rate)
    merge = _frames(MERGE_SECONDS, rate)
    minimum = _frames(MIN_SHOT_SECONDS, rate)
    flash_span = _frames(FLASH_MAX_SECONDS, rate)

    cuts: list[int] = []
    flashes: list[int] = []
    blocked_until = 0
    index = 1
    while index < count:
        value = distances[index]
        if value < HIGH_DELTA_E or index < blocked_until:
            index += 1
            continue

        window = [
            distances[j]
            for j in range(max(1, index - baseline_half), min(count, index + baseline_half + 1))
            if j != index
        ]
        baseline = max(_median(window), BASELINE_FLOOR_DELTA_E)
        if value < baseline * BASELINE_RATIO:
            index += 1
            continue

        neighbourhood = range(max(1, index - nms), min(count, index + nms + 1))
        # Strictly greater to the left, greater-or-equal to the right: on a
        # plateau (a two-frame dissolve with equal distances) this keeps the
        # FIRST frame, which is the one the picture started changing at. The
        # same asymmetry, for the same reason, as `moments._local_max`.
        if any(distances[j] >= value for j in neighbourhood if j < index) or any(
            distances[j] > value for j in neighbourhood if j > index
        ):
            index += 1
            continue

        returned = _flash_return(signatures, index, flash_span)
        if returned is not None:
            flashes.append(index)
            blocked_until = returned + 1
            index = returned + 1
            continue

        if cuts and index - cuts[-1] < minimum:
            # Too close behind the previous boundary to be its own shot. Not a
            # new cut, and not a reason to re-examine: whatever it was belongs
            # to the transition that already produced one.
            index += 1
            continue

        cuts.append(index)
        # Hysteresis: everything above the LOW threshold in the merge window
        # after a declared boundary belongs to that boundary. This is what
        # makes a short dissolve one cut rather than three.
        blocked_until = index + 1
        for j in range(index + 1, min(count, index + merge + 1)):
            if distances[j] >= LOW_DELTA_E:
                blocked_until = j + 1
        index = blocked_until

    return tuple(cuts), tuple(flashes)


def _flash_return(
    signatures: Sequence[Sequence[float]], index: int, span: int
) -> int | None:
    """The frame at which the picture returns to what preceded a spike, if it does.

    A flash is a PAIR of opposing spikes: the picture leaves, then comes back.
    Either edge can be the local maximum, and which one wins is decided by
    sub-threshold encoder noise — so both have to be recognised or the
    behaviour depends on the ffmpeg build.

    That is not hypothetical. This function originally only looked forward from
    `index - 1`, which is correct when the peak lands on the OUTGOING edge and
    guaranteed to fail when it lands on the RETURN edge: there, `index - 1` is
    the flash frame itself, so the search asks when the scene comes back to
    white, and the answer is never. A single white frame in an unbroken scene
    was reported as a cut on Linux and not on macOS, because ffmpeg 6 and 7
    quantise the two edges of the spike differently.

    Returns the frame the picture has settled at, or None if it never settles.
    """
    # Outgoing edge: the picture leaves at `index` and comes back later.
    before = signatures[index - 1]
    for j in range(index + 1, min(len(signatures), index + span + 1)):
        if _distance(before, signatures[j]) <= FLASH_RETURN_DELTA_E:
            return j

    # Return edge: the picture at `index` is already back to something it held
    # shortly before, so the frames in between were the transient. Compared
    # against frames at `index - 2` and earlier, because `index - 1` is inside
    # the spike by construction.
    #
    # This will also absorb a genuine A-B-A cut whose B shot is shorter than
    # FLASH_MAX_SECONDS. That is intended: a shot that appears and disappears
    # inside 0.4s is an event within a scene by any useful definition, and
    # MIN_SHOT_SECONDS (0.40) would refuse to emit it as a shot anyway.
    here = signatures[index]
    for k in range(index - 2, max(-1, index - span - 2), -1):
        if _distance(signatures[k], here) <= FLASH_RETURN_DELTA_E:
            return index
    return None
