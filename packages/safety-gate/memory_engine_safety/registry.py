"""Building a `SafetyClassifier` from what the model registry actually permits.

THIS IS WHERE THE BLOCK BECOMES EXECUTABLE RATHER THAN ARGUED

Everything else in this package is correct in the abstract. This module asks the
real `models/registry.json`, through the real `models/policy/load_gate.py`,
whether the sensitive-content head may load -- and today the answer is
`UNLOADABLE_REASON_PLACEHOLDER`, refused in every mode, because the entry has no
weights file, no hash, an unverified licence and `blocks_commercial_release:
true`. Four independent reasons, any one of which is enough.

That refusal becomes `load_gate_denied`, which is `indeterminate`, which blocks
every print, every share and every contact sheet. Running the tests in this
package is what demonstrates it; nothing here is a description of what would
happen.

WHY THE sys.path DANCE

`models/` is a repository-root directory rather than an installed distribution,
and this package must not depend on anything in `services/` (Codex's territory,
and a safety verifier that only works when a service is importable is a verifier
that fails open everywhere else). So the repo root goes on the path locally,
inside the function, and a failure to import is itself a refusal rather than an
exception that escapes.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from memory_engine_safety.classify import DEFAULT_THRESHOLD, SafetyClassifier, Thresholds
from memory_engine_safety.embedding import ImageEmbedder

__all__ = ["SAFETY_MODEL_ID", "classifier_from_registry", "model_ref_from_config"]

#: The registry id of the sensitive-content head. `docs/safety-classifier-
#: decision.md` §6.6 proposes renaming this to `sensitive-content-siglip2-head`,
#: because `nsfw-siglip-head` describes neither what it does nor what it emits.
#: Not done here: `workers/ml-runtime` loads by id, so a rename is a
#: cross-boundary change that needs Codex's sign-off rather than a unilateral
#: edit in a branch they cannot review this week.
SAFETY_MODEL_ID = "nsfw-siglip-head"


def _repo_root(start: Path | None = None) -> Path:
    here = (start or Path(__file__).resolve()).parent
    for candidate in (here, *here.parents):
        if (candidate / "models" / "registry.json").is_file():
            return candidate
    raise FileNotFoundError("no models/registry.json above this package")


def model_ref_from_config(repo_root: Path, model_id: str = SAFETY_MODEL_ID) -> dict[str, Any]:
    """The `ModelRef` a clearance would pin, read from the registry.

    `config_blake3` comes from the registry entry rather than being recomputed,
    so a config edited on disk without restamping produces a pin that will not
    match what the load gate verified -- which is a mismatch, which is a denial.
    """
    registry = json.loads((repo_root / "models" / "registry.json").read_text("utf-8"))
    entry = next(
        (e for e in registry["entries"] if e["model_id"] == model_id), None
    )
    if entry is None:
        raise LookupError(f"{model_id} is not in the registry")
    config = json.loads(
        (repo_root / "models" / "configs" / f"{model_id}.json").read_text("utf-8")
    )
    return {
        "model_id": model_id,
        "version": config["version"],
        "weights_blake3": config["weights"]["blake3"],
        "config_blake3": entry.get("config_blake3"),
        "runtime": None,
        "precision": config["weights"].get("quantization"),
    }


def classifier_from_registry(
    repo_root: Path | None = None,
    *,
    embedder: ImageEmbedder | None = None,
    thresholds: Thresholds | None = None,
    environ: dict[str, str] | None = None,
) -> tuple[SafetyClassifier, str]:
    """`(classifier, load_mode)` for the sensitive-content head as it stands.

    Returns rather than raises when the head cannot load: a refusal is a normal,
    expected state that has to become `indeterminate` verdicts, not an exception
    for a caller to handle however they feel like.
    """
    root = repo_root or _repo_root()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    try:
        from models.policy import digest as digest_module  # noqa: PLC0415
        from models.policy import load_gate  # noqa: PLC0415
    except Exception as failure:  # noqa: BLE001
        return (
            SafetyClassifier(
                thresholds=thresholds or Thresholds(),
                unavailable=(
                    "model_unloadable",
                    f"the model-registry load policy could not be imported "
                    f"({type(failure).__name__}: {failure}), so no gate decided "
                    "whether the classifier may load -- and undecided is not "
                    "permitted",
                ),
            ),
            "release",
        )

    policy = load_gate.load_policy(root / "models" / "registry.json")
    mode = load_gate.resolve_mode(policy, environ)

    registry = json.loads((root / "models" / "registry.json").read_text("utf-8"))
    entry = next(
        (e for e in registry["entries"] if e["model_id"] == SAFETY_MODEL_ID), None
    )
    config_path = root / "models" / "configs" / f"{SAFETY_MODEL_ID}.json"
    config = json.loads(config_path.read_text("utf-8")) if config_path.is_file() else {}
    weights_name = (config.get("weights") or {}).get("filename")
    weights_path = (
        root / "models" / "weights" / weights_name if weights_name else None
    )

    candidate = load_gate.Candidate(
        registered=entry is not None,
        weights_present=bool(weights_path and weights_path.is_file()),
        pinned_hash=(config.get("weights") or {}).get("blake3"),
        actual_hash=None,
        license_verified=bool((config.get("license") or {}).get("verified")),
        blocks_commercial_release=bool(
            (config.get("license") or {}).get("blocks_commercial_release", True)
        ),
        config_valid=bool(config),
        config_present=config_path.is_file(),
        pinned_config_digest=(entry or {}).get("config_blake3"),
        actual_config_digest=(
            digest_module.config_digest(config_path) if config_path.is_file() else None
        ),
        is_placeholder=(config.get("rollout") or {}).get("state") == "placeholder",
        preprocessing_pinned=load_gate.preprocessing_pinned(config),
    )
    refusal = load_gate.decide_load(candidate, mode, policy)
    if refusal is not None:
        return (
            SafetyClassifier(
                thresholds=thresholds or Thresholds(),
                unavailable=(
                    "load_gate_denied",
                    f"the model registry refuses to load {SAFETY_MODEL_ID} in {mode} "
                    f"mode: {refusal}. The head is a matrix over the SigLIP 2 "
                    "so400m-384 embedding and that tower has no ONNX export in this "
                    "registry yet (issue #79), so nothing has been fitted, hashed or "
                    "measured. Restoring the capability unblocks publication without "
                    "re-planning the project.",
                ),
            ),
            mode,
        )

    # Reachable only once #79 lands, the head is fitted and hashed, the licence
    # is verified and the rollout leaves `placeholder`. Deliberately left as a
    # refusal rather than a half-built loader: the weights format, the artifact
    # path and the calibration path are decisions that belong with the fitted
    # head, and guessing them now would produce a loader nobody tested against a
    # real file.
    return (
        SafetyClassifier(
            thresholds=thresholds or Thresholds(DEFAULT_THRESHOLD, DEFAULT_THRESHOLD, DEFAULT_THRESHOLD),
            embedder=embedder,
            unavailable=(
                "model_unavailable",
                f"{SAFETY_MODEL_ID} now passes the load gate, but this build has no "
                "code to read a fitted head artifact from disk -- that lands with "
                "the fitted head itself. Until then the answer is still 'nobody "
                "checked', which blocks.",
            ),
        ),
        mode,
    )
