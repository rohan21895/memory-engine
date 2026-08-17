"""Identity tests: the gate, and every way it has been got wrong elsewhere.

The single most important test in this package is
`GateShapeTests.test_eligibility_is_not_a_constructor_argument`. Everything else
here defends a behaviour; that one defends the SHAPE, and the shape is what
makes the behaviour unforgettable.

Every threshold is asserted on both sides. Every state that must be ineligible
is enumerated rather than sampled, because "we tested the interesting ones" is
how the boring one ships.
"""

from __future__ import annotations

import dataclasses
import math
import unittest

from support import (  # noqa: E402
    NOW,
    axis_vector,
    embedding,
    expired_consent,
    fid,
    live_consent,
    person_faces,
    pid,
    revoked_consent,
)

from memory_engine_face.identity import (  # noqa: E402
    MIN_AUTOMATED_OUTPUT_CONFIDENCE,
    USER_CONFIRMATION_CONFIDENCE,
    MIN_CALIBRATION_PAIRS,
    PRECISION_TARGET,
    Assignment,
    AutomatedFaceSet,
    Candidate,
    ConsentRef,
    DecidedBy,
    Decisions,
    FaceContext,
    FittedCalibrator,
    IdentityError,
    IneligibleFace,
    MinorStatus,
    Person,
    PersonAssignment,
    PersonGallery,
    ReviewReason,
    ThresholdProfile,
    Thresholds,
    UncalibratedSimilarity,
    assign_identities,
    effective_minor_status,
    may_be_named,
)

PERSON = pid("grandma")
OTHER = pid("uncle")
EVAL_DIGEST = fid("evaluation-set")


def fitted(operating_confidence: float = 0.92) -> FittedCalibrator:
    return FittedCalibrator(
        space="arcface_buffalo_l_512",
        operating_similarity=0.5,
        operating_confidence=operating_confidence,
        measured_precision=0.995,
        evaluated_pairs=5000,
        inputs_digest=EVAL_DIGEST,
        fitted_on="2026-08-01",
    )


def assignment(**overrides) -> PersonAssignment:
    """An eligible assignment, so a test can break exactly one thing."""
    base = {
        "face_id": fid("f0"),
        "assignment": Assignment.AUTO_HIGH_CONFIDENCE,
        "person_id": PERSON,
        "confidence": 0.97,
        "threshold_used": 0.92,
        "threshold_profile": ThresholdProfile.AUTOMATED_OUTPUT,
        "minor_status": MinorStatus.CONFIRMED_ADULT,
        "has_own_embedding": True,
        "decided_by": DecidedBy.MODEL,
        "decided_at": NOW,
    }
    base.update(overrides)
    return PersonAssignment(**base)


# ---------------------------------------------------------------------------
# The shape of the gate
# ---------------------------------------------------------------------------


class GateShapeTests(unittest.TestCase):
    def test_eligibility_is_not_a_constructor_argument(self) -> None:
        names = {f.name for f in dataclasses.fields(PersonAssignment)}
        self.assertNotIn(
            "eligible_for_automated_output",
            names,
            "eligibility became a field; a caller can now assert it without "
            "carrying the evidence, which is the entire failure this package "
            "was built to make unreachable",
        )
        with self.assertRaises(TypeError):
            PersonAssignment(  # type: ignore[call-arg]
                face_id=fid("f0"),
                assignment=Assignment.UNASSIGNED,
                eligible_for_automated_output=True,
            )

    def test_eligibility_cannot_be_written_after_construction(self) -> None:
        record = assignment()
        with self.assertRaises(Exception):
            record.eligible_for_automated_output = False  # type: ignore[misc]

    def test_a_correctly_evidenced_assignment_is_eligible(self) -> None:
        self.assertTrue(assignment().eligible_for_automated_output)


class EligibilityTests(unittest.TestCase):
    def test_only_two_assignment_states_can_ever_be_eligible(self) -> None:
        eligible_states = set()
        for state in Assignment:
            try:
                record = assignment(assignment=state)
            except IdentityError:
                # Constructing this state with a person_id is refused outright,
                # which is a stronger guarantee than being ineligible.
                continue
            if record.eligible_for_automated_output:
                eligible_states.add(state)
        self.assertEqual(
            eligible_states,
            {Assignment.USER_CONFIRMED, Assignment.AUTO_HIGH_CONFIDENCE},
        )

    def test_a_missing_person_id_is_never_eligible(self) -> None:
        record = PersonAssignment(
            face_id=fid("f0"),
            assignment=Assignment.UNASSIGNED,
            minor_status=MinorStatus.CONFIRMED_ADULT,
        )
        self.assertFalse(record.eligible_for_automated_output)

    def test_confidence_is_compared_to_the_threshold_on_both_sides(self) -> None:
        self.assertTrue(
            assignment(confidence=0.92, threshold_used=0.92)
            .eligible_for_automated_output
        )
        self.assertFalse(
            assignment(confidence=0.9199999, threshold_used=0.92)
            .eligible_for_automated_output
        )

    def test_a_threshold_below_the_floor_cannot_justify_anything(self) -> None:
        # The schema's own invariant -- "eligible requires confidence >=
        # threshold_used" -- is satisfiable with threshold_used = 0.1. The floor
        # is what makes that comparison mean something.
        just_under = MIN_AUTOMATED_OUTPUT_CONFIDENCE - 1e-9
        self.assertFalse(
            assignment(confidence=1.0, threshold_used=just_under)
            .eligible_for_automated_output
        )
        self.assertTrue(
            assignment(
                confidence=1.0, threshold_used=MIN_AUTOMATED_OUTPUT_CONFIDENCE
            ).eligible_for_automated_output
        )

    def test_a_nan_confidence_is_not_eligible(self) -> None:
        # A NaN cannot reach here through the constructor, which refuses it; the
        # property checks anyway because `nan >= threshold` is False and
        # `nan < threshold` is also False, so which branch a NaN lands in
        # depends on how the comparison happened to be written.
        with self.assertRaises(IdentityError):
            assignment(confidence=float("nan"))

    def test_a_search_only_threshold_profile_is_not_eligible(self) -> None:
        # search_only is permissive on purpose: a wrong search hit is a shrug.
        self.assertFalse(
            assignment(threshold_profile=ThresholdProfile.SEARCH_ONLY)
            .eligible_for_automated_output
        )
        self.assertFalse(
            assignment(threshold_profile=ThresholdProfile.REVIEW_QUEUE)
            .eligible_for_automated_output
        )

    def test_an_automated_match_without_its_own_embedding_is_not_eligible(self) -> None:
        # Cluster membership can be inherited from a track; permission cannot.
        self.assertFalse(
            assignment(has_own_embedding=False).eligible_for_automated_output
        )

    def test_a_human_confirmation_survives_a_missing_embedding(self) -> None:
        # A person looked at the photograph. That is evidence about this frame.
        record = PersonAssignment(
            face_id=fid("f0"),
            assignment=Assignment.USER_CONFIRMED,
            person_id=PERSON,
            confidence=USER_CONFIRMATION_CONFIDENCE,
            threshold_used=0.92,
            minor_status=MinorStatus.CONFIRMED_ADULT,
            has_own_embedding=False,
            decided_by=DecidedBy.USER,
            decided_at=NOW,
        )
        self.assertTrue(record.eligible_for_automated_output)


class MinorGateTests(unittest.TestCase):
    def test_the_eligibility_table(self) -> None:
        cases = [
            (MinorStatus.CONFIRMED_ADULT, None, True),
            (MinorStatus.CONFIRMED_MINOR, live_consent(), True),
            (MinorStatus.ESTIMATED_MINOR, None, False),
            (MinorStatus.UNKNOWN, None, False),
        ]
        for status, consent, expected in cases:
            with self.subTest(status=status):
                record = assignment(
                    minor_status=status,
                    labeling_consent=consent,
                    consent_checked_at=NOW,
                )
                self.assertEqual(record.eligible_for_automated_output, expected)

    def test_unknown_is_not_treated_as_adult(self) -> None:
        # The schema says so in as many words. This is the assertion that stops
        # a "sensible default" from reintroducing it.
        self.assertFalse(
            assignment(minor_status=MinorStatus.UNKNOWN)
            .eligible_for_automated_output
        )

    def test_a_confirmed_minor_cannot_be_named_without_consent(self) -> None:
        with self.assertRaises(IdentityError):
            assignment(
                minor_status=MinorStatus.CONFIRMED_MINOR,
                labeling_consent=None,
                consent_checked_at=NOW,
            )

    def test_a_revoked_or_expired_consent_does_not_authorise_naming(self) -> None:
        for consent in (revoked_consent(), expired_consent()):
            with self.subTest(consent=consent.ledger_entry_id):
                with self.assertRaises(IdentityError):
                    assignment(
                        minor_status=MinorStatus.CONFIRMED_MINOR,
                        labeling_consent=consent,
                        consent_checked_at=NOW,
                    )

    def test_a_consent_for_another_scope_does_not_authorise_naming(self) -> None:
        with self.assertRaises(IdentityError):
            assignment(
                minor_status=MinorStatus.CONFIRMED_MINOR,
                labeling_consent=live_consent(scope="cloud_render"),
                consent_checked_at=NOW,
            )

    def test_a_consent_granted_in_the_future_is_not_live(self) -> None:
        future = ConsentRef(
            ledger_entry_id=pid("future"),
            scope="minor_face_labeling",
            granted_at="2027-01-01T00:00:00+05:30",
        )
        self.assertFalse(
            may_be_named(MinorStatus.CONFIRMED_MINOR, future, as_of=NOW)
        )

    def test_a_consent_check_with_no_reference_time_fails_closed(self) -> None:
        self.assertFalse(
            may_be_named(MinorStatus.CONFIRMED_MINOR, live_consent(), as_of=None)
        )

    def test_an_unparseable_or_offsetless_timestamp_fails_closed(self) -> None:
        for as_of in ("not-a-time", "2026-08-17T10:00:00", ""):
            with self.subTest(as_of=as_of):
                self.assertFalse(
                    may_be_named(
                        MinorStatus.CONFIRMED_MINOR, live_consent(), as_of=as_of
                    )
                )

    def test_naming_is_allowed_for_unknown_and_estimated_minor(self) -> None:
        # Blocking naming here too would be more conservative and would make the
        # product unusable; the caution is applied at eligibility instead.
        for status in (MinorStatus.UNKNOWN, MinorStatus.ESTIMATED_MINOR):
            with self.subTest(status=status):
                self.assertTrue(may_be_named(status, None, as_of=NOW))

    def test_effective_status_takes_the_more_cautious_of_the_two(self) -> None:
        cases = [
            (MinorStatus.UNKNOWN, MinorStatus.CONFIRMED_MINOR, MinorStatus.CONFIRMED_MINOR),
            (MinorStatus.CONFIRMED_ADULT, MinorStatus.CONFIRMED_MINOR, MinorStatus.CONFIRMED_MINOR),
            (MinorStatus.CONFIRMED_ADULT, MinorStatus.ESTIMATED_MINOR, MinorStatus.ESTIMATED_MINOR),
            (MinorStatus.CONFIRMED_ADULT, MinorStatus.UNKNOWN, MinorStatus.UNKNOWN),
            (MinorStatus.CONFIRMED_ADULT, MinorStatus.CONFIRMED_ADULT, MinorStatus.CONFIRMED_ADULT),
        ]
        for face, person, expected in cases:
            with self.subTest(face=face, person=person):
                self.assertIs(effective_minor_status(face, person), expected)
                self.assertIs(effective_minor_status(person, face), expected)

    def test_with_minor_status_strips_the_name_when_the_answer_forbids_it(self) -> None:
        before = assignment()
        after = before.with_minor_status(
            MinorStatus.CONFIRMED_MINOR, consent=None, as_of=NOW
        )
        self.assertIsNone(after.person_id)
        self.assertIs(after.assignment, Assignment.UNASSIGNED)
        self.assertIs(after.review_reason, ReviewReason.MINOR_CONSENT_REQUIRED)
        self.assertFalse(after.eligible_for_automated_output)

    def test_with_minor_status_keeps_the_name_when_consent_is_live(self) -> None:
        after = assignment().with_minor_status(
            MinorStatus.CONFIRMED_MINOR, consent=live_consent(), as_of=NOW
        )
        self.assertEqual(after.person_id, PERSON)
        self.assertTrue(after.eligible_for_automated_output)


class ConsistencyTests(unittest.TestCase):
    def test_uncommitted_states_may_not_carry_a_person_id(self) -> None:
        for state in (
            Assignment.UNASSIGNED,
            Assignment.USER_REJECTED,
            Assignment.AUTO_BELOW_THRESHOLD,
            Assignment.REVIEW_QUEUED,
            Assignment.AMBIGUOUS_MULTIPLE_CANDIDATES,
        ):
            with self.subTest(state=state):
                with self.assertRaises(IdentityError):
                    assignment(
                        assignment=state, review_reason=ReviewReason.NEAR_BOUNDARY
                    )

    def test_committed_states_must_carry_a_person_id(self) -> None:
        for state in (Assignment.USER_CONFIRMED, Assignment.AUTO_HIGH_CONFIDENCE):
            with self.subTest(state=state):
                with self.assertRaises(IdentityError):
                    assignment(assignment=state, person_id=None)

    def test_auto_high_confidence_must_state_its_evidence(self) -> None:
        with self.assertRaises(IdentityError):
            assignment(confidence=None)
        with self.assertRaises(IdentityError):
            assignment(threshold_used=None)

    def test_an_eligible_assignment_must_state_its_evidence(self) -> None:
        # Mirrors the contract's positive constraint: eligible = true requires
        # person_id, confidence AND threshold_used. The first version of this
        # class produced a user-confirmed record that violated it, and only the
        # schema noticed.
        with self.assertRaises(IdentityError):
            PersonAssignment(
                face_id=fid("f0"),
                assignment=Assignment.USER_CONFIRMED,
                person_id=PERSON,
                confidence=None,
                threshold_used=0.92,
                minor_status=MinorStatus.CONFIRMED_ADULT,
            )
        with self.assertRaises(IdentityError):
            PersonAssignment(
                face_id=fid("f0"),
                assignment=Assignment.USER_CONFIRMED,
                person_id=PERSON,
                confidence=1.0,
                threshold_used=None,
                minor_status=MinorStatus.CONFIRMED_ADULT,
            )
        # Ineligible states may legitimately omit both.
        PersonAssignment(
            face_id=fid("f0"),
            assignment=Assignment.USER_CONFIRMED,
            person_id=PERSON,
            minor_status=MinorStatus.UNKNOWN,
        )

    def test_a_queued_face_must_say_why(self) -> None:
        for state in (
            Assignment.AUTO_BELOW_THRESHOLD,
            Assignment.REVIEW_QUEUED,
            Assignment.AMBIGUOUS_MULTIPLE_CANDIDATES,
        ):
            with self.subTest(state=state):
                with self.assertRaises(IdentityError):
                    PersonAssignment(
                        face_id=fid("f0"), assignment=state, review_reason=None
                    )

    def test_confidence_must_be_a_unit(self) -> None:
        for bad in (-0.01, 1.01, float("inf"), True):
            with self.subTest(bad=bad):
                with self.assertRaises(IdentityError):
                    assignment(confidence=bad)

    def test_candidates_are_capped_and_deduplicated(self) -> None:
        many = tuple(
            Candidate(person_id=pid(f"p{i}"), confidence=0.5) for i in range(9)
        )
        with self.assertRaises(IdentityError):
            assignment(candidates=many)
        duplicated = (
            Candidate(person_id=PERSON, confidence=0.6),
            Candidate(person_id=PERSON, confidence=0.5),
        )
        with self.assertRaises(IdentityError):
            assignment(candidates=duplicated)

    def test_timestamps_must_carry_an_offset(self) -> None:
        with self.assertRaises(IdentityError):
            assignment(decided_at="2026-08-17T10:00:00")


# ---------------------------------------------------------------------------
# Thresholds and calibration
# ---------------------------------------------------------------------------


class ThresholdTests(unittest.TestCase):
    def test_defaults_are_valid(self) -> None:
        thresholds = Thresholds()
        self.assertGreaterEqual(
            thresholds.automated_output, MIN_AUTOMATED_OUTPUT_CONFIDENCE
        )
        self.assertLess(thresholds.review_floor, thresholds.automated_output)

    def test_the_automated_threshold_cannot_be_lowered_past_the_floor(self) -> None:
        Thresholds(automated_output=MIN_AUTOMATED_OUTPUT_CONFIDENCE)
        with self.assertRaises(IdentityError):
            Thresholds(automated_output=MIN_AUTOMATED_OUTPUT_CONFIDENCE - 1e-6)

    def test_the_two_thresholds_cannot_be_collapsed_into_one(self) -> None:
        with self.assertRaises(IdentityError):
            Thresholds(automated_output=0.92, review_floor=0.92)
        with self.assertRaises(IdentityError):
            Thresholds(automated_output=0.92, review_floor=0.95)
        Thresholds(automated_output=0.92, review_floor=0.9199)

    def test_a_negative_pose_penalty_is_refused(self) -> None:
        # A negative penalty would LOWER the bar for exactly the faces the
        # schema says to raise it for.
        with self.assertRaises(IdentityError):
            Thresholds(extreme_pose_penalty=-0.01)
        Thresholds(extreme_pose_penalty=0.0)

    def test_non_numeric_and_non_finite_values_are_refused(self) -> None:
        for kwargs in (
            {"automated_output": float("nan")},
            {"review_floor": float("inf")},
            {"quality_floor": "high"},
            {"ambiguity_margin": True},
            {"extreme_yaw_deg": 181.0},
            {"quality_floor": 1.5},
            {"ambiguity_margin": 1.0},
        ):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(IdentityError):
                    Thresholds(**kwargs)


class CalibratorTests(unittest.TestCase):
    def test_the_default_calibrator_is_not_calibrated(self) -> None:
        self.assertFalse(UncalibratedSimilarity().calibrated)

    def test_the_uncalibrated_map_is_monotone_and_bounded(self) -> None:
        calibrator = UncalibratedSimilarity()
        previous = -1.0
        for step in range(21):
            similarity = -1.0 + step * 0.1
            value = calibrator.confidence(similarity)
            self.assertGreaterEqual(value, 0.0)
            self.assertLessEqual(value, 1.0)
            self.assertGreaterEqual(value, previous)
            previous = value

    def test_a_fitted_calibrator_is_exact_at_its_operating_point(self) -> None:
        # This equality is what makes "confidence >= automated_output" and
        # "similarity >= the similarity precision was measured at" the same
        # test. An approximation moves the real operating point invisibly.
        calibrator = fitted()
        self.assertEqual(
            calibrator.confidence(calibrator.operating_similarity),
            calibrator.operating_confidence,
        )

    def test_a_fitted_calibrator_is_monotone_through_its_operating_point(self) -> None:
        calibrator = fitted()
        below = calibrator.confidence(calibrator.operating_similarity - 1e-6)
        above = calibrator.confidence(calibrator.operating_similarity + 1e-6)
        self.assertLess(below, calibrator.operating_confidence)
        self.assertGreater(above, calibrator.operating_confidence)
        self.assertEqual(calibrator.confidence(1.0), 1.0)
        self.assertEqual(calibrator.confidence(-1.0), 0.0)

    def test_similarity_outside_the_valid_range_is_clamped_not_extrapolated(self) -> None:
        calibrator = fitted()
        self.assertEqual(calibrator.confidence(5.0), 1.0)
        self.assertEqual(calibrator.confidence(-5.0), 0.0)

    def test_a_non_finite_similarity_raises(self) -> None:
        for calibrator in (UncalibratedSimilarity(), fitted()):
            with self.subTest(calibrator=type(calibrator).__name__):
                with self.assertRaises(IdentityError):
                    calibrator.confidence(float("nan"))

    def test_precision_below_the_target_is_not_an_operating_point(self) -> None:
        with self.assertRaises(IdentityError):
            FittedCalibrator(
                space="arcface_buffalo_l_512",
                operating_similarity=0.5,
                operating_confidence=0.92,
                measured_precision=PRECISION_TARGET - 0.001,
                evaluated_pairs=5000,
                inputs_digest=EVAL_DIGEST,
                fitted_on="2026-08-01",
            )

    def test_too_few_pairs_is_a_coincidence_with_a_decimal_point(self) -> None:
        with self.assertRaises(IdentityError):
            FittedCalibrator(
                space="arcface_buffalo_l_512",
                operating_similarity=0.5,
                operating_confidence=0.92,
                measured_precision=1.0,
                evaluated_pairs=MIN_CALIBRATION_PAIRS - 1,
                inputs_digest=EVAL_DIGEST,
                fitted_on="2026-08-01",
            )
        FittedCalibrator(
            space="arcface_buffalo_l_512",
            operating_similarity=0.5,
            operating_confidence=0.92,
            measured_precision=1.0,
            evaluated_pairs=MIN_CALIBRATION_PAIRS,
            inputs_digest=EVAL_DIGEST,
            fitted_on="2026-08-01",
        )

    def test_provenance_is_required(self) -> None:
        for kwargs in (
            {"inputs_digest": "not-a-digest"},
            {"fitted_on": "August 2026"},
            {"operating_similarity": 1.0},
            {"operating_confidence": 0.5},
            {"measured_precision": 1.5},
        ):
            with self.subTest(kwargs=kwargs):
                base = {
                    "space": "arcface_buffalo_l_512",
                    "operating_similarity": 0.5,
                    "operating_confidence": 0.92,
                    "measured_precision": 0.995,
                    "evaluated_pairs": 5000,
                    "inputs_digest": EVAL_DIGEST,
                    "fitted_on": "2026-08-01",
                }
                base.update(kwargs)
                with self.assertRaises(IdentityError):
                    FittedCalibrator(**base)


# ---------------------------------------------------------------------------
# assign_identities
# ---------------------------------------------------------------------------


class AssignmentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.thresholds = Thresholds()
        self.enrolled = person_faces(0, 3)
        self.gallery = PersonGallery(
            people=(
                Person(
                    person_id=PERSON,
                    enrolled=tuple(self.enrolled),
                    minor_status=MinorStatus.CONFIRMED_ADULT,
                ),
            )
        )
        self.match = FaceContext(
            face_id=fid("p0-f9"),
            embedding=embedding(fid("p0-f9"), axis_vector(0)),
            quality=0.9,
            minor_status=MinorStatus.CONFIRMED_ADULT,
        )

    def assign(self, faces, calibrator=None, **kwargs):
        return assign_identities(
            faces,
            kwargs.pop("gallery", self.gallery),
            thresholds=kwargs.pop("thresholds", self.thresholds),
            calibrator=calibrator or fitted(),
            now=kwargs.pop("now", NOW),
            **kwargs,
        )

    def test_an_uncalibrated_calibrator_closes_the_automated_path(self) -> None:
        results = self.assign([self.match], calibrator=UncalibratedSimilarity())
        self.assertIs(results[0].assignment, Assignment.REVIEW_QUEUED)
        self.assertIs(
            results[0].review_reason, ReviewReason.UNCALIBRATED_THRESHOLD
        )
        self.assertFalse(results[0].eligible_for_automated_output)

    def test_a_calibrated_confident_match_is_eligible(self) -> None:
        results = self.assign([self.match])
        self.assertIs(results[0].assignment, Assignment.AUTO_HIGH_CONFIDENCE)
        self.assertEqual(results[0].person_id, PERSON)
        self.assertTrue(results[0].eligible_for_automated_output)

    def test_a_calibrator_fitted_for_another_operating_point_is_refused(self) -> None:
        with self.assertRaises(IdentityError):
            self.assign([self.match], calibrator=fitted(operating_confidence=0.95))

    def test_a_face_with_no_embedding_is_never_automatically_named(self) -> None:
        blind = FaceContext(
            face_id=fid("blind"),
            embedding=None,
            minor_status=MinorStatus.CONFIRMED_ADULT,
        )
        result = self.assign([blind])[0]
        self.assertIsNone(result.person_id)
        self.assertIs(result.review_reason, ReviewReason.NO_EMBEDDING)
        self.assertFalse(result.eligible_for_automated_output)

    def test_an_empty_gallery_produces_a_new_cluster_question(self) -> None:
        result = self.assign([self.match], gallery=PersonGallery())[0]
        self.assertIs(result.assignment, Assignment.UNASSIGNED)
        self.assertIs(result.review_reason, ReviewReason.NEW_CLUSTER)

    def test_a_person_with_no_enrolled_faces_is_never_a_candidate(self) -> None:
        gallery = PersonGallery(people=(Person(person_id=PERSON),))
        result = self.assign([self.match], gallery=gallery)[0]
        self.assertIs(result.review_reason, ReviewReason.NEW_CLUSTER)
        self.assertEqual(result.candidates, ())

    def test_a_distant_face_falls_below_the_review_floor(self) -> None:
        stranger = FaceContext(
            face_id=fid("stranger"),
            embedding=embedding(fid("stranger"), axis_vector(0, scale=-1.0)),
            quality=0.9,
            minor_status=MinorStatus.CONFIRMED_ADULT,
        )
        result = self.assign([stranger])[0]
        self.assertIs(result.assignment, Assignment.AUTO_BELOW_THRESHOLD)
        self.assertIs(result.review_reason, ReviewReason.BELOW_THRESHOLD)
        self.assertIsNone(result.person_id)
        self.assertEqual(len(result.candidates), 1)

    def test_two_equally_good_candidates_are_ambiguous_not_a_best_match(self) -> None:
        # Twins and siblings: a single best-match number hides them completely.
        twin_gallery = PersonGallery(
            people=(
                Person(
                    person_id=PERSON,
                    enrolled=(embedding(fid("e0"), axis_vector(0)),),
                    minor_status=MinorStatus.CONFIRMED_ADULT,
                ),
                Person(
                    person_id=OTHER,
                    enrolled=(embedding(fid("e1"), axis_vector(0)),),
                    minor_status=MinorStatus.CONFIRMED_ADULT,
                ),
            )
        )
        result = self.assign([self.match], gallery=twin_gallery)[0]
        self.assertIs(result.assignment, Assignment.AMBIGUOUS_MULTIPLE_CANDIDATES)
        self.assertIs(result.review_reason, ReviewReason.MULTIPLE_CANDIDATES)
        self.assertIsNone(result.person_id)
        self.assertEqual(len(result.candidates), 2)
        self.assertFalse(result.eligible_for_automated_output)

    def test_extreme_yaw_tightens_the_threshold_rather_than_rejecting(self) -> None:
        thresholds = Thresholds(extreme_pose_penalty=0.04)
        straight = self.assign([self.match], thresholds=thresholds)[0]
        turned = self.assign(
            [dataclasses.replace(self.match, yaw_deg=-60.0)], thresholds=thresholds
        )[0]
        self.assertEqual(straight.threshold_used, thresholds.automated_output)
        self.assertEqual(
            turned.threshold_used, thresholds.automated_output + 0.04
        )

    def test_yaw_at_the_limit_does_not_tighten_and_just_past_it_does(self) -> None:
        thresholds = Thresholds()
        at_limit = self.assign(
            [dataclasses.replace(self.match, yaw_deg=45.0)], thresholds=thresholds
        )[0]
        past_limit = self.assign(
            [dataclasses.replace(self.match, yaw_deg=45.001)], thresholds=thresholds
        )[0]
        self.assertEqual(at_limit.threshold_used, thresholds.automated_output)
        self.assertGreater(past_limit.threshold_used, thresholds.automated_output)

    def test_the_tightened_threshold_is_capped_at_one(self) -> None:
        thresholds = Thresholds(automated_output=0.99, extreme_pose_penalty=0.5)
        result = self.assign(
            [dataclasses.replace(self.match, yaw_deg=90.0)],
            thresholds=thresholds,
            calibrator=fitted(operating_confidence=0.99),
        )[0]
        self.assertLessEqual(result.threshold_used, 1.0)

    def test_a_low_quality_face_goes_to_review_however_confident(self) -> None:
        thresholds = Thresholds(quality_floor=0.35)
        poor = dataclasses.replace(self.match, quality=0.34)
        good = dataclasses.replace(self.match, quality=0.35)
        self.assertIs(
            self.assign([poor], thresholds=thresholds)[0].review_reason,
            ReviewReason.LOW_FACE_QUALITY,
        )
        self.assertIs(
            self.assign([good], thresholds=thresholds)[0].assignment,
            Assignment.AUTO_HIGH_CONFIDENCE,
        )

    def test_a_noise_cluster_never_reaches_automated_output(self) -> None:
        noisy = dataclasses.replace(self.match, cluster_is_noise=True)
        result = self.assign([noisy])[0]
        self.assertIs(result.assignment, Assignment.REVIEW_QUEUED)
        self.assertIs(result.review_reason, ReviewReason.NEW_CLUSTER)

    def test_an_unresolved_age_blocks_eligibility_but_not_the_name(self) -> None:
        gallery = PersonGallery(
            people=(
                Person(
                    person_id=PERSON,
                    enrolled=tuple(self.enrolled),
                    minor_status=MinorStatus.UNKNOWN,
                ),
            )
        )
        unknown = dataclasses.replace(self.match, minor_status=MinorStatus.UNKNOWN)
        result = self.assign([unknown], gallery=gallery)[0]
        self.assertIs(result.assignment, Assignment.AUTO_HIGH_CONFIDENCE)
        self.assertEqual(result.person_id, PERSON)
        self.assertFalse(result.eligible_for_automated_output)
        self.assertIs(result.review_reason, ReviewReason.MINOR_STATUS_UNRESOLVED)

    def test_a_confirmed_minor_without_consent_is_never_named(self) -> None:
        gallery = PersonGallery(
            people=(
                Person(
                    person_id=PERSON,
                    enrolled=tuple(self.enrolled),
                    minor_status=MinorStatus.CONFIRMED_MINOR,
                    labeling_consent=None,
                ),
            )
        )
        result = self.assign([self.match], gallery=gallery)[0]
        self.assertIsNone(result.person_id)
        self.assertIs(result.review_reason, ReviewReason.MINOR_CONSENT_REQUIRED)

    def test_a_confirmed_minor_with_consent_is_treated_like_anyone_else(self) -> None:
        gallery = PersonGallery(
            people=(
                Person(
                    person_id=PERSON,
                    enrolled=tuple(self.enrolled),
                    minor_status=MinorStatus.CONFIRMED_MINOR,
                    labeling_consent=live_consent(),
                ),
            )
        )
        result = self.assign([self.match], gallery=gallery)[0]
        self.assertEqual(result.person_id, PERSON)
        self.assertTrue(result.eligible_for_automated_output)

    def test_a_human_confirmation_outranks_the_model(self) -> None:
        stranger = FaceContext(
            face_id=fid("stranger"),
            embedding=embedding(fid("stranger"), axis_vector(0, scale=-1.0)),
            minor_status=MinorStatus.CONFIRMED_ADULT,
        )
        result = self.assign(
            [stranger], decisions=Decisions(confirmed={stranger.face_id: PERSON})
        )[0]
        self.assertIs(result.assignment, Assignment.USER_CONFIRMED)
        self.assertIs(result.decided_by, DecidedBy.USER)
        self.assertTrue(result.eligible_for_automated_output)

    def test_a_confirmation_naming_an_unknown_person_raises(self) -> None:
        with self.assertRaises(IdentityError):
            self.assign(
                [self.match],
                decisions=Decisions(confirmed={self.match.face_id: pid("ghost")}),
            )

    def test_a_human_naming_a_child_without_consent_is_refused(self) -> None:
        gallery = PersonGallery(
            people=(
                Person(
                    person_id=PERSON,
                    enrolled=tuple(self.enrolled),
                    minor_status=MinorStatus.CONFIRMED_MINOR,
                ),
            )
        )
        result = self.assign(
            [self.match],
            gallery=gallery,
            decisions=Decisions(confirmed={self.match.face_id: PERSON}),
        )[0]
        self.assertIsNone(result.person_id)
        self.assertIs(result.review_reason, ReviewReason.MINOR_CONSENT_REQUIRED)

    def test_a_rejected_person_is_removed_from_the_candidate_list(self) -> None:
        result = self.assign(
            [self.match],
            decisions=Decisions(rejected={self.match.face_id: frozenset({PERSON})}),
        )[0]
        self.assertIsNone(result.person_id)
        self.assertEqual(result.candidates, ())
        self.assertIs(result.review_reason, ReviewReason.NEW_CLUSTER)

    def test_contradictory_decisions_raise(self) -> None:
        with self.assertRaises(IdentityError):
            Decisions(
                confirmed={fid("f0"): PERSON},
                rejected={fid("f0"): frozenset({PERSON})},
            )

    def test_now_must_be_an_instant_with_an_offset(self) -> None:
        for bad in ("2026-08-17T10:00:00", "yesterday", ""):
            with self.subTest(bad=bad):
                with self.assertRaises(IdentityError):
                    self.assign([self.match], now=bad)

    def test_results_are_sorted_and_order_independent(self) -> None:
        faces = [
            FaceContext(
                face_id=fid(f"m{i}"),
                embedding=embedding(fid(f"m{i}"), axis_vector(0)),
                minor_status=MinorStatus.CONFIRMED_ADULT,
            )
            for i in range(6)
        ]
        forward = self.assign(faces)
        backward = self.assign(list(reversed(faces)))
        self.assertEqual(
            [a.face_id for a in forward], sorted(a.face_id for a in forward)
        )
        self.assertEqual(forward, backward)


class _NaNCalibrator:
    """A third-party Calibrator that returns NaN. `Calibrator` is a Protocol,
    so implementations outside this package exist and this one is legal."""

    space = "arcface_buffalo_l_512"
    calibrated = True
    operating_confidence = 0.92

    def confidence(self, similarity: float) -> float:
        return float("nan")


class MutationSurvivorTests(unittest.TestCase):
    """Properties the first mutation sweep proved nothing was checking."""

    def test_the_calibration_evidence_bar_is_an_absolute_number(self) -> None:
        # SURVIVOR ide09: MIN_CALIBRATION_PAIRS 2000 -> 1 survived, because the
        # existing test derived its input from the constant and moved with it.
        # 500 pairs is not enough evidence whatever the constant says today.
        with self.assertRaises(IdentityError):
            FittedCalibrator(
                space="arcface_buffalo_l_512",
                operating_similarity=0.5,
                operating_confidence=0.92,
                measured_precision=1.0,
                evaluated_pairs=500,
                inputs_digest=EVAL_DIGEST,
                fitted_on="2026-08-01",
            )
        self.assertGreaterEqual(MIN_CALIBRATION_PAIRS, 1000)

    def test_a_calibrator_returning_nan_is_refused_before_it_can_decide(self) -> None:
        # Written while chasing SURVIVOR ide15 (`not (confidence >= threshold)`
        # -> `confidence < threshold`), and it documents why that mutation is
        # EQUIVALENT rather than a gap: a NaN confidence never reaches the
        # threshold comparison, because `Candidate` refuses it first. That is
        # the stricter guard and the better place for it -- but the `not (>=)`
        # form stays, because it is the shape that keeps this safe if the
        # candidate list is ever built after the comparison instead of before.
        #
        # `Calibrator` is a Protocol, so implementations outside this package
        # exist and this is a legal one.
        gallery = PersonGallery(
            people=(
                Person(
                    person_id=PERSON,
                    enrolled=(embedding(fid("e0"), axis_vector(0)),),
                    minor_status=MinorStatus.CONFIRMED_ADULT,
                ),
            )
        )
        face = FaceContext(
            face_id=fid("f0"),
            embedding=embedding(fid("f0"), axis_vector(0)),
            minor_status=MinorStatus.CONFIRMED_ADULT,
        )
        with self.assertRaisesRegex(IdentityError, "nan"):
            assign_identities(
                [face],
                gallery,
                thresholds=Thresholds(),
                calibrator=_NaNCalibrator(),
                now=NOW,
            )

    def test_the_best_match_wins_not_the_worst(self) -> None:
        # SURVIVOR ide35: reversing the candidate sort survived, because every
        # existing fixture had either one candidate or two equally good ones.
        # With a clear winner and a clear loser, the ordering is load-bearing:
        # under the mutant the face is named as the person it matches LEAST.
        right = embedding(fid("right"), axis_vector(0))
        wrong = embedding(fid("wrong"), axis_vector(7))
        gallery = PersonGallery(
            people=(
                Person(
                    person_id=PERSON,
                    enrolled=(right,),
                    minor_status=MinorStatus.CONFIRMED_ADULT,
                ),
                Person(
                    person_id=OTHER,
                    enrolled=(wrong,),
                    minor_status=MinorStatus.CONFIRMED_ADULT,
                ),
            )
        )
        face = FaceContext(
            face_id=fid("f0"),
            embedding=embedding(fid("f0"), axis_vector(0)),
            minor_status=MinorStatus.CONFIRMED_ADULT,
        )
        result = assign_identities(
            [face],
            gallery,
            thresholds=Thresholds(),
            calibrator=fitted(),
            now=NOW,
        )[0]
        self.assertEqual(result.person_id, PERSON)
        self.assertEqual(result.candidates[0].person_id, PERSON)
        self.assertGreater(
            result.candidates[0].confidence, result.candidates[1].confidence
        )


class GalleryTests(unittest.TestCase):
    def test_similarity_is_none_for_an_unenrolled_person(self) -> None:
        gallery = PersonGallery(people=(Person(person_id=PERSON),))
        self.assertIsNone(
            gallery.similarity(
                embedding(fid("f0"), axis_vector(0)), gallery.people[0]
            )
        )

    def test_top_k_averages_the_closest_enrolled_faces(self) -> None:
        near = [embedding(fid(f"n{i}"), axis_vector(0)) for i in range(3)]
        far = [embedding(fid(f"x{i}"), axis_vector(50 + i)) for i in range(5)]
        person = Person(person_id=PERSON, enrolled=tuple(near + far))
        probe = embedding(fid("probe"), axis_vector(0))
        top3 = PersonGallery(people=(person,), top_k=3)
        everything = PersonGallery(people=(person,), top_k=99)
        self.assertGreater(
            top3.similarity(probe, person), everything.similarity(probe, person)
        )
        self.assertAlmostEqual(top3.similarity(probe, person), 1.0, places=9)

    def test_top_k_must_be_a_positive_int(self) -> None:
        for bad in (0, -1, True, 1.5):
            with self.subTest(bad=bad):
                with self.assertRaises(IdentityError):
                    PersonGallery(top_k=bad)

    def test_duplicate_people_raise(self) -> None:
        with self.assertRaises(IdentityError):
            PersonGallery(
                people=(Person(person_id=PERSON), Person(person_id=PERSON))
            )

    def test_a_gallery_mixing_spaces_raises(self) -> None:
        from memory_engine_face.embeddings import FaceEmbedding

        with self.assertRaises(IdentityError):
            Person(
                person_id=PERSON,
                enrolled=(
                    FaceEmbedding.from_raw(
                        fid("a"), "arcface_buffalo_l_512", axis_vector(0)
                    ),
                    FaceEmbedding.from_raw(
                        fid("b"), "adaface_ir101_512", axis_vector(0)
                    ),
                ),
            )

    def test_person_id_must_be_a_uuid(self) -> None:
        with self.assertRaises(IdentityError):
            Person(person_id="grandma")


class AutomatedFaceSetTests(unittest.TestCase):
    def test_an_ineligible_assignment_cannot_enter_the_set(self) -> None:
        ineligible = assignment(minor_status=MinorStatus.UNKNOWN)
        with self.assertRaises(IneligibleFace):
            AutomatedFaceSet([ineligible])

    def test_filtered_drops_and_reports_rather_than_raising(self) -> None:
        eligible = assignment()
        ineligible = assignment(face_id=fid("f1"), minor_status=MinorStatus.UNKNOWN)
        selected = AutomatedFaceSet.filtered([eligible, ineligible])
        self.assertEqual(len(selected), 1)
        self.assertEqual(
            [a.face_id for a in selected.excluded], [ineligible.face_id]
        )

    def test_the_set_exposes_people_and_their_faces(self) -> None:
        first = assignment()
        second = assignment(face_id=fid("f1"), person_id=OTHER)
        selected = AutomatedFaceSet([first, second])
        self.assertEqual(selected.person_ids, frozenset({PERSON, OTHER}))
        self.assertEqual(selected.faces_of(PERSON), (first,))
        self.assertEqual(list(selected), sorted([first, second], key=lambda a: a.face_id))

    def test_duplicate_faces_raise(self) -> None:
        with self.assertRaises(IdentityError):
            AutomatedFaceSet([assignment(), assignment()])

    def test_an_empty_set_is_valid(self) -> None:
        self.assertEqual(len(AutomatedFaceSet([])), 0)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
