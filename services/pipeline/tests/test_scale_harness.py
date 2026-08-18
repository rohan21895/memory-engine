"""Fast invariants for the scale harness, plus a real small public-API pass."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path

from support import PACKAGE_ROOT  # noqa: F401,E402 - also installs package import path

from memory_engine_pipeline.scale_harness import (  # noqa: E402
    GENERATION_STATE,
    REPORT_NAME,
    ScaleConfig,
    ScaleHarnessError,
    _stage_breakdown,
    generate_library,
    run_scale_harness,
    synthetic_item,
    synthetic_relative_path,
    verify_generated_library,
)
from memory_engine_pipeline.runner import run_pipeline  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[3]
INGEST_BINARY = REPO_ROOT / "workers/ingest/target/release/memory-engine-ingest"


class SyntheticGenerator(unittest.TestCase):
    def setUp(self):
        self.scratch = Path(tempfile.mkdtemp(prefix="memory-scale-test-"))
        self.addCleanup(shutil.rmtree, self.scratch, True)

    def _generate(self, name: str, **overrides):
        root = self.scratch / name
        return root, generate_library(
            root / "source",
            root / GENERATION_STATE,
            target_items=overrides.pop("target_items", 41),
            seed=overrides.pop("seed", 77),
            duplicate_group_size=overrides.pop("duplicate_group_size", 4),
            **overrides,
        )

    def test_same_seed_is_byte_identical_and_every_content_id_is_unique(self):
        first, first_result = self._generate("one")
        second, second_result = self._generate("two")

        def inventory(root: Path):
            return {
                path.relative_to(root / "source"): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in (root / "source").rglob("scale*")
            }

        one = inventory(first)
        two = inventory(second)
        self.assertEqual(one, two, "same seed and index must produce identical bytes")
        self.assertEqual(41, len(one))
        self.assertEqual(
            41,
            len(set(one.values())),
            "visually identical burst members still need distinct content-addressed ids",
        )
        self.assertEqual({"bmp": 18, "png": 20, "mp4": 3}, first_result.format_counts)
        self.assertEqual(first_result.format_counts, second_result.format_counts)

    def test_resume_never_rewrites_finished_files(self):
        root, partial = self._generate("resume", target_items=12, item_budget=5)
        self.assertFalse(partial.complete)
        self.assertEqual(5, partial.files_present)
        first_path = root / "source" / synthetic_relative_path(
            1, synthetic_item(1, seed=77, duplicate_group_size=4)[0]
        )
        before_bytes = first_path.read_bytes()
        before_mtime = first_path.stat().st_mtime_ns
        time.sleep(0.002)

        finished = generate_library(
            root / "source",
            root / GENERATION_STATE,
            target_items=12,
            seed=77,
            duplicate_group_size=4,
        )
        self.assertTrue(finished.complete)
        self.assertEqual(7, finished.created_this_attempt)
        self.assertEqual(before_bytes, first_path.read_bytes())
        self.assertEqual(before_mtime, first_path.stat().st_mtime_ns)

    def test_completed_generator_verification_treats_missing_as_failure(self):
        root, complete = self._generate("verify", target_items=6)
        self.assertTrue(complete.complete)
        extension, _payload = synthetic_item(4, seed=77, duplicate_group_size=4)
        missing = root / "source" / synthetic_relative_path(4, extension)
        missing.unlink()
        with self.assertRaisesRegex(ScaleHarnessError, "absent"):
            verify_generated_library(
                root / "source",
                root / GENERATION_STATE,
                target_items=6,
                seed=77,
                duplicate_group_size=4,
            )

    def test_crash_tail_is_verified_and_reused_but_corruption_fails_closed(self):
        root, partial = self._generate("tail", target_items=8, item_budget=3)
        self.assertFalse(partial.complete)
        state_path = root / GENERATION_STATE
        state = json.loads(state_path.read_text(encoding="utf-8"))

        # Simulate SIGKILL after the file rename and before its cursor update.
        extension, payload = synthetic_item(3, seed=77, duplicate_group_size=4)
        tail = root / "source" / synthetic_relative_path(3, extension)
        tail.parent.mkdir(parents=True, exist_ok=True)
        tail.write_bytes(payload)
        state["next_index"] = 3
        state_path.write_text(json.dumps(state), encoding="utf-8")
        resumed = generate_library(
            root / "source",
            state_path,
            target_items=8,
            seed=77,
            duplicate_group_size=4,
        )
        self.assertTrue(resumed.complete)
        self.assertEqual(1, resumed.reused_uncheckpointed)

        # Rewind once more, but put different bytes at the expected path.  A
        # benchmark must not overwrite the discrepancy and report a clean run.
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["next_index"] = 7
        state_path.write_text(json.dumps(state), encoding="utf-8")
        extension, _payload = synthetic_item(7, seed=77, duplicate_group_size=4)
        corrupt = root / "source" / synthetic_relative_path(7, extension)
        corrupt.write_bytes(b"not the generated item")
        with self.assertRaisesRegex(ScaleHarnessError, "refusing to overwrite"):
            generate_library(
                root / "source",
                state_path,
                target_items=8,
                seed=77,
                duplicate_group_size=4,
            )


class ReportSemantics(unittest.TestCase):
    def setUp(self):
        self.scratch = Path(tempfile.mkdtemp(prefix="memory-scale-report-"))
        self.addCleanup(shutil.rmtree, self.scratch, True)

    def test_controlled_stop_is_incomplete_and_nonzero_not_a_small_pass(self):
        called = []

        def pipeline_must_not_run(*args, **kwargs):
            called.append((args, kwargs))
            raise AssertionError("ingest must not run over a partial corpus")

        code = run_scale_harness(
            ScaleConfig(root=self.scratch, steps=(9,), generation_budget=3),
            repo_root=REPO_ROOT,
            pipeline_runner=pipeline_must_not_run,
        )
        report = json.loads((self.scratch / REPORT_NAME).read_text(encoding="utf-8"))
        self.assertEqual(1, code)
        self.assertEqual("incomplete", report["status"])
        self.assertFalse(report["ok"])
        self.assertEqual(1, report["exit_code"])
        self.assertEqual("incomplete", report["steps"][0]["phases"]["generation"]["status"])
        self.assertNotIn("ingest", report["steps"][0]["phases"])
        self.assertEqual([], called)

    def test_nonempty_unowned_destination_is_refused_without_touching_it(self):
        marker = self.scratch / "user-file.jpg"
        marker.write_bytes(b"leave me alone")
        with self.assertRaisesRegex(ScaleHarnessError, "unowned"):
            run_scale_harness(ScaleConfig(root=self.scratch, steps=(2,)))
        self.assertEqual(b"leave me alone", marker.read_bytes())

    def test_ingest_report_separates_scan_storage_and_job_finalization(self):
        events = self.scratch / "events.jsonl"
        rows = [
            {"at": "2026-01-01T00:00:00.000Z", "stage": "ingest", "kind": "stage_start"},
            {
                "at": "2026-01-01T00:00:02.000Z", "stage": "ingest", "kind": "progress",
                "message": "scanning local source", "units_done": 5000.0,
                "units_total": 5000.0,
            },
            {
                "at": "2026-01-01T00:00:05.000Z", "stage": "ingest", "kind": "progress",
                "message": "stored",
            },
            {"at": "2026-01-01T00:00:17.000Z", "stage": "ingest", "kind": "stage_done"},
        ]
        events.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
        breakdown = _stage_breakdown(events, "ingest")
        self.assertEqual(
            {
                "complete": True,
                "scan_worker_seconds": 2.0,
                "media_db_storage_seconds": 3.0,
                "job_finalization_seconds": 12.0,
                "event_interval_seconds": 17.0,
            },
            breakdown,
        )


class PublicApiIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not INGEST_BINARY.is_file():
            try:
                build = subprocess.run(
                    [
                        "cargo",
                        "build",
                        "--release",
                        "--manifest-path",
                        str(REPO_ROOT / "workers/ingest/Cargo.toml"),
                    ],
                    cwd=REPO_ROOT,
                    capture_output=True,
                    text=True,
                    check=False,
                )
            except OSError as error:
                raise RuntimeError(
                    "scale integration could not start the required release ingest build"
                ) from error
            if build.returncode != 0:
                raise RuntimeError(
                    "scale integration could not build the required release ingest worker: "
                    f"{build.stderr.strip() or build.stdout.strip()}"
                )
        if not INGEST_BINARY.is_file():
            raise RuntimeError(
                "cargo reported a successful release build but the release ingest "
                f"artifact is absent at {INGEST_BINARY}"
            )

    def setUp(self):
        self.scratch = Path(tempfile.mkdtemp(prefix="memory-scale-integration-"))
        self.addCleanup(shutil.rmtree, self.scratch, True)

    def test_real_ingest_media_db_dedupe_and_search_are_measured(self):
        config = ScaleConfig(
            root=self.scratch, steps=(24,), seed=11, rss_poll_seconds=0.02
        )
        code = run_scale_harness(config, repo_root=REPO_ROOT)
        report = json.loads((self.scratch / REPORT_NAME).read_text(encoding="utf-8"))
        self.assertEqual(0, code, report.get("error"))
        self.assertTrue(report["ok"])
        step = report["steps"][0]
        self.assertEqual(24, step["media_db_count"])
        self.assertEqual("completed", step["phases"]["ingest"]["status"])
        self.assertEqual("completed", step["phases"]["dedupe"]["status"])
        self.assertGreater(step["phases"]["dedupe"]["counts"]["duplicate_groups"], 0)
        self.assertGreater(step["phases"]["search"]["matches"], 0)
        self.assertGreater(step["peak_rss_bytes"], 0)
        self.assertGreater(step["measured_wall_seconds"], 0)
        self.assertGreater(step["end_to_end_items_per_second"], 0)
        breakdown = step["phases"]["ingest"]["breakdown"]
        self.assertTrue(breakdown["complete"])
        self.assertGreater(breakdown["scan_worker_seconds"], 0)
        self.assertGreaterEqual(breakdown["media_db_storage_seconds"], 0)
        self.assertGreater(breakdown["job_finalization_seconds"], 0)

        # A green replay is earned by checking artifacts, not by trusting this
        # report's previous status.
        self.assertEqual(0, run_scale_harness(config, repo_root=REPO_ROOT))
        replayed = json.loads((self.scratch / REPORT_NAME).read_text(encoding="utf-8"))
        self.assertEqual("completed", replayed["steps"][0]["replay_verification"]["status"])
        self.assertEqual(24, replayed["steps"][0]["replay_verification"]["media_db_count"])

    def test_completed_report_with_deleted_database_replays_nonzero(self):
        config = ScaleConfig(root=self.scratch, steps=(8,), seed=19, rss_poll_seconds=0.02)
        self.assertEqual(0, run_scale_harness(config, repo_root=REPO_ROOT))
        database = self.scratch / "steps" / "8" / "pipeline" / "library.db"
        database.unlink()

        code = run_scale_harness(config, repo_root=REPO_ROOT)
        report = json.loads((self.scratch / REPORT_NAME).read_text(encoding="utf-8"))
        self.assertEqual(2, code)
        self.assertFalse(report["ok"])
        self.assertEqual("failed", report["status"])
        self.assertIn("missing", report["error"]["message"])

    def test_first_run_refuses_ranking_counts_that_disagree_with_media_db(self):
        def lying_ranking(*args, **kwargs):
            report = run_pipeline(*args, **kwargs)
            if kwargs.get("stages") == ["ranking"]:
                results = tuple(
                    replace(
                        result,
                        counts={**dict(result.counts), "duplicate_groups": 9999},
                    ) if result.stage == "ranking" else result
                    for result in report.results
                )
                return replace(report, results=results)
            return report

        code = run_scale_harness(
            ScaleConfig(root=self.scratch, steps=(8,), seed=23, rss_poll_seconds=0.02),
            repo_root=REPO_ROOT,
            pipeline_runner=lying_ranking,
        )
        report = json.loads((self.scratch / REPORT_NAME).read_text(encoding="utf-8"))
        self.assertEqual(2, code)
        self.assertEqual("failed", report["status"])
        self.assertFalse(report["ok"])
        self.assertIn("ranking reported duplicate_groups=9999", report["error"]["message"])
        self.assertIn("media-db persisted duplicate_groups=2", report["error"]["message"])


if __name__ == "__main__":
    unittest.main()
