"""Per-frame visual signals, against real decoded video.

The important test in this file is `test_a_smooth_pan_does_not_read_as_shake`.
It is a regression: the first implementation searched INTEGER shifts on a 64px
raster, a sub-pixel pan quantised to a displacement flipping between 0 and 1,
and every panning clip in the demo library was eliminated by
`Policy.shake_max`. Nothing raised, nothing looked wrong; the footage simply
stopped existing.
"""

from __future__ import annotations

import sys
import unittest

import _support  # noqa: F401 - sets sys.path

import numpy

from memory_engine_story.moments import Frame, Policy, eliminate_frames, FeatureStream
from memory_engine_video_analysis import decode, visual


def _measure(clip):
    probe = decode.probe(clip)
    size = decode.analysis_size(probe.width, probe.height)
    return visual.analyse_frames(
        decode.iter_frames(clip, size), size=size, rate=float(probe.rate)
    ), float(probe.rate)


class SharedCalibration(unittest.TestCase):
    def test_laplacian_variance_matches_the_photo_pipeline(self):
        """`SHARPNESS_HALF_POINT` is borrowed from classical-quality 1.0.0.

        The constant only means something if the measurement behind it is the
        same measurement. Borrowing a calibration and then computing it
        differently is a decalibration that no output would reveal — the number
        stays in range, it is just no longer comparable with a still's.
        """
        sys.path.insert(0, str(_support.REPO_ROOT / "services" / "pipeline"))
        from memory_engine_pipeline import classical  # noqa: PLC0415

        generator = numpy.random.default_rng(20260817)
        for _ in range(5):
            array = generator.random((37, 53))
            self.assertAlmostEqual(
                visual._laplacian_variance(array),
                classical._laplacian_variance(array),
                places=15,
            )
        self.assertEqual(visual.SHARPNESS_HALF_POINT, classical.SHARPNESS_HALF_POINT)
        self.assertEqual(visual.HIGHLIGHT_LEVEL, classical.HIGHLIGHT_LEVEL)
        self.assertEqual(visual.SHADOW_LEVEL, classical.SHADOW_LEVEL)

    def test_the_luma_transform_matches_the_one_the_constant_was_measured_on(self):
        """`classical.py` reads luma through PIL's convert("L"), which is ITU-R
        601 on gamma-encoded values. Borrowing its sharpness constant while
        computing luma with 709 weights decalibrates the constant by a few
        percent — and nothing about the output would look wrong, because the
        number stays in range. It is simply no longer comparable with a still's.
        """
        from PIL import Image  # noqa: PLC0415

        generator = numpy.random.default_rng(4242)
        rgb = generator.integers(0, 256, size=(24, 31, 3), dtype=numpy.uint8)
        reference = (
            numpy.asarray(Image.fromarray(rgb, "RGB").convert("L"), dtype=numpy.float64)
            / 255.0
        )
        ours = (
            visual._LUMA_R * rgb[:, :, 0]
            + visual._LUMA_G * rgb[:, :, 1]
            + visual._LUMA_B * rgb[:, :, 2]
        ) / 255.0
        # PIL computes in fixed point and truncates to 8 bits, so one level is
        # the whole permitted difference. Still 25x tighter than the ~0.1
        # (25-level) gap that 709 weights would open, which is what this pins.
        difference = float(numpy.abs(ours - reference).max())
        self.assertLessEqual(difference, 1.0 / 255.0, f"max difference {difference}")


class Shake(unittest.TestCase):
    def test_a_smooth_pan_does_not_read_as_shake(self):
        measured, _ = _measure(_support.smooth_pan())
        worst = max(frame.shake for frame in measured.frames if frame.shake is not None)
        self.assertLess(
            worst,
            Policy().shake_max,
            "a smooth pan cleared the shake elimination gate; every panning shot "
            "in the library would be deleted",
        )

    def test_the_displacement_estimate_is_sub_pixel(self):
        """The integer-only estimator is what produced the regression above.

        Also pins the SIGN: the shift is defined by
        `current[i] == previous[i + s]`, so content moved towards HIGHER
        indices gives a negative shift. Nothing downstream depends on the sign
        — shake is the magnitude of a second difference — which is precisely
        why an inverted convention would never surface on its own. This test
        was written expecting the opposite sign and was the thing that was
        wrong.
        """
        previous = numpy.linspace(0.0, 1.0, 200) ** 2
        moved_right = numpy.interp(
            numpy.arange(200) - 0.4, numpy.arange(200), previous, left=0.0, right=1.0
        )
        estimate = visual._estimate_shift(moved_right, previous, 8)
        self.assertNotEqual(estimate, round(estimate))
        self.assertAlmostEqual(estimate, -0.4, delta=0.15)

        moved_left = numpy.interp(
            numpy.arange(200) + 0.4, numpy.arange(200), previous, left=0.0, right=1.0
        )
        self.assertAlmostEqual(
            visual._estimate_shift(moved_left, previous, 8), 0.4, delta=0.15
        )

    def test_a_flat_profile_resolves_to_zero_rather_than_to_the_search_bound(self):
        flat = numpy.zeros(64)
        self.assertEqual(visual._estimate_shift(flat, flat, 8), 0.0)

    def test_the_sub_pixel_estimate_is_accurate_across_a_sweep(self):
        """Accuracy, not merely non-integrality.

        The parabolic refinement fits a quadratic to three cost samples, which
        is right for a SQUARED cost and biased for an absolute one. Measured
        over this sweep: squared error peaks at 0.074px, absolute at 0.143px.
        That bias is not a rounding detail — the displacement noise it leaves
        behind is indistinguishable from camera shake, and shake is a hard
        elimination gate.
        """
        generator = numpy.random.default_rng(7)
        profile = numpy.convolve(
            generator.random(300), numpy.ones(9) / 9.0, mode="same"
        )
        positions = numpy.arange(300)
        worst = 0.0
        for true_shift in (0.0, 0.1, 0.2, 0.25, 0.3, 0.4, 0.5, -0.15, -0.35, -0.45,
                           1.3, -2.2):
            moved = numpy.interp(
                positions - true_shift, positions, profile,
                left=profile[0], right=profile[-1],
            )
            estimate = visual._estimate_shift(moved, profile, 8)
            worst = max(worst, abs(estimate - (-true_shift)))
        self.assertLess(worst, 0.10, f"worst sub-pixel error was {worst:.3f}px")

    def test_a_fast_pan_is_not_eliminated_as_shake(self):
        """Shake is the SECOND difference of displacement, not the first.

        A whip pan moves the picture by up to 12px per frame on the analysis
        raster and changes that velocity by 0.19px per frame on average. A
        first-difference definition of shake would read 12/8 — saturated — and
        eliminate the whole shot; the second difference reads 0.02.
        """
        measured, _ = _measure(_support.whip_pan())
        shakes = [f.shake for f in measured.frames if f.shake is not None]
        eliminated = [value for value in shakes if value >= Policy().shake_max]
        self.assertLess(
            len(eliminated) / len(shakes),
            0.10,
            f"{len(eliminated)} of {len(shakes)} frames of a pan would be "
            "eliminated as shake",
        )
        ordered = sorted(shakes)
        self.assertLess(ordered[len(ordered) // 2], 0.10)


class NotMeasuredIsNotZero(unittest.TestCase):
    def test_the_first_frame_has_no_motion_and_the_first_two_no_shake(self):
        """A difference needs two samples and a second difference needs three.

        Reporting 0.0 would put a fabricated perfectly-still moment at the head
        of every clip, which is exactly where a reel's hook is chosen from.
        """
        measured, _ = _measure(_support.hard_cut())
        self.assertIsNone(measured.frames[0].motion)
        self.assertIsNone(measured.frames[0].novelty)
        self.assertIsNone(measured.frames[0].shake)
        self.assertIsNone(measured.frames[1].shake)
        self.assertIsNotNone(measured.frames[1].motion)
        self.assertIsNotNone(measured.frames[2].shake)


class ContractRanges(unittest.TestCase):
    def test_every_measured_value_satisfies_the_stream_contract(self):
        measured, _ = _measure(_support.whip_pan())
        for index, frame in enumerate(measured.frames):
            Frame(
                luma=frame.luma,
                clipped_highlights=frame.clipped_highlights,
                clipped_shadows=frame.clipped_shadows,
                sharpness=frame.sharpness,
                exposure_stability=frame.exposure_stability,
                motion=frame.motion,
                shake=frame.shake,
                novelty=frame.novelty,
            ).validate(index)

    def test_measurements_are_quantised_to_six_decimals(self):
        measured, _ = _measure(_support.hard_cut())
        for frame in measured.frames[:20]:
            self.assertEqual(frame.luma, round(frame.luma, 6))
            self.assertEqual(frame.sharpness, round(frame.sharpness, 6))


class EliminationGates(unittest.TestCase):
    def test_a_black_clip_is_eliminated_as_a_black_frame(self):
        """The gate `moments.py` applies, applied to what this file produces.

        Measuring luma correctly and having the gate still not fire would be a
        units mismatch — the class of defect that produced 84% of real faces
        being discarded elsewhere in this repository.
        """
        measured, rate = _measure(_support.black_clip())
        stream = FeatureStream(
            media_id="f" * 64,
            rate=rate,
            frames=tuple(
                Frame(
                    luma=frame.luma,
                    clipped_highlights=frame.clipped_highlights,
                    clipped_shadows=frame.clipped_shadows,
                    sharpness=frame.sharpness,
                    exposure_stability=frame.exposure_stability,
                    motion=frame.motion,
                    shake=frame.shake,
                    novelty=frame.novelty,
                )
                for frame in measured.frames
            ),
        )
        reasons = eliminate_frames(stream, Policy())
        self.assertTrue(all("black_frame" in row for row in reasons))

    def test_busy_footage_is_not_eliminated_as_tripod_dead_time(self):
        measured, rate = _measure(_support.whip_pan())
        stream = FeatureStream(
            media_id="f" * 64,
            rate=rate,
            frames=tuple(
                Frame(luma=f.luma, sharpness=f.sharpness, motion=f.motion, shake=f.shake)
                for f in measured.frames
            ),
        )
        reasons = eliminate_frames(stream, Policy())
        self.assertEqual(sum(1 for row in reasons if "no_motion" in row), 0)


class Determinism(unittest.TestCase):
    def test_two_runs_over_the_same_clip_are_identical(self):
        first, _ = _measure(_support.hard_cut())
        second, _ = _measure(_support.hard_cut())
        self.assertEqual(first.frames, second.frames)
        self.assertEqual(first.signatures, second.signatures)


class Rasters(unittest.TestCase):
    def test_the_analysis_raster_never_upscales(self):
        self.assertEqual(decode.analysis_size(270, 480), (270, 480))
        self.assertEqual(decode.analysis_size(100, 100), (100, 100))

    def test_the_long_edge_is_capped_and_rounding_is_half_up(self):
        self.assertEqual(decode.analysis_size(854, 480), (512, 288))
        self.assertEqual(decode.analysis_size(1920, 1080), (512, 288))
        # 3x1000 -> the short edge is 1.536, which half-up is 2 and
        # half-to-even would make 2 as well; 1000x3 exercises the transpose.
        self.assertEqual(decode.analysis_size(1000, 3), (512, 2))

    def test_a_short_frame_buffer_is_an_error_not_a_partial_measurement(self):
        with self.assertRaises(visual.VisualError):
            visual.analyse_frames([b"\x00" * 10], size=(4, 4), rate=30.0)


if __name__ == "__main__":
    unittest.main()
