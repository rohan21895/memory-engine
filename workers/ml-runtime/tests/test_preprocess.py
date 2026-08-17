from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

import numpy as np

WORKER_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = WORKER_ROOT.parents[1]
sys.path.insert(0, str(WORKER_ROOT))

from memory_engine_ml_runtime.preprocess import _normalise_image


class TestYuNetPreprocessingFixture(unittest.TestCase):
    def test_channel_order_layout_and_arithmetic_match(self) -> None:
        data = json.loads(
            (
                REPO_ROOT / "models" / "fixtures" / "preprocess-yunet-2023mar.json"
            ).read_text(encoding="utf-8")
        )
        source = data["input"]
        image_rgb = np.empty((source["height"], source["width"], 3), dtype=np.uint8)
        seed = source["seed"]
        for y in range(source["height"]):
            for x in range(source["width"]):
                image_rgb[y, x] = (
                    (x * 7 + y * 3 + seed) % 256,
                    (x * 3 + y * 11 + seed * 2) % 256,
                    (x * 13 + y * 5 + seed * 3) % 256,
                )
        image_bgr = image_rgb[:, :, ::-1].copy()
        applied = data["applied"]
        preprocessing = {
            "input_size": {"width": source["width"], "height": source["height"]},
            "resize": "none",
            "interpolation": "bilinear",
            "color_order": applied["color_order"],
            "layout": applied["layout"],
            "scale": applied["scale"],
            "mean": applied["mean"],
            "std": applied["std"],
        }
        tensor = _normalise_image(image_bgr, preprocessing)
        flat = tensor.reshape(-1)
        expected = data["expected"]
        self.assertEqual(tuple(expected["shape"]), tensor.shape)
        self.assertEqual(expected["count"], flat.size)
        self.assertAlmostEqual(expected["mean"], float(flat.mean()), places=9)
        self.assertEqual(expected["min"], float(flat.min()))
        self.assertEqual(expected["max"], float(flat.max()))
        np.testing.assert_allclose(expected["first_values"], flat[:6], atol=0, rtol=0)
        for index, value in expected["sampled"].items():
            self.assertEqual(value, float(flat[int(index)]))


if __name__ == "__main__":
    unittest.main()
