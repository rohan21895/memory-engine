"""The embedding interface the head runs on, and the absence that blocks.

NOTHING IN THIS PACKAGE LOADS A MODEL.

The head is a 1152x3 matrix over the SigLIP 2 `so400m-384` image embedding that
`workers/ml-runtime` already computes for every photograph. This module defines
the SHAPE of the thing that supplies those vectors, so the head, the calibration
and the whole gate can be built and tested now, and the runtime plugs in later
without either side guessing. Same arrangement as `FaceEmbedder` in
packages/face-identity.

THE PART THAT MATTERS: WHAT HAPPENS WHEN THE EMBEDDER IS NOT THERE

Today it is not there. `google/siglip2-so400m-patch14-384` publishes safetensors
and no ONNX, so `models/registry.json` cannot fetch it and the load gate reports
`WEIGHTS_MISSING` (issue #79). The safety head is a matrix over a vector that
does not exist yet.

The tempting thing to do with a missing model is nothing -- return no scores,
let the caller carry on. That is precisely the defect
`contracts/schemas/safety-clearance.schema.json` was written to prevent, and the
one this project has already shipped once (#18, a load gate that permitted
weights whose hash had never been computed):

    "A safety check that silently no-ops when its model is missing is worse
     than no check, because everything downstream reads the absence as a pass."

So absence is not silence here. `EmbedderUnavailable` carries an
`indeterminate_reason` drawn from the contract's own enum, the classifier turns
it into an `indeterminate` verdict on every affected item, and one indeterminate
item denies the entire publication. A missing embedder therefore blocks every
print, every share and every contact sheet -- loudly, with a reason a human can
act on, and with no flag anywhere that turns it back into a pass.

WHY THE PROTOCOL TAKES EVIDENCE IDS AND NOT PIXELS

`ItemVerdict.evidence_id` is "the PROXY the classifier actually saw", not the
media id, because a proxy can be regenerated -- a better decoder, a corrected
orientation, a different size -- and a verdict about the old proxy is not
evidence about the new one. Keying the interface on the evidence id rather than
on the media id makes staleness a lookup miss instead of a judgement call.

A NOTE ON RESOLUTION, WHICH IS AN OPEN PROBLEM AND NOT A DETAIL

The gate scores the low-resolution proxy -- the same proxy the contact sheet is
built from, deliberately low-res for privacy. Low resolution is exactly where
small-in-frame explicit content stops being legible to the embedding. The
privacy design and the safety design pull against each other at the single
sharpest boundary in the system, the one where content leaves the device.
`docs/safety-classifier-decision.md` §8.2 records this and does not solve it;
`EmbeddingSource.pixel_height` is carried here so that a future gate can at
least REFUSE a proxy below a floor rather than score it and call the result a
clearance.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

__all__ = [
    "EMBEDDING_DIMENSIONS",
    "EMBEDDING_SPACE",
    "AbsentEmbedder",
    "EmbedderUnavailable",
    "ImageEmbedder",
    "StaticEmbedder",
]

#: The contract VectorSpace this head is defined over. A vector from any other
#: space is a vector of the same length and no shared meaning; multiplying the
#: head by one produces three numbers in range and no information.
EMBEDDING_SPACE = "siglip2_so400m_1152"

#: Width of that space. Pinned rather than inferred from whatever arrives,
#: because inferring it means a 768-d `siglip2_base_768` vector would be
#: silently accepted by a head that has 1152 columns only if someone remembered
#: to check.
EMBEDDING_DIMENSIONS = 1152

#: The subset of `ItemVerdict.indeterminate_reason` an embedder may legitimately
#: report. Constrained to the contract's enum so a reason invented here cannot
#: reach a manifest and fail validation at the far end, where the diagnosis is
#: harder.
_EMBEDDER_REASONS = frozenset(
    {
        "no_result",
        "model_unavailable",
        "model_unloadable",
        "load_gate_denied",
        "config_digest_mismatch",
        "inference_error",
        "inference_timeout",
        "evidence_stale",
    }
)


class EmbedderUnavailable(RuntimeError):
    """No embedding, and the contract's word for why.

    An exception rather than a `None` return. A `None` is something a caller can
    forget to check, and the thing they would then do with it is proceed. The
    only correct handling of this is to mark items indeterminate, and an
    exception is what makes forgetting look like a crash instead of a pass.
    """

    def __init__(self, reason: str, detail: str) -> None:
        if reason not in _EMBEDDER_REASONS:
            raise ValueError(
                f"{reason!r} is not an ItemVerdict.indeterminate_reason an embedder "
                f"may report; the contract's set is {sorted(_EMBEDDER_REASONS)}"
            )
        super().__init__(f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail


@dataclass(frozen=True)
class EmbeddingSource:
    """One vector plus the provenance a verdict has to be able to cite."""

    evidence_id: str
    vector: tuple[float, ...]
    #: Height in pixels of the proxy the vector was computed from, or None when
    #: the producer does not report it. See the module docstring: this is here
    #: so a resolution floor can be enforced later without a contract change.
    pixel_height: int | None = None


@runtime_checkable
class ImageEmbedder(Protocol):
    """What the head needs from `workers/ml-runtime`.

    `space` and `dimensions` are attributes rather than a method because they
    are properties of the loaded model, and a caller must be able to reject a
    mismatched embedder BEFORE asking it for anything.
    """

    space: str
    dimensions: int

    def embed(self, evidence_ids: Sequence[str]) -> Mapping[str, Sequence[float]]:
        """Vectors for these proxies, keyed by evidence id.

        Raises `EmbedderUnavailable` when the model cannot run at all. A
        per-item miss is reported by OMITTING the key, not by raising: one
        photograph whose proxy has been deleted is a different situation from a
        model that will not load, and the classifier records them as different
        `indeterminate_reason`s (`no_result` against `model_unavailable`).
        """
        ...


class AbsentEmbedder:
    """The embedder the product wires today, and the reason the gate blocks.

    SigLIP 2 has no ONNX export in this registry (#79), so there is no image
    embedder to hand the head. This class is what stands in its place, and it is
    deliberately not a null object that returns empty results -- it raises, with
    a contract reason, every time.

    Constructing it is a decision someone has to write down: `detail` is
    required and ends up in the manifest's `denied_reason`, so the person who
    hits the block reads "siglip2-so400m-384 has no ONNX export yet (issue #79)"
    rather than "safety check failed".

    DO NOT ADD A MODE WHERE THIS RETURNS SCORES. There is no such thing as a
    stand-in embedding for a safety classifier: a fabricated vector produces
    three numbers that look exactly like a measurement, and the entire failure
    mode this package exists to prevent is a number that looks like a safety
    guarantee.
    """

    space = EMBEDDING_SPACE
    dimensions = EMBEDDING_DIMENSIONS

    def __init__(self, *, reason: str = "model_unavailable", detail: str) -> None:
        if not detail or not detail.strip():
            raise ValueError(
                "an absent embedder must say WHY; 'safety check failed' is not "
                "something a user or an engineer can act on"
            )
        if reason not in _EMBEDDER_REASONS:
            raise ValueError(f"{reason!r} is not a contract indeterminate_reason")
        self.reason = reason
        self.detail = detail

    def embed(self, evidence_ids: Sequence[str]) -> Mapping[str, Sequence[float]]:
        raise EmbedderUnavailable(self.reason, self.detail)


class StaticEmbedder:
    """A dict with the `ImageEmbedder` shape, for tests and for offline fitting.

    NOT a stand-in for a model. It serves vectors somebody already computed and
    handed it; it never invents one. Calibration fitting and the eval harness
    both work from a table of precomputed embeddings, and this is that table
    behind the interface the classifier expects.

    A vector of the wrong width raises at construction rather than at
    multiplication time, so the error names the producer instead of the head.
    """

    def __init__(
        self,
        vectors: Mapping[str, Sequence[float]],
        *,
        space: str = EMBEDDING_SPACE,
        dimensions: int = EMBEDDING_DIMENSIONS,
    ) -> None:
        self.space = space
        self.dimensions = dimensions
        table: dict[str, tuple[float, ...]] = {}
        for evidence_id, vector in vectors.items():
            row = tuple(float(value) for value in vector)
            if len(row) != dimensions:
                raise ValueError(
                    f"{evidence_id}: {len(row)}-d vector in a {dimensions}-d space"
                )
            for value in row:
                if value != value or value in (float("inf"), float("-inf")):
                    raise ValueError(
                        f"{evidence_id}: a non-finite component poisons every "
                        "comparison it enters, silently -- NaN >= threshold and "
                        "NaN < threshold are both False"
                    )
            table[evidence_id] = row
        self._table = table

    def embed(self, evidence_ids: Sequence[str]) -> Mapping[str, Sequence[float]]:
        return {
            evidence_id: self._table[evidence_id]
            for evidence_id in evidence_ids
            if evidence_id in self._table
        }
