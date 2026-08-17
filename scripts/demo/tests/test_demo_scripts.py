"""Tests for the demo library generator and the demo runner.

Two things are guarded here, because both have already gone wrong:

  * the generator planning a library that does not match what it writes -- two
    events sharing a folder generated the same filenames and 27 stills were
    silently overwritten, with the only symptom a file count nobody read
  * the runner rendering a stage that did not run as a stage that passed

Nothing here needs FFmpeg, so it runs in CI without a media toolchain. The
video path is covered by generating with --no-video and asserting the manifest
says so.

    python3 -m unittest discover -s scripts/demo/tests
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

DEMO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(DEMO))

import run_demo  # noqa: E402

try:
    import make_library
except ImportError as error:  # pragma: no cover - dependency guard
    make_library = None
    MAKE_LIBRARY_IMPORT_ERROR = str(error)


def requires_make_library(test):
    return unittest.skipIf(
        make_library is None,
        # Never a bare skip: a skip whose reason is not printed is how a
        # missing dependency reads as a passing suite.
        f"make_library will not import ({globals().get('MAKE_LIBRARY_IMPORT_ERROR')}); "
        "install Pillow and numpy",
    )(test)


# ---------------------------------------------------------------------------
# The generator's plan
# ---------------------------------------------------------------------------


@requires_make_library
class PlanTests(unittest.TestCase):
    def test_event_codes_are_unique(self) -> None:
        codes = [event.code for event in make_library.EVENTS]
        self.assertEqual(len(codes), len(set(codes)), f"duplicate Event.code in {codes}")

    def test_planned_paths_are_unique_at_every_size(self) -> None:
        # The collision only appeared at some sizes, because it depended on how
        # far the filler round-robin had advanced in each event.
        for total in (22, 23, 40, 97, 200, 401):
            with self.subTest(stills=total):
                planned = make_library.plan_stills(7, total)
                paths = [still.relpath for still in planned]
                self.assertEqual(len(paths), total)
                self.assertEqual(len(paths), len(set(paths)))

    def test_plan_is_deterministic_for_a_seed(self) -> None:
        first = [(s.relpath, s.captured_local, s.orientation, s.burst_id)
                 for s in make_library.plan_stills(11, 60)]
        second = [(s.relpath, s.captured_local, s.orientation, s.burst_id)
                  for s in make_library.plan_stills(11, 60)]
        self.assertEqual(first, second)
        other = [s.relpath for s in make_library.plan_stills(12, 60)]
        self.assertEqual(len(set(other)), 60)

    def test_every_edge_case_is_planned(self) -> None:
        tags = {tag for still in make_library.plan_stills(3, 200) for tag in still.tags}
        for required in ("near_duplicate_burst", "no_exif_date", "no_filename_date",
                         "filename_dated", "exif_mtime_disagreement", "orientation_6"):
            self.assertIn(required, tags)

    def test_a_burst_of_five_exists(self) -> None:
        planned = make_library.plan_stills(3, 200)
        sizes: dict[str, int] = {}
        for still in planned:
            if still.burst_id:
                sizes[still.burst_id] = sizes.get(still.burst_id, 0) + 1
        self.assertIn(5, sizes.values(), f"no burst of five in {sizes}")

    def test_exif_and_mtime_actually_disagree(self) -> None:
        # A "disagreement" case where the two dates happen to match tests
        # nothing, and would pass silently.
        for still in make_library.plan_stills(3, 200):
            if "exif_mtime_disagreement" in still.tags:
                self.assertIsNotNone(still.captured_local)
                self.assertNotEqual(
                    still.captured_local,
                    make_library.exif_string(still.mtime_dt),
                )
                self.assertGreater(
                    abs((still.mtime_dt - still.event.start).days), 300
                )

    def test_stills_below_the_edge_case_floor_is_refused(self) -> None:
        with self.assertRaises(SystemExit):
            make_library.plan_stills(3, 4)


# ---------------------------------------------------------------------------
# GoPro naming, checked against the convention in gopro.rs
# ---------------------------------------------------------------------------


def gopro_index(filename: str) -> tuple[str, str, int] | None:
    """An independent re-implementation of `gopro.rs::parse_filename`.

    Deliberately not shared with the generator: a test that derives its
    expectation from the code under test only proves the code agrees with
    itself. The rules are read off workers/ingest/src/gopro.rs --
      GOPR<RRRR>  -> legacy, index 0
      GP<CC><RRRR>-> legacy, index CC   (so GP01 follows GOPR)
      GX/GH<CC><RRRR> -> that family, index CC - 1
    with chapter 00 rejected and an 8-character stem required.
    """
    stem, _, extension = filename.rpartition(".")
    if extension.lower() not in ("mp4", "lrv"):
        return None
    stem = stem.upper()
    if len(stem) != 8:
        return None
    if stem.startswith("GOPR") and stem[4:].isdigit():
        return ("legacy", stem[4:], 0)
    if stem[:2] in ("GH", "GX") and stem[2:].isdigit():
        chapter = int(stem[2:4])
        return None if chapter == 0 else (stem[:2].lower(), stem[4:], chapter - 1)
    if stem[:2] == "GP" and stem[2:].isdigit():
        chapter = int(stem[2:4])
        return None if chapter == 0 else ("legacy", stem[4:], chapter)
    return None


@requires_make_library
class GoProNamingTests(unittest.TestCase):
    def test_generated_names_parse_to_a_contiguous_span_from_zero(self) -> None:
        clips = make_library.plan_clips(3, 10)
        spans: dict[tuple[str, str], list[int]] = {}
        for clip in clips:
            parsed = gopro_index(Path(clip.relpath).name)
            if parsed is None:
                continue
            family, recording, index = parsed
            spans.setdefault((family, recording), []).append(index)

        self.assertEqual(len(spans), 2, f"expected a GX span and a legacy span, got {spans}")
        for key, indexes in spans.items():
            with self.subTest(span=key):
                self.assertEqual(sorted(indexes), list(range(len(indexes))),
                                 f"{key} is not contiguous from 0: {sorted(indexes)}")
                self.assertGreaterEqual(len(indexes), 2)

    def test_the_legacy_family_uses_the_other_index_convention(self) -> None:
        self.assertEqual(gopro_index("GOPR0044.MP4"), ("legacy", "0044", 0))
        self.assertEqual(gopro_index("GP010044.MP4"), ("legacy", "0044", 1))
        self.assertEqual(gopro_index("GX010012.MP4"), ("gx", "0012", 0))
        self.assertEqual(gopro_index("GX020012.MP4"), ("gx", "0012", 1))
        self.assertIsNone(gopro_index("GX000012.MP4"))
        self.assertIsNone(gopro_index("IMG_0001.JPG"))

    def test_span_members_are_given_different_content(self) -> None:
        # Identical chapters collide on BLAKE3 and gopro.rs then skips the
        # assembly, so a library of identical chapters tests nothing.
        clips = make_library.plan_clips(3, 10)
        gopro = [c for c in clips if gopro_index(Path(c.relpath).name)]
        keys = [c.scene_key for c in gopro]
        self.assertEqual(len(keys), len(set(keys)))


# ---------------------------------------------------------------------------
# Destination guards and manifest reconciliation
# ---------------------------------------------------------------------------


@requires_make_library
class DestinationTests(unittest.TestCase):
    def test_real_photo_locations_are_refused(self) -> None:
        for name in ("Pictures", "Downloads", "DCIM"):
            with self.subTest(name=name):
                self.assertIsNotNone(
                    run_demo.real_media_location(Path.home() / name / "somewhere")
                )
        self.assertIsNotNone(
            run_demo.real_media_location(Path("/tmp/Family.photoslibrary/x"))
        )
        self.assertIsNone(run_demo.real_media_location(Path("/tmp/demo-library")))

    def test_a_foreign_non_empty_directory_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            target = Path(raw) / "existing"
            target.mkdir()
            (target / "something-precious.jpg").write_bytes(b"x")
            with self.assertRaises(SystemExit):
                make_library.check_destination(target, force=False)
            with self.assertRaises(SystemExit):
                make_library.check_destination(target, force=True)

    def test_a_generated_directory_needs_force(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            target = Path(raw) / "generated"
            target.mkdir()
            (target / make_library.MANIFEST_NAME).write_text("{}", encoding="utf-8")
            (target / "photo.jpg").write_bytes(b"x")
            with self.assertRaises(SystemExit):
                make_library.check_destination(target, force=False)
            make_library.check_destination(target, force=True)


@requires_make_library
class ReconcileTests(unittest.TestCase):
    def _library(self, root: Path) -> list[dict]:
        (root / "a.jpg").write_bytes(b"aaaa")
        (root / "b.jpg").write_bytes(b"bbbb")
        return [
            {"relpath": "a.jpg", "blake3": make_library.blake3_hex(root / "a.jpg")},
            {"relpath": "b.jpg", "blake3": make_library.blake3_hex(root / "b.jpg")},
        ]

    def test_a_consistent_library_passes(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            make_library.reconcile(root, self._library(root))

    def test_a_missing_file_is_caught(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            files = self._library(root)
            (root / "b.jpg").unlink()
            with self.assertRaises(SystemExit):
                make_library.reconcile(root, files)

    def test_an_undeclared_file_is_caught(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            files = self._library(root)
            (root / "c.jpg").write_bytes(b"cccc")
            with self.assertRaises(SystemExit):
                make_library.reconcile(root, files)

    def test_a_rewritten_file_is_caught(self) -> None:
        # This is the collision case: the path exists and the manifest lists
        # it, but the bytes belong to whatever was written last.
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            files = self._library(root)
            (root / "a.jpg").write_bytes(b"overwritten")
            with self.assertRaises(SystemExit):
                make_library.reconcile(root, files)


# ---------------------------------------------------------------------------
# End to end, without a media toolchain
# ---------------------------------------------------------------------------


@requires_make_library
class NoVideoGenerationTests(unittest.TestCase):
    def test_a_stills_only_library_reports_itself_incomplete(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            out = Path(raw) / "library"
            code = make_library.main(
                ["--out", str(out), "--stills", "24", "--no-video", "--quiet"]
            )
            # Exit 2, not 0: a library with no video is not a whole library.
            self.assertEqual(code, 2)

            manifest = json.loads((out / "MANIFEST.json").read_text(encoding="utf-8"))
            self.assertTrue(manifest["skipped"], "an incomplete library must say so")
            self.assertEqual(manifest["counts"]["clips_generated"], 0)
            self.assertEqual(manifest["counts"]["stills"], 24)
            self.assertTrue(manifest["synthetic"])
            self.assertEqual(manifest["expectations"]["must_fail_at_proxy"], [])
            for span in manifest["expectations"]["gopro_spans"]:
                self.assertFalse(span["generated"])

            on_disk = {
                str(p.relative_to(out)) for p in out.rglob("*")
                if p.is_file() and p.name != "MANIFEST.json"
            }
            declared = {
                entry["relpath"] for entry in manifest["files"]
                if entry.get("generated", True)
            }
            self.assertEqual(on_disk, declared)

    def test_the_manifest_declares_what_it_does_not_represent(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            out = Path(raw) / "library"
            make_library.main(
                ["--out", str(out), "--stills", "24", "--no-video", "--quiet"]
            )
            manifest = json.loads((out / "MANIFEST.json").read_text(encoding="utf-8"))
            joined = " ".join(manifest["not_represented"]).lower()
            self.assertIn("real", joined)
            self.assertIn("cartoon", manifest["disclaimer"].lower())


# ---------------------------------------------------------------------------
# The runner's bookkeeping
# ---------------------------------------------------------------------------


class StageBookkeepingTests(unittest.TestCase):
    def test_a_failed_check_fails_the_stage(self) -> None:
        run = run_demo.Run(total=1)
        stage = run.stage("thing")
        stage.check("a", True)
        stage.check("b", False, "nope")
        run.report(stage)
        self.assertEqual(stage.status, run_demo.FAILED)
        self.assertEqual(run.exit_code(), 1)

    def test_a_failed_check_beats_a_not_wired_status(self) -> None:
        # The half of a stage that ran must not be able to hide a failure
        # behind the half that did not.
        run = run_demo.Run(total=1)
        stage = run.stage("thing")
        stage.check("a", False, "nope")
        stage.not_wired("the rest is not built", "so this is unproven")
        run.report(stage)
        self.assertEqual(stage.status, run_demo.FAILED)
        self.assertIn("not built", stage.reason)
        self.assertEqual(run.exit_code(), 1)

    def test_a_skipped_stage_is_never_a_pass(self) -> None:
        run = run_demo.Run(total=2)
        first = run.stage("ran")
        first.check("a", True)
        run.report(first)
        second = run.stage("skipped")
        second.skip("no dependency", "nothing is proven")
        run.report(second)
        self.assertEqual(run.exit_code(), 2)

    def test_not_wired_also_prevents_a_pass(self) -> None:
        run = run_demo.Run(total=1)
        stage = run.stage("half built")
        stage.not_wired("the demo does not do this yet", "so it is unproven")
        run.report(stage)
        self.assertEqual(run.exit_code(), 2)

    def test_all_ok_is_the_only_zero(self) -> None:
        run = run_demo.Run(total=2)
        for name in ("one", "two"):
            stage = run.stage(name)
            stage.check("a", True)
            run.report(stage)
        self.assertEqual(run.exit_code(), 0)

    def test_the_ledger_never_calls_an_incomplete_run_a_pass(self) -> None:
        import contextlib
        import io

        run = run_demo.Run(total=2)
        ran = run.stage("ran")
        ran.check("a", True)
        run.report(ran)
        skipped = run.stage("skipped")
        skipped.skip("ffmpeg missing", "the proxy path is untested",
                     "install ffmpeg")
        run.report(skipped)

        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = run.ledger()
        text = buffer.getvalue()

        self.assertEqual(code, 2)
        self.assertIn("INCOMPLETE", text)
        self.assertIn("ffmpeg missing", text)
        self.assertIn("the proxy path is untested", text)
        self.assertNotIn("every stage ran", text)


class JobSpecTests(unittest.TestCase):
    def test_the_scan_locator_digest_is_order_and_slash_insensitive(self) -> None:
        try:
            import blake3  # noqa: F401
        except ImportError:
            self.skipTest("blake3 is not installed; pip install blake3")
        self.assertEqual(
            run_demo.canonical_locator(["/a/b", "/c/d"]),
            run_demo.canonical_locator(["/c/d/", "/a/b"]),
        )
        self.assertNotEqual(
            run_demo.canonical_locator(["/a/b"]),
            run_demo.canonical_locator(["/a/c"]),
        )

    def test_the_proxy_job_declares_what_the_worker_requires(self) -> None:
        try:
            import blake3  # noqa: F401
        except ImportError:
            self.skipTest("blake3 is not installed; pip install blake3")
        job = run_demo.proxy_job(["a" * 64], "test")
        # video.rs::validate_job refuses on any of these.
        self.assertEqual(job["job_type"], "generate_video_proxy")
        self.assertFalse(job["egress"]["requires_egress"])
        self.assertTrue(job["requirements"]["hardware_decode"])
        self.assertTrue(job["checkpoint"]["resumable"])
        self.assertEqual(job["params"]["height"], 480)
        self.assertEqual(job["params"]["codec"], "h264")
        self.assertTrue(job["params"]["emit_frame_index"])


if __name__ == "__main__":
    unittest.main()
