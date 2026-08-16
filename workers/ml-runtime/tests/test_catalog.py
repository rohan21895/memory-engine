from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

try:
    import blake3 as _blake3_dependency  # noqa: F401
    import jsonschema as _jsonschema_dependency  # noqa: F401
except (
    ModuleNotFoundError
) as error:  # The repository CI does not install worker projects yet.
    raise unittest.SkipTest(
        f"install workers/ml-runtime dependencies: {error.name}"
    ) from error

WORKER_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = WORKER_ROOT.parents[1]
sys.path.insert(0, str(WORKER_ROOT))

from blake3 import blake3
from memory_engine_ml_runtime.catalog import ModelCatalog


class CatalogFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.temporary.name)
        shutil.copytree(REPO_ROOT / "models", self.repo_root / "models")
        self.weights_dir = self.repo_root / "models" / "weights"
        self.weights_dir.mkdir()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def add_declared_weights(self) -> None:
        for config_path in (self.repo_root / "models" / "configs").glob("*.json"):
            config = json.loads(config_path.read_text(encoding="utf-8"))
            (self.weights_dir / config["weights"]["filename"]).write_bytes(
                config["model_id"].encode("utf-8")
            )

    def catalog(self, *, development: bool = False) -> ModelCatalog:
        environ = {"MEMORY_ENGINE_ALLOW_UNVERIFIED_MODELS": "1"} if development else {}
        return ModelCatalog(
            repo_root=self.repo_root,
            environ=environ,
            provider_probe=lambda: frozenset({"onnxruntime_cpu"}),
        )


class TestModelCatalog(CatalogFixture):
    def test_current_registry_configs_match_their_raw_byte_pins(self) -> None:
        inspections = self.catalog().inspect_all()
        registry = json.loads(
            (self.repo_root / "models" / "registry.json").read_text(encoding="utf-8")
        )
        expected = {
            entry["model_id"]: entry["config_blake3"] for entry in registry["entries"]
        }
        self.assertEqual(6, len(inspections))
        for inspection in inspections:
            with self.subTest(model=inspection.model_id):
                self.assertEqual(
                    expected[inspection.model_id], inspection.config_blake3
                )
                self.assertEqual(
                    "UNLOADABLE_REASON_WEIGHTS_MISSING", inspection.unloadable_reason
                )

    def test_development_opt_in_allows_unpinned_weight_files(self) -> None:
        self.add_declared_weights()
        catalog = self.catalog(development=True)
        self.assertEqual("development", catalog.mode)
        self.assertTrue(
            all(item.unloadable_reason is None for item in catalog.inspect_all())
        )

    def test_semantically_equal_reformatted_config_fails_its_byte_pin(self) -> None:
        self.add_declared_weights()
        path = self.repo_root / "models" / "configs" / "siglip2-so400m-384.json"
        parsed = json.loads(path.read_text(encoding="utf-8"))
        canonical_digest = blake3(path.read_bytes()).hexdigest()
        path.write_text(json.dumps(parsed, separators=(",", ":")), encoding="utf-8")
        raw_digest = blake3(path.read_bytes()).hexdigest()
        self.assertNotEqual(canonical_digest, raw_digest)

        inspected = self.catalog(development=True).inspect("siglip2-so400m-384")
        self.assertIsNotNone(inspected)
        assert inspected is not None
        self.assertEqual(raw_digest, inspected.config_blake3)
        self.assertEqual(
            "UNLOADABLE_REASON_CONFIG_MISMATCH", inspected.unloadable_reason
        )

    def test_catalog_delegates_every_decision_to_shared_load_gate(self) -> None:
        catalog = self.catalog()
        with mock.patch.object(
            catalog._load_gate,
            "decide_load",
            return_value="UNLOADABLE_REASON_CONFIG_INVALID",
        ) as decide_load:
            inspected = catalog.inspect_all(task="face_embedding")
        self.assertEqual(1, len(inspected))
        decide_load.assert_called_once()
        self.assertEqual(
            "UNLOADABLE_REASON_CONFIG_INVALID", inspected[0].unloadable_reason
        )

    def test_registry_paths_cannot_escape_the_models_or_weights_roots(self) -> None:
        registry_path = self.repo_root / "models" / "registry.json"
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
        registry["entries"][0]["config"] = "../outside.json"
        registry_path.write_text(json.dumps(registry), encoding="utf-8")
        inspected = self.catalog().inspect_all()[0]
        self.assertEqual(
            "UNLOADABLE_REASON_CONFIG_MISSING", inspected.unloadable_reason
        )

    def test_repeated_inference_lookup_does_not_rehash_large_weights(self) -> None:
        self.add_declared_weights()
        catalog = self.catalog(development=True)
        with mock.patch.object(
            catalog, "_hash_file", wraps=catalog._hash_file
        ) as hash_file:
            first = catalog.inspect("siglip2-so400m-384")
            second = catalog.inspect("siglip2-so400m-384")
        self.assertIs(first, second)
        hash_file.assert_called_once()


if __name__ == "__main__":
    unittest.main()
