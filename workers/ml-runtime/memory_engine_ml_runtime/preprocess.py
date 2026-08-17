"""Fail-closed proxy resolution and model-config-driven preprocessing."""

from __future__ import annotations

import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from blake3 import blake3

from contracts.proto.generated.python import ml_runtime_pb2 as pb2

from .postprocess import RasterTransform, integer_letterbox

ProxyResolver = Callable[[str], Mapping[str, Any] | None]


@dataclass(frozen=True)
class InputFailure(Exception):
    code: int
    message: str
    retryable: bool = False


@dataclass(frozen=True)
class PreparedItem:
    item_id: str
    feeds: Mapping[str, np.ndarray]
    raster: RasterTransform | None


_PROTO_DTYPES: dict[int, np.dtype[Any]] = {
    pb2.DTYPE_FLOAT32: np.dtype("<f4"),
    pb2.DTYPE_FLOAT16: np.dtype("<f2"),
    pb2.DTYPE_INT64: np.dtype("<i8"),
    pb2.DTYPE_INT32: np.dtype("<i4"),
    pb2.DTYPE_UINT8: np.dtype("u1"),
}

_LANDMARK_SCHEMES = {
    pb2.LANDMARK_SCHEME_INSIGHTFACE_5: "insightface_5",
    pb2.LANDMARK_SCHEME_INSIGHTFACE_106: "insightface_106",
    pb2.LANDMARK_SCHEME_MEDIAPIPE_468: "mediapipe_468",
    pb2.LANDMARK_SCHEME_YUNET_5: "yunet_5",
}

_IMAGE_PROXY_KINDS = frozenset({"thumbnail_512", "preview_2048", "contact_sheet_tile"})


class ProxyLoader:
    """Open only paths returned by media-db's proxy resolver and verify identity."""

    def __init__(self, resolver: ProxyResolver | None) -> None:
        self._resolver = resolver
        self._digest_cache: dict[tuple[str, int, int], str] = {}
        self._lock = threading.Lock()

    def load_image(self, proxy_id: str) -> tuple[np.ndarray, Mapping[str, Any]]:
        if self._resolver is None:
            raise InputFailure(
                pb2.ERROR_CODE_PROXY_NOT_FOUND,
                "proxy store is not configured",
            )
        try:
            proxy = self._resolver(proxy_id)
        except Exception as error:
            raise InputFailure(
                pb2.ERROR_CODE_PROXY_NOT_FOUND,
                "proxy store lookup failed",
                retryable=True,
            ) from error
        if proxy is None or proxy.get("proxy_id") != proxy_id:
            raise InputFailure(pb2.ERROR_CODE_PROXY_NOT_FOUND, "proxy was not found")
        if proxy.get("kind") not in _IMAGE_PROXY_KINDS:
            raise InputFailure(
                pb2.ERROR_CODE_UNSUPPORTED_INPUT,
                "proxy kind is not an image input",
            )
        raw_path = proxy.get("path")
        if not isinstance(raw_path, str) or not raw_path:
            raise InputFailure(pb2.ERROR_CODE_INPUT_INVALID, "proxy record has no path")
        path = Path(raw_path)
        try:
            stat = path.stat()
        except OSError as error:
            raise InputFailure(
                pb2.ERROR_CODE_PROXY_NOT_FOUND,
                "proxy bytes are unavailable",
                retryable=True,
            ) from error
        expected_bytes = proxy.get("byte_size")
        if expected_bytes is not None and int(expected_bytes) != stat.st_size:
            raise InputFailure(
                pb2.ERROR_CODE_INPUT_INVALID, "proxy size does not match its record"
            )
        if self._digest(path, stat.st_size, stat.st_mtime_ns) != proxy_id:
            raise InputFailure(
                pb2.ERROR_CODE_INPUT_INVALID, "proxy content hash does not match"
            )

        image = cv2.imread(str(path), cv2.IMREAD_COLOR)
        if image is None or image.ndim != 3 or image.shape[2] != 3:
            raise InputFailure(
                pb2.ERROR_CODE_INPUT_INVALID, "proxy image could not be decoded"
            )
        size = proxy.get("size")
        if isinstance(size, Mapping):
            expected = int(size.get("width", 0)), int(size.get("height", 0))
            actual = image.shape[1], image.shape[0]
            if expected != actual:
                raise InputFailure(
                    pb2.ERROR_CODE_INPUT_INVALID,
                    "decoded proxy dimensions do not match its record",
                )
        return image, proxy

    def _digest(self, path: Path, size: int, mtime_ns: int) -> str:
        key = str(path.resolve()), size, mtime_ns
        with self._lock:
            cached = self._digest_cache.get(key)
        if cached is not None:
            return cached
        digest = blake3()
        try:
            with path.open("rb") as stream:
                while chunk := stream.read(1024 * 1024):
                    digest.update(chunk)
        except OSError as error:
            raise InputFailure(
                pb2.ERROR_CODE_PROXY_NOT_FOUND,
                "proxy bytes became unavailable",
                retryable=True,
            ) from error
        value = digest.hexdigest()
        with self._lock:
            self._digest_cache = {key: value}
        return value


def _interpolation(name: str) -> int:
    try:
        return {
            "nearest": cv2.INTER_NEAREST,
            "bilinear": cv2.INTER_LINEAR,
            "bicubic": cv2.INTER_CUBIC,
            "lanczos": cv2.INTER_LANCZOS4,
        }[name]
    except KeyError as error:
        raise InputFailure(
            pb2.ERROR_CODE_INPUT_INVALID,
            "model config names an unsupported interpolation mode",
        ) from error


def _normalise_image(
    image_bgr: np.ndarray, preprocessing: Mapping[str, Any]
) -> np.ndarray:
    input_size = preprocessing.get("input_size")
    if not isinstance(input_size, Mapping):
        raise InputFailure(
            pb2.ERROR_CODE_INPUT_INVALID, "image model has no input size"
        )
    target_width = int(input_size["width"])
    target_height = int(input_size["height"])
    if image_bgr.shape[1] != target_width or image_bgr.shape[0] != target_height:
        if preprocessing.get("resize") == "none":
            raise InputFailure(
                pb2.ERROR_CODE_INPUT_INVALID,
                "aligned image does not match the configured input size",
            )
        image_bgr = cv2.resize(
            image_bgr,
            (target_width, target_height),
            interpolation=_interpolation(str(preprocessing.get("interpolation"))),
        )

    color_order = preprocessing.get("color_order")
    if color_order == "rgb":
        image = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
    elif color_order == "bgr":
        image = image_bgr
    else:
        raise InputFailure(
            pb2.ERROR_CODE_INPUT_INVALID, "unsupported model color order"
        )
    values = image.astype(np.float32)
    values *= float(preprocessing.get("scale", 1.0))
    mean = preprocessing.get("mean") or []
    standard_deviation = preprocessing.get("std") or []
    if mean:
        values -= np.asarray(mean, dtype=np.float32).reshape(1, 1, -1)
    if standard_deviation:
        values /= np.asarray(standard_deviation, dtype=np.float32).reshape(1, 1, -1)
    layout = preprocessing.get("layout")
    if layout == "nchw":
        values = np.transpose(values, (2, 0, 1))
    elif layout != "nhwc":
        raise InputFailure(
            pb2.ERROR_CODE_INPUT_INVALID, "unsupported image tensor layout"
        )
    return np.expand_dims(np.ascontiguousarray(values), axis=0)


def _letterbox(
    image_bgr: np.ndarray, preprocessing: Mapping[str, Any]
) -> tuple[np.ndarray, RasterTransform]:
    input_size = preprocessing["input_size"]
    raster = integer_letterbox(
        image_bgr.shape[1],
        image_bgr.shape[0],
        int(input_size["width"]),
        int(input_size["height"]),
    )
    resized = cv2.resize(
        image_bgr,
        (raster.scaled_width, raster.scaled_height),
        interpolation=_interpolation(str(preprocessing.get("interpolation"))),
    )
    letterboxed = cv2.copyMakeBorder(
        resized,
        raster.pad_top,
        raster.pad_bottom,
        raster.pad_left,
        raster.pad_right,
        cv2.BORDER_CONSTANT,
        value=(0, 0, 0),
    )
    return letterboxed, raster


def _similarity_transform(
    source: Sequence[tuple[float, float]], destination: Sequence[Sequence[float]]
) -> np.ndarray:
    if len(source) < 2 or len(source) != len(destination):
        raise InputFailure(
            pb2.ERROR_CODE_INPUT_INVALID, "landmark count does not match template"
        )
    source_values = np.asarray(source, dtype=np.float64)
    destination_values = np.asarray(destination, dtype=np.float64)
    source_center = source_values.mean(axis=0)
    destination_center = destination_values.mean(axis=0)
    source_zero = source_values - source_center
    destination_zero = destination_values - destination_center
    denominator = float(np.sum(source_zero * source_zero))
    if denominator == 0.0:
        raise InputFailure(pb2.ERROR_CODE_INPUT_INVALID, "landmarks are degenerate")
    a = (
        float(
            np.sum(source_zero[:, 0] * destination_zero[:, 0])
            + np.sum(source_zero[:, 1] * destination_zero[:, 1])
        )
        / denominator
    )
    b = (
        float(
            np.sum(source_zero[:, 0] * destination_zero[:, 1])
            - np.sum(source_zero[:, 1] * destination_zero[:, 0])
        )
        / denominator
    )
    tx = destination_center[0] - (a * source_center[0] - b * source_center[1])
    ty = destination_center[1] - (b * source_center[0] + a * source_center[1])
    return np.asarray([[a, -b, tx], [b, a, ty]], dtype=np.float64)


def _align_face(
    image_bgr: np.ndarray, item: pb2.InferItem, preprocessing: Mapping[str, Any]
) -> np.ndarray:
    alignment = preprocessing["face_alignment"]
    expected_scheme = str(alignment["landmark_scheme"])
    actual_scheme = _LANDMARK_SCHEMES.get(item.landmark_scheme, "")
    if actual_scheme != expected_scheme:
        raise InputFailure(
            pb2.ERROR_CODE_INPUT_INVALID,
            "landmark scheme does not match the alignment template",
        )
    source = tuple(
        (point.x * image_bgr.shape[1], point.y * image_bgr.shape[0])
        for point in item.landmarks
    )
    destination = alignment["template"]
    transform = _similarity_transform(source, destination)
    output_size = alignment["output_size"]
    return cv2.warpAffine(
        image_bgr,
        transform,
        (int(output_size["width"]), int(output_size["height"])),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=(0, 0, 0),
    )


def _validate_alignment(item: pb2.InferItem, preprocessing: Mapping[str, Any]) -> int:
    face_alignment = preprocessing.get("face_alignment")
    alignment = item.alignment
    if face_alignment is not None and alignment == pb2.ALIGNMENT_UNSPECIFIED:
        alignment = pb2.ALIGNMENT_NEEDS_ALIGNMENT
    elif face_alignment is None and alignment == pb2.ALIGNMENT_UNSPECIFIED:
        alignment = pb2.ALIGNMENT_NONE

    input_kind = item.WhichOneof("input")
    if input_kind == "proxy_id" and alignment == pb2.ALIGNMENT_PREALIGNED:
        raise InputFailure(
            pb2.ERROR_CODE_INPUT_INVALID, "a stored proxy is not prealigned"
        )
    if alignment == pb2.ALIGNMENT_PREALIGNED and item.landmarks:
        raise InputFailure(
            pb2.ERROR_CODE_INPUT_INVALID, "prealigned input carries landmarks"
        )
    if alignment == pb2.ALIGNMENT_NEEDS_ALIGNMENT and not item.landmarks:
        raise InputFailure(
            pb2.ERROR_CODE_LANDMARKS_REQUIRED, "face landmarks are required"
        )
    if item.landmarks and item.landmark_scheme == pb2.LANDMARK_SCHEME_UNSPECIFIED:
        raise InputFailure(pb2.ERROR_CODE_INPUT_INVALID, "landmark scheme is required")
    if face_alignment is not None and alignment == pb2.ALIGNMENT_NONE:
        raise InputFailure(
            pb2.ERROR_CODE_INPUT_INVALID, "face model alignment cannot be skipped"
        )
    if face_alignment is None and alignment != pb2.ALIGNMENT_NONE:
        raise InputFailure(
            pb2.ERROR_CODE_INPUT_INVALID, "model does not accept face alignment"
        )
    return alignment


def _tensor_array(tensor: pb2.Tensor) -> np.ndarray:
    dtype = _PROTO_DTYPES.get(tensor.dtype)
    if dtype is None:
        raise InputFailure(pb2.ERROR_CODE_INPUT_INVALID, "tensor dtype is unsupported")
    shape = tuple(int(dimension) for dimension in tensor.shape)
    if not shape or any(dimension <= 0 for dimension in shape):
        raise InputFailure(
            pb2.ERROR_CODE_INPUT_INVALID, "tensor shape must be positive"
        )
    expected_size = int(np.prod(shape, dtype=np.int64)) * dtype.itemsize
    if expected_size != len(tensor.data):
        raise InputFailure(
            pb2.ERROR_CODE_INPUT_INVALID, "tensor byte length is invalid"
        )
    return np.frombuffer(tensor.data, dtype=dtype).reshape(shape).copy()


def _tensor_feeds(
    item: pb2.InferItem, config: Mapping[str, Any]
) -> Mapping[str, np.ndarray]:
    tensors = item.tensors.tensors
    if not tensors:
        raise InputFailure(pb2.ERROR_CODE_INPUT_INVALID, "tensor set is empty")
    configured_name = str(config["preprocessing"].get("input_name") or "")
    feeds: dict[str, np.ndarray] = {}
    for tensor in tensors:
        name = tensor.name
        if not name and len(tensors) == 1:
            name = configured_name
        if not name:
            raise InputFailure(
                pb2.ERROR_CODE_INPUT_NAME_UNKNOWN,
                "multi-input tensors must be named",
            )
        if name in feeds:
            raise InputFailure(
                pb2.ERROR_CODE_INPUT_INVALID, "tensor input name is duplicated"
            )
        if len(tensors) == 1 and configured_name and name != configured_name:
            raise InputFailure(
                pb2.ERROR_CODE_INPUT_NAME_UNKNOWN,
                "tensor input name is not registered for this model",
            )
        feeds[name] = _tensor_array(tensor)
    return feeds


def prepare_item(
    item: pb2.InferItem,
    config: Mapping[str, Any],
    proxy_loader: ProxyLoader,
) -> PreparedItem:
    if not item.item_id:
        raise InputFailure(pb2.ERROR_CODE_INPUT_INVALID, "item_id is required")
    input_kind = item.WhichOneof("input")
    if input_kind is None:
        raise InputFailure(pb2.ERROR_CODE_INPUT_INVALID, "item has no input")
    alignment = _validate_alignment(item, config["preprocessing"])
    if input_kind == "tensors":
        if alignment == pb2.ALIGNMENT_NEEDS_ALIGNMENT:
            raise InputFailure(
                pb2.ERROR_CODE_UNSUPPORTED_INPUT,
                "alignment of an inline tensor is not supported",
            )
        return PreparedItem(item.item_id, _tensor_feeds(item, config), None)

    if item.HasField("window"):
        raise InputFailure(
            pb2.ERROR_CODE_UNSUPPORTED_INPUT,
            "video proxy windows are not supported by this model path",
        )
    image_bgr, _ = proxy_loader.load_image(item.proxy_id)
    preprocessing = config["preprocessing"]
    if alignment == pb2.ALIGNMENT_NEEDS_ALIGNMENT:
        image_bgr = _align_face(image_bgr, item, preprocessing)
        raster = None
    elif preprocessing.get("resize") == "letterbox":
        image_bgr, raster = _letterbox(image_bgr, preprocessing)
    else:
        raster = None
    tensor = _normalise_image(image_bgr, preprocessing)
    return PreparedItem(
        item.item_id,
        {str(preprocessing["input_name"]): tensor},
        raster,
    )
