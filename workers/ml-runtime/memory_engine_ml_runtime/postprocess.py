"""Detector decoding and oriented-image coordinate conversion.

The model fixtures are the specification for this module.  None of these
operations are delegated to a caller: decoded anchor deltas must cross the
gRPC boundary exactly once, after deterministic NMS and normalisation.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

import numpy as np

LANDMARK_BOUNDS = (-0.5, 1.5)


@dataclass(frozen=True)
class RasterTransform:
    """The realised integer raster used to letterbox an oriented proxy."""

    source_width: int
    source_height: int
    target_width: int
    target_height: int
    scaled_width: int
    scaled_height: int
    pad_left: int
    pad_top: int
    pad_right: int
    pad_bottom: int

    @property
    def ideal_scale(self) -> float:
        return min(
            self.target_width / self.source_width,
            self.target_height / self.source_height,
        )

    @property
    def effective_scale_x(self) -> float:
        return self.scaled_width / self.source_width

    @property
    def effective_scale_y(self) -> float:
        return self.scaled_height / self.source_height

    def to_normalized(self, x: float, y: float) -> tuple[float, float]:
        # Written this way rather than cancelling source_width/source_height so
        # the exact coordinate basis remains obvious at the privacy boundary.
        source_x = (x - self.pad_left) / self.effective_scale_x
        source_y = (y - self.pad_top) / self.effective_scale_y
        return source_x / self.source_width, source_y / self.source_height


def _round_positive(value: float) -> int:
    """Round a positive raster dimension to nearest, with .5 rounded upward."""

    return math.floor(value + 0.5)


def integer_letterbox(
    source_width: int,
    source_height: int,
    target_width: int,
    target_height: int,
) -> RasterTransform:
    if min(source_width, source_height, target_width, target_height) <= 0:
        raise ValueError("letterbox dimensions must be positive")
    scale = min(target_width / source_width, target_height / source_height)
    scaled_width = _round_positive(source_width * scale)
    scaled_height = _round_positive(source_height * scale)
    pad_left = (target_width - scaled_width) // 2
    pad_top = (target_height - scaled_height) // 2
    return RasterTransform(
        source_width=source_width,
        source_height=source_height,
        target_width=target_width,
        target_height=target_height,
        scaled_width=scaled_width,
        scaled_height=scaled_height,
        pad_left=pad_left,
        pad_top=pad_top,
        pad_right=target_width - scaled_width - pad_left,
        pad_bottom=target_height - scaled_height - pad_top,
    )


@dataclass(frozen=True)
class Candidate:
    x1: float
    y1: float
    x2: float
    y2: float
    score: float
    landmarks: tuple[tuple[float, float], ...] = ()

    @property
    def area(self) -> float:
        return max(0.0, self.x2 - self.x1) * max(0.0, self.y2 - self.y1)


@dataclass(frozen=True)
class NormalizedDetection:
    x: float
    y: float
    w: float
    h: float
    score: float
    landmarks: tuple[tuple[float, float], ...]
    landmarks_out_of_range: bool


@dataclass(frozen=True)
class DetectionOutput:
    detections: tuple[NormalizedDetection, ...]
    score_threshold: float
    nms_iou_threshold: float
    truncated: bool
    landmark_scheme: str


def yunet_decode_bbox(
    cell: tuple[int, int], raw: Sequence[float], stride: int
) -> Candidate:
    """Decode YuNet's centre offsets and exponentiated width/height."""

    if len(raw) != 4:
        raise ValueError("YuNet bbox output must contain four values")
    column, row = cell
    cx = (column + float(raw[0])) * stride
    cy = (row + float(raw[1])) * stride
    width = math.exp(float(raw[2])) * stride
    height = math.exp(float(raw[3])) * stride
    return Candidate(
        x1=cx - width / 2.0,
        y1=cy - height / 2.0,
        x2=cx + width / 2.0,
        y2=cy + height / 2.0,
        score=0.0,
    )


def yunet_decode_kps(
    cell: tuple[int, int], raw: Sequence[float], stride: int
) -> tuple[tuple[float, float], ...]:
    """Decode YuNet keypoints in the model's own five-point ordering."""

    if len(raw) % 2:
        raise ValueError("YuNet keypoint output must contain coordinate pairs")
    column, row = cell
    return tuple(
        (
            (float(raw[index]) + column) * stride,
            (float(raw[index + 1]) + row) * stride,
        )
        for index in range(0, len(raw), 2)
    )


def geometric_mean_score(classification: float, objectness: float) -> float:
    classification = min(max(float(classification), 0.0), 1.0)
    objectness = min(max(float(objectness), 0.0), 1.0)
    return math.sqrt(classification * objectness)


def distance2bbox(
    center: tuple[float, float], raw: Sequence[float], stride: int
) -> Candidate:
    if len(raw) != 4:
        raise ValueError("SCRFD bbox output must contain four values")
    cx, cy = center
    left, top, right, bottom = (float(value) * stride for value in raw)
    return Candidate(cx - left, cy - top, cx + right, cy + bottom, 0.0)


def distance2kps(
    center: tuple[float, float], raw: Sequence[float], stride: int
) -> tuple[tuple[float, float], ...]:
    if len(raw) % 2:
        raise ValueError("SCRFD keypoint output must contain coordinate pairs")
    cx, cy = center
    return tuple(
        (cx + float(raw[index]) * stride, cy + float(raw[index + 1]) * stride)
        for index in range(0, len(raw), 2)
    )


def anchor_centers(
    stride: int, feature_width: int, feature_height: int, num_anchors: int = 1
) -> tuple[tuple[float, float], ...]:
    centers = tuple(
        (float(column * stride), float(row * stride))
        for row in range(feature_height)
        for column in range(feature_width)
    )
    return tuple(center for center in centers for _ in range(num_anchors))


def num_anchors_for(output_count: int) -> int:
    if output_count in (6, 9):
        return 2
    if output_count in (10, 15):
        return 1
    raise ValueError(
        f"unrecognised SCRFD output count {output_count}; expected 6, 9, 10, or 15"
    )


def _sort_key(candidate: Candidate) -> tuple[float, float, float, float, float]:
    return (-candidate.score, candidate.x1, candidate.y1, candidate.x2, candidate.y2)


def _float_iou(left: Candidate, right: Candidate) -> float:
    ix1 = max(left.x1, right.x1)
    iy1 = max(left.y1, right.y1)
    ix2 = min(left.x2, right.x2)
    iy2 = min(left.y2, right.y2)
    intersection = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    union = left.area + right.area - intersection
    return intersection / union if union > 0 else 0.0


def _integer_rect(candidate: Candidate) -> tuple[int, int, int, int]:
    x = int(candidate.x1)
    y = int(candidate.y1)
    return x, y, int(candidate.x2) - x, int(candidate.y2) - y


def _integer_rect_iou(left: Candidate, right: Candidate) -> float:
    lx, ly, lw, lh = _integer_rect(left)
    rx, ry, rw, rh = _integer_rect(right)
    intersection_width = max(0, min(lx + lw, rx + rw) - max(lx, rx))
    intersection_height = max(0, min(ly + lh, ry + rh) - max(ly, ry))
    intersection = intersection_width * intersection_height
    union = lw * lh + rw * rh - intersection
    return intersection / union if union > 0 else 0.0


def nms(candidates: Sequence[Candidate], threshold: float) -> list[Candidate]:
    kept: list[Candidate] = []
    for candidate in sorted(candidates, key=_sort_key):
        if all(_float_iou(candidate, existing) <= threshold for existing in kept):
            kept.append(candidate)
    return kept


def integer_rect_nms(
    candidates: Sequence[Candidate], threshold: float, top_k: int | None = None
) -> list[Candidate]:
    ordered = sorted(candidates, key=_sort_key)
    if top_k is not None:
        ordered = ordered[:top_k]
    kept: list[Candidate] = []
    for candidate in ordered:
        if all(
            _integer_rect_iou(candidate, existing) <= threshold for existing in kept
        ):
            kept.append(candidate)
    return kept


def normalize_candidate(
    candidate: Candidate, raster: RasterTransform
) -> NormalizedDetection | None:
    x1, y1 = raster.to_normalized(candidate.x1, candidate.y1)
    x2, y2 = raster.to_normalized(candidate.x2, candidate.y2)
    clipped_x1 = min(max(x1, 0.0), 1.0)
    clipped_y1 = min(max(y1, 0.0), 1.0)
    clipped_x2 = min(max(x2, 0.0), 1.0)
    clipped_y2 = min(max(y2, 0.0), 1.0)
    width = clipped_x2 - clipped_x1
    height = clipped_y2 - clipped_y1
    if width <= 0.0 or height <= 0.0:
        return None

    landmarks = tuple(raster.to_normalized(x, y) for x, y in candidate.landmarks)
    lower, upper = LANDMARK_BOUNDS
    out_of_range = any(
        x < lower or x > upper or y < lower or y > upper for x, y in landmarks
    )
    if out_of_range:
        landmarks = ()
    return NormalizedDetection(
        x=clipped_x1,
        y=clipped_y1,
        w=width,
        h=height,
        score=candidate.score,
        landmarks=landmarks,
        landmarks_out_of_range=out_of_range,
    )


def _rows(array: np.ndarray, width: int) -> np.ndarray:
    values = np.asarray(array)
    if values.size == 0:
        return values.reshape(0, width)
    if width == 1:
        return values.reshape(-1, 1)
    if values.size % width:
        raise ValueError(
            f"output with {values.size} values cannot form rows of {width}"
        )
    return values.reshape(-1, width)


def _output_by_stride(
    outputs: Mapping[str, np.ndarray], config: Mapping[str, Any], meaning: str
) -> dict[int, np.ndarray]:
    selected: dict[int, np.ndarray] = {}
    for output in config.get("outputs", []):
        if output.get("meaning") != meaning:
            continue
        name = str(output["name"])
        try:
            stride = int(name.rsplit("_", 1)[1])
        except (IndexError, ValueError) as error:
            raise ValueError(
                f"detector output {name!r} has no stride suffix"
            ) from error
        if name not in outputs:
            raise ValueError(f"detector output {name!r} is missing")
        selected[stride] = outputs[name]
    return selected


def _decode_yunet(
    outputs: Mapping[str, np.ndarray], config: Mapping[str, Any]
) -> list[Candidate]:
    preprocessing = config["preprocessing"]
    target = preprocessing["input_size"]
    postprocessing = config["postprocessing"]
    # YuNet has two score groups with the same semantic meaning. Bind the heads
    # by their pinned names, never by whichever dictionary order a caller used.
    cls_by_stride = {
        int(name.rsplit("_", 1)[1]): value
        for name, value in outputs.items()
        if name.startswith("cls_")
    }
    obj_by_stride = {
        int(name.rsplit("_", 1)[1]): value
        for name, value in outputs.items()
        if name.startswith("obj_")
    }
    strides = {int(value) for value in postprocessing["strides"]}
    if set(cls_by_stride) != strides or set(obj_by_stride) != strides:
        raise ValueError("YuNet classification/objectness heads are missing")
    bboxes = _output_by_stride(outputs, config, "bboxes")
    keypoints = _output_by_stride(outputs, config, "keypoints")
    threshold = float(postprocessing["score_threshold"])
    candidates: list[Candidate] = []
    for stride in postprocessing["strides"]:
        stride = int(stride)
        cls_rows = _rows(cls_by_stride[stride], 1)
        obj_rows = _rows(obj_by_stride[stride], 1)
        bbox_rows = _rows(bboxes[stride], 4)
        kps_rows = _rows(keypoints[stride], 10)
        expected = (target["width"] // stride) * (target["height"] // stride)
        if not all(
            len(rows) == expected for rows in (cls_rows, obj_rows, bbox_rows, kps_rows)
        ):
            raise ValueError(
                f"YuNet stride {stride} output count does not match its raster"
            )
        feature_width = target["width"] // stride
        for index in range(expected):
            score = geometric_mean_score(cls_rows[index, 0], obj_rows[index, 0])
            if score < threshold:
                continue
            cell = index % feature_width, index // feature_width
            box = yunet_decode_bbox(cell, bbox_rows[index], stride)
            candidates.append(
                replace(
                    box,
                    score=score,
                    landmarks=yunet_decode_kps(cell, kps_rows[index], stride),
                )
            )
    return candidates


def _decode_scrfd(
    outputs: Mapping[str, np.ndarray], config: Mapping[str, Any]
) -> list[Candidate]:
    preprocessing = config["preprocessing"]
    target = preprocessing["input_size"]
    postprocessing = config["postprocessing"]
    scores = _output_by_stride(outputs, config, "scores")
    bboxes = _output_by_stride(outputs, config, "bboxes")
    keypoints = _output_by_stride(outputs, config, "keypoints")
    anchors_per_location = num_anchors_for(len(outputs))
    threshold = float(postprocessing["score_threshold"])
    candidates: list[Candidate] = []
    for stride in postprocessing["strides"]:
        stride = int(stride)
        score_rows = _rows(scores[stride], 1)
        bbox_rows = _rows(bboxes[stride], 4)
        kps_rows = _rows(keypoints[stride], 10) if keypoints else None
        centers = anchor_centers(
            stride,
            target["width"] // stride,
            target["height"] // stride,
            anchors_per_location,
        )
        if len(score_rows) != len(centers) or len(bbox_rows) != len(centers):
            raise ValueError(
                f"SCRFD stride {stride} output count does not match its anchors"
            )
        if kps_rows is not None and len(kps_rows) != len(centers):
            raise ValueError(
                f"SCRFD stride {stride} keypoint count does not match its anchors"
            )
        for index, center in enumerate(centers):
            score = float(score_rows[index, 0])
            if score < threshold:
                continue
            box = distance2bbox(center, bbox_rows[index], stride)
            landmarks = (
                distance2kps(center, kps_rows[index], stride)
                if kps_rows is not None
                else ()
            )
            candidates.append(replace(box, score=score, landmarks=landmarks))
    return candidates


def postprocess_detector(
    outputs: Mapping[str, np.ndarray],
    config: Mapping[str, Any],
    raster: RasterTransform,
) -> DetectionOutput:
    """Decode, suppress, normalise, and cap one detector item's output."""

    postprocessing = config["postprocessing"]
    steps = set(postprocessing["steps"])
    score_threshold = float(postprocessing["score_threshold"])
    nms_threshold = float(postprocessing["nms_threshold"])
    if "yunet_decode_bbox" in steps:
        candidates = _decode_yunet(outputs, config)
        kept = integer_rect_nms(
            candidates,
            nms_threshold,
            int(postprocessing["top_k"]) if postprocessing.get("top_k") else None,
        )
    elif "distance2bbox" in steps:
        candidates = _decode_scrfd(outputs, config)
        kept = nms(candidates, nms_threshold)
    else:
        raise ValueError("registered detector has no supported decoder")

    normalized = tuple(
        detection
        for candidate in kept
        if (detection := normalize_candidate(candidate, raster)) is not None
    )
    maximum = int(postprocessing.get("max_detections") or 0)
    truncated = maximum > 0 and len(normalized) > maximum
    if truncated:
        normalized = normalized[:maximum]
    return DetectionOutput(
        detections=normalized,
        score_threshold=score_threshold,
        nms_iou_threshold=nms_threshold,
        truncated=truncated,
        landmark_scheme=str(postprocessing.get("landmark_scheme") or ""),
    )
