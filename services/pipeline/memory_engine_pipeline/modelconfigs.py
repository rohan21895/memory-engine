"""What the pipeline has to read out of `models/configs/` before it can call.

THE ONE FACT THIS MODULE EXISTS FOR

An embedding model's alignment template is defined against ONE landmark
ordering, and the detector that produced the landmarks has an ordering of its
own. `models/configs/arcface-buffalo-l.json` says it in its notes: "Feeding an
unaligned crop does not fail -- it returns a confidently wrong embedding, which
then clusters wrongly and puts the wrong person in an album." Feeding a
correctly-warped crop built from the WRONG five points fails the same way and
is harder to see, because the warp itself succeeds.

So the pipeline does not hardcode "SCRFD gives insightface_5". It reads both
configs, compares them, and refuses the pairing before a single face is
embedded. Hardcoding would survive a config edit; reading does not.

WHY THE CONFIG AND NOT THE HOST

The host reports a `config_blake3` in every response, but the caller has to
decide whether to CALL before it has a response to read. The config file on
disk is the same artifact the host loads and hashes, and the host refuses to
serve a config whose digest disagrees with the registry pin
(UNLOADABLE_REASON_CONFIG_MISMATCH), so reading it here and calling there is
one source of truth rather than two.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path
from typing import Any

__all__ = [
    "ModelConfigError",
    "detector_landmark_scheme",
    "embedder_alignment_scheme",
    "embedder_vector_space",
    "read_model_config",
]


class ModelConfigError(Exception):
    """A model config is missing, unreadable, or does not describe what it must."""


@lru_cache(maxsize=32)
def _load(path: str) -> Mapping[str, Any]:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as error:
        raise ModelConfigError(f"{path}: {error}") from error
    except json.JSONDecodeError as error:
        raise ModelConfigError(f"{path} is not valid JSON: {error}") from error


def read_model_config(repo_root: Path, model_id: str) -> Mapping[str, Any]:
    path = repo_root / "models" / "configs" / f"{model_id}.json"
    if not path.is_file():
        raise ModelConfigError(
            f"no model config at {path}. The registry is the only place a model's "
            "preprocessing lives, and guessing it produces silently wrong input"
        )
    return _load(str(path))


def detector_landmark_scheme(config: Mapping[str, Any]) -> str | None:
    """The ordering this detector's keypoints come out in, or None if it has none.

    None is a real answer: a detector with no keypoint head (SCRFD's non-`kps`
    variants, a body detector) cannot feed an alignment template at all, and
    that has to read differently from "it emits some unspecified five points".
    """
    scheme = (config.get("postprocessing") or {}).get("landmark_scheme")
    if scheme in (None, ""):
        return None
    if not isinstance(scheme, str):
        raise ModelConfigError(
            f"{config.get('model_id')!r}: postprocessing.landmark_scheme must be a "
            f"string, got {scheme!r}"
        )
    return scheme


def embedder_alignment_scheme(config: Mapping[str, Any]) -> str:
    """The landmark ordering this embedder's alignment template is built for."""
    alignment = (config.get("preprocessing") or {}).get("face_alignment")
    if not isinstance(alignment, Mapping):
        raise ModelConfigError(
            f"{config.get('model_id')!r}: the config declares no face_alignment "
            "block, so it is not a face embedding model this pipeline can drive"
        )
    scheme = alignment.get("landmark_scheme")
    if not isinstance(scheme, str) or not scheme:
        raise ModelConfigError(
            f"{config.get('model_id')!r}: face_alignment names no landmark_scheme. "
            "A template without an ordering is a template that can be fed the wrong "
            "five points"
        )
    return scheme


def embedder_vector_space(config: Mapping[str, Any]) -> tuple[str, int]:
    """The (space, dimensions) this model writes into.

    Read from the config's declared outputs rather than from a table in the
    pipeline, for the same reason `analysis.EMBEDDING_SPACES` refuses an
    unknown model: a vector stored in a space it was not produced in is worse
    than no vector, because every later distance is a plausible number with no
    meaning.
    """
    for output in config.get("outputs") or []:
        space = output.get("vector_space")
        dimensions = output.get("dimensions")
        if space and dimensions:
            return str(space), int(dimensions)
    raise ModelConfigError(
        f"{config.get('model_id')!r}: no output declares a vector_space and a "
        "dimension count, so there is nowhere to store what it returns"
    )
