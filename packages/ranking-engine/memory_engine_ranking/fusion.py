"""Quality score fusion v1 — hand-weighted linear, per build plan §4.3.

A transparent, tunable model, deliberately chosen over a learned one: there are
no PrefEvents yet to train on, and a linear fusion can be explained to a user
("this was picked because it was the sharpest frame with the clearest face")
and corrected by hand when it is wrong. The plan graduates this to a learned
head once events accumulate; the interface here is the one that has to survive
that swap, which is why weights are data rather than constants in code.

FOUR DECISIONS, AND WHAT THE ALTERNATIVES LOOK LIKE

1. ELIMINATION IS NOT SCORING, AND ONLY A DETERMINATION MAY ELIMINATE.
   A black frame, a lens-capped frame, a pocket shot: these are rejected, not
   scored low. The distinction is load-bearing. A low score still competes, and
   still wins whenever the pool is bad enough -- which is exactly the situation
   at the tail of a GoPro card, where the alternative to a pocket shot is
   another pocket shot. Build plan §4.3 puts elimination first for cost reasons
   (90-95% of an action-camera library discards before any expensive analysis);
   it belongs first for correctness reasons too.

   ISSUE #22 ADDED THE SECOND HALF OF THAT SENTENCE. Black frame and lens
   obstruction are booleans: something upstream determined a fact. Sharpness is
   a measurement on a scale that this repository has never defined -- ingest
   writes `quality = None`, and no Laplacian-to-Unit normalisation exists -- and
   it was nonetheless wired to a hard gate at 0.08. See `SharpnessFloor` for
   what 0.08 actually eliminates (measured: between 0% and 77.5% of the same
   images, depending only on an unwritten divisor). An uncalibrated measurement
   may rank. It may not eliminate.

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
import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Mapping, Sequence

# Bump when a signal is added, removed or redefined. Matches
# PrefEvent.FeatureContext.feature_set_id: a stored feature map is meaningless
# without knowing which feature list it was written against.
FEATURE_SET_ID = "photo-quality-v1"

# Every weighted signal, in one place. Used for validation, for the present-map
# and for the weight map, so the three cannot drift out of step.
_SIGNAL_NAMES = (
    "sharpness", "exposure", "noise", "contrast",
    "technical_iqa", "aesthetic", "composition", "face_quality",
)


class SignalError(ValueError):
    """An input that would corrupt the score rather than merely lower it."""


class WeightError(ValueError):
    """A weighting profile that cannot produce an honest coverage figure."""


class IncomparableScores(ValueError):
    """Scores that were not measured the same way cannot be ordered."""


class UncalibratedFloor(ValueError):
    """A threshold that would delete photographs on the strength of a guess."""


@dataclass(frozen=True)
class SharpnessFloor:
    """A sharpness value that is allowed to ELIMINATE, and the measurement that
    earned it that right.

    ISSUE #22. This used to be `DEFAULT_SHARPNESS_FLOOR = 0.08`, a bare float,
    and `eliminate()` discarded any frame below it. Three facts made that
    indefensible:

      * `workers/ingest` writes `quality = None` for every record, so nothing
        in the repository has ever produced a sharpness value.
      * No Laplacian-variance-to-unit normalisation exists anywhere. The
        contract declares `sharpness` as a Unit; nothing maps a measurement
        onto that Unit.
      * Therefore 0.08 was a number against a scale that does not exist. Not
        conservative, not aggressive -- undefined.

    MEASURED, NOT ARGUED. Laplacian variance over the 200 stills of
    `scripts/demo/make_library.py` spans 11.6 to 99.2. Feed those through
    three equally plausible normalisations and ask what 0.08 eliminates:

        min(1, lv/100)      0.0%
        min(1, lv/500)      0.5%
        min(1, lv/1000)    77.5%

    The same constant, the same library, the same photographs: between nothing
    and three quarters of them, decided entirely by a divisor nobody has
    chosen. That is the whole argument. A gate whose behaviour is set by an
    unwritten constant is not a gate.

    WHY THIS IS A TYPE AND NOT A NUMBER

    The alternative fix was to calibrate 0.08 against the synthetic library and
    move on. It was rejected, and the reason is worth keeping:

      * The synthetic library has no blurred variants. Every still gets one
        uniform `GaussianBlur(radius=0.4)` anti-aliasing pass; there is no junk
        tail and no bimodality. Measured: 8.6x total spread, no gap.
      * Synthetic blur is not camera blur. Gaussian defocus of flat vector
        shapes has none of what makes a real frame unrecoverable -- directional
        motion smear, sensor noise raising Laplacian variance while destroying
        detail, JPEG ringing, rolling-shutter skew.
      * The HARD NEGATIVES cannot be synthesised at all. A dim handheld shot of
        a first birthday, a deliberate long exposure, a shallow-depth-of-field
        portrait: these are technically poor and are exactly the photographs a
        family would be most upset to lose. Nothing procedurally drawn stands
        in for them.

    A floor calibrated on synthetic blur would LOOK calibrated -- it would have
    a number, a fixture and a green test -- and would still delete real
    photographs. Every defect this codebase has caught looked exactly like
    that.

    So the design changed instead: an uncalibrated threshold may rank, and may
    not eliminate. Low sharpness still costs a photo heavily (it carries the
    largest weight in the default profile, 0.22), so a blurry frame ranks last
    and only wins when the alternative is nothing -- which is the correct
    behaviour at the tail of a card, and the behaviour a hard gate destroys.

    WHAT THE FIELDS ARE FOR

    They are the evidence, and they are validated rather than decorative,
    because a provenance string nobody checks becomes "TODO" within a month.
    `hard_negatives_retained` must equal `hard_negatives_total`: a floor that
    eliminates even one photograph a family would want is refused outright, no
    matter how much junk it catches. That asymmetry is the entire point --
    keeping a bad photo costs a slot in an album, losing a good one is
    unrecoverable.
    """

    value: float
    benchmark_id: str
    measured_at: str
    junk_total: int
    junk_eliminated: int
    hard_negatives_total: int
    hard_negatives_retained: int
    normalisation_id: str
    note: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.value, (int, float)) or isinstance(self.value, bool):
            raise UncalibratedFloor(f"value={self.value!r} is not a number")
        if not math.isfinite(self.value) or not 0.0 <= self.value <= 1.0:
            raise UncalibratedFloor(
                f"value={self.value!r} is not a finite Unit; the contract declares "
                "sharpness on [0,1] and a floor outside it eliminates everything "
                "or nothing"
            )
        if not self.benchmark_id.strip():
            raise UncalibratedFloor(
                "benchmark_id is empty: a floor with no named benchmark cannot be "
                "re-measured, so nobody can ever tell whether it is still right"
            )
        if not self.normalisation_id.strip():
            raise UncalibratedFloor(
                "normalisation_id is empty. THE FLOOR MEANS NOTHING WITHOUT IT: "
                "0.08 eliminated 0% or 77.5% of the same 200 images depending on "
                "which unit mapping was used. A floor is a pair (threshold, "
                "normalisation), never a threshold alone"
            )
        if not self.measured_at.strip():
            raise UncalibratedFloor("measured_at is empty")
        if self.junk_total <= 0 or self.hard_negatives_total <= 0:
            raise UncalibratedFloor(
                "a calibration set needs both junk AND hard negatives. Junk alone "
                "proves the floor catches something; it cannot prove the floor "
                "keeps what matters, which is the failure that costs a memory"
            )
        if not 0 <= self.junk_eliminated <= self.junk_total:
            raise UncalibratedFloor("junk_eliminated is outside junk_total")
        if not 0 <= self.hard_negatives_retained <= self.hard_negatives_total:
            raise UncalibratedFloor(
                "hard_negatives_retained is outside hard_negatives_total"
            )
        if self.hard_negatives_retained != self.hard_negatives_total:
            lost = self.hard_negatives_total - self.hard_negatives_retained
            raise UncalibratedFloor(
                f"this floor eliminates {lost} of {self.hard_negatives_total} hard "
                "negatives. A hard negative is a technically poor photograph a "
                "family would keep -- a dim shot of a first birthday, a long "
                "exposure, a shallow-depth-of-field portrait. Losing one is not a "
                "tolerable error rate, it is the failure the floor exists to avoid; "
                "lower the threshold or improve the signal"
            )
        if self.junk_eliminated == 0:
            raise UncalibratedFloor(
                "this floor eliminates no junk, so it is pure risk: it can only "
                "ever discard something, never save any work"
            )


# THE DEFAULT IS "DO NOT ELIMINATE ON SHARPNESS", AND THAT IS THE FIX.
#
# It stays None until someone runs the calibration described on SharpnessFloor
# against real photographs -- which needs ingest to write `quality.sharpness`
# first (issue #22 item 1, Codex's side) because the normalisation is upstream
# and determines what any threshold means.
#
# The name is unchanged so that a caller passing `sharpness_floor=` still type-
# checks; the TYPE changed, so a caller passing a bare float now fails loudly
# instead of silently reinstating a guess.
DEFAULT_SHARPNESS_FLOOR: SharpnessFloor | None = None

# Coverage below which a score is provisional. Two of seven signals is enough to
# order a scan-in-progress preview and not enough to decide what goes in a
# printed book.
DEFAULT_MIN_COVERAGE = 0.5


class FaceState(str, Enum):
    """Why `face_quality` is absent, which the value alone cannot say.

    `has_faces: bool` conflated two different absences, as Codex pointed out:
    a landscape with no faces, and a portrait whose face detection has not run
    yet. Defaulting to "no faces" made an UNKNOWN record look fully measured --
    it removed the face weight from the denominator, so a photo awaiting face
    detection reported 100% coverage and was ranked as comparable against
    photos that really were complete.
    """

    NOT_RUN = "not_run"      # detection has not happened; coverage is incomplete
    NO_FACES = "no_faces"    # detection ran and found none; face weight N/A
    HAS_FACES = "has_faces"  # detection ran and found faces


@dataclass(frozen=True)
class Signals:
    """One photo's measurements, as plain floats.

    NOT the contract shape. `MediaRecord.quality` carries `Score` objects
    (`{"value": 0.87, "run_id": ...}`), and passing one of those straight in
    raised a TypeError deep inside the comparison -- Codex found that the whole
    package did not actually connect to the contract it claims to consume. Use
    `signals_from_media_record` rather than constructing this by hand from a
    record; this stays float-shaped because the arithmetic wants floats and
    because a preference model will eventually feed it values that never were
    Scores.

    Every optional field is `None` when not measured.
    """

    sharpness: float
    exposure: float
    noise: float | None = None
    contrast: float | None = None
    technical_iqa: float | None = None
    aesthetic: float | None = None
    composition: float | None = None
    face_quality: float | None = None

    face_state: FaceState = FaceState.NOT_RUN
    is_black_frame: bool = False
    is_lens_obstructed: bool = False

    @property
    def has_faces(self) -> bool:
        return self.face_state is FaceState.HAS_FACES

    def validate(self) -> None:
        """Reject values that would corrupt the score rather than fail.

        NaN is the reason this exists. `NaN < floor` is False, so a NaN
        sharpness sailed through the elimination gate untouched and produced
        `value=nan`, which then made `rank()` order by a value for which `<` is
        meaningless -- a silently unstable album ordering. Infinities behave
        the same way. Codex found it; a comparison-based gate cannot defend
        itself against a value that compares False to everything.
        """
        for name in _SIGNAL_NAMES:
            value = getattr(self, name)
            if value is None:
                continue
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise SignalError(
                    f"{name}={value!r} is not a number. MediaRecord.quality holds "
                    "Score objects; use signals_from_media_record()."
                )
            if not math.isfinite(value):
                raise SignalError(
                    f"{name}={value!r} is not finite. NaN compares False to every "
                    "threshold, so it would pass the elimination gate untouched and "
                    "produce an unorderable score."
                )
            if not 0.0 <= value <= 1.0:
                raise SignalError(
                    f"{name}={value} is outside [0,1]. The contract declares every "
                    "quality signal as a Unit; a value outside it means an upstream "
                    "normalisation is missing, and clamping here would hide that."
                )


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
      * `face_quality` is heavy when faces are present, because in a family
        library a clear face beats a marginally sharper frame without one.

        WHAT IT MEASURES, SINCE default-v2. The group-photo blind spot recorded
        here in v1 -- face_quality was a MAX over significant faces, so one
        excellent face masked four blinking ones while collecting the full
        0.18 -- is now closed. The faces stage computes the summary with
        `aggregate_face_quality` (this module), a min-anchored blend over ALL
        significant faces (weighted min + p10 + mean + eyes-open ratio), so
        "everyone is visible, in focus, and awake" is once again the claim the
        weight is justified by. The weight values themselves are unchanged from
        v1; the id bumped because the SEMANTICS of the signal it weighs changed,
        and a score attributed to "default-v2" must not be compared against one
        whose face_quality still means best-in-frame.
    """

    weights_id: str = "default-v2"

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

    def validate(self) -> None:
        """Refuse a profile that cannot produce an honest coverage figure.

        A NEGATIVE weight was silently excluded from scoring -- the live-weight
        filter takes `> 0` -- while still counting toward the applicable-weight
        DENOMINATOR. Codex's example: 0.22, 0.18, -0.39 gives an applicable
        total of 0.01, so coverage computed as 0.40/0.01 = 40, clamped to 1.0.
        The score then claimed to be fully measured while ignoring one of its
        three signals. Coverage was not approximate there, it was false.

        Zero is allowed and means "this profile does not use that signal".
        """
        weights = self.as_map()
        negative = sorted(n for n, w in weights.items() if w < 0.0)
        if negative:
            raise WeightError(
                f"negative weights {negative}: a negative weight is excluded from "
                "scoring but would still count toward the coverage denominator, "
                "making coverage report a fraction that was never measured"
            )
        for name, weight in weights.items():
            if not math.isfinite(weight):
                raise WeightError(f"{name}={weight!r} is not finite")
        if sum(weights.values()) <= 0.0:
            raise WeightError("all weights are zero: nothing would be measured")

    def digest(self) -> str:
        """Stable digest of the weight values.

        OVER CANONICAL BYTES, not a Python re-serialisation. This originally
        used json.dumps(sort_keys=True) -- the EXACT construction Codex had
        already proved non-portable for model configs, reintroduced here three
        files later because I wrote it before that lesson landed and never went
        back. Python writes the float 1.0 as `1.0` and JavaScript writes `1`, so
        a profile containing a whole number digests differently in the desktop
        shell than in the pipeline, and two identical profiles read as different
        fusions.

        Weights are formatted to a fixed 6-decimal representation, which every
        language produces identically, and the digest is taken over those bytes.
        Six decimals is well past the precision at which a weight change alters
        any ranking.
        """
        payload = ";".join(
            f"{name}={value:.6f}" for name, value in sorted(self.as_map().items())
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
    def measured(self) -> frozenset[str]:
        """Which signals actually fed this score."""
        return frozenset(name for name, _, _ in self.contributions)

    @property
    def comparable(self) -> bool:
        """Whether this score is complete enough to rank at all.

        A weak gate on its own -- see `comparable_with`, which is the one that
        matters.
        """
        return not self.rejected and self.coverage >= DEFAULT_MIN_COVERAGE

    def comparable_with(self, other: "FusedScore") -> bool:
        """Whether these two scores may be ranked AGAINST EACH OTHER.

        Coverage alone was the wrong test, as Codex showed: two photos can both
        clear 52% while one has face quality and the other does not, and the
        heaviest signal in the profile is then present on one side of the
        comparison and absent from the other. The values are not measuring the
        same thing, so ordering them is meaningless however high the coverage.

        The right test is that they were measured the SAME WAY: same signal set,
        same weights, same feature set. Anything else is comparing a portrait
        scored on seven signals with a landscape scored on six and calling the
        difference quality.
        """
        return (
            not self.rejected
            and not other.rejected
            and self.measured == other.measured
            and self.weights_digest == other.weights_digest
            and self.feature_set_id == other.feature_set_id
        )

    def as_feature_map(self) -> dict[str, float]:
        """The signal values that fed this score, shaped for
        PrefEvent.FeatureContext.named.

        Only the signals that were actually measured. A feature map that
        back-filled the missing ones would train the preference model on
        fabricated observations, which is a worse outcome than training it on
        fewer real ones.
        """
        return {name: value for name, value, _ in self.contributions}


def signals_from_media_record(
    record: Mapping, *, face_state: FaceState | None = None
) -> Signals:
    """A contract MediaRecord -> Signals. THE supported way in.

    Codex found the package did not actually connect to the contract it claims
    to consume: `MediaRecord.quality` holds `Score` objects such as
    `{"value": 0.87, "run_id": "..."}`, while `Signals` wanted bare floats, so
    passing a real fixture straight in raised a TypeError from inside a
    comparison. There was no adapter anywhere -- the generated types were being
    referenced in prose and bypassed in practice.

    `face_state` is passed in rather than inferred, because a MediaRecord alone
    cannot distinguish "no faces in this photo" from "face detection has not
    run". That is a property of the PIPELINE's progress, not of the record, and
    guessing it is what made mid-scan photos look fully measured. When omitted,
    the honest default is NOT_RUN: it under-claims coverage rather than
    over-claiming it, and under-claiming only costs a photo its place in an
    automated selection until the scan finishes.
    """
    quality = record.get("quality") or {}

    def unit(name: str) -> float | None:
        raw = quality.get(name)
        if raw is None:
            return None
        if isinstance(raw, Mapping):        # a contract Score
            return raw.get("value")
        if isinstance(raw, (int, float)) and not isinstance(raw, bool):
            return float(raw)               # already flattened by a caller
        raise SignalError(
            f"quality.{name} is {raw!r}; expected a Score object or a number"
        )

    required = {}
    for name in ("sharpness", "exposure"):
        value = unit(name)
        if value is None:
            raise SignalError(
                f"quality.{name} is absent. The classical measures are computed "
                "during ingest, so this is a record that was never successfully "
                "ingested (quarantined, truncated) or is not a still photo at "
                "all -- video is scored by the story engine's moment scoring, "
                "not by photo quality fusion. Either way it must not be silently "
                "scored as a bad photo."
            )
        required[name] = value

    return Signals(
        **required,
        noise=unit("noise"),
        contrast=unit("contrast"),
        technical_iqa=unit("technical_iqa"),
        aesthetic=unit("aesthetic"),
        composition=unit("composition"),
        face_quality=unit("face_quality"),
        face_state=face_state or FaceState.NOT_RUN,
        is_black_frame=bool(quality.get("is_black_frame", False)),
        is_lens_obstructed=bool(quality.get("is_lens_obstructed", False)),
    )


# Weights of the group-photo face-quality aggregate, in one place so the
# renormalised no-eyes-data variant is COMPUTED from these rather than being a
# second set of magic decimals that drifts from the first.
_FACE_AGG_W_MIN = 0.45
_FACE_AGG_W_P10 = 0.20
_FACE_AGG_W_MEAN = 0.20
_FACE_AGG_W_EYES = 0.15


def _p10(sorted_values: Sequence[float]) -> float:
    """Deterministic 10th percentile: lower-value convention, no interpolation.

    The value at index ceil(0.10 * n) - 1 of the ascending sort (floored at 0).
    With n <= 10 that index is 0, so p10 == min -- acceptable and deliberate:
    for the small face counts of a family photo the term simply reinforces the
    min, and it only starts to soften (second-worst, third-worst...) once the
    crowd is large enough that a single worst face should not carry 0.65 of the
    score alone. No interpolation, because interpolated quantiles differ
    between conventions and libraries, and this number feeds a digest-attributed
    score that must be bit-identical across machines.
    """
    index = max(0, math.ceil(0.10 * len(sorted_values)) - 1)
    return sorted_values[index]


def aggregate_face_quality(
    qualities: Sequence[float], eyes_open: Sequence[float | None]
) -> float:
    """One face_quality claim for a photo, from ALL its significant faces.

    Closes the group-photo blind spot: the summary used to be max() over
    significant faces, and a MAX is the wrong shape for the claim a family
    album makes. Nine perfect faces and one blinking key person is not an
    excellent group photo -- so the aggregate is anchored on the WEIGHTED MIN,
    not the mean (owner's framework §6: aggregate per-person with weighted
    min; one blinking person must not hide behind a smiling majority).

        0.45 * min(q)  -- the worst face dominates, by design
      + 0.20 * p10(q)  -- with n <= 10 this equals min (see _p10); in a crowd
                          it is the near-worst, so one stranger's face in the
                          background cannot single-handedly floor a wedding
      + 0.20 * mean(q) -- the majority still counts for something
      + 0.15 * eyes-open ratio, the fraction of faces whose stored eyes_open
        Confidence is >= 0.5. 0.5 is neutral in the stored mapping, so >= is
        "no negative evidence": an unremarkable eye state must not read as a
        blink. Faces with eyes_open None are unmeasured and excluded from the
        ratio -- a missing measurement never defaults to open OR closed. When
        EVERY face is unmeasured (a pre-backfill library) the term is dropped
        and the remaining three weights renormalise to sum to 1, computed from
        the constants above rather than hardcoded, per this module's rule:
        missing signals renormalise, they do not default.

    DEFERRED, DELIBERATELY:
      * A synchrony term (everyone looking at the camera at once) -- needs a
        per-face gaze signal that does not exist yet.
      * The importance-weighted min (the album owner's family outranks a
        photobombing guest) -- needs key-people data from face clusters and
        the review queue; until it exists every significant face weighs the
        same, which over-punishes strangers but never hides a family blink.

    `qualities[i]` and `eyes_open[i]` describe the same significant face:
    qualities are the per-face sharpness x visibility values on [0,1] the
    faces stage fuses, eyes_open the stored [0,1] Confidence or None when
    unmeasured. Empty `qualities` raises SignalError: the faces stage skips
    the summary write entirely when a photo has zero significant faces
    (absence of faces is not a face-quality claim), so an empty call here is
    a caller bug, not a photo state.

    Result quantised to six decimals like every score in this module.
    """
    if len(qualities) == 0:
        raise SignalError(
            "aggregate_face_quality over zero faces. A photo with no "
            "significant faces has NO face_quality -- the faces stage skips "
            "the summary write, it does not write an aggregate of nothing."
        )
    if len(qualities) != len(eyes_open):
        raise SignalError(
            f"{len(qualities)} qualities but {len(eyes_open)} eyes_open values; "
            "index i of both must describe the same face"
        )
    for name, values in (("qualities", qualities), ("eyes_open", eyes_open)):
        for value in values:
            if value is None and name == "eyes_open":
                continue
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise SignalError(f"{name} contains {value!r}, not a number")
            if not math.isfinite(value) or not 0.0 <= value <= 1.0:
                raise SignalError(
                    f"{name} contains {value!r}, outside the [0,1] Unit the "
                    "contract declares; an upstream normalisation is missing"
                )

    ordered = sorted(float(q) for q in qualities)
    quality_terms = (
        (_FACE_AGG_W_MIN, ordered[0]),
        (_FACE_AGG_W_P10, _p10(ordered)),
        (_FACE_AGG_W_MEAN, sum(ordered) / len(ordered)),
    )

    measured_eyes = [e for e in eyes_open if e is not None]
    if measured_eyes:
        ratio = sum(1 for e in measured_eyes if e >= 0.5) / len(measured_eyes)
        total = sum(w * v for w, v in quality_terms) + _FACE_AGG_W_EYES * ratio
    else:
        live = sum(w for w, _ in quality_terms)
        total = sum((w / live) * v for w, v in quality_terms)
    return _quantise(min(max(total, 0.0), 1.0))


# Elimination reasons, in the order they are checked. Order is fixed so two
# hosts report the same reason for a frame that fails several at once.
REJECT_BLACK_FRAME = "black_frame"
REJECT_LENS_OBSTRUCTED = "lens_obstructed"
REJECT_BELOW_SHARPNESS_FLOOR = "below_sharpness_floor"


def eliminate(
    signals: Signals,
    *,
    sharpness_floor: SharpnessFloor | None = DEFAULT_SHARPNESS_FLOOR,
) -> str | None:
    """The reason this frame is junk, or None if it is a candidate.

    Runs before any weighting, because the answer is not "a low number" -- it is
    "this frame does not enter the pool at all". Build plan §4.3: elimination
    first, always.

    WHAT MAY ELIMINATE, AND WHAT MAY ONLY RANK (issue #22)

    Black frame and lens obstruction are DETERMINATIONS. Something upstream
    looked at the frame and concluded a fact about it, and the value is a
    boolean whose meaning does not depend on a scale nobody has defined. They
    eliminate.

    Sharpness is a MEASUREMENT on a continuum, and until a calibrated
    `SharpnessFloor` exists it eliminates nothing -- it only pushes the score
    down, where a weak frame still competes and still wins if the pool is bad
    enough. That distinction is the fix: the previous code treated an
    uncalibrated constant as if it were a determination, and a hard gate on an
    undefined scale deletes photographs without anyone seeing what went.

    Passing a bare float is refused rather than accepted, because "just put the
    number back" is exactly how this returns.
    """
    if signals.is_black_frame:
        return REJECT_BLACK_FRAME
    if signals.is_lens_obstructed:
        return REJECT_LENS_OBSTRUCTED
    if sharpness_floor is None:
        return None
    if not isinstance(sharpness_floor, SharpnessFloor):
        raise UncalibratedFloor(
            f"sharpness_floor={sharpness_floor!r} is a bare number. Elimination "
            "deletes a photograph from a person's memories, so the floor has to "
            "carry the measurement that justifies it: construct a SharpnessFloor "
            "naming the benchmark, the normalisation and the hard negatives it "
            "was checked against."
        )
    if signals.sharpness < sharpness_floor.value:
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

    Excludes face quality only when detection has RUN and found nothing: a
    landscape is not an under-measured portrait, and counting a face weight it
    can never earn would cap every landscape below the comparability threshold.

    NOT_RUN keeps the weight in the denominator, because a photo awaiting face
    detection genuinely is incompletely measured. The previous `has_faces: bool`
    defaulted the unknown case to "no faces", so a record mid-scan reported full
    coverage and ranked as comparable against finished ones -- which is exactly
    the situation during the only period when it matters.
    """
    total = sum(weights.as_map().values())
    if signals.face_state is FaceState.NO_FACES:
        total -= weights.face_quality
    return total


def fuse(
    signals: Signals,
    weights: Weights | None = None,
    *,
    sharpness_floor: SharpnessFloor | None = DEFAULT_SHARPNESS_FLOOR,
) -> FusedScore:
    """Fuse one photo's signals into a single comparable quality score.

    Returns a rejected score with value 0.0 when the frame is eliminated. The
    value is 0.0 rather than None so a caller that ignores `rejected` sorts it
    last rather than raising -- but `rejected` is what callers must branch on,
    because 0.0 is also a legitimate score for a frame that is merely terrible.
    """
    weights = weights or Weights()
    signals.validate()
    weights.validate()

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
    if signals.face_state is FaceState.NO_FACES:
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
    scored: Mapping[str, FusedScore], *, allow_mixed: bool = False
) -> list[str]:
    """Media ids best-first, refusing to order scores that are not comparable.

    THE DEFAULT IS STRICT, AND THAT IS THE FIX. This used to sort everything
    given to it and left comparability as an opt-in flag, so the natural call --
    `rank(everything)` -- happily ranked a photo scored on two signals above one
    scored on eight. Codex measured it: 0.99 at 40% coverage beat 0.80 at 100%.
    An album built mid-scan would then reshuffle completely once analysis
    finished, and nothing would have reported a problem.

    A default that produces a plausible wrong answer is worse than one that
    refuses, because only the refusal gets noticed. `allow_mixed=True` is there
    for a progress preview, where an approximate order is genuinely better than
    none -- but it has to be asked for.

    Ties break on media id, matching `select_primary`: every ordering in this
    package breaks the same way so the two never disagree. Rejected items sort
    last rather than being dropped -- what to do with a pool of only-bad frames
    is the caller's call.
    """
    items = list(scored.items())
    if not allow_mixed:
        live = [(i, s) for i, s in items if not s.rejected]
        if live:
            reference = min(live, key=lambda row: row[0])[1]
            incomparable = sorted(
                media_id for media_id, score in live
                if not reference.comparable_with(score)
            )
            if incomparable:
                raise IncomparableScores(
                    f"cannot rank {incomparable} against {min(live)[0]!r}: they were "
                    "not measured the same way (different signal sets, weights or "
                    "feature sets). Finish the analysis, group by measurement, or "
                    "pass allow_mixed=True if an approximate order is acceptable."
                )
    return [
        media_id
        for media_id, _ in sorted(
            items, key=lambda row: (row[1].rejected, -row[1].value, row[0])
        )
    ]
