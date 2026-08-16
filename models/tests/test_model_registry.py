"""The registry must be trustworthy about three things.

1. **Every config is loadable.** ml-runtime binds tensors by name and shape; a
   malformed entry fails at model load in a worker, far from the edit that
   caused it.

2. **Preprocessing is completely specified.** A missing colour order or a wrong
   mean does not crash -- the model returns plausible numbers that are wrong,
   and the symptom shows up months later as "the taste model seems off".

3. **Licence status is honest.** Licensing is deliberately deferred right now
   (issue #3), which is a legitimate decision. These tests make sure it stays a
   *decision*: nothing may claim to be verified without evidence, and anything
   that would block a commercial release has to say so.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

MODELS_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = MODELS_ROOT.parent
CONFIG_DIR = MODELS_ROOT / "configs"
SCHEMA = MODELS_ROOT / "schema" / "model-config.schema.json"
REGISTRY = json.loads((MODELS_ROOT / "registry.json").read_text(encoding="utf-8"))

# Spaces the contract knows about. An embedding written into a space the
# contract has never heard of is unqueryable.
CONTRACT_VECTOR_SPACES = json.loads(
    (REPO_ROOT / "contracts" / "schemas" / "common.schema.json").read_text(encoding="utf-8")
)["$defs"]["VectorSpace"]["enum"]


def configs() -> list[tuple[str, dict]]:
    return [
        (path.name, json.loads(path.read_text(encoding="utf-8")))
        for path in sorted(CONFIG_DIR.glob("*.json"))
    ]


class TestConfigsValidate(unittest.TestCase):
    def test_every_config_matches_the_schema(self):
        from jsonschema import Draft202012Validator

        schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        validator = Draft202012Validator(schema)

        self.assertTrue(configs(), "no model configs found")
        for name, config in configs():
            with self.subTest(config=name):
                errors = sorted(validator.iter_errors(config), key=lambda e: list(e.path))
                self.assertEqual([], [f"{list(e.path)}: {e.message}" for e in errors])

    def test_model_ids_are_unique_and_match_their_filename(self):
        seen: set[str] = set()
        for name, config in configs():
            with self.subTest(config=name):
                self.assertNotIn(config["model_id"], seen)
                seen.add(config["model_id"])
                self.assertEqual(
                    name,
                    f"{config['model_id']}.json",
                    "filename must match model_id so the registry index is trivially checkable",
                )


class TestRegistryIndex(unittest.TestCase):
    def test_every_entry_points_at_a_real_config(self):
        for entry in REGISTRY["entries"]:
            with self.subTest(model=entry["model_id"]):
                path = MODELS_ROOT / entry["config"]
                self.assertTrue(path.is_file(), f"missing config {entry['config']}")
                config = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(entry["model_id"], config["model_id"])
                self.assertEqual(entry["task"], config["task"])

    def test_every_config_is_listed_in_the_registry(self):
        listed = {e["model_id"] for e in REGISTRY["entries"]}
        on_disk = {config["model_id"] for _, config in configs()}
        self.assertEqual(
            on_disk,
            listed,
            "a config that is not in registry.json cannot be loaded, and one that is "
            "listed but missing breaks the loader at startup",
        )

    def test_pipelines_reference_known_models(self):
        known = {e["model_id"] for e in REGISTRY["entries"]}
        # Non-model steps (classical CV) are allowed in a pipeline and have no config.
        non_model = {"classical_quality"}
        for pipeline, spec in REGISTRY["pipelines"].items():
            for step in spec["steps"]:
                with self.subTest(pipeline=pipeline, step=step):
                    self.assertIn(step, known | non_model)


class TestPreprocessingIsFullySpecified(unittest.TestCase):
    """The fields whose absence produces confidently-wrong numbers."""

    def test_image_models_specify_size_colour_order_and_layout(self):
        for name, config in configs():
            pre = config["preprocessing"]
            if pre["kind"] not in {"image", "image_sequence", "face_crop"}:
                continue
            with self.subTest(config=name):
                self.assertIsNotNone(pre["input_size"], "image models need an input size")
                self.assertIn(pre["color_order"], {"rgb", "bgr", "gray"})
                self.assertIn(pre["layout"], {"nchw", "nhwc"})

    def test_mean_and_std_are_consistent(self):
        for name, config in configs():
            pre = config["preprocessing"]
            with self.subTest(config=name):
                if pre["mean"] or pre["std"]:
                    self.assertEqual(
                        len(pre["mean"]),
                        len(pre["std"]),
                        "mean and std must cover the same channels",
                    )
                for value in pre["std"]:
                    self.assertNotEqual(0, value, "a zero std divides by zero")

    def test_face_embedding_models_declare_an_alignment_template(self):
        """Recognition without alignment does not fail loudly -- it returns a
        confidently wrong embedding, which clusters wrongly and puts the wrong
        person in an album. Precision over recall starts here."""
        for name, config in configs():
            if config["task"] != "face_embedding":
                continue
            with self.subTest(config=name):
                alignment = config["preprocessing"]["face_alignment"]
                self.assertIsNotNone(
                    alignment, "a face embedding model must state its alignment template"
                )
                size = alignment["output_size"]
                self.assertEqual(
                    config["preprocessing"]["input_size"],
                    size,
                    "the aligned crop size must equal the model's input size",
                )
                for point in alignment["template"]:
                    x, y = point
                    self.assertGreaterEqual(x, 0)
                    self.assertGreaterEqual(y, 0)
                    self.assertLessEqual(x, size["width"])
                    self.assertLessEqual(y, size["height"])

    def test_embeddings_are_l2_normalized(self):
        """Every VectorSpace in the contract stores normalised vectors, and
        media-db's cosine distance assumes it."""
        for name, config in configs():
            has_embedding = any(o["meaning"] == "embedding" for o in config["outputs"])
            if not has_embedding:
                continue
            with self.subTest(config=name):
                steps = (config.get("postprocessing") or {}).get("steps", [])
                self.assertIn("l2_normalize", steps)

    def test_embedding_dimensions_match_a_contract_vector_space(self):
        for name, config in configs():
            for output in config["outputs"]:
                if output["meaning"] != "embedding":
                    continue
                with self.subTest(config=name, output=output["name"]):
                    space = output["vector_space"]
                    self.assertIn(
                        space,
                        CONTRACT_VECTOR_SPACES,
                        "an embedding must be written into a space the contract knows",
                    )
                    self.assertIsNotNone(output["dimensions"])
                    # Spaces encode their width in the name by convention.
                    self.assertTrue(
                        space.endswith(str(output["dimensions"])),
                        f"space {space!r} disagrees with {output['dimensions']} dimensions",
                    )

    def test_detectors_declare_thresholds_and_strides(self):
        for name, config in configs():
            if config["task"] != "face_detection":
                continue
            with self.subTest(config=name):
                post = config["postprocessing"]
                self.assertIsNotNone(post["score_threshold"])
                self.assertIsNotNone(post["nms_threshold"])
                self.assertTrue(post["strides"], "anchor strides are needed to decode boxes")

    def test_score_producing_models_declare_normalisation(self):
        """Fusion must never need to know a model's native range."""
        for name, config in configs():
            produces_score = any(o["meaning"] in {"score", "scores"} for o in config["outputs"])
            if not produces_score or config["task"] == "face_detection":
                continue
            with self.subTest(config=name):
                post = config.get("postprocessing") or {}
                self.assertIsNotNone(
                    post.get("normalisation"),
                    "a model whose output becomes a contract Score must say how its "
                    "raw range maps to [0,1]",
                )


class TestLicenceHonesty(unittest.TestCase):
    def test_every_config_states_a_licence(self):
        for name, config in configs():
            with self.subTest(config=name):
                licence = config["license"]
                self.assertTrue(licence["code_license"])
                self.assertTrue(licence["weights_license"])
                self.assertIn(
                    licence["commercial_use"],
                    {"permitted", "prohibited", "unresolved", "requires_license_purchase"},
                )

    def test_nothing_claims_verification_without_evidence(self):
        """`verified` means a human read the licence at the source on a date.
        It is never set by inference, and never by me."""
        for name, config in configs():
            licence = config["license"]
            with self.subTest(config=name):
                if licence["verified"]:
                    self.assertIsNotNone(licence["verified_at"])
                    self.assertIsNotNone(licence["license_url"])
                else:
                    self.assertIsNone(
                        licence["verified_at"],
                        "an unverified entry must not carry a verification date",
                    )

    def test_restricted_models_are_flagged_as_blocking_release(self):
        """Licensing is deferred by decision, not by oversight. Anything whose
        commercial use is not permitted must say that shipping it commercially
        would be a violation -- so the deferral remains visible and reversible."""
        for name, config in configs():
            licence = config["license"]
            with self.subTest(config=name):
                if licence["commercial_use"] in {"prohibited", "requires_license_purchase"}:
                    self.assertTrue(
                        licence["blocks_commercial_release"],
                        f"{config['model_id']} is not licensed for commercial use but "
                        "does not flag itself as blocking a release",
                    )

    def test_blocked_models_explain_themselves(self):
        for name, config in configs():
            licence = config["license"]
            if licence["blocks_commercial_release"]:
                with self.subTest(config=name):
                    self.assertTrue(
                        licence.get("note"),
                        "a blocked model must say why and what the replacement path is",
                    )

    def test_unpinned_weights_cannot_be_promoted(self):
        """A record produced by an unpinned model is not reproducible, and
        reproducibility is the product."""
        for name, config in configs():
            with self.subTest(config=name):
                if config["weights"]["blake3"] is None:
                    self.assertEqual(
                        "candidate",
                        config["rollout"]["state"],
                        "weights must be downloaded and hashed before a model can be "
                        "promoted past candidate",
                    )

    def test_the_blocked_set_matches_the_documented_audit(self):
        """docs/model-registry.md and the configs must not drift apart."""
        blocked = {
            config["model_id"]
            for _, config in configs()
            if config["license"]["blocks_commercial_release"]
        }
        audit = (REPO_ROOT / "docs" / "model-registry.md").read_text(encoding="utf-8")
        for model_id in blocked:
            with self.subTest(model=model_id):
                family = model_id.split("-")[0]
                self.assertIn(
                    family.lower(),
                    audit.lower(),
                    f"{model_id} blocks release but is not discussed in the audit doc",
                )


if __name__ == "__main__":
    unittest.main()
