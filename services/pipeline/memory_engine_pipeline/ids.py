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
    "blake3_hex",
    "canonical_json",
    "canonical_source_roots",
    "digest_of",
    "job_identity",
    "params_digest",
    "source_locator_digest",
]

_UNIT_SEPARATOR = "\x1f"


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
