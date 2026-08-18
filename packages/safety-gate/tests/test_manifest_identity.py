"""`manifest_id`, the golden vectors, and the committed contract fixtures.

WHY THE VECTORS FILE EXISTS AT ALL

The Python planner writes a clearance and the TypeScript print worker recomputes
its id before emitting a PDF. If the two disagree about the canonical form, the
symptom is not a wrong answer -- it is a gate refusing correct output, which is
how gates end up disabled. `contracts/vectors/safety-clearance-manifest-id.json`
is the one table both implementations are checked against, and it carries the
PRE-IMAGE BYTES as well as the digest: a digest mismatch says only that
something diverged, the pre-image says which field did.

This file is the Python half. `workers/render-print/test/clearance.test.ts` is
the other one, reading the same file.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from support import FIXTURE_DIR, REPO_ROOT, assert_valid_clearance  # noqa: E402

from memory_engine_safety.canonical import (  # noqa: E402
    canonical_bytes,
    ecmascript_number,
    manifest_body,
    manifest_id,
)

VECTORS_PATH = REPO_ROOT / "contracts" / "vectors" / "safety-clearance-manifest-id.json"
VECTORS = json.loads(VECTORS_PATH.read_text(encoding="utf-8"))


class TestGoldenVectors(unittest.TestCase):
    def test_there_are_vectors_to_check(self):
        self.assertGreaterEqual(len(VECTORS["vectors"]), 4)

    def test_every_vector_reproduces_its_preimage_and_digest(self):
        for vector in VECTORS["vectors"]:
            with self.subTest(vector=vector["name"]):
                preimage = canonical_bytes(manifest_body(vector["manifest"]))
                self.assertEqual(
                    vector["preimage_utf8_hex"],
                    preimage.hex(),
                    "the canonical bytes diverged; compare the two pre-images "
                    "rather than the digests",
                )
                self.assertEqual(vector["manifest_id"], manifest_id(vector["manifest"]))

    def test_the_same_body_with_a_different_decision_has_the_same_id(self):
        """THE PROPERTY THAT LOOKS LIKE A HOLE AND IS NOT.

        `decision` is excluded from the digest because it is derived. So two
        manifests that differ only in their decision block are the same
        publication and share an id -- which is safe exactly as long as every
        verifier recomputes the decision from the items rather than reading it,
        and denies on disagreement. `verify.py` does both, and
        `test_verify.py::test_a_decision_block_that_lies_about_its_items` is the
        other half of this pair.
        """
        blocked = json.loads(
            (
                FIXTURE_DIR
                / "safety-clearance"
                / "valid"
                / "frontier-egress-blocked-by-indeterminate.json"
            ).read_text(encoding="utf-8")
        )
        lying = json.loads(
            (
                FIXTURE_DIR
                / "safety-clearance"
                / "schema-invalid"
                / "cleared-while-indeterminate.json"
            ).read_text(encoding="utf-8")
        )
        self.assertNotEqual(blocked["decision"], lying["decision"])
        self.assertEqual(manifest_body(blocked), manifest_body(lying))
        self.assertEqual(blocked["manifest_id"], lying["manifest_id"])


class TestTheNumberRule(unittest.TestCase):
    """The one thing that has actually broken a digest in this repository."""

    def test_an_integral_float_renders_without_its_fraction(self):
        self.assertEqual(b'{"a":1}', canonical_bytes({"a": 1.0}))
        self.assertEqual(b'{"a":0}', canonical_bytes({"a": 0.0}))

    def test_a_shortest_round_trip_decimal_survives(self):
        self.assertEqual(
            b'{"a":0.30000000000000004}', canonical_bytes({"a": 0.1 + 0.2})
        )

    def test_exponent_notation_is_refused_rather_than_written(self):
        with self.assertRaises(ValueError):
            ecmascript_number(1e-7)
        with self.assertRaises(ValueError):
            canonical_bytes({"tiny": 1e-7})

    def test_keys_sort_by_code_unit_not_by_locale(self):
        """`localeCompare` puts "a" before "B"; RFC 8785 does not."""
        self.assertEqual(b'{"B":1,"a":2}', canonical_bytes({"a": 2, "B": 1}))

    def test_a_non_finite_number_cannot_enter_a_digest(self):
        for value in (float("nan"), float("inf")):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    canonical_bytes({"a": value})


class TestTheCommittedFixtures(unittest.TestCase):
    """The golden fixtures both agents build against, checked end to end."""

    def _fixtures(self, kind: str) -> list[Path]:
        return sorted((FIXTURE_DIR / "safety-clearance" / kind).glob("*.json"))

    def test_every_valid_fixture_validates_and_its_id_recomputes(self):
        paths = self._fixtures("valid")
        self.assertTrue(paths)
        for path in paths:
            with self.subTest(fixture=path.name):
                document = json.loads(path.read_text(encoding="utf-8"))
                assert_valid_clearance(self, document)
                self.assertEqual(document["manifest_id"], manifest_id(document))

    def test_every_fixture_declares_the_contract_class_order(self):
        """Except the one whose entire purpose is not to."""
        for kind in ("valid", "schema-invalid"):
            for path in self._fixtures(kind):
                document = json.loads(path.read_text(encoding="utf-8"))
                declared = document["classifier"]["class_order"]
                with self.subTest(fixture=path.name):
                    if path.name == "class-order-transposed.json":
                        self.assertNotEqual(
                            ["explicit", "suggestive", "medical_or_artistic"], declared
                        )
                        self.assertEqual(
                            sorted(["explicit", "suggestive", "medical_or_artistic"]),
                            sorted(declared),
                            "the fixture must be a TRANSPOSITION, not a typo -- the "
                            "whole point is that it is the same three names",
                        )
                    else:
                        self.assertEqual(
                            ["explicit", "suggestive", "medical_or_artistic"], declared
                        )

    def test_the_transposed_fixture_is_otherwise_perfectly_valid(self):
        """Which is exactly why it needed a schema rule to catch it.

        Same scores, same thresholds, same counts, same everything. Take out the
        `const` on `class_order` and this document is indistinguishable from
        `print-run-fully-cleared.json` to every other check in the repository --
        while describing a model whose first column is `suggestive`.
        """
        good = json.loads(
            (FIXTURE_DIR / "safety-clearance" / "valid" / "print-run-fully-cleared.json")
            .read_text(encoding="utf-8")
        )
        transposed = json.loads(
            (
                FIXTURE_DIR
                / "safety-clearance"
                / "schema-invalid"
                / "class-order-transposed.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(good["items"], transposed["items"])
        self.assertEqual(good["thresholds"], transposed["thresholds"])
        self.assertEqual(good["decision"], transposed["decision"])
        self.assertEqual(transposed["manifest_id"], manifest_id(transposed))

    def test_the_fixture_index_lists_every_file_on_disk(self):
        index = json.loads(
            (FIXTURE_DIR / "index.json").read_text(encoding="utf-8")
        )
        listed = {
            entry["path"]
            for entry in index["fixtures"]
            if entry["schema"] == "safety-clearance"
        }
        on_disk = {
            f"safety-clearance/{path.parent.name}/{path.name}"
            for kind in ("valid", "schema-invalid")
            for path in self._fixtures(kind)
        }
        self.assertEqual(on_disk, listed)


if __name__ == "__main__":
    unittest.main()
