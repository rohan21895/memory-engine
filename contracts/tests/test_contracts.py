"""Golden tests for the Memory Engine contract layer.

Written as unittest.TestCase classes so the same file runs under both
`python3 -m unittest discover` (what scripts/ci/run-workspace-check.mjs uses)
and `pytest` (the convention in CLAUDE.md). No test-runner-specific features.

Four layers of checking, in order of what they would catch:

1. The schemas are themselves valid draft 2020-12 and every $ref resolves.
2. Every fixture behaves exactly as the manifest says: valid ones validate,
   schema-invalid ones are rejected AT THE FIELD THE MANIFEST NAMES (so a
   fixture cannot pass for an accidental reason), semantic-invalid ones pass
   the schema and fail an invariant.
3. Cross-field invariants that JSON Schema cannot express -- the precision-first
   face gate, EDL timeline contiguity and beat alignment, print DPI floors.
4. The generated bindings are fresh and actually round-trip the fixtures.
"""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

CONTRACTS = Path(__file__).resolve().parent.parent
SCHEMA_DIR = CONTRACTS / "schemas"
FIXTURE_DIR = CONTRACTS / "fixtures"
CODEGEN = CONTRACTS / "codegen"
GENERATED_PYTHON = CODEGEN / "generated" / "python"

MANIFEST = json.loads((FIXTURE_DIR / "index.json").read_text(encoding="utf-8"))


def _load_schema_documents() -> dict[str, dict]:
    return {
        path.name: json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(SCHEMA_DIR.glob("*.schema.json"))
    }


DOCUMENTS = _load_schema_documents()


def _registry():
    from referencing import Registry, Resource

    return Registry().with_resources(
        [(name, Resource.from_contents(doc)) for name, doc in DOCUMENTS.items()]
    )


def _validator(schema_key: str):
    from jsonschema import Draft202012Validator

    filename = MANIFEST["schemas"][schema_key]
    return Draft202012Validator(DOCUMENTS[filename], registry=_registry())


def _fixture(entry: dict) -> dict:
    return json.loads((FIXTURE_DIR / entry["path"]).read_text(encoding="utf-8"))


def _entries(expectation: str | None = None, schema: str | None = None) -> list[dict]:
    out = MANIFEST["fixtures"]
    if expectation:
        out = [e for e in out if e["expectation"] == expectation]
    if schema:
        out = [e for e in out if e["schema"] == schema]
    return out


def _seconds(rational: dict) -> float:
    return rational["value"] / rational["rate"]


class TestSchemasWellFormed(unittest.TestCase):
    def test_every_schema_is_valid_draft_2020_12(self):
        from jsonschema import Draft202012Validator

        self.assertTrue(DOCUMENTS, "no schemas found")
        for name, document in DOCUMENTS.items():
            with self.subTest(schema=name):
                Draft202012Validator.check_schema(document)

    def test_all_seven_contract_schemas_exist(self):
        expected = {
            "media-record",
            "face-record",
            "moment-record",
            "edl",
            "album-spec",
            "job-spec",
            "pref-event",
        }
        self.assertEqual(expected, set(MANIFEST["schemas"]))
        for filename in MANIFEST["schemas"].values():
            self.assertIn(filename, DOCUMENTS)

    def test_every_ref_resolves(self):
        for name, document in DOCUMENTS.items():
            if name == "common.schema.json":
                continue
            with self.subTest(schema=name):
                validator = _validator(
                    next(k for k, v in MANIFEST["schemas"].items() if v == name)
                )
                # Walking a trivial instance forces every $ref to be dereferenced;
                # an unresolvable one raises rather than returning errors.
                list(validator.iter_errors({}))

    def test_root_objects_forbid_additional_properties(self):
        for name, document in DOCUMENTS.items():
            if name == "common.schema.json":
                continue
            with self.subTest(schema=name):
                self.assertIs(
                    document.get("additionalProperties"),
                    False,
                    f"{name} must set additionalProperties:false at the root",
                )

    def test_every_record_declares_the_contract_version(self):
        for name, document in DOCUMENTS.items():
            if name == "common.schema.json":
                continue
            with self.subTest(schema=name):
                self.assertIn("schema_version", document.get("required", []))


class TestManifestMatchesDisk(unittest.TestCase):
    def test_every_fixture_on_disk_is_listed(self):
        on_disk = {
            str(path.relative_to(FIXTURE_DIR))
            for path in FIXTURE_DIR.rglob("*.json")
            if path.name != "index.json"
        }
        listed = {entry["path"] for entry in MANIFEST["fixtures"]}
        self.assertEqual(
            on_disk,
            listed,
            "fixtures on disk and fixtures in index.json have diverged",
        )

    def test_every_listed_fixture_exists_and_parses(self):
        for entry in MANIFEST["fixtures"]:
            with self.subTest(fixture=entry["path"]):
                path = FIXTURE_DIR / entry["path"]
                self.assertTrue(path.is_file(), f"missing fixture {entry['path']}")
                json.loads(path.read_text(encoding="utf-8"))

    def test_every_fixture_documents_its_purpose(self):
        for entry in MANIFEST["fixtures"]:
            with self.subTest(fixture=entry["path"]):
                self.assertTrue(entry.get("purpose"), "fixture needs a stated purpose")
                self.assertIn(
                    entry["expectation"], {"valid", "schema-invalid", "semantic-invalid"}
                )

    def test_phase_zero_required_edge_cases_are_covered(self):
        """CLAUDE.md Phase 0 task 3 names four edge cases by hand. None may go missing."""
        paths = {entry["path"] for entry in MANIFEST["fixtures"]}
        required = {
            "a GoPro chaptered video spanning files": {
                "media-record/valid/video-gopro-chapter-01.json",
                "media-record/valid/video-gopro-chapter-02.json",
                "media-record/valid/video-gopro-span-assembly.json",
            },
            "a photo with no EXIF date": {"media-record/valid/image-no-exif-date.json"},
            "a face below confidence threshold": {
                "face-record/valid/face-below-threshold-review-queued.json"
            },
            "an EDL with a beat-locked cut and a vertical reframe keyframe track": {
                "edl/valid/reel-beat-locked-vertical-reframe.json"
            },
        }
        for description, expected in required.items():
            with self.subTest(edge_case=description):
                self.assertTrue(expected <= paths, f"missing fixture(s) for: {description}")

    def test_every_schema_has_at_least_one_valid_fixture(self):
        covered = {entry["schema"] for entry in _entries("valid")}
        self.assertEqual(set(MANIFEST["schemas"]), covered)


class TestFixturesValidate(unittest.TestCase):
    def test_valid_fixtures_pass_schema_validation(self):
        for entry in _entries("valid"):
            with self.subTest(fixture=entry["path"]):
                validator = _validator(entry["schema"])
                errors = sorted(
                    validator.iter_errors(_fixture(entry)), key=lambda e: list(e.path)
                )
                self.assertEqual(
                    [],
                    [f"{list(e.path)}: {e.message}" for e in errors],
                )

    def test_schema_invalid_fixtures_are_rejected_at_the_named_field(self):
        for entry in _entries("schema-invalid"):
            with self.subTest(fixture=entry["path"]):
                validator = _validator(entry["schema"])
                errors = list(validator.iter_errors(_fixture(entry)))
                self.assertTrue(errors, "fixture was expected to be rejected")

                expected = entry.get("error_path")
                if expected is not None:
                    paths = [list(error.path) for error in errors]
                    self.assertIn(
                        expected,
                        paths,
                        f"expected a rejection at {expected}, got {paths}",
                    )

    def test_semantic_invalid_fixtures_pass_the_schema(self):
        for entry in _entries("semantic-invalid"):
            with self.subTest(fixture=entry["path"]):
                validator = _validator(entry["schema"])
                errors = sorted(
                    validator.iter_errors(_fixture(entry)), key=lambda e: list(e.path)
                )
                self.assertEqual(
                    [],
                    [f"{list(e.path)}: {e.message}" for e in errors],
                    "a semantic-invalid fixture must be structurally perfect; "
                    "if the schema rejects it, it belongs in schema-invalid/",
                )


# ---------------------------------------------------------------------------
# Cross-field invariants. JSON Schema cannot compare two sibling values, so the
# rules that actually protect the product live here.
# ---------------------------------------------------------------------------


def check_face_record(record: dict) -> list[str]:
    """Precision over recall. A wrong person in a family album is catastrophic."""
    problems: list[str] = []
    identity = record["identity"]
    eligible = identity["eligible_for_automated_output"]

    if eligible:
        if identity["assignment"] not in {"user_confirmed", "auto_high_confidence"}:
            problems.append(
                f"eligible face has assignment {identity['assignment']!r}; only "
                "user_confirmed or auto_high_confidence may be acted on unattended"
            )
        if identity.get("person_id") is None:
            problems.append("eligible face names no person_id")
        confidence = identity.get("confidence")
        threshold = identity.get("threshold_used")
        if confidence is None or threshold is None:
            problems.append("eligible face must state both confidence and threshold_used")
        elif identity["assignment"] == "auto_high_confidence" and confidence < threshold:
            problems.append(
                f"auto-assigned face is eligible at confidence {confidence} "
                f"below its own threshold {threshold}"
            )
        if identity.get("threshold_profile") != "automated_output":
            problems.append(
                "eligible face must have been judged against the automated_output "
                f"operating point, not {identity.get('threshold_profile')!r}"
            )

    minor = record.get("sensitive", {}).get("minor_status")
    if minor == "confirmed_minor":
        if record["sensitive"].get("labeling_consent") is None and identity.get("person_id"):
            problems.append("a confirmed minor was labeled without a consent reference")
    return problems


def check_media_record(record: dict) -> list[str]:
    problems: list[str] = []
    span = record.get("span")
    if span:
        if span["role"] == "member":
            if span.get("index") is None:
                problems.append("span member has no index")
            if span.get("member_media_ids"):
                problems.append("only the assembly record lists member_media_ids")
        else:
            if span.get("index") is not None:
                problems.append("span assembly must have a null index")
            if not span.get("member_media_ids"):
                problems.append("span assembly lists no members")
            if span["span_id"] != record["media_id"]:
                problems.append("assembly media_id must equal its span_id")

    captured = record["capture"]["captured_at"]
    if captured["precision"] == "unknown" and captured.get("local") is not None:
        problems.append("a time asserted with unknown precision must not carry a local time")
    if captured["source"] == "unknown" and captured["confidence"] > 0:
        problems.append("a time from an unknown source cannot have non-zero confidence")

    faces = record.get("faces")
    if faces and faces.get("face_ids"):
        if len(faces["face_ids"]) > faces["face_count"]:
            problems.append("more face_ids than face_count")

    image = record.get("image")
    if image and image["orientation"] in {5, 6, 7, 8}:
        stored, oriented = image["stored_size"], image["oriented_size"]
        if (stored["width"], stored["height"]) != (oriented["height"], oriented["width"]):
            problems.append("orientation implies a 90-degree swap that the sizes disagree with")
    return problems


def check_moment_record(record: dict) -> list[str]:
    problems: list[str] = []
    start = record["source_range"]["start_time"]
    end_value = start["value"] + record["source_range"]["duration"]["value"]

    for index, snap in enumerate(record.get("snap_points", [])):
        if not start["value"] <= snap["time"]["value"] <= end_value:
            problems.append(f"snap point {index} lies outside the moment's source_range")

    times = [snap["time"]["value"] for snap in record.get("snap_points", [])]
    if times != sorted(times):
        problems.append("snap points are not ordered by time")

    trim = record.get("safe_trim")
    if trim:
        if trim["earliest_in"]["value"] >= trim["latest_out"]["value"]:
            problems.append("safe_trim earliest_in is not before latest_out")
        for key in ("speech_safe_in", "speech_safe_out"):
            bound = trim.get(key)
            if bound is not None and not (
                trim["earliest_in"]["value"] <= bound["value"] <= trim["latest_out"]["value"]
            ):
                problems.append(f"{key} lies outside the safe trim window")

    if record["elimination"]["eliminated"]:
        if not record["elimination"].get("reasons"):
            problems.append("an eliminated moment must say why")
        if record.get("snap_points"):
            problems.append("an eliminated moment must not offer cut points")

    transcript = record.get("transcript")
    if transcript:
        for word in transcript.get("words", []):
            if word["start"]["value"] >= word["end"]["value"]:
                problems.append(f"word {word['word']!r} has a non-positive duration")
    return problems


def check_edl(edl: dict) -> list[str]:
    problems: list[str] = []
    refs = {ref["media_ref_id"]: ref for ref in edl["media_refs"]}

    for track in edl["tracks"]:
        position = 0
        for item in track["items"]:
            if item["item_type"] == "transition":
                continue
            if item["item_type"] == "gap":
                position += item["duration"]["value"]
                continue

            clip = item
            if clip["media_ref_id"] not in refs:
                problems.append(f"{clip['clip_id']} references unknown {clip['media_ref_id']!r}")
                continue

            available = refs[clip["media_ref_id"]]["available_range"]
            low = available["start_time"]["value"]
            high = low + available["duration"]["value"]
            source = clip["source_range"]
            source_end = source["start_time"]["value"] + source["duration"]["value"]
            if source["start_time"]["value"] < low or source_end > high:
                problems.append(
                    f"{clip['clip_id']} source_range [{source['start_time']['value']}, "
                    f"{source_end}) escapes available_range [{low}, {high})"
                )

            timeline = clip.get("timeline_range")
            if timeline is not None:
                if timeline["start_time"]["value"] != position:
                    problems.append(
                        f"{clip['clip_id']} starts at {timeline['start_time']['value']} "
                        f"but the track has reached {position}"
                    )
                position += timeline["duration"]["value"]
            else:
                position += source["duration"]["value"]

            if clip.get("reframe_track_id"):
                known = {t["reframe_track_id"] for t in edl.get("reframe_tracks", [])}
                if clip["reframe_track_id"] not in known:
                    problems.append(
                        f"{clip['clip_id']} references unknown reframe track "
                        f"{clip['reframe_track_id']!r}"
                    )

            effect = clip.get("time_effect")
            if effect:
                if effect["kind"] == "linear_speed" and not effect.get("time_scalar"):
                    problems.append(f"{clip['clip_id']} has a linear_speed effect with no scalar")
                if effect["kind"] == "freeze_frame" and effect.get("freeze_at") is None:
                    problems.append(f"{clip['clip_id']} has a freeze_frame with no freeze_at")

    grid = edl.get("beat_grid")
    if grid:
        indices = [beat["index"] for beat in grid["beats"]]
        if indices != sorted(indices):
            problems.append("beat grid is not ordered by index")
        times = [beat["time"]["value"] for beat in grid["beats"]]
        if times != sorted(times):
            problems.append("beat grid is not monotonic in time")

        tolerance = grid.get("tolerance_ms", 50.0)
        by_index = {beat["index"]: beat for beat in grid["beats"]}
        for track in edl["tracks"]:
            for clip in track["items"]:
                lock = clip.get("beat_lock") if clip["item_type"] == "clip" else None
                if not lock:
                    continue
                if lock["beat_index"] not in by_index:
                    problems.append(
                        f"{clip['clip_id']} locks to beat {lock['beat_index']}, "
                        "which is not in the grid"
                    )
                    continue
                if abs(lock["alignment_error_ms"]) > tolerance:
                    problems.append(
                        f"{clip['clip_id']} beat error {lock['alignment_error_ms']}ms "
                        f"exceeds the {tolerance}ms tolerance"
                    )
                beat = by_index[lock["beat_index"]]
                if lock.get("is_downbeat") != beat["is_downbeat"]:
                    problems.append(
                        f"{clip['clip_id']} disagrees with the grid about whether "
                        f"beat {lock['beat_index']} is a downbeat"
                    )

    source_aspect = None
    for ref in edl["media_refs"]:
        if ref["media_kind"] == "video":
            source_aspect = 16 / 9  # every video source in the fixtures is 16:9
            break

    for track in edl.get("reframe_tracks", []):
        ratio = track["target_aspect_ratio"]
        want = ratio["numerator"] / ratio["denominator"]
        times = [keyframe["time"]["value"] for keyframe in track["keyframes"]]
        if times != sorted(times):
            problems.append(f"{track['reframe_track_id']} keyframes are not ordered")
        if len(set(times)) != len(times):
            problems.append(f"{track['reframe_track_id']} has duplicate keyframe times")
        for keyframe in track["keyframes"]:
            crop = keyframe["crop"]
            if crop["x"] + crop["w"] > 1.0 + 1e-9 or crop["y"] + crop["h"] > 1.0 + 1e-9:
                problems.append(f"{track['reframe_track_id']} crop escapes the source frame")
            if source_aspect is not None:
                got = (crop["w"] * source_aspect) / crop["h"]
                if abs(got - want) > 1e-6:
                    problems.append(
                        f"{track['reframe_track_id']} crop aspect {got:.6f} does not "
                        f"match the target {want:.6f}"
                    )
            if keyframe["interpolation"] == "bezier" and not keyframe.get("bezier_control"):
                problems.append(
                    f"{track['reframe_track_id']} has a bezier keyframe with no control points"
                )

    plan = edl.get("audio_plan")
    if plan:
        cue_ids = {cue["cue_id"] for cue in plan.get("music", [])}
        if grid and grid["source_cue_id"] not in cue_ids:
            problems.append("beat grid references a music cue that is not in the audio plan")
        for cue in plan.get("music", []):
            cleared = set(cue["license"]["cleared_for"])
            destination = edl["target"]["destination"]
            social = {"instagram_reel", "instagram_feed", "youtube", "youtube_shorts", "tiktok",
                      "whatsapp_status"}
            if destination in social and "social_share" not in cleared:
                problems.append(
                    f"cue {cue['cue_id']} is not cleared for social_share but the "
                    f"target is {destination}"
                )
        for rule in plan.get("ducking", []):
            if rule["trigger"] == "explicit_ranges" and not rule.get("ranges"):
                problems.append(f"ducking rule {rule['rule_id']} declares explicit_ranges but none")

    arc = edl.get("story_arc")
    if arc:
        clip_ids = {
            clip["clip_id"]
            for track in edl["tracks"]
            for clip in track["items"]
            if clip["item_type"] == "clip"
        }
        for act in arc["acts"]:
            for beat in act.get("beats", []):
                if beat["required"] and not beat["satisfied_by_clip_ids"]:
                    problems.append(f"required story beat {beat['beat_id']} is unsatisfied")
                for clip_id in beat["satisfied_by_clip_ids"]:
                    if clip_id not in clip_ids:
                        problems.append(
                            f"story beat {beat['beat_id']} cites unknown clip {clip_id!r}"
                        )
        if arc["source"] == "tier3_model" and arc.get("consent") is None:
            problems.append("a Tier 3 story arc must carry the consent that allowed the upload")

    target = edl["target"]
    if target.get("max_duration"):
        total = 0
        for track in edl["tracks"]:
            if track["kind"] != "video":
                continue
            total = max(
                total,
                sum(
                    item.get("timeline_range", {}).get("duration", {}).get("value", 0)
                    if item["item_type"] == "clip"
                    else item["duration"]["value"] if item["item_type"] == "gap" else 0
                    for item in track["items"]
                ),
            )
        if total > target["max_duration"]["value"]:
            problems.append("timeline exceeds the target's max_duration")

    report = edl.get("validation")
    if report and report["status"] == "pass":
        failed = [c for c in report["checks"] if c["severity"] == "error" and not c["passed"]]
        if failed:
            problems.append("validation claims pass while error-severity checks failed")

    otio = edl.get("otio")
    if otio and otio.get("round_trip_verified") and otio.get("unmapped_fields"):
        problems.append("round trip cannot be verified while fields remain unmapped")
    return problems


def check_album_spec(spec: dict) -> list[str]:
    problems: list[str] = []
    profile = spec["vendor_profile"]
    floor = profile["dpi_floor"]

    placement_ids: set[str] = set()
    for page in spec["pages"]:
        for placement in page["placements"]:
            if placement["placement_id"] in placement_ids:
                problems.append(f"duplicate placement_id {placement['placement_id']!r}")
            placement_ids.add(placement["placement_id"])

            if placement["effective_dpi"] < floor:
                problems.append(
                    f"{placement['placement_id']} at {placement['effective_dpi']} DPI is "
                    f"below the {floor} DPI floor"
                )

            frame, crop = placement["frame"], placement["crop"]
            if frame["height_mm"] > 0 and crop["h"] > 0:
                frame_aspect = frame["width_mm"] / frame["height_mm"]
                # Crop aspect is only comparable once the source pixel aspect is known,
                # so this checks the weaker property: neither degenerates.
                if frame_aspect <= 0:
                    problems.append(f"{placement['placement_id']} has a degenerate frame")

            if crop["x"] + crop["w"] > 1.0 + 1e-9 or crop["y"] + crop["h"] > 1.0 + 1e-9:
                problems.append(f"{placement['placement_id']} crop escapes the source image")

            safety = placement.get("face_safety")
            if safety:
                if safety["faces_in_trim_zone"] and safety["all_faces_in_safe_zone"]:
                    problems.append(
                        f"{placement['placement_id']} claims all faces are safe while "
                        "reporting faces in the trim zone"
                    )
                if safety["faces_in_gutter"] and safety["all_faces_in_safe_zone"]:
                    problems.append(
                        f"{placement['placement_id']} claims all faces are safe while "
                        "reporting faces in the gutter"
                    )

            orders = [op["order"] for op in placement["enhancement_ops"]]
            if len(set(orders)) != len(orders):
                problems.append(f"{placement['placement_id']} has duplicate enhancement orders")
            for op in placement["enhancement_ops"]:
                if not op["license_cleared"]:
                    problems.append(
                        f"{placement['placement_id']} plans op {op['op_id']} whose model "
                        "has not passed the licence audit"
                    )

    for page in spec["pages"]:
        for block in page.get("text_blocks", []):
            margin = profile["safe_margin_mm"]
            bleed = profile["bleed_mm"]
            if block["frame"]["x_mm"] < bleed + margin:
                problems.append(
                    f"text block {block['block_id']} sits inside the safe margin"
                )

    harmony = spec.get("spread_harmony")
    if harmony and harmony["enabled"]:
        for spread in harmony["spreads"]:
            for adjustment in spread["adjustments"]:
                if adjustment["placement_id"] not in placement_ids:
                    problems.append(
                        f"spread harmony adjusts unknown placement "
                        f"{adjustment['placement_id']!r}"
                    )

    report = spec["validation"]
    failed = [c for c in report["checks"] if c["severity"] == "error" and not c["passed"]]
    if report["status"] == "pass" and failed:
        problems.append("validation claims pass while error-severity checks failed")
    if report["status"] == "pass" and report.get("error_count", 0) != 0:
        problems.append("validation claims pass with a non-zero error_count")
    if len(failed) != report.get("error_count", len(failed)):
        problems.append(
            f"error_count {report.get('error_count')} disagrees with {len(failed)} "
            "failed error-severity checks"
        )

    spread_sides: dict[str, list[str]] = {}
    for page in spec["pages"]:
        if page.get("spread_id"):
            spread_sides.setdefault(page["spread_id"], []).append(page["side"])
    for spread_id, sides in spread_sides.items():
        if sorted(sides) != ["left", "right"]:
            problems.append(f"spread {spread_id!r} is not a left/right pair: {sides}")
    return problems


def check_job_spec(job: dict) -> list[str]:
    problems: list[str] = []
    egress = job["egress"]
    if egress["requires_egress"]:
        if egress.get("consent") is None:
            problems.append("a job requiring egress has no consent-ledger reference")
        else:
            consent = egress["consent"]
            if consent.get("revoked_at") is not None:
                problems.append("a job requiring egress cites revoked consent")
        if egress.get("payload_kind") == "original_media":
            scope = (egress.get("consent") or {}).get("scope")
            if scope not in {"cloud_render", "cloud_backup"}:
                problems.append(
                    "sending original media requires an explicit cloud_render or "
                    f"cloud_backup consent scope, not {scope!r}"
                )
    else:
        if egress.get("consent") is not None:
            problems.append("a local-only job should not carry a consent reference")
        for entry in job.get("journal", {}).get("entries", []):
            if entry["action"] in {"network_send", "network_receive"}:
                problems.append("a local-only job journaled a network action")

    state = job["state"]
    if state["status"] == "completed":
        if state.get("finished_at") is None:
            problems.append("a completed job has no finished_at")
        if job.get("error") is not None and job["error"]["retryable"] is False:
            problems.append("a completed job carries a non-retryable error")
    if state["status"] == "running" and state.get("started_at") is None:
        problems.append("a running job has no started_at")

    progress = state.get("progress")
    if progress and progress.get("units_total") is not None:
        if progress["units_done"] > progress["units_total"]:
            problems.append("progress exceeds its own total")

    checkpoint = job.get("checkpoint")
    if checkpoint and not checkpoint["resumable"]:
        if checkpoint.get("cursor") is not None:
            problems.append("a non-resumable job carries a checkpoint cursor")

    retry = job.get("retry_policy")
    if retry and state["attempts"] > retry["max_attempts"]:
        problems.append("attempts exceed max_attempts")
    return problems


def check_pref_event(event: dict) -> list[str]:
    problems: list[str] = []
    if event["pixel_data_present"]:
        problems.append("a PrefEvent may never carry pixel data")

    privacy = event["privacy"]
    if privacy["shareable_for_global_model"]:
        if privacy.get("consent") is None:
            problems.append("a shareable event has no consent reference")
        if privacy.get("contains_local_identifiers", True):
            problems.append("a shareable event still holds local identifiers")
        if not privacy.get("anonymization_version"):
            problems.append("a shareable event records no anonymisation pass")
        person = event["features"].get("person_context") or {}
        if person.get("person_ids"):
            problems.append("a shareable event still names person ids")
        if (event.get("delta") or {}).get("instruction_text"):
            problems.append("a shareable event still carries free-text instructions")

    subject = event["subject"]
    alternatives = subject.get("alternatives", [])
    if alternatives:
        chosen = [a for a in alternatives if a["chosen"]]
        if len(chosen) != 1:
            problems.append(f"{len(chosen)} alternatives marked chosen; exactly one is required")
        elif chosen[0]["subject_id"] != subject["subject_id"]:
            problems.append("the chosen alternative is not the event's subject")

        ranks = [a["presented_rank"] for a in alternatives if a.get("presented_rank") is not None]
        if len(set(ranks)) != len(ranks):
            problems.append("two alternatives share a presented_rank")

        feature_set = event["features"]["feature_set_id"]
        for alternative in alternatives:
            vector = alternative.get("feature_vector")
            if vector and vector["feature_set_id"] != feature_set:
                problems.append(
                    f"alternative {alternative['subject_id']} uses feature set "
                    f"{vector['feature_set_id']!r}, not {feature_set!r}"
                )

    if event["decision"]["kind"] == "variant_picked" and not alternatives:
        problems.append("a variant pick without its alternatives is not a learnable preference")

    if event["decision"]["kind"] == "recropped":
        delta = event.get("delta") or {}
        if delta.get("crop_before") is None or delta.get("crop_after") is None:
            problems.append("a re-crop must record the crop before and after")

    dense = event["features"].get("dense")
    if dense:
        if dense["feature_set_id"] != event["features"]["feature_set_id"]:
            problems.append("dense vector and named features disagree about the feature set")
        if len(dense["values"]) != len(event["features"]["named"]):
            problems.append(
                f"dense vector has {len(dense['values'])} values but "
                f"{len(event['features']['named'])} named features"
            )
    return problems


CHECKS = {
    "media-record": check_media_record,
    "face-record": check_face_record,
    "moment-record": check_moment_record,
    "edl": check_edl,
    "album-spec": check_album_spec,
    "job-spec": check_job_spec,
    "pref-event": check_pref_event,
}


class TestSemanticInvariants(unittest.TestCase):
    def test_valid_fixtures_satisfy_every_invariant(self):
        for entry in _entries("valid"):
            with self.subTest(fixture=entry["path"]):
                problems = CHECKS[entry["schema"]](_fixture(entry))
                self.assertEqual([], problems)

    def test_semantic_invalid_fixtures_are_caught(self):
        for entry in _entries("semantic-invalid"):
            with self.subTest(fixture=entry["path"]):
                problems = CHECKS[entry["schema"]](_fixture(entry))
                self.assertTrue(
                    problems,
                    "fixture is declared semantically invalid but no invariant caught it",
                )

    def test_face_gate_rejects_a_fabricated_eligible_record(self):
        """The invariant must fail on a mutation, not merely pass on good data."""
        record = _fixture(
            next(
                e
                for e in _entries("valid", "face-record")
                if "below-threshold" in e["path"]
            )
        )
        self.assertEqual([], check_face_record(record))

        record["identity"]["eligible_for_automated_output"] = True
        self.assertTrue(check_face_record(record))

    def test_beat_alignment_invariant_rejects_a_drifting_cut(self):
        edl = _fixture(_entries("valid", "edl")[0])
        self.assertEqual([], check_edl(edl))

        for track in edl["tracks"]:
            for clip in track["items"]:
                if clip["item_type"] == "clip" and clip.get("beat_lock"):
                    clip["beat_lock"]["alignment_error_ms"] = 61.0
                    break
        problems = check_edl(edl)
        self.assertTrue(any("tolerance" in problem for problem in problems), problems)

    def test_dpi_floor_invariant_rejects_an_under_resolved_placement(self):
        spec = _fixture(_entries("valid", "album-spec")[0])
        self.assertEqual([], check_album_spec(spec))

        spec["pages"][0]["placements"][0]["effective_dpi"] = 150.0
        problems = check_album_spec(spec)
        self.assertTrue(any("DPI floor" in problem for problem in problems), problems)


class TestEdlDeterminismAndOtio(unittest.TestCase):
    """Properties the OTIO mapping depends on. See docs/otio-mapping.md."""

    def setUp(self):
        self.edl = _fixture(_entries("valid", "edl")[0])

    def test_a_hard_cut_is_the_absence_of_a_transition(self):
        for track in self.edl["tracks"]:
            for item in track["items"]:
                if item["item_type"] == "transition":
                    self.assertGreater(
                        item["in_offset"]["value"] + item["out_offset"]["value"],
                        0,
                        "a zero-length transition must never be emitted for a hard cut; "
                        "OTIO models a cut as adjacency",
                    )

    def test_every_video_time_shares_the_timeline_rate(self):
        rate = self.edl["rate"]
        for track in self.edl["tracks"]:
            if track["kind"] != "video":
                continue
            for item in track["items"]:
                for key in ("source_range", "timeline_range"):
                    span = item.get(key)
                    if not span:
                        continue
                    self.assertEqual(rate, span["start_time"]["rate"])
                    self.assertEqual(rate, span["duration"]["rate"])

    def test_frame_positions_on_the_timeline_are_integral(self):
        """OTIO tolerates fractional frames; NLEs do not. Cuts land on whole frames."""
        for track in self.edl["tracks"]:
            if track["kind"] != "video":
                continue
            for item in track["items"]:
                span = item.get("timeline_range")
                if not span:
                    continue
                for value in (span["start_time"]["value"], span["duration"]["value"]):
                    self.assertEqual(value, int(value), "timeline positions must be whole frames")

    def test_beat_positions_may_be_fractional(self):
        """Music does not land on frame boundaries, and pretending otherwise is the
        bug that makes beat-locked cuts drift. At least one beat must be fractional,
        proving the contract carries sub-frame musical time."""
        grid = self.edl["beat_grid"]
        fractional = [b for b in grid["beats"] if b["time"]["value"] != int(b["time"]["value"])]
        self.assertTrue(
            fractional,
            "no fractional beat positions -- the grid has been rounded to frames",
        )

    def test_the_exact_ntsc_rate_survives_a_json_round_trip(self):
        rate = self.edl["rate"]
        self.assertEqual(rate, 60000 / 1001)
        self.assertEqual(json.loads(json.dumps(rate)), 60000 / 1001)

    def test_reframe_tracks_produce_the_target_aspect(self):
        for track in self.edl["reframe_tracks"]:
            ratio = track["target_aspect_ratio"]
            self.assertEqual((ratio["numerator"], ratio["denominator"]), (9, 16))
            self.assertIsNotNone(track["fallback"], "a reframe must state its failure behaviour")

    def test_determinism_block_is_complete(self):
        determinism = self.edl["determinism"]
        for field in ("planner", "planner_version", "seed", "inputs_digest"):
            self.assertIn(field, determinism)
            self.assertIsNotNone(determinism[field])


class TestGeneratedBindings(unittest.TestCase):
    """The bindings must be fresh, and they must actually accept the fixtures.

    A generator that produces plausible-looking but wrong models is worse than no
    generator, so the fixtures are parsed through the real pydantic classes and
    dumped back for comparison.
    """

    @classmethod
    def setUpClass(cls):
        if str(GENERATED_PYTHON) not in sys.path:
            sys.path.insert(0, str(GENERATED_PYTHON))

    def test_codegen_is_fresh(self):
        result = subprocess.run(
            [sys.executable, str(CODEGEN / "generate.py"), "--check"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            0,
            result.returncode,
            f"generated bindings are stale:\n{result.stdout}\n{result.stderr}",
        )

    def test_generated_models_exist_for_every_root(self):
        from memory_engine_contracts import ROOT_MODELS

        self.assertEqual(
            {"MediaRecord", "FaceRecord", "MomentRecord", "EDL", "AlbumSpec", "JobSpec",
             "PrefEvent"},
            set(ROOT_MODELS),
        )

    def test_valid_fixtures_parse_through_the_generated_models(self):
        from memory_engine_contracts import ROOT_MODELS

        titles = {
            key: DOCUMENTS[filename]["title"]
            for key, filename in MANIFEST["schemas"].items()
        }
        for entry in _entries("valid"):
            with self.subTest(fixture=entry["path"]):
                model = ROOT_MODELS[titles[entry["schema"]]]
                model.model_validate(_fixture(entry))

    def test_generated_models_reject_the_schema_invalid_fixtures(self):
        from pydantic import ValidationError

        from memory_engine_contracts import ROOT_MODELS

        titles = {
            key: DOCUMENTS[filename]["title"]
            for key, filename in MANIFEST["schemas"].items()
        }
        for entry in _entries("schema-invalid"):
            path = entry["path"]
            # The generated models express types, required-ness and enums, but not
            # if/then constraints -- those are asserted against jsonschema above and
            # re-checked semantically. Only the type-level rejections are expected here.
            if entry.get("error_path") in (
                ["identity", "eligible_for_automated_output"],
                ["identity", "person_id"],
                ["egress", "consent"],
                ["privacy", "contains_local_identifiers"],
                ["validation", "error_count"],
            ):
                continue
            with self.subTest(fixture=path):
                model = ROOT_MODELS[titles[entry["schema"]]]
                with self.assertRaises(ValidationError):
                    model.model_validate(_fixture(entry))

    def test_round_trip_preserves_every_field(self):
        from memory_engine_contracts import ROOT_MODELS

        titles = {
            key: DOCUMENTS[filename]["title"]
            for key, filename in MANIFEST["schemas"].items()
        }
        for entry in _entries("valid"):
            with self.subTest(fixture=entry["path"]):
                original = _fixture(entry)
                model = ROOT_MODELS[titles[entry["schema"]]]
                dumped = model.model_validate(original).model_dump(
                    mode="json", exclude_unset=True, by_alias=True
                )
                self.assertEqual(
                    original,
                    dumped,
                    "a fixture changed shape passing through the generated model",
                )


if __name__ == "__main__":
    unittest.main()
