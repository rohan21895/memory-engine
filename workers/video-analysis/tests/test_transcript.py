"""The transcript interface, and the guarantee that the null backend invents nothing.

The tests that matter are the last two: a stream built with the null backend
must contain NO word, NO speech snap point and NO TranscriptSegment. A
fabricated transcript would not merely be inaccurate — it would make
`moments.py` certify cuts as speech-safe against words nobody said, which is
the exact defect this project just spent a day fixing from the other direction.
"""

from __future__ import annotations

import unittest

import _support  # noqa: F401 - sets sys.path

from memory_engine_story.moments import FeatureStream, Frame, Policy, plan_moments, snap_points
from memory_engine_video_analysis.transcript import (
    NullTranscriptBackend,
    TimedWord,
    TranscriptResult,
    transcribe,
    words_are_sorted_and_disjoint,
)


class TheNullBackend(unittest.TestCase):
    def test_it_reports_unavailable_with_a_reason_a_person_can_act_on(self):
        result = NullTranscriptBackend().transcribe("anything.mp4")
        self.assertFalse(result.available)
        self.assertEqual(result.words, ())
        self.assertIsNone(result.language)
        self.assertIn("faster-whisper", result.reason)
        self.assertIn("not unsafe, unknown", result.reason)

    def test_the_default_backend_is_the_null_one_rather_than_nothing(self):
        """A caller who forgets a backend gets "no transcript", not "no speech"."""
        result = transcribe("anything.mp4")
        self.assertFalse(result.available)
        self.assertTrue(result.reason)

    def test_it_never_reads_the_file(self):
        result = NullTranscriptBackend().transcribe("/does/not/exist.mp4")
        self.assertFalse(result.available)


class ResultInvariants(unittest.TestCase):
    def test_unavailable_with_words_is_a_contradiction(self):
        with self.assertRaises(ValueError):
            TranscriptResult(
                available=False, words=(TimedWord("hello", 0.0, 0.4),)
            )

    def test_available_without_a_language_is_refused(self):
        """Defaulting to "en" would mislabel a Hindi moment forever."""
        with self.assertRaises(ValueError):
            TranscriptResult(available=True, words=(TimedWord("नमस्ते", 0.0, 0.4),))

    def test_a_word_that_ends_before_it_starts_is_refused(self):
        with self.assertRaises(ValueError):
            TranscriptResult(
                available=True,
                language="en",
                words=(TimedWord("backwards", 0.9, 0.4),),
            )


class FrameGridMapping(unittest.TestCase):
    def test_seconds_become_frame_units_at_the_stream_rate(self):
        result = TranscriptResult(
            available=True,
            language="en-IN",
            words=(TimedWord("one", 1.0, 1.5), TimedWord("two", 2.0, 2.25)),
        )
        words = result.to_words(rate=30.0)
        self.assertEqual([w.word for w in words], ["one", "two"])
        self.assertAlmostEqual(words[0].start, 30.0)
        self.assertAlmostEqual(words[0].end, 45.0)
        self.assertAlmostEqual(words[1].start, 60.0)

    def test_the_start_offset_moves_every_word_with_the_picture(self):
        result = TranscriptResult(
            available=True, language="en", words=(TimedWord("x", 1.0, 1.5),)
        )
        words = result.to_words(rate=30.0, start_value=90.0)
        self.assertAlmostEqual(words[0].start, 120.0)

    def test_words_are_returned_in_time_order(self):
        result = TranscriptResult(
            available=True,
            language="en",
            words=(TimedWord("late", 2.0, 2.5), TimedWord("early", 0.5, 0.9)),
        )
        self.assertEqual([w.word for w in result.to_words(rate=25.0)], ["early", "late"])

    def test_word_times_are_not_snapped_to_frames(self):
        """Rounding a word boundary outward by half a frame clips a consonant."""
        result = TranscriptResult(
            available=True, language="en", words=(TimedWord("s", 1.017, 1.083),)
        )
        word = result.to_words(rate=30.0)[0]
        self.assertNotEqual(word.start, round(word.start))

    def test_overlap_is_reported_not_rejected(self):
        """Diarised speakers legitimately nest one word inside another."""
        self.assertTrue(
            words_are_sorted_and_disjoint(
                [TimedWord("a", 0.0, 0.5), TimedWord("b", 0.6, 0.9)]
            )
        )
        self.assertFalse(
            words_are_sorted_and_disjoint(
                [TimedWord("long", 0.0, 2.0), TimedWord("in", 0.4, 0.6)]
            )
        )


class WhatMomentsDoesWithAnAbsentTranscript(unittest.TestCase):
    def _stream(self):
        return FeatureStream(
            media_id="a" * 64,
            rate=30.0,
            frames=tuple(
                Frame(
                    luma=0.5,
                    sharpness=0.6,
                    motion=0.3 + 0.2 * ((index // 20) % 2),
                    shake=0.1,
                    exposure_stability=0.9,
                    novelty=0.4,
                    loudness_lufs=-20.0,
                )
                for index in range(180)
            ),
            words=NullTranscriptBackend()
            .transcribe("x.mp4")
            .to_words(rate=30.0),
            language=None,
        )

    def test_no_speech_snap_point_is_certified(self):
        kinds = {point.kind for point in snap_points(self._stream(), Policy())}
        self.assertNotIn("speech_start", kinds)
        self.assertNotIn("speech_end", kinds)
        self.assertNotIn("speech_gap", kinds)

    def test_no_record_carries_a_transcript_segment(self):
        plan = plan_moments(self._stream(), created_at="2026-08-17T00:00:00+00:00")
        self.assertTrue(plan.moments, "the fixture produced no moments to check")
        for scored in plan.moments:
            self.assertNotIn("transcript", scored.record)

    def test_safe_trim_reports_speech_bounds_as_null_rather_than_as_the_edges(self):
        """Null means "there is no speech here to be safe from", which is the
        truth; the edge times would read as a positive safety assertion."""
        plan = plan_moments(self._stream(), created_at="2026-08-17T00:00:00+00:00")
        for scored in plan.moments:
            trim = scored.record["safe_trim"]
            self.assertIsNone(trim["speech_safe_in"])
            self.assertIsNone(trim["speech_safe_out"])


if __name__ == "__main__":
    unittest.main()
