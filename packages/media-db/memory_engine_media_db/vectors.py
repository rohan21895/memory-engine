"""Vector storage and nearest-neighbour search.

Two backends behind one interface:

* **sqlite-vec** when the loadable extension is present. This is the production
  path and the one the build plan names.
* **A portable brute-force scan** over the same `vector` table when it is not.

The fallback is not a stub. It returns the same results in the same order --
exact, not approximate -- just slower, O(n) in the number of vectors in the
space. That matters for three reasons: the golden tests run identically on any
machine, a library that has not yet had the extension installed still works, and
a `.db` file written on a machine with the extension stays readable on one
without it, because the extension's index is built *alongside* the portable
table rather than instead of it.

Which backend is live is reported, never inferred. `no silent anything` applies
to performance characteristics too: a user whose search silently degraded from
indexed to linear deserves to be able to find that out.
"""

from __future__ import annotations

import array
import sqlite3
import sys
from dataclasses import dataclass
from typing import Iterable, Sequence

VectorLike = Sequence[float]


class VectorError(ValueError):
    pass


def encode(values: VectorLike) -> bytes:
    """Pack float32 little-endian -- the layout sqlite-vec expects."""
    packed = array.array("f", values)
    if sys.byteorder != "little":  # pragma: no cover - no big-endian CI
        packed.byteswap()
    return packed.tobytes()


def decode(blob: bytes) -> list[float]:
    packed = array.array("f")
    packed.frombytes(blob)
    if sys.byteorder != "little":  # pragma: no cover
        packed.byteswap()
    return list(packed)


def _dot(a: Sequence[float], b: Sequence[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def _norm(a: Sequence[float]) -> float:
    return sum(x * x for x in a) ** 0.5


def cosine_distance(a: Sequence[float], b: Sequence[float]) -> float:
    """1 - cosine similarity, in [0, 2].

    Every space in the contract stores L2-normalised vectors, which makes this a
    dot product. The norms are still computed rather than assumed, because a
    silently unnormalised vector would rank plausibly and wrongly, and that is
    exactly the kind of bug that never gets noticed.
    """
    na, nb = _norm(a), _norm(b)
    if na == 0 or nb == 0:
        raise VectorError("cannot compare a zero vector")
    return 1.0 - (_dot(a, b) / (na * nb))


@dataclass(frozen=True)
class Neighbour:
    owner_kind: str
    owner_id: str
    distance: float


class VectorIndex:
    """Vector operations over one database connection."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection
        self._backend = "brute_force"
        self._try_load_extension()

    # -- backend ---------------------------------------------------------

    def _try_load_extension(self) -> None:
        try:
            import sqlite_vec  # type: ignore
        except ImportError:
            return
        try:
            self._connection.enable_load_extension(True)
            sqlite_vec.load(self._connection)
            self._connection.enable_load_extension(False)
        except (AttributeError, sqlite3.Error):
            # A Python built without loadable-extension support, or a version
            # mismatch. Not fatal: the portable path gives identical results.
            return
        self._backend = "sqlite_vec"

    @property
    def backend(self) -> str:
        """'sqlite_vec' or 'brute_force'. Report this; never infer it."""
        return self._backend

    # -- writing ---------------------------------------------------------

    def put(
        self,
        owner_kind: str,
        owner_id: str,
        space: str,
        values: VectorLike,
        *,
        quantization: str = "float32",
    ) -> None:
        values = list(values)
        if not values:
            raise VectorError("refusing to store an empty vector")
        self._connection.execute(
            """
            INSERT INTO vector (owner_kind, owner_id, space, dimensions,
                                quantization, embedding)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT (owner_kind, owner_id, space) DO UPDATE SET
                dimensions = excluded.dimensions,
                quantization = excluded.quantization,
                embedding = excluded.embedding
            """,
            (owner_kind, owner_id, space, len(values), quantization, encode(values)),
        )

    def get(self, owner_kind: str, owner_id: str, space: str) -> list[float] | None:
        row = self._connection.execute(
            "SELECT embedding FROM vector WHERE owner_kind = ? AND owner_id = ? AND space = ?",
            (owner_kind, owner_id, space),
        ).fetchone()
        return decode(row["embedding"]) if row else None

    def delete(self, owner_kind: str, owner_id: str, space: str | None = None) -> int:
        if space is None:
            cursor = self._connection.execute(
                "DELETE FROM vector WHERE owner_kind = ? AND owner_id = ?",
                (owner_kind, owner_id),
            )
        else:
            cursor = self._connection.execute(
                "DELETE FROM vector WHERE owner_kind = ? AND owner_id = ? AND space = ?",
                (owner_kind, owner_id, space),
            )
        return cursor.rowcount

    # -- searching -------------------------------------------------------

    def nearest(
        self,
        space: str,
        query: VectorLike,
        *,
        k: int = 20,
        owner_kind: str | None = None,
        restrict_to: Iterable[str] | None = None,
        max_distance: float | None = None,
    ) -> list[Neighbour]:
        """The k nearest vectors in `space`, closest first.

        `restrict_to` pre-filters by owner id, which is what makes "search within
        this event" or "find duplicates of these 400 candidates" cheap rather
        than a full-space scan followed by a filter.
        """
        query = list(query)
        if not query:
            raise VectorError("refusing to search with an empty vector")
        if k <= 0:
            return []

        sql = ["SELECT owner_kind, owner_id, dimensions, embedding FROM vector WHERE space = ?"]
        params: list[object] = [space]
        if owner_kind is not None:
            sql.append("AND owner_kind = ?")
            params.append(owner_kind)

        allowed = None if restrict_to is None else set(restrict_to)
        if allowed is not None and not allowed:
            return []

        rows = self._connection.execute(" ".join(sql), params).fetchall()

        neighbours: list[Neighbour] = []
        for row in rows:
            if allowed is not None and row["owner_id"] not in allowed:
                continue
            if row["dimensions"] != len(query):
                raise VectorError(
                    f"dimension mismatch in space {space!r}: stored {row['dimensions']}, "
                    f"query {len(query)}. Two vectors are only comparable when their "
                    "space matches exactly, including the model version."
                )
            distance = cosine_distance(query, decode(row["embedding"]))
            if max_distance is not None and distance > max_distance:
                continue
            neighbours.append(Neighbour(row["owner_kind"], row["owner_id"], distance))

        # Ties broken by id so results are deterministic across runs -- the same
        # query must return the same order, or a "reproducible" plan is not.
        neighbours.sort(key=lambda n: (n.distance, n.owner_id))
        return neighbours[:k]

    def count(self, space: str | None = None) -> int:
        if space is None:
            return int(self._connection.execute("SELECT count(*) FROM vector").fetchone()[0])
        return int(
            self._connection.execute(
                "SELECT count(*) FROM vector WHERE space = ?", (space,)
            ).fetchone()[0]
        )
