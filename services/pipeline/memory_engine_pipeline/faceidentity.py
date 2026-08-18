"""The pipeline's one set of face-identity settings, and the calls that use them.

Two stages touch identity -- `analysis` writes a face the moment it is detected,
`faces` re-decides every face once the whole library is known -- and both have
to use the same thresholds, the same calibrator and the same gallery, or the
record written by one contradicts the record written by the other while each is
individually plausible. So they are here, once.

WHAT THIS MODULE DOES NOT DO

It does not lower a bar. `packages/face-identity` refuses to emit an eligible
assignment unless its calibrator reports `calibrated = True`, no calibration
exists for these weights, and the correct behaviour of this pipeline today is
therefore:

    N faces detected, 0 eligible for automated output, N awaiting review.

That is not a degraded mode to be worked around; it is the product promise.
CLAUDE.md rule 5 -- "a wrong person in a family album is a catastrophic
failure" -- is what makes an uncalibrated automated path unacceptable, and the
only honest way to open it is to measure the precision (see
packages/face-identity/README.md, "the eval that would measure precision").

THE GALLERY IS EMPTY, AND THAT IS A FACT ABOUT THE PRODUCT, NOT A STUB

A `Person` is created by a human labelling a face, and `Person.enrolled` holds
only embeddings of faces a human confirmed. Nothing in this repository has a
labelling surface yet -- apps/desktop's person screen is not built -- so no
Person exists and no face is enrolled. A gallery loaded from the database would
therefore be empty in a different, more confusing way: people with no enrolled
faces, which `PersonGallery.similarity` correctly scores as None. Rather than
dress that up, this module returns the empty gallery and the stages report
`gallery_people: 0` in their counts so a reader can see WHY nothing was named.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from memory_engine_face.clustering import (  # noqa: PLC0415
    ClusteringResult,
    FaceObservation,
    cluster_faces,
)
from memory_engine_face.embeddings import FaceEmbedding
from memory_engine_face.identity import (
    FaceContext,
    MinorStatus,
    PersonAssignment,
    PersonGallery,
    Thresholds,
    UncalibratedSimilarity,
    assign_identities,
)
from memory_engine_face.records import DetectedFace, to_face_record

__all__ = [
    "CLUSTERING_METHOD_RUN_PREFIX",
    "FACE_SPACE",
    "MERGE_THRESHOLD",
    "THRESHOLDS",
    "assign",
    "assignable_minor_status",
    "calibrator",
    "cluster",
    "embedding_of",
    "gallery",
    "record_for",
    "restore_human_identity",
    "restore_sensitive",
]

# Cosine distance. The value `packages/eval-harness/gates/
# face-clustering-synthetic.gate.json` was measured at, and the only one any
# number in this repository describes. Changing it invalidates that gate, which
# is why it is a named constant here rather than an argument with a default:
# the two have to move together.
MERGE_THRESHOLD = 0.50

FACE_SPACE = "arcface_buffalo_l_512"

CLUSTERING_METHOD_RUN_PREFIX = "pipeline-faces"

# Defaults. Stated explicitly rather than taken implicitly so that a reader can
# see there is no local override: `Thresholds` refuses an automated_output below
# its own floor, so this cannot be quietly relaxed here even by editing it.
THRESHOLDS = Thresholds()


def calibrator(space: str = FACE_SPACE) -> UncalibratedSimilarity:
    """The only calibrator this repository has: an honest, uncalibrated one.

    Swapping in a `FittedCalibrator` is what opens the automated path, and it
    cannot be constructed without a measured precision, at least 2,000 labelled
    pairs and the digest of the evaluation set. There is nowhere in this
    pipeline to pass one in, deliberately: the day that measurement exists, the
    change lands here, in a diff somebody reviews.
    """
    return UncalibratedSimilarity(space=space)


def gallery() -> PersonGallery:
    """See the module docstring: empty, because nothing enrols."""
    return PersonGallery()


def cluster(
    observations: Iterable[FaceObservation], *, run_id: str
) -> ClusteringResult:
    return cluster_faces(
        observations, merge_threshold=MERGE_THRESHOLD, run_id=run_id
    )


def assign(
    faces: Sequence[FaceContext], *, now: str
) -> tuple[PersonAssignment, ...]:
    return assign_identities(
        faces,
        gallery(),
        thresholds=THRESHOLDS,
        calibrator=calibrator(),
        now=now,
    )


def record_for(
    face: DetectedFace,
    assignment: PersonAssignment,
    *,
    membership: Any = None,
    created_at: str,
    updated_at: str | None = None,
) -> Mapping[str, Any]:
    """One contract FaceRecord. The eligibility field is never passed in."""
    return to_face_record(
        face,
        assignment,
        membership=membership,
        created_at=created_at,
        updated_at=updated_at,
    )


def embedding_of(face_id: str, values: Sequence[float], *, space: str) -> FaceEmbedding:
    """Wrap stored floats as a comparable vector, or raise saying why not.

    `FaceEmbedding` verifies the norm rather than normalising it, so a producer
    that has started emitting raw logits is caught here instead of ranking
    plausibly and wrongly forever.

    `space` is REQUIRED and has no default, and callers pass the space the
    record they are reading actually declares rather than `FACE_SPACE`. That is
    not pedantry. `FaceEmbedding` refuses to compare two vectors from different
    spaces -- common.schema.json: "Two vectors may only be compared when their
    space matches exactly" -- and stamping every vector with one constant
    disables that guard entirely: a library holding one adaface vector among
    its arcface ones clusters it against them and returns a cosine distance in
    [0,2] with no meaning, silently. Measured: before this argument existed, a
    vector moved into `adaface_ir101_512` was clustered as arcface with no
    error, no note and no count.
    """
    return FaceEmbedding(face_id, space, values)


def assignable_minor_status(sensitive: Mapping[str, Any] | None) -> MinorStatus:
    """The minor status the assignment may reason from, given a stored record.

    `confirmed_minor` is deliberately NOT passed through. The consent that a
    confirmed minor's record carries lives in the record; the assignment reads
    consent from the `PersonGallery`, which is empty because nothing enrols yet
    (see the module docstring). Feeding `confirmed_minor` to an assignment with
    no consent makes `to_face_record` raise -- correctly, per the schema -- and
    would brick every re-analysis of a library that contains one.

    Reporting `unknown` to the assignment instead is strictly conservative:
    `unknown` and `confirmed_minor`-without-consent are both ineligible for
    automated output, so no face becomes nameable that was not already. The
    stored envelope itself is not touched; `restore_sensitive` writes it back
    verbatim, so the real answer -- and its ledger entry -- survives.
    """
    status = MinorStatus((sensitive or {}).get("minor_status", "unknown"))
    return MinorStatus.UNKNOWN if status is MinorStatus.CONFIRMED_MINOR else status


def restore_sensitive(
    record: dict[str, Any], sensitive: Mapping[str, Any] | None
) -> dict[str, Any]:
    """Put a previously stored minor-safety envelope back on a rewritten record.

    A face_id is content-addressed over the detector and the box, so re-running
    the same detector rewrites the SAME face -- and rewriting it from the
    detection alone discards every answer a human gave about it. Measured
    before this existed: `--reanalyze-faces` reset `minor_status` from
    `estimated_minor` to `unknown` and `excluded_from_sharing` from true to
    false, with no event and no count. That is the silent data loss hard rule 7
    forbids, and it fails in the unsafe direction.
    """
    if sensitive:
        record["sensitive"] = dict(sensitive)
    return record


def restore_human_identity(
    record: dict[str, Any], identity: Mapping[str, Any] | None
) -> dict[str, Any]:
    """Keep an authoritative human decision when re-deriving the same face.

    Detection can update the box, landmarks and model evidence. It cannot
    revoke an answer a person already gave. A stable content-addressed
    ``face_id`` means this is the same face, so replacing ``user_confirmed`` or
    ``user_rejected`` with the model's fresh ``unassigned`` default is silent
    data loss, not re-analysis.

    ``decided_by: user`` is included as a belt-and-braces signal for records
    written by older clients whose assignment vocabulary may be narrower. The
    whole identity block is restored because the person id, eligibility gate,
    confidence provenance and decision timestamp are one atomic decision; a
    mixture of old and new fields could satisfy the schema while explaining a
    decision nobody made.
    """
    if not identity:
        return record
    if (
        identity.get("decided_by") == "user"
        or identity.get("assignment") in {"user_confirmed", "user_rejected"}
    ):
        record["identity"] = dict(identity)
    return record
