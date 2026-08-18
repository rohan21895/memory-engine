"""Runner tests: the half that turns a comparator into a gate.

The interesting failures are not "does it print a verdict". They are the ways a
gate that measured nothing, or measured the wrong thing, could still come back
green:

  * a probe that could not run reported as a pass;
  * a case added and never baselined, quietly ignored;
  * a baseline edited by hand and no longer matching the code that produced it;
  * a candidate whose score moved while the code did not;
  * a non-CI suite dropped from CI's output rather than named in it.
"""

from __future__ import annotations

import contextlib
import io
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PACKAGE_ROOT))

from memory_engine_eval import benchmarks as bench  # noqa: E402
from memory_engine_eval import harness as hz  # noqa: E402
from memory_engine_eval import probes as probe_module  # noqa: E402
from memory_engine_eval import runner  # noqa: E402

CI_SUITE = bench.BENCHMARK_DIR / "deterministic-properties.suite.json"


def _run_main(*argv: str) -> tuple[int, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = runner.main(list(argv))
    return code, out.getvalue() + err.getvalue()


class TestTheCommittedCiSuiteActuallyPasses(unittest.TestCase):
    def test_running_the_ci_suite_measures_and_passes(self) -> None:
        code, text = _run_main("run", "--ci", str(CI_SUITE))
        self.assertEqual(code, hz.EXIT_PASS, text)
        self.assertIn("case(s) measured against code on disk", text)
        # The whole point of the digest pins: with no source edit, the run is a
        # no-op, which arms the sharpest determinism check the harness has.
        self.assertIn("no-op run", text)

    def test_ci_mode_names_the_suites_it_excludes(self) -> None:
        code, text = _run_main("run", "--ci", *[str(p) for p in bench.suite_paths()])
        self.assertEqual(code, hz.EXIT_PASS, text)
        for suite in (bench.load_suite(path) for path in bench.suite_paths()):
            if not suite.runs_in_ci:
                self.assertIn("EXCLUDED from CI", text)
                self.assertIn(suite.suite_id, text)

    def test_running_only_non_ci_suites_in_ci_mode_is_not_a_pass(self) -> None:
        # A run that measured nothing must never be green, however it got there.
        non_ci = [
            str(path)
            for path in bench.suite_paths()
            if not bench.load_suite(path).runs_in_ci
        ]
        self.assertTrue(non_ci)
        code, text = _run_main("run", "--ci", *non_ci)
        self.assertEqual(code, hz.EXIT_REFUSED, text)
        self.assertIn("no suite ran", text)


class TestARefusalIsNotAPass(unittest.TestCase):
    def test_a_probe_that_cannot_run_exits_refused(self) -> None:
        library_suite = next(
            path
            for path in bench.suite_paths()
            if bench.load_suite(path).requires_library is not None
        )
        # No --library, so the probe raises. The distinction being defended:
        # this is 2 (nothing was measured), not 0 and not 1.
        code, text = _run_main("run", str(library_suite))
        self.assertEqual(code, hz.EXIT_REFUSED, text)
        self.assertIn("nothing was measured", text)
        self.assertNotIn("verdict: PASS", text)

    def test_a_library_without_its_declaration_is_a_usage_error(self) -> None:
        code, text = _run_main("run", str(CI_SUITE), "--library", "/tmp")
        self.assertEqual(code, hz.EXIT_USAGE, text)

    def test_a_malformed_suite_is_a_usage_error(self) -> None:
        broken = Path(tempfile.mkdtemp()) / "broken.suite.json"
        self.addCleanup(shutil.rmtree, broken.parent, True)
        broken.write_text("{ not json", encoding="utf-8")
        code, text = _run_main("run", str(broken))
        self.assertEqual(code, hz.EXIT_USAGE, text)


class TestEditedSuites(unittest.TestCase):
    """Copies of the real CI suite, mutated the way a person would mutate it."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        self.document = json.loads(CI_SUITE.read_text(encoding="utf-8"))

    def _write(self) -> Path:
        path = self.root / "edited.suite.json"
        path.write_text(json.dumps(self.document), encoding="utf-8")
        return path

    def test_a_case_with_no_baseline_fails_rather_than_being_skipped(self) -> None:
        self.document["cases"][0].pop("baseline")
        code, text = _run_main("run", str(self._write()))
        self.assertEqual(code, hz.EXIT_FAIL, text)
        self.assertIn("case_new_without_baseline", text)

    def test_a_hand_edited_baseline_number_fails(self) -> None:
        # The candidate is measured live, so lowering the recorded baseline
        # cannot make a red gate green -- but raising it fails, loudly, which is
        # what stops "just re-record it" being invisible.
        self.document["cases"][0]["baseline"]["samples"] = [0.5, 0.5, 0.5]
        code, text = _run_main("run", str(self._write()))
        self.assertEqual(code, hz.EXIT_FAIL, text)
        self.assertIn("no_op_drift", text)

    def test_a_baseline_source_digest_that_no_longer_matches_is_a_real_delta(
        self,
    ) -> None:
        # Pretend the baseline came from different code. The run stops being a
        # no-op, so the movement is compared as a delta instead of as drift --
        # and with equal scores it still passes, which is the correct answer.
        first = next(iter(self.document["baseline_sources"]))
        self.document["baseline_sources"][first] = "f" * 64
        code, text = _run_main("run", str(self._write()))
        self.assertEqual(code, hz.EXIT_PASS, text)
        self.assertNotIn("no-op run", text)

    def test_an_inputs_digest_that_no_longer_matches_refuses_the_comparison(
        self,
    ) -> None:
        # Swap the recorded input identity: same model, different benchmark
        # data. That is not a delta, so it is a refusal (2) and not a fail (1).
        self.document["cases"][0]["baseline"]["inputs_digest"] = "a" * 64
        code, text = _run_main("run", str(self._write()))
        self.assertEqual(code, hz.EXIT_REFUSED, text)
        self.assertIn("InputsDigestMismatch", text)

    def test_a_baseline_source_missing_for_a_used_probe_is_a_usage_error(self) -> None:
        self.document["baseline_sources"].pop(
            next(iter(self.document["baseline_sources"]))
        )
        code, text = _run_main("run", str(self._write()))
        self.assertEqual(code, hz.EXIT_USAGE, text)


class TestTheRunModelSet(unittest.TestCase):
    def setUp(self) -> None:
        self.suite = bench.load_suite(CI_SUITE)

    def test_one_pin_per_probe_and_all_of_them_pinned(self) -> None:
        models = runner.model_set_for(self.suite)
        self.assertEqual(
            len(models.pins), len({case.probe_id for case in self.suite.cases})
        )
        # `require_pinned` is what refuses a run that cannot be reproduced; the
        # code digests are what let a benchmark with no weights satisfy it.
        models.require_pinned("candidate")

    def test_the_params_reach_the_config_digest(self) -> None:
        # Two suites over the same code at different operating points must not
        # compare as the same model. Without this, changing `permutations` from
        # 8 to 2 would read as a quality change.
        document = json.loads(CI_SUITE.read_text(encoding="utf-8"))
        for case in document["cases"]:
            if case["case_id"] == "dedupe_ids_stable_under_permutation":
                case["params"]["permutations"] = 3
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        path = root / "edited.suite.json"
        path.write_text(json.dumps(document), encoding="utf-8")
        other = runner.model_set_for(bench.load_suite(path))
        self.assertNotEqual(
            runner.model_set_for(self.suite).identity, other.identity
        )

    def test_editing_a_probe_source_moves_the_pin(self) -> None:
        probe = probe_module.PROBES["dedupe_burst_recovery"]
        before = probe.sources_digest()
        source = probe.sources[0]
        original = source.read_bytes()
        self.addCleanup(source.write_bytes, original)
        source.write_bytes(original + b"\n# a change\n")
        self.assertNotEqual(before, probe.sources_digest())


class TestTheGateHasTeeth(unittest.TestCase):
    """The thing to avoid is a benchmark that always passes.

    `test_falsification.py` proves each PROBE is sensitive to a broken input.
    This proves the whole path is: change the behaviour of a measured module and
    the committed CI suite comes back FAIL, with the failures naming what moved.
    """

    def test_changing_dedupe_behaviour_fails_the_committed_ci_suite(self) -> None:
        from memory_engine_ranking import dedupe

        original = dedupe.DEFAULT_HAMMING_DECISIVE_THRESHOLD
        self.addCleanup(
            setattr, dedupe, "DEFAULT_HAMMING_DECISIVE_THRESHOLD", original
        )
        # A change somebody might genuinely make -- "be stricter about merges" --
        # which loses the burst whose frames differ by two bits.
        dedupe.DEFAULT_HAMMING_DECISIVE_THRESHOLD = 0

        code, text = _run_main("run", str(CI_SUITE))
        self.assertEqual(code, hz.EXIT_FAIL, text)
        # Three separate gates catch it, and each says a different true thing.
        self.assertIn("case_drop_exceeds_cap", text)
        self.assertIn("case_below_expected", text)
        self.assertIn("category_below_floor", text)
        # And because the source bytes did not move, the harness also reports
        # the sharpest version: identical digests, different answer.
        self.assertIn("no_op_drift", text)

    def test_changing_the_validator_behaviour_fails_the_committed_ci_suite(
        self,
    ) -> None:
        from memory_engine_album import validator

        original = validator.MM_PER_INCH
        self.addCleanup(setattr, validator, "MM_PER_INCH", original)
        # Every effective DPI in the report is computed through this constant.
        validator.MM_PER_INCH = 1.0

        code, text = _run_main("run", str(CI_SUITE))
        self.assertEqual(code, hz.EXIT_FAIL, text)
        self.assertIn("print_", text)


class TestRecording(unittest.TestCase):
    def test_record_writes_samples_sources_and_a_measured_by(self) -> None:
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        path = root / "copy.suite.json"
        document = json.loads(CI_SUITE.read_text(encoding="utf-8"))
        for case in document["cases"]:
            case.pop("baseline", None)
        document.pop("baseline_sources", None)
        path.write_text(json.dumps(document), encoding="utf-8")

        code, text = _run_main("record", "--by", "a test", str(path))
        self.assertEqual(code, hz.EXIT_PASS, text)

        recorded = json.loads(path.read_text(encoding="utf-8"))
        self.assertTrue(recorded["baseline_sources"])
        for case in recorded["cases"]:
            self.assertEqual(len(case["baseline"]["samples"]), case["repeats"])
            self.assertEqual(case["baseline"]["measured_by"], "a test")
        # And the freshly recorded suite passes its own gate.
        code, text = _run_main("run", str(path))
        self.assertEqual(code, hz.EXIT_PASS, text)


if __name__ == "__main__":
    unittest.main()
