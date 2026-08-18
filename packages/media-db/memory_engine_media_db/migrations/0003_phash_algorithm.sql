-- Memory Engine media-db, migration 0003: the perceptual hash keeps its name.
--
-- WHY (issue #14). The hash was indexed by its digest and its length and not by
-- what it is. `phash-dct-64`, `phash-dct-64-v2`, `dhash-64`, `ahash-64` and
-- `wavelet-64` are all sixteen hex characters, so a lookup on `media_phash_idx`
-- returns rows produced by any of them and the caller has nothing to tell them
-- apart. Two digests from different algorithms are unrelated numbers; a Hamming
-- distance between them is not small or large, it is meaningless -- and dedupe
-- acts on that number by dropping a photo from every automated output. The
-- failure is therefore silent and destructive rather than an error, which is
-- why the name has to be a column a query can filter on rather than a value
-- buried in the JSON.
--
-- LIKE EVERY OTHER COLUMN HERE, THIS IS DERIVED DATA. `media.record_json`
-- remains the source of truth; `$.perceptual.image_hash.algorithm` has been in
-- every record all along. The backfill below is the same derivation applied to
-- rows written before the column existed, so an upgraded database and a freshly
-- created one are indistinguishable -- the property migration 0002 established
-- and the reason a user does not have to re-scan a library to get a fix.
--
-- A row whose record carries no image_hash keeps NULL, and NULL means "unknown,
-- do not compare". It must never be read as a default: guessing which algorithm
-- produced a digest is precisely the mistake this column exists to prevent.

ALTER TABLE media ADD COLUMN phash_algorithm TEXT;

UPDATE media
SET phash_algorithm = json_extract(record_json, '$.perceptual.image_hash.algorithm')
WHERE json_extract(record_json, '$.perceptual.image_hash.algorithm') IS NOT NULL;

-- Rebuilt with the algorithm leading, because a band lookup selects on the
-- algorithm first and the digest second. Leading with the digest would still
-- scan across algorithms and hand the caller rows it must not compare.
DROP INDEX IF EXISTS media_phash_idx;
CREATE INDEX media_phash_idx ON media (phash_algorithm, phash_hex);
