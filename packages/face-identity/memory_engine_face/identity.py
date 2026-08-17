"""Person assignment, and the gate that decides what an album is allowed to know.

CLAUDE.md hard rule 5: "A wrong person in a family album is a catastrophic
failure. Automated output uses the high-confidence threshold; uncertain matches
go to the review queue." This module is that rule, expressed as types rather
than as a convention people are asked to follow.

THE THING THIS FILE IS ACTUALLY FOR

`eligible_for_automated_output` is not a field. There is no constructor argument
for it, no setter, no keyword. It is a computed property of `PersonAssignment`,
derived from the assignment state, the confidence, the threshold that was
applied, and the minor-safety envelope. A caller cannot pass it in, cannot
override it, and cannot construct an assignment that claims it without also
carrying the evidence that justifies it -- `__post_init__` refuses.

That is deliberate and it is the difference between a rule and a gate. A boolean
field would be set correctly by every caller written this year and incorrectly by
the one written next year, in a diff that reads fine.

TWO THRESHOLDS, NOT ONE

    confidence >= automated_output   -> auto_high_confidence, may be used unattended
    review_floor <= confidence < automated_output -> review_queued, a human is asked
    confidence < review_floor        -> auto_below_threshold, not worth a human's time

The middle band is the entire point. A single threshold has only two outcomes,
"use it" and "drop it", and drop-it means the system silently forgets a face it
half-recognised. The middle band is where the active-learning loop lives: those
are the questions whose answers move the most faces (build plan 4.2, "ten taps
of labeling fixes a thousand photos").

THE CALIBRATION GATE, AND THE HONEST LIMIT

`Confidence` in the contract means "calibrated probability ... comparable to a
threshold". A raw cosine similarity is not that. It is ordered, not calibrated,
and comparing it to 0.92 produces a number that looks like a probability and is
not one.

There are no ArcFace embeddings in this repository, so no operating point has
been measured here, so this package ships WITHOUT a calibrated confidence. The
consequence is enforced rather than documented: `assign_identities` refuses to
produce a single `auto_high_confidence` assignment unless the calibrator it is
given reports `calibrated = True`, and `FittedCalibrator` cannot be constructed
without stating the eval set digest, the measured precision, and the number of
pairs it was measured over. With the default `UncalibratedSimilarity`, every
match that would have been automatic goes to the review queue instead.

So: the automated path is closed until somebody measures it. That is the correct
state for a system whose failure mode is a stranger's face in a family book, and
it means no precision number is claimed anywhere in this package that was not
measured on real data.

MINORS: TWO SEPARATE GATES, BECAUSE THEY ARE TWO SEPARATE QUESTIONS

face-record.schema.json is explicit that `unknown` "is NOT treated as adult" and
that "'nobody asked' is not the same as 'no'". Two gates fall out of that, and
conflating them would break one of them:

  1. MAY THIS FACE BE NAMED AT ALL?  Blocked only for `confirmed_minor` with no
     live, correctly-scoped `minor_face_labeling` consent. That face is still
     detected, still counted, still laid out safely -- it is never given a
     person_id, by any path, including a human tapping a name in the UI. A tap
     is not a consent-ledger entry, and the schema requires the consent object
     to exist before the label is written rather than being back-filled after.

  2. MAY THIS FACE BE USED UNATTENDED?  Requires the age question to have been
     ANSWERED: `confirmed_adult`, or `confirmed_minor` with live consent.
     `unknown` and `estimated_minor` may be named and searched; they are never
     eligible for automated output.

ASSUMPTION, STATED PLAINLY: gate 2 means a freshly scanned library produces
ZERO eligible faces until somebody resolves minor status, because `unknown` is
the schema default and no age model exists in the registry. That is the intended
reading of "unknown is not adult", and it is a real product cost: the first
automated album cannot name anybody until the user answers a handful of "is this
a child?" questions. It is survivable because it does not touch face SAFETY --
print trim-zone checks run on face BOXES and need no identity at all (see
records.face_boxes_for_layout). Identity eligibility gates NAMING. It never
gates safety, and wiring one to the other would be a way to turn a privacy
control into a print defect.

The conservative direction of every choice here: minor status propagates from
the PERSON to the face and takes the more cautious of the two, so learning that
a person is a child retroactively removes the name from every face of theirs,
including ones already assigned.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Protocol, runtime_checkable

from .embeddings import FaceEmbedding, cosine_similarity

__all__ = [
    "AMBIGUITY_MARGIN_DEFAULT",
    "Assignment",
    "AutomatedFaceSet",
    "Calibrator",
    "Candidate",
    "ConsentRef",
    "Decisions",
    "DecidedBy",
    "FaceContext",
    "FittedCalibrator",
    "IdentityError",
    "IneligibleFace",
    "MIN_AUTOMATED_OUTPUT_CONFIDENCE",
    "MIN_CALIBRATION_PAIRS",
    "MinorStatus",
    "Person",
    "USER_CONFIRMATION_CONFIDENCE",
    "PersonAssignment",
    "PersonGallery",
    "PRECISION_TARGET",
    "ReviewReason",
    "ThresholdProfile",
    "Thresholds",
    "UncalibratedSimilarity",
    "assign_identities",
    "effective_minor_status",
    "may_be_named",
]


_BLAKE3_RE = re.compile(r"^[0-9a-f]{64}$")
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)

# The build plan's quality gate (section 7): ">=99% precision at the confidence
# threshold used for automated output".
PRECISION_TARGET = 0.99

# No `automated_output` threshold below this may be configured, and no
# assignment claiming eligibility may cite one. Without this floor the schema's
# stated invariant -- "eligible requires confidence >= threshold_used" -- is
# satisfiable by setting threshold_used to 0.1, which is a true statement and a
# worthless one. The floor is what makes the comparison mean something.
MIN_AUTOMATED_OUTPUT_CONFIDENCE = 0.90

# A precision of 1.000 over forty pairs is not evidence of anything. Any
# operating point offered to this module has to have been measured over at least
# this many labelled pairs.
MIN_CALIBRATION_PAIRS = 2000

# How far the best candidate must beat the runner-up before "best" means
# anything. Siblings and twins are the case this exists for: a single best-match
# number hides them completely.
AMBIGUITY_MARGIN_DEFAULT = 0.05

# The confidence written onto a face a human confirmed. It is 1.0 because a
# person looked at the photograph and said so, and `decided_by: user` is what
# records where the number came from -- nothing reads this as a model output.
#
# The alternative, carrying forward whatever similarity the model last computed,
# is worse in a specific way: it reports the MODEL's uncertainty on a decision
# the model did not make, and a later "why was this face used?" trace would
# blame a 0.71 for a choice a human made.
USER_CONFIRMATION_CONFIDENCE = 1.0


class IdentityError(Exception):
    """An assignment, threshold set, gallery or calibrator that cannot be trusted."""


class IneligibleFace(IdentityError):
    """An ineligible assignment was offered to something that requires eligibility."""


# ---------------------------------------------------------------------------
# Contract-mirroring enums
# ---------------------------------------------------------------------------


class Assignment(Enum):
    """FaceRecord.identity.assignment."""

    UNASSIGNED = "unassigned"
    USER_CONFIRMED = "user_confirmed"
    USER_REJECTED = "user_rejected"
    AUTO_HIGH_CONFIDENCE = "auto_high_confidence"
    AUTO_BELOW_THRESHOLD = "auto_below_threshold"
    REVIEW_QUEUED = "review_queued"
    AMBIGUOUS_MULTIPLE_CANDIDATES = "ambiguous_multiple_candidates"


class ThresholdProfile(Enum):
    AUTOMATED_OUTPUT = "automated_output"
    REVIEW_QUEUE = "review_queue"
    SEARCH_ONLY = "search_only"


class DecidedBy(Enum):
    MODEL = "model"
    USER = "user"
    RULE = "rule"


class MinorStatus(Enum):
    UNKNOWN = "unknown"
    ESTIMATED_MINOR = "estimated_minor"
    CONFIRMED_MINOR = "confirmed_minor"
    CONFIRMED_ADULT = "confirmed_adult"


# Most cautious first. Used to combine a face's status with its person's: the
# more cautious of the two always wins, so a person confirmed to be a child
# makes every one of their faces a child's face regardless of what the face
# itself was tagged with.
_CAUTION_ORDER: tuple[MinorStatus, ...] = (
    MinorStatus.CONFIRMED_MINOR,
    MinorStatus.ESTIMATED_MINOR,
    MinorStatus.UNKNOWN,
    MinorStatus.CONFIRMED_ADULT,
)


class ReviewReason(Enum):
    """Why a face is not being used unattended.

    A superset of FaceRecord.identity.review_reason. The four values below the
    line have no contract equivalent and serialise as null (records.py maps
    them); the contract cannot express them today and changing it needs Codex's
    sign-off, so the richer reason is kept in the queue where it is acted on.
    Losing it in the record costs an explanation, not a decision.
    """

    BELOW_THRESHOLD = "below_threshold"
    NEAR_BOUNDARY = "near_boundary"
    MULTIPLE_CANDIDATES = "multiple_candidates"
    NEW_CLUSTER = "new_cluster"
    USER_REPORTED_ERROR = "user_reported_error"
    LOW_FACE_QUALITY = "low_face_quality"
    EXTREME_POSE = "extreme_pose"
    # --- no contract equivalent ---
    MINOR_CONSENT_REQUIRED = "minor_consent_required"
    MINOR_STATUS_UNRESOLVED = "minor_status_unresolved"
    UNCALIBRATED_THRESHOLD = "uncalibrated_threshold"
    NO_EMBEDDING = "no_embedding"

    @property
    def contract_value(self) -> str | None:
        if self in _NON_CONTRACT_REASONS:
            return None
        return self.value


_NON_CONTRACT_REASONS = frozenset(
    {
        ReviewReason.MINOR_CONSENT_REQUIRED,
        ReviewReason.MINOR_STATUS_UNRESOLVED,
        ReviewReason.UNCALIBRATED_THRESHOLD,
        ReviewReason.NO_EMBEDDING,
    }
)


# ---------------------------------------------------------------------------
# Consent
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ConsentRef:
    """A pointer into the consent ledger owned by services/api.

    This package never creates consent. It reads one, checks that it is the
    right kind and still live, and refuses to proceed otherwise. A revoked
    consent that still validates is worse than no consent at all, because it
    reads as permission (face-record.schema.json says exactly this).
    """

    ledger_entry_id: str
    scope: str
    granted_at: str
    expires_at: str | None = None
    revoked_at: str | None = None

    def __post_init__(self) -> None:
        if not _UUID_RE.match(self.ledger_entry_id or ""):
            raise IdentityError(
                f"consent ledger_entry_id must be a lowercase UUID, got "
                f"{self.ledger_entry_id!r}"
            )
        for name in ("granted_at", "expires_at", "revoked_at"):
            value = getattr(self, name)
            if value is None:
                continue
            if _parse_timestamp(value) is None:
                raise IdentityError(
                    f"consent {name} must be an RFC 3339 instant with an offset, "
                    f"got {value!r}"
                )

    def is_live_for(self, scope: str, *, as_of: str) -> bool:
        """True only when this consent authorises `scope` right now.

        Every branch is written to fail closed. An unparseable `as_of`, a
        missing offset, an expiry that cannot be read -- all of them return
        False rather than being skipped, because a consent check that cannot
        run is not a consent check that passed.
        """
        if self.scope != scope:
            return False
        if self.revoked_at is not None:
            return False
        now = _parse_timestamp(as_of)
        if now is None:
            return False
        granted = _parse_timestamp(self.granted_at)
        if granted is None or granted > now:
            return False
        if self.expires_at is not None:
            expires = _parse_timestamp(self.expires_at)
            if expires is None or expires <= now:
                return False
        return True


def _parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        # LocalDateTime is a different contract type for a reason: a wall-clock
        # reading with no offset cannot be ordered against an instant, and
        # guessing a zone here would make consent expire at different moments in
        # different places.
        return None
    return parsed


# ---------------------------------------------------------------------------
# Thresholds and calibration
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Thresholds:
    """The operating points. Two, plus the corrections that tighten them."""

    automated_output: float = 0.92
    review_floor: float = 0.60
    ambiguity_margin: float = AMBIGUITY_MARGIN_DEFAULT
    # face-record.schema.json, FaceAttributes.yaw_deg: "Beyond about +/-45
    # degrees yaw, recognition confidence degrades sharply and the
    # automated-output threshold should tighten." Implemented as a tightening
    # rather than a rejection so the record shows the higher bar in
    # `threshold_used` and a genuinely certain profile shot can still pass it.
    extreme_yaw_deg: float = 45.0
    extreme_pose_penalty: float = 0.04
    # Below this fused face quality the embedding is not worth a decision. The
    # value is a starting point, not a measurement.
    quality_floor: float = 0.35

    def __post_init__(self) -> None:
        for name in (
            "automated_output",
            "review_floor",
            "ambiguity_margin",
            "extreme_yaw_deg",
            "extreme_pose_penalty",
            "quality_floor",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise IdentityError(f"Thresholds.{name} must be a number")
            if not math.isfinite(value):
                raise IdentityError(f"Thresholds.{name} must be finite, got {value!r}")
        if not 0.0 <= self.automated_output <= 1.0:
            raise IdentityError("automated_output must be a Confidence in [0,1]")
        if self.automated_output < MIN_AUTOMATED_OUTPUT_CONFIDENCE:
            raise IdentityError(
                f"automated_output {self.automated_output} is below the "
                f"{MIN_AUTOMATED_OUTPUT_CONFIDENCE} floor. Lowering the bar for "
                "unattended output is a decision about putting the wrong person "
                "in a family album, and it is not made by passing an argument"
            )
        if not 0.0 <= self.review_floor < self.automated_output:
            raise IdentityError(
                f"review_floor {self.review_floor} must be in [0, automated_output); "
                "collapsing the two thresholds removes the review band, which is "
                "where every uncertain match is supposed to go"
            )
        if not 0.0 <= self.ambiguity_margin < 1.0:
            raise IdentityError("ambiguity_margin must be in [0,1)")
        if not 0.0 <= self.extreme_yaw_deg <= 180.0:
            raise IdentityError("extreme_yaw_deg must be in [0,180]")
        if self.extreme_pose_penalty < 0.0:
            raise IdentityError(
                "extreme_pose_penalty must be >= 0; a negative penalty would "
                "LOWER the bar for exactly the faces the schema says to raise it for"
            )
        if not 0.0 <= self.quality_floor <= 1.0:
            raise IdentityError("quality_floor must be a Unit in [0,1]")


@runtime_checkable
class Calibrator(Protocol):
    """Maps a cosine similarity onto the contract's `Confidence`."""

    @property
    def space(self) -> str: ...

    @property
    def calibrated(self) -> bool:
        """True only when the mapping was fitted on measured, labelled data."""

    @property
    def operating_confidence(self) -> float:
        """The confidence value the fitted operating point lands on."""

    def confidence(self, similarity: float) -> float: ...


@dataclass(frozen=True, slots=True)
class UncalibratedSimilarity:
    """The default: cosine similarity rescaled to [0,1], and honest about it.

    Monotone, so ordering and the review queue's ranking are meaningful. NOT a
    probability, so `calibrated` is False and `assign_identities` will not
    produce a single automated assignment while this is in use.
    """

    space: str = "arcface_buffalo_l_512"

    @property
    def calibrated(self) -> bool:
        return False

    @property
    def operating_confidence(self) -> float:
        return float("nan")

    def confidence(self, similarity: float) -> float:
        if not math.isfinite(similarity):
            raise IdentityError(f"similarity must be finite, got {similarity!r}")
        value = (similarity + 1.0) / 2.0
        return min(1.0, max(0.0, value))


@dataclass(frozen=True, slots=True)
class FittedCalibrator:
    """An operating point somebody measured, with the measurement attached.

    Every field is a question that has to be answered before the automated path
    opens. They are constructor arguments rather than documentation because a
    calibrator with no provenance is indistinguishable, at the call site, from
    one with provenance.
    """

    space: str
    operating_similarity: float
    operating_confidence: float
    measured_precision: float
    evaluated_pairs: int
    inputs_digest: str
    fitted_on: str
    precision_target: float = PRECISION_TARGET

    def __post_init__(self) -> None:
        if not isinstance(self.space, str) or not self.space:
            raise IdentityError("FittedCalibrator.space must be a VectorSpace name")
        for name in (
            "operating_similarity",
            "operating_confidence",
            "measured_precision",
            "precision_target",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise IdentityError(f"FittedCalibrator.{name} must be a number")
            if not math.isfinite(value):
                raise IdentityError(f"FittedCalibrator.{name} must be finite")
        if not -1.0 < self.operating_similarity < 1.0:
            raise IdentityError(
                "operating_similarity must be a cosine similarity in (-1,1); at "
                "exactly 1.0 nothing but an identical vector could ever clear it"
            )
        if not MIN_AUTOMATED_OUTPUT_CONFIDENCE <= self.operating_confidence <= 1.0:
            raise IdentityError(
                f"operating_confidence must be in "
                f"[{MIN_AUTOMATED_OUTPUT_CONFIDENCE}, 1.0]"
            )
        if not 0.0 <= self.measured_precision <= 1.0:
            raise IdentityError("measured_precision must be a Unit in [0,1]")
        if self.measured_precision < self.precision_target:
            raise IdentityError(
                f"measured precision {self.measured_precision} is below the "
                f"{self.precision_target} target from build plan section 7; an "
                "operating point that misses the target is not an operating point"
            )
        if isinstance(self.evaluated_pairs, bool) or not isinstance(
            self.evaluated_pairs, int
        ):
            raise IdentityError("evaluated_pairs must be an int")
        if self.evaluated_pairs < MIN_CALIBRATION_PAIRS:
            raise IdentityError(
                f"{self.evaluated_pairs} labelled pairs is below the "
                f"{MIN_CALIBRATION_PAIRS} minimum; a precision measured over a "
                "handful of pairs is a coincidence with a decimal point"
            )
        if not _BLAKE3_RE.match(self.inputs_digest or ""):
            raise IdentityError(
                "inputs_digest must be the BLAKE3 digest of the evaluation set, so "
                "the measurement can be reproduced and the eval harness can refuse "
                "a comparison against different data"
            )
        if _parse_date(self.fitted_on) is None:
            raise IdentityError(
                f"fitted_on must be an ISO date, got {self.fitted_on!r}"
            )

    @property
    def calibrated(self) -> bool:
        return True

    def confidence(self, similarity: float) -> float:
        """Piecewise linear, continuous, and exact at the operating point.

        Exactness matters: `confidence(operating_similarity)` must equal
        `operating_confidence` to the bit, because that is what makes
        "confidence >= automated_output" and "similarity >= the similarity we
        measured precision at" the same test. An approximation here would move
        the real operating point by an amount nobody could see.
        """
        if not math.isfinite(similarity):
            raise IdentityError(f"similarity must be finite, got {similarity!r}")
        s = min(1.0, max(-1.0, similarity))
        if s == self.operating_similarity:
            return self.operating_confidence
        if s < self.operating_similarity:
            span = self.operating_similarity - (-1.0)
            return self.operating_confidence * (s - (-1.0)) / span
        span = 1.0 - self.operating_similarity
        if span == 0.0:  # pragma: no cover - excluded by __post_init__
            return 1.0
        rise = 1.0 - self.operating_confidence
        return self.operating_confidence + rise * (s - self.operating_similarity) / span


def _parse_date(value: object) -> object | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# People
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Person:
    """A user-facing identity. Created by labeling, never by clustering alone.

    `enrolled` holds ONLY embeddings of faces a human confirmed. Enrolling
    auto-assigned faces is the standard way a face gallery rots: one wrong match
    becomes gallery evidence, which makes the next wrong match easier, and after
    a hundred photos the person is quietly two people. The gallery only ever
    grows on a human decision.
    """

    person_id: str
    enrolled: tuple[FaceEmbedding, ...] = ()
    minor_status: MinorStatus = MinorStatus.UNKNOWN
    labeling_consent: ConsentRef | None = None

    def __post_init__(self) -> None:
        if not _UUID_RE.match(self.person_id or ""):
            raise IdentityError(
                f"person_id must be a lowercase UUID, got {self.person_id!r}"
            )
        if not isinstance(self.enrolled, tuple):
            raise IdentityError("Person.enrolled must be a tuple")
        spaces = {embedding.space for embedding in self.enrolled}
        if len(spaces) > 1:
            raise IdentityError(
                f"{self.person_id}: gallery mixes spaces {sorted(spaces)}"
            )
        if not isinstance(self.minor_status, MinorStatus):
            raise IdentityError("Person.minor_status must be a MinorStatus")


@dataclass(frozen=True)
class PersonGallery:
    """Everyone the system has been taught, and how many faces it was taught with."""

    people: tuple[Person, ...] = ()
    # How many of the closest enrolled faces a similarity is averaged over. One
    # (nearest-neighbour) is maximally sensitive to a single bad enrolment; the
    # whole gallery requires every photo of a person to agree, which no real
    # person's photos do. Three is a compromise, and it is a guess rather than a
    # measurement -- see README, "the eval that would measure this".
    top_k: int = 3

    def __post_init__(self) -> None:
        seen: set[str] = set()
        for person in self.people:
            if person.person_id in seen:
                raise IdentityError(f"duplicate person_id in gallery: {person.person_id}")
            seen.add(person.person_id)
        if isinstance(self.top_k, bool) or not isinstance(self.top_k, int):
            raise IdentityError("PersonGallery.top_k must be an int")
        if self.top_k < 1:
            raise IdentityError("PersonGallery.top_k must be >= 1")
        object.__setattr__(
            self, "people", tuple(sorted(self.people, key=lambda p: p.person_id))
        )

    @property
    def by_id(self) -> dict[str, Person]:
        return {person.person_id: person for person in self.people}

    def similarity(self, embedding: FaceEmbedding, person: Person) -> float | None:
        """Mean of the top-k similarities to this person's enrolled faces.

        None when the person has no enrolled faces: a person nobody has taught
        the system about has no similarity, and returning 0.0 or -1.0 instead
        would put them in the candidate list at the bottom, where a
        `max()` over an empty-ish gallery could still select them.
        """
        if not person.enrolled:
            return None
        scores = sorted(
            (cosine_similarity(embedding, enrolled) for enrolled in person.enrolled),
            reverse=True,
        )
        top = scores[: max(1, self.top_k)]
        return math.fsum(top) / len(top)


# ---------------------------------------------------------------------------
# The assignment
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Candidate:
    person_id: str
    confidence: float

    def __post_init__(self) -> None:
        if not _UUID_RE.match(self.person_id or ""):
            raise IdentityError(f"candidate person_id must be a UUID, got {self.person_id!r}")
        if (
            isinstance(self.confidence, bool)
            or not isinstance(self.confidence, (int, float))
            or not math.isfinite(self.confidence)
            or not 0.0 <= self.confidence <= 1.0
        ):
            raise IdentityError(
                f"candidate confidence must be a Confidence in [0,1], got "
                f"{self.confidence!r}"
            )


@dataclass(frozen=True, slots=True)
class PersonAssignment:
    """Who we say this face is, and whether that is sure enough to act on.

    Note what is NOT here: a field called `eligible_for_automated_output`. It is
    a property below. Adding the field back would take one line and would
    silently undo the only structural guarantee in this package.
    """

    face_id: str
    assignment: Assignment
    person_id: str | None = None
    confidence: float | None = None
    threshold_used: float | None = None
    threshold_profile: ThresholdProfile = ThresholdProfile.AUTOMATED_OUTPUT
    minor_status: MinorStatus = MinorStatus.UNKNOWN
    labeling_consent: ConsentRef | None = None
    consent_checked_at: str | None = None
    has_own_embedding: bool = True
    candidates: tuple[Candidate, ...] = ()
    review_reason: ReviewReason | None = None
    decided_by: DecidedBy | None = None
    decided_at: str | None = None

    def __post_init__(self) -> None:
        if not _BLAKE3_RE.match(self.face_id or ""):
            raise IdentityError(
                f"face_id must be a lowercase 64-hex BLAKE3 digest, got "
                f"{self.face_id!r}"
            )
        if not isinstance(self.assignment, Assignment):
            raise IdentityError("assignment must be an Assignment")
        if not isinstance(self.minor_status, MinorStatus):
            raise IdentityError("minor_status must be a MinorStatus")
        if not isinstance(self.threshold_profile, ThresholdProfile):
            raise IdentityError("threshold_profile must be a ThresholdProfile")
        if self.person_id is not None and not _UUID_RE.match(self.person_id):
            raise IdentityError(f"person_id must be a UUID, got {self.person_id!r}")
        for name in ("confidence", "threshold_used"):
            value = getattr(self, name)
            if value is None:
                continue
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or not 0.0 <= value <= 1.0
            ):
                raise IdentityError(
                    f"{self.face_id}: {name} must be a Confidence in [0,1] or None, "
                    f"got {value!r}"
                )
        if not isinstance(self.candidates, tuple):
            raise IdentityError("candidates must be a tuple")
        if len(self.candidates) > 8:
            raise IdentityError(
                "candidates exceeds the contract's maxItems of 8"
            )
        candidate_ids = [candidate.person_id for candidate in self.candidates]
        if len(set(candidate_ids)) != len(candidate_ids):
            raise IdentityError(f"{self.face_id}: duplicate person_id in candidates")

        # -- state consistency -------------------------------------------------
        committed = {Assignment.USER_CONFIRMED, Assignment.AUTO_HIGH_CONFIDENCE}
        if self.assignment in committed and self.person_id is None:
            raise IdentityError(
                f"{self.face_id}: {self.assignment.value} without a person_id is a "
                "decision about nobody"
            )
        if self.assignment not in committed and self.person_id is not None:
            raise IdentityError(
                f"{self.face_id}: {self.assignment.value} carries a person_id. Only a "
                "committed assignment names somebody; a guess belongs in candidates, "
                "where nothing downstream reads it as an identity"
            )
        if self.assignment is Assignment.AUTO_HIGH_CONFIDENCE and (
            self.confidence is None or self.threshold_used is None
        ):
            raise IdentityError(
                f"{self.face_id}: auto_high_confidence must state the confidence and "
                "the threshold that justified it"
            )
        if (
            self.assignment
            in {
                Assignment.AUTO_BELOW_THRESHOLD,
                Assignment.REVIEW_QUEUED,
                Assignment.AMBIGUOUS_MULTIPLE_CANDIDATES,
            }
            and self.review_reason is None
        ):
            raise IdentityError(
                f"{self.face_id}: {self.assignment.value} must say why. A face in the "
                "queue with no reason is a question nobody can phrase"
            )
        if self.review_reason is not None and not isinstance(
            self.review_reason, ReviewReason
        ):
            raise IdentityError("review_reason must be a ReviewReason")
        if self.decided_by is not None and not isinstance(self.decided_by, DecidedBy):
            raise IdentityError("decided_by must be a DecidedBy")
        for name in ("decided_at", "consent_checked_at"):
            value = getattr(self, name)
            if value is not None and _parse_timestamp(value) is None:
                raise IdentityError(
                    f"{self.face_id}: {name} must be an RFC 3339 instant with an "
                    f"offset, got {value!r}"
                )

        # -- the contract's positive invariant ---------------------------------
        # face-record.schema.json: `eligible_for_automated_output: true` REQUIRES
        # person_id, confidence and threshold_used. Enforced here rather than
        # discovered at serialisation, because an assignment that cannot be
        # written down is not a valid assignment -- and the first version of this
        # class produced exactly that record for every user-confirmed face.
        if self.eligible_for_automated_output and (
            self.confidence is None or self.threshold_used is None
        ):
            raise IdentityError(
                f"{self.face_id}: an eligible assignment must state the confidence "
                "and the threshold that justified it. The contract refuses the "
                "record otherwise, and 'we were sure but cannot say why' is not a "
                "justification"
            )

        # -- the minor gate ----------------------------------------------------
        if self.person_id is not None and not may_be_named(
            self.minor_status,
            self.labeling_consent,
            as_of=self.consent_checked_at,
        ):
            raise IdentityError(
                f"{self.face_id}: a confirmed minor cannot carry a person_id without "
                "a live minor_face_labeling consent. The face is still detected and "
                "counted; it is not named"
            )

    # -- THE GATE -----------------------------------------------------------

    @property
    def eligible_for_automated_output(self) -> bool:
        """Whether album, film and reel selection may treat this as a known person.

        Read the conditions as a list of the ways this has gone wrong elsewhere:

        * no person_id -> there is nothing to be eligible about.
        * no embedding of its own -> a face that inherited its cluster from a
          track has no evidence of its own. Membership is inheritable;
          permission is not. (A human confirming the face IS evidence, so
          user_confirmed is exempt.)
        * minor status unresolved -> `unknown` is not adult.
        * threshold below the floor -> "confidence >= threshold_used" is
          trivially satisfiable with a small enough threshold.
        * profile is not automated_output -> `search_only` is a permissive
          operating point on purpose; a wrong search hit is a shrug. Letting a
          search-tuned confidence gate an album would move that shrug into print.
        """
        if self.person_id is None:
            return False
        if not self._minor_status_resolved():
            return False
        if self.assignment is Assignment.USER_CONFIRMED:
            return True
        if self.assignment is not Assignment.AUTO_HIGH_CONFIDENCE:
            return False
        if not self.has_own_embedding:
            return False
        if self.threshold_profile is not ThresholdProfile.AUTOMATED_OUTPUT:
            return False
        confidence, threshold = self.confidence, self.threshold_used
        if confidence is None or threshold is None:
            return False
        if not math.isfinite(confidence) or not math.isfinite(threshold):
            return False
        if threshold < MIN_AUTOMATED_OUTPUT_CONFIDENCE:
            return False
        return confidence >= threshold

    def _minor_status_resolved(self) -> bool:
        if self.minor_status is MinorStatus.CONFIRMED_ADULT:
            return True
        if self.minor_status is MinorStatus.CONFIRMED_MINOR:
            return may_be_named(
                self.minor_status,
                self.labeling_consent,
                as_of=self.consent_checked_at,
            )
        return False

    def with_minor_status(
        self,
        status: MinorStatus,
        *,
        consent: ConsentRef | None,
        as_of: str,
    ) -> "PersonAssignment":
        """Re-derive this assignment under a newly answered age question.

        Strips the name when the answer forbids it, rather than leaving a label
        that the gate would merely refuse to use. A name that exists but may not
        be used still appears in search, in exports, and in the UI.
        """
        if not may_be_named(status, consent, as_of=as_of):
            return PersonAssignment(
                face_id=self.face_id,
                assignment=Assignment.UNASSIGNED,
                person_id=None,
                confidence=None,
                threshold_used=None,
                threshold_profile=self.threshold_profile,
                minor_status=status,
                labeling_consent=consent,
                consent_checked_at=as_of,
                has_own_embedding=self.has_own_embedding,
                candidates=self.candidates,
                review_reason=ReviewReason.MINOR_CONSENT_REQUIRED,
                decided_by=DecidedBy.RULE,
                decided_at=as_of,
            )
        return PersonAssignment(
            face_id=self.face_id,
            assignment=self.assignment,
            person_id=self.person_id,
            confidence=self.confidence,
            threshold_used=self.threshold_used,
            threshold_profile=self.threshold_profile,
            minor_status=status,
            labeling_consent=consent,
            consent_checked_at=as_of,
            has_own_embedding=self.has_own_embedding,
            candidates=self.candidates,
            review_reason=self.review_reason,
            decided_by=self.decided_by,
            decided_at=self.decided_at,
        )


def may_be_named(
    status: MinorStatus,
    consent: ConsentRef | None,
    *,
    as_of: str | None,
) -> bool:
    """Whether a face in this state may carry a person_id at all.

    Only `confirmed_minor` is blocked, and only without live consent. Blocking
    `unknown` here too would be more conservative and would also make the
    product unusable -- nobody could label their own library -- so the caution
    for `unknown` is applied at the other gate, eligibility, where it costs an
    automated album rather than the whole feature.
    """
    if status is not MinorStatus.CONFIRMED_MINOR:
        return True
    if consent is None or as_of is None:
        return False
    return consent.is_live_for("minor_face_labeling", as_of=as_of)


def effective_minor_status(
    face_status: MinorStatus, person_status: MinorStatus
) -> MinorStatus:
    """The more cautious of the face's own status and its person's."""
    for status in _CAUTION_ORDER:
        if face_status is status or person_status is status:
            return status
    raise IdentityError(  # pragma: no cover - the enum is exhausted above
        f"unhandled minor status pair: {face_status}, {person_status}"
    )


# ---------------------------------------------------------------------------
# The consumer-facing set
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AutomatedFaceSet:
    """The only type that should appear in an album/film/reel signature.

    An album planner that takes `Sequence[PersonAssignment]` has to remember to
    filter. An album planner that takes an `AutomatedFaceSet` cannot forget: the
    filtering happened before the value existed, and the constructor raises on
    anything ineligible rather than dropping it quietly.

    Two entry points, and the difference between them is the point:

        AutomatedFaceSet(assignments)          -- asserts. Every one must be
                                                  eligible or it raises.
        AutomatedFaceSet.filtered(assignments) -- selects, and reports what it
                                                  excluded in `excluded`.
    """

    assignments: tuple[PersonAssignment, ...]
    excluded: tuple[PersonAssignment, ...] = ()

    def __init__(
        self,
        assignments: Iterable[PersonAssignment],
        excluded: Iterable[PersonAssignment] = (),
    ) -> None:
        ordered = tuple(sorted(assignments, key=lambda a: a.face_id))
        for assignment in ordered:
            if not assignment.eligible_for_automated_output:
                raise IneligibleFace(
                    f"{assignment.face_id} is {assignment.assignment.value} and is not "
                    "eligible for automated output. Use AutomatedFaceSet.filtered to "
                    "drop it deliberately, or answer its review item"
                )
        seen: set[str] = set()
        for assignment in ordered:
            if assignment.face_id in seen:
                raise IdentityError(f"duplicate face_id in set: {assignment.face_id}")
            seen.add(assignment.face_id)
        object.__setattr__(self, "assignments", ordered)
        object.__setattr__(
            self, "excluded", tuple(sorted(excluded, key=lambda a: a.face_id))
        )

    @classmethod
    def filtered(
        cls, assignments: Iterable[PersonAssignment]
    ) -> "AutomatedFaceSet":
        materialised = tuple(assignments)
        keep = [a for a in materialised if a.eligible_for_automated_output]
        drop = [a for a in materialised if not a.eligible_for_automated_output]
        return cls(keep, drop)

    @property
    def person_ids(self) -> frozenset[str]:
        return frozenset(
            a.person_id for a in self.assignments if a.person_id is not None
        )

    def faces_of(self, person_id: str) -> tuple[PersonAssignment, ...]:
        return tuple(a for a in self.assignments if a.person_id == person_id)

    def __len__(self) -> int:
        return len(self.assignments)

    def __iter__(self):
        return iter(self.assignments)


# ---------------------------------------------------------------------------
# Assignment
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FaceContext:
    """One face as assignment needs to see it."""

    face_id: str
    embedding: FaceEmbedding | None = None
    quality: float | None = None
    yaw_deg: float | None = None
    minor_status: MinorStatus = MinorStatus.UNKNOWN
    cluster_is_noise: bool = False
    inherited_from_track: bool = False

    def __post_init__(self) -> None:
        if not _BLAKE3_RE.match(self.face_id or ""):
            raise IdentityError(
                f"face_id must be a lowercase 64-hex BLAKE3 digest, got "
                f"{self.face_id!r}"
            )
        if self.embedding is not None and self.embedding.face_id != self.face_id:
            raise IdentityError(
                f"{self.face_id}: embedding belongs to {self.embedding.face_id}"
            )
        if not isinstance(self.minor_status, MinorStatus):
            raise IdentityError("minor_status must be a MinorStatus")


@dataclass(frozen=True)
class Decisions:
    """What humans have already said. Read-only input to assignment.

    `confirmed` outranks everything the model computes. `rejected` is a negative
    constraint: person p is removed from this face's candidate list entirely, so
    a rejection cannot be undone by the model getting more confident later.
    """

    confirmed: Mapping[str, str] = field(default_factory=dict)
    rejected: Mapping[str, frozenset[str]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for face_id, person_id in self.confirmed.items():
            if not _BLAKE3_RE.match(face_id):
                raise IdentityError(f"confirmed: {face_id!r} is not a face_id")
            if not _UUID_RE.match(person_id):
                raise IdentityError(f"confirmed[{face_id}]: {person_id!r} is not a UUID")
        for face_id, people in self.rejected.items():
            if not _BLAKE3_RE.match(face_id):
                raise IdentityError(f"rejected: {face_id!r} is not a face_id")
            for person_id in people:
                if not _UUID_RE.match(person_id):
                    raise IdentityError(
                        f"rejected[{face_id}]: {person_id!r} is not a UUID"
                    )
        contradictions = sorted(
            face_id
            for face_id, person_id in self.confirmed.items()
            if person_id in self.rejected.get(face_id, frozenset())
        )
        if contradictions:
            raise IdentityError(
                f"{contradictions[0]} is both confirmed as and rejected from the same "
                "person; resolve that where it was recorded"
            )


def assign_identities(
    faces: Iterable[FaceContext],
    gallery: PersonGallery,
    *,
    thresholds: Thresholds,
    calibrator: Calibrator,
    now: str,
    decisions: Decisions | None = None,
) -> tuple[PersonAssignment, ...]:
    """Turn faces plus a gallery into assignments. Deterministic, order-free.

    `now` is passed in rather than read from the clock so that a replay of the
    same inputs produces the same output, consent expiry included. A consent
    that expires between two runs must change the answer; a consent that expires
    between two lines of the same run must not.
    """
    if _parse_timestamp(now) is None:
        raise IdentityError(
            f"now must be an RFC 3339 instant with an offset, got {now!r}"
        )
    if not isinstance(gallery, PersonGallery):
        raise IdentityError("gallery must be a PersonGallery")
    if calibrator.calibrated and not math.isclose(
        calibrator.operating_confidence,
        thresholds.automated_output,
        rel_tol=0.0,
        abs_tol=1e-12,
    ):
        raise IdentityError(
            f"the calibrator was fitted for an operating confidence of "
            f"{calibrator.operating_confidence} but the thresholds ask for "
            f"{thresholds.automated_output}. The measured precision belongs to the "
            "operating point it was measured at, and moving the threshold away from "
            "it silently discards the measurement"
        )
    decisions = decisions or Decisions()
    people = gallery.by_id

    results: list[PersonAssignment] = []
    for face in sorted(faces, key=lambda f: f.face_id):
        results.append(
            _assign_one(
                face=face,
                gallery=gallery,
                people=people,
                thresholds=thresholds,
                calibrator=calibrator,
                now=now,
                decisions=decisions,
            )
        )
    return tuple(results)


def _assign_one(
    *,
    face: FaceContext,
    gallery: PersonGallery,
    people: Mapping[str, Person],
    thresholds: Thresholds,
    calibrator: Calibrator,
    now: str,
    decisions: Decisions,
) -> PersonAssignment:
    confirmed_person = decisions.confirmed.get(face.face_id)
    if confirmed_person is not None:
        person = people.get(confirmed_person)
        if person is None:
            # A confirmation naming somebody the gallery has never heard of means
            # the person record was lost or never written. Defaulting their minor
            # status to `unknown` here would be inventing the safest-sounding
            # answer to a question about a person we cannot look up.
            raise IdentityError(
                f"{face.face_id} is confirmed as {confirmed_person}, who is not in "
                "the gallery; a confirmed person must exist before their faces do"
            )
        status = effective_minor_status(face.minor_status, person.minor_status)
        consent = person.labeling_consent
        if not may_be_named(status, consent, as_of=now):
            # A human tapped a name on a child's face and there is no consent
            # ledger entry. The tap is not the consent; the schema requires the
            # consent object to exist before the label is written.
            return _unnamed(
                face,
                status=status,
                consent=consent,
                now=now,
                reason=ReviewReason.MINOR_CONSENT_REQUIRED,
                assignment=Assignment.UNASSIGNED,
            )
        return PersonAssignment(
            face_id=face.face_id,
            assignment=Assignment.USER_CONFIRMED,
            person_id=confirmed_person,
            confidence=USER_CONFIRMATION_CONFIDENCE,
            threshold_used=thresholds.automated_output,
            minor_status=status,
            labeling_consent=consent,
            consent_checked_at=now,
            has_own_embedding=face.embedding is not None,
            decided_by=DecidedBy.USER,
            decided_at=now,
        )

    if face.embedding is None:
        return _unnamed(
            face,
            status=face.minor_status,
            consent=None,
            now=now,
            reason=ReviewReason.NO_EMBEDDING,
            assignment=Assignment.UNASSIGNED,
        )

    rejected = decisions.rejected.get(face.face_id, frozenset())
    scored: list[tuple[float, str]] = []
    for person in gallery.people:
        if person.person_id in rejected:
            continue
        similarity = gallery.similarity(face.embedding, person)
        if similarity is None:
            continue
        scored.append((calibrator.confidence(similarity), person.person_id))
    # Highest confidence first; person_id breaks ties so two equally-matching
    # people do not swap places between runs.
    scored.sort(key=lambda pair: (-pair[0], pair[1]))

    if not scored:
        return _unnamed(
            face,
            status=face.minor_status,
            consent=None,
            now=now,
            reason=ReviewReason.NEW_CLUSTER,
            assignment=Assignment.UNASSIGNED,
        )

    candidates = tuple(
        Candidate(person_id=person_id, confidence=confidence)
        for confidence, person_id in scored[:8]
    )
    best_confidence, best_person = scored[0]
    person = people[best_person]
    status = effective_minor_status(face.minor_status, person.minor_status)
    consent = person.labeling_consent

    if best_confidence < thresholds.review_floor:
        return _unnamed(
            face,
            status=status,
            consent=consent,
            now=now,
            reason=ReviewReason.BELOW_THRESHOLD,
            assignment=Assignment.AUTO_BELOW_THRESHOLD,
            candidates=candidates,
            confidence=best_confidence,
            threshold_used=thresholds.automated_output,
        )

    if len(scored) > 1 and (best_confidence - scored[1][0]) < thresholds.ambiguity_margin:
        return _unnamed(
            face,
            status=status,
            consent=consent,
            now=now,
            reason=ReviewReason.MULTIPLE_CANDIDATES,
            assignment=Assignment.AMBIGUOUS_MULTIPLE_CANDIDATES,
            candidates=candidates,
            confidence=best_confidence,
            threshold_used=thresholds.automated_output,
        )

    threshold = _effective_threshold(face, thresholds)
    blocked = _automation_blocker(
        face=face,
        thresholds=thresholds,
        calibrator=calibrator,
        threshold=threshold,
        confidence=best_confidence,
    )
    if blocked is not None:
        return _unnamed(
            face,
            status=status,
            consent=consent,
            now=now,
            reason=blocked,
            assignment=Assignment.REVIEW_QUEUED,
            candidates=candidates,
            confidence=best_confidence,
            threshold_used=threshold,
        )

    if not may_be_named(status, consent, as_of=now):
        return _unnamed(
            face,
            status=status,
            consent=consent,
            now=now,
            reason=ReviewReason.MINOR_CONSENT_REQUIRED,
            assignment=Assignment.UNASSIGNED,
            candidates=candidates,
        )

    # The model is confident enough and allowed to name this face. Whether the
    # name may be USED unattended is a separate question that the eligibility
    # property answers -- and if the age question is still open, the answer is
    # no, with a review reason attached so the queue asks it.
    unresolved = status in (MinorStatus.UNKNOWN, MinorStatus.ESTIMATED_MINOR)
    return PersonAssignment(
        face_id=face.face_id,
        assignment=Assignment.AUTO_HIGH_CONFIDENCE,
        person_id=best_person,
        confidence=best_confidence,
        threshold_used=threshold,
        minor_status=status,
        labeling_consent=consent,
        consent_checked_at=now,
        has_own_embedding=True,
        candidates=candidates,
        review_reason=ReviewReason.MINOR_STATUS_UNRESOLVED if unresolved else None,
        decided_by=DecidedBy.MODEL,
        decided_at=now,
    )


def _effective_threshold(face: FaceContext, thresholds: Thresholds) -> float:
    """The bar this particular face has to clear.

    Tightened for extreme yaw, per the schema's own guidance. Capped at 1.0
    because a threshold above 1.0 is unreachable by a Confidence and would read
    in the record as "we required something impossible" rather than "we refused".
    """
    threshold = thresholds.automated_output
    yaw = face.yaw_deg
    if yaw is not None and math.isfinite(yaw) and abs(yaw) > thresholds.extreme_yaw_deg:
        threshold += thresholds.extreme_pose_penalty
    return min(1.0, threshold)


def _automation_blocker(
    *,
    face: FaceContext,
    thresholds: Thresholds,
    calibrator: Calibrator,
    threshold: float,
    confidence: float,
) -> ReviewReason | None:
    """Why this face may not take the automated path. None means it may.

    Order matters only for which reason is reported, and the order is chosen so
    the most actionable reason wins: an uncalibrated system is a repository-wide
    condition, so it is reported first and a user is not asked to fix a face.
    """
    if not calibrator.calibrated:
        return ReviewReason.UNCALIBRATED_THRESHOLD
    if face.cluster_is_noise:
        # "Never surfaces in automated output" -- ClusterMembership.is_noise.
        return ReviewReason.NEW_CLUSTER
    if face.quality is not None and face.quality < thresholds.quality_floor:
        return ReviewReason.LOW_FACE_QUALITY
    if not confidence >= threshold:
        # Written as `not (>=)` so a NaN confidence lands here rather than in the
        # branch that names somebody.
        yaw = face.yaw_deg
        if (
            yaw is not None
            and math.isfinite(yaw)
            and abs(yaw) > thresholds.extreme_yaw_deg
        ):
            return ReviewReason.EXTREME_POSE
        return ReviewReason.NEAR_BOUNDARY
    return None


def _unnamed(
    face: FaceContext,
    *,
    status: MinorStatus,
    consent: ConsentRef | None,
    now: str,
    reason: ReviewReason,
    assignment: Assignment,
    candidates: tuple[Candidate, ...] = (),
    confidence: float | None = None,
    threshold_used: float | None = None,
) -> PersonAssignment:
    return PersonAssignment(
        face_id=face.face_id,
        assignment=assignment,
        person_id=None,
        confidence=confidence,
        threshold_used=threshold_used,
        minor_status=status,
        labeling_consent=consent,
        consent_checked_at=now,
        has_own_embedding=face.embedding is not None,
        candidates=candidates,
        review_reason=reason,
        decided_by=DecidedBy.MODEL,
        decided_at=now,
    )
