"""Kill it mid-run; the next run continues rather than restarting.

TWO KINDS OF INTERRUPTION, TESTED TWO WAYS, BECAUSE THEY BREAK DIFFERENTLY

* SIGKILL to the whole process group, during ingest. This is the real thing:
  no unwinding, no flush, the Rust worker dies mid-scan alongside the runner.
  It is the only way to test that what is on disk at the moment of death is
  usable -- an exception-based test proves nothing about durability, because
  Python got to run its `finally` blocks.

* An injected failure during analysis, at a chosen record. Deterministic, so
  the assertion "record 4 was analysed once, not twice" is exact rather than
  dependent on how fast the machine is. Timing a signal to land in the middle
  of a 20ms stage would be a flaky test pretending to be a thorough one.

WHAT "CONTINUED" MEANS HERE, PRECISELY

Not "finished eventually" -- a runner that starts from scratch also finishes
eventually, and on a 3TB library that is the difference between minutes and a
day. So the assertions count REDONE WORK:

    ingest    `resumed_skips > 0` and `processed < total` in the second run.
              The Rust worker reports both, so the claim is measured, not
              inferred from a wall-clock reading.
    analysis  every record's `attempts` is exactly 1. A record analysed twice
              would still look perfect in the finished library; the attempt
              counter is the only place the waste is visible.

AND WHAT "DID NOT CORRUPT" MEANS

The resumed library is compared field-for-field against a library built in one
clean run of the same folder: same media ids, same quality values, same
embeddings, same duplicate assignment. A resumed run that produced subtly
different numbers would pass every "did it finish" check ever written.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from support import (  # noqa: E402
    PACKAGE_ROOT,
    FakeMlRuntime,
    make_library,
    require_ingest_binary,
)

from memory_engine_pipeline.ids import blake3_hex  # noqa: E402
from memory_engine_pipeline.runner import run_pipeline  # noqa: E402
from memory_engine_pipeline.stages.base import Settings, StageStatus  # noqa: E402

SMALL = (900, 700)
PHOTOS = 12
STAGES = ["ingest", "analysis", "ranking"]


def _stage(report, name):
    for result in report.results:
        if result.stage == name:
            return result
    raise AssertionError(f"{name} did not run")


def _fingerprint(database: Path) -> list[tuple]:
    """Everything a resumed run could plausibly get subtly wrong."""
    with sqlite3.connect(database) as connection:
        media = connection.execute(
            "SELECT media_id, processing_state, quality_sharpness, quality_exposure, "
            "face_count, dedupe_group_id, is_dedupe_primary, phash_hex "
            "FROM media ORDER BY media_id"
        ).fetchall()
        vectors = connection.execute(
            "SELECT owner_id, space, dimensions, embedding FROM vector ORDER BY owner_id, space"
        ).fetchall()
    return [tuple(row) for row in media] + [tuple(row) for row in vectors]


class KilledDuringIngest(unittest.TestCase):
    """SIGKILL the process group mid-scan, then run again."""

    @classmethod
    def setUpClass(cls):
        require_ingest_binary()

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="mep-src-"))
        self.workdir = Path(tempfile.mkdtemp(prefix="mep-work-"))
        self.addCleanup(shutil.rmtree, self.root, True)
        self.addCleanup(shutil.rmtree, self.workdir, True)
        make_library(self.root, PHOTOS, size=SMALL)

    def _spawn(self, endpoint: str) -> subprocess.Popen:
        environment = dict(os.environ)
        environment["MEMORY_ENGINE_ML_RUNTIME"] = endpoint
        environment["PYTHONPATH"] = str(PACKAGE_ROOT)
        return subprocess.Popen(
            [
                sys.executable, "-m", "memory_engine_pipeline",
                str(self.root), "--workdir", str(self.workdir),
                "--stages", ",".join(STAGES), "--quiet",
            ],
            cwd=str(PACKAGE_ROOT),
            env=environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            # Its own group, so the SIGKILL reaches the Rust worker too. Killing
            # only the parent would leave the scan running and the test would
            # be measuring a race rather than a resume.
            start_new_session=True,
        )

    def _checkpoint(self) -> dict | None:
        directory = self.workdir / "ingest"
        if not directory.is_dir():
            return None
        for path in sorted(directory.glob("*.json")):
            if path.name.endswith(".request.json"):
                continue
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                # Mid-rename. The next poll will read the finished file.
                continue
        return None

    def test_a_hard_kill_mid_scan_resumes_from_the_worker_cursor(self):
        with FakeMlRuntime() as host:
            process = self._spawn(host.endpoint)
            killed_after = self._kill_when_scan_reaches(process, 3)

            self.assertGreaterEqual(killed_after, 3)
            self.assertLess(
                killed_after, PHOTOS,
                "the kill landed after the scan had already finished; the test "
                "would prove nothing",
            )

            second = run_pipeline(
                [self.root], self.workdir, stages=STAGES,
                settings=Settings(
                    ml_runtime_endpoint=host.endpoint,
                    render_print=False, render_video=False,
                ),
            )

        ingest = _stage(second, "ingest")
        self.assertEqual(StageStatus.COMPLETED, ingest.status, ingest.detail)
        self.assertGreater(
            ingest.counts["resumed_skips"], 0,
            "the second run re-walked from the beginning instead of resuming",
        )
        self.assertLess(
            ingest.counts["processed"], PHOTOS,
            "the second run re-hashed files the first run had already finished",
        )
        self.assertEqual(
            PHOTOS, ingest.counts["resumed_skips"] + ingest.counts["processed"],
            "skipped plus processed must account for every file exactly once",
        )

        with sqlite3.connect(self.workdir / "library.db") as connection:
            count = connection.execute("SELECT COUNT(*) FROM media").fetchone()[0]
        self.assertEqual(PHOTOS, count, "the resumed scan lost or duplicated a file")

        self.assertEqual(
            StageStatus.COMPLETED, _stage(second, "analysis").status,
            _stage(second, "analysis").detail,
        )
        self.assertEqual(self._clean_fingerprint(), _fingerprint(self.workdir / "library.db"))

    def _kill_when_scan_reaches(self, process: subprocess.Popen, files: int) -> int:
        """Poll the worker's own checkpoint and kill the group at `files`.

        The checkpoint is rewritten (atomically) after every single file, which
        makes this precise rather than a sleep-and-hope. If the scan somehow
        finishes first, the caller's assertion catches it.
        """
        deadline = time.monotonic() + 60.0
        seen = 0
        while time.monotonic() < deadline:
            snapshot = self._checkpoint()
            progress = ((snapshot or {}).get("state") or {}).get("progress") or {}
            seen = int(progress.get("units_done") or 0)
            if seen >= files:
                break
            if process.poll() is not None:
                break
            time.sleep(0.01)
        if process.poll() is None:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        process.wait(timeout=30)
        # Re-read after death: the last durable checkpoint is what the next run
        # will actually resume from, and it may be one file ahead of the last
        # value this loop observed.
        snapshot = self._checkpoint()
        progress = ((snapshot or {}).get("state") or {}).get("progress") or {}
        return int(progress.get("units_done") or seen)

    def _clean_fingerprint(self) -> list[tuple]:
        """The same folder, imported once, without interruption."""
        reference = Path(tempfile.mkdtemp(prefix="mep-ref-"))
        self.addCleanup(shutil.rmtree, reference, True)
        with FakeMlRuntime() as host:
            run_pipeline(
                [self.root], reference, stages=STAGES,
                settings=Settings(
                    ml_runtime_endpoint=host.endpoint,
                    render_print=False, render_video=False,
                ),
            )
        return _fingerprint(reference / "library.db")


class _FailAfter:
    """A media-db proxy that stops writing records after N of them.

    Stands in for a crash at a chosen point. Everything already committed stays
    committed, which is exactly the state a kill leaves behind and exactly what
    the next run has to pick up from.
    """

    def __init__(self, database, limit: int) -> None:
        self._database = database
        self._limit = limit
        self.writes = 0

    def __getattr__(self, name):
        return getattr(self._database, name)

    def put_media(self, record):
        if self.writes >= self._limit:
            raise RuntimeError("simulated crash")
        self.writes += 1
        return self._database.put_media(record)


class _DropAfter:
    """A corrupt storage boundary that acknowledges records without writing."""

    def __init__(self, database, limit: int) -> None:
        self._database = database
        self._limit = limit
        self.writes = 0

    def __getattr__(self, name):
        return getattr(self._database, name)

    def put_media(self, record):
        self.writes += 1
        if self.writes > self._limit:
            return record["media_id"]
        return self._database.put_media(record)


class InterruptedIngestImport(unittest.TestCase):
    """The full manifest survives until every MediaRecord is durable."""

    @classmethod
    def setUpClass(cls):
        require_ingest_binary()

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="mep-src-"))
        self.workdir = Path(tempfile.mkdtemp(prefix="mep-work-"))
        self.addCleanup(shutil.rmtree, self.root, True)
        self.addCleanup(shutil.rmtree, self.workdir, True)
        make_library(self.root, PHOTOS, size=SMALL)

    def _worker_checkpoint(self) -> dict:
        checkpoints = sorted(
            path for path in (self.workdir / "ingest").glob("*.json")
            if not path.name.endswith(".request.json")
        )
        self.assertEqual(1, len(checkpoints))
        return json.loads(checkpoints[0].read_text(encoding="utf-8"))

    def assert_worker_outputs_are_bound_to_artifacts(self, worker: dict) -> None:
        output_ids = set()
        for output in worker["outputs"]:
            artifact = Path(output["path"]).read_bytes()
            self.assertEqual(len(artifact), output["byte_size"])
            self.assertEqual(blake3_hex(artifact), output["id"])
            output_ids.add(output["id"])
        self.assertEqual(PHOTOS, len(output_ids))
        self.assertEqual(
            output_ids,
            set(worker["checkpoint"]["partial_output_ids"]),
        )

    def test_completed_scan_stores_records_not_a_second_full_manifest(self):
        from memory_engine_media_db import Database

        result = run_pipeline(
            [self.root], self.workdir, stages=["ingest"],
            settings=Settings(render_print=False, render_video=False),
        )
        self.assertEqual(StageStatus.COMPLETED, _stage(result, "ingest").status)

        worker = self._worker_checkpoint()
        self.assertEqual(PHOTOS, len(worker["outputs"]))
        self.assertEqual(PHOTOS, len(worker["checkpoint"]["partial_output_ids"]))
        self.assert_worker_outputs_are_bound_to_artifacts(worker)

        with Database.open(self.workdir / "library.db") as database:
            self.assertEqual(PHOTOS, database.count_media())
            completed = [
                job for job in database.jobs_by_status("completed")
                if job["job_type"] == "scan_source"
            ]
        self.assertEqual(1, len(completed))
        self.assertEqual([], completed[0]["outputs"])
        self.assertEqual([], completed[0]["checkpoint"]["partial_output_ids"])

    def test_a_crash_during_record_import_retains_and_reuses_the_full_checkpoint(self):
        from memory_engine_media_db import Database

        database = Database.open(self.workdir / "library.db")
        guard = _FailAfter(database, limit=3)
        interrupted = run_pipeline(
            [self.root], self.workdir, stages=["ingest"],
            settings=Settings(render_print=False, render_video=False),
            database=guard,
        )
        running = [
            job for job in database.jobs_by_status("running")
            if job["job_type"] == "scan_source"
        ]
        database.close()

        self.assertEqual(StageStatus.FAILED, _stage(interrupted, "ingest").status)
        self.assertIn("simulated crash", _stage(interrupted, "ingest").detail)
        self.assertEqual(1, len(running))
        self.assertEqual([], running[0]["outputs"])
        self.assertEqual([], running[0]["checkpoint"]["partial_output_ids"])

        checkpoint = self._worker_checkpoint()
        self.assertEqual("completed", checkpoint["state"]["status"])
        self.assertEqual(PHOTOS, len(checkpoint["outputs"]))
        self.assertEqual(PHOTOS, len(checkpoint["checkpoint"]["partial_output_ids"]))
        self.assert_worker_outputs_are_bound_to_artifacts(checkpoint)
        self.assertTrue(
            all(Path(output["path"]).is_file() for output in checkpoint["outputs"]),
            "the retry manifest points at missing record artifacts",
        )

        resumed = run_pipeline(
            [self.root], self.workdir, stages=["ingest"],
            settings=Settings(render_print=False, render_video=False),
        )
        ingest = _stage(resumed, "ingest")
        self.assertEqual(StageStatus.COMPLETED, ingest.status, ingest.detail)
        self.assertEqual((running[0]["job_id"],), resumed.reclaimed_jobs)
        self.assertEqual(PHOTOS - 3, ingest.counts["loaded"])
        self.assertEqual(3, ingest.counts["already_present"])

        with Database.open(self.workdir / "library.db") as database:
            self.assertEqual(PHOTOS, database.count_media())
            completed = [
                job for job in database.jobs_by_status("completed")
                if job["job_type"] == "scan_source"
            ]
        self.assertEqual(1, len(completed))
        self.assertEqual([], completed[0]["outputs"])
        self.assertEqual([], completed[0]["checkpoint"]["partial_output_ids"])

    def test_acknowledged_but_missing_records_cannot_complete_the_scan(self):
        from memory_engine_media_db import Database

        database = Database.open(self.workdir / "library.db")
        guard = _DropAfter(database, limit=3)
        incomplete = run_pipeline(
            [self.root], self.workdir, stages=["ingest"],
            settings=Settings(render_print=False, render_video=False),
            database=guard,
        )
        self.assertEqual(3, database.count_media())
        database.close()

        ingest = _stage(incomplete, "ingest")
        self.assertEqual(StageStatus.FAILED, ingest.status)
        self.assertEqual(PHOTOS - 3, ingest.counts["missing_from_media_db"])
        self.assertIn("not durably imported", ingest.detail)
        self.assertFalse((self.workdir / "inventory.json").exists())
        checkpoint = self._worker_checkpoint()
        self.assertEqual(PHOTOS, len(checkpoint["outputs"]))

        resumed = run_pipeline(
            [self.root], self.workdir, stages=["ingest"],
            settings=Settings(render_print=False, render_video=False),
        )
        self.assertEqual(StageStatus.COMPLETED, _stage(resumed, "ingest").status)
        with Database.open(self.workdir / "library.db") as database:
            self.assertEqual(PHOTOS, database.count_media())


class KilledDuringAnalysis(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        require_ingest_binary()

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="mep-src-"))
        self.workdir = Path(tempfile.mkdtemp(prefix="mep-work-"))
        self.addCleanup(shutil.rmtree, self.root, True)
        self.addCleanup(shutil.rmtree, self.workdir, True)
        make_library(self.root, PHOTOS, size=SMALL)

    def test_analysis_continues_from_the_records_rather_than_restarting(self):
        from memory_engine_media_db import Database

        settings = Settings(render_print=False, render_video=False)
        with FakeMlRuntime() as host:
            settings = Settings(
                ml_runtime_endpoint=host.endpoint, render_print=False, render_video=False
            )
            # First: ingest only, cleanly, so the crash lands in analysis.
            run_pipeline([self.root], self.workdir, stages=["ingest"], settings=settings)

            database = Database.open(self.workdir / "library.db")
            # 12 ingest loads are already done, so the counter starts at zero
            # here: this instance only sees the analysis writes.
            guard = _FailAfter(database, limit=5)
            interrupted = run_pipeline(
                [self.root], self.workdir, stages=["analysis"],
                settings=settings, database=guard,
            )
            database.close()

            self.assertEqual(StageStatus.FAILED, _stage(interrupted, "analysis").status)
            partial = self._states()
            self.assertEqual(
                5, sum(1 for _id, record in partial
                       if record["processing"]["stages"].get("classical_quality", {}).get("status")
                       == "done"),
                "the injected crash did not land where the test intended",
            )

            host.infer_calls.clear()
            resumed = run_pipeline(
                [self.root], self.workdir, stages=STAGES, settings=settings
            )
            calls = list(host.infer_calls)

        self.assertEqual(
            StageStatus.COMPLETED, _stage(resumed, "analysis").status,
            _stage(resumed, "analysis").detail,
        )
        counts = _stage(resumed, "analysis").counts
        self.assertEqual(
            PHOTOS - 5, counts["classical_quality"]["done"],
            "classical quality was recomputed for records that already had it",
        )
        for media_id, record in self._states():
            stages = record["processing"]["stages"]
            self.assertEqual("analyzed", record["processing"]["state"])
            for step in ("classical_quality", "image_embedding", "face_detection",
                         "face_embedding"):
                self.assertEqual(
                    1, stages[step]["attempts"],
                    f"{step} ran twice on {media_id[:8]} across the interruption",
                )

        # Every photo needed all three model steps -- the crash was before any
        # of them -- so the host saw twelve items for the embedder and twelve
        # for the detector, and not one more. The face embedder is counted in
        # FACES rather than photos, so it gets its own assertion.
        per_model: dict[str, int] = {}
        for model, count in calls:
            per_model[model] = per_model.get(model, 0) + count
        self.assertEqual(PHOTOS, per_model["siglip2-so400m-384"], f"calls were {calls}")
        self.assertEqual(PHOTOS, per_model["scrfd-10g-bnkps"], f"calls were {calls}")
        faces = sum(
            len(record["faces"]["face_ids"]) for _media_id, record in self._states()
        )
        self.assertEqual(
            faces, per_model["arcface-buffalo-l"],
            "every detected face is embedded exactly once across the interruption",
        )

    def test_a_job_left_running_by_a_kill_is_reclaimed_not_abandoned(self):
        """A crashed `running` job must become `pending`, or its queue is dead.

        Without reclamation the next run sees "already running", declines to
        touch it, and reports success over a job nobody is executing -- which
        reads exactly like a finished import.
        """
        from memory_engine_media_db import Database

        with FakeMlRuntime() as host:
            settings = Settings(
                ml_runtime_endpoint=host.endpoint, render_print=False, render_video=False
            )
            run_pipeline([self.root], self.workdir, stages=["ingest"], settings=settings)

            database = Database.open(self.workdir / "library.db")
            guard = _FailAfter(database, limit=2)
            run_pipeline(
                [self.root], self.workdir, stages=["analysis"],
                settings=settings, database=guard,
            )
            stranded = [
                job["job_id"] for job in database.jobs_by_status("running")
            ]
            database.close()
            self.assertTrue(stranded, "the interrupted analysis job should be left running")

            resumed = run_pipeline(
                [self.root], self.workdir, stages=STAGES, settings=settings
            )

        self.assertEqual(set(stranded), set(resumed.reclaimed_jobs))
        self.assertEqual(StageStatus.COMPLETED, _stage(resumed, "analysis").status)

    def _states(self):
        with sqlite3.connect(self.workdir / "library.db") as connection:
            return [
                (media_id, json.loads(raw))
                for media_id, raw in connection.execute(
                    "SELECT media_id, record_json FROM media ORDER BY media_id"
                )
            ]


if __name__ == "__main__":
    unittest.main()
