"""Contact-sheet composition -- the one place a user's imagery leaves the device.

CLAUDE.md hard rule 2: "The frontier model never sees raw files and never
free-picks from the library. It receives pre-filtered low-res candidates
retrieved by the local index and returns structured JSON referencing IDs. No
exceptions." This module IS that boundary. Everything below exists to make the
rule structural rather than a thing everyone remembers.

WHAT LEAVES, AND WHAT IT CAN SAY
A single PNG: a grid of small proxy tiles, each with a printed label, on a plain
background. Nothing else is drawn. No filename, no date, no place, no caption,
no score, no duration -- not because those would be useless to a model, but
because every additional field is a new egress channel that needs its own
review, and v0 opens exactly one. Adding one is a deliberate act, not an
oversight, and `_assert_manifest_clean` will fail the build if it is added to the
manifest without also being added to the closed vocabulary.

The labels are the model's entire vocabulary for referring to a photograph. They
are per-sheet grid coordinates (`A1`..`H8`), not media ids: a leaked sheet is
then a page of thumbnails rather than an index into somebody's library, and the
64-hex content address of a file is not readable at 21px anyway.
`SheetManifest` maps label -> media_id on this side, and `memory_engine_prompt.
structured` rejects any id the sheet did not carry. Read that module before
changing this one; its allow-list is the second half of this defence and this
module deliberately does not re-implement any of it.

WHY THE LABEL GRAMMAR IS LETTER-THEN-DIGIT
A vision model can misread a glyph. The label alphabet is positional -- position
0 is a row letter A-H, position 1 is a column digit 1-8 -- so the most likely
confusion, a shape collision across the two classes (B/8, G/6), produces a
string like "88" or "6G" that is not a label at all. That fails closed at
`structured.parse_reply` as CODE_UNKNOWN_ID. Only a within-class misread (B for
E) can yield a different *valid* label, and that is the residual risk stated in
`draw_label`'s legibility note below.

THE RESOLUTION CEILING IS A REFUSAL, NOT A CLAMP
`MAX_SOURCE_EDGE_PX` bounds the proxy handed in. Above it this module RAISES.
It does not downscale. The failure being defended against is the day the proxy
pipeline changes and starts handing over originals: a clamp would resize them,
the sheets would look identical, and full-resolution imagery would have been
read, decoded and re-encoded at this boundary with nobody informed. Refusing
turns that into a stack trace on the first file. Legitimate downscaling --
a 512px thumbnail into a 256px tile -- is the module's actual job and is
unaffected; the ceiling is on the INPUT, checked from the file header before a
single pixel is decoded, which also makes a decompression bomb a refusal rather
than a memory event.

Every ceiling exists twice: a public constant (documentation, and what the
policy validates against) and a private one re-checked immediately before
composition. `contact_sheet.MAX_CELL_PX = 4096` from a test or a plugin is one
line, and a ceiling a caller can raise by assignment is not a ceiling.

STRIP AND ASSERT
Tiles are rebuilt from raw pixels via `Image.frombytes`, so no EXIF block, GPS
IFD, XMP packet, ICC profile, PNG text chunk or `Image.filename` can ride along
-- the container is left behind rather than filtered. Then it is checked on the
way out, twice, because "we strip it" is a claim and this boundary does not run
on claims:

  * the encoded PNG's chunk stream must contain exactly IHDR/IDAT/IEND. eXIf,
    tEXt, iTXt, zTXt, tIME are all rejections.
  * every key and every string value in the manifest must come from a closed
    vocabulary (module constants, a label, a media id, a hex digest). A future
    edit that adds `"source_path"` to a cell fails here rather than in review.

An EXIF orientation other than 1 is REFUSED rather than applied. Proxies reach
this module already oriented (docs/architecture.md: coordinates are normalised
against the *oriented* image), so a surviving orientation tag means either the
pipeline stopped orienting or the tag was left behind after orienting. Those two
need opposite handling and nothing here can tell them apart, so neither is
guessed.

FAILING LOUDLY IS THE POINT
A missing proxy, an undecodable one, a truncated one, an aspect ratio so extreme
the tile would be a line -- all raise. None of them produces a blank cell,
because a blank cell is not visibly an error: the model describes it as a dark
or empty photograph and returns a decision about it, and that decision then
looks exactly like every other decision. `ImageFile.LOAD_TRUNCATED_IMAGES` is
checked at composition time for the same reason -- it is a global anyone can set,
and with it set Pillow completes a truncated JPEG with grey and raises nothing.

DETERMINISM
Same candidate list plus same policy produces byte-identical output. Placement
is the caller's list order (that order is the story: chronology for a film,
rank for a cull), never a set or dict iteration; the only internal sort is on
strings for error messages. Sheet appearance depends on no clock, no locale, no
font file: glyphs are a bitmap table in this file, so a machine without fonts
renders the same sheet as one with all of them. Byte-identity is claimed within
one environment -- Pillow's LANCZOS kernel and zlib are not versioned parts of
this contract, which is why `pixel_digest` (over raw RGB) is published next to
`image_digest` (over the PNG).

Python 3.12. Pillow, and blake3 for the digests.
"""

from __future__ import annotations

import io
import json
import math
import re
# collections.abc for isinstance, typing only for annotations -- the same split
# structured.py uses, and for the same reason: isinstance against a typing alias
# is deprecated, and these checks are load-bearing.
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

__all__ = [
    "ALPHA_BACKDROP",
    "BACKGROUND",
    "BORDER",
    "COLUMN_DIGITS",
    "DEFAULT_POLICY",
    "IMAGE_FORMAT",
    "LABEL_INK",
    "LABEL_PAPER",
    "MANIFEST_SCHEMA",
    "MAX_CELLS",
    "MAX_CELL_PX",
    "MAX_COLUMNS",
    "MAX_LABEL_SCALE",
    "MAX_ROWS",
    "MAX_SHEET_EDGE_PX",
    "MAX_SOURCE_EDGE_PX",
    "MIN_CELL_PX",
    "MIN_LABEL_SCALE",
    "MIN_RENDERED_EDGE_PX",
    "ROW_LETTERS",
    "ContactSheet",
    "ContactSheetError",
    "ContactSheetPolicy",
    "LeakError",
    "ProxyError",
    "ResolutionCeilingError",
    "SheetCandidate",
    "SheetCell",
    "SheetManifest",
    "build_contact_sheet",
    "cell_label",
]


# --------------------------------------------------------------------------
# Ceilings and constants.
#
# Public name for documentation and policy validation; private twin re-checked
# at composition time. See the module docstring on why one constant is not
# enough.
# --------------------------------------------------------------------------

#: Long edge of a proxy this module will accept. A `thumbnail_512` passes; a
#: `preview_2048`, and every original ever taken, does not.
MAX_SOURCE_EDGE_PX = 1024
_SOURCE_EDGE_HARD_CEILING = 1024

#: Long edge of one rendered tile.
MAX_CELL_PX = 320
MIN_CELL_PX = 96
_CELL_PX_HARD_CEILING = 320
_CELL_PX_HARD_FLOOR = 96

MAX_COLUMNS = 8
MAX_ROWS = 8
MAX_CELLS = 64
_COLUMNS_HARD_CEILING = 8
_ROWS_HARD_CEILING = 8
_CELLS_HARD_CEILING = 64

#: Belt and braces on the composed canvas. Derived from the ceilings above, so
#: tripping it means the layout arithmetic is wrong, not that a caller asked for
#: too much.
MAX_SHEET_EDGE_PX = 4096

#: Below this, a fitted tile is a smear rather than a photograph. A 4000x3 panorama
#: fitted into a 256px cell is 256x0.2 -> rounded to 1px; the model would describe a
#: line. Refused, with the aspect ratio named.
MIN_RENDERED_EDGE_PX = 8

#: Integer upscale of the 5x7 glyph table. 3 gives 21px-tall labels with 3px
#: strokes; see `draw_label` for what that is assuming.
MIN_LABEL_SCALE = 2
MAX_LABEL_SCALE = 8

IMAGE_FORMAT = "PNG"
MANIFEST_SCHEMA = "contact_sheet_manifest/v0"

# Flat, non-photographic, and fixed. Not configurable: the contrast between ink
# and paper is a legibility guarantee, and a caller who could set it could set
# grey on grey.
BACKGROUND = (245, 245, 245)
BORDER = (24, 24, 24)
LABEL_PAPER = (255, 255, 255)
LABEL_INK = (0, 0, 0)
#: What a transparent proxy is composited onto. White rather than black: a black
#: backdrop reads as a dark photograph, which is the failure mode the whole
#: module is arranged against.
ALPHA_BACKDROP = (255, 255, 255)

ROW_LETTERS = "ABCDEFGH"
COLUMN_DIGITS = "12345678"

# Gap between a tile and its own label, and the gutter between cells. The
# invariant `gutter >= 3 * _LABEL_GAP_PX` keeps every label unambiguously closer
# to the tile it names than to the tile below it -- proximity is the only cue a
# model has for which label belongs to which picture.
_LABEL_GAP_PX = 3
_MIN_GUTTER_PX = 3 * _LABEL_GAP_PX
_MARGIN_PX = 16

_LABEL_RE = re.compile(r"^[A-H][1-8]$")

# A media id is a local identifier and must look like one. The check is here
# because the one plausible way a filesystem path reaches the manifest is a
# caller passing it as the id -- `SheetCandidate` has no path field, so there is
# no other door.
_MEDIA_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_FILENAME_SUFFIX_RE = re.compile(
    r"\.(jpe?g|png|gif|bmp|tiff?|webp|avif|heic|heif|dng|cr[23w]|nef|arw|raf|orf|rw2|"
    r"mp4|mov|m4v|avi|mkv|mts|m2ts|mpe?g|wmv|3gp|insv|360|webm|aae|xmp|json|txt)$",
    re.IGNORECASE,
)
_HEX_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")

# Exactly what Pillow writes for a fresh RGB image. Anything else -- eXIf, tEXt,
# iTXt, zTXt, tIME, or a chunk a future Pillow decides to add -- is a rejection
# rather than a tolerated extra, because "harmless metadata" is a judgement this
# boundary is not allowed to make on its own.
_ALLOWED_PNG_CHUNKS = ("IHDR", "IDAT", "IEND")

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

# Pillow modes that convert to RGB with a defined, photographic meaning. "I",
# "F", "I;16" and friends are deliberately absent: they are measurement rasters
# (depth, radiometry) whose conversion to 8-bit rescales by an assumption this
# module has no basis for making.
_CONVERTIBLE_MODES = frozenset({"1", "L", "LA", "P", "PA", "RGB", "RGBA", "CMYK", "YCbCr"})

_EXIF_ORIENTATION_TAG = 0x0112


class ContactSheetError(Exception):
    """A contact sheet could not be composed, or a reply could not be mapped back."""


class ResolutionCeilingError(ContactSheetError):
    """A proxy exceeded the input resolution ceiling. Never downscaled instead."""


class ProxyError(ContactSheetError):
    """A proxy was missing, undecodable, truncated, or unusable as a tile."""


class LeakError(ContactSheetError):
    """Something that must not leave the device was found on the way out."""


# --------------------------------------------------------------------------
# Glyphs
#
# A 5x7 bitmap table rather than a font file. Three reasons, in order of
# importance: a sheet must render identically on a machine with no fonts
# installed (determinism is a product requirement, hard rule 3); the stroke
# width is then exactly `scale` pixels and can be *stated* rather than measured;
# and it removes a dependency on freetype being compiled into Pillow, which is
# a build-time coin flip.
#
# Only the characters a label can contain are defined. `_check_glyph_coverage`
# runs at import and fails if the ceilings ever grow past the table.
# --------------------------------------------------------------------------

_GLYPHS: dict[str, tuple[str, ...]] = {
    "A": (".###.", "#...#", "#...#", "#####", "#...#", "#...#", "#...#"),
    "B": ("####.", "#...#", "#...#", "####.", "#...#", "#...#", "####."),
    "C": (".###.", "#...#", "#....", "#....", "#....", "#...#", ".###."),
    "D": ("####.", "#...#", "#...#", "#...#", "#...#", "#...#", "####."),
    "E": ("#####", "#....", "#....", "####.", "#....", "#....", "#####"),
    "F": ("#####", "#....", "#....", "####.", "#....", "#....", "#...."),
    "G": (".###.", "#...#", "#....", "#.###", "#...#", "#...#", ".###."),
    "H": ("#...#", "#...#", "#...#", "#####", "#...#", "#...#", "#...#"),
    "1": ("..#..", ".##..", "..#..", "..#..", "..#..", "..#..", ".###."),
    "2": (".###.", "#...#", "....#", "...#.", "..#..", ".#...", "#####"),
    "3": ("#####", "...#.", "..#..", "...#.", "....#", "#...#", ".###."),
    "4": ("...#.", "..##.", ".#.#.", "#..#.", "#####", "...#.", "...#."),
    "5": ("#####", "#....", "####.", "....#", "....#", "#...#", ".###."),
    "6": ("..##.", ".#...", "#....", "####.", "#...#", "#...#", ".###."),
    "7": ("#####", "....#", "...#.", "..#..", ".#...", ".#...", ".#..."),
    "8": (".###.", "#...#", "#...#", ".###.", "#...#", "#...#", ".###."),
}

_GLYPH_W = 5
_GLYPH_H = 7
_GLYPH_ADVANCE = _GLYPH_W + 1  # one blank column between characters


def _check_glyph_coverage() -> None:
    """Every label the ceilings permit must be renderable. Checked at import.

    Raising a KeyError from deep inside a draw call, on the day someone raises
    `MAX_ROWS` to 10, would surface as a crash in the compositor rather than as
    "the glyph table stops at H".
    """
    missing = sorted(
        set(ROW_LETTERS[:_ROWS_HARD_CEILING] + COLUMN_DIGITS[:_COLUMNS_HARD_CEILING])
        - set(_GLYPHS)
    )
    if missing:  # pragma: no cover - structural guard, unreachable as configured
        raise ContactSheetError(f"no glyph for label character(s): {missing}")
    for name, rows in _GLYPHS.items():  # pragma: no branch
        if len(rows) != _GLYPH_H or any(len(row) != _GLYPH_W for row in rows):
            raise ContactSheetError(f"glyph {name} is not {_GLYPH_W}x{_GLYPH_H}")


_check_glyph_coverage()


def cell_label(row: int, column: int) -> str:
    """`A1`-style label for a zero-indexed grid position.

    Exposed because the manifest, the drawing code and the tests must agree on
    exactly one derivation; two implementations of "what is the label at (2, 3)"
    is how a reply about one photograph gets applied to another.
    """
    if not isinstance(row, int) or isinstance(row, bool):
        raise ContactSheetError("row must be an int")
    if not isinstance(column, int) or isinstance(column, bool):
        raise ContactSheetError("column must be an int")
    if row < 0 or row >= len(ROW_LETTERS):
        raise ContactSheetError(f"row {row} outside 0..{len(ROW_LETTERS) - 1}")
    if column < 0 or column >= len(COLUMN_DIGITS):
        raise ContactSheetError(f"column {column} outside 0..{len(COLUMN_DIGITS) - 1}")
    return ROW_LETTERS[row] + COLUMN_DIGITS[column]


def label_size(text: str, scale: int) -> tuple[int, int]:
    """Pixel extent of `text` at `scale`, including inter-character gaps."""
    if not text:
        raise ContactSheetError("label must not be empty")
    width = len(text) * _GLYPH_ADVANCE - 1
    return width * scale, _GLYPH_H * scale


def draw_label(image: Any, text: str, origin: tuple[int, int], scale: int) -> None:
    """Stamp `text` at `origin` as scale x scale blocks. No font, no antialiasing.

    LEGIBILITY -- AN ASSUMPTION THAT CANNOT BE TESTED IN THIS REPO
    Whether a frontier vision model reads "B4" correctly off a 2000px sheet is
    a property of that model, and nothing here can measure it. What this module
    can guarantee, and what the default policy is chosen to hold to, is stated
    so it can be argued with:

      * cap height 21px and stroke width 3px at the sheet's NATIVE resolution
        (scale 3). Frontier vision stacks downsample a large image to a fixed
        budget -- roughly 1500px on the long edge for the current Claude and
        Gemini classes -- so a 1648x2088 default sheet is reduced by about 0.72
        and the glyph lands near 15px with 2px strokes. That is above the ~10px
        floor where OCR-style reading of blocky glyphs starts to degrade, and
        the margin is deliberately thin rather than generous because a bigger
        label means a smaller tile.
      * pure black on pure white, in a strip that is never drawn over
        photography, so no image content can be mistaken for a label and no
        label can be lost against a bright sky.
      * 5x7 blocky forms with no serifs, no antialiasing and no subpixel
        placement, which is the shape family least damaged by bilinear
        downsampling.
      * the label alphabet is positional (letter then digit), so a cross-class
        misread is rejected downstream rather than silently redirected. Only a
        within-class misread survives, and that is the residual risk.

    If a sheet ever needs more than 64 cells, the honest change is more sheets,
    not smaller labels. Shrinking the label to fit is how this stops working
    with no error anywhere.
    """
    if scale < MIN_LABEL_SCALE or scale > MAX_LABEL_SCALE:
        raise ContactSheetError(f"label scale {scale} outside {MIN_LABEL_SCALE}..{MAX_LABEL_SCALE}")
    x0, y0 = origin
    pen = x0
    for character in text:
        glyph = _GLYPHS.get(character)
        if glyph is None:
            raise ContactSheetError(f"no glyph for label character {character!r}")
        for row_index, row in enumerate(glyph):
            for column_index, pixel in enumerate(row):
                if pixel != "#":
                    continue
                left = pen + column_index * scale
                top = y0 + row_index * scale
                for dy in range(scale):
                    for dx in range(scale):
                        image.putpixel((left + dx, top + dy), LABEL_INK)
        pen += _GLYPH_ADVANCE * scale


# --------------------------------------------------------------------------
# Policy
# --------------------------------------------------------------------------


def _require_int(value: object, name: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool):
        # bool is int in Python; `columns=True` would otherwise mean one column.
        raise ContactSheetError(f"{name} must be an int")


@dataclass(frozen=True)
class ContactSheetPolicy:
    """Composition limits. Every field is bounded by a module ceiling.

    `columns` is a maximum, not a target: five candidates lay out five across
    rather than six across with a hole, because empty cells are dead area the
    model still has to look at.
    """

    cell_px: int = 256
    columns: int = 6
    max_cells: int = 40
    label_scale: int = 3
    gutter_px: int = 16

    def __post_init__(self) -> None:
        _require_int(self.cell_px, "cell_px")
        if self.cell_px > MAX_CELL_PX:
            raise ContactSheetError(
                f"cell_px {self.cell_px} exceeds the ceiling of {MAX_CELL_PX}; "
                "the ceiling is not raisable by a caller"
            )
        if self.cell_px < MIN_CELL_PX:
            # A floor as well as a ceiling. Below it the model cannot resolve
            # the picture, so the upload buys nothing and costs privacy.
            raise ContactSheetError(f"cell_px {self.cell_px} is below the floor of {MIN_CELL_PX}")

        _require_int(self.columns, "columns")
        if self.columns < 1 or self.columns > MAX_COLUMNS:
            raise ContactSheetError(f"columns {self.columns} outside 1..{MAX_COLUMNS}")

        _require_int(self.max_cells, "max_cells")
        if self.max_cells < 1 or self.max_cells > MAX_CELLS:
            raise ContactSheetError(f"max_cells {self.max_cells} outside 1..{MAX_CELLS}")

        _require_int(self.label_scale, "label_scale")
        if self.label_scale < MIN_LABEL_SCALE or self.label_scale > MAX_LABEL_SCALE:
            raise ContactSheetError(
                f"label_scale {self.label_scale} outside {MIN_LABEL_SCALE}..{MAX_LABEL_SCALE}"
            )

        _require_int(self.gutter_px, "gutter_px")
        if self.gutter_px < _MIN_GUTTER_PX:
            raise ContactSheetError(
                f"gutter_px {self.gutter_px} is below {_MIN_GUTTER_PX}; a label must sit "
                "closer to the tile it names than to the next row"
            )
        if self.gutter_px > 64:
            raise ContactSheetError(f"gutter_px {self.gutter_px} is implausibly large")


DEFAULT_POLICY = ContactSheetPolicy()


# --------------------------------------------------------------------------
# Candidate
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class SheetCandidate:
    """One tile's worth of input: a local id, and the proxy's encoded bytes.

    There is no path field and there will not be one. The caller reads the proxy
    and hands over bytes, so a path cannot leak out of this module because it
    never enters it. That also means "the proxy is missing" is a condition the
    caller hits at read time, with the path in hand for the error message, which
    is where it is actually useful.

    `proxy_bytes` is the encoded file (JPEG/PNG/WebP), not raw pixels: the
    header is what the resolution ceiling is checked against before anything is
    decoded.
    """

    media_id: str
    proxy_bytes: bytes

    def __post_init__(self) -> None:
        if not isinstance(self.media_id, str):
            raise ContactSheetError("media_id must be a str")
        if not _MEDIA_ID_RE.fullmatch(self.media_id):
            # fullmatch, never match: `$` also matches before a trailing
            # newline, so `match` would accept "abc\n/etc/passwd"... and more to
            # the point would accept an id whose last byte forges a line in a
            # log. The same trap was found in structured.py's slug filter.
            raise ContactSheetError(
                f"media_id {self.media_id!r} is not a local identifier "
                "(letters, digits, dot, colon, dash, underscore; 128 chars max)"
            )
        if _FILENAME_SUFFIX_RE.search(self.media_id):
            raise ContactSheetError(
                f"media_id {self.media_id!r} looks like a filename; ids are content "
                "addresses, and a filename in the manifest is exactly the egress "
                "this module exists to prevent"
            )
        if not isinstance(self.proxy_bytes, (bytes, bytearray)):
            raise ContactSheetError(
                f"proxy_bytes for {self.media_id} must be bytes; a missing proxy is an "
                "error, never an empty cell"
            )
        if not self.proxy_bytes:
            raise ContactSheetError(f"proxy_bytes for {self.media_id} is empty")
        object.__setattr__(self, "proxy_bytes", bytes(self.proxy_bytes))


# --------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class SheetCell:
    """Where one candidate landed, and what happened to it on the way.

    `box` is the tile's exact pixel rectangle on the sheet -- the photograph's
    extent, not the cell's, so a letterboxed panorama reports the strip it
    actually occupies. Useful for a review UI that wants to show the human the
    same thing the model saw.

    `metadata_fields_removed` counts the container-level entries thrown away
    (EXIF block, ICC profile, PNG text chunks, ...). A count and not the keys:
    a PNG text chunk's KEY is author-controlled and can itself be a filename, so
    recording key names would reopen the hole this module closes.
    """

    label: str
    row: int
    column: int
    media_id: str
    box: tuple[int, int, int, int]
    label_box: tuple[int, int, int, int]
    source_size: tuple[int, int]
    rendered_size: tuple[int, int]
    metadata_fields_removed: int


@dataclass(frozen=True)
class SheetManifest:
    """The local half of the boundary: which label is which media id.

    Never sent anywhere. It exists so a reply naming `B4` can be turned back
    into a media id, and so the consent ledger can record precisely what left.
    """

    schema: str
    image_format: str
    image_width: int
    image_height: int
    columns: int
    rows: int
    cell_px: int
    label_scale: int
    image_digest: str
    pixel_digest: str
    cells: tuple[SheetCell, ...]
    empty_positions: tuple[tuple[int, int], ...]

    @property
    def labels(self) -> tuple[str, ...]:
        """Every label on the sheet, in placement order.

        This tuple is the complete vocabulary a reply may use, and it is what
        `request()` hands to `structured.Request`.
        """
        return tuple(cell.label for cell in self.cells)

    def media_id_for(self, label: str) -> str:
        """Exact lookup. No normalising, no stripping, no case folding.

        A lenient lookup (`" b4 "` -> `B4`) is how a reply about one photograph
        is applied to another, and there is no upside: `structured.parse_reply`
        has already rejected everything that is not verbatim a label.
        """
        for cell in self.cells:
            if cell.label == label:
                return cell.media_id
        raise ContactSheetError(f"{label!r} is not a label on this sheet")

    def request(self, *, purpose: str, **kwargs: Any) -> Any:
        """Build the `structured.Request` that matches this sheet.

        The point of constructing it here is that the SAME object which knows
        what was drawn decides what a legal reply is. Two hand-kept lists drift,
        and when they drift the allow-check starts rejecting real labels.

        Imported inside the function on purpose: `memory_engine_prompt/__init__`
        records why this package has no import-time edges between its modules,
        and composing a sheet must not require the parser to import cleanly.
        """
        from memory_engine_prompt.structured import Request  # noqa: PLC0415

        return Request(purpose=purpose, allowed_ids=self.labels, **kwargs)

    def resolve(self, result: Any) -> tuple[str, ...]:
        """Media ids for a validated `ParseResult`, in the model's order.

        Takes the ParseResult rather than raw ids so the mapping cannot be run
        on something that skipped validation. It re-checks nothing:
        `structured` owns that, its allow-list was independently verified, and a
        second implementation here would be a second thing to keep correct.
        """
        items = getattr(result, "items", None)
        if not isinstance(items, tuple):
            # `isinstance(..., tuple)` and not a truthiness or None test: a plain
            # dict has an `.items` ATTRIBUTE (the bound method), so `if items is
            # None` would wave a dict straight through and fail later with a
            # confusing TypeError from inside the comprehension.
            raise ContactSheetError(
                "resolve expects a structured.ParseResult (its .items tuple), got "
                f"{type(result).__name__}"
            )
        return tuple(self.media_id_for(item.id) for item in items)

    def to_dict(self) -> dict[str, Any]:
        """JSON-able form. Checked against the closed vocabulary before return."""
        payload = {
            "schema": self.schema,
            "image_format": self.image_format,
            "image_width": self.image_width,
            "image_height": self.image_height,
            "columns": self.columns,
            "rows": self.rows,
            "cell_px": self.cell_px,
            "label_scale": self.label_scale,
            "image_digest": self.image_digest,
            "pixel_digest": self.pixel_digest,
            "cells": [
                {
                    "label": cell.label,
                    "row": cell.row,
                    "column": cell.column,
                    "media_id": cell.media_id,
                    "box": list(cell.box),
                    "label_box": list(cell.label_box),
                    "source_size": list(cell.source_size),
                    "rendered_size": list(cell.rendered_size),
                    "metadata_fields_removed": cell.metadata_fields_removed,
                }
                for cell in self.cells
            ],
            "empty_positions": [list(position) for position in self.empty_positions],
        }
        _assert_manifest_clean(payload)
        return payload

    def to_json(self) -> str:
        """Canonical JSON: sorted keys, no incidental whitespace."""
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True)
class ContactSheet:
    """The composited PNG and the manifest that explains it."""

    image_bytes: bytes
    manifest: SheetManifest

    def open_image(self) -> Any:
        """Re-decode the emitted bytes. For tests and review UIs.

        Deliberately not a stored `Image` object: a mutable PIL image on a
        frozen dataclass could be drawn on after the digests were taken, and the
        digests are what the consent ledger records.
        """
        from PIL import Image  # noqa: PLC0415

        return Image.open(io.BytesIO(self.image_bytes))


# --------------------------------------------------------------------------
# Manifest vocabulary check
# --------------------------------------------------------------------------

_MANIFEST_KEYS = frozenset({
    "schema", "image_format", "image_width", "image_height", "columns", "rows",
    "cell_px", "label_scale", "image_digest", "pixel_digest", "cells",
    "empty_positions",
})
_CELL_KEYS = frozenset({
    "label", "row", "column", "media_id", "box", "label_box", "source_size",
    "rendered_size", "metadata_fields_removed",
})
_CONSTANT_STRINGS = frozenset({MANIFEST_SCHEMA, IMAGE_FORMAT})


def _assert_manifest_clean(payload: Mapping[str, Any]) -> None:
    """Every key and every string in the manifest comes from a closed vocabulary.

    This is the check that has to survive the next six months. Nothing in the
    manifest today can hold a path -- `SheetCandidate` has no path field -- so
    this catches nothing on the day it is written. It exists for the edit that
    adds `"source": record["sources"][0]["path"]` to a cell "for debugging",
    which is a one-line change that reviews as obviously helpful.
    """
    unknown = sorted(set(payload) - _MANIFEST_KEYS)
    if unknown:
        raise LeakError(f"manifest has undeclared field(s): {unknown}")
    for cell in payload["cells"]:
        unknown_cell = sorted(set(cell) - _CELL_KEYS)
        if unknown_cell:
            raise LeakError(f"manifest cell has undeclared field(s): {unknown_cell}")

    def check(value: Any, where: str) -> None:
        if isinstance(value, str):
            if value in _CONSTANT_STRINGS:
                return
            if _LABEL_RE.fullmatch(value):
                return
            if _HEX_DIGEST_RE.fullmatch(value):
                return
            if _MEDIA_ID_RE.fullmatch(value) and not _FILENAME_SUFFIX_RE.search(value):
                return
            raise LeakError(
                f"manifest field {where} holds a string that is not a constant, a label, "
                f"a digest or a media id: {value!r}"
            )
        if isinstance(value, bool) or isinstance(value, int):
            return
        if isinstance(value, (list, tuple)):
            for index, element in enumerate(value):
                check(element, f"{where}[{index}]")
            return
        if isinstance(value, Mapping):
            for key, element in value.items():
                check(key, f"{where}.{key}")
                check(element, f"{where}.{key}")
            return
        raise LeakError(f"manifest field {where} holds an unexpected {type(value).__name__}")

    for key, value in payload.items():
        check(value, key)


# --------------------------------------------------------------------------
# Digests
# --------------------------------------------------------------------------


def _blake3_hex(payload: bytes) -> str:
    """BLAKE3, or an exception. Never a substitute hash.

    A digest recorded in the consent ledger as the content address of what left
    the device has to actually be that. Falling back to blake2b would produce a
    plausible 64-hex string that no other component can reproduce.
    """
    try:
        import blake3  # type: ignore  # noqa: PLC0415
    except ImportError as error:  # pragma: no cover - dependency guard
        raise ContactSheetError(
            "contact-sheet digests must be BLAKE3 content addresses and the blake3 "
            f"package is not installed ({error}); pip install blake3"
        ) from error
    return blake3.blake3(payload).hexdigest()


# --------------------------------------------------------------------------
# Tile decoding
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class _Tile:
    image: Any
    source_size: tuple[int, int]
    rendered_size: tuple[int, int]
    metadata_fields_removed: int


def _decode_tile(candidate: SheetCandidate, cell_px: int) -> _Tile:
    """Bytes -> a clean RGB tile at most `cell_px` on its long edge.

    Order matters and is load-bearing:

      1. open (header only -- Pillow is lazy), so a bomb or an original is
         refused from its dimensions before any pixel is decoded;
      2. check the ceiling and REFUSE, never resize;
      3. check the orientation tag and refuse a proxy that was not oriented;
      4. decode;
      5. rebuild from raw pixels, which is what actually strips the container.
    """
    from PIL import Image, ImageFile  # noqa: PLC0415

    if getattr(ImageFile, "LOAD_TRUNCATED_IMAGES", False):
        # A global anyone can set. With it set, a truncated JPEG decodes to the
        # part that arrived plus a grey block, and raises nothing -- the exact
        # "blank cell the model describes as a dark photo" this module refuses
        # to produce. Checked per tile rather than once at import because it can
        # be flipped at any point in a long-running worker.
        raise ProxyError(
            "PIL.ImageFile.LOAD_TRUNCATED_IMAGES is enabled; a truncated proxy would be "
            "silently completed with grey. Refusing to compose under that setting."
        )

    try:
        handle = Image.open(io.BytesIO(candidate.proxy_bytes))
    except Exception as error:  # noqa: BLE001 - any decoder failure is one condition
        raise ProxyError(
            f"proxy for {candidate.media_id} could not be opened: {type(error).__name__}: {error}"
        ) from error

    try:
        width, height = handle.size
        if width < 1 or height < 1:
            raise ProxyError(
                f"proxy for {candidate.media_id} has a zero dimension: {width}x{height}"
            )

        ceiling = min(MAX_SOURCE_EDGE_PX, _SOURCE_EDGE_HARD_CEILING)
        if max(width, height) > ceiling:
            raise ResolutionCeilingError(
                f"proxy for {candidate.media_id} is {width}x{height}, above the {ceiling}px "
                "input ceiling. Refusing rather than downscaling: this is what a pipeline "
                "handing over originals looks like, and a silent resize would hide it."
            )

        try:
            orientation = handle.getexif().get(_EXIF_ORIENTATION_TAG)
        except Exception:  # noqa: BLE001 - a malformed EXIF block is not a tile problem
            orientation = None
        if orientation is not None and orientation != 1:
            raise ProxyError(
                f"proxy for {candidate.media_id} carries EXIF orientation {orientation}. "
                "Proxies reach this boundary already oriented; a surviving tag means either "
                "the pipeline stopped orienting or it forgot to clear the tag, and those "
                "need opposite fixes. Not guessing."
            )

        mode = handle.mode
        if mode not in _CONVERTIBLE_MODES:
            raise ProxyError(
                f"proxy for {candidate.media_id} has mode {mode!r}, which is not a "
                "photographic mode this module knows how to convert without inventing a scale"
            )

        metadata_fields_removed = len(getattr(handle, "info", {}) or {})

        try:
            handle.load()
            if mode in ("P", "PA", "RGBA", "LA"):
                # Palette and alpha go through RGBA so transparency composites
                # against a known colour rather than becoming black.
                rgba = handle.convert("RGBA")
                backdrop = Image.new("RGBA", rgba.size, ALPHA_BACKDROP + (255,))
                decoded = Image.alpha_composite(backdrop, rgba).convert("RGB")
            else:
                decoded = handle.convert("RGB")
        except ContactSheetError:
            raise
        except Exception as error:  # noqa: BLE001
            raise ProxyError(
                f"proxy for {candidate.media_id} could not be decoded "
                f"({type(error).__name__}: {error}); a truncated or corrupt proxy is an "
                "error, never a blank cell"
            ) from error
    finally:
        handle.close()

    # The strip. `frombytes` builds a new image from raw pixels, so nothing from
    # the container -- exif, icc_profile, xmp, text chunks, filename, dpi,
    # comment -- can survive. Filtering `info` key by key would instead leave
    # whatever key a future format adds.
    clean = Image.frombytes("RGB", decoded.size, decoded.tobytes())
    # Not decoration: `convert()` COPIES `.info`, so a tile that skipped the
    # rebuild arrives here still carrying the JPEG's exif and icc entries and
    # this raises. Mutation-tested -- replacing the rebuild with `clean = decoded`
    # is caught here and nowhere else.
    if clean.info:
        raise LeakError(f"rebuilt tile for {candidate.media_id} still carries {sorted(clean.info)}")

    target = _fit(decoded.size, cell_px)
    if min(target) < MIN_RENDERED_EDGE_PX:
        raise ProxyError(
            f"proxy for {candidate.media_id} is {width}x{height}; fitted into a "
            f"{cell_px}px cell that is {target[0]}x{target[1]}, below the "
            f"{MIN_RENDERED_EDGE_PX}px floor. A tile that thin is a line, and the model "
            "would describe it as one."
        )
    if target != clean.size:
        clean = clean.resize(target, resample=Image.Resampling.LANCZOS)

    return _Tile(
        image=clean,
        source_size=(width, height),
        rendered_size=target,
        metadata_fields_removed=metadata_fields_removed,
    )


def _fit(size: tuple[int, int], cell_px: int) -> tuple[int, int]:
    """Largest size with the same aspect ratio that fits in a cell_px square.

    Contain, never cover and never stretch. Cropping to fill would decide which
    part of the photograph the model is allowed to consider, which is a creative
    decision, and CLAUDE.md rule 3 keeps those in the plan.

    Never enlarges, either. A 64px proxy blown up to a 256px cell looks like a
    256px photograph that someone shot badly, and the model's judgement of
    sharpness -- which is a thing we ask it for -- would then be a judgement of
    our resampler. A small proxy renders small; the empty area around it is the
    honest signal that this is all the picture there is.
    """
    width, height = size
    scale = min(1.0, cell_px / width, cell_px / height)
    fitted_w = max(1, min(cell_px, int(math.floor(width * scale + 0.5))))
    fitted_h = max(1, min(cell_px, int(math.floor(height * scale + 0.5))))
    return fitted_w, fitted_h


# --------------------------------------------------------------------------
# PNG inspection
# --------------------------------------------------------------------------


def png_chunk_types(data: bytes) -> tuple[str, ...]:
    """Chunk type codes of a PNG, in file order. Structural, not heuristic.

    A substring search for `b"Exif"` would miss a compressed zTXt block holding
    a path, and would false-positive on pixel data that happens to spell it.
    Walking the chunk table is the only answer that is actually about what is in
    the file.
    """
    if not data.startswith(_PNG_MAGIC):
        raise LeakError("emitted bytes are not a PNG")
    types: list[str] = []
    index = len(_PNG_MAGIC)
    while index < len(data):
        if index + 8 > len(data):
            raise LeakError("PNG truncated inside a chunk header")
        length = int.from_bytes(data[index : index + 4], "big")
        types.append(data[index + 4 : index + 8].decode("ascii", errors="replace"))
        index += 12 + length  # length + type + payload + crc
    if index != len(data):
        raise LeakError("PNG chunk lengths do not add up to the file size")
    return tuple(types)


def _assert_no_metadata_chunks(data: bytes) -> None:
    found = png_chunk_types(data)
    extra = sorted(set(found) - set(_ALLOWED_PNG_CHUNKS))
    if extra:
        raise LeakError(
            f"emitted sheet carries PNG chunk(s) {extra}; only {list(_ALLOWED_PNG_CHUNKS)} "
            "may leave the device"
        )


# --------------------------------------------------------------------------
# Composition
# --------------------------------------------------------------------------


def build_contact_sheet(
    candidates: Sequence[SheetCandidate],
    *,
    policy: ContactSheetPolicy = DEFAULT_POLICY,
) -> ContactSheet:
    """Compose one contact sheet. Raises on anything it cannot do honestly.

    `candidates` is a Sequence and its ORDER is the layout order, left to right
    then top to bottom. That order is the caller's editorial decision --
    chronology for a film, rank for a cull -- and is preserved rather than
    re-sorted here. Sets and dicts are refused: iterating either would make the
    sheet depend on hash seeding, and the sheet is supposed to be reproducible
    from the ledger entry.
    """
    from PIL import Image  # noqa: PLC0415

    if not isinstance(policy, ContactSheetPolicy):
        raise ContactSheetError("policy must be a ContactSheetPolicy")
    if isinstance(candidates, (str, bytes, bytearray, Mapping, set, frozenset)):
        raise ContactSheetError(
            f"candidates must be an ordered sequence, got {type(candidates).__name__}; "
            "placement order is the caller's editorial decision and an unordered "
            "container has none"
        )
    if not isinstance(candidates, Sequence):
        raise ContactSheetError(
            f"candidates must be a Sequence, got {type(candidates).__name__}"
        )

    items = tuple(candidates)
    for index, candidate in enumerate(items):
        if not isinstance(candidate, SheetCandidate):
            raise ContactSheetError(
                f"candidate {index} is a {type(candidate).__name__}, not a SheetCandidate"
            )

    if not items:
        raise ContactSheetError(
            "no candidates; an empty sheet is a question with no picture in it and "
            "would still cost an upload"
        )

    cap = min(policy.max_cells, _CELLS_HARD_CEILING)
    if len(items) > cap:
        # Not truncated. Choosing which candidates to drop is selection, which
        # belongs to ranking-engine and album-engine, not to the compositor.
        raise ContactSheetError(
            f"{len(items)} candidates exceeds the cap of {cap}; select before composing "
            "-- dropping some here would be a silent editorial decision"
        )

    seen: dict[str, int] = {}
    for index, candidate in enumerate(items):
        if candidate.media_id in seen:
            raise ContactSheetError(
                f"media_id {candidate.media_id} appears at positions "
                f"{seen[candidate.media_id]} and {index}; two labels for one photograph "
                "makes the reply ambiguous"
            )
        seen[candidate.media_id] = index

    cell_px = policy.cell_px
    if cell_px > _CELL_PX_HARD_CEILING or cell_px < _CELL_PX_HARD_FLOOR:
        # Re-checked against the private twin: the policy validated against the
        # public constant, which a caller can rebind.
        raise ResolutionCeilingError(
            f"cell_px {cell_px} outside the hard bounds "
            f"{_CELL_PX_HARD_FLOOR}..{_CELL_PX_HARD_CEILING}"
        )

    columns = min(policy.columns, len(items))
    if columns > _COLUMNS_HARD_CEILING:
        raise ContactSheetError(f"columns {columns} above the hard ceiling")
    rows = -(-len(items) // columns)
    if rows > _ROWS_HARD_CEILING:
        raise ContactSheetError(
            f"{len(items)} candidates over {columns} columns needs {rows} rows, above the "
            f"hard ceiling of {_ROWS_HARD_CEILING}"
        )

    tiles = [_decode_tile(candidate, cell_px) for candidate in items]

    label_w, label_h = label_size(cell_label(0, 0), policy.label_scale)
    block_h = cell_px + _LABEL_GAP_PX + label_h
    width = _MARGIN_PX * 2 + columns * cell_px + (columns - 1) * policy.gutter_px
    height = _MARGIN_PX * 2 + rows * block_h + (rows - 1) * policy.gutter_px
    if max(width, height) > MAX_SHEET_EDGE_PX:  # pragma: no cover - arithmetic guard
        raise ContactSheetError(
            f"composed sheet would be {width}x{height}, above {MAX_SHEET_EDGE_PX}px"
        )

    sheet = Image.new("RGB", (width, height), BACKGROUND)

    cells: list[SheetCell] = []
    for index, (candidate, tile) in enumerate(zip(items, tiles, strict=True)):
        row, column = divmod(index, columns)
        cell_x = _MARGIN_PX + column * (cell_px + policy.gutter_px)
        cell_y = _MARGIN_PX + row * (block_h + policy.gutter_px)

        tile_w, tile_h = tile.rendered_size
        # Centred in the cell, with the leftover as plain background rather than
        # a pad colour of its own: one flat field is unambiguous, two invite the
        # model to read the pad as part of the picture.
        tile_x = cell_x + (cell_px - tile_w) // 2
        tile_y = cell_y + (cell_px - tile_h) // 2
        sheet.paste(tile.image, (tile_x, tile_y))
        # A hairline exactly around the photograph's extent, so a letterboxed
        # panorama's true edges are visible and the model is not left guessing
        # where the picture stops and the sheet starts.
        _draw_frame(sheet, tile_x - 1, tile_y - 1, tile_w + 2, tile_h + 2)

        label = cell_label(row, column)
        # Anchored to the TILE, not to the cell: exactly `_LABEL_GAP_PX` below the
        # picture it names, whatever that picture's shape. Anchoring to the cell
        # instead puts a letterboxed panorama's label ~120px from its own tile
        # and only slightly further from the row below, and "which label belongs
        # to which picture" is the one thing this sheet must never be vague about.
        label_x = tile_x + (tile_w - label_w) // 2
        label_y = tile_y + tile_h + _LABEL_GAP_PX
        if label_x < cell_x or label_x + label_w > cell_x + cell_px:
            # Unreachable while tiles are centred and `cell_px >= label_w`
            # (MIN_CELL_PX is 96, the widest two-character label is 88), which is
            # why this raises instead of clamping: a clamp would silently slide
            # the label somewhere else, and a label that is not under its own
            # picture is the one failure this sheet must never ship quietly.
            raise ContactSheetError(  # pragma: no cover - see the proof above
                f"label {label} would fall outside its cell; refusing rather than "
                "moving it somewhere the model would misread"
            )
        # A white plate under the label: the strip sits on the sheet background,
        # and pure-black-on-pure-white is the contrast the legibility note
        # assumes. One pixel of bleed either side keeps the glyph edges clean.
        _fill(sheet, label_x - 1, label_y - 1, label_w + 2, label_h + 2, LABEL_PAPER)
        draw_label(sheet, label, (label_x, label_y), policy.label_scale)

        cells.append(
            SheetCell(
                label=label,
                row=row,
                column=column,
                media_id=candidate.media_id,
                box=(tile_x, tile_y, tile_w, tile_h),
                label_box=(label_x, label_y, label_w, label_h),
                source_size=tile.source_size,
                rendered_size=tile.rendered_size,
                metadata_fields_removed=tile.metadata_fields_removed,
            )
        )

    # Trailing positions in the last row get nothing at all: no frame, no label,
    # no pad colour. Unbroken background is the only rendering a model cannot
    # mistake for a photograph.
    empty_positions = tuple(
        divmod(index, columns) for index in range(len(items), rows * columns)
    )

    if sheet.info:  # pragma: no cover - structural guard
        raise LeakError(f"composed sheet carries metadata: {sorted(sheet.info)}")

    buffer = io.BytesIO()
    sheet.save(buffer, format=IMAGE_FORMAT, optimize=False, compress_level=6)
    image_bytes = buffer.getvalue()
    _assert_no_metadata_chunks(image_bytes)

    manifest = SheetManifest(
        schema=MANIFEST_SCHEMA,
        image_format=IMAGE_FORMAT,
        image_width=width,
        image_height=height,
        columns=columns,
        rows=rows,
        cell_px=cell_px,
        label_scale=policy.label_scale,
        image_digest=_blake3_hex(image_bytes),
        pixel_digest=_blake3_hex(
            f"{width}x{height}:".encode("ascii") + sheet.tobytes()
        ),
        cells=tuple(cells),
        empty_positions=empty_positions,
    )
    # Runs the closed-vocabulary check on the real manifest, so a leak fails the
    # build rather than waiting for someone to call to_dict().
    manifest.to_dict()

    return ContactSheet(image_bytes=image_bytes, manifest=manifest)


def _fill(image: Any, x: int, y: int, width: int, height: int, colour: tuple[int, int, int]) -> None:
    for dy in range(height):
        for dx in range(width):
            image.putpixel((x + dx, y + dy), colour)


def _draw_frame(image: Any, x: int, y: int, width: int, height: int) -> None:
    """One-pixel rectangle outline. Drawn with putpixel rather than ImageDraw so
    the geometry is exactly what the manifest's `box` says it is."""
    for dx in range(width):
        image.putpixel((x + dx, y), BORDER)
        image.putpixel((x + dx, y + height - 1), BORDER)
    for dy in range(height):
        image.putpixel((x, y + dy), BORDER)
        image.putpixel((x + width - 1, y + dy), BORDER)
