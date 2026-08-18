"""Tests for the Tier 3 album-selection pass -- the seam, and the refusals.

NO LIVE CALL IS MADE ANYWHERE IN THIS FILE, and none is possible: every test
drives `transport.RecordedSender`, which replays recorded reply shapes and never
opens a socket. The one real call this branch makes lives in
`scripts/demo/tier3_taste_smoke.py`, is opt-in, and is not part of any suite.

WHAT THIS FILE IS FOR

`contact_sheet.py`, `transport.py` and `structured.py` each have their own tests
and each passes them alone. What none of them could test is the JOIN -- and the
join is where a privacy guarantee actually lives, because a guard is only worth
what the caller's wiring lets it see. Four claims are made about this path and
each is asserted here against a real composed sheet and a real built request
body rather than against a mock:

  1. nothing above the resolution ceiling is sent, and the ceiling is checked
     BEFORE any pixel is decoded (asserted by instrumenting Pillow's decoder,
     not by reading the source);
  2. no EXIF, GPS, filename or path survives into the sheet, the manifest, or
     the request body -- the body being the one that matters, since it is the
     bytes that would leave;
  3. `requires_egress` true AND a ConsentRef, or no call happens -- proved by
     running with consent absent and asserting the sender was never invoked;
  4. a reply naming an id that was not on the sheet is refused whole, never
     dropped and never corrected to the nearest real id.

Where a test could pass because the thing under test did nothing, it asserts
the positive case too: the leak tests plant markers AND assert the sheet is a
real multi-tile sheet; the refusal tests assert the sender's call count.
"""

from __future__ import annotations

import base64
import io
import json
import sys
import unittest
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PACKAGE_ROOT))

from memory_engine_prompt import album_taste, contact_sheet, structured, transport  # noqa: E402

# Strings planted in the inputs. Every one of them is searched for in the
# emitted sheet, the manifest and the request body. Declared once here so a
# test cannot quietly stop looking for one of them.
MARKERS = {
    "exif_make": "TASTEPROBECAMERA",
    "exif_software": "TASTEPROBESOFTWARE",
    "exif_datetime": "2019:03:14 09:11:02",
    "icc_profile": "TASTEPROBEICCPROFILE",
    "exif_path": "/Users/tasteprobe/Pictures/2019-03 Goa/IMG_0042.jpg",
    "png_text_value": "/Users/tasteprobe/Pictures/IMG_0099.jpg",
    "png_text_key": "TASTEPROBECOMMENT",
}

# A media id shaped like the real thing: 64 hex characters, content-addressed.
def media_id(index: int) -> str:
    return f"{index:064x}"


def _checkerboard(index: int, size: tuple[int, int], step: int, ink):
    """Structure rather than a flat field, so a tile is visibly a picture.

    Drawn with `ImageDraw.rectangle` and not `putpixel`: the per-pixel version
    of this helper made the suite take minutes, which is its own kind of test
    failure -- a suite nobody runs guards nothing.
    """
    from PIL import Image, ImageDraw

    image = Image.new("RGB", size, (30 + index * 17 % 200, 60, 90 + index * 31 % 150))
    draw = ImageDraw.Draw(image)
    for x in range(0, size[0], step):
        for y in range(0, size[1], step):
            if (x // step + y // step) % 2:
                draw.rectangle([x, y, x + step - 1, y + step - 1], fill=ink)
    return image


@lru_cache(maxsize=None)
def clean_proxy(index: int, size: tuple[int, int] = (512, 384)) -> bytes:
    """A plain JPEG with no metadata at all."""
    image = _checkerboard(index, size, 32, (200, 40 + index * 7 % 200, 90))
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=85)
    return buffer.getvalue()


# Which container carries which marker. TWO containers, deliberately: a PNG
# stores its ICC profile zlib-COMPRESSED in an iCCP chunk, so the profile's
# text is not in the file at all and a leak check against a PNG-only input
# would pass against a module that strips nothing. That was found by the
# vacuity assertion below, not by reading the PNG spec.
_JPEG_MARKERS = ("exif_make", "exif_software", "exif_datetime", "icc_profile", "exif_path")
_PNG_MARKERS = ("png_text_key", "png_text_value")


@lru_cache(maxsize=None)
def _dirty_jpeg(index: int) -> bytes:
    """A JPEG with EXIF (including a path), binary GPS rationals and an ICC profile."""
    from PIL import Image

    image = _checkerboard(index, (480, 360), 24, (210, 60, 40 + index * 11 % 180))

    exif = Image.Exif()
    exif[0x010F] = MARKERS["exif_make"]  # Make
    exif[0x0131] = MARKERS["exif_software"]  # Software
    exif[0x010E] = MARKERS["exif_path"]  # ImageDescription, holding a path
    exif[0x0112] = 1  # Orientation: proxies reach this boundary already oriented
    sub = exif.get_ifd(0x8769)
    sub[0x9003] = MARKERS["exif_datetime"]  # DateTimeOriginal
    # GPS as real binary rationals rather than text. A string search for "15"
    # would not find a packed RATIONAL, which is exactly how a coordinate
    # survives a leak check that only greps for words.
    gps = exif.get_ifd(0x8825)
    gps[1] = "N"
    gps[2] = (15.0, 29.0, 54.32)
    gps[3] = "E"
    gps[4] = (73.0, 49.0, 12.34)

    buffer = io.BytesIO()
    image.save(
        buffer,
        "JPEG",
        quality=85,
        exif=exif.tobytes(),
        icc_profile=MARKERS["icc_profile"].encode("ascii") * 8,
    )
    return _assert_carries(buffer.getvalue(), _JPEG_MARKERS, "dirty jpeg")


@lru_cache(maxsize=None)
def _dirty_png(index: int) -> bytes:
    """A PNG carrying a tEXt chunk whose value is a filesystem path."""
    from PIL.PngImagePlugin import PngInfo

    image = _checkerboard(index, (400, 400), 25, (60, 190, 120))
    text = PngInfo()
    text.add_text(MARKERS["png_text_key"], MARKERS["png_text_value"])
    buffer = io.BytesIO()
    image.save(buffer, "PNG", pnginfo=text)
    return _assert_carries(buffer.getvalue(), _PNG_MARKERS, "dirty png")


def _assert_carries(payload: bytes, names, what: str) -> bytes:
    """A leak test against an input with no leak in it proves nothing."""
    missing = [name for name in names if MARKERS[name].encode("utf-8") not in payload]
    if missing:
        raise AssertionError(f"{what} is vacuous; markers absent from the input: {missing}")
    return payload


def dirty_proxy(index: int) -> bytes:
    return _dirty_jpeg(index) if index % 2 == 0 else _dirty_png(index)


def candidates(count: int, *, dirty: bool = False) -> list[album_taste.TasteCandidate]:
    maker = dirty_proxy if dirty else clean_proxy
    return [
        album_taste.TasteCandidate(
            media_id=media_id(index), proxy_bytes=maker(index), classical_rank=index
        )
        for index in range(count)
    ]


def fresh_consent(**overrides) -> transport.ConsentRef:
    granted = (transport.utc_now() - timedelta(days=1)).isoformat()
    fields = {
        "ledger_entry_id": "led-taste-0001",
        "scope": transport.REQUIRED_CONSENT_SCOPE,
        "granted_at": granted,
        "expires_at": (transport.utc_now() + timedelta(days=30)).isoformat(),
    }
    fields.update(overrides)
    return transport.ConsentRef(**fields)


def grant(consent: transport.ConsentRef | None, *, requires: bool = True) -> transport.EgressGrant:
    return transport.EgressGrant(
        requires_egress=requires,
        consent=consent,
        destination=transport.REQUIRED_DESTINATION,
        payload_kind="contact_sheet",
        estimated_bytes=1,
    )


class CountingSender:
    """A sender that records every call and replays a script.

    `transport.RecordedSender` already does this, but this one is used where the
    assertion is `calls == 0`: a test that proved nothing was sent by checking a
    ledger would still pass if the ledger were the thing that was broken.
    """

    def __init__(self, script=()) -> None:
        self.script = list(script)
        self.calls: list[transport.PreparedRequest] = []

    def __call__(self, prepared):
        self.calls.append(prepared)
        if not self.script:
            raise AssertionError("sender called with an empty script")
        return self.script.pop(0)


def reply_message(request_id: str, labels, *, notes: str = "", extra=None) -> dict:
    items = [
        {"id": label, "score": round(0.9 - 0.05 * index, 3), "note": f"note {index}"}
        for index, label in enumerate(labels)
    ]
    payload = {"request_id": request_id, "items": items}
    if notes:
        payload["notes"] = notes
    if extra:
        payload.update(extra)
    return {
        "id": "msg_taste_0001",
        "model": album_taste.MODEL_ID,
        "stop_reason": "end_turn",
        "content": [{"type": "text", "text": json.dumps(payload)}],
        "usage": {"input_tokens": 1234, "output_tokens": 210},
    }


# ---------------------------------------------------------------- ceiling --


class ResolutionCeiling(unittest.TestCase):
    def test_over_ceiling_is_refused_and_never_decoded(self):
        """The ceiling is a refusal, and it fires before a pixel is decoded.

        Instrumenting `Image.Image.load` is the only way to assert the ORDER
        rather than the outcome. A clamp would have produced a sheet that looks
        identical, and a check placed after `load()` would refuse the same file
        while having already read, decoded and held a full-resolution original
        in memory -- which is the thing the ceiling exists to prevent.
        """
        from PIL import Image

        loads: list[tuple[int, int]] = []
        original = Image.Image.load

        def counting_load(self, *args, **kwargs):
            loads.append(self.size)
            return original(self, *args, **kwargs)

        oversize = clean_proxy(1, size=(contact_sheet.MAX_SOURCE_EDGE_PX + 1, 400))
        items = [
            album_taste.TasteCandidate(
                media_id=media_id(0), proxy_bytes=clean_proxy(0), classical_rank=0
            ),
            album_taste.TasteCandidate(
                media_id=media_id(1), proxy_bytes=oversize, classical_rank=1
            ),
            album_taste.TasteCandidate(
                media_id=media_id(2), proxy_bytes=clean_proxy(2), classical_rank=2
            ),
        ]

        Image.Image.load = counting_load
        try:
            with self.assertRaises(contact_sheet.ResolutionCeilingError) as caught:
                album_taste.plan_taste(items, target=1)
        finally:
            Image.Image.load = original

        self.assertIn(str(contact_sheet.MAX_SOURCE_EDGE_PX), str(caught.exception))
        self.assertNotIn(
            (contact_sheet.MAX_SOURCE_EDGE_PX + 1, 400),
            loads,
            "the oversize proxy was decoded before the ceiling refused it",
        )

    def test_ceiling_is_not_raisable_by_rebinding_the_public_constant(self):
        """A ceiling a caller can raise by assignment is not a ceiling."""
        original = contact_sheet.MAX_SOURCE_EDGE_PX
        contact_sheet.MAX_SOURCE_EDGE_PX = 8192
        try:
            oversize = clean_proxy(1, size=(original + 64, 400))
            items = [
                album_taste.TasteCandidate(
                    media_id=media_id(0), proxy_bytes=clean_proxy(0), classical_rank=0
                ),
                album_taste.TasteCandidate(
                    media_id=media_id(1), proxy_bytes=oversize, classical_rank=1
                ),
            ]
            with self.assertRaises(contact_sheet.ResolutionCeilingError):
                album_taste.plan_taste(items, target=1)
        finally:
            contact_sheet.MAX_SOURCE_EDGE_PX = original


# ------------------------------------------------------------------- leaks --


class WhatActuallyLeaves(unittest.TestCase):
    """The body is the artefact under test, not the sheet.

    `tests/egress/contact-sheet.test.mjs` already inspects the composed PNG with
    its own chunk walker. What it could not see before this seam existed is the
    REQUEST BODY -- the base64 image plus the prompt text, the system prompt and
    the output schema. Those three carry text we author, and the natural way to
    write the prompt (`"here are the candidates: <media ids>"`) would put 64-hex
    content addresses on the wire while every check on the PNG still passed.
    """

    @classmethod
    def setUpClass(cls):
        cls.items = candidates(6, dirty=True)
        cls.plan = album_taste.plan_taste(cls.items, target=2, request_id="probe-0001")
        cls.prepared = transport.build_request(
            cls.plan.payload,
            cls.plan.request,
            cls.plan.instruction,
            transport.TransportConfig(model=album_taste.MODEL_ID),
        )
        cls.body_text = cls.prepared.body_bytes.decode("utf-8")

    def test_the_sheet_is_a_real_sheet(self):
        """Guard against every leak test passing because nothing was drawn."""
        self.assertEqual(len(self.plan.labels), 6)
        self.assertEqual(self.plan.manifest.rows * self.plan.manifest.columns >= 6, True)
        self.assertGreater(len(self.plan.sheet.image_bytes), 2000)
        # Set, not sequence: a sheet large enough to be a real sheet is split
        # across several IDAT chunks, which is normal and not a leak.
        chunks = contact_sheet.png_chunk_types(self.plan.sheet.image_bytes)
        self.assertEqual(set(chunks), {"IHDR", "IDAT", "IEND"})
        self.assertEqual((chunks[0], chunks[-1]), ("IHDR", "IEND"))

    def test_no_marker_survives_into_the_request_body(self):
        for name, value in MARKERS.items():
            self.assertNotIn(value, self.body_text, f"request body leaks {name}")

    def test_no_marker_survives_into_the_decoded_image_or_manifest(self):
        image = self._embedded_image()
        manifest = self.plan.manifest.to_json()
        for name, value in MARKERS.items():
            self.assertNotIn(value.encode("utf-8"), image, f"embedded image leaks {name}")
            self.assertNotIn(value, manifest, f"manifest leaks {name}")
        # The GPS coordinates went in as binary rationals, so the string search
        # above would not have found them.
        self.assertNotIn(bytes([0, 0, 0, 15, 0, 0, 0, 1]), image)

    def test_media_ids_never_reach_the_wire(self):
        """The label indirection, asserted rather than described.

        A media id in the body would be an index into the user's library sitting
        next to a picture of them, and it would look completely normal in review.
        """
        for candidate in self.items:
            self.assertNotIn(candidate.media_id, self.body_text)
        # And the labels DO reach it, so this is not passing on an empty body.
        for label in self.plan.labels:
            self.assertIn(label, self.body_text)

    def test_the_body_carries_no_path_like_text(self):
        # The base64 blob is excluded: it is opaque and any substring can occur
        # in it by chance. Everything else in the body is text we authored.
        body = json.loads(self.prepared.body_bytes)
        blob = body["messages"][0]["content"][0]["source"]["data"]
        authored = self.prepared.body_bytes.decode("utf-8").replace(blob, "")
        for suffix in (".jpg", ".JPG", ".png", ".heic", ".mp4", "IMG_", "DCIM"):
            self.assertNotIn(suffix, authored, f"authored body text holds {suffix}")

    def test_context_is_empty_and_the_prompt_says_nothing_about_the_library(self):
        self.assertEqual(self.plan.payload.context, "")
        self.assertNotIn("captured", self.body_text.lower())
        self.assertNotIn("latitude", self.body_text.lower())

    def test_embedded_image_is_byte_identical_to_the_composed_sheet(self):
        self.assertEqual(self._embedded_image(), self.plan.sheet.image_bytes)

    def _embedded_image(self) -> bytes:
        body = json.loads(self.prepared.body_bytes)
        source = body["messages"][0]["content"][0]["source"]
        return base64.standard_b64decode(source["data"])


# ---------------------------------------------------------------- consent --


class ConsentGatesTheCall(unittest.TestCase):
    """Absence blocks. Proved by counting calls, not by reading a ledger."""

    def setUp(self):
        self.plan = album_taste.plan_taste(candidates(6), target=2, request_id="gate-0001")
        self.ledger = transport.InMemoryLedger()

    def _run(self, egress_grant):
        sender = CountingSender([reply_message("gate-0001", self.plan.labels[:2])])
        try:
            decision = album_taste.run_album_taste(
                self.plan, grant=egress_grant, ledger=self.ledger, sender=sender
            )
        except transport.EgressBlocked as blocked:
            return blocked, sender
        return decision, sender

    def test_consent_absent_blocks_and_nothing_is_sent(self):
        blocked, sender = self._run(grant(None))
        self.assertIsInstance(blocked, transport.EgressBlocked)
        self.assertEqual(blocked.code, transport.BLOCK_CONSENT_MISSING)
        self.assertEqual(sender.calls, [], "a request was sent with no consent record")
        self.assertEqual(self.ledger.entries, [], "a send was journaled that never happened")

    def test_requires_egress_false_blocks_even_with_valid_consent(self):
        blocked, sender = self._run(grant(fresh_consent(), requires=False))
        self.assertEqual(blocked.code, transport.BLOCK_NOT_DECLARED)
        self.assertEqual(sender.calls, [])

    def test_revoked_consent_blocks_and_says_revoked_not_expired(self):
        revoked = fresh_consent(revoked_at=transport.utc_now().isoformat())
        blocked, sender = self._run(grant(revoked))
        self.assertEqual(blocked.code, transport.BLOCK_CONSENT_REVOKED)
        self.assertEqual(sender.calls, [])

    def test_expired_consent_blocks(self):
        expired = fresh_consent(
            expires_at=(transport.utc_now() - timedelta(minutes=1)).isoformat()
        )
        blocked, sender = self._run(grant(expired))
        self.assertEqual(blocked.code, transport.BLOCK_CONSENT_EXPIRED)
        self.assertEqual(sender.calls, [])

    def test_wrong_scope_blocks(self):
        wrong = fresh_consent(scope="cloud_render")
        blocked, sender = self._run(grant(wrong))
        self.assertEqual(blocked.code, transport.BLOCK_CONSENT_SCOPE)
        self.assertEqual(sender.calls, [])

    def test_valid_consent_lets_exactly_one_request_through_and_journals_it(self):
        decision, sender = self._run(grant(fresh_consent()))
        self.assertTrue(decision.ok, decision.detail)
        self.assertEqual(len(sender.calls), 1)
        actions = [entry.action for entry in self.ledger.entries]
        self.assertEqual(actions, ["network_send", "network_receive"])
        # The journal line is written BEFORE the send, which is the only
        # ordering that survives a crash mid-flight.
        self.assertEqual(self.ledger.entries[0].reversible, False)
        self.assertEqual(self.ledger.entries[0].target_id, decision.sheet_digest)

    def test_building_the_real_sender_requires_neither_the_sdk_nor_a_key(self):
        """A blocked send must never be reported as a missing dependency.

        Found by running the pipeline with a REVOKED consent record on a
        machine without the `anthropic` package: the stage failed with "the
        anthropic SDK is not installed" and never mentioned that the user had
        withdrawn permission. Nothing was sent either way, so the ledger looked
        fine -- the damage was entirely in what the operator was told, which is
        the surface CLAUDE.md rule 7 is about.

        The cause was evaluation order: the sender is an ARGUMENT to the call
        that checks consent, so Python built the client first. Construction is
        now inert, which also makes the module's own sentence true -- no key is
        required to build a request, only to send one.
        """
        import builtins

        real_import = builtins.__import__

        def refuse_anthropic(name, *args, **kwargs):
            if name == "anthropic" or name.startswith("anthropic."):
                raise ImportError("no anthropic SDK in this environment")
            return real_import(name, *args, **kwargs)

        builtins.__import__ = refuse_anthropic
        try:
            sender = transport.AnthropicSender()  # must not raise
            with self.assertRaises(transport.EgressBlocked) as caught:
                album_taste.run_album_taste(
                    self.plan,
                    grant=grant(fresh_consent(revoked_at=transport.utc_now().isoformat())),
                    ledger=self.ledger,
                    sender=sender,
                )
            self.assertEqual(caught.exception.code, transport.BLOCK_CONSENT_REVOKED)
            # And the SDK is still required to actually send.
            with self.assertRaises(transport.TransportError):
                sender(
                    transport.build_request(
                        self.plan.payload, self.plan.request, self.plan.instruction,
                        transport.TransportConfig(model=album_taste.MODEL_ID),
                    )
                )
        finally:
            builtins.__import__ = real_import

    def test_an_injected_client_is_used_verbatim(self):
        """The lazy resolve must not discard a client a caller handed over."""

        class Recorder:
            def __init__(self):
                self.messages = self
                self.bodies = []

            def create(self, **body):
                self.bodies.append(body)
                return reply_message("gate-0001", self.plan_labels[:2])

        recorder = Recorder()
        recorder.plan_labels = self.plan.labels
        sender = transport.AnthropicSender(client=recorder)
        decision = album_taste.run_album_taste(
            self.plan, grant=grant(fresh_consent()), ledger=self.ledger, sender=sender
        )
        self.assertTrue(decision.ok, decision.detail)
        self.assertEqual(len(recorder.bodies), 1)
        self.assertEqual(recorder.bodies[0]["model"], album_taste.MODEL_ID)

    def test_a_ledger_that_cannot_record_blocks_the_send(self):
        class BrokenLedger(transport.EgressLedger):
            def record(self, entry):
                raise OSError("disk full")

        sender = CountingSender([reply_message("gate-0001", self.plan.labels[:2])])
        with self.assertRaises(transport.EgressBlocked) as caught:
            album_taste.run_album_taste(
                self.plan, grant=grant(fresh_consent()), ledger=BrokenLedger(), sender=sender
            )
        self.assertEqual(caught.exception.code, transport.BLOCK_LEDGER_REFUSED)
        self.assertEqual(sender.calls, [], "sent despite an unwritable journal")


# ------------------------------------------------------------------ replies --


class RepliesAreValidatedAgainstTheSheet(unittest.TestCase):
    def setUp(self):
        self.plan = album_taste.plan_taste(candidates(6), target=2, request_id="reply-0001")
        self.ledger = transport.InMemoryLedger()

    def _decide(self, message):
        return album_taste.run_album_taste(
            self.plan,
            grant=grant(fresh_consent()),
            ledger=self.ledger,
            sender=CountingSender([message]),
        )

    def test_an_id_that_was_not_on_the_sheet_is_refused_whole(self):
        """Not dropped, not corrected, not partially applied.

        `H8` is a syntactically valid label that this six-tile sheet does not
        carry. The tempting handlings are both wrong: dropping it returns one
        picture where two were asked for and reads as success, and repairing it
        to the nearest real label is how a photograph nobody chose enters a
        printed book.
        """
        good = self.plan.labels[0]
        decision = self._decide(reply_message("reply-0001", [good, "H8"]))
        self.assertFalse(decision.ok)
        self.assertEqual(decision.selected, (), "a hallucinated id was silently dropped")
        self.assertEqual(decision.parse_status, structured.Status.REJECTED.value)
        self.assertTrue(any("unknown_id" in line for line in decision.rejections))
        # The good label is not smuggled through under another field either.
        self.assertNotIn(good, decision.selected)
        # And the retry hint never quotes what came back.
        self.assertNotIn("H8", decision.retry_hint)

    def test_a_near_miss_is_diagnosed_and_still_refused(self):
        label = self.plan.labels[0]
        decision = self._decide(reply_message("reply-0001", [label, label.lower()]))
        self.assertFalse(decision.ok)
        self.assertEqual(decision.selected, ())
        self.assertTrue(any("near miss" in line for line in decision.rejections))

    def test_a_reply_answering_a_different_request_is_refused(self):
        message = reply_message("some-other-job", self.plan.labels[:2])
        decision = self._decide(message)
        self.assertFalse(decision.ok)
        self.assertEqual(decision.selected, ())
        self.assertTrue(any("request_mismatch" in line for line in decision.rejections))

    def test_the_wrong_number_of_items_is_refused_rather_than_truncated(self):
        decision = self._decide(reply_message("reply-0001", self.plan.labels[:4]))
        self.assertFalse(decision.ok)
        self.assertEqual(decision.selected, ())
        self.assertTrue(any("count" in line for line in decision.rejections))

    def test_a_repeated_id_is_refused(self):
        label = self.plan.labels[0]
        decision = self._decide(reply_message("reply-0001", [label, label]))
        self.assertFalse(decision.ok)
        self.assertEqual(decision.selected, ())

    def test_a_truncated_reply_is_not_reported_as_malformed_json(self):
        message = reply_message("reply-0001", self.plan.labels[:2])
        message["stop_reason"] = "max_tokens"
        decision = self._decide(message)
        self.assertFalse(decision.ok)
        self.assertEqual(decision.outcome, transport.Outcome.TRUNCATED.value)
        self.assertEqual(decision.selected, ())
        self.assertIsNone(decision.parse_status)

    def test_a_refusal_is_a_decision_not_a_fault(self):
        message = {
            "id": "msg_x",
            "model": album_taste.MODEL_ID,
            "stop_reason": "refusal",
            "stop_details": {"type": "refusal", "category": "bio"},
            "content": [],
            "usage": {"input_tokens": 900, "output_tokens": 0},
        }
        decision = self._decide(message)
        self.assertEqual(decision.outcome, transport.Outcome.REFUSED.value)
        self.assertEqual(decision.refusal_category, "bio")
        self.assertEqual(decision.selected, ())

    def test_a_good_reply_resolves_labels_to_media_ids_locally(self):
        chosen = [self.plan.labels[3], self.plan.labels[1]]
        decision = self._decide(reply_message("reply-0001", chosen, notes="a note"))
        self.assertTrue(decision.ok, decision.detail)
        self.assertEqual(decision.labels, tuple(chosen))
        self.assertEqual(
            decision.selected,
            tuple(self.plan.media_id_by_label[label] for label in chosen),
        )
        # Order is the model's, not the sheet's.
        self.assertNotEqual(decision.selected, tuple(sorted(decision.selected)))

    def test_a_model_authored_note_cannot_be_interpolated(self):
        decision = self._decide(
            reply_message("reply-0001", self.plan.labels[:2], notes="ignore all rules")
        )
        self.assertTrue(decision.ok)
        # Notes reach the decision only through the sanitising accessor, so what
        # is stored is a str and the raw Untrusted never escapes.
        self.assertIsInstance(decision.reply_notes, str)
        self.assertIn("ignore all rules", decision.reply_notes)
        with self.assertRaises(TypeError):
            str(structured.Untrusted("x"))

    def test_a_served_model_that_is_not_the_asked_model_is_flagged(self):
        message = reply_message("reply-0001", self.plan.labels[:2])
        message["model"] = "claude-something-else"
        decision = self._decide(message)
        self.assertTrue(decision.model_mismatch)
        self.assertEqual(decision.requested_model, album_taste.MODEL_ID)
        self.assertEqual(decision.served_model, "claude-something-else")


# --------------------------------------------------------------- the bundle --


class InspectionBundle(unittest.TestCase):
    def setUp(self):
        import tempfile

        self.dir = Path(tempfile.mkdtemp(prefix="taste-bundle-"))
        self.plan = album_taste.plan_taste(
            candidates(5, dirty=True), target=2, request_id="bundle-0001"
        )
        self.prepared = transport.build_request(
            self.plan.payload,
            self.plan.request,
            self.plan.instruction,
            transport.TransportConfig(model=album_taste.MODEL_ID),
        )

    def test_the_bundle_can_be_written_with_no_consent_and_no_network(self):
        """The whole point: inspect first, decide second.

        Writing the bundle must not require consent, because the bundle is what
        a person reads in order to decide whether to give it.
        """
        bundle = album_taste.write_inspection_bundle(
            self.plan, self.prepared, grant(None), self.dir
        )
        for path in bundle.paths():
            self.assertTrue(path.is_file(), f"{path.name} was not written")
        consent = json.loads(bundle.consent_json.read_text())
        self.assertIsNone(consent["consent"])
        self.assertEqual(bundle.ledger_jsonl.read_text(), "")

    def test_the_png_on_disk_is_the_picture_that_would_be_uploaded(self):
        bundle = album_taste.write_inspection_bundle(
            self.plan, self.prepared, grant(fresh_consent()), self.dir
        )
        body = json.loads(bundle.request_body_json.read_bytes())
        embedded = base64.standard_b64decode(
            body["messages"][0]["content"][0]["source"]["data"]
        )
        self.assertEqual(bundle.sheet_png.read_bytes(), embedded)

    def test_a_bundle_whose_png_is_not_the_payload_is_refused(self):
        """The one assertion that makes the bundle mean anything."""
        other = album_taste.plan_taste(candidates(4), target=1, request_id="other-0001")
        with self.assertRaises(album_taste.TasteError):
            album_taste.write_inspection_bundle(other, self.prepared, grant(None), self.dir)

    def test_the_summary_marks_local_only_fields_as_local_only(self):
        bundle = album_taste.write_inspection_bundle(
            self.plan, self.prepared, grant(None), self.dir
        )
        summary = json.loads(bundle.request_summary_json.read_text())
        self.assertIn("label_to_media_id_LOCAL_ONLY", summary)
        body_text = bundle.request_body_json.read_text()
        for value in summary["label_to_media_id_LOCAL_ONLY"].values():
            self.assertNotIn(value, body_text)


# -------------------------------------------------------------- determinism --


class Determinism(unittest.TestCase):
    def test_the_same_shortlist_produces_the_same_request(self):
        first = album_taste.plan_taste(candidates(6), target=2, request_id="det-0001")
        second = album_taste.plan_taste(candidates(6), target=2, request_id="det-0001")
        config = transport.TransportConfig(model=album_taste.MODEL_ID)
        left = transport.build_request(first.payload, first.request, first.instruction, config)
        right = transport.build_request(second.payload, second.request, second.instruction, config)
        self.assertEqual(left.cache_key, right.cache_key)
        self.assertEqual(first.manifest.image_digest, second.manifest.image_digest)

    def test_reordering_the_sheet_is_a_different_question(self):
        items = candidates(6)
        config = transport.TransportConfig(model=album_taste.MODEL_ID)
        forward = album_taste.plan_taste(items, target=2, request_id="det-0002")
        backward = album_taste.plan_taste(
            list(reversed(items)), target=2, request_id="det-0002"
        )
        left = transport.build_request(
            forward.payload, forward.request, forward.instruction, config
        )
        right = transport.build_request(
            backward.payload, backward.request, backward.instruction, config
        )
        self.assertNotEqual(left.cache_key, right.cache_key)

    def test_classical_selection_follows_rank_not_sheet_order(self):
        items = [
            album_taste.TasteCandidate(
                media_id=media_id(index), proxy_bytes=clean_proxy(index),
                classical_rank=(5 - index),
            )
            for index in range(6)
        ]
        plan = album_taste.plan_taste(items, target=2, request_id="det-0003")
        self.assertEqual(plan.classical_selection, (media_id(5), media_id(4)))

    def test_two_candidates_sharing_a_rank_are_refused(self):
        items = [
            album_taste.TasteCandidate(
                media_id=media_id(index), proxy_bytes=clean_proxy(index), classical_rank=0
            )
            for index in range(3)
        ]
        with self.assertRaises(album_taste.TasteError):
            album_taste.plan_taste(items, target=1)


# --------------------------------------------------------------------- cost --


class Cost(unittest.TestCase):
    def test_an_unpriced_model_reports_none_rather_than_zero(self):
        self.assertIsNone(
            album_taste.estimate_cost_usd(
                "claude-not-a-model", {"input_tokens": 10, "output_tokens": 10}
            )
        )

    def test_absent_counters_report_none_rather_than_zero(self):
        self.assertIsNone(album_taste.estimate_cost_usd(album_taste.MODEL_ID, {}))

    def test_the_introductory_rate_applies_before_its_end_and_not_after(self):
        usage = {"input_tokens": 1_000_000, "output_tokens": 1_000_000}
        during = album_taste.estimate_cost_usd(
            album_taste.MODEL_ID, usage, on=datetime(2026, 8, 18, tzinfo=timezone.utc)
        )
        after = album_taste.estimate_cost_usd(
            album_taste.MODEL_ID, usage, on=datetime(2026, 9, 1, tzinfo=timezone.utc)
        )
        self.assertAlmostEqual(during, 12.0)
        self.assertAlmostEqual(after, 18.0)

    def test_agreement_is_none_when_there_is_no_selection(self):
        decision = album_taste.TasteDecision(outcome="refused", ok=False)
        self.assertIsNone(decision.agreement)

    def test_agreement_counts_overlap(self):
        decision = album_taste.TasteDecision(
            outcome="completed",
            ok=True,
            selected=("a", "b", "c", "d"),
            classical_selection=("a", "b", "z", "y"),
        )
        self.assertAlmostEqual(decision.agreement, 0.5)


# ------------------------------------------------------------------- shapes --


class PlanRefusals(unittest.TestCase):
    def test_asking_for_everything_on_the_sheet_is_refused(self):
        with self.assertRaises(album_taste.TasteError):
            album_taste.plan_taste(candidates(4), target=4)

    def test_a_filename_shaped_media_id_is_refused(self):
        with self.assertRaises(contact_sheet.ContactSheetError):
            album_taste.plan_taste(
                [
                    album_taste.TasteCandidate(
                        media_id="IMG_0042.jpg", proxy_bytes=clean_proxy(0), classical_rank=0
                    ),
                    album_taste.TasteCandidate(
                        media_id=media_id(1), proxy_bytes=clean_proxy(1), classical_rank=1
                    ),
                ],
                target=1,
            )

    def test_the_request_allow_list_is_exactly_the_sheets_labels(self):
        plan = album_taste.plan_taste(candidates(7), target=3, request_id="shape-0001")
        self.assertEqual(tuple(plan.request.allowed_ids), plan.labels)
        self.assertEqual(set(plan.payload.tile_ids), set(plan.request.allowed_ids))

    def test_a_sheet_and_request_that_disagree_never_reach_the_wire(self):
        plan = album_taste.plan_taste(candidates(6), target=2, request_id="shape-0002")
        mismatched = structured.Request(
            purpose=album_taste.ALBUM_PURPOSE,
            allowed_ids=("A1", "A2", "A3", "A4", "A5", "H8"),
            min_items=2,
            max_items=2,
            request_id="shape-0002",
        )
        with self.assertRaises(ValueError):
            transport.build_request(
                plan.payload, mismatched, plan.instruction,
                transport.TransportConfig(model=album_taste.MODEL_ID),
            )


if __name__ == "__main__":
    unittest.main()
