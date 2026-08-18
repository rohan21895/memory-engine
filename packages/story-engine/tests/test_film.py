"""Tests for the film planner.

WHAT THESE TESTS ARE TRYING TO CATCH

The reel suite's opening note applies unchanged: every defect this repository
has found was silent -- a plausible number and no exception. So the assertions
here are about VALUES and INVARIANTS.

What is different is WHERE the film can be silently wrong, and those are the
places most of this file is aimed at:

  * PACING. "The pacing varies" is the one claim a film planner exists to make
    and the easiest one to fake. So the tests assert the actual holds: that an
    act changes them, that content energy changes them in the stated direction,
    that an UNMEASURED energy leaves them alone rather than lengthening them,
    and that `window_limited` counts the shots whose length the analysis layer
    decided instead of the policy.
  * SPEECH. The in-point preference, the out-point extension and the pull-back
    are three arithmetic decisions that would each produce a perfectly playable
    film if they were wrong. They are asserted against exact frame numbers.
  * ABSENCE. `no_mid_word_cut` must be ABSENT with no words and present at
    severity ERROR with them. A test that only checks "the plan passes" would
    pass on a planner that emitted a fabricated passing finding, which is the
    exact defect the story stage's docstring has been warning about since
    before this module existed.
  * THE RENDERER'S GATE. `workers/render-video` requires a PASSING finding for
    six named checks and refuses several declarations outright. Those are
    asserted here so a plan that no renderer accepts fails in this suite rather
    than at the end of a pipeline run.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = PACKAGE_ROOT.parent.parent
SCHEMA_DIR = REPO_ROOT / "contracts" / "schemas"

# The CI runner invokes `python3 -m unittest discover -s tests`, which puts the
# tests directory on sys.path and not the package.
sys.path.insert(0, str(PACKAGE_ROOT))

from memory_engine_story import film as film_module  # noqa: E402
from memory_engine_story import reel as reel_module  # noqa: E402
from memory_engine_story.film import (  # noqa: E402
    ActShape,
    AmbientSettings,
    FilmRequest,
    FilmTooShort,
    MusicLicense,
    MusicTrack,
    Pacing,
    ReframeSettings,
    RenderTarget,
    SafeTrim,
    SelectedMoment,
    SnapPoint,
    SourceMedia,
    SubjectSample,
    Word,
    pacing_spread,
    plan_film,
)

try:  # jsonschema is a CI dependency; the rest of the suite must not need it.
    from jsonschema import Draft202012Validator
    from referencing import Registry, Resource

    _HAVE_JSONSCHEMA = True
except ImportError:  # pragma: no cover
    _HAVE_JSONSCHEMA = False

# 30000/1001, the exact NTSC rational. Used throughout rather than a friendly
# 30.0, so every rounding rule is exercised at the rate the contract calls out.
RATE = 30000.0 / 1001.0

MEDIA_ID = "a371bd849cc440490b2013581e0e77ff53db9a984fd9d37ceddbeaffefb96cf2"
SECOND_MEDIA_ID = "22fbf421bb5540190572a9439a138fc77ad8c3c13b3a87be34f96a965da222ce"
MUSIC_MEDIA_ID = "5f2f5c0e5d5f4d3a8b7c6d5e4f3a2b1c0d9e8f7a6b5c4d3e2f1a0b9c8d7e6f5a"

# The checks `workers/render-video` will not render without, each of which must
# be PRESENT and PASSING (workers/render-video/src/gate.ts REQUIRED_ERROR_CHECKS).
RENDERER_REQUIRED_CHECKS = (
    "source_range_within_available",
    "media_refs_resolvable",
    "timeline_contiguous",
    "reframe_aspect_matches_target",
    "reframe_keyframes_ordered",
    "determinism_digest_present",
)


# ---------------------------------------------------------------- fixtures --


def moment_id(index: int) -> str:
    """A valid Blake3Hash whose lexical order matches its index."""
    return format(index, "064x")


def moment(
    index: int,
    *,
    source_start: float,
    source_duration: float = 400.0,
    media_id: str = MEDIA_ID,
    score: float = 0.5,
    emotional_peak: float | None = None,
    motion_energy: float | None = None,
    speech_ratio: float | None = None,
    noise_ratio: float | None = None,
    snap_points: tuple[SnapPoint, ...] = (),
    words: tuple[Word, ...] = (),
    safe_trim: SafeTrim | None = None,
    subject_track: tuple[SubjectSample, ...] = (),
    label: str = "",
) -> SelectedMoment:
    return SelectedMoment(
        moment_id=moment_id(index),
        media_id=media_id,
        source_start=source_start,
        source_duration=source_duration,
        score=score,
        snap_points=snap_points,
        safe_trim=safe_trim,
        words=words,
        emotional_peak=emotional_peak,
        motion_energy=motion_energy,
        speech_ratio=speech_ratio,
        noise_ratio=noise_ratio,
        subject_track=subject_track,
        label=label,
    )


def source_media(**kwargs) -> SourceMedia:
    defaults = dict(
        media_ref_id="src-000",
        media_id=MEDIA_ID,
        available_start=0.0,
        available_duration=100000.0,
        aspect_ratio=(16, 9),
        expected_frame_rate=RATE,
        label="clip.mp4",
    )
    defaults.update(kwargs)
    return SourceMedia(**defaults)


def render_target(**kwargs) -> RenderTarget:
    defaults = dict(
        destination="master",
        resolution=(1920, 1080),
        aspect_ratio=(16, 9),
        loudness_target_lufs=-14.0,
    )
    defaults.update(kwargs)
    return RenderTarget(**defaults)


def spaced_moments(count: int, **kwargs) -> tuple[SelectedMoment, ...]:
    """`count` moments, 1000 frames apart, each with a 400-frame window."""
    return tuple(
        moment(index, source_start=1000.0 * index, **kwargs) for index in range(count)
    )


def request(
    moments: tuple[SelectedMoment, ...] | None = None,
    media: tuple[SourceMedia, ...] | None = None,
    **kwargs,
) -> FilmRequest:
    defaults: dict = dict(
        rate=RATE,
        target=render_target(),
        media=media if media is not None else (source_media(),),
        moments=moments if moments is not None else spaced_moments(6),
        name="a film",
        reframe=ReframeSettings(enabled=False),
        generated_at="2026-08-18T00:00:00+00:00",
        validated_at="2026-08-18T00:00:00+00:00",
    )
    defaults.update(kwargs)
    return FilmRequest(**defaults)


def _edl_validator():
    documents = {
        path.name: json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(SCHEMA_DIR.glob("*.schema.json"))
    }
    registry = Registry().with_resources(
        [(name, Resource.from_contents(doc)) for name, doc in documents.items()]
    )
    return Draft202012Validator(documents["edl.schema.json"], registry=registry)


def clips(edl: dict) -> list[dict]:
    return [
        item
        for track in edl["tracks"]
        if track["kind"] == "video"
        for item in track["items"]
        if item["item_type"] == "clip"
    ]


def check(edl: dict, check_id: str) -> dict | None:
    for entry in edl["validation"]["checks"]:
        if entry["check_id"] == check_id:
            return entry
    return None


class FilmTestCase(unittest.TestCase):
    def assert_schema_valid(self, edl: dict) -> None:
        if not _HAVE_JSONSCHEMA:  # pragma: no cover
            self.skipTest("jsonschema/referencing not installed")
        errors = sorted(_edl_validator().iter_errors(edl), key=lambda e: list(e.path))
        self.assertEqual(
            [],
            [f"{list(e.path)}: {e.message}" for e in errors],
            "the film EDL failed its own schema",
        )


# ------------------------------------------------------------- the contract --


class ContractShape(FilmTestCase):
    def test_the_plan_is_a_film_and_validates_against_the_edl_schema(self):
        plan = plan_film(request())
        self.assert_schema_valid(plan.edl)
        self.assertEqual("film", plan.edl["kind"])
        self.assertEqual("v0", plan.edl["schema_version"])
        self.assertEqual(64, len(plan.edl_id))

    def test_every_emitted_time_is_a_whole_frame(self):
        """`workers/render-video` refuses a fractional source time rather than
        seeking between frames, and it is right to: which frame is a decision.
        A snap point at 1000.4 must therefore not reach the plan as 1000.4."""
        moments = tuple(
            moment(
                index,
                source_start=1000.0 * index,
                snap_points=(
                    SnapPoint(time=1000.0 * index + 0.4, kind="shot_boundary", strength=0.9),
                ),
            )
            for index in range(6)
        )
        plan = plan_film(request(moments))
        for clip in clips(plan.edl):
            for field in ("source_range", "timeline_range"):
                for part in ("start_time", "duration"):
                    value = clip[field][part]["value"]
                    self.assertIsInstance(
                        value, int, f"{clip['clip_id']}.{field}.{part} is {value!r}"
                    )
                    self.assertEqual(RATE, clip[field][part]["rate"])

    def test_a_fractional_snap_point_is_rounded_towards_the_inside_of_the_shot(self):
        """ceil, never floor: rounding an in-point down could move it back into
        the word a speech gap was chosen to clear."""
        moments = (
            moment(
                0,
                source_start=0.0,
                snap_points=(SnapPoint(time=10.2, kind="speech_gap", strength=0.9),),
            ),
        ) + spaced_moments(5)[1:]
        plan = plan_film(request(moments))
        first = clips(plan.edl)[0]
        self.assertEqual(11, first["source_range"]["start_time"]["value"])

    def test_the_timeline_tiles_from_zero_with_no_hole(self):
        plan = plan_film(request(spaced_moments(7)))
        cursor = 0
        for item in plan.edl["tracks"][0]["items"]:
            if item["item_type"] == "gap":
                cursor += item["duration"]["value"]
                continue
            if item["item_type"] == "transition":
                continue
            self.assertEqual(cursor, item["timeline_range"]["start_time"]["value"])
            cursor += item["timeline_range"]["duration"]["value"]
        self.assertEqual(plan.duration_frames, cursor)
        self.assertEqual(0, plan.edl["global_start_time"]["value"])

    def test_the_renderer_s_required_checks_are_all_present_and_passing(self):
        plan = plan_film(request())
        self.assertEqual("pass", plan.status)
        for check_id in RENDERER_REQUIRED_CHECKS:
            entry = check(plan.edl, check_id)
            self.assertIsNotNone(entry, f"{check_id} is missing")
            self.assertTrue(entry["passed"], f"{check_id} did not pass")
            self.assertEqual("error", entry["severity"])

    def test_the_reframe_checks_are_emitted_even_with_no_reframe_track(self):
        """Vacuously true is still true; not stated is not. The renderer
        requires a passing finding for both before it renders anything."""
        plan = plan_film(request())
        self.assertEqual([], plan.edl["reframe_tracks"])
        for check_id in ("reframe_aspect_matches_target", "reframe_keyframes_ordered"):
            entry = check(plan.edl, check_id)
            self.assertTrue(entry["passed"])
            self.assertIn("no reframe track is planned", entry["detail"])

    def test_no_clip_claims_a_beat_lock_and_no_beat_grid_is_emitted(self):
        """A film's cuts answer to the sentence, not the bar. A beat_lock with
        no grid behind it is a claim the renderer would have to refuse."""
        plan = plan_film(request(music=None))
        self.assertIsNone(plan.edl["beat_grid"])
        self.assertTrue(all(clip["beat_lock"] is None for clip in clips(plan.edl)))


# ------------------------------------------------------------- determinism --


class Determinism(FilmTestCase):
    def test_the_same_request_produces_a_byte_identical_edl(self):
        first = plan_film(request())
        second = plan_film(request())
        self.assertEqual(
            reel_module.canonical_json(first.edl),
            reel_module.canonical_json(second.edl),
        )

    def test_the_id_survives_the_clock_and_moves_with_the_plan(self):
        base = plan_film(request())
        later = plan_film(
            request(
                generated_at="2027-01-01T00:00:00+00:00",
                validated_at="2027-01-01T00:00:00+00:00",
            )
        )
        self.assertEqual(base.edl_id, later.edl_id)

        slower = plan_film(request(pacing=Pacing(development_hold_seconds=3.0)))
        self.assertNotEqual(base.edl_id, slower.edl_id)
        self.assertNotEqual(
            base.edl["determinism"]["inputs_digest"],
            slower.edl["determinism"]["inputs_digest"],
        )

    def test_a_film_and_a_reel_over_the_same_moments_are_different_plans(self):
        moments = spaced_moments(6)
        film = plan_film(request(moments))
        reel = reel_module.plan_reel(
            reel_module.ReelRequest(
                rate=RATE,
                target=reel_module.RenderTarget(
                    destination="master",
                    resolution=(1920, 1080),
                    aspect_ratio=(16, 9),
                    target_duration=15.0 * RATE,
                ),
                media=(source_media(),),
                moments=moments,
                reframe=reel_module.ReframeSettings(enabled=False),
            )
        )
        self.assertNotEqual(film.edl_id, reel.edl_id)
        self.assertEqual("reel", reel.edl["kind"])
        self.assertEqual("film-planner", film.edl["determinism"]["planner"])
        self.assertEqual("reel-planner", reel.edl["determinism"]["planner"])

    def test_the_inputs_digest_moves_when_a_moment_changes(self):
        base = plan_film(request())
        changed = list(spaced_moments(6))
        changed[3] = moment(3, source_start=3000.0, motion_energy=0.9)
        other = plan_film(request(tuple(changed)))
        self.assertNotEqual(
            base.edl["determinism"]["inputs_digest"],
            other.edl["determinism"]["inputs_digest"],
        )


# ------------------------------------------------------------------ pacing --


class PacingVaries(FilmTestCase):
    def test_the_act_changes_the_hold(self):
        """Setup and resolution breathe; development moves. With the content
        signal unmeasured this is the ONLY thing moving the holds, so the three
        act bases have to land on the frame."""
        plan = plan_film(request(spaced_moments(8)))
        holds = {}
        for clip, placement_act in zip(clips(plan.edl), _acts_of(plan.edl)):
            holds.setdefault(placement_act, set()).add(
                clip["source_range"]["duration"]["value"]
            )
        self.assertEqual({150}, holds["act-setup"])  # 5.0s at 30000/1001
        self.assertEqual({120}, holds["act-development"])  # 4.0s
        self.assertEqual({165}, holds["act-resolution"])  # 5.5s
        self.assertGreater(plan.pacing_spread, 0.0)

    def test_a_busy_shot_is_held_shorter_than_a_quiet_one_in_the_same_act(self):
        moments = (
            moment(0, source_start=0.0),
            moment(1, source_start=1000.0, motion_energy=0.0, emotional_peak=0.0),
            moment(2, source_start=2000.0, motion_energy=1.0, emotional_peak=1.0),
            moment(3, source_start=3000.0),
            moment(4, source_start=4000.0),
        )
        plan = plan_film(request(moments, act_shape=ActShape(0.2, 0.2)))
        by_clip = {c["clip_id"]: c for c in clips(plan.edl)}
        quiet = by_clip["clip-02"]["source_range"]["duration"]["value"]
        busy = by_clip["clip-03"]["source_range"]["duration"]["value"]
        self.assertLess(busy, quiet)
        # 4.0s base, quiet_scale 1.3 and busy_scale 0.7 at 30000/1001.
        self.assertEqual(156, quiet)
        self.assertEqual(84, busy)

    def test_an_unmeasured_energy_is_neutral_rather_than_quiet(self):
        """The single most plausible wrong answer in this module. Reading an
        unmeasured motion signal as "still" would hand the longest holds in the
        film to whichever moments the motion pass had not reached."""
        self.assertEqual(1.0, Pacing().scale(None))
        self.assertEqual(1.3, Pacing().scale(0.0))
        self.assertEqual(0.7, Pacing().scale(1.0))
        self.assertEqual(1.0, Pacing().scale(0.5))

        measured = plan_film(
            request(
                (
                    moment(0, source_start=0.0),
                    moment(1, source_start=1000.0, motion_energy=0.0),
                    moment(2, source_start=2000.0),
                    moment(3, source_start=3000.0),
                    moment(4, source_start=4000.0),
                ),
                act_shape=ActShape(0.2, 0.2),
            )
        )
        by_clip = {c["clip_id"]: c for c in clips(measured.edl)}
        self.assertEqual(156, by_clip["clip-02"]["source_range"]["duration"]["value"])
        self.assertEqual(120, by_clip["clip-03"]["source_range"]["duration"]["value"])

    def test_a_half_measured_energy_renormalises_rather_than_defaulting(self):
        """motion 1.0 with the peak unmeasured must read as energy 1.0, not as
        0.6 -- a missing signal leaves the denominator, it does not contribute
        a zero."""
        both = plan_film(
            request(
                (
                    moment(0, source_start=0.0),
                    moment(1, source_start=1000.0, motion_energy=1.0, emotional_peak=1.0),
                    moment(2, source_start=2000.0, motion_energy=1.0),
                    moment(3, source_start=3000.0),
                    moment(4, source_start=4000.0),
                ),
                act_shape=ActShape(0.2, 0.2),
            )
        )
        by_clip = {c["clip_id"]: c for c in clips(both.edl)}
        self.assertEqual(
            by_clip["clip-02"]["source_range"]["duration"]["value"],
            by_clip["clip-03"]["source_range"]["duration"]["value"],
        )

    def test_a_hold_is_clamped_by_the_moments_own_window_and_the_clamp_is_counted(self):
        narrow = tuple(
            moment(index, source_start=1000.0 * index, source_duration=60.0)
            for index in range(5)
        )
        plan = plan_film(request(narrow))
        self.assertEqual(5, plan.window_limited)
        self.assertEqual((60, 60, 60, 60, 60), plan.shot_frames)
        self.assertEqual(0.0, plan.pacing_spread)
        self.assertTrue(
            any("cut at constant density" in note for note in plan.notes),
            plan.notes,
        )
        self.assertTrue(
            any("window allowed rather than" in note for note in plan.notes),
            plan.notes,
        )

    def test_a_hold_the_policy_reached_is_not_counted_as_window_limited(self):
        plan = plan_film(request(spaced_moments(6)))
        self.assertEqual(0, plan.window_limited)

    def test_the_maximum_hold_caps_a_generous_window(self):
        plan = plan_film(
            request(
                spaced_moments(5),
                pacing=Pacing(
                    setup_hold_seconds=8.0,
                    development_hold_seconds=8.0,
                    resolution_hold_seconds=8.0,
                    max_hold_seconds=8.0,
                ),
            )
        )
        self.assertTrue(all(frames <= 240 for frames in plan.shot_frames))

    def test_pacing_spread_is_the_stated_formula(self):
        self.assertEqual(0.0, pacing_spread(()))
        self.assertEqual(0.0, pacing_spread((90, 90, 90)))
        # (120 - 60) / 90
        self.assertEqual(0.666667, pacing_spread((60, 90, 120)))
        self.assertEqual(1.0, pacing_spread((45, 90, 135)))

    def test_a_pacing_policy_that_cannot_produce_a_shot_is_refused(self):
        with self.assertRaises(ValueError):
            Pacing(min_hold_seconds=9.0, max_hold_seconds=8.0).validate()
        with self.assertRaises(ValueError):
            Pacing(development_hold_seconds=20.0).validate()
        with self.assertRaises(ValueError):
            Pacing(quiet_scale=0.0).validate()


def _acts_of(edl: dict) -> list[str]:
    """The act each video clip belongs to, in timeline order."""
    by_clip: dict[str, str] = {}
    for act in edl["story_arc"]["acts"]:
        for beat in act["beats"]:
            for clip_id in beat["satisfied_by_clip_ids"]:
                by_clip.setdefault(clip_id, act["act_id"])
    return [by_clip[clip["clip_id"]] for clip in clips(edl)]


# ------------------------------------------------------------------- story --


class TheArc(FilmTestCase):
    def test_the_arc_is_three_acts_with_every_required_beat_satisfied(self):
        plan = plan_film(request(spaced_moments(9)))
        arc = plan.edl["story_arc"]
        self.assertEqual("three_act", arc["template"])
        self.assertEqual("template", arc["source"])
        self.assertIsNone(arc["model"])
        self.assertIsNone(arc["consent"])
        self.assertEqual(
            ["act-setup", "act-development", "act-resolution"],
            [act["act_id"] for act in arc["acts"]],
        )
        for act in arc["acts"]:
            for beat in act["beats"]:
                if beat["required"]:
                    self.assertTrue(
                        beat["satisfied_by_clip_ids"],
                        f"{beat['beat_id']} is required and unsatisfied",
                    )
        entry = check(plan.edl, "required_story_beats_satisfied")
        self.assertTrue(entry["passed"])
        self.assertEqual("error", entry["severity"])

    def test_every_act_holds_at_least_one_shot_at_every_size(self):
        for count in range(3, 16):
            plan = plan_film(request(spaced_moments(count)))
            populated = [
                act["act_id"]
                for act in plan.edl["story_arc"]["acts"]
                if act["beats"][0]["satisfied_by_clip_ids"]
            ]
            self.assertEqual(
                ["act-setup", "act-development", "act-resolution"],
                populated,
                f"{count} moments left an act empty",
            )

    def test_the_film_runs_in_chronological_order_and_moves_nothing(self):
        """The difference from a reel, in one assertion: the reel puts its
        strongest moment first, the film leaves it where it happened."""
        moments = (
            moment(0, source_start=0.0),
            moment(1, source_start=1000.0),
            moment(2, source_start=2000.0),
            moment(3, source_start=3000.0, emotional_peak=1.0, score=0.99),
            moment(4, source_start=4000.0),
            moment(5, source_start=5000.0),
        )
        plan = plan_film(request(moments))
        starts = [c["source_range"]["start_time"]["value"] for c in clips(plan.edl)]
        self.assertEqual(sorted(starts), starts)
        self.assertEqual([0, 1000, 2000, 3000, 4000, 5000], starts)

    def test_the_turn_is_the_strongest_moment_and_is_marked(self):
        # EVERY moment carries a measured peak, which is what makes the peak a
        # usable axis at all. A pool where only the intended winner was measured
        # would pass this assertion even if the planner were reading "nobody
        # measured this" as "this scored zero" -- see
        # `test_an_unmeasured_peak_does_not_lose_to_a_measured_zero`.
        moments = list(spaced_moments(7, emotional_peak=0.2))
        moments[3] = moment(3, source_start=3000.0, emotional_peak=0.95)
        plan = plan_film(request(tuple(moments)))
        marked = [
            clip["clip_id"]
            for clip in clips(plan.edl)
            if any(m["kind"] == "emotional_peak" for m in clip["markers"])
        ]
        self.assertEqual(["clip-04"], marked)
        turn_beats = [
            beat
            for act in plan.edl["story_arc"]["acts"]
            for beat in act["beats"]
            if beat["beat_id"] == "beat-turn"
        ]
        self.assertEqual(1, len(turn_beats))
        self.assertEqual(["clip-04"], turn_beats[0]["satisfied_by_clip_ids"])

    def test_a_turn_that_would_land_in_the_setup_act_moves_the_boundary(self):
        moments = list(spaced_moments(10, emotional_peak=0.2))
        moments[1] = moment(1, source_start=1000.0, emotional_peak=0.99)
        plan = plan_film(request(tuple(moments)))
        acts = _acts_of(plan.edl)
        self.assertEqual("act-development", acts[1])
        self.assertEqual(1, acts.count("act-setup"))

    def test_a_turn_that_happened_first_stays_in_the_setup_act_and_says_so(self):
        """Act I is a prefix of the chronology. Moving the turn out of it would
        mean reordering the film, which is the one thing a film does not do."""
        moments = list(spaced_moments(8, emotional_peak=0.2))
        moments[0] = moment(0, source_start=0.0, emotional_peak=0.99)
        plan = plan_film(request(tuple(moments)))
        self.assertEqual("act-setup", _acts_of(plan.edl)[0])
        self.assertTrue(
            any("turn falls in the setup act" in note for note in plan.notes),
            plan.notes,
        )
        self.assertEqual("pass", plan.status)

    def test_a_turn_that_happened_last_stays_in_the_resolution_act(self):
        moments = list(spaced_moments(8, emotional_peak=0.2))
        moments[-1] = moment(7, source_start=7000.0, emotional_peak=0.99)
        plan = plan_film(request(tuple(moments)))
        self.assertEqual("act-resolution", _acts_of(plan.edl)[-1])
        self.assertTrue(
            any("turn falls in the resolution act" in note for note in plan.notes),
            plan.notes,
        )

    def test_the_energy_curve_is_ordered_and_carries_one_energy_per_frame(self):
        plan = plan_film(request(spaced_moments(9)))
        curve = plan.edl["story_arc"]["energy_curve"]
        times = [point["time"]["value"] for point in curve]
        self.assertEqual(sorted(set(times)), times)
        self.assertTrue(all(0.0 <= point["energy"] <= 1.0 for point in curve))
        self.assertIn(1.0, [point["energy"] for point in curve])

    def test_a_story_beat_id_on_every_clip_names_a_beat_in_the_arc(self):
        plan = plan_film(request(spaced_moments(7)))
        beat_ids = {
            beat["beat_id"]
            for act in plan.edl["story_arc"]["acts"]
            for beat in act["beats"]
        }
        for clip in clips(plan.edl):
            self.assertIn(clip["story_beat_id"], beat_ids)

    def test_the_candidate_pool_is_retained_for_a_revision(self):
        plan = plan_film(request(spaced_moments(9)))
        candidates = {
            candidate
            for act in plan.edl["story_arc"]["acts"]
            for beat in act["beats"]
            for candidate in beat["candidate_moment_ids"]
        }
        self.assertEqual({moment_id(i) for i in range(9)}, candidates)


# -------------------------------------------------------------- chronology --


class Chronology(FilmTestCase):
    def _two_sources(self):
        return (
            source_media(media_ref_id="src-000", media_id=MEDIA_ID),
            source_media(media_ref_id="src-001", media_id=SECOND_MEDIA_ID),
        )

    def _interleaved(self):
        return (
            moment(0, source_start=0.0, media_id=MEDIA_ID),
            moment(1, source_start=1000.0, media_id=MEDIA_ID),
            moment(2, source_start=0.0, media_id=SECOND_MEDIA_ID),
            moment(3, source_start=1000.0, media_id=SECOND_MEDIA_ID),
        )

    def test_without_a_sequence_the_inter_file_order_is_declared_as_unknown(self):
        plan = plan_film(request(self._interleaved(), media=self._two_sources()))
        self.assertTrue(
            any("is not known to be capture order" in note for note in plan.notes),
            plan.notes,
        )

    def test_a_supplied_sequence_decides_which_file_comes_first(self):
        media = self._two_sources()
        moments = self._interleaved()
        forward = plan_film(
            request(moments, media=media, media_sequence=(MEDIA_ID, SECOND_MEDIA_ID))
        )
        backward = plan_film(
            request(moments, media=media, media_sequence=(SECOND_MEDIA_ID, MEDIA_ID))
        )
        self.assertEqual(
            [MEDIA_ID, MEDIA_ID, SECOND_MEDIA_ID, SECOND_MEDIA_ID],
            _media_order(forward.edl, media),
        )
        self.assertEqual(
            [SECOND_MEDIA_ID, SECOND_MEDIA_ID, MEDIA_ID, MEDIA_ID],
            _media_order(backward.edl, media),
        )
        self.assertNotEqual(forward.edl_id, backward.edl_id)
        for plan in (forward, backward):
            self.assertFalse(
                any("is not known to be capture order" in n for n in plan.notes)
            )

    def test_a_partial_sequence_is_refused_rather_than_half_applied(self):
        with self.assertRaises(ValueError) as caught:
            request(
                self._interleaved(),
                media=self._two_sources(),
                media_sequence=(MEDIA_ID,),
            )
        self.assertIn("EVERY declared source", str(caught.exception))

    def test_a_sequence_naming_undeclared_media_is_refused(self):
        with self.assertRaises(ValueError):
            request(media_sequence=(MEDIA_ID, MUSIC_MEDIA_ID))

    def test_a_sequence_that_repeats_a_source_is_refused(self):
        with self.assertRaises(ValueError):
            request(media_sequence=(MEDIA_ID, MEDIA_ID))


def _media_order(edl: dict, media: tuple[SourceMedia, ...]) -> list[str]:
    by_ref = {m.media_ref_id: m.media_id for m in media}
    return [by_ref[clip["media_ref_id"]] for clip in clips(edl)]


# ------------------------------------------------------------------ speech --


class SpeechAwareTrimming(FilmTestCase):
    def test_the_in_point_prefers_a_speech_gap_over_a_comparable_motion_onset(self):
        moments = list(spaced_moments(5))
        moments[2] = moment(
            2,
            source_start=2000.0,
            snap_points=(
                SnapPoint(time=2010.0, kind="motion_onset", strength=0.80),
                SnapPoint(time=2040.0, kind="speech_gap", strength=0.72),
            ),
        )
        plan = plan_film(request(tuple(moments)))
        by_clip = {c["clip_id"]: c for c in clips(plan.edl)}
        self.assertEqual(2040, by_clip["clip-03"]["source_range"]["start_time"]["value"])

    def test_the_preference_is_bounded_and_a_decisive_boundary_still_wins(self):
        """0.15 of strength, not a veto. `moment-record.schema.json#SnapPoint`
        says cutting on a weak onset is worse than cutting later on a strong
        one, and that rule survives inside the film's preference."""
        moments = list(spaced_moments(5))
        moments[2] = moment(
            2,
            source_start=2000.0,
            snap_points=(
                SnapPoint(time=2010.0, kind="motion_onset", strength=0.99),
                SnapPoint(time=2040.0, kind="speech_gap", strength=0.50),
            ),
        )
        plan = plan_film(request(tuple(moments)))
        by_clip = {c["clip_id"]: c for c in clips(plan.edl)}
        self.assertEqual(2010, by_clip["clip-03"]["source_range"]["start_time"]["value"])

    def test_a_snap_point_that_cannot_be_an_in_point_is_never_used_as_one(self):
        moments = list(spaced_moments(5))
        moments[2] = moment(
            2,
            source_start=2000.0,
            snap_points=(
                SnapPoint(
                    time=2040.0, kind="speech_gap", strength=0.99, cut_direction="out"
                ),
            ),
        )
        plan = plan_film(request(tuple(moments)))
        by_clip = {c["clip_id"]: c for c in clips(plan.edl)}
        self.assertEqual(2000, by_clip["clip-03"]["source_range"]["start_time"]["value"])

    def test_the_out_point_is_extended_to_finish_the_word_it_would_have_cut(self):
        """4.0s of development at 30000/1001 is 120 frames, so the natural
        out-point is 2120 -- inside the word spanning [2100, 2160.4). The film
        finishes the sentence: ceil(2160.4) == 2161."""
        moments = list(spaced_moments(5))
        moments[2] = moment(
            2,
            source_start=2000.0,
            words=(Word(start=2100.0, end=2160.4, text="incredible"),),
        )
        plan = plan_film(request(tuple(moments), act_shape=ActShape(0.2, 0.2)))
        by_clip = {c["clip_id"]: c for c in clips(plan.edl)}
        clip = by_clip["clip-03"]
        self.assertEqual(2000, clip["source_range"]["start_time"]["value"])
        self.assertEqual(161, clip["source_range"]["duration"]["value"])

    def test_the_out_point_is_pulled_back_when_the_window_forbids_extending(self):
        """The word runs past the end of the trimmable window, so there is
        nowhere to extend to. The cut then lands before the word starts:
        floor(2100.6) == 2100, a 100-frame hold instead of the 120 asked for."""
        moments = list(spaced_moments(5))
        moments[2] = moment(
            2,
            source_start=2000.0,
            source_duration=130.0,
            words=(Word(start=2100.6, end=2140.0, text="incredible"),),
        )
        plan = plan_film(request(tuple(moments), act_shape=ActShape(0.2, 0.2)))
        by_clip = {c["clip_id"]: c for c in clips(plan.edl)}
        self.assertEqual(100, by_clip["clip-03"]["source_range"]["duration"]["value"])
        self.assertEqual(1, plan.window_limited)

    def test_no_emitted_boundary_ever_lands_strictly_inside_a_word(self):
        """The property the two tests above are instances of, asserted over a
        pool where every moment carries speech at an awkward offset."""
        moments = tuple(
            moment(
                index,
                source_start=1000.0 * index,
                words=tuple(
                    Word(
                        start=1000.0 * index + 30.0 * step + 0.3,
                        end=1000.0 * index + 30.0 * step + 24.7,
                        text=f"w{step}",
                    )
                    for step in range(12)
                ),
            )
            for index in range(6)
        )
        plan = plan_film(request(moments))
        for clip, placed in zip(clips(plan.edl), _placed_moments(plan.edl, moments)):
            start = clip["source_range"]["start_time"]["value"]
            end = start + clip["source_range"]["duration"]["value"]
            for word in placed.words:
                self.assertFalse(word.start < start < word.end, f"{clip['clip_id']} in")
                self.assertFalse(word.start < end < word.end, f"{clip['clip_id']} out")

    def test_a_moment_with_no_word_safe_hold_is_dropped_with_a_reason(self):
        """One unbroken word over every frame the hold could end on. There is
        no legal out-point at or above the 45-frame floor and none at or below
        the window's end, so the moment is not film material -- and it leaves
        with a reason rather than with a mid-word cut."""
        moments = list(spaced_moments(6))
        moments[2] = moment(
            2,
            source_start=2000.0,
            source_duration=90.0,
            words=(Word(start=2044.5, end=2090.5, text="aaaaaaaaa"),),
        )
        plan = plan_film(request(tuple(moments)))
        self.assertEqual(5, len(clips(plan.edl)))
        self.assertNotIn(moment_id(2), [c["moment_id"] for c in clips(plan.edl)])
        self.assertTrue(
            any(moment_id(2) in note and "dropped" in note for note in plan.notes),
            plan.notes,
        )

    def test_a_moment_whose_in_point_is_buried_in_speech_is_dropped(self):
        """The window's low edge is inside a word and pushing it clear leaves
        no room for the minimum hold. Refused rather than cut mid-word."""
        moments = list(spaced_moments(6))
        moments[2] = moment(
            2,
            source_start=2000.0,
            source_duration=50.0,
            words=(Word(start=1999.0, end=2049.0, text="unbroken"),),
        )
        plan = plan_film(request(tuple(moments)))
        self.assertEqual(5, len(clips(plan.edl)))

    def test_a_snap_point_that_rounds_into_a_word_is_not_used(self):
        moments = list(spaced_moments(5))
        moments[2] = moment(
            2,
            source_start=2000.0,
            snap_points=(
                SnapPoint(time=2049.5, kind="speech_gap", strength=0.99),
                SnapPoint(time=2080.0, kind="motion_onset", strength=0.40),
            ),
            words=(Word(start=2049.6, end=2070.0, text="mid"),),
        )
        plan = plan_film(request(tuple(moments), act_shape=ActShape(0.2, 0.2)))
        by_clip = {c["clip_id"]: c for c in clips(plan.edl)}
        self.assertEqual(2080, by_clip["clip-03"]["source_range"]["start_time"]["value"])

    def test_no_mid_word_cut_is_absent_when_there_is_no_word_timing(self):
        """0 certified word-safe is ABSENT, not passing. A fabricated passing
        finding here would certify a property nothing measured."""
        plan = plan_film(request())
        self.assertIsNone(check(plan.edl, "no_mid_word_cut"))
        self.assertFalse(plan.word_safe_certified)
        self.assertTrue(
            any("Absent is not passing" in note for note in plan.notes), plan.notes
        )

    def test_no_mid_word_cut_is_an_error_for_a_film_when_words_exist(self):
        """Build plan §7 makes it a quality gate for films specifically, where
        the reel planner emits the same finding as a warning."""
        moments = list(spaced_moments(5))
        moments[2] = moment(
            2, source_start=2000.0, words=(Word(start=2050.0, end=2060.0, text="hello"),)
        )
        plan = plan_film(request(tuple(moments)))
        entry = check(plan.edl, "no_mid_word_cut")
        self.assertIsNotNone(entry)
        self.assertEqual("error", entry["severity"])
        self.assertTrue(entry["passed"])
        self.assertTrue(plan.word_safe_certified)


def _placed_moments(edl: dict, moments: tuple[SelectedMoment, ...]):
    by_id = {m.moment_id: m for m in moments}
    return [by_id[clip["moment_id"]] for clip in clips(edl)]


# ------------------------------------------------------------------ l-cuts --


class AudioTails(FilmTestCase):
    def _tailed(self, **kwargs):
        moments = list(spaced_moments(5))
        moments[2] = moment(
            2,
            source_start=2000.0,
            safe_trim=SafeTrim(
                earliest_in=2000.0,
                latest_out=2400.0,
                preserve_audio_tail=True,
            ),
            **kwargs,
        )
        return tuple(moments)

    def test_an_l_cut_runs_to_the_end_of_the_words_that_start_at_the_cut(self):
        """4.0s of development is 120 frames, so the picture cuts at 2120 --
        already word-safe, because the sentence starts just after it. The audio
        then holds to the end of the last whole word within reach:
        ceil(2168.4) == 2169, so 49 frames past the picture."""
        moments = self._tailed(
            words=(
                Word(start=2125.0, end=2140.0, text="did"),
                Word(start=2141.0, end=2168.4, text="you"),
                # Starts too late for the tail's reach; never counted.
                Word(start=2380.0, end=2399.0, text="see"),
            )
        )
        plan = plan_film(request(moments, act_shape=ActShape(0.2, 0.2)))
        by_clip = {c["clip_id"]: c for c in clips(plan.edl)}
        clip = by_clip["clip-03"]
        self.assertEqual(120, clip["source_range"]["duration"]["value"])
        self.assertEqual(49, clip["audio"]["audio_extends_past_out"]["value"])
        self.assertEqual(1, plan.l_cuts)

    def test_an_l_cut_never_outlasts_the_shot_it_plays_under(self):
        """The tail plays under the NEXT shot; longer and it would sit under a
        shot the planner never decided it should cover."""
        moments = list(self._tailed(words=(Word(start=2125.0, end=2390.0, text="long"),)))
        moments[3] = moment(3, source_start=3000.0, source_duration=60.0)
        plan = plan_film(request(tuple(moments), act_shape=ActShape(0.2, 0.2)))
        by_clip = {c["clip_id"]: c for c in clips(plan.edl)}
        following = by_clip["clip-04"]["source_range"]["duration"]["value"]
        tail = by_clip["clip-03"]["audio"]["audio_extends_past_out"]
        # 2120 + 60 = 2180 is the ceiling, and ceil(2390) does not fit under it.
        self.assertIsNone(tail)
        self.assertEqual(60, following)
        self.assertTrue(
            any("finishes within reach" in note for note in plan.notes), plan.notes
        )

    def test_no_l_cut_is_planned_when_nothing_can_size_it(self):
        """`preserve_audio_tail` says the audio continues and says nothing
        about how far. Without word timings the length would be invented."""
        plan = plan_film(request(self._tailed(), act_shape=ActShape(0.2, 0.2)))
        by_clip = {c["clip_id"]: c for c in clips(plan.edl)}
        self.assertIsNone(by_clip["clip-03"]["audio"]["audio_extends_past_out"])
        self.assertEqual(0, plan.l_cuts)
        self.assertTrue(
            any("no word timings to size it" in note for note in plan.notes),
            plan.notes,
        )

    def test_the_last_shot_never_holds_audio_past_the_end_of_the_film(self):
        moments = list(spaced_moments(5))
        moments[4] = moment(
            4,
            source_start=4000.0,
            safe_trim=SafeTrim(
                earliest_in=4000.0, latest_out=4400.0, preserve_audio_tail=True
            ),
            words=(Word(start=4100.0, end=4200.0, text="bye"),),
        )
        plan = plan_film(request(tuple(moments)))
        self.assertIsNone(clips(plan.edl)[-1]["audio"]["audio_extends_past_out"])

    def test_an_l_cut_never_reads_past_the_end_of_the_file(self):
        """A tail the file does not contain is a decode past EOF at exactly the
        frame the plan says a laugh lands. `latest_out` is a claim about the
        MOMENT; whether the file holds those frames is a different question."""
        moments = list(spaced_moments(5))
        moments[2] = moment(
            2,
            source_start=2000.0,
            source_duration=200.0,
            safe_trim=SafeTrim(
                earliest_in=2000.0, latest_out=2200.0, preserve_audio_tail=True
            ),
            words=(Word(start=2125.0, end=2195.0, text="laughing"),),
        )
        media = (source_media(available_start=0.0, available_duration=2150.0),)
        plan = plan_film(
            request(tuple(moments), media=media, act_shape=ActShape(0.2, 0.2))
        )
        by_clip = {c["clip_id"]: c for c in clips(plan.edl)}
        self.assertIsNone(by_clip["clip-03"]["audio"]["audio_extends_past_out"])
        entry = check(plan.edl, "source_range_within_available")
        self.assertTrue(entry["passed"], entry["detail"])
        self.assert_schema_valid(plan.edl)


# ------------------------------------------------------------------- audio --


class AudioPlan(FilmTestCase):
    def test_a_clip_never_carries_both_its_own_gain_and_an_ambient_gain(self):
        """contracts#53: two gains on one bed and no composition rule. The
        level lives in `AmbientPlan.per_clip_gain_db`, once."""
        moments = list(spaced_moments(5))
        moments[2] = moment(2, source_start=2000.0, speech_ratio=0.8)
        moments[3] = moment(3, source_start=3000.0, noise_ratio=0.9)
        plan = plan_film(request(tuple(moments)))
        for clip in clips(plan.edl):
            self.assertEqual(0.0, clip["audio"]["gain_db"])
        per_clip = {
            entry["clip_id"]: entry["gain_db"]
            for entry in plan.edl["audio_plan"]["ambient"]["per_clip_gain_db"]
        }
        self.assertEqual(-6.0, per_clip["clip-03"])
        self.assertEqual(-60.0, per_clip["clip-04"])

    def test_a_muted_bed_carries_no_per_clip_levels(self):
        plan = plan_film(request(ambient=AmbientSettings(enabled=False)))
        self.assertEqual([], plan.edl["audio_plan"]["ambient"]["per_clip_gain_db"])
        self.assertTrue(all(clip["audio"]["muted"] for clip in clips(plan.edl)))

    def test_the_music_bed_is_placed_once_and_claimed_by_exactly_one_cue(self):
        plan = plan_film(_with_music())
        entry = check(plan.edl, "music_cues_placed_once")
        self.assertIsNotNone(entry, "the renderer refuses music without this finding")
        self.assertTrue(entry["passed"], entry["detail"])
        cue = plan.edl["audio_plan"]["music"][0]
        music_items = [
            item
            for track in plan.edl["tracks"]
            if track.get("role") == "music"
            for item in track["items"]
        ]
        self.assertEqual(
            sorted(cue["clip_ids"]), sorted(item["clip_id"] for item in music_items)
        )

    def test_a_short_track_is_laid_down_repeatedly_and_tiles_the_film(self):
        plan = plan_film(_with_music(track_frames=300.0))
        music_track = next(t for t in plan.edl["tracks"] if t.get("role") == "music")
        cursor = 0
        for item in music_track["items"]:
            self.assertEqual(cursor, item["timeline_range"]["start_time"]["value"])
            cursor += item["timeline_range"]["duration"]["value"]
        self.assertEqual(plan.duration_frames, cursor)
        self.assertGreater(len(music_track["items"]), 1)

    def test_the_music_licence_is_checked_against_the_destination(self):
        plan = plan_film(
            _with_music(
                cleared_for=("private_playback",),
                target=render_target(destination="youtube", resolution=(1920, 1080)),
            )
        )
        entry = check(plan.edl, "music_license_covers_destination")
        self.assertFalse(entry["passed"])
        self.assertEqual("fail", plan.status)

    def test_the_ducking_rule_is_a_step_because_no_envelope_is_specified(self):
        """contracts#54: the gain in the middle of a ramp is where the voice
        is, and no shape is stated. A zero-length attack and release is the one
        envelope the contract fully describes."""
        moments = list(spaced_moments(5))
        moments[2] = moment(2, source_start=2000.0, speech_ratio=0.9)
        plan = plan_film(_with_music(moments=tuple(moments)))
        rules = plan.edl["audio_plan"]["ducking"]
        self.assertEqual(1, len(rules))
        rule = rules[0]
        self.assertEqual("explicit_ranges", rule["trigger"])
        self.assertEqual(0.0, rule["attack_ms"])
        self.assertEqual(0.0, rule["release_ms"])
        self.assertEqual("music", rule["target"])
        self.assertTrue(rule["ranges"])

    def test_no_ducking_rule_is_emitted_when_nothing_speaks(self):
        plan = plan_film(_with_music())
        self.assertEqual([], plan.edl["audio_plan"]["ducking"])

    def test_no_ducking_rule_is_emitted_over_a_muted_bed(self):
        moments = list(spaced_moments(5))
        moments[2] = moment(2, source_start=2000.0, speech_ratio=0.9)
        plan = plan_film(
            _with_music(moments=tuple(moments), ambient=AmbientSettings(enabled=False))
        )
        self.assertEqual([], plan.edl["audio_plan"]["ducking"])


def _with_music(
    *,
    track_frames: float = 100000.0,
    cleared_for: tuple[str, ...] = ("private_playback", "social_share"),
    moments: tuple[SelectedMoment, ...] | None = None,
    **kwargs,
) -> FilmRequest:
    music_media = SourceMedia(
        media_ref_id="src-music",
        media_id=MUSIC_MEDIA_ID,
        available_start=0.0,
        available_duration=track_frames,
        media_kind="music",
    )
    return request(
        moments if moments is not None else spaced_moments(5),
        media=(source_media(), music_media),
        music=MusicTrack(
            media=music_media,
            license=MusicLicense(
                provider="catalog_partner",
                license_type="royalty_free",
                cleared_for=cleared_for,
                track_title="a bed",
            ),
        ),
        **kwargs,
    )


# ------------------------------------------------------------- transitions --


class Transitions(FilmTestCase):
    def test_the_default_film_is_all_hard_cuts(self):
        """A hard cut is the ABSENCE of a Transition, never a zero-length one."""
        plan = plan_film(request(spaced_moments(9)))
        self.assertEqual(
            [],
            [
                item
                for track in plan.edl["tracks"]
                for item in track["items"]
                if item["item_type"] == "transition"
            ],
        )
        self.assertIsNone(check(plan.edl, "transition_handles_available"))

    def test_a_dissolve_is_placed_only_where_one_act_becomes_the_next(self):
        plan = plan_film(request(spaced_moments(9), act_transition_frames=12))
        items = plan.edl["tracks"][0]["items"]
        transitions = [i for i in items if i["item_type"] == "transition"]
        self.assertEqual(2, len(transitions))
        acts = _acts_of(plan.edl)
        following = []
        for position, item in enumerate(items):
            if item["item_type"] == "transition":
                following.append(items[position + 1]["clip_id"])
        clip_acts = {c["clip_id"]: a for c, a in zip(clips(plan.edl), acts)}
        self.assertEqual(
            ["act-development", "act-resolution"],
            [clip_acts[clip_id] for clip_id in following],
        )
        for transition in transitions:
            self.assertEqual("dissolve", transition["transition_type"])
            self.assertEqual("linear", transition["easing"])
            self.assertEqual(12, transition["in_offset"]["value"])
            self.assertEqual(12, transition["out_offset"]["value"])
        entry = check(plan.edl, "transition_handles_available")
        self.assertTrue(entry["passed"], entry["detail"])
        self.assert_schema_valid(plan.edl)

    def test_a_dissolve_is_downgraded_when_the_sources_carry_no_handles(self):
        moments = tuple(
            moment(index, source_start=1000.0 * index, source_duration=200.0)
            for index in range(6)
        )
        media = (source_media(available_start=0.0, available_duration=5200.0),)
        plan = plan_film(
            request(
                moments,
                media=media,
                act_transition_frames=4000,
            )
        )
        self.assertEqual(
            [],
            [i for i in plan.edl["tracks"][0]["items"] if i["item_type"] == "transition"],
        )
        self.assertTrue(
            any("downgraded to a hard cut" in note for note in plan.notes), plan.notes
        )

    def test_a_dissolve_over_an_unmuted_bed_is_announced_as_unrenderable(self):
        """The plan states the decision; `render-video` refuses it on
        contracts#52 because the contract never says what the beds do across
        the blend. Nobody should discover that at the end of a render."""
        plan = plan_film(request(spaced_moments(9), act_transition_frames=12))
        self.assertTrue(
            any("contracts#52" in note for note in plan.notes), plan.notes
        )


# -------------------------------------------------------------- end matter --


class EndHold(FilmTestCase):
    def test_a_trailing_hold_of_black_is_a_gap_and_lengthens_the_film(self):
        plain = plan_film(request(spaced_moments(6)))
        held = plan_film(request(spaced_moments(6), end_hold_frames=45))
        self.assertEqual(plain.duration_frames + 45, held.duration_frames)
        gap = held.edl["tracks"][0]["items"][-1]
        self.assertEqual("gap", gap["item_type"])
        self.assertEqual("black", gap["fill"])
        self.assertEqual(45, gap["duration"]["value"])
        self.assertTrue(check(held.edl, "timeline_contiguous")["passed"])
        self.assert_schema_valid(held.edl)


# ------------------------------------------------------------- refusals --


class Refusals(FilmTestCase):
    def test_fewer_than_three_usable_moments_is_refused_not_relabelled(self):
        with self.assertRaises(FilmTooShort) as caught:
            plan_film(request(spaced_moments(2)))
        self.assertIn("three-act", str(caught.exception))

    def test_moments_that_cannot_hold_a_shot_do_not_count_towards_the_three(self):
        moments = spaced_moments(4, source_duration=10.0)
        with self.assertRaises(FilmTooShort):
            plan_film(request(moments))

    def test_an_unsupported_arc_template_is_refused_rather_than_substituted(self):
        with self.assertRaises(ValueError) as caught:
            request(arc_template="montage")
        self.assertIn("montage", str(caught.exception))

    def test_a_moment_naming_undeclared_media_is_refused(self):
        with self.assertRaises(ValueError):
            request((moment(0, source_start=0.0, media_id=SECOND_MEDIA_ID),) + spaced_moments(3)[1:])

    def test_a_duplicate_moment_is_refused(self):
        duplicate = moment(0, source_start=0.0)
        with self.assertRaises(ValueError):
            request((duplicate, duplicate) + spaced_moments(3)[1:])

    def test_music_that_is_not_also_declared_as_media_is_refused(self):
        music_media = SourceMedia(
            media_ref_id="src-music",
            media_id=MUSIC_MEDIA_ID,
            available_start=0.0,
            available_duration=1000.0,
            media_kind="music",
        )
        with self.assertRaises(ValueError):
            request(
                music=MusicTrack(
                    media=music_media,
                    license=MusicLicense(
                        provider="catalog_partner",
                        license_type="royalty_free",
                        cleared_for=("private_playback",),
                    ),
                )
            )

    def test_a_negative_transition_or_hold_is_refused(self):
        with self.assertRaises(ValueError):
            request(act_transition_frames=-1)
        with self.assertRaises(ValueError):
            request(end_hold_frames=-1)

    def test_a_duration_ceiling_is_reported_rather_than_silently_exceeded(self):
        plan = plan_film(
            request(
                spaced_moments(9),
                target=render_target(max_duration=60.0),
            )
        )
        entry = check(plan.edl, "duration_within_max")
        self.assertIsNotNone(entry)
        self.assertFalse(entry["passed"])
        self.assertEqual("fail", plan.status)


# --------------------------------------------------------------- reframing --


class Reframing(FilmTestCase):
    def _tracked(self):
        return tuple(
            moment(
                index,
                source_start=1000.0 * index,
                subject_track=tuple(
                    SubjectSample(
                        time=1000.0 * index + 20.0 * step,
                        center_x=0.3 + 0.02 * step,
                        center_y=0.5,
                        confidence=0.9,
                    )
                    for step in range(8)
                ),
            )
            for index in range(5)
        )

    def test_a_vertical_target_produces_crop_tracks_that_match_the_target(self):
        plan = plan_film(
            request(
                self._tracked(),
                target=render_target(resolution=(1080, 1920), aspect_ratio=(9, 16)),
                reframe=ReframeSettings(enabled=True),
            )
        )
        self.assertEqual(len(clips(plan.edl)), len(plan.edl["reframe_tracks"]))
        self.assertTrue(check(plan.edl, "reframe_aspect_matches_target")["passed"])
        self.assertTrue(check(plan.edl, "reframe_keyframes_ordered")["passed"])
        self.assert_schema_valid(plan.edl)

    def test_the_crop_track_is_identical_to_the_reel_planners_for_one_geometry(self):
        """The crop maths is IMPORTED from `reel.py` rather than re-written, so
        that two planners cannot come to crop differently by a pixel. This is
        the assertion that keeps the sharing honest -- and that fails loudly the
        day someone forks one of them."""
        moment_one = self._tracked()[0]
        source = source_media()
        film_request = request(
            (moment_one,) + self._tracked()[1:],
            target=render_target(resolution=(1080, 1920), aspect_ratio=(9, 16)),
            reframe=ReframeSettings(enabled=True),
        )
        width, height = reel_module._crop_size((16, 9), (9, 16))

        class _Stub:
            act = "setup"
            clip_id = "clip-01"
            source_start = 0
            duration = 150

            def __init__(self, moment):
                self.moment = moment

            @property
            def source_end(self):
                return self.source_start + self.duration

        expected = reel_module._reframe_track(
            _Stub(moment_one), film_request, width, height
        )
        plan = plan_film(film_request)
        first = plan.edl["reframe_tracks"][0]
        self.assertEqual(expected["keyframes"], first["keyframes"])
        self.assertEqual(expected["smoothing"], first["smoothing"])

    def test_a_film_pans_more_slowly_than_a_reel_by_default(self):
        self.assertLess(
            FilmRequest(
                rate=RATE,
                target=render_target(),
                media=(source_media(),),
                moments=spaced_moments(3),
            ).reframe.max_velocity_per_second,
            reel_module.ReframeSettings().max_velocity_per_second,
        )


# ------------------------------------------------------------------ length --


class Length(FilmTestCase):
    def test_a_film_shorter_than_the_form_says_how_short_it_came_in(self):
        plan = plan_film(request(spaced_moments(4)))
        self.assertLess(plan.duration_frames / RATE, 60.0)
        self.assertTrue(
            any("floor for the form" in note for note in plan.notes), plan.notes
        )

    def test_a_long_enough_film_makes_no_such_claim(self):
        plan = plan_film(request(spaced_moments(20), min_film_seconds=10.0))
        self.assertGreater(plan.duration_frames / RATE, 10.0)
        self.assertFalse(any("floor for the form" in note for note in plan.notes))

    def test_the_realised_length_is_the_sum_of_the_holds(self):
        plan = plan_film(request(spaced_moments(11)))
        self.assertEqual(sum(plan.shot_frames), plan.duration_frames)
        self.assertEqual(len(clips(plan.edl)), len(plan.shot_frames))


# ------------------------------------------------------------- boundaries --
#
# Everything below was written from a MUTATION RUN, not from the module. Each
# test names the wrong answer it exists to catch, because a test whose failure
# message does not say what broke is worth about half a test. The first pass
# over `film.py` applied 86 mutants and 34 of them survived the suite above;
# these are the survivors that were reachable.


class EdgeArithmetic(FilmTestCase):
    def test_a_snap_point_exactly_on_a_word_edge_is_usable(self):
        """MUTANT: the mid-word test made inclusive. A cut ON a word boundary
        is the good case; rejecting it throws away every clean speech-gap cut
        the analysis layer certified."""
        moments = list(spaced_moments(5))
        moments[2] = moment(
            2,
            source_start=2000.0,
            snap_points=(SnapPoint(time=2050.0, kind="speech_gap", strength=0.9),),
            words=(Word(start=2020.0, end=2050.0, text="right"),),
        )
        plan = plan_film(request(tuple(moments)))
        by_clip = {c["clip_id"]: c for c in clips(plan.edl)}
        self.assertEqual(2050, by_clip["clip-03"]["source_range"]["start_time"]["value"])

    def test_an_out_point_exactly_on_a_word_edge_is_kept(self):
        """MUTANTS: the straddle test made inclusive, in the trimmer and again
        in the validator. 4.0s of development is 120 frames, so the out-point
        lands exactly on the end of the word -- which is a clean cut, not a
        cut through it."""
        moments = list(spaced_moments(5))
        moments[2] = moment(
            2,
            source_start=2000.0,
            words=(Word(start=2060.0, end=2120.0, text="finished"),),
        )
        plan = plan_film(request(tuple(moments), act_shape=ActShape(0.2, 0.2)))
        by_clip = {c["clip_id"]: c for c in clips(plan.edl)}
        self.assertEqual(120, by_clip["clip-03"]["source_range"]["duration"]["value"])
        self.assertTrue(check(plan.edl, "no_mid_word_cut")["passed"])
        self.assertEqual("pass", plan.status)

    def test_a_snap_too_late_to_leave_room_for_a_hold_is_not_used(self):
        """MUTANT: the `latest_in` bound dropped. A strong snap 20 frames from
        the end of the window is a real snap point and a useless in-point."""
        moments = list(spaced_moments(5))
        moments[2] = moment(
            2,
            source_start=2000.0,
            source_duration=100.0,
            snap_points=(SnapPoint(time=2080.0, kind="shot_boundary", strength=0.99),),
        )
        plan = plan_film(request(tuple(moments)))
        self.assertEqual(5, len(clips(plan.edl)))
        by_clip = {c["clip_id"]: c for c in clips(plan.edl)}
        self.assertEqual(2000, by_clip["clip-03"]["source_range"]["start_time"]["value"])

    def test_the_fallback_in_point_clears_a_run_of_continuous_speech(self):
        """MUTANT: `_word_safe_frame` stops after one word. Speech is
        continuous, so escaping one word routinely lands inside the next."""
        moments = list(spaced_moments(5))
        moments[2] = moment(
            2,
            source_start=2000.0,
            words=(
                Word(start=1999.0, end=2010.2, text="and"),
                Word(start=2010.5, end=2030.4, text="then"),
            ),
        )
        plan = plan_film(request(tuple(moments)))
        by_clip = {c["clip_id"]: c for c in clips(plan.edl)}
        self.assertEqual(2031, by_clip["clip-03"]["source_range"]["start_time"]["value"])
        self.assertEqual("pass", plan.status)

    def test_extension_finishes_the_LAST_of_two_overlapping_words(self):
        """MUTANT: extend to the earliest end instead of the latest. Two
        speakers overlap constantly, and stopping at the first one's last
        syllable cuts through the second's."""
        moments = list(spaced_moments(5))
        moments[2] = moment(
            2,
            source_start=2000.0,
            words=(
                Word(start=2100.0, end=2130.0, text="yes"),
                Word(start=2110.0, end=2160.4, text="exactly"),
            ),
        )
        plan = plan_film(request(tuple(moments), act_shape=ActShape(0.2, 0.2)))
        by_clip = {c["clip_id"]: c for c in clips(plan.edl)}
        self.assertEqual(161, by_clip["clip-03"]["source_range"]["duration"]["value"])

    def test_a_pull_back_clears_the_EARLIEST_of_two_overlapping_words(self):
        """MUTANT: pull back to the latest start instead of the earliest. The
        window forbids extending here, so the cut retreats -- and it has to
        retreat far enough to clear both speakers, not just one."""
        moments = list(spaced_moments(5))
        moments[2] = moment(
            2,
            source_start=2000.0,
            source_duration=200.0,
            words=(
                Word(start=2090.6, end=2300.0, text="listen"),
                Word(start=2100.2, end=2310.0, text="wait"),
            ),
        )
        plan = plan_film(request(tuple(moments), act_shape=ActShape(0.2, 0.2)))
        by_clip = {c["clip_id"]: c for c in clips(plan.edl)}
        self.assertEqual(90, by_clip["clip-03"]["source_range"]["duration"]["value"])

    def test_a_hold_the_pacing_cannot_place_falls_back_to_the_shortest_legal_one(self):
        """MUTANT: the fallback to the minimum hold removed. The pacing target
        can be boxed in by speech on both sides while the floor is clear, and
        dropping the shot is a worse answer than holding it briefly."""
        moments = list(spaced_moments(5))
        moments[2] = moment(
            2,
            source_start=2000.0,
            source_duration=170.0,
            words=(
                Word(start=2100.5, end=2160.0, text="one"),
                Word(start=2159.5, end=2200.0, text="two"),
            ),
        )
        plan = plan_film(request(tuple(moments), act_shape=ActShape(0.2, 0.2)))
        self.assertEqual(5, len(clips(plan.edl)))
        by_clip = {c["clip_id"]: c for c in clips(plan.edl)}
        self.assertEqual(45, by_clip["clip-03"]["source_range"]["duration"]["value"])
        self.assertEqual("pass", plan.status)

    def test_the_trimmable_window_is_rounded_inwards(self):
        """MUTANT: floor the low edge and ceil the high one. Rounding outwards
        widens a window that speech analysis narrowed, which is the one
        direction that can undo the guarantee."""
        moments = list(spaced_moments(5))
        moments[2] = moment(
            2,
            source_start=2000.0,
            safe_trim=SafeTrim(
                earliest_in=2000.0,
                latest_out=2400.0,
                speech_safe_in=2000.4,
                speech_safe_out=2350.6,
            ),
        )
        plan = plan_film(request(tuple(moments)))
        by_clip = {c["clip_id"]: c for c in clips(plan.edl)}
        self.assertEqual(2001, by_clip["clip-03"]["source_range"]["start_time"]["value"])

    def test_the_window_is_clamped_to_the_frames_the_file_actually_has(self):
        """MUTANT: the media clamp dropped. `latest_out` is a claim about the
        MOMENT; whether the file holds those frames is a different question,
        and the answer is a decode past the start of the file."""
        moments = tuple(
            moment(index, source_start=2000.0 + 1000.0 * index) for index in range(5)
        )
        media = (source_media(available_start=2050.0, available_duration=6000.0),)
        plan = plan_film(request(moments, media=media))
        self.assertEqual(
            2050, clips(plan.edl)[0]["source_range"]["start_time"]["value"]
        )
        self.assertTrue(check(plan.edl, "source_range_within_available")["passed"])
        self.assertEqual("pass", plan.status)


class PacingArithmetic(FilmTestCase):
    def test_the_two_energy_signals_carry_the_weights_the_module_declares(self):
        """MUTANT: the weights swapped. Motion leads emotional peak 0.6/0.4,
        and a shot with all of one and none of the other lands on a different
        frame under each reading."""
        moments = (
            moment(0, source_start=0.0),
            moment(1, source_start=1000.0, motion_energy=1.0, emotional_peak=0.0),
            moment(2, source_start=2000.0),
            moment(3, source_start=3000.0),
            moment(4, source_start=4000.0),
        )
        plan = plan_film(request(moments, act_shape=ActShape(0.2, 0.2)))
        by_clip = {c["clip_id"]: c for c in clips(plan.edl)}
        # energy 0.6 -> scale 0.94 -> 3.76s at 30000/1001
        self.assertEqual(113, by_clip["clip-02"]["source_range"]["duration"]["value"])

    def test_the_analysis_layers_minimum_duration_outranks_the_pacing_target(self):
        """MUTANT: `SafeTrim.min_duration` ignored. It is a claim about THIS
        window -- below it the shot reads as a flash frame -- and a busy shot's
        short pacing target must not undercut it."""
        moments = list(spaced_moments(5))
        moments[2] = moment(
            2,
            source_start=2000.0,
            motion_energy=1.0,
            emotional_peak=1.0,
            safe_trim=SafeTrim(
                earliest_in=2000.0, latest_out=2400.0, min_duration=200.0
            ),
        )
        plan = plan_film(request(tuple(moments), act_shape=ActShape(0.2, 0.2)))
        by_clip = {c["clip_id"]: c for c in clips(plan.edl)}
        self.assertEqual(200, by_clip["clip-03"]["source_range"]["duration"]["value"])


class ActInvariants(FilmTestCase):
    def test_every_act_is_non_empty_for_every_size_shape_and_turn(self):
        """MUTANT: any of the boundary clamps. An empty act is an unsatisfied
        required beat, which fails validation and makes the film unrenderable
        -- so the invariant is asserted over the whole space rather than at the
        one or two sizes a hand-written case would reach."""
        shapes = (
            ActShape(0.25, 0.20),
            ActShape(0.05, 0.05),
            ActShape(0.60, 0.30),
            ActShape(0.45, 0.45),
            ActShape(0.90, 0.05),
        )
        for count in range(3, 25):
            for shape in shapes:
                for turn in range(count):
                    setup_end, resolution_start = film_module._act_boundaries(
                        count, turn, shape
                    )
                    self.assertGreaterEqual(setup_end, 1, (count, shape, turn))
                    self.assertLess(setup_end, resolution_start, (count, shape, turn))
                    self.assertLessEqual(
                        resolution_start, count - 1, (count, shape, turn)
                    )

    def test_the_turn_is_pulled_out_of_the_resolution_act_when_it_can_be(self):
        """MUTANT: the resolution-side nudge removed. A turn that lands on the
        first frame of act III belongs to the development that led to it; only
        a turn that is literally the last thing that happened does not."""
        moments = list(spaced_moments(10, emotional_peak=0.2))
        moments[8] = moment(8, source_start=8000.0, emotional_peak=0.99)
        plan = plan_film(request(tuple(moments)))
        self.assertEqual("act-development", _acts_of(plan.edl)[8])
        self.assertEqual(1, _acts_of(plan.edl).count("act-resolution"))

    def test_the_refusal_names_how_many_moments_were_actually_usable(self):
        """MUTANT: the three-moment floor lowered. `_act_boundaries` raises its
        own FilmTooShort below three, so a test that only checks the exception
        TYPE passes on a planner with no floor at all."""
        with self.assertRaises(FilmTooShort) as caught:
            plan_film(request(spaced_moments(2)))
        self.assertIn("2 moment(s) can hold a shot", str(caught.exception))

    def test_acts_are_assigned_over_the_moments_that_will_actually_be_placed(self):
        """MUTANT: the usability pre-pass skipped. Assigning acts first and
        dropping afterwards empties whichever act the dropped shots were in,
        and the film then fails validation on footage that was never the
        problem."""
        moments = list(spaced_moments(5)) + [
            moment(5, source_start=5000.0, source_duration=10.0),
            moment(6, source_start=6000.0, source_duration=10.0),
        ]
        plan = plan_film(request(tuple(moments)))
        self.assertEqual(5, len(clips(plan.edl)))
        self.assertEqual("pass", plan.status)
        self.assertTrue(check(plan.edl, "required_story_beats_satisfied")["passed"])

    def test_the_turn_beat_is_required_and_counted(self):
        """MUTANT: the turn beat made optional. `required` is the mechanism by
        which "the film has an arc" is checked; a turn beat nothing enforces is
        an annotation."""
        plan = plan_film(request(spaced_moments(8)))
        turn = [
            beat
            for act in plan.edl["story_arc"]["acts"]
            for beat in act["beats"]
            if beat["beat_id"] == "beat-turn"
        ]
        self.assertEqual(1, len(turn))
        self.assertTrue(turn[0]["required"])
        self.assertIn(
            "4 required beats", check(plan.edl, "required_story_beats_satisfied")["detail"]
        )


class TheTurnContest(FilmTestCase):
    """Which moment the film is ABOUT, and what happens when nobody measured it.

    Written after the planner shipped with the turn reading an unmeasured
    emotional peak as 0.0 -- the exact defect `_energy` is written to avoid, in
    the same module. Every pre-existing turn test gave the intended winner the
    only measured peak in the pool, so all of them passed under both readings.
    """

    def test_an_unmeasured_peak_does_not_lose_to_a_measured_zero(self):
        """THE DEFECT, AS A TEST.

        One moment is the peak of the film by every measurement anyone made
        (score 0.99); another was measured at 0.01 and is the film's dullest
        shot. Reading "unmeasured" as zero hands the turn to the dull one.
        """
        moments = list(spaced_moments(8, score=0.10))
        moments[3] = moment(3, source_start=3000.0, score=0.99)
        moments[5] = moment(5, source_start=5000.0, score=0.10, emotional_peak=0.01)
        plan = plan_film(request(tuple(moments)))
        turn = [
            clip["moment_id"]
            for clip in clips(plan.edl)
            if any(m["kind"] == "emotional_peak" for m in clip["markers"])
        ]
        self.assertEqual([moment_id(3)], turn)

    def test_a_pool_missing_any_peak_says_which_axis_decided(self):
        moments = list(spaced_moments(8, score=0.10))
        moments[3] = moment(3, source_start=3000.0, score=0.99)
        moments[5] = moment(5, source_start=5000.0, score=0.10, emotional_peak=0.01)
        plan = plan_film(request(tuple(moments)))
        self.assertTrue(
            any(
                "turn was chosen on the fused moment score" in note
                and "7 of 8 placed moments carry no emotional_peak" in note
                for note in plan.notes
            ),
            plan.notes,
        )

    def test_a_fully_measured_pool_uses_the_peak_and_says_nothing(self):
        """The axis is dropped only where it is unusable. A pool that WAS
        measured must still be ranked on the peak, or the fix has thrown the
        signal away rather than the assumption."""
        moments = list(spaced_moments(8, emotional_peak=0.2, score=0.99))
        moments[4] = moment(4, source_start=4000.0, emotional_peak=0.90, score=0.10)
        plan = plan_film(request(tuple(moments)))
        turn = [
            clip["moment_id"]
            for clip in clips(plan.edl)
            if any(m["kind"] == "emotional_peak" for m in clip["markers"])
        ]
        self.assertEqual([moment_id(4)], turn)
        self.assertFalse(
            [n for n in plan.notes if "turn was chosen on the fused" in n], plan.notes
        )

    def test_the_score_fallback_still_breaks_ties_on_the_lowest_moment_id(self):
        """Determinism outlives the axis change: an all-equal pool must pick the
        same shot every time, and say which rule did it."""
        first = plan_film(request(spaced_moments(6)))
        second = plan_film(request(spaced_moments(6)))
        self.assertEqual(first.edl_id, second.edl_id)
        turn = [
            clip["moment_id"]
            for clip in clips(first.edl)
            if any(m["kind"] == "emotional_peak" for m in clip["markers"])
        ]
        self.assertEqual(1, len(turn))
        self.assertEqual(min(m.moment_id for m in spaced_moments(6)), turn[0])


class AmbientPolicyIsCoherent(FilmTestCase):
    """A bed that says "preserve speech" must not make speech the quietest thing.

    `services/pipeline` shipped exactly this: default_gain_db 0.0 with
    speech_gain_db left at -6, so every speaking shot was planned 6 dB below
    every other one. It never fired because nothing in that pipeline measures
    speech yet -- it was waiting for the transcript backend.
    """

    def test_a_speech_level_below_the_default_is_refused(self):
        with self.assertRaises(ValueError) as caught:
            request(
                ambient=AmbientSettings(
                    default_gain_db=0.0, speech_gain_db=-6.0, preserve_speech=True
                )
            )
        self.assertIn("preserve_speech is on but speech_gain_db", str(caught.exception))

    def test_the_defaults_are_coherent_and_accepted(self):
        plan = plan_film(request(ambient=AmbientSettings()))
        self.assertEqual("pass", plan.status)

    def test_a_flat_bed_is_allowed_when_it_does_not_claim_to_preserve_speech(self):
        plan = plan_film(
            request(
                ambient=AmbientSettings(
                    default_gain_db=0.0,
                    speech_gain_db=-6.0,
                    preserve_speech=False,
                    wind_gain_db=-60.0,
                )
            )
        )
        self.assertEqual("pass", plan.status)

    def test_a_speech_level_equal_to_the_default_is_allowed(self):
        plan = plan_film(
            request(
                ambient=AmbientSettings(
                    default_gain_db=0.0, speech_gain_db=0.0, wind_gain_db=-60.0
                )
            )
        )
        self.assertEqual("pass", plan.status)

    def test_a_wind_level_above_the_default_is_refused(self):
        with self.assertRaises(ValueError) as caught:
            request(
                ambient=AmbientSettings(
                    default_gain_db=-20.0, speech_gain_db=-6.0, wind_gain_db=0.0
                )
            )
        self.assertIn("wind_gain_db", str(caught.exception))

    def test_a_muted_bed_is_not_policed_because_it_has_no_levels(self):
        plan = plan_film(
            request(
                ambient=AmbientSettings(
                    enabled=False, default_gain_db=0.0, speech_gain_db=-6.0
                )
            )
        )
        self.assertEqual("pass", plan.status)
        self.assertEqual([], plan.edl["audio_plan"]["ambient"]["per_clip_gain_db"])

    def test_the_speech_level_actually_reaches_the_plan_the_right_way_up(self):
        """The guard is worth nothing if the gain it protects is never emitted."""
        moments = list(spaced_moments(6))
        moments[2] = moment(2, source_start=2000.0, speech_ratio=0.9)
        plan = plan_film(
            request(
                tuple(moments),
                ambient=AmbientSettings(
                    default_gain_db=-14.0, speech_gain_db=-6.0, preserve_speech=True
                ),
            )
        )
        per_clip = plan.edl["audio_plan"]["ambient"]["per_clip_gain_db"]
        self.assertEqual([{"clip_id": "clip-03", "gain_db": -6.0}], per_clip)
        self.assertGreater(
            per_clip[0]["gain_db"], plan.edl["audio_plan"]["ambient"]["default_gain_db"]
        )


class ChronologyArithmetic(FilmTestCase):
    def test_source_time_orders_a_file_even_when_the_ids_disagree(self):
        """MUTANT: source time dropped from the sort key. `moment_id` is a
        content hash and has no relationship to when anything happened; a suite
        whose fixtures happen to number moments in capture order never notices
        the difference."""
        moments = tuple(
            moment(9 - index, source_start=1000.0 * index) for index in range(6)
        )
        plan = plan_film(request(moments))
        starts = [c["source_range"]["start_time"]["value"] for c in clips(plan.edl)]
        self.assertEqual([0, 1000, 2000, 3000, 4000, 5000], starts)


class AudioTailArithmetic(FilmTestCase):
    def test_an_l_cut_is_bounded_by_the_file_as_well_as_by_the_next_shot(self):
        """MUTANT: the media bound dropped from the tail's limit. The next shot
        is long here and the FILE is short, so only the media bound stops the
        tail reading past the end of the recording."""
        short = source_media(
            media_ref_id="src-000", media_id=MEDIA_ID, available_start=0.0,
            available_duration=2150.0,
        )
        rest = source_media(
            media_ref_id="src-001", media_id=SECOND_MEDIA_ID, available_start=0.0,
            available_duration=10000.0,
        )
        moments = (
            moment(
                0,
                source_start=2000.0,
                source_duration=150.0,
                media_id=MEDIA_ID,
                safe_trim=SafeTrim(
                    earliest_in=2000.0, latest_out=2150.0, preserve_audio_tail=True
                ),
                words=(Word(start=2125.0, end=2200.0, text="laughing"),),
            ),
        ) + tuple(
            moment(index, source_start=1000.0 * index, media_id=SECOND_MEDIA_ID)
            for index in range(1, 5)
        )
        plan = plan_film(
            request(
                moments,
                media=(short, rest),
                media_sequence=(MEDIA_ID, SECOND_MEDIA_ID),
            )
        )
        first = clips(plan.edl)[0]
        self.assertIsNone(first["audio"]["audio_extends_past_out"])
        self.assertTrue(check(plan.edl, "source_range_within_available")["passed"])
        self.assertEqual("pass", plan.status)

    def test_a_tail_explained_only_by_earlier_speech_is_refused_and_named(self):
        """MUTANT: words that start BEFORE the cut counted towards the tail.
        They cannot size an L-cut -- the audio that continues is what comes
        after -- and silently returning no tail loses the note that says why."""
        moments = list(spaced_moments(5))
        moments[2] = moment(
            2,
            source_start=2000.0,
            safe_trim=SafeTrim(
                earliest_in=2000.0, latest_out=2400.0, preserve_audio_tail=True
            ),
            words=(Word(start=2010.0, end=2040.0, text="before"),),
        )
        plan = plan_film(request(tuple(moments), act_shape=ActShape(0.2, 0.2)))
        by_clip = {c["clip_id"]: c for c in clips(plan.edl)}
        self.assertIsNone(by_clip["clip-03"]["audio"]["audio_extends_past_out"])
        self.assertEqual(0, plan.l_cuts)
        self.assertTrue(
            any("no whole word starts at its out-point" in n for n in plan.notes),
            plan.notes,
        )

    def test_no_tail_is_planned_where_the_analysis_layer_did_not_ask_for_one(self):
        """MUTANT: `preserve_audio_tail` ignored. Speech continuing past a cut
        is normal; holding the picture's audio over the next shot because of it
        is an editorial decision the analysis layer makes, not the planner."""
        moments = list(spaced_moments(5))
        moments[2] = moment(
            2,
            source_start=2000.0,
            safe_trim=SafeTrim(
                earliest_in=2000.0, latest_out=2400.0, preserve_audio_tail=False
            ),
            words=(Word(start=2125.0, end=2150.0, text="after"),),
        )
        plan = plan_film(request(tuple(moments), act_shape=ActShape(0.2, 0.2)))
        by_clip = {c["clip_id"]: c for c in clips(plan.edl)}
        self.assertIsNone(by_clip["clip-03"]["audio"]["audio_extends_past_out"])
        self.assertEqual(0, plan.l_cuts)


class MixArithmetic(FilmTestCase):
    def test_a_muted_bed_carries_no_per_clip_level_even_for_speech_or_wind(self):
        """MUTANT: the `ambient.enabled` guard dropped from the per-clip list.
        With the bed muted there is no signal for a level to apply to, and a
        plan that states one describes a mix that does not exist."""
        moments = list(spaced_moments(5))
        moments[2] = moment(2, source_start=2000.0, speech_ratio=0.9)
        moments[3] = moment(3, source_start=3000.0, noise_ratio=0.95)
        plan = plan_film(
            request(tuple(moments), ambient=AmbientSettings(enabled=False))
        )
        self.assertEqual([], plan.edl["audio_plan"]["ambient"]["per_clip_gain_db"])
        self.assertTrue(all(clip["audio"]["muted"] for clip in clips(plan.edl)))

    def test_a_music_pass_never_claims_a_frame_the_track_does_not_have(self):
        """MUTANT: the pass length ceiled instead of floored. A fractional last
        frame is a frame the decoder does not have, and the plan asks for it.

        The track has to be SHORTER than the film for this to bite: a bed that
        covers the whole cut in one pass never reaches the pass length, so a
        generous fixture would report a pass on a planner that reads past the
        end of every looped track it is ever given.
        """
        plan = plan_film(_with_music(track_frames=200.5))
        music = next(t for t in plan.edl["tracks"] if t.get("role") == "music")
        self.assertGreater(len(music["items"]), 1, "the bed did not have to loop")
        for item in music["items"]:
            self.assertLessEqual(item["source_range"]["duration"]["value"], 200)
        self.assertTrue(check(plan.edl, "source_range_within_available")["passed"])
        self.assertEqual("pass", plan.status)


class TimelineIntegrity(FilmTestCase):
    def test_a_track_that_stops_short_of_the_plan_is_a_contiguity_failure(self):
        """MUTANT: the trailing gap left out of the contiguity walk. Every clip
        still starts where the last one stopped, so the per-item check passes
        on a film one hold of black shorter than the plan claims."""
        plan = plan_film(request(spaced_moments(6), end_hold_frames=45))
        entry = check(plan.edl, "timeline_contiguous")
        self.assertTrue(entry["passed"], entry["detail"])
        self.assertIn(f"[0, {plan.duration_frames})", entry["detail"])
        walked = 0
        for item in plan.edl["tracks"][0]["items"]:
            if item["item_type"] == "gap":
                walked += item["duration"]["value"]
            elif item["item_type"] == "clip":
                walked += item["timeline_range"]["duration"]["value"]
        self.assertEqual(plan.duration_frames, walked)


class TheNeutralScaleStaysInsideItsBand(FilmTestCase):
    """`scale(None)` returns 1.0, and 1.0 has to mean "neither longer nor
    shorter" rather than "off the end of the range".

    The module argues at length that an unmeasured energy must NOT get the
    quiet scale, because that would hand the longest holds in the film to the
    moments the motion pass never reached. That argument silently assumed the
    band straddles 1.0. `Pacing.validate` checked every other coherence
    property of the type -- min <= max, each act base inside the bounds -- and
    not this one, so a caller could tighten or slacken the whole film and get
    exactly the failure the neutral scale exists to prevent.
    """

    def test_a_band_entirely_below_one_is_refused(self):
        """quiet 0.90 / busy 0.60: every MEASURED energy shortens the hold, so
        the unmeasured moments would be the longest shots in the film."""
        with self.assertRaises(ValueError) as caught:
            Pacing(quiet_scale=0.90, busy_scale=0.60).validate()
        self.assertIn("do not span 1.0", str(caught.exception))

    def test_a_band_entirely_above_one_is_refused(self):
        """The mirror image: unmeasured moments as the shortest shots."""
        with self.assertRaises(ValueError) as caught:
            Pacing(quiet_scale=1.80, busy_scale=1.20).validate()
        self.assertIn("do not span 1.0", str(caught.exception))

    def test_the_defaults_and_the_degenerate_band_are_accepted(self):
        Pacing().validate()
        # quiet == busy == 1.0 turns content modulation off. Legitimate, and
        # visible in `pacing_spread`; see the Pacing docstring.
        Pacing(quiet_scale=1.0, busy_scale=1.0).validate()

    def test_an_unmeasured_moment_is_never_the_extreme_of_an_accepted_band(self):
        """The property the check exists to guarantee, asserted directly rather
        than through the two examples above."""
        for quiet, busy in ((1.30, 0.70), (1.0, 1.0), (2.0, 0.5), (1.05, 0.95)):
            pacing = Pacing(quiet_scale=quiet, busy_scale=busy)
            pacing.validate()
            measured = [pacing.scale(value / 10) for value in range(11)]
            self.assertLessEqual(min(measured), pacing.scale(None))
            self.assertLessEqual(pacing.scale(None), max(measured))


class TheRunningOrderIsTheOrderTheCallerDeclared(FilmTestCase):
    """The film's note says the sources run in declaration order. They must.

    `_media_rank` sorted by `media_ref_id` while three separate strings -- the
    module header, the `_media_rank` docstring and the note the plan emits --
    called the result "declaration order". Those coincide only when the ref ids
    sort the way the tuple was built, which is true of `services/pipeline`
    (`src-000`, `src-001`, ...) and is exactly why it went unseen. For anyone
    else the film ran in alphabetical order of a slug and said otherwise, and
    reordering `FilmRequest.media` to fix a running order did nothing at all.
    """

    def _two_sources(self):
        second_media_id = SECOND_MEDIA_ID
        # Declared late-alphabet FIRST, so ref-id order and declaration order
        # disagree. Under the old rank "alpha" opened the film.
        media = (
            source_media(media_ref_id="zulu", media_id=MEDIA_ID),
            source_media(media_ref_id="alpha", media_id=second_media_id),
        )
        moments = (
            moment(0, source_start=0.0, media_id=MEDIA_ID),
            moment(1, source_start=1000.0, media_id=MEDIA_ID),
            moment(2, source_start=0.0, media_id=second_media_id),
            moment(3, source_start=1000.0, media_id=second_media_id),
            moment(4, source_start=2000.0, media_id=second_media_id),
            moment(5, source_start=3000.0, media_id=MEDIA_ID),
        )
        return media, moments

    def test_the_film_opens_on_the_source_declared_first(self):
        media, moments = self._two_sources()
        plan = plan_film(request(moments, media=media))
        clips = [
            item
            for track in plan.edl["tracks"]
            if track["kind"] == "video"
            for item in track["items"]
            if item["item_type"] == "clip"
        ]
        self.assertEqual("zulu", clips[0]["media_ref_id"])

    def test_reordering_the_declaration_reorders_the_film(self):
        """The caller-visible consequence. Under the sorted rank both requests
        produced the same running order and this assertion could not fail."""
        media, moments = self._two_sources()
        first = plan_film(request(moments, media=media))
        second = plan_film(request(moments, media=(media[1], media[0])))

        def refs(plan):
            return [
                item["media_ref_id"]
                for track in plan.edl["tracks"]
                if track["kind"] == "video"
                for item in track["items"]
                if item["item_type"] == "clip"
            ]

        self.assertEqual("zulu", refs(first)[0])
        self.assertEqual("alpha", refs(second)[0])
        self.assertNotEqual(first.edl_id, second.edl_id)

    def test_the_note_names_the_order_the_planner_actually_used(self):
        media, moments = self._two_sources()
        plan = plan_film(request(moments, media=media))
        note = next(n for n in plan.notes if "capture order" in n)
        self.assertIn("the order they were declared in", note)
        self.assertNotIn("media_ref_id order", note)

    def test_media_sequence_still_overrides_the_declaration(self):
        media, moments = self._two_sources()
        plan = plan_film(
            request(
                moments,
                media=media,
                media_sequence=(media[1].media_id, media[0].media_id),
            )
        )
        clips = [
            item
            for track in plan.edl["tracks"]
            if track["kind"] == "video"
            for item in track["items"]
            if item["item_type"] == "clip"
        ]
        self.assertEqual("alpha", clips[0]["media_ref_id"])
        self.assertFalse([n for n in plan.notes if "capture order" in n])


# --------------------------------------------------------------------------
# Mutation survivors
# --------------------------------------------------------------------------
#
# A mutation run over this module left 23 of 65 mutants alive. Each class below
# closes one group of them: the mutant is named, so that a later change which
# makes the assertion vacuous is visible as a survivor again rather than as a
# green test. The mutants left deliberately alive are argued at the bottom.


class ValidationCannotBeWeakenedSilently(FilmTestCase):
    """The six checks `workers/render-video` gates on, and the state it reads.

    Every mutant here left the planner producing an EDL the renderer accepts
    while the property the check exists to assert was gone. That is the worst
    shape a defect can have in this repository: a passing finding over an
    unchecked condition.
    """

    def _film_with_a_tail(self):
        """A film whose last-but-one shot carries an L-cut running to the very
        end of its source, so the audio tail is what makes the bounds tight."""
        words = tuple(
            Word(start=float(s), end=float(s + 8), text="w") for s in range(200, 260, 10)
        )
        moments = tuple(
            moment(
                index,
                source_start=1000.0 * index,
                source_duration=300.0,
                words=words if index == 0 else (),
                safe_trim=SafeTrim(
                    earliest_in=1000.0 * index,
                    latest_out=1000.0 * index + 300.0,
                    preserve_audio_tail=index == 0,
                )
                if index == 0
                else None,
            )
            for index in range(4)
        )
        return plan_film(request(moments))

    def test_an_audio_tail_that_runs_past_the_file_is_a_bounds_failure(self):
        """MUTANT M50: `reach > available_end` weakened to `end > available_end`,
        which drops the L-cut from the bounds check entirely. The renderer then
        decodes past EOF at exactly the frame the plan says the audio ends."""
        plan = self._film_with_a_tail()
        entry = check(plan.edl, "source_range_within_available")
        self.assertIsNotNone(entry)

        # The check must READ the tail, so hand it an EDL whose tail escapes.
        escaped = json.loads(json.dumps(plan.edl))
        target = None
        for item in clips(escaped):
            if item["audio"]["audio_extends_past_out"] is not None:
                target = item
                break
        self.assertIsNotNone(target, "the fixture produced no L-cut to escape with")
        ref = next(
            r for r in escaped["media_refs"] if r["media_ref_id"] == target["media_ref_id"]
        )
        end = (
            target["source_range"]["start_time"]["value"]
            + target["source_range"]["duration"]["value"]
        )
        # The picture stays inside the file; only the tail leaves it.
        ref["available_range"]["start_time"]["value"] = 0
        ref["available_range"]["duration"]["value"] = end
        from memory_engine_story.film import _validate  # noqa: PLC0415

        rebuilt = _validate(
            escaped,
            request(),
            (),
            escaped["tracks"][0]["items"][-1]["timeline_range"]["duration"]["value"],
            False,
        )
        entry = next(
            c for c in rebuilt["checks"] if c["check_id"] == "source_range_within_available"
        )
        self.assertFalse(entry["passed"], entry["detail"])
        self.assertIn("audio tail", entry["detail"])

    def test_a_track_ending_short_of_the_plan_fails_contiguity(self):
        """MUTANT M51: `cursor != total_frames` weakened to `cursor >`, so a
        track that stops EARLY passes. Every clip still starts where the last
        one stopped, so nothing else notices."""
        from memory_engine_story.film import _validate  # noqa: PLC0415

        plan = plan_film(request(spaced_moments(4)))
        report = _validate(plan.edl, request(), (), plan.duration_frames + 30, False)
        entry = next(
            c for c in report["checks"] if c["check_id"] == "timeline_contiguous"
        )
        self.assertFalse(entry["passed"], entry["detail"])
        self.assertIn("ends at", entry["detail"])

    def test_a_required_beat_with_an_empty_clip_list_is_unsatisfied(self):
        """MUTANT M54: `not beat[...]` weakened to `is None`, so an EMPTY list
        of satisfying clips counts as satisfied. An act with no shot in it then
        passes the one check that exists to prove the film has an arc."""
        from memory_engine_story.film import _validate  # noqa: PLC0415

        plan = plan_film(request(spaced_moments(4)))
        emptied = json.loads(json.dumps(plan.edl))
        emptied["story_arc"]["acts"][0]["beats"][0]["satisfied_by_clip_ids"] = []
        report = _validate(emptied, request(), (), plan.duration_frames, False)
        entry = next(
            c for c in report["checks"] if c["check_id"] == "required_story_beats_satisfied"
        )
        self.assertFalse(entry["passed"], entry["detail"])
        self.assertIn("unsatisfied", entry["detail"])

    def test_the_determinism_digest_must_be_64_hex_characters(self):
        """MUTANT M55: the length-and-alphabet test weakened to `len > 0`, so
        any non-empty string passes as a BLAKE3 digest."""
        from memory_engine_story.film import _validate  # noqa: PLC0415

        plan = plan_film(request(spaced_moments(4)))
        for bad in ("z" * 64, "abc", "A" * 64):
            broken = json.loads(json.dumps(plan.edl))
            broken["determinism"]["inputs_digest"] = bad
            report = _validate(broken, request(), (), plan.duration_frames, False)
            entry = next(
                c for c in report["checks"] if c["check_id"] == "determinism_digest_present"
            )
            self.assertFalse(entry["passed"], f"{bad!r} was accepted as a digest")

    def test_a_failing_warning_downgrades_the_status_to_warn(self):
        """MUTANT M56: the `warn` branch collapsed into `pass`, so a film whose
        mix misses its loudness target reports a clean bill of health."""
        from memory_engine_story.film import _validate  # noqa: PLC0415

        plan = plan_film(request(spaced_moments(4)))
        drifted = json.loads(json.dumps(plan.edl))
        drifted["audio_plan"]["mix"]["loudness_target_lufs"] = -9.0
        report = _validate(drifted, request(), (), plan.duration_frames, False)
        entry = next(
            c for c in report["checks"] if c["check_id"] == "audio_loudness_target_set"
        )
        self.assertFalse(entry["passed"])
        self.assertEqual("warning", entry["severity"])
        self.assertEqual("warn", report["status"])

    def test_the_mid_word_check_reads_both_edges_of_every_cut(self):
        """MUTANT M53: the edge loop reduced to `source_start`, so a cut whose
        OUT-point lands inside a word passes. On a film that is the build plan's
        stated quality gate checking half of what it claims to check."""
        from memory_engine_story.film import _validate  # noqa: PLC0415

        plan = plan_film(request(spaced_moments(4)))
        placed = clips(plan.edl)[0]
        start = placed["source_range"]["start_time"]["value"]
        end = start + placed["source_range"]["duration"]["value"]

        class _P:
            clip_id = placed["clip_id"]

        _P.source_start, _P.source_end = start, end
        # One word straddling the OUT-point only. The in-point is clear.
        _P.moment = moment(
            0,
            source_start=float(start),
            source_duration=float(end - start) + 50.0,
            words=(Word(start=end - 3.0, end=end + 3.0, text="cut"),),
        )
        report = _validate(plan.edl, request(), (_P(),), plan.duration_frames, True)
        entry = next(c for c in report["checks"] if c["check_id"] == "no_mid_word_cut")
        self.assertFalse(entry["passed"], entry["detail"])
        self.assertEqual("error", entry["severity"])


class TheAudioTailIsBoundedByTheFileAndTheNextShot(FilmTestCase):
    def _moment_with_tail(self, *, words, latest_out, source_duration=300.0):
        return moment(
            0,
            source_start=0.0,
            source_duration=source_duration,
            words=words,
            safe_trim=SafeTrim(
                earliest_in=0.0, latest_out=latest_out, preserve_audio_tail=True
            ),
        )

    def test_a_tail_is_clamped_to_the_frames_the_file_actually_has(self):
        """MUTANT M46: `min(trim.latest_out, medium.available_end)` reduced to
        `trim.latest_out`. `SafeTrim` is a claim about the MOMENT; whether the
        file holds those frames is a different question, and the answer is a
        decode past EOF.

        The bound only binds when the file is SHORTER than the moment's
        `latest_out`, so the short medium below is the whole fixture -- a first
        attempt gave the medium 4000 frames against a latest_out of 300, where
        `min` and no-`min` return the same number and the mutant lived.
        """
        short_media_id = SECOND_MEDIA_ID
        # The last word ends BETWEEN the file's end (260) and the moment's
        # `latest_out` (300), and inside the following shot's reach. It is the
        # only word the two versions disagree about: with the media clamp it is
        # out of reach, without it the tail runs to frame 265 of a 260-frame
        # file. A first attempt stopped the words at 285, where the
        # next-shot bound already cut the tail short and the mutant lived.
        words = tuple(
            Word(start=float(s), end=float(s + 5), text="w")
            for s in (100, 140, 180, 220, 250, 261)
        )
        first = moment(
            0,
            source_start=0.0,
            source_duration=300.0,
            media_id=short_media_id,
            words=words,
            safe_trim=SafeTrim(
                earliest_in=0.0, latest_out=300.0, preserve_audio_tail=True
            ),
        )
        rest = tuple(
            moment(index, source_start=1000.0 * index, source_duration=300.0)
            for index in range(1, 4)
        )
        short_end = 260.0
        media = (
            source_media(
                media_ref_id="src-short",
                media_id=short_media_id,
                available_start=0.0,
                available_duration=short_end,
            ),
            source_media(),
        )
        plan = plan_film(request((first,) + rest, media=media))
        tails = 0
        for item in clips(plan.edl):
            tail = item["audio"]["audio_extends_past_out"]
            if tail is None:
                continue
            tails += 1
            reach = (
                item["source_range"]["start_time"]["value"]
                + item["source_range"]["duration"]["value"]
                + tail["value"]
            )
            if item["media_ref_id"] == "src-short":
                self.assertLessEqual(reach, short_end, "the L-cut reads past EOF")
        self.assertEqual(1, tails, "the fixture planned no L-cut to bound")
        self.assertTrue(check(plan.edl, "source_range_within_available")["passed"])

    def test_a_word_ending_exactly_on_the_cut_buys_no_tail(self):
        """MUTANT M47: `out <` relaxed to `out <=`, which admits a word that
        ends exactly AT the out-point and yields a zero-length L-cut -- an
        `audio_extends_past_out` of 0 frames, which says the audio continues
        and then does not."""
        plan = plan_film(request(spaced_moments(4)))
        placed = clips(plan.edl)[0]
        out = (
            placed["source_range"]["start_time"]["value"]
            + placed["source_range"]["duration"]["value"]
        )
        first = self._moment_with_tail(
            words=(Word(start=out - 10.0, end=float(out), text="ends"),),
            latest_out=300.0,
        )
        rest = tuple(
            moment(index, source_start=1000.0 * index, source_duration=300.0)
            for index in range(1, 4)
        )
        plan = plan_film(request((first,) + rest))
        for item in clips(plan.edl):
            tail = item["audio"]["audio_extends_past_out"]
            self.assertTrue(tail is None or tail["value"] > 0, "a zero-length L-cut")
        self.assertEqual(0, plan.l_cuts)
        self.assertTrue(
            any("finishes within reach" in note for note in plan.notes), plan.notes
        )


class PolicyBoundsAreTestedAtTheirBoundary(FilmTestCase):
    def test_acts_that_leave_development_exactly_empty_are_refused(self):
        """MUTANT M07: `>= 1.0` relaxed to `> 1.0`. Setup 0.5 + resolution 0.5
        leaves development nothing at all, and the type exists to prevent it."""
        with self.assertRaises(ValueError) as caught:
            ActShape(setup_fraction=0.5, resolution_fraction=0.5).validate()
        self.assertIn("no room for development", str(caught.exception))
        ActShape(setup_fraction=0.5, resolution_fraction=0.49).validate()

    def test_a_fixed_hold_with_min_equal_to_max_is_legal(self):
        """MUTANT M20: `>` tightened to `>=`, which forbids a deliberately
        constant hold. The Pacing docstring calls that a legitimate choice."""
        Pacing(
            min_hold_seconds=4.0,
            max_hold_seconds=4.0,
            setup_hold_seconds=4.0,
            development_hold_seconds=4.0,
            resolution_hold_seconds=4.0,
        ).validate()

    def test_a_fractional_min_duration_rounds_up_not_down(self):
        """MUTANT M22: `ceil` weakened to `floor` on `SafeTrim.min_duration`.
        Rounding a floor DOWN plans a shot shorter than the analysis layer said
        was safe -- the one direction the bound exists to forbid."""
        from memory_engine_story.film import _min_frames  # noqa: PLC0415

        subject = moment(
            0,
            source_start=0.0,
            source_duration=900.0,
            safe_trim=SafeTrim(
                earliest_in=0.0, latest_out=900.0, min_duration=200.5
            ),
        )
        self.assertEqual(201, _min_frames(subject, request()))

    def test_a_window_exactly_the_minimum_hold_wide_is_usable(self):
        """MUTANT M29: `<` relaxed to `<=`, which drops a moment whose window is
        EXACTLY long enough. An exact fit is the good case."""
        from memory_engine_story.film import _cut_for, _min_frames  # noqa: PLC0415

        req = request()
        floor = _min_frames(moment(0, source_start=0.0), req)
        exact = moment(
            0,
            source_start=0.0,
            source_duration=float(floor),
            safe_trim=SafeTrim(earliest_in=0.0, latest_out=float(floor)),
        )
        cut = _cut_for(exact, source_media(), req, floor)
        self.assertIsNotNone(cut, "a window of exactly the floor was refused")
        self.assertEqual(floor, cut.duration)

    def test_no_shot_is_held_longer_than_the_maximum(self):
        """MUTANT M30: the ceiling raised by one frame.

        Without words the ceiling never binds -- `wanted` is already capped at
        `max_frames`, so `min(high, start + max)` and `min(high, start + max +
        1)` give the same answer and the mutant lives. It binds only where a
        word EXTENSION pushes the out-point at the cap, so the fixture puts a
        word straddling exactly the capped out-point and ending one frame past
        it. That single frame is the whole difference between a bounded hold
        and one the pacing policy no longer controls.
        """
        cap = int(round(3.0 * RATE))
        straddler = Word(start=float(cap - 2), end=float(cap + 1), text="over")
        req = request(
            tuple(
                moment(
                    index,
                    source_start=2000.0 * index,
                    source_duration=1800.0,
                    words=(
                        Word(
                            start=2000.0 * index + straddler.start,
                            end=2000.0 * index + straddler.end,
                            text="over",
                        ),
                    ),
                )
                for index in range(4)
            ),
            pacing=Pacing(
                max_hold_seconds=3.0,
                setup_hold_seconds=3.0,
                development_hold_seconds=3.0,
                resolution_hold_seconds=3.0,
            ),
        )
        plan = plan_film(req)
        self.assertTrue(plan.shot_frames)
        for held in plan.shot_frames:
            self.assertLessEqual(held, cap, plan.shot_frames)

    def test_an_energy_outside_zero_to_one_cannot_escape_the_scale_band(self):
        """MUTANT M17: the clamp on `energy` removed. Nothing validates
        `SelectedMoment.motion_energy` to [0,1] -- checked, it does not -- so an
        out-of-range reading from a future producer would drive the hold outside
        the band the Pacing policy declares."""
        pacing = Pacing()
        band = (min(pacing.busy_scale, pacing.quiet_scale),
                max(pacing.busy_scale, pacing.quiet_scale))
        for energy in (-5.0, -0.001, 1.001, 12.0):
            scale = pacing.scale(energy)
            self.assertGreaterEqual(scale, band[0], energy)
            self.assertLessEqual(scale, band[1], energy)


class TheEnergyCurveIsSampledWhereEachActBegins(FilmTestCase):
    def test_each_act_contributes_a_control_point_at_its_own_first_frame(self):
        """MUTANT M63: `curve.append((start, energy))` collapsed to frame 0, so
        all three acts declare their target energy at the same instant and the
        curve stops being a curve."""
        plan = plan_film(request(spaced_moments(9)))
        arc = plan.edl["story_arc"]
        times = [point["time"]["value"] for point in arc["energy_curve"]]
        self.assertEqual(sorted(set(times)), times, "control points are not distinct")
        self.assertGreater(len(times), 1)
        act_starts = {
            act["timeline_range"]["start_time"]["value"]
            for act in arc["acts"]
            if act["timeline_range"] is not None
        }
        self.assertTrue(act_starts.issubset(set(times)), (act_starts, times))
        self.assertGreater(max(times), 0, "every control point sits at frame 0")


class TheEdlIdCoversWhatTheRendererWillOpen(FilmTestCase):
    def test_two_films_differing_only_in_source_range_get_different_ids(self):
        """MUTANT M66: the digest view popped `source_range` instead of
        `timeline_range`. `timeline_range` is derivable from the durations and
        is stripped for that reason; `source_range` is WHICH FRAMES OF THE FILE
        get rendered. Two different cuts sharing one id means the render cache
        serves the wrong film."""
        base = plan_film(request(spaced_moments(4)))
        shifted = plan_film(
            request(
                tuple(
                    moment(index, source_start=1000.0 * index + 17.0)
                    for index in range(4)
                )
            )
        )
        base_ranges = [c["source_range"] for c in clips(base.edl)]
        shifted_ranges = [c["source_range"] for c in clips(shifted.edl)]
        self.assertNotEqual(base_ranges, shifted_ranges, "the fixture did not differ")
        self.assertNotEqual(base.edl_id, shifted.edl_id)


class WordBoundariesAreClearedForward(FilmTestCase):
    def test_a_frame_sitting_exactly_on_a_word_end_is_already_clear(self):
        """MUTANT M40: `w.start < frame < w.end` relaxed to `<= w.end`, which
        treats a boundary ON the end of a word as inside it and pushes the cut
        forward for no reason -- past the gap the snap point was chosen for."""
        from memory_engine_story.film import _word_safe_frame  # noqa: PLC0415

        words = (Word(start=10.0, end=20.0, text="a"), Word(start=30.0, end=40.0, text="b"))
        self.assertEqual(20, _word_safe_frame(20, words))
        self.assertEqual(10, _word_safe_frame(10, words))
        self.assertEqual(25, _word_safe_frame(25, words))
        # Strictly inside still moves, and lands clear of the NEXT word too.
        self.assertEqual(20, _word_safe_frame(15, words))
        self.assertEqual(40, _word_safe_frame(35, words))

    def test_an_out_point_outside_its_own_bounds_is_refused(self):
        """MUTANT M43: the upper half of the range guard dropped. The function
        is the only thing standing between a pacing target and a hold longer
        than the window allows."""
        from memory_engine_story.film import _word_safe_out  # noqa: PLC0415

        subject = moment(0, source_start=0.0, source_duration=900.0)
        self.assertIsNone(_word_safe_out(subject, 500, 100, 400))
        self.assertIsNone(_word_safe_out(subject, 50, 100, 400))
        self.assertEqual(400, _word_safe_out(subject, 400, 100, 400))
        self.assertEqual(100, _word_safe_out(subject, 100, 100, 400))


# --------------------------------------------------------------------------
# The mutants left alive, and why each one cannot be killed
# --------------------------------------------------------------------------
#
# 66 mutations, 59 killed, 7 alive. The seven are argued below rather than left
# as a number, because "89%" and "89% and here is the remaining 11%" are
# different claims and only the second one is checkable. Every entry is an
# EQUIVALENT mutant: the mutated program computes the same thing, so a test
# that appeared to kill it would be asserting something untrue.
#
#   M03  `if 1 <= turn < setup_end` -> `<=`.
#        The body is `setup_end = turn`. When `turn == setup_end` that is a
#        no-op, so the extra iteration of the condition changes nothing.
#
#   M09  the turn contest's `>` -> `>=`.
#        A `>=` would let a later candidate win an exact tie. There are no
#        exact ties: the sort key ends in `_negated_id(moment)`, which is
#        derived from `moment_id` and is unique across the pool by
#        construction (`FilmRequest` rejects a duplicate moment_id).
#
#   M40  `_word_safe_frame`'s `w.start < frame < w.end` -> `<= w.end`.
#        The extra match is absorbed one line later: a frame exactly on a
#        word's end gives `moved = ceil(w.end) == frame`, and the
#        `if moved <= frame: return frame` guard returns it unchanged. The two
#        programs differ only in which line does the nothing.
#
#   M41  `max(w.end ...)` -> `min(w.end ...)` over straddling words.
#        Already argued in the source: the extremum bounds how many iterations
#        the loop needs, not what it converges to. It keeps going until nothing
#        straddles, and it gets there from either end. The source comment says
#        a mutation run confirmed this; this is that run, again.
#
#   M42  dropping `and extended != out`.
#        Unreachable as written. The branch is only entered when some word
#        strictly straddles `out` (`w.start < out < w.end`), so
#        `ceil(latest_end) >= ceil(w.end) > out` for that word, and `extended`
#        is never equal to `out`.
#
#   M65  `_digest_view` not filtering out `edl_id`.
#        There is no `edl_id` to filter at the moment it runs: the key is
#        ASSIGNED from this function's own result
#        (`edl["edl_id"] = blake3_hex(canonical_json(_digest_view(edl)))`), so
#        the filter removes a key that is not there yet. It is worth keeping
#        for the day something re-digests a complete EDL, which is exactly when
#        it would stop being equivalent.
#
#   M66  popping `source_range` instead of `timeline_range` from the digest.
#        This one is equivalent for a REASON WORTH WRITING DOWN, because it
#        looks like a real defect and is not. Stripping `source_range` should
#        let two different cuts of the same footage share an `edl_id` -- and it
#        does not, because `determinism.inputs_digest` is also inside the
#        digest view and is computed from the whole `FilmRequest`, source
#        positions included. So the id still moves when the cut moves. Two
#        guards covering one property is the pattern `_act_boundaries` warns
#        about; here the redundancy is inherited from the contract's shape
#        rather than chosen, and the id is correct under either.


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
