"""Executable reference for detector post-processing.

Pure arithmetic with no resampling ambiguity, so unlike resize this can be
pinned exactly and both implementations must agree to the last decimal.

Three steps, all of which fail quietly rather than loudly when wrong:

* **distance2bbox** turns an anchor point plus four predicted distances into a
  box. Get the anchor stride wrong and every box is offset by a consistent
  amount, which looks like a mediocre detector rather than a bug.
* **NMS** collapses overlapping detections. Too strict and one face becomes
  three; too loose and two adjacent faces in a group photo become one. Either
  way the failure surfaces as face-clustering noise a long way downstream.
* **to_normalized** maps a box out of letterboxed network pixels into the
  oriented-image [0,1] space every consumer speaks. This is the step with the
  longest blast radius: a wrong scale or pad does not error, it produces
  plausibly-placed wrong boxes, and a plausibly-placed wrong box reaches a user
  as a crop through somebody's face.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence


@dataclass(frozen=True)
class Detection:
    x1: float
    y1: float
    x2: float
    y2: float
    score: float

    @property
    def area(self) -> float:
        return max(0.0, self.x2 - self.x1) * max(0.0, self.y2 - self.y1)

    def to_json(self) -> dict:
        return {
            "x1": round(self.x1, 6),
            "y1": round(self.y1, 6),
            "x2": round(self.x2, 6),
            "y2": round(self.y2, 6),
            "score": round(self.score, 6),
        }


def anchor_centers(
    stride: int, feature_width: int, feature_height: int, num_anchors: int = 1
) -> list[tuple[float, float]]:
    """Anchor centres for one feature map, in input-image pixels.

    Row-major (y outer, x inner), matching the flattened order the ONNX exports
    emit. Iterating the other way produces boxes that are transposed in a way
    that still looks like plausible detections.

    `num_anchors` is not decoration. SCRFD variants with 6 or 9 ONNX outputs
    predict TWO anchors per location, and InsightFace duplicates each centre
    accordingly (`np.stack([centers]*n, axis=1).reshape(-1, 2)`). scrfd-10g-bnkps
    is a nine-output variant, so generating one centre per location leaves the
    centre list half the length of the score and box arrays -- every prediction
    past the first row then pairs with the wrong centre. Found by Codex in
    review; the failure is not an exception, it is a detector that appears to
    work and puts boxes in the wrong places.
    """
    centers = [
        (float(x * stride), float(y * stride))
        for y in range(feature_height)
        for x in range(feature_width)
    ]
    if num_anchors <= 1:
        return centers
    # Duplicate each centre in place, matching InsightFace's stack-then-reshape:
    # [c0, c0, c1, c1, ...], NOT [c0, c1, ..., c0, c1, ...].
    return [center for center in centers for _ in range(num_anchors)]


def num_anchors_for(output_count: int) -> int:
    """Anchors per location, derived from the ONNX output count.

    InsightFace's SCRFD loader derives this rather than configuring it: 6 or 9
    outputs mean three feature maps with two anchors each; 10 or 15 mean five
    feature maps with one. Deriving it here too means a swapped checkpoint
    cannot silently keep the old anchor count.
    """
    if output_count in (6, 9):
        return 2
    if output_count in (10, 15):
        return 1
    raise ValueError(
        f"unrecognised SCRFD output count {output_count}; "
        "expected 6, 9, 10 or 15 -- refusing to guess the anchor multiplicity"
    )


def distance2bbox(
    center: tuple[float, float], distances: tuple[float, float, float, float], stride: int
) -> Detection:
    """Anchor centre + (left, top, right, bottom) distances -> box.

    The distances come out of the network in stride units, so they are scaled by
    the stride before being applied. Omitting that scaling yields boxes roughly
    8-32x too small, which reads as "the detector missed everything".
    """
    cx, cy = center
    left, top, right, bottom = (d * stride for d in distances)
    return Detection(cx - left, cy - top, cx + right, cy + bottom, 0.0)


def iou(a: Detection, b: Detection) -> float:
    ix1 = max(a.x1, b.x1)
    iy1 = max(a.y1, b.y1)
    ix2 = min(a.x2, b.x2)
    iy2 = min(a.y2, b.y2)
    intersection = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    union = a.area + b.area - intersection
    return intersection / union if union > 0 else 0.0


def nms(detections: list[Detection], threshold: float) -> list[Detection]:
    """Greedy non-maximum suppression, highest score first.

    Ties broken by geometry rather than by input order, so the same detections
    in a different order produce the same output. Without that, a batched run
    and a single run can disagree, and a plan that is supposed to be
    reproducible quietly is not.
    """
    ordered = sorted(
        detections,
        key=lambda d: (-d.score, d.x1, d.y1, d.x2, d.y2),
    )
    kept: list[Detection] = []
    for candidate in ordered:
        if all(iou(candidate, k) <= threshold for k in kept):
            kept.append(candidate)
    return kept


def filter_by_score(detections: list[Detection], threshold: float) -> list[Detection]:
    return [d for d in detections if d.score >= threshold]


def combine_scores(classification: float, objectness: float) -> float:
    """YuNet confidence: sqrt(clamp(cls) * clamp(obj)).

    THE BUG THIS REPLACES: this returned `classification * objectness`. OpenCV's
    face_detect.cpp clamps each score to [0,1] and takes the GEOMETRIC MEAN, and
    the difference is not cosmetic -- at YuNet's configured threshold of 0.6 the
    product rule discards roughly 84% of the detections YuNet accepts. A face at
    cls=0.7, obj=0.7 scores 0.49 under the product (dropped) and 0.70 under the
    geometric mean (kept).

    That would have read as "YuNet is a much weaker detector than SCRFD", which
    is exactly the wrong conclusion to draw while YuNet is the licence-clean
    release path and SCRFD is the non-commercial one. Found by Codex in review
    and confirmed against the OpenCV source.

    The clamps are part of the formula, not defensive coding: the raw sigmoid
    outputs can land marginally outside [0,1], and sqrt of a negative product
    is not a number.
    """
    cls = min(max(classification, 0.0), 1.0)
    obj = min(max(objectness, 0.0), 1.0)
    return math.sqrt(cls * obj)


def yunet_decode_bbox(
    cell: tuple[int, int], raw: tuple[float, float, float, float], stride: int
) -> Detection:
    """YuNet box decode: centre offset plus exponentiated size.

    NOT the same decoder as SCRFD's `distance2bbox`, which is what this file
    previously applied to both. OpenCV's face_detect.cpp:

        cx = (c + raw[0]) * stride
        cy = (r + raw[1]) * stride
        w  = exp(raw[2]) * stride
        h  = exp(raw[3]) * stride

    The offsets are cell-relative fractions, not distances to the four sides.
    Feeding SCRFD's decoder YuNet's outputs produces boxes that are plausibly
    sized and consistently misplaced -- the failure mode where a detector looks
    mediocre rather than broken.
    """
    column, row = cell
    cx = (column + raw[0]) * stride
    cy = (row + raw[1]) * stride
    width = math.exp(raw[2]) * stride
    height = math.exp(raw[3]) * stride
    return Detection(cx - width / 2.0, cy - height / 2.0,
                     cx + width / 2.0, cy + height / 2.0, 0.0)


def yunet_decode_landmarks(
    cell: tuple[int, int], raw: Sequence[float], stride: int
) -> list[tuple[float, float]]:
    """YuNet landmark decode: cell-relative offsets, scaled by the stride.

        x = (raw[2n]     + c) * stride
        y = (raw[2n + 1] + r) * stride

    Linear, unlike the box's exponentiated size. Five points, in YuNet's own
    order -- see the model config's `landmark_scheme`, because ArcFace alignment
    assumes a specific ordering and a silently reordered set produces a
    plausible warp and a wrong embedding.
    """
    column, row = cell
    return [
        ((raw[2 * n] + column) * stride, (raw[2 * n + 1] + row) * stride)
        for n in range(len(raw) // 2)
    ]


def letterboxed_to_normalized(
    x: float,
    y: float,
    *,
    scale: float,
    pad_x: float,
    pad_y: float,
    source_width: int,
    source_height: int,
) -> tuple[float, float]:
    """One point, out of letterboxed network pixels and into oriented-image [0,1].

    Deliberately unclamped. Callers clamp boxes and do not clamp landmarks, and
    baking either policy in here would silently impose it on the other.
    """
    return (
        (x - pad_x) / scale / source_width,
        (y - pad_y) / scale / source_height,
    )


def to_normalized_box(
    detection: Detection,
    *,
    scale: float,
    pad_x: float,
    pad_y: float,
    source_width: int,
    source_height: int,
) -> dict | None:
    """A NormalizedBox, or None when the detection does not touch the image.

    Two rules, both of which exist because of what the alternative looks like:

    * **Clip to [0,1].** A NormalizedBox is a location in the image; a box
      claiming to extend past the frame describes nothing a renderer can crop.

    * **Drop, do not clamp, an empty intersection.** The letterbox padding band
      is uniform grey and detectors do occasionally fire on it. Clamping such a
      box yields a zero-height rectangle at y=0 -- which passes downstream as a
      face at the top edge of every affected photo, and NormalizedBox requires
      w and h strictly greater than zero, so it would not even validate.
    """
    x1, y1 = letterboxed_to_normalized(
        detection.x1, detection.y1,
        scale=scale, pad_x=pad_x, pad_y=pad_y,
        source_width=source_width, source_height=source_height,
    )
    x2, y2 = letterboxed_to_normalized(
        detection.x2, detection.y2,
        scale=scale, pad_x=pad_x, pad_y=pad_y,
        source_width=source_width, source_height=source_height,
    )

    cx1, cy1 = min(max(x1, 0.0), 1.0), min(max(y1, 0.0), 1.0)
    cx2, cy2 = min(max(x2, 0.0), 1.0), min(max(y2, 0.0), 1.0)

    width, height = cx2 - cx1, cy2 - cy1
    if width <= 0.0 or height <= 0.0:
        return None

    return {"x": cx1, "y": cy1, "w": width, "h": height}
