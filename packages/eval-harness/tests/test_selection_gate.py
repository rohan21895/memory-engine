"""The selection critical-error gate, and the checks that keep it honest.

Three layers, mirroring tests/../..//face-identity/tests/test_eval.py:

1. EVERY METRIC BITES. A rate that stays 0.0 against both the correct and a
   broken selector certifies the bug, so for each critical-error metric there
   is a test that hands the measurement a policy with the guarding rule
   disabled and requires the rate to LEAVE zero. The specific rule each one
   strips is named in its docstring.

2. THE COMMITTED GATE FILE MATCHES A FRESH MEASUREMENT. The committed file's
   baseline and candidate are equal by construction, so every harness delta
   is zero and the file cannot notice the code moving underneath it. This
   test re-runs the builder and compares; without it the gate is a souvenir.

3. THE GATE RUNS AND PASSES -- and a doctored nonzero critical rate FAILS.
   The gate's entire promise is "any departure from 0 is exit 1"; that is
   asserted directly rather than trusted to the policy wiring.
"""

from __future__ import annotations

import json
import sys
import unittest
from dataclasses import replace
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PACKAGE_ROOT))

from memory_engine_eval.harness import (  # noqa: E402
    EXIT_FAIL,
    EXIT_PASS,
    format_report,
    run_gate_file,
)
from memory_engine_eval.selection_bench import (  # noqa: E402
    CASE_EXPECTATIONS,
    CRITICAL_CASES,
    RETENTION_CASES,
    build_gate_document,
    measure_blink_when_clean_exists,
    measure_category_presets_change_winner,
    measure_clean_take_selected,
    measure_exclude_violated,
    measure_pin_violated,
    measure_rare_moment_falsely_rejected,
    measure_screenshot_selected,
    measure_twin_pair_rate,
    measure_worse_version_selected,
)

# selection_bench put album-engine on sys.path already.
from memory_engine_album.selection import DEFAULT_POLICY  # noqa: E402

GATE_PATH = PACKAGE_ROOT / "gates" / "selection-critical-errors.gate.json"


class MetricBiteTests(unittest.TestCase):
    """Each critical rate leaves zero when its guarding rule is stripped."""

    def test_blink_rate_bites(self) -> None:
        """CATCHES: the mid-blink penalty (and the per-face eyes term) losing
        their effect -- with both zeroed, the blink frame's better aesthetics
        win the shot and the rate must go to 1."""
        self.assertEqual(measure_blink_when_clean_exists(), 0.0)
        broken = replace(DEFAULT_POLICY, mid_blink_penalty=0.0, per_face_weight=0.0)
        self.assertGreater(measure_blink_when_clean_exists(broken), 0.0)

    def test_twin_rate_bites(self) -> None:
        """CATCHES: the max_selected_similarity backstop and the redundancy
        penalty both going soft -- the 0.95-cosine twins are the two best
        photos in the pool and sail in together without them."""
        self.assertEqual(measure_twin_pair_rate(), 0.0)
        broken = replace(
            DEFAULT_POLICY, max_selected_similarity=1.0, weight_redundancy=0.0
        )
        self.assertGreater(measure_twin_pair_rate(broken), 0.0)

    def test_screenshot_rate_bites(self) -> None:
        """CATCHES: the screenshot/document gate being disarmed -- the planted
        receipts carry the pool's best fused scores and get selected the
        moment the contrast threshold stops firing."""
        self.assertEqual(measure_screenshot_selected(), 0.0)
        broken = replace(DEFAULT_POLICY, screenshot_min_contrast=1.0)
        self.assertGreater(measure_screenshot_selected(broken), 0.0)

    def test_rare_moment_rate_bites(self) -> None:
        """CATCHES: the rare-moment waiver disappearing -- with the isolation
        requirement pushed beyond the pool's day-scale gaps, the lone
        below-floor photo is rejected and the rate goes to 1."""
        self.assertEqual(measure_rare_moment_falsely_rejected(), 0.0)
        broken = replace(DEFAULT_POLICY, rare_moment_isolation_s=1_000_000.0)
        self.assertEqual(measure_rare_moment_falsely_rejected(broken), 1.0)

    def test_worse_version_rate_bites(self) -> None:
        """CATCHES: shot grouping (and with it Pareto domination) being lost
        -- ungrouped, the dominated takes out-gain the fillers on quality and
        enter the album beside their own better versions."""
        self.assertEqual(measure_worse_version_selected(), 0.0)
        broken = replace(
            DEFAULT_POLICY,
            burst_window_s=0.0,
            max_selected_similarity=1.0,
            weight_redundancy=0.0,
        )
        self.assertGreater(measure_worse_version_selected(broken), 0.0)

    def test_pin_and_exclude_rates_bite(self) -> None:
        """CATCHES: pins/excludes being dropped from the policy -- the pinned
        photo fails the quality floor on its own merits (so only the pin can
        save it) and the excluded photo is the pool's best (so only the
        exclusion keeps it out)."""
        self.assertEqual(measure_pin_violated(), 0.0)
        self.assertEqual(measure_exclude_violated(), 0.0)
        self.assertEqual(measure_pin_violated(DEFAULT_POLICY), 1.0)
        self.assertEqual(measure_exclude_violated(DEFAULT_POLICY), 1.0)

    def test_retention_cases_hold(self) -> None:
        """The positive direction: the dominators win their shots, and the
        category presets deterministically flip the pool's winner."""
        self.assertEqual(measure_clean_take_selected(), 1.0)
        self.assertEqual(measure_category_presets_change_winner(), 1.0)


class GateFileTests(unittest.TestCase):
    def _committed(self) -> dict:
        return json.loads(GATE_PATH.read_text(encoding="utf-8"))

    def test_the_committed_gate_file_matches_a_fresh_measurement(self) -> None:
        """CATCHES: selection.py moving without the gate file being
        regenerated -- the committed file's own deltas are zero by
        construction, so this comparison is the only thing that notices."""
        committed = self._committed()
        fresh = build_gate_document(as_of=committed["as_of"])
        self.assertEqual(
            fresh,
            committed,
            "the committed gate file no longer matches a fresh measurement; "
            "regenerate it with `python3 -m memory_engine_eval.selection_bench "
            "--as-of <date> --write gates/selection-critical-errors.gate.json` "
            "and review the diff -- a changed rate is a changed behaviour",
        )

    def test_the_gate_file_runs_and_passes(self) -> None:
        outcome = run_gate_file(GATE_PATH)
        detail = format_report(outcome.report) if outcome.report else outcome.refusal
        self.assertEqual(outcome.exit_code, EXIT_PASS, f"gate did not pass:\n{detail}")

    def test_a_nonzero_critical_rate_fails_the_gate(self) -> None:
        """CATCHES: the expected/enforce_expected wiring going soft. The gate's
        one promise is that ANY critical-error rate above zero is exit 1; a
        doctored candidate sample proves the promise is live rather than
        assumed. Every critical case is doctored in turn, so no case can
        quietly stop gating."""
        import tempfile  # noqa: PLC0415

        committed = self._committed()
        for case_id in sorted(CRITICAL_CASES):
            document = json.loads(json.dumps(committed))  # deep copy
            doctored = [
                entry
                for entry in document["candidate"]
                if entry["case_id"] == case_id
            ]
            self.assertEqual(len(doctored), 1, case_id)
            doctored[0]["samples"] = [0.25, 0.25, 0.25]
            with tempfile.NamedTemporaryFile(
                "w", suffix=".gate.json", delete=False
            ) as handle:
                json.dump(document, handle)
                path = Path(handle.name)
            try:
                outcome = run_gate_file(path)
                self.assertEqual(outcome.exit_code, EXIT_FAIL, case_id)
                assert outcome.report is not None
                codes = {failure.code for failure in outcome.report.failures}
                self.assertIn("case_below_expected", codes, case_id)
            finally:
                path.unlink()

    def test_every_critical_case_is_expected_zero_and_lower_is_better(self) -> None:
        """CATCHES: a critical case drifting to a tolerant expectation. The
        suite's whole claim is 'expected exactly 0'; anything else in the
        committed file is a different gate wearing this one's name."""
        committed = self._committed()
        self.assertTrue(committed["policy"]["enforce_expected"])
        by_id = {case["case_id"]: case for case in committed["suite"]["cases"]}
        self.assertEqual(sorted(by_id), sorted(CASE_EXPECTATIONS))
        for case_id in CRITICAL_CASES:
            case = by_id[case_id]
            self.assertEqual(case["expected"], 0.0, case_id)
            self.assertEqual(case["metric"]["direction"], "lower_is_better", case_id)
            self.assertEqual(case["category"], "selection_critical", case_id)
        for case_id in RETENTION_CASES:
            case = by_id[case_id]
            self.assertEqual(case["expected"], 1.0, case_id)
            self.assertEqual(case["metric"]["direction"], "higher_is_better", case_id)
            self.assertEqual(case["category"], "selection_retention", case_id)

    def test_samples_are_three_real_repeats_that_agree(self) -> None:
        """min_repeats: 3 with nondeterminism_tolerance 0 is the determinism
        claim; the builder must therefore emit three genuinely equal runs."""
        committed = self._committed()
        self.assertEqual(committed["policy"]["min_repeats"], 3)
        for side in ("baseline", "candidate"):
            for entry in committed[side]:
                self.assertEqual(len(entry["samples"]), 3, entry["case_id"])
                self.assertEqual(len(set(entry["samples"])), 1, entry["case_id"])


if __name__ == "__main__":
    unittest.main()
