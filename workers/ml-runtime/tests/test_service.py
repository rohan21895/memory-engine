from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

import grpc
from blake3 import blake3

WORKER_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = WORKER_ROOT.parents[1]
sys.path.insert(0, str(WORKER_ROOT))

from memory_engine_ml_runtime.catalog import ModelCatalog
from memory_engine_ml_runtime.service import MlRuntimeService, start_server

from contracts.proto.generated.python import ml_runtime_pb2 as pb2
from contracts.proto.generated.python import ml_runtime_pb2_grpc as pb2_grpc


class TestLoopbackService(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.temporary.name)
        shutil.copytree(REPO_ROOT / "models", self.repo_root / "models")
        weights_dir = self.repo_root / "models" / "weights"
        weights_dir.mkdir()
        for config_path in (self.repo_root / "models" / "configs").glob("*.json"):
            config = json.loads(config_path.read_text(encoding="utf-8"))
            (weights_dir / config["weights"]["filename"]).write_bytes(
                config["model_id"].encode("utf-8")
            )
        catalog = ModelCatalog(
            repo_root=self.repo_root,
            environ={"MEMORY_ENGINE_ALLOW_UNVERIFIED_MODELS": "true"},
            provider_probe=lambda: frozenset({"onnxruntime_cpu"}),
        )
        self.running = start_server(MlRuntimeService(catalog), port=0)
        self.channel = grpc.insecure_channel(self.running.address)
        grpc.channel_ready_future(self.channel).result(timeout=5)
        self.stub = pb2_grpc.MlRuntimeStub(self.channel)

    def tearDown(self) -> None:
        self.channel.close()
        self.running.stop()
        self.temporary.cleanup()

    def test_server_is_loopback_only_and_lists_registry_models(self) -> None:
        self.assertEqual("127.0.0.1", self.running.host)
        response = self.stub.ListModels(
            pb2.ListModelsRequest(task="face_detection", include_unloadable=False),
            timeout=5,
        )
        self.assertEqual(pb2.LOAD_MODE_DEVELOPMENT, response.load_mode)
        self.assertEqual(
            ["yunet-2023mar", "scrfd-10g-bnkps"],
            [model.pin.model_id for model in response.models],
        )
        for model in response.models:
            self.assertTrue(model.loadable)
            self.assertEqual(pb2.OUTPUT_KIND_DETECTIONS, model.output_kind)
            self.assertEqual(
                [pb2.RUNTIME_TARGET_ONNXRUNTIME_CPU], list(model.available_runtimes)
            )

    def test_health_reports_mode_and_no_phantom_loaded_models(self) -> None:
        health = self.stub.Health(pb2.HealthRequest(), timeout=5)
        self.assertTrue(health.serving)
        self.assertEqual(pb2.LOAD_MODE_DEVELOPMENT, health.load_mode)
        self.assertEqual([], list(health.loaded))
        self.assertEqual(0, health.queue_depth)
        self.assertIn("development load gate enabled", health.warnings)

    def test_infer_returns_a_typed_validation_error(self) -> None:
        response = self.stub.Infer(pb2.InferRequest(), timeout=5)
        self.assertEqual(pb2.ERROR_CODE_INPUT_INVALID, response.error.code)
        self.assertEqual([], list(response.results))

    def test_load_model_returns_gate_refusal_before_execution(self) -> None:
        missing = self.stub.LoadModel(
            pb2.LoadModelRequest(model_id="not-registered"), timeout=5
        )
        self.assertFalse(missing.loaded)
        self.assertEqual(pb2.ERROR_CODE_MODEL_NOT_REGISTERED, missing.error.code)

        failed = self.stub.LoadModel(
            pb2.LoadModelRequest(model_id="siglip2-so400m-384"), timeout=5
        )
        self.assertFalse(failed.loaded)
        self.assertIn(
            failed.error.code,
            (pb2.ERROR_CODE_PROVIDER_UNAVAILABLE, pb2.ERROR_CODE_CONFIG_MISMATCH),
        )

    def test_load_refusal_error_mapping_is_terminal_and_exhaustive(self) -> None:
        mappings = {
            "UNLOADABLE_REASON_CONFIG_MISMATCH": pb2.ERROR_CODE_CONFIG_MISMATCH,
            "UNLOADABLE_REASON_LICENSE_UNVERIFIED": pb2.ERROR_CODE_LICENSE_BLOCKED,
            "UNLOADABLE_REASON_LICENSE_BLOCKS_RELEASE": pb2.ERROR_CODE_LICENSE_BLOCKED,
        }
        reasons = [
            "UNLOADABLE_REASON_NOT_REGISTERED",
            "UNLOADABLE_REASON_WEIGHTS_MISSING",
            "UNLOADABLE_REASON_HASH_MISMATCH",
            "UNLOADABLE_REASON_HASH_UNPINNED",
            "UNLOADABLE_REASON_LICENSE_UNVERIFIED",
            "UNLOADABLE_REASON_LICENSE_BLOCKS_RELEASE",
            "UNLOADABLE_REASON_NO_PROVIDER_AVAILABLE",
            "UNLOADABLE_REASON_CONFIG_INVALID",
            "UNLOADABLE_REASON_CONFIG_MISSING",
            "UNLOADABLE_REASON_CONFIG_MISMATCH",
            "UNLOADABLE_REASON_CONFIG_UNPINNED",
            "UNLOADABLE_REASON_INTEGRITY_UNVERIFIED",
            "UNLOADABLE_REASON_PLACEHOLDER",
        ]
        for reason in reasons:
            with self.subTest(reason=reason):
                error = MlRuntimeService._load_error(reason)
                self.assertEqual(
                    mappings.get(reason, pb2.ERROR_CODE_MODEL_UNLOADABLE),
                    error.code,
                )
                self.assertFalse(error.retryable)

    def test_refusal_pin_reports_registry_values_not_observed_digests(self) -> None:
        config_path = (
            self.repo_root / "models" / "configs" / "transnetv2.json"
        )
        registry = json.loads(
            (self.repo_root / "models" / "registry.json").read_text(
                encoding="utf-8"
            )
        )
        registry_pin = next(
            entry["config_blake3"]
            for entry in registry["entries"]
            if entry["model_id"] == "transnetv2"
        )
        config_path.write_bytes(config_path.read_bytes() + b"\n")
        self.running.stop()
        catalog = ModelCatalog(
            repo_root=self.repo_root,
            environ={"MEMORY_ENGINE_ALLOW_UNVERIFIED_MODELS": "true"},
            provider_probe=lambda: frozenset({"onnxruntime_cpu"}),
        )
        self.running = start_server(MlRuntimeService(catalog), port=0)
        self.channel.close()
        self.channel = grpc.insecure_channel(self.running.address)
        grpc.channel_ready_future(self.channel).result(timeout=5)
        self.stub = pb2_grpc.MlRuntimeStub(self.channel)

        response = self.stub.LoadModel(
            pb2.LoadModelRequest(model_id="transnetv2"), timeout=5
        )

        self.assertFalse(response.loaded)
        self.assertEqual(pb2.ERROR_CODE_CONFIG_MISMATCH, response.error.code)
        self.assertEqual(registry_pin, response.pin.config_blake3)
        self.assertNotEqual(
            blake3(config_path.read_bytes()).hexdigest(), response.pin.config_blake3
        )
        self.assertEqual("", response.pin.weights_blake3)

    def test_unpinned_weights_are_not_reported_as_a_pin(self) -> None:
        response = self.stub.LoadModel(
            pb2.LoadModelRequest(model_id="siglip2-so400m-384"), timeout=5
        )

        self.assertEqual("", response.pin.weights_blake3)


if __name__ == "__main__":
    unittest.main()
