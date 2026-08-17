"""The embedding interface, and the arithmetic every later decision rests on.

ArcFace is not available in this repository. Nothing here calls a model; this
module defines the *shape* of the thing that will, and the value type its output
has to be, so that clustering and assignment can be built and tested now and the
runtime can be plugged in later without either side guessing.

WHY THIS FILE IS STRICT ABOUT THINGS THAT LOOK LIKE PLUMBING

Every failure in face identity is silent. A wrong embedding does not raise, it
produces a plausible number, which produces a plausible cluster, which produces
a plausible name on a face in a printed book. There is no exception anywhere
downstream to catch. So the checks have to live at the boundary, here:

1.  ALIGNMENT TEMPLATE MISMATCH.
    face-record.schema.json says it in as many words: "yunet_5 and
    insightface_5 are both five points and are NOT interchangeable: feeding one
    to an alignment template built for the other produces a plausible warp and a
    wrong embedding, which is the worst failure mode in this system because
    nothing downstream can detect it." So an embedder declares the scheme it was
    trained against, an aligned face carries the scheme it was warped with, and
    a mismatch raises. It is five lines of code against a class of bug that is
    otherwise undetectable.

2.  SPACE MISMATCH.
    common.schema.json, VectorSpace: "Two vectors may only be compared when
    their space matches exactly, including the model version that produced
    them." A cosine distance between an arcface_buffalo_l_512 vector and an
    adaface_ir101_512 vector is a number in [0,2] with no meaning. Comparing
    across spaces raises rather than returning that number.

3.  NORMALISATION.
    The contract says every space stores L2-normalised vectors, which is what
    makes cosine distance a dot product. The constructor VERIFIES rather than
    normalises: silently normalising a vector of norm 20 would accept a producer
    that is emitting raw logits, and every distance computed from it would be
    fine-looking and wrong. Rounding drift is a different thing from a different
    convention, so drift inside `NORM_TOLERANCE` is corrected exactly (and the
    corrected values are what get stored, so distances are reproducible) and
    anything outside it raises. `from_raw` exists for callers who legitimately
    hold an unnormalised vector and are saying so.

4.  NaN AND ZERO.
    A NaN component poisons every comparison it enters: `nan >= threshold` is
    False and `nan < threshold` is also False, so a NaN slides through whichever
    branch was written as the negative. A zero vector has no direction, so its
    cosine distance to everything is 1.0 and it lands mid-library. Both raise at
    construction, which is the only place they can still be attributed to their
    producer.

DETERMINISM

`math.fsum` is used for every dot product and every centroid component. It is
exactly rounded, so the result does not depend on summation order. That matters
more than it sounds: without it, a distance can differ in the last bit between
two runs that batched the same faces differently, and a face sitting exactly on
a threshold flips. Same library in, same identities out is a product promise
(CLAUDE.md hard rule 3), and it is made or broken at this level.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

__all__ = [
    "AlignedFace",
    "EmbeddingError",
    "FACE_EMBEDDING_SPACES",
    "FaceEmbedder",
    "FaceEmbedding",
    "LANDMARK_SCHEMES",
    "NORM_TOLERANCE",
    "AlignmentMismatch",
    "SpaceMismatch",
    "centroid",
    "cosine_distance",
    "cosine_similarity",
]


_BLAKE3_RE = re.compile(r"^[0-9a-f]{64}$")

# Face recognition spaces declared in common.schema.json (VectorSpace) and the
# dimensionality each one is defined at. A vector whose length disagrees with
# its declared space is not a vector in that space, whatever the producer says.
FACE_EMBEDDING_SPACES: dict[str, int] = {
    "arcface_buffalo_l_512": 512,
    "adaface_ir101_512": 512,
}

# Landmark schemes from face-record.schema.json (Landmarks.scheme).
LANDMARK_SCHEMES: frozenset[str] = frozenset(
    {"insightface_5", "insightface_106", "mediapipe_468", "yunet_5"}
)

# How far off unit norm a vector may be and still be treated as "normalised,
# with rounding". Chosen for fp16 execution: accumulating 512 fp16 products
# drifts on the order of 1e-4, so 1e-3 leaves headroom without being wide enough
# to admit a vector that was never normalised (those have norms in the tens).
NORM_TOLERANCE = 1e-3


class EmbeddingError(Exception):
    """Anything wrong with an embedding, its space, or its alignment."""


class SpaceMismatch(EmbeddingError):
    """Two vectors from different spaces were compared."""


class AlignmentMismatch(EmbeddingError):
    """A face was warped with a landmark scheme the embedder was not trained on."""


# ---------------------------------------------------------------------------
# The value type
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FaceEmbedding:
    """One L2-normalised recognition vector, tied to the face it came from.

    Frozen and hashable so it can be a dict key and cannot be mutated between
    the moment a distance is computed and the moment a decision is made from it.
    """

    face_id: str
    space: str
    values: tuple[float, ...]

    def __init__(self, face_id: str, space: str, values: Sequence[float]) -> None:
        if not isinstance(face_id, str) or not _BLAKE3_RE.match(face_id):
            raise EmbeddingError(
                f"face_id must be a lowercase 64-hex BLAKE3 digest, got {face_id!r}"
            )
        if space not in FACE_EMBEDDING_SPACES:
            raise EmbeddingError(
                f"{face_id}: {space!r} is not a face recognition space; known spaces "
                f"are {sorted(FACE_EMBEDDING_SPACES)}"
            )
        components = tuple(float(v) for v in values)
        expected = FACE_EMBEDDING_SPACES[space]
        if len(components) != expected:
            raise EmbeddingError(
                f"{face_id}: {space} is {expected}-dimensional, got "
                f"{len(components)} components"
            )
        for index, value in enumerate(components):
            if not math.isfinite(value):
                raise EmbeddingError(
                    f"{face_id}: component {index} is {value!r}; a non-finite "
                    "component makes every comparison this vector enters return "
                    "False, in whichever direction it was written"
                )
        norm = _norm(components)
        if norm == 0.0:
            raise EmbeddingError(
                f"{face_id}: the zero vector has no direction, so its cosine "
                "distance to every face in the library is exactly 1.0"
            )
        if abs(norm - 1.0) > NORM_TOLERANCE:
            raise EmbeddingError(
                f"{face_id}: vector has L2 norm {norm:.6g}, which is further than "
                f"{NORM_TOLERANCE} from unit. The contract stores normalised "
                "vectors; this is a different producer convention rather than "
                "rounding drift. Use FaceEmbedding.from_raw if that is deliberate"
            )
        # Correct the drift exactly, so two runs that differ only in accumulation
        # order store identical components and therefore identical distances.
        object.__setattr__(self, "face_id", face_id)
        object.__setattr__(self, "space", space)
        object.__setattr__(self, "values", tuple(v / norm for v in components))

    @classmethod
    def from_raw(
        cls, face_id: str, space: str, values: Sequence[float]
    ) -> "FaceEmbedding":
        """Normalise an arbitrary finite non-zero vector, deliberately.

        The visible alternative to the constructor's strictness. A caller that
        genuinely holds unnormalised output says so here, in one place a reviewer
        can grep for, rather than by having the constructor quietly fix it for
        everybody including the producer that is broken.
        """
        components = tuple(float(v) for v in values)
        for index, value in enumerate(components):
            if not math.isfinite(value):
                raise EmbeddingError(
                    f"{face_id}: component {index} is {value!r}; from_raw "
                    "normalises, it does not repair"
                )
        norm = _norm(components)
        if norm == 0.0:
            raise EmbeddingError(f"{face_id}: cannot normalise the zero vector")
        return cls(face_id, space, tuple(v / norm for v in components))

    @property
    def dimensions(self) -> int:
        return len(self.values)


def _norm(values: Sequence[float]) -> float:
    return math.sqrt(math.fsum(v * v for v in values))


# ---------------------------------------------------------------------------
# Distance
# ---------------------------------------------------------------------------


def cosine_similarity(a: FaceEmbedding, b: FaceEmbedding) -> float:
    """Dot product of two unit vectors, clamped to [-1, 1].

    The clamp is not cosmetic. Floating point routinely returns 1.0000000000000002
    for a vector against itself, and 1 - that is a negative distance, which then
    compares false against every `>= 0` guard written downstream and true against
    every `< threshold` one. Clamping here means no caller has to know.
    """
    if a.space != b.space:
        raise SpaceMismatch(
            f"cannot compare {a.face_id} in {a.space} with {b.face_id} in "
            f"{b.space}: a distance across two spaces is a number without a meaning"
        )
    dot = math.fsum(x * y for x, y in zip(a.values, b.values, strict=True))
    if dot > 1.0:
        return 1.0
    if dot < -1.0:
        return -1.0
    return dot


def cosine_distance(a: FaceEmbedding, b: FaceEmbedding) -> float:
    """1 - cosine similarity, in [0, 2]. Identical vectors are exactly 0.0."""
    return 1.0 - cosine_similarity(a, b)


def centroid(embeddings: Iterable[FaceEmbedding]) -> FaceEmbedding:
    """The spherical mean of a set of unit vectors, as a unit vector.

    The arithmetic mean of unit vectors is NOT a unit vector -- its norm shrinks
    as the set spreads out -- so it is renormalised. Skipping that step makes
    every distance-to-centroid smaller for tight clusters and larger for loose
    ones by a factor nobody has written down, which is exactly the sort of
    plausible number this codebase keeps producing.

    The centroid borrows the face_id of its lowest-id member so it is a valid
    FaceEmbedding and so the borrowing is deterministic; it is a summary, not a
    face, and callers should not read identity from it.
    """
    members = sorted(embeddings, key=lambda e: e.face_id)
    if not members:
        raise EmbeddingError("the centroid of an empty set is undefined")
    space = members[0].space
    for member in members:
        if member.space != space:
            raise SpaceMismatch(
                f"centroid over mixed spaces: {space} and {member.space}"
            )
    dimensions = members[0].dimensions
    mean = tuple(
        math.fsum(member.values[i] for member in members) / len(members)
        for i in range(dimensions)
    )
    if _norm(mean) == 0.0:
        # Only reachable for an exactly antipodal set, which cannot occur inside
        # a cluster whose diameter is bounded below 2.0. Stated rather than left
        # to from_raw's error, because the cause is different: this is not a bad
        # vector, it is a set with no meaningful centre.
        raise EmbeddingError(
            "these vectors cancel exactly; the set has no centre and no cluster "
            "produced by this package can contain it"
        )
    return FaceEmbedding.from_raw(members[0].face_id, space, mean)


# ---------------------------------------------------------------------------
# The runtime interface
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AlignedFace:
    """A face crop warped to an embedder's input template, ready to embed.

    Carries the landmark scheme the warp was computed from precisely so the
    embedder can refuse it. This package never performs the warp -- that is
    ml-runtime's job (AGENTS.md) -- but it defines what has to travel with it.
    """

    face_id: str
    landmark_scheme: str
    # Whatever the runtime hands over: a tensor handle, a path, raw bytes. This
    # package never looks inside it, which is why it is typed `object`.
    payload: object = None

    def __post_init__(self) -> None:
        if not _BLAKE3_RE.match(self.face_id):
            raise EmbeddingError(
                f"face_id must be a lowercase 64-hex BLAKE3 digest, got "
                f"{self.face_id!r}"
            )
        if self.landmark_scheme not in LANDMARK_SCHEMES:
            raise EmbeddingError(
                f"{self.face_id}: unknown landmark scheme "
                f"{self.landmark_scheme!r}; known schemes are "
                f"{sorted(LANDMARK_SCHEMES)}"
            )


@runtime_checkable
class FaceEmbedder(Protocol):
    """What clustering and assignment require of a recognition model.

    Deliberately small. Everything about batching, execution providers and model
    loading lives in workers/ml-runtime; the intelligence layer only needs to
    know the space, the alignment contract, and that some faces come back
    without a vector.
    """

    @property
    def space(self) -> str:
        """The VectorSpace this embedder writes into."""

    @property
    def required_landmark_scheme(self) -> str:
        """The alignment template the weights were trained against."""

    def embed(
        self, faces: Sequence[AlignedFace]
    ) -> tuple[FaceEmbedding | None, ...]:
        """One result per input face, in order.

        `None` means the model declined to embed: too small, too blurred, too
        occluded. That is a real and common answer, and it is NOT an error --
        face-record.schema.json keeps such a face because "an unembeddable face
        is still worth recording, because it counts toward 'how many people are
        in this photo'". Callers must handle None; they must never fill it in.
        """


def check_alignment(embedder: FaceEmbedder, face: AlignedFace) -> None:
    """Raise unless the warp that produced `face` matches what `embedder` needs.

    Call this before every embed. The failure it prevents leaves no trace: a
    yunet_5 warp fed to an insightface_5 template yields a normalised 512-vector
    that clusters, scores, and gets a person's name attached to it.
    """
    if face.landmark_scheme != embedder.required_landmark_scheme:
        raise AlignmentMismatch(
            f"{face.face_id}: aligned with {face.landmark_scheme} but this "
            f"embedder requires {embedder.required_landmark_scheme}. The two "
            "produce a plausible warp and a wrong embedding, and nothing "
            "downstream can detect it"
        )
