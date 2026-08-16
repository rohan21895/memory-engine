"""Place selected photos onto printed pages.

Every number in this module is millimetres against a physical sheet of paper, or
normalised [0,1] coordinates into a source image. There are no pixels on the page
and no millimetres in the image; mixing the two is the single easiest way to ship
a book that was printed wrong, and a printed book cannot be patched.

The page coordinate system is the one the contract fixes: origin at the top-left
of the BLEED box, x right, y down. So a full-bleed placement on a 300x300mm trim
with 3mm bleed is the rect (0, 0, 306, 306), the trim line is inset 3mm on every
side, and no coordinate in a valid layout is ever negative.

Four physical constraints drive everything here:

  BLEED   Artwork that reaches a page edge must reach the BLEED edge, not the
          trim line. The guillotine wanders; artwork that stops at the trim line
          leaves a white sliver on the finished book. The corollary is stricter
          and is enforced below: an edge that lands strictly between the trim
          line and the bleed line is a defect either way -- too far in to be
          clean, too far out to be safe -- so frames must be fully bleeding or
          fully inside the safe margin, never in between.

  TRIM    The guillotine tolerance is the safe margin. A face inside it can lose
          a chin. Faces are therefore checked against the safe box, not the trim
          box.

  GUTTER  The inner margin at the spine. Layflat loses ~5mm; perfect bound can
          swallow 14mm. A face there disappears into the binding. Which page
          edge is the spine depends on the side: a LEFT page binds on its RIGHT
          edge. Getting that backwards pushes faces into the spine while the
          numbers all look fine, so it is derived in exactly one place
          (`_spine_side`) and tested directly.

  DPI     effective_dpi = cropped source pixels along an edge / printed inches of
          that edge. Cropped, not total: a crop throws pixels away and a DPI
          computed from the full source is the flattering number, not the true
          one. Layout sizes frames so this stays above the vendor floor rather
          than proposing a placement the print validator will reject.

Aspect is never distorted. A frame's aspect and its crop's aspect must agree in
PIXEL space -- (crop.w * source_width) / (crop.h * source_height) -- because the
crop is normalised against a source that is usually not square. Comparing
crop.w/crop.h against the frame aspect is the plausible wrong version: it is
correct only for square sources and silently squashes everything else.

Determinism: same input -> byte-identical output. Every collection is iterated in
a sorted or caller-given order, every tie is broken explicitly on media_id, and
every emitted number is quantised (mm to 1e-3, normalised to 1e-6, DPI floored to
1e-2) so no float noise reaches the JSON.

Not implemented here, deliberately: the print validator (owned elsewhere -- this
module's job is to produce layouts that pass it), enhancement planning, photo
selection, and rotated placements.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any, Final, Mapping, Sequence

__all__ = [
    "MM_PER_INCH",
    "DEFAULT_INNER_GUTTER_MM",
    "FULL_BLEED",
    "SINGLE_INSET",
    "Face",
    "GridSpec",
    "LayoutError",
    "NormBox",
    "PageGeometry",
    "PageRequest",
    "Photo",
    "RectMm",
    "blank_page",
    "cover_crop",
    "effective_dpi",
    "face_safety",
    "full_bleed_frame",
    "grid_frames",
    "grid_template_id",
    "inset_frame",
    "layout_album",
    "layout_page",
    "max_frame_for_dpi",
    "page_count_is_valid",
    "page_geometry",
    "place",
    "placement_is_print_safe",
]


MM_PER_INCH: Final = 25.4

_MM_DECIMALS: Final = 3
_NORM_DECIMALS: Final = 6
_DPI_DECIMALS: Final = 2

# Frames are quantised to 1e-3 mm, so anything within one quantum of a boundary
# IS on the boundary. A tighter epsilon would report a frame that rounded from
# 305.9999996 to 306.0 as having missed the bleed edge it actually reaches.
#
# The upper bound is not taste either: face margins are emitted at the same
# 1e-3 mm, so a tolerance wider than a quantum or two would report 0.0 about a
# crossing the field can perfectly well describe. One and a half quanta is the
# only value that is loose enough for the rounding and tight enough for the
# reporting; both edges of that are pinned in the tests.
_EDGE_EPS_MM: Final = 1.5e-3

# A frame sized to land exactly on the DPI floor lands on 299.99999999 about as
# often as on 300.00000001, and a validator comparing `>= floor` would reject
# half of them. Shrinking the frame 0.1% first buys ~0.3 DPI of headroom, which
# swamps both float noise and the mm quantisation. 0.1% of a 200mm frame is
# 0.2mm -- invisible on paper, decisive at the gate.
_DPI_SIZING_HEADROOM: Final = 0.999

# The contract's own tolerance for crop-vs-frame aspect agreement (the shipped
# fixture reports "within 0.5%"). Applied to the QUANTISED values that ship.
_ASPECT_TOLERANCE: Final = 5e-3

# Applied to the pre-quantisation arithmetic, where agreement should be exact to
# float precision. A real error in the crop maths shows up far above this, and
# catching it here rather than at the loose contract tolerance is the difference
# between a caught bug and a subtly squashed photo.
_ASPECT_ASSERT_TOLERANCE: Final = 1e-9

# Below this fraction of its template cell a frame is not a smaller photo, it is
# a different layout. Better to reject the arrangement and let a later template
# in the ladder handle it than to emit a stamp floating in white space.
_MIN_FRAME_FRACTION: Final = 0.35

# A page holding more than this is a contact sheet, not an album page.
_MAX_PHOTOS_PER_PAGE: Final = 12

# How far past the content-box edge a face-alignment shift aims. Half a
# millimetre on a 200mm page is invisible; landing exactly on the boundary is
# not, because it makes the verdict depend on somebody else's epsilon.
_ALIGN_GUARD_MM: Final = 0.5

DEFAULT_INNER_GUTTER_MM: Final = 6.0

FULL_BLEED: Final = "full_bleed"
SINGLE_INSET: Final = "single_inset"

_SPINE_BY_SIDE: Final[Mapping[str, str | None]] = {
    # A left-hand page binds on its RIGHT edge and a right-hand page on its LEFT.
    # This mapping is the whole gutter story; everything else derives from it.
    "left": "right",
    "right": "left",
    # Covers and singles have no facing page, so no gutter applies to them.
    "single": None,
    "front_cover": None,
    "back_cover": None,
    "inside_flap": None,
}

_VALID_SIDES: Final = frozenset(_SPINE_BY_SIDE)

_BLAKE3_RE: Final = re.compile(r"^[0-9a-f]{64}$")
_SLUG_RE: Final = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_HEX_COLOR_RE: Final = re.compile(r"^#[0-9a-fA-F]{6}$")


class LayoutError(ValueError):
    """A layout could not be produced without a print defect.

    Raised rather than returning a degraded layout: a print defect is not
    recoverable after the book ships, so the only safe place to fail is here.
    """


# ---------------------------------------------------------------------------
# scalar guards
# ---------------------------------------------------------------------------


def _finite(value: Any, name: str) -> float:
    """Coerce to float and reject NaN/inf.

    NaN is the reason this exists. Every threshold in this module is a
    comparison, and every comparison against NaN is False -- so a NaN dimension
    does not raise, it quietly passes `dpi < floor`, quietly fails `face in safe
    box`, and produces a layout full of plausible numbers built on nothing.
    """
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise LayoutError(f"{name} must be a number, got {value!r}") from exc
    if not math.isfinite(result):
        raise LayoutError(f"{name} must be a finite number, got {value!r}")
    return result


def _positive(value: Any, name: str) -> float:
    result = _finite(value, name)
    if result <= 0.0:
        raise LayoutError(f"{name} must be greater than zero, got {result!r}")
    return result


def _non_negative(value: Any, name: str) -> float:
    result = _finite(value, name)
    if result < 0.0:
        raise LayoutError(f"{name} must not be negative, got {result!r}")
    return result


def _round_mm(value: float) -> float:
    rounded = round(value, _MM_DECIMALS)
    # -0.0 serialises as "-0.0"; harmless arithmetically, noise in a diff of two
    # supposedly identical AlbumSpecs.
    return 0.0 if rounded == 0.0 else rounded


def _round_norm(value: float) -> float:
    rounded = round(value, _NORM_DECIMALS)
    return 0.0 if rounded == 0.0 else rounded


def _floor_to(value: float, decimals: int) -> float:
    """Quantise DOWNWARD.

    Used for effective_dpi and face margins, both of which are compared against
    a floor by the validator. Rounding to nearest would let a true 299.996 DPI
    present itself as 300.0 and clear a gate it does not actually clear; the
    error must always land on the safe side of the physical constraint.
    """
    scale = 10.0**decimals
    return math.floor(value * scale) / scale


def _snap_to_edge(value: float, *, eps: float = _EDGE_EPS_MM) -> float:
    """Collapse a clearance that is within one quantum of zero onto zero.

    Every boolean in this module treats a rect within `eps` of a boundary as
    ON the boundary (`RectMm.contains` allows -eps, `RectMm.intersects` demands
    more than eps of overlap). The reported NUMBER has to agree, and it cannot
    agree by itself: `_floor_to` quantises downward, so a clearance of -1e-13 --
    which every boolean here calls "on the line" -- floors to -0.001 and ships a
    record whose boolean says safe and whose number says a face crossed. The
    print validator reads both and rejects that pair outright
    (validator.py: "all_faces_in_safe_zone=true while min_face_margin_mm ... is
    negative"), so an album that is physically fine fails its export gate on
    float noise. Snapping first is what makes the two answers one answer.
    """
    return 0.0 if abs(value) <= eps else value


# ---------------------------------------------------------------------------
# geometry primitives
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class NormBox:
    """Axis-aligned box in normalised ORIENTED-image coordinates, per contract."""

    x: float
    y: float
    w: float
    h: float

    def __post_init__(self) -> None:
        for name in ("x", "y", "w", "h"):
            _finite(getattr(self, name), f"NormBox.{name}")
        if self.w <= 0.0 or self.h <= 0.0:
            raise LayoutError(f"NormBox must have positive extent, got {self!r}")
        if self.x < 0.0 or self.y < 0.0:
            raise LayoutError(f"NormBox origin must be non-negative, got {self!r}")
        # 1e-9 slack absorbs float reconstruction of a box that mathematically
        # ends at exactly 1.0; anything larger is a genuine out-of-image box.
        if self.x + self.w > 1.0 + 1e-9 or self.y + self.h > 1.0 + 1e-9:
            raise LayoutError(f"NormBox must stay inside the image, got {self!r}")

    @property
    def right(self) -> float:
        return self.x + self.w

    @property
    def bottom(self) -> float:
        return self.y + self.h

    @property
    def area(self) -> float:
        return self.w * self.h

    def as_dict(self) -> dict[str, Any]:
        return {
            "x": _round_norm(self.x),
            "y": _round_norm(self.y),
            "w": _round_norm(self.w),
            "h": _round_norm(self.h),
            "rotation_deg": 0,
        }


@dataclass(frozen=True, slots=True)
class RectMm:
    """Axis-aligned rect on the page, mm from the bleed-box origin."""

    x_mm: float
    y_mm: float
    width_mm: float
    height_mm: float

    def __post_init__(self) -> None:
        _finite(self.x_mm, "RectMm.x_mm")
        _finite(self.y_mm, "RectMm.y_mm")
        _positive(self.width_mm, "RectMm.width_mm")
        _positive(self.height_mm, "RectMm.height_mm")

    @property
    def right_mm(self) -> float:
        return self.x_mm + self.width_mm

    @property
    def bottom_mm(self) -> float:
        return self.y_mm + self.height_mm

    @property
    def area_mm2(self) -> float:
        return self.width_mm * self.height_mm

    @property
    def aspect(self) -> float:
        return self.width_mm / self.height_mm

    @property
    def center(self) -> tuple[float, float]:
        return (self.x_mm + self.width_mm / 2.0, self.y_mm + self.height_mm / 2.0)

    def rounded(self) -> RectMm:
        return RectMm(
            _round_mm(self.x_mm),
            _round_mm(self.y_mm),
            _round_mm(self.width_mm),
            _round_mm(self.height_mm),
        )

    def clearance_of(self, inner: RectMm) -> float:
        """Signed mm by which `inner` sits inside self. Negative means it crossed.

        Signed rather than boolean because "how close did the nearest face get to
        the spine" is the number the review queue and FaceSafety.min_face_margin_mm
        both want, and a boolean throws it away.
        """
        return min(
            inner.x_mm - self.x_mm,
            self.right_mm - inner.right_mm,
            inner.y_mm - self.y_mm,
            self.bottom_mm - inner.bottom_mm,
        )

    def separation_from(self, other: RectMm) -> float:
        """Signed mm of clear space between self and `other`.

        Positive: they are apart by that much. Zero: edge contact. Negative: they
        overlap, and the magnitude is the shallowest penetration depth -- which
        is the number "how far into the gutter did this face get" wants.

        The boolean form (`intersects`) is defined ON this, not beside it. Two
        independent implementations of "do these overlap" and "by how much" is
        exactly how `faces_in_gutter` and `min_face_margin_mm` came to disagree
        about the same face.
        """
        return max(
            other.x_mm - self.right_mm,
            self.x_mm - other.right_mm,
            other.y_mm - self.bottom_mm,
            self.y_mm - other.bottom_mm,
        )

    def contains(self, inner: RectMm, *, eps: float = _EDGE_EPS_MM) -> bool:
        return self.clearance_of(inner) >= -eps

    def intersects(self, other: RectMm, *, eps: float = _EDGE_EPS_MM) -> bool:
        """True only for genuine overlap; edge contact is not an intersection.

        `face_safety` deliberately does NOT call this -- it keeps the signed
        number and takes its sign, so its count and its margin are one fact.
        This is here for callers that only want the verdict, and it is defined
        on `separation_from` so that it can never mean something else.
        """
        return self.separation_from(other) < -eps

    def as_dict(self) -> dict[str, Any]:
        return {
            "x_mm": _round_mm(self.x_mm),
            "y_mm": _round_mm(self.y_mm),
            "width_mm": _round_mm(self.width_mm),
            "height_mm": _round_mm(self.height_mm),
            "rotation_deg": 0,
        }


def _inset(rect: RectMm, left: float, top: float, right: float, bottom: float) -> RectMm:
    width = rect.width_mm - left - right
    height = rect.height_mm - top - bottom
    if width <= 0.0 or height <= 0.0:
        raise LayoutError(
            f"insets ({left}, {top}, {right}, {bottom}) leave no area inside {rect!r}"
        )
    return RectMm(rect.x_mm + left, rect.y_mm + top, width, height)


# ---------------------------------------------------------------------------
# inputs
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Face:
    """One detected face in a source image, in oriented-image coordinates."""

    face_id: str
    box: NormBox
    is_subject: bool = True

    def __post_init__(self) -> None:
        if not _BLAKE3_RE.match(self.face_id):
            raise LayoutError(
                f"face_id must be a lowercase 64-hex BLAKE3 digest, got {self.face_id!r}"
            )


@dataclass(frozen=True, slots=True)
class Photo:
    """A selected source image, as far as layout needs to know about it.

    pixel_width/pixel_height are of the ORIENTED image (EXIF rotation already
    applied), matching NormalizedBox in the contract. Feeding unoriented
    dimensions alongside oriented boxes transposes every crop on a portrait
    photo without raising anything.
    """

    media_id: str
    pixel_width: int
    pixel_height: int
    faces: tuple[Face, ...] = ()
    salience: NormBox | None = None

    def __post_init__(self) -> None:
        if not _BLAKE3_RE.match(self.media_id):
            raise LayoutError(
                f"media_id must be a lowercase 64-hex BLAKE3 digest, got {self.media_id!r}"
            )
        for name in ("pixel_width", "pixel_height"):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise LayoutError(f"Photo.{name} must be a positive int, got {value!r}")
        if not isinstance(self.faces, tuple):
            raise LayoutError("Photo.faces must be a tuple (hashable and ordered)")

    @property
    def aspect(self) -> float:
        return self.pixel_width / self.pixel_height


@dataclass(frozen=True, slots=True)
class GridSpec:
    columns: int
    rows: int
    gutter_mm: float = DEFAULT_INNER_GUTTER_MM

    def __post_init__(self) -> None:
        for name in ("columns", "rows"):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise LayoutError(f"GridSpec.{name} must be a positive int, got {value!r}")
        _non_negative(self.gutter_mm, "GridSpec.gutter_mm")

    @property
    def cells(self) -> int:
        return self.columns * self.rows

    def as_dict(self) -> dict[str, Any]:
        return {
            "columns": self.columns,
            "rows": self.rows,
            "gutter_mm": _round_mm(self.gutter_mm),
        }


@dataclass(frozen=True, slots=True)
class PageRequest:
    """One page's worth of already-selected photos.

    Layout does not decide WHICH photos go together or in what order -- that is
    selection's job and it needs chronology, people and diversity that this
    module has no access to. Taking the grouping as input keeps the boundary
    honest, and keeps the output a pure function of it.
    """

    photos: tuple[Photo, ...]
    template: str | None = None
    section_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.photos, tuple):
            raise LayoutError("PageRequest.photos must be a tuple")
        if self.section_id is not None and not _SLUG_RE.match(self.section_id):
            raise LayoutError(f"section_id must be a slug, got {self.section_id!r}")


# ---------------------------------------------------------------------------
# page geometry
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PageGeometry:
    """Every box on one physical page, derived once from the vendor profile.

    Two inner boxes, because two different rules apply:

      `safe_box`    trim inset by the safe margin. This is the guillotine rule,
                    and it is what a face is measured against for the trim-zone
                    check. It knows nothing about the binding.

      `content_box` safe_box with the gutter taken off the spine side. This is
                    where non-bleeding frames are laid out AND where faces must
                    end up. A photo may still cross into the gutter -- that is
                    what a spread-crossing full-bleed image does -- but it has to
                    ask for it by bleeding, because an inset photo whose inner
                    4mm is eaten by a perfect-bound spine is a photo with a
                    lopsided margin that nobody chose.
    """

    side: str
    bleed_mm: float
    safe_margin_mm: float
    gutter_mm: float
    spine_side: str | None
    bleed_box: RectMm
    trim_box: RectMm
    safe_box: RectMm
    content_box: RectMm
    gutter_band: RectMm | None


def _spine_side(side: str) -> str | None:
    if side not in _VALID_SIDES:
        raise LayoutError(f"unknown page side {side!r}; expected one of {sorted(_VALID_SIDES)}")
    return _SPINE_BY_SIDE[side]


def page_geometry(profile: Mapping[str, Any], side: str) -> PageGeometry:
    """Derive the boxes for one page of `profile`."""
    spine = _spine_side(side)

    trim = profile.get("trim_size_mm")
    if not isinstance(trim, Mapping):
        raise LayoutError("vendor profile is missing trim_size_mm")
    trim_w = _positive(trim.get("width_mm"), "trim_size_mm.width_mm")
    trim_h = _positive(trim.get("height_mm"), "trim_size_mm.height_mm")

    bleed = _non_negative(profile.get("bleed_mm"), "bleed_mm")
    safe = _non_negative(profile.get("safe_margin_mm"), "safe_margin_mm")
    # gutter_mm carries a schema default of 0; a profile that omits it genuinely
    # has no gutter (a single-sheet product), which is different from unknown.
    gutter = _non_negative(profile.get("gutter_mm", 0.0), "gutter_mm")

    bleed_box = RectMm(0.0, 0.0, trim_w + 2.0 * bleed, trim_h + 2.0 * bleed)
    trim_box = RectMm(bleed, bleed, trim_w, trim_h)
    safe_box = _inset(trim_box, safe, safe, safe, safe)

    if spine is None or gutter <= 0.0:
        gutter_band = None
        content_box = safe_box
    elif spine == "right":
        # Band runs from `gutter` inside the trim line out to the bleed edge, so
        # a face that has already crossed the spine still registers as in it.
        gutter_band = RectMm(
            trim_box.right_mm - gutter,
            0.0,
            bleed_box.right_mm - (trim_box.right_mm - gutter),
            bleed_box.height_mm,
        )
        # max(0, gutter - safe): the safe margin already ate part of the gutter
        # band. Subtracting the full gutter again would double-count it, which
        # on the layflat profile (gutter 5 < margin 8) would silently shave 5mm
        # off every inner edge for no physical reason.
        content_box = _inset(safe_box, 0.0, 0.0, max(0.0, gutter - safe), 0.0)
    else:
        gutter_band = RectMm(0.0, 0.0, trim_box.x_mm + gutter, bleed_box.height_mm)
        content_box = _inset(safe_box, max(0.0, gutter - safe), 0.0, 0.0, 0.0)

    return PageGeometry(
        side=side,
        bleed_mm=bleed,
        safe_margin_mm=safe,
        gutter_mm=gutter,
        spine_side=spine,
        bleed_box=bleed_box,
        trim_box=trim_box,
        safe_box=safe_box,
        content_box=content_box,
        gutter_band=gutter_band,
    )


# ---------------------------------------------------------------------------
# frames
# ---------------------------------------------------------------------------


def full_bleed_frame(geom: PageGeometry) -> RectMm:
    """The whole bleed box: artwork past the trim on all four edges."""
    return geom.bleed_box.rounded()


def inset_frame(
    geom: PageGeometry,
    aspect: float,
    *,
    max_width_mm: float | None = None,
    max_height_mm: float | None = None,
) -> RectMm:
    """Largest rect of `aspect` centred in the content box, optionally capped."""
    aspect = _positive(aspect, "aspect")
    box = geom.content_box

    width = min(box.width_mm, box.height_mm * aspect)
    if max_width_mm is not None:
        width = min(width, _positive(max_width_mm, "max_width_mm"))
    if max_height_mm is not None:
        width = min(width, _positive(max_height_mm, "max_height_mm") * aspect)
    height = width / aspect

    cx, cy = box.center
    return RectMm(cx - width / 2.0, cy - height / 2.0, width, height).rounded()


def grid_frames(geom: PageGeometry, spec: GridSpec) -> list[RectMm]:
    """Cells of `spec` tiling the content box, in reading order (row-major)."""
    box = geom.content_box
    cell_w = (box.width_mm - (spec.columns - 1) * spec.gutter_mm) / spec.columns
    cell_h = (box.height_mm - (spec.rows - 1) * spec.gutter_mm) / spec.rows
    if cell_w <= 0.0 or cell_h <= 0.0:
        raise LayoutError(
            f"{spec.columns}x{spec.rows} grid with {spec.gutter_mm}mm gutters does not "
            f"fit the {box.width_mm}x{box.height_mm}mm content box"
        )
    frames: list[RectMm] = []
    for row in range(spec.rows):
        for col in range(spec.columns):
            frames.append(
                RectMm(
                    box.x_mm + col * (cell_w + spec.gutter_mm),
                    box.y_mm + row * (cell_h + spec.gutter_mm),
                    cell_w,
                    cell_h,
                ).rounded()
            )
    return frames


def grid_template_id(spec: GridSpec) -> str:
    return f"grid_{spec.columns}x{spec.rows}"


# ---------------------------------------------------------------------------
# cropping
# ---------------------------------------------------------------------------


def _cover_window(photo: Photo, aspect: float) -> tuple[float, float]:
    """Largest (width, height) in SOURCE PIXELS of `aspect` that fits the image.

    One implementation, used by both the crop and the max-printable-size
    calculation. Two implementations of the same formula is how the max size and
    the actual size come to disagree by a few percent, which is invisible in
    every test that does not compare them.
    """
    width = float(photo.pixel_width)
    height = float(photo.pixel_height)
    if width / height > aspect:
        crop_h = height
        crop_w = aspect * height
    else:
        crop_w = width
        crop_h = width / aspect
    # Guard the degenerate float case where the "fits exactly" branch overshoots.
    return (min(crop_w, width), min(crop_h, height))


def _focus_box(photo: Photo) -> NormBox | None:
    """Union of the boxes the crop should try to keep, or None to centre.

    Subject faces take priority over salience; only when there are no subject
    faces does the salience box stand in. Unioning them unconditionally would
    let a wide salience box drag the crop off the people, which is the one thing
    an album crop must never do.
    """
    subjects = [f.box for f in photo.faces if f.is_subject]
    boxes = subjects if subjects else ([photo.salience] if photo.salience else [])
    if not boxes:
        return None
    x = min(b.x for b in boxes)
    y = min(b.y for b in boxes)
    right = max(b.right for b in boxes)
    bottom = max(b.bottom for b in boxes)
    return NormBox(x, y, min(right, 1.0) - x, min(bottom, 1.0) - y)


def cover_crop(photo: Photo, aspect: float, *, focus: NormBox | None = None) -> NormBox:
    """Largest crop of `aspect`, centred on the focus box (never distorting).

    "Cover" as in CSS object-fit: the crop always fills the frame completely, so
    the frame is never letterboxed and the photo is never squashed to fit. The
    price is that content leaves the frame, which is why the crop is positioned
    on the faces rather than on the middle of the sensor.
    """
    aspect = _positive(aspect, "aspect")
    crop_w, crop_h = _cover_window(photo, aspect)
    width = float(photo.pixel_width)
    height = float(photo.pixel_height)

    if focus is None:
        focus = _focus_box(photo)

    if focus is None:
        cx = (width - crop_w) / 2.0
        cy = (height - crop_h) / 2.0
    else:
        focus_cx = (focus.x + focus.w / 2.0) * width
        focus_cy = (focus.y + focus.h / 2.0) * height
        cx = focus_cx - crop_w / 2.0
        cy = focus_cy - crop_h / 2.0

    cx = min(max(cx, 0.0), width - crop_w)
    cy = min(max(cy, 0.0), height - crop_h)
    return _pixels_to_norm(photo, cx, cy, crop_w, crop_h)


def _pixels_to_norm(photo: Photo, cx: float, cy: float, cw: float, ch: float) -> NormBox:
    width = float(photo.pixel_width)
    height = float(photo.pixel_height)
    x = min(max(cx / width, 0.0), 1.0)
    y = min(max(cy / height, 0.0), 1.0)
    w = min(cw / width, 1.0 - x)
    h = min(ch / height, 1.0 - y)
    return NormBox(x, y, w, h)


def _quantise_crop(crop: NormBox) -> NormBox:
    """Round to the emitted precision. Containment is re-checked, not repaired.

    The obvious version of this function silently pulls the origin back when
    rounding pushes x+w past 1. That repair cannot fire: the input already
    satisfies x+w <= 1, so the two 7th-digit remainders are complementary and
    at most one of x and w can round up. Writing the repair anyway would leave
    an untestable branch that quietly absorbs the one thing it could ever
    catch -- a genuine bug upstream producing an out-of-image crop.

    So the NormBox constructor stays the single enforcement point and raises.
    An impossible condition should be loud if it ever becomes possible.
    """
    return NormBox(
        _round_norm(crop.x),
        _round_norm(crop.y),
        _round_norm(crop.w),
        _round_norm(crop.h),
    )


def _assert_aspect_matches(
    photo: Photo, crop: NormBox, frame: RectMm, tolerance: float
) -> None:
    """Crop and frame aspect must agree IN PIXEL SPACE.

    crop.w/crop.h is the wrong ratio unless the source is square: the crop is
    normalised against the source's own dimensions, so the printed aspect is
    (crop.w * W) / (crop.h * H). A layout that compares the normalised ratio
    passes its own check and hands the renderer a photo it must squash.

    Called three times in `place()`, and only the third -- after quantisation,
    at the loose contract tolerance -- can fire on any input. `cover_crop` and
    `_align_crop_for_faces` are exact: a 120k-case random sweep over source
    sizes, frame aspects and face positions put their worst relative aspect
    error at 8e-16, seven orders of magnitude under the 1e-9 the first two calls
    check against. Those two are tripwires for a future bug inside those
    functions, not conditions any caller can reach, and deleting them changes
    nothing observable today. They are kept because the alternative to catching
    a crop-maths bug here is catching it at the 0.5% contract tolerance, i.e.
    after it has silently squashed a photo by half a percent.
    """
    crop_aspect = (crop.w * photo.pixel_width) / (crop.h * photo.pixel_height)
    frame_aspect = frame.aspect
    relative = abs(crop_aspect - frame_aspect) / frame_aspect
    if relative > tolerance:
        raise LayoutError(
            f"crop aspect {crop_aspect:.9f} does not match frame aspect "
            f"{frame_aspect:.9f} (relative error {relative:.3e} > {tolerance:.1e}) "
            f"for media {photo.media_id[:8]}"
        )


# ---------------------------------------------------------------------------
# DPI
# ---------------------------------------------------------------------------


def effective_dpi(photo: Photo, crop: NormBox, frame: RectMm) -> float:
    """Cropped source pixels per printed inch, quantised downward.

    Both edges are computed and the smaller is reported. They should be equal --
    the crop was built to match the frame aspect -- so a disagreement beyond the
    contract tolerance means the crop and the frame have drifted apart and the
    DPI number would be meaningless. That raises rather than silently reporting
    the flattering edge.
    """
    dpi_x = (crop.w * photo.pixel_width) * MM_PER_INCH / frame.width_mm
    dpi_y = (crop.h * photo.pixel_height) * MM_PER_INCH / frame.height_mm
    smaller = min(dpi_x, dpi_y)
    if abs(dpi_x - dpi_y) / smaller > _ASPECT_TOLERANCE:
        raise LayoutError(
            f"effective DPI disagrees between axes ({dpi_x:.3f} vs {dpi_y:.3f}) for "
            f"media {photo.media_id[:8]}; crop and frame aspects have drifted"
        )
    return _floor_to(smaller, _DPI_DECIMALS)


def max_frame_for_dpi(photo: Photo, aspect: float, dpi_floor: float) -> tuple[float, float]:
    """Largest (width_mm, height_mm) of `aspect` this photo can print at `dpi_floor`.

    This is what stops layout proposing a 2MP phone photo across a full spread:
    the size is derived from the pixels the crop will actually keep, not from the
    photo's total pixel count.
    """
    aspect = _positive(aspect, "aspect")
    dpi_floor = _positive(dpi_floor, "dpi_floor")
    crop_w, crop_h = _cover_window(photo, aspect)
    width_mm = crop_w * MM_PER_INCH / dpi_floor
    height_mm = crop_h * MM_PER_INCH / dpi_floor
    return (width_mm, height_mm)


# ---------------------------------------------------------------------------
# faces on the page
# ---------------------------------------------------------------------------


def _face_rect_mm(face: Face, crop: NormBox, frame: RectMm) -> RectMm | None:
    """Where a face lands on the page, or None if the crop excluded it entirely.

    The face is clipped to the crop first: only the visible part is printed, and
    a rect extending past the frame would be measured against the safe box as if
    ink existed where it does not.
    """
    x0 = max(face.box.x, crop.x)
    y0 = max(face.box.y, crop.y)
    x1 = min(face.box.right, crop.right)
    y1 = min(face.box.bottom, crop.bottom)
    if x1 <= x0 or y1 <= y0:
        return None

    u0 = (x0 - crop.x) / crop.w
    v0 = (y0 - crop.y) / crop.h
    u1 = (x1 - crop.x) / crop.w
    v1 = (y1 - crop.y) / crop.h
    return RectMm(
        frame.x_mm + u0 * frame.width_mm,
        frame.y_mm + v0 * frame.height_mm,
        max((u1 - u0) * frame.width_mm, 1e-9),
        max((v1 - v0) * frame.height_mm, 1e-9),
    )


def _is_clipped(face: Face, crop: NormBox) -> bool:
    eps = 1e-9
    return (
        face.box.x < crop.x - eps
        or face.box.y < crop.y - eps
        or face.box.right > crop.right + eps
        or face.box.bottom > crop.bottom + eps
    )


def face_safety(
    photo: Photo, crop: NormBox, frame: RectMm, geom: PageGeometry
) -> dict[str, Any]:
    """The FaceSafety block for one placement.

    Counts cover every face at least partly visible in the crop, subject or not:
    the print validator sees faces, not our opinion of whose face matters, and a
    background face losing its chin at the trim line is still a defect.

    ONE number per face per hazard, and every field below is a reduction of those
    numbers -- the counts, the boolean and the margin alike. The previous shape
    of this function asked `safe_box.contains(...)` for the boolean and measured
    `content_box.clearance_of(...)` for the number: two comparators, two boxes,
    two epsilons, and a record that could say `all_faces_in_safe_zone: true`
    beside a negative `min_face_margin_mm`. That pair is not merely untidy, it is
    a hard export block in the print validator's self-contradiction check.

    Which box the margin is measured against (P2): the contract defines it as
    "distance from the nearest face to the nearest unsafe boundary", and there
    are two unsafe boundaries -- the safe line and the gutter -- so the margin is
    the MINIMUM of the two clearances, which is exactly the content box. The
    print validator's trim check reconstructs the distance to the blade as
    `margin + safe_margin_mm`, i.e. it reads the margin as safe-box-relative.
    The two agree whenever the safe line is the nearer hazard, and where they
    differ this number is the SMALLER one, so the validator's reconstruction is
    pessimistic and never flattering. That is the only direction a print gate
    may err in, and reporting the safe-box clearance instead would hide how
    close a face got to the spine -- the number the review queue exists to read.
    """
    # The sort is currently unobservable and is kept anyway. Every consumer of
    # this order below is an order-invariant reduction -- a `len`, a `min`, and
    # a list that is sorted again on its own key -- so removing it cannot change
    # a byte today (20k randomised differential cases, zero differences). It
    # stays because the next field added here is as likely as not to be
    # order-sensitive, and an emitted order that depends on detector output
    # order is a determinism bug that shows up as an unstable digest, weeks
    # later, in someone else's package.
    visible: list[tuple[Face, RectMm]] = []
    for face in sorted(photo.faces, key=lambda f: f.face_id):
        rect = _face_rect_mm(face, crop, frame)
        if rect is not None:
            visible.append((face, rect))

    in_gutter = 0
    in_trim = 0
    margins: list[float] = []
    for _, rect in visible:
        # Positive = clear of the hazard, negative = crossed it, snapped so that
        # "on the line" is one value and not a coin flip on the sign of 1e-13.
        trim_clearance = _snap_to_edge(geom.safe_box.clearance_of(rect))
        gutter_clearance = (
            math.inf
            if geom.gutter_band is None
            else _snap_to_edge(geom.gutter_band.separation_from(rect))
        )
        if trim_clearance < 0.0:
            in_trim += 1
        if gutter_clearance < 0.0:
            in_gutter += 1
        margins.append(min(trim_clearance, gutter_clearance))

    cropped = sorted(
        face.face_id for face, _ in visible if _is_clipped(face, crop)
    )

    # No faces is vacuously safe. Stated rather than falling out of an `all()`,
    # because "there were no faces" and "every face was safe" are answers to
    # different questions and only one of them means the crop was checked.
    #
    # This is now the same test as `min_face_margin_mm >= 0` rather than a
    # second opinion about it: both counts are `clearance < 0` over the same
    # snapped numbers the margin is the minimum of.
    all_safe = in_gutter == 0 and in_trim == 0

    return {
        "face_count": len(visible),
        "all_faces_in_safe_zone": all_safe,
        "faces_in_gutter": in_gutter,
        "faces_in_trim_zone": in_trim,
        "min_face_margin_mm": (
            _floor_to(min(margins), _MM_DECIMALS) if margins else None
        ),
        "cropped_face_ids": cropped,
    }


def _align_crop_for_faces(
    photo: Photo, crop: NormBox, frame: RectMm, geom: PageGeometry
) -> NormBox:
    """Slide the crop so the visible faces land inside the face-safe box.

    Moving the crop window LEFT in the source moves its content RIGHT on the
    page -- the signs are inverted, and inverting them the other way is a change
    that makes every number move plausibly while pushing faces further into the
    spine. Only the slack left over by the cover crop is available, so this can
    fail; it reports by leaving the faces where they are, and the caller checks
    face_safety rather than trusting this to have worked.
    """
    rects = [
        rect
        for rect in (
            _face_rect_mm(face, crop, frame)
            for face in sorted(photo.faces, key=lambda f: f.face_id)
        )
        if rect is not None
    ]
    if not rects:
        return crop

    bbox = RectMm(
        min(r.x_mm for r in rects),
        min(r.y_mm for r in rects),
        max(r.right_mm for r in rects) - min(r.x_mm for r in rects),
        max(r.bottom_mm for r in rects) - min(r.y_mm for r in rects),
    )
    target = geom.content_box

    def shift(lo_need: float, hi_need: float) -> float:
        # Both positive means the faces span wider than the safe box; no
        # translation fixes that, so do not pretend one does.
        if lo_need > 0.0 and hi_need > 0.0:
            return 0.0
        # Overshoot by the guard band rather than landing the face exactly on
        # the boundary. Exactly on it is a coin flip against whatever epsilon
        # the print validator uses, and that epsilon is not ours to assume --
        # a face parked on the gutter line would pass here and fail there.
        if lo_need > 0.0:
            return lo_need + _ALIGN_GUARD_MM
        if hi_need > 0.0:
            return -(hi_need + _ALIGN_GUARD_MM)
        return 0.0

    dx_mm = shift(max(0.0, target.x_mm - bbox.x_mm), max(0.0, bbox.right_mm - target.right_mm))
    dy_mm = shift(max(0.0, target.y_mm - bbox.y_mm), max(0.0, bbox.bottom_mm - target.bottom_mm))
    if dx_mm == 0.0 and dy_mm == 0.0:
        return crop

    crop_w_px = crop.w * photo.pixel_width
    crop_h_px = crop.h * photo.pixel_height
    cx = crop.x * photo.pixel_width - dx_mm * crop_w_px / frame.width_mm
    cy = crop.y * photo.pixel_height - dy_mm * crop_h_px / frame.height_mm

    cx = min(max(cx, 0.0), photo.pixel_width - crop_w_px)
    cy = min(max(cy, 0.0), photo.pixel_height - crop_h_px)
    return _pixels_to_norm(photo, cx, cy, crop_w_px, crop_h_px)


# ---------------------------------------------------------------------------
# bleed
# ---------------------------------------------------------------------------


def _bleeds_for(frame: RectMm, geom: PageGeometry) -> list[str]:
    """Derive the bleed edge list from geometry, and reject the cut zone.

    Derived, never accepted as input: a caller-supplied `bleeds: ["left"]` on a
    frame that stops at the trim line declares coverage that does not exist, and
    the printed result is a white sliver down the edge of the book.

    An edge that lands strictly between the trim line and the bleed line is
    rejected outright. It is not artwork that bleeds (it does not reach the
    edge) and it is not artwork that is safe (it is inside the guillotine's
    tolerance), so there is no correct thing to record about it.
    """
    box = geom.bleed_box
    safe = geom.safe_box
    edges: list[str] = []

    checks = (
        ("left", frame.x_mm, box.x_mm, safe.x_mm, +1.0),
        ("top", frame.y_mm, box.y_mm, safe.y_mm, +1.0),
        ("right", frame.right_mm, box.right_mm, safe.right_mm, -1.0),
        ("bottom", frame.bottom_mm, box.bottom_mm, safe.bottom_mm, -1.0),
    )
    for name, value, bleed_line, safe_line, sign in checks:
        # sign +1: "further out" means smaller. sign -1: means larger.
        outside_or_on_bleed = sign * (value - bleed_line) <= _EDGE_EPS_MM
        inside_or_on_safe = sign * (value - safe_line) >= -_EDGE_EPS_MM
        if outside_or_on_bleed:
            edges.append(name)
        elif not inside_or_on_safe:
            raise LayoutError(
                f"{name} edge of frame at {value:.3f}mm lands in the cut zone: it must "
                f"either reach the bleed line at {bleed_line:.3f}mm or stop at or "
                f"inside the safe line at {safe_line:.3f}mm"
            )
    # Contract order, not discovery order: two identical layouts must serialise
    # identically, and set/append order is not a guarantee.
    return [edge for edge in ("top", "bottom", "left", "right") if edge in edges]


# ---------------------------------------------------------------------------
# placement
# ---------------------------------------------------------------------------


def place(
    photo: Photo,
    frame: RectMm,
    geom: PageGeometry,
    *,
    placement_id: str,
    dpi_floor: float,
    is_hero: bool = False,
    z_index: int = 0,
    caption: str | None = None,
    align_faces: bool = True,
) -> dict[str, Any]:
    """Build one contract-shaped Placement, or raise.

    Raises rather than emitting a placement below the DPI floor. Callers size
    frames with `max_frame_for_dpi` beforehand; this check is the backstop, and
    it is loud on purpose -- a silent clamp here would turn a sizing bug into a
    blurry book.
    """
    if not _SLUG_RE.match(placement_id):
        raise LayoutError(f"placement_id must be a slug, got {placement_id!r}")
    dpi_floor = _positive(dpi_floor, "dpi_floor")

    # Quantise BEFORE deriving anything: the crop must match the aspect of the
    # frame that actually ships, not of the full-precision one it came from.
    frame = frame.rounded()

    if not geom.bleed_box.contains(frame):
        raise LayoutError(
            f"frame {frame!r} extends outside the page bleed box {geom.bleed_box!r}"
        )

    bleeds = _bleeds_for(frame, geom)

    crop = cover_crop(photo, frame.aspect)
    _assert_aspect_matches(photo, crop, frame, _ASPECT_ASSERT_TOLERANCE)
    if align_faces:
        crop = _align_crop_for_faces(photo, crop, frame, geom)
        _assert_aspect_matches(photo, crop, frame, _ASPECT_ASSERT_TOLERANCE)

    crop = _quantise_crop(crop)
    _assert_aspect_matches(photo, crop, frame, _ASPECT_TOLERANCE)

    dpi = effective_dpi(photo, crop, frame)
    if dpi < dpi_floor:
        raise LayoutError(
            f"media {photo.media_id[:8]} at {frame.width_mm}x{frame.height_mm}mm gives "
            f"{dpi} DPI, below the vendor floor of {dpi_floor}; the print validator "
            f"would reject this, so layout must not propose it"
        )

    return {
        "placement_id": placement_id,
        "media_id": photo.media_id,
        "frame": frame.as_dict(),
        "crop": crop.as_dict(),
        "effective_dpi": dpi,
        "z_index": z_index,
        "bleeds": bleeds,
        "is_hero": is_hero,
        "face_safety": face_safety(photo, crop, frame, geom),
        "enhancement_ops": [],
        "caption": caption,
        "border": None,
    }


def placement_is_print_safe(placement: Mapping[str, Any], dpi_floor: float) -> bool:
    """The subset of the hard gates layout is able to answer for one placement."""
    safety = placement.get("face_safety") or {}
    dpi = _finite(placement.get("effective_dpi"), "effective_dpi")
    return (
        dpi >= _positive(dpi_floor, "dpi_floor")
        and bool(safety.get("all_faces_in_safe_zone", False))
        and int(safety.get("faces_in_gutter", 0)) == 0
        and int(safety.get("faces_in_trim_zone", 0)) == 0
    )


# ---------------------------------------------------------------------------
# page planning
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Arrangement:
    template_id: str
    frames: tuple[RectMm, ...]
    grid: GridSpec | None


def _grid_options(photos: Sequence[Photo], gutter_mm: float) -> list[GridSpec]:
    """Grid shapes for n photos, best-fitting orientation first.

    Landscape photos want wide cells (stack them in rows); portrait photos want
    tall cells (place them in columns). The tie is broken toward rows
    explicitly, because "whichever the majority is" with an equal split resolves
    on iteration order otherwise.
    """
    n = len(photos)
    landscape = sum(1 for p in photos if p.aspect >= 1.0)
    wide_cells = landscape >= (n - landscape)

    if n <= 3:
        primary = GridSpec(1, n, gutter_mm) if wide_cells else GridSpec(n, 1, gutter_mm)
        secondary = GridSpec(n, 1, gutter_mm) if wide_cells else GridSpec(1, n, gutter_mm)
    elif n == 4:
        primary = GridSpec(2, 2, gutter_mm)
        secondary = GridSpec(1, 4, gutter_mm) if wide_cells else GridSpec(4, 1, gutter_mm)
    else:
        cols = math.ceil(math.sqrt(n))
        rows = math.ceil(n / cols)
        primary = GridSpec(cols, rows, gutter_mm)
        secondary = GridSpec(rows, cols, gutter_mm)
    if primary == secondary:
        return [primary]
    return [primary, secondary]


def _arrangements(
    photos: Sequence[Photo],
    geom: PageGeometry,
    template: str | None,
    gutter_mm: float,
) -> list[_Arrangement]:
    """The ordered ladder of arrangements to try for this page."""
    n = len(photos)
    if n == 0:
        return []
    if n > _MAX_PHOTOS_PER_PAGE:
        raise LayoutError(
            f"{n} photos on one page exceeds the {_MAX_PHOTOS_PER_PAGE} limit; that is a "
            f"contact sheet, not an album page"
        )

    options: list[_Arrangement] = []

    def add_full_bleed() -> None:
        if n == 1:
            options.append(_Arrangement(FULL_BLEED, (full_bleed_frame(geom),), None))

    def add_single_inset() -> None:
        if n == 1:
            options.append(
                _Arrangement(
                    SINGLE_INSET, (inset_frame(geom, photos[0].aspect),), None
                )
            )

    def add_grids() -> None:
        for spec in _grid_options(photos, gutter_mm):
            options.append(
                _Arrangement(
                    grid_template_id(spec), tuple(grid_frames(geom, spec)[:n]), spec
                )
            )

    if template is None:
        # A single photo defaults to an inset frame, not full bleed: full bleed
        # crops a 3:2 photo to the page aspect and burns a third of the frame,
        # so it has to be asked for.
        if n == 1:
            add_single_inset()
            add_full_bleed()
        else:
            add_grids()
    elif template == FULL_BLEED:
        if n != 1:
            raise LayoutError(f"{FULL_BLEED} takes exactly one photo, got {n}")
        add_full_bleed()
        add_single_inset()
    elif template == SINGLE_INSET:
        if n != 1:
            raise LayoutError(f"{SINGLE_INSET} takes exactly one photo, got {n}")
        add_single_inset()
    elif template.startswith("grid_"):
        try:
            cols, rows = (int(part) for part in template[len("grid_") :].split("x", 1))
        except ValueError as exc:
            raise LayoutError(f"unrecognised grid template {template!r}") from exc
        spec = GridSpec(cols, rows, gutter_mm)
        if spec.cells < n:
            raise LayoutError(f"template {template!r} has {spec.cells} cells for {n} photos")
        options.append(
            _Arrangement(grid_template_id(spec), tuple(grid_frames(geom, spec)[:n]), spec)
        )
    else:
        raise LayoutError(f"unknown template {template!r}")

    return options


def _fit_frame_to_dpi(
    photo: Photo, frame: RectMm, dpi_floor: float, bleeds: bool
) -> RectMm | None:
    """Shrink a frame about its centre until the photo clears the DPI floor.

    A bleeding frame cannot shrink -- shrinking it is exactly the white sliver
    the bleed exists to prevent -- so it either clears the floor or the whole
    arrangement is wrong for this photo.
    """
    max_w, _ = max_frame_for_dpi(photo, frame.aspect, dpi_floor)
    if frame.width_mm <= max_w:
        return frame
    if bleeds:
        return None
    scale = (max_w * _DPI_SIZING_HEADROOM) / frame.width_mm
    if scale < _MIN_FRAME_FRACTION:
        return None
    cx, cy = frame.center
    width = frame.width_mm * scale
    height = frame.height_mm * scale
    return RectMm(cx - width / 2.0, cy - height / 2.0, width, height).rounded()


def _placement_id(page_index: int, slot: int, photo: Photo) -> str:
    return f"p{page_index}-{slot}-{photo.media_id[:8]}"


def _try_arrangement(
    photos: Sequence[Photo],
    arrangement: _Arrangement,
    geom: PageGeometry,
    *,
    page_index: int,
    dpi_floor: float,
    dpi_preferred: float | None,
) -> tuple[list[dict[str, Any]], list[str], list[str]] | None:
    """Realise one arrangement, or return None if it cannot be made print-safe.

    None means "try the next template", and it is returned only for the two
    recoverable conditions: the photo cannot hold the frame at the DPI floor, or
    a face lands in the trim zone or the gutter. Anything else propagates as a
    LayoutError -- swallowing exceptions here would turn a bug in the crop maths
    into a silent fall through to a different template.
    """
    placements: list[dict[str, Any]] = []
    satisfied: set[str] = set()
    relaxed: set[str] = set()

    for slot, (photo, frame) in enumerate(zip(photos, arrangement.frames, strict=True)):
        bleeds = bool(_bleeds_for(frame.rounded(), geom))
        fitted = _fit_frame_to_dpi(photo, frame, dpi_floor, bleeds)
        if fitted is None:
            return None
        if fitted is not frame:
            relaxed.add("frame_shrunk_for_dpi")

        placement = place(
            photo,
            fitted,
            geom,
            placement_id=_placement_id(page_index, slot, photo),
            dpi_floor=dpi_floor,
        )
        if not placement_is_print_safe(placement, dpi_floor):
            return None

        if placement["bleeds"]:
            satisfied.add("full_bleed_coverage")
        if placement["face_safety"]["face_count"]:
            satisfied.add("faces_within_safe_zone")
            if geom.gutter_band is not None:
                satisfied.add("faces_clear_of_gutter")
        if placement["face_safety"]["cropped_face_ids"]:
            relaxed.add("faces_clipped_by_crop")
        if dpi_preferred is not None and placement["effective_dpi"] < dpi_preferred:
            relaxed.add("below_preferred_dpi")
        placements.append(placement)

    satisfied.update({"dpi_floor_met", "aspect_preserved", "frames_within_page"})
    return placements, sorted(satisfied), sorted(relaxed)


def _crop_loss(photos: Sequence[Photo], placements: Sequence[Mapping[str, Any]]) -> float:
    if not placements:
        return 0.0
    total = 0.0
    for photo, placement in zip(photos, placements, strict=True):
        crop = placement["crop"]
        total += 1.0 - (crop["w"] * crop["h"])
    return total / len(placements)


def blank_page(
    *, page_index: int, side: str, spread_id: str | None = None, background_hex: str = "#ffffff"
) -> dict[str, Any]:
    """An empty page, used for covers and for padding to the vendor page count."""
    _spine_side(side)
    if not _HEX_COLOR_RE.match(background_hex):
        raise LayoutError(f"background_hex must be #rrggbb, got {background_hex!r}")
    return {
        "page_index": page_index,
        "spread_id": spread_id,
        "side": side,
        "section_id": None,
        "background": {"kind": "solid", "color_hex": background_hex},
        "placements": [],
        "text_blocks": [],
        "layout": None,
    }


def layout_page(
    photos: Sequence[Photo],
    profile: Mapping[str, Any],
    *,
    page_index: int,
    side: str,
    spread_id: str | None = None,
    section_id: str | None = None,
    template: str | None = None,
    inner_gutter_mm: float = DEFAULT_INNER_GUTTER_MM,
    background_hex: str = "#ffffff",
) -> dict[str, Any]:
    """Lay out one page, returning a contract-shaped Page."""
    if not isinstance(page_index, int) or isinstance(page_index, bool) or page_index < 0:
        raise LayoutError(f"page_index must be a non-negative int, got {page_index!r}")
    if spread_id is not None and not _SLUG_RE.match(spread_id):
        raise LayoutError(f"spread_id must be a slug, got {spread_id!r}")
    if section_id is not None and not _SLUG_RE.match(section_id):
        raise LayoutError(f"section_id must be a slug, got {section_id!r}")

    geom = page_geometry(profile, side)
    dpi_floor = _positive(profile.get("dpi_floor"), "dpi_floor")
    preferred_raw = profile.get("dpi_preferred")
    dpi_preferred = None if preferred_raw is None else _positive(preferred_raw, "dpi_preferred")

    if not photos:
        return blank_page(
            page_index=page_index,
            side=side,
            spread_id=spread_id,
            background_hex=background_hex,
        )

    attempted: list[str] = []
    for arrangement in _arrangements(photos, geom, template, inner_gutter_mm):
        attempted.append(arrangement.template_id)
        result = _try_arrangement(
            photos,
            arrangement,
            geom,
            page_index=page_index,
            dpi_floor=dpi_floor,
            dpi_preferred=dpi_preferred,
        )
        if result is None:
            continue
        placements, satisfied, relaxed = result

        # Exactly one hero per page, decided by printed area with media_id as the
        # explicit tiebreak. Without the tiebreak, two equal-sized cells make the
        # hero depend on max()'s first-wins scan, which is stable here but is not
        # a property anyone should be relying on.
        hero = min(
            placements,
            key=lambda p: (-p["frame"]["width_mm"] * p["frame"]["height_mm"], p["media_id"]),
        )
        for placement in placements:
            placement["is_hero"] = placement is hero

        page = blank_page(
            page_index=page_index,
            side=side,
            spread_id=spread_id,
            background_hex=background_hex,
        )
        page["section_id"] = section_id
        page["placements"] = placements
        page["layout"] = {
            # "template", not "constraint_solver": this picks the first template
            # in an ordered ladder that satisfies the hard constraints and shrinks
            # frames to clear the DPI floor. Calling that a constraint solver
            # would overstate what ran, and LayoutInfo is a record of what ran.
            "solver": "template",
            "template_id": arrangement.template_id,
            "grid": arrangement.grid.as_dict() if arrangement.grid else None,
            "constraints_satisfied": satisfied,
            "constraints_relaxed": relaxed,
            "solver_cost": round(
                _crop_loss(photos, placements) + 0.25 * len(relaxed), 4
            ),
        }
        return page

    raise LayoutError(
        f"no arrangement of {len(photos)} photo(s) on page {page_index} ({side}) is "
        f"print-safe; tried {attempted}. Every candidate either fell below the "
        f"{dpi_floor} DPI floor or put a face in the trim zone or gutter."
    )


# ---------------------------------------------------------------------------
# album planning
# ---------------------------------------------------------------------------


def page_count_is_valid(page_count: int, profile: Mapping[str, Any]) -> bool:
    """Vendor page-count rule, counting EVERY entry in `pages`.

    Covers are counted. The shipped golden fixture is a 20-page layflat album
    whose 20 pages include both covers, against a profile whose minimum is 20 --
    so the contract's own worked example counts them, and counting only the
    interior would have produced a book the printer rejects.
    """
    counts = profile.get("page_count")
    if not isinstance(counts, Mapping):
        # No stated rule is not the same as "any count is fine", but there is
        # genuinely nothing to check against.
        return True
    minimum = int(counts["minimum"])
    maximum = int(counts["maximum"])
    increment = int(counts["increment"])
    return minimum <= page_count <= maximum and page_count % increment == 0


def _pad_to_valid_count(interior: int, fixed: int, profile: Mapping[str, Any]) -> int:
    """Number of interior pages after padding, or raise if no count works.

    Interior pages only move in twos, because they are facing pairs. So the
    PARITY of the total is fixed by the cover count and padding cannot change
    it: one cover page against a 2-page increment is unsatisfiable at every
    size. Walking upward until the vendor maximum is exceeded would "discover"
    that and then report it as an album that is too big, which sends whoever
    reads the error looking in the wrong place entirely.
    """
    counts = profile.get("page_count")
    if interior % 2:
        interior += 1
    if not isinstance(counts, Mapping):
        return interior

    minimum = int(counts["minimum"])
    maximum = int(counts["maximum"])
    increment = int(counts["increment"])

    if interior + fixed < minimum:
        interior = minimum - fixed
        if interior < 0:
            interior = 0
        if interior % 2:
            interior += 1

    # At most `increment` steps of 2 are needed to hit a multiple, or none ever
    # will; the bound is what turns an unsatisfiable parity into a diagnosis.
    for _ in range(increment + 1):
        if (interior + fixed) % increment == 0:
            break
        interior += 2
    else:
        raise LayoutError(
            f"{fixed} cover page(s) plus interior pages in facing pairs can never "
            f"total a multiple of the vendor's {increment}-page increment: interior "
            f"pages move in twos, so the parity of the total is decided by the covers"
        )

    total = interior + fixed
    if total > maximum:
        raise LayoutError(
            f"album needs {total} pages, above the vendor maximum of {maximum}"
        )
    if total < minimum:
        raise LayoutError(
            f"album has {total} pages, below the vendor minimum of {minimum}"
        )
    return interior


def layout_album(
    requests: Sequence[PageRequest],
    profile: Mapping[str, Any],
    *,
    cover: Photo | None = None,
    back_cover: bool = True,
    pad_to_page_count: bool = True,
    inner_gutter_mm: float = DEFAULT_INNER_GUTTER_MM,
    background_hex: str = "#ffffff",
) -> list[dict[str, Any]]:
    """Lay out a whole album: the `pages` array of an AlbumSpec.

    Interior pages pair into spreads in the order given -- request 0 is the left
    page of spread-01, request 1 its right -- which is the convention the shipped
    golden fixture uses.
    """
    fixed = (1 if cover is not None else 0) + (1 if back_cover else 0)
    interior_count = len(requests)
    if pad_to_page_count:
        interior_count = _pad_to_valid_count(interior_count, fixed, profile)
    elif interior_count % 2:
        raise LayoutError(
            "an odd number of interior pages leaves a page without a facing page; "
            "pass pad_to_page_count=True or supply an even number of requests"
        )

    pages: list[dict[str, Any]] = []
    if cover is not None:
        pages.append(
            layout_page(
                (cover,),
                profile,
                page_index=0,
                side="front_cover",
                template=FULL_BLEED,
                inner_gutter_mm=inner_gutter_mm,
                background_hex=background_hex,
            )
        )

    base = len(pages)
    for slot in range(interior_count):
        request = requests[slot] if slot < len(requests) else PageRequest(())
        pages.append(
            layout_page(
                request.photos,
                profile,
                page_index=base + slot,
                side="left" if slot % 2 == 0 else "right",
                spread_id=f"spread-{slot // 2 + 1:02d}",
                section_id=request.section_id,
                template=request.template,
                inner_gutter_mm=inner_gutter_mm,
                background_hex=background_hex,
            )
        )

    if back_cover:
        pages.append(
            blank_page(
                page_index=len(pages), side="back_cover", background_hex=background_hex
            )
        )

    _promote_spread_heroes(pages)

    if pad_to_page_count and not page_count_is_valid(len(pages), profile):
        raise LayoutError(
            f"padding produced {len(pages)} pages, which the vendor profile rejects"
        )
    return pages


def _promote_spread_heroes(pages: Sequence[dict[str, Any]]) -> None:
    """Exactly one hero per spread, not one per page.

    A hero is "the anchor image of its SPREAD" (contract), and the two facing
    pages are looked at together. Resolving it per page would put two competing
    anchors on one opening.
    """
    groups: dict[str, list[dict[str, Any]]] = {}
    for page in pages:
        key = page["spread_id"] or f"page-{page['page_index']}"
        groups.setdefault(key, []).extend(page["placements"])

    for key in sorted(groups):
        placements = groups[key]
        if not placements:
            continue
        hero = min(
            placements,
            key=lambda p: (
                -p["frame"]["width_mm"] * p["frame"]["height_mm"],
                p["media_id"],
                p["placement_id"],
            ),
        )
        for placement in placements:
            placement["is_hero"] = placement is hero
