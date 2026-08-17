"""Decoding, and the two ways a decode can lie about being complete.

A decoder that returns the frames it managed to get is a video scored on its
first two seconds and reported as if it were scored on all of it. Both paths
that could do that — a partial final frame, and FFmpeg exiting non-zero after
producing whole frames — are pinned here.
"""

from __future__ import annotations

import shutil
import unittest
from pathlib import Path

import _support  # noqa: F401 - sets sys.path

from memory_engine_video_analysis import decode


class Probing(unittest.TestCase):
    def test_the_frame_rate_is_an_exact_fraction(self):
        probe = decode.probe(_support.hard_cut())
        self.assertEqual(probe.rate.denominator, 1)
        self.assertEqual(probe.rate.numerator, _support.RATE)

    def test_a_missing_file_is_named(self):
        with self.assertRaises(decode.DecodeError):
            decode.probe("/does/not/exist.mp4")

    def test_a_file_with_no_video_stream_is_refused(self):
        clip = _support.steady_tone()
        audio_only = clip.with_name("audio_only.m4a")
        import subprocess  # noqa: PLC0415

        subprocess.run(
            [_support.ffmpeg(), "-y", "-hide_banner", "-loglevel", "error",
             "-i", str(clip), "-vn", "-c:a", "aac", str(audio_only)],
            check=True, capture_output=True,
        )
        with self.assertRaises(decode.DecodeError) as caught:
            decode.probe(audio_only)
        self.assertIn("no video stream", str(caught.exception))


class TruncatedInput(unittest.TestCase):
    def test_a_truncated_container_does_not_decode_quietly(self):
        """The failure mode this whole module is arranged around.

        FFmpeg reads whole frames out of a half-written file and then exits
        non-zero. Every frame arrives cleanly; the video is half decoded.
        """
        source = _support.hard_cut()
        truncated = Path(str(source).replace(".mp4", ".truncated.mp4"))
        payload = source.read_bytes()
        truncated.write_bytes(payload[: int(len(payload) * 0.55)])

        probe = decode.probe(source)
        size = decode.analysis_size(probe.width, probe.height)
        with self.assertRaises(decode.DecodeError):
            for _ in decode.iter_frames(truncated, size):
                pass

    def test_a_complete_file_yields_exactly_its_frames(self):
        clip = _support.hard_cut()
        probe = decode.probe(clip)
        size = decode.analysis_size(probe.width, probe.height)
        frames = list(decode.iter_frames(clip, size))
        self.assertEqual(len(frames), probe.nb_frames)
        self.assertTrue(
            all(len(frame) == size[0] * size[1] * 3 for frame in frames)
        )

    def test_stopping_early_is_not_an_error(self):
        """A caller reading two frames must not be reported as a broken decode."""
        clip = _support.hard_cut()
        probe = decode.probe(clip)
        size = decode.analysis_size(probe.width, probe.height)
        stream = decode.iter_frames(clip, size)
        first = next(stream)
        second = next(stream)
        stream.close()
        self.assertEqual(len(first), len(second))


class ToolResolution(unittest.TestCase):
    def test_an_override_pointing_at_nothing_is_named(self):
        import os  # noqa: PLC0415

        previous = os.environ.get("MEMORY_ENGINE_FFMPEG")
        os.environ["MEMORY_ENGINE_FFMPEG"] = "/nowhere/ffmpeg"
        try:
            with self.assertRaises(decode.ToolMissing) as caught:
                decode.ffmpeg_path()
            self.assertIn("MEMORY_ENGINE_FFMPEG", str(caught.exception))
        finally:
            if previous is None:
                del os.environ["MEMORY_ENGINE_FFMPEG"]
            else:
                os.environ["MEMORY_ENGINE_FFMPEG"] = previous

    def test_ffmpeg_and_ffprobe_are_present(self):
        """The suite has no skips; this is where their absence is reported."""
        self.assertTrue(Path(decode.ffmpeg_path()).exists())
        self.assertTrue(
            shutil.which("ffprobe") or Path(decode.ffprobe_path()).exists()
        )


if __name__ == "__main__":
    unittest.main()
