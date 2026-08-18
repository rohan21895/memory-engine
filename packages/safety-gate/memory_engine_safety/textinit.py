"""Zero-shot initialisation of the head from SigLIP 2's own text tower.

WHAT THIS WOULD DO, AND WHY IT CANNOT DO IT TODAY

`docs/safety-classifier-decision.md` §6.2 step 1: embed a committed prompt bank
through `google/siglip2-so400m-patch14-384`'s TEXT encoder, average within class,
L2-normalise, subtract the benign reference, and stack. The result is a 1152x3
matrix, which *is* a linear head, obtained with zero third-party NSFW images and
zero third-party NSFW weights. The text tower is a build-time tool and never
ships.

The blocker is that there is no text tower in this registry to embed with.
`google/siglip2-so400m-patch14-384` publishes safetensors and no ONNX, which is
issue #79 -- open, being worked in parallel, and the reason
`models/configs/nsfw-siglip-head.json` still has `weights.blake3: null` and
`rollout.state: placeholder`.

So the arithmetic in this module is complete and tested, and the one thing it
cannot do is fetch the vectors it operates on. `build_head` takes a
`TextEncoder` and there is no implementation of that protocol in the tree. That
is the honest shape of "waiting on #79": not a stub that returns something, a
function with a hole where the model goes.

WHY THERE IS NO FALLBACK PATH

The obvious convenience -- a random matrix, a hash-derived matrix, anything that
lets the pipeline run end to end -- would produce three numbers per photograph
in [0, 1] that pass every threshold check, populate every `ClassScores`, satisfy
the manifest schema, and mean nothing. A number that looks like a safety
guarantee is the specific failure this whole package exists to prevent, and it
is worse than a block, because a block is visible.

The tests in this package therefore build heads from EXPLICITLY SYNTHETIC
vectors that the test constructs itself, and nothing in the shipped path can
reach that. A synthetic head in a unit test proves the arithmetic; a synthetic
head in the product is a lie with a confidence interval.

THE SELF-TEST IS THE POINT OF `build_head` RETURNING MORE THAN A MATRIX

`HeadBuild` carries `probes`: for each class, the class's own mean text
direction. Running the head over its own probes must put the maximum logit on
that class's column. That is a check on the COLUMN ORDER that needs no images at
all -- it catches the transposition in §6.6 at load time, on a user's machine,
after CI is long gone. `verify_class_axis` below is the check; see classes.py
for why the same fact is pinned in four places.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from memory_engine_safety.canonical import blake3_hex
from memory_engine_safety.classes import CLASS_ORDER, ClassOrderMismatch
from memory_engine_safety.embedding import EMBEDDING_DIMENSIONS, EMBEDDING_SPACE
from memory_engine_safety.head import HeadProvenance, LinearHead

__all__ = [
    "PROMPT_BANK_PATH",
    "REFERENCE_CLASS",
    "HeadBuild",
    "PromptBank",
    "TextEncoder",
    "TextTowerUnavailable",
    "build_head",
    "load_prompt_bank",
    "verify_class_axis",
]

PROMPT_BANK_PATH = Path(__file__).resolve().parent / "prompts.json"

#: The fourth direction, embedded and subtracted but never emitted. Named here
#: rather than inferred from "whichever key is not in CLASS_ORDER", because an
#: inference would silently accept a bank that grew a fifth class.
REFERENCE_CLASS = "benign"


class TextTowerUnavailable(RuntimeError):
    """There is no text encoder, so there is no head to build.

    Raised rather than returning `None`, and carrying the issue number, because
    the person who hits this needs to know it is a missing export and not a bug
    in their invocation.
    """


@runtime_checkable
class TextEncoder(Protocol):
    """SigLIP 2's text tower, at build time only. Never shipped, never loaded
    in the product, never on the inference path.

    NOTHING IN THIS REPOSITORY IMPLEMENTS THIS YET -- see the module docstring
    and issue #79.
    """

    space: str
    dimensions: int

    def encode(self, prompts: Sequence[str]) -> Sequence[Sequence[float]]:
        """One vector per prompt, in the order given."""
        ...


@dataclass(frozen=True)
class PromptBank:
    """The committed bank, checked for the things that make a class wrong."""

    version: int
    text_tower: str
    reference_class: str
    prompts: Mapping[str, tuple[str, ...]]
    digest: str

    def prompts_for(self, name: str) -> tuple[str, ...]:
        try:
            return self.prompts[name]
        except KeyError:  # pragma: no cover - load_prompt_bank refuses first
            raise ClassOrderMismatch(
                f"the prompt bank has no class {name!r}"
            ) from None


@dataclass(frozen=True)
class HeadBuild:
    """A head plus the evidence that its columns are the right way round."""

    head: LinearHead
    #: One probe per class, in `CLASS_ORDER`: the class's own mean text
    #: direction, referenced-subtracted the same way the rows are. Running the
    #: head over probe `i` must maximise column `i`.
    probes: tuple[tuple[float, ...], ...]


def _l2_normalise(vector: Sequence[float], *, where: str) -> tuple[float, ...]:
    norm = math.sqrt(math.fsum(value * value for value in vector))
    if norm == 0.0 or norm != norm or norm == float("inf"):
        raise ValueError(
            f"{where}: the mean prompt direction has norm {norm}; it carries no "
            "direction and normalising it would divide by zero or produce NaN"
        )
    return tuple(value / norm for value in vector)


def _mean(vectors: Sequence[Sequence[float]], *, where: str) -> tuple[float, ...]:
    if not vectors:
        raise ValueError(f"{where}: no prompts, so there is no direction to average")
    width = len(vectors[0])
    for index, vector in enumerate(vectors):
        if len(vector) != width:
            raise ValueError(
                f"{where}: prompt {index} embedded to {len(vector)} dimensions "
                f"against {width}"
            )
    count = float(len(vectors))
    return tuple(
        math.fsum(vector[column] for vector in vectors) / count for column in range(width)
    )


def load_prompt_bank(path: Path | None = None) -> PromptBank:
    """Read and check the committed bank.

    Refusals here are about the things that make a class silently wrong: a
    missing class, an empty class, a duplicate prompt (which reweights the mean
    without saying so), and a bank whose declared reference is not present.
    """
    source = path or PROMPT_BANK_PATH
    raw = source.read_bytes()
    document = json.loads(raw.decode("utf-8"))
    version = document.get("prompt_bank_version")
    if version != 1:
        raise ValueError(
            f"prompt bank version {version!r} is not 1; denied rather than parsed"
        )
    classes = document.get("classes")
    if not isinstance(classes, Mapping):
        raise ValueError("the prompt bank declares no classes")

    reference = document.get("reference_class", REFERENCE_CLASS)
    expected = set(CLASS_ORDER) | {reference}
    present = set(classes)
    if present != expected:
        raise ClassOrderMismatch(
            f"the prompt bank covers {sorted(present)}; it must cover exactly "
            f"{sorted(expected)} -- an extra class silently contributes nothing "
            "and a missing one silently zeroes a row"
        )

    prompts: dict[str, tuple[str, ...]] = {}
    for name in sorted(expected):
        entries = classes[name].get("prompts")
        if not isinstance(entries, list) or not entries:
            raise ValueError(f"class {name!r} has no prompts")
        texts = [str(entry) for entry in entries]
        if len(set(texts)) != len(texts):
            duplicates = sorted({t for t in texts if texts.count(t) > 1})
            raise ValueError(
                f"class {name!r} repeats {duplicates}; a duplicate prompt "
                "reweights the class mean without anybody deciding to"
            )
        prompts[name] = tuple(texts)

    return PromptBank(
        version=version,
        text_tower=str(document.get("text_tower", "")),
        reference_class=str(reference),
        prompts=prompts,
        # Over the file's own bytes, so the digest recorded in a head artifact
        # identifies the exact text that produced it -- including the comments,
        # which are the part a reviewer argues with.
        digest=blake3_hex(raw),
    )


def build_head(
    encoder: TextEncoder | None,
    *,
    bank: PromptBank | None = None,
    note: str = "",
) -> HeadBuild:
    """Zero-shot head from the prompt bank. Refuses without a text tower.

    `encoder=None` is the state of the world today and is not an error the
    caller did something to cause, so the exception says which issue it is
    waiting on.
    """
    if encoder is None:
        raise TextTowerUnavailable(
            "no SigLIP 2 text tower is available, so the zero-shot head cannot be "
            "built. google/siglip2-so400m-patch14-384 publishes safetensors and no "
            "ONNX export (issue #79). There is deliberately no fallback: a head "
            "built from anything else would emit three numbers in [0, 1] that pass "
            "every threshold and mean nothing, which is worse than the block."
        )
    bank = bank or load_prompt_bank()
    if getattr(encoder, "space", None) != EMBEDDING_SPACE:
        raise ValueError(
            f"the text encoder embeds into {getattr(encoder, 'space', None)!r}, not "
            f"{EMBEDDING_SPACE!r}. SigLIP's image and text towers share a space by "
            "construction; two towers that do not are two models, and their dot "
            "product is a number with no meaning."
        )
    if getattr(encoder, "dimensions", None) != EMBEDDING_DIMENSIONS:
        raise ValueError(
            f"the text encoder is {getattr(encoder, 'dimensions', None)}-d against a "
            f"{EMBEDDING_DIMENSIONS}-d head"
        )

    directions: dict[str, tuple[float, ...]] = {}
    for name in (*CLASS_ORDER, bank.reference_class):
        prompts = bank.prompts_for(name)
        vectors = [tuple(float(v) for v in row) for row in encoder.encode(prompts)]
        if len(vectors) != len(prompts):
            raise ValueError(
                f"class {name!r}: {len(prompts)} prompts in, {len(vectors)} vectors "
                "out; a truncated batch silently drops prompts from the mean"
            )
        directions[name] = _l2_normalise(
            _mean(vectors, where=f"class {name!r}"), where=f"class {name!r}"
        )

    reference = directions[bank.reference_class]
    rows: list[tuple[float, ...]] = []
    probes: list[tuple[float, ...]] = []
    for name in CLASS_ORDER:
        direction = directions[name]
        # Subtract the benign reference, then renormalise. §6.2: this removes
        # the large class-independent "is this a photograph" component, which is
        # what leaves calibration with six parameters to fit rather than a scale
        # problem to solve.
        contrast = tuple(a - b for a, b in zip(direction, reference, strict=True))
        rows.append(_l2_normalise(contrast, where=f"class {name!r} contrast"))
        probes.append(contrast)

    head = LinearHead(
        class_order=CLASS_ORDER,
        rows=tuple(rows),
        # Zero bias: the offset is entirely the calibration's job, and putting a
        # non-zero bias here would mean two things could move the operating
        # point and only one of them would be recorded in the calibration
        # artifact.
        bias=tuple(0.0 for _ in CLASS_ORDER),
        space=EMBEDDING_SPACE,
        provenance=HeadProvenance(
            method="text_tower_zero_shot",
            text_tower=bank.text_tower,
            prompt_bank_digest=bank.digest,
            calibration_corpus_manifest=None,
            note=note,
        ),
    )
    return HeadBuild(head=head, probes=tuple(probes))


def verify_class_axis(build: HeadBuild) -> None:
    """Run the head over its own probes and refuse a transposed column order.

    THIS IS THE CHECK THAT NEEDS NO IMAGES. §6.6's defect -- nothing pinning
    which index of the 3-vector is which class -- is pinned by schemas in CI and
    by `read_config_class_order` at load. Both of those check what a file
    DECLARES. This checks what the matrix DOES: probe `i` is class `i`'s own
    text direction, so if column `i` is not the argmax, the rows are not in the
    order the artifact says they are.

    A schema cannot catch that, because the artifact would be internally
    consistent and wrong.
    """
    if len(build.probes) != len(CLASS_ORDER):
        raise ClassOrderMismatch(
            f"{len(build.probes)} probes against {len(CLASS_ORDER)} classes"
        )
    for index, (name, probe) in enumerate(zip(CLASS_ORDER, build.probes, strict=True)):
        logits = build.head.logits(probe)
        winner = max(range(len(logits)), key=lambda column: logits[column])
        if winner != index:
            raise ClassOrderMismatch(
                f"the head's own {name!r} probe maximises column {winner} "
                f"({CLASS_ORDER[winner]!r}), not column {index}. The weight rows "
                "are not in the order this artifact declares, which is the "
                "transposition that has no symptom: every score would stay in "
                "range and every threshold would still fire."
            )
