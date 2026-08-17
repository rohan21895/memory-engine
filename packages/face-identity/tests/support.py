"""Shared fixtures for the face-identity tests.

Everything here is deterministic. A test that fails once in fifty runs is worse
than no test: it teaches the reader that red means "run it again".
"""

from __future__ import annotations

import hashlib
import math
import random
import sys
import uuid
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = PACKAGE_ROOT.parent.parent
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

from memory_engine_face.embeddings import FaceEmbedding  # noqa: E402
from memory_engine_face.identity import ConsentRef  # noqa: E402

SPACE = "arcface_buffalo_l_512"
DIMENSIONS = 512
NOW = "2026-08-17T10:00:00+05:30"


def fid(name: object) -> str:
    """A stable 64-hex face id from any label."""
    return hashlib.blake2b(str(name).encode(), digest_size=32).hexdigest()


def pid(name: object) -> str:
    """A stable lowercase UUID from any label."""
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, f"person:{name}"))


def axis_vector(index: int, *, scale: float = 1.0) -> list[float]:
    values = [0.0] * DIMENSIONS
    values[index % DIMENSIONS] = scale
    return values


def blob(
    centre_index: int, offset: int, *, jitter: float = 0.01, seed: int = 0
) -> list[float]:
    """A vector near the given axis, deterministic in (centre, offset, seed)."""
    rng = random.Random((centre_index, offset, seed).__hash__() & 0xFFFFFFFF)
    values = axis_vector(centre_index)
    return [v + rng.gauss(0.0, jitter) for v in values]


def embedding(face_id: str, values: list[float], *, space: str = SPACE) -> FaceEmbedding:
    return FaceEmbedding.from_raw(face_id, space, values)


def person_faces(
    person: int, count: int, *, jitter: float = 0.01, seed: int = 0
) -> list[FaceEmbedding]:
    """`count` embeddings of one synthetic person, all near one axis."""
    return [
        embedding(fid(f"p{person}-f{i}"), blob(person, i, jitter=jitter, seed=seed))
        for i in range(count)
    ]


def unit(values: list[float]) -> list[float]:
    norm = math.sqrt(math.fsum(v * v for v in values))
    return [v / norm for v in values]


def live_consent(scope: str = "minor_face_labeling") -> ConsentRef:
    return ConsentRef(
        ledger_entry_id=pid("consent"),
        scope=scope,
        granted_at="2026-08-01T09:00:00+05:30",
        expires_at=None,
        revoked_at=None,
    )


def revoked_consent() -> ConsentRef:
    return ConsentRef(
        ledger_entry_id=pid("consent-revoked"),
        scope="minor_face_labeling",
        granted_at="2026-08-01T09:00:00+05:30",
        expires_at=None,
        revoked_at="2026-08-10T09:00:00+05:30",
    )


def expired_consent() -> ConsentRef:
    return ConsentRef(
        ledger_entry_id=pid("consent-expired"),
        scope="minor_face_labeling",
        granted_at="2026-08-01T09:00:00+05:30",
        expires_at="2026-08-05T09:00:00+05:30",
        revoked_at=None,
    )
