from __future__ import annotations

import copy
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

try:
    import cv2
    import grpc as _grpc_dependency  # noqa: F401
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

from memory_engine_ml_runtime.media_db import MediaDbProxyResolver, database_type
from memory_engine_ml_runtime.photo_analysis import PhotoAnalysisRunner
from memory_engine_ml_runtime.smoke import (
    _persist_media,
    _read_records,
    _run_ingest,
    build_scan_job,
)
from contracts.proto.generated.python import ml_runtime_pb2 as pb2


class TestSmokeJob(unittest.TestCase):
    def test_job_is_specialised_from_the_golden_scan_fixture(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary).resolve()
            job = build_scan_job(source, REPO_ROOT)
        self.assertEqual("scan_source", job["job_type"])
        self.assertEqual([str(source)], job["inputs"]["source_paths"])
        self.assertEqual(
            blake3(str(source).encode("utf-8")).hexdigest(),
            job["inputs"]["source_locator_digest"],
        )
        self.assertEqual(
            "1f5d55602fdab85a6ca4488c3d13276ed719196615421a6bf0d66c46901fd61a",
            job["params_digest"],
        )
        self.assertFalse(job["egress"]["requires_egress"])
        self.assertTrue(job["checkpoint"]["resumable"])
        self.assertEqual("pending", job["state"]["status"])


class TestPhotoAnalysisPipeline(unittest.TestCase):
    def test_every_declared_step_runs_and_contract_safe_outputs_are_persisted(
        self,
    ) -> None:
        fixture = json.loads(
            (
                REPO_ROOT
                / "contracts"
                / "fixtures"
                / "media-record"
                / "valid"
                / "image-no-exif-date.json"
            ).read_text(encoding="utf-8")
        )
        record = copy.deepcopy(fixture)
        record["quality"] = None
        record["content"] = None
        record["faces"] = None
        record["model_runs"] = []
        record["processing"] = {
            "state": "proxied",
            "stages": {
                "hash": {"status": "done", "attempts": 1},
                "metadata": {"status": "done", "attempts": 1},
                "thumbnail": {"status": "done", "attempts": 1},
                "perceptual_hash": {"status": "done", "attempts": 1},
            },
        }
        media_id = record["media_id"]
        proxy_id = record["proxies"][0]["proxy_id"]

        inspections = {
            "siglip": SimpleNamespace(
                model_id="siglip",
                task="image_embedding",
                unloadable_reason=None,
                available_runtimes=("onnxruntime_cpu",),
                config={
                    "postprocessing": {"steps": ["l2_normalize"]},
                    "outputs": [
                        {
                            "name": "image_embeds",
                            "dimensions": 2,
                            "vector_space": "siglip2_so400m_1152",
                        }
                    ]
                },
            ),
            "detector": SimpleNamespace(
                model_id="detector",
                task="face_detection",
                unloadable_reason=None,
                available_runtimes=("onnxruntime_cpu",),
                config={},
            ),
            "arcface": SimpleNamespace(
                model_id="arcface",
                task="face_embedding",
                unloadable_reason=None,
                available_runtimes=("onnxruntime_cpu",),
                config={},
            ),
        }

        class FakeCatalog:
            mode = "development"

            @staticmethod
            def inspect(model_id: str):
                return inspections.get(model_id)

        calls: list[str] = []

        def response(model_id: str, items):
            calls.append(model_id)
            pin = pb2.ModelPin(
                model_id=model_id,
                version="1.0.0",
                runtime=pb2.RUNTIME_TARGET_ONNXRUNTIME_CPU,
                precision=pb2.PRECISION_FP32,
            )
            if model_id == "siglip":
                results = [
                    pb2.InferResult(
                        item_id=items[0].item_id,
                        tensors=pb2.TensorSet(
                            tensors=[
                                pb2.Tensor(
                                    name="image_embeds",
                                    shape=[1, 2],
                                    dtype=pb2.DTYPE_FLOAT32,
                                    data=np.asarray(
                                        [0.6, 0.8], dtype=np.float32
                                    ).tobytes(),
                                )
                            ]
                        ),
                    )
                ]
            elif model_id == "detector":
                landmarks = [
                    pb2.Point2D(x=x, y=y)
                    for x, y in (
                        (0.4, 0.4),
                        (0.6, 0.4),
                        (0.5, 0.5),
                        (0.42, 0.62),
                        (0.58, 0.62),
                    )
                ]
                results = [
                    pb2.InferResult(
                        item_id=items[0].item_id,
                        detections=pb2.DetectionSet(
                            detections=[
                                pb2.Detection(
                                    box=pb2.NormalizedBox(
                                        x=0.3, y=0.3, w=0.4, h=0.4
                                    ),
                                    score=0.95,
                                    landmarks=landmarks,
                                    landmark_scheme=pb2.LANDMARK_SCHEME_INSIGHTFACE_5,
                                )
                            ]
                        ),
                    )
                ]
            else:
                self.assertEqual(pb2.ALIGNMENT_NEEDS_ALIGNMENT, items[0].alignment)
                self.assertEqual(5, len(items[0].landmarks))
                results = [
                    pb2.InferResult(
                        item_id=items[0].item_id,
                        tensors=pb2.TensorSet(
                            tensors=[
                                pb2.Tensor(
                                    name="embedding",
                                    shape=[1, 2],
                                    dtype=pb2.DTYPE_FLOAT32,
                                    data=np.asarray(
                                        [1.0, 0.0], dtype=np.float32
                                    ).tobytes(),
                                )
                            ]
                        ),
                    )
                ]
            return pb2.InferResponse(
                request_id=f"request-{model_id}",
                pin=pin,
                runtime_used=pb2.RUNTIME_TARGET_ONNXRUNTIME_CPU,
                results=results,
                batch_size=len(items),
            )

        pipeline = SimpleNamespace(
            pipeline_id="photo_analysis",
            steps=("classical_quality", "siglip", "detector", "arcface"),
        )
        Database = database_type(REPO_ROOT)
        with tempfile.TemporaryDirectory() as temporary:
            database_path = Path(temporary) / "library.db"
            self.assertEqual(1, _persist_media(REPO_ROOT, database_path, [record]))
            result = PhotoAnalysisRunner(
                repo_root=REPO_ROOT,
                database_path=database_path,
                database_class=Database,
                catalog=FakeCatalog(),
                pipeline=pipeline,
                records=[record],
                proxy_items=[(media_id, proxy_id)],
                infer=response,
            ).run()
            with Database.open(database_path, migrate=False) as database:
                stored = database.get_media(media_id)
                vector = database.vectors.get(
                    "media", media_id, "siglip2_so400m_1152"
                )

        self.assertEqual(["siglip", "detector", "arcface"], calls)
        self.assertEqual("partial_success", result["status"])
        self.assertEqual(1, result["persistence"]["records_updated"])
        self.assertEqual(1, result["persistence"]["vectors_written"])
        assert vector is not None
        self.assertAlmostEqual(0.6, vector[0], places=6)
        self.assertAlmostEqual(0.8, vector[1], places=6)
        assert stored is not None
        self.assertEqual("failed", stored["processing"]["state"])
        self.assertEqual(
            "done", stored["processing"]["stages"]["image_embedding"]["status"]
        )
        self.assertEqual(
            "done", stored["processing"]["stages"]["face_detection"]["status"]
        )
        self.assertEqual(
            "index", stored["content"]["embedding"]["storage"]
        )
        self.assertEqual(2, len(stored["model_runs"]))
        self.assertTrue(
            any(item.get("issue") == 34 for item in result["breakages"])
        )


@unittest.skipUnless(
    shutil.which("cargo"), "cargo is required for the real ingest smoke"
)
class TestRealIngestToMediaDb(unittest.TestCase):
    def test_real_jpeg_becomes_a_proxy_resolvable_only_through_media_db(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            source.mkdir()
            pixels = np.zeros((24, 32, 3), dtype=np.uint8)
            pixels[:, :, 2] = 180
            self.assertTrue(cv2.imwrite(str(source / "real.jpg"), pixels))
            work = root / "work"
            output = work / "library"
            checkpoint = work / "jobs" / "scan.json"
            initial = work / "jobs" / "scan.initial.json"

            first = _run_ingest(
                source=source,
                repo_root=REPO_ROOT,
                output_dir=output,
                checkpoint_path=checkpoint,
                initial_job_path=initial,
            )
            self.assertTrue(first["complete"])
            self.assertEqual(1, first["processed"])
            records = _read_records(output)
            self.assertEqual(1, len(records))
            self.assertEqual("image", records[0]["kind"])
            self.assertEqual("thumbnail_512", records[0]["proxies"][0]["kind"])

            database_path = work / "library.db"
            self.assertEqual(1, _persist_media(REPO_ROOT, database_path, records))
            resolver = MediaDbProxyResolver(REPO_ROOT, database_path)
            proxy_id = records[0]["proxies"][0]["proxy_id"]
            self.assertEqual(proxy_id, resolver(proxy_id)["proxy_id"])
            self.assertIsNone(resolver(records[0]["media_id"]))

            resumed = _run_ingest(
                source=source,
                repo_root=REPO_ROOT,
                output_dir=output,
                checkpoint_path=checkpoint,
                initial_job_path=initial,
            )
            self.assertTrue(resumed["complete"])
            self.assertEqual(0, resumed["processed"])


if __name__ == "__main__":
    unittest.main()
