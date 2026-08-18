"""Build the Tier 3 request body from dirty proxies, and try to send it unconsented.

Run by tests/egress/taste-request.test.mjs, which inspects the artefacts ITSELF.
The split is the same one `contact_sheet_probe.py` makes and for the same
reason: this script is not allowed to grade its own homework. It produces the
artefact and declares what it planted; every assertion about what came out is
made in the Node test with its own PNG chunk walker and its own string search.

WHY THIS EXISTS ALONGSIDE contact_sheet_probe.py

That probe inspects the composed SHEET. This one inspects the REQUEST BODY --
the bytes that would actually leave the machine: the base64 image, the task
text, the system prompt and the output schema. Three of those four are text we
author, and the natural way to write the prompt ("choose from these candidates:
<ids>") puts 64-hex content addresses on the wire while every assertion about
the PNG still passes. The sheet being clean is necessary and not sufficient.

It also exercises the refusal, because "no network egress without a
consent-ledger entry" is a claim about a code path and not about a document:
the probe asks the transport to send with no consent, with a counting sender
that would record any call, and reports the block code and the call count.

Exit codes: 0 wrote the artefacts, 2 could not run (missing dependency). Never
0 for "skipped" -- a skipped egress check must not read as a passing one.

    python3 taste_request_probe.py <output-dir>
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "packages" / "prompt-engine"))

# Planted in the proxies. The Node test searches the emitted request body for
# every one of these, so they are declared here rather than duplicated there.
MARKERS = {
    "exif_make": "TASTEEGRESSCAMERA",
    "exif_software": "TASTEEGRESSSOFTWARE",
    "exif_datetime": "2019:03:14 09:11:02",
    "exif_description": "/Users/tasteegress/Pictures/2019-03 Goa/IMG_0042.jpg",
    "icc_profile": "TASTEEGRESSICCPROFILE",
    "png_text_key": "TASTEEGRESSCOMMENT",
    "png_text_value": "/Users/tasteegress/Pictures/IMG_0099.jpg",
}

TILES = 6


class CountingSender:
    """Would record any call. The point is that it never gets one."""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, prepared):  # pragma: no cover - must never run
        self.calls += 1
        raise AssertionError("the transport sent a request with no consent record")


def dirty_jpeg(index: int) -> bytes:
    from PIL import Image, ImageDraw

    image = Image.new("RGB", (480, 360), (20 + index * 23 % 180, 90, 140))
    draw = ImageDraw.Draw(image)
    for x in range(0, 480, 24):
        for y in range(0, 360, 24):
            if (x // 24 + y // 24) % 2:
                draw.rectangle([x, y, x + 23, y + 23], fill=(210, 60, 40 + index * 11 % 180))

    exif = Image.Exif()
    exif[0x010F] = MARKERS["exif_make"]
    exif[0x0131] = MARKERS["exif_software"]
    exif[0x010E] = MARKERS["exif_description"]
    exif[0x0112] = 1  # oriented already, which is what this boundary requires
    sub = exif.get_ifd(0x8769)
    sub[0x9003] = MARKERS["exif_datetime"]
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
    return buffer.getvalue()


def dirty_png(index: int) -> bytes:
    from PIL import Image, ImageDraw
    from PIL.PngImagePlugin import PngInfo

    image = Image.new("RGB", (400, 400), (170, 60, 90))
    draw = ImageDraw.Draw(image)
    for x in range(0, 400, 25):
        for y in range(0, 400, 25):
            if (x // 25 + y // 25) % 2:
                draw.rectangle([x, y, x + 24, y + 24], fill=(60, 190, 120))
    text = PngInfo()
    text.add_text(MARKERS["png_text_key"], MARKERS["png_text_value"])
    buffer = io.BytesIO()
    image.save(buffer, "PNG", pnginfo=text)
    return buffer.getvalue()


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: taste_request_probe.py <output-dir>", file=sys.stderr)
        return 2
    out = Path(sys.argv[1])

    try:
        from PIL import Image  # noqa: F401
    except ImportError as error:
        print(f"taste egress probe cannot run: Pillow is required ({error})", file=sys.stderr)
        return 2
    try:
        import blake3  # noqa: F401
    except ImportError as error:
        print(f"taste egress probe cannot run: blake3 is required ({error})", file=sys.stderr)
        return 2

    from memory_engine_prompt import album_taste, transport

    proxies = [dirty_jpeg(index) if index % 2 == 0 else dirty_png(index)
               for index in range(TILES)]

    # A leak check against an input with no leak in it proves nothing.
    joined = b"".join(proxies)
    missing = [name for name, value in MARKERS.items()
               if value.encode("utf-8") not in joined]
    if missing:
        print(f"taste egress probe is vacuous: markers absent from the inputs: {missing}",
              file=sys.stderr)
        return 2

    media_ids = [f"{index + 1:064x}" for index in range(TILES)]
    candidates = [
        album_taste.TasteCandidate(
            media_id=media_ids[index], proxy_bytes=proxies[index], classical_rank=index
        )
        for index in range(TILES)
    ]

    plan = album_taste.plan_taste(candidates, target=2, request_id="egress-probe-0001")
    prepared = transport.build_request(
        plan.payload,
        plan.request,
        plan.instruction,
        transport.TransportConfig(model=album_taste.MODEL_ID),
    )

    # The refusal, executed rather than described. Two grants that must both
    # be refused, with a sender that would record a call if one were made.
    sender = CountingSender()
    ledger = transport.InMemoryLedger()
    blocks = {}
    for name, grant in (
        ("consent_absent", transport.EgressGrant(
            requires_egress=True,
            consent=None,
            destination=transport.REQUIRED_DESTINATION,
            payload_kind="contact_sheet",
        )),
        ("requires_egress_false", transport.EgressGrant(
            requires_egress=False,
            consent=transport.ConsentRef(
                ledger_entry_id="3f2a1c58-4b7e-4c21-9a6d-0e51b7c9d420",
                scope=transport.REQUIRED_CONSENT_SCOPE,
                granted_at="2026-01-01T00:00:00+00:00",
            ),
            destination=transport.REQUIRED_DESTINATION,
            payload_kind="contact_sheet",
        )),
    ):
        try:
            album_taste.run_album_taste(
                plan, grant=grant, ledger=ledger, sender=sender
            )
        except transport.EgressBlocked as blocked:
            blocks[name] = blocked.code
        else:  # pragma: no cover - a send with no consent is the failure
            blocks[name] = "NOT BLOCKED"

    out.mkdir(parents=True, exist_ok=True)
    (out / "request-body.json").write_bytes(prepared.body_bytes)
    (out / "sheet.png").write_bytes(plan.sheet.image_bytes)
    (out / "markers.json").write_text(json.dumps(MARKERS, sort_keys=True))
    (out / "local.json").write_text(
        json.dumps(
            {
                # Local-only, and written here so the Node test can assert their
                # ABSENCE from the body without inventing them itself.
                "media_ids": media_ids,
                "labels": list(plan.labels),
                "tile_count": TILES,
                "sender_calls": sender.calls,
                "ledger_entries": len(ledger.entries),
                "blocks": blocks,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
