"""Moment scoring v1, tested for the behaviours that decide what a viewer sees.

The interesting cases are never "does the arithmetic work". They are the ones a
plausible implementation gets quietly wrong:

  * eliminated footage that still competes because it was scored low rather than
    gated out;
  * a window penalised for having been measured less, or a faceless action shot
    penalised for containing no smile to measure;
  * two peaks half a second apart emitted as two near-identical clips;
  * a boundary that lands in the middle of a spoken word — the one error class a
    viewer perceives instantly;
  * output that differs between runs because a dict or a set decided an order.

Written as unittest.TestCase so the same file runs under
`python3 -m unittest discover` (what scripts/ci/run-workspace-check.mjs uses)
and under pytest (the convention in CLAUDE.md).
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import random
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from memory_engine_story.moments import (  # noqa: E402
    ELIMINATION_ORDER,
    FEATURE_SET_ID,
    AudioEvent,
    SnapPoint,
    FeatureStream,
    Frame,
    MomentIdUnavailable,
    Policy,
    PolicyError,
    Scorer,
    Shot,
    StreamError,
    WeightError,
    Weights,
    Word,
    aggregate,
    eliminate_frames,
    explain,
    fuse_window,
    moment_id,
    plan_moments,
    snap_points,
)
# Private, and tested directly on purpose: the public path cannot isolate the
# boundary picker, and the trade-off it encodes is a stated design decision.
from memory_engine_story.moments import _pick_snap  # noqa: E402

REPO = Path(__file__).resolve().parents[3]
SCHEMA_DIR = REPO / "contracts" / "schemas"

MEDIA_ID = "a" * 64
RATE = 30.0


def fake_hasher(payload: bytes) -> str:
    """A deterministic stand-in for BLAKE3.

    Explicitly injected rather than defaulted to, because the production default
    refuses to substitute a different hash for a field the contract defines as a
    BLAKE3 content address. A test double is allowed to; a pipeline is not.
    """
    return hashlib.blake2b(payload, digest_size=32).hexdigest()


def good(**overrides) -> Frame:
    fields = dict(
        motion=0.60, shake=0.10, exposure_stability=0.90, sharpness=0.80,
        luma=0.50, clipped_highlights=0.02, clipped_shadows=0.02,
        face_presence=1.0, max_face_area_ratio=0.12, smile_intensity=0.70,
        loudness_lufs=-18.0, speech=0.20, noise=0.10, novelty=0.60,
    )
    fields.update(overrides)
    return Frame(**fields)


def dull(**overrides) -> Frame:
    """Usable footage that is simply not interesting. Scores below the floor,
    but is NOT eliminated -- the distinction the whole module rests on."""
    fields = dict(
        motion=0.03, shake=0.40, exposure_stability=0.30, sharpness=0.05,
        luma=0.45, clipped_highlights=0.01, clipped_shadows=0.01,
        face_presence=0.0, max_face_area_ratio=None, smile_intensity=None,
        loudness_lufs=-65.0, speech=0.0, noise=0.10, novelty=0.02,
    )
    fields.update(overrides)
    return Frame(**fields)


def stream(frames, **overrides) -> FeatureStream:
    fields = dict(media_id=MEDIA_ID, rate=RATE, frames=list(frames))
    fields.update(overrides)
    return FeatureStream(**fields)


def plan(frames, **overrides):
    stream_kwargs = overrides.pop("stream_kwargs", {})
    return plan_moments(stream(frames, **stream_kwargs), id_hasher=fake_hasher, **overrides)


def span(record) -> tuple[float, float]:
    start = record["source_range"]["start_time"]["value"]
    return start, start + record["source_range"]["duration"]["value"]


# --------------------------------------------------------------------------


class TestElimination(unittest.TestCase):
    """Junk is gated out, not scored low.

    A low score still competes and still wins when the pool is bad enough --
    exactly the tail of a GoPro card, where the alternative to a pocket shot is
    another pocket shot.
    """

    def test_classical_reasons_are_detected_per_frame(self):
        cases = {
            "black_frame": good(luma=0.0),
            "lens_obstructed": good(lens_obstructed=True),
            "blown_exposure": good(clipped_highlights=0.9),
            "crushed_exposure": good(clipped_shadows=0.8),
            "shake": good(shake=0.9),
            "wind_noise_dominant": good(noise=0.95),
        }
        for reason, frame in cases.items():
            with self.subTest(reason=reason):
                found = eliminate_frames(stream([frame]), Policy())[0]
                self.assertIn(reason, found)

    def test_thresholds_are_inclusive_at_the_boundary(self):
        """A frame exactly at the threshold is eliminated.

        Off-by-one on a threshold is invisible in every test that uses a value
        comfortably past it, and elimination thresholds are tuned by moving them
        onto real measurements -- which is exactly when the boundary case stops
        being hypothetical.
        """
        policy = Policy()
        cases = {
            "black_frame": good(luma=policy.black_luma_max),
            "blown_exposure": good(clipped_highlights=policy.blown_highlight_max),
            "crushed_exposure": good(clipped_shadows=policy.crushed_shadow_max),
            "shake": good(shake=policy.shake_max),
            "wind_noise_dominant": good(noise=policy.noise_max),
        }
        for reason, frame in cases.items():
            with self.subTest(reason=reason):
                self.assertIn(reason, eliminate_frames(stream([frame]), policy)[0])

    def test_pocket_footage_is_dark_and_textureless_together(self):
        """Either alone is a legitimate shot; only both together is a pocket."""
        policy = Policy()
        dark_only = eliminate_frames(stream([good(luma=0.03, sharpness=0.8)]), policy)[0]
        soft_only = eliminate_frames(stream([good(luma=0.5, sharpness=0.01)]), policy)[0]
        both = eliminate_frames(stream([good(luma=0.05, sharpness=0.01)]), policy)[0]
        self.assertNotIn("lens_obstructed", soft_only)
        self.assertNotIn("lens_obstructed", dark_only)
        self.assertIn("lens_obstructed", both)

    def test_black_frame_does_not_also_report_crushed_exposure(self):
        found = eliminate_frames(stream([good(luma=0.0, clipped_shadows=1.0)]), Policy())[0]
        self.assertEqual({"black_frame"}, set(found))

    def test_tripod_dead_time_needs_to_be_sustained(self):
        policy = Policy()
        brief = [good()] * 30 + [good(motion=0.0)] * 30 + [good()] * 30
        sustained = [good()] * 30 + [good(motion=0.0)] * 120 + [good()] * 30
        self.assertFalse(
            any("no_motion" in row for row in eliminate_frames(stream(brief), policy)),
            "a one-second pause is a breath, not dead time",
        )
        found = eliminate_frames(stream(sustained), policy)
        self.assertTrue(any("no_motion" in row for row in found))
        self.assertEqual(120, sum(1 for row in found if "no_motion" in row))

    def test_a_locked_off_interview_is_not_dead_time(self):
        """The one case where a static frame IS the point."""
        frames = [good()] * 30 + [good(motion=0.0, speech=0.9)] * 120 + [good()] * 30
        found = eliminate_frames(stream(frames), Policy())
        self.assertFalse(any("no_motion" in row for row in found))

    def test_eliminated_samples_never_appear_inside_a_kept_moment(self):
        """The load-bearing invariant: eliminated footage cannot compete."""
        frames = [good()] * 90 + [good(shake=0.95)] * 60 + [good()] * 90
        result = plan(frames)
        eliminated = [bool(row) for row in eliminate_frames(stream(frames), Policy())]
        self.assertTrue(result.moments)
        for moment in result.moments:
            lo, hi = span(moment.record)
            for index in range(int(math.floor(lo)), int(math.ceil(hi))):
                self.assertFalse(
                    eliminated[index],
                    f"sample {index} was eliminated and still ended up in a moment",
                )

    def test_eliminated_regions_are_emitted_as_records_not_dropped(self):
        frames = [good()] * 90 + [good(luma=0.0)] * 60 + [good()] * 90
        result = plan(frames)
        black = [
            m for m in result.eliminated
            if "black_frame" in m.record["elimination"]["reasons"]
        ]
        self.assertEqual(1, len(black), "the black region is one merged record")
        record = black[0].record
        self.assertEqual((90, 150), span(record))
        self.assertEqual("classical", record["elimination"]["stage"])
        self.assertIsNone(record["safe_trim"], "there is nothing here to trim to")
        self.assertEqual([], record["snap_points"], "no cut positions inside junk")
        self.assertTrue(black[0].eliminated)

    def test_reason_order_is_canonical_not_alphabetical_or_set_order(self):
        """These three reasons order differently under every plausible rule, so
        the assertion cannot pass by coincidence: alphabetically it would be
        blown_exposure, lens_obstructed, shake."""
        broken = good(lens_obstructed=True, clipped_highlights=0.9, shake=0.99)
        frames = [good()] * 60 + [broken] * 60
        reasons = [
            m.record["elimination"]["reasons"] for m in plan(frames).eliminated
            if m.record["elimination"]["stage"] == "classical"
        ]
        self.assertEqual(1, len(reasons))
        self.assertEqual(["lens_obstructed", "blown_exposure", "shake"], reasons[0])
        ranks = [ELIMINATION_ORDER.index(name) for name in reasons[0]]
        self.assertEqual(sorted(ranks), ranks)

    def test_a_clean_run_too_short_to_hold_a_moment_is_marked_too_short(self):
        # 5 clean frames (0.17s) between two eliminated blocks: below the 0.6s
        # floor, so it can never be a moment and must not silently vanish.
        frames = [good(shake=0.99)] * 60 + [good()] * 5 + [good(shake=0.99)] * 60
        result = plan(frames)
        self.assertEqual((), result.moments)
        reasons = {
            reason for m in result.eliminated
            for reason in m.record["elimination"]["reasons"]
        }
        self.assertIn("too_short", reasons)
        self.assertEqual(125, result.eliminated_samples)
        self.assertEqual(1.0, result.eliminated_fraction)

    def test_below_floor_candidates_are_recorded_with_a_reason(self):
        result = plan([dull()] * 300)
        self.assertEqual((), result.moments)
        self.assertTrue(result.eliminated)
        stages = {m.record["elimination"]["stage"] for m in result.eliminated}
        self.assertEqual({"fusion"}, stages)
        for moment in result.eliminated:
            self.assertEqual(["below_score_floor"], moment.record["elimination"]["reasons"])
            self.assertIn("below_score_floor", explain(moment))

    def test_dull_footage_is_not_eliminated_classically(self):
        """Below the score floor is a judgement; elimination is a gate. Dull
        footage must stay available for a thin pool."""
        found = eliminate_frames(stream([dull()] * 60), Policy())
        self.assertTrue(all(not row for row in found))


class TestFusion(unittest.TestCase):
    """Same shape as packages/ranking-engine/fusion.py, deliberately."""

    def test_missing_signals_renormalise_rather_than_default(self):
        full = aggregate(stream([good()] * 60), 0, 60, Policy())
        partial = {k: v for k, v in full.items() if k in {"motion_energy", "sharpness"}}
        fused_full = fuse_window(full, Weights())
        fused_partial = fuse_window(partial, Weights())
        self.assertLess(fused_partial.coverage, fused_full.coverage)
        # Defaulting missing signals to 0 would drag this far below the two
        # measured values; renormalising keeps it between them.
        self.assertGreaterEqual(fused_partial.value, min(partial.values()) - 1e-9)
        self.assertLessEqual(fused_partial.value, max(partial.values()) + 1e-9)

    def test_a_measured_zero_is_not_the_same_as_a_missing_signal(self):
        measured_zero = fuse_window({"motion_energy": 0.0, "sharpness": 0.8}, Weights())
        missing = fuse_window({"sharpness": 0.8}, Weights())
        self.assertLess(measured_zero.value, missing.value)
        self.assertGreater(measured_zero.coverage, missing.coverage)

    def test_no_face_makes_smile_not_applicable_rather_than_unmeasured(self):
        """A faceless action shot is not an under-measured portrait.

        Counting a smile weight it can never earn would cap every landscape
        below the comparability bar -- the same defect the photo fusion had with
        face_quality.
        """
        values = aggregate(stream([good(face_presence=0.0, smile_intensity=None,
                                        max_face_area_ratio=None)] * 60), 0, 60, Policy())
        fused = fuse_window(values, Weights())
        self.assertNotIn("smile_intensity", fused.measured)
        self.assertEqual(1.0, fused.coverage)

        # Face detection not run at all: the same absent smile now DOES count
        # against coverage, because the window really is incompletely measured.
        unrun = dict(values)
        unrun.pop("face_presence")
        self.assertLess(fuse_window(unrun, Weights()).coverage, 1.0)

    def test_coverage_is_reported_not_folded_into_the_value(self):
        one = fuse_window({"sharpness": 0.8}, Weights())
        two = fuse_window({"sharpness": 0.8, "stability": 0.8}, Weights())
        self.assertEqual(one.value, two.value)
        self.assertNotEqual(one.coverage, two.coverage)

    def test_scores_measured_differently_are_not_comparable(self):
        a = fuse_window({"sharpness": 0.8, "stability": 0.7}, Weights())
        b = fuse_window({"sharpness": 0.8, "face_presence": 0.7}, Weights())
        self.assertFalse(a.comparable_with(b))
        self.assertTrue(a.comparable_with(fuse_window(
            {"sharpness": 0.1, "stability": 0.2}, Weights())))

    def test_negative_weights_are_refused(self):
        with self.assertRaises(WeightError):
            fuse_window({"sharpness": 0.8}, Weights(sharpness=-0.5))
        with self.assertRaises(WeightError):
            Weights(**{name: 0.0 for name in (
                "motion_energy", "motion_peak", "stability", "exposure_stability",
                "sharpness", "face_presence", "smile_intensity", "audio_energy",
                "speech_presence", "novelty")}).validate()

    def test_weight_digest_is_portable_and_sensitive(self):
        base = Weights()
        self.assertEqual(base.digest(), Weights().digest())
        self.assertNotEqual(base.digest(), Weights(sharpness=0.13).digest())
        # weights_id is provenance, not a weight: renaming a profile must not
        # change the digest of the numbers it holds.
        self.assertEqual(base.digest(), Weights(weights_id="other").digest())

    def test_the_default_digest_is_a_fixed_value(self):
        """A golden digest, pinning the ENCODING and not just the numbers.

        The digest is a cross-language identifier: the desktop shell and the
        pipeline must agree on it. Python's repr of a float is not JavaScript's,
        so an encoding that merely round-trips within Python would let the same
        profile read as two different fusions across the boundary. Only a pinned
        value catches that, because both sides of a repr-based digest are
        self-consistent.
        """
        self.assertEqual("a6991050e7376d855de94425414037d6", Weights().digest())

    def test_policy_seconds_resolve_to_fixed_sample_counts(self):
        """Rounding is half-up and explicit: 0.25s at 30fps is 7.5 samples, and
        Python's half-to-even round() would make that 8 or 7 depending on the
        neighbouring integer -- a policy value silently changing meaning with
        the frame rate."""
        grid = Policy().grid(30.0)
        self.assertEqual(
            (60, 8, 45, 18, 180, 30, 90, 15),
            (grid.window, grid.hop, grid.separation, grid.min_moment,
             grid.max_moment, grid.extend, grid.dead_time, grid.hook),
        )

    def test_motion_peak_is_the_peak(self):
        frames = [good(motion=0.1)] * 59 + [good(motion=0.9)]
        values = aggregate(stream(frames), 0, 60, Policy())
        self.assertEqual(0.9, values["motion_peak"])
        self.assertNotEqual(values["motion_peak"], values["motion_energy"])

    def test_loudness_maps_onto_the_unit_range_at_its_declared_endpoints(self):
        """-70 LUFS is digital silence and 0 is full scale. The mapping is
        stated once so nothing downstream re-derives it differently."""
        for lufs, expected in ((-70.0, 0.0), (-35.0, 0.5), (0.0, 1.0)):
            with self.subTest(lufs=lufs):
                values = aggregate(stream([good(loudness_lufs=lufs)] * 60), 0, 60, Policy())
                self.assertAlmostEqual(expected, values["audio_energy"], places=6)
                self.assertEqual(lufs, values["loudness_lufs"])

    def test_a_measured_zero_face_presence_still_counts_against_the_score(self):
        """Only the SMILE becomes not-applicable when there is no face. Face
        presence itself was measured, and measured zero, which is a real and
        important signal for a family library."""
        faceless = fuse_window({"sharpness": 1.0, "face_presence": 0.0}, Weights())
        unmeasured = fuse_window({"sharpness": 1.0}, Weights())
        self.assertIn("face_presence", faceless.measured)
        self.assertLess(faceless.value, unmeasured.value)

    def test_feature_map_only_holds_measured_signals(self):
        fused = fuse_window({"sharpness": 0.8}, Weights())
        self.assertEqual({"sharpness": 0.8}, fused.as_feature_map())
        self.assertEqual(FEATURE_SET_ID, fused.feature_set_id)

    def test_peak_signals_bypass_the_sample_coverage_gate(self):
        """One frame where a child actually laughs IS the measurement."""
        frames = [good(smile_intensity=None)] * 59 + [good(smile_intensity=0.95)]
        values = aggregate(stream(frames), 0, 60, Policy())
        self.assertEqual(0.95, values["smile_intensity"])

    def test_smile_is_aggregated_by_peak_not_by_mean(self):
        """A mean would dilute the one frame where the laugh happens; here it
        would report 0.30 for a window containing a 0.90 laugh."""
        frames = (
            [good(smile_intensity=0.1, max_face_area_ratio=0.05)] * 40
            + [good(smile_intensity=0.9, max_face_area_ratio=0.40)] * 20
        )
        values = aggregate(stream(frames), 0, 60, Policy())
        self.assertEqual(0.9, values["smile_intensity"])
        self.assertEqual(0.40, values["max_face_area_ratio"])
        # The mean-aggregated signals in the same window still average, so this
        # is a property of the signal and not of the aggregator being broken.
        self.assertAlmostEqual(0.60, values["motion_energy"], places=6)

    def test_a_mean_over_two_of_sixty_samples_is_not_a_measurement(self):
        frames = [good(sharpness=None)] * 58 + [good(sharpness=1.0)] * 2
        values = aggregate(stream(frames), 0, 60, Policy())
        self.assertNotIn("sharpness", values)


class TestPeakSelection(unittest.TestCase):
    def test_a_long_boring_stretch_yields_one_moment_at_the_good_second(self):
        frames = [dull()] * 300 + [good()] * 30 + [dull()] * 300
        result = plan(frames)
        self.assertEqual(1, len(result.moments), "one good second is one moment")
        lo, hi = span(result.moments[0].record)
        self.assertLessEqual(lo, 300)
        self.assertGreaterEqual(hi, 330)

    def test_two_peaks_half_a_second_apart_are_one_moment(self):
        frames = [dull()] * 300
        for index in range(150, 159):
            frames[index] = good()
        for index in range(165, 174):
            frames[index] = good()
        result = plan(frames)
        self.assertEqual(1, len(result.moments))

    def test_peaks_far_apart_are_two_moments(self):
        frames = [dull()] * 600
        for index in range(100, 130):
            frames[index] = good()
        for index in range(400, 430):
            frames[index] = good()
        result = plan(frames)
        self.assertEqual(2, len(result.moments))

    def test_moments_never_overlap(self):
        frames = [good()] * 900
        result = plan(frames)
        self.assertGreater(len(result.moments), 1)
        self.assertNoOverlap(result.moments)

    def test_an_extension_that_would_overlap_is_trimmed_back(self):
        """Boundary snapping pulls moments outward, and two moments extended
        toward each other will collide. The footage is still good -- it just
        cannot be claimed twice -- so the later moment is trimmed rather than
        dropped, and it loses its snapped in-point in the process.

        The word layout is built so the best out-point of the first moment sits
        LATER than the best in-point of the second: an "out"-only snap at 85 and
        an "in"-only snap at 35, with nothing usable by both near the window
        edge at 60.
        """
        words = [Word("a", 8.0, 18.0), Word("b", 35.0, 45.0),
                 Word("c", 55.0, 85.0), Word("d", 100.0, 110.0)]
        result = plan_moments(
            stream([good()] * 300, words=words, language="en-IN"), id_hasher=fake_hasher
        )
        spans = [span(m.record) for m in result.moments]
        self.assertEqual((8, 85), spans[0])
        self.assertEqual((85, 110), spans[1], "the second moment starts where the first ends")
        self.assertIsNone(result.moments[1].in_snap_kind,
                          "a trimmed in-point is not a snapped one and must not claim to be")
        self.assertNoOverlap(result.moments)

    def assertNoOverlap(self, moments):
        previous = None
        for moment in moments:
            lo, hi = span(moment.record)
            if previous is not None:
                self.assertGreaterEqual(lo, previous, "two moments claim the same frames")
            previous = hi

    def test_a_clip_shorter_than_the_window_is_still_one_moment(self):
        """A moment that is genuinely the whole clip is a normal outcome."""
        result = plan([good()] * 25)
        self.assertEqual(1, len(result.moments))
        self.assertEqual((0, 25), span(result.moments[0].record))

    def test_a_moment_never_crosses_a_shot_boundary(self):
        frames = [good()] * 300
        shots = [Shot("shot-0001", 0, 150), Shot("shot-0002", 150, 300)]
        result = plan(frames, stream_kwargs={"shots": shots})
        self.assertTrue(result.moments)
        for moment in result.moments:
            lo, hi = span(moment.record)
            self.assertIn(moment.record["shot_id"], {"shot-0001", "shot-0002"})
            if moment.record["shot_id"] == "shot-0001":
                self.assertLessEqual(hi, 150)
            else:
                self.assertGreaterEqual(lo, 150)

    def test_the_emitted_score_describes_the_emitted_range(self):
        """Snapping moves the boundaries, so the window's score is no longer a
        measurement of what the record covers.

        The burst here is deliberately shorter than the window: the moment gets
        snapped onto the action and its score RISES above the window score that
        selected it. A record whose score was measured on footage the record
        does not cover is exactly the kind of silent lie this file exists to
        prevent, so both numbers are kept and both are checked.
        """
        frames = [good(motion=0.05) for _ in range(300)]
        for index in range(100, 140):
            frames[index] = good(motion=0.95)
        result = plan(frames)
        snapped = [m for m in result.moments if span(m.record) == (100, 140)]
        self.assertEqual(1, len(snapped), [span(m.record) for m in result.moments])
        self.assertGreater(snapped[0].fusion.value, snapped[0].peak_value)
        for moment in result.moments:
            lo, hi = span(moment.record)
            values = aggregate(stream(frames), int(lo), int(hi), Policy())
            expected = fuse_window(values, Weights()).value
            self.assertEqual(expected, moment.record["scores"]["moment_score"]["value"])
            self.assertEqual(expected, moment.fusion.value)

    def test_the_tail_of_a_segment_is_always_scored(self):
        """The window steps by the hop, and the last hop rarely lands exactly on
        the end of a segment. Without an explicit final window the last fraction
        of every clean run is never a candidate -- and the best second of a clip
        is very often its last."""
        frames = [dull()] * 45 + [good()] * 60
        result = plan(frames, policy=Policy(max_extend_seconds=0.0))
        self.assertEqual([(45, 105)], [span(m.record) for m in result.moments])

    def test_a_segment_too_short_for_a_moment_produces_no_candidate(self):
        """Shot splitting can create a segment shorter than the minimum even
        when the clean run is long. Scoring it produces a moment that then has
        to be thrown away by the planner -- work done to produce a record that
        says nothing."""
        frames = [good()] * 300
        shots = [Shot("shot-0001", 0, 10), Shot("shot-0002", 10, 300)]
        result = plan(frames, stream_kwargs={"shots": shots})
        self.assertEqual(
            [], [m for m in result.eliminated
                 if m.record["elimination"]["stage"] == "planner"],
        )
        for moment in result.moments:
            lo, hi = span(moment.record)
            self.assertGreaterEqual(hi - lo, 18)
            self.assertEqual("shot-0002", moment.record["shot_id"])

    def test_a_moment_never_straddles_a_shot_boundary_even_when_the_action_does(self):
        """The action is deliberately placed across the cut, so the best window
        by score would span it if segments were not intersected with shots."""
        frames = [good(motion=0.05) for _ in range(300)]
        for index in range(140, 180):
            frames[index] = good(motion=0.95)
        shots = [Shot("shot-0001", 0, 160), Shot("shot-0002", 160, 300)]
        result = plan(frames, stream_kwargs={"shots": shots})
        self.assertTrue(result.moments)
        for moment in result.moments:
            lo, hi = span(moment.record)
            self.assertFalse(lo < 160 < hi, f"moment ({lo},{hi}) crosses the cut at 160")
            self.assertEqual("shot-0001" if hi <= 160 else "shot-0002",
                             moment.record["shot_id"])

    def test_a_moment_is_never_shorter_than_the_minimum(self):
        """A snapped in-point close to the window's end leaves less than the
        minimum behind it; the out-point is pushed out rather than emitting a
        flash frame.

        The generous extension is what makes the case reachable: an in-point may
        then be snapped further forward than the window has frames left.
        """
        frames = [good(motion=0.1) for _ in range(300)]
        for index in range(45, 48):
            frames[index] = good(motion=0.6)
        result = plan(frames, policy=Policy(max_extend_seconds=1.5))
        self.assertTrue(result.moments)
        durations = [hi - lo for lo, hi in (span(m.record) for m in result.moments)]
        self.assertEqual((45, 63), span(result.moments[0].record))
        self.assertEqual("motion_onset", result.moments[0].in_snap_kind)
        self.assertTrue(all(d >= 18 for d in durations), durations)

    def test_a_moment_is_never_longer_than_the_maximum(self):
        """The out-point search is already bounded by the maximum, so this is
        about the FALLBACK: with no snap in range the window's own end is used,
        and the window can be longer than the maximum a policy allows."""
        result = plan([good()] * 300, policy=Policy(max_moment_seconds=1.5))
        durations = [hi - lo for lo, hi in (span(m.record) for m in result.moments)]
        self.assertTrue(durations)
        self.assertEqual({45}, set(durations),
                         "a 60-frame window under a 45-frame ceiling must be clamped")

    def test_windows_with_nothing_measured_are_not_ranked(self):
        result = plan([Frame()] * 300)
        self.assertEqual((), result.moments)
        self.assertEqual(0, result.candidates_considered)


class TestSnapSelection(unittest.TestCase):
    """The boundary picker, in isolation.

    Tested directly because the end-to-end path cannot isolate it: which snap
    wins depends on the score, which depends on the same features that produced
    the snaps. The trade-off it encodes is a stated design decision and deserves
    a test that fails when the decision is reversed.
    """

    def pick(self, snaps, prefer, directions=("in", "both"), policy=None):
        return _pick_snap(
            tuple(sorted(snaps, key=lambda s: s.time)),
            min(s.time for s in snaps), max(s.time for s in snaps),
            frozenset(directions), prefer, policy or Policy(), RATE,
        )

    def test_a_slightly_weaker_snap_nearby_beats_a_stronger_one_a_second_away(self):
        near = SnapPoint(60.0, "motion_onset", 0.65, "in")
        far = SnapPoint(90.0, "shot_boundary", 0.85, "in")
        self.assertEqual(near, self.pick([near, far], prefer=60.0))

    def test_but_strength_still_leads_when_the_gap_is_wide(self):
        """Cutting on a weak onset is worse than cutting 40ms later on a strong
        one -- the schema's own guidance, and the reason distance is a cost
        rather than a filter."""
        near = SnapPoint(60.0, "motion_onset", 0.30, "in")
        far = SnapPoint(66.0, "shot_boundary", 0.95, "in")
        self.assertEqual(far, self.pick([near, far], prefer=60.0))

    def test_direction_is_a_hard_filter_not_a_preference(self):
        out_only = SnapPoint(60.0, "motion_offset", 0.99, "out")
        usable = SnapPoint(75.0, "motion_onset", 0.20, "in")
        self.assertEqual(usable, self.pick([out_only, usable], prefer=60.0))
        self.assertIsNone(self.pick([out_only], prefer=60.0))


class TestSnapPoints(unittest.TestCase):
    """A cut through a word is a different class of bug from a wrong score."""

    def speech_stream(self) -> FeatureStream:
        frames = [good() for _ in range(300)]
        for index in range(120, 160):
            frames[index] = good(motion=0.95)
        words = [
            Word("did", 60.0, 72.0),
            Word("you", 72.0, 84.0),
            Word("see", 84.0, 99.0),
            # 1.0s gap here
            Word("that", 129.0, 141.0),
            Word("wave", 141.0, 156.0),
        ]
        return stream(frames, words=words, language="en-IN")

    def test_no_snap_point_lands_strictly_inside_a_word(self):
        source = self.speech_stream()
        points = snap_points(source, Policy())
        self.assertTrue(points)
        for point in points:
            for word in source.words:
                self.assertFalse(
                    word.start < point.time < word.end,
                    f"{point.kind} at {point.time} is inside {word.word!r}",
                )

    def test_a_speech_gap_becomes_a_snap_point(self):
        points = snap_points(self.speech_stream(), Policy())
        gaps = [p for p in points if p.kind == "speech_gap"]
        self.assertEqual(1, len(gaps))
        self.assertTrue(99.0 < gaps[0].time < 129.0)
        self.assertEqual("both", gaps[0].cut_direction)

    def test_a_word_boundary_is_a_cut_position_not_an_interior(self):
        """The mid-word filter has to be STRICT.

        A time equal to a word's start or end is exactly where we want to cut.
        Treating it as inside the word deletes every speech_start and speech_end
        snap, which removes the only certified positions a speech-heavy moment
        has and silently sends the planner back to window edges.
        """
        source = self.speech_stream()
        points = snap_points(source, Policy())
        self.assertIn(60.0, [p.time for p in points if p.kind == "speech_start"])
        self.assertIn(99.0, [p.time for p in points if p.kind == "speech_end"])
        self.assertIn(156.0, [p.time for p in points if p.kind == "speech_end"])
        self.assertEqual(
            ["in"], sorted({p.cut_direction for p in points if p.kind == "speech_start"})
        )

    def test_motion_onsets_are_in_points_and_offsets_are_out_points(self):
        points = snap_points(self.speech_stream(), Policy())
        onsets = [p for p in points if p.kind == "motion_onset"]
        offsets = [p for p in points if p.kind == "motion_offset"]
        self.assertTrue(onsets and offsets)
        self.assertTrue(all(p.cut_direction == "in" for p in onsets))
        self.assertTrue(all(p.cut_direction == "out" for p in offsets))
        self.assertEqual([120.0], [p.time for p in onsets])
        self.assertEqual([160.0], [p.time for p in offsets])

    def test_a_motion_onset_inside_a_word_is_discarded(self):
        """The guarantee in its sharpest form.

        The contract says the planner may only cut on snap points, so leaving a
        mid-word onset in the list is the same as authorising the cut. The
        onset is real -- it is simply not a legal place to cut.
        """
        frames = [good(motion=0.1)] * 300
        for index in range(135, 200):
            frames[index] = good(motion=0.95)
        word = Word("spectacular", 130.0, 145.0)
        without_speech = snap_points(stream(frames), Policy())
        with_speech = snap_points(stream(frames, words=[word], language="en-IN"), Policy())
        self.assertIn(135.0, [p.time for p in without_speech if p.kind == "motion_onset"])
        self.assertEqual([], [p.time for p in with_speech if p.kind == "motion_onset"])

    def test_a_ramp_produces_one_onset_not_one_per_sample(self):
        frames = [good(motion=0.0)] * 30
        for index in range(30, 40):
            frames.append(good(motion=0.05 * (index - 29)))
        frames += [good(motion=0.5)] * 30
        points = [p for p in snap_points(stream(frames), Policy(motion_onset_delta=0.04))
                  if p.kind == "motion_onset"]
        self.assertEqual(1, len(points), f"one onset per ramp, got {[p.time for p in points]}")

    def test_snap_points_on_eliminated_samples_are_dropped(self):
        frames = [good(motion=0.0)] * 60 + [good(motion=0.95, shake=0.99)] * 60
        mask = [bool(row) for row in eliminate_frames(stream(frames), Policy())]
        points = snap_points(stream(frames), Policy(), mask)
        self.assertTrue(all(not mask[int(p.time)] for p in points if int(p.time) < len(mask)))

    def test_shot_boundaries_snap_in_at_the_start_and_out_at_the_end(self):
        source = stream([good()] * 120, shots=[Shot("shot-0001", 0, 120)])
        points = [p for p in snap_points(source, Policy()) if p.kind == "shot_boundary"]
        self.assertEqual([(0.0, "in"), (120.0, "out")],
                         [(p.time, p.cut_direction) for p in points])

    def test_a_shared_shot_boundary_is_usable_in_both_directions(self):
        """Adjacent shots meet on one frame, which is both the out-point of the
        first and the in-point of the second. Deduping without the direction
        collapsed the pair and left the second shot with no certified way in."""
        source = stream(
            [good()] * 240,
            shots=[Shot("shot-0001", 0, 120), Shot("shot-0002", 120, 240)],
        )
        shared = [
            p for p in snap_points(source, Policy())
            if p.kind == "shot_boundary" and p.time == 120.0
        ]
        self.assertEqual({"in", "out"}, {p.cut_direction for p in shared})

    def test_an_audio_rise_below_the_threshold_is_not_an_onset(self):
        quiet = [good(loudness_lufs=-40.0)] * 60 + [good(loudness_lufs=-38.0)] * 60
        loud = [good(loudness_lufs=-40.0)] * 60 + [good(loudness_lufs=-28.0)] * 60
        self.assertEqual(
            [], [p for p in snap_points(stream(quiet), Policy()) if p.kind == "audio_onset"],
            "a 2 dB rise is a level drift, not an event",
        )
        onsets = [p for p in snap_points(stream(loud), Policy()) if p.kind == "audio_onset"]
        self.assertEqual([60.0], [p.time for p in onsets])
        self.assertEqual("both", onsets[0].cut_direction)

    def test_a_brightness_change_cuts_in_both_directions(self):
        frames = [good(luma=0.2)] * 60 + [good(luma=0.8)] * 60
        points = [p for p in snap_points(stream(frames), Policy())
                  if p.kind == "scene_brightness_change"]
        self.assertEqual([60.0], [p.time for p in points])
        self.assertEqual("both", points[0].cut_direction)

    def test_a_plateau_of_equal_rises_still_produces_one_onset(self):
        """Two identical deltas in a row must not cancel each other out. The
        earliest sample of the plateau wins; emitting none is the failure mode
        of a naive strictly-greater test on both sides."""
        # Binary-exact motion values: 0.3 - 0.1 and 0.5 - 0.3 are NOT equal in
        # float, so a plateau built from decimal-looking numbers would not be a
        # plateau at all and the test would pass for the wrong reason.
        frames = ([good(motion=0.125)] * 30 + [good(motion=0.375)]
                  + [good(motion=0.625)] + [good(motion=0.625)] * 30)
        onsets = [p for p in snap_points(stream(frames), Policy())
                  if p.kind == "motion_onset"]
        self.assertEqual([30.0], [p.time for p in onsets])

    def test_a_snap_on_the_last_eliminated_sample_is_still_dropped(self):
        """The mask lookup has to floor, not ceil: a snap sitting on the last
        frame of a junk region would otherwise be tested against the first clean
        frame after it and kept."""
        frames = [good()] * 60 + [good(shake=0.99, motion=0.05)] * 60 + [good()] * 60
        frames[119] = good(shake=0.99, motion=0.95)  # a big onset inside the junk
        mask = [bool(row) for row in eliminate_frames(stream(frames), Policy())]
        self.assertTrue(mask[119])
        points = snap_points(stream(frames), Policy(), mask)
        self.assertEqual([], [p for p in points if p.time == 119.0])

    def test_a_gap_snap_stays_strictly_between_the_words(self):
        """Rounding the midpoint to a whole frame can land on a word boundary
        when the gap is short, and the clamp is what keeps it inside the gap."""
        words = [Word("ek", 10.0, 20.0), Word("do", 20.6, 30.0)]
        points = [
            p for p in snap_points(
                stream([good()] * 120, words=words, language="hi-IN"),
                Policy(speech_gap_seconds=0.01),
            )
            if p.kind == "speech_gap"
        ]
        self.assertEqual(1, len(points))
        self.assertTrue(20.0 < points[0].time < 20.6, points[0].time)

    def test_moment_boundaries_never_land_inside_a_word(self):
        result = plan_moments(self.speech_stream(), id_hasher=fake_hasher)
        self.assertTrue(result.moments)
        for moment in result.moments:
            lo, hi = span(moment.record)
            for word in self.speech_stream().words:
                self.assertFalse(word.start < lo < word.end, f"in-point inside {word.word!r}")
                self.assertFalse(word.start < hi < word.end, f"out-point inside {word.word!r}")

    def test_a_fallback_boundary_is_pushed_out_of_the_word_it_lands_in(self):
        """With no snap point in range the window edge is used -- and the window
        edge knows nothing about words, so it has to be corrected."""
        frames = [good()] * 300
        source = stream(frames, words=[Word("wonderful", 55.5, 70.5)], language="en-IN")
        result = plan_moments(source, policy=Policy(max_extend_seconds=0.0),
                              id_hasher=fake_hasher)
        starts_and_ends = [span(m.record) for m in result.moments]
        self.assertGreaterEqual(len(starts_and_ends), 2)
        first_out = starts_and_ends[0][1]
        second_in = starts_and_ends[1][0]
        self.assertEqual(55, first_out, "out-point floors to the frame before the word")
        self.assertEqual(71, second_in, "in-point ceils to the frame after the word")

    def test_emitted_snap_points_are_inside_the_moment_and_ordered(self):
        result = plan_moments(self.speech_stream(), id_hasher=fake_hasher)
        for moment in result.moments:
            lo, hi = span(moment.record)
            times = [p["time"]["value"] for p in moment.record["snap_points"]]
            self.assertEqual(sorted(times), times, "snap points must be ordered by time")
            for time in times:
                self.assertGreaterEqual(time, lo)
                self.assertLessEqual(time, hi)

    def test_a_moment_prefers_a_strong_onset_over_the_window_edge(self):
        frames = [good(motion=0.02)] * 200
        for index in range(100, 140):
            frames[index] = good(motion=0.95)
        result = plan(frames)
        self.assertTrue(result.moments)
        self.assertEqual("motion_onset", result.moments[0].in_snap_kind)
        self.assertEqual(100, span(result.moments[0].record)[0])


class TestSafeTrimAndTranscript(unittest.TestCase):
    def test_speech_safe_bounds_are_null_without_speech(self):
        result = plan([good()] * 300)
        trim = result.moments[0].record["safe_trim"]
        self.assertIsNone(trim["speech_safe_in"])
        self.assertIsNone(trim["speech_safe_out"])
        self.assertEqual(18, trim["min_duration"]["value"])

    def test_preserve_audio_tail_is_set_when_a_laugh_lands_after_the_cut(self):
        frames = [good()] * 120
        result_without = plan(frames)
        out = span(result_without.moments[0].record)[1]
        self.assertFalse(result_without.moments[0].record["safe_trim"]["preserve_audio_tail"])

        with_event = plan_moments(
            stream(frames, audio_events=[AudioEvent("laughter", 0.9, out + 2)]),
            id_hasher=fake_hasher,
        )
        self.assertTrue(with_event.moments[0].record["safe_trim"]["preserve_audio_tail"])

    def test_a_quiet_event_after_the_cut_is_not_an_l_cut(self):
        frames = [good()] * 120
        out = span(plan(frames).moments[0].record)[1]
        result = plan_moments(
            stream(frames, audio_events=[AudioEvent("wind", 0.99, out + 2)]),
            id_hasher=fake_hasher,
        )
        self.assertFalse(result.moments[0].record["safe_trim"]["preserve_audio_tail"])

    def test_transcript_requires_a_declared_language(self):
        frames = [good()] * 300
        words = [Word("namaste", 40.0, 55.0)]
        without = plan_moments(stream(frames, words=words), id_hasher=fake_hasher)
        self.assertNotIn("transcript", without.moments[0].record)

        with_language = plan_moments(
            stream(frames, words=words, language="hi-IN"), id_hasher=fake_hasher
        )
        transcripts = [
            m.record["transcript"] for m in with_language.moments
            if "transcript" in m.record
        ]
        self.assertTrue(transcripts)
        self.assertEqual("hi-IN", transcripts[0]["language"])
        self.assertEqual("namaste", transcripts[0]["text"])

    def test_audio_events_inside_the_moment_are_carried_into_features(self):
        frames = [good()] * 120
        result = plan_moments(
            stream(frames, audio_events=[AudioEvent("laughter", 0.93, 30.0)]),
            id_hasher=fake_hasher,
        )
        events = result.moments[0].record["features"]["audio"]["events"]
        self.assertEqual(["laughter"], [e["label"] for e in events])
        self.assertEqual(30, events[0]["time"]["value"])

    def test_events_are_emitted_in_time_order_whatever_order_they_arrived_in(self):
        frames = [good()] * 120
        supplied = [
            AudioEvent("cheering", 0.7, 40.0),
            AudioEvent("laughter", 0.9, 10.0),
            AudioEvent("applause", 0.8, 25.0),
        ]
        result = plan_moments(stream(frames, audio_events=supplied), id_hasher=fake_hasher)
        events = result.moments[0].record["features"]["audio"]["events"]
        self.assertEqual([10, 25, 40], [e["time"]["value"] for e in events])

    def test_a_word_starting_at_the_out_point_belongs_to_the_next_moment(self):
        """Half-open ranges, all the way down. A word that starts exactly where
        the cut lands is not in this moment -- including it would put speech in
        the record that the moment does not contain, and would duplicate it in
        the moment that does.
        """
        frames = [good()] * 300
        source = stream(frames, words=[Word("next", 60.0, 70.0)], language="en-IN")
        # Hop chosen so a window edge lands exactly on the word start; boundary
        # snapping is off so the edge is what the moment actually uses.
        result = plan_moments(source, policy=Policy(max_extend_seconds=0.0, hop_seconds=0.2),
                              id_hasher=fake_hasher)
        first, second = result.moments[0], result.moments[1]
        self.assertEqual((0, 60), span(first.record))
        self.assertEqual((60, 120), span(second.record))
        self.assertNotIn("transcript", first.record)
        self.assertEqual("next", second.record["transcript"]["text"])
        # ... and the audio that continues past the cut is flagged for an L-cut.
        self.assertTrue(first.record["safe_trim"]["preserve_audio_tail"])

    def test_speech_starting_well_after_the_cut_is_not_an_l_cut(self):
        frames = [good()] * 300
        source = stream(frames, words=[Word("later", 90.0, 100.0)], language="en-IN")
        result = plan_moments(source, policy=Policy(max_extend_seconds=0.0),
                              id_hasher=fake_hasher)
        self.assertEqual((0, 60), span(result.moments[0].record))
        self.assertFalse(result.moments[0].record["safe_trim"]["preserve_audio_tail"])

    def test_the_representative_frame_is_the_best_frame_not_the_first(self):
        frames = [good(sharpness=0.2)] * 120
        frames[75] = good(sharpness=1.0)
        record = plan(frames, policy=Policy(max_extend_seconds=0.0)).moments[0].record
        self.assertEqual(75, record["features"]["representative_frame_time"]["value"])

    def test_the_representative_frame_breaks_ties_to_the_earliest(self):
        record = plan([good()] * 120).moments[0].record
        lo = span(record)[0]
        self.assertEqual(lo, record["features"]["representative_frame_time"]["value"])

    def test_emotional_peak_rises_with_a_laugh(self):
        frames = [good()] * 120
        plain = plan(frames).moments[0].record["scores"]["emotional_peak"]["value"]
        laughed = plan_moments(
            stream(frames, audio_events=[AudioEvent("laughter", 1.0, 30.0)]),
            id_hasher=fake_hasher,
        ).moments[0].record["scores"]["emotional_peak"]["value"]
        self.assertGreater(laughed, plain)

    def test_hook_potential_punishes_a_slow_build(self):
        """Same footage, same length, energy at the front or at the back.

        Boundary snapping is disabled here on purpose: with it on, the motion
        onset in the slow-build clip pulls the moment's in-point onto the action
        and the two moments stop being the same comparison. That behaviour is
        correct and is covered separately; this test is about the score.
        """
        front = [good(motion=0.9)] * 30 + [good(motion=0.1)] * 30
        back = [good(motion=0.1)] * 30 + [good(motion=0.9)] * 30
        fixed = Policy(max_extend_seconds=0.0)
        front_hook = plan(front, policy=fixed).moments[0].record["scores"]["hook_potential"]
        back_hook = plan(back, policy=fixed).moments[0].record["scores"]["hook_potential"]
        self.assertEqual((0, 60), span(plan(back, policy=fixed).moments[0].record))
        self.assertGreater(front_hook["value"], back_hook["value"])

    def test_the_build_penalty_is_separate_from_the_lead_measurement(self):
        """Identical first half-second, different remainder.

        Both clips open the same way, so the lead aggregate is identical and any
        difference can only come from the slow-build penalty. Without it, a clip
        whose energy all arrives after the hook window scores as good a hook as
        one that is energetic from the start.
        """
        fixed = Policy(max_extend_seconds=0.0)
        steady = [good(motion=0.5)] * 60
        build = [good(motion=0.5)] * 15 + [good(motion=1.0)] * 45
        steady_hook = plan(steady, policy=fixed).moments[0].record["scores"]["hook_potential"]
        build_hook = plan(build, policy=fixed).moments[0].record["scores"]["hook_potential"]
        self.assertGreater(steady_hook["value"], build_hook["value"])
        # ... while the overall score moves the other way, which is the point:
        # hook_potential is not a second copy of moment_score.
        self.assertGreater(
            plan(build, policy=fixed).moments[0].fusion.value,
            plan(steady, policy=fixed).moments[0].fusion.value,
        )

    def test_narrative_value_stays_null_for_a_local_pass(self):
        scores = plan([good()] * 120).moments[0].record["scores"]
        self.assertIsNone(scores["narrative_value"])
        self.assertEqual("local_fusion", scores["source"])
        self.assertTrue(scores["fusion_weights_version"].startswith("moment-default-v1@"))


class TestContractConformance(unittest.TestCase):
    """Every record this module emits must validate against the schema it claims
    to populate. The package that only referenced its contract in prose is the
    defect this test exists to prevent."""

    @classmethod
    def setUpClass(cls):
        try:
            from jsonschema import Draft202012Validator
            from referencing import Registry, Resource
        except ImportError as exc:  # pragma: no cover - environment dependent
            raise unittest.SkipTest(f"jsonschema/referencing unavailable: {exc}")
        documents = {
            path.name: json.loads(path.read_text(encoding="utf-8"))
            for path in sorted(SCHEMA_DIR.glob("*.schema.json"))
        }
        registry = Registry().with_resources(
            [(name, Resource.from_contents(doc)) for name, doc in documents.items()]
        )
        cls.validator = Draft202012Validator(
            documents["moment-record.schema.json"], registry=registry
        )

    def all_records(self):
        frames = [good()] * 120 + [good(shake=0.99)] * 60 + [dull()] * 120 + [good()] * 90
        for index in range(300, 320):
            frames[index] = good(motion=0.95)
        source = FeatureStream(
            media_id=MEDIA_ID,
            rate=59.94005994005994,
            frames=frames,
            start_value=3049200,
            shots=[Shot("shot-0041", 0, 200), Shot("shot-0042", 200, 390)],
            words=[Word("look", 3049260.0, 3049275.0), Word("there", 3049340.0, 3049360.0)],
            audio_events=[AudioEvent("laughter", 0.93, 3049330.0)],
            language="en-IN",
            proxy_rate=30.0,
            proxy_start_value=0.0,
        )
        result = plan_moments(
            source,
            id_hasher=fake_hasher,
            run_id="run-fusion-0512",
            created_at="2026-03-15T12:21:44+05:30",
            model_runs=[{
                "run_id": "run-fusion-0512",
                "model": {
                    "model_id": "moment-fusion-linear",
                    "version": "1.0.0",
                    "weights_blake3": "f" * 64,
                },
                "ran_at": "2026-03-15T12:21:40+05:30",
            }],
        )
        return result

    def test_every_record_validates(self):
        result = self.all_records()
        self.assertTrue(result.moments)
        self.assertTrue(result.eliminated)
        for record in result.records:
            errors = sorted(self.validator.iter_errors(record), key=lambda e: e.path)
            self.assertEqual(
                [], [f"{list(e.path)}: {e.message}" for e in errors],
                f"record {record['moment_id'][:8]} failed validation",
            )

    def test_records_are_time_ordered(self):
        records = self.all_records().records
        starts = [r["source_range"]["start_time"]["value"] for r in records]
        self.assertEqual(sorted(starts), starts)

    def test_proxy_range_is_the_source_range_converted_by_rate(self):
        """A 59.94fps source analysed against a 30fps proxy: the proxy frame
        number is roughly half the source offset, and a mapping that forgot the
        rate would look plausible near the start of the clip and be minutes out
        at the end."""
        factor = 30.0 / 59.94005994005994
        result = self.all_records()
        checked = 0
        for moment in result.moments:
            record = moment.record
            offset = record["source_range"]["start_time"]["value"] - 3049200
            self.assertEqual(
                round(offset * factor), record["proxy_range"]["start_time"]["value"]
            )
            self.assertEqual(
                max(1, round(record["source_range"]["duration"]["value"] * factor)),
                record["proxy_range"]["duration"]["value"],
            )
            self.assertEqual(30.0, record["proxy_range"]["duration"]["rate"])
            checked += offset > 0
        self.assertGreater(checked, 0, "no moment started past the clip head")

    def test_a_coarse_proxy_never_yields_a_zero_length_range(self):
        """A 1fps keyframe-index proxy: a two-second moment is two proxy frames,
        and a shorter one rounds toward zero. A zero-length range is not a
        cheaper range, it is an empty one, and a re-score against it would read
        no frames and report the moment as unmeasurable."""
        result = plan_moments(
            stream([good()] * 20, proxy_rate=0.5, proxy_start_value=0.0),
            id_hasher=fake_hasher,
        )
        self.assertTrue(result.records)
        for record in result.records:
            self.assertLess(record["source_range"]["duration"]["value"] * (0.5 / RATE), 0.5,
                            "this moment is supposed to round toward zero")
            self.assertGreaterEqual(record["proxy_range"]["duration"]["value"], 1)

    def test_proxy_range_is_null_without_a_mapping(self):
        self.assertIsNone(plan([good()] * 120).moments[0].record["proxy_range"])

    def test_times_are_integral_where_the_grid_allows(self):
        for record in self.all_records().records:
            value = record["source_range"]["start_time"]["value"]
            self.assertIsInstance(value, int, "video-track times should stay integral")

    def test_created_at_is_omitted_rather_than_invented(self):
        record = plan([good()] * 120).moments[0].record
        self.assertNotIn("created_at", record)


class TestIdentity(unittest.TestCase):
    def test_id_changes_with_range_and_with_scorer_version(self):
        base = moment_id(MEDIA_ID, 100.0, 60.0, RATE, Scorer(), fake_hasher)
        self.assertEqual(base, moment_id(MEDIA_ID, 100.0, 60.0, RATE, Scorer(), fake_hasher))
        self.assertNotEqual(base, moment_id(MEDIA_ID, 101.0, 60.0, RATE, Scorer(), fake_hasher))
        self.assertNotEqual(base, moment_id(MEDIA_ID, 100.0, 61.0, RATE, Scorer(), fake_hasher))
        self.assertNotEqual(
            base, moment_id(MEDIA_ID, 100.0, 60.0, RATE, Scorer(version="1.0.1"), fake_hasher)
        )
        self.assertNotEqual(base, moment_id("b" * 64, 100.0, 60.0, RATE, Scorer(), fake_hasher))

    def test_a_malformed_hasher_result_is_refused(self):
        with self.assertRaises(StreamError):
            moment_id(MEDIA_ID, 0.0, 1.0, RATE, Scorer(), lambda payload: "not-a-digest")

    def test_the_default_hasher_never_substitutes_another_algorithm(self):
        try:
            import blake3  # noqa: F401
        except ImportError:
            with self.assertRaises(MomentIdUnavailable):
                moment_id(MEDIA_ID, 0.0, 1.0, RATE, Scorer())
        else:
            digest = moment_id(MEDIA_ID, 0.0, 1.0, RATE, Scorer())
            self.assertEqual(64, len(digest))
            self.assertNotEqual(
                digest, fake_hasher(b"anything"),
                "the production path must not be the test double",
            )


class TestDeterminism(unittest.TestCase):
    """Same plan = identical render (CLAUDE.md hard rule 3) is only true if the
    plan itself is reproducible."""

    def frames(self):
        rng = random.Random(20260817)
        out = []
        for index in range(600):
            out.append(good(
                motion=round(rng.random(), 3),
                shake=round(rng.random() * 0.4, 3),
                sharpness=round(rng.random(), 3),
                smile_intensity=round(rng.random(), 3),
                novelty=round(rng.random(), 3),
            ))
        return out

    def test_two_runs_produce_identical_records(self):
        frames = self.frames()
        first = plan(frames).records
        second = plan(frames).records
        self.assertEqual(json.dumps(first, sort_keys=True), json.dumps(second, sort_keys=True))

    def test_input_ordering_does_not_change_the_output(self):
        frames = self.frames()
        words = [Word(f"w{i}", 20.0 * i, 20.0 * i + 8.0) for i in range(1, 12)]
        events = [AudioEvent("laughter", 0.6 + i / 100, 25.0 * i) for i in range(1, 12)]
        shots = [Shot(f"shot-{i:04d}", 200 * (i - 1), 200 * i) for i in range(1, 4)]
        ordered = plan_moments(
            stream(frames, words=words, audio_events=events, shots=shots, language="en-IN"),
            id_hasher=fake_hasher,
        ).records
        rng = random.Random(7)
        shuffled_words, shuffled_events, shuffled_shots = list(words), list(events), list(shots)
        rng.shuffle(shuffled_words)
        rng.shuffle(shuffled_events)
        rng.shuffle(shuffled_shots)
        shuffled = plan_moments(
            stream(frames, words=shuffled_words, audio_events=shuffled_events,
                   shots=shuffled_shots, language="en-IN"),
            id_hasher=fake_hasher,
        ).records
        self.assertEqual(
            json.dumps(ordered, sort_keys=True), json.dumps(shuffled, sort_keys=True),
            "output depends on the order the caller happened to supply its inputs",
        )

    def test_every_emitted_float_is_quantised(self):
        for record in plan(self.frames()).records:
            for value in _floats(record):
                self.assertEqual(round(value, 6), value, f"unquantised float {value}")

    def test_a_tie_is_broken_by_time_not_by_construction_order(self):
        """Uniform footage means every window scores identically; the earliest
        must win, every time."""
        first = plan([good()] * 300).moments[0]
        self.assertEqual(0, span(first.record)[0])


def _floats(node):
    if isinstance(node, dict):
        for value in node.values():
            yield from _floats(value)
    elif isinstance(node, list):
        for value in node:
            yield from _floats(value)
    elif isinstance(node, float):
        yield node


class TestValidation(unittest.TestCase):
    def test_nan_is_refused_rather_than_silently_passing_every_gate(self):
        with self.assertRaises(StreamError):
            plan([good(shake=float("nan"))])
        with self.assertRaises(StreamError):
            plan([good(motion=float("inf"))])

    def test_nan_is_refused_where_no_range_check_would_catch_it(self):
        """The range checks catch a NaN Unit by accident (every comparison is
        False). Nothing catches a NaN rate, offset or word timing except the
        finiteness check itself -- and a NaN rate would put every emitted
        RationalTime beyond repair."""
        nan = float("nan")
        with self.assertRaises(StreamError):
            plan_moments(stream([good()] * 60, rate=nan), id_hasher=fake_hasher)
        with self.assertRaises(StreamError):
            plan_moments(stream([good()] * 60, start_value=nan), id_hasher=fake_hasher)
        with self.assertRaises(StreamError):
            plan_moments(stream([good()] * 60, words=[Word("x", nan, 10.0)]),
                         id_hasher=fake_hasher)
        with self.assertRaises(StreamError):
            plan_moments(stream([good()] * 60, audio_events=[AudioEvent("laughter", nan, 5.0)]),
                         id_hasher=fake_hasher)

    def test_values_outside_the_contract_range_are_refused(self):
        with self.assertRaises(StreamError):
            plan([good(sharpness=1.4)])
        with self.assertRaises(StreamError):
            plan([good(loudness_lufs=6.0)])

    def test_a_boolean_is_not_a_measurement(self):
        with self.assertRaises(StreamError):
            plan([good(sharpness=True)])

    def test_identifiers_are_checked_before_anything_is_stored(self):
        with self.assertRaises(StreamError):
            plan_moments(stream([good()] * 60, media_id="short"), id_hasher=fake_hasher)
        with self.assertRaises(StreamError):
            plan([good()] * 60, run_id="Run Fusion 1")
        with self.assertRaises(StreamError):
            plan_moments(stream([good()] * 60, shots=[Shot("Shot 1", 0, 60)]),
                         id_hasher=fake_hasher)

    def test_an_impossible_policy_is_refused(self):
        with self.assertRaises(PolicyError):
            Policy(min_moment_seconds=5.0, max_moment_seconds=2.0).validate()
        with self.assertRaises(PolicyError):
            Policy(window_seconds=0.5, min_moment_seconds=2.0).validate()
        with self.assertRaises(PolicyError):
            Policy(score_floor=1.5).validate()
        with self.assertRaises(PolicyError):
            Policy(hop_seconds=0.0).validate()

    def test_half_a_proxy_mapping_is_refused(self):
        with self.assertRaises(StreamError):
            plan_moments(stream([good()] * 60, proxy_rate=30.0), id_hasher=fake_hasher)

    def test_a_shot_outside_the_stream_is_refused(self):
        with self.assertRaises(StreamError):
            plan_moments(stream([good()] * 60, shots=[Shot("shot-0001", 0, 90)]),
                         id_hasher=fake_hasher)

    def test_an_empty_stream_is_an_empty_plan_not_a_crash(self):
        result = plan([])
        self.assertEqual((), result.moments)
        self.assertEqual((), result.eliminated)
        self.assertEqual(0.0, result.eliminated_fraction)

    def test_records_are_independent_objects(self):
        """A caller mutating one record must not change another."""
        result = plan([good()] * 300)
        records = result.records
        clone = copy.deepcopy(records[0])
        records[0]["scores"]["moment_score"]["value"] = 0.0
        self.assertNotEqual(clone, records[0])
        self.assertNotEqual(
            0.0, result.records[1]["scores"]["moment_score"]["value"]
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
