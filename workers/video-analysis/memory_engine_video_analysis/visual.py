"""Per-frame visual signals: photometry, sharpness, motion, shake, novelty.

Classical, no weights, no GPU, one decode pass. This is the video half of the
"cheap 95%" the build plan puts first in cost order, and it is what
`eliminate_frames` in `moments.py` runs its gates against — black frames, lens
cap, blown or crushed exposure, shake and tripod dead time.

WHAT IS MEASURED HERE AND WHAT IS DELIBERATELY LEFT None

    luma, clipped_highlights, clipped_shadows, sharpness   measured
    motion, shake, novelty, exposure_stability             measured
    face_presence, smile_intensity, max_face_area_ratio    NOT MEASURED
    speech, noise                                          NOT MEASURED

The face signals need SCRFD and an expression head running in the model host.
This worker does not talk to the host, so those fields stay None. None means
NOT MEASURED and `moments.py` renormalises it out of the fusion and reports the
reduced coverage; a fabricated 0.0 would mean "measured, and there is no face",
which is a different claim and one that no later audit could separate from a
real measurement. Leaving them absent costs a faceless action shot nothing
(`_not_applicable` handles the smile term) and costs a real portrait its face
weight, which is the correct direction to be wrong in.

`lens_obstructed` is also left False on every frame, and that is not a
measurement either: `eliminate_frames` derives lens obstruction from the
dark-AND-textureless combination of `luma` and `sharpness`, which is the same
rule `classical.py` applies to stills. Setting the flag here as well would be
the same decision taken twice in two places, which is how two rules drift.

CALIBRATION IS A PRIOR, NOT A TRUTH — AND THESE PRIORS DRIVE HARD GATES

Every mapping below is monotone in a physical measure and saturating into
[0,1]. The constants that set the half-way or full-scale point are priors. They
have NEVER been calibrated against real footage; the only footage they have run
on is `scripts/demo/make_library.py`'s synthetic clips, whose caveat section
says explicitly that nothing tuned against them transfers.

That matters more here than in the photo path, because `Policy` in `moments.py`
applies HARD ELIMINATION GATES to these numbers — `dead_motion_max=0.02`,
`shake_max=0.55`, `blown_highlight_max=0.35` — and eliminated footage is never
scored, never ranked and never shown. A `MOTION_FULL_SCALE` that is too large
makes real handheld footage read as tripod dead time and deletes it from the
product. This is the same hazard recorded as issue #22 for
`DEFAULT_SHARPNESS_FLOOR`, and it is recorded here for the same reason: so that
a wrong number is found by reading rather than by a user losing a video.

`EXECUTOR_VERSION` changes whenever any constant or formula changes. Scores
from two calibrations are not comparable and must not be mixed.

THE LUMA WEIGHTS ARE ITU-R 601, NOT 709, AND THAT IS ON PURPOSE
`classical.py` measures stills through PIL's `convert("L")`, which is
0.299/0.587/0.114 on gamma-encoded values, and `SHARPNESS_HALF_POINT` is
calibrated against exactly that. This file inherits the constant, so it must
inherit the transform. Using 709 weights here would shift the measure by a few
percent and silently decalibrate the constant it borrowed.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "EXECUTOR_ID",
    "EXECUTOR_VERSION",
    "VisualAnalysis",
    "VisualFrame",
    "analyse_frames",
    "lab_signature",
]

EXECUTOR_ID = "video-visual-features"
EXECUTOR_VERSION = "1.0.0"

# Inherited verbatim from classical-quality 1.0.0 so a frame of video and a
# still are judged on the same scale. Both are measured on a raster whose long
# edge is at most 512px (see decode.ANALYSIS_MAX_EDGE); Laplacian variance is
# resolution-dependent, so that shared raster is what makes the constant mean
# anything. The two rasters are produced by different resamplers (FFmpeg
# bicubic here, the `image` crate in ingest there), so the agreement is close
# rather than exact — `tests/test_visual.py` pins the formula agreement, not
# the pipeline agreement.
SHARPNESS_HALF_POINT = 2.0e-3
HIGHLIGHT_LEVEL = 0.98
SHADOW_LEVEL = 0.02

# Mean absolute frame-to-frame luma difference, in [0,1] luma units, at which
# motion reads 1.0. A ramp rather than a saturating curve, and that is the
# opposite of the choice `classical.py` made for sharpness — deliberately. The
# decisions that hang off sharpness happen at the TOP of its range (which photo
# is the hero), where a ramp's ceiling would flatten real differences. The
# decisions that hang off motion happen at the BOTTOM (is this tripod dead
# time), where a saturating curve compresses the region the gate lives in:
# v/(v+k) never reaches 0.02 until v is 2% of k, which would make
# `dead_motion_max` fire on essentially nothing.
MOTION_FULL_SCALE = 0.12

# The global-shift search runs on the FULL analysis raster (its cost is one
# mean per axis plus a few hundred subtractions, so downsampling first buys
# nothing) and reaches this far, as a fraction of the raster's extent.
SHIFT_SEARCH_FRACTION = 0.05
SHIFT_SEARCH_MINIMUM = 8

# Frame-to-frame CHANGE in global displacement, in analysis-raster pixels per
# frame squared, at which shake reads 1.0. The second difference is the point:
# a smooth pan has a near-constant velocity and therefore near-zero
# acceleration, while handheld jitter reverses direction every few frames. A
# first-difference definition would call every pan unusable.
#
# THE DISPLACEMENT ESTIMATE IS SUB-PIXEL, AND THAT IS NOT AN OPTIMISATION.
# The first version of this file searched integer shifts on a 64px-wide
# raster. A smooth pan of 0.43px per frame then quantises to a displacement
# that flips between 0 and 1, producing a JERK OF EXACTLY 1.0 several times a
# second — shake 0.67 against `Policy.shake_max` of 0.55, so every panning shot
# in the demo library was eliminated as unusably shaky. Measured, not
# theorised: `tests/test_visual.py::test_a_smooth_pan_does_not_read_as_shake`
# is that case, pinned.
SHAKE_FULL_SCALE = 8.0

# Half-width of the window over which exposure hunting is measured, and the
# luma standard deviation within it that reads as fully unstable.
EXPOSURE_WINDOW_SECONDS = 0.75
EXPOSURE_FULL_SCALE = 0.10

# Novelty is measured against an exponential moving average of what has already
# been seen in this clip. The decay sets how long ago still counts as "already
# seen"; the full scale is the mean CIE76 colour difference at which a frame is
# entirely new. 25 dE is a different scene, not a different exposure.
NOVELTY_DECAY = 0.90
NOVELTY_FULL_SCALE = 25.0

# The coarse grid every colour signature is reduced to. Small enough that a
# person walking through frame does not read as a scene change, large enough
# that a cut between two similarly-lit scenes does.
COARSE_COLUMNS = 16
COARSE_ROWS = 9

# ITU-R 601 luma weights, matching PIL's convert("L"). See the module docstring.
_LUMA_R, _LUMA_G, _LUMA_B = 0.299, 0.587, 0.114


class VisualError(ValueError):
    """The frames cannot be measured. Not a low score -- an absent one."""


@dataclass(frozen=True, slots=True)
class VisualFrame:
    """One sample's visual signals, in the units `moments.Frame` declares.

    None is NOT MEASURED throughout. The first frame has no motion, and the
    first two have no shake, because a difference needs two samples and a
    second difference needs three. Reporting 0.0 for them would put a fake
    still moment at the head of every clip, which is exactly where a reel's
    hook is chosen from.
    """

    luma: float
    clipped_highlights: float
    clipped_shadows: float
    sharpness: float
    exposure_stability: float
    motion: float | None
    shake: float | None
    novelty: float | None


@dataclass(frozen=True, slots=True)
class VisualAnalysis:
    frames: tuple[VisualFrame, ...]
    signatures: tuple[tuple[float, ...], ...]
    executor_id: str = EXECUTOR_ID
    executor_version: str = EXECUTOR_VERSION
    raw: dict[str, tuple[float, ...]] = field(default_factory=dict)

    @property
    def run_id(self) -> str:
        return f"{self.executor_id}-{self.executor_version.replace('.', '-')}"


def _quantise(value: float) -> float:
    """Six decimals, the precision `Score` stores and `moments.py` quantises to.

    Float addition is not associative, so an unquantised mean can differ in the
    last bit between machines; invisible until it reorders a tie and a
    different second of footage lands in the reel.
    """
    return round(min(1.0, max(0.0, value)) + 0.0, 6)


def _saturating(value: float, half_point: float) -> float:
    if value <= 0.0:
        return 0.0
    return value / (value + half_point)


def _block_bounds(extent: int, blocks: int) -> list[int]:
    """Near-equal integer block boundaries, by integer arithmetic only.

    Deterministic across platforms in a way that a float-accumulated boundary
    is not, and never empty: a raster narrower than the block count gets one
    column per block until it runs out, which the caller handles by using
    fewer blocks.
    """
    count = min(blocks, extent)
    return [i * extent // count for i in range(count)] + [extent]


def _lab_from_linear(linear: Any) -> Any:
    """Linear-light RGB (D65) -> CIE L*a*b*. Shape (..., 3) in, same shape out."""
    import numpy  # noqa: PLC0415

    matrix = numpy.array(
        [
            [0.4123907992659595, 0.3575843393838780, 0.1804807884018343],
            [0.2126390058715104, 0.7151686787677559, 0.0721923153607337],
            [0.0193308187155918, 0.1191947797946259, 0.9505321522496608],
        ],
        dtype=numpy.float64,
    )
    xyz = linear @ matrix.T
    white = numpy.array([0.9504559270516716, 1.0, 1.0890577507598784])
    scaled = xyz / white
    epsilon = 216.0 / 24389.0
    kappa = 24389.0 / 27.0
    f = numpy.where(
        scaled > epsilon,
        numpy.cbrt(numpy.maximum(scaled, 0.0)),
        (kappa * scaled + 16.0) / 116.0,
    )
    lightness = 116.0 * f[..., 1] - 16.0
    a = 500.0 * (f[..., 0] - f[..., 1])
    b = 200.0 * (f[..., 1] - f[..., 2])
    return numpy.stack([lightness, a, b], axis=-1)


def _srgb_to_linear_table() -> Any:
    """A 256-entry sRGB -> linear-light lookup.

    A table rather than a pow() over every pixel: 512x288x3 calls to pow per
    frame is most of the cost of the whole pass, and the input is 8-bit, so a
    table is exact rather than approximate.
    """
    import numpy  # noqa: PLC0415

    values = numpy.arange(256, dtype=numpy.float64) / 255.0
    return numpy.where(
        values <= 0.04045, values / 12.92, ((values + 0.055) / 1.055) ** 2.4
    )


def _block_means(image: Any, row_bounds: list[int], column_bounds: list[int]) -> Any:
    """Mean of every block, by two `reduceat` passes.

    Not a Python loop over blocks: at 16x9 blocks that is 144 numpy calls per
    frame, which measured as most of the cost of the whole visual pass.
    """
    import numpy  # noqa: PLC0415

    summed = numpy.add.reduceat(
        numpy.add.reduceat(image, row_bounds[:-1], axis=0), column_bounds[:-1], axis=1
    )
    counts = numpy.outer(numpy.diff(row_bounds), numpy.diff(column_bounds))
    if summed.ndim == 3:
        counts = counts[:, :, None]
    return summed / counts


def lab_signature(rgb: Any, *, rows: int = COARSE_ROWS, columns: int = COARSE_COLUMNS) -> Any:
    """Block-mean CIELAB signature of one frame, averaged in LINEAR light.

    Averaging gamma-encoded values and then converting would make the signature
    of a block depend on how the display transfer function happens to curve,
    which is measurable as a false scene change when the exposure shifts.
    """
    _ensure_tables()
    linear = _LINEAR_TABLE[rgb]
    height, width = rgb.shape[0], rgb.shape[1]
    return _lab_from_linear(
        _block_means(linear, _block_bounds(height, rows), _block_bounds(width, columns))
    )


_LINEAR_TABLE: Any = None


def _ensure_tables() -> None:
    global _LINEAR_TABLE
    if _LINEAR_TABLE is None:
        _LINEAR_TABLE = _srgb_to_linear_table()


def _laplacian_variance(luma: Any) -> float:
    """4-neighbour discrete Laplacian on the interior, written out.

    Identical arithmetic to `classical.py::_laplacian_variance`, which is what
    licenses this file to reuse its calibration constant.
    `tests/test_visual.py` asserts the two agree on the same array; if that
    test ever fails, one of the two moved and the constant belongs to neither.
    """
    import numpy  # noqa: PLC0415

    centre = luma[1:-1, 1:-1]
    response = (
        luma[:-2, 1:-1] + luma[2:, 1:-1] + luma[1:-1, :-2] + luma[1:-1, 2:] - 4.0 * centre
    )
    return float(numpy.var(response))


def _profiles(luma: Any) -> tuple[Any, Any]:
    """Column and row mean profiles of the raster, for the shift search."""
    return luma.mean(axis=0), luma.mean(axis=1)


def _search_radius(extent: int) -> int:
    """How far the shift search reaches along an axis of `extent` pixels.

    A fraction of the extent rather than a constant: the same physical camera
    move is more pixels on a wider raster, and a radius that does not scale
    would clip the estimate on one geometry and not on another.
    """
    if extent <= 3:
        return 1
    wanted = max(SHIFT_SEARCH_MINIMUM, int(extent * SHIFT_SEARCH_FRACTION + 0.5))
    return max(1, min(wanted, extent - 2))


def _estimate_shift(current: Any, previous: Any, radius: int) -> float:
    """Sub-pixel shift of `current` against `previous`, by projection matching.

    SIGN CONVENTION, stated because it is not self-evident and a reader who
    assumes the other one will misread every displacement: the returned shift
    `s` is the offset that makes `current[i] == previous[i + s]`. So a POSITIVE
    shift means the picture moved towards LOWER indices — left along the column
    profile, up along the row profile — between `previous` and `current`.

    Nothing downstream depends on the sign (shake is the magnitude of a second
    difference, and a global sign flip cancels), which is exactly why it would
    have gone unnoticed. It is pinned in
    `test_the_displacement_estimate_is_sub_pixel`.

    A 1-D projection search rather than a 2-D block match: two correlations of
    a few hundred elements instead of an image difference per candidate offset.
    It measures translation only, which is what camera shake is; it cannot see
    rotation or zoom, and this says so rather than pretending otherwise.

    The integer minimum is refined by fitting a parabola to the cost at the
    minimum and its two neighbours. Without that refinement the estimate is
    quantised to whole pixels, and a pan slower than one pixel per frame
    produces a displacement that flips between two integers — an oscillation
    that is indistinguishable from shake and is not shake. See
    `SHAKE_FULL_SCALE` for the measured consequence.

    Integer ties break to the SMALLEST magnitude, then to the more negative
    shift. Without an explicit rule a flat profile (a black frame, a wall)
    would resolve by array order and the signal would depend on the search
    bounds rather than on the picture.
    """
    import numpy  # noqa: PLC0415

    length = current.shape[0]
    limit = min(radius, max(0, length - 2))
    if limit <= 0:
        return 0.0
    costs: dict[int, float] = {}
    best_cost = math.inf
    best = 0
    for shift in range(-limit, limit + 1):
        if shift < 0:
            a, b = current[-shift:], previous[: length + shift]
        elif shift > 0:
            a, b = current[: length - shift], previous[shift:]
        else:
            a, b = current, previous
        if a.shape[0] == 0:
            continue
        difference = a - b
        # SQUARED difference, not absolute. The parabolic refinement below fits
        # a quadratic to three cost samples, and an L1 cost is V-shaped at its
        # minimum rather than quadratic, so the fit is biased and the residual
        # appears as jitter in the displacement — which IS the shake signal, so
        # the bias is reported as camera shake. Measured on a smoothly panning
        # demo clip: mean jerk fell from 0.84 to 0.62 px/frame^2 on the switch,
        # and the peak from 3.5 to 2.0.
        cost = float((difference * difference).mean())
        costs[shift] = cost
        if (cost, abs(shift), shift) < (best_cost, abs(best), best):
            best_cost, best = cost, shift

    left = costs.get(best - 1)
    right = costs.get(best + 1)
    if left is None or right is None:
        return float(best)
    denominator = left - 2.0 * best_cost + right
    if denominator <= 0.0:
        # Not a minimum in the neighbours' opinion (a plateau, or a cost curve
        # that is flat to the last bit). Refining against it would move the
        # estimate by an arbitrary amount, so it is left at the integer.
        return float(best)
    offset = 0.5 * (left - right) / denominator
    return float(best) + max(-0.5, min(0.5, offset))


def analyse_frames(
    frames: Iterable[bytes],
    *,
    size: tuple[int, int],
    rate: float,
) -> VisualAnalysis:
    """Measure every frame of one proxy, streaming.

    `frames` yields RGB24 buffers of exactly `size`. One frame plus a handful
    of small reductions is held at a time; nothing accumulates a decoded clip.
    """
    import numpy  # noqa: PLC0415

    _ensure_tables()
    width, height = size
    if width <= 0 or height <= 0:
        raise VisualError(f"cannot measure a {width}x{height} raster")
    if not (isinstance(rate, (int, float)) and math.isfinite(rate) and rate > 0):
        raise VisualError(f"rate={rate!r} must be a positive finite number")

    column_radius = _search_radius(width)
    row_radius = _search_radius(height)

    luma_series: list[float] = []
    highlights: list[float] = []
    shadows: list[float] = []
    sharpness: list[float] = []
    raw_variance: list[float] = []
    motion: list[float | None] = []
    raw_motion: list[float] = []
    novelty: list[float | None] = []
    raw_novelty: list[float] = []
    signatures: list[tuple[float, ...]] = []
    displacements: list[tuple[float, float]] = []

    previous_luma: Any = None
    previous_profiles: tuple[Any, Any] | None = None
    average_signature: Any = None

    for buffer in frames:
        array = numpy.frombuffer(buffer, dtype=numpy.uint8)
        if array.size != width * height * 3:
            raise VisualError(
                f"frame is {array.size} bytes, expected {width * height * 3}"
            )
        rgb = array.reshape(height, width, 3)
        grey = (
            _LUMA_R * rgb[:, :, 0] + _LUMA_G * rgb[:, :, 1] + _LUMA_B * rgb[:, :, 2]
        ) / 255.0

        luma_series.append(float(grey.mean()))
        highlights.append(float(numpy.count_nonzero(grey >= HIGHLIGHT_LEVEL)) / grey.size)
        shadows.append(float(numpy.count_nonzero(grey <= SHADOW_LEVEL)) / grey.size)
        variance = _laplacian_variance(grey) if min(grey.shape) >= 3 else 0.0
        raw_variance.append(variance)
        sharpness.append(_saturating(variance, SHARPNESS_HALF_POINT))

        if previous_luma is None:
            motion.append(None)
            raw_motion.append(0.0)
        else:
            difference = float(numpy.abs(grey - previous_luma).mean())
            raw_motion.append(difference)
            motion.append(min(1.0, difference / MOTION_FULL_SCALE))

        columns, rows = _profiles(grey)
        if previous_profiles is None:
            displacements.append((0.0, 0.0))
        else:
            displacements.append(
                (
                    _estimate_shift(columns, previous_profiles[0], column_radius),
                    _estimate_shift(rows, previous_profiles[1], row_radius),
                )
            )
        previous_profiles = (columns, rows)

        # Through the public helper, not an inlined copy of it: an inlined
        # version is a second implementation that tests would never touch,
        # because the tests exercise the helper.
        signature = lab_signature(rgb)
        signatures.append(tuple(float(v) for v in signature.reshape(-1)))
        if average_signature is None:
            novelty.append(None)
            raw_novelty.append(0.0)
            average_signature = signature
        else:
            distance = float(
                numpy.sqrt(((signature - average_signature) ** 2).sum(axis=-1)).mean()
            )
            raw_novelty.append(distance)
            novelty.append(min(1.0, distance / NOVELTY_FULL_SCALE))
            average_signature = (
                NOVELTY_DECAY * average_signature + (1.0 - NOVELTY_DECAY) * signature
            )

        previous_luma = grey

    count = len(luma_series)
    if count == 0:
        raise VisualError("the proxy decoded to zero frames")

    shake: list[float | None] = []
    raw_shake: list[float] = []
    for index in range(count):
        if index < 2:
            shake.append(None)
            raw_shake.append(0.0)
            continue
        dx = displacements[index][0] - displacements[index - 1][0]
        dy = displacements[index][1] - displacements[index - 1][1]
        jerk = math.hypot(dx, dy)
        raw_shake.append(jerk)
        shake.append(min(1.0, jerk / SHAKE_FULL_SCALE))

    stability = _exposure_stability(luma_series, rate)

    measured = tuple(
        VisualFrame(
            luma=_quantise(luma_series[i]),
            clipped_highlights=_quantise(highlights[i]),
            clipped_shadows=_quantise(shadows[i]),
            sharpness=_quantise(sharpness[i]),
            exposure_stability=_quantise(stability[i]),
            motion=None if motion[i] is None else _quantise(motion[i]),
            shake=None if shake[i] is None else _quantise(shake[i]),
            novelty=None if novelty[i] is None else _quantise(novelty[i]),
        )
        for i in range(count)
    )
    return VisualAnalysis(
        frames=measured,
        signatures=tuple(signatures),
        raw={
            "luma": tuple(luma_series),
            "laplacian_variance": tuple(raw_variance),
            "frame_difference": tuple(raw_motion),
            "displacement_jerk": tuple(raw_shake),
            "signature_distance": tuple(raw_novelty),
        },
    )


def _exposure_stability(luma: list[float], rate: float) -> list[float]:
    """1 - (local luma spread / full scale), over a window centred on each frame.

    Region-level for the same reason `_mark_dead_time` in `moments.py` is: a
    single dark frame in a bright shot is a flash or a dropped frame, while a
    camera hunting exposure moves the level for a second or more. The window is
    truncated at the clip edges rather than padded — padding with the edge
    value would report the first second of every clip as perfectly stable.
    """
    half = max(1, int(EXPOSURE_WINDOW_SECONDS * rate + 0.5))
    count = len(luma)
    out: list[float] = []
    for index in range(count):
        lo = max(0, index - half)
        hi = min(count, index + half + 1)
        window = luma[lo:hi]
        mean = sum(window) / len(window)
        variance = sum((value - mean) ** 2 for value in window) / len(window)
        spread = math.sqrt(variance)
        out.append(max(0.0, 1.0 - spread / EXPOSURE_FULL_SCALE))
    return out
