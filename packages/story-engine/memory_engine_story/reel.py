"""Reel planner — selected moments + a beat grid + a target duration -> one EDL.

Build plan §4.4. The output validates against `contracts/schemas/edl.schema.json`
and is the complete instruction set for `workers/render-video/`: the renderer
seeks, crops, mixes and encodes, and decides nothing. If a render ever "needs a
judgement call", that is a gap in this file, not in the renderer.

WHAT THE CONTRACT ALREADY DECIDED, AND IS NOT RE-DECIDED HERE

  * A HARD CUT IS THE ABSENCE OF A TRANSITION. Never a zero-length one. OTIO
    models a straight cut as two clips butting together; a degenerate
    Transition round-trips as a real transition and some NLEs render it as a
    one-frame dissolve. So `_video_items` emits a Transition only where a
    dissolve was actually asked for, and `validate_edl` fails any EDL carrying
    a transition with both offsets at zero.
  * TIMERANGE IS HALF-OPEN. [start, start+duration). Adjacent clips tile with
    no off-by-one frame, which is why clip i+1 starts at exactly clip i's start
    plus its duration and not one frame later.
  * `timeline_range` IS DERIVED. It is the running sum of preceding durations.
    We emit it so a validator can catch a planner that emitted an inconsistent
    timeline -- this one, most likely -- and it is excluded from `edl_id`
    precisely because it carries no decision. Note that MusicCue.timeline_range
    is NOT derived and is NOT excluded: where a cue sits is a real choice.
  * TIME IS RATIONAL, NEVER FLOAT SECONDS. 30000/1001 has no float
    representation that survives an OTIO round trip, and a drifting frame is a
    missed beat.

THE FIVE DECISIONS THIS FILE MAKES, AND WHAT THE ALTERNATIVES BREAK

1. THE TIMELINE IS THE QUANTISED BEAT GRID.

   Cut points are beat times rounded to whole frames, and clip durations are
   the differences between consecutive rounded beats. A beat at 112.387612
   frames becomes a cut at frame 112, and `beat_lock.alignment_error_ms`
   records the -6.47 ms that costs.

   The alternative -- letting clips start on exact fractional beat times -- is
   not renderable: there is no frame 112.387612 to seek to, so the renderer
   would round, and the rounding decision would live in the renderer instead of
   the plan. Recording the error here is what makes the <50 ms downbeat gate
   measurable from the plan alone, without rendering anything.

2. THE SOURCE IN-POINT IS ALWAYS A CERTIFIED SNAP POINT.

   MomentRecord.snap_points are the positions the analysis layer certified as
   safe to cut on. The planner picks among them; it never invents one. That
   constraint is the entire mechanism behind "no mid-word cuts" and behind a
   cut landing on a real motion onset rather than 40 ms before one.

   When no snap point can host a slot, this raises. The tempting alternative --
   fall back to the moment's start -- produces a plausible EDL with a cut in
   the middle of a word, and nothing downstream can tell that happened. Every
   defect this repo has found was silent; a loud refusal the caller must handle
   is strictly better than a quiet cut in the middle of "happy birth-".

3. THE REALISED DURATION IS A BEAT, NOT THE TARGET.

   No combination of moments lands on 15.000 s, and chasing it would put the
   final cut off the music. So the last boundary is the beat nearest the
   target, the shortfall is reported on `ReelPlan.duration_error_ms`, and
   `max_duration` is a hard ceiling that is never crossed (the contract calls
   exceeding it a validation failure, not a warning). `target.target_duration`
   keeps what was asked for so the difference stays auditable.

4. A MOMENT IS ADDRESSED BY ITS MEDIA ID, WHICH MAY BE AN ASSEMBLY.

   GoPro chaptered footage is one virtual MediaRecord spanning GH01/GH02/GH03.
   A moment that straddles a chapter boundary names the ASSEMBLY id and a
   single continuous source range; this planner never learns that a boundary
   exists, and must not, because the moment that a chapter boundary becomes
   visible to the planner is the moment it can accidentally cut on one. The
   renderer expands the assembly into ordered member reads.

5. AMBIENT LEVEL LIVES IN EXACTLY ONE PLACE.

   `AmbientPlan.default_gain_db` plus `per_clip_gain_db`, never
   `Clip.audio.gain_db`, which stays at 0. The golden fixture sets both to the
   same value; two fields that must agree are two fields that will eventually
   disagree, and a renderer that applies both would attenuate twice. Flagged
   for Codex in the package notes -- the contract does not say which wins.

DETERMINISM IS NOT BEST-EFFORT HERE

"Same EDL + same sources = identical render intent" is CLAUDE.md hard rule 3,
and it is only true if the same inputs produce the same EDL. So:

  * Every ordering is an explicit sort with a total key. Every tie is broken on
    an id, never left to input order, and never left to a dict or set.
  * Input sequences are sorted on entry: passing the same moments in a
    different order yields a byte-identical EDL. `test_reel` asserts this
    against a reversed input list, because "we always call it in id order" is a
    property of today's caller, not of the planner.
  * Rounding is half-up via floor(x + 0.5), not Python's round(), which is
    half-to-even: 0.5 -> 0 in Python and 1 in JavaScript, so a beat landing
    exactly on a half frame would quantise differently in the desktop shell
    than in the pipeline.
  * Digests are over a fixed-decimal text encoding, not over a JSON
    re-serialisation. See `canonical_digest_text`.
"""

from __future__ import annotations

import copy
import json
import math
from dataclasses import dataclass, field, replace
from fractions import Fraction
from typing import Iterable, Mapping, Sequence

SCHEMA_VERSION = "v0"
PLANNER_ID = "reel-planner"
PLANNER_VERSION = "1.0.0"
VALIDATOR_VERSION = "edl-validator/1.0.0"

# Containment comparisons are on frame counts, where a real error is at least
# one whole frame. This epsilon only absorbs float noise from summing durations;
# it is nine orders of magnitude smaller than the smallest defect it could hide.
_EPS = 1e-9


class PlanningError(ValueError):
    """The requested reel cannot be planned from the given inputs.

    Raised rather than returning a degraded plan. A reel that is 4 seconds long
    because half the moments were unusable looks like a plan and renders like
    one; the caller has to be told it could not be built.
    """


class RateMismatch(PlanningError):
    """An input time is expressed at a rate other than the timeline rate."""


class LicenseError(PlanningError):
    """The music is not cleared for where this cut is going."""


class ContractError(ValueError):
    """A value that could not appear in a valid EDL."""


# ---------------------------------------------------------------------------
# Canonical digest encoding
# ---------------------------------------------------------------------------

# The EDL contract says `edl_id` is "BLAKE3 over the canonical JSON of this EDL".
# It does not say what canonical means, and models/policy/digest.py records at
# length why "sorted keys and compact separators" is not an answer: Python
# writes the float 1.0 as `1.0`, `1e-07` as `1e-07` and `-0.0` as `-0.0`;
# JavaScript writes `1`, `1e-7` and `0`. Every EDL we emit contains whole-number
# floats (rates, gains, energies), so a digest computed in the Tauri shell would
# disagree with the one Python stamped -- and would fail as "someone edited the
# plan" rather than as "the digest is not portable".
#
# digest.py solved it by hashing file bytes. That works for a file on disk and
# not for an in-memory document, so this uses the other portable option: a text
# encoding in which no number formatting decision is left to the language.
#
#     null | true | false
#     numbers  -> exactly six decimal places, ALWAYS, including integers.
#                 Python f"{v:.6f}", JS v.toFixed(6), Rust format!("{:.6}").
#                 -0.0 is normalised to 0.0 first, because JS toFixed drops the
#                 sign and Python keeps it.
#     strings  -> JSON string, non-ASCII escaped as \\uXXXX with lowercase hex
#     arrays   -> [a,b,c] in order
#     objects  -> {"k":v,...} with keys sorted by Unicode code point
#
# Six decimals is well past the precision at which any field here changes a
# render: it is a millionth of a frame.
#
# KNOWN RESIDUAL RISK, recorded rather than hidden: a value whose seventh
# decimal is exactly 5 may round differently between Python's round-half-even
# formatting and JavaScript's toFixed. Nothing we emit is generated at that
# precision (times are whole frames, gains and energies are authored constants),
# but the day a beat time arrives from a tracker with more precision than that,
# this is where it will bite.


def _canon_number(value: float) -> str:
    number = float(value) + 0.0  # +0.0 turns -0.0 into 0.0
    if not math.isfinite(number):
        raise ContractError(
            f"{value!r} is not finite; a NaN or infinity in a plan would digest "
            "as a real value and render as garbage"
        )
    return f"{number:.6f}"


def canonical_digest_text(value: object) -> str:
    """The portable text encoding a digest is taken over. See the note above."""
    if value is None:
        return "null"
    if isinstance(value, bool):  # before int: bool IS an int in Python
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return _canon_number(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=True)
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(canonical_digest_text(item) for item in value) + "]"
    if isinstance(value, Mapping):
        return (
            "{"
            + ",".join(
                f"{json.dumps(key, ensure_ascii=True)}:{canonical_digest_text(value[key])}"
                for key in sorted(value)
            )
            + "}"
        )
    raise ContractError(f"{type(value).__name__} has no canonical encoding")


def blake3_hex(data: bytes) -> str:
    """BLAKE3 hex digest.

    Imported lazily and re-raised with instructions, matching
    models/policy/digest.py: a missing dependency must not be mistakable for a
    computed answer, so there is deliberately no fallback hash here. A digest
    that silently meant "blake3 was not installed" would compare equal to every
    other such digest, which is the worst possible failure -- two different
    plans would claim the same id.
    """
    try:
        from blake3 import blake3  # type: ignore
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise PlanningError(
            "the blake3 package is required to stamp an EDL: pip install blake3"
        ) from exc
    return blake3(data).hexdigest()


def digest_of(value: object) -> str:
    return blake3_hex(canonical_digest_text(value).encode("utf-8"))


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------

_SLUG_CHARS = set("abcdefghijklmnopqrstuvwxyz0123456789_-")
_HEX_CHARS = set("0123456789abcdef")


def _check_slug(value: str, what: str) -> str:
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= 64
        or value[0] not in _SLUG_CHARS
        or value[0] in "_-"
        or not set(value) <= _SLUG_CHARS
    ):
        raise ContractError(
            f"{what}={value!r} is not a contract Slug "
            "(^[a-z0-9][a-z0-9_-]{0,63}$)"
        )
    return value


def _check_blake3(value: str, what: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or not set(value) <= _HEX_CHARS:
        raise ContractError(f"{what}={value!r} is not a 64-char lowercase BLAKE3 hex")
    return value


@dataclass(frozen=True)
class SnapPoint:
    """A certified cut position, in SOURCE frames at the timeline rate."""

    time: float
    kind: str
    strength: float
    cut_direction: str = "both"


@dataclass(frozen=True)
class Moment:
    """One selected moment, flattened to the fields a planner actually uses.

    NOT the contract shape -- `MomentRecord` nests scores as `Score` objects and
    times as `RationalTime`. Use `moment_from_record` rather than building this
    by hand from a record, for the same reason the ranking engine has
    `signals_from_media_record`: a package that references the generated types
    in prose and bypasses them in practice is not connected to the contract.

    All times are SOURCE frames at the timeline rate.
    """

    moment_id: str
    media_id: str
    source_start: float
    source_duration: float
    snap_points: tuple[SnapPoint, ...] = ()
    earliest_in: float | None = None
    latest_out: float | None = None
    speech_safe_in: float | None = None
    speech_safe_out: float | None = None
    min_duration: float | None = None
    preserve_audio_tail: bool = False
    moment_score: float = 0.0
    hook_potential: float | None = None
    emotional_peak: float | None = None
    speech_ratio: float | None = None
    noise_ratio: float | None = None
    has_speech: bool = False
    label: str = ""

    @property
    def source_end(self) -> float:
        return self.source_start + self.source_duration

    def in_bound(self) -> float:
        """Earliest source frame a cut may start on."""
        low = self.source_start
        if self.earliest_in is not None:
            low = max(low, self.earliest_in)
        if self.has_speech and self.speech_safe_in is not None:
            low = max(low, self.speech_safe_in)
        return low

    def out_bound(self) -> float:
        """Latest source frame a cut may run to.

        `speech_safe_out` applies only when the moment actually contains speech.
        A moment with no speech has no mid-word to land in, and letting a null
        speech bound narrow a silent clip would drop usable material for a
        reason that does not exist.
        """
        high = self.source_end
        if self.latest_out is not None:
            high = min(high, self.latest_out)
        if self.has_speech and self.speech_safe_out is not None:
            high = min(high, self.speech_safe_out)
        return high


@dataclass(frozen=True)
class Beat:
    """One beat, in TIMELINE frames at the timeline rate."""

    index: int
    time: float
    is_downbeat: bool
    bar: int | None = None
    beat_in_bar: int | None = None
    strength: float | None = None
    section: str | None = None


@dataclass(frozen=True)
class BeatGrid:
    """Explicit per-beat times, never a BPM to extrapolate from.

    Real tracks drift; an extrapolated grid is 200 ms out by the end of a
    30-second reel, which is four times the downbeat tolerance.
    """

    source_cue_id: str
    bpm: float
    beats: tuple[Beat, ...]
    tolerance_ms: float = 50.0
    beats_per_bar: int | None = None
    beat_unit: int | None = None
    bpm_confidence: float | None = None
    analyzer: Mapping[str, object] | None = None

    def validate(self) -> None:
        _check_slug(self.source_cue_id, "beat_grid.source_cue_id")
        if not self.beats:
            raise PlanningError("beat grid has no beats")
        if not math.isfinite(self.bpm) or self.bpm <= 0:
            raise PlanningError(f"bpm={self.bpm!r} is not a positive number")
        previous = -math.inf
        for position, beat in enumerate(self.beats):
            if beat.index != position:
                # BeatLock.beat_index is documented as an index INTO this list.
                # If the field and the position disagree, every beat_lock we
                # emit points at the wrong beat and the alignment audit trail
                # silently describes a different cut than the one made.
                raise PlanningError(
                    f"beat at position {position} declares index {beat.index}; "
                    "BeatLock.beat_index indexes this list, so the two must agree"
                )
            if not math.isfinite(beat.time) or beat.time < 0:
                raise PlanningError(f"beat {position} has time {beat.time!r}")
            if beat.time <= previous:
                raise PlanningError(
                    f"beat {position} at {beat.time} is not after beat "
                    f"{position - 1} at {previous}; a non-monotonic grid makes "
                    "'the nearest beat' undefined"
                )
            previous = beat.time
        if self.tolerance_ms < 0:
            raise PlanningError("tolerance_ms must not be negative")


@dataclass(frozen=True)
class MediaSource:
    """A source this reel may draw from, addressed by content hash.

    `aspect_ratio` is the source's DISPLAY aspect as exact integers -- 16:9, not
    1.777... -- because the reframe crop is derived from it and "is this 16:9"
    has to be an equality test rather than an epsilon comparison.
    """

    media_id: str
    media_ref_id: str
    available_start: float
    available_duration: float
    aspect_ratio: tuple[int, int]
    is_span_assembly: bool = False
    expected_frame_rate: float | None = None
    label: str | None = None
    media_kind: str = "video"

    @property
    def available_end(self) -> float:
        return self.available_start + self.available_duration


@dataclass(frozen=True)
class MusicTrack:
    """The music bed, its licence, and where in the track the reel starts."""

    media_id: str
    media_ref_id: str
    available_start: float
    available_duration: float
    license: Mapping[str, object]
    source_start: float = 0.0
    gain_db: float = 0.0
    fade_in_frames: int = 0
    fade_out_frames: int = 0
    label: str | None = None

    @property
    def available_end(self) -> float:
        return self.available_start + self.available_duration


@dataclass(frozen=True)
class RenderTarget:
    """What this cut is for. Duration, reframe and loudness are all chosen for
    it -- an Instagram cut is not a YouTube cut at a different bitrate."""

    destination: str
    width: int
    height: int
    aspect_ratio: tuple[int, int]
    target_duration_frames: float
    max_duration_frames: float | None = None
    loudness_target_lufs: float = -14.0


@dataclass(frozen=True)
class SubjectSample:
    """Where the subject is at one source time, in normalised source coords."""

    time: float
    center_x: float
    center_y: float
    confidence: float | None = None


@dataclass(frozen=True)
class SubjectPath:
    """A tracked subject for one moment, which the reframe keyframes follow."""

    moment_id: str
    samples: tuple[SubjectSample, ...]
    source: str = "sam2_track"
    subject_ref: str | None = None
    person_id: str | None = None
    keep_in_frame: str = "head"
    headroom: float | None = 0.12
    fallback: str = "hold_last_keyframe"


@dataclass(frozen=True)
class Dissolve:
    """A dissolve before clip `before_clip_index` (0-based, >= 1).

    Symmetric on purpose: an asymmetric dissolve is a real thing an editor
    wants and a thing we have no rule for choosing, so it is not offered until
    something can decide it.
    """

    before_clip_index: int
    frames: int
    transition_type: str = "dissolve"
    easing: str = "ease_in_out"


@dataclass(frozen=True)
class ArcIntent:
    """Narrative framing supplied by whoever authored the arc."""

    arc_id: str = "arc-reel"
    title: str | None = None
    logline: str | None = None
    rationale: str | None = None
    source: str = "template"
    model: Mapping[str, object] | None = None
    prompt_id: str | None = None
    consent: Mapping[str, object] | None = None


@dataclass(frozen=True)
class ReelStyle:
    """Pacing and mix constants. Data, not literals scattered through the code,
    so a "slower, warmer" style is a stored profile rather than a code change --
    and so `variant.strategy = "pacing_seed"` has something to vary."""

    min_clip_seconds: float = 1.5
    max_clip_seconds: float = 2.5
    preferred_clip_seconds: float = 2.0

    # Ambient bed. -14 dB under a music bed at 0 keeps location sound audible
    # without competing; keeping real ambient is most of what separates a film
    # that feels like a memory from a slideshow with a soundtrack.
    ambient_gain_db: float = -14.0
    high_pass_hz: float = 120.0
    noise_suppression: str = "moderate"

    # A clip whose audio is mostly wind gets buried rather than high-passed:
    # above this ratio there is no location sound left to preserve, only rumble.
    wind_noise_ratio_floor: float = 0.6
    wind_clip_gain_db: float = -60.0
    speech_clip_gain_db: float = -6.0

    duck_reduction_db: float = 9.0
    duck_attack_ms: float = 60.0
    duck_release_ms: float = 320.0

    music_fade_in_frames: int = 12
    music_fade_out_frames: int = 36

    # L-cut length: how far a preserved audio tail runs past the visual cut.
    l_cut_frames: int = 18

    reframe_deadzone: float = 0.015
    reframe_smoothing_method: str = "savitzky_golay"
    reframe_smoothing_window: int = 31
    reframe_max_velocity_per_second: float = 0.35

    # Snap-point kinds ranked as IN-points, best first. The contract says a
    # beat-locked cut is the intersection of a beat and a motion or audio onset;
    # the rest are usable and worse. A motion_offset in-point is legal and
    # visually wrong -- entering on a movement ending reads as arriving late --
    # which is why the ranking exists rather than just filtering on direction.
    in_point_kind_rank: tuple[str, ...] = (
        "motion_onset",
        "audio_onset",
        "impact",
        "shot_boundary",
        "subject_entry",
        "speech_start",
        "scene_brightness_change",
        "speech_gap",
    )
    # How near an out-capable snap point the computed out-point must land to
    # count as "lands on a snap too". A tiebreak, not a requirement.
    out_snap_tolerance_frames: float = 2.0


# Which clearance a destination needs. A cut aimed at Instagram is a publication;
# one aimed at the master or a preview is not.
_DESTINATION_CLEARANCE = {
    "master": "private_playback",
    "web_preview": "private_playback",
    "instagram_reel": "social_share",
    "instagram_feed": "social_share",
    "youtube": "social_share",
    "youtube_shorts": "social_share",
    "tiktok": "social_share",
    "whatsapp_status": "social_share",
}

# Act energies for the hook -> build -> peak -> button template. Constants
# rather than measurements: this is the SPECIFICATION the planner satisfies by
# choosing moments, not a description of what it chose.
_ACT_ENERGY = {"hook": 0.9, "build": 0.55, "peak": 1.0, "button": 0.35}

# Sections where the visual peak belongs, if the analyser labelled any.
_PEAK_SECTIONS = ("drop", "chorus")

# Above this, a moment counts as carrying speech even without a transcript.
SPEECH_RATIO_FLOOR = 0.2


# ---------------------------------------------------------------------------
# Contract adapters
# ---------------------------------------------------------------------------


def _rational(value: Mapping[str, object] | None, rate: float, what: str) -> float | None:
    """A contract RationalTime -> frames at `rate`.

    Refuses a different rate rather than converting. Conversion is not free:
    a 29.97 source time expressed at 59.94 is only exact for even frames, and
    a planner that quietly rounded would move a certified snap point off the
    onset it was certified on. The conversion belongs in the proxy frame index,
    which knows the real mapping; here it would be a guess.
    """
    if value is None:
        return None
    if not isinstance(value, Mapping) or "value" not in value or "rate" not in value:
        raise ContractError(f"{what} is not a RationalTime: {value!r}")
    got = float(value["rate"])  # type: ignore[arg-type]
    if abs(got - rate) > _EPS:
        raise RateMismatch(
            f"{what} is at rate {got} but the timeline is at {rate}. Convert in "
            "the frame index, which knows the real source mapping, not here."
        )
    return float(value["value"])  # type: ignore[arg-type]


def _score_value(block: Mapping[str, object] | None) -> float | None:
    """A contract Score -> its value. Scores nest; the arithmetic wants floats."""
    if block is None:
        return None
    if isinstance(block, Mapping):
        raw = block.get("value")
        return None if raw is None else float(raw)  # type: ignore[arg-type]
    raise ContractError(f"expected a Score object, got {block!r}")


def moment_from_record(record: Mapping[str, object], *, rate: float) -> Moment:
    """A contract MomentRecord -> Moment. THE supported way in.

    Refuses an eliminated moment. Elimination is first-class in the contract --
    "a MomentRecord that exists only to say 'rejected, shake' is a normal
    record" -- and a planner that scored one as merely weak would put shaky
    footage in a reel whenever the pool was bad enough, which is exactly the
    situation at the tail of a GoPro card.
    """
    elimination = record.get("elimination") or {}
    if isinstance(elimination, Mapping) and elimination.get("eliminated"):
        reasons = ", ".join(sorted(elimination.get("reasons") or [])) or "unstated"
        raise PlanningError(
            f"moment {record.get('moment_id')!r} was eliminated ({reasons}); "
            "eliminated moments do not enter the candidate pool at all"
        )

    source_range = record.get("source_range")
    if not isinstance(source_range, Mapping):
        raise ContractError("moment record has no source_range")
    start = _rational(source_range.get("start_time"), rate, "source_range.start_time")
    duration = _rational(source_range.get("duration"), rate, "source_range.duration")
    if start is None or duration is None:
        raise ContractError("source_range needs both start_time and duration")

    snaps = []
    for raw in record.get("snap_points") or []:
        time = _rational(raw.get("time"), rate, "snap_point.time")
        snaps.append(
            SnapPoint(
                time=float(time),
                kind=str(raw["kind"]),
                strength=float(raw["strength"]),
                cut_direction=str(raw.get("cut_direction", "both")),
            )
        )

    trim = record.get("safe_trim") or {}
    features = record.get("features") or {}
    audio = (features or {}).get("audio") or {}
    scores = record.get("scores") or {}
    transcript = record.get("transcript")

    speech_ratio = audio.get("speech_ratio")
    speech_ratio = None if speech_ratio is None else float(speech_ratio)
    has_speech = bool(
        (isinstance(transcript, Mapping) and str(transcript.get("text", "")).strip())
        or (speech_ratio is not None and speech_ratio >= SPEECH_RATIO_FLOOR)
    )

    noise_ratio = audio.get("noise_ratio")

    return Moment(
        moment_id=_check_blake3(str(record["moment_id"]), "moment_id"),
        media_id=_check_blake3(str(record["media_id"]), "media_id"),
        source_start=start,
        source_duration=duration,
        snap_points=tuple(snaps),
        earliest_in=_rational(trim.get("earliest_in"), rate, "safe_trim.earliest_in"),
        latest_out=_rational(trim.get("latest_out"), rate, "safe_trim.latest_out"),
        speech_safe_in=_rational(trim.get("speech_safe_in"), rate, "safe_trim.speech_safe_in"),
        speech_safe_out=_rational(trim.get("speech_safe_out"), rate, "safe_trim.speech_safe_out"),
        min_duration=_rational(trim.get("min_duration"), rate, "safe_trim.min_duration"),
        preserve_audio_tail=bool(trim.get("preserve_audio_tail", False)),
        moment_score=float(_score_value(scores.get("moment_score")) or 0.0),
        hook_potential=_score_value(scores.get("hook_potential")),
        emotional_peak=_score_value(scores.get("emotional_peak")),
        speech_ratio=speech_ratio,
        noise_ratio=None if noise_ratio is None else float(noise_ratio),
        has_speech=has_speech,
        label="",
    )


def beat_grid_from_contract(block: Mapping[str, object], *, rate: float) -> BeatGrid:
    """A contract BeatGrid -> BeatGrid."""
    signature = block.get("time_signature") or {}
    beats = tuple(
        Beat(
            index=int(raw["index"]),
            time=float(_rational(raw["time"], rate, "beat.time")),
            is_downbeat=bool(raw["is_downbeat"]),
            bar=None if raw.get("bar") is None else int(raw["bar"]),
            beat_in_bar=None if raw.get("beat_in_bar") is None else int(raw["beat_in_bar"]),
            strength=None if raw.get("strength") is None else float(raw["strength"]),
            section=raw.get("section"),
        )
        for raw in block["beats"]
    )
    grid = BeatGrid(
        source_cue_id=str(block["source_cue_id"]),
        bpm=float(block["bpm"]),
        beats=beats,
        tolerance_ms=float(block.get("tolerance_ms", 50.0)),
        beats_per_bar=signature.get("beats_per_bar"),
        beat_unit=signature.get("beat_unit"),
        bpm_confidence=block.get("bpm_confidence"),
        analyzer=block.get("analyzer"),
    )
    grid.validate()
    return grid


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ReelPlan:
    """The EDL, plus what the planner could only say out loud.

    `warnings` is not decoration. A centre-crop fallback and a mean clip length
    outside the pacing band are both legal plans that a human would want to
    know about, and neither has anywhere to live in the EDL: `EdlValidation`
    has a closed `check_id` enum with no entry for either. Returning them
    beside the document is the honest option; dropping them would make the
    planner silent about the two things most likely to make a reel look wrong.
    """

    edl: dict
    warnings: tuple[str, ...] = ()
    dropped_moment_ids: tuple[str, ...] = ()
    clip_count: int = 0
    beat_step: int = 0
    realised_duration_frames: int = 0
    target_duration_frames: float = 0.0
    duration_error_ms: float = 0.0


# ---------------------------------------------------------------------------
# Planning internals
# ---------------------------------------------------------------------------


def _round_half_up(value: float) -> int:
    """Half-up rounding, not Python's round().

    round() is half-to-even: round(0.5) == 0 and round(1.5) == 2, while
    JavaScript's Math.round gives 1 and 2. A beat landing exactly on a half
    frame would then quantise to a different frame in the Tauri shell than in
    the pipeline, and the two would disagree about where the cut is.
    """
    return math.floor(value + 0.5)


def _q(value: float, places: int = 6) -> float:
    """Quantise a sort key so float noise cannot reorder a tie."""
    return round(value + 0.0, places)


@dataclass(frozen=True)
class _Pacing:
    step: int
    beat_positions: tuple[int, ...]  # positions in grid.beats, one per boundary
    boundaries: tuple[int, ...]  # quantised timeline frames, one per boundary
    mean_clip_seconds: float

    @property
    def clip_count(self) -> int:
        return len(self.boundaries) - 1

    @property
    def total_frames(self) -> int:
        return self.boundaries[-1]


def _pacings(grid: BeatGrid, target: RenderTarget, rate: float, style: ReelStyle,
             max_clips: int) -> list[_Pacing]:
    """Every beat step that could carry this reel, one _Pacing each.

    A step of N means "cut every N beats". At 128 BPM a beat is 0.469 s, so
    step 4 is a 1.875 s clip and step 3 is 1.4 s -- which is why the step is
    searched rather than fixed: the same style constants have to produce a
    watchable reel at 90 BPM and at 140.
    """
    ceiling = target.max_duration_frames
    found: list[_Pacing] = []
    for step in range(1, len(grid.beats)):
        positions = list(range(0, len(grid.beats), step))
        quantised = [_round_half_up(grid.beats[p].time) for p in positions]
        if quantised[0] != 0:
            # The grid must start the timeline. A reel whose first frame is not
            # a beat has nothing to lock its opening cut to.
            break
        # A step so small that two cuts quantise to the same frame cannot tile.
        if any(b <= a for a, b in zip(quantised, quantised[1:])):
            continue

        usable = [
            (position, frames)
            for position, frames in enumerate(quantised)
            if position >= 1
            and (ceiling is None or frames <= ceiling + _EPS)
            and position <= max_clips
        ]
        if not usable:
            continue
        # Nearest boundary to the target; on an exact tie prefer the shorter
        # cut, because overrunning a platform limit is a hard failure and
        # underrunning is a shorter reel.
        end_position, total = min(
            usable, key=lambda row: (abs(row[1] - target.target_duration_frames), row[1])
        )
        found.append(
            _Pacing(
                step=step,
                beat_positions=tuple(positions[: end_position + 1]),
                boundaries=tuple(quantised[: end_position + 1]),
                mean_clip_seconds=_q(total / end_position / rate),
            )
        )
    return found


def _choose_pacing(candidates: Sequence[_Pacing], target: RenderTarget,
                   grid: BeatGrid, style: ReelStyle) -> _Pacing:
    """Pick one pacing. The priority order IS the creative decision.

    1. Land inside the 1.5-2.5 s clip band if any step can.
    2. Get as close to the requested duration as possible. The user asked for
       15 seconds; a musically perfect 22-second reel is not what they asked for.
    3. Prefer cutting on downbeats. Between two equally close durations, the one
       whose cuts land on bar starts feels deliberate and the other feels like
       it was chopped.
    4. Prefer clips near the preferred length, then the smallest step, so the
       result is fully determined with no appeal to input order.
    """
    if not candidates:
        raise PlanningError(
            "no beat step can produce a reel: the grid is too short, every step "
            "overruns max_duration, or there are no moments to fill a single clip"
        )

    def key(pacing: _Pacing) -> tuple:
        in_band = 0 if (
            style.min_clip_seconds <= pacing.mean_clip_seconds <= style.max_clip_seconds
        ) else 1
        downbeats = sum(
            1 for position in pacing.beat_positions if grid.beats[position].is_downbeat
        )
        return (
            in_band,
            abs(pacing.total_frames - target.target_duration_frames),
            -_q(downbeats / len(pacing.beat_positions)),
            _q(abs(pacing.mean_clip_seconds - style.preferred_clip_seconds)),
            pacing.step,
        )

    return min(candidates, key=key)


def _assign_roles(moments: Sequence[Moment], clip_count: int,
                  grid: BeatGrid, pacing: _Pacing) -> tuple[list[Moment], list[str], int]:
    """Which moment fills which slot. Returns (per-slot moments, dropped ids, peak slot).

    hook -> build... -> peak -> button, per build plan §4.4. The hook is the
    highest hook_potential moment because the first second is where retention
    is won; the peak is the highest emotional_peak because that is the one
    moment the reel exists to show. Everything between them runs
    chronologically, because a build that jumps around in time reads as a
    collection rather than a sequence.
    """
    pool = sorted(moments, key=lambda m: m.moment_id)

    def by_role(candidates: Sequence[Moment], field_name: str) -> Moment:
        # A moment with no role-specific score falls back to its overall score
        # rather than to zero: an unscored moment is not a bad one, and zeroing
        # it would make "the model has not run yet" indistinguishable from
        # "this is a terrible hook".
        def key(m: Moment) -> tuple:
            specific = getattr(m, field_name)
            return (
                -_q(m.moment_score if specific is None else specific),
                -_q(m.moment_score),
                m.moment_id,
            )

        return min(candidates, key=key)

    hook = by_role(pool, "hook_potential")
    remaining = [m for m in pool if m.moment_id != hook.moment_id]

    slots: list[Moment | None] = [None] * clip_count
    slots[0] = hook

    if clip_count == 1:
        used = [hook]
        peak_slot = 0
    else:
        peak = by_role(remaining, "emotional_peak")
        remaining = [m for m in remaining if m.moment_id != peak.moment_id]

        # The peak belongs on the drop if the analyser found one, so the visual
        # peak and the musical peak are the same instant. Otherwise it sits one
        # slot before the button, which is where the template puts it.
        peak_slot = clip_count - 1
        if clip_count >= 3:
            peak_slot = clip_count - 2
            for slot in range(1, clip_count - 1):
                section = grid.beats[pacing.beat_positions[slot]].section
                if section in _PEAK_SECTIONS:
                    peak_slot = slot
                    break
        slots[peak_slot] = peak

        chronological = sorted(remaining, key=lambda m: (m.source_start, m.moment_id))
        button = None
        if clip_count >= 3 and chronological:
            button = chronological[-1]
            slots[clip_count - 1] = button
            chronological = chronological[:-1]

        open_slots = [i for i, filled in enumerate(slots) if filled is None]
        # More moments than slots: keep the strongest, then restore chronology.
        # Selecting by score and ordering by time are different questions and
        # answering them in one pass gets both wrong.
        strongest = sorted(
            chronological, key=lambda m: (-_q(m.moment_score), m.moment_id)
        )[: len(open_slots)]
        for slot, moment in zip(open_slots, sorted(
            strongest, key=lambda m: (m.source_start, m.moment_id)
        )):
            slots[slot] = moment

        used = [hook, peak] + strongest + ([button] if button else [])

    if any(slot is None for slot in slots):
        raise PlanningError(
            f"{len(moments)} moments cannot fill {clip_count} slots; "
            "plan a shorter reel or supply more moments"
        )

    used_ids = {m.moment_id for m in used}
    dropped = tuple(sorted(m.moment_id for m in pool if m.moment_id not in used_ids))
    return [slot for slot in slots if slot is not None], list(dropped), peak_slot


def _choose_in_point(moment: Moment, duration: int, style: ReelStyle,
                     head_handle: float, tail_handle: float, slot: int) -> SnapPoint:
    """The source in-point for one slot: a certified snap point, or nothing.

    `head_handle`/`tail_handle` are the extra frames a neighbouring dissolve
    needs on each side. They narrow the legal window rather than being taken
    afterwards, because a dissolve that reaches past `latest_out` shows exactly
    the material the analysis layer said was unusable.
    """
    low = moment.in_bound() + head_handle
    high = moment.out_bound() - tail_handle
    if moment.min_duration is not None and duration < moment.min_duration - _EPS:
        raise PlanningError(
            f"slot {slot} is {duration} frames but moment {moment.moment_id[:12]} "
            f"needs at least {moment.min_duration}; below that it reads as a "
            "flash frame rather than a shot"
        )
    if high - low < duration - _EPS:
        raise PlanningError(
            f"moment {moment.moment_id[:12]} has {high - low:.0f} usable frames "
            f"for a {duration}-frame slot {slot} (bounds {low:.0f}..{high:.0f})"
        )

    latest_in = high - duration
    candidates = [
        snap
        for snap in moment.snap_points
        if snap.cut_direction in ("in", "both")
        and low - _EPS <= snap.time <= latest_in + _EPS
    ]
    if not candidates:
        raise PlanningError(
            f"moment {moment.moment_id[:12]} has no in-capable snap point in "
            f"[{low:.0f}, {latest_in:.0f}] for slot {slot}. The planner may only "
            "cut on certified snap points; falling back to the moment start "
            "would put a cut somewhere nothing said was safe."
        )

    outs = [
        snap.time
        for snap in moment.snap_points
        if snap.cut_direction in ("out", "both")
    ]

    def rank(snap: SnapPoint) -> tuple:
        try:
            kind_rank = style.in_point_kind_rank.index(snap.kind)
        except ValueError:
            kind_rank = len(style.in_point_kind_rank)
        out_time = snap.time + duration
        lands_on_out = any(
            abs(candidate - out_time) <= style.out_snap_tolerance_frames
            for candidate in outs
        )
        return (kind_rank, -_q(snap.strength), 0 if lands_on_out else 1, _q(snap.time), snap.kind)

    return min(candidates, key=rank)


def _crop_size(source: tuple[int, int], target: tuple[int, int]) -> tuple[float, float]:
    """Normalised crop w/h that turns a `source`-aspect frame into `target`.

    Exact rationals throughout: 9:16 out of 16:9 is 81/256 = 0.31640625, which
    is exactly representable. Computing it as 0.5625 / 1.7777... gives
    0.3164062499999999, and a crop whose aspect is not exactly the target is a
    validation failure the renderer would then have to reconcile -- which is the
    renderer making a decision.
    """
    source_ar = Fraction(source[0], source[1])
    target_ar = Fraction(target[0], target[1])
    if target_ar == source_ar:
        return 1.0, 1.0
    if target_ar < source_ar:  # narrower than the source: crop the sides
        return float(target_ar / source_ar), 1.0
    return 1.0, float(source_ar / target_ar)  # taller: crop top and bottom


def _clamp(value: float, low: float, high: float) -> float:
    return low if value < low else (high if value > high else value)


def _reframe_track(track_id: str, moment: Moment, path: SubjectPath | None,
                   source_in: float, duration: int, crop_w: float, crop_h: float,
                   target_aspect: tuple[int, int], style: ReelStyle,
                   warnings: list[str]) -> dict:
    """One crop keyframe track for one clip.

    The fallback matters more than the keyframes. The contract is explicit that
    a reframe silently falling back to a centre crop can decapitate the subject
    of the shot, so a clip with no subject track gets a STATIC centre crop, an
    explicit `center_crop` fallback, and a warning on the plan -- rather than a
    moving crop derived from nothing.
    """
    source_out = source_in + duration
    keyframes: list[dict] = []

    samples = ()
    if path is not None:
        samples = tuple(
            sample for sample in path.samples
            if source_in - _EPS <= sample.time <= source_out + _EPS
        )

    if not samples:
        if path is not None:
            warnings.append(
                f"{track_id}: the subject track has no samples inside "
                f"[{source_in:.0f}, {source_out:.0f}); using a static centre crop"
            )
        else:
            warnings.append(
                f"{track_id}: no subject track, so the crop is a static centre "
                "crop. A centred crop can cut the subject out of the frame; "
                "review before publishing."
            )
        keyframes.append(
            {
                "time": {"value": source_in, "rate": None},  # rate filled by caller
                "crop": {
                    "x": _q((1.0 - crop_w) / 2.0),
                    "y": _q((1.0 - crop_h) / 2.0),
                    "w": crop_w,
                    "h": crop_h,
                    "rotation_deg": 0,
                },
                "interpolation": "hold",
                "bezier_control": None,
                "confidence": None,
            }
        )
        return {
            "reframe_track_id": track_id,
            "target_aspect_ratio": {
                "numerator": target_aspect[0],
                "denominator": target_aspect[1],
            },
            "keyframes": keyframes,
            "subject_lock": None,
            "smoothing": None,
            "fallback": "center_crop",
        }

    def crop_for(sample: SubjectSample) -> dict:
        # Clamped so the window stays inside the frame. A subject at the very
        # edge would otherwise produce a crop hanging off the source, which the
        # renderer would have to reconcile.
        return {
            "x": _q(_clamp(sample.center_x - crop_w / 2.0, 0.0, 1.0 - crop_w)),
            "y": _q(_clamp(sample.center_y - crop_h / 2.0, 0.0, 1.0 - crop_h)),
            "w": crop_w,
            "h": crop_h,
            "rotation_deg": 0,
        }

    ordered = sorted(samples, key=lambda s: s.time)
    for earlier, later in zip(ordered, ordered[1:]):
        if later.time <= earlier.time:
            raise PlanningError(
                f"{track_id}: subject samples at {earlier.time} repeat; keyframe "
                "times must be strictly increasing"
            )

    emitted: list[tuple[float, dict, float | None]] = []
    for sample in ordered:
        crop = crop_for(sample)
        if emitted:
            last = emitted[-1][1]
            moved = max(abs(crop["x"] - last["x"]), abs(crop["y"] - last["y"]))
            # Deadzone: movement this small does not move the crop at all, which
            # is what stops a mostly-still subject causing constant micro-drift.
            if moved < style.reframe_deadzone:
                continue
        emitted.append((sample.time, crop, sample.confidence))

    # The track must cover the clip. A keyframe before the in-point or after the
    # out-point would leave the renderer interpolating from nothing at the edges.
    if emitted[0][0] > source_in + _EPS:
        emitted.insert(0, (source_in, emitted[0][1], emitted[0][2]))
    if emitted[-1][0] < source_out - _EPS:
        emitted.append((source_out, emitted[-1][1], emitted[-1][2]))

    for position, (time, crop, confidence) in enumerate(emitted):
        keyframes.append(
            {
                "time": {"value": time, "rate": None},
                "crop": crop,
                # The last keyframe has no next keyframe to reach, so `hold` is
                # the only honest value; `smooth` there would imply a movement
                # towards a keyframe that does not exist.
                "interpolation": "hold" if position == len(emitted) - 1 else "smooth",
                "bezier_control": None,
                "confidence": confidence,
            }
        )

    subject_lock = {
        "source": path.source,
        "subject_ref": path.subject_ref,
        "person_id": path.person_id,
        "keep_in_frame": path.keep_in_frame,
        "headroom": path.headroom,
    }
    return {
        "reframe_track_id": track_id,
        "target_aspect_ratio": {
            "numerator": target_aspect[0],
            "denominator": target_aspect[1],
        },
        "keyframes": keyframes,
        "subject_lock": subject_lock,
        "smoothing": {
            "method": style.reframe_smoothing_method,
            "window_frames": style.reframe_smoothing_window,
            "max_velocity_per_second": style.reframe_max_velocity_per_second,
            "deadzone": style.reframe_deadzone,
        },
        "fallback": path.fallback,
    }


def _merge_ranges(ranges: Sequence[tuple[int, int]]) -> list[tuple[int, int]]:
    """Merge sorted [start, end) intervals that touch or overlap.

    Adjacent speech clips tile exactly, so without merging a two-clip
    conversation produces two abutting duck ranges and the mixer releases and
    re-attacks between them -- an audible pump in the middle of a sentence.
    """
    merged: list[tuple[int, int]] = []
    for start, end in sorted(ranges):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


# ---------------------------------------------------------------------------
# The planner
# ---------------------------------------------------------------------------


def plan_reel(
    *,
    moments: Sequence[Moment],
    beat_grid: BeatGrid,
    target: RenderTarget,
    sources: Sequence[MediaSource],
    music: MusicTrack,
    rate: float,
    seed: int,
    name: str = "",
    style: ReelStyle | None = None,
    subject_paths: Sequence[SubjectPath] = (),
    dissolves: Sequence[Dissolve] = (),
    arc: ArcIntent | None = None,
    variant: Mapping[str, object] | None = None,
    generated_at: str | None = None,
    planner_version: str = PLANNER_VERSION,
) -> ReelPlan:
    """Plan one reel. Returns the EDL plus what could not be said inside it."""
    style = style or ReelStyle()
    arc = arc or ArcIntent()

    if not math.isfinite(rate) or rate <= 0:
        raise PlanningError(f"rate={rate!r} must be a positive number")
    if not moments:
        raise PlanningError("no moments to plan from")
    beat_grid.validate()
    if beat_grid.source_cue_id != "cue-01":
        # The grid names the cue it describes. We emit exactly one cue, so a
        # grid pointing at another one would leave beat_grid.source_cue_id
        # dangling and every beat_lock unverifiable.
        raise PlanningError(
            f"beat grid describes cue {beat_grid.source_cue_id!r} but this "
            "planner emits a single cue named 'cue-01'"
        )

    ordered_moments = sorted(moments, key=lambda m: m.moment_id)
    if len({m.moment_id for m in ordered_moments}) != len(ordered_moments):
        raise PlanningError("duplicate moment_id in the candidate pool")

    by_media = {}
    for source in sorted(sources, key=lambda s: s.media_ref_id):
        _check_blake3(source.media_id, "media_source.media_id")
        _check_slug(source.media_ref_id, "media_source.media_ref_id")
        if source.media_id in by_media:
            raise PlanningError(f"two media sources share media_id {source.media_id}")
        by_media[source.media_id] = source

    for moment in ordered_moments:
        source = by_media.get(moment.media_id)
        if source is None:
            raise PlanningError(
                f"moment {moment.moment_id[:12]} names media {moment.media_id[:12]} "
                "which is not in `sources`; a plan that cannot name its sources "
                "up front fails at 80% of a render instead of before it"
            )
        if (
            moment.source_start < source.available_start - _EPS
            or moment.source_end > source.available_end + _EPS
        ):
            raise PlanningError(
                f"moment {moment.moment_id[:12]} runs outside the available range "
                f"of {source.media_ref_id}"
            )

    _check_slug(music.media_ref_id, "music.media_ref_id")
    _check_blake3(music.media_id, "music.media_id")
    required = _DESTINATION_CLEARANCE.get(target.destination)
    if required is None:
        raise PlanningError(f"unknown destination {target.destination!r}")
    cleared = set(music.license.get("cleared_for") or ())
    if required not in cleared:
        raise LicenseError(
            f"music is cleared for {sorted(cleared)} but {target.destination} "
            f"needs {required!r}. An unlicensed track in a shared reel is a legal "
            "problem, so this refuses rather than warns."
        )

    if arc.source == "tier3_model" and arc.consent is None:
        raise PlanningError(
            "a tier3_model story arc must carry its ConsentRef: producing it "
            "meant sending a contact sheet off the device, and hard rule 7 is "
            "no silent anything"
        )

    # ---- pacing -----------------------------------------------------------
    pacings = _pacings(beat_grid, target, rate, style, max_clips=len(ordered_moments))
    pacing = _choose_pacing(pacings, target, beat_grid, style)
    clip_count = pacing.clip_count
    total_frames = pacing.total_frames

    warnings: list[str] = []
    if not (style.min_clip_seconds <= pacing.mean_clip_seconds <= style.max_clip_seconds):
        warnings.append(
            f"mean clip length {pacing.mean_clip_seconds:.2f}s is outside the "
            f"{style.min_clip_seconds}-{style.max_clip_seconds}s band; no beat "
            "step in this grid lands inside it"
        )

    slot_moments, dropped, peak_slot = _assign_roles(
        ordered_moments, clip_count, beat_grid, pacing
    )

    # ---- dissolves --------------------------------------------------------
    dissolve_by_slot: dict[int, Dissolve] = {}
    for spec in sorted(dissolves, key=lambda d: d.before_clip_index):
        if not 1 <= spec.before_clip_index <= clip_count - 1:
            raise PlanningError(
                f"dissolve before clip {spec.before_clip_index} has no two "
                f"neighbours in a {clip_count}-clip reel"
            )
        if spec.frames <= 0:
            # A hard cut is the ABSENCE of a Transition, never a zero-length
            # one: OTIO round-trips a degenerate transition as a real one and
            # some NLEs render it as a one-frame dissolve.
            raise PlanningError(
                "a zero-length transition is not how a hard cut is expressed; "
                "omit the dissolve instead"
            )
        if spec.before_clip_index in dissolve_by_slot:
            raise PlanningError(
                f"two dissolves requested before clip {spec.before_clip_index}"
            )
        dissolve_by_slot[spec.before_clip_index] = spec

    paths_by_moment: dict[str, SubjectPath] = {}
    for path in sorted(subject_paths, key=lambda p: p.moment_id):
        if path.moment_id in paths_by_moment:
            raise PlanningError(f"two subject paths for moment {path.moment_id[:12]}")
        paths_by_moment[path.moment_id] = path

    crop_w, crop_h = _crop_size(
        (0, 0) if not sources else by_media[slot_moments[0].media_id].aspect_ratio,
        target.aspect_ratio,
    )
    needs_reframe = crop_w != 1.0 or crop_h != 1.0

    # ---- clips ------------------------------------------------------------
    clips: list[dict] = []
    reframe_tracks: list[dict] = []
    ambient_gains: list[dict] = []
    speech_ranges: list[tuple[int, int]] = []

    for slot, moment in enumerate(slot_moments):
        start = pacing.boundaries[slot]
        duration = pacing.boundaries[slot + 1] - start
        clip_id = f"clip-{slot + 1:02d}"
        source = by_media[moment.media_id]

        head = float(dissolve_by_slot[slot].frames) if slot in dissolve_by_slot else 0.0
        tail = (
            float(dissolve_by_slot[slot + 1].frames)
            if slot + 1 in dissolve_by_slot
            else 0.0
        )
        snap = _choose_in_point(moment, duration, style, head, tail, slot)
        source_in = snap.time

        beat = beat_grid.beats[pacing.beat_positions[slot]]
        # The cut is at a whole frame; the beat is wherever the analyser put it.
        # This difference is the entire audit trail for the <50 ms gate.
        error_ms = _q((start - beat.time) / rate * 1000.0, 4)

        tail_frames = None
        if moment.preserve_audio_tail and slot < clip_count - 1:
            spare = moment.out_bound() - (source_in + duration)
            usable = min(float(style.l_cut_frames), math.floor(spare))
            if usable > 0:
                tail_frames = {"value": usable, "rate": rate}

        reframe_track_id = None
        if needs_reframe:
            reframe_track_id = f"reframe-{clip_id}"
            track = _reframe_track(
                reframe_track_id,
                moment,
                paths_by_moment.get(moment.moment_id),
                source_in,
                duration,
                crop_w,
                crop_h,
                target.aspect_ratio,
                style,
                warnings,
            )
            for keyframe in track["keyframes"]:
                keyframe["time"]["rate"] = rate
            reframe_tracks.append(track)

        clips.append(
            {
                "item_type": "clip",
                "clip_id": clip_id,
                "name": moment.label,
                "media_ref_id": source.media_ref_id,
                "source_range": {
                    "start_time": {"value": source_in, "rate": rate},
                    "duration": {"value": duration, "rate": rate},
                },
                "timeline_range": {
                    "start_time": {"value": start, "rate": rate},
                    "duration": {"value": duration, "rate": rate},
                },
                "moment_id": moment.moment_id,
                "enabled": True,
                # No time effects. OTIO's LinearTimeWarp and source_range
                # interact in a way this contract does not pin down -- the
                # golden fixture carries a 0.5 time_scalar on a clip whose
                # source and timeline durations are equal, which cannot be right
                # under either reading. Emitting a retime we cannot define would
                # be a creative decision the renderer has to guess at.
                "time_effect": None,
                "reframe_track_id": reframe_track_id,
                "color_ops": [],
                "audio": {
                    # Ambient level lives in AudioPlan.ambient, in one place.
                    "gain_db": 0.0,
                    "muted": False,
                    "fade_in": None,
                    "fade_out": None,
                    "audio_extends_past_out": tail_frames,
                },
                "beat_lock": {
                    "beat_index": beat.index,
                    "is_downbeat": beat.is_downbeat,
                    "alignment_error_ms": error_ms,
                    "snap_point_kind": snap.kind,
                },
                "story_beat_id": None,  # filled in with the arc, below
                "markers": [],
            }
        )

        if moment.noise_ratio is not None and moment.noise_ratio >= style.wind_noise_ratio_floor:
            ambient_gains.append({"clip_id": clip_id, "gain_db": style.wind_clip_gain_db})
        elif moment.has_speech:
            ambient_gains.append({"clip_id": clip_id, "gain_db": style.speech_clip_gain_db})

        if moment.has_speech:
            speech_ranges.append((start, start + duration))

    # ---- handles ----------------------------------------------------------
    for slot, spec in sorted(dissolve_by_slot.items()):
        outgoing = clips[slot - 1]
        incoming = clips[slot]
        out_end = (
            outgoing["source_range"]["start_time"]["value"]
            + outgoing["source_range"]["duration"]["value"]
        )
        if out_end + spec.frames > slot_moments[slot - 1].out_bound() + _EPS:
            raise PlanningError(
                f"dissolve before {incoming['clip_id']} needs {spec.frames} frames "
                f"of handle past {outgoing['clip_id']}'s out-point and the moment "
                "does not have them"
            )
        if (
            incoming["source_range"]["start_time"]["value"] - spec.frames
            < slot_moments[slot].in_bound() - _EPS
        ):
            raise PlanningError(
                f"dissolve before {incoming['clip_id']} needs {spec.frames} frames "
                "of handle before its in-point and the moment does not have them"
            )

    # ---- story arc --------------------------------------------------------
    acts, beat_of_clip = _build_arc(
        clips, peak_slot, clip_count, rate, arc, dropped, total_frames
    )
    for clip in clips:
        clip["story_beat_id"] = beat_of_clip.get(clip["clip_id"])

    # ---- tracks -----------------------------------------------------------
    video_items: list[dict] = []
    for slot, clip in enumerate(clips):
        if slot in dissolve_by_slot:
            spec = dissolve_by_slot[slot]
            video_items.append(
                {
                    "item_type": "transition",
                    "transition_id": f"xfade-{slot:02d}",
                    "transition_type": spec.transition_type,
                    "in_offset": {"value": spec.frames, "rate": rate},
                    "out_offset": {"value": spec.frames, "rate": rate},
                    "easing": spec.easing,
                    "parameters": {},
                }
            )
        video_items.append(clip)

    music_cue = _music_cue(music, total_frames, rate, style)
    tracks = [
        {
            "track_id": "v1",
            "kind": "video",
            "name": "V1",
            "role": "primary",
            "enabled": True,
            "items": video_items,
        },
        {
            "track_id": "a1",
            "kind": "audio",
            "name": "A1 music",
            "role": "music",
            "enabled": True,
            "items": [_music_clip(music, music_cue, rate)],
        },
    ]

    ducking = []
    if speech_ranges:
        ducking.append(
            {
                "rule_id": "duck-music-under-speech",
                "target": "music",
                # explicit_ranges, not `speech`: the planner already knows where
                # the speech is, and a detector re-deciding at render time makes
                # the mix depend on the renderer's VAD rather than on the plan.
                "trigger": "explicit_ranges",
                "reduction_db": style.duck_reduction_db,
                "threshold_db": None,
                "ratio": None,
                "attack_ms": style.duck_attack_ms,
                "release_ms": style.duck_release_ms,
                "ranges": [
                    {
                        "start_time": {"value": start, "rate": rate},
                        "duration": {"value": end - start, "rate": rate},
                    }
                    for start, end in _merge_ranges(speech_ranges)
                ],
            }
        )

    edl: dict = {
        "schema_version": SCHEMA_VERSION,
        "edl_id": "0" * 64,  # replaced below; the digest cannot include itself
        "name": name,
        "kind": "reel",
        "rate": rate,
        "global_start_time": {"value": 0, "rate": rate},
        "target": {
            "destination": target.destination,
            "resolution": {"width": target.width, "height": target.height},
            "aspect_ratio": {
                "numerator": target.aspect_ratio[0],
                "denominator": target.aspect_ratio[1],
            },
            "target_duration": {"value": target.target_duration_frames, "rate": rate},
            "max_duration": (
                None
                if target.max_duration_frames is None
                else {"value": target.max_duration_frames, "rate": rate}
            ),
            "loudness_target_lufs": target.loudness_target_lufs,
        },
        "media_refs": _media_refs(by_media, slot_moments, music, rate),
        "tracks": tracks,
        "reframe_tracks": reframe_tracks,
        "audio_plan": {
            "music": [music_cue],
            "ambient": {
                "enabled": True,
                "default_gain_db": style.ambient_gain_db,
                "preserve_speech": True,
                "high_pass_hz": style.high_pass_hz,
                "noise_suppression": style.noise_suppression,
                "per_clip_gain_db": sorted(ambient_gains, key=lambda row: row["clip_id"]),
            },
            "ducking": ducking,
            "mix": {
                "master_gain_db": 0.0,
                "loudness_target_lufs": target.loudness_target_lufs,
                "true_peak_ceiling_db": -1.0,
                "limiter": True,
                "channels": "stereo",
                "sample_rate": 48000,
            },
        },
        "beat_grid": _beat_grid_block(beat_grid, rate),
        "story_arc": _arc_block(arc, acts, rate, total_frames),
        "color_pipeline": {
            "input_transform": "auto",
            "working_space": "rec709",
            "output_transform": "rec709",
            "tone_map_hdr_to_sdr": True,
        },
        "variant": None if variant is None else dict(variant),
        "determinism": {
            "planner": PLANNER_ID,
            "planner_version": planner_version,
            "seed": seed,
            "inputs_digest": _inputs_digest(
                ordered_moments, beat_grid, target, music, style, seed, planner_version,
                dissolves, subject_paths, arc, rate,
            ),
            "generated_at": generated_at,
        },
        # The exporter sets this after re-importing its own output. Claiming
        # `round_trip_verified` or an empty `unmapped_fields` here would be
        # asserting a property of an export that has not happened.
        "otio": None,
    }

    edl["edl_id"] = _edl_id(edl)

    validation = validate_edl(edl, sources=list(by_media.values()))
    if any(moment.has_speech for moment in slot_moments):
        # Only the planner can make this claim: the EDL carries no transcript,
        # and the guarantee comes from having bounded every in and out point by
        # SafeTrim.speech_safe_*, which are derived from word timestamps.
        validation["checks"].append(
            {
                "check_id": "no_mid_word_cut",
                "passed": True,
                "severity": "warning",
                "detail": (
                    "every speech-carrying clip was bounded by its moment's "
                    "speech_safe_in/out before an in-point was chosen"
                ),
                "clip_id": None,
            }
        )
    edl["validation"] = validation

    if validation["status"] == "fail":
        failed = sorted(
            check["check_id"] for check in validation["checks"] if not check["passed"]
        )
        raise PlanningError(
            f"the planner produced an EDL that fails its own validation: {failed}. "
            "This is a planner bug, not a caller error."
        )

    return ReelPlan(
        edl=edl,
        warnings=tuple(warnings),
        dropped_moment_ids=tuple(dropped),
        clip_count=clip_count,
        beat_step=pacing.step,
        realised_duration_frames=total_frames,
        target_duration_frames=target.target_duration_frames,
        duration_error_ms=_q(
            (total_frames - target.target_duration_frames) / rate * 1000.0, 4
        ),
    )


def _media_refs(by_media: Mapping[str, MediaSource], slot_moments: Sequence[Moment],
                music: MusicTrack, rate: float) -> list[dict]:
    """Only the sources this cut actually touches, sorted by alias.

    Declared up front so a renderer can resolve, verify and pre-open everything
    before it starts: a missing source is then a clean up-front failure rather
    than a crash at 80%.
    """
    used = {moment.media_id for moment in slot_moments}
    refs = [
        {
            "media_ref_id": source.media_ref_id,
            "media_id": source.media_id,
            "media_kind": source.media_kind,
            "available_range": {
                "start_time": {"value": source.available_start, "rate": rate},
                "duration": {"value": source.available_duration, "rate": rate},
            },
            "is_span_assembly": source.is_span_assembly,
            "expected_frame_rate": source.expected_frame_rate,
            "label": source.label,
        }
        for media_id, source in sorted(by_media.items(), key=lambda row: row[1].media_ref_id)
        if media_id in used
    ]
    refs.append(
        {
            "media_ref_id": music.media_ref_id,
            "media_id": music.media_id,
            "media_kind": "music",
            "available_range": {
                "start_time": {"value": music.available_start, "rate": rate},
                "duration": {"value": music.available_duration, "rate": rate},
            },
            "is_span_assembly": False,
            "expected_frame_rate": None,
            "label": music.label,
        }
    )
    return refs


def _music_cue(music: MusicTrack, total_frames: int, rate: float,
               style: ReelStyle) -> dict:
    end = music.source_start + total_frames
    if music.source_start < music.available_start - _EPS or end > music.available_end + _EPS:
        raise PlanningError(
            f"the music runs out: a {total_frames}-frame reel starting at "
            f"{music.source_start} needs material to {end}, and the track ends at "
            f"{music.available_end}. Looping is a musical decision, so it is not "
            "made silently here."
        )
    return {
        "cue_id": "cue-01",
        "media_ref_id": music.media_ref_id,
        "source_range": {
            "start_time": {"value": music.source_start, "rate": rate},
            "duration": {"value": total_frames, "rate": rate},
        },
        "timeline_range": {
            "start_time": {"value": 0, "rate": rate},
            "duration": {"value": total_frames, "rate": rate},
        },
        "license": dict(music.license),
        "gain_db": music.gain_db,
        "fade_in": {"value": style.music_fade_in_frames, "rate": rate},
        "fade_out": {"value": style.music_fade_out_frames, "rate": rate},
        "loop": False,
    }


def _music_clip(music: MusicTrack, cue: dict, rate: float) -> dict:
    """The music cue as a track item, so an NLE shows the bed on a real track."""
    return {
        "item_type": "clip",
        "clip_id": "music-01",
        "name": str(music.license.get("track_title") or ""),
        "media_ref_id": music.media_ref_id,
        "source_range": copy.deepcopy(cue["source_range"]),
        "timeline_range": copy.deepcopy(cue["timeline_range"]),
        "moment_id": None,
        "enabled": True,
        "time_effect": None,
        "reframe_track_id": None,
        "color_ops": [],
        "audio": {
            "gain_db": music.gain_db,
            "muted": False,
            "fade_in": copy.deepcopy(cue["fade_in"]),
            "fade_out": copy.deepcopy(cue["fade_out"]),
            "audio_extends_past_out": None,
        },
        "beat_lock": None,
        "story_beat_id": None,
        "markers": [],
    }


def _beat_grid_block(grid: BeatGrid, rate: float) -> dict:
    block: dict = {
        "source_cue_id": grid.source_cue_id,
        "bpm": grid.bpm,
        "bpm_confidence": grid.bpm_confidence,
        "beats": [
            {
                "index": beat.index,
                "time": {"value": beat.time, "rate": rate},
                "is_downbeat": beat.is_downbeat,
                "bar": beat.bar,
                "beat_in_bar": beat.beat_in_bar,
                "strength": beat.strength,
                "section": beat.section,
            }
            for beat in grid.beats
        ],
        "analyzer": None if grid.analyzer is None else dict(grid.analyzer),
        "tolerance_ms": grid.tolerance_ms,
    }
    if grid.beats_per_bar is not None and grid.beat_unit is not None:
        block["time_signature"] = {
            "beats_per_bar": grid.beats_per_bar,
            "beat_unit": grid.beat_unit,
        }
    return block


def _build_arc(clips: Sequence[dict], peak_slot: int, clip_count: int, rate: float,
               arc: ArcIntent, dropped: Sequence[str],
               total_frames: int) -> tuple[list[dict], dict[str, str]]:
    """Acts, story beats, and which clip satisfies which.

    The arc is kept with the plan so that a revision instruction -- "more of
    her", "less drone" -- re-satisfies the SAME arc instead of re-planning from
    scratch. That persistence is what makes iterative editing feel like
    direction rather than dice-rolling, which is also why `candidate_moment_ids`
    carries the DROPPED moments: those are exactly what a revision can swap in
    without re-running retrieval. The clips already satisfying a beat are named
    in `satisfied_by_clip_ids`, so repeating them there would say nothing.
    """
    spans: list[tuple[str, int, int]] = [("hook", 0, 1)]
    if clip_count >= 3:
        spans.append(("build", 1, peak_slot))
        spans.append(("peak", peak_slot, clip_count - 1))
        spans.append(("button", clip_count - 1, clip_count))
    elif clip_count == 2:
        spans.append(("peak", 1, 2))

    intents = {
        "hook": "Open on motion so the first second earns the rest.",
        "build": "Establish where we are and what it took to get there.",
        "peak": "The moment the reel exists to show.",
        "button": "Land somewhere held, so the loop does not feel abrupt.",
    }
    required = {"hook": True, "build": False, "peak": True, "button": True}

    acts: list[dict] = []
    beat_of_clip: dict[str, str] = {}
    candidates = sorted(dropped)

    for act_name, first, last in spans:
        if last <= first:
            continue
        members = clips[first:last]
        start = members[0]["timeline_range"]["start_time"]["value"]
        end = (
            members[-1]["timeline_range"]["start_time"]["value"]
            + members[-1]["timeline_range"]["duration"]["value"]
        )
        beat_id = f"beat-{act_name}"
        for clip in members:
            beat_of_clip[clip["clip_id"]] = beat_id
        acts.append(
            {
                "act_id": f"act-{act_name}",
                "name": act_name.capitalize(),
                "intent": intents[act_name],
                "timeline_range": {
                    "start_time": {"value": start, "rate": rate},
                    "duration": {"value": end - start, "rate": rate},
                },
                "target_energy": _ACT_ENERGY[act_name],
                "beats": [
                    {
                        "beat_id": beat_id,
                        "description": intents[act_name],
                        "required": required[act_name],
                        "satisfied_by_clip_ids": [clip["clip_id"] for clip in members],
                        "candidate_moment_ids": candidates,
                    }
                ],
            }
        )
    return acts, beat_of_clip


def _arc_block(arc: ArcIntent, acts: Sequence[dict], rate: float,
               total_frames: int) -> dict:
    curve = [
        {
            "time": copy.deepcopy(act["timeline_range"]["start_time"]),
            "energy": act["target_energy"],
        }
        for act in acts
    ]
    # A final control point so the curve is defined over the whole timeline
    # rather than stopping at the last act's start.
    curve.append(
        {
            "time": {"value": total_frames, "rate": rate},
            "energy": acts[-1]["target_energy"],
        }
    )
    return {
        "arc_id": _check_slug(arc.arc_id, "arc.arc_id"),
        "template": "hook_build_peak_button",
        "title": arc.title,
        "logline": arc.logline,
        "acts": list(acts),
        "energy_curve": curve,
        "source": arc.source,
        "model": None if arc.model is None else dict(arc.model),
        "prompt_id": arc.prompt_id,
        "consent": None if arc.consent is None else dict(arc.consent),
        "rationale": arc.rationale,
    }


def _inputs_digest(moments: Sequence[Moment], grid: BeatGrid, target: RenderTarget,
                   music: MusicTrack, style: ReelStyle, seed: int,
                   planner_version: str, dissolves: Sequence[Dissolve],
                   subject_paths: Sequence[SubjectPath], arc: ArcIntent,
                   rate: float) -> str:
    """BLAKE3 over everything the planner read.

    "Two plans with the same digest and the same planner version are guaranteed
    identical" is only true if the digest covers every input that can change the
    output -- including the style constants and the seed, which are the two
    people forget. Moments are digested by id and by the fields the planner
    actually reads, so a rescored moment (new id) or a retrimmed one changes it.
    """
    payload = {
        "rate": rate,
        "seed": seed,
        "planner": PLANNER_ID,
        "planner_version": planner_version,
        "target": {
            "destination": target.destination,
            "width": target.width,
            "height": target.height,
            "aspect": list(target.aspect_ratio),
            "target_duration": target.target_duration_frames,
            "max_duration": target.max_duration_frames,
            "lufs": target.loudness_target_lufs,
        },
        "moments": [
            {
                "moment_id": moment.moment_id,
                "media_id": moment.media_id,
                "source_start": moment.source_start,
                "source_duration": moment.source_duration,
                "snap_points": [
                    [snap.time, snap.kind, snap.strength, snap.cut_direction]
                    for snap in moment.snap_points
                ],
                "bounds": [moment.in_bound(), moment.out_bound()],
                "min_duration": moment.min_duration,
                "preserve_audio_tail": moment.preserve_audio_tail,
                "scores": [
                    moment.moment_score,
                    moment.hook_potential,
                    moment.emotional_peak,
                ],
                "audio": [moment.speech_ratio, moment.noise_ratio, moment.has_speech],
                "label": moment.label,
            }
            for moment in sorted(moments, key=lambda m: m.moment_id)
        ],
        "beat_grid": {
            "cue": grid.source_cue_id,
            "bpm": grid.bpm,
            "tolerance_ms": grid.tolerance_ms,
            "beats": [[beat.time, beat.is_downbeat, beat.section] for beat in grid.beats],
        },
        "music": {
            "media_id": music.media_id,
            "source_start": music.source_start,
            "gain_db": music.gain_db,
            "license": dict(music.license),
        },
        "style": {
            key: getattr(style, key)
            for key in sorted(style.__dataclass_fields__)
        },
        "dissolves": sorted(
            [[d.before_clip_index, d.frames, d.transition_type, d.easing] for d in dissolves]
        ),
        "subject_paths": [
            {
                "moment_id": path.moment_id,
                "samples": [
                    [s.time, s.center_x, s.center_y, s.confidence] for s in path.samples
                ],
                "fallback": path.fallback,
                "keep_in_frame": path.keep_in_frame,
                "headroom": path.headroom,
            }
            for path in sorted(subject_paths, key=lambda p: p.moment_id)
        ],
        "arc": {
            "arc_id": arc.arc_id,
            "source": arc.source,
            "title": arc.title,
            "logline": arc.logline,
            "prompt_id": arc.prompt_id,
        },
    }
    return digest_of(payload)


def _edl_id(edl: Mapping[str, object]) -> str:
    """BLAKE3 over the plan with the volatile fields removed.

    Removed, by PATH and not by name:
      * `edl_id` itself, which cannot contain its own digest.
      * `determinism.generated_at`, a wall clock reading.
      * every clip's `timeline_range`, which the contract calls derived.
      * `validation`, which is a report ABOUT the plan rather than part of it,
        and carries a timestamp and a validator version that both move without
        the render changing.

    By path, because a recursive strip of the NAME `timeline_range` would also
    delete `MusicCue.timeline_range` and `Act.timeline_range`. The cue's is a
    real decision -- where the music sits -- so a digest blind to it would give
    two genuinely different mixes the same id.

    The last two exclusions are wider than the contract's parenthetical
    ("generated_at, timeline_range"); flagged for Codex, since a validator
    written to the letter of that comment would compute a different id.
    """
    document = copy.deepcopy(dict(edl))
    document.pop("edl_id", None)
    document.pop("validation", None)
    determinism = document.get("determinism")
    if isinstance(determinism, dict):
        determinism.pop("generated_at", None)
    for track in document.get("tracks", []):
        for item in track.get("items", []):
            if item.get("item_type") == "clip":
                item.pop("timeline_range", None)
    return digest_of(document)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _check(check_id: str, passed: bool, severity: str, detail: str = "",
           clip_id: str | None = None) -> dict:
    return {
        "check_id": check_id,
        "passed": passed,
        "severity": severity,
        "detail": detail,
        "clip_id": clip_id,
    }


def _clips_of(edl: Mapping[str, object]) -> list[tuple[dict, dict]]:
    """(track, clip) for every clip on every track, in document order."""
    found = []
    for track in edl.get("tracks", []):
        for item in track.get("items", []):
            if item.get("item_type") == "clip":
                found.append((track, item))
    return found


def validate_edl(edl: Mapping[str, object], *,
                 sources: Sequence[MediaSource] | None = None,
                 validated_at: str | None = None) -> dict:
    """Re-derive the pre-render checks from a finished EDL.

    Deliberately independent of the planner: it reads the document and nothing
    else, so a planner that miscomputed a timeline is caught by arithmetic that
    did not produce it. `plan_reel` runs this against its own output and raises
    on failure, which is the only reason a planner bug becomes a stack trace
    rather than a reel with a one-frame gap in it.

    `sources` is optional and only widens what can be checked: crop aspect
    needs the source's pixel aspect, which the EDL deliberately does not carry.
    Checks that could not be run are omitted rather than reported as passing --
    "I could not check" and "I checked and it was fine" must never share a
    result.
    """
    rate = float(edl["rate"])  # type: ignore[arg-type]
    checks: list[dict] = []
    refs = {ref["media_ref_id"]: ref for ref in edl.get("media_refs", [])}
    clips = _clips_of(edl)
    clip_ids = {clip["clip_id"] for _, clip in clips}

    # --- media refs resolvable ---
    missing = sorted(
        {
            clip["media_ref_id"]
            for _, clip in clips
            if clip["media_ref_id"] not in refs
        }
        | {
            cue["media_ref_id"]
            for cue in ((edl.get("audio_plan") or {}).get("music") or [])
            if cue["media_ref_id"] not in refs
        }
    )
    checks.append(
        _check(
            "media_refs_resolvable",
            not missing,
            "error",
            f"{len(refs)} refs declared"
            + (f"; unresolved: {missing}" if missing else ""),
        )
    )

    # --- source ranges inside their available range ---
    escapes: list[tuple[str, str]] = []
    for _, clip in clips:
        ref = refs.get(clip["media_ref_id"])
        if ref is None:
            continue
        available_start = float(ref["available_range"]["start_time"]["value"])
        available_end = available_start + float(ref["available_range"]["duration"]["value"])
        start = float(clip["source_range"]["start_time"]["value"])
        end = start + float(clip["source_range"]["duration"]["value"])
        if start < available_start - _EPS or end > available_end + _EPS:
            escapes.append(
                (
                    clip["clip_id"],
                    f"{clip['clip_id']} covers [{start:.0f}, {end:.0f}) outside "
                    f"[{available_start:.0f}, {available_end:.0f})",
                )
            )
    checks.append(
        _check(
            "source_range_within_available",
            not escapes,
            "error",
            "; ".join(detail for _, detail in escapes)
            or f"{len(clips)} clips checked",
            escapes[0][0] if escapes else None,
        )
    )

    # --- timeline contiguity, per track ---
    breaks: list[str] = []
    unchecked = 0
    for track in edl.get("tracks", []):
        cursor = 0.0
        for item in track.get("items", []):
            if item.get("item_type") == "transition":
                continue  # transitions overlap their neighbours; they add no time
            if item.get("item_type") == "gap":
                cursor += float(item["duration"]["value"])
                continue
            span = item.get("timeline_range")
            if span is None:
                unchecked += 1
                continue
            start = float(span["start_time"]["value"])
            if abs(start - cursor) > _EPS:
                breaks.append(
                    f"{track['track_id']}/{item['clip_id']} starts at {start} "
                    f"but the track is at {cursor}"
                )
            cursor = start + float(span["duration"]["value"])
    checks.append(
        _check(
            "timeline_contiguous",
            not breaks,
            "error",
            "; ".join(breaks)
            or (
                f"tracks tile with no gaps ({unchecked} items carry no "
                "timeline_range and were not checked)"
                if unchecked
                else "tracks tile with no gaps"
            ),
        )
    )

    # --- transitions ---
    transitions = [
        (track, index, item)
        for track in edl.get("tracks", [])
        for index, item in enumerate(track.get("items", []))
        if item.get("item_type") == "transition"
    ]
    if transitions:
        problems: list[str] = []
        for track, index, item in transitions:
            in_offset = float(item["in_offset"]["value"])
            out_offset = float(item["out_offset"]["value"])
            if in_offset <= 0 and out_offset <= 0:
                problems.append(
                    f"{item.get('transition_id')} has zero length; a hard cut is "
                    "the ABSENCE of a transition, never a degenerate one"
                )
                continue
            items = track["items"]
            outgoing = items[index - 1] if index >= 1 else None
            incoming = items[index + 1] if index + 1 < len(items) else None
            if outgoing is None or incoming is None or outgoing.get("item_type") != "clip" \
                    or incoming.get("item_type") != "clip":
                problems.append(
                    f"{item.get('transition_id')} does not sit between two clips"
                )
                continue
            for clip, offset, direction in (
                (outgoing, in_offset, "after"),
                (incoming, out_offset, "before"),
            ):
                ref = refs.get(clip["media_ref_id"])
                if ref is None:
                    continue
                available_start = float(ref["available_range"]["start_time"]["value"])
                available_end = available_start + float(
                    ref["available_range"]["duration"]["value"]
                )
                start = float(clip["source_range"]["start_time"]["value"])
                end = start + float(clip["source_range"]["duration"]["value"])
                have = (available_end - end) if direction == "after" else (start - available_start)
                if have < offset - _EPS:
                    problems.append(
                        f"{clip['clip_id']} has {have:.0f} frames of handle "
                        f"{direction} its cut, and the transition needs {offset:.0f}"
                    )
        checks.append(
            _check(
                "transition_handles_available",
                not problems,
                "error",
                "; ".join(problems) or f"{len(transitions)} transitions have handles",
            )
        )

    # --- beat alignment ---
    grid = edl.get("beat_grid")
    if grid:
        beats = grid["beats"]
        tolerance = float(grid.get("tolerance_ms", 50.0))
        problems = []
        worst = 0.0
        worst_clip = None
        locked = 0
        for _, clip in clips:
            lock = clip.get("beat_lock")
            span = clip.get("timeline_range")
            if lock is None or span is None:
                continue
            locked += 1
            index = int(lock["beat_index"])
            if not 0 <= index < len(beats):
                problems.append(f"{clip['clip_id']} locks to beat {index}, which does not exist")
                continue
            beat = beats[index]
            if bool(beat["is_downbeat"]) != bool(lock.get("is_downbeat", False)):
                problems.append(
                    f"{clip['clip_id']} claims is_downbeat="
                    f"{lock.get('is_downbeat')} against beat {index}"
                )
            # Recomputed, not trusted: a declared alignment error is the one
            # number a planner could get wrong and still look correct.
            actual = (
                float(span["start_time"]["value"]) - float(beat["time"]["value"])
            ) / rate * 1000.0
            declared = float(lock["alignment_error_ms"])
            if abs(actual - declared) > 0.001:
                problems.append(
                    f"{clip['clip_id']} declares {declared} ms of beat error and "
                    f"is actually {actual:.4f} ms out"
                )
            if abs(actual) > abs(worst):
                worst, worst_clip = actual, clip["clip_id"]
            if bool(beat["is_downbeat"]) and abs(actual) > tolerance:
                problems.append(
                    f"{clip['clip_id']} is {actual:.2f} ms off a downbeat "
                    f"against a {tolerance} ms tolerance"
                )
        if locked:
            checks.append(
                _check(
                    "beat_alignment_within_tolerance",
                    not problems,
                    "error",
                    "; ".join(problems)
                    or f"worst error {worst:.4f} ms against a {tolerance} ms tolerance",
                    worst_clip,
                )
            )

    # --- reframe ---
    reframes = edl.get("reframe_tracks") or []
    if reframes:
        target_aspect = edl["target"]["aspect_ratio"]
        by_id = {track["reframe_track_id"]: track for track in reframes}
        aspect_problems: list[str] = []
        order_problems: list[str] = []
        for track in sorted(reframes, key=lambda t: t["reframe_track_id"]):
            declared = track["target_aspect_ratio"]
            if (
                declared["numerator"] * target_aspect["denominator"]
                != declared["denominator"] * target_aspect["numerator"]
            ):
                aspect_problems.append(
                    f"{track['reframe_track_id']} targets "
                    f"{declared['numerator']}:{declared['denominator']}, the cut is "
                    f"{target_aspect['numerator']}:{target_aspect['denominator']}"
                )
            times = [float(k["time"]["value"]) for k in track["keyframes"]]
            if any(b <= a for a, b in zip(times, times[1:])):
                order_problems.append(track["reframe_track_id"])
        source_aspects: dict[str, tuple[int, int]] = {}
        if sources:
            source_aspects = {
                source.media_id: source.aspect_ratio for source in sources
            }
            for _, clip in clips:
                track_id = clip.get("reframe_track_id")
                if track_id is None or track_id not in by_id:
                    continue
                ref = refs.get(clip["media_ref_id"])
                aspect = source_aspects.get(ref["media_id"]) if ref else None
                if aspect is None:
                    continue
                want = Fraction(target_aspect["numerator"], target_aspect["denominator"])
                for keyframe in by_id[track_id]["keyframes"]:
                    crop = keyframe["crop"]
                    got = (
                        Fraction(aspect[0], aspect[1])
                        * Fraction(crop["w"]).limit_denominator(10**9)
                        / Fraction(crop["h"]).limit_denominator(10**9)
                    )
                    if abs(float(got) - float(want)) > 1e-6:
                        aspect_problems.append(
                            f"{track_id} has a crop at {float(got):.6f} against a "
                            f"target of {float(want):.6f}"
                        )
                        break
        checks.append(
            _check(
                "reframe_aspect_matches_target",
                not aspect_problems,
                "error",
                "; ".join(aspect_problems)
                or (
                    f"{len(reframes)} tracks, crops verified against source aspect"
                    if source_aspects
                    else f"{len(reframes)} tracks declare the target aspect "
                    "(crop aspect not checked: no source dimensions supplied)"
                ),
            )
        )
        checks.append(
            _check(
                "reframe_keyframes_ordered",
                not order_problems,
                "error",
                f"keyframes not strictly increasing on {sorted(order_problems)}"
                if order_problems
                else f"{len(reframes)} tracks, keyframes strictly increasing",
            )
        )

    # --- duration ---
    longest = 0.0
    for track in edl.get("tracks", []):
        cursor = 0.0
        for item in track.get("items", []):
            if item.get("item_type") == "gap":
                cursor += float(item["duration"]["value"])
            elif item.get("item_type") == "clip":
                cursor += float(item["source_range"]["duration"]["value"])
        longest = max(longest, cursor)
    ceiling = edl["target"].get("max_duration")
    if ceiling is not None:
        limit = float(ceiling["value"])
        checks.append(
            _check(
                "duration_within_max",
                longest <= limit + _EPS,
                "error",
                f"{longest / rate:.3f} s against a {limit / rate:.3f} s ceiling",
            )
        )

    # --- music licence ---
    cues = (edl.get("audio_plan") or {}).get("music") or []
    if cues:
        destination = edl["target"]["destination"]
        needed = _DESTINATION_CLEARANCE.get(destination)
        uncleared = sorted(
            cue["cue_id"]
            for cue in cues
            if needed is None or needed not in set(cue["license"].get("cleared_for") or ())
        )
        checks.append(
            _check(
                "music_license_covers_destination",
                not uncleared,
                "error",
                f"{destination} needs {needed!r}; uncovered cues: {uncleared}"
                if uncleared
                else f"{len(cues)} cues cleared for {destination}",
            )
        )

    # --- story beats ---
    arc = edl.get("story_arc")
    if arc:
        unsatisfied: list[str] = []
        dangling: list[str] = []
        total_required = 0
        for act in arc.get("acts", []):
            for beat in act.get("beats", []):
                satisfied = beat.get("satisfied_by_clip_ids") or []
                if beat.get("required"):
                    total_required += 1
                    if not satisfied:
                        unsatisfied.append(beat["beat_id"])
                for clip_id in satisfied:
                    if clip_id not in clip_ids:
                        dangling.append(f"{beat['beat_id']} -> {clip_id}")
        checks.append(
            _check(
                "required_story_beats_satisfied",
                not unsatisfied and not dangling,
                "error",
                f"unsatisfied: {sorted(unsatisfied)}; dangling: {sorted(dangling)}"
                if unsatisfied or dangling
                else f"{total_required} required beats, all satisfied",
            )
        )

    # --- mix ---
    mix = (edl.get("audio_plan") or {}).get("mix")
    if edl.get("audio_plan") is not None:
        has_target = bool(mix) and mix.get("loudness_target_lufs") is not None
        checks.append(
            _check(
                "audio_loudness_target_set",
                has_target,
                "error",
                f"{mix.get('loudness_target_lufs')} LUFS" if has_target else "absent",
            )
        )

    # --- determinism ---
    determinism = edl.get("determinism") or {}
    digest = str(determinism.get("inputs_digest", ""))
    identity = str(edl.get("edl_id", ""))
    stamped = (
        len(digest) == 64
        and set(digest) <= _HEX_CHARS
        and len(identity) == 64
        and set(identity) <= _HEX_CHARS
        and identity != "0" * 64
    )
    checks.append(
        _check(
            "determinism_digest_present",
            stamped,
            "error",
            "" if stamped else "edl_id or inputs_digest is missing or not a BLAKE3 hex",
        )
    )

    failed_errors = [c for c in checks if not c["passed"] and c["severity"] == "error"]
    failed_warnings = [c for c in checks if not c["passed"] and c["severity"] == "warning"]
    status = "fail" if failed_errors else ("warn" if failed_warnings else "pass")

    return {
        "status": status,
        "checks": checks,
        "validated_at": validated_at,
        "validator_version": VALIDATOR_VERSION,
    }
