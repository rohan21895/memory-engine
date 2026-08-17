"""A missing model host must not be able to produce an analysed-looking library.

THE FAILURE THIS FILE EXISTS TO PREVENT

Ingest succeeds without any model. Perceptual dedupe succeeds without any
model. Classical quality succeeds without any model. So a pipeline whose model
host was never running still produces a library with thumbnails, dates, hashes,
duplicate groups and quality scores -- everything a grid view renders. If the
album stage then builds a book out of it, the output is indistinguishable from
a real one to everybody except the person who receives the printed thing.

So the assertions here are not "an error was logged". They are:

  1. the analysis stage reports BLOCKED, a status distinct from both success
     and "nothing to do",
  2. no record reaches `processing.state == "analyzed"`,
  3. no record carries an embedding or a face summary,
  4. the album stage refuses, naming analysis as the blocker,
  5. NO AlbumSpec and NO PDF exist on disk,
  6. the process exit code is non-zero.

Four ways the host can be missing are covered, because they fail differently
and only one of them is the obvious one: nothing listening, something
listening that is not serving, something serving that does not offer the
models, and something offering models it cannot load.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from support import (  # noqa: E402
    EMBEDDING_MODEL,
    FACE_MODEL,
    PRINT_SAFE_SIZE,
    FakeMlRuntime,
    make_library,
    require_ingest_binary,
    unused_endpoint,
)

from memory_engine_pipeline.runner import run_pipeline  # noqa: E402
from memory_engine_pipeline.stages.base import Settings, StageStatus  # noqa: E402


def _stage(report, name):
    for result in report.results:
        if result.stage == name:
            return result
    raise AssertionError(f"{name} did not run")


class MissingModelHost(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        require_ingest_binary()

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="mep-src-"))
        self.workdir = Path(tempfile.mkdtemp(prefix="mep-work-"))
        self.addCleanup(shutil.rmtree, self.root, True)
        self.addCleanup(shutil.rmtree, self.workdir, True)
        make_library(self.root, 4, size=PRINT_SAFE_SIZE)

    def _run(self, endpoint: str):
        return run_pipeline(
            [self.root],
            self.workdir,
            settings=Settings(
                ml_runtime_endpoint=endpoint, render_print=False, render_video=False
            ),
        )

    def _assert_library_is_not_analysed(self, report):
        analysis = _stage(report, "analysis")
        self.assertEqual(
            StageStatus.BLOCKED, analysis.status,
            f"analysis reported {analysis.status.value}: {analysis.detail}",
        )
        self.assertIn("model host", analysis.detail)

        with sqlite3.connect(self.workdir / "library.db") as connection:
            rows = connection.execute(
                "SELECT processing_state, record_json FROM media"
            ).fetchall()
        self.assertTrue(rows, "ingest should still have imported the photos")
        for state, raw in rows:
            record = json.loads(raw)
            self.assertNotEqual(
                "analyzed", state,
                "a record reached `analyzed` with no model host running",
            )
            self.assertEqual("analyzing", state)
            self.assertIsNone(
                (record.get("content") or {}).get("embedding"),
                "an embedding exists although nothing produced one",
            )
            self.assertIsNone(
                record.get("faces"),
                "a face summary exists although the detector never ran",
            )
            stages = record["processing"]["stages"]
            self.assertEqual("done", stages["classical_quality"]["status"])
            self.assertNotIn("image_embedding", stages)
            self.assertNotIn("face_detection", stages)

        album = _stage(report, "album")
        self.assertEqual(StageStatus.BLOCKED, album.status)
        self.assertEqual("analysis", album.blocked_by)

        self.assertEqual(
            [], sorted((self.workdir / "outputs").rglob("*.json"))
            if (self.workdir / "outputs").exists() else [],
            "an AlbumSpec was written despite the library never being analysed",
        )
        self.assertEqual(
            [], sorted(self.workdir.rglob("*.pdf")),
            "a PDF was written despite the library never being analysed",
        )
        self.assertNotEqual(0, report.exit_code)
        self.assertFalse(report.ok)

    def test_nothing_listening(self):
        report = self._run(unused_endpoint())
        self._assert_library_is_not_analysed(report)
        self.assertIn("unreachable", json.dumps(_stage(report, "analysis").counts))

    def test_listening_but_not_serving(self):
        with FakeMlRuntime(serving=False) as host:
            report = self._run(host.endpoint)
        self._assert_library_is_not_analysed(report)
        self.assertIn("not_serving", json.dumps(_stage(report, "analysis").counts))

    def test_serving_but_the_models_are_not_offered(self):
        """The most convincing failure: a healthy host with the wrong models.

        Health returns serving, so a runner that probed liveness alone would
        proceed and get PROXY_NOT_FOUND or MODEL_NOT_REGISTERED per item -- or,
        worse, empty results it might read as "no faces here".
        """
        with FakeMlRuntime(models=()) as host:
            report = self._run(host.endpoint)
        self._assert_library_is_not_analysed(report)
        counts = json.dumps(_stage(report, "analysis").counts)
        self.assertIn("models_unavailable", counts)
        self.assertIn(EMBEDDING_MODEL, counts)
        self.assertIn(FACE_MODEL, counts)

    def test_serving_but_a_model_will_not_load(self):
        with FakeMlRuntime(unloadable=(FACE_MODEL,)) as host:
            report = self._run(host.endpoint)
        self._assert_library_is_not_analysed(report)
        counts = json.dumps(_stage(report, "analysis").counts)
        self.assertIn("weights_missing", counts)
        self.assertIn(FACE_MODEL, counts)

    def test_the_block_is_recoverable_and_costs_nothing_twice(self):
        """Starting the host and re-running finishes the job it left.

        A gate that could only be satisfied by starting over would be a
        different kind of trap: the operator fixes the real problem and is
        punished with a full re-scan.
        """
        blocked = self._run(unused_endpoint())
        self._assert_library_is_not_analysed(blocked)

        with FakeMlRuntime() as host:
            recovered = run_pipeline(
                [self.root],
                self.workdir,
                settings=Settings(
                    ml_runtime_endpoint=host.endpoint,
                    render_print=False,
                    render_video=False,
                ),
            )
            calls = list(host.infer_calls)

        self.assertEqual(StageStatus.COMPLETED, _stage(recovered, "analysis").status)
        self.assertEqual(
            StageStatus.SKIPPED, _stage(recovered, "ingest").status,
            "recovery must not re-read the source folder",
        )
        with sqlite3.connect(self.workdir / "library.db") as connection:
            states = [
                row[0] for row in connection.execute("SELECT processing_state FROM media")
            ]
        self.assertEqual(["analyzed"] * 4, states)

        # Classical quality already ran during the blocked attempt, so only the
        # two model steps were dispatched -- one batch each.
        self.assertEqual(
            sorted(model for model, _count in calls), sorted([EMBEDDING_MODEL, FACE_MODEL])
        )
        for _model, count in calls:
            self.assertEqual(4, count)
        for media_id, record in self._records():
            self.assertEqual(
                1, record["processing"]["stages"]["classical_quality"]["attempts"],
                f"{media_id[:8]} had its classical quality recomputed after the block",
            )

    def _records(self):
        with sqlite3.connect(self.workdir / "library.db") as connection:
            for media_id, raw in connection.execute(
                "SELECT media_id, record_json FROM media"
            ):
                yield media_id, json.loads(raw)


if __name__ == "__main__":
    unittest.main()
