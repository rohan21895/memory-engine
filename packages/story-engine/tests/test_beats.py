"""Beat-locked cutting, tested for the ways it can be silently wrong.

Every defect this codebase has shipped so far was silent: plausible numbers, no
exception. Beat locking is unusually good at being silently wrong, because the
output is a small signed number that always looks reasonable. So the tests here
are built around the specific lies a plausible implementation tells:

  * it rounds the beat to a frame and calls the residual zero;
  * it extrapolates from BPM instead of reading the stored beat times, which is
    invisible for four bars and 1.5 seconds out by the end;
  * it emits a BeatLock whose alignment_error_ms exceeds the gate it claims to
    satisfy;
  * it locks two clips to the same beat and produces a zero-length shot;
  * it accumulates float seconds and drifts two frames over a reel;
  * it silently passes beats the analyser had no confidence in.

The golden test is the strongest one: the module re-derives, from the beat grid
alone, all eight alignment_error_ms values in the committed EDL fixture, and
then re-plans the same eight cuts and rediscovers the same eight beats.
"""

from __future__ import annotations

import json
import sys
import unittest
from fractions import Fraction
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from memory_engine_story.beats import (  # noqa: E402
    DOWNBEAT_GATE_MS,
    REASON_GRID_CONFIDENCE,
    REASON_LOCKED_BEAT,
    REASON_LOCKED_DOWNBEAT,
    REASON_NO_ELIGIBLE_BEAT,
    REASON_OUTSIDE_TOLERANCE,
    Beat,
    BeatGrid,
    BlockedAnalyzerError,
    LockPolicy,
    RationalTime,
    SnapPoint,
    _round_half_away,
    alignment_error_ms,
    audit_lock,
    beats_between,
    blocked_analyzer_reason,
    ceil_frame,
    floor_frame,
    frame_time,
    interval_after,
    local_bpm,
    lock_cut,
    nearest_beat,
    nearest_frame,
    plan_cuts,
    tempo_anomalies,
)

PACKAGE_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = PACKAGE_ROOT.parent.parent
FIXTURE = (
    REPO_ROOT
    / "contracts"
    / "fixtures"
    / "edl"
    / "valid"
    / "reel-beat-locked-vertical-reframe.json"
)
EDL_SCHEMA = REPO_ROOT / "contracts" / "schemas" / "edl.schema.json"

NTSC_5994 = Fraction(60000, 1001)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def make_grid(
    frames: list[float],
    *,
    rate: object,
    downbeat_every: int | None = None,
    downbeats: set[int] | None = None,
    strengths: dict[int, float | None] | None = None,
    bpm: float = 120.0,
    tolerance_ms: float = 50.0,
    bpm_confidence: float | None = None,
) -> BeatGrid:
    """A grid from explicit frame positions. Positions may be fractional --
    real beats do not land on frames, and a helper that only produced integral
    beats would hide the entire quantisation problem."""
    beats = []
    for index, position in enumerate(frames):
        if downbeats is not None:
            is_downbeat = index in downbeats
        elif downbeat_every is not None:
            is_downbeat = index % downbeat_every == 0
        else:
            is_downbeat = False
        strength = None
        if strengths is not None:
            strength = strengths.get(index, 1.0)
        beats.append(
            Beat(
                index=index,
                time=RationalTime(position, rate),
                is_downbeat=is_downbeat,
                strength=strength,
            )
        )
    return BeatGrid(
        source_cue_id="cue-test",
        bpm=bpm,
        beats=tuple(beats),
        tolerance_ms=tolerance_ms,
        bpm_confidence=bpm_confidence,
    )


# ---------------------------------------------------------------------------
# exact time
# ---------------------------------------------------------------------------


class TestRationalTime(unittest.TestCase):
    def test_frame_accumulation_at_5994_does_not_drift(self) -> None:
        # 1800 frames is a 30-second reel at 59.94. Summing one frame at a time
        # must land on frame 1800 EXACTLY, not on 1800 plus accumulated binary
        # error, because the last cut of the reel is placed against that total.
        step = RationalTime(1, NTSC_5994)
        total = RationalTime(0, NTSC_5994)
        for _ in range(1800):
            total = total + step
        self.assertEqual(total.value, Fraction(1800))
        self.assertEqual(total.value.denominator, 1)
        self.assertEqual(total, RationalTime(1800, NTSC_5994))

        # The same accumulation in float seconds does drift, which is the whole
        # reason this class exists. Anything below a frame is invisible in a
        # single add and lethal after a thousand.
        float_step = 1.0 / float(NTSC_5994)
        accumulated = 0.0
        for _ in range(1800):
            accumulated += float_step
        exact_seconds = float(Fraction(1800) / NTSC_5994)
        self.assertNotEqual(accumulated, exact_seconds)

    def test_seconds_are_exact_rationals_not_floats(self) -> None:
        one_frame = RationalTime(1, Fraction(30000, 1001))
        self.assertEqual(one_frame.seconds, Fraction(1001, 30000))
        self.assertIsInstance(one_frame.seconds, Fraction)

    def test_equality_and_hash_are_by_instant_not_by_fields(self) -> None:
        # 60 units at 60/s and 1 unit at 1/s are the same moment. If __eq__
        # said so but __hash__ did not, a dict keyed on cut times would hold
        # both and a dedupe would silently pass.
        a = RationalTime(60, 60)
        b = RationalTime(1, 1)
        self.assertEqual(a, b)
        self.assertEqual(hash(a), hash(b))
        self.assertEqual(len({a, b}), 1)
        self.assertNotEqual(a, RationalTime(61, 60))

    def test_ordering(self) -> None:
        self.assertLess(RationalTime(1, 24), RationalTime(1, 23))
        self.assertGreater(RationalTime(48, 24), RationalTime(1, 1))
        self.assertLessEqual(RationalTime(24, 24), RationalTime(1, 1))
        self.assertGreaterEqual(RationalTime(24, 24), RationalTime(1, 1))

    def test_rescale_is_lossless_and_keeps_the_instant(self) -> None:
        samples = RationalTime(48000, 48000)  # one second of audio
        as_video = samples.rescaled_to(NTSC_5994)
        self.assertEqual(as_video.seconds, Fraction(1))
        self.assertEqual(as_video.value, NTSC_5994)  # fractional on purpose
        self.assertEqual(as_video.rescaled_to(48000), samples)

    def test_subtraction_result_carries_the_left_hand_rate(self) -> None:
        delta = RationalTime(100, 60) - RationalTime(48000, 48000)
        self.assertEqual(delta.rate, Fraction(60))
        self.assertEqual(delta.value, Fraction(40))

    def test_rejects_bool_and_nonpositive_rate(self) -> None:
        # bool is an int subclass: a stray True would become rate=1 and put
        # every time in the plan off by the real frame rate.
        with self.assertRaises(TypeError):
            RationalTime(1, True)
        with self.assertRaises(ValueError):
            RationalTime(1, 0)
        with self.assertRaises(ValueError):
            RationalTime(1, -24)
        with self.assertRaises(ValueError):
            RationalTime(float("nan"), 24)

    def test_contract_round_trip_is_lossless_for_every_fixture_beat(self) -> None:
        data = json.loads(FIXTURE.read_text(encoding="utf-8"))
        for beat in data["beat_grid"]["beats"]:
            original = beat["time"]
            self.assertEqual(RationalTime.from_contract(original).to_contract(), original)

    def test_to_contract_emits_int_for_integral_values(self) -> None:
        emitted = RationalTime(112, NTSC_5994).to_contract()
        self.assertIsInstance(emitted["value"], int)
        self.assertEqual(emitted["value"], 112)


class TestFrameQuantisation(unittest.TestCase):
    def test_floor_ceil_nearest(self) -> None:
        rate = 60
        t = RationalTime(Fraction(1004, 10), rate)  # frame 100.4
        self.assertEqual(floor_frame(t, rate), 100)
        self.assertEqual(ceil_frame(t, rate), 101)
        self.assertEqual(nearest_frame(t, rate), 100)
        self.assertEqual(nearest_frame(RationalTime(Fraction(1006, 10), rate), rate), 101)

    def test_exact_half_goes_to_the_later_frame_not_to_even(self) -> None:
        # round() is banker's: it would send 112.5 -> 112 and 113.5 -> 114, so
        # two cuts one beat apart would quantise in opposite directions.
        rate = 60
        self.assertEqual(nearest_frame(RationalTime(Fraction(225, 2), rate), rate), 113)
        self.assertEqual(nearest_frame(RationalTime(Fraction(227, 2), rate), rate), 114)

    def test_quantisation_respects_the_target_rate_not_the_source_rate(self) -> None:
        one_second_of_audio = RationalTime(48000, 48000)
        self.assertEqual(nearest_frame(one_second_of_audio, 30), 30)
        self.assertEqual(floor_frame(one_second_of_audio, NTSC_5994), 59)

    def test_round_half_away_from_zero_is_symmetric(self) -> None:
        # The quality gate is on |error|, so +x and -x must round to the same
        # magnitude. Banker's rounding does not: round(2.5)==2 but
        # round(3.5)==4, so two errors of equal magnitude on opposite sides of
        # a beat would be reported with different magnitudes.
        self.assertEqual(_round_half_away(Fraction(5, 2), 0), 3.0)
        self.assertEqual(_round_half_away(Fraction(-5, 2), 0), -3.0)
        self.assertEqual(round(2.5), 2)  # what we are deliberately not doing
        self.assertEqual(_round_half_away(Fraction(7, 2), 0), 4.0)
        self.assertEqual(_round_half_away(Fraction(-7, 2), 0), -4.0)
        positive = _round_half_away(Fraction(125, 100000), 4)
        negative = _round_half_away(Fraction(-125, 100000), 4)
        self.assertEqual(positive, -negative)
        self.assertEqual(positive, 0.0013)


# ---------------------------------------------------------------------------
# grid construction and validation
# ---------------------------------------------------------------------------


class TestBeatGridValidation(unittest.TestCase):
    def test_empty_grid_rejected(self) -> None:
        with self.assertRaises(ValueError):
            BeatGrid(source_cue_id="c", bpm=120.0, beats=())

    def test_non_monotonic_beats_rejected(self) -> None:
        beats = (
            Beat(0, RationalTime(0, 60), True),
            Beat(1, RationalTime(60, 60), False),
            Beat(2, RationalTime(30, 60), False),
        )
        with self.assertRaises(ValueError):
            BeatGrid(source_cue_id="c", bpm=120.0, beats=beats)

    def test_duplicate_beat_times_rejected(self) -> None:
        # Two beats at one instant make "the nearest beat" ambiguous, and an
        # ambiguous nearest is a nondeterministic plan.
        beats = (
            Beat(0, RationalTime(0, 60), True),
            Beat(1, RationalTime(30, 60), False),
            Beat(2, RationalTime(30, 60), False),
        )
        with self.assertRaises(ValueError):
            BeatGrid(source_cue_id="c", bpm=120.0, beats=beats)

    def test_index_must_match_position(self) -> None:
        # BeatLock.beat_index is positional. A grid whose beats carry their own
        # disagreeing index would emit locks pointing at the wrong beat, and
        # every one of them would validate against the schema.
        beats = (
            Beat(0, RationalTime(0, 60), True),
            Beat(7, RationalTime(30, 60), False),
        )
        with self.assertRaises(ValueError):
            BeatGrid(source_cue_id="c", bpm=120.0, beats=beats)

    def test_beat_field_ranges(self) -> None:
        with self.assertRaises(ValueError):
            Beat(0, RationalTime(0, 60), True, strength=1.5)
        with self.assertRaises(ValueError):
            Beat(0, RationalTime(0, 60), True, beat_in_bar=0)
        with self.assertRaises(ValueError):
            Beat(-1, RationalTime(0, 60), True)
        with self.assertRaises(TypeError):
            Beat(0, RationalTime(0, 60), 1)  # type: ignore[arg-type]

    def test_grid_scalar_ranges(self) -> None:
        beats = (Beat(0, RationalTime(0, 60), True),)
        with self.assertRaises(ValueError):
            BeatGrid(source_cue_id="c", bpm=0.0, beats=beats)
        with self.assertRaises(ValueError):
            BeatGrid(source_cue_id="c", bpm=120.0, beats=beats, tolerance_ms=-1.0)
        with self.assertRaises(ValueError):
            BeatGrid(source_cue_id="c", bpm=120.0, beats=beats, bpm_confidence=1.4)


class TestAnalyzerLicence(unittest.TestCase):
    def _grid_data(self, model_id: str) -> dict:
        return {
            "source_cue_id": "cue-01",
            "bpm": 120.0,
            "beats": [{"index": 0, "time": {"value": 0, "rate": 60}, "is_downbeat": True}],
            "analyzer": {"model_id": model_id, "version": "1", "weights_blake3": None},
        }

    def test_madmom_grid_is_refused(self) -> None:
        with self.assertRaises(BlockedAnalyzerError):
            BeatGrid.from_contract(self._grid_data("madmom-dbn-downbeat"))

    def test_essentia_grid_is_refused(self) -> None:
        with self.assertRaises(BlockedAnalyzerError):
            BeatGrid.from_contract(self._grid_data("essentia"))

    def test_librosa_grid_is_accepted(self) -> None:
        grid = BeatGrid.from_contract(self._grid_data("librosa-beat-track"))
        self.assertEqual(grid.analyzer_id, "librosa-beat-track")

    def test_opt_in_is_explicit_not_silent(self) -> None:
        grid = BeatGrid.from_contract(
            self._grid_data("madmom"), allow_non_commercial_analyzer=True
        )
        self.assertEqual(grid.analyzer_id, "madmom")

    def test_stem_match_is_bounded(self) -> None:
        # Prefix matching must stop at a separator, or an unrelated analyser
        # whose name merely starts with a blocked stem gets banned.
        self.assertIsNotNone(blocked_analyzer_reason("madmom_beat"))
        self.assertIsNotNone(blocked_analyzer_reason("MADMOM"))
        self.assertIsNone(blocked_analyzer_reason("madmomentum-tracker"))
        self.assertIsNone(blocked_analyzer_reason("librosa"))


# ---------------------------------------------------------------------------
# grid queries and tempo
# ---------------------------------------------------------------------------


class TestGridQueries(unittest.TestCase):
    def test_nearest_beat_breaks_exact_ties_toward_the_earlier_beat(self) -> None:
        grid = make_grid([0.0, 60.0], rate=60)
        exact_midpoint = RationalTime(30, 60)
        self.assertEqual(nearest_beat(grid, exact_midpoint).index, 0)

    def test_nearest_beat_honours_filters(self) -> None:
        grid = make_grid([0.0, 30.0, 60.0], rate=60, downbeats={0, 2})
        near_offbeat = RationalTime(31, 60)
        self.assertEqual(nearest_beat(grid, near_offbeat).index, 1)
        self.assertEqual(nearest_beat(grid, near_offbeat, downbeats_only=True).index, 2)

    def test_nearest_beat_with_no_eligible_beat_raises(self) -> None:
        grid = make_grid([0.0, 30.0], rate=60)  # no downbeats at all
        with self.assertRaises(ValueError):
            nearest_beat(grid, RationalTime(0, 60), downbeats_only=True)

    def test_beats_between_is_half_open(self) -> None:
        grid = make_grid([0.0, 30.0, 60.0, 90.0], rate=60)
        found = beats_between(grid, RationalTime(30, 60), RationalTime(90, 60))
        self.assertEqual([b.index for b in found], [1, 2])

    def test_interval_after_uses_real_times_and_ends_at_none(self) -> None:
        grid = make_grid([0.0, 30.0, 75.0], rate=60)
        self.assertEqual(interval_after(grid, 0), Fraction(1, 2))
        self.assertEqual(interval_after(grid, 1), Fraction(3, 4))
        self.assertIsNone(interval_after(grid, 2))
        with self.assertRaises(IndexError):
            interval_after(grid, 3)


class TestTempoDrift(unittest.TestCase):
    """A track that speeds up. This is where BPM extrapolation dies."""

    def _accelerando(self) -> BeatGrid:
        # 40 beats, interval shrinking 2ms per beat from 500ms. Human drummers
        # and a good deal of licensed library music do exactly this.
        seconds = [Fraction(0)]
        for k in range(39):
            seconds.append(seconds[-1] + Fraction(1, 2) - Fraction(2, 1000) * k)
        frames = [s * 60 for s in seconds]
        return make_grid(
            [float(f) for f in frames], rate=60, downbeat_every=4, bpm=120.0
        )

    def test_grid_times_diverge_from_bpm_extrapolation(self) -> None:
        grid = self._accelerando()
        extrapolated = Fraction(39) * Fraction(1, 2)  # 39 beats at the stated 120bpm
        actual = grid.beats[39].seconds
        self.assertGreater(abs(extrapolated - actual), Fraction(1))

    def test_lock_uses_the_real_beat_not_the_extrapolated_one(self) -> None:
        grid = self._accelerando()
        rate = 60
        real = grid.beats[39].time.rescaled_to(rate)
        decision = lock_cut(grid, real, rate=rate)
        self.assertTrue(decision.locked)
        self.assertEqual(decision.beat_index, 39)
        self.assertLessEqual(abs(decision.alignment_error_ms or 0.0), DOWNBEAT_GATE_MS)

        # A planner that extrapolated from BPM would put this cut here, and
        # there is no beat within a second of it, so it must refuse to lock.
        extrapolated = RationalTime(Fraction(39, 2) * 60, rate)
        self.assertFalse(lock_cut(grid, extrapolated, rate=rate).locked)

    def test_local_bpm_tracks_the_acceleration(self) -> None:
        grid = self._accelerando()
        start = local_bpm(grid, 0)
        end = local_bpm(grid, 38)
        assert start is not None and end is not None
        self.assertAlmostEqual(start, 120.0, places=6)
        self.assertGreater(end - start, 15.0)
        self.assertIsNone(local_bpm(grid, 39))

    def test_tempo_anomalies_finds_a_dropped_beat_and_stays_quiet_otherwise(self) -> None:
        steady = make_grid([0.0, 30.0, 60.0, 90.0, 120.0], rate=60)
        self.assertEqual(tempo_anomalies(steady), ())
        # beat 2 -> 3 is a double-length gap: the tracker missed one.
        dropped = make_grid([0.0, 30.0, 60.0, 120.0, 150.0, 180.0], rate=60)
        self.assertEqual(tempo_anomalies(dropped), (2,))
        # An accelerando is drift, not an anomaly, and must not be flagged.
        self.assertEqual(tempo_anomalies(self._accelerando()), ())

    def test_anomaly_baseline_is_the_median_not_the_mean(self) -> None:
        # One four-second gap at the end (an outro pause) drags the MEAN to
        # 0.94s, at which point every ordinary 0.5s beat interval looks
        # anomalously short and the whole track is flagged. The median stays at
        # 0.5s and flags only the gap. Getting this wrong does not raise -- it
        # just makes the diagnostic useless on exactly the tracks that need it.
        frames = [0.0, 30.0, 60.0, 90.0, 120.0, 150.0, 180.0, 210.0, 450.0]
        grid = make_grid(frames, rate=60)
        self.assertEqual(tempo_anomalies(grid), (7,))

    def test_tempo_anomalies_rejects_a_meaningless_ratio(self) -> None:
        with self.assertRaises(ValueError):
            tempo_anomalies(make_grid([0.0, 30.0, 60.0], rate=60), ratio=1.0)


# ---------------------------------------------------------------------------
# the golden fixture
# ---------------------------------------------------------------------------


class TestGoldenFixture(unittest.TestCase):
    """The committed EDL says these eight cuts are beat-locked. Prove it."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.data = json.loads(FIXTURE.read_text(encoding="utf-8"))
        cls.grid = BeatGrid.from_contract(cls.data["beat_grid"])
        cls.rate = cls.data["rate"]
        cls.clips = [
            item
            for track in cls.data["tracks"]
            for item in track["items"]
            if item.get("item_type") == "clip" and item.get("beat_lock")
        ]

    def test_fixture_is_the_one_we_think_it_is(self) -> None:
        self.assertEqual(len(self.clips), 8)
        self.assertEqual(self.grid.analyzer_id, "librosa-beat-track")
        self.assertEqual(self.grid.tolerance_ms, 50.0)

    def test_every_declared_alignment_error_is_reproduced_exactly(self) -> None:
        for clip in self.clips:
            start = RationalTime.from_contract(clip["timeline_range"]["start_time"])
            lock = clip["beat_lock"]
            recomputed = alignment_error_ms(self.grid, lock["beat_index"], start)
            self.assertEqual(
                recomputed,
                lock["alignment_error_ms"],
                f"{clip['clip_id']}: fixture says {lock['alignment_error_ms']}ms",
            )

    def test_every_declared_lock_passes_audit(self) -> None:
        for clip in self.clips:
            start = RationalTime.from_contract(clip["timeline_range"]["start_time"])
            self.assertEqual(audit_lock(self.grid, start, clip["beat_lock"]), ())

    def test_replanning_rediscovers_the_same_beats_and_frames(self) -> None:
        # Feed the planner only the content-ideal positions and the grid; it
        # must land on the same frames and claim the same beats as the fixture,
        # with no knowledge of the fixture's answers.
        ideals = [
            RationalTime.from_contract(clip["timeline_range"]["start_time"])
            for clip in self.clips
        ]
        decisions = plan_cuts(self.grid, ideals, rate=self.rate)
        self.assertEqual(
            [d.beat_index for d in decisions],
            [clip["beat_lock"]["beat_index"] for clip in self.clips],
        )
        self.assertEqual(
            [int(d.time.value) for d in decisions],
            [int(clip["timeline_range"]["start_time"]["value"]) for clip in self.clips],
        )
        self.assertEqual(
            [d.alignment_error_ms for d in decisions],
            [clip["beat_lock"]["alignment_error_ms"] for clip in self.clips],
        )
        self.assertTrue(all(d.locked and d.is_downbeat for d in decisions))

    def test_emitted_beat_lock_validates_against_the_edl_schema(self) -> None:
        jsonschema = __import__("jsonschema")
        schema = json.loads(EDL_SCHEMA.read_text(encoding="utf-8"))
        beat_lock_schema = schema["$defs"]["BeatLock"]
        ideals = [
            RationalTime.from_contract(clip["timeline_range"]["start_time"])
            for clip in self.clips
        ]
        for decision in plan_cuts(self.grid, ideals, rate=self.rate):
            emitted = decision.to_beat_lock()
            self.assertIsNotNone(emitted)
            jsonschema.validate(emitted, beat_lock_schema)

    def test_fixture_grid_has_no_tempo_anomalies(self) -> None:
        self.assertEqual(tempo_anomalies(self.grid), ())


# ---------------------------------------------------------------------------
# the three-way fight: music vs frames vs content
# ---------------------------------------------------------------------------


class TestLockBreaking(unittest.TestCase):
    def test_frame_grid_too_coarse_to_reach_the_beat_breaks_the_lock(self) -> None:
        # 4fps: frames are 250ms apart, so a beat at 600ms is 100ms from the
        # nearest frame. No cut can be within the 50ms tolerance. The tempting
        # bug is to round to frame 2 and report 0.0ms.
        grid = make_grid([2.4], rate=4, downbeats={0})
        ideal = RationalTime(Fraction(24, 10), 4)
        decision = lock_cut(grid, ideal, rate=4)
        self.assertFalse(decision.locked)
        self.assertEqual(decision.reason, REASON_OUTSIDE_TOLERANCE)
        self.assertIsNone(decision.beat_index)
        self.assertIsNone(decision.alignment_error_ms)
        self.assertIsNone(decision.to_beat_lock())
        self.assertEqual(decision.time, RationalTime(2, 4))

    def test_nearest_beat_too_far_for_the_content_budget_breaks_the_lock(self) -> None:
        # Beats one second apart; the content wants a cut halfway between. We
        # will not drag the cut 500ms to reach a beat.
        grid = make_grid([0.0, 60.0], rate=60, downbeats={0, 1})
        decision = lock_cut(grid, RationalTime(30, 60), rate=60)
        self.assertFalse(decision.locked)
        self.assertEqual(decision.reason, REASON_NO_ELIGIBLE_BEAT)
        self.assertEqual(decision.time, RationalTime(30, 60))
        self.assertEqual(decision.content_drift_ms, 0.0)

    def test_downbeat_gate_is_stricter_than_a_loosened_tolerance(self) -> None:
        # Identical geometry, identical 200ms tolerance; the only difference is
        # whether the beat is a downbeat. 60ms out is acceptable for an
        # off-beat under this tolerance and never acceptable for a downbeat.
        # 4fps: frame 2 is 0.500s, the beat is at frame 2.24 = 0.560s, so the
        # best reachable cut is 60ms early.
        ideal = RationalTime(Fraction(224, 100), 4)
        offbeat = make_grid([2.24], rate=4, downbeats=set(), tolerance_ms=200.0)
        loose = lock_cut(offbeat, ideal, rate=4)
        self.assertTrue(loose.locked)
        self.assertEqual(loose.reason, REASON_LOCKED_BEAT)
        self.assertAlmostEqual(loose.alignment_error_ms or 0.0, -60.0, places=4)

        downbeat = make_grid([2.24], rate=4, downbeats={0}, tolerance_ms=200.0)
        strict = lock_cut(downbeat, ideal, rate=4)
        self.assertFalse(strict.locked)
        self.assertEqual(strict.reason, REASON_OUTSIDE_TOLERANCE)

    def test_gate_is_the_minimum_of_tolerance_and_the_downbeat_ceiling(self) -> None:
        policy = LockPolicy()
        loose = make_grid([0.0], rate=60, downbeats={0}, tolerance_ms=200.0)
        tight = make_grid([0.0], rate=60, downbeats={0}, tolerance_ms=20.0)
        self.assertEqual(policy.gate_ms(loose, is_downbeat=True), Fraction(50))
        self.assertEqual(policy.gate_ms(loose, is_downbeat=False), Fraction(200))
        # Tightening tolerance below the ceiling must tighten downbeats too.
        self.assertEqual(policy.gate_ms(tight, is_downbeat=True), Fraction(20))

    def test_error_exactly_on_the_gate_still_locks(self) -> None:
        # tolerance is documented as the MAXIMUM ACCEPTABLE error, so the
        # boundary is inclusive. 10fps frames are 100ms apart and the beat sits
        # exactly between two of them: both candidates are exactly 50.0ms out.
        # An exclusive comparison would reject a cut that is precisely on gate.
        grid = make_grid([2.5], rate=10, downbeats={0})
        decision = lock_cut(grid, RationalTime(Fraction(5, 2), 10), rate=10)
        self.assertTrue(decision.locked)
        self.assertEqual(decision.alignment_error_ms, -50.0)
        self.assertEqual(decision.time, RationalTime(2, 10))

    def test_low_grid_confidence_refuses_to_lock_even_on_an_exact_beat(self) -> None:
        grid = make_grid([100.0], rate=60, downbeats={0}, bpm_confidence=0.3)
        ideal = RationalTime(100, 60)
        decision = lock_cut(grid, ideal, rate=60)
        self.assertFalse(decision.locked)
        self.assertEqual(decision.reason, REASON_GRID_CONFIDENCE)
        self.assertEqual(decision.time, ideal)  # cut still happens, honestly

        permissive = LockPolicy(min_bpm_confidence=0.2)
        self.assertTrue(lock_cut(grid, ideal, rate=60, policy=permissive).locked)

    def test_weak_beats_are_skipped_and_unknown_strength_never_passes_a_floor(self) -> None:
        # A breakdown where the tracker's per-beat confidence collapses. The
        # tempting bug is to treat a missing strength as "fine".
        grid = make_grid(
            [100.0, 130.0],
            rate=60,
            downbeats={0, 1},
            strengths={0: 0.2, 1: None},
        )
        policy = LockPolicy(min_beat_strength=0.5)
        self.assertFalse(lock_cut(grid, RationalTime(100, 60), rate=60, policy=policy).locked)
        self.assertFalse(lock_cut(grid, RationalTime(130, 60), rate=60, policy=policy).locked)
        # Same grid, no floor: both lock.
        self.assertTrue(lock_cut(grid, RationalTime(100, 60), rate=60).locked)
        self.assertTrue(lock_cut(grid, RationalTime(130, 60), rate=60).locked)

    def test_downbeats_only_policy(self) -> None:
        grid = make_grid([100.0, 130.0], rate=60, downbeats={1})
        onbeat = lock_cut(grid, RationalTime(100, 60), rate=60)
        self.assertEqual(onbeat.beat_index, 0)
        restricted = lock_cut(
            grid, RationalTime(100, 60), rate=60, policy=LockPolicy(downbeats_only=True)
        )
        self.assertFalse(restricted.locked)
        self.assertEqual(restricted.reason, REASON_NO_ELIGIBLE_BEAT)


class TestPreferences(unittest.TestCase):
    def test_a_downbeat_wins_over_a_closer_offbeat_within_its_budget(self) -> None:
        # Synthetic spacing so the two candidates are close enough to compete:
        # downbeat at frame 100, off-beat at frame 103, content wants 102.
        grid = make_grid([100.0, 103.0], rate=100, downbeats={0})
        ideal = RationalTime(102, 100)

        chosen = lock_cut(grid, ideal, rate=100)
        self.assertEqual(chosen.beat_index, 0)
        self.assertTrue(chosen.is_downbeat)
        self.assertEqual(chosen.reason, REASON_LOCKED_DOWNBEAT)
        self.assertEqual(chosen.time, RationalTime(100, 100))
        self.assertEqual(chosen.content_drift_ms, -20.0)

        # With the deliberateness bonus removed, the nearer off-beat wins --
        # which is what proves the bonus is doing the work above.
        no_bonus = lock_cut(grid, ideal, rate=100, policy=LockPolicy(downbeat_bonus_ms=0.0))
        self.assertEqual(no_bonus.beat_index, 1)
        self.assertFalse(no_bonus.is_downbeat)

    def test_a_snap_point_pulls_the_cut_off_the_bare_beat_quantisation(self) -> None:
        # Beat at frame 100, content ideal at 102, a real motion onset at 102.
        grid = make_grid([100.0], rate=100, downbeats={0})
        ideal = RationalTime(102, 100)
        snaps = [SnapPoint(RationalTime(102, 100), "motion_onset")]

        chosen = lock_cut(grid, ideal, rate=100, snap_points=snaps)
        self.assertEqual(chosen.time, RationalTime(102, 100))
        self.assertEqual(chosen.snap_point_kind, "motion_onset")
        self.assertTrue(chosen.locked)
        self.assertEqual(chosen.alignment_error_ms, 20.0)

        without = lock_cut(
            grid, ideal, rate=100, snap_points=snaps, policy=LockPolicy(snap_bonus_ms=0.0)
        )
        self.assertEqual(without.time, RationalTime(100, 100))
        self.assertIsNone(without.snap_point_kind)

    def test_snap_kind_is_recorded_in_the_emitted_beat_lock(self) -> None:
        grid = make_grid([100.0], rate=100, downbeats={0})
        decision = lock_cut(
            grid,
            RationalTime(102, 100),
            rate=100,
            snap_points=[SnapPoint(RationalTime(102, 100), "motion_onset")],
        )
        lock = decision.to_beat_lock()
        assert lock is not None
        self.assertEqual(lock["snap_point_kind"], "motion_onset")
        self.assertEqual(lock["beat_index"], 0)
        self.assertIs(lock["is_downbeat"], True)

    def test_the_frame_after_the_beat_can_win(self) -> None:
        # The beat is at frame 100.9 and the content wants 102. Walking one
        # frame earlier trades 16.67ms of drift for 16.67ms of beat error, so
        # cost is flat and the |error| tie-break decides -- landing on frame
        # 101, the CEILING of the beat, which is neither the ideal frame nor
        # the floor. An implementation that only ever quantises the beat one
        # way (or that only considers the ideal frame) never finds it.
        grid = make_grid([100.9], rate=60, downbeats={0})
        decision = lock_cut(grid, RationalTime(102, 60), rate=60)
        self.assertTrue(decision.locked)
        self.assertEqual(decision.time, RationalTime(101, 60))
        self.assertEqual(decision.alignment_error_ms, 1.6667)
        self.assertEqual(decision.content_drift_ms, -16.6667)

    def test_a_fractional_earliest_bound_is_never_violated(self) -> None:
        # `earliest` comes from the previous clip and need not be integral once
        # rates differ. Flooring it would place this cut at frame 101, half a
        # frame INSIDE the previous clip -- overlapping clips, undefined render.
        grid = make_grid([101.0], rate=60, downbeats={0})
        earliest = RationalTime(Fraction(203, 2), 60)  # frame 101.5
        decision = lock_cut(grid, RationalTime(101, 60), rate=60, earliest=earliest)
        self.assertGreaterEqual(decision.time, earliest)
        self.assertEqual(decision.time, RationalTime(102, 60))
        # ...and it still locks: frame 102 is 16.67ms off the beat, well inside
        # the gate. Being pushed off the beat's own frames must not silently
        # downgrade an honest lock to an unlocked cut.
        self.assertTrue(decision.locked)
        self.assertEqual(decision.beat_index, 0)
        self.assertEqual(decision.alignment_error_ms, 16.6667)
        # Without the bound the cut sits on the beat exactly.
        self.assertEqual(lock_cut(grid, RationalTime(101, 60), rate=60).time, RationalTime(101, 60))

    def test_alignment_weight_can_price_beat_error_above_content_drift(self) -> None:
        grid = make_grid([100.0], rate=100, downbeats={0})
        ideal = RationalTime(103, 100)
        # Cheap alignment: stay near the content.
        cheap = lock_cut(grid, ideal, rate=100, policy=LockPolicy(alignment_weight=0.0))
        self.assertEqual(cheap.time, RationalTime(103, 100))
        # Expensive alignment: go to the beat.
        dear = lock_cut(grid, ideal, rate=100, policy=LockPolicy(alignment_weight=5.0))
        self.assertEqual(dear.time, RationalTime(100, 100))


# ---------------------------------------------------------------------------
# sequences
# ---------------------------------------------------------------------------


class TestPlanCuts(unittest.TestCase):
    def _steady(self) -> BeatGrid:
        # 120bpm at 60fps: a beat every 30 frames, downbeat every 4 beats.
        return make_grid([30.0 * k for k in range(9)], rate=60, downbeat_every=4)

    def test_a_shot_shorter_than_a_beat_comes_back_unlocked_not_stretched(self) -> None:
        grid = self._steady()
        # Two ideal cuts 3 frames (50ms) apart, both inside one beat interval.
        decisions = plan_cuts(
            grid, [RationalTime(30, 60), RationalTime(33, 60)], rate=60
        )
        first, second = decisions
        self.assertTrue(first.locked)
        self.assertEqual(first.beat_index, 1)
        self.assertEqual(first.time, RationalTime(30, 60))

        self.assertFalse(second.locked)
        self.assertIsNone(second.beat_index)
        self.assertIsNone(second.to_beat_lock())
        # Not stretched to the next beat (frame 60) and not collapsed onto the
        # first cut: it stays where the content asked for it.
        self.assertEqual(second.time, RationalTime(33, 60))
        self.assertGreater(second.time, first.time)

    def test_min_clip_frames_is_enforced(self) -> None:
        grid = self._steady()
        decisions = plan_cuts(
            grid, [RationalTime(30, 60), RationalTime(31, 60)], rate=60
        )
        self.assertEqual(decisions[0].time, RationalTime(30, 60))
        self.assertEqual(decisions[1].time, RationalTime(32, 60))
        self.assertGreaterEqual(
            decisions[1].time.value - decisions[0].time.value, Fraction(2)
        )

        wider = plan_cuts(
            grid,
            [RationalTime(30, 60), RationalTime(31, 60)],
            rate=60,
            policy=LockPolicy(min_clip_frames=6),
        )
        self.assertEqual(wider[1].time, RationalTime(36, 60))

    def test_no_beat_is_claimed_by_two_clips(self) -> None:
        # One beat at frame 100.4; tolerance 50ms is 3 frames at 60fps, so
        # frames 100 and 102 are BOTH within tolerance of it. Without the
        # exclusion both cuts would declare themselves locked to beat 0.
        grid = make_grid([100.4], rate=60, downbeats={0})
        decisions = plan_cuts(grid, [RationalTime(100, 60), RationalTime(102, 60)], rate=60)
        self.assertEqual(decisions[0].beat_index, 0)
        self.assertIsNone(decisions[1].beat_index)
        self.assertFalse(decisions[1].locked)

        # Proof the second cut really was lockable and was refused on purpose,
        # not merely out of range: the same call without the exclusion locks.
        would_have = lock_cut(
            grid, RationalTime(102, 60), rate=60, earliest=RationalTime(102, 60)
        )
        self.assertTrue(would_have.locked)
        self.assertEqual(would_have.beat_index, 0)

    def test_cuts_are_strictly_increasing_across_a_long_plan(self) -> None:
        grid = self._steady()
        ideals = [RationalTime(v, 60) for v in (0, 28, 61, 63, 90, 124, 180, 182, 240)]
        decisions = plan_cuts(grid, ideals, rate=60)
        times = [d.time for d in decisions]
        for earlier, later in zip(times, times[1:]):
            self.assertGreater(later, earlier)
        # Every locked cut sits within its own gate.
        for decision in decisions:
            if decision.locked:
                assert decision.beat_index is not None
                self.assertEqual(
                    audit_lock(grid, decision.time, decision.to_beat_lock() or {}), ()
                )
        # And no beat index appears twice.
        claimed = [d.beat_index for d in decisions if d.beat_index is not None]
        self.assertEqual(len(claimed), len(set(claimed)))

    def test_unordered_ideals_raise_rather_than_being_silently_sorted(self) -> None:
        grid = self._steady()
        with self.assertRaises(ValueError):
            plan_cuts(grid, [RationalTime(60, 60), RationalTime(30, 60)], rate=60)
        with self.assertRaises(ValueError):
            plan_cuts(grid, [RationalTime(30, 60), RationalTime(30, 60)], rate=60)

    def test_empty_plan(self) -> None:
        self.assertEqual(plan_cuts(self._steady(), [], rate=60), ())


class TestCrossRate(unittest.TestCase):
    def test_grid_in_audio_samples_locks_a_2997_timeline(self) -> None:
        # Beat grid stored at 48kHz (the analyser's native rate), timeline at
        # 30000/1001. If any step went through float seconds this is where it
        # would show up.
        rate = Fraction(30000, 1001)
        sample_positions = [48000 * k // 2 for k in range(9)]  # every 0.5s
        beats = tuple(
            Beat(index, RationalTime(pos, 48000), index % 4 == 0)
            for index, pos in enumerate(sample_positions)
        )
        grid = BeatGrid(source_cue_id="cue-audio", bpm=120.0, beats=beats)

        # Content wants a cut near the 4th beat (2.0s exactly).
        ideal = RationalTime.from_seconds(Fraction(2), rate)
        decision = lock_cut(grid, ideal, rate=rate)
        self.assertTrue(decision.locked)
        self.assertEqual(decision.beat_index, 4)
        self.assertTrue(decision.is_downbeat)
        # 2.0s is 59.94005... frames, so no frame is exactly on it; the best
        # possible error is under half a frame, ~16.7ms.
        self.assertLess(abs(decision.alignment_error_ms or 0.0), 17.0)
        self.assertEqual(decision.time.rate, rate)
        self.assertEqual(decision.time.value.denominator, 1)


# ---------------------------------------------------------------------------
# determinism
# ---------------------------------------------------------------------------


class TestDeterminism(unittest.TestCase):
    def test_snap_point_input_order_does_not_change_the_plan(self) -> None:
        grid = make_grid([30.0 * k for k in range(9)], rate=60, downbeat_every=4)
        ideals = [RationalTime(v, 60) for v in (0, 61, 121, 181)]
        snaps = [
            SnapPoint(RationalTime(1, 60), "shot_boundary"),
            SnapPoint(RationalTime(60, 60), "motion_onset"),
            SnapPoint(RationalTime(122, 60), "stabilised"),
            SnapPoint(RationalTime(180, 60), "motion_onset"),
        ]
        forward = plan_cuts(grid, ideals, rate=60, snap_points=snaps)
        reversed_order = plan_cuts(grid, ideals, rate=60, snap_points=list(reversed(snaps)))
        self.assertEqual(forward, reversed_order)

    def test_two_snap_points_on_one_frame_resolve_stably(self) -> None:
        grid = make_grid([100.0], rate=100, downbeats={0})
        ideal = RationalTime(102, 100)
        both = [
            SnapPoint(RationalTime(102, 100), "motion_onset"),
            SnapPoint(RationalTime(102, 100), "shot_boundary"),
        ]
        first = lock_cut(grid, ideal, rate=100, snap_points=both)
        second = lock_cut(grid, ideal, rate=100, snap_points=list(reversed(both)))
        self.assertEqual(first, second)
        # Tie broken on the kind string, not on argument order.
        self.assertEqual(first.snap_point_kind, "motion_onset")

    def test_repeated_planning_is_identical(self) -> None:
        data = json.loads(FIXTURE.read_text(encoding="utf-8"))
        grid = BeatGrid.from_contract(data["beat_grid"])
        ideals = [RationalTime(v, data["rate"]) for v in (0, 112, 225, 337, 450)]
        runs = [plan_cuts(grid, ideals, rate=data["rate"]) for _ in range(5)]
        for run in runs[1:]:
            self.assertEqual(run, runs[0])


# ---------------------------------------------------------------------------
# auditing someone else's plan
# ---------------------------------------------------------------------------


class TestAudit(unittest.TestCase):
    def setUp(self) -> None:
        self.grid = make_grid([100.4], rate=60, downbeats={0})
        self.cut = RationalTime(100, 60)

    def test_clean_lock(self) -> None:
        lock = {
            "beat_index": 0,
            "is_downbeat": True,
            "alignment_error_ms": alignment_error_ms(self.grid, 0, self.cut),
            "snap_point_kind": None,
        }
        self.assertEqual(audit_lock(self.grid, self.cut, lock), ())

    def test_declared_error_that_does_not_match_the_grid_is_caught(self) -> None:
        # The signature failure: a planner that reports 0.0 because it thinks
        # rounding to the frame put it on the beat.
        lock = {"beat_index": 0, "is_downbeat": True, "alignment_error_ms": 0.0}
        violations = audit_lock(self.grid, self.cut, lock)
        self.assertEqual(len(violations), 1)
        self.assertIn("alignment_error_ms", violations[0])

    def test_out_of_range_index(self) -> None:
        lock = {"beat_index": 9, "is_downbeat": True, "alignment_error_ms": 0.0}
        self.assertIn("out of range", audit_lock(self.grid, self.cut, lock)[0])

    def test_non_integer_index(self) -> None:
        self.assertTrue(audit_lock(self.grid, self.cut, {"beat_index": "0"}))
        self.assertTrue(audit_lock(self.grid, self.cut, {"beat_index": True}))

    def test_downbeat_flag_mismatch_is_caught(self) -> None:
        # A lock that claims a downbeat it did not land on would pass the
        # loose gate while the render lands on an off-beat.
        lock = {
            "beat_index": 0,
            "is_downbeat": False,
            "alignment_error_ms": alignment_error_ms(self.grid, 0, self.cut),
        }
        violations = audit_lock(self.grid, self.cut, lock)
        self.assertTrue(any("is_downbeat" in v for v in violations))

    def test_over_gate_lock_is_caught_even_when_self_consistent(self) -> None:
        # Honest about its own error, but 100ms is not beat-locked.
        far = make_grid([2.4], rate=4, downbeats={0})
        cut = RationalTime(2, 4)
        lock = {
            "beat_index": 0,
            "is_downbeat": True,
            "alignment_error_ms": alignment_error_ms(far, 0, cut),
        }
        violations = audit_lock(far, cut, lock)
        self.assertEqual(len(violations), 1)
        self.assertIn("gate", violations[0])

    def test_missing_error_field(self) -> None:
        lock = {"beat_index": 0, "is_downbeat": True}
        self.assertTrue(any("must be a number" in v for v in audit_lock(self.grid, self.cut, lock)))


class TestPolicyValidation(unittest.TestCase):
    def test_negative_budgets_rejected(self) -> None:
        with self.assertRaises(ValueError):
            LockPolicy(max_content_drift_ms=-1.0)
        with self.assertRaises(ValueError):
            LockPolicy(downbeat_bonus_ms=-1.0)
        with self.assertRaises(ValueError):
            LockPolicy(snap_bonus_ms=-1.0)
        with self.assertRaises(ValueError):
            LockPolicy(alignment_weight=-1.0)
        with self.assertRaises(ValueError):
            LockPolicy(tolerance_ms=-1.0)

    def test_out_of_range_confidences_rejected(self) -> None:
        with self.assertRaises(ValueError):
            LockPolicy(min_bpm_confidence=1.5)
        with self.assertRaises(ValueError):
            LockPolicy(min_beat_strength=-0.1)

    def test_min_clip_frames_floor(self) -> None:
        with self.assertRaises(ValueError):
            LockPolicy(min_clip_frames=0)

    def test_snap_point_requires_a_kind(self) -> None:
        with self.assertRaises(ValueError):
            SnapPoint(RationalTime(0, 60), "")

    def test_lock_cut_rejects_a_bad_rate(self) -> None:
        grid = make_grid([0.0], rate=60, downbeats={0})
        with self.assertRaises(ValueError):
            lock_cut(grid, RationalTime(0, 60), rate=0)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
