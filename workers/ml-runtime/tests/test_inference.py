from __future__ import annotations

import json
import math
import shutil
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

try:
    import cv2
    import grpc
    import jsonschema as _jsonschema_dependency  # noqa: F401
    import numpy as np
    from blake3 import blake3
except ModuleNotFoundError as error:
    raise unittest.SkipTest(
        f"install workers/ml-runtime dependencies: {error.name}"
    ) from error

WORKER_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = WORKER_ROOT.parents[1]
sys.path.insert(0, str(WORKER_ROOT))

from memory_engine_ml_runtime.catalog import ModelCatalog
from memory_engine_ml_runtime.execution import SessionManager
from memory_engine_ml_runtime.service import MlRuntimeService, start_server

from contracts.proto.generated.python import ml_runtime_pb2 as pb2
from contracts.proto.generated.python import ml_runtime_pb2_grpc as pb2_grpc


class _Metadata:
    def __init__(self, name: str, shape=None, kind: str = "tensor(float)") -> None:
        self.name = name
        self.shape = shape or []
        self.type = kind


class _FakeSession:
    run_count = 0
    block = False
    fixed_batch = False
    entered = threading.Event()
    release = threading.Event()

    def __init__(self, path: str, sess_options, providers) -> None:
        del sess_options
        self._providers = providers
        self._aesthetic = path.endswith("aesthetic_head_v2.onnx")
        if self._aesthetic:
            self._input_name = "embedding"
            self._input_shape = [None, 1152]
            self._output_names = ["score"]
        else:
            self._input_name = "input"
            self._input_shape = [None, 3, 640, 640]
            self._output_names = [
                *(f"cls_{stride}" for stride in (8, 16, 32)),
                *(f"obj_{stride}" for stride in (8, 16, 32)),
                *(f"bbox_{stride}" for stride in (8, 16, 32)),
                *(f"kps_{stride}" for stride in (8, 16, 32)),
            ]

    def get_providers(self):
        return self._providers

    def get_inputs(self):
        shape = list(self._input_shape)
        if type(self).fixed_batch:
            shape[0] = 1
        return [_Metadata(self._input_name, shape)]

    def get_outputs(self):
        return [_Metadata(name) for name in self._output_names]

    def run(self, output_names, feeds):
        type(self).run_count += 1
        if type(self).block:
            type(self).entered.set()
            type(self).release.wait(timeout=5)
        if self._aesthetic:
            batch = feeds["embedding"].shape[0]
            return [np.full((batch, 1), 5.5, dtype=np.float32)]
        batch = feeds["input"].shape[0]
        outputs = {}
        for stride in (8, 16, 32):
            count = (640 // stride) * (640 // stride)
            outputs[f"cls_{stride}"] = np.zeros((batch, count, 1), dtype=np.float32)
            outputs[f"obj_{stride}"] = np.zeros((batch, count, 1), dtype=np.float32)
            outputs[f"bbox_{stride}"] = np.zeros((batch, count, 4), dtype=np.float32)
            outputs[f"kps_{stride}"] = np.zeros((batch, count, 10), dtype=np.float32)
        index = 25 * (640 // 8) + 20
        outputs["cls_8"][:, index, 0] = 0.9
        outputs["obj_8"][:, index, 0] = 0.9
        outputs["bbox_8"][:, index, :] = (0.5, 0.5, math.log(4.0), math.log(4.0))
        outputs["kps_8"][:, index, :] = (
            0.2,
            0.2,
            0.8,
            0.2,
            0.5,
            0.5,
            0.25,
            0.8,
            0.75,
            0.8,
        )
        return [outputs[name] for name in output_names]


class _FakeOrt:
    class SessionOptions:
        pass

    class GraphOptimizationLevel:
        ORT_ENABLE_ALL = 99

    InferenceSession = _FakeSession


class TestRealInferPath(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        shutil.copytree(REPO_ROOT / "models", self.root / "models")
        weights_dir = self.root / "models" / "weights"
        weights_dir.mkdir()
        config = json.loads(
            (self.root / "models" / "configs" / "yunet-2023mar.json").read_text(
                encoding="utf-8"
            )
        )
        (weights_dir / config["weights"]["filename"]).write_bytes(b"fake-yunet-onnx")
        aesthetic = json.loads(
            (self.root / "models" / "configs" / "laion-aesthetic-v2.json").read_text(
                encoding="utf-8"
            )
        )
        (weights_dir / aesthetic["weights"]["filename"]).write_bytes(
            b"fake-aesthetic-onnx"
        )

        image = np.zeros((50, 100, 3), dtype=np.uint8)
        image[:, :, 1] = 80
        encoded_ok, encoded = cv2.imencode(".jpg", image)
        self.assertTrue(encoded_ok)
        raw = encoded.tobytes()
        self.proxy_id = blake3(raw).hexdigest()
        self.proxy_path = self.root / f"{self.proxy_id}.jpg"
        self.proxy_path.write_bytes(raw)
        proxy = {
            "proxy_id": self.proxy_id,
            "kind": "thumbnail_512",
            "path": str(self.proxy_path),
            "size": {"width": 100, "height": 50},
            "byte_size": len(raw),
        }

        catalog = ModelCatalog(
            repo_root=self.root,
            environ={"MEMORY_ENGINE_ALLOW_UNVERIFIED_MODELS": "true"},
            provider_probe=lambda: frozenset({"onnxruntime_cpu"}),
        )
        sessions = SessionManager(ort_module=_FakeOrt)
        resolver = lambda value: proxy if value == self.proxy_id else None
        self.running = start_server(
            MlRuntimeService(catalog, proxy_resolver=resolver, sessions=sessions),
            port=0,
        )
        self.channel = grpc.insecure_channel(self.running.address)
        grpc.channel_ready_future(self.channel).result(timeout=5)
        self.stub = pb2_grpc.MlRuntimeStub(self.channel)
        _FakeSession.run_count = 0
        _FakeSession.block = False
        _FakeSession.fixed_batch = False
        _FakeSession.entered.clear()
        _FakeSession.release.clear()

    def tearDown(self) -> None:
        self.channel.close()
        self.running.stop()
        self.temporary.cleanup()

    def _request(self, request_id: str = "infer-real-proxies") -> pb2.InferRequest:
        return pb2.InferRequest(
            request_id=request_id,
            model_id="yunet-2023mar",
            preferred_runtimes=[
                pb2.RUNTIME_TARGET_ONNXRUNTIME_COREML,
                pb2.RUNTIME_TARGET_ONNXRUNTIME_CPU,
            ],
            items=[
                pb2.InferItem(
                    item_id="first",
                    proxy_id=self.proxy_id,
                    alignment=pb2.ALIGNMENT_NONE,
                ),
                pb2.InferItem(
                    item_id="second",
                    proxy_id=self.proxy_id,
                    alignment=pb2.ALIGNMENT_NONE,
                ),
            ],
        )

    def test_two_real_proxy_decodes_run_as_one_batch_and_return_detections(
        self,
    ) -> None:
        response = self.stub.Infer(self._request(), timeout=10)
        self.assertFalse(response.HasField("error"))
        self.assertEqual(pb2.RUNTIME_TARGET_ONNXRUNTIME_CPU, response.runtime_used)
        self.assertEqual(2, response.batch_size)
        self.assertEqual(1, _FakeSession.run_count)
        self.assertEqual(
            ["first", "second"], [item.item_id for item in response.results]
        )
        for result in response.results:
            self.assertEqual("detections", result.WhichOneof("outcome"))
            self.assertEqual(1, len(result.detections.detections))
            detection = result.detections.detections[0]
            self.assertAlmostEqual(0.23125, detection.box.x, places=6)
            self.assertAlmostEqual(0.0875, detection.box.y, places=6)
            self.assertAlmostEqual(0.05, detection.box.w, places=6)
            self.assertAlmostEqual(0.1, detection.box.h, places=6)
            self.assertEqual(pb2.LANDMARK_SCHEME_YUNET_5, detection.landmark_scheme)
            self.assertAlmostEqual(0.6, result.detections.score_threshold, places=6)
            self.assertAlmostEqual(0.3, result.detections.nms_iou_threshold, places=6)

        cached = self.stub.Infer(self._request(), timeout=10)
        self.assertEqual(response.SerializeToString(), cached.SerializeToString())
        self.assertEqual(1, _FakeSession.run_count)

        changed = self._request()
        changed.items[0].item_id = "changed"
        rejected = self.stub.Infer(changed, timeout=10)
        self.assertEqual(pb2.ERROR_CODE_INPUT_INVALID, rejected.error.code)

    def test_proxy_lookup_never_falls_back_to_an_original_id(self) -> None:
        request = self._request("original-is-not-a-proxy")
        del request.items[:]
        request.items.add(
            item_id="original",
            proxy_id="0" * 64,
            alignment=pb2.ALIGNMENT_NONE,
        )
        response = self.stub.Infer(request, timeout=10)
        self.assertFalse(response.HasField("error"))
        self.assertEqual(pb2.ERROR_CODE_PROXY_NOT_FOUND, response.results[0].error.code)
        self.assertEqual(0, _FakeSession.run_count)

    def test_inline_tensors_batch_and_return_postprocessed_tensor_sets(self) -> None:
        values = np.ones((1, 1152), dtype=np.float32)
        tensor = pb2.Tensor(
            shape=list(values.shape),
            dtype=pb2.DTYPE_FLOAT32,
            data=values.tobytes(),
            name="embedding",
        )
        request = pb2.InferRequest(
            request_id="tensor-batch",
            model_id="laion-aesthetic-v2",
            preferred_runtimes=[pb2.RUNTIME_TARGET_ONNXRUNTIME_CPU],
            items=[
                pb2.InferItem(
                    item_id="one",
                    tensors=pb2.TensorSet(tensors=[tensor]),
                    alignment=pb2.ALIGNMENT_NONE,
                ),
                pb2.InferItem(
                    item_id="two",
                    tensors=pb2.TensorSet(tensors=[tensor]),
                    alignment=pb2.ALIGNMENT_NONE,
                ),
            ],
        )
        response = self.stub.Infer(request, timeout=10)
        self.assertFalse(response.HasField("error"))
        self.assertEqual(1, _FakeSession.run_count)
        for result in response.results:
            self.assertEqual("tensors", result.WhichOneof("outcome"))
            output = result.tensors.tensors[0]
            self.assertEqual([1, 1], list(output.shape))
            self.assertEqual(
                0.5, float(np.frombuffer(output.data, dtype=np.float32)[0])
            )

    def test_expected_pin_mismatch_fails_before_proxy_decode(self) -> None:
        request = self._request("pin-mismatch")
        request.expected_pin.config_blake3 = "f" * 64
        response = self.stub.Infer(request, timeout=10)
        self.assertEqual(pb2.ERROR_CODE_PIN_MISMATCH, response.error.code)
        self.assertEqual(0, _FakeSession.run_count)

    def test_concurrent_duplicate_request_runs_exactly_once(self) -> None:
        _FakeSession.block = True
        request = self._request("concurrent-idempotency")
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(self.stub.Infer, request, timeout=10)
            self.assertTrue(_FakeSession.entered.wait(timeout=5))
            second = pool.submit(self.stub.Infer, request, timeout=10)
            _FakeSession.release.set()
            first_response = first.result(timeout=10)
            second_response = second.result(timeout=10)
        self.assertEqual(
            first_response.SerializeToString(), second_response.SerializeToString()
        )
        self.assertEqual(1, _FakeSession.run_count)

    def test_fixed_batch_onnx_input_overrides_an_incorrect_dynamic_config(self) -> None:
        _FakeSession.fixed_batch = True
        response = self.stub.Infer(self._request("fixed-onnx-batch"), timeout=10)
        self.assertFalse(response.HasField("error"))
        self.assertEqual(2, len(response.results))
        self.assertEqual(2, _FakeSession.run_count)

    def test_load_health_and_unload_report_the_actual_runtime(self) -> None:
        loaded = self.stub.LoadModel(
            pb2.LoadModelRequest(
                model_id="yunet-2023mar",
                preferred_runtimes=[pb2.RUNTIME_TARGET_ONNXRUNTIME_CPU],
            ),
            timeout=10,
        )
        self.assertTrue(loaded.loaded)
        self.assertEqual(pb2.RUNTIME_TARGET_ONNXRUNTIME_CPU, loaded.runtime_used)
        health = self.stub.Health(pb2.HealthRequest(), timeout=5)
        self.assertEqual(1, len(health.loaded))
        self.assertEqual(pb2.RUNTIME_TARGET_ONNXRUNTIME_CPU, health.loaded[0].runtime)
        unloaded = self.stub.UnloadModel(
            pb2.UnloadModelRequest(model_id="yunet-2023mar"), timeout=5
        )
        self.assertTrue(unloaded.unloaded)
        self.assertGreater(unloaded.freed_bytes, 0)
        self.assertEqual(
            [], list(self.stub.Health(pb2.HealthRequest(), timeout=5).loaded)
        )


if __name__ == "__main__":
    unittest.main()
