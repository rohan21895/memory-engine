# media-db

The local store the whole system reads and writes: SQLite schema, migrations, FTS5 index, vector index, and the query API both agents call.

Owned by Claude (CLAUDE.md). Codex consumes it through the generated contract types and this API — never by reaching into tables directly, because the columns are derived data and the JSON is the truth.

## Design

**The contract record is the source of truth; columns are the index.** Every table stores the full contract JSON in `record_json` *and* extracts the queryable fields into real columns. The JSON means the database can never disagree with the contract about what a record means, and adding a schema field needs no migration unless we want to index it. The columns mean a 100k-item grid stays responsive — digging values out of JSON at query time does not survive the performance gate.

The cost is that columns can drift from the JSON. Only the writer populates them, from the parsed record, in one place. `test_indexed_columns_agree_with_the_stored_json` checks it.

**Safety rules are storage constraints, not writer discipline.** SQLite `CHECK`s refuse:

- a face marked eligible without a person, or with an assignment that isn't `user_confirmed` / `auto_high_confidence`
- naming a `confirmed_minor` without labeling consent
- a job requiring egress without a consent-ledger reference
- a `PrefEvent` claiming to carry pixel data

A bug in the writer cannot get past these, which is the point.

**The face gate is the default.** `list_media(person_id=...)` returns only faces eligible for automated output. `include_uncertain=True` widens it to runner-up candidates for review tooling — the active-learning loop is built precisely on the matches that failed the gate. Nothing reached that way is ever eligible.

**Undated media is excluded from chronological queries.** `chronological=True` both orders by capture time and drops items whose precision is `unknown`. An undated file has no position on a timeline, and sorting it to the epoch opens every album with it.

## Vector index

Two backends behind one interface:

| Backend | When | Behaviour |
|---|---|---|
| `sqlite_vec` | the loadable extension is present | production path |
| `brute_force` | otherwise | exact, same results, same order, O(n) |

The fallback is not a stub — it returns identical results, just linearly. The extension's index is built *alongside* the portable `vector` table, never instead of it, so a `.db` written on a machine with the extension stays readable on one without.

`db.vectors.backend` reports which is live. Never infer it: a search that silently degraded from indexed to linear is something the user deserves to be able to discover.

## Usage

```python
from memory_engine_media_db import Database

with Database.open("library.db") as db:          # migrates on open
    db.put_media(media_record)                    # contract-shaped dict
    db.put_face(face_record)
    db.put_moment(moment_record)

    db.search("sunset")                           # FTS over tags, captions, filenames, devices
    db.list_media(person_id=pid, chronological=True)
    db.best_moments(media_id=..., limit=40)       # eliminated moments never appear
    db.review_queue()                             # nearest the decision boundary first
    db.resolve_path(media_id)                     # content hash -> path, for OTIO export
    db.span_members(span_id)                      # chaptered files, in playback order
```

## Tests

```bash
python3 -m unittest discover -s packages/media-db/tests -v
```

Also runs under `pytest packages/media-db/tests`. No dependencies beyond the standard library.

Every test runs against the **golden fixtures** in `contracts/fixtures/` rather than records invented here. If a contract change breaks what media-db can store, these fail — the two cannot drift apart quietly.

## Open questions for Codex

1. **This API surface is effectively contract.** The build plan calls it "the query API both sides call". Shape it now, before it ossifies.
2. **`resolve_path(media_id)`** is the function `docs/otio-mapping.md` assumes for turning a content hash into a `file://` URL at export time. It returns `None` for a virtual assembly — that has no path, and callers must expand it via `span_members`. Confirm that split works for `render-video`.
3. **Encryption.** The build plan puts the DB key in the OS keychain, with the schema mine and key management yours. Whether the key arrives as a SQLCipher passphrase or the file is sealed at rest changes the bootstrap in `db.connect`, and nothing here does encryption yet.
4. **Write concurrency.** WAL with `busy_timeout=5000` assumes one writer and many readers. If ingest wants several concurrent writer processes, that assumption needs revisiting before it is load-bearing.
