"""Vendor profiles must be valid against the contract and internally coherent.

A profile is the spec the print validator enforces. A malformed one does not
fail loudly -- it fails by confidently passing an album that the printer then
rejects, which costs a refund and a customer. So it is checked here, before it
can reach a print job.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = PACKAGE_ROOT.parent.parent
PROFILE_DIR = PACKAGE_ROOT / "vendor_profiles"
SCHEMA_DIR = REPO_ROOT / "contracts" / "schemas"


def _profiles() -> list[tuple[str, dict]]:
    return [
        (path.name, json.loads(path.read_text(encoding="utf-8")))
        for path in sorted(PROFILE_DIR.glob("*.json"))
    ]


def _validator():
    from jsonschema import Draft202012Validator
    from referencing import Registry, Resource

    documents = {
        path.name: json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(SCHEMA_DIR.glob("*.schema.json"))
    }
    registry = Registry().with_resources(
        [(name, Resource.from_contents(doc)) for name, doc in documents.items()]
    )
    # Reference the definition through the registry rather than lifting the
    # subschema out of its document. Extracting it strips the base URI, and its
    # internal `#/$defs/SizeMm` reference then resolves against nothing.
    return Draft202012Validator(
        {"$ref": "album-spec.schema.json#/$defs/VendorProfile"}, registry=registry
    )


class TestVendorProfiles(unittest.TestCase):
    def test_at_least_two_profiles_ship(self):
        """The vendor abstraction is only proven if two profiles genuinely differ."""
        self.assertGreaterEqual(len(_profiles()), 2)

    def test_every_profile_validates_against_the_contract(self):
        validator = _validator()
        for name, profile in _profiles():
            with self.subTest(profile=name):
                errors = sorted(validator.iter_errors(profile), key=lambda e: list(e.path))
                self.assertEqual([], [f"{list(e.path)}: {e.message}" for e in errors])

    def test_profile_ids_are_unique(self):
        seen = [(p["vendor_id"], p["product_id"]) for _, p in _profiles()]
        self.assertEqual(len(seen), len(set(seen)))

    def test_geometry_is_internally_coherent(self):
        for name, profile in _profiles():
            with self.subTest(profile=name):
                trim = profile["trim_size_mm"]
                self.assertGreater(trim["width_mm"], 0)
                self.assertGreater(trim["height_mm"], 0)

                # A safe margin wider than half the page leaves no printable area.
                self.assertLess(profile["safe_margin_mm"] * 2, trim["width_mm"])
                self.assertLess(profile["safe_margin_mm"] * 2, trim["height_mm"])

                # Bleed extends outward; a bleed larger than the trim is nonsense.
                self.assertLess(profile["bleed_mm"], trim["width_mm"] / 2)

                # The gutter eats into the inner edge of each page of a spread.
                self.assertLess(profile["gutter_mm"] * 2, trim["width_mm"])

    def test_dpi_floor_is_the_commercial_print_standard(self):
        for name, profile in _profiles():
            with self.subTest(profile=name):
                self.assertGreaterEqual(
                    profile["dpi_floor"],
                    300.0,
                    "300 DPI is the commercial print floor; going below it is a "
                    "product decision, not a default",
                )
                if profile.get("dpi_preferred") is not None:
                    self.assertGreaterEqual(profile["dpi_preferred"], profile["dpi_floor"])

    def test_page_counts_are_satisfiable(self):
        for name, profile in _profiles():
            with self.subTest(profile=name):
                counts = profile["page_count"]
                self.assertLessEqual(counts["minimum"], counts["maximum"])
                self.assertEqual(
                    0,
                    counts["minimum"] % counts["increment"],
                    "the minimum page count must itself be on the increment, or no "
                    "valid album exists at the smallest size",
                )

    def test_layflat_has_a_smaller_gutter_than_perfect_bound(self):
        """The structural difference that justifies having two profiles at all.

        A layflat spread opens completely; a perfect-bound one swallows its
        inner edge. A face 8mm from the spine is fine in one and gone in the
        other, and that is a user-visible failure the validator must catch.
        """
        by_binding = {p["binding"]: p for _, p in _profiles()}
        if "layflat" in by_binding and "perfect_bound" in by_binding:
            self.assertLess(
                by_binding["layflat"]["gutter_mm"],
                by_binding["perfect_bound"]["gutter_mm"],
            )

    def test_the_album_fixture_uses_a_shipped_profile_verbatim(self):
        """The golden album fixture must validate against a profile we actually
        ship, not against numbers invented inside the fixture. Otherwise the
        print validator is tested against a spec that exists nowhere, which is
        the same failure as validating against an imagined vendor."""
        fixture = json.loads(
            (
                REPO_ROOT
                / "contracts/fixtures/album-spec/valid/album-thailand-validated.json"
            ).read_text(encoding="utf-8")
        )
        shipped = [profile for _, profile in _profiles()]
        self.assertIn(
            fixture["vendor_profile"],
            shipped,
            "the fixture's vendor_profile has drifted from every shipped profile",
        )

    def test_profiles_are_marked_as_defaults_not_real_vendors(self):
        """These are placeholders until a real spec sheet arrives. The version
        string says so, so an album validated against them cannot be mistaken
        for one validated against a signed vendor."""
        for name, profile in _profiles():
            with self.subTest(profile=name):
                self.assertIn(
                    "default",
                    profile["profile_version"],
                    "a placeholder profile must announce itself in profile_version",
                )


if __name__ == "__main__":
    unittest.main()
