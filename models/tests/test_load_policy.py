"""The load policy, and the gate ml-runtime must implement.

Issue #10 acceptance criterion 2: unverified, blocked, hash-mismatched or
unregistered weights are unconditionally unloadable.

Taken literally that makes the system unloadable today, because licensing is
deliberately deferred (issue #3) and nothing has been hashed yet. The resolution
is two named modes plus one rule that holds in both -- a hash mismatch is always
fatal -- and these tests pin the resulting truth table so the Python host and
this policy cannot drift apart.

The gate itself lives in models/policy/load_gate.py and is imported here rather
than reimplemented. It was previously copied into this module, which meant these
tests proved things about the copy while `ml-runtime` would have imported the
other one -- the exact drift Codex objected to when the gate lived only in a
test. Codex should make the host agree with the imported module.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

MODELS_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(MODELS_ROOT.parent))

from models.policy.load_gate import Candidate, decide_load as _decide_load  # noqa: E402

REGISTRY = json.loads((MODELS_ROOT / "registry.json").read_text(encoding="utf-8"))
POLICY = REGISTRY["load_policy"]

# Digests the registry claims, not digests recomputed here -- recomputing would
# make every test agree with whatever the configs currently say, which is the
# one thing these tests must not do.
DIGESTS = {
    entry["model_id"]: entry.get("config_blake3") for entry in REGISTRY["entries"]
}


def decide_load(candidate: Candidate, mode: str) -> str | None:
    """Bind the gate to the registry this test module loaded."""
    return _decide_load(candidate, mode, POLICY)


def candidate(
    registered: bool = True,
    pinned_hash: str | None = "a" * 64,
    actual_hash: str | None = "a" * 64,
    license_verified: bool = True,
    blocks_commercial_release: bool = False,
    **overrides: object,
) -> Candidate:
    """A clean candidate by default; pass only the field under test.

    Weights and config are both present and both pinned to the same value they
    hash to, so any refusal a test sees comes from the thing it varied.
    """
    defaults: dict[str, object] = {
        "registered": registered,
        "weights_present": True,
        "pinned_hash": pinned_hash,
        "actual_hash": actual_hash,
        "license_verified": license_verified,
        "blocks_commercial_release": blocks_commercial_release,
        "config_present": True,
        "config_valid": True,
        "pinned_config_digest": "c" * 64,
        "actual_config_digest": "c" * 64,
    }
    defaults.update(overrides)
    return Candidate(**defaults)  # type: ignore[arg-type]


GOOD = candidate()


class TestPolicyShape(unittest.TestCase):
    def test_registry_validates_against_its_schema(self):
        from jsonschema import Draft202012Validator

        schema = json.loads(
            (MODELS_ROOT / "schema" / "registry.schema.json").read_text(encoding="utf-8")
        )
        Draft202012Validator.check_schema(schema)
        errors = sorted(
            Draft202012Validator(schema).iter_errors(REGISTRY), key=lambda e: list(e.path)
        )
        self.assertEqual([], [f"{list(e.path)}: {e.message}" for e in errors])

    def test_hash_mismatch_is_declared_always_fatal(self):
        self.assertTrue(POLICY["hash_mismatch_is_always_fatal"])

    def test_release_mode_relaxes_nothing(self):
        release = POLICY["modes"]["release"]
        self.assertTrue(release["require_registered"])
        self.assertTrue(release["require_pinned_hash"])
        self.assertTrue(release["require_license_verified"])
        self.assertFalse(release["allow_blocks_commercial_release"])
        self.assertIsNone(release["opt_in_env"])

    def test_any_relaxed_mode_requires_an_explicit_opt_in_and_warns(self):
        """A relaxed gate must be a deliberate act, and must never be silent."""
        for name, gate in POLICY["modes"].items():
            relaxed = (
                not gate["require_pinned_hash"]
                or not gate["require_license_verified"]
                or gate["allow_blocks_commercial_release"]
            )
            if not relaxed:
                continue
            with self.subTest(mode=name):
                self.assertIsNotNone(
                    gate["opt_in_env"],
                    f"{name} relaxes a gate without requiring an environment opt-in",
                )
                self.assertTrue(
                    gate["warn_per_load"],
                    f"{name} relaxes a gate without warning on every load",
                )


class TestGateDecisions(unittest.TestCase):
    def test_a_fully_clean_model_loads_in_both_modes(self):
        for mode in ("release", "development"):
            with self.subTest(mode=mode):
                self.assertIsNone(decide_load(GOOD, mode))

    def test_unregistered_is_refused_everywhere(self):
        cand = candidate(registered=False)
        for mode in ("release", "development"):
            with self.subTest(mode=mode):
                self.assertEqual(
                    "UNLOADABLE_REASON_NOT_REGISTERED", decide_load(cand, mode)
                )

    def test_hash_mismatch_is_refused_everywhere(self):
        """The one rule no mode may relax. A file that disagrees with its pin is
        corrupt or tampered with."""
        cand = candidate(actual_hash="b" * 64)
        for mode in ("release", "development"):
            with self.subTest(mode=mode):
                self.assertEqual(
                    "UNLOADABLE_REASON_HASH_MISMATCH", decide_load(cand, mode)
                )

    def test_integrity_is_reported_before_licensing(self):
        """A corrupt file that also has an unresolved licence must report the
        corruption, not the licence."""
        cand = candidate(actual_hash="b" * 64, license_verified=False,
                         blocks_commercial_release=True)
        self.assertEqual("UNLOADABLE_REASON_HASH_MISMATCH", decide_load(cand, "release"))

    def test_unpinned_weights_are_release_blocked_but_development_allowed(self):
        cand = candidate(pinned_hash=None, actual_hash=None)
        self.assertEqual("UNLOADABLE_REASON_HASH_UNPINNED", decide_load(cand, "release"))
        self.assertIsNone(decide_load(cand, "development"))

    def test_unverified_licence_is_release_blocked_but_development_allowed(self):
        cand = candidate(license_verified=False)
        self.assertEqual(
            "UNLOADABLE_REASON_LICENSE_UNVERIFIED", decide_load(cand, "release")
        )
        self.assertIsNone(decide_load(cand, "development"))

    def test_a_blocked_model_cannot_reach_a_release(self):
        cand = candidate(blocks_commercial_release=True)
        self.assertEqual(
            "UNLOADABLE_REASON_LICENSE_BLOCKS_RELEASE", decide_load(cand, "release")
        )
        self.assertIsNone(decide_load(cand, "development"))


class TestConfigDigestGate(unittest.TestCase):
    """The config is half of what determines behaviour, so the gate checks it.

    The SCRFD/ArcFace preprocessing defect -- 1/128 applied twice, collapsing the
    input range to a 0.016-wide sliver -- changed no weights byte, raised no
    error, and would have produced quietly wrong embeddings indefinitely. These
    tests exist so that class of change cannot pass as the pinned model.
    """

    def test_config_mismatch_is_refused_in_every_mode(self):
        cand = candidate(actual_config_digest="d" * 64)
        for mode in ("release", "development"):
            with self.subTest(mode=mode):
                self.assertEqual(
                    "UNLOADABLE_REASON_CONFIG_MISMATCH", decide_load(cand, mode)
                )

    def test_the_policy_declares_config_mismatch_always_fatal(self):
        self.assertTrue(POLICY["config_mismatch_is_always_fatal"])

    def test_weights_integrity_is_reported_before_config_integrity(self):
        """Both broken means the weights are reported. Arbitrary but fixed: one
        reason has to win, and a caller comparing two hosts must get the same
        one from both."""
        cand = candidate(actual_hash="b" * 64, actual_config_digest="d" * 64)
        self.assertEqual("UNLOADABLE_REASON_HASH_MISMATCH", decide_load(cand, "release"))

    def test_an_unpinned_config_blocks_release_but_not_development(self):
        cand = candidate(pinned_config_digest=None, actual_config_digest=None)
        self.assertEqual(
            "UNLOADABLE_REASON_CONFIG_UNPINNED", decide_load(cand, "release")
        )
        self.assertIsNone(decide_load(cand, "development"))

    def test_a_missing_config_is_distinct_from_an_invalid_one(self):
        """Same distinction as weights_present vs actual_hash: an absent file and
        a malformed one are different failures with different remedies."""
        self.assertEqual(
            "UNLOADABLE_REASON_CONFIG_MISSING",
            decide_load(candidate(config_present=False), "release"),
        )
        self.assertEqual(
            "UNLOADABLE_REASON_CONFIG_INVALID",
            decide_load(candidate(config_valid=False), "release"),
        )

    def test_a_mode_that_pins_weights_never_silently_accepts_an_unpinned_config(self):
        """The default when a mode predates `require_pinned_config`. Without it,
        adding the key to the policy later would leave older modes quietly
        certifying half a model."""
        legacy = {"modes": {"legacy": dict(POLICY["modes"]["release"])}}
        del legacy["modes"]["legacy"]["require_pinned_config"]
        cand = candidate(pinned_config_digest=None, actual_config_digest=None)
        self.assertEqual(
            "UNLOADABLE_REASON_CONFIG_UNPINNED", _decide_load(cand, "legacy", legacy)
        )


class TestConfigDigestCanonicalisation(unittest.TestCase):
    """The digest is over a canonical form, so it means "behaviour changed"
    rather than "someone reindented the file"."""

    def setUp(self):
        from models.policy import digest as module

        self.module = module
        try:
            module.blake3_hex(b"")
        except module.Blake3Missing:
            self.skipTest("blake3 is not installed")

    def _config(self) -> dict:
        return json.loads(
            (MODELS_ROOT / "configs" / "scrfd-10g-bnkps.json").read_text(encoding="utf-8")
        )

    def test_reformatting_does_not_change_the_digest(self):
        """Reindenting and reordering keys is not a behaviour change, and a
        digest that flagged it would be restamped reflexively until nobody read
        the diff -- which is how a real change gets waved through."""
        original = self._config()
        shuffled = dict(reversed(list(original.items())))
        self.assertEqual(
            self.module.blake3_hex(self.module.canonical_bytes(original)),
            self.module.blake3_hex(self.module.canonical_bytes(shuffled)),
        )

    def test_a_threshold_change_does_change_the_digest(self):
        """The whole point. Same weights, moved decision boundary, different
        pin."""
        original = self._config()
        edited = json.loads(json.dumps(original))
        target = edited.get("postprocessing", edited)
        key = next(
            (k for k in ("score_threshold", "nms_threshold", "nms_iou_threshold")
             if k in target),
            None,
        )
        if key is None:  # pragma: no cover - config shape changed
            self.skipTest("no threshold key in the SCRFD config to perturb")
        target[key] = float(target[key]) + 0.1
        self.assertNotEqual(
            self.module.blake3_hex(self.module.canonical_bytes(original)),
            self.module.blake3_hex(self.module.canonical_bytes(edited)),
        )

    def test_every_registry_entry_pins_the_config_on_disk(self):
        stale = [
            entry["model_id"]
            for entry in REGISTRY["entries"]
            if entry.get("config_blake3")
            != self.module.config_digest(MODELS_ROOT / entry["config"])
        ]
        self.assertEqual(
            [],
            stale,
            "config changed without restamping; run "
            "python3 models/policy/digest.py --write",
        )


class TestRegistryAgainstPolicy(unittest.TestCase):
    """What the policy means for the models actually in the registry today."""

    def _configs(self) -> dict[str, dict]:
        out = {}
        for entry in REGISTRY["entries"]:
            path = MODELS_ROOT / entry["config"]
            out[entry["model_id"]] = json.loads(path.read_text(encoding="utf-8"))
        return out

    def test_nothing_currently_qualifies_for_release(self):
        """Honest baseline: no weight is hashed and no licence is verified, so
        release mode loads nothing at all. That is the correct state, and saying
        so out loud is the point of the gate."""
        for model_id, config in self._configs().items():
            with self.subTest(model=model_id):
                cand = candidate(
                    pinned_hash=config["weights"]["blake3"],
                    actual_hash=config["weights"]["blake3"],
                    license_verified=config["license"]["verified"],
                    blocks_commercial_release=config["license"]["blocks_commercial_release"],
                    pinned_config_digest=DIGESTS.get(model_id),
                    actual_config_digest=DIGESTS.get(model_id),
                )
                self.assertIsNotNone(
                    decide_load(cand, "release"),
                    f"{model_id} claims to be release-ready; verify that is intended",
                )

    def test_everything_loads_in_development(self):
        """Velocity is not blocked: the deferral is recorded, not enforced."""
        for model_id, config in self._configs().items():
            with self.subTest(model=model_id):
                cand = candidate(
                    pinned_hash=config["weights"]["blake3"],
                    actual_hash=config["weights"]["blake3"],
                    license_verified=config["license"]["verified"],
                    blocks_commercial_release=config["license"]["blocks_commercial_release"],
                    pinned_config_digest=DIGESTS.get(model_id),
                    actual_config_digest=DIGESTS.get(model_id),
                )
                self.assertIsNone(decide_load(cand, "development"))

    def test_the_selected_face_detector_is_licence_cleared(self):
        """A licence-clean detector must remain available and working.

        The selected stack for internal use is SCRFD + ArcFace, on accuracy
        grounds. YuNet is not shelved by that decision: it stays configured and
        sits in the release pipeline, so the swap needed to ship commercially is
        mechanical rather than a rediscovery."""
        configs = self._configs()
        selected = configs["yunet-2023mar"]
        self.assertEqual("permitted", selected["license"]["commercial_use"])
        self.assertFalse(selected["license"]["blocks_commercial_release"])

        scrfd = configs["scrfd-10g-bnkps"]
        self.assertTrue(scrfd["license"]["blocks_commercial_release"])

        for name, spec in REGISTRY["pipelines"].items():
            if spec["min_load_mode"] != "release":
                continue
            with self.subTest(pipeline=name):
                self.assertNotIn(
                    "scrfd-10g-bnkps",
                    spec["steps"],
                    "SCRFD is non-commercial; it may not sit in a release pipeline",
                )

    def test_a_release_pipeline_contains_no_release_blocking_model(self):
        """The guard that survives the decision.

        Using non-commercial weights internally is a deliberate choice. Shipping
        them is not, and a pipeline declaring itself release-ready may not
        contain one -- which makes "we will sort licences later" a mechanical
        swap rather than an archaeology exercise.
        """
        configs = self._configs()
        for name, spec in REGISTRY["pipelines"].items():
            if spec["min_load_mode"] != "release":
                continue
            for step in spec["steps"]:
                config = configs.get(step)
                if config is None:
                    continue  # classical, non-model step
                with self.subTest(pipeline=name, step=step):
                    self.assertFalse(
                        config["license"]["blocks_commercial_release"],
                        f"{step} blocks a commercial release but sits in {name}",
                    )

    def test_a_development_pipeline_declares_itself_as_such(self):
        """A pipeline containing a blocked model must say it is development-only,
        so the constraint lives in the data rather than in someone's memory."""
        configs = self._configs()
        for name, spec in REGISTRY["pipelines"].items():
            blocked = [
                step for step in spec["steps"]
                if configs.get(step, {}).get("license", {}).get("blocks_commercial_release")
            ]
            if not blocked:
                continue
            with self.subTest(pipeline=name):
                self.assertEqual(
                    "development",
                    spec["min_load_mode"],
                    f"{name} contains {blocked} but claims to be release-ready",
                )

    def test_a_licence_clean_path_still_exists(self):
        """Insurance against the internal decision quietly becoming permanent."""
        release_pipelines = {
            name for name, spec in REGISTRY["pipelines"].items()
            if spec["min_load_mode"] == "release"
        }
        self.assertTrue(
            release_pipelines,
            "no release-ready pipeline remains; the licence-clean path has been lost",
        )


if __name__ == "__main__":
    unittest.main()
