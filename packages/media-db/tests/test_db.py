"""The migration runner's own rules.

Separate from test_media_db.py, which is about what the database can store.
This file is about what the loader will and will not accept as a migration.

unittest.TestCase so the same file runs under `python3 -m unittest discover`
(what scripts/ci/run-workspace-check.mjs uses) and under pytest.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PACKAGE_ROOT))

from memory_engine_media_db import db  # noqa: E402


class TestMigrationFilenamesAreRefusedNotSkipped(unittest.TestCase):
    """A stray file in the migrations directory must name itself.

    Regression test for a real incident. Something on a development machine
    dropped a byte-identical `0002_proxy_index 2.sql` beside the original; the
    loader's `*.sql` glob swallowed it as a second version 2, and main went red
    with "migration versions are not contiguous" -- an error that reads as a
    broken migration set rather than as a file that should not exist. The
    debugging went to the wrong question first.

    Refusing by name rather than skipping unknown names is the deliberate
    choice. A skipped shadow copy stays on disk, invisible, for every other
    directory-walking loader in this repository to find.
    """

    def test_a_shadow_copy_is_refused_by_name(self) -> None:
        shadow = db.MIGRATIONS_DIR / "0002_proxy_index 2.sql"
        shadow.write_text("SELECT 1;\n", encoding="utf-8")
        try:
            with self.assertRaises(db.MigrationError) as caught:
                db.discover_migrations()
            message = str(caught.exception)
            self.assertIn("0002_proxy_index 2.sql", message)
            self.assertIn("not a migration filename", message)
            # The whole point: it must not present as a numbering problem.
            self.assertNotIn("contiguous", message)
        finally:
            shadow.unlink()

    def test_the_committed_migrations_all_pass_the_pattern(self) -> None:
        found = sorted(db.MIGRATIONS_DIR.glob("*.sql"))
        self.assertTrue(found, "no migrations found at all")
        for path in found:
            with self.subTest(migration=path.name):
                self.assertRegex(path.name, db.MIGRATION_FILENAME)

    def test_versions_are_still_required_to_be_contiguous(self) -> None:
        """The original rule must survive the new one.

        A well-named migration with a gap in its numbering is a different
        failure and still has to be caught.
        """
        gap = db.MIGRATIONS_DIR / "0099_far_future.sql"
        gap.write_text("SELECT 1;\n", encoding="utf-8")
        try:
            with self.assertRaises(db.MigrationError) as caught:
                db.discover_migrations()
            self.assertIn("contiguous", str(caught.exception))
        finally:
            gap.unlink()


if __name__ == "__main__":
    unittest.main()
