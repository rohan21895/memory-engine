"""Tests for contact-sheet composition.

Written against the ways this boundary fails QUIETLY, not against the happy
path. A sheet that is missing a photograph, or that carries a GPS block, or that
downscaled an original nobody meant to send, looks exactly like a correct sheet.
So most of what is below either

  * proves a refusal happens (and that the thing refused would otherwise have
    been accepted), or
  * proves the leak the module claims to close is actually present in the INPUT
    first -- a stripping test whose fixture never had the metadata passes
    forever and means nothing. Every metadata test here asserts the marker is in
    the proxy bytes before asserting it is absent from the sheet.

unittest.TestCase so the same file runs under `python3 -m unittest discover`
(what scripts/ci/run-workspace-check.mjs uses) and under pytest.
"""

from __future__ import annotations

import io
import json
import sys
import unittest
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PACKAGE_ROOT))

from PIL import Image, ImageFile  # noqa: E402
from PIL.PngImagePlugin import PngInfo  # noqa: E402

from memory_engine_prompt import contact_sheet as cs  # noqa: E402
from memory_engine_prompt.contact_sheet import (  # noqa: E402
    BACKGROUND,
    LABEL_INK,
    LABEL_PAPER,
    MAX_SOURCE_EDGE_PX,
    MIN_RENDERED_EDGE_PX,
    ContactSheetError,
    ContactSheetPolicy,
    LeakError,
    ProxyError,
    ResolutionCeilingError,
    SheetCandidate,
    build_contact_sheet,
    cell_label,
    label_size,
    png_chunk_types,
)
from memory_engine_prompt.structured import (  # noqa: E402
    CODE_UNKNOWN_ID,
    Status,
    parse_reply,
)

# --------------------------------------------------------------------------
# Fixtures. Synthetic imagery only -- scripts/demo/make_library.py is the
# project's source of realistic test media and it too draws everything it
# writes. No real photograph is read by this suite.
# --------------------------------------------------------------------------

EXIF_MAKE = 0x010F
EXIF_MODEL = 0x0110
EXIF_ORIENTATION = 0x0112
EXIF_SOFTWARE = 0x0131
EXIF_SUB_IFD = 0x8769
EXIF_GPS_IFD = 0x8825
EXIF_DATETIME_ORIGINAL = 0x9003


def media_id(index: int) -> str:
    """A BLAKE3-shaped id, which is what MediaRecord.media_id actually is."""
    return f"{index:064x}"


def drawn_image(width: int, height: int, colour: tuple[int, int, int]) -> Image.Image:
    """A flat block with a corner mark, so a tile can be told apart by pixel."""
    image = Image.new("RGB", (width, height), colour)
    for y in range(min(4, height)):
        for x in range(min(4, width)):
            image.putpixel((x, y), (255, 255, 255))
    return image


def jpeg_bytes(
    width: int = 400,
    height: int = 300,
    colour: tuple[int, int, int] = (30, 90, 160),
    *,
    exif: bytes | None = None,
    icc_profile: bytes | None = None,
) -> bytes:
    buffer = io.BytesIO()
    kwargs: dict[str, object] = {"quality": 85, "optimize": False}
    if exif is not None:
        kwargs["exif"] = exif
    if icc_profile is not None:
        kwargs["icc_profile"] = icc_profile
    drawn_image(width, height, colour).save(buffer, "JPEG", **kwargs)
    return buffer.getvalue()


def png_bytes(
    width: int = 300,
    height: int = 200,
    colour: tuple[int, int, int] = (160, 40, 90),
    *,
    text: dict[str, str] | None = None,
    mode: str = "RGB",
) -> bytes:
    image = drawn_image(width, height, colour)
    if mode != "RGB":
        image = image.convert(mode)
    info = None
    if text:
        info = PngInfo()
        for key, value in text.items():
            info.add_text(key, value)
    buffer = io.BytesIO()
    image.save(buffer, "PNG", pnginfo=info)
    return buffer.getvalue()


def loaded_exif(*, orientation: int = 1, with_gps: bool = True) -> bytes:
    """An EXIF block shaped like a real camera's, with a GPS fix in it."""
    exif = Image.Exif()
    exif[EXIF_MAKE] = "SECRETCAMMAKE"
    exif[EXIF_MODEL] = "SECRETCAMMODEL"
    exif[EXIF_SOFTWARE] = "SECRETSOFTWARE"
    exif[EXIF_ORIENTATION] = orientation
    sub = exif.get_ifd(EXIF_SUB_IFD)
    sub[EXIF_DATETIME_ORIGINAL] = "2019:08:04 17:22:31"
    if with_gps:
        gps = exif.get_ifd(EXIF_GPS_IFD)
        gps[1] = "N"
        gps[2] = (15.0, 29.0, 54.32)
        gps[3] = "E"
        gps[4] = (73.0, 49.0, 12.34)
    return exif.tobytes()


def candidates(count: int, **kwargs: object) -> list[SheetCandidate]:
    return [
        SheetCandidate(
            media_id=media_id(index + 1),
            proxy_bytes=jpeg_bytes(colour=(20 + 30 * index % 200, 90, 140), **kwargs),  # type: ignore[arg-type]
        )
        for index in range(count)
    ]


def sample(image: Image.Image, x: int, y: int) -> tuple[int, int, int]:
    return image.convert("RGB").getpixel((x, y))  # type: ignore[return-value]


def rect_distance(box_a: tuple[int, int, int, int], box_b: tuple[int, int, int, int]) -> float:
    """Gap between two axis-aligned rectangles; 0 when they touch or overlap."""
    ax, ay, aw, ah = box_a
    bx, by, bw, bh = box_b
    dx = max(bx - (ax + aw), ax - (bx + bw), 0)
    dy = max(by - (ay + ah), ay - (by + bh), 0)
    return (dx * dx + dy * dy) ** 0.5


def boxes_overlap(box_a: tuple[int, int, int, int], box_b: tuple[int, int, int, int]) -> bool:
    ax, ay, aw, ah = box_a
    bx, by, bw, bh = box_b
    return ax < bx + bw and bx < ax + aw and ay < by + bh and by < ay + ah


# --------------------------------------------------------------------------


class DeterminismTest(unittest.TestCase):
    def test_same_candidates_produce_byte_identical_sheets(self):
        first = build_contact_sheet(candidates(7))
        second = build_contact_sheet(candidates(7))
        self.assertEqual(first.image_bytes, second.image_bytes)
        self.assertEqual(first.manifest.to_json(), second.manifest.to_json())
        self.assertEqual(first.manifest.image_digest, second.manifest.image_digest)
        self.assertEqual(first.manifest.pixel_digest, second.manifest.pixel_digest)

    def test_image_digest_is_the_digest_of_the_bytes_that_leave(self):
        # The ledger records this number as "what was uploaded". If it is taken
        # over anything other than the emitted file, the ledger is describing
        # something that never left.
        import blake3  # noqa: PLC0415

        sheet = build_contact_sheet(candidates(3))
        self.assertEqual(
            sheet.manifest.image_digest, blake3.blake3(sheet.image_bytes).hexdigest()
        )

    def test_pixel_digest_is_domain_separated_from_the_raw_pixels(self):
        # The shape is hashed with the pixels, so two sheets cannot collide on
        # raw bytes alone, and the pixel digest can never be confused with a
        # plain hash of a bitmap someone hands in.
        import blake3  # noqa: PLC0415

        sheet = build_contact_sheet(candidates(3))
        raw = sheet.open_image().convert("RGB").tobytes()
        self.assertNotEqual(sheet.manifest.pixel_digest, blake3.blake3(raw).hexdigest())
        self.assertEqual(
            sheet.manifest.pixel_digest,
            blake3.blake3(
                f"{sheet.manifest.image_width}x{sheet.manifest.image_height}:".encode("ascii")
                + raw
            ).hexdigest(),
        )

    def test_digests_are_blake3_shaped_and_distinct_for_distinct_sheets(self):
        sheet = build_contact_sheet(candidates(3))
        for digest in (sheet.manifest.image_digest, sheet.manifest.pixel_digest):
            self.assertRegex(digest, r"^[0-9a-f]{64}$")
        self.assertNotEqual(sheet.manifest.image_digest, sheet.manifest.pixel_digest)
        other = build_contact_sheet(
            [
                SheetCandidate(media_id=media_id(1), proxy_bytes=jpeg_bytes(colour=(1, 2, 3))),
                SheetCandidate(media_id=media_id(2), proxy_bytes=jpeg_bytes(colour=(9, 9, 9))),
                SheetCandidate(media_id=media_id(3), proxy_bytes=jpeg_bytes(colour=(4, 5, 6))),
            ]
        )
        self.assertNotEqual(sheet.manifest.pixel_digest, other.manifest.pixel_digest)

    def test_order_is_the_callers_and_is_preserved(self):
        forward = candidates(4)
        reversed_order = list(reversed(forward))
        first = build_contact_sheet(forward)
        second = build_contact_sheet(reversed_order)
        self.assertEqual(
            [cell.media_id for cell in first.manifest.cells],
            [candidate.media_id for candidate in forward],
        )
        self.assertEqual(
            [cell.media_id for cell in second.manifest.cells],
            [candidate.media_id for candidate in reversed_order],
        )
        # Same labels, different pictures behind them. If placement were sorted
        # internally these two sheets would be identical.
        self.assertEqual(first.manifest.labels, second.manifest.labels)
        self.assertNotEqual(first.image_bytes, second.image_bytes)

    def test_unordered_containers_are_refused(self):
        pool = candidates(3)
        for container in (set(pool), frozenset(pool), {c.media_id: c for c in pool}):
            with self.assertRaises(ContactSheetError) as caught:
                build_contact_sheet(container)  # type: ignore[arg-type]
            self.assertIn("order", str(caught.exception))

    def test_generator_is_refused_rather_than_consumed(self):
        # A generator is a one-shot iterator: accepting one would make the plan
        # unreproducible from the same argument.
        with self.assertRaises(ContactSheetError):
            build_contact_sheet(candidate for candidate in candidates(3))  # type: ignore[arg-type]


class ResolutionCeilingTest(unittest.TestCase):
    def test_proxy_at_the_ceiling_is_accepted(self):
        sheet = build_contact_sheet(
            [SheetCandidate(media_id=media_id(1), proxy_bytes=jpeg_bytes(MAX_SOURCE_EDGE_PX, 700))]
        )
        self.assertEqual(sheet.manifest.cells[0].source_size, (MAX_SOURCE_EDGE_PX, 700))

    def test_one_pixel_over_the_ceiling_is_refused(self):
        with self.assertRaises(ResolutionCeilingError) as caught:
            build_contact_sheet(
                [
                    SheetCandidate(
                        media_id=media_id(1),
                        proxy_bytes=jpeg_bytes(MAX_SOURCE_EDGE_PX + 1, 700),
                    )
                ]
            )
        message = str(caught.exception)
        self.assertIn("Refusing rather than downscaling", message)
        self.assertIn(str(MAX_SOURCE_EDGE_PX), message)

    def test_an_original_never_reaches_the_sheet_even_alongside_good_proxies(self):
        # The whole sheet fails. A composer that skipped the offending candidate
        # would emit a plausible sheet with one photograph quietly missing.
        pool = candidates(3)
        pool.insert(1, SheetCandidate(media_id=media_id(99), proxy_bytes=jpeg_bytes(4000, 3000)))
        with self.assertRaises(ResolutionCeilingError):
            build_contact_sheet(pool)

    def test_rebinding_the_public_ceiling_does_not_raise_it(self):
        original = cs.MAX_SOURCE_EDGE_PX
        try:
            cs.MAX_SOURCE_EDGE_PX = 8192
            with self.assertRaises(ResolutionCeilingError):
                build_contact_sheet(
                    [SheetCandidate(media_id=media_id(1), proxy_bytes=jpeg_bytes(2048, 1536))]
                )
        finally:
            cs.MAX_SOURCE_EDGE_PX = original

    def test_rebinding_the_public_cell_ceiling_does_not_raise_it(self):
        original = cs.MAX_CELL_PX
        try:
            cs.MAX_CELL_PX = 4096
            policy = ContactSheetPolicy(cell_px=1024)  # accepted by the rebound public check
            with self.assertRaises(ResolutionCeilingError):
                build_contact_sheet(candidates(1), policy=policy)
        finally:
            cs.MAX_CELL_PX = original

    def test_the_ceiling_is_checked_from_the_header_before_any_decode(self):
        # A file whose header declares 4000x3000 but whose scan stops 20 bytes
        # in. If the ceiling were checked after decoding, this would raise
        # ProxyError ("could not be decoded"); checking the header first makes it
        # a ResolutionCeilingError, which is the guarantee that an oversized
        # image is never decoded -- or allocated -- at this boundary.
        oversized = jpeg_bytes(4000, 3000)
        start_of_scan = oversized.index(b"\xff\xda")
        truncated = oversized[: start_of_scan + 20]
        with self.assertRaises(ResolutionCeilingError):
            build_contact_sheet(
                [SheetCandidate(media_id=media_id(1), proxy_bytes=truncated)]
            )

    def test_a_small_proxy_is_never_enlarged(self):
        sheet = build_contact_sheet(
            [SheetCandidate(media_id=media_id(1), proxy_bytes=jpeg_bytes(64, 48))]
        )
        self.assertEqual(sheet.manifest.cells[0].rendered_size, (64, 48))

    def test_the_hard_cell_floor_survives_a_rebound_public_floor(self):
        # Mirror of the ceiling case: MIN_CELL_PX is public and rebindable, the
        # private twin is what actually stops a 32px cell being composed.
        original = cs.MIN_CELL_PX
        try:
            cs.MIN_CELL_PX = 8
            policy = ContactSheetPolicy(cell_px=32)  # accepted by the rebound check
            with self.assertRaises(ResolutionCeilingError):
                build_contact_sheet(candidates(1), policy=policy)
        finally:
            cs.MIN_CELL_PX = original


class FitTest(unittest.TestCase):
    """`_fit` decides how much of the model's attention each picture gets.

    Pinned with worked values rather than by re-deriving the formula, because a
    test that recomputes the implementation agrees with every version of it.
    """

    def test_fit_rounds_to_nearest_rather_than_down(self):
        # 900x70 into a 256 cell: 70 * (256/900) = 19.911. Rounding down loses a
        # row of the picture and shifts the label that is anchored to it.
        self.assertEqual(cs._fit((900, 70), 256), (256, 20))
        self.assertEqual(cs._fit((70, 900), 256), (20, 256))

    def test_fit_preserves_aspect_and_never_exceeds_the_cell(self):
        for size in ((400, 300), (300, 400), (1024, 1024), (513, 97)):
            with self.subTest(size=size):
                fitted = cs._fit(size, 256)
                self.assertLessEqual(max(fitted), 256)

    def test_fit_never_enlarges(self):
        self.assertEqual(cs._fit((64, 48), 256), (64, 48))
        self.assertEqual(cs._fit((1, 1), 256), (1, 1))

    def test_a_tile_fitting_to_exactly_the_floor_is_accepted(self):
        # 960x30 into a 256 cell is exactly 256x8, the floor. The floor rejects
        # what is BELOW it, not what sits on it.
        self.assertEqual(cs._fit((960, 30), 256), (256, MIN_RENDERED_EDGE_PX))
        sheet = build_contact_sheet(
            [SheetCandidate(media_id=media_id(1), proxy_bytes=jpeg_bytes(960, 30))]
        )
        self.assertEqual(sheet.manifest.cells[0].rendered_size[1], MIN_RENDERED_EDGE_PX)


class MetadataStrippingTest(unittest.TestCase):
    def test_exif_and_gps_do_not_reach_the_sheet(self):
        proxy = jpeg_bytes(exif=loaded_exif())
        # The fixture must actually carry what we claim to strip, or this test
        # would pass against a module that does nothing.
        for marker in (b"SECRETCAMMAKE", b"SECRETCAMMODEL", b"SECRETSOFTWARE", b"2019:08:04"):
            self.assertIn(marker, proxy)

        sheet = build_contact_sheet([SheetCandidate(media_id=media_id(1), proxy_bytes=proxy)])
        for marker in (b"SECRETCAMMAKE", b"SECRETCAMMODEL", b"SECRETSOFTWARE", b"2019:08:04"):
            self.assertNotIn(marker, sheet.image_bytes)
        # The byte scan above is necessary but not sufficient -- a compressed
        # zTXt block would hide the marker from it. The chunk table is the real
        # answer, so assert on that too.
        self.assertEqual(set(png_chunk_types(sheet.image_bytes)), {"IHDR", "IDAT", "IEND"})
        self.assertEqual(sheet.open_image().info, {})

    def test_png_text_chunks_carrying_a_path_do_not_survive(self):
        proxy = png_bytes(text={"Comment": "/Users/someone/Pictures/IMG_0042.jpg"})
        self.assertIn(b"/Users/someone/Pictures/IMG_0042.jpg", proxy)
        sheet = build_contact_sheet([SheetCandidate(media_id=media_id(1), proxy_bytes=proxy)])
        self.assertNotIn(b"/Users/someone", sheet.image_bytes)
        self.assertNotIn(b"IMG_0042", sheet.image_bytes)
        self.assertEqual(set(png_chunk_types(sheet.image_bytes)), {"IHDR", "IDAT", "IEND"})

    def test_icc_profile_is_stripped_and_counted(self):
        profile = b"NOTAREALICCPROFILE" * 8
        proxy = jpeg_bytes(icc_profile=profile)
        self.assertIn(b"NOTAREALICCPROFILE", proxy)
        sheet = build_contact_sheet([SheetCandidate(media_id=media_id(1), proxy_bytes=proxy)])
        self.assertNotIn(b"NOTAREALICCPROFILE", sheet.image_bytes)
        self.assertGreater(sheet.manifest.cells[0].metadata_fields_removed, 0)

    def test_metadata_count_is_recorded_not_the_keys(self):
        # The KEY of a PNG text chunk is author-controlled and can itself be a
        # path, so the manifest records how many entries were dropped and never
        # what they were called.
        proxy = png_bytes(text={"/Users/someone/secret.jpg": "value"})
        sheet = build_contact_sheet([SheetCandidate(media_id=media_id(1), proxy_bytes=proxy)])
        self.assertNotIn("someone", sheet.manifest.to_json())
        self.assertIsInstance(sheet.manifest.cells[0].metadata_fields_removed, int)

    def test_an_unapplied_exif_orientation_is_refused_not_applied(self):
        proxy = jpeg_bytes(exif=loaded_exif(orientation=6))
        with self.assertRaises(ProxyError) as caught:
            build_contact_sheet([SheetCandidate(media_id=media_id(1), proxy_bytes=proxy)])
        self.assertIn("orientation 6", str(caught.exception))

    def test_orientation_one_is_the_normal_case(self):
        proxy = jpeg_bytes(exif=loaded_exif(orientation=1))
        sheet = build_contact_sheet([SheetCandidate(media_id=media_id(1), proxy_bytes=proxy)])
        self.assertEqual(len(sheet.manifest.cells), 1)


class ManifestVocabularyTest(unittest.TestCase):
    def test_manifest_holds_only_labels_ids_digests_and_numbers(self):
        sheet = build_contact_sheet(candidates(5))
        payload = json.loads(sheet.manifest.to_json())
        self.assertEqual(payload["schema"], cs.MANIFEST_SCHEMA)
        for cell in payload["cells"]:
            self.assertRegex(cell["label"], r"^[A-H][1-8]$")
            self.assertRegex(cell["media_id"], r"^[0-9a-f]{64}$")

    def test_a_future_field_holding_a_path_fails_the_vocabulary_check(self):
        # Simulates the edit this check exists for: someone adds a helpful
        # "source" to a cell six months from now.
        sheet = build_contact_sheet(candidates(2))
        payload = sheet.manifest.to_dict()
        payload["cells"][0]["source"] = "/Users/someone/Pictures/IMG_0042.jpg"
        with self.assertRaises(LeakError) as caught:
            cs._assert_manifest_clean(payload)
        self.assertIn("undeclared", str(caught.exception))

    def test_a_future_top_level_field_fails_the_vocabulary_check(self):
        sheet = build_contact_sheet(candidates(2))
        payload = sheet.manifest.to_dict()
        payload["source_folder"] = "/Users/someone/Pictures"
        with self.assertRaises(LeakError) as caught:
            cs._assert_manifest_clean(payload)
        self.assertIn("undeclared", str(caught.exception))

    def test_an_undeclared_field_fails_even_when_its_value_is_innocuous(self):
        # The value check cannot catch this one -- an int passes every string
        # rule -- so only the key check stands between a new field and the
        # manifest. Tested separately because a value-shaped test would pass
        # against a key check that had been inverted into a no-op.
        sheet = build_contact_sheet(candidates(2))
        payload = sheet.manifest.to_dict()
        payload["scan_session_number"] = 7
        with self.assertRaises(LeakError):
            cs._assert_manifest_clean(payload)

    def test_a_value_with_a_trailing_newline_fails_the_vocabulary_check(self):
        # `re.match` would accept it because `$` matches before a final newline.
        # A manifest value that can carry a newline can forge a line in any log
        # or ledger entry built from the manifest.
        sheet = build_contact_sheet(candidates(2))
        payload = sheet.manifest.to_dict()
        payload["cells"][0]["media_id"] = media_id(1) + "\n"
        with self.assertRaises(LeakError):
            cs._assert_manifest_clean(payload)

    def test_the_vocabulary_check_runs_during_build_not_only_on_serialise(self):
        # Narrow the declared cell vocabulary and the build itself must fail. If
        # the check only ran from to_json(), a leaking manifest would be handed
        # back and only fail later, if anyone serialised it.
        original = cs._CELL_KEYS
        try:
            cs._CELL_KEYS = frozenset(original - {"label_box"})
            with self.assertRaises(LeakError):
                build_contact_sheet(candidates(2))
        finally:
            cs._CELL_KEYS = original

    def test_the_chunk_check_runs_during_build(self):
        original = cs._ALLOWED_PNG_CHUNKS
        try:
            cs._ALLOWED_PNG_CHUNKS = ("IHDR", "IEND")
            with self.assertRaises(LeakError):
                build_contact_sheet(candidates(2))
        finally:
            cs._ALLOWED_PNG_CHUNKS = original

    def test_a_declared_field_holding_a_path_still_fails(self):
        sheet = build_contact_sheet(candidates(2))
        payload = sheet.manifest.to_dict()
        payload["cells"][0]["media_id"] = "/Users/someone/Pictures/IMG_0042.jpg"
        with self.assertRaises(LeakError):
            cs._assert_manifest_clean(payload)

    def test_a_filename_shaped_value_fails_even_without_a_directory(self):
        sheet = build_contact_sheet(candidates(2))
        payload = sheet.manifest.to_dict()
        payload["cells"][0]["media_id"] = "IMG_0042.JPG"
        with self.assertRaises(LeakError):
            cs._assert_manifest_clean(payload)

    def test_media_id_that_is_a_path_is_refused_at_construction(self):
        with self.assertRaises(ContactSheetError):
            SheetCandidate(media_id="/Users/someone/IMG_0042.jpg", proxy_bytes=jpeg_bytes())

    def test_media_id_that_is_a_filename_is_refused_at_construction(self):
        with self.assertRaises(ContactSheetError) as caught:
            SheetCandidate(media_id="IMG_0042.jpg", proxy_bytes=jpeg_bytes())
        self.assertIn("filename", str(caught.exception))

    def test_media_id_with_a_trailing_newline_is_refused(self):
        # `re.match` would accept this because `$` matches before a final
        # newline; the id would then forge a second line in any log that echoes
        # it. Same trap that was found in structured.py's slug filter.
        with self.assertRaises(ContactSheetError):
            SheetCandidate(media_id=media_id(1) + "\n", proxy_bytes=jpeg_bytes())

    def test_media_id_must_be_a_string(self):
        with self.assertRaises(ContactSheetError):
            SheetCandidate(media_id=42, proxy_bytes=jpeg_bytes())  # type: ignore[arg-type]


class LoudFailureTest(unittest.TestCase):
    def test_missing_proxy_is_an_error_not_a_blank_cell(self):
        with self.assertRaises(ContactSheetError) as caught:
            SheetCandidate(media_id=media_id(1), proxy_bytes=None)  # type: ignore[arg-type]
        self.assertIn("never an empty cell", str(caught.exception))

    def test_empty_proxy_bytes_are_an_error(self):
        with self.assertRaises(ContactSheetError):
            SheetCandidate(media_id=media_id(1), proxy_bytes=b"")

    def test_corrupt_proxy_raises_rather_than_rendering_something(self):
        with self.assertRaises(ProxyError) as caught:
            build_contact_sheet(
                [SheetCandidate(media_id=media_id(1), proxy_bytes=b"\x00\x01not an image" * 40)]
            )
        self.assertIn(media_id(1), str(caught.exception))

    def test_truncated_proxy_raises_rather_than_completing_with_grey(self):
        whole = jpeg_bytes(400, 300)
        truncated = whole[: len(whole) // 2]
        with self.assertRaises(ProxyError):
            build_contact_sheet(
                [SheetCandidate(media_id=media_id(1), proxy_bytes=truncated)]
            )

    def test_load_truncated_images_global_blocks_composition_entirely(self):
        original = ImageFile.LOAD_TRUNCATED_IMAGES
        try:
            ImageFile.LOAD_TRUNCATED_IMAGES = True
            with self.assertRaises(ProxyError) as caught:
                build_contact_sheet(candidates(2))
            self.assertIn("LOAD_TRUNCATED_IMAGES", str(caught.exception))
        finally:
            ImageFile.LOAD_TRUNCATED_IMAGES = original

    def test_load_truncated_images_would_otherwise_hide_a_truncated_proxy(self):
        # Proves the guard above is guarding something real: with the global set
        # and the guard bypassed, Pillow decodes the truncated file happily.
        whole = jpeg_bytes(400, 300)
        truncated = whole[: len(whole) // 2]
        original = ImageFile.LOAD_TRUNCATED_IMAGES
        try:
            ImageFile.LOAD_TRUNCATED_IMAGES = True
            handle = Image.open(io.BytesIO(truncated))
            handle.load()  # no exception: the rest of the frame is filled in
            self.assertEqual(handle.size, (400, 300))
        finally:
            ImageFile.LOAD_TRUNCATED_IMAGES = original

    def test_extreme_aspect_ratio_is_refused_with_the_floor_named(self):
        with self.assertRaises(ProxyError) as caught:
            build_contact_sheet(
                [SheetCandidate(media_id=media_id(1), proxy_bytes=jpeg_bytes(1000, 5))]
            )
        self.assertIn(str(MIN_RENDERED_EDGE_PX), str(caught.exception))

    def test_a_merely_wide_panorama_is_still_accepted(self):
        sheet = build_contact_sheet(
            [SheetCandidate(media_id=media_id(1), proxy_bytes=jpeg_bytes(1000, 60))]
        )
        width, height = sheet.manifest.cells[0].rendered_size
        self.assertGreaterEqual(height, MIN_RENDERED_EDGE_PX)
        self.assertEqual(width, 256)

    def test_non_photographic_mode_is_refused(self):
        buffer = io.BytesIO()
        Image.new("F", (64, 64), 0.5).save(buffer, "TIFF")
        with self.assertRaises(ProxyError) as caught:
            build_contact_sheet(
                [SheetCandidate(media_id=media_id(1), proxy_bytes=buffer.getvalue())]
            )
        self.assertIn("mode", str(caught.exception))

    def test_grayscale_and_palette_proxies_are_accepted(self):
        for mode in ("L", "P"):
            with self.subTest(mode=mode):
                sheet = build_contact_sheet(
                    [SheetCandidate(media_id=media_id(1), proxy_bytes=png_bytes(mode=mode))]
                )
                self.assertEqual(len(sheet.manifest.cells), 1)

    def test_transparency_composites_onto_white_not_black(self):
        # Both alpha modes, because they take different branches: a
        # transparent tile flattened to black is exactly the "dark photograph"
        # the model would then describe and score.
        for mode, transparent in (("RGBA", (0, 0, 0, 0)), ("LA", (0, 0))):
            with self.subTest(mode=mode):
                buffer = io.BytesIO()
                Image.new(mode, (200, 200), transparent).save(buffer, "PNG")
                sheet = build_contact_sheet(
                    [SheetCandidate(media_id=media_id(1), proxy_bytes=buffer.getvalue())]
                )
                box = sheet.manifest.cells[0].box
                centre = sample(
                    sheet.open_image(), box[0] + box[2] // 2, box[1] + box[3] // 2
                )
                self.assertEqual(centre, cs.ALPHA_BACKDROP)

    def test_the_tile_handed_to_the_compositor_carries_no_container_metadata(self):
        # The rebuild-from-raw-pixels step, tested where it happens. The control
        # below is the point: Pillow's convert() COPIES .info, so without the
        # rebuild the tile pasted onto the sheet is still carrying the JPEG's
        # exif and icc entries.
        proxy = jpeg_bytes(exif=loaded_exif(), icc_profile=b"NOTAREALICCPROFILE" * 8)
        candidate = SheetCandidate(media_id=media_id(1), proxy_bytes=proxy)

        control = Image.open(io.BytesIO(proxy)).convert("RGB")
        self.assertNotEqual(control.info, {}, "control is vacuous; fixture carries nothing")
        self.assertIn("exif", control.info)

        tile = cs._decode_tile(candidate, 256)
        self.assertEqual(tile.image.info, {})

    def test_no_candidates_is_refused(self):
        with self.assertRaises(ContactSheetError) as caught:
            build_contact_sheet([])
        self.assertIn("empty sheet", str(caught.exception))

    def test_over_the_cap_is_refused_not_truncated(self):
        policy = ContactSheetPolicy(max_cells=4)
        with self.assertRaises(ContactSheetError) as caught:
            build_contact_sheet(candidates(5), policy=policy)
        self.assertIn("select before composing", str(caught.exception))

    def test_exactly_the_cap_is_accepted(self):
        policy = ContactSheetPolicy(max_cells=4)
        sheet = build_contact_sheet(candidates(4), policy=policy)
        self.assertEqual(len(sheet.manifest.cells), 4)

    def test_duplicate_media_id_is_refused(self):
        pool = candidates(3)
        pool[2] = SheetCandidate(media_id=pool[0].media_id, proxy_bytes=jpeg_bytes())
        with self.assertRaises(ContactSheetError) as caught:
            build_contact_sheet(pool)
        self.assertIn("ambiguous", str(caught.exception))

    def test_a_non_candidate_in_the_list_is_refused(self):
        pool: list[object] = list(candidates(2))
        pool.append({"media_id": media_id(9), "proxy_bytes": jpeg_bytes()})
        with self.assertRaises(ContactSheetError):
            build_contact_sheet(pool)  # type: ignore[arg-type]

    def test_a_foreign_policy_object_is_refused(self):
        # Every value here is legal, so the refusal can only come from the type
        # check. A duck-typed policy is one that never ran __post_init__, which
        # is where every bound in this module is enforced.
        class FakePolicy:
            cell_px = 256
            columns = 6
            max_cells = 40
            label_scale = 3
            gutter_px = 16

        with self.assertRaises(ContactSheetError) as caught:
            build_contact_sheet(candidates(1), policy=FakePolicy())  # type: ignore[arg-type]
        self.assertIn("ContactSheetPolicy", str(caught.exception))


class LayoutTest(unittest.TestCase):
    def test_grid_shrinks_to_the_candidate_count(self):
        sheet = build_contact_sheet(candidates(3), policy=ContactSheetPolicy(columns=6))
        self.assertEqual(sheet.manifest.columns, 3)
        self.assertEqual(sheet.manifest.rows, 1)
        self.assertEqual(sheet.manifest.empty_positions, ())

    def test_partial_last_row_reports_its_empty_positions(self):
        sheet = build_contact_sheet(candidates(7), policy=ContactSheetPolicy(columns=4))
        self.assertEqual(sheet.manifest.rows, 2)
        self.assertEqual(sheet.manifest.empty_positions, ((1, 3),))

    def test_empty_positions_are_unbroken_background(self):
        policy = ContactSheetPolicy(columns=4)
        sheet = build_contact_sheet(candidates(7), policy=policy)
        image = sheet.open_image().convert("RGB")
        row, column = sheet.manifest.empty_positions[0]
        label_w, label_h = label_size("A1", policy.label_scale)
        block_h = policy.cell_px + 3 + label_h
        x0 = 16 + column * (policy.cell_px + policy.gutter_px)
        y0 = 16 + row * (block_h + policy.gutter_px)
        seen = {
            image.getpixel((x0 + dx, y0 + dy))
            for dx in range(0, policy.cell_px, 7)
            for dy in range(0, block_h, 7)
        }
        # Not a frame, not a label, not a pad colour -- nothing a model could
        # read as a photograph.
        self.assertEqual(seen, {BACKGROUND})
        # And light, absolutely rather than relative to the constant: a dark
        # empty cell is precisely what gets described as "a dark photograph".
        for channel in BACKGROUND:
            self.assertGreater(channel, 200)

    def test_labels_are_unique_and_follow_reading_order(self):
        sheet = build_contact_sheet(candidates(7), policy=ContactSheetPolicy(columns=3))
        labels = sheet.manifest.labels
        self.assertEqual(len(set(labels)), len(labels))
        self.assertEqual(labels[:4], ("A1", "A2", "A3", "B1"))
        for cell in sheet.manifest.cells:
            self.assertEqual(cell.label, cell_label(cell.row, cell.column))

    def test_each_label_sits_nearer_its_own_tile_than_any_other(self):
        # The single property the sheet must never be vague about. Checked on a
        # deliberately ragged layout: letterboxed panoramas and pillarboxed
        # portraits put labels at wildly different heights within a row.
        shapes = [(900, 70), (300, 900), (400, 300), (256, 256), (700, 120), (120, 700)]
        pool = [
            SheetCandidate(media_id=media_id(index + 1), proxy_bytes=jpeg_bytes(w, h))
            for index, (w, h) in enumerate(shapes)
        ]
        sheet = build_contact_sheet(pool, policy=ContactSheetPolicy(columns=3))
        cells = sheet.manifest.cells
        for cell in cells:
            own = rect_distance(cell.label_box, cell.box)
            for other in cells:
                if other.label == cell.label:
                    continue
                self.assertLess(
                    own,
                    rect_distance(cell.label_box, other.box),
                    f"{cell.label} is not closest to its own tile",
                )

    def test_labels_never_overlap_any_tile(self):
        shapes = [(900, 70), (300, 900), (400, 300), (256, 256), (700, 120), (120, 700)]
        pool = [
            SheetCandidate(media_id=media_id(index + 1), proxy_bytes=jpeg_bytes(w, h))
            for index, (w, h) in enumerate(shapes)
        ]
        sheet = build_contact_sheet(pool, policy=ContactSheetPolicy(columns=3))
        for cell in sheet.manifest.cells:
            for other in sheet.manifest.cells:
                self.assertFalse(
                    boxes_overlap(cell.label_box, other.box),
                    f"label {cell.label} overlaps tile {other.label}",
                )

    def test_manifest_box_is_where_the_picture_actually_is(self):
        # Distinct colours per tile, then sample the centre of each declared box
        # and check the right picture is there. Catches a row/column transpose,
        # which is otherwise invisible and puts the model's answer on the wrong
        # photograph.
        colours = [(200, 20, 20), (20, 200, 20), (20, 20, 200), (200, 200, 20), (20, 200, 200)]
        pool = [
            SheetCandidate(media_id=media_id(index + 1), proxy_bytes=png_bytes(colour=colour))
            for index, colour in enumerate(colours)
        ]
        sheet = build_contact_sheet(pool, policy=ContactSheetPolicy(columns=2))
        image = sheet.open_image().convert("RGB")
        for index, cell in enumerate(sheet.manifest.cells):
            x, y, w, h = cell.box
            self.assertEqual(image.getpixel((x + w // 2, y + h // 2)), colours[index])

    def test_a_hairline_frames_the_picture_extent(self):
        sheet = build_contact_sheet(
            [SheetCandidate(media_id=media_id(1), proxy_bytes=jpeg_bytes(900, 70))]
        )
        image = sheet.open_image().convert("RGB")
        x, y, w, h = sheet.manifest.cells[0].box
        self.assertEqual(image.getpixel((x - 1, y - 1)), cs.BORDER)
        self.assertEqual(image.getpixel((x + w, y + h)), cs.BORDER)
        # And the background is still background one pixel further out.
        self.assertEqual(image.getpixel((x - 2, y - 2)), BACKGROUND)

    def test_sheet_dimensions_match_the_manifest(self):
        sheet = build_contact_sheet(candidates(5))
        image = sheet.open_image()
        self.assertEqual(
            image.size, (sheet.manifest.image_width, sheet.manifest.image_height)
        )

    def test_canvas_arithmetic_pinned_by_worked_values(self):
        # Worked by hand rather than recomputed from the module's own formula,
        # which is the only way this catches an off-by-one-gutter or a row that
        # forgot the tile-to-label gap.
        #
        #   cell 256, gutter 16, margin 16, label 21 tall with a 3px gap
        #   block height = 256 + 3 + 21                       = 280
        #   width  = 16 + 4*256 + 3*16 + 16                   = 1104
        #   height = 16 + 2*280 + 1*16 + 16                   =  608
        policy = ContactSheetPolicy(columns=4, cell_px=256, gutter_px=16, label_scale=3)
        sheet = build_contact_sheet(candidates(7), policy=policy)
        self.assertEqual((sheet.manifest.image_width, sheet.manifest.image_height), (1104, 608))
        self.assertEqual(sheet.open_image().size, (1104, 608))

    def test_tiles_are_centred_in_their_cell(self):
        # Pillarboxed and letterboxed shapes, so a tile pinned to the cell's
        # top-left corner is visible as a difference.
        shapes = [(300, 900), (900, 300), (256, 256), (400, 300)]
        pool = [
            SheetCandidate(media_id=media_id(index + 1), proxy_bytes=jpeg_bytes(w, h))
            for index, (w, h) in enumerate(shapes)
        ]
        policy = ContactSheetPolicy(columns=2)
        sheet = build_contact_sheet(pool, policy=policy)
        label_h = label_size("A1", policy.label_scale)[1]
        block_h = policy.cell_px + 3 + label_h
        for cell in sheet.manifest.cells:
            cell_x = cs._MARGIN_PX + cell.column * (policy.cell_px + policy.gutter_px)
            cell_y = cs._MARGIN_PX + cell.row * (block_h + policy.gutter_px)
            x, y, w, h = cell.box
            self.assertEqual(x - cell_x, (policy.cell_px - w) // 2, f"{cell.label} x")
            self.assertEqual(y - cell_y, (policy.cell_px - h) // 2, f"{cell.label} y")

    def test_labels_are_centred_under_their_own_tile(self):
        shapes = [(300, 900), (900, 300), (256, 256), (400, 300)]
        pool = [
            SheetCandidate(media_id=media_id(index + 1), proxy_bytes=jpeg_bytes(w, h))
            for index, (w, h) in enumerate(shapes)
        ]
        sheet = build_contact_sheet(pool, policy=ContactSheetPolicy(columns=2))
        for cell in sheet.manifest.cells:
            tile_x, _, tile_w, _ = cell.box
            label_x, _, label_w, _ = cell.label_box
            self.assertEqual(label_x, tile_x + (tile_w - label_w) // 2, cell.label)

    def test_the_label_follows_the_tile_not_the_cell(self):
        # Centring under the tile and centring in the cell agree for most
        # geometries -- they differ only when the two halvings round the other
        # way. 310x900 into a 96px cell fits to 33px wide, and there the two
        # rules disagree by a pixel, which is the only configuration that can
        # tell them apart. Without this case the difference is untestable and
        # "anchored to the tile" is an unverified claim.
        policy = ContactSheetPolicy(cell_px=96, columns=1, label_scale=2)
        sheet = build_contact_sheet(
            [SheetCandidate(media_id=media_id(1), proxy_bytes=jpeg_bytes(310, 900))],
            policy=policy,
        )
        cell = sheet.manifest.cells[0]
        tile_x, _, tile_w, _ = cell.box
        label_x, _, label_w, _ = cell.label_box
        self.assertEqual(tile_w, 33)
        self.assertEqual(label_x, tile_x + (tile_w - label_w) // 2)
        cell_x = cs._MARGIN_PX
        self.assertNotEqual(label_x, cell_x + (policy.cell_px - label_w) // 2)


class LabelRenderingTest(unittest.TestCase):
    def expected_bitmap(self, text: str, scale: int) -> set[tuple[int, int]]:
        ink: set[tuple[int, int]] = set()
        pen = 0
        for character in text:
            for row_index, row in enumerate(cs._GLYPHS[character]):
                for column_index, pixel in enumerate(row):
                    if pixel != "#":
                        continue
                    for dy in range(scale):
                        for dx in range(scale):
                            ink.add(
                                (pen + column_index * scale + dx, row_index * scale + dy)
                            )
            pen += cs._GLYPH_ADVANCE * scale
        return ink

    def test_every_label_is_drawn_exactly_as_the_glyph_table_says(self):
        policy = ContactSheetPolicy(columns=3, label_scale=3)
        sheet = build_contact_sheet(candidates(5), policy=policy)
        image = sheet.open_image().convert("RGB")
        for cell in sheet.manifest.cells:
            x, y, w, h = cell.label_box
            expected = self.expected_bitmap(cell.label, policy.label_scale)
            drawn = {
                (dx, dy)
                for dy in range(h)
                for dx in range(w)
                if image.getpixel((x + dx, y + dy)) == LABEL_INK
            }
            self.assertEqual(drawn, expected, f"label {cell.label} not drawn as declared")

    def test_labels_are_black_on_white_with_a_quiet_zone(self):
        sheet = build_contact_sheet(candidates(2))
        image = sheet.open_image().convert("RGB")
        x, y, w, h = sheet.manifest.cells[0].label_box
        colours = {
            image.getpixel((x + dx, y + dy)) for dy in range(h) for dx in range(w)
        }
        self.assertEqual(colours, {LABEL_INK, LABEL_PAPER})
        for dx in range(-1, w + 1):
            self.assertEqual(image.getpixel((x + dx, y - 1)), LABEL_PAPER)
            self.assertEqual(image.getpixel((x + dx, y + h)), LABEL_PAPER)

    def test_stroke_width_and_cap_height_match_the_stated_assumption(self):
        # The legibility argument in draw_label's docstring rests on these two
        # numbers. If a change makes labels smaller, this fails and the docstring
        # has to be re-argued rather than quietly falsified.
        policy = ContactSheetPolicy(label_scale=3)
        sheet = build_contact_sheet(candidates(1), policy=policy)
        _, _, _, height = sheet.manifest.cells[0].label_box
        self.assertEqual(height, 21)
        image = sheet.open_image().convert("RGB")
        x, y, _, _ = sheet.manifest.cells[0].label_box
        # The "A" glyph's left stem: three consecutive ink pixels on its row.
        run = [
            image.getpixel((x + dx, y + 3 * 3)) == LABEL_INK for dx in range(3)
        ]
        self.assertEqual(run, [True, True, True])

    def test_label_size_accounts_for_inter_character_gaps(self):
        self.assertEqual(label_size("A1", 3), (33, 21))
        self.assertEqual(label_size("A1", 2), (22, 14))

    def test_cell_label_is_the_single_derivation(self):
        self.assertEqual(cell_label(0, 0), "A1")
        self.assertEqual(cell_label(7, 7), "H8")
        for bad in ((-1, 0), (0, -1), (8, 0), (0, 8)):
            with self.assertRaises(ContactSheetError):
                cell_label(*bad)
        with self.assertRaises(ContactSheetError):
            cell_label(True, 0)

    def test_every_label_the_ceilings_permit_has_a_glyph(self):
        for row in range(cs._ROWS_HARD_CEILING):
            for column in range(cs._COLUMNS_HARD_CEILING):
                label = cell_label(row, column)
                for character in label:
                    self.assertIn(character, cs._GLYPHS)

    def test_glyph_table_is_uniform(self):
        for name, rows in cs._GLYPHS.items():
            self.assertEqual(len(rows), cs._GLYPH_H, name)
            for row in rows:
                self.assertEqual(len(row), cs._GLYPH_W, name)
                self.assertEqual(set(row) - {"#", "."}, set(), name)

    def test_label_alphabet_is_positional_so_cross_class_misreads_fail_closed(self):
        # A row letter can only appear in position 0 and a column digit only in
        # position 1, so reading "B" as "8" yields "88", which is not a label and
        # is rejected downstream rather than pointing at another photograph.
        sheet = build_contact_sheet(candidates(8), policy=ContactSheetPolicy(columns=8))
        request = sheet.manifest.request(purpose="misread-check")
        for misread in ("88", "6G", "1A", "b1", "A9", "I1"):
            self.assertNotIn(misread, request.allowed_ids)

    def test_draw_label_refuses_a_scale_outside_the_band(self):
        image = Image.new("RGB", (100, 100), BACKGROUND)
        for scale in (1, 9):
            with self.assertRaises(ContactSheetError):
                cs.draw_label(image, "A1", (0, 0), scale)

    def test_draw_label_refuses_a_character_with_no_glyph(self):
        image = Image.new("RGB", (200, 100), BACKGROUND)
        with self.assertRaises(ContactSheetError):
            cs.draw_label(image, "Z9", (0, 0), 3)


class PolicyTest(unittest.TestCase):
    def test_cell_px_bounds(self):
        with self.assertRaises(ContactSheetError):
            ContactSheetPolicy(cell_px=cs.MAX_CELL_PX + 1)
        with self.assertRaises(ContactSheetError):
            ContactSheetPolicy(cell_px=cs.MIN_CELL_PX - 1)
        self.assertEqual(ContactSheetPolicy(cell_px=cs.MAX_CELL_PX).cell_px, cs.MAX_CELL_PX)
        self.assertEqual(ContactSheetPolicy(cell_px=cs.MIN_CELL_PX).cell_px, cs.MIN_CELL_PX)

    def test_column_and_cap_bounds(self):
        with self.assertRaises(ContactSheetError):
            ContactSheetPolicy(columns=0)
        with self.assertRaises(ContactSheetError):
            ContactSheetPolicy(columns=cs.MAX_COLUMNS + 1)
        with self.assertRaises(ContactSheetError):
            ContactSheetPolicy(max_cells=0)
        with self.assertRaises(ContactSheetError):
            ContactSheetPolicy(max_cells=cs.MAX_CELLS + 1)

    def test_label_scale_bounds(self):
        with self.assertRaises(ContactSheetError):
            ContactSheetPolicy(label_scale=cs.MIN_LABEL_SCALE - 1)
        with self.assertRaises(ContactSheetError):
            ContactSheetPolicy(label_scale=cs.MAX_LABEL_SCALE + 1)

    def test_gutter_must_keep_labels_grouped_with_their_own_row(self):
        with self.assertRaises(ContactSheetError) as caught:
            ContactSheetPolicy(gutter_px=cs._MIN_GUTTER_PX - 1)
        self.assertIn("closer to the tile it names", str(caught.exception))
        with self.assertRaises(ContactSheetError):
            ContactSheetPolicy(gutter_px=1000)
        # Worked value, not `_MIN_GUTTER_PX - 1`: the gap between a tile and its
        # own label is 3px, so a row gutter of 4 would put the row below almost
        # as close to a label as the picture it names. Written absolutely so
        # weakening the invariant itself fails here.
        for too_tight in (3, 4, 8):
            with self.assertRaises(ContactSheetError):
                ContactSheetPolicy(gutter_px=too_tight)
        self.assertEqual(ContactSheetPolicy(gutter_px=9).gutter_px, 9)

    def test_bools_are_not_ints_here(self):
        for kwargs in (
            {"cell_px": True},
            {"columns": True},
            {"max_cells": True},
            {"label_scale": True},
            {"gutter_px": True},
        ):
            with self.assertRaises(ContactSheetError):
                ContactSheetPolicy(**kwargs)  # type: ignore[arg-type]

    def test_full_grid_at_the_hard_ceilings_still_composes(self):
        policy = ContactSheetPolicy(columns=8, max_cells=64, cell_px=cs.MAX_CELL_PX)
        sheet = build_contact_sheet(candidates(64), policy=policy)
        self.assertEqual(sheet.manifest.rows, 8)
        self.assertEqual(sheet.manifest.labels[-1], "H8")
        self.assertLessEqual(sheet.manifest.image_width, cs.MAX_SHEET_EDGE_PX)
        self.assertLessEqual(sheet.manifest.image_height, cs.MAX_SHEET_EDGE_PX)


class StructuredIntegrationTest(unittest.TestCase):
    def reply(self, request, labels, notes=None):
        payload = {
            "request_id": request.effective_request_id,
            "items": [{"id": label, "score": 0.5} for label in labels],
        }
        if notes is not None:
            payload["notes"] = notes
        return json.dumps(payload)

    def test_allowed_ids_are_exactly_the_labels_on_the_sheet(self):
        sheet = build_contact_sheet(candidates(5), policy=ContactSheetPolicy(columns=3))
        request = sheet.manifest.request(purpose="album-hero")
        self.assertEqual(tuple(request.allowed_ids), sheet.manifest.labels)

    def test_round_trip_from_sheet_to_media_ids(self):
        pool = candidates(6)
        sheet = build_contact_sheet(pool, policy=ContactSheetPolicy(columns=3))
        request = sheet.manifest.request(purpose="album-hero", max_items=3)
        result = parse_reply(self.reply(request, ["B3", "A1", "A2"]), request)
        self.assertIs(result.status, Status.OK)
        self.assertEqual(
            sheet.manifest.resolve(result),
            (pool[5].media_id, pool[0].media_id, pool[1].media_id),
        )

    def test_a_label_that_was_not_on_the_sheet_is_rejected(self):
        sheet = build_contact_sheet(candidates(4), policy=ContactSheetPolicy(columns=4))
        request = sheet.manifest.request(purpose="album-hero")
        # A4 exists; B1 does not, because the grid is one row of four.
        result = parse_reply(self.reply(request, ["A4", "B1"]), request)
        self.assertIn(CODE_UNKNOWN_ID, [rejection.code for rejection in result.rejections])
        self.assertNotIn("B1", result.usable_ids)

    def test_a_media_id_returned_instead_of_a_label_is_rejected(self):
        # The model has never seen a media id, so this can only be a
        # hallucination -- and it is the shape of hallucination that would look
        # most convincing in a log.
        pool = candidates(3)
        sheet = build_contact_sheet(pool, policy=ContactSheetPolicy(columns=3))
        request = sheet.manifest.request(purpose="album-hero")
        result = parse_reply(self.reply(request, [pool[0].media_id]), request)
        self.assertIs(result.status, Status.REJECTED)

    def test_media_id_lookup_is_exact_and_not_lenient(self):
        sheet = build_contact_sheet(candidates(2), policy=ContactSheetPolicy(columns=2))
        self.assertEqual(sheet.manifest.media_id_for("A1"), media_id(1))
        for lenient in ("a1", " A1", "A1 ", "A01", "A-1"):
            with self.assertRaises(ContactSheetError):
                sheet.manifest.media_id_for(lenient)

    def test_resolve_refuses_anything_that_is_not_a_parse_result(self):
        sheet = build_contact_sheet(candidates(2), policy=ContactSheetPolicy(columns=2))
        # A dict has an `.items` attribute -- the bound method -- which is
        # exactly the object a duck-typed check would wave through.
        for wrong in ({}, {"items": [{"id": "A1"}]}, None, "A1"):
            with self.assertRaises(ContactSheetError):
                sheet.manifest.resolve(wrong)

    def test_request_rejects_a_purpose_structured_would_reject(self):
        # Delegated, not re-implemented: structured.Request owns the rule.
        sheet = build_contact_sheet(candidates(2), policy=ContactSheetPolicy(columns=2))
        with self.assertRaises(ValueError):
            sheet.manifest.request(purpose="Album Hero")

    def test_request_carries_caller_bounds_through(self):
        sheet = build_contact_sheet(candidates(4), policy=ContactSheetPolicy(columns=4))
        request = sheet.manifest.request(purpose="reel-pick", min_items=2, max_items=3)
        self.assertEqual(request.min_items, 2)
        self.assertEqual(request.max_items, 3)


class PngInspectionTest(unittest.TestCase):
    def test_chunk_walker_reads_the_chunk_table(self):
        sheet = build_contact_sheet(candidates(2))
        types = png_chunk_types(sheet.image_bytes)
        self.assertEqual(types[0], "IHDR")
        self.assertEqual(types[-1], "IEND")

    def test_a_text_chunk_spliced_into_a_sheet_is_detected(self):
        # Proves the out-the-door check would actually catch a leak, by building
        # the leak by hand. Nothing in the module can produce this today; the
        # check exists for the edit that could.
        sheet = build_contact_sheet(candidates(2))
        data = bytearray(sheet.image_bytes)
        payload = b"Comment\x00/Users/someone/IMG_0042.jpg"
        chunk = (
            len(payload).to_bytes(4, "big") + b"tEXt" + payload + b"\x00\x00\x00\x00"
        )
        spliced = bytes(data[:-12]) + chunk + bytes(data[-12:])
        self.assertIn("tEXt", png_chunk_types(spliced))
        with self.assertRaises(LeakError):
            cs._assert_no_metadata_chunks(spliced)

    def test_non_png_bytes_are_refused(self):
        with self.assertRaises(LeakError):
            png_chunk_types(jpeg_bytes())

    def test_chunk_lengths_that_do_not_add_up_are_refused(self):
        sheet = build_contact_sheet(candidates(1))
        with self.assertRaises(LeakError):
            png_chunk_types(sheet.image_bytes + b"trailing")


class RealisticLibraryTest(unittest.TestCase):
    """A sheet built from proxies shaped like the demo library's output.

    scripts/demo/make_library.py writes JPEGs carrying Make/Model/Software and
    an EXIF sub-IFD with DateTimeOriginal -- the same fields a real camera
    writes. Composing from that shape end to end is the closest thing to an
    integration test this module can have without real photographs.
    """

    def test_end_to_end_sheet_from_camera_shaped_proxies(self):
        pool = [
            SheetCandidate(
                media_id=media_id(index + 1),
                proxy_bytes=jpeg_bytes(
                    512, 384, colour=(30 + index * 7, 90, 140), exif=loaded_exif()
                ),
            )
            for index in range(12)
        ]
        sheet = build_contact_sheet(pool, policy=ContactSheetPolicy(columns=4))

        self.assertEqual(sheet.manifest.rows, 3)
        self.assertEqual(len(sheet.manifest.cells), 12)
        self.assertEqual(set(png_chunk_types(sheet.image_bytes)), {"IHDR", "IDAT", "IEND"})
        for marker in (b"SECRETCAMMAKE", b"2019:08:04", b"\x00\x00GPS"):
            self.assertNotIn(marker, sheet.image_bytes)

        manifest_json = sheet.manifest.to_json()
        # The schema constant is the only string in the manifest allowed to
        # contain a slash; remove it and there must be no path punctuation left.
        without_schema = manifest_json.replace(cs.MANIFEST_SCHEMA, "")
        for forbidden in ("SECRET", "2019", "/", "\\", ".jpg", ".JPG", "IMG_"):
            self.assertNotIn(forbidden, without_schema)

        request = sheet.manifest.request(purpose="film-arc", min_items=1, max_items=5)
        reply = json.dumps(
            {
                "request_id": request.effective_request_id,
                "items": [
                    {"id": "A1", "score": 0.91},
                    {"id": "C4", "score": 0.72},
                ],
            }
        )
        result = parse_reply(reply, request)
        self.assertIs(result.status, Status.OK)
        self.assertEqual(
            sheet.manifest.resolve(result), (pool[0].media_id, pool[11].media_id)
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
