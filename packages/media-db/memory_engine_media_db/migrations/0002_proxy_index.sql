-- Memory Engine media-db, migration 0002: an indexed proxy table.
--
-- WHY (issue #32): `resolve_proxy` was
--
--     SELECT record_json FROM media WHERE record_json LIKE '%' || ? || '%' LIMIT 50
--
-- which is a full table scan of every MediaRecord in the library, on the hot
-- path of every proxy inference. At the 100k-record gate that is ~100k JSON
-- blobs read and substring-matched per lookup, and the analysis pass does one
-- lookup per item -- quadratic in library size on the code path that runs most.
--
-- It was also WRONG, not merely slow, in a way no test caught. The substring
-- match hits any record whose JSON contains the id ANYWHERE: a `dedupe.group_id`,
-- a `span_id`, a path that embeds the hash, another record's `input_proxy_id`
-- provenance field. `LIMIT 50` then truncates that candidate set. Once more than
-- fifty records mention an id, the record that actually owns the proxy can fall
-- outside the limit and the resolver returns None -- reported downstream as
-- PROXY_NOT_FOUND, i.e. "that proxy does not exist" rather than "the query gave
-- up". Silent, plausible, and strictly worse the larger the library gets.
--
-- THE TABLE IS DERIVED DATA, LIKE EVERY OTHER COLUMN HERE.
-- `media.record_json` remains the source of truth. This is an index over
-- `$.proxies[*]`, written by the same `put_media` transaction that writes the
-- record, so a row can never exist for a record that was not committed. The
-- backfill below is that same derivation applied to rows written before this
-- migration existed, so an upgraded database and a freshly created one are
-- indistinguishable.
--
-- WHY THE KEY IS (proxy_id, media_id) AND NOT proxy_id ALONE
-- Proxies are content-addressed: the id is a BLAKE3 of the proxy bytes, so two
-- records that produce byte-identical proxies legitimately share one id (a
-- JPEG and its HEIC twin can reduce to the same 512px thumbnail). Keying on
-- proxy_id alone would have the second record's write REPLACE the first's row,
-- and then deleting the second record would cascade the row away while the
-- first still lists that proxy -- `resolve_proxy` returning None for a proxy
-- that exists, which is the same silent miss this migration is fixing, just
-- rarer. A composite key stores one row per (proxy, owner) and the cascade
-- removes exactly the rows whose owner went away.
--
-- The primary key's index is ordered (proxy_id, media_id), so a lookup by
-- proxy_id alone is a range search on that index rather than a scan. The
-- resolver takes the lowest media_id among matches, which is deterministic;
-- see `Database.resolve_proxy` for why "arbitrary among equals" is sound here
-- and why `media_id_for_proxy` is a separate method.

PRAGMA foreign_keys = ON;

CREATE TABLE media_proxy (
    proxy_id    TEXT NOT NULL,
    media_id    TEXT NOT NULL REFERENCES media(media_id) ON DELETE CASCADE,
    kind        TEXT NOT NULL,
    path        TEXT NOT NULL,
    byte_size   INTEGER,
    proxy_json  TEXT NOT NULL,
    PRIMARY KEY (proxy_id, media_id),
    CHECK (json_valid(proxy_json))
);

-- Deleting a media row must take its proxies with it, and "every proxy of this
-- record" is a real query (cascade, re-proxying, orphan sweeps). Without this
-- index the cascade itself is a table scan, so the fix would have moved the
-- scan from read time to delete time rather than removing it.
CREATE INDEX media_proxy_media_idx ON media_proxy (media_id, kind);

-- Backfill from the records already stored. json_each is part of SQLite's JSON1
-- extension, which this schema already depends on (`CHECK (json_valid(...))` in
-- migration 0001), so any build that can open the existing database can run this.
--
-- INSERT OR REPLACE rather than plain INSERT: a record whose proxies array
-- lists the same proxy twice is odd but not invalid, and a bare INSERT would
-- abort the entire migration on it, leaving the database at version 1 with no
-- explanation a user could act on.
INSERT OR REPLACE INTO media_proxy (proxy_id, media_id, kind, path, byte_size, proxy_json)
SELECT
    json_extract(proxy.value, '$.proxy_id'),
    media.media_id,
    json_extract(proxy.value, '$.kind'),
    json_extract(proxy.value, '$.path'),
    json_extract(proxy.value, '$.byte_size'),
    proxy.value
FROM media, json_each(media.record_json, '$.proxies') AS proxy
WHERE json_extract(proxy.value, '$.proxy_id') IS NOT NULL
  AND json_extract(proxy.value, '$.kind') IS NOT NULL
  AND json_extract(proxy.value, '$.path') IS NOT NULL;
