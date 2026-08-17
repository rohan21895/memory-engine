"""Reel planner: selected moments + a beat grid + a target duration -> one EDL.

WHAT THIS MODULE IS RESPONSIBLE FOR

Every creative decision in a finished reel: which moment goes where, how long
each shot is held, where the cut lands relative to the music, how a 16:9 source
is cropped to 9:16 and which subject the crop follows, what the music does under
speech, and what story the sequence is trying to tell. All of it is written into
an `EDL` dict that validates against `contracts/schemas/edl.schema.json`.

The renderer receives that plan and decides nothing (AGENTS.md hard rule 3). If
a render would need a judgement call, the judgement is missing from here.

DETERMINISM, WHICH IS THE WHOLE POINT

`plan_reel` is a pure function of its request. Same request in, byte-identical
EDL out, on any machine:

  * No wall clock. `generated_at` and `validated_at` are REQUEST fields. A
    planner that called `datetime.now()` would emit a different `edl_id` every
    run, which would make the id useless as the cache key it exists to be.
  * No randomness. `seed` is recorded because `Determinism` requires it and
    because a future variant sampler will want it; nothing here is stochastic.
    Every tie is broken by an explicit, stated rule -- usually "then by
    moment_id ascending" -- so no result ever depends on dict or set ordering.
  * No floats where a decision hinges on the last bit. Positions are integer
    frames; rounding is half-away-from-zero via `decimal`, never Python's
    banker's `round()` and never `floor(x + 0.5)` (which returns 1 for
    0.49999999999999994, because the addition rounds up before the floor does).

TIME MODEL

Every time in a request is a number of FRAMES AT THE TIMELINE RATE, source and
timeline alike. The contract permits a mixed rate only on audio tracks, and the
OTIO header asks for one rate everywhere else, so a request carrying 29.97
source times for a 59.94 timeline is rejected rather than silently converted --
`moment_from_record` is where a contract record is converted, once, loudly.

Ranges are half-open, `[start, start + duration)`, exactly as OTIO and
`common.schema.json#TimeRange` define them, so clip N+1 starts on the frame
clip N stops. A closed convention here would cost one duplicated frame per cut,
which at 30 cuts is half a second of stutter nobody could locate.

WHAT "BEAT-LOCKED" MEANS HERE

Cuts land on beats; the beat grid is authored in TIMELINE time (the contract
says so) so the planner never has to reason about where the music was trimmed
from. Within a moment, the SOURCE in-point is chosen from the certified snap
points that MomentRecord carries -- the planner does not invent cut positions,
it chooses among the ones analysis certified (moment-record.schema.json's
second design decision). `BeatLock.alignment_error_ms` records the residual so
the <50ms downbeat gate is measurable from the plan alone.

A target duration that no combination of moments hits exactly is the normal
case, not an error: the reel ends on the grid point nearest the target,
preferring the shorter one on a tie, and `target.target_duration` keeps saying
what was asked for. Landing on a beat matters more than hitting 15.000s.

RELATIONSHIP TO `beats.py`

`beats.py` (same package, authored in parallel) owns the general cut-locking
policy with exact `Fraction` arithmetic. This module deliberately does not
import it: coupling a planner to a module being written in the same session
would mean either suite can fail for the other's reasons, and the __init__
docstring's warning about dependency edges applies to submodules too. The two
share conventions on purpose -- signed alignment error with "negative is
early", milliseconds rounded half-away-from-zero to 4 decimals -- so folding
this module onto `beats.lock_cut` later is mechanical rather than a rewrite.
That consolidation is a stated follow-up, not an oversight.

WHAT IS DELIBERATELY NOT PLANNED HERE

  * Speed ramps (`TimeEffect`). A 0.5x time_scalar is only cinema if the source
    was shot at a high frame rate; on 30fps footage it is judder. The planner
    is not told the source's capture rate reliably enough to decide, so it
    emits none rather than a decision it cannot justify.
  * Colour grades (`ColorOp`). Same reason: no scene analysis is passed in.
  * Variant sets. `variant` is passed through, but `sibling_edl_ids` cannot be
    computed by a planner that hashes its own output -- each sibling's id would
    depend on every other's. Whoever mints a variant set fills those in after,
    which is why they are excluded from `edl_id` (see `EDL_ID_EXCLUDED`).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from fractions import Fraction
from typing import Any, Callable, Iterable, Mapping, Sequence

__all__ = [
    "ARC_TEMPLATES",
    "AmbientSettings",
    "Beat",
    "BeatGrid",
    "DEFAULT_DUCK_REDUCTION_DB",
    "EDL_ID_EXCLUDED",
    "MusicLicense",
    "MusicTrack",
    "PLANNER_ID",
    "PLANNER_VERSION",
    "RateMismatch",
    "ReelPlan",
    "ReelRequest",
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
    "blake3_hex",
    "canonical_json",
    "clearance_for_destination",
    "moment_from_record",
    "plan_reel",
]

PLANNER_ID = "reel-planner"
PLANNER_VERSION = "1.0.0"

SCHEMA_VERSION = "v0"

# Milliseconds are emitted at 4 decimals: 0.1 microsecond, far finer than the
# 50ms gate and coarse enough that the last float bit never shows up in a diff.
EMITTED_MS_DECIMALS = 4

DEFAULT_DUCK_REDUCTION_DB = 9.0

# A shot below this reads as a flash frame rather than an edit. Overridable per
# moment by SafeTrim.min_duration, which is the analysis layer's stronger claim.
DEFAULT_MIN_CLIP_FRAMES = 8

# Fields removed before `edl_id` is computed. The contract names two
# ("generated_at, timeline_range"); the other two follow from the same rule --
# an id that changed when the clock ticked, or when a sibling variant was
# planned, would not be the render-cache key it exists to be.
EDL_ID_EXCLUDED = (
    "edl_id",
    "determinism.generated_at",
    "validation.validated_at",
    "tracks[].items[].timeline_range",
    "variant.sibling_edl_ids",
)

ARC_TEMPLATES = ("hook_build_peak_button", "chronological")

# Where a cut may be published, per destination. `master` and `web_preview`
# never leave the machine, so private playback covers them; everything else is
# a share. A track cleared only for private_playback must not back an Instagram
# cut, and the plan is where that becomes checkable rather than discovered
# after upload (edl.schema.json, MusicLicense.cleared_for).
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

_SLUG_CHARS = set("abcdefghijklmnopqrstuvwxyz0123456789_-")
_HEX_CHARS = set("0123456789abcdef")

_SNAP_IN_DIRECTIONS = frozenset({"in", "both"})

_VALID_CUT_DIRECTIONS = frozenset({"in", "out", "both"})


class RateMismatch(ValueError):
    """A contract time was expressed at a rate other than the timeline rate."""


class SchemaVersionUnsupported(ValueError):
    """A contract record was written against a schema version we do not know.

    Refusing beats guessing: common.schema.json#SchemaVersion says a reader
    that does not recognise the value must refuse the record (hard rule 7).
    """


# --------------------------------------------------------------------------
# Canonical JSON and BLAKE3
# --------------------------------------------------------------------------
#
# `edl_id` and `inputs_digest` are BLAKE3 over "canonical JSON", per the EDL
# contract. Canonical here means RFC 8785 (JCS) in the two places that actually
# differ between languages:
#
#   * keys sorted by their UTF-16 code units, which for our ASCII slugs is
#     plain byte order;
#   * numbers formatted by the ECMAScript Number-to-String algorithm, so 1.0
#     serialises as "1", 1e-7 as "1e-7" and -0.0 as "0".
#
# The number rule is the load-bearing one. Python's repr writes `1.0`, `1e-07`
# and `-0.0`; JavaScript writes `1`, `1e-7` and `0`. models/policy/digest.py
# rejected canonical JSON for model configs for exactly this reason and hashes
# raw file bytes instead -- but an EDL has no file bytes at the moment it is
# hashed (the id goes INSIDE the file), so the choice is between implementing
# JCS once, here, and having a digest the Rust renderer cannot reproduce. JCS
# it is, with the ES number rule spelled out below so the other two languages
# have something to match rather than a Python opinion to reverse-engineer.


def _es_number(value: float | int) -> str:
    """Format a number the way ECMAScript's String(Number) does (RFC 8785 §3.2.2.3)."""
    if isinstance(value, bool):  # bool is an int subclass; check it first.
        raise TypeError("bool is not a JSON number")
    if isinstance(value, int):
        return str(value)
    if not math.isfinite(value):
        # NaN and Infinity have no JSON form. Emitting "null" (json's default
        # is to emit bare NaN, which is not JSON at all) would turn a broken
        # number into a plausible absent one.
        raise ValueError(f"not a finite JSON number: {value!r}")
    if value == 0:
        return "0"  # covers -0.0, which ECMAScript also prints as "0"
    sign = "-" if value < 0 else ""
    digits_tuple = Decimal(repr(abs(value))).as_tuple()
    digits = "".join(str(d) for d in digits_tuple.digits).rstrip("0") or "0"
    # value = digits * 10**exponent, with the trailing zeros folded back in.
    exponent = int(digits_tuple.exponent) + (
        len(digits_tuple.digits) - len(digits)
    )
    k = len(digits)
    n = exponent + k  # position of the decimal point relative to `digits`
    if k <= n <= 21:
        return sign + digits + "0" * (n - k)
    if 0 < n <= 21:
        return sign + digits[:n] + "." + digits[n:]
    if -6 < n <= 0:
        return sign + "0." + "0" * (-n) + digits
    exp = n - 1
    mantissa = digits if k == 1 else digits[0] + "." + digits[1:]
    return sign + mantissa + ("e+" if exp >= 0 else "e-") + str(abs(exp))


def _canonical_chunks(obj: Any, out: list[str]) -> None:
    if obj is None:
        out.append("null")
    elif obj is True:
        out.append("true")
    elif obj is False:
        out.append("false")
    elif isinstance(obj, str):
        out.append(_json_string(obj))
    elif isinstance(obj, (int, float)):
        out.append(_es_number(obj))
    elif isinstance(obj, Mapping):
        out.append("{")
        # Sorted by code unit. Our keys are ASCII slugs, where code-unit order
        # and byte order coincide; the sort is stated anyway because a future
        # non-ASCII key must not silently reorder between languages.
        for i, key in enumerate(sorted(obj, key=_utf16_key)):
            if not isinstance(key, str):
                raise TypeError(f"non-string JSON key: {key!r}")
            if i:
                out.append(",")
            out.append(_json_string(key))
            out.append(":")
            _canonical_chunks(obj[key], out)
        out.append("}")
    elif isinstance(obj, (list, tuple)):
        out.append("[")
        for i, item in enumerate(obj):
            if i:
                out.append(",")
            _canonical_chunks(item, out)
        out.append("]")
    else:
        raise TypeError(f"not JSON-serialisable: {type(obj).__name__}")


def _utf16_key(key: Any) -> tuple[int, ...]:
    if not isinstance(key, str):
        raise TypeError(f"non-string JSON key: {key!r}")
    return tuple(key.encode("utf-16-be"))


_JSON_ESCAPES = {
    '"': '\\"',
    "\\": "\\\\",
    "\b": "\\b",
    "\f": "\\f",
    "\n": "\\n",
    "\r": "\\r",
    "\t": "\\t",
}


def _json_string(text: str) -> str:
    out = ['"']
    for ch in text:
        escape = _JSON_ESCAPES.get(ch)
        if escape is not None:
            out.append(escape)
        elif ch < "\x20":
            out.append(f"\\u{ord(ch):04x}")
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def canonical_json(obj: Any) -> bytes:
    """RFC 8785-style canonical JSON bytes for `obj` (UTF-8, sorted keys)."""
    out: list[str] = []
    _canonical_chunks(obj, out)
    return "".join(out).encode("utf-8")


# --- BLAKE3 -----------------------------------------------------------------
#
# The contract types these ids as BLAKE3 and nothing else. The `blake3` package
# is not installed in CI (see .github/workflows/ci.yml, which installs only
# pydantic and jsonschema), and substituting blake2b would produce a 64-hex
# string that looks exactly like a BLAKE3 digest and is not one -- the precise
# shape of silent defect this repo keeps finding. So: use the package when it
# is importable, and otherwise compute real BLAKE3 here. Both paths produce the
# same bytes, which is what test_reel.py checks against the official vectors.

_IV = (
    0x6A09E667,
    0xBB67AE85,
    0x3C6EF372,
    0xA54FF53A,
    0x510E527F,
    0x9B05688C,
    0x1F83D9AB,
    0x5BE0CD19,
)
_MSG_PERMUTATION = (2, 6, 3, 10, 7, 0, 4, 13, 1, 11, 12, 5, 9, 14, 15, 8)
_CHUNK_START = 1
_CHUNK_END = 2
_PARENT = 4
_ROOT = 8
_BLOCK_LEN = 64
_CHUNK_LEN = 1024
_MASK32 = 0xFFFFFFFF


def _rotr32(x: int, n: int) -> int:
    return ((x >> n) | (x << (32 - n))) & _MASK32


def _g(s: list[int], a: int, b: int, c: int, d: int, mx: int, my: int) -> None:
    s[a] = (s[a] + s[b] + mx) & _MASK32
    s[d] = _rotr32(s[d] ^ s[a], 16)
    s[c] = (s[c] + s[d]) & _MASK32
    s[b] = _rotr32(s[b] ^ s[c], 12)
    s[a] = (s[a] + s[b] + my) & _MASK32
    s[d] = _rotr32(s[d] ^ s[a], 8)
    s[c] = (s[c] + s[d]) & _MASK32
    s[b] = _rotr32(s[b] ^ s[c], 7)


def _compress(
    cv: Sequence[int], block: Sequence[int], counter: int, block_len: int, flags: int
) -> list[int]:
    state = [
        cv[0], cv[1], cv[2], cv[3], cv[4], cv[5], cv[6], cv[7],
        _IV[0], _IV[1], _IV[2], _IV[3],
        counter & _MASK32, (counter >> 32) & _MASK32, block_len, flags,
    ]
    m = list(block)
    for round_index in range(7):
        _g(state, 0, 4, 8, 12, m[0], m[1])
        _g(state, 1, 5, 9, 13, m[2], m[3])
        _g(state, 2, 6, 10, 14, m[4], m[5])
        _g(state, 3, 7, 11, 15, m[6], m[7])
        _g(state, 0, 5, 10, 15, m[8], m[9])
        _g(state, 1, 6, 11, 12, m[10], m[11])
        _g(state, 2, 7, 8, 13, m[12], m[13])
        _g(state, 3, 4, 9, 14, m[14], m[15])
        if round_index < 6:
            m = [m[i] for i in _MSG_PERMUTATION]
    for i in range(8):
        state[i] ^= state[i + 8]
        state[i + 8] ^= cv[i]
    return state


def _words(block: bytes) -> list[int]:
    padded = block + b"\x00" * (_BLOCK_LEN - len(block))
    return [int.from_bytes(padded[i : i + 4], "little") for i in range(0, _BLOCK_LEN, 4)]


def _chunk_state(data: bytes, counter: int, extra_flags: int) -> list[int]:
    """Compress one chunk (<= 1024 bytes); returns the full 16-word state."""
    cv = list(_IV)
    blocks = [data[i : i + _BLOCK_LEN] for i in range(0, len(data), _BLOCK_LEN)] or [b""]
    state: list[int] = []
    for index, block in enumerate(blocks):
        flags = 0
        if index == 0:
            flags |= _CHUNK_START
        if index == len(blocks) - 1:
            flags |= _CHUNK_END | extra_flags
        state = _compress(cv, _words(block), counter, len(block), flags)
        cv = state[:8]
    return state


def _subtree_cv(data: bytes, counter: int) -> list[int]:
    if len(data) <= _CHUNK_LEN:
        return _chunk_state(data, counter, 0)[:8]
    left_len = _left_len(len(data))
    left = _subtree_cv(data[:left_len], counter)
    right = _subtree_cv(data[left_len:], counter + left_len // _CHUNK_LEN)
    return _compress(_IV, left + right, 0, _BLOCK_LEN, _PARENT)[:8]


def _left_len(total: int) -> int:
    """Bytes in the left subtree: the largest power-of-two chunk count below `total`."""
    chunks = (total + _CHUNK_LEN - 1) // _CHUNK_LEN
    return (1 << ((chunks - 1).bit_length() - 1)) * _CHUNK_LEN


def _blake3_pure(data: bytes) -> str:
    if len(data) <= _CHUNK_LEN:
        state = _chunk_state(data, 0, _ROOT)
    else:
        left_len = _left_len(len(data))
        left = _subtree_cv(data[:left_len], 0)
        right = _subtree_cv(data[left_len:], left_len // _CHUNK_LEN)
        state = _compress(_IV, left + right, 0, _BLOCK_LEN, _PARENT | _ROOT)
    return b"".join(w.to_bytes(4, "little") for w in state[:8]).hex()


def _resolve_blake3() -> Callable[[bytes], str]:
    try:  # pragma: no cover - depends on whether the wheel is installed
        import blake3 as _blake3_module
    except ImportError:
        return _blake3_pure

    def _hex(data: bytes) -> str:
        return _blake3_module.blake3(data).hexdigest()

    return _hex


_BLAKE3_IMPL = _resolve_blake3()


def blake3_hex(data: bytes) -> str:
    """Lowercase hex BLAKE3-256 of `data`, matching common.schema.json#Blake3Hash."""
    return _BLAKE3_IMPL(data)


# --------------------------------------------------------------------------
# Rounding
# --------------------------------------------------------------------------


def _round_half_up(value: float) -> int:
    """Nearest integer, ties away from zero, via decimal rather than float tricks.

    `math.floor(x + 0.5)` is the usual shortcut and it is wrong: the addition
    rounds first, so 0.49999999999999994 + 0.5 == 1.0 and the "floor" returns
    1. A frame index that is one out puts every subsequent cut one frame off
    the beat, which is exactly the kind of defect that never raises.
    """
    return int(Decimal(repr(float(value))).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def _round_ms(value: float) -> float:
    """Milliseconds at `EMITTED_MS_DECIMALS`, ties away from zero."""
    quantum = Decimal(1).scaleb(-EMITTED_MS_DECIMALS)
    return float(Decimal(repr(float(value))).quantize(quantum, rounding=ROUND_HALF_UP))


def _round_unit(value: float, places: int = 6) -> float:
    quantum = Decimal(1).scaleb(-places)
    return float(Decimal(repr(float(value))).quantize(quantum, rounding=ROUND_HALF_UP))


# --------------------------------------------------------------------------
# Input types
# --------------------------------------------------------------------------


def _check_slug(value: str, what: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{what} must be a non-empty slug, got {value!r}")
    if len(value) > 64 or set(value) - _SLUG_CHARS or value[0] in "_-":
        raise ValueError(f"{what} is not a contract Slug: {value!r}")
    return value


def _check_hash(value: str, what: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or set(value) - _HEX_CHARS:
        raise ValueError(f"{what} is not a BLAKE3 hex digest: {value!r}")
    return value


def _check_finite(value: float, what: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{what} must be finite, got {value!r}")
    return number


@dataclass(frozen=True)
class SnapPoint:
    """A certified cut position inside a moment, in source frames."""

    time: float
    kind: str
    strength: float
    confidence: float | None = None
    cut_direction: str = "both"

    def __post_init__(self) -> None:
        _check_finite(self.time, "SnapPoint.time")
        if not self.kind:
            raise ValueError("SnapPoint.kind must be non-empty")
        if not 0.0 <= self.strength <= 1.0:
            raise ValueError(f"SnapPoint.strength must be in [0,1], got {self.strength}")
        if self.cut_direction not in _VALID_CUT_DIRECTIONS:
            raise ValueError(f"SnapPoint.cut_direction invalid: {self.cut_direction!r}")


@dataclass(frozen=True)
class SafeTrim:
    """Bounds a planner may trim to, in source frames (moment-record.schema.json)."""

    earliest_in: float
    latest_out: float
    speech_safe_in: float | None = None
    speech_safe_out: float | None = None
    min_duration: float | None = None
    preserve_audio_tail: bool = False

    def __post_init__(self) -> None:
        if self.latest_out <= self.earliest_in:
            raise ValueError(
                f"SafeTrim.latest_out {self.latest_out} must exceed earliest_in "
                f"{self.earliest_in}"
            )


@dataclass(frozen=True)
class Word:
    """One transcribed word, in source frames. Used only by the mid-word check."""

    start: float
    end: float
    text: str = ""


@dataclass(frozen=True)
class SubjectSample:
    """Where the tracked subject was at a given source time, normalised [0,1]."""

    time: float
    center_x: float
    center_y: float = 0.5
    confidence: float | None = None


@dataclass(frozen=True)
class SelectedMoment:
    """One moment the ranking layer selected, reduced to what the planner needs."""

    moment_id: str
    media_id: str
    source_start: float
    source_duration: float
    score: float = 0.0
    snap_points: tuple[SnapPoint, ...] = ()
    safe_trim: SafeTrim | None = None
    words: tuple[Word, ...] = ()
    hook_potential: float | None = None
    emotional_peak: float | None = None
    motion_energy: float | None = None
    speech_ratio: float | None = None
    noise_ratio: float | None = None
    subject_track: tuple[SubjectSample, ...] = ()
    label: str = ""

    def __post_init__(self) -> None:
        _check_hash(self.moment_id, "SelectedMoment.moment_id")
        _check_hash(self.media_id, "SelectedMoment.media_id")
        _check_finite(self.source_start, "SelectedMoment.source_start")
        if self.source_duration <= 0:
            raise ValueError("SelectedMoment.source_duration must be > 0")
        # A snap point may sit anywhere in the moment, including outside the
        # narrower speech-safe window -- `_choose_source_in` filters those.
        # Outside the moment entirely means the record is inconsistent, and
        # cutting there would seek somewhere nothing certified.
        for snap in self.snap_points:
            if not self.source_start <= snap.time <= self.source_end:
                raise ValueError(
                    f"moment {self.moment_id}: snap point at {snap.time} falls "
                    f"outside its source range "
                    f"[{self.source_start}, {self.source_end})"
                )
        # Same rule for safe_trim, and for the same reason. SafeTrim is "hard
        # bounds on TRIMMING" (moment-record.schema.json), and you can only
        # trim within the window analysis actually scored: `window()` hands
        # `latest_out` straight to the placement walk, so a trim wider than its
        # moment silently lets a clip leave the moment -- into footage the
        # elimination-first pass may have discarded as shaken or blown. The
        # only downstream guard, `source_range_within_available`, checks MEDIA
        # bounds, not moment bounds, so nothing else can catch this.
        trim = self.safe_trim
        if trim is not None:
            for name in (
                "earliest_in",
                "latest_out",
                "speech_safe_in",
                "speech_safe_out",
            ):
                bound = getattr(trim, name)
                if bound is None:
                    continue
                if not self.source_start <= bound <= self.source_end:
                    raise ValueError(
                        f"moment {self.moment_id}: safe_trim.{name} at {bound} "
                        f"falls outside its source range "
                        f"[{self.source_start}, {self.source_end})"
                    )

    @property
    def source_end(self) -> float:
        return self.source_start + self.source_duration

    def window(self) -> tuple[float, float]:
        """The trimmable window, in source frames.

        When speech-safe bounds exist they WIN over the wider general bounds.
        The alternative -- trim to earliest_in/latest_out and rely on a
        mid-word check afterwards -- makes the no-mid-word guarantee depend on
        a word list that is absent for most footage. Losing a few frames is
        cheaper than losing the first syllable of "did you see that".
        """
        trim = self.safe_trim
        if trim is None:
            return self.source_start, self.source_end
        low = trim.earliest_in if trim.speech_safe_in is None else trim.speech_safe_in
        high = trim.latest_out if trim.speech_safe_out is None else trim.speech_safe_out
        if high <= low:
            raise ValueError(
                f"moment {self.moment_id}: speech-safe window is empty "
                f"[{low}, {high})"
            )
        return low, high


@dataclass(frozen=True)
class SourceMedia:
    """One source, addressed by content hash (edl.schema.json#MediaRef)."""

    media_ref_id: str
    media_id: str
    available_start: float
    available_duration: float
    aspect_ratio: tuple[int, int] = (16, 9)
    media_kind: str = "video"
    is_span_assembly: bool = False
    expected_frame_rate: float | None = None
    label: str | None = None

    def __post_init__(self) -> None:
        _check_slug(self.media_ref_id, "SourceMedia.media_ref_id")
        _check_hash(self.media_id, "SourceMedia.media_id")
        if self.available_duration <= 0:
            raise ValueError("SourceMedia.available_duration must be > 0")
        if len(self.aspect_ratio) != 2 or min(self.aspect_ratio) <= 0:
            raise ValueError(f"SourceMedia.aspect_ratio invalid: {self.aspect_ratio!r}")

    @property
    def available_end(self) -> float:
        return self.available_start + self.available_duration


@dataclass(frozen=True)
class MusicLicense:
    provider: str
    license_type: str
    cleared_for: tuple[str, ...]
    license_id: str | None = None
    track_title: str | None = None
    attribution_required: bool = False
    attribution_text: str | None = None

    def __post_init__(self) -> None:
        if not self.cleared_for:
            raise ValueError("MusicLicense.cleared_for must list at least one use")


@dataclass(frozen=True)
class MusicTrack:
    media: SourceMedia
    license: MusicLicense
    cue_id: str = "cue-01"
    source_start: float = 0.0
    gain_db: float = 0.0
    fade_in: float | None = None
    fade_out: float | None = None

    def __post_init__(self) -> None:
        _check_slug(self.cue_id, "MusicTrack.cue_id")


@dataclass(frozen=True)
class Beat:
    index: int
    time: float
    is_downbeat: bool = False
    bar: int | None = None
    beat_in_bar: int | None = None
    strength: float | None = None
    section: str | None = None


@dataclass(frozen=True)
class BeatGrid:
    """Per-beat TIMELINE times. Not a BPM to extrapolate -- real tracks drift."""

    bpm: float
    beats: tuple[Beat, ...]
    time_signature: tuple[int, int] | None = (4, 4)
    bpm_confidence: float | None = None
    analyzer: Mapping[str, Any] | None = None
    tolerance_ms: float = 50.0

    def __post_init__(self) -> None:
        if not self.beats:
            raise ValueError("BeatGrid.beats must not be empty")
        previous = -math.inf
        for position, beat in enumerate(self.beats):
            if beat.index != position:
                # BeatLock.beat_index is defined as an index INTO this array.
                # If the two disagree, every beat_lock in the EDL points at the
                # wrong beat and the alignment audit silently checks nothing.
                raise ValueError(
                    f"BeatGrid.beats[{position}].index is {beat.index}; the "
                    "contract indexes beats by array position"
                )
            if beat.time <= previous:
                raise ValueError(
                    f"BeatGrid.beats must be strictly increasing; beat "
                    f"{position} at {beat.time} follows {previous}"
                )
            previous = beat.time


@dataclass(frozen=True)
class RenderTarget:
    destination: str
    resolution: tuple[int, int]
    aspect_ratio: tuple[int, int]
    target_duration: float | None = None
    max_duration: float | None = None
    loudness_target_lufs: float | None = -14.0

    def __post_init__(self) -> None:
        if self.destination not in _DESTINATION_CLEARANCE:
            raise ValueError(f"unknown destination {self.destination!r}")
        if len(self.resolution) != 2 or min(self.resolution) <= 0:
            raise ValueError(f"RenderTarget.resolution invalid: {self.resolution!r}")
        if len(self.aspect_ratio) != 2 or min(self.aspect_ratio) <= 0:
            raise ValueError(f"RenderTarget.aspect_ratio invalid: {self.aspect_ratio!r}")


@dataclass(frozen=True)
class AmbientSettings:
    """How much of the original location sound survives under the music."""

    enabled: bool = True
    default_gain_db: float = -14.0
    preserve_speech: bool = True
    high_pass_hz: float | None = 120.0
    noise_suppression: str = "light"
    # Above this noise_ratio the clip's ambient is wind, not a memory.
    wind_noise_ratio: float = 0.6
    wind_gain_db: float = -60.0
    # At or above this speech_ratio the clip carries speech worth hearing: its
    # ambient comes up and the music ducks under it.
    speech_ratio_threshold: float = 0.15
    speech_gain_db: float = -6.0
    duck_reduction_db: float = DEFAULT_DUCK_REDUCTION_DB
    duck_attack_ms: float = 60.0
    duck_release_ms: float = 320.0


@dataclass(frozen=True)
class ReframeSettings:
    """Constraints for the landscape -> vertical crop."""

    enabled: bool = True
    fallback: str = "hold_last_keyframe"
    smoothing_method: str = "savitzky_golay"
    smoothing_window_frames: int | None = 31
    # Normalised crop travel per second. The difference between a considered
    # pan and a whip; enforced on the keyframes we emit, not left to the
    # renderer, because "all creative decisions live in the plan".
    max_velocity_per_second: float = 0.35
    deadzone: float = 0.015
    keep_in_frame: str = "head"
    headroom: float | None = 0.12
    subject_source: str = "sam2_track"


@dataclass(frozen=True)
class VariantInfo:
    variant_id: str
    variant_index: int
    strategy: str
    description: str | None = None
    sibling_edl_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class ReelRequest:
    """Everything the planner reads. Its canonical JSON is the inputs_digest."""

    rate: float
    target: RenderTarget
    media: tuple[SourceMedia, ...]
    moments: tuple[SelectedMoment, ...]
    beat_grid: BeatGrid | None = None
    music: MusicTrack | None = None
    arc_template: str = "hook_build_peak_button"
    beats_per_cut: int = 4
    # Only used when there is no beat grid: nominal shot length in frames.
    unlocked_clip_frames: float = 90.0
    min_clip_frames: int = DEFAULT_MIN_CLIP_FRAMES
    name: str = ""
    title: str | None = None
    logline: str | None = None
    rationale: str | None = None
    seed: int = 0
    planner_version: str = PLANNER_VERSION
    generated_at: str | None = None
    validated_at: str | None = None
    ambient: AmbientSettings = field(default_factory=AmbientSettings)
    reframe: ReframeSettings = field(default_factory=ReframeSettings)
    # 0 means every cut is a hard cut, which is the OTIO convention: a hard cut
    # is the ABSENCE of a Transition, never a zero-length one. Above 0, the
    # final cut into the button becomes a dissolve of this many frames, but
    # only if both neighbours have that much unused source for handles.
    dissolve_frames: int = 0
    variant: VariantInfo | None = None

    def __post_init__(self) -> None:
        if self.rate <= 0 or not math.isfinite(self.rate):
            raise ValueError(f"ReelRequest.rate must be > 0, got {self.rate}")
        if not self.media:
            raise ValueError("ReelRequest.media must not be empty")
        if not self.moments:
            raise ValueError("ReelRequest.moments must not be empty")
        if self.arc_template not in ARC_TEMPLATES:
            raise ValueError(
                f"unsupported arc_template {self.arc_template!r}; "
                f"supported: {ARC_TEMPLATES}"
            )
        if self.beats_per_cut < 1:
            raise ValueError("ReelRequest.beats_per_cut must be >= 1")
        if self.min_clip_frames < 1:
            raise ValueError("ReelRequest.min_clip_frames must be >= 1")
        if self.unlocked_clip_frames <= 0:
            raise ValueError("ReelRequest.unlocked_clip_frames must be > 0")
        if self.dissolve_frames < 0:
            raise ValueError("ReelRequest.dissolve_frames must be >= 0")
        if self.seed < 0:
            raise ValueError("ReelRequest.seed must be >= 0")

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
                    f"{moment.media_id}, which is not in ReelRequest.media"
                )
            moment.window()  # raises if the speech-safe bounds are empty

        if self.music is not None and self.music.media.media_ref_id not in seen_ref:
            raise ValueError(
                "ReelRequest.music.media must also appear in ReelRequest.media, "
                "so the EDL declares every source once"
            )
        if self.music is not None:
            # The cue in-point has to leave at least one frame of track behind
            # it, or there is nothing to play and nothing to loop. Caught here
            # rather than in `_audio_plan`, where it would divide by zero.
            music_media = self.music.media
            usable = music_media.available_end - self.music.source_start
            if self.music.source_start < music_media.available_start or usable < 1:
                raise ValueError(
                    f"music cue starts at {self.music.source_start}, outside the "
                    f"usable part of its track "
                    f"[{music_media.available_start}, {music_media.available_end})"
                )
        if self.beat_grid is not None and self.music is None:
            # A beat grid is a description of a music cue; BeatGrid.source_cue_id
            # is required and there would be no cue to point it at.
            raise ValueError("a beat_grid requires a music track to describe")


@dataclass(frozen=True)
class ReelPlan:
    """The EDL, plus what the planner had to give up to produce it.

    `notes` are outside the EDL because the contract has nowhere to put them
    and inventing a field would break `additionalProperties: false`. They exist
    because a dropped moment must never be silent (hard rule 7): the caller can
    show "3 moments did not fit" rather than the user wondering where they went.
    """

    edl: dict[str, Any]
    notes: tuple[str, ...] = ()
    # Realised timeline length in frames. It lives here rather than in the EDL
    # because the EDL is additionalProperties:false and the length is derivable
    # from the tracks -- but the caller comparing "asked 15s, got 14.98s"
    # should not have to re-derive it.
    duration_frames: int = 0

    @property
    def status(self) -> str:
        return str(self.edl["validation"]["status"])

    @property
    def edl_id(self) -> str:
        return str(self.edl["edl_id"])


# --------------------------------------------------------------------------
# Contract adapter
# --------------------------------------------------------------------------


def _rational_frames(value: Mapping[str, Any], rate: float, what: str) -> float:
    if not isinstance(value, Mapping):
        raise TypeError(f"{what} must be a RationalTime object, got {value!r}")
    got = float(value["rate"])
    if got != float(rate):
        raise RateMismatch(
            f"{what} is at rate {got}, timeline rate is {rate}. Convert once, "
            "explicitly -- a silent rescale here would drift every cut."
        )
    return float(value["value"])


def moment_from_record(record: Mapping[str, Any], *, rate: float) -> SelectedMoment:
    """Build a `SelectedMoment` from a contract MomentRecord mapping.

    The single place a contract record becomes planner input, so rate
    mismatches and unknown schema versions are caught once and loudly.
    """
    version = record.get("schema_version")
    if version != SCHEMA_VERSION:
        raise SchemaVersionUnsupported(
            f"MomentRecord schema_version {version!r} is not {SCHEMA_VERSION!r}; "
            "refusing rather than guessing (common.schema.json#SchemaVersion)"
        )
    elimination = record.get("elimination") or {}
    if elimination.get("eliminated"):
        # Elimination is a decision the analysis layer already made. A planner
        # that quietly reinstates a shaken, blown-out window would make the
        # elimination-first design (moment-record.schema.json) a lie.
        raise ValueError(
            f"moment {record.get('moment_id')} was eliminated "
            f"({', '.join(elimination.get('reasons', ())) or 'no reason given'}); "
            "the planner does not resurrect eliminated moments"
        )

    source_range = record["source_range"]
    start = _rational_frames(source_range["start_time"], rate, "source_range.start_time")
    duration = _rational_frames(source_range["duration"], rate, "source_range.duration")

    features = record.get("features") or {}
    audio = features.get("audio") or {}
    scores = record.get("scores") or {}

    def score_value(name: str) -> float | None:
        entry = scores.get(name)
        return None if entry is None else float(entry["value"])

    snaps = tuple(
        SnapPoint(
            time=_rational_frames(snap["time"], rate, "snap_point.time"),
            kind=str(snap["kind"]),
            strength=float(snap["strength"]),
            confidence=(
                None if snap.get("confidence") is None else float(snap["confidence"])
            ),
            cut_direction=str(snap.get("cut_direction", "both")),
        )
        for snap in record.get("snap_points") or ()
    )

    trim_raw = record.get("safe_trim")
    trim = None
    if trim_raw is not None:
        def optional(name: str) -> float | None:
            entry = trim_raw.get(name)
            return None if entry is None else _rational_frames(entry, rate, name)

        trim = SafeTrim(
            earliest_in=_rational_frames(trim_raw["earliest_in"], rate, "earliest_in"),
            latest_out=_rational_frames(trim_raw["latest_out"], rate, "latest_out"),
            speech_safe_in=optional("speech_safe_in"),
            speech_safe_out=optional("speech_safe_out"),
            min_duration=optional("min_duration"),
            preserve_audio_tail=bool(trim_raw.get("preserve_audio_tail", False)),
        )

    transcript = record.get("transcript") or {}
    words = tuple(
        Word(
            start=_rational_frames(word["start"], rate, "word.start"),
            end=_rational_frames(word["end"], rate, "word.end"),
            text=str(word.get("word", "")),
        )
        for word in transcript.get("words") or ()
    )

    return SelectedMoment(
        moment_id=str(record["moment_id"]),
        media_id=str(record["media_id"]),
        source_start=start,
        source_duration=duration,
        score=float(scores["moment_score"]["value"]),
        snap_points=snaps,
        safe_trim=trim,
        words=words,
        hook_potential=score_value("hook_potential"),
        emotional_peak=score_value("emotional_peak"),
        motion_energy=(
            None
            if features.get("motion_energy") is None
            else float(features["motion_energy"])
        ),
        speech_ratio=(
            None if audio.get("speech_ratio") is None else float(audio["speech_ratio"])
        ),
        noise_ratio=(
            None if audio.get("noise_ratio") is None else float(audio["noise_ratio"])
        ),
        label=str(record.get("shot_id") or ""),
    )


# --------------------------------------------------------------------------
# The cut grid
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class _GridPoint:
    """One position a cut may land on: a beat, or a synthetic tick without music."""

    frame: int
    time: float
    beat_index: int | None
    is_downbeat: bool
    section: str | None


def _cut_grid(request: ReelRequest) -> tuple[_GridPoint, ...]:
    if request.beat_grid is not None:
        return tuple(
            _GridPoint(
                frame=_round_half_up(beat.time),
                time=beat.time,
                beat_index=beat.index,
                is_downbeat=beat.is_downbeat,
                section=beat.section,
            )
            for beat in request.beat_grid.beats
        )

    # No music: a synthetic grid at beats_per_cut ticks per nominal shot, so the
    # "shorten to the previous grid point" behaviour is identical with and
    # without a beat grid. Ticks carry no beat_index, so no clip claims to be
    # beat-locked when there is nothing to lock to.
    span = _limit_frames(request)
    step = request.unlocked_clip_frames / request.beats_per_cut
    count = max(2, int(math.ceil(span / step)) + 1)
    return tuple(
        _GridPoint(
            frame=_round_half_up(index * step),
            time=index * step,
            beat_index=None,
            is_downbeat=False,
            section=None,
        )
        for index in range(count)
    )


def _limit_frames(request: ReelRequest) -> float:
    """Longest timeline the request permits, in frames."""
    target = request.target
    if target.max_duration is not None:
        return float(target.max_duration)
    if target.target_duration is not None:
        # Without a platform ceiling, allow overshooting the ask by one nominal
        # shot so the nearest grid point above the target stays reachable.
        return float(target.target_duration) + request.unlocked_clip_frames
    return float(request.unlocked_clip_frames) * len(request.moments)


def _end_index(grid: Sequence[_GridPoint], request: ReelRequest) -> int:
    """The grid point the reel ends on.

    The target duration is what the planner was ASKED for; the realised
    duration is where the music lets it stop. Nearest grid point wins, ties go
    to the shorter cut (a reel that runs over its ask is more annoying than one
    that comes in under), and `max_duration` is a ceiling, never a target.
    """
    max_frames = (
        None
        if request.target.max_duration is None
        else _round_half_up(request.target.max_duration)
    )
    allowed = [
        index
        for index in range(1, len(grid))
        if max_frames is None or grid[index].frame <= max_frames
    ]
    if not allowed:
        raise ValueError(
            "max_duration is shorter than the first available cut point; no "
            "reel of this length can be beat-locked to this grid"
        )
    if request.target.target_duration is None:
        return allowed[-1]
    target_frames = _round_half_up(request.target.target_duration)
    # The last two key components cannot change today's answer -- `allowed` is
    # already in ascending frame order, so min() picks the shorter reel anyway.
    # They are stated because CLAUDE.md requires every tie to be broken
    # explicitly, and because the day `allowed` is built differently, an
    # implicit dependence on iteration order would flip the chosen duration
    # without failing anything. (Mutating them away survives the suite; that is
    # an equivalent mutant, not an untested branch.)
    return min(
        allowed,
        key=lambda index: (
            abs(grid[index].frame - target_frames),
            grid[index].frame,  # tie -> the shorter reel
            index,
        ),
    )


# --------------------------------------------------------------------------
# Story arc assignment
# --------------------------------------------------------------------------

_ACT_HOOK = "hook"
_ACT_BUILD = "build"
_ACT_PEAK = "peak"
_ACT_BUTTON = "button"

# act key -> (act_id, name, beat_id, beat is REQUIRED, target energy).
# Hook and peak are required: a reel with no opening shot and no destination is
# not a reel, and an unsatisfied required beat is how "the film has an arc"
# gets checked rather than hoped for. The button is optional because a two-shot
# reel ending on its peak is a legitimate cut, not a broken one -- marking it
# required would fail every short reel for lacking a shot it has no room for.
_ACT_SPECS = {
    _ACT_HOOK: ("act-hook", "Hook", "beat-hook-open", True, 0.9),
    _ACT_BUILD: ("act-build", "Build", "beat-build-place", False, 0.55),
    _ACT_PEAK: ("act-peak", "Peak", "beat-peak", True, 1.0),
    _ACT_BUTTON: ("act-button", "Button", "beat-button-close", False, 0.35),
}

_ACT_INTENT = {
    _ACT_HOOK: "Open on the strongest available frame so the first second earns the rest.",
    _ACT_BUILD: "Establish where we are, in the order it happened.",
    _ACT_PEAK: "The moment the reel is about.",
    _ACT_BUTTON: "Land somewhere calm so the loop does not feel abrupt.",
}

_BEAT_DESCRIPTION = {
    _ACT_HOOK: "Immediate movement or an immediate face; no establishing shot.",
    _ACT_BUILD: "Where we are and how we got there.",
    _ACT_PEAK: "The single moment that could not be inferred from any other shot.",
    _ACT_BUTTON: "A shot to rest on, so the loop does not feel abrupt.",
}


def _or_zero(value: float | None) -> float:
    return 0.0 if value is None else float(value)


def _assign_acts(
    request: ReelRequest,
) -> tuple[tuple[str, SelectedMoment], ...]:
    """Order the moments and say which act each one serves.

    THE PEAK IS CHOSEN FIRST, and the hook from what is left. The arc is built
    backwards from the moment that could not be inferred from any other shot;
    spending it on the hook because it also scored well for openings leaves the
    reel with no destination. Both positions are chosen for effect; everything
    between them runs in capture order, because a build that jumps around in
    time reads as a highlight reel of a highlight reel. Ties break on
    moment_id, so the same pool always produces the same reel.
    """
    moments = sorted(request.moments, key=lambda m: m.moment_id)
    if request.arc_template == "chronological":
        return tuple((_ACT_BUILD, m) for m in _chronological(moments))

    remaining = list(moments)
    peak = max(
        remaining,
        key=lambda m: (_or_zero(m.emotional_peak), m.score, _negated_id(m)),
    )
    remaining.remove(peak)

    hook = None
    if remaining:
        hook = max(
            remaining,
            key=lambda m: (_or_zero(m.hook_potential), m.score, _negated_id(m)),
        )
        remaining.remove(hook)

    button = None
    if remaining:
        # A button is a shot you can rest on: mostly the calmest thing left,
        # with score as the tie-break so "calm and bad" never wins.
        #
        # An UNMEASURED motion_energy earns no calmness credit at all (calm =
        # 0), rather than being read as zero motion. Treating "not measured" as
        # "perfectly still" would hand the closing shot to whichever moment the
        # motion pass had not reached yet -- the same mistake the ranking
        # engine's fusion notes call out for missing signals.
        def calm(m: SelectedMoment) -> float:
            measured = 1.0 if m.motion_energy is None else float(m.motion_energy)
            return _round_unit(0.5 * (1.0 - measured) + 0.5 * m.score)

        # `_negated_id` makes max() prefer the LOWEST id on a tie. Redundant
        # while `remaining` is already id-sorted (dropping it survives the
        # suite), kept so the rule does not live in an upstream sort call.
        button = max(remaining, key=lambda m: (calm(m), _negated_id(m)))
        remaining.remove(button)

    ordered: list[tuple[str, SelectedMoment]] = []
    if hook is not None:
        ordered.append((_ACT_HOOK, hook))
    ordered.extend((_ACT_BUILD, m) for m in _chronological(remaining))
    ordered.append((_ACT_PEAK, peak))
    if button is not None:
        ordered.append((_ACT_BUTTON, button))
    return tuple(ordered)


def _negated_id(moment: SelectedMoment) -> tuple[int, ...]:
    """Sort key making `max()` prefer the LOWEST moment_id on a tie."""
    return tuple(-b for b in moment.moment_id.encode("ascii"))


def _chronological(moments: Iterable[SelectedMoment]) -> list[SelectedMoment]:
    # media_id first: two moments from different files have no comparable
    # source time at all, so ordering them by source offset would be noise
    # dressed as chronology.
    return sorted(moments, key=lambda m: (m.media_id, m.source_start, m.moment_id))


def _fit_to_capacity(
    ordered: Sequence[tuple[str, SelectedMoment]],
    start_index: int,
    end_index: int,
    request: ReelRequest,
    notes: list[str],
) -> tuple[tuple[str, SelectedMoment], ...]:
    """Drop build moments until the shots that must fit, can.

    A 15-second reel holds three or four shots. Handing the walk ten moments
    and letting it run out of timeline would drop whatever came last -- which
    is the peak and the button, the two things the reel exists for. So the
    build is what gets cut, lowest score first.
    """
    capacity = max(1, (end_index - start_index) // request.beats_per_cut)
    kept = list(ordered)
    if len(kept) <= capacity:
        return tuple(kept)
    droppable = [
        (act, moment) for act, moment in kept if act == _ACT_BUILD
    ]
    droppable.sort(key=lambda pair: (pair[1].score, pair[1].moment_id))
    for entry in droppable:
        if len(kept) <= capacity:
            break
        kept.remove(entry)
        notes.append(
            f"moment {entry[1].moment_id} dropped: the timeline holds "
            f"{capacity} shots and the build was the longest queue"
        )
    if len(kept) > capacity:
        notes.append(
            f"{len(kept)} required shots exceed the {capacity} the target "
            "duration affords; the reel will run to the last one that fits"
        )
    return tuple(kept)


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
    source_start: float
    grid_index: int
    snap_kind: str | None

    @property
    def timeline_end(self) -> int:
        return self.timeline_start + self.duration

    @property
    def source_end(self) -> float:
        return self.source_start + self.duration


def _min_frames(moment: SelectedMoment, request: ReelRequest) -> int:
    floor = request.min_clip_frames
    trim = moment.safe_trim
    if trim is not None and trim.min_duration is not None:
        # The analysis layer's claim about THIS window beats our global floor.
        floor = max(floor, int(math.ceil(trim.min_duration)))
    return max(1, floor)


def _choose_source_in(
    moment: SelectedMoment, duration: int
) -> tuple[float, str | None]:
    """Pick the source in-point: the strongest certified snap point that fits.

    "Cutting on a weak onset is worse than cutting 40ms later on a strong one"
    (moment-record.schema.json#SnapPoint), so strength leads. Time ascending
    then kind breaks ties, giving the earliest of two equally strong onsets --
    which leaves the most footage for the tail.
    """
    low, high = moment.window()
    latest_in = high - duration
    candidates = [
        snap
        for snap in moment.snap_points
        if snap.cut_direction in _SNAP_IN_DIRECTIONS and low <= snap.time <= latest_in
    ]
    if not candidates:
        return low, None
    best = min(candidates, key=lambda s: (-s.strength, s.time, s.kind))
    return best.time, best.kind


def _place(
    ordered: Sequence[tuple[str, SelectedMoment]],
    grid: Sequence[_GridPoint],
    start_index: int,
    end_index: int,
    request: ReelRequest,
    notes: list[str],
) -> tuple[_Placement, ...]:
    """Walk the grid, hanging one moment on each slot.

    The nominal slot is `beats_per_cut` grid points long. When the moment is
    too short for that, the slot shrinks to the last grid point it can fill --
    still a beat, so the NEXT cut stays locked too. Shrinking rather than
    stretching is deliberate: padding a shot past its safe window means holding
    frames the analysis layer said were unusable.
    """
    placements: list[_Placement] = []
    index = start_index
    for act, moment in ordered:
        if index >= end_index:
            notes.append(
                f"moment {moment.moment_id} unused: the reel reached its "
                "target duration first"
            )
            continue
        low, high = moment.window()
        available = high - low
        minimum = _min_frames(moment, request)
        nominal = min(index + request.beats_per_cut, end_index)
        chosen = None
        for candidate in range(nominal, index, -1):
            duration = grid[candidate].frame - grid[index].frame
            if duration <= available and duration >= minimum:
                chosen = candidate
                break
        if chosen is None:
            notes.append(
                f"moment {moment.moment_id} skipped: its {available:g}-frame "
                f"trimmable window fits no cut point between "
                f"{minimum} frames and the next beat"
            )
            continue
        duration = grid[chosen].frame - grid[index].frame
        source_start, snap_kind = _choose_source_in(moment, duration)
        placements.append(
            _Placement(
                act=act,
                moment=moment,
                clip_id=f"clip-{len(placements) + 1:02d}",
                # Absolute, NOT relative to the first grid point. Shifting the
                # timeline so the first cut lands on frame 0 would desync it
                # from the beat grid, which the contract says is in TIMELINE
                # time: the clips would move and the beats would not, and every
                # beat_lock would still read zero error. A grid that starts
                # late gets an explicit leading Gap instead.
                timeline_start=grid[index].frame,
                duration=duration,
                source_start=source_start,
                grid_index=index,
                snap_kind=snap_kind,
            )
        )
        index = chosen
    return tuple(placements)


# --------------------------------------------------------------------------
# Reframe
# --------------------------------------------------------------------------


def _crop_size(source: tuple[int, int], target: tuple[int, int]) -> tuple[float, float]:
    """Normalised crop w,h that turns `source` aspect into `target` aspect.

    Computed as an exact Fraction and only then converted, so 9:16 out of 16:9
    is 81/256 = 0.31640625 exactly rather than a float that fails its own
    aspect check by 1e-17.
    """
    source_ratio = Fraction(source[0], source[1])
    target_ratio = Fraction(target[0], target[1])
    if target_ratio == source_ratio:
        return 1.0, 1.0
    if target_ratio < source_ratio:  # narrower target: keep full height
        return float(target_ratio / source_ratio), 1.0
    return 1.0, float(source_ratio / target_ratio)


def _clamp(value: float, low: float, high: float) -> float:
    return low if value < low else (high if value > high else value)


def _reframe_track(
    placement: _Placement, request: ReelRequest, width: float, height: float
) -> dict[str, Any] | None:
    """One crop keyframe track for one clip, in SOURCE time.

    Keyframes live in source time so the track survives a later trim or
    retime of the clip that uses it (edl.schema.json#ReframeKeyframe).
    """
    settings = request.reframe
    span_start = placement.source_start
    span_end = placement.source_end
    samples = sorted(
        (
            sample
            for sample in placement.moment.subject_track
            if span_start <= sample.time <= span_end
        ),
        key=lambda s: s.time,
    )

    max_x = 1.0 - width
    max_y = 1.0 - height

    def box(sample: SubjectSample | None) -> tuple[float, float, float | None]:
        if sample is None:
            return _clamp(0.5 - width / 2, 0.0, max_x), _clamp(
                0.5 - height / 2, 0.0, max_y
            ), None
        return (
            _clamp(sample.center_x - width / 2, 0.0, max_x),
            _clamp(sample.center_y - height / 2, 0.0, max_y),
            sample.confidence,
        )

    if not samples and placement.moment.subject_track:
        # The tracker followed this subject, but not over the frames we kept.
        # Holding the nearest known position beats falling back to a centre
        # crop, which is how a subject standing at the edge of frame gets
        # cropped out of a shot that was tracked perfectly well.
        nearest = min(
            placement.moment.subject_track,
            key=lambda s: (
                min(abs(s.time - span_start), abs(s.time - span_end)),
                s.time,
            ),
        )
        samples = [SubjectSample(span_start, nearest.center_x, nearest.center_y, nearest.confidence)]

    points: list[tuple[float, float, float, float | None]] = []
    if not samples:
        x, y, confidence = box(None)
        points.append((span_start, x, y, confidence))
    else:
        if samples[0].time > span_start:
            # Hold the first known position back to the in-point rather than
            # letting the renderer guess what happens before the first sample.
            x, y, confidence = box(samples[0])
            points.append((span_start, x, y, confidence))
        for sample in samples:
            x, y, confidence = box(sample)
            points.append((sample.time, x, y, confidence))

    # Velocity clamp, applied to the plan rather than declared and left to the
    # renderer. A subject that jumps across frame between two samples would
    # otherwise become a whip pan; here it becomes a considered one, and the
    # crop simply lags the subject for a moment.
    limit_per_frame = settings.max_velocity_per_second / request.rate
    clamped: list[tuple[float, float, float, float | None]] = []
    for i, (time, x, y, confidence) in enumerate(points):
        if i:
            previous = clamped[-1]
            budget = max(0.0, (time - previous[0]) * limit_per_frame)
            x = _clamp(x, previous[1] - budget, previous[1] + budget)
            y = _clamp(y, previous[2] - budget, previous[2] + budget)
        clamped.append((time, x, y, confidence))

    keyframes = []
    for i, (time, x, y, confidence) in enumerate(clamped):
        keyframes.append(
            {
                "time": _rational(time, request.rate),
                "crop": {
                    "x": _round_unit(x),
                    "y": _round_unit(y),
                    "w": width,
                    "h": height,
                    "rotation_deg": 0,
                },
                # The last keyframe holds: there is nothing after it to
                # interpolate towards, and `smooth` on a final keyframe is how
                # a crop drifts off the subject at the end of a shot.
                "interpolation": "hold" if i == len(clamped) - 1 else "smooth",
                "bezier_control": None,
                "confidence": None if confidence is None else _round_unit(confidence),
            }
        )

    subject_lock = None
    if samples:
        subject_lock = {
            "source": settings.subject_source,
            "subject_ref": None,
            "person_id": None,
            "keep_in_frame": settings.keep_in_frame,
            "headroom": settings.headroom,
        }

    return {
        "reframe_track_id": f"reframe-{placement.clip_id}",
        "target_aspect_ratio": {
            "numerator": request.target.aspect_ratio[0],
            "denominator": request.target.aspect_ratio[1],
        },
        "keyframes": keyframes,
        "subject_lock": subject_lock,
        "smoothing": {
            "method": settings.smoothing_method,
            "window_frames": settings.smoothing_window_frames,
            "max_velocity_per_second": settings.max_velocity_per_second,
            "deadzone": settings.deadzone,
        },
        # Never implicit: a reframe that silently falls back to a centre crop
        # can decapitate the subject of the shot.
        "fallback": settings.fallback,
    }


# --------------------------------------------------------------------------
# EDL assembly
# --------------------------------------------------------------------------


def _rational(value: float, rate: float) -> dict[str, Any]:
    number = value
    if isinstance(value, float) and value.is_integer():
        number = int(value)
    return {"value": number, "rate": rate}


def _range(start: float, duration: float, rate: float) -> dict[str, Any]:
    return {
        "start_time": _rational(start, rate),
        "duration": _rational(duration, rate),
    }


def _alignment_error_ms(timeline_frame: int, beat_time: float, rate: float) -> float:
    """Signed ms from the beat to the cut. Negative = the cut is early."""
    return _round_ms((timeline_frame - beat_time) / rate * 1000.0)


def _speech_clip(placement: _Placement, request: ReelRequest) -> bool:
    ratio = placement.moment.speech_ratio
    if ratio is not None and ratio >= request.ambient.speech_ratio_threshold:
        return True
    return bool(placement.moment.words)


def _windy_clip(placement: _Placement, request: ReelRequest) -> bool:
    ratio = placement.moment.noise_ratio
    return ratio is not None and ratio >= request.ambient.wind_noise_ratio


def _merge_ranges(ranges: Sequence[tuple[int, int]]) -> list[tuple[int, int]]:
    """Merge touching or overlapping [start, end) pairs. Half-open, so
    adjacent clips merge into one rule instead of two that abut.

    The sort is redundant while the only caller passes clips in timeline order
    (removing it survives the suite -- an equivalent mutant), and it stays
    because the merge is silently wrong on unordered input rather than loud.
    """
    merged: list[tuple[int, int]] = []
    for start, end in sorted(ranges):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def plan_reel(request: ReelRequest) -> ReelPlan:
    """Plan one reel. Pure function of `request`; see the module docstring."""
    notes: list[str] = []
    rate = request.rate
    grid = _cut_grid(request)
    end_index = _end_index(grid, request)

    ordered = _assign_acts(request)
    # The pool BEFORE the capacity cut is what `candidate_moment_ids` is for:
    # "retained so a revision can swap in an alternative without re-running
    # retrieval" (edl.schema.json#StoryBeat). Handing `_story_arc` the
    # post-drop list instead would make the candidate pool equal the placed
    # clips exactly, carrying no information at all -- and the notes that do
    # record the drops live on ReelPlan, not in the EDL, so anything reading
    # the persisted plan would have lost them.
    considered = ordered
    ordered = _fit_to_capacity(ordered, 0, end_index, request, notes)
    placements = _place(ordered, grid, 0, end_index, request, notes)
    if not placements:
        raise ValueError(
            "no moment could be placed: every candidate was shorter than the "
            "shortest cut this beat grid offers"
        )

    media_by_id = {medium.media_id: medium for medium in request.media}
    total_frames = placements[-1].timeline_end

    if request.target.target_duration is not None:
        asked = _round_half_up(request.target.target_duration)
        if total_frames != asked:
            # Never silent: a 15-second ask that realises 7.5 seconds is a
            # result the caller has to be able to see and act on, and the two
            # causes (ended on a beat vs ran out of footage) need different
            # responses.
            notes.append(
                f"realised {total_frames} frames against a {asked}-frame "
                f"target ({total_frames / rate:.3f}s vs {asked / rate:.3f}s): "
                f"{len(placements)} of {len(request.moments)} moments placed, "
                "and the reel ends on a cut point rather than mid-beat"
            )

    # --- video track ------------------------------------------------------
    reframe_tracks: list[dict[str, Any]] = []
    items: list[dict[str, Any]] = []

    if placements[0].timeline_start > 0:
        # A beat grid whose first beat is not at zero means the music starts
        # before the picture does. Modelled as an explicit Gap, exactly as OTIO
        # does, so the hold is a stated intention rather than an accident of
        # arithmetic -- and so the cut positions keep matching the beat times.
        items.append(
            {
                "item_type": "gap",
                "gap_id": "lead-in",
                "duration": _rational(placements[0].timeline_start, rate),
                "fill": "black",
            }
        )
        notes.append(
            f"the reel opens on {placements[0].timeline_start} frames of black: "
            "the beat grid's first beat is not at timeline zero"
        )

    dissolve_index = _dissolve_before(placements, request, media_by_id, notes)

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

        beat_lock = None
        grid_point = grid[placement.grid_index]
        if grid_point.beat_index is not None:
            beat_lock = {
                "beat_index": grid_point.beat_index,
                "is_downbeat": grid_point.is_downbeat,
                "alignment_error_ms": _alignment_error_ms(
                    placement.timeline_start, grid_point.time, rate
                ),
                "snap_point_kind": placement.snap_kind,
            }

        markers = []
        if placement.act == _ACT_PEAK:
            markers.append(
                {
                    "name": "emotional peak",
                    "marked_range": _range(
                        placement.timeline_start, placement.duration, rate
                    ),
                    "color": "MAGENTA",
                    "kind": "emotional_peak",
                    "comment": "the moment the reel is built around",
                }
            )

        audio_tail = None
        trim = moment.safe_trim
        if trim is not None and trim.preserve_audio_tail:
            # L-cut: hold the audio past the picture so a laugh finishes over
            # the next shot. Bounded by latest_out, not by the speech-safe out:
            # the tail is exactly the audio the narrower window excluded, and
            # clamping it to that window would extend the audio by nothing.
            #
            # Bounded by the MEDIA as well. `latest_out` is a claim about the
            # moment; whether the file has those frames is a different question,
            # and a moment whose window runs to the end of a truncated recording
            # would otherwise have the renderer decode past EOF -- silence or a
            # decode error at exactly the frame the plan says a laugh lands.
            reachable = min(trim.latest_out, medium.available_end)
            spare = reachable - placement.source_end
            if spare > 0 and position < len(placements) - 1:
                audio_tail = _rational(min(spare, placement.duration), rate)

        if position == dissolve_index:
            items.append(
                {
                    "item_type": "transition",
                    # Named for the clip it dissolves into, not for the act it
                    # is assumed to serve. `_dissolve_before` puts the dissolve
                    # in front of the LAST placement, which is the button only
                    # when a button exists: with two moments the acts are hook
                    # and peak, and a transition labelled "into-button" in a
                    # plan whose whole purpose is to state decisions honestly
                    # is a mislabelled decision.
                    "transition_id": f"xfade-into-{placement.clip_id}",
                    "transition_type": "dissolve",
                    "in_offset": _rational(request.dissolve_frames, rate),
                    "out_offset": _rational(request.dissolve_frames, rate),
                    "easing": "ease_in_out",
                    "parameters": {},
                }
            )

        items.append(
            {
                "item_type": "clip",
                "clip_id": placement.clip_id,
                "name": moment.label,
                "media_ref_id": medium.media_ref_id,
                "source_range": _range(placement.source_start, placement.duration, rate),
                "timeline_range": _range(
                    placement.timeline_start, placement.duration, rate
                ),
                "moment_id": moment.moment_id,
                "enabled": True,
                "time_effect": None,
                "reframe_track_id": None if track is None else track["reframe_track_id"],
                "color_ops": [],
                "audio": {
                    "gain_db": _clip_gain_db(placement, request),
                    "muted": not request.ambient.enabled,
                    "fade_in": None,
                    "fade_out": None,
                    "audio_extends_past_out": audio_tail,
                },
                "beat_lock": beat_lock,
                "story_beat_id": _ACT_SPECS[placement.act][2]
                if request.arc_template == "hook_build_peak_button"
                else None,
                "markers": markers,
            }
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
                f"source {medium.media_ref_id} declared but unused; it is not "
                "in the EDL, so the renderer will not open it"
            )

    # --- audio ------------------------------------------------------------
    audio_plan, music_track = _audio_plan(request, placements, total_frames, notes)
    if music_track is not None:
        tracks.append(music_track)

    beat_grid_block = None
    if request.beat_grid is not None and request.music is not None:
        beat_grid_block = _beat_grid_block(request)

    if len(placements) == 1 and request.arc_template == "hook_build_peak_button":
        notes.append(
            f"single-shot reel: {placements[0].clip_id} is both the hook and "
            "the peak, and satisfies both required story beats"
        )

    story_arc = _story_arc(request, placements, considered, total_frames)

    edl: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "edl_id": "0" * 64,  # replaced below, once the rest is final
        "name": request.name,
        "kind": "reel",
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
        "beat_grid": beat_grid_block,
        "story_arc": story_arc,
        "color_pipeline": {
            "input_transform": "auto",
            "working_space": "rec709",
            "output_transform": "rec709",
            # Left implicit, an HLG or PQ source renders washed out. Enabling it
            # unconditionally is safe: an SDR source is already inside the
            # target volume, so the transform is identity.
            "tone_map_hdr_to_sdr": True,
        },
        "variant": _variant_block(request),
        "determinism": {
            "planner": PLANNER_ID,
            "planner_version": request.planner_version,
            "seed": request.seed,
            "inputs_digest": blake3_hex(canonical_json(_request_digest_payload(request))),
            "generated_at": request.generated_at,
        },
        "validation": None,
        "otio": {
            "otio_schema_version": "OTIO_SCHEMA:Timeline.1",
            "metadata_namespace": "memory_engine",
            "unmapped_fields": [],
            # Only the exporter may set this, and only after re-importing its
            # own output. A planner claiming it would be claiming a test it
            # never ran.
            "round_trip_verified": False,
        },
    }

    edl["validation"] = _validate(edl, request, placements, total_frames)
    # Last, over everything else: the id is a digest of the finished plan, so
    # anything written after it would not be covered by it.
    edl["edl_id"] = blake3_hex(canonical_json(_digest_view(edl)))

    return ReelPlan(edl=edl, notes=tuple(notes), duration_frames=total_frames)


def _clip_gain_db(placement: _Placement, request: ReelRequest) -> float:
    ambient = request.ambient
    if _windy_clip(placement, request):
        return ambient.wind_gain_db
    if ambient.preserve_speech and _speech_clip(placement, request):
        return ambient.speech_gain_db
    return ambient.default_gain_db


def _media_ref(medium: SourceMedia, rate: float) -> dict[str, Any]:
    return {
        "media_ref_id": medium.media_ref_id,
        # For chaptered footage this is the span ASSEMBLY id. A moment that
        # straddles a GH01/GH02 boundary is one clip against one media_id here;
        # expanding it into member reads is the renderer's job, and the planner
        # never learns the boundary exists.
        "media_id": medium.media_id,
        "media_kind": medium.media_kind,
        "available_range": _range(
            medium.available_start, medium.available_duration, rate
        ),
        "is_span_assembly": medium.is_span_assembly,
        "expected_frame_rate": medium.expected_frame_rate,
        "label": medium.label,
    }


def _dissolve_before(
    placements: Sequence[_Placement],
    request: ReelRequest,
    media_by_id: Mapping[str, SourceMedia],
    notes: list[str],
) -> int | None:
    """Index of the clip a dissolve precedes, or None for all hard cuts.

    A hard cut is the ABSENCE of a Transition (OTIO's rule, restated twice in
    the EDL contract). So this returns None unless a dissolve was asked for AND
    both neighbours have real handle frames -- a transition without handles
    would have the renderer blending frames that do not exist.
    """
    if request.dissolve_frames <= 0 or len(placements) < 2:
        return None
    index = len(placements) - 1
    outgoing = placements[index - 1]
    incoming = placements[index]
    handles = request.dissolve_frames
    outgoing_media = media_by_id[outgoing.moment.media_id]
    incoming_media = media_by_id[incoming.moment.media_id]
    # Tail on the outgoing side, head on the incoming side -- the same charge
    # `transition_handles_available` makes, and the reason the two offsets are
    # emitted equal below: an asymmetric dissolve would need this to split into
    # out_offset against the tail and in_offset against the head.
    if (
        outgoing.source_end + handles <= outgoing_media.available_end
        and incoming.source_start - handles >= incoming_media.available_start
    ):
        return index
    notes.append(
        f"dissolve into {incoming.clip_id} downgraded to a hard cut: the "
        f"sources do not carry {handles} frames of handle on both sides"
    )
    return None


def _music_items(
    request: ReelRequest, total_frames: int, pass_frames: int
) -> list[dict[str, Any]]:
    """The music cue as timeline items: one clip per pass over the track.

    A track shorter than the reel is the ordinary case -- a 15-second cut over
    a 3-second sting -- and `MusicCue.loop` is how the plan says so. The loop
    still has to be REALISED on the track, though: one clip whose source_range
    spans the whole reel would read past the end of the file, and a renderer
    that "just knows" to wrap around would be making the decision instead of
    executing it (AGENTS.md rule 3). So each pass is its own clip over the
    material that exists, the last one cut short, and the timeline tiles
    exactly once with no frame of silence.

    The first clip keeps the unsuffixed id, so the ordinary single-pass cue is
    emitted exactly as it was before looping was expressible.
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
                    # A fade belongs to the cue, not to every pass of it: the
                    # music fades in once at the top and out once at the end.
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


def _audio_plan(
    request: ReelRequest,
    placements: Sequence[_Placement],
    total_frames: int,
    notes: list[str],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    rate = request.rate
    ambient = request.ambient
    # With ambient off every clip is emitted muted, so a per-clip ambient gain
    # is a level for a signal that is not in the mix. Emitting it anyway would
    # have a renderer -- or a human reading the plan -- believe the location
    # sound is coming up 6 dB under the music when it is not there at all.
    per_clip = [
        {"clip_id": placement.clip_id, "gain_db": _clip_gain_db(placement, request)}
        for placement in placements
        if ambient.enabled
        and _clip_gain_db(placement, request) != ambient.default_gain_db
    ]

    music_cues: list[dict[str, Any]] = []
    music_track_block: dict[str, Any] | None = None
    ducking: list[dict[str, Any]] = []

    if request.music is not None:
        music = request.music
        available = music.media.available_duration - (
            music.source_start - music.media.available_start
        )
        loop = available < total_frames
        if loop:
            notes.append(
                f"music cue loops: {available:g} frames of track under a "
                f"{total_frames}-frame reel"
            )
        # The cue reads what the track HAS, never the length of the reel. A
        # source_range of `total_frames` over a shorter track claims frames the
        # file does not contain, which `source_range_within_available` then
        # fails at severity error -- the planner deliberately planning a loop
        # and then declaring its own plan unrenderable, with an error message
        # about source ranges rather than about looping.
        #
        # Whole frames only, and the SAME number the track items below read: a
        # fractional last frame is a frame the decoder does not have, and a cue
        # that declared 200.5 while its clips read 200 would put the two halves
        # of one decision half a frame apart.
        pass_frames = max(1, int(math.floor(available)))
        cue_frames = min(pass_frames, total_frames)
        cue = {
            "cue_id": music.cue_id,
            "media_ref_id": music.media.media_ref_id,
            "source_range": _range(music.source_start, cue_frames, rate),
            "timeline_range": _range(0, total_frames, rate),
            "license": {
                "provider": music.license.provider,
                "license_id": music.license.license_id,
                "track_title": music.license.track_title,
                "attribution_required": music.license.attribution_required,
                "attribution_text": music.license.attribution_text,
                "license_type": music.license.license_type,
                "cleared_for": list(music.license.cleared_for),
            },
            "gain_db": music.gain_db,
            "fade_in": None if music.fade_in is None else _rational(music.fade_in, rate),
            "fade_out": (
                None if music.fade_out is None else _rational(music.fade_out, rate)
            ),
            "loop": loop,
        }
        music_cues.append(cue)
        repeats = _music_items(request, total_frames, pass_frames)
        if len(repeats) > 1:
            notes.append(
                f"the music track is laid down {len(repeats)} times to cover "
                f"the reel; the last pass is cut short at "
                f"{repeats[-1]['source_range']['duration']['value']} frames"
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
        # `ambient.enabled` as well as `preserve_speech`: with ambient off the
        # clips are muted, so there is no dialogue for the music to make room
        # for. Ducking anyway pulls the track down 9 dB under silence for a
        # quarter of the reel -- audible, wrong, and nothing in the plan says
        # why, because the reason is a rule that should not have been emitted.
        if speech_ranges and ambient.preserve_speech and ambient.enabled:
            # explicit_ranges, not `speech`: the planner already knows where the
            # words are, and a renderer re-detecting them at mix time would make
            # the same EDL mix differently on a different build.
            ducking.append(
                {
                    "rule_id": "duck-music-under-speech",
                    "target": "music",
                    "trigger": "explicit_ranges",
                    "reduction_db": ambient.duck_reduction_db,
                    "threshold_db": None,
                    "ratio": None,
                    "attack_ms": ambient.duck_attack_ms,
                    "release_ms": ambient.duck_release_ms,
                    "ranges": [
                        _range(start, end - start, rate) for start, end in speech_ranges
                    ],
                }
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


def _beat_grid_block(request: ReelRequest) -> dict[str, Any]:
    grid = request.beat_grid
    assert grid is not None and request.music is not None
    return {
        "source_cue_id": request.music.cue_id,
        "bpm": grid.bpm,
        "bpm_confidence": grid.bpm_confidence,
        "time_signature": (
            None
            if grid.time_signature is None
            else {
                "beats_per_bar": grid.time_signature[0],
                "beat_unit": grid.time_signature[1],
            }
        ),
        "beats": [
            {
                "index": beat.index,
                "time": _rational(beat.time, request.rate),
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


def _story_arc(
    request: ReelRequest,
    placements: Sequence[_Placement],
    considered: Sequence[tuple[str, SelectedMoment]],
    total_frames: int,
) -> dict[str, Any]:
    rate = request.rate
    if request.arc_template == "chronological":
        return {
            "arc_id": "arc-chronological",
            "template": "chronological",
            "title": request.title,
            "logline": request.logline,
            "acts": [
                {
                    "act_id": "act-timeline",
                    "name": "Timeline",
                    "intent": "The day in the order it happened.",
                    "timeline_range": _range(0, total_frames, rate),
                    "target_energy": None,
                    "beats": [
                        {
                            "beat_id": "beat-chronology",
                            "description": "Every selected moment, in capture order.",
                            "required": False,
                            "satisfied_by_clip_ids": [p.clip_id for p in placements],
                            "candidate_moment_ids": sorted(
                                m.moment_id for _, m in considered
                            ),
                        }
                    ],
                }
            ],
            "energy_curve": [],
            "source": "template",
            "model": None,
            "prompt_id": None,
            "consent": None,
            "rationale": request.rationale,
        }

    by_act: dict[str, list[_Placement]] = {key: [] for key in _ACT_SPECS}
    for placement in placements:
        by_act[placement.act].append(placement)
    candidates: dict[str, list[str]] = {key: [] for key in _ACT_SPECS}
    for act, moment in considered:
        candidates[act].append(moment.moment_id)

    # A one-shot reel is a legitimate cut, and its single shot IS the opening
    # frame as well as the moment the reel is about: `_assign_acts` calls it
    # the peak, leaves no moment for the hook, and the required hook beat then
    # fails -- a hard rejection of a valid reel on a technicality of the act
    # table. So with exactly one clip that clip satisfies both required beats.
    # Stated here rather than by relaxing `required`, because the required flag
    # is what makes "the film has an arc" checkable at all.
    single = placements[0] if len(placements) == 1 else None

    acts = []
    energy_curve = []
    for act_key in (_ACT_HOOK, _ACT_BUILD, _ACT_PEAK, _ACT_BUTTON):
        act_id, name, beat_id, required, energy = _ACT_SPECS[act_key]
        members = by_act[act_key]
        borrowed = False
        if not members and required and single is not None:
            members = [single]
            borrowed = True
        if not members and not required and not candidates[act_key]:
            # An optional act with nothing in it AND nothing that was
            # considered for it is not part of this arc. A required one IS
            # emitted empty, so the unsatisfied beat fails validation instead
            # of vanishing -- and so is an optional one whose candidates were
            # all dropped by the capacity cut, because "these are the shots we
            # weighed for the build and could not fit" is exactly what the
            # revision path needs and the only place the EDL can carry it.
            continue
        timeline_range = None
        if members:
            start = members[0].timeline_start
            timeline_range = _range(start, members[-1].timeline_end - start, rate)
            if not borrowed:
                # One clip, one energy point. A borrowed member would put two
                # different target energies on the same timeline frame.
                energy_curve.append({"time": _rational(start, rate), "energy": energy})
        acts.append(
            {
                "act_id": act_id,
                "name": name,
                "intent": _ACT_INTENT[act_key],
                "timeline_range": timeline_range,
                "target_energy": energy,
                "beats": [
                    {
                        "beat_id": beat_id,
                        "description": _BEAT_DESCRIPTION[act_key],
                        "required": required,
                        "satisfied_by_clip_ids": [p.clip_id for p in members],
                        "candidate_moment_ids": sorted(candidates[act_key]),
                    }
                ],
            }
        )

    return {
        "arc_id": "arc-hook-build-peak-button",
        "template": "hook_build_peak_button",
        "title": request.title,
        "logline": request.logline,
        "acts": acts,
        "energy_curve": energy_curve,
        "source": "template",
        "model": None,
        "prompt_id": None,
        "consent": None,
        "rationale": request.rationale,
    }


def _variant_block(request: ReelRequest) -> dict[str, Any] | None:
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
# Digests
# --------------------------------------------------------------------------


def _request_digest_payload(request: ReelRequest) -> dict[str, Any]:
    """Every input the planner reads, as canonical-JSON-able data.

    Deliberately explicit rather than `dataclasses.asdict`: a new field must be
    added here consciously, because a field that silently escapes the digest
    makes two different plans claim the same inputs.
    """
    target = request.target
    ambient = request.ambient
    reframe = request.reframe
    return {
        "planner": PLANNER_ID,
        "planner_version": request.planner_version,
        "rate": request.rate,
        "seed": request.seed,
        "arc_template": request.arc_template,
        "beats_per_cut": request.beats_per_cut,
        "unlocked_clip_frames": request.unlocked_clip_frames,
        "min_clip_frames": request.min_clip_frames,
        "dissolve_frames": request.dissolve_frames,
        "name": request.name,
        "title": request.title,
        "logline": request.logline,
        "rationale": request.rationale,
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
            "duck_reduction_db": ambient.duck_reduction_db,
            "duck_attack_ms": ambient.duck_attack_ms,
            "duck_release_ms": ambient.duck_release_ms,
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
        "beat_grid": None
        if request.beat_grid is None
        else {
            "bpm": request.beat_grid.bpm,
            "bpm_confidence": request.beat_grid.bpm_confidence,
            "tolerance_ms": request.beat_grid.tolerance_ms,
            "time_signature": (
                None
                if request.beat_grid.time_signature is None
                else list(request.beat_grid.time_signature)
            ),
            "analyzer": None
            if request.beat_grid.analyzer is None
            else dict(request.beat_grid.analyzer),
            "beats": [
                {
                    "index": b.index,
                    "time": b.time,
                    "is_downbeat": b.is_downbeat,
                    "bar": b.bar,
                    "beat_in_bar": b.beat_in_bar,
                    "strength": b.strength,
                    "section": b.section,
                }
                for b in request.beat_grid.beats
            ],
        },
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


def clearance_for_destination(destination: str) -> str:
    """Which `cleared_for` entry a destination requires."""
    try:
        return _DESTINATION_CLEARANCE[destination]
    except KeyError:  # pragma: no cover - RenderTarget rejects these first
        raise ValueError(f"unknown destination {destination!r}") from None


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


def _validate(
    edl: Mapping[str, Any],
    request: ReelRequest,
    placements: Sequence[_Placement],
    total_frames: int,
) -> dict[str, Any]:
    """Pre-render checks, run against the EDL we just built.

    Deliberately re-derived from the emitted structure rather than from the
    planner's intermediate state: a check that reads the same variable the
    writer read cannot catch the writer being wrong.
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

    # source_range_within_available
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
            # An L-cut reads past the picture's out-point, so the frames it
            # reaches are part of what this clip asks the decoder for. Checking
            # only `source_range` would pass a plan that holds audio past the
            # end of the file, which is the same defect as a clip escaping its
            # source and is invisible in every other check.
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

    # timeline_contiguous
    gaps: list[str] = []
    for track in edl["tracks"]:
        cursor = 0
        for item in track["items"]:
            if item["item_type"] == "transition":
                # Zero-duration in OTIO: a transition consumes no timeline, so
                # the clips either side still butt up. The handles it overlaps
                # come from OUTSIDE the neighbours' source_ranges -- which is
                # what `transition_handles_available` checks them against.
                # (edl.schema.json's OTIO header says the opposite in one
                # sentence: "The clips' source_ranges already include the
                # handles the overlap consumes." Under that reading the handle
                # check would be pure duplication of
                # `source_range_within_available`, and every dissolve would
                # shorten its two shots by `dissolve_frames` of picture. The
                # planner implements the OTIO reading; the contract sentence
                # needs Codex sign-off to correct, so it is flagged rather than
                # edited here -- see the branch report.)
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
        # A transition needs a neighbour on both sides; one at either end of a
        # track has nothing to overlap and is not a transition at all.
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
            # `media_refs_resolvable` has already failed on this; raising a
            # KeyError here would abort the whole validation block and hand the
            # caller a traceback instead of the list of everything that is
            # wrong with the plan.
            handle_failures.append(
                f"{item['transition_id']} sits between clips whose media "
                "cannot be resolved"
            )
            continue
        # Which offset is charged to which neighbour is not symmetric.
        # `in_offset` extends the transition BACKWARDS into the outgoing item
        # and `out_offset` FORWARDS into the incoming one (edl.schema.json),
        # so during the overlap the outgoing clip is still on screen for
        # `out_offset` frames after its own out-point -- it needs that much
        # TAIL -- and the incoming clip is already on screen for `in_offset`
        # frames before its in-point, so it needs that much HEAD. Charging
        # them the other way round is invisible while every dissolve we emit
        # has in_offset == out_offset, and wrong the day one does not.
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
                f"{outgoing['clip_id']} has no tail handle for "
                f"{item['transition_id']}"
            )
        if in_start < in_ref["available_range"]["start_time"]["value"]:
            handle_failures.append(
                f"{incoming['clip_id']} has no head handle for "
                f"{item['transition_id']}"
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

    # beat_alignment_within_tolerance
    locked = [
        item
        for track in edl["tracks"]
        for item in track["items"]
        if item["item_type"] == "clip" and item.get("beat_lock")
    ]
    if locked:
        tolerance = (
            request.beat_grid.tolerance_ms if request.beat_grid is not None else 50.0
        )
        worst = max(locked, key=lambda i: abs(i["beat_lock"]["alignment_error_ms"]))
        worst_error = abs(worst["beat_lock"]["alignment_error_ms"])
        checks.append(
            _check(
                "beat_alignment_within_tolerance",
                worst_error <= tolerance,
                "error",
                f"worst error {worst_error:.4f} ms against a {tolerance:g} ms "
                f"tolerance",
                worst["clip_id"],
            )
        )

    # no_mid_word_cut. Strict inequalities: a cut exactly ON a word boundary is
    # the good case, and `<=` here would condemn every clean speech-gap cut.
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
    words_present = any(placement.moment.words for placement in placements)
    if words_present:
        checks.append(
            _check(
                "no_mid_word_cut",
                not mid_word,
                "warning",
                "every speech clip cuts between words"
                if not mid_word
                else "; ".join(detail for _, detail in mid_word),
                None if not mid_word else mid_word[0][0],
            )
        )

    # reframe_aspect_matches_target / reframe_keyframes_ordered
    reframe_tracks = edl["reframe_tracks"]
    if reframe_tracks:
        aspect_failures: list[str] = []
        order_failures: list[str] = []
        source_aspect_by_ref = {
            m.media_ref_id: m.aspect_ratio for m in request.media
        }
        clip_by_reframe = {
            item["reframe_track_id"]: item
            for track in edl["tracks"]
            for item in track["items"]
            if item["item_type"] == "clip" and item.get("reframe_track_id")
        }
        for reframe in reframe_tracks:
            clip = clip_by_reframe.get(reframe["reframe_track_id"])
            source_aspect = (
                (16, 9)
                if clip is None
                else source_aspect_by_ref.get(clip["media_ref_id"])
            )
            if source_aspect is None:
                # The clip points at a source the request does not declare, so
                # `media_refs_resolvable` has already failed. Guessing an
                # aspect ratio here would either invent an aspect failure or
                # hide a real one, and raising would abort the whole block.
                continue
            wanted = Fraction(
                reframe["target_aspect_ratio"]["numerator"],
                reframe["target_aspect_ratio"]["denominator"],
            )
            previous = -math.inf
            for keyframe in reframe["keyframes"]:
                crop = keyframe["crop"]
                got = (
                    crop["w"] * source_aspect[0] / (crop["h"] * source_aspect[1])
                )
                if abs(got - float(wanted)) > 1e-9 * float(wanted):
                    aspect_failures.append(
                        f"{reframe['reframe_track_id']} crop is {got:.9f}, "
                        f"target is {float(wanted):.9f}"
                    )
                time = keyframe["time"]["value"]
                if time <= previous:
                    order_failures.append(
                        f"{reframe['reframe_track_id']} keyframe at {time} "
                        f"follows {previous}"
                    )
                previous = time
        checks.append(
            _check(
                "reframe_aspect_matches_target",
                not aspect_failures,
                "error",
                f"{len(reframe_tracks)} tracks match the target aspect"
                if not aspect_failures
                else "; ".join(sorted(set(aspect_failures))),
            )
        )
        checks.append(
            _check(
                "reframe_keyframes_ordered",
                not order_failures,
                "error",
                f"{len(reframe_tracks)} tracks, keyframes strictly increasing"
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

    # music_license_covers_destination
    if edl["audio_plan"] and edl["audio_plan"]["music"]:
        needed = clearance_for_destination(request.target.destination)
        uncovered = [
            cue["cue_id"]
            for cue in edl["audio_plan"]["music"]
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


def _audio_tail_frames(item: Mapping[str, Any]) -> float:
    """How far past its picture out-point a clip's audio reads, in frames."""
    tail = (item.get("audio") or {}).get("audio_extends_past_out")
    return 0 if tail is None else tail["value"]


def _clip_count(edl: Mapping[str, Any]) -> int:
    return sum(
        1
        for track in edl["tracks"]
        for item in track["items"]
        if item["item_type"] == "clip"
    )
