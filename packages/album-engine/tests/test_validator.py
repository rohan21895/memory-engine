"""Print-validator tests.

The defect class this suite is built against is not "raises the wrong
exception". It is a validator that returns a report full of plausible numbers,
says "pass", and puts a blurry photo or a decapitated face into a physical book
that is then posted to a customer. Nothing crashes. Nobody finds out until the
parcel arrives.

So the tests are organised around the ways a gate fails *open*:

* the input needed to run the check was missing, and the check quietly skipped;
* a NaN entered a threshold comparison and lost it in whichever direction the
  author happened to write the comparison;
* a two-sided equality check verified nothing because one side was null;
* a unit conversion was applied twice, or not at all, and the result still
  looked like a believable DPI;
* the answer depended on dict/list ordering, so it was right on Tuesday;
* the numbers were hardcoded to one vendor and the other vendor's book was
  approved against the wrong spec.

Several tests carry an explicit assertion showing that the NAIVE implementation
would have passed the same input. That is there so the test cannot rot into a
tautology: if someone rewrites the gate in the obvious way, the paired assertion
documents exactly which obvious way was wrong.
"""

from __future__ import annotations

import json
import math
import sys
import unittest
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = PACKAGE_ROOT.parent.parent
sys.path.insert(0, str(PACKAGE_ROOT))

from memory_engine_album.validator import (  # noqa: E402
    DEFAULT_GUILLOTINE_DRIFT_MM,
    GATE_ORDER,
    MM_PER_INCH,
    DocumentColor,
    SourceImage,
    validate_album,
    validate_album_spec,
)
from memory_engine_album.layout import (  # noqa: E402
    FULL_BLEED,
    Face,
    NormBox,
    Photo,
    full_bleed_frame,
    layout_page,
    page_geometry,
    place,
    placement_is_print_safe,
)
from memory_engine_album.validator import _Ctx, _worst  # noqa: E402

PROFILE_DIR = PACKAGE_ROOT / "vendor_profiles"
SCHEMA_DIR = REPO_ROOT / "contracts" / "schemas"
FIXTURE = (
    REPO_ROOT / "contracts" / "fixtures" / "album-spec" / "valid"
    / "album-thailand-validated.json"
)

LAYFLAT = json.loads((PROFILE_DIR / "layflat-300-square.json").read_text(encoding="utf-8"))
PERFECT_BOUND = json.loads(
    (PROFILE_DIR / "perfectbound-210-square.json").read_text(encoding="utf-8")
)

DOCUMENT_OK = DocumentColor(icc_name="FOGRA39 Coated", intent="relative_colorimetric")

SOURCE_W, SOURCE_H = 6000, 4000
MEDIA = "1" * 64

# A phone-era JPEG. At the default frame it prints at ~171 DPI, which is the
# only way to get below a 300 DPI floor on a 300mm page: 6000px of source
# cannot be stretched thin enough to fail on a book this size, and a test that
# pretends otherwise is testing arithmetic that cannot happen.
SMALL_MEDIA = "2" * 64
SMALL_SOURCE = (1600, 1067)


# --------------------------------------------------------------------------
# Builders. Everything they produce is self-consistent by construction, so a
# failing test is about the thing under test and not about the scaffolding.
# --------------------------------------------------------------------------


def dpi_for(width_mm: float, height_mm: float, crop_w: float, crop_h: float,
            source: tuple[int, int] = (SOURCE_W, SOURCE_H)) -> float:
    return min(
        crop_w * source[0] * MM_PER_INCH / width_mm,
        crop_h * source[1] * MM_PER_INCH / height_mm,
    )


def face_safety(count=0, all_safe=True, trim=0, gutter=0, margin=None):
    return {
        "face_count": count,
        "all_faces_in_safe_zone": all_safe,
        "faces_in_gutter": gutter,
        "faces_in_trim_zone": trim,
        "min_face_margin_mm": margin,
        "cropped_face_ids": [],
    }


def placement(
    placement_id="p1",
    media_id=MEDIA,
    x=51.0,
    y=86.0,
    width_mm=203.9,
    height_mm=135.9,
    crop_w=0.86,
    crop_h=0.86,
    dpi=None,
    bleeds=(),
    safety=None,
    rotation_deg=0,
    crop_rotation_deg=0,
    source=(SOURCE_W, SOURCE_H),
):
    return {
        "placement_id": placement_id,
        "media_id": media_id,
        "frame": {
            "x_mm": x,
            "y_mm": y,
            "width_mm": width_mm,
            "height_mm": height_mm,
            "rotation_deg": rotation_deg,
        },
        # Centred, so the crop always lies inside the image whatever w/h the
        # caller picks. A fixed origin of 0.07 with w=1.0 describes a crop that
        # runs 7% off the right edge, which is not a valid crop and is now
        # rejected -- scaffolding that is only self-consistent for the default
        # arguments is scaffolding that makes tests fail for reasons that have
        # nothing to do with what they are testing. (0.86 still gives 0.07.)
        "crop": {"x": (1.0 - crop_w) / 2.0, "y": (1.0 - crop_h) / 2.0,
                 "w": crop_w, "h": crop_h, "rotation_deg": crop_rotation_deg},
        "effective_dpi": dpi if dpi is not None
        else dpi_for(width_mm, height_mm, crop_w, crop_h, source),
        "z_index": 0,
        "bleeds": list(bleeds),
        "is_hero": False,
        "face_safety": face_safety() if safety is None else safety,
        "enhancement_ops": [],
        "caption": None,
        "border": None,
    }


def low_dpi_placement(placement_id="p0", **kwargs):
    return placement(placement_id, media_id=SMALL_MEDIA, source=SMALL_SOURCE, **kwargs)


def page(page_index, placements=None, side="left", spread_id="spread-01"):
    return {
        "page_index": page_index,
        "spread_id": spread_id,
        "side": side,
        "section_id": None,
        "background": None,
        "placements": [placement(f"p{page_index}")] if placements is None else placements,
        "text_blocks": [],
        "layout": None,
    }


def album(count=20, placements_by_page=None):
    pages = []
    for index in range(count):
        override = (placements_by_page or {}).get(index)
        pages.append(page(index, override))
    return pages


DEFAULT_SOURCES = {
    MEDIA: SourceImage(SOURCE_W, SOURCE_H),
    SMALL_MEDIA: SourceImage(*SMALL_SOURCE),
}


def run(pages, profile=None, sources=None, document=DOCUMENT_OK, **kwargs):
    return validate_album(
        pages=pages,
        vendor_profile=LAYFLAT if profile is None else profile,
        sources=DEFAULT_SOURCES if sources is None else sources,
        document_color=document,
        validated_at="2026-03-17T14:22:11+05:30",
        **kwargs,
    )


def findings(report, check_id, *, failed_only=True):
    """Itemised findings only. The rollup is excluded because it carries no
    location, and a test that accidentally asserts against it is a test that
    stops checking that failures name the placement they are about."""
    return [
        f
        for f in report.checks
        if f.check_id == check_id
        and f.counts_toward_totals
        and (not failed_only or not f.passed)
    ]


def summary(report, check_id):
    """The rollup for a gate: the one entry that is not itemised."""
    rollups = [
        f for f in report.checks if f.check_id == check_id and not f.counts_toward_totals
    ]
    return rollups[0] if rollups else None


# --------------------------------------------------------------------------


class TestTheGateFiresOnARealLayout(unittest.TestCase):
    """The face gate, end to end, on pages this engine actually produced.

    Every other test in this file hands the validator a hand-written
    `face_safety` block. That is the right way to test the validator's own
    logic, and it leaves one thing unproven: whether a face that is genuinely
    in the trim zone of a genuinely laid-out page ever reaches the validator as
    a failure. Until `services/pipeline` began storing face rectangles, it
    could not -- every album was planned with `faces=()`, so `face_count` was 0
    on every placement, no face could be in the trim zone, and this gate passed
    on every book ever produced without once being exercised.

    A gate that has never been observed failing is not known to work. These two
    tests observe it.
    """

    CORNERS = (
        NormBox(0.0, 0.0, 0.06, 0.08),
        NormBox(0.94, 0.0, 0.06, 0.08),
        NormBox(0.0, 0.92, 0.06, 0.08),
        NormBox(0.94, 0.92, 0.06, 0.08),
    )

    def _photo(self, boxes):
        return Photo(
            media_id=MEDIA,
            pixel_width=SOURCE_W,
            pixel_height=SOURCE_H,
            faces=tuple(
                Face(face_id=chr(ord("a") + index) * 64, box=box)
                for index, box in enumerate(boxes)
            ),
        )

    def _full_bleed_page(self, photo):
        """One full-bleed placement, built with the same primitive `layout_page`
        uses -- `place` -- rather than through the arrangement ladder.

        The ladder is why this cannot be done with `layout_page`: it calls
        `placement_is_print_safe` and returns None for a placement whose face is
        in the trim zone, so the engine falls back to an inset frame and, if
        nothing works, raises. That refusal is asserted separately below. Here
        the point is the OTHER half of the defence: a page that reached an
        AlbumSpec anyway -- from a hand-edited spec, from apps/desktop's editor,
        from a future planner -- must be caught by the print validator.
        """
        geometry = page_geometry(LAYFLAT, "right")
        placement = place(
            photo,
            full_bleed_frame(geometry),
            geometry,
            placement_id="p2-0-11111111",
            dpi_floor=LAYFLAT["dpi_floor"],
        )
        return page(2, [placement], side="right", spread_id="spread-02")

    def test_a_face_in_the_trim_zone_fails_the_gate(self):
        pages = album(20)
        pages[2] = self._full_bleed_page(self._photo(self.CORNERS[:1]))

        safety = pages[2]["placements"][0]["face_safety"]
        self.assertEqual(1, safety["face_count"])
        self.assertEqual(1, safety["faces_in_trim_zone"])
        self.assertFalse(safety["all_faces_in_safe_zone"])
        self.assertLess(safety["min_face_margin_mm"], 0.0)

        report = run(pages)
        self.assertEqual("fail", report.status)
        failures = findings(report, "face_in_trim_zone")
        self.assertTrue(failures, "the trim-zone gate did not fire")
        self.assertEqual(2, failures[0].page_index)
        self.assertEqual("error", failures[0].severity)

    def test_the_same_page_with_no_faces_recorded_passes_vacuously(self):
        """The bug this whole exercise is about, reproduced deliberately.

        Identical geometry, identical crop, identical everything -- except that
        nobody recorded where the faces were. `face_count: 0`, no face can be
        in the trim zone, the gate passes, and the guillotine still cuts
        through a face. `services/pipeline` planned every album this way until
        it began storing face rectangles.
        """
        pages = album(20)
        pages[2] = self._full_bleed_page(self._photo(()))

        self.assertEqual(0, pages[2]["placements"][0]["face_safety"]["face_count"])
        self.assertEqual("pass", run(pages).status)
        self.assertEqual([], findings(run(pages), "face_in_trim_zone"))

    def test_the_layout_engine_refuses_the_arrangement_before_the_validator_sees_it(self):
        """Defence in depth, and the two layers are not the same layer.

        `layout_page` drops any arrangement whose placement is not print-safe
        and tries the next template. So asking for a full bleed on a photo with
        a face in the corner does not produce the failing page tested above --
        it produces an INSET page, whose frame is the safe box itself, where no
        face can be in the trim zone at all.

        Both layers matter. This one keeps the engine from ever proposing the
        bad page; the validator catches the bad page when it arrives from
        somewhere else. Neither is a substitute for the other, and neither was
        being exercised at all while the pipeline planned albums with no faces.
        """
        photo = self._photo(self.CORNERS[:1])
        geometry = page_geometry(LAYFLAT, "right")
        bleeding = place(
            photo,
            full_bleed_frame(geometry),
            geometry,
            placement_id="p2-0-11111111",
            dpi_floor=LAYFLAT["dpi_floor"],
        )
        self.assertFalse(
            placement_is_print_safe(bleeding, LAYFLAT["dpi_floor"]),
            "the layout engine would have proposed a page with a face in the trim zone",
        )

        fell_back = layout_page(
            [photo],
            LAYFLAT,
            page_index=2,
            side="right",
            spread_id="spread-02",
            template=FULL_BLEED,
        )
        frame = fell_back["placements"][0]["frame"]
        self.assertNotEqual(
            full_bleed_frame(geometry).width_mm, frame["width_mm"],
            "the full-bleed arrangement was accepted despite the face in the trim zone",
        )
        safety = fell_back["placements"][0]["face_safety"]
        self.assertEqual(1, safety["face_count"])
        self.assertEqual(0, safety["faces_in_trim_zone"])
        self.assertEqual("pass", run(album(20)).status)


class TestHappyPath(unittest.TestCase):
    def test_a_clean_album_passes(self):
        report = run(album())
        self.assertEqual("pass", report.status, report.blocking)
        self.assertEqual(0, report.error_count)

    def test_a_passing_report_carries_every_hard_gate_as_passed(self):
        """The AlbumSpec schema will not accept a 'pass' that is missing one of
        these, and a report that omits a gate is a report where nobody ran it."""
        report = run(album())
        passed = {f.check_id for f in report.checks if f.passed}
        for gate in (
            "dpi_floor",
            "face_in_trim_zone",
            "bleed_coverage",
            "color_profile_match",
            "page_count_valid",
        ):
            self.assertIn(gate, passed)

    def test_a_failing_gate_never_also_reports_itself_as_passed(self):
        """The AlbumSpec schema gates on `checks contains {check_id, passed:true}`,
        so one stray passing entry satisfies the contract for a gate that
        actually failed. A report must never carry both verdicts for the same
        check_id."""
        broken_albums = [
            album(18),
            album(20, {0: [low_dpi_placement("p0")]}),
            album(20, {0: [placement("p0", bleeds=("left",), x=1.0, y=0.0,
                                     width_mm=305.0, height_mm=306.0,
                                     crop_w=0.6667, crop_h=1.0)]}),
            album(20, {0: [placement("p0", safety=face_safety(
                count=1, all_safe=False, trim=1, margin=-8.0))]}),
            album(20, {0: [placement("p0", safety=face_safety(
                count=1, all_safe=False, gutter=1, margin=-2.0))]}),
        ]
        cases = [(pages, DOCUMENT_OK) for pages in broken_albums]
        cases.append((album(20), None))  # missing document colour
        cases.append((album(20), DocumentColor("GRACoL 2006", "perceptual")))
        for index, (pages, document) in enumerate(cases):
            with self.subTest(case=index):
                report = run(pages, document=document)
                self.assertEqual("fail", report.status)
                failed_ids = {f.check_id for f in report.blocking}
                passed_ids = {f.check_id for f in report.checks if f.passed}
                self.assertEqual(set(), failed_ids & passed_ids)

    def test_counts_match_the_findings(self):
        report = run(album())
        self.assertEqual(len(report.blocking), report.error_count)
        self.assertEqual(len(report.warnings), report.warning_count)

    def test_a_rollup_is_not_counted_as_an_extra_error(self):
        """Three bad placements must read as three errors, not six."""
        report = run(
            album(
                placements_by_page={
                    3: [low_dpi_placement("p3")],
                    5: [low_dpi_placement("p5")],
                    7: [low_dpi_placement("p7")],
                }
            )
        )
        self.assertEqual("fail", report.status)
        self.assertEqual(3, report.error_count)


class TestDpiArithmetic(unittest.TestCase):
    def test_exact_conversion(self):
        """3000 px across 254mm is 300.0 DPI exactly.

        Chosen because every plausible mistake lands somewhere obvious: a
        missing 25.4 gives 11.8, a doubled one gives 7620, and a swapped
        numerator gives 0.0033. This is the assertion that catches the
        "preprocessing scale applied twice" family of bug in this module.
        """
        self.assertAlmostEqual(300.0, dpi_for(254.0, 254.0, 1.0, 1.0, (3000, 3000)), places=9)
        pages = [page(0, [placement("p0", width_mm=254.0, height_mm=254.0, crop_w=1.0,
                                    crop_h=1.0, source=(3000, 3000))])]
        report = run(
            pages + album(20)[1:],
            sources={MEDIA: SourceImage(3000, 3000)},
        )
        dpi = summary(report, "dpi_floor").measured_value
        self.assertAlmostEqual(300.0, dpi, places=4)

    def test_the_softer_axis_decides(self):
        """A crop whose aspect does not match the frame prints at the DPI of the
        axis with fewer pixels per mm. 6000x4000 into a square 254mm frame is
        600 DPI across and 400 DPI down: the width alone would report 600, the
        mean 500, and the paper shows 400."""
        pages = album(20, {0: [placement("p0", width_mm=254.0, height_mm=254.0,
                                         crop_w=1.0, crop_h=1.0)]})
        report = run(pages)
        rollup = summary(report, "dpi_floor")
        self.assertEqual("p0", rollup.placement_id)
        self.assertAlmostEqual(400.0, rollup.measured_value, places=4)


class TestCannotRunMeansFail(unittest.TestCase):
    """Every input this validator needs, withheld one at a time.

    Not one of these may produce a passing report. This is the exact shape of
    the load gate that permitted weights whose hash was never computed.
    """

    def test_unknown_source_resolution_fails_dpi(self):
        report = run(album(), sources={})
        self.assertEqual("fail", report.status)
        self.assertTrue(findings(report, "dpi_floor"))
        self.assertIn("unknown", findings(report, "dpi_floor")[0].detail)

    def test_zero_pixel_source_fails(self):
        report = run(album(), sources={MEDIA: SourceImage(0, 4000)})
        self.assertEqual("fail", report.status)
        self.assertTrue(findings(report, "source_media_available"))

    def test_null_face_safety_fails(self):
        """A null face_safety is indistinguishable from 'nobody looked'.

        The tempting reading is 'no faces recorded, therefore no faces'. A
        scenery photo can say face_count: 0 for free, so the only thing a null
        can mean is that the check did not run.
        """
        anon = placement("p0")
        anon["face_safety"] = None
        report = run(album(20, {0: [anon]}))
        self.assertEqual("fail", report.status)
        self.assertTrue(
            any("null" in f.detail for f in findings(report, "face_in_trim_zone"))
        )

    def test_missing_face_margin_with_faces_fails(self):
        """Without the margin there is no way to separate a warning from a
        catastrophe, and the fallback must be the catastrophe."""
        anon = placement("p0", safety=face_safety(count=2, all_safe=True, margin=None))
        report = run(album(20, {0: [anon]}))
        self.assertEqual("fail", report.status)

    def test_no_document_colour_fails(self):
        report = run(album(), document=None)
        self.assertEqual("fail", report.status)
        self.assertTrue(findings(report, "color_profile_match"))

    def test_profile_without_dpi_floor_fails(self):
        profile = dict(LAYFLAT)
        del profile["dpi_floor"]
        report = run(album(), profile=profile)
        self.assertEqual("fail", report.status)
        self.assertTrue(findings(report, "dpi_floor"))

    def test_profile_without_page_count_fails(self):
        profile = dict(LAYFLAT)
        del profile["page_count"]
        report = run(album(), profile=profile)
        self.assertEqual("fail", report.status)
        self.assertTrue(findings(report, "page_count_valid"))

    def test_profile_without_safe_margin_fails(self):
        profile = dict(LAYFLAT)
        del profile["safe_margin_mm"]
        report = run(album(), profile=profile)
        self.assertEqual("fail", report.status)
        self.assertTrue(findings(report, "face_in_trim_zone"))

    def test_profile_with_incoherent_page_limits_fails(self):
        profile = dict(LAYFLAT)
        profile["page_count"] = {"minimum": 21, "maximum": 100, "increment": 4}
        report = run(album(24), profile=profile)
        self.assertEqual("fail", report.status)
        self.assertIn("inconsistent", findings(report, "page_count_valid")[0].detail)

    def test_rotated_crop_fails_rather_than_being_approximated(self):
        """A NormalizedBox rotates in normalised space; on a 3:2 image that is
        not a rotation in pixel space, so the usual w*W is wrong by an amount
        that varies with the angle and still looks like a DPI."""
        rotated = placement("p0", crop_rotation_deg=12.0)
        report = run(album(20, {0: [rotated]}))
        self.assertEqual("fail", report.status)
        self.assertTrue(any("rotated" in f.detail for f in findings(report, "dpi_floor")))

    def test_rotated_full_bleed_frame_fails(self):
        """A rotated rectangle does not cover its own bounding box. Measuring
        the bounding box would approve a white wedge in the corner of a page."""
        rotated = placement(
            "p0", x=0.0, y=0.0, width_mm=306.0, height_mm=306.0, crop_w=0.6667,
            crop_h=1.0, bleeds=("top", "bottom", "left", "right"), rotation_deg=4.0,
        )
        report = run(album(20, {0: [rotated]}))
        self.assertEqual("fail", report.status)
        self.assertTrue(findings(report, "bleed_coverage"))

    def test_an_album_with_no_placements_does_not_pass_vacuously(self):
        """Twenty blank pages satisfy every per-placement gate: no placement is
        below the floor if there are no placements. That empty-set pass is the
        oldest fail-open there is."""
        blank = [page(i, []) for i in range(20)]
        report = run(blank)
        self.assertEqual("fail", report.status)
        self.assertTrue(findings(report, "page_count_valid"))


class TestNaNDoesNotSlipThrough(unittest.TestCase):
    def test_the_naive_comparison_would_have_let_nan_print(self):
        """Documents the bug, so the test below cannot rot into a tautology."""
        self.assertFalse(math.nan < 300.0)  # `if dpi < floor: fail` -> no failure
        self.assertFalse(math.nan >= 300.0)  # `if not (dpi >= floor): fail` -> failure

    def test_nan_declared_dpi_fails(self):
        bad = placement("p0", dpi=float("nan"))
        report = run(album(20, {0: [bad]}))
        self.assertEqual("fail", report.status)
        self.assertTrue(findings(report, "dpi_floor"))

    def test_nan_geometry_fails(self):
        bad = placement("p0", width_mm=float("nan"))
        report = run(album(20, {0: [bad]}))
        self.assertEqual("fail", report.status)

    def test_nan_face_margin_fails(self):
        bad = placement(
            "p0", safety=face_safety(count=1, all_safe=False, trim=1,
                                     margin=float("nan"))
        )
        report = run(album(20, {0: [bad]}))
        self.assertEqual("fail", report.status)

    def test_infinite_dpi_declaration_fails(self):
        bad = placement("p0", dpi=float("inf"))
        report = run(album(20, {0: [bad]}))
        self.assertEqual("fail", report.status)

    def test_boolean_is_not_a_number(self):
        """`isinstance(True, int)` is True in Python, so a stray boolean becomes
        1.0 in any arithmetic that does not guard against it."""
        bad = placement("p0")
        bad["frame"]["width_mm"] = True
        report = run(album(20, {0: [bad]}))
        self.assertEqual("fail", report.status)


class TestDeclaredDpiIsNotTrusted(unittest.TestCase):
    def test_a_lying_effective_dpi_is_caught(self):
        """The declared value comes from the same planner that produced the
        layout. A gate that reads it is only as good as the code it is gating."""
        liar = placement("p0", dpi=480.0)
        report = run(album(20, {0: [liar]}))
        self.assertEqual("fail", report.status)
        details = " ".join(f.detail for f in findings(report, "dpi_floor"))
        self.assertIn("480.0", details)

    def test_rounding_in_the_declaration_is_tolerated(self):
        honest = placement("p0")
        honest["effective_dpi"] = round(honest["effective_dpi"], 1)
        report = run(album(20, {0: [honest]}))
        self.assertEqual("pass", report.status, report.blocking)


class TestColourProfileGate(unittest.TestCase):
    def test_name_mismatch_blocks(self):
        report = run(album(), document=DocumentColor("GRACoL 2006", "relative_colorimetric"))
        self.assertEqual("fail", report.status)

    def test_intent_mismatch_blocks(self):
        report = run(album(), document=DocumentColor("FOGRA39 Coated", "perceptual"))
        self.assertEqual("fail", report.status)

    def test_whitespace_and_case_are_not_a_mismatch(self):
        report = run(album(), document=DocumentColor("  fogra39   coated ",
                                                     "relative_colorimetric"))
        self.assertEqual("pass", report.status, report.blocking)

    def test_unhashed_profile_passes_but_says_so(self):
        """vendor_profiles/README.md: icc_hash is null today, so this gate is a
        name comparison and weaker than it looks. A weakness that is not in the
        report is a weakness nobody will ever act on."""
        report = run(album())
        warned = [f for f in report.warnings if f.check_id == "color_profile_match"]
        self.assertEqual(1, len(warned))
        self.assertIn("icc_hash", warned[0].detail)

    def test_the_two_non_null_comparison_would_have_verified_nothing(self):
        """The exact fail-open this repo already shipped once: a comparison
        that needs two non-null values does nothing when it has one, and the
        one it is missing is always the unverified side."""
        vendor_hash, document_hash = "a" * 64, None
        self.assertFalse(
            bool(vendor_hash and document_hash and vendor_hash != document_hash)
        )  # naive gate: no mismatch detected, weights/profile permitted

        profile = json.loads(json.dumps(LAYFLAT))
        profile["color_profile"]["icc_hash"] = vendor_hash
        report = run(album(), profile=profile,
                     document=DocumentColor("FOGRA39 Coated", "relative_colorimetric",
                                            icc_hash=document_hash))
        self.assertEqual("fail", report.status)
        self.assertIn("unverified", findings(report, "color_profile_match")[0].detail)

    def test_matching_hash_passes_without_the_warning(self):
        profile = json.loads(json.dumps(LAYFLAT))
        profile["color_profile"]["icc_hash"] = "b" * 64
        report = run(album(), profile=profile,
                     document=DocumentColor("FOGRA39 Coated", "relative_colorimetric",
                                            icc_hash="b" * 64))
        self.assertEqual("pass", report.status, report.blocking)
        self.assertEqual([], [f for f in report.warnings
                              if f.check_id == "color_profile_match"])


class TestFaceSeverity(unittest.TestCase):
    """A face 0.2mm into the safe band and a face at the trim line are both
    'in the trim zone'. Only one of them is a ruined book."""

    def _report(self, margin):
        risky = placement(
            "p0", safety=face_safety(count=2, all_safe=False, trim=1, margin=margin)
        )
        return run(album(20, {0: [risky]}))

    def test_a_grazing_face_is_a_warning_not_a_block(self):
        report = self._report(-0.2)  # 7.8mm still between the face and the blade
        self.assertEqual("pass", report.status, report.blocking)
        self.assertEqual(1, len([f for f in report.warnings
                                 if f.check_id == "face_in_trim_zone"]))

    def test_a_face_at_the_trim_line_blocks(self):
        report = self._report(-8.0)  # the full 8mm safe margin consumed
        self.assertEqual("fail", report.status)
        blocking = [f for f in report.blocking if f.check_id == "face_in_trim_zone"]
        self.assertEqual(1, len(blocking))
        self.assertIn("past the trim line", blocking[0].detail)

    def test_a_face_already_across_the_trim_blocks(self):
        self.assertEqual("fail", self._report(-9.5).status)

    def test_the_drift_boundary_is_closed(self):
        """Exactly at the guillotine tolerance counts as unsafe. `>` not `>=`:
        the drift figure is the distance the blade *can* travel, so landing on
        it is landing under it."""
        at_boundary = -(LAYFLAT["safe_margin_mm"] - DEFAULT_GUILLOTINE_DRIFT_MM)
        self.assertEqual("fail", self._report(at_boundary).status)
        just_inside = at_boundary + 0.01
        self.assertEqual("pass", self._report(just_inside).status)

    def test_contradictory_face_safety_is_not_believed(self):
        """all_faces_in_safe_zone=true while a face is counted in the trim zone.
        Whatever produced this is broken, and a broken producer's cheerful field
        is not evidence."""
        lying = placement(
            "p0", safety=face_safety(count=2, all_safe=True, trim=1, margin=2.0)
        )
        report = run(album(20, {0: [lying]}))
        self.assertEqual("fail", report.status)
        self.assertIn("contradicts itself",
                      findings(report, "face_in_trim_zone")[0].detail)

    def test_negative_margin_contradicts_an_all_safe_claim(self):
        lying = placement(
            "p0", safety=face_safety(count=2, all_safe=True, trim=0, margin=-3.0)
        )
        self.assertEqual("fail", run(album(20, {0: [lying]})).status)

    def test_unattributed_unsafety_blocks(self):
        """all_faces_in_safe_zone=false with nothing counted anywhere: something
        is wrong and the record cannot say what."""
        vague = placement(
            "p0", safety=face_safety(count=2, all_safe=False, margin=1.0)
        )
        self.assertEqual("fail", run(album(20, {0: [vague]})).status)

    def test_more_faces_in_the_trim_zone_than_faces_blocks(self):
        impossible = placement(
            "p0", safety=face_safety(count=1, all_safe=False, trim=3, margin=-1.0)
        )
        self.assertEqual("fail", run(album(20, {0: [impossible]})).status)


class TestGutter(unittest.TestCase):
    def test_a_face_in_the_gutter_blocks(self):
        swallowed = placement(
            "p0", safety=face_safety(count=1, all_safe=False, gutter=1, margin=-2.0)
        )
        report = run(album(20, {0: [swallowed]}))
        self.assertEqual("fail", report.status)
        blocking = [f for f in report.blocking if f.check_id == "face_in_gutter"]
        self.assertEqual(1, len(blocking))
        self.assertEqual(0, blocking[0].page_index)
        self.assertEqual("p0", blocking[0].placement_id)

    def test_the_gutter_width_reported_is_the_vendors(self):
        """The golden fixture's own report says 'no faces within the 10mm
        gutter' while its profile says 5mm. A hardcoded number in a message is
        a hardcoded number in the next person's mental model."""
        self.assertIn("5.0mm", summary(run(album()), "face_in_gutter").detail)
        report = run(album(24), profile=PERFECT_BOUND)
        self.assertIn("14.0mm", summary(report, "face_in_gutter").detail)

    def test_profile_without_a_gutter_declaration_fails(self):
        """'The vendor did not say' and 'the vendor said zero' are different
        claims, and only one of them is safe to act on."""
        profile = dict(LAYFLAT)
        del profile["gutter_mm"]
        self.assertEqual("fail", run(album(), profile=profile).status)


class TestBleed(unittest.TestCase):
    FULL = dict(x=0.0, y=0.0, width_mm=306.0, height_mm=306.0, crop_w=0.6667, crop_h=1.0)

    def test_exact_bleed_passes(self):
        cover = placement("p0", bleeds=("top", "bottom", "left", "right"), **self.FULL)
        report = run(album(20, {0: [cover]}))
        self.assertEqual("pass", report.status, report.blocking)
        self.assertAlmostEqual(3.0, summary(report, "bleed_coverage").measured_value,
                               places=6)

    def test_short_bleed_blocks_and_says_by_how_much(self):
        short = placement("p0", bleeds=("left",), x=0.5, y=0.0, width_mm=305.5,
                          height_mm=306.0, crop_w=0.6667, crop_h=1.0)
        report = run(album(20, {0: [short]}))
        self.assertEqual("fail", report.status)
        bad = [f for f in report.blocking if f.check_id == "bleed_coverage"][0]
        self.assertEqual(0, bad.page_index)
        self.assertEqual("p0", bad.placement_id)
        self.assertAlmostEqual(2.5, bad.measured_value, places=6)
        self.assertAlmostEqual(3.0, bad.required_value, places=6)
        self.assertIn("0.50mm short", bad.detail)

    def test_a_hair_under_the_bleed_still_blocks(self):
        """0.1mm of missing bleed is a white line down the finished edge, not a
        rounding preference."""
        short = placement("p0", bleeds=("left",), x=0.1, y=0.0, width_mm=305.9,
                          height_mm=306.0, crop_w=0.6667, crop_h=1.0)
        self.assertEqual("fail", run(album(20, {0: [short]})).status)

    def test_undeclared_spill_over_the_trim_is_a_warning(self):
        """Artwork crossing the trim without declaring a bleed gets cut. The
        planner meant something the guillotine will not do."""
        spill = placement("p0", x=40.0, y=86.0, width_mm=270.0, height_mm=180.0,
                          crop_w=1.0, crop_h=1.0)
        report = run(album(20, {0: [spill]}))
        warned = [f for f in report.warnings if f.check_id == "bleed_coverage"]
        self.assertTrue(warned)
        self.assertIn("cut off", warned[0].detail)

    def test_unknown_bleed_edge_blocks(self):
        weird = placement("p0", bleeds=("spine",), **self.FULL)
        self.assertEqual("fail", run(album(20, {0: [weird]})).status)

    def test_bleed_is_measured_from_the_bleed_box_origin(self):
        """RectMm's origin is the top-left of the BLEED box, not the trim box.
        Reading it as trim-relative shifts every measurement by exactly the
        bleed and makes a correct cover look 3mm short."""
        cover = placement("p0", bleeds=("left",), **self.FULL)
        report = run(album(20, {0: [cover]}))
        left = summary(report, "bleed_coverage")
        self.assertAlmostEqual(LAYFLAT["bleed_mm"], left.measured_value, places=6)


class TestPageCount(unittest.TestCase):
    def test_below_minimum_blocks(self):
        report = run(album(18))
        self.assertEqual("fail", report.status)
        bad = findings(report, "page_count_valid")[0]
        self.assertEqual(18.0, bad.measured_value)
        self.assertEqual(20.0, bad.required_value)
        self.assertIn("2 below", bad.detail)

    def test_above_maximum_blocks(self):
        self.assertEqual("fail", run(album(102)).status)

    def test_off_increment_blocks(self):
        report = run(album(21))
        self.assertEqual("fail", report.status)
        self.assertIn("multiple", findings(report, "page_count_valid")[0].detail)

    def test_duplicate_page_index_blocks(self):
        pages = album(20)
        pages[7]["page_index"] = 6
        report = run(pages)
        self.assertEqual("fail", report.status)
        self.assertTrue(findings(report, "page_count_valid"))

    def test_missing_page_index_blocks(self):
        pages = album(20)
        del pages[3]["page_index"]
        self.assertEqual("fail", run(pages).status)

    def test_an_unreadable_placement_is_not_quietly_dropped(self):
        """A placement the iteration cannot parse is a placement no gate ever
        sees. Skipping it means the album can pass on the strength of the
        photos that happened to be well-formed."""
        pages = album(20)
        pages[4]["placements"] = ["not-a-placement"]
        report = run(pages)
        self.assertEqual("fail", report.status)
        self.assertIn("invisible to every per-placement gate",
                      findings(report, "page_count_valid")[0].detail)

    def test_an_unreadable_page_is_not_quietly_dropped(self):
        pages = album(20)
        pages[9] = "not-a-page"
        self.assertEqual("fail", run(pages).status)


class TestVendorProfilesActuallyDiffer(unittest.TestCase):
    """The abstraction is only real if the same album gets different answers.
    A validator with any of these numbers baked in passes this suite's other
    tests and approves the wrong book."""

    def test_page_count_answer_depends_on_the_vendor(self):
        twenty = album(20)
        self.assertEqual("pass", run(twenty).status)
        self.assertEqual("fail", run(twenty, profile=PERFECT_BOUND).status)

    def test_face_severity_depends_on_the_vendors_safe_margin(self):
        """7mm into the safe band leaves 1mm of paper on a layflat book (blocked)
        and 3mm on a perfect-bound one (a warning). Same album, same face."""
        risky = placement(
            "p0", safety=face_safety(count=1, all_safe=False, trim=1, margin=-7.0)
        )
        self.assertEqual("fail", run(album(20, {0: [risky]})).status)
        self.assertEqual(
            "pass", run(album(24, {0: [risky]}), profile=PERFECT_BOUND).status
        )

    def test_dpi_floor_comes_from_the_profile(self):
        strict = json.loads(json.dumps(LAYFLAT))
        strict["dpi_floor"] = 700.0
        self.assertEqual("pass", run(album(20)).status)
        self.assertEqual("fail", run(album(20), profile=strict).status)


class TestDeterminism(unittest.TestCase):
    def test_page_and_placement_order_do_not_change_the_report(self):
        pages = album(20)
        shuffled = list(reversed(pages))
        self.assertEqual(run(pages).to_dict(), run(shuffled).to_dict())

    def test_a_tie_for_worst_offender_is_broken_the_same_way_every_time(self):
        """The golden fixture has three placements at exactly 324.0 DPI. A
        min() over an unsorted list names whichever the iteration reached
        first, which is stable until someone reorders the pages."""
        tied = {
            5: [placement("p5", width_mm=280.0, height_mm=186.6)],
            11: [placement("p11", width_mm=280.0, height_mm=186.6)],
            17: [placement("p17", width_mm=280.0, height_mm=186.6)],
        }
        forwards = run(album(20, tied))
        backwards = run(list(reversed(album(20, tied))))
        self.assertEqual(forwards.to_dict(), backwards.to_dict())
        self.assertEqual(5, summary(forwards, "dpi_floor").page_index)

    def test_no_clock_reading_leaks_into_the_report(self):
        """validated_at is an argument. A now() inside would make two runs of
        the same album differ, which is the determinism guarantee the whole
        product rests on."""
        report = validate_album(
            pages=album(20),
            vendor_profile=LAYFLAT,
            sources={MEDIA: SourceImage(SOURCE_W, SOURCE_H)},
            document_color=DOCUMENT_OK,
        )
        self.assertIsNone(report.validated_at)


class TestFailuresAreActionable(unittest.TestCase):
    """The fix is a human moving a photo, so every failure has to say which
    photo, on which page, and by how much."""

    def test_a_low_dpi_failure_locates_itself(self):
        report = run(album(20, {7: [low_dpi_placement("p7")]}))
        blocking = [f for f in report.blocking if f.check_id == "dpi_floor"]
        self.assertEqual(1, len(blocking))
        found = blocking[0]
        self.assertEqual(7, found.page_index)
        self.assertEqual("p7", found.placement_id)
        self.assertEqual(300.0, found.required_value)
        self.assertLess(found.measured_value, 300.0)
        self.assertIn("below the vendor floor", found.detail)
        self.assertIn("mm", found.remediation)

    def test_every_blocking_finding_carries_a_remediation(self):
        cases = [
            album(18),
            album(20, {0: [low_dpi_placement("p0")]}),
            album(20, {0: [placement("p0", bleeds=("left",), x=1.0, y=0.0,
                                     width_mm=305.0, height_mm=306.0,
                                     crop_w=0.6667, crop_h=1.0)]}),
            album(20, {0: [placement("p0", safety=face_safety(
                count=1, all_safe=False, trim=1, margin=-8.0))]}),
        ]
        for index, pages in enumerate(cases):
            with self.subTest(case=index):
                report = run(pages)
                self.assertEqual("fail", report.status)
                for found in report.blocking:
                    self.assertTrue(found.remediation, found.detail)
                    self.assertTrue(found.detail)

    def test_the_remediation_frame_width_actually_clears_the_floor(self):
        """A remediation that does not fix the problem is worse than none: the
        human follows it to the letter, re-validates, and fails again by a
        hundredth of a DPI, and concludes the validator is broken.

        Swept across source sizes rather than tried once, because the suggested
        width is printed to one decimal and only about half of all inputs
        expose the difference between rounding it and flooring it. A single
        sample picks its own answer.
        """
        for source_w in range(1500, 1700, 7):
            source = (source_w, round(source_w * 2 / 3))
            sources = {**DEFAULT_SOURCES, SMALL_MEDIA: SourceImage(*source)}
            with self.subTest(source=source):
                report = run(
                    album(20, {0: [placement("p0", media_id=SMALL_MEDIA, source=source)]}),
                    sources=sources,
                )
                found = [f for f in report.blocking if f.check_id == "dpi_floor"][0]
                suggested = float(found.remediation.split("to ")[1].split("mm")[0])
                fixed = placement("p0", media_id=SMALL_MEDIA, source=source,
                                  width_mm=suggested,
                                  height_mm=suggested * 135.9 / 203.9)
                after = run(album(20, {0: [fixed]}), sources=sources)
                self.assertEqual("pass", after.status,
                                 [f.detail for f in after.blocking])


class TestGoldenFixture(unittest.TestCase):
    """Run the real contract fixture through the real validator.

    The fixture carries no source resolutions, so they are derived from the
    declared effective_dpi and the geometry -- which is exactly the arithmetic
    under test, so the fixture is used for shape, ordering and schema
    conformance rather than as an independent oracle of the DPI maths.
    """

    @staticmethod
    def derive_sources(spec):
        implied: dict[str, tuple[float, float]] = {}
        for page_ in spec["pages"]:
            for pl in page_["placements"]:
                frame, crop = pl["frame"], pl["crop"]
                width = pl["effective_dpi"] * frame["width_mm"] / (MM_PER_INCH * crop["w"])
                height = pl["effective_dpi"] * frame["height_mm"] / (MM_PER_INCH * crop["h"])
                previous = implied.get(pl["media_id"], (0.0, 0.0))
                implied[pl["media_id"]] = (max(previous[0], width),
                                           max(previous[1], height))
        return {media: SourceImage(round(w), round(h)) for media, (w, h) in implied.items()}

    @classmethod
    def setUpClass(cls):
        cls.spec = json.loads(FIXTURE.read_text(encoding="utf-8"))
        cls.sources = cls.derive_sources(cls.spec)

    def test_the_fixture_passes(self):
        report = validate_album_spec(
            self.spec, sources=self.sources, document_color=DOCUMENT_OK,
            validated_at="2026-03-17T14:22:11+05:30",
        )
        self.assertEqual("pass", report.status, [f.detail for f in report.blocking])

    def test_the_produced_report_satisfies_the_albumspec_contract(self):
        """The report is part of the spec, not a side effect of rendering it.
        If what this module emits does not validate, render-print rejects the
        book for a reason that has nothing to do with the book."""
        report = validate_album_spec(
            self.spec, sources=self.sources, document_color=DOCUMENT_OK,
            validated_at="2026-03-17T14:22:11+05:30",
        )
        spec = json.loads(json.dumps(self.spec))
        spec["validation"] = report.to_dict()
        errors = sorted(_schema_validator().iter_errors(spec), key=lambda e: list(e.path))
        self.assertEqual([], [f"{list(e.path)}: {e.message}" for e in errors])

    def test_the_schema_rejects_a_report_that_drops_a_hard_gate(self):
        """Confirms the contract-side half of the gate really bites, so the
        module's own belt-and-braces assertion is not the only thing standing
        between a missing check and an export."""
        report = validate_album_spec(
            self.spec, sources=self.sources, document_color=DOCUMENT_OK,
        )
        forged = report.to_dict()
        forged["checks"] = [c for c in forged["checks"] if c["check_id"] != "dpi_floor"]
        spec = json.loads(json.dumps(self.spec))
        spec["validation"] = forged
        self.assertTrue(list(_schema_validator().iter_errors(spec)))

    def test_one_degraded_placement_fails_the_whole_album(self):
        spec = json.loads(json.dumps(self.spec))
        victim = spec["pages"][5]["placements"][0]
        victim["frame"]["width_mm"] = 280.0
        victim["frame"]["height_mm"] = 186.6
        victim["effective_dpi"] = dpi_for(
            280.0, 186.6, victim["crop"]["w"], victim["crop"]["h"],
            (self.sources[victim["media_id"]].oriented_width_px,
             self.sources[victim["media_id"]].oriented_height_px),
        )
        report = validate_album_spec(spec, sources=self.sources,
                                     document_color=DOCUMENT_OK)
        self.assertEqual("fail", report.status)
        blocking = [f for f in report.blocking if f.check_id == "dpi_floor"]
        self.assertEqual(5, blocking[0].page_index)
        self.assertEqual("p5-hero", blocking[0].placement_id)

    def test_a_failing_report_also_satisfies_the_contract(self):
        spec = json.loads(json.dumps(self.spec))
        report = validate_album_spec(spec, sources={}, document_color=None)
        self.assertEqual("fail", report.status)
        spec["validation"] = report.to_dict()
        errors = list(_schema_validator().iter_errors(spec))
        self.assertEqual([], [f"{list(e.path)}: {e.message}" for e in errors])


class TestSchemaDefaultsAreNotMissingData(unittest.TestCase):
    """A field the CONTRACT has already answered is not a field nobody answered.

    Four properties on the AlbumSpec path are optional AND carry an explicit
    JSON-Schema `default`: NormalizedBox.rotation_deg (0), RectMm.rotation_deg
    (0), FaceSafety.faces_in_trim_zone (0) and FaceSafety.faces_in_gutter (0).
    A producer that leaves them out has not gone silent -- it has said "zero" in
    the only way the schema offers. Reading the omission as unusable made this
    validator block books that were correct, and a print gate that blocks
    correct books is a print gate that gets an override flag bolted onto it.

    The other half of the rule still holds and is tested below: a key that is
    PRESENT and unreadable fails, and a `required` field that is absent fails.
    """

    @staticmethod
    def _strip_defaults(pages):
        for page_ in pages:
            for pl in page_["placements"]:
                del pl["crop"]["rotation_deg"]
                del pl["frame"]["rotation_deg"]
                del pl["face_safety"]["faces_in_trim_zone"]
                del pl["face_safety"]["faces_in_gutter"]
        return pages

    def test_the_stripped_shape_is_a_legal_albumspec(self):
        """Proves the input below is contract-valid, so the next test is about
        the validator being wrong and not about the fixture being wrong."""
        spec = json.loads(FIXTURE.read_text(encoding="utf-8"))
        self._strip_defaults(spec["pages"])
        errors = list(_schema_validator().iter_errors(spec))
        self.assertEqual([], [f"{list(e.path)}: {e.message}" for e in errors])

    def test_an_album_that_omits_them_is_not_rejected(self):
        report = run(self._strip_defaults(album(20)))
        self.assertEqual("pass", report.status, [f.detail for f in report.blocking])
        self.assertEqual(0, report.error_count)

    def test_the_golden_fixture_still_passes_with_them_stripped(self):
        spec = json.loads(FIXTURE.read_text(encoding="utf-8"))
        self._strip_defaults(spec["pages"])
        sources = TestGoldenFixture.derive_sources(spec)
        report = validate_album_spec(spec, sources=sources, document_color=DOCUMENT_OK)
        self.assertEqual("pass", report.status, [f.detail for f in report.blocking])

    def test_a_present_but_unreadable_rotation_still_fails(self):
        """`"0"` and `null` are not the schema's default. Something wrote them,
        and what it wrote is not a number."""
        for field, value in (("crop", None), ("crop", "0"), ("frame", None),
                             ("frame", "0"), ("crop", float("nan"))):
            with self.subTest(field=field, value=value):
                bad = placement("p0")
                bad[field]["rotation_deg"] = value
                self.assertEqual("fail", run(album(20, {0: [bad]})).status)

    def test_a_missing_required_face_field_still_fails(self):
        """face_count and all_faces_in_safe_zone are `required` with no default,
        so their absence really does mean nobody looked."""
        for field in ("face_count", "all_faces_in_safe_zone"):
            with self.subTest(field=field):
                bad = placement("p0")
                del bad["face_safety"][field]
                self.assertEqual("fail", run(album(20, {0: [bad]})).status)

    def test_a_present_but_unreadable_face_count_still_fails(self):
        for value in (-1, "1", 1.5, None, True):
            with self.subTest(value=value):
                bad = placement("p0")
                bad["face_safety"]["faces_in_trim_zone"] = value
                self.assertEqual("fail", run(album(20, {0: [bad]})).status)

    def test_defaulting_the_counts_does_not_silence_the_contradiction_check(self):
        """The safety net that makes defaulting these two safe: a producer that
        omits both while declaring the faces unsafe is still caught, because
        nothing is then attributed to the trim zone or the gutter."""
        unsafe = placement("p0", safety={
            "face_count": 2,
            "all_faces_in_safe_zone": False,
            "min_face_margin_mm": 1.0,
            "cropped_face_ids": [],
        })
        report = run(album(20, {0: [unsafe]}))
        self.assertEqual("fail", report.status)
        self.assertIn("no face is attributed",
                      findings(report, "face_in_trim_zone")[0].detail)


class TestCropBounds(unittest.TestCase):
    """A NormalizedBox lives in [0,1] of the oriented image, and effective DPI
    multiplies its width by the source's pixel width. A crop that claims pixels
    outside the image inflates that product directly."""

    def _with_crop(self, **crop):
        bad = placement("p0", media_id=SMALL_MEDIA, source=SMALL_SOURCE)
        bad["crop"].update(crop)
        bad["effective_dpi"] = dpi_for(
            203.9, 135.9, bad["crop"]["w"], bad["crop"]["h"], SMALL_SOURCE
        )
        return bad

    def test_a_crop_wider_than_its_image_cannot_manufacture_dpi(self):
        bad = self._with_crop(x=0.0, y=0.0, w=4.0, h=4.0)
        # The naive arithmetic this gate performs, unguarded: a 1600px phone
        # JPEG declared at four times its own width prints at 797 DPI on paper,
        # clears a 300 floor with room to spare, and arrives as mush.
        self.assertGreater(bad["effective_dpi"], 2 * LAYFLAT["dpi_floor"])
        report = run(album(20, {0: [bad]}))
        self.assertEqual("fail", report.status)
        self.assertIn("larger than the image",
                      findings(report, "dpi_floor")[0].detail)

    def test_a_crop_running_off_the_edge_cannot_manufacture_dpi(self):
        """x=0.6 with w=0.5 has only 0.4 of the image to its right, so a fifth
        of the pixels it bills for do not exist."""
        bad = self._with_crop(x=0.6, y=0.0, w=0.5, h=0.5)
        report = run(album(20, {0: [bad]}))
        self.assertEqual("fail", report.status)
        self.assertIn("runs off the image", findings(report, "dpi_floor")[0].detail)

    def test_an_origin_outside_the_image_fails(self):
        """Deliberately sized so nothing else catches it: x=-0.1 with w=1.0 sums
        to 0.9 so the containment check is satisfied, and the crop is big enough
        that the DPI clears the floor comfortably. The only thing wrong with it
        is that it starts 10% to the left of the image, where the source has no
        pixels at all -- so the leftmost tenth of the printed photo is whatever
        the renderer decides to put there, which is not a decision the renderer
        is allowed to make."""
        for x, y in ((-0.1, 0.0), (0.0, -0.3), (1.2, 0.0)):
            with self.subTest(x=x, y=y):
                bad = placement("p0")
                bad["crop"].update(x=x, y=y, w=1.0, h=1.0)
                bad["effective_dpi"] = dpi_for(203.9, 135.9, 1.0, 1.0)
                self.assertGreater(bad["effective_dpi"], 2 * LAYFLAT["dpi_floor"])
                report = run(album(20, {0: [bad]}))
                self.assertEqual("fail", report.status)
                self.assertIn("outside the [0,1] image",
                              findings(report, "dpi_floor")[0].detail)

    def test_a_crop_flush_with_the_image_edge_is_fine(self):
        """The containment check must not reject the commonest crop there is:
        the whole frame, and a crop pushed hard against one edge."""
        for x, w in ((0.0, 1.0), (0.14, 0.86)):
            with self.subTest(x=x, w=w):
                good = placement("p0")
                good["crop"].update(x=x, y=0.0, w=w, h=1.0)
                good["effective_dpi"] = dpi_for(203.9, 135.9, w, 1.0)
                self.assertEqual("pass", run(album(20, {0: [good]})).status)


class TestAPageThatCannotBeReadIsNeverBlank(unittest.TestCase):
    """The worst possible failure for a print validator: a page whose contents
    were unreadable is reported as an empty page, and empty pages pass."""

    def test_the_naive_truthiness_read_would_have_blanked_the_page(self):
        for value in (0, False, "", {}, None):
            self.assertEqual([], value or [])  # `get("placements") or []`

    def test_a_placements_field_that_is_not_a_list_is_reported(self):
        for value in (0, False, "", {}, None, "p0"):
            with self.subTest(value=value):
                pages = album(20)
                pages[4]["placements"] = value
                report = run(pages)
                self.assertEqual("fail", report.status)
                self.assertIn(
                    "invisible to every per-placement gate",
                    " ".join(f.detail for f in findings(report, "page_count_valid")),
                )

    def test_an_absent_placements_field_is_reported(self):
        """`placements` is `required` on Page, so unlike rotation_deg there is
        no contract-blessed way to leave it out. A blank page says `[]`."""
        pages = album(20)
        del pages[4]["placements"]
        report = run(pages)
        self.assertEqual("fail", report.status)
        self.assertTrue(findings(report, "page_count_valid"))

    def test_an_explicitly_empty_page_is_still_allowed(self):
        pages = album(20)
        pages[4]["placements"] = []
        self.assertEqual("pass", run(pages).status)


class TestCalibrationConstantsEarnTheirValue(unittest.TestCase):
    """Each of these pins the behaviour a constant exists to produce, on an
    input where a materially different value gives a different verdict. None of
    them asserts the literal."""

    def test_a_five_percent_lie_in_the_declared_dpi_is_caught(self):
        """DPI_RELATIVE_TOLERANCE exists to absorb the planner printing its
        output to one decimal place, nothing more. A declaration 5% above the
        truth is 32 DPI at this placement's 643 -- the difference between a
        sharp photo and a soft one, and evidence that the planner and the
        validator disagree about the geometry. Anything above about 5% slack
        lets that through. (The exact figure is a judgement call; what is not a
        judgement call is that it must be far below the size of a lie that
        matters.)
        """
        truth = dpi_for(203.9, 135.9, 0.86, 0.86)
        liar = placement("p0", dpi=truth * 1.05)
        report = run(album(20, {0: [liar]}))
        self.assertEqual("fail", report.status)
        self.assertIn("cannot tell which", findings(report, "dpi_floor")[0].detail)

    def test_the_geometry_epsilon_absorbs_float_noise(self):
        """A 12-inch square book: 304.8mm trim, 3mm bleed, a frame sitting
        exactly flush with the right edge of the bleed box. The subtraction
        lands 5.7e-14mm short of 3.0 and the frame is perfect. Without the
        epsilon the gate rejects flawless geometry for the last bits of a
        double, which is how a validator earns a reputation for lying.
        """
        profile = json.loads(json.dumps(LAYFLAT))
        profile["trim_size_mm"] = {"width_mm": 304.8, "height_mm": 304.8}
        x, w = 16.03, 294.77
        self.assertLess((x + w) - (3.0 + 304.8), 3.0)  # the noise, in the raw
        flush = placement("p0", bleeds=("right",), x=x, y=50.0, width_mm=w,
                          height_mm=196.51, crop_w=1.0, crop_h=1.0)
        report = run(album(20, {0: [flush]}), profile=profile)
        self.assertEqual("pass", report.status, [f.detail for f in report.blocking])

    def test_the_geometry_epsilon_is_not_a_bleed_allowance(self):
        """A thousandth of a millimetre short is still short. The epsilon is
        float slack; every millimetre of it that is not is a millimetre of white
        paper on a finished edge that this gate signed off."""
        short = placement("p0", bleeds=("left",), x=0.001, y=0.0, width_mm=305.999,
                          height_mm=306.0, crop_w=0.6667, crop_h=1.0)
        report = run(album(20, {0: [short]}))
        self.assertEqual("fail", report.status)
        self.assertTrue([f for f in report.blocking if f.check_id == "bleed_coverage"])

    def test_half_a_millimetre_of_undeclared_spill_is_worth_saying(self):
        """UNDECLARED_SPILL_THRESHOLD_MM separates float dust from artwork the
        guillotine is going to remove. 0.5mm off the edge of a photo is visible
        on the finished page; 0.01mm is the planner's arithmetic."""
        for x, expected in ((2.5, 1), (2.99, 0)):
            with self.subTest(x=x):
                spill = placement("p0", x=x, y=50.0, width_mm=200.0, height_mm=133.3)
                report = run(album(20, {0: [spill]}))
                warned = [f for f in report.warnings if f.check_id == "bleed_coverage"]
                self.assertEqual(expected, len(warned))


class TestPreferredDpiIsReportedWithoutBlocking(unittest.TestCase):
    def test_below_preferred_but_above_the_floor_warns(self):
        """dpi_preferred is the vendor saying 'this will print, but we would
        rather it did not'. Dropping the branch loses the only signal that
        distinguishes a book that is fine from a book that is merely legal, and
        loses it silently -- nothing fails, the warning just stops existing."""
        source = (2941, 1961)
        soft = placement("p0", media_id=SMALL_MEDIA, source=source)
        report = run(
            album(20, {0: [soft]}),
            sources={**DEFAULT_SOURCES, SMALL_MEDIA: SourceImage(*source)},
        )
        self.assertEqual("pass", report.status, [f.detail for f in report.blocking])
        warned = [f for f in report.warnings if f.check_id == "dpi_floor"]
        self.assertEqual(1, len(warned))
        self.assertEqual(0, warned[0].page_index)
        self.assertEqual("p0", warned[0].placement_id)
        self.assertEqual(LAYFLAT["dpi_preferred"], warned[0].required_value)
        self.assertLess(warned[0].measured_value, LAYFLAT["dpi_preferred"])
        self.assertGreater(warned[0].measured_value, LAYFLAT["dpi_floor"])

    def test_a_profile_with_no_preferred_dpi_simply_does_not_warn(self):
        """dpi_preferred is optional. Its absence is not a failure -- unlike
        dpi_floor, nothing physical depends on it."""
        profile = dict(LAYFLAT)
        del profile["dpi_preferred"]
        source = (2941, 1961)
        soft = placement("p0", media_id=SMALL_MEDIA, source=source)
        report = run(
            album(20, {0: [soft]}), profile=profile,
            sources={**DEFAULT_SOURCES, SMALL_MEDIA: SourceImage(*source)},
        )
        self.assertEqual("pass", report.status)
        self.assertEqual([], [f for f in report.warnings if f.check_id == "dpi_floor"])


class TestRotatedBoundingBox(unittest.TestCase):
    def test_the_rotated_extent_uses_the_right_axis_for_each_dimension(self):
        """A 200x100mm frame turned 30 degrees is 111.60mm wide and 93.30mm tall
        from its centre. Swapping the two formulas gives 93.30 and 111.60 --
        still plausible, still symmetric, and wrong for every frame that is not
        square. Here it hides a 4.6mm horizontal overhang and invents a 59.6mm
        vertical one, so the report names the wrong edge and the wrong amount.
        """
        tilted = placement("p0", x=10.0, y=5.0, width_mm=200.0, height_mm=100.0,
                           rotation_deg=30.0)
        report = run(album(20, {0: [tilted]}))
        warned = {
            f.detail.split("past the ")[1].split(" trim")[0]: f.measured_value
            for f in report.warnings if f.check_id == "bleed_coverage"
        }
        self.assertEqual({"left", "top"}, set(warned))
        self.assertAlmostEqual(4.6025403784, warned["left"], places=6)
        self.assertAlmostEqual(41.3012701892, warned["top"], places=6)


class TestReportShape(unittest.TestCase):
    def test_checks_are_emitted_in_gate_order(self):
        """GATE_ORDER is the order a human reads the report in, hardest physical
        gate first. It is also the only thing that decides that order -- a gate
        that emits itself wherever its call happens to sit produces a report
        whose shape changes when someone reorders two lines."""
        report = run(album(20, {0: [low_dpi_placement("p0")]}))
        seen = []
        for check in report.checks:
            if not seen or seen[-1] != check.check_id:
                seen.append(check.check_id)
        self.assertEqual(len(set(seen)), len(seen))  # no gate emitted in two runs
        self.assertEqual([g for g in GATE_ORDER if g in set(seen)], seen)

    def test_a_finding_from_an_unknown_gate_is_appended_not_dropped(self):
        """Ordering the report by GATE_ORDER means asking, for each known gate,
        which findings belong to it -- and the failure mode of that shape is
        that a finding belonging to no known gate is never asked for. Silent
        loss of a finding is exactly what this module exists not to do, so the
        leftovers are appended. Reached here by injecting a gate that emits an
        id GATE_ORDER has never heard of, because no real gate does today.
        """
        from unittest import mock

        from memory_engine_album import validator as module

        stray = module.Finding(
            check_id="spine_width_valid", severity="error", passed=False,
            detail="a gate that was added without touching GATE_ORDER",
        )
        original = module._check_page_count

        def with_stray(*args, **kwargs):
            return [*original(*args, **kwargs), stray]

        with mock.patch.object(module, "_check_page_count", with_stray):
            report = run(album(20))
        self.assertIn(stray, report.checks)
        self.assertEqual("fail", report.status)
        self.assertEqual(1, report.error_count)

    def test_reported_numbers_are_rounded_for_presentation_only(self):
        """299.99996 DPI against a 300 floor. The report says 300.0 because
        nobody wants seventeen significant figures in a PDF-side report -- and
        the album still fails, because the comparison was made on the number and
        not on the presentation of it. Round before comparing and this book gets
        printed; do not round at all and every measured value in the report is a
        float artefact that makes two byte-identical runs look different.
        """
        hair = placement("p0", width_mm=254.0000338666712, height_mm=254.0,
                         crop_w=1.0, crop_h=1.0, source=(3000, 3000))
        report = run(album(20, {0: [hair]}), sources={MEDIA: SourceImage(3000, 3000)})
        self.assertEqual("fail", report.status)
        found = [f for f in report.blocking if f.check_id == "dpi_floor"][0]
        self.assertLess(found.measured_value, LAYFLAT["dpi_floor"])
        self.assertEqual(300.0, found.to_dict()["measured_value"])

    def test_the_worst_offender_tiebreak_does_not_rely_on_input_order(self):
        """Unit-level, because through `validate_album` the placements are
        already sorted before they reach `_worst` and the tiebreak never fires.
        It is there so that stays true for a future caller that builds its list
        some other way -- and an untested guarantee is not a guarantee.
        """
        def ctx(order, page_index, placement_id):
            return _Ctx(order=order, page_index=page_index, side="left",
                        spread_id=None, placement_id=placement_id,
                        media_id=MEDIA, placement={})

        tied = [
            (324.0, ctx(0, 11, "p11")),
            (324.0, ctx(1, 5, "p5")),
            (324.0, ctx(2, 17, "p17")),
            (401.0, ctx(3, 1, "p1")),
        ]
        self.assertEqual(5, _worst(tied)[1].page_index)
        self.assertEqual(5, _worst(list(reversed(tied)))[1].page_index)
        self.assertIsNone(_worst([]))


def _schema_validator():
    from jsonschema import Draft202012Validator
    from referencing import Registry, Resource

    documents = {
        path.name: json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(SCHEMA_DIR.glob("*.schema.json"))
    }
    registry = Registry().with_resources(
        [(name, Resource.from_contents(doc)) for name, doc in documents.items()]
    )
    return Draft202012Validator({"$ref": "album-spec.schema.json"}, registry=registry)


class TestInputShapes(unittest.TestCase):
    def test_dataclass_pages_are_accepted(self):
        """Layout is another agent's module, so this one must not depend on its
        types. Mappings and dataclasses both work; anything else is refused
        loudly rather than iterated into silence."""
        from dataclasses import dataclass, field

        @dataclass
        class Frame:
            x_mm: float
            y_mm: float
            width_mm: float
            height_mm: float
            rotation_deg: float = 0.0

        @dataclass
        class Crop:
            x: float
            y: float
            w: float
            h: float
            rotation_deg: float = 0.0

        @dataclass
        class Placement:
            placement_id: str
            media_id: str
            frame: Frame
            crop: Crop
            effective_dpi: float
            bleeds: list = field(default_factory=list)
            face_safety: dict = field(default_factory=lambda: face_safety())

        @dataclass
        class Page:
            page_index: int
            side: str
            placements: list

        pages = [
            Page(
                page_index=i,
                side="left",
                placements=[
                    Placement(
                        placement_id=f"p{i}",
                        media_id=MEDIA,
                        frame=Frame(51.0, 86.0, 203.9, 135.9),
                        crop=Crop(0.07, 0.07, 0.86, 0.86),
                        effective_dpi=dpi_for(203.9, 135.9, 0.86, 0.86),
                    )
                ],
            )
            for i in range(20)
        ]
        self.assertEqual("pass", run(pages).status)

    def test_a_non_mapping_profile_is_refused_loudly(self):
        with self.assertRaises(TypeError):
            validate_album(pages=album(), vendor_profile="layflat", sources={})

    def test_a_negative_drift_is_refused_loudly(self):
        with self.assertRaises(ValueError):
            run(album(), guillotine_drift_mm=-1.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
