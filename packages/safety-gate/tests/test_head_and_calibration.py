"""The arithmetic: the linear head, the Platt fit, and what each refuses.

Every head in this file is built by the test out of numbers the test wrote. The
product has no head (issue #79) and nothing here gives it one -- see
tests/support.py for why that distinction is the whole point.
"""

from __future__ import annotations

import math
import unittest

from support import axis, synthetic_calibration, synthetic_head  # noqa: E402

from memory_engine_safety.calibration import (  # noqa: E402
    MINIMUM_EXAMPLES_PER_CLASS,
    CalibrationError,
    PlattScaling,
    fit_per_class,
    fit_platt,
    load_calibration,
    sigmoid,
)
from memory_engine_safety.classes import CLASS_ORDER, ClassOrderMismatch  # noqa: E402
from memory_engine_safety.head import (  # noqa: E402
    HeadProvenance,
    LinearHead,
    load_head,
)
from memory_engine_safety.textinit import (  # noqa: E402
    TextTowerUnavailable,
    build_head,
    load_prompt_bank,
    verify_class_axis,
)


class TestTheLinearHead(unittest.TestCase):
    def test_a_logit_is_a_dot_product(self):
        head = synthetic_head()
        self.assertEqual((1.0, 0.0, 0.0), head.logits(axis(0, 1.0)))
        self.assertEqual((0.0, 1.0, 0.0), head.logits(axis(1, 1.0)))

    def test_summation_order_cannot_move_a_borderline_photograph(self):
        """math.fsum is exactly rounded, so batching cannot flip a verdict.

        Built to be nasty on purpose: a huge value, many tiny ones, and the huge
        value again with the opposite sign. Uncompensated left-to-right addition
        absorbs the whole tail into the rounding error of the first term and
        returns 0.0; the exact answer is 100.0.

        (CPython's builtin `sum` happens to compensate for floats as of 3.12, so
        the contrast is drawn against an explicit naive loop rather than against
        `sum` -- otherwise this test would be measuring an implementation detail
        of the interpreter instead of the property the head relies on.)
        """
        row = [1e16] + [1.0] * 100 + [-1e16]
        head = LinearHead(
            class_order=CLASS_ORDER,
            rows=tuple(tuple(row + [0.0] * (1152 - len(row))) for _ in CLASS_ORDER),
            bias=(0.0, 0.0, 0.0),
            space="siglip2_so400m_1152",
            provenance=HeadProvenance("text_tower_zero_shot", None, None, None),
        )
        ones = [1.0] * 1152
        self.assertEqual(100.0, head.logits(ones)[0])

        naive = 0.0
        for value in row:
            naive += value
        self.assertEqual(0.0, naive, "the naive contrast is no longer a contrast")

    def test_a_nan_weight_is_refused_rather_than_propagated(self):
        """`nan >= 0.3` and `nan < 0.3` are both False, so a NaN takes whichever
        branch reads as 'fine'."""
        rows = [list(axis(index)) for index in range(3)]
        rows[0][5] = float("nan")
        with self.assertRaises(ValueError) as caught:
            LinearHead(
                class_order=CLASS_ORDER,
                rows=tuple(tuple(row) for row in rows),
                bias=(0.0, 0.0, 0.0),
                space="siglip2_so400m_1152",
                provenance=HeadProvenance("text_tower_zero_shot", None, None, None),
            )
        self.assertIn("NaN", str(caught.exception))

    def test_a_head_over_another_space_is_refused(self):
        with self.assertRaises(ValueError):
            LinearHead(
                class_order=CLASS_ORDER,
                rows=tuple(tuple(axis(i)) for i in range(3)),
                bias=(0.0, 0.0, 0.0),
                space="clip_vit_l14_768",
                provenance=HeadProvenance("text_tower_zero_shot", None, None, None),
            )

    def test_a_head_with_a_transposed_class_order_is_refused_at_construction(self):
        with self.assertRaises(ClassOrderMismatch):
            LinearHead(
                class_order=("suggestive", "explicit", "medical_or_artistic"),
                rows=tuple(tuple(axis(i)) for i in range(3)),
                bias=(0.0, 0.0, 0.0),
                space="siglip2_so400m_1152",
                provenance=HeadProvenance("text_tower_zero_shot", None, None, None),
            )

    def test_the_artifact_round_trips(self):
        head = synthetic_head()
        self.assertEqual(head, load_head(head.to_artifact()))

    def test_an_unrecognised_artifact_version_is_denied_not_parsed(self):
        artifact = {**synthetic_head().to_artifact(), "artifact_version": 99}
        with self.assertRaises(ValueError):
            load_head(artifact)


class TestTheTextTowerIsAbsent(unittest.TestCase):
    """Issue #79, as a refusal rather than a stub."""

    def test_building_without_an_encoder_names_the_issue(self):
        with self.assertRaises(TextTowerUnavailable) as caught:
            build_head(None)
        message = str(caught.exception)
        self.assertIn("#79", message)
        self.assertIn("no fallback", message)

    def test_an_encoder_in_the_wrong_space_is_refused(self):
        class WrongSpace:
            space = "clip_vit_l14_768"
            dimensions = 768

            def encode(self, prompts):  # pragma: no cover - refused before use
                raise AssertionError("must not be called")

        with self.assertRaises(ValueError):
            build_head(WrongSpace())

    def test_a_deterministic_stand_in_encoder_builds_a_head_that_passes_its_probes(self):
        """Proves the CONSTRUCTION, not the model.

        The encoder here maps each prompt to a fixed axis chosen by its class,
        so the class means are orthogonal and the expected answer is knowable by
        hand. It embeds nothing and knows nothing about images.
        """
        bank = load_prompt_bank()
        order = [*CLASS_ORDER, bank.reference_class]

        class AxisEncoder:
            space = "siglip2_so400m_1152"
            dimensions = 1152

            def encode(self, prompts):
                index = next(
                    i for i, name in enumerate(order) if prompts == bank.prompts[name]
                )
                return [axis(index, 1.0) for _ in prompts]

        build = build_head(AxisEncoder(), bank=bank, note="test construction")
        verify_class_axis(build)
        self.assertEqual(bank.digest, build.head.provenance.prompt_bank_digest)
        self.assertEqual("text_tower_zero_shot", build.head.provenance.method)
        # Zero bias: the operating point is entirely the calibration's job.
        self.assertEqual((0.0, 0.0, 0.0), build.head.bias)
        for row in build.head.rows:
            self.assertAlmostEqual(
                1.0, math.sqrt(math.fsum(v * v for v in row)), places=9
            )


class TestPlattCalibration(unittest.TestCase):
    def _separable_table(self, n=400, spread=2.0):
        """Logits that a two-parameter curve can fit, with a real overlap.

        Deliberately not perfectly separable: a separable class has a singular
        Hessian and the fit is refused, which is its own test below.
        """
        logits, labels = [], []
        for index in range(n):
            positive = index % 2 == 0
            offset = (index % 20) / 20.0 - 0.5
            logits.append((spread if positive else -spread) + offset * 4.0)
            labels.append(positive)
        return logits, labels

    def test_a_fit_produces_a_calibrated_probability(self):
        logits, labels = self._separable_table()
        scale, bias = fit_platt(logits, labels, where="test")
        self.assertGreater(scale, 0.0)
        # The mean predicted probability should track the base rate.
        mean = sum(sigmoid(scale * v + bias) for v in logits) / len(logits)
        self.assertAlmostEqual(0.5, mean, delta=0.05)

    def test_the_fit_is_byte_identical_across_runs(self):
        """Determinism is what makes the fitted artifact hashable."""
        logits, labels = self._separable_table()
        self.assertEqual(
            fit_platt(logits, labels, where="a"), fit_platt(logits, labels, where="b")
        )

    def test_a_class_with_no_negatives_is_refused(self):
        with self.assertRaises(CalibrationError) as caught:
            fit_platt([0.1] * 300, [True] * 300, where="test")
        self.assertIn("never fires or always fires", str(caught.exception))

    def test_a_dozen_examples_is_refused_as_a_shipping_calibration(self):
        logits, labels = self._separable_table(n=12)
        with self.assertRaises(CalibrationError) as caught:
            fit_platt(logits, labels, where="test")
        self.assertIn(str(MINIMUM_EXAMPLES_PER_CLASS), str(caught.exception))

    def test_a_perfectly_separable_class_does_not_produce_a_verdict_of_1_or_0(self):
        """This is what Platt's prior correction buys, stated as a test.

        Regressing on hard 0/1 targets over a separable class drives the slope
        to infinity and pins every predicted probability at exactly 0 or 1 --
        which for a gate means a confidently WRONG verdict rather than an
        uncertain one, and no threshold anywhere can distinguish the two. With
        the corrected targets t+ = (N+ + 1)/(N+ + 2) and t- = 1/(N- + 2) the fit
        stays finite and the extremes stay off the boundary.
        """
        logits = [-50.0] * 200 + [50.0] * 200
        labels = [False] * 200 + [True] * 200
        scale, bias = fit_platt(logits, labels, where="test")
        self.assertTrue(math.isfinite(scale) and math.isfinite(bias))
        high = sigmoid(scale * 50.0 + bias)
        low = sigmoid(scale * -50.0 + bias)
        self.assertLess(high, 1.0, "a probability of exactly 1 cannot be wrong politely")
        self.assertGreater(low, 0.0)
        self.assertGreater(high, 0.9)
        self.assertLess(low, 0.1)

    def test_labels_are_taken_by_name_so_they_cannot_be_transposed(self):
        logits, labels = self._separable_table()
        rows = [[v, -v, 0.0] for v in logits]
        named = [
            {"explicit": label, "suggestive": not label, "medical_or_artistic": label}
            for label in labels
        ]
        fitted = fit_per_class(rows, named)
        self.assertEqual(CLASS_ORDER, fitted.class_order)
        self.assertEqual(3, len(fitted.scale))
        # `explicit` reads column 0 and `suggestive` column 1, which carry
        # opposite signs -- so both slopes must be positive if and only if the
        # columns were lined up with the names correctly.
        self.assertGreater(fitted.scale[0], 0.0)
        self.assertGreater(fitted.scale[1], 0.0)

    def test_a_missing_label_is_not_a_negative(self):
        logits, labels = self._separable_table()
        rows = [[v, v, v] for v in logits]
        named = [{"explicit": label, "suggestive": label} for label in labels]
        with self.assertRaises(CalibrationError) as caught:
            fit_per_class(rows, named)
        self.assertIn("missing label is not a negative", str(caught.exception))

    def test_probabilities_are_independent_sigmoids_not_a_softmax(self):
        """A breastfeeding photograph should be able to score high on two."""
        calibration = synthetic_calibration()
        probabilities = calibration.probabilities([1.0, 1.0, 1.0])
        self.assertGreater(sum(probabilities), 1.5)
        for value in probabilities:
            self.assertGreater(value, 0.5)

    def test_a_non_finite_parameter_is_refused(self):
        with self.assertRaises(CalibrationError):
            PlattScaling(
                class_order=CLASS_ORDER,
                scale=(1.0, float("nan"), 1.0),
                bias=(0.0, 0.0, 0.0),
                support=((10, 10),) * 3,
            )

    def test_the_calibration_artifact_round_trips(self):
        calibration = synthetic_calibration()
        self.assertEqual(calibration, load_calibration(calibration.to_artifact()))

    def test_sigmoid_does_not_overflow_in_either_tail(self):
        """The naive form raises OverflowError at -800; this one does not.

        It saturates to 0.0 and 1.0, which is fine -- what is not fine is an
        exception in the middle of a fit, which surfaces as "calibration failed"
        with no indication that the cause was one outlying logit.
        """
        self.assertEqual(0.0, sigmoid(-800.0))
        self.assertEqual(1.0, sigmoid(800.0))
        self.assertEqual(0.5, sigmoid(0.0))
        with self.assertRaises(OverflowError):
            1.0 / (1.0 + math.exp(800.0))


if __name__ == "__main__":
    unittest.main()
