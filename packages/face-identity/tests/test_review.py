"""Review-queue tests.

The two properties worth the most here:

* **Scope.** An answer moves the faces the human was shown and nothing else.
  A regression that widens propagation to the cluster does not raise, does not
  fail any type check, and mislabels two hundred photos with a human's
  signature on it.
* **Retroaction.** A rejection and a "this is a child" answer must reach
  BACKWARDS into decisions already made. A regression that only affects future
  passes leaves an already-eligible face eligible, which is the case that ends
  up in a printed book.

`eligibility_revoked_face_ids` is computed by diffing the gate before and after
rather than by the code under test asserting it, so a propagation that forgets
to revoke shows up as a missing entry rather than as a wrong label.
"""

from __future__ import annotations

import unittest

from support import NOW, axis_vector, embedding, fid, live_consent, pid  # noqa: E402

from memory_engine_face.clustering import (  # noqa: E402
    FaceObservation,
    PairConstraints,
    cluster_faces,
)
from memory_engine_face.identity import (  # noqa: E402
    USER_CONFIRMATION_CONFIDENCE,
    Assignment,
    Candidate,
    DecidedBy,
    Decisions,
    MinorStatus,
    PersonAssignment,
    ReviewReason,
    Thresholds,
)
from memory_engine_face.review import (  # noqa: E402
    MERGE_QUESTION_WINDOW,
    Answer,
    QuestionKind,
    ReviewDecision,
    ReviewError,
    ReviewItem,
    ReviewState,
    apply_decisions,
    build_review_queue,
)

PERSON = pid("grandma")
OTHER = pid("uncle")
LATER = "2026-08-18T10:00:00+05:30"


def auto(face: str, person: str = PERSON, **kwargs) -> PersonAssignment:
    base = dict(
        face_id=fid(face),
        assignment=Assignment.AUTO_HIGH_CONFIDENCE,
        person_id=person,
        confidence=0.97,
        threshold_used=0.92,
        minor_status=MinorStatus.CONFIRMED_ADULT,
        decided_by=DecidedBy.MODEL,
        decided_at=NOW,
    )
    base.update(kwargs)
    return PersonAssignment(**base)


def queued(face: str, reason: ReviewReason, confidence: float | None = 0.80, **kwargs):
    base = dict(
        face_id=fid(face),
        assignment=Assignment.REVIEW_QUEUED,
        confidence=confidence,
        threshold_used=0.92,
        minor_status=MinorStatus.CONFIRMED_ADULT,
        review_reason=reason,
        candidates=(Candidate(person_id=PERSON, confidence=confidence or 0.5),),
        decided_by=DecidedBy.MODEL,
        decided_at=NOW,
    )
    base.update(kwargs)
    return PersonAssignment(**base)


def item(kind: QuestionKind, shown, affected, **kwargs) -> ReviewItem:
    base = dict(
        item_id=pid(f"item-{kind.value}-{shown[0]}"),
        kind=kind,
        face_ids=tuple(fid(f) for f in shown),
        affected_face_ids=tuple(sorted(fid(f) for f in affected)),
        subject_person_id=PERSON,
        reason=ReviewReason.NEAR_BOUNDARY,
    )
    base.update(kwargs)
    return ReviewItem(**base)


# ---------------------------------------------------------------------------
# Building the queue
# ---------------------------------------------------------------------------


class QueueBuildTests(unittest.TestCase):
    def setUp(self) -> None:
        self.thresholds = Thresholds()

    def test_eligible_faces_produce_no_questions(self) -> None:
        queue = build_review_queue([auto("a")], thresholds=self.thresholds)
        self.assertEqual(queue, ())

    def test_a_queued_face_produces_a_question(self) -> None:
        queue = build_review_queue(
            [queued("a", ReviewReason.NEAR_BOUNDARY)], thresholds=self.thresholds
        )
        self.assertEqual(len(queue), 1)
        self.assertIs(queue[0].kind, QuestionKind.CONFIRM_PERSON)
        self.assertEqual(queue[0].face_ids, (fid("a"),))

    def test_a_face_with_no_candidate_asks_who_this_is(self) -> None:
        blank = PersonAssignment(
            face_id=fid("a"),
            assignment=Assignment.UNASSIGNED,
            minor_status=MinorStatus.CONFIRMED_ADULT,
            review_reason=ReviewReason.NEW_CLUSTER,
        )
        queue = build_review_queue([blank], thresholds=self.thresholds)
        self.assertIs(queue[0].kind, QuestionKind.NAME_CLUSTER)

    def test_ambiguity_asks_which_person_not_whether(self) -> None:
        ambiguous = PersonAssignment(
            face_id=fid("a"),
            assignment=Assignment.AMBIGUOUS_MULTIPLE_CANDIDATES,
            confidence=0.93,
            minor_status=MinorStatus.CONFIRMED_ADULT,
            review_reason=ReviewReason.MULTIPLE_CANDIDATES,
            candidates=(
                Candidate(person_id=PERSON, confidence=0.93),
                Candidate(person_id=OTHER, confidence=0.92),
            ),
        )
        queue = build_review_queue([ambiguous], thresholds=self.thresholds)
        self.assertIs(queue[0].kind, QuestionKind.DISAMBIGUATE)
        self.assertEqual(set(queue[0].candidate_person_ids), {PERSON, OTHER})

    def test_a_consent_question_outranks_everything_in_its_group(self) -> None:
        blocked = PersonAssignment(
            face_id=fid("a"),
            assignment=Assignment.UNASSIGNED,
            minor_status=MinorStatus.ESTIMATED_MINOR,
            review_reason=ReviewReason.MINOR_CONSENT_REQUIRED,
        )
        near = queued("b", ReviewReason.NEAR_BOUNDARY)
        clustering = self._one_cluster([blocked.face_id, near.face_id])
        queue = build_review_queue(
            [blocked, near], thresholds=self.thresholds, clustering=clustering
        )
        self.assertEqual(len(queue), 1)
        self.assertIs(queue[0].kind, QuestionKind.GRANT_MINOR_CONSENT)

    def test_an_age_question_outranks_a_boundary_question(self) -> None:
        unresolved = auto("a", minor_status=MinorStatus.UNKNOWN,
                          review_reason=ReviewReason.MINOR_STATUS_UNRESOLVED)
        near = queued("b", ReviewReason.NEAR_BOUNDARY)
        clustering = self._one_cluster([unresolved.face_id, near.face_id])
        queue = build_review_queue(
            [unresolved, near], thresholds=self.thresholds, clustering=clustering
        )
        self.assertIs(queue[0].kind, QuestionKind.RESOLVE_MINOR_STATUS)

    def test_the_shown_face_is_the_strongest_evidence_in_the_group(self) -> None:
        weak = queued("a", ReviewReason.NEAR_BOUNDARY, confidence=0.70)
        strong = queued("b", ReviewReason.NEAR_BOUNDARY, confidence=0.89)
        clustering = self._one_cluster([weak.face_id, strong.face_id])
        queue = build_review_queue(
            [weak, strong], thresholds=self.thresholds, clustering=clustering
        )
        self.assertEqual(queue[0].face_ids, (strong.face_id,))
        self.assertEqual(len(queue[0].affected_face_ids), 2)

    def test_informativeness_rewards_big_groups_near_the_boundary(self) -> None:
        big_near = [queued(f"n{i}", ReviewReason.NEAR_BOUNDARY, 0.915) for i in range(6)]
        small_far = [queued("f0", ReviewReason.BELOW_THRESHOLD, 0.61)]
        clustering = self._clusters(
            [[a.face_id for a in big_near], [a.face_id for a in small_far]]
        )
        queue = build_review_queue(
            big_near + small_far, thresholds=self.thresholds, clustering=clustering
        )
        self.assertEqual(len(queue), 2)
        self.assertGreater(queue[0].informativeness, queue[1].informativeness)
        self.assertEqual(len(queue[0].affected_face_ids), 6)

    def test_deferring_pushes_a_question_down_the_queue(self) -> None:
        base = item(QuestionKind.CONFIRM_PERSON, ["a"], ["a", "b", "c"])
        deferred = ReviewItem(
            item_id=base.item_id,
            kind=base.kind,
            face_ids=base.face_ids,
            affected_face_ids=base.affected_face_ids,
            deferrals=3,
        )
        self.assertLess(deferred.informativeness, base.informativeness)

    def test_item_ids_are_a_function_of_the_question(self) -> None:
        assignments = [queued("a", ReviewReason.NEAR_BOUNDARY)]
        first = build_review_queue(assignments, thresholds=self.thresholds)
        second = build_review_queue(assignments, thresholds=self.thresholds)
        self.assertEqual(first[0].item_id, second[0].item_id)

    def test_a_question_must_show_at_least_one_face(self) -> None:
        with self.assertRaises(ReviewError):
            ReviewItem(
                item_id=pid("x"),
                kind=QuestionKind.CONFIRM_PERSON,
                face_ids=(),
                affected_face_ids=(fid("a"),),
            )

    def test_a_shown_face_must_also_be_an_affected_face(self) -> None:
        with self.assertRaises(ReviewError):
            ReviewItem(
                item_id=pid("x"),
                kind=QuestionKind.CONFIRM_PERSON,
                face_ids=(fid("a"),),
                affected_face_ids=(fid("b"),),
            )

    def _one_cluster(self, face_ids):
        return self._clusters([list(face_ids)])

    def _clusters(self, groups):
        observations = []
        for index, group in enumerate(groups):
            for face_id in group:
                observations.append(
                    FaceObservation(
                        face_id=face_id,
                        embedding=embedding(face_id, axis_vector(index * 10)),
                    )
                )
        return cluster_faces(observations, merge_threshold=0.5, run_id="run-1")


class MutationSurvivorTests(unittest.TestCase):
    """Properties the first mutation sweep proved nothing was checking."""

    def test_an_eligible_face_is_never_asked_about_even_if_it_carries_a_reason(
        self,
    ) -> None:
        # SURVIVOR rev14: removing the eligibility skip survived, because the
        # only "eligible" fixture also had review_reason=None and was dropped by
        # the next guard. An eligible face CAN carry a reason -- the
        # minor-status-unresolved path produces exactly that -- and asking about
        # a face that is already correct spends the user's attention on the one
        # part of the library that does not need it.
        settled = auto(
            "a",
            minor_status=MinorStatus.CONFIRMED_ADULT,
            review_reason=ReviewReason.NEAR_BOUNDARY,
        )
        self.assertTrue(settled.eligible_for_automated_output)
        self.assertIsNotNone(settled.review_reason)
        self.assertEqual(
            build_review_queue([settled], thresholds=Thresholds()), ()
        )


class MergeQuestionTests(unittest.TestCase):
    def _library(self, separation: float):
        import math

        cos = 1.0 - separation
        sin = math.sqrt(max(0.0, 1.0 - cos * cos))
        left = axis_vector(0)
        right = [cos * v for v in left]
        right[300] += sin
        faces = [
            FaceObservation(face_id=fid("a"), embedding=embedding(fid("a"), left)),
            FaceObservation(face_id=fid("b"), embedding=embedding(fid("b"), right)),
        ]
        clustering = cluster_faces(faces, merge_threshold=0.5, run_id="run-1")
        return faces, clustering

    def test_a_pair_just_outside_the_threshold_is_asked_about(self) -> None:
        faces, clustering = self._library(0.55)
        queue = build_review_queue(
            [],
            thresholds=Thresholds(),
            clustering=clustering,
            embeddings={f.face_id: f.embedding for f in faces},
        )
        self.assertEqual(len(queue), 1)
        self.assertIs(queue[0].kind, QuestionKind.MERGE_CLUSTERS)
        self.assertEqual(len(queue[0].face_ids), 2)

    def test_a_pair_far_outside_the_window_is_not_asked_about(self) -> None:
        faces, clustering = self._library(0.5 + MERGE_QUESTION_WINDOW + 0.05)
        queue = build_review_queue(
            [],
            thresholds=Thresholds(),
            clustering=clustering,
            embeddings={f.face_id: f.embedding for f in faces},
        )
        self.assertEqual(queue, ())

    def test_an_answered_pair_is_not_asked_about_again(self) -> None:
        faces, clustering = self._library(0.55)
        queue = build_review_queue(
            [],
            thresholds=Thresholds(),
            clustering=clustering,
            embeddings={f.face_id: f.embedding for f in faces},
            constraints=PairConstraints(
                cannot_link=frozenset({frozenset({fid("a"), fid("b")})})
            ),
        )
        self.assertEqual(queue, ())

    def test_merge_questions_need_embeddings(self) -> None:
        _, clustering = self._library(0.55)
        queue = build_review_queue([], thresholds=Thresholds(), clustering=clustering)
        self.assertEqual(queue, ())


# ---------------------------------------------------------------------------
# Answers
# ---------------------------------------------------------------------------


class AnswerValidationTests(unittest.TestCase):
    def state(self, *assignments) -> ReviewState:
        return ReviewState(assignments=tuple(assignments))

    def test_confirming_a_naming_question_requires_a_person(self) -> None:
        question = item(QuestionKind.CONFIRM_PERSON, ["a"], ["a"])
        with self.assertRaises(ReviewError):
            apply_decisions(
                self.state(queued("a", ReviewReason.NEAR_BOUNDARY)),
                [question],
                [ReviewDecision(question.item_id, Answer.CONFIRM, LATER)],
                thresholds=Thresholds(),
            )

    def test_disambiguation_only_accepts_an_offered_candidate(self) -> None:
        question = item(
            QuestionKind.DISAMBIGUATE,
            ["a"],
            ["a"],
            candidate_person_ids=(PERSON, OTHER),
        )
        with self.assertRaises(ReviewError):
            apply_decisions(
                self.state(queued("a", ReviewReason.MULTIPLE_CANDIDATES)),
                [question],
                [
                    ReviewDecision(
                        question.item_id,
                        Answer.CONFIRM,
                        LATER,
                        person_id=pid("stranger"),
                    )
                ],
                thresholds=Thresholds(),
            )

    def test_an_age_question_must_be_answered_with_a_resolved_status(self) -> None:
        question = item(QuestionKind.RESOLVE_MINOR_STATUS, ["a"], ["a"])
        for status in (None, MinorStatus.UNKNOWN, MinorStatus.ESTIMATED_MINOR):
            with self.subTest(status=status):
                with self.assertRaises(ReviewError):
                    apply_decisions(
                        self.state(auto("a")),
                        [question],
                        [
                            ReviewDecision(
                                question.item_id,
                                Answer.CONFIRM,
                                LATER,
                                minor_status=status,
                            )
                        ],
                        thresholds=Thresholds(),
                    )

    def test_granting_consent_requires_a_live_correctly_scoped_ledger_entry(self) -> None:
        question = item(QuestionKind.GRANT_MINOR_CONSENT, ["a"], ["a"])
        state = self.state(auto("a"))
        with self.assertRaises(ReviewError):
            apply_decisions(
                state,
                [question],
                [ReviewDecision(question.item_id, Answer.CONFIRM, LATER)],
                thresholds=Thresholds(),
            )
        with self.assertRaises(ReviewError):
            apply_decisions(
                state,
                [question],
                [
                    ReviewDecision(
                        question.item_id,
                        Answer.CONFIRM,
                        LATER,
                        consent=live_consent(scope="cloud_render"),
                    )
                ],
                thresholds=Thresholds(),
            )

    def test_an_answer_to_a_question_not_in_the_queue_raises(self) -> None:
        with self.assertRaises(ReviewError):
            apply_decisions(
                self.state(auto("a")),
                [],
                [ReviewDecision(pid("ghost"), Answer.DEFER, LATER)],
                thresholds=Thresholds(),
            )

    def test_a_decision_id_is_content_addressed(self) -> None:
        first = ReviewDecision(pid("q"), Answer.CONFIRM, LATER, person_id=PERSON)
        same = ReviewDecision(pid("q"), Answer.CONFIRM, LATER, person_id=PERSON)
        different = ReviewDecision(pid("q"), Answer.CONFIRM, LATER, person_id=OTHER)
        self.assertEqual(first.decision_id, same.decision_id)
        self.assertNotEqual(first.decision_id, different.decision_id)


class ConfirmTests(unittest.TestCase):
    def test_a_confirmation_moves_only_the_faces_that_were_shown(self) -> None:
        # The whole safety argument of review.py. A regression that widens this
        # to the cluster labels two hundred photos on one tap.
        shown = queued("a", ReviewReason.NEAR_BOUNDARY)
        unshown = queued("b", ReviewReason.NEAR_BOUNDARY)
        question = item(QuestionKind.CONFIRM_PERSON, ["a"], ["a", "b"])
        result = apply_decisions(
            ReviewState(assignments=(shown, unshown)),
            [question],
            [ReviewDecision(question.item_id, Answer.CONFIRM, LATER, person_id=PERSON)],
            thresholds=Thresholds(),
        )
        by_face = result.state.by_face
        self.assertIs(by_face[fid("a")].assignment, Assignment.USER_CONFIRMED)
        self.assertIs(by_face[fid("b")].assignment, Assignment.REVIEW_QUEUED)
        self.assertEqual(result.confirmed_face_ids, (fid("a"),))
        self.assertEqual(result.eligibility_granted_face_ids, (fid("a"),))

    def test_a_confirmation_is_recorded_for_the_next_assignment_pass(self) -> None:
        question = item(QuestionKind.CONFIRM_PERSON, ["a"], ["a"])
        result = apply_decisions(
            ReviewState(assignments=(queued("a", ReviewReason.NEAR_BOUNDARY),)),
            [question],
            [ReviewDecision(question.item_id, Answer.CONFIRM, LATER, person_id=PERSON)],
            thresholds=Thresholds(),
        )
        self.assertEqual(result.state.decisions.confirmed, {fid("a"): PERSON})

    def test_naming_a_child_without_consent_is_refused_not_warned(self) -> None:
        child = PersonAssignment(
            face_id=fid("a"),
            assignment=Assignment.UNASSIGNED,
            minor_status=MinorStatus.CONFIRMED_MINOR,
            review_reason=ReviewReason.MINOR_CONSENT_REQUIRED,
        )
        question = item(QuestionKind.CONFIRM_PERSON, ["a"], ["a"])
        result = apply_decisions(
            ReviewState(assignments=(child,)),
            [question],
            [ReviewDecision(question.item_id, Answer.CONFIRM, LATER, person_id=PERSON)],
            thresholds=Thresholds(),
        )
        self.assertEqual(result.refused_face_ids, (fid("a"),))
        self.assertEqual(result.confirmed_face_ids, ())
        self.assertIsNone(result.state.by_face[fid("a")].person_id)

    def test_confirming_a_face_not_in_the_state_raises(self) -> None:
        question = item(QuestionKind.CONFIRM_PERSON, ["a"], ["a"])
        with self.assertRaises(ReviewError):
            apply_decisions(
                ReviewState(),
                [question],
                [
                    ReviewDecision(
                        question.item_id, Answer.CONFIRM, LATER, person_id=PERSON
                    )
                ],
                thresholds=Thresholds(),
            )


class RejectTests(unittest.TestCase):
    def scenario(self):
        shown = auto("a")
        sibling = auto("b")
        confirmed = auto(
            "c",
            assignment=Assignment.USER_CONFIRMED,
            confidence=USER_CONFIRMATION_CONFIDENCE,
            decided_by=DecidedBy.USER,
        )
        unrelated = auto("d", person=OTHER)
        state = ReviewState(assignments=(shown, sibling, confirmed, unrelated))
        question = item(
            QuestionKind.CONFIRM_PERSON, ["a"], ["a", "b", "c", "d"]
        )
        return state, question

    def apply(self, state, question):
        return apply_decisions(
            state,
            [question],
            [
                ReviewDecision(
                    question.item_id, Answer.REJECT, LATER, person_id=PERSON
                )
            ],
            thresholds=Thresholds(),
        )

    def test_the_shown_face_is_rejected_and_loses_its_name(self) -> None:
        state, question = self.scenario()
        result = self.apply(state, question)
        rejected = result.state.by_face[fid("a")]
        self.assertIs(rejected.assignment, Assignment.USER_REJECTED)
        self.assertIsNone(rejected.person_id)
        self.assertFalse(rejected.eligible_for_automated_output)

    def test_rejection_reaches_backwards_into_automated_siblings(self) -> None:
        state, question = self.scenario()
        result = self.apply(state, question)
        sibling = result.state.by_face[fid("b")]
        self.assertIs(sibling.assignment, Assignment.REVIEW_QUEUED)
        self.assertIs(sibling.review_reason, ReviewReason.USER_REPORTED_ERROR)
        self.assertEqual(result.demoted_face_ids, (fid("b"),))

    def test_a_human_confirmation_is_contested_not_overruled(self) -> None:
        state, question = self.scenario()
        result = self.apply(state, question)
        self.assertEqual(result.contested_face_ids, (fid("c"),))
        self.assertIs(
            result.state.by_face[fid("c")].assignment, Assignment.USER_CONFIRMED
        )

    def test_another_person_in_the_same_group_is_untouched(self) -> None:
        state, question = self.scenario()
        result = self.apply(state, question)
        self.assertIs(
            result.state.by_face[fid("d")].assignment,
            Assignment.AUTO_HIGH_CONFIDENCE,
        )
        self.assertEqual(result.state.by_face[fid("d")].person_id, OTHER)

    def test_eligibility_is_revoked_and_reported(self) -> None:
        state, question = self.scenario()
        result = self.apply(state, question)
        self.assertEqual(
            set(result.eligibility_revoked_face_ids), {fid("a"), fid("b")}
        )

    def test_the_rejection_survives_into_the_next_assignment_pass(self) -> None:
        state, question = self.scenario()
        result = self.apply(state, question)
        self.assertEqual(
            result.state.decisions.rejected[fid("a")], frozenset({PERSON})
        )

    def test_a_rejection_that_names_nobody_raises(self) -> None:
        state, question = self.scenario()
        anonymous = ReviewItem(
            item_id=question.item_id,
            kind=QuestionKind.NAME_CLUSTER,
            face_ids=question.face_ids,
            affected_face_ids=question.affected_face_ids,
            subject_person_id=None,
        )
        with self.assertRaises(ReviewError):
            apply_decisions(
                state,
                [anonymous],
                [ReviewDecision(question.item_id, Answer.REJECT, LATER)],
                thresholds=Thresholds(),
            )


class MergeAnswerTests(unittest.TestCase):
    def question(self):
        return item(QuestionKind.MERGE_CLUSTERS, ["a", "b"], ["a", "b"])

    def test_yes_records_a_must_link_and_names_nobody(self) -> None:
        question = self.question()
        result = apply_decisions(
            ReviewState(assignments=(queued("a", ReviewReason.NEAR_BOUNDARY),)),
            [question],
            [ReviewDecision(question.item_id, Answer.CONFIRM, LATER)],
            thresholds=Thresholds(),
        )
        self.assertIn(
            frozenset({fid("a"), fid("b")}), result.state.constraints.must_link
        )
        self.assertIsNone(result.state.by_face[fid("a")].person_id)

    def test_no_records_a_cannot_link(self) -> None:
        question = self.question()
        result = apply_decisions(
            ReviewState(),
            [question],
            [ReviewDecision(question.item_id, Answer.REJECT, LATER)],
            thresholds=Thresholds(),
        )
        self.assertIn(
            frozenset({fid("a"), fid("b")}), result.state.constraints.cannot_link
        )

    def test_a_merge_question_showing_one_face_raises(self) -> None:
        question = item(QuestionKind.MERGE_CLUSTERS, ["a"], ["a"])
        with self.assertRaises(ReviewError):
            apply_decisions(
                ReviewState(),
                [question],
                [ReviewDecision(question.item_id, Answer.CONFIRM, LATER)],
                thresholds=Thresholds(),
            )


class MinorAnswerTests(unittest.TestCase):
    def test_confirming_a_child_strips_the_name_from_the_whole_group(self) -> None:
        state = ReviewState(assignments=(auto("a"), auto("b"), auto("c")))
        question = item(
            QuestionKind.RESOLVE_MINOR_STATUS, ["a"], ["a", "b", "c"]
        )
        result = apply_decisions(
            state,
            [question],
            [
                ReviewDecision(
                    question.item_id,
                    Answer.CONFIRM,
                    LATER,
                    minor_status=MinorStatus.CONFIRMED_MINOR,
                )
            ],
            thresholds=Thresholds(),
        )
        self.assertEqual(
            set(result.stripped_face_ids), {fid("a"), fid("b"), fid("c")}
        )
        for face in ("a", "b", "c"):
            record = result.state.by_face[fid(face)]
            self.assertIsNone(record.person_id)
            self.assertFalse(record.eligible_for_automated_output)
        self.assertEqual(len(result.eligibility_revoked_face_ids), 3)

    def test_confirming_an_adult_applies_only_to_the_face_that_was_shown(self) -> None:
        # An answer that OPENS a gate may not propagate across a cluster: the
        # cluster is a hypothesis, and a child inside it would be declared an
        # adult by somebody looking at a photograph of their brother.
        unresolved = auto(
            "a",
            minor_status=MinorStatus.UNKNOWN,
            review_reason=ReviewReason.MINOR_STATUS_UNRESOLVED,
        )
        neighbour = auto(
            "b",
            minor_status=MinorStatus.UNKNOWN,
            review_reason=ReviewReason.MINOR_STATUS_UNRESOLVED,
        )
        question = item(QuestionKind.RESOLVE_MINOR_STATUS, ["a"], ["a", "b"])
        result = apply_decisions(
            ReviewState(assignments=(unresolved, neighbour)),
            [question],
            [
                ReviewDecision(
                    question.item_id,
                    Answer.CONFIRM,
                    LATER,
                    minor_status=MinorStatus.CONFIRMED_ADULT,
                )
            ],
            thresholds=Thresholds(),
        )
        self.assertTrue(
            result.state.by_face[fid("a")].eligible_for_automated_output
        )
        self.assertFalse(
            result.state.by_face[fid("b")].eligible_for_automated_output
        )
        self.assertEqual(result.eligibility_granted_face_ids, (fid("a"),))

    def test_granting_consent_attaches_it_only_to_confirmed_minors(self) -> None:
        child = PersonAssignment(
            face_id=fid("a"),
            assignment=Assignment.UNASSIGNED,
            minor_status=MinorStatus.CONFIRMED_MINOR,
            labeling_consent=None,
            review_reason=ReviewReason.MINOR_CONSENT_REQUIRED,
        )
        adult = auto("b")
        question = item(QuestionKind.GRANT_MINOR_CONSENT, ["a"], ["a", "b"])
        result = apply_decisions(
            ReviewState(assignments=(child, adult)),
            [question],
            [
                ReviewDecision(
                    question.item_id,
                    Answer.CONFIRM,
                    LATER,
                    consent=live_consent(),
                )
            ],
            thresholds=Thresholds(),
        )
        self.assertIsNotNone(result.state.by_face[fid("a")].labeling_consent)
        self.assertIs(
            result.state.by_face[fid("b")].minor_status, MinorStatus.CONFIRMED_ADULT
        )
        self.assertIsNone(result.state.by_face[fid("b")].labeling_consent)

    def test_granting_consent_does_not_invent_a_name(self) -> None:
        child = PersonAssignment(
            face_id=fid("a"),
            assignment=Assignment.UNASSIGNED,
            minor_status=MinorStatus.CONFIRMED_MINOR,
            review_reason=ReviewReason.MINOR_CONSENT_REQUIRED,
        )
        question = item(QuestionKind.GRANT_MINOR_CONSENT, ["a"], ["a"])
        result = apply_decisions(
            ReviewState(assignments=(child,)),
            [question],
            [
                ReviewDecision(
                    question.item_id, Answer.CONFIRM, LATER, consent=live_consent()
                )
            ],
            thresholds=Thresholds(),
        )
        self.assertIsNone(result.state.by_face[fid("a")].person_id)
        self.assertEqual(result.eligibility_granted_face_ids, ())

    def test_refusing_consent_is_remembered(self) -> None:
        child = PersonAssignment(
            face_id=fid("a"),
            assignment=Assignment.UNASSIGNED,
            minor_status=MinorStatus.CONFIRMED_MINOR,
            review_reason=ReviewReason.MINOR_CONSENT_REQUIRED,
        )
        question = item(QuestionKind.GRANT_MINOR_CONSENT, ["a"], ["a"])
        result = apply_decisions(
            ReviewState(assignments=(child,)),
            [question],
            [ReviewDecision(question.item_id, Answer.REJECT, LATER)],
            thresholds=Thresholds(),
        )
        self.assertIn(fid("a"), result.state.consent_denied_face_ids)


class LedgerTests(unittest.TestCase):
    def test_replaying_a_decision_is_a_no_op(self) -> None:
        question = item(QuestionKind.CONFIRM_PERSON, ["a"], ["a"])
        decision = ReviewDecision(
            question.item_id, Answer.CONFIRM, LATER, person_id=PERSON
        )
        first = apply_decisions(
            ReviewState(assignments=(queued("a", ReviewReason.NEAR_BOUNDARY),)),
            [question],
            [decision],
            thresholds=Thresholds(),
        )
        second = apply_decisions(first.state, [question], [decision], thresholds=Thresholds())
        self.assertEqual(second.ignored_decision_ids, (decision.decision_id,))
        self.assertEqual(second.state.assignments, first.state.assignments)

    def test_deferring_changes_nothing_but_the_count(self) -> None:
        question = item(QuestionKind.CONFIRM_PERSON, ["a"], ["a"])
        before = ReviewState(assignments=(queued("a", ReviewReason.NEAR_BOUNDARY),))
        result = apply_decisions(
            before,
            [question],
            [ReviewDecision(question.item_id, Answer.DEFER, LATER)],
            thresholds=Thresholds(),
        )
        self.assertEqual(result.state.assignments, before.assignments)
        self.assertEqual(result.state.deferrals[question.item_id], 1)

    def test_decisions_apply_in_order(self) -> None:
        question = item(QuestionKind.CONFIRM_PERSON, ["a"], ["a"])
        confirm = ReviewDecision(
            question.item_id, Answer.CONFIRM, LATER, person_id=PERSON
        )
        reject = ReviewDecision(
            question.item_id, Answer.REJECT, "2026-08-19T10:00:00+05:30",
            person_id=PERSON,
        )
        result = apply_decisions(
            ReviewState(assignments=(queued("a", ReviewReason.NEAR_BOUNDARY),)),
            [question],
            [confirm, reject],
            thresholds=Thresholds(),
        )
        self.assertIs(
            result.state.by_face[fid("a")].assignment, Assignment.USER_REJECTED
        )
        self.assertNotIn(fid("a"), result.state.decisions.confirmed)

    def test_the_state_is_not_mutated_in_place(self) -> None:
        question = item(QuestionKind.CONFIRM_PERSON, ["a"], ["a"])
        before = ReviewState(assignments=(queued("a", ReviewReason.NEAR_BOUNDARY),))
        snapshot = before.assignments
        apply_decisions(
            before,
            [question],
            [ReviewDecision(question.item_id, Answer.CONFIRM, LATER, person_id=PERSON)],
            thresholds=Thresholds(),
        )
        self.assertEqual(before.assignments, snapshot)

    def test_eligible_face_ids_reads_the_gate_not_a_stored_flag(self) -> None:
        state = ReviewState(assignments=(auto("a"), queued("b", ReviewReason.NEAR_BOUNDARY)))
        self.assertEqual(state.eligible_face_ids, frozenset({fid("a")}))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
