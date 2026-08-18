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
import math
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

    def test_every_contract_schema_exists(self):
        """Eight now. SafetyClearance joined the original seven because a
        publication gate that lives only in code is a gate one caller can
        bypass -- see contracts/schemas/safety-clearance.schema.json."""
        expected = {
            "media-record",
            "face-record",
            "moment-record",
            "edl",
            "album-spec",
            "job-spec",
            "pref-event",
            "safety-clearance",
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
                if entry["expectation"] == "schema-invalid":
                    self.assertIn(
                        entry.get("rejected_by"),
                        {"schema_type", "schema_conditional"},
                        "a negative fixture must say which kind of rule rejects it, so "
                        "the generated-bindings test knows what to expect of it",
                    )

    def test_required_edge_cases_are_covered(self):
        """The edge cases named in CLAUDE.md Phase 0 task 3, plus the ones Codex
        raised in review: schema support without a golden fixture does not
        establish that ingest and render can actually use a shape."""
        paths = {entry["path"] for entry in MANIFEST["fixtures"]}
        required = {
            "a GoPro chaptered video spanning files": {
                "media-record/valid/video-gopro-chapter-01.json",
                "media-record/valid/video-gopro-chapter-02.json",
                "media-record/valid/video-gopro-span-assembly.json",
            },
            "a photo with no EXIF date": {"media-record/valid/image-no-exif-date.json"},
            "a WhatsApp filename-derived date": {
                "media-record/valid/image-whatsapp-filename-date.json"
            },
            "HEIC": {"media-record/valid/image-heic-mislabeled-extension.json"},
            "paired Live Photos": {
                "media-record/valid/live-photo-still.json",
                "media-record/valid/live-photo-motion.json",
            },
            "a corrupt or truncated file": {
                "media-record/valid/file-truncated-quarantined.json"
            },
            "a zero-byte file": {"media-record/valid/file-zero-byte-quarantined.json"},
            "a face below confidence threshold": {
                "face-record/valid/face-below-threshold-review-queued.json"
            },
            "an EDL with a beat-locked cut and a vertical reframe keyframe track": {
                "edl/valid/reel-beat-locked-vertical-reframe.json"
            },
            "a moment crossing a GoPro chapter boundary": {
                "moment-record/valid/moment-crosses-chapter-boundary.json"
            },
            "distinct scan roots yielding distinct job ids": {
                "job-spec/valid/job-scan-source-root-a.json",
                "job-spec/valid/job-scan-source-root-b.json",
            },
        }
        for description, expected in required.items():
            with self.subTest(edge_case=description):
                self.assertTrue(
                    expected <= paths,
                    f"missing fixture(s) for: {description} -- {sorted(expected - paths)}",
                )

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

    sensitive = record.get("sensitive")
    if sensitive is None:
        problems.append(
            "no minor-safety envelope; 'nobody asked' is not the same as 'not a child'"
        )
        return problems

    minor = sensitive.get("minor_status", "unknown")
    consent = sensitive.get("labeling_consent")
    if minor == "confirmed_minor":
        if consent is None:
            problems.append("a confirmed minor was labeled without a consent reference")
        else:
            if consent.get("scope") != "minor_face_labeling":
                problems.append(
                    f"child labeling justified by a {consent.get('scope')!r} consent; "
                    "a permission for one thing is not a permission for another"
                )
            if consent.get("revoked_at") is not None:
                problems.append("child labeling relies on a revoked consent")
            if consent.get("expires_at") is not None and consent.get("granted_at") is not None:
                if consent["expires_at"] <= consent["granted_at"]:
                    problems.append("child labeling consent expires before it was granted")
    elif consent is not None:
        problems.append(
            f"minor labeling consent attached to a face marked {minor!r}, which does not need it"
        )
    return problems


def check_media_record(record: dict) -> list[str]:
    problems: list[str] = []
    asset_kind = record.get("asset_kind")
    span = record.get("span")

    # Identity, size and source rules differ by asset_kind. Getting this wrong is
    # what the review caught: an assembly that claimed a byte size no file has.
    if asset_kind == "virtual_assembly":
        if record["byte_size"] != 0:
            problems.append(
                f"virtual assembly claims byte_size {record['byte_size']}; it has no bytes "
                "of its own, and a size matching no file breaks filesystem verification"
            )
        if record["sources"]:
            problems.append("virtual assembly exposes a source path; its members own the paths")
        if record.get("proxies"):
            problems.append("virtual assembly has proxies; it reads its members' proxies in order")
        if span is None:
            problems.append("virtual assembly has no span")
        elif span["span_id"] != record["media_id"]:
            problems.append(
                "assembly media_id must equal its span_id -- with no bytes to hash, its "
                "members' identity is its identity"
            )
    elif asset_kind == "physical_file":
        if not record["sources"]:
            problems.append("physical file has no source location")
        if span and span["role"] != "member":
            problems.append("a physical file may only be a span member, never the assembly")
    else:
        problems.append(f"unknown asset_kind {asset_kind!r}")

    if span:
        if span["role"] == "member":
            if span.get("index") is None:
                problems.append("span member has no index")
            if span.get("member_media_ids"):
                problems.append("only the assembly record lists member_media_ids")
        else:
            if span.get("index") is not None:
                problems.append("span assembly must have a null index")
            if len(span.get("member_media_ids", [])) < 2:
                problems.append("a span assembly needs at least two members")
            if span.get("member_count") is not None:
                if span["member_count"] != len(span.get("member_media_ids", [])):
                    problems.append("member_count disagrees with member_media_ids")

    for hash_field in (
        (record.get("perceptual") or {}).get("image_hash"),
        *[k["hash"] for k in (record.get("perceptual") or {}).get("keyframe_hashes", [])],
    ):
        if not hash_field:
            continue
        if hash_field["bits"] != 4 * len(hash_field["hex"]):
            problems.append(
                f"perceptual hash declares {hash_field['bits']} bits but carries "
                f"{4 * len(hash_field['hex'])} bits of hex; malformed digests poison dedupe"
            )
        suffix = hash_field["algorithm"].rsplit("-", 1)[-1]
        if suffix.isdigit() and int(suffix) != hash_field["bits"]:
            problems.append(
                f"algorithm {hash_field['algorithm']} implies {suffix} bits but "
                f"bits is {hash_field['bits']}"
            )

    paired = (record.get("image") or {}).get("paired_motion_media_id")
    if paired and paired == record["media_id"]:
        problems.append("a Live Photo cannot be paired to itself")

    if record["processing"]["state"] == "quarantined":
        if not record["exclusion"]["excluded_from_automation"]:
            problems.append("a quarantined file must be excluded from automated output")
        for name, stage in record["processing"]["stages"].items():
            error = stage.get("last_error")
            if error and error["retryable"]:
                problems.append(
                    f"quarantined on a retryable error in stage {name!r}; quarantine is for "
                    "files that must never be retried automatically"
                )

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

            if clip.get("reframe_track_id"):
                known = {t["reframe_track_id"] for t in edl.get("reframe_tracks", [])}
                if clip["reframe_track_id"] not in known:
                    problems.append(
                        f"{clip['clip_id']} references unknown reframe track "
                        f"{clip['reframe_track_id']!r}"
                    )

            # contracts#50: source_range is the media read; the timeline extent is
            # DERIVED from it and any time effect. Nothing else may set it, and a
            # timeline_range that disagrees is the drift this rule exists to stop.
            extent = source["duration"]["value"]
            effect = clip.get("time_effect")
            if effect:
                if effect["kind"] == "linear_speed":
                    scalar = effect.get("time_scalar")
                    if not scalar:
                        problems.append(
                            f"{clip['clip_id']} has a linear_speed effect with no scalar"
                        )
                    else:
                        if effect.get("hold_duration") is not None:
                            problems.append(
                                f"{clip['clip_id']} is a linear_speed clip carrying a "
                                "hold_duration, which belongs to freeze_frame"
                            )
                        exact = extent / scalar
                        if exact != int(exact):
                            problems.append(
                                f"{clip['clip_id']} reads {extent} frames at {scalar}x, which "
                                f"is {exact} timeline frames rather than a whole number"
                            )
                        extent = exact
                elif effect["kind"] == "freeze_frame":
                    if effect.get("freeze_at") is None:
                        problems.append(f"{clip['clip_id']} has a freeze_frame with no freeze_at")
                    elif effect["freeze_at"]["value"] != source["start_time"]["value"]:
                        problems.append(
                            f"{clip['clip_id']} freezes at {effect['freeze_at']['value']} but "
                            f"reads from {source['start_time']['value']}; the frozen frame must "
                            "be the frame the clip reads"
                        )
                    if source["duration"]["value"] != 1:
                        problems.append(
                            f"{clip['clip_id']} is a freeze_frame reading "
                            f"{source['duration']['value']} source frames; it reads exactly one"
                        )
                    if effect.get("hold_duration") is None:
                        problems.append(
                            f"{clip['clip_id']} is a freeze_frame with no hold_duration, so its "
                            "timeline extent is undefined"
                        )
                    else:
                        if effect.get("time_scalar") is not None:
                            problems.append(
                                f"{clip['clip_id']} is a freeze_frame carrying a time_scalar"
                            )
                        extent = effect["hold_duration"]["value"]

            timeline = clip.get("timeline_range")
            if timeline is not None:
                if timeline["start_time"]["value"] != position:
                    problems.append(
                        f"{clip['clip_id']} starts at {timeline['start_time']['value']} "
                        f"but the track has reached {position}"
                    )
                if timeline["duration"]["value"] != extent:
                    problems.append(
                        f"{clip['clip_id']} declares a {timeline['duration']['value']}-frame "
                        f"timeline extent; source_range and its time effect derive {extent}"
                    )
            position += extent

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

        # contracts#59: the bed is placed on a track, once. The cue licenses that
        # placement and does not repeat it, so a renderer can never double it.
        music_clips = {
            item["clip_id"]: item
            for track in edl["tracks"]
            if track["kind"] == "audio" and track.get("role") == "music"
            for item in track["items"]
            if item["item_type"] == "clip"
        }
        claimed: dict[str, str] = {}
        for cue in plan.get("music", []):
            for clip_id in cue["clip_ids"]:
                if clip_id not in music_clips:
                    problems.append(
                        f"cue {cue['cue_id']} places itself on {clip_id!r}, which is not a clip "
                        "on a music-role audio track"
                    )
                    continue
                if clip_id in claimed:
                    problems.append(
                        f"clip {clip_id} is claimed by cues {claimed[clip_id]} and "
                        f"{cue['cue_id']}; a bed with two licences plays once and is licensed "
                        "twice"
                    )
                claimed[clip_id] = cue["cue_id"]
                if music_clips[clip_id]["media_ref_id"] != cue["media_ref_id"]:
                    problems.append(
                        f"cue {cue['cue_id']} licenses {cue['media_ref_id']} but clip {clip_id} "
                        f"plays {music_clips[clip_id]['media_ref_id']}"
                    )
        for clip_id in music_clips:
            if clip_id not in claimed:
                problems.append(
                    f"music clip {clip_id} is placed on a music track and no cue claims it, so "
                    "it plays with no licence attached"
                )

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


#: Gates that must appear, and have passed, in any AlbumSpec claiming a pass.
#: The first four are named in the build plan; page_count_valid is here because a
#: page count the vendor rejects is just as final a failure and the renderer is
#: explicitly forbidden from fixing it later.
HARD_PRINT_GATES = (
    "dpi_floor",
    "face_in_trim_zone",
    "bleed_coverage",
    "color_profile_match",
    "page_count_valid",
)


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

    page_count = len(spec["pages"])
    limits = profile.get("page_count")
    if limits:
        if page_count < limits["minimum"]:
            problems.append(
                f"{page_count} pages against a vendor minimum of {limits['minimum']}; "
                "the renderer is forbidden from adding filler later"
            )
        if page_count > limits["maximum"]:
            problems.append(f"{page_count} pages exceeds the vendor maximum of {limits['maximum']}")
        if page_count % limits["increment"] != 0:
            problems.append(
                f"{page_count} pages is off the vendor increment of {limits['increment']}"
            )

    report = spec["validation"]
    failed = [c for c in report["checks"] if c["severity"] == "error" and not c["passed"]]
    if report["status"] == "pass" and failed:
        problems.append("validation claims pass while error-severity checks failed")
    if report["status"] == "pass":
        # A pass must be earned. An empty or partial checks array would otherwise
        # pass trivially -- the case where a spec ships because nobody ran the
        # validator rather than because it is correct.
        present = {c["check_id"] for c in report["checks"] if c["passed"]}
        for gate in HARD_PRINT_GATES:
            if gate not in present:
                problems.append(f"pass report is missing the {gate} hard gate")
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

    if job["job_type"] == "scan_source":
        # A scan runs before anything has a content hash, so without a locator
        # digest two scans of different roots share an identity and the second is
        # skipped as already-done: a whole drive silently never imported.
        if not job["inputs"].get("source_paths"):
            problems.append("a scan job names no source paths")
        if not job["inputs"].get("source_locator_digest"):
            problems.append(
                "a scan job has no source_locator_digest; its identity would be "
                "indistinguishable from any other scan with the same params"
            )
        if job["inputs"].get("media_ids"):
            problems.append("a scan job cannot have media ids -- discovering them is its output")
    elif job["inputs"].get("source_locator_digest"):
        problems.append(
            f"{job['job_type']} carries a source_locator_digest; only scan_source "
            "addresses locations rather than content"
        )

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


def check_safety_clearance(doc: dict) -> list[str]:
    """Cross-field invariants for the publication gate.

    The schema can express "cleared implies zero indeterminate" but not
    arithmetic, and the arithmetic is where a forged summary would hide: a
    verifier that trusted `decision` over `items` would publish the very photo
    the manifest says was never checked.
    """
    problems: list[str] = []
    items = doc["items"]
    decision = doc["decision"]

    counts = {"cleared": 0, "blocked": 0, "indeterminate": 0}
    for item in items:
        counts[item["verdict"]] += 1

    if decision["item_count"] != len(items):
        problems.append(
            f"decision.item_count {decision['item_count']} but {len(items)} items"
        )
    for verdict, key in (("cleared", "cleared_count"),
                         ("blocked", "blocked_count"),
                         ("indeterminate", "indeterminate_count")):
        if decision[key] != counts[verdict]:
            problems.append(
                f"decision.{key} says {decision[key]} but {counts[verdict]} items are {verdict}"
            )

    # Duplicate media ids make the manifest ambiguous: two entries for one photo
    # can disagree, and which one a verifier honours becomes an accident of
    # iteration order.
    ids = [item["media_id"] for item in items]
    if len(set(ids)) != len(ids):
        problems.append("duplicate media_id in items; the manifest is ambiguous")

    # The rule everything else protects, checked arithmetically as well as
    # conditionally: an unchecked item denies the whole publication.
    unresolved = [
        item["media_id"] for item in items
        if item["verdict"] == "indeterminate"
        or (item["verdict"] == "blocked" and not item.get("override"))
    ]
    if decision["cleared_for_publication"] and unresolved:
        problems.append(
            f"cleared_for_publication is true while {len(unresolved)} items are "
            "unresolved (indeterminate, or blocked with no override)"
        )
    if not decision["cleared_for_publication"] and not unresolved:
        problems.append(
            "cleared_for_publication is false but every item is resolved; a "
            "denial nobody can explain is a denial nobody will trust"
        )

    # A verdict produced by a permissive host must not clear a real publication.
    load_mode = doc["classifier"].get("load_mode")
    if (
        load_mode == "development"
        and decision["cleared_for_publication"]
        and doc["sink"] in {"print", "share", "frontier_egress"}
    ):
        problems.append(
            f"a development-mode classifier cleared a {doc['sink']} publication; "
            "unpinned weights must not clear anything irreversible"
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
    "safety-clearance": check_safety_clearance,
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

    def _retimed_clip(self, edl: dict) -> dict:
        for track in edl["tracks"]:
            for clip in track["items"]:
                if clip["item_type"] == "clip" and clip.get("time_effect"):
                    return clip
        self.fail("the golden EDL no longer exercises a time effect")

    def test_time_effect_invariant_rejects_the_other_reading(self):
        """contracts#50. Reading source_range as the TIMELINE extent -- the other
        candidate reading -- makes the clip read 112 frames instead of 56, and
        every cut after it lands 112 frames late with nothing to say so."""
        edl = _fixture(_entries("valid", "edl")[0])
        self.assertEqual([], check_edl(edl))

        clip = self._retimed_clip(edl)
        timeline = clip["timeline_range"]["duration"]["value"]
        clip["source_range"]["duration"]["value"] = timeline
        problems = check_edl(edl)
        self.assertTrue(any("timeline extent" in problem for problem in problems), problems)

    def test_a_speed_that_does_not_divide_into_whole_frames_is_rejected(self):
        edl = _fixture(_entries("valid", "edl")[0])
        clip = self._retimed_clip(edl)
        clip["time_effect"]["time_scalar"] = 0.75
        problems = check_edl(edl)
        self.assertTrue(any("whole number" in problem for problem in problems), problems)

    def test_a_freeze_frame_without_a_hold_has_no_extent(self):
        edl = _fixture(_entries("valid", "edl")[0])
        clip = self._retimed_clip(edl)
        start = clip["source_range"]["start_time"]
        clip["time_effect"] = {
            "kind": "freeze_frame",
            "time_scalar": None,
            "freeze_at": dict(start),
            "hold_duration": None,
            "audio_handling": "mute",
        }
        clip["source_range"]["duration"]["value"] = 1
        problems = check_edl(edl)
        self.assertTrue(any("hold_duration" in problem for problem in problems), problems)

    def test_music_placement_invariant_rejects_an_unclaimed_bed(self):
        """contracts#59. A music clip no cue claims is a bed playing with no
        licence -- which is the failure the cue exists to make impossible."""
        edl = _fixture(_entries("valid", "edl")[0])
        self.assertEqual([], check_edl(edl))

        edl["audio_plan"]["music"][0]["clip_ids"] = []
        problems = check_edl(edl)
        self.assertTrue(any("no licence attached" in problem for problem in problems), problems)

    def test_music_placement_invariant_rejects_a_cue_that_places_nothing(self):
        edl = _fixture(_entries("valid", "edl")[0])
        edl["audio_plan"]["music"][0]["clip_ids"] = ["not-a-clip"]
        problems = check_edl(edl)
        self.assertTrue(any("music-role audio track" in problem for problem in problems), problems)

    def test_dpi_floor_invariant_rejects_an_under_resolved_placement(self):
        spec = _fixture(_entries("valid", "album-spec")[0])
        self.assertEqual([], check_album_spec(spec))

        spec["pages"][0]["placements"][0]["effective_dpi"] = 150.0
        problems = check_album_spec(spec)
        self.assertTrue(any("DPI floor" in problem for problem in problems), problems)

    def test_page_count_invariant_rejects_a_short_album(self):
        spec = _fixture(_entries("valid", "album-spec")[0])
        self.assertEqual([], check_album_spec(spec))

        spec["pages"] = spec["pages"][:3]
        problems = check_album_spec(spec)
        self.assertTrue(any("vendor minimum" in problem for problem in problems), problems)

    def test_pass_report_invariant_rejects_a_missing_hard_gate(self):
        spec = _fixture(_entries("valid", "album-spec")[0])
        self.assertEqual([], check_album_spec(spec))

        spec["validation"]["checks"] = [
            c for c in spec["validation"]["checks"] if c["check_id"] != "bleed_coverage"
        ]
        problems = check_album_spec(spec)
        self.assertTrue(any("bleed_coverage" in problem for problem in problems), problems)

    def test_assembly_invariant_rejects_borrowed_bytes_and_paths(self):
        record = _fixture(
            next(e for e in _entries("valid", "media-record") if "assembly" in e["path"])
        )
        self.assertEqual([], check_media_record(record))

        record["byte_size"] = 6043992064
        problems = check_media_record(record)
        self.assertTrue(any("byte_size" in problem for problem in problems), problems)

    def test_minor_consent_invariant_rejects_a_wrong_scope(self):
        record = _fixture(
            next(e for e in _entries("valid", "face-record") if "minor" in e["path"])
        )
        self.assertEqual([], check_face_record(record))

        record["sensitive"]["labeling_consent"]["scope"] = "cloud_render"
        problems = check_face_record(record)
        self.assertTrue(any("permission for one thing" in p for p in problems), problems)

        record["sensitive"]["labeling_consent"]["scope"] = "minor_face_labeling"
        record["sensitive"]["labeling_consent"]["revoked_at"] = "2026-03-19T08:00:00+05:30"
        problems = check_face_record(record)
        self.assertTrue(any("revoked" in p for p in problems), problems)

    def test_perceptual_hash_invariant_rejects_a_length_mismatch(self):
        record = _fixture(
            next(e for e in _entries("valid", "media-record") if "beach-sunset" in e["path"])
        )
        self.assertEqual([], check_media_record(record))

        record["perceptual"]["image_hash"]["bits"] = 256
        problems = check_media_record(record)
        self.assertTrue(any("poison dedupe" in problem for problem in problems), problems)

    def test_scan_identity_invariant_rejects_a_missing_locator(self):
        job = _fixture(
            next(e for e in _entries("valid", "job-spec") if "scan-source-root-a" in e["path"])
        )
        self.assertEqual([], check_job_spec(job))

        job["inputs"]["source_locator_digest"] = None
        problems = check_job_spec(job)
        self.assertTrue(any("indistinguishable" in problem for problem in problems), problems)


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


class TestChapterSpanning(unittest.TestCase):
    """A recording split across files must be usable as one timeline.

    These derive the boundary from the member fixtures rather than hard-coding
    it, so the fixtures cannot drift apart from each other silently.
    """

    def setUp(self):
        media = {}
        for entry in _entries("valid", "media-record"):
            media[Path(entry["path"]).stem] = _fixture(entry)
        self.assembly = media["video-gopro-span-assembly"]
        self.members = [media["video-gopro-chapter-01"], media["video-gopro-chapter-02"]]
        start = self.assembly["video"]["start_timecode"]["value"]
        first = self.members[0]["video"]["duration"]["value"]
        self.boundary = start + first
        self.available = (start, start + self.assembly["video"]["duration"]["value"])

    def test_assembly_duration_is_the_sum_of_its_members(self):
        total = sum(m["video"]["duration"]["value"] for m in self.members)
        self.assertEqual(total, self.assembly["video"]["duration"]["value"])

    def test_members_are_listed_in_order_with_matching_offsets(self):
        listed = self.assembly["span"]["member_media_ids"]
        self.assertEqual([m["media_id"] for m in self.members], listed)

        offset = 0
        for member in self.members:
            self.assertEqual(offset, member["span"]["offset_in_span"]["value"])
            offset += member["video"]["duration"]["value"]

    def test_every_member_shares_the_assemblys_span_id(self):
        for member in self.members:
            self.assertEqual(self.assembly["span"]["span_id"], member["span"]["span_id"])
            self.assertEqual("physical_file", member["asset_kind"])
        self.assertEqual("virtual_assembly", self.assembly["asset_kind"])

    def test_a_moment_actually_crosses_the_chapter_boundary(self):
        moment = _fixture(
            next(
                e
                for e in _entries("valid", "moment-record")
                if "crosses-chapter" in e["path"]
            )
        )
        self.assertEqual(self.assembly["media_id"], moment["media_id"])
        start = moment["source_range"]["start_time"]["value"]
        end = start + moment["source_range"]["duration"]["value"]
        self.assertLess(start, self.boundary)
        self.assertGreater(end, self.boundary)
        self.assertGreaterEqual(start, self.available[0])
        self.assertLessEqual(end, self.available[1])

    def test_an_edl_clip_actually_crosses_the_chapter_boundary(self):
        edl = _fixture(_entries("valid", "edl")[0])
        refs = {
            r["media_ref_id"]: r for r in edl["media_refs"] if r.get("is_span_assembly")
        }
        self.assertTrue(refs, "no span assembly referenced by the EDL")

        crossing = []
        for track in edl["tracks"]:
            for item in track["items"]:
                if item["item_type"] != "clip" or item["media_ref_id"] not in refs:
                    continue
                start = item["source_range"]["start_time"]["value"]
                end = start + item["source_range"]["duration"]["value"]
                if start < self.boundary < end:
                    crossing.append(item["clip_id"])

        self.assertTrue(
            crossing,
            f"no EDL clip spans the chapter boundary at {self.boundary}; render cannot be "
            "shown to handle a cut across a chaptered file split",
        )

    def test_reframe_keyframes_track_the_crossing_clip(self):
        """A clip's reframe keyframes are in source time, so they must have moved
        with the clip rather than being left pointing at the old source range."""
        edl = _fixture(_entries("valid", "edl")[0])
        tracks = {t["reframe_track_id"]: t for t in edl["reframe_tracks"]}
        for track in edl["tracks"]:
            for clip in track["items"]:
                if clip["item_type"] != "clip" or not clip.get("reframe_track_id"):
                    continue
                start = clip["source_range"]["start_time"]["value"]
                end = start + clip["source_range"]["duration"]["value"]
                for keyframe in tracks[clip["reframe_track_id"]]["keyframes"]:
                    self.assertGreaterEqual(keyframe["time"]["value"], start, clip["clip_id"])
                    self.assertLessEqual(keyframe["time"]["value"], end, clip["clip_id"])


class TestSpanIdentityIsComputable(unittest.TestCase):
    """`span_id` must be RECOMPUTABLE from its members, not asserted.

    Codex found (issue #26) that the golden assembly's span_id matched none of
    the plausible byte encodings -- concatenated hex, concatenated raw digests,
    or any delimited variant. It matched none of them because it had been
    written by hand rather than computed, so the fixture that was supposed to
    pin a content-addressed identity was pinning a number somebody made up.

    A cross-language producer cannot be checked against a made-up hash, and
    this identity is persisted and referenced by MomentRecord and EDL. So the
    encoding is now specified in the schema and recomputed here.
    """

    ENCODING = "concatenated lowercase hex, no delimiter, index order"

    def _assemblies(self):
        found = []
        for path in sorted((CONTRACTS / "fixtures" / "media-record").rglob("*.json")):
            record = json.loads(path.read_text(encoding="utf-8"))
            span = record.get("span") or {}
            if span.get("member_media_ids"):
                found.append((path, record, span))
        return found

    def test_there_is_an_assembly_fixture_to_check(self):
        self.assertTrue(self._assemblies(), "this guard has nothing to guard")

    def test_every_span_id_recomputes_from_its_members(self):
        try:
            from blake3 import blake3
        except ImportError:
            self.skipTest("blake3 is not installed")

        for path, _record, span in self._assemblies():
            with self.subTest(fixture=path.name):
                payload = "".join(span["member_media_ids"]).encode("ascii")
                self.assertEqual(
                    blake3(payload).hexdigest(),
                    span["span_id"],
                    f"span_id does not recompute under the canonical encoding "
                    f"({self.ENCODING}); see issue #26",
                )

    def test_an_assemblys_span_id_is_also_its_media_id(self):
        """The assembly has no bytes to hash, so its members' identity is its
        identity. Stated in the schema; unchecked until now."""
        for path, record, span in self._assemblies():
            if span.get("role") != "assembly":
                continue
            with self.subTest(fixture=path.name):
                self.assertEqual(span["span_id"], record["media_id"])

    def test_the_encoding_is_prefix_free_so_no_delimiter_is_needed(self):
        """Why concatenation without a separator is safe rather than lucky.

        Every Blake3Hash is exactly 64 hex characters, so the concatenation is
        fixed-width and no two different member lists can produce the same byte
        string. A variable-length id would need a delimiter, and omitting it is
        where this class of bug usually starts.
        """
        schema = json.loads(
            (CONTRACTS / "schemas" / "common.schema.json").read_text(encoding="utf-8")
        )
        blake3_hash = schema["$defs"]["Blake3Hash"]
        self.assertEqual("^[0-9a-f]{64}$", blake3_hash["pattern"])

    def test_order_changes_the_identity(self):
        """Chapters are a sequence, not a set. Sorting by hash would scramble a
        timeline, and a different order really is a different recording."""
        try:
            from blake3 import blake3
        except ImportError:
            self.skipTest("blake3 is not installed")

        a, b = "a" * 64, "b" * 64
        self.assertNotEqual(
            blake3((a + b).encode("ascii")).hexdigest(),
            blake3((b + a).encode("ascii")).hexdigest(),
        )


#: Paths removed from an EDL before `edl_id` is computed. Stated in the schema's
#: $comment on edl_id; repeated here because this is what enforces it.
EDL_ID_EXCLUDED = (
    "edl_id",
    "determinism.generated_at",
    "validation.validated_at",
    "tracks[].items[].timeline_range",
    "variant.sibling_edl_ids",
)


def _digest_view(edl: dict) -> dict:
    view = {key: value for key, value in edl.items() if key != "edl_id"}
    view["determinism"] = {
        key: value for key, value in view["determinism"].items() if key != "generated_at"
    }
    if view.get("validation") is not None:
        view["validation"] = {
            key: value for key, value in view["validation"].items() if key != "validated_at"
        }
    if view.get("variant") is not None:
        view["variant"] = {
            key: value for key, value in view["variant"].items() if key != "sibling_edl_ids"
        }
    view["tracks"] = [
        {
            **track,
            "items": [
                {key: value for key, value in item.items() if key != "timeline_range"}
                for item in track["items"]
            ],
        }
        for track in view["tracks"]
    ]
    return view


def _canonical_json(value) -> bytes:
    """RFC 8785 canonical JSON, for the value shapes this contract permits.

    Deliberately independent of the planner's implementation: a guard that
    imported the producer's canonicaliser would agree with the producer by
    construction, which is exactly the property that must not be assumed.
    """
    out: list[str] = []

    def emit(node) -> None:
        if node is None:
            out.append("null")
        elif node is True:
            out.append("true")
        elif node is False:
            out.append("false")
        elif isinstance(node, str):
            out.append(json.dumps(node, ensure_ascii=False))
        elif isinstance(node, int):
            out.append(str(node))
        elif isinstance(node, float):
            # ECMAScript Number::toString: an integral double prints without a
            # fractional part, so 120.0 is `120` and not `120.0`.
            out.append(str(int(node)) if node.is_integer() else repr(node))
        elif isinstance(node, dict):
            out.append("{")
            for index, key in enumerate(sorted(node, key=lambda k: k.encode("utf-16-be"))):
                if index:
                    out.append(",")
                out.append(json.dumps(key, ensure_ascii=False))
                out.append(":")
                emit(node[key])
            out.append("}")
        elif isinstance(node, list):
            out.append("[")
            for index, item in enumerate(node):
                if index:
                    out.append(",")
                emit(item)
            out.append("]")
        else:
            raise TypeError(f"not JSON-serialisable: {type(node).__name__}")

    emit(value)
    return "".join(out).encode("utf-8")


class TestEdlIdentityIsComputable(unittest.TestCase):
    """`edl_id` must RECOMPUTE from the plan, not be asserted alongside it.

    The same failure as issue #26's invented span_id, one schema over: the
    golden EDL shipped an edl_id that matched no implementation of the rule its
    own description states, and nothing checked. An id that does not recompute
    cannot be a render-cache key, cannot be the input a JobSpec names, and
    cannot tell two plans apart -- it is decoration.
    """

    def _edls(self):
        return [(entry["path"], _fixture(entry)) for entry in _entries("valid", "edl")]

    def test_there_is_an_edl_to_check(self):
        self.assertTrue(self._edls(), "this guard has nothing to guard")

    def test_every_edl_id_recomputes_from_its_plan(self):
        try:
            from blake3 import blake3
        except ImportError:
            self.skipTest("blake3 is not installed")

        for path, edl in self._edls():
            with self.subTest(fixture=path):
                self.assertEqual(
                    blake3(_canonical_json(_digest_view(edl))).hexdigest(),
                    edl["edl_id"],
                    "edl_id does not recompute under the canonicalisation the schema states",
                )

    def test_the_excluded_paths_really_are_excluded(self):
        """Changing an excluded field must not move the id, or the id is not a
        cache key. Changing anything else must move it, or it is not an id."""
        try:
            from blake3 import blake3
        except ImportError:
            self.skipTest("blake3 is not installed")

        _, edl = self._edls()[0]
        digest = lambda doc: blake3(_canonical_json(_digest_view(doc))).hexdigest()  # noqa: E731
        before = digest(edl)

        edl["determinism"]["generated_at"] = "2031-01-01T00:00:00+00:00"
        edl["validation"]["validated_at"] = "2031-01-01T00:00:00+00:00"
        for track in edl["tracks"]:
            for item in track["items"]:
                if item["item_type"] == "clip":
                    item["timeline_range"] = None
        self.assertEqual(before, digest(edl), f"an excluded path moved the id ({EDL_ID_EXCLUDED})")

        edl["tracks"][0]["items"][0]["source_range"]["duration"]["value"] += 1
        self.assertNotEqual(before, digest(edl), "a source range change did not move the id")


#: contracts/vectors/face-id.json -- golden input -> face_id pairs, the artifact
#: issue #34 asked for. Kept outside fixtures/ because it is not an instance of
#: any schema, and fixtures/index.json is required to account for every file
#: under fixtures/.
FACE_ID_VECTORS = json.loads(
    (CONTRACTS / "vectors" / "face-id.json").read_text(encoding="utf-8")
)

_UNIT_SEPARATOR = "\x1f"
_FACE_ID_DOMAIN = "face:v1"
_BBOX_QUANTUM = 10_000


def _ecmascript_number(value) -> str:
    """RFC 8785 number rendering, or a refusal.

    Deliberately not `repr` and not `str`: `repr(1.0)` is `1.0` where
    JavaScript's `String(1)` is `1`, and that single difference is what made
    every model report a config-digest mismatch once already. An exponent form
    is refused rather than written, because that is the one range where
    Python's shortest-round-trip formatting and ECMAScript's stop agreeing on
    padding -- and no real frame rate needs one.
    """
    if isinstance(value, bool):
        raise TypeError("a boolean is not a number")
    if isinstance(value, int):
        rendered = str(value)
    else:
        number = float(value)
        if number != number or number in (float("inf"), float("-inf")):
            raise ValueError(f"{value!r} is not finite")
        rendered = str(int(number)) if number.is_integer() else repr(number)
    if "e" in rendered or "E" in rendered:
        raise ValueError(f"{value!r} needs exponent notation and is refused")
    return rendered


def _quantise(value) -> int:
    """v * 10000, rounded HALF AWAY FROM ZERO.

    Not `round()`. Python rounds half to even, JavaScript's `Math.round` and
    Rust's `f64::round` round half away from zero, and 8855 of the 10000
    half-quantum positions in [0,1] are exactly representable as doubles -- so
    `round()` here means the same detection gets two ids depending on which
    language wrote it.
    """
    number = float(value)
    if number < 0:
        raise ValueError(f"{value!r} is negative; a normalised box component is not")
    scaled = number * _BBOX_QUANTUM
    floor = int(scaled // 1)
    return floor + 1 if scaled - floor >= 0.5 else floor


def _face_id_preimage(
    *, media_id: str, frame_time, bbox: dict, model_id: str, version: str
) -> bytes:
    """The exact bytes face_id is BLAKE3 of, per face-record.schema.json.

    Independent of every producer in the repo, on purpose. A guard that
    imported the producer's encoder would agree with the producer by
    construction, which is the one property that must not be assumed.
    """
    time_part = (
        ""
        if frame_time is None
        else f"{_ecmascript_number(frame_time['value'])}/{_ecmascript_number(frame_time['rate'])}"
    )
    box_part = ",".join(str(_quantise(bbox[axis])) for axis in ("x", "y", "w", "h"))
    for field in (model_id, version):
        if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in field):
            raise ValueError("a detector id or version carries the field separator")
    joined = _UNIT_SEPARATOR.join(
        [_FACE_ID_DOMAIN, media_id, time_part, box_part, model_id, version]
    )
    return joined.encode("utf-8")


def _preimage_of_record(record: dict) -> bytes:
    detection = record["detection"]
    return _face_id_preimage(
        media_id=record["media_id"],
        frame_time=record.get("frame_time"),
        bbox=detection["bbox"],
        model_id=detection["detector"]["model_id"],
        version=detection["detector"]["version"],
    )


class TestFaceIdentityIsComputable(unittest.TestCase):
    """`face_id` must RECOMPUTE from the detection, not be asserted beside it.

    Issue #34: the schema named a tuple and never said how it becomes bytes, so
    every writer picked its own separators, its own bbox rounding and its own
    number formatting -- and the three golden fixtures carried ids that matched
    none of them, because they had been typed by hand. That is issue #26's
    invented span_id happening a second time, one schema over, and the same
    remedy applies: state the byte string, then compute every fixture from it.
    """

    def _face_entries(self):
        return _entries(schema="face-record")

    def test_there_are_face_fixtures_to_check(self):
        self.assertTrue(self._face_entries(), "this guard has nothing to guard")

    def test_every_face_id_recomputes_from_its_detection(self):
        try:
            from blake3 import blake3
        except ImportError:
            self.skipTest("blake3 is not installed")

        for entry in self._face_entries():
            with self.subTest(fixture=entry["path"]):
                record = _fixture(entry)
                self.assertEqual(
                    blake3(_preimage_of_record(record)).hexdigest(),
                    record["face_id"],
                    "face_id does not recompute under the canonical encoding "
                    "stated on face_id in face-record.schema.json; see issue #34",
                )

    def test_the_same_detection_recorded_twice_keeps_one_id(self):
        """Four fixtures describe two detections. Two identity blocks over one
        box is one face, and it must stay one row."""
        by_preimage: dict[bytes, set[str]] = {}
        for entry in self._face_entries():
            record = _fixture(entry)
            by_preimage.setdefault(_preimage_of_record(record), set()).add(
                record["face_id"]
            )
        shared = {k: v for k, v in by_preimage.items() if len(v) > 1}
        self.assertEqual({}, shared, "one detection carries more than one id")
        self.assertLess(
            len(by_preimage),
            len(self._face_entries()),
            "no fixture pair shares a detection, so this guard proves nothing",
        )

    def test_a_still_and_a_video_face_are_both_covered(self):
        times = [_fixture(e).get("frame_time") for e in self._face_entries()]
        self.assertTrue(any(t is None for t in times), "no still-image face fixture")
        self.assertTrue(
            any(t is not None for t in times),
            "no face fixture with a frame_time, so the half of the encoding that "
            "renders a RationalTime is never exercised against a real record",
        )

    def test_the_null_frame_time_field_cannot_be_confused_with_a_real_one(self):
        """A still contributes an empty field. That is unambiguous only because
        a rendered time always contains a '/', so assert it does."""
        for entry in self._face_entries():
            record = _fixture(entry)
            fields = _preimage_of_record(record).decode("utf-8").split(_UNIT_SEPARATOR)
            self.assertEqual(6, len(fields), entry["path"])
            if record.get("frame_time") is None:
                self.assertEqual("", fields[2], entry["path"])
            else:
                self.assertIn("/", fields[2], entry["path"])

    def test_rotation_does_not_silently_collapse_two_boxes(self):
        """`rotation_deg` is outside the tuple, so the schema has to keep it at
        zero. Without that constraint two different boxes share one id."""
        validator = _validator("face-record")
        record = _fixture(_entries("valid", "face-record")[0])
        record["detection"]["bbox"]["rotation_deg"] = 30
        errors = list(validator.iter_errors(record))
        self.assertTrue(
            errors, "a rotated detection box validated; face_id cannot distinguish it"
        )


class TestFaceIdVectors(unittest.TestCase):
    """The golden input -> face_id table issue #34 asked for.

    Both columns are checked: the exact UTF-8 pre-image and the digest over it.
    The pre-image is the useful one on a failure -- a digest mismatch says only
    that something diverged, while a pre-image mismatch names the field.
    """

    def test_the_table_declares_the_constants_the_schema_states(self):
        self.assertEqual(31, FACE_ID_VECTORS["separator"]["codepoint"])
        self.assertEqual(_FACE_ID_DOMAIN, FACE_ID_VECTORS["domain_tag"])
        self.assertEqual(_BBOX_QUANTUM, FACE_ID_VECTORS["bbox_quantum"])
        self.assertEqual("half away from zero", FACE_ID_VECTORS["bbox_rounding"])

    def test_every_vector_states_what_it_is_for(self):
        self.assertTrue(FACE_ID_VECTORS["vectors"])
        for vector in FACE_ID_VECTORS["vectors"]:
            with self.subTest(vector=vector["name"]):
                self.assertTrue(vector.get("why"), "a vector needs a stated purpose")

    def test_every_vector_reproduces_its_preimage(self):
        """Compared as decoded text, not as hex.

        A hex diff of two 100-byte strings tells a reader nothing about which
        field moved; the decoded form shows the separators and the changed
        field directly."""
        self.maxDiff = None
        for vector in FACE_ID_VECTORS["vectors"]:
            with self.subTest(vector=vector["name"]):
                got = _face_id_preimage(
                    media_id=vector["input"]["media_id"],
                    frame_time=vector["input"]["frame_time"],
                    bbox=vector["input"]["bbox"],
                    model_id=vector["input"]["detector"]["model_id"],
                    version=vector["input"]["detector"]["version"],
                )
                self.assertEqual(
                    bytes.fromhex(vector["preimage_utf8_hex"]).decode("utf-8"),
                    got.decode("utf-8"),
                    "the committed pre-image is not what this encoding produces",
                )

    def test_every_vector_reproduces_its_digest(self):
        try:
            from blake3 import blake3
        except ImportError:
            self.skipTest("blake3 is not installed")

        for vector in FACE_ID_VECTORS["vectors"]:
            with self.subTest(vector=vector["name"]):
                self.assertEqual(
                    vector["face_id"],
                    blake3(bytes.fromhex(vector["preimage_utf8_hex"])).hexdigest(),
                )

    def test_an_integral_time_written_as_a_float_gives_the_same_id(self):
        """The failure this encoding exists to prevent: Python writes 1.0 where
        JavaScript writes 1, and the same frame gets two ids."""
        vectors = {v["name"]: v for v in FACE_ID_VECTORS["vectors"]}
        as_int = vectors["video-integral-time-as-int"]
        as_float = vectors["video-integral-time-as-float"]
        self.assertIsInstance(as_float["input"]["frame_time"]["value"], float)
        self.assertNotIsInstance(as_int["input"]["frame_time"]["value"], float)
        self.assertEqual(as_int["face_id"], as_float["face_id"])
        self.assertEqual(as_int["preimage_utf8_hex"], as_float["preimage_utf8_hex"])

    def test_the_half_quantum_vector_disagrees_with_bankers_rounding(self):
        """If it did not, the vector would not be testing the rounding rule."""
        vector = {v["name"]: v for v in FACE_ID_VECTORS["vectors"]}[
            "bbox-on-the-half-quantum"
        ]
        box = vector["input"]["bbox"]
        canonical = [_quantise(box[axis]) for axis in ("x", "y", "w", "h")]
        bankers = [round(float(box[axis]) * _BBOX_QUANTUM) for axis in ("x", "y", "w", "h")]
        self.assertNotEqual(
            canonical,
            bankers,
            "this vector rounds the same way under both rules, so it does not "
            "pin the rule it was added to pin",
        )

    def test_a_detector_change_moves_the_id(self):
        vectors = {v["name"]: v for v in FACE_ID_VECTORS["vectors"]}
        base = vectors["still-frame-time-null"]["face_id"]
        self.assertNotEqual(base, vectors["detector-version-bump-issues-a-new-id"]["face_id"])
        self.assertNotEqual(base, vectors["detector-swap-issues-a-new-id"]["face_id"])

    def test_provenance_that_is_outside_the_tuple_stays_outside_it(self):
        """weights_blake3, config_blake3, runtime and precision are recorded on
        the detector but must not reach the id. The fixtures are the evidence:
        two of them carry different runtimes for one detection and must still
        share an id, so assert the vector inputs simply have nowhere to put
        them."""
        for vector in FACE_ID_VECTORS["vectors"]:
            with self.subTest(vector=vector["name"]):
                self.assertEqual(
                    {"model_id", "version"}, set(vector["input"]["detector"])
                )


class TestJobIdentityIsComputable(unittest.TestCase):
    """`job_id` and `params_digest` must RECOMPUTE, on every job fixture.

    They did not. Four of the six job fixtures carried digests that matched no
    implementation of the rule their own descriptions state, because they had
    been typed rather than computed -- the third time this repo has found that
    (issue #26's span_id, issue #34's face_id, here). It matters more for a job
    than for a record: `job_id` IS the idempotency key, so a fixture pinning a
    made-up one is a fixture that cannot detect the failure it exists for,
    which is a worker redoing completed work or skipping work it never did.

    The encoding is stated on `job_id` and `params_digest` in the schema, and
    reimplemented here rather than imported from the runner -- a guard that
    calls the producer agrees with the producer by construction.
    """

    def _jobs(self):
        return [(entry["path"], _fixture(entry)) for entry in _entries(schema="job-spec")]

    def _params_digest(self, job: dict) -> str:
        from blake3 import blake3

        return blake3(_canonical_json(job.get("params") or {})).hexdigest()

    def _job_id(self, job: dict) -> str:
        from blake3 import blake3

        inputs = job["inputs"]
        ids = sorted(
            list(inputs.get("media_ids") or []) + list(inputs.get("moment_ids") or [])
        )
        joined = _UNIT_SEPARATOR.join(
            [
                job["job_type"],
                ",".join(ids),
                inputs.get("source_locator_digest") or "",
                job["params_digest"],
                job.get("scope") or "",
            ]
        )
        return blake3(joined.encode("utf-8")).hexdigest()

    def test_there_are_jobs_to_check(self):
        self.assertTrue(self._jobs(), "this guard has nothing to guard")

    def test_every_params_digest_recomputes(self):
        try:
            import blake3  # noqa: F401
        except ImportError:
            self.skipTest("blake3 is not installed")

        for path, job in self._jobs():
            with self.subTest(fixture=path):
                self.assertEqual(self._params_digest(job), job["params_digest"])

    def test_every_job_id_recomputes(self):
        try:
            import blake3  # noqa: F401
        except ImportError:
            self.skipTest("blake3 is not installed")

        for path, job in self._jobs():
            with self.subTest(fixture=path):
                self.assertEqual(self._job_id(job), job["job_id"])

    def test_a_null_locator_and_an_empty_one_are_the_same_bytes(self):
        """Stated in the schema because it is the field most likely to be
        rendered two ways, and a job with no locator getting two ids means the
        same work runs twice."""
        try:
            import blake3  # noqa: F401
        except ImportError:
            self.skipTest("blake3 is not installed")

        base = {
            "job_type": "rank_media",
            "inputs": {"media_ids": [], "moment_ids": [], "source_locator_digest": None},
            "params_digest": "0" * 64,
            "scope": "library:default",
        }
        empty = json.loads(json.dumps(base))
        empty["inputs"]["source_locator_digest"] = ""
        self.assertEqual(self._job_id(base), self._job_id(empty))

    def test_the_model_pins_that_affect_identity_are_the_pins_recorded(self):
        """`inputs.models` is provenance; `params["model_pins"]` is identity.
        A pin in one and not the other is a model swap that reuses the previous
        result -- the fixture is where that stays visible."""
        for path, job in self._jobs():
            declared = job["inputs"].get("models") or []
            pinned = (job.get("params") or {}).get("model_pins")
            if not declared and pinned is None:
                continue
            with self.subTest(fixture=path):
                self.assertEqual(pinned or [], declared)

    def test_the_analyze_image_job_exists_and_is_resumable_mid_pipeline(self):
        """Issue #42 named this exact scenario: killed after image embeddings
        are stored. Without a fixture for it, nothing pins what resumption is
        supposed to look like."""
        found = [
            job for _path, job in self._jobs() if job["job_type"] == "analyze_image"
        ]
        self.assertTrue(found, "no golden analyze_image job (issue #42)")
        job = found[0]
        self.assertTrue(job["checkpoint"]["resumable"])
        cursor = json.loads(job["checkpoint"]["cursor"])
        self.assertIn(cursor["step"], job["params"]["steps"])
        self.assertEqual(
            [],
            job["checkpoint"]["completed_input_ids"],
            "completion for this job lives in MediaRecord.processing.stages; a "
            "second answer here could disagree with the records",
        )
        self.assertTrue(job["checkpoint"]["partial_output_ids"])
        self.assertEqual(
            "classical_quality",
            job["params"]["steps"][0],
            "the cheap measure that makes the required quality fields exist runs first",
        )
        self.assertTrue(job["params"]["model_pins"], "no model pins in the identity")
        self.assertEqual({"media_record", "face_record"},
                         {output["kind"] for output in job["outputs"]})


class TestScanIdentity(unittest.TestCase):
    """Two scans of different roots must be two jobs.

    Recomputed from the fixtures rather than compared to a stored constant, so
    the property is demonstrated rather than asserted.
    """

    def setUp(self):
        self.jobs = [
            _fixture(e)
            for e in _entries("valid", "job-spec")
            if "scan-source-root" in e["path"]
        ]
        self.assertEqual(2, len(self.jobs))

    def test_the_two_scans_differ_only_in_their_source_roots(self):
        a, b = self.jobs
        self.assertEqual(a["params_digest"], b["params_digest"])
        self.assertEqual(a["scope"], b["scope"])
        self.assertEqual(a["job_type"], b["job_type"])
        self.assertEqual([], a["inputs"]["media_ids"])
        self.assertEqual([], b["inputs"]["media_ids"])
        self.assertNotEqual(a["inputs"]["source_paths"], b["inputs"]["source_paths"])

    def test_distinct_roots_yield_distinct_locator_digests_and_job_ids(self):
        a, b = self.jobs
        self.assertNotEqual(
            a["inputs"]["source_locator_digest"], b["inputs"]["source_locator_digest"]
        )
        self.assertNotEqual(
            a["job_id"],
            b["job_id"],
            "without the locator digest these two scans collide and the second drive "
            "is silently treated as already imported",
        )

    def test_the_locator_digest_is_reproducible_from_the_paths(self):
        """BLAKE3, not SHA-256.

        These fixtures originally used SHA-256 and this test asserted it.
        Codex's Rust ingest recomputes the digest and refused a real job, which
        is how the mismatch surfaced -- the contract says BLAKE3 (`Blake3Hash`),
        so the fixtures were wrong and the implementation was right. Skips
        rather than fails where the blake3 package is absent, since the contract
        tests must keep running on a bare Python.
        """
        import unicodedata

        try:
            import blake3
        except ImportError:
            self.skipTest("blake3 not installed; digest reproduction not checked here")

        for job in self.jobs:
            paths = job["inputs"]["source_paths"]
            canonical = sorted(
                unicodedata.normalize("NFC", p.rstrip("/")) for p in paths
            )
            expected = blake3.blake3("\x00".join(canonical).encode()).hexdigest()
            self.assertEqual(
                expected,
                job["inputs"]["source_locator_digest"],
                "the fixture's digest is not the documented BLAKE3 canonicalisation",
            )


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
             "PrefEvent", "SafetyClearance"},
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
        # The generated models express types, required-ness and enums, but not
        # if/then constraints -- those are asserted against jsonschema above and
        # re-checked by the semantic invariants. The manifest says which rule each
        # fixture rests on, so this stays honest as fixtures are added.
        for entry in _entries("schema-invalid"):
            if entry.get("rejected_by") != "schema_type":
                continue
            with self.subTest(fixture=entry["path"]):
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


PHASH_VECTORS = json.loads(
    (CONTRACTS / "vectors" / "phash-dct-64-v2.json").read_text(encoding="utf-8")
)

_PHASH_SIDE = 32
_PHASH_BLOCK = 8


def _phash_coefficient_order() -> list[tuple[int, int]]:
    """(u, v) in bit order, most significant first.

    Reimplemented from the schema text rather than imported from anywhere. The
    only producer lives in Rust; a Python copy of it would agree with it by
    construction and would show nothing.
    """
    order = [
        (u, v)
        for u in range(_PHASH_BLOCK)
        for v in range(_PHASH_BLOCK)
        if (u, v) != (0, 0)
    ]
    order.append((0, _PHASH_BLOCK))
    return order


def _phash_coefficient(luma: list[list[int]], u: int, v: int) -> float:
    total = 0.0
    for x in range(_PHASH_SIDE):
        cos_x = math.cos((2 * x + 1) * u * math.pi / (2 * _PHASH_SIDE))
        for y in range(_PHASH_SIDE):
            cos_y = math.cos((2 * y + 1) * v * math.pi / (2 * _PHASH_SIDE))
            total += luma[y][x] * cos_x * cos_y
    return total


def _phash_coefficients(luma: list[list[int]]) -> list[float]:
    return [_phash_coefficient(luma, u, v) for u, v in _phash_coefficient_order()]


def _phash_digest(values: list[float]) -> str:
    threshold = sorted(values)[len(values) // 2]
    bits = 0
    for index, value in enumerate(values):
        if value > threshold:
            bits |= 1 << (63 - index)
    return f"{bits:016x}"


def _phash_luma(vector: dict) -> list[list[int]]:
    rows = vector["luma_rows_hex"]
    if len(rows) != _PHASH_SIDE:
        raise AssertionError(f"{vector['name']} carries {len(rows)} rows")
    return [
        [int(row[index * 2 : index * 2 + 2], 16) for index in range(_PHASH_SIDE)]
        for row in rows
    ]


class TestPerceptualHashEncoding(unittest.TestCase):
    """`phash-dct-64-v2`, recomputed here in a second language (issue #14).

    The producer is Rust and there is only one of it, so a golden table is the
    only thing that can show the encoding is a contract rather than whatever
    that one implementation happens to do. `workers/ingest` recomputes the same
    file on its side.
    """

    def test_the_table_declares_the_constants_the_schema_states(self):
        self.assertEqual("phash-dct-64-v2", PHASH_VECTORS["algorithm"])
        self.assertEqual(_PHASH_SIDE, PHASH_VECTORS["side"])
        self.assertIn("(0, 0) omitted", PHASH_VECTORS["coefficient_order"])
        self.assertIn("index 32", PHASH_VECTORS["threshold"])

    def test_the_algorithm_is_a_value_the_schema_permits(self):
        enum = DOCUMENTS["common.schema.json"]["$defs"]["PerceptualHash"][
            "properties"
        ]["algorithm"]["enum"]
        self.assertIn(PHASH_VECTORS["algorithm"], enum)
        self.assertIn(
            "phash-dct-64",
            enum,
            "the superseded value must stay: records written before the change "
            "are still valid and must not start failing validation",
        )

    def test_every_vector_states_what_it_is_for(self):
        self.assertGreaterEqual(len(PHASH_VECTORS["vectors"]), 6)
        for vector in PHASH_VECTORS["vectors"]:
            with self.subTest(vector=vector["name"]):
                self.assertTrue(vector.get("why"), "a vector needs a stated purpose")

    def test_every_vector_reproduces_its_digest(self):
        for vector in PHASH_VECTORS["vectors"]:
            with self.subTest(vector=vector["name"]):
                values = _phash_coefficients(_phash_luma(vector))
                self.assertEqual(
                    vector["phash_dct_64_v2_hex"],
                    _phash_digest(values),
                    "the committed digest is not what this encoding produces",
                )

    def test_no_vector_is_decided_by_rounding(self):
        """The margin that makes these vectors portable at all.

        A flat field, a linear ramp and a checkerboard are separable in x and y,
        which drives most of the 64 coefficients to within 1e-10 of zero and puts
        the threshold inside that cloud -- so the digest would be a property of
        summation order. Every committed vector has to clear that by orders of
        magnitude, checked here rather than trusted from the generator.
        """
        for vector in PHASH_VECTORS["vectors"]:
            with self.subTest(vector=vector["name"]):
                values = _phash_coefficients(_phash_luma(vector))
                self.assertGreater(min(abs(value) for value in values), 1e-3)
                ordered = sorted(values)
                gap = min(ordered[32] - ordered[31], ordered[33] - ordered[32])
                self.assertGreater(gap, 1e-6, "the threshold sits on a near-tie")

    def test_at_most_thirty_one_bits_can_be_set(self):
        """The threshold is drawn from the tuple and the comparison is strict,
        so a digest with 32 or more bits set could not have been produced by
        this algorithm. Cheap enough to be worth checking on invented values."""
        for vector in PHASH_VECTORS["vectors"]:
            with self.subTest(vector=vector["name"]):
                popcount = bin(int(vector["phash_dct_64_v2_hex"], 16)).count("1")
                self.assertEqual(popcount, vector["popcount"])
                self.assertLessEqual(popcount, 31)

    def test_a_brightness_offset_does_not_move_the_digest(self):
        """C(0, 0) is the only coefficient a constant offset touches, and it is
        no longer hashed."""
        vectors = {v["name"]: v for v in PHASH_VECTORS["vectors"]}
        base = _phash_luma(vectors["noise"])
        lifted = _phash_luma(vectors["noise-plus-64"])
        self.assertEqual(
            [[value + 64 for value in row] for row in base],
            lifted,
            "the vector pair is no longer an offset of one another",
        )
        self.assertEqual(
            vectors["noise"]["phash_dct_64_v2_hex"],
            vectors["noise-plus-64"]["phash_dct_64_v2_hex"],
        )

    def test_the_transposed_vector_pins_which_axis_u_belongs_to(self):
        """u pairs with x, the column index. Without the pair, an implementation
        that swapped them would reproduce every single-matrix vector."""
        vectors = {v["name"]: v for v in PHASH_VECTORS["vectors"]}
        base = _phash_luma(vectors["noise"])
        transposed = _phash_luma(vectors["noise-transposed"])
        self.assertEqual(
            [[base[x][y] for x in range(_PHASH_SIDE)] for y in range(_PHASH_SIDE)],
            transposed,
        )
        self.assertNotEqual(
            vectors["noise"]["phash_dct_64_v2_hex"],
            vectors["noise-transposed"]["phash_dct_64_v2_hex"],
        )

    def test_the_appended_coefficient_is_the_bit_that_moves(self):
        """`photographic-vertical-8` differs from `photographic` by a cosine at
        exactly the frequency of the appended (0, 8) coefficient. An
        implementation that dropped DC and shipped 63 bits, or appended a
        different coefficient, cannot reproduce both."""
        vectors = {v["name"]: v for v in PHASH_VECTORS["vectors"]}
        plain = int(vectors["photographic"]["phash_dct_64_v2_hex"], 16)
        waved = int(vectors["photographic-vertical-8"]["phash_dct_64_v2_hex"], 16)
        self.assertNotEqual(plain & 1, waved & 1, "the last bit did not move")

    def test_the_superseded_algorithm_had_a_constant_top_bit(self):
        """The defect issue #14 reported, kept as committed evidence rather than
        as a claim in a commit message. C(0, 0) is the sum of every sample, so it
        is above a threshold drawn from the other coefficients for every input
        that is not exactly black -- and it occupied bit 63."""
        legacy = [
            int(vector["legacy_phash_dct_64_hex"], 16)
            for vector in PHASH_VECTORS["vectors"]
        ]
        self.assertTrue(
            all(value >> 63 == 1 for value in legacy),
            "every legacy digest should have had its top bit set",
        )
        current = [
            int(vector["phash_dct_64_v2_hex"], 16) for vector in PHASH_VECTORS["vectors"]
        ]
        self.assertNotEqual(
            len({value >> 63 for value in current}),
            1,
            "the top bit is still constant across the vectors",
        )

    def test_every_fixture_digest_could_have_been_produced(self):
        """Fixture hashes are stand-ins -- there is no image behind a fixture to
        hash -- but a stand-in must still be a value the algorithm can emit.
        Three of them were not: they set 32 or 33 bits, which this threshold
        rule cannot do."""
        for entry in _entries("valid"):
            fixture = _fixture(entry)
            for hash_object in _walk_perceptual_hashes(fixture):
                if hash_object.get("algorithm") != "phash-dct-64-v2":
                    continue
                with self.subTest(fixture=entry["path"], hex=hash_object["hex"]):
                    self.assertEqual(64, hash_object["bits"])
                    self.assertLessEqual(
                        bin(int(hash_object["hex"], 16)).count("1"),
                        31,
                        "no input produces this digest",
                    )


def _walk_perceptual_hashes(node):
    """Every PerceptualHash-shaped object anywhere in a fixture."""
    if isinstance(node, dict):
        if {"algorithm", "bits", "hex"} <= set(node):
            yield node
        for value in node.values():
            yield from _walk_perceptual_hashes(value)
    elif isinstance(node, list):
        for value in node:
            yield from _walk_perceptual_hashes(value)


if __name__ == "__main__":
    unittest.main()
