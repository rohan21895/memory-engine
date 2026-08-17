"""Record-emission tests, validated against the real contract schema.

Two things are being defended.

1.  Emitted records satisfy `contracts/schemas/face-record.schema.json`. Not a
    hand-copy of it -- the actual file, loaded from the repository, so a schema
    change that this package violates fails here rather than in render.

2.  `eligible_for_automated_output` in the emitted record equals the property
    and cannot be supplied. The parameter-shape test is the important one: it
    fails the moment somebody adds a convenience keyword.

Also covered: `face_boxes_for_layout` must NOT accept identity, because face
safety is about where a face is and identity is about whose it is. That
signature is the thing standing between a privacy control and a print defect.
"""

from __future__ import annotations

import inspect
import json
import unittest
from functools import lru_cache
from pathlib import Path

from support import (  # noqa: E402
    NOW,
    REPO_ROOT,
    axis_vector,
    embedding,
    expired_consent,
    fid,
    live_consent,
    pid,
)

from memory_engine_face.clustering import (  # noqa: E402
    FaceObservation,
    cluster_faces,
)
from memory_engine_face.identity import (  # noqa: E402
    Assignment,
    Candidate,
    DecidedBy,
    MinorStatus,
    PersonAssignment,
    ReviewReason,
)
from memory_engine_face.records import (  # noqa: E402
    SUBJECT_DETECTION_FLOOR,
    DetectedFace,
    Detection,
    ModelRef,
    NormalizedBox,
    RecordError,
    face_boxes_for_layout,
    face_context_from_record,
    to_face_record,
)

PERSON = pid("grandma")
SCHEMA_DIR = REPO_ROOT / "contracts" / "schemas"


@lru_cache(maxsize=1)
def face_record_validator():
    from jsonschema import Draft202012Validator
    from referencing import Registry, Resource

    documents = {
        path.name: json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(SCHEMA_DIR.glob("*.schema.json"))
    }
    registry = Registry().with_resources(
        [(name, Resource.from_contents(doc)) for name, doc in documents.items()]
    )
    return Draft202012Validator(
        documents["face-record.schema.json"], registry=registry
    )


def detector() -> ModelRef:
    return ModelRef(
        model_id="scrfd-10g-bnkps",
        version="1.0.0",
        weights_blake3=fid("scrfd-weights"),
        runtime="onnxruntime_coreml",
        precision="fp16",
    )


def detected(name: str = "f0", *, score: float = 0.99, **kwargs) -> DetectedFace:
    base = dict(
        face_id=fid(name),
        media_id=fid("media"),
        detection=Detection(
            bbox=NormalizedBox(x=0.4, y=0.3, w=0.1, h=0.15),
            detection_score=score,
            detector=detector(),
            detected_on="preview_2048",
            face_area_ratio=0.015,
        ),
    )
    base.update(kwargs)
    return DetectedFace(**base)


def eligible(name: str = "f0") -> PersonAssignment:
    return PersonAssignment(
        face_id=fid(name),
        assignment=Assignment.AUTO_HIGH_CONFIDENCE,
        person_id=PERSON,
        confidence=0.97,
        threshold_used=0.92,
        minor_status=MinorStatus.CONFIRMED_ADULT,
        decided_by=DecidedBy.MODEL,
        decided_at=NOW,
    )


class ContractTests(unittest.TestCase):
    def assertValid(self, record) -> None:
        errors = sorted(
            face_record_validator().iter_errors(record),
            key=lambda e: list(e.path),
        )
        self.assertEqual(
            errors,
            [],
            "; ".join(f"{list(e.path)}: {e.message}" for e in errors[:5]),
        )

    def test_an_eligible_record_validates(self) -> None:
        self.assertValid(to_face_record(detected(), eligible(), created_at=NOW))

    def test_a_queued_record_validates(self) -> None:
        queued = PersonAssignment(
            face_id=fid("f0"),
            assignment=Assignment.REVIEW_QUEUED,
            confidence=0.80,
            threshold_used=0.92,
            minor_status=MinorStatus.UNKNOWN,
            review_reason=ReviewReason.NEAR_BOUNDARY,
            candidates=(Candidate(person_id=PERSON, confidence=0.80),),
            decided_by=DecidedBy.MODEL,
            decided_at=NOW,
        )
        self.assertValid(to_face_record(detected(), queued, created_at=NOW))

    def test_a_consented_minor_record_validates(self) -> None:
        child = PersonAssignment(
            face_id=fid("f0"),
            assignment=Assignment.USER_CONFIRMED,
            person_id=PERSON,
            confidence=1.0,
            threshold_used=0.92,
            minor_status=MinorStatus.CONFIRMED_MINOR,
            labeling_consent=live_consent(),
            consent_checked_at=NOW,
            decided_by=DecidedBy.USER,
            decided_at=NOW,
        )
        record = to_face_record(
            detected(excluded_from_sharing=True), child, created_at=NOW
        )
        self.assertValid(record)
        self.assertEqual(
            record["sensitive"]["labeling_consent"]["scope"], "minor_face_labeling"
        )

    def test_a_clustered_record_validates(self) -> None:
        face_id = fid("f0")
        clustering = cluster_faces(
            [
                FaceObservation(
                    face_id=face_id, embedding=embedding(face_id, axis_vector(0))
                ),
                FaceObservation(
                    face_id=fid("f1"), embedding=embedding(fid("f1"), axis_vector(0))
                ),
            ],
            merge_threshold=0.5,
            run_id="run-1",
        )
        record = to_face_record(
            detected(),
            eligible(),
            membership=clustering.membership_of(face_id),
            created_at=NOW,
        )
        self.assertValid(record)
        self.assertEqual(record["cluster"]["method"], "agglomerative_cosine")

    def test_the_schema_refuses_a_forged_eligibility(self) -> None:
        # Proves the contract itself carries the invariant, so this package is
        # a second line of defence rather than the only one.
        record = to_face_record(
            detected(),
            PersonAssignment(
                face_id=fid("f0"),
                assignment=Assignment.REVIEW_QUEUED,
                confidence=0.5,
                minor_status=MinorStatus.UNKNOWN,
                review_reason=ReviewReason.NEAR_BOUNDARY,
            ),
            created_at=NOW,
        )
        record["identity"]["eligible_for_automated_output"] = True
        self.assertNotEqual(list(face_record_validator().iter_errors(record)), [])


class GateEmissionTests(unittest.TestCase):
    def test_eligibility_is_read_from_the_property(self) -> None:
        record = to_face_record(detected(), eligible(), created_at=NOW)
        self.assertTrue(record["identity"]["eligible_for_automated_output"])
        ineligible = PersonAssignment(
            face_id=fid("f0"),
            assignment=Assignment.AUTO_HIGH_CONFIDENCE,
            person_id=PERSON,
            confidence=0.97,
            threshold_used=0.92,
            minor_status=MinorStatus.UNKNOWN,
        )
        record = to_face_record(detected(), ineligible, created_at=NOW)
        self.assertFalse(record["identity"]["eligible_for_automated_output"])

    def test_there_is_no_way_to_pass_eligibility_in(self) -> None:
        parameters = set(inspect.signature(to_face_record).parameters)
        self.assertNotIn("eligible", parameters)
        self.assertNotIn("eligible_for_automated_output", parameters)

    def test_a_mismatched_detection_and_assignment_raises(self) -> None:
        with self.assertRaises(RecordError):
            to_face_record(detected("f0"), eligible("f1"), created_at=NOW)

    def test_a_mismatched_membership_raises(self) -> None:
        face_id = fid("f1")
        clustering = cluster_faces(
            [
                FaceObservation(
                    face_id=face_id, embedding=embedding(face_id, axis_vector(0))
                )
            ],
            merge_threshold=0.5,
            run_id="run-1",
        )
        with self.assertRaises(RecordError):
            to_face_record(
                detected("f0"),
                eligible("f0"),
                membership=clustering.membership_of(face_id),
                created_at=NOW,
            )

    def test_reasons_with_no_contract_equivalent_serialise_as_null(self) -> None:
        # Reporting `near_boundary` for a face held back by a missing consent
        # would be a plausible, wrong explanation.
        for reason in (
            ReviewReason.MINOR_CONSENT_REQUIRED,
            ReviewReason.MINOR_STATUS_UNRESOLVED,
            ReviewReason.UNCALIBRATED_THRESHOLD,
            ReviewReason.NO_EMBEDDING,
        ):
            with self.subTest(reason=reason):
                self.assertIsNone(reason.contract_value)
                record = to_face_record(
                    detected(),
                    PersonAssignment(
                        face_id=fid("f0"),
                        assignment=Assignment.REVIEW_QUEUED,
                        minor_status=MinorStatus.UNKNOWN,
                        review_reason=reason,
                    ),
                    created_at=NOW,
                )
                self.assertIsNone(record["identity"]["review_reason"])

    def test_contract_reasons_round_trip(self) -> None:
        for reason in (
            ReviewReason.BELOW_THRESHOLD,
            ReviewReason.NEAR_BOUNDARY,
            ReviewReason.MULTIPLE_CANDIDATES,
            ReviewReason.NEW_CLUSTER,
            ReviewReason.USER_REPORTED_ERROR,
            ReviewReason.LOW_FACE_QUALITY,
            ReviewReason.EXTREME_POSE,
        ):
            with self.subTest(reason=reason):
                self.assertEqual(reason.contract_value, reason.value)

    def test_a_confirmed_minor_with_no_consent_cannot_be_serialised(self) -> None:
        # The schema requires the consent object whenever minor_status is
        # confirmed_minor, named or not. Raising here attributes the gap to the
        # face that needs it instead of emitting a record render will reject.
        unconsented = PersonAssignment(
            face_id=fid("f0"),
            assignment=Assignment.UNASSIGNED,
            minor_status=MinorStatus.CONFIRMED_MINOR,
            labeling_consent=None,
            review_reason=ReviewReason.MINOR_CONSENT_REQUIRED,
        )
        with self.assertRaises(RecordError):
            to_face_record(detected(), unconsented, created_at=NOW)

    def test_an_expired_consent_does_not_make_a_record_eligible(self) -> None:
        stale = PersonAssignment(
            face_id=fid("f0"),
            assignment=Assignment.UNASSIGNED,
            minor_status=MinorStatus.CONFIRMED_MINOR,
            labeling_consent=expired_consent(),
            consent_checked_at=NOW,
            review_reason=ReviewReason.MINOR_CONSENT_REQUIRED,
        )
        record = to_face_record(detected(), stale, created_at=NOW)
        self.assertFalse(record["identity"]["eligible_for_automated_output"])
        self.assertIsNone(record["identity"]["person_id"])


class MutationSurvivorTests(unittest.TestCase):
    """Properties the first mutation sweep proved nothing was checking."""

    def test_the_sharing_exclusion_flag_survives_serialisation(self) -> None:
        # SURVIVOR rec06: hard-coding `excluded_from_sharing: False` survived,
        # because nothing asserted the flag round-trips. It is the field that
        # keeps a face out of a shared album, and losing it is a privacy
        # failure that produces a perfectly valid record.
        excluded = to_face_record(
            detected(excluded_from_sharing=True), eligible(), created_at=NOW
        )
        included = to_face_record(
            detected(excluded_from_sharing=False), eligible(), created_at=NOW
        )
        self.assertTrue(excluded["sensitive"]["excluded_from_sharing"])
        self.assertFalse(included["sensitive"]["excluded_from_sharing"])


class ValidationTests(unittest.TestCase):
    def test_a_box_outside_the_frame_raises(self) -> None:
        for kwargs in (
            {"x": -0.1},
            {"y": 1.1},
            {"w": 0.0},
            {"h": -0.2},
            {"w": 1.2},
            {"x": float("nan")},
        ):
            with self.subTest(kwargs=kwargs):
                base = {"x": 0.1, "y": 0.1, "w": 0.2, "h": 0.2}
                base.update(kwargs)
                with self.assertRaises(RecordError):
                    NormalizedBox(**base)

    def test_a_detection_score_outside_zero_to_one_raises(self) -> None:
        for bad in (-0.01, 1.01, float("nan"), True):
            with self.subTest(bad=bad):
                with self.assertRaises(RecordError):
                    Detection(
                        bbox=NormalizedBox(0.1, 0.1, 0.2, 0.2),
                        detection_score=bad,
                        detector=detector(),
                    )

    def test_an_unknown_rendition_raises(self) -> None:
        with self.assertRaises(RecordError):
            Detection(
                bbox=NormalizedBox(0.1, 0.1, 0.2, 0.2),
                detection_score=0.9,
                detector=detector(),
                detected_on="preview_4096",
            )

    def test_a_non_slug_model_id_raises(self) -> None:
        with self.assertRaises(RecordError):
            ModelRef(model_id="SCRFD 10G", version="1.0.0")

    def test_a_non_digest_weights_hash_raises(self) -> None:
        with self.assertRaises(RecordError):
            ModelRef(model_id="scrfd", version="1", weights_blake3="deadbeef")

    def test_a_non_digest_media_id_raises(self) -> None:
        with self.assertRaises(RecordError):
            DetectedFace(
                face_id=fid("f0"),
                media_id="media-1",
                detection=Detection(
                    bbox=NormalizedBox(0.1, 0.1, 0.2, 0.2),
                    detection_score=0.9,
                    detector=detector(),
                ),
            )


class LayoutBoxTests(unittest.TestCase):
    def test_identity_cannot_reach_the_safety_check(self) -> None:
        # If this signature ever grows an assignment argument, a face the system
        # could not identify becomes a face the guillotine is allowed to cut.
        parameters = set(inspect.signature(face_boxes_for_layout).parameters)
        self.assertEqual(parameters, {"faces", "detection_floor"})

    def test_every_face_over_the_floor_is_returned(self) -> None:
        faces = [detected("f0", score=0.99), detected("f1", score=0.61)]
        boxes = face_boxes_for_layout(faces)
        self.assertEqual(len(boxes), 2)
        self.assertEqual([b.face_id for b in boxes], sorted(b.face_id for b in boxes))

    def test_the_detector_floor_is_applied_on_both_sides(self) -> None:
        at_floor = detected("f0", score=SUBJECT_DETECTION_FLOOR)
        below = detected("f1", score=SUBJECT_DETECTION_FLOOR - 1e-9)
        boxes = face_boxes_for_layout([at_floor, below])
        self.assertEqual([b.face_id for b in boxes], [at_floor.face_id])

    def test_unnamed_and_ineligible_faces_are_still_protected(self) -> None:
        # There is no way to express "skip this one" in the call, which is the
        # point; this asserts the behaviour the signature guarantees.
        boxes = face_boxes_for_layout([detected("stranger", score=0.95)])
        self.assertEqual(len(boxes), 1)
        self.assertTrue(boxes[0].is_subject)

    def test_the_box_survives_the_trip_unchanged(self) -> None:
        face = detected("f0")
        box = face_boxes_for_layout([face])[0]
        self.assertEqual(
            (box.x, box.y, box.w, box.h),
            (
                face.detection.bbox.x,
                face.detection.bbox.y,
                face.detection.bbox.w,
                face.detection.bbox.h,
            ),
        )

    def test_an_invalid_floor_raises(self) -> None:
        for bad in (-0.1, 1.1, float("nan")):
            with self.subTest(bad=bad):
                with self.assertRaises(RecordError):
                    face_boxes_for_layout([detected()], detection_floor=bad)

    def test_an_empty_image_yields_no_boxes(self) -> None:
        self.assertEqual(face_boxes_for_layout([]), ())


class ReadBackTests(unittest.TestCase):
    def test_context_is_read_back_without_inventing_an_embedding(self) -> None:
        record = to_face_record(detected(), eligible(), created_at=NOW)
        context = face_context_from_record(record)
        self.assertEqual(context["face_id"], fid("f0"))
        self.assertIs(context["minor_status"], MinorStatus.CONFIRMED_ADULT)
        self.assertIs(context["assignment"], Assignment.AUTO_HIGH_CONFIDENCE)
        # `embedding` is absent, not None: a FaceContext with embedding=None
        # asserts "this face has no embedding", which is a different and very
        # consequential statement from "the caller has not fetched it yet".
        self.assertNotIn("embedding", context)

    def test_attributes_and_cluster_flags_are_read(self) -> None:
        record = to_face_record(
            detected(attributes={"yaw_deg": -50.0, "quality": {"value": 0.42}}),
            eligible(),
            created_at=NOW,
        )
        context = face_context_from_record(record)
        self.assertEqual(context["yaw_deg"], -50.0)
        self.assertEqual(context["quality"], 0.42)
        self.assertFalse(context["cluster_is_noise"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
