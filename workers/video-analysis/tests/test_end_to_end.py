"""Proxy in, schema-valid MomentRecords out. The deliverable, pinned.

Everything here runs the real chain — real FFmpeg decode, real feature
producers, the real `plan_moments` from `packages/story-engine`, and validation
against `contracts/schemas/moment-record.schema.json` with the real
jsonschema — because the modules being individually correct is not the claim.
The claim is that a video goes in and MomentRecords that the contract accepts
come out, placed at the right point in source time.

THE TIMECODE ASSERTIONS ARE THE POINT. A moment whose features are excellent
and whose `source_range` is off by a frame is worse than no moment at all: it
renders, it looks like a reel, and the only way to find it is to watch the
output against the source.
"""

from __future__ import annotations

import json
import unittest

import _support  # noqa: F401 - sets sys.path

from memory_engine_story.moments import plan_moments
from memory_engine_video_analysis import stream as stream_module
from memory_engine_video_analysis.proxy import Proxy, read_frame_index
from memory_engine_video_analysis.transcript import NullTranscriptBackend

SCHEMA_DIR = _support.REPO_ROOT / "contracts" / "schemas"
FIXED_TIME = "2026-08-17T00:00:00+00:00"


def _validator():
    from jsonschema import Draft202012Validator
    from referencing import Registry, Resource

    documents = {
        path.name: json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(SCHEMA_DIR.glob("*.schema.json"))
    }
    registry = Registry().with_resources(
        [(name, Resource.from_contents(document)) for name, document in documents.items()]
    )
    return Draft202012Validator(
        documents["moment-record.schema.json"], registry=registry
    )


class EndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.proxy = _support.make_proxy(_support.hard_cut())
        cls.feature_stream, cls.report = stream_module.analyse_proxy(
            cls.proxy, transcript_backend=NullTranscriptBackend()
        )
        cls.plan = plan_moments(
            cls.feature_stream,
            run_id=stream_module.SCORER_RUN_ID,
            created_at=FIXED_TIME,
            model_runs=stream_module.model_runs_for(
                cls.proxy, cls.report, ran_at=FIXED_TIME
            ),
        )
        cls.validator = _validator()

    def test_every_record_satisfies_the_contract(self):
        self.assertTrue(self.plan.records, "the run produced no records at all")
        for index, record in enumerate(self.plan.records):
            errors = sorted(
                self.validator.iter_errors(record), key=lambda error: list(error.path)
            )
            self.assertEqual(
                errors,
                [],
                f"record {index}: "
                + "; ".join(f"{list(e.path)}: {e.message}" for e in errors[:3]),
            )

    def test_moments_were_actually_kept_and_not_only_eliminated(self):
        self.assertGreater(len(self.plan.moments), 0)

    def test_every_source_range_lies_inside_the_footage(self):
        grid = self.proxy.proxy_grid
        first = grid.start_value
        last = grid.start_value + grid.frame_count
        for scored in self.plan.moments:
            self.assertGreaterEqual(scored.start, first)
            self.assertLessEqual(scored.start + scored.duration, last + 1e-6)

    def test_the_source_rate_is_carried_exactly_into_every_record(self):
        expected = self.proxy.proxy_grid.rate_float
        for record in self.plan.records:
            self.assertEqual(record["source_range"]["start_time"]["rate"], expected)
            self.assertEqual(record["source_range"]["duration"]["rate"], expected)

    def test_no_moment_crosses_the_detected_shot_boundary(self):
        """`_segments` makes this structural; this is the check that the shots
        that reached the stream are the ones the detector actually found."""
        cut = _support.CUT_FRAME
        for scored in self.plan.moments:
            start, end = scored.start, scored.start + scored.duration
            self.assertFalse(
                start < cut < end,
                f"a moment spans the cut at frame {cut}: [{start}, {end})",
            )

    def test_the_shot_boundary_is_offered_as_a_snap_point(self):
        kinds = {
            point["kind"]
            for scored in self.plan.moments
            for point in scored.record["snap_points"]
        }
        self.assertIn("shot_boundary", kinds)

    def test_features_absent_from_the_producers_are_absent_from_the_records(self):
        """Not zero. `moments.py` renormalises absence and reports coverage;
        a fabricated zero would be indistinguishable from a measurement."""
        for scored in self.plan.moments:
            features = scored.record.get("features") or {}
            for name in ("face_presence", "smile_intensity", "max_face_area_ratio"):
                self.assertNotIn(name, features)
            self.assertLess(scored.fusion.coverage, 1.0)
            self.assertGreater(scored.fusion.coverage, 0.0)

    def test_measured_features_are_present(self):
        for scored in self.plan.moments:
            features = scored.record["features"]
            for name in ("motion_energy", "shake", "sharpness", "exposure_stability"):
                self.assertIn(name, features)

    def test_provenance_names_every_producer_that_ran(self):
        runs = self.plan.records[0]["model_runs"]
        identifiers = {run["model"]["model_id"] for run in runs}
        self.assertIn("video-visual-features", identifiers)
        self.assertIn("video-audio-features", identifiers)
        self.assertIn(self.report.shot_detector, identifiers)
        for run in runs:
            self.assertEqual(run["input_proxy_id"], self.proxy.proxy_id)

    def test_the_score_run_id_resolves_to_a_recorded_run(self):
        runs = {run["run_id"] for run in self.plan.records[0]["model_runs"]}
        pointed_at = self.plan.moments[0].record["scores"]["moment_score"]["run_id"]
        self.assertIn(pointed_at, runs)

    def test_provenance_is_omitted_rather_than_dated_with_an_invented_time(self):
        self.assertEqual(
            stream_module.model_runs_for(self.proxy, self.report, ran_at=None), ()
        )

    def test_the_run_is_reproducible_byte_for_byte(self):
        """Same proxy, same policy, same fixed clock: identical records.

        `moments.py` promises this and it is only true if the producers are
        deterministic too — one float summed in a different order reorders a
        tie and a different second of footage lands in the reel.
        """
        again_stream, again_report = stream_module.analyse_proxy(
            self.proxy, transcript_backend=NullTranscriptBackend()
        )
        again = plan_moments(
            again_stream,
            run_id=stream_module.SCORER_RUN_ID,
            created_at=FIXED_TIME,
            model_runs=stream_module.model_runs_for(
                self.proxy, again_report, ran_at=FIXED_TIME
            ),
        )
        self.assertEqual(
            json.dumps(self.plan.records, sort_keys=True),
            json.dumps(again.records, sort_keys=True),
        )

    def test_the_stream_declares_no_language_when_there_is_no_transcript(self):
        """`FeatureStream.language` is a BCP-47 tag that reaches the database.

        With no transcript there is no language, and a default of "en" would
        label a Hindi moment English forever. It happens to change no record
        today — `_transcript` needs words as well — which is exactly why it
        needs pinning rather than leaving to a downstream check.
        """
        self.assertIsNone(self.feature_stream.language)
        self.assertEqual(self.feature_stream.words, ())

    def test_the_report_states_what_was_not_measured(self):
        text = "\n".join(self.report.lines())
        self.assertIn("transcript: NONE", text)
        self.assertIn("WEIGHTS_MISSING", text)
        self.assertIn("face_presence", text)


class SilentFootage(unittest.TestCase):
    def test_a_clip_with_no_audio_still_produces_valid_records(self):
        """The demo library ships a silent clip for exactly this path."""
        proxy = _support.make_proxy(_support.silent_video())
        feature_stream, report = stream_module.analyse_proxy(proxy)
        self.assertFalse(report.audio_available)
        plan = plan_moments(feature_stream, created_at=FIXED_TIME)
        validator = _validator()
        for record in plan.records:
            self.assertEqual(list(validator.iter_errors(record)), [])
        for scored in plan.moments:
            audio = (scored.record.get("features") or {}).get("audio")
            self.assertIsNone(audio, "a silent clip reported audio features")


class SourceOffset(unittest.TestCase):
    def test_a_proxy_that_starts_late_shifts_every_emitted_time(self):
        """A proxy of a clip that begins at source frame 900, not 0.

        Every moment must move by exactly 900 frames. An offset dropped here
        would place a real moment at the wrong point in a real source file, and
        the record would look entirely well-formed.
        """
        offset = 900
        clip = _support.hard_cut()
        plain = _support.write_sidecar(clip, clip.with_suffix(".plain.idx"))
        lines = plain.read_text(encoding="utf-8").splitlines()
        header = json.loads(lines[0])
        rows = [json.loads(line) for line in lines[1:]]
        delta = rows[1]["source_pts"] - rows[0]["source_pts"]
        for row in rows:
            row["source_pts"] += offset * delta
            row["source_time_seconds"] = row["source_pts"] * (
                header["source_time_base_numerator"]
                / header["source_time_base_denominator"]
            )
        shifted_path = clip.with_suffix(".shifted.idx")
        shifted_path.write_text(
            "\n".join([json.dumps(header), *(json.dumps(row) for row in rows)]) + "\n",
            encoding="utf-8",
        )

        def moments_for(path):
            proxy = Proxy(
                media_id="a" * 64,
                path=clip,
                frame_index=read_frame_index(path),
                proxy_id="b" * 64,
            )
            built, _ = stream_module.analyse_proxy(proxy)
            return plan_moments(built, created_at=FIXED_TIME), built

        base_plan, base_stream = moments_for(plain)
        shifted_plan, shifted_stream = moments_for(shifted_path)

        self.assertEqual(base_stream.start_value, 0.0)
        self.assertEqual(shifted_stream.start_value, float(offset))
        self.assertEqual(len(base_plan.moments), len(shifted_plan.moments))
        for original, moved in zip(base_plan.moments, shifted_plan.moments):
            self.assertAlmostEqual(moved.start - original.start, offset, places=6)
            self.assertAlmostEqual(moved.duration, original.duration, places=6)
        # The proxy range is NOT shifted: proxy frame 0 is still proxy frame 0.
        for moved in shifted_plan.moments:
            self.assertGreaterEqual(
                moved.record["proxy_range"]["start_time"]["value"], 0
            )
            self.assertLess(
                moved.record["proxy_range"]["start_time"]["value"], offset
            )


class GridSafety(unittest.TestCase):
    def test_a_frame_count_that_disagrees_with_the_sidecar_is_refused(self):
        """The single most dangerous inconsistency in this worker.

        One dropped frame and every later sample is attributed to the wrong
        source frame — a wrong timecode that nothing downstream can detect.
        """
        clip = _support.hard_cut()
        sidecar = _support.write_sidecar(clip, clip.with_suffix(".short.idx"))
        lines = sidecar.read_text(encoding="utf-8").splitlines()
        header = json.loads(lines[0])
        header["entry_count"] = len(lines) - 2
        sidecar.write_text(
            "\n".join([json.dumps(header), *lines[1:-1]]) + "\n", encoding="utf-8"
        )
        proxy = Proxy(
            media_id="a" * 64,
            path=clip,
            frame_index=read_frame_index(sidecar),
            proxy_id="b" * 64,
        )
        with self.assertRaises(stream_module.StreamAssemblyError) as caught:
            stream_module.analyse_proxy(proxy)
        self.assertIn("frame index holds", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
