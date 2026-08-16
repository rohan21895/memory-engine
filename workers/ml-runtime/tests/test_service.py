from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

try:
    import blake3 as _blake3_dependency  # noqa: F401
    import google.protobuf as _protobuf_dependency  # noqa: F401
    import grpc
    import jsonschema as _jsonschema_dependency  # noqa: F401
except (
    ModuleNotFoundError
) as error:  # The repository CI does not install worker projects yet.
    raise unittest.SkipTest(
        f"install workers/ml-runtime dependencies: {error.name}"
    ) from error

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


if __name__ == "__main__":
    unittest.main()
