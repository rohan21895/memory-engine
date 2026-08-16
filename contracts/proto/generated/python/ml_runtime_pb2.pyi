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

class ExecutionProvider(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    EXECUTION_PROVIDER_UNSPECIFIED: _ClassVar[ExecutionProvider]
    EXECUTION_PROVIDER_COREML: _ClassVar[ExecutionProvider]
    EXECUTION_PROVIDER_CUDA: _ClassVar[ExecutionProvider]
    EXECUTION_PROVIDER_DIRECTML: _ClassVar[ExecutionProvider]
    EXECUTION_PROVIDER_CPU: _ClassVar[ExecutionProvider]
    EXECUTION_PROVIDER_CTRANSLATE2: _ClassVar[ExecutionProvider]

class Precision(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    PRECISION_UNSPECIFIED: _ClassVar[Precision]
    PRECISION_FP32: _ClassVar[Precision]
    PRECISION_FP16: _ClassVar[Precision]
    PRECISION_INT8: _ClassVar[Precision]

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

class DType(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    DTYPE_UNSPECIFIED: _ClassVar[DType]
    DTYPE_FLOAT32: _ClassVar[DType]
    DTYPE_FLOAT16: _ClassVar[DType]
    DTYPE_INT64: _ClassVar[DType]
    DTYPE_INT32: _ClassVar[DType]
    DTYPE_UINT8: _ClassVar[DType]

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
    ERROR_CODE_RESOURCE_EXHAUSTED: _ClassVar[ErrorCode]
    ERROR_CODE_PROVIDER_UNAVAILABLE: _ClassVar[ErrorCode]
    ERROR_CODE_DEADLINE_EXCEEDED: _ClassVar[ErrorCode]
    ERROR_CODE_CANCELLED: _ClassVar[ErrorCode]
    ERROR_CODE_MODEL_LOADING: _ClassVar[ErrorCode]
    ERROR_CODE_INTERNAL: _ClassVar[ErrorCode]
EXECUTION_PROVIDER_UNSPECIFIED: ExecutionProvider
EXECUTION_PROVIDER_COREML: ExecutionProvider
EXECUTION_PROVIDER_CUDA: ExecutionProvider
EXECUTION_PROVIDER_DIRECTML: ExecutionProvider
EXECUTION_PROVIDER_CPU: ExecutionProvider
EXECUTION_PROVIDER_CTRANSLATE2: ExecutionProvider
PRECISION_UNSPECIFIED: Precision
PRECISION_FP32: Precision
PRECISION_FP16: Precision
PRECISION_INT8: Precision
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
DTYPE_UNSPECIFIED: DType
DTYPE_FLOAT32: DType
DTYPE_FLOAT16: DType
DTYPE_INT64: DType
DTYPE_INT32: DType
DTYPE_UINT8: DType
ERROR_CODE_UNSPECIFIED: ErrorCode
ERROR_CODE_MODEL_NOT_REGISTERED: ErrorCode
ERROR_CODE_MODEL_UNLOADABLE: ErrorCode
ERROR_CODE_PIN_MISMATCH: ErrorCode
ERROR_CODE_PROXY_NOT_FOUND: ErrorCode
ERROR_CODE_INPUT_INVALID: ErrorCode
ERROR_CODE_LANDMARKS_REQUIRED: ErrorCode
ERROR_CODE_UNSUPPORTED_INPUT: ErrorCode
ERROR_CODE_LICENSE_BLOCKED: ErrorCode
ERROR_CODE_RESOURCE_EXHAUSTED: ErrorCode
ERROR_CODE_PROVIDER_UNAVAILABLE: ErrorCode
ERROR_CODE_DEADLINE_EXCEEDED: ErrorCode
ERROR_CODE_CANCELLED: ErrorCode
ERROR_CODE_MODEL_LOADING: ErrorCode
ERROR_CODE_INTERNAL: ErrorCode

class ModelPin(_message.Message):
    __slots__ = ("model_id", "version", "weights_blake3", "provider", "precision")
    MODEL_ID_FIELD_NUMBER: _ClassVar[int]
    VERSION_FIELD_NUMBER: _ClassVar[int]
    WEIGHTS_BLAKE3_FIELD_NUMBER: _ClassVar[int]
    PROVIDER_FIELD_NUMBER: _ClassVar[int]
    PRECISION_FIELD_NUMBER: _ClassVar[int]
    model_id: str
    version: str
    weights_blake3: str
    provider: ExecutionProvider
    precision: Precision
    def __init__(self, model_id: _Optional[str] = ..., version: _Optional[str] = ..., weights_blake3: _Optional[str] = ..., provider: _Optional[_Union[ExecutionProvider, str]] = ..., precision: _Optional[_Union[Precision, str]] = ...) -> None: ...

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
    __slots__ = ("pin", "task", "loadable", "unloadable_reason", "currently_loaded", "available_providers", "max_batch", "blocks_commercial_release")
    PIN_FIELD_NUMBER: _ClassVar[int]
    TASK_FIELD_NUMBER: _ClassVar[int]
    LOADABLE_FIELD_NUMBER: _ClassVar[int]
    UNLOADABLE_REASON_FIELD_NUMBER: _ClassVar[int]
    CURRENTLY_LOADED_FIELD_NUMBER: _ClassVar[int]
    AVAILABLE_PROVIDERS_FIELD_NUMBER: _ClassVar[int]
    MAX_BATCH_FIELD_NUMBER: _ClassVar[int]
    BLOCKS_COMMERCIAL_RELEASE_FIELD_NUMBER: _ClassVar[int]
    pin: ModelPin
    task: str
    loadable: bool
    unloadable_reason: UnloadableReason
    currently_loaded: bool
    available_providers: _containers.RepeatedScalarFieldContainer[ExecutionProvider]
    max_batch: int
    blocks_commercial_release: bool
    def __init__(self, pin: _Optional[_Union[ModelPin, _Mapping]] = ..., task: _Optional[str] = ..., loadable: bool = ..., unloadable_reason: _Optional[_Union[UnloadableReason, str]] = ..., currently_loaded: bool = ..., available_providers: _Optional[_Iterable[_Union[ExecutionProvider, str]]] = ..., max_batch: _Optional[int] = ..., blocks_commercial_release: bool = ...) -> None: ...

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
    __slots__ = ("request_id", "model_id", "expected_pin", "items", "preferred_providers", "deadline_ms", "priority")
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    MODEL_ID_FIELD_NUMBER: _ClassVar[int]
    EXPECTED_PIN_FIELD_NUMBER: _ClassVar[int]
    ITEMS_FIELD_NUMBER: _ClassVar[int]
    PREFERRED_PROVIDERS_FIELD_NUMBER: _ClassVar[int]
    DEADLINE_MS_FIELD_NUMBER: _ClassVar[int]
    PRIORITY_FIELD_NUMBER: _ClassVar[int]
    request_id: str
    model_id: str
    expected_pin: ModelPin
    items: _containers.RepeatedCompositeFieldContainer[InferItem]
    preferred_providers: _containers.RepeatedScalarFieldContainer[ExecutionProvider]
    deadline_ms: int
    priority: int
    def __init__(self, request_id: _Optional[str] = ..., model_id: _Optional[str] = ..., expected_pin: _Optional[_Union[ModelPin, _Mapping]] = ..., items: _Optional[_Iterable[_Union[InferItem, _Mapping]]] = ..., preferred_providers: _Optional[_Iterable[_Union[ExecutionProvider, str]]] = ..., deadline_ms: _Optional[int] = ..., priority: _Optional[int] = ...) -> None: ...

class InferItem(_message.Message):
    __slots__ = ("item_id", "proxy_id", "tensor", "window", "landmarks")
    ITEM_ID_FIELD_NUMBER: _ClassVar[int]
    PROXY_ID_FIELD_NUMBER: _ClassVar[int]
    TENSOR_FIELD_NUMBER: _ClassVar[int]
    WINDOW_FIELD_NUMBER: _ClassVar[int]
    LANDMARKS_FIELD_NUMBER: _ClassVar[int]
    item_id: str
    proxy_id: str
    tensor: Tensor
    window: TimeWindow
    landmarks: _containers.RepeatedCompositeFieldContainer[Point2D]
    def __init__(self, item_id: _Optional[str] = ..., proxy_id: _Optional[str] = ..., tensor: _Optional[_Union[Tensor, _Mapping]] = ..., window: _Optional[_Union[TimeWindow, _Mapping]] = ..., landmarks: _Optional[_Iterable[_Union[Point2D, _Mapping]]] = ...) -> None: ...

class TimeWindow(_message.Message):
    __slots__ = ("start_value", "start_rate", "duration_value", "duration_rate")
    START_VALUE_FIELD_NUMBER: _ClassVar[int]
    START_RATE_FIELD_NUMBER: _ClassVar[int]
    DURATION_VALUE_FIELD_NUMBER: _ClassVar[int]
    DURATION_RATE_FIELD_NUMBER: _ClassVar[int]
    start_value: float
    start_rate: float
    duration_value: float
    duration_rate: float
    def __init__(self, start_value: _Optional[float] = ..., start_rate: _Optional[float] = ..., duration_value: _Optional[float] = ..., duration_rate: _Optional[float] = ...) -> None: ...

class Point2D(_message.Message):
    __slots__ = ("x", "y")
    X_FIELD_NUMBER: _ClassVar[int]
    Y_FIELD_NUMBER: _ClassVar[int]
    x: float
    y: float
    def __init__(self, x: _Optional[float] = ..., y: _Optional[float] = ...) -> None: ...

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

class InferResponse(_message.Message):
    __slots__ = ("request_id", "pin", "provider_used", "results", "duration_ms", "batch_size")
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    PIN_FIELD_NUMBER: _ClassVar[int]
    PROVIDER_USED_FIELD_NUMBER: _ClassVar[int]
    RESULTS_FIELD_NUMBER: _ClassVar[int]
    DURATION_MS_FIELD_NUMBER: _ClassVar[int]
    BATCH_SIZE_FIELD_NUMBER: _ClassVar[int]
    request_id: str
    pin: ModelPin
    provider_used: ExecutionProvider
    results: _containers.RepeatedCompositeFieldContainer[InferResult]
    duration_ms: int
    batch_size: int
    def __init__(self, request_id: _Optional[str] = ..., pin: _Optional[_Union[ModelPin, _Mapping]] = ..., provider_used: _Optional[_Union[ExecutionProvider, str]] = ..., results: _Optional[_Iterable[_Union[InferResult, _Mapping]]] = ..., duration_ms: _Optional[int] = ..., batch_size: _Optional[int] = ...) -> None: ...

class InferResult(_message.Message):
    __slots__ = ("item_id", "error", "outputs")
    ITEM_ID_FIELD_NUMBER: _ClassVar[int]
    ERROR_FIELD_NUMBER: _ClassVar[int]
    OUTPUTS_FIELD_NUMBER: _ClassVar[int]
    item_id: str
    error: InferError
    outputs: _containers.RepeatedCompositeFieldContainer[Tensor]
    def __init__(self, item_id: _Optional[str] = ..., error: _Optional[_Union[InferError, _Mapping]] = ..., outputs: _Optional[_Iterable[_Union[Tensor, _Mapping]]] = ...) -> None: ...

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
    __slots__ = ("model_id", "preferred_providers", "expected_pin")
    MODEL_ID_FIELD_NUMBER: _ClassVar[int]
    PREFERRED_PROVIDERS_FIELD_NUMBER: _ClassVar[int]
    EXPECTED_PIN_FIELD_NUMBER: _ClassVar[int]
    model_id: str
    preferred_providers: _containers.RepeatedScalarFieldContainer[ExecutionProvider]
    expected_pin: ModelPin
    def __init__(self, model_id: _Optional[str] = ..., preferred_providers: _Optional[_Iterable[_Union[ExecutionProvider, str]]] = ..., expected_pin: _Optional[_Union[ModelPin, _Mapping]] = ...) -> None: ...

class LoadModelResponse(_message.Message):
    __slots__ = ("loaded", "pin", "provider_used", "error", "load_duration_ms", "relaxed_gate_warning")
    LOADED_FIELD_NUMBER: _ClassVar[int]
    PIN_FIELD_NUMBER: _ClassVar[int]
    PROVIDER_USED_FIELD_NUMBER: _ClassVar[int]
    ERROR_FIELD_NUMBER: _ClassVar[int]
    LOAD_DURATION_MS_FIELD_NUMBER: _ClassVar[int]
    RELAXED_GATE_WARNING_FIELD_NUMBER: _ClassVar[int]
    loaded: bool
    pin: ModelPin
    provider_used: ExecutionProvider
    error: InferError
    load_duration_ms: int
    relaxed_gate_warning: str
    def __init__(self, loaded: bool = ..., pin: _Optional[_Union[ModelPin, _Mapping]] = ..., provider_used: _Optional[_Union[ExecutionProvider, str]] = ..., error: _Optional[_Union[InferError, _Mapping]] = ..., load_duration_ms: _Optional[int] = ..., relaxed_gate_warning: _Optional[str] = ...) -> None: ...

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
