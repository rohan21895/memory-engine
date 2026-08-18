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
import unittest.mock
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


class TheStoredPrecisionMatchesTheDeclaration(unittest.TestCase):
    """`weights.quantization` against the dtype of the graph's initializers.

    Separate from the class above because it needs a different dependency:
    onnxruntime answers questions about inputs and outputs, which are float32
    here by design, and cannot see what dtype the WEIGHTS are stored in. A
    tower silently exported in fp32 is identical through the session API, twice
    the size on disk, and agrees with a config declaring fp16 unless something
    reads the initializers.
    """

    def setUp(self) -> None:
        self.exporter = _load_exporter()
        self.declared = self.exporter.Declared.read()
        self.artifact = MODELS_ROOT / "weights" / self.declared.filename
        if not self.artifact.is_file():
            self.skipTest("the SigLIP 2 vision tower has not been exported here")
        try:
            import onnx  # noqa: F401
        except ImportError:  # pragma: no cover - environment dependent
            self.skipTest("the onnx package is needed to read initializer dtypes")

    def test_the_artifact_stores_the_precision_the_config_declares(self):
        ran = self.exporter.check_precision(
            self.artifact, self.declared, log=lambda _message: None, required=True
        )
        self.assertTrue(ran, "the check reported that it did not run")

    def test_a_declaration_the_artifact_contradicts_is_caught(self):
        """Tested by breaking it: fp16 weights against a config claiming fp32.

        A check that passes on a correct artifact proves nothing about whether
        it would fail on a wrong one -- which is how the interpolation field sat
        wrong in this same config for weeks.
        """
        wrong = self.exporter.Declared(
            filename=self.declared.filename,
            input_name=self.declared.input_name,
            width=self.declared.width,
            height=self.declared.height,
            output_names=self.declared.output_names,
            dimensions=self.declared.dimensions,
            quantization="fp32",
        )
        with self.assertRaises(self.exporter.CheckFailed) as caught:
            self.exporter.check_precision(
                self.artifact, wrong, log=lambda _message: None, required=True
            )
        self.assertIn("FLOAT16", str(caught.exception))


class ThePrecisionCheckSeparatesWeightsFromPlumbing(unittest.TestCase):
    """Built graphs, not the artifact: 857MB cannot express these two cases.

    The real export folds all of its shape constants into `Constant` nodes and
    carries no integer initializers, so "every initializer is float16" and
    "every float initializer is float16" are indistinguishable on it. They are
    not the same rule, and the difference decides whether the next legitimate
    re-export passes.
    """

    def setUp(self) -> None:
        self.exporter = _load_exporter()
        try:
            import onnx  # noqa: F401
        except ImportError:  # pragma: no cover - environment dependent
            self.skipTest("the onnx package is needed to build a graph to check")
        self.declared = self.exporter.Declared.read()

    def _graph(self, weight_dtype: int, *, with_int64: bool) -> Path:
        import onnx
        from onnx import helper, numpy_helper
        import numpy as np
        import tempfile

        weights = numpy_helper.from_array(
            np.zeros((2, 2), dtype=onnx.helper.tensor_dtype_to_np_dtype(weight_dtype)),
            name="weights",
        )
        initializers = [weights]
        if with_int64:
            initializers.append(
                numpy_helper.from_array(np.array([2, 2], dtype=np.int64), name="shape")
            )
        graph = helper.make_graph(
            [helper.make_node("Identity", ["weights"], ["out"])],
            "tiny",
            [],
            [helper.make_tensor_value_info("out", weight_dtype, [2, 2])],
            initializer=initializers,
        )
        handle = tempfile.NamedTemporaryFile(suffix=".onnx", delete=False)
        handle.close()
        path = Path(handle.name)
        self.addCleanup(path.unlink, missing_ok=True)
        onnx.save(helper.make_model(graph, opset_imports=[helper.make_opsetid("", 17)]),
                  str(path))
        return path

    def _declared(self, quantization: str):
        return self.exporter.Declared(
            filename=self.declared.filename,
            input_name=self.declared.input_name,
            width=self.declared.width,
            height=self.declared.height,
            output_names=self.declared.output_names,
            dimensions=self.declared.dimensions,
            quantization=quantization,
        )

    def test_int64_plumbing_alongside_fp16_weights_passes(self):
        """Shapes and indices are int64 in every ONNX graph that has any."""
        from onnx import TensorProto

        messages: list[str] = []
        ran = self.exporter.check_precision(
            self._graph(TensorProto.FLOAT16, with_int64=True),
            self._declared("fp16"),
            log=messages.append,
            required=True,
        )
        self.assertTrue(ran)
        self.assertTrue(
            any("INT64" in message for message in messages),
            f"the non-float initializers must be reported, not hidden: {messages}",
        )

    def test_fp32_weights_against_an_fp16_declaration_still_fail(self):
        """The relaxation must not have relaxed the check it exists for."""
        from onnx import TensorProto

        with self.assertRaises(self.exporter.CheckFailed):
            self.exporter.check_precision(
                self._graph(TensorProto.FLOAT, with_int64=True),
                self._declared("fp16"),
                log=lambda _message: None,
                required=True,
            )


class ThePrecisionCheckIsHonestAboutNotRunning(unittest.TestCase):
    """No artifact and no onnx needed: these are about the check's own contract.

    They run on CI, where neither exists.
    """

    def setUp(self) -> None:
        self.exporter = _load_exporter()
        self.declared = self.exporter.Declared.read()

    def test_without_onnx_it_reports_not_run_rather_than_a_pass(self):
        absent = MODELS_ROOT / "weights" / "there-is-no-such-artifact.onnx"
        messages: list[str] = []
        with unittest.mock.patch.dict(sys.modules, {"onnx": None}):
            ran = self.exporter.check_precision(
                absent, self.declared, log=messages.append, required=False
            )
        self.assertFalse(ran)
        self.assertTrue(
            any("NOT CHECKED" in message for message in messages),
            f"a skipped check must say so; it logged {messages}",
        )

    def test_on_export_a_missing_onnx_is_a_precondition_not_a_skip(self):
        """Exporting without being able to check the result is a broken
        environment: `torch.onnx.export` cannot run without `onnx` either."""
        absent = MODELS_ROOT / "weights" / "there-is-no-such-artifact.onnx"
        with unittest.mock.patch.dict(sys.modules, {"onnx": None}):
            with self.assertRaises(self.exporter.Precondition):
                self.exporter.check_precision(
                    absent, self.declared, log=lambda _message: None, required=True
                )

    def test_an_unknown_quantization_is_refused_rather_than_skipped(self):
        """A precision this script cannot check must not read as checked."""
        unknown = self.exporter.Declared(
            filename=self.declared.filename,
            input_name=self.declared.input_name,
            width=self.declared.width,
            height=self.declared.height,
            output_names=self.declared.output_names,
            dimensions=self.declared.dimensions,
            quantization="int8",
        )
        with self.assertRaises(self.exporter.Precondition):
            self.exporter.check_precision(
                MODELS_ROOT / "weights" / "irrelevant.onnx",
                unknown,
                log=lambda _message: None,
                required=False,
            )


if __name__ == "__main__":
    unittest.main()
