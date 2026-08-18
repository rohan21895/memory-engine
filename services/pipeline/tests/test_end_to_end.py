"""A real folder in, a validated AlbumSpec out -- and, on request, the PDF.

The PDF half is behind `MEMORY_ENGINE_SLOW_TESTS=1` because rasterising twenty
306mm pages at 350 DPI takes about four minutes on an M-series laptop. That is
the renderer doing its job, not a defect, but it is too slow to sit in the
default suite. Everything up to and including the print validator runs every
time, so a regression in the spine is caught in seconds; only the pixels are
optional.

The progress assertions are here rather than in a unit test on purpose: what
matters is not that `ProgressReporter` can format a line, it is that a real run
of the real pipeline leaves a machine-readable trail a desktop shell could tail.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from support import (  # noqa: E402
    PRINT_SAFE_SIZE,
    FakeMlRuntime,
    make_library,
    require_ingest_binary,
)

from memory_engine_pipeline.runner import run_pipeline  # noqa: E402
from memory_engine_pipeline.stages.base import Settings, StageStatus  # noqa: E402

SLOW = os.environ.get("MEMORY_ENGINE_SLOW_TESTS") == "1"


def _stage(report, name):
    for result in report.results:
        if result.stage == name:
            return result
    raise AssertionError(f"{name} did not run")


class EndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        require_ingest_binary()
        cls.root = Path(tempfile.mkdtemp(prefix="mep-e2e-src-"))
        cls.workdir = Path(tempfile.mkdtemp(prefix="mep-e2e-work-"))
        make_library(cls.root, 8, size=PRINT_SAFE_SIZE)
        with FakeMlRuntime() as host:
            cls.report = run_pipeline(
                [cls.root],
                cls.workdir,
                stages=["ingest", "analysis", "faces", "ranking", "album"],
                settings=Settings(
                    ml_runtime_endpoint=host.endpoint,
                    render_print=False,
                    render_video=False,
                ),
            )

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.root, ignore_errors=True)
        if not SLOW:
            shutil.rmtree(cls.workdir, ignore_errors=True)

    def test_the_spine_completes(self):
        self.assertTrue(self.report.ok, [r.to_dict() for r in self.report.results])
        self.assertEqual(0, self.report.exit_code)
        for name in ("ingest", "analysis", "faces", "ranking", "album"):
            self.assertEqual(
                StageStatus.COMPLETED, _stage(self.report, name).status,
                _stage(self.report, name).detail,
            )

    def test_the_album_spec_satisfies_the_contract_and_passed_the_print_gate(self):
        spec = self._album()
        self.assertEqual("pass", spec["validation"]["status"])
        self.assertEqual(0, spec["validation"]["error_count"])
        self.assertEqual(spec["album_id"], spec["determinism"]["inputs_digest"])

        from jsonschema import Draft202012Validator
        from referencing import Registry, Resource

        schema_dir = Path(__file__).resolve().parents[3] / "contracts" / "schemas"
        documents = {
            path.name: json.loads(path.read_text(encoding="utf-8"))
            for path in sorted(schema_dir.glob("*.schema.json"))
        }
        registry = Registry().with_resources(
            [(name, Resource.from_contents(doc)) for name, doc in documents.items()]
        )
        validator = Draft202012Validator(
            documents["album-spec.schema.json"], registry=registry
        )
        errors = [f"{list(e.path)}: {e.message}" for e in validator.iter_errors(spec)]
        self.assertEqual([], errors)

    def test_every_media_record_satisfies_the_contract(self):
        """The pipeline writes MediaRecords by hand; nothing else checks them.

        media-db's `put_media` stores whatever it is given, so a field this
        runner gets wrong would sit in the library unnoticed until a consumer
        with a stricter reader -- the desktop shell, the render worker -- fell
        over on it.
        """
        import sqlite3

        from jsonschema import Draft202012Validator
        from referencing import Registry, Resource

        schema_dir = Path(__file__).resolve().parents[3] / "contracts" / "schemas"
        documents = {
            path.name: json.loads(path.read_text(encoding="utf-8"))
            for path in sorted(schema_dir.glob("*.schema.json"))
        }
        registry = Registry().with_resources(
            [(name, Resource.from_contents(doc)) for name, doc in documents.items()]
        )
        validator = Draft202012Validator(
            documents["media-record.schema.json"], registry=registry
        )
        with sqlite3.connect(self.workdir / "library.db") as connection:
            records = [
                json.loads(row[0])
                for row in connection.execute("SELECT record_json FROM media")
            ]
        self.assertEqual(8, len(records))
        for record in records:
            errors = [f"{list(e.path)}: {e.message}" for e in validator.iter_errors(record)]
            self.assertEqual([], errors, record["media_id"][:12])

    def test_every_job_the_run_created_satisfies_the_contract(self):
        import sqlite3

        with sqlite3.connect(self.workdir / "library.db") as connection:
            jobs = [
                json.loads(row[0])
                for row in connection.execute(
                    "SELECT record_json FROM job ORDER BY job_id"
                )
            ]
        self.assertGreaterEqual(len(jobs), 3)
        types = {job["job_type"] for job in jobs}
        self.assertEqual({"scan_source", "analyze_image", "cluster_faces", "dedupe_cluster",
                          "plan_album"},
                         types)
        for job in jobs:
            self.assertEqual("completed", job["state"]["status"], job["job_type"])
            self.assertFalse(job["egress"]["requires_egress"])

    def test_progress_is_machine_readable_and_honest(self):
        lines = [
            json.loads(line)
            for line in (self.workdir / "events.jsonl").read_text(
                encoding="utf-8"
            ).splitlines()
            if line.strip()
        ]
        self.assertTrue(lines)
        self.assertEqual({line["run_id"] for line in lines}, {self.report.run_id})

        kinds = {line["kind"] for line in lines}
        self.assertIn("run_start", kinds)
        self.assertIn("run_done", kinds)
        self.assertIn("progress", kinds)

        progress = [line for line in lines if line["kind"] == "progress"]
        self.assertTrue(progress)
        for line in progress:
            self.assertIn("unit", line)
            self.assertIn("units_done", line)
            # units_total may be null while a walk is still discovering; what it
            # must never be is a number smaller than what has already been done,
            # which is the shape of a progress bar that goes backwards.
            if line["units_total"] is not None:
                self.assertLessEqual(line["units_done"], line["units_total"], line)

        stage_units = {line["stage"]: line["unit"] for line in progress}
        self.assertIn("ingest", stage_units)
        self.assertIn("analysis", stage_units)

    def test_the_run_manifest_records_what_happened(self):
        manifest = json.loads((self.workdir / "run.json").read_text(encoding="utf-8"))
        self.assertTrue(manifest["ok"])
        self.assertEqual(0, manifest["exit_code"])
        self.assertEqual(
            ["ingest", "analysis", "faces", "ranking", "album"],
            [stage["stage"] for stage in manifest["stages"]],
        )

    @unittest.skipUnless(SLOW, "set MEMORY_ENGINE_SLOW_TESTS=1 to render the PDF")
    def test_render_print_produces_a_pdf(self):
        with FakeMlRuntime() as host:
            report = run_pipeline(
                [self.root],
                self.workdir,
                settings=Settings(
                    ml_runtime_endpoint=host.endpoint, render_video=False
                ),
            )
        result = _stage(report, "render-print")
        if result.status is StageStatus.UNAVAILABLE:
            self.skipTest(result.detail)
        self.assertEqual(StageStatus.COMPLETED, result.status, result.detail)
        pdf = Path(result.outputs[0])
        self.assertTrue(pdf.is_file())
        self.assertEqual(b"%PDF", pdf.read_bytes()[:4])
        self.assertGreater(pdf.stat().st_size, 100_000)

    def _album(self) -> dict:
        outputs = _stage(self.report, "album").outputs
        self.assertEqual(1, len(outputs))
        return json.loads(Path(outputs[0]).read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
