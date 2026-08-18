"""The one serialisation a clearance manifest is hashed under.

WHY THIS FILE EXISTS AT ALL

`SafetyClearance.manifest_id` binds a publication to its clearance. The Python
planner computes it; the TypeScript print worker recomputes it and refuses the
export if the two disagree. So the two implementations have to produce
byte-identical pre-images, and there is exactly one interesting way for them not
to: numbers.

    Python  json.dumps(1.0)   -> "1.0"
    JS      JSON.stringify(1) -> "1"

Both are the same value. Neither is wrong. And a manifest that verifies in the
pipeline and fails in the renderer is a gate that blocks correct output, which
is precisely how gates get switched off. This project has already had that
failure once, against `params_digest`.

So the rule is RFC 8785's, the same one `edl_id`, `face_id` and `params_digest`
already use in this repository: keys sorted by code unit, `,` and `:`
separators, no whitespace, UTF-8, and every number rendered the way ECMAScript's
`Number::toString` renders it -- with exponent notation REFUSED rather than
written, because that is the one range where Python's shortest-round-trip
formatting and ECMAScript's disagree about padding.

WHY IT IS NOT IMPORTED FROM services/pipeline/memory_engine_pipeline/ids.py

That module has the same rule and is the right long-term home, but it lives in
the pipeline service, which is Codex's territory and which this package must not
depend on -- a safety verifier that only works when a service happens to be
importable is a verifier that fails open in every other host. The two are tested
against the SAME vectors (`contracts/vectors/safety-clearance-manifest-id.json`)
rather than against each other, so a divergence fails a test instead of a print
run.

WHAT `manifest_body` REMOVES, AND WHY THE CONTRACT'S ORIGINAL WORDING COULD NOT
BE IMPLEMENTED

The schema used to say the digest was over "the SERIALISED BYTES of this
document with `manifest_id` and `decision` removed. Bytes rather than a
re-serialisation." There are no such bytes: you cannot delete two keys from a
byte string without re-serialising what is left, so every implementation would
have invented its own canonical form -- the exact divergence the sentence was
trying to prevent. `models/policy/digest.py` can hash bytes because a config
file IS the artifact on disk; a manifest body is a projection of one, so it has
to be constructed. The wording is fixed in this branch and the construction is
here.

`decision` is excluded because it is derived: every verifier recomputes it from
`items` rather than trusting it, so hashing it would bind the manifest to a
value nobody is allowed to believe.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

__all__ = [
    "MANIFEST_BODY_EXCLUDED_KEYS",
    "blake3_hex",
    "canonical_bytes",
    "ecmascript_number",
    "manifest_body",
    "manifest_id",
]

#: Removed before hashing. `manifest_id` because a digest cannot contain
#: itself; `decision` because it is derived and recomputed rather than trusted.
MANIFEST_BODY_EXCLUDED_KEYS = ("manifest_id", "decision")


def ecmascript_number(value: Any) -> str:
    """Render a number the way RFC 8785 and JavaScript's `String()` do."""
    if isinstance(value, bool):
        raise TypeError("a boolean is not a number here")
    if isinstance(value, int):
        rendered = str(value)
    else:
        number = float(value)
        if number != number or number in (float("inf"), float("-inf")):
            raise ValueError(f"{value!r} is not finite and cannot enter a digest")
        rendered = str(int(number)) if number.is_integer() else repr(number)
    if "e" in rendered or "E" in rendered:
        raise ValueError(
            f"{value!r} needs exponent notation, where Python and JavaScript "
            "formatting stop agreeing; refused rather than written"
        )
    return rendered


def _canonical_numbers(value: Any) -> Any:
    """Rewrite every number in a tree into its RFC 8785 rendering.

    Rendering and reading back is what makes the two languages agree: an
    integral float becomes an int and therefore serialises with no fractional
    part, and a value needing exponent notation raises instead of being written
    in a form the two pad differently.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, float):
        rendered = ecmascript_number(value)
        return int(rendered) if "." not in rendered else value
    if isinstance(value, Mapping):
        return {key: _canonical_numbers(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonical_numbers(item) for item in value]
    return value


def canonical_bytes(value: Any) -> bytes:
    """Sorted-key, separator-tight, UTF-8 JSON. The only bytes ever hashed."""
    return json.dumps(
        _canonical_numbers(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def blake3_hex(data: bytes) -> str:
    """BLAKE3-256 of `data`, lowercase hex.

    Imported here rather than at module import time so that a host without the
    wheel fails when it tries to VERIFY -- which is a refusal, and therefore a
    block -- rather than at import, which a caller could catch and route around
    with a bare `except ImportError: pass`. Absence blocks; it does not degrade.
    """
    import blake3  # noqa: PLC0415 - deliberate, see docstring

    return blake3.blake3(data).hexdigest()


def manifest_body(manifest: Mapping[str, Any]) -> dict[str, Any]:
    """The hashed projection: everything but `manifest_id` and `decision`."""
    return {
        key: value
        for key, value in manifest.items()
        if key not in MANIFEST_BODY_EXCLUDED_KEYS
    }


def manifest_id(manifest: Mapping[str, Any]) -> str:
    """`SafetyClearance.manifest_id` for this document."""
    return blake3_hex(canonical_bytes(manifest_body(manifest)))
