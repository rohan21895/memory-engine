"""The review queue: what a human is asked, what their answer means, and what
it changes about decisions already made.

This is the other half of hard rule 5. `identity.py` refuses to act on an
uncertain match; without somewhere for those matches to go, that refusal is just
data loss with better manners. The queue is where they go, and it is data --
questions, answers and consequences -- rather than a UI concern, because the
consequences have to be replayable and auditable.

THE ONE RULE THAT SHAPES EVERYTHING HERE

    AN ANSWER APPLIES TO EXACTLY THE FACES THE HUMAN WAS SHOWN.

The tempting design is the opposite: show one face, ask "is this Grandma?", and
on "yes" mark the whole cluster confirmed. Two hundred faces fixed by one tap.
It is also how a stranger who clustered with Grandma acquires her name with a
human's signature on it -- and a `user_confirmed` assignment is the one state
this system trusts unconditionally, so the error is now unreachable by every
later check.

So an answer moves the faces in `ReviewItem.face_ids`, which are the faces the
question rendered, and nothing else. The other two hundred faces move on the
NEXT assignment pass, because the confirmation enrolled a new face into the
person's gallery and every one of them is then re-scored against it. The human
decision changes the EVIDENCE; the mechanical rule re-derives the conclusions.
That is slower by one pass and it is the difference between "ten taps fixed a
thousand photos" and "one tap mislabelled a thousand photos".

Two exceptions, and they are exceptions because they run the other way:

* REJECTION IS RETROACTIVE. "That is not my brother" does not only concern the
  face that was tapped. Every other face that the AUTOMATED path assigned to
  that person out of the same cluster rests on the evidence the human just
  contradicted, so all of them are demoted back into the queue and lose
  eligibility. Faces a human separately confirmed are left alone but reported
  as `contested`, because overruling one human decision with another is a
  product decision and not this module's to make.

* LEARNING SOMEBODY IS A CHILD STRIPS THE NAME, retroactively, from every face
  of theirs, including ones already assigned and already used. Leaving the name
  in place and merely declining to use it would be the wrong shape: a name that
  exists shows up in search, in exports and in the UI, and "we stored it but
  promised not to look" is not a privacy control.

DETERMINISM AND IDEMPOTENCY

Item ids are uuid5 over the question's subject, so the same library produces the
same queue on any machine and an item can be answered from a phone that built
its copy of the queue separately. Decision ids are uuid5 over the decision's
content, so replaying a decision log is a no-op after the first application --
which is what makes the queue survivable across a crash mid-sync.
"""

from __future__ import annotations

import math
import re
import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from enum import Enum

from .clustering import ClusteringResult, PairConstraints
from .embeddings import FaceEmbedding, cosine_distance
from .identity import (
    USER_CONFIRMATION_CONFIDENCE,
    Assignment,
    ConsentRef,
    DecidedBy,
    Decisions,
    MinorStatus,
    PersonAssignment,
    ReviewReason,
    Thresholds,
    may_be_named,
)

__all__ = [
    "Answer",
    "PropagationResult",
    "QuestionKind",
    "ReviewDecision",
    "ReviewError",
    "ReviewItem",
    "ReviewState",
    "apply_decisions",
    "build_review_queue",
]


_BLAKE3_RE = re.compile(r"^[0-9a-f]{64}$")
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)

ITEM_NAMESPACE = uuid.UUID("2b0d5a6f-9c41-5f8a-9d3e-7c1b2a4e6f08")
DECISION_NAMESPACE = uuid.UUID("7d3c1e42-5b8f-5a97-8e6d-1f0a9c4b3e25")

# How far past the merge threshold two clusters may sit and still be worth
# asking about. Beyond this the answer is almost always "different people", and
# a queue full of questions whose answer is obvious trains the user to tap
# through it, which costs more precision than the merges it would have found.
MERGE_QUESTION_WINDOW = 0.15


class ReviewError(Exception):
    """A malformed question, answer, or an answer to a question not asked."""


class QuestionKind(Enum):
    CONFIRM_PERSON = "confirm_person"
    DISAMBIGUATE = "disambiguate"
    NAME_CLUSTER = "name_cluster"
    MERGE_CLUSTERS = "merge_clusters"
    RESOLVE_MINOR_STATUS = "resolve_minor_status"
    GRANT_MINOR_CONSENT = "grant_minor_consent"


class Answer(Enum):
    CONFIRM = "confirm"
    REJECT = "reject"
    DEFER = "defer"


_REASON_PRIORITY = (
    ReviewReason.UNCALIBRATED_THRESHOLD,
    ReviewReason.USER_REPORTED_ERROR,
    ReviewReason.NEW_CLUSTER,
    ReviewReason.NO_EMBEDDING,
    ReviewReason.EXTREME_POSE,
    ReviewReason.LOW_FACE_QUALITY,
    ReviewReason.NEAR_BOUNDARY,
    ReviewReason.BELOW_THRESHOLD,
    ReviewReason.MULTIPLE_CANDIDATES,
    ReviewReason.MINOR_STATUS_UNRESOLVED,
    ReviewReason.MINOR_CONSENT_REQUIRED,
)


# ---------------------------------------------------------------------------
# Questions
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ReviewItem:
    """One question, and the exact scope of the answer.

    `face_ids` is what the human sees and what the answer moves.
    `affected_face_ids` is what the answer will INFLUENCE once the next
    assignment pass runs. Keeping them apart is the whole safety argument of
    this module; collapsing them into one field would make the informativeness
    ranking and the propagation scope the same thing, and propagation would
    silently grow to the size of the cluster.
    """

    item_id: str
    kind: QuestionKind
    face_ids: tuple[str, ...]
    affected_face_ids: tuple[str, ...]
    cluster_ids: tuple[str, ...] = ()
    candidate_person_ids: tuple[str, ...] = ()
    subject_person_id: str | None = None
    reason: ReviewReason | None = None
    boundary_proximity: float = 1.0
    deferrals: int = 0

    def __post_init__(self) -> None:
        if not self.face_ids:
            raise ReviewError(
                f"{self.kind.value}: a question with no face to show is a question "
                "nobody can answer"
            )
        for face_id in self.face_ids + self.affected_face_ids:
            if not _BLAKE3_RE.match(face_id):
                raise ReviewError(f"{face_id!r} is not a face_id digest")
        if not set(self.face_ids) <= set(self.affected_face_ids):
            raise ReviewError(
                f"{self.item_id}: every shown face must also be an affected face"
            )
        for person_id in self.candidate_person_ids:
            if not _UUID_RE.match(person_id):
                raise ReviewError(f"{person_id!r} is not a person_id UUID")
        if not 0.0 <= self.boundary_proximity <= 1.0 or not math.isfinite(
            self.boundary_proximity
        ):
            raise ReviewError("boundary_proximity must be a Unit in [0,1]")
        if self.deferrals < 0:
            raise ReviewError("deferrals cannot be negative")

    @property
    def informativeness(self) -> float:
        """How much of the library this answer is expected to move.

        `affected * proximity` -- the size of the group the answer influences,
        weighted by how close the decision is to the threshold. A confident
        match nobody is unsure about scores near zero however many faces it
        covers; a coin-flip on two faces scores low too. The questions that rise
        are the uncertain ones about big groups, which is the active-learning
        loop the build plan asks for ("ten taps of labeling fixes a thousand
        photos").

        Deferrals divide it down. Without that, the single most informative
        question the user cannot answer -- a blurry face they genuinely do not
        recognise -- sits at the top of the queue forever and the queue is dead.
        """
        return (
            len(self.affected_face_ids)
            * self.boundary_proximity
            / (1.0 + self.deferrals)
        )


def build_review_queue(
    assignments: Iterable[PersonAssignment],
    *,
    thresholds: Thresholds,
    clustering: ClusteringResult | None = None,
    embeddings: Mapping[str, FaceEmbedding] | None = None,
    constraints: PairConstraints | None = None,
    merge_question_window: float = MERGE_QUESTION_WINDOW,
) -> tuple[ReviewItem, ...]:
    """Every question worth asking, most informative first.

    An assignment that is already eligible produces no question: it is either
    user-confirmed or it cleared the automated bar, and asking about it spends
    the user's attention on the one part of the library that is already right.
    """
    materialised = sorted(assignments, key=lambda a: a.face_id)
    membership = clustering.memberships if clustering is not None else {}

    groups: dict[str, list[PersonAssignment]] = {}
    for assignment in materialised:
        if assignment.eligible_for_automated_output:
            continue
        if assignment.review_reason is None:
            continue
        member = membership.get(assignment.face_id)
        key = member.cluster_id if member is not None else f"face:{assignment.face_id}"
        groups.setdefault(key, []).append(assignment)

    items: list[ReviewItem] = []
    for key, members in sorted(groups.items()):
        items.append(_question_for(key, members, thresholds))

    if clustering is not None and embeddings is not None:
        items.extend(
            _merge_questions(
                clustering=clustering,
                embeddings=embeddings,
                constraints=constraints or PairConstraints(),
                window=merge_question_window,
            )
        )

    items.sort(key=lambda item: (-item.informativeness, item.item_id))
    return tuple(items)


def _question_for(
    cluster_key: str,
    members: Sequence[PersonAssignment],
    thresholds: Thresholds,
) -> ReviewItem:
    affected = tuple(sorted(a.face_id for a in members))
    reasons = {a.review_reason for a in members}

    # The most blocking reason wins. Consent and age questions come first
    # because until they are answered NOTHING in the group can become eligible,
    # so any other question about the same faces is asked too early.
    if ReviewReason.MINOR_CONSENT_REQUIRED in reasons:
        kind, reason = QuestionKind.GRANT_MINOR_CONSENT, ReviewReason.MINOR_CONSENT_REQUIRED
    elif ReviewReason.MINOR_STATUS_UNRESOLVED in reasons:
        kind, reason = (
            QuestionKind.RESOLVE_MINOR_STATUS,
            ReviewReason.MINOR_STATUS_UNRESOLVED,
        )
    elif ReviewReason.MULTIPLE_CANDIDATES in reasons:
        kind, reason = QuestionKind.DISAMBIGUATE, ReviewReason.MULTIPLE_CANDIDATES
    else:
        reason = next(r for r in _REASON_PRIORITY if r in reasons)
        has_candidates = any(a.candidates for a in members)
        kind = (
            QuestionKind.CONFIRM_PERSON if has_candidates else QuestionKind.NAME_CLUSTER
        )

    # The face shown is the one with the strongest evidence, so the human is
    # asked about the clearest example rather than the first one by digest. For
    # a RESOLVE_MINOR_STATUS question the strongest evidence is still the best
    # face; the question is about the person, not about the match.
    shown = max(
        members,
        key=lambda a: (
            a.confidence if a.confidence is not None else -1.0,
            a.face_id,
        ),
    )
    candidates = tuple(
        dict.fromkeys(
            candidate.person_id for member in members for candidate in member.candidates
        )
    )[:8]
    subject = shown.person_id or (candidates[0] if candidates else None)

    proximity = _proximity(members, thresholds, kind)
    return ReviewItem(
        item_id=_item_id(kind, affected),
        kind=kind,
        face_ids=(shown.face_id,),
        affected_face_ids=affected,
        cluster_ids=() if cluster_key.startswith("face:") else (cluster_key,),
        candidate_person_ids=candidates,
        subject_person_id=subject,
        reason=reason,
        boundary_proximity=proximity,
    )


def _proximity(
    members: Sequence[PersonAssignment],
    thresholds: Thresholds,
    kind: QuestionKind,
) -> float:
    """How close to the decision boundary this group sits, in [0,1].

    Minor questions are pinned at 1.0. They are not uncertain -- there is no
    confidence involved -- but they are absolutely blocking, and a blocking
    question that ranks below a borderline match is a queue that never unblocks.
    """
    if kind in (QuestionKind.GRANT_MINOR_CONSENT, QuestionKind.RESOLVE_MINOR_STATUS):
        return 1.0
    window = thresholds.automated_output - thresholds.review_floor
    if window <= 0.0:  # pragma: no cover - Thresholds forbids this
        return 1.0
    confidences = [a.confidence for a in members if a.confidence is not None]
    if not confidences:
        # Nothing matched at all. That is maximally informative in the sense
        # that a name would create a whole new person, and there is no distance
        # to a threshold to measure, so it is treated as fully uncertain.
        return 1.0
    best = max(confidences)
    gap = abs(best - thresholds.automated_output)
    return max(0.0, 1.0 - min(1.0, gap / window))


def _merge_questions(
    *,
    clustering: ClusteringResult,
    embeddings: Mapping[str, FaceEmbedding],
    constraints: PairConstraints,
    window: float,
) -> list[ReviewItem]:
    """"Are these two groups the same person?" for pairs that nearly merged.

    Only pairs just outside the merge threshold are asked about. Complete
    linkage over-splits by design (clustering.py), so these questions are how
    the over-split is repaired -- and each one is worth the whole of both
    clusters, which is why they usually top the queue.
    """
    if window < 0.0 or not math.isfinite(window):
        raise ReviewError("merge_question_window must be a finite distance >= 0")
    threshold = clustering.merge_threshold
    clusters = [c for c in clustering.clusters if c.representative_face_ids]
    items: list[ReviewItem] = []
    for index, first in enumerate(clusters):
        for second in clusters[index + 1 :]:
            pairs = [
                (a, b)
                for a in first.representative_face_ids
                for b in second.representative_face_ids
                if a in embeddings and b in embeddings
            ]
            if not pairs:
                continue
            if any(frozenset(pair) in constraints.cannot_link for pair in pairs):
                # Already answered "different people". Asking again is how a
                # product teaches its user that answering does nothing.
                continue
            linkage = max(
                cosine_distance(embeddings[a], embeddings[b]) for a, b in pairs
            )
            if not threshold < linkage <= threshold + window:
                continue
            affected = tuple(
                sorted(first.member_face_ids + second.member_face_ids)
            )
            shown = (
                min(first.representative_face_ids),
                min(second.representative_face_ids),
            )
            items.append(
                ReviewItem(
                    item_id=_item_id(QuestionKind.MERGE_CLUSTERS, shown),
                    kind=QuestionKind.MERGE_CLUSTERS,
                    face_ids=tuple(sorted(shown)),
                    affected_face_ids=affected,
                    cluster_ids=tuple(
                        sorted((first.cluster_id, second.cluster_id))
                    ),
                    reason=ReviewReason.NEAR_BOUNDARY,
                    boundary_proximity=max(
                        0.0, 1.0 - ((linkage - threshold) / window)
                    )
                    if window > 0.0
                    else 1.0,
                )
            )
    return items


def _item_id(kind: QuestionKind, subject: Sequence[str]) -> str:
    return str(
        uuid.uuid5(ITEM_NAMESPACE, f"{kind.value}:" + ",".join(sorted(subject)))
    )


# ---------------------------------------------------------------------------
# Answers
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ReviewDecision:
    """One human answer to one question."""

    item_id: str
    answer: Answer
    decided_at: str
    person_id: str | None = None
    minor_status: MinorStatus | None = None
    consent: ConsentRef | None = None

    def __post_init__(self) -> None:
        if not _UUID_RE.match(self.item_id or ""):
            raise ReviewError(f"item_id must be a UUID, got {self.item_id!r}")
        if not isinstance(self.answer, Answer):
            raise ReviewError("answer must be an Answer")
        if self.person_id is not None and not _UUID_RE.match(self.person_id):
            raise ReviewError(f"person_id must be a UUID, got {self.person_id!r}")
        if self.minor_status is not None and not isinstance(
            self.minor_status, MinorStatus
        ):
            raise ReviewError("minor_status must be a MinorStatus")
        if self.consent is not None and not isinstance(self.consent, ConsentRef):
            raise ReviewError("consent must be a ConsentRef")

    @property
    def decision_id(self) -> str:
        """Content-addressed, so replaying a decision log is idempotent."""
        parts = [
            self.item_id,
            self.answer.value,
            self.decided_at,
            self.person_id or "",
            self.minor_status.value if self.minor_status else "",
            self.consent.ledger_entry_id if self.consent else "",
        ]
        return str(uuid.uuid5(DECISION_NAMESPACE, "|".join(parts)))


def _validate_against(item: ReviewItem, decision: ReviewDecision) -> None:
    """Refuse an answer that does not answer the question that was asked."""
    if decision.answer is Answer.DEFER:
        return
    naming = {
        QuestionKind.CONFIRM_PERSON,
        QuestionKind.DISAMBIGUATE,
        QuestionKind.NAME_CLUSTER,
    }
    if item.kind in naming and decision.answer is Answer.CONFIRM:
        if decision.person_id is None:
            raise ReviewError(
                f"{item.item_id}: confirming a {item.kind.value} question means "
                "naming somebody, so person_id is required"
            )
        if (
            item.kind is QuestionKind.DISAMBIGUATE
            and decision.person_id not in item.candidate_person_ids
        ):
            raise ReviewError(
                f"{item.item_id}: {decision.person_id} was not one of the "
                "candidates this question offered"
            )
    if item.kind is QuestionKind.CONFIRM_PERSON and decision.answer is Answer.REJECT:
        if decision.person_id is None and item.subject_person_id is None:
            raise ReviewError(
                f"{item.item_id}: a rejection has to say who is being rejected"
            )
    if item.kind is QuestionKind.RESOLVE_MINOR_STATUS:
        if decision.minor_status is None:
            raise ReviewError(
                f"{item.item_id}: this question asks for a minor status and the "
                "answer must carry one"
            )
        if decision.minor_status in (
            MinorStatus.UNKNOWN,
            MinorStatus.ESTIMATED_MINOR,
        ):
            raise ReviewError(
                f"{item.item_id}: {decision.minor_status.value} is not an answer, it "
                "is the state that produced the question"
            )
    if (
        item.kind is QuestionKind.GRANT_MINOR_CONSENT
        and decision.answer is Answer.CONFIRM
    ):
        if decision.consent is None:
            raise ReviewError(
                f"{item.item_id}: granting consent requires the ledger entry that "
                "records it; this package does not create consent"
            )
        if not decision.consent.is_live_for(
            "minor_face_labeling", as_of=decision.decided_at
        ):
            raise ReviewError(
                f"{item.item_id}: the supplied consent is not a live "
                "minor_face_labeling consent. A consent granted for cloud "
                "rendering does not authorise naming a child"
            )


# ---------------------------------------------------------------------------
# State and propagation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ReviewState:
    """Everything human answers have accumulated, plus the current assignments.

    Immutable: `apply_decisions` returns a new state. A queue that mutates in
    place cannot be diffed, and the diff is what tells the user what their tap
    actually did.
    """

    assignments: tuple[PersonAssignment, ...] = ()
    decisions: Decisions = field(default_factory=Decisions)
    constraints: PairConstraints = field(default_factory=PairConstraints)
    applied_decision_ids: frozenset[str] = frozenset()
    deferrals: Mapping[str, int] = field(default_factory=dict)
    consent_denied_face_ids: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "assignments",
            tuple(sorted(self.assignments, key=lambda a: a.face_id)),
        )

    @property
    def by_face(self) -> dict[str, PersonAssignment]:
        return {a.face_id: a for a in self.assignments}

    @property
    def eligible_face_ids(self) -> frozenset[str]:
        return frozenset(
            a.face_id for a in self.assignments if a.eligible_for_automated_output
        )


@dataclass(frozen=True)
class PropagationResult:
    """The new state, and an itemised account of what moved.

    Every list here exists because "no silent anything" (hard rule 7) applies
    hardest to the case where a human answer quietly un-names a face that is
    already in a book being printed.
    """

    state: ReviewState
    confirmed_face_ids: tuple[str, ...] = ()
    rejected_face_ids: tuple[str, ...] = ()
    demoted_face_ids: tuple[str, ...] = ()
    contested_face_ids: tuple[str, ...] = ()
    stripped_face_ids: tuple[str, ...] = ()
    refused_face_ids: tuple[str, ...] = ()
    constraints_added: tuple[tuple[str, str], ...] = ()
    ignored_decision_ids: tuple[str, ...] = ()
    eligibility_revoked_face_ids: tuple[str, ...] = ()
    eligibility_granted_face_ids: tuple[str, ...] = ()


def apply_decisions(
    state: ReviewState,
    items: Iterable[ReviewItem],
    decisions: Iterable[ReviewDecision],
    *,
    thresholds: Thresholds,
) -> PropagationResult:
    """Apply human answers to the current state, in the order given.

    Answers to questions not in `items` raise. That is not pedantry: an answer
    carries the scope of the question it answered, and applying it against a
    question the caller cannot produce means the scope is being guessed.

    `thresholds` is required, not defaulted, because a confirmation records the
    operating point that was in force when it was made. Defaulting it would
    write today's threshold onto a decision taken under yesterday's, and
    `threshold_used` is the field that makes retuning an operating point a
    replayable decision rather than a silent behaviour change.
    """
    by_item = {item.item_id: item for item in items}
    working = state
    before_eligible = state.eligible_face_ids
    confirmed: list[str] = []
    rejected: list[str] = []
    demoted: list[str] = []
    contested: list[str] = []
    stripped: list[str] = []
    refused: list[str] = []
    added: list[tuple[str, str]] = []
    ignored: list[str] = []

    for decision in decisions:
        item = by_item.get(decision.item_id)
        if item is None:
            raise ReviewError(
                f"decision names item {decision.item_id}, which is not in the queue "
                "that was passed in"
            )
        if decision.decision_id in working.applied_decision_ids:
            ignored.append(decision.decision_id)
            continue
        _validate_against(item, decision)
        working, effects = _apply_one(working, item, decision, thresholds)
        confirmed.extend(effects.get("confirmed", ()))
        rejected.extend(effects.get("rejected", ()))
        demoted.extend(effects.get("demoted", ()))
        contested.extend(effects.get("contested", ()))
        stripped.extend(effects.get("stripped", ()))
        refused.extend(effects.get("refused", ()))
        added.extend(effects.get("constraints", ()))
        working = replace(
            working,
            applied_decision_ids=working.applied_decision_ids
            | {decision.decision_id},
        )

    after_eligible = working.eligible_face_ids
    return PropagationResult(
        state=working,
        confirmed_face_ids=tuple(sorted(set(confirmed))),
        rejected_face_ids=tuple(sorted(set(rejected))),
        demoted_face_ids=tuple(sorted(set(demoted))),
        contested_face_ids=tuple(sorted(set(contested))),
        stripped_face_ids=tuple(sorted(set(stripped))),
        refused_face_ids=tuple(sorted(set(refused))),
        constraints_added=tuple(sorted(set(added))),
        ignored_decision_ids=tuple(ignored),
        eligibility_revoked_face_ids=tuple(sorted(before_eligible - after_eligible)),
        eligibility_granted_face_ids=tuple(sorted(after_eligible - before_eligible)),
    )


def _apply_one(
    state: ReviewState,
    item: ReviewItem,
    decision: ReviewDecision,
    thresholds: Thresholds,
) -> tuple[ReviewState, dict[str, tuple]]:
    if decision.answer is Answer.DEFER:
        counts = dict(state.deferrals)
        counts[item.item_id] = counts.get(item.item_id, 0) + 1
        return replace(state, deferrals=counts), {}

    if item.kind is QuestionKind.MERGE_CLUSTERS:
        return _apply_merge(state, item, decision)
    if item.kind is QuestionKind.RESOLVE_MINOR_STATUS:
        return _apply_minor_status(state, item, decision)
    if item.kind is QuestionKind.GRANT_MINOR_CONSENT:
        return _apply_consent(state, item, decision)
    if decision.answer is Answer.CONFIRM:
        return _apply_confirm(state, item, decision, thresholds)
    return _apply_reject(state, item, decision)


def _apply_merge(
    state: ReviewState, item: ReviewItem, decision: ReviewDecision
) -> tuple[ReviewState, dict[str, tuple]]:
    """Merge answers change CLUSTERING, not assignments.

    A "yes, same person" does not name anybody -- the clusters may both be
    unnamed -- so nothing about identity changes until the next clustering pass
    honours the constraint and the next assignment pass re-scores the result.
    Writing a person_id here would be inventing an identity out of a similarity
    judgement, which is the exact confusion `cluster` and `identity` are stored
    separately to prevent.
    """
    pair = tuple(sorted(item.face_ids))
    if len(pair) != 2:
        raise ReviewError(
            f"{item.item_id}: a merge question must show exactly two faces"
        )
    link = frozenset(pair)
    if decision.answer is Answer.CONFIRM:
        constraints = PairConstraints(
            cannot_link=state.constraints.cannot_link,
            must_link=state.constraints.must_link | {link},
        )
    else:
        constraints = PairConstraints(
            cannot_link=state.constraints.cannot_link | {link},
            must_link=state.constraints.must_link,
        )
    return replace(state, constraints=constraints), {"constraints": (pair,)}


def _apply_confirm(
    state: ReviewState,
    item: ReviewItem,
    decision: ReviewDecision,
    thresholds: Thresholds,
) -> tuple[ReviewState, dict[str, tuple]]:
    person_id = decision.person_id
    assert person_id is not None  # _validate_against guarantees this
    by_face = state.by_face
    updated: dict[str, PersonAssignment] = dict(by_face)
    confirmed: list[str] = []
    refused: list[str] = []

    for face_id in item.face_ids:
        current = by_face.get(face_id)
        if current is None:
            raise ReviewError(
                f"{item.item_id}: face {face_id} is not in the state being updated"
            )
        if not may_be_named(
            current.minor_status,
            current.labeling_consent,
            as_of=decision.decided_at,
        ):
            # A human named a child's face and the consent does not exist. The
            # tap is refused rather than honoured-with-a-warning: the schema
            # requires the consent before the label is written.
            refused.append(face_id)
            continue
        confirmed.append(face_id)
        updated[face_id] = PersonAssignment(
            face_id=face_id,
            assignment=Assignment.USER_CONFIRMED,
            person_id=person_id,
            # Not `current.confidence`: that is the model's number for a
            # decision the model did not make. See USER_CONFIRMATION_CONFIDENCE.
            confidence=USER_CONFIRMATION_CONFIDENCE,
            threshold_used=thresholds.automated_output,
            threshold_profile=current.threshold_profile,
            minor_status=current.minor_status,
            labeling_consent=current.labeling_consent,
            consent_checked_at=decision.decided_at,
            has_own_embedding=current.has_own_embedding,
            candidates=current.candidates,
            review_reason=None,
            decided_by=DecidedBy.USER,
            decided_at=decision.decided_at,
        )

    new_confirmed = dict(state.decisions.confirmed)
    for face_id in confirmed:
        new_confirmed[face_id] = person_id
    rejected_map = {
        face_id: frozenset(people) - {person_id} if face_id in confirmed else people
        for face_id, people in state.decisions.rejected.items()
    }
    return (
        replace(
            state,
            assignments=tuple(updated.values()),
            decisions=Decisions(confirmed=new_confirmed, rejected=rejected_map),
        ),
        {"confirmed": tuple(confirmed), "refused": tuple(refused)},
    )


def _apply_reject(
    state: ReviewState, item: ReviewItem, decision: ReviewDecision
) -> tuple[ReviewState, dict[str, tuple]]:
    person_id = decision.person_id or item.subject_person_id
    if person_id is None:
        raise ReviewError(f"{item.item_id}: a rejection has to say who is rejected")
    by_face = state.by_face
    updated: dict[str, PersonAssignment] = dict(by_face)
    rejected: list[str] = []
    demoted: list[str] = []
    contested: list[str] = []

    for face_id in item.face_ids:
        current = by_face.get(face_id)
        if current is None:
            raise ReviewError(
                f"{item.item_id}: face {face_id} is not in the state being updated"
            )
        rejected.append(face_id)
        updated[face_id] = PersonAssignment(
            face_id=face_id,
            assignment=Assignment.USER_REJECTED,
            person_id=None,
            confidence=None,
            threshold_used=None,
            threshold_profile=current.threshold_profile,
            minor_status=current.minor_status,
            labeling_consent=current.labeling_consent,
            consent_checked_at=decision.decided_at,
            has_own_embedding=current.has_own_embedding,
            candidates=tuple(
                c for c in current.candidates if c.person_id != person_id
            ),
            review_reason=ReviewReason.USER_REPORTED_ERROR,
            decided_by=DecidedBy.USER,
            decided_at=decision.decided_at,
        )

    # Retroactive half. Everything else in the affected group that the AUTOMATED
    # path attached to this person rests on the evidence just contradicted.
    for face_id in item.affected_face_ids:
        if face_id in item.face_ids:
            continue
        current = updated.get(face_id)
        if current is None or current.person_id != person_id:
            continue
        if current.assignment is Assignment.USER_CONFIRMED:
            contested.append(face_id)
            continue
        demoted.append(face_id)
        updated[face_id] = PersonAssignment(
            face_id=face_id,
            assignment=Assignment.REVIEW_QUEUED,
            person_id=None,
            confidence=current.confidence,
            threshold_used=current.threshold_used,
            threshold_profile=current.threshold_profile,
            minor_status=current.minor_status,
            labeling_consent=current.labeling_consent,
            consent_checked_at=decision.decided_at,
            has_own_embedding=current.has_own_embedding,
            candidates=current.candidates,
            review_reason=ReviewReason.USER_REPORTED_ERROR,
            decided_by=DecidedBy.RULE,
            decided_at=decision.decided_at,
        )

    new_rejected = {k: frozenset(v) for k, v in state.decisions.rejected.items()}
    for face_id in rejected:
        new_rejected[face_id] = new_rejected.get(face_id, frozenset()) | {person_id}
    new_confirmed = {
        face_id: pid
        for face_id, pid in state.decisions.confirmed.items()
        if not (face_id in rejected and pid == person_id)
    }
    return (
        replace(
            state,
            assignments=tuple(updated.values()),
            decisions=Decisions(confirmed=new_confirmed, rejected=new_rejected),
        ),
        {
            "rejected": tuple(rejected),
            "demoted": tuple(demoted),
            "contested": tuple(contested),
        },
    )


def _apply_minor_status(
    state: ReviewState, item: ReviewItem, decision: ReviewDecision
) -> tuple[ReviewState, dict[str, tuple]]:
    """The retroactive strip, and the asymmetry that keeps it safe.

    AN ANSWER THAT CLOSES A GATE MAY PROPAGATE WIDELY.
    AN ANSWER THAT OPENS ONE MAY NOT.

    `confirmed_minor` applies to the WHOLE affected group -- the one place the
    shown-faces-only rule is deliberately broken. The question is about a
    person, not about a match: if this person is a child then every face of
    theirs is a child's face, including the ones already named and already used,
    and restricting the answer to the face on screen would leave the other two
    hundred named.

    `confirmed_adult` applies ONLY to the faces shown. It opens the eligibility
    gate, and the group it would open it for is a CLUSTER -- a hypothesis that
    is allowed to be wrong. A cluster that has quietly absorbed a child's face
    would, under symmetric propagation, have that child declared an adult by
    somebody who was looking at a photograph of their brother. Answering "adult"
    for a whole person is done by setting `Person.minor_status` in the gallery,
    which the next assignment pass applies to every face matched to that person
    -- through the identity path, which has its own thresholds, rather than
    through the clustering path, which does not.
    """
    status = decision.minor_status
    assert status is not None  # _validate_against guarantees this
    by_face = state.by_face
    updated = dict(by_face)
    stripped: list[str] = []

    scope = (
        item.affected_face_ids
        if status is MinorStatus.CONFIRMED_MINOR
        else item.face_ids
    )
    for face_id in scope:
        current = updated.get(face_id)
        if current is None:
            continue
        consent = current.labeling_consent if decision.consent is None else decision.consent
        revised = current.with_minor_status(
            status, consent=consent, as_of=decision.decided_at
        )
        if current.person_id is not None and revised.person_id is None:
            stripped.append(face_id)
        updated[face_id] = revised

    confirmed = {
        face_id: person_id
        for face_id, person_id in state.decisions.confirmed.items()
        if face_id not in stripped
    }
    return (
        replace(
            state,
            assignments=tuple(updated.values()),
            decisions=Decisions(confirmed=confirmed, rejected=state.decisions.rejected),
        ),
        {"stripped": tuple(stripped)},
    )


def _apply_consent(
    state: ReviewState, item: ReviewItem, decision: ReviewDecision
) -> tuple[ReviewState, dict[str, tuple]]:
    """Attach or withhold the child-labeling consent for a group.

    Granting consent does NOT restore a name. The name was never written, so
    there is nothing to restore; the next assignment pass produces one. That
    asymmetry with the strip above is deliberate -- removing permission takes
    effect immediately, granting it takes effect through the normal path.
    """
    by_face = state.by_face
    updated = dict(by_face)
    if decision.answer is Answer.REJECT:
        denied = set(state.consent_denied_face_ids) | set(item.affected_face_ids)
        return replace(state, consent_denied_face_ids=frozenset(denied)), {}

    for face_id in item.affected_face_ids:
        current = updated.get(face_id)
        if current is None:
            continue
        if current.minor_status is not MinorStatus.CONFIRMED_MINOR:
            # The group is a cluster, and a cluster can hold more than one
            # person. Attaching a child-labeling consent to a face nobody has
            # said is a child would re-tag an adult as a minor on the strength
            # of a grouping hypothesis.
            continue
        updated[face_id] = current.with_minor_status(
            MinorStatus.CONFIRMED_MINOR,
            consent=decision.consent,
            as_of=decision.decided_at,
        )
    denied = set(state.consent_denied_face_ids) - set(item.affected_face_ids)
    return (
        replace(
            state,
            assignments=tuple(updated.values()),
            consent_denied_face_ids=frozenset(denied),
        ),
        {},
    )
