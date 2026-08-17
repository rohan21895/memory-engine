"""The client for `workers/ml-runtime`, and the gate in front of it.

THIS MODULE EXISTS TO MAKE ONE FAILURE LOUD.

The single most likely way this product ships a plausible-looking lie is a
library that imported cleanly, deduplicated cleanly, produced an album, and
contains no analysis at all -- because the model host was not running and
somebody wrote `except Exception: pass` around the inference call. Every photo
still has a thumbnail, a date and a hash, so the grid looks right. Nothing in
the UI says the taste layer never ran.

So the rules here are:

1. UNAVAILABILITY IS A RESULT, NOT AN EXCEPTION TO SWALLOW. `probe()` returns a
   status with a machine-readable reason. The caller must act on it; there is
   no path where an absent host produces an empty-but-successful analysis.

2. "THE HOST IS UP" IS NOT "THE MODELS ARE THERE". A host that answers Health
   but cannot load SigLIP is exactly as useless to the analysis stage as no
   host, and far more convincing. `probe()` therefore also asks ListModels for
   the models the caller named and reports each one that is missing or
   unloadable, with the host's own reason.

3. A PER-ITEM ERROR IS NOT A ZERO. `InferResult` carries either an outcome or
   an error, never both, and this client preserves that distinction all the way
   out: a failed item is returned as a failure, never as an empty detection set.
   "Ran and found no faces" and "never ran" must not be the same value, which
   is why the proto separates them and why collapsing them here would undo the
   whole design.

The transport is loopback-only gRPC and carries no media: an InferItem holds a
PROXY id or already-decoded tensors, and the proto has nowhere to put a
filesystem path. Analysis never touches originals, structurally.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

__all__ = [
    "DEFAULT_ENDPOINT",
    "Detection",
    "MlRuntimeClient",
    "MlRuntimeError",
    "MlRuntimeUnavailable",
    "RuntimeStatus",
    "probe",
]

DEFAULT_ENDPOINT = "127.0.0.1:50151"
ENDPOINT_ENV = "MEMORY_ENGINE_ML_RUNTIME"

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PROTO_ROOT = _REPO_ROOT / "contracts" / "proto"


class MlRuntimeError(RuntimeError):
    """An inference call failed in a way the caller must not treat as data."""


class MlRuntimeUnavailable(MlRuntimeError):
    """The host is absent, not serving, or missing a model the caller needs."""

    def __init__(self, status: RuntimeStatus) -> None:
        super().__init__(status.detail)
        self.status = status


def _load_stubs() -> tuple[Any, Any, Any]:
    """Import grpc and the generated stubs, or explain why not.

    Deferred rather than module-level because a pipeline run that never reaches
    the analysis stage should not require grpcio to be installed, and because
    an ImportError raised at import time of this module would be reported
    against the wrong stage.
    """
    if str(_PROTO_ROOT) not in sys.path:
        sys.path.insert(0, str(_PROTO_ROOT))
    import grpc  # noqa: PLC0415
    from generated.python import ml_runtime_pb2 as pb  # noqa: PLC0415
    from generated.python import ml_runtime_pb2_grpc as pb_grpc  # noqa: PLC0415

    return grpc, pb, pb_grpc


def endpoint_from_env(explicit: str | None = None) -> str:
    return explicit or os.environ.get(ENDPOINT_ENV) or DEFAULT_ENDPOINT


# -------------------------------------------------------------------- types --


@dataclass(frozen=True, slots=True)
class ModelAvailability:
    model_id: str
    present: bool
    loadable: bool
    reason: str


@dataclass(frozen=True, slots=True)
class RuntimeStatus:
    """What the host is, right now, in terms a scheduler can branch on."""

    available: bool
    endpoint: str
    reason: str
    detail: str
    load_mode: str = "unknown"
    loaded: tuple[str, ...] = ()
    models: tuple[ModelAvailability, ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def missing_models(self) -> tuple[str, ...]:
        return tuple(item.model_id for item in self.models if not (item.present and item.loadable))

    def to_dict(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "endpoint": self.endpoint,
            "reason": self.reason,
            "detail": self.detail,
            "load_mode": self.load_mode,
            "loaded": list(self.loaded),
            "missing_models": list(self.missing_models),
            "warnings": list(self.warnings),
        }

    def require(self) -> None:
        if not self.available:
            raise MlRuntimeUnavailable(self)


@dataclass(frozen=True, slots=True)
class Point:
    x: float
    y: float


@dataclass(frozen=True, slots=True)
class Detection:
    """One detection, normalised against the oriented image."""

    x: float
    y: float
    w: float
    h: float
    score: float
    landmarks: tuple[Point, ...] = ()
    landmarks_out_of_range: bool = False
    landmark_scheme: str = "unspecified"

    @property
    def area_ratio(self) -> float:
        return max(0.0, self.w) * max(0.0, self.h)


@dataclass(frozen=True, slots=True)
class ItemFailure:
    item_id: str
    code: str
    message: str
    retryable: bool


@dataclass(frozen=True, slots=True)
class InferOutcome:
    """One batch's worth of results, with failures kept separate from data."""

    pin: Mapping[str, str]
    runtime_used: str
    tensors: Mapping[str, tuple[float, ...]] = field(default_factory=dict)
    detections: Mapping[str, tuple[Detection, ...]] = field(default_factory=dict)
    failures: tuple[ItemFailure, ...] = ()


# -------------------------------------------------------------------- probe --


def probe(
    *,
    endpoint: str | None = None,
    required_models: Sequence[str] = (),
    timeout_s: float = 3.0,
) -> RuntimeStatus:
    """Ask the host whether it can do the work, without doing any of it."""
    target = endpoint_from_env(endpoint)
    try:
        grpc, pb, pb_grpc = _load_stubs()
    except ImportError as error:
        return RuntimeStatus(
            available=False,
            endpoint=target,
            reason="client_unavailable",
            detail=(
                "the gRPC client could not be imported, so the model host cannot be "
                f"reached from this process: {error}"
            ),
        )

    channel = grpc.insecure_channel(target)
    try:
        stub = pb_grpc.MlRuntimeStub(channel)
        try:
            health = stub.Health(pb.HealthRequest(), timeout=timeout_s)
        except grpc.RpcError as error:
            code = getattr(error, "code", lambda: None)()
            return RuntimeStatus(
                available=False,
                endpoint=target,
                reason="unreachable",
                detail=(
                    f"no model host answered at {target} "
                    f"({getattr(code, 'name', 'RPC_ERROR')}). Start workers/ml-runtime, "
                    f"or set {ENDPOINT_ENV} to where it is listening."
                ),
            )

        load_mode = pb.LoadMode.Name(health.load_mode).removeprefix("LOAD_MODE_").lower()
        loaded = tuple(pin.model_id for pin in health.loaded)
        warnings = tuple(health.warnings)

        if not health.serving:
            return RuntimeStatus(
                available=False,
                endpoint=target,
                reason="not_serving",
                detail=f"the model host at {target} answered Health but is not serving",
                load_mode=load_mode,
                loaded=loaded,
                warnings=warnings,
            )

        availability = _model_availability(
            grpc, pb, stub, required_models, timeout_s=timeout_s
        )
        missing = [item for item in availability if not (item.present and item.loadable)]
        if missing:
            listed = ", ".join(f"{item.model_id} ({item.reason})" for item in missing)
            return RuntimeStatus(
                available=False,
                endpoint=target,
                reason="models_unavailable",
                detail=f"the model host is serving but cannot provide: {listed}",
                load_mode=load_mode,
                loaded=loaded,
                models=availability,
                warnings=warnings,
            )

        return RuntimeStatus(
            available=True,
            endpoint=target,
            reason="ok",
            detail=f"model host serving at {target} in {load_mode} mode",
            load_mode=load_mode,
            loaded=loaded,
            models=availability,
            warnings=warnings,
        )
    finally:
        channel.close()


def _model_availability(
    grpc: Any, pb: Any, stub: Any, required: Sequence[str], *, timeout_s: float
) -> tuple[ModelAvailability, ...]:
    if not required:
        return ()
    try:
        listed = stub.ListModels(
            pb.ListModelsRequest(include_unloadable=True), timeout=timeout_s
        )
    except grpc.RpcError as error:
        code = getattr(error, "code", lambda: None)()
        return tuple(
            ModelAvailability(
                model_id=model_id,
                present=False,
                loadable=False,
                reason=f"ListModels failed: {getattr(code, 'name', 'RPC_ERROR')}",
            )
            for model_id in required
        )

    known = {info.pin.model_id: info for info in listed.models}
    result: list[ModelAvailability] = []
    for model_id in required:
        info = known.get(model_id)
        if info is None:
            result.append(
                ModelAvailability(model_id, present=False, loadable=False,
                                  reason="not offered by this host")
            )
            continue
        if not info.loadable:
            reason = (
                pb.UnloadableReason.Name(info.unloadable_reason)
                .removeprefix("UNLOADABLE_REASON_")
                .lower()
            )
            result.append(
                ModelAvailability(model_id, present=True, loadable=False, reason=reason)
            )
            continue
        result.append(ModelAvailability(model_id, present=True, loadable=True, reason="ok"))
    return tuple(result)


# ------------------------------------------------------------------- client --


class MlRuntimeClient:
    """A thin, honest wrapper over the MlRuntime stub.

    Thin because postprocessing belongs to the host by contract, and honest
    because every way a call can fail is surfaced as either an exception (the
    whole request failed) or an `ItemFailure` (this item failed) -- never as
    absent data.
    """

    def __init__(self, endpoint: str | None = None, *, timeout_s: float = 60.0) -> None:
        self._grpc, self._pb, pb_grpc = _load_stubs()
        self.endpoint = endpoint_from_env(endpoint)
        self._timeout_s = timeout_s
        self._channel = self._grpc.insecure_channel(self.endpoint)
        self._stub = pb_grpc.MlRuntimeStub(self._channel)

    def close(self) -> None:
        self._channel.close()

    def __enter__(self) -> MlRuntimeClient:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # -- calls -----------------------------------------------------------

    def infer_proxies(
        self,
        *,
        model_id: str,
        request_id: str,
        items: Mapping[str, str],
        alignment: str = "none",
        priority: int = 100,
    ) -> InferOutcome:
        """One batch. `items` maps the caller's item_id to a proxy id.

        The two are kept separate rather than reusing the proxy id as the item
        id, because proxies are CONTENT-addressed: two different photos that
        produce byte-identical thumbnails share a proxy id, and a batch keyed
        on it would collapse them into one item. The response would then answer
        for one media record and the other would never be analysed -- or, worse
        with a different keying, one photo's faces would be written onto
        another. The proto calls item_id "the caller's correlation id" for
        exactly this reason.
        """
        pb = self._pb
        alignment_value = {
            "none": pb.ALIGNMENT_NONE,
            "needs_alignment": pb.ALIGNMENT_NEEDS_ALIGNMENT,
            "prealigned": pb.ALIGNMENT_PREALIGNED,
        }[alignment]
        request = pb.InferRequest(
            request_id=request_id,
            model_id=model_id,
            items=[
                pb.InferItem(item_id=item_id, proxy_id=proxy_id, alignment=alignment_value)
                for item_id, proxy_id in items.items()
            ],
            deadline_ms=int(self._timeout_s * 1000),
            priority=priority,
        )
        try:
            response = self._stub.Infer(request, timeout=self._timeout_s)
        except self._grpc.RpcError as error:
            code = getattr(error, "code", lambda: None)()
            raise MlRuntimeError(
                f"Infer({model_id}) failed at the transport: "
                f"{getattr(code, 'name', 'RPC_ERROR')}"
            ) from error
        return self._read(response, model_id=model_id, expected=list(items))

    def _read(
        self, response: Any, *, model_id: str, expected: Sequence[str]
    ) -> InferOutcome:
        pb = self._pb
        if response.HasField("error"):
            code = pb.ErrorCode.Name(response.error.code).removeprefix("ERROR_CODE_").lower()
            raise MlRuntimeError(
                f"Infer({model_id}) was refused whole: {code}: {response.error.message}"
            )

        tensors: dict[str, tuple[float, ...]] = {}
        detections: dict[str, tuple[Detection, ...]] = {}
        failures: list[ItemFailure] = []
        seen: set[str] = set()

        for result in response.results:
            if not result.item_id:
                raise MlRuntimeError(f"Infer({model_id}) returned a result with no item_id")
            if result.item_id in seen:
                raise MlRuntimeError(
                    f"Infer({model_id}) returned item_id {result.item_id!r} twice; batch "
                    "correlation is ambiguous and one item's results could be written "
                    "onto another"
                )
            seen.add(result.item_id)
            outcome = result.WhichOneof("outcome")
            if outcome is None:
                # An empty oneof is a host bug. "Ran and found nothing" is an
                # EMPTY DetectionSet; treating an absent outcome as that would
                # invent a fact the host never asserted.
                raise MlRuntimeError(
                    f"Infer({model_id}) returned no outcome for item {result.item_id!r}"
                )
            if outcome == "error":
                code = (
                    pb.ErrorCode.Name(result.error.code).removeprefix("ERROR_CODE_").lower()
                )
                failures.append(
                    ItemFailure(
                        item_id=result.item_id,
                        code=code,
                        message=result.error.message,
                        retryable=result.error.retryable,
                    )
                )
            elif outcome == "tensors":
                tensors[result.item_id] = _decode_vector(pb, result.tensors)
            elif outcome == "detections":
                detections[result.item_id] = _decode_detections(pb, result.detections)
            else:
                raise MlRuntimeError(
                    f"Infer({model_id}) returned an unexpected outcome {outcome!r} for "
                    f"item {result.item_id!r}"
                )

        unanswered = sorted(set(expected) - seen)
        if unanswered:
            raise MlRuntimeError(
                f"Infer({model_id}) answered {len(seen)} of {len(expected)} items; "
                f"{len(unanswered)} were silently dropped"
            )

        return InferOutcome(
            pin={
                "model_id": response.pin.model_id,
                "version": response.pin.version,
                "weights_blake3": response.pin.weights_blake3,
                "config_blake3": response.pin.config_blake3,
            },
            runtime_used=pb.RuntimeTarget.Name(response.runtime_used)
            .removeprefix("RUNTIME_TARGET_")
            .lower(),
            tensors=tensors,
            detections=detections,
            failures=tuple(failures),
        )


def _decode_vector(pb: Any, tensor_set: Any) -> tuple[float, ...]:
    import struct  # noqa: PLC0415

    if len(tensor_set.tensors) != 1:
        raise MlRuntimeError(
            f"expected exactly one output tensor, got {len(tensor_set.tensors)}"
        )
    tensor = tensor_set.tensors[0]
    if tensor.dtype != pb.DTYPE_FLOAT32:
        raise MlRuntimeError(
            "embedding tensors must be float32; "
            f"got {pb.DType.Name(tensor.dtype)}"
        )
    count = len(tensor.data) // 4
    expected = 1
    for dimension in tensor.shape:
        expected *= dimension
    if tensor.shape and expected != count:
        raise MlRuntimeError(
            f"tensor shape {list(tensor.shape)} implies {expected} floats but the "
            f"payload holds {count}"
        )
    return struct.unpack(f"<{count}f", tensor.data)


def _decode_detections(pb: Any, detection_set: Any) -> tuple[Detection, ...]:
    out: list[Detection] = []
    for detection in detection_set.detections:
        landmarks = tuple(Point(x=point.x, y=point.y) for point in detection.landmarks)
        out.append(
            Detection(
                x=detection.box.x,
                y=detection.box.y,
                w=detection.box.w,
                h=detection.box.h,
                score=detection.score,
                landmarks=landmarks,
                landmarks_out_of_range=detection.landmarks_out_of_range,
                landmark_scheme=pb.LandmarkScheme.Name(detection.landmark_scheme)
                .removeprefix("LANDMARK_SCHEME_")
                .lower(),
            )
        )
    return tuple(out)
