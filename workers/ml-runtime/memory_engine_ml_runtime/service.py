"""Generated-contract gRPC service and loopback-only server bootstrap."""

from __future__ import annotations

import hashlib
import threading
import time
from collections import OrderedDict
from concurrent import futures
from dataclasses import dataclass
from typing import Any

import grpc

from contracts.proto.generated.python import ml_runtime_pb2 as pb2
from contracts.proto.generated.python import ml_runtime_pb2_grpc as pb2_grpc

from .catalog import (
    OUTPUT_KIND_ENUM_NAMES,
    PRECISION_ENUM_NAMES,
    RUNTIME_ENUM_NAMES,
    ModelCatalog,
    ModelInspection,
)
from .execution import (
    ExecutionFailure,
    InferenceExecutor,
    SessionManager,
    _infer_error,
)
from .preprocess import InputFailure, ProxyLoader, ProxyResolver, prepare_item

LOAD_MODE_ENUM_NAMES = {
    "release": "LOAD_MODE_RELEASE",
    "development": "LOAD_MODE_DEVELOPMENT",
}

LICENSE_REFUSALS = {
    "UNLOADABLE_REASON_LICENSE_UNVERIFIED",
    "UNLOADABLE_REASON_LICENSE_BLOCKS_RELEASE",
}


class MlRuntimeService(pb2_grpc.MlRuntimeServicer):
    def __init__(
        self,
        catalog: ModelCatalog,
        *,
        proxy_resolver: ProxyResolver | None = None,
        sessions: SessionManager | Any | None = None,
        executor: InferenceExecutor | Any | None = None,
        response_cache_size: int = 128,
    ) -> None:
        self.catalog = catalog
        self.sessions = sessions or SessionManager()
        self.executor = executor or InferenceExecutor()
        self.proxy_loader = ProxyLoader(proxy_resolver)
        self._started_at = time.monotonic()
        self._state_lock = threading.RLock()
        self._inflight = 0
        # A zero-sized cache would violate InferRequest's idempotency contract.
        self._response_cache_size = max(1, response_cache_size)
        self._responses: OrderedDict[str, tuple[bytes, bytes]] = OrderedDict()
        self._pending: dict[str, tuple[bytes, threading.Event]] = {}

    def ListModels(self, request: pb2.ListModelsRequest, context: grpc.ServicerContext):
        del context
        models = []
        loaded_ids = {model.inspection.model_id for model in self.sessions.loaded()}
        for inspection in self.catalog.inspect_all(request.task):
            if inspection.unloadable_reason and not request.include_unloadable:
                continue
            models.append(
                self._model_info(inspection, inspection.model_id in loaded_ids)
            )
        return pb2.ListModelsResponse(
            models=models,
            load_mode=self._load_mode(),
        )

    def Health(self, request: pb2.HealthRequest, context: grpc.ServicerContext):
        del request, context
        warnings = []
        if self.catalog.mode == "development":
            warnings.append("development load gate enabled")
        loaded = self.sessions.loaded()
        with self._state_lock:
            queue_depth = self._inflight
        return pb2.HealthResponse(
            serving=True,
            load_mode=self._load_mode(),
            loaded=[model.pin for model in loaded],
            queue_depth=queue_depth,
            uptime_seconds=int(time.monotonic() - self._started_at),
            warnings=warnings,
        )

    def Infer(self, request: pb2.InferRequest, context: grpc.ServicerContext):
        started = time.monotonic()
        fingerprint = hashlib.blake2b(
            request.SerializeToString(deterministic=True), digest_size=32
        ).digest()
        pending_event: threading.Event | None = None
        owns_request = True
        if request.request_id:
            with self._state_lock:
                cached = self._responses.get(request.request_id)
                if cached is not None:
                    cached_fingerprint, serialized = cached
                    if cached_fingerprint != fingerprint:
                        return self._top_error(
                            request,
                            pb2.ERROR_CODE_INPUT_INVALID,
                            "request_id was reused with different work",
                            started,
                        )
                    self._responses.move_to_end(request.request_id)
                    response = pb2.InferResponse()
                    response.ParseFromString(serialized)
                    return response
                pending = self._pending.get(request.request_id)
                if pending is not None:
                    pending_fingerprint, pending_event = pending
                    if pending_fingerprint != fingerprint:
                        return self._top_error(
                            request,
                            pb2.ERROR_CODE_INPUT_INVALID,
                            "request_id was reused with different work",
                            started,
                        )
                    owns_request = False
                else:
                    pending_event = threading.Event()
                    self._pending[request.request_id] = fingerprint, pending_event

        if not owns_request and pending_event is not None:
            timeout = context.time_remaining()
            if not pending_event.wait(timeout=timeout):
                return self._top_error(
                    request,
                    pb2.ERROR_CODE_DEADLINE_EXCEEDED,
                    "duplicate inference request timed out behind the active request",
                    started,
                    retryable=True,
                )
            with self._state_lock:
                cached = self._responses.get(request.request_id)
            if cached is None:
                return self._top_error(
                    request,
                    pb2.ERROR_CODE_INTERNAL,
                    "active inference request ended without a cached result",
                    started,
                    retryable=True,
                )
            response = pb2.InferResponse()
            response.ParseFromString(cached[1])
            return response

        with self._state_lock:
            self._inflight += 1
        try:
            try:
                response = self._infer_uncached(request, context, started)
            # Registry, provider, and media-db implementations can surface
            # implementation-specific exceptions. Redact them at the boundary.
            except Exception:  # noqa: BLE001
                response = self._top_error(
                    request,
                    pb2.ERROR_CODE_INTERNAL,
                    "inference request failed internally",
                    started,
                    retryable=True,
                )
        finally:
            with self._state_lock:
                self._inflight -= 1
        if request.request_id:
            serialized = response.SerializeToString(deterministic=True)
            with self._state_lock:
                self._responses[request.request_id] = fingerprint, serialized
                self._responses.move_to_end(request.request_id)
                while len(self._responses) > self._response_cache_size:
                    self._responses.popitem(last=False)
                pending = self._pending.pop(request.request_id, None)
                if pending is not None:
                    pending[1].set()
        return response

    def _infer_uncached(
        self,
        request: pb2.InferRequest,
        context: grpc.ServicerContext,
        started: float,
    ) -> pb2.InferResponse:
        if not request.request_id:
            return self._top_error(
                request,
                pb2.ERROR_CODE_INPUT_INVALID,
                "request_id is required",
                started,
            )
        if not request.items:
            return self._top_error(
                request,
                pb2.ERROR_CODE_INPUT_INVALID,
                "inference request has no items",
                started,
            )
        item_ids = [item.item_id for item in request.items]
        if any(not item_id for item_id in item_ids) or len(set(item_ids)) != len(
            item_ids
        ):
            return self._top_error(
                request,
                pb2.ERROR_CODE_INPUT_INVALID,
                "item_id values must be non-empty and unique",
                started,
            )
        inspection = self.catalog.inspect(request.model_id)
        if inspection is None:
            return self._top_error(
                request,
                pb2.ERROR_CODE_MODEL_NOT_REGISTERED,
                "model is not registered",
                started,
            )
        if inspection.unloadable_reason:
            error = self._load_error(inspection.unloadable_reason)
            return self._top_error(
                request, error.code, error.message, started, retryable=error.retryable
            )
        try:
            loaded = self.sessions.load(inspection, request.preferred_runtimes)
        except ExecutionFailure as failure:
            return self._top_failure(request, failure, started)
        mismatch = self._pin_mismatch(request.expected_pin, loaded.pin)
        if mismatch:
            return self._top_error(
                request,
                pb2.ERROR_CODE_PIN_MISMATCH,
                "loaded model does not match the expected pin",
                started,
            )
        deadline = (
            started + request.deadline_ms / 1000.0 if request.deadline_ms > 0 else None
        )
        if deadline is not None and time.monotonic() >= deadline:
            return self._top_error(
                request,
                pb2.ERROR_CODE_DEADLINE_EXCEEDED,
                "inference deadline elapsed before work began",
                started,
                retryable=True,
            )

        prepared = []
        by_id: dict[str, pb2.InferResult] = {}
        for item in request.items:
            if not context.is_active():
                by_id[item.item_id] = pb2.InferResult(
                    item_id=item.item_id,
                    error=pb2.InferError(
                        code=pb2.ERROR_CODE_CANCELLED,
                        message="inference was cancelled",
                        retryable=True,
                    ),
                )
                continue
            try:
                prepared.append(
                    prepare_item(item, inspection.config, self.proxy_loader)
                )
            except InputFailure as failure:
                by_id[item.item_id] = pb2.InferResult(
                    item_id=item.item_id, error=_infer_error(failure)
                )

        batching = inspection.config.get("batching")
        max_batch = (
            int(batching.get("max_batch", 1)) if isinstance(batching, dict) else 1
        )
        for result in self.executor.run(
            loaded,
            prepared,
            max_batch=max_batch,
            deadline=deadline,
            is_cancelled=lambda: not context.is_active(),
        ):
            by_id[result.item_id] = result
        if set(by_id) != set(item_ids):
            return self._top_error(
                request,
                pb2.ERROR_CODE_INTERNAL,
                "inference did not produce exactly one result per item",
                started,
                retryable=True,
            )
        return pb2.InferResponse(
            request_id=request.request_id,
            pin=loaded.pin,
            runtime_used=loaded.runtime_enum,
            results=[by_id[item_id] for item_id in item_ids],
            duration_ms=max(0, int((time.monotonic() - started) * 1000)),
            batch_size=len(request.items),
        )

    def InferStream(self, request_iterator, context: grpc.ServicerContext):
        for request in request_iterator:
            if not context.is_active():
                return
            yield self.Infer(request, context)

    def LoadModel(self, request: pb2.LoadModelRequest, context: grpc.ServicerContext):
        del context
        started = time.monotonic()
        inspection = self.catalog.inspect(request.model_id)
        if inspection is None:
            return pb2.LoadModelResponse(
                loaded=False,
                error=pb2.InferError(
                    code=pb2.ERROR_CODE_MODEL_NOT_REGISTERED,
                    message="model is not registered",
                    retryable=False,
                ),
            )
        if inspection.unloadable_reason:
            return pb2.LoadModelResponse(
                loaded=False,
                pin=self._model_pin(inspection),
                error=self._load_error(inspection.unloadable_reason),
            )
        try:
            loaded = self.sessions.load(inspection, request.preferred_runtimes)
        except ExecutionFailure as failure:
            return pb2.LoadModelResponse(
                loaded=False,
                pin=self._model_pin(inspection),
                error=_infer_error(failure),
                load_duration_ms=max(0, int((time.monotonic() - started) * 1000)),
            )
        if self._pin_mismatch(request.expected_pin, loaded.pin):
            return pb2.LoadModelResponse(
                loaded=False,
                pin=loaded.pin,
                runtime_used=loaded.runtime_enum,
                error=pb2.InferError(
                    code=pb2.ERROR_CODE_PIN_MISMATCH,
                    message="loaded model does not match the expected pin",
                    retryable=False,
                ),
                load_duration_ms=max(0, int((time.monotonic() - started) * 1000)),
            )
        warning = (
            "development gate relaxed release refusal: "
            f"{inspection.release_unloadable_reason}"
            if self.catalog.mode == "development"
            and inspection.release_unloadable_reason is not None
            else ""
        )
        return pb2.LoadModelResponse(
            loaded=True,
            pin=loaded.pin,
            runtime_used=loaded.runtime_enum,
            load_duration_ms=max(0, int((time.monotonic() - started) * 1000)),
            relaxed_gate_warning=warning,
        )

    def UnloadModel(
        self, request: pb2.UnloadModelRequest, context: grpc.ServicerContext
    ):
        del context
        unloaded, freed_bytes = self.sessions.unload(request.model_id)
        return pb2.UnloadModelResponse(unloaded=unloaded, freed_bytes=freed_bytes)

    def _model_info(
        self, inspection: ModelInspection, currently_loaded: bool = False
    ) -> pb2.ModelInfo:
        config = inspection.config
        batching = (
            config.get("batching") if isinstance(config.get("batching"), dict) else {}
        )
        preprocessing = (
            config.get("preprocessing")
            if isinstance(config.get("preprocessing"), dict)
            else {}
        )
        input_name = preprocessing.get("input_name")
        return pb2.ModelInfo(
            pin=self._model_pin(inspection),
            task=inspection.task,
            loadable=inspection.unloadable_reason is None,
            unloadable_reason=(
                getattr(pb2, inspection.unloadable_reason)
                if inspection.unloadable_reason
                else pb2.UNLOADABLE_REASON_UNSPECIFIED
            ),
            currently_loaded=currently_loaded,
            available_runtimes=[
                getattr(pb2, RUNTIME_ENUM_NAMES[runtime])
                for runtime in inspection.available_runtimes
            ],
            max_batch=int(batching.get("max_batch", 0)),
            blocks_commercial_release=(
                isinstance(config.get("license"), dict)
                and config["license"].get("blocks_commercial_release") is True
            ),
            output_kind=getattr(
                pb2,
                OUTPUT_KIND_ENUM_NAMES.get(inspection.task, "OUTPUT_KIND_TENSORS"),
            ),
            input_names=[input_name]
            if isinstance(input_name, str) and input_name
            else [],
        )

    @staticmethod
    def _model_pin(inspection: ModelInspection) -> pb2.ModelPin:
        config = inspection.config
        registry_config_pin = inspection.entry.get("config_blake3")
        config_matches_registry = (
            isinstance(registry_config_pin, str)
            and registry_config_pin == inspection.config_blake3
        )
        weights = (
            config.get("weights")
            if config_matches_registry and isinstance(config.get("weights"), dict)
            else {}
        )
        precision = str(weights.get("quantization", ""))
        return pb2.ModelPin(
            model_id=inspection.model_id,
            version=str(config.get("version", "")) if config_matches_registry else "",
            weights_blake3=str(weights.get("blake3") or ""),
            config_blake3=str(registry_config_pin or ""),
            runtime=pb2.RUNTIME_TARGET_UNSPECIFIED,
            precision=getattr(
                pb2,
                PRECISION_ENUM_NAMES.get(precision, "PRECISION_UNSPECIFIED"),
            ),
        )

    @staticmethod
    def _pin_mismatch(expected: pb2.ModelPin, actual: pb2.ModelPin) -> bool:
        return any(
            getattr(expected, field) not in ("", 0)
            and getattr(expected, field) != getattr(actual, field)
            for field in (
                "model_id",
                "version",
                "weights_blake3",
                "config_blake3",
                "runtime",
                "precision",
            )
        )

    @staticmethod
    def _load_error(reason: str) -> pb2.InferError:
        if reason == "UNLOADABLE_REASON_CONFIG_MISMATCH":
            code = pb2.ERROR_CODE_CONFIG_MISMATCH
        elif reason in LICENSE_REFUSALS:
            code = pb2.ERROR_CODE_LICENSE_BLOCKED
        else:
            code = pb2.ERROR_CODE_MODEL_UNLOADABLE
        return pb2.InferError(
            code=code,
            message=f"model rejected by load policy: {reason}",
            retryable=False,
        )

    def _load_mode(self) -> int:
        return getattr(pb2, LOAD_MODE_ENUM_NAMES[self.catalog.mode])

    @staticmethod
    def _top_error(
        request: pb2.InferRequest,
        code: int,
        message: str,
        started: float,
        *,
        retryable: bool = False,
    ) -> pb2.InferResponse:
        return pb2.InferResponse(
            request_id=request.request_id,
            duration_ms=max(0, int((time.monotonic() - started) * 1000)),
            batch_size=len(request.items),
            error=pb2.InferError(code=code, message=message, retryable=retryable),
        )

    @classmethod
    def _top_failure(
        cls,
        request: pb2.InferRequest,
        failure: ExecutionFailure,
        started: float,
    ) -> pb2.InferResponse:
        response = cls._top_error(
            request,
            failure.code,
            failure.message,
            started,
            retryable=failure.retryable,
        )
        response.error.retry_after_ms = failure.retry_after_ms
        return response


@dataclass
class RunningServer:
    server: grpc.Server
    host: str
    port: int

    @property
    def address(self) -> str:
        return f"{self.host}:{self.port}"

    def stop(self, grace: float = 0) -> None:
        self.server.stop(grace).wait()


def start_server(
    service: MlRuntimeService,
    *,
    port: int = 0,
    max_workers: int = 4,
) -> RunningServer:
    if not 0 <= port <= 65535:
        raise ValueError("port must be between 0 and 65535")
    if max_workers < 1:
        raise ValueError("max_workers must be positive")

    server = grpc.server(futures.ThreadPoolExecutor(max_workers=max_workers))
    pb2_grpc.add_MlRuntimeServicer_to_server(service, server)
    host = "127.0.0.1"
    bound_port = server.add_insecure_port(f"{host}:{port}")
    if bound_port == 0:
        raise RuntimeError("could not bind ml-runtime to loopback")
    server.start()
    return RunningServer(server=server, host=host, port=bound_port)
