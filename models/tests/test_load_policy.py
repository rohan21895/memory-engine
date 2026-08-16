"""The load policy, and the gate ml-runtime must implement.

Issue #10 acceptance criterion 2: unverified, blocked, hash-mismatched or
unregistered weights are unconditionally unloadable.

Taken literally that makes the system unloadable today, because licensing is
deliberately deferred (issue #3) and nothing has been hashed yet. The resolution
is two named modes plus one rule that holds in both -- a hash mismatch is always
fatal -- and these tests pin the resulting truth table so the Python host and
this policy cannot drift apart.

`decide_load` here is the reference implementation of the gate. Codex should
make the host agree with it, not reimplement the reasoning.
"""

from __future__ import annotations

import json
import unittest
from dataclasses import dataclass
from pathlib import Path

MODELS_ROOT = Path(__file__).resolve().parent.parent
REGISTRY = json.loads((MODELS_ROOT / "registry.json").read_text(encoding="utf-8"))
POLICY = REGISTRY["load_policy"]


@dataclass(frozen=True)
class Candidate:
    """Everything the gate needs to know about a load attempt."""

    registered: bool
    pinned_hash: str | None
    actual_hash: str | None
    license_verified: bool
    blocks_commercial_release: bool


def decide_load(candidate: Candidate, mode: str) -> str | None:
    """Return None to permit the load, or the UnloadableReason that refuses it.

    Order matters: integrity is checked before licensing, because a corrupt file
    is a worse problem than an unresolved licence and should be reported as
    itself rather than masked by a licence complaint.
    """
    gate = POLICY["modes"][mode]

    if gate["require_registered"] and not candidate.registered:
        return "UNLOADABLE_REASON_NOT_REGISTERED"

    # Always fatal, in every mode. A file disagreeing with its pin is corrupt or
    # tampered with, and no build flag may wave that through.
    if (
        candidate.pinned_hash is not None
        and candidate.actual_hash is not None
        and candidate.pinned_hash != candidate.actual_hash
    ):
        return "UNLOADABLE_REASON_HASH_MISMATCH"

    if gate["require_pinned_hash"] and candidate.pinned_hash is None:
        return "UNLOADABLE_REASON_HASH_UNPINNED"

    if gate["require_license_verified"] and not candidate.license_verified:
        return "UNLOADABLE_REASON_LICENSE_UNVERIFIED"

    if not gate["allow_blocks_commercial_release"] and candidate.blocks_commercial_release:
        return "UNLOADABLE_REASON_LICENSE_BLOCKS_RELEASE"

    return None


GOOD = Candidate(
    registered=True,
    pinned_hash="a" * 64,
    actual_hash="a" * 64,
    license_verified=True,
    blocks_commercial_release=False,
)


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
        candidate = Candidate(False, "a" * 64, "a" * 64, True, False)
        for mode in ("release", "development"):
            with self.subTest(mode=mode):
                self.assertEqual(
                    "UNLOADABLE_REASON_NOT_REGISTERED", decide_load(candidate, mode)
                )

    def test_hash_mismatch_is_refused_everywhere(self):
        """The one rule no mode may relax. A file that disagrees with its pin is
        corrupt or tampered with."""
        candidate = Candidate(True, "a" * 64, "b" * 64, True, False)
        for mode in ("release", "development"):
            with self.subTest(mode=mode):
                self.assertEqual(
                    "UNLOADABLE_REASON_HASH_MISMATCH", decide_load(candidate, mode)
                )

    def test_integrity_is_reported_before_licensing(self):
        """A corrupt file that also has an unresolved licence must report the
        corruption, not the licence."""
        candidate = Candidate(True, "a" * 64, "b" * 64, False, True)
        self.assertEqual("UNLOADABLE_REASON_HASH_MISMATCH", decide_load(candidate, "release"))

    def test_unpinned_weights_are_release_blocked_but_development_allowed(self):
        candidate = Candidate(True, None, None, True, False)
        self.assertEqual("UNLOADABLE_REASON_HASH_UNPINNED", decide_load(candidate, "release"))
        self.assertIsNone(decide_load(candidate, "development"))

    def test_unverified_licence_is_release_blocked_but_development_allowed(self):
        candidate = Candidate(True, "a" * 64, "a" * 64, False, False)
        self.assertEqual(
            "UNLOADABLE_REASON_LICENSE_UNVERIFIED", decide_load(candidate, "release")
        )
        self.assertIsNone(decide_load(candidate, "development"))

    def test_a_blocked_model_cannot_reach_a_release(self):
        candidate = Candidate(True, "a" * 64, "a" * 64, True, True)
        self.assertEqual(
            "UNLOADABLE_REASON_LICENSE_BLOCKS_RELEASE", decide_load(candidate, "release")
        )
        self.assertIsNone(decide_load(candidate, "development"))


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
                candidate = Candidate(
                    registered=True,
                    pinned_hash=config["weights"]["blake3"],
                    actual_hash=config["weights"]["blake3"],
                    license_verified=config["license"]["verified"],
                    blocks_commercial_release=config["license"]["blocks_commercial_release"],
                )
                self.assertIsNotNone(
                    decide_load(candidate, "release"),
                    f"{model_id} claims to be release-ready; verify that is intended",
                )

    def test_everything_loads_in_development(self):
        """Velocity is not blocked: the deferral is recorded, not enforced."""
        for model_id, config in self._configs().items():
            with self.subTest(model=model_id):
                candidate = Candidate(
                    registered=True,
                    pinned_hash=config["weights"]["blake3"],
                    actual_hash=config["weights"]["blake3"],
                    license_verified=config["license"]["verified"],
                    blocks_commercial_release=config["license"]["blocks_commercial_release"],
                )
                self.assertIsNone(decide_load(candidate, "development"))

    def test_the_selected_face_detector_is_licence_cleared(self):
        """Issue #10 requires a licence-cleared detector, and issue #3 leaves
        SCRFD's weights unusable. YuNet is selected on that basis; SCRFD stays
        registered for benchmarking but out of every pipeline."""
        configs = self._configs()
        selected = configs["yunet-2023mar"]
        self.assertEqual("permitted", selected["license"]["commercial_use"])
        self.assertFalse(selected["license"]["blocks_commercial_release"])

        scrfd = configs["scrfd-10g-bnkps"]
        self.assertTrue(scrfd["license"]["blocks_commercial_release"])

        for pipeline, steps in REGISTRY["pipelines"].items():
            with self.subTest(pipeline=pipeline):
                self.assertNotIn(
                    "scrfd-10g-bnkps",
                    steps,
                    "SCRFD must not sit in a pipeline while issue #3 is unresolved",
                )

    def test_no_pipeline_contains_a_release_blocking_model(self):
        configs = self._configs()
        for pipeline, steps in REGISTRY["pipelines"].items():
            for step in steps:
                config = configs.get(step)
                if config is None:
                    continue  # classical, non-model step
                with self.subTest(pipeline=pipeline, step=step):
                    self.assertFalse(
                        config["license"]["blocks_commercial_release"],
                        f"{step} blocks a commercial release but sits in {pipeline}",
                    )


if __name__ == "__main__":
    unittest.main()
