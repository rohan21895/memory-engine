"""Run a unittest directory and make every unexpected skip a failure."""

from __future__ import annotations

import argparse
import sys
import unittest
from pathlib import Path


def _tests(suite: unittest.TestSuite):
    for candidate in suite:
        if isinstance(candidate, unittest.TestSuite):
            yield from _tests(candidate)
        else:
            yield candidate


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("tests_dir", type=Path)
    parser.add_argument("--exclude", action="append", default=[])
    args = parser.parse_args()

    suite = unittest.defaultTestLoader.discover(str(args.tests_dir.resolve()))
    discovered = list(_tests(suite))
    ids = [test.id() for test in discovered]
    missing_exclusions = sorted(set(args.exclude) - set(ids))
    if missing_exclusions:
        print(
            "required suite lost explicitly optional test(s):\n  - "
            + "\n  - ".join(missing_exclusions),
            file=sys.stderr,
        )
        return 2

    excluded = set(args.exclude)
    for test_id in args.exclude:
        print(f"optional suite NOT RUN: {test_id}", file=sys.stderr)
    selected = [test for test in discovered if test.id() not in excluded]

    result = unittest.TextTestRunner(verbosity=1).run(unittest.TestSuite(selected))
    if result.skipped:
        print(
            f"required suite skipped {len(result.skipped)} test(s):",
            file=sys.stderr,
        )
        for test, reason in result.skipped:
            print(f"  - {test.id()}: {reason}", file=sys.stderr)
        return 1
    if result.expectedFailures:
        print(
            f"required suite expected {len(result.expectedFailures)} failure(s); "
            "move optional work out of the required suite",
            file=sys.stderr,
        )
        return 1
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
