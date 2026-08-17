"""Regression gate tests, aimed at the ways this file could be quietly wrong.

"Does the subtraction work" is not interesting. The interesting cases are the
ones where a plausible implementation returns a believable number and ships a
bad model:

  * a mean that rises while one category collapses;
  * a delta measured against a baseline a different model produced;
  * a case dropped from the candidate run, which just disappears;
  * a case present in the candidate mean but absent from the baseline mean;
  * a lower-is-better metric whose regression reads as an improvement;
  * a waiver that keeps applying after the model it was reviewed against;
  * a no-op run whose scores moved anyway.

Every test below is one of those.
"""

from __future__ import annotations

import contextlib
import dataclasses
import io
import json
import math
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PACKAGE_ROOT))

from memory_engine_eval.harness import (  # noqa: E402
    BENCHMARK_CATEGORIES,
    EXIT_FAIL,
    EXIT_PASS,
    EXIT_REFUSED,
    EXIT_USAGE,
    MAX_WAIVER_DROP,
    MAX_WAIVER_HORIZON_DAYS,
    BaselineDigestMismatch,
    BenchmarkCase,
    BenchmarkSuite,
    CaseResult,
    CaseStatus,
    Direction,
    GateInput,
    GateOutcome,
    GatePolicy,
    InputsDigestMismatch,
    Metric,
    ModelPin,
    ModelSet,
    ModelSetMismatch,
    SuiteError,
    UnknownCase,
    UnpinnedModel,
    Verdict,
    Waiver,
    _quantise,
    evaluate,
    format_outcome,
    format_report,
    load_gate_file,
    load_gate_input,
    main,
    run_gate,
    run_gate_file,
)

AS_OF = date(2026, 8, 17)

ACCEPTANCE = Metric("reel_acceptance", Direction.HIGHER_IS_BETTER)
FALSE_MATCH = Metric("face_false_match_rate", Direction.LOWER_IS_BETTER)


def digest(n: int) -> str:
    """A distinct, well-formed BLAKE3-shaped hex digest per integer."""
    return f"{n:064x}"


def pin(model_id="siglip2", version="1.0", weights=1, config=2) -> ModelPin:
    return ModelPin(
        model_id=model_id,
        version=version,
        weights_blake3=None if weights is None else digest(weights),
        config_blake3=None if config is None else digest(config),
    )


BASELINE = ModelSet([pin()])
CANDIDATE = ModelSet([pin(weights=3)])


def default_scores() -> dict[str, list[tuple[float | None, float | None]]]:
    """Six categories, four flat cases each: a run where nothing moved."""
    return {category: [(0.80, 0.80)] * 4 for category in BENCHMARK_CATEGORIES}


def _samples(value):
    return value if isinstance(value, tuple) else (value,)


def world(
    scores=None,
    *,
    metric=ACCEPTANCE,
    expected=0.0,
    baseline_models=BASELINE,
    candidate_models=CANDIDATE,
    case_pin=None,
):
    """Build a (suite, baseline_results, candidate_results) triple.

    `scores` maps category -> list of (baseline, candidate). Either side may be
    None to model a case one run did not score; either may be a tuple to model
    repeats.
    """
    scores = default_scores() if scores is None else scores
    cases, baseline, candidate = [], [], []
    index = 0
    for category in sorted(scores):
        for position, (before, after) in enumerate(scores[category]):
            index += 1
            case_id = f"{category}_{position}"
            inputs = digest(1000 + index)
            cases.append(
                BenchmarkCase(
                    case_id=case_id,
                    category=category,
                    inputs_digest=inputs,
                    baseline_models=case_pin or baseline_models,
                    metric=metric,
                    expected=expected,
                )
            )
            if before is not None:
                baseline.append(
                    CaseResult(case_id, baseline_models, inputs, _samples(before))
                )
            if after is not None:
                candidate.append(
                    CaseResult(case_id, candidate_models, inputs, _samples(after))
                )
    return BenchmarkSuite(cases), baseline, candidate


def run(scores=None, *, policy=None, waivers=(), as_of=AS_OF, **kwargs):
    suite, base, cand = world(scores, **kwargs)
    return evaluate(suite, base, cand, policy, waivers, as_of=as_of)


def codes(report):
    return sorted(set(report.failure_codes()))


def scoped(report, code):
    return sorted(f.scope for f in report.failures if f.code == code)


def case_of(report, case_id):
    return next(c for c in report.cases if c.case_id == case_id)


def category_of(report, name):
    return next(c for c in report.categories if c.category == name)


class TestFlatRun(unittest.TestCase):
    def test_nothing_moved_passes(self):
        report = run()
        self.assertEqual(report.verdict, Verdict.PASS, format_report(report))
        self.assertEqual(report.failures, ())
        self.assertEqual(report.overall_delta, 0.0)

    def test_report_renders(self):
        text = format_report(run())
        self.assertIn("verdict: PASS", text)
        self.assertIn("baby_family", text)


class TestCategoriesOutrankTheMean(unittest.TestCase):
    """The failure this harness exists to catch.

    A model that gains three points on travel while collapsing on baby/family
    reads as an improvement to any harness that compares aggregates. The mean
    here genuinely rises; the run must still fail.
    """

    def test_mean_rises_but_a_category_collapses(self):
        scores = default_scores()
        scores["travel"] = [(0.60, 0.90)] * 4
        scores["baby_family"] = [(0.80, 0.60)] * 4
        report = run(scores)

        self.assertGreater(report.overall_delta, 0)
        self.assertEqual(report.verdict, Verdict.FAIL)
        self.assertIn("category_mean_drop", codes(report))
        self.assertEqual(scoped(report, "category_mean_drop"), ["baby_family"])
        self.assertIn("case_drop_exceeds_cap", codes(report))

    def test_overall_is_category_balanced_not_case_weighted(self):
        """A 20-case category must not outvote a 2-case one.

        Case-weighting the overall figure would re-create, one level up, the
        exact drowning effect the per-category gates exist to prevent.
        """
        scores = default_scores()
        scores["travel"] = [(0.50, 0.90)] * 20
        scores["baby_family"] = [(0.90, 0.50)] * 2
        report = run(scores)

        self.assertIsNotNone(report.case_weighted_candidate_mean)
        self.assertGreater(
            report.case_weighted_candidate_mean, report.overall_candidate_mean
        )
        # Category-balanced: (0.9 + 0.5 + 0.8*4) / 6. Written as a literal, not
        # as _quantise(4.6/6): routing the expectation through the same helper
        # the code uses would make the assertion agree with any rounding the
        # module happened to adopt.
        self.assertEqual(report.overall_candidate_mean, 0.766667)
        self.assertEqual(report.verdict, Verdict.FAIL)

    def test_many_small_losses_hidden_by_two_big_wins(self):
        """The mean holds. Three quarters of the category got worse anyway."""
        scores = default_scores()
        scores["festivals"] = [
            (0.50, 0.90),
            (0.80, 0.78),
            (0.80, 0.78),
            (0.80, 0.78),
        ]
        report = run(scores)
        festivals = category_of(report, "festivals")
        self.assertGreater(festivals.mean_delta, 0)  # the mean is UP
        self.assertEqual(festivals.regressed_fraction, 0.75)
        self.assertIn("category_regressed_fraction", codes(report))
        self.assertEqual(report.verdict, Verdict.FAIL)

    def test_absolute_category_floor(self):
        scores = default_scores()
        scores["indian_weddings"] = [(0.40, 0.41)] * 4
        policy = GatePolicy(category_floors={"indian_weddings": 0.75})
        report = run(scores, policy=policy)
        self.assertIn("category_below_floor", codes(report))
        self.assertFalse(category_of(report, "indian_weddings").meets_floor)

    def test_the_floor_is_read_from_the_candidate_not_the_baseline(self):
        """A baseline comfortably over the floor does not license the candidate.

        With both sides below the floor the two readings are indistinguishable,
        so this case puts the baseline above it and the candidate under it.
        """
        scores = default_scores()
        scores["indian_weddings"] = [(0.80, 0.70)] * 4
        policy = GatePolicy(category_floors={"indian_weddings": 0.75})
        report = run(scores, policy=policy)
        category = category_of(report, "indian_weddings")
        self.assertEqual(category.baseline_mean, 0.80)
        self.assertEqual(category.candidate_mean, 0.70)
        self.assertFalse(category.meets_floor)
        self.assertIn("category_below_floor", codes(report))

    def test_the_category_mean_cap_is_its_own_number(self):
        """A drop of 0.015 is far inside the per-case cap and still fails.

        Constructed so nothing else fires: three of eight cases fall 0.04 (under
        the 0.05 per-case cap, and 3/8 is under the regressed fraction), while
        the category mean falls 0.015 -- three times the 0.005 category cap and
        well inside the 0.05 per-case one.
        """
        scores = default_scores()
        scores["festivals"] = [(0.80, 0.76)] * 3 + [(0.80, 0.80)] * 5
        report = run(scores)
        self.assertEqual(codes(report), ["category_mean_drop"])
        self.assertEqual(category_of(report, "festivals").mean_delta, -0.015)

    def test_the_overall_gate_only_bites_when_it_is_stricter_than_the_category_gate(self):
        """At default settings the overall gate can never fire alone.

        The overall figure is the mean of the category means, so it cannot drop
        by more than the worst category does -- with both caps at 0.005, any
        overall failure is already a category failure. The gate earns its place
        only under a policy that permits redistribution between categories while
        forbidding a net loss, which is the configuration exercised here: each
        category may move 0.05, the suite as a whole may lose 0.005.
        """
        scores = {
            category: [(0.80, 0.76), (0.80, 0.76), (0.80, 0.80), (0.80, 0.80)]
            for category in BENCHMARK_CATEGORIES
        }
        policy = GatePolicy(max_category_mean_drop=0.05, max_overall_mean_drop=0.005)
        report = run(scores, policy=policy)
        self.assertEqual(codes(report), ["overall_mean_drop"])
        self.assertEqual(report.overall_delta, -0.02)

    def test_unrepresented_category_fails(self):
        scores = default_scores()
        del scores["drone"]
        report = run(scores)
        self.assertIn("category_unrepresented", codes(report))
        self.assertEqual(scoped(report, "category_unrepresented"), ["drone"])

    def test_a_category_of_only_new_cases_is_unrepresented(self):
        """Four cases present, nothing comparable. Not a covered category.

        Counting members instead of comparable members would report this
        category as covered while its mean is None -- the gate would then be
        silent about an entire benchmark library.
        """
        scores = default_scores()
        scores["drone"] = [(None, 0.80)] * 4
        report = run(scores, policy=GatePolicy(allow_new_cases=True))
        drone = category_of(report, "drone")
        self.assertEqual(len(drone.case_ids), 4)
        self.assertEqual(drone.comparable_case_ids, ())
        self.assertIsNone(drone.candidate_mean)
        self.assertIn("category_unrepresented", codes(report))

    def test_regressed_fraction_is_over_the_comparable_cases(self):
        """New cases must not dilute the fraction that gates the category.

        Two of two comparable cases regressed. Counting the two new,
        un-baselined cases in the denominator would report 0.5 and pass.
        """
        scores = default_scores()
        scores["festivals"] = [(0.80, 0.72)] * 2 + [(None, 0.80)] * 2
        report = run(scores, policy=GatePolicy(allow_new_cases=True))
        festivals = category_of(report, "festivals")
        self.assertEqual(len(festivals.case_ids), 4)
        self.assertEqual(len(festivals.comparable_case_ids), 2)
        self.assertEqual(festivals.regressed_fraction, 1.0)
        self.assertIn("category_regressed_fraction", codes(report))

    def test_mistyped_category_is_caught(self):
        scores = default_scores()
        scores["indian_wedding"] = scores.pop("indian_weddings")
        report = run(scores)
        self.assertIn("unknown_category", codes(report))
        # And the category it was meant to defend is now empty, which is the
        # damage a silently-accepted typo would do.
        self.assertIn("category_unrepresented", codes(report))
        self.assertEqual(scoped(report, "category_unrepresented"), ["indian_weddings"])


class TestDigestRefusals(unittest.TestCase):
    """A comparison across a digest mismatch is refused, not reported.

    Every case here must RAISE. Returning a FAIL verdict would be wrong: a FAIL
    is a quality signal that a human can waive or argue with, and the absence of
    a measurement is neither.
    """

    def test_stale_baseline_pin_is_refused(self):
        # The stored baseline was produced by weights=9; the case pins weights=1.
        suite, _, cand = world()
        _, stale_base, _ = world(baseline_models=ModelSet([pin(weights=9)]))
        with self.assertRaises(BaselineDigestMismatch):
            evaluate(suite, stale_base, cand, as_of=AS_OF)

    def test_config_only_change_is_a_different_model(self):
        """Weights identical, config different. Still a different model.

        ModelRef's own comment: input size, normalisation, thresholds, NMS IoU
        and the alignment template all live in the config, and every one of them
        changes downstream decisions while the weights hash stays identical.
        """
        suite, _, cand = world()
        _, stale_base, _ = world(baseline_models=ModelSet([pin(config=99)]))
        with self.assertRaises(BaselineDigestMismatch):
            evaluate(suite, stale_base, cand, as_of=AS_OF)

    def test_a_run_that_mixes_model_sets_is_refused(self):
        """Half the suite scored by weights still resident in the runtime."""
        suite, base, cand = world()
        contaminated = list(cand[:-1]) + [
            CaseResult(
                cand[-1].case_id,
                ModelSet([pin(weights=7)]),
                cand[-1].inputs_digest,
                cand[-1].samples,
            )
        ]
        with self.assertRaises(ModelSetMismatch):
            evaluate(suite, base, contaminated, as_of=AS_OF)

    def test_different_inputs_is_refused(self):
        suite, base, cand = world()
        wrong_inputs = [
            CaseResult(cand[0].case_id, CANDIDATE, digest(777), cand[0].samples)
        ] + list(cand[1:])
        with self.assertRaises(InputsDigestMismatch):
            evaluate(suite, base, wrong_inputs, as_of=AS_OF)

    def test_unpinned_weights_cannot_gate(self):
        unpinned = ModelSet([pin(weights=None)])
        suite, base, cand = world(candidate_models=unpinned)
        with self.assertRaises(UnpinnedModel):
            evaluate(suite, base, cand, as_of=AS_OF)

    def test_unpinned_config_cannot_gate(self):
        unpinned = ModelSet([pin(config=None)])
        suite, base, cand = world(candidate_models=unpinned)
        with self.assertRaises(UnpinnedModel):
            evaluate(suite, base, cand, as_of=AS_OF)

    def test_an_unpinned_baseline_cannot_gate_either(self):
        """The side that is easy to forget.

        A dev-mode baseline is exactly as unreproducible as a dev-mode
        candidate, and it is the one that gets committed and lives for months.
        """
        unpinned = ModelSet([pin(weights=None)])
        suite, base, cand = world(baseline_models=unpinned)
        with self.assertRaises(UnpinnedModel):
            evaluate(suite, base, cand, as_of=AS_OF)

    def test_result_for_an_unknown_case_is_refused(self):
        suite, base, cand = world()
        stray = list(cand) + [CaseResult("travel_99", CANDIDATE, digest(5), 0.9)]
        with self.assertRaises(UnknownCase):
            evaluate(suite, base, stray, as_of=AS_OF)

    def test_a_second_model_in_the_set_is_part_of_identity(self):
        """A swap changes one component of several; the others must be pinned too."""
        base_set = ModelSet([pin(), pin(model_id="scrfd", weights=10, config=11)])
        cand_set = ModelSet([pin(), pin(model_id="scrfd", weights=12, config=11)])
        suite, base, cand = world(
            baseline_models=base_set, candidate_models=cand_set
        )
        report = evaluate(suite, base, cand, as_of=AS_OF)
        self.assertFalse(report.is_no_op)
        self.assertEqual(report.verdict, Verdict.PASS)


class TestModelIdentity(unittest.TestCase):
    def test_version_is_not_part_of_identity(self):
        """A re-tagged release with byte-identical weights is the same model.

        The weights hash exists because version strings lie. The converse also
        holds, and comparing version strings would refuse a legitimate re-tag.
        """
        retagged = ModelSet([pin(version="1.0.1")])
        self.assertEqual(retagged.identity, BASELINE.identity)
        report = run(candidate_models=retagged)
        self.assertTrue(report.is_no_op)
        self.assertEqual(report.verdict, Verdict.PASS)

    def test_model_set_order_does_not_change_identity(self):
        a = ModelSet([pin(), pin(model_id="scrfd", weights=10, config=11)])
        b = ModelSet([pin(model_id="scrfd", weights=10, config=11), pin()])
        self.assertEqual(a.identity, b.identity)

    def test_duplicate_model_in_a_set_is_rejected(self):
        with self.assertRaises(SuiteError):
            ModelSet([pin(), pin(weights=4)])


class TestNoOpRuns(unittest.TestCase):
    """Same digests in, same numbers out. Anything else is untrustworthy."""

    def test_identical_digests_with_drift_fails(self):
        scores = default_scores()
        # 0.002 is inside the classification tolerance AND inside the per-case
        # cap, so every relative gate would let it through.
        scores["drone"] = [(0.80, 0.802)] + [(0.80, 0.80)] * 3
        report = run(scores, candidate_models=BASELINE)
        self.assertTrue(report.is_no_op)
        self.assertEqual(case_of(report, "drone_0").status, CaseStatus.UNCHANGED)
        self.assertIn("no_op_drift", codes(report))
        self.assertEqual(report.verdict, Verdict.FAIL)

    def test_identical_digests_and_identical_scores_pass(self):
        report = run(candidate_models=BASELINE)
        self.assertTrue(report.is_no_op)
        self.assertEqual(report.verdict, Verdict.PASS)


class TestMissingResults(unittest.TestCase):
    def test_dropped_case_fails(self):
        scores = default_scores()
        scores["gopro_adventure"] = [(0.80, None)] + [(0.80, 0.80)] * 3
        report = run(scores)
        self.assertEqual(
            case_of(report, "gopro_adventure_0").status, CaseStatus.MISSING_CANDIDATE
        )
        self.assertIn("case_missing_candidate", codes(report))

    def test_a_dropped_case_cannot_be_waived(self):
        """The one thing no waiver may forgive.

        Quietly dropping the hardest case is the cheapest way to make a suite
        green, so a waiver on it must not help.
        """
        scores = default_scores()
        scores["gopro_adventure"] = [(0.80, None)] + [(0.80, 0.80)] * 3
        waiver = make_waiver("gopro_adventure_0")
        report = run(scores, waivers=[waiver], policy=GatePolicy(allow_new_cases=True))
        self.assertIn("case_missing_candidate", codes(report))
        self.assertEqual(report.verdict, Verdict.FAIL)
        self.assertFalse(case_of(report, "gopro_adventure_0").waived)

    def test_new_case_without_a_baseline_does_not_pass_by_default(self):
        scores = default_scores()
        scores["festivals"] = [(None, 0.95)] + [(0.80, 0.80)] * 3
        report = run(scores)
        self.assertEqual(case_of(report, "festivals_0").status, CaseStatus.NO_BASELINE)
        self.assertIn("case_new_without_baseline", codes(report))

    def test_new_case_admitted_by_explicit_policy(self):
        scores = default_scores()
        scores["festivals"] = [(None, 0.95)] + [(0.80, 0.80)] * 3
        report = run(scores, policy=GatePolicy(allow_new_cases=True))
        self.assertEqual(report.verdict, Verdict.PASS, format_report(report))
        self.assertNotIn("case_new_without_baseline", codes(report))

    def test_one_sided_cases_enter_neither_mean(self):
        """The most convincing wrong number this file could produce.

        A case in the candidate mean that the baseline mean cannot contain
        shifts the delta by an amount unrelated to model quality. Here the
        one-sided case scores 1.0 -- if it leaked into the candidate mean, that
        mean would read 0.85 instead of 0.80.
        """
        scores = default_scores()
        scores["festivals"] = [(None, 1.0)] + [(0.80, 0.80)] * 3
        report = run(scores, policy=GatePolicy(allow_new_cases=True))
        festivals = category_of(report, "festivals")
        self.assertEqual(len(festivals.case_ids), 4)
        self.assertEqual(len(festivals.comparable_case_ids), 3)
        self.assertEqual(festivals.candidate_mean, 0.80)
        self.assertEqual(festivals.baseline_mean, 0.80)
        self.assertEqual(festivals.mean_delta, 0.0)

    def test_baseline_only_case_leaves_the_baseline_mean_alone(self):
        scores = default_scores()
        scores["festivals"] = [(0.0, None)] + [(0.80, 0.80)] * 3
        report = run(scores)
        festivals = category_of(report, "festivals")
        self.assertEqual(festivals.baseline_mean, 0.80)
        self.assertEqual(festivals.candidate_mean, 0.80)

    def test_case_neither_run_scored(self):
        scores = default_scores()
        scores["travel"] = [(None, None)] + [(0.80, 0.80)] * 3
        report = run(scores)
        self.assertEqual(case_of(report, "travel_0").status, CaseStatus.UNRUN)
        self.assertEqual(report.verdict, Verdict.PASS, format_report(report))


class TestDirection(unittest.TestCase):
    """A lower-is-better metric whose regression reads as an improvement is the
    classic silent eval bug. Face false-match rate is exactly such a metric, and
    CLAUDE.md hard rule 5 makes it the one that matters most."""

    def test_rising_false_match_rate_is_a_regression(self):
        scores = {category: [(0.01, 0.01)] * 4 for category in BENCHMARK_CATEGORIES}
        scores["baby_family"] = [(0.01, 0.04)] * 4
        report = run(scores, metric=FALSE_MATCH, expected=1.0)
        comparison = case_of(report, "baby_family_0")
        self.assertEqual(comparison.status, CaseStatus.REGRESSED)
        self.assertEqual(comparison.baseline_goodness, 0.99)
        self.assertEqual(comparison.candidate_goodness, 0.96)
        self.assertEqual(comparison.delta, -0.03)
        self.assertEqual(report.verdict, Verdict.FAIL)

    def test_falling_false_match_rate_is_an_improvement(self):
        scores = {category: [(0.04, 0.04)] * 4 for category in BENCHMARK_CATEGORIES}
        scores["baby_family"] = [(0.04, 0.01)] * 4
        report = run(scores, metric=FALSE_MATCH, expected=1.0)
        self.assertEqual(case_of(report, "baby_family_0").status, CaseStatus.IMPROVED)
        self.assertEqual(report.verdict, Verdict.PASS, format_report(report))

    def test_expected_outcome_respects_direction(self):
        scores = {category: [(0.01, 0.01)] * 4 for category in BENCHMARK_CATEGORIES}
        scores["baby_family"] = [(0.01, 0.03)] * 4
        report = run(scores, metric=FALSE_MATCH, expected=0.02)
        comparison = case_of(report, "baby_family_0")
        self.assertTrue(comparison.baseline_meets_expected)
        self.assertFalse(comparison.candidate_meets_expected)
        self.assertIn("case_below_expected", codes(report))


class TestExpectedOutcome(unittest.TestCase):
    def test_absolute_floor_is_enforced_even_when_nothing_regressed(self):
        scores = {category: [(0.98, 0.98)] * 4 for category in BENCHMARK_CATEGORIES}
        scores["baby_family"] = [(0.98, 0.985)] * 4
        report = run(scores, expected=0.99)
        self.assertIn("case_below_expected", codes(report))
        self.assertEqual(report.verdict, Verdict.FAIL)

    def test_an_already_failing_baseline_is_said_out_loud(self):
        scores = {category: [(0.50, 0.50)] * 4 for category in BENCHMARK_CATEGORIES}
        report = run(scores, expected=0.99)
        detail = next(f.detail for f in report.failures if f.code == "case_below_expected")
        self.assertIn("already failing", detail)

    def test_expected_enforcement_is_a_stated_policy_choice(self):
        scores = {category: [(0.50, 0.50)] * 4 for category in BENCHMARK_CATEGORIES}
        report = run(scores, expected=0.99, policy=GatePolicy(enforce_expected=False))
        self.assertEqual(report.verdict, Verdict.PASS, format_report(report))

    def test_exactly_meeting_the_expectation_passes(self):
        """The bar is "at least", not "better than".

        A `>` here would fail every case that lands precisely on a round
        published target, which is where hand-tuned thresholds tend to land.
        """
        scores = {category: [(0.80, 0.80)] * 4 for category in BENCHMARK_CATEGORIES}
        report = run(scores, expected=0.80)
        self.assertTrue(case_of(report, "travel_0").candidate_meets_expected)
        self.assertNotIn("case_below_expected", codes(report))
        self.assertEqual(report.verdict, Verdict.PASS, format_report(report))

    def test_an_expected_failure_cannot_be_waived(self):
        scores = {category: [(0.98, 0.98)] * 4 for category in BENCHMARK_CATEGORIES}
        report = run(scores, expected=0.99, waivers=[make_waiver("travel_0")])
        self.assertIn("case_below_expected", codes(report))
        self.assertIn("travel_0", scoped(report, "case_below_expected"))


class TestNondeterminism(unittest.TestCase):
    def test_disagreeing_repeats_fail(self):
        scores = default_scores()
        scores["drone"] = [(0.80, (0.80, 0.82))] + [(0.80, 0.80)] * 3
        report = run(scores)
        comparison = case_of(report, "drone_0")
        self.assertEqual(comparison.status, CaseStatus.NONDETERMINISTIC)
        self.assertIn("case_nondeterministic", codes(report))
        self.assertEqual(comparison.repeats, 2)

    def test_a_nondeterministic_case_is_never_reported_as_improved(self):
        scores = default_scores()
        scores["drone"] = [(0.60, (0.90, 0.70))] + [(0.80, 0.80)] * 3
        report = run(scores)
        self.assertEqual(case_of(report, "drone_0").status, CaseStatus.NONDETERMINISTIC)

    def test_a_nondeterministic_case_reports_no_drop_figure(self):
        """Its apparent drop is noise; naming a number would dignify it.

        The mean of (0.70, 0.50) is 0.30 below the baseline -- far past the
        per-case cap. Reporting that as "dropped 0.30" invites someone to waive
        a quantity that was never measured.
        """
        scores = default_scores()
        scores["drone"] = [(0.90, (0.70, 0.50))] + [(0.80, 0.80)] * 3
        report = run(scores)
        self.assertIn("drone_0", scoped(report, "case_nondeterministic"))
        self.assertNotIn("drone_0", scoped(report, "case_drop_exceeds_cap"))

    def test_a_nondeterministic_baseline_is_caught_too(self):
        """A committed baseline that cannot repeat itself set the bar by luck.

        Only inspecting the candidate side would let that noise into every
        future delta with nothing in the report saying where it came from.
        """
        scores = default_scores()
        scores["drone"] = [((0.78, 0.82), 0.80)] + [(0.80, 0.80)] * 3
        report = run(scores)
        comparison = case_of(report, "drone_0")
        self.assertEqual(comparison.status, CaseStatus.NONDETERMINISTIC)
        self.assertEqual(comparison.spread, 0.0)
        self.assertAlmostEqual(comparison.baseline_spread, 0.04)
        detail = next(
            f.detail for f in report.failures if f.code == "case_nondeterministic"
        )
        self.assertIn("baseline", detail)

    def test_tolerance_is_an_explicit_opt_in(self):
        scores = default_scores()
        scores["drone"] = [(0.80, (0.80, 0.82))] + [(0.80, 0.80)] * 3
        report = run(scores, policy=GatePolicy(nondeterminism_tolerance=0.05))
        self.assertNotIn("case_nondeterministic", codes(report))
        self.assertEqual(case_of(report, "drone_0").candidate_value, 0.81)

    def test_repeat_order_does_not_change_the_value(self):
        forward = CaseResult("travel_0", CANDIDATE, digest(1), (0.1, 0.2, 0.7))
        reverse = CaseResult("travel_0", CANDIDATE, digest(1), (0.7, 0.2, 0.1))
        self.assertEqual(forward.value, reverse.value)

    def test_min_repeats_is_enforced_on_the_candidate(self):
        report = run(policy=GatePolicy(min_repeats=3))
        self.assertIn("case_insufficient_repeats", codes(report))
        self.assertEqual(len(scoped(report, "case_insufficient_repeats")), 24)


def make_waiver(
    case_id,
    *,
    max_drop=0.10,
    baseline_models=BASELINE,
    candidate_models=CANDIDATE,
    approved_on=date(2026, 8, 1),
    expires_on=date(2026, 9, 1),
):
    return Waiver(
        case_id=case_id,
        baseline_models=baseline_models,
        candidate_models=candidate_models,
        max_drop=max_drop,
        reason="known crop regression on chaptered GoPro spans, tracked in #41",
        approved_by="rohan",
        approved_on=approved_on,
        expires_on=expires_on,
    )


def one_big_drop():
    """One case falls 0.08; the rest of its category rises just enough that the
    category mean does not move. Isolates the per-case cap."""
    scores = default_scores()
    scores["baby_family"] = [(0.80, 0.72)] + [(0.80, 0.827)] * 3
    return scores


class TestWaivers(unittest.TestCase):
    def test_the_drop_fails_without_a_waiver(self):
        report = run(one_big_drop())
        self.assertIn("case_drop_exceeds_cap", codes(report))
        self.assertEqual(scoped(report, "case_drop_exceeds_cap"), ["baby_family_0"])

    def test_a_matching_waiver_forgives_exactly_that_case(self):
        report = run(one_big_drop(), waivers=[make_waiver("baby_family_0")])
        self.assertEqual(report.verdict, Verdict.PASS, format_report(report))
        comparison = case_of(report, "baby_family_0")
        self.assertTrue(comparison.waived)
        self.assertEqual(comparison.status, CaseStatus.REGRESSED)
        self.assertIn("rohan", " ".join(comparison.notes))

    def test_a_waiver_dies_when_the_candidate_changes(self):
        """The anti-blanket property: reviewed against v2, useless against v3."""
        other = ModelSet([pin(weights=42)])
        report = run(
            one_big_drop(),
            candidate_models=other,
            waivers=[make_waiver("baby_family_0")],
        )
        self.assertIn("case_drop_exceeds_cap", codes(report))
        self.assertFalse(case_of(report, "baby_family_0").waived)
        self.assertIn("different model pair", " ".join(case_of(report, "baby_family_0").notes))

    def test_a_waiver_dies_when_the_baseline_changes(self):
        waiver = make_waiver(
            "baby_family_0", baseline_models=ModelSet([pin(weights=44)])
        )
        report = run(one_big_drop(), waivers=[waiver])
        self.assertIn("case_drop_exceeds_cap", codes(report))
        self.assertFalse(case_of(report, "baby_family_0").waived)

    def test_an_expired_waiver_does_not_apply(self):
        waiver = make_waiver("baby_family_0", expires_on=date(2026, 8, 16))
        report = run(one_big_drop(), waivers=[waiver], as_of=date(2026, 8, 17))
        self.assertIn("case_drop_exceeds_cap", codes(report))
        self.assertEqual(report.expired_waivers, ("baby_family_0",))

    def test_a_waiver_applies_on_its_last_day(self):
        waiver = make_waiver("baby_family_0", expires_on=date(2026, 8, 17))
        report = run(one_big_drop(), waivers=[waiver], as_of=date(2026, 8, 17))
        self.assertEqual(report.verdict, Verdict.PASS, format_report(report))

    def test_a_waiver_caps_what_it_forgives(self):
        waiver = make_waiver("baby_family_0", max_drop=0.06)
        report = run(one_big_drop(), waivers=[waiver])
        self.assertIn("case_drop_exceeds_cap", codes(report))
        self.assertFalse(case_of(report, "baby_family_0").waived)

    def test_waivers_do_not_buy_a_category(self):
        """Three waived cases still sink the category, by design.

        This is what stops waivers accumulating into a bypass: the only way out
        of a category failure is a reviewed edit to the declared policy.
        """
        scores = default_scores()
        scores["baby_family"] = [(0.80, 0.72)] * 3 + [(0.80, 0.80)]
        waivers = [make_waiver(f"baby_family_{i}") for i in range(3)]
        report = run(scores, waivers=waivers)
        self.assertEqual(report.verdict, Verdict.FAIL)
        self.assertIn("category_mean_drop", codes(report))
        self.assertIn("category_regressed_fraction", codes(report))
        self.assertNotIn("case_drop_exceeds_cap", codes(report))
        self.assertEqual(len(category_of(report, "baby_family").waived_case_ids), 3)

    def test_a_waived_case_still_counts_as_regressed(self):
        report = run(one_big_drop(), waivers=[make_waiver("baby_family_0")])
        self.assertIn(
            "baby_family_0", category_of(report, "baby_family").regressed_case_ids
        )

    def test_an_unused_waiver_is_reported(self):
        """A stale waiver is the thing that forgives the NEXT regression."""
        report = run(waivers=[make_waiver("travel_0")])
        self.assertEqual(report.unused_waivers, ("travel_0",))
        self.assertEqual(report.verdict, Verdict.PASS)

    def test_two_waivers_for_one_case_are_rejected(self):
        with self.assertRaises(SuiteError):
            run(
                one_big_drop(),
                waivers=[
                    make_waiver("baby_family_0", max_drop=0.02),
                    make_waiver("baby_family_0", max_drop=0.10),
                ],
            )

    def test_a_waiver_cannot_forgive_more_than_the_hard_cap(self):
        with self.assertRaises(SuiteError):
            make_waiver("travel_0", max_drop=MAX_WAIVER_DROP + 0.01)

    def test_a_waiver_cannot_outlive_the_horizon(self):
        approved = date(2026, 8, 1)
        with self.assertRaises(SuiteError):
            make_waiver(
                "travel_0",
                approved_on=approved,
                expires_on=approved + timedelta(days=MAX_WAIVER_HORIZON_DAYS + 1),
            )

    def test_a_waiver_must_expire_after_it_was_approved(self):
        with self.assertRaises(SuiteError):
            make_waiver(
                "travel_0", approved_on=date(2026, 8, 1), expires_on=date(2026, 8, 1)
            )

    def test_a_waiver_needs_an_approver_and_a_reason(self):
        with self.assertRaises(SuiteError):
            Waiver(
                case_id="travel_0",
                baseline_models=BASELINE,
                candidate_models=CANDIDATE,
                max_drop=0.05,
                reason="   ",
                approved_by="rohan",
                approved_on=date(2026, 8, 1),
                expires_on=date(2026, 8, 20),
            )
        with self.assertRaises(SuiteError):
            Waiver(
                case_id="travel_0",
                baseline_models=BASELINE,
                candidate_models=CANDIDATE,
                max_drop=0.05,
                reason="tracked in #41",
                approved_by="",
                approved_on=date(2026, 8, 1),
                expires_on=date(2026, 8, 20),
            )


class TestInputValidation(unittest.TestCase):
    def test_a_raw_scale_value_is_rejected(self):
        """47.0 ms handed over unnormalised would dominate every mean it entered."""
        with self.assertRaises(SuiteError):
            CaseResult("travel_0", CANDIDATE, digest(1), 47.0)

    def test_nan_is_rejected(self):
        # The message is asserted, not just the exception: NaN also trips the
        # [0,1] range check (every comparison against NaN is False), so a suite
        # that only asserted `SuiteError` would still pass with the finiteness
        # check deleted -- and then an infinity would be reported as merely
        # "outside [0,1]" rather than as a broken measurement.
        with self.assertRaisesRegex(SuiteError, "finite"):
            CaseResult("travel_0", CANDIDATE, digest(1), math.nan)

    def test_infinity_is_rejected(self):
        with self.assertRaisesRegex(SuiteError, "finite"):
            CaseResult("travel_0", CANDIDATE, digest(1), math.inf)

    def test_a_bool_is_rejected(self):
        """bool is an int subclass: True would sail through as a perfect score."""
        with self.assertRaises(SuiteError):
            CaseResult("travel_0", CANDIDATE, digest(1), True)

    def test_a_bool_inside_a_repeat_list_is_rejected(self):
        """The path a scalar guard does not cover.

        A runner emitting per-repeat pass/fail booleans reaches the value
        checker, not the scalar shortcut, and `True` there means 1.0.
        """
        with self.assertRaises(SuiteError):
            CaseResult("travel_0", CANDIDATE, digest(1), (0.8, True))

    def test_a_bool_expectation_is_rejected(self):
        with self.assertRaises(SuiteError):
            BenchmarkCase(
                case_id="travel_0",
                category="travel",
                inputs_digest=digest(1),
                baseline_models=BASELINE,
                metric=ACCEPTANCE,
                expected=True,
            )

    def test_an_empty_result_is_rejected(self):
        with self.assertRaises(SuiteError):
            CaseResult("travel_0", CANDIDATE, digest(1), ())

    def test_expected_must_be_a_unit(self):
        with self.assertRaises(SuiteError):
            BenchmarkCase(
                case_id="travel_0",
                category="travel",
                inputs_digest=digest(1),
                baseline_models=BASELINE,
                metric=ACCEPTANCE,
                expected=1.5,
            )

    def test_a_malformed_digest_is_rejected(self):
        with self.assertRaises(SuiteError):
            ModelPin("siglip2", "1.0", "not-a-digest", digest(2))
        with self.assertRaises(SuiteError):
            CaseResult("travel_0", CANDIDATE, "abc", 0.8)

    def test_duplicate_case_ids_are_rejected(self):
        case = BenchmarkCase(
            case_id="travel_0",
            category="travel",
            inputs_digest=digest(1),
            baseline_models=BASELINE,
            metric=ACCEPTANCE,
            expected=0.0,
        )
        with self.assertRaises(SuiteError):
            BenchmarkSuite([case, case])

    def test_a_run_reporting_a_case_twice_is_rejected(self):
        suite, base, cand = world()
        with self.assertRaises(SuiteError):
            evaluate(suite, base, list(cand) + [cand[0]], as_of=AS_OF)

    def test_an_empty_candidate_run_is_rejected(self):
        suite, base, _ = world()
        with self.assertRaises(SuiteError):
            evaluate(suite, base, [], as_of=AS_OF)

    def test_a_floor_for_an_undeclared_category_is_rejected(self):
        with self.assertRaises(SuiteError):
            GatePolicy(category_floors={"wedding": 0.8})

    def test_negative_tolerances_are_rejected(self):
        with self.assertRaises(SuiteError):
            GatePolicy(max_case_drop=-0.01)

    def test_a_regressed_fraction_outside_zero_to_one_is_rejected(self):
        with self.assertRaises(SuiteError):
            GatePolicy(max_regressed_fraction_per_category=0.0)
        with self.assertRaises(SuiteError):
            GatePolicy(max_regressed_fraction_per_category=1.5)


class TestDeterminism(unittest.TestCase):
    """Same plan, identical render. Same run, identical report."""

    def test_the_same_run_produces_the_same_digest(self):
        first = run(one_big_drop())
        second = run(one_big_drop())
        self.assertEqual(first.digest(), second.digest())

    def test_result_order_does_not_change_the_report(self):
        suite, base, cand = world(one_big_drop())
        forward = evaluate(suite, base, cand, as_of=AS_OF)
        backward = evaluate(
            suite, list(reversed(base)), list(reversed(cand)), as_of=AS_OF
        )
        self.assertEqual(forward.digest(), backward.digest())

    def test_suite_order_does_not_change_the_report(self):
        suite, base, cand = world(one_big_drop())
        shuffled = BenchmarkSuite(tuple(reversed(suite.cases)))
        self.assertEqual(
            evaluate(suite, base, cand, as_of=AS_OF).digest(),
            evaluate(shuffled, base, cand, as_of=AS_OF).digest(),
        )

    def test_the_suite_itself_stores_cases_sorted(self):
        """`cases` is public: anything iterating it must get a stable order.

        `evaluate` sorts independently, so this would go unnoticed there -- and
        the next consumer to loop over `suite.cases` would inherit whatever
        order the caller happened to build the list in.
        """
        suite, _, _ = world()
        reversed_suite = BenchmarkSuite(tuple(reversed(suite.cases)))
        self.assertEqual(
            [c.case_id for c in reversed_suite.cases],
            sorted(c.case_id for c in reversed_suite.cases),
        )

    def test_cases_and_categories_are_reported_in_sorted_order(self):
        report = run()
        self.assertEqual(
            [c.case_id for c in report.cases], sorted(c.case_id for c in report.cases)
        )
        self.assertEqual(
            [c.category for c in report.categories],
            sorted(c.category for c in report.categories),
        )

    def test_failures_are_sorted(self):
        scores = default_scores()
        scores["travel"] = [(0.80, 0.50)] * 4
        scores["drone"] = [(0.80, 0.50)] * 4
        report = run(scores)
        keys = [(f.code, f.scope) for f in report.failures]
        self.assertEqual(keys, sorted(keys))

    def test_policy_field_order_does_not_change_the_digest(self):
        a = GatePolicy(categories=BENCHMARK_CATEGORIES)
        b = GatePolicy(categories=tuple(reversed(BENCHMARK_CATEGORIES)))
        suite, base, cand = world()
        self.assertEqual(
            evaluate(suite, base, cand, a, as_of=AS_OF).digest(),
            evaluate(suite, base, cand, b, as_of=AS_OF).digest(),
        )

    def test_reported_arithmetic_closes(self):
        """The printed delta equals the difference of the printed values.

        A report whose own arithmetic does not close is a report nobody can
        check by hand, which in practice means nobody checks it.
        """
        report = run(one_big_drop())
        for comparison in report.cases:
            if comparison.delta is None:
                continue
            self.assertEqual(
                comparison.delta,
                round(comparison.candidate_goodness - comparison.baseline_goodness, 6),
            )
        for category in report.categories:
            if category.mean_delta is None:
                continue
            self.assertEqual(
                category.mean_delta,
                round(category.candidate_mean - category.baseline_mean, 6),
            )

    def test_values_are_quantised_to_six_decimals(self):
        """Six, matching the precision the contract's Score stores.

        Coarser rounding would silently merge scores that the fusion layer can
        still distinguish, and every tie it created would be broken by whatever
        order the cases arrived in.
        """
        self.assertEqual(_quantise(1 / 3), 0.333333)
        self.assertEqual(_quantise(2 / 3), 0.666667)

    def test_the_digest_covers_the_policy(self):
        """Two runs gated under different rules are not the same result.

        A digest that ignored the policy would let a loosened gate be attached
        to a CI run as if it were the reviewed one.
        """
        suite, base, cand = world()
        strict = evaluate(suite, base, cand, GatePolicy(), as_of=AS_OF)
        loose = evaluate(
            suite, base, cand, GatePolicy(max_case_drop=0.2), as_of=AS_OF
        )
        self.assertEqual(strict.verdict, loose.verdict)
        self.assertNotEqual(strict.digest(), loose.digest())

    def test_negative_zero_is_normalised(self):
        """-0.0 and 0.0 compare equal but serialise differently, which would give
        two identical runs two different report digests."""
        self.assertEqual(repr(_quantise(-1e-9)), "0.0")
        self.assertEqual(repr(_quantise(-0.0)), "0.0")


class TestClassification(unittest.TestCase):
    def test_tolerance_band_is_unchanged_not_regressed(self):
        scores = default_scores()
        scores["travel"] = [(0.80, 0.795)] + [(0.80, 0.80)] * 3
        report = run(scores)
        self.assertEqual(case_of(report, "travel_0").status, CaseStatus.UNCHANGED)
        self.assertEqual(report.verdict, Verdict.PASS, format_report(report))

    def test_just_past_the_tolerance_is_a_regression(self):
        scores = default_scores()
        scores["travel"] = [(0.80, 0.789)] + [(0.80, 0.804)] * 3
        report = run(scores)
        self.assertEqual(case_of(report, "travel_0").status, CaseStatus.REGRESSED)

    def test_the_per_case_cap_is_exclusive_at_the_boundary(self):
        scores = default_scores()
        # Exactly 0.05 down: at the cap, not over it.
        scores["travel"] = [(0.80, 0.75)] + [(0.80, 0.817)] * 3
        report = run(scores)
        self.assertEqual(case_of(report, "travel_0").status, CaseStatus.REGRESSED)
        self.assertNotIn("case_drop_exceeds_cap", codes(report))

    def test_improvement_is_recognised(self):
        scores = default_scores()
        scores["travel"] = [(0.60, 0.90)] * 4
        report = run(scores)
        self.assertEqual(case_of(report, "travel_0").status, CaseStatus.IMPROVED)
        self.assertEqual(report.verdict, Verdict.PASS, format_report(report))

# --------------------------------------------------------------------------
# The gate as a command: three answers, three exit codes
# --------------------------------------------------------------------------


def gate_document(scores=None, *, policy=None, waivers=(), as_of=AS_OF, **kwargs):
    """The on-disk gate-file shape, built independently of the loader.

    Deliberately not written with the dataclasses' own `to_dict`: a file format
    validated by the same code that produced it proves nothing about the format
    a human will type.
    """
    suite, base, cand = world(scores, **kwargs)

    def result_json(result):
        return {
            "case_id": result.case_id,
            "models": [p.to_dict() for p in result.models.pins],
            "inputs_digest": result.inputs_digest,
            "samples": list(result.samples),
        }

    document = {
        "as_of": as_of.isoformat(),
        "suite": {
            "cases": [
                {
                    "case_id": case.case_id,
                    "category": case.category,
                    "inputs_digest": case.inputs_digest,
                    "baseline_models": [p.to_dict() for p in case.baseline_models.pins],
                    "metric": {
                        "name": case.metric.name,
                        "direction": case.metric.direction.value,
                    },
                    "expected": case.expected,
                }
                for case in suite.cases
            ]
        },
        "baseline": [result_json(r) for r in base],
        "candidate": [result_json(r) for r in cand],
    }
    if policy is not None:
        document["policy"] = policy
    if waivers:
        document["waivers"] = list(waivers)
    return document


class GateFileCase(unittest.TestCase):
    def setUp(self):
        self.directory = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.directory, True)

    def write(self, document, name="gate.json"):
        path = self.directory / name
        if isinstance(document, str):
            path.write_text(document, encoding="utf-8")
        else:
            path.write_text(json.dumps(document), encoding="utf-8")
        return path

    def call(self, argv):
        """main(argv) with its output captured, returning (code, text)."""
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(argv)
        return code, out.getvalue() + err.getvalue()


class TestVerdictExitCodes(unittest.TestCase):
    """"I could not check" and "I checked and it got worse" are different answers.

    models/policy/digest.py had to learn this at the other end and returns 2 for
    "could not check". The same rule applies here: SKIP must not share a shell
    status with FAIL, or a stale pin gets treated as a quality signal -- argued
    with, waived, overridden.
    """

    def test_the_four_exit_codes_are_distinct(self):
        self.assertEqual(
            len({EXIT_PASS, EXIT_FAIL, EXIT_REFUSED, EXIT_USAGE}),
            4,
        )
        self.assertEqual(EXIT_PASS, 0)

    def test_every_verdict_has_its_own_exit_code(self):
        codes_by_verdict = {verdict: verdict.exit_code for verdict in Verdict}
        self.assertEqual(len(set(codes_by_verdict.values())), len(Verdict))
        self.assertEqual(Verdict.PASS.exit_code, EXIT_PASS)
        self.assertEqual(Verdict.FAIL.exit_code, EXIT_FAIL)
        self.assertEqual(Verdict.SKIP.exit_code, EXIT_REFUSED)

    def test_skip_does_not_share_a_code_with_fail(self):
        self.assertNotEqual(Verdict.SKIP.exit_code, Verdict.FAIL.exit_code)

    def test_only_a_non_zero_code_may_mean_anything_but_pass(self):
        for verdict in Verdict:
            if verdict is Verdict.PASS:
                continue
            self.assertNotEqual(verdict.exit_code, 0, verdict)

    def test_a_report_can_never_carry_skip(self):
        # A report is the record of a comparison that happened; SKIP means none
        # did. Allowing it would put means, deltas and a digest on a run that
        # was refused.
        report = run()
        with self.assertRaises(SuiteError):
            dataclasses.replace(report, verdict=Verdict.SKIP)


class TestRunGate(GateFileCase):
    def gate(self, **kwargs):
        return load_gate_input(gate_document(**kwargs))

    def test_a_clean_run_is_a_pass_outcome(self):
        outcome = run_gate(self.gate())
        self.assertEqual(outcome.verdict, Verdict.PASS)
        self.assertEqual(outcome.exit_code, EXIT_PASS)
        self.assertIsNotNone(outcome.report)
        self.assertIsNone(outcome.refusal)

    def test_a_regression_is_a_fail_outcome(self):
        scores = default_scores()
        scores["baby_family"] = [(0.80, 0.60)] * 4
        outcome = run_gate(self.gate(scores=scores))
        self.assertEqual(outcome.verdict, Verdict.FAIL)
        self.assertEqual(outcome.exit_code, EXIT_FAIL)
        self.assertIn("case_drop_exceeds_cap", outcome.report.failure_codes())

    def test_a_stale_baseline_pin_is_a_skip_not_a_fail(self):
        outcome = run_gate(self.gate(case_pin=ModelSet([pin(weights=9)])))
        self.assertEqual(outcome.verdict, Verdict.SKIP)
        self.assertEqual(outcome.exit_code, EXIT_REFUSED)
        self.assertIsNone(outcome.report)
        self.assertEqual(outcome.refusal_type, "BaselineDigestMismatch")

    def test_an_unpinned_candidate_is_a_skip(self):
        outcome = run_gate(
            self.gate(candidate_models=ModelSet([pin(model_id="scrfd", weights=None)]))
        )
        self.assertEqual(outcome.verdict, Verdict.SKIP)
        self.assertEqual(outcome.refusal_type, "UnpinnedModel")

    def test_a_malformed_suite_is_not_a_skip(self):
        # SuiteError is "this gate was never set up", which is a different
        # person's problem from "these two runs cannot be compared". Swallowing
        # it into SKIP would report a broken harness as an unmeasurable model.
        gate = self.gate()
        broken = GateInput(
            suite=gate.suite,
            baseline=gate.baseline,
            candidate=(),
            policy=gate.policy,
            waivers=gate.waivers,
            as_of=gate.as_of,
        )
        with self.assertRaises(SuiteError):
            run_gate(broken)

    def test_a_skip_outcome_may_not_carry_a_report(self):
        report = run()
        with self.assertRaises(SuiteError):
            GateOutcome(
                source="x",
                verdict=Verdict.SKIP,
                report=report,
                refusal="mismatch",
                refusal_type="ModelSetMismatch",
            )

    def test_a_measured_outcome_must_carry_its_report(self):
        with self.assertRaises(SuiteError):
            GateOutcome(
                source="x",
                verdict=Verdict.FAIL,
                report=None,
                refusal=None,
                refusal_type=None,
            )

    def test_a_skip_outcome_names_the_refusal_in_its_log_block(self):
        text = format_outcome(run_gate(self.gate(case_pin=ModelSet([pin(weights=9)]))))
        self.assertIn("SKIP", text)
        self.assertIn("BaselineDigestMismatch", text)
        self.assertNotIn("verdict: FAIL", text)


class TestGateFileLoading(GateFileCase):
    def test_a_gate_file_produces_the_same_report_as_the_library(self):
        scores = default_scores()
        scores["drone"] = [(0.80, 0.79)] * 4
        document = gate_document(scores=scores)
        from_file = run_gate_file(self.write(document))
        direct = run(scores)
        self.assertEqual(from_file.report.digest(), direct.digest())

    def test_an_absent_policy_means_the_strict_defaults(self):
        outcome = run_gate_file(self.write(gate_document()))
        self.assertEqual(outcome.report.policy, GatePolicy())

    def test_a_declared_policy_is_used(self):
        document = gate_document(policy={"min_repeats": 3})
        outcome = run_gate_file(self.write(document))
        self.assertEqual(outcome.report.policy.min_repeats, 3)
        self.assertEqual(outcome.verdict, Verdict.FAIL)

    def test_an_unknown_top_level_field_is_refused(self):
        document = gate_document()
        document["baselines"] = document["baseline"]
        with self.assertRaises(SuiteError) as caught:
            load_gate_input(document)
        self.assertIn("baselines", str(caught.exception))

    def test_a_misspelled_policy_knob_is_refused(self):
        # The whole failure mode: the file in the repository says one thing and
        # the policy that ran took the default.
        document = gate_document(policy={"max_case_drop_": 0.9})
        with self.assertRaises(SuiteError) as caught:
            load_gate_input(document)
        self.assertIn("max_case_drop_", str(caught.exception))

    def test_a_missing_as_of_is_refused(self):
        document = gate_document()
        del document["as_of"]
        with self.assertRaises(SuiteError) as caught:
            load_gate_input(document)
        self.assertIn("as_of", str(caught.exception))

    def test_as_of_must_be_a_date(self):
        document = gate_document()
        document["as_of"] = "yesterday"
        with self.assertRaises(SuiteError):
            load_gate_input(document)

    def test_an_unknown_metric_direction_is_refused(self):
        document = gate_document()
        document["suite"]["cases"][0]["metric"]["direction"] = "up"
        with self.assertRaises(SuiteError) as caught:
            load_gate_input(document)
        self.assertIn("direction", str(caught.exception))

    def test_a_boolean_sample_is_refused_by_the_loader(self):
        document = gate_document()
        document["candidate"][0]["samples"] = [True]
        with self.assertRaises(SuiteError):
            load_gate_input(document)

    def test_samples_must_be_an_array(self):
        document = gate_document()
        document["candidate"][0]["samples"] = 0.8
        with self.assertRaises(SuiteError):
            load_gate_input(document)

    def test_a_file_that_is_not_json_is_a_suite_error(self):
        path = self.write("{not json", name="broken.json")
        with self.assertRaises(SuiteError):
            load_gate_file(path)

    def test_a_waiver_loads_and_applies(self):
        scores = default_scores()
        scores["baby_family"] = [(0.80, 0.72)] + [(0.80, 0.827)] * 3
        document = gate_document(
            scores=scores,
            waivers=[
                {
                    "case_id": "baby_family_0",
                    "baseline_models": [p.to_dict() for p in BASELINE.pins],
                    "candidate_models": [p.to_dict() for p in CANDIDATE.pins],
                    "max_drop": 0.09,
                    "reason": "known low-light crop change, reviewed",
                    "approved_by": "rohan",
                    "approved_on": "2026-08-01",
                    "expires_on": "2026-09-01",
                }
            ],
        )
        outcome = run_gate_file(self.write(document))
        self.assertNotIn("case_drop_exceeds_cap", outcome.report.failure_codes())
        self.assertTrue(case_of(outcome.report, "baby_family_0").waived)


class TestCommandLine(GateFileCase):
    def test_a_clean_gate_file_exits_zero(self):
        code, _ = self.call([str(self.write(gate_document()))])
        self.assertEqual(code, EXIT_PASS)

    def test_a_regression_exits_one(self):
        scores = default_scores()
        scores["baby_family"] = [(0.80, 0.60)] * 4
        code, text = self.call([str(self.write(gate_document(scores=scores)))])
        self.assertEqual(code, EXIT_FAIL)
        self.assertIn("case_drop_exceeds_cap", text)

    def test_a_refusal_exits_two(self):
        document = gate_document(case_pin=ModelSet([pin(weights=9)]))
        code, text = self.call([str(self.write(document))])
        self.assertEqual(code, EXIT_REFUSED)
        self.assertIn("SKIP", text)

    def test_a_malformed_gate_file_exits_three(self):
        code, text = self.call([str(self.write("{not json", name="bad.json"))])
        self.assertEqual(code, EXIT_USAGE)
        self.assertIn("UNUSABLE", text)

    def test_a_missing_gate_file_exits_three(self):
        code, _ = self.call([str(self.directory / "absent.json")])
        self.assertEqual(code, EXIT_USAGE)

    def test_argparse_usage_errors_do_not_borrow_the_refusal_code(self):
        # argparse exits 2 by default, which is EXIT_REFUSED here. A missing
        # argument would then read to CI as "the comparison was refused".
        err = io.StringIO()
        with contextlib.redirect_stderr(err), self.assertRaises(SystemExit) as caught:
            main([])
        self.assertEqual(caught.exception.code, EXIT_USAGE)

    def test_a_real_failure_outranks_a_refusal(self):
        scores = default_scores()
        scores["baby_family"] = [(0.80, 0.60)] * 4
        failing = self.write(gate_document(scores=scores), name="fail.json")
        refused = self.write(
            gate_document(case_pin=ModelSet([pin(weights=9)])), name="skip.json"
        )
        code, _ = self.call([str(refused), str(failing)])
        self.assertEqual(code, EXIT_FAIL)

    def test_a_refusal_outranks_a_pass(self):
        clean = self.write(gate_document(), name="pass.json")
        refused = self.write(
            gate_document(case_pin=ModelSet([pin(weights=9)])), name="skip.json"
        )
        code, _ = self.call([str(clean), str(refused)])
        self.assertEqual(code, EXIT_REFUSED)

    def test_an_unusable_file_outranks_everything(self):
        scores = default_scores()
        scores["baby_family"] = [(0.80, 0.60)] * 4
        failing = self.write(gate_document(scores=scores), name="fail.json")
        broken = self.write("{", name="bad.json")
        code, _ = self.call([str(failing), str(broken)])
        self.assertEqual(code, EXIT_USAGE)

    def test_json_output_records_the_verdict_and_the_code(self):
        refused = self.write(gate_document(case_pin=ModelSet([pin(weights=9)])))
        out = self.directory / "outcomes.json"
        code, _ = self.call([str(refused), "--json", str(out)])
        payload = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(code, EXIT_REFUSED)
        self.assertEqual(payload[0]["verdict"], "skip")
        self.assertEqual(payload[0]["exit_code"], EXIT_REFUSED)
        self.assertIsNone(payload[0]["report"])

    def test_the_module_runs_as_a_command_and_returns_the_refusal_code(self):
        # Proof the entry point exists at all: CI invokes the module, not main().
        document = gate_document(case_pin=ModelSet([pin(weights=9)]))
        path = self.write(document)
        done = subprocess.run(
            [sys.executable, "-m", "memory_engine_eval.harness", str(path)],
            capture_output=True,
            text=True,
            cwd=str(PACKAGE_ROOT),
        )
        self.assertEqual(done.returncode, EXIT_REFUSED, done.stderr)
        self.assertIn("SKIP", done.stdout + done.stderr)

    def test_the_module_runs_as_a_command_and_returns_zero_on_a_clean_gate(self):
        path = self.write(gate_document())
        done = subprocess.run(
            [sys.executable, "-m", "memory_engine_eval.harness", str(path)],
            capture_output=True,
            text=True,
            cwd=str(PACKAGE_ROOT),
        )
        self.assertEqual(done.returncode, EXIT_PASS, done.stderr)


class TestToleranceCannotDisableTheCap(unittest.TestCase):
    """`case_regression_tolerance` used to gate `max_case_drop`.

    The per-case cap was only checked on a case already classified REGRESSED,
    so a policy whose tolerance exceeded the cap made the cap unreachable: a
    baby/family case falling 0.09 against a documented 0.05 cap returned PASS
    with an empty failure list. Two independent defences now.
    """

    def test_a_tolerance_above_the_cap_is_refused(self):
        with self.assertRaises(SuiteError) as caught:
            GatePolicy(case_regression_tolerance=0.10, max_case_drop=0.05)
        message = str(caught.exception)
        self.assertIn("case_regression_tolerance", message)
        self.assertIn("max_case_drop", message)

    def test_a_tolerance_equal_to_the_cap_is_allowed(self):
        # At equality the cap is still reachable: REGRESSED needs drop > tol,
        # and the cap needs drop > cap, so the two fire together.
        policy = GatePolicy(case_regression_tolerance=0.05, max_case_drop=0.05)
        self.assertEqual(policy.case_regression_tolerance, 0.05)

    def test_the_defaults_keep_the_cap_reachable(self):
        policy = GatePolicy()
        self.assertLess(policy.case_regression_tolerance, policy.max_case_drop)

    def test_the_cliff_edge_case_still_fails_at_the_loosest_legal_tolerance(self):
        scores = default_scores()
        scores["baby_family"] = [(0.80, 0.71)] + [(0.80, 0.80)] * 3
        report = run(
            scores,
            policy=GatePolicy(case_regression_tolerance=0.05, max_case_drop=0.05),
        )
        self.assertEqual(report.verdict, Verdict.FAIL)
        self.assertIn("case_drop_exceeds_cap", codes(report))
        self.assertIn("baby_family_0", scoped(report, "case_drop_exceeds_cap"))

    def test_the_cap_does_not_depend_on_the_classification(self):
        # Belt and braces, tested rather than asserted in a comment: even with
        # the constructor guard bypassed -- which is what a future edit to
        # __post_init__ amounts to -- a drop past the cap still fails, and the
        # case is not quietly labelled UNCHANGED and forgotten.
        policy = GatePolicy(case_regression_tolerance=0.01, max_case_drop=0.05)
        object.__setattr__(policy, "case_regression_tolerance", 0.10)
        scores = default_scores()
        scores["baby_family"] = [(0.80, 0.71)] + [(0.80, 0.80)] * 3
        report = run(scores, policy=policy)
        self.assertEqual(case_of(report, "baby_family_0").status, CaseStatus.UNCHANGED)
        self.assertIn("case_drop_exceeds_cap", codes(report))
        self.assertEqual(report.verdict, Verdict.FAIL)

    def test_a_nondeterministic_case_is_not_also_capped(self):
        # Its delta is not a measurement (decision 6), so failing it twice
        # would blame model quality for an unrepeatable number.
        scores = default_scores()
        scores["drone"] = [(0.80, (0.10, 0.90))] + [(0.80, 0.80)] * 3
        report = run(scores)
        self.assertEqual(case_of(report, "drone_0").status, CaseStatus.NONDETERMINISTIC)
        self.assertIn("case_nondeterministic", codes(report))
        self.assertNotIn("drone_0", scoped(report, "case_drop_exceeds_cap"))


class TestTrailingNewlineAnchoring(unittest.TestCase):
    """`$` in a `.match()` pattern also matches before a FINAL newline.

    A digest read with `open(path).read()` instead of `.read().strip()` was
    accepted as a 65-character "BLAKE3" digest, and then compared unequal to
    the same digest without the newline -- BaselineDigestMismatch, a refusal,
    chased as a stale-baseline ghost. A case_id with a trailing newline forged
    a second line in format_report().
    """

    def test_a_digest_with_a_trailing_newline_is_not_a_digest(self):
        with self.assertRaises(SuiteError):
            ModelPin("siglip2", "1.0", digest(1) + "\n", digest(2))
        with self.assertRaises(SuiteError):
            ModelPin("siglip2", "1.0", digest(1), digest(2) + "\n")

    def test_a_case_id_with_a_trailing_newline_is_not_a_slug(self):
        with self.assertRaises(SuiteError):
            BenchmarkCase(
                case_id="travel_0\n",
                category="travel",
                inputs_digest=digest(7),
                baseline_models=BASELINE,
                metric=ACCEPTANCE,
                expected=0.0,
            )

    def test_a_category_with_a_trailing_newline_is_not_a_slug(self):
        with self.assertRaises(SuiteError):
            BenchmarkCase(
                case_id="travel_0",
                category="travel\n",
                inputs_digest=digest(7),
                baseline_models=BASELINE,
                metric=ACCEPTANCE,
                expected=0.0,
            )

    def test_an_inputs_digest_with_a_trailing_newline_is_refused(self):
        with self.assertRaises(SuiteError):
            BenchmarkCase(
                case_id="travel_0",
                category="travel",
                inputs_digest=digest(7) + "\n",
                baseline_models=BASELINE,
                metric=ACCEPTANCE,
                expected=0.0,
            )
        with self.assertRaises(SuiteError):
            CaseResult("travel_0", BASELINE, digest(7) + "\n", (0.8,))

    def test_a_model_id_or_metric_name_with_a_trailing_newline_is_refused(self):
        with self.assertRaises(SuiteError):
            ModelPin("siglip2\n", "1.0", digest(1), digest(2))
        with self.assertRaises(SuiteError):
            Metric("reel_acceptance\n", Direction.HIGHER_IS_BETTER)

    def test_a_waiver_case_id_with_a_trailing_newline_is_refused(self):
        with self.assertRaises(SuiteError):
            Waiver(
                case_id="travel_0\n",
                baseline_models=BASELINE,
                candidate_models=CANDIDATE,
                max_drop=0.01,
                reason="r",
                approved_by="rohan",
                approved_on=date(2026, 8, 1),
                expires_on=date(2026, 9, 1),
            )

    def test_two_pins_naming_the_same_weights_cannot_differ_by_whitespace(self):
        # The ghost this prevents: one runner strips the digest file, another
        # does not, and the gate refuses the comparison as a model change.
        clean = ModelPin("siglip2", "1.0", digest(1), digest(2))
        with self.assertRaises(SuiteError):
            ModelPin("siglip2", "1.0", digest(1) + "\n", digest(2))
        self.assertEqual(clean.weights_blake3, digest(1))

class TestCommittedGateFiles(unittest.TestCase):
    """CI runs `gates/*.gate.json`; this makes a broken one a unit-test failure.

    Without the first assertion the CI step would glob nothing, succeed, and
    report a green gate over zero measurements -- which is the same shape of
    lie as a gate that is never invoked.
    """

    def test_every_committed_gate_file_passes(self):
        gates = sorted((PACKAGE_ROOT / "gates").glob("*.gate.json"))
        self.assertTrue(gates, "no committed gate file; the CI gate would run on nothing")
        for path in gates:
            with self.subTest(gate=path.name):
                outcome = run_gate_file(path)
                self.assertEqual(outcome.exit_code, EXIT_PASS, format_outcome(outcome))


if __name__ == "__main__":
    unittest.main()
