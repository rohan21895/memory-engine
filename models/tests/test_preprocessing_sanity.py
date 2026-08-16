"""Regressions for defects Codex found in review.

Every one of these would have failed silently -- the models would have run and
returned plausible numbers that were wrong -- which is exactly the class of bug
a golden test has to catch, because nothing downstream would have complained.
"""

from __future__ import annotations

import json
import re
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
    GOOD = Candidate(
        True, True, "a" * 64, "a" * 64, True, False,
        pinned_config_digest="c" * 64, actual_config_digest="c" * 64,
    )

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

    def _case(self, mode: str = "development", **overrides) -> tuple[Candidate, str]:
        fields = {
            "registered": True,
            "weights_present": True,
            "pinned_hash": "a" * 64,
            "actual_hash": "a" * 64,
            "license_verified": True,
            "blocks_commercial_release": False,
            "pinned_config_digest": "c" * 64,
            "actual_config_digest": "c" * 64,
        }
        fields.update(overrides)
        return Candidate(**fields), mode

    def test_every_declared_unloadable_reason_is_reachable(self):
        """A reason the proto declares but the gate can never return is a lie in
        the contract: a caller writes a branch for it that is dead, or worse
        assumes the condition cannot occur.

        The expected set is read from the proto rather than listed here, so
        adding a reason there without a path to returning it fails immediately
        instead of whenever someone next reads both files.
        """
        proto = (
            Path(__file__).resolve().parents[2]
            / "contracts" / "proto" / "ml_runtime.proto"
        ).read_text(encoding="utf-8")
        declared = {
            name
            for name in re.findall(r"(UNLOADABLE_REASON_\w+)\s*=\s*\d+;", proto)
            if not name.endswith("_UNSPECIFIED")
        }

        cases = {
            "UNLOADABLE_REASON_NOT_REGISTERED": self._case(registered=False),
            "UNLOADABLE_REASON_CONFIG_MISSING": self._case(config_present=False),
            "UNLOADABLE_REASON_CONFIG_INVALID": self._case(config_valid=False),
            "UNLOADABLE_REASON_WEIGHTS_MISSING": self._case(weights_present=False),
            "UNLOADABLE_REASON_HASH_MISMATCH": self._case(actual_hash="b" * 64),
            "UNLOADABLE_REASON_CONFIG_MISMATCH": self._case(
                actual_config_digest="d" * 64
            ),
            "UNLOADABLE_REASON_HASH_UNPINNED": self._case(
                "release", pinned_hash=None, actual_hash=None
            ),
            "UNLOADABLE_REASON_CONFIG_UNPINNED": self._case(
                "release", pinned_config_digest=None, actual_config_digest=None
            ),
            "UNLOADABLE_REASON_LICENSE_UNVERIFIED": self._case(
                "release", license_verified=False
            ),
            "UNLOADABLE_REASON_LICENSE_BLOCKS_RELEASE": self._case(
                "release", blocks_commercial_release=True
            ),
            "UNLOADABLE_REASON_NO_PROVIDER_AVAILABLE": self._case(
                available_providers=()
            ),
            # The fail-open Codex found: pinned to a hash the loader never
            # computed. Refused in every mode, because it is a loader bug rather
            # than a policy choice.
            "UNLOADABLE_REASON_INTEGRITY_UNVERIFIED": self._case(actual_hash=None),
        }

        self.assertEqual(
            set(),
            declared - set(cases),
            "the proto declares reasons the gate has no path to returning",
        )
        for expected, (candidate, mode) in cases.items():
            with self.subTest(reason=expected):
                self.assertEqual(expected, decide_load(candidate, mode, self.POLICY))

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
