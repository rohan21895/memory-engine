"""Tests for the album layout engine.

These are written against the failure mode that actually costs money here: not a
crash, but a layout full of plausible numbers that gets printed and bound before
anyone notices. So most of these tests are differential or physical rather than
"does it return something" --

  * the spine tests place the SAME face on a left and a right page and require
    opposite verdicts, because a gutter check that ignores the side still passes
    every single-page test;
  * the aspect tests use a 3:2 source in a square frame, where the normalised
    crop ratio and the printed aspect differ by 50%, because on a square source
    the wrong formula and the right one agree;
  * the DPI tests assert the number changes when the CROP changes with the
    source untouched, because a DPI computed from full source pixels is
    flattering, stable, and wrong;
  * the NaN tests exist because `NaN < floor` is False, so a NaN dimension
    passes every gate in this module unless something explicitly rejects it.

Run: python3 tests/test_layout.py
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
import unittest
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = PACKAGE_ROOT.parent.parent
PROFILE_DIR = PACKAGE_ROOT / "vendor_profiles"
SCHEMA_DIR = REPO_ROOT / "contracts" / "schemas"


def _load_layout():
    """Load layout.py directly from its path.

    Deliberately not `import memory_engine_album.layout`: other agents are
    writing sibling modules in this package right now and own its __init__.py,
    so a plain package import would couple these tests to the state of a file
    this module has nothing to do with. Registering in sys.modules before
    exec_module is required -- @dataclass looks its own module up there, and
    without it the decorator raises on the first frozen dataclass.
    """
    path = PACKAGE_ROOT / "memory_engine_album" / "layout.py"
    spec = importlib.util.spec_from_file_location("memory_engine_album_layout", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


L = _load_layout()

LAYFLAT = json.loads((PROFILE_DIR / "layflat-300-square.json").read_text(encoding="utf-8"))
PERFECT = json.loads(
    (PROFILE_DIR / "perfectbound-210-square.json").read_text(encoding="utf-8")
)


def mid(n: int) -> str:
    """A distinct, contract-shaped BLAKE3 id."""
    return f"{n:064x}"


def photo(n: int = 1, w: int = 6000, h: int = 4000, **kwargs) -> "L.Photo":
    return L.Photo(media_id=mid(n), pixel_width=w, pixel_height=h, **kwargs)


def face(n: int, x: float, y: float, w: float, h: float, subject: bool = True) -> "L.Face":
    return L.Face(face_id=mid(1000 + n), box=L.NormBox(x, y, w, h), is_subject=subject)


# ---------------------------------------------------------------------------
# page geometry
# ---------------------------------------------------------------------------


class TestPageGeometry(unittest.TestCase):
    def test_bleed_box_is_trim_plus_bleed_on_every_side(self):
        geom = L.page_geometry(LAYFLAT, "left")
        self.assertEqual((0.0, 0.0, 306.0, 306.0), _rect(geom.bleed_box))
        self.assertEqual((3.0, 3.0, 300.0, 300.0), _rect(geom.trim_box))
        # Origin is the bleed box, so nothing in a valid layout is negative.
        self.assertEqual(0.0, geom.bleed_box.x_mm)

    def test_safe_box_is_the_trim_inset_by_the_guillotine_tolerance(self):
        geom = L.page_geometry(LAYFLAT, "left")
        self.assertEqual((11.0, 11.0, 284.0, 284.0), _rect(geom.safe_box))

    def test_a_left_page_binds_on_its_right_edge(self):
        """The single mapping the whole gutter story hangs on.

        Reversed, every number below still looks reasonable and every face gets
        pushed toward the spine instead of away from it.
        """
        left = L.page_geometry(PERFECT, "left")
        right = L.page_geometry(PERFECT, "right")

        self.assertEqual("right", left.spine_side)
        self.assertEqual("left", right.spine_side)

        # 210 trim + 3 bleed => trim runs 3..213, so a 14mm gutter on the right
        # page edge starts at 199 and runs out to the bleed edge at 216.
        self.assertEqual(199.0, left.gutter_band.x_mm)
        self.assertEqual(216.0, left.gutter_band.right_mm)
        # Mirrored on the facing page.
        self.assertEqual(0.0, right.gutter_band.x_mm)
        self.assertEqual(17.0, right.gutter_band.right_mm)

    def test_content_box_is_pulled_off_the_spine_not_off_the_outer_edge(self):
        left = L.page_geometry(PERFECT, "left")
        right = L.page_geometry(PERFECT, "right")
        # safe box is 13..203; gutter 14 exceeds the 10mm margin by 4mm.
        self.assertEqual(13.0, left.content_box.x_mm)
        self.assertEqual(199.0, left.content_box.right_mm)
        self.assertEqual(17.0, right.content_box.x_mm)
        self.assertEqual(203.0, right.content_box.right_mm)

    def test_a_gutter_smaller_than_the_margin_takes_nothing_extra(self):
        """Layflat: gutter 5mm, margin 8mm. The margin already covers it.

        Subtracting the gutter unconditionally would shave 5mm off every inner
        edge of every layflat page -- a real 5mm of lost photo, produced by an
        arithmetic double-count that no number in the output would reveal.
        """
        geom = L.page_geometry(LAYFLAT, "left")
        self.assertEqual(_rect(geom.safe_box), _rect(geom.content_box))
        self.assertLess(LAYFLAT["gutter_mm"], LAYFLAT["safe_margin_mm"])

    def test_covers_and_singles_have_no_gutter(self):
        for side in ("front_cover", "back_cover", "single"):
            with self.subTest(side=side):
                geom = L.page_geometry(PERFECT, side)
                self.assertIsNone(geom.spine_side)
                self.assertIsNone(geom.gutter_band)
                self.assertEqual(_rect(geom.safe_box), _rect(geom.content_box))

    def test_unknown_side_raises(self):
        with self.assertRaises(L.LayoutError):
            L.page_geometry(LAYFLAT, "middle")


# ---------------------------------------------------------------------------
# cropping and aspect
# ---------------------------------------------------------------------------


class TestCropAspect(unittest.TestCase):
    def test_crop_aspect_is_a_pixel_space_ratio_not_a_normalised_one(self):
        """The bug this whole module is shaped around.

        A 3:2 source cropped to a square frame has a normalised ratio of 0.667
        and a printed ratio of 1.0. Comparing the normalised ratio against the
        frame aspect passes on square sources and squashes everything else, and
        nothing about the output looks wrong until the book arrives.
        """
        src = photo(1, 6000, 4000)
        crop = L.cover_crop(src, 1.0)

        naive = crop.w / crop.h
        printed = (crop.w * src.pixel_width) / (crop.h * src.pixel_height)

        self.assertAlmostEqual(1.0, printed, places=9)
        self.assertAlmostEqual(2.0 / 3.0, naive, places=6)
        self.assertNotAlmostEqual(naive, printed, places=2)

    def test_cover_crop_is_maximal_so_dpi_is_never_thrown_away(self):
        src = photo(1, 6000, 4000)
        # Landscape source into a square frame: the height is the limit.
        self.assertAlmostEqual(1.0, L.cover_crop(src, 1.0).h, places=9)
        # Portrait frame from the same source: the height is still the limit.
        self.assertAlmostEqual(1.0, L.cover_crop(src, 0.5).h, places=9)
        # Wider frame than the source: the width becomes the limit.
        self.assertAlmostEqual(1.0, L.cover_crop(src, 3.0).w, places=9)

    def test_cover_crop_stays_inside_the_image(self):
        for aspect in (0.25, 0.5, 1.0, 1.5, 2.0, 4.0):
            for w, h in ((6000, 4000), (4000, 6000), (3000, 3000), (8000, 1000)):
                with self.subTest(aspect=aspect, size=(w, h)):
                    crop = L.cover_crop(photo(1, w, h), aspect)
                    self.assertGreaterEqual(crop.x, 0.0)
                    self.assertGreaterEqual(crop.y, 0.0)
                    self.assertLessEqual(crop.right, 1.0 + 1e-9)
                    self.assertLessEqual(crop.bottom, 1.0 + 1e-9)

    def test_crop_centres_on_the_faces_not_on_the_sensor(self):
        """A group at the left of a wide frame must survive a square crop."""
        subject = photo(1, 6000, 4000, faces=(face(1, 0.05, 0.4, 0.10, 0.15),))
        plain = photo(2, 6000, 4000)

        with_face = L.cover_crop(subject, 1.0)
        without = L.cover_crop(plain, 1.0)

        self.assertLess(with_face.x, without.x)
        self.assertAlmostEqual(0.0, with_face.x, places=6)  # clamped at the edge
        self.assertGreater(without.x, 0.15)  # centred

    def test_salience_is_only_used_when_there_are_no_subject_faces(self):
        salient_only = photo(1, 6000, 4000, salience=L.NormBox(0.75, 0.4, 0.2, 0.2))
        both = photo(
            2,
            6000,
            4000,
            faces=(face(1, 0.05, 0.4, 0.10, 0.15),),
            salience=L.NormBox(0.75, 0.4, 0.2, 0.2),
        )
        self.assertGreater(L.cover_crop(salient_only, 1.0).x, 0.2)
        # The face wins; a union with the salience box would drag the crop right
        # and put the person on the frame edge.
        self.assertAlmostEqual(0.0, L.cover_crop(both, 1.0).x, places=6)

    def test_emitted_crops_stay_inside_the_image_across_a_wide_sweep(self):
        """Quantisation must not push a crop out of [0,1].

        Swept rather than spot-checked because the failure is a rounding
        interaction: it would appear for a handful of source dimensions and
        aspects and for no others, which is precisely what a single hand-picked
        example misses.
        """
        geom = L.page_geometry(PERFECT, "left")
        sizes = ((6000, 4000), (4000, 6000), (4032, 3024), (3000, 3000), (7680, 2160), (1920, 5760))
        for w, h in sizes:
            for aspect in (0.31, 0.5, 0.7777, 1.0, 1.3333, 1.5, 2.1, 3.7):
                with self.subTest(size=(w, h), aspect=aspect):
                    src = photo(1, w, h)
                    placement = L.place(
                        src,
                        L.inset_frame(geom, aspect),
                        geom,
                        placement_id="p",
                        dpi_floor=1.0,
                    )
                    crop = placement["crop"]
                    self.assertGreaterEqual(crop["x"], 0.0)
                    self.assertGreaterEqual(crop["y"], 0.0)
                    self.assertLessEqual(crop["x"] + crop["w"], 1.0)
                    self.assertLessEqual(crop["y"] + crop["h"], 1.0)
                    self.assertGreater(crop["w"], 0.0)
                    self.assertGreater(crop["h"], 0.0)

    def test_the_crop_matches_the_frame_that_actually_ships(self):
        """Quantise the frame first, then derive the crop from it.

        Derive the crop first and the crop matches a full-precision frame that
        never reaches the JSON, while the frame that does reach it has been
        rounded to the millimetre grid underneath. The two then disagree by the
        rounding, which is a real if small distortion -- and it is invisible,
        because each number is individually correct.
        """
        geom = L.page_geometry(LAYFLAT, "left")
        src = photo(1, 6000, 4000)
        # Chosen so rounding to 1e-3 mm moves the aspect by ~1e-4 relative,
        # two orders above the ~1e-6 the crop quantisation can account for.
        ragged = L.RectMm(20.0, 20.0, 5.00049, 5.0)

        placement = L.place(src, ragged, geom, placement_id="p", dpi_floor=1.0)
        crop = placement["crop"]
        emitted_aspect = (
            placement["frame"]["width_mm"] / placement["frame"]["height_mm"]
        )
        printed_aspect = (crop["w"] * 6000) / (crop["h"] * 4000)
        self.assertLess(
            abs(printed_aspect - emitted_aspect) / emitted_aspect, 1e-5
        )

    def test_every_emitted_placement_matches_its_frame_aspect(self):
        geom = L.page_geometry(LAYFLAT, "left")
        for w, h in ((6000, 4000), (4000, 6000), (5000, 5000), (7000, 2000)):
            for frame in (L.full_bleed_frame(geom), L.inset_frame(geom, 1.4)):
                with self.subTest(size=(w, h), frame=frame):
                    src = photo(1, w, h)
                    placement = L.place(
                        src, frame, geom, placement_id="p0-0-aaaaaaaa", dpi_floor=1.0
                    )
                    crop = placement["crop"]
                    printed = (crop["w"] * w) / (crop["h"] * h)
                    frame_aspect = (
                        placement["frame"]["width_mm"] / placement["frame"]["height_mm"]
                    )
                    self.assertLess(abs(printed - frame_aspect) / frame_aspect, 5e-3)


# ---------------------------------------------------------------------------
# effective DPI
# ---------------------------------------------------------------------------


class TestEffectiveDpi(unittest.TestCase):
    def test_dpi_counts_cropped_pixels_not_source_pixels(self):
        """A crop throws pixels away; a DPI that ignores that is the flattering
        number. Same 24MP source, same frame, two crops -- the DPI must move."""
        src = photo(1, 6000, 4000)
        frame = L.RectMm(0.0, 0.0, 306.0, 306.0)

        square_crop = L.cover_crop(src, 1.0)  # keeps 4000 of 6000 px across
        dpi = L.effective_dpi(src, square_crop, frame)

        self.assertAlmostEqual(4000 * 25.4 / 306.0, dpi, delta=0.02)
        # The full-source answer would be 498; using it would clear a 300 floor
        # that the actual placement clears only because 332 happens to as well.
        self.assertNotAlmostEqual(6000 * 25.4 / 306.0, dpi, places=0)

    def test_dpi_matches_the_golden_fixture_cover(self):
        """contracts/fixtures album cover: 306mm full bleed at 332 DPI."""
        src = photo(1, 6000, 4000)
        geom = L.page_geometry(LAYFLAT, "front_cover")
        placement = L.place(
            src,
            L.full_bleed_frame(geom),
            geom,
            placement_id="cover-hero",
            dpi_floor=300.0,
        )
        self.assertEqual({"x": 0.166667, "y": 0.0, "w": 0.666667, "h": 1.0, "rotation_deg": 0}, placement["crop"])
        self.assertAlmostEqual(332.0, placement["effective_dpi"], delta=0.1)

    def test_dpi_is_quantised_downward_never_to_nearest(self):
        """299.998 must not present itself as 300.0 and clear the floor."""
        src = photo(1, 1000, 1000)
        # width chosen so the true DPI lands just under 300.
        width_mm = 1000 * 25.4 / 299.998
        frame = L.RectMm(0.0, 0.0, width_mm, width_mm)
        dpi = L.effective_dpi(src, L.NormBox(0.0, 0.0, 1.0, 1.0), frame)
        self.assertLess(dpi, 300.0)
        self.assertAlmostEqual(299.99, dpi, places=2)

    def test_axis_disagreement_raises_instead_of_reporting_the_kind_edge(self):
        src = photo(1, 6000, 4000)
        frame = L.RectMm(0.0, 0.0, 200.0, 100.0)  # 2:1
        squashed = L.NormBox(0.0, 0.0, 1.0, 1.0)  # 1.5:1 in pixel space
        with self.assertRaises(L.LayoutError):
            L.effective_dpi(src, squashed, frame)

    def test_max_frame_for_dpi_agrees_with_effective_dpi(self):
        """Two formulas for the same physical fact; if they drift, layout sizes
        frames just past the floor and the validator rejects the book."""
        for w, h in ((6000, 4000), (1600, 1200), (4000, 6000)):
            for aspect in (0.75, 1.0, 1.5, 2.0):
                with self.subTest(size=(w, h), aspect=aspect):
                    src = photo(1, w, h)
                    max_w, max_h = L.max_frame_for_dpi(src, aspect, 300.0)
                    frame = L.RectMm(0.0, 0.0, max_w, max_h)
                    crop = L.cover_crop(src, aspect)
                    self.assertAlmostEqual(
                        300.0, L.effective_dpi(src, crop, frame), delta=0.05
                    )

    def test_a_two_megapixel_phone_photo_is_refused_across_a_full_spread(self):
        """The headline case: layout must not propose what the validator rejects."""
        src = photo(1, 1600, 1200)
        geom = L.page_geometry(LAYFLAT, "front_cover")
        with self.assertRaises(L.LayoutError) as ctx:
            L.place(
                src,
                L.full_bleed_frame(geom),
                geom,
                placement_id="cover-hero",
                dpi_floor=300.0,
            )
        self.assertIn("DPI", str(ctx.exception))

    def test_the_same_photo_is_accepted_at_a_size_it_can_hold(self):
        src = photo(1, 1600, 1200)
        geom = L.page_geometry(LAYFLAT, "left")
        page = L.layout_page(
            (src,), LAYFLAT, page_index=1, side="left", template=L.SINGLE_INSET
        )
        placement = page["placements"][0]
        self.assertGreaterEqual(placement["effective_dpi"], LAYFLAT["dpi_floor"])
        self.assertIn("frame_shrunk_for_dpi", page["layout"]["constraints_relaxed"])
        self.assertLess(placement["frame"]["width_mm"], geom.content_box.width_mm)


# ---------------------------------------------------------------------------
# bleed
# ---------------------------------------------------------------------------


class TestBleed(unittest.TestCase):
    def test_full_bleed_reaches_the_bleed_line_on_all_four_edges(self):
        geom = L.page_geometry(LAYFLAT, "front_cover")
        placement = L.place(
            photo(1, 6000, 4000),
            L.full_bleed_frame(geom),
            geom,
            placement_id="cover-hero",
            dpi_floor=300.0,
        )
        self.assertEqual(["top", "bottom", "left", "right"], placement["bleeds"])
        self.assertEqual(0.0, placement["frame"]["x_mm"])
        self.assertEqual(306.0, placement["frame"]["width_mm"])
        # 3mm past the trim on each side, which is what bleed_mm buys.
        self.assertEqual(
            LAYFLAT["trim_size_mm"]["width_mm"] + 2 * LAYFLAT["bleed_mm"],
            placement["frame"]["width_mm"],
        )

    def test_a_frame_that_stops_at_the_trim_line_is_refused(self):
        """The white sliver. It is not a warning; the book is already printed."""
        geom = L.page_geometry(LAYFLAT, "front_cover")
        at_trim = L.RectMm(3.0, 3.0, 300.0, 300.0)
        with self.assertRaises(L.LayoutError) as ctx:
            L.place(
                photo(1, 6000, 4000), at_trim, geom, placement_id="p", dpi_floor=1.0
            )
        self.assertIn("cut zone", str(ctx.exception))

    def test_a_frame_stranded_inside_the_bleed_is_refused(self):
        """Halfway between the trim line and the bleed line: too far out to be
        safe, too far in to cover the cut. There is nothing correct to record."""
        geom = L.page_geometry(LAYFLAT, "front_cover")
        stranded = L.RectMm(1.5, 1.5, 303.0, 303.0)
        with self.assertRaises(L.LayoutError):
            L.place(
                photo(1, 6000, 4000), stranded, geom, placement_id="p", dpi_floor=1.0
            )

    def test_an_inset_frame_declares_no_bleed(self):
        geom = L.page_geometry(LAYFLAT, "left")
        placement = L.place(
            photo(1, 6000, 4000),
            L.inset_frame(geom, 1.5),
            geom,
            placement_id="p",
            dpi_floor=300.0,
        )
        self.assertEqual([], placement["bleeds"])

    def test_bleed_edges_are_emitted_in_a_fixed_order(self):
        geom = L.page_geometry(LAYFLAT, "front_cover")
        box = geom.bleed_box
        half = L.RectMm(0.0, 0.0, box.width_mm, geom.safe_box.bottom_mm)
        placement = L.place(
            photo(1, 6000, 4000), half, geom, placement_id="p", dpi_floor=1.0
        )
        self.assertEqual(["top", "left", "right"], placement["bleeds"])


# ---------------------------------------------------------------------------
# faces: trim zone and gutter
# ---------------------------------------------------------------------------


def _spine_face_photo(n: int) -> "L.Photo":
    """Square source with one face at 199.5-202mm of a 216mm full-bleed page.

    That band is inside the safe box (13..203) but inside the gutter band of a
    LEFT perfect-bound page (199..216) and clear of a RIGHT page's (0..17). It
    is the only position that separates a real spine check from a check that
    merely tests the safe margin.
    """
    return L.Photo(
        media_id=mid(n),
        pixel_width=4000,
        pixel_height=4000,
        faces=(face(1, 199.5 / 216.0, 0.45, 2.5 / 216.0, 0.06),),
    )


def _gutter_slack_photo(n: int) -> "L.Photo":
    """3:2 source whose crop still has slack when a face is near the spine.

    The subject face at 0.42 is what the crop centres itself on; the background
    face at 0.74 then lands at ~202-215mm of a 216mm full-bleed page, inside the
    gutter of a perfect-bound left page, with 2000px of unspent slack in the
    source. That combination -- unsafe AND fixable -- is what makes the
    alignment step observable at all.
    """
    return L.Photo(
        media_id=mid(n),
        pixel_width=6000,
        pixel_height=4000,
        faces=(
            face(1, 0.42, 0.44, 0.06, 0.09),
            face(2, 0.74, 0.44, 0.04, 0.06, subject=False),
        ),
    )


class TestFaceSafety(unittest.TestCase):
    def test_the_same_face_is_unsafe_on_a_left_page_and_safe_on_a_right_page(self):
        src = _spine_face_photo(1)
        results = {}
        for side in ("left", "right"):
            geom = L.page_geometry(PERFECT, side)
            crop = L.NormBox(0.0, 0.0, 1.0, 1.0)
            results[side] = L.face_safety(src, crop, L.full_bleed_frame(geom), geom)

        self.assertEqual(1, results["left"]["faces_in_gutter"])
        self.assertFalse(results["left"]["all_faces_in_safe_zone"])
        self.assertLess(results["left"]["min_face_margin_mm"], 0.0)

        self.assertEqual(0, results["right"]["faces_in_gutter"])
        self.assertTrue(results["right"]["all_faces_in_safe_zone"])
        self.assertGreater(results["right"]["min_face_margin_mm"], 0.0)

    def test_the_same_face_is_safe_on_both_sides_of_a_layflat_book(self):
        """Same geometry, smaller gutter. If the gutter value were ignored and a
        constant used, this and the test above cannot both pass."""
        src = _spine_face_photo(1)
        for side in ("left", "right"):
            with self.subTest(side=side):
                geom = L.page_geometry(LAYFLAT, side)
                # 199.5mm on a 216mm page is 0.923 of the way across; on the
                # 306mm layflat page that is 282.6mm, inside its 295mm safe box.
                safety = L.face_safety(
                    src, L.NormBox(0.0, 0.0, 1.0, 1.0), L.full_bleed_frame(geom), geom
                )
                self.assertEqual(0, safety["faces_in_gutter"])
                self.assertTrue(safety["all_faces_in_safe_zone"])

    def test_a_face_at_the_page_edge_is_in_the_trim_zone(self):
        geom = L.page_geometry(LAYFLAT, "front_cover")
        src = L.Photo(
            media_id=mid(1),
            pixel_width=4000,
            pixel_height=4000,
            faces=(face(1, 0.0, 0.45, 0.05, 0.06),),
        )
        safety = L.face_safety(
            src, L.NormBox(0.0, 0.0, 1.0, 1.0), L.full_bleed_frame(geom), geom
        )
        self.assertEqual(1, safety["faces_in_trim_zone"])
        self.assertFalse(safety["all_faces_in_safe_zone"])
        self.assertLess(safety["min_face_margin_mm"], 0.0)

    def test_no_faces_is_vacuously_safe_and_has_no_margin(self):
        geom = L.page_geometry(LAYFLAT, "left")
        safety = L.face_safety(
            photo(1), L.NormBox(0.0, 0.0, 1.0, 1.0), L.full_bleed_frame(geom), geom
        )
        self.assertEqual(0, safety["face_count"])
        self.assertTrue(safety["all_faces_in_safe_zone"])
        self.assertIsNone(safety["min_face_margin_mm"])

    def test_a_face_the_crop_cut_through_is_recorded(self):
        geom = L.page_geometry(LAYFLAT, "front_cover")
        # 3:2 source into a square frame drops a sixth off each side; a face at
        # x in [0.05, 0.20] straddles the left crop edge at 0.1667.
        src = L.Photo(
            media_id=mid(1),
            pixel_width=6000,
            pixel_height=4000,
            faces=(
                face(1, 0.05, 0.45, 0.15, 0.10, subject=False),
                face(2, 0.45, 0.45, 0.10, 0.10),
            ),
        )
        crop = L.cover_crop(src, 1.0, focus=L.NormBox(0.45, 0.45, 0.10, 0.10))
        safety = L.face_safety(src, crop, L.full_bleed_frame(geom), geom)
        self.assertEqual([mid(1001)], safety["cropped_face_ids"])
        self.assertEqual(2, safety["face_count"])

    def test_a_face_entirely_outside_the_crop_is_not_counted(self):
        geom = L.page_geometry(LAYFLAT, "front_cover")
        src = L.Photo(
            media_id=mid(1),
            pixel_width=6000,
            pixel_height=4000,
            faces=(
                face(1, 0.01, 0.45, 0.05, 0.10, subject=False),
                face(2, 0.45, 0.45, 0.10, 0.10),
            ),
        )
        crop = L.cover_crop(src, 1.0, focus=L.NormBox(0.45, 0.45, 0.10, 0.10))
        safety = L.face_safety(src, crop, L.full_bleed_frame(geom), geom)
        self.assertEqual(1, safety["face_count"])
        self.assertEqual([], safety["cropped_face_ids"])

    def test_face_counts_include_non_subject_faces(self):
        """The validator sees faces, not our opinion of whose face matters."""
        geom = L.page_geometry(LAYFLAT, "front_cover")
        src = L.Photo(
            media_id=mid(1),
            pixel_width=4000,
            pixel_height=4000,
            faces=(face(1, 0.0, 0.45, 0.03, 0.05, subject=False),),
        )
        safety = L.face_safety(
            src, L.NormBox(0.0, 0.0, 1.0, 1.0), L.full_bleed_frame(geom), geom
        )
        self.assertEqual(1, safety["faces_in_trim_zone"])

    def test_the_crop_slides_to_pull_a_face_out_of_the_gutter(self):
        """A wide source has slack the crop can spend.

        The subject face at 0.42 is what the crop centres on, so a background
        face at 0.74 lands near the spine with slack still left over. Spending
        that slack in the wrong direction pushes the face further into the
        binding, and every number the layout emits still looks reasonable.
        """
        src = _gutter_slack_photo(1)
        geom = L.page_geometry(PERFECT, "left")
        frame = L.full_bleed_frame(geom)

        unaligned = L.place(
            src, frame, geom, placement_id="p", dpi_floor=1.0, align_faces=False
        )
        aligned = L.place(
            src, frame, geom, placement_id="p", dpi_floor=1.0, align_faces=True
        )

        self.assertEqual(1, unaligned["face_safety"]["faces_in_gutter"])
        self.assertLess(unaligned["face_safety"]["min_face_margin_mm"], 0.0)

        self.assertEqual(0, aligned["face_safety"]["faces_in_gutter"])
        self.assertTrue(aligned["face_safety"]["all_faces_in_safe_zone"])
        self.assertGreater(aligned["face_safety"]["min_face_margin_mm"], 0.0)
        # The face is near the spine of a LEFT page, so content had to move LEFT
        # on the page -- which means the crop window moved RIGHT in the source.
        # The two directions are opposites, and swapping them is a change that
        # only shows up as a face swallowed by the binding.
        self.assertGreater(aligned["crop"]["x"], unaligned["crop"]["x"])

    def test_the_alignment_shift_depends_on_which_edge_is_the_spine(self):
        """Same photo, same frame, opposite bindings.

        A left page has to clear the face from 199mm; a right page only from
        203mm. Equal shifts would mean the alignment reads the gutter size but
        not the side it is on.
        """
        src = _gutter_slack_photo(1)
        shifts = {}
        for side in ("left", "right"):
            geom = L.page_geometry(PERFECT, side)
            placement = L.place(
                src,
                L.full_bleed_frame(geom),
                geom,
                placement_id="p",
                dpi_floor=1.0,
            )
            self.assertTrue(placement["face_safety"]["all_faces_in_safe_zone"], side)
            shifts[side] = placement["crop"]["x"]
        self.assertGreater(shifts["left"], shifts["right"])

    def test_alignment_reports_failure_rather_than_faking_success(self):
        """A square source in a square frame has zero slack. The shift is a
        no-op and the safety block must say so."""
        src = _spine_face_photo(1)
        geom = L.page_geometry(PERFECT, "left")
        placement = L.place(
            src, L.full_bleed_frame(geom), geom, placement_id="p", dpi_floor=1.0
        )
        self.assertEqual(1, placement["face_safety"]["faces_in_gutter"])
        self.assertFalse(L.placement_is_print_safe(placement, 300.0))


# ---------------------------------------------------------------------------
# page planning
# ---------------------------------------------------------------------------


class TestLayoutPage(unittest.TestCase):
    def test_a_page_whose_hero_would_gutter_a_face_falls_back_to_an_inset(self):
        src = _spine_face_photo(1)
        page = L.layout_page(
            (src,), PERFECT, page_index=1, side="left", template=L.FULL_BLEED
        )
        # The step-down from a refused full bleed is the bokeh hero since 0.3.1;
        # the point stands: the bleed was abandoned, never shrunk or face-guttered.
        self.assertEqual(L.BLUR_HERO, page["layout"]["template_id"])
        placement = page["placements"][0]
        self.assertEqual(0, placement["face_safety"]["faces_in_gutter"])
        self.assertTrue(L.placement_is_print_safe(placement, PERFECT["dpi_floor"]))

    def test_a_page_with_no_safe_arrangement_raises_rather_than_degrading(self):
        tiny = photo(1, 400, 400)
        with self.assertRaises(L.LayoutError) as ctx:
            L.layout_page((tiny,), LAYFLAT, page_index=1, side="left")
        self.assertIn("print-safe", str(ctx.exception))

    def test_grid_cells_tile_the_content_box_without_overlapping(self):
        geom = L.page_geometry(PERFECT, "left")
        frames = L.grid_frames(geom, L.GridSpec(2, 2, 6.0))
        self.assertEqual(4, len(frames))
        for i, a in enumerate(frames):
            self.assertTrue(geom.content_box.contains(a))
            for b in frames[i + 1 :]:
                self.assertFalse(a.intersects(b))
        # Cells span the content box exactly, gutters included.
        self.assertAlmostEqual(geom.content_box.x_mm, frames[0].x_mm, places=3)
        self.assertAlmostEqual(geom.content_box.right_mm, frames[1].right_mm, places=3)

    def test_grid_orientation_follows_the_photos(self):
        landscape = (photo(1, 6000, 4000), photo(2, 6000, 4000))
        portrait = (photo(3, 4000, 6000), photo(4, 4000, 6000))

        wide = L.layout_page(landscape, LAYFLAT, page_index=1, side="left")
        tall = L.layout_page(portrait, LAYFLAT, page_index=1, side="left")

        # Two landscape photos want wide cells: one column, two rows.
        self.assertEqual("grid_1x2", wide["layout"]["template_id"])
        self.assertEqual("grid_2x1", tall["layout"]["template_id"])

    def test_all_frames_stay_within_the_page(self):
        photos = tuple(photo(n, 6000, 4000) for n in range(1, 5))
        for side in ("left", "right"):
            page = L.layout_page(photos, PERFECT, page_index=1, side=side)
            geom = L.page_geometry(PERFECT, side)
            for placement in page["placements"]:
                frame = L.RectMm(
                    placement["frame"]["x_mm"],
                    placement["frame"]["y_mm"],
                    placement["frame"]["width_mm"],
                    placement["frame"]["height_mm"],
                )
                self.assertTrue(geom.bleed_box.contains(frame))

    def test_a_page_with_no_photos_is_blank_not_an_error(self):
        page = L.layout_page((), LAYFLAT, page_index=7, side="right", spread_id="spread-04")
        self.assertEqual([], page["placements"])
        self.assertIsNone(page["layout"])
        self.assertEqual("spread-04", page["spread_id"])

    def test_the_solver_field_says_template_because_that_is_what_ran(self):
        """LayoutInfo is a record of what happened. Claiming constraint_solver
        for a template ladder is a small lie that makes the record useless."""
        page = L.layout_page((photo(1),), LAYFLAT, page_index=1, side="left")
        self.assertEqual("template", page["layout"]["solver"])

    def test_too_many_photos_on_one_page_raises(self):
        photos = tuple(photo(n, 6000, 4000) for n in range(1, 15))
        with self.assertRaises(L.LayoutError):
            L.layout_page(photos, LAYFLAT, page_index=1, side="left")


class TestHeroLayout(unittest.TestCase):
    """Composed hero + companion pages: one dominant cell, one smaller, both
    filling the page so almost no mat shows."""

    def test_hero_frames_tile_the_box_with_a_dominant_cell(self):
        geom = L.page_geometry(PERFECT, "left")
        for template in (L.HERO_LEFT, L.HERO_TOP):
            frames = L.hero_frames(geom, template, 6.0)
            self.assertEqual(2, len(frames), template)
            hero, companion = frames
            for frame in frames:
                self.assertTrue(geom.content_box.contains(frame), template)
            self.assertFalse(hero.intersects(companion), template)
            # The hero is unambiguously the larger cell.
            self.assertGreater(hero.area_mm2, companion.area_mm2, template)

    def test_hero_left_places_the_first_photo_as_the_dominant_cell(self):
        photos = (photo(1, 6000, 4000), photo(2, 6000, 4000))
        page = L.layout_page(
            photos, PERFECT, page_index=1, side="left", template=L.HERO_LEFT
        )
        self.assertEqual(L.HERO_LEFT, page["layout"]["template_id"])
        hero, companion = page["placements"]
        hero_area = hero["frame"]["width_mm"] * hero["frame"]["height_mm"]
        comp_area = companion["frame"]["width_mm"] * companion["frame"]["height_mm"]
        self.assertGreater(hero_area, comp_area)
        for placement in page["placements"]:
            self.assertTrue(
                L.placement_is_print_safe(placement, PERFECT["dpi_floor"])
            )

    def test_a_hero_page_needs_exactly_two_photos(self):
        for n in (1, 3):
            photos = tuple(photo(k, 6000, 4000) for k in range(1, n + 1))
            with self.assertRaises(L.LayoutError):
                L.layout_page(
                    photos, PERFECT, page_index=1, side="left", template=L.HERO_TOP
                )

    def test_the_hero_ladder_carries_an_even_grid_fallback_after_it(self):
        # The composed hero cover-crops to fill; if that would drop a face in
        # the trim/gutter the placer refuses it and the page must revert to the
        # even, never-cropped fit-grid rather than fail. So the ladder offers the
        # hero FIRST and a fit-grid fallback right behind it.
        geom = L.page_geometry(PERFECT, "left")
        photos = (photo(1, 4000, 6000), photo(2, 4000, 6000))
        options = L._arrangements(photos, geom, L.HERO_LEFT, 6.0)
        self.assertEqual(L.HERO_LEFT, options[0].template_id)
        self.assertTrue(
            any(opt.template_id.startswith("fit_grid_") for opt in options[1:]),
            "a composed hero page with no even-grid fallback would fail instead "
            "of degrading when a companion crop is unsafe",
        )


# ---------------------------------------------------------------------------
# album planning
# ---------------------------------------------------------------------------


def _album(profile=LAYFLAT, n_pages: int = 6):
    requests = tuple(
        L.PageRequest(photos=(photo(n, 6000, 4000),), section_id="section-arrival")
        for n in range(1, n_pages + 1)
    )
    return L.layout_album(requests, profile, cover=photo(100, 6000, 4000))


class TestLayoutAlbum(unittest.TestCase):
    def test_pages_pair_into_spreads_the_way_the_fixture_does(self):
        pages = _album()
        self.assertEqual("front_cover", pages[0]["side"])
        self.assertIsNone(pages[0]["spread_id"])
        self.assertEqual(("left", "spread-01"), (pages[1]["side"], pages[1]["spread_id"]))
        self.assertEqual(("right", "spread-01"), (pages[2]["side"], pages[2]["spread_id"]))
        self.assertEqual(("left", "spread-02"), (pages[3]["side"], pages[3]["spread_id"]))
        self.assertEqual("back_cover", pages[-1]["side"])
        self.assertEqual(list(range(len(pages))), [p["page_index"] for p in pages])

    def test_page_count_satisfies_the_vendor_rule_including_covers(self):
        for profile in (LAYFLAT, PERFECT):
            with self.subTest(product=profile["product_id"]):
                pages = _album(profile)
                self.assertTrue(L.page_count_is_valid(len(pages), profile))
                self.assertGreaterEqual(len(pages), profile["page_count"]["minimum"])
                self.assertEqual(0, len(pages) % profile["page_count"]["increment"])

    def test_the_golden_fixtures_own_page_count_is_accepted(self):
        """The shipped 20-page layflat fixture counts both covers among its 20.
        If this module counted only the interior it would pad to 22 and the
        printer would reject the book."""
        self.assertTrue(L.page_count_is_valid(20, LAYFLAT))
        self.assertFalse(L.page_count_is_valid(19, LAYFLAT))
        self.assertFalse(L.page_count_is_valid(18, LAYFLAT))
        self.assertFalse(L.page_count_is_valid(102, LAYFLAT))

    def test_every_spread_has_exactly_two_facing_pages(self):
        """An odd number of pages leaves one without a partner.

        A spread is a physical opening: two sheets seen together. A lone page
        carrying a spread_id is a spread the harmoniser will colour-match
        against nothing and the binder will pair with whatever follows.
        """
        # Both sides of the vendor minimum. Below it, padding up to the minimum
        # happens to land on an even count and would mask a missing pairing
        # rule; only an odd request count ABOVE the minimum exercises it.
        for n_requests in (1, 3, 5, 7, 21, 25):
            with self.subTest(requests=n_requests):
                requests = tuple(
                    L.PageRequest(photos=(photo(n, 6000, 4000),))
                    for n in range(1, n_requests + 1)
                )
                pages = L.layout_album(
                    requests, LAYFLAT, cover=photo(100, 6000, 4000)
                )
                counts: dict[str, int] = {}
                for page in pages:
                    if page["spread_id"] is not None:
                        counts[page["spread_id"]] = counts.get(page["spread_id"], 0) + 1
                self.assertTrue(counts)
                for spread_id, count in sorted(counts.items()):
                    self.assertEqual(2, count, f"{spread_id} has {count} page(s)")
                sides = [p["side"] for p in pages if p["spread_id"] is not None]
                self.assertEqual(["left", "right"] * (len(sides) // 2), sides)

    def test_an_unsatisfiable_page_parity_is_diagnosed_as_parity(self):
        """One cover page against a 2-page increment can never work.

        Interior pages move in twos, so no amount of padding changes the total's
        parity. Walking upward until the vendor maximum is exceeded reaches the
        same refusal by the wrong road, and reports an album that is too big --
        sending the reader to look at the photo count instead of the covers.
        """
        requests = (L.PageRequest(photos=(photo(1, 6000, 4000),)),)
        with self.assertRaises(L.LayoutError) as ctx:
            L.layout_album(requests, LAYFLAT, cover=None, back_cover=True)
        message = str(ctx.exception)
        self.assertIn("parity", message)
        self.assertNotIn("maximum", message)

    def test_both_covers_or_neither_pads_cleanly(self):
        requests = (L.PageRequest(photos=(photo(1, 6000, 4000),)),)
        with_covers = L.layout_album(
            requests, LAYFLAT, cover=photo(100, 6000, 4000), back_cover=True
        )
        without = L.layout_album(requests, LAYFLAT, cover=None, back_cover=False)
        self.assertTrue(L.page_count_is_valid(len(with_covers), LAYFLAT))
        self.assertTrue(L.page_count_is_valid(len(without), LAYFLAT))

    def test_an_album_too_big_for_the_vendor_raises(self):
        requests = tuple(
            L.PageRequest(photos=(photo(n, 6000, 4000),)) for n in range(1, 130)
        )
        with self.assertRaises(L.LayoutError):
            L.layout_album(requests, PERFECT)

    def test_exactly_one_hero_per_spread_not_one_per_page(self):
        requests = tuple(
            L.PageRequest(photos=(photo(n, 6000, 4000), photo(n + 50, 4000, 6000)))
            for n in range(1, 5)
        )
        pages = L.layout_album(requests, LAYFLAT, cover=photo(100, 6000, 4000))
        by_spread: dict[str, int] = {}
        for page in pages:
            key = page["spread_id"] or f"page-{page['page_index']}"
            by_spread.setdefault(key, 0)
            by_spread[key] += sum(1 for p in page["placements"] if p["is_hero"])
        for key, count in sorted(by_spread.items()):
            with self.subTest(spread=key):
                total = sum(
                    len(p["placements"])
                    for p in pages
                    if (p["spread_id"] or f"page-{p['page_index']}") == key
                )
                self.assertEqual(1 if total else 0, count)

    def test_every_placement_in_a_whole_album_is_print_safe(self):
        for profile in (LAYFLAT, PERFECT):
            pages = _album(profile)
            for page in pages:
                for placement in page["placements"]:
                    with self.subTest(product=profile["product_id"], p=placement["placement_id"]):
                        self.assertTrue(
                            L.placement_is_print_safe(placement, profile["dpi_floor"])
                        )

    def test_placement_ids_are_unique_and_contract_shaped(self):
        slug = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
        pages = _album()
        ids = [p["placement_id"] for page in pages for p in page["placements"]]
        self.assertEqual(len(ids), len(set(ids)))
        for value in ids:
            self.assertRegex(value, slug)


# ---------------------------------------------------------------------------
# determinism
# ---------------------------------------------------------------------------


class TestDeterminism(unittest.TestCase):
    def test_the_same_input_serialises_to_the_same_bytes(self):
        first = json.dumps(_album(), sort_keys=False)
        second = json.dumps(_album(), sort_keys=False)
        self.assertEqual(first, second)

    def test_face_order_within_a_photo_does_not_change_the_output(self):
        faces = (
            face(1, 0.20, 0.30, 0.08, 0.10),
            face(2, 0.60, 0.35, 0.08, 0.10),
            face(3, 0.40, 0.55, 0.08, 0.10),
        )
        forward = L.Photo(mid(1), 6000, 4000, faces=faces)
        backward = L.Photo(mid(1), 6000, 4000, faces=tuple(reversed(faces)))

        geom = L.page_geometry(PERFECT, "left")
        args = dict(placement_id="p1-0-aaaaaaaa", dpi_floor=300.0)
        self.assertEqual(
            json.dumps(L.place(forward, L.inset_frame(geom, 1.5), geom, **args)),
            json.dumps(L.place(backward, L.inset_frame(geom, 1.5), geom, **args)),
        )

    def test_the_hero_of_a_spread_does_not_depend_on_input_order(self):
        """Two identically sized placements is the common case, not the corner.

        With no explicit tiebreak the hero falls to whichever `min` happened to
        scan first, so reversing the two pages of a spread silently moves the
        anchor image -- and the anchor is what the harmoniser and the review
        queue both key off.
        """
        a, b = photo(1, 6000, 4000), photo(2, 6000, 4000)
        cover = photo(100, 6000, 4000)
        forward = L.layout_album(
            (L.PageRequest(photos=(a,)), L.PageRequest(photos=(b,))), LAYFLAT, cover=cover
        )
        reversed_ = L.layout_album(
            (L.PageRequest(photos=(b,)), L.PageRequest(photos=(a,))), LAYFLAT, cover=cover
        )

        def hero_of(pages, spread):
            return [
                p["media_id"]
                for page in pages
                if page["spread_id"] == spread
                for p in page["placements"]
                if p["is_hero"]
            ]

        self.assertEqual([mid(1)], hero_of(forward, "spread-01"))
        self.assertEqual(hero_of(forward, "spread-01"), hero_of(reversed_, "spread-01"))

    def test_the_hero_of_a_page_does_not_depend_on_input_order(self):
        a, b = photo(1, 6000, 4000), photo(2, 6000, 4000)
        forward = L.layout_page((a, b), LAYFLAT, page_index=1, side="left")
        backward = L.layout_page((b, a), LAYFLAT, page_index=1, side="left")
        self.assertEqual(
            [p["media_id"] for p in forward["placements"] if p["is_hero"]],
            [p["media_id"] for p in backward["placements"] if p["is_hero"]],
        )

    def test_emitted_numbers_are_quantised(self):
        pages = _album()
        for page in pages:
            for placement in page["placements"]:
                for key, value in placement["frame"].items():
                    with self.subTest(key=key):
                        self.assertEqual(value, round(value, 3))
                for key, value in placement["crop"].items():
                    with self.subTest(key=key):
                        self.assertEqual(value, round(value, 6))

    def test_no_negative_zero_reaches_the_json(self):
        text = json.dumps(_album())
        self.assertNotIn("-0.0", text)


# ---------------------------------------------------------------------------
# hostile inputs
# ---------------------------------------------------------------------------


class TestHostileInputs(unittest.TestCase):
    def test_nan_dimensions_raise_instead_of_passing_every_gate(self):
        """`NaN < floor` is False, so a NaN sails through every comparison in
        this module and produces a layout of plausible numbers built on nothing.
        The only defence is an explicit finiteness check."""
        geom = L.page_geometry(LAYFLAT, "left")
        with self.assertRaises(L.LayoutError):
            L.RectMm(0.0, 0.0, float("nan"), 100.0)
        with self.assertRaises(L.LayoutError):
            L.NormBox(0.0, 0.0, float("nan"), 1.0)
        with self.assertRaises(L.LayoutError):
            L.place(
                photo(1),
                L.inset_frame(geom, 1.5),
                geom,
                placement_id="p",
                dpi_floor=float("nan"),
            )

    def test_infinite_dimensions_raise(self):
        with self.assertRaises(L.LayoutError):
            L.RectMm(0.0, 0.0, float("inf"), 100.0)

    def test_a_nan_dpi_floor_in_a_profile_is_caught(self):
        broken = dict(LAYFLAT, dpi_floor=float("nan"))
        with self.assertRaises(L.LayoutError):
            L.layout_page((photo(1),), broken, page_index=1, side="left")

    def test_zero_and_negative_extents_raise(self):
        with self.assertRaises(L.LayoutError):
            L.RectMm(0.0, 0.0, 0.0, 10.0)
        with self.assertRaises(L.LayoutError):
            L.NormBox(0.0, 0.0, -0.5, 1.0)

    def test_a_normalised_box_outside_the_image_raises(self):
        with self.assertRaises(L.LayoutError):
            L.NormBox(0.8, 0.0, 0.5, 1.0)

    def test_malformed_ids_raise(self):
        with self.assertRaises(L.LayoutError):
            L.Photo("not-a-hash", 100, 100)
        with self.assertRaises(L.LayoutError):
            L.Face("ABC", L.NormBox(0, 0, 1, 1))
        with self.assertRaises(L.LayoutError):
            L.place(
                photo(1),
                L.inset_frame(L.page_geometry(LAYFLAT, "left"), 1.5),
                L.page_geometry(LAYFLAT, "left"),
                placement_id="Not A Slug",
                dpi_floor=300.0,
            )

    def test_non_integer_pixel_dimensions_raise(self):
        with self.assertRaises(L.LayoutError):
            L.Photo(mid(1), 6000.5, 4000)
        with self.assertRaises(L.LayoutError):
            L.Photo(mid(1), True, 4000)


# ---------------------------------------------------------------------------
# contract conformance
# ---------------------------------------------------------------------------


def _schema_validator(pointer: str):
    from jsonschema import Draft202012Validator
    from referencing import Registry, Resource

    documents = {
        path.name: json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(SCHEMA_DIR.glob("*.schema.json"))
    }
    registry = Registry().with_resources(
        [(name, Resource.from_contents(doc)) for name, doc in documents.items()]
    )
    return Draft202012Validator({"$ref": pointer}, registry=registry)


class TestContractConformance(unittest.TestCase):
    def test_every_page_validates_against_the_page_definition(self):
        validator = _schema_validator("album-spec.schema.json#/$defs/Page")
        for profile in (LAYFLAT, PERFECT):
            for page in _album(profile):
                with self.subTest(product=profile["product_id"], page=page["page_index"]):
                    errors = [
                        f"{list(e.path)}: {e.message}"
                        for e in validator.iter_errors(page)
                    ]
                    self.assertEqual([], errors)

    def test_a_whole_album_spec_built_from_these_pages_validates(self):
        """Pages are only useful if they drop into an AlbumSpec unchanged.

        `validation.status` is 'not_run' on purpose: this module does not run
        the print validator and must not assert a pass it did not earn.
        """
        validator = _schema_validator("album-spec.schema.json")
        spec = {
            "schema_version": "v0",
            "album_id": mid(0xABC),
            "title": "Thailand",
            "subtitle": None,
            "event": None,
            "vendor_profile": LAYFLAT,
            "pages": _album(),
            "selection": None,
            "spread_harmony": None,
            "determinism": {
                "planner": "album-layout",
                "planner_version": "0.1.0",
                "seed": 0,
                "inputs_digest": mid(0xDEF),
                "generated_at": None,
            },
            "validation": {"status": "not_run", "checks": []},
            "review": None,
        }
        errors = [f"{list(e.path)}: {e.message}" for e in validator.iter_errors(spec)]
        self.assertEqual([], errors)

    def test_a_mixed_orientation_multi_photo_album_validates(self):
        validator = _schema_validator("album-spec.schema.json#/$defs/Page")
        requests = (
            L.PageRequest(photos=(photo(1, 6000, 4000), photo(2, 4000, 6000))),
            L.PageRequest(photos=(photo(3, 5000, 5000),)),
            L.PageRequest(
                photos=tuple(photo(n, 6000, 4000) for n in range(10, 14))
            ),
            L.PageRequest(photos=(photo(20, 4000, 6000), photo(21, 4000, 6000), photo(22, 4000, 6000))),
        )
        pages = L.layout_album(requests, PERFECT, cover=photo(99, 6000, 4000))
        for page in pages:
            with self.subTest(page=page["page_index"]):
                self.assertEqual(
                    [], [f"{list(e.path)}: {e.message}" for e in validator.iter_errors(page)]
                )


# ---------------------------------------------------------------------------
# the face-safety record has to agree with itself
# ---------------------------------------------------------------------------


class TestFaceSafetyAgreesWithItself(unittest.TestCase):
    """The boolean and the number are one answer, not two opinions about one.

    The print validator reads both and treats disagreement as a hard export
    block: `all_faces_in_safe_zone: true` beside a negative
    `min_face_margin_mm` is refused outright ("face_safety contradicts
    itself"), whatever the pixels actually look like. So the two have to fall
    out of the same comparison, and they used not to -- the boolean asked
    `safe_box.contains(rect)`, which forgives a thousandth of a millimetre,
    while the number floored `content_box.clearance_of(rect)` downward with no
    forgiveness at all. Different box, different comparator, different epsilon.
    """

    def assert_record_is_coherent(self, safety: dict) -> None:
        margin = safety["min_face_margin_mm"]
        if safety["face_count"] == 0:
            self.assertIsNone(margin)
            self.assertTrue(safety["all_faces_in_safe_zone"])
            return
        self.assertIsNotNone(margin)
        self.assertEqual(
            safety["all_faces_in_safe_zone"],
            margin >= 0.0,
            f"boolean and margin disagree: {safety}",
        )
        self.assertEqual(
            safety["all_faces_in_safe_zone"],
            safety["faces_in_gutter"] == 0 and safety["faces_in_trim_zone"] == 0,
            f"boolean and counts disagree: {safety}",
        )

    def test_a_face_flush_with_the_content_edge_is_not_safe_and_negative_at_once(self):
        """The reachable form of the bug, through the public entry point.

        A 5000x5000 source fills the 284mm square content box exactly, so the
        crop keeps the whole image and a face whose box reaches x=1.0 lands on
        the frame's right edge -- which IS the safe line. The rect's right edge
        is built as `frame.x + u0*w + (u1-u0)*w`, which is not bit-identical to
        `frame.x + w`, so the clearance arrives at -5.7e-14: inside every
        boolean's tolerance here, and -0.001 once floored to the emitted
        precision. Before the fix this page shipped
        `all_faces_in_safe_zone: true, min_face_margin_mm: -0.001` and the
        validator blocked the export of a book that was physically fine.
        """
        src = L.Photo(
            media_id=mid(1),
            pixel_width=5000,
            pixel_height=5000,
            faces=(face(1, 0.061, 0.4, 0.939, 0.1),),
        )
        page = L.layout_page((src,), LAYFLAT, page_index=0, side="single")
        safety = page["placements"][0]["face_safety"]
        self.assertEqual(1, safety["face_count"])
        self.assertTrue(safety["all_faces_in_safe_zone"])
        self.assertEqual(0.0, safety["min_face_margin_mm"])
        self.assert_record_is_coherent(safety)

    def test_a_face_one_quantum_over_the_gutter_line_is_on_it_not_in_it(self):
        """Same tolerance, other hazard.

        Frames and face rects are emitted at 1e-3mm, so a rect that overlaps
        the gutter band by exactly one quantum is touching the line, not
        crossing it. Counting that as a gutter hit throws the whole page onto
        the next template in the ladder over a micron -- and, worse, pairs
        `all_faces_in_safe_zone: false` with a margin of 0.0.
        """
        geom = L.page_geometry(PERFECT, "left")
        self.assertEqual(199.0, geom.gutter_band.x_mm)
        src = L.Photo(
            media_id=mid(1),
            pixel_width=4000,
            pixel_height=4000,
            faces=(face(1, 0.0, 0.0, 1.0, 1.0),),
        )
        for width_mm, label in ((186.001, "one quantum"), (186.0000000000001, "float noise")):
            with self.subTest(overlap=label):
                frame = L.RectMm(13.0, 13.0, width_mm, 186.0)
                safety = L.face_safety(src, L.NormBox(0.0, 0.0, 1.0, 1.0), frame, geom)
                self.assertEqual(0, safety["faces_in_gutter"])
                self.assertTrue(safety["all_faces_in_safe_zone"])
                self.assertEqual(0.0, safety["min_face_margin_mm"])
                self.assert_record_is_coherent(safety)

    def test_a_face_genuinely_in_the_gutter_still_reports_how_far_in(self):
        """The tolerance is a quantum, not a licence.

        The upper bound on it is not a matter of taste: the margin ships at
        1e-3mm, so a tolerance wider than that swallows crossings the field is
        perfectly able to describe, and the record starts saying 0.0 about a
        face it has measured at -0.01. 2mm and 0.01mm are both crossings and
        both have to survive into the number.
        """
        geom = L.page_geometry(PERFECT, "left")
        src = L.Photo(
            media_id=mid(1),
            pixel_width=4000,
            pixel_height=4000,
            faces=(face(1, 0.0, 0.0, 1.0, 1.0),),
        )
        for width_mm, expected in ((181.0, -2.0), (179.01, -0.01)):
            with self.subTest(margin=expected):
                frame = L.RectMm(20.0, 20.0, width_mm, 170.0)
                safety = L.face_safety(src, L.NormBox(0.0, 0.0, 1.0, 1.0), frame, geom)
                self.assertEqual(1, safety["faces_in_gutter"])
                self.assertEqual(0, safety["faces_in_trim_zone"])
                self.assertEqual(expected, safety["min_face_margin_mm"])
                self.assert_record_is_coherent(safety)

    def test_the_margin_is_the_nearer_hazard_not_whichever_one_flatters(self):
        """Contract: "distance from the nearest face to the nearest unsafe
        boundary" -- and a bound page has two of them.

        The same placement as above sits 2mm clear of the safe line at 203mm
        and 2mm INSIDE the gutter at 199mm. Reporting the safe-box clearance
        would put +2.0 on a face that is in the spine.

        This is also what keeps the sibling print validator honest: it
        reconstructs the distance to the blade as
        `min_face_margin_mm + safe_margin_mm`. Feeding it the nearer of the two
        hazards makes that reconstruction pessimistic; feeding it the safe-box
        clearance would make it flattering, and a print gate may only ever err
        toward refusing.
        """
        geom = L.page_geometry(PERFECT, "left")
        frame = L.RectMm(20.0, 20.0, 181.0, 170.0)
        self.assertEqual(2.0, geom.safe_box.clearance_of(frame))
        src = L.Photo(
            media_id=mid(1),
            pixel_width=4000,
            pixel_height=4000,
            faces=(face(1, 0.0, 0.0, 1.0, 1.0),),
        )
        margin = L.face_safety(src, L.NormBox(0.0, 0.0, 1.0, 1.0), frame, geom)[
            "min_face_margin_mm"
        ]
        self.assertEqual(-2.0, margin)
        self.assertLess(margin, geom.safe_box.clearance_of(frame))

    def test_no_placement_anywhere_on_the_page_can_contradict_itself(self):
        """A sweep, because the contradiction is a boundary effect and boundary
        effects are found by walking across the boundary, not by guessing where
        it is. Both bindings, both spine sides, faces marched across the page."""
        for profile in (LAYFLAT, PERFECT):
            geom_side = {side: L.page_geometry(profile, side) for side in ("left", "right")}
            for side, geom in geom_side.items():
                frame = L.full_bleed_frame(geom)
                for i in range(0, 101):
                    x = i / 100.0
                    for w in (0.02, 0.1, 1.0 - x):
                        if w <= 0.0 or x + w > 1.0:
                            continue
                        src = L.Photo(
                            media_id=mid(1),
                            pixel_width=4000,
                            pixel_height=4000,
                            faces=(face(1, x, 0.4, w, 0.08),),
                        )
                        safety = L.face_safety(
                            src, L.NormBox(0.0, 0.0, 1.0, 1.0), frame, geom
                        )
                        with self.subTest(product=profile["product_id"], side=side, x=x, w=w):
                            self.assert_record_is_coherent(safety)


# ---------------------------------------------------------------------------
# geometry predicates: the tolerance is one emitted quantum, on both sides
# ---------------------------------------------------------------------------


class TestEdgeTolerance(unittest.TestCase):
    def test_a_frame_a_quantum_past_the_bleed_box_is_on_it_not_outside_it(self):
        """Frames ship at 1e-3mm. A spread frame whose arithmetic lands on
        306.001 on a 306mm page has reached the bleed edge and nothing else;
        refusing it refuses a correct full-bleed layout over one micron. The
        page still has to refuse a real overhang, so both sides are pinned."""
        geom = L.page_geometry(LAYFLAT, "front_cover")
        src = L.Photo(media_id=mid(1), pixel_width=5000, pixel_height=5000)
        placement = L.place(
            src,
            L.RectMm(0.0, 0.0, 306.001, 306.001),
            geom,
            placement_id="edge-case",
            dpi_floor=300.0,
        )
        self.assertEqual(["top", "bottom", "left", "right"], placement["bleeds"])
        with self.assertRaises(L.LayoutError):
            # 0.05mm is 50 quanta: artwork actually hanging off the sheet.
            L.place(
                src,
                L.RectMm(0.0, 0.0, 306.05, 306.05),
                geom,
                placement_id="overhang",
                dpi_floor=300.0,
            )

    def test_touching_rects_do_not_overlap_and_one_quantum_is_touching(self):
        """`intersects` is the boolean shadow of `separation_from`, and the two
        must not drift apart -- that drift is what let `faces_in_gutter` and
        `min_face_margin_mm` describe the same face differently."""
        band = L.RectMm(199.0, 0.0, 17.0, 216.0)
        cases = (
            (L.RectMm(13.0, 13.0, 186.0, 186.0), False, "edge contact"),
            (L.RectMm(13.0, 13.0, 186.001, 186.0), False, "one quantum over"),
            (L.RectMm(13.0, 13.0, 188.0, 186.0), True, "2mm over"),
            (L.RectMm(13.0, 13.0, 100.0, 186.0), False, "clear by 86mm"),
        )
        for rect, expected, label in cases:
            with self.subTest(case=label):
                self.assertEqual(expected, band.intersects(rect))
                # the sign of the number and the boolean are the same fact
                self.assertEqual(expected, band.separation_from(rect) < -1.5e-3)
        self.assertEqual(0.0, band.separation_from(L.RectMm(13.0, 13.0, 186.0, 186.0)))
        self.assertEqual(-2.0, band.separation_from(L.RectMm(13.0, 13.0, 188.0, 186.0)))
        self.assertEqual(86.0, band.separation_from(L.RectMm(13.0, 13.0, 100.0, 186.0)))


# ---------------------------------------------------------------------------
# DPI, crops and the guards around them
# ---------------------------------------------------------------------------


class TestDpiAndCropGuards(unittest.TestCase):
    def test_the_dpi_reported_is_the_worse_axis_not_the_flattering_one(self):
        """Axes that disagree by less than the contract tolerance are not an
        error, so the function returns one of them -- and which one decides
        whether a placement clears the vendor floor. 299.5 against 300.4 is a
        0.3% disagreement: legal, and on opposite sides of a 300 DPI gate."""
        src = photo(1, 4000, 4000)
        frame = L.RectMm(0.0, 0.0, 100.0, 100.0)
        crop = L.NormBox(
            0.0,
            0.0,
            299.5 * 100.0 / (25.4 * 4000),
            300.4 * 100.0 / (25.4 * 4000),
        )
        dpi = L.effective_dpi(src, crop, frame)
        self.assertAlmostEqual(299.5, dpi, delta=0.02)
        self.assertLess(dpi, 300.0)

    def test_the_reported_dpi_is_recomputable_from_the_crop_that_ships(self):
        """The crop is quantised BEFORE the DPI is derived from it, so the
        record is internally checkable: anyone holding the page can multiply
        the emitted crop by the source pixels and get the emitted DPI back.
        Deriving the DPI from the full-precision crop instead leaves a number
        that is right about a crop nobody receives -- here, 1400.73 reported
        against an emitted crop worth 1400.72.
        """
        photos = tuple(photo(n, 5000, 5000) for n in range(1, 13))
        page = L.layout_page(
            photos, LAYFLAT, page_index=1, side="left", template="grid_3x4"
        )
        self.assertEqual(12, len(page["placements"]))
        for src, placement in zip(photos, page["placements"]):
            with self.subTest(placement=placement["placement_id"]):
                crop = placement["crop"]
                frame = placement["frame"]
                recomputed = L.effective_dpi(
                    src,
                    L.NormBox(crop["x"], crop["y"], crop["w"], crop["h"]),
                    L.RectMm(
                        frame["x_mm"], frame["y_mm"], frame["width_mm"], frame["height_mm"]
                    ),
                )
                self.assertEqual(placement["effective_dpi"], recomputed)

    def test_a_crop_that_no_longer_matches_its_frame_is_named_as_such(self):
        """The aspect guard is a tripwire, and this is the smallest legal input
        that trips it: a 0.021mm x 278mm hairline, where rounding the crop to
        the emitted 1e-6 costs 0.7% of an aspect of 7.6e-5. Nobody lays out a
        hairline -- the point is what happens when the crop maths and the frame
        disagree at all.

        Without the guard the placement is not saved, it is refused one step
        later by the DPI axis check, which reports "effective DPI disagrees
        between axes" and sends whoever reads it to look at pixel counts. The
        failure that matters is the crop, and the message has to say so.
        """
        src = photo(1, 6000, 4000)
        geom = L.page_geometry(LAYFLAT, "single")
        with self.assertRaises(L.LayoutError) as ctx:
            L.place(
                src,
                L.RectMm(100.0, 11.0, 0.021, 278.0),
                geom,
                placement_id="hairline",
                dpi_floor=300.0,
            )
        self.assertIn("does not match frame aspect", str(ctx.exception))


# ---------------------------------------------------------------------------
# templates: which arrangement is tried, and in what order
# ---------------------------------------------------------------------------


class TestTemplateLadder(unittest.TestCase):
    def test_a_lone_photo_is_inset_by_default_and_bleeds_only_on_request(self):
        """Full bleed on a square page crops a 3:2 photo to 2:3 of its width --
        a third of the frame, and whatever was in it. That is a decision, so it
        has to be asked for; the default keeps the photo whole."""
        src = photo(1, 6000, 4000)
        default = L.layout_page((src,), LAYFLAT, page_index=0, side="single")
        self.assertEqual(L.SINGLE_INSET, default["layout"]["template_id"])
        self.assertEqual([], default["placements"][0]["bleeds"])

        asked = L.layout_page(
            (src,), LAYFLAT, page_index=0, side="single", template=L.FULL_BLEED
        )
        self.assertEqual(L.FULL_BLEED, asked["layout"]["template_id"])
        self.assertEqual(
            ["top", "bottom", "left", "right"], asked["placements"][0]["bleeds"]
        )

        kept_default = default["placements"][0]["crop"]["w"] * default["placements"][0]["crop"]["h"]
        kept_bleed = asked["placements"][0]["crop"]["w"] * asked["placements"][0]["crop"]["h"]
        self.assertAlmostEqual(1.0, kept_default, places=3)
        self.assertLess(kept_bleed, 0.7)

    def test_an_even_split_of_orientations_stacks_into_rows(self):
        """One landscape, one portrait: the majority rule has no majority. The
        tie has to be decided in the source or it is decided by whichever way
        `sum()` happened to count, and the same two photos come back as a 1x2
        on one run and a 2x1 on the next."""
        page = L.layout_page(
            (photo(1, 6000, 4000), photo(2, 4000, 6000)),
            LAYFLAT,
            page_index=0,
            side="left",
        )
        self.assertEqual("grid_1x2", page["layout"]["template_id"])
        frames = [p["frame"] for p in page["placements"]]
        self.assertEqual(frames[0]["x_mm"], frames[1]["x_mm"])
        self.assertLess(frames[0]["y_mm"], frames[1]["y_mm"])

    def test_four_photos_form_a_block_not_a_strip(self):
        """2x2 on a square page gives four ~139mm cells at the page's own
        aspect. 4x1 gives four 65x284mm slivers, and a 3:2 photo in a 1:4.3
        cell keeps under a quarter of its frame. Same photo count, different
        book."""
        photos = tuple(photo(n, 6000, 4000) for n in range(1, 5))
        page = L.layout_page(photos, LAYFLAT, page_index=0, side="left")
        self.assertEqual("grid_2x2", page["layout"]["template_id"])
        self.assertEqual({"columns": 2, "rows": 2, "gutter_mm": 6.0}, page["layout"]["grid"])

        xs = sorted({p["frame"]["x_mm"] for p in page["placements"]})
        ys = sorted({p["frame"]["y_mm"] for p in page["placements"]})
        self.assertEqual(2, len(xs))
        self.assertEqual(2, len(ys))

        strip = L.layout_page(
            photos, LAYFLAT, page_index=0, side="left", template="grid_4x1"
        )
        kept_block = min(p["crop"]["w"] * p["crop"]["h"] for p in page["placements"])
        kept_strip = min(p["crop"]["w"] * p["crop"]["h"] for p in strip["placements"])
        self.assertGreater(kept_block, 3 * kept_strip)


# ---------------------------------------------------------------------------
# vendor page-count arithmetic
# ---------------------------------------------------------------------------


class TestVendorPageCount(unittest.TestCase):
    def test_a_count_inside_the_range_still_has_to_land_on_the_increment(self):
        """A layflat book is bound in facing pairs, so 21 pages is not a book
        that is one page long in the wrong place -- it is a book the printer
        cannot fold. Both shipped profiles keep counts well inside their range
        that the increment alone rejects."""
        for count in (21, 25, 99):
            with self.subTest(layflat=count):
                self.assertTrue(20 <= count <= 100)
                self.assertFalse(L.page_count_is_valid(count, LAYFLAT))
        for count in (25, 26, 27, 118):
            with self.subTest(perfect=count):
                self.assertTrue(24 <= count <= 120)
                self.assertFalse(L.page_count_is_valid(count, PERFECT))
        self.assertTrue(L.page_count_is_valid(28, PERFECT))
        self.assertTrue(L.page_count_is_valid(100, LAYFLAT))

    def test_both_bounds_are_inclusive_and_the_counts_just_past_them_are_not(self):
        """Neither shipped profile can see an off-by-one in either bound.

        Their bounds sit ON their increments -- 20/100 by 2, 24/120 by 4 -- so
        the counts one page outside (19, 101, 23, 121) are refused by the
        increment rule whatever the bounds say, and a bound that is one too
        generous is invisible. A profile whose bounds are NOT multiples of its
        increment is the only shape that separates the three rules, so that is
        what this uses: minimum-1 and maximum+1 are both legal multiples here,
        and both must still be refused.
        """
        awkward = {"page_count": {"minimum": 25, "maximum": 99, "increment": 4}}
        self.assertEqual(0, 24 % 4)
        self.assertEqual(0, 100 % 4)
        self.assertFalse(L.page_count_is_valid(24, awkward))  # one under the minimum
        self.assertFalse(L.page_count_is_valid(100, awkward))  # one over the maximum
        self.assertTrue(L.page_count_is_valid(28, awkward))
        self.assertTrue(L.page_count_is_valid(96, awkward))
        self.assertFalse(L.page_count_is_valid(26, awkward))  # inside, off increment

    def test_padding_takes_as_many_two_page_steps_as_the_increment_needs(self):
        """Interior pages move in twos and the increment need not be two.

        Against a 6-page signature, 8 interior pages are two steps from a legal
        count, not one. A padding loop that only ever tries a single step
        reaches the parity refusal instead and blames the covers for an album
        that pads perfectly well.
        """
        signature = {
            **LAYFLAT,
            "page_count": {"minimum": 6, "maximum": 60, "increment": 6},
        }
        requests = tuple(
            L.PageRequest(photos=(photo(n, 6000, 4000),)) for n in range(1, 9)
        )
        pages = L.layout_album(requests, signature, cover=None, back_cover=False)
        self.assertEqual(12, len(pages))
        self.assertTrue(L.page_count_is_valid(len(pages), signature))

    def test_padding_finds_a_legal_count_whenever_one_exists(self):
        """The bound on the padding loop is only correct if it is at least the
        number of two-page steps a modulus can need. Swept rather than asserted
        as a literal, because the literal is the thing under test."""
        for increment in (2, 4, 6, 8, 10):
            signature = {
                **LAYFLAT,
                "page_count": {"minimum": increment, "maximum": 120, "increment": increment},
            }
            for n_requests in range(1, 13):
                with self.subTest(increment=increment, requests=n_requests):
                    requests = tuple(
                        L.PageRequest(photos=(photo(n, 6000, 4000),))
                        for n in range(1, n_requests + 1)
                    )
                    if increment % 2:
                        continue
                    pages = L.layout_album(
                        requests, signature, cover=None, back_cover=False
                    )
                    self.assertTrue(
                        L.page_count_is_valid(len(pages), signature),
                        f"{len(pages)} pages is not a multiple of {increment}",
                    )
                    self.assertGreaterEqual(len(pages), n_requests)


# ---------------------------------------------------------------------------
# ties, costs and ordering
# ---------------------------------------------------------------------------


class TestTiesAndCost(unittest.TestCase):
    def test_the_same_photo_twice_in_a_spread_still_has_one_named_hero(self):
        """Area and media_id both tie when a photo repeats inside one spread,
        and something has to break it. Without a third key the winner is
        whichever `min()` scanned first, which is page order -- so the hero of
        a spread would depend on which page of it was built first rather than
        on anything in the record. Pages 9 and 10 make the two orders disagree:
        list order gives page 9, the placement_id order gives "p10-...".
        """
        repeated = photo(7, 5000, 5000)
        requests = []
        for n in range(1, 19):
            requests.append(
                L.PageRequest(photos=(repeated if n in (9, 10) else photo(n, 5000, 5000),))
            )
        pages = L.layout_album(tuple(requests), LAYFLAT, cover=photo(100, 6000, 4000))

        spread = [p for p in pages if p["spread_id"] == "spread-05"]
        self.assertEqual([9, 10], [p["page_index"] for p in spread])
        placements = [pl for p in spread for pl in p["placements"]]
        self.assertEqual(2, len(placements))
        self.assertEqual(
            placements[0]["media_id"], placements[1]["media_id"], "the tie must be real"
        )
        self.assertEqual(
            placements[0]["frame"], placements[1]["frame"], "the tie must be real"
        )
        heroes = [pl["placement_id"] for pl in placements if pl["is_hero"]]
        self.assertEqual([min(pl["placement_id"] for pl in placements)], heroes)
        self.assertTrue(heroes[0].startswith("p10-"))

    def test_a_page_that_gave_something_up_costs_more_than_one_that_did_not(self):
        """solver_cost has to rank, or it is decoration.

        Two full-bleed pages, identical crop loss (a square photo in a square
        frame keeps everything), differing only in that one prints below the
        vendor's preferred DPI. If the relaxation does not enter the cost, the
        compromised page and the clean one are indistinguishable to anything
        that compares them.
        """
        clean = L.layout_page(
            (photo(1, 5000, 5000),),
            LAYFLAT,
            page_index=0,
            side="front_cover",
            template=L.FULL_BLEED,
        )
        compromised = L.layout_page(
            (photo(2, 3800, 3800),),
            LAYFLAT,
            page_index=0,
            side="front_cover",
            template=L.FULL_BLEED,
        )
        self.assertEqual([], clean["layout"]["constraints_relaxed"])
        self.assertEqual(
            ["below_preferred_dpi"], compromised["layout"]["constraints_relaxed"]
        )
        # Same photo shape, same frame: the crop loss is zero for both, so the
        # whole difference in cost is the relaxation.
        self.assertEqual(0.0, clean["layout"]["solver_cost"])
        self.assertGreater(compromised["layout"]["solver_cost"], clean["layout"]["solver_cost"])

    def test_a_page_with_no_binding_has_no_gutter_hazard_to_report(self):
        """A cover has no facing page, so its only unsafe boundary is the trim
        line and the margin is the whole distance to it. Folding a non-existent
        gutter into the minimum as a zero would report 0.0mm about a face
        sitting 126mm inside the page -- and 0.0 is the number that means
        "exactly on the line"."""
        geom = L.page_geometry(LAYFLAT, "front_cover")
        self.assertIsNone(geom.gutter_band)
        src = L.Photo(
            media_id=mid(1),
            pixel_width=4000,
            pixel_height=4000,
            faces=(face(1, 0.45, 0.45, 0.1, 0.1),),
        )
        safety = L.face_safety(
            src, L.NormBox(0.0, 0.0, 1.0, 1.0), L.full_bleed_frame(geom), geom
        )
        # 126.699 rather than 126.7 because the margin is floored, not rounded.
        self.assertAlmostEqual(126.7, safety["min_face_margin_mm"], places=2)
        self.assertLessEqual(safety["min_face_margin_mm"], 126.7)

    def test_the_margin_rounds_toward_the_hazard_like_every_other_number_here(self):
        """Same rule as the DPI floor: the error lands on the safe side.

        A face 2.0005mm into the gutter reports -2.001, not -2.0. Rounding to
        nearest would shave the incursion toward zero, which is the direction
        that makes a face look further from the spine than it is.
        """
        geom = L.page_geometry(PERFECT, "left")
        src = L.Photo(
            media_id=mid(1),
            pixel_width=4000,
            pixel_height=4000,
            faces=(face(1, 0.0, 0.0, 1.0, 1.0),),
        )
        frame = L.RectMm(20.0, 20.0, 181.0005, 170.0)
        safety = L.face_safety(src, L.NormBox(0.0, 0.0, 1.0, 1.0), frame, geom)
        self.assertEqual(-2.001, safety["min_face_margin_mm"])

    def test_the_hero_is_the_biggest_printed_photo_not_the_smallest(self):
        """"Anchor image" is a size claim. A page where one frame had to shrink
        to clear the DPI floor is the cheapest way to make the two candidates
        genuinely different sizes."""
        page = L.layout_page(
            (photo(1, 6000, 4000), photo(2, 1600, 1200)),
            LAYFLAT,
            page_index=0,
            side="left",
            template="grid_1x2",
        )
        areas = [p["frame"]["width_mm"] * p["frame"]["height_mm"] for p in page["placements"]]
        self.assertGreater(areas[0], areas[1] * 2, "the frames must differ in size")
        self.assertEqual([True, False], [p["is_hero"] for p in page["placements"]])

    def test_a_bleeding_frame_is_abandoned_rather_than_shrunk(self):
        """Shrinking a full-bleed frame to buy DPI is the white sliver the bleed
        exists to prevent: the artwork stops short of the guillotine's range and
        the finished book shows paper down the edge. A photo that cannot hold
        the bleed drops to the next template instead.

        2000x2000 on purpose: it clears the DPI floor at 169mm, which is 55% of
        the 306mm bleed box and so passes the minimum-frame-fraction check that
        would otherwise reject this arrangement for an unrelated reason. The
        only thing standing between this photo and a shrunken "full bleed" is
        the bleed rule itself.
        """
        page = L.layout_page(
            (photo(1, 2000, 2000),),
            LAYFLAT,
            page_index=0,
            side="single",
            template=L.FULL_BLEED,
        )
        # The step-down from a refused full bleed is the bokeh hero since 0.3.1;
        # the point stands: the bleed was abandoned, never shrunk or face-guttered.
        self.assertEqual(L.BLUR_HERO, page["layout"]["template_id"])
        self.assertEqual([], page["placements"][0]["bleeds"])
        frame = page["placements"][0]["frame"]
        self.assertGreater(frame["x_mm"], 0.0)

    def test_cost_is_per_photo_so_pages_of_different_sizes_compare(self):
        """A four-photo page is not four times worse than a one-photo page for
        having four crops. If the loss is summed rather than averaged, cost
        ranks by photo count and nothing else."""
        cell = dict(profile=LAYFLAT, page_index=0, side="left", template="grid_2x2")
        one = L.layout_page((photo(1, 6000, 4000),), **cell)
        four = L.layout_page(tuple(photo(n, 6000, 4000) for n in range(1, 5)), **cell)
        self.assertEqual([], one["layout"]["constraints_relaxed"])
        self.assertEqual([], four["layout"]["constraints_relaxed"])
        self.assertAlmostEqual(
            one["layout"]["solver_cost"], four["layout"]["solver_cost"], places=4
        )
        self.assertGreater(one["layout"]["solver_cost"], 0.0)

    def test_print_safety_re_reads_every_gate_rather_than_the_summary_boolean(self):
        """`placement_is_print_safe` is the last thing between a layout and the
        renderer, and it is fed records this module did not necessarily build.
        A record whose boolean says safe while its own counts say otherwise is
        not evidence of safety, and neither is one that is simply too blurry."""
        def placement(**overrides):
            safety = {
                "face_count": 1,
                "all_faces_in_safe_zone": True,
                "faces_in_gutter": 0,
                "faces_in_trim_zone": 0,
                "min_face_margin_mm": 5.0,
            }
            safety.update(overrides.pop("face_safety", {}))
            return {"effective_dpi": 320.0, "face_safety": safety, **overrides}

        self.assertTrue(L.placement_is_print_safe(placement(), 300.0))
        self.assertFalse(
            L.placement_is_print_safe(placement(effective_dpi=299.99), 300.0)
        )
        self.assertFalse(
            L.placement_is_print_safe(
                placement(face_safety={"faces_in_gutter": 1}), 300.0
            )
        )
        self.assertFalse(
            L.placement_is_print_safe(
                placement(face_safety={"faces_in_trim_zone": 1}), 300.0
            )
        )
        self.assertFalse(
            L.placement_is_print_safe(
                placement(face_safety={"all_faces_in_safe_zone": False}), 300.0
            )
        )

    def test_the_order_faces_arrive_in_never_reaches_the_output(self):
        """Deliberately stronger than the module currently needs.

        Every consumer of the face iteration order inside `face_safety` is an
        order-invariant reduction today -- a count, a `min`, and a list that is
        sorted separately -- so shuffling faces cannot change a byte, and the
        explicit sort is defence for whoever adds a consumer that is not.
        This asserts the property rather than the sort, so it keeps holding if
        the internals change and starts failing the moment the property stops.
        """
        faces = [
            face(1, 0.10, 0.20, 0.06, 0.08),
            face(2, 0.55, 0.42, 0.05, 0.07),
            face(3, 0.80, 0.30, 0.04, 0.06, subject=False),
            face(4, 0.31, 0.61, 0.07, 0.09),
        ]
        orders = (
            tuple(faces),
            tuple(reversed(faces)),
            (faces[2], faces[0], faces[3], faces[1]),
            (faces[1], faces[3], faces[0], faces[2]),
        )
        rendered = set()
        for order in orders:
            src = L.Photo(
                media_id=mid(1), pixel_width=6000, pixel_height=4000, faces=order
            )
            page = L.layout_page((src,), PERFECT, page_index=1, side="left")
            rendered.add(json.dumps(page, sort_keys=True))
        self.assertEqual(1, len(rendered))


def _rect(rect) -> tuple[float, float, float, float]:
    return (rect.x_mm, rect.y_mm, rect.width_mm, rect.height_mm)


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestBlurHero(unittest.TestCase):
    """blur_hero: an inset photo over a blurred, dimmed backdrop of itself --
    white margins become bokeh, and the backdrop media is always the page's
    own photo so the render job's asset set never grows."""

    def test_the_page_carries_its_own_photo_as_backdrop(self):
        page = L.layout_page(
            (photo(1, w=4000, h=6000),), LAYFLAT, page_index=1, side="left",
            template=L.BLUR_HERO,
        )
        self.assertEqual(L.BLUR_HERO, page["layout"]["template_id"])
        background = page["background"]
        self.assertEqual("media_blur", background["kind"])
        self.assertEqual(mid(1), background["media_id"])
        self.assertEqual(1, len(page["placements"]))
        frame = page["placements"][0]["frame"]
        # Geometrically an inset: inside the content box, aspect preserved.
        self.assertAlmostEqual(
            frame["width_mm"] / frame["height_mm"], 4000 / 6000, places=2
        )

    def test_other_templates_keep_a_solid_background(self):
        page = L.layout_page(
            (photo(1),), LAYFLAT, page_index=1, side="left",
            template=L.SINGLE_INSET,
        )
        self.assertEqual("solid", page["background"]["kind"])

    def test_blur_hero_takes_exactly_one_photo(self):
        with self.assertRaises(L.LayoutError):
            L.layout_page(
                (photo(1), photo(2)), LAYFLAT, page_index=1, side="left",
                template=L.BLUR_HERO,
            )
