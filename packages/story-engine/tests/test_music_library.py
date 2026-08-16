"""The music library must not contain a track that could not legally be used.

Every entry's licence block is validated as a `MusicLicense` from the EDL
contract, so a track sitting in the library looking usable really is usable --
`music_license_covers_destination` is a hard EDL validation check, and a track
that would fail it has no business being offered to a planner.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = PACKAGE_ROOT.parent.parent
LIBRARY = PACKAGE_ROOT / "music" / "library.json"
SCHEMA_DIR = REPO_ROOT / "contracts" / "schemas"

DATA = json.loads(LIBRARY.read_text(encoding="utf-8"))

# Social destinations a reel may be published to. A track cleared only for
# private playback cannot back a cut aimed at any of these.
SOCIAL_DESTINATIONS = frozenset({"social_share", "commercial_use"})


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
    # Referenced through the registry, not lifted out of the document, so any
    # local $ref inside the definition still resolves.
    return Draft202012Validator(
        {"$ref": "edl.schema.json#/$defs/MusicLicense"}, registry=registry
    )


class TestMusicLibrary(unittest.TestCase):
    def test_every_licence_block_is_a_valid_contract_music_licence(self):
        validator = _validator()
        for track in DATA["tracks"]:
            with self.subTest(track=track["track_id"]):
                errors = sorted(
                    validator.iter_errors(track["license"]), key=lambda e: list(e.path)
                )
                self.assertEqual([], [f"{list(e.path)}: {e.message}" for e in errors])

    def test_every_track_matches_the_stated_policy(self):
        accepted = set(DATA["policy"]["accepted_license_types"])
        for track in DATA["tracks"]:
            with self.subTest(track=track["track_id"]):
                self.assertIn(
                    track["license"]["license_type"],
                    accepted,
                    f"library policy accepts {sorted(accepted)}; this track is not one",
                )

    def test_no_track_requires_attribution_under_the_current_policy(self):
        """The CC0 decision is what lets the reel planner skip an attribution
        overlay entirely. A single attribution-required track in the library
        would quietly invalidate that, because the planner has no code to
        display a credit."""
        if DATA["policy"]["require_attribution"]:
            self.skipTest("policy has moved to attribution-required music")
        for track in DATA["tracks"]:
            with self.subTest(track=track["track_id"]):
                self.assertFalse(
                    track["license"]["attribution_required"],
                    "an attribution-required track needs an overlay the planner "
                    "does not build; see music/README.md before adding one",
                )

    def test_every_track_is_cleared_for_sharing(self):
        for track in DATA["tracks"]:
            with self.subTest(track=track["track_id"]):
                cleared = set(track["license"]["cleared_for"])
                self.assertTrue(
                    SOCIAL_DESTINATIONS & cleared,
                    "a reel that cannot be shared is not a reel",
                )

    def test_track_ids_are_unique(self):
        ids = [t["track_id"] for t in DATA["tracks"]]
        self.assertEqual(len(ids), len(set(ids)))

    def test_placeholders_are_honestly_marked(self):
        """No entry may look playable when it is not, and no entry may claim a
        verified licence it has not had checked at the source."""
        for track in DATA["tracks"]:
            with self.subTest(track=track["track_id"]):
                if track["status"] == "placeholder":
                    self.assertIsNone(track["audio_path"])
                    self.assertFalse(track["verified"])
                    self.assertIsNone(
                        track["license"]["license_id"],
                        "a placeholder must not carry a licence id it cannot back up",
                    )

    def test_no_audio_is_claimed_to_be_bundled_while_none_is(self):
        if not DATA["policy"]["audio_bundled"]:
            for track in DATA["tracks"]:
                with self.subTest(track=track["track_id"]):
                    self.assertIsNone(track["audio_path"])

    def test_a_verified_track_must_name_its_licence(self):
        """Guard for when real tracks land: verified means someone checked the
        licence at the source, and the evidence is the licence id."""
        for track in DATA["tracks"]:
            if track["verified"]:
                with self.subTest(track=track["track_id"]):
                    self.assertIsNotNone(track["license"]["license_id"])
                    self.assertIsNotNone(track["audio_path"])

    def test_bpm_hints_are_musically_plausible(self):
        for track in DATA["tracks"]:
            with self.subTest(track=track["track_id"]):
                bpm = track["bpm_hint"]
                self.assertGreaterEqual(bpm, 40)
                self.assertLessEqual(bpm, 220)


if __name__ == "__main__":
    unittest.main()
