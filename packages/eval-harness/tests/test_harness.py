"""The regression gate, tested for the failures that would ship silently.

The interesting cases are not "does it subtract two numbers". They are the ones
where a plausible harness returns a confident, wrong PASS:

  * the mean improves while one category collapses
  * the candidate crashes on the hardest case and the mean rises because that
    case is gone
  * a lower-is-better metric gets worse and reads as an improvement
  * the candidate quietly loaded the baseline's weights and every delta is 0.0
  * a second model changed too, so the delta belongs to something else
  * a waiver written for last month's swap silently covers this month's
  * the metric's own run-to-run noise is wider than the threshold gating it

Every assertion below is about a number no exception would ever have raised on.
"""

from __future__ import annotations

import random
import sys
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from memory_engine_eval.harness import (  # noqa: E402
    BENCHMARK_CATEGORIES,
    BenchmarkCase,
    CaseDefinitionError,
    CaseResult,
    CategoryPolicy,
    GateRefused,
    HarnessError,
    MetricDirection,
    MissingBaseline,
    ModelPin,
    ModelSet,
    Policy,
    PolicyError,
    RunReport,
    ViolationKind,
    Waiver,
    WaiverScope,
    comparison_digest,
    evaluate,
    model_set_digest,
)

W_OLD = "a" * 64
W_NEW = "b" * 64
C_OLD = "c" * 64
C_NEW = "d" * 64
W_FACE = "e" * 64
C_FACE = "f" * 64
INPUTS = "1" * 64

NOW = datetime(2026, 8, 16, 12, 0, 0, tzinfo=timezone.utc)


def base_models() -> ModelSet:
    return ModelSet.of(
        {
            "face_detect": ModelPin("scrfd-10g", "1.0", W_FACE, C_FACE),
            "image_embedding": ModelPin("siglip2-base", "2.0", W_OLD, C_OLD),
        }
    )


def swapped_models() -> ModelSet:
    """One role changed. The controlled experiment the gate assumes."""
    return ModelSet.of(
        {
            "face_detect": ModelPin("scrfd-10g", "1.0", W_FACE, C_FACE),
            "image_embedding": ModelPin("siglip2-so400m", "3.0", W_NEW, C_NEW),
        }
    )


def scenario(
    values: dict[str, tuple[str, float, float]],
    *,
    metric: str = "reel_acceptance",
    direction: MetricDirection = MetricDirection.HIGHER_IS_BETTER,
    candidate_models: ModelSet | None = None,
    tolerance: float = 0.0,
) -> tuple[list[BenchmarkCase], RunReport, RunReport]:
    """case_id -> (category, baseline value, candidate value).

    `expected` is pinned to the baseline value with zero tolerance, so any test
    that wants baseline drift has to introduce it deliberately.
    """
    cases: list[BenchmarkCase] = []
    baseline_results: list[CaseResult] = []
    candidate_results: list[CaseResult] = []
    for case_id in sorted(values):
        category, base_value, cand_value = values[case_id]
        cases.append(
            BenchmarkCase(
                case_id=case_id,
                category=category,
                metric=metric,
                direction=direction,
                inputs_digest=INPUTS,
                baseline_pins=ModelSet.of(
                    {"image_embedding": ModelPin("siglip2-base", "2.0", W_OLD, C_OLD)}
                ),
                expected=base_value,
                tolerance=tolerance,
            )
        )
        baseline_results.append(CaseResult(case_id, (base_value,)))
        candidate_results.append(CaseResult(case_id, (cand_value,)))
    return (
        cases,
        RunReport("baseline", base_models(), tuple(baseline_results)),
        RunReport("candidate", candidate_models or swapped_models(), tuple(candidate_results)),
    )


def policy_for(
    categories: tuple[str, ...],
    *,
    floor: float | None = None,
    max_mean_regression: float = 0.02,
    **kwargs: object,
) -> Policy:
    defaults: dict[str, object] = {
        "categories": tuple(sorted(categories)),
        "default_category": CategoryPolicy(
            worst_acceptable_mean=floor, max_mean_regression=max_mean_regression
        ),
        "min_cases_per_category": 1,
    }
    defaults.update(kwargs)
    return Policy(**defaults)  # type: ignore[arg-type]


def kinds(report) -> list[str]:
    return sorted(v.kind.value for v in report.violations if not v.waived)


class TestModelIdentity(unittest.TestCase):
    def test_from_ref_reads_a_contract_model_ref(self):
        """The adapter exists from the start.

        The ranking engine shipped referencing the contract in prose and
        bypassing it in practice; the adapter was the thing left out.
        """
        pin = ModelPin.from_ref(
            {
                "model_id": "siglip2-base",
                "version": "2.0",
                "weights_blake3": W_OLD,
                "config_blake3": C_OLD,
                "runtime": "onnxruntime_coreml",
                "precision": "fp16",
            }
        )
        self.assertEqual(pin.behaviour_key, ("siglip2-base", W_OLD, C_OLD))

    def test_version_string_is_not_identity(self):
        """"The same version" of a HF repo has changed weights before -- the
        contract says so. Identity is the digests."""
        a = ModelPin("siglip2-base", "2.0", W_OLD, C_OLD)
        b = ModelPin("siglip2-base", "2.1-hotfix", W_OLD, C_OLD)
        self.assertEqual(a.behaviour_key, b.behaviour_key)

    def test_config_alone_changes_identity(self):
        """A config change moves every threshold while the weights hash stays
        byte-identical. If the digest ignored config, the swap would be
        invisible."""
        a = ModelPin("siglip2-base", "2.0", W_OLD, C_OLD)
        b = ModelPin("siglip2-base", "2.0", W_OLD, C_NEW)
        self.assertNotEqual(a.behaviour_key, b.behaviour_key)
        self.assertNotEqual(
            model_set_digest(ModelSet.of({"r": a})),
            model_set_digest(ModelSet.of({"r": b})),
        )

    def test_model_set_digest_is_order_independent(self):
        forward = ModelSet.of(
            {"a": ModelPin("m-a", "1", W_OLD), "b": ModelPin("m-b", "1", W_NEW)}
        )
        backward = ModelSet.of(
            {"b": ModelPin("m-b", "1", W_NEW), "a": ModelPin("m-a", "1", W_OLD)}
        )
        self.assertEqual(model_set_digest(forward), model_set_digest(backward))

    def test_comparison_digest_is_directional(self):
        """A waiver for the A->B swap must not cover the rollback B->A."""
        a, b = base_models(), swapped_models()
        self.assertNotEqual(comparison_digest(a, b), comparison_digest(b, a))

    def test_unsorted_pins_are_refused(self):
        with self.assertRaises(HarnessError):
            ModelSet((("z", ModelPin("m-z", "1", W_OLD)), ("a", ModelPin("m-a", "1", W_OLD))))


class TestPolicyValidation(unittest.TestCase):
    def test_noise_allowance_must_be_below_the_case_threshold(self):
        """A threshold inside the noise floor cannot detect what it gates on;
        it emits PASS and FAIL and both are coin flips."""
        policy = policy_for(("travel",), max_case_regression=0.004, max_nondeterminism=0.005)
        with self.assertRaises(PolicyError):
            policy.validate()

    def test_noise_allowance_must_be_below_every_category_threshold(self):
        policy = policy_for(("travel",), max_mean_regression=0.005, max_nondeterminism=0.005)
        with self.assertRaises(PolicyError):
            policy.validate()

    def test_floor_for_an_undeclared_category_is_refused(self):
        """A floor keyed to a category that does not exist reads as protection
        in review and never runs."""
        policy = Policy(
            categories=("travel",),
            per_category={"drone": CategoryPolicy(worst_acceptable_mean=0.9)},
        )
        with self.assertRaises(PolicyError):
            policy.validate()

    def test_digest_tracks_the_floor(self):
        a = policy_for(("travel",), floor=0.70)
        b = policy_for(("travel",), floor=0.60)
        self.assertNotEqual(a.digest(), b.digest())

    def test_digest_is_insertion_order_independent(self):
        one = Policy(
            categories=("drone", "travel"),
            per_category={
                "travel": CategoryPolicy(0.7),
                "drone": CategoryPolicy(0.8),
            },
        )
        two = Policy(
            categories=("drone", "travel"),
            per_category={
                "drone": CategoryPolicy(0.8),
                "travel": CategoryPolicy(0.7),
            },
        )
        self.assertEqual(one.digest(), two.digest())

    def test_empty_case_list_does_not_pass_vacuously(self):
        with self.assertRaises(CaseDefinitionError):
            evaluate([], RunReport("b", base_models(), ()),
                     RunReport("c", base_models(), ()), policy_for(("travel",)))


class TestComparability(unittest.TestCase):
    """Refusals. Not FAILs -- a refusal says no verdict exists."""

    def test_uncontrolled_second_change_is_refused(self):
        cases, baseline, candidate = scenario({"t1": ("travel", 0.80, 0.81)})
        also_face_changed = ModelSet.of(
            {
                "face_detect": ModelPin("scrfd-10g", "2.0", W_NEW, C_FACE),
                "image_embedding": ModelPin("siglip2-so400m", "3.0", W_NEW, C_NEW),
            }
        )
        candidate = replace(candidate, models=also_face_changed)
        with self.assertRaises(GateRefused) as caught:
            evaluate(cases, baseline, candidate, policy_for(("travel",)),
                     under_test=["image_embedding"])
        self.assertIn("face_detect", str(caught.exception))

    def test_role_declared_under_test_that_did_not_change_is_refused(self):
        """The wiring bug that produces a confident PASS for a model that never
        ran: the job points at the new weights, a path falls back, every delta
        is 0.0."""
        cases, baseline, candidate = scenario(
            {"t1": ("travel", 0.80, 0.80)}, candidate_models=base_models()
        )
        with self.assertRaises(GateRefused) as caught:
            evaluate(cases, baseline, candidate, policy_for(("travel",)),
                     under_test=["image_embedding"])
        self.assertIn("byte-identical", str(caught.exception))

    def test_no_swap_and_no_declaration_is_a_valid_code_change_gate(self):
        cases, baseline, candidate = scenario(
            {"t1": ("travel", 0.80, 0.81)}, candidate_models=base_models()
        )
        report = evaluate(cases, baseline, candidate, policy_for(("travel",)))
        self.assertTrue(report.passed)
        self.assertEqual(report.swapped_roles, ())

    def test_added_role_is_refused(self):
        cases, baseline, candidate = scenario({"t1": ("travel", 0.80, 0.81)})
        bigger = ModelSet.of(
            {
                "face_detect": ModelPin("scrfd-10g", "1.0", W_FACE, C_FACE),
                "image_embedding": ModelPin("siglip2-so400m", "3.0", W_NEW, C_NEW),
                "reranker": ModelPin("rerank", "1.0", W_OLD, C_OLD),
            }
        )
        with self.assertRaises(GateRefused):
            evaluate(cases, baseline, replace(candidate, models=bigger),
                     policy_for(("travel",)), under_test=["image_embedding"])

    def test_unpinned_weights_cannot_gate(self):
        cases, baseline, candidate = scenario({"t1": ("travel", 0.80, 0.81)})
        unpinned = ModelSet.of(
            {
                "face_detect": ModelPin("scrfd-10g", "1.0", W_FACE, C_FACE),
                "image_embedding": ModelPin("siglip2-so400m", "3.0", None, C_NEW),
            }
        )
        with self.assertRaises(GateRefused) as caught:
            evaluate(cases, baseline, replace(candidate, models=unpinned),
                     policy_for(("travel",)), under_test=["image_embedding"])
        self.assertIn("unreproducible", str(caught.exception))

    def test_case_pinned_to_a_baseline_that_no_longer_matches_is_refused(self):
        """The baseline was regenerated with different weights and the case was
        not updated, so its expected outcome describes a third model."""
        cases, baseline, candidate = scenario({"t1": ("travel", 0.80, 0.81)})
        stale = replace(
            cases[0],
            baseline_pins=ModelSet.of(
                {"image_embedding": ModelPin("siglip2-base", "2.0", W_NEW, C_OLD)}
            ),
        )
        with self.assertRaises(GateRefused) as caught:
            evaluate([stale], baseline, candidate, policy_for(("travel",)),
                     under_test=["image_embedding"])
        self.assertIn("no longer on either side", str(caught.exception))

    def test_under_test_names_a_role_that_does_not_exist(self):
        cases, baseline, candidate = scenario({"t1": ("travel", 0.80, 0.81)})
        with self.assertRaises(GateRefused):
            evaluate(cases, baseline, candidate, policy_for(("travel",)),
                     under_test=["image_embedding", "typo_role"])

    def test_result_for_an_unknown_case_is_refused(self):
        cases, baseline, candidate = scenario({"t1": ("travel", 0.80, 0.81)})
        candidate = replace(
            candidate, results=candidate.results + (CaseResult("t9", (0.9,)),)
        )
        with self.assertRaises(GateRefused):
            evaluate(cases, baseline, candidate, policy_for(("travel",)),
                     under_test=["image_embedding"])

    def test_duplicate_result_is_refused(self):
        cases, baseline, candidate = scenario({"t1": ("travel", 0.80, 0.81)})
        candidate = replace(
            candidate, results=candidate.results + (CaseResult("t1", (0.99,)),)
        )
        with self.assertRaises(HarnessError):
            evaluate(cases, baseline, candidate, policy_for(("travel",)),
                     under_test=["image_embedding"])

    def test_case_in_an_undeclared_category_is_refused(self):
        """A typo'd category quietly forms its own category and vanishes from
        the one it was meant to protect."""
        cases, baseline, candidate = scenario({"t1": ("baby_familly", 0.80, 0.81)})
        with self.assertRaises(CaseDefinitionError):
            evaluate(cases, baseline, candidate, policy_for(("baby_family",)),
                     under_test=["image_embedding"])

    def test_case_with_no_pins_is_refused(self):
        with self.assertRaises(CaseDefinitionError):
            BenchmarkCase(
                case_id="t1",
                category="travel",
                metric="m",
                direction=MetricDirection.HIGHER_IS_BETTER,
                inputs_digest=INPUTS,
                baseline_pins=ModelSet(()),
                expected=0.5,
            )


class TestPerCategoryFloors(unittest.TestCase):
    """The whole reason this module exists."""

    def test_mean_improves_while_one_category_collapses(self):
        cases, baseline, candidate = scenario(
            {
                "w1": ("indian_weddings", 0.80, 0.90),
                "w2": ("indian_weddings", 0.80, 0.90),
                "b1": ("baby_family", 0.80, 0.50),
                "b2": ("baby_family", 0.80, 0.50),
            }
        )
        report = evaluate(
            cases, baseline, candidate,
            policy_for(("baby_family", "indian_weddings"), floor=0.70),
            under_test=["image_embedding"],
        )
        # The mean genuinely improved. A mean-based gate ships this.
        self.assertGreater(report.overall_candidate_mean, report.overall_baseline_mean)
        self.assertFalse(report.passed)
        self.assertTrue(report.mean_masked_regression)
        self.assertIn("category_floor", kinds(report))
        self.assertIn("category_regression", kinds(report))
        baby = next(c for c in report.categories if c.category == "baby_family")
        wedding = next(c for c in report.categories if c.category == "indian_weddings")
        self.assertFalse(baby.passed)
        self.assertTrue(wedding.passed)

    def test_clean_improvement_passes(self):
        cases, baseline, candidate = scenario(
            {
                "w1": ("indian_weddings", 0.80, 0.83),
                "b1": ("baby_family", 0.78, 0.79),
            }
        )
        report = evaluate(
            cases, baseline, candidate,
            policy_for(("baby_family", "indian_weddings"), floor=0.70),
            under_test=["image_embedding"],
        )
        self.assertTrue(report.passed, report.summary())
        self.assertFalse(report.mean_masked_regression)
        self.assertEqual(report.violations, ())

    def test_floor_is_inclusive(self):
        """The floor is the worst ACCEPTABLE value: a policy stating 0.70 is
        acceptable must not reject 0.70."""
        cases, baseline, candidate = scenario({"b1": ("baby_family", 0.72, 0.70)})
        report = evaluate(
            cases, baseline, candidate,
            policy_for(("baby_family",), floor=0.70, max_mean_regression=0.05),
            under_test=["image_embedding"],
        )
        self.assertTrue(report.passed, report.summary())

    def test_one_step_below_the_floor_fails(self):
        cases, baseline, candidate = scenario({"b1": ("baby_family", 0.72, 0.699999)})
        report = evaluate(
            cases, baseline, candidate,
            policy_for(("baby_family",), floor=0.70, max_mean_regression=0.05),
            under_test=["image_embedding"],
        )
        self.assertFalse(report.passed)
        self.assertEqual(kinds(report), ["category_floor"])

    def test_regression_above_the_floor_still_fails(self):
        """A category far above its floor can still lose a lot in one swap. The
        absolute bar alone would let it."""
        cases, baseline, candidate = scenario(
            {"d1": ("drone", 0.98, 0.90), "d2": ("drone", 0.98, 0.90)}
        )
        report = evaluate(
            cases, baseline, candidate,
            policy_for(("drone",), floor=0.50, max_case_regression=0.50),
            under_test=["image_embedding"],
        )
        self.assertFalse(report.passed)
        self.assertEqual(kinds(report), ["category_regression"])

    def test_single_case_collapse_inside_a_healthy_category(self):
        """The category mean absorbs it; the per-case bar does not."""
        cases, baseline, candidate = scenario(
            {
                "g1": ("gopro_adventure", 0.80, 0.95),
                "g2": ("gopro_adventure", 0.80, 0.95),
                "g3": ("gopro_adventure", 0.80, 0.50),
            }
        )
        report = evaluate(
            cases, baseline, candidate,
            policy_for(("gopro_adventure",), floor=0.60),
            under_test=["image_embedding"],
        )
        category = report.categories[0]
        self.assertGreater(category.candidate_mean, category.baseline_mean)
        self.assertFalse(report.passed)
        self.assertEqual(kinds(report), ["case_regression"])

    def test_category_absent_from_the_case_list_fails(self):
        """An unmeasured category cannot fail, which is not the same as passing."""
        cases, baseline, candidate = scenario({"t1": ("travel", 0.80, 0.81)})
        report = evaluate(
            cases, baseline, candidate,
            policy_for(("drone", "travel")),
            under_test=["image_embedding"],
        )
        self.assertFalse(report.passed)
        self.assertEqual(kinds(report), ["category_missing"])

    def test_default_policy_names_the_build_plan_categories(self):
        self.assertEqual(
            BENCHMARK_CATEGORIES,
            (
                "baby_family",
                "drone",
                "gopro_adventure",
                "indian_festivals",
                "indian_weddings",
                "travel",
            ),
        )
        self.assertEqual(Policy().categories, BENCHMARK_CATEGORIES)


class TestMetricDirection(unittest.TestCase):
    """A gate that assumes higher-is-better reports a rise in the face
    false-match rate as an improvement."""

    def lower_is_better(self, values):
        return scenario(
            values,
            metric="beat_alignment_error_ms",
            direction=MetricDirection.LOWER_IS_BETTER,
        )

    def test_lower_is_better_metric_getting_worse_fails(self):
        cases, baseline, candidate = self.lower_is_better(
            {"m1": ("travel", 20.0, 48.0), "m2": ("travel", 20.0, 48.0)}
        )
        report = evaluate(
            cases, baseline, candidate,
            policy_for(("travel",), floor=50.0, max_case_regression=5.0,
                       max_mean_regression=5.0, max_nondeterminism=0.5),
            under_test=["image_embedding"],
        )
        self.assertFalse(report.passed, report.summary())
        self.assertIn("case_regression", kinds(report))
        self.assertIn("category_regression", kinds(report))
        self.assertEqual(report.case_deltas[0].regression, 28.0)

    def test_lower_is_better_metric_getting_better_passes(self):
        cases, baseline, candidate = self.lower_is_better(
            {"m1": ("travel", 48.0, 20.0), "m2": ("travel", 48.0, 20.0)}
        )
        report = evaluate(
            cases, baseline, candidate,
            policy_for(("travel",), floor=50.0, max_case_regression=5.0,
                       max_mean_regression=5.0, max_nondeterminism=0.5),
            under_test=["image_embedding"],
        )
        self.assertTrue(report.passed, report.summary())
        self.assertEqual(report.case_deltas[0].regression, -28.0)

    def test_lower_is_better_floor_is_a_ceiling_in_value_terms(self):
        cases, baseline, candidate = self.lower_is_better({"m1": ("travel", 48.0, 49.0)})
        report = evaluate(
            cases, baseline, candidate,
            policy_for(("travel",), floor=48.5, max_case_regression=5.0,
                       max_mean_regression=5.0, max_nondeterminism=0.5),
            under_test=["image_embedding"],
        )
        self.assertFalse(report.passed)
        self.assertEqual(kinds(report), ["category_floor"])

    def test_mixed_metrics_in_one_category_are_refused(self):
        cases, baseline, candidate = scenario(
            {"a1": ("travel", 0.80, 0.81), "a2": ("travel", 0.80, 0.81)}
        )
        cases[1] = replace(cases[1], metric="beat_alignment_error_ms")
        with self.assertRaises(CaseDefinitionError):
            evaluate(cases, baseline, candidate, policy_for(("travel",)),
                     under_test=["image_embedding"])

    def test_overall_mean_is_undefined_across_metrics(self):
        """Averaging milliseconds with acceptance rates makes a headline number
        that moves for reasons nobody can name."""
        cases, baseline, candidate = scenario(
            {"t1": ("travel", 0.80, 0.81), "d1": ("drone", 0.80, 0.81)}
        )
        cases = [
            replace(c, metric="beat_alignment_error_ms",
                    direction=MetricDirection.LOWER_IS_BETTER)
            if c.category == "drone" else c
            for c in cases
        ]
        report = evaluate(
            cases, baseline, candidate,
            policy_for(("drone", "travel")),
            under_test=["image_embedding"],
        )
        self.assertIsNone(report.overall_candidate_mean)
        self.assertFalse(report.mean_masked_regression)


class TestMissingResults(unittest.TestCase):
    def test_dropping_the_hard_case_does_not_manufacture_a_pass(self):
        """Without pairing, removing the worst case raises the mean from 0.70 to
        0.80 and the gate goes green on a candidate that cannot process the
        input at all."""
        cases, baseline, candidate = scenario(
            {
                "b1": ("baby_family", 0.80, 0.80),
                "b2": ("baby_family", 0.80, 0.80),
                "b3": ("baby_family", 0.80, 0.40),
            }
        )
        candidate = replace(
            candidate, results=tuple(r for r in candidate.results if r.case_id != "b3")
        )
        report = evaluate(
            cases, baseline, candidate,
            policy_for(("baby_family",), floor=0.75),
            under_test=["image_embedding"],
        )
        self.assertFalse(report.passed, report.summary())
        self.assertEqual(kinds(report), ["case_not_run"])
        category = report.categories[0]
        self.assertEqual(category.n_cases, 2)
        self.assertEqual(category.baseline_mean, 0.80)
        self.assertEqual(category.candidate_mean, 0.80)

    def test_category_means_use_the_same_case_set_on_both_sides(self):
        cases, baseline, candidate = scenario(
            {
                "b1": ("baby_family", 0.90, 0.90),
                "b2": ("baby_family", 0.30, 0.30),
            }
        )
        candidate = replace(
            candidate, results=tuple(r for r in candidate.results if r.case_id != "b2")
        )
        report = evaluate(
            cases, baseline, candidate,
            policy_for(("baby_family",), floor=0.50),
            under_test=["image_embedding"],
        )
        category = report.categories[0]
        # Baseline mean must be 0.90 (the paired case only), not 0.60 (all of
        # them) -- otherwise the missing case reads as a huge improvement.
        self.assertEqual(category.baseline_mean, 0.90)
        self.assertEqual(category.delta, 0.0)

    def test_missing_baseline_is_refused_by_default(self):
        cases, baseline, candidate = scenario({"t1": ("travel", 0.80, 0.81)})
        baseline = replace(baseline, results=())
        with self.assertRaises(GateRefused):
            evaluate(cases, baseline, candidate, policy_for(("travel",)),
                     under_test=["image_embedding"])

    def test_new_case_is_checked_against_the_floor_not_averaged_in(self):
        cases, baseline, candidate = scenario(
            {"t1": ("travel", 0.80, 0.80), "t2": ("travel", 0.80, 0.40)}
        )
        baseline = replace(
            baseline, results=tuple(r for r in baseline.results if r.case_id != "t2")
        )
        report = evaluate(
            cases, baseline, candidate,
            policy_for(("travel",), floor=0.70,
                       on_missing_baseline=MissingBaseline.ADMIT_AS_NEW),
            under_test=["image_embedding"],
        )
        self.assertEqual(report.new_cases, ("t2",))
        self.assertEqual(report.categories[0].n_cases, 1)
        self.assertEqual(report.categories[0].candidate_mean, 0.80)
        self.assertEqual(kinds(report), ["new_case_below_floor"])

    def test_good_new_case_passes(self):
        cases, baseline, candidate = scenario(
            {"t1": ("travel", 0.80, 0.80), "t2": ("travel", 0.80, 0.90)}
        )
        baseline = replace(
            baseline, results=tuple(r for r in baseline.results if r.case_id != "t2")
        )
        report = evaluate(
            cases, baseline, candidate,
            policy_for(("travel",), floor=0.70,
                       on_missing_baseline=MissingBaseline.ADMIT_AS_NEW),
            under_test=["image_embedding"],
        )
        self.assertTrue(report.passed, report.summary())
        self.assertEqual(report.new_cases, ("t2",))


class TestNondeterminism(unittest.TestCase):
    def test_spread_wider_than_the_allowance_blinds_the_gate(self):
        cases, baseline, candidate = scenario({"t1": ("travel", 0.80, 0.80)})
        candidate = replace(candidate, results=(CaseResult("t1", (0.79, 0.81, 0.80)),))
        report = evaluate(
            cases, baseline, candidate,
            policy_for(("travel",), max_nondeterminism=0.005),
            under_test=["image_embedding"],
        )
        self.assertFalse(report.passed, report.summary())
        self.assertEqual(kinds(report), ["nondeterministic"])
        self.assertAlmostEqual(report.case_deltas[0].candidate_spread, 0.02)

    def test_spread_at_the_allowance_is_accepted(self):
        cases, baseline, candidate = scenario({"t1": ("travel", 0.80, 0.80)})
        candidate = replace(candidate, results=(CaseResult("t1", (0.7975, 0.8025)),))
        report = evaluate(
            cases, baseline, candidate,
            policy_for(("travel",), max_nondeterminism=0.005),
            under_test=["image_embedding"],
        )
        self.assertTrue(report.passed, report.summary())

    def test_baseline_nondeterminism_is_caught_too(self):
        cases, baseline, candidate = scenario({"t1": ("travel", 0.80, 0.80)})
        baseline = replace(baseline, results=(CaseResult("t1", (0.78, 0.82)),))
        report = evaluate(
            cases, baseline, candidate,
            policy_for(("travel",), max_nondeterminism=0.005),
            under_test=["image_embedding"],
        )
        self.assertEqual(kinds(report), ["nondeterministic"])

    def test_single_replicate_asserts_determinism_and_is_not_checked(self):
        cases, baseline, candidate = scenario({"t1": ("travel", 0.80, 0.80)})
        report = evaluate(cases, baseline, candidate, policy_for(("travel",)),
                          under_test=["image_embedding"])
        self.assertTrue(report.passed)
        self.assertEqual(report.case_deltas[0].candidate_spread, 0.0)

    def test_replicates_aggregate_to_their_mean(self):
        cases, baseline, candidate = scenario({"t1": ("travel", 0.80, 0.80)})
        candidate = replace(candidate, results=(CaseResult("t1", (0.80, 0.804, 0.802)),))
        report = evaluate(
            cases, baseline, candidate,
            policy_for(("travel",), max_nondeterminism=0.005),
            under_test=["image_embedding"],
        )
        self.assertAlmostEqual(report.case_deltas[0].candidate, 0.802, places=6)

    def test_non_finite_value_is_refused(self):
        """NaN compares False to every threshold and passes every check."""
        with self.assertRaises(HarnessError):
            CaseResult("t1", (float("nan"),))


class TestBaselineDrift(unittest.TestCase):
    def test_baseline_that_no_longer_reproduces_the_case_fails(self):
        cases, baseline, candidate = scenario({"t1": ("travel", 0.80, 0.81)})
        baseline = replace(baseline, results=(CaseResult("t1", (0.60,)),))
        report = evaluate(
            cases, baseline, candidate,
            policy_for(("travel",), max_case_regression=1.0, max_mean_regression=1.0),
            under_test=["image_embedding"],
        )
        self.assertFalse(report.passed)
        self.assertIn("baseline_drift", kinds(report))

    def test_drift_within_tolerance_is_accepted(self):
        cases, baseline, candidate = scenario(
            {"t1": ("travel", 0.80, 0.81)}, tolerance=0.01
        )
        baseline = replace(baseline, results=(CaseResult("t1", (0.795,)),))
        report = evaluate(cases, baseline, candidate, policy_for(("travel",)),
                          under_test=["image_embedding"])
        self.assertTrue(report.passed, report.summary())


class TestThinCategory(unittest.TestCase):
    def test_too_few_cases_cannot_certify_a_category(self):
        cases, baseline, candidate = scenario(
            {"b1": ("baby_family", 0.80, 0.80), "b2": ("baby_family", 0.80, 0.80)}
        )
        report = evaluate(
            cases, baseline, candidate,
            policy_for(("baby_family",), min_cases_per_category=3),
            under_test=["image_embedding"],
        )
        self.assertFalse(report.passed)
        self.assertEqual(kinds(report), ["thin_category"])


class TestWaivers(unittest.TestCase):
    def waiver(self, baseline, candidate, **overrides):
        fields = {
            "scope": WaiverScope.CASE,
            "target": "b3",
            "kind": ViolationKind.CASE_REGRESSION,
            "max_magnitude": 0.10,
            "reason": "known low-light indoor regression, tracked in issue #212",
            "approver": "rohan",
            "expires_at": NOW + timedelta(days=30),
            "comparison": comparison_digest(baseline.models, candidate.models),
        }
        fields.update(overrides)
        return Waiver(**fields)  # type: ignore[arg-type]

    def regressing_scenario(self):
        return scenario(
            {
                "b1": ("baby_family", 0.80, 0.80),
                "b2": ("baby_family", 0.80, 0.80),
                "b3": ("baby_family", 0.80, 0.72),
            }
        )

    def run_gate(self, cases, baseline, candidate, waivers, now=NOW):
        return evaluate(
            cases, baseline, candidate,
            policy_for(("baby_family",), floor=0.50, max_mean_regression=0.10),
            under_test=["image_embedding"], waivers=waivers, now=now,
        )

    def test_valid_waiver_passes_the_gate_and_stays_in_the_report(self):
        cases, baseline, candidate = self.regressing_scenario()
        report = self.run_gate(cases, baseline, candidate,
                               [self.waiver(baseline, candidate)])
        self.assertTrue(report.passed, report.summary())
        self.assertEqual(len(report.violations), 1)
        self.assertTrue(report.violations[0].waived)
        self.assertEqual(report.violations[0].waiver_approver, "rohan")
        self.assertIn("issue #212", report.violations[0].waiver_reason)
        self.assertEqual(report.blocking, ())

    def test_no_waiver_fails(self):
        cases, baseline, candidate = self.regressing_scenario()
        report = self.run_gate(cases, baseline, candidate, [])
        self.assertFalse(report.passed)
        self.assertEqual(kinds(report), ["case_regression"])

    def test_expired_waiver_does_not_apply(self):
        cases, baseline, candidate = self.regressing_scenario()
        waiver = self.waiver(baseline, candidate, expires_at=NOW - timedelta(days=1))
        report = self.run_gate(cases, baseline, candidate, [waiver])
        self.assertFalse(report.passed)
        self.assertEqual(report.expired_waivers, ("case:b3:case_regression",))

    def test_waiver_does_not_cover_a_larger_regression(self):
        """Approving a 0.08 drop must not approve the 0.40 collapse that lands
        on the same case next month."""
        cases, baseline, candidate = self.regressing_scenario()
        candidate = replace(
            candidate,
            results=tuple(
                CaseResult("b3", (0.40,)) if r.case_id == "b3" else r
                for r in candidate.results
            ),
        )
        report = self.run_gate(cases, baseline, candidate,
                               [self.waiver(baseline, candidate)])
        self.assertFalse(report.passed)
        self.assertEqual(kinds(report), ["case_regression"])

    def test_waiver_bound_to_another_swap_does_not_apply(self):
        cases, baseline, candidate = self.regressing_scenario()
        other = ModelSet.of(
            {
                "face_detect": ModelPin("scrfd-10g", "1.0", W_FACE, C_FACE),
                "image_embedding": ModelPin("siglip2-xl", "4.0", "9" * 64, C_NEW),
            }
        )
        stale = self.waiver(
            baseline, candidate, comparison=comparison_digest(baseline.models, other)
        )
        report = self.run_gate(cases, baseline, candidate, [stale])
        self.assertFalse(report.passed)
        self.assertEqual(report.unused_waivers, ("case:b3:case_regression",))

    def test_waiver_for_another_case_does_not_apply(self):
        cases, baseline, candidate = self.regressing_scenario()
        report = self.run_gate(cases, baseline, candidate,
                               [self.waiver(baseline, candidate, target="b1")])
        self.assertFalse(report.passed)

    def test_category_waiver_does_not_excuse_a_case_violation(self):
        cases, baseline, candidate = self.regressing_scenario()
        waiver = self.waiver(
            baseline, candidate,
            scope=WaiverScope.CATEGORY, target="baby_family",
            kind=ViolationKind.CATEGORY_REGRESSION,
        )
        report = self.run_gate(cases, baseline, candidate, [waiver])
        self.assertFalse(report.passed)

    def test_structural_violations_are_not_waivable(self):
        for kind in (
            ViolationKind.CASE_NOT_RUN,
            ViolationKind.NONDETERMINISTIC,
            ViolationKind.BASELINE_DRIFT,
            ViolationKind.THIN_CATEGORY,
            ViolationKind.CATEGORY_MISSING,
        ):
            with self.subTest(kind=kind), self.assertRaises(HarnessError):
                Waiver(
                    scope=WaiverScope.CASE, target="b3", kind=kind, max_magnitude=1.0,
                    reason="r", approver="a", expires_at=NOW + timedelta(days=1),
                    comparison="0" * 32,
                )

    def test_wildcard_target_is_refused(self):
        for target in ("*", "all", ""):
            with self.subTest(target=target), self.assertRaises(HarnessError):
                Waiver(
                    scope=WaiverScope.CASE, target=target,
                    kind=ViolationKind.CASE_REGRESSION, max_magnitude=1.0,
                    reason="r", approver="a", expires_at=NOW + timedelta(days=1),
                    comparison="0" * 32,
                )

    def test_naive_expiry_is_refused(self):
        with self.assertRaises(HarnessError):
            Waiver(
                scope=WaiverScope.CASE, target="b3",
                kind=ViolationKind.CASE_REGRESSION, max_magnitude=1.0,
                reason="r", approver="a", expires_at=datetime(2027, 1, 1),
                comparison="0" * 32,
            )

    def test_empty_reason_is_refused(self):
        with self.assertRaises(HarnessError):
            Waiver(
                scope=WaiverScope.CASE, target="b3",
                kind=ViolationKind.CASE_REGRESSION, max_magnitude=1.0,
                reason="   ", approver="a", expires_at=NOW + timedelta(days=1),
                comparison="0" * 32,
            )

    def test_unbound_comparison_is_refused(self):
        with self.assertRaises(HarnessError):
            Waiver(
                scope=WaiverScope.CASE, target="b3",
                kind=ViolationKind.CASE_REGRESSION, max_magnitude=1.0,
                reason="r", approver="a", expires_at=NOW + timedelta(days=1),
                comparison="not-a-digest",
            )

    def test_waivers_without_now_are_refused(self):
        cases, baseline, candidate = self.regressing_scenario()
        with self.assertRaises(HarnessError):
            self.run_gate(cases, baseline, candidate,
                          [self.waiver(baseline, candidate)], now=None)

    def test_waiving_a_case_does_not_rescue_the_category_floor(self):
        """Waiving the individual cases must not accidentally buy the category."""
        cases, baseline, candidate = scenario(
            {
                "b1": ("baby_family", 0.80, 0.55),
                "b2": ("baby_family", 0.80, 0.55),
            }
        )
        waivers = [
            self.waiver(baseline, candidate, target=target, max_magnitude=0.30)
            for target in ("b1", "b2")
        ]
        report = evaluate(
            cases, baseline, candidate,
            policy_for(("baby_family",), floor=0.70, max_mean_regression=0.50),
            under_test=["image_embedding"], waivers=waivers, now=NOW,
        )
        self.assertFalse(report.passed)
        self.assertEqual(kinds(report), ["category_floor"])


class TestDeterminism(unittest.TestCase):
    """CLAUDE.md hard rule 3, applied to the gate's own output."""

    def build(self, seed: int):
        values = {
            "b1": ("baby_family", 0.81, 0.79),
            "b2": ("baby_family", 0.77, 0.83),
            "b3": ("baby_family", 0.9123456, 0.7654321),
            "d1": ("drone", 0.6666666, 0.7777777),
            "d2": ("drone", 0.5, 0.55),
            "t1": ("travel", 0.33333, 0.44444),
            "t2": ("travel", 0.11111, 0.22222),
        }
        cases, baseline, candidate = scenario(values)
        rng = random.Random(seed)
        shuffled_cases = list(cases)
        rng.shuffle(shuffled_cases)
        base_results = list(baseline.results)
        rng.shuffle(base_results)
        cand_results = list(candidate.results)
        rng.shuffle(cand_results)
        return (
            shuffled_cases,
            replace(baseline, results=tuple(base_results)),
            replace(candidate, results=tuple(cand_results)),
        )

    def test_report_is_identical_under_input_reordering(self):
        policy = policy_for(("baby_family", "drone", "travel"), floor=0.10,
                            max_mean_regression=0.30, max_case_regression=0.30)
        reports = []
        for seed in range(8):
            cases, baseline, candidate = self.build(seed)
            reports.append(
                repr(evaluate(cases, baseline, candidate, policy,
                              under_test=["image_embedding"]))
            )
        self.assertEqual(len(set(reports)), 1)

    def test_summary_is_identical_under_input_reordering(self):
        policy = policy_for(("baby_family", "drone", "travel"), floor=0.80,
                            max_mean_regression=0.01, max_case_regression=0.01)
        summaries = []
        for seed in range(8):
            cases, baseline, candidate = self.build(seed)
            summaries.append(
                evaluate(cases, baseline, candidate, policy,
                         under_test=["image_embedding"]).summary()
            )
        self.assertEqual(len(set(summaries)), 1)
        self.assertIn("FAIL", summaries[0])

    def test_means_are_quantised(self):
        cases, baseline, candidate = scenario(
            {
                "t1": ("travel", 0.1, 0.1),
                "t2": ("travel", 0.2, 0.2),
                "t3": ("travel", 0.30000000000000004, 0.3),
            }
        )
        report = evaluate(
            cases, baseline, candidate,
            policy_for(("travel",), tolerance := None) if False else
            policy_for(("travel",)),
            under_test=["image_embedding"],
        )
        for value in (report.categories[0].baseline_mean,
                      report.categories[0].candidate_mean):
            self.assertEqual(value, round(value, 6))

    def test_waiver_choice_is_independent_of_file_order(self):
        cases, baseline, candidate = scenario(
            {"b1": ("baby_family", 0.80, 0.72)}
        )
        comparison = comparison_digest(baseline.models, candidate.models)
        common = {
            "scope": WaiverScope.CASE,
            "target": "b1",
            "kind": ViolationKind.CASE_REGRESSION,
            "expires_at": NOW + timedelta(days=5),
            "comparison": comparison,
        }
        first = Waiver(max_magnitude=0.10, reason="aaa", approver="alice", **common)
        second = Waiver(max_magnitude=0.10, reason="bbb", approver="bob", **common)
        forward = evaluate(
            cases, baseline, candidate,
            policy_for(("baby_family",), max_mean_regression=0.20),
            under_test=["image_embedding"], waivers=[first, second], now=NOW,
        )
        backward = evaluate(
            cases, baseline, candidate,
            policy_for(("baby_family",), max_mean_regression=0.20),
            under_test=["image_embedding"], waivers=[second, first], now=NOW,
        )
        self.assertEqual(forward.violations[0].waiver_approver,
                         backward.violations[0].waiver_approver)


if __name__ == "__main__":
    unittest.main()
