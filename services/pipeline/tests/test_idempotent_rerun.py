"""Re-running must do no work, and adding one file must do one file's work.

WHY THIS IS THE TEST THAT MATTERS MOST FOR A TERABYTE

Every other property degrades gracefully. This one does not: a runner that
re-reads 500GB because it cannot tell the folder is unchanged is unusable, and
a runner that skips work it should have done loses photos silently. The two
failures look identical from the outside on a small library, which is why they
are asserted here by COUNTING WORK rather than by inspecting results.

WHAT "WORK" IS COUNTED

  * files the Rust worker actually hashed         (`processed` in its report)
  * inference items sent to the model host        (the fake host records them)
  * records whose analysis steps were re-attempted (`attempts` on the record)

The aggregate stages -- dedupe, ranking, album planning -- are deliberately NOT
expected to be proportional to the delta. Their output is a function of the
whole library: adding a photo can change which of a pair is the dedupe primary
and which 24 photos make the book, so recomputing is correct rather than
wasteful. What must hold is that they recompute from stored facts and read no
source files, which is what the ingest counter proves.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from support import (  # noqa: E402
    PRINT_SAFE_SIZE,
    FakeMlRuntime,
    make_library,
    require_ingest_binary,
    write_photo,
)

from memory_engine_pipeline.runner import run_pipeline  # noqa: E402
from memory_engine_pipeline.stages.base import Settings, StageStatus  # noqa: E402

# Big enough that a full-bleed placement on the 300mm vendor product clears the
# DPI floor, so the album stage is genuinely exercised rather than refusing
# every layout. Small fixtures would make every album assertion vacuous.
SMALL = PRINT_SAFE_SIZE


def _stage(report, name):
    for result in report.results:
        if result.stage == name:
            return result
    raise AssertionError(f"{name} did not run")


class IdempotentRerun(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        require_ingest_binary()

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="mep-src-"))
        self.workdir = Path(tempfile.mkdtemp(prefix="mep-work-"))
        self.addCleanup(shutil.rmtree, self.root, True)
        self.addCleanup(shutil.rmtree, self.workdir, True)
        make_library(self.root, 6, size=SMALL)

    def _run(self, host, **overrides):
        settings = Settings(
            ml_runtime_endpoint=host.endpoint,
            render_print=False,
            render_video=False,
            **overrides,
        )
        return run_pipeline([self.root], self.workdir, settings=settings)

    def test_a_second_run_over_an_unchanged_folder_does_nothing(self):
        with FakeMlRuntime() as host:
            first = self._run(host)
            self.assertEqual(StageStatus.COMPLETED, _stage(first, "ingest").status)
            self.assertEqual(6, _stage(first, "ingest").counts["processed"])
            first_infer = list(host.infer_calls)
            self.assertTrue(first_infer, "the first run must actually call the host")

            host.infer_calls.clear()
            second = self._run(host)

        ingest = _stage(second, "ingest")
        self.assertEqual(StageStatus.COMPLETED, ingest.status)
        self.assertIn("unchanged", ingest.detail)
        # Not "hashed zero files" -- the worker was never started at all.
        self.assertNotIn("processed", ingest.counts)

        self.assertEqual(
            StageStatus.COMPLETED, _stage(second, "analysis").status,
            _stage(second, "analysis").detail,
        )
        self.assertEqual(
            [], host.infer_calls,
            "an unchanged library must not send a single item to the model host",
        )
        self.assertEqual(StageStatus.COMPLETED, _stage(second, "ranking").status)

    def test_ids_are_identical_across_runs(self):
        with FakeMlRuntime() as host:
            first = self._run(host)
            first_ids = self._library()
            first_album = _stage(first, "album").outputs
            second = self._run(host)
            second_ids = self._library()
            second_album = _stage(second, "album").outputs

        self.assertEqual(first.run_id, second.run_id)
        self.assertEqual(first_ids, second_ids)
        self.assertEqual(first_album, second_album)
        self.assertEqual(
            _stage(first, "album").job_id, _stage(second, "album").job_id,
            "the album job id is the content address of the plan; it must not drift",
        )
        self.assertEqual(StageStatus.COMPLETED, _stage(second, "album").status)

    def test_adding_one_file_costs_one_file(self):
        with FakeMlRuntime() as host:
            self._run(host)
            before = self._library()
            host.infer_calls.clear()

            write_photo(
                self.root / "IMG_9000.jpg",
                index=9000,
                captured="2026:03:14 11:45:00",
                size=SMALL,
            )
            second = self._run(host)

        ingest = _stage(second, "ingest")
        self.assertEqual(StageStatus.COMPLETED, ingest.status)
        self.assertEqual(
            1, ingest.counts["processed"],
            "a delta scan must hash the added file and nothing else",
        )
        self.assertEqual({"added": 1, "changed": 0, "removed": 0},
                         {key: ingest.counts[key] for key in ("added", "changed", "removed")})

        # One call per model for the one new photo, each carrying only what
        # that photo needs: one image for the embedder and the detector, and
        # one item per face it turned out to contain for the face embedder.
        by_model = [model for model, _count in host.infer_calls]
        self.assertEqual(
            ["siglip2-so400m-384", "scrfd-10g-bnkps", "arcface-buffalo-l"],
            by_model,
            f"expected one call per model, got {host.infer_calls}",
        )
        for model, count in host.infer_calls:
            if model != "arcface-buffalo-l":
                self.assertEqual(
                    1, count, f"{model} was sent more than the one new photo"
                )

        after = self._library()
        self.assertEqual(len(before) + 1, len(after))
        self.assertTrue(set(before).issubset(set(after)),
                        "existing media ids must survive a delta scan unchanged")

        # Nothing already analysed was analysed again.
        for media_id in before:
            record = self._record(media_id)
            for step in ("classical_quality", "image_embedding", "face_detection",
                         "face_embedding"):
                self.assertEqual(
                    1, record["processing"]["stages"][step]["attempts"],
                    f"{step} was re-attempted on an untouched record",
                )

    def test_rescan_re_reads_every_file_and_reaches_the_same_ids(self):
        """The escape hatch for a user who does not trust the stat inventory.

        It must actually re-read -- otherwise it is a no-op with a reassuring
        name -- and it must not do so by naming 6 files as 6 separate source
        roots, which is how a delta scan of a whole drive becomes a JobSpec
        with 300,000 paths in it.
        """
        with FakeMlRuntime() as host:
            self._run(host)
            before = self._library()
            second = self._run(host, rescan=True)

        ingest = _stage(second, "ingest")
        self.assertEqual(StageStatus.COMPLETED, ingest.status, ingest.detail)
        self.assertEqual(6, ingest.counts["processed"], "rescan did not re-read")
        self.assertEqual(0, ingest.counts["resumed_skips"])
        self.assertEqual(
            6, ingest.counts["already_present"],
            "re-reading identical bytes must reproduce identical ids, so every "
            "record should already be in the library",
        )
        self.assertEqual(before, self._library())

    def test_a_removed_file_is_reported_and_never_deleted(self):
        with FakeMlRuntime() as host:
            self._run(host)
            before = self._library()
            (self.root / "IMG_0000.jpg").unlink()
            second = self._run(host)

        ingest = _stage(second, "ingest")
        self.assertEqual(1, ingest.counts["removed"])
        self.assertEqual(
            before, self._library(),
            "a file disappearing from disk must not remove its record: an "
            "unplugged drive is not a deletion",
        )

    # -- helpers ---------------------------------------------------------

    def _library(self) -> list[str]:
        import sqlite3

        with sqlite3.connect(self.workdir / "library.db") as connection:
            return [
                row[0]
                for row in connection.execute("SELECT media_id FROM media ORDER BY media_id")
            ]

    def _record(self, media_id: str) -> dict:
        import sqlite3

        with sqlite3.connect(self.workdir / "library.db") as connection:
            row = connection.execute(
                "SELECT record_json FROM media WHERE media_id = ?", (media_id,)
            ).fetchone()
        return json.loads(row[0])


if __name__ == "__main__":
    unittest.main()
