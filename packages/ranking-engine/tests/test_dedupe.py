"""Dedupe tests.

Two properties matter more than the rest, because their failure modes are
silent:

* **Determinism.** Same library in, same groups and same primaries out. A pass
  that reshuffled between runs would make "why did this photo disappear from my
  album" unanswerable.

* **No false merges.** A false merge deletes a photo from every automated
  output and nobody is told. A false split merely shows the user two similar
  photos. The thresholds are tuned on that asymmetry, and the tests hold it.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = PACKAGE_ROOT.parent.parent
sys.path.insert(0, str(PACKAGE_ROOT))

from memory_engine_ranking import (  # noqa: E402
    DEFAULT_HAMMING_DECISIVE_THRESHOLD,
    DEFAULT_HAMMING_THRESHOLD,
    Candidate,
    assignments,
    band_keys,
    candidate_pairs,
    cosine_distance,
    find_duplicates,
    hamming_distance,
    select_primary,
)

FIXTURES = REPO_ROOT / "contracts" / "fixtures"


def mid(char: str) -> str:
    return char * 64


class TestHashArithmetic(unittest.TestCase):
    def test_hamming_distance_counts_bits(self):
        self.assertEqual(0, hamming_distance("ff", "ff"))
        self.assertEqual(8, hamming_distance("ff", "00"))
        self.assertEqual(1, hamming_distance("f0e1c3878f1e3c78", "f0e1c3878f1e3c79"))

    def test_comparing_different_length_hashes_is_an_error(self):
        """A length mismatch means different algorithms. Returning a number
        would be worse than failing: it would silently poison the buckets."""
        with self.assertRaises(ValueError):
            hamming_distance("ffff", "ff")

    def test_cosine_distance_endpoints(self):
        self.assertAlmostEqual(0.0, cosine_distance([1, 0], [1, 0]), places=9)
        self.assertAlmostEqual(1.0, cosine_distance([1, 0], [0, 1]), places=9)
        self.assertAlmostEqual(2.0, cosine_distance([1, 0], [-1, 0]), places=9)

    def test_bands_partition_the_hash_exactly(self):
        keys = band_keys("f0e1c3878f1e3c78", bands=4)
        self.assertEqual(4, len(keys))
        self.assertEqual("f0e1c3878f1e3c78", "".join(k[1] for k in keys))

    def test_band_position_is_part_of_the_key(self):
        """Two hashes sharing a slice in different positions are a coincidence,
        not a similarity."""
        a = dict(band_keys("aaaabbbbccccdddd"))
        self.assertNotEqual(a[0], a[1])
        self.assertEqual(("aaaa"), a[0])


class TestCandidatePairs(unittest.TestCase):
    def test_identical_hashes_are_candidates(self):
        items = [
            Candidate(mid("a"), phash_hex="f0e1c3878f1e3c78"),
            Candidate(mid("b"), phash_hex="f0e1c3878f1e3c78"),
        ]
        self.assertEqual([(mid("a"), mid("b"))], candidate_pairs(items))

    def test_wildly_different_hashes_are_not_candidates(self):
        items = [
            Candidate(mid("a"), phash_hex="0000000000000000"),
            Candidate(mid("b"), phash_hex="ffffffffffffffff"),
        ]
        self.assertEqual([], candidate_pairs(items))

    def test_a_close_hash_is_found_via_a_shared_band(self):
        """One bit differs, so three of four bands still match exactly. This is
        the pigeonhole property the whole approach rests on."""
        items = [
            Candidate(mid("a"), phash_hex="f0e1c3878f1e3c78"),
            Candidate(mid("b"), phash_hex="f0e1c3878f1e3c79"),
        ]
        self.assertEqual(1, len(candidate_pairs(items)))

    def test_items_without_a_hash_are_not_paired(self):
        """A file we could not fingerprint is not evidence of similarity."""
        items = [
            Candidate(mid("a"), phash_hex=None),
            Candidate(mid("b"), phash_hex=None),
        ]
        self.assertEqual([], candidate_pairs(items))

    def test_pairs_are_sorted_and_deduplicated(self):
        items = [Candidate(mid(c), phash_hex="f0e1c3878f1e3c78") for c in "abc"]
        pairs = candidate_pairs(items)
        self.assertEqual(sorted(set(pairs)), pairs)
        self.assertEqual(3, len(pairs))
        for a, b in pairs:
            self.assertLess(a, b)

    def test_degenerate_hashes_do_not_explode_quadratically(self):
        """600 all-black frames share every band. Pairing them all is quadratic
        work on exactly the junk that gets eliminated anyway."""
        items = [
            Candidate(f"{i:064x}", phash_hex="0000000000000000") for i in range(600)
        ]
        self.assertEqual([], candidate_pairs(items))


class TestGrouping(unittest.TestCase):
    def test_a_burst_collapses_to_one_group(self):
        burst = [
            Candidate(mid("a"), phash_hex="f0e1c3878f1e3c78", quality=0.70),
            Candidate(mid("b"), phash_hex="f0e1c3878f1e3c79", quality=0.91),
            Candidate(mid("c"), phash_hex="f0e1c3878f1e3c7b", quality=0.65),
        ]
        groups = find_duplicates(burst)
        self.assertEqual(1, len(groups))
        self.assertEqual(3, groups[0].size)
        self.assertEqual(mid("b"), groups[0].primary_media_id)

    def test_unrelated_photos_form_no_group(self):
        items = [
            Candidate(mid("a"), phash_hex="0000000000000000"),
            Candidate(mid("b"), phash_hex="ffffffffffffffff"),
            Candidate(mid("c"), phash_hex="0f0f0f0f0f0f0f0f"),
        ]
        self.assertEqual([], find_duplicates(items))

    def test_grouping_is_transitive(self):
        """a~b and b~c means all three are one group, even if a and c are
        further apart than the threshold on their own."""
        items = [
            Candidate(mid("a"), phash_hex="f0e1c3878f1e3c70"),
            Candidate(mid("b"), phash_hex="f0e1c3878f1e3c78"),
            Candidate(mid("c"), phash_hex="f0e1c3878f1e3c7c"),
        ]
        groups = find_duplicates(items)
        self.assertEqual(1, len(groups))
        self.assertEqual(3, groups[0].size)

    def test_the_embedding_prevents_a_false_merge(self):
        """Two dark frames can land close in pHash while being different
        photographs. Merging them silently drops one from every album, so the
        embedding is the arbiter whenever both sides have one."""
        close_hashes = ("f0e1c3878f1e3c78", "f0e1c3878f1e3c79")
        without = find_duplicates([
            Candidate(mid("a"), phash_hex=close_hashes[0]),
            Candidate(mid("b"), phash_hex=close_hashes[1]),
        ])
        self.assertEqual(1, len(without))

        with_embeddings = find_duplicates([
            Candidate(mid("a"), phash_hex=close_hashes[0], embedding=[1.0, 0.0, 0.0]),
            Candidate(mid("b"), phash_hex=close_hashes[1], embedding=[0.0, 1.0, 0.0]),
        ])
        self.assertEqual([], with_embeddings, "different subjects must not merge")

    def test_matching_embeddings_confirm_a_merge(self):
        groups = find_duplicates([
            Candidate(mid("a"), phash_hex="f0e1c3878f1e3c78", embedding=[1.0, 0.0, 0.02]),
            Candidate(mid("b"), phash_hex="f0e1c3878f1e3c79", embedding=[1.0, 0.0, 0.01]),
        ])
        self.assertEqual(1, len(groups))
        self.assertEqual("phash_bucket_embedding_refined", groups[0].method)

    def test_a_duplicate_media_id_is_rejected(self):
        with self.assertRaises(ValueError):
            find_duplicates([
                Candidate(mid("a"), phash_hex="f0e1c3878f1e3c78"),
                Candidate(mid("a"), phash_hex="f0e1c3878f1e3c79"),
            ])


class TestDecisiveThreshold(unittest.TestCase):
    """Regression: found by running the real pipeline, not by reasoning.

    Two genuinely different scenes sat 6 bits apart and merged into one group,
    silently dropping one from every automated output. The candidate threshold
    (10) is right for *finding* pairs worth comparing; it is far too loose for
    *deciding* when no embedding exists to arbitrate.
    """

    @staticmethod
    def _hex_at_distance(base: str, bits: int) -> str:
        value = int(base, 16)
        for i in range(bits):
            value ^= 1 << i
        return f"{value:016x}"

    BASE = "f0e1c3878f1e3c78"

    def test_six_bits_apart_does_not_merge_without_an_embedding(self):
        items = [
            Candidate(mid("a"), phash_hex=self.BASE),
            Candidate(mid("b"), phash_hex=self._hex_at_distance(self.BASE, 6)),
        ]
        self.assertEqual(
            [], find_duplicates(items),
            "6 bits apart is a candidate, not a decision -- merging silently "
            "drops one photo from every album",
        )

    def test_a_real_burst_still_merges(self):
        """Near-identical frames of one moment are 0-3 bits apart. The fix must
        not cost us the case dedupe exists for."""
        items = [
            Candidate(mid("a"), phash_hex=self.BASE, quality=0.6),
            Candidate(mid("b"), phash_hex=self._hex_at_distance(self.BASE, 2), quality=0.9),
            Candidate(mid("c"), phash_hex=self.BASE, quality=0.5),
        ]
        groups = find_duplicates(items)
        self.assertEqual(1, len(groups))
        self.assertEqual(3, groups[0].size)
        self.assertEqual(mid("b"), groups[0].primary_media_id)

    def test_an_embedding_re_enables_the_generous_threshold(self):
        """With an arbiter present, a distant pHash pair may still merge --
        that is what the candidate threshold is for."""
        items = [
            Candidate(mid("a"), phash_hex=self.BASE, embedding=[1.0, 0.0, 0.02]),
            Candidate(
                mid("b"),
                phash_hex=self._hex_at_distance(self.BASE, 8),
                embedding=[1.0, 0.0, 0.01],
            ),
        ]
        groups = find_duplicates(items)
        self.assertEqual(1, len(groups), "the embedding, not the hash, decides here")
        self.assertEqual("phash_bucket_embedding_refined", groups[0].method)

    def test_the_decisive_threshold_is_stricter_than_the_candidate_one(self):
        self.assertLess(DEFAULT_HAMMING_DECISIVE_THRESHOLD, DEFAULT_HAMMING_THRESHOLD)


class TestDeterminism(unittest.TestCase):
    ITEMS = [
        Candidate(mid("a"), phash_hex="f0e1c3878f1e3c78", quality=0.70),
        Candidate(mid("b"), phash_hex="f0e1c3878f1e3c79", quality=0.91),
        Candidate(mid("c"), phash_hex="f0e1c3878f1e3c7b", quality=0.65),
        Candidate(mid("d"), phash_hex="0f0f0f0f0f0f0f0f", quality=0.50),
    ]

    def test_input_order_does_not_change_the_result(self):
        forward = find_duplicates(self.ITEMS)
        backward = find_duplicates(list(reversed(self.ITEMS)))
        self.assertEqual(
            [(g.group_id, g.primary_media_id, g.members) for g in forward],
            [(g.group_id, g.primary_media_id, g.members) for g in backward],
        )

    def test_group_ids_are_stable_across_runs(self):
        first = find_duplicates(self.ITEMS)
        second = find_duplicates(self.ITEMS)
        self.assertEqual([g.group_id for g in first], [g.group_id for g in second])

    def test_existing_group_ids_can_be_preserved(self):
        """Re-running dedupe on a growing library must not reshuffle the ids
        already stored against records."""
        groups = find_duplicates(self.ITEMS)
        anchor = min(groups[0].members)
        again = find_duplicates(self.ITEMS, group_id_for={anchor: "kept-id"})
        self.assertEqual("kept-id", again[0].group_id)


class TestPrimarySelection(unittest.TestCase):
    def test_highest_quality_wins(self):
        members = [
            Candidate(mid("a"), quality=0.4),
            Candidate(mid("b"), quality=0.9),
            Candidate(mid("c"), quality=0.7),
        ]
        self.assertEqual(mid("b"), select_primary(members).media_id)

    def test_a_user_favourite_outranks_quality(self):
        """The system's opinion about sharpness does not overrule a human's
        opinion about which frame mattered."""
        members = [
            Candidate(mid("a"), quality=0.95),
            Candidate(mid("b"), quality=0.30, favorite=True),
        ]
        self.assertEqual(mid("b"), select_primary(members).media_id)

    def test_ties_break_deterministically(self):
        members = [Candidate(mid(c), quality=0.5) for c in "cab"]
        self.assertEqual(mid("a"), select_primary(members).media_id)
        self.assertEqual(
            select_primary(members).media_id,
            select_primary(list(reversed(members))).media_id,
        )

    def test_an_empty_group_is_an_error(self):
        with self.assertRaises(ValueError):
            select_primary([])


class TestAssignments(unittest.TestCase):
    def test_output_matches_the_contract_dedupe_shape(self):
        """The result is written straight into MediaRecord.dedupe. Translation
        between a planner's output and a record's shape is where fields get
        quietly dropped, so there is none."""
        schema = json.loads(
            (REPO_ROOT / "contracts/schemas/media-record.schema.json").read_text(
                encoding="utf-8"
            )
        )
        expected = set(schema["$defs"]["DedupeMembership"]["properties"])
        required = set(schema["$defs"]["DedupeMembership"]["required"])

        groups = find_duplicates([
            Candidate(mid("a"), phash_hex="f0e1c3878f1e3c78", quality=0.7),
            Candidate(mid("b"), phash_hex="f0e1c3878f1e3c79", quality=0.9),
        ])
        for media_id, update in assignments(groups).items():
            with self.subTest(media=media_id[:8]):
                self.assertTrue(set(update).issubset(expected), set(update) - expected)
                self.assertTrue(required.issubset(set(update)))

    def test_the_method_is_a_value_the_contract_allows(self):
        schema = json.loads(
            (REPO_ROOT / "contracts/schemas/media-record.schema.json").read_text(
                encoding="utf-8"
            )
        )
        allowed = set(
            schema["$defs"]["DedupeMembership"]["properties"]["method"]["enum"]
        )
        groups = find_duplicates([
            Candidate(mid("a"), phash_hex="f0e1c3878f1e3c78"),
            Candidate(mid("b"), phash_hex="f0e1c3878f1e3c79"),
        ])
        for update in assignments(groups).values():
            self.assertIn(update["method"], allowed)

    def test_exactly_one_primary_per_group(self):
        groups = find_duplicates([
            Candidate(mid(c), phash_hex="f0e1c3878f1e3c78", quality=q)
            for c, q in zip("abc", (0.5, 0.9, 0.7))
        ])
        primaries = [u for u in assignments(groups).values() if u["is_primary"]]
        self.assertEqual(1, len(primaries))

    def test_every_member_points_at_the_same_primary(self):
        groups = find_duplicates([
            Candidate(mid(c), phash_hex="f0e1c3878f1e3c78", quality=q)
            for c, q in zip("abc", (0.5, 0.9, 0.7))
        ])
        updates = assignments(groups)
        self.assertEqual(1, len({u["primary_media_id"] for u in updates.values()}))


class TestAgainstGoldenFixtures(unittest.TestCase):
    """The dedupe input comes from real MediaRecords, so it is built from them."""

    def _candidates(self) -> list[Candidate]:
        manifest = json.loads((FIXTURES / "index.json").read_text(encoding="utf-8"))
        out = []
        for entry in manifest["fixtures"]:
            if entry["schema"] != "media-record" or entry["expectation"] != "valid":
                continue
            record = json.loads((FIXTURES / entry["path"]).read_text(encoding="utf-8"))
            phash = (record.get("perceptual") or {}).get("image_hash") or {}
            quality = (record.get("quality") or {}).get("aesthetic") or {}
            out.append(
                Candidate(
                    media_id=record["media_id"],
                    phash_hex=phash.get("hex"),
                    phash_bits=phash.get("bits", 64),
                    quality=quality.get("value", 0.0),
                    captured_utc=record["capture"]["captured_at"].get("utc"),
                    favorite=(record.get("user") or {}).get("favorite", False),
                )
            )
        return out

    def test_real_records_can_be_deduped_without_error(self):
        candidates = self._candidates()
        self.assertTrue(candidates)
        find_duplicates(candidates)

    def test_hashes_of_different_lengths_coexist_safely(self):
        """One fixture carries a 256-bit hash and the rest 64-bit. Banding must
        not compare across them, and must not crash."""
        candidates = self._candidates()
        lengths = {len(c.phash_hex) for c in candidates if c.phash_hex}
        self.assertGreater(len(lengths), 1, "fixtures no longer cover mixed hash widths")
        find_duplicates(candidates)


if __name__ == "__main__":
    unittest.main()
