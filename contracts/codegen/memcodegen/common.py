"""Helpers shared by the three emitters."""

from __future__ import annotations

import re
import textwrap

BANNER = (
    "GENERATED FILE -- DO NOT EDIT.\n"
    "\n"
    "Produced by contracts/codegen/generate.py from contracts/schemas/*.schema.json.\n"
    "Edit the schemas and re-run `npm run codegen`. CI fails if these files drift\n"
    "from the schemas (see scripts/ci/check-codegen-freshness.mjs).\n"
)


def screaming_snake(text: str) -> str:
    """Enum member name from an enum value: 'pdf_x_1a' -> 'PDF_X_1A'."""
    cleaned = re.sub(r"[^0-9a-zA-Z]+", "_", text).strip("_").upper()
    if not cleaned:
        return "EMPTY"
    if cleaned[0].isdigit():
        cleaned = f"V_{cleaned}"
    return cleaned


def pascal(text: str) -> str:
    parts = re.split(r"[^0-9a-zA-Z]+", text)
    out = "".join(part[:1].upper() + part[1:] for part in parts if part)
    if out and out[0].isdigit():
        out = f"V{out}"
    return out or "Value"


def camel(text: str) -> str:
    out = pascal(text)
    return out[:1].lower() + out[1:] if out else out


def wrap_doc(text: str, width: int = 88, indent: str = "") -> list[str]:
    """Wrap a schema description into comment lines, preserving paragraphs."""
    if not text:
        return []
    lines: list[str] = []
    for index, paragraph in enumerate(text.split("\n\n")):
        collapsed = " ".join(paragraph.split())
        if not collapsed:
            continue
        if index:
            lines.append("")
        lines.extend(
            textwrap.wrap(collapsed, width=max(width - len(indent), 20)) or [""]
        )
    return lines
