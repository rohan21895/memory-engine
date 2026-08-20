"""Per-face expression backfill: crop geometry, the frozen mapping, graceful
degradation, face exposure, and the group-photo rollup.

The one rule under test everywhere here: an absent measurement stays absent.
A host that is down, an artifact that is missing, a crop too small to mean
anything -- each leaves eyes_open/smile null while sharpness is still written,
and none of them crashes the backfill.
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from support import REPO_ROOT  # noqa: F401 - sys.path bootstrap for the package

from memory_engine_pipeline import classical
from memory_engine_pipeline.stages import faces
from memory_engine_pipeline.stages.base import Settings


# ------------------------------------------------------------------- fakes --


class FakeReporter:
    def __init__(self) -> None:
        self.events: list[tuple[str, str, str]] = []

    def event(self, stage: str, kind: str, message: str = "", **detail: Any) -> None:
        self.events.append((stage, kind, message))

    def kinds(self) -> list[str]:
        return [kind for _, kind, _ in self.events]


class FakeDatabase:
    """Just the five calls the backfill makes, over plain dicts."""

    def __init__(self, media: dict[str, dict], faces_by_media: dict[str, list[dict]],
                 proxies: dict[str, list[dict]]) -> None:
        self.media = media
        self.faces_by_media = faces_by_media
        self.proxies = proxies
        self.put_faces: list[dict] = []
        self.put_medias: list[dict] = []

    def get_media(self, media_id: str) -> dict | None:
        return self.media.get(media_id)

    def put_media(self, record: dict) -> str:
        self.media[record["media_id"]] = record
        self.put_medias.append(record)
        return record["media_id"]

    def proxies_for_media(self, media_id: str, kind: str | None = None) -> list[dict]:
        return [p for p in self.proxies.get(media_id, [])
                if kind is None or p.get("kind") == kind]

    def put_face(self, record: dict) -> str:
        self.put_faces.append(record)
        return record["face_id"]

    def faces_for_media(self, media_id: str) -> list[dict]:
        return self.faces_by_media.get(media_id, [])


def _face(face_id: str, media_id: str, *, area: float = 0.05,
          bbox: dict | None = None, score: float = 0.9,
          attributes: dict | None = None) -> dict:
    return {
        "face_id": face_id,
        "media_id": media_id,
        "detection": {
            "bbox": bbox or {"x": 0.3, "y": 0.3, "w": 0.25, "h": 0.25},
            "detection_score": score,
            "face_area_ratio": area,
        },
        "attributes": attributes or {},
    }


def _ctx(database: FakeDatabase, reporter: FakeReporter, *,
         repo_root: Path, endpoint: str = "127.0.0.1:1") -> SimpleNamespace:
    """The slice of StageContext the backfill reads. The default endpoint is
    a port nothing listens on, so 'host down' is the default fixture state."""
    return SimpleNamespace(
        database=database,
        reporter=reporter,
        repo_root=repo_root,
        settings=Settings(ml_runtime_endpoint=endpoint, ml_runtime_timeout_s=0.3),
    )


# ---------------------------------------------------------------- geometry --


class CropGeometry(unittest.TestCase):
    def test_growth_is_1_3x_about_the_centre(self):
        box = {"x": 0.4, "y": 0.4, "w": 0.2, "h": 0.2}
        grown = faces._grown_box(box, faces._EXPRESSION_BOX_SCALE)
        self.assertAlmostEqual(grown["w"], 0.26)
        self.assertAlmostEqual(grown["h"], 0.26)
        # Same centre.
        self.assertAlmostEqual(grown["x"] + grown["w"] / 2, 0.5)
        self.assertAlmostEqual(grown["y"] + grown["h"] / 2, 0.5)

    def test_clamped_at_the_frame_and_never_out_of_range(self):
        corner = {"x": 0.0, "y": 0.9, "w": 0.15, "h": 0.1}
        grown = faces._grown_box(corner, faces._EXPRESSION_BOX_SCALE)
        self.assertGreaterEqual(grown["x"], 0.0)
        self.assertGreaterEqual(grown["y"], 0.0)
        self.assertLessEqual(grown["x"] + grown["w"], 1.0)
        self.assertLessEqual(grown["y"] + grown["h"], 1.0)
        # The grown size survives the clamp -- the box slides, it does not shrink.
        self.assertAlmostEqual(grown["w"], 0.15 * 1.3)

    def test_a_huge_box_caps_at_the_whole_frame(self):
        grown = faces._grown_box({"x": 0.05, "y": 0.05, "w": 0.9, "h": 0.9}, 1.3)
        self.assertEqual((grown["x"], grown["y"], grown["w"], grown["h"]),
                         (0.0, 0.0, 1.0, 1.0))


class ExpressionCrops(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="mep-expr-"))
        self.addCleanup(shutil.rmtree, self.root, True)

    def test_exif_orientation_is_applied_before_cropping(self):
        """bbox is normalised to the ORIENTED frame: a stored-landscape photo
        with orientation 6 must be rotated to portrait before pixel math."""
        from PIL import Image

        # Stored 200x100; orientation 6 (rotate 90 CW) -> oriented 100x200.
        image = Image.new("RGB", (200, 100), (10, 10, 10))
        # After a 90 CW rotation, stored (x, y) lands at oriented
        # (height-1-y, x): stored (150..199, 0..49) -> oriented x in 50..99
        # (from y 0..49) and oriented y in 150..199 (from x 150..199) --
        # the oriented bottom-right quadrant.
        for x in range(150, 200):
            for y in range(0, 50):
                image.putpixel((x, y), (250, 0, 0))
        exif = Image.Exif()
        exif[0x0112] = 6
        path = self.root / "oriented.jpg"
        image.save(path, format="JPEG", quality=95, exif=exif)

        # A box over the oriented bottom-right quadrant. 1.3x growth stays
        # inside the red region for the centre pixel check.
        bbox = {"x": 0.6, "y": 0.8, "w": 0.2, "h": 0.1}
        crop = faces._expression_crops(path, [bbox])[0]
        self.assertIsNotNone(crop)
        red, green, blue = crop.getpixel((crop.width // 2, crop.height // 2))
        self.assertGreater(red, 200, "orientation was not applied before cropping")
        self.assertLess(green, 60)

    def test_a_tiny_face_is_none_and_order_is_kept(self):
        from PIL import Image

        path = self.root / "plain.jpg"
        Image.new("RGB", (512, 384), (128, 128, 128)).save(path, format="JPEG")
        tiny = {"x": 0.5, "y": 0.5, "w": 0.005, "h": 0.005}
        big = {"x": 0.2, "y": 0.2, "w": 0.3, "h": 0.3}
        crops = faces._expression_crops(path, [tiny, big])
        self.assertIsNone(crops[0])
        self.assertIsNotNone(crops[1])


# ----------------------------------------------------------- frozen mapping --


class ContrastMapping(unittest.TestCase):
    def test_the_constant_is_frozen(self):
        self.assertEqual(faces.PER_FACE_CONTRAST_SCALE, 5.0)

    def test_the_mapping_is_the_frozen_formula(self):
        self.assertEqual(faces._expression_confidence(0.0), 0.5)
        self.assertEqual(faces._expression_confidence(0.05), 0.75)
        self.assertEqual(faces._expression_confidence(-0.05), 0.25)
        # Clamped, then rounded to six decimals.
        self.assertEqual(faces._expression_confidence(0.3), 1.0)
        self.assertEqual(faces._expression_confidence(-0.3), 0.0)
        self.assertEqual(faces._expression_confidence(0.0123456789), 0.561728)


# ---------------------------------------------------- graceful degradation --


class GracefulDegradation(unittest.TestCase):
    """Host down / artifact missing: sharpness is still written, expression
    stays null, exactly one warning names the reason, nothing raises."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="mep-degrade-"))
        self.addCleanup(shutil.rmtree, self.root, True)

    def _library(self):
        from PIL import Image

        thumb = self.root / "thumb.jpg"
        image = Image.new("L", (512, 384), 128)
        # Texture inside the face box so sharpness is measurable and nonzero.
        for x in range(150, 260, 6):
            for y in range(110, 210):
                image.putpixel((x, y), 240)
        image.convert("RGB").save(thumb, format="JPEG", quality=92)

        face = _face(
            "f" * 64, "m" * 64, area=0.06,
            bbox={"x": 150 / 512, "y": 110 / 384, "w": 110 / 512, "h": 100 / 384},
        )
        media = {"media_id": "m" * 64, "sources": [], "quality": {}}
        database = FakeDatabase(
            media={"m" * 64: media},
            faces_by_media={"m" * 64: [face]},
            proxies={"m" * 64: [{"kind": "thumbnail_512", "path": str(thumb)}]},
        )
        return database, [face]

    def test_host_down_writes_sharpness_and_leaves_expression_null(self):
        database, records = self._library()
        reporter = FakeReporter()
        # Real repo root: the head artifact exists, so the failure under test
        # is the unreachable host, not a missing file.
        ctx = _ctx(database, reporter, repo_root=REPO_ROOT)
        updated = faces._backfill_face_sharpness(ctx, records)
        self.assertEqual(updated, 1)
        attributes = database.put_faces[0]["attributes"]
        self.assertIsNotNone(attributes.get("sharpness"))
        self.assertEqual(attributes["quality"]["run_id"], faces._FACE_QUALITY_RUN_ID)
        self.assertNotIn("eyes_open", attributes)
        self.assertNotIn("smile", attributes)
        self.assertEqual(reporter.kinds().count("warning"), 1)
        # The media rollup was still refreshed from the fused quality.
        self.assertEqual(len(database.put_medias), 1)
        self.assertIsNotNone(
            database.put_medias[0]["quality"]["face_quality"]["value"]
        )

    def test_missing_head_artifact_degrades_the_same_way(self):
        database, records = self._library()
        reporter = FakeReporter()
        # An empty repo root: no artifact. The host must never be contacted --
        # with none listening this would otherwise burn the probe timeout.
        ctx = _ctx(database, reporter, repo_root=self.root)
        updated = faces._backfill_face_sharpness(ctx, records)
        self.assertEqual(updated, 1)
        attributes = database.put_faces[0]["attributes"]
        self.assertIsNotNone(attributes.get("sharpness"))
        self.assertNotIn("eyes_open", attributes)
        self.assertEqual(reporter.kinds().count("warning"), 1)

    def test_an_insignificant_face_is_never_scored(self):
        database, records = self._library()
        records[0]["detection"]["face_area_ratio"] = 0.01
        self.assertFalse(faces._needs_expression(records[0]))

    def test_a_face_with_stored_expression_does_not_repend(self):
        face = _face("a" * 64, "b" * 64, attributes={
            "sharpness": 0.5, "head_sharpness": 0.5,
            "eyes_open": 0.7, "smile": 0.6,
            "quality": {"value": 0.4, "run_id": faces._FACE_QUALITY_RUN_ID},
        })
        database = FakeDatabase(media={}, faces_by_media={}, proxies={})
        reporter = FakeReporter()
        ctx = _ctx(database, reporter, repo_root=REPO_ROOT)
        self.assertEqual(faces._backfill_face_sharpness(ctx, [face]), 0)
        self.assertEqual(database.put_faces, [])


# ------------------------------------------------------------ face exposure --


class FaceExposure(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="mep-exposure-"))
        self.addCleanup(shutil.rmtree, self.root, True)

    def _photo(self) -> Path:
        """A bright, partly clipped background with a dark face region."""
        from PIL import Image, ImageDraw

        image = Image.new("L", (512, 384), 255)      # clipped-white background
        draw = ImageDraw.Draw(image)
        draw.rectangle((100, 100, 199, 199), fill=20)  # dark face, not crushed
        path = self.root / "backlit.png"               # PNG: no JPEG ringing
        image.save(path, format="PNG")
        return path

    FACE = {"x": 100 / 512, "y": 100 / 384, "w": 100 / 512, "h": 100 / 384}

    def test_a_dark_face_on_a_bright_background_reads_dark_and_unclipped(self):
        mean, clipped = classical.measure_face_exposure(self._photo(), self.FACE)
        self.assertAlmostEqual(mean, 20 / 255, delta=0.02)
        self.assertEqual(clipped, 0.0)

    def test_the_bright_background_reads_bright_and_clipped(self):
        background = {"x": 0.6, "y": 0.6, "w": 0.3, "h": 0.3}
        mean, clipped = classical.measure_face_exposure(self._photo(), background)
        self.assertGreater(mean, 0.97)
        self.assertEqual(clipped, 1.0)

    def test_a_degenerate_box_raises_rather_than_scoring(self):
        with self.assertRaises(classical.ClassicalQualityError):
            classical.measure_face_exposure(
                self._photo(), {"x": 0.5, "y": 0.5, "w": 0.0, "h": 0.0}
            )
        with self.assertRaises(classical.ClassicalQualityError):
            classical.measure_face_exposure(self.root / "missing.png", self.FACE)


# ------------------------------------------------------------------- rollup --


class FaceQualityRollup(unittest.TestCase):
    """The summary is aggregate_face_quality over significant faces, not MAX."""

    MEDIA = "d" * 64

    def _database(self, face_specs):
        media = {"media_id": self.MEDIA, "quality": {}}
        face_records = [
            _face(f"{i:064d}", self.MEDIA, area=area, attributes=attributes)
            for i, (area, attributes) in enumerate(face_specs)
        ]
        return FakeDatabase(
            media={self.MEDIA: media},
            faces_by_media={self.MEDIA: face_records},
            proxies={},
        )

    def test_the_rollup_is_the_shared_aggregate_not_max(self):
        from memory_engine_ranking.fusion import aggregate_face_quality

        database = self._database([
            (0.05, {"quality": {"value": 0.9, "run_id": "x"}, "eyes_open": 0.8}),
            (0.05, {"quality": {"value": 0.2, "run_id": "x"}, "eyes_open": 0.3}),
            (0.01, {"quality": {"value": 0.99, "run_id": "x"}}),  # insignificant
        ])
        ctx = _ctx(database, FakeReporter(), repo_root=REPO_ROOT)
        faces._refresh_face_quality_summary(ctx, self.MEDIA)
        stored = database.media[self.MEDIA]["quality"]["face_quality"]
        self.assertEqual(
            stored["value"], aggregate_face_quality([0.9, 0.2], [0.8, 0.3])
        )
        self.assertEqual(stored["run_id"], faces._FACE_QUALITY_RUN_ID)
        self.assertNotEqual(stored["value"], 0.9, "this is the old MAX blind spot")

    def test_all_eyes_unmeasured_renormalises_rather_than_defaulting(self):
        from memory_engine_ranking.fusion import aggregate_face_quality

        database = self._database([
            (0.05, {"quality": {"value": 0.6, "run_id": "x"}}),
            (0.05, {"quality": {"value": 0.4, "run_id": "x"}}),
        ])
        ctx = _ctx(database, FakeReporter(), repo_root=REPO_ROOT)
        faces._refresh_face_quality_summary(ctx, self.MEDIA)
        stored = database.media[self.MEDIA]["quality"]["face_quality"]["value"]
        self.assertEqual(stored, aggregate_face_quality([0.6, 0.4], [None, None]))
        with_eyes = aggregate_face_quality([0.6, 0.4], [1.0, 1.0])
        self.assertNotEqual(stored, with_eyes)

    def test_no_significant_face_writes_no_claim(self):
        database = self._database([
            (0.01, {"quality": {"value": 0.9, "run_id": "x"}}),
        ])
        ctx = _ctx(database, FakeReporter(), repo_root=REPO_ROOT)
        faces._refresh_face_quality_summary(ctx, self.MEDIA)
        self.assertNotIn("face_quality", database.media[self.MEDIA]["quality"])


if __name__ == "__main__":
    unittest.main()
