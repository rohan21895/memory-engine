"""ONNX Runtime session management, batching, and typed result construction."""

from __future__ import annotations

import importlib
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from types import ModuleType
from typing import Any

import numpy as np

from contracts.proto.generated.python import ml_runtime_pb2 as pb2

from .catalog import PRECISION_ENUM_NAMES, RUNTIME_ENUM_NAMES, ModelInspection
from .postprocess import DetectionOutput, postprocess_detector
from .preprocess import InputFailure, PreparedItem

ORT_PROVIDERS = {
    "onnxruntime_cpu": "CPUExecutionProvider",
    "onnxruntime_coreml": "CoreMLExecutionProvider",
    "onnxruntime_directml": "DmlExecutionProvider",
    "onnxruntime_cuda": "CUDAExecutionProvider",
}

PROTO_TO_RUNTIME = {
    getattr(pb2, enum_name): runtime
    for runtime, enum_name in RUNTIME_ENUM_NAMES.items()
}

_ORT_INPUT_DTYPES: dict[str, np.dtype[Any]] = {
    "tensor(float)": np.dtype(np.float32),
    "tensor(float16)": np.dtype(np.float16),
    "tensor(int64)": np.dtype(np.int64),
    "tensor(int32)": np.dtype(np.int32),
    "tensor(uint8)": np.dtype(np.uint8),
}

_PROTO_OUTPUT_DTYPES: dict[np.dtype[Any], int] = {
    np.dtype(np.float32): pb2.DTYPE_FLOAT32,
    np.dtype(np.float16): pb2.DTYPE_FLOAT16,
    np.dtype(np.int64): pb2.DTYPE_INT64,
    np.dtype(np.int32): pb2.DTYPE_INT32,
    np.dtype(np.uint8): pb2.DTYPE_UINT8,
}

_LANDMARK_SCHEMES = {
    "insightface_5": pb2.LANDMARK_SCHEME_INSIGHTFACE_5,
    "insightface_106": pb2.LANDMARK_SCHEME_INSIGHTFACE_106,
    "mediapipe_468": pb2.LANDMARK_SCHEME_MEDIAPIPE_468,
    "yunet_5": pb2.LANDMARK_SCHEME_YUNET_5,
}


@dataclass(frozen=True)
class ExecutionFailure(Exception):
    code: int
    message: str
    retryable: bool = False
    retry_after_ms: int = 0


@dataclass(frozen=True)
class LoadedModel:
    inspection: ModelInspection
    runtime_name: str
    runtime_enum: int
    provider_name: str
    session: Any

    @property
    def pin(self) -> pb2.ModelPin:
        config = self.inspection.config
        weights = (
            config.get("weights") if isinstance(config.get("weights"), Mapping) else {}
        )
        precision = str(weights.get("quantization", ""))
        return pb2.ModelPin(
            model_id=self.inspection.model_id,
            version=str(config.get("version", "")),
            weights_blake3=self.inspection.weights_blake3 or "",
            config_blake3=self.inspection.config_blake3 or "",
            runtime=self.runtime_enum,
            precision=getattr(
                pb2,
                PRECISION_ENUM_NAMES.get(precision, "PRECISION_UNSPECIFIED"),
            ),
        )


class SessionManager:
    """Thread-safe, lazy ONNX session cache keyed by model and runtime."""

    def __init__(self, ort_module: ModuleType | Any | None = None) -> None:
        self._ort = ort_module
        self._sessions: dict[tuple[str, str], LoadedModel] = {}
        self._lock = threading.RLock()

    def load(
        self,
        inspection: ModelInspection,
        preferred_runtimes: Sequence[int],
    ) -> LoadedModel:
        runtime_name = self._select_runtime(inspection, preferred_runtimes)
        key = inspection.model_id, runtime_name
        with self._lock:
            cached = self._sessions.get(key)
            if cached is not None:
                return cached
            if inspection.weights_path is None:
                raise ExecutionFailure(
                    pb2.ERROR_CODE_MODEL_UNLOADABLE,
                    "model weights are unavailable",
                )
            ort = self._ort
            if ort is None:
                try:
                    ort = importlib.import_module("onnxruntime")
                except ModuleNotFoundError as error:
                    raise ExecutionFailure(
                        pb2.ERROR_CODE_PROVIDER_UNAVAILABLE,
                        "ONNX Runtime is not installed",
                        retryable=True,
                    ) from error
                self._ort = ort
            provider = ORT_PROVIDERS[runtime_name]
            try:
                session_options = ort.SessionOptions()
                if hasattr(ort, "GraphOptimizationLevel"):
                    session_options.graph_optimization_level = (
                        ort.GraphOptimizationLevel.ORT_ENABLE_ALL
                    )
                session = ort.InferenceSession(
                    str(inspection.weights_path),
                    sess_options=session_options,
                    providers=[provider],
                )
            except Exception as error:
                raise ExecutionFailure(
                    pb2.ERROR_CODE_PROVIDER_UNAVAILABLE,
                    "model session could not be created on the selected provider",
                    retryable=True,
                ) from error
            if provider not in session.get_providers():
                raise ExecutionFailure(
                    pb2.ERROR_CODE_PROVIDER_UNAVAILABLE,
                    "selected provider was not activated",
                    retryable=True,
                )
            self._validate_session_contract(session, inspection)
            loaded = LoadedModel(
                inspection=inspection,
                runtime_name=runtime_name,
                runtime_enum=getattr(pb2, RUNTIME_ENUM_NAMES[runtime_name]),
                provider_name=provider,
                session=session,
            )
            self._sessions[key] = loaded
            return loaded

    def loaded(self) -> list[LoadedModel]:
        with self._lock:
            return list(self._sessions.values())

    def unload(self, model_id: str) -> tuple[bool, int]:
        with self._lock:
            keys = [key for key in self._sessions if key[0] == model_id]
            sessions = [self._sessions.pop(key) for key in keys]
        freed = sum(
            model.inspection.weights_path.stat().st_size
            for model in sessions
            if model.inspection.weights_path is not None
            and model.inspection.weights_path.is_file()
        )
        return bool(sessions), freed

    @staticmethod
    def _validate_session_contract(session: Any, inspection: ModelInspection) -> None:
        input_name = str(inspection.config["preprocessing"].get("input_name") or "")
        actual_inputs = {item.name for item in session.get_inputs()}
        if input_name and input_name not in actual_inputs:
            raise ExecutionFailure(
                pb2.ERROR_CODE_CONFIG_MISMATCH,
                "configured model input is absent from the weights",
            )
        expected_outputs = [
            str(output["name"]) for output in inspection.config["outputs"]
        ]
        actual_outputs = {item.name for item in session.get_outputs()}
        if any(name not in actual_outputs for name in expected_outputs):
            raise ExecutionFailure(
                pb2.ERROR_CODE_CONFIG_MISMATCH,
                "configured model output is absent from the weights",
            )

    @staticmethod
    def _select_runtime(
        inspection: ModelInspection, preferred_runtimes: Sequence[int]
    ) -> str:
        preferred = [
            PROTO_TO_RUNTIME[value]
            for value in preferred_runtimes
            if value in PROTO_TO_RUNTIME and PROTO_TO_RUNTIME[value] in ORT_PROVIDERS
        ]
        declared = [
            str(value)
            for value in inspection.config.get("runtime_targets", [])
            if str(value) in ORT_PROVIDERS
        ]
        ordered = preferred + [value for value in declared if value not in preferred]
        for runtime in ordered:
            if runtime in inspection.available_runtimes:
                return runtime
        raise ExecutionFailure(
            pb2.ERROR_CODE_PROVIDER_UNAVAILABLE,
            "no requested model provider is available",
            retryable=True,
        )


def _infer_error(failure: InputFailure | ExecutionFailure) -> pb2.InferError:
    return pb2.InferError(
        code=failure.code,
        message=failure.message,
        retryable=failure.retryable,
        retry_after_ms=getattr(failure, "retry_after_ms", 0),
    )


def _result_error(
    item_id: str, failure: InputFailure | ExecutionFailure
) -> pb2.InferResult:
    return pb2.InferResult(item_id=item_id, error=_infer_error(failure))


def _tensor_dtype(array: np.ndarray) -> tuple[np.ndarray, int]:
    contiguous = np.ascontiguousarray(array)
    dtype = _PROTO_OUTPUT_DTYPES.get(contiguous.dtype)
    if dtype is not None:
        return contiguous, dtype
    if np.issubdtype(contiguous.dtype, np.floating):
        contiguous = contiguous.astype(np.float32)
        return contiguous, pb2.DTYPE_FLOAT32
    raise ExecutionFailure(
        pb2.ERROR_CODE_INTERNAL, "model returned an unsupported tensor dtype"
    )


def _postprocess_tensor(array: np.ndarray, steps: Sequence[str]) -> np.ndarray:
    result = np.asarray(array)
    for step in steps:
        if step == "l2_normalize":
            norm = np.linalg.norm(result, axis=-1, keepdims=True)
            result = np.divide(result, norm, out=np.zeros_like(result), where=norm != 0)
        elif step == "scale_to_unit":
            result = np.clip((result - 1.0) / 9.0, 0.0, 1.0)
        elif step == "sigmoid":
            result = 1.0 / (1.0 + np.exp(-result))
        else:
            raise ExecutionFailure(
                pb2.ERROR_CODE_MODEL_UNLOADABLE,
                "model config names an unsupported postprocessing step",
            )
    return result


def _tensor_set(
    outputs: Mapping[str, np.ndarray], config: Mapping[str, Any]
) -> pb2.TensorSet:
    steps = tuple(str(step) for step in config["postprocessing"]["steps"])
    tensors = []
    for output in config["outputs"]:
        name = str(output["name"])
        array, dtype = _tensor_dtype(_postprocess_tensor(outputs[name], steps))
        tensors.append(
            pb2.Tensor(
                shape=list(array.shape),
                dtype=dtype,
                data=array.tobytes(order="C"),
                name=name,
            )
        )
    return pb2.TensorSet(tensors=tensors)


def _detection_set(output: DetectionOutput) -> pb2.DetectionSet:
    scheme = _LANDMARK_SCHEMES.get(
        output.landmark_scheme, pb2.LANDMARK_SCHEME_UNSPECIFIED
    )
    detections = []
    for detection in output.detections:
        detections.append(
            pb2.Detection(
                box=pb2.NormalizedBox(
                    x=detection.x,
                    y=detection.y,
                    w=detection.w,
                    h=detection.h,
                    rotation_deg=0.0,
                ),
                score=detection.score,
                landmarks=[pb2.Point2D(x=x, y=y) for x, y in detection.landmarks],
                landmarks_out_of_range=detection.landmarks_out_of_range,
                landmark_scheme=scheme,
            )
        )
    return pb2.DetectionSet(
        detections=detections,
        score_threshold=output.score_threshold,
        nms_iou_threshold=output.nms_iou_threshold,
        truncated=output.truncated,
    )


class InferenceExecutor:
    """Run prepared items in deterministic, config-sized ONNX batches."""

    def run(
        self,
        loaded: LoadedModel,
        items: Sequence[PreparedItem],
        *,
        max_batch: int,
        deadline: float | None,
        is_cancelled: Callable[[], bool],
    ) -> list[pb2.InferResult]:
        results: list[pb2.InferResult] = []
        effective_max_batch = max(1, max_batch)
        for metadata in loaded.session.get_inputs():
            if metadata.shape and isinstance(metadata.shape[0], int):
                effective_max_batch = min(effective_max_batch, metadata.shape[0])
        for start in range(0, len(items), effective_max_batch):
            batch = items[start : start + effective_max_batch]
            if is_cancelled():
                failure = ExecutionFailure(
                    pb2.ERROR_CODE_CANCELLED, "inference was cancelled", retryable=True
                )
                results.extend(_result_error(item.item_id, failure) for item in batch)
                continue
            if deadline is not None and time.monotonic() >= deadline:
                failure = ExecutionFailure(
                    pb2.ERROR_CODE_DEADLINE_EXCEEDED,
                    "inference deadline elapsed",
                    retryable=True,
                )
                results.extend(_result_error(item.item_id, failure) for item in batch)
                continue
            try:
                results.extend(self._run_batch(loaded, batch))
            except (InputFailure, ExecutionFailure) as failure:
                # Shape-incompatible inline tensors should not poison otherwise
                # independent items. Retry each item separately before failing it.
                if len(batch) > 1:
                    for item in batch:
                        try:
                            results.extend(self._run_batch(loaded, [item]))
                        except (InputFailure, ExecutionFailure) as item_failure:
                            results.append(_result_error(item.item_id, item_failure))
                else:
                    results.append(_result_error(batch[0].item_id, failure))
            # A provider may surface implementation-specific exception types.
            # They must not escape gRPC or leak model/path details to telemetry.
            except Exception:  # noqa: BLE001
                failure = ExecutionFailure(
                    pb2.ERROR_CODE_INTERNAL,
                    "model inference failed",
                    retryable=True,
                )
                results.extend(_result_error(item.item_id, failure) for item in batch)
        return results

    def _run_batch(
        self, loaded: LoadedModel, items: Sequence[PreparedItem]
    ) -> list[pb2.InferResult]:
        session = loaded.session
        input_metadata = {item.name: item for item in session.get_inputs()}
        expected_names = set(input_metadata)
        if any(set(item.feeds) != expected_names for item in items):
            raise InputFailure(
                pb2.ERROR_CODE_INPUT_NAME_UNKNOWN,
                "tensor inputs do not match the model session",
            )
        feeds: dict[str, np.ndarray] = {}
        for name, metadata in input_metadata.items():
            arrays = [self._coerce_input(item.feeds[name], metadata) for item in items]
            shapes = {array.shape[1:] for array in arrays}
            if len(shapes) != 1:
                raise InputFailure(
                    pb2.ERROR_CODE_INPUT_INVALID,
                    "tensor shapes cannot share an inference batch",
                )
            feeds[name] = np.concatenate(arrays, axis=0)

        output_names = [
            str(output["name"]) for output in loaded.inspection.config["outputs"]
        ]
        try:
            raw_outputs = session.run(output_names, feeds)
        except Exception as error:
            raise ExecutionFailure(
                pb2.ERROR_CODE_INTERNAL,
                "ONNX Runtime inference failed",
                retryable=True,
            ) from error
        if len(raw_outputs) != len(output_names):
            raise ExecutionFailure(
                pb2.ERROR_CODE_INTERNAL, "model returned the wrong output count"
            )
        per_item = [{} for _ in items]
        for name, raw in zip(output_names, raw_outputs):
            array = np.asarray(raw)
            if array.ndim == 0 or array.shape[0] != len(items):
                if len(items) != 1:
                    raise ExecutionFailure(
                        pb2.ERROR_CODE_INTERNAL,
                        "batched model output has no batch axis",
                    )
                per_item[0][name] = array
                continue
            for index in range(len(items)):
                per_item[index][name] = array[index : index + 1]

        task = loaded.inspection.task
        results = []
        for item, outputs in zip(items, per_item):
            if task == "face_detection":
                if item.raster is None:
                    raise InputFailure(
                        pb2.ERROR_CODE_INPUT_INVALID,
                        "detector input has no oriented-image transform",
                    )
                detections = _detection_set(
                    postprocess_detector(outputs, loaded.inspection.config, item.raster)
                )
                results.append(
                    pb2.InferResult(item_id=item.item_id, detections=detections)
                )
            elif task == "shot_detection":
                raise InputFailure(
                    pb2.ERROR_CODE_UNSUPPORTED_INPUT,
                    "shot boundary conversion requires a video proxy window",
                )
            else:
                results.append(
                    pb2.InferResult(
                        item_id=item.item_id,
                        tensors=_tensor_set(outputs, loaded.inspection.config),
                    )
                )
        return results

    @staticmethod
    def _coerce_input(array: np.ndarray, metadata: Any) -> np.ndarray:
        expected_dtype = _ORT_INPUT_DTYPES.get(metadata.type)
        if expected_dtype is None:
            raise InputFailure(
                pb2.ERROR_CODE_INPUT_INVALID, "model input dtype is unsupported"
            )
        expected_rank = len(metadata.shape)
        value = np.asarray(array)
        if value.ndim == expected_rank - 1:
            value = np.expand_dims(value, axis=0)
        if value.ndim != expected_rank or value.shape[0] != 1:
            raise InputFailure(
                pb2.ERROR_CODE_INPUT_INVALID,
                "each inference item must contain exactly one batch element",
            )
        return np.ascontiguousarray(value.astype(expected_dtype, copy=False))


__all__ = [
    "ExecutionFailure",
    "InferenceExecutor",
    "LoadedModel",
    "SessionManager",
    "_infer_error",
]
