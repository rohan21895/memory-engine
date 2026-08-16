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

from dataclasses import dataclass


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


def anchor_centers(stride: int, feature_width: int, feature_height: int) -> list[tuple[float, float]]:
    """Anchor centres for one feature map, in input-image pixels.

    Row-major (y outer, x inner), matching the flattened order the ONNX exports
    emit. Iterating the other way produces boxes that are transposed in a way
    that still looks like plausible detections.
    """
    return [
        (float(x * stride), float(y * stride))
        for y in range(feature_height)
        for x in range(feature_width)
    ]


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
    """YuNet emits classification and objectness separately; confidence is their
    product. Using either alone roughly doubles the false-positive rate."""
    return classification * objectness


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
