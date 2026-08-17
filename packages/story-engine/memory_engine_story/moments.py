"""Moment scoring v1 — sliding window over a fused feature stream (build plan §4.3).

A video arrives here as a per-sample feature stream (motion, shake, exposure,
sharpness, faces, audio, speech) computed on the PROXY by the ml-runtime, plus
shot boundaries and word-level transcript timings. It leaves here as
MomentRecords: scored intervals in SOURCE time, each carrying the cut positions
a planner is allowed to use.

FIVE DECISIONS, AND WHAT THE ALTERNATIVES LOOK LIKE

1. ELIMINATION IS FIRST, AND ELIMINATED FOOTAGE NEVER COMPETES.
   Build plan §4.3: shake, blown exposure, black frames, lens cap, pocket
   footage and tripod dead time discard 90-95% of a real GoPro card before any
   expensive analysis. That is the single biggest cost AND quality lever in the
   system, so it is a gate, not a penalty.

   The tempting alternative — score everything, let the bad stuff sink — fails
   exactly where it matters. A low score still competes, and still wins whenever
   the pool is bad enough, which is precisely the tail of an action-camera card
   where the alternative to a pocket shot is another pocket shot. Here an
   eliminated sample cannot be inside any candidate window at all: windows are
   generated only inside clean segments, so the ranking never sees the footage.

   Eliminated regions are still EMITTED, as MomentRecords with
   `elimination.eliminated = true` and the reasons. Per the schema's own comment
   a record that exists only to say "rejected, shake" is normal and valuable —
   it is what the culling UI renders, and dropping it silently would violate
   hard rule 7 (no silent data loss) as well as making "your 40 usable minutes"
   unexplainable.

2. SNAP POINTS ARE THE PRODUCT; A MID-WORD CUT IS A DIFFERENT CLASS OF BUG.
   A slightly-wrong score costs a viewer a mediocre second. A cut through a word
   is instantly perceptible and reads as broken. So:

     * boundaries are chosen from detected motion onsets, audio onsets, speech
       gaps and shot boundaries — never from the window edge when a snap point
       is available;
     * any snap point that falls STRICTLY INSIDE a spoken word is discarded
       before the planner ever sees it. The contract says the planner may only
       cut on snap points, so leaving a mid-word onset in the list is the same
       as authorising the cut. The expressiveness that loses (a visual cut under
       continuing speech) is recovered by `safe_trim.preserve_audio_tail`, which
       is how an L-cut is meant to be expressed;
     * when a fallback boundary (a segment edge, with no snap in range) does
       land inside a word, it is pushed OUT to the next whole frame past the
       word — `math.ceil` for an in-point, `math.floor` for an out-point, so
       rounding can never move a boundary back into the word it just escaped.

3. HAND-WEIGHTED LINEAR FUSION, SAME SHAPE AS packages/ranking-engine/fusion.py.
   Missing signals renormalise rather than defaulting (defaulting to 0 punishes
   footage the expensive model has not reached; defaulting to 0.5 fabricates a
   measurement that no later audit can separate from a real mediocre one).
   Coverage is reported, never folded into the value. Not-applicable is
   distinguished from not-measured: when face detection HAS run and found no
   face in the window, `smile_intensity` is not an under-measurement — its
   weight leaves the denominator, exactly as `face_quality` does for a landscape
   in the photo fusion. Getting that wrong caps every faceless action shot below
   the comparability threshold.

   Coverage cannot live in the record: MomentRecord is additionalProperties:false
   and has no field for it. So `plan_moments` returns ScoredMoment objects that
   carry the record AND its fusion provenance, and the caller stores the record.

4. LOCAL MAXIMA, WITH SUPPRESSION AT LEAST ONE WINDOW WIDE.
   Windows overlap by design (a 2s window every 0.25s), so the raw score series
   has a plateau around every good second. Emitting each plateau sample would
   put the same footage in a reel eight times. Greedy non-maximum suppression
   over the score series solves it: best window first, then reject anything
   whose centre is within the suppression radius. The radius is
   max(min_separation, window) — smaller than a window would let two accepted
   moments share most of their frames.

   Two peaks 0.5s apart are therefore ONE moment (the stronger one), and the
   long boring stretch containing one good second yields exactly one moment at
   that second, with the surrounding windows suppressed rather than emitted.

5. THE EMITTED SCORE DESCRIBES THE EMITTED RANGE.
   Selection scores the fixed-length window; the record then covers a snapped,
   possibly longer range. Re-fusing over the final range is not cosmetic: a
   record whose score was measured on footage the record does not cover is a
   silent lie of exactly the kind this codebase keeps finding. Both numbers are
   kept — `ScoredMoment.peak_value` (what won the selection) and
   `ScoredMoment.fusion` (what the record claims) — so a disagreement between
   them is visible instead of hidden.

DETERMINISM
Same stream + same policy + same weights = identical records, byte for byte.
Every iteration is over a sorted sequence, every tie is broken explicitly (score
then start time then id), every float that reaches a record is quantised to six
decimals, and no output ever depends on dict or set iteration order. "Same plan
= identical render" (CLAUDE.md hard rule 3) is only true if the plan itself is
reproducible.

IDS
`moment_id` is BLAKE3 over (media_id, source_range, scorer model_id+version), as
the schema requires. blake3 is not in the standard library and this package is
stdlib-only, so the hasher is injected: `plan_moments(..., id_hasher=...)`. The
default tries to import blake3 and RAISES if it is absent rather than quietly
substituting blake2b — an id that claims to be a BLAKE3 content address and is
not would corrupt idempotency across the whole system, and nothing downstream
could detect it.
"""

from __future__ import annotations

import bisect
import hashlib
import math
import re
from dataclasses import dataclass, field
from typing import Callable, Mapping, Sequence

SCHEMA_VERSION = "v0"

# Bump when a signal is added, removed or redefined. Matches
# PrefEvent.FeatureContext.feature_set_id: a stored feature map is meaningless
# without knowing which feature list it was written against.
FEATURE_SET_ID = "moment-fusion-v1"

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")

# Float comparisons on the sample grid. Word timings are real-valued (whisper
# gives seconds), so times that are conceptually equal can differ in the last
# bit. Everything smaller than this is the same instant; it is ~1e-6 of a frame.
_EPS = 1e-9

# Every weighted fusion signal, in one place, so the weight map, the presence
# map and the validation cannot drift out of step.
SIGNAL_NAMES = (
    "motion_energy",
    "motion_peak",
    "stability",
    "exposure_stability",
    "sharpness",
    "face_presence",
    "smile_intensity",
    "audio_energy",
    "speech_presence",
    "novelty",
)

# Reasons in the order they are reported. Fixed so two hosts describe a frame
# that fails several checks at once with the same list in the same order.
ELIMINATION_ORDER = (
    "black_frame",
    "lens_obstructed",
    "blown_exposure",
    "crushed_exposure",
    "shake",
    "wind_noise_dominant",
    "no_motion",
    "too_short",
    "below_score_floor",
)

# Audio events that mean "something happened here" rather than "sound existed".
# Used for the local emotional-peak approximation and for the L-cut test.
PEAK_EVENT_LABELS = frozenset(
    {"laughter", "cheering", "applause", "singing", "shouting", "fireworks"}
)


class StreamError(ValueError):
    """A feature stream that would corrupt the scoring rather than lower it."""


class PolicyError(ValueError):
    """A policy whose thresholds cannot produce a well-formed moment."""


class WeightError(ValueError):
    """A weighting profile that cannot produce an honest coverage figure."""


class MomentIdUnavailable(RuntimeError):
    """No BLAKE3 implementation, and no id_hasher supplied.

    Deliberately fatal. The alternative — hashing with blake2b and writing the
    result into a field the contract defines as a BLAKE3 content address —
    produces ids that look right, collide with nothing, and make every
    downstream idempotency check silently wrong.
    """


# --------------------------------------------------------------------------
# Inputs
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Frame:
    """One sample of the fused per-frame feature stream.

    Every field is None when the corresponding analysis has not run. None means
    NOT MEASURED and is renormalised away; 0.0 means measured-and-zero and
    counts fully. Conflating the two is the mistake that makes a mid-scan
    ranking invert relative to the finished one.

    Units are as the contract defines them: everything is a Unit in [0,1]
    except `loudness_lufs`, which is real LUFS in [-70,0] because that is what
    MomentRecord.features.audio stores and re-deriving it from a normalised
    number is lossy.
    """

    motion: float | None = None
    shake: float | None = None
    exposure_stability: float | None = None
    sharpness: float | None = None
    luma: float | None = None
    clipped_highlights: float | None = None
    clipped_shadows: float | None = None
    face_presence: float | None = None
    max_face_area_ratio: float | None = None
    smile_intensity: float | None = None
    loudness_lufs: float | None = None
    speech: float | None = None
    noise: float | None = None
    novelty: float | None = None
    lens_obstructed: bool = False

    _UNITS = (
        "motion", "shake", "exposure_stability", "sharpness", "luma",
        "clipped_highlights", "clipped_shadows", "face_presence",
        "max_face_area_ratio", "smile_intensity", "speech", "noise", "novelty",
    )

    def validate(self, index: int) -> None:
        for name in self._UNITS:
            value = getattr(self, name)
            if value is None:
                continue
            _require_number(value, f"frame[{index}].{name}")
            if not 0.0 <= value <= 1.0:
                raise StreamError(
                    f"frame[{index}].{name}={value} is outside [0,1]. The contract "
                    "declares these as Unit; a value outside it means an upstream "
                    "normalisation is missing, and clamping here would hide that."
                )
        if self.loudness_lufs is not None:
            _require_number(self.loudness_lufs, f"frame[{index}].loudness_lufs")
            if not -70.0 <= self.loudness_lufs <= 0.0:
                raise StreamError(
                    f"frame[{index}].loudness_lufs={self.loudness_lufs} is outside "
                    "[-70,0], the range MomentRecord.features.audio declares."
                )


@dataclass(frozen=True)
class Word:
    """A transcribed word with its timing, in the stream's rate units.

    Word times are NOT snapped to the sample grid. Rounding a word boundary
    outward by half a frame is exactly the error that clips a consonant, and the
    contract stores RationalTime values that may be fractional for this reason.
    """

    word: str
    start: float
    end: float
    confidence: float | None = None

    @classmethod
    def from_seconds(cls, word: str, start_s: float, end_s: float, rate: float,
                     *, confidence: float | None = None) -> "Word":
        return cls(word, start_s * rate, end_s * rate, confidence)


@dataclass(frozen=True)
class AudioEvent:
    """A CLAP audio event at a point in time, in the stream's rate units."""

    label: str
    confidence: float
    time: float


@dataclass(frozen=True)
class Shot:
    """A TransNetV2 shot, as a half-open sample-index range [start, end).

    A moment never crosses a shot boundary — crossing one is a cut, and cuts
    belong to the planner (MomentRecord.shot_id).
    """

    shot_id: str
    start: int
    end: int


@dataclass(frozen=True)
class FeatureStream:
    """One media item's feature stream plus everything needed to place it in
    source time.

    Sample i sits at source RationalTime {value: start_value + i, rate: rate}.
    Keeping the stream on the source frame grid means every emitted time is
    exact — no proxy-to-source float conversion ever reaches a record, which is
    what the 50ms beat-alignment gate needs.

    `proxy_rate`/`proxy_start_value` are optional and only populate
    `proxy_range`, whose stated purpose is to let a re-score reuse the cached
    proxy. Mapping to the proxy grid rounds to the nearest proxy frame; that is
    acceptable here and nowhere else, because source_range remains authoritative.
    """

    media_id: str
    rate: float
    frames: Sequence[Frame]
    start_value: float = 0.0
    shots: Sequence[Shot] = ()
    words: Sequence[Word] = ()
    audio_events: Sequence[AudioEvent] = ()
    language: str | None = None
    proxy_rate: float | None = None
    proxy_start_value: float | None = None

    def validate(self) -> None:
        if not _HEX64_RE.match(self.media_id or ""):
            raise StreamError(
                f"media_id={self.media_id!r} is not a BLAKE3 hex digest; the "
                "contract addresses media by content hash"
            )
        _require_number(self.rate, "rate")
        if self.rate <= 0:
            raise StreamError("rate must be > 0 (RationalTime.rate is exclusiveMinimum 0)")
        _require_number(self.start_value, "start_value")
        for index, frame in enumerate(self.frames):
            frame.validate(index)
        n = len(self.frames)
        for shot in self.shots:
            if shot.start < 0 or shot.end > n or shot.start >= shot.end:
                raise StreamError(
                    f"shot {shot.shot_id!r} spans [{shot.start},{shot.end}) which is "
                    f"not a non-empty range inside [0,{n})"
                )
            if not _SLUG_RE.match(shot.shot_id or ""):
                raise StreamError(
                    f"shot_id={shot.shot_id!r} is not a contract Slug "
                    "(^[a-z0-9][a-z0-9_-]{0,63}$)"
                )
        for word in self.words:
            _require_number(word.start, f"word {word.word!r}.start")
            _require_number(word.end, f"word {word.word!r}.end")
            if word.end < word.start:
                raise StreamError(f"word {word.word!r} ends before it starts")
        for event in self.audio_events:
            _require_number(event.time, f"audio event {event.label!r}.time")
            _require_number(event.confidence, f"audio event {event.label!r}.confidence")
        if (self.proxy_rate is None) != (self.proxy_start_value is None):
            raise StreamError(
                "proxy_rate and proxy_start_value must be given together; half a "
                "mapping cannot place the moment on the proxy grid"
            )
        if self.proxy_rate is not None and self.proxy_rate <= 0:
            raise StreamError("proxy_rate must be > 0")

    @property
    def sorted_words(self) -> tuple[Word, ...]:
        """Words in time order. Never iterate `words` directly: whisper output
        is usually ordered and occasionally is not, and a single out-of-order
        word silently breaks the gap detection that the no-mid-word guarantee
        rests on."""
        return tuple(sorted(self.words, key=lambda w: (w.start, w.end, w.word)))


# --------------------------------------------------------------------------
# Policy and weights
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Policy:
    """Thresholds, as data. A per-camera or per-user profile is then a stored
    row rather than a code change.

    The elimination numbers are deliberately CONSERVATIVE in the sense that
    matters: they discard footage that is unusable by construction (a black
    frame, a lens cap, a shake level no stabiliser recovers) and leave
    "merely bad" footage to the score floor. Eliminating aggressively here is
    unrecoverable — the user never sees the frame again — whereas a low score
    still surfaces when the pool is thin.
    """

    # windowing
    window_seconds: float = 2.0
    hop_seconds: float = 0.25
    min_separation_seconds: float = 1.5
    min_moment_seconds: float = 0.6
    max_moment_seconds: float = 6.0
    max_extend_seconds: float = 1.0

    # fusion
    score_floor: float = 0.35
    min_sample_coverage: float = 0.5

    # classical elimination
    black_luma_max: float = 0.02
    blown_highlight_max: float = 0.35
    crushed_shadow_max: float = 0.60
    shake_max: float = 0.55
    pocket_luma_max: float = 0.08
    pocket_sharpness_max: float = 0.06
    noise_max: float = 0.80
    dead_motion_max: float = 0.02
    dead_time_seconds: float = 3.0
    # A locked-off interview is the one case where a static frame IS the point.
    # Without this, tripod dead-time elimination throws away every to-camera
    # shot in the library.
    dead_time_speech_max: float = 0.20

    # snap-point detection
    motion_onset_delta: float = 0.15
    motion_onset_level: float = 0.20
    motion_onset_full: float = 0.50
    onset_nms_seconds: float = 0.20
    audio_onset_db: float = 6.0
    audio_onset_db_full: float = 18.0
    brightness_delta: float = 0.25
    brightness_delta_full: float = 0.50
    speech_gap_seconds: float = 0.35
    gap_strength_full_seconds: float = 1.0
    shot_boundary_strength: float = 0.97
    # Cost, in strength units per second of displacement, of moving a boundary
    # away from where the window wanted it. The schema's own guidance: cutting
    # on a weak onset is worse than cutting 40ms later on a strong one.
    snap_distance_cost: float = 0.25

    # L-cut detection
    audio_tail_seconds: float = 0.5
    audio_tail_event_confidence: float = 0.5

    # hook
    hook_seconds: float = 0.5

    def validate(self) -> None:
        for name in (
            "window_seconds", "hop_seconds", "min_separation_seconds",
            "min_moment_seconds", "max_moment_seconds", "dead_time_seconds",
            "speech_gap_seconds", "hook_seconds",
        ):
            value = getattr(self, name)
            _require_number(value, f"policy.{name}", error=PolicyError)
            if value <= 0:
                raise PolicyError(f"policy.{name}={value} must be > 0")
        if self.max_extend_seconds < 0:
            raise PolicyError("policy.max_extend_seconds must be >= 0")
        if self.min_moment_seconds > self.max_moment_seconds:
            raise PolicyError(
                f"min_moment_seconds={self.min_moment_seconds} exceeds "
                f"max_moment_seconds={self.max_moment_seconds}: no moment could "
                "satisfy both bounds, so every candidate would be dropped as "
                "too_short and the run would produce nothing while looking healthy"
            )
        if self.window_seconds < self.min_moment_seconds:
            raise PolicyError(
                f"window_seconds={self.window_seconds} is shorter than "
                f"min_moment_seconds={self.min_moment_seconds}: every window would "
                "have to be extended past its own extent to be legal"
            )
        if not 0.0 <= self.score_floor <= 1.0:
            raise PolicyError("policy.score_floor must be within [0,1]")
        if not 0.0 < self.min_sample_coverage <= 1.0:
            raise PolicyError("policy.min_sample_coverage must be within (0,1]")

    def grid(self, rate: float) -> "Grid":
        return Grid(
            window=_samples(self.window_seconds, rate),
            hop=_samples(self.hop_seconds, rate),
            separation=_samples(self.min_separation_seconds, rate),
            min_moment=_samples(self.min_moment_seconds, rate),
            max_moment=_samples(self.max_moment_seconds, rate),
            extend=max(0, _samples(self.max_extend_seconds, rate) if self.max_extend_seconds else 0),
            dead_time=_samples(self.dead_time_seconds, rate),
            onset_nms=_samples(self.onset_nms_seconds, rate),
            speech_gap=self.speech_gap_seconds * rate,
            hook=_samples(self.hook_seconds, rate),
            audio_tail=self.audio_tail_seconds * rate,
        )


@dataclass(frozen=True)
class Grid:
    """Policy seconds resolved onto this stream's sample grid, once."""

    window: int
    hop: int
    separation: int
    min_moment: int
    max_moment: int
    extend: int
    dead_time: int
    onset_nms: int
    speech_gap: float
    hook: int
    audio_tail: float


@dataclass(frozen=True)
class Weights:
    """A moment-fusion weighting profile.

    The v1 numbers are chosen to be defensible rather than optimal; the eval
    harness moves them.

      * `stability` (1 - shake) carries the most weight of any single signal
        because shake is the most common reason handheld and action footage is
        unusable, and unlike exposure it cannot be graded back.
      * `face_presence` is heavy: in a family library a frame with a face beats
        a marginally better-exposed one without.
      * `smile_intensity` is a PEAK signal, aggregated by max rather than mean —
        one frame where a child actually laughs is the moment; a mean over the
        window dilutes it to nothing.
      * `novelty` is light. It is the anti-repetition term, and the diversity
        constraint in the reel planner is where repetition is really solved.
    """

    weights_id: str = "moment-default-v1"

    motion_energy: float = 0.12
    motion_peak: float = 0.06
    stability: float = 0.16
    exposure_stability: float = 0.10
    sharpness: float = 0.12
    face_presence: float = 0.14
    smile_intensity: float = 0.08
    audio_energy: float = 0.08
    speech_presence: float = 0.06
    novelty: float = 0.08

    def as_map(self) -> dict[str, float]:
        return {name: getattr(self, name) for name in SIGNAL_NAMES}

    def validate(self) -> None:
        """Refuse a profile that cannot produce an honest coverage figure.

        A NEGATIVE weight is excluded from scoring (the live filter takes > 0)
        while still counting toward the coverage DENOMINATOR, so coverage would
        report a fraction that was never measured — the identical defect Codex
        found in the photo fusion. Zero is allowed and means "this profile does
        not use that signal".
        """
        weights = self.as_map()
        negative = sorted(name for name, w in weights.items() if w < 0.0)
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
        """Stable digest over canonical bytes, not a Python re-serialisation.

        json.dumps(sort_keys=True) is not portable: Python writes the float 1.0
        as `1.0` and JavaScript writes `1`, so the same profile digests
        differently in the desktop shell and in the pipeline. Fixed 6-decimal
        formatting is produced identically by every language we ship.
        """
        payload = ";".join(
            f"{name}={value:.6f}" for name, value in sorted(self.as_map().items())
        ).encode("utf-8")
        return hashlib.blake2b(payload, digest_size=16).hexdigest()


@dataclass(frozen=True)
class Scorer:
    """The scorer identity that feeds `moment_id`.

    Rescoring with a new model must yield new ids, so an EDL always points at
    the exact moment definition it was planned against (schema, moment_id).
    """

    model_id: str = "moment-fusion-linear"
    version: str = "1.0.0"


DEFAULT_SCORER = Scorer()


# --------------------------------------------------------------------------
# Outputs
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class FusedWindow:
    """A fused score and everything needed to defend it.

    `coverage` lives here rather than in the record because MomentRecord is
    additionalProperties:false. It must still be reported: 0.82 from three
    signals and 0.82 from ten are not the same claim.
    """

    value: float
    coverage: float
    weights_id: str
    weights_digest: str
    feature_set_id: str = FEATURE_SET_ID
    contributions: tuple[tuple[str, float, float], ...] = ()

    @property
    def measured(self) -> frozenset[str]:
        return frozenset(name for name, _, _ in self.contributions)

    def comparable_with(self, other: "FusedWindow") -> bool:
        """Whether two moment scores may be ordered against each other.

        Same test as the photo fusion, for the same reason: two windows can both
        clear a coverage bar while one has audio and the other does not, and
        ordering them then compares different measurements and calls the
        difference quality.
        """
        return (
            self.measured == other.measured
            and self.weights_digest == other.weights_digest
            and self.feature_set_id == other.feature_set_id
        )

    def as_feature_map(self) -> dict[str, float]:
        """Signals that actually fed this score, shaped for
        PrefEvent.FeatureContext.named. Never back-filled: training a preference
        model on fabricated observations is worse than training it on fewer real
        ones."""
        return {name: value for name, value, _ in self.contributions}


@dataclass(frozen=True)
class ScoredMoment:
    """One MomentRecord plus the provenance the record has no field for."""

    record: dict
    fusion: FusedWindow
    peak_value: float
    in_snap_kind: str | None = None
    out_snap_kind: str | None = None

    @property
    def eliminated(self) -> bool:
        return bool(self.record["elimination"]["eliminated"])

    @property
    def start(self) -> float:
        return float(self.record["source_range"]["start_time"]["value"])

    @property
    def duration(self) -> float:
        return float(self.record["source_range"]["duration"]["value"])


@dataclass(frozen=True)
class MomentPlan:
    """Everything one stream produced: what survived, what did not, and why.

    `eliminated` is not debris. It is the culling product ("your 40 usable
    minutes"), and it is the audit trail that makes elimination-first
    reviewable instead of a black hole.
    """

    media_id: str
    moments: tuple[ScoredMoment, ...] = ()
    eliminated: tuple[ScoredMoment, ...] = ()
    sample_count: int = 0
    eliminated_samples: int = 0
    candidates_considered: int = 0
    weights_id: str = ""
    weights_digest: str = ""
    feature_set_id: str = FEATURE_SET_ID

    @property
    def records(self) -> tuple[dict, ...]:
        """Every record, kept and eliminated, in time order.

        Sorted by (start, duration, moment_id) rather than by construction order
        so the stored sequence does not depend on which loop produced which
        record.
        """
        rows = list(self.moments) + list(self.eliminated)
        rows.sort(key=lambda m: (m.start, m.duration, m.record["moment_id"]))
        return tuple(row.record for row in rows)

    @property
    def eliminated_fraction(self) -> float:
        if self.sample_count == 0:
            return 0.0
        return _quantise(self.eliminated_samples / self.sample_count)


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------


def _require_number(value, label: str, *, error=StreamError) -> None:
    """Reject non-numbers and non-finite numbers.

    NaN is the reason this exists. `NaN >= threshold` is False, so a NaN shake
    value passes every elimination gate untouched, then propagates into the
    fused score, and finally makes the ordering of moments meaningless — `<` is
    False in both directions, so the sort is stable-but-arbitrary and the same
    library produces a different reel on a different day. A comparison-based
    gate cannot defend itself against a value that compares False to everything.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise error(f"{label}={value!r} is not a number")
    if not math.isfinite(value):
        raise error(
            f"{label}={value!r} is not finite. NaN compares False to every "
            "threshold, so it would pass the elimination gates untouched and "
            "produce an unorderable score."
        )


def _samples(seconds: float, rate: float) -> int:
    """Seconds -> whole samples, at least one.

    Explicit half-up rounding rather than round(): Python's round() is
    half-to-even, so a 0.5-sample duration would round to 0 or 1 depending on
    the neighbouring integer, which is a policy value silently changing meaning
    with the frame rate.
    """
    return max(1, int(seconds * rate + 0.5))


def _quantise(value: float) -> float:
    """Six decimals, matching the precision Score stores.

    Float addition is not associative, so an unquantised fusion can differ in
    the last bit between machines. Invisible until it reorders a tie in the
    peak selection and a different second of footage silently lands in the reel.
    """
    return round(value + 0.0, 6)


def _clamp01(value: float) -> float:
    return min(1.0, max(0.0, value))


def _rt(value: float, rate: float) -> dict:
    """A contract RationalTime.

    Integral values are emitted as int: the schema states integral values are
    strongly preferred on video tracks, and `3049200` round-trips through every
    JSON implementation identically while `3049200.0` does not.
    """
    quantised = _quantise(value)
    if abs(quantised - round(quantised)) < _EPS:
        quantised = int(round(quantised))
    return {"value": quantised, "rate": rate}


# --------------------------------------------------------------------------
# Elimination
# --------------------------------------------------------------------------


def eliminate_frames(stream: FeatureStream, policy: Policy) -> tuple[frozenset[str], ...]:
    """Per-sample elimination reasons. Empty set = the sample may compete.

    Runs before any weighting, because the answer for a black frame is not "a
    low number", it is "this footage does not enter the pool at all".
    """
    grid = policy.grid(stream.rate)
    reasons: list[set[str]] = []
    for frame in stream.frames:
        found: set[str] = set()
        black = frame.luma is not None and frame.luma <= policy.black_luma_max
        if black:
            found.add("black_frame")
        if frame.lens_obstructed:
            found.add("lens_obstructed")
        elif (
            frame.luma is not None
            and frame.sharpness is not None
            and frame.luma <= policy.pocket_luma_max
            and frame.sharpness <= policy.pocket_sharpness_max
        ):
            # Pocket footage: dark AND textureless. Either alone is a legitimate
            # night shot or a legitimate shallow-depth close-up; together there
            # is nothing in the frame.
            found.add("lens_obstructed")
        if (
            frame.clipped_highlights is not None
            and frame.clipped_highlights >= policy.blown_highlight_max
        ):
            found.add("blown_exposure")
        if (
            not black
            and frame.clipped_shadows is not None
            and frame.clipped_shadows >= policy.crushed_shadow_max
        ):
            # Suppressed when the frame is already black: a black frame is the
            # specific diagnosis, and reporting both makes the culling UI give
            # two names to one cause.
            found.add("crushed_exposure")
        if frame.shake is not None and frame.shake >= policy.shake_max:
            found.add("shake")
        if frame.noise is not None and frame.noise >= policy.noise_max:
            found.add("wind_noise_dominant")
        reasons.append(found)

    _mark_dead_time(stream, policy, grid, reasons)
    return tuple(frozenset(row) for row in reasons)


def _mark_dead_time(
    stream: FeatureStream, policy: Policy, grid: Grid, reasons: list[set[str]]
) -> None:
    """Tripod dead time: a sustained run of near-zero motion.

    Region-level, not per-frame, and that is the whole point. A two-second pause
    inside a good shot is a breath; three seconds of a camera pointed at a wall
    is dead time. A per-frame threshold cannot tell them apart and would eat the
    still beat before every reveal.
    """
    frames = stream.frames
    n = len(frames)
    index = 0
    while index < n:
        motion = frames[index].motion
        if motion is None or motion > policy.dead_motion_max:
            index += 1
            continue
        end = index
        while end < n:
            value = frames[end].motion
            if value is None or value > policy.dead_motion_max:
                break
            end += 1
        if end - index >= grid.dead_time:
            speech = [
                frames[i].speech for i in range(index, end) if frames[i].speech is not None
            ]
            talking = bool(speech) and (sum(speech) / len(speech)) > policy.dead_time_speech_max
            if not talking:
                for i in range(index, end):
                    reasons[i].add("no_motion")
        index = max(end, index + 1)


def _runs(mask: Sequence[bool], wanted: bool) -> list[tuple[int, int]]:
    """Maximal half-open runs [start, end) where mask == wanted."""
    out: list[tuple[int, int]] = []
    start: int | None = None
    for index, value in enumerate(mask):
        if value == wanted and start is None:
            start = index
        elif value != wanted and start is not None:
            out.append((start, index))
            start = None
    if start is not None:
        out.append((start, len(mask)))
    return out


def _segments(
    stream: FeatureStream, eliminated: Sequence[bool]
) -> list[tuple[int, int, str | None]]:
    """Clean runs intersected with shots: the regions a moment may live in.

    Intersecting here rather than checking later is what makes "a moment never
    crosses a shot boundary" structural instead of a rule the extension code has
    to remember.
    """
    clean = _runs(eliminated, False)
    if not stream.shots:
        return [(start, end, None) for start, end in clean]
    shots = sorted(stream.shots, key=lambda s: (s.start, s.end, s.shot_id))
    out: list[tuple[int, int, str | None]] = []
    for start, end in clean:
        for shot in shots:
            lo = max(start, shot.start)
            hi = min(end, shot.end)
            if lo < hi:
                out.append((lo, hi, shot.shot_id))
    out.sort()
    return out


# --------------------------------------------------------------------------
# Aggregation and fusion
# --------------------------------------------------------------------------


def aggregate(
    stream: FeatureStream, lo: int, hi: int, policy: Policy
) -> dict[str, float]:
    """Window features. A signal is present only if enough samples carry it.

    The coverage gate matters: a mean over 2 of 60 samples is not a measurement
    of the window, and treating it as one lets a single stray frame decide a
    moment. `smile_intensity` and `max_face_area_ratio` are deliberately exempt
    because they are peak signals — the one frame where someone laughs IS the
    measurement, and a coverage gate would discard exactly the signal we want.
    """
    frames = stream.frames
    span = hi - lo
    if span <= 0:
        return {}
    needed = max(1, math.ceil(policy.min_sample_coverage * span))
    out: dict[str, float] = {}

    def column(name: str) -> list[float]:
        return [
            getattr(frames[i], name)
            for i in range(lo, hi)
            if getattr(frames[i], name) is not None
        ]

    motion = column("motion")
    if len(motion) >= needed:
        out["motion_energy"] = sum(motion) / len(motion)
        out["motion_peak"] = max(motion)
    shake = column("shake")
    if len(shake) >= needed:
        mean_shake = sum(shake) / len(shake)
        out["shake"] = mean_shake
        out["stability"] = 1.0 - mean_shake
    exposure = column("exposure_stability")
    if len(exposure) >= needed:
        out["exposure_stability"] = sum(exposure) / len(exposure)
    sharpness = column("sharpness")
    if len(sharpness) >= needed:
        out["sharpness"] = sum(sharpness) / len(sharpness)
    faces = column("face_presence")
    if len(faces) >= needed:
        out["face_presence"] = sum(faces) / len(faces)
    smiles = column("smile_intensity")
    if smiles:
        out["smile_intensity"] = max(smiles)
    areas = column("max_face_area_ratio")
    if areas:
        out["max_face_area_ratio"] = max(areas)
    loudness = column("loudness_lufs")
    if len(loudness) >= needed:
        mean_lufs = sum(loudness) / len(loudness)
        out["loudness_lufs"] = mean_lufs
        # -70 LUFS is digital silence and 0 is full scale; the contract stores
        # LUFS and fusion needs a Unit, so the mapping is stated once, here.
        out["audio_energy"] = _clamp01((mean_lufs + 70.0) / 70.0)
    speech = column("speech")
    if len(speech) >= needed:
        out["speech_presence"] = sum(speech) / len(speech)
    noise = column("noise")
    if len(noise) >= needed:
        out["noise_ratio"] = sum(noise) / len(noise)
    novelty = column("novelty")
    if len(novelty) >= needed:
        out["novelty"] = sum(novelty) / len(novelty)
    return out


def _renormalised(
    values: Mapping[str, float],
    weight_map: Mapping[str, float],
    not_applicable: frozenset[str] = frozenset(),
) -> tuple[float, float, tuple[tuple[str, float, float], ...]]:
    """Weighted mean over PRESENT signals, with the weights renormalised.

    Returns (value, coverage, contributions). Coverage is live weight over
    APPLICABLE weight — applicable excludes signals that could never have been
    measured for this window (see `not_applicable`), because a faceless action
    shot is not an under-measured portrait and counting a smile weight it can
    never earn would cap it below every comparability bar in the system.
    """
    live = {
        name: weight_map[name]
        for name in weight_map
        if name in values and name not in not_applicable and weight_map[name] > 0.0
    }
    live_total = sum(live.values())
    applicable = sum(
        weight for name, weight in weight_map.items()
        if name not in not_applicable and weight > 0.0
    )
    if live_total <= 0.0:
        return 0.0, 0.0, ()
    total = 0.0
    contributions: list[tuple[str, float, float]] = []
    for name in sorted(live):  # fixed summation order across runs and machines
        share = live[name] / live_total
        total += values[name] * share
        contributions.append((name, _quantise(values[name]), _quantise(share)))
    coverage = live_total / applicable if applicable > 0.0 else 0.0
    return (
        _quantise(_clamp01(total)),
        _quantise(min(coverage, 1.0)),
        tuple(contributions),
    )


def _not_applicable(values: Mapping[str, float]) -> frozenset[str]:
    """Signals that could not exist for this window, as opposed to not measured.

    Face detection having RUN and found nothing (face_presence present and zero)
    means there is no smile to measure. Treating that as an under-measurement is
    the same defect the photo fusion had with `face_quality` on landscapes.
    """
    if values.get("face_presence") == 0.0 and "smile_intensity" not in values:
        return frozenset({"smile_intensity"})
    return frozenset()


def fuse_window(values: Mapping[str, float], weights: Weights) -> FusedWindow:
    """Fuse one window's aggregate into a comparable moment score."""
    weights.validate()
    value, coverage, contributions = _renormalised(
        values, weights.as_map(), _not_applicable(values)
    )
    return FusedWindow(
        value=value,
        coverage=coverage,
        weights_id=weights.weights_id,
        weights_digest=weights.digest(),
        contributions=contributions,
    )


# The sub-scores. Each is a renormalised fusion over its own weight map, so a
# missing signal behaves the same way everywhere in this file.
_TECHNICAL_WEIGHTS = {"stability": 0.4, "exposure_stability": 0.3, "sharpness": 0.3}
_HOOK_WEIGHTS = {"motion_energy": 0.4, "motion_peak": 0.2, "face_presence": 0.4}
_EMOTION_WEIGHTS = {"smile_intensity": 0.4, "peak_event": 0.4, "motion_peak": 0.2}


def _technical(values: Mapping[str, float]) -> float | None:
    value, coverage, _ = _renormalised(values, _TECHNICAL_WEIGHTS)
    return value if coverage > 0.0 else None


def _hook(lead: Mapping[str, float], whole: Mapping[str, float]) -> float | None:
    """Fitness for the first second of a reel.

    Measured on the LEAD of the moment, not on the whole of it — retention is
    won in the first second, and a moment that is dull for two seconds and then
    spectacular is a bad hook however good its mean. The build penalty then
    scales it by how much of the moment's energy is already present at the
    start, so a slow build is punished without being zeroed.
    """
    value, coverage, _ = _renormalised(lead, _HOOK_WEIGHTS)
    if coverage <= 0.0:
        return None
    lead_motion = lead.get("motion_energy")
    whole_motion = whole.get("motion_energy")
    if lead_motion is not None and whole_motion is not None and whole_motion > 0.0:
        ratio = _clamp01(lead_motion / whole_motion)
        value *= 0.7 + 0.3 * ratio
    # Deliberately NOT quantised here: `_score` is the single place where a
    # number is clamped and rounded on its way into a record. Quantising in both
    # places means neither is load-bearing, and a sub-score added later that
    # forgets to do it would reach the record unrounded with nothing to catch it.
    return value


def _emotional_peak(
    values: Mapping[str, float], peak_event: float | None
) -> float | None:
    """Local approximation only. The frontier model is the one that can tell
    'child sees the ocean' from 'child near ocean'; when it has ruled,
    `scores.source` says so and this number is replaced, not blended."""
    inputs = dict(values)
    if peak_event is not None:
        inputs["peak_event"] = peak_event
    value, coverage, _ = _renormalised(inputs, _EMOTION_WEIGHTS)
    return value if coverage > 0.0 else None


# --------------------------------------------------------------------------
# Snap points
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class SnapPoint:
    """A time at which cutting is defensible, with a reason.

    `confidence` is deliberately absent: the schema defines it as a CALIBRATED
    probability, and nothing here is calibrated against a validation set.
    Emitting a made-up 0.9 would be a claim we cannot back, and the field is
    optional precisely so we do not have to make one.
    """

    time: float
    kind: str
    strength: float
    cut_direction: str

    def as_dict(self, rate: float) -> dict:
        return {
            "time": _rt(self.time, rate),
            "kind": self.kind,
            "strength": _quantise(_clamp01(self.strength)),
            "cut_direction": self.cut_direction,
        }


def snap_points(
    stream: FeatureStream, policy: Policy, eliminated: Sequence[bool] = ()
) -> tuple[SnapPoint, ...]:
    """Every certified cut position in the stream, in time order.

    Four families, all of them cheap and all of them things a viewer can feel:
    shot boundaries, motion onsets/offsets, audio onsets and brightness changes,
    and speech structure (phrase starts, phrase ends, and the gaps between).
    """
    grid = policy.grid(stream.rate)
    frames = stream.frames
    n = len(frames)
    base = stream.start_value
    found: list[SnapPoint] = []

    for shot in sorted(stream.shots, key=lambda s: (s.start, s.end, s.shot_id)):
        found.append(SnapPoint(base + shot.start, "shot_boundary",
                               policy.shot_boundary_strength, "in"))
        found.append(SnapPoint(base + shot.end, "shot_boundary",
                               policy.shot_boundary_strength, "out"))

    found.extend(_motion_snaps(frames, base, policy, grid))
    found.extend(_level_snaps(frames, base, policy, grid))
    found.extend(_speech_snaps(stream, policy, grid))

    words = stream.sorted_words
    starts = [w.start for w in words]
    keep: list[SnapPoint] = []
    for point in found:
        # `base + n` is the exclusive end of the stream and is a legal out-point
        # (TimeRange is half-open), so the upper bound is n, not n - 1.
        if point.time < base - _EPS or point.time > base + n + _EPS:
            continue
        if _covering_word(point.time, words, starts) is not None:
            # A snap point inside a word authorises a mid-word cut, because the
            # contract says the planner may only cut on snap points. Dropping it
            # is the whole no-mid-word guarantee; the phrase-boundary and gap
            # snaps below are what the planner uses instead.
            continue
        if eliminated:
            index = int(math.floor(point.time - base + _EPS))
            if 0 <= index < len(eliminated) and eliminated[index]:
                continue
        keep.append(point)

    # Dedupe on (time, kind, DIRECTION), first occurrence wins.
    #
    # The direction belongs in the key. Keying on (time, kind) alone collapsed
    # the two snaps that adjacent shots produce at their shared boundary -- the
    # "out" of one shot and the "in" of the next land on the same frame with the
    # same kind -- so the second shot lost the in-point at its own start and the
    # planner was left with no certified way into it. Mutation testing found
    # that; nothing in the output looked wrong, there was simply one fewer entry.
    # First occurrence wins; with the direction in the key the only remaining
    # duplicates are exactly-identical entries (two shot records covering the
    # same frame), so this is an idempotence guard rather than a choice.
    best: dict[tuple[float, str, str], SnapPoint] = {}
    for point in keep:
        best.setdefault((_quantise(point.time), point.kind, point.cut_direction), point)
    return tuple(
        sorted(best.values(), key=lambda p: (p.time, p.kind, -p.strength, p.cut_direction))
    )


def _local_max(series: Mapping[int, float], index: int, radius: int) -> bool:
    """True when `index` is the earliest maximum of `series` within ±radius.

    Strictly-greater on the left and greater-or-equal on the right: on a plateau
    that keeps the FIRST sample rather than emitting one snap per sample of a
    ramp. Emitting the whole ramp would give the planner two dozen
    indistinguishable "onsets" and make the strongest-onset choice meaningless.
    """
    value = series[index]
    for offset in range(-radius, radius + 1):
        if offset == 0:
            continue
        other = series.get(index + offset)
        if other is None:
            continue
        if offset < 0 and other >= value:
            return False
        if offset > 0 and other > value:
            return False
    return True


def _motion_snaps(
    frames: Sequence[Frame], base: float, policy: Policy, grid: Grid
) -> list[SnapPoint]:
    rises: dict[int, float] = {}
    falls: dict[int, float] = {}
    for index in range(1, len(frames)):
        previous, current = frames[index - 1].motion, frames[index].motion
        if previous is None or current is None:
            continue
        delta = current - previous
        if delta > 0:
            rises[index] = delta
        elif delta < 0:
            falls[index] = -delta
    out: list[SnapPoint] = []
    for index in sorted(rises):
        delta = rises[index]
        level = frames[index].motion
        if delta < policy.motion_onset_delta or level is None or level < policy.motion_onset_level:
            continue
        if not _local_max(rises, index, grid.onset_nms):
            continue
        # A motion onset is a great in-point and a poor out-point; the schema
        # calls out that asymmetry, and encoding it stops the planner making
        # technically-legal, visually-wrong cuts.
        out.append(SnapPoint(base + index, "motion_onset",
                             _clamp01(delta / policy.motion_onset_full), "in"))
    for index in sorted(falls):
        delta = falls[index]
        level = frames[index - 1].motion
        if delta < policy.motion_onset_delta or level is None or level < policy.motion_onset_level:
            continue
        if not _local_max(falls, index, grid.onset_nms):
            continue
        out.append(SnapPoint(base + index, "motion_offset",
                             _clamp01(delta / policy.motion_onset_full), "out"))
    return out


def _level_snaps(
    frames: Sequence[Frame], base: float, policy: Policy, grid: Grid
) -> list[SnapPoint]:
    """Audio onsets (LUFS rises) and scene brightness changes."""
    audio: dict[int, float] = {}
    bright: dict[int, float] = {}
    for index in range(1, len(frames)):
        previous, current = frames[index - 1].loudness_lufs, frames[index].loudness_lufs
        if previous is not None and current is not None and current - previous > 0:
            audio[index] = current - previous
        previous_luma, current_luma = frames[index - 1].luma, frames[index].luma
        if previous_luma is not None and current_luma is not None:
            bright[index] = abs(current_luma - previous_luma)
    out: list[SnapPoint] = []
    for index in sorted(audio):
        rise = audio[index]
        if rise < policy.audio_onset_db or not _local_max(audio, index, grid.onset_nms):
            continue
        out.append(SnapPoint(base + index, "audio_onset",
                             _clamp01(rise / policy.audio_onset_db_full), "both"))
    for index in sorted(bright):
        delta = bright[index]
        if delta < policy.brightness_delta or not _local_max(bright, index, grid.onset_nms):
            continue
        out.append(SnapPoint(base + index, "scene_brightness_change",
                             _clamp01(delta / policy.brightness_delta_full), "both"))
    return out


def _speech_snaps(stream: FeatureStream, policy: Policy, grid: Grid) -> list[SnapPoint]:
    """Phrase starts, phrase ends, and the gaps between them.

    A gap snaps to the whole frame nearest its midpoint, then is clamped back
    inside the gap. Snapping to the midpoint rather than to either word boundary
    leaves the maximum headroom on both sides, which is what keeps a cut off the
    breath at the start of the next word.
    """
    words = stream.sorted_words
    if not words:
        return []
    out: list[SnapPoint] = []
    full = max(policy.gap_strength_full_seconds * stream.rate, _EPS)

    out.append(SnapPoint(words[0].start, "speech_start", 0.8, "in"))
    for index in range(1, len(words)):
        gap = words[index].start - words[index - 1].end
        if gap < grid.speech_gap:
            continue
        strength = _clamp01(gap / full)
        out.append(SnapPoint(words[index - 1].end, "speech_end", strength, "out"))
        out.append(SnapPoint(words[index].start, "speech_start", strength, "in"))
        midpoint = (words[index - 1].end + words[index].start) / 2.0
        snapped = float(round(midpoint))
        lower = words[index - 1].end
        upper = words[index].start
        if snapped <= lower or snapped >= upper:
            snapped = midpoint
        out.append(SnapPoint(snapped, "speech_gap", strength, "both"))
    out.append(SnapPoint(words[-1].end, "speech_end", 0.8, "out"))
    return out


def _covering_word(
    time: float, words: Sequence[Word], starts: Sequence[float]
) -> Word | None:
    """The word STRICTLY containing `time`, or None.

    Strict: a time equal to a word's start or end is a boundary, not an
    interior, and boundaries are exactly the positions we want to cut on.
    """
    if not words:
        return None
    index = bisect.bisect_right(starts, time + _EPS) - 1
    # Walk back a few entries rather than trusting the single nearest start:
    # diarised output from overlapping speakers produces words that overlap in
    # time, so the word containing `time` is not always the last one that
    # started before it.
    for offset in range(0, 5):
        position = index - offset
        if position < 0:
            break
        word = words[position]
        if word.start + _EPS < time < word.end - _EPS:
            return word
    return None


# --------------------------------------------------------------------------
# Planning
# --------------------------------------------------------------------------


def _blake3_hex(payload: bytes) -> str:
    try:
        import blake3  # type: ignore
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise MomentIdUnavailable(
            "moment_id must be a BLAKE3 content address and blake3 is not "
            "installed. Pass id_hasher=... explicitly. Substituting another hash "
            "would produce ids that look like content addresses and are not, "
            "which breaks idempotency everywhere downstream and is undetectable."
        ) from exc
    return blake3.blake3(payload).hexdigest()


def moment_id(
    media_id: str,
    start: float,
    duration: float,
    rate: float,
    scorer: Scorer,
    hasher: Callable[[bytes], str] | None = None,
) -> str:
    """BLAKE3 over (media_id, source_range, scorer model_id+version).

    Fixed 6-decimal formatting of the times, for the same portability reason as
    the weights digest: a Python re-serialisation of floats does not match a
    JavaScript or Rust one, and an id that differs by language is not a content
    address.
    """
    payload = "|".join(
        [
            "moment-v1",
            media_id,
            f"{start:.6f}",
            f"{duration:.6f}",
            f"{rate:.6f}",
            scorer.model_id,
            scorer.version,
        ]
    ).encode("utf-8")
    digest = (hasher or _blake3_hex)(payload)
    if not _HEX64_RE.match(digest or ""):
        raise StreamError(
            f"id_hasher returned {digest!r}, which is not a 64-char lowercase hex "
            "digest; MomentRecord.moment_id is a Blake3Hash and a malformed id "
            "would be written straight into the database"
        )
    return digest


def plan_moments(
    stream: FeatureStream,
    *,
    policy: Policy | None = None,
    weights: Weights | None = None,
    scorer: Scorer = DEFAULT_SCORER,
    run_id: str | None = None,
    created_at: str | None = None,
    model_runs: Sequence[Mapping] = (),
    id_hasher: Callable[[bytes], str] | None = None,
) -> MomentPlan:
    """Score one stream and return every MomentRecord it produced.

    `created_at` is a parameter rather than a clock read: two runs of the same
    pipeline over the same footage must produce identical records, and a
    timestamp baked in here would make every record differ from the last.
    """
    policy = policy or Policy()
    weights = weights or Weights()
    stream.validate()
    policy.validate()
    weights.validate()
    if run_id is not None and not _SLUG_RE.match(run_id):
        raise StreamError(
            f"run_id={run_id!r} is not a contract Slug; Score.run_id would fail "
            "validation only after the record was already stored"
        )

    grid = policy.grid(stream.rate)
    n = len(stream.frames)
    if n == 0:
        return MomentPlan(
            media_id=stream.media_id,
            weights_id=weights.weights_id,
            weights_digest=weights.digest(),
        )

    reasons = [set(row) for row in eliminate_frames(stream, policy)]
    mask = [bool(row) for row in reasons]

    # A clean run too short to hold a legal moment is not a candidate region at
    # all; marking it here (rather than discarding it later) keeps it in the
    # eliminated output with a reason instead of vanishing.
    for start, end in _runs(mask, False):
        if end - start < grid.min_moment:
            for index in range(start, end):
                reasons[index].add("too_short")
                mask[index] = True

    segments = _segments(stream, mask)
    all_snaps = snap_points(stream, policy, mask)
    context = _Context(stream, policy, weights, grid, scorer, run_id, created_at,
                       tuple(model_runs), id_hasher, all_snaps)

    candidates = _candidates(stream, policy, grid, segments, weights)
    accepted = _suppress(candidates, grid)

    kept: list[ScoredMoment] = []
    dropped: list[ScoredMoment] = []
    for candidate in accepted:
        if candidate.fusion.value < policy.score_floor:
            dropped.append(
                _eliminated_record(
                    context, candidate.lo, candidate.hi, ("below_score_floor",),
                    "fusion", candidate.shot_id,
                )
            )
            continue
        kept.append(candidate)

    moments, planner_dropped = _finalise(context, kept, segments)
    dropped.extend(planner_dropped)

    for start, end in _runs(mask, True):
        merged = sorted(
            {reason for index in range(start, end) for reason in reasons[index]},
            key=lambda name: (ELIMINATION_ORDER.index(name)
                              if name in ELIMINATION_ORDER else len(ELIMINATION_ORDER), name),
        )
        dropped.append(
            _eliminated_record(context, start, end, tuple(merged), "classical", None)
        )

    dropped.sort(key=lambda m: (m.start, m.duration, m.record["moment_id"]))
    return MomentPlan(
        media_id=stream.media_id,
        moments=tuple(moments),
        eliminated=tuple(dropped),
        sample_count=n,
        eliminated_samples=sum(1 for flag in mask if flag),
        candidates_considered=len(candidates),
        weights_id=weights.weights_id,
        weights_digest=weights.digest(),
    )


@dataclass(frozen=True)
class _Context:
    """Everything the record builders need, gathered once."""

    stream: FeatureStream
    policy: Policy
    weights: Weights
    grid: Grid
    scorer: Scorer
    run_id: str | None
    created_at: str | None
    model_runs: tuple[Mapping, ...]
    id_hasher: Callable[[bytes], str] | None
    snaps: tuple[SnapPoint, ...]


@dataclass(frozen=True)
class _Candidate:
    lo: int
    hi: int
    shot_id: str | None
    fusion: FusedWindow

    @property
    def centre(self) -> float:
        return (self.lo + self.hi) / 2.0


def _candidates(
    stream: FeatureStream,
    policy: Policy,
    grid: Grid,
    segments: Sequence[tuple[int, int, str | None]],
    weights: Weights,
) -> list[_Candidate]:
    """Every scoreable window inside a clean segment, in time order.

    A segment shorter than the window still yields ONE candidate covering the
    whole segment: a moment that is genuinely the whole clip is a normal
    outcome, and a fixed window length that refused to shrink would silently
    discard every clip shorter than two seconds.
    """
    out: list[_Candidate] = []
    for start, end, shot_id in segments:
        span = end - start
        if span < grid.min_moment:
            continue
        width = min(grid.window, span)
        starts = sorted({*range(start, end - width + 1, grid.hop), end - width})
        for lo in starts:
            values = aggregate(stream, lo, lo + width, policy)
            fused = fuse_window(values, weights)
            if fused.coverage <= 0.0:
                # Nothing measured in this window. Not a rejection — we simply
                # have not looked at it yet — but it cannot be ranked, and
                # ranking a 0.0 would put unanalysed footage last for a reason
                # that has nothing to do with the footage.
                continue
            out.append(_Candidate(lo, lo + width, shot_id, fused))
    return out


def _suppress(candidates: Sequence[_Candidate], grid: Grid) -> list[_Candidate]:
    """Greedy non-maximum suppression: the local maxima of the score series.

    Radius is at least one window wide. With a 2s window every 0.25s, a smaller
    radius would accept two "different" moments sharing seven eighths of their
    frames — and two peaks 0.5s apart really are one moment, so the second must
    lose rather than become a near-duplicate clip in the reel.

    Ties break on start index, so the earlier of two equally good windows wins
    and the output does not depend on candidate construction order.
    """
    radius = max(grid.separation, grid.window)
    ordered = sorted(candidates, key=lambda c: (-c.fusion.value, c.lo, c.hi))
    accepted: list[_Candidate] = []
    for candidate in ordered:
        if any(abs(candidate.centre - other.centre) < radius for other in accepted):
            continue
        accepted.append(candidate)
    accepted.sort(key=lambda c: (c.lo, c.hi))
    return accepted


def _pick_snap(
    snaps: Sequence[SnapPoint],
    lo: float,
    hi: float,
    directions: frozenset[str],
    prefer: float,
    policy: Policy,
    rate: float,
) -> SnapPoint | None:
    """The best snap point in [lo, hi], trading strength against displacement.

    "Cutting on a weak onset is worse than cutting 40ms later on a strong one"
    (schema, SnapPoint.strength), so strength leads and distance costs
    `snap_distance_cost` per second. Ties break on distance, then time, then
    kind — never on list order.
    """
    best: tuple[tuple, SnapPoint] | None = None
    for point in snaps:
        if point.time < lo - _EPS or point.time > hi + _EPS:
            continue
        if point.cut_direction not in directions:
            continue
        distance = abs(point.time - prefer) / rate
        key = (
            -_quantise(point.strength - policy.snap_distance_cost * distance),
            _quantise(distance),
            point.time,
            point.kind,
        )
        if best is None or key < best[0]:
            best = (key, point)
    return best[1] if best else None


def _word_safe_in(time: float, words: Sequence[Word], starts: Sequence[float]) -> float:
    """Push an in-point out of any word it lands inside, to the next whole frame.

    ceil, never round: rounding could land back inside the word by up to half a
    frame, which is the exact failure this function exists to prevent.
    """
    word = _covering_word(time, words, starts)
    if word is None:
        return time
    return float(math.ceil(word.end - _EPS))


def _word_safe_out(time: float, words: Sequence[Word], starts: Sequence[float]) -> float:
    word = _covering_word(time, words, starts)
    if word is None:
        return time
    return float(math.floor(word.start + _EPS))


def _finalise(
    context: _Context,
    kept: Sequence[_Candidate],
    segments: Sequence[tuple[int, int, str | None]],
) -> tuple[list[ScoredMoment], list[ScoredMoment]]:
    """Snap each accepted window to real boundaries and emit records."""
    stream = context.stream
    policy = context.policy
    grid = context.grid
    rate = stream.rate
    base = stream.start_value
    words = stream.sorted_words
    starts = [w.start for w in words]

    bounds: dict[tuple[int, int], tuple[int, int]] = {}
    for start, end, _ in segments:
        for candidate in kept:
            if start <= candidate.lo and candidate.hi <= end:
                bounds[(candidate.lo, candidate.hi)] = (start, end)

    staged: list[tuple[_Candidate, float, float, SnapPoint | None, SnapPoint | None]] = []
    for candidate in sorted(kept, key=lambda c: (c.lo, c.hi)):
        segment = bounds.get((candidate.lo, candidate.hi))
        if segment is None:  # pragma: no cover - candidates come from segments
            continue
        seg_lo, seg_hi = segment
        lo_val, hi_val = base + seg_lo, base + seg_hi
        want_in, want_out = base + candidate.lo, base + candidate.hi

        in_snap = _pick_snap(
            context.snaps,
            max(lo_val, want_in - grid.extend),
            min(hi_val - grid.min_moment, want_in + grid.extend),
            frozenset({"in", "both"}),
            want_in,
            policy,
            rate,
        )
        in_point = in_snap.time if in_snap else want_in
        in_point = max(lo_val, _word_safe_in(in_point, words, starts))

        out_snap = _pick_snap(
            context.snaps,
            max(in_point + grid.min_moment, want_out - grid.extend),
            min(hi_val, min(want_out + grid.extend, in_point + grid.max_moment)),
            frozenset({"out", "both"}),
            want_out,
            policy,
            rate,
        )
        out_point = out_snap.time if out_snap else want_out
        out_point = min(hi_val, _word_safe_out(out_point, words, starts))

        if out_point - in_point < grid.min_moment:
            out_point = min(hi_val, in_point + grid.min_moment)
        if out_point - in_point > grid.max_moment:
            out_point = in_point + grid.max_moment
        staged.append((candidate, in_point, out_point, in_snap, out_snap))

    moments: list[ScoredMoment] = []
    dropped: list[ScoredMoment] = []
    previous_out: float | None = None
    for candidate, in_point, out_point, in_snap, out_snap in staged:
        if previous_out is not None and in_point < previous_out - _EPS:
            # Overlap after extension. Trim forward rather than dropping: the
            # footage is still good, it just cannot be claimed twice.
            in_point = _word_safe_in(previous_out, words, starts)
            in_snap = None
        if out_point - in_point < grid.min_moment - _EPS:
            dropped.append(
                _eliminated_record(
                    context, candidate.lo, candidate.hi, ("too_short",), "planner",
                    candidate.shot_id,
                )
            )
            continue
        moments.append(
            _kept_record(context, candidate, in_point, out_point, in_snap, out_snap)
        )
        previous_out = out_point
    return moments, dropped


def _sample_span(context: _Context, in_point: float, out_point: float) -> tuple[int, int]:
    """Samples whose times fall in [in_point, out_point).

    Half-open, matching TimeRange, so adjacent moments tile the source with no
    sample counted twice.
    """
    base = context.stream.start_value
    n = len(context.stream.frames)
    lo = max(0, int(math.ceil(in_point - base - _EPS)))
    hi = min(n, int(math.ceil(out_point - base - _EPS)))
    if hi <= lo:
        hi = min(n, lo + 1)
    return lo, hi


def _kept_record(
    context: _Context,
    candidate: _Candidate,
    in_point: float,
    out_point: float,
    in_snap: SnapPoint | None,
    out_snap: SnapPoint | None,
) -> ScoredMoment:
    stream = context.stream
    rate = stream.rate
    lo, hi = _sample_span(context, in_point, out_point)
    values = aggregate(stream, lo, hi, context.policy)
    fused = fuse_window(values, context.weights)

    lead_hi = min(hi, lo + context.grid.hook)
    lead = aggregate(stream, lo, lead_hi, context.policy)
    # Filtered, not ordered: `_features` is the single place that decides the
    # order events appear in a record, so that ordering has one home and one
    # test rather than two half-guarantees.
    events = [
        event for event in stream.audio_events
        if in_point - _EPS <= event.time < out_point + _EPS
    ]
    peak_event = max(
        (event.confidence for event in events if event.label in PEAK_EVENT_LABELS),
        default=None,
    )

    scores = {
        "moment_score": _score(fused.value, context.run_id),
        "technical": _score(_technical(values), context.run_id),
        "hook_potential": _score(_hook(lead, values), context.run_id),
        "emotional_peak": _score(_emotional_peak(values, peak_event), context.run_id),
        # narrative_value stays null: the schema says it is only ever populated
        # by a Tier 2/3 reasoning pass, and a local guess in that field would be
        # indistinguishable from a frontier-model judgement.
        "narrative_value": None,
        "source": "local_fusion",
        # The id alone cannot identify an EDITED profile, so the digest travels
        # with it. Two users legitimately disagree about the same footage, and
        # "why is this 0.72" has to stay answerable for each.
        "fusion_weights_version": f"{context.weights.weights_id}@{context.weights.digest()}",
    }

    record = _base_record(context, in_point, out_point, candidate.shot_id)
    record["features"] = _features(context, values, lo, hi, events)
    record["scores"] = scores
    record["snap_points"] = [
        point.as_dict(rate)
        for point in context.snaps
        if in_point - _EPS <= point.time <= out_point + _EPS
    ]
    record["safe_trim"] = _safe_trim(context, in_point, out_point)
    record["elimination"] = {"eliminated": False, "reasons": [], "stage": None}
    transcript = _transcript(context, in_point, out_point)
    if transcript is not None:
        record["transcript"] = transcript
    return ScoredMoment(
        record=record,
        fusion=fused,
        peak_value=candidate.fusion.value,
        in_snap_kind=in_snap.kind if in_snap else None,
        out_snap_kind=out_snap.kind if out_snap else None,
    )


def _eliminated_record(
    context: _Context,
    lo: int,
    hi: int,
    reasons: tuple[str, ...],
    stage: str,
    shot_id: str | None,
) -> ScoredMoment:
    """A record that exists to say "rejected, and here is why".

    Features are still emitted — they were computed classically and cost
    nothing — because the culling UI has to be able to say "shake 0.81" rather
    than "trust me". `safe_trim` and `snap_points` are deliberately empty: there
    is nothing here to trim to, and offering a planner a cut position inside
    eliminated footage invites it to use one.
    """
    stream = context.stream
    base = stream.start_value
    in_point, out_point = base + lo, base + hi
    values = aggregate(stream, lo, hi, context.policy)
    record = _base_record(context, in_point, out_point, shot_id)
    record["features"] = _features(context, values, lo, hi, [])
    record["scores"] = {
        # 0.0 is not a measurement here. `elimination.eliminated` is the field to
        # branch on; the value exists so a caller that ignores it sorts this
        # last rather than raising.
        "moment_score": {"value": 0.0, "run_id": context.run_id, "raw_value": None},
        "technical": None,
        "hook_potential": None,
        "emotional_peak": None,
        "narrative_value": None,
        "source": "local_fusion",
        "fusion_weights_version": (
            f"{context.weights.weights_id}@{context.weights.digest()}"
        ),
    }
    record["snap_points"] = []
    record["safe_trim"] = None
    record["elimination"] = {
        "eliminated": True,
        "reasons": list(reasons),
        "stage": stage,
    }
    return ScoredMoment(
        record=record,
        fusion=FusedWindow(
            value=0.0,
            coverage=0.0,
            weights_id=context.weights.weights_id,
            weights_digest=context.weights.digest(),
        ),
        peak_value=0.0,
    )


def _base_record(
    context: _Context, in_point: float, out_point: float, shot_id: str | None
) -> dict:
    stream = context.stream
    rate = stream.rate
    duration = out_point - in_point
    record = {
        "schema_version": SCHEMA_VERSION,
        "moment_id": moment_id(
            stream.media_id, in_point, duration, rate, context.scorer, context.id_hasher
        ),
        "media_id": stream.media_id,
        "source_range": {
            "start_time": _rt(in_point, rate),
            "duration": _rt(duration, rate),
        },
        "proxy_range": _proxy_range(stream, in_point, duration),
        "shot_id": shot_id,
    }
    if context.model_runs:
        record["model_runs"] = [dict(run) for run in context.model_runs]
    if context.created_at is not None:
        record["created_at"] = context.created_at
    return record


def _proxy_range(stream: FeatureStream, in_point: float, duration: float) -> dict | None:
    if stream.proxy_rate is None or stream.proxy_start_value is None:
        return None
    factor = stream.proxy_rate / stream.rate
    offset = (in_point - stream.start_value) * factor
    start = stream.proxy_start_value + round(offset)
    length = max(1.0, float(round(duration * factor)))
    return {
        "start_time": _rt(start, stream.proxy_rate),
        "duration": _rt(length, stream.proxy_rate),
    }


def _score(value: float | None, run_id: str | None) -> dict | None:
    if value is None:
        return None
    return {"value": _quantise(_clamp01(value)), "run_id": run_id}


def _features(
    context: _Context,
    values: Mapping[str, float],
    lo: int,
    hi: int,
    events: Sequence[AudioEvent],
) -> dict | None:
    """MomentFeatures for the emitted range. Absent keys mean NOT MEASURED.

    Nothing is back-filled: a zero written where a measurement is missing is
    indistinguishable from a real zero the moment it is stored, and every audit
    downstream inherits the lie.
    """
    stream = context.stream
    rate = stream.rate
    out: dict = {}
    for name in ("motion_energy", "motion_peak", "shake", "exposure_stability",
                 "sharpness", "face_presence", "max_face_area_ratio",
                 "smile_intensity", "novelty"):
        if name in values:
            out[name] = _quantise(_clamp01(values[name]))

    audio: dict = {}
    if "loudness_lufs" in values:
        audio["loudness_lufs"] = _quantise(min(0.0, max(-70.0, values["loudness_lufs"])))
    if "speech_presence" in values:
        audio["speech_ratio"] = _quantise(_clamp01(values["speech_presence"]))
    if "noise_ratio" in values:
        audio["noise_ratio"] = _quantise(_clamp01(values["noise_ratio"]))
    if events:
        audio["events"] = [
            {
                "label": event.label,
                "confidence": _quantise(_clamp01(event.confidence)),
                "time": _rt(event.time, rate),
            }
            for event in sorted(events, key=lambda e: (e.time, e.label, -e.confidence))
        ]
    if audio:
        out["audio"] = audio

    representative = _representative_frame(stream, lo, hi)
    if representative is not None:
        out["representative_frame_time"] = _rt(representative, rate)
    return out or None


def _representative_frame(stream: FeatureStream, lo: int, hi: int) -> float | None:
    """Best single frame in the window: the contact-sheet tile.

    Scored on the frame's own measured signals only, renormalised, so a frame
    the face model has not reached is not beaten by a worse one that it has.
    Ties break to the EARLIEST frame — a stable choice, and the earlier frame is
    the one closer to the moment's in-point.
    """
    weight_map = {"sharpness": 0.4, "face_presence": 0.3, "smile_intensity": 0.2,
                  "stability": 0.1}
    best: tuple[float, int] | None = None
    for index in range(lo, hi):
        frame = stream.frames[index]
        values: dict[str, float] = {}
        if frame.sharpness is not None:
            values["sharpness"] = frame.sharpness
        if frame.face_presence is not None:
            values["face_presence"] = frame.face_presence
        if frame.smile_intensity is not None:
            values["smile_intensity"] = frame.smile_intensity
        if frame.shake is not None:
            values["stability"] = 1.0 - frame.shake
        value, coverage, _ = _renormalised(values, weight_map)
        if coverage <= 0.0:
            continue
        # STRICTLY greater: the scan runs forward, so an equal-scoring later
        # frame must not displace the one already held. Ties therefore go to the
        # earliest frame, which is the one nearest the moment's in-point.
        if best is None or value > best[0]:
            best = (value, index)
    if best is None:
        return None
    return stream.start_value + best[1]


def _safe_trim(context: _Context, in_point: float, out_point: float) -> dict:
    """Bounds a planner may trim to, and the speech-exact versions of them."""
    stream = context.stream
    rate = stream.rate
    words = stream.sorted_words
    starts = [w.start for w in words]
    inside = [w for w in words if w.start < out_point and w.end > in_point]

    trim = {
        "earliest_in": _rt(in_point, rate),
        "latest_out": _rt(out_point, rate),
        "speech_safe_in": None,
        "speech_safe_out": None,
        "min_duration": _rt(context.grid.min_moment, rate),
        "preserve_audio_tail": _audio_tail(context, out_point),
    }
    if inside:
        # Null only when the moment contains no speech (schema): with speech
        # present these are the exact bounds derived from word timestamps, not
        # from voice-activity guesses.
        trim["speech_safe_in"] = _rt(_word_safe_in(in_point, words, starts), rate)
        trim["speech_safe_out"] = _rt(_word_safe_out(out_point, words, starts), rate)
    return trim


def _audio_tail(context: _Context, out_point: float) -> bool:
    """True when the audio meaningfully continues past the visual out-point.

    A laugh that lands after the cut, or a word that starts just after it. The
    renderer honours this with an audio-only extension (an L-cut) — a decision
    that has to live in the plan, because renderers are dumb by contract.
    """
    window = context.grid.audio_tail
    for word in context.stream.sorted_words:
        if out_point - _EPS <= word.start < out_point + window:
            return True
    for event in context.stream.audio_events:
        if (
            event.label in PEAK_EVENT_LABELS
            and event.confidence >= context.policy.audio_tail_event_confidence
            and out_point - _EPS <= event.time < out_point + window
        ):
            return True
    return False


def _transcript(context: _Context, in_point: float, out_point: float) -> dict | None:
    """Speech inside the moment, with word timing.

    Emitted only when the caller declared a language: TranscriptSegment requires
    BCP-47 and Indian-language libraries are a first-class target, so defaulting
    to English would mislabel a Hindi moment as English in the database forever.
    """
    stream = context.stream
    if stream.language is None:
        return None
    words = [
        word for word in stream.sorted_words
        if word.start < out_point - _EPS and word.end > in_point + _EPS
    ]
    if not words:
        return None
    rate = stream.rate
    return {
        "text": " ".join(word.word for word in words),
        "language": stream.language,
        "words": [
            {
                "word": word.word,
                "start": _rt(word.start, rate),
                "end": _rt(word.end, rate),
                **({"confidence": _quantise(_clamp01(word.confidence))}
                   if word.confidence is not None else {}),
            }
            for word in words
        ],
    }


def explain(moment: ScoredMoment, *, limit: int = 3) -> str:
    """One human-readable line, for the culling UI and for debugging.

    "Why did this get cut" has to be answerable for eliminated footage too —
    that is the entire promise of showing a user their 40 usable minutes.
    """
    elimination = moment.record["elimination"]
    if elimination["eliminated"]:
        reasons = ", ".join(elimination["reasons"]) or "unspecified"
        return f"eliminated ({elimination['stage']}): {reasons}"
    if not moment.fusion.contributions:
        return "not yet measured"
    ranked = sorted(
        moment.fusion.contributions, key=lambda row: (-(row[1] * row[2]), row[0])
    )[:limit]
    parts = ", ".join(f"{name} {value:.2f}" for name, value, _ in ranked)
    suffix = "" if moment.fusion.coverage >= 1.0 else (
        f", at {moment.fusion.coverage:.0%} coverage"
    )
    return f"{moment.fusion.value:.2f} from {parts}{suffix}"
