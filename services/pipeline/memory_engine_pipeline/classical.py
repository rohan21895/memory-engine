"""`classical_quality` — the cheap first pass, made executable.

WHY THIS FILE EXISTS

`models/registry.json` lists `classical_quality` as step one of the
`photo_analysis` pipeline, and issue #42 records the problem: there was no
entry, no formula, no version pin and no executor behind that name. A host
could not produce `MediaRecord.quality.sharpness` or `.exposure`, both of which
the contract marks REQUIRED, so nothing downstream could run -- `Signals`
refuses to fuse without them, `select()` refuses to rank without a fused score,
and the album stage therefore had no input. Treating the step as a no-op would
have been worse than leaving it missing: it would have produced MediaRecords
that look analysed and carry no measurements.

WHAT IT IS

Four numbers and two flags, computed from the 512px thumbnail proxy that ingest
already wrote. No model, no weights, no network, no GPU. It is the "nearly
free" filter the build plan puts first in cost order, and it runs on the proxy
rather than the original because analysis never opens source files.

CALIBRATION IS A PRIOR, NOT A TRUTH

Every mapping below is monotone in the underlying physical measure and
saturating into [0,1]; the constants that set the half-way point are priors,
chosen to be defensible and, more importantly, to be TUNABLE in one place with
a version pin attached. `EXECUTOR_VERSION` changes whenever any constant or
formula changes, which is what lets a record say which arithmetic produced it
and lets the eval harness gate a recalibration the same way it gates a model
swap. A score computed by 1.0.0 and a score computed by 1.1.0 are not
comparable and must not be silently mixed.

WHY THE PROXY SIZE MATTERS

Laplacian variance is resolution-dependent: the same photo measured at 6000px
and at 512px gives different numbers, and a library measured at a mix of both
cannot be ranked. Standardising on `thumbnail_512` is what makes the constant
below mean anything at all. The executor therefore records the proxy kind it
consumed and refuses a proxy kind it was not calibrated for.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

__all__ = [
    "EXECUTOR_ID",
    "EXECUTOR_VERSION",
    "ClassicalQuality",
    "HISTOGRAM_BINS",
    "SUPPORTED_PROXY_KINDS",
    "measure",
    "measure_face_sharpness",
]

EXECUTOR_ID = "classical-quality"
EXECUTOR_VERSION = "1.0.0"
SUPPORTED_PROXY_KINDS = frozenset({"thumbnail_512"})

# Laplacian variance, measured on a 512px-long-edge luma image in [0,1], at
# which sharpness reads exactly 0.5. Set from the observation that a normally
# focused JPEG thumbnail lands in the 1e-3 to 1e-2 range and a visibly blurred
# one an order of magnitude below it.
SHARPNESS_HALF_POINT = 2.0e-3

# Robust noise sigma (in [0,1] luma units) at which the cleanliness score reads
# exactly 0.5. Clean daylight thumbnails sit near 0.005; visible chroma noise
# from a phone at ISO 3200 sits near 0.02.
NOISE_HALF_POINT = 0.012

# Luma std-dev that reads as full contrast.
CONTRAST_FULL_SCALE = 0.30

# --- face sharpness ---------------------------------------------------------
# Whole-image Laplacian variance is NOT scale-invariant: a close-up portrait is
# mostly smooth skin, so its variance is diluted and a global measure flags
# exactly the photos with tack-sharp eyes and creamy bokeh -- the best
# portraits in the library. So every face is measured at ONE standard crop
# size, which makes a 40px face and a 400px face comparable claims.
#
# Calibrated on a real 439-face library at 112px crops: in-focus faces span
# 2e-4 to 6e-3 (a shallow-depth-of-field close-up measured 5.5e-3, p90);
# visibly blurred faces cluster below 1e-4, two orders of magnitude under the
# median. The half point sits at that library's median so 0.5 reads "typical
# in-focus face".
FACE_SHARPNESS_STD_EDGE_PX = 112
FACE_SHARPNESS_HALF_POINT = 8.0e-4
#: Below this many proxy pixels on either edge, a crop's Laplacian is sensor
#: noise, not focus evidence; the face is reported as unmeasurable (None).
FACE_SHARPNESS_MIN_EDGE_PX = 16

# Clipping tolerated before it costs anything. Specular highlights and genuine
# black are normal; a blown sky or a crushed shadow is not.
CLIP_FREE_FRACTION = 0.02
CLIP_FULL_PENALTY_FRACTION = 0.35
HIGHLIGHT_LEVEL = 0.98
SHADOW_LEVEL = 0.02

# Black frame and pocket-shot thresholds, matching the elimination constants the
# story engine already uses for video (Policy.black_luma_max, pocket_luma_max,
# pocket_sharpness_max) so a still and a frame of video are judged the same way.
BLACK_LUMA_MAX = 0.02
POCKET_LUMA_MAX = 0.08
POCKET_SHARPNESS_MAX = 0.06

# Histogram resolution for the exposure term. Public, and declared in the
# registry with the rest of the calibration: changing the bin count changes
# the entropy normalisation and therefore every exposure score, so it is a
# calibration constant rather than an implementation detail.
HISTOGRAM_BINS = 64


class ClassicalQualityError(ValueError):
    """The proxy could not be measured. Not a low score -- an absent one."""


@dataclass(frozen=True, slots=True)
class ClassicalQuality:
    sharpness: float
    exposure: float
    noise: float
    contrast: float
    is_black_frame: bool
    is_lens_obstructed: bool
    raw: dict[str, float]

    @property
    def run_id(self) -> str:
        return f"{EXECUTOR_ID}-{EXECUTOR_VERSION.replace('.', '-')}"

    def to_quality(self) -> dict[str, Any]:
        """A contract `QualityScores` fragment.

        Only the four classical fields and the two flags. The learned fields
        (`technical_iqa`, `aesthetic`, `composition`, `face_quality`) are left
        ABSENT rather than zero: absent renormalises out of the fusion and is
        reported as reduced coverage, whereas zero is a fabricated measurement
        that no later audit can distinguish from a real bad one.
        """
        run_id = self.run_id
        return {
            "sharpness": {"value": self.sharpness, "run_id": run_id,
                          "raw_value": self.raw["laplacian_variance"]},
            "exposure": {"value": self.exposure, "run_id": run_id,
                         "raw_value": self.raw["clipped_fraction"]},
            "noise": {"value": self.noise, "run_id": run_id,
                      "raw_value": self.raw["noise_sigma"]},
            "contrast": {"value": self.contrast, "run_id": run_id,
                         "raw_value": self.raw["luma_std"]},
            "is_black_frame": self.is_black_frame,
            "is_lens_obstructed": self.is_lens_obstructed,
        }


def _saturating(value: float, half_point: float) -> float:
    """v / (v + k): monotone, bounded by [0,1), exactly 0.5 when v == k.

    Chosen over a clamped linear ramp because a ramp has a hard ceiling above
    which two genuinely different photos score identically, and the top of the
    sharpness range is exactly where hero selection happens.
    """
    if value <= 0.0:
        return 0.0
    return value / (value + half_point)


def _quantise(value: float) -> float:
    """Six decimals, matching the ranking engine.

    Float arithmetic is not associative and the fusion downstream quantises for
    the same reason: a score that differs in the last bit between two machines
    reorders ties, and reordered ties change which photo is the album hero.
    """
    return round(min(1.0, max(0.0, value)), 6)


def _luma(path: Path, *, max_edge: int) -> Any:
    from PIL import Image, ImageOps  # noqa: PLC0415
    import numpy  # noqa: PLC0415

    with Image.open(path) as handle:
        # The proxy was written from the EXIF-oriented image by ingest, but a
        # proxy produced by some other tool may not have been; exif_transpose
        # is a no-op when there is no orientation tag and correct when there is.
        oriented = ImageOps.exif_transpose(handle)
        grey = oriented.convert("L")
        if max(grey.size) > max_edge:
            raise ClassicalQualityError(
                f"proxy long edge is {max(grey.size)}px; this executor is "
                f"calibrated for {max_edge}px and its constants do not transfer"
            )
        array = numpy.asarray(grey, dtype=numpy.float64) / 255.0
    if array.size == 0 or min(array.shape) < 3:
        raise ClassicalQualityError("proxy is too small to measure")
    return array


def _laplacian_variance(luma: Any) -> float:
    import numpy  # noqa: PLC0415

    # 4-neighbour discrete Laplacian on the interior. Written out rather than
    # convolved so there is no dependency on SciPy or OpenCV -- the latter is
    # broken on at least one machine in this project, and a quality measure
    # that cannot run is worse than one that is slightly slower.
    centre = luma[1:-1, 1:-1]
    response = (
        luma[:-2, 1:-1] + luma[2:, 1:-1] + luma[1:-1, :-2] + luma[1:-1, 2:] - 4.0 * centre
    )
    return float(numpy.var(response))


def _noise_sigma(luma: Any) -> float:
    """Immerkaer's fast noise estimator over the 3x3 [[1,-2,1],[-2,4,-2],[1,-2,1]] mask.

    The mask is deliberately blind to smooth gradients and to first-order edges,
    so a sharp photo does not read as a noisy one -- which a plain Laplacian
    would.
    """
    import numpy  # noqa: PLC0415

    response = (
        luma[:-2, :-2] - 2.0 * luma[:-2, 1:-1] + luma[:-2, 2:]
        - 2.0 * luma[1:-1, :-2] + 4.0 * luma[1:-1, 1:-1] - 2.0 * luma[1:-1, 2:]
        + luma[2:, :-2] - 2.0 * luma[2:, 1:-1] + luma[2:, 2:]
    )
    height, width = luma.shape
    scale = math.sqrt(math.pi / 2.0) / (6.0 * (width - 2) * (height - 2))
    return float(scale * numpy.abs(response).sum())


def _entropy(luma: Any) -> float:
    import numpy  # noqa: PLC0415

    counts, _ = numpy.histogram(luma, bins=HISTOGRAM_BINS, range=(0.0, 1.0))
    total = counts.sum()
    if total == 0:
        return 0.0
    probabilities = counts[counts > 0] / total
    entropy = float(-(probabilities * numpy.log2(probabilities)).sum())
    return entropy / math.log2(HISTOGRAM_BINS)


def measure(proxy_path: str | Path, *, proxy_kind: str = "thumbnail_512") -> ClassicalQuality:
    """Measure one proxy. Raises rather than returning a plausible zero."""
    import numpy  # noqa: PLC0415

    if proxy_kind not in SUPPORTED_PROXY_KINDS:
        raise ClassicalQualityError(
            f"{EXECUTOR_ID} {EXECUTOR_VERSION} is calibrated for "
            f"{sorted(SUPPORTED_PROXY_KINDS)}, not {proxy_kind!r}"
        )
    path = Path(proxy_path)
    if not path.is_file():
        raise ClassicalQualityError("proxy file is missing")

    luma = _luma(path, max_edge=512)

    variance = _laplacian_variance(luma)
    sigma = _noise_sigma(luma)
    mean = float(numpy.mean(luma))
    std = float(numpy.std(luma))
    high = float(numpy.count_nonzero(luma >= HIGHLIGHT_LEVEL)) / luma.size
    low = float(numpy.count_nonzero(luma <= SHADOW_LEVEL)) / luma.size
    percentile99 = float(numpy.percentile(luma, 99))

    sharpness = _quantise(_saturating(variance, SHARPNESS_HALF_POINT))
    noise = _quantise(1.0 - _saturating(sigma, NOISE_HALF_POINT))
    contrast = _quantise(std / CONTRAST_FULL_SCALE)

    clipped = high + low
    over_tolerance = max(0.0, clipped - CLIP_FREE_FRACTION)
    span = CLIP_FULL_PENALTY_FRACTION - CLIP_FREE_FRACTION
    clip_penalty = min(1.0, over_tolerance / span)
    # "1.0 is a well-distributed histogram" (the contract's words), so the
    # spread term is literally the normalised Shannon entropy of the histogram,
    # discounted by whatever was clipped off either end.
    exposure = _quantise((1.0 - clip_penalty) * _entropy(luma))

    is_black_frame = mean <= BLACK_LUMA_MAX and percentile99 <= BLACK_LUMA_MAX * 3.0
    is_lens_obstructed = (
        not is_black_frame
        and mean <= POCKET_LUMA_MAX
        and sharpness <= POCKET_SHARPNESS_MAX
    )

    return ClassicalQuality(
        sharpness=sharpness,
        exposure=exposure,
        noise=noise,
        contrast=contrast,
        is_black_frame=is_black_frame,
        is_lens_obstructed=is_lens_obstructed,
        raw={
            "laplacian_variance": round(variance, 9),
            "noise_sigma": round(sigma, 9),
            "luma_mean": round(mean, 6),
            "luma_std": round(std, 6),
            "clipped_fraction": round(clipped, 6),
            "entropy": round(_entropy(luma), 6),
        },
    )


def measure_face_sharpness(
    proxy_path: str | Path,
    boxes: Sequence[Mapping[str, float]],
    *,
    proxy_kind: str = "thumbnail_512",
) -> list[float | None]:
    """Sharpness of each face crop, in box order. None = too small to measure.

    `boxes` are normalised {x, y, w, h} against the ORIENTED image, which is
    what the thumbnail proxy is. Each crop is resized to
    FACE_SHARPNESS_STD_EDGE_PX square before measuring -- see the constant
    block for why scale normalisation is the whole point.
    """
    import numpy  # noqa: PLC0415
    from PIL import Image, ImageOps  # noqa: PLC0415

    if proxy_kind not in SUPPORTED_PROXY_KINDS:
        raise ClassicalQualityError(
            f"face sharpness is calibrated for {sorted(SUPPORTED_PROXY_KINDS)}, "
            f"not {proxy_kind!r}"
        )
    path = Path(proxy_path)
    if not path.is_file():
        raise ClassicalQualityError("proxy file is missing")

    with Image.open(path) as handle:
        grey = ImageOps.exif_transpose(handle).convert("L")
        width, height = grey.size
        results: list[float | None] = []
        for box in boxes:
            x0 = max(0, int(float(box["x"]) * width))
            y0 = max(0, int(float(box["y"]) * height))
            x1 = min(width, int((float(box["x"]) + float(box["w"])) * width))
            y1 = min(height, int((float(box["y"]) + float(box["h"])) * height))
            if x1 - x0 < FACE_SHARPNESS_MIN_EDGE_PX or y1 - y0 < FACE_SHARPNESS_MIN_EDGE_PX:
                results.append(None)
                continue
            crop = grey.crop((x0, y0, x1, y1)).resize(
                (FACE_SHARPNESS_STD_EDGE_PX, FACE_SHARPNESS_STD_EDGE_PX),
                Image.LANCZOS,
            )
            array = numpy.asarray(crop, dtype=numpy.float64) / 255.0
            results.append(
                _quantise(
                    _saturating(_laplacian_variance(array), FACE_SHARPNESS_HALF_POINT)
                )
            )
    return results
