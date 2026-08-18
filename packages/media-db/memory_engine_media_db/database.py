"""The media-db query API.

This is the surface both agents call, so it is deliberately small, explicit and
hard to misuse. Three rules shape it:

1. **Writes take contract records, not loose fields.** `put_media` accepts a
   MediaRecord-shaped mapping and derives every indexed column from it in one
   place. There is no way to write a row whose columns disagree with its JSON.

2. **The precision-first face gate is the default, not an option.** Person-scoped
   queries return only faces eligible for automated output unless a caller opts
   out explicitly with `include_uncertain=True`. A wrong person in a family album
   is a catastrophic failure, so the safe behaviour is the one you get by
   forgetting to think about it.

3. **Undated media is excluded from chronological queries.** A file whose capture
   time has `precision: unknown` has no place on a timeline, and sorting it to
   the epoch would put it at the front of every album.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from . import db as _db
from .vectors import Neighbour, VectorIndex

Record = Mapping[str, Any]

# Capture precisions good enough to place an item on a timeline. Anything
# coarser is real information, but not chronological information.
CHRONOLOGICAL_PRECISIONS = ("second", "minute", "hour", "day")

# Assignments that may be treated as a known person without a human in the loop.
ELIGIBLE_ASSIGNMENTS = ("user_confirmed", "auto_high_confidence")


class QueryError(ValueError):
    pass


@dataclass(frozen=True)
class MediaSummary:
    """The row the library grid renders. Deliberately not the whole record --
    100k of these must fit in a virtualised list without parsing 100k JSON blobs."""

    media_id: str
    kind: str
    asset_kind: str
    captured_utc: str | None
    capture_precision: str
    width: int | None
    height: int | None
    aesthetic: float | None
    face_count: int
    excluded: bool
    processing_state: str


def _rational(node: Any) -> tuple[float | None, float | None]:
    if not isinstance(node, Mapping):
        return None, None
    return node.get("value"), node.get("rate")


def _search_body(record: Record) -> str:
    """Everything a person might reasonably type to find this item.

    Filenames are included because people search for `IMG_4821` far more often
    than product designers expect, and transcripts because "the bit where she
    says the ocean" is a real query.
    """
    parts: list[str] = []
    content = record.get("content") or {}
    for tag in content.get("tags", []) or []:
        parts.append(str(tag.get("label", "")))
    if content.get("scene_type"):
        parts.append(str(content["scene_type"]))

    user = record.get("user") or {}
    if user.get("caption"):
        parts.append(str(user["caption"]))
    parts.extend(str(t) for t in user.get("tags", []) or [])

    for source in record.get("sources", []) or []:
        if source.get("original_filename"):
            parts.append(str(source["original_filename"]))

    capture = (record.get("capture") or {}).get("device") or {}
    for key in ("make", "model"):
        if capture.get(key):
            parts.append(str(capture[key]))

    return " ".join(p for p in parts if p)


class Database:
    """A migrated media-db, with its vector index."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection
        self.vectors = VectorIndex(connection)

    # -- lifecycle -------------------------------------------------------

    @classmethod
    def open(cls, path: str | Path, *, migrate: bool = True) -> "Database":
        connection = _db.connect(path)
        if migrate:
            _db.migrate(connection)
        return cls(connection)

    @property
    def connection(self) -> sqlite3.Connection:
        return self._connection

    @property
    def schema_version(self) -> int:
        return _db.current_version(self._connection)

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- media -----------------------------------------------------------

    def put_media(self, record: Record) -> str:
        """Insert or update a MediaRecord. Idempotent on media_id."""
        media_id = record["media_id"]
        capture = record.get("capture") or {}
        captured = capture.get("captured_at") or {}
        gps = capture.get("gps") or {}
        image = record.get("image") or {}
        video = record.get("video") or {}
        quality = record.get("quality") or {}
        content = record.get("content") or {}
        dedupe = record.get("dedupe") or {}
        exclusion = record.get("exclusion") or {}
        user = record.get("user") or {}
        faces = record.get("faces") or {}
        perceptual = record.get("perceptual") or {}
        phash = perceptual.get("image_hash") or {}

        size = image.get("oriented_size") or video.get("oriented_size") or {}
        duration_value, duration_rate = _rational(video.get("duration"))

        def score(node: Any) -> float | None:
            return node.get("value") if isinstance(node, Mapping) else None

        with self._connection:
            self._connection.execute(
                """
                INSERT INTO media (
                    media_id, asset_kind, kind, byte_size, mime_type, file_format,
                    captured_utc, captured_local, capture_precision, capture_confidence,
                    capture_source, latitude, longitude, width, height,
                    duration_value, duration_rate,
                    phash_hex, phash_bits, phash_algorithm,
                    dedupe_group_id, is_dedupe_primary,
                    quality_sharpness, quality_exposure, quality_aesthetic, face_count,
                    scene_type, nsfw_score, excluded, exclusion_reasons,
                    favorite, hidden, rating, processing_state,
                    first_seen_at, updated_at, record_json
                ) VALUES (
                    ?, ?, ?, ?, ?, ?,  ?, ?, ?, ?,  ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?,  ?, ?,  ?, ?, ?, ?,  ?, ?, ?, ?,
                    ?, ?, ?, ?,  ?, ?, ?
                )
                ON CONFLICT (media_id) DO UPDATE SET
                    asset_kind = excluded.asset_kind,
                    kind = excluded.kind,
                    byte_size = excluded.byte_size,
                    mime_type = excluded.mime_type,
                    file_format = excluded.file_format,
                    captured_utc = excluded.captured_utc,
                    captured_local = excluded.captured_local,
                    capture_precision = excluded.capture_precision,
                    capture_confidence = excluded.capture_confidence,
                    capture_source = excluded.capture_source,
                    latitude = excluded.latitude,
                    longitude = excluded.longitude,
                    width = excluded.width,
                    height = excluded.height,
                    duration_value = excluded.duration_value,
                    duration_rate = excluded.duration_rate,
                    phash_hex = excluded.phash_hex,
                    phash_bits = excluded.phash_bits,
                    phash_algorithm = excluded.phash_algorithm,
                    dedupe_group_id = excluded.dedupe_group_id,
                    is_dedupe_primary = excluded.is_dedupe_primary,
                    quality_sharpness = excluded.quality_sharpness,
                    quality_exposure = excluded.quality_exposure,
                    quality_aesthetic = excluded.quality_aesthetic,
                    face_count = excluded.face_count,
                    scene_type = excluded.scene_type,
                    nsfw_score = excluded.nsfw_score,
                    excluded = excluded.excluded,
                    exclusion_reasons = excluded.exclusion_reasons,
                    favorite = excluded.favorite,
                    hidden = excluded.hidden,
                    rating = excluded.rating,
                    processing_state = excluded.processing_state,
                    first_seen_at = excluded.first_seen_at,
                    updated_at = excluded.updated_at,
                    record_json = excluded.record_json
                """,
                (
                    media_id,
                    record["asset_kind"],
                    record["kind"],
                    record["byte_size"],
                    record.get("mime_type"),
                    record.get("file_format"),
                    captured.get("utc"),
                    captured.get("local"),
                    captured.get("precision", "unknown"),
                    captured.get("confidence", 0.0),
                    captured.get("source", "unknown"),
                    gps.get("latitude"),
                    gps.get("longitude"),
                    size.get("width"),
                    size.get("height"),
                    duration_value,
                    duration_rate,
                    phash.get("hex"),
                    phash.get("bits"),
                    # Never defaulted. A digest whose algorithm is unknown
                    # must not be compared against anything (issue #14),
                    # and guessing a name here is exactly how the digest
                    # and its meaning came apart in the first place.
                    phash.get("algorithm"),
                    dedupe.get("group_id"),
                    1 if dedupe.get("is_primary") else 0,
                    score(quality.get("sharpness")),
                    score(quality.get("exposure")),
                    score(quality.get("aesthetic")),
                    faces.get("face_count", 0),
                    content.get("scene_type"),
                    (content.get("safety") or {}).get("nsfw_score"),
                    1 if exclusion.get("excluded_from_automation") else 0,
                    json.dumps(exclusion.get("reasons", [])),
                    1 if user.get("favorite") else 0,
                    1 if user.get("hidden") else 0,
                    user.get("rating"),
                    record["processing"]["state"],
                    record.get("first_seen_at"),
                    record.get("updated_at"),
                    json.dumps(record, sort_keys=True, separators=(",", ":")),
                ),
            )

            # Child rows are replaced wholesale rather than diffed: a MediaRecord
            # is the complete truth about a file, so anything absent from it has
            # genuinely gone away.
            self._connection.execute("DELETE FROM media_source WHERE media_id = ?", (media_id,))
            self._connection.executemany(
                """INSERT INTO media_source (media_id, path, adapter, volume_id,
                                             present, first_seen_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                [
                    (
                        media_id,
                        s["path"],
                        s["adapter"],
                        s.get("volume_id"),
                        1 if s.get("present", True) else 0,
                        s.get("first_seen_at"),
                    )
                    for s in record.get("sources", []) or []
                ],
            )

            self._connection.execute("DELETE FROM media_span WHERE media_id = ?", (media_id,))
            span = record.get("span")
            if span:
                offset_value, offset_rate = _rational(span.get("offset_in_span"))
                self._connection.execute(
                    """INSERT INTO media_span (span_id, media_id, role, member_index,
                                               offset_value, offset_rate, continuity)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        span["span_id"],
                        media_id,
                        span["role"],
                        span.get("index"),
                        offset_value,
                        offset_rate,
                        span.get("continuity", "unverified"),
                    ),
                )

            self._connection.execute("DELETE FROM media_tag WHERE media_id = ?", (media_id,))
            seen: set[tuple[str, str]] = set()
            rows = []
            for tag in content.get("tags", []) or []:
                key = (tag["label"], tag["source"])
                if key in seen:
                    continue
                seen.add(key)
                rows.append((media_id, tag["label"], tag["score"], tag["source"]))
            self._connection.executemany(
                "INSERT INTO media_tag (media_id, label, score, source) VALUES (?, ?, ?, ?)",
                rows,
            )

            # Proxies are indexed in their own table because `resolve_proxy` is
            # on the hot path of every inference and had no index to use --
            # issue #32. Replaced wholesale, like every other child table: a
            # MediaRecord is the complete truth about a file, so a proxy absent
            # from it has genuinely been deleted or regenerated under a new
            # content hash.
            #
            # Scoped to this media_id in both directions. A content-addressed
            # proxy can be shared with another record, and deleting by proxy_id
            # would silently unindex that record's copy.
            self._connection.execute(
                "DELETE FROM media_proxy WHERE media_id = ?", (media_id,)
            )
            self._connection.executemany(
                """INSERT OR REPLACE INTO media_proxy
                       (proxy_id, media_id, kind, path, byte_size, proxy_json)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                [
                    (
                        proxy["proxy_id"],
                        media_id,
                        proxy["kind"],
                        proxy["path"],
                        proxy.get("byte_size"),
                        json.dumps(proxy, sort_keys=True, separators=(",", ":")),
                    )
                    for proxy in record.get("proxies", []) or []
                ],
            )

            self._connection.execute("DELETE FROM media_fts WHERE media_id = ?", (media_id,))
            body = _search_body(record)
            if body:
                self._connection.execute(
                    "INSERT INTO media_fts (media_id, body) VALUES (?, ?)", (media_id, body)
                )

        return media_id

    def get_media(self, media_id: str) -> dict[str, Any] | None:
        row = self._connection.execute(
            "SELECT record_json FROM media WHERE media_id = ?", (media_id,)
        ).fetchone()
        return json.loads(row["record_json"]) if row else None

    def resolve_path(self, media_id: str) -> str | None:
        """The best on-disk path for a media id, or None if nothing is reachable.

        Prefers a source currently present, and prefers an internal volume over a
        removable one, so an unplugged archive drive does not shadow a local copy.
        The OTIO exporter needs exactly this to turn a content hash into a
        `file://` URL at export time -- see docs/otio-mapping.md.

        Returns None for a virtual assembly: it has no path of its own. Callers
        wanting to render one must expand it via `span_members`.
        """
        row = self._connection.execute(
            """
            SELECT path FROM media_source
            WHERE media_id = ?
            ORDER BY present DESC, (volume_id IS NULL) DESC, path
            LIMIT 1
            """,
            (media_id,),
        ).fetchone()
        return row["path"] if row else None

    def resolve_proxy(self, proxy_id: str) -> dict | None:
        """A proxy id to the proxy the ml-runtime should open, or None.

        THE ONLY LOOKUP THE INFERENCE PATH IS ALLOWED TO USE. Codex flagged that
        `ml_runtime.proto` promises the host resolves `proxy_id` through
        media-db, while media-db only had `resolve_path`, which returns an
        ORIGINAL. Handing the inference path a resolver that can return an
        original defeats the structural guarantee that analysis never touches
        source files -- so this searches the proxy list only, and a media_id
        passed here resolves to nothing.

        Returns the ProxyRef as stored, so the caller gets kind, size and the
        frame-index sidecar without a second query.

        INDEXED SINCE MIGRATION 0002 (issue #32). This used to be
        `record_json LIKE '%' || ? || '%' LIMIT 50`, which was a full scan of the
        media table on every inference AND was incorrect above 50 incidental
        matches -- an id mentioned in fifty other records' JSON pushed the
        owning record out of the candidate set, and the resolver returned None.
        A miss here is reported downstream as PROXY_NOT_FOUND, which reads as
        "no such proxy" rather than "the query gave up", so the failure grew
        with library size and would never have raised.

        The signature is unchanged on purpose: ml-runtime deliberately calls
        only this and never queries tables directly, because `resolve_path` can
        return an ORIGINAL.

        ORDER BY media_id resolves the one genuine ambiguity. Proxy ids are
        BLAKE3 of the proxy bytes, so two records sharing an id have byte-
        identical proxies; the ProxyRefs differ at most in which record listed
        them. Returning the lowest media_id's copy is arbitrary among equals but
        it is the SAME arbitrary choice on every host and every run, which is
        what determinism requires. Callers needing to know whose it is ask
        `media_id_for_proxy`.
        """
        row = self._connection.execute(
            """SELECT proxy_json FROM media_proxy
               WHERE proxy_id = ? ORDER BY media_id LIMIT 1""",
            (proxy_id,),
        ).fetchone()
        return json.loads(row["proxy_json"]) if row else None

    def media_id_for_proxy(self, proxy_id: str) -> str | None:
        """Which record a proxy belongs to, or None.

        Separate from `resolve_proxy` because the inference path must not have
        it: handing back a media_id is one `resolve_path` call away from an
        original, and the point of the proxy resolver is that no such step
        exists on that path. This is for the review and diagnostics surfaces,
        which legitimately need to say "this thumbnail came from that photo".
        """
        row = self._connection.execute(
            """SELECT media_id FROM media_proxy
               WHERE proxy_id = ? ORDER BY media_id LIMIT 1""",
            (proxy_id,),
        ).fetchone()
        return row["media_id"] if row else None

    def proxies_for_media(self, media_id: str, kind: str | None = None) -> list[dict]:
        """Every proxy belonging to one record, optionally filtered by kind.

        Reads the RECORD, not `media_proxy`, and both are one indexed lookup so
        this is not about speed. The record's array is ORDERED and the table's
        rows are not: ingest writes thumbnail before preview before video proxy,
        and a caller taking `proxies_for_media(id)[0]` would get a different
        rendition depending on how SQLite happened to return the rows. The
        record is the source of truth; the table is an index for the one query
        that could not use it.
        """
        record = self.get_media(media_id)
        if record is None:
            return []
        proxies = record.get("proxies") or []
        return [p for p in proxies if kind is None or p.get("kind") == kind]

    def span_members(self, span_id: str) -> list[str]:
        """Member media ids of a span, in playback order."""
        return [
            row["media_id"]
            for row in self._connection.execute(
                """SELECT media_id FROM media_span
                   WHERE span_id = ? AND role = 'member'
                   ORDER BY member_index""",
                (span_id,),
            )
        ]

    def count_media(self) -> int:
        return int(self._connection.execute("SELECT count(*) FROM media").fetchone()[0])

    def search(
        self,
        text: str,
        *,
        limit: int = 50,
        include_excluded: bool = False,
    ) -> list[MediaSummary]:
        """Full-text search over tags, captions, filenames and device names."""
        if not text.strip():
            return []
        sql = """
            SELECT m.* FROM media_fts f
            JOIN media m ON m.media_id = f.media_id
            WHERE media_fts MATCH ?
        """
        params: list[object] = [text]
        if not include_excluded:
            sql += " AND m.excluded = 0 AND m.hidden = 0"
        sql += " ORDER BY rank LIMIT ?"
        params.append(limit)
        return [self._summary(row) for row in self._connection.execute(sql, params)]

    def list_media(
        self,
        *,
        kind: str | None = None,
        person_id: str | None = None,
        include_uncertain: bool = False,
        chronological: bool = False,
        primaries_only: bool = False,
        include_excluded: bool = False,
        min_aesthetic: float | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> list[MediaSummary]:
        """The library grid's query.

        `chronological=True` both orders by capture time AND drops items whose
        capture precision is unknown -- an undated file has no position on a
        timeline, and putting it at the epoch is worse than omitting it.
        """
        sql = ["SELECT DISTINCT m.* FROM media m"]
        params: list[object] = []
        where: list[str] = []

        if person_id is not None:
            sql.append("JOIN face fa ON fa.media_id = m.media_id")
            if include_uncertain:
                # Review tooling asks "which photos MIGHT be this person", so an
                # unassigned face whose runner-up candidate is this person counts.
                # Nothing here is eligible for automated output -- that is the
                # entire reason these matches exist as candidates and not as
                # assignments.
                sql.append("LEFT JOIN face_candidate fc ON fc.face_id = fa.face_id")
                where.append("(fa.person_id = ? OR fc.person_id = ?)")
                params.extend([person_id, person_id])
            else:
                where.append("fa.person_id = ?")
                params.append(person_id)
                where.append("fa.eligible = 1")

        if kind is not None:
            where.append("m.kind = ?")
            params.append(kind)
        if not include_excluded:
            where.append("m.excluded = 0")
            where.append("m.hidden = 0")
        if primaries_only:
            where.append("(m.dedupe_group_id IS NULL OR m.is_dedupe_primary = 1)")
        if min_aesthetic is not None:
            where.append("m.quality_aesthetic >= ?")
            params.append(min_aesthetic)
        if chronological:
            placeholders = ", ".join("?" for _ in CHRONOLOGICAL_PRECISIONS)
            where.append(f"m.capture_precision IN ({placeholders})")
            params.extend(CHRONOLOGICAL_PRECISIONS)
            where.append("m.captured_utc IS NOT NULL")

        if where:
            sql.append("WHERE " + " AND ".join(where))
        sql.append(
            "ORDER BY m.captured_utc, m.media_id" if chronological else "ORDER BY m.media_id"
        )
        sql.append("LIMIT ? OFFSET ?")
        params.extend([limit, offset])

        return [self._summary(row) for row in self._connection.execute(" ".join(sql), params)]

    @staticmethod
    def _summary(row: sqlite3.Row) -> MediaSummary:
        return MediaSummary(
            media_id=row["media_id"],
            kind=row["kind"],
            asset_kind=row["asset_kind"],
            captured_utc=row["captured_utc"],
            capture_precision=row["capture_precision"],
            width=row["width"],
            height=row["height"],
            aesthetic=row["quality_aesthetic"],
            face_count=row["face_count"],
            excluded=bool(row["excluded"]),
            processing_state=row["processing_state"],
        )

    # -- people ----------------------------------------------------------

    def put_person(
        self,
        person_id: str,
        *,
        display_name: str | None = None,
        is_priority: bool = False,
        minor_status: str = "unknown",
        created_at: str | None = None,
    ) -> str:
        with self._connection:
            self._connection.execute(
                """INSERT INTO person (person_id, display_name, is_priority,
                                       minor_status, created_at)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT (person_id) DO UPDATE SET
                       display_name = excluded.display_name,
                       is_priority = excluded.is_priority,
                       minor_status = excluded.minor_status""",
                (person_id, display_name, 1 if is_priority else 0, minor_status, created_at),
            )
        return person_id

    def put_face(self, record: Record) -> str:
        """Insert or update a FaceRecord.

        The eligibility gate is copied from the record rather than recomputed,
        because the contract already decided it and two implementations of one
        rule is how they drift. The table's CHECK constraints then refuse a row
        that contradicts it.
        """
        identity = record["identity"]
        sensitive = record.get("sensitive") or {}
        detection = record["detection"]
        attributes = record.get("attributes") or {}
        track = record.get("track") or {}
        cluster = record.get("cluster") or {}
        frame_value, frame_rate = _rational(record.get("frame_time"))
        quality = attributes.get("quality")

        person_id = identity.get("person_id")
        if person_id is not None:
            exists = self._connection.execute(
                "SELECT 1 FROM person WHERE person_id = ?", (person_id,)
            ).fetchone()
            if not exists:
                self.put_person(person_id, minor_status=sensitive.get("minor_status", "unknown"))

        with self._connection:
            self._connection.execute(
                """
                INSERT INTO face (
                    face_id, media_id, person_id, cluster_id, assignment, confidence,
                    threshold_used, eligible, minor_status, has_labeling_consent,
                    frame_value, frame_rate, track_id, detection_score,
                    face_area_ratio, quality, record_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (face_id) DO UPDATE SET
                    person_id = excluded.person_id,
                    cluster_id = excluded.cluster_id,
                    assignment = excluded.assignment,
                    confidence = excluded.confidence,
                    threshold_used = excluded.threshold_used,
                    eligible = excluded.eligible,
                    minor_status = excluded.minor_status,
                    has_labeling_consent = excluded.has_labeling_consent,
                    quality = excluded.quality,
                    record_json = excluded.record_json
                """,
                (
                    record["face_id"],
                    record["media_id"],
                    person_id,
                    cluster.get("cluster_id"),
                    identity["assignment"],
                    identity.get("confidence"),
                    identity.get("threshold_used"),
                    1 if identity["eligible_for_automated_output"] else 0,
                    sensitive.get("minor_status", "unknown"),
                    1 if sensitive.get("labeling_consent") else 0,
                    frame_value,
                    frame_rate,
                    track.get("track_id"),
                    detection["detection_score"],
                    detection["face_area_ratio"],
                    quality.get("value") if isinstance(quality, Mapping) else None,
                    json.dumps(record, sort_keys=True, separators=(",", ":")),
                ),
            )

            self._connection.execute(
                "DELETE FROM face_candidate WHERE face_id = ?", (record["face_id"],)
            )
            for candidate in identity.get("candidates", []) or []:
                self.put_person(candidate["person_id"])
                self._connection.execute(
                    """INSERT OR REPLACE INTO face_candidate (face_id, person_id, confidence)
                       VALUES (?, ?, ?)""",
                    (record["face_id"], candidate["person_id"], candidate["confidence"]),
                )
        return record["face_id"]

    def faces_for_media(self, media_id: str, *, eligible_only: bool = False) -> list[dict]:
        sql = "SELECT record_json FROM face WHERE media_id = ?"
        if eligible_only:
            sql += " AND eligible = 1"
        return [
            json.loads(row["record_json"])
            for row in self._connection.execute(sql + " ORDER BY face_id", (media_id,))
        ]

    def delete_face(self, face_id: str) -> bool:
        """Forget one face. Its candidates go with it (ON DELETE CASCADE).

        Needed because `face_id` is content-addressed over the detector's id
        AND version: re-detecting with an upgraded detector produces new ids for
        the same faces, and without a delete the library accumulates two
        rectangles per face -- the second of which no MediaRecord's face_count
        agrees with. The vector index is NOT touched here; the caller owns that
        because it owns the space name.
        """
        with self._connection:
            cursor = self._connection.execute(
                "DELETE FROM face WHERE face_id = ?", (face_id,)
            )
        return cursor.rowcount > 0

    def list_faces(self, *, limit: int = 500, offset: int = 0) -> list[dict]:
        """Every FaceRecord, in face_id order, a page at a time.

        Clustering is a whole-library operation -- a person's faces are spread
        across every event they appear in -- so the alternative to this is one
        `faces_for_media` call per media row, which is a query per photo for a
        pass that is going to look at all of them anyway.

        It is deliberately NOT filtered by eligibility. A caller that only
        wants eligible faces has `faces_for_media(eligible_only=True)` and the
        person-scoped queries; a caller that is about to CLUSTER wants every
        face, because eligibility is the output of that pass and not an input
        to it. Filtering here would quietly make each run cluster only the
        faces the previous run had already settled.
        """
        rows = self._connection.execute(
            "SELECT record_json FROM face ORDER BY face_id LIMIT ? OFFSET ?",
            (limit, offset),
        )
        return [json.loads(row["record_json"]) for row in rows]

    def count_faces(self) -> int:
        return int(self._connection.execute("SELECT count(*) FROM face").fetchone()[0])

    def review_queue(self, *, limit: int = 100) -> list[dict]:
        """Faces awaiting a human decision, most informative first.

        Ordered by how close the model came to its own threshold: the cluster
        pairs nearest the decision boundary are the ones where one tap of human
        labelling settles the most other faces.
        """
        rows = self._connection.execute(
            """
            SELECT record_json FROM face
            WHERE eligible = 0
              AND assignment IN ('auto_below_threshold', 'review_queued',
                                 'ambiguous_multiple_candidates')
            ORDER BY abs(coalesce(threshold_used, 0.92) - coalesce(confidence, 0)) ASC,
                     face_id
            LIMIT ?
            """,
            (limit,),
        )
        return [json.loads(row["record_json"]) for row in rows]

    def people_in_media(self, media_id: str, *, include_uncertain: bool = False) -> list[str]:
        sql = "SELECT DISTINCT person_id FROM face WHERE media_id = ? AND person_id IS NOT NULL"
        if not include_uncertain:
            sql += " AND eligible = 1"
        return [row["person_id"] for row in self._connection.execute(sql, (media_id,))]

    # -- moments ---------------------------------------------------------

    def put_moment(self, record: Record) -> str:
        scores = record["scores"]
        start_value, start_rate = _rational(record["source_range"]["start_time"])
        duration_value, _ = _rational(record["source_range"]["duration"])

        def score(node: Any) -> float | None:
            return node.get("value") if isinstance(node, Mapping) else None

        with self._connection:
            self._connection.execute(
                """
                INSERT INTO moment (moment_id, media_id, shot_id, start_value, start_rate,
                                    duration_value, moment_score, hook_potential,
                                    emotional_peak, eliminated, score_source, record_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (moment_id) DO UPDATE SET
                    moment_score = excluded.moment_score,
                    hook_potential = excluded.hook_potential,
                    emotional_peak = excluded.emotional_peak,
                    eliminated = excluded.eliminated,
                    score_source = excluded.score_source,
                    record_json = excluded.record_json
                """,
                (
                    record["moment_id"],
                    record["media_id"],
                    record.get("shot_id"),
                    start_value,
                    start_rate,
                    duration_value,
                    score(scores["moment_score"]),
                    score(scores.get("hook_potential")),
                    score(scores.get("emotional_peak")),
                    1 if record["elimination"]["eliminated"] else 0,
                    scores.get("source", "local_fusion"),
                    json.dumps(record, sort_keys=True, separators=(",", ":")),
                ),
            )

            people = (record.get("people") or {}).get("person_ids", []) or []
            self._connection.execute(
                "DELETE FROM moment_person WHERE moment_id = ?", (record["moment_id"],)
            )
            for person_id in people:
                self.put_person(person_id)
                self._connection.execute(
                    "INSERT OR IGNORE INTO moment_person (moment_id, person_id) VALUES (?, ?)",
                    (record["moment_id"], person_id),
                )
        return record["moment_id"]

    def best_moments(
        self,
        *,
        media_id: str | None = None,
        limit: int = 40,
        min_score: float = 0.0,
        person_id: str | None = None,
    ) -> list[dict]:
        """Surviving moments, best first. Eliminated ones never appear."""
        sql = ["SELECT m.record_json FROM moment m"]
        params: list[object] = []
        where = ["m.eliminated = 0", "m.moment_score >= ?"]
        params.append(min_score)

        if person_id is not None:
            sql.append("JOIN moment_person mp ON mp.moment_id = m.moment_id")
            where.append("mp.person_id = ?")
            params.append(person_id)
        if media_id is not None:
            where.append("m.media_id = ?")
            params.append(media_id)

        sql.append("WHERE " + " AND ".join(where))
        sql.append("ORDER BY m.moment_score DESC, m.moment_id LIMIT ?")
        params.append(limit)
        return [
            json.loads(row["record_json"])
            for row in self._connection.execute(" ".join(sql), params)
        ]

    # -- jobs ------------------------------------------------------------

    def put_job(self, record: Record) -> str:
        egress = record.get("egress") or {}
        consent = egress.get("consent") or {}
        state = record["state"]
        with self._connection:
            self._connection.execute(
                """
                INSERT INTO job (job_id, job_type, status, priority, scope, attempts,
                                 requires_egress, consent_ref, heartbeat_at,
                                 created_at, record_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (job_id) DO UPDATE SET
                    status = excluded.status,
                    priority = excluded.priority,
                    attempts = excluded.attempts,
                    heartbeat_at = excluded.heartbeat_at,
                    record_json = excluded.record_json
                """,
                (
                    record["job_id"],
                    record["job_type"],
                    state["status"],
                    record.get("priority", 100),
                    record.get("scope"),
                    state.get("attempts", 0),
                    1 if egress.get("requires_egress") else 0,
                    consent.get("ledger_entry_id"),
                    state.get("heartbeat_at"),
                    record.get("created_at"),
                    json.dumps(record, sort_keys=True, separators=(",", ":")),
                ),
            )
        return record["job_id"]

    def claim_next_job(self, *, job_types: Sequence[str] | None = None) -> dict | None:
        """The highest-priority runnable job, or None.

        Read-only here: it reports what to run next and leaves the state
        transition to the worker, which owns the heartbeat.
        """
        sql = ["SELECT record_json FROM job WHERE status = 'pending'"]
        params: list[object] = []
        if job_types:
            placeholders = ", ".join("?" for _ in job_types)
            sql.append(f"AND job_type IN ({placeholders})")
            params.extend(job_types)
        sql.append("ORDER BY priority DESC, created_at, job_id LIMIT 1")
        row = self._connection.execute(" ".join(sql), params).fetchone()
        return json.loads(row["record_json"]) if row else None

    def jobs_by_status(self, status: str) -> list[dict]:
        return [
            json.loads(row["record_json"])
            for row in self._connection.execute(
                "SELECT record_json FROM job WHERE status = ? ORDER BY job_id", (status,)
            )
        ]

    # -- preferences -----------------------------------------------------

    def put_pref_event(self, record: Record) -> str:
        decision = record["decision"]
        subject = record["subject"]
        privacy = record["privacy"]
        with self._connection:
            self._connection.execute(
                """
                INSERT INTO pref_event (event_id, occurred_at, decision_kind, surface,
                                        explicit, task, subject_type, subject_id,
                                        feature_set_id, shareable, record_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (event_id) DO UPDATE SET record_json = excluded.record_json
                """,
                (
                    record["event_id"],
                    record["occurred_at"],
                    decision["kind"],
                    decision["surface"],
                    1 if decision["explicit"] else 0,
                    record["context"]["task"],
                    subject["subject_type"],
                    subject["subject_id"],
                    record["features"]["feature_set_id"],
                    1 if privacy["shareable_for_global_model"] else 0,
                    json.dumps(record, sort_keys=True, separators=(",", ":")),
                ),
            )
        return record["event_id"]

    def pref_events(
        self, *, task: str | None = None, shareable_only: bool = False, limit: int = 1000
    ) -> list[dict]:
        sql = ["SELECT record_json FROM pref_event"]
        where: list[str] = []
        params: list[object] = []
        if task is not None:
            where.append("task = ?")
            params.append(task)
        if shareable_only:
            where.append("shareable = 1")
        if where:
            sql.append("WHERE " + " AND ".join(where))
        sql.append("ORDER BY occurred_at, event_id LIMIT ?")
        params.append(limit)
        return [
            json.loads(row["record_json"])
            for row in self._connection.execute(" ".join(sql), params)
        ]

    # -- similarity ------------------------------------------------------

    def similar_media(
        self,
        media_id: str,
        space: str,
        *,
        k: int = 10,
        max_distance: float | None = None,
    ) -> list[Neighbour]:
        """Nearest neighbours of one item, excluding itself."""
        query = self.vectors.get("media", media_id, space)
        if query is None:
            raise QueryError(f"{media_id} has no embedding in space {space!r}")
        found = self.vectors.nearest(
            space, query, k=k + 1, owner_kind="media", max_distance=max_distance
        )
        return [n for n in found if n.owner_id != media_id][:k]
