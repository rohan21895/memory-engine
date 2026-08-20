"""The additive sidecar exposure on `Selection`: groups and diagnostics.

`select()` now carries OUT what it already computed INSIDE: the shot-group
membership and the per-candidate signals the greedy scored with. These tests
pin the exposure contract the pipeline's selection sidecar builds on:

  * `groups` covers every survivor and only survivors, and agrees with the
    burst semantics (`_shot_groups`): time-adjacent, similarity-linked frames
    share a group id, which is the smallest media_id in the group.
  * `diagnostics` carries, for every survivor, the standing, the raw fused
    value, each expression-axis pool percentile, and the domination flag.
  * The embedded AlbumSpec report is UNCHANGED -- the sidecar exists precisely
    so the contract object does not grow.

Runs under `python3 -m unittest` and pytest, like the rest of this directory.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from test_selection import cand, mid

from memory_engine_album.selection import select


SPACE = "exposure-test-space"


def at_s(second: int, day: int = 1) -> str:
    base = datetime(2026, 3, 1, 12, 0, 0, tzinfo=timezone.utc)
    return (base + timedelta(days=day - 1, seconds=second)).isoformat()


def unit(*components: float) -> tuple[float, ...]:
    norm = sum(c * c for c in components) ** 0.5
    return tuple(c / norm for c in components)


def burst(tag: str, second: int, value: float, **kwargs) -> object:
    """One frame of the same photograph: identical embeddings, seconds apart."""
    return cand(
        tag,
        value,
        captured_utc=at_s(second),
        embedding=unit(1.0, 0.0, 0.0),
        embedding_space=SPACE,
        **kwargs,
    )


def pool() -> list:
    return [
        burst("b1", 0, 0.90, awake=0.04),
        burst("b2", 1, 0.86, awake=-0.06),  # blinked: dominated by b1
        burst("b3", 2, 0.88, awake=0.05, smile=0.05),
        cand(
            "solo",
            0.70,
            captured_utc=at_s(0, day=3),
            embedding=unit(0.0, 1.0, 0.0),
            embedding_space=SPACE,
        ),
        cand("hidden", 0.95, user_hidden=True),
    ]


class GroupExposure(unittest.TestCase):
    def setUp(self):
        self.selection = select(pool(), 2)

    def test_groups_cover_every_survivor_and_only_survivors(self):
        rejected = {r.media_id for r in self.selection.rejected}
        self.assertIn(mid("hidden"), rejected)
        survivors = {mid("b1"), mid("b2"), mid("b3"), mid("solo")}
        self.assertEqual(survivors, set(self.selection.groups))

    def test_burst_frames_share_the_smallest_member_id_and_solo_stands_alone(self):
        groups = self.selection.groups
        expected = min(mid("b1"), mid("b2"), mid("b3"))
        self.assertEqual(expected, groups[mid("b1")])
        self.assertEqual(expected, groups[mid("b2")])
        self.assertEqual(expected, groups[mid("b3")])
        self.assertEqual(mid("solo"), groups[mid("solo")])

    def test_diagnostics_carry_the_numbers_the_greedy_scored_with(self):
        diagnostics = self.selection.diagnostics
        self.assertEqual(set(self.selection.groups), set(diagnostics))
        for media_id, entry in diagnostics.items():
            for key in (
                "standing", "quality", "worse_version_in_group",
                "smile_pct", "awake_pct", "aesthetic_pct", "composed_pct",
            ):
                self.assertIn(key, entry, f"{key} missing for {media_id}")
            self.assertTrue(0.0 < entry["standing"] <= 1.0)
        self.assertEqual(0.90, diagnostics[mid("b1")]["quality"])

    def test_the_dominated_burst_frame_is_flagged_and_the_winner_is_not(self):
        # b2 loses to b1 on awake by more than the perceptual margin and wins
        # on nothing, so it is a worse version of an existing photograph.
        self.assertTrue(self.selection.diagnostics[mid("b2")]["worse_version_in_group"])
        self.assertFalse(self.selection.diagnostics[mid("b1")]["worse_version_in_group"])

    def test_the_contract_report_did_not_grow(self):
        # The sidecar exists so the AlbumSpec's embedded report does not have
        # to. A new key here would be a silent contract change.
        report = self.selection.to_selection_report()
        self.assertEqual(
            {"candidate_count", "selected_count", "diversity_constraints", "rejected"},
            set(report),
        )


if __name__ == "__main__":
    unittest.main()
