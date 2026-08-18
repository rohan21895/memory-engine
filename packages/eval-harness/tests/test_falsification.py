"""Every committed case, run against a deliberately broken input.

This is the test that makes the numbers in `benchmarks/` mean anything. A
benchmark nobody has watched fail is not evidence -- it is a number that happens
to be printed, and the repository already knows what that costs: the print
validator passed every PDF it was ever handed because the only assertion on the
artifact was `%PDF` and "bigger than 100kB" (docs/architecture.md).

So for every case in every committed suite, for every break its probe
implements, this asserts two things:

  * the broken score is at or under the bound the CASE declares. The bound is in
    the suite file, in review, next to the sentence explaining it -- not here,
    where it would be a magic number in a test;
  * the broken score is STRICTLY BELOW the passing score. A break that leaves
    the number where it was demonstrates nothing, and a bound generous enough to
    admit the passing score is a bound that would never have been noticed.

WHAT CANNOT RUN HERE, AND WHY IT DOES NOT QUIETLY PASS

Two suites need something CI does not have: the 216-file synthetic library, and
the fetched ONNX checkpoints. Their cases cannot be falsified in CI, and a
`skip` that leaves the suite green is the exact failure mode this repository has
found three times ("a skip must never share an exit code with a pass"). So the
unrun cases are not skipped silently: `test_every_unfalsified_case_is_one_that_
declared_a_requirement` asserts that the set of cases this run could not break is
EXACTLY the set whose probes declare a requirement. Add a case that quietly
stops being falsifiable and that test fails.

Their falsifications were run by hand on the machine that recorded their
baselines, and the numbers are written down in docs/benchmark-libraries.md.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PACKAGE_ROOT))

from memory_engine_eval import benchmarks as bench  # noqa: E402
from memory_engine_eval import probes as probe_module  # noqa: E402


def _runnable(case: bench.CaseDeclaration) -> bool:
    """Whether this case's probe needs nothing beyond the repository."""
    return not probe_module.PROBES[case.probe_id].requires


class TestEveryCaseHasBeenSeenFailing(unittest.TestCase):
    def setUp(self) -> None:
        self.suites = [bench.load_suite(path) for path in bench.suite_paths()]
        self.assertTrue(self.suites, "no committed suite to falsify")
        self.context = probe_module.ProbeContext()

    def test_every_declared_break_drops_the_score_below_its_bound(self) -> None:
        checked = 0
        for suite in self.suites:
            for case in suite.cases:
                if not _runnable(case):
                    continue
                probe = probe_module.PROBES[case.probe_id]
                inputs, _digest = probe.load(self.context, case.params)
                passing = probe.measure(inputs, case.params)
                for falsification in case.falsifications:
                    with self.subTest(case=case.case_id, mode=falsification.mode):
                        broken = probe.measure(
                            inputs, case.params, falsify=falsification.mode
                        )
                        self.assertLessEqual(
                            broken,
                            falsification.max_goodness,
                            f"{case.case_id} under {falsification.mode} scored "
                            f"{broken}, above the {falsification.max_goodness} the "
                            "case declares it may reach",
                        )
                        self.assertLess(
                            broken,
                            passing,
                            f"{case.case_id} under {falsification.mode} scored "
                            f"{broken}, the same as or better than the unbroken "
                            f"{passing}; this break demonstrates nothing",
                        )
                        checked += 1
        self.assertGreaterEqual(
            checked,
            10,
            "far too few falsifications ran; the suite directory or the probe "
            "requirements have changed and this test is no longer covering them",
        )

    def test_every_unfalsified_case_is_one_that_declared_a_requirement(self) -> None:
        unrun = {
            case.case_id
            for suite in self.suites
            for case in suite.cases
            if not _runnable(case)
        }
        needs_input = {
            case.case_id
            for suite in self.suites
            for case in suite.cases
            if probe_module.PROBES[case.probe_id].requires
        }
        # Equality both ways. A case that stops being falsifiable without
        # declaring a requirement would otherwise vanish from this file's
        # coverage without a single test turning red.
        self.assertEqual(unrun, needs_input)
        for suite in self.suites:
            if any(probe_module.PROBES[c.probe_id].requires for c in suite.cases):
                self.assertFalse(
                    suite.runs_in_ci,
                    f"{suite.suite_id} contains a case CI cannot measure yet claims "
                    "to run in CI",
                )

    def test_a_probe_refuses_a_falsification_it_does_not_implement(self) -> None:
        """The break's name is not free text either.

        A typo in a mode would otherwise measure the UNBROKEN case and assert it
        is worse than itself, which fails -- but for the wrong reason, and the
        message would send somebody looking at the probe rather than at the
        spelling.
        """
        probe = probe_module.PROBES["dedupe_burst_recovery"]
        inputs, _ = probe.load(self.context, {})
        with self.assertRaises(probe_module.ProbeError):
            probe.measure(inputs, {}, falsify="no_such_break")


if __name__ == "__main__":
    unittest.main()
