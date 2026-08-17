"""Run the ml-runtime suite and fail when any required test is skipped."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


def main() -> int:
    tests_dir = Path(__file__).resolve().parent
    repo_root = tests_dir.parents[2]
    sys.path.insert(0, str(repo_root))
    suite = unittest.defaultTestLoader.discover(str(tests_dir))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if result.skipped:
        print(
            f"ml-runtime required suite skipped {len(result.skipped)} test(s)",
            file=sys.stderr,
        )
        return 1
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
