# GENERATED FILE -- DO NOT EDIT.
#
# Produced by contracts/proto/generate.py from contracts/proto/ml_runtime.proto
# with grpcio-tools==1.68.1. Edit the .proto and re-run `npm run codegen`.
# CI fails if these files drift from the proto.

from google.protobuf.internal import containers as _containers
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from typing import ClassVar as _ClassVar, Iterable as _Iterable, Mapping as _Mapping, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class RuntimeTarget(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    RUNTIME_TARGET_UNSPECIFIED: _ClassVar[RuntimeTarget]
    RUNTIME_TARGET_ONNXRUNTIME_CPU: _ClassVar[RuntimeTarget]
    RUNTIME_TARGET_ONNXRUNTIME_COREML: _ClassVar[RuntimeTarget]
    RUNTIME_TARGET_ONNXRUNTIME_DIRECTML: _ClassVar[RuntimeTarget]
    RUNTIME_TARGET_ONNXRUNTIME_CUDA: _ClassVar[RuntimeTarget]
    RUNTIME_TARGET_CTRANSLATE2: _ClassVar[RuntimeTarget]
    RUNTIME_TARGET_MLX: _ClassVar[RuntimeTarget]
    RUNTIME_TARGET_LLAMA_CPP: _ClassVar[RuntimeTarget]
    RUNTIME_TARGET_OPENCV: _ClassVar[RuntimeTarget]
    RUNTIME_TARGET_LIBROSA: _ClassVar[RuntimeTarget]
    RUNTIME_TARGET_NATIVE: _ClassVar[RuntimeTarget]

class Precision(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    PRECISION_UNSPECIFIED: _ClassVar[Precision]
    PRECISION_FP32: _ClassVar[Precision]
    PRECISION_FP16: _ClassVar[Precision]
    PRECISION_BF16: _ClassVar[Precision]
    PRECISION_INT8: _ClassVar[Precision]
    PRECISION_INT4: _ClassVar[Precision]

class LoadMode(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    LOAD_MODE_UNSPECIFIED: _ClassVar[LoadMode]
    LOAD_MODE_RELEASE: _ClassVar[LoadMode]
    LOAD_MODE_DEVELOPMENT: _ClassVar[LoadMode]

class UnloadableReason(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    UNLOADABLE_REASON_UNSPECIFIED: _ClassVar[UnloadableReason]
    UNLOADABLE_REASON_NOT_REGISTERED: _ClassVar[UnloadableReason]
    UNLOADABLE_REASON_WEIGHTS_MISSING: _ClassVar[UnloadableReason]
    UNLOADABLE_REASON_HASH_MISMATCH: _ClassVar[UnloadableReason]
    UNLOADABLE_REASON_HASH_UNPINNED: _ClassVar[UnloadableReason]
    UNLOADABLE_REASON_LICENSE_UNVERIFIED: _ClassVar[UnloadableReason]
    UNLOADABLE_REASON_LICENSE_BLOCKS_RELEASE: _ClassVar[UnloadableReason]
    UNLOADABLE_REASON_NO_PROVIDER_AVAILABLE: _ClassVar[UnloadableReason]
    UNLOADABLE_REASON_CONFIG_INVALID: _ClassVar[UnloadableReason]
    UNLOADABLE_REASON_CONFIG_MISSING: _ClassVar[UnloadableReason]
    UNLOADABLE_REASON_CONFIG_MISMATCH: _ClassVar[UnloadableReason]
    UNLOADABLE_REASON_CONFIG_UNPINNED: _ClassVar[UnloadableReason]

class LandmarkScheme(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    LANDMARK_SCHEME_UNSPECIFIED: _ClassVar[LandmarkScheme]
    LANDMARK_SCHEME_INSIGHTFACE_5: _ClassVar[LandmarkScheme]
    LANDMARK_SCHEME_INSIGHTFACE_106: _ClassVar[LandmarkScheme]
    LANDMARK_SCHEME_MEDIAPIPE_468: _ClassVar[LandmarkScheme]

class DType(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    DTYPE_UNSPECIFIED: _ClassVar[DType]
    DTYPE_FLOAT32: _ClassVar[DType]
    DTYPE_FLOAT16: _ClassVar[DType]
    DTYPE_INT64: _ClassVar[DType]
    DTYPE_INT32: _ClassVar[DType]
    DTYPE_UINT8: _ClassVar[DType]

class OutputKind(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    OUTPUT_KIND_UNSPECIFIED: _ClassVar[OutputKind]
    OUTPUT_KIND_TENSORS: _ClassVar[OutputKind]
    OUTPUT_KIND_DETECTIONS: _ClassVar[OutputKind]
    OUTPUT_KIND_SHOTS: _ClassVar[OutputKind]

class ErrorCode(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    ERROR_CODE_UNSPECIFIED: _ClassVar[ErrorCode]
    ERROR_CODE_MODEL_NOT_REGISTERED: _ClassVar[ErrorCode]
    ERROR_CODE_MODEL_UNLOADABLE: _ClassVar[ErrorCode]
    ERROR_CODE_PIN_MISMATCH: _ClassVar[ErrorCode]
    ERROR_CODE_PROXY_NOT_FOUND: _ClassVar[ErrorCode]
    ERROR_CODE_INPUT_INVALID: _ClassVar[ErrorCode]
    ERROR_CODE_LANDMARKS_REQUIRED: _ClassVar[ErrorCode]
    ERROR_CODE_UNSUPPORTED_INPUT: _ClassVar[ErrorCode]
    ERROR_CODE_LICENSE_BLOCKED: _ClassVar[ErrorCode]
    ERROR_CODE_CONFIG_MISMATCH: _ClassVar[ErrorCode]
    ERROR_CODE_INPUT_NAME_UNKNOWN: _ClassVar[ErrorCode]
    ERROR_CODE_RESOURCE_EXHAUSTED: _ClassVar[ErrorCode]
    ERROR_CODE_PROVIDER_UNAVAILABLE: _ClassVar[ErrorCode]
    ERROR_CODE_DEADLINE_EXCEEDED: _ClassVar[ErrorCode]
    ERROR_CODE_CANCELLED: _ClassVar[ErrorCode]
    ERROR_CODE_MODEL_LOADING: _ClassVar[ErrorCode]
    ERROR_CODE_INTERNAL: _ClassVar[ErrorCode]
RUNTIME_TARGET_UNSPECIFIED: RuntimeTarget
RUNTIME_TARGET_ONNXRUNTIME_CPU: RuntimeTarget
RUNTIME_TARGET_ONNXRUNTIME_COREML: RuntimeTarget
RUNTIME_TARGET_ONNXRUNTIME_DIRECTML: RuntimeTarget
RUNTIME_TARGET_ONNXRUNTIME_CUDA: RuntimeTarget
RUNTIME_TARGET_CTRANSLATE2: RuntimeTarget
RUNTIME_TARGET_MLX: RuntimeTarget
RUNTIME_TARGET_LLAMA_CPP: RuntimeTarget
RUNTIME_TARGET_OPENCV: RuntimeTarget
RUNTIME_TARGET_LIBROSA: RuntimeTarget
RUNTIME_TARGET_NATIVE: RuntimeTarget
PRECISION_UNSPECIFIED: Precision
PRECISION_FP32: Precision
PRECISION_FP16: Precision
PRECISION_BF16: Precision
PRECISION_INT8: Precision
PRECISION_INT4: Precision
LOAD_MODE_UNSPECIFIED: LoadMode
LOAD_MODE_RELEASE: LoadMode
LOAD_MODE_DEVELOPMENT: LoadMode
UNLOADABLE_REASON_UNSPECIFIED: UnloadableReason
UNLOADABLE_REASON_NOT_REGISTERED: UnloadableReason
UNLOADABLE_REASON_WEIGHTS_MISSING: UnloadableReason
UNLOADABLE_REASON_HASH_MISMATCH: UnloadableReason
UNLOADABLE_REASON_HASH_UNPINNED: UnloadableReason
UNLOADABLE_REASON_LICENSE_UNVERIFIED: UnloadableReason
UNLOADABLE_REASON_LICENSE_BLOCKS_RELEASE: UnloadableReason
UNLOADABLE_REASON_NO_PROVIDER_AVAILABLE: UnloadableReason
UNLOADABLE_REASON_CONFIG_INVALID: UnloadableReason
UNLOADABLE_REASON_CONFIG_MISSING: UnloadableReason
UNLOADABLE_REASON_CONFIG_MISMATCH: UnloadableReason
UNLOADABLE_REASON_CONFIG_UNPINNED: UnloadableReason
LANDMARK_SCHEME_UNSPECIFIED: LandmarkScheme
LANDMARK_SCHEME_INSIGHTFACE_5: LandmarkScheme
LANDMARK_SCHEME_INSIGHTFACE_106: LandmarkScheme
LANDMARK_SCHEME_MEDIAPIPE_468: LandmarkScheme
DTYPE_UNSPECIFIED: DType
DTYPE_FLOAT32: DType
DTYPE_FLOAT16: DType
DTYPE_INT64: DType
DTYPE_INT32: DType
DTYPE_UINT8: DType
OUTPUT_KIND_UNSPECIFIED: OutputKind
OUTPUT_KIND_TENSORS: OutputKind
OUTPUT_KIND_DETECTIONS: OutputKind
OUTPUT_KIND_SHOTS: OutputKind
ERROR_CODE_UNSPECIFIED: ErrorCode
ERROR_CODE_MODEL_NOT_REGISTERED: ErrorCode
ERROR_CODE_MODEL_UNLOADABLE: ErrorCode
ERROR_CODE_PIN_MISMATCH: ErrorCode
ERROR_CODE_PROXY_NOT_FOUND: ErrorCode
ERROR_CODE_INPUT_INVALID: ErrorCode
ERROR_CODE_LANDMARKS_REQUIRED: ErrorCode
ERROR_CODE_UNSUPPORTED_INPUT: ErrorCode
ERROR_CODE_LICENSE_BLOCKED: ErrorCode
ERROR_CODE_CONFIG_MISMATCH: ErrorCode
ERROR_CODE_INPUT_NAME_UNKNOWN: ErrorCode
ERROR_CODE_RESOURCE_EXHAUSTED: ErrorCode
ERROR_CODE_PROVIDER_UNAVAILABLE: ErrorCode
ERROR_CODE_DEADLINE_EXCEEDED: ErrorCode
ERROR_CODE_CANCELLED: ErrorCode
ERROR_CODE_MODEL_LOADING: ErrorCode
ERROR_CODE_INTERNAL: ErrorCode

class ModelPin(_message.Message):
    __slots__ = ("model_id", "version", "weights_blake3", "config_blake3", "runtime", "precision")
    MODEL_ID_FIELD_NUMBER: _ClassVar[int]
    VERSION_FIELD_NUMBER: _ClassVar[int]
    WEIGHTS_BLAKE3_FIELD_NUMBER: _ClassVar[int]
    CONFIG_BLAKE3_FIELD_NUMBER: _ClassVar[int]
    RUNTIME_FIELD_NUMBER: _ClassVar[int]
    PRECISION_FIELD_NUMBER: _ClassVar[int]
    model_id: str
    version: str
    weights_blake3: str
    config_blake3: str
    runtime: RuntimeTarget
    precision: Precision
    def __init__(self, model_id: _Optional[str] = ..., version: _Optional[str] = ..., weights_blake3: _Optional[str] = ..., config_blake3: _Optional[str] = ..., runtime: _Optional[_Union[RuntimeTarget, str]] = ..., precision: _Optional[_Union[Precision, str]] = ...) -> None: ...

class NormalizedBox(_message.Message):
    __slots__ = ("x", "y", "w", "h", "rotation_deg")
    X_FIELD_NUMBER: _ClassVar[int]
    Y_FIELD_NUMBER: _ClassVar[int]
    W_FIELD_NUMBER: _ClassVar[int]
    H_FIELD_NUMBER: _ClassVar[int]
    ROTATION_DEG_FIELD_NUMBER: _ClassVar[int]
    x: float
    y: float
    w: float
    h: float
    rotation_deg: float
    def __init__(self, x: _Optional[float] = ..., y: _Optional[float] = ..., w: _Optional[float] = ..., h: _Optional[float] = ..., rotation_deg: _Optional[float] = ...) -> None: ...

class Point2D(_message.Message):
    __slots__ = ("x", "y")
    X_FIELD_NUMBER: _ClassVar[int]
    Y_FIELD_NUMBER: _ClassVar[int]
    x: float
    y: float
    def __init__(self, x: _Optional[float] = ..., y: _Optional[float] = ...) -> None: ...

class RationalTime(_message.Message):
    __slots__ = ("value", "rate")
    VALUE_FIELD_NUMBER: _ClassVar[int]
    RATE_FIELD_NUMBER: _ClassVar[int]
    value: float
    rate: float
    def __init__(self, value: _Optional[float] = ..., rate: _Optional[float] = ...) -> None: ...

class TimeRange(_message.Message):
    __slots__ = ("start_time", "duration")
    START_TIME_FIELD_NUMBER: _ClassVar[int]
    DURATION_FIELD_NUMBER: _ClassVar[int]
    start_time: RationalTime
    duration: RationalTime
    def __init__(self, start_time: _Optional[_Union[RationalTime, _Mapping]] = ..., duration: _Optional[_Union[RationalTime, _Mapping]] = ...) -> None: ...

class ListModelsRequest(_message.Message):
    __slots__ = ("task", "include_unloadable")
    TASK_FIELD_NUMBER: _ClassVar[int]
    INCLUDE_UNLOADABLE_FIELD_NUMBER: _ClassVar[int]
    task: str
    include_unloadable: bool
    def __init__(self, task: _Optional[str] = ..., include_unloadable: bool = ...) -> None: ...

class ListModelsResponse(_message.Message):
    __slots__ = ("models", "load_mode")
    MODELS_FIELD_NUMBER: _ClassVar[int]
    LOAD_MODE_FIELD_NUMBER: _ClassVar[int]
    models: _containers.RepeatedCompositeFieldContainer[ModelInfo]
    load_mode: LoadMode
    def __init__(self, models: _Optional[_Iterable[_Union[ModelInfo, _Mapping]]] = ..., load_mode: _Optional[_Union[LoadMode, str]] = ...) -> None: ...

class ModelInfo(_message.Message):
    __slots__ = ("pin", "task", "loadable", "unloadable_reason", "currently_loaded", "available_runtimes", "max_batch", "blocks_commercial_release", "output_kind", "input_names")
    PIN_FIELD_NUMBER: _ClassVar[int]
    TASK_FIELD_NUMBER: _ClassVar[int]
    LOADABLE_FIELD_NUMBER: _ClassVar[int]
    UNLOADABLE_REASON_FIELD_NUMBER: _ClassVar[int]
    CURRENTLY_LOADED_FIELD_NUMBER: _ClassVar[int]
    AVAILABLE_RUNTIMES_FIELD_NUMBER: _ClassVar[int]
    MAX_BATCH_FIELD_NUMBER: _ClassVar[int]
    BLOCKS_COMMERCIAL_RELEASE_FIELD_NUMBER: _ClassVar[int]
    OUTPUT_KIND_FIELD_NUMBER: _ClassVar[int]
    INPUT_NAMES_FIELD_NUMBER: _ClassVar[int]
    pin: ModelPin
    task: str
    loadable: bool
    unloadable_reason: UnloadableReason
    currently_loaded: bool
    available_runtimes: _containers.RepeatedScalarFieldContainer[RuntimeTarget]
    max_batch: int
    blocks_commercial_release: bool
    output_kind: OutputKind
    input_names: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, pin: _Optional[_Union[ModelPin, _Mapping]] = ..., task: _Optional[str] = ..., loadable: bool = ..., unloadable_reason: _Optional[_Union[UnloadableReason, str]] = ..., currently_loaded: bool = ..., available_runtimes: _Optional[_Iterable[_Union[RuntimeTarget, str]]] = ..., max_batch: _Optional[int] = ..., blocks_commercial_release: bool = ..., output_kind: _Optional[_Union[OutputKind, str]] = ..., input_names: _Optional[_Iterable[str]] = ...) -> None: ...

class HealthRequest(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class HealthResponse(_message.Message):
    __slots__ = ("serving", "load_mode", "loaded", "queue_depth", "uptime_seconds", "warnings")
    SERVING_FIELD_NUMBER: _ClassVar[int]
    LOAD_MODE_FIELD_NUMBER: _ClassVar[int]
    LOADED_FIELD_NUMBER: _ClassVar[int]
    QUEUE_DEPTH_FIELD_NUMBER: _ClassVar[int]
    UPTIME_SECONDS_FIELD_NUMBER: _ClassVar[int]
    WARNINGS_FIELD_NUMBER: _ClassVar[int]
    serving: bool
    load_mode: LoadMode
    loaded: _containers.RepeatedCompositeFieldContainer[ModelPin]
    queue_depth: int
    uptime_seconds: int
    warnings: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, serving: bool = ..., load_mode: _Optional[_Union[LoadMode, str]] = ..., loaded: _Optional[_Iterable[_Union[ModelPin, _Mapping]]] = ..., queue_depth: _Optional[int] = ..., uptime_seconds: _Optional[int] = ..., warnings: _Optional[_Iterable[str]] = ...) -> None: ...

class InferRequest(_message.Message):
    __slots__ = ("request_id", "model_id", "expected_pin", "items", "preferred_runtimes", "deadline_ms", "priority")
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    MODEL_ID_FIELD_NUMBER: _ClassVar[int]
    EXPECTED_PIN_FIELD_NUMBER: _ClassVar[int]
    ITEMS_FIELD_NUMBER: _ClassVar[int]
    PREFERRED_RUNTIMES_FIELD_NUMBER: _ClassVar[int]
    DEADLINE_MS_FIELD_NUMBER: _ClassVar[int]
    PRIORITY_FIELD_NUMBER: _ClassVar[int]
    request_id: str
    model_id: str
    expected_pin: ModelPin
    items: _containers.RepeatedCompositeFieldContainer[InferItem]
    preferred_runtimes: _containers.RepeatedScalarFieldContainer[RuntimeTarget]
    deadline_ms: int
    priority: int
    def __init__(self, request_id: _Optional[str] = ..., model_id: _Optional[str] = ..., expected_pin: _Optional[_Union[ModelPin, _Mapping]] = ..., items: _Optional[_Iterable[_Union[InferItem, _Mapping]]] = ..., preferred_runtimes: _Optional[_Iterable[_Union[RuntimeTarget, str]]] = ..., deadline_ms: _Optional[int] = ..., priority: _Optional[int] = ...) -> None: ...

class InferItem(_message.Message):
    __slots__ = ("item_id", "proxy_id", "tensors", "window", "landmarks", "landmark_scheme")
    ITEM_ID_FIELD_NUMBER: _ClassVar[int]
    PROXY_ID_FIELD_NUMBER: _ClassVar[int]
    TENSORS_FIELD_NUMBER: _ClassVar[int]
    WINDOW_FIELD_NUMBER: _ClassVar[int]
    LANDMARKS_FIELD_NUMBER: _ClassVar[int]
    LANDMARK_SCHEME_FIELD_NUMBER: _ClassVar[int]
    item_id: str
    proxy_id: str
    tensors: TensorSet
    window: TimeRange
    landmarks: _containers.RepeatedCompositeFieldContainer[Point2D]
    landmark_scheme: LandmarkScheme
    def __init__(self, item_id: _Optional[str] = ..., proxy_id: _Optional[str] = ..., tensors: _Optional[_Union[TensorSet, _Mapping]] = ..., window: _Optional[_Union[TimeRange, _Mapping]] = ..., landmarks: _Optional[_Iterable[_Union[Point2D, _Mapping]]] = ..., landmark_scheme: _Optional[_Union[LandmarkScheme, str]] = ...) -> None: ...

class Tensor(_message.Message):
    __slots__ = ("shape", "dtype", "data", "name")
    SHAPE_FIELD_NUMBER: _ClassVar[int]
    DTYPE_FIELD_NUMBER: _ClassVar[int]
    DATA_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    shape: _containers.RepeatedScalarFieldContainer[int]
    dtype: DType
    data: bytes
    name: str
    def __init__(self, shape: _Optional[_Iterable[int]] = ..., dtype: _Optional[_Union[DType, str]] = ..., data: _Optional[bytes] = ..., name: _Optional[str] = ...) -> None: ...

class TensorSet(_message.Message):
    __slots__ = ("tensors",)
    TENSORS_FIELD_NUMBER: _ClassVar[int]
    tensors: _containers.RepeatedCompositeFieldContainer[Tensor]
    def __init__(self, tensors: _Optional[_Iterable[_Union[Tensor, _Mapping]]] = ...) -> None: ...

class InferResponse(_message.Message):
    __slots__ = ("request_id", "pin", "runtime_used", "results", "duration_ms", "batch_size", "error")
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    PIN_FIELD_NUMBER: _ClassVar[int]
    RUNTIME_USED_FIELD_NUMBER: _ClassVar[int]
    RESULTS_FIELD_NUMBER: _ClassVar[int]
    DURATION_MS_FIELD_NUMBER: _ClassVar[int]
    BATCH_SIZE_FIELD_NUMBER: _ClassVar[int]
    ERROR_FIELD_NUMBER: _ClassVar[int]
    request_id: str
    pin: ModelPin
    runtime_used: RuntimeTarget
    results: _containers.RepeatedCompositeFieldContainer[InferResult]
    duration_ms: int
    batch_size: int
    error: InferError
    def __init__(self, request_id: _Optional[str] = ..., pin: _Optional[_Union[ModelPin, _Mapping]] = ..., runtime_used: _Optional[_Union[RuntimeTarget, str]] = ..., results: _Optional[_Iterable[_Union[InferResult, _Mapping]]] = ..., duration_ms: _Optional[int] = ..., batch_size: _Optional[int] = ..., error: _Optional[_Union[InferError, _Mapping]] = ...) -> None: ...

class InferResult(_message.Message):
    __slots__ = ("item_id", "error", "tensors", "detections", "shots")
    ITEM_ID_FIELD_NUMBER: _ClassVar[int]
    ERROR_FIELD_NUMBER: _ClassVar[int]
    TENSORS_FIELD_NUMBER: _ClassVar[int]
    DETECTIONS_FIELD_NUMBER: _ClassVar[int]
    SHOTS_FIELD_NUMBER: _ClassVar[int]
    item_id: str
    error: InferError
    tensors: TensorSet
    detections: DetectionSet
    shots: ShotBoundarySet
    def __init__(self, item_id: _Optional[str] = ..., error: _Optional[_Union[InferError, _Mapping]] = ..., tensors: _Optional[_Union[TensorSet, _Mapping]] = ..., detections: _Optional[_Union[DetectionSet, _Mapping]] = ..., shots: _Optional[_Union[ShotBoundarySet, _Mapping]] = ...) -> None: ...

class Detection(_message.Message):
    __slots__ = ("box", "score", "landmarks", "landmark_scheme", "class_id", "class_label")
    BOX_FIELD_NUMBER: _ClassVar[int]
    SCORE_FIELD_NUMBER: _ClassVar[int]
    LANDMARKS_FIELD_NUMBER: _ClassVar[int]
    LANDMARK_SCHEME_FIELD_NUMBER: _ClassVar[int]
    CLASS_ID_FIELD_NUMBER: _ClassVar[int]
    CLASS_LABEL_FIELD_NUMBER: _ClassVar[int]
    box: NormalizedBox
    score: float
    landmarks: _containers.RepeatedCompositeFieldContainer[Point2D]
    landmark_scheme: LandmarkScheme
    class_id: int
    class_label: str
    def __init__(self, box: _Optional[_Union[NormalizedBox, _Mapping]] = ..., score: _Optional[float] = ..., landmarks: _Optional[_Iterable[_Union[Point2D, _Mapping]]] = ..., landmark_scheme: _Optional[_Union[LandmarkScheme, str]] = ..., class_id: _Optional[int] = ..., class_label: _Optional[str] = ...) -> None: ...

class DetectionSet(_message.Message):
    __slots__ = ("detections", "score_threshold", "nms_iou_threshold", "truncated")
    DETECTIONS_FIELD_NUMBER: _ClassVar[int]
    SCORE_THRESHOLD_FIELD_NUMBER: _ClassVar[int]
    NMS_IOU_THRESHOLD_FIELD_NUMBER: _ClassVar[int]
    TRUNCATED_FIELD_NUMBER: _ClassVar[int]
    detections: _containers.RepeatedCompositeFieldContainer[Detection]
    score_threshold: float
    nms_iou_threshold: float
    truncated: bool
    def __init__(self, detections: _Optional[_Iterable[_Union[Detection, _Mapping]]] = ..., score_threshold: _Optional[float] = ..., nms_iou_threshold: _Optional[float] = ..., truncated: bool = ...) -> None: ...

class ShotBoundary(_message.Message):
    __slots__ = ("time", "score")
    TIME_FIELD_NUMBER: _ClassVar[int]
    SCORE_FIELD_NUMBER: _ClassVar[int]
    time: RationalTime
    score: float
    def __init__(self, time: _Optional[_Union[RationalTime, _Mapping]] = ..., score: _Optional[float] = ...) -> None: ...

class ShotBoundarySet(_message.Message):
    __slots__ = ("boundaries", "score_threshold")
    BOUNDARIES_FIELD_NUMBER: _ClassVar[int]
    SCORE_THRESHOLD_FIELD_NUMBER: _ClassVar[int]
    boundaries: _containers.RepeatedCompositeFieldContainer[ShotBoundary]
    score_threshold: float
    def __init__(self, boundaries: _Optional[_Iterable[_Union[ShotBoundary, _Mapping]]] = ..., score_threshold: _Optional[float] = ...) -> None: ...

class InferError(_message.Message):
    __slots__ = ("code", "message", "retryable", "retry_after_ms")
    CODE_FIELD_NUMBER: _ClassVar[int]
    MESSAGE_FIELD_NUMBER: _ClassVar[int]
    RETRYABLE_FIELD_NUMBER: _ClassVar[int]
    RETRY_AFTER_MS_FIELD_NUMBER: _ClassVar[int]
    code: ErrorCode
    message: str
    retryable: bool
    retry_after_ms: int
    def __init__(self, code: _Optional[_Union[ErrorCode, str]] = ..., message: _Optional[str] = ..., retryable: bool = ..., retry_after_ms: _Optional[int] = ...) -> None: ...

class LoadModelRequest(_message.Message):
    __slots__ = ("model_id", "preferred_runtimes", "expected_pin")
    MODEL_ID_FIELD_NUMBER: _ClassVar[int]
    PREFERRED_RUNTIMES_FIELD_NUMBER: _ClassVar[int]
    EXPECTED_PIN_FIELD_NUMBER: _ClassVar[int]
    model_id: str
    preferred_runtimes: _containers.RepeatedScalarFieldContainer[RuntimeTarget]
    expected_pin: ModelPin
    def __init__(self, model_id: _Optional[str] = ..., preferred_runtimes: _Optional[_Iterable[_Union[RuntimeTarget, str]]] = ..., expected_pin: _Optional[_Union[ModelPin, _Mapping]] = ...) -> None: ...

class LoadModelResponse(_message.Message):
    __slots__ = ("loaded", "pin", "runtime_used", "error", "load_duration_ms", "relaxed_gate_warning")
    LOADED_FIELD_NUMBER: _ClassVar[int]
    PIN_FIELD_NUMBER: _ClassVar[int]
    RUNTIME_USED_FIELD_NUMBER: _ClassVar[int]
    ERROR_FIELD_NUMBER: _ClassVar[int]
    LOAD_DURATION_MS_FIELD_NUMBER: _ClassVar[int]
    RELAXED_GATE_WARNING_FIELD_NUMBER: _ClassVar[int]
    loaded: bool
    pin: ModelPin
    runtime_used: RuntimeTarget
    error: InferError
    load_duration_ms: int
    relaxed_gate_warning: str
    def __init__(self, loaded: bool = ..., pin: _Optional[_Union[ModelPin, _Mapping]] = ..., runtime_used: _Optional[_Union[RuntimeTarget, str]] = ..., error: _Optional[_Union[InferError, _Mapping]] = ..., load_duration_ms: _Optional[int] = ..., relaxed_gate_warning: _Optional[str] = ...) -> None: ...

class UnloadModelRequest(_message.Message):
    __slots__ = ("model_id",)
    MODEL_ID_FIELD_NUMBER: _ClassVar[int]
    model_id: str
    def __init__(self, model_id: _Optional[str] = ...) -> None: ...

class UnloadModelResponse(_message.Message):
    __slots__ = ("unloaded", "freed_bytes")
    UNLOADED_FIELD_NUMBER: _ClassVar[int]
    FREED_BYTES_FIELD_NUMBER: _ClassVar[int]
    unloaded: bool
    freed_bytes: int
    def __init__(self, unloaded: bool = ..., freed_bytes: _Optional[int] = ...) -> None: ...
