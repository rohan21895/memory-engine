"""Moment scoring v1 -- build plan 4.3, Phase 3.

A sliding window over a fused per-frame feature stream. Windows that survive
elimination are scored by a hand-weighted linear fusion; local maxima of that
score curve are grown into moments; the moment's boundaries are then snapped
onto certified cut positions and written out as contract MomentRecords.

The three things this file is actually about, in the order they matter:

ELIMINATION FIRST, ALWAYS
    Shake, blown and crushed exposure, black frames, a lens cap, pocket
    footage and tripod dead time discard 90-95% of a real GoPro card before
    any expensive analysis. That is the single biggest cost and quality lever
    in the system, and it is not scoring: an eliminated region does not get a
    low score, it does not enter the pool at all. The distinction is
    load-bearing for the same reason it is in the ranking engine's fusion.py:
    a low score still competes, and still wins whenever the pool around it is
    bad enough -- which is exactly the situation at the tail of an action-cam
    card, where the alternative to a pocket shot is another pocket shot.

    Elimination is enforced at FRAME granularity, not window granularity. A
    window is 2 seconds and hops 0.5 seconds, so windows overlap by 75%; if
    "eliminated" only meant "this window is out", the surviving neighbour
    window would still drag three quarters of the junk into its moment. So the
    cheap per-frame verdicts (black, lens-obstructed, blown, crushed) define
    frame regions, and no moment's source range may contain an eliminated
    frame. Window-level verdicts (mean shake, zero motion, wind) remove the
    window from peak candidacy and from growth.

SNAP POINTS ARE THE PRODUCT
    A cut through a word is instantly perceptible in a way a slightly-wrong
    score is not. So boundaries are not chosen by the planner and are not
    computed from the score: they are selected from positions this file has
    certified as cuttable -- motion onsets, audio onsets, speech gaps, word
    edges, shot boundaries, subject entries. Any candidate that falls strictly
    inside a spoken word is discarded before selection, and when no candidate
    exists inside the tolerance the raw boundary is still walked out of any
    word it landed in. "Never cut mid-word" is therefore a property of the
    data the planner is given, not a hope about the planner's code.

FUSION MATCHES THE RANKING ENGINE
    Same shape as packages/ranking-engine/memory_engine_ranking/fusion.py, on
    purpose, because two fusions that renormalise differently is how two parts
    of one product end up with two different definitions of "0.8":

      * Missing signals RENORMALISE. They are not defaulted to 0 (which
        punishes footage the expensive model has not reached yet) and not
        defaulted to 0.5 (which fabricates a measurement indistinguishable
        from a real mediocre one).
      * Coverage is REPORTED, not folded into the value, so an under-measured
        window does not masquerade as a bad one.
      * NOT APPLICABLE is not NOT MEASURED. Face prominence is absent both for
        a drone shot with no faces in it and for footage the face model has
        not reached. The first must not count against coverage; the second
        must. FaceState and AudioState carry that distinction because the
        frame data cannot.
      * Provenance is recorded: which weight profile, which digest, which
        feature set. A score you cannot attribute to a specific set of weights
        is not reproducible, and per-user reweighting is the entire point of
        the PrefEvent flywheel.

DETERMINISM
    Same stream + same params = byte-identical records. Everything is indexed
    in integer frames rather than float seconds; every iteration is over a
    sorted sequence; every tie is broken explicitly (higher strength, then
    nearer, then earlier); every emitted float is quantised to six decimals;
    integral RationalTime values are emitted as ints so the JSON bytes do not
    depend on whether a value happened to arrive as 12 or 12.0. `created_at`
    is a parameter rather than a clock read, because a record whose bytes
    change on every run is not reproducible even when its id is stable.

WHAT THIS FILE DELIBERATELY DOES NOT DO
    * It does not emit `proxy_range`. A proxy_range derived by scaling the
      source index by a rate ratio would fabricate the mapping that the proxy
      frame index actually owns, and a wrong proxy_range silently re-scores
      the wrong footage. The ingest worker owns that mapping; when it supplies
      one, this becomes a passthrough.
    * It does not invent `shot_id`. Shot ids are authored by the boundary
      detector. Synthesising "shot-0007" here would produce ids that disagree
      with the detector's the moment either side reorders.
    * It does not populate `novelty` or `visual_embedding`. Both are defined
      relative to what has already been SELECTED, which is the reel planner's
      state, not the scorer's.
    * It does not set `contains_name_mention`. Detecting a name needs an NER
      pass this package does not run, and defaulting it to false is the
      contract's own default rather than a claim made here.
"""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable, Mapping, Sequence

# ---------------------------------------------------------------------------
# Identity of the scorer
# ---------------------------------------------------------------------------

# Bump when a signal is added, removed or redefined. Matches
# PrefEvent.FeatureContext.feature_set_id: a stored feature map is meaningless
# without knowing which feature list it was written against.
FEATURE_SET_ID = "moment-v1"

# Identifies the scoring ALGORITHM, separately from the weights. The schema
# derives moment_id from "(media_id, source_range, scorer model_id+version)";
# for a fusion the analogue of a weights hash is the weights digest, so the
# scorer identity below carries both. Changing the peak picker without changing
# any weight still has to produce new ids, because the same source range now
# means a different thing.
SCORER_ID = "moment-scoring-v1"

_BLAKE3_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_SLUG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


class StreamError(ValueError):
    """Input that would corrupt the analysis rather than merely weaken it."""


class ParamError(ValueError):
    """A parameter set that cannot produce a coherent moment."""


class WeightError(ValueError):
    """A weighting profile that cannot produce an honest coverage figure."""


# ---------------------------------------------------------------------------
# What ran, as distinct from what was found
# ---------------------------------------------------------------------------

class FaceState(str, Enum):
    """Why face features are absent, which the values alone cannot say.

    Copied in spirit from ranking-engine fusion.py, where conflating these two
    absences made a mid-scan record report 100% coverage: defaulting UNKNOWN to
    "no faces" removes the face weight from the denominator, so footage that is
    merely un-analysed looks fully measured and ranks as comparable against
    footage that really is.
    """

    NOT_RUN = "not_run"      # detection has not happened; coverage is incomplete
    NO_FACES = "no_faces"    # detection ran over the stream and found none
    HAS_FACES = "has_faces"  # detection ran and found faces somewhere


class AudioState(str, Enum):
    """Whether audio features are absent because nothing ran, or because there
    is no audio to analyse.

    A muted drone clip and a clip awaiting CLAP are both "no audio numbers",
    and they must account for coverage in opposite directions. NO_AUDIO makes
    every audio weight not-applicable (a silent file is not an under-measured
    file); NOT_RUN keeps them in the denominator.
    """

    NOT_RUN = "not_run"
    NO_AUDIO = "no_audio"
    MEASURED = "measured"


# ---------------------------------------------------------------------------
# Input: the fused per-frame feature stream
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FrameSample:
    """One sample of the fused per-frame feature stream.

    `index` is a SOURCE frame index. The schema is explicit that everything
    downstream addresses source time and that proxy time never escapes the
    analysis layer, so the mapping back through the proxy frame index happens
    before this file sees the stream -- not inside it.

    Every measurement is optional and `None` means NOT MEASURED. It never means
    zero: a zero motion reading eliminates a window as tripod dead time, and an
    unmeasured one must not.
    """

    index: int
    motion: float | None = None
    shake: float | None = None
    sharpness: float | None = None
    luma: float | None = None
    face_count: int | None = None
    face_area: float | None = None
    smile: float | None = None
    loudness_lufs: float | None = None
    speech: bool | None = None
    noise: float | None = None
    lens_obstructed: bool = False


@dataclass(frozen=True)
class SpeechWord:
    """One word with its timing, in SOURCE FRAME units at the stream rate.

    Fractional positions are allowed and expected -- a word does not begin on a
    frame boundary. They are the reason `RationalTime.value` is a number rather
    than an integer, and the reason every rounding in this file rounds AWAY
    from speech: `math.floor` before a word, `math.ceil` after it. Rounding
    can then only ever make a cut safer, never move it 16ms inside a word.
    """

    word: str
    start: float
    end: float
    confidence: float | None = None


@dataclass(frozen=True)
class AudioEvent:
    """A detected audio event at a frame index.

    Laughter and cheering are among the strongest emotional-peak signals a
    local model can produce, which is why they are weighted separately from
    loudness rather than folded into it.
    """

    label: str
    confidence: float
    index: int


@dataclass(frozen=True)
class Shot:
    """A shot span from boundary detection, half-open [start, end).

    Carries the detector's own id. A moment never crosses a shot boundary --
    crossing one is a cut, and cuts belong to the planner.
    """

    shot_id: str
    start: int
    end: int


# Every audio label the contract allows, with how much emotional weight it
# carries. Kept as data rather than an if-chain so adding a label is a one-line
# change that cannot forget one of the three places labels are used.
EMOTIONAL_EVENT_WEIGHT: Mapping[str, float] = {
    "laughter": 1.00,
    "cheering": 1.00,
    "applause": 0.80,
    "singing": 0.70,
    "crying": 0.60,
    "fireworks": 0.60,
    "shouting": 0.50,
    "splash": 0.40,
    "animal": 0.30,
    "music": 0.20,
    "speech": 0.10,
    "engine": 0.10,
    "other": 0.00,
    "wind": 0.00,
    "silence": 0.00,
}

# Events that mark a physical impact worth cutting ON. A splash or a firework
# is a genuine in-point; laughter is an emotional peak but a poor cut position,
# because the cut wants to land before the laugh, not on it.
IMPACT_LABELS = ("fireworks", "splash")


@dataclass(frozen=True)
class FeatureStream:
    """Everything the scorer is given about one piece of media.

    `rate` is the SOURCE frame rate the indices are expressed in. It is carried
    on the stream rather than on each frame because a stream with two rates in
    it is not a stream.
    """

    media_id: str
    rate: float
    frames: tuple[FrameSample, ...]
    face_state: FaceState = FaceState.NOT_RUN
    audio_state: AudioState = AudioState.NOT_RUN
    shots: tuple[Shot, ...] = ()
    words: tuple[SpeechWord, ...] = ()
    language: str | None = None
    audio_events: tuple[AudioEvent, ...] = ()

    @property
    def origin(self) -> int:
        return self.frames[0].index

    @property
    def end(self) -> int:
        """Exclusive end index. Ranges in this file are half-open, matching
        TimeRange and OTIO, so adjacent moments tile with no off-by-one frame."""
        return self.frames[0].index + len(self.frames)

    def at(self, index: int) -> FrameSample:
        return self.frames[index - self.origin]

    def span(self, lo: int, hi: int) -> tuple[FrameSample, ...]:
        return self.frames[lo - self.origin:hi - self.origin]

    def validate(self) -> None:
        """Reject a stream that would produce plausible wrong numbers.

        Every check here corresponds to a failure that does not raise on its
        own: a gap in the indices silently changes what a "2 second window"
        spans, a NaN sails through every threshold comparison because NaN
        compares False to everything, and a face_state that disagrees with the
        frame data mis-states coverage in whichever direction the caller
        happened to guess.
        """
        if not _BLAKE3_PATTERN.match(self.media_id or ""):
            raise StreamError(
                f"media_id {self.media_id!r} is not a 64-hex BLAKE3 digest; the "
                "record this produces would fail contract validation at the far "
                "end of the pipeline instead of here"
            )
        if not isinstance(self.rate, (int, float)) or isinstance(self.rate, bool):
            raise StreamError(f"rate {self.rate!r} is not a number")
        if not math.isfinite(self.rate) or self.rate <= 0.0:
            raise StreamError(f"rate {self.rate!r} must be finite and positive")
        if not self.frames:
            raise StreamError("stream has no frames")

        origin = self.frames[0].index
        if not isinstance(origin, int) or isinstance(origin, bool) or origin < 0:
            raise StreamError(f"first frame index {origin!r} must be a non-negative int")

        faces_seen = False
        for position, frame in enumerate(self.frames):
            expected = origin + position
            if frame.index != expected:
                raise StreamError(
                    f"frame {position} has index {frame.index}, expected {expected}. "
                    "The stream must be contiguous and ascending: a gap makes every "
                    "window cover more time than it claims, so every duration in the "
                    "output is wrong without anything raising."
                )
            for name in ("motion", "shake", "sharpness", "luma", "face_area", "smile", "noise"):
                value = getattr(frame, name)
                if value is None:
                    continue
                _require_unit(f"frame {frame.index} {name}", value)
            if frame.loudness_lufs is not None:
                value = frame.loudness_lufs
                if not isinstance(value, (int, float)) or isinstance(value, bool):
                    raise StreamError(f"frame {frame.index} loudness_lufs is not a number")
                if not math.isfinite(value) or not -70.0 <= value <= 0.0:
                    raise StreamError(
                        f"frame {frame.index} loudness_lufs={value} is outside the "
                        "contract range [-70, 0]"
                    )
            if frame.face_count is not None:
                if not isinstance(frame.face_count, int) or isinstance(frame.face_count, bool):
                    raise StreamError(f"frame {frame.index} face_count is not an int")
                if frame.face_count < 0:
                    raise StreamError(f"frame {frame.index} face_count is negative")
                if frame.face_count > 0:
                    faces_seen = True
            if frame.speech is not None and not isinstance(frame.speech, bool):
                raise StreamError(f"frame {frame.index} speech is not a bool")

        self._validate_face_state(faces_seen)
        self._validate_audio_state()
        self._validate_shots()
        self._validate_words()
        self._validate_events()

    def _validate_face_state(self, faces_seen: bool) -> None:
        counts_present = any(f.face_count is not None for f in self.frames)
        if self.face_state is FaceState.NOT_RUN and counts_present:
            raise StreamError(
                "face_state=not_run but frames carry face_count. One of the two is "
                "lying, and the version that survives determines whether face weight "
                "sits in the coverage denominator."
            )
        if self.face_state is FaceState.NO_FACES:
            if not counts_present:
                raise StreamError("face_state=no_faces but no frame carries a face_count")
            if faces_seen:
                raise StreamError("face_state=no_faces but a frame has face_count > 0")
        if self.face_state is FaceState.HAS_FACES and not faces_seen:
            raise StreamError(
                "face_state=has_faces but no frame has face_count > 0; use no_faces, "
                "which makes face prominence not-applicable instead of unmeasured"
            )
        for frame in self.frames:
            if frame.face_area is not None and frame.face_count == 0:
                raise StreamError(
                    f"frame {frame.index} has face_area with face_count=0: a face area "
                    "for no face is a measurement of nothing"
                )

    def _validate_audio_state(self) -> None:
        audio_present = any(
            f.loudness_lufs is not None or f.speech is not None or f.noise is not None
            for f in self.frames
        )
        if self.audio_state is AudioState.NOT_RUN and (audio_present or self.audio_events):
            raise StreamError(
                "audio_state=not_run but audio measurements are present; the state is "
                "what decides whether audio weight counts against coverage"
            )
        if self.audio_state is AudioState.NO_AUDIO and (audio_present or self.audio_events):
            raise StreamError(
                "audio_state=no_audio but audio measurements are present"
            )
        if self.audio_state is AudioState.MEASURED and not audio_present:
            raise StreamError(
                "audio_state=measured but no frame carries a loudness, speech or noise "
                "reading; use not_run so the missing weight is reported as uncovered"
            )

    def _validate_shots(self) -> None:
        if not self.shots:
            return
        previous_end = self.origin
        for shot in self.shots:
            if not _SLUG_PATTERN.match(shot.shot_id or ""):
                raise StreamError(f"shot_id {shot.shot_id!r} is not a contract Slug")
            if shot.end <= shot.start:
                raise StreamError(f"shot {shot.shot_id} is empty or inverted")
            if shot.start != previous_end:
                raise StreamError(
                    f"shot {shot.shot_id} starts at {shot.start}, expected {previous_end}. "
                    "Shots must tile the stream exactly: a gap or an overlap means a "
                    "moment can be placed in no shot or in two, and 'a moment never "
                    "crosses a cut' stops being enforceable."
                )
            previous_end = shot.end
        if previous_end != self.end:
            raise StreamError(
                f"shots end at {previous_end}, stream ends at {self.end}"
            )

    def _validate_words(self) -> None:
        if not self.words:
            return
        if not self.language:
            raise StreamError(
                "words are present but language is unset; TranscriptSegment requires "
                "language, and Indian-language libraries are a first-class target so it "
                "cannot be assumed English"
            )
        previous_start = float("-inf")
        for word in self.words:
            for name in ("start", "end"):
                value = getattr(word, name)
                if not isinstance(value, (int, float)) or isinstance(value, bool):
                    raise StreamError(f"word {word.word!r} {name} is not a number")
                if not math.isfinite(value):
                    raise StreamError(f"word {word.word!r} {name} is not finite")
            if word.end <= word.start:
                raise StreamError(f"word {word.word!r} is empty or inverted")
            if word.start < self.origin or word.end > self.end:
                raise StreamError(
                    f"word {word.word!r} spans [{word.start}, {word.end}] outside the "
                    f"stream [{self.origin}, {self.end}]"
                )
            if word.start < previous_start:
                raise StreamError(
                    "words must be sorted by start time; unsorted words make the "
                    "speech-gap scan skip gaps rather than fail"
                )
            previous_start = word.start
            if word.confidence is not None:
                _require_unit(f"word {word.word!r} confidence", word.confidence)

    def _validate_events(self) -> None:
        for event in self.audio_events:
            if event.label not in EMOTIONAL_EVENT_WEIGHT:
                raise StreamError(
                    f"audio event label {event.label!r} is not in the contract enum; "
                    f"known labels are {sorted(EMOTIONAL_EVENT_WEIGHT)}"
                )
            _require_unit(f"audio event {event.label} confidence", event.confidence)
            if not self.origin <= event.index < self.end:
                raise StreamError(
                    f"audio event {event.label} at {event.index} is outside the stream"
                )


def _require_unit(label: str, value: object) -> None:
    """Every scalar the contract types as Unit, checked once, in one place.

    NaN is the reason this exists rather than a clamp. `NaN <= floor` is False,
    so a NaN motion reading passes the zero-motion elimination gate untouched
    and then produces a NaN score, which makes peak selection order by a value
    for which `<` is meaningless -- a silently unstable moment list. Clamping
    would hide a missing upstream normalisation; raising says which frame.
    """
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise StreamError(f"{label} is {value!r}, not a number")
    if not math.isfinite(value):
        raise StreamError(
            f"{label} is {value!r}; NaN and infinity compare False to every threshold, "
            "so they pass elimination untouched and produce an unorderable score"
        )
    if not 0.0 <= float(value) <= 1.0:
        raise StreamError(
            f"{label} is {value}, outside [0,1]. The contract declares it a Unit; a "
            "value outside that means an upstream normalisation is missing and "
            "clamping here would hide it."
        )


# ---------------------------------------------------------------------------
# Parameters
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class MomentParams:
    """Window geometry and the rules that turn a score curve into moments.

    The defaults are chosen to be defensible rather than optimal; the eval
    harness is what will move them.

      * window 2.0s / hop 0.5s: a 2-second window is long enough that a single
        lucky frame cannot carry it and short enough to localise a one-second
        event; a 0.5s hop means every position is covered by four windows, so
        a peak is measured four times before it is believed.
      * min_separation 2.0s (one window): TWO PEAKS 0.5 SECONDS APART ARE ONE
        MOMENT. They are the same event seen twice by overlapping windows, and
        emitting both produces two records whose source ranges overlap by 75%
        and a reel that cuts to the same action twice.
      * growth_ratio 0.85: a moment grows outward while the neighbouring window
        still scores at least 85% of the peak. Lower and every moment grows to
        max_duration; higher and a genuinely uniform clip fragments into
        window-sized pieces instead of becoming one moment.
      * max_duration 8.0s: the film planner's longest hold. A moment is not a
        clip; it is the unit a planner trims from.
    """

    window_seconds: float = 2.0
    hop_seconds: float = 0.5
    min_duration_seconds: float = 1.2
    max_duration_seconds: float = 8.0
    growth_ratio: float = 0.85
    score_floor: float = 0.35
    snap_tolerance_seconds: float = 0.4
    min_separation_seconds: float = 2.0
    hook_seconds: float = 1.0
    audio_tail_seconds: float = 0.75
    min_speech_gap_seconds: float = 0.20
    full_speech_gap_seconds: float = 0.60
    onset_suppression_seconds: float = 0.15

    def validate(self) -> None:
        for name in (
            "window_seconds", "hop_seconds", "min_duration_seconds",
            "max_duration_seconds", "snap_tolerance_seconds",
            "min_separation_seconds", "hook_seconds", "audio_tail_seconds",
            "min_speech_gap_seconds", "full_speech_gap_seconds",
            "onset_suppression_seconds",
        ):
            value = getattr(self, name)
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise ParamError(f"{name} is not a number")
            if not math.isfinite(value) or value < 0.0:
                raise ParamError(f"{name}={value} must be finite and non-negative")
        if self.window_seconds <= 0.0:
            raise ParamError("window_seconds must be positive")
        if self.hop_seconds <= 0.0:
            raise ParamError("hop_seconds must be positive")
        if self.hop_seconds > self.window_seconds:
            raise ParamError(
                "hop_seconds > window_seconds leaves gaps between windows: footage "
                "between two windows is never scored and can never become a moment, "
                "which looks exactly like footage that scored badly"
            )
        if self.min_duration_seconds <= 0.0:
            raise ParamError("min_duration_seconds must be positive")
        if self.max_duration_seconds < self.min_duration_seconds:
            raise ParamError("max_duration_seconds < min_duration_seconds")
        if self.window_seconds > self.max_duration_seconds:
            raise ParamError(
                "window_seconds > max_duration_seconds: every peak would be born "
                "already over the cap"
            )
        if not 0.0 < self.growth_ratio <= 1.0:
            raise ParamError("growth_ratio must be in (0, 1]")
        if not 0.0 <= self.score_floor <= 1.0:
            raise ParamError("score_floor must be in [0, 1]")
        if self.full_speech_gap_seconds < self.min_speech_gap_seconds:
            raise ParamError("full_speech_gap_seconds < min_speech_gap_seconds")


@dataclass(frozen=True)
class EliminationThresholds:
    """Where junk stops being junk.

    Deliberately permissive: these are elimination floors, not quality bars.
    The alternative to a shaky handheld shot of a first birthday is often
    nothing at all, so the shake gate sits where footage becomes genuinely
    unwatchable rather than where it becomes imperfect.
    """

    black_luma_max: float = 0.02
    crushed_luma_max: float = 0.10
    blown_luma_min: float = 0.94
    shake_max: float = 0.65
    no_motion_max: float = 0.02
    noise_dominant_min: float = 0.90

    # Fraction of a window's frames that must carry a per-frame verdict before
    # the WINDOW is eliminated for it. A single black frame in a 2-second
    # window is a compression artefact or a flash, not a dead window; half the
    # window being black is a dead window. The frames themselves are still
    # excluded from every moment regardless of this fraction -- that is what
    # frame-granularity elimination means.
    frame_fraction: float = 0.5

    # Below this a window's fused score is not a weak candidate, it is junk.
    # Applied after fusion, hence stage="fusion".
    def validate(self) -> None:
        for name in (
            "black_luma_max", "crushed_luma_max", "blown_luma_min",
            "shake_max", "no_motion_max", "noise_dominant_min", "frame_fraction",
        ):
            _require_unit(f"threshold {name}", getattr(self, name))
        if self.black_luma_max > self.crushed_luma_max:
            raise ParamError("black_luma_max > crushed_luma_max: black frames would be "
                             "reported as merely crushed")
        if self.crushed_luma_max >= self.blown_luma_min:
            raise ParamError("crushed and blown exposure ranges overlap")
        if self.frame_fraction <= 0.0:
            raise ParamError("frame_fraction must be positive")


# The luminance range mapped onto exposure stability. A window whose luma swings
# by half the full range is a camera hunting exposure -- walking from a hallway
# into direct sun -- and looks bad no matter how good the content is.
EXPOSURE_SPREAD_FULL = 0.5

# Loudness normalisation. Below -60 LUFS is silence for any practical purpose;
# above -6 LUFS a consumer mic is at its ceiling. Mapping to a Unit here rather
# than weighting LUFS directly keeps the fusion free of native model ranges,
# which is the same rule the contract applies to every model output.
LUFS_SILENCE = -60.0
LUFS_FULL = -6.0

# Strength assigned to a subject entry or exit whose face area is unknown.
# 0.5 is the deliberate no-information value: the boundary is real (a person
# walked into frame) but its prominence was not measured. The alternative --
# dropping the snap point -- loses a genuine cut position, which is worse,
# because a missing snap point silently pushes the cut somewhere arbitrary.
UNKNOWN_SUBJECT_STRENGTH = 0.5

# Deltas at which a change becomes an onset, and the delta that counts as a
# full-strength one.
MOTION_ONSET_DELTA = 0.12
MOTION_ONSET_FULL = 0.50
AUDIO_ONSET_DELTA = 0.12
AUDIO_ONSET_FULL = 0.50
BRIGHTNESS_CHANGE_DELTA = 0.25
BRIGHTNESS_CHANGE_FULL = 0.50

# An audio event must be at least this salient to justify holding audio past a
# visual cut (SafeTrim.preserve_audio_tail).
AUDIO_TAIL_SALIENCE = 0.50


# ---------------------------------------------------------------------------
# Weights
# ---------------------------------------------------------------------------

_SIGNAL_NAMES = (
    "motion_energy",
    "stability",
    "sharpness",
    "exposure_stability",
    "face_presence",
    "face_prominence",
    "smile_intensity",
    "audio_energy",
    "speech_presence",
    "event_salience",
)


@dataclass(frozen=True)
class MomentWeights:
    """The v1 moment-score profile. Data, not constants, so per-user
    reweighting is a stored row rather than a code change.

    NOTE THE SIGN CONVENTION. Every weighted signal is "higher is better".
    `shake` is a badness measure, so what is weighted is `stability = 1 -
    shake`. Weighting shake directly with a positive coefficient is a sign
    error that produces entirely plausible numbers and ranks the shakiest
    footage first -- the exact class of silent defect this codebase keeps
    finding, so the conversion happens once, in aggregation, and the badness
    value never reaches the fusion.

      * stability is the heaviest single signal because camera shake is the
        most common reason handheld video is unusable, and unlike a photo there
        is no second frame to fall back on.
      * face_presence is heavy in a family library for the same reason face
        quality is heavy in photo fusion: a clear subject beats a marginally
        smoother frame without one.
      * event_salience carries real weight because laughter and cheering are
        the only emotional-peak evidence a local model can produce at all.
      * smile_intensity is deliberately light. It is a proxy for delight, and a
        proxy that a concentrating child fails.
    """

    weights_id: str = "moment-default-v1"

    motion_energy: float = 0.16
    stability: float = 0.18
    sharpness: float = 0.14
    exposure_stability: float = 0.08
    face_presence: float = 0.14
    face_prominence: float = 0.06
    smile_intensity: float = 0.04
    audio_energy: float = 0.06
    speech_presence: float = 0.06
    event_salience: float = 0.08

    def as_map(self) -> dict[str, float]:
        return {name: getattr(self, name) for name in _SIGNAL_NAMES}

    def validate(self) -> None:
        """Refuse a profile that cannot produce an honest coverage figure.

        A NEGATIVE weight is silently excluded from scoring -- the live-weight
        filter takes `> 0` -- while still counting toward the applicable-weight
        denominator, so coverage reports a fraction that was never measured and
        can exceed 1 before being clamped. Ranking engine fusion.py was found
        with exactly this hole; the same shape here would have the same hole.

        Zero is allowed and means "this profile does not use that signal".
        """
        weights = self.as_map()
        for name, weight in weights.items():
            if not isinstance(weight, (int, float)) or isinstance(weight, bool):
                raise WeightError(f"{name}={weight!r} is not a number")
            if not math.isfinite(weight):
                raise WeightError(f"{name}={weight!r} is not finite")
        negative = sorted(n for n, w in weights.items() if w < 0.0)
        if negative:
            raise WeightError(
                f"negative weights {negative}: a negative weight is excluded from "
                "scoring but still counts toward the coverage denominator, making "
                "coverage report a fraction that was never measured"
            )
        if sum(weights.values()) <= 0.0:
            raise WeightError("all weights are zero: nothing would be measured")

    def digest(self) -> str:
        """Stable digest of the weight values, over CANONICAL BYTES.

        Not json.dumps(sort_keys=True): Python writes the float 1.0 as `1.0`
        and JavaScript writes `1`, so a profile containing a whole number
        digests differently in the desktop shell than in the pipeline and two
        identical profiles read as two different scorers. Fixed 6-decimal
        formatting is produced identically by every language, and six decimals
        is well past the precision at which a weight change alters any ranking.
        """
        payload = ";".join(
            f"{name}={value:.6f}" for name, value in sorted(self.as_map().items())
        ).encode("utf-8")
        return hashlib.blake2b(payload, digest_size=16).hexdigest()


# The three decomposition scores are NOT user-reweightable in v1 and are
# therefore constants rather than a profile. Only `moment_score` drives
# selection, so only `moment_score` is what a PrefEvent can train; giving the
# others a tunable profile would imply a feedback signal that does not exist.
HOOK_WEIGHTS: Mapping[str, float] = {
    "motion_peak": 0.40,
    "face_presence": 0.25,
    "event_salience": 0.20,
    "stability": 0.15,
}
EMOTIONAL_WEIGHTS: Mapping[str, float] = {
    "event_salience": 0.40,
    "smile_intensity": 0.30,
    "face_prominence": 0.15,
    "audio_energy": 0.15,
}
TECHNICAL_WEIGHTS: Mapping[str, float] = {
    "stability": 0.40,
    "sharpness": 0.35,
    "exposure_stability": 0.25,
}


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class WindowFeatures:
    """A frame span reduced to the named, bounded features the contract's
    MomentFeatures block carries. Named and stable rather than an opaque
    vector because these are exactly what a PrefEvent captures as decision
    context."""

    motion_energy: float | None
    motion_peak: float | None
    shake: float | None
    stability: float | None
    exposure_stability: float | None
    sharpness: float | None
    face_presence: float | None
    face_prominence: float | None
    smile_intensity: float | None
    audio_energy: float | None
    loudness_lufs: float | None
    speech_presence: float | None
    noise_ratio: float | None
    event_salience: float | None
    has_face: bool
    representative_index: int | None

    def as_values(self) -> dict[str, float | None]:
        return {
            "motion_energy": self.motion_energy,
            "motion_peak": self.motion_peak,
            "stability": self.stability,
            "sharpness": self.sharpness,
            "exposure_stability": self.exposure_stability,
            "face_presence": self.face_presence,
            "face_prominence": self.face_prominence,
            "smile_intensity": self.smile_intensity,
            "audio_energy": self.audio_energy,
            "speech_presence": self.speech_presence,
            "event_salience": self.event_salience,
        }


def _mean(values: Sequence[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _lufs_to_unit(lufs: float) -> float:
    return _clamp((lufs - LUFS_SILENCE) / (LUFS_FULL - LUFS_SILENCE))


def _clamp(value: float) -> float:
    return min(max(value, 0.0), 1.0)


def _quantise(value: float) -> float:
    """Six decimals, matching the precision the contract's Score stores.

    Float addition is not associative, so an unquantised fusion can differ in
    the last bit between machines. Invisible until it flips a tie in peak
    selection and a different second of footage silently ends up in the reel.
    """
    return round(value + 0.0, 6)


def aggregate(stream: FeatureStream, lo: int, hi: int) -> WindowFeatures:
    """Reduce the half-open frame span [lo, hi) to window features.

    Aggregation choices worth defending:

      * motion is a MEAN and motion_peak a MAX. The mean says how much is
        happening; the peak is what a hook needs, because the first second of a
        reel is won by one visible burst, not by an average.
      * smile is a MAX, not a mean. A smile is an event inside the window and a
        mean dilutes it by the window length, so a two-second window containing
        a half-second grin would score a third of one containing a constant
        polite smile.
      * exposure stability is derived from the luma SPREAD, not the variance.
        Spread is what a viewer sees (the shot got blown out and came back);
        variance would rank a window that flickers gently below one that blows
        out once, which is backwards.
      * face_presence counts frames with a face over frames where detection ran,
        never over the whole window, so a partially-analysed window reports the
        fraction it actually measured.
    """
    frames = stream.span(lo, hi)
    if not frames:
        raise StreamError(f"empty window [{lo}, {hi})")

    motions = [f.motion for f in frames if f.motion is not None]
    shakes = [f.shake for f in frames if f.shake is not None]
    sharps = [f.sharpness for f in frames if f.sharpness is not None]
    lumas = [f.luma for f in frames if f.luma is not None]
    counted = [f for f in frames if f.face_count is not None]
    with_faces = [f for f in counted if f.face_count > 0]
    areas = [f.face_area for f in with_faces if f.face_area is not None]
    smiles = [f.smile for f in with_faces if f.smile is not None]
    louds = [f.loudness_lufs for f in frames if f.loudness_lufs is not None]
    speech = [f for f in frames if f.speech is not None]
    noises = [f.noise for f in frames if f.noise is not None]

    shake_mean = _mean(shakes)
    exposure_stability = None
    if lumas:
        spread = max(lumas) - min(lumas)
        exposure_stability = _clamp(1.0 - spread / EXPOSURE_SPREAD_FULL)

    if stream.audio_state is AudioState.MEASURED:
        events = [e for e in stream.audio_events if lo <= e.index < hi]
        salience = max(
            (e.confidence * EMOTIONAL_EVENT_WEIGHT[e.label] for e in events),
            default=0.0,
        )
    else:
        # NOT_RUN and NO_AUDIO both mean "no event evidence", but they mean
        # opposite things for coverage; the applicability map, not this value,
        # is where that is expressed.
        salience = None

    return WindowFeatures(
        motion_energy=_quantise(_mean(motions)) if motions else None,
        motion_peak=_quantise(max(motions)) if motions else None,
        shake=_quantise(shake_mean) if shake_mean is not None else None,
        stability=_quantise(1.0 - shake_mean) if shake_mean is not None else None,
        exposure_stability=(
            _quantise(exposure_stability) if exposure_stability is not None else None
        ),
        sharpness=_quantise(_mean(sharps)) if sharps else None,
        face_presence=_quantise(len(with_faces) / len(counted)) if counted else None,
        face_prominence=_quantise(max(areas)) if areas else None,
        smile_intensity=_quantise(max(smiles)) if smiles else None,
        audio_energy=(
            _quantise(_mean([_lufs_to_unit(v) for v in louds])) if louds else None
        ),
        loudness_lufs=_quantise(_mean(louds)) if louds else None,
        speech_presence=(
            _quantise(sum(1 for f in speech if f.speech) / len(speech)) if speech else None
        ),
        noise_ratio=_quantise(_mean(noises)) if noises else None,
        event_salience=_quantise(salience) if salience is not None else None,
        has_face=bool(with_faces),
        representative_index=_representative_index(frames),
    )


def _representative_index(frames: Sequence[FrameSample]) -> int | None:
    """The best single frame in the span -- the contact-sheet tile the frontier
    model is shown.

    Scored on the frame's own merits only (sharp, steady, a prominent face),
    never on motion: the most representative frame of an action shot is the one
    you can actually see, and optical flow peaks exactly where the frame is
    most smeared. Ties break on the earliest index so the choice does not
    depend on iteration order.
    """
    best_index: int | None = None
    best_score = -1.0
    for frame in frames:
        parts = []
        if frame.sharpness is not None:
            parts.append(frame.sharpness)
        if frame.shake is not None:
            parts.append(1.0 - frame.shake)
        if frame.face_area is not None:
            parts.append(frame.face_area)
        if not parts:
            continue
        score = _quantise(sum(parts) / len(parts))
        if score > best_score:
            best_score = score
            best_index = frame.index
    return best_index


# ---------------------------------------------------------------------------
# The one fusion path
# ---------------------------------------------------------------------------

def _weighted(
    values: Mapping[str, float | None],
    weights: Mapping[str, float],
    applicable: Mapping[str, bool],
) -> tuple[float, float, tuple[tuple[str, float, float], ...]]:
    """Renormalised weighted mean. THE only fusion in this file.

    moment_score, technical, hook_potential and emotional_peak all come through
    here with different weight maps, so a fix to the renormalisation is a fix
    to all four. Four hand-rolled weighted means is how three of them end up
    treating a missing signal differently from the fourth.

    Returns (value, coverage, contributions). Contributions exist because
    "why is this moment 0.82" has to stay answerable; a bare float is not an
    answer, and the reel planner's variant picker shows the reason to the user.
    """
    live = {
        name: weight
        for name, weight in weights.items()
        if weight > 0.0 and applicable.get(name, True) and values.get(name) is not None
    }
    live_total = sum(live.values())
    applicable_total = sum(
        weight for name, weight in weights.items()
        if weight > 0.0 and applicable.get(name, True)
    )
    if live_total <= 0.0:
        # Nothing measured, nothing claimed. Not an elimination -- the footage
        # may be fine, we simply have not looked at it.
        return 0.0, 0.0, ()

    total = 0.0
    contributions: list[tuple[str, float, float]] = []
    for name in sorted(live):  # sorted, so summation order is fixed everywhere
        share = live[name] / live_total
        value = float(values[name])
        total += value * share
        contributions.append((name, _quantise(value), _quantise(share)))

    coverage = live_total / applicable_total if applicable_total > 0.0 else 0.0
    return _quantise(_clamp(total)), _quantise(min(coverage, 1.0)), tuple(contributions)


def applicability(stream: FeatureStream, features: WindowFeatures) -> dict[str, bool]:
    """Which signals COULD have been measured for this window.

    The distinction that this function exists for: `face_prominence` is absent
    for a drone shot over a beach and absent for footage the face model has not
    reached, and those two absences must move coverage in opposite directions.
    Counting a face weight a landscape can never earn caps every landscape
    below the comparability threshold; NOT counting it for un-analysed footage
    makes a half-scanned library look fully measured.

    Applicability is per WINDOW, not per stream, for the face signals: a stream
    where faces appear at all is HAS_FACES, but a window inside it with nobody
    in frame has no face to measure a prominence of. `face_presence` stays
    applicable throughout, because 0.0 there is a real measurement -- "we
    looked, nobody was in frame" -- not a missing one.
    """
    face_measurable = (
        stream.face_state is FaceState.NOT_RUN or features.has_face
    )
    audio_measurable = stream.audio_state is not AudioState.NO_AUDIO
    return {
        "motion_energy": True,
        "motion_peak": True,
        "stability": True,
        "sharpness": True,
        "exposure_stability": True,
        "face_presence": True,
        "face_prominence": face_measurable,
        "smile_intensity": face_measurable,
        "audio_energy": audio_measurable,
        "speech_presence": audio_measurable,
        "event_salience": audio_measurable,
    }


# ---------------------------------------------------------------------------
# Elimination
# ---------------------------------------------------------------------------

REASON_BLACK = "black_frame"
REASON_LENS = "lens_obstructed"
REASON_BLOWN = "blown_exposure"
REASON_CRUSHED = "crushed_exposure"
REASON_SHAKE = "shake"
REASON_NO_MOTION = "no_motion"
REASON_WIND = "wind_noise_dominant"
REASON_TOO_SHORT = "too_short"
REASON_BELOW_FLOOR = "below_score_floor"

# Fixed check order, so two hosts report the same primary reason for footage
# that fails several at once. Sorting alphabetically instead would put
# "black_frame" ahead of "lens_obstructed" by accident of spelling rather than
# by which verdict actually explains the footage to a user.
FRAME_REASON_ORDER = (REASON_BLACK, REASON_CRUSHED, REASON_BLOWN, REASON_LENS)
WINDOW_REASON_ORDER = FRAME_REASON_ORDER + (REASON_SHAKE, REASON_NO_MOTION, REASON_WIND)


def frame_reasons(frame: FrameSample, thresholds: EliminationThresholds) -> tuple[str, ...]:
    """The cheap per-frame verdicts: is this frame junk on its own?

    These are the free ones -- they need luma and a lens-cap flag, both of
    which fall out of proxy generation -- and on a real action-cam card they
    account for the overwhelming majority of what gets discarded. That is why
    they are evaluated per frame and enforced as regions: a moment may not
    contain a single one of these frames, however good its neighbours are.

    Black, crushed and blown are mutually exclusive by construction. A black
    frame reported additionally as crushed would double-count in the fraction
    test and eliminate a window at half the intended threshold.
    """
    reasons: list[str] = []
    if frame.luma is not None:
        if frame.luma <= thresholds.black_luma_max:
            reasons.append(REASON_BLACK)
        elif frame.luma <= thresholds.crushed_luma_max:
            reasons.append(REASON_CRUSHED)
        elif frame.luma >= thresholds.blown_luma_min:
            reasons.append(REASON_BLOWN)
    if frame.lens_obstructed:
        reasons.append(REASON_LENS)
    return tuple(reasons)


def window_reasons(
    features: WindowFeatures,
    frame_verdicts: Sequence[tuple[str, ...]],
    thresholds: EliminationThresholds,
) -> tuple[str, ...]:
    """Why this window does not compete, in fixed check order.

    THE NO-MOTION GUARD IS THE INTERESTING PART. "Zero-motion tripod dead time"
    is one of the highest-yield eliminations on a real card, and it is also one
    keystroke away from deleting the best footage in a family library: an
    interview on a tripod, a sleeping baby, a birthday speech held on a locked
    shot. So near-zero motion eliminates only when nothing MEASURED contradicts
    it -- no face in frame, no speech, no audio event. When face and audio
    analysis have not run at all, nothing can contradict it and the window is
    eliminated, because zero motion is then the only evidence available and
    dead time is the overwhelmingly likely explanation. That is a deliberate
    recall trade recorded here rather than an oversight: the culling product
    re-runs after analysis completes, and this stage is explicitly the cheap
    one.

    Wind is eliminated only at 0.90, where the microphone is recording wind
    rather than the scene. The alternative -- treating it as a score penalty --
    was rejected for the reason elimination exists at all: a penalised window
    still wins a bad pool, and a reel that cuts to thirty seconds of roar is a
    worse failure than a reel that is one shot shorter.
    """
    reasons: list[str] = []
    total = len(frame_verdicts)
    if total:
        for reason in FRAME_REASON_ORDER:
            hits = sum(1 for verdicts in frame_verdicts if reason in verdicts)
            if hits / total >= thresholds.frame_fraction:
                reasons.append(reason)

    if features.shake is not None and features.shake >= thresholds.shake_max:
        reasons.append(REASON_SHAKE)

    if features.motion_energy is not None and features.motion_energy <= thresholds.no_motion_max:
        contradicted = (
            (features.face_presence is not None and features.face_presence > 0.0)
            or (features.speech_presence is not None and features.speech_presence > 0.0)
            or (features.event_salience is not None and features.event_salience > 0.0)
        )
        if not contradicted:
            reasons.append(REASON_NO_MOTION)

    if features.noise_ratio is not None and features.noise_ratio >= thresholds.noise_dominant_min:
        reasons.append(REASON_WIND)

    return tuple(reasons)


# ---------------------------------------------------------------------------
# Snap points
# ---------------------------------------------------------------------------

CUT_DIRECTION = {
    "shot_boundary": "both",
    "motion_onset": "in",
    "motion_offset": "out",
    "audio_onset": "in",
    "speech_gap": "both",
    "speech_start": "in",
    "speech_end": "out",
    "subject_entry": "in",
    "subject_exit": "out",
    "impact": "in",
    "scene_brightness_change": "both",
}


@dataclass(frozen=True)
class SnapPoint:
    """A position at which cutting is defensible, with a reason.

    `time` is in source frames and may be fractional, because speech-derived
    points are: a word does not begin on a frame boundary, and the 50ms
    beat-alignment gate downstream has no headroom to spare on rounding.
    """

    time: float
    kind: str
    strength: float
    cut_direction: str

    def allows(self, direction: str) -> bool:
        return self.cut_direction in (direction, "both")


def _suppress(
    candidates: Sequence[tuple[float, float]], radius: int
) -> list[tuple[float, float]]:
    """Non-maximum suppression over (position, strength) pairs.

    Greedy by strongest-first with an explicit tie-break on position, rather
    than a strict local-maximum test: a plateau -- motion rising at a constant
    rate for six frames -- has no strict local maximum, so a naive test emits
    either nothing or all six. Greedy NMS emits exactly one, the earliest of
    the strongest, deterministically.
    """
    accepted: list[tuple[float, float]] = []
    for position, strength in sorted(
        candidates, key=lambda row: (-_quantise(row[1]), row[0])
    ):
        if all(abs(position - kept) >= radius for kept, _ in accepted):
            accepted.append((position, strength))
    return accepted


def snap_points(
    stream: FeatureStream,
    params: MomentParams,
) -> tuple[SnapPoint, ...]:
    """Every certified cut position in the stream, sorted by (time, kind).

    A candidate that falls STRICTLY inside a spoken word is dropped here, at
    the source, rather than filtered at selection time. The planner is only
    ever handed positions that are already safe, which is what turns "never cut
    mid-word" into a property of the data. Points exactly at a word edge
    survive: that is a boundary, not a mid-word cut.
    """
    rate = stream.rate
    origin, end = stream.origin, stream.end
    suppression = _frames_for(params.onset_suppression_seconds, rate)
    points: list[SnapPoint] = []

    # Shot boundaries are the strongest cut positions there are: the footage
    # already cuts there.
    shot_starts = {shot.start for shot in stream.shots}
    for shot in stream.shots:
        if shot.start > origin:
            points.append(SnapPoint(float(shot.start), "shot_boundary", 1.0,
                                    CUT_DIRECTION["shot_boundary"]))

    motion_rises: list[tuple[float, float]] = []
    motion_falls: list[tuple[float, float]] = []
    audio_rises: list[tuple[float, float]] = []
    brightness: list[tuple[float, float]] = []
    entries: list[tuple[float, float]] = []
    exits: list[tuple[float, float]] = []

    previous = None
    for frame in stream.frames:
        if previous is not None:
            at_cut = frame.index in shot_starts
            if previous.motion is not None and frame.motion is not None and not at_cut:
                # A cut is not a motion onset. Optical flow across a shot change
                # is enormous and meaningless, and without this guard every hard
                # cut manufactures a full-strength "motion onset" one frame
                # after the shot_boundary point that already describes it.
                delta = frame.motion - previous.motion
                if delta >= MOTION_ONSET_DELTA:
                    motion_rises.append((float(frame.index), _clamp(delta / MOTION_ONSET_FULL)))
                elif -delta >= MOTION_ONSET_DELTA:
                    motion_falls.append((float(frame.index), _clamp(-delta / MOTION_ONSET_FULL)))
            if previous.loudness_lufs is not None and frame.loudness_lufs is not None:
                delta = _lufs_to_unit(frame.loudness_lufs) - _lufs_to_unit(previous.loudness_lufs)
                if delta >= AUDIO_ONSET_DELTA:
                    audio_rises.append((float(frame.index), _clamp(delta / AUDIO_ONSET_FULL)))
            if previous.luma is not None and frame.luma is not None and not at_cut:
                delta = abs(frame.luma - previous.luma)
                if delta >= BRIGHTNESS_CHANGE_DELTA:
                    brightness.append((float(frame.index), _clamp(delta / BRIGHTNESS_CHANGE_FULL)))
            if previous.face_count is not None and frame.face_count is not None:
                if previous.face_count == 0 and frame.face_count > 0:
                    strength = frame.face_area if frame.face_area is not None else UNKNOWN_SUBJECT_STRENGTH
                    entries.append((float(frame.index), strength))
                elif previous.face_count > 0 and frame.face_count == 0:
                    strength = previous.face_area if previous.face_area is not None else UNKNOWN_SUBJECT_STRENGTH
                    exits.append((float(frame.index), strength))
        previous = frame

    for kind, raw in (
        ("motion_onset", motion_rises),
        ("motion_offset", motion_falls),
        ("audio_onset", audio_rises),
        ("scene_brightness_change", brightness),
        ("subject_entry", entries),
        ("subject_exit", exits),
    ):
        for position, strength in _suppress(raw, suppression):
            points.append(SnapPoint(position, kind, _quantise(strength), CUT_DIRECTION[kind]))

    for event in stream.audio_events:
        if event.label in IMPACT_LABELS:
            points.append(SnapPoint(float(event.index), "impact", _quantise(event.confidence),
                                    CUT_DIRECTION["impact"]))

    points.extend(_speech_points(stream, params))

    words = tuple(stream.words)
    certified = [p for p in points if origin <= p.time <= end and not _inside_word(p.time, words)]
    return tuple(sorted(certified, key=lambda p: (p.time, p.kind, -p.strength)))


def _speech_points(stream: FeatureStream, params: MomentParams) -> list[SnapPoint]:
    """Speech gaps, starts and ends -- the safest cut positions in the stream.

    A gap is emitted at its MIDPOINT rather than at either edge, because the
    edges are exactly where a rounding error or a word-timestamp error puts the
    cut back inside a word. The middle of a 300ms silence has 150ms of slack on
    both sides.
    """
    if not stream.words:
        return []
    min_gap = params.min_speech_gap_seconds * stream.rate
    full_gap = params.full_speech_gap_seconds * stream.rate
    points: list[SnapPoint] = []

    ordered = sorted(stream.words, key=lambda w: (w.start, w.end, w.word))
    previous_end = float(stream.origin)
    for word in ordered:
        gap = word.start - previous_end
        if gap >= min_gap and min_gap > 0.0:
            midpoint = previous_end + gap / 2.0
            strength = _clamp(gap / full_gap) if full_gap > 0.0 else 1.0
            points.append(SnapPoint(_quantise(midpoint), "speech_gap", _quantise(strength),
                                    CUT_DIRECTION["speech_gap"]))
            points.append(SnapPoint(_quantise(math.floor(word.start)), "speech_start",
                                    _quantise(strength), CUT_DIRECTION["speech_start"]))
            points.append(SnapPoint(_quantise(math.ceil(previous_end)), "speech_end",
                                    _quantise(strength), CUT_DIRECTION["speech_end"]))
        previous_end = max(previous_end, word.end)

    trailing = stream.end - previous_end
    if trailing >= min_gap and min_gap > 0.0:
        strength = _clamp(trailing / full_gap) if full_gap > 0.0 else 1.0
        points.append(SnapPoint(_quantise(math.ceil(previous_end)), "speech_end",
                                _quantise(strength), CUT_DIRECTION["speech_end"]))
    return points


def _inside_word(time: float, words: Sequence[SpeechWord]) -> bool:
    """Strictly inside -- a position exactly on a word edge is a boundary."""
    return any(word.start < time < word.end for word in words)


def _word_safe(time: float, words: Sequence[SpeechWord], *, direction: str) -> float:
    """Walk a raw boundary out of any word it landed in.

    This is the backstop for when no snap point exists inside the tolerance:
    the moment still gets a boundary, and that boundary still must not sit
    inside a word. An in-point moves EARLIER (to before the word), which keeps
    the whole word; an out-point moves LATER, same reason. Both directions
    round away from speech, so the rounding itself can only add safety.

    The caller re-checks duration afterwards, because widening is not always
    possible inside the region.
    """
    for word in sorted(words, key=lambda w: (w.start, w.end, w.word)):
        if word.start < time < word.end:
            return math.floor(word.start) if direction == "in" else math.ceil(word.end)
    return time


# ---------------------------------------------------------------------------
# Windows, peaks, moments
# ---------------------------------------------------------------------------

def _frames_for(seconds: float, rate: float) -> int:
    """Seconds -> whole frames, at least one.

    math.floor(x + 0.5) rather than round(): Python's round() is banker's
    rounding, so round(0.5) is 0 and round(1.5) is 2. A window length that
    depends on whether the frame count happens to land on an even half is a
    determinism hazard for no benefit.
    """
    return max(1, int(math.floor(seconds * rate + 0.5)))


@dataclass(frozen=True)
class WindowScore:
    start: int
    end: int
    features: WindowFeatures
    value: float
    coverage: float
    contributions: tuple[tuple[str, float, float], ...]
    reasons: tuple[str, ...]
    stage: str | None

    @property
    def eliminated(self) -> bool:
        return bool(self.reasons)


@dataclass(frozen=True)
class Moment:
    """One scored interval, ready to be written as a MomentRecord."""

    media_id: str
    rate: float
    start: int
    end: int
    eliminated: bool
    reasons: tuple[str, ...]
    stage: str | None
    features: WindowFeatures | None = None
    value: float = 0.0
    coverage: float = 0.0
    contributions: tuple[tuple[str, float, float], ...] = ()
    technical: float | None = None
    hook_potential: float | None = None
    emotional_peak: float | None = None
    snap_points: tuple[SnapPoint, ...] = ()
    safe_trim: dict | None = None
    transcript: dict | None = None
    shot_id: str | None = None
    weights_id: str = ""
    weights_digest: str = ""

    @property
    def duration(self) -> int:
        return self.end - self.start

    def duration_seconds(self) -> float:
        return _quantise(self.duration / self.rate)

    def moment_id(self) -> str:
        """Content address of the moment DEFINITION.

        Over (media_id, source_range, scorer identity), exactly as the schema
        specifies, so a rescore with different weights or a different peak
        picker yields new ids and an EDL always points at the moment definition
        it was planned against.

        blake2b-256 rather than BLAKE3: BLAKE3 is not in the stdlib and this
        package is stdlib-only. The output is a schema-valid 64-hex digest and
        the CANONICALISATION -- which is the part that is easy to get wrong and
        hard to change later -- is the real content here. Swapping in the real
        BLAKE3 changes every id but no other behaviour; it is a one-line change
        in this function, tracked as a known gap rather than hidden.
        """
        payload = "|".join((
            self.media_id,
            f"{self.start}",
            f"{self.duration}",
            f"{self.rate:.9f}",
            SCORER_ID,
            FEATURE_SET_ID,
            self.weights_id,
            self.weights_digest,
        )).encode("utf-8")
        return hashlib.blake2b(payload, digest_size=32).hexdigest()

    def to_record(self, *, created_at: str | None = None,
                  model_runs: Sequence[Mapping] | None = None) -> dict:
        """The contract MomentRecord, as a plain dict.

        Unmeasured features are OMITTED rather than written as null. The two
        are equivalent to a validator, and omitting keeps a 200-hour library's
        records small; more importantly it means a null in a stored record is
        always something a producer chose to write, never a placeholder this
        function manufactured.
        """
        record: dict = {
            "schema_version": "v0",
            "moment_id": self.moment_id(),
            "media_id": self.media_id,
            "source_range": {
                "start_time": _rational(self.start, self.rate),
                "duration": _rational(self.duration, self.rate),
            },
            "scores": self._scores(),
            "elimination": {
                "eliminated": self.eliminated,
                "reasons": list(self.reasons),
                "stage": self.stage,
            },
        }
        if self.shot_id is not None:
            record["shot_id"] = self.shot_id
        if self.features is not None:
            record["features"] = self._features_block()
        if self.snap_points:
            record["snap_points"] = [
                {
                    "time": _rational(point.time, self.rate),
                    "kind": point.kind,
                    "strength": point.strength,
                    "cut_direction": point.cut_direction,
                }
                for point in self.snap_points
            ]
        if self.safe_trim is not None:
            record["safe_trim"] = self.safe_trim
        if self.transcript is not None:
            record["transcript"] = self.transcript
        if model_runs:
            record["model_runs"] = [dict(run) for run in model_runs]
        if created_at is not None:
            record["created_at"] = created_at
        return record

    def _scores(self) -> dict:
        scores: dict = {
            "moment_score": {"value": self.value},
            "source": "local_fusion",
            "fusion_weights_version": self.weights_id,
        }
        for name, value in (
            ("technical", self.technical),
            ("hook_potential", self.hook_potential),
            ("emotional_peak", self.emotional_peak),
        ):
            # A sub-score of 0.0 for "nothing was measured" is the exact lie
            # the renormalisation exists to avoid, so an uncovered sub-score is
            # absent rather than zero.
            if value is not None:
                scores[name] = {"value": value}
        return scores

    def _features_block(self) -> dict:
        features = self.features
        assert features is not None
        block: dict = {}
        for key, value in (
            ("motion_energy", features.motion_energy),
            ("motion_peak", features.motion_peak),
            ("shake", features.shake),
            ("exposure_stability", features.exposure_stability),
            ("sharpness", features.sharpness),
            ("face_presence", features.face_presence),
            ("max_face_area_ratio", features.face_prominence),
            ("smile_intensity", features.smile_intensity),
        ):
            if value is not None:
                block[key] = value
        if features.representative_index is not None:
            block["representative_frame_time"] = _rational(
                features.representative_index, self.rate
            )
        audio: dict = {}
        for key, value in (
            ("loudness_lufs", features.loudness_lufs),
            ("speech_ratio", features.speech_presence),
            ("noise_ratio", features.noise_ratio),
        ):
            if value is not None:
                audio[key] = value
        if self._audio_events:
            audio["events"] = [
                {
                    "label": event.label,
                    "confidence": event.confidence,
                    "time": _rational(event.index, self.rate),
                }
                for event in self._audio_events
            ]
        if audio:
            block["audio"] = audio
        return block

    _audio_events: tuple[AudioEvent, ...] = ()


def _rational(value: float, rate: float) -> dict:
    """A contract RationalTime.

    Integral values are emitted as ints. `{"value": 12}` and `{"value": 12.0}`
    validate identically and serialise differently, and "same plan = identical
    render" is a claim about bytes.
    """
    number = float(value)
    return {
        "value": int(number) if number.is_integer() else _quantise(number),
        "rate": rate,
    }


@dataclass(frozen=True)
class MomentAnalysis:
    """Everything the culling product and the planners need from one pass."""

    media_id: str
    rate: float
    moments: tuple[Moment, ...]
    eliminated: tuple[Moment, ...]
    frames_total: int
    frames_kept: int
    windows_scored: int
    windows_eliminated: int
    reason_counts: tuple[tuple[str, int], ...]
    weights_id: str
    weights_digest: str
    feature_set_id: str = FEATURE_SET_ID
    scorer_id: str = SCORER_ID

    @property
    def kept_seconds(self) -> float:
        return _quantise(sum(m.duration for m in self.moments) / self.rate)

    @property
    def total_seconds(self) -> float:
        return _quantise(self.frames_total / self.rate)

    def records(self, **kwargs) -> list[dict]:
        return [moment.to_record(**kwargs) for moment in self.moments]


def analyse(
    stream: FeatureStream,
    *,
    params: MomentParams | None = None,
    weights: MomentWeights | None = None,
    thresholds: EliminationThresholds | None = None,
) -> MomentAnalysis:
    """Score a feature stream and return its moments.

    The pipeline, in the order the build plan puts it:

      1. per-frame elimination  -> regions no moment may touch
      2. windows                -> the sliding score curve
      3. window elimination     -> which windows may not compete
      4. fusion                 -> the score, for those that survived
      5. peaks (NMS)            -> local maxima, one per event
      6. growth                 -> a peak becomes an interval
      7. snapping               -> that interval's edges become cuttable
      8. overlap resolution     -> two moments never describe the same footage
    """
    params = params or MomentParams()
    weights = weights or MomentWeights()
    thresholds = thresholds or EliminationThresholds()
    stream.validate()
    params.validate()
    weights.validate()
    thresholds.validate()

    rate = stream.rate
    origin, end = stream.origin, stream.end
    frames_total = end - origin

    verdicts = [frame_reasons(frame, thresholds) for frame in stream.frames]
    hard = [bool(v) for v in verdicts]

    min_frames = _frames_for(params.min_duration_seconds, rate)
    max_frames = _frames_for(params.max_duration_seconds, rate)
    window_frames = min(_frames_for(params.window_seconds, rate), frames_total)
    hop_frames = _frames_for(params.hop_seconds, rate)

    if frames_total < min_frames:
        # The whole clip is shorter than the shortest usable moment. A record
        # that says so is worth more than no record: the culling UI has to be
        # able to explain where the missing seconds went.
        whole = Moment(
            media_id=stream.media_id, rate=rate, start=origin, end=end,
            eliminated=True, reasons=(REASON_TOO_SHORT,), stage="classical",
            weights_id=weights.weights_id, weights_digest=weights.digest(),
        )
        return MomentAnalysis(
            media_id=stream.media_id, rate=rate, moments=(), eliminated=(whole,),
            frames_total=frames_total, frames_kept=0, windows_scored=0,
            windows_eliminated=0, reason_counts=((REASON_TOO_SHORT, 1),),
            weights_id=weights.weights_id, weights_digest=weights.digest(),
        )

    windows = _score_windows(
        stream, verdicts, window_frames, hop_frames, weights, thresholds, params
    )
    regions = _regions(origin, hard)
    shot_lookup = tuple(stream.shots)
    points = snap_points(stream, params)

    peaks = _pick_peaks(windows, _frames_for(params.min_separation_seconds, rate))

    accepted: list[Moment] = []
    for index in peaks:
        moment = _grow(
            stream, windows, index, regions, shot_lookup, points, params, weights,
            min_frames, max_frames, thresholds,
        )
        if moment is None:
            continue
        # Two moments never describe the same footage. Growth from two peaks
        # that NMS kept apart can still meet in the middle; the stronger peak
        # was processed first, so the weaker one is dropped whole rather than
        # clipped. A clipped remnant is a partial action, and it would need
        # re-snapping which can push it straight back into the overlap.
        if any(moment.start < kept.end and kept.start < moment.end for kept in accepted):
            continue
        accepted.append(moment)

    accepted.sort(key=lambda m: (m.start, m.end))
    eliminated = _eliminated_runs(stream, windows, weights)

    counts: dict[str, int] = {}
    for window in windows:
        for reason in window.reasons:
            counts[reason] = counts.get(reason, 0) + 1

    return MomentAnalysis(
        media_id=stream.media_id,
        rate=rate,
        moments=tuple(accepted),
        eliminated=eliminated,
        frames_total=frames_total,
        frames_kept=sum(m.duration for m in accepted),
        windows_scored=len(windows),
        windows_eliminated=sum(1 for w in windows if w.eliminated),
        reason_counts=tuple(sorted(counts.items())),
        weights_id=weights.weights_id,
        weights_digest=weights.digest(),
    )


def _score_windows(
    stream: FeatureStream,
    verdicts: Sequence[tuple[str, ...]],
    window_frames: int,
    hop_frames: int,
    weights: MomentWeights,
    thresholds: EliminationThresholds,
    params: MomentParams,
) -> tuple[WindowScore, ...]:
    """The sliding window, including the tail.

    range(0, n - w + 1, hop) leaves up to hop-1 frames at the end covered by no
    window whose start is on the grid, so a moment in the last half second of a
    clip -- the shot that ends the day, which is exactly the shot people keep --
    would be invisible. A final window flush with the end of the stream fixes
    that; it is deduplicated when the grid already lands there.
    """
    origin, end = stream.origin, stream.end
    starts = list(range(origin, end - window_frames + 1, hop_frames))
    tail = end - window_frames
    if not starts:
        starts = [origin]
    elif starts[-1] != tail and tail > starts[-1]:
        starts.append(tail)

    scored: list[WindowScore] = []
    for start in starts:
        stop = min(start + window_frames, end)
        features = aggregate(stream, start, stop)
        reasons = window_reasons(
            features, verdicts[start - origin:stop - origin], thresholds
        )
        if reasons:
            scored.append(WindowScore(start, stop, features, 0.0, 0.0, (), reasons, "classical"))
            continue
        value, coverage, contributions = _weighted(
            features.as_values(), weights.as_map(), applicability(stream, features)
        )
        if value < params.score_floor:
            scored.append(WindowScore(
                start, stop, features, value, coverage, contributions,
                (REASON_BELOW_FLOOR,), "fusion",
            ))
            continue
        scored.append(WindowScore(start, stop, features, value, coverage, contributions, (), None))
    return tuple(scored)


def _regions(origin: int, hard: Sequence[bool]) -> tuple[tuple[int, int], ...]:
    """Maximal half-open runs of frames that survived per-frame elimination.

    These are the only spans a moment may occupy. Everything else -- growth,
    snapping, trimming -- is clipped to one of them, which is how "eliminated
    regions never compete" survives the fact that windows overlap by 75%.
    """
    runs: list[tuple[int, int]] = []
    start: int | None = None
    for offset, eliminated in enumerate(hard):
        if eliminated:
            if start is not None:
                runs.append((start, origin + offset))
                start = None
        elif start is None:
            start = origin + offset
    if start is not None:
        runs.append((start, origin + len(hard)))
    return tuple(runs)


def _containing(spans: Sequence[tuple[int, int]], index: int) -> tuple[int, int] | None:
    for lo, hi in spans:
        if lo <= index < hi:
            return (lo, hi)
    return None


def _pick_peaks(windows: Sequence[WindowScore], min_separation: int) -> list[int]:
    """Local maxima of the score curve, as window indices, strongest first.

    Greedy non-maximum suppression rather than a strict local-maximum test, for
    the same reason as onset detection: a plateau of equal scores has no strict
    maximum, and this curve is full of plateaus because neighbouring windows
    share 75% of their frames.

    min_separation defaults to one window length, which is the answer to "two
    peaks 0.5 seconds apart -- one moment or two?". One. At a 0.5s hop they are
    adjacent windows sharing three quarters of their footage; they are the same
    event measured twice.

    Ties break on the earliest window. Deterministic, and it biases the moment
    toward the start of an action, where the interesting part usually is.
    """
    order = sorted(
        (i for i, w in enumerate(windows) if not w.eliminated),
        key=lambda i: (-windows[i].value, windows[i].start),
    )
    accepted: list[int] = []
    for index in order:
        start = windows[index].start
        if all(abs(start - windows[kept].start) >= min_separation for kept in accepted):
            accepted.append(index)
    return accepted


def _grow(
    stream: FeatureStream,
    windows: Sequence[WindowScore],
    peak: int,
    regions: Sequence[tuple[int, int]],
    shots: Sequence[Shot],
    points: Sequence[SnapPoint],
    params: MomentParams,
    weights: MomentWeights,
    min_frames: int,
    max_frames: int,
    thresholds: EliminationThresholds,
) -> Moment | None:
    """Turn one peak window into a snapped, bounded, re-scored moment."""
    window = windows[peak]
    centre = (window.start + window.end) // 2

    region = _containing(regions, centre)
    if region is None:
        return None
    lo, hi = region
    if shots:
        shot = _shot_at(shots, centre)
        if shot is None:
            return None
        # A moment never crosses a cut. Clipping to the shot here rather than
        # checking afterwards means growth cannot even propose a range that
        # spans one.
        lo, hi = max(lo, shot.start), min(hi, shot.end)
        shot_id: str | None = shot.shot_id
    else:
        shot_id = None

    start = max(lo, window.start)
    stop = min(hi, window.end)
    if stop - start <= 0:
        return None

    left = right = peak
    threshold = window.value * params.growth_ratio
    while True:
        options: list[tuple[float, int, str, int, int]] = []
        if left - 1 >= 0:
            candidate = windows[left - 1]
            new_start = max(lo, candidate.start)
            if (not candidate.eliminated and candidate.value >= threshold
                    and new_start < start and stop - new_start <= max_frames):
                options.append((candidate.value, 0, "l", new_start, stop))
        if right + 1 < len(windows):
            candidate = windows[right + 1]
            new_stop = min(hi, candidate.end)
            if (not candidate.eliminated and candidate.value >= threshold
                    and new_stop > stop and new_stop - start <= max_frames):
                options.append((candidate.value, 1, "r", start, new_stop))
        if not options:
            break
        # Strongest side first; ties go left (index 0 sorts before 1), so the
        # moment centres on the strongest available footage deterministically.
        _, _, side, start, stop = max(options, key=lambda row: (row[0], -row[1]))
        if side == "l":
            left -= 1
        else:
            right += 1

    start, stop = _snap_bounds(start, stop, lo, hi, points, stream.words, params,
                               stream.rate, min_frames)
    if stop - start < min_frames or stop - start > max_frames:
        return None

    features = aggregate(stream, start, stop)
    reasons = window_reasons(
        features,
        [frame_reasons(f, thresholds) for f in stream.span(start, stop)],
        thresholds,
    )
    if reasons:
        # Growth diluted the moment into junk. It does not get emitted as a
        # weak candidate: elimination is not scoring.
        return None
    values = features.as_values()
    applicable = applicability(stream, features)
    value, coverage, contributions = _weighted(values, weights.as_map(), applicable)
    if value < params.score_floor or coverage <= 0.0:
        return None

    hook_stop = min(stop, start + _frames_for(params.hook_seconds, stream.rate))
    hook_features = aggregate(stream, start, hook_stop)
    hook_value, hook_coverage, _ = _weighted(
        hook_features.as_values(), HOOK_WEIGHTS, applicability(stream, hook_features)
    )
    technical_value, technical_coverage, _ = _weighted(values, TECHNICAL_WEIGHTS, applicable)
    emotional_value, emotional_coverage, _ = _weighted(values, EMOTIONAL_WEIGHTS, applicable)

    inside = tuple(p for p in points if start <= p.time <= stop)
    words = tuple(w for w in stream.words if w.start < stop and w.end > start)

    return Moment(
        media_id=stream.media_id,
        rate=stream.rate,
        start=start,
        end=stop,
        eliminated=False,
        reasons=(),
        stage=None,
        features=features,
        value=value,
        coverage=coverage,
        contributions=contributions,
        technical=technical_value if technical_coverage > 0.0 else None,
        hook_potential=hook_value if hook_coverage > 0.0 else None,
        emotional_peak=emotional_value if emotional_coverage > 0.0 else None,
        snap_points=inside,
        safe_trim=_safe_trim(stream, start, stop, words, params, min_frames),
        transcript=_transcript(stream, words),
        shot_id=shot_id,
        weights_id=weights.weights_id,
        weights_digest=weights.digest(),
        _audio_events=tuple(
            e for e in stream.audio_events
            if start <= e.index < stop and stream.audio_state is AudioState.MEASURED
        ),
    )


def _shot_at(shots: Sequence[Shot], index: int) -> Shot | None:
    for shot in shots:
        if shot.start <= index < shot.end:
            return shot
    return None


def _snap_bounds(
    start: int,
    stop: int,
    lo: int,
    hi: int,
    points: Sequence[SnapPoint],
    words: Sequence[SpeechWord],
    params: MomentParams,
    rate: float,
    min_frames: int,
) -> tuple[int, int]:
    """Move both boundaries onto certified cut positions.

    In-point first, then out-point against the already-moved in-point, so the
    minimum-duration constraint is evaluated once against a settled value
    rather than twice against a moving one.

    Selection prefers STRENGTH over proximity, because the schema's own rule is
    that cutting on a weak onset is worse than cutting 40ms later on a strong
    one -- and the tolerance is what bounds "later". Strengths are compared
    quantised so float noise cannot reorder two equally strong candidates.
    """
    tolerance = _frames_for(params.snap_tolerance_seconds, rate)

    snapped_in = _nearest(points, "in", start, lo, stop - min_frames, tolerance)
    if snapped_in is not None:
        start = snapped_in
    else:
        safe = _word_safe(float(start), words, direction="in")
        candidate = int(math.floor(safe))
        if lo <= candidate <= stop - min_frames:
            start = candidate

    snapped_out = _nearest(points, "out", stop, start + min_frames, hi, tolerance)
    if snapped_out is not None:
        stop = snapped_out
    else:
        safe = _word_safe(float(stop), words, direction="out")
        candidate = int(math.ceil(safe))
        if start + min_frames <= candidate <= hi:
            stop = candidate
    return start, stop


def _nearest(
    points: Sequence[SnapPoint],
    direction: str,
    target: int,
    lower: int,
    upper: int,
    tolerance: int,
) -> int | None:
    """The best snap position for one boundary, or None if there is none.

    Candidate positions are floored for an in-point and ceiled for an out-point
    -- always outward -- so a fractional speech-derived position can only ever
    widen the moment, never clip into the content the point was protecting.
    """
    if lower > upper:
        return None
    best: tuple[float, int, int] | None = None
    for point in points:
        if not point.allows(direction):
            continue
        if abs(point.time - target) > tolerance:
            continue
        position = int(math.floor(point.time)) if direction == "in" else int(math.ceil(point.time))
        if not lower <= position <= upper:
            continue
        key = (-_quantise(point.strength), abs(position - target), position)
        if best is None or key < best:
            best = key
            chosen = position
    return chosen if best is not None else None


def _safe_trim(
    stream: FeatureStream,
    start: int,
    stop: int,
    words: Sequence[SpeechWord],
    params: MomentParams,
    min_frames: int,
) -> dict:
    """SafeTrim: the bounds a planner may trim to without damaging the moment.

    INTERPRETATION NOTE, because the schema's prose and its role disagree and
    this is the honest place to record it. SafeTrim is described as "hard
    bounds on trimming", and `speech_safe_in` is described as "earliest in-point
    that does not land inside a spoken word" -- read literally that is the
    moment's own start, since this file guarantees the start is never inside a
    word, which makes the field carry no information. The reading implemented
    here is the one that makes all four fields coherent: earliest_in/latest_out
    are the OUTER bounds, and speech_safe_in/speech_safe_out are the INNER ones
    imposed by speech, so a planner may trim the in-point anywhere in
    [earliest_in, speech_safe_in] and the out-point anywhere in
    [speech_safe_out, latest_out] without clipping a word. Flagged for the
    contract review rather than silently picked.

    `preserve_audio_tail` is set when speech or a salient audio event begins
    within the tail window AFTER the visual out-point -- a laugh that lands
    after the cut. The renderer honours it with an audio-only extension, and
    that decision has to live in the plan because the renderer is dumb.
    """
    trim: dict = {
        "earliest_in": _rational(start, stream.rate),
        "latest_out": _rational(stop, stream.rate),
        "min_duration": _rational(min_frames, stream.rate),
    }
    if words:
        ordered = sorted(words, key=lambda w: (w.start, w.end, w.word))
        safe_in = min(max(math.floor(ordered[0].start), start), stop)
        safe_out = max(min(math.ceil(max(w.end for w in ordered)), stop), start)
        trim["speech_safe_in"] = _rational(safe_in, stream.rate)
        trim["speech_safe_out"] = _rational(safe_out, stream.rate)

    tail = _frames_for(params.audio_tail_seconds, stream.rate)
    continues = any(stop <= w.start < stop + tail for w in stream.words)
    if not continues and stream.audio_state is AudioState.MEASURED:
        continues = any(
            stop <= e.index < stop + tail
            and e.confidence * EMOTIONAL_EVENT_WEIGHT[e.label] >= AUDIO_TAIL_SALIENCE
            for e in stream.audio_events
        )
    if continues:
        trim["preserve_audio_tail"] = True
    return trim


def _transcript(stream: FeatureStream, words: Sequence[SpeechWord]) -> dict | None:
    if not words:
        return None
    ordered = sorted(words, key=lambda w: (w.start, w.end, w.word))
    segment: dict = {
        "text": " ".join(w.word for w in ordered),
        "language": stream.language,
        "words": [
            {
                "word": w.word,
                "start": _rational(w.start, stream.rate),
                "end": _rational(w.end, stream.rate),
                **({"confidence": w.confidence} if w.confidence is not None else {}),
            }
            for w in ordered
        ],
    }
    return segment


def _eliminated_runs(
    stream: FeatureStream,
    windows: Sequence[WindowScore],
    weights: MomentWeights,
) -> tuple[Moment, ...]:
    """Merged spans of eliminated windows, as eliminated MomentRecords.

    One record per maximal run rather than per window: a 40-minute pocket-shot
    stretch is one fact about the footage, not 4800 of them, and the culling UI
    ("your 40 usable minutes") wants the fact.

    These records carry NO features block. An eliminated record exists to say
    "this footage is junk and here is why"; attaching measurements invites a
    downstream consumer to score it and let it compete, which is precisely what
    elimination-first exists to prevent. Their moment_score is 0.0 for the same
    reason the ranking engine's rejected FusedScore is: a caller that ignores
    the elimination block sorts them last instead of raising.
    """
    runs: list[Moment] = []
    current_start: int | None = None
    current_stop = 0
    current_reasons: list[str] = []
    current_stage: str | None = None

    def flush() -> None:
        nonlocal current_start, current_reasons, current_stage
        if current_start is None:
            return
        ordered = [r for r in WINDOW_REASON_ORDER if r in current_reasons]
        ordered += sorted(r for r in set(current_reasons) if r not in WINDOW_REASON_ORDER)
        runs.append(Moment(
            media_id=stream.media_id, rate=stream.rate,
            start=current_start, end=current_stop,
            eliminated=True, reasons=tuple(ordered), stage=current_stage,
            weights_id=weights.weights_id, weights_digest=weights.digest(),
        ))
        current_start = None
        current_reasons = []
        current_stage = None

    for window in windows:
        if not window.eliminated:
            flush()
            continue
        if current_start is None or window.start > current_stop:
            flush()
            current_start = window.start
            current_stop = window.end
            current_reasons = list(window.reasons)
            current_stage = window.stage
        else:
            current_stop = max(current_stop, window.end)
            current_reasons.extend(window.reasons)
            # "classical" beats "fusion": the run was junk for a free reason
            # somewhere in it, which is the more useful thing to report.
            if window.stage == "classical":
                current_stage = "classical"
    flush()
    return tuple(runs)


def explain(moment: Moment, *, limit: int = 3) -> str:
    """One human-readable sentence, for the UI and for debugging.

    The contract's promise that "why is this 0.82" stays answerable is only
    kept if something renders the answer.
    """
    if moment.eliminated:
        return f"eliminated: {', '.join(moment.reasons) or 'unspecified'}"
    if not moment.contributions:
        return "not yet measured"
    ranked = sorted(
        moment.contributions, key=lambda row: (-(row[1] * row[2]), row[0])
    )[:limit]
    parts = ", ".join(f"{name} {value:.2f}" for name, value, _ in ranked)
    return (
        f"{moment.value:.2f} over {moment.duration_seconds():.1f}s from {parts} "
        f"at {moment.coverage:.0%} coverage"
    )
