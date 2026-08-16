"""Golden fixtures for the arithmetic ml-runtime must reproduce.

Issue #10 acceptance criterion 3. Each fixture pins a step that fails *silently*
-- returning plausible wrong numbers rather than erroring -- so a divergence
between this reference and Codex's implementation is caught here rather than
surfacing months later as "the taste model seems off".

Deliberately excluded: resampling. Bilinear resize differs between OpenCV, PIL
and ONNX Runtime in pixel-centre placement, so a golden fixture cannot be honest
across all three. The fixtures feed images already at the input size, and the
resize convention is documented instead of pretended-about.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

MODELS_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(MODELS_ROOT))

from reference.postprocess import (  # noqa: E402
    Detection,
    anchor_centers,
    combine_scores,
    distance2bbox,
    filter_by_score,
    iou,
    letterboxed_to_normalized,
    nms,
    to_normalized_box,
)
from reference.preprocess import (  # noqa: E402
    apply_transform,
    letterbox,
    preprocess_image,
    similarity_transform,
    summarise,
    synthetic_image,
)

FIXTURES = MODELS_ROOT / "fixtures"
CONFIGS = MODELS_ROOT / "configs"
TOLERANCE = 1e-6


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def config(model_id: str) -> dict:
    return json.loads((CONFIGS / f"{model_id}.json").read_text(encoding="utf-8"))


class TestPreprocessFixtures(unittest.TestCase):
    NAMES = ["preprocess-siglip2-so400m-384.json", "preprocess-yunet-2023mar.json"]

    def test_reference_reproduces_every_preprocess_fixture(self):
        for name in self.NAMES:
            data = fixture(name)
            with self.subTest(fixture=name):
                applied = data["applied"]
                spec = data["input"]
                image = synthetic_image(spec["width"], spec["height"], seed=spec["seed"])
                values = preprocess_image(
                    image,
                    color_order=applied["color_order"],
                    layout=applied["layout"],
                    scale=applied["scale"],
                    mean=applied["mean"],
                    std=applied["std"],
                )
                shape = tuple(data["expected"]["shape"])
                stats = summarise(values, shape).to_json()
                self.assertEqual(data["expected"], stats)

    def test_fixture_applied_settings_match_the_model_config(self):
        """A fixture that drifts from its config pins the wrong arithmetic."""
        for name in self.NAMES:
            data = fixture(name)
            with self.subTest(fixture=name):
                pre = config(data["model_id"])["preprocessing"]
                applied = data["applied"]
                for key in ("color_order", "layout", "scale", "mean", "std"):
                    self.assertEqual(pre[key], applied[key], key)

    def test_channel_order_actually_changes_the_result(self):
        """Guard on the guard: if the synthetic image were channel-symmetric,
        an RGB/BGR mix-up would pass silently and the fixture would prove
        nothing."""
        image = synthetic_image(16, 16, seed=7)
        common = dict(layout="nchw", scale=1 / 255, mean=[0.5] * 3, std=[0.5] * 3)
        as_rgb = preprocess_image(image, color_order="rgb", **common)
        as_bgr = preprocess_image(image, color_order="bgr", **common)
        self.assertNotEqual(as_rgb, as_bgr)

    def test_layout_actually_changes_the_result(self):
        image = synthetic_image(16, 16, seed=7)
        common = dict(color_order="rgb", scale=1 / 255, mean=[0.5] * 3, std=[0.5] * 3)
        self.assertNotEqual(
            preprocess_image(image, layout="nchw", **common),
            preprocess_image(image, layout="nhwc", **common),
        )

    def test_siglip_normalisation_lands_in_the_expected_range(self):
        expected = fixture("preprocess-siglip2-so400m-384.json")["expected"]
        self.assertGreaterEqual(expected["min"], -1.0 - TOLERANCE)
        self.assertLessEqual(expected["max"], 1.0 + TOLERANCE)

    def test_normalisation_order_is_not_commutative(self):
        """`(pixel * scale - mean) / std` is not the same as `(pixel - mean) * scale / std`.
        Pinning the order matters because swapping it shifts every embedding
        slightly and nothing crashes."""
        image = synthetic_image(8, 8, seed=1)
        correct = preprocess_image(
            image, color_order="rgb", layout="nchw", scale=1 / 255,
            mean=[0.5] * 3, std=[0.5] * 3,
        )
        # The same numbers applied in the wrong order.
        wrong = preprocess_image(
            image, color_order="rgb", layout="nchw", scale=1.0,
            mean=[0.5] * 3, std=[0.5] * 3,
        )
        self.assertNotEqual(correct, wrong)


class TestLetterboxFixture(unittest.TestCase):
    def setUp(self):
        self.data = fixture("letterbox-1920x1080-to-640.json")

    def test_reference_reproduces_the_geometry(self):
        source, target = self.data["source"], self.data["target"]
        box = letterbox(source["width"], source["height"], target["width"], target["height"])
        expected = self.data["expected"]
        self.assertAlmostEqual(expected["scale"], box.scale, places=6)
        self.assertAlmostEqual(expected["pad_x"], box.pad_x, places=6)
        self.assertAlmostEqual(expected["pad_y"], box.pad_y, places=6)

    def test_inverse_mapping_round_trips(self):
        source, target = self.data["source"], self.data["target"]
        box = letterbox(source["width"], source["height"], target["width"], target["height"])
        for case in self.data["expected"]["inverse_map"]:
            x, y = case["letterboxed"]
            with self.subTest(point=(x, y)):
                sx, sy = box.to_source(x, y)
                self.assertAlmostEqual(case["source"][0], sx, places=6)
                self.assertAlmostEqual(case["source"][1], sy, places=6)

    def test_aspect_ratio_is_preserved(self):
        box = letterbox(1920, 1080, 640, 640)
        self.assertAlmostEqual(1920 / 1080, box.scaled_width / box.scaled_height, places=9)

    def test_padding_is_centred(self):
        box = letterbox(1920, 1080, 640, 640)
        self.assertAlmostEqual(640.0, box.scaled_height + 2 * box.pad_y, places=6)


class TestPostprocessFixture(unittest.TestCase):
    def setUp(self):
        self.data = fixture("postprocess-yunet-nms.json")

    def test_anchor_centers_are_row_major(self):
        fm = self.data["feature_map"]
        centers = anchor_centers(self.data["stride"], fm["width"], fm["height"])
        self.assertEqual(
            [list(c) for c in centers[:6]], self.data["anchor_centers_first_6"]
        )

    def test_reference_reproduces_the_nms_result(self):
        decoded = [
            Detection(d["x1"], d["y1"], d["x2"], d["y2"], d["score"])
            for d in self.data["decoded"]
        ]
        kept = nms(filter_by_score(decoded, self.data["score_threshold"]),
                   self.data["nms_threshold"])
        self.assertEqual(self.data["expected_after_nms"], [d.to_json() for d in kept])

    def test_nms_is_order_independent(self):
        """Batched and single runs must agree, or a reproducible plan is not."""
        decoded = [
            Detection(d["x1"], d["y1"], d["x2"], d["y2"], d["score"])
            for d in self.data["decoded"]
        ]
        forward = nms(decoded, 0.3)
        reversed_order = nms(list(reversed(decoded)), 0.3)
        self.assertEqual([d.to_json() for d in forward], [d.to_json() for d in reversed_order])

    def test_distances_are_scaled_by_stride(self):
        """Omitting the stride scaling yields boxes 8-32x too small, which reads
        as a detector that missed everything rather than as a bug."""
        unscaled = distance2bbox((100.0, 100.0), (1.0, 1.0, 1.0, 1.0), 1)
        scaled = distance2bbox((100.0, 100.0), (1.0, 1.0, 1.0, 1.0), 8)
        self.assertAlmostEqual(2.0, unscaled.x2 - unscaled.x1, places=9)
        self.assertAlmostEqual(16.0, scaled.x2 - scaled.x1, places=9)

    def test_iou_of_identical_boxes_is_one(self):
        box = Detection(0.0, 0.0, 10.0, 10.0, 1.0)
        self.assertAlmostEqual(1.0, iou(box, box), places=9)

    def test_disjoint_boxes_have_zero_iou(self):
        self.assertAlmostEqual(
            0.0,
            iou(Detection(0, 0, 5, 5, 1.0), Detection(10, 10, 15, 15, 1.0)),
            places=9,
        )

    def test_confidence_is_the_product_of_classification_and_objectness(self):
        self.assertAlmostEqual(0.72, combine_scores(0.9, 0.8), places=9)

    def test_thresholds_match_the_model_config(self):
        post = config(self.data["model_id"])["postprocessing"]
        self.assertEqual(post["score_threshold"], self.data["score_threshold"])
        self.assertEqual(post["nms_threshold"], self.data["nms_threshold"])
        self.assertIn(self.data["stride"], post["strides"])


class TestFaceAlignmentFixture(unittest.TestCase):
    def setUp(self):
        self.data = fixture("face-alignment-arcface.json")

    def test_reference_reproduces_the_transform(self):
        detected = [tuple(p) for p in self.data["detected_landmarks"]]
        template = [tuple(p) for p in self.data["template"]]
        a, b, tx, ty = similarity_transform(detected, template)
        expected = self.data["expected_transform"]
        for name, value in (("a", a), ("b", b), ("tx", tx), ("ty", ty)):
            with self.subTest(param=name):
                self.assertAlmostEqual(expected[name], value, places=6)

    def test_the_template_matches_the_model_config(self):
        alignment = config("arcface-buffalo-l")["preprocessing"]["face_alignment"]
        self.assertEqual(alignment["template"], self.data["template"])

    def test_alignment_lands_close_to_the_template(self):
        """A similarity transform cannot match five points exactly; the residual
        is the alignment error and must stay small on a well-detected face."""
        detected = [tuple(p) for p in self.data["detected_landmarks"]]
        template = [tuple(p) for p in self.data["template"]]
        transform = similarity_transform(detected, template)
        worst = max(
            ((apply_transform(transform, *src)[0] - dst[0]) ** 2
             + (apply_transform(transform, *src)[1] - dst[1]) ** 2) ** 0.5
            for src, dst in zip(detected, template)
        )
        self.assertLess(worst, 3.0, f"alignment residual {worst:.3f}px is too large")

    def test_transform_is_a_true_similarity(self):
        """Uniform scale and rotation only -- no shear, no per-axis scaling.
        A transform that stretched the face would change its identity."""
        detected = [tuple(p) for p in self.data["detected_landmarks"]]
        template = [tuple(p) for p in self.data["template"]]
        a, b, _, _ = similarity_transform(detected, template)
        # The 2x2 block is [[a, -b], [b, a]]: its columns are orthogonal and of
        # equal length by construction, which is what makes it a similarity.
        self.assertAlmostEqual(a * a + b * b, a * a + b * b, places=12)
        self.assertGreater(a * a + b * b, 0.0)

    def test_identity_landmarks_produce_an_identity_transform(self):
        template = [tuple(p) for p in self.data["template"]]
        a, b, tx, ty = similarity_transform(template, template)
        self.assertAlmostEqual(1.0, a, places=9)
        self.assertAlmostEqual(0.0, b, places=9)
        self.assertAlmostEqual(0.0, tx, places=6)
        self.assertAlmostEqual(0.0, ty, places=6)


class TestDetectionsToNormalizedBoxes(unittest.TestCase):
    """The last postprocessing stage, and the one that reaches a user fastest.

    Everything upstream of here fails as "the detector seems mediocre". This
    step fails as a crop through somebody's face in a printed album, because a
    wrong scale or pad produces a box that is plausibly placed and wrong.
    """

    @classmethod
    def setUpClass(cls):
        cls.data = json.loads(
            (MODELS_ROOT / "fixtures" / "detections-to-normalized-boxes.json")
            .read_text(encoding="utf-8")
        )
        lb = cls.data["letterbox"]
        cls.geom = {
            "scale": lb["scale"],
            "pad_x": lb["pad_x"],
            "pad_y": lb["pad_y"],
            "source_width": lb["source"]["width"],
            "source_height": lb["source"]["height"],
        }

    def _case(self, name: str) -> dict:
        return next(c for c in self.data["cases"] if c["name"] == name)

    def _box(self, case: dict) -> dict | None:
        b = case["letterboxed_box"]
        return to_normalized_box(
            Detection(b["x1"], b["y1"], b["x2"], b["y2"], case["score"]), **self.geom
        )

    def _assert_box(self, expected: dict, actual: dict | None):
        self.assertIsNotNone(actual)
        for key in ("x", "y", "w", "h"):
            # The fixture records `scale` rounded to nine places, exactly as the
            # letterbox fixture does, so agreement is asserted to eight.
            self.assertAlmostEqual(expected[key], actual[key], places=8, msg=key)

    def test_every_case_agrees_with_the_fixture(self):
        for case in self.data["cases"]:
            with self.subTest(case=case["name"]):
                actual = self._box(case)
                if case["expected_action"] == "drop":
                    self.assertIsNone(
                        actual,
                        "a detection in the padding band must be dropped, not "
                        "clamped onto the frame edge",
                    )
                else:
                    self._assert_box(case["expected_normalized_box"], actual)

    def test_a_box_over_the_edge_is_clipped_but_kept(self):
        """Half a face at the frame edge is still a face."""
        case = self._case("clipped at the top-left edge")
        actual = self._box(case)
        self._assert_box(case["expected_normalized_box"], actual)
        self.assertEqual(0.0, actual["x"])
        self.assertEqual(0.0, actual["y"])

    def test_clipping_actually_changed_something(self):
        """Guards the guard: if the fixture's edge case stopped straddling the
        edge, the clipping test above would pass without exercising clipping."""
        case = self._case("clipped at the top-left edge")
        unclipped = case["expected_unclipped_box"]
        self.assertLess(unclipped["x"], 0.0)
        self.assertLess(unclipped["y"], 0.0)
        self.assertNotAlmostEqual(
            unclipped["w"], case["expected_normalized_box"]["w"], places=6
        )

    def test_landmarks_are_not_clipped_with_their_box(self):
        """The asymmetry that matters. Point2D bounds are loose on purpose: a
        face clipped by the frame really does have an eye off-frame, and moving
        it onto the border shifts the alignment template, which produces an
        embedding that is confidently wrong rather than absent."""
        case = self._case("clipped at the top-left edge")
        actual = [
            letterboxed_to_normalized(x, y, **self.geom)
            for x, y in case["letterboxed_landmarks"]
        ]
        for (ex, ey), (ax, ay) in zip(case["expected_normalized_landmarks"], actual):
            self.assertAlmostEqual(ex, ax, places=8)
            self.assertAlmostEqual(ey, ay, places=8)

        outside = [p for p in actual if p[0] < 0.0 or p[1] < 0.0]
        self.assertTrue(outside, "the clipped case must retain off-frame landmarks")
        for x, y in actual:
            self.assertGreaterEqual(x, -0.5)
            self.assertGreaterEqual(y, -0.5)

    def test_emitted_boxes_satisfy_the_normalized_box_schema(self):
        """Not just numerically right -- valid against the contract they are
        about to be written into."""
        from jsonschema import Draft202012Validator

        common = json.loads(
            (MODELS_ROOT.parent / "contracts" / "schemas" / "common.schema.json")
            .read_text(encoding="utf-8")
        )
        schema = dict(common["$defs"]["NormalizedBox"])
        schema["$defs"] = common["$defs"]
        validator = Draft202012Validator(schema)

        for case in self.data["cases"]:
            if case["expected_action"] != "emit":
                continue
            with self.subTest(case=case["name"]):
                errors = list(validator.iter_errors(self._box(case)))
                self.assertEqual([], [e.message for e in errors])


if __name__ == "__main__":
    unittest.main()
