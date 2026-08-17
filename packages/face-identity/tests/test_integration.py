"""End to end: embeddings -> clusters -> assignments -> queue -> answers -> records.

The unit tests check that each piece behaves. This file checks the claim the
package is actually making, which only exists between the pieces:

    an uncertain face cannot reach an album without a human decision,
    and there is no argument, flag or code path that changes that.

It also walks the full loop the product depends on -- ten taps fixing a thousand
photos -- and asserts the shape of it: one confirmation enrols one face, and the
NEXT assignment pass is what moves the rest.
"""

from __future__ import annotations

import unittest

from support import NOW, axis_vector, embedding, fid, pid, unit  # noqa: E402

from memory_engine_face.clustering import FaceObservation, cluster_faces  # noqa: E402
from memory_engine_face.identity import (  # noqa: E402
    Assignment,
    AutomatedFaceSet,
    Decisions,
    FaceContext,
    FittedCalibrator,
    IneligibleFace,
    MinorStatus,
    Person,
    PersonGallery,
    ReviewReason,
    Thresholds,
    UncalibratedSimilarity,
    assign_identities,
)
from memory_engine_face.records import (  # noqa: E402
    DetectedFace,
    Detection,
    ModelRef,
    NormalizedBox,
    face_boxes_for_layout,
    to_face_record,
)
from memory_engine_face.review import (  # noqa: E402
    Answer,
    QuestionKind,
    ReviewDecision,
    ReviewState,
    apply_decisions,
    build_review_queue,
)

GRANDMA = pid("grandma")
LATER = "2026-08-18T10:00:00+05:30"


def calibrator() -> FittedCalibrator:
    return FittedCalibrator(
        space="arcface_buffalo_l_512",
        operating_similarity=0.6,
        operating_confidence=0.92,
        measured_precision=0.995,
        evaluated_pairs=5000,
        inputs_digest=fid("eval-set"),
        fitted_on="2026-08-01",
    )


def library():
    """Four faces of one person and three of another, plus one lone stranger."""
    faces = []
    for person, axis in ((0, 0), (1, 40)):
        for index in range(4 if person == 0 else 3):
            name = f"p{person}-{index}"
            values = axis_vector(axis)
            values[100 + index] += 0.02
            faces.append(
                FaceObservation(
                    face_id=fid(name),
                    embedding=embedding(fid(name), unit(values)),
                    quality=0.9,
                )
            )
    faces.append(
        FaceObservation(
            face_id=fid("stranger"),
            embedding=embedding(fid("stranger"), axis_vector(300)),
            quality=0.9,
        )
    )
    return faces


def contexts(faces, *, minor_status=MinorStatus.CONFIRMED_ADULT, clustering=None):
    memberships = clustering.memberships if clustering else {}
    return [
        FaceContext(
            face_id=face.face_id,
            embedding=face.embedding,
            quality=face.quality,
            minor_status=minor_status,
            cluster_is_noise=bool(
                memberships[face.face_id].is_noise
                if face.face_id in memberships
                else False
            ),
        )
        for face in faces
    ]


class FreshLibraryTests(unittest.TestCase):
    def test_a_fresh_library_produces_no_eligible_faces(self) -> None:
        # Nobody has been labelled, no calibration exists, and no age question
        # has been answered. Every one of those on its own is enough.
        faces = library()
        clustering = cluster_faces(faces, merge_threshold=0.5, run_id="run-1")
        assignments = assign_identities(
            contexts(faces, minor_status=MinorStatus.UNKNOWN, clustering=clustering),
            PersonGallery(),
            thresholds=Thresholds(),
            calibrator=UncalibratedSimilarity(),
            now=NOW,
        )
        selected = AutomatedFaceSet.filtered(assignments)
        self.assertEqual(len(selected), 0)
        self.assertEqual(len(selected.excluded), len(faces))

    def test_the_face_boxes_the_print_validator_needs_are_produced_anyway(self) -> None:
        # This is the whole point of separating safety from identity: the album
        # gate stops being vacuous even though not one face is eligible.
        detections = [
            DetectedFace(
                face_id=face.face_id,
                media_id=fid("media"),
                detection=Detection(
                    bbox=NormalizedBox(0.4, 0.3, 0.1, 0.15),
                    detection_score=0.98,
                    detector=ModelRef("scrfd-10g-bnkps", "1.0.0"),
                ),
            )
            for face in library()
        ]
        boxes = face_boxes_for_layout(detections)
        self.assertEqual(len(boxes), len(detections))


class TheLoopTests(unittest.TestCase):
    def setUp(self) -> None:
        self.faces = library()
        self.thresholds = Thresholds()
        self.clustering = cluster_faces(
            self.faces, merge_threshold=0.5, run_id="run-1"
        )
        self.by_id = {face.face_id: face for face in self.faces}

    def assign(self, gallery, decisions=None):
        return assign_identities(
            contexts(self.faces, clustering=self.clustering),
            gallery,
            thresholds=self.thresholds,
            calibrator=calibrator(),
            now=NOW,
            decisions=decisions,
        )

    def test_one_confirmation_moves_the_rest_on_the_next_pass(self) -> None:
        # Pass 1: an empty gallery, so nothing matches and the queue asks who
        # these people are.
        first = self.assign(PersonGallery())
        self.assertTrue(all(not a.eligible_for_automated_output for a in first))
        queue = build_review_queue(
            first, thresholds=self.thresholds, clustering=self.clustering
        )
        naming = [q for q in queue if q.kind is QuestionKind.NAME_CLUSTER]
        self.assertTrue(naming)

        # The most informative question is about the biggest group.
        question = queue[0]
        self.assertEqual(len(question.face_ids), 1)

        # The human names one face.
        result = apply_decisions(
            ReviewState(assignments=first),
            queue,
            [
                ReviewDecision(
                    question.item_id, Answer.CONFIRM, LATER, person_id=GRANDMA
                )
            ],
            thresholds=self.thresholds,
        )
        self.assertEqual(len(result.confirmed_face_ids), 1)

        # Only that one face moved.
        moved = [
            a
            for a in result.state.assignments
            if a.assignment is Assignment.USER_CONFIRMED
        ]
        self.assertEqual(len(moved), 1)

        # Pass 2: the confirmation enrolled a face, and the rest of that
        # person's photographs are re-scored against it.
        confirmed_face_id = result.confirmed_face_ids[0]
        gallery = PersonGallery(
            people=(
                Person(
                    person_id=GRANDMA,
                    enrolled=(self.by_id[confirmed_face_id].embedding,),
                    minor_status=MinorStatus.CONFIRMED_ADULT,
                ),
            )
        )
        second = self.assign(gallery, decisions=result.state.decisions)
        eligible = AutomatedFaceSet.filtered(second)
        self.assertGreater(len(eligible), 1)
        self.assertEqual(eligible.person_ids, frozenset({GRANDMA}))

        # And the other person's faces did NOT acquire the name.
        named = {a.face_id for a in eligible}
        other_person = {fid(f"p1-{i}") for i in range(3)}
        self.assertEqual(named & other_person, set())

    def test_a_stranger_never_becomes_eligible_by_being_alone(self) -> None:
        gallery = PersonGallery(
            people=(
                Person(
                    person_id=GRANDMA,
                    enrolled=(self.by_id[fid("p0-0")].embedding,),
                    minor_status=MinorStatus.CONFIRMED_ADULT,
                ),
            )
        )
        results = {a.face_id: a for a in self.assign(gallery)}
        stranger = results[fid("stranger")]
        self.assertIsNone(stranger.person_id)
        self.assertFalse(stranger.eligible_for_automated_output)

    def test_a_rejection_reaches_a_face_that_was_already_eligible(self) -> None:
        gallery = PersonGallery(
            people=(
                Person(
                    person_id=GRANDMA,
                    enrolled=(self.by_id[fid("p0-0")].embedding,),
                    minor_status=MinorStatus.CONFIRMED_ADULT,
                ),
            )
        )
        assignments = self.assign(gallery)
        before = AutomatedFaceSet.filtered(assignments)
        self.assertGreater(len(before), 1)

        # Build the question the app would have shown, over the whole cluster.
        cluster = next(
            c
            for c in self.clustering.clusters
            if fid("p0-0") in c.member_face_ids
        )
        from memory_engine_face.review import ReviewItem

        question = ReviewItem(
            item_id=pid("reported-error"),
            kind=QuestionKind.CONFIRM_PERSON,
            face_ids=(fid("p0-1"),),
            affected_face_ids=tuple(sorted(cluster.member_face_ids)),
            subject_person_id=GRANDMA,
            reason=ReviewReason.USER_REPORTED_ERROR,
        )
        result = apply_decisions(
            ReviewState(assignments=assignments),
            [question],
            [
                ReviewDecision(
                    question.item_id, Answer.REJECT, LATER, person_id=GRANDMA
                )
            ],
            thresholds=self.thresholds,
        )
        after = AutomatedFaceSet.filtered(result.state.assignments)
        self.assertEqual(len(after), 0)
        self.assertEqual(
            set(result.eligibility_revoked_face_ids),
            {a.face_id for a in before},
        )

    def test_the_album_boundary_refuses_an_uncertain_face(self) -> None:
        assignments = self.assign(PersonGallery())
        with self.assertRaises(IneligibleFace):
            AutomatedFaceSet(assignments)

    def test_every_assignment_serialises_to_a_valid_record(self) -> None:
        from test_records import face_record_validator

        gallery = PersonGallery(
            people=(
                Person(
                    person_id=GRANDMA,
                    enrolled=(self.by_id[fid("p0-0")].embedding,),
                    minor_status=MinorStatus.CONFIRMED_ADULT,
                ),
            )
        )
        validator = face_record_validator()
        for assignment in self.assign(gallery):
            detected = DetectedFace(
                face_id=assignment.face_id,
                media_id=fid("media"),
                detection=Detection(
                    bbox=NormalizedBox(0.4, 0.3, 0.1, 0.15),
                    detection_score=0.98,
                    detector=ModelRef("scrfd-10g-bnkps", "1.0.0"),
                ),
            )
            record = to_face_record(
                detected,
                assignment,
                membership=self.clustering.membership_of(assignment.face_id),
                created_at=NOW,
            )
            errors = list(validator.iter_errors(record))
            self.assertEqual(
                errors,
                [],
                f"{assignment.face_id}: "
                + "; ".join(f"{list(e.path)}: {e.message}" for e in errors[:3]),
            )


class DeterminismTests(unittest.TestCase):
    def test_the_whole_pipeline_is_reproducible(self) -> None:
        faces = library()
        gallery = PersonGallery(
            people=(
                Person(
                    person_id=GRANDMA,
                    enrolled=(faces[0].embedding,),
                    minor_status=MinorStatus.CONFIRMED_ADULT,
                ),
            )
        )

        def run(order):
            clustering = cluster_faces(order, merge_threshold=0.5, run_id="run-1")
            assignments = assign_identities(
                contexts(order, clustering=clustering),
                gallery,
                thresholds=Thresholds(),
                calibrator=calibrator(),
                now=NOW,
                decisions=Decisions(),
            )
            queue = build_review_queue(
                assignments, thresholds=Thresholds(), clustering=clustering
            )
            return assignments, tuple(q.item_id for q in queue)

        self.assertEqual(run(faces), run(list(reversed(faces))))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
