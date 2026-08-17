"""Beat grid and beat-locked cutting (build plan Phase 4).

This module does NOT analyse audio. It consumes a `BeatGrid` that a pinned
analyser already produced (librosa, ISC licence -- madmom is BLOCKED, BY-NC-SA)
and decides where cuts land against it.

=====================================================================
 WHY EVERY TIME IN HERE IS A FRACTION AND NEVER A FLOAT SECOND
=====================================================================

A 15-second reel at 60000/1001 fps is 899 frames. 30000/1001 has no exact float
form, so `frame / 29.97002997002997` accumulates error, and at the end of a
three-minute film the accumulated error is larger than a frame. A cut that
drifts two frames off the beat is audible-as-wrong: the whole point of
beat-locking is that the picture change and the transient happen together.

So: `RationalTime` holds `fractions.Fraction` internally, all arithmetic is
exact, and the only rounding in the module happens once -- at the moment a time
is quantised to a whole frame on the timeline -- with an explicitly chosen
rounding rule. The contract (`common.schema.json#/$defs/RationalTime`) stores
value/rate as JSON numbers; `from_contract`/`to_contract` are the only places
floats appear, and float(60000/1001) round-trips exactly back to 60000/1001.

=====================================================================
 THE TWO BUDGETS -- CONFLATING THEM IS THE CLASSIC SILENT BUG
=====================================================================

There are two different distances in beat-locked cutting and they are measured
against different things and bounded by different numbers:

  PULL      |ideal_content_time - beat_time|
            How far the cut moves AWAY from the moment the shot is about, in
            order to reach the beat. Bounded by `max_pull_ms` /
            `max_pull_beats`. This is the "content vs music" trade: past the
            bound, content wins and the lock is dropped.

  ALIGNMENT |cut_time - beat_time|   (signed, recorded as alignment_error_ms)
            How far the frame that actually renders sits from the beat.
            Comes from two sources: frame quantisation (up to half a frame --
            8.3ms at 59.94, 20.8ms at 23.976) and the offset of the certified
            snap point we landed on. Bounded by `BeatGrid.tolerance_ms`, and by
            50ms on downbeats, which is the EDL quality gate.

Taking a slightly worse PULL to land on a downbeat costs nothing in ALIGNMENT.
Treating them as one budget produces a planner that refuses good downbeats and
claims locks it does not have.

SIGN CONVENTION: `alignment_error_ms = (cut - beat) * 1000`. Negative is early.
This is fixed by `contracts/fixtures/edl/valid/reel-beat-locked-vertical-reframe.json`
and reproduced exactly by `tests/test_beats.py` -- do not flip it.

=====================================================================
 DECISIONS TAKEN HERE, AND WHAT THE ALTERNATIVE BREAKS
=====================================================================

1. THE GRID IS NEVER EXTRAPOLATED.
   `BeatGrid.beats` is an explicit list because real tracks drift; the schema
   says so. So `nearest_beat_index` never invents a beat past the end of the
   list. A cut after the last beat therefore has a large pull, exceeds
   `max_pull_ms`, and comes back unlocked -- which is correct, because past the
   end of the analysed region we do not know where the beat is. Extrapolating
   from `bpm` would produce a confident, wrong, silent answer.

2. LOW CONFIDENCE BREAKS THE LOCK, IT DOES NOT LOWER IT.
   Two levels. `bpm_confidence` below `min_bpm_confidence` disqualifies the
   whole grid: if the tracker is not sure of the tempo, every beat time is
   suspect and a cut placed on one is a coin flip dressed as a decision.
   Per-beat `strength` below `min_beat_strength` disqualifies that beat only --
   that is how "the tracker lost the beat during the breakdown" is expressed,
   since the contract has no per-beat confidence field. A null `strength` means
   NOT MEASURED and is not held against the beat (same reasoning as coverage in
   the ranking engine: absence of a measurement is not a bad measurement).

3. A BROKEN LOCK IS STILL A CUT.
   Every path returns a `CutDecision` with a frame. Dropping the lock changes
   `locked` and `reason`, never whether an edit happens. The alternative --
   raising, or returning None -- pushes the fallback into every caller, and the
   fallback is where silent behaviour breeds.

4. QUANTISE, THEN CHECK TOLERANCE.
   A snap point 45ms from the beat is inside a 50ms gate until it is rounded to
   a frame; then it is 53ms out. Checking before quantisation is how an EDL
   ends up claiming a lock the renderer cannot honour.

5. THE CUT LANDS ON A CERTIFIED SNAP POINT WHEN ONE IS NEAR THE BEAT.
   `MomentRecord.SnapPoint` exists so that "no mid-word cuts" is a testable
   property. Given the choice between the mathematical beat and a real motion
   onset 6ms from it, we take the onset and record the 6ms -- exactly what
   `BeatLock.alignment_error_ms` is documented to be for.

6. ROUNDING IS HALF-AWAY-FROM-ZERO, DONE ON FRACTIONS.
   Not `round()`: on floats it inherits representation error, and on Fractions
   it is banker's rounding, so two cuts sitting exactly half a frame from their
   beats round in opposite directions depending on the parity of the neighbour.
   Half-away-from-zero is symmetric about zero, so a negative timeline (a
   pre-roll handle) mirrors a positive one instead of biasing toward zero.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass, replace
from fractions import Fraction
from typing import Any, Iterable, Mapping, Sequence

__all__ = [
    "BLOCKED_ANALYZER_FAMILIES",
    "Beat",
    "BeatGrid",
    "BeatGridError",
    "BeatLockPolicy",
    "BlockedAnalyzerError",
    "CutDecision",
    "DEFAULT_POLICY",
    "GateViolation",
    "GridIssue",
    "ISSUE_ANALYZER_BLOCKED",
    "ISSUE_ANALYZER_UNPINNED",
    "ISSUE_BPM_DISAGREES_WITH_BEATS",
    "ISSUE_LOW_BPM_CONFIDENCE",
    "ISSUE_TEMPO_CHANGE",
    "REASON_ALIGNMENT_OUTSIDE_TOLERANCE",
    "REASON_BEAT_BEYOND_MAX_PULL",
    "REASON_GRID_CONFIDENCE_BELOW_FLOOR",
    "REASON_LOCKED",
    "REASON_MIN_SHOT_LENGTH_FORCED",
    "REASON_NO_LOCKABLE_BEAT",
    "RationalTime",
    "SnapPoint",
    "alignment_gate",
    "audit_grid",
    "downbeat_indices",
    "local_interval_seconds",
    "measured_bpm",
    "nearest_beat_index",
    "plan_beat_locked_cuts",
    "rate_fraction",
    "snap_cut",
]


class BeatGridError(ValueError):
    """A grid that cannot be trusted to place a cut against."""


class BlockedAnalyzerError(BeatGridError):
    """The grid came from an analyser whose licence forbids shipping it.

    Second belt only. The registry licence audit (CLAUDE.md hard rule 4) is the
    real gate; this catches a grid that reached the planner anyway, because the
    failure mode is a shipped product built on BY-NC-SA analysis and nobody
    noticing until legal does.
    """


# Matched against `ModelRef.model_id` as a family prefix rather than an exact
# slug: the registry will hold `madmom-dbn-beat-tracker` and friends, and a new
# variant must not slip through because its full slug was not on a list.
BLOCKED_ANALYZER_FAMILIES = frozenset({"madmom", "essentia"})


# --------------------------------------------------------------------------
# Exact time
# --------------------------------------------------------------------------

# Every NTSC rational this product can encounter has denominator 1001; nothing
# legitimate needs a larger one, so 1001 is the search ceiling for recovering a
# rational from the float the JSON contract carries.
_MAX_RATE_DENOMINATOR = 1001

# Only snap a float rate to a small rational when they agree to ~1e-9 relative,
# i.e. when the float is the IEEE double nearest that rational. 29.97 (the
# rounded decimal the schema explicitly warns against) is 1e-4 away from
# 30000/1001 and is therefore left alone as 2997/100: we represent exactly what
# we were given rather than silently deciding what was meant.
_RATE_SNAP_RELATIVE_TOLERANCE = Fraction(1, 10**9)

_MS_PER_SECOND = Fraction(1000)

# 0.1 microsecond. Far below the 50ms gate, and fixed so the JSON is
# byte-stable: an EDL id is a hash over canonical JSON, and a value that
# differs in the 15th decimal between machines is a different EDL.
_ERROR_DECIMAL_PLACES = 4


def _round_half_away_from_zero(x: Fraction) -> int:
    """Round a Fraction to the nearest int, halves away from zero. See §6 above."""
    n, d = x.numerator, x.denominator  # Fraction normalises d > 0
    if n >= 0:
        return (2 * n + d) // (2 * d)
    return -((-2 * n + d) // (2 * d))


def _quantize_ms(seconds: Fraction) -> float:
    """Exact seconds -> milliseconds rounded to `_ERROR_DECIMAL_PLACES`.

    Rounded on the Fraction and only then converted, so there is no
    double-rounding through a float and no platform-dependent last digit.
    """
    scale = 10**_ERROR_DECIMAL_PLACES
    return _round_half_away_from_zero(seconds * _MS_PER_SECOND * scale) / scale


def rate_fraction(rate: Any) -> Fraction:
    """Coerce a contract rate to an exact Fraction, recovering NTSC rationals."""
    if isinstance(rate, Fraction):
        exact = rate
    elif isinstance(rate, bool):  # bool is an int; a boolean rate is a bug
        raise BeatGridError(f"rate must be a number, got {rate!r}")
    elif isinstance(rate, int):
        exact = Fraction(rate)
    elif isinstance(rate, float):
        if rate != rate or rate in (float("inf"), float("-inf")):
            raise BeatGridError(f"rate must be finite, got {rate!r}")
        exact = Fraction(rate)
    elif isinstance(rate, str):
        exact = Fraction(rate)
    else:
        raise BeatGridError(f"rate must be a number, got {type(rate).__name__}")
    if exact <= 0:
        raise BeatGridError(f"rate must be > 0, got {rate!r}")
    approx = exact.limit_denominator(_MAX_RATE_DENOMINATOR)
    if approx > 0 and abs(approx - exact) <= _RATE_SNAP_RELATIVE_TOLERANCE * exact:
        return approx
    return exact


def _value_fraction(value: Any) -> Fraction:
    """Coerce a contract time value. No snapping: a value is a frame count."""
    if isinstance(value, Fraction):
        return value
    if isinstance(value, bool):
        raise BeatGridError(f"time value must be a number, got {value!r}")
    if isinstance(value, int):
        return Fraction(value)
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise BeatGridError(f"time value must be finite, got {value!r}")
        return Fraction(value)
    if isinstance(value, str):
        return Fraction(value)
    raise BeatGridError(f"time value must be a number, got {type(value).__name__}")


@functools.total_ordering
@dataclass(frozen=True, eq=False)
class RationalTime:
    """`value` units at `rate` units/second. Maps 1:1 to otio RationalTime."""

    value: Fraction
    rate: Fraction

    def __post_init__(self) -> None:
        object.__setattr__(self, "rate", rate_fraction(self.rate))
        object.__setattr__(self, "value", _value_fraction(self.value))

    # Equality and ordering are on the instant, not on the field pair: 1@2fps
    # and 2@4fps are the same moment in time, and a rescaled time compared
    # against an authored one must not differ merely because it is expressed at
    # another rate. __hash__ follows __eq__ or dict/set membership would
    # disagree with ==.
    def seconds(self) -> Fraction:
        return self.value / self.rate

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, RationalTime):
            return NotImplemented
        return self.seconds() == other.seconds()

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, RationalTime):
            return NotImplemented
        return self.seconds() < other.seconds()

    def __hash__(self) -> int:
        return hash(self.seconds())

    def rescaled_to(self, rate: Any) -> "RationalTime":
        """Exact re-expression at another rate. May produce a fractional value."""
        target = rate_fraction(rate)
        return RationalTime(self.value * target / self.rate, target)

    def quantized_to(self, rate: Any) -> "RationalTime":
        """Re-express at `rate` and round to a whole unit. The one rounding site."""
        target = rate_fraction(rate)
        return RationalTime(
            Fraction(_round_half_away_from_zero(self.value * target / self.rate)),
            target,
        )

    @property
    def frame(self) -> int:
        if self.value.denominator != 1:
            raise BeatGridError(
                f"time {self.value} @ {self.rate} is not on a whole frame; "
                "quantize_to(rate) before asking for a frame number"
            )
        return int(self.value)

    @classmethod
    def from_contract(cls, data: Mapping[str, Any]) -> "RationalTime":
        missing = {"value", "rate"} - set(data)
        if missing:
            raise BeatGridError(f"RationalTime missing {sorted(missing)}")
        return cls(_value_fraction(data["value"]), rate_fraction(data["rate"]))

    def to_contract(self) -> dict[str, Any]:
        return {"value": _number(self.value), "rate": _number(self.rate)}


def _number(x: Fraction) -> int | float:
    """Integral Fractions emit as ints; the schema prefers integral frames."""
    return int(x) if x.denominator == 1 else float(x)


def _frame_at(time: RationalTime, rate: Fraction) -> int:
    return _round_half_away_from_zero(time.value * rate / time.rate)


# --------------------------------------------------------------------------
# Snap points (MomentRecord#/$defs/SnapPoint)
# --------------------------------------------------------------------------

_CUT_DIRECTIONS = frozenset({"in", "out", "both"})


@dataclass(frozen=True)
class SnapPoint:
    """A time at which cutting is defensible.

    NOTE ON TIME BASE: `MomentRecord.snap_points[].time` is a SOURCE timecode,
    while a BeatGrid is in TIMELINE time. This class holds whichever the caller
    passes, and `snap_cut` requires TIMELINE times, because the caller is the
    only party that knows the clip's source->timeline mapping. Mixing the two
    is a category error that produces cuts hundreds of seconds away with no
    exception raised, so it is stated here rather than assumed.
    """

    time: RationalTime
    kind: str
    strength: float
    confidence: float | None = None
    cut_direction: str = "both"

    def __post_init__(self) -> None:
        if self.cut_direction not in _CUT_DIRECTIONS:
            raise BeatGridError(f"cut_direction {self.cut_direction!r} not in {sorted(_CUT_DIRECTIONS)}")
        if not 0.0 <= self.strength <= 1.0:
            raise BeatGridError(f"snap point strength {self.strength} outside [0,1]")
        if self.confidence is not None and not 0.0 <= self.confidence <= 1.0:
            raise BeatGridError(f"snap point confidence {self.confidence} outside [0,1]")

    def usable_for(self, direction: str) -> bool:
        return self.cut_direction == "both" or self.cut_direction == direction

    @classmethod
    def from_contract(cls, data: Mapping[str, Any]) -> "SnapPoint":
        return cls(
            time=RationalTime.from_contract(data["time"]),
            kind=data["kind"],
            strength=float(data["strength"]),
            confidence=(None if data.get("confidence") is None else float(data["confidence"])),
            cut_direction=data.get("cut_direction", "both"),
        )


# --------------------------------------------------------------------------
# Beat grid
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Beat:
    index: int
    time: RationalTime
    is_downbeat: bool
    bar: int | None = None
    beat_in_bar: int | None = None
    strength: float | None = None
    section: str | None = None

    def __post_init__(self) -> None:
        if self.index < 0:
            raise BeatGridError(f"beat index {self.index} is negative")
        if self.strength is not None and not 0.0 <= self.strength <= 1.0:
            raise BeatGridError(f"beat {self.index} strength {self.strength} outside [0,1]")

    @classmethod
    def from_contract(cls, data: Mapping[str, Any]) -> "Beat":
        return cls(
            index=int(data["index"]),
            time=RationalTime.from_contract(data["time"]),
            is_downbeat=bool(data["is_downbeat"]),
            bar=data.get("bar"),
            beat_in_bar=data.get("beat_in_bar"),
            strength=(None if data.get("strength") is None else float(data["strength"])),
            section=data.get("section"),
        )


@dataclass(frozen=True)
class BeatGrid:
    source_cue_id: str
    bpm: float
    beats: tuple[Beat, ...]
    bpm_confidence: float | None = None
    beats_per_bar: int | None = None
    beat_unit: int | None = None
    analyzer_model_id: str | None = None
    tolerance_ms: float = 50.0

    def __post_init__(self) -> None:
        object.__setattr__(self, "beats", tuple(self.beats))
        if not self.beats:
            raise BeatGridError("beat grid has no beats")
        if self.bpm <= 0:
            raise BeatGridError(f"bpm must be > 0, got {self.bpm}")
        if self.tolerance_ms < 0:
            raise BeatGridError(f"tolerance_ms must be >= 0, got {self.tolerance_ms}")
        if self.bpm_confidence is not None and not 0.0 <= self.bpm_confidence <= 1.0:
            raise BeatGridError(f"bpm_confidence {self.bpm_confidence} outside [0,1]")

        previous: Fraction | None = None
        for position, beat in enumerate(self.beats):
            # BeatLock.beat_index is "index into BeatGrid.beats", so the stored
            # index and the list position are the same number by definition. If
            # they disagree, every BeatLock we emit points at the wrong beat --
            # and points at *a* beat, so nothing downstream would notice.
            if beat.index != position:
                raise BeatGridError(
                    f"beat at position {position} carries index {beat.index}; "
                    "BeatLock.beat_index indexes the list, so they must agree"
                )
            seconds = beat.time.seconds()
            # Strictly increasing, not merely sorted: two beats at the same
            # instant make "nearest beat" ambiguous, and re-sorting a corrupt
            # grid here would hide an analyser bug instead of surfacing it.
            if previous is not None and seconds <= previous:
                raise BeatGridError(
                    f"beat {position} at {float(seconds)}s does not advance past "
                    f"{float(previous)}s; grid must be strictly increasing"
                )
            previous = seconds
            if self.beats_per_bar is not None and beat.beat_in_bar is not None:
                if not 1 <= beat.beat_in_bar <= self.beats_per_bar:
                    raise BeatGridError(
                        f"beat {position} beat_in_bar {beat.beat_in_bar} outside "
                        f"1..{self.beats_per_bar}"
                    )
                # A downbeat is beat 1 of a bar. A grid that says otherwise has
                # a bar phase error, which is precisely the failure that makes a
                # cut read as a mistake rather than as deliberate.
                if beat.is_downbeat != (beat.beat_in_bar == 1):
                    raise BeatGridError(
                        f"beat {position}: is_downbeat={beat.is_downbeat} contradicts "
                        f"beat_in_bar={beat.beat_in_bar}"
                    )

    # -- derived ---------------------------------------------------------

    def seconds(self, index: int) -> Fraction:
        return self.beats[index].time.seconds()

    def is_lockable(self, index: int, policy: "BeatLockPolicy") -> bool:
        """Whether a cut may claim to be locked to this specific beat."""
        if not self.grid_confident(policy):
            return False
        strength = self.beats[index].strength
        # None means the analyser did not report a strength. Absence of a
        # measurement is not a bad measurement, so it passes; the alternative
        # (treat None as 0) makes every grid from a tracker that omits the
        # field completely unlockable.
        if strength is None:
            return True
        return strength >= policy.min_beat_strength

    def grid_confident(self, policy: "BeatLockPolicy") -> bool:
        if self.bpm_confidence is None:
            return True
        return self.bpm_confidence >= policy.min_bpm_confidence

    @classmethod
    def from_contract(cls, data: Mapping[str, Any]) -> "BeatGrid":
        signature = data.get("time_signature") or {}
        analyzer = data.get("analyzer") or {}
        return cls(
            source_cue_id=data["source_cue_id"],
            bpm=float(data["bpm"]),
            beats=tuple(Beat.from_contract(b) for b in data["beats"]),
            bpm_confidence=(
                None if data.get("bpm_confidence") is None else float(data["bpm_confidence"])
            ),
            beats_per_bar=signature.get("beats_per_bar"),
            beat_unit=signature.get("beat_unit"),
            analyzer_model_id=analyzer.get("model_id"),
            tolerance_ms=float(data.get("tolerance_ms", 50.0)),
        )


def downbeat_indices(grid: BeatGrid) -> tuple[int, ...]:
    return tuple(b.index for b in grid.beats if b.is_downbeat)


def local_interval_seconds(grid: BeatGrid, index: int) -> Fraction | None:
    """Seconds to the next beat; for the last beat, from the previous one.

    Local rather than 60/bpm: the schema stores explicit beat times *because*
    real tracks drift and change tempo, so the interval that matters at a cut
    is the one around that cut, not the track average.
    """
    if len(grid.beats) < 2:
        return None
    if index < len(grid.beats) - 1:
        return grid.seconds(index + 1) - grid.seconds(index)
    return grid.seconds(index) - grid.seconds(index - 1)


def measured_bpm(grid: BeatGrid) -> float | None:
    """Median inter-beat tempo actually present in the grid.

    Median, not mean: one dropped beat doubles a single interval, and a mean
    would report a tempo the track never plays.
    """
    if len(grid.beats) < 2:
        return None
    intervals = sorted(
        grid.seconds(i + 1) - grid.seconds(i) for i in range(len(grid.beats) - 1)
    )
    middle = len(intervals) // 2
    if len(intervals) % 2:
        median = intervals[middle]
    else:
        median = (intervals[middle - 1] + intervals[middle]) / 2
    return float(60 / median)


# --------------------------------------------------------------------------
# Policy
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class BeatLockPolicy:
    """Where the content-vs-music trade is made. All bounds are explicit."""

    # PULL budget: how far a cut may move from its content-ideal time to reach
    # a beat. 120ms is roughly the point at which a viewer stops reading the
    # cut as "on the action" -- past it, content wins and the lock is dropped.
    max_pull_ms: float = 120.0
    # ...but also never more than half a beat, so the bound scales with tempo.
    # At 60bpm a fixed 120ms is 12% of a beat; at 180bpm it is 36%. The beat
    # fraction is the bound that means the same thing at both tempos.
    max_pull_beats: float = 0.5

    # Extra PULL we will accept to land on a downbeat instead of an off-beat.
    # A cut on a downbeat reads as deliberate; the same cut one beat off reads
    # as a mistake, which is worth 60ms of content drift.
    downbeat_preference_ms: float = 60.0

    # ALIGNMENT budget. None means "use BeatGrid.tolerance_ms" -- the grid
    # carries the number because the analyser knows how tight its own times
    # are. The downbeat gate is the EDL quality gate and is a ceiling, never a
    # relaxation: a grid asking for 20ms still gets 20ms on downbeats.
    tolerance_ms: float | None = None
    downbeat_tolerance_ms: float = 50.0

    min_bpm_confidence: float = 0.5
    min_beat_strength: float = 0.15
    # Not filtering weak snap points by default: MomentRecord.snap_points is
    # already curated. Raise this when feeding a raw detector dump.
    min_snap_strength: float = 0.0

    def __post_init__(self) -> None:
        for name in ("max_pull_ms", "downbeat_preference_ms", "downbeat_tolerance_ms"):
            if getattr(self, name) < 0:
                raise BeatGridError(f"{name} must be >= 0")
        if self.max_pull_beats <= 0:
            raise BeatGridError("max_pull_beats must be > 0")
        if self.tolerance_ms is not None and self.tolerance_ms < 0:
            raise BeatGridError("tolerance_ms must be >= 0")

    def tolerances(self, grid: BeatGrid) -> tuple[Fraction, Fraction]:
        base = Fraction(
            str(grid.tolerance_ms if self.tolerance_ms is None else self.tolerance_ms)
        )
        return base, min(base, Fraction(str(self.downbeat_tolerance_ms)))

    def pull_limit_ms(self, grid: BeatGrid, index: int) -> Fraction:
        limit = Fraction(str(self.max_pull_ms))
        interval = local_interval_seconds(grid, index)
        if interval is not None:
            limit = min(limit, Fraction(str(self.max_pull_beats)) * interval * _MS_PER_SECOND)
        return limit


DEFAULT_POLICY = BeatLockPolicy()


# --------------------------------------------------------------------------
# Cut decisions
# --------------------------------------------------------------------------

REASON_LOCKED = "locked"
REASON_GRID_CONFIDENCE_BELOW_FLOOR = "grid_confidence_below_floor"
REASON_NO_LOCKABLE_BEAT = "no_lockable_beat"
REASON_BEAT_BEYOND_MAX_PULL = "nearest_beat_beyond_max_pull"
REASON_ALIGNMENT_OUTSIDE_TOLERANCE = "alignment_outside_tolerance"
REASON_MIN_SHOT_LENGTH_FORCED = "min_shot_length_forced"


@dataclass(frozen=True)
class CutDecision:
    """Where the cut goes, whether it may claim a lock, and why.

    `beat_index` and `alignment_error_ms` are populated whenever a target beat
    was identified, EVEN WHEN `locked` IS FALSE -- that is the audit trail for
    "we tried, here is how far off it was". `to_beat_lock()` is the only thing
    that decides what reaches the contract, and it emits nothing unless locked.
    """

    time: RationalTime
    locked: bool
    reason: str
    beat_index: int | None = None
    is_downbeat: bool = False
    alignment_error_ms: float | None = None
    snap_point_kind: str | None = None

    @property
    def frame(self) -> int:
        return self.time.frame

    def to_beat_lock(self) -> dict[str, Any] | None:
        """EDL#/$defs/BeatLock, or None when this cut is not beat-locked."""
        if not self.locked:
            return None
        return {
            "beat_index": self.beat_index,
            "is_downbeat": self.is_downbeat,
            "alignment_error_ms": self.alignment_error_ms,
            "snap_point_kind": self.snap_point_kind,
        }


def _assert_analyzer_licensed(grid: BeatGrid) -> None:
    model_id = grid.analyzer_model_id
    if model_id is None:
        return
    family = model_id.split("-", 1)[0]
    if family in BLOCKED_ANALYZER_FAMILIES:
        raise BlockedAnalyzerError(
            f"beat grid was produced by {model_id!r}; the {family} family is "
            "licence-blocked (non-commercial) and must not reach a shipped cut"
        )


def nearest_beat_index(
    grid: BeatGrid,
    time: RationalTime,
    *,
    candidates: Sequence[int] | None = None,
) -> int | None:
    """Index of the beat closest to `time`, or None when `candidates` is empty.

    Never extrapolates past either end of the grid (see §1 in the module
    docstring). Ties go to the EARLIER beat: a cut that lands fractionally
    ahead of the transient reads as anticipation, one that lands behind it
    reads as lag, and an unstated tie-break is a determinism bug.
    """
    pool = range(len(grid.beats)) if candidates is None else candidates
    best: int | None = None
    best_distance: Fraction | None = None
    target = time.seconds()
    for index in pool:
        distance = abs(grid.seconds(index) - target)
        if best_distance is None or distance < best_distance:
            best, best_distance = index, distance
    return best


def snap_cut(
    grid: BeatGrid,
    ideal_time: RationalTime,
    snap_points: Sequence[SnapPoint] = (),
    *,
    rate: Any | None = None,
    direction: str = "in",
    policy: BeatLockPolicy = DEFAULT_POLICY,
) -> CutDecision:
    """Place one cut: content-ideal time in, beat-locked frame out.

    `ideal_time` is where the CONTENT wants the cut. `snap_points` are the
    certified cut times for this shot, in TIMELINE time (see `SnapPoint`).
    `rate` is the timeline rate the cut must land on; it defaults to
    `ideal_time.rate` and may differ from the rate the beat grid is expressed
    at (a grid authored at 48000 audio units is rescaled exactly, not
    approximately).
    """
    return _snap_cut_impl(
        grid,
        ideal_time,
        snap_points,
        rate=ideal_time.rate if rate is None else rate,
        direction=direction,
        policy=policy,
        floor_frame=None,
    )


def _snap_cut_impl(
    grid: BeatGrid,
    ideal_time: RationalTime,
    snap_points: Sequence[SnapPoint],
    *,
    rate: Any,
    direction: str,
    policy: BeatLockPolicy,
    floor_frame: int | None,
) -> CutDecision:
    _assert_analyzer_licensed(grid)
    if direction not in ("in", "out"):
        raise BeatGridError(f"direction must be 'in' or 'out', got {direction!r}")
    timeline_rate = rate_fraction(rate)
    tolerance, downbeat_tolerance = policy.tolerances(grid)
    ideal_seconds = ideal_time.seconds()

    # Snap points usable for this cut. `floor_frame` is the minimum-shot-length
    # repair in plan_beat_locked_cuts: a snap point before the floor cannot be
    # used, because using it would produce a shot shorter than the caller said
    # is renderable.
    usable = tuple(
        sp
        for sp in snap_points
        if sp.usable_for(direction)
        and sp.strength >= policy.min_snap_strength
        and (floor_frame is None or _frame_at(sp.time, timeline_rate) >= floor_frame)
    )

    def content_fallback(reason: str, beat_index: int | None) -> CutDecision:
        """Cut where the content says, claim nothing about the music."""
        kind: str | None = None
        if usable:
            best = min(
                usable,
                key=lambda sp: (
                    abs(sp.time.seconds() - ideal_seconds),
                    -sp.strength,
                    sp.time.seconds(),
                    sp.kind,
                ),
            )
            frame = _frame_at(best.time, timeline_rate)
            kind = best.kind
        else:
            frame = _frame_at(ideal_time, timeline_rate)
        if floor_frame is not None and frame < floor_frame:
            frame = floor_frame
            # We were pushed off the snap point, so we may no longer name it.
            # Reporting the kind here would put a "motion_onset" label on a
            # frame that is not a motion onset.
            kind = None
        error = None
        if beat_index is not None:
            error = _quantize_ms(
                Fraction(frame) / timeline_rate - grid.seconds(beat_index)
            )
        return CutDecision(
            time=RationalTime(Fraction(frame), timeline_rate),
            locked=False,
            reason=reason,
            beat_index=beat_index,
            is_downbeat=(False if beat_index is None else grid.beats[beat_index].is_downbeat),
            alignment_error_ms=error,
            snap_point_kind=kind,
        )

    if not grid.grid_confident(policy):
        # The tracker is not sure of the tempo, so no beat time in this grid is
        # worth moving a cut for. Fall back to content entirely rather than
        # locking to a number we do not believe.
        return content_fallback(REASON_GRID_CONFIDENCE_BELOW_FLOOR, None)

    lockable = [
        i
        for i in range(len(grid.beats))
        if grid.is_lockable(i, policy)
        and (floor_frame is None or _frame_at(grid.beats[i].time, timeline_rate) >= floor_frame)
    ]
    if not lockable:
        return content_fallback(REASON_NO_LOCKABLE_BEAT, None)

    nearest = nearest_beat_index(grid, ideal_time, candidates=lockable)
    assert nearest is not None  # lockable is non-empty
    nearest_pull = abs(grid.seconds(nearest) - ideal_seconds) * _MS_PER_SECOND
    pull_limit = policy.pull_limit_ms(grid, nearest)

    target = nearest
    if not grid.beats[nearest].is_downbeat:
        downbeats = [i for i in lockable if grid.beats[i].is_downbeat]
        candidate = nearest_beat_index(grid, ideal_time, candidates=downbeats)
        if candidate is not None:
            downbeat_pull = abs(grid.seconds(candidate) - ideal_seconds) * _MS_PER_SECOND
            # Two conditions, not one. The extra pull must be affordable AND the
            # total pull must still be inside the budget -- a downbeat 400ms
            # away is not worth having even if the nearest beat is 350ms away.
            if (
                downbeat_pull - nearest_pull <= Fraction(str(policy.downbeat_preference_ms))
                and downbeat_pull <= policy.pull_limit_ms(grid, candidate)
            ):
                target = candidate

    target_pull = abs(grid.seconds(target) - ideal_seconds) * _MS_PER_SECOND
    limit = pull_limit if target == nearest else policy.pull_limit_ms(grid, target)
    if target_pull > limit:
        # THE CONTENT/MUSIC TRADE RESOLVES HERE. Beyond this distance, moving
        # the cut to the beat means cutting away from the thing the shot is
        # about. Content wins, and we do not claim a lock we did not make.
        return content_fallback(REASON_BEAT_BEYOND_MAX_PULL, target)

    beat_seconds = grid.seconds(target)
    beat_tolerance = downbeat_tolerance if grid.beats[target].is_downbeat else tolerance

    # Prefer a certified snap point close to the beat over the bare beat time
    # (see §5). Selection is against the BEAT, not the ideal: at this point the
    # beat has already won the pull argument.
    near_beat = [
        sp
        for sp in usable
        if abs(sp.time.seconds() - beat_seconds) * _MS_PER_SECOND <= beat_tolerance
    ]
    if near_beat:
        chosen = min(
            near_beat,
            key=lambda sp: (
                abs(sp.time.seconds() - beat_seconds),
                -sp.strength,
                sp.time.seconds(),
                sp.kind,
            ),
        )
        cut_frame = _frame_at(chosen.time, timeline_rate)
        kind: str | None = chosen.kind
    elif usable:
        # Snap points exist for this shot but none is near the beat. Landing on
        # the beat would mean cutting at a time nothing in the picture supports
        # (mid-gesture, mid-word). Take the content point and drop the lock.
        return content_fallback(REASON_ALIGNMENT_OUTSIDE_TOLERANCE, target)
    else:
        cut_frame = _frame_at(grid.beats[target].time, timeline_rate)
        kind = None

    if floor_frame is not None and cut_frame < floor_frame:
        return content_fallback(REASON_MIN_SHOT_LENGTH_FORCED, target)

    # QUANTISE FIRST, THEN CHECK (see §4). `cut_frame` is already the rounded
    # frame, so this error is the one the renderer will actually produce.
    error_seconds = Fraction(cut_frame) / timeline_rate - beat_seconds
    error_ms = _quantize_ms(error_seconds)
    within = abs(Fraction(str(error_ms))) <= beat_tolerance
    return CutDecision(
        time=RationalTime(Fraction(cut_frame), timeline_rate),
        locked=within,
        reason=REASON_LOCKED if within else REASON_ALIGNMENT_OUTSIDE_TOLERANCE,
        beat_index=target,
        is_downbeat=grid.beats[target].is_downbeat,
        alignment_error_ms=error_ms,
        snap_point_kind=kind,
    )


def plan_beat_locked_cuts(
    grid: BeatGrid,
    ideal_cuts: Sequence[RationalTime],
    *,
    rate: Any | None = None,
    snap_points: Sequence[Sequence[SnapPoint]] | None = None,
    min_shot_frames: int = 1,
    direction: str = "in",
    policy: BeatLockPolicy = DEFAULT_POLICY,
) -> tuple[CutDecision, ...]:
    """Snap a whole sequence of content-ideal cut times, then repair overlaps.

    A SHOT SHORTER THAN ONE BEAT is the case this function exists for. Snapping
    each cut independently can put two cuts on the same frame, or one frame
    apart, which is not a shot -- it is a flash frame. So a second pass walks
    the sequence in order and, wherever a cut would land closer than
    `min_shot_frames` to the previous one, re-runs the decision with a floor:
    only beats and snap points at or after the floor are eligible. If the
    earliest eligible beat is beyond the pull budget, the cut is placed at the
    floor UNLOCKED rather than silently dragged onto a distant beat.

    Cuts must arrive in non-decreasing order. Sorting them here would silently
    reorder the story; an out-of-order sequence is a caller bug.
    """
    if min_shot_frames < 1:
        raise BeatGridError(f"min_shot_frames must be >= 1, got {min_shot_frames}")
    if snap_points is not None and len(snap_points) != len(ideal_cuts):
        raise BeatGridError(
            f"snap_points has {len(snap_points)} entries for {len(ideal_cuts)} cuts"
        )
    if not ideal_cuts:
        return ()

    timeline_rate = rate_fraction(ideal_cuts[0].rate if rate is None else rate)
    previous_seconds: Fraction | None = None
    for position, cut in enumerate(ideal_cuts):
        seconds = cut.seconds()
        if previous_seconds is not None and seconds < previous_seconds:
            raise BeatGridError(
                f"ideal_cuts[{position}] at {float(seconds)}s precedes "
                f"ideal_cuts[{position - 1}]; cut times must be non-decreasing"
            )
        previous_seconds = seconds

    decisions: list[CutDecision] = []
    floor: int | None = None
    for position, cut in enumerate(ideal_cuts):
        points = () if snap_points is None else tuple(snap_points[position])
        decision = _snap_cut_impl(
            grid,
            cut,
            points,
            rate=timeline_rate,
            direction=direction,
            policy=policy,
            floor_frame=None,
        )
        if floor is not None and decision.frame < floor:
            decision = _snap_cut_impl(
                grid,
                cut,
                points,
                rate=timeline_rate,
                direction=direction,
                policy=policy,
                floor_frame=floor,
            )
        decisions.append(decision)
        floor = decision.frame + min_shot_frames
    return tuple(decisions)


# --------------------------------------------------------------------------
# Gates and audits
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class GateViolation:
    position: int
    beat_index: int | None
    error_ms: float
    limit_ms: float


def alignment_gate(
    decisions: Sequence[CutDecision],
    grid: BeatGrid,
    *,
    policy: BeatLockPolicy = DEFAULT_POLICY,
) -> tuple[GateViolation, ...]:
    """Every cut CLAIMING a lock must be inside tolerance. Feeds EdlValidation.

    Only locked cuts are checked. An unlocked cut is not claiming anything, so
    a large error on it is information, not a violation -- gating it would push
    planners toward hiding failed locks instead of reporting them.
    """
    tolerance, downbeat_tolerance = policy.tolerances(grid)
    violations: list[GateViolation] = []
    for position, decision in enumerate(decisions):
        if not decision.locked or decision.alignment_error_ms is None:
            continue
        limit = downbeat_tolerance if decision.is_downbeat else tolerance
        if abs(Fraction(str(decision.alignment_error_ms))) > limit:
            violations.append(
                GateViolation(
                    position=position,
                    beat_index=decision.beat_index,
                    error_ms=decision.alignment_error_ms,
                    limit_ms=float(limit),
                )
            )
    return tuple(violations)


ISSUE_ANALYZER_BLOCKED = "analyzer_licence_blocked"
ISSUE_ANALYZER_UNPINNED = "analyzer_unpinned"
ISSUE_LOW_BPM_CONFIDENCE = "bpm_confidence_below_floor"
ISSUE_BPM_DISAGREES_WITH_BEATS = "bpm_disagrees_with_beat_spacing"
ISSUE_TEMPO_CHANGE = "tempo_change"


@dataclass(frozen=True)
class GridIssue:
    code: str
    detail: str
    beat_index: int | None = None


def audit_grid(
    grid: BeatGrid,
    *,
    policy: BeatLockPolicy = DEFAULT_POLICY,
    tempo_change_ratio: float = 0.08,
) -> tuple[GridIssue, ...]:
    """Statistical problems a grid can have without being structurally invalid.

    Separate from construction on purpose: a grid with a half-time tempo error
    is still a well-formed grid, and you must be able to load one to inspect
    it. Construction raises only on contradictions (see BeatGrid.__post_init__);
    everything judgemental is reported here so a caller decides in the open.
    Returned sorted so two runs produce identical reports.
    """
    issues: list[GridIssue] = []
    model_id = grid.analyzer_model_id
    if model_id is None:
        issues.append(
            GridIssue(ISSUE_ANALYZER_UNPINNED, "analyzer is null; grid is not reproducible")
        )
    elif model_id.split("-", 1)[0] in BLOCKED_ANALYZER_FAMILIES:
        issues.append(GridIssue(ISSUE_ANALYZER_BLOCKED, f"analyzer {model_id!r} is non-commercial"))

    if not grid.grid_confident(policy):
        issues.append(
            GridIssue(
                ISSUE_LOW_BPM_CONFIDENCE,
                f"bpm_confidence {grid.bpm_confidence} < {policy.min_bpm_confidence}",
            )
        )

    measured = measured_bpm(grid)
    if measured is not None:
        # 4% catches a stated bpm that was rounded; it does not catch a
        # half-time/double-time error, which is the point -- that shows up as a
        # ~100% disagreement and must not be lost in a wide tolerance.
        if abs(measured - grid.bpm) > 0.04 * grid.bpm:
            issues.append(
                GridIssue(
                    ISSUE_BPM_DISAGREES_WITH_BEATS,
                    f"stated bpm {grid.bpm} vs measured {measured:.4f}",
                )
            )
        reference = Fraction(60) / Fraction(str(measured))
        for index in range(len(grid.beats) - 1):
            interval = grid.seconds(index + 1) - grid.seconds(index)
            if abs(interval - reference) > Fraction(str(tempo_change_ratio)) * reference:
                issues.append(
                    GridIssue(
                        ISSUE_TEMPO_CHANGE,
                        f"interval {float(interval):.6f}s vs median {float(reference):.6f}s",
                        beat_index=index,
                    )
                )
    return tuple(
        sorted(issues, key=lambda i: (i.code, -1 if i.beat_index is None else i.beat_index, i.detail))
    )
