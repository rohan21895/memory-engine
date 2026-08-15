-- Memory Engine media-db, migration 0001: the initial store.
--
-- DESIGN: the contract record is the source of truth, columns are the index.
--
-- Every table keeps the full contract JSON in a `record_json` column and
-- *also* extracts the fields worth querying into real columns. That split is
-- deliberate:
--
--   * The JSON is validated against contracts/schemas, so the database can
--     never disagree with the contract about what a record means. Adding a
--     field to a schema does not require a migration unless we want to index it.
--   * The columns exist so a 100k-item library grid stays responsive. Sorting
--     500k rows by a value dug out of JSON at query time is not viable, and
--     the UI performance gate is non-negotiable.
--
-- The cost is that columns are derived data and can drift from the JSON. The
-- writer is the only thing allowed to populate them, and it derives them from
-- the parsed record in one place -- see queries.py::_media_columns.
--
-- IDS: text primary keys holding BLAKE3 hex. Not blobs: readable ids make
-- every debugging session, log line and test failure legible, and 64 bytes per
-- row is not the thing that will make this database large.

PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------- media ----

CREATE TABLE media (
    media_id            TEXT PRIMARY KEY,
    asset_kind          TEXT NOT NULL,
    kind                TEXT NOT NULL,
    byte_size           INTEGER NOT NULL,
    mime_type           TEXT,
    file_format         TEXT,

    -- Capture time is an assertion, never a bare timestamp. All three parts are
    -- indexed because a query that ignores precision would sort undated files
    -- into 1970 and open an album with them.
    captured_utc        TEXT,
    captured_local      TEXT,
    capture_precision   TEXT NOT NULL DEFAULT 'unknown',
    capture_confidence  REAL NOT NULL DEFAULT 0.0,
    capture_source      TEXT NOT NULL DEFAULT 'unknown',

    latitude            REAL,
    longitude           REAL,

    width               INTEGER,
    height              INTEGER,
    duration_value      REAL,
    duration_rate       REAL,

    phash_hex           TEXT,
    phash_bits          INTEGER,

    dedupe_group_id     TEXT,
    is_dedupe_primary   INTEGER NOT NULL DEFAULT 0,

    quality_sharpness   REAL,
    quality_exposure    REAL,
    quality_aesthetic   REAL,
    face_count          INTEGER NOT NULL DEFAULT 0,

    scene_type          TEXT,
    nsfw_score          REAL,

    excluded            INTEGER NOT NULL DEFAULT 0,
    exclusion_reasons   TEXT NOT NULL DEFAULT '[]',

    favorite            INTEGER NOT NULL DEFAULT 0,
    hidden              INTEGER NOT NULL DEFAULT 0,
    rating              INTEGER,

    processing_state    TEXT NOT NULL,
    first_seen_at       TEXT,
    updated_at          TEXT,

    record_json         TEXT NOT NULL,

    CHECK (asset_kind IN ('physical_file', 'virtual_assembly')),
    CHECK (json_valid(record_json))
);

-- The library grid's default ordering. Undated items are excluded from
-- chronological views by predicate, not by sorting them to the epoch.
CREATE INDEX media_captured_idx      ON media (captured_utc);
CREATE INDEX media_precision_idx     ON media (capture_precision);
CREATE INDEX media_kind_idx          ON media (kind, excluded);
CREATE INDEX media_dedupe_idx        ON media (dedupe_group_id, is_dedupe_primary);
CREATE INDEX media_phash_idx         ON media (phash_hex);
CREATE INDEX media_state_idx         ON media (processing_state);
CREATE INDEX media_aesthetic_idx     ON media (quality_aesthetic DESC);

-- One row per place the bytes were seen. Separate table because content
-- addressing means one record legitimately has many paths.
CREATE TABLE media_source (
    media_id        TEXT NOT NULL REFERENCES media(media_id) ON DELETE CASCADE,
    path            TEXT NOT NULL,
    adapter         TEXT NOT NULL,
    volume_id       TEXT,
    present         INTEGER NOT NULL DEFAULT 1,
    first_seen_at   TEXT,
    PRIMARY KEY (media_id, path)
);
CREATE INDEX media_source_path_idx   ON media_source (path);
CREATE INDEX media_source_volume_idx ON media_source (volume_id, present);

-- Span membership, so "give me the members of this assembly, in order" is one
-- indexed query rather than a JSON scan across the whole library.
CREATE TABLE media_span (
    span_id         TEXT NOT NULL,
    media_id        TEXT NOT NULL REFERENCES media(media_id) ON DELETE CASCADE,
    role            TEXT NOT NULL,
    member_index    INTEGER,
    offset_value    REAL,
    offset_rate     REAL,
    continuity      TEXT NOT NULL DEFAULT 'unverified',
    PRIMARY KEY (span_id, media_id),
    CHECK (role IN ('member', 'assembly'))
);
CREATE INDEX media_span_media_idx ON media_span (media_id);
CREATE INDEX media_span_order_idx ON media_span (span_id, member_index);

CREATE TABLE media_tag (
    media_id    TEXT NOT NULL REFERENCES media(media_id) ON DELETE CASCADE,
    label       TEXT NOT NULL,
    score       REAL NOT NULL,
    source      TEXT NOT NULL,
    PRIMARY KEY (media_id, label, source)
);
CREATE INDEX media_tag_label_idx ON media_tag (label, score DESC);

-- ---------------------------------------------------------------- people ---

CREATE TABLE person (
    person_id       TEXT PRIMARY KEY,
    display_name    TEXT,
    is_priority     INTEGER NOT NULL DEFAULT 0,
    -- Mirrors FaceRecord.sensitive.minor_status. Held on the person as well as
    -- the face so a sharing flow can answer "does this album contain a child"
    -- without walking every face in it.
    minor_status    TEXT NOT NULL DEFAULT 'unknown',
    created_at      TEXT
);

CREATE TABLE face (
    face_id             TEXT PRIMARY KEY,
    media_id            TEXT NOT NULL REFERENCES media(media_id) ON DELETE CASCADE,
    person_id           TEXT REFERENCES person(person_id) ON DELETE SET NULL,
    cluster_id          TEXT,
    assignment          TEXT NOT NULL,
    confidence          REAL,
    threshold_used      REAL,

    -- THE GATE. Person-scoped queries filter on this by default. Precision over
    -- recall is enforced here, at the query layer, because that is where a wrong
    -- person actually reaches an album.
    eligible            INTEGER NOT NULL DEFAULT 0,

    minor_status        TEXT NOT NULL DEFAULT 'unknown',
    has_labeling_consent INTEGER NOT NULL DEFAULT 0,

    frame_value         REAL,
    frame_rate          REAL,
    track_id            TEXT,
    detection_score     REAL NOT NULL,
    face_area_ratio     REAL NOT NULL,
    quality             REAL,

    record_json         TEXT NOT NULL,

    CHECK (json_valid(record_json)),
    -- The precision-first invariant, enforced by the storage engine itself and
    -- not merely by the code that writes to it.
    CHECK (eligible IN (0, 1)),
    CHECK (eligible = 0 OR person_id IS NOT NULL),
    CHECK (eligible = 0 OR assignment IN ('user_confirmed', 'auto_high_confidence')),
    -- A confirmed minor may be counted, but never named without consent.
    CHECK (minor_status <> 'confirmed_minor'
           OR person_id IS NULL
           OR has_labeling_consent = 1)
);
CREATE INDEX face_media_idx   ON face (media_id);
CREATE INDEX face_person_idx  ON face (person_id, eligible);
CREATE INDEX face_cluster_idx ON face (cluster_id);
CREATE INDEX face_review_idx  ON face (assignment) WHERE eligible = 0;

-- Runner-up identities the matcher considered but was not allowed to act on.
--
-- Kept in its own table rather than only inside the record JSON because the
-- review UI's central question is "which photos MIGHT be this person" -- the
-- active-learning loop is built on exactly the matches that failed the gate.
-- A candidate is explicitly not an assignment: nothing here may reach automated
-- output, which is why it cannot live in `face.person_id`.
CREATE TABLE face_candidate (
    face_id     TEXT NOT NULL REFERENCES face(face_id) ON DELETE CASCADE,
    person_id   TEXT NOT NULL REFERENCES person(person_id) ON DELETE CASCADE,
    confidence  REAL NOT NULL,
    PRIMARY KEY (face_id, person_id)
);
CREATE INDEX face_candidate_person_idx ON face_candidate (person_id, confidence DESC);

-- --------------------------------------------------------------- moments ---

CREATE TABLE moment (
    moment_id       TEXT PRIMARY KEY,
    media_id        TEXT NOT NULL REFERENCES media(media_id) ON DELETE CASCADE,
    shot_id         TEXT,
    start_value     REAL NOT NULL,
    start_rate      REAL NOT NULL,
    duration_value  REAL NOT NULL,
    moment_score    REAL NOT NULL,
    hook_potential  REAL,
    emotional_peak  REAL,
    eliminated      INTEGER NOT NULL DEFAULT 0,
    score_source    TEXT NOT NULL DEFAULT 'local_fusion',
    record_json     TEXT NOT NULL,
    CHECK (json_valid(record_json))
);
-- The culling product's central query: the best surviving moments in a video.
CREATE INDEX moment_media_idx ON moment (media_id, eliminated, moment_score DESC);
CREATE INDEX moment_score_idx ON moment (eliminated, moment_score DESC);

CREATE TABLE moment_person (
    moment_id   TEXT NOT NULL REFERENCES moment(moment_id) ON DELETE CASCADE,
    person_id   TEXT NOT NULL REFERENCES person(person_id) ON DELETE CASCADE,
    PRIMARY KEY (moment_id, person_id)
);

-- ------------------------------------------------------------------ jobs ---

CREATE TABLE job (
    job_id          TEXT PRIMARY KEY,
    job_type        TEXT NOT NULL,
    status          TEXT NOT NULL,
    priority        INTEGER NOT NULL DEFAULT 100,
    scope           TEXT,
    attempts        INTEGER NOT NULL DEFAULT 0,
    requires_egress INTEGER NOT NULL DEFAULT 0,
    consent_ref     TEXT,
    heartbeat_at    TEXT,
    created_at      TEXT,
    record_json     TEXT NOT NULL,
    CHECK (json_valid(record_json)),
    -- No egress without a consent-ledger entry. The CI egress test is the second
    -- line of defence; this is the first, and it lives in the storage layer so a
    -- job cannot even be persisted in a state that would allow it.
    CHECK (requires_egress = 0 OR consent_ref IS NOT NULL)
);
CREATE INDEX job_queue_idx  ON job (status, priority DESC, created_at);
CREATE INDEX job_type_idx   ON job (job_type, status);

-- --------------------------------------------------------- preferences ----

CREATE TABLE pref_event (
    event_id        TEXT PRIMARY KEY,
    occurred_at     TEXT NOT NULL,
    decision_kind   TEXT NOT NULL,
    surface         TEXT NOT NULL,
    explicit        INTEGER NOT NULL DEFAULT 1,
    task            TEXT NOT NULL,
    subject_type    TEXT NOT NULL,
    subject_id      TEXT NOT NULL,
    feature_set_id  TEXT NOT NULL,
    shareable       INTEGER NOT NULL DEFAULT 0,
    record_json     TEXT NOT NULL,
    CHECK (json_valid(record_json)),
    -- Feature vectors and decisions only. A row that claims to carry pixels is
    -- rejected by the database, not merely by the writer.
    CHECK (json_extract(record_json, '$.pixel_data_present') = 0)
);
CREATE INDEX pref_task_idx   ON pref_event (task, decision_kind);
CREATE INDEX pref_subject_idx ON pref_event (subject_type, subject_id);

-- --------------------------------------------------------------- search ----

-- Search text is assembled from tags, captions, OCR, transcripts and the
-- original filename. content='' makes this a contentless index: the text is not
-- duplicated in the FTS table, we only keep the terms.
CREATE VIRTUAL TABLE media_fts USING fts5(
    media_id UNINDEXED,
    body,
    tokenize = 'unicode61 remove_diacritics 2'
);

-- --------------------------------------------------------------- vectors ---

-- Portable storage for embeddings. The sqlite-vec virtual table, when the
-- extension is available, is built ALONGSIDE this rather than instead of it, so
-- the database file remains readable on a machine without the extension. See
-- vectors.py for the two backends.
CREATE TABLE vector (
    owner_kind  TEXT NOT NULL,
    owner_id    TEXT NOT NULL,
    space       TEXT NOT NULL,
    dimensions  INTEGER NOT NULL,
    quantization TEXT NOT NULL DEFAULT 'float32',
    embedding   BLOB NOT NULL,
    PRIMARY KEY (owner_kind, owner_id, space),
    CHECK (owner_kind IN ('media', 'face', 'moment'))
);
CREATE INDEX vector_space_idx ON vector (space, owner_kind);
