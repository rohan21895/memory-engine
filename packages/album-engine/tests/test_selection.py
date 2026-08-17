"""Tests for album photo selection.

WHAT THESE TESTS ARE FOR

This codebase has been through three rounds of adversarial review that found 21
defects, every one of them SILENT: a plausible number, no exception, a wrong
answer. So the bar here is not "the function runs". Each test below was
verified to BITE -- the implementation was broken on purpose and the test was
confirmed to fail -- and the specific mutation each one catches is named in its
docstring. A test that passes against both the correct and the broken code is
worse than no test, because it certifies the bug.

Runs under `python3 tests/test_selection.py`, `python3 -m unittest` and pytest.
No runner-specific features, matching the convention in contracts/tests.
"""

from __future__ import annotations

import dataclasses
import json
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
ALBUM_PKG = REPO / "packages" / "album-engine"
RANKING_PKG = REPO / "packages" / "ranking-engine"
SCHEMA_DIR = REPO / "contracts" / "schemas"

# Both packages are plain source trees rather than installed distributions, so
# the path is set up here. `memory_engine_ranking.fusion` is on main; the
# scaffolding that used to materialise it from an unmerged branch is gone.
sys.path.insert(0, str(ALBUM_PKG))
sys.path.insert(0, str(RANKING_PKG))

from memory_engine_album import selection as selection_module  # noqa: E402
from memory_engine_album.selection import (  # noqa: E402
    DEFAULT_POLICY,
    Rejection,
    Selection,
    SelectionCandidate,
    SelectionError,
    SelectionPolicy,
    candidate_from_media_record,
    geo_cell,
    select,
)
from memory_engine_ranking.fusion import (  # noqa: E402
    FaceState,
    FusedScore,
    Signals,
    Weights,
    fuse,
    signals_from_media_record,
)

WEIGHTS = Weights()
DIGEST = WEIGHTS.digest()

# 64 lowercase hex chars, so ids are contract-shaped Blake3Hash values and the
# emitted SelectionReport can be validated against the real schema.
def mid(tag: str) -> str:
    body = tag.encode("utf-8").hex()
    return (body + "0" * 64)[:64]


PERSON = {
    name: f"{i:08x}-0000-4000-8000-000000000000"
    for i, name in enumerate(["ava", "bo", "cy", "dee", "gran"], start=1)
}


def score(
    value: float,
    *,
    measured: tuple[str, ...] = ("exposure", "sharpness"),
    coverage: float = 1.0,
    rejected: bool = False,
    reason: str | None = None,
    digest: str = DIGEST,
    feature_set: str = "photo-quality-v1",
) -> FusedScore:
    """A FusedScore built directly, so a test can pin one number.

    `contributions` carries (name, value, share) triples; only the names feed
    `measured`, which is what the comparability partition keys on.
    """
    return FusedScore(
        value=value,
        coverage=coverage,
        rejected=rejected,
        rejection_reason=reason,
        weights_id="default-v1",
        weights_digest=digest,
        feature_set_id=feature_set,
        contributions=tuple((name, value, 1.0 / len(measured)) for name in sorted(measured)),
    )


def cand(
    tag: str, value: float = 0.8, *, rejected_score: bool = False, **kwargs
) -> SelectionCandidate:
    """A candidate with a score pinned to `value`.

    `rejected_score=True` is the eliminated-frame shorthand: fusion returns
    value 0.0 with `rejected=True`, which is a different thing from a score of
    0.0 and the distinction is what the elimination tests turn on.
    """
    if rejected_score:
        kwargs.setdefault("score", score(0.0, rejected=True, reason="black_frame"))
    kwargs.setdefault("score", score(value))
    return SelectionCandidate(media_id=mid(tag), **kwargs)


def at(day: int, hour: int = 12) -> str:
    """A contract Timestamp on a fixed March 2026 trip.

    Built with timedelta rather than string interpolation so a test that says
    `at(3, 30)` gets a real instant on the next day instead of the string
    '2026-03-03T30:00:00', which is not a time and would fail parsing rather
    than testing what the test meant to test.
    """
    base = datetime(2026, 3, 1, tzinfo=timezone.utc)
    return (base + timedelta(days=day - 1, hours=hour)).isoformat()


class TestDeterminism(unittest.TestCase):
    def test_same_input_same_album_regardless_of_argument_order(self):
        """CATCHES: any reliance on input order, dict order or set iteration --
        specifically `select` no longer sorting candidates by media_id before it
        iterates.

        The WHOLE `Selection` is compared, not just `selected`. Comparing only
        the chosen ids does not bite: `selected` is re-sorted chronologically on
        the way out and every tiebreak inside the greedy is explicit on
        media_id, so dropping the input sort leaves the album identical and
        silently reorders `unselected` and `rejected` -- the two fields the
        report's "why is my photo missing" answer is assembled from, and the two
        a caller is most likely to diff between runs.
        """
        # Two measurement classes, because `Selection.classes` is ordered by a
        # stringified key rather than by dict insertion and that is only visible
        # when there is more than one class to order.
        pool = [
            cand(f"p{i}", value=0.5 + (i % 7) / 20, captured_utc=at(1 + i % 5, i % 24),
                 score=score(0.5 + (i % 7) / 20,
                             measured=("sharpness", "exposure", "noise") if i % 4
                             else ("sharpness", "exposure")),
                 scene_type=["portrait", "landscape", "food"][i % 3],
                 place_key=f"cell-{i % 4}")
            for i in range(40)
        ]
        first = select(pool, 8)
        self.assertEqual(len(first.classes), 2)
        self.assertGreater(len(first.unselected), 1, "pool must leave losers to reorder")
        for rotation in (7, 13, 29):
            rotated = pool[rotation:] + pool[:rotation]
            self.assertEqual(select(rotated, 8), first)
        self.assertEqual(select(list(reversed(pool)), 8), first)

    def test_repeated_runs_are_bit_identical(self):
        pool = [
            cand(f"q{i}", value=0.4 + i / 100, captured_utc=at(1 + i % 3),
                 person_ids=(PERSON["ava"],) if i % 2 else ())
            for i in range(30)
        ]
        runs = {select(pool, 10).selected for _ in range(5)}
        self.assertEqual(len(runs), 1)


class TestDiversityBeatsNaiveTopN(unittest.TestCase):
    def test_forty_sunsets_do_not_win(self):
        """The headline requirement: quality-only selection returns one scene.

        CATCHES: dropping the coverage terms from the objective, or letting the
        quality weight dominate them. Twenty photos of the sunset all outscore
        every other photo in the event; a top-N-by-score selector returns eight
        sunsets and this asserts it does not.
        """
        sunsets = [
            cand(f"sunset{i:02d}", value=0.95, scene_type="landscape",
                 place_key="cliff", captured_utc=at(1, 18))
            for i in range(20)
        ]
        rest = [
            cand(f"other{i:02d}", value=0.62, scene_type=["food", "portrait", "indoor"][i % 3],
                 place_key=f"town-{i % 5}", captured_utc=at(2 + i % 4, 9 + i))
            for i in range(20)
        ]
        result = select(sunsets + rest, 8)
        chosen = set(result.selected)
        sunset_ids = {c.media_id for c in sunsets}
        picked_sunsets = len(chosen & sunset_ids)

        self.assertGreaterEqual(picked_sunsets, 1, "the best scene should still appear")
        self.assertLessEqual(
            picked_sunsets, 3,
            f"{picked_sunsets}/8 photos are the same sunset at the same place and hour",
        )

    def test_embedding_redundancy_penalises_visual_near_twins(self):
        """CATCHES: the MMR redundancy term being dropped or its sign flipped.

        These are not dedupe near-duplicates -- different pHashes, different
        moments -- but they point at the same thing, which is what an embedding
        sees and a hash does not.
        """
        twins = [
            cand(f"twin{i}", value=0.9, embedding=(1.0, 0.02 * i, 0.0),
                 captured_utc=at(1, 10 + i), place_key=f"p{i}",
                 scene_type=["landscape", "outdoor", "night"][i % 3])
            for i in range(6)
        ]
        varied = [
            cand(f"var{i}", value=0.75, embedding=(0.0, 0.0, 1.0) if i % 2 else (0.0, 1.0, 0.0),
                 captured_utc=at(2, 10 + i), place_key=f"v{i}",
                 scene_type=["food", "indoor", "portrait"][i % 3])
            for i in range(6)
        ]
        result = select(twins + varied, 4)
        picked_twins = len(set(result.selected) & {c.media_id for c in twins})
        self.assertLessEqual(picked_twins, 2, "redundancy penalty is not biting")

    def test_redundancy_is_measured_against_every_pick_not_just_the_last(self):
        """CATCHES: the running similarity cache degrading into 'similar to the
        most recent pick'.

        The penalty is a max over the WHOLE selected set, maintained
        incrementally for speed. If that cache is ever overwritten instead of
        maxed, a photo that duplicates the FIRST pick sails through as long as
        something unrelated was chosen in between -- and it is the first pick,
        the best photo in the album, that attracts the duplicates.

        `c` is a near-twin of `a` and unrelated to `b`. Greedy takes a, then b.
        On the third pick `c` outscores `d` on quality and must still lose to
        it, on the strength of a similarity recorded two rounds earlier.
        """
        same_moment = at(1, 9)
        pool = [
            cand("a_first", value=0.99, embedding=(1.0, 0.0, 0.0), captured_utc=same_moment),
            cand("b_other", value=0.98, embedding=(0.0, 1.0, 0.0), captured_utc=same_moment),
            cand("c_twin_of_a", value=0.97, embedding=(0.98, 0.2, 0.0), captured_utc=same_moment),
            cand("d_novel", value=0.90, embedding=(0.0, 0.0, 1.0), captured_utc=same_moment),
        ]
        result = select(pool, 3)
        self.assertEqual(
            set(result.selected), {mid("a_first"), mid("b_other"), mid("d_novel")}
        )


class TestTemporalSpread(unittest.TestCase):
    def test_day_one_does_not_take_the_album(self):
        """CATCHES: the time bucket collapsing (e.g. a constant key), which
        makes the temporal term a no-op and hands the album to the day with the
        most photos.

        Day one has 30 photos and every other day has 4, and day one's photos
        are slightly better -- exactly the real pattern, since that is when
        everyone is still enthusiastic.
        """
        day_one = [cand(f"d1_{i:02d}", value=0.85, captured_utc=at(1, i % 24)) for i in range(30)]
        later = [
            cand(f"d{d}_{i}", value=0.80, captured_utc=at(d, 9 + i * 3))
            for d in (2, 3, 4, 5) for i in range(4)
        ]
        pool = day_one + later
        day_of = {c.media_id: c.captured_utc[8:10] for c in pool}

        result = select(pool, 10)
        days = {day_of[m] for m in result.selected}
        self.assertGreaterEqual(len(days), 4, f"only {len(days)} distinct days represented")

    def test_unknown_capture_time_is_its_own_bucket_not_day_one(self):
        """CATCHES: sorting missing dates to the epoch.

        Undated photos binned as bin zero make the first morning look covered,
        and sorted to the epoch they open the album. Both are silent. Here the
        undated photos are the best in the pool, so a broken implementation
        puts them first in `selected`.
        """
        undated = [cand(f"u{i}", value=0.95) for i in range(3)]
        dated = [cand(f"d{i}", value=0.70, captured_utc=at(1 + i, 10)) for i in range(5)]
        result = select(undated + dated, 6)

        undated_ids = {c.media_id for c in undated}
        positions = [i for i, m in enumerate(result.selected) if m in undated_ids]
        dated_positions = [i for i, m in enumerate(result.selected) if m not in undated_ids]
        self.assertTrue(
            all(p > max(dated_positions) for p in positions),
            "undated photos must sort last, not to the epoch",
        )

    def test_undated_photos_do_not_make_day_one_look_covered(self):
        """CATCHES: `_time_bucket` folding an unknown capture time into bin
        zero, which is what sorting missing dates to the epoch amounts to on the
        coverage axis.

        The existing undated test only checks where they LAND in the album, and
        it passes with the buckets collapsed. This one checks what they COST: if
        the undated photos occupy day one's bucket then day one is already
        covered, its photos are discounted, and the first morning of the trip
        disappears from the book -- while the report still shows a nicely spread
        timeline.
        """
        pool = (
            [cand(f"una{i}", value=0.90) for i in range(3)]
            + [cand(f"one{i}", value=0.85, captured_utc=at(1, 9 + i)) for i in range(3)]
            + [cand(f"two{i}", value=0.85, captured_utc=at(2, 9 + i)) for i in range(3)]
        )
        selected = set(select(pool, 3).selected)
        for prefix in ("una", "one", "two"):
            self.assertEqual(
                len(selected & {mid(f"{prefix}{i}") for i in range(3)}), 1,
                f"expected exactly one photo from the {prefix!r} group",
            )

    def test_last_photo_of_the_event_does_not_get_a_private_bin(self):
        """CATCHES: the classic off-by-one where t == end computes bin index
        `bins` instead of `bins - 1`.

        With the bug the final photo is alone in a bucket no other photo can
        share, so it wins a full novelty bonus and is always selected however
        poor it is. Here it is the worst photo in the event.
        """
        pool = [cand(f"e{i:02d}", value=0.9, captured_utc=at(1, i)) for i in range(12)]
        pool.append(cand("last", value=0.36, captured_utc=at(1, 12)))
        result = select(pool, 3)
        self.assertNotIn(mid("last"), result.selected)


class TestPeopleCoverage(unittest.TestCase):
    def test_every_person_appears(self):
        """The catastrophic failure: a family album that omits one child."""
        pool = [
            cand(f"ava{i}", value=0.95, person_ids=(PERSON["ava"],), captured_utc=at(1, i))
            for i in range(15)
        ] + [
            cand("bo1", value=0.55, person_ids=(PERSON["bo"],), captured_utc=at(2)),
            cand("cy1", value=0.52, person_ids=(PERSON["cy"],), captured_utc=at(3)),
            cand("dee1", value=0.51, person_ids=(PERSON["dee"],), captured_utc=at(4)),
        ]
        result = select(pool, 6)
        for name in ("ava", "bo", "cy", "dee"):
            self.assertGreaterEqual(
                result.person_counts.get(PERSON[name], 0), 1, f"{name} is missing"
            )
        self.assertEqual(result.missing_person_ids, ())

    def test_group_shot_covers_several_people_at_once(self):
        """CATCHES: a per-person loop instead of greedy maximum coverage.

        One group photo covers three people; three separate portraits would
        spend three slots to do the same. With N=2 only the coverage-aware
        version fits everybody in.
        """
        pool = [
            cand("group", value=0.60,
                 person_ids=tuple(sorted((PERSON["ava"], PERSON["bo"], PERSON["cy"]))),
                 captured_utc=at(1)),
            cand("solo_ava", value=0.99, person_ids=(PERSON["ava"],), captured_utc=at(2)),
            cand("solo_bo", value=0.98, person_ids=(PERSON["bo"],), captured_utc=at(3)),
            cand("solo_cy", value=0.97, person_ids=(PERSON["cy"],), captured_utc=at(4)),
        ]
        result = select(pool, 2)
        self.assertIn(mid("group"), result.selected)
        self.assertEqual(result.missing_person_ids, ())

    def test_uncoverable_person_is_reported_not_swallowed(self):
        """CATCHES: reporting a satisfied constraint the algorithm never met.

        Four people, room for two photos, no group shots: coverage is
        impossible. The requirement is not that it succeeds, it is that it says
        so -- in `missing_person_ids` and in the constraint report.
        """
        pool = [
            cand(f"solo_{n}", value=0.9, person_ids=(PERSON[n],), captured_utc=at(i + 1))
            for i, n in enumerate(["ava", "bo", "cy", "dee"])
        ]
        result = select(pool, 2)
        self.assertEqual(len(result.selected), 2)
        self.assertEqual(len(result.missing_person_ids), 2)

        entry = constraint(result, "max_per_person_per_section")
        self.assertFalse(entry.satisfied)
        self.assertIn("NOT COVERED", entry.detail)

    def test_required_person_absent_from_the_event_is_named(self):
        """A person the album is *about* who has no confirmed photo at all must
        surface, not vanish. CATCHES: deriving the person set only from the
        candidates and ignoring `required_person_ids`."""
        pool = [cand(f"x{i}", value=0.8, person_ids=(PERSON["ava"],), captured_utc=at(i + 1))
                for i in range(4)]
        result = select(pool, 3, required_person_ids=[PERSON["ava"], PERSON["gran"]])
        self.assertEqual(result.missing_person_ids, (PERSON["gran"],))

    def test_one_person_event_is_not_capped_in_half(self):
        """CATCHES: applying the per-person cap unconditionally.

        An album of one grandchild is legitimately all the grandchild. A blanket
        cap of 50% returns a half-length album and reports diversity success.
        """
        pool = [cand(f"solo{i:02d}", value=0.8, person_ids=(PERSON["ava"],),
                     captured_utc=at(1 + i % 5, i)) for i in range(20)]
        result = select(pool, 10)
        self.assertEqual(len(result.selected), 10)

    def test_one_photogenic_person_cannot_take_the_whole_album(self):
        """The mirror-image failure to the person floor."""
        pool = [
            cand(f"ava{i:02d}", value=0.99, person_ids=(PERSON["ava"],), captured_utc=at(1, i))
            for i in range(20)
        ] + [
            cand(f"bo{i:02d}", value=0.70, person_ids=(PERSON["bo"],), captured_utc=at(2, i))
            for i in range(20)
        ]
        result = select(pool, 10)
        self.assertLessEqual(result.person_counts[PERSON["ava"]], 5)
        self.assertTrue(constraint(result, "max_per_person_per_section").satisfied)

    def test_people_and_scenery_are_balanced(self):
        pool = [
            cand(f"face{i:02d}", value=0.9, person_ids=(PERSON["ava"],), captured_utc=at(1, i))
            for i in range(20)
        ] + [
            cand(f"scene{i:02d}", value=0.55, scene_type="landscape", captured_utc=at(2, i))
            for i in range(10)
        ]
        result = select(pool, 10)
        non_people = sum(1 for m in result.selected if m.startswith(mid("scene")[:4]))
        self.assertTrue(constraint(result, "people_scenery_detail_balance").satisfied)
        self.assertGreaterEqual(
            sum(1 for m in result.selected if m in {c.media_id for c in pool if not c.person_ids}),
            1,
        )


class TestScarcitySurvivesQualityFirstCut(unittest.TestCase):
    def test_only_photo_of_grandmother_survives_the_floor(self):
        """The irreplaceable-but-poor case.

        CATCHES: applying the quality floor before knowing who is scarce. A
        0.12 photo of a person with nothing better must survive; a 0.12 photo
        of nobody must not.
        """
        pool = [
            cand(f"good{i}", value=0.9, person_ids=(PERSON["ava"],), captured_utc=at(1, i))
            for i in range(6)
        ] + [
            cand("gran_only", value=0.12, person_ids=(PERSON["gran"],), captured_utc=at(2)),
            cand("junk", value=0.12, captured_utc=at(3)),
        ]
        result = select(pool, 5)

        self.assertIn(mid("gran_only"), result.selected)
        self.assertIn(mid("gran_only"), result.rescued_media_ids)
        self.assertIn(mid("junk"), [r.media_id for r in result.rejected])
        self.assertEqual(
            reason_for(result, mid("junk")), "below_quality_floor"
        )

    def test_scarcity_rescues_one_photo_not_a_run_of_them(self):
        """A bad run of photos of a scarce person must not fill the book."""
        pool = [
            cand(f"gran{i}", value=0.10 + i / 1000, person_ids=(PERSON["gran"],),
                 captured_utc=at(1, i))
            for i in range(8)
        ] + [
            cand(f"good{i}", value=0.9, person_ids=(PERSON["ava"],), captured_utc=at(2, i))
            for i in range(8)
        ]
        result = select(pool, 6)
        self.assertEqual(len(result.rescued_media_ids), 1)
        gran_photos = {c.media_id for c in pool if c.media_id.startswith(mid("gran")[:4])}
        self.assertEqual(len(set(result.selected) & gran_photos), 1)

    def test_elimination_is_never_rescued(self):
        """CATCHES: waiving fusion's elimination alongside the quality floor.

        A black frame containing grandmother is still a black frame -- there is
        no image there. Elimination and the quality floor are different
        judgements and only one of them bends to scarcity.
        """
        pool = [
            cand("gran_black", value=0.0, rejected_score=True,
                 person_ids=(PERSON["gran"],), captured_utc=at(1)),
            cand(f"good1", value=0.9, person_ids=(PERSON["ava"],), captured_utc=at(2)),
            cand(f"good2", value=0.85, person_ids=(PERSON["ava"],), captured_utc=at(3)),
        ]
        result = select(pool, 3)
        self.assertNotIn(mid("gran_black"), result.selected)
        self.assertNotIn(mid("gran_black"), result.rescued_media_ids)
        self.assertEqual(reason_for(result, mid("gran_black")), "below_quality_floor")
        self.assertIn(PERSON["gran"], result.missing_person_ids)


class TestDedupeInteraction(unittest.TestCase):
    def test_two_members_of_one_duplicate_group_are_never_both_selected(self):
        group = "11111111-1111-4111-8111-111111111111"
        pool = [
            cand("burst_primary", value=0.9, dedupe_group_id=group, is_dedupe_primary=True,
                 captured_utc=at(1, 10)),
        ] + [
            cand(f"burst_sec{i}", value=0.95, dedupe_group_id=group, is_dedupe_primary=False,
                 captured_utc=at(1, 10))
            for i in range(5)
        ] + [
            cand(f"other{i}", value=0.6, captured_utc=at(2, i)) for i in range(5)
        ]
        result = select(pool, 6)
        selected_groups = [
            c.dedupe_group_id for c in pool
            if c.media_id in set(result.selected) and c.dedupe_group_id
        ]
        self.assertEqual(len(selected_groups), len(set(selected_groups)))
        self.assertIn(mid("burst_primary"), result.selected)
        self.assertTrue(constraint(result, "no_near_duplicates_on_spread").satisfied)
        for i in range(5):
            self.assertEqual(reason_for(result, mid(f"burst_sec{i}")), "near_duplicate")

    def test_ungrouped_photo_is_its_own_primary(self):
        """CATCHES: defaulting `is_primary` to False for records that were
        never deduped, which drops every ungrouped photo in the library."""
        record = base_record("solo")
        candidate = candidate_from_media_record(record, score(0.8))
        self.assertTrue(candidate.is_dedupe_primary)
        self.assertEqual(len(select([candidate], 1).selected), 1)


class TestComparability(unittest.TestCase):
    def test_portrait_and_landscape_are_ranked_within_their_own_class(self):
        """The comparability rule applied where it matters.

        A landscape is scored on seven signals and a portrait on eight, so
        `fusion.rank` refuses to order them. Selection must still build an
        album containing both -- by comparing standing, not value.
        """
        portraits = [
            cand(f"por{i}", value=0.90,
                 score=score(0.90, measured=("sharpness", "exposure", "face_quality")),
                 person_ids=(PERSON["ava"],), captured_utc=at(1, i))
            for i in range(6)
        ]
        landscapes = [
            cand(f"lan{i}", value=0.55,
                 score=score(0.55, measured=("sharpness", "exposure")),
                 scene_type="landscape", captured_utc=at(2, i))
            for i in range(6)
        ]
        result = select(portraits + landscapes, 6)
        self.assertEqual(len(result.classes), 2)
        self.assertEqual(len(result.selected), 6)
        # Both kinds present: raw-value ranking would have taken six portraits.
        picked_landscapes = len(set(result.selected) & {c.media_id for c in landscapes})
        self.assertGreaterEqual(picked_landscapes, 1)

    def test_a_class_of_one_does_not_score_a_perfect_standing(self):
        """CATCHES: raw within-class standing with no small-class shrink.

        One photo measured differently from everything else is 'best of its
        kind' by default. Un-shrunk it scores 1.0 and always makes the album.
        Here the oddball is mediocre and there is stiff competition.
        """
        crowd = [
            cand(f"c{i:02d}", value=0.90, captured_utc=at(1, i), place_key=f"pl{i}",
                 scene_type=["food", "indoor", "portrait"][i % 3])
            for i in range(10)
        ]
        oddball = cand(
            "odd", value=0.40,
            score=score(0.40, measured=("sharpness", "exposure", "noise")),
            captured_utc=at(1, 3), place_key="pl3", scene_type="food",
        )
        result = select(crowd + [oddball], 3)
        self.assertNotIn(mid("odd"), result.selected)

    def test_class_key_and_comparable_with_cannot_drift_apart(self):
        """CATCHES: the partition key becoming coarser than the real rule.

        Two scores with the same signal set but different weight digests are
        NOT comparable. If the key ever stops including the digest, the two
        land in one class and get ranked against each other with no error --
        the silent failure this whole module is defensive about.
        """
        a = cand("wa", value=0.9, score=score(0.9, digest="a" * 32), captured_utc=at(1))
        b = cand("wb", value=0.8, score=score(0.8, digest="b" * 32), captured_utc=at(2))
        self.assertFalse(a.score.comparable_with(b.score))
        result = select([a, b], 2)
        self.assertEqual(len(result.classes), 2)

        c = cand("fc", value=0.9, score=score(0.9, feature_set="photo-quality-v2"),
                 captured_utc=at(3))
        d = cand("fd", value=0.8, score=score(0.8), captured_utc=at(4))
        self.assertEqual(len(select([c, d], 2).classes), 2)

    def test_allow_mixed_is_opt_in(self):
        """Preview mode exists but must be asked for."""
        mixed = [
            cand("m1", value=0.9, score=score(0.9, measured=("sharpness", "exposure"))),
            cand("m2", value=0.8, score=score(0.8, measured=("sharpness",))),
        ]
        strict = select(mixed, 2)
        self.assertEqual(len(strict.classes), 2)
        loose = select(mixed, 2, allow_mixed_measurements=True)
        self.assertEqual(loose.classes, ())
        self.assertEqual(len(loose.selected), 2)


class TestHardGates(unittest.TestCase):
    def test_armed_pixel_gate_rejects_unknown_dimensions(self):
        """CATCHES the review's own example: a gate that needs two non-null
        values to fire, so an unverified input passes it.

        With `min_long_edge_px` set, a candidate whose pixel size is unknown is
        NOT print-verified and must be rejected, not admitted.
        """
        policy = SelectionPolicy(min_long_edge_px=3000)
        pool = [
            cand("unknown_px", value=0.95, captured_utc=at(1)),
            cand("small", value=0.94, long_edge_px=1200, captured_utc=at(2)),
            cand("big", value=0.60, long_edge_px=4000, captured_utc=at(3)),
        ]
        result = select(pool, 3, policy=policy)
        self.assertEqual(result.selected, (mid("big"),))
        self.assertEqual(reason_for(result, mid("unknown_px")), "dpi_too_low")
        self.assertEqual(reason_for(result, mid("small")), "dpi_too_low")

    def test_disarmed_pixel_gate_admits_unknown_dimensions(self):
        pool = [cand("unknown_px", value=0.95, captured_utc=at(1))]
        self.assertEqual(len(select(pool, 1).selected), 1)

    def test_user_hidden_and_forced_out_and_forced_in(self):
        pool = [
            cand("hidden", value=0.99, user_hidden=True, captured_utc=at(1)),
            cand("forced_out", value=0.99, user_override=False, captured_utc=at(2)),
            cand("forced_in", value=0.99, excluded_from_automation=True,
                 exclusion_reasons=("screenshot",), user_override=True, captured_utc=at(3)),
            cand("excluded", value=0.99, excluded_from_automation=True,
                 exclusion_reasons=("nsfw",), captured_utc=at(4)),
            cand("plain", value=0.5, captured_utc=at(5)),
        ]
        result = select(pool, 5)
        self.assertEqual(set(result.selected), {mid("forced_in"), mid("plain")})
        self.assertEqual(reason_for(result, mid("hidden")), "user_hidden")
        self.assertEqual(reason_for(result, mid("forced_out")), "user_hidden")
        self.assertEqual(reason_for(result, mid("excluded")), "excluded_content")

    def test_a_photo_is_rejected_for_exactly_one_reason(self):
        """CATCHES: gates that fall through and record a media_id twice, which
        breaks the report's own count arithmetic."""
        pool = [
            cand("everything_wrong", value=0.01, user_hidden=True,
                 excluded_from_automation=True, exclusion_reasons=("nsfw", "corrupt"),
                 dedupe_group_id="22222222-2222-4222-8222-222222222222",
                 is_dedupe_primary=False, long_edge_px=10, captured_utc=at(1)),
            cand("fine", value=0.9, long_edge_px=5000, captured_utc=at(2)),
        ]
        result = select(pool, 2, policy=SelectionPolicy(min_long_edge_px=3000))
        ids = [r.media_id for r in result.rejected]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(reason_for(result, mid("everything_wrong")), "user_hidden")


class TestInputValidation(unittest.TestCase):
    def test_nan_score_is_refused(self):
        """CATCHES the review's own example: NaN passing an elimination
        threshold because `NaN < x` is False.

        A NaN score would clear the quality floor untouched and then make every
        greedy comparison meaningless.
        """
        with self.assertRaises(SelectionError) as ctx:
            select([cand("nan", value=float("nan"), score=score(float("nan")))], 1)
        self.assertIn("not finite", str(ctx.exception))

    def test_naive_timestamp_is_refused(self):
        """CATCHES a determinism bug invisible on a single machine: a naive
        datetime resolves against the host timezone, so the same album orders
        differently in Mumbai and California."""
        with self.assertRaises(SelectionError) as ctx:
            select([cand("naive", captured_utc="2026-03-01T12:00:00")], 1)
        self.assertIn("offset", str(ctx.exception))

    def test_mismatched_embedding_dimensions_are_refused_up_front(self):
        pool = [
            cand("e3", value=0.9, embedding=(1.0, 0.0, 0.0), captured_utc=at(1)),
            cand("e2", value=0.8, embedding=(1.0, 0.0), captured_utc=at(2)),
        ]
        with self.assertRaises(SelectionError):
            select(pool, 2)

    def test_duplicate_media_id_is_refused(self):
        with self.assertRaises(SelectionError):
            select([cand("same"), cand("same", value=0.5)], 2)

    def test_repeated_person_id_on_one_photo_is_refused(self):
        with self.assertRaises(SelectionError):
            select([cand("dbl", person_ids=(PERSON["ava"], PERSON["ava"]))], 1)

    def test_nan_policy_threshold_is_refused(self):
        with self.assertRaises(SelectionError):
            select([cand("a")], 1, policy=SelectionPolicy(quality_floor=float("nan")))

    def test_empty_and_zero_cases(self):
        self.assertEqual(select([], 10).selected, ())
        self.assertEqual(select([cand("a")], 0).selected, ())
        with self.assertRaises(SelectionError):
            select([cand("a")], -1)

    def test_asking_for_more_than_exists_returns_what_exists(self):
        pool = [cand(f"z{i}", value=0.8, captured_utc=at(i + 1)) for i in range(3)]
        self.assertEqual(len(select(pool, 40).selected), 3)


class TestReport(unittest.TestCase):
    def test_report_validates_against_the_album_spec_schema(self):
        pool = [
            cand(f"r{i:02d}", value=0.4 + i / 50, captured_utc=at(1 + i % 4, i),
                 person_ids=(PERSON["ava"],) if i % 3 == 0 else (),
                 scene_type=["landscape", "food", "portrait"][i % 3])
            for i in range(20)
        ]
        pool.append(cand("hidden", value=0.99, user_hidden=True, captured_utc=at(1)))
        result = select(pool, 6)
        report = result.to_selection_report()

        validator(  ).validate(report)
        self.assertEqual(report["candidate_count"], 21)
        self.assertEqual(report["selected_count"], 6)
        self.assertEqual(len(report["diversity_constraints"]), 6)

    def test_no_space_entries_are_opt_in_and_complete_when_asked_for(self):
        pool = [cand(f"n{i:02d}", value=0.8, captured_utc=at(1, i)) for i in range(10)]
        result = select(pool, 3)
        self.assertEqual(result.to_selection_report()["rejected"], [])
        full = result.to_selection_report(include_no_space=True)
        validator().validate(full)
        self.assertEqual(len(full["rejected"]), 7)
        self.assertEqual(len(result.unselected), 7)

    def test_constraints_are_measured_not_asserted(self):
        """CATCHES a report that says `satisfied: true` because the enforcing
        code ran rather than because the result was checked.

        Feeding a selection whose only possible outcome violates a constraint
        must flip the flag. Here every photo is a portrait, so consecutive
        scenes repeat and there is no scenery at all.
        """
        pool = [
            cand(f"p{i}", value=0.8, scene_type="portrait", person_ids=(PERSON["ava"],),
                 captured_utc=at(1, i))
            for i in range(6)
        ]
        result = select(pool, 4)
        self.assertFalse(constraint(result, "no_consecutive_same_scene").satisfied)
        self.assertTrue(constraint(result, "people_scenery_detail_balance").satisfied,
                        "no scenery exists, so the floor is 0 and it is met")
        self.assertTrue(constraint(result, "chronological_within_section").satisfied)

    def test_hero_quality_is_reported_honestly(self):
        weak = [cand(f"w{i}", value=0.40, captured_utc=at(i + 1)) for i in range(4)]
        self.assertFalse(constraint(select(weak, 3), "min_hero_quality").satisfied)
        strong = weak + [cand("hero", value=0.92, captured_utc=at(5))]
        self.assertTrue(constraint(select(strong, 3), "min_hero_quality").satisfied)


class TestContractAdapter(unittest.TestCase):
    def test_media_record_maps_through_fusion_and_into_a_candidate(self):
        """The whole path: a contract MediaRecord -> Signals -> FusedScore ->
        SelectionCandidate. CATCHES a package that references the generated
        types in prose and bypasses them in practice."""
        record = base_record("real")
        record["quality"] = {
            "sharpness": {"value": 0.81, "run_id": "r1"},
            "exposure": {"value": 0.74, "run_id": "r1"},
        }
        record["capture"]["captured_at"] = {
            "utc": "2026-03-02T09:15:00+00:00", "source": "exif", "precision": "second",
            "confidence": 0.99,
        }
        record["capture"]["gps"] = {"latitude": 12.97, "longitude": 77.59, "source": "exif_gps"}
        record["faces"] = {"face_count": 1, "face_ids": [], "confirmed_person_ids": [PERSON["ava"]]}
        record["content"] = {
            "scene_type": "portrait",
            "embedding": {"space": "siglip2_so400m", "dimensions": 3, "storage": "inline",
                          "values": [0.1, 0.2, 0.3]},
        }
        record["image"] = {
            "stored_size": {"width": 6000, "height": 4000},
            "oriented_size": {"width": 6000, "height": 4000},
        }

        signals = signals_from_media_record(record, face_state=FaceState.HAS_FACES)
        fused = fuse(signals)
        candidate = candidate_from_media_record(record, fused)

        self.assertEqual(candidate.person_ids, (PERSON["ava"],))
        self.assertEqual(candidate.long_edge_px, 6000)
        self.assertEqual(candidate.embedding, (0.1, 0.2, 0.3))
        # The VectorRef's space rides along. Dropping it is how two models'
        # embeddings end up in one selection with nothing able to detect it.
        self.assertEqual(candidate.embedding_space, "siglip2_so400m")
        self.assertEqual(candidate.scene_type, "portrait")
        self.assertIsNotNone(candidate.place_key)
        self.assertEqual(candidate.captured_utc, "2026-03-02T09:15:00+00:00")

    def test_coarse_capture_precision_is_not_placed_on_the_timeline(self):
        """CATCHES treating a 'year' precision assertion as a real instant.

        The contract is explicit: an unusable precision must be excluded from
        chronology-ordered output, not sorted to a fabricated position.
        """
        record = base_record("coarse")
        record["capture"]["captured_at"] = {
            "utc": "2019-01-01T00:00:00+00:00", "source": "filename_pattern",
            "precision": "year", "confidence": 0.3,
        }
        candidate = candidate_from_media_record(record, score(0.8))
        self.assertIsNone(candidate.captured_utc)

    def test_geo_cell_is_stable_and_does_not_fold_across_zero(self):
        """CATCHES `int()` truncation toward zero, which merges the cells
        either side of the equator and the prime meridian."""
        self.assertEqual(geo_cell(0.01, 0.01), geo_cell(0.02, 0.02))
        self.assertNotEqual(geo_cell(0.01, 0.01), geo_cell(-0.01, -0.01))
        self.assertEqual(geo_cell(12.97, 77.59), geo_cell(12.97, 77.59))


class TestCapAttribution(unittest.TestCase):
    """Why a photo is missing, attributed to the thing that actually did it.

    The report exists to answer one question. An answer that is technically a
    sentence and factually wrong is worse than "no space", because the user acts
    on it -- they go looking for a cap to raise that never fired.
    """

    def test_photos_cut_for_no_space_are_not_blamed_on_the_cap(self):
        """CATCHES the P0: attributing the cap AFTER the fill loop, by sweeping
        the leftovers for anyone sitting at their cap.

        Here Ava reaches her cap of 2 on the LAST pick of a full album. The cap
        never blocked anything -- at every round with a slot to give, Ava was
        below it -- so the two leftover photos of Ava lost to no space, and that
        is what the report must say. The after-the-fact sweep sees `ava == 2 ==
        cap` and blames the cap for both.
        """
        pool = [
            cand(f"ava{i}", value=0.9, person_ids=(PERSON["ava"],), captured_utc=at(1, i))
            for i in range(4)
        ] + [
            cand("bo1", value=0.9, person_ids=(PERSON["bo"],), captured_utc=at(2)),
            cand("scene1", value=0.9, scene_type="landscape", place_key="cliff",
                 captured_utc=at(3)),
        ]
        result = select(pool, 4)

        self.assertEqual(len(result.selected), 4, "album filled normally")
        self.assertEqual(result.person_counts[PERSON["ava"]], 2, "ava ended exactly at cap")
        blamed = [r.media_id for r in result.rejected if r.reason == "diversity_constraint"]
        self.assertEqual(
            blamed, [],
            "the cap blocked nothing: it was only reached by the final pick",
        )
        self.assertEqual(len(result.unselected), 2, "both leftovers lost to no space")

    def test_a_cap_that_actually_blocks_is_still_reported(self):
        """The other direction, so the fix above cannot be 'stop reporting'.

        With the cap at 2 Ava is blocked from round three onward while the album
        still has three slots to give, and the six photos of hers that never got
        to compete are attributed to the cap -- not to the scenery that filled
        those slots instead.
        """
        pool = [
            cand(f"ava{i}", value=0.98, person_ids=(PERSON["ava"],), captured_utc=at(1, i))
            for i in range(8)
        ] + [
            cand("bo1", value=0.98, person_ids=(PERSON["bo"],), captured_utc=at(2)),
        ] + [
            cand(f"scene{i}", value=0.5, scene_type="landscape", place_key="cliff",
                 captured_utc=at(3, i))
            for i in range(6)
        ]
        result = select(pool, 6, policy=SelectionPolicy(max_per_person_fraction=0.25))

        self.assertEqual(len(result.selected), 6)
        self.assertEqual(result.person_counts[PERSON["ava"]], 2)
        blamed = {r.media_id for r in result.rejected if r.reason == "diversity_constraint"}
        self.assertEqual(blamed, {mid(f"ava{i}") for i in range(2, 8)})
        # The scenery that lost on merit is NOT blamed on the cap.
        self.assertEqual(set(result.unselected), {mid(f"scene{i}") for i in range(3, 6)})

    def test_a_short_album_says_the_cap_did_it(self):
        """CATCHES the P1: the greedy stops because every remaining candidate is
        of a capped person, and no constraint says so.

        One photo of Bo, ten of Ava, room for four, cap of two. The album can
        only reach three, and a spec that comes back short with every constraint
        green leaves "why is my album short" answerable nowhere.
        """
        pool = [
            cand(f"ava{i}", value=0.9, person_ids=(PERSON["ava"],), captured_utc=at(1, i))
            for i in range(10)
        ] + [cand("bo1", value=0.85, person_ids=(PERSON["bo"],), captured_utc=at(2))]

        result = select(pool, 4)
        self.assertEqual(len(result.selected), 3, "cap makes a full album impossible")

        entry = constraint(result, "max_per_person_per_section")
        self.assertFalse(entry.satisfied, "a short album must not report green")
        self.assertIn("SHORT BY 1", entry.detail)
        self.assertIn("cap of 2", entry.detail)

    def test_running_out_of_candidates_is_not_blamed_on_the_cap(self):
        """The shortfall report must not fire for the ordinary case of asking
        for more photos than the event contains -- that is `no_space`, and a cap
        warning there would send the user hunting for a limit that is not the
        reason."""
        pool = [
            cand(f"ava{i}", value=0.9, person_ids=(PERSON["ava"],), captured_utc=at(1, i))
            for i in range(2)
        ]
        result = select(pool, 10)
        self.assertEqual(len(result.selected), 2)
        self.assertNotIn("SHORT BY", constraint(result, "max_per_person_per_section").detail)


class TestEmbeddingHygiene(unittest.TestCase):
    """An embedding that cannot support a cosine is refused at the door.

    Every one of these used to fail somewhere else: mid-greedy, inside the
    ranking engine, with no media_id in the message -- or not at all.
    """

    def test_non_finite_component_is_refused(self):
        """CATCHES: a NaN component silently disabling the redundancy penalty.

        `_similarity` returns NaN, `NaN > running_max` is False, so the running
        maximum is never updated and the photo becomes INVISIBLE to the
        redundancy term. No exception, no report entry -- its near-twins simply
        arrive in the album beside it.
        """
        pool = [
            cand("clean", value=0.9, embedding=(1.0, 0.0, 0.0), captured_utc=at(1)),
            cand("poison", value=0.8, embedding=(float("nan"), 0.0, 1.0), captured_utc=at(2)),
        ]
        with self.assertRaises(SelectionError) as ctx:
            select(pool, 2)
        self.assertIn("non-finite", str(ctx.exception))
        self.assertIn(mid("poison"), str(ctx.exception))

    def test_all_zero_embedding_is_refused_here_not_deep_inside_dedupe(self):
        """CATCHES: a bare ValueError raised from `dedupe.cosine_distance`
        halfway through the greedy, naming neither photo.

        `assertRaises(SelectionError)` is the whole point -- the unfixed module
        raises a plain ValueError, which is NOT a SelectionError and fails this
        test even though something was raised.
        """
        pool = [
            cand("real", value=0.9, embedding=(1.0, 0.0, 0.0), captured_utc=at(1)),
            cand("nulled", value=0.8, embedding=(0.0, 0.0, 0.0), captured_utc=at(2)),
        ]
        with self.assertRaises(SelectionError) as ctx:
            select(pool, 2)
        self.assertIn("all zeros", str(ctx.exception))

    def test_empty_embedding_is_refused(self):
        """A zero-length vector never even reaches a comparison when it is the
        only one, so nothing raised and the photo was quietly treated as
        embedded. Refused explicitly instead."""
        with self.assertRaises(SelectionError) as ctx:
            select([cand("hollow", value=0.9, embedding=(), captured_utc=at(1))], 1)
        self.assertIn("empty", str(ctx.exception))

    def test_two_vector_spaces_are_never_compared(self):
        """CATCHES: `VectorRef.space` being read nowhere.

        A library re-embedded during a model migration holds both spaces at the
        same dimension count, so the dimension check passes and the cosine
        between them is a number with no meaning. The redundancy penalty then
        fires on noise and there is nothing to see anywhere.
        """
        pool = [
            cand("siglip", value=0.9, embedding=(1.0, 0.0, 0.0), captured_utc=at(1),
                 embedding_space="siglip2_so400m"),
            cand("clip", value=0.8, embedding=(0.0, 1.0, 0.0), captured_utc=at(2),
                 embedding_space="clip_vit_l14"),
        ]
        with self.assertRaises(SelectionError) as ctx:
            select(pool, 2)
        self.assertIn("vector space", str(ctx.exception))

        # ...and one space is fine.
        same = [
            cand("a", value=0.9, embedding=(1.0, 0.0, 0.0), captured_utc=at(1),
                 embedding_space="siglip2_so400m"),
            cand("b", value=0.8, embedding=(0.0, 1.0, 0.0), captured_utc=at(2),
                 embedding_space="siglip2_so400m"),
        ]
        self.assertEqual(len(select(same, 2).selected), 2)

    def test_an_unlabelled_embedding_is_not_assumed_to_match_a_labelled_one(self):
        """'Probably the same model' is exactly the assumption that produces a
        meaningless similarity, so it is refused rather than guessed."""
        pool = [
            cand("labelled", value=0.9, embedding=(1.0, 0.0, 0.0), captured_utc=at(1),
                 embedding_space="siglip2_so400m"),
            cand("bare", value=0.8, embedding=(0.0, 1.0, 0.0), captured_utc=at(2)),
        ]
        with self.assertRaises(SelectionError):
            select(pool, 2)


class TestStandingCrossesClassesOnQualityNotSize(unittest.TestCase):
    def test_a_large_mediocre_class_does_not_outrank_a_small_excellent_one(self):
        """CATCHES: within-class standing being PURE RANK shrunk toward a flat
        0.5, which makes the cross-class comparison a function of class SIZE.

        Twelve photos at 0.42 measured one way are each 'top of their class' and
        so is each of three photos at 0.95 measured another way. Rank cannot tell
        those apart; only the value can. With the flat prior the twelve win on
        headcount (0.875 vs 0.714) and the album is built entirely out of the
        mediocre class while every number in the report looks healthy.

        Also CATCHES `_STANDING_PRIOR = 0`: with no shrink at all both classes
        rank a flat 1.0, the gain ties, and the media_id tiebreak hands it to the
        `aaa` class -- which is why they are named to sort first.
        """
        moment, place, scene = at(1, 9), "hall", "indoor"
        many = [
            cand(f"aaa{i:02d}", value=0.42,
                 score=score(0.42, measured=("sharpness", "exposure")),
                 captured_utc=moment, place_key=place, scene_type=scene)
            for i in range(12)
        ]
        few = [
            cand(f"zzz{i}", value=0.95,
                 score=score(0.95, measured=("sharpness", "exposure", "noise")),
                 captured_utc=moment, place_key=place, scene_type=scene)
            for i in range(3)
        ]
        result = select(many + few, 3)
        self.assertEqual(set(result.selected), {mid(f"zzz{i}") for i in range(3)})


class TestChronologyIsSelfChecked(unittest.TestCase):
    def test_a_broken_chronological_sort_flips_the_constraint(self):
        """`chronological_within_section` is measured on a list `_chronological`
        just sorted, so it cannot fail while that sort is right -- which makes it
        unfalsifiable unless something proves it still bites. This is that proof:
        the sort is replaced with two broken ones and the flag must go False.

        Without this test the constraint is decoration, and decoration in a
        report that exists to be trusted is worse than an absent field.
        """
        pool = [cand(f"c{i}", value=0.8, captured_utc=at(i + 1)) for i in range(3)]
        pool.append(cand("undated", value=0.8))
        original = selection_module._chronological

        self.assertTrue(constraint(select(pool, 4), "chronological_within_section").satisfied)

        try:
            # 1. reversed: dated photos run backwards.
            selection_module._chronological = (
                lambda selected, by_id: tuple(reversed(original(selected, by_id)))
            )
            self.assertFalse(
                constraint(select(pool, 4), "chronological_within_section").satisfied
            )
            # 2. undated first, dated still ascending -- the epoch-sort bug,
            #    which a plain monotonicity check would wave through.
            selection_module._chronological = lambda selected, by_id: tuple(
                sorted(original(selected, by_id),
                       key=lambda m: (by_id[m].captured_utc is not None,))
            )
            self.assertFalse(
                constraint(select(pool, 4), "chronological_within_section").satisfied
            )
        finally:
            selection_module._chronological = original


class TestObjectiveTermsAllCarryWeight(unittest.TestCase):
    """One test per term in `gain`, on a pool where every OTHER term is
    identical across candidates, so deleting that one term changes the album.

    Built this way because a term can be removed from a weighted sum without any
    test noticing: the remaining terms still produce a plausible album.
    """

    def test_place_coverage_decides_when_nothing_else_does(self):
        """CATCHES: the place term deleted, or its weight zeroed.

        Nine identical photos but for location: six at the cliff, one each in
        three other places. Same instant, same (unknown) scene, nobody in them --
        so quality, time, scene, people and redundancy are equal for every
        candidate in every round and only place can break the tie. Without the
        term the album is four photos of the same cliff.
        """
        moment = at(1, 9)
        pool = [
            cand(f"aa{i}", value=0.9, captured_utc=moment, place_key="cliff")
            for i in range(6)
        ] + [
            cand(f"z{k}", value=0.9, captured_utc=moment, place_key=k)
            for k in ("bay", "cafe", "dune")
        ]
        place_of = {c.media_id: c.place_key for c in pool}
        result = select(pool, 4)
        places = {place_of[m] for m in result.selected}
        self.assertGreaterEqual(
            len(places), 3, f"only {len(places)} distinct places: {sorted(places)}"
        )

    def test_scene_coverage_decides_when_nothing_else_does(self):
        """CATCHES: the scene term deleted, or its weight zeroed. Same
        construction as the place test, one axis over."""
        moment = at(1, 9)
        pool = [
            cand(f"aa{i}", value=0.9, captured_utc=moment, place_key="one",
                 scene_type="portrait")
            for i in range(6)
        ] + [
            cand(f"z{s}", value=0.9, captured_utc=moment, place_key="one", scene_type=s)
            for s in ("food", "indoor", "night")
        ]
        scene_of = {c.media_id: c.scene_type for c in pool}
        result = select(pool, 4)
        scenes = {scene_of[m] for m in result.selected}
        self.assertGreaterEqual(
            len(scenes), 3, f"only {len(scenes)} distinct scenes: {sorted(scenes)}"
        )

    def test_person_saturation_eventually_lets_scenery_in(self):
        """CATCHES: `weight_person` zeroed, i.e. the people term in the FILL
        objective deleted.

        The people FLOOR is a separate phase and covers Ava with one photo
        whatever this weight is, so a test that only checks 'Ava appears' passes
        with the term gone. What the term does is make the sixth photo of Ava
        worth less than the first photo of the mountains: here Ava is the only
        person (so the cap is suspended and cannot do the job instead) and her
        photos outscore the scenery on quality, yet the decayed people bucket
        must still let one landscape through.
        """
        moment = at(1, 9)
        pool = [
            cand(f"ava{i}", value=0.9, person_ids=(PERSON["ava"],), captured_utc=moment,
                 place_key="one", scene_type="portrait")
            for i in range(6)
        ] + [
            cand(f"land{i}", value=0.7, captured_utc=moment, place_key="one",
                 scene_type="portrait")
            for i in range(4)
        ]
        result = select(pool, 5)
        scenery = {mid(f"land{i}") for i in range(4)}
        self.assertGreaterEqual(
            len(set(result.selected) & scenery), 1,
            "an album of one person still owes the trip one photo of somewhere",
        )

    def test_a_group_shot_does_not_out_earn_a_portrait_by_containing_more_faces(self):
        """CATCHES: the people term summed instead of averaged.

        Everyone is already covered -- the floor did that with the first group
        shot -- so what is left is a question of value, and a second identical
        group shot of the same five people is worth LESS than a good portrait,
        not five times more. Summed, `g2` earns 0.6 * 5 * 0.5 = 1.5 against a
        portrait's 0.3 and walks into the album on headcount alone.
        """
        five = tuple(sorted(PERSON[n] for n in ("ava", "bo", "cy", "dee", "gran")))
        moment = at(1, 9)
        common = dict(captured_utc=moment, place_key="one", scene_type="portrait")
        pool = [
            cand("g1", value=0.4, person_ids=five, **common),
            cand("g2", value=0.4, person_ids=five, **common),
        ] + [
            cand(f"solo_{n}", value=0.9, person_ids=(PERSON[n],), **common)
            for n in ("ava", "bo", "cy", "dee", "gran")
        ]
        result = select(pool, 6)
        self.assertIn(mid("g1"), result.selected, "the floor covers everyone at once")
        self.assertNotIn(mid("g2"), result.selected)

    def test_a_dissimilar_photo_gets_no_redundancy_bonus(self):
        """CATCHES: the `max(0.0, ...)` clamp removed from the redundancy term.

        Unclamped, a photo whose closest selected neighbour sits BELOW
        `redundancy_free_similarity` gets a negative penalty -- a bonus of up to
        0.8 * 0.6 = 0.48 for being unlike everything. A uniform bonus would
        cancel out and hide, so the pool is deliberately mixed: `b_embed` carries
        an embedding and `a_no_embed` does not, which is the ordinary state of a
        library mid-scan. The clamp is what stops 'we have not compared this yet'
        from paying better than quality.
        """
        moment = at(1, 9)
        pool = [
            cand("a_no_embed", value=0.9, captured_utc=moment, place_key="one",
                 scene_type="portrait"),
            cand("b_embed", value=0.6, embedding=(1.0, 0.0, 0.0), captured_utc=moment,
                 place_key="one", scene_type="portrait"),
        ]
        self.assertEqual(select(pool, 1).selected, (mid("a_no_embed"),))

    def test_the_people_floor_covers_a_person_with_their_BEST_photo(self):
        """CATCHES: the floor phase's gain tiebreak losing its sign, so the
        cheapest way to cover someone becomes their worst photo.

        Coverage is satisfied either way and `missing_person_ids` stays empty, so
        nothing else in the report notices. Bo is covered by one of two photos
        and it must be the good one.
        """
        moment = at(1, 9)
        pool = [
            cand("ava1", value=0.90, person_ids=(PERSON["ava"],), captured_utc=moment,
                 place_key="one", scene_type="portrait"),
            cand("bo_bad", value=0.40, person_ids=(PERSON["bo"],), captured_utc=moment,
                 place_key="one", scene_type="portrait"),
            cand("bo_good", value=0.95, person_ids=(PERSON["bo"],), captured_utc=moment,
                 place_key="one", scene_type="portrait"),
        ]
        result = select(pool, 2)
        self.assertIn(mid("bo_good"), result.selected)
        self.assertNotIn(mid("bo_bad"), result.selected)

    def test_scarcity_rescues_the_best_photo_of_a_scarce_person_not_the_worst(self):
        """CATCHES: the rescue tiebreak inverted.

        Every photo of Gran is under the floor, so exactly one is rescued and it
        is the only one she will ever have in the book. `len(rescued) == 1` --
        which is what the existing scarcity test checks -- is equally true of the
        worst one, and the worst one is the failure: the album prints the blurred
        frame instead of the merely dark one.

        Named so the best photo sorts LAST by media_id, because an inverted
        tiebreak that still picked the alphabetically-first id would otherwise be
        indistinguishable from a correct one.
        """
        pool = [
            cand("gran_a", value=0.10, person_ids=(PERSON["gran"],), captured_utc=at(1)),
            cand("gran_b", value=0.20, person_ids=(PERSON["gran"],), captured_utc=at(2)),
            cand("gran_c", value=0.30, person_ids=(PERSON["gran"],), captured_utc=at(3)),
        ] + [
            cand(f"ava{i}", value=0.9, person_ids=(PERSON["ava"],), captured_utc=at(4, i))
            for i in range(4)
        ]
        result = select(pool, 4)
        self.assertEqual(result.rescued_media_ids, (mid("gran_c"),))
        self.assertIn(mid("gran_c"), result.selected)

    def test_by_standing_is_best_first(self):
        """CATCHES: the `by_standing` sort losing its negation.

        `by_standing[0]` is documented as the hero candidate and is what a caller
        reaches for to pick the cover. Reversed, the cover of the book becomes
        the weakest photo in it -- and `selected` is unaffected, so every other
        assertion in this file still passes.
        """
        pool = [
            cand("hero", value=0.95, captured_utc=at(1)),
            cand("middling", value=0.75, captured_utc=at(2)),
            cand("weakest", value=0.55, captured_utc=at(3)),
        ]
        result = select(pool, 3)
        self.assertEqual(
            result.by_standing, (mid("hero"), mid("middling"), mid("weakest"))
        )


class TestBoundariesAndQuantisation(unittest.TestCase):
    """The exact-equality cases, and the six-decimal rule that keeps two
    machines producing the same album."""

    def test_a_photo_exactly_at_the_pixel_floor_is_printable(self):
        """CATCHES: `<` becoming `<=` in the pixel gate. The vendor profile says
        3000px is acceptable, so a 3000px photo is acceptable; rejecting it
        throws away a photo the printer would have taken."""
        policy = SelectionPolicy(min_long_edge_px=3000)
        pool = [cand("exact", value=0.9, long_edge_px=3000, captured_utc=at(1))]
        self.assertEqual(select(pool, 1, policy=policy).selected, (mid("exact"),))

    def test_a_photo_exactly_at_the_quality_floor_is_kept(self):
        """CATCHES: `<` becoming `<=` in the quality floor. The floor is the
        lowest ACCEPTABLE value, not the highest rejected one -- and the scarcity
        rescue keys on the same comparison, so an off-by-one here also rescues a
        person who did not need rescuing."""
        pool = [cand("edge", value=DEFAULT_POLICY.quality_floor, captured_utc=at(1))]
        result = select(pool, 1)
        self.assertEqual(result.selected, (mid("edge"),))
        self.assertEqual(result.rescued_media_ids, ())

    def test_a_score_above_one_is_refused(self):
        """CATCHES: the [0,1] range check dropped. A value outside the range is
        not a very good photo, it is a fusion bug -- and it silently dominates
        every coverage term, which is how one broken score takes an album."""
        with self.assertRaises(SelectionError) as ctx:
            select([cand("hot", value=1.5, score=score(1.5))], 1)
        self.assertIn("outside [0,1]", str(ctx.exception))

    def test_gains_that_differ_below_six_decimals_break_on_media_id(self):
        """CATCHES: the gain no longer being quantised.

        Two photos a ten-millionth apart are the same photo to any human, but
        `>` picks one of them, and WHICH one depends on summation order and
        therefore on the machine. Quantising makes it an explicit tie that the
        media_id tiebreak resolves identically everywhere. `zzz` is the very
        slightly better photo and must still lose, because it is not better by
        any amount that means anything.

        Preview mode is used so standing is the raw score and the difference
        survives to the gain unmodified.
        """
        moment = at(1, 9)
        pool = [
            cand("aaa", value=0.5, captured_utc=moment, place_key="one",
                 scene_type="indoor"),
            cand("zzz", value=0.5000001, captured_utc=moment, place_key="one",
                 scene_type="indoor"),
        ]
        result = select(pool, 1, allow_mixed_measurements=True)
        self.assertEqual(result.selected, (mid("aaa"),))

    def test_quantisation_is_fine_enough_to_keep_a_real_difference(self):
        """The other side of the same constant: six decimals, not two.

        A coarser quantum would fold genuinely different gains into ties and hand
        the album to whichever media_id sorts first -- an alphabetical album
        dressed up as determinism. `zzz` is better by 0.004, which is a real
        difference, and it must win despite sorting last.
        """
        moment = at(1, 9)
        pool = [
            cand("aaa", value=0.500, captured_utc=moment, place_key="one",
                 scene_type="indoor"),
            cand("zzz", value=0.504, captured_utc=moment, place_key="one",
                 scene_type="indoor"),
        ]
        result = select(pool, 1, allow_mixed_measurements=True)
        self.assertEqual(result.selected, (mid("zzz"),))

    def test_standings_equal_to_six_decimals_order_by_media_id(self):
        """CATCHES: standing no longer being quantised.

        `by_standing[0]` is the cover of the book. Two photos whose standing
        agrees to six decimals must be ordered by media_id, not by whatever the
        sixteenth decimal happens to say on this machine -- `zzz` is ahead by
        4e-7, which is noise, and must not take the cover from `aaa`.
        """
        moment = at(1, 9)
        pool = [
            cand("aaa", value=0.5, score=score(0.5, measured=("sharpness", "exposure")),
                 captured_utc=moment, place_key="one", scene_type="indoor"),
            cand("zzz", value=0.5000005,
                 score=score(0.5000005, measured=("sharpness", "exposure", "noise")),
                 captured_utc=moment, place_key="one", scene_type="indoor"),
        ]
        result = select(pool, 2)
        self.assertEqual(result.by_standing, (mid("aaa"), mid("zzz")))

    def test_max_time_bins_actually_caps_the_timeline(self):
        """CATCHES: `min(target_count, max_time_bins)` losing the cap.

        With one bin the whole event is a single moment, so the temporal term
        cannot distinguish anything and quality decides alone. Uncapped, the bin
        count follows the album size and the timeline term reappears -- which is
        the difference between "one bin per photo we intend to select, capped"
        and "one bin per photo we intend to select".
        """
        policy = SelectionPolicy(max_time_bins=1)
        strong = [cand(f"aa{i}", value=0.95, captured_utc=at(1, i)) for i in range(5)]
        weak = [cand(f"zz{i}", value=0.55, captured_utc=at(2 + i, 9)) for i in range(5)]
        result = select(strong + weak, 5, policy=policy)
        self.assertEqual(set(result.selected), {mid(f"aa{i}") for i in range(5)})


class TestConstraintsCatchWhatTheGreedyDidNotPrevent(unittest.TestCase):
    """The report measures the OUTCOME. These are the cases where the outcome
    violates something -- because the greedy is allowed to, or because the input
    was malformed -- and the flag must go False."""

    def test_two_primaries_of_one_dedupe_group_are_reported(self):
        """CATCHES: the duplicate-group constraint hard-coded satisfied.

        The hard gate drops secondaries, so this only happens when the dedupe
        pass marked two members of a group primary -- malformed upstream data.
        The constraint exists precisely to notice that rather than trust the
        gate, and it is the difference between finding the bug and printing two
        frames of one moment on facing pages.
        """
        group = "33333333-3333-4333-8333-333333333333"
        pool = [
            cand("twin_a", value=0.9, dedupe_group_id=group, is_dedupe_primary=True,
                 captured_utc=at(1)),
            cand("twin_b", value=0.9, dedupe_group_id=group, is_dedupe_primary=True,
                 captured_utc=at(2)),
        ]
        entry = constraint(select(pool, 2), "no_near_duplicates_on_spread")
        self.assertFalse(entry.satisfied)
        self.assertIn(group, entry.detail)

    def test_two_untagged_photos_in_a_row_are_not_a_scene_repeat(self):
        """CATCHES: the scene-repeat check counting `None == None` as a repeat.

        A library that has not been scene-tagged yet would report a violation on
        every adjacent pair, which makes the flag mean nothing at exactly the
        moment a user is most likely to look at it.
        """
        pool = [cand(f"u{i}", value=0.8, captured_utc=at(i + 1)) for i in range(4)]
        self.assertTrue(constraint(select(pool, 4), "no_consecutive_same_scene").satisfied)

    def test_the_people_floor_overrunning_the_cap_is_reported_not_hidden(self):
        """CATCHES: the cap constraint that can never fire.

        Phase one deliberately ignores the cap -- covering everybody matters more
        than a tidy ratio -- so Ava, who is in every group shot, ends up over her
        share. The report must say what HAPPENED, not what the policy intended.
        A constraint that only ever agrees with the algorithm certifies nothing.
        """
        pool = [
            cand(f"pair_{n}", value=0.9,
                 person_ids=tuple(sorted((PERSON["ava"], PERSON[n]))), captured_utc=at(i + 1))
            for i, n in enumerate(["bo", "cy", "dee", "gran"])
        ]
        result = select(pool, 8, policy=SelectionPolicy(max_per_person_fraction=0.25))
        self.assertEqual(result.person_counts[PERSON["ava"]], 4)
        entry = constraint(result, "max_per_person_per_section")
        self.assertFalse(entry.satisfied, "ava is over a cap of 2 and nothing says so")

    def test_a_people_floor_that_eats_the_scenery_reserve_is_reported(self):
        """CATCHES: the scenery floor measured against a constant instead of
        against what the event actually contains.

        Ten people, ten slots: the floor spends every one of them on faces and
        the scenery reserve never gets a round. That is the right call -- omitting
        a person is worse -- but the balance was NOT met, and re-deriving `wanted`
        as zero would let the spec claim it was.
        """
        pool = [
            cand(f"solo_{i}", value=0.9,
                 person_ids=(f"{i:08x}-0000-4000-8000-000000000000",), captured_utc=at(1, i))
            for i in range(1, 11)
        ] + [
            cand(f"scene{i}", value=0.5, scene_type="landscape", captured_utc=at(2, i))
            for i in range(3)
        ]
        result = select(pool, 10)
        self.assertEqual(len(result.selected), 10)
        entry = constraint(result, "people_scenery_detail_balance")
        self.assertFalse(entry.satisfied)
        self.assertIn("wanted 1", entry.detail)

    def test_the_non_people_reserve_holds_a_slot_back(self):
        """CATCHES: the reserve deleted, or its `>=` weakened to `>`.

        One person, so the cap is suspended and cannot do this job instead, and
        her photos beat the scenery by so much that the decaying people bucket
        never catches up. The reserve is the only thing that puts one photo of
        somewhere in a twenty-photo album of one person -- and `>` misses by
        exactly one round, which is the whole reserve.
        """
        moment = at(1, 9)
        pool = [
            cand(f"ava{i:02d}", value=0.95, person_ids=(PERSON["ava"],),
                 captured_utc=moment, place_key="one", scene_type="portrait")
            for i in range(20)
        ] + [
            cand(f"land{i}", value=0.36, captured_utc=moment, place_key="one",
                 scene_type="portrait")
            for i in range(5)
        ]
        result = select(pool, 10)
        scenery = {mid(f"land{i}") for i in range(5)}
        self.assertEqual(len(set(result.selected) & scenery), 1)
        self.assertTrue(constraint(result, "people_scenery_detail_balance").satisfied)

    def test_a_cap_below_the_person_floor_would_certify_a_cap_nobody_applied(self):
        """CATCHES: `_person_cap` dropping the `max(min_per_person, ...)`.

        The floor guarantees three photos of each person; a cap computed as
        `ceil(6 * 0.1) = 1` would then mark the very album the floor just built
        as a cap violation. The two numbers live in one function so they cannot
        contradict each other, and this is the contradiction.
        """
        policy = SelectionPolicy(min_per_person=3, max_per_person_fraction=0.1)
        pool = [
            cand(f"ava{i}", value=0.9, person_ids=(PERSON["ava"],), captured_utc=at(1, i))
            for i in range(5)
        ] + [
            cand(f"bo{i}", value=0.9, person_ids=(PERSON["bo"],), captured_utc=at(2, i))
            for i in range(5)
        ]
        result = select(pool, 6, policy=policy)
        self.assertEqual(result.person_counts[PERSON["ava"]], 3)
        self.assertTrue(
            constraint(result, "max_per_person_per_section").satisfied,
            "the report is failing the album its own floor required",
        )

    def test_an_unmapped_exclusion_reason_does_not_borrow_a_specific_one(self):
        """CATCHES: the fallback rejection reason becoming something specific.

        A reason the map does not know must degrade to the generic
        `excluded_content`. Borrowing `below_quality_floor` puts a confident,
        wrong explanation in front of the user -- they go looking at sharpness
        for a photo that was dropped as a screenshot.
        """
        pool = [
            cand("mystery", value=0.9, excluded_from_automation=True,
                 exclusion_reasons=("some_future_reason",), captured_utc=at(1)),
            cand("bare", value=0.9, excluded_from_automation=True, captured_utc=at(2)),
            cand("fine", value=0.8, captured_utc=at(3)),
        ]
        result = select(pool, 3)
        self.assertEqual(reason_for(result, mid("mystery")), "excluded_content")
        self.assertEqual(reason_for(result, mid("bare")), "excluded_content")

    def test_the_report_lists_each_media_id_once(self):
        """CATCHES: a rejection list that repeats an id.

        The report's arithmetic -- candidates minus selected minus rejected --
        is how a caller recovers the count that `include_no_space` leaves out,
        and a duplicated entry silently breaks it.
        """
        pool = [
            cand("hidden", value=0.99, user_hidden=True, captured_utc=at(1)),
        ] + [cand(f"n{i}", value=0.8, captured_utc=at(2, i)) for i in range(5)]
        report = select(pool, 2).to_selection_report(include_no_space=True)
        ids = [entry["media_id"] for entry in report["rejected"]]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(len(ids), 4, "1 hidden + 3 that lost on merit")


class TestPartitionDriftGuard(unittest.TestCase):
    """`measurement_key` is derived from the same three properties
    `comparable_with` compares, so today they cannot disagree. The guard exists
    for the day fusion changes one of them and this module does not follow --
    a silent failure, because a coarser partition just ranks two photos that
    should never have been ordered and returns a plausible album.

    Guards that cannot be triggered are guards nobody knows are broken, so the
    disagreement is manufactured here with a score whose rule has drifted.
    """

    @staticmethod
    def drifted(base: FusedScore, verdict: bool) -> FusedScore:
        cls = type(
            "DriftedScore", (FusedScore,),
            {"comparable_with": lambda self, other, _v=verdict: _v},
        )
        return cls(**{f.name: getattr(base, f.name) for f in dataclasses.fields(base)})

    def test_a_key_coarser_than_the_real_rule_is_refused(self):
        """Same key, fusion says not comparable -- the partition would rank two
        photos against each other that fusion refuses to order."""
        pool = [
            cand("zzz", value=0.9, score=self.drifted(score(0.9), False), captured_utc=at(1)),
            cand("aaa", value=0.8, score=self.drifted(score(0.8), False), captured_utc=at(2)),
        ]
        # Reversed on the way in, so the error text can only name `aaa` if the
        # class members were sorted by media_id first. CATCHES: class members
        # left in input order, which is otherwise invisible.
        with self.assertRaises(SelectionError) as ctx:
            select(pool, 2)
        message = str(ctx.exception)
        self.assertIn("class key has drifted", message)
        self.assertIn(f"{mid('zzz')} shares a measurement key with {mid('aaa')}", message)

    def test_class_order_depends_on_the_classes_not_on_which_photo_sorts_first(self):
        """CATCHES: `Selection.classes` ordered by dict insertion.

        Insertion order is stable per run, so this is not a flakiness bug -- it
        is a churn bug. Ordered by first appearance, the class list is a function
        of which media_id happens to sort lowest, so importing one more photo
        reorders a report that did not otherwise change and every diff of two
        AlbumSpecs is noise. Ordered by key, it is a function of the classes
        themselves.

        `aaa` sorts first by media_id, and its class sorts SECOND by key
        (['exposure', 'sharpness'] against ['exposure', 'noise', 'sharpness']),
        so the two orders disagree and only one of them can be right.
        """
        pool = [
            cand("aaa", value=0.9, score=score(0.9, measured=("sharpness", "exposure")),
                 captured_utc=at(1)),
            cand("zzz", value=0.8,
                 score=score(0.8, measured=("sharpness", "exposure", "noise")),
                 captured_utc=at(2)),
        ]
        classes = select(pool, 2).classes
        self.assertEqual(
            [sorted(entry.measured) for entry in classes],
            [["exposure", "noise", "sharpness"], ["exposure", "sharpness"]],
        )

    def test_a_key_finer_than_the_real_rule_is_refused(self):
        """Different keys, fusion says comparable -- the partition would split
        photos that belong in one class, and standing would then be measured
        against the wrong peers."""
        pool = [
            cand("aaa", value=0.9,
                 score=self.drifted(score(0.9, measured=("sharpness", "exposure")), True),
                 captured_utc=at(1)),
            cand("zzz", value=0.8,
                 score=self.drifted(score(0.8, measured=("sharpness",)), True),
                 captured_utc=at(2)),
        ]
        with self.assertRaises(SelectionError) as ctx:
            select(pool, 2)
        self.assertIn("finer than FusedScore.comparable_with", str(ctx.exception))


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def constraint(result: Selection, name: str):
    for entry in result.constraints:
        if entry.constraint == name:
            return entry
    raise AssertionError(f"no constraint named {name}")


def reason_for(result: Selection, media_id: str) -> str | None:
    for entry in result.rejected:
        if entry.media_id == media_id:
            return entry.reason
    return None


def base_record(tag: str) -> dict:
    return {
        "schema_version": "0.1.0",
        "media_id": mid(tag),
        "asset_kind": "file",
        "kind": "image",
        "byte_size": 1024,
        "capture": {"captured_at": {"source": "unknown", "precision": "unknown",
                                    "confidence": 0.0}, "metadata_present": []},
        "processing": {},
        "exclusion": {"excluded_from_automation": False},
        "user": {},
        "first_seen_at": "2026-03-01T00:00:00+00:00",
        "updated_at": "2026-03-01T00:00:00+00:00",
    }


_VALIDATOR = None


def validator():
    """A Draft 2020-12 validator for AlbumSpec's SelectionReport, with the
    cross-file $refs resolvable."""
    global _VALIDATOR
    if _VALIDATOR is None:
        from jsonschema import Draft202012Validator
        from referencing import Registry, Resource

        documents = {
            path.name: json.loads(path.read_text(encoding="utf-8"))
            for path in sorted(SCHEMA_DIR.glob("*.schema.json"))
        }
        registry = Registry().with_resources(
            [(name, Resource.from_contents(doc)) for name, doc in documents.items()]
        )
        album = documents["album-spec.schema.json"]
        schema = dict(album["$defs"]["SelectionReport"])
        schema["$id"] = "album-spec.schema.json"
        schema["$defs"] = album["$defs"]
        _VALIDATOR = Draft202012Validator(schema, registry=registry)
    return _VALIDATOR


if __name__ == "__main__":
    unittest.main(verbosity=2)
