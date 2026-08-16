"""Event clustering: the first decision in the album pipeline.

An album is built per event, so every later step -- selection, layout, the
frontier-model taste pass -- inherits whatever this module gets wrong. A photo
put in the wrong event is not recoverable downstream: nothing later ever
reconsiders membership.

WHAT AN EVENT IS

"The Thailand trip", "Diwali at home", "Avika's first birthday". A contiguous
run of capture activity that a person would name as one thing. There is no
ground truth for this, so the goal is not accuracy against a label set; it is
that the failure modes are the survivable ones and that they are visible.

    over-split  ->  the user sees two albums where they wanted one.
    under-split ->  Diwali photos appear inside the Thailand album.

Only the first is recoverable by the person looking at it, and only the first is
even noticeable. So every threshold here is tuned toward splitting, and every
signal that could *merge* two runs is rejected on principle: location, for
instance, is allowed to split a run and is never allowed to join one. Joining on
location would fuse five years of "at home" into a single event, and nothing
downstream would report it.

TIME IS A CLAIM, NOT A FACT

`MediaRecord.capture.captured_at` is a TimeAssertion: a value, a source, a
precision and a confidence. A third of a real library has no EXIF date -- scans,
WhatsApp forwards, screenshots -- and the schema deliberately refuses to
fabricate one. This module takes the precision seriously in two ways:

1.  A claim whose precision is coarser than a day, or whose precision is
    'unknown', or whose confidence is below the floor, is NOT a position on the
    timeline. Such an item is undated. Sorting it to its nominal value would
    anchor a fake event on 1 January of whatever year the assertion names.

2.  For claims that do land on the timeline, the gap between two items is
    reduced by their combined uncertainty before it is compared to a threshold.
    Two WhatsApp photos on consecutive days both snap to local midnight, so
    their nominal gap is exactly 24h; their *conservative* gap is 0, and they
    stay in one event. Three days apart, the conservative gap is 48h and they
    split. That is the correct amount of confidence to have in a day-precision
    date, and it falls out of the schema rather than out of a fudge factor.

TIMEZONES

`captured_at` here is an INSTANT (unix seconds, UTC), never a wall-clock
reading. This is the single most important convention in the module. A trip that
crosses zones has wall-clock readings that jump, and Tokyo -> Los Angeles jumps
*backwards*: sorting by wall clock reorders arrival photos before departure
photos and produces negative gaps, and a negative gap silently fails every
`gap > threshold` test rather than raising. Instants are monotone by
construction, so the whole class of bug is unreachable.

`utc_offset_minutes` is carried but never used for ordering. It is surfaced on
the cluster so a downstream label ("Sunday in Bangkok") can be rendered in the
zone the photo was actually taken in, and so a caller can see that an event
crossed zones.

Known limitation, stated rather than hidden: when a library mixes items whose
zone was resolved with items whose zone was not, gaps across that boundary carry
up to +/-14h of unquantified error. We do not widen the uncertainty for it,
because doing so would make two items' relationship depend on the rest of the
library, which is worse than the error it fixes. In the common case -- a library
where no file has an offset -- ingest applies one consistent interpretation, the
shift cancels in every difference, and gaps are exact.

THE GAP THRESHOLD IS DERIVED, NOT CONSTANT

A wedding weekend and a two-week trip have different rhythms, so a constant gap
is wrong for one of them. See `adaptive_gap_threshold` for the derivation and
for why a knee search beats a percentile or a local median.

DETERMINISM

Same library in, same clusters and same ids out. Everything is sorted before it
is iterated, every tie breaks on media_id, and cluster ids are uuid5 over sorted
membership rather than over anything positional. Sets and dicts are only ever
built, never iterated for anything that reaches the output.
"""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass
from typing import Iterable, Literal, Mapping, Sequence

HOUR = 3600.0
DAY = 24.0 * HOUR

# ---------------------------------------------------------------------------
# Calibration
#
# These are defaults, not truths. The eval harness owns re-deriving them against
# a labelled benchmark library; they are module constants so that a change is a
# reviewable diff rather than a number buried in a call site.
# ---------------------------------------------------------------------------

TimePrecision = Literal["second", "minute", "hour", "day", "month", "year", "unknown"]

# Precisions that put an item on the timeline at all. 'month' and 'year' are
# excluded: a month-precision assertion is a bucket, not a moment, and treating
# it as a moment invents an event on the first of the month.
USABLE_TIME_PRECISIONS: frozenset[str] = frozenset({"second", "minute", "hour", "day"})

# The full TimeAssertion.precision enum. A value outside it is a contract
# violation, and guessing what an unrecognised precision means is how an
# assertion gets believed more than it deserves.
ALL_TIME_PRECISIONS: frozenset[str] = USABLE_TIME_PRECISIONS | frozenset(
    {"month", "year", "unknown"}
)

# Half-width of the interval a claim of each precision actually covers. Used to
# shrink gaps before they are compared to a threshold. The centring convention
# does not matter (midnight vs noon for a day) because it cancels in a
# difference; only the width does.
PRECISION_HALF_WIDTH_S: Mapping[str, float] = {
    "second": 0.5,
    "minute": 30.0,
    "hour": 1800.0,
    "day": 43200.0,
}

# Used when the library is too small or too featureless to derive a threshold.
# 20h clears a long night plus a slow morning (a 14h evening-to-late-breakfast
# gap must not end an event, or every day of a trip becomes its own album) while
# staying under a full day of no photographs at all.
DEFAULT_GAP_S = 20.0 * HOUR

# The derived threshold is clamped into this band. Below the floor we are inside
# one day's rhythm and a gap means lunch; above the ceiling nothing was
# photographed for four days and it is a different event whatever the rhythm.
MIN_GAP_S = 6.0 * HOUR
MAX_GAP_S = 96.0 * HOUR

# Adaptivity is only trusted with enough evidence. Below these counts the "knee"
# in the gap distribution is one photo's worth of noise.
MIN_GAPS_FOR_ADAPTIVE = 12
MIN_IN_BAND_GAPS_FOR_ADAPTIVE = 4

# A knee must be a real separation, not a gentle slope. 2.0 = the between-event
# gaps are at least twice the within-event ones; anything less and the
# distribution is a continuum with no boundary in it, so we decline to invent
# one and fall back to the default.
MIN_KNEE_RATIO = 2.0

# No event runs longer than this. Without it, a library where someone photographs
# something every day has no gap above threshold anywhere and collapses into a
# single five-year "event" -- the worst possible output, produced silently. The
# cut is placed at the quietest interior gap, which is the least-bad place for an
# arbitrary cut to land. 21 days so a two-week trip survives whole.
MAX_EVENT_SPAN_S = 21.0 * DAY

# Distance between temporally adjacent geotagged items that ends an event.
# Generous: a day out crosses tens of km, and GPS on a phone in a pocket is
# noisy. 150km means the place genuinely changed.
LOCATION_SPLIT_KM = 150.0

# Below this, the time assertion is a guess (neighbour interpolation, a filename
# pattern that half-matched) and the item is treated as undated instead.
MIN_TIME_CONFIDENCE = 0.5

# Cosine similarity (SigLIP, L2-normalised, so a dot product) an undated item
# must reach against its best-matching event before it is placed in that event
# rather than in the undated bucket, plus the margin it must beat the
# runner-up event by. Two thresholds, not one: without the margin, a photo that
# matches two events equally well lands in whichever sorted first, which is
# exactly the "silently inserted somewhere adjacent" failure the undated bucket
# exists to prevent.
UNDATED_SIMILARITY_THRESHOLD = 0.75
UNDATED_SIMILARITY_MARGIN = 0.05

# Sentinel for "no score yet", one below the bottom of the cosine range so it
# can never be mistaken for one. It is compared by equality, not by magnitude:
# -1.0 is a legitimate score (an exactly opposed embedding) and a runner-up that
# scores it is a real runner-up, so a `< -1.0` test would quietly reclassify it
# as "no competition" and skip the ambiguity check.
_NO_SCORE = -2.0

# Embeddings are contractually L2-normalised (common.schema.json VectorRef
# .normalized). We verify rather than assume: an un-normalised or double-scaled
# vector still produces a plausible-looking number from a dot product, which is
# precisely the kind of wrong answer that never raises.
EMBEDDING_NORM_TOLERANCE = 1e-3

EARTH_RADIUS_KM = 6371.0088

_ID_NAMESPACE_PREFIX = "memory-engine/album/event/"


# ---------------------------------------------------------------------------
# Inputs and outputs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EventItem:
    """One item entering clustering.

    Deliberately not a MediaRecord. Clustering needs eight fields; taking the
    whole record would make every test construct a hundred-field object, and
    tests that are expensive to write do not get written.

    `captured_at` is an INSTANT in unix seconds (UTC) -- see the module
    docstring. It is NOT a wall-clock reading; passing one here for a library
    that crosses zones will reorder the timeline.

    `time_precision` defaults to 'second' so that a caller who has a real
    instant can pass one and nothing else. A caller who has a coarser claim must
    say so; a caller with no claim passes captured_at=None. There is no
    combination that silently upgrades an unknown time into a position on the
    timeline.
    """

    media_id: str
    captured_at: float | None = None
    time_precision: TimePrecision = "second"
    time_confidence: float = 1.0
    # TimeAssertion.source, carried for reporting only. Never used to decide.
    time_source: str | None = None
    # Offset actually recorded in the file, when present. Carried for labelling;
    # never used for ordering (module docstring).
    utc_offset_minutes: int | None = None
    latitude: float | None = None
    longitude: float | None = None
    embedding: tuple[float, ...] | None = None


@dataclass(frozen=True)
class EventCluster:
    """One event.

    `media_ids` is chronological, because that is the order an album wants.
    `cluster_id` is uuid5 over the *sorted* member ids, because an id must not
    depend on the order anything was iterated in.

    Items that were assigned by embedding rather than by time appear at the end
    of `media_ids` and are listed again in `undated_media_ids`. They have no
    position on the timeline, so interleaving them into it would be a fabricated
    chronology; and a downstream step that needs to lay out a date-ordered
    spread has to be able to tell which members it cannot place.
    """

    cluster_id: str
    media_ids: tuple[str, ...]
    kind: Literal["dated", "undated"]
    start_utc: float | None
    end_utc: float | None
    undated_media_ids: tuple[str, ...] = ()
    # Why this cluster begins where it does. 'library_start' for the first.
    boundary_reasons: tuple[str, ...] = ()
    # Distinct recorded offsets among members. More than one means the event
    # crossed a timezone, which a label renderer needs to know.
    utc_offsets: tuple[int, ...] = ()
    # The gap threshold in force when this run was cut, in seconds. Recorded so
    # "why is this two albums" is answerable without re-running the pass.
    gap_threshold_s: float | None = None

    @property
    def size(self) -> int:
        return len(self.media_ids)


# ---------------------------------------------------------------------------
# Geometry
# ---------------------------------------------------------------------------


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in kilometres.

    Haversine rather than the spherical law of cosines: the law of cosines loses
    all precision at short range, and short range is most of what we compare.
    """
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = phi2 - phi1
    # Taking the difference in degrees before converting keeps the antimeridian
    # correct: 179 -> -179 gives -358 degrees, and sin(-179 deg) has the same
    # square as sin(1 deg), which is the 2 degree separation we want.
    d_lambda = math.radians(lon2 - lon1)
    a = (
        math.sin(d_phi / 2.0) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2.0) ** 2
    )
    # Clamp: for near-antipodal points floating point pushes `a` a hair above 1
    # and math.asin then raises ValueError on real data.
    return 2.0 * EARTH_RADIUS_KM * math.asin(math.sqrt(min(1.0, a)))


def coordinate_of(item: EventItem) -> tuple[float, float] | None:
    """The item's usable position, or None.

    Two cases return None that a naive reader would not expect, and both are
    load-bearing:

    * exactly one of latitude/longitude present. Defaulting the missing one to
      zero would place the photo on a meridian thousands of km away and split
      the event. Half a coordinate is not a coordinate.
    * exactly (0.0, 0.0). Null Island: cameras and exporters write it when they
      have no fix. It is technically in the Gulf of Guinea, where nobody has
      taken any of these photographs, and treating it as real manufactures a
      6000km jump on every affected file.
    """
    if item.latitude is None or item.longitude is None:
        return None
    if item.latitude == 0.0 and item.longitude == 0.0:
        return None
    return (item.latitude, item.longitude)


# ---------------------------------------------------------------------------
# Time claims
# ---------------------------------------------------------------------------


def undated_reason(item: EventItem, *, min_time_confidence: float = MIN_TIME_CONFIDENCE) -> str | None:
    """Why this item has no position on the timeline, or None if it does.

    Public and tested because "no silent anything" applies here more than
    anywhere: an item dropping out of the chronology is invisible in the output
    otherwise, and the caller needs to be able to report it.
    """
    # ORDER IS PART OF THE CONTRACT. An item usually fails more than one of
    # these at once (a scan has no timestamp AND unknown precision AND a
    # fabricated confidence), and this function returns exactly one string that
    # a person reads in a "why is this photo not in an album" report. The order
    # runs from most fundamental to least, so the answer names the thing that
    # would have to be fixed first. Reordering the checks changes the public
    # answer for every multiply-failing item without changing any count, so it
    # is the kind of regression that no cluster-shaped assertion would catch.
    if item.captured_at is None:
        return "no_timestamp"
    if item.time_precision == "unknown":
        # A TimeAssertion with precision 'unknown' means "we have no usable time
        # at all" (common.schema.json). A number arriving alongside it is a
        # placeholder, not a claim, and must not be believed.
        return "precision_unknown"
    if item.time_precision not in USABLE_TIME_PRECISIONS:
        return "precision_too_coarse"
    # Strict <, so the floor is the lowest ACCEPTED confidence, not the highest
    # rejected one. `<=` would reject an assertion sitting exactly on the
    # documented floor -- and 0.5 is not a rare value: it is what an extractor
    # emits when it means "no better than even", which callers set the floor to
    # in order to include.
    if item.time_confidence < min_time_confidence:
        return "low_time_confidence"
    return None


def _half_width_s(item: EventItem) -> float:
    return PRECISION_HALF_WIDTH_S[item.time_precision]


# ---------------------------------------------------------------------------
# Threshold derivation
# ---------------------------------------------------------------------------


def adaptive_gap_threshold(
    gaps: Iterable[float],
    *,
    floor_s: float = MIN_GAP_S,
    ceiling_s: float = MAX_GAP_S,
    default_s: float = DEFAULT_GAP_S,
    min_gaps: int = MIN_GAPS_FOR_ADAPTIVE,
    min_in_band: int = MIN_IN_BAND_GAPS_FOR_ADAPTIVE,
    min_ratio: float = MIN_KNEE_RATIO,
) -> float:
    """Derive the event-separating gap from the library's own gap distribution.

    THE SHAPE OF THE DATA. Gaps in a photo library are not one population. They
    are within-event gaps (seconds to a few hours) and between-event gaps (days
    to months), and the threshold we want is the empty stretch between the two.
    In log space that stretch is a visible step.

    WHY A KNEE SEARCH AND NOT THE OBVIOUS ALTERNATIVES.

    * A percentile needs to know what fraction of gaps are between-event, which
      is the number of events, which is what we are trying to compute. A 95th
      percentile is right for a library of 20 events and wrong for a library of
      2000.
    * Mean or median of gaps is dominated by bursts. Ten thousand frames of
      continuous shooting drive the median to a second, and any multiple of a
      second is still inside the burst.
    * A *local* median (adapting per region rather than per library) sounds
      better and is worse: it makes the threshold a function of how many photos
      were taken, which is a fact about the camera's burst mode, not about
      event structure. A 200-frame burst would pin the threshold to the floor
      for that stretch of the day.

    The search is restricted to the [floor, ceiling] band, so it can only ever
    resolve the genuinely ambiguous middle. Below the floor and above the
    ceiling the answer is not in question and the data does not get a vote.

    Falls back to `default_s` -- loudly, in the sense that the value returned is
    the documented constant rather than something derived -- when there is not
    enough evidence, or when the in-band gaps form a continuum with no step in
    it. Inventing a boundary from a smooth distribution would be worse than
    using a stated default.
    """
    if floor_s > ceiling_s:
        raise ValueError(f"empty threshold band: floor {floor_s} > ceiling {ceiling_s}")

    # Materialised before anything reads it, because this function makes two
    # passes (finiteness, then the sort) and a caller passing a generator --
    # `adaptive_gap_threshold(b - a for a, b in pairs)` is the natural way to
    # write the call -- would have it drained by the first pass. The second pass
    # would then see an empty sequence and return the default, silently, on
    # every library.
    gap_values = list(gaps)

    clamped_default = min(max(default_s, floor_s), ceiling_s)

    # A non-finite gap here would compare False against every threshold and
    # silently merge two events. It cannot arise from validated input, so
    # reaching this is a bug worth stopping on.
    for gap in gap_values:
        if not math.isfinite(gap):
            raise ValueError(f"non-finite gap in distribution: {gap!r}")

    positive = sorted(g for g in gap_values if g > 0.0)
    if len(positive) < min_gaps:
        return clamped_default

    in_band = [g for g in positive if floor_s <= g <= ceiling_s]
    if len(in_band) < min_in_band:
        return clamped_default

    best_ratio = 0.0
    best_index = -1
    for i in range(len(in_band) - 1):
        # in_band is sorted and all entries are >= floor_s > 0, so this cannot
        # divide by zero.
        ratio = in_band[i + 1] / in_band[i]
        # Strict >, so the SMALLEST knee among equal ratios wins. This is not a
        # tidiness preference. A log-uniform library (gaps at 6h, 12h, 24h, 48h,
        # 96h -- every ratio exactly 2) has no single step, and >= would take the
        # last one and put the threshold at 68h, which merges every real boundary
        # below three days. Taking the first puts it at 8.5h, which over-splits.
        # Over-splitting is the recoverable failure (module docstring), so on a
        # distribution that does not name a boundary we take the low one.
        if ratio > best_ratio:
            best_ratio, best_index = ratio, i

    if best_index < 0 or best_ratio < min_ratio:
        return clamped_default

    # Geometric mean, not arithmetic: the two populations are separated
    # multiplicatively, so the midpoint of the empty stretch is the geometric
    # one. An arithmetic midpoint of 14h and 72h sits at 43h, much closer to the
    # upper population than it should be.
    # Not clamped on the way out, deliberately. Both operands come from in_band,
    # so both are within [floor_s, ceiling_s], and the geometric mean of two
    # numbers lies between them -- a clamp here can never fire. Leaving one in
    # would be dead code that reads like a guarantee, so a future change that
    # widened the candidate set would look guarded when it was not. The band is
    # enforced where it is real: on `in_band` and on `clamped_default`.
    return math.sqrt(in_band[best_index] * in_band[best_index + 1])


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _require_finite(value: float, media_id: str, field_name: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        # NaN is the specific danger: `nan > threshold` is False, so a NaN
        # timestamp would sail through every split test and merge two events
        # without raising anything.
        raise ValueError(
            f"{field_name} on {media_id} is {value!r}; a non-finite value compares "
            "False against every threshold and would silently merge events"
        )
    return number


def _validate(items: Sequence[EventItem]) -> None:
    seen: set[str] = set()
    dimensions: int | None = None
    dimensions_from: str | None = None

    for item in items:
        if not isinstance(item.media_id, str) or not item.media_id:
            raise ValueError(f"media_id must be a non-empty string, got {item.media_id!r}")
        if item.media_id in seen:
            raise ValueError(
                f"duplicate media_id {item.media_id!r}; media_id is a primary key and "
                "a repeat means two different files collapsed upstream"
            )
        seen.add(item.media_id)

        if item.time_precision not in ALL_TIME_PRECISIONS:
            raise ValueError(
                f"unknown time_precision {item.time_precision!r} on {item.media_id}; "
                "it must be one of the TimeAssertion precisions"
            )

        _require_finite(item.time_confidence, item.media_id, "time_confidence")
        if not 0.0 <= item.time_confidence <= 1.0:
            raise ValueError(
                f"time_confidence {item.time_confidence} on {item.media_id} is outside [0,1]"
            )

        if item.captured_at is not None:
            _require_finite(item.captured_at, item.media_id, "captured_at")

        if item.utc_offset_minutes is not None and not -1080 <= item.utc_offset_minutes <= 1080:
            raise ValueError(
                f"utc_offset_minutes {item.utc_offset_minutes} on {item.media_id} is outside "
                "the contract range [-1080, 1080]"
            )

        if item.latitude is not None:
            _require_finite(item.latitude, item.media_id, "latitude")
            if not -90.0 <= item.latitude <= 90.0:
                raise ValueError(f"latitude {item.latitude} on {item.media_id} is outside [-90,90]")
        if item.longitude is not None:
            _require_finite(item.longitude, item.media_id, "longitude")
            if not -180.0 <= item.longitude <= 180.0:
                raise ValueError(
                    f"longitude {item.longitude} on {item.media_id} is outside [-180,180]"
                )

        if item.embedding is not None:
            if len(item.embedding) == 0:
                raise ValueError(f"empty embedding on {item.media_id}")
            if dimensions is None:
                dimensions, dimensions_from = len(item.embedding), item.media_id
            elif len(item.embedding) != dimensions:
                raise ValueError(
                    f"embedding dimension mismatch: {item.media_id} has "
                    f"{len(item.embedding)} but {dimensions_from} has {dimensions}; "
                    "vectors from different spaces are not comparable"
                )
            total = 0.0
            for component in item.embedding:
                value = _require_finite(component, item.media_id, "embedding component")
                total += value * value
            norm = math.sqrt(total)
            if abs(norm - 1.0) > EMBEDDING_NORM_TOLERANCE:
                # Verified rather than assumed. A vector scaled twice (or not at
                # all) still yields a plausible dot product, so trusting the
                # contract here would convert an upstream bug into a wrong
                # answer with no symptom.
                raise ValueError(
                    f"embedding on {item.media_id} has L2 norm {norm:.6f}, not 1.0; "
                    "every vector space in the contract stores normalised vectors "
                    "and a dot product on an unnormalised one is not a cosine"
                )


# ---------------------------------------------------------------------------
# The pass
# ---------------------------------------------------------------------------


def cluster_events(
    items: Iterable[EventItem],
    *,
    gap_threshold_s: float | None = None,
    location_split_km: float = LOCATION_SPLIT_KM,
    max_event_span_s: float = MAX_EVENT_SPAN_S,
    min_time_confidence: float = MIN_TIME_CONFIDENCE,
    undated_similarity_threshold: float = UNDATED_SIMILARITY_THRESHOLD,
    undated_similarity_margin: float = UNDATED_SIMILARITY_MARGIN,
) -> list[EventCluster]:
    """Group a library into events.

    Returns dated clusters in chronological order, followed by at most one
    'undated' cluster holding everything that could not be placed. Every input
    media_id appears in exactly one cluster: nothing is dropped, and nothing is
    inserted into an event it was not shown to belong to.

    `gap_threshold_s` pins the threshold instead of deriving it, which is what
    the eval harness uses to sweep it.

    `items` may be any iterable, including a generator: it is materialised
    before it is read.
    """
    # Every threshold below is compared with `<`/`>`, and NaN loses every one of
    # those comparisons. A NaN location_split_km would make `distance >
    # location_split_km` False for every pair on earth, so the location signal
    # would switch itself off and the library would come back under-split with
    # nothing raised -- the unrecoverable failure direction. Checking `<= 0.0`
    # alone lets exactly that value through, so each bound is checked for
    # finiteness first.
    if not math.isfinite(location_split_km) or location_split_km <= 0.0:
        raise ValueError(f"location_split_km must be finite and positive, got {location_split_km!r}")
    if not math.isfinite(max_event_span_s) or max_event_span_s <= 0.0:
        raise ValueError(f"max_event_span_s must be finite and positive, got {max_event_span_s!r}")
    if gap_threshold_s is not None and (
        not math.isfinite(gap_threshold_s) or gap_threshold_s <= 0.0
    ):
        raise ValueError(f"gap_threshold_s must be finite and positive, got {gap_threshold_s!r}")

    # NaN fails a range test rather than passing it (every comparison against it
    # is False), so these three need no separate finiteness check -- unlike the
    # bounds above, which are one-sided.
    if not 0.0 <= min_time_confidence <= 1.0:
        raise ValueError("min_time_confidence must be in [0,1]")
    # Both of these are compared against a cosine of two unit vectors, so their
    # meaningful ranges are fixed by geometry rather than by taste. A NaN here
    # is the quiet one: `best_score >= nan` is False for every item, so the
    # undated bucket silently swallows the entire library and every album comes
    # out missing its scans with nothing raised anywhere.
    if not -1.0 <= undated_similarity_threshold <= 1.0:
        raise ValueError(
            f"undated_similarity_threshold must be a cosine in [-1,1], got "
            f"{undated_similarity_threshold!r}"
        )
    if not 0.0 <= undated_similarity_margin <= 2.0:
        raise ValueError(
            f"undated_similarity_margin must be a cosine difference in [0,2], got "
            f"{undated_similarity_margin!r}"
        )

    # Materialised BEFORE the first read. This function traverses `items` twice
    # -- once to validate, once to partition into dated and undated -- and a
    # single-pass iterable would be exhausted by the validation pass, so the
    # partition would see nothing and the function would return an empty list
    # for a library it had just checked item by item. Silent total data loss,
    # from a call site (`cluster_events(record_to_item(r) for r in records)`)
    # that reads perfectly well.
    items = list(items)

    _validate(items)

    dated: list[EventItem] = []
    undated: list[EventItem] = []
    for item in items:
        if undated_reason(item, min_time_confidence=min_time_confidence) is None:
            dated.append(item)
        else:
            undated.append(item)

    # Sort by instant, then media_id. The second key is not cosmetic: two photos
    # taken in the same second are common (bursts, Live Photos) and without it
    # the run order would depend on input order and so would every cluster id.
    order = sorted(dated, key=lambda it: (it.captured_at, it.media_id))
    times = [float(it.captured_at) for it in order]  # type: ignore[arg-type]

    conservative_gaps: list[float] = []
    for i in range(len(order) - 1):
        nominal = times[i + 1] - times[i]
        # Shrink by the uncertainty both claims carry. Two day-precision photos
        # on consecutive days have a nominal 24h gap and a conservative 0: at
        # that precision they might have been minutes apart, and splitting on a
        # gap the data cannot support is inventing an event boundary.
        conservative_gaps.append(max(0.0, nominal - _half_width_s(order[i]) - _half_width_s(order[i + 1])))

    threshold = (
        gap_threshold_s
        if gap_threshold_s is not None
        # Derived from the same quantity the split test uses. Deriving from
        # nominal gaps and testing conservative ones would put the threshold in
        # a different unit from the thing it is compared against -- the two
        # differ by a full day in a WhatsApp-only library.
        else adaptive_gap_threshold(conservative_gaps)
    )

    reasons_at_boundary: dict[int, set[str]] = {}

    for i, gap in enumerate(conservative_gaps):
        # Strict >, so the threshold is the largest gap that still holds an
        # event together. `>=` would split on a gap exactly equal to it, which
        # matters because callers pin the threshold to a round number (the eval
        # harness sweeps 6h, 12h, 20h) and libraries shot on a timer land gaps
        # exactly on round numbers. The choice is arbitrary in isolation and is
        # not arbitrary in effect: it has to be the same one the derivation
        # assumes, and `adaptive_gap_threshold` returns a geometric mean placed
        # strictly between two observed gaps precisely so that the smaller one
        # stays inside the event.
        if gap > threshold:
            reasons_at_boundary.setdefault(i, set()).add("time_gap")

    # Location compares each geotagged item to the previous geotagged item, not
    # to its immediate predecessor: a run of un-geotagged photos in the middle
    # of an event must not hide the fact that the place changed across it. The
    # boundary is placed immediately before the item that moved.
    previous_geo: tuple[float, float] | None = None
    for index, item in enumerate(order):
        here = coordinate_of(item)
        if here is None:
            continue
        if previous_geo is not None:
            distance = haversine_km(previous_geo[0], previous_geo[1], here[0], here[1])
            # Strict >, matching the time test above: the parameter is the
            # furthest apart two photographs can be and still be one event. A
            # caller sweeping this value (the eval harness does) reads the
            # number it passed as inclusive, and `>=` would make the reported
            # radius exclusive -- a one-off difference at every setting.
            if distance > location_split_km:
                # index >= 1 whenever previous_geo is set, so this boundary index
                # is always a real interior position.
                reasons_at_boundary.setdefault(index - 1, set()).add("location_jump")
        previous_geo = here

    runs: list[tuple[int, int]] = []
    if order:
        start = 0
        for i in range(len(order) - 1):
            if i in reasons_at_boundary:
                runs.append((start, i))
                start = i + 1
        runs.append((start, len(order) - 1))

    runs = _enforce_max_span(runs, times, conservative_gaps, max_event_span_s, reasons_at_boundary)

    dated_clusters: list[tuple[list[str], float, float, tuple[str, ...], tuple[int, ...]]] = []
    for lo, hi in runs:
        members = [it.media_id for it in order[lo : hi + 1]]
        boundary = (
            tuple(sorted(reasons_at_boundary[lo - 1]))
            if lo > 0 and lo - 1 in reasons_at_boundary
            else ("library_start",)
        )
        offsets = tuple(
            sorted({it.utc_offset_minutes for it in order[lo : hi + 1] if it.utc_offset_minutes is not None})
        )
        dated_clusters.append((members, times[lo], times[hi], boundary, offsets))

    assigned = _assign_undated(
        undated,
        order,
        runs,
        threshold=undated_similarity_threshold,
        margin=undated_similarity_margin,
    )

    out: list[EventCluster] = []
    for run_index, (members, start_utc, end_utc, boundary, offsets) in enumerate(dated_clusters):
        # The one place undated member order is decided. `_assign_undated`
        # appends in whatever order the caller supplied its items, so without
        # this the tail of `media_ids` would follow input order -- output that
        # differs run to run for the same library, which is the determinism rule
        # broken in the least visible way available.
        extra = sorted(assigned.get(run_index, ()))
        all_members = tuple(members) + tuple(extra)
        out.append(
            EventCluster(
                cluster_id=_stable_cluster_id(all_members),
                media_ids=all_members,
                kind="dated",
                start_utc=start_utc,
                end_utc=end_utc,
                undated_media_ids=tuple(extra),
                boundary_reasons=boundary,
                utc_offsets=offsets,
                gap_threshold_s=threshold,
            )
        )

    # Same reason as `extra` above: the bucket's order is part of the output.
    leftovers = sorted(assigned.get(-1, ()))
    if leftovers:
        out.append(
            EventCluster(
                cluster_id=_stable_cluster_id(leftovers),
                media_ids=tuple(leftovers),
                kind="undated",
                start_utc=None,
                end_utc=None,
                undated_media_ids=tuple(leftovers),
                boundary_reasons=("undated",),
                utc_offsets=(),
                gap_threshold_s=threshold,
            )
        )

    return out


def _enforce_max_span(
    runs: Sequence[tuple[int, int]],
    times: Sequence[float],
    conservative_gaps: Sequence[float],
    max_span_s: float,
    reasons_at_boundary: dict[int, set[str]],
) -> list[tuple[int, int]]:
    """Cut any run longer than `max_span_s` at its quietest interior gap.

    Recursive by nature, iterative in fact: a pathological library (one photo a
    day for twenty years, no gap anywhere above threshold) recurses once per
    resulting cluster, and Python's stack limit is not a place to discover that.

    Ties among equally quiet gaps break toward the middle of the run, THEN
    toward the earlier index. Breaking on index alone looks harmless and is not:
    a perfectly regular library (one photo every twelve hours for two months)
    has every interior gap identical, so "earliest" peels one item off the front
    per pass and turns a two-month run into seventy single-photo albums. Nothing
    raises; the output is just wrong. Splitting nearest the temporal midpoint
    halves the run each time instead.
    """
    out: list[tuple[int, int]] = []
    for run in runs:
        stack = [run]
        while stack:
            lo, hi = stack.pop()
            if hi <= lo or times[hi] - times[lo] <= max_span_s:
                out.append((lo, hi))
                continue
            midpoint = (times[lo] + times[hi]) / 2.0
            best = max(
                range(lo, hi),
                key=lambda i: (
                    conservative_gaps[i],
                    -abs((times[i] + times[i + 1]) / 2.0 - midpoint),
                    # Final tiebreak: the EARLIER index. A run with an odd number
                    # of interior gaps and uniform spacing has two candidates
                    # equidistant from the midpoint, and something has to choose.
                    # `-i` (earliest) rather than `i` (latest) only because a
                    # rule is needed and this one is stated; what matters is that
                    # it is a fixed rule, since `max` over a dict-free range is
                    # otherwise decided by iteration order and the cut point --
                    # and therefore every downstream cluster id -- would move.
                    -i,
                ),
            )
            if conservative_gaps[best] <= 0.0:
                # Every interior gap is inside the precision of the claims. There
                # is no quiet moment to cut at, and cutting at an arbitrary index
                # would be a fabricated boundary. Leave the run over-long; the
                # caller can see the span.
                out.append((lo, hi))
                continue
            reasons_at_boundary.setdefault(best, set()).add("max_span")
            # Push the later half first so the earlier half is popped first and
            # `out` stays chronological. This is a stack, so pushing in the
            # obvious (earlier-first) order emits the halves back to front, and
            # `cluster_events` returns `out` as it stands -- nothing sorts the
            # clusters afterwards. The output would be a correctly partitioned
            # library in scrambled order, which every membership assertion still
            # passes and which the album UI renders as a jumbled timeline.
            stack.append((best + 1, hi))
            stack.append((lo, best))
    return out


def _assign_undated(
    undated: Sequence[EventItem],
    order: Sequence[EventItem],
    runs: Sequence[tuple[int, int]],
    *,
    threshold: float,
    margin: float,
) -> dict[int, list[str]]:
    """Place undated items by appearance, or admit that we cannot.

    Returns run index -> media_ids, with -1 for "no event". An item is placed
    only when its best-matching event beats the threshold AND beats the
    second-best event by the margin. Both conditions matter: the first stops a
    scan of a document landing in a beach event, the second stops a photo that
    is equally at home in two events landing in whichever one sorted first,
    which would look like a decision and be a coin toss.

    Similarity to an event is the maximum over its members, not the distance to
    a centroid. A centroid over a diverse event (beach, dinner, fireworks) is a
    blurry vector close to nothing in particular, so centroid matching rejects
    exactly the items it should accept -- a scanned wedding photograph matches
    *a* wedding photograph strongly and the wedding's average weakly.

    Cost is O(undated x dated) dot products. Acceptable while the undated share
    of a library is small; if that stops being true this wants the sqlite-vec
    index instead, which is a media-db call and a boundary change, not a tweak.
    """
    placements: dict[int, list[str]] = {}
    if not undated:
        return placements

    run_vectors: list[list[tuple[float, ...]]] = []
    for lo, hi in runs:
        run_vectors.append([it.embedding for it in order[lo : hi + 1] if it.embedding is not None])

    # Iterated in the order given. Placement is decided per item against the
    # runs and never against the other undated items, so this order cannot
    # change any DECISION -- only the order ids land in each list, which the
    # caller sorts before it emits them.
    #
    # This loop used to sort too. Two guards for one invariant is worse than
    # one: each masks the other, so neither can be shown to do anything, and a
    # guard nothing can test is a guard that quietly stops working. The single
    # guarantee lives at the output boundary in `cluster_events`, which is where
    # EventCluster's member order is actually promised.
    for item in undated:
        if item.embedding is None or not runs:
            placements.setdefault(-1, []).append(item.media_id)
            continue

        best_score = _NO_SCORE
        best_run = -1
        second_score = _NO_SCORE
        for run_index, vectors in enumerate(run_vectors):
            if not vectors:
                continue
            # MAX over members, not mean. See the docstring: a mean is the
            # centroid in disguise, and the centroid of a diverse event (beach,
            # dinner, fireworks) is close to nothing in particular, so it
            # rejects exactly the items the margin test was built to accept.
            score = max(_dot(item.embedding, vector) for vector in vectors)
            if score > best_score:
                # The old leader is demoted to runner-up. Dropping this line
                # leaves second_score at its sentinel whenever the scores happen
                # to arrive in ascending order, and the accept test reads a
                # sentinel second_score as "only one candidate event" -- so the
                # ambiguity guard switches itself off for exactly the libraries
                # where the best match is late in the timeline. The test still
                # passes for descending arrivals, which is why this survives
                # every obvious test.
                second_score = best_score
                best_score, best_run = score, run_index
            elif score > second_score:
                second_score = score

        # Written as an explicit accept test rather than `if not (...)` so that a
        # NaN score -- which would make every comparison False -- falls to the
        # undated bucket rather than into an arbitrary event.
        accepted = (
            best_run >= 0
            # >= : the threshold is the lowest score that still places an item,
            # matching "must reach" in the constant's own documentation. The
            # eval harness sweeps this value and reports the number it passed.
            and best_score >= threshold
            # Compared to the sentinel by identity of value, not by magnitude, so
            # that a real runner-up is never mistaken for an absent one.
            and (second_score == _NO_SCORE or best_score - second_score >= margin)
        )
        placements.setdefault(best_run if accepted else -1, []).append(item.media_id)

    return placements


def _dot(a: Sequence[float], b: Sequence[float]) -> float:
    if len(a) != len(b):
        raise ValueError(f"dimension mismatch: {len(a)} vs {len(b)}")
    return math.fsum(x * y for x, y in zip(a, b))


def _stable_cluster_id(members: Sequence[str]) -> str:
    """uuid5 over sorted membership.

    Content-derived, so two runs over the same library agree without a database
    round-trip, and so an id never encodes the order anything was iterated in.
    The tradeoff, stated: adding one photo to an event changes that event's id.
    AlbumSpec.event.event_cluster_id is a Uuid, and uuid5 satisfies its pattern.
    """
    return str(uuid.uuid5(uuid.NAMESPACE_URL, _ID_NAMESPACE_PREFIX + ",".join(sorted(members))))
