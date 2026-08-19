"""Burst photos are ONE photograph -- the album gets one frame of it.

THE FAILURE THIS FILE EXISTS TO PREVENT

A real 281-photo family library produced a 24-page album containing eleven
frames of one 26-second burst, four of another, and six of a third: four
distinct photographs, twenty-four pages. Two causes, both proven on that
library:

  1. The redundancy penalty's fixed free zone (0.60) sat BELOW the library's
     own baseline similarity (0.744 median between photos taken days apart --
     one baby, one home, one camera). Every candidate paid roughly the same
     penalty, so the penalty stopped discriminating and quality differences
     between burst frames outvoted it.
  2. There was no hard notion of "the same photograph taken repeatedly":
     1-second-apart frames at 0.98 similarity competed as independent photos.

So: shot grouping (time-adjacent AND similarity >= shot_similarity, transitive)
with a hard one-per-shot cap that relaxes only when every distinct shot is
already in the album; and a redundancy free zone calibrated to the library's
measured similarity, never below the policy floor.
"""

from __future__ import annotations

import random
import unittest
from datetime import datetime, timedelta, timezone

from memory_engine_album.selection import (
    SelectionPolicy,
    _calibrated_redundancy,
    _shot_groups,
    select,
)

from test_selection import cand, mid


def at_s(day: int, hour: int, second: int = 0) -> str:
    base = datetime(2026, 3, 1, tzinfo=timezone.utc)
    return (base + timedelta(days=day - 1, hours=hour, seconds=second)).isoformat()


def unit(*components: float) -> tuple[float, ...]:
    norm = sum(c * c for c in components) ** 0.5
    return tuple(c / norm for c in components)


SPACE = "test_space_4"


def burst_frame(tag: str, second: int, *, value: float, day: int = 1) -> object:
    """One frame of the same photograph: embeddings ~0.998 similar."""
    return cand(
        tag,
        value,
        captured_utc=at_s(day, 12, second),
        embedding=unit(1.0, 0.0, 0.0, 0.05 * (second % 3)),
        embedding_space=SPACE,
    )


def distinct(tag: str, day: int, axis: int, *, value: float = 0.7) -> object:
    """A genuinely different photograph on its own day: orthogonal embedding."""
    components = [0.0, 0.0, 0.0, 0.0]
    components[axis] = 1.0
    return cand(
        tag,
        value,
        captured_utc=at_s(day, 15),
        embedding=unit(*components),
        embedding_space=SPACE,
    )


class TestShotCap(unittest.TestCase):
    def test_a_burst_contributes_one_frame_while_distinct_shots_remain(self):
        """CATCHES: burst frames out-scoring distinct photos on quality.

        The burst frames carry the HIGHEST quality in the pool -- that is the
        realistic case (the photogenic moment got burst-shot) and the one a
        soft penalty loses.
        """
        burst = [burst_frame(f"burst{i}", i, value=0.95) for i in range(8)]
        others = [distinct(f"day{d}", d, axis) for d, axis in ((2, 1), (3, 2), (4, 3))]
        selection = select(burst + others, 4)

        chosen = set(selection.selected)
        self.assertEqual(4, len(chosen))
        burst_chosen = chosen & {c.media_id for c in burst}
        self.assertEqual(
            1, len(burst_chosen),
            f"{len(burst_chosen)} frames of the same burst were selected; a "
            "burst is one photograph and the album has distinct shots to spend "
            "its pages on",
        )
        for other in others:
            self.assertIn(other.media_id, chosen)

    def test_relaxation_deepens_every_shot_rather_than_stopping_short(self):
        """CATCHES: the cap starving the album when shots < pages.

        Two shots, six pages: the album must come back with six photos --
        three frames of each shot -- not two photos and an apology. The
        deepening must be even, not six frames of the better burst.
        """
        burst_a = [burst_frame(f"a{i}", i, value=0.9) for i in range(5)]
        burst_b = [
            cand(
                f"b{i}",
                0.8,
                captured_utc=at_s(2, 12, i),
                embedding=unit(0.0, 1.0, 0.0, 0.05 * (i % 3)),
                embedding_space=SPACE,
            )
            for i in range(5)
        ]
        selection = select(burst_a + burst_b, 6)

        self.assertEqual(6, len(selection.selected))
        a_count = len(set(selection.selected) & {c.media_id for c in burst_a})
        self.assertEqual(
            3, a_count,
            "relaxation must deepen shot by shot (3 + 3), not refill from the "
            "higher-quality burst first",
        )

    def test_shot_grouping_is_transitive_and_respects_the_time_window(self):
        frames = [burst_frame(f"f{i}", i * 60, value=0.8) for i in range(4)]
        far_away = burst_frame("far", 4 * 3600, value=0.8)  # same look, hours later
        groups = _shot_groups(frames + [far_away], SelectionPolicy())
        self.assertEqual(
            1, len({groups[c.media_id] for c in frames}),
            "adjacent frames chain transitively into one shot",
        )
        self.assertNotEqual(
            groups[frames[0].media_id], groups[far_away.media_id],
            "the same composition hours apart is a re-visit, not a burst",
        )

    def test_no_embedding_or_no_time_is_a_singleton_not_a_merge(self):
        no_embedding = cand("plain", 0.8, captured_utc=at_s(1, 12, 1))
        no_time = cand(
            "notime", 0.8, embedding=unit(1.0, 0.0, 0.0, 0.0), embedding_space=SPACE
        )
        groups = _shot_groups(
            [burst_frame("b0", 0, value=0.8), no_embedding, no_time],
            SelectionPolicy(),
        )
        self.assertEqual(3, len(set(groups.values())))

    def test_selection_is_deterministic_under_input_shuffle(self):
        pool = (
            [burst_frame(f"burst{i}", i, value=0.95) for i in range(6)]
            + [distinct(f"day{d}", d, axis) for d, axis in ((2, 1), (3, 2), (4, 3))]
        )
        baseline = select(pool, 5)
        for seed in range(3):
            shuffled = list(pool)
            random.Random(seed).shuffle(shuffled)
            self.assertEqual(baseline, select(shuffled, 5))


class TestCalibratedRedundancy(unittest.TestCase):
    def test_homogeneous_library_raises_the_free_zone(self):
        """CATCHES: the fixed 0.60 free zone penalising everything equally.

        Every candidate here shares a dominant component (~0.89 pairwise), the
        one-baby one-home case. The free zone must move up to the measured
        baseline so only ABOVE-baseline similarity is penalised.
        """
        pool = [
            cand(
                f"h{i}",
                0.8,
                captured_utc=at_s(1 + i, 12),
                embedding=unit(3.0, (i % 4 == 1) * 1.0, (i % 4 == 2) * 1.0, (i % 4 == 3) * 1.0),
                embedding_space=SPACE,
            )
            for i in range(16)
        ]
        free, denominator = _calibrated_redundancy(pool, SelectionPolicy())
        self.assertGreater(free, 0.8, f"free zone {free} did not track the 0.9 baseline")
        self.assertGreater(denominator, 0.0)

    def test_varied_library_keeps_the_policy_floor(self):
        pool = [
            distinct(f"v{i}", 1 + i, i % 4) for i in range(16)
        ]
        free, _ = _calibrated_redundancy(pool, SelectionPolicy())
        self.assertEqual(
            SelectionPolicy().redundancy_free_similarity, free,
            "a genuinely varied library must not have its free zone dragged up",
        )

    def test_fewer_than_eight_embeddings_is_no_measurement(self):
        pool = [distinct(f"s{i}", 1 + i, i % 4) for i in range(4)]
        free, _ = _calibrated_redundancy(pool, SelectionPolicy())
        self.assertEqual(SelectionPolicy().redundancy_free_similarity, free)


if __name__ == "__main__":
    unittest.main()
