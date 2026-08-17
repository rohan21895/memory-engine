"""Shot detection, against clips built to be exactly the four hard cases.

A cut, a flash, a whip pan and a dissolve. The flash and the pan are the two
that a naive frame-differencing detector gets wrong, and getting them wrong is
not symmetric: a false boundary fragments a moment, a missed one lets a moment
span a scene change and reach a finished reel.

`test_a_slow_dissolve_is_missed` asserts a LIMITATION rather than a behaviour.
It is here so the gap is visible in the suite instead of only in a docstring,
and so that wiring TransNetV2 shows up as this test failing — which is the
point at which someone should read it and delete it.
"""

from __future__ import annotations

import unittest

import _support  # noqa: F401 - sets sys.path

from memory_engine_video_analysis import decode, shots, visual


def _signatures(clip):
    probe = decode.probe(clip)
    size = decode.analysis_size(probe.width, probe.height)
    measured = visual.analyse_frames(
        decode.iter_frames(clip, size), size=size, rate=float(probe.rate)
    )
    return measured.signatures, float(probe.rate)


def _detect(clip):
    signatures, rate = _signatures(clip)
    return shots.detect_shots(signatures, rate=rate)


class TheFourCases(unittest.TestCase):
    def test_a_hard_cut_is_found_at_the_frame_it_happens(self):
        detection = _detect(_support.hard_cut())
        self.assertEqual(detection.cut_frames, (_support.CUT_FRAME,))
        self.assertEqual(len(detection.shots), 2)

    def test_a_flash_is_not_a_cut(self):
        detection = _detect(_support.flash())
        self.assertEqual(detection.cut_frames, ())
        self.assertEqual(len(detection.shots), 1)
        self.assertIn(_support.FLASH_FRAME, detection.suppressed_flashes)

    def test_a_whip_pan_is_not_a_cut(self):
        """And it clears the absolute threshold, so only the baseline saves it.

        The pan's distances run above `HIGH_DELTA_E` for most of a second. If
        the adaptive baseline were removed, this clip would be chopped into
        dozens of shots and every moment in it would be a fragment.
        """
        signatures, rate = _signatures(_support.whip_pan())
        detection = shots.detect_shots(signatures, rate=rate)
        self.assertEqual(detection.cut_frames, ())
        self.assertGreater(
            max(detection.distances),
            shots.HIGH_DELTA_E,
            "this clip no longer exercises the baseline test",
        )

    def test_a_short_dissolve_is_one_boundary_not_several(self):
        detection = _detect(_support.dissolve_short())
        self.assertEqual(len(detection.cut_frames), 1)
        # The fade is centred at 1.5s + half of 0.4s at 30fps.
        self.assertAlmostEqual(detection.cut_frames[0], 51, delta=15)

    def test_a_slow_dissolve_is_missed(self):
        """KNOWN LIMITATION, pinned. See the module docstring in shots.py.

        A 1.5s cross fade spreads the change over 45 frames and peaks at about
        dE 3.2, under every threshold here. TransNetV2 is what fixes this; when
        it is wired, this test should fail and be deleted.
        """
        detection = _detect(_support.dissolve_long())
        self.assertEqual(detection.cut_frames, ())
        self.assertLess(max(detection.distances), shots.HIGH_DELTA_E)


class Structure(unittest.TestCase):
    def test_shots_partition_the_stream_with_no_gap_and_no_overlap(self):
        signatures, rate = _signatures(_support.hard_cut())
        detection = shots.detect_shots(signatures, rate=rate)
        self.assertEqual(detection.shots[0].start, 0)
        self.assertEqual(detection.shots[-1].end, len(signatures))
        for earlier, later in zip(detection.shots, detection.shots[1:]):
            self.assertEqual(earlier.end, later.start)

    def test_every_shot_id_is_a_contract_slug(self):
        import re  # noqa: PLC0415

        pattern = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
        signatures, rate = _signatures(_support.hard_cut())
        for shot in shots.detect_shots(signatures, rate=rate).shots:
            self.assertRegex(shot.shot_id, pattern)

    def test_a_clip_with_no_cuts_is_one_shot_rather_than_none(self):
        """"One continuous shot" and "nobody ran a detector" must not look alike."""
        detection = _detect(_support.whip_pan())
        self.assertEqual(len(detection.shots), 1)
        self.assertEqual(detection.shots[0].start, 0)

    def test_the_stream_validates_the_shots_against_its_own_frame_count(self):
        from memory_engine_story.moments import FeatureStream, Frame  # noqa: PLC0415

        signatures, rate = _signatures(_support.hard_cut())
        detection = shots.detect_shots(signatures, rate=rate)
        FeatureStream(
            media_id="a" * 64,
            rate=rate,
            frames=tuple(Frame(luma=0.5) for _ in signatures),
            shots=detection.shots,
        ).validate()


class LearnedBackendSeam(unittest.TestCase):
    def test_the_gate_is_consulted_and_refuses_for_want_of_weights(self):
        status = shots.transnetv2_seam()
        self.assertTrue(status.checked, status.reason)
        self.assertFalse(status.available)
        self.assertIn("WEIGHTS_MISSING", status.reason)

    def test_a_backend_decides_the_cuts_when_one_is_supplied(self):
        class Fake:
            model_id = "transnetv2"
            version = "2.0.0"

            def detect(self, proxy_path, frame_count):
                del proxy_path
                return [frame_count // 2]

        signatures, rate = _signatures(_support.whip_pan())
        detection = shots.detect_shots(
            signatures, rate=rate, proxy_path="x.mp4", backend=Fake()
        )
        self.assertTrue(detection.is_learned)
        self.assertEqual(detection.detector_id, "transnetv2")
        self.assertEqual(len(detection.shots), 2)
        self.assertEqual(detection.distances[1:2] != (), True)

    def test_a_backend_cut_outside_the_stream_is_refused(self):
        class Rogue:
            model_id = "transnetv2"
            version = "2.0.0"

            def detect(self, proxy_path, frame_count):
                del proxy_path
                return [0, frame_count]

        signatures, rate = _signatures(_support.hard_cut())
        with self.assertRaises(shots.ShotError):
            shots.detect_shots(
                signatures, rate=rate, proxy_path="x.mp4", backend=Rogue()
            )

    def test_a_backend_without_a_proxy_path_is_refused(self):
        class Fake:
            model_id = "transnetv2"
            version = "2.0.0"

            def detect(self, proxy_path, frame_count):
                return []

        signatures, rate = _signatures(_support.whip_pan())
        with self.assertRaises(shots.ShotError):
            shots.detect_shots(signatures, rate=rate, backend=Fake())


class Distances(unittest.TestCase):
    def test_the_distance_is_a_mean_not_a_maximum(self):
        """One block changing completely is a subject, not a cut."""
        blocks = 16 * 9
        same = [0.0, 0.0, 0.0] * blocks
        one_block_black_to_white = [0.0, 0.0, 0.0] * blocks
        one_block_black_to_white[0] = 100.0
        distance = shots._distance(same, one_block_black_to_white)
        self.assertAlmostEqual(distance, 100.0 / blocks, places=9)
        self.assertLess(distance, shots.HIGH_DELTA_E)

    def test_mismatched_signature_lengths_are_refused(self):
        with self.assertRaises(shots.ShotError):
            shots._distance([0.0, 0.0, 0.0], [0.0, 0.0, 0.0, 1.0, 1.0, 1.0])


if __name__ == "__main__":
    unittest.main()
