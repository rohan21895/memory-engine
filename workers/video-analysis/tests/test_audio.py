"""Loudness, onsets and silence — checked against FFmpeg's own BS.1770.

TWO TESTS CARRY THIS FILE

`test_loudness_agrees_with_ffmpeg_ebur128` measures a signal with this module
and with `ffmpeg -af ebur128`, which is an independent implementation of the
same recommendation by people who were not in this conversation. A K-weighting
transcribed with a sign error, applied at the wrong sample rate, or missing the
-0.691 offset produces perfectly plausible numbers in exactly the right range;
this is the only check here that could tell.

`test_an_onset_produces_a_rise_the_planner_can_see` is the one that justifies
the short window. `moments._level_snaps` needs a FRAME-TO-FRAME rise of
`Policy.audio_onset_db` (6 LU). BS.1770's momentary loudness is a 400ms window,
which at 30fps spreads a 20 LU jump over twelve frames of under 2 LU each — so
with a by-the-book momentary window this producer would emit a loudness series
on which NO audio onset could ever fire, on any footage. That failure is
invisible: the series looks right, the planner just never snaps to sound.
"""

from __future__ import annotations

import math
import subprocess
import unittest

import _support  # noqa: F401 - sets sys.path

import numpy

from memory_engine_story.moments import FeatureStream, Frame, Policy, snap_points
from memory_engine_video_analysis import audio, decode


def _ffmpeg_integrated_lufs(clip) -> float:
    completed = subprocess.run(
        [_support.ffmpeg(), "-hide_banner", "-nostats", "-nostdin",
         "-i", str(clip), "-af", "ebur128", "-f", "null", "-"],
        capture_output=True, text=True, check=False,
    )
    for line in completed.stderr.splitlines():
        stripped = line.strip()
        if stripped.startswith("I:") and "LUFS" in stripped:
            return float(stripped.split()[1])
    raise AssertionError("ffmpeg's ebur128 reported no integrated loudness")


def _analyse(clip):
    probe = decode.probe(clip)
    rate = float(probe.rate)
    frames = probe.nb_frames or int((probe.duration_s or 1.0) * rate)
    return audio.analyse_audio(
        str(clip), frame_count=frames, rate=rate, video_start_s=probe.video_start_s
    ), rate


class AgainstAReference(unittest.TestCase):
    def test_loudness_agrees_with_ffmpeg_ebur128(self):
        """A steady tone: momentary loudness and integrated loudness coincide.

        Tolerance is 0.5 LU. The two implementations differ in gating (ffmpeg's
        integrated figure applies BS.1770's relative gate) and in window
        placement at the edges, so exact equality would be the wrong assertion;
        half a loudness unit is far tighter than any of the mistakes this test
        exists to catch, all of which are worth several LU or more.
        """
        clip = _support.steady_tone()
        reference = _ffmpeg_integrated_lufs(clip)

        raw = decode.read_audio(str(clip), filters=audio.K_WEIGHTING_FILTER)
        self.assertIsNotNone(raw)
        channels = decode.probe(clip).audio.channels
        weighted = audio._as_channel_matrix(raw, channels)
        centres = [0.5 + 0.1 * index for index in range(20)]
        momentary = audio.momentary_loudness(
            weighted, sample_rate=decode.AUDIO_SAMPLE_RATE, centres_seconds=centres
        )
        measured = sum(momentary) / len(momentary)
        self.assertAlmostEqual(
            measured,
            reference,
            delta=0.5,
            msg=f"this module says {measured:.2f} LUFS, ffmpeg says {reference:.2f}",
        )

    def test_the_offset_is_not_silently_absent(self):
        """-0.691 is small enough to look like rounding and is not optional."""
        self.assertAlmostEqual(audio.LOUDNESS_OFFSET_DB, -0.691, places=6)
        ones = numpy.ones((48000, 1))
        value = audio.loudness_series(
            ones, sample_rate=48000, centres_seconds=[0.5], window_seconds=0.4
        )[0]
        self.assertAlmostEqual(value, -0.691, places=5)


class Onsets(unittest.TestCase):
    def test_an_onset_produces_a_rise_the_planner_can_see(self):
        analysis, rate = _analyse(_support.tone_onset())
        self.assertTrue(analysis.available, analysis.reason)
        threshold = Policy().audio_onset_db
        rises = audio.count_loudness_rises(analysis.loudness_lufs, threshold)
        self.assertGreaterEqual(
            rises,
            1,
            "silence followed by a 1kHz tone produced no frame-to-frame rise of "
            f"{threshold} LU, so no audio onset could ever be snapped to",
        )

    def test_the_onset_lands_at_the_right_second(self):
        analysis, rate = _analyse(_support.tone_onset())
        deltas = [
            analysis.loudness_lufs[i] - analysis.loudness_lufs[i - 1]
            for i in range(1, len(analysis.loudness_lufs))
        ]
        loudest = max(range(len(deltas)), key=lambda i: deltas[i]) + 1
        self.assertAlmostEqual(
            loudest / rate,
            _support.ONSET_SECOND,
            delta=0.15,
            msg="the loudest rise is not where the tone starts; the audio is "
            "misaligned against the video frame grid",
        )

    def test_moments_turns_that_rise_into_an_audio_onset_snap_point(self):
        """The producer's job is done only if the planner's detector fires."""
        analysis, rate = _analyse(_support.tone_onset())
        stream = FeatureStream(
            media_id="a" * 64,
            rate=rate,
            frames=tuple(
                Frame(loudness_lufs=value) for value in analysis.loudness_lufs
            ),
        )
        kinds = {point.kind for point in snap_points(stream, Policy())}
        self.assertIn("audio_onset", kinds)

    def test_an_audio_stream_that_starts_before_the_picture_is_still_aligned(self):
        """A container whose streams do not start at the same instant.

        The ground truth is taken from the decoded samples themselves — the
        first sample above the noise floor — rather than from what the fixture
        was asked to build, so the assertion holds however FFmpeg resolved the
        filter timeline. Ignoring the two stream start times moves every
        loudness window by the offset, and the reported onset with it.
        """
        clip = _support.offset_audio()
        probe = decode.probe(clip)
        self.assertIsNotNone(probe.audio_start_s)
        self.assertNotAlmostEqual(
            probe.audio_start_s,
            probe.video_start_s,
            places=3,
            msg="the fixture no longer has an audio/video start offset",
        )

        raw = decode.read_audio(str(clip))
        samples = audio._as_channel_matrix(raw, probe.audio.channels)
        loud = numpy.flatnonzero(numpy.abs(samples[:, 0]) > 0.1)
        self.assertTrue(loud.size, "the fixture carries no audible tone")
        tone_at = probe.audio_start_s + loud[0] / decode.AUDIO_SAMPLE_RATE

        rate = float(probe.rate)
        analysis = audio.analyse_audio(
            str(clip),
            frame_count=int(2.9 * rate),
            rate=rate,
            video_start_s=probe.video_start_s,
        )
        deltas = [
            analysis.loudness_lufs[i] - analysis.loudness_lufs[i - 1]
            for i in range(1, len(analysis.loudness_lufs))
        ]
        loudest = max(range(len(deltas)), key=lambda i: deltas[i]) + 1
        self.assertAlmostEqual(
            probe.video_start_s + loudest / rate,
            tone_at,
            delta=0.12,
            msg=f"the onset was reported at frame {loudest} "
            f"({loudest / rate:.2f}s) but the tone starts at {tone_at:.2f}s",
        )

    def test_the_by_the_book_400ms_window_would_hide_a_real_onset(self):
        """Why the window is one frame. This is the whole argument, measured.

        A 12 LU step is a voice starting over room tone. The planner's rule is
        a 6 LU frame-to-frame rise.
        """
        sample_rate = 48000
        seconds = numpy.arange(sample_rate * 3) / sample_rate
        tone = numpy.sin(2.0 * math.pi * 1000.0 * seconds)
        amplitude = numpy.where(seconds < 1.5, 0.05, 0.05 * 10 ** (12.0 / 20.0))
        signal = (tone * amplitude).reshape(-1, 1)
        centres = [(index + 0.5) / 30.0 for index in range(90)]

        def largest_rise(window: float) -> float:
            series = audio.loudness_series(
                signal,
                sample_rate=sample_rate,
                centres_seconds=centres,
                window_seconds=window,
            )
            return max(series[i] - series[i - 1] for i in range(1, len(series)))

        one_frame = largest_rise(1 / 30.0)
        momentary = largest_rise(0.400)
        self.assertGreaterEqual(one_frame, Policy().audio_onset_db)
        self.assertLess(momentary, Policy().audio_onset_db)
        self.assertAlmostEqual(one_frame, 12.0, delta=0.5)
        self.assertAlmostEqual(momentary, 2.5, delta=0.5)

    def test_the_window_is_one_frame_at_ordinary_rates(self):
        analysis, rate = _analyse(_support.tone_onset())
        self.assertAlmostEqual(analysis.window_seconds, 1.0 / rate, places=9)

    def test_a_constant_tone_produces_no_onsets(self):
        analysis, _ = _analyse(_support.steady_tone())
        self.assertEqual(
            audio.count_loudness_rises(analysis.loudness_lufs, Policy().audio_onset_db),
            0,
        )


class AbsenceIsNotSilence(unittest.TestCase):
    def test_a_clip_with_no_audio_stream_reports_why(self):
        analysis, _ = _analyse(_support.silent_video())
        self.assertFalse(analysis.available)
        self.assertIn("no audio stream", analysis.reason)
        self.assertEqual(analysis.loudness_lufs, ())

    def test_digital_silence_is_measured_and_reads_the_floor(self):
        """Distinct from the case above: this one HAS a stream, and it is quiet."""
        samples = numpy.zeros((48000, 1))
        value = audio.loudness_series(
            samples, sample_rate=48000, centres_seconds=[0.5], window_seconds=0.05
        )[0]
        self.assertEqual(value, audio.LOUDNESS_FLOOR_LUFS)


class Ranges(unittest.TestCase):
    def test_loudness_stays_inside_the_contract_range(self):
        analysis, rate = _analyse(_support.tone_onset())
        for index, value in enumerate(analysis.loudness_lufs):
            self.assertTrue(
                -70.0 <= value <= 0.0, f"frame {index} reported {value} LUFS"
            )
            Frame(loudness_lufs=value).validate(index)

    def test_a_window_never_reaches_past_the_signal(self):
        """Zero padding would drag the first frame towards silence and put a
        false onset at the head of every timeline."""
        samples = numpy.ones((1000, 1))
        first = audio.loudness_series(
            samples, sample_rate=48000, centres_seconds=[0.0], window_seconds=0.05
        )[0]
        middle = audio.loudness_series(
            samples, sample_rate=48000, centres_seconds=[0.01], window_seconds=0.05
        )[0]
        self.assertAlmostEqual(first, middle, places=6)


class Silence(unittest.TestCase):
    def test_short_dips_are_not_reported_as_silence(self):
        rate = 30.0
        flags = [False] * 10 + [True] * 3 + [False] * 10
        self.assertEqual(audio.silence_runs(flags, rate), ())

    def test_a_real_silent_passage_is_reported_with_its_bounds(self):
        rate = 30.0
        flags = [False] * 10 + [True] * 20 + [False] * 10
        self.assertEqual(audio.silence_runs(flags, rate), ((10, 30),))

    def test_silence_that_runs_to_the_end_is_still_reported(self):
        self.assertEqual(audio.silence_runs([False] * 5 + [True] * 20, 30.0), ((5, 25),))

    def test_the_lead_in_of_the_onset_clip_is_silent(self):
        analysis, rate = _analyse(_support.tone_onset())
        runs = analysis.silence_runs(rate)
        self.assertTrue(runs, "the 1.5s of digital silence was not detected")
        start, end = runs[0]
        self.assertEqual(start, 0)
        self.assertAlmostEqual(end / rate, _support.ONSET_SECOND, delta=0.2)


class Shapes(unittest.TestCase):
    def test_a_truncated_sample_buffer_is_refused(self):
        with self.assertRaises(audio.AudioError):
            audio._as_channel_matrix(b"\x00" * 10, 3)

    def test_the_windowed_mean_square_matches_a_naive_computation(self):
        generator = numpy.random.default_rng(11)
        samples = generator.standard_normal((500, 2))
        centres = numpy.array([0, 17, 250, 499])
        half = 20
        fast = audio._windowed_mean_square(samples, centres, half)
        for row, centre in enumerate(centres):
            lo, hi = max(0, centre - half), min(500, centre + half)
            expected = (samples[lo:hi] ** 2).mean(axis=0)
            for channel in range(2):
                self.assertTrue(
                    math.isclose(fast[row, channel], expected[channel], rel_tol=1e-12)
                )


if __name__ == "__main__":
    unittest.main()
