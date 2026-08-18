"""The 1152 -> 3 linear head, and the artifact format it ships as.

WHAT THIS IS

`docs/safety-classifier-decision.md` §6 surveyed thirteen candidate classifiers
and rejected twelve of them on PROVENANCE rather than on licence: there is no
off-the-shelf open NSFW classifier with documented, rights-cleared training
data, so the requirement in issue #21 does not narrow the field, it empties it.
What is left is a construction:

    logits = W @ image_embedding + b,   W is 3x1152, b is 3

initialised from SigLIP 2's own text tower (see textinit.py), then per-class
Platt-calibrated (see calibration.py). 3 459 parameters, about 13.5 KiB, and
zero marginal inference cost because the embedding already exists for every
photograph in the library. That last property is a correctness property and not
a performance one: the gate has to run on EVERY file, because
`SafetyClearance.decision.cleared_for_publication` is false if even one item is
indeterminate, so a classifier too expensive to run on everything does not
degrade the product, it blocks it.

WHY PURE PYTHON ARITHMETIC AND NO NUMPY

Same reason packages/face-identity has none. This package must import and run
anywhere the contract tests run, including a CI container with no wheels built.
3 x 1152 multiply-accumulates per image is nothing next to the vision tower that
produced the embedding.

`math.fsum` rather than `sum`, and that is not fussiness. fsum is exactly
rounded, so the logit does not depend on summation order -- which means a photo
sitting exactly on the 0.3 threshold cannot flip between two runs that batched
differently. "Same library in, same decisions out" is hard rule 3, and for a
safety gate the failure it prevents is a photograph that was cleared yesterday
and blocked today with nothing in the diff.

WHAT IS DELIBERATELY REFUSED

* A head whose `class_order` is not the contract's. See classes.py.
* A head over any space but `siglip2_so400m_1152`.
* A non-finite weight. A NaN in W makes every logit NaN, and `nan >= 0.3` and
  `nan < 0.3` are both False -- so a NaN slides through whichever branch was
  written as the negative, which for a gate is whichever branch means "fine".
* A head with no calibration attached, at DECISION time. Raw SigLIP logits are
  not probabilities, so `sigmoid(logit) >= 0.3` compares a number to a threshold
  that was chosen for a probability. That is not a small error: it is a
  threshold with no meaning, producing verdicts that look calibrated. See
  calibration.py.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from memory_engine_safety.classes import CLASS_ORDER, check_class_order
from memory_engine_safety.embedding import EMBEDDING_DIMENSIONS, EMBEDDING_SPACE

__all__ = [
    "HEAD_ARTIFACT_VERSION",
    "HeadProvenance",
    "LinearHead",
    "load_head",
    "read_config_class_order",
]

#: Format version of the on-disk head artifact. A reader that does not
#: recognise it must DENY rather than parse best-effort, for the same reason
#: `SafetyClearance.manifest_version` says so: the field an old reader ignores
#: will be the one that was added because something went wrong.
HEAD_ARTIFACT_VERSION = 1


@dataclass(frozen=True)
class HeadProvenance:
    """Where the numbers came from. Recorded because it is the safety claim.

    For every other model in this stack, undocumented training data is a quality
    risk. For this one it IS the product -- the classifier's entire job is to
    make a claim about a family's private photographs, including their
    children's. So the artifact carries its own answer to "what is this trained
    on", and `docs/safety-classifier-decision.md` §6.1 is the long form.

    `prompt_bank_digest` is BLAKE3 over the committed prompt bank. It is what
    makes the zero-shot initialisation reproducible and reviewable: the prompts
    are text we wrote, in the repository, diffable and arguable-with, which is
    more scrutiny than any candidate model's training set permits.
    """

    method: str
    """`text_tower_zero_shot` or `logistic_refit`. Two different provenance
    stories: the first introduces no third-party images at all, the second
    reintroduces the data-volume problem in full and is the fallback."""

    text_tower: str | None
    """Model id of the text encoder the rows were initialised from, when
    `method` is zero-shot. `None` for a refit."""

    prompt_bank_digest: str | None
    calibration_corpus_manifest: str | None
    """Repo-relative path to the committed manifest of the calibration corpus --
    every image with a recorded source URL, licence and collection date. Null
    until the corpus exists; a head with a null here cannot be promoted past
    development, because the corpus IS the provenance claim."""

    note: str = ""


@dataclass(frozen=True)
class LinearHead:
    """W and b, with the class axis attached to them rather than assumed.

    `rows` is indexed BY CLASS, in `class_order`. That is the opposite of the
    tensor layout (`[batch, class]`) and it is deliberate: a row you can name is
    a row you cannot silently transpose.
    """

    class_order: tuple[str, ...]
    rows: tuple[tuple[float, ...], ...]
    bias: tuple[float, ...]
    space: str
    provenance: HeadProvenance

    def __post_init__(self) -> None:
        check_class_order(self.class_order, where="head artifact")
        if self.space != EMBEDDING_SPACE:
            raise ValueError(
                f"head is defined over {self.space!r}, not {EMBEDDING_SPACE!r}; a "
                "vector from another space has the same shape and no shared "
                "meaning, so the product would be three numbers and no information"
            )
        if len(self.rows) != len(self.class_order):
            raise ValueError(
                f"{len(self.rows)} weight rows against {len(self.class_order)} "
                "classes"
            )
        if len(self.bias) != len(self.class_order):
            raise ValueError(
                f"{len(self.bias)} bias terms against {len(self.class_order)} "
                "classes"
            )
        for name, row in zip(self.class_order, self.rows, strict=True):
            if len(row) != EMBEDDING_DIMENSIONS:
                raise ValueError(
                    f"row {name!r} has {len(row)} columns against a "
                    f"{EMBEDDING_DIMENSIONS}-d embedding"
                )
            _reject_non_finite(row, where=f"head row {name!r}")
        _reject_non_finite(self.bias, where="head bias")

    def logits(self, embedding: Sequence[float]) -> tuple[float, ...]:
        """Raw, UNCALIBRATED scores, in `class_order`.

        Named `logits` and not `scores` on purpose. Handing these to a sigmoid
        and comparing to 0.3 is the mistake calibration.py exists to prevent,
        and a name that read like a probability would invite it.
        """
        vector = tuple(float(value) for value in embedding)
        if len(vector) != EMBEDDING_DIMENSIONS:
            raise ValueError(
                f"embedding has {len(vector)} components against a "
                f"{EMBEDDING_DIMENSIONS}-d head"
            )
        _reject_non_finite(vector, where="image embedding")
        return tuple(
            math.fsum(w * x for w, x in zip(row, vector, strict=True)) + b
            for row, b in zip(self.rows, self.bias, strict=True)
        )

    def to_artifact(self) -> dict:
        """The committed JSON form. Round-trips through `load_head`."""
        return {
            "artifact_version": HEAD_ARTIFACT_VERSION,
            "space": self.space,
            "class_order": list(self.class_order),
            "rows": [list(row) for row in self.rows],
            "bias": list(self.bias),
            "provenance": {
                "method": self.provenance.method,
                "text_tower": self.provenance.text_tower,
                "prompt_bank_digest": self.provenance.prompt_bank_digest,
                "calibration_corpus_manifest": (
                    self.provenance.calibration_corpus_manifest
                ),
                "note": self.provenance.note,
            },
        }


def _reject_non_finite(values: Sequence[float], *, where: str) -> None:
    for index, value in enumerate(values):
        if value != value:
            raise ValueError(
                f"{where}[{index}] is NaN. Every comparison against NaN is False, "
                "so a NaN takes whichever branch was written as the negative -- "
                "and in a gate that branch means 'fine'."
            )
        if value in (float("inf"), float("-inf")):
            raise ValueError(f"{where}[{index}] is infinite")


def load_head(source: Path | Mapping) -> LinearHead:
    """Read a head artifact, refusing anything it does not fully recognise."""
    document = (
        json.loads(Path(source).read_text(encoding="utf-8"))
        if isinstance(source, (str, Path))
        else dict(source)
    )
    version = document.get("artifact_version")
    if version != HEAD_ARTIFACT_VERSION:
        raise ValueError(
            f"head artifact version {version!r} is not {HEAD_ARTIFACT_VERSION}; "
            "an unrecognised version is denied rather than parsed, because the "
            "field a stale reader ignores is the one that was added for a reason"
        )
    provenance = document.get("provenance") or {}
    return LinearHead(
        class_order=tuple(document.get("class_order", ())),
        rows=tuple(tuple(float(v) for v in row) for row in document.get("rows", ())),
        bias=tuple(float(v) for v in document.get("bias", ())),
        space=str(document.get("space", "")),
        provenance=HeadProvenance(
            method=str(provenance.get("method", "")),
            text_tower=provenance.get("text_tower"),
            prompt_bank_digest=provenance.get("prompt_bank_digest"),
            calibration_corpus_manifest=provenance.get("calibration_corpus_manifest"),
            note=str(provenance.get("note", "")),
        ),
    )


def read_config_class_order(config_path: Path) -> tuple[str, ...]:
    """The class axis a model config declares, checked against the contract.

    This is the load-time half of the four-way pin described in classes.py. The
    schema already refuses a config whose `class_order` is missing or transposed
    -- but the schema runs in CI, and this runs in the process that is about to
    multiply a matrix. A config edited on a user's machine after install passes
    no CI at all.
    """
    config = json.loads(Path(config_path).read_text(encoding="utf-8"))
    if config.get("task") != "safety_classifier":
        raise ValueError(
            f"{config_path.name} is task {config.get('task')!r}, not a safety "
            "classifier"
        )
    declared: list[tuple[str, object]] = [
        (output.get("name", "?"), output.get("class_order"))
        for output in config.get("outputs", [])
        if output.get("meaning") in {"logits", "scores"}
    ]
    if not declared:
        raise ValueError(
            f"{config_path.name} declares no logits or scores output, so there is "
            "no class axis to pin"
        )
    orders = set()
    for name, order in declared:
        orders.add(check_class_order(order, where=f"{config_path.name}:{name}"))
    if len(orders) != 1:
        raise ValueError(
            f"{config_path.name} declares more than one class axis; the head "
            "emits one"
        )
    order = orders.pop()
    shapes = {
        tuple(output.get("shape") or ())[-1:]
        for output in config.get("outputs", [])
        if output.get("meaning") in {"logits", "scores"}
    }
    for shape_tail in shapes:
        if shape_tail and shape_tail[0] != len(order):
            raise ValueError(
                f"{config_path.name}: {shape_tail[0]} output columns against "
                f"{len(order)} class names. A short order reads the wrong column "
                "and is exactly as silent as a transposition."
            )
    # `check_class_order` above already guarantees `order == CLASS_ORDER`; it is
    # not re-asserted here because `python -O` strips asserts, and a guard that
    # disappears under a flag is worse than no guard.
    return order
