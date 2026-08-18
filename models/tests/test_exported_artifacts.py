"""The artifacts this repository BUILDS, checked against the configs that bind them.

Issue #79. `scripts/models/export_siglip2_vision_onnx.py` produces the SigLIP 2
vision tower because no publisher ships it. Everything about that artifact --
its input name, its output name, its dimensionality -- is a claim the config
makes, and the only thing that can settle a claim about a graph is the graph.

These tests SKIP where the artifact is absent, which is every CI runner and
every fresh clone: 857MB is not committed. That makes them worth little on CI
and a great deal on a machine that has run the export, which is exactly the
machine where a wrong export would otherwise go unnoticed. The counterpart that
does run everywhere is `TestConversionRecipesAreComplete` in
test_model_registry.py -- it checks the recipe, not the output.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from pathlib import Path

MODELS_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = MODELS_ROOT.parent
EXPORTER = REPO_ROOT / "scripts" / "models" / "export_siglip2_vision_onnx.py"


def _load_exporter():
    """Import the script by path; `scripts/` is not a package."""
    spec = importlib.util.spec_from_file_location(
        "export_siglip2_vision_onnx", EXPORTER
    )
    module = importlib.util.module_from_spec(spec)
    # Registered before execution because @dataclass resolves annotations
    # through sys.modules and fails on a module that is not in it yet.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class TheExporterAgreesWithTheConfig(unittest.TestCase):
    """These run everywhere: they read the config, not the artifact."""

    def setUp(self) -> None:
        self.assertTrue(EXPORTER.is_file(), f"{EXPORTER} is missing")
        self.exporter = _load_exporter()

    def test_it_reads_its_bindings_from_the_config(self):
        """Not from constants of its own.

        A script carrying its own copy of the output name would agree with
        itself while disagreeing with the config, which is the shape of the
        SCRFD (#36) and ArcFace (#69) defects.
        """
        declared = self.exporter.Declared.read()
        config = json.loads(
            (MODELS_ROOT / "configs" / "siglip2-so400m-384.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(config["weights"]["filename"], declared.filename)
        self.assertEqual(config["preprocessing"]["input_name"], declared.input_name)
        self.assertEqual((config["outputs"][0]["name"],), declared.output_names)
        self.assertEqual(config["outputs"][0]["dimensions"], declared.dimensions)
        self.assertEqual(config["weights"]["quantization"], declared.quantization)

    def test_the_config_points_back_at_this_script(self):
        conversion = json.loads(
            (MODELS_ROOT / "configs" / "siglip2-so400m-384.json").read_text(
                encoding="utf-8"
            )
        )["weights"]["conversion"]
        self.assertEqual(
            EXPORTER.resolve(), (REPO_ROOT / conversion["script"]).resolve()
        )

    def test_the_verification_image_is_a_file_in_this_repository(self):
        """The check runs a real photograph, and it must not need the network.

        It must also never reach for the user's own pictures: the demo scripts
        refuse `~/Pictures` and a DCIM folder for the same reason.
        """
        self.assertTrue(
            self.exporter.DEFAULT_IMAGE.is_file(),
            f"{self.exporter.DEFAULT_IMAGE} is the verification image and is missing",
        )
        self.assertIn(REPO_ROOT, self.exporter.DEFAULT_IMAGE.parents)


class TheExportedGraphMatchesTheConfig(unittest.TestCase):
    """Not a fixture: the actual artifact, when an export has been run."""

    def setUp(self) -> None:
        self.exporter = _load_exporter()
        self.declared = self.exporter.Declared.read()
        self.artifact = MODELS_ROOT / "weights" / self.declared.filename
        if not self.artifact.is_file():
            self.skipTest("the SigLIP 2 vision tower has not been exported here")
        try:
            import onnxruntime  # noqa: F401
            from PIL import Image  # noqa: F401
        except ImportError:  # pragma: no cover - environment dependent
            self.skipTest("onnxruntime and Pillow are needed to run the graph")

    def test_the_graph_satisfies_every_binding_the_config_declares(self):
        """The whole verification, including a real image and a real batch.

        `verify` raises `CheckFailed` on any disagreement, so this asserts by
        not raising -- the alternative is restating its checks here and letting
        the two copies drift.
        """
        config = json.loads(
            (MODELS_ROOT / "configs" / "siglip2-so400m-384.json").read_text(
                encoding="utf-8"
            )
        )
        self.exporter.verify(
            self.artifact,
            self.declared,
            config,
            self.exporter.DEFAULT_IMAGE,
            log=lambda _message: None,
        )

    def test_a_config_bound_to_the_wrong_output_name_is_caught(self):
        """The check that #36 and #69 needed, tested by breaking it.

        A verification that passes on a correct artifact proves nothing about
        whether it would fail on a wrong one.
        """
        config = json.loads(
            (MODELS_ROOT / "configs" / "siglip2-so400m-384.json").read_text(
                encoding="utf-8"
            )
        )
        wrong = self.exporter.Declared(
            filename=self.declared.filename,
            input_name=self.declared.input_name,
            width=self.declared.width,
            height=self.declared.height,
            output_names=("pooler_output",),
            dimensions=self.declared.dimensions,
            quantization=self.declared.quantization,
        )
        with self.assertRaises(self.exporter.CheckFailed) as caught:
            self.exporter.verify(
                self.artifact, wrong, config, self.exporter.DEFAULT_IMAGE,
                log=lambda _message: None,
            )
        self.assertIn("pooler_output", str(caught.exception))

    def test_a_wrong_declared_dimensionality_is_caught(self):
        config = json.loads(
            (MODELS_ROOT / "configs" / "siglip2-so400m-384.json").read_text(
                encoding="utf-8"
            )
        )
        wrong = self.exporter.Declared(
            filename=self.declared.filename,
            input_name=self.declared.input_name,
            width=self.declared.width,
            height=self.declared.height,
            output_names=self.declared.output_names,
            dimensions=768,
            quantization=self.declared.quantization,
        )
        with self.assertRaises(self.exporter.CheckFailed):
            self.exporter.verify(
                self.artifact, wrong, config, self.exporter.DEFAULT_IMAGE,
                log=lambda _message: None,
            )


if __name__ == "__main__":
    unittest.main()
