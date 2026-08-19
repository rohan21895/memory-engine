"""An out-of-focus face does not go in the album; a bokeh portrait does.

THE FAILURE THIS FILE EXISTS TO PREVENT

Global Laplacian sharpness flags exactly the best portraits: a close-up with
tack-sharp eyes and a creamy background measured 0.386 whole-image sharpness
on a real library -- the softest photo in its album by the global measure and
p90-sharp by the face measure. Meanwhile genuinely motion-blurred faces sailed
into selection because their backgrounds were sharp. The gate therefore runs
on `face_sharpness` (measured on the face region, scale-normalised), not on
the global number, and a missing measurement passes rather than blocks.
"""

from __future__ import annotations

import unittest

from memory_engine_album.selection import SelectionPolicy, select

from test_selection import PERSON, cand, mid


class TestFaceSharpnessFloor(unittest.TestCase):
    def test_a_blurred_face_is_rejected_with_the_reason_recorded(self):
        blurred = cand("blurry", 0.9, face_sharpness=0.05)
        sharp = cand("sharp", 0.7, face_sharpness=0.8)
        selection = select([blurred, sharp], 2)

        self.assertEqual([sharp.media_id], list(selection.selected))
        rejection = next(
            r for r in selection.rejected if r.media_id == blurred.media_id
        )
        self.assertEqual("below_quality_floor", rejection.reason)
        self.assertIn("out of focus", rejection.detail)

    def test_no_measurement_passes_the_floor(self):
        unmeasured = cand("plain", 0.8)  # face_sharpness=None
        selection = select([unmeasured], 1)
        self.assertEqual([unmeasured.media_id], list(selection.selected))

    def test_scarce_person_rescue_waives_the_face_floor(self):
        """The only photo of grandmother is worth printing at any sharpness --
        the module's own words, and they apply to the face floor too."""
        only_gran = cand(
            "gran", 0.9, face_sharpness=0.05, person_ids=(PERSON["gran"],)
        )
        other = cand("other", 0.8, face_sharpness=0.9, person_ids=(PERSON["ava"],))
        selection = select([only_gran, other], 2)
        self.assertIn(only_gran.media_id, selection.selected)

    def test_rescue_does_not_fire_when_the_person_has_a_passing_photo(self):
        soft = cand(
            "soft", 0.95, face_sharpness=0.05, person_ids=(PERSON["ava"],)
        )
        passing = cand(
            "pass", 0.6, face_sharpness=0.7, person_ids=(PERSON["ava"],)
        )
        selection = select([soft, passing], 2)
        self.assertNotIn(
            soft.media_id, selection.selected,
            "a person with an in-focus photo must not drag their blurred one in",
        )
        self.assertIn(passing.media_id, selection.selected)


if __name__ == "__main__":
    unittest.main()
