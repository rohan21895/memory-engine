"""Content-addressed identity.

Every id in the pipeline is a BLAKE3 over canonical bytes, because that is what
makes "have we already done this" a lookup rather than a guess. Two rules are
worth stating because getting either wrong produces a silent failure rather
than an error:

1. CANONICALISATION IS PART OF THE DIGEST. `/Volumes/Archive` and
   `/Volumes/Archive/` must hash identically or a re-scan re-walks a whole
   drive. The canonical form here is byte-for-byte the one the Rust ingest
   worker computes in `workers/ingest/src/job.rs`, and it has to stay that way:
   ingest recomputes the digest from the paths it was handed and REFUSES the
   job on a mismatch. That refusal is the contract working, but only if the two
   implementations agree in the first place.

   The Rust order is: canonicalise each root (resolving symlinks) -> sort the
   canonical paths -> dedupe adjacent -> then, per root, NFC-normalise, strip
   the trailing separator, and hash with a single NUL byte between roots. Note
   that the sort happens BEFORE normalisation, which matters for a path whose
   NFC and NFD forms sort differently. `scripts/demo/run_demo.py` normalises
   first and then sorts; for ASCII paths the two agree, and for a decomposed
   Unicode path they need not. This module follows Rust.

2. JOB IDENTITY INCLUDES THE PARAMS DIGEST AND THE SCOPE. Same work with
   different settings is different work; the same work for two libraries is two
   jobs. Both are in the tuple the JobSpec schema specifies.
"""

from __future__ import annotations

import json
import os
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

import blake3 as _blake3

__all__ = [
    "BBOX_QUANTUM",
    "blake3_hex",
    "canonical_json",
    "canonical_source_roots",
    "digest_of",
    "ecmascript_number",
    "face_identity",
    "job_identity",
    "params_digest",
    "quantise_box_component",
    "source_locator_digest",
]

_UNIT_SEPARATOR = "\x1f"

# Normalised box coordinates are rounded to this many parts of the frame before
# they enter a face_id. 1e-4 of a 6000px frame is 0.6px -- finer than any
# detector's own precision, and coarse enough that the last-bit differences
# between two execution providers (CoreML and CPU do not agree at fp32's last
# digit) cannot turn one face into two. Without the quantum, re-running the
# same detector on the same photo on a different provider silently doubles
# every face in the library.
BBOX_QUANTUM = 10_000


def ecmascript_number(value: Any) -> str:
    """Render a number the way RFC 8785 and JavaScript's `String()` do.

    `repr()` is not this. `repr(1.0)` is `'1.0'` where `String(1)` is `'1'`, and
    a digest built on `repr` therefore disagrees with a JavaScript writer over
    the same value -- which is exactly how every model once reported a config
    digest mismatch. An exponent form is REFUSED rather than written: it is the
    one range where Python's shortest-round-trip formatting and ECMAScript's
    differ in padding, and no RationalTime component needs it.
    """
    if isinstance(value, bool):
        raise TypeError("a boolean is not a number here")
    if isinstance(value, int):
        rendered = str(value)
    else:
        number = float(value)
        if number != number or number in (float("inf"), float("-inf")):
            raise ValueError(f"{value!r} is not finite and cannot enter an id")
        rendered = str(int(number)) if number.is_integer() else repr(number)
    if "e" in rendered or "E" in rendered:
        raise ValueError(
            f"{value!r} needs exponent notation, where Python and JavaScript "
            "formatting stop agreeing; refused rather than written"
        )
    return rendered


def quantise_box_component(value: Any) -> int:
    """`value * BBOX_QUANTUM`, rounded HALF AWAY FROM ZERO.

    Deliberately not `round()`, which rounds half to even. See face_identity.
    """
    number = float(value)
    if number != number or number in (float("inf"), float("-inf")):
        raise ValueError(f"{value!r} is not finite and cannot enter an id")
    if number < 0:
        raise ValueError(f"{value!r} is negative; a normalised box component is not")
    scaled = number * BBOX_QUANTUM
    floor = int(scaled // 1)
    return floor + 1 if scaled - floor >= 0.5 else floor


def canonical_json(value: Any) -> bytes:
    """Sorted-key, separator-tight, UTF-8 JSON. The only serialisation hashed."""
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


def blake3_hex(data: bytes) -> str:
    return _blake3.blake3(data).hexdigest()


def digest_of(value: Any) -> str:
    return blake3_hex(canonical_json(value))


def params_digest(params: Mapping[str, Any]) -> str:
    return digest_of(dict(params))


def canonical_source_roots(paths: Iterable[str | os.PathLike[str]]) -> list[str]:
    """Resolved, sorted, deduplicated roots, in the Rust worker's order.

    Resolution is `Path.resolve(strict=True)`: a root that does not exist is an
    error here rather than a digest over a path that was never walked. The Rust
    side uses `fs::canonicalize`, which is also strict, so a missing root fails
    on both sides -- just earlier, and with a message that names the problem.
    """
    resolved: list[str] = []
    for path in paths:
        resolved.append(str(Path(path).expanduser().resolve(strict=True)))
    resolved.sort()
    deduped: list[str] = []
    for item in resolved:
        if not deduped or deduped[-1] != item:
            deduped.append(item)
    return deduped


def source_locator_digest(paths: Iterable[str | os.PathLike[str]]) -> str:
    """BLAKE3 over the canonical roots, matching the Rust ingest worker."""
    hasher = _blake3.blake3()
    for index, root in enumerate(canonical_source_roots(paths)):
        if index:
            hasher.update(b"\x00")
        normalised = unicodedata.normalize("NFC", root)
        # `str.rstrip(sep)` would strip a run of separators; Rust's
        # `trim_end_matches` does too, so they agree. The root "/" survives as
        # "" on both sides, which is consistent rather than correct -- nobody
        # scans the filesystem root, and if they ever do, both sides are wrong
        # in the same way and the mismatch check still holds.
        hasher.update(normalised.rstrip(os.sep).encode("utf-8"))
    return hasher.hexdigest()


def face_identity(
    *,
    media_id: str,
    bbox: Sequence[float],
    detector_model_id: str,
    detector_version: str,
    frame_time: Mapping[str, Any] | None = None,
) -> str:
    """The face_id the FaceRecord contract specifies, computed.

    face-record.schema.json: "BLAKE3 over (media_id, frame_time, quantised
    bbox, detector model_id+version). Content-addressed, so re-running the same
    detector on the same frame produces the same id and re-detection is
    idempotent. Changing detector version deliberately produces new ids rather
    than silently mutating old ones."

    Every element of that tuple is load-bearing:

      * media_id -- two photos may contain the same face in the same place.
      * frame_time -- one video contains the same person at 1s and at 4s, in
        very nearly the same box. Serialised as value/rate rather than as
        seconds because RationalTime exists precisely so 30000/1001 is not
        rounded, and rounding it here would collide two adjacent frames.
      * quantised bbox -- see BBOX_QUANTUM.
      * detector id AND version -- a detector upgrade moves boxes by a pixel or
        two. Keeping the old ids would attach v2's box to v1's embedding, which
        is arithmetic on two different faces with nothing to reveal it.

    Two things this function got wrong before the encoding was frozen in the
    schema (issue #34), both silent, both found by computing the fixtures:

      * `round()` is BANKER'S rounding. It sends 3002.5 to 3002 where
        JavaScript's Math.round and Rust's f64::round send it to 3003, and 8855
        of the 10000 half-quantum positions in [0,1] are exactly representable
        as doubles -- so this was not theoretical. Half away from zero now,
        spelled out rather than delegated to a builtin whose tie-breaking
        differs by language.
      * `!r` on the RationalTime components is Python's repr, so a frame time
        that parsed as 1001.0 hashed differently from the same time parsed as
        1001 -- two ids for one frame, from one language, depending only on how
        the JSON had been typed. Numbers are rendered in ECMAScript
        Number::toString form now, which is what edl_id already uses.
    """
    if len(bbox) != 4:
        raise ValueError(f"bbox must be (x, y, w, h), got {list(bbox)!r}")
    quantised = ",".join(str(quantise_box_component(value)) for value in bbox)
    if frame_time is None:
        time_part = ""
    else:
        time_part = (
            f"{ecmascript_number(frame_time['value'])}"
            f"/{ecmascript_number(frame_time['rate'])}"
        )
    for name, field in (
        ("model_id", detector_model_id),
        ("version", detector_version),
    ):
        if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in field):
            raise ValueError(
                f"detector {name} {field!r} contains a control character; the "
                "face_id encoding joins fields with U+001F and a field carrying "
                "it would let two different detectors share one id"
            )
    joined = _UNIT_SEPARATOR.join(
        [
            "face:v1",
            media_id,
            time_part,
            quantised,
            detector_model_id,
            detector_version,
        ]
    )
    return blake3_hex(joined.encode("utf-8"))


def job_identity(
    *,
    job_type: str,
    input_ids: Sequence[str] = (),
    locator_digest: str | None = None,
    params_digest_hex: str,
    scope: str | None = None,
) -> str:
    """The JobSpec identity tuple, hashed.

    BLAKE3 over (job_type, sorted input ids, locator digest or "", params
    digest, scope), joined with a unit separator. Matches the encoding
    `scripts/demo/run_demo.py` already uses, so the demo's scan job and this
    runner's scan job over the same folder are the same job -- which is the
    point of content addressing and would be quietly untrue if the encodings
    differed by a separator.
    """
    joined = _UNIT_SEPARATOR.join(
        [
            job_type,
            ",".join(sorted(input_ids)),
            locator_digest or "",
            params_digest_hex,
            scope or "",
        ]
    )
    return blake3_hex(joined.encode("utf-8"))
