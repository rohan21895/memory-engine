"""Executable reference for the arithmetic in every model config.

This is the specification, not the implementation. `workers/ml-runtime` will do
this with numpy or in Rust, orders of magnitude faster; the point of a slow,
dependency-free version is that it is unambiguous and can generate golden
numbers CI checks both sides against.

WHAT IS PINNED HERE AND WHY

The three preprocessing steps that fail *silently* -- returning plausible wrong
numbers rather than erroring -- are pinned exactly:

* **Normalisation** `(pixel * scale - mean) / std`, in that order. Swap the
  order or the operands and every embedding shifts slightly. Nothing crashes;
  the taste model just quietly gets worse.
* **Channel order.** InsightFace-lineage models want BGR, nearly everything
  else RGB. A whole library embedded with the channels swapped is subtly and
  uniformly wrong.
* **Letterbox geometry.** Get the scale or padding wrong and every detected box
  lands in the wrong place, which surfaces as bad crops rather than as an error.

WHAT IS DELIBERATELY NOT PINNED

**Resampling.** Bilinear resize differs between OpenCV, PIL and ONNX Runtime in
how they place pixel centres, and no golden fixture can be honest across all
three. Pretending otherwise would produce a test that fails for good
implementations. Instead the fixtures feed images already at the model's input
size, so resampling is the identity, and the resize convention is documented as
a separate decision that needs its own measurement:

    Convention: half-pixel-centred bilinear (OpenCV's default, PIL's default,
    ONNX Resize with coordinate_transformation_mode="half_pixel").

Codex should confirm the runtime's resize matches that before trusting box
coordinates at sub-pixel precision.
"""

from __future__ import annotations

from dataclasses import dataclass

Pixel = tuple[int, int, int]  # RGB, 0-255


def synthetic_image(width: int, height: int, seed: int = 0) -> list[list[Pixel]]:
    """A deterministic test image, defined by formula rather than shipped as bytes.

    A binary fixture would be opaque and unreviewable; a formula is auditable in
    the diff and reproducible in any language. The gradients differ per channel
    so a channel swap is visible in the resulting statistics rather than
    cancelling out -- an image where R, G and B are equal would pass an
    RGB/BGR mix-up silently, which is precisely the bug being guarded.
    """
    rows: list[list[Pixel]] = []
    for y in range(height):
        row: list[Pixel] = []
        for x in range(width):
            r = (x * 7 + y * 3 + seed) % 256
            g = (x * 3 + y * 11 + seed * 2) % 256
            b = (x * 13 + y * 5 + seed * 3) % 256
            row.append((r, g, b))
        rows.append(row)
    return rows


@dataclass(frozen=True)
class TensorStats:
    """Summary of a preprocessed tensor.

    Statistics plus sampled values rather than the whole tensor: a 3x384x384
    float array is 442k numbers, which is unreviewable in a diff and enormous in
    git, while the mean, extremes and a handful of indexed values pin the
    arithmetic just as tightly.
    """

    shape: tuple[int, ...]
    count: int
    mean: float
    minimum: float
    maximum: float
    first_values: tuple[float, ...]
    sampled: dict[int, float]

    def to_json(self) -> dict:
        return {
            "shape": list(self.shape),
            "count": self.count,
            "mean": round(self.mean, 9),
            "min": round(self.minimum, 9),
            "max": round(self.maximum, 9),
            "first_values": [round(v, 9) for v in self.first_values],
            "sampled": {str(k): round(v, 9) for k, v in sorted(self.sampled.items())},
        }


def preprocess_image(
    image: list[list[Pixel]],
    *,
    color_order: str,
    layout: str,
    scale: float,
    mean: list[float],
    std: list[float],
) -> list[float]:
    """Turn an RGB image into a flat tensor, following a model config exactly.

    Order is `(pixel * scale - mean) / std`. Empty mean/std mean no shift and no
    division, which is how TransNetV2 wants its raw [0,255] input.
    """
    if color_order not in {"rgb", "bgr"}:
        raise ValueError(f"unsupported color_order {color_order!r}")
    if layout not in {"nchw", "nhwc"}:
        raise ValueError(f"unsupported layout {layout!r}")
    if mean and std and len(mean) != len(std):
        raise ValueError("mean and std must cover the same channels")

    height = len(image)
    width = len(image[0])

    # Channel `c` of the output reads pixel component `source_channel[c]`.
    # For BGR that is the reverse of the stored RGB order.
    source_channel = (0, 1, 2) if color_order == "rgb" else (2, 1, 0)

    def channel_value(pixel: Pixel, channel: int) -> float:
        value = float(pixel[source_channel[channel]])
        value *= scale
        if mean:
            value -= mean[channel]
        if std:
            value /= std[channel]
        return value

    flat: list[float] = []
    if layout == "nchw":
        for channel in range(3):
            for y in range(height):
                for x in range(width):
                    flat.append(channel_value(image[y][x], channel))
    else:
        for y in range(height):
            for x in range(width):
                for channel in range(3):
                    flat.append(channel_value(image[y][x], channel))

    return flat


def summarise(values: list[float], shape: tuple[int, ...]) -> TensorStats:
    count = len(values)
    step = max(1, count // 8)
    sampled = {i: values[i] for i in range(0, count, step)}
    return TensorStats(
        shape=shape,
        count=count,
        mean=sum(values) / count,
        minimum=min(values),
        maximum=max(values),
        first_values=tuple(values[:6]),
        sampled=sampled,
    )


@dataclass(frozen=True)
class Letterbox:
    """Geometry of a letterboxed resize, and how to undo it.

    Detectors are fed a square letterboxed image; their boxes come back in that
    padded space and must be mapped home before they mean anything. Getting this
    wrong does not error -- it produces boxes that are plausibly placed and
    wrong, which reaches a user as a crop through someone's face.
    """

    scale: float
    pad_x: float
    pad_y: float
    scaled_width: float
    scaled_height: float

    def to_source(self, x: float, y: float) -> tuple[float, float]:
        """Map a coordinate from letterboxed space back to source pixels."""
        return ((x - self.pad_x) / self.scale, (y - self.pad_y) / self.scale)

    def to_normalised(
        self, x: float, y: float, source_width: int, source_height: int
    ) -> tuple[float, float]:
        """Map to the normalised [0,1] space every contract box uses."""
        sx, sy = self.to_source(x, y)
        return (sx / source_width, sy / source_height)


def letterbox(
    source_width: int, source_height: int, target_width: int, target_height: int
) -> Letterbox:
    """Fit a source into a target box preserving aspect, centring the padding."""
    scale = min(target_width / source_width, target_height / source_height)
    scaled_width = source_width * scale
    scaled_height = source_height * scale
    return Letterbox(
        scale=scale,
        pad_x=(target_width - scaled_width) / 2.0,
        pad_y=(target_height - scaled_height) / 2.0,
        scaled_width=scaled_width,
        scaled_height=scaled_height,
    )


def similarity_transform(
    source: list[tuple[float, float]], destination: list[tuple[float, float]]
) -> tuple[float, float, float, float]:
    """Least-squares similarity transform (Umeyama, uniform scale + rotation).

    Returns (a, b, tx, ty) for:
        x' = a*x - b*y + tx
        y' = b*x + a*y + ty

    This is the face-alignment step. It is here because alignment is the one
    preprocessing stage whose omission is catastrophic rather than merely
    degrading: an unaligned crop into ArcFace yields a confidently wrong
    embedding, which clusters wrongly, which puts the wrong person in a family
    album. Precision-over-recall starts with this arithmetic being right.
    """
    n = len(source)
    if n < 2 or n != len(destination):
        raise ValueError("need at least two matched point pairs")

    src_cx = sum(p[0] for p in source) / n
    src_cy = sum(p[1] for p in source) / n
    dst_cx = sum(p[0] for p in destination) / n
    dst_cy = sum(p[1] for p in destination) / n

    num_a = num_b = den = 0.0
    for (sx, sy), (dx, dy) in zip(source, destination):
        sx0, sy0 = sx - src_cx, sy - src_cy
        dx0, dy0 = dx - dst_cx, dy - dst_cy
        num_a += sx0 * dx0 + sy0 * dy0
        num_b += sx0 * dy0 - sy0 * dx0
        den += sx0 * sx0 + sy0 * sy0

    if den == 0:
        raise ValueError("degenerate source points")

    a = num_a / den
    b = num_b / den
    tx = dst_cx - (a * src_cx - b * src_cy)
    ty = dst_cy - (b * src_cx + a * src_cy)
    return (a, b, tx, ty)


def apply_transform(
    transform: tuple[float, float, float, float], x: float, y: float
) -> tuple[float, float]:
    a, b, tx, ty = transform
    return (a * x - b * y + tx, b * x + a * y + ty)
