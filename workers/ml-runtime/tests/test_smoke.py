from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

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

from memory_engine_ml_runtime.media_db import MediaDbProxyResolver
from memory_engine_ml_runtime.smoke import (
    _persist_media,
    _read_records,
    _run_ingest,
    build_scan_job,
)


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
