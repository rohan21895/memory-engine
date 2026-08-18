"""Suite-loader tests, aimed at the ways a benchmark declaration lies quietly.

The loader's whole job is to refuse. Every test here is a document that would
compile under a more forgiving reader and would then describe a benchmark
different from the one that ran.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PACKAGE_ROOT))

from memory_engine_eval import benchmarks as bench  # noqa: E402
from memory_engine_eval import probes as probe_module  # noqa: E402
from memory_engine_eval.library import ClaimClass  # noqa: E402

_CASE = {
    "case_id": "determinism_case",
    "category": "dedupe",
    "claim_class": "determinism",
    "probe": "dedupe_burst_recovery",
    "expected": 1.0,
    "repeats": 2,
    "measures": "that the declared bursts come back exactly",
    "does_not_measure": "anything about real photographs",
    "falsifications": [
        {"mode": "phash_bit_rot", "max_goodness": 0.0, "why": "rotted bits must break it"},
        {
            "mode": "decisive_threshold_zero",
            "max_goodness": 0.5,
            "why": "identical-hash bursts survive, the others do not",
        },
    ],
}

_SUITE = {
    "suite_id": "unit-suite",
    "description": "a suite built by a test",
    "as_of": "2026-08-18",
    "runs_in_ci": True,
    "policy": {"categories": ["dedupe"], "min_repeats": 1},
    "cases": [_CASE],
}


def _write(document: object) -> Path:
    handle = tempfile.NamedTemporaryFile(
        "w", suffix=".suite.json", delete=False, encoding="utf-8"
    )
    json.dump(document, handle)
    handle.close()
    return Path(handle.name)


def _suite(**overrides) -> dict:
    document = json.loads(json.dumps(_SUITE))
    document.update(overrides)
    return document


def _case(**overrides) -> dict:
    entry = json.loads(json.dumps(_CASE))
    entry.update(overrides)
    return entry


class TestTheLoaderRefuses(unittest.TestCase):
    def assertRefused(self, document: object, fragment: str) -> None:
        with self.assertRaises(bench.BenchmarkDeclarationError) as raised:
            bench.load_suite(_write(document))
        self.assertIn(fragment, str(raised.exception))

    def test_a_well_formed_suite_loads(self) -> None:
        suite = bench.load_suite(_write(_SUITE))
        self.assertEqual(suite.suite_id, "unit-suite")
        self.assertEqual(len(suite.cases), 1)
        self.assertEqual(suite.cases[0].claim_class, ClaimClass.DETERMINISM)

    def test_an_unknown_field_is_refused(self) -> None:
        # A misspelled knob takes its default silently, so the file in review
        # would not be the file that ran.
        self.assertRefused(_suite(max_case_drop=0.05), "unknown field")

    def test_an_unknown_probe_is_refused(self) -> None:
        self.assertRefused(
            _suite(cases=[_case(probe="dedupe_burst_recoveryy")]), "no probe named"
        )

    def test_an_unknown_param_is_refused(self) -> None:
        self.assertRefused(
            _suite(cases=[_case(params={"hamming_thresold": 4})]), "has no parameter"
        )

    def test_a_falsification_the_probe_cannot_apply_is_refused(self) -> None:
        broken = _case()
        broken["falsifications"][0]["mode"] = "phash_bit_rott"
        self.assertRefused(_suite(cases=[broken]), "implements")

    def test_a_subset_of_the_probes_falsifications_is_refused(self) -> None:
        # The point: a break added to a probe and never bounded is the break
        # nobody ever runs.
        broken = _case()
        broken["falsifications"] = broken["falsifications"][:1]
        self.assertRefused(_suite(cases=[broken]), "every break a probe implements")

    def test_a_falsification_allowed_to_score_one_is_refused(self) -> None:
        broken = _case()
        broken["falsifications"][0]["max_goodness"] = 1.0
        self.assertRefused(_suite(cases=[broken]), "demonstrates nothing")

    def test_a_case_claiming_more_than_its_probe_supports_is_refused(self) -> None:
        self.assertRefused(
            _suite(cases=[_case(claim_class="quality")]), "can only support"
        )

    def test_a_case_missing_does_not_measure_is_refused(self) -> None:
        broken = _case()
        del broken["does_not_measure"]
        self.assertRefused(_suite(cases=[broken]), "does_not_measure")

    def test_a_blank_does_not_measure_is_refused(self) -> None:
        self.assertRefused(
            _suite(cases=[_case(does_not_measure="   ")]), "non-empty sentence"
        )

    def test_a_suite_that_needs_a_library_cannot_claim_to_run_in_ci(self) -> None:
        self.assertRefused(
            _suite(requires_library="synthetic-demo"), "cannot claim to run in CI"
        )

    def test_a_suite_excluded_from_ci_must_say_why(self) -> None:
        self.assertRefused(_suite(runs_in_ci=False), "must say what it needs")

    def test_a_case_needing_an_input_cannot_sit_in_a_ci_suite(self) -> None:
        self.assertRefused(
            _suite(
                cases=[
                    _case(
                        probe="library_media_id_agreement",
                        falsifications=[
                            {
                                "mode": "manifest_digest_edited",
                                "max_goodness": 0.999,
                                "why": "an edited digest must be caught",
                            },
                            {
                                "mode": "declared_digests_swapped",
                                "max_goodness": 0.999,
                                "why": "swapped digests must be caught",
                            },
                        ],
                    )
                ]
            ),
            "which CI does not have",
        )

    def test_a_baseline_with_the_wrong_number_of_samples_is_refused(self) -> None:
        # repeats and len(samples) are the same statement made twice; drifting
        # apart means the two sides carry different amounts of evidence.
        self.assertRefused(
            _suite(
                cases=[
                    _case(
                        baseline={
                            "samples": [1.0],
                            "inputs_digest": "a" * 64,
                            "measured_at": "2026-08-18T00:00:00+00:00",
                            "measured_by": "a test",
                        }
                    )
                ]
            ),
            "re-record the baseline",
        )

    def test_a_baseline_source_for_an_unused_probe_is_refused(self) -> None:
        self.assertRefused(
            _suite(baseline_sources={"album_hard_gates_fire": "b" * 64}),
            "that no case uses",
        )

    def test_a_duplicate_case_id_is_refused(self) -> None:
        self.assertRefused(_suite(cases=[_case(), _case()]), "duplicate case_id")

    def test_an_unknown_policy_knob_is_refused(self) -> None:
        suite = bench.load_suite(
            _write(_suite(policy={"categories": ["dedupe"], "min_repeats": 1}))
        )
        self.assertEqual(bench.load_policy(suite).min_repeats, 1)
        with self.assertRaises(Exception):
            bench.load_policy(
                bench.load_suite(
                    _write(_suite(policy={"categories": ["dedupe"], "min_repeat": 1}))
                )
            )


class TestTheCommittedSuites(unittest.TestCase):
    """The files actually in the repository, not fixtures built by a test."""

    def setUp(self) -> None:
        self.suites = [bench.load_suite(path) for path in bench.suite_paths()]

    def test_every_committed_suite_loads(self) -> None:
        self.assertGreaterEqual(len(self.suites), 3)

    def test_at_least_one_suite_runs_in_ci(self) -> None:
        # Otherwise the whole directory is documentation.
        self.assertTrue(any(suite.runs_in_ci for suite in self.suites))

    def test_every_case_carries_a_recorded_baseline(self) -> None:
        # A case with no baseline fails the gate by design; committing one is
        # still a mistake, and this says so where it is cheap to see.
        for suite in self.suites:
            for case in suite.cases:
                with self.subTest(case=case.case_id):
                    self.assertIsNotNone(
                        case.baseline,
                        f"{case.case_id} has no recorded baseline; run "
                        "`runner record` and commit the diff",
                    )

    def test_no_committed_case_claims_quality(self) -> None:
        # There are no photographs here. If this ever fails, either a real
        # benchmark library arrived or somebody mislabelled a number.
        for suite in self.suites:
            for case in suite.cases:
                self.assertNotEqual(case.claim_class, ClaimClass.QUALITY, case.case_id)

    def test_every_probe_is_used_by_some_committed_case(self) -> None:
        used = {case.probe_id for suite in self.suites for case in suite.cases}
        self.assertEqual(
            used,
            set(probe_module.PROBES),
            "a probe nothing declares is a probe nothing runs, and its "
            "falsifications are never exercised",
        )

    def test_the_description_carries_the_claim_class(self) -> None:
        for suite in self.suites:
            for case in suite.cases:
                self.assertTrue(
                    case.description().startswith(
                        f"[{case.claim_class.value.upper()}]"
                    ),
                    case.case_id,
                )
                self.assertIn("DOES NOT MEASURE:", case.description())


if __name__ == "__main__":
    unittest.main()
