"""Tests for the reel planner.

WHAT THESE TESTS ARE TRYING TO CATCH

Every defect this repo has found in four review rounds was silent: a plausible
number, no exception. So the assertions here are mostly about VALUES and
INVARIANTS, not about "it returned something":

  * the EDL validates against contracts/schemas/edl.schema.json, including
    additionalProperties:false, so an invented field fails;
  * three beat-alignment errors are checked against the numbers in the golden
    fixture contracts/fixtures/edl/valid/reel-beat-locked-vertical-reframe.json,
    which was authored by hand from the same grid -- an independently derived
    expectation, not one read back out of the code;
  * BLAKE3 is checked against the official test vectors, because a digest that
    is 64 hex characters of the wrong algorithm looks exactly like a correct
    one;
  * canonical JSON is checked against RFC 8785's number rules, because
    Python's `1.0` and JavaScript's `1` hash differently and nothing raises;
  * timeline tiling is checked by re-deriving the timeline from durations,
    because an off-by-one frame is invisible in every other assertion.
"""

from __future__ import annotations

import copy
import json
import math
import sys
import unittest
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = PACKAGE_ROOT.parent.parent
SCHEMA_DIR = REPO_ROOT / "contracts" / "schemas"
MOMENT_FIXTURES = REPO_ROOT / "contracts" / "fixtures" / "moment-record" / "valid"

# The CI runner invokes `python3 -m unittest discover -s tests`, which puts the
# tests directory on sys.path and not the package. Same shape as media-db's
# suite, for the same reason.
sys.path.insert(0, str(PACKAGE_ROOT))

from memory_engine_story import reel  # noqa: E402
from memory_engine_story.reel import (  # noqa: E402
    AmbientSettings,
    Beat,
    BeatGrid,
    ColorSettings,
    MusicLicense,
    MusicTrack,
    ReelRequest,
    ReframeSettings,
    RenderTarget,
    SafeTrim,
    SelectedMoment,
    SnapPoint,
    SourceMedia,
    SubjectSample,
    VariantInfo,
    Word,
    blake3_hex,
    canonical_json,
    color_pipeline_check,
    encode_profile_for,
    moment_from_record,
    plan_reel,
)

try:  # jsonschema is a CI dependency; the rest of the suite must not need it.
    from jsonschema import Draft202012Validator
    from referencing import Registry, Resource

    _HAVE_JSONSCHEMA = True
except ImportError:  # pragma: no cover
    _HAVE_JSONSCHEMA = False

# 59.94, the exact NTSC rational. Used throughout so the tests exercise the
# rate the contract calls out rather than a friendly 30.0 that hides rounding.
RATE = 59.94005994005994

RIDE_MEDIA_ID = "a371bd849cc440490b2013581e0e77ff53db9a984fd9d37ceddbeaffefb96cf2"
MUSIC_MEDIA_ID = "22fbf421bb5540190572a9439a138fc77ad8c3c13b3a87be34f96a965da222ce"

# contracts#55. Read from the MediaRecord fixture that defines the assembly
# rather than copied here, so a change to the chapter set breaks in one place
# and the two documents cannot drift into disagreeing about one recording.
_SPAN_FIXTURE = json.loads(
    (
        REPO_ROOT
        / "contracts"
        / "fixtures"
        / "media-record"
        / "valid"
        / "video-gopro-span-assembly.json"
    ).read_text(encoding="utf-8")
)
RIDE_MEMBER_IDS = tuple(_SPAN_FIXTURE["span"]["member_media_ids"])
RIDE_CONTINUITY = _SPAN_FIXTURE["span"]["continuity"]

# The golden EDL fixture's grid: 32 bars of 128bpm laid over 899 frames at
# 59.94. Recomputing it here (rather than reading the fixture) is deliberate --
# the alignment errors asserted below then come from two independent
# derivations that have to agree.
BEAT_INTERVAL = 899.100899 / 32


def _edl_validator():
    documents = {
        path.name: json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(SCHEMA_DIR.glob("*.schema.json"))
    }
    registry = Registry().with_resources(
        [(name, Resource.from_contents(doc)) for name, doc in documents.items()]
    )
    return Draft202012Validator(documents["edl.schema.json"], registry=registry)


def _assert_schema_valid(case: unittest.TestCase, edl: dict) -> None:
    if not _HAVE_JSONSCHEMA:  # pragma: no cover
        case.skipTest("jsonschema/referencing not installed")
    errors = sorted(_edl_validator().iter_errors(edl), key=lambda e: list(e.path))
    case.assertEqual(
        [], [f"{list(e.path)}: {e.message}" for e in errors], "EDL failed its schema"
    )


def beat_grid(count: int = 33, **kwargs) -> BeatGrid:
    beats = tuple(
        Beat(
            index=i,
            time=i * BEAT_INTERVAL,
            is_downbeat=(i % 4 == 0),
            bar=i // 4,
            beat_in_bar=(i % 4) + 1,
            strength=0.95 if i % 4 == 0 else 0.62,
            section="drop" if 16 <= i < 28 else "verse",
        )
        for i in range(count)
    )
    return BeatGrid(bpm=128.0, beats=beats, bpm_confidence=0.97, **kwargs)


def source_media(**kwargs) -> SourceMedia:
    defaults = dict(
        media_ref_id="src-ride",
        media_id=RIDE_MEDIA_ID,
        available_start=3025080,
        available_duration=61593,
        aspect_ratio=(16, 9),
        is_span_assembly=True,
        member_media_ids=RIDE_MEMBER_IDS,
        continuity=RIDE_CONTINUITY,
        color_encoding="bt709",
        expected_frame_rate=RATE,
        label="GH01/GH02 1234 (assembled)",
    )
    defaults.update(kwargs)
    return SourceMedia(**defaults)


def music_media(**kwargs) -> SourceMedia:
    defaults = dict(
        media_ref_id="src-music",
        media_id=MUSIC_MEDIA_ID,
        available_start=0,
        available_duration=9590,
        aspect_ratio=(1, 1),
        media_kind="music",
        label="Ridgeline",
    )
    defaults.update(kwargs)
    return SourceMedia(**defaults)


def music_track(media: SourceMedia | None = None, **kwargs) -> MusicTrack:
    defaults = dict(
        media=media or music_media(),
        license=MusicLicense(
            provider="catalog_partner",
            license_type="royalty_free",
            cleared_for=("private_playback", "social_share", "commercial_use"),
            license_id="CAT-2026-004182",
            track_title="Ridgeline",
        ),
        source_start=1798,
        fade_out=36,
    )
    defaults.update(kwargs)
    return MusicTrack(**defaults)


def moment(
    tag: int,
    start: float,
    duration: float = 300,
    *,
    with_subject: bool = True,
    **kwargs,
) -> SelectedMoment:
    """A synthetic moment with two snap points, the later one stronger."""
    defaults = dict(
        moment_id=f"{tag:064x}",
        media_id=RIDE_MEDIA_ID,
        source_start=start,
        source_duration=duration,
        score=0.7,
        snap_points=(
            SnapPoint(time=start, kind="shot_boundary", strength=0.90, cut_direction="in"),
            SnapPoint(
                time=start + 30, kind="motion_onset", strength=0.95, cut_direction="both"
            ),
        ),
        safe_trim=SafeTrim(earliest_in=start, latest_out=start + duration),
    )
    if with_subject:
        defaults["subject_track"] = (
            SubjectSample(time=start, center_x=0.40, confidence=0.9),
            SubjectSample(time=start + duration / 2, center_x=0.62, confidence=0.9),
            SubjectSample(time=start + duration, center_x=0.50, confidence=0.8),
        )
    defaults.update(kwargs)
    return SelectedMoment(**defaults)


def request(**kwargs) -> ReelRequest:
    media = kwargs.pop("media", None)
    moments = kwargs.pop(
        "moments",
        (
            moment(0xA1, 3031860, hook_potential=0.88, motion_energy=0.9),
            moment(0xA2, 3040000, hook_potential=0.40, motion_energy=0.7),
            moment(0xA3, 3049200, hook_potential=0.55, emotional_peak=0.93, score=0.91),
            moment(0xA4, 3055440, hook_potential=0.30, motion_energy=0.15),
        ),
    )
    music = kwargs.pop("music", music_track())
    if media is None:
        media = (source_media(),) if music is None else (source_media(), music.media)
    defaults = dict(
        rate=RATE,
        target=RenderTarget(
            destination="instagram_reel",
            resolution=(1080, 1920),
            aspect_ratio=(9, 16),
            target_duration=899,
            max_duration=5395,
            loudness_target_lufs=-14.0,
        ),
        media=media,
        moments=moments,
        beat_grid=kwargs.pop("beat_grid", beat_grid()),
        music=music,
        name="Ridge ride",
        seed=20260316,
        generated_at="2026-03-16T20:04:52+05:30",
        validated_at="2026-03-16T20:04:53+05:30",
    )
    defaults.update(kwargs)
    return ReelRequest(**defaults)


def video_clips(edl: dict) -> list[dict]:
    track = next(t for t in edl["tracks"] if t["kind"] == "video")
    return [item for item in track["items"] if item["item_type"] == "clip"]


def video_items(edl: dict) -> list[dict]:
    return next(t for t in edl["tracks"] if t["kind"] == "video")["items"]


# ---------------------------------------------------------------------------
# BLAKE3
# ---------------------------------------------------------------------------


class TestBlake3(unittest.TestCase):
    """The ids are typed BLAKE3 in the contract, so they had better be BLAKE3."""

    # From the reference implementation's test_vectors.json: input byte i is
    # i % 251. Any other 64-hex digest function passes every other test in this
    # file and fails here, which is the point.
    VECTORS = {
        0: "af1349b9f5f9a1a6a0404dea36dcc9499bcb25c9adc112b7cc9a93cae41f3262",
        1: "2d3adedff11b61f14c886e35afa036736dcd87a74d27b5c1510225d0f592e213",
        2: "7b7015bb92cf0b318037702a6cdd81dee41224f734684c2c122cd6359cb1ee63",
        3: "e1be4d7a8ab5560aa4199eea339849ba8e293d55ca0a81006726d184519e647f",
        63: "e9bc37a594daad83be9470df7f7b3798297c3d834ce80ba85d6e207627b7db7b",
        64: "4eed7141ea4a5cd4b788606bd23f46e212af9cacebacdc7d1f4c6dc7f2511b98",
        65: "de1e5fa0be70df6d2be8fffd0e99ceaa8eb6e8c93a63f2d8d1c30ecb6b263dee",
        1023: "10108970eeda3eb932baac1428c7a2163b0e924c9a9e25b35bba72b28f70bd11",
        1024: "42214739f095a406f3fc83deb889744ac00df831c10daa55189b5d121c855af7",
        1025: "d00278ae47eb27b34faecf67b4fe263f82d5412916c1ffd97c8cb7fb814b8444",
        2048: "e776b6028c7cd22a4d0ba182a8bf62205d2ef576467e838ed6f2529b85fba24a",
        2049: "5f4d72f40d7a5f82b15ca2b2e44b1de3c2ef86c426c95c1af0b6879522563030",
        3072: "b98cb0ff3623be03326b373de6b9095218513e64f1ee2edd2525c7ad1e5cffd2",
        4096: "015094013f57a5277b59d8475c0501042c0b642e531b0a1c8f58d2163229e969",
        5000: "ee78d92070de3df1c57c37002abf0a6b1a6589acdeef4d8ffac7cf3d9e8f2836",
        6144: "3e2e5b74e048f3add6d21faab3f83aa44d3b2278afb83b80b3c35164ebeca205",
        102400: "bc3e3d41a1146b069abffad3c0d44860cf664390afce4d9661f7902e7943e085",
    }

    @staticmethod
    def _input(length: int) -> bytes:
        return bytes(i % 251 for i in range(length))

    def test_pure_python_matches_the_official_vectors(self):
        for length, expected in sorted(self.VECTORS.items()):
            with self.subTest(length=length):
                self.assertEqual(expected, reel._blake3_pure(self._input(length)))

    def test_the_resolved_implementation_matches_the_official_vectors(self):
        # Whichever path is in use here -- the wheel or the fallback -- has to
        # produce the same bytes, or an id minted on a dev box would not match
        # one minted in CI.
        for length, expected in sorted(self.VECTORS.items()):
            with self.subTest(length=length):
                self.assertEqual(expected, blake3_hex(self._input(length)))

    def test_chunk_boundaries_do_not_shift_the_tree(self):
        # 1024 is the chunk size and the tree splits at powers of two; lengths
        # either side of each boundary are where a wrong split hides.
        for length in (1023, 1024, 1025, 2047, 2048, 2049, 4095, 4096, 4097):
            with self.subTest(length=length):
                data = bytes((i * 7 + 3) % 256 for i in range(length))
                self.assertEqual(reel._blake3_pure(data), blake3_hex(data))


# ---------------------------------------------------------------------------
# Canonical JSON
# ---------------------------------------------------------------------------


class TestCanonicalJson(unittest.TestCase):
    def test_number_formatting_follows_the_ecmascript_rule(self):
        # Left to Python's repr these would be 1.0, 1e-07, -0.0 and 1e+20 --
        # each of which hashes differently from what a JS or Rust JCS writer
        # produces, silently, on the digest a renderer compares against.
        cases = {
            1.0: "1",
            -0.0: "0",
            0.0: "0",
            1e-7: "1e-7",
            1e-6: "0.000001",
            1e20: "100000000000000000000",
            1e21: "1e+21",
            -1.5e-8: "-1.5e-8",
            0.31640625: "0.31640625",
            RATE: "59.94005994005994",
            112: "112",
            -3: "-3",
        }
        for value, expected in cases.items():
            with self.subTest(value=value):
                self.assertEqual(expected, reel._es_number(value))

    def test_keys_are_sorted_and_output_is_compact(self):
        self.assertEqual(
            b'{"a":1,"b":[1,2],"z":null}',
            canonical_json({"z": None, "b": [1, 2], "a": 1}),
        )

    def test_nested_objects_sort_too(self):
        self.assertEqual(
            b'{"outer":{"a":true,"b":false}}',
            canonical_json({"outer": {"b": False, "a": True}}),
        )

    def test_booleans_are_not_numbers(self):
        # bool is an int subclass in Python; a naive isinstance(x, int) writes
        # `1` where the contract says `true`.
        self.assertEqual(b'{"flag":true}', canonical_json({"flag": True}))
        with self.assertRaises(TypeError):
            reel._es_number(True)

    def test_non_finite_numbers_are_refused(self):
        for value in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    canonical_json({"x": value})

    def test_strings_are_escaped(self):
        self.assertEqual(
            b'{"k":"a\\"b\\\\c\\nd\\u0001"}', canonical_json({"k": 'a"b\\c\nd\x01'})
        )


class TestRounding(unittest.TestCase):
    def test_half_up_is_not_floor_plus_a_half(self):
        # floor(0.49999999999999994 + 0.5) == 1, because the addition rounds
        # up first. One frame of drift per cut is invisible until the whole
        # reel is off the beat.
        self.assertEqual(0, reel._round_half_up(0.49999999999999994))
        self.assertEqual(1, math.floor(0.49999999999999994 + 0.5))

    def test_half_up_is_not_bankers_rounding(self):
        self.assertEqual(1, reel._round_half_up(0.5))
        self.assertEqual(3, reel._round_half_up(2.5))  # round() gives 2
        self.assertEqual(-3, reel._round_half_up(-2.5))

    def test_milliseconds_round_away_from_zero(self):
        self.assertEqual(-6.4667, reel._round_ms(-6.46665))
        self.assertEqual(6.4667, reel._round_ms(6.46665))


# ---------------------------------------------------------------------------
# The plan as a whole
# ---------------------------------------------------------------------------


class TestPlanShape(unittest.TestCase):
    def setUp(self):
        self.plan = plan_reel(request())
        self.edl = self.plan.edl

    def test_edl_validates_against_the_contract_schema(self):
        _assert_schema_valid(self, self.edl)

    def test_no_undeclared_top_level_fields(self):
        # Same guarantee as additionalProperties:false, asserted without
        # jsonschema so it still holds on a bare Python.
        self.assertEqual(
            {
                "schema_version",
                "edl_id",
                "name",
                "kind",
                "rate",
                "global_start_time",
                "target",
                "media_refs",
                "tracks",
                "reframe_tracks",
                "audio_plan",
                "beat_grid",
                "story_arc",
                "color_pipeline",
                "variant",
                "determinism",
                "validation",
                "otio",
            },
            set(self.edl),
        )

    def test_it_passes_its_own_validation(self):
        self.assertEqual("pass", self.plan.status)
        failures = [c for c in self.edl["validation"]["checks"] if not c["passed"]]
        self.assertEqual([], failures)

    def test_every_moment_placed_is_traceable_to_its_moment_id(self):
        placed = {clip["moment_id"] for clip in video_clips(self.edl)}
        self.assertTrue(placed)
        self.assertLessEqual(placed, {m.moment_id for m in request().moments})

    def test_clips_reference_declared_media_refs_only(self):
        declared = {ref["media_ref_id"] for ref in self.edl["media_refs"]}
        for track in self.edl["tracks"]:
            for item in track["items"]:
                if item["item_type"] == "clip":
                    self.assertIn(item["media_ref_id"], declared)


class TestHalfOpenTiling(unittest.TestCase):
    def test_clips_tile_the_timeline_with_no_off_by_one_frame(self):
        edl = plan_reel(request()).edl
        for track in edl["tracks"]:
            cursor = 0
            for item in track["items"]:
                if item["item_type"] == "transition":
                    continue
                if item["item_type"] == "gap":
                    cursor += item["duration"]["value"]
                    continue
                start = item["timeline_range"]["start_time"]["value"]
                self.assertEqual(cursor, start, f"{item['clip_id']} does not butt up")
                cursor += item["timeline_range"]["duration"]["value"]

    def test_realised_duration_is_the_sum_of_the_clip_durations(self):
        plan = plan_reel(request())
        clips = video_clips(plan.edl)
        self.assertEqual(
            plan.duration_frames,
            sum(c["timeline_range"]["duration"]["value"] for c in clips),
        )

    def test_source_and_timeline_durations_agree_without_a_time_effect(self):
        for clip in video_clips(plan_reel(request()).edl):
            self.assertIsNone(clip["time_effect"])
            self.assertEqual(
                clip["source_range"]["duration"]["value"],
                clip["timeline_range"]["duration"]["value"],
            )


class TestBeatLock(unittest.TestCase):
    def test_alignment_errors_match_the_golden_fixture(self):
        # contracts/fixtures/edl/valid/reel-beat-locked-vertical-reframe.json
        # records -6.4667ms on beat 4, +3.75ms on beat 8 and -2.7167ms on beat
        # 12 for this grid. It was authored by hand; if the planner disagrees,
        # one of the two is wrong and both are checkable.
        plan = plan_reel(request())
        errors = {
            clip["beat_lock"]["beat_index"]: clip["beat_lock"]["alignment_error_ms"]
            for clip in video_clips(plan.edl)
        }
        self.assertEqual(0.0, errors[0])
        self.assertEqual(-6.4667, errors[4])
        self.assertEqual(3.75, errors[8])
        self.assertEqual(-2.7167, errors[12])

    def test_a_cut_before_the_beat_is_negative(self):
        # "Negative is early" (edl.schema.json#BeatLock). A flipped sign still
        # passes the tolerance gate, which is why the sign gets its own test.
        plan = plan_reel(request())
        clip = next(
            c for c in video_clips(plan.edl) if c["beat_lock"]["beat_index"] == 4
        )
        beat_time = 4 * BEAT_INTERVAL
        timeline_in = clip["timeline_range"]["start_time"]["value"]
        self.assertLess(timeline_in, beat_time)
        self.assertLess(clip["beat_lock"]["alignment_error_ms"], 0)

    def test_every_lock_is_inside_the_declared_tolerance(self):
        plan = plan_reel(request())
        tolerance = plan.edl["beat_grid"]["tolerance_ms"]
        for clip in video_clips(plan.edl):
            self.assertLessEqual(abs(clip["beat_lock"]["alignment_error_ms"]), tolerance)
        check = next(
            c
            for c in plan.edl["validation"]["checks"]
            if c["check_id"] == "beat_alignment_within_tolerance"
        )
        self.assertTrue(check["passed"])

    def test_beat_index_indexes_the_emitted_grid(self):
        plan = plan_reel(request())
        beats = plan.edl["beat_grid"]["beats"]
        for clip in video_clips(plan.edl):
            index = clip["beat_lock"]["beat_index"]
            self.assertEqual(index, beats[index]["index"])
            self.assertEqual(beats[index]["is_downbeat"], clip["beat_lock"]["is_downbeat"])

    def test_no_music_means_no_beat_lock_claimed(self):
        plan = plan_reel(request(music=None, beat_grid=None))
        self.assertIsNone(plan.edl["beat_grid"])
        for clip in video_clips(plan.edl):
            self.assertIsNone(clip["beat_lock"])
        _assert_schema_valid(self, plan.edl)

    def test_a_grid_that_starts_late_opens_on_an_explicit_gap(self):
        # The beat grid is in TIMELINE time. Sliding the clips back so the
        # first cut lands on frame 0 would move the picture and leave the
        # music where it was -- and every beat_lock would still read zero
        # error, because the planner would be measuring against its own shift.
        offset = 60
        late = BeatGrid(
            bpm=128.0,
            bpm_confidence=0.97,
            beats=tuple(
                Beat(
                    index=i,
                    time=offset + i * BEAT_INTERVAL,
                    is_downbeat=(i % 4 == 0),
                )
                for i in range(33)
            ),
        )
        plan = plan_reel(request(beat_grid=late))
        items = video_items(plan.edl)
        self.assertEqual("gap", items[0]["item_type"])
        self.assertEqual(offset, items[0]["duration"]["value"])
        first_clip = next(i for i in items if i["item_type"] == "clip")
        self.assertEqual(offset, first_clip["timeline_range"]["start_time"]["value"])
        # The cut still sits on its beat, measured in timeline time.
        self.assertEqual(0.0, first_clip["beat_lock"]["alignment_error_ms"])
        self.assertEqual(
            offset, plan.edl["beat_grid"]["beats"][0]["time"]["value"]
        )
        self.assertTrue(any("black" in note for note in plan.notes), plan.notes)
        self.assertEqual("pass", plan.status)
        _assert_schema_valid(self, plan.edl)

    def test_a_beat_grid_without_music_is_refused(self):
        with self.assertRaises(ValueError):
            request(music=None, media=(source_media(),))

    def test_beat_indices_must_match_their_array_positions(self):
        beats = (
            Beat(index=0, time=0.0, is_downbeat=True),
            Beat(index=7, time=30.0, is_downbeat=False),
        )
        with self.assertRaises(ValueError):
            BeatGrid(bpm=120.0, beats=beats)

    def test_beats_must_increase(self):
        beats = (
            Beat(index=0, time=10.0, is_downbeat=True),
            Beat(index=1, time=10.0, is_downbeat=False),
        )
        with self.assertRaises(ValueError):
            BeatGrid(bpm=120.0, beats=beats)


class TestTargetDuration(unittest.TestCase):
    def test_the_reel_ends_on_a_cut_point_not_on_the_target(self):
        plan = plan_reel(request())
        beat_frames = {reel._round_half_up(b["time"]["value"]) for b in plan.edl["beat_grid"]["beats"]}
        self.assertIn(plan.duration_frames, beat_frames)

    def test_missing_the_target_is_reported_rather_than_hidden(self):
        plan = plan_reel(request())
        self.assertNotEqual(899, plan.duration_frames)
        self.assertTrue(
            any("against a 899-frame target" in note for note in plan.notes),
            plan.notes,
        )

    def test_target_duration_records_what_was_asked_for(self):
        plan = plan_reel(request())
        self.assertEqual(899, plan.edl["target"]["target_duration"]["value"])

    def test_the_platform_ceiling_is_never_exceeded(self):
        # A 3-frame ceiling would need a cut before the first beat, which is
        # not a shorter reel but an impossible one.
        with self.assertRaises(ValueError):
            plan_reel(request(target=RenderTarget(
                destination="instagram_reel",
                resolution=(1080, 1920),
                aspect_ratio=(9, 16),
                target_duration=899,
                max_duration=3,
            )))

    def test_a_ceiling_shorter_than_the_target_wins(self):
        plan = plan_reel(
            request(
                target=RenderTarget(
                    destination="instagram_reel",
                    resolution=(1080, 1920),
                    aspect_ratio=(9, 16),
                    target_duration=899,
                    max_duration=200,
                )
            )
        )
        self.assertLessEqual(plan.duration_frames, 200)
        check = next(
            c
            for c in plan.edl["validation"]["checks"]
            if c["check_id"] == "duration_within_max"
        )
        self.assertTrue(check["passed"])

    def test_an_exactly_equidistant_target_takes_the_shorter_reel(self):
        # Target halfway between two grid points: 56.19 and 112.39 are both
        # 28.1 frames away from 84.29. The shorter one wins by rule, not by
        # whichever the sort happened to see first.
        grid = beat_grid()
        halfway = (grid.beats[2].time + grid.beats[3].time) / 2
        end = reel._end_index(reel._cut_grid(request(beat_grid=grid)), request(
            beat_grid=grid,
            target=RenderTarget(
                destination="instagram_reel",
                resolution=(1080, 1920),
                aspect_ratio=(9, 16),
                target_duration=halfway,
                max_duration=5395,
            ),
        ))
        self.assertEqual(2, end)


class TestSnapPoints(unittest.TestCase):
    def test_the_in_point_is_a_certified_snap_point(self):
        plan = plan_reel(request())
        by_id = {m.moment_id: m for m in request().moments}
        for clip in video_clips(plan.edl):
            source_moment = by_id[clip["moment_id"]]
            times = {s.time for s in source_moment.snap_points}
            self.assertIn(clip["source_range"]["start_time"]["value"], times)

    def test_the_strongest_usable_snap_point_wins(self):
        # Both snap points fit; the later one is stronger, so it is chosen
        # even though the earlier one leaves more tail.
        plan = plan_reel(request())
        clip = video_clips(plan.edl)[0]
        self.assertEqual("motion_onset", clip["beat_lock"]["snap_point_kind"])

    def test_an_out_only_snap_point_is_never_used_as_an_in_point(self):
        only_out = moment(
            0xB1,
            3031860,
            snap_points=(
                SnapPoint(
                    time=3031900, kind="speech_gap", strength=1.0, cut_direction="out"
                ),
            ),
        )
        plan = plan_reel(request(moments=(only_out, moment(0xB2, 3040000))))
        clip = next(
            c for c in video_clips(plan.edl) if c["moment_id"] == only_out.moment_id
        )
        # Falls back to the window start, and says so by reporting no snap kind.
        self.assertEqual(3031860, clip["source_range"]["start_time"]["value"])
        self.assertIsNone(clip["beat_lock"]["snap_point_kind"])

    def test_a_snap_point_too_late_to_fit_the_duration_is_skipped(self):
        late = moment(
            0xB3,
            3031860,
            duration=300,
            snap_points=(
                SnapPoint(time=3031860, kind="shot_boundary", strength=0.5, cut_direction="in"),
                SnapPoint(time=3032100, kind="motion_onset", strength=1.0, cut_direction="in"),
            ),
        )
        plan = plan_reel(request(moments=(late, moment(0xB4, 3040000))))
        clip = next(
            c for c in video_clips(plan.edl) if c["moment_id"] == late.moment_id
        )
        duration = clip["source_range"]["duration"]["value"]
        start = clip["source_range"]["start_time"]["value"]
        self.assertEqual(3031860, start)  # the strong one would overrun latest_out
        self.assertLessEqual(start + duration, 3032160)

    def test_a_snap_point_outside_the_moment_is_refused(self):
        with self.assertRaises(ValueError):
            moment(
                0xB5,
                3031860,
                snap_points=(
                    SnapPoint(time=9999999, kind="motion_onset", strength=1.0),
                ),
            )


class TestSpeechAndAudio(unittest.TestCase):
    def _speech_moment(self, **kwargs) -> SelectedMoment:
        start = 3049200
        return moment(
            0xC1,
            start,
            duration=210,
            emotional_peak=0.93,
            score=0.91,
            speech_ratio=0.31,
            safe_trim=SafeTrim(
                earliest_in=start,
                latest_out=start + 210,
                speech_safe_in=start + 84,
                speech_safe_out=start + 198,
                min_duration=45,
                preserve_audio_tail=True,
            ),
            words=(
                Word(start=start + 90, end=start + 108, text="did"),
                Word(start=start + 108, end=start + 122, text="you"),
                Word(start=start + 122, end=start + 146, text="see"),
                Word(start=start + 146, end=start + 180, text="that"),
            ),
            **kwargs,
        )

    def test_speech_clips_stay_inside_the_speech_safe_window(self):
        speech = self._speech_moment()
        plan = plan_reel(
            request(moments=(speech, moment(0xC2, 3031860, hook_potential=0.9)))
        )
        clip = next(
            c for c in video_clips(plan.edl) if c["moment_id"] == speech.moment_id
        )
        start = clip["source_range"]["start_time"]["value"]
        end = start + clip["source_range"]["duration"]["value"]
        self.assertGreaterEqual(start, 3049284)
        self.assertLessEqual(end, 3049398)

    def test_no_mid_word_cut_passes_and_is_reported(self):
        plan = plan_reel(
            request(
                moments=(
                    self._speech_moment(),
                    moment(0xC2, 3031860, hook_potential=0.9),
                    moment(0xC9, 3055440, motion_energy=0.05),
                )
            )
        )
        check = next(
            c
            for c in plan.edl["validation"]["checks"]
            if c["check_id"] == "no_mid_word_cut"
        )
        self.assertTrue(check["passed"])
        self.assertEqual("warning", check["severity"])

    def test_a_cut_landing_inside_a_word_is_flagged(self):
        # No speech-safe bounds, and the strongest snap point sits mid-word --
        # the exact case the speech-safe window normally prevents.
        start = 3049200
        bad = moment(
            0xC3,
            start,
            duration=210,
            emotional_peak=0.9,
            snap_points=(
                SnapPoint(time=start + 95, kind="motion_onset", strength=1.0, cut_direction="in"),
            ),
            safe_trim=SafeTrim(earliest_in=start, latest_out=start + 210),
            words=(Word(start=start + 90, end=start + 108, text="did"),),
        )
        plan = plan_reel(
            request(moments=(bad, moment(0xC4, 3031860, hook_potential=0.9)))
        )
        check = next(
            c
            for c in plan.edl["validation"]["checks"]
            if c["check_id"] == "no_mid_word_cut"
        )
        self.assertFalse(check["passed"])
        self.assertEqual("warn", plan.status)

    def test_music_ducks_under_speech_over_explicit_ranges(self):
        speech = self._speech_moment()
        plan = plan_reel(
            request(moments=(speech, moment(0xC2, 3031860, hook_potential=0.9)))
        )
        rules = plan.edl["audio_plan"]["ducking"]
        self.assertEqual(1, len(rules))
        rule = rules[0]
        self.assertEqual("music", rule["target"])
        # There is no `trigger` any more (contracts#54): the only value that
        # was ever deterministic was `explicit_ranges`, and the detection
        # triggers asked a renderer to analyse the mix, which would mix the
        # same EDL differently on a different build. Ranges are the rule.
        self.assertNotIn("trigger", rule)
        self.assertEqual(9.0, rule["reduction_db"])
        # The envelope contracts#54 defines: the ramp down ENDS at the range
        # start, so the declared range is fully ducked for its whole extent.
        self.assertEqual(60.0, rule["attack_ms"])
        self.assertEqual(320.0, rule["release_ms"])
        clip = next(
            c for c in video_clips(plan.edl) if c["moment_id"] == speech.moment_id
        )
        self.assertEqual(
            clip["timeline_range"]["start_time"]["value"],
            rule["ranges"][0]["start_time"]["value"],
        )

    def test_adjacent_speech_clips_merge_into_one_range(self):
        # Two speech clips that butt up: half-open ranges mean the second
        # starts on the frame the first ends, and two rules there would fight.
        speech_a = self._speech_moment()
        speech_b = moment(0xC5, 3031860, speech_ratio=0.5, emotional_peak=0.1)
        speech_c = moment(0xC6, 3040000, speech_ratio=0.5, hook_potential=0.9)
        plan = plan_reel(request(moments=(speech_a, speech_b, speech_c)))
        ranges = plan.edl["audio_plan"]["ducking"][0]["ranges"]
        self.assertEqual(1, len(ranges), "abutting speech ranges did not merge")
        self.assertEqual(0, ranges[0]["start_time"]["value"])
        self.assertEqual(plan.duration_frames, ranges[0]["duration"]["value"])

    def test_no_speech_means_no_ducking_rule(self):
        plan = plan_reel(request())
        self.assertEqual([], plan.edl["audio_plan"]["ducking"])

    def test_preserve_speech_off_suppresses_the_rule(self):
        plan = plan_reel(
            request(
                moments=(
                    self._speech_moment(),
                    moment(0xC2, 3031860, hook_potential=0.9),
                ),
                ambient=AmbientSettings(preserve_speech=False),
            )
        )
        self.assertEqual([], plan.edl["audio_plan"]["ducking"])

    def test_wind_beats_speech_when_a_clip_is_both(self):
        # Wind at 0.96 makes the "speech" unintelligible anyway; bringing the
        # ambient UP because a voice was detected amplifies the wind.
        howling = moment(
            0xCB, 3031860, noise_ratio=0.96, speech_ratio=0.5, hook_potential=0.9
        )
        plan = plan_reel(request(moments=(howling, moment(0xCC, 3040000))))
        clip = next(
            c for c in video_clips(plan.edl) if c["moment_id"] == howling.moment_id
        )
        self.assertEqual(-60.0, clip["audio"]["gain_db"])

    def test_wind_dominated_clips_have_their_ambient_pulled_down(self):
        windy = moment(0xC7, 3031860, noise_ratio=0.96, hook_potential=0.9)
        plan = plan_reel(request(moments=(windy, moment(0xC8, 3040000))))
        clip = next(
            c for c in video_clips(plan.edl) if c["moment_id"] == windy.moment_id
        )
        self.assertEqual(-60.0, clip["audio"]["gain_db"])
        # contracts#53: the level is stated exactly once, on the clip. The
        # AmbientPlan carries no gains at all now, so there is no second copy
        # for a renderer to sum with, override by, or silently prefer.
        self.assertEqual(
            {"high_pass"}, set(plan.edl["audio_plan"]["ambient"])
        )

    def test_an_l_cut_is_planned_when_the_audio_outlives_the_picture(self):
        # Three moments so the speech clip is not the last one: there is
        # nothing for a tail to run over at the end of a reel.
        speech = self._speech_moment()
        plan = plan_reel(
            request(
                moments=(
                    speech,
                    moment(0xC2, 3031860, hook_potential=0.9),
                    moment(0xCA, 3055440, motion_energy=0.05),
                )
            )
        )
        clip = next(
            c for c in video_clips(plan.edl) if c["moment_id"] == speech.moment_id
        )
        tail = clip["audio"]["audio_extends_past_out"]
        self.assertIsNotNone(tail)
        self.assertGreater(tail["value"], 0)
        source_end = (
            clip["source_range"]["start_time"]["value"]
            + clip["source_range"]["duration"]["value"]
        )
        self.assertLessEqual(source_end + tail["value"], 3049410)

    def test_the_music_cue_covers_the_whole_reel(self):
        """contracts#59: the bed is placed once, on a track. The cue carries the
        licence and points at that placement; it does not repeat it."""
        plan = plan_reel(request())
        cue = plan.edl["audio_plan"]["music"][0]
        music_track_block = next(
            t for t in plan.edl["tracks"] if t.get("role") == "music"
        )
        items = music_track_block["items"]
        self.assertEqual([i["clip_id"] for i in items], cue["clip_ids"])
        self.assertEqual(1, len(items))
        self.assertEqual(0, items[0]["timeline_range"]["start_time"]["value"])
        self.assertEqual(
            plan.duration_frames, items[0]["timeline_range"]["duration"]["value"]
        )
        self.assertEqual(1798, items[0]["source_range"]["start_time"]["value"])
        self.assertNotIn("timeline_range", cue)
        self.assertNotIn("loop", cue)

    def test_a_short_track_loops_and_says_so(self):
        short = music_media(available_duration=120)
        plan = plan_reel(
            request(
                music=music_track(media=short, source_start=0),
                media=(source_media(), short),
            )
        )
        cue = plan.edl["audio_plan"]["music"][0]
        self.assertGreater(len(cue["clip_ids"]), 1, "a loop is more than one placement")
        self.assertTrue(any("loops" in note for note in plan.notes), plan.notes)

    def test_an_uncleared_track_fails_the_licence_check(self):
        personal = music_track(
            license=MusicLicense(
                provider="user_supplied",
                license_type="personal_use_only",
                cleared_for=("private_playback",),
            )
        )
        plan = plan_reel(request(music=personal))
        check = next(
            c
            for c in plan.edl["validation"]["checks"]
            if c["check_id"] == "music_license_covers_destination"
        )
        self.assertFalse(check["passed"])
        self.assertEqual("fail", plan.status)

    def test_the_same_track_is_cleared_for_a_local_master(self):
        personal = music_track(
            license=MusicLicense(
                provider="user_supplied",
                license_type="personal_use_only",
                cleared_for=("private_playback",),
            )
        )
        plan = plan_reel(
            request(
                music=personal,
                target=RenderTarget(
                    destination="master",
                    resolution=(1920, 1080),
                    aspect_ratio=(16, 9),
                    target_duration=899,
                    max_duration=5395,
                    loudness_target_lufs=-23.0,
                ),
            )
        )
        self.assertEqual("pass", plan.status)


class TestVerticalReframe(unittest.TestCase):
    def setUp(self):
        self.plan = plan_reel(request())
        self.tracks = self.plan.edl["reframe_tracks"]

    def test_a_16_9_source_in_a_9_16_target_crops_to_exactly_81_256(self):
        # 9/16 divided by 16/9 is 81/256, which is exactly representable in
        # binary. Computed as a float ratio it would miss its own aspect check.
        self.assertTrue(self.tracks)
        for track in self.tracks:
            for keyframe in track["keyframes"]:
                self.assertEqual(81 / 256, keyframe["crop"]["w"])
                self.assertEqual(1.0, keyframe["crop"]["h"])

    def test_the_crop_stays_inside_the_frame(self):
        for track in self.tracks:
            for keyframe in track["keyframes"]:
                crop = keyframe["crop"]
                self.assertGreaterEqual(crop["x"], 0.0)
                self.assertLessEqual(crop["x"] + crop["w"], 1.0 + 1e-9)

    def test_keyframes_are_in_source_time_and_ordered(self):
        clips = {c["reframe_track_id"]: c for c in video_clips(self.plan.edl)}
        for track in self.tracks:
            clip = clips[track["reframe_track_id"]]
            start = clip["source_range"]["start_time"]["value"]
            end = start + clip["source_range"]["duration"]["value"]
            times = [k["time"]["value"] for k in track["keyframes"]]
            self.assertEqual(sorted(times), times)
            self.assertEqual(len(set(times)), len(times))
            self.assertGreaterEqual(times[0], start)
            self.assertLessEqual(times[-1], end)

    def test_the_last_keyframe_holds(self):
        # `smooth` on a final keyframe is how a crop drifts off its subject at
        # the end of a shot; there is nothing after it to interpolate towards.
        for track in self.tracks:
            self.assertEqual("hold", track["keyframes"][-1]["interpolation"])

    def test_the_crop_never_travels_faster_than_the_declared_limit(self):
        limit = self.plan.edl["reframe_tracks"][0]["smoothing"]["max_velocity_per_second"]
        for track in self.tracks:
            keyframes = track["keyframes"]
            for previous, current in zip(keyframes, keyframes[1:]):
                seconds = (
                    current["time"]["value"] - previous["time"]["value"]
                ) / RATE
                travel = abs(current["crop"]["x"] - previous["crop"]["x"])
                self.assertLessEqual(travel, limit * seconds + 1e-9)

    def test_a_subject_that_jumps_is_followed_at_the_speed_limit(self):
        jumpy = moment(
            0xD1,
            3031860,
            hook_potential=0.9,
            snap_points=(
                SnapPoint(
                    time=3031860, kind="shot_boundary", strength=0.9, cut_direction="in"
                ),
            ),
            subject_track=(
                SubjectSample(time=3031860, center_x=0.10, confidence=0.9),
                SubjectSample(time=3031866, center_x=0.90, confidence=0.9),
            ),
        )
        plan = plan_reel(request(moments=(jumpy, moment(0xD2, 3040000))))
        clip = next(
            c for c in video_clips(plan.edl) if c["moment_id"] == jumpy.moment_id
        )
        track = next(
            t
            for t in plan.edl["reframe_tracks"]
            if t["reframe_track_id"] == clip["reframe_track_id"]
        )
        first, second = track["keyframes"][0], track["keyframes"][1]
        seconds = (second["time"]["value"] - first["time"]["value"]) / RATE
        self.assertLessEqual(
            abs(second["crop"]["x"] - first["crop"]["x"]),
            ReframeSettings().max_velocity_per_second * seconds + 1e-9,
        )

    def test_a_moment_with_no_subject_track_gets_a_static_centre_crop(self):
        plain = moment(0xD3, 3031860, with_subject=False, hook_potential=0.9)
        plan = plan_reel(request(moments=(plain, moment(0xD4, 3040000, with_subject=False))))
        clip = next(
            c for c in video_clips(plan.edl) if c["moment_id"] == plain.moment_id
        )
        track = next(
            t
            for t in plan.edl["reframe_tracks"]
            if t["reframe_track_id"] == clip["reframe_track_id"]
        )
        self.assertEqual(1, len(track["keyframes"]))
        self.assertAlmostEqual(
            0.5 - (81 / 256) / 2, track["keyframes"][0]["crop"]["x"], places=6
        )
        self.assertIsNone(track["subject_lock"])

    def test_the_fallback_is_always_stated(self):
        for track in self.tracks:
            self.assertIn(
                track["fallback"],
                {"center_crop", "saliency_crop", "letterbox", "hold_last_keyframe"},
            )

    def test_a_matching_aspect_needs_no_reframe_track(self):
        plan = plan_reel(
            request(
                target=RenderTarget(
                    destination="youtube",
                    resolution=(1920, 1080),
                    aspect_ratio=(16, 9),
                    target_duration=899,
                    max_duration=5395,
                    loudness_target_lufs=-14.0,
                )
            )
        )
        self.assertEqual([], plan.edl["reframe_tracks"])
        for clip in video_clips(plan.edl):
            self.assertIsNone(clip["reframe_track_id"])
        _assert_schema_valid(self, plan.edl)

    def test_the_reframe_findings_are_still_recorded_when_there_is_no_crop(self):
        """A plan with no reframe track must still SAY the reframe was checked.

        `workers/render-video` requires a passing finding for both reframe
        checks before it renders anything, because `EdlValidation.checks` is the
        only place it can tell "looked and found nothing wrong" apart from
        "never looked". Emitting them only when a crop exists made every
        16:9-into-16:9 master unrenderable, and the renderer's complaint was
        about a missing check rather than about the absent crop that explained
        it. This is that regression, pinned.
        """
        plan = plan_reel(
            request(
                target=RenderTarget(
                    destination="youtube",
                    resolution=(1920, 1080),
                    aspect_ratio=(16, 9),
                    target_duration=899,
                    max_duration=5395,
                    loudness_target_lufs=-14.0,
                )
            )
        )
        self.assertEqual([], plan.edl["reframe_tracks"])
        by_id = {c["check_id"]: c for c in plan.edl["validation"]["checks"]}
        for check_id in ("reframe_aspect_matches_target", "reframe_keyframes_ordered"):
            self.assertIn(check_id, by_id, f"{check_id} was not recorded at all")
            self.assertTrue(by_id[check_id]["passed"])
            self.assertEqual("error", by_id[check_id]["severity"])
            self.assertIn("no reframe track", by_id[check_id]["detail"])

    def test_a_wider_target_crops_height_instead(self):
        portrait = source_media(aspect_ratio=(9, 16))
        plan = plan_reel(
            request(
                media=(portrait, music_media()),
                target=RenderTarget(
                    destination="youtube",
                    resolution=(1920, 1080),
                    aspect_ratio=(16, 9),
                    target_duration=899,
                    max_duration=5395,
                    loudness_target_lufs=-14.0,
                ),
            )
        )
        keyframe = plan.edl["reframe_tracks"][0]["keyframes"][0]
        self.assertEqual(1.0, keyframe["crop"]["w"])
        self.assertEqual(81 / 256, keyframe["crop"]["h"])


class TestTransitions(unittest.TestCase):
    def test_a_hard_cut_is_the_absence_of_a_transition(self):
        # OTIO's rule, restated twice in the contract: never a zero-length one.
        edl = plan_reel(request()).edl
        self.assertEqual(
            [], [i for i in video_items(edl) if i["item_type"] == "transition"]
        )

    def test_a_dissolve_is_emitted_only_when_asked_for(self):
        edl = plan_reel(request(dissolve_frames=6)).edl
        transitions = [i for i in video_items(edl) if i["item_type"] == "transition"]
        self.assertEqual(1, len(transitions))
        self.assertEqual("dissolve", transitions[0]["transition_type"])
        self.assertEqual(6, transitions[0]["in_offset"]["value"])
        self.assertEqual(6, transitions[0]["out_offset"]["value"])

    def test_a_dissolve_sits_between_two_clips(self):
        items = video_items(plan_reel(request(dissolve_frames=6)).edl)
        index = next(
            i for i, item in enumerate(items) if item["item_type"] == "transition"
        )
        self.assertEqual("clip", items[index - 1]["item_type"])
        self.assertEqual("clip", items[index + 1]["item_type"])

    def test_a_dissolve_does_not_consume_timeline_time(self):
        plain = plan_reel(request())
        dissolved = plan_reel(request(dissolve_frames=6))
        self.assertEqual(plain.duration_frames, dissolved.duration_frames)

    def test_a_dissolve_without_handles_is_downgraded_to_a_cut(self):
        # The button comes from a second source that begins exactly where the
        # clip does: there are no frames in front of it to blend from, so the
        # dissolve becomes a cut rather than a renderer inventing frames.
        second = SourceMedia(
            media_ref_id="src-b",
            media_id="f" * 64,
            available_start=3055440,
            available_duration=300,
            aspect_ratio=(16, 9),
            color_encoding="bt709",
        )
        button = SelectedMoment(
            moment_id=f"{0x2B1:064x}",
            media_id=second.media_id,
            source_start=3055440,
            source_duration=300,
            motion_energy=0.0,
            score=0.6,
            snap_points=(
                SnapPoint(
                    time=3055440, kind="shot_boundary", strength=0.9, cut_direction="in"
                ),
            ),
            safe_trim=SafeTrim(earliest_in=3055440, latest_out=3055740),
        )
        plan = plan_reel(
            request(
                media=(source_media(), music_media(), second),
                moments=(
                    moment(0x2B2, 3031860, hook_potential=0.99),
                    moment(0x2B3, 3049200, emotional_peak=0.95),
                    button,
                ),
                dissolve_frames=6,
            )
        )
        self.assertEqual(
            [], [i for i in video_items(plan.edl) if i["item_type"] == "transition"]
        )
        self.assertTrue(any("downgraded" in note for note in plan.notes), plan.notes)
        # And the plan is still valid without it.
        self.assertEqual("pass", plan.status)

    def test_the_handle_check_runs_when_a_dissolve_exists(self):
        plan = plan_reel(request(dissolve_frames=6))
        check = next(
            c
            for c in plan.edl["validation"]["checks"]
            if c["check_id"] == "transition_handles_available"
        )
        self.assertTrue(check["passed"])
        _assert_schema_valid(self, plan.edl)


class TestStoryArc(unittest.TestCase):
    def test_the_peak_is_the_moment_with_the_highest_emotional_peak(self):
        # The expectation is spelled out rather than recomputed with the
        # planner's own helper, which would agree with it however it changed.
        plan = plan_reel(request())
        peak_clip = next(
            c for c in video_clips(plan.edl) if c["story_beat_id"] == "beat-peak"
        )
        self.assertEqual(f"{0xA3:064x}", peak_clip["moment_id"])

    def test_an_unscored_moment_does_not_outrank_a_scored_one(self):
        # A missing emotional_peak means "not measured", not "average". Treated
        # as 0.5 it would beat a real 0.4 and quietly take the peak slot.
        unmeasured = moment(0x3A1, 3031860, emotional_peak=None, hook_potential=0.1)
        measured = moment(0x3A2, 3040000, emotional_peak=0.4, hook_potential=0.9)
        plan = plan_reel(request(moments=(unmeasured, measured)))
        peak_clip = next(
            c for c in video_clips(plan.edl) if c["story_beat_id"] == "beat-peak"
        )
        self.assertEqual(measured.moment_id, peak_clip["moment_id"])

    def test_two_identical_moments_break_their_tie_on_moment_id(self):
        # Identical scores, so the only thing left to choose by is the id. The
        # rule is stated in the key function AND holds here regardless of the
        # order they arrive in.
        low = moment(0x1, 3031860, emotional_peak=0.5, score=0.5, motion_energy=0.5)
        high = moment(0x2, 3040000, emotional_peak=0.5, score=0.5, motion_energy=0.5)
        forward = plan_reel(request(moments=(low, high)))
        backward = plan_reel(request(moments=(high, low)))
        for plan in (forward, backward):
            peak = next(
                c for c in video_clips(plan.edl) if c["story_beat_id"] == "beat-peak"
            )
            self.assertEqual(low.moment_id, peak["moment_id"])
        self.assertEqual(forward.edl_id, backward.edl_id)

    def test_only_the_peak_clip_carries_a_marker(self):
        plan = plan_reel(request())
        marked = [c["clip_id"] for c in video_clips(plan.edl) if c["markers"]]
        peak_clip = next(
            c for c in video_clips(plan.edl) if c["story_beat_id"] == "beat-peak"
        )
        self.assertEqual([peak_clip["clip_id"]], marked)

    def test_the_peak_carries_a_marker_for_the_human_editor(self):
        plan = plan_reel(request())
        peak_clip = next(
            c for c in video_clips(plan.edl) if c["story_beat_id"] == "beat-peak"
        )
        self.assertEqual(
            ["emotional_peak"], [m["kind"] for m in peak_clip["markers"]]
        )

    def test_the_build_runs_in_capture_order(self):
        plan = plan_reel(request())
        build = [
            c
            for c in video_clips(plan.edl)
            if c["story_beat_id"] == "beat-build-place"
        ]
        starts = [c["source_range"]["start_time"]["value"] for c in build]
        self.assertEqual(sorted(starts), starts)

    def test_acts_run_hook_build_peak_button(self):
        plan = plan_reel(request())
        self.assertEqual(
            ["act-hook", "act-build", "act-peak", "act-button"],
            [act["act_id"] for act in plan.edl["story_arc"]["acts"]],
        )

    def test_act_ranges_cover_their_clips(self):
        plan = plan_reel(request())
        clips = {c["clip_id"]: c for c in video_clips(plan.edl)}
        for act in plan.edl["story_arc"]["acts"]:
            members = [
                clips[cid]
                for beat in act["beats"]
                for cid in beat["satisfied_by_clip_ids"]
            ]
            start = act["timeline_range"]["start_time"]["value"]
            end = start + act["timeline_range"]["duration"]["value"]
            for member in members:
                member_start = member["timeline_range"]["start_time"]["value"]
                member_end = member_start + member["timeline_range"]["duration"]["value"]
                self.assertGreaterEqual(member_start, start)
                self.assertLessEqual(member_end, end)

    def test_a_missing_required_beat_fails_validation(self):
        # This used to plan a ONE-MOMENT reel, whose hook beat came out empty:
        # it was asserting the defect fixed in TestSingleShotReel, where a
        # legitimate single-shot cut was hard-failed for lacking an act it has
        # no room for. The check itself is real, so it is exercised against an
        # arc that genuinely leaves a required beat unsatisfied.
        req = request()
        plan = plan_reel(req)
        edl = json.loads(json.dumps(plan.edl))
        hook = next(a for a in edl["story_arc"]["acts"] if a["act_id"] == "act-hook")
        hook["beats"][0]["satisfied_by_clip_ids"] = []
        validation = reel._validate(edl, req, (), plan.duration_frames)
        check = next(
            c
            for c in validation["checks"]
            if c["check_id"] == "required_story_beats_satisfied"
        )
        self.assertFalse(check["passed"])
        self.assertEqual("error", check["severity"])
        self.assertIn("beat-hook-open", check["detail"])
        self.assertEqual("fail", validation["status"])
        _assert_schema_valid(self, plan.edl)

    def test_candidate_moments_are_retained_for_re_planning(self):
        plan = plan_reel(request())
        candidates = {
            mid
            for act in plan.edl["story_arc"]["acts"]
            for beat in act["beats"]
            for mid in beat["candidate_moment_ids"]
        }
        self.assertEqual({m.moment_id for m in request().moments}, candidates)

    def test_the_chronological_template_is_one_act_in_capture_order(self):
        plan = plan_reel(request(arc_template="chronological"))
        acts = plan.edl["story_arc"]["acts"]
        self.assertEqual(1, len(acts))
        starts = [
            c["source_range"]["start_time"]["value"] for c in video_clips(plan.edl)
        ]
        self.assertEqual(sorted(starts), starts)
        for clip in video_clips(plan.edl):
            self.assertIsNone(clip["story_beat_id"])
        _assert_schema_valid(self, plan.edl)

    def test_an_unknown_template_is_refused(self):
        with self.assertRaises(ValueError):
            request(arc_template="three_act")


class TestCapacity(unittest.TestCase):
    def test_the_build_is_what_gets_dropped_when_the_reel_is_short(self):
        moments = (
            moment(0xF1, 3031860, hook_potential=0.99, score=0.8),
            moment(0xF2, 3033000, score=0.30),
            moment(0xF3, 3034000, score=0.20),
            moment(0xF4, 3035000, score=0.10),
            moment(0xF5, 3049200, emotional_peak=0.95, score=0.9),
            moment(0xF6, 3055440, motion_energy=0.05, score=0.6),
        )
        plan = plan_reel(
            request(
                moments=moments,
                target=RenderTarget(
                    destination="instagram_reel",
                    resolution=(1080, 1920),
                    aspect_ratio=(9, 16),
                    target_duration=340,
                    max_duration=5395,
                    loudness_target_lufs=-14.0,
                ),
            )
        )
        placed = {c["moment_id"] for c in video_clips(plan.edl)}
        self.assertIn(f"{0xF5:064x}", placed, "the peak must survive")
        self.assertIn(f"{0xF1:064x}", placed, "the hook must survive")
        # Exactly the three weakest build moments went, in score order: a drop
        # policy that took the best ones first would leave the same COUNT.
        self.assertNotIn(f"{0xF2:064x}", placed)
        self.assertNotIn(f"{0xF3:064x}", placed)
        self.assertNotIn(f"{0xF4:064x}", placed)
        self.assertTrue(any("dropped" in note for note in plan.notes), plan.notes)

    def test_the_weakest_build_moment_is_the_one_that_goes(self):
        # Room for four shots and five moments, TWO of them build: exactly one
        # build is dropped, so which one it is becomes observable. With a
        # single droppable moment the policy is untestable, because every
        # ordering drops the same one.
        moments = (
            moment(0x2C1, 3031860, hook_potential=0.99, score=0.8),
            moment(0x2C2, 3033000, score=0.75, hook_potential=0.1),
            moment(0x2C3, 3034000, score=0.11, hook_potential=0.1),
            moment(0x2C4, 3049200, emotional_peak=0.95, score=0.9),
            moment(0x2C5, 3055440, motion_energy=0.02, score=0.6, hook_potential=0.1),
        )
        plan = plan_reel(
            request(
                moments=moments,
                target=RenderTarget(
                    destination="instagram_reel",
                    resolution=(1080, 1920),
                    aspect_ratio=(9, 16),
                    target_duration=450,
                    max_duration=5395,
                    loudness_target_lufs=-14.0,
                ),
            )
        )
        placed = {c["moment_id"] for c in video_clips(plan.edl)}
        self.assertEqual(4, len(placed))
        self.assertIn(f"{0x2C2:064x}", placed, "the stronger build must survive")
        self.assertNotIn(f"{0x2C3:064x}", placed, "the weaker build must go first")

    def test_an_unmeasured_motion_energy_does_not_win_the_closing_shot(self):
        # calm(unmeasured) == 0: "not measured" is not "perfectly still".
        unmeasured = moment(0x2E1, 3033000, score=0.9, hook_potential=0.1)
        actually_calm = moment(0x2E2, 3055440, motion_energy=0.05, score=0.5, hook_potential=0.1)
        plan = plan_reel(
            request(
                moments=(
                    moment(0x2E3, 3031860, hook_potential=0.99),
                    moment(0x2E4, 3049200, emotional_peak=0.95),
                    unmeasured,
                    actually_calm,
                )
            )
        )
        button = next(
            c
            for c in video_clips(plan.edl)
            if c["story_beat_id"] == "beat-button-close"
        )
        self.assertEqual(actually_calm.moment_id, button["moment_id"])

    def test_a_moment_shorter_than_its_own_declared_minimum_is_not_placed(self):
        # SafeTrim.min_duration is the analysis layer's claim about THIS
        # window; it overrides the planner's global floor, and a shot below it
        # reads as a flash frame however much timeline is free.
        picky = moment(
            0x2D1,
            3031860,
            duration=300,
            hook_potential=0.99,
            safe_trim=SafeTrim(
                earliest_in=3031860, latest_out=3032160, min_duration=200
            ),
        )
        plan = plan_reel(
            request(moments=(picky, moment(0x2D2, 3049200, emotional_peak=0.9)))
        )
        placed = {c["moment_id"] for c in video_clips(plan.edl)}
        self.assertNotIn(picky.moment_id, placed)
        self.assertTrue(
            any(picky.moment_id in note and "skipped" in note for note in plan.notes),
            plan.notes,
        )

    def test_a_moment_too_short_for_any_cut_point_is_skipped_loudly(self):
        tiny = moment(
            0xF7,
            3031860,
            duration=4,
            hook_potential=0.9,
            snap_points=(
                SnapPoint(time=3031860, kind="shot_boundary", strength=0.9, cut_direction="in"),
            ),
            safe_trim=SafeTrim(earliest_in=3031860, latest_out=3031864),
            subject_track=(),
        )
        plan = plan_reel(request(moments=(tiny, moment(0xF8, 3040000, emotional_peak=0.5))))
        placed = {c["moment_id"] for c in video_clips(plan.edl)}
        self.assertNotIn(tiny.moment_id, placed)
        self.assertTrue(
            any(tiny.moment_id in note and "skipped" in note for note in plan.notes),
            plan.notes,
        )

    def test_a_short_moment_shrinks_its_slot_to_an_earlier_beat(self):
        # 60 frames of usable window cannot fill a 4-beat slot (112 frames), so
        # the cut moves to the beat that fits -- still a beat, so the next cut
        # stays locked.
        short = moment(
            0xF9,
            3031860,
            duration=60,
            hook_potential=0.99,
            snap_points=(
                SnapPoint(time=3031860, kind="shot_boundary", strength=0.9, cut_direction="in"),
            ),
            safe_trim=SafeTrim(earliest_in=3031860, latest_out=3031920),
            subject_track=(),
        )
        plan = plan_reel(request(moments=(short, moment(0xFA, 3049200, emotional_peak=0.9))))
        clips = video_clips(plan.edl)
        first = next(c for c in clips if c["moment_id"] == short.moment_id)
        self.assertLessEqual(first["timeline_range"]["duration"]["value"], 60)
        second = next(c for c in clips if c["moment_id"] != short.moment_id)
        self.assertEqual(
            first["timeline_range"]["duration"]["value"],
            second["timeline_range"]["start_time"]["value"],
        )
        self.assertIsNotNone(second["beat_lock"])

    def test_every_moment_that_does_not_appear_is_accounted_for(self):
        moments = tuple(
            moment(0x100 + i, 3031860 + i * 1000, score=0.5 + i / 100)
            for i in range(8)
        )
        plan = plan_reel(
            request(
                moments=moments,
                target=RenderTarget(
                    destination="instagram_reel",
                    resolution=(1080, 1920),
                    aspect_ratio=(9, 16),
                    target_duration=340,
                    max_duration=5395,
                    loudness_target_lufs=-14.0,
                ),
            )
        )
        placed = {c["moment_id"] for c in video_clips(plan.edl)}
        for candidate in moments:
            if candidate.moment_id not in placed:
                self.assertTrue(
                    any(candidate.moment_id in note for note in plan.notes),
                    f"{candidate.moment_id} vanished without a note",
                )


class TestChapteredFootage(unittest.TestCase):
    def test_a_moment_crossing_a_chapter_boundary_is_one_clip_on_the_assembly(self):
        # GH01 ends at 3067485 in assembly time; this moment straddles it. The
        # planner must not know or care -- one clip, one media_id, and the
        # renderer expands the span into member reads.
        crossing = moment(
            0x1A1,
            3067380,
            duration=240,
            emotional_peak=0.9,
            snap_points=(
                SnapPoint(time=3067380, kind="shot_boundary", strength=0.91, cut_direction="in"),
            ),
            safe_trim=SafeTrim(earliest_in=3067380, latest_out=3067620),
        )
        plan = plan_reel(request(moments=(crossing, moment(0x1A2, 3031860))))
        clip = next(
            c for c in video_clips(plan.edl) if c["moment_id"] == crossing.moment_id
        )
        start = clip["source_range"]["start_time"]["value"]
        end = start + clip["source_range"]["duration"]["value"]
        self.assertLess(start, 3067485)
        self.assertGreater(end, 3067485)
        ref = next(
            r for r in plan.edl["media_refs"] if r["media_ref_id"] == clip["media_ref_id"]
        )
        self.assertTrue(ref["is_span_assembly"])
        self.assertEqual(RIDE_MEDIA_ID, ref["media_id"])
        self.assertEqual(1, len([c for c in video_clips(plan.edl) if c["moment_id"] == crossing.moment_id]))

    def test_source_ranges_stay_inside_the_assembly(self):
        plan = plan_reel(request())
        check = next(
            c
            for c in plan.edl["validation"]["checks"]
            if c["check_id"] == "source_range_within_available"
        )
        self.assertTrue(check["passed"])

    def test_a_clip_that_escapes_its_source_fails_before_the_render_does(self):
        # A moment whose safe_trim claims more footage than the media actually
        # has. Caught up front rather than as a seek failure at 80% of a
        # render, which is the whole reason media_refs are declared.
        truncated = source_media(available_start=3031860, available_duration=140)
        plan = plan_reel(
            request(
                media=(truncated, music_media()),
                moments=(
                    moment(0x1B1, 3031860, duration=300, hook_potential=0.9),
                    moment(0x1B2, 3031900, duration=200, emotional_peak=0.9),
                ),
            )
        )
        check = next(
            c
            for c in plan.edl["validation"]["checks"]
            if c["check_id"] == "source_range_within_available"
        )
        self.assertFalse(check["passed"])
        self.assertEqual("fail", plan.status)
        self.assertIsNotNone(check["clip_id"])

    def test_an_unused_source_is_not_declared(self):
        spare = source_media(
            media_ref_id="src-spare",
            media_id="b" * 64,
            is_span_assembly=False,
            member_media_ids=(),
            continuity=None,
            label="unused",
        )
        plan = plan_reel(request(media=(source_media(), music_media(), spare)))
        refs = {r["media_ref_id"] for r in plan.edl["media_refs"]}
        self.assertNotIn("src-spare", refs)
        self.assertTrue(any("src-spare" in note for note in plan.notes), plan.notes)


class TestDeterminism(unittest.TestCase):
    def test_the_same_request_produces_byte_identical_output(self):
        first = canonical_json(plan_reel(request()).edl)
        second = canonical_json(plan_reel(request()).edl)
        self.assertEqual(first, second)

    def test_moment_order_in_the_request_does_not_change_the_plan(self):
        forward = request()
        backward = request(moments=tuple(reversed(forward.moments)))
        self.assertEqual(
            canonical_json(plan_reel(forward).edl),
            canonical_json(plan_reel(backward).edl),
        )

    def test_media_order_in_the_request_does_not_change_the_plan(self):
        forward = request()
        backward = request(media=tuple(reversed(forward.media)))
        self.assertEqual(
            canonical_json(plan_reel(forward).edl),
            canonical_json(plan_reel(backward).edl),
        )

    def test_the_clock_does_not_reach_the_id(self):
        early = plan_reel(request(generated_at="2020-01-01T00:00:00+00:00"))
        late = plan_reel(
            request(
                generated_at="2031-12-31T23:59:59+00:00",
                validated_at="2031-12-31T23:59:59+00:00",
            )
        )
        self.assertEqual(early.edl_id, late.edl_id)
        self.assertNotEqual(
            early.edl["determinism"]["generated_at"],
            late.edl["determinism"]["generated_at"],
        )

    def test_sibling_variant_ids_do_not_reach_the_id(self):
        # They cannot: each sibling's id would depend on every other's.
        alone = plan_reel(
            request(variant=VariantInfo("var-01", 0, "pacing_seed", "faster"))
        )
        with_siblings = plan_reel(
            request(
                variant=VariantInfo(
                    "var-01", 0, "pacing_seed", "faster", sibling_edl_ids=("c" * 64,)
                )
            )
        )
        self.assertEqual(alone.edl_id, with_siblings.edl_id)
        self.assertEqual(["c" * 64], with_siblings.edl["variant"]["sibling_edl_ids"])

    def test_a_different_seed_changes_the_inputs_digest(self):
        a = plan_reel(request(seed=1)).edl["determinism"]["inputs_digest"]
        b = plan_reel(request(seed=2)).edl["determinism"]["inputs_digest"]
        self.assertNotEqual(a, b)

    def test_a_different_moment_changes_the_id(self):
        base = plan_reel(request())
        changed = plan_reel(
            request(
                moments=(
                    moment(0xA1, 3031860, hook_potential=0.88, motion_energy=0.9),
                    moment(0xA2, 3040000, hook_potential=0.40, motion_energy=0.7),
                    moment(0xA3, 3049200, hook_potential=0.55, emotional_peak=0.93, score=0.91),
                    moment(0xA5, 3055440, hook_potential=0.30, motion_energy=0.15),
                )
            )
        )
        self.assertNotEqual(base.edl_id, changed.edl_id)

    def test_the_id_covers_the_plan_and_not_just_the_inputs(self):
        plan = plan_reel(request())
        tampered = json.loads(json.dumps(plan.edl))
        clip = next(
            i for i in video_items(tampered) if i["item_type"] == "clip"
        )
        clip["source_range"]["start_time"]["value"] += 1
        self.assertNotEqual(
            plan.edl_id, blake3_hex(canonical_json(reel._digest_view(tampered)))
        )

    def test_derived_timeline_ranges_are_excluded_from_the_id(self):
        # timeline_range is derived and not exported to OTIO, so an importer
        # that recomputes it must still get the same id back.
        plan = plan_reel(request())
        rebuilt = json.loads(json.dumps(plan.edl))
        for item in video_items(rebuilt):
            if item["item_type"] == "clip":
                item["timeline_range"] = None
        self.assertEqual(
            plan.edl_id, blake3_hex(canonical_json(reel._digest_view(rebuilt)))
        )

    def test_the_inputs_digest_is_a_well_formed_hash(self):
        digest = plan_reel(request()).edl["determinism"]["inputs_digest"]
        self.assertEqual(64, len(digest))
        self.assertEqual(digest, digest.lower())
        int(digest, 16)

    def test_the_planner_is_named_in_the_determinism_block(self):
        block = plan_reel(request()).edl["determinism"]
        self.assertEqual("reel-planner", block["planner"])
        self.assertEqual(reel.PLANNER_VERSION, block["planner_version"])
        self.assertEqual(20260316, block["seed"])


class TestRequestValidation(unittest.TestCase):
    def test_duplicate_moment_ids_are_refused(self):
        duplicate = moment(0xA1, 3031860)
        with self.assertRaises(ValueError):
            request(moments=(duplicate, moment(0xA1, 3040000)))

    def test_duplicate_media_ref_ids_are_refused(self):
        with self.assertRaises(ValueError):
            request(media=(source_media(), source_media(media_id="d" * 64)))

    def test_a_moment_pointing_at_unknown_media_is_refused(self):
        orphan = moment(0xA9, 3031860, media_id="e" * 64)
        with self.assertRaises(ValueError):
            request(moments=(orphan,))

    def test_a_non_hex_moment_id_is_refused(self):
        with self.assertRaises(ValueError):
            SelectedMoment(
                moment_id="not-a-hash",
                media_id=RIDE_MEDIA_ID,
                source_start=0,
                source_duration=10,
            )

    def test_a_non_slug_media_ref_id_is_refused(self):
        with self.assertRaises(ValueError):
            source_media(media_ref_id="Src Ride")

    def test_an_empty_moment_list_is_refused(self):
        with self.assertRaises(ValueError):
            request(moments=())

    def test_an_unknown_destination_is_refused(self):
        with self.assertRaises(ValueError):
            RenderTarget(
                destination="vimeo", resolution=(1080, 1920), aspect_ratio=(9, 16)
            )

    def test_an_inverted_safe_trim_is_refused(self):
        with self.assertRaises(ValueError):
            SafeTrim(earliest_in=100, latest_out=100)

    def test_an_empty_speech_safe_window_is_refused(self):
        with self.assertRaises(ValueError):
            request(
                moments=(
                    moment(
                        0xAB,
                        3031860,
                        safe_trim=SafeTrim(
                            earliest_in=3031860,
                            latest_out=3032160,
                            speech_safe_in=3032000,
                            speech_safe_out=3031900,
                        ),
                    ),
                )
            )


class TestContractAdapter(unittest.TestCase):
    def _record(self, name: str) -> dict:
        return json.loads((MOMENT_FIXTURES / name).read_text(encoding="utf-8"))

    def test_the_golden_emotional_peak_fixture_converts(self):
        record = self._record("moment-emotional-peak.json")
        converted = moment_from_record(record, rate=RATE)
        self.assertEqual(record["moment_id"], converted.moment_id)
        self.assertEqual(3049200, converted.source_start)
        self.assertEqual(210, converted.source_duration)
        self.assertEqual(0.91, converted.score)
        self.assertEqual(0.93, converted.emotional_peak)
        self.assertEqual(5, len(converted.snap_points))
        self.assertEqual(4, len(converted.words))
        self.assertEqual((3049284.0, 3049398.0), converted.window())
        self.assertTrue(converted.safe_trim.preserve_audio_tail)

    def test_the_golden_chapter_crossing_fixture_converts(self):
        converted = moment_from_record(
            self._record("moment-crosses-chapter-boundary.json"), rate=RATE
        )
        self.assertEqual(RIDE_MEDIA_ID, converted.media_id)
        self.assertEqual(0.41, converted.noise_ratio)

    def test_an_eliminated_moment_is_refused(self):
        with self.assertRaises(ValueError):
            moment_from_record(self._record("moment-eliminated-shake.json"), rate=RATE)

    def test_a_rate_mismatch_raises_rather_than_rescaling(self):
        with self.assertRaises(reel.RateMismatch):
            moment_from_record(self._record("moment-emotional-peak.json"), rate=30.0)

    def test_an_unknown_schema_version_is_refused(self):
        record = self._record("moment-emotional-peak.json")
        record["schema_version"] = "v1"
        with self.assertRaises(reel.SchemaVersionUnsupported):
            moment_from_record(record, rate=RATE)

    def test_a_converted_fixture_plans_and_validates(self):
        peak = moment_from_record(self._record("moment-emotional-peak.json"), rate=RATE)
        crossing = moment_from_record(
            self._record("moment-crosses-chapter-boundary.json"), rate=RATE
        )
        plan = plan_reel(
            request(moments=(peak, crossing, moment(0xA1, 3031860, hook_potential=0.9)))
        )
        self.assertEqual("pass", plan.status)
        _assert_schema_valid(self, plan.edl)


class TestValidationBlock(unittest.TestCase):
    def test_every_check_id_is_one_the_contract_knows(self):
        allowed = {
            "source_range_within_available",
            "media_refs_resolvable",
            "span_continuity_verified",
            "timeline_contiguous",
            "transition_handles_available",
            "beat_alignment_within_tolerance",
            "no_mid_word_cut",
            "reframe_aspect_matches_target",
            "reframe_keyframes_ordered",
            "duration_within_max",
            "music_license_covers_destination",
            "required_story_beats_satisfied",
            "audio_loudness_target_set",
            "color_pipeline_resolves",
            "determinism_digest_present",
        }
        plan = plan_reel(request(dissolve_frames=6))
        ids = {c["check_id"] for c in plan.edl["validation"]["checks"]}
        self.assertLessEqual(ids, allowed)
        self.assertIn("determinism_digest_present", ids)

    def test_a_failed_error_check_fails_the_plan(self):
        # This used to plan a ONE-MOMENT reel and assert "fail" -- it was
        # asserting the defect fixed in TestSingleShotReel below, where the
        # planner called its own single shot the peak and then failed the plan
        # for having no hook. An uncleared licence is a real error failure.
        plan = plan_reel(
            request(
                music=music_track(
                    license=MusicLicense(
                        provider="user_supplied",
                        license_type="personal_use_only",
                        cleared_for=("private_playback",),
                    )
                )
            )
        )
        failed = [c for c in plan.edl["validation"]["checks"] if not c["passed"]]
        self.assertEqual(
            ["music_license_covers_destination"], [c["check_id"] for c in failed]
        )
        self.assertEqual("error", failed[0]["severity"])
        self.assertEqual("fail", plan.status)

    def test_a_failed_warning_check_only_warns(self):
        req = request(
            target=RenderTarget(
                destination="instagram_reel",
                resolution=(1080, 1920),
                aspect_ratio=(9, 16),
                target_duration=899,
                max_duration=5395,
                loudness_target_lufs=-16.0,
            ),
            ambient=AmbientSettings(),
        )
        plan = plan_reel(req)
        # The mix takes the target's LUFS, so this one passes; force the
        # mismatch through the check itself instead.
        self.assertEqual(-16.0, plan.edl["audio_plan"]["mix"]["loudness_target_lufs"])
        self.assertEqual("pass", plan.status)

        # A mix that does NOT match the target warns, and warning-only
        # failures leave the plan renderable.
        damaged = json.loads(json.dumps(plan.edl))
        damaged["audio_plan"]["mix"]["loudness_target_lufs"] = -9.0
        validation = reel._validate(damaged, req, (), plan.duration_frames)
        check = next(
            c
            for c in validation["checks"]
            if c["check_id"] == "audio_loudness_target_set"
        )
        self.assertFalse(check["passed"])
        self.assertEqual("warning", check["severity"])
        self.assertEqual("warn", validation["status"])

    def test_an_unsatisfied_optional_beat_does_not_fail_the_plan(self):
        # Only REQUIRED beats gate the plan. Every act the planner emits today
        # has clips in it, so this rule is only reachable by handing the
        # validator an arc with an empty optional beat -- which is exactly what
        # a future template that declares optional beats up front will produce.
        req = request()
        plan = plan_reel(req)
        edl = json.loads(json.dumps(plan.edl))
        edl["story_arc"]["acts"][0]["beats"].append(
            {
                "beat_id": "beat-optional-extra",
                "description": "nice to have",
                "required": False,
                "satisfied_by_clip_ids": [],
                "candidate_moment_ids": [],
            }
        )
        # Re-run the checks over the doctored arc. `placements` only feeds the
        # mid-word check, which is not what is under test here.
        validation = reel._validate(edl, req, (), plan.duration_frames)
        self.assertEqual("pass", validation["status"])
        check = next(
            c
            for c in validation["checks"]
            if c["check_id"] == "required_story_beats_satisfied"
        )
        self.assertTrue(check["passed"])

        # ... and the same arc with the beat marked required does fail.
        edl["story_arc"]["acts"][0]["beats"][-1]["required"] = True
        failed = reel._validate(edl, req, (), plan.duration_frames)
        self.assertEqual("fail", failed["status"])

    def test_the_validator_version_is_recorded(self):
        block = plan_reel(request()).edl["validation"]
        self.assertEqual(f"reel-planner/{reel.PLANNER_VERSION}", block["validator_version"])
        self.assertEqual("2026-03-16T20:04:53+05:30", block["validated_at"])


def open_target(**kwargs) -> RenderTarget:
    """A target with no duration ask and no ceiling, for the grid tests."""
    defaults = dict(
        destination="instagram_reel",
        resolution=(1080, 1920),
        aspect_ratio=(9, 16),
        target_duration=None,
        max_duration=None,
        loudness_target_lufs=-14.0,
    )
    defaults.update(kwargs)
    return RenderTarget(**defaults)


class TestSyntheticGridArithmetic(unittest.TestCase):
    """Without music the grid is invented, so its arithmetic is the only thing
    holding the cut positions up.

    Every number below is derived here from `unlocked_clip_frames /
    beats_per_cut` and half-away-from-zero rounding -- the same two rules the
    module docstring states -- rather than read back out of a plan. An
    off-by-one tick is invisible in a plan that still validates, still tiles
    and still ends on a grid point.
    """

    @staticmethod
    def ticks(unlocked: float, beats_per_cut: int, count: int) -> list[int]:
        step = unlocked / beats_per_cut
        return [reel._round_half_up(i * step) for i in range(count)]

    def silent_request(self, **kwargs) -> ReelRequest:
        moments = kwargs.pop(
            "moments",
            (
                moment(0x711, 3031860, hook_potential=0.9),
                moment(0x712, 3040000, emotional_peak=0.9),
                moment(0x713, 3049200, motion_energy=0.05),
            ),
        )
        return request(
            music=None,
            beat_grid=None,
            media=(source_media(),),
            moments=moments,
            target=kwargs.pop("target", open_target()),
            **kwargs,
        )

    def test_the_default_grid_is_four_ticks_to_a_ninety_frame_shot(self):
        # No target and no ceiling: the span is one nominal shot per moment
        # (3 x 90 = 270 frames), the grid is 22.5 frames per tick, and the reel
        # runs to the LAST tick it can reach -- one shot of four ticks each.
        plan = plan_reel(self.silent_request())
        ticks = self.ticks(90.0, 4, 13)
        self.assertEqual([0, 23, 45, 68, 90], ticks[:5])
        clips = video_clips(plan.edl)
        self.assertEqual(3, len(clips))
        self.assertEqual(
            [ticks[0], ticks[4], ticks[8]],
            [c["timeline_range"]["start_time"]["value"] for c in clips],
        )
        self.assertEqual(
            [90, 90, 90],
            [c["timeline_range"]["duration"]["value"] for c in clips],
        )
        self.assertEqual(ticks[12], plan.duration_frames)
        self.assertEqual(270, plan.duration_frames)
        for clip in clips:
            self.assertIsNone(clip["beat_lock"], "no music, no beat lock")
        self.assertEqual("pass", plan.status)

    def test_a_one_tick_slot_cuts_on_the_rounded_tick_not_the_truncated_one(self):
        # 22.5 frames per tick with one tick per cut: the cut points are 0, 23,
        # 45, 68 -- rounded half away from zero, alternating 23- and 22-frame
        # shots. Truncating instead gives 0, 22, 45, 67, which is a different
        # cut on every second shot and still tiles perfectly.
        plan = plan_reel(
            self.silent_request(
                beats_per_cut=1,
                unlocked_clip_frames=22.5,
                target=open_target(target_duration=90),
            )
        )
        ticks = self.ticks(22.5, 1, 6)
        self.assertEqual([0, 23, 45, 68, 90], ticks[:5])
        clips = video_clips(plan.edl)
        self.assertEqual(
            [ticks[0], ticks[1], ticks[2]],
            [c["timeline_range"]["start_time"]["value"] for c in clips],
        )
        self.assertEqual(
            [23, 22, 23],
            [c["timeline_range"]["duration"]["value"] for c in clips],
        )
        self.assertEqual(ticks[3], plan.duration_frames)

    def test_with_no_target_the_reel_runs_to_the_last_reachable_tick(self):
        # `_end_index` falls back to the LAST allowed grid point, not the
        # first. Taking the first would end every unasked-for reel one tick in.
        plan = plan_reel(self.silent_request())
        self.assertEqual(270, plan.duration_frames)
        self.assertEqual(3, len(video_clips(plan.edl)))

    def test_the_grid_still_reaches_its_span_when_the_division_is_inexact(self):
        # 100 frames over 3 ticks is 33.333333333333336 in binary, so a
        # 500-frame span divides into "14.999999999999998" ticks rather than
        # the 15 it mathematically is. Rounding the tick count DOWN there costs
        # the grid its last tick, the timeline one shot of capacity, and the
        # reel its fifth moment -- and the short reel that comes out still
        # validates, still tiles and still ends on a grid point.
        moments = tuple(
            moment(0x715 + i, 3031860 + 2000 * i, duration=300, score=0.5 + 0.01 * i)
            for i in range(5)
        )
        plan = plan_reel(
            self.silent_request(
                moments=moments, unlocked_clip_frames=100.0, beats_per_cut=3
            )
        )
        clips = video_clips(plan.edl)
        self.assertEqual(5, len(clips), "a moment was dropped for want of a tick")
        self.assertEqual([100] * 5, [c["timeline_range"]["duration"]["value"] for c in clips])
        self.assertEqual(500, plan.duration_frames)
        self.assertEqual([], [n for n in plan.notes if "dropped" in n], plan.notes)

    def test_a_ceiling_alone_still_ends_on_a_tick_under_it(self):
        plan = plan_reel(
            self.silent_request(target=open_target(max_duration=200))
        )
        ticks = self.ticks(90.0, 4, 13)
        self.assertLessEqual(plan.duration_frames, 200)
        self.assertIn(plan.duration_frames, ticks)
        check = next(
            c
            for c in plan.edl["validation"]["checks"]
            if c["check_id"] == "duration_within_max"
        )
        self.assertTrue(check["passed"])


class TestActSelectionTieBreaks(unittest.TestCase):
    """Which moment gets which act is the reel's whole creative content.

    These are all cases where two plausible orderings disagree: a rule that
    reads the wrong score, or places the peak before the build, reorders every
    clip in the reel and still emits a plan that validates, tiles and locks to
    the beat.
    """

    def beat_of(self, edl, beat_id):
        return next(c for c in video_clips(edl) if c["story_beat_id"] == beat_id)

    def test_the_peak_is_chosen_on_emotional_peak_not_on_overall_score(self):
        peak = moment(0x721, 3031860, emotional_peak=0.90, score=0.20)
        loud = moment(0x722, 3040000, emotional_peak=0.10, score=0.99)
        hook = moment(0x723, 3049200, hook_potential=0.95, score=0.50)
        plan = plan_reel(request(moments=(peak, loud, hook)))
        self.assertEqual(
            peak.moment_id, self.beat_of(plan.edl, "beat-peak")["moment_id"]
        )

    def test_the_hook_is_chosen_on_hook_potential_not_on_overall_score(self):
        peak = moment(0x731, 3031860, emotional_peak=0.99, score=0.50)
        hook = moment(0x732, 3040000, hook_potential=0.90, score=0.20)
        loud = moment(0x733, 3049200, hook_potential=0.10, score=0.95)
        plan = plan_reel(request(moments=(peak, hook, loud)))
        self.assertEqual(
            hook.moment_id, self.beat_of(plan.edl, "beat-hook-open")["moment_id"]
        )

    def test_the_clips_run_hook_build_peak_button_in_that_order(self):
        # The ACT list is emitted in a fixed order whatever happens, so it
        # cannot catch a peak placed before the build. The clip order can.
        plan = plan_reel(request())
        self.assertEqual(
            [
                "beat-hook-open",
                "beat-build-place",
                "beat-peak",
                "beat-button-close",
            ],
            [c["story_beat_id"] for c in video_clips(plan.edl)],
        )

    def test_the_calmest_shot_closes_the_reel_even_when_it_scores_lower(self):
        # calm = half stillness, half score. These two swap places under any
        # other weighting: 0.2 motion / 0.4 score against 0.4 motion / 0.8.
        peak = moment(0x741, 3031860, emotional_peak=0.95, score=0.5)
        hook = moment(0x742, 3040000, hook_potential=0.95, score=0.5)
        still_but_weak = moment(0x743, 3049200, motion_energy=0.2, score=0.4)
        livelier_but_better = moment(0x744, 3055440, motion_energy=0.4, score=0.8)
        plan = plan_reel(
            request(
                moments=(peak, hook, still_but_weak, livelier_but_better)
            )
        )
        self.assertEqual(
            livelier_but_better.moment_id,
            self.beat_of(plan.edl, "beat-button-close")["moment_id"],
        )

    def test_the_build_orders_by_media_first_then_by_source_time(self):
        # Two files have no comparable source clock. Ordering the build by raw
        # source offset across files is chronology-shaped noise: here it would
        # put a second camera's frame 100 before the first camera's frame
        # 3060000, as though one had happened before the other.
        other = SourceMedia(
            media_ref_id="src-other",
            media_id="f" * 64,
            available_start=0,
            available_duration=5000,
            aspect_ratio=(16, 9),
            color_encoding="bt709",
        )
        first_file = moment(0x751, 3060000, motion_energy=0.9, score=0.7)
        second_file = moment(
            0x752, 100, motion_energy=0.9, score=0.7, media_id=other.media_id
        )
        plan = plan_reel(
            request(
                media=(source_media(), music_media(), other),
                beats_per_cut=2,
                moments=(
                    moment(0x753, 3031860, hook_potential=0.99),
                    moment(0x754, 3049200, emotional_peak=0.95),
                    moment(0x755, 3055440, motion_energy=0.02, score=0.6),
                    first_file,
                    second_file,
                ),
            )
        )
        build = [
            c["moment_id"]
            for c in video_clips(plan.edl)
            if c["story_beat_id"] == "beat-build-place"
        ]
        self.assertEqual([first_file.moment_id, second_file.moment_id], build)

    def test_the_capacity_cut_never_takes_the_peak_or_the_button(self):
        # Ten moments into a three-shot timeline, with the peak deliberately
        # the LOWEST-scoring moment in the pool: a drop policy that ranked on
        # score alone would throw away the shot the reel exists for.
        peak = moment(0x761, 3031860, emotional_peak=0.95, score=0.05)
        hook = moment(0x762, 3032400, hook_potential=0.99, score=0.80)
        button = moment(0x763, 3033000, motion_energy=0.02, score=0.06)
        builds = tuple(
            moment(0x770 + i, 3034000 + 400 * i, score=0.30 + 0.05 * i)
            for i in range(7)
        )
        plan = plan_reel(
            request(
                moments=(peak, hook, button) + builds,
                target=RenderTarget(
                    destination="instagram_reel",
                    resolution=(1080, 1920),
                    aspect_ratio=(9, 16),
                    target_duration=340,
                    max_duration=5395,
                    loudness_target_lufs=-14.0,
                ),
            )
        )
        placed = [c["moment_id"] for c in video_clips(plan.edl)]
        self.assertEqual(3, len(placed), "the timeline holds exactly three shots")
        self.assertEqual([hook.moment_id, peak.moment_id, button.moment_id], placed)
        self.assertEqual("pass", plan.status)


class TestSnapAndMinimumLength(unittest.TestCase):
    def test_two_equally_strong_onsets_cut_on_the_earlier_one(self):
        # Strength leads; the tie goes to the EARLIER onset, which leaves the
        # most footage behind the cut for the tail. Both orderings produce a
        # certified cut position, so nothing else can tell them apart.
        tied = moment(
            0x781,
            3031860,
            hook_potential=0.9,
            snap_points=(
                SnapPoint(
                    time=3031860, kind="shot_boundary", strength=0.95, cut_direction="in"
                ),
                SnapPoint(
                    time=3031890, kind="motion_onset", strength=0.95, cut_direction="both"
                ),
            ),
        )
        plan = plan_reel(
            request(moments=(tied, moment(0x782, 3049200, emotional_peak=0.9)))
        )
        clip = next(
            c for c in video_clips(plan.edl) if c["moment_id"] == tied.moment_id
        )
        self.assertEqual(3031860, clip["source_range"]["start_time"]["value"])

    def test_a_shot_below_the_eight_frame_floor_is_refused(self):
        # A three-frame grid, three ticks to a cut: the slots on offer are 9,
        # 6 and 3 frames. An eight-frame window can only take the 6, which is
        # under the floor, so the moment is skipped and said to be skipped. A
        # nine-frame window takes the 9 and is kept -- which pins the floor at
        # 8 from both sides rather than asserting the constant.
        rate = 30.0
        grid = BeatGrid(
            bpm=600.0,
            beats=tuple(
                Beat(index=i, time=3 * i, is_downbeat=(i % 4 == 0)) for i in range(41)
            ),
            bpm_confidence=0.9,
        )
        track = music_track(media=music_media(), source_start=0)

        def tight(tag: int, start: float, duration: float, **kwargs) -> SelectedMoment:
            return moment(
                tag,
                start,
                duration=duration,
                with_subject=False,
                snap_points=(
                    SnapPoint(
                        time=start,
                        kind="shot_boundary",
                        strength=0.9,
                        cut_direction="in",
                    ),
                ),
                safe_trim=SafeTrim(earliest_in=start, latest_out=start + duration),
                **kwargs,
            )

        too_short = tight(0x791, 3040000, 8, score=0.5)
        just_long_enough = tight(0x792, 3041000, 9, score=0.6)
        plan = plan_reel(
            request(
                rate=rate,
                beats_per_cut=3,
                beat_grid=grid,
                music=track,
                media=(source_media(), track.media),
                moments=(
                    moment(0x793, 3031860, hook_potential=0.9),
                    moment(0x794, 3049200, emotional_peak=0.9),
                    too_short,
                    just_long_enough,
                ),
                target=RenderTarget(
                    destination="instagram_reel",
                    resolution=(1080, 1920),
                    aspect_ratio=(9, 16),
                    target_duration=36,
                    max_duration=None,
                    loudness_target_lufs=-14.0,
                ),
            )
        )
        placed = {c["moment_id"] for c in video_clips(plan.edl)}
        self.assertNotIn(too_short.moment_id, placed)
        self.assertIn(just_long_enough.moment_id, placed)
        self.assertTrue(
            any(too_short.moment_id in note and "skipped" in note for note in plan.notes),
            plan.notes,
        )
        self.assertEqual(
            [9, 9, 9],
            [c["timeline_range"]["duration"]["value"] for c in video_clips(plan.edl)],
        )


class TestValidatorCatchesDamagedPlans(unittest.TestCase):
    """The validation block is a GATE, not a report.

    `workers/render-video` refuses to render a plan whose status is "fail", so
    every check in that block is load-bearing. A check only ever run against a
    correct plan is untested by construction: hard-wire it to `True` and every
    other test still passes. So each check here is handed a plan damaged in the
    one way it exists to catch, re-validated, and required to fail -- with the
    right severity, because a check demoted to "warning" leaves the plan
    renderable and is the same defect as deleting it.

    `_validate` re-derives everything from the emitted EDL, which is what makes
    this possible without a second planner: `placements` feeds only the
    mid-word check, so `()` is honest for the rest.
    """

    def setUp(self):
        self.request = request(dissolve_frames=6)
        self.plan = plan_reel(self.request)
        self.assertEqual("pass", self.plan.status)

    def damaged(self):
        return json.loads(json.dumps(self.plan.edl))

    def revalidate(self, edl, total_frames=None):
        return reel._validate(
            edl,
            self.request,
            (),
            self.plan.duration_frames if total_frames is None else total_frames,
        )

    def check(self, validation, check_id):
        return next(
            c for c in validation["checks"] if c["check_id"] == check_id
        )

    def assert_error_failure(self, validation, check_id):
        check = self.check(validation, check_id)
        self.assertFalse(check["passed"], f"{check_id} did not catch the damage")
        self.assertEqual("error", check["severity"], f"{check_id} is not a hard gate")
        self.assertEqual("fail", validation["status"])
        return check

    # -- timeline_contiguous ----------------------------------------------
    def test_a_clip_that_does_not_butt_up_fails_the_plan(self):
        edl = self.damaged()
        clips = video_clips(edl)
        clips[1]["timeline_range"]["start_time"]["value"] += 1
        check = self.assert_error_failure(
            self.revalidate(edl), "timeline_contiguous"
        )
        self.assertIn(clips[1]["clip_id"], check["detail"])

    def test_a_one_frame_overlap_fails_too(self):
        # The half-open convention means an overlap and a gap are the same
        # defect with opposite signs; a check that only looked for gaps would
        # pass a plan that renders one frame twice.
        edl = self.damaged()
        video_clips(edl)[1]["timeline_range"]["start_time"]["value"] -= 1
        self.assert_error_failure(self.revalidate(edl), "timeline_contiguous")

    # -- status precedence -------------------------------------------------
    def test_an_error_outranks_a_warning_when_both_fail(self):
        # A plan whose status reads "warn" gets rendered. If a failed error
        # check can be masked by a failed warning, the gate is open exactly
        # when two things are wrong instead of one.
        edl = self.damaged()
        video_clips(edl)[1]["timeline_range"]["start_time"]["value"] += 1
        edl["audio_plan"]["mix"]["loudness_target_lufs"] = -9.0
        validation = self.revalidate(edl)
        self.assertFalse(self.check(validation, "timeline_contiguous")["passed"])
        self.assertFalse(self.check(validation, "audio_loudness_target_set")["passed"])
        self.assertEqual("fail", validation["status"])

    # -- duration_within_max ----------------------------------------------
    def test_a_reel_over_the_platform_ceiling_fails(self):
        ceiling = reel._round_half_up(self.request.target.max_duration)
        validation = self.revalidate(self.damaged(), total_frames=ceiling + 1)
        self.assert_error_failure(validation, "duration_within_max")
        # ... and exactly on the ceiling is the good case.
        self.assertTrue(
            self.check(
                self.revalidate(self.damaged(), total_frames=ceiling),
                "duration_within_max",
            )["passed"]
        )

    # -- media_refs_resolvable --------------------------------------------
    def test_a_clip_pointing_at_an_undeclared_source_fails(self):
        edl = self.damaged()
        video_clips(edl)[0]["media_ref_id"] = "src-not-declared"
        check = self.assert_error_failure(
            self.revalidate(edl), "media_refs_resolvable"
        )
        self.assertIn("src-not-declared", check["detail"])

    # -- source_range_within_available ------------------------------------
    def test_an_audio_tail_reading_past_the_end_of_the_file_fails(self):
        # The picture is inside the file and the AUDIO is not: checking only
        # source_range would pass a plan that decodes past EOF at exactly the
        # frame the plan says a laugh lands.
        edl = self.damaged()
        clip = video_clips(edl)[0]
        ref = next(
            r for r in edl["media_refs"] if r["media_ref_id"] == clip["media_ref_id"]
        )
        available_end = (
            ref["available_range"]["start_time"]["value"]
            + ref["available_range"]["duration"]["value"]
        )
        source_end = (
            clip["source_range"]["start_time"]["value"]
            + clip["source_range"]["duration"]["value"]
        )
        clip["audio"]["audio_extends_past_out"] = {
            "value": available_end - source_end + 1,
            "rate": edl["rate"],
        }
        check = self.assert_error_failure(
            self.revalidate(edl), "source_range_within_available"
        )
        self.assertIn("audio tail", check["detail"])

    # -- determinism_digest_present ---------------------------------------
    def test_a_digest_that_is_not_a_hash_fails(self):
        edl = self.damaged()
        edl["determinism"]["inputs_digest"] = "not-a-blake3-digest"
        self.assert_error_failure(self.revalidate(edl), "determinism_digest_present")

    # -- reframe -----------------------------------------------------------
    def test_a_crop_of_the_wrong_aspect_fails(self):
        # One percent off is invisible to the eye and fatal to a 9:16 master:
        # the tolerance is 1e-9 relative, so a check loosened to a few percent
        # would pass this and still look like a check.
        edl = self.damaged()
        edl["reframe_tracks"][0]["keyframes"][0]["crop"]["w"] *= 1.01
        self.assert_error_failure(
            self.revalidate(edl), "reframe_aspect_matches_target"
        )

    def test_two_keyframes_at_the_same_time_fail(self):
        # Not "out of order" -- EQUAL. Two keyframes on one frame make the
        # interpolation ambiguous, which is how a deterministic plan renders
        # differently on a different build.
        # A subject sampled inside the kept frames, so the track has more than
        # the single held keyframe the default moments produce.
        tracked = moment(
            0x3F1,
            3031860,
            hook_potential=0.9,
            subject_track=(
                SubjectSample(time=3031860, center_x=0.40, confidence=0.9),
                SubjectSample(time=3031900, center_x=0.46, confidence=0.9),
                SubjectSample(time=3031940, center_x=0.52, confidence=0.9),
            ),
        )
        req = request(moments=(tracked, moment(0x3F2, 3049200, emotional_peak=0.9)))
        plan = plan_reel(req)
        edl = json.loads(json.dumps(plan.edl))
        keyframes = edl["reframe_tracks"][0]["keyframes"]
        self.assertGreaterEqual(len(keyframes), 2)
        keyframes[1]["time"]["value"] = keyframes[0]["time"]["value"]
        validation = reel._validate(edl, req, (), plan.duration_frames)
        check = next(
            c
            for c in validation["checks"]
            if c["check_id"] == "reframe_keyframes_ordered"
        )
        self.assertFalse(check["passed"])
        self.assertEqual("error", check["severity"])
        self.assertEqual("fail", validation["status"])

    # -- transition_handles_available --------------------------------------
    def test_a_dissolve_without_a_tail_handle_names_the_outgoing_clip(self):
        # in_offset reaches BACKWARDS into the outgoing item and out_offset
        # FORWARDS into the incoming one (edl.schema.json), so the outgoing
        # clip owes `out_offset` frames of TAIL and the incoming owes
        # `in_offset` frames of HEAD. Charging them the other way round is
        # invisible while every dissolve we emit is symmetric -- so this
        # asymmetric one is the only thing that can tell them apart.
        edl = self.damaged()
        items = video_items(edl)
        index = next(
            i for i, item in enumerate(items) if item["item_type"] == "transition"
        )
        transition = items[index]
        outgoing = items[index - 1]
        transition["in_offset"]["value"] = 0
        transition["out_offset"]["value"] = 100000
        check = self.assert_error_failure(
            self.revalidate(edl), "transition_handles_available"
        )
        self.assertEqual(
            f"{outgoing['clip_id']} has no tail handle for "
            f"{transition['transition_id']}",
            check["detail"],
        )

    def test_a_dissolve_without_a_head_handle_names_the_incoming_clip(self):
        edl = self.damaged()
        items = video_items(edl)
        index = next(
            i for i, item in enumerate(items) if item["item_type"] == "transition"
        )
        transition = items[index]
        incoming = items[index + 1]
        transition["in_offset"]["value"] = 100000
        transition["out_offset"]["value"] = 0
        check = self.assert_error_failure(
            self.revalidate(edl), "transition_handles_available"
        )
        self.assertEqual(
            f"{incoming['clip_id']} has no head handle for "
            f"{transition['transition_id']}",
            check["detail"],
        )


class TestMusicShorterThanTheReel(unittest.TestCase):
    """A 15-second cut over a 3-second sting is the ordinary case.

    The planner used to declare `loop: true` and then write a source_range the
    length of the REEL over a track that short, which its own
    `source_range_within_available` check fails at severity error -- so every
    such reel came out `status: "fail"`, unrenderable, with an error message
    about source ranges rather than about looping.
    """

    def a1_items(self, edl):
        return next(t for t in edl["tracks"] if t["track_id"] == "a1")["items"]

    def test_a_short_track_is_laid_down_repeatedly_and_the_plan_passes(self):
        short = music_media(available_duration=120)
        plan = plan_reel(
            request(
                music=music_track(media=short, source_start=0),
                media=(source_media(), short),
            )
        )
        self.assertEqual("pass", plan.status)
        cue = plan.edl["audio_plan"]["music"][0]
        items = self.a1_items(plan.edl)
        # contracts#59: the cue places nothing. It names the clips that place
        # it, in order, and every pass is one of them.
        self.assertEqual([i["clip_id"] for i in items], cue["clip_ids"])
        self.assertGreater(len(items), 1, "a track shorter than the reel is laid down twice")
        # The first pass reads what the track HAS ...
        self.assertEqual(120, items[0]["source_range"]["duration"]["value"])
        # ... and the passes together cover the reel on the timeline.
        self.assertEqual(
            plan.duration_frames,
            sum(i["timeline_range"]["duration"]["value"] for i in items),
        )

        self.assertEqual(math.ceil(plan.duration_frames / 120), len(items))
        self.assertEqual(len(items), len({i["clip_id"] for i in items}))
        cursor = 0
        for item in items:
            self.assertEqual(cursor, item["timeline_range"]["start_time"]["value"])
            span = item["timeline_range"]["duration"]["value"]
            self.assertEqual(span, item["source_range"]["duration"]["value"])
            self.assertEqual(0, item["source_range"]["start_time"]["value"])
            self.assertLessEqual(span, 120, "a pass longer than the track exists")
            cursor += span
        self.assertEqual(plan.duration_frames, cursor)
        # The fade belongs to the cue, not to every pass of it.
        self.assertEqual(
            [None] * (len(items) - 1) + [36],
            [
                None if i["audio"]["fade_out"] is None else i["audio"]["fade_out"]["value"]
                for i in items
            ],
        )
        self.assertTrue(any("loops" in note for note in plan.notes), plan.notes)
        _assert_schema_valid(self, plan.edl)

    def test_a_track_exactly_as_long_as_the_reel_does_not_loop(self):
        reference = plan_reel(request())
        total = reference.duration_frames
        exact = music_media(available_duration=total + 1798)
        plan = plan_reel(
            request(
                music=music_track(media=exact), media=(source_media(), exact)
            )
        )
        cue = plan.edl["audio_plan"]["music"][0]
        items = self.a1_items(plan.edl)
        self.assertEqual(1, len(items), "a track that exactly covers the reel is not a loop")
        self.assertEqual([items[0]["clip_id"]], cue["clip_ids"])
        self.assertEqual(total, items[0]["source_range"]["duration"]["value"])
        self.assertEqual("pass", plan.status)

        # One frame shorter -- measured FROM THE CUE IN-POINT, not from the
        # top of the file -- and it does loop.
        short = music_media(available_duration=total + 1798 - 1)
        looped = plan_reel(
            request(music=music_track(media=short), media=(source_media(), short))
        )
        self.assertEqual(2, len(self.a1_items(looped.edl)))
        self.assertEqual(
            [i["clip_id"] for i in self.a1_items(looped.edl)],
            looped.edl["audio_plan"]["music"][0]["clip_ids"],
            "a loop is two clips the cue claims, not a boolean",
        )
        self.assertEqual("pass", looped.status)

    def test_a_track_that_is_not_a_whole_number_of_frames_is_read_in_whole_frames(self):
        # 120.5 frames of track: the half frame does not exist as a frame, and
        # a pass declaring 120.5 while the decoder has 120 would read a frame
        # that is not there.
        ragged = music_media(available_duration=120.5)
        plan = plan_reel(
            request(
                music=music_track(media=ragged, source_start=0),
                media=(source_media(), ragged),
            )
        )
        cue = plan.edl["audio_plan"]["music"][0]
        items = self.a1_items(plan.edl)
        self.assertEqual([i["clip_id"] for i in items], cue["clip_ids"])
        spans = [item["source_range"]["duration"]["value"] for item in items]
        self.assertEqual(120, spans[0])
        self.assertTrue(all(span <= 120 for span in spans), spans)
        self.assertEqual("pass", plan.status)

    def test_a_cue_in_point_past_the_end_of_the_track_is_refused(self):
        track = music_media(available_duration=100)
        with self.assertRaises(ValueError):
            request(
                music=music_track(media=track, source_start=100),
                media=(source_media(), track),
            )


class TestSingleShotReel(unittest.TestCase):
    """One moment in, one shot out -- and that is a legitimate reel.

    `_assign_acts` calls the sole moment the peak, which used to leave the
    required hook beat empty and hard-fail the plan. A render worker gates on
    that status, so a perfectly good one-shot cut was unrenderable.
    """

    def setUp(self):
        self.plan = plan_reel(
            request(moments=(moment(0xE1, 3031860, emotional_peak=0.9),))
        )

    def test_it_passes_its_own_validation(self):
        self.assertEqual("pass", self.plan.status)
        self.assertEqual(
            [], [c for c in self.plan.edl["validation"]["checks"] if not c["passed"]]
        )

    def test_the_one_clip_satisfies_both_required_beats(self):
        clips = video_clips(self.plan.edl)
        self.assertEqual(1, len(clips))
        satisfied = {
            beat["beat_id"]: beat["satisfied_by_clip_ids"]
            for act in self.plan.edl["story_arc"]["acts"]
            for beat in act["beats"]
            if beat["required"]
        }
        self.assertEqual(
            {"beat-hook-open": ["clip-01"], "beat-peak": ["clip-01"]}, satisfied
        )
        # It is still the peak on the clip itself, and it still carries the
        # marker the human editor looks for.
        self.assertEqual("beat-peak", clips[0]["story_beat_id"])
        self.assertEqual(1, len(clips[0]["markers"]))

    def test_the_double_duty_is_stated_and_the_energy_curve_stays_single_valued(self):
        self.assertTrue(
            any("single-shot" in note for note in self.plan.notes), self.plan.notes
        )
        curve = self.plan.edl["story_arc"]["energy_curve"]
        self.assertEqual(1, len(curve))
        _assert_schema_valid(self, self.plan.edl)


class TestAmbientDisabled(unittest.TestCase):
    """With ambient off the location sound is not in the mix at all."""

    def _speech_request(self, **kwargs):
        start = 3049200
        speech = moment(
            0x7A1,
            start,
            duration=210,
            emotional_peak=0.93,
            score=0.91,
            speech_ratio=0.31,
            safe_trim=SafeTrim(earliest_in=start, latest_out=start + 210),
        )
        return request(
            moments=(speech, moment(0x7A2, 3031860, hook_potential=0.9)), **kwargs
        )

    def test_ambient_on_ducks_the_music_and_keeps_the_clip_audio(self):
        plan = plan_reel(self._speech_request())
        self.assertEqual(1, len(plan.edl["audio_plan"]["ducking"]))
        self.assertIsNotNone(plan.edl["audio_plan"]["ambient"]["high_pass"])
        for clip in video_clips(plan.edl):
            self.assertFalse(clip["audio"]["muted"])
            self.assertNotEqual(0.0, clip["audio"]["gain_db"])

    def test_ambient_off_mutes_the_clips_and_stops_ducking_under_silence(self):
        # Ducking here would pull the music down 9 dB for a quarter of the reel
        # with nothing underneath it: audible, wrong, and silent in the plan.
        plan = plan_reel(self._speech_request(ambient=AmbientSettings(enabled=False)))
        # contracts#53 removed AmbientPlan.enabled: a bed is silent because the
        # clip says muted, in one place, where a reader can see which sound is
        # gone. The planner setting still decides -- it just does not travel as
        # a second switch the renderer would have to reconcile.
        for clip in video_clips(plan.edl):
            self.assertTrue(clip["audio"]["muted"])
        self.assertEqual([], plan.edl["audio_plan"]["ducking"])
        # A high-pass over a mix with no location sound in it filters silence.
        self.assertIsNone(plan.edl["audio_plan"]["ambient"]["high_pass"])
        self.assertEqual("pass", plan.status)
        _assert_schema_valid(self, plan.edl)


class TestAudioTailBounds(unittest.TestCase):
    """The L-cut is the one place the plan reads outside the picture."""

    def tail_of(self, edl, moment_id):
        clip = next(c for c in video_clips(edl) if c["moment_id"] == moment_id)
        tail = clip["audio"]["audio_extends_past_out"]
        return clip, 0 if tail is None else tail["value"]

    def test_the_tail_stops_at_the_end_of_the_media_file(self):
        # The moment claims 400 frames; the file holds 150. `latest_out` is a
        # claim about the MOMENT, and whether those frames exist is a different
        # question -- one FFmpeg answers with silence or a decode error at
        # exactly the frame the plan says a laugh lands.
        truncated = SourceMedia(
            media_ref_id="src-truncated",
            media_id="b" * 64,
            available_start=2000,
            available_duration=150,
            aspect_ratio=(16, 9),
            color_encoding="bt709",
        )
        laugh = SelectedMoment(
            moment_id=f"{0x7B1:064x}",
            media_id=truncated.media_id,
            source_start=2000,
            source_duration=400,
            score=0.6,
            hook_potential=0.99,
            snap_points=(
                SnapPoint(
                    time=2000, kind="shot_boundary", strength=0.9, cut_direction="in"
                ),
            ),
            safe_trim=SafeTrim(
                earliest_in=2000, latest_out=2400, preserve_audio_tail=True
            ),
        )
        plan = plan_reel(
            request(
                media=(source_media(), music_media(), truncated),
                moments=(laugh, moment(0x7B2, 3049200, emotional_peak=0.95)),
            )
        )
        clip, tail = self.tail_of(plan.edl, laugh.moment_id)
        source_end = (
            clip["source_range"]["start_time"]["value"]
            + clip["source_range"]["duration"]["value"]
        )
        self.assertGreater(tail, 0)
        self.assertEqual(2150, source_end + tail, "the tail reads past the file")
        self.assertEqual("pass", plan.status)
        _assert_schema_valid(self, plan.edl)

    def test_the_tail_never_outlasts_the_shot_it_belongs_to(self):
        # 288 frames of spare audio behind a 112-frame shot: holding all of it
        # would run the laugh over the next TWO shots.
        start = 3040000
        talky = SelectedMoment(
            moment_id=f"{0x7C1:064x}",
            media_id=RIDE_MEDIA_ID,
            source_start=start,
            source_duration=400,
            score=0.6,
            hook_potential=0.99,
            snap_points=(
                SnapPoint(
                    time=start, kind="shot_boundary", strength=0.9, cut_direction="in"
                ),
            ),
            safe_trim=SafeTrim(
                earliest_in=start,
                latest_out=start + 400,
                speech_safe_out=start + 120,
                preserve_audio_tail=True,
            ),
        )
        plan = plan_reel(
            request(moments=(talky, moment(0x7C2, 3049200, emotional_peak=0.95)))
        )
        clip, tail = self.tail_of(plan.edl, talky.moment_id)
        self.assertEqual(clip["source_range"]["duration"]["value"], tail)

    def test_the_last_shot_gets_no_tail_because_nothing_follows_it(self):
        start = 3055440
        closing = SelectedMoment(
            moment_id=f"{0x7D1:064x}",
            media_id=RIDE_MEDIA_ID,
            source_start=start,
            source_duration=400,
            score=0.6,
            motion_energy=0.01,
            snap_points=(
                SnapPoint(
                    time=start, kind="shot_boundary", strength=0.9, cut_direction="in"
                ),
            ),
            safe_trim=SafeTrim(
                earliest_in=start,
                latest_out=start + 400,
                speech_safe_out=start + 120,
                preserve_audio_tail=True,
            ),
        )
        plan = plan_reel(
            request(
                moments=(
                    moment(0x7D2, 3031860, hook_potential=0.99),
                    moment(0x7D3, 3049200, emotional_peak=0.95),
                    closing,
                )
            )
        )
        clips = video_clips(plan.edl)
        self.assertEqual(closing.moment_id, clips[-1]["moment_id"])
        self.assertIsNone(clips[-1]["audio"]["audio_extends_past_out"])


class TestSafeTrimStaysInsideItsMoment(unittest.TestCase):
    """`window()` hands `latest_out` straight to the placement walk.

    A trim wider than the moment therefore lets a clip cut into footage the
    analysis layer never scored -- and may have discarded as shaken or blown --
    with nothing downstream to catch it: `source_range_within_available`
    checks MEDIA bounds, not moment bounds.
    """

    def test_a_trim_reaching_past_the_end_of_the_moment_is_refused(self):
        with self.assertRaises(ValueError) as caught:
            SelectedMoment(
                moment_id=f"{0x7E1:064x}",
                media_id=RIDE_MEDIA_ID,
                source_start=2000,
                source_duration=100,
                safe_trim=SafeTrim(earliest_in=2000, latest_out=9000),
            )
        self.assertIn("latest_out", str(caught.exception))

    def test_a_trim_starting_before_the_moment_is_refused(self):
        with self.assertRaises(ValueError):
            SelectedMoment(
                moment_id=f"{0x7E2:064x}",
                media_id=RIDE_MEDIA_ID,
                source_start=2000,
                source_duration=100,
                safe_trim=SafeTrim(earliest_in=1900, latest_out=2100),
            )

    def test_a_speech_safe_bound_outside_the_moment_is_refused(self):
        with self.assertRaises(ValueError) as caught:
            SelectedMoment(
                moment_id=f"{0x7E3:064x}",
                media_id=RIDE_MEDIA_ID,
                source_start=2000,
                source_duration=100,
                safe_trim=SafeTrim(
                    earliest_in=2000, latest_out=2100, speech_safe_out=2500
                ),
            )
        self.assertIn("speech_safe_out", str(caught.exception))

    def test_a_trim_exactly_on_the_moment_bounds_is_accepted(self):
        SelectedMoment(
            moment_id=f"{0x7E4:064x}",
            media_id=RIDE_MEDIA_ID,
            source_start=2000,
            source_duration=100,
            safe_trim=SafeTrim(earliest_in=2000, latest_out=2100),
        )


class TestTransitionLabelling(unittest.TestCase):
    def test_the_dissolve_is_named_for_the_clip_it_dissolves_into(self):
        # Two moments: the acts are hook and peak, and there is no button at
        # all. A transition hard-coded "xfade-into-button" would be stating a
        # creative decision the plan did not make.
        plan = plan_reel(
            request(
                moments=(
                    moment(0x7F1, 3031860, hook_potential=0.9),
                    moment(0x7F2, 3049200, emotional_peak=0.9),
                ),
                dissolve_frames=6,
            )
        )
        acts = {act["act_id"] for act in plan.edl["story_arc"]["acts"]}
        self.assertNotIn("act-button", acts)
        items = video_items(plan.edl)
        transition = next(i for i in items if i["item_type"] == "transition")
        incoming = next(
            i for i in items[items.index(transition) + 1 :] if i["item_type"] == "clip"
        )
        self.assertEqual(
            f"xfade-into-{incoming['clip_id']}", transition["transition_id"]
        )
        self.assertNotIn("button", transition["transition_id"])
        _assert_schema_valid(self, plan.edl)


class TestEmittedDecisionsAreStated(unittest.TestCase):
    """Constants that reach the renderer as instructions.

    Each of these is a decision the plan is making on the user's behalf; a
    plan that quietly changed one would render differently and validate just
    the same.
    """

    def test_an_all_sdr_plan_states_its_colour_path_and_no_tone_map(self):
        # contracts#58. The pipeline is REQUIRED even when it is the boring
        # case: a plan that says nothing about colour is a plan that leaves the
        # decision to the renderer, and a wrong one is invisible until print.
        # A tone map here would be a grade nobody asked for.
        self.assertEqual(
            {
                "working_space": "linear_bt709",
                "output_encoding": "bt709",
                "tone_map": None,
            },
            plan_reel(request()).edl["color_pipeline"],
        )

    def test_an_hdr_source_pulls_a_fully_specified_tone_map_into_the_plan(self):
        # The tone map is DERIVED from the sources, not copied from a setting:
        # it appears exactly when a source needs it. Every field it carries is
        # required, because the boolean it replaced named no operator, and
        # hable and mobius differ most on exactly the shot that triggers them.
        plan = plan_reel(
            request(
                media=(
                    source_media(color_encoding="bt2100_pq", source_peak_nits=1000.0),
                    music_media(),
                )
            )
        )
        self.assertEqual(
            {
                "operator": "hable",
                "operator_param": None,
                "reference_white_nits": 100.0,
                "desaturation": 2.0,
            },
            plan.edl["color_pipeline"]["tone_map"],
        )
        _assert_schema_valid(self, plan.edl)

    def test_a_reinhard_tone_map_carries_its_curve_parameter(self):
        plan = plan_reel(
            request(
                media=(
                    source_media(color_encoding="bt2100_hlg", source_peak_nits=1000.0),
                    music_media(),
                ),
                color=ColorSettings(
                    tone_map_operator="reinhard", tone_map_operator_param=0.5
                ),
            )
        )
        tone_map = plan.edl["color_pipeline"]["tone_map"]
        self.assertEqual("reinhard", tone_map["operator"])
        self.assertEqual(0.5, tone_map["operator_param"])
        _assert_schema_valid(self, plan.edl)

    def test_hlg_may_not_claim_a_peak_other_than_the_nominal_display(self):
        # HLG's EOTF is defined against a nominal display, and this contract
        # fixes that at 1000 cd/m^2. A source claiming 4000 is describing a
        # decode that did not happen, and every highlight would land wrong.
        with self.assertRaisesRegex(ValueError, "decode that did not happen"):
            source_media(color_encoding="bt2100_hlg", source_peak_nits=4000.0)

    def test_an_hdr_source_must_state_the_peak_it_is_graded_to(self):
        with self.assertRaisesRegex(ValueError, "no stated peak"):
            source_media(color_encoding="bt2100_pq")

    def test_an_sdr_source_may_not_carry_a_peak(self):
        with self.assertRaisesRegex(ValueError, "not HDR and declares"):
            source_media(color_encoding="bt709", source_peak_nits=1000.0)

    def test_a_music_source_has_no_primaries(self):
        with self.assertRaisesRegex(ValueError, "sound has no primaries"):
            music_media(color_encoding="bt709")

    def test_the_colour_check_is_re_derived_from_the_emitted_plan(self):
        # The check must read the EDL, not the ColorSettings that wrote it --
        # a validator that reads the writer's variables cannot catch the writer
        # being wrong. Corrupting the emitted refs after the fact must fail it.
        plan = plan_reel(request())
        self.assertTrue(color_pipeline_check(plan.edl)["passed"])

        washed_out = copy.deepcopy(plan.edl)
        for ref in washed_out["media_refs"]:
            if ref["media_kind"] == "video":
                ref["color_encoding"] = "bt2100_pq"
                ref["source_peak_nits"] = 1000.0
        check = color_pipeline_check(washed_out)
        self.assertFalse(check["passed"])
        self.assertIn("no tone_map", check["detail"])

    def test_the_colour_check_catches_each_way_the_plan_can_lie(self):
        plan = plan_reel(request())

        pointless = copy.deepcopy(plan.edl)
        pointless["color_pipeline"]["tone_map"] = {
            "operator": "hable",
            "operator_param": None,
            "reference_white_nits": 100.0,
            "desaturation": 2.0,
        }
        self.assertIn("nobody asked for", color_pipeline_check(pointless)["detail"])

        silent = copy.deepcopy(plan.edl)
        for ref in silent["media_refs"]:
            if ref["media_kind"] == "video":
                ref["color_encoding"] = None
        self.assertIn("declaring color_encoding", color_pipeline_check(silent)["detail"])

        loud_sound = copy.deepcopy(plan.edl)
        for ref in loud_sound["media_refs"]:
            if ref["media_kind"] == "music":
                ref["color_encoding"] = "bt709"
        self.assertIn("sound has no primaries", color_pipeline_check(loud_sound)["detail"])

        wrong_peak = copy.deepcopy(plan.edl)
        for ref in wrong_peak["media_refs"]:
            if ref["media_kind"] == "video":
                ref["color_encoding"] = "bt2100_hlg"
                ref["source_peak_nits"] = 4000.0
        wrong_peak["color_pipeline"]["tone_map"] = {
            "operator": "hable",
            "operator_param": None,
            "reference_white_nits": 100.0,
            "desaturation": 2.0,
        }
        self.assertIn("nominal display", color_pipeline_check(wrong_peak)["detail"])

    def test_the_timeline_starts_at_zero_even_when_the_music_does_not(self):
        # The lead-in is a Gap, never a shifted origin: `global_start_time` is
        # where the TIMELINE begins, and moving it would desync it from a beat
        # grid that is authored in timeline time.
        offset = 60
        late = BeatGrid(
            bpm=128.0,
            bpm_confidence=0.97,
            beats=tuple(
                Beat(index=i, time=offset + i * BEAT_INTERVAL, is_downbeat=(i % 4 == 0))
                for i in range(33)
            ),
        )
        plan = plan_reel(request(beat_grid=late))
        self.assertEqual(0, plan.edl["global_start_time"]["value"])
        self.assertEqual("gap", video_items(plan.edl)[0]["item_type"])

    def test_whole_frame_times_are_emitted_as_integers(self):
        # RFC 8785 writes 112.0 as "112", so this cannot change a digest -- but
        # it does change what a Rust or TypeScript reader deserialises into,
        # and "112.0" in a plan invites a float frame index downstream.
        #
        # The request deliberately carries FLOATS for whole-frame times: a
        # planner that merely passes its inputs through emits integers here
        # only because the caller happened to hand it integers.
        floaty = moment(0x7F8, 3031860.0, duration=300.0, hook_potential=0.9)
        plan = plan_reel(
            request(
                moments=(floaty, moment(0x7F9, 3049200.0, emotional_peak=0.9)),
                target=RenderTarget(
                    destination="instagram_reel",
                    resolution=(1080, 1920),
                    aspect_ratio=(9, 16),
                    target_duration=899.0,
                    max_duration=5395.0,
                    loudness_target_lufs=-14.0,
                ),
            )
        )
        self.assertIsInstance(plan.edl["target"]["target_duration"]["value"], int)
        self.assertIsInstance(plan.edl["target"]["max_duration"]["value"], int)
        self.assertIsInstance(plan.edl["global_start_time"]["value"], int)
        for clip in video_clips(plan.edl):
            for field in ("source_range", "timeline_range"):
                for part in ("start_time", "duration"):
                    value = clip[field][part]["value"]
                    self.assertIsInstance(
                        value, int, f"{field}.{part} is not an integer"
                    )

    def test_the_crop_travels_at_most_a_third_of_the_frame_per_second(self):
        # 0.35 normalised units per second is the difference between a
        # considered pan and a whip. The budget is spent on the PLAN, so the
        # exact clamped position is checkable from the declared limit.
        self.assertEqual(0.35, ReframeSettings().max_velocity_per_second)
        jumpy = moment(
            0x7F5,
            3031860,
            hook_potential=0.9,
            snap_points=(
                SnapPoint(
                    time=3031860, kind="shot_boundary", strength=0.9, cut_direction="in"
                ),
            ),
            subject_track=(
                SubjectSample(time=3031860, center_x=0.10, confidence=0.9),
                SubjectSample(time=3031866, center_x=0.90, confidence=0.9),
            ),
        )
        plan = plan_reel(
            request(moments=(jumpy, moment(0x7F6, 3049200, emotional_peak=0.9)))
        )
        clip = next(
            c for c in video_clips(plan.edl) if c["moment_id"] == jumpy.moment_id
        )
        track = next(
            t
            for t in plan.edl["reframe_tracks"]
            if t["reframe_track_id"] == clip["reframe_track_id"]
        )
        self.assertEqual(0.35, track["smoothing"]["max_velocity_per_second"])
        first, second = track["keyframes"][0], track["keyframes"][1]
        # The subject jumps 0.8 of the frame in 6 frames; the crop is allowed
        # 6 * 0.35 / rate, and lags the subject for the rest.
        self.assertEqual(0.0, first["crop"]["x"])
        self.assertAlmostEqual(6 * 0.35 / RATE, second["crop"]["x"], places=6)

    def test_an_unstated_loudness_target_defaults_to_minus_fourteen_and_says_so(self):
        plan = plan_reel(
            request(
                target=RenderTarget(
                    destination="instagram_reel",
                    resolution=(1080, 1920),
                    aspect_ratio=(9, 16),
                    target_duration=899,
                    max_duration=5395,
                    loudness_target_lufs=None,
                )
            )
        )
        self.assertEqual(-14.0, plan.edl["audio_plan"]["mix"]["loudness_target_lufs"])
        self.assertTrue(any("-14 LUFS" in note for note in plan.notes), plan.notes)


class TestCandidatePoolSurvivesTheCapacityCut(unittest.TestCase):
    def test_dropped_moments_stay_in_the_candidate_pool(self):
        # "Retained so a revision can swap in an alternative without re-running
        # retrieval" (edl.schema.json#StoryBeat). Handing the arc the
        # post-capacity list makes the pool equal the placed clips exactly,
        # which carries no information at all -- and the drop notes live on
        # ReelPlan, not in the EDL, so a persisted plan has lost them.
        moments = tuple(
            moment(
                0x800 + i,
                3031860 + 400 * i,
                score=0.30 + 0.05 * i,
                hook_potential=0.1 * i,
                emotional_peak=0.05 * i,
                motion_energy=0.4,
            )
            for i in range(10)
        )
        plan = plan_reel(
            request(
                moments=moments,
                target=RenderTarget(
                    destination="instagram_reel",
                    resolution=(1080, 1920),
                    aspect_ratio=(9, 16),
                    target_duration=340,
                    max_duration=5395,
                    loudness_target_lufs=-14.0,
                ),
            )
        )
        placed = {c["moment_id"] for c in video_clips(plan.edl)}
        candidates = {
            mid
            for act in plan.edl["story_arc"]["acts"]
            for beat in act["beats"]
            for mid in beat["candidate_moment_ids"]
        }
        self.assertEqual(3, len(placed))
        self.assertEqual({m.moment_id for m in moments}, candidates)
        self.assertLess(len(placed), len(candidates))
        self.assertTrue(any("dropped" in note for note in plan.notes), plan.notes)


class TestBeatToleranceIsEnforced(unittest.TestCase):
    def test_a_grid_the_planner_cannot_hit_fails_the_plan(self):
        # The <50ms downbeat gate is the reason `alignment_error_ms` is in the
        # plan at all. Frames are integers, so a grid whose beats fall between
        # frames cannot be hit exactly; with a 1ms tolerance the planner has to
        # SAY it missed rather than emit a plan the render worker would trust.
        plan = plan_reel(request(beat_grid=beat_grid(tolerance_ms=1.0)))
        check = next(
            c
            for c in plan.edl["validation"]["checks"]
            if c["check_id"] == "beat_alignment_within_tolerance"
        )
        self.assertFalse(check["passed"])
        self.assertEqual("error", check["severity"])
        self.assertEqual("fail", plan.status)
        # The worst clip is named, and the error it reports is a real one.
        worst = next(
            c for c in video_clips(plan.edl) if c["clip_id"] == check["clip_id"]
        )
        self.assertGreater(abs(worst["beat_lock"]["alignment_error_ms"]), 1.0)

    def test_the_same_grid_at_the_contract_tolerance_passes(self):
        plan = plan_reel(request(beat_grid=beat_grid(tolerance_ms=50.0)))
        self.assertEqual("pass", plan.status)


class TestOtioMapping(unittest.TestCase):
    def test_the_exporter_flags_are_honest(self):
        otio = plan_reel(request()).edl["otio"]
        self.assertEqual("memory_engine", otio["metadata_namespace"])
        self.assertEqual([], otio["unmapped_fields"])
        # A planner claiming a verified round trip would be claiming a test it
        # never ran; only the exporter may set this.
        self.assertFalse(otio["round_trip_verified"])

    def test_video_times_all_share_the_timeline_rate(self):
        edl = plan_reel(request()).edl
        rate = edl["rate"]
        track = next(t for t in edl["tracks"] if t["kind"] == "video")
        for item in track["items"]:
            for field in ("source_range", "timeline_range"):
                span = item.get(field)
                if span is None:
                    continue
                self.assertEqual(rate, span["start_time"]["rate"])
                self.assertEqual(rate, span["duration"]["rate"])

    def test_frame_positions_are_whole_frames(self):
        for clip in video_clips(plan_reel(request()).edl):
            for field in ("source_range", "timeline_range"):
                for part in ("start_time", "duration"):
                    value = clip[field][part]["value"]
                    self.assertEqual(value, int(value), f"{field}.{part} is fractional")


class TestEncodeProfile(unittest.TestCase):
    """contracts#56. The delivery encode is in the plan, and it is a decision."""

    def test_the_plan_carries_the_destination_profile(self):
        edl = plan_reel(request()).edl
        encode = edl["target"]["encode"]
        self.assertEqual("instagram-reel-h264-crf20-v1", encode["profile_id"])
        self.assertEqual("mp4", encode["container"])
        self.assertEqual("h264", encode["video"]["codec"])
        self.assertEqual("libx264", encode["video"]["encoder"])
        self.assertEqual(
            {"mode": "crf", "quality": 20, "bit_rate_kbps": None},
            encode["video"]["rate_control"],
        )
        # Stated rather than left to x264's 250-frame default: a platform's own
        # re-encode seeks against the keyframes it is handed.
        self.assertEqual(120, encode["video"]["keyframe_interval_frames"])
        # Not a performance setting. x264 slices a frame across threads and the
        # slice boundaries move with the count.
        self.assertEqual(1, encode["encoder_threads"])
        _assert_schema_valid(self, edl)

    def test_each_destination_has_its_own_versioned_profile(self):
        ids = {
            destination: encode_profile_for(destination)["profile_id"]
            for destination in ("master", "instagram_reel", "youtube", "whatsapp_status")
        }
        self.assertEqual(len(ids), len(set(ids.values())), ids)

    def test_two_plans_that_differ_only_by_encode_have_different_digests(self):
        """The digest claims two plans with the same value are the same cut.

        Before contracts#56 that claim was false for the one thing a viewer
        actually receives: the encode arrived in the render job, so two renders
        of one EDL could differ by an entire codec with nothing recording which.
        """
        baseline = plan_reel(request()).edl
        louder = encode_profile_for("instagram_reel")
        louder["profile_id"] = "instagram-reel-h264-crf14-v1"
        louder["video"]["rate_control"]["quality"] = 14
        other = plan_reel(
            request(
                target=RenderTarget(
                    destination="instagram_reel",
                    resolution=(1080, 1920),
                    aspect_ratio=(9, 16),
                    encode=louder,
                )
            )
        ).edl
        self.assertNotEqual(
            baseline["determinism"]["inputs_digest"], other["determinism"]["inputs_digest"]
        )
        self.assertNotEqual(baseline["edl_id"], other["edl_id"])

    def test_an_encoder_that_does_not_produce_the_declared_codec_is_refused(self):
        profile = encode_profile_for("master")
        profile["video"]["codec"] = "hevc"  # still encoded by libx264
        with self.assertRaisesRegex(ValueError, "produces"):
            RenderTarget(
                destination="master",
                resolution=(1080, 1920),
                aspect_ratio=(9, 16),
                encode=profile,
            )

    def test_a_crf_profile_that_names_no_quality_is_refused(self):
        profile = encode_profile_for("master")
        profile["video"]["rate_control"]["quality"] = None
        with self.assertRaisesRegex(ValueError, "needs a quality value"):
            RenderTarget(
                destination="master",
                resolution=(1080, 1920),
                aspect_ratio=(9, 16),
                encode=profile,
            )


class TestSpanAssemblyIsSelfDescribing(unittest.TestCase):
    """contracts#55. The member order is what the assembly's identity is made of."""

    def test_the_plan_names_the_members_and_the_verified_continuity(self):
        edl = plan_reel(request()).edl
        ref = next(r for r in edl["media_refs"] if r["is_span_assembly"])
        self.assertEqual(list(RIDE_MEMBER_IDS), ref["member_media_ids"])
        self.assertEqual("verified_gapless", ref["continuity"])
        self.assertEqual(
            RIDE_MEDIA_ID, blake3_hex("".join(ref["member_media_ids"]).encode("ascii"))
        )
        check = next(
            c
            for c in edl["validation"]["checks"]
            if c["check_id"] == "span_continuity_verified"
        )
        self.assertTrue(check["passed"])
        _assert_schema_valid(self, edl)

    def test_a_non_assembly_names_no_members(self):
        edl = plan_reel(request(music=music_track())).edl
        ref = next(r for r in edl["media_refs"] if not r["is_span_assembly"])
        self.assertEqual([], ref["member_media_ids"])
        self.assertIsNone(ref["continuity"])

    def test_a_reversed_member_list_is_refused_at_the_source(self):
        # It hashes to a different assembly, which is exactly right: a different
        # order is a different recording, and every source timecode drawn from
        # it after the split would be wrong.
        with self.assertRaisesRegex(ValueError, "mis-ordered"):
            source_media(member_media_ids=tuple(reversed(RIDE_MEMBER_IDS)))

    def test_an_assembly_of_one_file_is_refused(self):
        with self.assertRaisesRegex(ValueError, "assembly of one file"):
            source_media(member_media_ids=RIDE_MEMBER_IDS[:1])

    def test_an_assembly_must_declare_a_continuity(self):
        with self.assertRaisesRegex(ValueError, "continuity"):
            source_media(continuity=None)

    def test_an_unverified_span_fails_the_plan_rather_than_being_concatenated(self):
        # Nothing in the plan carries the length of a gap to compensate with, so
        # the only honest answer is to refuse. A verified_gap assembly rendered
        # as if it were gapless puts every cut after the split on the wrong
        # frame, and the picture still looks like a picture.
        plan = plan_reel(
            request(
                media=(source_media(continuity="verified_gap"),),
                music=None,
                beat_grid=None,
            )
        )
        check = next(
            c
            for c in plan.edl["validation"]["checks"]
            if c["check_id"] == "span_continuity_verified"
        )
        self.assertFalse(check["passed"])
        self.assertEqual("fail", plan.status)


class TestAmbientFilterAndDuckEnvelope(unittest.TestCase):
    """contracts#53 and #54: a filter with a response, and a duck with a curve."""

    def test_the_high_pass_states_its_corner_and_its_order(self):
        edl = plan_reel(request()).edl
        self.assertEqual(
            {"corner_hz": 120.0, "order": 4}, edl["audio_plan"]["ambient"]["high_pass"]
        )

    def test_a_second_order_filter_is_emitted_when_asked_for(self):
        edl = plan_reel(
            request(ambient=AmbientSettings(high_pass_hz=80.0, high_pass_order=2))
        ).edl
        self.assertEqual(
            {"corner_hz": 80.0, "order": 2}, edl["audio_plan"]["ambient"]["high_pass"]
        )

    def test_an_order_the_contract_does_not_define_is_refused(self):
        with self.assertRaisesRegex(ValueError, "must be 2 or 4"):
            AmbientSettings(high_pass_order=3)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
