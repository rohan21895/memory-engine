"""Selection criteria v3: the group is judged by its worst face, the album
by its breadth of moments, and the user's word is final.

THE PHOTOS THIS FILE EXISTS BECAUSE OF

A real group photo went to print with one person mid-blink: the whole-frame
axes averaged him away behind three smiling faces. A kiss with closed eyes
was penalised as a blink. A close portrait was deleted as "a worse version"
of the wide frame shot two seconds earlier. The only photo of the goodbye at
the airport was rejected for being 0.02 under the quality floor. And a
grocery receipt nearly made an album because it was the only "photo" of an
aunt. v3 adds exactly those senses:

  - per-face aggregates: blink detection on the WORST significant face,
    taking precedence over the whole-frame majority;
  - embrace context: closed eyes inside a kiss are the photo, not a defect;
  - category presets: a portrait is judged on the face, a detail on the light;
  - moments: a loose grouping above shots, so breadth beats a second take;
  - story roles: a close-up is never a worse version of a wide;
  - rare moments: an irreplaceable moment beats a technical rule;
  - hard content gates: screenshots never, clipping and black faces gated
    (with waivers) because they are UNREPAIRABLE, unlike the mild exposure
    problems auto-develop fixes at render;
  - pins and excludes: user choice is sovereign, and a silent no-op pin is
    a lost user decision.
"""

from __future__ import annotations

import unittest
from dataclasses import replace

# test_selection sets sys.path up for the plain source trees; import it first.
from test_selection import PERSON, at, cand, mid

from memory_engine_album.selection import (  # noqa: E402
    CATEGORY_PRESETS,
    PerFaceAggregates,
    SelectionError,
    SelectionPolicy,
    select,
)


def faces(
    eyes_min: float | None = None,
    smile_min: float | None = None,
    exposure_min: float | None = None,
    exposure_clipped_max: float | None = None,
    largest_area: float | None = None,
    count: int = 2,
    detected_count: int = 0,
    detected_largest_area: float | None = None,
) -> PerFaceAggregates:
    return PerFaceAggregates(
        significant_count=count,
        largest_area=largest_area,
        eyes_min=eyes_min,
        smile_min=smile_min,
        exposure_min=exposure_min,
        exposure_clipped_max=exposure_clipped_max,
        detected_count=detected_count or count,
        detected_largest_area=(
            detected_largest_area if detected_largest_area is not None else largest_area
        ),
    )


def unit(*components: float) -> tuple[float, ...]:
    norm = sum(c * c for c in components) ** 0.5
    return tuple(c / norm for c in components)


class OneBlinkerCannotHideBehindASmilingMajority(unittest.TestCase):
    """The whole-frame awake axis IS the majority: three open-eyed smiles drag
    the frame contrast positive and the fourth person's blink disappears.
    Per-face eyes_min sees the worst face, and it takes precedence."""

    def _pool(self):
        # Whole-frame awake is POSITIVE on the group shot (the majority), yet
        # the worst face's eyes read shut (stored [0,1] value < 0.5) and rank
        # bottom of the pool.
        blinker = cand(
            "blinkgrp", 0.85, smile=0.09, awake=0.06,
            per_face=faces(eyes_min=0.30, smile_min=0.55, count=4),
        )
        opens = [
            cand(
                f"open{i}", 0.65 - i * 0.01, smile=0.02 + i * 0.01, awake=0.05,
                per_face=faces(eyes_min=0.72 - i * 0.02, smile_min=0.58 + i * 0.02, count=4),
            )
            for i in range(3)
        ]
        return blinker, opens

    def test_the_group_with_a_blinker_loses_despite_quality_and_smiles(self):
        blinker, opens = self._pool()
        result = select([blinker] + opens, 1)
        self.assertEqual((mid("open0"),), result.selected)

    def test_the_kill_switch_restores_whole_frame_behaviour(self):
        # per_face_weight=0.0 disables every per-face term at once: the
        # whole-frame awake contrast is positive, so no penalty fires and the
        # higher-quality group shot wins again.
        blinker, opens = self._pool()
        result = select(
            [blinker] + opens, 1, policy=SelectionPolicy(per_face_weight=0.0)
        )
        self.assertEqual((mid("blinkgrp"),), result.selected)


class AKissWithClosedEyesIsThePhoto(unittest.TestCase):
    """Embrace context suppresses the blink penalty: validated on a real
    maternity library where the kiss/embrace shots rank top of the pool.
    Rank-triggered, never an absolute threshold -- the raw contrast straddles
    zero across libraries."""

    def _pool(self, kiss_embrace: float):
        kiss = cand(
            "kiss", 0.9, embrace_context=kiss_embrace,
            per_face=faces(eyes_min=0.30, count=2),
        )
        others = [
            cand(
                f"open{i}", 0.6, embrace_context=-0.05 - i * 0.01,
                per_face=faces(eyes_min=0.70 + i * 0.01, count=2),
            )
            for i in range(3)
        ]
        return [kiss] + others

    def test_the_kiss_wins_when_its_embrace_rank_is_top_of_pool(self):
        result = select(self._pool(kiss_embrace=0.12), 1)
        self.assertEqual((mid("kiss"),), result.selected)

    def test_without_embrace_evidence_the_blink_penalty_still_fires(self):
        # Same closed eyes, no embrace: the penalty overturns the 0.9 vs 0.6
        # quality lead, exactly as designed. (Which open-eyed frame wins is
        # the worst-face eyes term's business -- the best eyes_min takes it.)
        result = select(self._pool(kiss_embrace=-0.05), 1)
        self.assertNotIn(mid("kiss"), result.selected)
        self.assertEqual((mid("open2"),), result.selected)


class CategoryPresetsChangeTheWinnerDeterministically(unittest.TestCase):
    """A detail shot is judged on the light: the 'detail' preset raises
    weight_aesthetic from 0.55 to 0.70, enough to overturn a quality-standing
    lead the default weights respect. Presets are starter values pending
    PrefEvent learning -- this pins the mechanism, not the numbers' taste."""

    def _pool(self, category: str | None):
        pretty = cand("pretty", 0.6, aesthetic=0.10, category=category)
        plain = cand("plain", 0.8, aesthetic=-0.10, category=category)
        return [pretty, plain]

    def test_default_weights_let_quality_win(self):
        result = select(self._pool(category=None), 1)
        self.assertEqual((mid("plain"),), result.selected)

    def test_the_detail_preset_lets_the_light_win(self):
        self.assertIn("detail", CATEGORY_PRESETS)
        result = select(self._pool(category="detail"), 1)
        self.assertEqual((mid("pretty"),), result.selected)

    def test_an_unknown_category_is_the_default_not_an_error(self):
        # The classifier's vocabulary may grow ahead of the preset table.
        result = select(self._pool(category="pet"), 1)
        self.assertEqual((mid("plain"),), result.selected)


class MomentBreadthBeatsASecondTake(unittest.TestCase):
    """Two takes of one moment and one photo of another: the album takes one
    of each. The pool is built so quality favours the second take and the
    redundancy penalty is IDENTICAL for both contenders (every pairwise
    similarity is exactly 0.85) -- only the moment term can prefer breadth."""

    def _member(self, tag: str, index: int, value: float, hour: float):
        # sqrt(0.85) on a shared axis + sqrt(0.15) on a private one: every
        # pairwise cosine is exactly 0.85 -- above moment_similarity (0.80),
        # below shot_similarity (0.93).
        embedding = [0.0] * 4
        embedding[3] = 0.85 ** 0.5
        embedding[index] = 0.15 ** 0.5
        return cand(
            tag, value, captured_utc=at(1, hour),
            embedding=tuple(embedding), embedding_space="moment_space",
        )

    def test_one_take_per_moment_before_a_second_of_the_best(self):
        take1 = self._member("take1", 0, 0.90, hour=12.0)
        take2 = self._member("take2", 1, 0.85, hour=12.2)  # same moment: 12 min later
        other = self._member("other", 2, 0.80, hour=22.0)  # 9h50m later: its own moment
        # time_bins=1 levels the temporal term so it cannot decide instead.
        result = select(
            [take1, take2, other], 2, policy=SelectionPolicy(time_bins=1)
        )
        self.assertEqual({mid("take1"), mid("other")}, set(result.selected),
                         "breadth across moments beats a better second take")


class ACloseUpIsNeverAWorseVersionOfAWide(unittest.TestCase):
    """Same instant, same scene, 0.95 embedding similarity -- but the wide
    establishes the place and the close-up shows the person. The story-role
    split (largest face area differing by more than 2x) separates them before
    domination runs."""

    def _frames(self, close_area: float):
        wide = cand(
            "wide", 0.80, captured_utc=at(1, 12),
            embedding=unit(1.0, 0.0, 0.0), embedding_space="s",
            aesthetic=0.06, per_face=faces(largest_area=0.02),
        )
        close = cand(
            "close", 0.80, captured_utc=at(1, 12.002),  # seconds later
            embedding=unit(0.95, 0.3122, 0.0), embedding_space="s",
            aesthetic=0.02,  # loses to the wide by more than the margin
            per_face=faces(largest_area=close_area),
        )
        return wide, close

    def test_wide_and_close_both_survive_domination(self):
        wide, close = self._frames(close_area=0.09)  # 4.5x the wide's face
        result = select([wide, close], 2)
        self.assertEqual({mid("wide"), mid("close")}, set(result.selected))
        self.assertFalse(
            result.diagnostics[mid("close")]["worse_version_in_group"],
            "a different story role is a different picture, not a worse one",
        )
        self.assertNotEqual(
            result.groups[mid("wide")], result.groups[mid("close")],
            "the sidecar groups must reflect the story-role split",
        )

    def test_the_same_frames_at_equal_scale_still_dominate(self):
        # Control: same pair, same face scale -- now it IS a worse version
        # (beaten on aesthetic, winning nowhere) and stays out.
        wide, close = self._frames(close_area=0.02)
        result = select([wide, close], 2)
        self.assertIn(mid("wide"), result.selected)
        self.assertNotIn(mid("close"), result.selected)

    def test_solo_and_couple_on_the_same_set_are_different_pictures(self):
        # The real failure this pins: mom solo and mom-with-dad on the same
        # backdrop in the same dress measured 0.95+ similar and were treated
        # as one pose. One person versus two is a different photograph.
        solo = cand(
            "solo", 0.80, captured_utc=at(1, 12),
            embedding=unit(1.0, 0.0, 0.0), embedding_space="s",
            aesthetic=0.06, per_face=faces(largest_area=0.03, count=1),
        )
        couple = cand(
            "couple", 0.80, captured_utc=at(1, 12.003),
            embedding=unit(0.95, 0.3122, 0.0), embedding_space="s",
            aesthetic=0.02,  # would be dominated if they shared a group
            per_face=faces(largest_area=0.03, count=2),
        )
        result = select([solo, couple], 2)
        self.assertEqual({mid("solo"), mid("couple")}, set(result.selected))
        self.assertNotEqual(
            result.groups[mid("solo")], result.groups[mid("couple")],
            "people count must split the shot group",
        )

    def test_full_body_frames_still_split_by_scale_and_people(self):
        # The maternity sitting group: a tight crop (face 5% of frame) and a
        # wide editorial frame (face 1% -- under the 2% measurement floor, so
        # significant fields read empty). Detected evidence must still split
        # them; before it did, the tight crop structurally dominated and the
        # visibly better wide frames were unreachable.
        close = cand(
            "close", 0.80, captured_utc=at(1, 12),
            embedding=unit(1.0, 0.0, 0.0), embedding_space="s",
            per_face=faces(largest_area=0.05, count=1,
                           detected_count=1, detected_largest_area=0.05),
        )
        wide = cand(
            "wide", 0.70, captured_utc=at(1, 12.004),
            embedding=unit(0.95, 0.3122, 0.0), embedding_space="s",
            per_face=faces(largest_area=None, count=0,
                           detected_count=1, detected_largest_area=0.01),
        )
        result = select([close, wide], 2)
        self.assertEqual({mid("close"), mid("wide")}, set(result.selected))
        self.assertNotEqual(
            result.groups[mid("close")], result.groups[mid("wide")],
            "a 5x scale gap is two story roles even when the wide face is "
            "below the measurement floor",
        )

    def test_unmeasured_faces_do_not_split_a_group(self):
        # Absence of the measure must not split: the control pair from above
        # with no per_face stays one group and dominates as before.
        wide, close = self._frames(close_area=0.02)
        wide = replace(wide, per_face=None)
        close = replace(close, per_face=None)
        result = select([wide, close], 2)
        self.assertEqual(
            result.groups[mid("wide")], result.groups[mid("close")],
            "no face evidence, no split",
        )


class OnePoseCannotFillTheAlbumThroughItsRoleBands(unittest.TestCase):
    """The role/people splits exist so a wide and its close-up can BOTH
    compete -- but the day they landed, one pose took five of twenty-five
    pages through five role bands. The pose-family cap bounds what one pose
    can take, however many split groups it fans into."""

    def _family(self):
        # One burst, three story roles: chained embeddings keep every
        # adjacent pair above the shot threshold (one burst group) while the
        # far pair sits below the selected-similarity cap (so the pair
        # distinctness backstop is NOT what keeps the third frame out).
        a1 = cand(
            "fam1", 0.85, captured_utc=at(1, 12),
            embedding=unit(1.0, 0.0, 0.0), embedding_space="s",
            per_face=faces(largest_area=0.02, count=1),
        )
        a2 = cand(
            "fam2", 0.84, captured_utc=at(1, 12.002),
            embedding=unit(0.95, 0.3122, 0.0), embedding_space="s",
            per_face=faces(largest_area=0.05, count=1),
        )
        a3 = cand(
            "fam3", 0.83, captured_utc=at(1, 12.004),
            embedding=unit(0.85, 0.5268, 0.0), embedding_space="s",
            per_face=faces(largest_area=0.13, count=1),
        )
        return [a1, a2, a3]

    def test_the_cap_holds_against_a_weaker_distinct_pose(self):
        family = self._family()
        other = cand(
            "other", 0.60, captured_utc=at(1, 15),
            embedding=unit(0.0, 0.0, 1.0), embedding_space="s",
            per_face=faces(largest_area=0.03, count=1),
        )
        result = select(
            family + [other], 2, policy=SelectionPolicy(max_per_pose_family=1)
        )
        groups = result.groups
        self.assertEqual(
            3, len({groups[c.media_id] for c in family}),
            "the story-role split must fan the burst into three groups",
        )
        self.assertEqual(
            1, len({groups[c.media_id].split("#", 1)[0] for c in family}),
            "three roles, one pose family",
        )
        from_family = [m for m in result.selected if m != mid("other")]
        self.assertIn(mid("other"), result.selected)
        self.assertEqual(
            1, len(from_family),
            "the family cap must hold while a distinct pose is available",
        )

    def test_the_cap_relaxes_rather_than_short_change_the_album(self):
        # Only one pose exists and the user asked for three pages: the cap
        # relaxes a round at a time instead of returning a two-page album.
        family = self._family()
        result = select(family, 3, policy=SelectionPolicy(max_per_pose_family=1))
        self.assertEqual(3, len(result.selected))


class ABystanderInTheFrameCostsEverywhere(unittest.TestCase):
    """clean_frame used to matter only WITHIN a shot group -- the day the
    role split shrank the groups, a crew-in-frame take stopped meeting its
    clean twin in any comparison and walked onto the cover on fused quality
    alone. The pool-wide gain term makes contamination cost wherever the
    frame sits."""

    def _pool(self):
        # Six distinct poses so percentile gaps are meaningful: the
        # contaminated frame tops fused quality by one rank, the clean one
        # tops clean_frame by five.
        contaminated = cand(
            "dirty", 0.85, captured_utc=at(1, 8),
            embedding=unit(1.0, 0, 0, 0, 0, 0), embedding_space="s",
            clean_frame=0.005,
        )
        clean = cand(
            "clean", 0.84, captured_utc=at(1, 9),
            embedding=unit(0, 1.0, 0, 0, 0, 0), embedding_space="s",
            clean_frame=0.030,
        )
        axes = [(0, 0, 1.0, 0, 0, 0), (0, 0, 0, 1.0, 0, 0),
                (0, 0, 0, 0, 1.0, 0), (0, 0, 0, 0, 0, 1.0)]
        fillers = [
            cand(
                f"fill{i}", 0.70 - i * 0.02, captured_utc=at(1, 10 + i),
                embedding=unit(*axis), embedding_space="s",
                clean_frame=0.010 + i * 0.002,
            )
            for i, axis in enumerate(axes)
        ]
        return [contaminated, clean] + fillers

    def test_the_clean_frame_wins_the_slot(self):
        result = select(self._pool(), 1)
        self.assertEqual((mid("clean"),), result.selected)

    def test_zero_weight_restores_fused_quality_order(self):
        result = select(
            self._pool(), 1, policy=SelectionPolicy(weight_clean_frame=0.0)
        )
        self.assertEqual((mid("dirty"),), result.selected)


class AnIrreplaceableMomentBeatsATechnicalRule(unittest.TestCase):
    """A singleton shot with nothing else within rare_moment_isolation_s is
    the only record of its moment: the soft floors are waived. It can take a
    smaller page, never a rejection."""

    def test_an_isolated_soft_photo_survives_the_quality_floor(self):
        lone = cand("lone", 0.30, captured_utc=at(1, 12))  # under the 0.35 floor
        good1 = cand("gooda", 0.80, captured_utc=at(2, 12))
        good2 = cand("goodb", 0.75, captured_utc=at(2, 12.2))
        result = select([lone, good1, good2], 3)
        self.assertIn(mid("lone"), result.selected)
        self.assertIn(mid("lone"), result.rescued_media_ids,
                      "the waiver reports itself like the scarce-person one")

    def test_the_same_photo_with_a_neighbour_is_rejected(self):
        # Identical soft photo, but another candidate sits 10 minutes away:
        # the moment was recorded again, so the floor holds.
        lone = cand("lone", 0.30, captured_utc=at(1, 12))
        nearby = cand("nearby", 0.80, captured_utc=at(1, 12.2))
        good1 = cand("gooda", 0.80, captured_utc=at(2, 12))
        result = select([lone, nearby, good1], 3)
        self.assertNotIn(mid("lone"), result.selected)
        reasons = {r.media_id: r.reason for r in result.rejected}
        self.assertEqual("below_quality_floor", reasons[mid("lone")])


class AReceiptIsNeverAnAlbumPage(unittest.TestCase):
    """The screenshot/document gate is absolute (calibrated: every genuine
    photo on two real libraries scored <= -0.043) and waived for NOTHING --
    not scarcity, not rarity."""

    def test_the_only_photo_of_a_person_is_still_rejected(self):
        # High quality, only photo of gran, temporally isolated: every waiver
        # in the module would love to save it, and none may.
        shot = cand(
            "receipt", 0.95, screenshot_document=0.08,
            person_ids=(PERSON["gran"],), captured_utc=at(1, 12),
        )
        photo = cand("photo", 0.60, screenshot_document=-0.30, captured_utc=at(2, 12))
        result = select([shot, photo], 2)
        self.assertNotIn(mid("receipt"), result.selected)
        self.assertNotIn(mid("receipt"), result.rescued_media_ids)
        rejection = next(r for r in result.rejected if r.media_id == mid("receipt"))
        self.assertEqual("excluded_content", rejection.reason)
        self.assertIn(PERSON["gran"], result.missing_person_ids,
                      "the gap is reported, not papered over")


class ClippingIsUnrepairable(unittest.TestCase):
    """Clipped FACE data is gone -- no auto-develop brings it back -- so a
    heavily clipped face gates here while mild clipping passes (the
    repairability taxonomy). The gate reads the face region only: the first
    real studio library proved a whole-frame gate wrong (a white seamless
    backdrop clips half the frame BY INTENT and rejected 47% of a perfectly
    exposed maternity shoot)."""

    def test_a_clipped_face_is_rejected_and_a_mild_one_passes(self):
        clipped = cand(
            "clipped", 0.90, per_face=faces(exposure_clipped_max=0.40),
            captured_utc=at(1, 12),
        )
        mild = cand(
            "mild", 0.70, per_face=faces(exposure_clipped_max=0.10),
            captured_utc=at(1, 12.2),
        )
        result = select([clipped, mild], 2)
        self.assertEqual((mid("mild"),), result.selected)
        rejection = next(r for r in result.rejected if r.media_id == mid("clipped"))
        self.assertIn("data is gone", rejection.detail)

    def test_a_clipped_studio_backdrop_never_gates(self):
        # 47% of the frame at pure white -- the high-key seamless. The subject
        # faces are fine, so the photo must survive untouched.
        backdrop = cand(
            "backdrop", 0.90, clipped_fraction=0.47,
            per_face=faces(exposure_clipped_max=0.02), captured_utc=at(1, 12),
        )
        result = select([backdrop], 1)
        self.assertEqual((mid("backdrop"),), result.selected)

    def test_the_scarce_person_waiver_applies(self):
        only = cand(
            "only", 0.90, per_face=faces(exposure_clipped_max=0.40),
            person_ids=(PERSON["gran"],), captured_utc=at(1, 12),
        )
        other = cand("other", 0.70, captured_utc=at(1, 12.2))
        result = select([only, other], 2)
        self.assertIn(mid("only"), result.selected)
        self.assertIn(mid("only"), result.rescued_media_ids)


class AFaceTooDarkToDevelop(unittest.TestCase):
    """Backlit-silhouette detector: a face can read black while the global
    histogram looks fine. Below the floor there is no face data to lift;
    above it, auto-develop repairs the photo at render and rejecting it
    would throw away a memory to save a slider move."""

    def test_a_black_face_is_rejected_and_a_dim_one_passes(self):
        black = cand(
            "black", 0.90, per_face=faces(exposure_min=0.02), captured_utc=at(1, 12)
        )
        dim = cand(
            "dim", 0.70, per_face=faces(exposure_min=0.10), captured_utc=at(1, 12.2)
        )
        result = select([black, dim], 2)
        self.assertEqual((mid("dim"),), result.selected)
        rejection = next(r for r in result.rejected if r.media_id == mid("black"))
        self.assertIn("auto-develop", rejection.detail)

    def test_the_scarce_person_waiver_applies(self):
        only = cand(
            "only", 0.90, per_face=faces(exposure_min=0.02),
            person_ids=(PERSON["gran"],), captured_utc=at(1, 12),
        )
        other = cand("other", 0.70, captured_utc=at(1, 12.2))
        result = select([only, other], 2)
        self.assertIn(mid("only"), result.selected)


class PinsAreSovereign(unittest.TestCase):
    """The user looked at the exact pixels and said 'this one'. No gate, no
    floor, no cap argues back."""

    def test_a_pinned_photo_survives_every_gate_and_counts_against_target(self):
        awful = cand("awful", 0.05, face_cut=True, user_hidden=True)
        goods = [cand(f"good{i}", 0.9 - i * 0.01) for i in range(3)]
        policy = SelectionPolicy(pinned_media_ids=frozenset({mid("awful")}))
        result = select([awful] + goods, 2, policy=policy)
        self.assertIn(mid("awful"), result.selected)
        self.assertEqual(2, len(result.selected),
                         "a pin spends a page, it does not add one")

    def test_an_unknown_pin_raises(self):
        # A pin that silently no-ops is a lost user decision.
        policy = SelectionPolicy(pinned_media_ids=frozenset({mid("ghost")}))
        with self.assertRaises(SelectionError):
            select([cand("real", 0.8)], 1, policy=policy)


class ExcludesAreAbsolute(unittest.TestCase):
    """The swap's other half: the outgoing photo is never selectable again,
    whatever else says otherwise."""

    def test_an_excluded_photo_is_out_even_when_forced_in(self):
        best = cand("best", 0.95, user_override=True, user_favorite=True)
        other = cand("other", 0.50)
        policy = SelectionPolicy(excluded_media_ids=frozenset({mid("best")}))
        result = select([best, other], 2, policy=policy)
        self.assertNotIn(mid("best"), result.selected)
        rejection = next(r for r in result.rejected if r.media_id == mid("best"))
        self.assertEqual("user_hidden", rejection.reason)
        self.assertEqual("excluded by user (swap)", rejection.detail)

    def test_a_pin_that_is_also_an_exclude_is_refused(self):
        # Both cannot hold, and picking one silently loses a user decision.
        policy = SelectionPolicy(
            pinned_media_ids=frozenset({mid("x")}),
            excluded_media_ids=frozenset({mid("x")}),
        )
        with self.assertRaises(SelectionError):
            select([cand("x", 0.8)], 1, policy=policy)


if __name__ == "__main__":
    unittest.main()
