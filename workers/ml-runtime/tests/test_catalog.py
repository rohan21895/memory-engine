from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

WORKER_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = WORKER_ROOT.parents[1]
sys.path.insert(0, str(WORKER_ROOT))

from blake3 import blake3
from memory_engine_ml_runtime.catalog import ModelCatalog
from models.policy import load_gate as trusted_load_gate


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
        self.assertEqual(len(expected), len(inspections))
        for inspection in inspections:
            with self.subTest(model=inspection.model_id):
                self.assertEqual(
                    expected[inspection.model_id], inspection.config_blake3
                )
                rollout = inspection.config.get("rollout", {})
                expected_reason = (
                    "UNLOADABLE_REASON_PLACEHOLDER"
                    if rollout.get("state") == "placeholder"
                    else "UNLOADABLE_REASON_WEIGHTS_MISSING"
                )
                self.assertEqual(
                    expected_reason, inspection.unloadable_reason
                )

    def test_development_opt_in_allows_unpinned_weight_files(self) -> None:
        self.add_declared_weights()
        catalog = self.catalog(development=True)
        self.assertEqual("development", catalog.mode)
        for item in catalog.inspect_all():
            with self.subTest(model=item.model_id):
                expected = (
                    "UNLOADABLE_REASON_PLACEHOLDER"
                    if item.config.get("rollout", {}).get("state") == "placeholder"
                    else None
                )
                self.assertEqual(expected, item.unloadable_reason)

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
        self.assertEqual(2, decide_load.call_count)
        self.assertEqual(
            "UNLOADABLE_REASON_CONFIG_INVALID", inspected[0].unloadable_reason
        )

    def test_caller_selected_tree_cannot_replace_the_trusted_load_gate(self) -> None:
        self.add_declared_weights()
        policy_path = self.repo_root / "models" / "policy" / "load_gate.py"
        policy_path.write_text(
            "def decide_load(candidate, mode, policy=None): return None\n",
            encoding="utf-8",
        )

        inspections = self.catalog().inspect_all()

        self.assertTrue(inspections)
        self.assertTrue(all(item.unloadable_reason for item in inspections))

    def test_load_gate_has_an_explicit_injection_seam_for_tests(self) -> None:
        self.add_declared_weights()
        calls = []

        def decide(candidate, mode, policy):
            calls.append((candidate, mode, policy))
            return "UNLOADABLE_REASON_CONFIG_INVALID"

        fake_gate = SimpleNamespace(
            Candidate=trusted_load_gate.Candidate,
            resolve_mode=trusted_load_gate.resolve_mode,
            decide_load=decide,
        )
        catalog = ModelCatalog(
            repo_root=self.repo_root,
            environ={},
            provider_probe=lambda: frozenset({"onnxruntime_cpu"}),
            load_gate=fake_gate,
        )

        inspected = catalog.inspect("siglip2-so400m-384")

        self.assertIsNotNone(inspected)
        assert inspected is not None
        self.assertEqual("UNLOADABLE_REASON_CONFIG_INVALID", inspected.unloadable_reason)
        self.assertEqual(["release", "release"], [call[1] for call in calls])

    def test_unreadable_config_only_marks_that_model_missing(self) -> None:
        target = (
            self.repo_root / "models" / "configs" / "siglip2-so400m-384.json"
        ).resolve()
        original_read_bytes = Path.read_bytes

        def selective_read_bytes(path: Path) -> bytes:
            if path == target:
                raise PermissionError("fixture denies reads")
            return original_read_bytes(path)

        with mock.patch.object(type(target), "read_bytes", selective_read_bytes):
            inspections = self.catalog().inspect_all()

        by_id = {item.model_id: item for item in inspections}
        self.assertEqual(
            "UNLOADABLE_REASON_CONFIG_MISSING",
            by_id["siglip2-so400m-384"].unloadable_reason,
        )
        self.assertTrue(
            any(
                item.model_id != "siglip2-so400m-384" and item.config
                for item in inspections
            )
        )

    def test_config_task_must_match_the_registry_entry(self) -> None:
        self.add_declared_weights()
        config_path = (
            self.repo_root / "models" / "configs" / "arcface-buffalo-l.json"
        )
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config["task"] = "face_detection"
        config_path.write_text(json.dumps(config), encoding="utf-8")
        registry_path = self.repo_root / "models" / "registry.json"
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
        for entry in registry["entries"]:
            if entry["model_id"] == "arcface-buffalo-l":
                entry["config_blake3"] = blake3(config_path.read_bytes()).hexdigest()
        registry_path.write_text(json.dumps(registry), encoding="utf-8")

        inspected = self.catalog(development=True).inspect("arcface-buffalo-l")

        self.assertIsNotNone(inspected)
        assert inspected is not None
        self.assertEqual(
            "UNLOADABLE_REASON_CONFIG_INVALID", inspected.unloadable_reason
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
