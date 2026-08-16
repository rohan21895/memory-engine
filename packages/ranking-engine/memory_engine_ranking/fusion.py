"""Quality score fusion v1 — hand-weighted linear, per build plan §4.3.

A transparent, tunable model, deliberately chosen over a learned one: there are
no PrefEvents yet to train on, and a linear fusion can be explained to a user
("this was picked because it was the sharpest frame with everyone's eyes open")
and corrected by hand when it is wrong. The plan graduates this to a learned
head once events accumulate; the interface here is the one that has to survive
that swap, which is why weights are data rather than constants in code.

FOUR DECISIONS, AND WHAT THE ALTERNATIVES LOOK LIKE

1. ELIMINATION IS NOT SCORING.
   A black frame, a lens-capped frame, a pocket shot: these are rejected, not
   scored low. The distinction is load-bearing. A low score still competes, and
   still wins whenever the pool is bad enough -- which is exactly the situation
   at the tail of a GoPro card, where the alternative to a pocket shot is
   another pocket shot. Build plan §4.3 puts elimination first for cost reasons
   (90-95% of an action-camera library discards before any expensive analysis);
   it belongs first for correctness reasons too.

2. MISSING SIGNALS RENORMALISE. THEY DO NOT DEFAULT.
   Most of QualityScores is optional, and mid-scan almost everything is missing.
   Three tempting ways to handle that, all wrong:

     * Treat missing as 0 -- punishes every photo the expensive model has not
       reached yet, so ranking during a scan is inverted with respect to
       ranking after it.
     * Treat missing as 0.5 -- fabricates a measurement. The number is then
       indistinguishable from a real mediocre score, and no later audit can
       separate them.
     * Sum present signals and ignore the rest -- a photo scores lower purely
       for having been measured less.

   So the weights of present signals are renormalised to sum to 1, and the
   fraction of total weight actually present is reported as `coverage`.

3. COVERAGE IS REPORTED, NOT FOLDED IN.
   0.82 from two signals and 0.82 from seven are not the same claim, but
   discounting the first would make the value lie in a different direction --
   an under-measured photo would look worse than a measured bad one. So the
   value stays honest about what was measured, `coverage` says how much that
   was, and the caller decides. `comparable` exists because comparing across a
   large coverage gap is the mistake this design makes possible.

4. NOT APPLICABLE IS NOT THE SAME AS NOT MEASURED.
   `face_quality` is null both for a landscape with no faces and for a portrait
   the face model has not reached. The first must not count against coverage --
   a mountain is not an under-measured photo -- and the second must. The caller
   distinguishes them by passing `has_faces`, because only the caller knows.

DETERMINISM
Same signals and same weights produce the same value, bit for bit: contributions
are summed in sorted-name order rather than dict order, and the result is
quantised to six decimals. Float addition is not associative, and a fusion that
returned 0.8199999 on one machine and 0.8200001 on another would reorder ties
in `select_primary` between runs.

WEIGHTS ARE IDENTIFIED
`FusedScore.weights_id` records which profile produced the number, for the same
reason ModelPin carries a config digest: a score you cannot attribute to a
specific set of weights is not reproducible, and per-user reweighting is the
entire point of the PrefEvent flywheel. Two users will legitimately disagree
about the same photo, and "why is this 0.82" has to be answerable for each.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Mapping

# Bump when a signal is added, removed or redefined. Matches
# PrefEvent.FeatureContext.feature_set_id: a stored feature map is meaningless
# without knowing which feature list it was written against.
FEATURE_SET_ID = "photo-quality-v1"

# Below this, a frame is junk rather than a weak candidate. Deliberately low:
# this is an elimination floor, not a quality bar, and a genuine low-light
# handheld shot of a first birthday is worth keeping at 0.15 sharpness when the
# alternative is nothing.
DEFAULT_SHARPNESS_FLOOR = 0.08

# Coverage below which a score is provisional. Two of seven signals is enough to
# order a scan-in-progress preview and not enough to decide what goes in a
# printed book.
DEFAULT_MIN_COVERAGE = 0.5


@dataclass(frozen=True)
class Signals:
    """One photo's measurements, exactly as MediaRecord.quality carries them.

    Every optional field is `None` when not measured. `has_faces` is separate
    from `face_quality` because null means two different things and only the
    caller can tell them apart.
    """

    sharpness: float
    exposure: float
    noise: float | None = None
    contrast: float | None = None
    technical_iqa: float | None = None
    aesthetic: float | None = None
    composition: float | None = None
    face_quality: float | None = None

    has_faces: bool = False
    is_black_frame: bool = False
    is_lens_obstructed: bool = False


@dataclass(frozen=True)
class Weights:
    """A weighting profile. Data, not constants, so a per-user profile is a
    stored row rather than a code change.

    The v1 numbers are a starting point chosen to be defensible rather than
    optimal, and the eval harness is what will move them:

      * Sharpness and exposure carry the most weight because they are the only
        signals guaranteed present, and because blur is the single most common
        reason a person rejects a photo.
      * `aesthetic` is deliberately light. Build plan §4.2 calls it a PRIOR, and
        an aesthetic head trained on stock photography has opinions about family
        snapshots that no family shares.
      * `face_quality` is heavy when faces are present. In a family library the
        photo where everyone's eyes are open beats the sharper one where they
        are not, and that is not a close call.
    """

    weights_id: str = "default-v1"

    sharpness: float = 0.22
    exposure: float = 0.18
    noise: float = 0.06
    contrast: float = 0.06
    technical_iqa: float = 0.14
    aesthetic: float = 0.08
    composition: float = 0.08
    face_quality: float = 0.18

    def as_map(self) -> dict[str, float]:
        return {
            "sharpness": self.sharpness,
            "exposure": self.exposure,
            "noise": self.noise,
            "contrast": self.contrast,
            "technical_iqa": self.technical_iqa,
            "aesthetic": self.aesthetic,
            "composition": self.composition,
            "face_quality": self.face_quality,
        }

    def digest(self) -> str:
        """Stable digest of the weight values, canonicalised the same way model
        configs are. Two profiles that differ only by name are the same fusion;
        two that share a name and differ by a value are not, and a stored score
        must be able to tell.
        """
        payload = json.dumps(
            self.as_map(), sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
        return hashlib.blake2b(payload, digest_size=16).hexdigest()


@dataclass(frozen=True)
class FusedScore:
    """The fused number, and everything needed to defend it.

    `contributions` exists because the contract promises "why is this photo
    ranked 0.82" stays answerable. A bare float is not an answer.
    """

    value: float
    coverage: float
    rejected: bool
    rejection_reason: str | None
    weights_id: str
    weights_digest: str
    feature_set_id: str
    contributions: tuple[tuple[str, float], ...] = field(default=())

    @property
    def comparable(self) -> bool:
        """Whether this score may be ranked against a fully-measured one."""
        return not self.rejected and self.coverage >= DEFAULT_MIN_COVERAGE

    def as_feature_map(self) -> dict[str, float]:
        """The signal values that fed this score, shaped for
        PrefEvent.FeatureContext.named.

        Only the signals that were actually measured. A feature map that
        back-filled the missing ones would train the preference model on
        fabricated observations, which is a worse outcome than training it on
        fewer real ones.
        """
        return {name: value for name, value, _ in self.contributions}


# Elimination reasons, in the order they are checked. Order is fixed so two
# hosts report the same reason for a frame that fails several at once.
REJECT_BLACK_FRAME = "black_frame"
REJECT_LENS_OBSTRUCTED = "lens_obstructed"
REJECT_BELOW_SHARPNESS_FLOOR = "below_sharpness_floor"


def eliminate(
    signals: Signals, *, sharpness_floor: float = DEFAULT_SHARPNESS_FLOOR
) -> str | None:
    """The reason this frame is junk, or None if it is a candidate.

    Runs before any weighting, because the answer is not "a low number" -- it is
    "this frame does not enter the pool at all". Build plan §4.3: elimination
    first, always.
    """
    if signals.is_black_frame:
        return REJECT_BLACK_FRAME
    if signals.is_lens_obstructed:
        return REJECT_LENS_OBSTRUCTED
    if signals.sharpness < sharpness_floor:
        return REJECT_BELOW_SHARPNESS_FLOOR
    return None


def _present(signals: Signals) -> dict[str, float]:
    """Measured signals only, keyed by name."""
    values = {
        "sharpness": signals.sharpness,
        "exposure": signals.exposure,
        "noise": signals.noise,
        "contrast": signals.contrast,
        "technical_iqa": signals.technical_iqa,
        "aesthetic": signals.aesthetic,
        "composition": signals.composition,
        "face_quality": signals.face_quality,
    }
    return {name: value for name, value in values.items() if value is not None}


def _applicable_weight(signals: Signals, weights: Weights) -> float:
    """Total weight of signals that *could* have been measured for this photo.

    Excludes face quality on a photo with no faces: a landscape is not an
    under-measured portrait, and counting a face weight it can never earn would
    cap every landscape's coverage below the comparability threshold.
    """
    total = sum(weights.as_map().values())
    if not signals.has_faces:
        total -= weights.face_quality
    return total


def fuse(
    signals: Signals,
    weights: Weights | None = None,
    *,
    sharpness_floor: float = DEFAULT_SHARPNESS_FLOOR,
) -> FusedScore:
    """Fuse one photo's signals into a single comparable quality score.

    Returns a rejected score with value 0.0 when the frame is eliminated. The
    value is 0.0 rather than None so a caller that ignores `rejected` sorts it
    last rather than raising -- but `rejected` is what callers must branch on,
    because 0.0 is also a legitimate score for a frame that is merely terrible.
    """
    weights = weights or Weights()

    reason = eliminate(signals, sharpness_floor=sharpness_floor)
    if reason is not None:
        return FusedScore(
            value=0.0,
            coverage=0.0,
            rejected=True,
            rejection_reason=reason,
            weights_id=weights.weights_id,
            weights_digest=weights.digest(),
            feature_set_id=FEATURE_SET_ID,
            contributions=(),
        )

    weight_map = weights.as_map()
    present = _present(signals)
    if not signals.has_faces:
        present.pop("face_quality", None)

    live = {
        name: weight_map[name]
        for name in present
        if weight_map.get(name, 0.0) > 0.0
    }
    live_total = sum(live.values())

    if live_total <= 0.0:
        # Every weighted signal is missing or zero-weighted. Honest answer:
        # nothing measured, nothing claimed. Not a rejection -- the photo may be
        # fine, we simply have not looked at it yet.
        return FusedScore(
            value=0.0,
            coverage=0.0,
            rejected=False,
            rejection_reason=None,
            weights_id=weights.weights_id,
            weights_digest=weights.digest(),
            feature_set_id=FEATURE_SET_ID,
            contributions=(),
        )

    # Sorted, so summation order is fixed across runs and machines.
    contributions: list[tuple[str, float, float]] = []
    total = 0.0
    for name in sorted(live):
        share = live[name] / live_total
        total += present[name] * share
        contributions.append((name, present[name], share))

    applicable = _applicable_weight(signals, weights)
    coverage = live_total / applicable if applicable > 0.0 else 0.0

    return FusedScore(
        value=_quantise(min(max(total, 0.0), 1.0)),
        coverage=_quantise(min(coverage, 1.0)),
        rejected=False,
        rejection_reason=None,
        weights_id=weights.weights_id,
        weights_digest=weights.digest(),
        feature_set_id=FEATURE_SET_ID,
        contributions=tuple(
            (name, _quantise(value), _quantise(share)) for name, value, share in contributions
        ),
    )


def _quantise(value: float) -> float:
    """Six decimals, matching the precision Score stores.

    Float addition is not associative, so an unquantised fusion can differ in
    the last bit between machines. That difference is invisible until it
    reorders a tie in `select_primary` and a photo silently swaps out of an
    album between two runs of the same pipeline.
    """
    return round(value + 0.0, 6)


def explain(score: FusedScore, *, limit: int = 3) -> str:
    """One human-readable sentence for the UI and for debugging.

    The contract's promise that "why is this photo ranked 0.82" stays answerable
    is only kept if something actually renders the answer.
    """
    if score.rejected:
        return f"rejected: {score.rejection_reason}"
    if not score.contributions:
        return "not yet measured"
    ranked = sorted(
        score.contributions, key=lambda row: (-(row[1] * row[2]), row[0])
    )[:limit]
    parts = ", ".join(f"{name} {value:.2f}" for name, value, _ in ranked)
    suffix = "" if score.comparable else f", provisional at {score.coverage:.0%} coverage"
    return f"{score.value:.2f} from {parts}{suffix}"


def rank(
    scored: Mapping[str, FusedScore], *, require_comparable: bool = False
) -> list[str]:
    """Media ids best-first.

    Ties break on media id, matching `select_primary` -- every ordering in this
    package has to be stable for the same reason, so they all break the same way.
    Rejected items sort last rather than being dropped, because the caller
    deciding what to do with a pool of only-bad frames is not this function's
    call to make.
    """
    items = scored.items()
    if require_comparable:
        items = [(media_id, score) for media_id, score in items if score.comparable]
    return [
        media_id
        for media_id, _ in sorted(
            items, key=lambda row: (row[1].rejected, -row[1].value, row[0])
        )
    ]
