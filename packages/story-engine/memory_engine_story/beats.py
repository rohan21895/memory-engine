"""Beat grid consumption and beat-locked cutting.

Phase 4. This module does NOT analyse audio. It consumes a `BeatGrid` that a
pinned analyser already produced (times + tempo + confidence) and decides where
cuts land on the timeline.

WHY THERE IS NO LIBROSA IMPORT HERE

`BeatGrid.analyzer` exists because the analyser is part of provenance, and
because the licence situation is a minefield: librosa is ISC and safe, madmom
is CC BY-NC-SA and BLOCKED for a product we sell, Essentia is AGPL. Analysis
runs in the ml-runtime host, not here. What this module does enforce is that a
grid produced by a non-commercial analyser cannot be used to plan a cut without
the caller saying so out loud -- see `NON_COMMERCIAL_ANALYZERS`. Silently
planning against madmom output is exactly the kind of "no silent anything"
violation that ships a licence breach.

TIME IS EXACT, NEVER FLOAT SECONDS

Every time in this module is a `RationalTime` -- `value` units at `rate` units
per second -- backed by `fractions.Fraction`. Not floats.

The reason is 30000/1001. It has no exact binary form, so `1/29.97...` seconds
added 900 times is not 900 frames; it is 900 frames plus a few nanoseconds of
garbage, and the garbage grows monotonically. A 30-second reel at 59.94 is
~1800 frames. Float accumulation of frame durations drifts, and drift is the
one failure mode beat-locking exists to prevent: a cut two frames off the beat
is visible, and worse, it is visible as *sloppiness* rather than as a choice.

So: all arithmetic is on Fractions (exact), and floats appear exactly twice --
when a contract mapping is parsed in (a float is converted to its exact binary
value, which is lossless) and when a number is written back out for JSON. Cost
comparisons are on Fractions too, so tie-breaking is exact rather than
subject to the last bit of a double.

WHAT "LOCKED" MEANS

A cut is beat-locked when its timeline in-point sits within the grid's
tolerance of a real beat. Three things fight over where that in-point goes:

  1. the music     -- the beat time, which is almost never on a frame boundary
  2. the frame grid -- a cut can only happen on an integer frame
  3. the content   -- the moment actually wants to start at a motion onset or a
                      shot boundary, which is also almost never on the beat

`lock_cut` scores every (candidate frame, candidate beat) pair and picks one,
then reports the residual as `BeatLock.alignment_error_ms` so the <50ms
downbeat quality gate is measurable from the plan alone, without rendering.
When nothing satisfies the gate it returns an UNLOCKED decision with a reason
instead of quietly emitting a `BeatLock` that lies about being on the beat.

DOWNBEATS

A cut on a downbeat reads as deliberate; a cut on an off-beat reads as a
mistake even when it is numerically closer to *a* beat. So downbeats get an
explicit content-drift budget (`downbeat_bonus_ms`): we will move the cut up to
that far from the content-ideal point to land on a downbeat. Conversely the
downbeat gate is the strictest -- a downbeat that is 60ms out is more audibly
wrong than an off-beat 60ms out.

DETERMINISM

Same inputs, same plan, byte for byte. Candidates are sorted before scoring,
every tie is broken by an explicit total ordering ending in an integer frame
number and a beat index, no dict or set is ever iterated, and the emitted
`alignment_error_ms` is rounded from an exact Fraction with an explicit
half-away-from-zero rule (not `round()`, which is banker's rounding and would
make the output depend on the parity of the last kept digit).
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from fractions import Fraction

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Contract default for BeatGrid.tolerance_ms.
DEFAULT_TOLERANCE_MS = 50.0

# The downbeat quality gate from the EDL schema. Applied as an upper bound on
# top of tolerance_ms, never as a replacement: a caller who loosens tolerance to
# 120ms for a lo-fi track still may not claim a downbeat lock at 120ms, because
# a missed downbeat is the single most audible alignment error there is.
DOWNBEAT_GATE_MS = 50.0

# Decimal places kept on emitted alignment/drift figures. Four is what the
# golden EDL fixture carries; keeping the same precision means a re-planned EDL
# is byte-comparable with the fixture rather than merely close to it.
EMITTED_MS_DECIMALS = 4

# Tolerance when auditing a declared alignment_error_ms against a recomputed
# one. Purely the rounding slack of EMITTED_MS_DECIMALS, plus a margin.
AUDIT_EPSILON_MS = 1e-3

# Analysers whose licence forbids use in the shipped product. Keyed by the
# model_id stem so "madmom-dbn-downbeat" is caught as well as "madmom".
# Mapped to the reason so the refusal explains itself; a bare blocklist that
# raises "not allowed" teaches the next person nothing.
NON_COMMERCIAL_ANALYZERS: Mapping[str, str] = {
    "madmom": "CC BY-NC-SA 4.0 -- non-commercial only",
    "essentia": "AGPL-3.0 -- copyleft, incompatible with a shipped binary",
}

# Reasons a CutDecision carries. Strings rather than an enum so they survive
# into a JSON plan diff unchanged.
REASON_LOCKED_DOWNBEAT = "locked_downbeat"
REASON_LOCKED_BEAT = "locked_beat"
REASON_GRID_CONFIDENCE = "unlocked_grid_confidence_below_min"
REASON_NO_ELIGIBLE_BEAT = "unlocked_no_eligible_beat_near_cut"
REASON_OUTSIDE_TOLERANCE = "unlocked_beat_outside_tolerance"
REASON_NO_CANDIDATE = "unlocked_no_candidate_position"


# ---------------------------------------------------------------------------
# Exact time
# ---------------------------------------------------------------------------


def _to_fraction(x: object, *, what: str) -> Fraction:
    """Coerce a contract number to an exact Fraction.

    `Fraction(float)` is *exact*: it takes the binary value the double actually
    holds, not a decimal approximation of it. So parsing rate 59.94005994005994
    and writing it back out round-trips bit for bit, while all arithmetic in
    between is exact. Deliberately NOT `Fraction(str(x))`, which would silently
    change the value to a nearby decimal.
    """
    if isinstance(x, bool):
        # bool is an int subclass; a stray True would become rate=1 and every
        # time in the plan would be off by a factor of the real rate.
        raise TypeError(f"{what}: bool is not a number")
    if isinstance(x, Fraction):
        return x
    if isinstance(x, int):
        return Fraction(x)
    if isinstance(x, float):
        if not math.isfinite(x):
            raise ValueError(f"{what}: {x!r} is not finite")
        return Fraction(x)
    raise TypeError(f"{what}: expected int, float or Fraction, got {type(x).__name__}")


def _round_half_away(value: Fraction, decimals: int) -> float:
    """Round an exact Fraction to `decimals` places, halves away from zero.

    Not `round()`: Python rounds halves to even, so 0.00125 and 0.00135 both
    land on 0.0012/0.0014 depending on parity. That is deterministic but it is
    not symmetric, and a -6.46665 error and a +6.46665 error would round to
    magnitudes that differ. Away-from-zero keeps |error| symmetric, which
    matters because the quality gate is on |error|.
    """
    scale = 10**decimals
    scaled = value * scale
    if scaled >= 0:
        units = math.floor(scaled + Fraction(1, 2))
    else:
        units = math.ceil(scaled - Fraction(1, 2))
    return float(Fraction(units, scale))


@dataclass(frozen=True, eq=False)
class RationalTime:
    """`value` units at `rate` units/second. Seconds = value / rate.

    Maps 1:1 onto `opentimelineio.opentime.RationalTime`. `value` may be
    fractional -- beat times are, because a beat lands between frames -- but a
    cut's value is always an integer frame.
    """

    value: Fraction
    rate: Fraction

    def __post_init__(self) -> None:
        object.__setattr__(self, "value", _to_fraction(self.value, what="RationalTime.value"))
        object.__setattr__(self, "rate", _to_fraction(self.rate, what="RationalTime.rate"))
        if self.rate <= 0:
            raise ValueError(f"RationalTime.rate must be > 0, got {self.rate}")

    # -- construction --------------------------------------------------

    @classmethod
    def from_contract(cls, data: Mapping[str, object]) -> "RationalTime":
        try:
            value = data["value"]
            rate = data["rate"]
        except (KeyError, TypeError) as exc:
            raise ValueError(f"RationalTime requires 'value' and 'rate': {data!r}") from exc
        return cls(value, rate)  # type: ignore[arg-type]

    @classmethod
    def from_seconds(cls, seconds: object, rate: object) -> "RationalTime":
        rate_f = _to_fraction(rate, what="rate")
        return cls(_to_fraction(seconds, what="seconds") * rate_f, rate_f)

    @classmethod
    def from_ms(cls, ms: object, rate: object) -> "RationalTime":
        rate_f = _to_fraction(rate, what="rate")
        return cls(_to_fraction(ms, what="ms") * rate_f / 1000, rate_f)

    # -- exact accessors -----------------------------------------------

    @property
    def seconds(self) -> Fraction:
        """Exact seconds. The canonical form for cross-rate comparison."""
        return self.value / self.rate

    def rescaled_to(self, rate: object) -> "RationalTime":
        """Same instant, expressed at another rate. Exact -- no rounding.

        The result's value is usually fractional. Quantising to a frame is a
        separate, explicit step (`nearest_frame`) precisely so that a rescale
        can never silently move a time.
        """
        rate_f = _to_fraction(rate, what="rate")
        if rate_f <= 0:
            raise ValueError(f"rate must be > 0, got {rate_f}")
        if rate_f == self.rate:
            return self
        return RationalTime(self.seconds * rate_f, rate_f)

    # -- arithmetic ----------------------------------------------------

    def __add__(self, other: "RationalTime") -> "RationalTime":
        # Result carries the LHS rate: in this codebase the LHS is always the
        # timeline and the RHS is an offset, and a result that flipped to the
        # RHS rate would make `timeline + audio_offset` come back in samples.
        return RationalTime((self.seconds + other.seconds) * self.rate, self.rate)

    def __sub__(self, other: "RationalTime") -> "RationalTime":
        return RationalTime((self.seconds - other.seconds) * self.rate, self.rate)

    # -- ordering ------------------------------------------------------
    #
    # Equality is by INSTANT, not by (value, rate): 60@60fps and 1@1fps are the
    # same moment and must compare equal, or a cross-rate dedupe would keep
    # both. __hash__ therefore hashes the instant too, keeping the
    # equal-implies-same-hash invariant that a field-wise dataclass __eq__ plus
    # an instant-wise comparison would have broken.

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, RationalTime):
            return NotImplemented
        return self.seconds == other.seconds

    def __hash__(self) -> int:
        return hash(self.seconds)

    def __lt__(self, other: "RationalTime") -> bool:
        return self.seconds < other.seconds

    def __le__(self, other: "RationalTime") -> bool:
        return self.seconds <= other.seconds

    def __gt__(self, other: "RationalTime") -> bool:
        return self.seconds > other.seconds

    def __ge__(self, other: "RationalTime") -> bool:
        return self.seconds >= other.seconds

    # -- output --------------------------------------------------------

    def to_contract(self) -> dict[str, float | int]:
        """The only lossy step in the module, and only for values a double
        cannot hold. Integral values emit as `int` because the contract
        strongly prefers integral values on video tracks and `112.0` vs `112`
        is a spurious diff in every golden fixture."""
        value: float | int
        if self.value.denominator == 1:
            value = int(self.value)
        else:
            value = float(self.value)
        rate: float | int
        if self.rate.denominator == 1:
            rate = int(self.rate)
        else:
            rate = float(self.rate)
        return {"value": value, "rate": rate}

    def __repr__(self) -> str:
        return f"RationalTime({self.value}, {self.rate})"


def _ms(seconds: Fraction) -> Fraction:
    return seconds * 1000


def _ms_to_seconds(ms: object, *, what: str) -> Fraction:
    return _to_fraction(ms, what=what) / 1000


def floor_frame(time: RationalTime, rate: object) -> int:
    """Largest frame index at or before `time`."""
    return math.floor(time.rescaled_to(rate).value)


def ceil_frame(time: RationalTime, rate: object) -> int:
    """Smallest frame index at or after `time`."""
    return math.ceil(time.rescaled_to(rate).value)


def nearest_frame(time: RationalTime, rate: object) -> int:
    """Nearest frame index; an exact half goes to the LATER frame.

    Explicit rule rather than `round()`, whose banker's rounding would send
    112.5 to 112 and 113.5 to 114 -- two cuts a beat apart quantising in
    opposite directions is exactly the kind of one-frame inconsistency that
    reads as jitter.
    """
    return math.floor(time.rescaled_to(rate).value + Fraction(1, 2))


def frame_time(frame: int, rate: object) -> RationalTime:
    """A frame index as a RationalTime at that rate."""
    return RationalTime(Fraction(int(frame)), rate)


# ---------------------------------------------------------------------------
# Beat grid
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TimeSignature:
    beats_per_bar: int
    beat_unit: int

    def __post_init__(self) -> None:
        if not 1 <= self.beats_per_bar <= 32:
            raise ValueError(f"beats_per_bar out of range: {self.beats_per_bar}")
        if self.beat_unit not in (1, 2, 4, 8, 16):
            raise ValueError(f"beat_unit must be one of 1,2,4,8,16: {self.beat_unit}")


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
            raise ValueError(f"Beat.index must be >= 0, got {self.index}")
        if not isinstance(self.is_downbeat, bool):
            raise TypeError("Beat.is_downbeat must be a bool")
        if self.bar is not None and self.bar < 0:
            raise ValueError(f"Beat.bar must be >= 0, got {self.bar}")
        if self.beat_in_bar is not None and self.beat_in_bar < 1:
            raise ValueError(f"Beat.beat_in_bar is 1-based, got {self.beat_in_bar}")
        if self.strength is not None and not 0.0 <= self.strength <= 1.0:
            raise ValueError(f"Beat.strength must be in [0,1], got {self.strength}")

    @property
    def seconds(self) -> Fraction:
        return self.time.seconds

    @classmethod
    def from_contract(cls, data: Mapping[str, object]) -> "Beat":
        return cls(
            index=int(data["index"]),  # type: ignore[call-overload]
            time=RationalTime.from_contract(data["time"]),  # type: ignore[arg-type]
            is_downbeat=bool(data["is_downbeat"]),
            bar=None if data.get("bar") is None else int(data["bar"]),  # type: ignore[call-overload]
            beat_in_bar=(
                None if data.get("beat_in_bar") is None else int(data["beat_in_bar"])  # type: ignore[call-overload]
            ),
            strength=None if data.get("strength") is None else float(data["strength"]),  # type: ignore[arg-type]
            section=None if data.get("section") is None else str(data["section"]),
        )


class BlockedAnalyzerError(RuntimeError):
    """Raised when a grid from a non-commercial analyser is used unguarded."""


@dataclass(frozen=True)
class BeatGrid:
    source_cue_id: str
    bpm: float
    beats: tuple[Beat, ...]
    bpm_confidence: float | None = None
    time_signature: TimeSignature | None = None
    tolerance_ms: float = DEFAULT_TOLERANCE_MS
    analyzer_id: str | None = None
    _seconds: tuple[Fraction, ...] = field(
        default=(), init=False, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        beats = tuple(self.beats)
        object.__setattr__(self, "beats", beats)
        if not beats:
            raise ValueError("BeatGrid requires at least one beat")
        if self.bpm <= 0:
            raise ValueError(f"BeatGrid.bpm must be > 0, got {self.bpm}")
        if self.tolerance_ms < 0:
            raise ValueError(f"BeatGrid.tolerance_ms must be >= 0, got {self.tolerance_ms}")
        if self.bpm_confidence is not None and not 0.0 <= self.bpm_confidence <= 1.0:
            raise ValueError(f"bpm_confidence must be in [0,1], got {self.bpm_confidence}")

        seconds: list[Fraction] = []
        for position, beat in enumerate(beats):
            # BeatLock.beat_index is documented as an index INTO this array. If
            # a beat's own `index` field disagreed with its position, every
            # emitted lock would point at the wrong beat while still validating
            # against the schema -- silent, and unrecoverable after the fact.
            if beat.index != position:
                raise ValueError(
                    f"BeatGrid.beats[{position}].index is {beat.index}; "
                    "beat_index is positional, so they must agree"
                )
            current = beat.seconds
            if position > 0 and current <= seconds[-1]:
                # Equal times are rejected as well as reversed ones: two beats
                # at the same instant make "the nearest beat" ambiguous, and an
                # ambiguous nearest is a nondeterministic plan.
                raise ValueError(
                    f"BeatGrid.beats must be strictly increasing in time; "
                    f"beat {position} at {float(current)}s does not follow "
                    f"beat {position - 1} at {float(seconds[-1])}s"
                )
            seconds.append(current)
        object.__setattr__(self, "_seconds", tuple(seconds))

    # -- construction --------------------------------------------------

    @classmethod
    def from_contract(
        cls,
        data: Mapping[str, object],
        *,
        allow_non_commercial_analyzer: bool = False,
    ) -> "BeatGrid":
        """Parse a contract `BeatGrid`.

        Refuses a grid whose analyser is licence-blocked unless the caller
        explicitly opts in (research/eval only). The opt-in is a keyword
        argument rather than a config flag so it shows up in a code review
        diff rather than in a YAML file nobody reads.
        """
        analyzer = data.get("analyzer")
        analyzer_id: str | None = None
        if isinstance(analyzer, Mapping):
            raw = analyzer.get("model_id")
            analyzer_id = None if raw is None else str(raw)
        if analyzer_id is not None and not allow_non_commercial_analyzer:
            blocked = blocked_analyzer_reason(analyzer_id)
            if blocked is not None:
                raise BlockedAnalyzerError(
                    f"beat grid was produced by '{analyzer_id}' ({blocked}); "
                    "planning a shippable cut against it would ship the licence "
                    "breach. Re-analyse with librosa (ISC), or pass "
                    "allow_non_commercial_analyzer=True for research only."
                )

        raw_ts = data.get("time_signature")
        time_signature = None
        if isinstance(raw_ts, Mapping):
            time_signature = TimeSignature(
                beats_per_bar=int(raw_ts["beats_per_bar"]),  # type: ignore[call-overload]
                beat_unit=int(raw_ts["beat_unit"]),  # type: ignore[call-overload]
            )

        raw_beats = data.get("beats")
        if not isinstance(raw_beats, Sequence) or isinstance(raw_beats, (str, bytes)):
            raise ValueError("BeatGrid.beats must be an array")

        tolerance = data.get("tolerance_ms")
        confidence = data.get("bpm_confidence")
        return cls(
            source_cue_id=str(data["source_cue_id"]),
            bpm=float(data["bpm"]),  # type: ignore[arg-type]
            beats=tuple(Beat.from_contract(b) for b in raw_beats),
            bpm_confidence=None if confidence is None else float(confidence),  # type: ignore[arg-type]
            time_signature=time_signature,
            tolerance_ms=DEFAULT_TOLERANCE_MS if tolerance is None else float(tolerance),  # type: ignore[arg-type]
            analyzer_id=analyzer_id,
        )

    # -- queries -------------------------------------------------------

    def __len__(self) -> int:
        return len(self.beats)

    @property
    def downbeats(self) -> tuple[Beat, ...]:
        return tuple(b for b in self.beats if b.is_downbeat)

    def span(self) -> tuple[RationalTime, RationalTime]:
        return self.beats[0].time, self.beats[-1].time


def blocked_analyzer_reason(model_id: str) -> str | None:
    """Licence reason if `model_id` names a non-commercial analyser, else None.

    Matches on the stem so registry ids like `madmom-dbn-downbeat` are caught,
    but only at a `-`/`_` boundary: a substring match would flag a
    hypothetical `essentiary-beats` and a bare equality match would miss every
    real registry id, which carries a variant suffix.
    """
    lowered = model_id.strip().lower()
    for stem in sorted(NON_COMMERCIAL_ANALYZERS):
        if lowered == stem or lowered.startswith(stem + "-") or lowered.startswith(stem + "_"):
            return NON_COMMERCIAL_ANALYZERS[stem]
    return None


# ---------------------------------------------------------------------------
# Grid diagnostics -- tempo drift and analysis holes
# ---------------------------------------------------------------------------


def interval_after(grid: BeatGrid, index: int) -> Fraction | None:
    """Seconds from beat `index` to the next beat, or None for the last beat.

    THE local period. Everything that needs "how long is a beat here" uses
    this and never `grid.bpm`: bpm is a single number for a track that
    accelerates, and by bar 40 an extrapolated grid is a beat and a half out.
    That is precisely why the contract stores per-beat times at all.
    """
    if not 0 <= index < len(grid.beats):
        raise IndexError(f"beat index {index} out of range for grid of {len(grid.beats)}")
    if index == len(grid.beats) - 1:
        return None
    return grid._seconds[index + 1] - grid._seconds[index]


def local_bpm(grid: BeatGrid, index: int) -> float | None:
    """Instantaneous tempo at `index` from the actual interval, or None."""
    interval = interval_after(grid, index)
    if interval is None:
        return None
    return float(60 / interval)


def tempo_anomalies(grid: BeatGrid, *, ratio: float = 1.5) -> tuple[int, ...]:
    """Indices whose interval-to-next deviates from the median by > `ratio`.

    A dropped beat shows up as one interval of ~2x; a spurious beat as two of
    ~0.5x. Reported, never acted on: the cut planner works off real beat times
    and is unharmed by a wrong beat *count*, but a plan that locks a cut inside
    an analysis hole will look off, so a caller that cares can steer around it.
    Silently deleting suspect beats would change beat_index for every later
    lock, which is worse than reporting.
    """
    if ratio <= 1:
        raise ValueError(f"ratio must be > 1, got {ratio}")
    if len(grid.beats) < 3:
        return ()
    intervals = [grid._seconds[i + 1] - grid._seconds[i] for i in range(len(grid.beats) - 1)]
    ordered = sorted(intervals)
    middle = len(ordered) // 2
    if len(ordered) % 2 == 1:
        median = ordered[middle]
    else:
        # Exact rational mean; averaging as floats here would make the
        # anomaly set depend on rounding at the threshold.
        median = (ordered[middle - 1] + ordered[middle]) / 2
    ratio_f = _to_fraction(ratio, what="ratio")
    return tuple(
        index
        for index, interval in enumerate(intervals)
        if interval > median * ratio_f or interval * ratio_f < median
    )


def nearest_beat(
    grid: BeatGrid,
    time: RationalTime,
    *,
    downbeats_only: bool = False,
    min_strength: float | None = None,
) -> Beat:
    """Beat closest to `time`. Exact ties go to the EARLIER beat.

    A cut that is equidistant between two beats is arbitrary either way; the
    earlier one is chosen because a cut that anticipates the beat reads as
    tight and one that lags reads as a dropped frame. `b.index` is the
    tie-break key and beats are strictly increasing, so earlier index == earlier
    time.
    """
    target = time.seconds
    best: Beat | None = None
    best_key: tuple[Fraction, int] | None = None
    for beat in grid.beats:
        if not _beat_eligible(beat, downbeats_only=downbeats_only, min_strength=min_strength):
            continue
        key = (abs(target - beat.seconds), beat.index)
        if best_key is None or key < best_key:
            best, best_key = beat, key
    if best is None:
        raise ValueError("no beat satisfies the requested filters")
    return best


def beats_between(grid: BeatGrid, start: RationalTime, end: RationalTime) -> tuple[Beat, ...]:
    """Beats in [start, end). Half-open, matching every range in the contract."""
    if end < start:
        raise ValueError("end must not precede start")
    lo, hi = start.seconds, end.seconds
    return tuple(b for b in grid.beats if lo <= b.seconds < hi)


def _beat_eligible(
    beat: Beat, *, downbeats_only: bool, min_strength: float | None
) -> bool:
    if downbeats_only and not beat.is_downbeat:
        return False
    if min_strength is not None:
        # A beat with no strength cannot satisfy a strength floor. Treating
        # unknown as "passes" would make the filter a no-op on exactly the
        # grids where the analyser was least sure -- the ones it exists for.
        if beat.strength is None or beat.strength < min_strength:
            return False
    return True


# ---------------------------------------------------------------------------
# Cut planning
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SnapPoint:
    """A content-certified position a cut may land on.

    Comes from MomentRecord: a shot boundary, a motion onset, a stabilised
    frame. Landing on one is what makes a cut feel like it belongs to the
    footage rather than to the grid.
    """

    time: RationalTime
    kind: str

    def __post_init__(self) -> None:
        if not self.kind:
            raise ValueError("SnapPoint.kind must be a non-empty string")


@dataclass(frozen=True)
class LockPolicy:
    """How the three-way fight between music, frames and content is settled.

    All budgets are in milliseconds so they are comparable to each other and to
    the quality gate; they are converted to exact Fractions before any
    comparison happens.
    """

    # None means "use the grid's own tolerance_ms". The grid is the authority
    # because tolerance is a property of how well that track was analysed.
    tolerance_ms: float | None = None

    # Hard ceiling on downbeat alignment error, applied on top of tolerance.
    downbeat_gate_ms: float = DOWNBEAT_GATE_MS

    # How far the cut may move from the content-ideal point at all. Beyond
    # this the cut is no longer the cut the story wanted, and we would rather
    # have an unlocked cut in the right place than a locked cut in the wrong
    # one. ~120ms is about four frames at 30fps: perceptible as timing, not as
    # a different edit.
    max_content_drift_ms: float = 120.0

    # Content drift we will happily spend to land on a downbeat rather than an
    # off-beat. This is the "deliberate vs mistake" knob.
    downbeat_bonus_ms: float = 30.0

    # Content drift we will spend to land on a certified snap point rather than
    # on bare frame quantisation of the beat.
    snap_bonus_ms: float = 20.0

    # Relative price of beat error against content drift. 1.0 = a millisecond
    # off the beat costs the same as a millisecond off the moment.
    alignment_weight: float = 1.0

    # Below this the grid is not trustworthy enough to lock to at all.
    min_bpm_confidence: float = 0.5

    # Optional per-beat floor; None disables. Use it to refuse to lock in a
    # section the tracker was unsure about (a breakdown, an ambient intro)
    # rather than locking to a beat that may not be there.
    min_beat_strength: float | None = None

    # Only downbeats are lockable.
    downbeats_only: bool = False

    # Shortest clip a sequence plan may produce. 1 frame is a flash, not a
    # shot; 2 is the floor at which a cut is a cut.
    min_clip_frames: int = 2

    def __post_init__(self) -> None:
        for name in (
            "downbeat_gate_ms",
            "max_content_drift_ms",
            "downbeat_bonus_ms",
            "snap_bonus_ms",
            "alignment_weight",
        ):
            value = getattr(self, name)
            if value < 0:
                raise ValueError(f"LockPolicy.{name} must be >= 0, got {value}")
        if self.tolerance_ms is not None and self.tolerance_ms < 0:
            raise ValueError("LockPolicy.tolerance_ms must be >= 0")
        if not 0.0 <= self.min_bpm_confidence <= 1.0:
            raise ValueError("LockPolicy.min_bpm_confidence must be in [0,1]")
        if self.min_beat_strength is not None and not 0.0 <= self.min_beat_strength <= 1.0:
            raise ValueError("LockPolicy.min_beat_strength must be in [0,1]")
        if self.min_clip_frames < 1:
            raise ValueError("LockPolicy.min_clip_frames must be >= 1")

    def effective_tolerance_ms(self, grid: BeatGrid) -> float:
        return grid.tolerance_ms if self.tolerance_ms is None else self.tolerance_ms

    def gate_ms(self, grid: BeatGrid, *, is_downbeat: bool) -> Fraction:
        tolerance = _to_fraction(self.effective_tolerance_ms(grid), what="tolerance_ms")
        if not is_downbeat:
            return tolerance
        # min(), not the downbeat gate alone: tightening tolerance below 50ms
        # must tighten downbeats too, and loosening it above 50ms must not
        # loosen them.
        return min(tolerance, _to_fraction(self.downbeat_gate_ms, what="downbeat_gate_ms"))


DEFAULT_LOCK_POLICY = LockPolicy()


@dataclass(frozen=True)
class CutDecision:
    """Where a cut goes, and whether it is honestly beat-locked."""

    time: RationalTime
    locked: bool
    reason: str
    beat_index: int | None
    is_downbeat: bool
    alignment_error_ms: float | None
    content_drift_ms: float
    snap_point_kind: str | None

    def to_beat_lock(self) -> dict[str, object] | None:
        """Contract `BeatLock`, or None when the cut is not locked.

        None rather than a BeatLock with a large error: `BeatLock` means "this
        cut was placed against the music". Emitting one for a cut that missed
        would corrupt the very audit trail the field exists to provide.
        """
        if not self.locked:
            return None
        assert self.beat_index is not None and self.alignment_error_ms is not None
        return {
            "beat_index": self.beat_index,
            "is_downbeat": self.is_downbeat,
            "alignment_error_ms": self.alignment_error_ms,
            "snap_point_kind": self.snap_point_kind,
        }


def alignment_error(grid: BeatGrid, beat_index: int, cut: RationalTime) -> Fraction:
    """Exact signed seconds from the beat to the cut. Negative = cut is early."""
    if not 0 <= beat_index < len(grid.beats):
        raise IndexError(f"beat_index {beat_index} out of range for {len(grid.beats)} beats")
    return cut.seconds - grid.beats[beat_index].seconds


def alignment_error_ms(grid: BeatGrid, beat_index: int, cut: RationalTime) -> float:
    """`alignment_error` in milliseconds, rounded for emission."""
    return _round_half_away(_ms(alignment_error(grid, beat_index, cut)), EMITTED_MS_DECIMALS)


def lock_cut(
    grid: BeatGrid,
    ideal: RationalTime,
    *,
    policy: LockPolicy = DEFAULT_LOCK_POLICY,
    rate: object | None = None,
    snap_points: Iterable[SnapPoint] = (),
    earliest: RationalTime | None = None,
    exclude_beat_indices: frozenset[int] = frozenset(),
) -> CutDecision:
    """Place one cut as near `ideal` as the music and the frame grid allow.

    `ideal` is where the CONTENT wants the cut. `rate` is the timeline rate
    (defaults to `ideal`'s) -- a cut may only exist on an integer frame of it.
    `earliest` is a floor from the previous cut. `exclude_beat_indices` are
    beats already claimed by an earlier cut.

    Scores every (frame, beat) pair, not just the nearest beat to the nearest
    frame. Evaluating them jointly is what lets a downbeat one frame further
    away beat an off-beat that is marginally closer, and what stops a nearest
    beat that fails its own gate from vetoing a further beat that passes a
    looser one.
    """
    timeline_rate = ideal.rate if rate is None else _to_fraction(rate, what="rate")
    if timeline_rate <= 0:
        raise ValueError(f"rate must be > 0, got {timeline_rate}")
    ideal_at = ideal.rescaled_to(timeline_rate)
    ideal_seconds = ideal_at.seconds

    earliest_frame = None if earliest is None else ceil_frame(earliest, timeline_rate)

    drift_budget = _ms_to_seconds(policy.max_content_drift_ms, what="max_content_drift_ms")
    lo_frame = math.ceil((ideal_seconds - drift_budget) * timeline_rate)
    hi_frame = math.floor((ideal_seconds + drift_budget) * timeline_rate)
    if earliest_frame is not None:
        lo_frame = max(lo_frame, earliest_frame)

    # Widest gate any beat could use, for the pre-filter below.
    max_gate = max(
        policy.gate_ms(grid, is_downbeat=False),
        policy.gate_ms(grid, is_downbeat=True),
    )
    beat_window = drift_budget + max_gate / 1000

    grid_trusted = grid.bpm_confidence is None or grid.bpm_confidence >= policy.min_bpm_confidence

    eligible: list[Beat] = []
    if grid_trusted:
        for beat in grid.beats:
            if beat.index in exclude_beat_indices:
                continue
            if not _beat_eligible(
                beat,
                downbeats_only=policy.downbeats_only,
                min_strength=policy.min_beat_strength,
            ):
                continue
            # Any beat further than drift+gate from the ideal cannot be within
            # gate of ANY admissible frame, so dropping it changes no outcome.
            if abs(beat.seconds - ideal_seconds) > beat_window:
                continue
            eligible.append(beat)

    # The admissible frame closest to the content ideal, clamped into the
    # window. Seeding with the clamped value rather than the bare ideal frame
    # matters when `earliest` has pushed the window past the beat: the beat's
    # own floor/ceil frames are then inadmissible, and without this the
    # candidate set comes out EMPTY and a cut that could honestly have locked
    # two frames later is reported unlocked.
    ideal_frame = nearest_frame(ideal_at, timeline_rate)
    if lo_frame <= hi_frame:
        ideal_frame = min(max(ideal_frame, lo_frame), hi_frame)

    # Candidate frames, as (frame, snap_kind). A frame reachable both by
    # quantising a beat and by a snap point appears twice on purpose: the snap
    # variant carries the bonus and records the kind, the bare variant does
    # not, and letting them compete is how the bonus actually decides anything.
    candidates: list[tuple[int, str | None]] = [(ideal_frame, None)]
    for beat in grid.beats:
        if abs(beat.seconds - ideal_seconds) > beat_window:
            continue
        at_rate = beat.time.rescaled_to(timeline_rate)
        # Both neighbours, never a single rounding: the beat sits between two
        # frames and which one is better depends on the content drift as well
        # as on the beat error, so the choice cannot be made here.
        candidates.append((math.floor(at_rate.value), None))
        candidates.append((math.ceil(at_rate.value), None))
    for snap in snap_points:
        candidates.append((nearest_frame(snap.time, timeline_rate), snap.kind))

    admissible = sorted(
        {(f, k) for (f, k) in candidates if lo_frame <= f <= hi_frame},
        key=lambda item: (item[0], item[1] or ""),
    )

    weight = _to_fraction(policy.alignment_weight, what="alignment_weight")
    downbeat_bonus = _to_fraction(policy.downbeat_bonus_ms, what="downbeat_bonus_ms")
    snap_bonus = _to_fraction(policy.snap_bonus_ms, what="snap_bonus_ms")

    best_key: tuple[Fraction, Fraction, Fraction, int, int, int, str] | None = None
    best: CutDecision | None = None

    for frame, kind in admissible:
        cut_seconds = Fraction(frame) / timeline_rate
        drift = _ms(cut_seconds - ideal_seconds)
        for beat in eligible:
            error = _ms(cut_seconds - beat.seconds)
            if abs(error) > policy.gate_ms(grid, is_downbeat=beat.is_downbeat):
                continue
            cost = abs(drift) + weight * abs(error)
            if beat.is_downbeat:
                cost -= downbeat_bonus
            if kind is not None:
                cost -= snap_bonus
            # Total order, every component exact. The trailing frame/index/kind
            # terms are what make two genuinely equal-cost options resolve the
            # same way on every machine and every run.
            key = (
                cost,
                abs(error),
                abs(drift),
                0 if beat.is_downbeat else 1,
                frame,
                beat.index,
                kind or "",
            )
            if best_key is None or key < best_key:
                best_key = key
                best = CutDecision(
                    time=frame_time(frame, timeline_rate),
                    locked=True,
                    reason=REASON_LOCKED_DOWNBEAT if beat.is_downbeat else REASON_LOCKED_BEAT,
                    beat_index=beat.index,
                    is_downbeat=beat.is_downbeat,
                    alignment_error_ms=_round_half_away(error, EMITTED_MS_DECIMALS),
                    content_drift_ms=_round_half_away(drift, EMITTED_MS_DECIMALS),
                    snap_point_kind=kind,
                )

    if best is not None:
        return best

    # ---- lock broken. Place the cut honestly and say why. ----
    if not grid_trusted:
        reason = REASON_GRID_CONFIDENCE
    elif not eligible:
        reason = REASON_NO_ELIGIBLE_BEAT
    elif not admissible:
        reason = REASON_NO_CANDIDATE
    else:
        reason = REASON_OUTSIDE_TOLERANCE

    if admissible:
        # No beat works, so content is the only remaining authority: nearest to
        # ideal, with the snap bonus still applied because a real motion onset
        # is still better than an arbitrary frame.
        def fallback_key(item: tuple[int, str | None]) -> tuple[Fraction, Fraction, int, str]:
            frame, kind = item
            drift = abs(_ms(Fraction(frame) / timeline_rate - ideal_seconds))
            adjusted = drift - (snap_bonus if kind is not None else Fraction(0))
            return (adjusted, drift, frame, kind or "")

        frame, kind = min(admissible, key=fallback_key)
    else:
        # Even the ideal frame was excluded -- `earliest` pushed past the drift
        # window. The min-clip constraint wins: a clip shorter than the floor
        # is not a shot, and overlapping clips are a renderer crash.
        frame = lo_frame if earliest_frame is not None else nearest_frame(ideal_at, timeline_rate)
        kind = None

    drift = _ms(Fraction(frame) / timeline_rate - ideal_seconds)
    return CutDecision(
        time=frame_time(frame, timeline_rate),
        locked=False,
        reason=reason,
        beat_index=None,
        is_downbeat=False,
        alignment_error_ms=None,
        content_drift_ms=_round_half_away(drift, EMITTED_MS_DECIMALS),
        snap_point_kind=kind,
    )


def plan_cuts(
    grid: BeatGrid,
    ideals: Sequence[RationalTime],
    *,
    policy: LockPolicy = DEFAULT_LOCK_POLICY,
    rate: object | None = None,
    snap_points: Iterable[SnapPoint] = (),
) -> tuple[CutDecision, ...]:
    """Lock a whole sequence of cuts, left to right.

    Guarantees, in order of how badly their absence breaks a render:

      * strictly increasing cut times -- overlapping clips are undefined;
      * every clip at least `min_clip_frames` long;
      * no beat claimed twice. Two clips locked to the same beat_index would
        each declare themselves on the beat while one of them plainly is not,
        and the min-clip floor alone does not prevent it: two frames either
        side of one beat are both within tolerance of it.

    THE SHOT SHORTER THAN A BEAT

    When two ideal cuts fall inside one beat interval, the second cannot have
    that beat and the next beat is normally outside its drift budget. It comes
    back UNLOCKED, sitting where the content wanted it. That is the musically
    correct answer -- a fill or a triplet is not on the grid -- and, more
    importantly, it is the honest one. Stretching the shot to the next beat
    would silently rewrite the edit, and collapsing both onto one beat would
    emit a zero-length clip.

    `ideals` must be strictly increasing. Sorting them here would silently
    break the caller's ideal-to-decision correspondence in the returned tuple;
    a caller with unordered content points has a bug upstream.
    """
    ordered = tuple(ideals)
    for position in range(1, len(ordered)):
        if ordered[position] <= ordered[position - 1]:
            raise ValueError(
                f"ideals must be strictly increasing; index {position} "
                f"({float(ordered[position].seconds)}s) does not follow index "
                f"{position - 1} ({float(ordered[position - 1].seconds)}s)"
            )

    timeline_rate = _to_fraction(rate, what="rate") if rate is not None else None
    snaps = tuple(snap_points)

    decisions: list[CutDecision] = []
    used_beats: list[int] = []  # membership only; never iterated for ordering
    previous: CutDecision | None = None
    for ideal in ordered:
        this_rate = timeline_rate if timeline_rate is not None else ideal.rate
        earliest = None
        if previous is not None:
            earliest = previous.time + frame_time(policy.min_clip_frames, this_rate)
        decision = lock_cut(
            grid,
            ideal,
            policy=policy,
            rate=this_rate,
            snap_points=snaps,
            earliest=earliest,
            exclude_beat_indices=frozenset(used_beats),
        )
        if decision.beat_index is not None:
            used_beats.append(decision.beat_index)
        decisions.append(decision)
        previous = decision
    return tuple(decisions)


# ---------------------------------------------------------------------------
# Audit -- verifying a plan someone else emitted
# ---------------------------------------------------------------------------


def audit_lock(
    grid: BeatGrid,
    cut: RationalTime,
    lock: Mapping[str, object],
    *,
    policy: LockPolicy = DEFAULT_LOCK_POLICY,
) -> tuple[str, ...]:
    """Check a declared `BeatLock` against the grid. Empty tuple == clean.

    This is the <50ms gate, evaluated from the plan alone with nothing
    rendered. It re-derives the alignment error rather than trusting the
    declared one, because the declared number is precisely what a broken
    planner would get wrong while still producing schema-valid output.
    """
    violations: list[str] = []
    raw_index = lock.get("beat_index")
    if not isinstance(raw_index, int) or isinstance(raw_index, bool):
        return (f"beat_index must be an integer, got {raw_index!r}",)
    if not 0 <= raw_index < len(grid.beats):
        return (f"beat_index {raw_index} out of range for {len(grid.beats)} beats",)

    beat = grid.beats[raw_index]
    declared_downbeat = bool(lock.get("is_downbeat", False))
    if declared_downbeat != beat.is_downbeat:
        violations.append(
            f"is_downbeat declared {declared_downbeat} but beat {raw_index} "
            f"is_downbeat={beat.is_downbeat}"
        )

    recomputed = _ms(alignment_error(grid, raw_index, cut))
    raw_declared = lock.get("alignment_error_ms")
    if not isinstance(raw_declared, (int, float)) or isinstance(raw_declared, bool):
        violations.append(f"alignment_error_ms must be a number, got {raw_declared!r}")
    else:
        delta = abs(float(recomputed) - float(raw_declared))
        if delta > AUDIT_EPSILON_MS:
            violations.append(
                f"alignment_error_ms declared {float(raw_declared)} but cut is "
                f"{_round_half_away(recomputed, EMITTED_MS_DECIMALS)}ms from beat {raw_index}"
            )

    gate = policy.gate_ms(grid, is_downbeat=beat.is_downbeat)
    if abs(recomputed) > gate:
        violations.append(
            f"beat {raw_index} alignment {_round_half_away(recomputed, EMITTED_MS_DECIMALS)}ms "
            f"exceeds the {float(gate)}ms gate"
        )
    return tuple(violations)
