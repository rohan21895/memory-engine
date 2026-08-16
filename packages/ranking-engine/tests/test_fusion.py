"""Score fusion v1, tested for the behaviours that decide what a user sees.

The interesting cases are not "does the arithmetic work". They are the ones
where a plausible implementation is quietly wrong: a photo penalised for having
been measured less, a landscape penalised for containing no faces, a black frame
that wins because everything around it is worse, and a tie that reorders between
runs.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from memory_engine_ranking.fusion import (  # noqa: E402
    DEFAULT_MIN_COVERAGE,
    FEATURE_SET_ID,
    REJECT_BELOW_SHARPNESS_FLOOR,
    REJECT_BLACK_FRAME,
    REJECT_LENS_OBSTRUCTED,
    FusedScore,
    Signals,
    Weights,
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
        "has_faces": True,
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

    def test_the_sharpness_floor_is_an_elimination_not_a_quality_bar(self):
        """A dim handheld shot of a first birthday is worth keeping. Only
        genuinely unrecoverable blur is eliminated."""
        self.assertIsNone(eliminate(measured(sharpness=0.15)))
        self.assertEqual(
            REJECT_BELOW_SHARPNESS_FLOOR, eliminate(measured(sharpness=0.02))
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
        partial = fuse(Signals(sharpness=0.9, exposure=0.9, has_faces=False))
        self.assertGreater(partial.value, 0.8)

    def test_a_missing_signal_is_not_invented(self):
        """Treating missing as 0.5 would make it indistinguishable from a real
        mediocre measurement, and no later audit could separate them."""
        score = fuse(Signals(sharpness=0.9, exposure=0.9, has_faces=False))
        self.assertNotIn("aesthetic", score.as_feature_map())
        self.assertEqual({"sharpness", "exposure"}, set(score.as_feature_map()))

    def test_measuring_more_signals_does_not_by_itself_lower_the_score(self):
        """The third wrong approach: summing present signals and ignoring the
        rest, so a photo scores lower purely for having been measured less."""
        two = fuse(Signals(sharpness=0.7, exposure=0.7, has_faces=False))
        many = fuse(Signals(sharpness=0.7, exposure=0.7, noise=0.7, contrast=0.7,
                            technical_iqa=0.7, aesthetic=0.7, composition=0.7,
                            has_faces=False))
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
        partial = fuse(Signals(sharpness=0.9, exposure=0.9, has_faces=True))
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
        partial = fuse(Signals(sharpness=0.8, exposure=0.7, has_faces=True))
        full = fuse(Signals(sharpness=0.8, exposure=0.7, noise=0.6, contrast=0.65,
                            technical_iqa=0.72, aesthetic=0.55, composition=0.6,
                            has_faces=False))
        self.assertGreater(partial.value, full.value)
        self.assertFalse(partial.comparable)
        self.assertTrue(full.comparable)
        self.assertEqual(["full"], rank({"partial": partial, "full": full},
                                        require_comparable=True))

    def test_a_landscape_is_not_an_under_measured_portrait(self):
        """`face_quality` is null for a mountain and for an unprocessed portrait.
        Counting the face weight against the mountain would cap every landscape
        below the comparability threshold, quietly excluding scenery from
        automated output."""
        landscape = fuse(Signals(sharpness=0.8, exposure=0.7, noise=0.6,
                                 contrast=0.65, technical_iqa=0.72,
                                 aesthetic=0.55, composition=0.6,
                                 has_faces=False))
        self.assertEqual(1.0, landscape.coverage)
        self.assertTrue(landscape.comparable)

        portrait = fuse(Signals(sharpness=0.8, exposure=0.7, noise=0.6,
                                contrast=0.65, technical_iqa=0.72,
                                aesthetic=0.55, composition=0.6,
                                has_faces=True))
        self.assertLess(portrait.coverage, 1.0)

    def test_a_face_score_is_ignored_when_the_photo_has_no_faces(self):
        """A stale or mistaken face_quality on a landscape must not move the
        number."""
        without = fuse(Signals(sharpness=0.8, exposure=0.7, has_faces=False))
        with_stale = fuse(Signals(sharpness=0.8, exposure=0.7,
                                  face_quality=0.01, has_faces=False))
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


class TestProvenance(unittest.TestCase):
    """A score you cannot attribute is not reproducible -- the same argument
    that put a config digest in ModelPin."""

    def test_the_score_records_which_weights_produced_it(self):
        score = fuse(measured())
        self.assertEqual("default-v1", score.weights_id)
        self.assertEqual(Weights().digest(), score.weights_digest)
        self.assertEqual(FEATURE_SET_ID, score.feature_set_id)

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
        text = explain(fuse(Signals(sharpness=0.9, exposure=0.9, has_faces=True)))
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
        perfect = fuse(Signals(*(1.0,) * 8, has_faces=True))
        self.assertEqual(1.0, perfect.value)
        # Sharpness must clear the elimination floor, so "worthless" is the
        # worst score a frame can have while still being a candidate.
        worthless = fuse(Signals(0.1, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0,
                                 has_faces=True))
        self.assertLess(worthless.value, 0.03)

    def test_a_fused_score_is_hashable_and_frozen(self):
        """It gets stored, compared and used as a dict key; mutating one after
        the fact would detach it from the weights digest it claims."""
        score = fuse(measured())
        self.assertIsInstance(hash(score), int)
        with self.assertRaises(Exception):
            score.value = 0.5  # type: ignore[misc]


if __name__ == "__main__":
    unittest.main()
