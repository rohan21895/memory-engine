"""Shared fixtures. Everything here is deterministic and nothing here is a model.

THE ONE RULE THIS FILE HAS TO OBEY

`synthetic_head()` and `synthetic_calibration()` build a head out of numbers this
module wrote. They exist to prove the ARITHMETIC -- that a logit is a dot
product, that a Platt curve is a Platt curve, that a transposed column is caught.
They are not a stand-in for the real head and nothing in `memory_engine_safety`
can reach them: the shipped path gets its head from a fitted artifact that does
not exist yet (issue #79), and `AbsentEmbedder` is what the product wires in the
meantime.

The distinction is the whole reason this package exists. A synthetic head in a
unit test proves a multiplication. A synthetic head in the product is three
numbers in [0, 1] that look exactly like a measurement.
"""

from __future__ import annotations

import json
import sys
from functools import lru_cache
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = PACKAGE_ROOT.parent.parent
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

from memory_engine_safety.calibration import PlattScaling  # noqa: E402
from memory_engine_safety.classes import CLASS_ORDER  # noqa: E402
from memory_engine_safety.embedding import (  # noqa: E402
    EMBEDDING_DIMENSIONS,
    EMBEDDING_SPACE,
    StaticEmbedder,
)
from memory_engine_safety.head import HeadProvenance, LinearHead  # noqa: E402

SCHEMA_DIR = REPO_ROOT / "contracts" / "schemas"
FIXTURE_DIR = REPO_ROOT / "contracts" / "fixtures"

NOW = "2026-08-18T09:00:00+05:30"
RAN_AT = "2026-08-18T08:59:00+05:30"

MODEL_REF = {
    "model_id": "nsfw-siglip-head",
    "version": "1.0.0",
    "weights_blake3": "d" * 64,
    "config_blake3": "c" * 64,
    "runtime": "onnxruntime_coreml",
    "precision": "fp32",
}


def hex64(label: object) -> str:
    """A stable 64-hex id from any label. Not a hash of anything meaningful."""
    import hashlib

    return hashlib.blake2b(str(label).encode(), digest_size=32).hexdigest()


def axis(index: int, scale: float = 1.0) -> list[float]:
    values = [0.0] * EMBEDDING_DIMENSIONS
    values[index % EMBEDDING_DIMENSIONS] = scale
    return values


def synthetic_head(*, transposed: bool = False) -> LinearHead:
    """A head whose class `i` reads embedding axis `i`. Explicitly not a model.

    One axis per class makes every assertion in the tests readable: an embedding
    that is 1.0 on axis 0 and zero elsewhere has an `explicit` logit of 1 and
    the other two of 0. `transposed=True` swaps the first two rows, which is the
    §6.6 defect made concrete.
    """
    rows = [tuple(axis(index)) for index in range(len(CLASS_ORDER))]
    if transposed:
        rows[0], rows[1] = rows[1], rows[0]
    return LinearHead(
        class_order=CLASS_ORDER,
        rows=tuple(rows),
        bias=tuple(0.0 for _ in CLASS_ORDER),
        space=EMBEDDING_SPACE,
        provenance=HeadProvenance(
            method="text_tower_zero_shot",
            text_tower="TEST ONLY - not a model",
            prompt_bank_digest=None,
            calibration_corpus_manifest=None,
            note="synthetic, built by the test suite; see tests/support.py",
        ),
    )


def synthetic_calibration(scale: float = 8.0, bias: float = -4.0) -> PlattScaling:
    """A steep-ish curve so a logit of 1.0 lands well above 0.3 and 0.0 below."""
    return PlattScaling(
        class_order=CLASS_ORDER,
        scale=tuple(scale for _ in CLASS_ORDER),
        bias=tuple(bias for _ in CLASS_ORDER),
        support=tuple((250, 250) for _ in CLASS_ORDER),
        corpus_manifest=None,
        note="synthetic, built by the test suite",
    )


def embedder_over(vectors: dict[str, list[float]]) -> StaticEmbedder:
    return StaticEmbedder(vectors)


@lru_cache(maxsize=None)
def _schema_documents() -> dict[str, dict]:
    return {
        path.name: json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(SCHEMA_DIR.glob("*.schema.json"))
    }


def clearance_validator():
    """A Draft 2020-12 validator over the REAL contract schema.

    Loaded from the repository rather than hand-copied, so a schema change this
    package violates fails here rather than in a render.
    """
    from jsonschema import Draft202012Validator
    from referencing import Registry, Resource

    documents = _schema_documents()
    registry = Registry().with_resources(
        [(name, Resource.from_contents(doc)) for name, doc in documents.items()]
    )
    return Draft202012Validator(
        documents["safety-clearance.schema.json"], registry=registry
    )


def assert_valid_clearance(case, document: dict) -> None:
    errors = sorted(clearance_validator().iter_errors(document), key=lambda e: e.path)
    case.assertEqual(
        [],
        [f"{list(error.path)}: {error.message}" for error in errors],
        "the built manifest does not satisfy contracts/schemas/safety-clearance.schema.json",
    )
