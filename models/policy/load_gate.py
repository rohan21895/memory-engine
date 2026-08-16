"""The model load gate, as importable production code.

`workers/ml-runtime` should call `decide_load` rather than reimplementing the
reasoning. Codex flagged that the only implementation lived inside a test
module: importing production behaviour from a test is unacceptable, and
duplicating it guarantees the two copies drift. So it lives here, and the tests
import it.

Codex also found three real gaps in the original four-check version, all fixed:
missing weights were indistinguishable from unpinned ones, the environment
opt-in was named but never enforced, and several UnloadableReasons the proto
declares had no path to being returned.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

REGISTRY_PATH = Path(__file__).resolve().parent.parent / "registry.json"

# Values that count as enabling an opt-in environment variable. Anything else,
# including "0", "false" and the empty string, does not -- an unset-looking
# value must never silently relax a gate.
TRUTHY = frozenset({"1", "true", "yes", "on"})


class UnloadableReason(str):
    """Mirrors the proto enum so the host can return it directly."""

    NOT_REGISTERED = "UNLOADABLE_REASON_NOT_REGISTERED"
    WEIGHTS_MISSING = "UNLOADABLE_REASON_WEIGHTS_MISSING"
    HASH_MISMATCH = "UNLOADABLE_REASON_HASH_MISMATCH"
    HASH_UNPINNED = "UNLOADABLE_REASON_HASH_UNPINNED"
    LICENSE_UNVERIFIED = "UNLOADABLE_REASON_LICENSE_UNVERIFIED"
    LICENSE_BLOCKS_RELEASE = "UNLOADABLE_REASON_LICENSE_BLOCKS_RELEASE"
    NO_PROVIDER_AVAILABLE = "UNLOADABLE_REASON_NO_PROVIDER_AVAILABLE"
    CONFIG_INVALID = "UNLOADABLE_REASON_CONFIG_INVALID"
    CONFIG_MISSING = "UNLOADABLE_REASON_CONFIG_MISSING"
    CONFIG_MISMATCH = "UNLOADABLE_REASON_CONFIG_MISMATCH"
    CONFIG_UNPINNED = "UNLOADABLE_REASON_CONFIG_UNPINNED"


@dataclass(frozen=True)
class Candidate:
    """Everything the gate needs about one load attempt.

    `weights_present` is separate from `actual_hash` on purpose: a file that is
    absent and a file that is present but unhashed are different failures, and
    conflating them was one of the defects Codex found.

    The config digest fields mirror the weights ones for the same reason they
    exist in ModelPin: the config is half of what determines behaviour, and a
    gate that checks only the weights certifies half a model.
    """

    registered: bool
    weights_present: bool
    pinned_hash: str | None
    actual_hash: str | None
    license_verified: bool
    blocks_commercial_release: bool
    config_valid: bool = True
    config_present: bool = True
    pinned_config_digest: str | None = None
    actual_config_digest: str | None = None
    available_providers: tuple[str, ...] = ("onnxruntime_cpu",)


def load_policy(registry_path: Path | None = None) -> dict:
    path = registry_path or REGISTRY_PATH
    return json.loads(path.read_text(encoding="utf-8"))["load_policy"]


def resolve_mode(policy: dict, environ: dict | None = None) -> str:
    """Which gate is in force.

    Release unless a relaxed mode's opt-in variable is explicitly set to a
    truthy value. Defaulting to the strict mode means forgetting to configure
    anything fails closed.
    """
    env = os.environ if environ is None else environ
    for name, gate in policy["modes"].items():
        opt_in = gate.get("opt_in_env")
        if opt_in and str(env.get(opt_in, "")).strip().lower() in TRUTHY:
            return name
    return "release"


def decide_load(candidate: Candidate, mode: str, policy: dict | None = None) -> str | None:
    """None permits the load; otherwise the UnloadableReason refusing it.

    Precedence is deliberate and deterministic, because `ListModels` must give
    one reason when several gates fail:

        registration -> config present/valid -> weights present -> integrity
        (weights, then config) -> pinning (weights, then config) -> licence
        -> providers

    Integrity outranks licensing: a corrupt file is a worse problem than an
    unresolved licence and should be reported as itself.
    """
    gate = (policy or load_policy())["modes"][mode]

    if gate["require_registered"] and not candidate.registered:
        return UnloadableReason.NOT_REGISTERED

    if not candidate.config_present:
        return UnloadableReason.CONFIG_MISSING

    if not candidate.config_valid:
        return UnloadableReason.CONFIG_INVALID

    if not candidate.weights_present:
        return UnloadableReason.WEIGHTS_MISSING

    # Always fatal, in every mode. A file disagreeing with its pin is corrupt or
    # tampered with, and no build flag may wave that through.
    if (
        candidate.pinned_hash is not None
        and candidate.actual_hash is not None
        and candidate.pinned_hash != candidate.actual_hash
    ):
        return UnloadableReason.HASH_MISMATCH

    # Same rule, same reason. An edited config is the likelier of the two in
    # practice -- weights are downloaded once and never touched, thresholds get
    # tuned by hand at 1am -- so waving this through would be the more common
    # way to run something other than what the pin claims.
    if (
        candidate.pinned_config_digest is not None
        and candidate.actual_config_digest is not None
        and candidate.pinned_config_digest != candidate.actual_config_digest
    ):
        return UnloadableReason.CONFIG_MISMATCH

    if gate["require_pinned_hash"] and candidate.pinned_hash is None:
        return UnloadableReason.HASH_UNPINNED

    # Defaults to the weights-pinning gate when the mode predates this key, so
    # a mode that requires pinned weights never silently accepts an unpinned
    # config.
    if gate.get("require_pinned_config", gate["require_pinned_hash"]) and (
        candidate.pinned_config_digest is None
    ):
        return UnloadableReason.CONFIG_UNPINNED

    if gate["require_license_verified"] and not candidate.license_verified:
        return UnloadableReason.LICENSE_UNVERIFIED

    if not gate["allow_blocks_commercial_release"] and candidate.blocks_commercial_release:
        return UnloadableReason.LICENSE_BLOCKS_RELEASE

    if not candidate.available_providers:
        return UnloadableReason.NO_PROVIDER_AVAILABLE

    return None
