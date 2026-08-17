"""Fail-closed registry inspection for the local model host."""

from __future__ import annotations

import importlib.util
import json
import os
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

from blake3 import blake3
from jsonschema import Draft202012Validator
from models.policy import load_gate as TRUSTED_LOAD_GATE

# The policy and protobuf files are repository-owned inputs to this worker. Keep their source
# directories out of this package so there cannot be a private copy that drifts from them.
DEFAULT_REPO_ROOT = Path(__file__).resolve().parents[3]


RUNTIME_ENUM_NAMES = {
    "onnxruntime_cpu": "RUNTIME_TARGET_ONNXRUNTIME_CPU",
    "onnxruntime_coreml": "RUNTIME_TARGET_ONNXRUNTIME_COREML",
    "onnxruntime_directml": "RUNTIME_TARGET_ONNXRUNTIME_DIRECTML",
    "onnxruntime_cuda": "RUNTIME_TARGET_ONNXRUNTIME_CUDA",
    "ctranslate2": "RUNTIME_TARGET_CTRANSLATE2",
    "mlx": "RUNTIME_TARGET_MLX",
    "llama_cpp": "RUNTIME_TARGET_LLAMA_CPP",
    "opencv": "RUNTIME_TARGET_OPENCV",
    "librosa": "RUNTIME_TARGET_LIBROSA",
    "native": "RUNTIME_TARGET_NATIVE",
}

PRECISION_ENUM_NAMES = {
    "fp32": "PRECISION_FP32",
    "fp16": "PRECISION_FP16",
    "bf16": "PRECISION_BF16",
    "int8": "PRECISION_INT8",
    "int4": "PRECISION_INT4",
}

OUTPUT_KIND_ENUM_NAMES = {
    "face_detection": "OUTPUT_KIND_DETECTIONS",
    "shot_detection": "OUTPUT_KIND_SHOTS",
}


class CatalogError(RuntimeError):
    """The registry itself cannot be loaded safely."""


@dataclass(frozen=True)
class ModelInspection:
    entry: Mapping[str, Any]
    config: Mapping[str, Any]
    config_blake3: str | None
    weights_blake3: str | None
    weights_path: Path | None
    available_runtimes: tuple[str, ...]
    unloadable_reason: str | None
    release_unloadable_reason: str | None

    @property
    def model_id(self) -> str:
        return str(self.entry["model_id"])

    @property
    def task(self) -> str:
        return str(self.entry["task"])


ProviderProbe = Callable[[], frozenset[str]]


def detect_available_providers() -> frozenset[str]:
    """Return execution runtimes this host can actually create sessions for."""
    providers: set[str] = set()
    if importlib.util.find_spec("onnxruntime") is not None:
        import onnxruntime  # type: ignore[import-not-found]

        onnx_providers = set(onnxruntime.get_available_providers())
        if "CPUExecutionProvider" in onnx_providers:
            providers.add("onnxruntime_cpu")
        if "CoreMLExecutionProvider" in onnx_providers:
            providers.add("onnxruntime_coreml")
        if "DmlExecutionProvider" in onnx_providers:
            providers.add("onnxruntime_directml")
        if "CUDAExecutionProvider" in onnx_providers:
            providers.add("onnxruntime_cuda")
    return frozenset(providers)


class ModelCatalog:
    """Immutable view of the registry evaluated through the shared load gate."""

    def __init__(
        self,
        *,
        repo_root: Path = DEFAULT_REPO_ROOT,
        weights_dir: Path | None = None,
        environ: Mapping[str, str] | None = None,
        provider_probe: ProviderProbe = detect_available_providers,
        load_gate: ModuleType = TRUSTED_LOAD_GATE,
    ) -> None:
        self.repo_root = repo_root.resolve()
        self.models_root = self.repo_root / "models"
        self.registry_path = self.models_root / "registry.json"
        self.weights_dir = (weights_dir or self.models_root / "weights").resolve()
        self._environ = dict(os.environ if environ is None else environ)
        self._provider_probe = provider_probe
        # The policy implementation is trusted application code, never code from the
        # caller-selected model tree. Tests may inject a gate explicitly without
        # weakening the production import boundary.
        self._load_gate = load_gate
        self._registry = self._read_registry()
        self._policy = self._registry["load_policy"]
        self.mode = self._load_gate.resolve_mode(self._policy, self._environ)
        self._schema = self._read_json(
            self.models_root / "schema" / "model-config.schema.json"
        )
        Draft202012Validator.check_schema(self._schema)
        self._validator = Draft202012Validator(self._schema)
        self._entries = {
            str(entry["model_id"]): entry for entry in self._registry["entries"]
        }
        self._inspection_cache: dict[str, ModelInspection] = {}
        self._available_providers: frozenset[str] | None = None
        self._cache_lock = threading.RLock()

    def inspect_all(self, task: str = "") -> list[ModelInspection]:
        return [
            self._cached_inspection(entry)
            for entry in self._registry["entries"]
            if not task or entry["task"] == task
        ]

    def inspect(self, model_id: str) -> ModelInspection | None:
        entry = self._entries.get(model_id)
        if entry is None:
            return None
        return self._cached_inspection(entry)

    def _cached_inspection(self, entry: Mapping[str, Any]) -> ModelInspection:
        model_id = str(entry["model_id"])
        with self._cache_lock:
            cached = self._inspection_cache.get(model_id)
            if cached is not None:
                return cached
            if self._available_providers is None:
                self._available_providers = self._provider_probe()
            inspected = self._inspect(entry, self._available_providers)
            self._inspection_cache[model_id] = inspected
            return inspected

    def _read_registry(self) -> Mapping[str, Any]:
        registry = self._read_json(self.registry_path)
        entries = registry.get("entries")
        policy = registry.get("load_policy")
        if not isinstance(entries, list) or not isinstance(policy, dict):
            raise CatalogError("model registry is missing entries or load_policy")
        ids = [entry.get("model_id") for entry in entries if isinstance(entry, dict)]
        if len(ids) != len(entries) or len(ids) != len(set(ids)):
            raise CatalogError(
                "model registry entries must have unique model_id values"
            )
        return registry

    @staticmethod
    def _read_json(path: Path) -> Mapping[str, Any]:
        try:
            parsed = json.loads(path.read_bytes().decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise CatalogError(
                f"required model metadata is unreadable: {path.name}"
            ) from error
        if not isinstance(parsed, dict):
            raise CatalogError(
                f"required model metadata must be an object: {path.name}"
            )
        return parsed

    def _inspect(
        self, entry: Mapping[str, Any], available: frozenset[str]
    ) -> ModelInspection:
        config_path = self._safe_child(self.models_root, str(entry.get("config", "")))
        config_present = config_path is not None and config_path.is_file()
        config: Mapping[str, Any] = {}
        config_valid = False
        actual_config_digest: str | None = None
        if config_present and config_path is not None:
            try:
                raw_config = config_path.read_bytes()
            except OSError:
                # One unreadable entry is an unloadable model, not a catalog-wide
                # exception that makes every other model disappear from ListModels.
                config_present = False
            else:
                # This is intentionally the raw byte buffer, not parsed or
                # re-serialised JSON.
                actual_config_digest = blake3(raw_config).hexdigest()
                try:
                    candidate_config = json.loads(raw_config.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    candidate_config = None
                if isinstance(candidate_config, dict):
                    config = candidate_config
                    config_valid = not any(
                        self._validator.iter_errors(candidate_config)
                    )
                    config_valid = config_valid and candidate_config.get(
                        "model_id"
                    ) == entry.get("model_id")
                    config_valid = config_valid and candidate_config.get(
                        "task"
                    ) == entry.get("task")

        weights = (
            config.get("weights") if isinstance(config.get("weights"), dict) else {}
        )
        weights_path = self._safe_child(
            self.weights_dir, str(weights.get("filename", ""))
        )
        weights_present = weights_path is not None and weights_path.is_file()
        actual_weights_digest = (
            self._hash_file(weights_path)
            if weights_present and weights_path is not None
            else None
        )
        declared_runtimes = config.get("runtime_targets", [])
        available_runtimes = tuple(
            runtime
            for runtime in declared_runtimes
            if isinstance(runtime, str) and runtime in available
        )
        licence = (
            config.get("license") if isinstance(config.get("license"), dict) else {}
        )
        rollout = (
            config.get("rollout") if isinstance(config.get("rollout"), dict) else {}
        )

        candidate = self._load_gate.Candidate(
            registered=True,
            weights_present=weights_present,
            pinned_hash=weights.get("blake3"),
            actual_hash=actual_weights_digest,
            license_verified=licence.get("verified") is True,
            blocks_commercial_release=licence.get("blocks_commercial_release") is True,
            config_valid=config_valid,
            config_present=config_present,
            pinned_config_digest=entry.get("config_blake3"),
            actual_config_digest=actual_config_digest,
            is_placeholder=rollout.get("state") == "placeholder",
            available_providers=available_runtimes,
        )
        refusal = self._load_gate.decide_load(candidate, self.mode, self._policy)
        release_refusal = self._load_gate.decide_load(
            candidate, "release", self._policy
        )
        return ModelInspection(
            entry=entry,
            config=config,
            config_blake3=actual_config_digest,
            weights_blake3=actual_weights_digest,
            weights_path=weights_path if weights_present else None,
            available_runtimes=available_runtimes,
            unloadable_reason=refusal,
            release_unloadable_reason=release_refusal,
        )

    @staticmethod
    def _safe_child(root: Path, relative: str) -> Path | None:
        if not relative:
            return None
        candidate = (root / relative).resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            return None
        return candidate

    @staticmethod
    def _hash_file(path: Path) -> str:
        digest = blake3()
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
        return digest.hexdigest()
