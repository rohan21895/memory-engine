"""Run every required pipeline test and refuse missing prerequisites or skips.

The pipeline suite crosses process boundaries: it invokes the release ingest
worker, the compiled video renderer, FFmpeg, and ffprobe. A plain unittest
discovery turns a missing one into a successful run with most integration
classes skipped. CI must use this entry point so "green" means those paths
actually executed.

The multi-minute print raster test is an explicitly separate suite. It is not
reported as passing or skipped here; set ``MEMORY_ENGINE_SLOW_TESTS=1`` to add
it to this required run.
"""

from __future__ import annotations

import os
import shutil
import sys
import unittest
from pathlib import Path


OPTIONAL_SLOW_TEST = "test_end_to_end.EndToEnd.test_render_print_produces_a_pdf"


def _tests(suite: unittest.TestSuite):
    """Flatten unittest's nested discovery suites without trusting their depth."""
    for candidate in suite:
        if isinstance(candidate, unittest.TestSuite):
            yield from _tests(candidate)
        else:
            yield candidate


def _missing_prerequisites(repo_root: Path) -> list[str]:
    required_files = {
        "release ingest worker": (
            repo_root
            / "workers"
            / "ingest"
            / "target"
            / "release"
            / "memory-engine-ingest"
        ),
        "compiled video renderer": (
            repo_root
            / "workers"
            / "render-video"
            / "dist"
            / "workers"
            / "render-video"
            / "src"
            / "cli.js"
        ),
    }
    missing = [
        f"{name}: {path}"
        for name, path in required_files.items()
        if not path.is_file()
    ]
    for command in ("ffmpeg", "ffprobe", "node"):
        if shutil.which(command) is None:
            missing.append(f"command on PATH: {command}")
    return missing


def main() -> int:
    tests_dir = Path(__file__).resolve().parent
    repo_root = tests_dir.parents[2]
    missing = _missing_prerequisites(repo_root)
    if missing:
        print(
            "pipeline required suite cannot start; missing prerequisite(s):\n  - "
            + "\n  - ".join(missing),
            file=sys.stderr,
        )
        return 2

    discovered = unittest.defaultTestLoader.discover(str(tests_dir))
    flattened = list(_tests(discovered))
    by_id = {test.id(): test for test in flattened}
    if OPTIONAL_SLOW_TEST not in by_id:
        print(
            f"pipeline required suite lost its optional test sentinel: "
            f"{OPTIONAL_SLOW_TEST}",
            file=sys.stderr,
        )
        return 2

    include_slow = os.environ.get("MEMORY_ENGINE_SLOW_TESTS") == "1"
    selected = (
        flattened
        if include_slow
        else [test for test in flattened if test.id() != OPTIONAL_SLOW_TEST]
    )
    if not include_slow:
        print(
            "pipeline optional suite NOT RUN: "
            f"{OPTIONAL_SLOW_TEST} "
            "(set MEMORY_ENGINE_SLOW_TESTS=1 to require it)",
            file=sys.stderr,
        )

    result = unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite(selected))
    if result.skipped:
        print(
            f"pipeline required suite skipped {len(result.skipped)} test(s)",
            file=sys.stderr,
        )
        return 1
    if result.expectedFailures:
        print(
            f"pipeline required suite expected "
            f"{len(result.expectedFailures)} failure(s)",
            file=sys.stderr,
        )
        return 1
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
