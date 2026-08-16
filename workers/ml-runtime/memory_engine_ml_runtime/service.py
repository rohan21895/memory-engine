"""Generated-contract gRPC service and loopback-only server bootstrap."""

from __future__ import annotations

import time
from concurrent import futures
from dataclasses import dataclass
from typing import NoReturn

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


LOAD_MODE_ENUM_NAMES = {
    "release": "LOAD_MODE_RELEASE",
    "development": "LOAD_MODE_DEVELOPMENT",
}

LICENSE_REFUSALS = {
    "UNLOADABLE_REASON_LICENSE_UNVERIFIED",
    "UNLOADABLE_REASON_LICENSE_BLOCKS_RELEASE",
}


class MlRuntimeService(pb2_grpc.MlRuntimeServicer):
    def __init__(self, catalog: ModelCatalog) -> None:
        self.catalog = catalog
        self._started_at = time.monotonic()

    def ListModels(self, request: pb2.ListModelsRequest, context: grpc.ServicerContext):
        del context
        models = []
        for inspection in self.catalog.inspect_all(request.task):
            if inspection.unloadable_reason and not request.include_unloadable:
                continue
            models.append(self._model_info(inspection))
        return pb2.ListModelsResponse(
            models=models,
            load_mode=self._load_mode(),
        )

    def Health(self, request: pb2.HealthRequest, context: grpc.ServicerContext):
        del request, context
        warnings = []
        if self.catalog.mode == "development":
            warnings.append("development load gate enabled")
        return pb2.HealthResponse(
            serving=True,
            load_mode=self._load_mode(),
            loaded=[],
            queue_depth=0,
            uptime_seconds=int(time.monotonic() - self._started_at),
            warnings=warnings,
        )

    def Infer(self, request: pb2.InferRequest, context: grpc.ServicerContext) -> NoReturn:
        del request
        context.abort(grpc.StatusCode.UNIMPLEMENTED, "inference execution is not implemented")

    def InferStream(self, request_iterator, context: grpc.ServicerContext) -> NoReturn:
        del request_iterator
        context.abort(grpc.StatusCode.UNIMPLEMENTED, "streaming inference is not implemented")

    def LoadModel(self, request: pb2.LoadModelRequest, context: grpc.ServicerContext):
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
        context.abort(grpc.StatusCode.UNIMPLEMENTED, "model execution loading is not implemented")

    def UnloadModel(self, request: pb2.UnloadModelRequest, context: grpc.ServicerContext):
        del request, context
        return pb2.UnloadModelResponse(unloaded=False, freed_bytes=0)

    def _model_info(self, inspection: ModelInspection) -> pb2.ModelInfo:
        config = inspection.config
        batching = config.get("batching") if isinstance(config.get("batching"), dict) else {}
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
            currently_loaded=False,
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
            input_names=[input_name] if isinstance(input_name, str) and input_name else [],
        )

    @staticmethod
    def _model_pin(inspection: ModelInspection) -> pb2.ModelPin:
        config = inspection.config
        weights = config.get("weights") if isinstance(config.get("weights"), dict) else {}
        precision = str(weights.get("quantization", ""))
        return pb2.ModelPin(
            model_id=inspection.model_id,
            version=str(config.get("version", "")),
            weights_blake3=inspection.weights_blake3 or "",
            config_blake3=inspection.config_blake3 or "",
            runtime=pb2.RUNTIME_TARGET_UNSPECIFIED,
            precision=getattr(
                pb2,
                PRECISION_ENUM_NAMES.get(precision, "PRECISION_UNSPECIFIED"),
            ),
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
