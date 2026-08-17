"""Compose one contact sheet from deliberately dirty proxies, for the egress test.

Run by tests/egress/contact-sheet.test.mjs, which then inspects the emitted
bytes ITSELF. That split is the point: this script is not allowed to grade its
own homework, so it only produces the artefact and declares what it planted in
the input. Every assertion about what came out is made in the Node test, with
its own PNG chunk walker, so a bug in the module's own leak check cannot make
the egress test pass.

Exit codes: 0 wrote the artefacts, 2 could not run (missing dependency). Never
0 for "skipped" -- a skipped egress check must not read as a passing one.

    python3 contact_sheet_probe.py <output-dir>
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "packages" / "prompt-engine"))

# Strings planted in the proxies. The Node test searches the emitted sheet for
# every one of them, so they are declared here rather than duplicated there.
MARKERS = {
    "exif_make": "EGRESSPROBECAMERA",
    "exif_software": "EGRESSPROBESOFTWARE",
    "exif_datetime": "2019:08:04 17:22:31",
    "icc_profile": "EGRESSPROBEICCPROFILE",
    "png_text_value": "/Users/egressprobe/Pictures/IMG_0042.jpg",
    "png_text_key": "EGRESSPROBECOMMENT",
}


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: contact_sheet_probe.py <output-dir>", file=sys.stderr)
        return 2
    out = Path(sys.argv[1])

    try:
        from PIL import Image
        from PIL.PngImagePlugin import PngInfo
    except ImportError as error:
        print(f"egress probe cannot run: Pillow is required ({error})", file=sys.stderr)
        return 2
    try:
        import blake3  # noqa: F401
    except ImportError as error:
        print(f"egress probe cannot run: blake3 is required ({error})", file=sys.stderr)
        return 2

    from memory_engine_prompt.contact_sheet import (
        ContactSheetPolicy,
        SheetCandidate,
        build_contact_sheet,
    )

    exif = Image.Exif()
    exif[0x010F] = MARKERS["exif_make"]
    exif[0x0131] = MARKERS["exif_software"]
    exif[0x0112] = 1
    sub = exif.get_ifd(0x8769)
    sub[0x9003] = MARKERS["exif_datetime"]
    gps = exif.get_ifd(0x8825)
    gps[1] = "N"
    gps[2] = (15.0, 29.0, 54.32)
    gps[3] = "E"
    gps[4] = (73.0, 49.0, 12.34)
    exif_bytes = exif.tobytes()

    jpeg_buffer = io.BytesIO()
    Image.new("RGB", (512, 384), (40, 110, 170)).save(
        jpeg_buffer,
        "JPEG",
        quality=85,
        exif=exif_bytes,
        icc_profile=MARKERS["icc_profile"].encode("ascii") * 8,
    )
    dirty_jpeg = jpeg_buffer.getvalue()

    text = PngInfo()
    text.add_text(MARKERS["png_text_key"], MARKERS["png_text_value"])
    png_buffer = io.BytesIO()
    Image.new("RGB", (400, 400), (170, 60, 90)).save(png_buffer, "PNG", pnginfo=text)
    dirty_png = png_buffer.getvalue()

    # If the planted markers are not actually in the inputs, the whole check is
    # vacuous. Fail here rather than emit an artefact nothing could ever fail on.
    missing = [
        name
        for name, value in MARKERS.items()
        if value.encode("utf-8") not in dirty_jpeg + dirty_png
    ]
    if missing:
        print(f"egress probe is vacuous: markers absent from the inputs: {missing}",
              file=sys.stderr)
        return 2

    sheet = build_contact_sheet(
        [
            SheetCandidate(media_id=f"{index:064x}", proxy_bytes=proxy)
            for index, proxy in enumerate([dirty_jpeg, dirty_png, dirty_jpeg[:], dirty_png[:]], 1)
        ],
        policy=ContactSheetPolicy(columns=2),
    )

    out.mkdir(parents=True, exist_ok=True)
    (out / "sheet.png").write_bytes(sheet.image_bytes)
    (out / "manifest.json").write_text(sheet.manifest.to_json())
    (out / "markers.json").write_text(json.dumps(MARKERS, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
