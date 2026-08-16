"""Regressions for defects Codex found in review.

Every one of these would have failed silently -- the models would have run and
returned plausible numbers that were wrong -- which is exactly the class of bug
a golden test has to catch, because nothing downstream would have complained.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

MODELS_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(MODELS_ROOT))

from policy import Candidate, decide_load, load_policy, resolve_mode  # noqa: E402

REGISTRY = json.loads((MODELS_ROOT / "registry.json").read_text(encoding="utf-8"))


def configs() -> list[tuple[str, dict]]:
    return [
        (p.name, json.loads(p.read_text(encoding="utf-8")))
        for p in sorted((MODELS_ROOT / "configs").glob("*.json"))
    ]


class TestPreprocessingSpansItsRange(unittest.TestCase):
    """THE BUG: SCRFD and ArcFace carried scale=1/128 alongside mean=127.5,
    std=128, applying the division twice. The whole 0-255 input range collapsed
    into a 0.016-wide sliver near -1. Detection and every face embedding would
    have been garbage, and nothing would have raised."""

    @staticmethod
    def _normalise(pixel: float, pre: dict, channel: int = 0) -> float:
        value = pixel * pre["scale"]
        if pre["mean"]:
            value -= pre["mean"][channel]
        if pre["std"]:
            value /= pre["std"][channel]
        return value

    def test_every_image_model_maps_black_and_white_far_apart(self):
        for name, config in configs():
            pre = config["preprocessing"]
            if pre["kind"] not in {"image", "image_sequence", "face_crop"}:
                continue
            with self.subTest(config=name):
                low = self._normalise(0.0, pre)
                high = self._normalise(255.0, pre)
                span = abs(high - low)
                self.assertGreater(
                    span, 0.5,
                    f"black->{low:+.4f} white->{high:+.4f} spans only {span:.4f}. "
                    "The input range has collapsed, which means scale is being "
                    "applied on top of mean/std rather than instead of them.",
                )

    def test_normalised_output_is_centred_not_offset(self):
        """A correct normalisation straddles zero. One that lands entirely on
        one side is the signature of the double-scaling bug."""
        for name, config in configs():
            pre = config["preprocessing"]
            if pre["kind"] not in {"image", "face_crop"} or not pre["mean"]:
                continue
            with self.subTest(config=name):
                low = self._normalise(0.0, pre)
                high = self._normalise(255.0, pre)
                self.assertLess(low, 0.0, "black should be below zero")
                self.assertGreater(high, 0.0, "white should be above zero")


class TestPipelinesAreRunnable(unittest.TestCase):
    """A pipeline containing a known-broken step is a plan, not a pipeline."""

    def _configs_by_id(self) -> dict[str, dict]:
        return {c["model_id"]: c for _, c in configs()}

    def test_no_pipeline_contains_a_placeholder_model(self):
        by_id = self._configs_by_id()
        for name, spec in REGISTRY["pipelines"].items():
            for step in spec["steps"]:
                config = by_id.get(step)
                if config is None:
                    continue
                with self.subTest(pipeline=name, step=step):
                    notes = (config.get("notes") or "").lower()
                    self.assertNotIn(
                        "placeholder for the shape", notes,
                        f"{step} is a documented placeholder but sits in {name}",
                    )


class TestLoadGateIsProductionCode(unittest.TestCase):
    """Codex refused to import the gate from a test module, correctly. These
    exercise the importable one, including the three gaps they identified."""

    POLICY = load_policy()
    GOOD = Candidate(True, True, "a" * 64, "a" * 64, True, False)

    def test_missing_weights_are_distinct_from_unpinned_ones(self):
        missing = Candidate(True, False, "a" * 64, None, True, False)
        self.assertEqual(
            "UNLOADABLE_REASON_WEIGHTS_MISSING",
            decide_load(missing, "development", self.POLICY),
        )
        unpinned = Candidate(True, True, None, None, True, False)
        self.assertEqual(
            "UNLOADABLE_REASON_HASH_UNPINNED",
            decide_load(unpinned, "release", self.POLICY),
        )

    def test_mode_fails_closed_when_nothing_is_configured(self):
        self.assertEqual("release", resolve_mode(self.POLICY, {}))

    def test_only_a_truthy_opt_in_relaxes_the_gate(self):
        var = "MEMORY_ENGINE_ALLOW_UNVERIFIED_MODELS"
        for value in ("1", "true", "YES", "on"):
            self.assertEqual("development", resolve_mode(self.POLICY, {var: value}))
        for value in ("0", "false", "", "no", "maybe"):
            self.assertEqual("release", resolve_mode(self.POLICY, {var: value}))

    def test_every_declared_unloadable_reason_is_reachable(self):
        """The proto declares these; each needs a path to being returned."""
        cases = {
            "UNLOADABLE_REASON_NOT_REGISTERED": Candidate(False, True, "a"*64, "a"*64, True, False),
            "UNLOADABLE_REASON_CONFIG_INVALID": Candidate(True, True, "a"*64, "a"*64, True, False, config_valid=False),
            "UNLOADABLE_REASON_WEIGHTS_MISSING": Candidate(True, False, "a"*64, "a"*64, True, False),
            "UNLOADABLE_REASON_HASH_MISMATCH": Candidate(True, True, "a"*64, "b"*64, True, False),
            "UNLOADABLE_REASON_NO_PROVIDER_AVAILABLE": Candidate(True, True, "a"*64, "a"*64, True, False, available_providers=()),
        }
        for expected, candidate in cases.items():
            with self.subTest(reason=expected):
                self.assertEqual(expected, decide_load(candidate, "development", self.POLICY))

    def test_integrity_outranks_licensing(self):
        corrupt_and_unlicensed = Candidate(True, True, "a" * 64, "b" * 64, False, True)
        self.assertEqual(
            "UNLOADABLE_REASON_HASH_MISMATCH",
            decide_load(corrupt_and_unlicensed, "release", self.POLICY),
        )

    def test_a_clean_model_loads(self):
        self.assertIsNone(decide_load(self.GOOD, "release", self.POLICY))


if __name__ == "__main__":
    unittest.main()
