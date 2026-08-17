from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

import numpy as np  # noqa: F401

WORKER_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = WORKER_ROOT.parents[1]
sys.path.insert(0, str(WORKER_ROOT))

from memory_engine_ml_runtime.postprocess import (
    Candidate,
    anchor_centers,
    geometric_mean_score,
    integer_letterbox,
    integer_rect_nms,
    normalize_candidate,
    num_anchors_for,
    yunet_decode_bbox,
    yunet_decode_kps,
)


def fixture(name: str) -> dict:
    return json.loads(
        (REPO_ROOT / "models" / "fixtures" / name).read_text(encoding="utf-8")
    )


class TestYuNetGoldenDecode(unittest.TestCase):
    def setUp(self) -> None:
        self.data = fixture("postprocess-yunet-decode.json")

    def test_decode_and_scores_match_the_executable_fixture(self) -> None:
        decoded = []
        for case in self.data["cases"]:
            box = yunet_decode_bbox(
                tuple(case["cell"]), case["bbox_raw"], self.data["stride"]
            )
            score = geometric_mean_score(case["cls"], case["obj"])
            landmarks = yunet_decode_kps(
                tuple(case["cell"]), case["landmarks_raw"], self.data["stride"]
            )
            expected = case["decoded_box"]
            self.assertAlmostEqual(expected["x1"], box.x1, places=6)
            self.assertAlmostEqual(expected["y1"], box.y1, places=6)
            self.assertAlmostEqual(expected["x2"], box.x2, places=6)
            self.assertAlmostEqual(expected["y2"], box.y2, places=6)
            self.assertAlmostEqual(case["score"], score, places=6)
            for actual, wanted in zip(landmarks, case["decoded_landmarks"]):
                self.assertAlmostEqual(wanted[0], actual[0], places=6)
                self.assertAlmostEqual(wanted[1], actual[1], places=6)
            decoded.append(Candidate(box.x1, box.y1, box.x2, box.y2, score, landmarks))

        filtered = [
            item for item in decoded if item.score >= self.data["score_threshold"]
        ]
        kept = integer_rect_nms(
            filtered, self.data["nms_threshold"], self.data["top_k"]
        )
        self.assertEqual(len(self.data["expected_after_nms"]), len(kept))
        for actual, expected in zip(kept, self.data["expected_after_nms"]):
            self.assertAlmostEqual(expected["x1"], actual.x1, places=6)
            self.assertAlmostEqual(expected["y1"], actual.y1, places=6)
            self.assertAlmostEqual(expected["x2"], actual.x2, places=6)
            self.assertAlmostEqual(expected["y2"], actual.y2, places=6)
            self.assertAlmostEqual(expected["score"], actual.score, places=6)

    def test_integer_rectangle_nms_takes_the_fixture_divergence_branch(self) -> None:
        divergence = self.data["nms_convention_divergence"]
        boxes = [Candidate(**box) for box in divergence["boxes"]]
        self.assertEqual(
            divergence["integer_rect_nms_keeps"],
            len(integer_rect_nms(boxes, divergence["nms_threshold"])),
        )

    def test_score_is_clamped_before_the_geometric_mean(self) -> None:
        self.assertEqual(0.0, geometric_mean_score(-1.0, 0.8))
        self.assertEqual(1.0, geometric_mean_score(1.2, 4.0))


class TestIntegerLetterboxGolden(unittest.TestCase):
    def test_every_real_raster_case_matches(self) -> None:
        for case in fixture("letterbox-integer-raster.json")["cases"]:
            with self.subTest(proxy=case["proxy"], backend=case["backend"]):
                source = case["proxy"]
                target = case["target"]
                expected = case["expected"]
                raster = integer_letterbox(
                    source["width"], source["height"], target["width"], target["height"]
                )
                self.assertEqual(expected["scaled_width"], raster.scaled_width)
                self.assertEqual(expected["scaled_height"], raster.scaled_height)
                self.assertEqual(expected["pad_left"], raster.pad_left)
                self.assertEqual(expected["pad_top"], raster.pad_top)
                self.assertEqual(expected["pad_right"], raster.pad_right)
                self.assertEqual(expected["pad_bottom"], raster.pad_bottom)
                self.assertAlmostEqual(
                    expected["effective_scale_x"], raster.effective_scale_x, places=9
                )
                self.assertAlmostEqual(
                    expected["effective_scale_y"], raster.effective_scale_y, places=9
                )
                for probe in case["probes"]:
                    actual = raster.to_normalized(*probe["letterboxed"])
                    self.assertAlmostEqual(probe["normalised"][0], actual[0], places=9)
                    self.assertAlmostEqual(probe["normalised"][1], actual[1], places=9)

    def test_box_and_landmark_normalisation_matches_fixture(self) -> None:
        data = fixture("detections-to-normalized-boxes.json")
        source = data["letterbox"]["source"]
        target = data["letterbox"]["target"]
        raster = integer_letterbox(
            source["width"], source["height"], target["width"], target["height"]
        )
        for case in data["cases"]:
            box = case["letterboxed_box"]
            candidate = Candidate(
                box["x1"],
                box["y1"],
                box["x2"],
                box["y2"],
                case["score"],
                tuple(tuple(point) for point in case.get("letterboxed_landmarks", [])),
            )
            actual = normalize_candidate(candidate, raster)
            if case["expected_action"] == "drop":
                self.assertIsNone(actual)
                continue
            assert actual is not None
            expected = case["expected_normalized_box"]
            self.assertAlmostEqual(expected["x"], actual.x, places=9)
            self.assertAlmostEqual(expected["y"], actual.y, places=9)
            self.assertAlmostEqual(expected["w"], actual.w, places=9)
            self.assertAlmostEqual(expected["h"], actual.h, places=9)
            for point, wanted in zip(
                actual.landmarks, case.get("expected_normalized_landmarks", [])
            ):
                self.assertAlmostEqual(wanted[0], point[0], places=9)
                self.assertAlmostEqual(wanted[1], point[1], places=9)


class TestScrfdAnchorMultiplicity(unittest.TestCase):
    def test_nine_outputs_duplicate_each_anchor_in_place(self) -> None:
        self.assertEqual(2, num_anchors_for(9))
        self.assertEqual(
            ((0.0, 0.0), (0.0, 0.0), (8.0, 0.0), (8.0, 0.0)),
            anchor_centers(8, 2, 1, num_anchors_for(9)),
        )

    def test_unknown_output_count_is_never_guessed(self) -> None:
        with self.assertRaises(ValueError):
            num_anchors_for(12)


if __name__ == "__main__":
    unittest.main()
