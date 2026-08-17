"""Registry-driven execution for the diagnostic real-photo smoke command.

This is deliberately not a production analysis dispatcher.  The repository has
no golden ``analyze_image`` JobSpec fixture yet, so the smoke command cannot
claim resumability without inventing a contract shape.  It does, however, run
every declared model step, persist every result the frozen contracts can
represent, and report the remaining seams explicitly.
"""

from __future__ import annotations

import copy
import json
import math
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
from blake3 import blake3
from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from contracts.proto.generated.python import ml_runtime_pb2 as pb2

from .catalog import ModelCatalog, ModelInspection, PipelineDefinition

InferenceCall = Callable[[str, Sequence[pb2.InferItem]], pb2.InferResponse]

_PROTO_DTYPES: dict[int, np.dtype[Any]] = {
    pb2.DTYPE_FLOAT32: np.dtype("<f4"),
    pb2.DTYPE_FLOAT16: np.dtype("<f2"),
    pb2.DTYPE_INT64: np.dtype("<i8"),
    pb2.DTYPE_INT32: np.dtype("<i4"),
    pb2.DTYPE_UINT8: np.dtype("u1"),
}

_VECTOR_QUANTIZATION = {
    pb2.DTYPE_FLOAT32: "float32",
    pb2.DTYPE_FLOAT16: "float16",
}

_RUNTIMES = {
    pb2.RUNTIME_TARGET_ONNXRUNTIME_CPU: "onnxruntime_cpu",
    pb2.RUNTIME_TARGET_ONNXRUNTIME_COREML: "onnxruntime_coreml",
    pb2.RUNTIME_TARGET_ONNXRUNTIME_DIRECTML: "onnxruntime_directml",
    pb2.RUNTIME_TARGET_ONNXRUNTIME_CUDA: "onnxruntime_cuda",
    pb2.RUNTIME_TARGET_CTRANSLATE2: "ctranslate2",
    pb2.RUNTIME_TARGET_MLX: "mlx",
    pb2.RUNTIME_TARGET_LLAMA_CPP: "llama_cpp",
    pb2.RUNTIME_TARGET_OPENCV: "opencv",
    pb2.RUNTIME_TARGET_LIBROSA: "librosa",
    pb2.RUNTIME_TARGET_NATIVE: "native",
}

_PRECISIONS = {
    pb2.PRECISION_FP32: "fp32",
    pb2.PRECISION_FP16: "fp16",
    pb2.PRECISION_BF16: "bf16",
    pb2.PRECISION_INT8: "int8",
    pb2.PRECISION_INT4: "int4",
}

_MEDIA_STAGES = {
    "image_embedding": "image_embedding",
    "face_detection": "face_detection",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _error_code(value: int) -> str:
    return pb2.ErrorCode.Name(value).lower()


def _detection_json(detection: pb2.Detection) -> dict[str, Any]:
    return {
        "score": round(detection.score, 6),
        "box": {
            "x": round(detection.box.x, 6),
            "y": round(detection.box.y, 6),
            "w": round(detection.box.w, 6),
            "h": round(detection.box.h, 6),
        },
        "landmark_scheme": pb2.LandmarkScheme.Name(detection.landmark_scheme),
        "landmarks": [
            {"x": round(point.x, 6), "y": round(point.y, 6)}
            for point in detection.landmarks
        ],
        "landmarks_out_of_range": detection.landmarks_out_of_range,
    }


def _tensor_json(tensor: pb2.Tensor) -> dict[str, Any]:
    return {
        "name": tensor.name,
        "shape": list(tensor.shape),
        "dtype": pb2.DType.Name(tensor.dtype),
        "byte_length": len(tensor.data),
    }


def response_report(response: pb2.InferResponse) -> dict[str, Any]:
    """Summarise a response without dumping embedding vectors into the report."""

    if response.HasField("error"):
        return {
            "status": "error",
            "code": pb2.ErrorCode.Name(response.error.code),
            "message": response.error.message,
            "retryable": response.error.retryable,
        }
    results: list[dict[str, Any]] = []
    for result in response.results:
        outcome = result.WhichOneof("outcome")
        item: dict[str, Any] = {"item_id": result.item_id, "status": "ok"}
        if outcome == "error":
            item.update(
                status="error",
                code=pb2.ErrorCode.Name(result.error.code),
                message=result.error.message,
                retryable=result.error.retryable,
            )
        elif outcome == "detections":
            item["detections"] = [
                _detection_json(value) for value in result.detections.detections
            ]
        elif outcome == "tensors":
            item["tensors"] = [_tensor_json(value) for value in result.tensors.tensors]
        elif outcome == "shots":
            item["shot_count"] = len(result.shots.boundaries)
        else:
            item.update(status="error", message="result has no typed outcome")
        results.append(item)
    return {
        "status": (
            "partial_error"
            if any(item["status"] == "error" for item in results)
            else "ok"
        ),
        "runtime": pb2.RuntimeTarget.Name(response.runtime_used),
        "duration_ms": response.duration_ms,
        "model_pin": {
            "model_id": response.pin.model_id,
            "version": response.pin.version,
            "weights_blake3": response.pin.weights_blake3 or None,
            "config_blake3": response.pin.config_blake3 or None,
            "precision": pb2.Precision.Name(response.pin.precision),
        },
        "results": results,
    }


class MediaRecordValidator:
    """Validate analysis writes against the repository's frozen schema."""

    def __init__(self, repo_root: Path) -> None:
        schema_dir = repo_root / "contracts" / "schemas"
        documents = {
            path.name: json.loads(path.read_text(encoding="utf-8"))
            for path in sorted(schema_dir.glob("*.schema.json"))
        }
        registry = Registry().with_resources(
            [
                (name, Resource.from_contents(document))
                for name, document in documents.items()
            ]
        )
        self._validator = Draft202012Validator(
            documents["media-record.schema.json"], registry=registry
        )

    def validate(self, record: Mapping[str, Any]) -> None:
        errors = sorted(
            self._validator.iter_errors(record), key=lambda error: list(error.path)
        )
        if errors:
            location = ".".join(str(part) for part in errors[0].path) or "record"
            raise ValueError(f"analysis produced an invalid MediaRecord at {location}")


class PhotoAnalysisRunner:
    """Attempt every step in one registry pipeline and persist safe outputs."""

    def __init__(
        self,
        *,
        repo_root: Path,
        database_path: Path,
        database_class: Any,
        catalog: ModelCatalog,
        pipeline: PipelineDefinition,
        records: Sequence[Mapping[str, Any]],
        proxy_items: Sequence[tuple[str, str]],
        infer: InferenceCall,
    ) -> None:
        self.database_path = database_path
        self.database_class = database_class
        self.catalog = catalog
        self.pipeline = pipeline
        self.records = {
            media_id: copy.deepcopy(record)
            for record in records
            if (media_id := str(record.get("media_id") or ""))
        }
        self.proxy_items = list(proxy_items)
        self.proxy_by_media = dict(self.proxy_items)
        self.target_ids = [media_id for media_id, _ in self.proxy_items]
        self.infer = infer
        self.validator = MediaRecordValidator(repo_root)
        self.steps: list[dict[str, Any]] = []
        self.breakages: list[dict[str, Any]] = []
        self.detections: dict[str, list[pb2.Detection]] = {}
        self.vectors: dict[tuple[str, str], tuple[list[float], str]] = {}

    def run(self) -> dict[str, Any]:
        self._break(
            "analysis_job",
            "golden_fixture_missing",
            "no golden analyze_image JobSpec fixture exists, so this diagnostic run is not resumable",
        )
        if not any(
            inspection.task == "safety_classifier"
            for step in self.pipeline.steps
            if (inspection := self.catalog.inspect(step)) is not None
        ):
            self._break(
                "pipeline_definition",
                "safety_gate_unselected",
                "the pipeline has no sensitive-content gate and is unsafe for automated output",
            )

        for step_id in self.pipeline.steps:
            if step_id == "classical_quality":
                self._run_classical_quality_gap()
                continue
            inspection = self.catalog.inspect(step_id)
            if inspection is None:
                self.steps.append(
                    {"step_id": step_id, "status": "blocked", "reason": "not_registered"}
                )
                self._break(
                    step_id,
                    "model_not_registered",
                    "pipeline step does not resolve to a registered executable model",
                )
                continue
            if inspection.unloadable_reason:
                self._run_unloadable(inspection)
                continue
            if inspection.task == "image_embedding":
                self._run_image_embedding(inspection)
            elif inspection.task == "face_detection":
                self._run_face_detection(inspection)
            elif inspection.task == "face_embedding":
                self._run_face_embedding(inspection)
            else:
                self.steps.append(
                    {
                        "step_id": step_id,
                        "task": inspection.task,
                        "status": "blocked",
                        "reason": "orchestrator_missing",
                    }
                )
                self._break(
                    step_id,
                    "orchestrator_missing",
                    "the smoke command has no contract-safe adapter for this model task",
                )

        persistence = self._persist()
        return {
            "pipeline_id": self.pipeline.pipeline_id,
            "load_mode": self.catalog.mode,
            "status": "partial_success" if self.breakages else "complete",
            "resumable_job": False,
            "records_targeted": len(self.target_ids),
            "steps": self.steps,
            "persistence": persistence,
            "breakages": self.breakages,
        }

    def _break(self, stage: str, code: str, message: str, **extra: Any) -> None:
        item = {"stage": stage, "code": code, "message": message}
        item.update(extra)
        self.breakages.append(item)

    def _run_classical_quality_gap(self) -> None:
        message = (
            "classical_quality is named by the pipeline but has no executable config, "
            "normalization spec, or worker entry point"
        )
        self.steps.append(
            {
                "step_id": "classical_quality",
                "task": "classical_quality",
                "status": "blocked",
                "reason": "executor_missing",
            }
        )
        self._break("classical_quality", "executor_missing", message)
        for media_id in self.target_ids:
            self._set_stage_failed(media_id, "classical_quality", "executor_missing", message)

    def _run_unloadable(self, inspection: ModelInspection) -> None:
        reason = str(inspection.unloadable_reason)
        self.steps.append(
            {
                "step_id": inspection.model_id,
                "task": inspection.task,
                "status": "blocked",
                "reason": reason,
                "available_runtimes": list(inspection.available_runtimes),
            }
        )
        self._break(
            inspection.model_id,
            reason.lower(),
            "registered model cannot be loaded; inspect the catalog entry in this report",
        )
        media_stage = _MEDIA_STAGES.get(inspection.task)
        if media_stage:
            for media_id in self.target_ids:
                self._set_stage_failed(
                    media_id,
                    media_stage,
                    reason.lower(),
                    "registered analysis model is unavailable",
                )

    def _proxy_infer_items(self) -> list[pb2.InferItem]:
        return [
            pb2.InferItem(
                item_id=media_id,
                proxy_id=proxy_id,
                alignment=pb2.ALIGNMENT_NONE,
            )
            for media_id, proxy_id in self.proxy_items
        ]

    def _run_image_embedding(self, inspection: ModelInspection) -> None:
        response = self.infer(inspection.model_id, self._proxy_infer_items())
        step = {
            "step_id": inspection.model_id,
            "task": inspection.task,
            **response_report(response),
        }
        self.steps.append(step)
        if response.HasField("error"):
            self._response_failure(inspection, response.error)
            return

        output = inspection.config["outputs"][0]
        output_name = str(output["name"])
        dimensions = int(output["dimensions"])
        vector_space = str(output["vector_space"])
        for result in response.results:
            media_id = result.item_id
            if result.WhichOneof("outcome") == "error":
                self._item_failure(inspection, media_id, result.error)
                continue
            try:
                tensor = next(
                    tensor for tensor in result.tensors.tensors if tensor.name == output_name
                )
                values = self._tensor_values(tensor, dimensions)
                self._store_media_embedding(
                    media_id,
                    vector_space,
                    values,
                    tensor,
                    response,
                    normalized="l2_normalize"
                    in inspection.config["postprocessing"]["steps"],
                )
            except (StopIteration, ValueError) as error:
                message = str(error) or "embedding output is absent"
                self._set_stage_failed(
                    media_id,
                    "image_embedding",
                    "output_invalid",
                    "embedding output is invalid",
                )
                self._break(
                    inspection.model_id,
                    "output_invalid",
                    message,
                    media_id=media_id,
                )

    def _run_face_detection(self, inspection: ModelInspection) -> None:
        response = self.infer(inspection.model_id, self._proxy_infer_items())
        self.steps.append(
            {
                "step_id": inspection.model_id,
                "task": inspection.task,
                **response_report(response),
            }
        )
        if response.HasField("error"):
            self._response_failure(inspection, response.error)
            return
        ran_at = _now()
        for result in response.results:
            media_id = result.item_id
            if result.WhichOneof("outcome") == "error":
                self._item_failure(inspection, media_id, result.error)
                continue
            if result.WhichOneof("outcome") != "detections":
                self._set_stage_failed(
                    media_id,
                    "face_detection",
                    "output_invalid",
                    "face detector returned the wrong output kind",
                )
                continue
            self.detections[media_id] = [
                pb2.Detection.FromString(value.SerializeToString(deterministic=True))
                for value in result.detections.detections
            ]
            self._set_stage_done(media_id, "face_detection", ran_at)
            self._add_model_run(media_id, response, ran_at)
        self._break(
            "face_record_persistence",
            "canonical_face_id_missing",
            "any detections and face embeddings cannot be persisted until issue #34 freezes face_id encoding",
            issue=34,
        )

    def _run_face_embedding(self, inspection: ModelInspection) -> None:
        if not self.detections:
            self.steps.append(
                {
                    "step_id": inspection.model_id,
                    "task": inspection.task,
                    "status": "blocked",
                    "reason": "face_detection_unavailable",
                }
            )
            self._break(
                inspection.model_id,
                "dependency_unavailable",
                "face embeddings require successful detector landmarks",
            )
            return

        items: list[pb2.InferItem] = []
        skipped = 0
        for media_id in self.target_ids:
            proxy_id = self.proxy_by_media[media_id]
            for index, detection in enumerate(self.detections.get(media_id, [])):
                if detection.landmarks_out_of_range or not detection.landmarks:
                    skipped += 1
                    continue
                items.append(
                    pb2.InferItem(
                        item_id=f"{media_id}-{index}",
                        proxy_id=proxy_id,
                        alignment=pb2.ALIGNMENT_NEEDS_ALIGNMENT,
                        landmarks=detection.landmarks,
                        landmark_scheme=detection.landmark_scheme,
                    )
                )
        if not items:
            status = "partial_success" if skipped else "not_applicable"
            self.steps.append(
                {
                    "step_id": inspection.model_id,
                    "task": inspection.task,
                    "status": status,
                    "faces_submitted": 0,
                    "faces_skipped_unalignable": skipped,
                }
            )
            if skipped:
                self._break(
                    inspection.model_id,
                    "unalignable_faces",
                    "detector landmarks were absent or outside the contract range",
                    count=skipped,
                )
            return

        response = self.infer(inspection.model_id, items)
        result = response_report(response)
        result.update(
            step_id=inspection.model_id,
            task=inspection.task,
            faces_submitted=len(items),
            faces_skipped_unalignable=skipped,
        )
        self.steps.append(result)
        if response.HasField("error"):
            self._break(
                inspection.model_id,
                _error_code(response.error.code),
                response.error.message,
            )
            return
        failures = [
            value
            for value in response.results
            if value.WhichOneof("outcome") == "error"
        ]
        if failures:
            self._break(
                inspection.model_id,
                "item_inference_failed",
                "one or more aligned face embeddings failed",
                count=len(failures),
            )
        # Successful vectors deliberately remain in the report only.  Without a
        # canonical face_id there is no idempotent owner_id for media-db.vectors.

    @staticmethod
    def _tensor_values(tensor: pb2.Tensor, dimensions: int) -> list[float]:
        dtype = _PROTO_DTYPES.get(tensor.dtype)
        if dtype is None or tensor.dtype not in _VECTOR_QUANTIZATION:
            raise ValueError("embedding tensor dtype is unsupported")
        shape = tuple(int(value) for value in tensor.shape)
        if not shape or any(value <= 0 for value in shape):
            raise ValueError("embedding tensor shape is invalid")
        count = math.prod(shape)
        if count != dimensions or len(tensor.data) != count * dtype.itemsize:
            raise ValueError("embedding tensor dimensions do not match the registry")
        array = np.frombuffer(tensor.data, dtype=dtype).astype(np.float32)
        if not np.all(np.isfinite(array)):
            raise ValueError("embedding tensor contains non-finite values")
        return [float(value) for value in array]

    def _store_media_embedding(
        self,
        media_id: str,
        vector_space: str,
        values: list[float],
        tensor: pb2.Tensor,
        response: pb2.InferResponse,
        *,
        normalized: bool,
    ) -> None:
        record = self.records[media_id]
        content = record.get("content") or {}
        content["embedding"] = {
            "space": vector_space,
            "dimensions": len(values),
            "storage": "index",
            "index_key": f"vec:media:{media_id}:{vector_space}",
            "values": None,
            "quantization": _VECTOR_QUANTIZATION[tensor.dtype],
            "normalized": normalized,
        }
        record["content"] = content
        self.vectors[media_id, vector_space] = (
            values,
            _VECTOR_QUANTIZATION[tensor.dtype],
        )
        ran_at = _now()
        self._set_stage_done(media_id, "image_embedding", ran_at)
        self._add_model_run(media_id, response, ran_at)

    def _response_failure(
        self, inspection: ModelInspection, error: pb2.InferError
    ) -> None:
        code = _error_code(error.code)
        self._break(inspection.model_id, code, error.message)
        media_stage = _MEDIA_STAGES.get(inspection.task)
        if media_stage:
            for media_id in self.target_ids:
                self._set_stage_failed(media_id, media_stage, code, error.message)

    def _item_failure(
        self, inspection: ModelInspection, media_id: str, error: pb2.InferError
    ) -> None:
        code = _error_code(error.code)
        self._break(inspection.model_id, code, error.message, media_id=media_id)
        media_stage = _MEDIA_STAGES.get(inspection.task)
        if media_stage:
            self._set_stage_failed(media_id, media_stage, code, error.message)

    def _set_stage_done(self, media_id: str, stage: str, completed_at: str) -> None:
        processing = self.records[media_id]["processing"]
        processing["stages"][stage] = {
            "status": "done",
            "attempts": 1,
            "completed_at": completed_at,
        }

    def _set_stage_failed(
        self, media_id: str, stage: str, code: str, message: str
    ) -> None:
        processing = self.records[media_id]["processing"]
        processing["stages"][stage] = {
            "status": "failed",
            "attempts": 1,
            "completed_at": _now(),
            "last_error": {
                "code": code[:64],
                "message": message,
                "retryable": False,
                "occurred_at": _now(),
            },
        }

    def _add_model_run(
        self, media_id: str, response: pb2.InferResponse, ran_at: str
    ) -> None:
        pin = response.pin
        runtime = _RUNTIMES.get(response.runtime_used)
        precision = _PRECISIONS.get(pin.precision)
        if not pin.model_id or not pin.version or runtime is None or precision is None:
            raise ValueError("successful inference returned incomplete model provenance")
        proxy_id = self.proxy_by_media[media_id]
        digest = blake3(
            b"real-photo-model-run-v1\0"
            + pin.SerializeToString(deterministic=True)
            + b"\0"
            + media_id.encode("ascii")
            + b"\0"
            + proxy_id.encode("ascii")
        ).hexdigest()
        model_id = pin.model_id[:40]
        run_id = f"run-{model_id}-{digest[:12]}"
        model_run = {
            "run_id": run_id,
            "model": {
                "model_id": pin.model_id,
                "version": pin.version,
                "weights_blake3": pin.weights_blake3 or None,
                "runtime": runtime,
                "precision": precision,
                "config_blake3": pin.config_blake3 or None,
            },
            "ran_at": ran_at,
            "input_proxy_id": proxy_id,
            "duration_ms": None,
            "job_id": None,
        }
        runs = self.records[media_id].setdefault("model_runs", [])
        runs[:] = [run for run in runs if run.get("run_id") != run_id]
        runs.append(model_run)

    def _persist(self) -> dict[str, Any]:
        updated = [self.records[media_id] for media_id in self.target_ids]
        for record in updated:
            stages = record["processing"]["stages"]
            record["processing"]["state"] = (
                "failed"
                if any(
                    isinstance(value, Mapping) and value.get("status") == "failed"
                    for value in stages.values()
                )
                else "analyzed"
            )
            record["updated_at"] = _now()
            self.validator.validate(record)

        with self.database_class.open(self.database_path, migrate=False) as database:
            for (media_id, space), (values, quantization) in self.vectors.items():
                database.vectors.put(
                    "media",
                    media_id,
                    space,
                    values,
                    quantization=quantization,
                )
            for record in updated:
                database.put_media(record)
            return {
                "records_updated": len(updated),
                "vectors_written": len(self.vectors),
                "database_count": database.count_media(),
                "vector_count": database.vectors.count(),
            }

__all__ = ["PhotoAnalysisRunner", "response_report"]
