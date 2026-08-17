"""Beat-locked cutting, tested for the ways it can be quietly wrong.

The uninteresting question is "does it find the nearest beat". The interesting
ones are the ones that produce a plausible number and a wrong cut:

  * a time base that drifts, so the answer is right at 0s and two frames out at
    three minutes;
  * a tolerance checked before quantisation, so the EDL claims a lock the
    renderer cannot honour;
  * the content budget and the alignment budget treated as one number;
  * a low-confidence section that still gets locked;
  * a shot shorter than one beat quietly becoming a one-frame flash;
  * a sign flip on alignment_error_ms, which is invisible in aggregate and
    exactly backwards in the audit trail.

The anchor test reproduces `contracts/fixtures/edl/valid/reel-beat-locked-vertical-reframe.json`
frame for frame and millisecond for millisecond from a synthesised 128 BPM grid.
That fixture is the contract's own statement of what beat-locking means here.
"""

from __future__ import annotations

import json
import sys
import unittest
from fractions import Fraction
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = PACKAGE_ROOT.parent.parent
sys.path.insert(0, str(PACKAGE_ROOT))

from memory_engine_story.beats import (  # noqa: E402
    Beat,
    BeatGrid,
    BeatGridError,
    BeatLockPolicy,
    BlockedAnalyzerError,
    CutDecision,
    DEFAULT_POLICY,
    ISSUE_ANALYZER_BLOCKED,
    ISSUE_ANALYZER_UNPINNED,
    ISSUE_BPM_DISAGREES_WITH_BEATS,
    ISSUE_LOW_BPM_CONFIDENCE,
    ISSUE_TEMPO_CHANGE,
    REASON_ALIGNMENT_OUTSIDE_TOLERANCE,
    REASON_BEAT_BEYOND_MAX_PULL,
    REASON_GRID_CONFIDENCE_BELOW_FLOOR,
    REASON_LOCKED,
    REASON_NO_LOCKABLE_BEAT,
    RationalTime,
    SnapPoint,
    alignment_gate,
    audit_grid,
    downbeat_indices,
    local_interval_seconds,
    measured_bpm,
    nearest_beat_index,
    plan_beat_locked_cuts,
    rate_fraction,
    snap_cut,
)

FIXTURE = (
    REPO_ROOT
    / "contracts"
    / "fixtures"
    / "edl"
    / "valid"
    / "reel-beat-locked-vertical-reframe.json"
)

NTSC_60 = Fraction(60000, 1001)
NTSC_24 = Fraction(24000, 1001)


# --------------------------------------------------------------------------
# builders
# --------------------------------------------------------------------------


def seconds_time(value) -> RationalTime:
    """A time expressed in whole seconds at rate 1 -- exact, and rate-agnostic."""
    return RationalTime(Fraction(value), 1)


def grid(
    times,
    *,
    bpm=128.0,
    downbeat_every=4,
    strengths=None,
    tolerance_ms=50.0,
    bpm_confidence=None,
    analyzer_model_id="librosa-beat-track",
    with_signature=True,
):
    beats = tuple(
        Beat(
            index=i,
            time=seconds_time(t),
            is_downbeat=(i % downbeat_every == 0),
            bar=(i // downbeat_every if with_signature else None),
            beat_in_bar=((i % downbeat_every) + 1 if with_signature else None),
            strength=(None if strengths is None else strengths[i]),
        )
        for i, t in enumerate(times)
    )
    return BeatGrid(
        source_cue_id="cue-01",
        bpm=bpm,
        beats=beats,
        bpm_confidence=bpm_confidence,
        beats_per_bar=(downbeat_every if with_signature else None),
        beat_unit=(4 if with_signature else None),
        analyzer_model_id=analyzer_model_id,
        tolerance_ms=tolerance_ms,
    )


def even_grid(count, interval, **kw):
    step = Fraction(interval)
    return grid([step * i for i in range(count)], **kw)


def beat_lock_validator():
    from jsonschema import Draft202012Validator
    from referencing import Registry, Resource

    schema_dir = REPO_ROOT / "contracts" / "schemas"
    documents = {
        path.name: json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(schema_dir.glob("*.schema.json"))
    }
    registry = Registry().with_resources(
        [(name, Resource.from_contents(doc)) for name, doc in documents.items()]
    )
    return Draft202012Validator(
        {"$ref": "edl.schema.json#/$defs/BeatLock"}, registry=registry
    )


# --------------------------------------------------------------------------
# exact time
# --------------------------------------------------------------------------


class TestExactTime(unittest.TestCase):
    def test_ntsc_float_rates_recover_their_exact_rational(self):
        # The contract carries rates as JSON numbers. float(60000/1001) must come
        # back as 60000/1001, or every downstream multiplication drifts.
        self.assertEqual(NTSC_60, rate_fraction(59.94005994005994))
        self.assertEqual(Fraction(30000, 1001), rate_fraction(29.97002997002997))
        self.assertEqual(NTSC_24, rate_fraction(23.976023976023978))
        self.assertEqual(Fraction(25), rate_fraction(25))
        self.assertEqual(Fraction(48000), rate_fraction(48000))

    def test_rounded_decimal_rate_is_not_silently_promoted_to_ntsc(self):
        # 29.97 is the rounded decimal the schema warns against. Snapping it to
        # 30000/1001 would be guessing what the author meant -- and guessing
        # right 99% of the time is the worst possible failure rate for a silent
        # correction. We represent exactly what we were handed.
        self.assertEqual(Fraction(2997, 100), rate_fraction(29.97))
        self.assertNotEqual(Fraction(30000, 1001), rate_fraction(29.97))

    def test_seconds_are_exact_at_a_long_offset(self):
        # One hour into a 29.97 timeline. Float seconds accumulate error here;
        # a Fraction does not, and this is the whole reason for the type.
        t = RationalTime(107892, 29.97002997002997)  # 3600s worth of frames
        self.assertEqual(Fraction(107892 * 1001, 30000), t.seconds())
        self.assertEqual(Fraction(3600), t.seconds())

    def test_equality_is_on_the_instant_not_the_field_pair(self):
        self.assertEqual(RationalTime(1, 2), RationalTime(2, 4))
        self.assertEqual(hash(RationalTime(1, 2)), hash(RationalTime(2, 4)))
        self.assertLess(RationalTime(1, 2), RationalTime(3, 4))

    def test_quantisation_rounds_halves_away_from_zero(self):
        # Banker's rounding (Python's round) would give 0, 2, 0, 2 here: two
        # cuts sitting exactly half a frame from their beats would round in
        # opposite directions depending on the parity of the neighbour.
        self.assertEqual(1, RationalTime(Fraction(1, 2), 1).quantized_to(1).frame)
        self.assertEqual(2, RationalTime(Fraction(3, 2), 1).quantized_to(1).frame)
        self.assertEqual(3, RationalTime(Fraction(5, 2), 1).quantized_to(1).frame)
        self.assertEqual(-1, RationalTime(Fraction(-1, 2), 1).quantized_to(1).frame)
        self.assertEqual(-3, RationalTime(Fraction(-5, 2), 1).quantized_to(1).frame)

    def test_frame_refuses_a_time_that_is_not_on_a_frame(self):
        with self.assertRaises(BeatGridError):
            _ = RationalTime(Fraction(1, 2), 1).frame

    def test_rescale_between_audio_and_video_rates_is_exact(self):
        # A beat grid authored at 48000 audio units, consumed on a 59.94 video
        # timeline. 1.875s = 90000 samples = exactly 112.3875 frames.
        audio = RationalTime(90000, 48000)
        self.assertEqual(Fraction(15, 8), audio.seconds())
        self.assertEqual(Fraction(112387, 1000) + Fraction(1, 2000), audio.rescaled_to(NTSC_60).value)
        self.assertEqual(112, audio.quantized_to(NTSC_60).frame)

    def test_contract_round_trip(self):
        data = {"value": 899, "rate": 59.94005994005994}
        self.assertEqual(data, RationalTime.from_contract(data).to_contract())


# --------------------------------------------------------------------------
# grid validation
# --------------------------------------------------------------------------


class TestBeatGridValidation(unittest.TestCase):
    def test_empty_grid_is_rejected(self):
        with self.assertRaises(BeatGridError):
            BeatGrid(source_cue_id="cue-01", bpm=120.0, beats=())

    def test_index_must_match_list_position(self):
        # BeatLock.beat_index indexes the list. A grid whose stored indices are
        # off by one still produces a BeatLock pointing at *a* beat, so nothing
        # downstream would ever notice.
        beats = (
            Beat(index=0, time=seconds_time(0), is_downbeat=True),
            Beat(index=2, time=seconds_time(Fraction(1, 2)), is_downbeat=False),
        )
        with self.assertRaises(BeatGridError) as caught:
            BeatGrid(source_cue_id="cue-01", bpm=120.0, beats=beats)
        self.assertIn("index", str(caught.exception))

    def test_beats_must_strictly_increase(self):
        for second in (Fraction(0), Fraction(-1, 2)):
            with self.subTest(second=second):
                beats = (
                    Beat(index=0, time=seconds_time(0), is_downbeat=True),
                    Beat(index=1, time=seconds_time(second), is_downbeat=False),
                )
                with self.assertRaises(BeatGridError):
                    BeatGrid(source_cue_id="cue-01", bpm=120.0, beats=beats)

    def test_downbeat_flag_contradicting_bar_position_is_rejected(self):
        # A bar-phase error puts every "deliberate" cut on beat 3. It is the
        # difference between reading as intentional and reading as a mistake.
        beats = (
            Beat(index=0, time=seconds_time(0), is_downbeat=True, beat_in_bar=1),
            Beat(index=1, time=seconds_time(Fraction(1, 2)), is_downbeat=True, beat_in_bar=2),
        )
        with self.assertRaises(BeatGridError) as caught:
            BeatGrid(
                source_cue_id="cue-01", bpm=120.0, beats=beats, beats_per_bar=4, beat_unit=4
            )
        self.assertIn("is_downbeat", str(caught.exception))

    def test_beat_in_bar_outside_the_signature_is_rejected(self):
        beats = (
            Beat(index=0, time=seconds_time(0), is_downbeat=False, beat_in_bar=5),
        )
        with self.assertRaises(BeatGridError):
            BeatGrid(
                source_cue_id="cue-01", bpm=120.0, beats=beats, beats_per_bar=4, beat_unit=4
            )

    def test_out_of_range_scores_are_rejected(self):
        with self.assertRaises(BeatGridError):
            Beat(index=0, time=seconds_time(0), is_downbeat=True, strength=1.4)
        with self.assertRaises(BeatGridError):
            SnapPoint(time=seconds_time(0), kind="motion_onset", strength=-0.1)
        with self.assertRaises(BeatGridError):
            even_grid(4, Fraction(1, 2), bpm_confidence=1.2)

    def test_from_contract_reads_the_schema_shape(self):
        data = {
            "source_cue_id": "cue-01",
            "bpm": 128.0,
            "bpm_confidence": 0.91,
            "time_signature": {"beats_per_bar": 4, "beat_unit": 4},
            "beats": [
                {
                    "index": i,
                    "time": {"value": i * 90000, "rate": 48000},
                    "is_downbeat": i % 4 == 0,
                    "bar": i // 4,
                    "beat_in_bar": (i % 4) + 1,
                    "strength": 0.8,
                    "section": "chorus",
                }
                for i in range(8)
            ],
            "analyzer": {
                "model_id": "librosa-beat-track",
                "version": "0.10.2",
                "weights_blake3": None,
            },
            "tolerance_ms": 40.0,
        }
        parsed = BeatGrid.from_contract(data)
        self.assertEqual(8, len(parsed.beats))
        self.assertEqual(4, parsed.beats_per_bar)
        self.assertEqual(40.0, parsed.tolerance_ms)
        self.assertEqual("librosa-beat-track", parsed.analyzer_model_id)
        self.assertEqual((0, 4), downbeat_indices(parsed))
        self.assertEqual(Fraction(15, 8), parsed.seconds(1))


# --------------------------------------------------------------------------
# the anchor: reproduce the golden fixture
# --------------------------------------------------------------------------


class TestGoldenFixture(unittest.TestCase):
    """Reproduce every beat lock in the contract's own beat-locked EDL.

    The fixture is a 15s reel at 60000/1001 cut to a 128 BPM track, one cut per
    bar. Its eight `alignment_error_ms` values are not round numbers -- they are
    what falls out of quantising exact bar times onto a 59.94 frame grid. If any
    part of the time handling here is float-based, or the sign convention is
    flipped, these numbers do not come back.
    """

    def setUp(self):
        self.expected = []
        for track in json.loads(FIXTURE.read_text(encoding="utf-8"))["tracks"]:
            if track["kind"] != "video":
                continue
            for item in track["items"]:
                if item.get("item_type") == "clip" and item.get("beat_lock"):
                    self.expected.append(
                        (
                            int(item["timeline_range"]["start_time"]["value"]),
                            item["beat_lock"],
                            item["clip_id"],
                        )
                    )

    def test_fixture_actually_contains_beat_locks(self):
        # Without this, a fixture that lost its beat_lock blocks would make the
        # test below pass by iterating over nothing.
        self.assertEqual(8, len(self.expected))

    def test_every_fixture_lock_is_reproduced_exactly(self):
        # 128 BPM: one beat every 15/32 s. Downbeats every 4 beats.
        music = even_grid(32, Fraction(15, 32), bpm=128.0)
        for frame, lock, clip_id in self.expected:
            with self.subTest(clip=clip_id):
                cut_time = RationalTime(frame, NTSC_60)
                point = SnapPoint(
                    time=cut_time, kind=lock["snap_point_kind"], strength=0.9
                )
                decision = snap_cut(music, cut_time, [point])
                self.assertTrue(decision.locked, decision.reason)
                self.assertEqual(frame, decision.frame)
                self.assertEqual(lock["beat_index"], decision.beat_index)
                self.assertEqual(lock["is_downbeat"], decision.is_downbeat)
                self.assertEqual(lock["alignment_error_ms"], decision.alignment_error_ms)
                self.assertEqual(lock["snap_point_kind"], decision.snap_point_kind)
                self.assertEqual(lock, decision.to_beat_lock())

    def test_error_sign_is_negative_when_the_cut_is_early(self):
        # Fixture clip-02 sits at frame 112 = 1.868533s against a beat at
        # 1.875s: early, and recorded as -6.4667. A flipped sign passes every
        # magnitude-based check and inverts the entire audit trail.
        music = even_grid(32, Fraction(15, 32))
        early = snap_cut(music, RationalTime(112, NTSC_60))
        self.assertEqual(-6.4667, early.alignment_error_ms)
        self.assertLess(early.alignment_error_ms, 0.0)

    def test_emitted_beat_lock_validates_against_the_contract(self):
        validator = beat_lock_validator()
        music = even_grid(32, Fraction(15, 32))
        for frame, _lock, clip_id in self.expected:
            with self.subTest(clip=clip_id):
                emitted = snap_cut(music, RationalTime(frame, NTSC_60)).to_beat_lock()
                self.assertEqual([], [e.message for e in validator.iter_errors(emitted)])


# --------------------------------------------------------------------------
# nearest beat / no extrapolation
# --------------------------------------------------------------------------


class TestNearestBeat(unittest.TestCase):
    def test_ties_go_to_the_earlier_beat(self):
        music = even_grid(4, Fraction(1, 2))
        self.assertEqual(0, nearest_beat_index(music, seconds_time(Fraction(1, 4))))

    def test_candidate_pool_is_respected(self):
        music = even_grid(4, Fraction(1, 2))
        self.assertEqual(2, nearest_beat_index(music, seconds_time(0), candidates=[2, 3]))
        self.assertIsNone(nearest_beat_index(music, seconds_time(0), candidates=[]))

    def test_the_grid_is_never_extrapolated_past_its_end(self):
        # The schema stores explicit beat times *because* extrapolating from bpm
        # is 200ms out by the end of a reel. A cut past the analysed region gets
        # the last real beat and an honest "too far", never an invented beat.
        music = even_grid(8, Fraction(1, 2))
        decision = snap_cut(music, seconds_time(10))
        self.assertFalse(decision.locked)
        self.assertEqual(REASON_BEAT_BEYOND_MAX_PULL, decision.reason)
        self.assertEqual(7, decision.beat_index)
        self.assertEqual(10, decision.time.seconds())

    def test_local_interval_uses_the_previous_gap_for_the_last_beat(self):
        music = grid([Fraction(0), Fraction(1, 2), Fraction(3, 2)])
        self.assertEqual(Fraction(1, 2), local_interval_seconds(music, 0))
        self.assertEqual(Fraction(1), local_interval_seconds(music, 1))
        self.assertEqual(Fraction(1), local_interval_seconds(music, 2))
        self.assertIsNone(local_interval_seconds(even_grid(1, Fraction(1, 2)), 0))


# --------------------------------------------------------------------------
# the two budgets
# --------------------------------------------------------------------------


class TestContentVersusMusic(unittest.TestCase):
    """Pull (content cost) and alignment (musical cost) are separate budgets."""

    def test_a_beat_inside_the_pull_budget_wins(self):
        music = even_grid(8, Fraction(15, 32))
        # ideal 100ms before beat 2, inside the 120ms default pull budget
        ideal = seconds_time(Fraction(15, 16) - Fraction(1, 10))
        decision = snap_cut(music, ideal, rate=NTSC_60)
        self.assertTrue(decision.locked)
        self.assertEqual(2, decision.beat_index)

    def test_a_beat_outside_the_pull_budget_loses_to_content(self):
        music = even_grid(8, Fraction(15, 32))
        ideal = seconds_time(Fraction(15, 16) - Fraction(2, 10))  # 200ms early
        decision = snap_cut(music, ideal, rate=NTSC_60)
        self.assertFalse(decision.locked)
        self.assertEqual(REASON_BEAT_BEYOND_MAX_PULL, decision.reason)
        self.assertEqual(2, decision.beat_index)  # reported for the audit trail
        self.assertIsNone(decision.to_beat_lock())
        # and the cut still happens, at the content time
        self.assertEqual(RationalTime(Fraction(3, 4), 1).quantized_to(NTSC_60).frame, decision.frame)

    def test_the_pull_budget_is_the_policy_and_nothing_else(self):
        music = even_grid(8, Fraction(15, 32))
        ideal = seconds_time(Fraction(15, 16) - Fraction(2, 10))
        generous = BeatLockPolicy(max_pull_ms=250.0)
        decision = snap_cut(music, ideal, rate=NTSC_60, policy=generous)
        self.assertTrue(decision.locked)
        self.assertEqual(2, decision.beat_index)

    def test_pull_budget_also_scales_with_tempo(self):
        # At 500 BPM the beats are 120ms apart, so a flat 120ms pull would let a
        # cut jump a whole beat. max_pull_beats caps it at half an interval.
        fast = even_grid(16, Fraction(12, 100), bpm=500.0)
        ideal = seconds_time(Fraction(12, 100) * 2 - Fraction(7, 100))  # 70ms early
        decision = snap_cut(fast, ideal, rate=NTSC_60)
        self.assertFalse(decision.locked)
        self.assertEqual(REASON_BEAT_BEYOND_MAX_PULL, decision.reason)

    def test_alignment_error_is_measured_from_the_cut_not_from_the_ideal(self):
        # Conflating the budgets shows up here: the ideal is 100ms from the
        # beat, but the cut lands on the beat, so the alignment error is a
        # quantisation residue, not 100ms.
        music = even_grid(8, Fraction(15, 32))
        ideal = seconds_time(Fraction(15, 16) - Fraction(1, 10))
        decision = snap_cut(music, ideal, rate=NTSC_60)
        self.assertLess(abs(decision.alignment_error_ms), 17.0)


class TestDownbeatPreference(unittest.TestCase):
    """A cut on a downbeat reads as deliberate; one beat off reads as a mistake.

    GEOMETRY WORTH KNOWING: with a quarter-note grid, adjacent beats are one
    interval apart, so a downbeat can only be inside the pull budget at the same
    time as an off-beat when the interval is under twice the budget -- i.e.
    above ~250 BPM at the default 120ms, or when a caller widens the budget.
    Both branches are pinned below so that widening the default cannot silently
    change behaviour.
    """

    def test_a_downbeat_is_preferred_when_both_are_affordable(self):
        music = even_grid(8, Fraction(1, 4))  # 240 BPM, downbeats every 4 beats
        wide = BeatLockPolicy(max_pull_ms=400.0, downbeat_preference_ms=200.0)
        ideal = seconds_time(Fraction(9, 10))  # nearest beat is index 4 (1.0s)
        near = snap_cut(music, ideal, rate=NTSC_60, policy=BeatLockPolicy(max_pull_ms=400.0, downbeat_preference_ms=0.0))
        self.assertEqual(4, near.beat_index)
        self.assertTrue(near.is_downbeat)
        # move the ideal so the nearest beat is an off-beat (0.75s) and the
        # downbeat at 1.0s is 200ms further
        ideal = seconds_time(Fraction(8, 10))
        strict = snap_cut(music, ideal, rate=NTSC_60, policy=BeatLockPolicy(max_pull_ms=400.0, downbeat_preference_ms=0.0))
        self.assertEqual(4, strict.beat_index)
        self.assertEqual(Fraction(1), music.seconds(strict.beat_index))
        loose = snap_cut(music, seconds_time(Fraction(7, 10)), rate=NTSC_60, policy=wide)
        self.assertTrue(loose.is_downbeat)
        self.assertEqual(4, loose.beat_index)

    def test_without_the_preference_the_off_beat_wins(self):
        music = even_grid(8, Fraction(1, 4))
        ideal = seconds_time(Fraction(7, 10))  # 50ms from 0.75, 300ms from 1.0
        none = BeatLockPolicy(max_pull_ms=400.0, downbeat_preference_ms=0.0)
        decision = snap_cut(music, ideal, rate=NTSC_60, policy=none)
        self.assertEqual(3, decision.beat_index)
        self.assertFalse(decision.is_downbeat)

    def test_an_unaffordable_downbeat_is_not_taken(self):
        # 30ms of extra pull is cheap; 300ms is not, even for a downbeat.
        music = even_grid(8, Fraction(1, 4))
        policy = BeatLockPolicy(max_pull_ms=120.0, downbeat_preference_ms=1000.0)
        decision = snap_cut(music, seconds_time(Fraction(7, 10)), rate=NTSC_60, policy=policy)
        self.assertEqual(3, decision.beat_index)
        self.assertFalse(decision.is_downbeat)

    def test_downbeats_get_the_50ms_ceiling_even_on_a_looser_grid(self):
        loose = even_grid(4, Fraction(1, 2), tolerance_ms=200.0)
        base, downbeat = DEFAULT_POLICY.tolerances(loose)
        self.assertEqual(Fraction(200), base)
        self.assertEqual(Fraction(50), downbeat)

    def test_a_tight_grid_tolerance_is_not_relaxed_up_to_50ms(self):
        tight = even_grid(4, Fraction(1, 2), tolerance_ms=20.0)
        base, downbeat = DEFAULT_POLICY.tolerances(tight)
        self.assertEqual(Fraction(20), base)
        self.assertEqual(Fraction(20), downbeat)


# --------------------------------------------------------------------------
# confidence
# --------------------------------------------------------------------------


class TestConfidence(unittest.TestCase):
    def test_a_low_confidence_grid_is_not_locked_to_at_all(self):
        unsure = even_grid(8, Fraction(1, 2), bpm_confidence=0.2)
        decision = snap_cut(unsure, seconds_time(Fraction(1, 2)), rate=NTSC_60)
        self.assertFalse(decision.locked)
        self.assertEqual(REASON_GRID_CONFIDENCE_BELOW_FLOOR, decision.reason)
        self.assertIsNone(decision.beat_index)
        self.assertIsNone(decision.to_beat_lock())
        self.assertEqual(seconds_time(Fraction(1, 2)).quantized_to(NTSC_60).frame, decision.frame)

    def test_the_same_grid_locks_once_confidence_clears_the_floor(self):
        sure = even_grid(8, Fraction(1, 2), bpm_confidence=0.9)
        self.assertTrue(snap_cut(sure, seconds_time(Fraction(1, 2)), rate=NTSC_60).locked)

    def test_a_weak_beat_is_skipped_but_its_neighbours_are_not(self):
        # "The tracker lost the beat during the breakdown." Per-beat strength is
        # the only local signal the contract carries, so it is what gates a
        # single beat; the rest of the grid stays usable.
        weak = even_grid(8, Fraction(1, 2), strengths=[0.9, 0.02] + [0.9] * 6)
        decision = snap_cut(weak, seconds_time(Fraction(1, 2)), rate=NTSC_60)
        self.assertFalse(decision.locked)
        self.assertEqual(REASON_BEAT_BEYOND_MAX_PULL, decision.reason)
        self.assertNotEqual(1, decision.beat_index)
        # a cut elsewhere on the same grid is unaffected
        self.assertTrue(snap_cut(weak, seconds_time(1), rate=NTSC_60).locked)

    def test_a_null_strength_is_not_measured_and_is_not_held_against_the_beat(self):
        # Treating None as 0 makes every grid from a tracker that omits the
        # field completely unlockable.
        unmeasured = even_grid(8, Fraction(1, 2), strengths=[None] * 8)
        self.assertTrue(snap_cut(unmeasured, seconds_time(Fraction(1, 2)), rate=NTSC_60).locked)

    def test_a_grid_of_entirely_weak_beats_reports_no_lockable_beat(self):
        weak = even_grid(4, Fraction(1, 2), strengths=[0.01] * 4)
        decision = snap_cut(weak, seconds_time(Fraction(1, 2)), rate=NTSC_60)
        self.assertFalse(decision.locked)
        self.assertEqual(REASON_NO_LOCKABLE_BEAT, decision.reason)


# --------------------------------------------------------------------------
# snap points
# --------------------------------------------------------------------------


class TestSnapPoints(unittest.TestCase):
    def test_a_snap_point_near_the_beat_wins_over_the_bare_beat(self):
        music = even_grid(8, Fraction(1, 2))
        onset = SnapPoint(
            time=seconds_time(Fraction(1, 2) + Fraction(6, 1000)), kind="motion_onset", strength=0.8
        )
        decision = snap_cut(music, seconds_time(Fraction(1, 2)), [onset], rate=NTSC_60)
        self.assertTrue(decision.locked)
        self.assertEqual("motion_onset", decision.snap_point_kind)
        self.assertEqual(onset.time.quantized_to(NTSC_60).frame, decision.frame)
        self.assertGreater(decision.alignment_error_ms, 0.0)

    def test_no_snap_points_means_the_cut_lands_on_the_beat_itself(self):
        music = even_grid(8, Fraction(1, 2))
        decision = snap_cut(music, seconds_time(Fraction(1, 2)), rate=NTSC_60)
        self.assertTrue(decision.locked)
        self.assertIsNone(decision.snap_point_kind)
        self.assertEqual(seconds_time(Fraction(1, 2)).quantized_to(NTSC_60).frame, decision.frame)

    def test_snap_points_far_from_the_beat_break_the_lock_and_keep_the_content(self):
        # Landing on the beat here would mean cutting mid-gesture. The content
        # point wins and we do not claim a lock we did not make.
        music = even_grid(8, Fraction(1, 2))
        late = SnapPoint(time=seconds_time(Fraction(58, 100)), kind="motion_onset", strength=0.8)
        decision = snap_cut(music, seconds_time(Fraction(1, 2)), [late], rate=NTSC_60)
        self.assertFalse(decision.locked)
        self.assertEqual(REASON_ALIGNMENT_OUTSIDE_TOLERANCE, decision.reason)
        self.assertEqual(late.time.quantized_to(NTSC_60).frame, decision.frame)
        self.assertEqual(1, decision.beat_index)

    def test_out_only_snap_points_are_not_used_for_an_in_point(self):
        # A motion onset is a great in-point and a poor out-point; the contract
        # encodes that asymmetry and a planner that ignores it makes
        # technically-legal, visually-wrong cuts.
        music = even_grid(8, Fraction(1, 2))
        wrong_way = SnapPoint(
            time=seconds_time(Fraction(1, 2) + Fraction(6, 1000)),
            kind="motion_offset",
            strength=0.8,
            cut_direction="out",
        )
        decision = snap_cut(music, seconds_time(Fraction(1, 2)), [wrong_way], rate=NTSC_60)
        self.assertIsNone(decision.snap_point_kind)
        self.assertTrue(decision.locked)
        as_out = snap_cut(music, seconds_time(Fraction(1, 2)), [wrong_way], rate=NTSC_60, direction="out")
        self.assertEqual("motion_offset", as_out.snap_point_kind)

    def test_equidistant_snap_points_are_broken_by_strength_then_time(self):
        music = even_grid(8, Fraction(1, 2))
        before = SnapPoint(
            time=seconds_time(Fraction(1, 2) - Fraction(6, 1000)), kind="audio_onset", strength=0.4
        )
        after = SnapPoint(
            time=seconds_time(Fraction(1, 2) + Fraction(6, 1000)), kind="motion_onset", strength=0.9
        )
        forwards = snap_cut(music, seconds_time(Fraction(1, 2)), [before, after], rate=NTSC_60)
        backwards = snap_cut(music, seconds_time(Fraction(1, 2)), [after, before], rate=NTSC_60)
        self.assertEqual("motion_onset", forwards.snap_point_kind)
        self.assertEqual(forwards, backwards)

        # equal strength as well: the earlier point wins, in both input orders
        tied_early = SnapPoint(
            time=seconds_time(Fraction(1, 2) - Fraction(6, 1000)), kind="audio_onset", strength=0.9
        )
        first = snap_cut(music, seconds_time(Fraction(1, 2)), [tied_early, after], rate=NTSC_60)
        second = snap_cut(music, seconds_time(Fraction(1, 2)), [after, tied_early], rate=NTSC_60)
        self.assertEqual("audio_onset", first.snap_point_kind)
        self.assertEqual(first, second)

    def test_weak_snap_points_can_be_filtered_by_policy(self):
        music = even_grid(8, Fraction(1, 2))
        feeble = SnapPoint(
            time=seconds_time(Fraction(1, 2) + Fraction(6, 1000)), kind="motion_onset", strength=0.05
        )
        picky = BeatLockPolicy(min_snap_strength=0.3)
        self.assertIsNone(
            snap_cut(music, seconds_time(Fraction(1, 2)), [feeble], rate=NTSC_60, policy=picky).snap_point_kind
        )
        self.assertEqual(
            "motion_onset",
            snap_cut(music, seconds_time(Fraction(1, 2)), [feeble], rate=NTSC_60).snap_point_kind,
        )


class TestQuantiseThenCheck(unittest.TestCase):
    def test_a_snap_point_inside_tolerance_can_fall_outside_it_once_quantised(self):
        # 24000/1001 is a 41.7ms frame. The beat sits exactly half a frame off
        # the frame grid, and a snap point 45ms after it (inside the 50ms
        # tolerance) rounds to a frame 62.5ms after the beat. Checking the
        # tolerance before quantisation is how an EDL claims a lock the
        # renderer cannot honour.
        half_frame_offset = Fraction(49, 2) * Fraction(1001, 24000)
        beats = (
            Beat(index=0, time=RationalTime(Fraction(49, 2), NTSC_24), is_downbeat=True),
        )
        music = BeatGrid(source_cue_id="cue-01", bpm=60.0, beats=beats)
        onset = SnapPoint(
            time=seconds_time(half_frame_offset + Fraction(45, 1000)),
            kind="motion_onset",
            strength=0.9,
        )
        pre_quantisation_error_ms = 45.0
        self.assertLessEqual(pre_quantisation_error_ms, music.tolerance_ms)

        decision = snap_cut(music, onset.time, [onset], rate=NTSC_24)
        self.assertEqual(26, decision.frame)
        self.assertGreater(abs(decision.alignment_error_ms), music.tolerance_ms)
        self.assertFalse(decision.locked)
        self.assertEqual(REASON_ALIGNMENT_OUTSIDE_TOLERANCE, decision.reason)
        self.assertIsNone(decision.to_beat_lock())


# --------------------------------------------------------------------------
# sequences: shots shorter than a beat
# --------------------------------------------------------------------------


class TestSequencePlanning(unittest.TestCase):
    def test_cuts_are_strictly_separated_by_the_minimum_shot_length(self):
        # One cut per beat at 128 BPM is a 28-frame shot at 59.94. Ask for 40
        # frames minimum and every cut has to move; the failure this prevents is
        # a one-frame flash that the renderer will happily produce.
        music = even_grid(16, Fraction(15, 32))
        ideals = [RationalTime(Fraction(15, 32) * i, 1).rescaled_to(NTSC_60) for i in range(8)]
        decisions = plan_beat_locked_cuts(music, ideals, rate=NTSC_60, min_shot_frames=40)
        frames = [d.frame for d in decisions]
        self.assertEqual(sorted(frames), frames)
        for previous, current in zip(frames, frames[1:]):
            self.assertGreaterEqual(current - previous, 40)

    def test_a_repaired_cut_moves_to_a_later_beat_when_one_is_affordable(self):
        music = even_grid(16, Fraction(15, 32))
        ideals = [seconds_time(0), seconds_time(Fraction(15, 32))]
        # 40 frames > one beat (28), so cut 1 cannot stay on beat 1; beat 2 is
        # 56 frames in, which is affordable only with a wide pull budget.
        wide = BeatLockPolicy(max_pull_ms=600.0)
        decisions = plan_beat_locked_cuts(
            music, ideals, rate=NTSC_60, min_shot_frames=40, policy=wide
        )
        self.assertTrue(decisions[1].locked)
        self.assertEqual(2, decisions[1].beat_index)
        self.assertGreaterEqual(decisions[1].frame - decisions[0].frame, 40)

    def test_a_repaired_cut_drops_the_lock_rather_than_dragging_to_a_far_beat(self):
        music = even_grid(16, Fraction(15, 32))
        ideals = [seconds_time(0), seconds_time(Fraction(15, 32))]
        decisions = plan_beat_locked_cuts(music, ideals, rate=NTSC_60, min_shot_frames=40)
        self.assertTrue(decisions[0].locked)
        self.assertFalse(decisions[1].locked)
        self.assertEqual(REASON_BEAT_BEYOND_MAX_PULL, decisions[1].reason)
        self.assertGreaterEqual(decisions[1].frame - decisions[0].frame, 40)

    def test_repair_does_not_claim_a_snap_point_it_was_pushed_off(self):
        music = even_grid(16, Fraction(15, 32))
        onset = SnapPoint(time=seconds_time(Fraction(15, 32)), kind="motion_onset", strength=0.9)
        decisions = plan_beat_locked_cuts(
            music,
            [seconds_time(0), seconds_time(Fraction(15, 32))],
            rate=NTSC_60,
            snap_points=[[], [onset]],
            min_shot_frames=40,
        )
        self.assertFalse(decisions[1].locked)
        self.assertIsNone(decisions[1].snap_point_kind)

    def test_an_unconstrained_sequence_keeps_every_lock(self):
        music = even_grid(32, Fraction(15, 32))
        ideals = [RationalTime(Fraction(15, 8) * i, 1) for i in range(8)]  # one per bar
        decisions = plan_beat_locked_cuts(music, ideals, rate=NTSC_60, min_shot_frames=1)
        self.assertTrue(all(d.locked for d in decisions))
        self.assertEqual([0, 4, 8, 12, 16, 20, 24, 28], [d.beat_index for d in decisions])
        self.assertEqual([], list(alignment_gate(decisions, music)))

    def test_out_of_order_cuts_are_a_caller_bug_not_something_to_sort_away(self):
        music = even_grid(8, Fraction(1, 2))
        with self.assertRaises(BeatGridError):
            plan_beat_locked_cuts(music, [seconds_time(1), seconds_time(0)], rate=NTSC_60)

    def test_mismatched_snap_point_lists_are_rejected(self):
        music = even_grid(8, Fraction(1, 2))
        with self.assertRaises(BeatGridError):
            plan_beat_locked_cuts(
                music, [seconds_time(0), seconds_time(1)], rate=NTSC_60, snap_points=[[]]
            )

    def test_empty_input_is_empty_output(self):
        self.assertEqual((), plan_beat_locked_cuts(even_grid(4, Fraction(1, 2)), []))

    def test_min_shot_frames_must_be_at_least_one_frame(self):
        music = even_grid(8, Fraction(1, 2))
        with self.assertRaises(BeatGridError):
            plan_beat_locked_cuts(music, [seconds_time(0)], rate=NTSC_60, min_shot_frames=0)


# --------------------------------------------------------------------------
# gates and audits
# --------------------------------------------------------------------------


class TestAlignmentGate(unittest.TestCase):
    def test_a_locked_cut_outside_tolerance_is_a_violation(self):
        music = even_grid(4, Fraction(1, 2))
        bad = CutDecision(
            time=RationalTime(30, NTSC_60),
            locked=True,
            reason=REASON_LOCKED,
            beat_index=1,
            is_downbeat=False,
            alignment_error_ms=61.0,
        )
        violations = alignment_gate([bad], music)
        self.assertEqual(1, len(violations))
        self.assertEqual(0, violations[0].position)
        self.assertEqual(61.0, violations[0].error_ms)

    def test_an_unlocked_cut_is_not_gated(self):
        # Gating unlocked cuts would push planners toward hiding failed locks.
        music = even_grid(4, Fraction(1, 2))
        honest = CutDecision(
            time=RationalTime(30, NTSC_60),
            locked=False,
            reason=REASON_BEAT_BEYOND_MAX_PULL,
            beat_index=1,
            alignment_error_ms=210.0,
        )
        self.assertEqual((), alignment_gate([honest], music))

    def test_the_downbeat_ceiling_is_the_one_that_applies_to_downbeats(self):
        music = even_grid(4, Fraction(1, 2), tolerance_ms=90.0)
        off_beat = CutDecision(
            time=RationalTime(30, NTSC_60),
            locked=True,
            reason=REASON_LOCKED,
            beat_index=1,
            is_downbeat=False,
            alignment_error_ms=70.0,
        )
        down = CutDecision(
            time=RationalTime(30, NTSC_60),
            locked=True,
            reason=REASON_LOCKED,
            beat_index=0,
            is_downbeat=True,
            alignment_error_ms=70.0,
        )
        self.assertEqual((), alignment_gate([off_beat], music))
        self.assertEqual(1, len(alignment_gate([down], music)))


class TestAnalyzerLicence(unittest.TestCase):
    def test_a_madmom_grid_cannot_produce_a_cut(self):
        # madmom is BY-NC-SA (CLAUDE.md hard rule 4). The registry audit is the
        # real gate; this is the belt that catches a grid that got past it.
        blocked = even_grid(8, Fraction(1, 2), analyzer_model_id="madmom-dbn-beat-tracker")
        with self.assertRaises(BlockedAnalyzerError):
            snap_cut(blocked, seconds_time(Fraction(1, 2)), rate=NTSC_60)
        with self.assertRaises(BlockedAnalyzerError):
            plan_beat_locked_cuts(blocked, [seconds_time(0)], rate=NTSC_60)

    def test_a_blocked_grid_can_still_be_loaded_and_audited(self):
        blocked = even_grid(8, Fraction(1, 2), analyzer_model_id="essentia-rhythm")
        codes = [issue.code for issue in audit_grid(blocked)]
        self.assertIn(ISSUE_ANALYZER_BLOCKED, codes)

    def test_a_librosa_grid_is_allowed(self):
        ok = even_grid(8, Fraction(1, 2), analyzer_model_id="librosa-beat-track")
        self.assertTrue(snap_cut(ok, seconds_time(Fraction(1, 2)), rate=NTSC_60).locked)
        self.assertNotIn(ISSUE_ANALYZER_BLOCKED, [i.code for i in audit_grid(ok)])

    def test_a_name_that_merely_starts_with_a_blocked_word_is_not_matched(self):
        ok = even_grid(8, Fraction(1, 2), analyzer_model_id="madmomentum-tracker")
        self.assertTrue(snap_cut(ok, seconds_time(Fraction(1, 2)), rate=NTSC_60).locked)


class TestGridAudit(unittest.TestCase):
    def test_a_clean_grid_audits_clean(self):
        clean = even_grid(16, Fraction(15, 32), bpm=128.0, bpm_confidence=0.9)
        self.assertEqual((), audit_grid(clean))

    def test_a_half_time_tempo_error_is_reported(self):
        # The classic beat-tracker failure: beats detected at half the real
        # tempo. Structurally the grid is perfect, which is why this is an
        # audit and not a construction error.
        halved = even_grid(16, Fraction(15, 16), bpm=128.0)
        codes = [issue.code for issue in audit_grid(halved)]
        self.assertIn(ISSUE_BPM_DISAGREES_WITH_BEATS, codes)

    def test_a_mid_track_tempo_change_is_reported_with_its_beat_index(self):
        times = [Fraction(1, 2) * i for i in range(8)]
        times += [times[-1] + Fraction(1, 3) * (i + 1) for i in range(8)]
        drifting = grid(times, bpm=120.0)
        changes = [i for i in audit_grid(drifting) if i.code == ISSUE_TEMPO_CHANGE]
        self.assertTrue(changes)
        self.assertTrue(all(c.beat_index is not None for c in changes))

    def test_gentle_drift_is_not_reported_as_a_tempo_change(self):
        times = [Fraction(0)]
        for i in range(15):
            times.append(times[-1] + Fraction(1, 2) + Fraction(i, 4000))
        drifting = grid(times, bpm=120.0)
        self.assertEqual(
            [], [i for i in audit_grid(drifting) if i.code == ISSUE_TEMPO_CHANGE]
        )

    def test_an_unpinned_analyzer_is_reported(self):
        unpinned = even_grid(8, Fraction(1, 2), analyzer_model_id=None)
        self.assertIn(ISSUE_ANALYZER_UNPINNED, [i.code for i in audit_grid(unpinned)])

    def test_low_confidence_is_reported(self):
        unsure = even_grid(8, Fraction(1, 2), bpm_confidence=0.1)
        self.assertIn(ISSUE_LOW_BPM_CONFIDENCE, [i.code for i in audit_grid(unsure)])

    def test_audit_output_is_ordered(self):
        messy = even_grid(8, Fraction(1, 2), bpm=200.0, bpm_confidence=0.1, analyzer_model_id=None)
        issues = audit_grid(messy)
        self.assertEqual(list(issues), sorted(issues, key=lambda i: i.code))

    def test_measured_bpm_survives_one_dropped_beat(self):
        # Median, not mean: a single missed beat doubles one interval, and a
        # mean would report a tempo the track never plays.
        times = [Fraction(1, 2) * i for i in range(9)]
        del times[4]
        dropped = grid([t for t in times], bpm=120.0, with_signature=False)
        self.assertAlmostEqual(120.0, measured_bpm(dropped), places=9)
        self.assertIsNone(measured_bpm(even_grid(1, Fraction(1, 2))))


class TestDeterminism(unittest.TestCase):
    def test_the_same_inputs_produce_the_same_decision_object(self):
        music = even_grid(32, Fraction(15, 32))
        points = [
            SnapPoint(time=seconds_time(Fraction(15, 32) * 4 + Fraction(k, 1000)), kind=name, strength=0.5)
            for k, name in ((3, "audio_onset"), (-3, "motion_onset"), (9, "impact"))
        ]
        first = snap_cut(music, seconds_time(Fraction(15, 8)), points, rate=NTSC_60)
        second = snap_cut(music, seconds_time(Fraction(15, 8)), list(reversed(points)), rate=NTSC_60)
        self.assertEqual(first, second)
        self.assertEqual(first.to_beat_lock(), second.to_beat_lock())


if __name__ == "__main__":
    unittest.main()
