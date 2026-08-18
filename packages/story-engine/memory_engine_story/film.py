"""Film planner: selected moments + a three-act arc -> one EDL of kind "film".

Build plan §4.5 and phase 5. This is the sibling of `reel.py`, and everything
interesting about it is a place where a film is NOT a long reel.

A REEL IS A RHYTHM. A FILM IS A NARRATIVE.

  * A reel is 15-30 seconds cut to a beat grid for a scroll. Its structure is
    hook -> build -> peak -> button, and the strongest shot is moved to the
    front because the first second has to earn the rest.
  * A film runs for minutes, is watched deliberately, and runs in the order
    things happened. Nothing is moved to the front. The three acts are imposed
    ON the chronology (setup / development / resolution); they do not reorder
    it. A film that opens on its best shot has spent its ending.

FOUR DECISIONS, AND WHAT THE ALTERNATIVES LOOK LIKE

1. PACING VARIES, AND THE VARIATION IS MEASURED RATHER THAN ASSERTED.
   A film cut at reel density is exhausting; a film cut at constant density is
   inert. So a shot's hold comes from two independent sources:

     * its ACT. Setup and resolution breathe (a longer base hold), development
       moves (a shorter one). Films open slow, run, and land slow.
     * its CONTENT. A busy shot is held SHORTER than a quiet one, because the
       eye has already read it. `Pacing.quiet_scale` and `Pacing.busy_scale`
       bound how far content is allowed to move a hold from its act's base.

   An UNMEASURED energy earns the neutral scale 1.0 -- not the quiet one. The
   quiet scale would hand the longest holds in the film to whichever moments
   the motion pass had not reached, which is the same mistake the reel planner
   calls out for its button selection and the ranking engine calls out for
   missing signals.

   The claim "pacing varies" is then CHECKABLE: `FilmPlan.shot_frames` carries
   every realised hold and `FilmPlan.pacing_spread` is (max - min) / mean over
   them. A plan whose spread is 0.0 says so in its notes. It is not in the EDL
   because `EdlValidation.checks` is a closed enum and `EDL` is
   additionalProperties:false -- inventing a field or a check id there would
   fail the contract, and inventing a *number* would be worse.

   `FilmPlan.window_limited` is the honest counterweight: a hold clamped by the
   moment's own trimmable window was decided by the analysis layer, not by the
   pacing policy, and a film where every shot is window-limited has no pacing
   design in it at all however varied its shot lengths look.

2. SPEECH OUTRANKS PACING, AND THE OUT-POINT IS TRIMMED FOR IT.
   `reel.py` chooses a duration from the beat grid and then CHECKS for a
   mid-word cut, reporting a warning. That is right for a reel, where the
   music decides and speech is incidental. It is not enough for a film, where
   the words are the point:

     * the in-point prefers a `speech_gap` or `speech_start` snap over a
       motion onset. The reel wants the cut to land where the movement starts;
       the film wants it to land where the sentence starts. The preference is
       BOUNDED (`_IN_POINT_PENALTY`, at most 0.2 of strength) so a barely-there
       speech gap still loses to a decisive shot boundary;
     * the out-point is MOVED to clear a word -- extended to the end of the
       word when the trimmable window and the maximum hold allow it, pulled
       back to the start of the word when they do not. Finishing the sentence
       is the preferred direction, which is why extension is tried first;
     * `no_mid_word_cut` is emitted at severity ERROR here, against the reel's
       warning. Build plan §7: "Films: no mid-word cuts (word-timestamp
       verified)". A film that cuts through a word is not a film with a
       blemish, it is a broken deliverable.

3. WHAT CANNOT BE MEASURED IS NOT CLAIMED.
   There is no transcript backend in this repository. Everything above is
   therefore INERT on footage planned today, and the plan says so rather than
   implying otherwise:

     * `no_mid_word_cut` is emitted ONLY when at least one placed moment
       carries word timings. With no words there is no finding at all -- not a
       passing one. `FilmPlan.word_safe_certified` is False and the absence is
       the honest report. `services/pipeline`'s story stage already states this
       for the reel and states it for the film in the same words;
     * an L-CUT is planned only when WHOLE WORDS explain its length. `SafeTrim.
       preserve_audio_tail` is a boolean: it says the audio meaningfully
       continues, and says nothing about how far. With word timings the tail
       runs to the end of the last word that starts at the cut and finishes
       inside two measured bounds -- the frames the file has, and the shot the
       tail plays under. Without them the length would be a number this planner
       made up, so no tail is emitted and `FilmPlan.notes` records each moment
       whose tail was dropped for want of the timing that would have sized it.

4. THE FILM ANSWERS TO THE SENTENCE, NOT TO THE BAR.
   No cut here is beat-locked and no `beat_grid` is emitted, even when music is
   supplied. Beat-locking a three-second hold to a 128bpm grid moves the cut
   away from the speech boundary it was chosen for, to gain an alignment
   nobody watching a film perceives. Music under a film is a bed that ducks
   under the ambient, which is what `AudioPlan.ducking` is for.

WHAT THIS PLANNER REFUSES TO EMIT, AND WHY

  * A DISSOLVE, BY DEFAULT. `act_transition_frames` defaults to 0, so every
    cut is hard -- and in OTIO a hard cut is the ABSENCE of a Transition, never
    a zero-length one. The build plan asks for "gentle transitions" at act
    boundaries and this planner will place them; `workers/render-video` then
    refuses the plan, because the contract describes a transition as a picture
    operation and never says whether the ambient beds cross-fade with it or
    hard-cut at the cut point (contracts#52). Emitting one by default would
    mean shipping a film planner whose output nothing can render. So the
    capability is built, tested and OFF, the refusal is stated in the notes the
    moment a caller turns it on, and the day #52 closes the default moves.
  * A COLOUR GRADE or a SPEED RAMP, for the reasons `reel.py` states.
  * A CAPTURE-ORDER CHRONOLOGY IT WAS NOT GIVEN. Two moments from different
    files have no comparable source time, and `SelectedMoment` carries no
    capture timestamp. `FilmRequest.media_sequence` is how the caller supplies
    the real order; when it is absent the film runs in the order the sources
    were DECLARED in and a note says, in those words, that the inter-file order
    is declaration order and is not known to be chronological. Sorting by
    content hash and calling the result a story would be the exact defect this
    module exists to avoid -- and so, more quietly, is sorting by a slug while
    telling the caller it was their order (see `_media_rank`).

WHAT IS BORROWED FROM `reel.py`, AND WHY THAT IS NOT LAZINESS

The input vocabulary (`SelectedMoment`, `SourceMedia`, `SafeTrim`, `SnapPoint`,
`Word`, `MusicTrack`, `RenderTarget`, `AmbientSettings`, `ReframeSettings`),
the canonical-JSON and BLAKE3 implementations, the rounding rules and the crop
geometry are imported rather than re-written. Two reasons, both concrete:

  * `moment_from_record` produces exactly one type. A film planner with its own
    `SelectedMoment` would need its own adapter, and the two adapters would
    read the same contract record differently the first time either changed.
  * a second implementation of half-away-from-zero rounding, of RFC 8785
    number formatting, or of the 9:16-out-of-16:9 crop is how two planners come
    to disagree by a frame or by a pixel, silently, in the direction nobody
    tests. `test_film.py` asserts the crop tracks are identical to the reel's
    for identical geometry, so the sharing is checked rather than assumed.

The private names (`_rational`, `_round_half_up`, `_crop_size`,
`_reframe_track`) are imported deliberately. They are private to the PACKAGE,
not to the module; both planners are the same owner's code.

DETERMINISM

`plan_film` is a pure function of its request. No clock (`generated_at` and
`validated_at` are request fields), no randomness, every tie broken by a stated
rule ending in `moment_id`, every position an integer frame. Same request in,
byte-identical EDL out.

INTEGER FRAMES, EVERYWHERE

`workers/render-video` refuses a `source_range` that is not a whole frame at
the timeline rate, and it is right to: a fractional seek is a decision about
which frame, made by the decoder. So every in-point and every out-point this
planner emits is an integer. A snap point that falls between frames is rounded
towards the INSIDE of the shot -- `ceil` for an in-point, `floor` for an
out-point -- which is the only direction that cannot move a boundary back into
the word it was chosen to clear.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from .reel import (
    AmbientSettings,
    MusicLicense,
    MusicTrack,
    RateMismatch,
    ReframeSettings,
    RenderTarget,
    SafeTrim,
    SchemaVersionUnsupported,
    SelectedMoment,
    SnapPoint,
    SourceMedia,
    SubjectSample,
    VariantInfo,
    Word,
    blake3_hex,
    canonical_json,
    clearance_for_destination,
    moment_from_record,
)
from .reel import (
    _crop_size,
    _range,
    _rational,
    _reframe_track,
    _round_half_up,
    _round_unit,
)

__all__ = [
    "ACT_KEYS",
    "AmbientSettings",
    "FILM_ARC_TEMPLATES",
    "FilmPlan",
    "FilmRequest",
    "FilmTooShort",
    "MusicLicense",
    "MusicTrack",
    "PLANNER_ID",
    "PLANNER_VERSION",
    "Pacing",
    "RateMismatch",
    "ReframeSettings",
    "RenderTarget",
    "SafeTrim",
    "SchemaVersionUnsupported",
    "SelectedMoment",
    "SnapPoint",
    "SourceMedia",
    "SubjectSample",
    "VariantInfo",
    "Word",
    "moment_from_record",
    "pacing_spread",
    "plan_film",
]

PLANNER_ID = "film-planner"
PLANNER_VERSION = "1.0.0"

SCHEMA_VERSION = "v0"

# The contract's StoryArc.template values this planner can satisfy. One, for
# now: a film is three acts here. `montage` and `day_in_the_life` are real
# templates with different rules, and listing them before they are implemented
# would let a caller ask for one and silently receive a three-act cut.
FILM_ARC_TEMPLATES = ("three_act",)

# Fields removed before `edl_id` is computed, for the reasons EDL_ID_EXCLUDED
# states in `reel.py`: an id that moved when the clock ticked would not be the
# render-cache key it exists to be.
EDL_ID_EXCLUDED = (
    "edl_id",
    "determinism.generated_at",
    "validation.validated_at",
    "tracks[].items[].timeline_range",
    "variant.sibling_edl_ids",
)

_ACT_SETUP = "setup"
_ACT_DEVELOPMENT = "development"
_ACT_RESOLUTION = "resolution"
ACT_KEYS = (_ACT_SETUP, _ACT_DEVELOPMENT, _ACT_RESOLUTION)

# act key -> (act_id, name, intent, required beat id, beat description, target energy)
_ACT_SPECS: Mapping[str, tuple[str, str, str, str, str, float]] = {
    _ACT_SETUP: (
        "act-setup",
        "Setup",
        "Establish where we are and when this starts, at a pace that lets a "
        "viewer arrive.",
        "beat-establish",
        "Where we are, held long enough to be read rather than glimpsed.",
        0.35,
    ),
    _ACT_DEVELOPMENT: (
        "act-development",
        "Development",
        "What happened, in the order it happened.",
        "beat-development",
        "The middle of it: the shots that would be missed if they were cut.",
        0.70,
    ),
    _ACT_RESOLUTION: (
        "act-resolution",
        "Resolution",
        "Land somewhere quiet, so the film ends rather than stops.",
        "beat-close",
        "The last thing the film says.",
        0.30,
    ),
}

# The turn is emitted as an extra beat inside whichever act holds the strongest
# moment. Kept required: it exists only where it is satisfied, and a required
# flag on a beat that is always satisfied still carries information, because a
# future planner that emits the beat without placing its clip fails validation
# rather than shipping a film with no turn in it.
_TURN_BEAT_ID = "beat-turn"
_TURN_BEAT_DESCRIPTION = (
    "The single moment the film is about -- the one that could not be inferred "
    "from any other shot."
)
_TURN_ENERGY = 1.0

# How energy is fused for the pacing decision. Renormalised over what was
# actually measured, exactly as `moments.py` and the ranking engine's fusion
# do: a missing signal leaves the denominator rather than defaulting to a
# number nobody measured.
_ENERGY_WEIGHTS: Mapping[str, float] = {"motion_energy": 0.6, "emotional_peak": 0.4}

# How much a snap point's kind is allowed to move it in the in-point contest,
# in units of `strength`. A film cuts on sentence and shot boundaries; a reel
# cuts on motion onsets. The penalty is bounded so the reel planner's own rule
# still holds inside a kind -- "cutting on a weak onset is worse than cutting
# 40ms later on a strong one" (moment-record.schema.json#SnapPoint).
_IN_POINT_PENALTY: Mapping[str, float] = {
    "speech_gap": 0.00,
    "speech_start": 0.00,
    "shot_boundary": 0.05,
    "speech_end": 0.05,
    "subject_entry": 0.10,
    "motion_onset": 0.15,
    "motion_offset": 0.15,
    "audio_onset": 0.15,
    "impact": 0.20,
    "scene_brightness_change": 0.20,
    "subject_exit": 0.20,
}
_DEFAULT_IN_POINT_PENALTY = 0.20

_SNAP_IN_DIRECTIONS = frozenset({"in", "both"})

_HEX_CHARS = set("0123456789abcdef")

# Word timings are real-valued (whisper gives seconds), so a boundary that is
# conceptually exactly on a word edge can differ in the last bit. Anything
# smaller than this is the same instant.
_EPS = 1e-9


class FilmTooShort(ValueError):
    """Fewer usable moments than an arc needs.

    Deliberately fatal rather than degraded. A three-act film needs a shot in
    each act; below that the honest answer is "there is no film in this
    footage", and a two-shot artifact labelled `kind: "film"` is precisely the
    relabelling this module was written to avoid.
    """


# --------------------------------------------------------------------------
# Policy
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Pacing:
    """How long a shot is held, as data.

    Build plan §4.5 asks for "longer holds (3-8s)". The act bases sit inside
    that; `min_hold_seconds` is BELOW it on purpose, as the floor at which a
    shot stops reading as a hold at all. A moment whose trimmable window cannot
    afford the floor is not film material and is dropped with a note, rather
    than emitted as a 0.4-second flash inside a film.
    """

    setup_hold_seconds: float = 5.0
    development_hold_seconds: float = 4.0
    resolution_hold_seconds: float = 5.5

    min_hold_seconds: float = 1.5
    max_hold_seconds: float = 8.0

    # Content modulation. 1.0 is neutral; > 1 lengthens, < 1 shortens.
    # `quiet_scale == busy_scale` turns content modulation off, and a caller
    # who does that gets a film paced only by its acts -- which is a legitimate
    # choice and a visible one, because `pacing_spread` drops.
    quiet_scale: float = 1.30
    busy_scale: float = 0.70

    def validate(self) -> None:
        for name in (
            "setup_hold_seconds",
            "development_hold_seconds",
            "resolution_hold_seconds",
            "min_hold_seconds",
            "max_hold_seconds",
            "quiet_scale",
            "busy_scale",
        ):
            value = float(getattr(self, name))
            if not math.isfinite(value) or value <= 0.0:
                raise ValueError(f"Pacing.{name} must be a finite number > 0, got {value!r}")
        if self.min_hold_seconds > self.max_hold_seconds:
            raise ValueError(
                f"Pacing.min_hold_seconds {self.min_hold_seconds} exceeds "
                f"max_hold_seconds {self.max_hold_seconds}: no shot could satisfy "
                "both bounds, so every moment would be dropped while the planner "
                "looked healthy"
            )
        for name in ("setup", "development", "resolution"):
            base = float(getattr(self, f"{name}_hold_seconds"))
            if not self.min_hold_seconds <= base <= self.max_hold_seconds:
                raise ValueError(
                    f"Pacing.{name}_hold_seconds {base} is outside "
                    f"[{self.min_hold_seconds}, {self.max_hold_seconds}]; an act "
                    "base that is already out of bounds means the clamp, not the "
                    "policy, decides that act's pace"
                )
        # THE NEUTRAL SCALE HAS TO BE INSIDE THE BAND IT IS NEUTRAL WITHIN.
        #
        # `scale(None)` returns 1.0 so that a moment nobody measured is neither
        # lengthened nor shortened -- see its docstring, and the module header:
        # "the quiet scale would hand the LONGEST holds in the film to whichever
        # moments the motion pass had not reached". That argument only holds
        # while 1.0 sits between the two scales. A caller who tightens the whole
        # film (quiet 0.90, busy 0.60) or slackens it (quiet 1.80, busy 1.20)
        # moves the band off 1.0, and then the unmeasured moments get a hold no
        # MEASURED energy could ever produce: the longest shots in the film in
        # the first case, the shortest in the second. Both were accepted before
        # this check, silently, and both are the precise failure the neutral
        # scale exists to prevent.
        low = min(self.quiet_scale, self.busy_scale)
        high = max(self.quiet_scale, self.busy_scale)
        if not low <= 1.0 <= high:
            raise ValueError(
                f"Pacing scales [{low:g}, {high:g}] do not span 1.0, which is the "
                "scale an UNMEASURED energy gets. A moment the motion pass never "
                "reached would then be held longer or shorter than any measured "
                "moment could be -- the film's pacing decided by what analysis "
                "missed. Keep quiet_scale >= 1 >= busy_scale, or move both bases "
                "instead of the scales"
            )

    def base_seconds(self, act: str) -> float:
        return {
            _ACT_SETUP: self.setup_hold_seconds,
            _ACT_DEVELOPMENT: self.development_hold_seconds,
            _ACT_RESOLUTION: self.resolution_hold_seconds,
        }[act]

    def scale(self, energy: float | None) -> float:
        """Content scale for a hold. Unmeasured energy is NEUTRAL, not quiet."""
        if energy is None:
            return 1.0
        clamped = 0.0 if energy < 0.0 else (1.0 if energy > 1.0 else float(energy))
        return _round_unit(
            self.quiet_scale + (self.busy_scale - self.quiet_scale) * clamped
        )


@dataclass(frozen=True)
class ActShape:
    """What fraction of the film each of the outer acts takes.

    Development is the remainder, because it is the act that absorbs whatever
    the film turns out to be: a trip with forty moments and one with four
    should both open and close in proportion, not both open on one shot.
    """

    setup_fraction: float = 0.25
    resolution_fraction: float = 0.20

    def validate(self) -> None:
        for name in ("setup_fraction", "resolution_fraction"):
            value = float(getattr(self, name))
            if not math.isfinite(value) or not 0.0 < value < 1.0:
                raise ValueError(f"ActShape.{name} must be within (0,1), got {value!r}")
        if self.setup_fraction + self.resolution_fraction >= 1.0:
            raise ValueError(
                "ActShape leaves no room for development: setup_fraction + "
                "resolution_fraction must be < 1"
            )


# --------------------------------------------------------------------------
# Request and result
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class FilmRequest:
    """Everything the planner reads. Its canonical JSON is the inputs_digest."""

    rate: float
    target: RenderTarget
    media: tuple[SourceMedia, ...]
    moments: tuple[SelectedMoment, ...]
    music: MusicTrack | None = None
    arc_template: str = "three_act"
    pacing: Pacing = field(default_factory=Pacing)
    act_shape: ActShape = field(default_factory=ActShape)
    # media_ids in CAPTURE order. Empty means the caller does not know, and the
    # film then runs in media_ref_id order with a note saying exactly that.
    media_sequence: tuple[str, ...] = ()
    name: str = ""
    title: str | None = None
    logline: str | None = None
    rationale: str | None = None
    seed: int = 0
    planner_version: str = PLANNER_VERSION
    generated_at: str | None = None
    validated_at: str | None = None
    ambient: AmbientSettings = field(default_factory=AmbientSettings)
    # Films pan slowly. 0.35/second is a reel gesture; over a five-second hold
    # it crosses a third of the frame and reads as a camera move that never
    # happened.
    reframe: ReframeSettings = field(
        default_factory=lambda: ReframeSettings(max_velocity_per_second=0.12)
    )
    # 0 means every cut is hard. See the module docstring: dissolves are built
    # and off, because render-video refuses them under ambient audio
    # (contracts#52).
    act_transition_frames: int = 0
    # An explicit hold of black at the end. `Gap` exists in the contract with
    # exactly this example in its description; 0 means the film ends on its
    # last frame of picture.
    end_hold_frames: int = 0
    # A film is minutes long. Below this the planner still emits the film and
    # says how short it came in, because "the library only had this much" is a
    # fact about the library, not a planner failure.
    min_film_seconds: float = 60.0
    ducking_reduction_db: float = 9.0
    variant: VariantInfo | None = None

    def __post_init__(self) -> None:
        if self.rate <= 0 or not math.isfinite(self.rate):
            raise ValueError(f"FilmRequest.rate must be > 0, got {self.rate}")
        if not self.media:
            raise ValueError("FilmRequest.media must not be empty")
        if not self.moments:
            raise ValueError("FilmRequest.moments must not be empty")
        if self.arc_template not in FILM_ARC_TEMPLATES:
            raise ValueError(
                f"unsupported arc_template {self.arc_template!r}; supported: "
                f"{FILM_ARC_TEMPLATES}"
            )
        if self.act_transition_frames < 0:
            raise ValueError("FilmRequest.act_transition_frames must be >= 0")
        if self.end_hold_frames < 0:
            raise ValueError("FilmRequest.end_hold_frames must be >= 0")
        if self.seed < 0:
            raise ValueError("FilmRequest.seed must be >= 0")
        if not math.isfinite(self.min_film_seconds) or self.min_film_seconds < 0:
            raise ValueError("FilmRequest.min_film_seconds must be finite and >= 0")
        if not math.isfinite(self.ducking_reduction_db):
            raise ValueError("FilmRequest.ducking_reduction_db must be finite")
        self.pacing.validate()
        self.act_shape.validate()
        self._validate_ambient()

        seen_ref: set[str] = set()
        seen_media: set[str] = set()
        for medium in self.media:
            if medium.media_ref_id in seen_ref:
                raise ValueError(f"duplicate media_ref_id {medium.media_ref_id!r}")
            if medium.media_id in seen_media:
                raise ValueError(f"duplicate media_id {medium.media_id!r}")
            seen_ref.add(medium.media_ref_id)
            seen_media.add(medium.media_id)

        seen_moment: set[str] = set()
        for moment in self.moments:
            if moment.moment_id in seen_moment:
                raise ValueError(f"duplicate moment_id {moment.moment_id!r}")
            seen_moment.add(moment.moment_id)
            if moment.media_id not in seen_media:
                raise ValueError(
                    f"moment {moment.moment_id} references media_id "
                    f"{moment.media_id}, which is not in FilmRequest.media"
                )
            moment.window()  # raises if the speech-safe window is empty

        # A partial sequence is worse than none: it would order some files by
        # capture time and the rest by hash, and the join between the two would
        # look like a chronology.
        if self.media_sequence:
            listed = list(self.media_sequence)
            if len(set(listed)) != len(listed):
                raise ValueError("FilmRequest.media_sequence repeats a media_id")
            unknown = [mid for mid in listed if mid not in seen_media]
            if unknown:
                raise ValueError(
                    f"FilmRequest.media_sequence names media that is not declared: "
                    f"{', '.join(sorted(unknown))}"
                )
            missing = sorted(seen_media - set(listed))
            if missing:
                raise ValueError(
                    "FilmRequest.media_sequence must order EVERY declared source "
                    f"or none of them; it omits {', '.join(missing)}. A partial "
                    "order would put some files in capture order and the rest in "
                    "hash order, and the seam would read as chronology"
                )

        if self.music is not None:
            if self.music.media.media_ref_id not in seen_ref:
                raise ValueError(
                    "FilmRequest.music.media must also appear in FilmRequest.media, "
                    "so the EDL declares every source once"
                )
            music_media = self.music.media
            usable = music_media.available_end - self.music.source_start
            if self.music.source_start < music_media.available_start or usable < 1:
                raise ValueError(
                    f"music cue starts at {self.music.source_start}, outside the "
                    f"usable part of its track [{music_media.available_start}, "
                    f"{music_media.available_end})"
                )

    def _validate_ambient(self) -> None:
        """An ambient policy must not do the opposite of what its flags say.

        `AmbientSettings` carries three levels for one bed -- the default, the
        one used where somebody is speaking, and the one used where the mic was
        full of wind -- and NOTHING in the type relates them. Its own defaults
        (-14 default, -6 speech, -60 wind) are coherent: speech comes UP out of
        a ducked bed and wind goes DOWN out of an open one. A caller who moves
        the default without moving the other two inverts both meanings, and
        every symptom of that is a mix nobody notices until they listen:
        `preserve_speech: true` written into the plan beside a per-clip gain
        that makes exactly the speaking shots the quietest in the film.

        This is not hypothetical. `services/pipeline` opened the bed to unity
        (0 dB) for a film with no music under it and left `speech_gain_db` at
        -6, so every shot with speech in it was planned 6 dB DOWN from every
        shot without. It never fired only because nothing in that pipeline
        measures speech yet -- it was waiting for the transcript backend to
        arrive and make the film's speech the hardest thing in it to hear.

        A film is the output where this matters most, so it is fatal here
        rather than a note. The reel planner accepts the same settings type and
        does not check this; see docs/architecture.md.
        """
        ambient = self.ambient
        if not ambient.enabled:
            return
        if ambient.preserve_speech and ambient.speech_gain_db < ambient.default_gain_db:
            raise ValueError(
                f"AmbientSettings.preserve_speech is on but speech_gain_db "
                f"({ambient.speech_gain_db:g} dB) is BELOW default_gain_db "
                f"({ambient.default_gain_db:g} dB), so every shot with speech in it "
                "would be planned quieter than every shot without. Raise "
                "speech_gain_db to at least the default, or turn preserve_speech "
                "off and say the bed is flat"
            )
        if ambient.wind_gain_db > ambient.default_gain_db:
            raise ValueError(
                f"AmbientSettings.wind_gain_db ({ambient.wind_gain_db:g} dB) is ABOVE "
                f"default_gain_db ({ambient.default_gain_db:g} dB), so the shots the "
                "noise measure flagged as wind would be the loudest in the film. The "
                "wind level exists to pull those shots down"
            )


@dataclass(frozen=True)
class FilmPlan:
    """The EDL, plus what the planner had to give up and what it measured.

    Nothing here is in the EDL, and none of it could be: `EDL` is
    additionalProperties:false and `EdlValidation.checks` is a closed enum. So
    the pacing evidence lives on the plan object, where the caller reads it and
    the story stage prints it.
    """

    edl: dict[str, Any]
    notes: tuple[str, ...] = ()
    duration_frames: int = 0
    # Realised hold of every placed clip, in timeline order.
    shot_frames: tuple[int, ...] = ()
    # Clips whose hold was decided by the moment's trimmable window rather than
    # by the pacing policy.
    window_limited: int = 0
    l_cuts: int = 0
    # False when no placed moment carried word timings, which is the only state
    # in which "no mid-word cuts" is unproven rather than proven.
    word_safe_certified: bool = False

    @property
    def status(self) -> str:
        return str(self.edl["validation"]["status"])

    @property
    def edl_id(self) -> str:
        return str(self.edl["edl_id"])

    @property
    def pacing_spread(self) -> float:
        """(max - min) / mean over the realised holds. 0.0 means constant density."""
        return pacing_spread(self.shot_frames)


def pacing_spread(shot_frames: Sequence[int]) -> float:
    """How far the realised holds range, relative to their mean.

    The one number behind "pacing varies". Zero means every shot is the same
    length, which is the failure mode a film planner has to be able to detect
    in its own output rather than assume away.
    """
    if not shot_frames:
        return 0.0
    mean = sum(shot_frames) / len(shot_frames)
    if mean <= 0:  # pragma: no cover - a zero-length hold cannot be placed
        return 0.0
    return _round_unit((max(shot_frames) - min(shot_frames)) / mean)


# --------------------------------------------------------------------------
# Chronology and acts
# --------------------------------------------------------------------------


def _media_rank(request: FilmRequest) -> dict[str, int]:
    """Position of each source in the film's running order.

    `media_sequence` when the caller supplied it; otherwise the order the
    sources were DECLARED in, which is not capture order. The caller is told
    which of the two it got.

    This used to sort by `media_ref_id` while every note, docstring and count
    in the film path called the result "declaration order". Those are the same
    order only while the ref ids happen to sort the way the tuple was built --
    which is true of `services/pipeline` (it names them `src-000`, `src-001`,
    ...) and is why the discrepancy never showed. For any other caller the film
    ran in alphabetical order of a slug while telling them it ran in theirs,
    and reordering `FilmRequest.media` to fix a running order did nothing.

    Declaration order is the honest reading and it is no less deterministic:
    `FilmRequest.media` is an ordered tuple and is digested as one, so the same
    request still produces the same film. `media_sequence` remains the only way
    to assert a real chronology, and it is validated to cover every source.
    """
    if request.media_sequence:
        return {media_id: index for index, media_id in enumerate(request.media_sequence)}
    return {medium.media_id: index for index, medium in enumerate(request.media)}


def _chronological(
    moments: Iterable[SelectedMoment], rank: Mapping[str, int]
) -> list[SelectedMoment]:
    """Running order: by source, then by source time, then by id.

    Source time is only comparable WITHIN one file, which is why the source
    rank leads. `moment_id` last so the order is total and the same pool always
    produces the same film.
    """
    return sorted(
        moments,
        key=lambda m: (rank[m.media_id], m.source_start, m.moment_id),
    )


def _turn_index(ordered: Sequence[SelectedMoment]) -> tuple[int, str]:
    """Position of the strongest moment -- the turn -- and the axis that chose it.

    THE CONTEST NEEDS ONE AXIS EVERY CANDIDATE WAS MEASURED ON.

    Emotional peak is the right signal for "the moment the film is about", and
    it leads WHENEVER THE WHOLE POOL CARRIES ONE. When any candidate's peak is
    unmeasured the axis is dropped for every candidate and the fused `score`
    decides instead, because a pool where some peaks are measured and some are
    not is not a ranking -- it is two rankings glued together.

    This used to read a missing peak as 0.0, and that is a defect the rest of
    this module is written against: `_energy` renormalises over what was
    measured rather than defaulting, and the docstring calls reading a missing
    signal as a low one "the same mistake the reel planner calls out for its
    button selection". Under the old rule a moment whose peak nobody measured
    could not be the turn however strong it was, so a MEASURED peak of 0.01
    beat an UNMEASURED moment scoring 0.99 -- and the film was "about" its
    weakest shot. The tests could not see it because every one of them gave the
    intended winner the only measured peak in the pool.

    Ties then go to the fused score, and last to the LOWEST moment_id -- stated
    rather than left to the sort, because `max` keeps the first of equal keys
    and the running order is not the id order.

    Returns (index, axis) where axis is "emotional_peak" or "score", so the
    caller can say which one decided rather than leaving it to be inferred.
    """
    axis = (
        "emotional_peak"
        if all(moment.emotional_peak is not None for moment in ordered)
        else "score"
    )

    def key(moment: SelectedMoment) -> tuple[float, float, tuple[int, ...]]:
        peak = (
            float(moment.emotional_peak)  # type: ignore[arg-type]
            if axis == "emotional_peak"
            else 0.0
        )
        return (peak, moment.score, _negated_id(moment))

    best = 0
    for index in range(1, len(ordered)):
        if key(ordered[index]) > key(ordered[best]):
            best = index
    return best, axis


def _negated_id(moment: SelectedMoment) -> tuple[int, ...]:
    """Sort key making a `max`-style contest prefer the LOWEST moment_id."""
    return tuple(-b for b in moment.moment_id.encode("ascii"))


def _act_boundaries(
    count: int, turn: int, shape: ActShape
) -> tuple[int, int]:
    """(setup_end, resolution_start) as indices into the running order.

    Act I is [0, setup_end), act II is [setup_end, resolution_start), act III
    is [resolution_start, count). All three are non-empty, which is what makes
    the three required story beats satisfiable.

    The boundaries are then nudged so the TURN falls in act II wherever that is
    possible. It is not possible when the strongest moment is the very first or
    the very last thing that happened -- act I is a prefix and act III a
    suffix, so a turn at either end belongs to the act that contains it. That
    is a fact about the footage, not a defect, and `plan_film` notes it.
    """
    if count < 3:  # pragma: no cover - `plan_film` refuses earlier and louder
        raise FilmTooShort("a three-act film needs at least three moments")
    # CLAMPED ONCE, BEFORE THE NUDGES, AND THE NUDGES PRESERVE THE INVARIANT.
    #
    # This used to clamp again afterwards. Mutation testing showed the second
    # clamp made the first one unobservable and vice versa: three separate
    # mutants that broke a clamp were repaired by its twin and survived the
    # suite. Two guards that cover for each other are one guard nobody can
    # test, so there is now one -- and the argument that the nudges cannot
    # break it is written down instead of re-asserted in code:
    #
    #   * `setup_end` only ever moves DOWN, to `turn`, and only when
    #     `turn >= 1`, so act I keeps at least one shot;
    #   * `resolution_start` only ever moves UP, to `turn + 1`, and only when
    #     `turn <= count - 2`, so act III keeps at least one shot;
    #   * a nudge that moves `setup_end` down sets it to `turn`, which is
    #     strictly below `resolution_start` (the nudge fires only when
    #     `turn < setup_end < resolution_start`); a nudge that moves
    #     `resolution_start` up sets it to `turn + 1 > turn >= setup_end`.
    #     Either way act II is non-empty.
    setup_end = _clamp_int(_round_half_up(count * shape.setup_fraction), 1, count - 2)
    resolution_start = _clamp_int(
        count - max(1, _round_half_up(count * shape.resolution_fraction)),
        setup_end + 1,
        count - 1,
    )
    if 1 <= turn < setup_end:
        setup_end = turn
    if resolution_start <= turn <= count - 2:
        resolution_start = turn + 1
    return setup_end, resolution_start


def _clamp_int(value: int, low: int, high: int) -> int:
    return low if value < low else (high if value > high else value)


def _act_of(index: int, setup_end: int, resolution_start: int) -> str:
    if index < setup_end:
        return _ACT_SETUP
    if index < resolution_start:
        return _ACT_DEVELOPMENT
    return _ACT_RESOLUTION


# --------------------------------------------------------------------------
# Energy and holds
# --------------------------------------------------------------------------


def _energy(moment: SelectedMoment) -> float | None:
    """Fused content energy in [0,1], renormalised over what was measured.

    None when nothing was measured. The caller must not read that as zero: a
    moment the motion pass never reached is not a still one.
    """
    values: dict[str, float] = {}
    if moment.motion_energy is not None:
        values["motion_energy"] = float(moment.motion_energy)
    if moment.emotional_peak is not None:
        values["emotional_peak"] = float(moment.emotional_peak)
    if not values:
        return None
    weight = sum(_ENERGY_WEIGHTS[name] for name in values)
    if weight <= 0.0:  # pragma: no cover - both weights are positive constants
        return None
    total = sum(_ENERGY_WEIGHTS[name] * value for name, value in values.items())
    return _round_unit(total / weight)


def _hold_frames(
    moment: SelectedMoment, act: str, request: FilmRequest
) -> int:
    """The hold this shot WANTS, before the trimmable window has its say."""
    pacing = request.pacing
    seconds = pacing.base_seconds(act) * pacing.scale(_energy(moment))
    return max(1, _round_half_up(seconds * request.rate))


def _min_frames(moment: SelectedMoment, request: FilmRequest) -> int:
    """Shortest legal hold for this shot.

    The analysis layer's `SafeTrim.min_duration` is a claim about THIS window
    and outranks the global floor, exactly as it does in the reel planner.
    """
    floor = max(1, _round_half_up(request.pacing.min_hold_seconds * request.rate))
    trim = moment.safe_trim
    if trim is not None and trim.min_duration is not None:
        floor = max(floor, int(math.ceil(trim.min_duration)))
    return max(1, floor)


def _max_frames(request: FilmRequest) -> int:
    return max(1, _round_half_up(request.pacing.max_hold_seconds * request.rate))


# --------------------------------------------------------------------------
# Placement
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class _Placement:
    act: str
    moment: SelectedMoment
    clip_id: str
    timeline_start: int
    duration: int
    source_start: int
    snap_kind: str | None
    window_limited: bool
    is_turn: bool

    @property
    def timeline_end(self) -> int:
        return self.timeline_start + self.duration

    @property
    def source_end(self) -> int:
        return self.source_start + self.duration


def _bounds(
    moment: SelectedMoment, medium: SourceMedia
) -> tuple[int, int]:
    """The integer-frame window this shot may be trimmed inside.

    `moment.window()` is the speech-safe window when speech-safe bounds exist,
    and the general one otherwise. It is then narrowed to the frames the MEDIA
    actually has: `latest_out` is a claim about the moment and whether the file
    holds those frames is a different question, and the answer is a decode past
    EOF at exactly the frame the plan says a shot ends.

    Rounded INWARDS on both sides -- ceil the low end, floor the high end -- so
    rounding can never widen a window that speech analysis narrowed.
    """
    low, high = moment.window()
    low = max(low, medium.available_start)
    high = min(high, medium.available_end)
    return int(math.ceil(low)), int(math.floor(high))


def _word_safe_frame(frame: int, words: Sequence[Word]) -> int:
    """`frame`, pushed forward past any word it falls strictly inside.

    Forward, never back: `math.ceil` on the word's end is the only direction
    that cannot leave the boundary inside the word it just escaped. Repeated
    until it clears, because pushing past one word can land inside the next
    when speech is continuous.
    """
    if not words:
        return frame
    for _ in range(len(words) + 1):
        straddling = [w for w in words if w.start < frame < w.end]
        if not straddling:
            return frame
        moved = int(math.ceil(max(w.end for w in straddling)))
        if moved <= frame:  # pragma: no cover - ceil of a larger end cannot shrink
            return frame
        frame = moved
    return frame


def _choose_source_in(
    moment: SelectedMoment, low: int, high: int, min_frames: int
) -> tuple[int, str | None] | None:
    """The in-point: the best certified snap point that leaves room for a hold.

    Kind-preference is bounded (`_IN_POINT_PENALTY`) and applied against
    strength, so it shifts the contest without overturning it. Ties go to the
    earliest point, which leaves the most footage for the tail, and then to the
    kind name so the order is total.

    Snap times are rounded UP to a whole frame. Rounding down could move an
    in-point back into the word a `speech_gap` was chosen to clear, and the
    renderer refuses a fractional source time in any case.

    A candidate that lands inside a word after rounding is DISCARDED rather
    than nudged: `moments.py` already refuses to certify a snap point strictly
    inside a word, so one that arrives here is either a rounding artefact or a
    record from a different producer, and in both cases another snap point is a
    better answer than a moved one. Only the FALLBACK -- the window's own low
    edge, which is not a certified cut position at all -- is pushed clear.

    Returns None when no word-safe in-point leaves room for the minimum hold.
    """
    latest_in = high - min_frames
    words = moment.words
    best: tuple[float, int, str] | None = None
    for snap in moment.snap_points:
        if snap.cut_direction not in _SNAP_IN_DIRECTIONS:
            continue
        frame = int(math.ceil(snap.time))
        if frame < low or frame > latest_in:
            continue
        if any(w.start < frame < w.end for w in words):
            continue
        penalty = _IN_POINT_PENALTY.get(snap.kind, _DEFAULT_IN_POINT_PENALTY)
        # Negated score first so `min` picks the strongest.
        key = (-(float(snap.strength) - penalty), frame, snap.kind)
        if best is None or key < best:
            best = key
    if best is not None:
        return best[1], best[2]
    fallback = _word_safe_frame(low, words)
    if fallback > latest_in:
        return None
    return fallback, None


def _word_safe_out(
    moment: SelectedMoment, desired_out: int, lowest_out: int, highest_out: int
) -> int | None:
    """An out-point that does not fall strictly inside a spoken word.

    Extension is tried FIRST: finishing the sentence is what a film does, and
    it is also the direction that keeps the shot closer to the hold the pacing
    policy asked for whenever the pacing was cut short by a word. Only when the
    window or the maximum hold forbids extending does the cut pull back to the
    start of the word.

    A boundary exactly ON a word edge is the good case, which is why the test
    is strict on both sides. Returns None when no legal position exists, and
    the caller then shortens to the minimum hold, which the usability pre-pass
    has already proven possible.
    """
    if desired_out < lowest_out or desired_out > highest_out:
        return None
    words = moment.words
    if not words:
        return desired_out
    out = desired_out
    seen: set[int] = set()
    # One iteration per word is the most that can be needed: each move clears
    # at least one word, and the bound stops a pathological overlapping-word
    # list from spinning.
    for _ in range(len(words) + 1):
        if out in seen:
            return None
        seen.add(out)
        straddling = [w for w in words if w.start < out < w.end]
        if not straddling:
            return out
        # The LATEST end and the EARLIEST start, so one move clears every word
        # that covers this position. The loop would converge either way -- it
        # keeps going until nothing straddles -- so this is a bound on the
        # number of iterations rather than a correctness property, and a
        # mutation run confirmed as much by surviving `min` here. Stated so
        # that nobody later "simplifies" the loop away and relies on the
        # extremum instead.
        latest_end = max(w.end for w in straddling)
        extended = int(math.ceil(latest_end))
        if lowest_out <= extended <= highest_out and extended != out:
            out = extended
            continue
        # The earliest start, for the same reason and with the same caveat as
        # the latest end above: it clears every straddling word in one move,
        # and the loop would get there anyway.
        earliest_start = min(w.start for w in straddling)
        pulled = int(math.floor(earliest_start))
        if lowest_out <= pulled <= highest_out and pulled != out:
            out = pulled
            continue
        return None
    return None


@dataclass(frozen=True)
class _Cut:
    """One moment reduced to the frames a film would actually hold."""

    source_start: int
    duration: int
    snap_kind: str | None
    window_limited: bool


def _cut_for(
    moment: SelectedMoment,
    medium: SourceMedia,
    request: FilmRequest,
    desired_frames: int,
) -> _Cut | None:
    """Trim one moment to a hold, or refuse it.

    The in-point is chosen ONCE, from the minimum hold, and the duration is
    decided afterwards. That separation is deliberate: where a shot should
    START is a property of the footage, and how long it is HELD is a pacing
    decision. Coupling them would let a longer act base silently move the cut
    off the speech gap it was chosen for.
    """
    low, high = _bounds(moment, medium)
    min_frames = _min_frames(moment, request)
    max_frames = _max_frames(request)
    if high - low < min_frames:
        return None
    chosen_in = _choose_source_in(moment, low, high, min_frames)
    if chosen_in is None:
        return None
    source_start, snap_kind = chosen_in
    ceiling = min(high, source_start + max_frames)
    floor = source_start + min_frames
    if ceiling < floor:
        return None
    wanted = source_start + max(min_frames, min(desired_frames, max_frames))
    wanted = max(floor, min(wanted, ceiling))
    out = _word_safe_out(moment, wanted, floor, ceiling)
    if out is None:
        # The pacing hold has no word-safe out-point. Fall back to the shortest
        # legal hold, which the usability pre-pass proved exists.
        out = _word_safe_out(moment, floor, floor, ceiling)
    if out is None:
        return None
    duration = out - source_start
    return _Cut(
        source_start=source_start,
        duration=duration,
        snap_kind=snap_kind,
        # The hold was decided by the footage, not the policy, whenever the
        # window stopped it reaching what the policy asked for.
        window_limited=duration < min(desired_frames, max_frames),
    )


def _usable(
    request: FilmRequest, media_by_id: Mapping[str, SourceMedia], notes: list[str]
) -> list[SelectedMoment]:
    """Moments that can hold a shot at the minimum, in running order.

    Run before acts are assigned, so that act membership is decided over the
    moments that will actually be placed. Assigning acts first and dropping
    afterwards is how an act empties out and its required beat fails
    validation on footage that was never the problem.
    """
    rank = _media_rank(request)
    keep: list[SelectedMoment] = []
    for moment in _chronological(request.moments, rank):
        medium = media_by_id[moment.media_id]
        minimum = _min_frames(moment, request)
        if _cut_for(moment, medium, request, minimum) is None:
            low, high = _bounds(moment, medium)
            notes.append(
                f"moment {moment.moment_id} dropped: its trimmable window "
                f"[{low}, {high}) affords no word-safe hold of {minimum} frames, "
                "which is the floor below which a shot stops reading as a hold"
            )
            continue
        keep.append(moment)
    return keep


# --------------------------------------------------------------------------
# EDL assembly
# --------------------------------------------------------------------------


def _media_ref(medium: SourceMedia, rate: float) -> dict[str, Any]:
    return {
        "media_ref_id": medium.media_ref_id,
        "media_id": medium.media_id,
        "media_kind": medium.media_kind,
        "available_range": _range(
            medium.available_start, medium.available_duration, rate
        ),
        "is_span_assembly": medium.is_span_assembly,
        "expected_frame_rate": medium.expected_frame_rate,
        "label": medium.label,
    }


def _speech_clip(placement: _Placement, request: FilmRequest) -> bool:
    ratio = placement.moment.speech_ratio
    if ratio is not None and ratio >= request.ambient.speech_ratio_threshold:
        return True
    return bool(placement.moment.words)


def _windy_clip(placement: _Placement, request: FilmRequest) -> bool:
    ratio = placement.moment.noise_ratio
    return ratio is not None and ratio >= request.ambient.wind_noise_ratio


def _ambient_gain_db(placement: _Placement, request: FilmRequest) -> float:
    ambient = request.ambient
    if _windy_clip(placement, request):
        return ambient.wind_gain_db
    if ambient.preserve_speech and _speech_clip(placement, request):
        return ambient.speech_gain_db
    return ambient.default_gain_db


def _audio_tail(
    placement: _Placement,
    following: _Placement | None,
    medium: SourceMedia,
    notes: list[str],
) -> int | None:
    """An L-cut, sized by whole words or not planned at all.

    `SafeTrim.preserve_audio_tail` says the audio meaningfully continues past
    the picture. It does not say HOW FAR, and there is no honest way to find
    out without the word timings that produced it -- `moments.py` sets the flag
    from a word starting just after the out-point OR from a peak audio event,
    and this planner is handed the words and not the events.

    So the tail is the end of the last WHOLE word that both starts at or after
    the cut and finishes inside the two bounds that were measured: the frames
    the file actually has, and the length of the shot the tail plays under. A
    tail of "whatever the trimmable window allows" would be a length this
    planner invented, sitting in a field whose entire purpose is to state a
    decision. Every case where that is all we would have is refused and named.

    Note that the out-point itself is already word-safe, so no word straddles
    it: the tail is what comes AFTER the cut, not what the cut interrupted.
    """
    trim = placement.moment.safe_trim
    if trim is None or not trim.preserve_audio_tail:
        return None
    if following is None:
        # A tail on the last shot has nothing to play over.
        return None
    if not placement.moment.words:
        notes.append(
            f"clip {placement.clip_id} declares preserve_audio_tail and carries no "
            "word timings to size it, so no L-cut is planned; the length would "
            "have been invented"
        )
        return None
    out = placement.source_end
    reachable = min(trim.latest_out, medium.available_end)
    # Two measured ceilings: the frames the file has, and the shot the tail
    # plays under. Running past the next shot would put the audio under a shot
    # the planner never decided it should cover.
    limit = min(int(math.floor(reachable)), out + following.duration)
    # ONE CONDITION, NOT TWO. The obvious way to write this is "starts at or
    # after the cut AND finishes within reach", plus a guard against a
    # non-positive tail. Mutation testing showed all three covering for each
    # other -- breaking any one of them left the other two producing the same
    # answer, so none of them was testable. `out < ceil(end) <= limit` is the
    # whole rule, and it is sufficient because THE OUT-POINT IS ALREADY
    # WORD-SAFE: no word straddles it, so a word that finishes after the cut
    # necessarily begins at or after it. The tail is therefore positive by
    # construction rather than by a second check.
    ends = [
        int(math.ceil(word.end))
        for word in placement.moment.words
        if out < int(math.ceil(word.end)) <= limit
    ]
    if not ends:
        notes.append(
            f"clip {placement.clip_id} declares preserve_audio_tail and no whole "
            f"word starts at its out-point ({out}) and finishes within reach "
            f"({limit}), so no L-cut is planned"
        )
        return None
    return max(ends) - out


def _dissolve_indices(
    placements: Sequence[_Placement], request: FilmRequest, media_by_id, notes: list[str]
) -> frozenset[int]:
    """Clip positions a dissolve precedes: the first shot of acts II and III.

    Within an act a hard cut is continuity; between acts a dissolve is the
    conventional way to say time passed. Handles are checked on both sides,
    because a transition without them has the renderer blending frames that do
    not exist.
    """
    if request.act_transition_frames <= 0 or len(placements) < 2:
        return frozenset()
    handles = request.act_transition_frames
    out: set[int] = set()
    for index in range(1, len(placements)):
        previous, current = placements[index - 1], placements[index]
        if previous.act == current.act:
            continue
        outgoing_media = media_by_id[previous.moment.media_id]
        incoming_media = media_by_id[current.moment.media_id]
        if (
            previous.source_end + handles <= outgoing_media.available_end
            and current.source_start - handles >= incoming_media.available_start
        ):
            out.add(index)
            continue
        notes.append(
            f"dissolve into {current.clip_id} downgraded to a hard cut: the "
            f"sources do not carry {handles} frames of handle on both sides"
        )
    if out and request.ambient.enabled:
        notes.append(
            f"{len(out)} act dissolve(s) are planned over unmuted ambient beds. "
            "workers/render-video refuses these on contracts#52 — the contract "
            "describes a transition as a picture operation and never says whether "
            "the beds cross-fade with it or hard-cut at the cut point"
        )
    return frozenset(out)


def _music_items(
    request: FilmRequest, total_frames: int, pass_frames: int
) -> list[dict[str, Any]]:
    """The music bed as timeline items: one clip per pass over the track.

    Same realisation as the reel planner's, and for the same reason: a loop
    that is declared rather than laid down asks the renderer to invent the
    restart point (contracts#57, #59).
    """
    music = request.music
    assert music is not None
    rate = request.rate
    items: list[dict[str, Any]] = []
    cursor = 0
    while cursor < total_frames:
        span = min(pass_frames, total_frames - cursor)
        index = len(items) + 1
        last = cursor + span >= total_frames
        items.append(
            {
                "item_type": "clip",
                "clip_id": (
                    f"{music.cue_id}-clip"
                    if index == 1
                    else f"{music.cue_id}-clip-{index:02d}"
                ),
                "name": music.license.track_title or "",
                "media_ref_id": music.media.media_ref_id,
                "source_range": _range(music.source_start, span, rate),
                "timeline_range": _range(cursor, span, rate),
                "moment_id": None,
                "enabled": True,
                "time_effect": None,
                "reframe_track_id": None,
                "color_ops": [],
                "audio": {
                    "gain_db": music.gain_db,
                    "muted": False,
                    "fade_in": (
                        None
                        if music.fade_in is None or index != 1
                        else _rational(music.fade_in, rate)
                    ),
                    "fade_out": (
                        None
                        if music.fade_out is None or not last
                        else _rational(music.fade_out, rate)
                    ),
                    "audio_extends_past_out": None,
                },
                "beat_lock": None,
                "story_beat_id": None,
                "markers": [],
            }
        )
        cursor += span
    return items


def _merge_ranges(ranges: Sequence[tuple[int, int]]) -> list[tuple[int, int]]:
    merged: list[tuple[int, int]] = []
    for start, end in sorted(ranges):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _audio_plan(
    request: FilmRequest,
    placements: Sequence[_Placement],
    total_frames: int,
    notes: list[str],
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    rate = request.rate
    ambient = request.ambient

    # THE BED'S LEVEL IS WRITTEN IN EXACTLY ONE PLACE.
    #
    # `AmbientPlan.per_clip_gain_db` is where a clip's ambient level belongs;
    # `ClipAudio.gain_db` is the clip's own gain. Writing the same decision
    # into both is what makes two gains apply to one bed, which the renderer
    # refuses on contracts#53 because summing, overriding and ignoring are all
    # defensible and are dB apart. So every clip below carries gain_db 0.0 and
    # the level lives here.
    per_clip = [
        {"clip_id": placement.clip_id, "gain_db": _ambient_gain_db(placement, request)}
        for placement in placements
        if ambient.enabled
        and _ambient_gain_db(placement, request) != ambient.default_gain_db
    ]

    music_cues: list[dict[str, Any]] = []
    music_track_block: dict[str, Any] | None = None
    ducking: list[dict[str, Any]] = []

    if request.music is not None:
        music = request.music
        available = music.media.available_duration - (
            music.source_start - music.media.available_start
        )
        pass_frames = max(1, int(math.floor(available)))
        repeats = _music_items(request, total_frames, pass_frames)
        if len(repeats) > 1:
            notes.append(
                f"the music bed is laid down {len(repeats)} times to cover the "
                f"film; the last pass is cut short at "
                f"{repeats[-1]['source_range']['duration']['value']} frames"
            )
        music_cues.append(
            {
                "cue_id": music.cue_id,
                "media_ref_id": music.media.media_ref_id,
                "clip_ids": [item["clip_id"] for item in repeats],
                "license": {
                    "provider": music.license.provider,
                    "license_id": music.license.license_id,
                    "track_title": music.license.track_title,
                    "attribution_required": music.license.attribution_required,
                    "attribution_text": music.license.attribution_text,
                    "license_type": music.license.license_type,
                    "cleared_for": list(music.license.cleared_for),
                },
            }
        )
        music_track_block = {
            "track_id": "a1",
            "kind": "audio",
            "name": "A1 music",
            "role": "music",
            "enabled": True,
            "items": repeats,
        }

        speech_ranges = _merge_ranges(
            [
                (placement.timeline_start, placement.timeline_end)
                for placement in placements
                if _speech_clip(placement, request)
            ]
        )
        if speech_ranges and ambient.preserve_speech and ambient.enabled:
            # A STEP, NOT A RAMP, AND THAT IS THE HONEST SHAPE.
            #
            # `DuckingRule` names an attack and a release and states no envelope
            # between them, so the gain in the middle of the ramp would be the
            # renderer's invention -- and the middle of the ramp is exactly
            # where the voice is (contracts#54). A zero-length attack and
            # release is the one envelope the contract fully describes, so it
            # is the one this planner asks for.
            ducking.append(
                {
                    "rule_id": "duck-music-under-speech",
                    "target": "music",
                    "trigger": "explicit_ranges",
                    "reduction_db": request.ducking_reduction_db,
                    "threshold_db": None,
                    "ratio": None,
                    "attack_ms": 0.0,
                    "release_ms": 0.0,
                    "ranges": [
                        _range(start, end - start, rate) for start, end in speech_ranges
                    ],
                }
            )
            notes.append(
                f"the music ducks {request.ducking_reduction_db:g} dB under "
                f"{len(speech_ranges)} speech range(s) as a step; contracts#54 "
                "states no envelope shape, and a ramp would be a curve this "
                "planner invented"
            )

    loudness = request.target.loudness_target_lufs
    if loudness is None:
        loudness = -14.0
        notes.append("no loudness target given; the mix defaults to -14 LUFS")

    audio_plan = {
        "music": music_cues,
        "ambient": {
            "enabled": ambient.enabled,
            "default_gain_db": ambient.default_gain_db,
            "preserve_speech": ambient.preserve_speech,
            "high_pass_hz": ambient.high_pass_hz,
            "noise_suppression": ambient.noise_suppression,
            "per_clip_gain_db": per_clip,
        },
        "ducking": ducking,
        "mix": {
            "master_gain_db": 0.0,
            "loudness_target_lufs": loudness,
            "true_peak_ceiling_db": -1.0,
            "limiter": True,
            "channels": "stereo",
            "sample_rate": 48000,
        },
    }
    return audio_plan, music_track_block


def _story_arc(
    request: FilmRequest,
    placements: Sequence[_Placement],
    considered: Mapping[str, list[str]],
    turn_clip_id: str | None,
) -> dict[str, Any]:
    """The three-act arc, as a decision rather than a description of the cut.

    `energy_curve` is the TARGET the placement satisfied, sampled at each act's
    first frame plus the turn -- "it is a specification, not a description"
    (edl.schema.json#StoryArc). The realised energies of the individual shots
    are not written here; they are inputs, and writing them back would make the
    specification unfalsifiable.
    """
    rate = request.rate
    by_act: dict[str, list[_Placement]] = {key: [] for key in ACT_KEYS}
    for placement in placements:
        by_act[placement.act].append(placement)

    acts: list[dict[str, Any]] = []
    curve: list[tuple[int, float]] = []
    for act_key in ACT_KEYS:
        act_id, name, intent, beat_id, beat_description, energy = _ACT_SPECS[act_key]
        members = by_act[act_key]
        timeline_range = None
        if members:
            start = members[0].timeline_start
            timeline_range = _range(start, members[-1].timeline_end - start, rate)
            curve.append((start, energy))
        beats = [
            {
                "beat_id": beat_id,
                "description": beat_description,
                "required": True,
                "satisfied_by_clip_ids": [p.clip_id for p in members],
                "candidate_moment_ids": sorted(considered[act_key]),
            }
        ]
        turn_member = next(
            (p for p in members if p.clip_id == turn_clip_id), None
        )
        if turn_member is not None:
            beats.append(
                {
                    "beat_id": _TURN_BEAT_ID,
                    "description": _TURN_BEAT_DESCRIPTION,
                    "required": True,
                    "satisfied_by_clip_ids": [turn_member.clip_id],
                    "candidate_moment_ids": sorted(considered[act_key]),
                }
            )
            curve.append((turn_member.timeline_start, _TURN_ENERGY))
        acts.append(
            {
                "act_id": act_id,
                "name": name,
                "intent": intent,
                "timeline_range": timeline_range,
                "target_energy": energy,
                "beats": beats,
            }
        )

    # One energy per frame. A turn that opens its own act would otherwise put
    # two different target energies on the same timeline position, which is not
    # a curve -- it is a contradiction. The turn wins, because it is the more
    # specific claim.
    resolved: dict[int, float] = {}
    for frame, energy in curve:
        if frame in resolved:
            resolved[frame] = max(resolved[frame], energy)
        else:
            resolved[frame] = energy
    energy_curve = [
        {"time": _rational(frame, rate), "energy": energy}
        for frame, energy in sorted(resolved.items())
    ]

    return {
        "arc_id": "arc-three-act",
        "template": "three_act",
        "title": request.title,
        "logline": request.logline,
        "acts": acts,
        "energy_curve": energy_curve,
        # `template`, not `tier3_model`: no frontier model was consulted, and a
        # source claiming otherwise would make a locally-derived arc look like
        # a judgement that cost a contact sheet and a consent entry.
        "source": "template",
        "model": None,
        "prompt_id": None,
        "consent": None,
        "rationale": request.rationale,
    }


def _variant_block(request: FilmRequest) -> dict[str, Any] | None:
    if request.variant is None:
        return None
    return {
        "variant_id": request.variant.variant_id,
        "variant_index": request.variant.variant_index,
        "sibling_edl_ids": list(request.variant.sibling_edl_ids),
        "strategy": request.variant.strategy,
        "description": request.variant.description,
    }


# --------------------------------------------------------------------------
# The planner
# --------------------------------------------------------------------------


def plan_film(request: FilmRequest) -> FilmPlan:
    """Plan one film. Pure function of `request`; see the module docstring."""
    notes: list[str] = []
    rate = request.rate
    media_by_id = {medium.media_id: medium for medium in request.media}

    if not request.media_sequence and len({m.media_id for m in request.moments}) > 1:
        notes.append(
            "the film runs its sources in the order they were declared in "
            "FilmRequest.media, which is not known to be capture order: "
            "SelectedMoment carries no capture timestamp. Pass "
            "FilmRequest.media_sequence to give the film a real chronology"
        )

    usable = _usable(request, media_by_id, notes)
    if len(usable) < 3:
        raise FilmTooShort(
            f"{len(usable)} moment(s) can hold a shot, and a three-act film needs "
            "one in each act. Refused rather than emitted as a two-shot artifact "
            "labelled a film"
        )

    turn, turn_axis = _turn_index(usable)
    if turn_axis != "emotional_peak":
        unmeasured = sum(1 for moment in usable if moment.emotional_peak is None)
        notes.append(
            f"the turn was chosen on the fused moment score: {unmeasured} of "
            f"{len(usable)} placed moments carry no emotional_peak, and a pool "
            "where only some peaks were measured cannot be ranked on that axis. "
            "A moment nobody measured is not a moment that scored zero"
        )
    setup_end, resolution_start = _act_boundaries(
        len(usable), turn, request.act_shape
    )
    turn_act = _act_of(turn, setup_end, resolution_start)
    if turn_act != _ACT_DEVELOPMENT:
        notes.append(
            f"the strongest moment is the {'first' if turn == 0 else 'last'} thing "
            f"that happened, so the turn falls in the {turn_act} act rather than "
            "the development act. Act I is a prefix of the chronology and act III "
            "a suffix; moving the turn would mean reordering the film"
        )

    # --- placement --------------------------------------------------------
    placements: list[_Placement] = []
    considered: dict[str, list[str]] = {key: [] for key in ACT_KEYS}
    cursor = 0
    for index, moment in enumerate(usable):
        act = _act_of(index, setup_end, resolution_start)
        considered[act].append(moment.moment_id)
        medium = media_by_id[moment.media_id]
        cut = _cut_for(moment, medium, request, _hold_frames(moment, act, request))
        if cut is None:  # pragma: no cover - `_usable` proved a hold exists
            notes.append(
                f"moment {moment.moment_id} dropped at placement: no word-safe "
                "hold survived the act's pacing target"
            )
            continue
        placements.append(
            _Placement(
                act=act,
                moment=moment,
                clip_id=f"clip-{len(placements) + 1:02d}",
                timeline_start=cursor,
                duration=cut.duration,
                source_start=cut.source_start,
                snap_kind=cut.snap_kind,
                window_limited=cut.window_limited,
                is_turn=(index == turn),
            )
        )
        cursor += cut.duration

    if len(placements) < 3:  # pragma: no cover - `_usable` guarantees three
        raise FilmTooShort(
            f"only {len(placements)} shot(s) could be placed; a three-act film "
            "needs one in each act"
        )

    picture_frames = placements[-1].timeline_end
    total_frames = picture_frames + request.end_hold_frames

    # --- video track ------------------------------------------------------
    dissolves = _dissolve_indices(placements, request, media_by_id, notes)
    reframe_tracks: list[dict[str, Any]] = []
    items: list[dict[str, Any]] = []
    turn_clip_id: str | None = None

    for position, placement in enumerate(placements):
        moment = placement.moment
        medium = media_by_id[moment.media_id]
        clip_width, clip_height = _crop_size(
            medium.aspect_ratio, request.target.aspect_ratio
        )
        track = None
        if request.reframe.enabled and (clip_width, clip_height) != (1.0, 1.0):
            track = _reframe_track(placement, request, clip_width, clip_height)
            reframe_tracks.append(track)

        markers = []
        if placement.is_turn:
            turn_clip_id = placement.clip_id
            markers.append(
                {
                    "name": "the turn",
                    "marked_range": _range(
                        placement.timeline_start, placement.duration, rate
                    ),
                    "color": "MAGENTA",
                    "kind": "emotional_peak",
                    "comment": "the moment the film is about",
                }
            )

        following = placements[position + 1] if position + 1 < len(placements) else None
        tail = _audio_tail(placement, following, medium, notes)

        if position in dissolves:
            items.append(
                {
                    "item_type": "transition",
                    "transition_id": f"xfade-into-{placement.clip_id}",
                    "transition_type": "dissolve",
                    "in_offset": _rational(request.act_transition_frames, rate),
                    "out_offset": _rational(request.act_transition_frames, rate),
                    # `linear` is the only easing whose curve the contract
                    # states; the others are names (contracts#52).
                    "easing": "linear",
                    "parameters": {},
                }
            )

        items.append(
            {
                "item_type": "clip",
                "clip_id": placement.clip_id,
                "name": moment.label,
                "media_ref_id": medium.media_ref_id,
                "source_range": _range(
                    placement.source_start, placement.duration, rate
                ),
                "timeline_range": _range(
                    placement.timeline_start, placement.duration, rate
                ),
                "moment_id": moment.moment_id,
                "enabled": True,
                "time_effect": None,
                "reframe_track_id": None if track is None else track["reframe_track_id"],
                "color_ops": [],
                "audio": {
                    # Always 0.0: the bed's level lives in the ambient plan, once.
                    "gain_db": 0.0,
                    "muted": not request.ambient.enabled,
                    "fade_in": None,
                    "fade_out": None,
                    "audio_extends_past_out": (
                        None if tail is None else _rational(tail, rate)
                    ),
                },
                # No cut in a film is beat-locked. See the module docstring.
                "beat_lock": None,
                "story_beat_id": _ACT_SPECS[placement.act][3],
                "markers": markers,
            }
        )

    if request.end_hold_frames > 0:
        items.append(
            {
                "item_type": "gap",
                "gap_id": "end-hold",
                "duration": _rational(request.end_hold_frames, rate),
                "fill": "black",
            }
        )
        notes.append(
            f"the film holds {request.end_hold_frames} frames of black after the "
            "last shot, as a stated intention rather than an accident of arithmetic"
        )

    tracks: list[dict[str, Any]] = [
        {
            "track_id": "v1",
            "kind": "video",
            "name": "V1",
            "role": "primary",
            "enabled": True,
            "items": items,
        }
    ]

    # --- media refs -------------------------------------------------------
    used_ids = {placement.moment.media_id for placement in placements}
    if request.music is not None:
        used_ids.add(request.music.media.media_id)
    media_refs = [
        _media_ref(medium, rate)
        for medium in sorted(request.media, key=lambda m: m.media_ref_id)
        if medium.media_id in used_ids
    ]
    for medium in sorted(request.media, key=lambda m: m.media_ref_id):
        if medium.media_id not in used_ids:
            notes.append(
                f"source {medium.media_ref_id} declared but unused; it is not in "
                "the EDL, so the renderer will not open it"
            )

    # --- audio ------------------------------------------------------------
    audio_plan, music_track = _audio_plan(request, placements, total_frames, notes)
    if music_track is not None:
        tracks.append(music_track)

    story_arc = _story_arc(request, placements, considered, turn_clip_id)

    edl: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "edl_id": "0" * 64,  # replaced below, once the rest is final
        "name": request.name,
        "kind": "film",
        "rate": rate,
        "global_start_time": _rational(0, rate),
        "target": {
            "destination": request.target.destination,
            "resolution": {
                "width": request.target.resolution[0],
                "height": request.target.resolution[1],
            },
            "aspect_ratio": {
                "numerator": request.target.aspect_ratio[0],
                "denominator": request.target.aspect_ratio[1],
            },
            "target_duration": (
                None
                if request.target.target_duration is None
                else _rational(request.target.target_duration, rate)
            ),
            "max_duration": (
                None
                if request.target.max_duration is None
                else _rational(request.target.max_duration, rate)
            ),
            "loudness_target_lufs": request.target.loudness_target_lufs,
        },
        "media_refs": media_refs,
        "tracks": tracks,
        "reframe_tracks": reframe_tracks,
        "audio_plan": audio_plan,
        # No beat grid: a film's cuts answer to the sentence, not the bar.
        "beat_grid": None,
        "story_arc": story_arc,
        "color_pipeline": {
            "input_transform": "auto",
            "working_space": "rec709",
            "output_transform": "rec709",
            "tone_map_hdr_to_sdr": True,
        },
        "variant": _variant_block(request),
        "determinism": {
            "planner": PLANNER_ID,
            "planner_version": request.planner_version,
            "seed": request.seed,
            "inputs_digest": blake3_hex(
                canonical_json(_request_digest_payload(request))
            ),
            "generated_at": request.generated_at,
        },
        "validation": None,
        "otio": {
            "otio_schema_version": "OTIO_SCHEMA:Timeline.1",
            "metadata_namespace": "memory_engine",
            "unmapped_fields": [],
            "round_trip_verified": False,
        },
    }

    words_present = any(placement.moment.words for placement in placements)
    edl["validation"] = _validate(edl, request, placements, total_frames, words_present)
    edl["edl_id"] = blake3_hex(canonical_json(_digest_view(edl)))

    shot_frames = tuple(placement.duration for placement in placements)
    window_limited = sum(1 for p in placements if p.window_limited)
    l_cuts = sum(
        1
        for track in tracks
        for item in track["items"]
        if item["item_type"] == "clip"
        and (item.get("audio") or {}).get("audio_extends_past_out") is not None
    )

    if not words_present:
        notes.append(
            "no placed moment carries word timings, so no cut in this film is "
            "certified word-safe and the plan emits no no_mid_word_cut finding. "
            "Absent is not passing"
        )
    if pacing_spread(shot_frames) == 0.0:
        notes.append(
            f"every shot is held for {shot_frames[0]} frames: the pacing spread is "
            "0.0, which is a film cut at constant density"
        )
    if window_limited:
        notes.append(
            f"{window_limited} of {len(shot_frames)} shots were held for as long as "
            "their trimmable window allowed rather than for as long as the pacing "
            "policy asked; on those shots the analysis layer set the pace"
        )
    realised_seconds = total_frames / rate
    if realised_seconds < request.min_film_seconds:
        notes.append(
            f"this film runs {realised_seconds:.2f}s against a "
            f"{request.min_film_seconds:g}s floor for the form: {len(shot_frames)} "
            "shots were available, and a film cannot be longer than its footage"
        )

    return FilmPlan(
        edl=edl,
        notes=tuple(notes),
        duration_frames=total_frames,
        shot_frames=shot_frames,
        window_limited=window_limited,
        l_cuts=l_cuts,
        word_safe_certified=words_present,
    )


# --------------------------------------------------------------------------
# Digests
# --------------------------------------------------------------------------


def _request_digest_payload(request: FilmRequest) -> dict[str, Any]:
    """Every input the planner reads, as canonical-JSON-able data.

    Explicit rather than `dataclasses.asdict`, for the reason `reel.py` gives:
    a field that silently escapes the digest makes two different plans claim
    the same inputs.
    """
    target = request.target
    ambient = request.ambient
    reframe = request.reframe
    pacing = request.pacing
    shape = request.act_shape
    return {
        "planner": PLANNER_ID,
        "planner_version": request.planner_version,
        "rate": request.rate,
        "seed": request.seed,
        "arc_template": request.arc_template,
        "act_transition_frames": request.act_transition_frames,
        "end_hold_frames": request.end_hold_frames,
        "min_film_seconds": request.min_film_seconds,
        "ducking_reduction_db": request.ducking_reduction_db,
        "media_sequence": list(request.media_sequence),
        "name": request.name,
        "title": request.title,
        "logline": request.logline,
        "rationale": request.rationale,
        "pacing": {
            "setup_hold_seconds": pacing.setup_hold_seconds,
            "development_hold_seconds": pacing.development_hold_seconds,
            "resolution_hold_seconds": pacing.resolution_hold_seconds,
            "min_hold_seconds": pacing.min_hold_seconds,
            "max_hold_seconds": pacing.max_hold_seconds,
            "quiet_scale": pacing.quiet_scale,
            "busy_scale": pacing.busy_scale,
        },
        "act_shape": {
            "setup_fraction": shape.setup_fraction,
            "resolution_fraction": shape.resolution_fraction,
        },
        "target": {
            "destination": target.destination,
            "resolution": list(target.resolution),
            "aspect_ratio": list(target.aspect_ratio),
            "target_duration": target.target_duration,
            "max_duration": target.max_duration,
            "loudness_target_lufs": target.loudness_target_lufs,
        },
        "ambient": {
            "enabled": ambient.enabled,
            "default_gain_db": ambient.default_gain_db,
            "preserve_speech": ambient.preserve_speech,
            "high_pass_hz": ambient.high_pass_hz,
            "noise_suppression": ambient.noise_suppression,
            "wind_noise_ratio": ambient.wind_noise_ratio,
            "wind_gain_db": ambient.wind_gain_db,
            "speech_ratio_threshold": ambient.speech_ratio_threshold,
            "speech_gain_db": ambient.speech_gain_db,
        },
        "reframe": {
            "enabled": reframe.enabled,
            "fallback": reframe.fallback,
            "smoothing_method": reframe.smoothing_method,
            "smoothing_window_frames": reframe.smoothing_window_frames,
            "max_velocity_per_second": reframe.max_velocity_per_second,
            "deadzone": reframe.deadzone,
            "keep_in_frame": reframe.keep_in_frame,
            "headroom": reframe.headroom,
            "subject_source": reframe.subject_source,
        },
        "media": [
            {
                "media_ref_id": m.media_ref_id,
                "media_id": m.media_id,
                "available_start": m.available_start,
                "available_duration": m.available_duration,
                "aspect_ratio": list(m.aspect_ratio),
                "media_kind": m.media_kind,
                "is_span_assembly": m.is_span_assembly,
                "expected_frame_rate": m.expected_frame_rate,
                "label": m.label,
            }
            for m in sorted(request.media, key=lambda m: m.media_ref_id)
        ],
        "moments": [
            {
                "moment_id": m.moment_id,
                "media_id": m.media_id,
                "source_start": m.source_start,
                "source_duration": m.source_duration,
                "score": m.score,
                "hook_potential": m.hook_potential,
                "emotional_peak": m.emotional_peak,
                "motion_energy": m.motion_energy,
                "speech_ratio": m.speech_ratio,
                "noise_ratio": m.noise_ratio,
                "label": m.label,
                "snap_points": [
                    {
                        "time": s.time,
                        "kind": s.kind,
                        "strength": s.strength,
                        "confidence": s.confidence,
                        "cut_direction": s.cut_direction,
                    }
                    for s in m.snap_points
                ],
                "safe_trim": None
                if m.safe_trim is None
                else {
                    "earliest_in": m.safe_trim.earliest_in,
                    "latest_out": m.safe_trim.latest_out,
                    "speech_safe_in": m.safe_trim.speech_safe_in,
                    "speech_safe_out": m.safe_trim.speech_safe_out,
                    "min_duration": m.safe_trim.min_duration,
                    "preserve_audio_tail": m.safe_trim.preserve_audio_tail,
                },
                "words": [
                    {"start": w.start, "end": w.end, "text": w.text} for w in m.words
                ],
                "subject_track": [
                    {
                        "time": s.time,
                        "center_x": s.center_x,
                        "center_y": s.center_y,
                        "confidence": s.confidence,
                    }
                    for s in m.subject_track
                ],
            }
            for m in sorted(request.moments, key=lambda m: m.moment_id)
        ],
        "music": None
        if request.music is None
        else {
            "cue_id": request.music.cue_id,
            "media_ref_id": request.music.media.media_ref_id,
            "source_start": request.music.source_start,
            "gain_db": request.music.gain_db,
            "fade_in": request.music.fade_in,
            "fade_out": request.music.fade_out,
            "license": {
                "provider": request.music.license.provider,
                "license_type": request.music.license.license_type,
                "cleared_for": list(request.music.license.cleared_for),
                "license_id": request.music.license.license_id,
                "track_title": request.music.license.track_title,
                "attribution_required": request.music.license.attribution_required,
                "attribution_text": request.music.license.attribution_text,
            },
        },
        "variant": None
        if request.variant is None
        else {
            "variant_id": request.variant.variant_id,
            "variant_index": request.variant.variant_index,
            "strategy": request.variant.strategy,
            "description": request.variant.description,
        },
    }


def _digest_view(edl: Mapping[str, Any]) -> dict[str, Any]:
    """The EDL with the volatile fields of `EDL_ID_EXCLUDED` removed."""
    view = {key: value for key, value in edl.items() if key != "edl_id"}
    determinism = dict(view["determinism"])
    determinism.pop("generated_at", None)
    view["determinism"] = determinism
    if view.get("validation") is not None:
        validation = dict(view["validation"])
        validation.pop("validated_at", None)
        view["validation"] = validation
    if view.get("variant") is not None:
        variant = dict(view["variant"])
        variant.pop("sibling_edl_ids", None)
        view["variant"] = variant
    stripped_tracks = []
    for track in view["tracks"]:
        track = dict(track)
        items = []
        for item in track["items"]:
            item = dict(item)
            item.pop("timeline_range", None)
            items.append(item)
        track["items"] = items
        stripped_tracks.append(track)
    view["tracks"] = stripped_tracks
    return view


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------


def _check(
    check_id: str,
    passed: bool,
    severity: str,
    detail: str = "",
    clip_id: str | None = None,
) -> dict[str, Any]:
    return {
        "check_id": check_id,
        "passed": passed,
        "severity": severity,
        "detail": detail,
        "clip_id": clip_id,
    }


def _audio_tail_frames(item: Mapping[str, Any]) -> float:
    tail = (item.get("audio") or {}).get("audio_extends_past_out")
    return 0 if tail is None else tail["value"]


def _clip_count(edl: Mapping[str, Any]) -> int:
    return sum(
        1
        for track in edl["tracks"]
        for item in track["items"]
        if item["item_type"] == "clip"
    )


def _validate(
    edl: Mapping[str, Any],
    request: FilmRequest,
    placements: Sequence[_Placement],
    total_frames: int,
    words_present: bool,
) -> dict[str, Any]:
    """Pre-render checks, re-derived from the EDL that was just built.

    Re-derived rather than read off the planner's intermediate state, for the
    reason `reel.py` states: a check that reads the same variable the writer
    read cannot catch the writer being wrong.
    """
    checks: list[dict[str, Any]] = []
    media_by_ref = {m["media_ref_id"]: m for m in edl["media_refs"]}
    rate = request.rate

    # media_refs_resolvable
    missing = sorted(
        {
            item["media_ref_id"]
            for track in edl["tracks"]
            for item in track["items"]
            if item["item_type"] == "clip" and item["media_ref_id"] not in media_by_ref
        }
    )
    checks.append(
        _check(
            "media_refs_resolvable",
            not missing,
            "error",
            f"{len(media_by_ref)} refs declared"
            if not missing
            else f"undeclared media_ref_id: {', '.join(missing)}",
        )
    )

    # source_range_within_available, including whatever an L-cut reads past the
    # picture out-point.
    escapes: list[tuple[str, str]] = []
    for track in edl["tracks"]:
        for item in track["items"]:
            if item["item_type"] != "clip":
                continue
            ref = media_by_ref.get(item["media_ref_id"])
            if ref is None:
                continue
            available_start = ref["available_range"]["start_time"]["value"]
            available_end = available_start + ref["available_range"]["duration"]["value"]
            start = item["source_range"]["start_time"]["value"]
            end = start + item["source_range"]["duration"]["value"]
            reach = end + _audio_tail_frames(item)
            if start < available_start or reach > available_end:
                span = (
                    f"[{start}, {end})"
                    if reach == end
                    else f"[{start}, {end}) plus {reach - end} frames of audio tail"
                )
                escapes.append((item["clip_id"], span))
    checks.append(
        _check(
            "source_range_within_available",
            not escapes,
            "error",
            f"{_clip_count(edl)} clips within their sources"
            if not escapes
            else "; ".join(f"{clip} at {span}" for clip, span in escapes),
            None if not escapes else escapes[0][0],
        )
    )

    # source_ranges_are_whole_frames is not a contract check id, so the
    # condition is enforced structurally instead: every position this planner
    # computes is an int, and `_rational` writes an int. A float that slipped
    # through would fail the renderer's own whole-frame test, loudly, which is
    # the right place for it -- inventing a check id here would fail the schema.

    # timeline_contiguous
    gaps: list[str] = []
    for track in edl["tracks"]:
        cursor = 0
        for item in track["items"]:
            if item["item_type"] == "transition":
                # Zero-duration in OTIO: the clips either side still butt up.
                continue
            if item["item_type"] == "gap":
                cursor += item["duration"]["value"]
                continue
            start = item["timeline_range"]["start_time"]["value"]
            if start != cursor:
                gaps.append(
                    f"{track['track_id']}/{item['clip_id']} starts at {start}, "
                    f"expected {cursor}"
                )
            cursor = start + item["timeline_range"]["duration"]["value"]
        # And the track ENDS where the plan says the film does. Without this a
        # trailing hold of black could be dropped from the timeline and every
        # clip would still start where the one before it stopped -- the check
        # would pass on a film one gap shorter than the plan claims. Mutation
        # testing found exactly that.
        if track["kind"] == "video" and cursor != total_frames:
            gaps.append(
                f"{track['track_id']} ends at {cursor}, and the plan is "
                f"{total_frames} frames long"
            )
    checks.append(
        _check(
            "timeline_contiguous",
            not gaps,
            "error",
            f"every track tiles [0, {total_frames})" if not gaps else "; ".join(gaps),
        )
    )

    # transition_handles_available
    transitions = [
        (track, position, item)
        for track in edl["tracks"]
        for position, item in enumerate(track["items"])
        if item["item_type"] == "transition"
    ]
    handle_failures: list[str] = []
    for track, position, item in transitions:
        siblings = track["items"]
        around = (
            []
            if position == 0 or position == len(siblings) - 1
            else [siblings[position - 1], siblings[position + 1]]
        )
        neighbours = [other for other in around if other["item_type"] == "clip"]
        if len(neighbours) != 2:
            handle_failures.append(f"{item['transition_id']} lacks two clip neighbours")
            continue
        outgoing, incoming = neighbours
        out_ref = media_by_ref.get(outgoing["media_ref_id"])
        in_ref = media_by_ref.get(incoming["media_ref_id"])
        if out_ref is None or in_ref is None:
            handle_failures.append(
                f"{item['transition_id']} sits between clips whose media cannot "
                "be resolved"
            )
            continue
        # `out_offset` extends the transition forwards into the incoming clip,
        # so the OUTGOING clip needs that much TAIL; `in_offset` extends it
        # backwards, so the INCOMING clip needs that much HEAD. Charging them
        # the other way round is invisible while the two are equal.
        out_end = (
            outgoing["source_range"]["start_time"]["value"]
            + outgoing["source_range"]["duration"]["value"]
            + item["out_offset"]["value"]
        )
        in_start = (
            incoming["source_range"]["start_time"]["value"] - item["in_offset"]["value"]
        )
        available_out_end = (
            out_ref["available_range"]["start_time"]["value"]
            + out_ref["available_range"]["duration"]["value"]
        )
        if out_end > available_out_end:
            handle_failures.append(
                f"{outgoing['clip_id']} has no tail handle for {item['transition_id']}"
            )
        if in_start < in_ref["available_range"]["start_time"]["value"]:
            handle_failures.append(
                f"{incoming['clip_id']} has no head handle for {item['transition_id']}"
            )
    if transitions:
        checks.append(
            _check(
                "transition_handles_available",
                not handle_failures,
                "error",
                f"{len(transitions)} transition(s) have handles on both sides"
                if not handle_failures
                else "; ".join(handle_failures),
            )
        )

    # no_mid_word_cut. ERROR, not warning: build plan §7 makes "no mid-word
    # cuts, word-timestamp verified" a quality gate for films specifically.
    # Emitted ONLY when words exist -- a passing finding derived from an empty
    # word list would certify a property nothing measured.
    if words_present:
        mid_word: list[tuple[str, str]] = []
        for placement in placements:
            for word in placement.moment.words:
                for edge in (placement.source_start, placement.source_end):
                    if word.start < edge < word.end:
                        mid_word.append(
                            (
                                placement.clip_id,
                                f"{placement.clip_id} cuts inside "
                                f"{word.text or 'a word'} at {edge:g}",
                            )
                        )
        checks.append(
            _check(
                "no_mid_word_cut",
                not mid_word,
                "error",
                f"{sum(len(p.moment.words) for p in placements)} word timings "
                "checked; every cut falls between words"
                if not mid_word
                else "; ".join(detail for _, detail in mid_word),
                None if not mid_word else mid_word[0][0],
            )
        )

    # reframe_aspect_matches_target / reframe_keyframes_ordered. Both emitted
    # unconditionally: `workers/render-video` requires a PASSING finding for
    # each before it renders anything, and "the validator looked and found
    # nothing" has to be distinguishable from "the validator never looked".
    reframe_tracks = edl["reframe_tracks"]
    aspect_failures: list[str] = []
    order_failures: list[str] = []
    if reframe_tracks:
        source_aspect_by_ref = {m.media_ref_id: m.aspect_ratio for m in request.media}
        clip_by_reframe = {
            item["reframe_track_id"]: item
            for track in edl["tracks"]
            for item in track["items"]
            if item["item_type"] == "clip" and item.get("reframe_track_id")
        }
        for reframe in reframe_tracks:
            clip = clip_by_reframe.get(reframe["reframe_track_id"])
            source_aspect = (
                None if clip is None else source_aspect_by_ref.get(clip["media_ref_id"])
            )
            if source_aspect is None:
                # `media_refs_resolvable` has already failed on this; guessing
                # an aspect here would invent a failure or hide a real one.
                continue
            wanted = (
                reframe["target_aspect_ratio"]["numerator"]
                / reframe["target_aspect_ratio"]["denominator"]
            )
            previous = -math.inf
            for keyframe in reframe["keyframes"]:
                crop = keyframe["crop"]
                got = crop["w"] * source_aspect[0] / (crop["h"] * source_aspect[1])
                if abs(got - wanted) > 1e-9 * wanted:
                    aspect_failures.append(
                        f"{reframe['reframe_track_id']} crop is {got:.9f}, target "
                        f"is {wanted:.9f}"
                    )
                time = keyframe["time"]["value"]
                if time <= previous:
                    order_failures.append(
                        f"{reframe['reframe_track_id']} keyframe at {time} follows "
                        f"{previous}"
                    )
                previous = time
    checks.append(
        _check(
            "reframe_aspect_matches_target",
            not aspect_failures,
            "error",
            (
                f"{len(reframe_tracks)} tracks match the target aspect"
                if reframe_tracks
                else "no reframe track is planned, so there is no crop whose aspect "
                "could disagree with the target"
            )
            if not aspect_failures
            else "; ".join(sorted(set(aspect_failures))),
        )
    )
    checks.append(
        _check(
            "reframe_keyframes_ordered",
            not order_failures,
            "error",
            (
                f"{len(reframe_tracks)} tracks, keyframes strictly increasing"
                if reframe_tracks
                else "no reframe track is planned, so there are no keyframes to order"
            )
            if not order_failures
            else "; ".join(order_failures),
        )
    )

    # duration_within_max
    if request.target.max_duration is not None:
        ceiling = _round_half_up(request.target.max_duration)
        checks.append(
            _check(
                "duration_within_max",
                total_frames <= ceiling,
                "error",
                f"{total_frames / rate:.3f} s against a {ceiling / rate:.3f} s ceiling",
            )
        )

    # music_license_covers_destination and music_cues_placed_once
    music_cues = (edl["audio_plan"] or {}).get("music") or []
    if music_cues:
        needed = clearance_for_destination(request.target.destination)
        uncovered = [
            cue["cue_id"]
            for cue in music_cues
            if needed not in cue["license"]["cleared_for"]
        ]
        checks.append(
            _check(
                "music_license_covers_destination",
                not uncovered,
                "error",
                f"{needed} is covered for every cue"
                if not uncovered
                else f"{needed} not cleared for: {', '.join(uncovered)}",
            )
        )

        # contracts#59: the bed is placed once, on a track, and the cue names
        # that placement. So: every clip a cue claims must be a music-role
        # clip, no clip may be claimed twice, and no music-role clip may go
        # unclaimed and therefore unlicensed.
        music_clips: dict[str, str] = {}
        for track in edl["tracks"]:
            if track["kind"] != "audio" or track.get("role") != "music":
                continue
            for item in track["items"]:
                if item["item_type"] == "clip":
                    music_clips[item["clip_id"]] = item["media_ref_id"]
        claimed: dict[str, str] = {}
        placement_failures: list[str] = []
        for cue in music_cues:
            if not cue["clip_ids"]:
                placement_failures.append(
                    f"cue {cue['cue_id']} places itself on no clips"
                )
            for clip_id in cue["clip_ids"]:
                if clip_id not in music_clips:
                    placement_failures.append(
                        f"cue {cue['cue_id']} names {clip_id}, which is not a clip "
                        "on a music-role track"
                    )
                    continue
                if clip_id in claimed:
                    placement_failures.append(
                        f"{clip_id} is claimed by cues {claimed[clip_id]} and "
                        f"{cue['cue_id']}"
                    )
                    continue
                claimed[clip_id] = cue["cue_id"]
                if music_clips[clip_id] != cue["media_ref_id"]:
                    placement_failures.append(
                        f"cue {cue['cue_id']} licenses {cue['media_ref_id']} but "
                        f"{clip_id} plays {music_clips[clip_id]}"
                    )
        for clip_id in sorted(music_clips):
            if clip_id not in claimed:
                placement_failures.append(
                    f"{clip_id} is on a music-role track and no cue claims it, so "
                    "it would play with no licence attached"
                )
        checks.append(
            _check(
                "music_cues_placed_once",
                not placement_failures,
                "error",
                f"{len(music_clips)} music clip(s), each claimed by exactly one cue"
                if not placement_failures
                else "; ".join(placement_failures),
            )
        )

    # required_story_beats_satisfied
    arc = edl["story_arc"]
    unsatisfied = [
        beat["beat_id"]
        for act in arc["acts"]
        for beat in act["beats"]
        if beat["required"] and not beat["satisfied_by_clip_ids"]
    ]
    required_count = sum(
        1 for act in arc["acts"] for beat in act["beats"] if beat["required"]
    )
    checks.append(
        _check(
            "required_story_beats_satisfied",
            not unsatisfied,
            "error",
            f"{required_count} required beats, all satisfied"
            if not unsatisfied
            else f"unsatisfied: {', '.join(unsatisfied)}",
        )
    )

    # audio_loudness_target_set
    mix_lufs = edl["audio_plan"]["mix"]["loudness_target_lufs"]
    target_lufs = request.target.loudness_target_lufs
    checks.append(
        _check(
            "audio_loudness_target_set",
            target_lufs is None or mix_lufs == target_lufs,
            "warning",
            f"mix normalises to {mix_lufs} LUFS"
            if target_lufs is None or mix_lufs == target_lufs
            else f"mix is {mix_lufs} LUFS but the target asked for {target_lufs}",
        )
    )

    # determinism_digest_present
    digest = edl["determinism"]["inputs_digest"]
    checks.append(
        _check(
            "determinism_digest_present",
            len(digest) == 64 and not set(digest) - _HEX_CHARS,
            "error",
            "",
        )
    )

    failed_errors = [c for c in checks if not c["passed"] and c["severity"] == "error"]
    failed_warnings = [
        c for c in checks if not c["passed"] and c["severity"] == "warning"
    ]
    status = "pass"
    if failed_errors:
        status = "fail"
    elif failed_warnings:
        status = "warn"

    return {
        "status": status,
        "checks": checks,
        "validated_at": request.validated_at,
        "validator_version": f"{PLANNER_ID}/{request.planner_version}",
    }
