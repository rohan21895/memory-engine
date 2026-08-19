"""Classical auto-develop: the EnhancementOps a photo needs, measured, not felt.

WHAT THIS IS

The AlbumSpec has carried `Placement.enhancement_ops` since the contract was
written, and the print renderer refused any album that used them, because
execution was not implemented and a silently-ignored improvement is worse
than none. This module is the PLANNING half: measure each selected photo on
its thumbnail proxy and emit the small, classical corrections most phone
photos need -- levels, white balance, print sharpening. The renderer applies
exactly what the plan says (workers/render-print/src/enhance.ts), so the same
AlbumSpec still produces the identical PDF; hard rule 3 is untouched because
the decisions live here, in the plan.

WHAT IT DELIBERATELY IS NOT

No models, no learned anything -- `model: null, license_cleared: true` is
honest for arithmetic. No gamma surgery, no local contrast, no skin-aware
masking: those are the enhance GPU host's territory (build plan §"Enhancement
stack") and arrive with license audits. And no correction strong enough to
change what a photo IS: every parameter is capped, because a baby under an
orange night lamp is a scene, not a colour cast, and "corrected" to neutral
grey it becomes a lie about the night it records.

DETERMINISM

Measurements run on the committed thumbnail proxy at a fixed size, every
emitted parameter is quantised to six decimals, and the op list for a photo
is a pure function of (proxy bytes, PLANNER_VERSION here). The ops feed the
album's inputs digest, so a formula change re-plans as a DIFFERENT album
rather than mutating an existing one.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

__all__ = ["DEVELOP_VERSION", "plan_develop_ops"]

DEVELOP_VERSION = "1.0.0"

# --- levels -----------------------------------------------------------------
#: Shadows are lifted (flat, hazy blacks) when the 1st-percentile luma sits
#: above this; the stretch pulls it back toward 0.
_BLACK_LIFT_TRIGGER = 0.04
#: Highlights are dull when the 99th percentile sits below this.
_WHITE_DULL_TRIGGER = 0.92
#: The stretch is capped: never crush more than this much shadow or lift more
#: than this much highlight, or backlit scenes lose their subject.
_MAX_BLACK_POINT = 0.10
_MIN_WHITE_POINT = 0.85
#: Midtone brightness: target median luma, lift trigger, and the cap that
#: keeps a night scene a night scene.
_BRIGHTNESS_TARGET_MEDIAN = 0.42
_BRIGHTNESS_TRIGGER_MEDIAN = 0.32
_MAX_BRIGHTNESS = 1.30

# --- white balance ----------------------------------------------------------
#: Channel gains come from the grey-world mean of mid-tone pixels only:
#: shadows carry compressed colour and highlights carry clipped colour, and
#: both would shout over the pixels that actually show the cast.
_WB_MIDTONE_LOW = 0.15
_WB_MIDTONE_HIGH = 0.90
#: Below this relative deviation the photo is already neutral and no op is
#: emitted -- a 1% "correction" is churn, not colour science.
_WB_SKIP_DEVIATION = 0.03
#: Gain cap. A raw grey-world gain beyond this is a lit scene (sunset, night
#: lamp, stage light); the correction applies AT the cap and the reason says
#: so, keeping the mood while taming the extreme.
_WB_MAX_GAIN = 1.15
_WB_MIN_GAIN = 1.0 / _WB_MAX_GAIN

# --- print sharpening -------------------------------------------------------
#: Applied to every placement after the resize to print resolution. Light on
#: purpose: output sharpening compensates the resample and the press, it does
#: not rescue focus -- the album gate already refused out-of-focus faces.
_SHARPEN_SIGMA = 1.0
_SHARPEN_FLAT = 0.6
_SHARPEN_JAGGED = 1.4


def _quantise(value: float) -> float:
    return round(float(value), 6)


def _load_rgb(proxy_path: str | Path) -> Any:
    import numpy  # noqa: PLC0415
    from PIL import Image, ImageOps  # noqa: PLC0415

    with Image.open(proxy_path) as handle:
        oriented = ImageOps.exif_transpose(handle).convert("RGB")
        return numpy.asarray(oriented, dtype=numpy.float64) / 255.0


def plan_develop_ops(proxy_path: str | Path, media_id: str) -> list[dict[str, Any]]:
    """The EnhancementOps this photo needs, in application order.

    Returns contract-shaped dicts ready to sit in
    `Placement.enhancement_ops`. Raises only if the proxy cannot be read --
    a photo the planner cannot measure must fail the plan loudly, not ship
    undeveloped next to twenty-three developed neighbours.
    """
    import numpy  # noqa: PLC0415

    rgb = _load_rgb(proxy_path)
    luma = 0.2126 * rgb[..., 0] + 0.7152 * rgb[..., 1] + 0.0722 * rgb[..., 2]
    p1, median, p99 = (
        float(numpy.percentile(luma, 1)),
        float(numpy.percentile(luma, 50)),
        float(numpy.percentile(luma, 99)),
    )
    tag = media_id[:8]
    ops: list[dict[str, Any]] = []
    order = 0

    # -- exposure: levels stretch + capped midtone lift ----------------------
    black_point = min(p1, _MAX_BLACK_POINT) if p1 > _BLACK_LIFT_TRIGGER else 0.0
    white_point = max(p99, _MIN_WHITE_POINT) if p99 < _WHITE_DULL_TRIGGER else 1.0
    brightness = 1.0
    if median < _BRIGHTNESS_TRIGGER_MEDIAN and median > 0.0:
        brightness = min(_BRIGHTNESS_TARGET_MEDIAN / median, _MAX_BRIGHTNESS)
    if black_point > 0.0 or white_point < 1.0 or brightness != 1.0:
        ops.append(
            {
                "op_id": f"exposure-{tag}",
                "kind": "exposure",
                "order": order,
                "model": None,
                "license_cleared": True,
                "parameters": {
                    "black_point": _quantise(black_point),
                    "white_point": _quantise(white_point),
                    "brightness": _quantise(brightness),
                },
                "strength": None,
                "reason": (
                    f"measured luma p1={p1:.3f} median={median:.3f} p99={p99:.3f}; "
                    "levels stretched and midtones lifted within caps"
                ),
            }
        )
        order += 1

    # -- white balance: grey-world on midtones, capped -----------------------
    midtones = (luma >= _WB_MIDTONE_LOW) & (luma <= _WB_MIDTONE_HIGH)
    if int(midtones.sum()) >= 256:
        means = [float(rgb[..., channel][midtones].mean()) for channel in range(3)]
        grey = sum(means) / 3.0
        if grey > 0.0 and min(means) > 0.0:
            raw_gains = [grey / mean for mean in means]
            deviation = max(abs(gain - 1.0) for gain in raw_gains)
            if deviation > _WB_SKIP_DEVIATION:
                gains = [
                    min(max(gain, _WB_MIN_GAIN), _WB_MAX_GAIN) for gain in raw_gains
                ]
                capped = any(
                    abs(raw - applied) > 1e-9
                    for raw, applied in zip(raw_gains, gains, strict=True)
                )
                ops.append(
                    {
                        "op_id": f"white-balance-{tag}",
                        "kind": "white_balance",
                        "order": order,
                        "model": None,
                        "license_cleared": True,
                        "parameters": {
                            "gain_r": _quantise(gains[0]),
                            "gain_g": _quantise(gains[1]),
                            "gain_b": _quantise(gains[2]),
                        },
                        "strength": None,
                        "reason": (
                            f"grey-world midtone means r={means[0]:.3f} "
                            f"g={means[1]:.3f} b={means[2]:.3f}"
                            + (
                                "; gains capped -- a cast this strong is scene "
                                "light, and the mood stays"
                                if capped
                                else ""
                            )
                        ),
                    }
                )
                order += 1

    # -- print output sharpening: every placement, always --------------------
    ops.append(
        {
            "op_id": f"sharpen-{tag}",
            "kind": "sharpen",
            "order": order,
            "model": None,
            "license_cleared": True,
            "parameters": {
                "sigma": _quantise(_SHARPEN_SIGMA),
                "flat": _quantise(_SHARPEN_FLAT),
                "jagged": _quantise(_SHARPEN_JAGGED),
            },
            "strength": None,
            "reason": "output sharpening after the resize to print resolution",
        }
    )
    return ops
