"""Benchmark-library identity tests: a stale library must not pass quietly.

`harness.py` refuses a comparison whose MODEL digests disagree. Until this
module existed nothing refused a comparison whose INPUTS disagreed beyond a
single opaque digest the caller typed in, which made the check a check on
somebody's clipboard. Every test here is a way a library can be the wrong
library while looking right.
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PACKAGE_ROOT))

from memory_engine_eval import library as lib  # noqa: E402


def _declaration(**overrides) -> dict:
    document = {
        "library_id": "unit-library",
        "library_version": "1",
        "provenance": "synthetic_generated",
        "claim_ceiling": "plumbing",
        "description": "drawn, not photographed",
        "file_count": 2,
        "inventory_digest": "0" * 64,
        "generator": {
            "command": ["python3", "make.py"],
            "generator_version": 2,
            "seed": 7,
        },
        "consent": None,
    }
    document.update(overrides)
    return document


def _write(document: object, suffix: str = ".library.json") -> Path:
    handle = tempfile.NamedTemporaryFile(
        "w", suffix=suffix, delete=False, encoding="utf-8"
    )
    json.dump(document, handle)
    handle.close()
    return Path(handle.name)


class TestTheDeclaration(unittest.TestCase):
    def assertRefused(self, document: object, fragment: str) -> None:
        with self.assertRaises(lib.LibraryError) as raised:
            lib.load_library_declaration(_write(document))
        self.assertIn(fragment, str(raised.exception))

    def test_a_synthetic_library_cannot_declare_a_quality_ceiling(self) -> None:
        # The containment that stops a number measured on cartoon ovals from
        # ever being produced as a quality claim.
        self.assertRefused(
            _declaration(claim_ceiling="quality"), "no ground truth to be right about"
        )

    def test_a_synthetic_library_must_name_its_generator(self) -> None:
        self.assertRefused(
            _declaration(generator=None), "cannot be rebuilt"
        )

    def test_a_real_library_must_name_its_consent_record(self) -> None:
        self.assertRefused(
            _declaration(
                provenance="consented_real",
                claim_ceiling="quality",
                generator=None,
                consent=None,
            ),
            "is not a benchmark library",
        )

    def test_a_real_library_with_minors_needs_a_separate_consent_ref(self) -> None:
        self.assertRefused(
            _declaration(
                provenance="consented_real",
                claim_ceiling="quality",
                generator=None,
                consent={
                    "consent_ledger_ref": "ledger://1",
                    "subjects_consented": 4,
                    "minors_present": True,
                },
            ),
            "minor_consent_ref",
        )

    def test_an_unknown_field_is_refused(self) -> None:
        self.assertRefused(_declaration(claim_celing="plumbing"), "unknown field")

    def test_plumbing_and_determinism_are_siblings_not_a_chain(self) -> None:
        # A plumbing-ceilinged library must still be able to carry determinism
        # cases: "it ran" and "it repeats" are different axes, and neither is
        # below the other.
        declaration = lib.load_library_declaration(_write(_declaration()))
        self.assertTrue(declaration.permits(lib.ClaimClass.DETERMINISM))
        self.assertTrue(declaration.permits(lib.ClaimClass.PLUMBING))
        self.assertFalse(declaration.permits(lib.ClaimClass.QUALITY))


class TestResolvingAgainstADirectory(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        (self.root / "a.jpg").write_bytes(b"first file")
        (self.root / "b.jpg").write_bytes(b"second file")
        self.entries = [
            (name, lib.file_digest(self.root / name), (self.root / name).stat().st_size)
            for name in ("a.jpg", "b.jpg")
        ]
        self.manifest = {
            "generator_version": 2,
            "seed": 7,
            "files": [
                {"relpath": relpath, "blake3": digest, "byte_size": size}
                for relpath, digest, size in self.entries
            ],
        }
        self._write_manifest()
        self.declaration = lib.load_library_declaration(
            _write(
                _declaration(
                    inventory_digest=lib.inventory_digest(self.entries), file_count=2
                )
            )
        )

    def _write_manifest(self) -> None:
        (self.root / lib.MANIFEST_NAME).write_text(
            json.dumps(self.manifest), encoding="utf-8"
        )

    def test_a_matching_library_resolves(self) -> None:
        resolved = lib.resolve(self.declaration, self.root)
        self.assertEqual(sorted(resolved.files), ["a.jpg", "b.jpg"])

    def test_a_missing_root_is_unavailable_not_stale(self) -> None:
        # "I do not have it" and "I have a different one" call for different
        # actions, so they are different exceptions.
        with self.assertRaises(lib.LibraryUnavailable):
            lib.resolve(self.declaration, None)
        with self.assertRaises(lib.LibraryUnavailable):
            lib.resolve(self.declaration, self.root / "nope")

    def test_a_directory_with_no_manifest_is_not_a_library(self) -> None:
        (self.root / lib.MANIFEST_NAME).unlink()
        with self.assertRaises(lib.LibraryUnavailable):
            lib.resolve(self.declaration, self.root)

    def test_a_same_length_edit_is_caught_by_the_hash(self) -> None:
        # The failure this function exists for: a manifest that agrees with
        # itself proves nothing, so the bytes are re-hashed off the disk. A
        # same-length edit is the case a size comparison alone cannot see, and
        # it is the realistic one -- a re-encode at the same quality, a byte
        # corrupted in transit.
        (self.root / "a.jpg").write_bytes(b"FIRST file")
        with self.assertRaises(lib.LibraryStale) as raised:
            lib.resolve(self.declaration, self.root)
        self.assertIn("The manifest and the media disagree", str(raised.exception))

    def test_a_length_changing_edit_is_caught_by_the_size(self) -> None:
        (self.root / "a.jpg").write_bytes(b"first file!")
        with self.assertRaises(lib.LibraryStale) as raised:
            lib.resolve(self.declaration, self.root)
        self.assertIn("manifest says", str(raised.exception))

    def test_a_file_count_that_disagrees_is_stale(self) -> None:
        self.manifest["files"].append(
            {"relpath": "c.jpg", "blake3": "c" * 64, "byte_size": 3}
        )
        self._write_manifest()
        with self.assertRaises(lib.LibraryStale):
            lib.resolve(self.declaration, self.root)

    def test_a_regenerated_library_has_a_different_inventory_digest(self) -> None:
        (self.root / "b.jpg").write_bytes(b"second file, regenerated")
        entries = [
            (name, lib.file_digest(self.root / name), (self.root / name).stat().st_size)
            for name in ("a.jpg", "b.jpg")
        ]
        self.assertNotEqual(
            lib.inventory_digest(entries), self.declaration.inventory_digest
        )

    def test_a_bumped_generator_version_is_a_mismatch(self) -> None:
        self.manifest["generator_version"] = 3
        self._write_manifest()
        with self.assertRaises(lib.LibraryMismatch):
            lib.resolve(self.declaration, self.root)

    def test_a_manifest_entry_with_no_digest_is_an_incomplete_library(self) -> None:
        self.manifest["files"][0].pop("blake3")
        self._write_manifest()
        with self.assertRaises(lib.LibraryStale):
            lib.resolve(self.declaration, self.root)


class TestInventoryDigest(unittest.TestCase):
    def test_field_boundaries_cannot_be_shifted(self) -> None:
        # The length prefix is what stops ("ab","c") digesting like ("a","bc").
        # Without it a relpath containing the separator would let two different
        # inventories agree.
        left = lib.inventory_digest([("ab", "c" * 64, 1)])
        right = lib.inventory_digest([("a", "bc" + "c" * 62, 1)])
        self.assertNotEqual(left, right)

    def test_walk_order_does_not_change_the_digest(self) -> None:
        entries = [("b", "1" * 64, 2), ("a", "0" * 64, 1)]
        self.assertEqual(
            lib.inventory_digest(entries), lib.inventory_digest(list(reversed(entries)))
        )

    def test_a_byte_size_is_digested_as_an_integer(self) -> None:
        # `1.0` versus `1` already cost this repository every model reporting a
        # config mismatch. The size is written as its decimal digits, so no
        # reader gets to choose a float representation for it.
        self.assertEqual(
            lib.inventory_digest([("a", "0" * 64, 1)]),
            lib.inventory_digest([("a", "0" * 64, int(1.0))]),
        )


class TestTheCommittedLibraryDeclarations(unittest.TestCase):
    def test_every_committed_declaration_loads(self) -> None:
        from memory_engine_eval import benchmarks as bench

        paths = bench.library_paths()
        self.assertTrue(paths, "no committed library declaration")
        for path in paths:
            with self.subTest(path=path.name):
                declaration = lib.load_library_declaration(path)
                self.assertFalse(
                    declaration.permits(lib.ClaimClass.QUALITY),
                    f"{declaration.ref} claims a quality ceiling; no library in this "
                    "repository can support one",
                )


if __name__ == "__main__":
    unittest.main()
