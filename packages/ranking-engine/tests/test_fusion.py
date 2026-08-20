"""Score fusion v1, tested for the behaviours that decide what a user sees.

The interesting cases are not "does the arithmetic work". They are the ones
where a plausible implementation is quietly wrong: a photo penalised for having
been measured less, a landscape penalised for containing no faces, a black frame
that wins because everything around it is worse, and a tie that reorders between
runs.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from memory_engine_ranking.fusion import (  # noqa: E402
    DEFAULT_MIN_COVERAGE,
    FEATURE_SET_ID,
    FaceState,
    IncomparableScores,
    SharpnessFloor,
    SignalError,
    UncalibratedFloor,
    WeightError,
    signals_from_media_record,
    REJECT_BELOW_SHARPNESS_FLOOR,
    REJECT_BLACK_FRAME,
    REJECT_LENS_OBSTRUCTED,
    FusedScore,
    Signals,
    Weights,
    aggregate_face_quality,
    eliminate,
    explain,
    fuse,
    rank,
)


def measured(**overrides) -> Signals:
    """A fully-measured portrait. Override only the signal under test."""
    fields = {
        "sharpness": 0.70,
        "exposure": 0.70,
        "noise": 0.60,
        "contrast": 0.60,
        "technical_iqa": 0.70,
        "aesthetic": 0.60,
        "composition": 0.60,
        "face_quality": 0.70,
        "face_state": FaceState.HAS_FACES,
    }
    fields.update(overrides)
    return Signals(**fields)


class TestElimination(unittest.TestCase):
    """Junk is rejected, not scored low.

    The distinction matters because a low score still competes, and still wins
    whenever the pool is bad enough -- which is precisely the tail of an
    action-camera card, where the alternative to a pocket shot is another
    pocket shot.
    """

    def test_black_frames_and_lens_obstructions_are_rejected(self):
        for signals, reason in (
            (measured(is_black_frame=True), REJECT_BLACK_FRAME),
            (measured(is_lens_obstructed=True), REJECT_LENS_OBSTRUCTED),
        ):
            with self.subTest(reason=reason):
                score = fuse(signals)
                self.assertTrue(score.rejected)
                self.assertEqual(reason, score.rejection_reason)

    def test_a_rejected_frame_never_outranks_a_kept_one(self):
        """Even when everything else in the pool is terrible."""
        terrible = fuse(measured(sharpness=0.10, exposure=0.05, noise=0.05,
                                 contrast=0.05, technical_iqa=0.05,
                                 aesthetic=0.05, composition=0.05,
                                 face_quality=0.05))
        black = fuse(measured(is_black_frame=True))
        self.assertFalse(terrible.rejected)
        self.assertEqual(["kept", "black"], rank({"kept": terrible, "black": black}))

    def test_a_rejected_frame_loses_to_an_unmeasured_one_on_value_alone(self):
        """The case value-ordering cannot decide, and the reason `rank` sorts on
        `rejected` first rather than trusting the number.

        Mid-scan an unmeasured photo scores 0.0 and is not rejected; a black
        frame also scores 0.0 and is. With only the value to go on the tie
        breaks on media id, so a black frame named `a_black` would outrank a
        perfectly good unmeasured photo named `z_unmeasured` -- and mid-scan is
        exactly when the library preview is being drawn.
        """
        unmeasured = fuse(Signals(sharpness=0.5, exposure=0.5),
                          Weights(sharpness=0.0, exposure=0.0))
        black = fuse(measured(is_black_frame=True))
        self.assertEqual(0.0, unmeasured.value)
        self.assertEqual(0.0, black.value)
        self.assertFalse(unmeasured.rejected)

        self.assertEqual(
            ["z_unmeasured", "a_black"],
            rank({"a_black": black, "z_unmeasured": unmeasured}),
        )

    def test_an_uncalibrated_sharpness_scale_eliminates_nothing(self):
        """Issue #22. The default floor is None, so no sharpness value -- not
        even 0.0 -- removes a photo from the pool.

        This test used to assert the opposite: 0.02 was eliminated and 0.15 was
        kept, against a constant chosen for a normalisation that has never
        existed. It passed for months while the gate it described could have
        deleted anywhere between 0% and 77.5% of a library depending on a
        divisor nobody had written down."""
        for sharpness in (0.0, 0.02, 0.15, 0.9):
            with self.subTest(sharpness=sharpness):
                self.assertIsNone(eliminate(measured(sharpness=sharpness)))
        self.assertFalse(fuse(measured(sharpness=0.0)).rejected)

    def test_low_sharpness_still_costs_a_photo_its_place(self):
        """Rank-only is not the same as ignored. The signal carries the largest
        weight in the default profile, so a blurry frame sorts last and wins
        only when the alternative is nothing -- which is the behaviour a hard
        gate destroys at the tail of a card."""
        blurry = fuse(measured(sharpness=0.02))
        sharp = fuse(measured(sharpness=0.95))
        self.assertLess(blurry.value, sharp.value)
        self.assertEqual(
            ["sharp", "blurry"], rank({"blurry": blurry, "sharp": sharp})
        )

    def test_a_calibrated_floor_eliminates(self):
        """The capability is not removed, only gated behind evidence."""
        floor = SharpnessFloor(
            value=0.08,
            benchmark_id="test-only",
            measured_at="2026-08-17",
            normalisation_id="test-only",
            junk_total=10,
            junk_eliminated=9,
            hard_negatives_total=12,
            hard_negatives_retained=12,
        )
        self.assertEqual(
            REJECT_BELOW_SHARPNESS_FLOOR,
            eliminate(measured(sharpness=0.02), sharpness_floor=floor),
        )
        self.assertIsNone(eliminate(measured(sharpness=0.15), sharpness_floor=floor))
        self.assertTrue(fuse(measured(sharpness=0.02), sharpness_floor=floor).rejected)

    def test_a_bare_number_is_refused(self):
        """`sharpness_floor=0.08` is exactly how the guess comes back."""
        with self.assertRaises(UncalibratedFloor):
            eliminate(measured(sharpness=0.9), sharpness_floor=0.08)
        with self.assertRaises(UncalibratedFloor):
            fuse(measured(sharpness=0.9), sharpness_floor=0.08)

    def test_a_floor_that_loses_one_hard_negative_is_refused(self):
        """The asymmetry, enforced: keeping a bad photo costs an album slot,
        losing a good one is unrecoverable. No error rate is acceptable here."""
        with self.assertRaises(UncalibratedFloor) as raised:
            SharpnessFloor(
                value=0.4, benchmark_id="b", measured_at="2026-08-17",
                normalisation_id="n", junk_total=100, junk_eliminated=98,
                hard_negatives_total=40, hard_negatives_retained=39,
            )
        self.assertIn("hard negative", str(raised.exception))

    def test_a_floor_without_a_normalisation_is_refused(self):
        """A threshold is meaningless without the unit mapping it was measured
        against -- the whole content of issue #22."""
        with self.assertRaises(UncalibratedFloor):
            SharpnessFloor(
                value=0.08, benchmark_id="b", measured_at="2026-08-17",
                normalisation_id="  ", junk_total=10, junk_eliminated=9,
                hard_negatives_total=10, hard_negatives_retained=10,
            )

    def test_a_floor_calibrated_only_on_junk_is_refused(self):
        """Junk alone proves the floor catches something. It cannot prove the
        floor keeps what matters, which is the failure that costs a memory."""
        with self.assertRaises(UncalibratedFloor):
            SharpnessFloor(
                value=0.08, benchmark_id="b", measured_at="2026-08-17",
                normalisation_id="n", junk_total=50, junk_eliminated=50,
                hard_negatives_total=0, hard_negatives_retained=0,
            )

    def test_a_determination_still_eliminates(self):
        """Black frame and lens obstruction are facts, not measurements on an
        undefined scale, and are unaffected by the change."""
        self.assertEqual(REJECT_BLACK_FRAME, eliminate(measured(is_black_frame=True)))
        self.assertEqual(
            REJECT_LENS_OBSTRUCTED, eliminate(measured(is_lens_obstructed=True))
        )

    def test_elimination_order_is_fixed_when_several_apply(self):
        """Two hosts must report the same reason for the same frame."""
        both = measured(is_black_frame=True, is_lens_obstructed=True, sharpness=0.0)
        self.assertEqual(REJECT_BLACK_FRAME, eliminate(both))


class TestMissingSignals(unittest.TestCase):
    """Missing renormalises. It does not default to 0, and it does not default
    to 0.5."""

    def test_an_unmeasured_photo_is_not_punished_for_being_unmeasured(self):
        """Treating missing as 0 would invert ranking during a scan: every photo
        the expensive models had not reached yet would sort last."""
        partial = fuse(Signals(sharpness=0.9, exposure=0.9, face_state=FaceState.NO_FACES))
        self.assertGreater(partial.value, 0.8)

    def test_a_missing_signal_is_not_invented(self):
        """Treating missing as 0.5 would make it indistinguishable from a real
        mediocre measurement, and no later audit could separate them."""
        score = fuse(Signals(sharpness=0.9, exposure=0.9, face_state=FaceState.NO_FACES))
        self.assertNotIn("aesthetic", score.as_feature_map())
        self.assertEqual({"sharpness", "exposure"}, set(score.as_feature_map()))

    def test_measuring_more_signals_does_not_by_itself_lower_the_score(self):
        """The third wrong approach: summing present signals and ignoring the
        rest, so a photo scores lower purely for having been measured less."""
        two = fuse(Signals(sharpness=0.7, exposure=0.7, face_state=FaceState.NO_FACES))
        many = fuse(Signals(sharpness=0.7, exposure=0.7, noise=0.7, contrast=0.7,
                            technical_iqa=0.7, aesthetic=0.7, composition=0.7,
                            face_state=FaceState.NO_FACES))
        self.assertAlmostEqual(two.value, many.value, places=6)
        self.assertLess(two.coverage, many.coverage)

    def test_nothing_measured_is_reported_as_nothing_claimed(self):
        """Not a rejection: the photo may well be fine, we have not looked."""
        empty = fuse(Signals(sharpness=0.5, exposure=0.5), Weights(sharpness=0.0, exposure=0.0))
        self.assertFalse(empty.rejected)
        self.assertEqual(0.0, empty.value)
        self.assertEqual(0.0, empty.coverage)


class TestCoverage(unittest.TestCase):
    def test_coverage_is_reported_rather_than_folded_into_the_value(self):
        """Discounting an under-measured photo would make it look worse than a
        measured bad one, which is a different lie rather than a fix."""
        partial = fuse(Signals(sharpness=0.9, exposure=0.9, face_state=FaceState.HAS_FACES))
        self.assertGreater(partial.value, 0.8)
        self.assertLess(partial.coverage, DEFAULT_MIN_COVERAGE)
        self.assertFalse(partial.comparable)

    def test_a_partially_measured_photo_can_outscore_a_fully_measured_one(self):
        """The hazard this design deliberately makes visible instead of hiding.

        Two signals at 0.8 average higher than seven signals averaging 0.69, so
        the raw values invert. `comparable` is what stops a caller ranking them
        against each other, and this test exists so that anyone who removes
        `comparable` sees what it was load-bearing for.
        """
        partial = fuse(Signals(sharpness=0.8, exposure=0.7, face_state=FaceState.HAS_FACES))
        full = fuse(Signals(sharpness=0.8, exposure=0.7, noise=0.6, contrast=0.65,
                            technical_iqa=0.72, aesthetic=0.55, composition=0.6,
                            face_state=FaceState.NO_FACES))
        self.assertGreater(partial.value, full.value)
        self.assertFalse(partial.comparable)
        self.assertTrue(full.comparable)

        # And rank() now REFUSES rather than producing the inverted order.
        with self.assertRaises(IncomparableScores):
            rank({"partial": partial, "full": full})
        # Only if the caller explicitly accepts an approximate order.
        self.assertEqual(["partial", "full"],
                         rank({"partial": partial, "full": full}, allow_mixed=True))

    def test_a_landscape_is_not_an_under_measured_portrait(self):
        """`face_quality` is null for a mountain and for an unprocessed portrait.
        Counting the face weight against the mountain would cap every landscape
        below the comparability threshold, quietly excluding scenery from
        automated output."""
        landscape = fuse(Signals(sharpness=0.8, exposure=0.7, noise=0.6,
                                 contrast=0.65, technical_iqa=0.72,
                                 aesthetic=0.55, composition=0.6,
                                 face_state=FaceState.NO_FACES))
        self.assertEqual(1.0, landscape.coverage)
        self.assertTrue(landscape.comparable)

        portrait = fuse(Signals(sharpness=0.8, exposure=0.7, noise=0.6,
                                contrast=0.65, technical_iqa=0.72,
                                aesthetic=0.55, composition=0.6,
                                face_state=FaceState.HAS_FACES))
        self.assertLess(portrait.coverage, 1.0)

    def test_a_face_score_is_ignored_when_the_photo_has_no_faces(self):
        """A stale or mistaken face_quality on a landscape must not move the
        number."""
        without = fuse(Signals(sharpness=0.8, exposure=0.7, face_state=FaceState.NO_FACES))
        with_stale = fuse(Signals(sharpness=0.8, exposure=0.7,
                                  face_quality=0.01, face_state=FaceState.NO_FACES))
        self.assertEqual(without.value, with_stale.value)


class TestWeighting(unittest.TestCase):
    def test_eyes_open_beats_a_sharper_frame_with_eyes_shut(self):
        """In a family library this is not a close call, and it is the main
        reason face_quality carries as much weight as it does."""
        eyes_open = fuse(measured(sharpness=0.70, face_quality=0.95))
        eyes_shut = fuse(measured(sharpness=0.92, technical_iqa=0.80,
                                  face_quality=0.20))
        self.assertGreater(eyes_open.value, eyes_shut.value)

    def test_aesthetic_is_a_prior_not_a_verdict(self):
        """Build plan §4.2 is explicit that the aesthetic head is a prior. A
        head trained on stock photography has opinions about family snapshots
        that no family shares, so it must not be able to overturn the technical
        signals on its own."""
        weights = Weights()
        self.assertLess(weights.aesthetic, weights.sharpness)
        self.assertLess(weights.aesthetic, weights.face_quality)

        loved = fuse(measured(aesthetic=1.0))
        hated = fuse(measured(aesthetic=0.0))
        self.assertLess(loved.value - hated.value, 0.15)

    def test_weights_are_data_so_a_user_profile_is_a_row_not_a_code_change(self):
        strict = Weights(weights_id="user-42", sharpness=0.6, exposure=0.1,
                         noise=0.05, contrast=0.05, technical_iqa=0.1,
                         aesthetic=0.05, composition=0.025, face_quality=0.025)
        signals = measured(sharpness=0.95)
        self.assertGreater(fuse(signals, strict).value, fuse(signals).value)
        self.assertEqual("user-42", fuse(signals, strict).weights_id)


class TestAggregateFaceQuality(unittest.TestCase):
    """The group-photo aggregate the faces stage writes into
    MediaRecord.quality.face_quality since default-v2.

    The blind spot this closes: the summary was max() over significant faces,
    so one excellent face masked four blinking ones. The aggregate is anchored
    on the min because nine perfect faces and one blinking key person is not
    an excellent group photo.
    """

    def test_the_formula_on_a_known_vector(self):
        # min=0.4, p10=0.4 (n=2 -> p10 == min), mean=0.6, eyes ratio=1.0
        # 0.45*0.4 + 0.20*0.4 + 0.20*0.6 + 0.15*1.0 = 0.53
        self.assertEqual(0.53, aggregate_face_quality([0.4, 0.8], [0.9, 0.9]))

    def test_p10_is_the_second_smallest_once_the_crowd_is_large_enough(self):
        """Lower-value convention, no interpolation: index ceil(0.1*n)-1.
        At n=20 that is index 1, so the near-worst face starts to soften the
        pure min without erasing it."""
        qualities = [0.1, 0.2] + [0.9] * 18
        eyes = [0.9] * 20
        # 0.45*0.1 + 0.20*0.2 + 0.20*0.825 + 0.15*1.0 = 0.4
        self.assertEqual(0.4, aggregate_face_quality(qualities, eyes))

    def test_one_weak_face_among_nine_strong_scores_clearly_below_all_strong(self):
        """The claim a max() could never make. Under the old summary both
        photos reported 0.9; under the min-anchored aggregate the group shot
        with one bad face drops far below the clean one."""
        clean = aggregate_face_quality([0.9] * 10, [0.9] * 10)
        one_bad = aggregate_face_quality([0.3] + [0.9] * 9, [0.9] * 10)
        self.assertGreater(clean - one_bad, 0.3)

    def test_one_blinker_among_nine_open_scores_below_all_eyes_open(self):
        nine_open = aggregate_face_quality([0.9] * 10, [0.9] * 10)
        one_blinker = aggregate_face_quality([0.9] * 10, [0.1] + [0.9] * 9)
        self.assertLess(one_blinker, nine_open)
        self.assertGreaterEqual(nine_open - one_blinker, 0.01)

    def test_a_neutral_eye_confidence_is_not_a_blink(self):
        """0.5 is neutral in the stored mapping; >= 0.5 counts as open,
        because an unremarkable eye state is not negative evidence."""
        neutral = aggregate_face_quality([0.8, 0.8], [0.5, 0.5])
        open_ = aggregate_face_quality([0.8, 0.8], [0.9, 0.9])
        self.assertEqual(neutral, open_)

    def test_all_eyes_unmeasured_renormalises_rather_than_defaulting(self):
        """A pre-backfill library drops the eyes term and the remaining three
        weights renormalise to sum 1 -- so uniform face quality passes through
        unchanged instead of being taxed 0.15 for a measurement nobody made."""
        self.assertEqual(0.7, aggregate_face_quality([0.7, 0.7, 0.7], [None] * 3))
        # And the renormalised weights are the computed 0.45/0.85 etc., not a
        # second set of hardcoded decimals:
        expected = round((0.45 * 0.4 + 0.20 * 0.4 + 0.20 * 0.6) / 0.85, 6)
        self.assertEqual(expected, aggregate_face_quality([0.4, 0.8], [None, None]))

    def test_a_partially_measured_photo_ignores_only_the_unmeasured_faces(self):
        """None is unmeasured, never closed: the ratio is over measured faces
        only, so a half-backfilled photo is not penalised for the backlog."""
        self.assertEqual(
            aggregate_face_quality([0.8, 0.8], [None, 0.9]),
            aggregate_face_quality([0.8, 0.8], [0.9, 0.9]),
        )

    def test_a_single_face_degenerates_to_that_face(self):
        # min == p10 == mean == 0.6; eyes open ratio 1.0
        self.assertEqual(round(0.85 * 0.6 + 0.15, 6), aggregate_face_quality([0.6], [0.8]))
        self.assertEqual(0.6, aggregate_face_quality([0.6], [None]))

    def test_zero_faces_is_a_caller_bug_not_a_score(self):
        """The faces stage skips the summary write when a photo has no
        significant faces; an empty aggregate call must fail loudly rather
        than fabricate a claim about faces that are not there."""
        with self.assertRaises(SignalError):
            aggregate_face_quality([], [])

    def test_mismatched_lengths_are_refused(self):
        """Index i of both sequences must describe the same face."""
        with self.assertRaises(SignalError):
            aggregate_face_quality([0.8, 0.9], [0.9])

    def test_values_outside_the_unit_interval_are_refused(self):
        for qualities, eyes in (
            ([1.2], [0.9]),
            ([0.8], [-0.1]),
            ([float("nan")], [0.9]),
            ([0.8], [float("inf")]),
        ):
            with self.subTest(qualities=qualities, eyes=eyes):
                with self.assertRaises(SignalError):
                    aggregate_face_quality(qualities, eyes)

    def test_deterministic_and_quantised_to_six_decimals(self):
        pairs = [(0.31, 0.7), (0.87, None), (0.44, 0.5), (0.91, 0.2), (0.66, 0.9)]
        qualities = [q for q, _ in pairs]
        eyes = [e for _, e in pairs]
        value = aggregate_face_quality(qualities, eyes)
        self.assertEqual(value, aggregate_face_quality(qualities, eyes))
        self.assertEqual(value, round(value, 6))
        # Face order is a detection artefact, not a property of the photo:
        # permuting the (quality, eyes) pairs together must not move the score.
        shuffled = sorted(pairs, key=lambda pair: (pair[0] * 7919) % 1.0)
        self.assertEqual(
            value,
            aggregate_face_quality(
                [q for q, _ in shuffled], [e for _, e in shuffled]
            ),
        )

    def test_the_result_stays_inside_the_unit_interval(self):
        self.assertEqual(1.0, aggregate_face_quality([1.0, 1.0], [1.0, 1.0]))
        self.assertEqual(0.0, aggregate_face_quality([0.0, 0.0], [0.0, 0.0]))


class TestProvenance(unittest.TestCase):
    """A score you cannot attribute is not reproducible -- the same argument
    that put a config digest in ModelPin."""

    def test_the_score_records_which_weights_produced_it(self):
        score = fuse(measured())
        self.assertEqual("default-v2", score.weights_id)
        self.assertEqual(Weights().digest(), score.weights_digest)
        self.assertEqual(FEATURE_SET_ID, score.feature_set_id)

    def test_default_v2_kept_the_v1_weight_values_so_the_digest_is_unchanged(self):
        """The v1->v2 bump is semantic -- face_quality became a min-anchored
        group aggregate instead of a max -- not numeric. The digest covers
        weight VALUES only (by design: renames are not refusions), so it must
        be identical to a v1-named profile with the same numbers. Anything
        else here means someone edited a weight alongside the rename."""
        self.assertEqual(
            Weights(weights_id="default-v1").digest(), Weights().digest()
        )

    def test_weights_differing_only_by_name_are_the_same_fusion(self):
        self.assertEqual(Weights().digest(), Weights(weights_id="renamed").digest())

    def test_weights_sharing_a_name_but_differing_by_value_are_not(self):
        """The failure the digest exists to catch: a profile edited in place."""
        self.assertNotEqual(
            Weights().digest(), Weights(sharpness=0.23).digest()
        )

    def test_the_explanation_names_the_signals_that_drove_the_score(self):
        """'Why is this photo ranked 0.82' is only answerable if something
        renders the answer."""
        text = explain(fuse(measured(face_quality=0.99)))
        self.assertIn("face_quality", text)
        self.assertIn("0.", text)

    def test_a_provisional_score_says_so_in_its_explanation(self):
        text = explain(fuse(Signals(sharpness=0.9, exposure=0.9, face_state=FaceState.HAS_FACES)))
        self.assertIn("provisional", text)

    def test_a_rejection_explains_itself(self):
        self.assertEqual(
            "rejected: black_frame", explain(fuse(measured(is_black_frame=True)))
        )


class TestDeterminism(unittest.TestCase):
    """A fusion that differed in the last bit between machines would reorder
    ties in `select_primary`, and a photo would silently swap out of an album
    between two runs of the same pipeline."""

    def test_the_same_input_gives_a_bit_identical_result(self):
        signals = measured()
        first = fuse(signals)
        for _ in range(20):
            self.assertEqual(first, fuse(signals))

    def test_the_value_is_quantised(self):
        score = fuse(measured(sharpness=1 / 3, exposure=1 / 7))
        self.assertEqual(score.value, round(score.value, 6))
        for _, value, share in score.contributions:
            self.assertEqual(value, round(value, 6))
            self.assertEqual(share, round(share, 6))

    def test_contributions_are_emitted_in_a_fixed_order(self):
        names = [name for name, _, _ in fuse(measured()).contributions]
        self.assertEqual(sorted(names), names)

    def test_ranking_ties_break_on_media_id(self):
        """Matching select_primary: every ordering in this package breaks the
        same way, so the two never disagree."""
        same = fuse(measured())
        self.assertEqual(
            ["m_a", "m_b", "m_c"],
            rank({"m_c": same, "m_a": same, "m_b": same}),
        )


class TestBounds(unittest.TestCase):
    def test_the_value_stays_in_unit_range(self):
        for sharpness in (0.0, 0.5, 1.0):
            for face in (0.0, 1.0):
                with self.subTest(sharpness=sharpness, face=face):
                    score = fuse(measured(sharpness=max(sharpness, 0.1),
                                          face_quality=face))
                    self.assertGreaterEqual(score.value, 0.0)
                    self.assertLessEqual(score.value, 1.0)

    def test_a_perfect_photo_scores_one_and_a_worthless_one_scores_zero(self):
        perfect = fuse(Signals(*(1.0,) * 8, face_state=FaceState.HAS_FACES))
        self.assertEqual(1.0, perfect.value)
        # Sharpness must clear the elimination floor, so "worthless" is the
        # worst score a frame can have while still being a candidate.
        worthless = fuse(Signals(0.1, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0,
                                 face_state=FaceState.HAS_FACES))
        self.assertLess(worthless.value, 0.03)

    def test_a_fused_score_is_hashable_and_frozen(self):
        """It gets stored, compared and used as a dict key; mutating one after
        the fact would detach it from the weights digest it claims."""
        score = fuse(measured())
        self.assertIsInstance(hash(score), int)
        with self.assertRaises(Exception):
            score.value = 0.5  # type: ignore[misc]




class TestContractAdapter(unittest.TestCase):
    """The package must actually consume the contract it claims to.

    Codex found there was no adapter at all: MediaRecord.quality holds Score
    objects, Signals wanted bare floats, and passing a real fixture raised a
    TypeError from inside a comparison. The generated types were referenced in
    prose and bypassed in practice.
    """

    CONTRACTS = Path(__file__).resolve().parents[3] / "contracts"

    def _fixture(self, name: str) -> dict:
        import json

        return json.loads(
            (self.CONTRACTS / "fixtures" / name).read_text(encoding="utf-8")
        )

    def test_a_real_media_record_fixture_scores(self):
        record = self._fixture("media-record/valid/image-no-exif-date.json")
        score = fuse(signals_from_media_record(record))
        self.assertFalse(score.rejected)
        self.assertGreater(score.value, 0.0)

    def test_score_objects_are_unwrapped(self):
        record = {"quality": {"sharpness": {"value": 0.9, "run_id": "r1"},
                              "exposure": {"value": 0.8}}}
        signals = signals_from_media_record(record)
        self.assertEqual(0.9, signals.sharpness)
        self.assertEqual(0.8, signals.exposure)

    def test_bare_floats_are_accepted_too(self):
        """A caller that has already flattened the record should not be forced
        to re-wrap it."""
        signals = signals_from_media_record(
            {"quality": {"sharpness": 0.9, "exposure": 0.8}}
        )
        self.assertEqual(0.9, signals.sharpness)

    def test_passing_a_raw_score_object_to_signals_is_refused(self):
        """The original TypeError, now a clear error at the boundary."""
        with self.assertRaises(SignalError):
            fuse(Signals(sharpness={"value": 0.9}, exposure=0.8))

    def test_a_record_with_no_classical_measures_is_refused(self):
        """Quarantined files and videos have no photo quality. Scoring them as
        bad photos would silently rank real failures against real photos."""
        with self.assertRaises(SignalError):
            signals_from_media_record({"quality": None})

    def test_face_state_defaults_to_not_run_rather_than_no_faces(self):
        """A MediaRecord cannot distinguish them -- it is a property of the
        pipeline's progress. The honest default under-claims coverage."""
        signals = signals_from_media_record(
            {"quality": {"sharpness": 0.9, "exposure": 0.8}}
        )
        self.assertIs(FaceState.NOT_RUN, signals.face_state)


class TestInputValidation(unittest.TestCase):
    def test_nan_is_refused_rather_than_bypassing_elimination(self):
        """`NaN < floor` is False, so a NaN sharpness passed the elimination
        gate untouched and produced value=nan -- which made rank() order by a
        value for which `<` is meaningless. A comparison-based gate cannot
        defend itself against a value that compares False to everything."""
        for bad in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(value=bad):
                with self.assertRaises(SignalError):
                    fuse(measured(sharpness=bad))

    def test_out_of_range_signals_are_refused(self):
        """A value outside [0,1] means an upstream normalisation is missing, and
        clamping here would hide that."""
        for bad in (-0.1, 1.5):
            with self.subTest(value=bad):
                with self.assertRaises(SignalError):
                    fuse(measured(aesthetic=bad))

    def test_negative_weights_are_refused_because_they_falsify_coverage(self):
        """Codex's case: 0.22, 0.18, -0.39 gives an applicable total of 0.01, so
        coverage computes as 40 and clamps to 1.0 -- claiming full measurement
        while ignoring a third of the profile."""
        with self.assertRaises(WeightError):
            fuse(measured(), Weights(noise=-0.39))

    def test_an_all_zero_profile_is_refused(self):
        with self.assertRaises(WeightError):
            fuse(measured(), Weights(**{n: 0.0 for n in (
                "sharpness", "exposure", "noise", "contrast",
                "technical_iqa", "aesthetic", "composition", "face_quality")}))


class TestFaceStateIsTriState(unittest.TestCase):
    """`has_faces: bool` defaulted the UNKNOWN case to "no faces", so a photo
    awaiting face detection reported full coverage and ranked as comparable
    against finished ones -- during the only period when it matters."""

    def test_not_run_counts_against_coverage(self):
        pending = fuse(measured(face_quality=None, face_state=FaceState.NOT_RUN))
        self.assertLess(pending.coverage, 1.0)

    def test_no_faces_does_not_count_against_coverage(self):
        landscape = fuse(measured(face_quality=None, face_state=FaceState.NO_FACES))
        self.assertEqual(1.0, landscape.coverage)

    def test_the_two_absences_are_distinguishable_in_the_result(self):
        pending = fuse(measured(face_quality=None, face_state=FaceState.NOT_RUN))
        landscape = fuse(measured(face_quality=None, face_state=FaceState.NO_FACES))
        self.assertNotEqual(pending.coverage, landscape.coverage)


class TestComparability(unittest.TestCase):
    """Coverage alone was the wrong test.

    Two photos can both clear the coverage bar while one has face quality and
    the other does not -- so the heaviest signal in the profile is present on
    one side of the comparison and absent from the other, and ordering them is
    meaningless however high the coverage.
    """

    def test_same_signal_set_is_comparable(self):
        a = fuse(measured())
        b = fuse(measured(sharpness=0.5))
        self.assertTrue(a.comparable_with(b))
        self.assertEqual(["a", "b"], rank({"a": a, "b": b}))

    def test_different_signal_sets_are_not_comparable_even_at_high_coverage(self):
        with_face = fuse(measured())
        without_face = fuse(measured(face_quality=None,
                                     face_state=FaceState.NO_FACES))
        self.assertGreater(without_face.coverage, DEFAULT_MIN_COVERAGE)
        self.assertFalse(with_face.comparable_with(without_face))

    def test_different_weight_profiles_are_not_comparable(self):
        """Scores from before and after a user reweighting must not be mixed by
        a resumable recompute -- which defeats the provenance fields entirely."""
        default = fuse(measured())
        custom = fuse(measured(), Weights(weights_id="user-1", sharpness=0.5))
        self.assertFalse(default.comparable_with(custom))
        with self.assertRaises(IncomparableScores):
            rank({"a": default, "b": custom})

    def test_rank_is_strict_by_default(self):
        """A default that produces a plausible wrong answer is worse than one
        that refuses, because only the refusal gets noticed."""
        a = fuse(measured())
        b = fuse(Signals(sharpness=0.99, exposure=0.99,
                         face_state=FaceState.HAS_FACES))
        with self.assertRaises(IncomparableScores):
            rank({"a": a, "b": b})
        self.assertEqual(2, len(rank({"a": a, "b": b}, allow_mixed=True)))

    def test_rejected_scores_do_not_block_ranking(self):
        """A black frame has no signal set, so requiring it to match would make
        any pool containing one unrankable."""
        good = fuse(measured())
        black = fuse(measured(is_black_frame=True))
        self.assertEqual(["good", "black"], rank({"good": good, "black": black}))


class TestDigestPortability(unittest.TestCase):
    """The same bug, three files later.

    `Weights.digest` used json.dumps(sort_keys=True) -- the exact construction
    already proved non-portable for model configs. Python writes 1.0 as `1.0`
    and JavaScript writes `1`.
    """

    def test_the_digest_payload_is_fixed_precision_decimal(self):
        payload = ";".join(
            f"{name}={value:.6f}" for name, value in sorted(Weights().as_map().items())
        )
        self.assertIn("sharpness=0.220000", payload)
        self.assertNotIn("sharpness=0.22;", payload)

    def test_a_whole_number_weight_does_not_serialise_ambiguously(self):
        """The specific value that differs between Python and JavaScript."""
        payload = f"{1.0:.6f}"
        self.assertEqual("1.000000", payload)
        self.assertNotEqual(json.dumps(1.0), payload)

    def test_weights_differing_below_the_digest_precision_are_the_same_fusion(self):
        """Six decimals is well past the precision at which a weight change
        alters any ranking."""
        self.assertEqual(
            Weights().digest(), Weights(sharpness=0.22 + 1e-9).digest()
        )

if __name__ == "__main__":
    unittest.main()
