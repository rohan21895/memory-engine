"""Candidates in, `ItemVerdict`s out -- and absence out, when there is no model.

THE ONE RULE

    Absence is `indeterminate`, and indeterminate blocks.

Every path in this module that cannot produce three calibrated probabilities
produces an `indeterminate` verdict with a reason from the contract's own enum.
There is no path that produces nothing, no path that produces a default, and no
path that produces a score it did not compute. `contracts/schemas/safety-
clearance.schema.json` says why at length; the short version is that a safety
check which silently no-ops when its model is missing is worse than no check,
because everything downstream reads the absence as a pass.

Today EVERY verdict this module can produce is `indeterminate`, because there is
no SigLIP 2 image embedder in the registry (#79) and therefore no head fitted
over it. That is not a degraded mode. It is the gate working: every print, every
share and every contact sheet is blocked, with a reason a human can act on.

WHY THRESHOLDS ARE PASSED IN AND ALSO RECORDED

`SafetyClearance.Thresholds` exists because "the config can change underneath a
stored verdict, and a verdict whose threshold you cannot reconstruct cannot be
re-audited". So the numbers that were applied travel with the verdicts rather
than being looked up later. 0.3 rather than 0.5 is a policy decision and is
asymmetric on purpose: a false positive omits one photograph and the user can
override it; a false negative puts sensitive content into a printed book, a
shared reel, or a contact sheet that has already left the device.

WHAT IS NOT IMPLEMENTED HERE, ON PURPOSE

`docs/safety-classifier-decision.md` §6.5 proposes that `medical_or_artistic`
stop being a blocking threshold and become a DISPOSITION -- when it is high and
a blocking class fired, route to the review queue with an explanation instead of
omitting the photograph silently. That is a good proposal and it is a POLICY
change: issue #21 fixed three classes and a 0.3 threshold and did not say what
the third class does. It needs sign-off, so it is not implemented. What is here
is `review_disposition`, which computes the signal and is used by nothing, so
that the day the proposal is signed off the UI has the number and the gate does
not have to change shape.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from memory_engine_safety.calibration import PlattScaling
from memory_engine_safety.classes import CLASS_ORDER, check_class_order, scores_to_mapping
from memory_engine_safety.embedding import (
    EMBEDDING_DIMENSIONS,
    EMBEDDING_SPACE,
    EmbedderUnavailable,
    ImageEmbedder,
)
from memory_engine_safety.head import LinearHead

__all__ = [
    "DEFAULT_THRESHOLD",
    "Candidate",
    "Classification",
    "SafetyClassifier",
    "Thresholds",
    "review_disposition",
]

#: The one number in the model config that is a policy decision rather than a
#: technical one. See the module docstring for why it is not 0.5.
DEFAULT_THRESHOLD = 0.3

_HEX64 = re.compile(r"^[0-9a-f]{64}$")


def _blake3_hex(value: object, *, where: str) -> str:
    if not isinstance(value, str) or not _HEX64.match(value):
        raise ValueError(
            f"{where}: {value!r} is not a 64-character lowercase BLAKE3 hex id"
        )
    return value


@dataclass(frozen=True)
class Thresholds:
    """The decision boundary per class, as applied and as recorded.

    A class fires at `score >= threshold`. `>=` and not `>` so that a threshold
    of 0.0 means "everything fires" rather than "almost everything fires", which
    is the reading a person setting it to zero intends.
    """

    explicit: float = DEFAULT_THRESHOLD
    suggestive: float = DEFAULT_THRESHOLD
    medical_or_artistic: float = DEFAULT_THRESHOLD

    def __post_init__(self) -> None:
        for name in CLASS_ORDER:
            value = getattr(self, name)
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise ValueError(f"threshold {name!r} is not a number")
            if value != value or not (0.0 <= float(value) <= 1.0):
                raise ValueError(
                    f"threshold {name!r} is {value!r}; `Unit` is [0, 1] and a "
                    "threshold outside it either never fires or always does"
                )

    def as_mapping(self) -> dict[str, float]:
        return {name: float(getattr(self, name)) for name in CLASS_ORDER}

    def fired(self, scores: Mapping[str, float]) -> tuple[str, ...]:
        """Which classes are at or above their threshold, in class order."""
        return tuple(
            name for name in CLASS_ORDER if scores[name] >= float(getattr(self, name))
        )


@dataclass(frozen=True)
class Candidate:
    """One item about to be published, and the proxy that will be classified.

    Both ids, not one. `media_id` is what the publication names; `evidence_id`
    is the proxy the classifier actually sees, and a proxy can be regenerated by
    a better decoder or a corrected orientation. A verdict about the old proxy
    is not evidence about the new one, so staleness has to be a lookup miss
    rather than a judgement call.
    """

    media_id: str
    evidence_id: str

    def __post_init__(self) -> None:
        _blake3_hex(self.media_id, where="Candidate.media_id")
        _blake3_hex(self.evidence_id, where="Candidate.evidence_id")


def review_disposition(scores: Mapping[str, float], thresholds: Thresholds) -> bool:
    """True when a block looks like it may be a medical or artistic photograph.

    NOT WIRED TO ANYTHING. Decision doc §6.5's proposal, computed and left for
    the review UI, because the parent whose breastfeeding photograph was dropped
    needs to be told rather than handed an override button they will never find.
    Making it change the verdict is a policy change that needs sign-off.
    """
    fired = thresholds.fired(scores)
    blocking = tuple(name for name in fired if name != "medical_or_artistic")
    return bool(blocking) and "medical_or_artistic" in fired


@dataclass(frozen=True)
class Classification:
    """Verdict documents, plus the one sentence that explains a wholesale block.

    The sentence is separate from the verdicts because `ItemVerdict` has
    `additionalProperties: false` and no free-text field -- and because when a
    model is missing the explanation is the same for all 512 items, so it
    belongs on the manifest's `denied_reason` once rather than per item.
    """

    verdicts: tuple[dict[str, Any], ...]
    detail: str | None = None


@dataclass(frozen=True)
class SafetyClassifier:
    """The head, its calibration and an embedder -- any of which may be absent.

    All three are optional and all three default to `None`, which is the state
    of the product today. That is deliberate: a required constructor argument
    would have forced every caller to supply something, and the something they
    would have supplied is a stand-in.
    """

    thresholds: Thresholds = Thresholds()
    head: LinearHead | None = None
    calibration: PlattScaling | None = None
    embedder: ImageEmbedder | None = None
    #: Set when the classifier is unavailable for a reason already known before
    #: any item is looked at -- most importantly a load-gate refusal. Carried as
    #: (contract reason, human detail).
    unavailable: tuple[str, str] | None = None

    def __post_init__(self) -> None:
        if self.head is not None:
            check_class_order(self.head.class_order, where="classifier head")
        if self.calibration is not None:
            check_class_order(self.calibration.class_order, where="classifier calibration")
        if self.embedder is not None:
            space = getattr(self.embedder, "space", None)
            if space != EMBEDDING_SPACE:
                raise ValueError(
                    f"the embedder serves {space!r}, not {EMBEDDING_SPACE!r}; a "
                    "vector from another space has the same width and no shared "
                    "meaning"
                )
            if getattr(self.embedder, "dimensions", None) != EMBEDDING_DIMENSIONS:
                raise ValueError("the embedder's width does not match the head's")

    def _blocked_before_looking(self) -> tuple[str, str] | None:
        """Why no item can get a verdict, or None if the model is complete."""
        if self.unavailable is not None:
            return self.unavailable
        if self.head is None:
            return (
                "model_unavailable",
                "no sensitive-content head is loaded. The head is a matrix over the "
                "SigLIP 2 so400m-384 embedding and there is no ONNX export of that "
                "tower in this registry yet (issue #79), so nothing has been fitted, "
                "hashed or measured.",
            )
        if self.calibration is None:
            return (
                "model_unloadable",
                "the head has no Platt calibration attached. Raw zero-shot logits are "
                "not probabilities, so comparing them to the 0.3 threshold would be a "
                "threshold with no meaning producing verdicts that look calibrated.",
            )
        if self.embedder is None:
            return (
                "model_unavailable",
                "no image embedder is available to compute the vectors the head runs "
                "on (issue #79).",
            )
        return None

    def classify(self, candidates: Sequence[Candidate]) -> Classification:
        """One `ItemVerdict` document per candidate, in the order given.

        Order is preserved because `SafetyClearance.items` is "in PUBLICATION
        ORDER" and "a manifest whose items match by set but not by order
        describes a different publication".
        """
        if not candidates:
            raise ValueError(
                "a clearance covers at least one item; an empty publication is not "
                "a thing to clear, and a manifest over no items would be vacuously "
                "cleared"
            )
        seen: set[str] = set()
        for candidate in candidates:
            if candidate.media_id in seen:
                raise ValueError(
                    f"{candidate.media_id} appears twice. A duplicate makes "
                    "item_count disagree with the publication and gives one "
                    "photograph two verdicts, which a verifier would have to pick "
                    "between."
                )
            seen.add(candidate.media_id)

        blocked = self._blocked_before_looking()
        if blocked is not None:
            reason, detail = blocked
            return Classification(
                verdicts=tuple(self._indeterminate(c, reason) for c in candidates),
                detail=detail,
            )

        head = self.head
        calibration = self.calibration
        embedder = self.embedder
        if head is None or calibration is None or embedder is None:  # pragma: no cover
            # Unreachable: _blocked_before_looking returns non-None for each of
            # these. Written as a branch rather than an assert because `python
            # -O` strips asserts, and a guard that disappears under a flag is
            # worse than no guard.
            return Classification(
                verdicts=tuple(
                    self._indeterminate(c, "verifier_exception") for c in candidates
                ),
                detail="the classifier is incomplete in a way its own availability "
                "check did not describe",
            )

        try:
            vectors = embedder.embed([c.evidence_id for c in candidates])
        except EmbedderUnavailable as absent:
            return Classification(
                verdicts=tuple(
                    self._indeterminate(c, absent.reason) for c in candidates
                ),
                detail=absent.detail,
            )
        except Exception as failure:  # noqa: BLE001 - any fault is indeterminate
            # Deliberately broad. An embedder is somebody else's process across
            # a gRPC boundary; the ways it can fail are not enumerable here, and
            # every one of them means nobody checked.
            return Classification(
                verdicts=tuple(
                    self._indeterminate(c, "inference_error") for c in candidates
                ),
                detail=f"the embedder raised {type(failure).__name__}: {failure}",
            )

        verdicts: list[dict[str, Any]] = []
        faults: list[str] = []
        for candidate in candidates:
            vector = vectors.get(candidate.evidence_id)
            if vector is None:
                verdicts.append(self._indeterminate(candidate, "no_result"))
                faults.append(
                    f"no vector for proxy {candidate.evidence_id[:12]}... (the proxy "
                    "may have been regenerated or deleted since it was indexed)"
                )
                continue
            try:
                logits = head.logits(vector)
                probabilities = calibration.probabilities(logits)
                scores = scores_to_mapping(probabilities, where="classifier output")
                for name, value in scores.items():
                    if value != value or not (0.0 <= value <= 1.0):
                        raise ValueError(
                            f"calibrated {name} is {value!r}, outside Unit; a score "
                            "outside [0, 1] cannot be compared to a threshold and "
                            "must not be recorded as one that was"
                        )
            except Exception as failure:  # noqa: BLE001 - see above
                verdicts.append(self._indeterminate(candidate, "inference_error"))
                faults.append(
                    f"{candidate.media_id[:12]}...: {type(failure).__name__}: {failure}"
                )
                continue

            fired = self.thresholds.fired(scores)
            verdicts.append(
                {
                    "media_id": candidate.media_id,
                    "evidence_id": candidate.evidence_id,
                    "verdict": "blocked" if fired else "cleared",
                    "scores": scores,
                    "indeterminate_reason": None,
                    "override": None,
                }
            )
        return Classification(
            verdicts=tuple(verdicts),
            detail="; ".join(faults) if faults else None,
        )

    @staticmethod
    def _indeterminate(candidate: Candidate, reason: str) -> dict[str, Any]:
        return {
            "media_id": candidate.media_id,
            "evidence_id": candidate.evidence_id,
            "verdict": "indeterminate",
            "scores": None,
            "indeterminate_reason": reason,
            "override": None,
        }
