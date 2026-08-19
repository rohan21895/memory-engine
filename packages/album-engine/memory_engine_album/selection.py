"""Album photo selection — choosing the forty that go in the book.

Picking 40 from 4000 is the product. Everything upstream (ingest, quality
fusion, dedupe) exists to make this decision possible, and everything
downstream (layout, print validation) assumes it was made well.

THE PROBLEM IS NOT QUALITY. IT IS COVERAGE.

Naive top-N by fused score returns forty photographs of the same sunset,
because the sunset genuinely *is* the most photogenic thing that happened. It
also over-covers day one of a trip, because that is when everyone was still
taking photos, and it will quietly omit the quiet child who does not perform
for cameras. Each of those failures produces a plausible album that the person
who lived the trip recognises as wrong.

So selection optimises a marginal-gain objective over four coverage axes --
time, place, scene, people -- on top of quality, and puts a hard floor under
people before quality gets a vote at all.

WHY GREEDY MARGINAL GAIN (AND WHERE IT STOPS BEING SUBMODULAR)

The bucket-coverage terms are deliberately concave in the bucket count: the
gain of adding the k-th photo of a bucket is `d**k * (1 - d)`, which is
strictly decreasing. A weighted sum of such terms plus a modular quality term
is monotone submodular, so greedy is within (1 - 1/e) of optimal and, more
usefully, is O(n * k) and explainable one pick at a time ("this went in because
it was the only photo from the afternoon of day three").

The redundancy term is NOT submodular. It is MMR-style: a penalty on the
maximum embedding similarity to anything already chosen. The theoretically
correct object is facility location -- sum over ALL candidates of the best
similarity to the chosen set, which IS submodular and is the right way to say
"this set represents the event". It was rejected on cost: its marginal gain is
O(n) per candidate per round, i.e. O(n^2 k) overall, which for a 4000-photo
event in pure Python is minutes, not milliseconds. MMR gives the same
qualitative behaviour (the second sunset is worth much less than the first) at
O(k) per candidate. The approximation guarantee is lost; the behaviour that
matters is kept. If this ever moves to numpy, facility location is the upgrade.

THE COMPARABILITY RULE IS LOAD-BEARING HERE

`FusedScore.comparable_with` says two scores measured differently must not be
ranked against each other. In a real event that is not an edge case: a
landscape is scored on seven signals and a portrait on eight, because
`face_quality` does not apply to the landscape. `fusion.rank` refuses to order
them, and it is right to.

Selection cannot simply refuse -- an album of a beach day contains both. So
candidates are partitioned into comparability classes and each class is ranked
*within itself* by `fusion.rank`. What crosses class boundaries is not the
value but the WITHIN-CLASS STANDING: "top 10% of the portraits" and "top 10% of
the landscapes" are the same claim about two different measurements, and that
claim is comparable when the raw numbers are not.

Standing is shrunk toward the photo's own fused value for small classes. A class
of one member would otherwise score a perfect 1.0 -- "best of its kind" -- when
all it has demonstrated is that nothing else was measured that way, which
mid-scan is the common case and not a compliment. Shrinking toward a flat 0.5
instead would fix that and introduce a worse one: pure rank knows nothing about
how good a class is, so a large mediocre class would out-stand a small excellent
one on SIZE alone. The value is what standing falls back on when the class has
not demonstrated enough to rank.

PEOPLE GET A FLOOR, NOT A WEIGHT

An album of a family trip that omits one child is a catastrophic failure, far
worse than one containing a slightly worse photo. A weight cannot express that:
a weight trades off, and this does not trade off. So coverage of every person
runs as a separate phase before the quality-driven fill, using greedy maximum
coverage (a group shot that covers three uncovered people beats three separate
better photos), and the floor is allowed to spend budget the fill phase would
have spent better.

The mirror-image failure is one photogenic person taking over the album, so
there is also a per-person cap -- suspended when the event only has one person
in it, because an album of one grandchild is legitimately all the grandchild.

SCARCITY BEATS THE QUALITY FLOOR, BUT NOT ELIMINATION

The only surviving photo of grandmother is worth printing at any sharpness. So
the absolute quality floor is waived for the single best photo of a person who
has no photo above the floor at all.

ELIMINATION IS NOT WAIVED. A black frame or a lens-capped frame containing
grandmother is still a black frame -- `fusion.eliminate` rejected it because
there is no image there, not because it scored badly, and printing it is not a
tribute. This is the same elimination/scoring distinction fusion draws, applied
one layer up.

DETERMINISM

Same event and same N produce the same album, every time. Candidates are sorted
by media_id before anything iterates; every gain is quantised to six decimals
before comparison (float addition is not associative, and an unquantised gain
that differs in the last bit reorders the greedy on a different machine); every
tie breaks on media_id, matching `select_primary` and `rank` so the three never
disagree. Timestamps without an explicit offset are rejected rather than parsed,
because a naive datetime resolves against the host's local zone and would sort
the same album differently in Mumbai and in California.

WHAT THIS MODULE DOES NOT DO

It does not lay out pages, and it does not compute DPI. `min_long_edge_px` is a
gate the caller configures with a number the vendor profile supplies; the
authoritative print check is the print validator's hard gate and runs later.
Selection only avoids proposing a photo that cannot survive it.
"""

from __future__ import annotations

import math
from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from typing import Callable, Iterable, Mapping, Sequence

from memory_engine_ranking.dedupe import cosine_distance
from memory_engine_ranking.fusion import FusedScore, rank

__all__ = [
    "DEFAULT_POLICY",
    "ConstraintResult",
    "MeasurementClass",
    "Rejection",
    "Selection",
    "SelectionCandidate",
    "SelectionError",
    "SelectionPolicy",
    "candidate_from_media_record",
    "geo_cell",
    "select",
]


class SelectionError(ValueError):
    """An input that would make the selection wrong rather than merely worse."""


# Bucket coverage decays geometrically: the first photo of a bucket is worth
# (1 - d), the second d*(1 - d), and so on. Strictly decreasing, so the summed
# objective is submodular and greedy has a guarantee. d = 0.5 means the second
# photo of a bucket is worth half the first, which is roughly how a person
# describes it ("I already have one of those").
_BUCKET_DECAY = 0.5

# The key under which "we do not know" lives for every categorical axis. It is
# a bucket in its own right, never folded into a real one -- see the note in
# `_time_bucket` about why unknown capture time must not become bin zero.
_UNKNOWN = "unknown"

# Pseudo-person key for a photo with nobody in it, so scenery competes for
# people-axis coverage instead of being invisible to it.
_NO_PEOPLE = ""

# How many peers a comparability class must hold before its internal ordering
# outweighs the raw fused value. A class of one gets (1.0*1 + value*4)/5 --
# mostly its own value -- rather than a perfect 1.0 for being alone. Judgement
# call, not a fitted number; see `_shrink`.
_STANDING_PRIOR = 4.0


@dataclass(frozen=True)
class SelectionPolicy:
    """Every knob, as data, so an album style is a stored row rather than a
    code change -- the same reason `fusion.Weights` is data.

    The numbers are chosen to be defensible rather than tuned; the eval harness
    is what will move them.
    """

    # --- hard gates -------------------------------------------------------
    # Absolute, not relative: a photo below this is not printed however thin
    # the pool. Set well below fusion's own elimination floor's neighbourhood
    # because fusion already removed the frames that are not images at all.
    quality_floor: float = 0.35
    # Vendor pixel floor. None disables the gate entirely; a number means the
    # gate is ARMED, and an armed gate rejects a candidate whose pixel size is
    # unknown. Passing the unknown case is how a load gate silently admits
    # unverified input.
    min_long_edge_px: int | None = None
    # Floor on the candidate's measured FACE sharpness (scale-normalised, see
    # classical.measure_face_sharpness). 0.12 is one order of magnitude under
    # a typical in-focus face on the calibration library and just above its
    # visibly-blurred cluster. A candidate with no measurement passes -- see
    # `_apply_quality_floor`.
    face_sharpness_floor: float = 0.12
    # Floor on the HEAD-region sharpness (face box grown to take in hair and
    # shoulders). Catches whole-subject smear -- camera shake, a moving
    # subject against a soft surround. ponytail: a sharp patterned BACKGROUND
    # inside the grown box masks a smeared subject (measured 0.97 on exactly
    # such a frame), so mid-gesture awkwardness is primarily the job of the
    # `composed`/mid-blink expression axes; this floor is the net under them.
    head_sharpness_floor: float = 0.08
    # A photo whose significant face has features cropped by the frame edge
    # (any landmark outside the margin) is rejected: "full faces visible" is
    # an album rule, not a preference. Scarce-person waiver applies.
    reject_cut_faces: bool = True

    # --- expression (zero-shot ranks, computed inside `select`) -----------
    # Raw contrasts arrive on the candidates; `select` converts each axis to a
    # percentile within THIS pool, because absolute zero-shot values are not
    # calibrated (the modality gap) while the ordering over one library is
    # exactly the signal. All weights are against a quality term spanning [0,1].
    weight_smile: float = 0.35
    weight_composed: float = 0.25
    # The strongest expression weight: "bring the best photos". Percentile of
    # the zero-shot aesthetic contrast (light, composition, clutter). At 0.55
    # it can overturn a mid-size quality-standing gap but not a large one --
    # a technically ruined frame still loses however pretty its light.
    weight_aesthetic: float = 0.55
    # Penalty for eyes that read closed on a photo that does NOT read as a
    # sleeping shot: an adult mid-blink, a grimace. Large on purpose -- it
    # must be able to undo a full smile bonus and most of a quality lead.
    mid_blink_penalty: float = 0.60
    # Sleeping-baby photos are a cherished TYPE, never rejected -- but a book
    # that is one-third closed eyes reads as a nap log. Cap as an album
    # fraction, enforced in the fill phase.
    max_sleeping_fraction: float = 0.20
    # Minimum raw sleeping contrast before a photo is TYPED as sleeping at
    # all: an evidence dead-zone, not a calibration. Genuine sleeping shots
    # measured 0.046-0.123 on a real library; contrasts from unrelated
    # content sit within +/-0.035 of zero, and typing on that noise let the
    # cap fire on photos that were never naps.
    sleeping_min_contrast: float = 0.04

    # --- people -----------------------------------------------------------
    min_per_person: int = 1
    # Cap as a fraction of the album. Suspended when the event contains one
    # person or none.
    max_per_person_fraction: float = 0.5
    # Album must not be wall-to-wall faces. Floor, applied only to the extent
    # such photos exist.
    min_non_people_fraction: float = 0.15

    # --- objective weights ------------------------------------------------
    # Each is the value of the FIRST photo covering a fresh bucket on that
    # axis, against a quality term that spans [0,1]. Time is nearly level with
    # quality on purpose: a photo from a day the album has not reached should
    # beat a marginally better photo from a day it already covers, which is the
    # whole difference between an album that tells a trip and an album of
    # whichever afternoon produced the most photos. Anything much below quality
    # here cannot outvote it and the temporal term becomes decorative.
    weight_quality: float = 1.0
    weight_time: float = 0.85
    weight_place: float = 0.50
    weight_scene: float = 0.35
    weight_person: float = 0.60
    weight_redundancy: float = 0.80
    # Cosine similarity below which two photos are simply different pictures
    # and attract no redundancy penalty at all. This is a FLOOR, not the
    # operating point: a homogeneous library (one baby, one home) measures
    # 0.74 median similarity between photos taken DAYS apart, so a fixed free
    # zone turns the penalty into a constant offset that discriminates
    # nothing. `select` calibrates the actual free zone to the library's own
    # measured similarity, never below this value.
    redundancy_free_similarity: float = 0.60

    # --- shots ------------------------------------------------------------
    # At or above this similarity, two photos taken within `burst_window_s`
    # of each other are the same photograph being taken repeatedly -- a
    # burst, a re-pose, a "wait, one more". An album gets ONE of them however
    # good the others are; a penalty cannot express that, because a burst of
    # a great moment out-scores a single frame of an ordinary one on quality
    # alone, which is precisely the failure this knob exists to stop.
    shot_similarity: float = 0.93
    # An hour, not three minutes: a studio session re-shoots the same pose
    # across the whole sitting (measured 0.95+ pairs 26 minutes apart on a
    # real maternity shoot), and at 0.93+ similarity the time gap carries no
    # information -- the similarity bar is what discriminates.
    burst_window_s: float = 3600.0
    # The hard backstop the user actually asked for: "no two photos should
    # be similar". A candidate this close to ANY already-selected photo is
    # ineligible while anything more distinct remains -- relaxed only when
    # the pool has nothing else, because a short book beats an empty one.
    max_selected_similarity: float = 0.92
    # Within one shot group the frames are the SAME picture except for
    # defects, so the clean_frame contrast compares like with like: lighting
    # and subjects cancel, and what remains is the assistant caught at the
    # frame edge. A frame more than this far below its own group's best is a
    # worse version of an existing photograph, and a worse version of a
    # picture the album can already have is never selectable. Measured: a
    # crew-contaminated take sat 0.020 under its clean twins.
    clean_frame_group_margin: float = 0.015

    # --- timeline ---------------------------------------------------------
    # 0 means "one bin per photo we intend to select, capped", which makes the
    # temporal term scale with the album rather than with the trip.
    time_bins: int = 0
    max_time_bins: int = 24

    # --- quality bar for the best photo in the book -----------------------
    hero_quality: float = 0.60

    def validate(self) -> None:
        for name in (
            "quality_floor", "max_per_person_fraction", "min_non_people_fraction",
            "redundancy_free_similarity", "hero_quality", "shot_similarity",
            "face_sharpness_floor", "head_sharpness_floor",
            "max_sleeping_fraction", "sleeping_min_contrast",
            "max_selected_similarity", "clean_frame_group_margin",
        ):
            value = getattr(self, name)
            # NaN is the reason this is `not (0 <= v <= 1)` and not `v < 0 or
            # v > 1` spelled optimistically: every comparison against NaN is
            # False, so a NaN floor would let everything through a `<` gate and
            # nothing through a `>` one, with no error either way.
            if not math.isfinite(value) or not 0.0 <= value <= 1.0:
                raise SelectionError(f"policy.{name}={value!r} must be a finite value in [0,1]")
        for name in (
            "weight_quality", "weight_time", "weight_place", "weight_scene",
            "weight_person", "weight_redundancy",
            "weight_smile", "weight_composed", "weight_aesthetic", "mid_blink_penalty",
        ):
            value = getattr(self, name)
            if not math.isfinite(value) or value < 0.0:
                raise SelectionError(f"policy.{name}={value!r} must be finite and non-negative")
        if self.min_per_person < 0:
            raise SelectionError("policy.min_per_person must be >= 0")
        if self.time_bins < 0 or self.max_time_bins < 1:
            raise SelectionError("policy.time_bins must be >= 0 and max_time_bins >= 1")
        if self.min_long_edge_px is not None and self.min_long_edge_px <= 0:
            raise SelectionError("policy.min_long_edge_px must be positive when set")
        if not math.isfinite(self.burst_window_s) or self.burst_window_s < 0.0:
            raise SelectionError("policy.burst_window_s must be finite and non-negative")


DEFAULT_POLICY = SelectionPolicy()


@dataclass(frozen=True)
class SelectionCandidate:
    """One photo entering selection.

    Deliberately not a MediaRecord: selection needs a dozen fields, and taking
    the whole record would make every test construct a 40-key document. Use
    `candidate_from_media_record` to build these from real records so the
    mapping lives in exactly one place -- a hand-rolled translation between a
    record and a planner's input is where fields get quietly dropped.
    """

    media_id: str
    score: FusedScore

    # Capture instant, RFC 3339 WITH an explicit offset. None when the capture
    # time is unknown or too coarse to place on a trip timeline.
    captured_utc: str | None = None

    # Person ids already through the automated-output face gate. Anything less
    # certain must not appear here: precision over recall, because a wrong
    # person in a family album is the catastrophic failure.
    person_ids: tuple[str, ...] = ()

    place_key: str | None = None
    scene_type: str | None = None

    # Dedupe membership. A secondary is never selected -- the group's primary
    # already represents it.
    dedupe_group_id: str | None = None
    is_dedupe_primary: bool = True

    excluded_from_automation: bool = False
    exclusion_reasons: tuple[str, ...] = ()
    # Tri-state, matching the contract: None = no opinion, True = forced in,
    # False = forced out.
    user_override: bool | None = None
    user_hidden: bool = False
    user_favorite: bool = False

    long_edge_px: int | None = None
    # Minimum scale-normalised sharpness across the photo's significant faces,
    # or None when no face was measured. Supplied by the caller (the face rows
    # live outside the MediaRecord); None never fails the face floor.
    face_sharpness: float | None = None
    # Same measure over the grown head regions (hair, shoulders): the
    # mid-movement detector. None never fails the head floor.
    head_sharpness: float | None = None
    # True when a significant face has features cropped by the frame edge
    # (computed by the caller from the stored landmarks). None-like False:
    # absence of face evidence is not a cut face.
    face_cut: bool = False
    # Raw zero-shot contrast scores, cos(image, positive) - cos(image,
    # negative), per expression axis. Uncalibrated by design -- `select`
    # ranks each axis within the pool. None = no embedding or no head file;
    # every expression term then stays neutral for this candidate.
    smile: float | None = None
    awake: float | None = None
    aesthetic: float | None = None
    clean_frame: float | None = None
    sleeping: float | None = None
    composed: float | None = None
    embedding: tuple[float, ...] | None = None
    # `ContentAnalysis.embedding.space` -- WHICH model produced the floats.
    # Carried because cosine similarity between two different spaces is a number
    # with no meaning: SigLIP's 0.81 and CLIP's 0.81 are not the same claim, and
    # a library re-embedded halfway through a model migration holds both. The
    # dimension check does not catch it (two 1152-d spaces are equally wrong),
    # so nothing at all would flag the mixture and the redundancy penalty would
    # simply fire on noise. `select` refuses the mixture rather than comparing
    # across it.
    embedding_space: str | None = None

    def measurement_key(self) -> tuple[frozenset[str], str, str]:
        """The comparability class this score belongs to.

        Derived from the same three properties `FusedScore.comparable_with`
        tests. `select` asserts the derivation still agrees with that method
        rather than trusting it, because this key silently becoming a weaker
        partition than the real rule is exactly the failure mode that produces
        a plausible wrong album.
        """
        return (self.score.measured, self.score.weights_digest, self.score.feature_set_id)


@dataclass(frozen=True)
class Rejection:
    media_id: str
    # One of AlbumSpec SelectionReport's rejection reasons.
    reason: str
    detail: str = ""


@dataclass(frozen=True)
class ConstraintResult:
    constraint: str
    satisfied: bool
    detail: str = ""


@dataclass(frozen=True)
class MeasurementClass:
    """One comparability class and the members ranked within it."""

    measured: frozenset[str]
    weights_digest: str
    feature_set_id: str
    ranked_media_ids: tuple[str, ...]

    @property
    def size(self) -> int:
        return len(self.ranked_media_ids)


@dataclass(frozen=True)
class Selection:
    """The chosen photos, and everything needed to defend the choice."""

    # Chronological, which is the order an album tells the story in. Photos
    # with no usable capture time sort last rather than to the epoch.
    selected: tuple[str, ...]
    # Same set, best-first by within-class standing. `by_standing[0]` is the
    # hero candidate.
    by_standing: tuple[str, ...]
    candidate_count: int
    rejected: tuple[Rejection, ...]
    constraints: tuple[ConstraintResult, ...]
    # Survivors that lost on merit rather than on a rule. Kept in full here and
    # summarised in the report -- see `to_selection_report`.
    unselected: tuple[str, ...]
    person_counts: Mapping[str, int]
    missing_person_ids: tuple[str, ...]
    rescued_media_ids: tuple[str, ...]
    classes: tuple[MeasurementClass, ...]

    @property
    def selected_count(self) -> int:
        return len(self.selected)

    def to_selection_report(self, *, include_no_space: bool = False) -> dict:
        """Populate AlbumSpec `$defs/SelectionReport`.

        `include_no_space` is off by default and that is a deliberate, stated
        choice rather than a silent drop. Listing every survivor that merely
        lost on merit puts 3,960 entries in the spec for a 4,000-photo event,
        which makes the AlbumSpec unreadable and unstorable for the one
        question the report exists to answer ("why is my best photo missing?").
        The count is still recoverable -- `candidate_count - selected_count`
        minus the listed rejections -- and `Selection.unselected` carries every
        id for a caller that wants them. Turn it on for small events and for
        the eval harness.
        """
        rejected = list(self.rejected)
        if include_no_space:
            rejected.extend(
                Rejection(media_id=media_id, reason="no_space")
                for media_id in self.unselected
            )
        return {
            "candidate_count": self.candidate_count,
            "selected_count": self.selected_count,
            "diversity_constraints": [
                {
                    "constraint": entry.constraint,
                    "satisfied": entry.satisfied,
                    "detail": entry.detail,
                }
                for entry in self.constraints
            ],
            # Sorted, and one entry per media_id: a report that listed the same
            # photo twice with two reasons would make the count arithmetic
            # above stop adding up.
            "rejected": [
                {"media_id": entry.media_id, "reason": entry.reason}
                for entry in sorted(rejected, key=lambda r: r.media_id)
            ],
        }


# ---------------------------------------------------------------------------
# Contract adapter
# ---------------------------------------------------------------------------

# Capture-time precisions fine enough to place a photo on a trip timeline.
# Anything coarser ('month', 'year', 'unknown') tells us which holiday, not
# which afternoon, and binning it would fabricate a position in the story.
_TIMELINE_PRECISIONS = frozenset({"second", "minute", "hour", "day"})

# MediaRecord exclusion reasons -> SelectionReport rejection reasons. The two
# vocabularies are different sizes on purpose; mapping is explicit so a new
# exclusion reason fails loudly here instead of defaulting to something wrong.
_EXCLUSION_TO_REASON = {
    "screenshot": "excluded_content",
    "document": "excluded_content",
    "nsfw": "excluded_content",
    "sensitive": "excluded_content",
    "corrupt": "excluded_content",
    "unreadable": "excluded_content",
    "unsupported_codec": "excluded_content",
    "duplicate_secondary": "near_duplicate",
    "below_quality_floor": "below_quality_floor",
    "black_frame": "below_quality_floor",
    "lens_obstructed": "below_quality_floor",
    "too_short": "excluded_content",
    "user_hidden": "user_hidden",
}


def candidate_from_media_record(
    record: Mapping,
    score: FusedScore,
    *,
    embedding: Sequence[float] | None = None,
    place_key: str | None = None,
    face_sharpness: float | None = None,
    head_sharpness: float | None = None,
    face_cut: bool = False,
    expression: Mapping[str, float] | None = None,
) -> SelectionCandidate:
    """A contract MediaRecord + its FusedScore -> SelectionCandidate.

    THE supported way in, for the reason `signals_from_media_record` exists in
    fusion: a package that references the generated types in prose and bypasses
    them in practice does not actually consume the contract it claims to.

    `embedding` is passed separately because `ContentAnalysis.embedding` is a
    `VectorRef` that normally lives in the vector index rather than inline --
    the record deliberately does not carry the floats. Inline fixture values
    are read when present so tests stay self-contained.

    `place_key` is passed in rather than derived, because how coarsely to
    bucket a location is an album-style decision (a city walk and a
    round-the-world trip want different cells). `geo_cell` builds one from GPS
    when the caller has no better idea.
    """
    capture = record.get("capture") or {}
    assertion = capture.get("captured_at") or {}
    precision = assertion.get("precision")
    captured_utc = assertion.get("utc") if precision in _TIMELINE_PRECISIONS else None

    faces = record.get("faces") or {}
    content = record.get("content") or {}
    exclusion = record.get("exclusion") or {}
    user = record.get("user") or {}
    dedupe = record.get("dedupe") or {}
    image = record.get("image") or {}

    vector = content.get("embedding") or {}
    # Read from the VectorRef whether or not the floats came inline: a caller
    # that fetched them from the vector index still fetched them from THIS
    # record's space, and dropping the label there is exactly how two spaces end
    # up mixed with nothing to detect it.
    embedding_space = vector.get("space")
    if embedding is None:
        if vector.get("storage") == "inline" and vector.get("values") is not None:
            embedding = vector["values"]

    if place_key is None:
        gps = capture.get("gps")
        if gps is not None:
            place_key = geo_cell(gps["latitude"], gps["longitude"])

    oriented = image.get("oriented_size") or {}
    width, height = oriented.get("width"), oriented.get("height")
    long_edge = max(width, height) if width is not None and height is not None else None

    return SelectionCandidate(
        media_id=record["media_id"],
        score=score,
        captured_utc=captured_utc,
        # Sorted so the tuple is a stable identity, and confirmed-only because
        # the contract promises that field already passed the face gate.
        person_ids=tuple(sorted(faces.get("confirmed_person_ids") or ())),
        place_key=place_key,
        scene_type=content.get("scene_type"),
        dedupe_group_id=dedupe.get("group_id"),
        # A record with no dedupe block was never grouped, so it is its own
        # primary. Defaulting the other way would drop every ungrouped photo.
        is_dedupe_primary=bool(dedupe.get("is_primary", True)),
        excluded_from_automation=bool(exclusion.get("excluded_from_automation", False)),
        exclusion_reasons=tuple(sorted(exclusion.get("reasons") or ())),
        user_override=exclusion.get("user_override"),
        user_hidden=bool(user.get("hidden", False)),
        user_favorite=bool(user.get("favorite", False)),
        long_edge_px=long_edge,
        face_sharpness=float(face_sharpness) if face_sharpness is not None else None,
        head_sharpness=float(head_sharpness) if head_sharpness is not None else None,
        face_cut=bool(face_cut),
        smile=_expression_axis(expression, "smile"),
        awake=_expression_axis(expression, "awake"),
        aesthetic=_expression_axis(expression, "aesthetic"),
        clean_frame=_expression_axis(expression, "clean_frame"),
        sleeping=_expression_axis(expression, "sleeping"),
        composed=_expression_axis(expression, "composed"),
        embedding=tuple(float(x) for x in embedding) if embedding is not None else None,
        embedding_space=embedding_space if embedding is not None else None,
    )


def _expression_axis(expression: Mapping[str, float] | None, name: str) -> float | None:
    if expression is None:
        return None
    value = expression.get(name)
    return float(value) if value is not None else None


def geo_cell(latitude: float, longitude: float, *, precision_deg: float = 0.05) -> str:
    """A coarse, deterministic place bucket from a GPS point.

    Integer cell indices rather than rounded coordinates: rounding produces a
    string whose formatting differs between languages, and `math.floor` of a
    negative quotient behaves correctly where `int()` truncates toward zero and
    would merge the cells either side of the equator and the prime meridian.
    """
    if not math.isfinite(latitude) or not math.isfinite(longitude):
        raise SelectionError(f"non-finite gps point ({latitude!r}, {longitude!r})")
    if precision_deg <= 0.0:
        raise SelectionError("precision_deg must be positive")
    return f"{math.floor(latitude / precision_deg)}:{math.floor(longitude / precision_deg)}"


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _quantise(value: float) -> float:
    """Six decimals, matching fusion.

    Two candidates whose gains differ in the sixteenth decimal are the same
    candidate as far as any human is concerned, but `>` will pick one of them,
    and which one depends on summation order and on the machine. Quantising
    turns that into an explicit tie, which the media_id tiebreak then resolves
    the same way everywhere.
    """
    return round(value + 0.0, 6)


def _epoch_seconds(candidate: SelectionCandidate) -> float | None:
    """Capture instant as a float, or None when there is no usable one."""
    raw = candidate.captured_utc
    if raw is None:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise SelectionError(
            f"{candidate.media_id}: captured_utc={raw!r} is not an RFC 3339 instant"
        ) from exc
    if parsed.tzinfo is None:
        # A naive datetime resolves against the HOST's local zone. The album
        # would then be ordered differently in Mumbai and in California from
        # the same input, which is a determinism failure that no test on one
        # machine can see. The contract's Timestamp requires an explicit
        # offset; enforce it rather than guessing UTC.
        raise SelectionError(
            f"{candidate.media_id}: captured_utc={raw!r} has no UTC offset. A naive "
            "timestamp resolves against the host timezone, so the same event would "
            "order differently on two machines."
        )
    return parsed.timestamp()


@lru_cache(maxsize=None)
def _bucket_gain(count: int) -> float:
    """Marginal value of adding one more item to a bucket already holding
    `count`.

    `d ** count`, so the FIRST photo of an uncovered bucket is worth the axis
    weight in full and each repeat is worth half the last. Writing it as
    `d**count * (1-d)` instead -- the other obvious form -- halves every axis
    silently: `weight_time = 0.6` then buys a maximum of 0.3, the coverage
    terms can never outvote a quality difference, and the album collapses onto
    whichever day has the most photos while every weight in the policy still
    reads as though it were doing something.

    Strictly decreasing either way, so the summed objective
    `(1 - d**k) / (1 - d)` is concave in the bucket count and the greedy keeps
    its submodular guarantee.
    """
    return _BUCKET_DECAY ** count


def _time_bucket(seconds: float | None, start: float, span: float, bins: int) -> str:
    """Which slice of the event's timeline this photo sits in.

    An unknown capture time gets its OWN bucket and never bin zero. Folding it
    into the first bin (which is what sorting missing dates to the epoch does)
    would make every undated photo look like day one and let the temporal term
    believe the first morning was already covered.
    """
    if seconds is None:
        return _UNKNOWN
    if span <= 0.0:
        return "t0"
    index = int((seconds - start) / span * bins)
    # The photo at exactly the end of the span computes to `bins`, one past the
    # last bin, and would get a bucket of its own that no other photo can share
    # -- so the single latest photo of every event would look maximally novel.
    return f"t{min(max(index, 0), bins - 1)}"


def _shrink(raw: float, size: int, value: float) -> float:
    """Pull a within-class standing toward the photo's OWN fused value, in
    proportion to how little the class has demonstrated.

    A class of one would otherwise be 'best of its kind' at 1.0, when all it has
    shown is that nothing else was measured that way -- which mid-scan is the
    common case and not a compliment.

    The prior is the photo's own value and NOT a flat 0.5, which is what this
    used to be. Rank alone carries no information about how good the class is,
    so with a flat prior the standing of a photo is decided by how many peers it
    was measured alongside: twelve mediocre photos measured one way (rank 1.0
    each, shrunk to 0.87) beat three superb photos measured another (rank 1.0
    each, shrunk to 0.71), purely because twelve is more than three. The album
    then fills with the mediocre class and every number in the report looks
    healthy. Anchoring on the value says the honest thing instead: when the class
    is too small to establish standing, fall back on what was actually measured.

    Rank still dominates once a class is real -- at size 12 the value moves the
    standing by at most 4/16 -- so "top 10% of the portraits" survives as the
    cross-class claim. `_STANDING_PRIOR` is how many peers a class must hold
    before its internal ordering outweighs the raw value; 4 is a judgement call,
    not a fitted number, and the eval harness is what should move it.
    """
    return (raw * size + value * _STANDING_PRIOR) / (size + _STANDING_PRIOR)


def _partition(candidates: Sequence[SelectionCandidate]) -> list[list[SelectionCandidate]]:
    """Group candidates into comparability classes, checking the derivation
    against `FusedScore.comparable_with` rather than trusting it.

    The key is built from the same three properties the method compares, but if
    fusion ever tightens that rule and this key does not follow, the failure is
    silent: a coarser partition ranks two photos against each other that fusion
    would have refused to order, and the album is subtly wrong with no error
    anywhere. So both directions are asserted -- everything sharing a key must
    be mutually comparable, and no two different keys may be.
    """
    grouped: dict[tuple[frozenset[str], str, str], list[SelectionCandidate]] = {}
    for candidate in candidates:
        grouped.setdefault(candidate.measurement_key(), []).append(candidate)

    # Sorted by a stringified key so class order -- and therefore nothing at
    # all -- depends on dict insertion or on frozenset hashing, which varies
    # between runs when PYTHONHASHSEED is not fixed.
    keys = sorted(grouped, key=lambda k: (sorted(k[0]), k[1], k[2]))
    # The member sort is REDUNDANT today -- `select` sorts before anything
    # iterates and grouping preserves that order, so this cannot change a list.
    # Kept because `_partition` is otherwise a standalone function whose output
    # order is load-bearing (it decides which media_id the drift errors name),
    # and a caller who forgets the outer sort should not get a different answer.
    # Deleting it is an equivalent mutation as long as that outer sort is there.
    classes = [sorted(grouped[key], key=lambda c: c.media_id) for key in keys]

    for members in classes:
        reference = members[0].score
        for member in members[1:]:
            if not reference.comparable_with(member.score):
                raise SelectionError(
                    f"{member.media_id} shares a measurement key with "
                    f"{members[0].media_id} but fusion says they are not comparable; "
                    "the class key has drifted from FusedScore.comparable_with"
                )
    for i, left in enumerate(classes):
        for right in classes[i + 1 :]:
            if left[0].score.comparable_with(right[0].score):
                raise SelectionError(
                    f"{left[0].media_id} and {right[0].media_id} landed in different "
                    "measurement classes but fusion says they are comparable; the "
                    "class key is finer than FusedScore.comparable_with"
                )
    return classes


def _similarity(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine similarity, via the ranking engine's distance so the two packages
    cannot disagree about what 'similar' means."""
    return 1.0 - cosine_distance(a, b)


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------


def select(
    candidates: Sequence[SelectionCandidate],
    target_count: int,
    *,
    policy: SelectionPolicy | None = None,
    required_person_ids: Sequence[str] = (),
    allow_mixed_measurements: bool = False,
) -> Selection:
    """Choose `target_count` photos from one event's candidates.

    `required_person_ids` is `AlbumSpec.event.person_ids` -- the people the
    album is *about*. They are floored even if they appear in only one photo;
    people merely present in the event are floored too, but a required person
    who cannot be covered at all is reported by name in `missing_person_ids`.

    `allow_mixed_measurements` abandons the comparability partition and ranks
    on the raw fused value. It exists for a scan-in-progress preview, where an
    approximate order is better than none, and it has to be asked for -- a
    default that produces a plausible wrong answer is worse than one that
    refuses, because only the refusal gets noticed.
    """
    policy = policy or DEFAULT_POLICY
    policy.validate()
    if target_count < 0:
        raise SelectionError(f"target_count={target_count} must be >= 0")

    ordered = sorted(candidates, key=lambda c: c.media_id)
    if len({c.media_id for c in ordered}) != len(ordered):
        raise SelectionError("duplicate media_id in candidates; media_id is a primary key")
    _validate_candidates(ordered)

    candidate_count = len(ordered)
    rejections: list[Rejection] = []

    survivors = _apply_hard_gates(ordered, policy, rejections)
    survivors, rescued = _apply_quality_floor(survivors, policy, rejections)

    by_id = {c.media_id: c for c in survivors}

    classes, standing = _rank_within_classes(survivors, allow_mixed_measurements)

    # The person universe comes from EVERY candidate, not just the survivors.
    # A person whose only photos were all eliminated or excluded would
    # otherwise never enter the set that `missing_person_ids` is measured
    # against, and the album would omit a child while reporting full coverage
    # -- the catastrophic failure, reported nowhere. Selection still only ever
    # draws from survivors; this set exists so the gap is visible.
    person_universe = sorted(
        {p for c in ordered for p in c.person_ids} | set(required_person_ids)
    )

    selected, cap_blocked = _greedy(
        survivors, by_id, standing, target_count, policy, person_universe
    )
    rejections.extend(
        Rejection(media_id=media_id, reason="diversity_constraint",
                  detail="per-person cap reached")
        for media_id in sorted(cap_blocked)
    )

    selected_set = set(selected)
    blocked = set(cap_blocked)
    unselected = tuple(
        c.media_id for c in survivors
        if c.media_id not in selected_set and c.media_id not in blocked
    )

    person_counts = _person_counts(selected, by_id)
    missing = tuple(
        person for person in person_universe
        if person_counts.get(person, 0) < policy.min_per_person
    )

    chronological = _chronological(selected, by_id)
    constraints = _measure_constraints(
        chronological, by_id, policy, person_counts, missing, target_count, survivors,
        _person_cap(survivors, target_count, policy),
    )

    return Selection(
        selected=chronological,
        by_standing=tuple(sorted(selected, key=lambda m: (-standing[m], m))),
        candidate_count=candidate_count,
        rejected=tuple(sorted(rejections, key=lambda r: r.media_id)),
        constraints=constraints,
        unselected=unselected,
        person_counts=person_counts,
        missing_person_ids=missing,
        rescued_media_ids=rescued,
        classes=classes,
    )


def _validate_candidates(candidates: Sequence[SelectionCandidate]) -> None:
    """Reject inputs that would corrupt the selection rather than worsen it."""
    dimension: int | None = None
    space: str | None = None
    space_owner: str | None = None
    for candidate in candidates:
        value = candidate.score.value
        if not math.isfinite(value):
            # NaN passes `value < floor` AND `value > floor`, so a NaN score
            # sails through the quality gate and then makes every gain
            # comparison meaningless -- the album silently reorders per run.
            raise SelectionError(
                f"{candidate.media_id}: score.value={value!r} is not finite. NaN "
                "compares False to every threshold, so it would pass the quality "
                "floor untouched and make the greedy ordering unstable."
            )
        if not 0.0 <= value <= 1.0:
            raise SelectionError(
                f"{candidate.media_id}: score.value={value} is outside [0,1]"
            )
        if len(set(candidate.person_ids)) != len(candidate.person_ids):
            raise SelectionError(
                f"{candidate.media_id}: repeated person id in {candidate.person_ids!r}; "
                "a person counted twice inflates that person's cap usage"
            )
        if candidate.embedding is not None:
            if not candidate.embedding:
                raise SelectionError(
                    f"{candidate.media_id}: embedding is empty. A zero-length vector "
                    "has no direction, so 'how similar is this to what we already "
                    "picked' has no answer and the redundancy term is undefined."
                )
            if not all(math.isfinite(x) for x in candidate.embedding):
                # A single NaN component makes every dot product and every norm
                # NaN, so `_similarity` returns NaN -- and `NaN > closest` is
                # False, so the running maximum is never updated. The photo does
                # not error; it becomes INVISIBLE to the redundancy penalty and
                # its near-twins sail in beside it. Nothing anywhere says so.
                raise SelectionError(
                    f"{candidate.media_id}: embedding has a non-finite component. "
                    "NaN compares False to the running similarity maximum, so the "
                    "redundancy penalty would silently never fire for this photo."
                )
            if not any(x != 0.0 for x in candidate.embedding):
                # cosine_distance divides by the norm and raises a bare
                # ValueError from inside the ranking engine when it is zero --
                # mid-greedy, on whichever pair happened to be compared first,
                # with no media_id in the message. Caught here, where the input
                # that caused it is named.
                raise SelectionError(
                    f"{candidate.media_id}: embedding is all zeros. It has no "
                    "direction, so cosine similarity against it is undefined; "
                    "an unembedded photo must pass embedding=None, not a zero vector."
                )
            if space_owner is None:
                space, space_owner = candidate.embedding_space, candidate.media_id
            elif candidate.embedding_space != space:
                # Includes the None-vs-named case on purpose. An unlabelled
                # embedding is not KNOWN to share a space with a labelled one,
                # and "probably the same model" is precisely the assumption that
                # makes a silently meaningless similarity.
                raise SelectionError(
                    f"{candidate.media_id}: embedding_space="
                    f"{candidate.embedding_space!r} but {space_owner} declared "
                    f"{space!r}; cosine similarity between two vector spaces is not "
                    "a similarity. Re-embed one side or drop the embeddings."
                )
            if dimension is None:
                dimension = len(candidate.embedding)
            elif len(candidate.embedding) != dimension:
                # Mid-greedy this raises from inside cosine_distance on
                # whichever pair happens to be compared first, which is a
                # confusing failure far from the cause.
                raise SelectionError(
                    f"{candidate.media_id}: embedding has {len(candidate.embedding)} "
                    f"dimensions, expected {dimension}; embeddings from two different "
                    "spaces cannot be compared"
                )
        # Parsed here so a malformed or naive timestamp fails before any
        # selection work, not halfway through the greedy.
        _epoch_seconds(candidate)


def _apply_hard_gates(
    candidates: Sequence[SelectionCandidate],
    policy: SelectionPolicy,
    rejections: list[Rejection],
) -> list[SelectionCandidate]:
    """Everything that removes a photo for a stated rule rather than on merit.

    Precedence is fixed so two hosts report the same reason for a photo that
    fails several gates at once, and each media_id appears at most once.
    """
    survivors: list[SelectionCandidate] = []
    for candidate in candidates:
        forced_in = candidate.user_override is True

        if candidate.user_hidden and not forced_in:
            rejections.append(Rejection(candidate.media_id, "user_hidden"))
            continue
        if candidate.user_override is False:
            rejections.append(
                Rejection(candidate.media_id, "user_hidden", "user forced this out")
            )
            continue
        if candidate.excluded_from_automation and not forced_in:
            reasons = [
                _EXCLUSION_TO_REASON[r]
                for r in candidate.exclusion_reasons
                if r in _EXCLUSION_TO_REASON
            ]
            # An unmapped or absent reason must not become a default: falling
            # back to a specific reason would put a wrong explanation in front
            # of the user, and falling back to "include" would print an
            # excluded photo.
            reason = reasons[0] if reasons else "excluded_content"
            rejections.append(
                Rejection(candidate.media_id, reason,
                          ",".join(candidate.exclusion_reasons))
            )
            continue
        if candidate.dedupe_group_id is not None and not candidate.is_dedupe_primary:
            # The group's primary already represents this photo. Selecting both
            # puts two frames of one moment on facing pages.
            rejections.append(
                Rejection(candidate.media_id, "near_duplicate",
                          f"secondary of group {candidate.dedupe_group_id}")
            )
            continue
        if candidate.score.rejected:
            # Eliminated by fusion: not a weak photo, not an image. Never
            # rescued by scarcity -- see the module docstring.
            rejections.append(
                Rejection(candidate.media_id, "below_quality_floor",
                          candidate.score.rejection_reason or "eliminated")
            )
            continue
        if policy.min_long_edge_px is not None:
            # ARMED gate. Unknown pixel size fails it. Requiring two non-null
            # values before a comparison can fire is how a gate ends up
            # admitting exactly the unverified input it was installed to stop.
            if candidate.long_edge_px is None:
                rejections.append(
                    Rejection(candidate.media_id, "dpi_too_low",
                              "pixel dimensions unknown")
                )
                continue
            if candidate.long_edge_px < policy.min_long_edge_px:
                rejections.append(
                    Rejection(candidate.media_id, "dpi_too_low",
                              f"{candidate.long_edge_px}px < {policy.min_long_edge_px}px")
                )
                continue
        survivors.append(candidate)
    return survivors


def _apply_quality_floor(
    survivors: Sequence[SelectionCandidate],
    policy: SelectionPolicy,
    rejections: list[Rejection],
) -> tuple[list[SelectionCandidate], tuple[str, ...]]:
    """The absolute floors, waived for irreplaceable subjects.

    Two floors, one waiver. `quality_floor` is the fused-score floor;
    `face_sharpness_floor` rejects a photo whose measured FACE is out of focus
    -- measured on the face region precisely because global sharpness flags
    shallow-depth-of-field portraits, the best photos in a library. A photo
    with no face-sharpness measurement passes this floor: absence of the
    measure is not evidence of blur (the gate that turns absence into a block
    is the safety gate's job, not a quality heuristic's).

    A person with no photo passing both floors is 'scarce', and their single
    best photo survives the cut however poor it is. Only one photo per scarce
    person is rescued: the point is that grandmother appears, not that a bad
    run of photos of grandmother fills the book.
    """
    def failure(candidate: SelectionCandidate) -> str | None:
        if candidate.score.value < policy.quality_floor:
            return f"{candidate.score.value:.3f} < {policy.quality_floor:.3f}"
        if (
            candidate.face_sharpness is not None
            and candidate.face_sharpness < policy.face_sharpness_floor
        ):
            return (
                f"face sharpness {candidate.face_sharpness:.3f} < "
                f"{policy.face_sharpness_floor:.3f}: the face is out of focus"
            )
        if (
            candidate.head_sharpness is not None
            and candidate.head_sharpness < policy.head_sharpness_floor
        ):
            return (
                f"head sharpness {candidate.head_sharpness:.3f} < "
                f"{policy.head_sharpness_floor:.3f}: the subject was moving "
                "(smeared hair or shoulders around a face)"
            )
        if policy.reject_cut_faces and candidate.face_cut:
            return "a significant face is cropped by the frame edge"
        return None

    verdicts = {c.media_id: failure(c) for c in survivors}
    covered = {
        person
        for candidate in survivors
        if verdicts[candidate.media_id] is None
        for person in candidate.person_ids
    }

    best_for_person: dict[str, tuple[float, str]] = {}
    for candidate in survivors:  # already media_id-sorted
        for person in sorted(candidate.person_ids):
            if person in covered:
                continue
            current = best_for_person.get(person)
            # Explicit tiebreak on media_id: two equally-scored photos of the
            # same person must rescue the same one on every machine.
            challenger = (-candidate.score.value, candidate.media_id)
            if current is None or challenger < (-current[0], current[1]):
                best_for_person[person] = (candidate.score.value, candidate.media_id)

    rescued = {media_id for _, media_id in best_for_person.values()}

    kept: list[SelectionCandidate] = []
    for candidate in survivors:
        reason = verdicts[candidate.media_id]
        if reason is not None and candidate.media_id not in rescued:
            rejections.append(
                Rejection(candidate.media_id, "below_quality_floor", reason)
            )
            continue
        kept.append(candidate)

    return kept, tuple(sorted(rescued))


def _rank_within_classes(
    survivors: Sequence[SelectionCandidate], allow_mixed: bool
) -> tuple[tuple[MeasurementClass, ...], dict[str, float]]:
    """Standing in (0,1] for every survivor, comparable across classes."""
    if allow_mixed:
        # Preview mode: the raw value, explicitly asked for.
        return (), {c.media_id: c.score.value for c in survivors}

    standing: dict[str, float] = {}
    classes: list[MeasurementClass] = []
    for members in _partition(survivors):
        scores = {c.media_id: c.score for c in members}
        # fusion.rank does the comparability check and the ordering, including
        # the media_id tiebreak. Reused rather than reimplemented so selection
        # and dedupe can never disagree about which of two equal photos wins.
        order = rank(scores, allow_mixed=False)

        # Standing is computed from the VALUE, not from the position in that
        # order, and equal values share a standing. Positional standing looks
        # equivalent and is not: thirty photos tied at 0.85 occupy positions 0
        # through 29 and would receive standings from 0.96 down to 0.40 purely
        # by media_id. The quality term would then be dominated by an arbitrary
        # hash ordering, every coverage term would be drowned out by it, and
        # nothing would be visibly wrong -- the album would just quietly be the
        # alphabetically-first photos of the busiest day.
        values = sorted(c.score.value for c in members)
        size = len(values)
        for candidate in members:
            below = bisect_left(values, candidate.score.value)
            equal = bisect_right(values, candidate.score.value) - below
            standing[candidate.media_id] = _quantise(
                _shrink((below + equal) / size, size, candidate.score.value)
            )

        reference = members[0].score
        classes.append(
            MeasurementClass(
                measured=reference.measured,
                weights_digest=reference.weights_digest,
                feature_set_id=reference.feature_set_id,
                ranked_media_ids=tuple(order),
            )
        )
    return tuple(classes), standing


def _shot_groups(
    survivors: Sequence[SelectionCandidate], policy: SelectionPolicy
) -> dict[str, str]:
    """media_id -> shot-group id. One group = one photograph taken repeatedly.

    Two photos join a group when they were captured within `burst_window_s` of
    each other AND their embeddings agree at `shot_similarity` or above.
    Membership is transitive (union-find), because a 40-frame burst drifts:
    frame 1 and frame 40 may sit below the threshold while every adjacent pair
    is above it, and they are still the same photograph being taken.

    Photos with no embedding or no usable capture time are singleton groups --
    absence of evidence must not merge anything.

    Deterministic: candidates are walked in (time, media_id) order and each is
    compared against a bounded window of predecessors, so the grouping does
    not depend on dict order or float summation order.
    """
    parent: dict[str, str] = {c.media_id: c.media_id for c in survivors}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            # Smaller id wins, so the group id is stable however links arrive.
            parent[max(ra, rb)] = min(ra, rb)

    timed = sorted(
        (
            (seconds, c.media_id, c)
            for c in survivors
            if (seconds := _epoch_seconds(c)) is not None and c.embedding is not None
        ),
        key=lambda entry: (entry[0], entry[1]),
    )
    # ponytail: bounded lookback (16 predecessors) instead of all pairs in the
    # window; adjacent frames of a burst are always neighbours in time order,
    # and the union is transitive, so a longer chain still merges.
    for index, (seconds, media_id, candidate) in enumerate(timed):
        for prev_seconds, prev_id, prev in reversed(timed[max(0, index - 16):index]):
            if seconds - prev_seconds > policy.burst_window_s:
                break
            if find(prev_id) == find(media_id):
                continue
            if _similarity(candidate.embedding, prev.embedding) >= policy.shot_similarity:
                union(media_id, prev_id)
    return {media_id: find(media_id) for media_id in parent}


def _calibrated_redundancy(
    survivors: Sequence[SelectionCandidate], policy: SelectionPolicy
) -> tuple[float, float]:
    """(free_similarity, slope_denominator) for the redundancy penalty.

    The free zone is the similarity of two photos that are simply DIFFERENT
    pictures -- and that number belongs to the library, not to the policy. A
    varied travel library measures ~0.4 between unrelated photos; a one-baby
    one-home library measures ~0.74 between photos taken days apart. A fixed
    threshold that is right for one is a constant offset in the other, and a
    constant offset discriminates nothing.

    So: free = max(policy floor, median similarity of a deterministic sample
    of candidate pairs), and the penalty runs linearly from 0 at `free` to the
    full `weight_redundancy` at `shot_similarity` -- "as similar as a burst
    frame" costs the whole weight, "as similar as two photos of the same life"
    costs nothing.
    """
    embedded = sorted(
        (c for c in survivors if c.embedding is not None), key=lambda c: c.media_id
    )
    free = policy.redundancy_free_similarity
    if len(embedded) >= 8:
        # Thin to at most 48 candidates (every k-th in media_id order), then
        # take all pairs: <= 1128 similarities, O(1) in library size.
        stride = max(1, len(embedded) // 48)
        sample = embedded[::stride][:48]
        sims = sorted(
            _similarity(a.embedding, b.embedding)
            for i, a in enumerate(sample)
            for b in sample[i + 1:]
        )
        median = sims[len(sims) // 2]
        if math.isfinite(median):
            free = max(free, min(median, policy.shot_similarity))
    denominator = max(policy.shot_similarity - free, 0.02)
    return free, denominator


def _greedy(
    survivors: Sequence[SelectionCandidate],
    by_id: Mapping[str, SelectionCandidate],
    standing: Mapping[str, float],
    target_count: int,
    policy: SelectionPolicy,
    person_universe: Sequence[str],
) -> tuple[list[str], list[str]]:
    """People floor first, then marginal-gain fill. Returns (selected,
    blocked-by-cap)."""
    if target_count == 0 or not survivors:
        return [], []

    times = {c.media_id: _epoch_seconds(c) for c in survivors}
    known = sorted(t for t in times.values() if t is not None)
    start = known[0] if known else 0.0
    span = (known[-1] - known[0]) if known else 0.0
    bins = policy.time_bins or max(1, min(target_count, policy.max_time_bins))

    time_key = {
        media_id: _time_bucket(seconds, start, span, bins)
        for media_id, seconds in times.items()
    }
    place_key = {c.media_id: c.place_key or _UNKNOWN for c in survivors}
    # Scene composed with a crowd bucket (solo / duo / group / no-people):
    # "different TYPES of photos" is part of what a varied album means, and on
    # a library where scene_type is uniformly unknown the crowd bucket is the
    # type signal that remains.
    scene_key = {
        c.media_id:
            f"{c.scene_type or _UNKNOWN}|faces:{min(len(c.person_ids), 3)}"
        for c in survivors
    }
    # Sorted once here rather than inside `gain`, which runs O(n*k) times. The
    # sort is not cosmetic: the person term sums floats, float addition is not
    # associative, and summing in tuple order would make the gain depend on
    # whatever order the face pipeline happened to emit ids in.
    people_key = {
        c.media_id: tuple(sorted(c.person_ids)) or (_NO_PEOPLE,) for c in survivors
    }

    time_counts: dict[str, int] = {}
    place_counts: dict[str, int] = {}
    scene_counts: dict[str, int] = {}
    person_counts: dict[str, int] = {}

    selected: list[str] = []
    remaining = [c.media_id for c in survivors]  # media_id-sorted already

    # Similarity to the closest ALREADY-SELECTED photo, maintained
    # incrementally. The redundancy penalty is a max over the selected set;
    # recomputing it inside every gain call costs O(k) per candidate per round,
    # which is O(n*k^2) overall and takes ten seconds for the 4000 -> 40 case
    # this module exists to serve. A running max is exactly equal -- max over a
    # set built one element at a time is the same number -- and costs one pass
    # per commit instead.
    # Floored at 0.0 rather than -inf: `redundancy_free_similarity` is
    # validated into [0,1], so a negative running max and a zero one produce
    # the identical `max(0, closest - free)` penalty. That equivalence is what
    # the floor rests on, and it stops holding if the free similarity is ever
    # allowed to go negative.
    closest: dict[str, float] = {}

    cap = _person_cap(survivors, target_count, policy)
    shot_of = _shot_groups(survivors, policy)
    shot_counts: dict[str, int] = {}

    worse_version = _dominated_within_shot(survivors, shot_of, policy)
    redundancy_free, redundancy_denom = _calibrated_redundancy(survivors, policy)

    # Expression axes as percentiles WITHIN this pool (the absolute zero-shot
    # values are uncalibrated; the ordering over one library is the signal).
    smile_pct = _axis_percentiles(survivors, lambda c: c.smile)
    aesthetic_pct = _axis_percentiles(survivors, lambda c: c.aesthetic)
    awake_pct = _axis_percentiles(survivors, lambda c: c.awake)
    sleeping_pct = _axis_percentiles(survivors, lambda c: c.sleeping)
    composed_pct = _axis_percentiles(survivors, lambda c: c.composed)
    # Eyes read shut. A sleeping shot is the cherished kind (capped below);
    # anything else with shut eyes -- a blink, a grimace -- is penalised hard.
    # Typed by comparing the two contrasts on the SAME image, not by pool
    # percentile: percentiles break when half the library is naps (someone is
    # always top-quartile), while cross-axis comparison cancels the modality
    # gap and asks only which direction this one image leans.
    is_sleeping = {
        c.media_id: (
            c.sleeping is not None
            and c.awake is not None
            and c.sleeping > c.awake
            # ...and the sleeping direction decisively beats ITS negatives: a
            # mid-blink frame also drags `awake` under `sleeping` (both
            # negative) and unrelated content wobbles within +/-0.035 of
            # zero; neither positively resembles a sleeping shot.
            and c.sleeping > policy.sleeping_min_contrast
        )
        for c in survivors
    }
    is_mid_blink = {
        c.media_id: (
            not is_sleeping[c.media_id]
            # Directional evidence of shut eyes AND bottom-quartile standing on
            # the axis: in an all-open-eyes library somebody is always bottom
            # quartile, and in a genuinely mixed one a small positive contrast
            # is still open eyes. Both conditions, or no penalty.
            and c.awake is not None
            and c.awake < 0.0
            and awake_pct[c.media_id] < 0.25
        )
        for c in survivors
    }
    sleeping_cap = max(1, int(target_count * policy.max_sleeping_fraction))
    sleeping_count = 0

    def commit(media_id: str) -> None:
        nonlocal sleeping_count
        candidate = by_id[media_id]
        selected.append(media_id)
        remaining.remove(media_id)
        if is_sleeping[media_id]:
            sleeping_count += 1
        shot_counts[shot_of[media_id]] = shot_counts.get(shot_of[media_id], 0) + 1
        time_counts[time_key[media_id]] = time_counts.get(time_key[media_id], 0) + 1
        place_counts[place_key[media_id]] = place_counts.get(place_key[media_id], 0) + 1
        scene_counts[scene_key[media_id]] = scene_counts.get(scene_key[media_id], 0) + 1
        for person in candidate.person_ids or (_NO_PEOPLE,):
            person_counts[person] = person_counts.get(person, 0) + 1
        if candidate.embedding is not None:
            for other in remaining:
                embedding = by_id[other].embedding
                if embedding is not None:
                    similarity = _similarity(embedding, candidate.embedding)
                    if similarity > closest.get(other, 0.0):
                        closest[other] = similarity

    def gain(media_id: str) -> float:
        candidate = by_id[media_id]
        value = policy.weight_quality * standing[media_id]
        value += policy.weight_time * _bucket_gain(time_counts.get(time_key[media_id], 0))
        value += policy.weight_place * _bucket_gain(place_counts.get(place_key[media_id], 0))
        value += policy.weight_scene * _bucket_gain(scene_counts.get(scene_key[media_id], 0))

        people = people_key[media_id]
        # Mean, not sum: a group shot of five already-covered people must not
        # out-earn a portrait of an uncovered one simply by containing more
        # faces. Coverage of NEW people is the floor phase's job.
        value += policy.weight_person * (
            sum(_bucket_gain(person_counts.get(p, 0)) for p in people) / len(people)
        )

        if candidate.embedding is not None:
            # Linear from 0 at the calibrated free zone to the full weight at
            # the shot boundary. Clamped: beyond the shot boundary the hard
            # per-shot cap is the enforcement, not a larger number here.
            value -= policy.weight_redundancy * min(
                1.0,
                max(0.0, closest.get(media_id, 0.0) - redundancy_free)
                / redundancy_denom,
            )

        # Expression: a smile out-earns a cry, a composed frame out-earns a
        # mid-gesture candid, and eyes shut outside a sleeping shot costs more
        # than either bonus can pay.
        value += policy.weight_smile * smile_pct[media_id]
        value += policy.weight_composed * composed_pct[media_id]
        value += policy.weight_aesthetic * aesthetic_pct[media_id]
        if is_mid_blink[media_id]:
            value -= policy.mid_blink_penalty
        return _quantise(value)

    # --- phase 1: people floor -------------------------------------------
    # Greedy maximum coverage. Runs before quality has a vote because omitting
    # a person is not a trade the objective is allowed to make.
    #
    # The per-person CAP is deliberately not enforced here. A group shot is the
    # cheapest way to cover three uncovered people and it may push a fourth,
    # already well-represented person over their share; refusing it to protect
    # the cap would drop a person from the album to keep a ratio tidy, which is
    # the wrong way round. The cap governs phase 2, and the report measures
    # what actually happened rather than what was intended.
    if policy.min_per_person > 0:
        while len(selected) < target_count:
            uncovered = {
                p for p in person_universe
                if person_counts.get(p, 0) < policy.min_per_person
            }
            if not uncovered:
                break
            best: tuple[int, float, str] | None = None
            for media_id in remaining:
                new_people = len(uncovered & set(by_id[media_id].person_ids))
                if new_people == 0:
                    continue
                # More new people first, then higher gain, then media_id. The
                # negations put the max at the front of a min-comparison, which
                # keeps the tiebreak a plain ascending string compare.
                key = (-new_people, -gain(media_id), media_id)
                if best is None or key < best:
                    best = key
            if best is None:
                break  # nobody left who covers anyone; reported as missing
            commit(best[2])

    # --- phase 2: marginal-gain fill --------------------------------------
    non_people_available = sum(1 for c in survivors if not c.person_ids)
    reserve = min(
        int(target_count * policy.min_non_people_fraction),
        non_people_available,
        target_count,
    )
    blocked: set[str] = set()

    # One photo per shot group, until every group is spent. Only when the
    # album still has empty slots and every distinct shot is already in does a
    # second frame of a burst become eligible -- and then a third, and so on.
    # A relaxation round, not a weight: 24 slots against 4 shot groups must
    # come back 4-then-deepen, never 24 frames of the best burst.
    allowed_per_shot = 1

    while len(selected) < target_count and remaining:
        slots_left = target_count - len(selected)
        non_people_selected = sum(1 for m in selected if not by_id[m].person_ids)
        still_needed = reserve - non_people_selected
        # `>=`, not `>`: when the remaining slots exactly equal the outstanding
        # reservation, every one of them is already spoken for.
        reserved_now = still_needed >= slots_left

        eligible: list[str] = []
        for media_id in remaining:
            candidate = by_id[media_id]
            at_cap = bool(candidate.person_ids) and any(
                person_counts.get(p, 0) >= cap for p in candidate.person_ids
            )
            if at_cap:
                # "Cut by the cap" means one thing only: this photo was denied a
                # chance to compete IN A ROUND THAT STILL HAD A SLOT TO GIVE.
                # Recorded here, as it happens, rather than inferred afterwards
                # from the final person counts -- an after-the-fact sweep cannot
                # tell "Ava already had five and this was her sixth-best" from
                # "the album filled up and Ava happened to end on five", and it
                # labels both as a diversity decision. The second is a lie the
                # user cannot argue with: they go looking for a cap to raise and
                # there is nothing there. Everything else falls to `unselected`.
                #
                # Person counts only ever increase, so a photo recorded here can
                # never become eligible again in a later round.
                blocked.add(media_id)
            if reserved_now and candidate.person_ids:
                continue
            if at_cap:
                continue
            if is_sleeping[media_id] and sleeping_count >= sleeping_cap:
                # A cap, not a rejection: sleeping-baby photos are a cherished
                # type, but a book that is a third closed eyes is a nap log.
                continue
            if worse_version[media_id]:
                continue
            eligible.append(media_id)

        if not eligible:
            # Only the cap is standing between us and a full album. Stop rather
            # than quietly overfilling one person's share; the shortfall is
            # measured and reported by `_measure_constraints`.
            break

        fresh_shots = [
            m for m in eligible
            if shot_counts.get(shot_of[m], 0) < allowed_per_shot
        ]
        if not fresh_shots:
            # Every eligible photo is another frame of a shot the album
            # already has. Deepen the whole album by one frame per shot
            # rather than stopping short -- the user asked for N pages.
            allowed_per_shot += 1
            continue
        # The distinctness backstop: `closest` already tracks each photo's
        # similarity to the nearest selected one. Prefer photos below the
        # cap; fall back to the full list only when nothing distinct is
        # left, because a shorter, varied book was judged better than a
        # padded one -- but an EMPTY round is worse than either.
        distinct = [
            m for m in fresh_shots
            if closest.get(m, 0.0) < policy.max_selected_similarity
        ]
        if distinct:
            best_id = min(distinct, key=lambda m: (-gain(m), m))
        else:
            # Nothing distinct remains -- a studio session's 279 frames can
            # genuinely be 16 poses, and slots 17..25 must repeat one. Take
            # the LEAST-similar repeat, not the highest-scoring one: gain
            # favours the twin of the best pose (a 0.99 clone), which is
            # exactly the pair the album must never show. Similarity is
            # rounded so a 0.001 difference cannot outvote real quality.
            best_id = min(
                fresh_shots,
                key=lambda m: (round(closest.get(m, 0.0), 2), -gain(m), m),
            )
        commit(best_id)

    return selected, sorted(blocked)


#: The margins at which one frame of a shot BEATS another on a signal.
#: Within a shot group the frames are the same picture except for defects --
#: same people, same light, same set -- so raw differences compare like with
#: like and each margin is "a difference a person would see on the page":
#: an assistant in the frame, a blink, visibly worse focus on someone's face.
#: clean_frame's margin lives on the policy (it was calibrated on a real
#: contaminated take, 0.020 under its clean twins); the rest are fixed here.
_VERSION_SIGNAL_MARGINS: "tuple[tuple[str, float], ...]" = (
    ("awake", 0.02),        # someone is mid-blink in this variant
    ("smile", 0.025),       # clearly worse expressions, same instant
    ("aesthetic", 0.02),    # worse light or framing of the same scene
    ("face_sharpness", 0.15),  # a face visibly softer than its twin
)
_VERSION_VALUE_MARGIN = 0.05  # decisively out-scored overall


def _dominated_within_shot(
    survivors: Sequence[SelectionCandidate],
    shot_of: Mapping[str, str],
    policy: SelectionPolicy,
) -> dict[str, bool]:
    """media_id -> True when a group-mate is simply the better version of it.

    "Is there a better version of the same picture?" as Pareto dominance
    with perceptual margins: frame X is a worse version of frame Y (same
    shot group) when Y beats X by the margin on at least one signal and X
    beats Y on none. Two frames that trade wins -- best smile against best
    focus -- are genuine alternatives and BOTH stay eligible; the greedy's
    objective picks between them. Only the frame that loses somewhere and
    wins nowhere is excluded, which also guarantees every group keeps at
    least one eligible frame (domination is a strict partial order).

    Hard exclusion, not a penalty: the better version is itself a survivor
    in the same group, so nothing the group can offer is lost.
    """
    signals: list[tuple[Callable[[SelectionCandidate], float | None], float]] = [
        (lambda c: c.clean_frame, policy.clean_frame_group_margin),
    ]
    signals.extend(
        ((lambda c, _n=name: getattr(c, _n)), margin)
        for name, margin in _VERSION_SIGNAL_MARGINS
    )
    signals.append((lambda c: c.score.value, _VERSION_VALUE_MARGIN))

    def beats(better: SelectionCandidate, worse: SelectionCandidate) -> bool:
        won = False
        for getter, margin in signals:
            a, b = getter(better), getter(worse)
            if a is None or b is None:
                continue
            if b - a > margin:
                return False  # `worse` wins somewhere: no domination
            if a - b > margin:
                won = True
        return won

    groups: dict[str, list[SelectionCandidate]] = {}
    for candidate in survivors:
        groups.setdefault(shot_of[candidate.media_id], []).append(candidate)

    dominated = {c.media_id: False for c in survivors}
    for members in groups.values():
        if len(members) < 2:
            continue
        for candidate in members:
            for other in members:
                if other is not candidate and beats(other, candidate):
                    dominated[candidate.media_id] = True
                    break
    return dominated


def _axis_percentiles(
    survivors: Sequence[SelectionCandidate],
    getter: "Callable[[SelectionCandidate], float | None]",
) -> dict[str, float]:
    """Each candidate's rank on one expression axis, as a fraction in [0,1].

    Candidates without a value sit at a NEUTRAL 0.5 -- absence of the measure
    must neither reward nor punish. Equal values share a percentile (the same
    argument as value-based standing in `_rank_within_classes`: positional
    ranks would order ties by media_id and let a hash decide the album).
    """
    measured = sorted(
        value for c in survivors if (value := getter(c)) is not None
    )
    size = len(measured)
    result: dict[str, float] = {}
    for candidate in survivors:
        value = getter(candidate)
        if value is None or size == 0:
            result[candidate.media_id] = 0.5
            continue
        below = bisect_left(measured, value)
        equal = bisect_right(measured, value) - below
        result[candidate.media_id] = _quantise((below + equal / 2.0) / size)
    return result


def _person_cap(
    survivors: Sequence[SelectionCandidate], target_count: int, policy: SelectionPolicy
) -> int:
    """How many photos one person may occupy.

    Keys on people who are actually SELECTABLE, not on the reporting universe:
    a second person whose every photo was excluded leaves the album nobody to
    share with, and capping the one remaining person at half the book would
    return a half-length album and call it diversity. An event with one person
    or none is legitimately all that person.

    Lives in one function because the greedy enforces this number and the
    report measures against it. Computed twice, the two copies drift, and the
    report then certifies a cap the selection never applied.
    """
    persons = {p for c in survivors for p in c.person_ids}
    if len(persons) <= 1:
        return target_count
    return max(policy.min_per_person, math.ceil(target_count * policy.max_per_person_fraction))


def _person_counts(
    selected: Iterable[str], by_id: Mapping[str, SelectionCandidate]
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for media_id in selected:
        for person in by_id[media_id].person_ids:
            counts[person] = counts.get(person, 0) + 1
    return dict(sorted(counts.items()))


def _chronological(
    selected: Sequence[str], by_id: Mapping[str, SelectionCandidate]
) -> tuple[str, ...]:
    """Album order: the trip as it happened.

    Photos with no usable capture time sort LAST, not to the epoch. The
    contract is explicit that an 'unknown' precision must be excluded from
    chronology-ordered output rather than sorted to zero, and an undated
    WhatsApp forward opening a holiday album is exactly the visible symptom.
    """
    def key(media_id: str) -> tuple[int, float, str]:
        seconds = _epoch_seconds(by_id[media_id])
        return (1, 0.0, media_id) if seconds is None else (0, seconds, media_id)

    return tuple(sorted(selected, key=key))


def _measure_constraints(
    selected: Sequence[str],
    by_id: Mapping[str, SelectionCandidate],
    policy: SelectionPolicy,
    person_counts: Mapping[str, int],
    missing: Sequence[str],
    target_count: int,
    survivors: Sequence[SelectionCandidate],
    cap: int,
) -> tuple[ConstraintResult, ...]:
    """Every constraint MEASURED on the final selection.

    Nothing here is asserted from the fact that the algorithm intended it. A
    report that says `satisfied: true` because the code that was supposed to
    enforce it ran is worth nothing -- it is precisely the failure mode where a
    broken selector still produces a clean-looking spec.
    """
    chosen = [by_id[m] for m in selected]

    groups = [c.dedupe_group_id for c in chosen if c.dedupe_group_id is not None]
    duplicate_groups = sorted({g for g in groups if groups.count(g) > 1})

    if not person_counts:
        cap_ok, cap_detail = True, "no people in selection"
    else:
        busiest = max(person_counts.values())
        over = sorted(p for p, n in person_counts.items() if n > cap)
        cap_ok = not over
        cap_detail = f"max {busiest} of {target_count} for one person, cap {cap}"
    # The per-person FLOOR rides on this constraint because the schema's enum
    # has no slot of its own for it, and this is the one entry that is about
    # per-person counts. Naming stretch, recorded rather than hidden: a missing
    # person is the catastrophic failure and must not be reported nowhere.
    if missing:
        cap_ok = False
        cap_detail += f"; NOT COVERED: {', '.join(missing)}"

    # A SHORT album with photos still on the table is the cap's other failure
    # mode, and it used to be reported nowhere: the greedy stopped because every
    # remaining candidate was of a person already at their share, the book came
    # back with 34 of 40 pages, and every constraint said `satisfied`. "Why is my
    # album short" then has no answer anywhere in the spec.
    #
    # Measured, not flagged by the greedy: the condition IS that the album is
    # short, photos remain, and every one of them is of a capped person. Reading
    # it off the outcome keeps this function's promise that nothing here is
    # asserted from the fact that some code ran. Running out of candidates
    # entirely is a different thing and is deliberately not reported here.
    chosen_ids = {c.media_id for c in chosen}
    left_over = [c for c in survivors if c.media_id not in chosen_ids]
    short_by = target_count - len(chosen)
    if (
        short_by > 0
        and left_over
        and all(
            c.person_ids and any(person_counts.get(p, 0) >= cap for p in c.person_ids)
            for c in left_over
        )
    ):
        cap_ok = False
        cap_detail += (
            f"; SHORT BY {short_by}: all {len(left_over)} remaining photos are of a "
            f"person already at the cap of {cap}"
        )

    repeats = [
        chosen[i].media_id
        for i in range(1, len(chosen))
        # Two unknowns are not evidence of repetition -- reporting a violation
        # for a library that has not been scene-tagged yet would make this flag
        # meaningless.
        if chosen[i].scene_type is not None
        and chosen[i].scene_type == chosen[i - 1].scene_type
    ]

    non_people = sum(1 for c in chosen if not c.person_ids)
    available_non_people = sum(1 for c in survivors if not c.person_ids)
    wanted = min(int(target_count * policy.min_non_people_fraction), available_non_people)

    # Honest about what this one is: `selected` arrives already sorted by
    # `_chronological`, so this cannot fail unless `_chronological` and this
    # function disagree about the order. It is a self-check on that sort, not an
    # independent finding, and it is worth keeping only because it does fail
    # when the sort breaks -- an implementation that dropped the undated-last
    # rule, or keyed on media_id, flips this to False. `test_selection` pins
    # exactly that by substituting a broken `_chronological`; without such a
    # test the flag would be unfalsifiable and therefore worthless.
    times = [_epoch_seconds(c) for c in chosen]
    dated = [t for t in times if t is not None]
    undated_positions = [i for i, t in enumerate(times) if t is None]
    in_order = all(dated[i] <= dated[i + 1] for i in range(len(dated) - 1)) and all(
        position >= len(dated) for position in undated_positions
    )

    best = max((c.score.value for c in chosen), default=0.0)

    return (
        ConstraintResult(
            "no_near_duplicates_on_spread",
            not duplicate_groups,
            "no two selections share a dedupe group"
            if not duplicate_groups
            else f"shared groups: {', '.join(duplicate_groups)}",
        ),
        ConstraintResult("max_per_person_per_section", cap_ok, cap_detail),
        ConstraintResult(
            "chronological_within_section",
            in_order,
            f"{len(dated)} dated, {len(undated_positions)} undated placed last",
        ),
        ConstraintResult(
            "no_consecutive_same_scene",
            not repeats,
            "no adjacent pair shares a known scene type"
            if not repeats
            else f"{len(repeats)} adjacent repeats",
        ),
        ConstraintResult(
            "people_scenery_detail_balance",
            non_people >= wanted,
            f"{non_people} non-people of {len(chosen)}, wanted {wanted}",
        ),
        ConstraintResult(
            "min_hero_quality",
            best >= policy.hero_quality,
            f"best selected {best:.3f} vs floor {policy.hero_quality:.3f}",
        ),
    )
