"""Conversion to and from the contract, and the face boxes the album needs.

Two jobs, and the second one is the reason this package matters to anything
that ships.

1.  `to_face_record` writes a FaceRecord. The only interesting line in it is the
    one that does NOT take an argument:

        "eligible_for_automated_output": assignment.eligible_for_automated_output

    The contract's gate is populated from the property, never from a parameter,
    so there is no call site anywhere that can write `True` into that field. If
    somebody adds an `eligible=` keyword to this function, every test in
    tests/test_records.py that asserts the invariant fails.

2.  `face_boxes_for_layout` hands album-engine the face rectangles it needs for
    the print validator's trim-zone and gutter checks.

    THIS IS THE PART THAT WAS MISSING AND IT IS NOT AN IDENTITY FEATURE.

    services/pipeline currently plans albums with an empty face set, so
    `validator.face_in_trim_zone` passes vacuously: `face_count: 0` on every
    placement, no face can be in the trim zone, and the gate that CLAUDE.md rule
    5 rests on cannot fail because there is nothing to gate. album.py says so in
    a comment and calls it "honest ... and is NOT the same as 'checked and
    safe'". It is honest and it is also a print defect waiting for its first
    real library.

    The fix does not need identity at all. Face SAFETY is about where a face is
    on the page; face IDENTITY is about whose it is. A guillotine does not care
    whose face it cuts through. So this function returns EVERY detected face
    above the detector floor -- named, unnamed, ineligible, a child with no
    consent, a stranger in the background -- and it deliberately does not accept
    an assignment argument, because there is no version of "exclude this face
    from the safety check because we could not identify it" that is not a bug.

    Wiring identity into safety would take a privacy control (a child whose
    parent has not consented to labeling) and turn it into a physical defect (a
    child's face cut in half by the trim). Those are separate systems on
    purpose, and this signature is where that separation is enforced.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from .clustering import ClusterMembership
from .identity import (
    Assignment,
    MinorStatus,
    PersonAssignment,
    ReviewReason,
    ThresholdProfile,
)

__all__ = [
    "Detection",
    "DetectedFace",
    "LayoutFaceBox",
    "ModelRef",
    "NormalizedBox",
    "RecordError",
    "SUBJECT_DETECTION_FLOOR",
    "face_boxes_for_layout",
    "face_context_from_record",
    "to_face_record",
]


_BLAKE3_RE = re.compile(r"^[0-9a-f]{64}$")
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")

# A detection this weak is more likely to be a pattern in a curtain than a face,
# and treating it as a face on a page would fail the print validator for a
# rectangle nobody would ever see. It is the same floor
# services/pipeline/stages/base.py already applies to the MediaRecord face
# summary (face_score_floor = 0.60), kept identical on purpose: a face that
# counts toward "how many people are in this photo" and a face that the trim
# check protects must be the same set, or the two numbers disagree and the
# validator's own contradiction checks fire on a difference of convention.
SUBJECT_DETECTION_FLOOR = 0.60

SCHEMA_VERSION = "v0"


class RecordError(Exception):
    """A record that would not satisfy the contract."""


@dataclass(frozen=True, slots=True)
class NormalizedBox:
    x: float
    y: float
    w: float
    h: float

    def __post_init__(self) -> None:
        for name in ("x", "y", "w", "h"):
            value = getattr(self, name)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
            ):
                raise RecordError(f"box.{name} must be a finite number, got {value!r}")
        if not 0.0 <= self.x <= 1.0 or not 0.0 <= self.y <= 1.0:
            raise RecordError("box origin must be inside the normalised frame")
        if not 0.0 < self.w <= 1.0 or not 0.0 < self.h <= 1.0:
            raise RecordError("box must have positive width and height within [0,1]")

    def to_dict(self) -> dict[str, float]:
        return {"x": self.x, "y": self.y, "w": self.w, "h": self.h, "rotation_deg": 0}


@dataclass(frozen=True, slots=True)
class ModelRef:
    model_id: str
    version: str
    weights_blake3: str | None = None
    runtime: str | None = None
    precision: str | None = None
    config_blake3: str | None = None

    def __post_init__(self) -> None:
        if not _SLUG_RE.match(self.model_id or ""):
            raise RecordError(f"model_id must be a Slug, got {self.model_id!r}")
        if not self.version:
            raise RecordError("model version must be non-empty")
        for name in ("weights_blake3", "config_blake3"):
            value = getattr(self, name)
            if value is not None and not _BLAKE3_RE.match(value):
                raise RecordError(f"{name} must be a BLAKE3 digest or None")

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_id": self.model_id,
            "version": self.version,
            "weights_blake3": self.weights_blake3,
            "runtime": self.runtime,
            "precision": self.precision,
            "config_blake3": self.config_blake3,
        }


@dataclass(frozen=True, slots=True)
class Detection:
    bbox: NormalizedBox
    detection_score: float
    detector: ModelRef
    detected_on: str = "thumbnail_512"
    face_area_ratio: float | None = None

    def __post_init__(self) -> None:
        if (
            isinstance(self.detection_score, bool)
            or not isinstance(self.detection_score, (int, float))
            or not math.isfinite(self.detection_score)
            or not 0.0 <= self.detection_score <= 1.0
        ):
            raise RecordError("detection_score must be a Confidence in [0,1]")
        if self.detected_on not in {
            "thumbnail_512",
            "preview_2048",
            "video_proxy_480p",
            "original",
        }:
            raise RecordError(f"unknown detected_on: {self.detected_on!r}")
        if self.face_area_ratio is not None and not 0.0 <= self.face_area_ratio <= 1.0:
            raise RecordError("face_area_ratio must be a Unit in [0,1]")


@dataclass(frozen=True, slots=True)
class DetectedFace:
    """Everything about a face that did not come from an identity decision."""

    face_id: str
    media_id: str
    detection: Detection
    embedding_ref: Mapping[str, Any] | None = None
    landmarks: Mapping[str, Any] | None = None
    attributes: Mapping[str, Any] | None = None
    track: Mapping[str, Any] | None = None
    frame_time: Mapping[str, Any] | None = None
    excluded_from_sharing: bool = False

    def __post_init__(self) -> None:
        for name in ("face_id", "media_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not _BLAKE3_RE.match(value):
                raise RecordError(
                    f"{name} must be a lowercase 64-hex BLAKE3 digest, got {value!r}"
                )


# ---------------------------------------------------------------------------
# Contract emission
# ---------------------------------------------------------------------------


def to_face_record(
    face: DetectedFace,
    assignment: PersonAssignment,
    *,
    membership: ClusterMembership | None = None,
    created_at: str,
    updated_at: str | None = None,
) -> dict[str, Any]:
    """Build one contract-shaped FaceRecord.

    Refuses when `face` and `assignment` describe different faces. That pairing
    is done by the caller from two different tables, and getting it wrong writes
    one person's identity onto another person's box with every field individually
    valid.
    """
    if face.face_id != assignment.face_id:
        raise RecordError(
            f"detection is for {face.face_id} but the assignment is for "
            f"{assignment.face_id}"
        )
    if membership is not None and membership.face_id != face.face_id:
        raise RecordError(
            f"cluster membership is for {membership.face_id}, not {face.face_id}"
        )

    record: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "face_id": face.face_id,
        "media_id": face.media_id,
        "frame_time": dict(face.frame_time) if face.frame_time else None,
        "track": dict(face.track) if face.track else None,
        "detection": {
            "bbox": face.detection.bbox.to_dict(),
            "detection_score": face.detection.detection_score,
            "detector": face.detection.detector.to_dict(),
            "detected_on": face.detection.detected_on,
        },
        "landmarks": dict(face.landmarks) if face.landmarks else None,
        "embedding": dict(face.embedding_ref) if face.embedding_ref else None,
        "attributes": dict(face.attributes) if face.attributes else None,
        "cluster": _cluster_block(membership),
        "identity": _identity_block(assignment),
        "sensitive": _sensitive_block(assignment, face),
        "model_runs": [],
        "created_at": created_at,
        "updated_at": updated_at or created_at,
    }
    if face.detection.face_area_ratio is not None:
        record["detection"]["face_area_ratio"] = face.detection.face_area_ratio
    return record


def _identity_block(assignment: PersonAssignment) -> dict[str, Any]:
    return {
        "person_id": assignment.person_id,
        "assignment": assignment.assignment.value,
        "confidence": assignment.confidence,
        "threshold_profile": assignment.threshold_profile.value,
        "threshold_used": assignment.threshold_used,
        # THE GATE. Read from the property; there is no parameter for it.
        "eligible_for_automated_output": assignment.eligible_for_automated_output,
        "candidates": [
            {"person_id": c.person_id, "confidence": c.confidence}
            for c in assignment.candidates
        ],
        # Four of this package's review reasons have no equivalent in the
        # contract enum (minor consent, unresolved age, uncalibrated threshold,
        # no embedding). They serialise as null rather than as the nearest
        # available value: reporting `near_boundary` for a face held back by a
        # missing consent would be a plausible, wrong explanation, and this
        # codebase already has enough of those. The real reason stays on the
        # review item. Widening the enum needs Codex's sign-off on contracts/.
        "review_reason": (
            assignment.review_reason.contract_value
            if assignment.review_reason is not None
            else None
        ),
        "decided_by": (
            assignment.decided_by.value if assignment.decided_by is not None else None
        ),
        "decided_at": assignment.decided_at,
    }


def _cluster_block(membership: ClusterMembership | None) -> dict[str, Any] | None:
    if membership is None:
        return None
    return {
        "cluster_id": membership.cluster_id,
        "method": membership.method,
        "membership_strength": membership.membership_strength,
        "is_noise": membership.is_noise,
        "distance_to_centroid": membership.distance_to_centroid,
        "clustering_run_id": membership.clustering_run_id,
    }


def _sensitive_block(
    assignment: PersonAssignment, face: DetectedFace
) -> dict[str, Any]:
    consent = assignment.labeling_consent
    # The schema requires a labeling_consent OBJECT whenever minor_status is
    # confirmed_minor, whether or not a name was written. A confirmed minor with
    # no consent therefore cannot be serialised at all, and that is the schema
    # working as intended: rather than emit a record the contract rejects, this
    # raises where the missing consent can still be attributed to the face that
    # needs it.
    if assignment.minor_status is MinorStatus.CONFIRMED_MINOR and consent is None:
        raise RecordError(
            f"{face.face_id}: confirmed_minor requires a labeling_consent object "
            "before the record can be written (face-record.schema.json). The face "
            "may be detected and counted; it may not be serialised as a named or "
            "nameable child without the ledger entry"
        )
    return {
        "minor_status": assignment.minor_status.value,
        "labeling_consent": (
            {
                "ledger_entry_id": consent.ledger_entry_id,
                "scope": consent.scope,
                "granted_at": consent.granted_at,
                "expires_at": consent.expires_at,
                "revoked_at": consent.revoked_at,
            }
            if consent is not None
            else None
        ),
        "excluded_from_sharing": face.excluded_from_sharing,
    }


def face_context_from_record(record: Mapping[str, Any]) -> dict[str, Any]:
    """Pull the fields assignment reasons about back out of a FaceRecord.

    Returns a plain dict rather than a FaceContext because the embedding lives
    in the vector index, not in the record -- the caller has to fetch it and
    only the caller knows how. Handing back a FaceContext with `embedding=None`
    would look like "this face has no embedding", which is a completely
    different and very consequential statement.
    """
    identity = record.get("identity") or {}
    sensitive = record.get("sensitive") or {}
    attributes = record.get("attributes") or {}
    cluster = record.get("cluster") or {}
    quality = (attributes.get("quality") or {}).get("value")
    return {
        "face_id": record["face_id"],
        "media_id": record["media_id"],
        "embedding_ref": record.get("embedding"),
        "quality": quality,
        "yaw_deg": attributes.get("yaw_deg"),
        "minor_status": MinorStatus(sensitive.get("minor_status", "unknown")),
        "cluster_is_noise": bool(cluster.get("is_noise", False)),
        "assignment": Assignment(identity.get("assignment", "unassigned")),
        "threshold_profile": ThresholdProfile(
            identity.get("threshold_profile", "automated_output")
        ),
        "review_reason": (
            ReviewReason(identity["review_reason"])
            if identity.get("review_reason")
            else None
        ),
    }


# ---------------------------------------------------------------------------
# Face boxes for the album
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LayoutFaceBox:
    """A face rectangle for album-engine's layout and the print validator.

    Shaped to construct `memory_engine_album.layout.Face` without this package
    importing album-engine: the two are separate installables and a dependency
    edge between them would make the album engine's tests depend on face
    identity, which is exactly backwards.
    """

    face_id: str
    x: float
    y: float
    w: float
    h: float
    is_subject: bool


def face_boxes_for_layout(
    faces: Iterable[DetectedFace],
    *,
    detection_floor: float = SUBJECT_DETECTION_FLOOR,
) -> tuple[LayoutFaceBox, ...]:
    """Every face on this image that the trim-zone check must protect.

    Note the signature: no assignments, no eligibility, no person ids, no way to
    pass any. See the module docstring. The only filter is the detector's own
    confidence, because a box that is not a face is not a safety concern.

    Ordered by face_id so a layout run is reproducible.
    """
    if not math.isfinite(detection_floor) or not 0.0 <= detection_floor <= 1.0:
        raise RecordError("detection_floor must be a Confidence in [0,1]")
    boxes = [
        LayoutFaceBox(
            face_id=face.face_id,
            x=face.detection.bbox.x,
            y=face.detection.bbox.y,
            w=face.detection.bbox.w,
            h=face.detection.bbox.h,
            # `is_subject` steers which faces a crop is aligned to keep in
            # frame. It is NOT a safety filter: album-engine's face-safety
            # report counts every Face it is given, subject or not. Every face
            # over the detector floor is a subject here because there is no
            # evidence at this layer for ranking one stranger above another --
            # that judgement belongs to selection, which has the fused scores.
            is_subject=True,
        )
        for face in faces
        if face.detection.detection_score >= detection_floor
    ]
    boxes.sort(key=lambda box: box.face_id)
    return tuple(boxes)
