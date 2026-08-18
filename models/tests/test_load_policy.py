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

from models.policy.load_gate import (  # noqa: E402
    Candidate,
    decide_load as _decide_load,
    preprocessing_pinned,
)

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

    def test_a_pin_that_was_never_checked_is_refused(self):
        """The fail-open Codex found.

        The mismatch test needs BOTH the pinned and the actual value; the
        pinning test only looks at the pinned one. So a candidate pinned to a
        hash whose file was never hashed passed release mode -- the gate whose
        entire job is refusing unverified weights was accepting exactly that.
        """
        for field in ("actual_hash", "actual_config_digest"):
            with self.subTest(field=field):
                cand = candidate(**{field: None})
                for mode in ("release", "development"):
                    self.assertEqual(
                        "UNLOADABLE_REASON_INTEGRITY_UNVERIFIED",
                        decide_load(cand, mode),
                        f"{field}=None passed the integrity gate in {mode}",
                    )

    def test_deferred_hashing_still_looks_like_an_absent_pin(self):
        """The distinction that keeps the fix from blocking real work: an entry
        with no pin at all is what deferred hashing looks like, and development
        mode still permits it."""
        cand = candidate(pinned_hash=None, actual_hash=None,
                         pinned_config_digest=None, actual_config_digest=None)
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


class TestConfigFormat(unittest.TestCase):
    """Formatting is part of the config's identity now, so it is checked
    WITHOUT blake3.

    Deliberately outside TestConfigDigest, whose setUp skips when blake3 is
    absent. CI installs pydantic and jsonschema only, so putting this behind
    that skip would mean the one part of the digest contract that needs no
    dependency at all went unchecked on main -- which is the same shape of
    mistake as the digest check exiting 0 when it could not run.
    """

    def _module(self):
        from models.policy import digest as module

        return module

    def test_every_committed_config_is_canonically_formatted(self):
        module = self._module()
        unformatted = [
            entry["model_id"]
            for entry in REGISTRY["entries"]
            if not module.is_canonical(MODELS_ROOT / entry["config"])
        ]
        self.assertEqual([], unformatted,
                         "run python3 models/policy/digest.py --write")

    def test_the_registry_itself_is_canonically_formatted(self):
        """It is rewritten by --write and carries the digests, so a reformat
        there is just as much a diff nobody reads."""
        self.assertTrue(self._module().is_canonical(MODELS_ROOT / "registry.json"))


class TestConfigDigest(unittest.TestCase):
    """The digest is over the config file's BYTES.

    The first design hashed a "canonical" JSON re-serialisation -- sorted keys,
    compact separators. Codex rejected it and was right: Python writes the float
    `1.0` as `1.0` and JavaScript writes it as `1`, and seven of eight configs
    contain a `1.0`. The Rust host and the TypeScript shell would have computed
    different digests from identical files, and it would have surfaced as
    CONFIG_MISMATCH on every model -- reading as "someone edited the configs"
    rather than "the digest is not portable".

    Bytes are the one representation every language agrees on without
    coordination. The cost is that formatting becomes part of identity, which is
    why the format is enforced rather than assumed.
    """

    def setUp(self):
        from models.policy import digest as module

        self.module = module
        try:
            module.blake3_hex(b"")
        except module.Blake3Missing:
            self.skipTest("blake3 is not installed")

    SCRFD = "configs/scrfd-10g-bnkps.json"

    def test_the_digest_is_the_hash_of_the_bytes_on_disk(self):
        """Stated as an equality rather than a docstring, so a future change
        back to re-serialisation fails here."""
        path = MODELS_ROOT / self.SCRFD
        self.assertEqual(
            self.module.blake3_hex(path.read_bytes()),
            self.module.config_digest(path),
        )

    def test_python_float_formatting_would_have_broken_portability(self):
        """The specific counter-example, kept executable.

        `json.dumps(1.0)` is `1.0` in Python and `1` in JavaScript. This asserts
        the hazard is real and present in our data, so nobody reintroduces
        re-serialisation on the grounds that it "should be fine".
        """
        self.assertEqual("1.0", json.dumps(1.0))
        with_floats = [
            entry["model_id"]
            for entry in REGISTRY["entries"]
            if "1.0" in (MODELS_ROOT / entry["config"]).read_text(encoding="utf-8")
        ]
        self.assertTrue(
            with_floats,
            "no config contains a float whose Python and JavaScript spellings "
            "differ; if that is genuinely true now, this guard can go",
        )

    def test_a_threshold_change_changes_the_digest(self):
        """The whole point. Same weights, moved decision boundary, different
        pin."""
        path = MODELS_ROOT / self.SCRFD
        original = path.read_bytes()
        edited = json.loads(original.decode("utf-8"))
        target = edited["postprocessing"]
        key = next(k for k in ("score_threshold", "nms_threshold") if k in target)
        target[key] = float(target[key]) + 0.1
        self.assertNotEqual(
            self.module.blake3_hex(original),
            self.module.blake3_hex(self.module.canonical_bytes(edited)),
        )

    def test_a_non_canonical_file_is_refused_rather_than_hashed(self):
        """Hashing an unformatted file would produce a digest that changes the
        next time an editor touches it, and a digest that churns gets restamped
        reflexively until nobody reads the diff."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text('{"b":1,   "a":2}', encoding="utf-8")
            self.assertFalse(self.module.is_canonical(path))
            with self.assertRaises(self.module.ConfigNotCanonical):
                self.module.config_digest(path)

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
        classical = set(REGISTRY.get("classical_steps", {}))
        for name, spec in REGISTRY["pipelines"].items():
            if spec["min_load_mode"] != "release":
                continue
            for step in spec["steps"]:
                config = configs.get(step)
                if config is None:
                    # A step with no model config is a classical step, or it is
                    # a name nobody can dispatch. Before issue #42 declared them
                    # in the registry these two were indistinguishable here, so
                    # a typo'd model id skipped the licence gate silently -- the
                    # gate reads as passing when it never ran.
                    with self.subTest(pipeline=name, step=step):
                        self.assertIn(
                            step,
                            classical,
                            f"{step} has no model config and is not a declared "
                            "classical step, so this licence check cannot see it",
                        )
                        self.assertFalse(
                            REGISTRY["classical_steps"][step]["license"][
                                "blocks_commercial_release"
                            ],
                            f"{step} blocks a commercial release but sits in {name}",
                        )
                    continue
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


class TestPreprocessingMustBePinned(unittest.TestCase):
    """Issue #33: a config may decline to pin the padded band's value, and a
    release must not load it.

    `pad_value: null` is an honest state, not a broken one -- SCRFD's two
    upstream references disagree by a full unit in tensor space, and picking one
    by preference is guessing. So the entry stays valid, says so out loud, and
    the gate refuses it where it matters.
    """

    def test_release_refuses_an_unpinned_preprocessing_value(self):
        self.assertEqual(
            "UNLOADABLE_REASON_CONFIG_UNPINNED",
            decide_load(candidate(preprocessing_pinned=False), "release"),
        )

    def test_development_permits_it_because_that_is_where_it_gets_measured(self):
        self.assertIsNone(decide_load(candidate(preprocessing_pinned=False), "development"))

    def test_pinning_it_removes_the_refusal(self):
        self.assertIsNone(decide_load(candidate(preprocessing_pinned=True), "release"))

    def test_weights_problems_are_still_reported_first(self):
        """Precedence: an unpinned pad value must not mask a missing file, or a
        user with no weights downloaded gets told about padding."""
        self.assertEqual(
            "UNLOADABLE_REASON_WEIGHTS_MISSING",
            decide_load(
                candidate(preprocessing_pinned=False, weights_present=False), "release"
            ),
        )


class TestPreprocessingPinnedIsComputedFromTheConfig(unittest.TestCase):
    """The `Candidate` field defaults to True, i.e. fail-open, so the thing that
    actually matters is that the real loader computes it. These test the
    computation; `workers/ml-runtime` passes it in `catalog.py::_inspect`."""

    def test_a_letterbox_config_with_a_null_pad_value_is_unpinned(self):
        self.assertFalse(
            preprocessing_pinned({"preprocessing": {"resize": "letterbox", "pad_value": None}})
        )

    def test_a_letterbox_config_with_a_pad_value_is_pinned(self):
        self.assertTrue(
            preprocessing_pinned(
                {
                    "preprocessing": {
                        "resize": "letterbox",
                        "pad_value": {"space": "pixel", "values": [0], "source": "x"},
                    }
                }
            )
        )

    def test_a_config_that_does_not_pad_has_nothing_to_pin(self):
        for resize in ("stretch", "center_crop", "none", "shortest_side", "longest_side"):
            with self.subTest(resize=resize):
                self.assertTrue(preprocessing_pinned({"preprocessing": {"resize": resize}}))

    def test_a_config_with_no_preprocessing_block_is_not_pinned(self):
        """Absence is not permission. A config the loader could not parse a
        preprocessing block out of has pinned nothing at all."""
        self.assertFalse(preprocessing_pinned({}))

    def test_the_shipped_configs_report_what_they_say(self):
        for path in sorted((MODELS_ROOT / "configs").glob("*.json")):
            config = json.loads(path.read_text(encoding="utf-8"))
            expected = not (
                config["preprocessing"].get("resize") == "letterbox"
                and config["preprocessing"].get("pad_value") is None
            )
            with self.subTest(config=path.name):
                self.assertEqual(expected, preprocessing_pinned(config))

    def test_scrfd_is_the_one_that_is_unpinned(self):
        """Named, so that resolving issue #33 for SCRFD has to update a test
        rather than quietly flipping a boolean nobody was watching."""
        scrfd = json.loads(
            (MODELS_ROOT / "configs" / "scrfd-10g-bnkps.json").read_text(encoding="utf-8")
        )
        self.assertIsNone(scrfd["preprocessing"]["pad_value"])
        self.assertFalse(preprocessing_pinned(scrfd))
        self.assertEqual(
            "UNLOADABLE_REASON_CONFIG_UNPINNED",
            decide_load(candidate(preprocessing_pinned=False), "release"),
        )
