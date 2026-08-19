"""Selection criteria v2: the frames nobody would print stay out.

THE PHOTO THIS FILE EXISTS BECAUSE OF

A real album printed a photo of a mother caught mid-motion tying her hair:
eyes down, arms up, hair a smear. Every gate it faced, it passed -- her FACE
was sharp (0.69) and unoccluded (detection 0.88), global sharpness 0.96
(the headboard was crisp). The blur lived in her hair, the closed eyes and
the awkward instant lived in no measure at all. Selection v2 adds exactly
those senses:

  - head_sharpness: the face box grown to hair and shoulders. A sharp face
    inside a smeared head is a subject in motion. Floored like face blur.
  - face_cut: features (landmarks) cropped by the frame edge -> rejected;
    "full faces visible" is an album rule.
  - expression axes (zero-shot contrasts, RANKED within the pool, never
    thresholded absolutely): smiles out-earn cries, composed frames out-earn
    mid-gesture candids, eyes shut outside a sleeping shot cost more than
    any bonus pays, and sleeping-baby pages are capped, never banned.
"""

from __future__ import annotations

import unittest

from memory_engine_album.selection import (
    SelectionPolicy,
    _axis_percentiles,
    select,
)

from test_selection import cand, mid


class HeadSharpnessFloor(unittest.TestCase):
    def test_a_smeared_head_around_a_sharp_face_is_rejected(self):
        moving = cand("moving", 0.9, face_sharpness=0.69, head_sharpness=0.02)
        still = cand("still", 0.6, face_sharpness=0.65, head_sharpness=0.30)
        result = select([moving, still], 2)
        self.assertEqual((mid("still"),), result.selected)
        reasons = {r.media_id: r for r in result.rejected}
        self.assertIn("moving", reasons[mid("moving")].detail or "")

    def test_no_head_measurement_is_not_blur_evidence(self):
        unmeasured = cand("unmeasured", 0.8, head_sharpness=None)
        result = select([unmeasured], 1)
        self.assertEqual((mid("unmeasured"),), result.selected)


class CutFaces(unittest.TestCase):
    def test_a_cut_face_is_rejected_and_a_full_face_survives(self):
        cut = cand("cut", 0.9, face_cut=True)
        full = cand("full", 0.5, face_cut=False)
        result = select([cut, full], 2)
        self.assertEqual((mid("full"),), result.selected)

    def test_the_scarce_person_waiver_still_applies(self):
        # The only photo of this person has a cut face: it is rescued, because
        # the person appearing beats the crop rule -- same waiver as blur.
        only = cand("only", 0.9, face_cut=True, person_ids=("p-1",))
        other = cand("other", 0.6)
        result = select([only, other], 2)
        self.assertIn(mid("only"), result.selected)
        self.assertIn(mid("only"), result.rescued_media_ids)

    def test_the_gate_can_be_disarmed_by_policy(self):
        cut = cand("cut", 0.9, face_cut=True)
        policy = SelectionPolicy(reject_cut_faces=False)
        result = select([cand("cut", 0.9, face_cut=True)], 1, policy=policy)
        self.assertEqual((mid("cut"),), result.selected)
        del cut


class ExpressionRanking(unittest.TestCase):
    def test_a_smile_outranks_a_cry_at_equal_quality(self):
        smiling = cand("smiling", 0.7, smile=0.08, awake=0.05, sleeping=-0.05, composed=0.0)
        crying = cand("crying", 0.7, smile=-0.08, awake=0.05, sleeping=-0.05, composed=0.0)
        result = select([smiling, crying], 1)
        self.assertEqual((mid("smiling"),), result.selected)

    def test_mid_blink_loses_to_a_lower_scored_open_eyed_frame(self):
        # Eyes read shut, and it does NOT read as a sleeping shot: the penalty
        # must overturn a solid quality lead.
        blink = cand("blink", 0.85, smile=0.0, awake=-0.09, sleeping=-0.06, composed=0.0)
        open_a = cand("opena", 0.65, smile=0.0, awake=0.07, sleeping=-0.05, composed=0.0)
        open_b = cand("openb", 0.64, smile=0.0, awake=0.06, sleeping=-0.05, composed=0.0)
        open_c = cand("openc", 0.63, smile=0.0, awake=0.05, sleeping=-0.04, composed=0.0)
        result = select([blink, open_a, open_b, open_c], 1)
        self.assertEqual((mid("opena"),), result.selected)

    def test_sleeping_shots_are_capped_not_banned(self):
        # Six sleeping frames rank high on quality; the cap (20% of 5 -> 1)
        # lets exactly one in and fills the rest with awake photos.
        sleepers = [
            cand(f"sleep{i}", 0.9, smile=0.0, awake=-0.09 - i * 0.001,
                 sleeping=0.09 + i * 0.001, composed=0.0)
            for i in range(6)
        ]
        awake = [
            cand(f"wake{i}", 0.5, smile=0.0, awake=0.06 + i * 0.001,
                 sleeping=-0.06, composed=0.0)
            for i in range(6)
        ]
        result = select(sleepers + awake, 5)
        sleeping_chosen = [m for m in result.selected if "736c656570" in m]  # hex("sleep")
        self.assertEqual(1, len(sleeping_chosen))
        self.assertEqual(5, len(result.selected))

    def test_no_expression_data_stays_neutral(self):
        # Candidates without axes must select purely on the old signals --
        # and absence must not crash or bias either way.
        better = cand("better", 0.8)
        worse = cand("worse", 0.6)
        result = select([better, worse], 1)
        self.assertEqual((mid("better"),), result.selected)


class AxisPercentiles(unittest.TestCase):
    def test_ties_share_a_percentile_and_none_is_neutral(self):
        a = cand("a", 0.8, smile=0.05)
        b = cand("b", 0.8, smile=0.05)
        c = cand("c", 0.8, smile=-0.05)
        d = cand("d", 0.8, smile=None)
        pct = _axis_percentiles([a, b, c, d], lambda cd: cd.smile)
        self.assertEqual(pct[mid("a")], pct[mid("b")])
        self.assertGreater(pct[mid("a")], pct[mid("c")])
        self.assertEqual(0.5, pct[mid("d")])

    def test_percentiles_are_deterministic(self):
        pool = [cand(f"p{i}", 0.8, smile=(i % 5) * 0.01) for i in range(20)]
        first = _axis_percentiles(pool, lambda cd: cd.smile)
        self.assertEqual(first, _axis_percentiles(list(reversed(pool)), lambda cd: cd.smile))


class CrowdVariety(unittest.TestCase):
    def test_a_group_shot_beats_a_marginally_better_fifth_solo(self):
        # Four solos are in; the scene axis (scene|faces bucket) should make
        # the first duo photo out-earn a fifth near-identical solo.
        solos = [
            cand(f"solo{i}", 0.80 - i * 0.001, person_ids=("p-1",))
            for i in range(5)
        ]
        duo = cand("duo", 0.75, person_ids=("p-1", "p-2"))
        result = select(solos + [duo], 5, policy=SelectionPolicy(max_per_person_fraction=1.0))
        self.assertIn(mid("duo"), result.selected)


if __name__ == "__main__":
    unittest.main()
