"""The print validator: the last gate between a bug and a physical object.

`workers/render-print` refuses to export a PDF whose AlbumSpec does not carry a
passing `PrintValidationReport`, and AGENTS.md says there is no override flag.
Everything downstream of this module is irreversible -- a print defect is a
refund, an apology, and a book that is already in the post.

THE DESIGN RULE THAT SHAPES EVERY FUNCTION HERE

    A check that cannot run FAILS. It never skips, and it never passes.

That sounds obvious and is the single most expensive mistake this codebase has
already made once: a model load gate that permitted weights whose hash had never
been computed, because the comparison it performed required two non-null values
and quietly did nothing when it only had one. The same shape hides everywhere in
a validator -- an unknown source resolution, a null `face_safety`, a vendor
profile missing a threshold, a NaN that loses every comparison it enters. All of
those produce a plausible report with no exception raised. So:

* Missing source pixel dimensions -> `dpi_floor` FAILS for that placement.
* `face_safety: null` -> `face_in_trim_zone` FAILS. A null is indistinguishable
  from "nobody looked", and a scenery photo can say `face_count: 0` for free.
* A vendor profile with no `dpi_floor` / `safe_margin_mm` / `page_count` ->
  the gate that needed it FAILS.
* NaN anywhere -> treated as absent, which means FAILS. Every threshold
  comparison is written as `not (measured >= required)` so a NaN lands in the
  failure branch rather than sliding through `measured < required`.
* A geometry this validator cannot reason about exactly (a rotated crop, a
  rotated full-bleed frame, a crop claiming pixels outside its own image) ->
  FAILS, rather than being measured with a formula that is nearly right.

THE ONE PLACE THAT RULE DOES NOT APPLY

A field the CONTRACT has already answered is not a field nobody answered.
`NormalizedBox.rotation_deg`, `RectMm.rotation_deg`, `FaceSafety.faces_in_trim_zone`
and `FaceSafety.faces_in_gutter` are optional with an explicit JSON-Schema
`default` of 0, so a spec that omits them has said "zero" in the only way the
schema offers. Treating those omissions as unreadable made this validator reject
books that were correct in every physical respect -- which is the same defect
wearing the opposite coat, and the one that gets an override flag added within a
month. `_defaulted` draws the line: ABSENT takes the schema's default, PRESENT
must be readable or the placement fails. Fields the schema marks `required`
(face_count, all_faces_in_safe_zone, Page.placements, the frame's x/y/w/h) get no
such grace, because for those the contract really has said nothing.

ERROR VERSUS WARNING

An error blocks the export. A warning is shown to a human who may proceed. The
distinction is physical, not stylistic: a face 0.2mm inside an 8mm safe band is
7.8mm from the blade and will survive; a face 8mm inside that band is *at* the
trim line and will be cut in half. Both are "in the trim zone". Only one is a
ruined book. `guillotine_drift_mm` is the line between them.

WHERE THE NUMBERS COME FROM

Nothing physical is hardcoded. Every threshold arrives in the vendor profile
(`packages/album-engine/vendor_profiles/*.json`), because the two shipped
profiles genuinely disagree -- 5mm gutter layflat versus 14mm perfect bound --
and a validator that baked in either would pass a book the other printer would
reject. The one constant that is not a vendor number is `guillotine_drift_mm`,
the mechanical tolerance of the cutter; it is a parameter with a documented
default so a vendor who publishes their own can override it.

DETERMINISM

Same input -> byte-identical report. Placements are visited in sorted order,
every "worst offender" is chosen with an explicit tiebreak on
(page_index, placement_id, media_id), and `validated_at` is an *argument*, never
`datetime.now()` -- a clock reading inside the report would make two runs of the
same album differ and quietly break the determinism guarantee the whole product
rests on.

WHAT THIS MODULE DOES NOT DO

It does not lay anything out. It takes Page/Placement-shaped mappings (or
dataclasses) and never imports the layout solver.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, is_dataclass
from typing import Any

__all__ = [
    "DEFAULT_GUILLOTINE_DRIFT_MM",
    "GATE_ORDER",
    "MM_PER_INCH",
    "VALIDATOR_VERSION",
    "DocumentColor",
    "Finding",
    "PrintValidationReport",
    "SourceImage",
    "validate_album",
    "validate_album_spec",
]


VALIDATOR_VERSION = "print-validator/1.0.0"

MM_PER_INCH = 25.4

# Mechanical tolerance of a guillotine. A face closer than this to the trim line
# is treated as already cut, because on a bad stack it is. Not a vendor field
# today; a parameter so it becomes one the day a printer publishes theirs.
DEFAULT_GUILLOTINE_DRIFT_MM = 1.5

# The declared `effective_dpi` in the spec is compared against the value this
# validator recomputes from the real source pixels. They should be identical;
# the tolerance exists only to absorb the planner rounding its output to one
# decimal place (the golden fixture carries 642.8, 332.0, 324.0).
#
# The absolute term is the one doing that job -- rounding to one decimal can
# only ever move a number by 0.05mm-worth of DPI. The relative term is a
# judgement call about how far apart the planner and the validator are allowed
# to drift at high DPI before somebody should look, and there is no physical
# quantity that fixes it at 1% rather than 2%. What is NOT a judgement call is
# the order of magnitude: the two numbers disagreeing is evidence that one of
# the two computed the geometry wrongly, and at 640 DPI a 20% band would swallow
# a 128 DPI discrepancy -- the difference between a sharp print and a soft one --
# without a word. See test_a_five_percent_lie_in_the_declared_dpi_is_caught.
DPI_RELATIVE_TOLERANCE = 0.01
DPI_ABSOLUTE_TOLERANCE = 0.5

# Millimetre slack for float noise in geometry comparisons. This is an allowance
# for the last bits of a double, NOT a print tolerance: `(x + w) - trim_right`
# on numbers like 40.7 and 265.3 lands an ulp or two either side of the exact
# answer, and a frame that is mathematically flush must not be reported short.
# It has to stay orders of magnitude below anything a press can hold, because
# every millimetre of slack here is a millimetre of white paper on a finished
# edge that this gate approved. 1e-6mm is a nanometre; float noise is ~1e-13mm.
GEOMETRY_EPSILON_MM = 1e-6

# Same idea in normalised crop space: a crop stored as 0.07 + 0.93 must not be
# rejected for containment because those decimals do not sum to exactly 1.0.
_NORMALIZED_EPSILON = 1e-9

# Artwork crossing the trim line by less than this without declaring a bleed is
# rounding, not intent. Above it, the planner meant one thing and the guillotine
# will do another, so it is worth a human glance.
UNDECLARED_SPILL_THRESHOLD_MM = 0.05

# Emission order of the report. The first five are the hard gates the AlbumSpec
# schema requires to be present AND passed before a report may claim "pass";
# see the `contains` clauses in album-spec.schema.json#/$defs/PrintValidationReport.
GATE_ORDER: tuple[str, ...] = (
    "dpi_floor",
    "face_in_trim_zone",
    "face_in_gutter",
    "bleed_coverage",
    "color_profile_match",
    "page_count_valid",
    "source_media_available",
)

_REQUIRED_GATES: frozenset[str] = frozenset(
    {
        "dpi_floor",
        "face_in_trim_zone",
        "bleed_coverage",
        "color_profile_match",
        "page_count_valid",
    }
)

_EDGES: tuple[str, ...] = ("top", "bottom", "left", "right")

_SEVERITY_ERROR = "error"
_SEVERITY_WARNING = "warning"


# --------------------------------------------------------------------------
# Inputs
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class SourceImage:
    """Pixel dimensions of one source photo.

    Named `oriented_*` on purpose. `NormalizedBox` is defined against the
    ORIENTED image -- after EXIF rotation -- so feeding stored dimensions for a
    portrait photo that carries an EXIF rotation flag computes a DPI that is
    wrong by the aspect ratio, raises nothing, and looks entirely plausible in
    the report. The field name is the only place that mistake can be caught,
    so it says what it needs.
    """

    oriented_width_px: int
    oriented_height_px: int


@dataclass(frozen=True)
class DocumentColor:
    """The colour setup the exported PDF will actually carry.

    Supplied by the caller (render-print knows what it is about to embed)
    rather than read off the AlbumSpec, because the point of the check is to
    compare the document against the vendor profile. Comparing the profile with
    itself would pass every time.
    """

    icc_name: str | None
    intent: str | None
    icc_hash: str | None = None


# --------------------------------------------------------------------------
# Outputs
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Finding:
    """One `ValidationCheck` from album-spec.schema.json.

    `counts_toward_totals` is not part of the contract and is dropped by
    `to_dict`. It exists so a per-item gate can emit a rollup ("3 of 19
    placements are below the floor") without that rollup being counted as a
    fourth error. A report that says "4 errors" for 3 problems is a small lie,
    and this project has learned what small lies cost.
    """

    check_id: str
    severity: str
    passed: bool
    detail: str
    page_index: int | None = None
    placement_id: str | None = None
    measured_value: float | None = None
    required_value: float | None = None
    remediation: str | None = None
    counts_toward_totals: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "check_id": self.check_id,
            "severity": self.severity,
            "passed": self.passed,
            "page_index": self.page_index,
            "placement_id": self.placement_id,
            "measured_value": _round(self.measured_value),
            "required_value": _round(self.required_value),
            "detail": self.detail,
            "remediation": self.remediation,
        }


@dataclass(frozen=True)
class PrintValidationReport:
    status: str
    checks: tuple[Finding, ...]
    error_count: int
    warning_count: int
    validated_at: str | None = None
    validator_version: str = VALIDATOR_VERSION

    @property
    def blocking(self) -> tuple[Finding, ...]:
        """Exactly the findings that stop the export. What a UI should show first."""
        return tuple(
            f
            for f in self.checks
            if f.counts_toward_totals and f.severity == _SEVERITY_ERROR and not f.passed
        )

    @property
    def warnings(self) -> tuple[Finding, ...]:
        return tuple(
            f
            for f in self.checks
            if f.counts_toward_totals and f.severity == _SEVERITY_WARNING and not f.passed
        )

    def to_dict(self) -> dict[str, Any]:
        """A `PrintValidationReport` that can be dropped straight into an
        AlbumSpec and validated against the schema."""
        return {
            "status": self.status,
            "checks": [check.to_dict() for check in self.checks],
            "validated_at": self.validated_at,
            "validator_version": self.validator_version,
            "error_count": self.error_count,
            "warning_count": self.warning_count,
        }


# --------------------------------------------------------------------------
# Coercion helpers
#
# Every one of these returns None for "unusable", and every caller treats None
# as a failure rather than as a skip. That is the whole safety property of this
# module expressed in four functions.
# --------------------------------------------------------------------------


def _as_mapping(obj: Any) -> Mapping[str, Any] | None:
    if obj is None:
        return None
    if is_dataclass(obj) and not isinstance(obj, type):
        return asdict(obj)
    if isinstance(obj, Mapping):
        return obj
    return None


def _number(value: Any) -> float | None:
    """A finite float, or None.

    NaN and infinity come back as None deliberately. If NaN were passed through,
    `nan >= floor` is False *and* `nan < floor` is False, so it fails an
    assertion of badness as happily as an assertion of goodness -- whichever
    branch the author happened to write. Converting it to None forces it down
    the "cannot check" path, which fails.

    `bool` is rejected because `isinstance(True, int)` is True in Python, and a
    stray boolean silently becoming 1.0 is exactly the class of bug this file
    exists to prevent.
    """
    if value is None or isinstance(value, bool):
        return None
    if not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number):
        return None
    return number


def _positive_number(value: Any) -> float | None:
    number = _number(value)
    if number is None or number <= 0:
        return None
    return number


def _count(value: Any) -> int | None:
    """A non-negative integer count, or None."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float) and math.isfinite(value) and value.is_integer():
        return int(value) if value >= 0 else None
    return None


def _pixels(value: Any) -> int | None:
    count = _count(value)
    if count is None or count <= 0:
        return None
    return count


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _round(value: float | None) -> float | None:
    """Round only for presentation. No decision in this module is ever taken on
    a rounded value -- that is how a 299.996 DPI placement gets reported as
    300.0 and printed."""
    if value is None:
        return None
    return round(value, 4)


_MISSING = object()


def _defaulted(mapping: Mapping[str, Any], key: str, default: Any) -> Any:
    """The value at `key`, or the schema's `default` when the key is ABSENT.

    The rest of this module treats "I could not read it" as a failure, and that
    is right for a field nobody has spoken about. It is wrong for a field the
    CONTRACT has already spoken about. `NormalizedBox.rotation_deg`,
    `RectMm.rotation_deg`, `FaceSafety.faces_in_trim_zone` and
    `FaceSafety.faces_in_gutter` are each optional with an explicit
    `"default"` in the schema, so an AlbumSpec that omits them is not silent --
    it has said "0" in the only way the contract offers. Reading that omission
    as unusable made this validator block books that were correct, and a print
    gate that cries wolf gets a `--force` flag added to it within a month.

    Absent is the only case that defaults. A key that is PRESENT and unusable
    (null, a string, NaN, a negative count) still fails every downstream check:
    the producer wrote something down and what it wrote is not the field.
    """
    value = mapping.get(key, _MISSING)
    return default if value is _MISSING else value


def _icc_key(name: str) -> str:
    """Normalise an ICC name for comparison: case and internal whitespace only.

    Deliberately no fuzzy matching. 'FOGRA39 Coated' and
    'Coated FOGRA39 (ISO 12647-2:2004)' are the same profile to a human and
    different strings here, and that mismatch should reach a person rather than
    be guessed at by a similarity heuristic that will one day match two profiles
    that are genuinely different.
    """
    return " ".join(name.split()).casefold()


# --------------------------------------------------------------------------
# Placement iteration
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class _Ctx:
    """One placement plus the page context needed to talk about it."""

    order: int
    page_index: int | None
    side: str | None
    spread_id: str | None
    placement_id: str | None
    media_id: str | None
    placement: Mapping[str, Any]

    @property
    def label(self) -> str:
        page = "unindexed page" if self.page_index is None else f"page {self.page_index}"
        name = self.placement_id or "<unidentified placement>"
        return f"{page}, placement '{name}'"

    @property
    def sort_key(self) -> tuple[Any, ...]:
        return (
            self.page_index is None,
            self.page_index if self.page_index is not None else 0,
            self.placement_id or "",
            self.media_id or "",
            self.order,
        )


def _placement_contexts(pages: Sequence[Any]) -> tuple[list[_Ctx], list[str]]:
    """Every placement in the album, in a fixed order, plus what was unreadable.

    Sorted rather than taken as given: a caller that hands us pages in a
    different order must get the same report, and the "worst offender" a
    summary points at must not depend on which page happened to come first.

    Anything this cannot read is *returned*, never dropped. A placement that
    silently falls out of the iteration is a placement no gate ever sees, and
    an album can then pass because its worst photo was unparseable.
    """
    contexts: list[_Ctx] = []
    unreadable: list[str] = []
    order = 0
    for position, page in enumerate(pages):
        page_map = _as_mapping(page)
        if page_map is None:
            unreadable.append(f"page at position {position} is not an object")
            continue
        page_index = _count(page_map.get("page_index"))
        side = _text(page_map.get("side"))
        spread_id = _text(page_map.get("spread_id"))
        # NOT `page_map.get("placements") or []`. That reads 0, False, "", {} and
        # a missing key alike as "no placements" and emits nothing at all, so a
        # page whose placements failed to parse upstream validates clean and
        # prints blank. Unlike rotation_deg and the face counts, `placements` is
        # `required` in the Page schema -- its `default: []` is unreachable -- so
        # there is no contract-sanctioned way to omit it and an absent key is a
        # defect, not a blank page. A blank page says so with `[]`.
        placements = page_map.get("placements", _MISSING)
        if placements is _MISSING:
            unreadable.append(
                f"page at position {position} has no placements field at all, which is "
                "not the same claim as an empty page"
            )
            continue
        if not isinstance(placements, Sequence) or isinstance(placements, (str, bytes)):
            unreadable.append(
                f"page at position {position} has a placements field that is not a list "
                f"({type(placements).__name__})"
            )
            continue
        for slot, placement in enumerate(placements):
            placement_map = _as_mapping(placement)
            if placement_map is None:
                unreadable.append(
                    f"placement {slot} on page at position {position} is not an object"
                )
                continue
            contexts.append(
                _Ctx(
                    order=order,
                    page_index=page_index,
                    side=side,
                    spread_id=spread_id,
                    placement_id=_text(placement_map.get("placement_id")),
                    media_id=_text(placement_map.get("media_id")),
                    placement=placement_map,
                )
            )
            order += 1
    contexts.sort(key=lambda ctx: ctx.sort_key)
    return contexts, unreadable


def _worst(items: Sequence[tuple[float, _Ctx]]) -> tuple[float, _Ctx] | None:
    """Lowest value wins, ties broken by page then placement_id then media_id.

    The golden fixture has three placements at exactly 324.0 DPI, and which one
    a report names must not depend on which page the caller handed us first.

    Every list this is called with today is built by walking `contexts`, which
    `_placement_contexts` has already sorted, so the tiebreak never actually
    fires through `validate_album` -- deleting it would change nothing you can
    observe from outside. It stays because "the caller sorted first" is an
    assumption held in a different function, and the day someone builds a list
    of candidates some other way this is the difference between a deterministic
    report and one that is right until the pages are reordered. Tested directly
    rather than through the public path, since the public path cannot reach it.
    """
    if not items:
        return None
    return min(items, key=lambda pair: (pair[0], pair[1].sort_key))


# --------------------------------------------------------------------------
# Gate: dpi_floor
# --------------------------------------------------------------------------


def _effective_dpi(
    ctx: _Ctx, source: SourceImage
) -> tuple[float | None, str | None, dict[str, float]]:
    """Recompute effective DPI from the geometry. Returns (dpi, failure_reason, parts).

    Deliberately recomputed rather than read from `placement.effective_dpi`.
    The declared number is produced by the same planner that produced the
    layout; trusting it means the gate is only as good as the code it is meant
    to be gating, and a planner that mis-scales a crop would declare a
    comfortable 332 for a 210 DPI placement and be believed.
    """
    frame = _as_mapping(ctx.placement.get("frame"))
    crop = _as_mapping(ctx.placement.get("crop"))
    if frame is None:
        return None, "placement has no frame", {}
    if crop is None:
        return None, "placement has no crop", {}

    frame_w = _positive_number(frame.get("width_mm"))
    frame_h = _positive_number(frame.get("height_mm"))
    crop_w = _positive_number(crop.get("w"))
    crop_h = _positive_number(crop.get("h"))
    if frame_w is None or frame_h is None:
        return None, "frame width/height is missing, zero or not a finite number", {}
    if crop_w is None or crop_h is None:
        return None, "crop width/height is missing, zero or not a finite number", {}

    crop_x = _number(crop.get("x"))
    crop_y = _number(crop.get("y"))
    if crop_x is None or crop_y is None:
        return None, "crop x/y is missing or not a finite number", {}

    # A NormalizedBox is defined in [0,1] of the ORIENTED image, and the DPI
    # below multiplies crop_w by the source's pixel width. A crop that runs off
    # the image claims pixels that do not exist: w=2.0 doubles the reported DPI,
    # and x=0.8 with w=0.5 claims 1.3x the pixels actually to the right of x.
    # Either one turns a 1600px phone JPEG into a confident 800 DPI and prints
    # it a metre wide. The schema bounds w/h at 1 but says nothing about
    # containment, so containment is checked here on physical grounds: this
    # validator refuses geometry it cannot measure honestly rather than
    # measuring it with a formula that is only right for well-behaved input.
    if not (0.0 <= crop_x <= 1.0) or not (0.0 <= crop_y <= 1.0):
        return None, f"crop origin ({crop_x}, {crop_y}) is outside the [0,1] image", {}
    if crop_w > 1.0 or crop_h > 1.0:
        return (
            None,
            f"crop is {crop_w}x{crop_h} of the image, and a crop cannot be larger "
            "than the image it is taken from",
            {},
        )
    limit = 1.0 + _NORMALIZED_EPSILON
    if crop_x + crop_w > limit or crop_y + crop_h > limit:
        return (
            None,
            f"crop runs off the image: x+w={crop_x + crop_w}, y+h={crop_y + crop_h}; "
            "the pixels it claims past the edge do not exist, so any DPI computed "
            "from its width would be an overstatement",
            {},
        )

    # Absent rotation_deg means 0 -- the schema says so with an explicit default,
    # and rejecting an unrotated crop for not saying it was unrotated blocked
    # perfectly good books. Present-but-unreadable is still a failure.
    crop_rotation = _number(_defaulted(crop, "rotation_deg", 0.0))
    if crop_rotation is None:
        return None, "crop rotation_deg is present but not a finite number", {}
    if crop_rotation != 0.0:
        # A NormalizedBox rotates in NORMALISED space. On a non-square image
        # that is not a rotation in pixel space -- the box maps to a
        # parallelogram whose edge length is w*sqrt(W^2cos^2 + H^2sin^2), not
        # w*W. Using w*W here would report a confident, wrong, plausible number.
        # Refusing is the honest answer until the renderer's sampling
        # convention for rotated crops is pinned down in the contract.
        return (
            None,
            f"crop is rotated {crop_rotation}deg and this validator cannot compute "
            "effective DPI for a rotated crop exactly",
            {},
        )

    dpi_x = crop_w * source.oriented_width_px * MM_PER_INCH / frame_w
    dpi_y = crop_h * source.oriented_height_px * MM_PER_INCH / frame_h
    # min, not mean: if the crop aspect does not match the frame aspect the
    # renderer must fill one axis and the softer one is what prints.
    return min(dpi_x, dpi_y), None, {"dpi_x": dpi_x, "dpi_y": dpi_y}


def _check_dpi_floor(
    contexts: Sequence[_Ctx],
    profile: Mapping[str, Any],
    sources: Mapping[str, Any],
) -> list[Finding]:
    floor = _positive_number(profile.get("dpi_floor"))
    if floor is None:
        return [
            Finding(
                check_id="dpi_floor",
                severity=_SEVERITY_ERROR,
                passed=False,
                detail=(
                    "vendor profile declares no usable dpi_floor, so no placement "
                    "can be checked against it"
                ),
                remediation="fix the vendor profile: dpi_floor must be a positive number",
            )
        ]

    preferred = _positive_number(profile.get("dpi_preferred"))
    violations: list[Finding] = []
    measured: list[tuple[float, _Ctx]] = []

    for ctx in contexts:
        source = _source_image(sources, ctx.media_id)
        if source is None:
            violations.append(
                Finding(
                    check_id="dpi_floor",
                    severity=_SEVERITY_ERROR,
                    passed=False,
                    page_index=ctx.page_index,
                    placement_id=ctx.placement_id,
                    required_value=floor,
                    detail=(
                        f"{ctx.label}: source resolution for media "
                        f"{ctx.media_id or '<missing media_id>'} is unknown, so effective "
                        "DPI cannot be computed"
                    ),
                    remediation=(
                        "supply the oriented pixel dimensions of the source; an "
                        "unknown resolution is a failure, not a skip"
                    ),
                )
            )
            continue

        dpi, reason, _ = _effective_dpi(ctx, source)
        if dpi is None:
            violations.append(
                Finding(
                    check_id="dpi_floor",
                    severity=_SEVERITY_ERROR,
                    passed=False,
                    page_index=ctx.page_index,
                    placement_id=ctx.placement_id,
                    required_value=floor,
                    detail=f"{ctx.label}: {reason}",
                    remediation="fix the placement geometry, then re-validate",
                )
            )
            continue

        measured.append((dpi, ctx))

        declared = _number(ctx.placement.get("effective_dpi"))
        if declared is None:
            violations.append(
                Finding(
                    check_id="dpi_floor",
                    severity=_SEVERITY_ERROR,
                    passed=False,
                    page_index=ctx.page_index,
                    placement_id=ctx.placement_id,
                    measured_value=dpi,
                    required_value=floor,
                    detail=(
                        f"{ctx.label}: effective_dpi is missing or not a finite number; "
                        f"recomputed from the geometry it is {dpi:.1f}"
                    ),
                    remediation=f"set effective_dpi to {dpi:.1f}",
                )
            )
        else:
            tolerance = max(DPI_ABSOLUTE_TOLERANCE, DPI_RELATIVE_TOLERANCE * dpi)
            if not abs(declared - dpi) <= tolerance:
                violations.append(
                    Finding(
                        check_id="dpi_floor",
                        severity=_SEVERITY_ERROR,
                        passed=False,
                        page_index=ctx.page_index,
                        placement_id=ctx.placement_id,
                        measured_value=dpi,
                        required_value=declared,
                        detail=(
                            f"{ctx.label}: the spec declares {declared:.1f} DPI but the "
                            f"geometry and source resolution give {dpi:.1f} DPI, a "
                            f"difference of {abs(declared - dpi):.1f}. One of the two is "
                            "wrong and this validator cannot tell which"
                        ),
                        remediation=(
                            "recompute effective_dpi in the planner, or correct the "
                            "recorded source resolution"
                        ),
                    )
                )

        # `not (dpi >= floor)` rather than `dpi < floor`: identical for real
        # numbers, and the only form that still fails if `dpi` were ever NaN.
        if not (dpi >= floor):
            frame = _as_mapping(ctx.placement.get("frame")) or {}
            frame_w = _positive_number(frame.get("width_mm")) or 0.0
            # Rounded DOWN to the printed decimal place. A remediation printed
            # at one decimal that was rounded up is one a human can follow
            # exactly and still fail re-validation by a fraction of a DPI,
            # which reads as the validator being broken.
            max_width_mm = math.floor(frame_w * dpi / floor * 10.0) / 10.0
            violations.append(
                Finding(
                    check_id="dpi_floor",
                    severity=_SEVERITY_ERROR,
                    passed=False,
                    page_index=ctx.page_index,
                    placement_id=ctx.placement_id,
                    measured_value=dpi,
                    required_value=floor,
                    detail=(
                        f"{ctx.label}: {dpi:.1f} DPI at printed size, {floor - dpi:.1f} "
                        f"below the vendor floor of {floor:.0f}"
                    ),
                    remediation=(
                        f"reduce the frame from {frame_w:.1f}mm to {max_width_mm:.1f}mm "
                        f"wide, upscale the source, or replace the photo"
                    ),
                )
            )
        elif preferred is not None and not (dpi >= preferred):
            violations.append(
                Finding(
                    check_id="dpi_floor",
                    severity=_SEVERITY_WARNING,
                    passed=False,
                    page_index=ctx.page_index,
                    placement_id=ctx.placement_id,
                    measured_value=dpi,
                    required_value=preferred,
                    detail=(
                        f"{ctx.label}: {dpi:.1f} DPI clears the {floor:.0f} floor but is "
                        f"below the vendor's preferred {preferred:.0f}"
                    ),
                    remediation="acceptable to print; a smaller frame would be sharper",
                )
            )

    blocking = [f for f in violations if f.severity == _SEVERITY_ERROR]
    worst = _worst(measured)
    if blocking:
        summary = Finding(
            check_id="dpi_floor",
            severity=_SEVERITY_ERROR,
            passed=False,
            page_index=blocking[0].page_index,
            placement_id=blocking[0].placement_id,
            measured_value=worst[0] if worst else None,
            required_value=floor,
            detail=(
                f"{len(blocking)} of {len(contexts)} placements fail the "
                f"{floor:.0f} DPI floor"
            ),
            remediation="see the itemised failures below",
            counts_toward_totals=False,
        )
    else:
        summary = Finding(
            check_id="dpi_floor",
            severity=_SEVERITY_ERROR,
            passed=True,
            page_index=worst[1].page_index if worst else None,
            placement_id=worst[1].placement_id if worst else None,
            measured_value=worst[0] if worst else None,
            required_value=floor,
            detail=(
                f"lowest effective DPI across {len(contexts)} placements"
                if worst
                else "no placements to check"
            ),
            counts_toward_totals=False,
        )
    return [summary, *violations]


def _source_image(sources: Mapping[str, Any], media_id: str | None) -> SourceImage | None:
    if media_id is None:
        return None
    raw = sources.get(media_id)
    if raw is None:
        return None
    if isinstance(raw, SourceImage):
        width, height = _pixels(raw.oriented_width_px), _pixels(raw.oriented_height_px)
    else:
        mapping = _as_mapping(raw)
        if mapping is None:
            return None
        width = _pixels(mapping.get("oriented_width_px"))
        height = _pixels(mapping.get("oriented_height_px"))
    if width is None or height is None:
        return None
    return SourceImage(oriented_width_px=width, oriented_height_px=height)


# --------------------------------------------------------------------------
# Gate: source_media_available
# --------------------------------------------------------------------------


def _check_source_media_available(
    contexts: Sequence[_Ctx], sources: Mapping[str, Any]
) -> list[Finding]:
    violations: list[Finding] = []
    for ctx in contexts:
        if ctx.media_id is None:
            violations.append(
                Finding(
                    check_id="source_media_available",
                    severity=_SEVERITY_ERROR,
                    passed=False,
                    page_index=ctx.page_index,
                    placement_id=ctx.placement_id,
                    detail=f"{ctx.label}: no media_id, so no source can be resolved",
                    remediation="set media_id to the BLAKE3 content address of the source",
                )
            )
        elif _source_image(sources, ctx.media_id) is None:
            violations.append(
                Finding(
                    check_id="source_media_available",
                    severity=_SEVERITY_ERROR,
                    passed=False,
                    page_index=ctx.page_index,
                    placement_id=ctx.placement_id,
                    detail=(
                        f"{ctx.label}: source {ctx.media_id} is missing or has no usable "
                        "oriented pixel dimensions"
                    ),
                    remediation="re-ingest the source, or replace the placement",
                )
            )

    resolved = len(contexts) - len(violations)
    summary = Finding(
        check_id="source_media_available",
        severity=_SEVERITY_ERROR,
        passed=not violations,
        detail=(
            f"{resolved} of {len(contexts)} placements resolved to a source"
            if contexts
            else "no placements to resolve"
        ),
        counts_toward_totals=False,
    )
    return [summary, *violations]


# --------------------------------------------------------------------------
# Gates: face_in_trim_zone and face_in_gutter
#
# Both read `FaceSafety`, which the album engine computes. This validator does
# not receive face rectangles -- the contract does not put them on a Placement --
# so what it can do is refuse to accept silence, and refuse to accept a
# FaceSafety that contradicts itself. A record claiming zero faces in the trim
# zone while also claiming not all faces are in the safe zone is a bug in
# whatever produced it, and the correct response to "these two fields disagree"
# is never to believe the cheerful one.
# --------------------------------------------------------------------------


def _face_safety(ctx: _Ctx) -> tuple[Mapping[str, Any] | None, str | None]:
    raw = ctx.placement.get("face_safety")
    if raw is None:
        return None, (
            "face_safety is null, which is indistinguishable from 'nobody ran the "
            "face check'"
        )
    mapping = _as_mapping(raw)
    if mapping is None:
        return None, "face_safety is present but not an object"
    return mapping, None


def _check_face_in_trim_zone(
    contexts: Sequence[_Ctx],
    profile: Mapping[str, Any],
    guillotine_drift_mm: float,
) -> list[Finding]:
    safe_margin = _number(profile.get("safe_margin_mm"))
    if safe_margin is None or safe_margin < 0:
        return [
            Finding(
                check_id="face_in_trim_zone",
                severity=_SEVERITY_ERROR,
                passed=False,
                detail=(
                    "vendor profile declares no usable safe_margin_mm, so no face can "
                    "be checked against the trim zone"
                ),
                remediation="fix the vendor profile: safe_margin_mm must be >= 0",
            )
        ]

    violations: list[Finding] = []
    margins: list[tuple[float, _Ctx]] = []

    for ctx in contexts:
        safety, reason = _face_safety(ctx)
        if safety is None:
            violations.append(
                Finding(
                    check_id="face_in_trim_zone",
                    severity=_SEVERITY_ERROR,
                    passed=False,
                    page_index=ctx.page_index,
                    placement_id=ctx.placement_id,
                    required_value=safe_margin,
                    detail=f"{ctx.label}: {reason}",
                    remediation=(
                        "run face-safety analysis on this placement; a photo with no "
                        "faces must say so with face_count: 0 rather than by omission"
                    ),
                )
            )
            continue

        # face_count and all_faces_in_safe_zone are `required` by the schema:
        # missing means nobody looked, and that fails. faces_in_trim_zone and
        # faces_in_gutter are optional with `default: 0`, so a placement whose
        # faces are all fine may legitimately omit them -- and the producer that
        # omits them while also setting all_faces_in_safe_zone=false is still
        # caught, by the "unattributed unsafety" contradiction below.
        face_count = _count(safety.get("face_count"))
        in_trim = _count(_defaulted(safety, "faces_in_trim_zone", 0))
        in_gutter = _count(_defaulted(safety, "faces_in_gutter", 0))
        all_safe = safety.get("all_faces_in_safe_zone")
        margin = _number(safety.get("min_face_margin_mm"))

        if face_count is None or in_trim is None or in_gutter is None:
            violations.append(
                Finding(
                    check_id="face_in_trim_zone",
                    severity=_SEVERITY_ERROR,
                    passed=False,
                    page_index=ctx.page_index,
                    placement_id=ctx.placement_id,
                    required_value=safe_margin,
                    detail=(
                        f"{ctx.label}: face_safety counts are missing or not "
                        "non-negative integers"
                    ),
                    remediation="re-run face-safety analysis on this placement",
                )
            )
            continue

        if not isinstance(all_safe, bool):
            violations.append(
                Finding(
                    check_id="face_in_trim_zone",
                    severity=_SEVERITY_ERROR,
                    passed=False,
                    page_index=ctx.page_index,
                    placement_id=ctx.placement_id,
                    required_value=safe_margin,
                    detail=f"{ctx.label}: all_faces_in_safe_zone is not a boolean",
                    remediation="re-run face-safety analysis on this placement",
                )
            )
            continue

        # Self-contradiction. Whatever produced this record is broken, and a
        # broken producer's optimistic field is not evidence of safety.
        contradictions: list[str] = []
        if in_trim > face_count:
            contradictions.append(
                f"faces_in_trim_zone={in_trim} exceeds face_count={face_count}"
            )
        if in_gutter > face_count:
            contradictions.append(
                f"faces_in_gutter={in_gutter} exceeds face_count={face_count}"
            )
        if all_safe and (in_trim > 0 or in_gutter > 0):
            contradictions.append(
                f"all_faces_in_safe_zone=true while faces_in_trim_zone={in_trim} and "
                f"faces_in_gutter={in_gutter}"
            )
        if not all_safe and in_trim == 0 and in_gutter == 0:
            contradictions.append(
                "all_faces_in_safe_zone=false but no face is attributed to the trim "
                "zone or the gutter"
            )
        if all_safe and margin is not None and margin < 0:
            # A negative margin means a face has already crossed an unsafe
            # boundary. Without this the record passes on the strength of its
            # own boolean while its own number says the opposite.
            contradictions.append(
                f"all_faces_in_safe_zone=true while min_face_margin_mm={margin:.2f} is "
                "negative, which means a face has crossed the safe boundary"
            )
        if contradictions:
            violations.append(
                Finding(
                    check_id="face_in_trim_zone",
                    severity=_SEVERITY_ERROR,
                    passed=False,
                    page_index=ctx.page_index,
                    placement_id=ctx.placement_id,
                    required_value=safe_margin,
                    detail=f"{ctx.label}: face_safety contradicts itself -- "
                    + "; ".join(contradictions),
                    remediation="re-run face-safety analysis on this placement",
                )
            )
            continue

        if face_count > 0 and margin is None:
            # Without the margin there is no way to tell a face grazing the safe
            # line from a face already under the blade, and those are not the
            # same problem.
            violations.append(
                Finding(
                    check_id="face_in_trim_zone",
                    severity=_SEVERITY_ERROR,
                    passed=False,
                    page_index=ctx.page_index,
                    placement_id=ctx.placement_id,
                    required_value=safe_margin,
                    detail=(
                        f"{ctx.label}: {face_count} face(s) but min_face_margin_mm is "
                        "missing or not a finite number, so how close they are to the "
                        "trim line is unknown"
                    ),
                    remediation="re-run face-safety analysis on this placement",
                )
            )
            continue

        if margin is not None and face_count > 0:
            margins.append((margin, ctx))

        if in_trim == 0:
            continue

        # margin is measured from the safe-zone boundary and is negative once a
        # face has crossed it, so the distance still separating the face from
        # the blade is the margin plus the width of the safe band.
        distance_to_trim = margin + safe_margin  # type: ignore[operator]
        if margin >= 0:  # type: ignore[operator]
            violations.append(
                Finding(
                    check_id="face_in_trim_zone",
                    severity=_SEVERITY_ERROR,
                    passed=False,
                    page_index=ctx.page_index,
                    placement_id=ctx.placement_id,
                    measured_value=margin,
                    required_value=safe_margin,
                    detail=(
                        f"{ctx.label}: {in_trim} face(s) reported in the trim zone but "
                        f"min_face_margin_mm={margin:.2f} says the nearest face is still "
                        "inside the safe zone"
                    ),
                    remediation="re-run face-safety analysis on this placement",
                )
            )
            continue

        if not (distance_to_trim > guillotine_drift_mm):
            violations.append(
                Finding(
                    check_id="face_in_trim_zone",
                    severity=_SEVERITY_ERROR,
                    passed=False,
                    page_index=ctx.page_index,
                    placement_id=ctx.placement_id,
                    measured_value=distance_to_trim,
                    required_value=safe_margin,
                    detail=(
                        f"{ctx.label}: {in_trim} face(s) in the trim zone; the nearest "
                        f"sits {distance_to_trim:.2f}mm from the trim line, inside the "
                        f"{guillotine_drift_mm:.2f}mm the guillotine can drift"
                        + (" -- it is already past the trim line" if distance_to_trim <= 0 else "")
                    ),
                    remediation=(
                        f"move the subject at least "
                        f"{safe_margin - distance_to_trim:.2f}mm further inside the page, "
                        "or re-crop so the face clears the safe margin"
                    ),
                )
            )
        else:
            violations.append(
                Finding(
                    check_id="face_in_trim_zone",
                    severity=_SEVERITY_WARNING,
                    passed=False,
                    page_index=ctx.page_index,
                    placement_id=ctx.placement_id,
                    measured_value=distance_to_trim,
                    required_value=safe_margin,
                    detail=(
                        f"{ctx.label}: {in_trim} face(s) {-margin:.2f}mm inside the "  # type: ignore[operator]
                        f"{safe_margin:.2f}mm safe margin, still "
                        f"{distance_to_trim:.2f}mm clear of the trim line"
                    ),
                    remediation=(
                        "safe to print, but the composition is tighter than the vendor "
                        "recommends; nudging the crop would fix it"
                    ),
                )
            )

    blocking = [f for f in violations if f.severity == _SEVERITY_ERROR]
    closest = _worst(margins)
    if blocking:
        summary = Finding(
            check_id="face_in_trim_zone",
            severity=_SEVERITY_ERROR,
            passed=False,
            page_index=blocking[0].page_index,
            placement_id=blocking[0].placement_id,
            required_value=safe_margin,
            detail=f"{len(blocking)} placement(s) fail the trim-zone check",
            remediation="see the itemised failures below",
            counts_toward_totals=False,
        )
    else:
        summary = Finding(
            check_id="face_in_trim_zone",
            severity=_SEVERITY_ERROR,
            passed=True,
            page_index=closest[1].page_index if closest else None,
            placement_id=closest[1].placement_id if closest else None,
            measured_value=closest[0] if closest else None,
            required_value=safe_margin,
            detail=(
                (
                    f"closest face sits {closest[0]:.2f}mm inside the safe zone"
                    if closest[0] >= 0
                    else f"closest face is {-closest[0]:.2f}mm past the safe-zone boundary"
                )
                if closest
                else f"no faces in {len(contexts)} placement(s)"
            ),
            counts_toward_totals=False,
        )
    return [summary, *violations]


def _check_face_in_gutter(
    contexts: Sequence[_Ctx], profile: Mapping[str, Any]
) -> list[Finding]:
    gutter = _number(profile.get("gutter_mm"))
    if gutter is None:
        # Absent gutter_mm has a schema default of 0, but "the vendor did not
        # say" and "the vendor said zero" are different claims and only one of
        # them is safe to act on.
        return [
            Finding(
                check_id="face_in_gutter",
                severity=_SEVERITY_ERROR,
                passed=False,
                detail=(
                    "vendor profile declares no usable gutter_mm, so no face can be "
                    "checked against the spine"
                ),
                remediation="fix the vendor profile: gutter_mm must be a number >= 0",
            )
        ]

    violations: list[Finding] = []
    total_in_gutter = 0
    for ctx in contexts:
        safety, _ = _face_safety(ctx)
        if safety is None:
            # Already an error under face_in_trim_zone. Reporting the same
            # missing record twice would inflate the error count.
            continue
        in_gutter = _count(_defaulted(safety, "faces_in_gutter", 0))
        if in_gutter is None:
            # Present and unreadable. Already an error under face_in_trim_zone,
            # which reports the same broken record once rather than twice.
            continue
        if in_gutter == 0:
            continue
        total_in_gutter += in_gutter
        violations.append(
            Finding(
                check_id="face_in_gutter",
                severity=_SEVERITY_ERROR,
                passed=False,
                page_index=ctx.page_index,
                placement_id=ctx.placement_id,
                measured_value=float(in_gutter),
                required_value=0.0,
                detail=(
                    f"{ctx.label}: {in_gutter} face(s) inside the {gutter:.1f}mm gutter"
                    + (
                        f"; on a {profile.get('binding')} book the spine swallows them"
                        if _text(profile.get("binding"))
                        else ""
                    )
                ),
                remediation=(
                    f"shift the placement at least {gutter:.1f}mm away from the spine, "
                    "or move the photo to the outer half of the spread"
                ),
            )
        )

    summary = Finding(
        check_id="face_in_gutter",
        severity=_SEVERITY_ERROR,
        passed=not violations,
        measured_value=float(total_in_gutter),
        required_value=0.0,
        detail=(
            f"{total_in_gutter} face(s) within the {gutter:.1f}mm gutter"
            if violations
            else f"no faces within the {gutter:.1f}mm gutter"
        ),
        counts_toward_totals=False,
    )
    return [summary, *violations]


# --------------------------------------------------------------------------
# Gate: bleed_coverage
# --------------------------------------------------------------------------


def _check_bleed_coverage(
    contexts: Sequence[_Ctx], profile: Mapping[str, Any]
) -> list[Finding]:
    bleed = _number(profile.get("bleed_mm"))
    trim = _as_mapping(profile.get("trim_size_mm"))
    trim_w = _positive_number(trim.get("width_mm")) if trim else None
    trim_h = _positive_number(trim.get("height_mm")) if trim else None
    if bleed is None or bleed < 0 or trim_w is None or trim_h is None:
        return [
            Finding(
                check_id="bleed_coverage",
                severity=_SEVERITY_ERROR,
                passed=False,
                detail=(
                    "vendor profile declares no usable bleed_mm or trim_size_mm, so no "
                    "placement can be checked for bleed"
                ),
                remediation=(
                    "fix the vendor profile: bleed_mm >= 0 and a positive trim size"
                ),
            )
        ]

    # RectMm coordinates are measured from the top-left of the BLEED box, so the
    # trim line sits `bleed` in from every edge and the bleed box is
    # trim + 2*bleed on each axis.
    trim_left = bleed
    trim_top = bleed
    trim_right = bleed + trim_w
    trim_bottom = bleed + trim_h

    violations: list[Finding] = []
    coverages: list[tuple[float, _Ctx]] = []

    for ctx in contexts:
        declared = ctx.placement.get("bleeds") or []
        if not isinstance(declared, Sequence) or isinstance(declared, (str, bytes)):
            violations.append(
                Finding(
                    check_id="bleed_coverage",
                    severity=_SEVERITY_ERROR,
                    passed=False,
                    page_index=ctx.page_index,
                    placement_id=ctx.placement_id,
                    detail=f"{ctx.label}: bleeds is not a list of edges",
                    remediation="set bleeds to a list drawn from top/bottom/left/right",
                )
            )
            continue

        declared_edges = [e for e in declared if isinstance(e, str)]
        unknown = sorted({e for e in declared_edges if e not in _EDGES})
        if unknown or len(declared_edges) != len(list(declared)):
            violations.append(
                Finding(
                    check_id="bleed_coverage",
                    severity=_SEVERITY_ERROR,
                    passed=False,
                    page_index=ctx.page_index,
                    placement_id=ctx.placement_id,
                    detail=(
                        f"{ctx.label}: bleeds contains entries this validator does not "
                        f"recognise ({', '.join(unknown) or 'non-string entries'}), so "
                        "the edges they refer to cannot be checked"
                    ),
                    remediation="use only top/bottom/left/right",
                )
            )
            continue

        frame = _as_mapping(ctx.placement.get("frame"))
        if frame is None:
            violations.append(
                Finding(
                    check_id="bleed_coverage",
                    severity=_SEVERITY_ERROR,
                    passed=False,
                    page_index=ctx.page_index,
                    placement_id=ctx.placement_id,
                    detail=f"{ctx.label}: placement has no frame",
                    remediation="fix the placement geometry, then re-validate",
                )
            )
            continue

        x = _number(frame.get("x_mm"))
        y = _number(frame.get("y_mm"))
        w = _positive_number(frame.get("width_mm"))
        h = _positive_number(frame.get("height_mm"))
        # RectMm.rotation_deg carries a schema default of 0, so an unrotated
        # frame is allowed to say nothing. x/y/width/height are all `required`
        # and get no such grace.
        rotation = _number(_defaulted(frame, "rotation_deg", 0.0))
        if x is None or y is None or w is None or h is None or rotation is None:
            violations.append(
                Finding(
                    check_id="bleed_coverage",
                    severity=_SEVERITY_ERROR,
                    passed=False,
                    page_index=ctx.page_index,
                    placement_id=ctx.placement_id,
                    detail=(
                        f"{ctx.label}: frame geometry is missing, zero or not a finite "
                        "number"
                    ),
                    remediation="fix the placement geometry, then re-validate",
                )
            )
            continue

        edges = set(declared_edges)
        if rotation != 0.0 and edges:
            # A rotated rectangle does not cover its own bounding box, so the
            # cheap AABB test would report coverage the artwork does not have --
            # a white wedge in the corner of a finished page, approved by a
            # validator that said yes. Refuse instead.
            violations.append(
                Finding(
                    check_id="bleed_coverage",
                    severity=_SEVERITY_ERROR,
                    passed=False,
                    page_index=ctx.page_index,
                    placement_id=ctx.placement_id,
                    measured_value=rotation,
                    detail=(
                        f"{ctx.label}: declares bleed on {', '.join(sorted(edges))} while "
                        f"rotated {rotation}deg; this validator cannot prove a rotated "
                        "frame covers the bleed area"
                    ),
                    remediation=(
                        "un-rotate the full-bleed placement, or oversize it and drop the "
                        "bleed declaration"
                    ),
                )
            )
            continue

        left, top, right, bottom = _aabb(x, y, w, h, rotation)
        coverage = {
            "left": trim_left - left,
            "top": trim_top - top,
            "right": right - trim_right,
            "bottom": bottom - trim_bottom,
        }

        for edge in sorted(edges):
            covered = coverage[edge]
            coverages.append((covered, ctx))
            if not (covered >= bleed - GEOMETRY_EPSILON_MM):
                violations.append(
                    Finding(
                        check_id="bleed_coverage",
                        severity=_SEVERITY_ERROR,
                        passed=False,
                        page_index=ctx.page_index,
                        placement_id=ctx.placement_id,
                        measured_value=covered,
                        required_value=bleed,
                        detail=(
                            f"{ctx.label}: declares a {edge} bleed but extends only "
                            f"{covered:.2f}mm past the {edge} trim line, "
                            f"{bleed - covered:.2f}mm short of the required "
                            f"{bleed:.2f}mm"
                        ),
                        remediation=(
                            f"grow the frame {bleed - covered:.2f}mm on the {edge} edge; "
                            "under-bleed prints as a white sliver on the finished edge"
                        ),
                    )
                )

        for edge in _EDGES:
            if edge in edges:
                continue
            spill = coverage[edge]
            if spill > UNDECLARED_SPILL_THRESHOLD_MM:
                violations.append(
                    Finding(
                        check_id="bleed_coverage",
                        severity=_SEVERITY_WARNING,
                        passed=False,
                        page_index=ctx.page_index,
                        placement_id=ctx.placement_id,
                        measured_value=spill,
                        required_value=0.0,
                        detail=(
                            f"{ctx.label}: extends {spill:.2f}mm past the {edge} trim "
                            f"line without declaring a {edge} bleed"
                            + (
                                " (measured on the bounding box of a rotated frame)"
                                if rotation != 0.0
                                else ""
                            )
                            + ", so that strip will be cut off"
                        ),
                        remediation=(
                            f"declare the {edge} bleed if it is intentional, or pull the "
                            f"frame {spill:.2f}mm back inside the trim"
                        ),
                    )
                )

    blocking = [f for f in violations if f.severity == _SEVERITY_ERROR]
    worst = _worst(coverages)
    if blocking:
        summary = Finding(
            check_id="bleed_coverage",
            severity=_SEVERITY_ERROR,
            passed=False,
            page_index=blocking[0].page_index,
            placement_id=blocking[0].placement_id,
            measured_value=worst[0] if worst else None,
            required_value=bleed,
            detail=f"{len(blocking)} bleed violation(s)",
            remediation="see the itemised failures below",
            counts_toward_totals=False,
        )
    else:
        summary = Finding(
            check_id="bleed_coverage",
            severity=_SEVERITY_ERROR,
            passed=True,
            page_index=worst[1].page_index if worst else None,
            placement_id=worst[1].placement_id if worst else None,
            measured_value=worst[0] if worst else None,
            required_value=bleed,
            detail=(
                f"tightest declared bleed extends {worst[0]:.2f}mm past the trim line"
                if worst
                else "no placement declares a bleed"
            ),
            counts_toward_totals=False,
        )
    return [summary, *violations]


def _aabb(
    x: float, y: float, w: float, h: float, rotation_deg: float
) -> tuple[float, float, float, float]:
    """Axis-aligned bounds of a frame, rotated about its own centre."""
    if rotation_deg == 0.0:
        return x, y, x + w, y + h
    theta = math.radians(rotation_deg)
    cos_t, sin_t = abs(math.cos(theta)), abs(math.sin(theta))
    half_w = (w * cos_t + h * sin_t) / 2.0
    half_h = (w * sin_t + h * cos_t) / 2.0
    cx, cy = x + w / 2.0, y + h / 2.0
    return cx - half_w, cy - half_h, cx + half_w, cy + half_h


# --------------------------------------------------------------------------
# Gate: color_profile_match
# --------------------------------------------------------------------------


def _check_color_profile_match(
    profile: Mapping[str, Any], document: DocumentColor | Mapping[str, Any] | None
) -> list[Finding]:
    required = _as_mapping(profile.get("color_profile"))
    if required is None:
        return [
            Finding(
                check_id="color_profile_match",
                severity=_SEVERITY_ERROR,
                passed=False,
                detail="vendor profile declares no color_profile to match against",
                remediation="fix the vendor profile: color_profile is required",
            )
        ]

    required_name = _text(required.get("icc_name"))
    required_intent = _text(required.get("intent"))
    required_hash = _text(required.get("icc_hash"))

    if required_name is None or required_intent is None:
        return [
            Finding(
                check_id="color_profile_match",
                severity=_SEVERITY_ERROR,
                passed=False,
                detail=(
                    "vendor profile's color_profile is missing icc_name or intent, so "
                    "there is nothing to match against"
                ),
                remediation="fix the vendor profile: icc_name and intent are required",
            )
        ]

    if document is None:
        return [
            Finding(
                check_id="color_profile_match",
                severity=_SEVERITY_ERROR,
                passed=False,
                detail=(
                    "no document colour profile was supplied, so this validator cannot "
                    f"confirm the export embeds '{required_name}'"
                ),
                remediation=(
                    "pass the ICC profile and rendering intent the PDF will embed; an "
                    "unverified colour setup is a failure, not a skip"
                ),
            )
        ]

    if isinstance(document, DocumentColor):
        doc_name, doc_intent, doc_hash = (
            _text(document.icc_name),
            _text(document.intent),
            _text(document.icc_hash),
        )
    else:
        doc_map = _as_mapping(document)
        if doc_map is None:
            return [
                Finding(
                    check_id="color_profile_match",
                    severity=_SEVERITY_ERROR,
                    passed=False,
                    detail="document colour profile is neither a DocumentColor nor a mapping",
                    remediation="pass a DocumentColor",
                )
            ]
        doc_name = _text(doc_map.get("icc_name"))
        doc_intent = _text(doc_map.get("intent"))
        doc_hash = _text(doc_map.get("icc_hash"))

    problems: list[str] = []
    if doc_name is None:
        problems.append("the document declares no ICC profile name")
    elif _icc_key(doc_name) != _icc_key(required_name):
        problems.append(
            f"the document embeds '{doc_name}' but the vendor requires '{required_name}'"
        )
    if doc_intent is None:
        problems.append("the document declares no rendering intent")
    elif doc_intent != required_intent:
        problems.append(
            f"the document renders with intent '{doc_intent}' but the vendor requires "
            f"'{required_intent}'"
        )

    # THE FAIL-OPEN TRAP THIS PROJECT HAS ALREADY PAID FOR ONCE. The obvious
    # form is `if required_hash and doc_hash and required_hash != doc_hash:`,
    # which verifies nothing at all when the document has no hash -- and a
    # document with no hash is precisely the one whose bytes nobody checked.
    # So the condition is on the vendor's side only: if the vendor pinned a
    # profile by hash, an unhashed document FAILS.
    if required_hash is not None:
        if doc_hash is None:
            problems.append(
                "the vendor pins its ICC profile by hash and the document carries no "
                "hash, so the embedded profile is unverified"
            )
        elif doc_hash != required_hash:
            problems.append(
                f"the embedded ICC profile hashes to {doc_hash} but the vendor's is "
                f"{required_hash}"
            )

    if problems:
        return [
            Finding(
                check_id="color_profile_match",
                severity=_SEVERITY_ERROR,
                passed=False,
                detail="colour profile mismatch: " + "; ".join(problems),
                remediation=(
                    f"export with '{required_name}' and intent '{required_intent}' as "
                    "specified by the vendor profile"
                ),
            )
        ]

    findings = [
        Finding(
            check_id="color_profile_match",
            severity=_SEVERITY_ERROR,
            passed=True,
            detail=(
                f"'{doc_name}' with intent '{doc_intent}' matches vendor profile "
                f"{profile.get('profile_version')}"
            ),
        )
    ]
    if required_hash is None:
        # Recorded rather than assumed. vendor_profiles/README.md is explicit
        # that icc_hash is null today, which makes this gate a string
        # comparison and not a colour guarantee. A pass that is weaker than it
        # looks has to say so in the report, or nobody will ever know.
        findings.append(
            Finding(
                check_id="color_profile_match",
                severity=_SEVERITY_WARNING,
                passed=False,
                detail=(
                    f"vendor profile {profile.get('profile_version')} pins no icc_hash, "
                    f"so '{required_name}' was matched by name only; two different ICC "
                    "profiles can share a name"
                ),
                remediation=(
                    "obtain the vendor's ICC profile, ship it, and fill icc_hash in the "
                    "vendor profile"
                ),
            )
        )
    return findings


# --------------------------------------------------------------------------
# Gate: page_count_valid
# --------------------------------------------------------------------------


def _check_page_count(
    pages: Sequence[Any],
    contexts: Sequence[_Ctx],
    profile: Mapping[str, Any],
    unreadable: Sequence[str],
) -> list[Finding]:
    findings: list[Finding] = []
    count = len(pages)

    for description in unreadable:
        findings.append(
            Finding(
                check_id="page_count_valid",
                severity=_SEVERITY_ERROR,
                passed=False,
                measured_value=float(count),
                detail=(
                    f"{description}, so it was invisible to every per-placement gate"
                ),
                remediation=(
                    "fix the album structure; a placement no gate can read is a "
                    "placement no gate checked"
                ),
            )
        )

    # An album of blank pages satisfies every per-placement gate vacuously --
    # no placement is below the DPI floor if there are no placements. That is
    # the classic empty-set pass, and it is caught here rather than left to
    # each gate to remember.
    if not contexts:
        findings.append(
            Finding(
                check_id="page_count_valid",
                severity=_SEVERITY_ERROR,
                passed=False,
                measured_value=float(count),
                detail=(
                    f"{count} page(s) but not a single placement; there is nothing to "
                    "print and every per-placement gate would pass vacuously"
                ),
                remediation="add placements, or do not submit this album",
            )
        )

    indices = [_count(_as_mapping(p).get("page_index")) if _as_mapping(p) else None for p in pages]
    if any(index is None for index in indices):
        findings.append(
            Finding(
                check_id="page_count_valid",
                severity=_SEVERITY_ERROR,
                passed=False,
                measured_value=float(count),
                detail=(
                    f"{sum(1 for i in indices if i is None)} page(s) have a missing or "
                    "invalid page_index, so the page order is undefined"
                ),
                remediation="give every page a distinct integer page_index from 0",
            )
        )
    elif sorted(indices) != list(range(count)):  # type: ignore[type-var]
        findings.append(
            Finding(
                check_id="page_count_valid",
                severity=_SEVERITY_ERROR,
                passed=False,
                measured_value=float(count),
                detail=(
                    f"page indices are not exactly 0..{count - 1}; got "
                    f"{sorted(i for i in indices if i is not None)}"
                ),
                remediation=(
                    "renumber the pages; a duplicate or skipped index silently drops or "
                    "reorders a sheet"
                ),
            )
        )

    limits = _as_mapping(profile.get("page_count"))
    if limits is None:
        findings.append(
            Finding(
                check_id="page_count_valid",
                severity=_SEVERITY_ERROR,
                passed=False,
                measured_value=float(count),
                detail=(
                    "vendor profile declares no page_count limits, so the page count "
                    "cannot be validated"
                ),
                remediation=(
                    "fix the vendor profile: page_count.minimum/maximum/increment are "
                    "required to print"
                ),
            )
        )
        return findings

    minimum = _count(limits.get("minimum"))
    maximum = _count(limits.get("maximum"))
    increment = _count(limits.get("increment"))
    if minimum is None or maximum is None or increment is None or increment < 1:
        findings.append(
            Finding(
                check_id="page_count_valid",
                severity=_SEVERITY_ERROR,
                passed=False,
                measured_value=float(count),
                detail=(
                    "vendor profile's page_count limits are missing or not positive "
                    "integers, so the page count cannot be validated"
                ),
                remediation="fix the vendor profile: minimum, maximum and increment >= 1",
            )
        )
        return findings

    if minimum > maximum or minimum % increment != 0:
        # An incoherent profile would otherwise reject every possible album, or
        # accept one no binder can make. Either way the profile is the defect.
        findings.append(
            Finding(
                check_id="page_count_valid",
                severity=_SEVERITY_ERROR,
                passed=False,
                measured_value=float(count),
                required_value=float(minimum),
                detail=(
                    f"vendor profile is internally inconsistent: minimum={minimum}, "
                    f"maximum={maximum}, increment={increment}"
                ),
                remediation="fix the vendor profile before validating any album against it",
            )
        )
        return findings

    if count < minimum:
        findings.append(
            Finding(
                check_id="page_count_valid",
                severity=_SEVERITY_ERROR,
                passed=False,
                measured_value=float(count),
                required_value=float(minimum),
                detail=(
                    f"{count} pages is {minimum - count} below the vendor minimum of "
                    f"{minimum}"
                ),
                remediation=f"add {minimum - count} page(s)",
            )
        )
    elif count > maximum:
        findings.append(
            Finding(
                check_id="page_count_valid",
                severity=_SEVERITY_ERROR,
                passed=False,
                measured_value=float(count),
                required_value=float(maximum),
                detail=(
                    f"{count} pages is {count - maximum} above the vendor maximum of "
                    f"{maximum}"
                ),
                remediation=f"remove {count - maximum} page(s)",
            )
        )
    elif count % increment != 0:
        short = increment - (count % increment)
        findings.append(
            Finding(
                check_id="page_count_valid",
                severity=_SEVERITY_ERROR,
                passed=False,
                measured_value=float(count),
                required_value=float(increment),
                detail=(
                    f"{count} pages is not a multiple of the binding increment of "
                    f"{increment}"
                ),
                remediation=(
                    f"add {short} page(s) to reach {count + short}, or remove "
                    f"{count % increment}"
                ),
            )
        )

    if findings:
        return findings

    return [
        Finding(
            check_id="page_count_valid",
            severity=_SEVERITY_ERROR,
            passed=True,
            measured_value=float(count),
            required_value=float(minimum),
            detail=(
                f"{count} pages: within {minimum}-{maximum} and on the increment of "
                f"{increment}"
            ),
        )
    ]


# --------------------------------------------------------------------------
# Entry points
# --------------------------------------------------------------------------


def validate_album(
    *,
    pages: Sequence[Any],
    vendor_profile: Any,
    sources: Mapping[str, Any],
    document_color: DocumentColor | Mapping[str, Any] | None = None,
    validated_at: str | None = None,
    guillotine_drift_mm: float = DEFAULT_GUILLOTINE_DRIFT_MM,
) -> PrintValidationReport:
    """Run every hard gate over an album and return a contract-shaped report.

    `pages` are Page-shaped mappings or dataclasses; nothing here imports the
    layout solver. `sources` maps media_id -> SourceImage with ORIENTED pixel
    dimensions. `document_color` is what the PDF will actually embed.

    `validated_at` is an argument rather than a clock reading so two runs over
    the same album produce byte-identical reports.
    """
    profile = _as_mapping(vendor_profile)
    if profile is None:
        raise TypeError("vendor_profile must be a mapping or a dataclass instance")
    if not isinstance(sources, Mapping):
        raise TypeError("sources must be a mapping of media_id -> SourceImage")
    drift = _number(guillotine_drift_mm)
    if drift is None or drift < 0:
        raise ValueError("guillotine_drift_mm must be a finite number >= 0")

    contexts, unreadable = _placement_contexts(pages)

    by_gate: dict[str, list[Finding]] = {}
    for produced in (
        _check_dpi_floor(contexts, profile, sources),
        _check_face_in_trim_zone(contexts, profile, drift),
        _check_face_in_gutter(contexts, profile),
        _check_bleed_coverage(contexts, profile),
        _check_color_profile_match(profile, document_color),
        _check_page_count(pages, contexts, profile, unreadable),
        _check_source_media_available(contexts, sources),
    ):
        for finding in produced:
            by_gate.setdefault(finding.check_id, []).append(finding)

    # GATE_ORDER is the single authority on report order, rather than a sort key
    # that happens to agree with the order the calls above are written in. Two
    # sources of truth for the same ordering drift the first time a gate is
    # added, and the drift is invisible: every check is still there, just not
    # where the reader who works top-down expects it. Within a gate the order is
    # already fixed (rollup first, then violations in sorted placement order).
    findings: list[Finding] = [f for gate in GATE_ORDER for f in by_gate.pop(gate, [])]
    # A gate emitting a check_id GATE_ORDER has never heard of is a bug, but the
    # response to it is not to drop the finding on the floor -- that is the
    # silent data loss this module exists to refuse. Appended, in the order the
    # gates produced them, so the report stays deterministic either way.
    for orphaned in by_gate.values():
        findings.extend(orphaned)

    errors = [
        f
        for f in findings
        if f.counts_toward_totals and f.severity == _SEVERITY_ERROR and not f.passed
    ]
    warnings = [
        f
        for f in findings
        if f.counts_toward_totals and f.severity == _SEVERITY_WARNING and not f.passed
    ]

    status = "pass" if not errors else "fail"

    # Belt and braces against a future edit dropping a gate: the AlbumSpec
    # schema will not accept a "pass" that is missing any of these, and
    # discovering that in the render worker is far too late.
    if status == "pass":
        passed_ids = {f.check_id for f in findings if f.passed}
        missing = sorted(_REQUIRED_GATES - passed_ids)
        if missing:
            raise AssertionError(
                "validator produced a passing report without these mandatory gates: "
                + ", ".join(missing)
            )

    return PrintValidationReport(
        status=status,
        checks=tuple(findings),
        error_count=len(errors),
        warning_count=len(warnings),
        validated_at=validated_at,
        validator_version=VALIDATOR_VERSION,
    )


def validate_album_spec(
    album_spec: Any,
    *,
    sources: Mapping[str, Any],
    document_color: DocumentColor | Mapping[str, Any] | None = None,
    validated_at: str | None = None,
    guillotine_drift_mm: float = DEFAULT_GUILLOTINE_DRIFT_MM,
) -> PrintValidationReport:
    """Convenience wrapper for a whole AlbumSpec.

    Reads `pages` and `vendor_profile` off the spec and ignores any validation
    report already inside it -- re-validating against a spec's own claim of
    having passed would be a gate that grades its own homework.
    """
    spec = _as_mapping(album_spec)
    if spec is None:
        raise TypeError("album_spec must be a mapping or a dataclass instance")
    pages = spec.get("pages")
    if not isinstance(pages, Sequence) or isinstance(pages, (str, bytes)):
        raise TypeError("album_spec.pages must be a sequence of pages")
    return validate_album(
        pages=pages,
        vendor_profile=spec.get("vendor_profile"),
        sources=sources,
        document_color=document_color,
        validated_at=validated_at,
        guillotine_drift_mm=guillotine_drift_mm,
    )
