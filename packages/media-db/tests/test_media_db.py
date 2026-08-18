"""Tests for media-db.

Everything here runs against the GOLDEN FIXTURES rather than against records
invented locally. That is the point: if a contract change breaks what media-db
can store, these fail, and the two cannot drift apart quietly.

unittest.TestCase so the same file runs under `python3 -m unittest discover`
(what scripts/ci/run-workspace-check.mjs uses) and under pytest.
"""

from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = PACKAGE_ROOT.parent.parent
FIXTURES = REPO_ROOT / "contracts" / "fixtures"

sys.path.insert(0, str(PACKAGE_ROOT))

from memory_engine_media_db import (  # noqa: E402
    Database,
    MigrationError,
    SCHEMA_VERSION,
    connect,
    cosine_distance,
    current_version,
    migrate,
)

MANIFEST = json.loads((FIXTURES / "index.json").read_text(encoding="utf-8"))


def fixtures_for(schema: str, expectation: str = "valid") -> list[dict]:
    out = []
    for entry in MANIFEST["fixtures"]:
        if entry["schema"] == schema and entry["expectation"] == expectation:
            out.append(json.loads((FIXTURES / entry["path"]).read_text(encoding="utf-8")))
    return out


def fixture_named(schema: str, needle: str, expectation: str = "valid") -> dict:
    """Look up one fixture by a fragment of its path.

    Restricted to `valid` by default and required to be unambiguous. Without
    both guards, `below-threshold` silently matches the schema-INVALID
    `eligible-while-below-threshold` fixture, and a test asserting the gate
    holds would be asserting it against a record that violates it.
    """
    matches = [
        entry
        for entry in MANIFEST["fixtures"]
        if entry["schema"] == schema
        and entry["expectation"] == expectation
        and needle in entry["path"]
    ]
    if not matches:
        raise LookupError(f"no {expectation} {schema} fixture matching {needle!r}")
    if len(matches) > 1:
        raise LookupError(
            f"{needle!r} matches {len(matches)} {schema} fixtures: "
            f"{[m['path'] for m in matches]}"
        )
    return json.loads((FIXTURES / matches[0]["path"]).read_text(encoding="utf-8"))


class DatabaseTestCase(unittest.TestCase):
    """A migrated in-memory database, loaded with every valid media fixture."""

    def setUp(self) -> None:
        self.db = Database.open(":memory:")
        self.addCleanup(self.db.close)

    def load_media(self) -> list[dict]:
        records = fixtures_for("media-record")
        for record in records:
            self.db.put_media(record)
        return records


class TestMigrations(unittest.TestCase):
    def test_migrate_creates_every_table(self):
        connection = connect(":memory:")
        self.addCleanup(connection.close)
        migrate(connection)

        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        for expected in (
            "media", "media_source", "media_span", "media_tag",
            "person", "face", "moment", "moment_person",
            "job", "pref_event", "vector",
        ):
            self.assertIn(expected, tables)

    def test_migrate_is_idempotent(self):
        connection = connect(":memory:")
        self.addCleanup(connection.close)
        first = migrate(connection)
        second = migrate(connection)
        self.assertEqual(first, second)
        self.assertEqual(SCHEMA_VERSION, second)

    def test_migrate_resumes_from_a_partial_version(self):
        connection = connect(":memory:")
        self.addCleanup(connection.close)
        self.assertEqual(0, current_version(connection))
        migrate(connection)
        self.assertEqual(SCHEMA_VERSION, current_version(connection))

    def test_a_database_from_the_future_is_refused(self):
        """Opening a newer file read-write would silently misread it. Refuse instead."""
        connection = connect(":memory:")
        self.addCleanup(connection.close)
        migrate(connection)
        connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 5}")
        with self.assertRaises(MigrationError) as caught:
            migrate(connection)
        self.assertIn("newer build", str(caught.exception))

    def test_survives_a_reopen_on_disk(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "library.db"
            with Database.open(path) as db:
                db.put_media(fixture_named("media-record", "image-beach-sunset"))
                self.assertEqual(1, db.count_media())
            with Database.open(path) as db:
                self.assertEqual(SCHEMA_VERSION, db.schema_version)
                self.assertEqual(1, db.count_media())


class TestMediaRoundTrip(DatabaseTestCase):
    def test_every_valid_media_fixture_stores_and_returns_unchanged(self):
        for record in fixtures_for("media-record"):
            with self.subTest(media_id=record["media_id"][:12]):
                self.db.put_media(record)
                self.assertEqual(record, self.db.get_media(record["media_id"]))

    def test_writing_the_same_record_twice_is_a_no_op(self):
        record = fixture_named("media-record", "image-beach-sunset")
        self.db.put_media(record)
        self.db.put_media(record)
        self.assertEqual(1, self.db.count_media())
        self.assertEqual(
            1,
            self.db.connection.execute(
                "SELECT count(*) FROM media_source WHERE media_id = ? AND adapter = 'filesystem'",
                (record["media_id"],),
            ).fetchone()[0],
        )

    def test_indexed_columns_agree_with_the_stored_json(self):
        """The columns are derived data. If they drift from the JSON, every query
        built on them is wrong in a way no test of the JSON would catch."""
        for record in self.load_media():
            row = self.db.connection.execute(
                "SELECT * FROM media WHERE media_id = ?", (record["media_id"],)
            ).fetchone()
            captured = record["capture"]["captured_at"]
            self.assertEqual(record["kind"], row["kind"])
            self.assertEqual(record["asset_kind"], row["asset_kind"])
            self.assertEqual(record["byte_size"], row["byte_size"])
            self.assertEqual(captured["precision"], row["capture_precision"])
            self.assertEqual(captured.get("utc"), row["captured_utc"])
            self.assertEqual(record["processing"]["state"], row["processing_state"])
            self.assertEqual(
                1 if record["exclusion"]["excluded_from_automation"] else 0, row["excluded"]
            )

    def test_multiple_sources_collapse_onto_one_record(self):
        record = fixture_named("media-record", "image-beach-sunset")
        self.db.put_media(record)
        paths = self.db.connection.execute(
            "SELECT count(*) FROM media_source WHERE media_id = ?", (record["media_id"],)
        ).fetchone()[0]
        self.assertEqual(2, paths, "the same bytes seen twice must be one record, two sources")

    def test_deleting_media_takes_its_children_with_it(self):
        media = fixture_named("media-record", "image-beach-sunset")
        self.db.put_media(media)
        self.db.vectors.put("media", media["media_id"], "siglip2_so400m_1152", [0.1] * 8)
        with self.db.connection:
            self.db.connection.execute(
                "DELETE FROM media WHERE media_id = ?", (media["media_id"],)
            )
        for table in ("media_source", "media_tag", "media_span"):
            remaining = self.db.connection.execute(
                f"SELECT count(*) FROM {table} WHERE media_id = ?", (media["media_id"],)
            ).fetchone()[0]
            self.assertEqual(0, remaining, f"{table} left an orphan")


class TestSpans(DatabaseTestCase):
    def test_span_members_come_back_in_playback_order(self):
        self.load_media()
        assembly = fixture_named("media-record", "span-assembly")
        members = self.db.span_members(assembly["span"]["span_id"])
        self.assertEqual(assembly["span"]["member_media_ids"], members)

    def test_a_virtual_assembly_has_no_path_of_its_own(self):
        self.load_media()
        assembly = fixture_named("media-record", "span-assembly")
        self.assertIsNone(
            self.db.resolve_path(assembly["media_id"]),
            "an assembly has no bytes on disk; callers must expand it via span_members",
        )

    def test_resolve_path_finds_a_real_file(self):
        self.load_media()
        record = fixture_named("media-record", "image-beach-sunset")
        path = self.db.resolve_path(record["media_id"])
        self.assertIn(path, [s["path"] for s in record["sources"]])

    def test_resolve_path_prefers_a_present_source(self):
        record = json.loads(json.dumps(fixture_named("media-record", "image-beach-sunset")))
        record["sources"][0]["present"] = False
        self.db.put_media(record)
        self.assertEqual(record["sources"][1]["path"], self.db.resolve_path(record["media_id"]))

    def test_resolve_path_is_none_for_an_unknown_id(self):
        self.assertIsNone(self.db.resolve_path("0" * 64))


class TestChronology(DatabaseTestCase):
    def test_undated_media_is_excluded_from_chronological_listings(self):
        self.load_media()
        undated = fixture_named("media-record", "image-no-exif-date")
        self.assertEqual("unknown", undated["capture"]["captured_at"]["precision"])

        ids = {m.media_id for m in self.db.list_media(chronological=True, limit=500)}
        self.assertNotIn(
            undated["media_id"],
            ids,
            "an undated file has no position on a timeline; sorting it to the epoch "
            "would open every album with it",
        )

        all_ids = {m.media_id for m in self.db.list_media(limit=500, include_excluded=True)}
        self.assertIn(undated["media_id"], all_ids, "but it must still be in the library")

    def test_a_day_precision_date_is_good_enough_for_a_timeline(self):
        whatsapp = fixture_named("media-record", "whatsapp-filename-date")
        self.assertEqual("day", whatsapp["capture"]["captured_at"]["precision"])
        whatsapp = json.loads(json.dumps(whatsapp))
        whatsapp["capture"]["captured_at"]["utc"] = "2023-08-12T00:00:00+00:00"
        self.db.put_media(whatsapp)
        ids = {m.media_id for m in self.db.list_media(chronological=True)}
        self.assertIn(whatsapp["media_id"], ids)

    def test_chronological_listing_is_ordered(self):
        self.load_media()
        listed = self.db.list_media(chronological=True, limit=500)
        stamps = [m.captured_utc for m in listed]
        self.assertEqual(sorted(stamps), stamps)

    def test_quarantined_media_is_excluded_by_default(self):
        self.load_media()
        corrupt = fixture_named("media-record", "truncated")
        ids = {m.media_id for m in self.db.list_media(limit=500)}
        self.assertNotIn(corrupt["media_id"], ids)
        ids = {m.media_id for m in self.db.list_media(limit=500, include_excluded=True)}
        self.assertIn(corrupt["media_id"], ids, "excluded is not deleted")


class TestFullTextSearch(DatabaseTestCase):
    def test_finds_media_by_tag(self):
        self.load_media()
        hits = self.db.search("sunset")
        self.assertTrue(hits)
        record = fixture_named("media-record", "image-beach-sunset")
        self.assertIn(record["media_id"], [h.media_id for h in hits])

    def test_finds_media_by_original_filename(self):
        self.load_media()
        hits = self.db.search("IMG_4821")
        self.assertIn(
            fixture_named("media-record", "image-beach-sunset")["media_id"],
            [h.media_id for h in hits],
            "people search for filenames far more often than designers expect",
        )

    def test_finds_media_by_camera(self):
        self.load_media()
        hits = self.db.search("GoPro")
        self.assertTrue(hits)

    def test_search_is_diacritic_insensitive(self):
        record = json.loads(json.dumps(fixture_named("media-record", "image-beach-sunset")))
        record["user"]["caption"] = "Café at Kata"
        self.db.put_media(record)
        self.assertTrue(self.db.search("cafe"), "unicode61 remove_diacritics 2 should match")

    def test_excluded_media_is_not_returned_by_default(self):
        record = json.loads(json.dumps(fixture_named("media-record", "image-beach-sunset")))
        record["exclusion"]["excluded_from_automation"] = True
        record["exclusion"]["reasons"] = ["screenshot"]
        self.db.put_media(record)
        self.assertEqual([], self.db.search("sunset"))
        self.assertTrue(self.db.search("sunset", include_excluded=True))

    def test_reindexing_replaces_rather_than_duplicates(self):
        record = fixture_named("media-record", "image-beach-sunset")
        self.db.put_media(record)
        self.db.put_media(record)
        rows = self.db.connection.execute(
            "SELECT count(*) FROM media_fts WHERE media_id = ?", (record["media_id"],)
        ).fetchone()[0]
        self.assertEqual(1, rows)

    def test_empty_query_returns_nothing_rather_than_everything(self):
        self.load_media()
        self.assertEqual([], self.db.search("   "))


class TestFaceGate(DatabaseTestCase):
    """Precision over recall, enforced where it actually matters: at the query."""

    def setUp(self) -> None:
        super().setUp()
        self.load_media()
        for record in fixtures_for("face-record"):
            self.db.put_face(record)

    def test_every_valid_face_fixture_round_trips(self):
        for record in fixtures_for("face-record"):
            with self.subTest(face_id=record["face_id"][:12]):
                stored = self.db.faces_for_media(record["media_id"])
                self.assertIn(record, stored)

    def test_person_queries_exclude_uncertain_faces_by_default(self):
        below = fixture_named("face-record", "below-threshold")
        self.assertFalse(below["identity"]["eligible_for_automated_output"])

        # The uncertain face has a leading candidate it is not allowed to be.
        candidate = below["identity"]["candidates"][0]["person_id"]
        confident = fixture_named("face-record", "confirmed-high-confidence")
        self.assertEqual(candidate, confident["identity"]["person_id"])

        safe = {m.media_id for m in self.db.list_media(person_id=candidate)}
        self.assertNotIn(
            below["media_id"],
            safe,
            "an uncertain match must never reach automated output",
        )

        widened = {
            m.media_id
            for m in self.db.list_media(person_id=candidate, include_uncertain=True)
        }
        self.assertIn(below["media_id"], widened, "but review tooling must be able to see it")

    def test_candidates_are_searchable_but_never_eligible(self):
        """The active-learning loop needs 'which photos MIGHT be this person'.
        None of those matches may reach automated output."""
        below = fixture_named("face-record", "below-threshold")
        candidate = below["identity"]["candidates"][0]["person_id"]

        rows = self.db.connection.execute(
            "SELECT confidence FROM face_candidate WHERE face_id = ? AND person_id = ?",
            (below["face_id"], candidate),
        ).fetchall()
        self.assertEqual(1, len(rows))

        eligible_media = {m.media_id for m in self.db.list_media(person_id=candidate)}
        for face in self.db.faces_for_media(below["media_id"]):
            self.assertFalse(face["identity"]["eligible_for_automated_output"])
        self.assertNotIn(below["media_id"], eligible_media)

    def test_list_faces_pages_the_whole_library_and_filters_nothing(self):
        """Clustering needs every face, including the ones nobody has settled.

        Filtering by eligibility here would make each run cluster only the
        faces the previous run had already decided about, which quietly turns
        an unsupervised pass over the library into a pass over its conclusions.
        """
        expected = sorted(record["face_id"] for record in fixtures_for("face-record"))
        self.assertEqual(len(expected), self.db.count_faces())

        paged: list[str] = []
        offset = 0
        while True:
            page = self.db.list_faces(limit=2, offset=offset)
            paged.extend(record["face_id"] for record in page)
            if len(page) < 2:
                break
            offset += 2
        self.assertEqual(expected, paged)
        self.assertEqual(expected, sorted(paged), "list_faces must order by face_id")

        ineligible = [
            record
            for record in self.db.list_faces(limit=100)
            if not record["identity"]["eligible_for_automated_output"]
        ]
        self.assertTrue(ineligible, "list_faces dropped the unsettled faces")

    def test_delete_face_removes_the_row_and_its_candidates(self):
        """Re-detection with an upgraded detector produces NEW face ids.

        `face_id` is a digest over the detector's id and version, so the old
        rows are not stale duplicates that will be overwritten -- they are
        addressed differently and would survive forever, leaving a MediaRecord
        whose face_count disagrees with the rectangles stored for it.
        """
        below = fixture_named("face-record", "below-threshold")
        self.assertTrue(
            self.db.connection.execute(
                "SELECT 1 FROM face_candidate WHERE face_id = ?", (below["face_id"],)
            ).fetchone()
        )

        self.assertTrue(self.db.delete_face(below["face_id"]))
        self.assertEqual([], [
            record
            for record in self.db.faces_for_media(below["media_id"])
            if record["face_id"] == below["face_id"]
        ])
        self.assertIsNone(
            self.db.connection.execute(
                "SELECT 1 FROM face_candidate WHERE face_id = ?", (below["face_id"],)
            ).fetchone()
        )
        self.assertFalse(
            self.db.delete_face(below["face_id"]),
            "deleting a face that is already gone must report that, not claim a delete",
        )

    def test_people_in_media_hides_uncertain_matches(self):
        below = fixture_named("face-record", "below-threshold")
        self.assertEqual([], self.db.people_in_media(below["media_id"]))

    def test_the_database_refuses_an_ineligible_face_claiming_eligibility(self):
        """The gate is a storage constraint, not just writer discipline."""
        record = json.loads(json.dumps(fixture_named("face-record", "below-threshold")))
        record["identity"]["eligible_for_automated_output"] = True
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.put_face(record)

    def test_the_database_refuses_naming_a_minor_without_consent(self):
        record = json.loads(json.dumps(fixture_named("face-record", "minor-with-consent")))
        record["sensitive"]["labeling_consent"] = None
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.put_face(record)

    def test_a_consented_minor_is_storable_and_queryable(self):
        record = fixture_named("face-record", "minor-with-consent")
        person_id = record["identity"]["person_id"]
        self.assertIn(person_id, self.db.people_in_media(record["media_id"]))

    def test_review_queue_surfaces_the_uncertain_faces(self):
        queued = self.db.review_queue()
        ids = [f["face_id"] for f in queued]
        self.assertIn(fixture_named("face-record", "below-threshold")["face_id"], ids)
        for face in queued:
            self.assertFalse(face["identity"]["eligible_for_automated_output"])

    def test_review_queue_orders_by_distance_from_the_threshold(self):
        """Ten taps of labelling should fix a thousand photos, so the faces
        nearest the decision boundary come first."""
        base = fixture_named("face-record", "below-threshold")
        for suffix, confidence in (("a", 0.20), ("b", 0.91), ("c", 0.55)):
            record = json.loads(json.dumps(base))
            record["face_id"] = (suffix * 2) + base["face_id"][2:]
            record["identity"]["confidence"] = confidence
            record["identity"]["threshold_used"] = 0.92
            self.db.put_face(record)

        confidences = [
            f["identity"]["confidence"]
            for f in self.db.review_queue()
            if f["identity"].get("confidence") is not None
        ]
        gaps = [abs(0.92 - c) for c in confidences]
        self.assertEqual(sorted(gaps), gaps)


class TestMoments(DatabaseTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.load_media()
        for record in fixtures_for("moment-record"):
            self.db.put_moment(record)

    def test_every_valid_moment_fixture_round_trips(self):
        for record in fixtures_for("moment-record"):
            with self.subTest(moment_id=record["moment_id"][:12]):
                found = self.db.best_moments(media_id=record["media_id"], limit=100)
                if record["elimination"]["eliminated"]:
                    self.assertNotIn(record, found)
                else:
                    self.assertIn(record, found)

    def test_eliminated_moments_never_surface(self):
        eliminated = fixture_named("moment-record", "eliminated-shake")
        self.assertTrue(eliminated["elimination"]["eliminated"])
        ids = [m["moment_id"] for m in self.db.best_moments(limit=100)]
        self.assertNotIn(eliminated["moment_id"], ids)

    def test_best_moments_are_ordered_by_score(self):
        scores = [m["scores"]["moment_score"]["value"] for m in self.db.best_moments(limit=100)]
        self.assertEqual(sorted(scores, reverse=True), scores)

    def test_moments_can_be_filtered_by_person(self):
        peak = fixture_named("moment-record", "emotional-peak")
        person_id = peak["people"]["person_ids"][0]
        ids = [m["moment_id"] for m in self.db.best_moments(person_id=person_id)]
        self.assertIn(peak["moment_id"], ids)

    def test_a_chapter_crossing_moment_is_stored_against_the_assembly(self):
        crossing = fixture_named("moment-record", "crosses-chapter")
        assembly = fixture_named("media-record", "span-assembly")
        self.assertEqual(assembly["media_id"], crossing["media_id"])
        ids = [m["moment_id"] for m in self.db.best_moments(media_id=assembly["media_id"])]
        self.assertIn(crossing["moment_id"], ids)


class TestJobs(DatabaseTestCase):
    def setUp(self) -> None:
        super().setUp()
        for record in fixtures_for("job-spec"):
            self.db.put_job(record)

    def test_every_valid_job_fixture_round_trips(self):
        for record in fixtures_for("job-spec"):
            with self.subTest(job_id=record["job_id"][:12]):
                stored = self.db.jobs_by_status(record["state"]["status"])
                self.assertIn(record, stored)

    def test_distinct_scan_roots_are_distinct_rows(self):
        scans = [
            j for j in fixtures_for("job-spec") if j["job_type"] == "scan_source"
        ]
        self.assertEqual(2, len(scans))
        self.assertEqual(
            2,
            self.db.connection.execute(
                "SELECT count(*) FROM job WHERE job_type = 'scan_source'"
            ).fetchone()[0],
            "without the locator digest these would collide on the primary key and "
            "the second drive would silently never be scanned",
        )

    def test_claim_next_job_returns_the_highest_priority_pending_job(self):
        # Every job fixture is mid-flight, so queue two pending ones explicitly.
        base = fixture_named("job-spec", "job-video-proxy-resumed")
        for suffix, priority in (("1", 50), ("2", 900)):
            record = json.loads(json.dumps(base))
            record["job_id"] = suffix * 64
            record["state"]["status"] = "pending"
            record["priority"] = priority
            self.db.put_job(record)

        job = self.db.claim_next_job()
        self.assertIsNotNone(job)
        self.assertEqual("pending", job["state"]["status"])
        self.assertEqual(900, job["priority"], "interactive work outranks background sweeps")

    def test_claim_next_job_is_none_when_nothing_is_pending(self):
        self.assertIsNone(self.db.claim_next_job())

    def test_the_database_refuses_egress_without_consent(self):
        record = json.loads(json.dumps(fixture_named("job-spec", "tier3-with-consent")))
        record["egress"]["consent"] = None
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.put_job(record)


class TestPrefEvents(DatabaseTestCase):
    def test_every_valid_pref_fixture_round_trips(self):
        for record in fixtures_for("pref-event"):
            self.db.put_pref_event(record)
        for record in fixtures_for("pref-event"):
            with self.subTest(event_id=record["event_id"]):
                self.assertIn(record, self.db.pref_events())

    def test_the_database_refuses_an_event_carrying_pixels(self):
        record = json.loads(json.dumps(fixtures_for("pref-event")[0]))
        record["pixel_data_present"] = True
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.put_pref_event(record)

    def test_shareable_events_can_be_selected_for_export(self):
        for record in fixtures_for("pref-event"):
            self.db.put_pref_event(record)
        shareable = self.db.pref_events(shareable_only=True)
        self.assertTrue(shareable)
        for event in shareable:
            self.assertTrue(event["privacy"]["shareable_for_global_model"])
            self.assertFalse(event["privacy"]["contains_local_identifiers"])


class TestVectorIndex(DatabaseTestCase):
    def test_reports_which_backend_is_live(self):
        self.assertIn(self.db.vectors.backend, {"sqlite_vec", "brute_force"})

    def test_round_trips_a_vector(self):
        values = [0.1, -0.2, 0.3, 0.9]
        self.db.vectors.put("media", "a" * 64, "siglip2_so400m_1152", values)
        stored = self.db.vectors.get("media", "a" * 64, "siglip2_so400m_1152")
        for expected, actual in zip(values, stored):
            self.assertAlmostEqual(expected, actual, places=6)

    def test_nearest_returns_closest_first(self):
        space = "siglip2_so400m_1152"
        self.db.vectors.put("media", "a" * 64, space, [1.0, 0.0, 0.0])
        self.db.vectors.put("media", "b" * 64, space, [0.9, 0.1, 0.0])
        self.db.vectors.put("media", "c" * 64, space, [0.0, 1.0, 0.0])

        found = self.db.vectors.nearest(space, [1.0, 0.0, 0.0], k=3)
        self.assertEqual(["a" * 64, "b" * 64, "c" * 64], [n.owner_id for n in found])
        self.assertEqual(sorted(n.distance for n in found), [n.distance for n in found])

    def test_nearest_is_deterministic_on_ties(self):
        space = "siglip2_so400m_1152"
        for owner in ("c" * 64, "a" * 64, "b" * 64):
            self.db.vectors.put("media", owner, space, [1.0, 0.0])
        first = [n.owner_id for n in self.db.vectors.nearest(space, [1.0, 0.0], k=3)]
        second = [n.owner_id for n in self.db.vectors.nearest(space, [1.0, 0.0], k=3)]
        self.assertEqual(first, second)
        self.assertEqual(sorted(first), first, "ties break by id, so plans stay reproducible")

    def test_restrict_to_prefilters_the_search(self):
        space = "siglip2_so400m_1152"
        self.db.vectors.put("media", "a" * 64, space, [1.0, 0.0])
        self.db.vectors.put("media", "b" * 64, space, [0.99, 0.01])
        found = self.db.vectors.nearest(space, [1.0, 0.0], k=5, restrict_to={"b" * 64})
        self.assertEqual(["b" * 64], [n.owner_id for n in found])

    def test_dimension_mismatch_is_an_error_not_a_wrong_answer(self):
        space = "siglip2_so400m_1152"
        self.db.vectors.put("media", "a" * 64, space, [1.0, 0.0, 0.0])
        with self.assertRaises(Exception):
            self.db.vectors.nearest(space, [1.0, 0.0], k=1)

    def test_spaces_do_not_mix(self):
        self.db.vectors.put("media", "a" * 64, "siglip2_so400m_1152", [1.0, 0.0])
        self.db.vectors.put("media", "a" * 64, "arcface_buffalo_l_512", [0.0, 1.0])
        found = self.db.vectors.nearest("arcface_buffalo_l_512", [0.0, 1.0], k=5)
        self.assertEqual(1, len(found))
        self.assertEqual(2, self.db.vectors.count())

    def test_cosine_distance_matches_hand_computed_values(self):
        self.assertAlmostEqual(0.0, cosine_distance([1, 0], [1, 0]), places=9)
        self.assertAlmostEqual(1.0, cosine_distance([1, 0], [0, 1]), places=9)
        self.assertAlmostEqual(2.0, cosine_distance([1, 0], [-1, 0]), places=9)

    def test_similar_media_excludes_the_query_itself(self):
        self.load_media()
        records = fixtures_for("media-record")[:3]
        space = "siglip2_so400m_1152"
        for index, record in enumerate(records):
            self.db.vectors.put("media", record["media_id"], space, [1.0, index * 0.1])
        found = self.db.similar_media(records[0]["media_id"], space, k=5)
        self.assertNotIn(records[0]["media_id"], [n.owner_id for n in found])


class TestDedupe(DatabaseTestCase):
    def test_only_primaries_are_returned_when_asked(self):
        self.load_media()
        record = fixture_named("media-record", "image-beach-sunset")
        self.assertTrue(record["dedupe"]["is_primary"])

        secondary = json.loads(json.dumps(record))
        secondary["media_id"] = "f" * 64
        secondary["dedupe"]["is_primary"] = False
        secondary["dedupe"]["primary_media_id"] = record["media_id"]
        self.db.put_media(secondary)

        primaries = {m.media_id for m in self.db.list_media(primaries_only=True, limit=500)}
        self.assertIn(record["media_id"], primaries)
        self.assertNotIn(
            secondary["media_id"],
            primaries,
            "a burst of near-identical frames must contribute one photo, not twelve",
        )


if __name__ == "__main__":
    unittest.main()


class TestProxyResolution(DatabaseTestCase):
    """The only lookup the inference path may use.

    Codex flagged that ml_runtime.proto promises the host resolves `proxy_id`
    through media-db, while media-db only had `resolve_path` -- which returns an
    ORIGINAL. Giving the inference path a resolver that can return an original
    defeats the structural guarantee that analysis never touches source files.
    """

    def test_a_proxy_id_resolves_to_its_proxy(self):
        record = fixture_named("media-record", "image-beach-sunset")
        self.db.put_media(record)
        proxy_id = record["proxies"][0]["proxy_id"]
        resolved = self.db.resolve_proxy(proxy_id)
        self.assertIsNotNone(resolved)
        self.assertEqual(proxy_id, resolved["proxy_id"])
        self.assertEqual("thumbnail_512", resolved["kind"])

    def test_a_media_id_does_not_resolve_as_a_proxy(self):
        """The guarantee, enforced: passing an original's id here gets nothing."""
        record = fixture_named("media-record", "image-beach-sunset")
        self.db.put_media(record)
        self.assertIsNone(
            self.db.resolve_proxy(record["media_id"]),
            "an original must never be reachable through the proxy resolver",
        )

    def test_an_unknown_id_resolves_to_none(self):
        self.assertIsNone(self.db.resolve_proxy("0" * 64))

    def test_proxies_can_be_listed_and_filtered_by_kind(self):
        self.load_media()
        chapter = fixture_named("media-record", "video-gopro-chapter-01")
        proxies = self.db.proxies_for_media(chapter["media_id"])
        self.assertTrue(proxies)
        video = self.db.proxies_for_media(chapter["media_id"], kind="video_proxy_480p")
        self.assertEqual(1, len(video))
        self.assertIsNotNone(
            video[0].get("frame_index"),
            "the video proxy must carry its frame-index sidecar, or proxy time "
            "cannot be mapped back to source timecode",
        )

    def test_an_assembly_has_no_proxies_of_its_own(self):
        self.load_media()
        assembly = fixture_named("media-record", "span-assembly")
        self.assertEqual([], self.db.proxies_for_media(assembly["media_id"]))


# --------------------------------------------------------------- issue #32 ---

LEGACY_RESOLVE_SQL = (
    "SELECT record_json FROM media WHERE record_json LIKE '%' || ? || '%' LIMIT 50"
)
INDEXED_RESOLVE_SQL = (
    "SELECT proxy_json FROM media_proxy WHERE proxy_id = ? ORDER BY media_id LIMIT 1"
)


def legacy_resolve_proxy(connection: sqlite3.Connection, proxy_id: str) -> dict | None:
    """`resolve_proxy` exactly as it was before migration 0002.

    Kept here, in the tests, so the regression is demonstrated rather than
    asserted: every test below that says "this would have failed" runs this and
    watches it fail.
    """
    for row in connection.execute(LEGACY_RESOLVE_SQL, (proxy_id,)):
        record = json.loads(row["record_json"])
        for proxy in record.get("proxies") or []:
            if proxy.get("proxy_id") == proxy_id:
                return proxy
    return None


def vm_steps(connection: sqlite3.Connection, sql: str, params: tuple) -> int:
    """How much work SQLite does to answer a query, in units of 10 VM steps.

    Wall-clock timing is the obvious way to test a performance fix and it is
    the wrong one: it is flaky under CI load and it measures the machine as
    much as the query. SQLite's progress handler fires every N virtual-machine
    instructions, so counting invocations is a deterministic measure of work
    done -- it returns the same number on a busy laptop and an idle one.
    """
    count = [0]

    def handler() -> int:
        count[0] += 1
        return 0

    connection.set_progress_handler(handler, 10)
    try:
        connection.execute(sql, params).fetchall()
    finally:
        connection.set_progress_handler(None, 0)
    return count[0]


class TestProxyLookupIsIndexed(unittest.TestCase):
    """Issue #32: `resolve_proxy` was an unindexed JSON substring scan.

    Two defects in one query, and the slower one was not the worse one:

      * COST. `record_json LIKE '%id%'` cannot use an index, so every proxy
        inference read and substring-matched every MediaRecord in the library.
        One lookup per analysed item makes the analysis pass quadratic in
        library size, against a 100k-record responsiveness gate.

      * CORRECTNESS. The substring matches any record whose JSON mentions the
        id anywhere, and `LIMIT 50` then truncates. Past fifty incidental
        mentions the owning record can fall outside the candidate set and the
        resolver returns None -- surfacing as PROXY_NOT_FOUND, which reads as
        "no such proxy" rather than "the query gave up". It never raised, and
        it got worse as the library grew.
    """

    def setUp(self) -> None:
        self.db = Database.open(":memory:")
        self.addCleanup(self.db.close)
        self.record = fixture_named("media-record", "image-beach-sunset")
        self.proxy_id = self.record["proxies"][0]["proxy_id"]

    def _add_mentioning_decoys(self, count: int) -> None:
        """Records that MENTION the proxy id without owning it.

        Not contrived: `dedupe.group_id`, `span_id`, provenance fields and
        proxy paths are all content-addressed hex in the same alphabet, and the
        substring match cannot tell one from another.
        """
        for index in range(count):
            decoy = json.loads(json.dumps(self.record))
            decoy["media_id"] = "%064x" % index
            decoy["proxies"] = []
            decoy["dedupe"] = {"group_id": self.proxy_id, "is_primary": False}
            self.db.put_media(decoy)

    def _add_unrelated(self, count: int) -> None:
        """Records that do not mention the id at all -- an ordinary library."""
        raw = json.dumps(self.record)
        for index in range(count):
            replacement = "%064x" % (10 ** 6 + index)
            decoy = json.loads(
                raw.replace(self.proxy_id, replacement).replace(
                    self.record["media_id"], "%064x" % index
                )
            )
            self.assertNotIn(self.proxy_id, json.dumps(decoy))
            self.db.put_media(decoy)

    def test_fifty_incidental_mentions_hid_a_proxy_that_exists(self):
        """The correctness half, and the reason this is a defect not a chore.

        The decoys are written first so the owning record falls outside the
        fifty rows the scan reads. Which side of the limit it lands on is
        insertion order -- i.e. the order the user happened to import their
        files in -- so the old resolver did not fail predictably. It failed for
        some proxies and not others, in a way no run could reproduce.
        """
        self._add_mentioning_decoys(60)
        self.db.put_media(self.record)

        self.assertIsNone(
            legacy_resolve_proxy(self.db.connection, self.proxy_id),
            "the LIMIT 50 substring scan is expected to miss here -- if it "
            "finds the proxy, this test is no longer reproducing the defect",
        )
        resolved = self.db.resolve_proxy(self.proxy_id)
        self.assertIsNotNone(resolved)
        self.assertEqual(self.proxy_id, resolved["proxy_id"])

    def test_the_lookup_uses_an_index_and_does_not_touch_the_media_table(self):
        self.db.put_media(self.record)
        plan = " | ".join(
            str(row[3])
            for row in self.db.connection.execute(
                "EXPLAIN QUERY PLAN " + INDEXED_RESOLVE_SQL, (self.proxy_id,)
            )
        )
        self.assertIn("SEARCH media_proxy", plan)
        self.assertNotIn("SCAN", plan)

        legacy_plan = " | ".join(
            str(row[3])
            for row in self.db.connection.execute(
                "EXPLAIN QUERY PLAN " + LEGACY_RESOLVE_SQL, (self.proxy_id,)
            )
        )
        self.assertIn(
            "SCAN media", legacy_plan,
            "the old query is expected to plan as a full scan; if it no longer "
            "does, this test is measuring something else",
        )

    def test_lookup_cost_does_not_grow_with_the_library(self):
        """The scaling half. Measured in VM steps, not seconds."""
        self.db.put_media(self.record)
        self._add_unrelated(200)
        small_new = vm_steps(self.db.connection, INDEXED_RESOLVE_SQL, (self.proxy_id,))
        small_old = vm_steps(self.db.connection, LEGACY_RESOLVE_SQL, (self.proxy_id,))

        self._add_unrelated(4000)
        large_new = vm_steps(self.db.connection, INDEXED_RESOLVE_SQL, (self.proxy_id,))
        large_old = vm_steps(self.db.connection, LEGACY_RESOLVE_SQL, (self.proxy_id,))

        self.assertGreater(
            large_old, small_old * 5,
            "the old query is expected to cost proportionally more on a bigger "
            "library; if it does not, the fixture is not exercising the scan",
        )
        self.assertLessEqual(
            large_new, small_new + 2,
            f"a twentyfold library grew the indexed lookup from {small_new} to "
            f"{large_new} -- it is not using the index",
        )

    def test_an_existing_database_is_backfilled_when_it_migrates(self):
        """A user upgrading does not have to re-scan their library.

        Exercised by removing the table from a populated database and
        re-migrating, which runs the real backfill against real records rather
        than against rows invented for the test.
        """
        records = [self.record, fixture_named("media-record", "video-gopro-chapter-01")]
        for record in records:
            self.db.put_media(record)

        # Undo everything migrations 2 and 3 added, not just the proxy table.
        # Setting user_version back while leaving their columns in place would
        # not be a version-1 database, and would hide a migration that cannot
        # actually run against one.
        self.db.connection.execute("DROP TABLE media_proxy")
        self.db.connection.execute("DROP INDEX IF EXISTS media_phash_idx")
        self.db.connection.execute("ALTER TABLE media DROP COLUMN phash_algorithm")
        self.db.connection.execute("CREATE INDEX media_phash_idx ON media (phash_hex)")
        self.db.connection.execute("PRAGMA user_version = 1")
        self.assertEqual(1, current_version(self.db.connection))

        self.assertEqual(SCHEMA_VERSION, migrate(self.db.connection))
        for record in records:
            for proxy in record["proxies"]:
                with self.subTest(proxy=proxy["proxy_id"][:12]):
                    resolved = self.db.resolve_proxy(proxy["proxy_id"])
                    self.assertIsNotNone(resolved)
                    self.assertEqual(proxy["kind"], resolved["kind"])

        # The pHash algorithm is backfilled from the records themselves, so a
        # library scanned before the column existed does not have to be
        # re-scanned to become comparable again (issue #14).
        for record in records:
            expected = ((record.get("perceptual") or {}).get("image_hash") or {}).get(
                "algorithm"
            )
            with self.subTest(record=record["media_id"][:12]):
                stored = self.db.connection.execute(
                    "SELECT phash_algorithm FROM media WHERE media_id = ?",
                    (record["media_id"],),
                ).fetchone()[0]
                self.assertEqual(expected, stored)

    def test_a_proxy_removed_from_its_record_stops_resolving(self):
        """The index is derived data; it may not outlive what it describes."""
        self.db.put_media(self.record)
        self.assertIsNotNone(self.db.resolve_proxy(self.proxy_id))

        without = json.loads(json.dumps(self.record))
        without["proxies"] = []
        self.db.put_media(without)
        self.assertIsNone(
            self.db.resolve_proxy(self.proxy_id),
            "a stale index row would send the runtime to a proxy file the "
            "record no longer claims exists",
        )

    def test_deleting_a_record_takes_its_proxy_rows(self):
        self.db.put_media(self.record)
        self.db.connection.execute(
            "DELETE FROM media WHERE media_id = ?", (self.record["media_id"],)
        )
        self.db.connection.commit()
        self.assertIsNone(self.db.resolve_proxy(self.proxy_id))

    def test_a_proxy_shared_by_two_records_survives_deleting_one(self):
        """Why the key is (proxy_id, media_id) and not proxy_id alone.

        Proxy ids are BLAKE3 of the proxy BYTES, so two records can share one
        (a JPEG and its HEIC twin reduce to the same 512px thumbnail). Keyed on
        proxy_id alone, the second write would replace the first's row and
        deleting the second record would cascade away a proxy the first still
        lists -- the same silent miss, just rarer.
        """
        self.db.put_media(self.record)
        twin = json.loads(json.dumps(self.record))
        twin["media_id"] = "f" * 64
        self.db.put_media(twin)

        self.assertEqual(
            2,
            self.db.connection.execute(
                "SELECT count(*) FROM media_proxy WHERE proxy_id = ?", (self.proxy_id,)
            ).fetchone()[0],
        )

        self.db.connection.execute("DELETE FROM media WHERE media_id = ?", (twin["media_id"],))
        self.db.connection.commit()
        self.assertIsNotNone(
            self.db.resolve_proxy(self.proxy_id),
            "deleting one owner must not unindex a proxy the other still has",
        )

    def test_resolution_is_the_same_choice_on_every_run(self):
        """Determinism where the answer is genuinely ambiguous."""
        self.db.put_media(self.record)
        twin = json.loads(json.dumps(self.record))
        twin["media_id"] = "0" * 63 + "1"
        self.db.put_media(twin)
        self.assertEqual(twin["media_id"], self.db.media_id_for_proxy(self.proxy_id))
        self.assertEqual(
            [self.db.resolve_proxy(self.proxy_id)] * 4,
            [self.db.resolve_proxy(self.proxy_id) for _ in range(4)],
        )

    def test_the_resolver_still_refuses_a_media_id(self):
        """The structural guarantee the whole method exists for, re-checked
        against the new implementation rather than assumed to have survived it."""
        self.db.put_media(self.record)
        self.assertIsNone(self.db.resolve_proxy(self.record["media_id"]))
        self.assertIsNone(self.db.media_id_for_proxy(self.record["media_id"]))
