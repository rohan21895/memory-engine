"""Event-clustering tests.

Written against the assumption that the first implementation is wrong in a way
that does not raise. The properties that get the most tests are the ones whose
violation produces a plausible-looking answer:

* **Nothing is lost and nothing is invented.** Every input id appears in exactly
  one cluster. A photo silently vanishing from the library is invisible in any
  output; so is one silently landing in the wrong event.
* **Undated items do not join events by accident.** The whole point of the
  precision field is that an unusable date stays unusable.
* **NaN.** `nan > threshold` is False, so a NaN gap merges two events without
  raising. Every numeric entry point is tested for it.
* **Determinism.** Shuffled input, identical output, ids included.

Where a test asserts a threshold behaviour it also asserts the behaviour just
the other side of the threshold, so a change that moves a constant fails rather
than silently loosening.
"""

from __future__ import annotations

import itertools
import json
import math
import random
import re
import sys
import unittest
import uuid
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = PACKAGE_ROOT.parent.parent
sys.path.insert(0, str(PACKAGE_ROOT))

from memory_engine_album.clustering import (  # noqa: E402
    DAY,
    DEFAULT_GAP_S,
    HOUR,
    LOCATION_SPLIT_KM,
    MAX_EVENT_SPAN_S,
    MAX_GAP_S,
    MIN_GAP_S,
    EventCluster,
    EventItem,
    adaptive_gap_threshold,
    cluster_events,
    coordinate_of,
    haversine_km,
    undated_reason,
)

# A plausible instant: 2024-01-10T00:00:00Z.
T0 = 1704844800.0


def mid(name: str) -> str:
    """A media_id shaped like the BLAKE3 hex the contract uses.

    Derived from the name, not from hash(): PYTHONHASHSEED is randomised per
    process, and an id that changes between runs would make every determinism
    assertion here test the wrong thing.
    """
    return name.encode("utf-8").hex().ljust(64, "0")[:64]


def item(name: str, at: float | None = None, **kwargs) -> EventItem:
    return EventItem(media_id=mid(name), captured_at=at, **kwargs)


def unit(*components: float) -> tuple[float, ...]:
    """L2-normalise, because the module refuses vectors that are not."""
    norm = math.sqrt(sum(c * c for c in components))
    return tuple(c / norm for c in components)


def all_ids(clusters: list[EventCluster]) -> list[str]:
    return sorted(itertools.chain.from_iterable(c.media_ids for c in clusters))


class TestConservation(unittest.TestCase):
    """The invariant every other test leans on."""

    LIBRARY = [
        item("a", T0, latitude=28.61, longitude=77.20),
        item("b", T0 + 600),
        item("c", T0 + 40 * DAY),
        item("d", None),
        item("e", T0 + 40 * DAY + 900, time_precision="day"),
        item("f", T0, time_precision="year"),
        item("g", T0 + 80 * DAY, time_confidence=0.1),
    ]

    def test_every_item_lands_in_exactly_one_cluster(self):
        clusters = cluster_events(self.LIBRARY)
        self.assertEqual(sorted(i.media_id for i in self.LIBRARY), all_ids(clusters))

    def test_clusters_are_disjoint(self):
        clusters = cluster_events(self.LIBRARY)
        flat = list(itertools.chain.from_iterable(c.media_ids for c in clusters))
        self.assertEqual(len(flat), len(set(flat)))

    def test_empty_library(self):
        self.assertEqual([], cluster_events([]))

    def test_a_single_item_is_one_cluster(self):
        clusters = cluster_events([item("a", T0)])
        self.assertEqual(1, len(clusters))
        self.assertEqual(1, clusters[0].size)
        self.assertEqual(T0, clusters[0].start_utc)
        self.assertEqual(T0, clusters[0].end_utc)


class TestDeterminism(unittest.TestCase):
    LIBRARY = [
        item("a", T0),
        item("b", T0 + 300),
        item("c", T0 + 30 * DAY, latitude=13.75, longitude=100.50),
        item("d", T0 + 30 * DAY + 60, latitude=13.75, longitude=100.50),
        item("e", None, embedding=unit(1.0, 0.0, 0.0)),
        item("f", T0 + 90 * DAY, time_precision="hour"),
    ]

    def _signature(self, clusters: list[EventCluster]):
        return [(c.cluster_id, c.media_ids, c.kind, c.start_utc, c.end_utc) for c in clusters]

    def test_input_order_does_not_change_the_result(self):
        expected = self._signature(cluster_events(self.LIBRARY))
        rng = random.Random(20240110)
        for _ in range(25):
            shuffled = list(self.LIBRARY)
            rng.shuffle(shuffled)
            self.assertEqual(expected, self._signature(cluster_events(shuffled)))

    def test_repeated_runs_agree(self):
        self.assertEqual(
            self._signature(cluster_events(self.LIBRARY)),
            self._signature(cluster_events(self.LIBRARY)),
        )

    def test_cluster_id_is_uuid5_over_sorted_membership(self):
        """Recomputed independently. An id derived from iteration order would
        still look like a uuid and would still be stable within a run."""
        for cluster in cluster_events(self.LIBRARY):
            expected = str(
                uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    "memory-engine/album/event/" + ",".join(sorted(cluster.media_ids)),
                )
            )
            self.assertEqual(expected, cluster.cluster_id)

    def test_cluster_id_satisfies_the_contract_uuid_pattern(self):
        """It is written into AlbumSpec.event.event_cluster_id, which is a Uuid."""
        common = json.loads(
            (REPO_ROOT / "contracts/schemas/common.schema.json").read_text(encoding="utf-8")
        )
        pattern = re.compile(common["$defs"]["Uuid"]["pattern"])
        for cluster in cluster_events(self.LIBRARY):
            self.assertRegex(cluster.cluster_id, pattern)

    def test_dated_members_are_in_capture_order(self):
        times = {i.media_id: i.captured_at for i in self.LIBRARY}
        for cluster in cluster_events(self.LIBRARY):
            if cluster.kind != "dated":
                continue
            ordered = [
                times[m] for m in cluster.media_ids if m not in cluster.undated_media_ids
            ]
            self.assertEqual(sorted(ordered), ordered)
            self.assertEqual(ordered[0], cluster.start_utc)
            self.assertEqual(ordered[-1], cluster.end_utc)

    def test_the_id_does_not_depend_on_chronological_order(self):
        """`z` is captured first but sorts last. If the id were built from
        membership in the order it was emitted, it would still be a valid uuid
        and still be stable within a run -- the failure would only show up when
        someone re-ran the pass with the items arriving differently."""
        clusters = cluster_events([item("z", T0), item("a", T0 + 60)])
        self.assertEqual(1, len(clusters))
        self.assertEqual((mid("z"), mid("a")), clusters[0].media_ids)
        self.assertEqual(
            str(
                uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    "memory-engine/album/event/" + ",".join([mid("a"), mid("z")]),
                )
            ),
            clusters[0].cluster_id,
        )

    def test_items_at_the_same_instant_are_ordered_by_media_id(self):
        """Bursts and Live Photos share a capture second constantly. Python's
        sort is stable, so without an explicit tiebreak the member order -- and
        therefore nothing else, but also the cluster's own reported order --
        would follow whatever order the caller passed."""
        forward = cluster_events([item("b", T0), item("a", T0)])
        backward = cluster_events([item("a", T0), item("b", T0)])
        self.assertEqual((mid("a"), mid("b")), forward[0].media_ids)
        self.assertEqual(forward[0].media_ids, backward[0].media_ids)

    def test_clusters_are_returned_in_chronological_order(self):
        clusters = [c for c in cluster_events(self.LIBRARY) if c.kind == "dated"]
        starts = [c.start_utc for c in clusters]
        self.assertEqual(sorted(starts), starts)


class TestTimeGapSplitting(unittest.TestCase):
    def test_a_burst_is_one_event(self):
        burst = [item(f"i{n}", T0 + n * 3.0) for n in range(20)]
        clusters = cluster_events(burst)
        self.assertEqual(1, len(clusters))

    def test_two_trips_weeks_apart_are_two_events(self):
        library = [item(f"a{n}", T0 + n * 600.0) for n in range(5)]
        library += [item(f"b{n}", T0 + 30 * DAY + n * 600.0) for n in range(5)]
        clusters = cluster_events(library)
        self.assertEqual(2, len(clusters))
        self.assertEqual(5, clusters[0].size)
        self.assertEqual(("time_gap",), clusters[1].boundary_reasons)

    def test_an_overnight_gap_does_not_end_an_event(self):
        """Evening of day one to late morning of day two is 14h. If that split,
        every day of a two-week trip would become its own album."""
        library = [item("evening", T0), item("morning", T0 + 14 * HOUR)]
        self.assertEqual(1, len(cluster_events(library)))

    def test_a_full_day_of_nothing_does_end_an_event(self):
        """The other side of the same threshold, so moving DEFAULT_GAP_S fails
        a test instead of quietly changing the product."""
        library = [item("before", T0), item("after", T0 + 26 * HOUR)]
        self.assertEqual(2, len(cluster_events(library)))

    def test_the_boundary_is_strict(self):
        """+2s and -1s around the threshold, because second-precision claims
        each carry half a second of uncertainty and the split test uses the
        conservative gap."""
        just_over = [item("a", T0), item("b", T0 + DEFAULT_GAP_S + 2.0)]
        self.assertEqual(2, len(cluster_events(just_over)))
        just_under = [item("a", T0), item("b", T0 + DEFAULT_GAP_S - 1.0)]
        self.assertEqual(1, len(cluster_events(just_under)))

    def test_a_pinned_threshold_overrides_derivation(self):
        library = [item("a", T0), item("b", T0 + 8 * HOUR)]
        self.assertEqual(1, len(cluster_events(library)))
        self.assertEqual(2, len(cluster_events(library, gap_threshold_s=4 * HOUR)))

    def test_the_threshold_in_force_is_reported(self):
        clusters = cluster_events([item("a", T0)], gap_threshold_s=7 * HOUR)
        self.assertEqual(7 * HOUR, clusters[0].gap_threshold_s)

    def test_a_nonsense_pinned_threshold_is_rejected(self):
        for bad in (0.0, -1.0, float("nan"), float("inf")):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    cluster_events([item("a", T0)], gap_threshold_s=bad)


class TestAdaptiveThreshold(unittest.TestCase):
    def test_a_bimodal_library_lands_between_the_modes(self):
        gaps = [8 * HOUR] * 12 + [72 * HOUR] * 4
        threshold = adaptive_gap_threshold(gaps)
        self.assertGreater(threshold, 8 * HOUR)
        self.assertLess(threshold, 72 * HOUR)

    def test_the_midpoint_is_geometric_not_arithmetic(self):
        """The two populations are separated multiplicatively, so the middle of
        the empty stretch is the geometric middle. The arithmetic one (40h here
        against 24h) sits far closer to the between-event population and lets
        the shorter real boundaries through -- a merge, and a silent one."""
        gaps = [8 * HOUR] * 12 + [72 * HOUR] * 4
        self.assertAlmostEqual(24 * HOUR, adaptive_gap_threshold(gaps), places=6)
        self.assertNotAlmostEqual(40 * HOUR, adaptive_gap_threshold(gaps), places=0)

    def test_a_continuum_falls_back_to_the_default(self):
        """No step means no boundary in the data. Inventing one from a smooth
        distribution is worse than using a stated constant."""
        gaps = [h * HOUR for h in range(6, 30)]
        self.assertEqual(DEFAULT_GAP_S, adaptive_gap_threshold(gaps))

    def test_too_few_gaps_falls_back(self):
        gaps = [8 * HOUR] * 3 + [72 * HOUR]
        self.assertEqual(DEFAULT_GAP_S, adaptive_gap_threshold(gaps))

    def test_gaps_outside_the_band_do_not_vote(self):
        """Sub-second burst gaps and month-long absences are not in question and
        must not drag the threshold anywhere."""
        gaps = [0.5] * 500 + [90 * DAY] * 20
        self.assertEqual(DEFAULT_GAP_S, adaptive_gap_threshold(gaps))

    def test_the_result_is_clamped_into_the_band(self):
        self.assertEqual(
            MAX_GAP_S, adaptive_gap_threshold([1.0] * 20, default_s=1000 * HOUR)
        )
        self.assertEqual(MIN_GAP_S, adaptive_gap_threshold([1.0] * 20, default_s=1.0))

    def test_zero_and_negative_gaps_are_ignored_not_divided_by(self):
        gaps = [0.0] * 30 + [-5.0] * 3 + [8 * HOUR] * 12 + [72 * HOUR] * 4
        threshold = adaptive_gap_threshold(gaps)
        self.assertGreater(threshold, 8 * HOUR)
        self.assertLess(threshold, 72 * HOUR)

    def test_a_nan_gap_raises(self):
        """It would compare False against every threshold and merge two events."""
        with self.assertRaises(ValueError):
            adaptive_gap_threshold([8 * HOUR] * 12 + [float("nan")])

    def test_an_empty_band_is_rejected(self):
        with self.assertRaises(ValueError):
            adaptive_gap_threshold([1.0], floor_s=10.0, ceiling_s=5.0)

    def test_derivation_beats_the_constant_on_a_slow_library(self):
        """The case that discriminates. Someone photographing a long, slow
        stretch -- one or two frames a day, 22h apart -- has within-event gaps
        ABOVE the 20h default. A constant threshold shreds each event into
        single-photo albums; the derived one sees the knee at 22h/90h and keeps
        them whole.

        Asserted both ways round, so this fails if the derivation quietly stops
        being consulted and the default takes over.
        """
        library: list[EventItem] = []
        clock = T0
        for event_index in range(6):
            for frame in range(4):
                library.append(item(f"e{event_index}f{frame}", clock + frame * 22 * HOUR))
            clock += 3 * 22 * HOUR + 90 * HOUR

        derived = cluster_events(library)
        self.assertEqual(6, len(derived), "the knee sits between 22h and 90h")
        self.assertEqual([4] * 6, [c.size for c in derived])

        constant = cluster_events(library, gap_threshold_s=DEFAULT_GAP_S)
        self.assertEqual(24, len(constant), "the default alone would shred this library")


class TestTimePrecision(unittest.TestCase):
    def test_consecutive_day_precision_photos_stay_together(self):
        """Two WhatsApp forwards from consecutive days both snap to midnight.
        Their nominal gap is 24h; the data cannot support a 24h claim, so it
        cannot support a split either."""
        library = [
            item("mon", T0, time_precision="day"),
            item("tue", T0 + DAY, time_precision="day"),
        ]
        self.assertEqual(1, len(cluster_events(library)))

    def test_day_precision_photos_three_days_apart_do_split(self):
        """The uncertainty is a day wide, not unbounded. Precision must not
        become a licence to merge everything."""
        library = [
            item("mon", T0, time_precision="day"),
            item("thu", T0 + 3 * DAY, time_precision="day"),
        ]
        self.assertEqual(2, len(cluster_events(library)))

    def test_second_precision_photos_a_day_apart_do_split(self):
        """Same nominal gap as the consecutive-day case, different precision,
        different answer. This is the test that proves precision is read."""
        library = [item("mon", T0), item("tue", T0 + DAY)]
        self.assertEqual(2, len(cluster_events(library)))

    def test_month_and_year_precision_are_undated(self):
        for precision in ("month", "year"):
            with self.subTest(precision=precision):
                self.assertEqual(
                    "precision_too_coarse",
                    undated_reason(item("x", T0, time_precision=precision)),
                )

    def test_unknown_precision_is_undated_even_with_a_number(self):
        self.assertEqual(
            "precision_unknown", undated_reason(item("x", T0, time_precision="unknown"))
        )

    def test_no_timestamp_is_undated(self):
        self.assertEqual("no_timestamp", undated_reason(item("x", None)))

    def test_a_low_confidence_claim_is_undated(self):
        self.assertEqual(
            "low_time_confidence", undated_reason(item("x", T0, time_confidence=0.2))
        )
        self.assertIsNone(undated_reason(item("x", T0, time_confidence=0.9)))

    def test_a_coarse_claim_does_not_anchor_a_fake_event(self):
        """A year-precision scan asserted at 1 January must not create a January
        event that later pulls neighbours in."""
        library = [item("scan", T0, time_precision="year")] + [
            item(f"real{n}", T0 + 200 * DAY + n * 60.0) for n in range(4)
        ]
        clusters = cluster_events(library)
        dated = [c for c in clusters if c.kind == "dated"]
        self.assertEqual(1, len(dated))
        self.assertEqual(4, dated[0].size)

    def test_the_threshold_is_derived_in_the_same_unit_it_is_tested_in(self):
        """A WhatsApp-only library: six trips of four consecutive days, two and
        a half days apart, every date good only to the day.

        Nominal gaps are 24h within a trip and 60h between; conservative gaps
        are 0h and 36h, because a day-precision date cannot support a 24h claim.
        Deriving the threshold from the nominal distribution puts it at 37.9h
        and then compares it against conservative gaps of 36h -- so no boundary
        clears it, all six trips fuse, and the only thing that eventually cuts
        the run is the span limit. The numbers all look reasonable; the answer
        is wrong. Derivation and test have to be in the same unit.
        """
        library: list[EventItem] = []
        for trip in range(6):
            start = T0 + trip * 5.5 * DAY
            for day in range(4):
                library.append(
                    item(f"trip{trip}day{day}", start + day * DAY, time_precision="day")
                )
        clusters = cluster_events(library)
        self.assertEqual(6, len(clusters))
        self.assertEqual([4] * 6, [c.size for c in clusters])

    def test_an_unrecognised_precision_is_rejected(self):
        with self.assertRaises(ValueError):
            cluster_events([item("x", T0, time_precision="decade")])


class TestLocation(unittest.TestCase):
    def test_a_place_change_ends_an_event(self):
        library = [
            item("delhi", T0, latitude=28.6139, longitude=77.2090),
            item("bangkok", T0 + 2 * HOUR, latitude=13.7563, longitude=100.5018),
        ]
        clusters = cluster_events(library)
        self.assertEqual(2, len(clusters))
        self.assertEqual(("location_jump",), clusters[1].boundary_reasons)

    def test_a_hotel_room_over_three_days_is_one_event(self):
        """No distance gap at all, and the time gaps are within a day. Location
        must contribute nothing here rather than something."""
        library = [
            item(f"h{n}", T0 + n * 8 * HOUR, latitude=13.7563, longitude=100.5018)
            for n in range(9)
        ]
        self.assertEqual(1, len(cluster_events(library)))

    def test_a_day_out_does_not_end_an_event(self):
        """Tens of km is a normal day. The threshold has to clear it."""
        library = [
            item("home", T0, latitude=28.6139, longitude=77.2090),
            item("out", T0 + 3 * HOUR, latitude=28.4595, longitude=77.0266),
        ]
        self.assertEqual(1, len(cluster_events(library)))

    def test_missing_coordinates_never_split(self):
        """A photo with no GPS is not evidence of anything."""
        library = [
            item("a", T0, latitude=28.6139, longitude=77.2090),
            item("b", T0 + HOUR),
            item("c", T0 + 2 * HOUR, latitude=28.6139, longitude=77.2090),
        ]
        self.assertEqual(1, len(cluster_events(library)))

    def test_half_a_coordinate_is_not_a_coordinate(self):
        """Defaulting the missing half to zero puts the photo on the prime
        meridian and splits the event."""
        self.assertIsNone(coordinate_of(item("a", T0, latitude=28.6139)))
        self.assertIsNone(coordinate_of(item("a", T0, longitude=77.2090)))
        library = [
            item("a", T0, latitude=28.6139, longitude=77.2090),
            item("b", T0 + HOUR, latitude=28.6139),
            item("c", T0 + 2 * HOUR, latitude=28.6139, longitude=77.2090),
        ]
        self.assertEqual(1, len(cluster_events(library)))

    def test_null_island_is_treated_as_no_fix(self):
        """Exactly (0,0) is what a camera writes when it has no fix. Believing
        it manufactures a 6000km jump on every affected file."""
        self.assertIsNone(coordinate_of(item("a", T0, latitude=0.0, longitude=0.0)))
        library = [
            item("a", T0, latitude=28.6139, longitude=77.2090),
            item("b", T0 + HOUR, latitude=0.0, longitude=0.0),
            item("c", T0 + 2 * HOUR, latitude=28.6139, longitude=77.2090),
        ]
        self.assertEqual(1, len(cluster_events(library)))

    def test_a_real_coordinate_near_null_island_is_still_real(self):
        self.assertIsNotNone(coordinate_of(item("a", T0, latitude=0.0, longitude=0.001)))

    def test_an_ungeotagged_run_does_not_hide_a_place_change(self):
        """Comparing only to the immediate predecessor would miss this: the
        photos either side of the move have no GPS."""
        library = [
            item("delhi", T0, latitude=28.6139, longitude=77.2090),
            item("plane1", T0 + HOUR),
            item("plane2", T0 + 2 * HOUR),
            item("bangkok", T0 + 3 * HOUR, latitude=13.7563, longitude=100.5018),
        ]
        clusters = cluster_events(library)
        self.assertEqual(2, len(clusters))
        self.assertEqual(3, clusters[0].size)
        self.assertEqual(1, clusters[1].size)

    def test_location_never_merges_across_a_time_gap(self):
        """Five years of photographs taken at home are not one event, however
        identical their coordinates."""
        library = [
            item("y1", T0, latitude=28.6139, longitude=77.2090),
            item("y2", T0 + 365 * DAY, latitude=28.6139, longitude=77.2090),
        ]
        self.assertEqual(2, len(cluster_events(library)))

    def test_an_out_of_range_coordinate_is_rejected(self):
        with self.assertRaises(ValueError):
            cluster_events([item("a", T0, latitude=91.0, longitude=0.0)])
        with self.assertRaises(ValueError):
            cluster_events([item("a", T0, latitude=0.0, longitude=181.0)])

    def test_a_nan_coordinate_is_rejected(self):
        """Every distance from a NaN coordinate is NaN, and `nan > 150` is
        False, so the place change would silently never be seen."""
        with self.assertRaises(ValueError):
            cluster_events([item("a", T0, latitude=float("nan"), longitude=0.0)])


class TestHaversine(unittest.TestCase):
    def test_zero_distance(self):
        self.assertAlmostEqual(0.0, haversine_km(28.6, 77.2, 28.6, 77.2), places=9)

    def test_one_degree_of_latitude(self):
        self.assertAlmostEqual(111.19, haversine_km(0.0, 0.0, 1.0, 0.0), places=1)

    def test_agrees_with_an_independent_formula(self):
        """Cross-checked against the spherical law of cosines, which is a
        different expression of the same geometry and fails differently."""
        pairs = [
            (28.6139, 77.2090, 51.5074, -0.1278),
            (13.7563, 100.5018, 35.6762, 139.6503),
            (-33.8688, 151.2093, 40.7128, -74.0060),
        ]
        for lat1, lon1, lat2, lon2 in pairs:
            with self.subTest(pair=(lat1, lon1)):
                p1, p2 = math.radians(lat1), math.radians(lat2)
                dl = math.radians(lon2 - lon1)
                cosine = math.sin(p1) * math.sin(p2) + math.cos(p1) * math.cos(p2) * math.cos(dl)
                expected = 6371.0088 * math.acos(min(1.0, max(-1.0, cosine)))
                self.assertAlmostEqual(
                    expected, haversine_km(lat1, lon1, lat2, lon2), delta=0.5
                )

    def test_the_antimeridian_is_not_a_wall(self):
        """179E to 179W is two degrees apart, not 358."""
        near = haversine_km(0.0, 179.0, 0.0, -179.0)
        self.assertLess(near, 250.0)
        self.assertGreater(near, 200.0)

    def test_antipodal_points_do_not_raise(self):
        self.assertAlmostEqual(math.pi * 6371.0088, haversine_km(0.0, 0.0, 0.0, 180.0), delta=1.0)
        self.assertAlmostEqual(math.pi * 6371.0088, haversine_km(-90.0, 0.0, 90.0, 0.0), delta=1.0)

    # Found by search over near-antipodal pairs, not by reasoning. The
    # haversine term here evaluates to 1.0000000000000004 -- two ulps above 1,
    # which is the smallest excess that survives the sqrt (one ulp rounds back
    # to exactly 1.0 and never reaches asin). The tidy antipodes above,
    # (0,0)/(0,180), land on exactly 1.0 and do not exercise the clamp at all,
    # which is why they are not sufficient on their own.
    OVERFLOWING_ANTIPODES = (
        -59.32845628596557,
        60.01201955549453,
        59.32845628596547,
        -119.98798044450547,
    )

    def test_the_asin_argument_is_clamped(self):
        lat1, lon1, lat2, lon2 = self.OVERFLOWING_ANTIPODES
        phi1, phi2 = math.radians(lat1), math.radians(lat2)
        naive = (
            math.sin((phi2 - phi1) / 2.0) ** 2
            + math.cos(phi1) * math.cos(phi2) * math.sin(math.radians(lon2 - lon1) / 2.0) ** 2
        )
        # Guarding the guard: if a future libm makes this land at or below 1.0
        # the test below stops testing anything, and would pass forever.
        self.assertGreater(math.sqrt(naive), 1.0, "this pair no longer overflows; find another")
        with self.assertRaises(ValueError):
            math.asin(math.sqrt(naive))
        self.assertAlmostEqual(
            math.pi * 6371.0088, haversine_km(lat1, lon1, lat2, lon2), delta=1.0
        )

    def test_the_split_threshold_is_where_it_says_it_is(self):
        just_over = haversine_km(0.0, 0.0, LOCATION_SPLIT_KM * 1.1 / 111.19, 0.0)
        self.assertGreater(just_over, LOCATION_SPLIT_KM)


class TestTimezones(unittest.TestCase):
    """A trip crosses zones, and wall clocks jump. The module clusters on
    instants precisely so this class of bug is unreachable."""

    # Tokyo 17:00 JST (+09:00) departure, Los Angeles 10:00 PST (-08:00)
    # arrival the same calendar day. Ten hours in the air, seven hours BACKWARDS
    # on the wall clock.
    DEPART = T0 + 8 * HOUR
    ARRIVE = T0 + 18 * HOUR
    LIBRARY = [
        EventItem(mid("tokyo"), DEPART, utc_offset_minutes=540),
        EventItem(mid("la"), ARRIVE, utc_offset_minutes=-480),
    ]

    def test_the_wall_clock_really_does_go_backwards(self):
        """Guarding the guard: if this stops being true the test below stops
        testing anything."""
        wall = [i.captured_at + i.utc_offset_minutes * 60.0 for i in self.LIBRARY]
        self.assertLess(wall[1] - wall[0], 0.0)

    def test_the_instant_timeline_stays_ordered(self):
        clusters = cluster_events(self.LIBRARY)
        self.assertEqual(1, len(clusters))
        self.assertEqual((mid("tokyo"), mid("la")), clusters[0].media_ids)
        self.assertLess(clusters[0].start_utc, clusters[0].end_utc)

    def test_a_crossed_zone_is_visible_on_the_cluster(self):
        clusters = cluster_events(self.LIBRARY)
        self.assertEqual((-480, 540), clusters[0].utc_offsets)

    def test_a_single_zone_library_reports_one_offset(self):
        library = [
            EventItem(mid("a"), T0, utc_offset_minutes=330),
            EventItem(mid("b"), T0 + 600, utc_offset_minutes=330),
        ]
        self.assertEqual((330,), cluster_events(library)[0].utc_offsets)

    def test_items_with_no_recorded_offset_report_none(self):
        self.assertEqual((), cluster_events([item("a", T0)])[0].utc_offsets)

    def test_an_impossible_offset_is_rejected(self):
        with self.assertRaises(ValueError):
            cluster_events([EventItem(mid("a"), T0, utc_offset_minutes=2000)])


class TestUndatedItems(unittest.TestCase):
    """Assigned by appearance or bucketed, never inserted into whatever event
    happened to be adjacent."""

    def _two_events(self, embedding_a, embedding_b):
        beach = [item(f"beach{n}", T0 + n * 600.0, embedding=embedding_a) for n in range(3)]
        party = [
            item(f"party{n}", T0 + 60 * DAY + n * 600.0, embedding=embedding_b) for n in range(3)
        ]
        return beach + party

    def test_a_strong_match_joins_its_event(self):
        library = self._two_events(unit(1.0, 0.0, 0.0), unit(0.0, 1.0, 0.0))
        library.append(item("scan", None, embedding=unit(0.99, 0.141067, 0.0)))
        clusters = cluster_events(library)
        self.assertEqual(2, len(clusters))
        self.assertIn(mid("scan"), clusters[0].media_ids)
        self.assertEqual((mid("scan"),), clusters[0].undated_media_ids)

    def test_an_ambiguous_match_goes_to_the_bucket(self):
        """Equally similar to two events. Placing it would look like a decision
        and be a coin toss."""
        library = self._two_events(unit(1.0, 0.0, 0.0), unit(1.0, 0.0, 0.0))
        library.append(item("scan", None, embedding=unit(1.0, 0.0, 0.0)))
        clusters = cluster_events(library)
        undated = [c for c in clusters if c.kind == "undated"]
        self.assertEqual(1, len(undated))
        self.assertEqual((mid("scan"),), undated[0].media_ids)

    def test_a_weak_match_goes_to_the_bucket(self):
        library = self._two_events(unit(1.0, 0.0, 0.0), unit(0.0, 1.0, 0.0))
        library.append(item("document", None, embedding=unit(0.0, 0.0, 1.0)))
        clusters = cluster_events(library)
        self.assertEqual(
            [mid("document")], [i for c in clusters if c.kind == "undated" for i in c.media_ids]
        )

    def test_a_weak_but_unambiguous_match_still_goes_to_the_bucket(self):
        """The margin alone is not enough. This scan is more like the beach
        event than the party -- unambiguously so -- and still nothing like
        either. Without the absolute threshold it would be filed under 'beach'
        on the strength of a 0.30 cosine, which is a decision made out of
        nothing."""
        library = self._two_events(unit(1.0, 0.0, 0.0), unit(0.0, 1.0, 0.0))
        library.append(item("scan", None, embedding=unit(0.3, 0.0, 0.95)))
        clusters = cluster_events(library)
        self.assertEqual(
            [mid("scan")], [i for c in clusters if c.kind == "undated" for i in c.media_ids]
        )

    def test_an_undated_item_with_no_embedding_goes_to_the_bucket(self):
        """There is nothing to decide with, so there is no decision to make."""
        library = self._two_events(unit(1.0, 0.0, 0.0), unit(0.0, 1.0, 0.0))
        library.append(item("nothing", None))
        clusters = cluster_events(library)
        self.assertEqual(
            [mid("nothing")], [i for c in clusters if c.kind == "undated" for i in c.media_ids]
        )

    def test_an_assigned_item_does_not_move_the_event_dates(self):
        """It has no position on the timeline. Letting it set one would be a
        fabricated chronology that every later step trusts."""
        library = self._two_events(unit(1.0, 0.0, 0.0), unit(0.0, 1.0, 0.0))
        before = cluster_events(library)[0]
        library.append(item("scan", None, embedding=unit(0.99, 0.141067, 0.0)))
        after = cluster_events(library)[0]
        self.assertEqual((before.start_utc, before.end_utc), (after.start_utc, after.end_utc))

    def test_assigned_items_are_listed_separately(self):
        library = self._two_events(unit(1.0, 0.0, 0.0), unit(0.0, 1.0, 0.0))
        library.append(item("scan", None, embedding=unit(0.99, 0.141067, 0.0)))
        cluster = cluster_events(library)[0]
        self.assertTrue(set(cluster.undated_media_ids).issubset(set(cluster.media_ids)))
        self.assertEqual(cluster.media_ids[-1], mid("scan"), "not interleaved into the timeline")

    def test_the_undated_bucket_has_no_date_range(self):
        """AlbumSpec.event.date_range is nullable for exactly this case."""
        clusters = cluster_events([item("a", None), item("b", None)])
        self.assertEqual(1, len(clusters))
        self.assertEqual("undated", clusters[0].kind)
        self.assertIsNone(clusters[0].start_utc)
        self.assertIsNone(clusters[0].end_utc)

    def test_a_library_with_no_dated_items_still_returns_everything(self):
        library = [item(f"u{n}", None, embedding=unit(1.0, 0.0, 0.0)) for n in range(5)]
        clusters = cluster_events(library)
        self.assertEqual(sorted(i.media_id for i in library), all_ids(clusters))

    def test_no_undated_bucket_is_emitted_when_there_is_nothing_in_it(self):
        clusters = cluster_events([item("a", T0), item("b", T0 + 60)])
        self.assertEqual([], [c for c in clusters if c.kind == "undated"])

    def test_assignment_is_independent_of_input_order(self):
        library = self._two_events(unit(1.0, 0.0, 0.0), unit(0.0, 1.0, 0.0))
        library.append(item("scan", None, embedding=unit(0.99, 0.141067, 0.0)))
        rng = random.Random(7)
        expected = [(c.cluster_id, c.media_ids) for c in cluster_events(library)]
        for _ in range(20):
            shuffled = list(library)
            rng.shuffle(shuffled)
            self.assertEqual(expected, [(c.cluster_id, c.media_ids) for c in cluster_events(shuffled)])


class TestMaxSpan(unittest.TestCase):
    def test_a_two_week_trip_survives_whole(self):
        library = [item(f"t{n}", T0 + n * 12 * HOUR) for n in range(28)]
        clusters = cluster_events(library)
        self.assertEqual(1, len(clusters), "14 days is under the span limit")

    def test_a_never_quiet_library_is_still_cut(self):
        """One photo every twelve hours for two months. No gap anywhere exceeds
        the threshold, so without a span limit this is a single 60-day event --
        the worst possible output, produced in silence."""
        library = [item(f"t{n}", T0 + n * 12 * HOUR) for n in range(120)]
        clusters = cluster_events(library)
        self.assertGreater(len(clusters), 1)
        for cluster in clusters:
            self.assertLessEqual(cluster.end_utc - cluster.start_utc, MAX_EVENT_SPAN_S)

    def test_the_cut_is_balanced_not_a_peel(self):
        """Every interior gap is identical here. Breaking the tie on index alone
        peels one photo off the front per pass and turns two months into seventy
        single-photo albums; nothing raises."""
        library = [item(f"t{n}", T0 + n * 12 * HOUR) for n in range(120)]
        clusters = cluster_events(library)
        self.assertLess(len(clusters), 8)
        self.assertTrue(all(c.size > 1 for c in clusters))

    def test_a_forced_cut_is_labelled(self):
        library = [item(f"t{n}", T0 + n * 12 * HOUR) for n in range(120)]
        clusters = cluster_events(library)
        self.assertIn("max_span", clusters[1].boundary_reasons)

    def test_a_run_with_no_quiet_moment_is_left_intact(self):
        """Every interior gap is inside the precision of the claims, so there is
        no honest place to cut. Leaving it over-long and visible beats cutting
        at an arbitrary index."""
        library = [item(f"t{n}", T0 + n * DAY, time_precision="day") for n in range(60)]
        clusters = cluster_events(library)
        self.assertEqual(1, len(clusters))
        self.assertGreater(clusters[0].end_utc - clusters[0].start_utc, MAX_EVENT_SPAN_S)

    def test_the_span_limit_is_configurable(self):
        library = [item(f"t{n}", T0 + n * 12 * HOUR) for n in range(28)]
        self.assertGreater(len(cluster_events(library, max_event_span_s=3 * DAY)), 3)

    def test_a_nonsense_span_is_rejected(self):
        with self.assertRaises(ValueError):
            cluster_events([item("a", T0)], max_event_span_s=0.0)


class TestInputValidation(unittest.TestCase):
    def test_a_duplicate_media_id_is_rejected(self):
        with self.assertRaises(ValueError):
            cluster_events([item("a", T0), item("a", T0 + 60)])

    def test_an_empty_media_id_is_rejected(self):
        with self.assertRaises(ValueError):
            cluster_events([EventItem("", T0)])

    def test_a_nan_timestamp_is_rejected(self):
        """`nan > threshold` is False, so a NaN capture time would sail through
        every split test and merge two events without raising anything."""
        with self.assertRaises(ValueError):
            cluster_events([item("a", float("nan")), item("b", T0)])

    def test_an_infinite_timestamp_is_rejected(self):
        with self.assertRaises(ValueError):
            cluster_events([item("a", float("inf")), item("b", T0)])

    def test_an_out_of_range_confidence_is_rejected(self):
        with self.assertRaises(ValueError):
            cluster_events([item("a", T0, time_confidence=1.5)])

    def test_an_unnormalised_embedding_is_rejected(self):
        """A dot product on an unnormalised vector is not a cosine, and it still
        returns a number in a believable range."""
        with self.assertRaises(ValueError):
            cluster_events([item("a", T0, embedding=(2.0, 0.0, 0.0))])
        with self.assertRaises(ValueError):
            cluster_events([item("a", T0, embedding=(0.5, 0.0, 0.0))])

    def test_a_zero_vector_is_rejected(self):
        with self.assertRaises(ValueError):
            cluster_events([item("a", T0, embedding=(0.0, 0.0, 0.0))])

    def test_a_nan_embedding_component_is_rejected(self):
        with self.assertRaises(ValueError):
            cluster_events([item("a", T0, embedding=(float("nan"), 0.0, 0.0))])

    def test_mismatched_embedding_dimensions_are_rejected(self):
        """Vectors from different spaces are not comparable, and zip() would
        silently compare the shorter prefix."""
        with self.assertRaises(ValueError):
            cluster_events(
                [
                    item("a", T0, embedding=unit(1.0, 0.0, 0.0)),
                    item("b", T0 + 60, embedding=unit(1.0, 0.0, 0.0, 0.0)),
                ]
            )

    def test_an_empty_embedding_is_rejected(self):
        with self.assertRaises(ValueError):
            cluster_events([item("a", T0, embedding=())])


class TestRealisticLibrary(unittest.TestCase):
    """An end-to-end shape check on something resembling a real year."""

    def _library(self) -> list[EventItem]:
        library: list[EventItem] = []

        # Diwali at home: one evening, two bursts, Delhi.
        for n in range(30):
            library.append(
                item(f"diwali{n}", T0 + n * 90.0, latitude=28.6139, longitude=77.2090,
                     utc_offset_minutes=330)
            )

        # Thailand trip: ten days, photographed every few hours, Bangkok.
        trip_start = T0 + 45 * DAY
        for n in range(60):
            library.append(
                item(f"thai{n}", trip_start + n * 4 * HOUR, latitude=13.7563,
                     longitude=100.5018, utc_offset_minutes=420)
            )

        # A birthday six months later, back home, no GPS on the phone that day.
        birthday = T0 + 220 * DAY
        for n in range(20):
            library.append(item(f"bday{n}", birthday + n * 300.0, utc_offset_minutes=330))

        # Scans with no date at all.
        library.append(item("scan1", None))
        library.append(item("scan2", None, time_precision="year", time_confidence=1.0))

        return library

    def test_the_three_events_come_out_as_three(self):
        clusters = cluster_events(self._library())
        dated = [c for c in clusters if c.kind == "dated"]
        self.assertEqual(3, len(dated))
        self.assertEqual([30, 60, 20], [c.size for c in dated])

    def test_the_undated_scans_are_bucketed_not_absorbed(self):
        clusters = cluster_events(self._library())
        undated = [c for c in clusters if c.kind == "undated"]
        self.assertEqual(1, len(undated))
        self.assertEqual({mid("scan1"), mid("scan2")}, set(undated[0].media_ids))

    def test_nothing_is_lost(self):
        library = self._library()
        clusters = cluster_events(library)
        self.assertEqual(sorted(i.media_id for i in library), all_ids(clusters))

    def test_the_result_is_stable_under_shuffling(self):
        library = self._library()
        rng = random.Random(99)
        expected = [(c.cluster_id, c.media_ids) for c in cluster_events(library)]
        for _ in range(10):
            rng.shuffle(library)
            self.assertEqual(expected, [(c.cluster_id, c.media_ids) for c in cluster_events(library)])


class TestSinglePassInputs(unittest.TestCase):
    """A generator is the natural way to call this and must not be a trap.

    `cluster_events` reads its input twice (validate, then partition) and
    `adaptive_gap_threshold` reads its input twice (finiteness, then sort). An
    exhausted iterator does not raise: the second pass sees an empty sequence,
    so the library comes back as no clusters at all and the distribution comes
    back as the default. Both are total, silent failures at a call site --
    `cluster_events(to_item(r) for r in records)` -- that reads correctly.
    """

    LIBRARY = [
        item("a", T0),
        item("b", T0 + 600),
        item("c", T0 + 40 * DAY),
        item("d", None),
    ]

    def test_a_generator_library_produces_the_same_clusters_as_a_list(self):
        from_list = cluster_events(self.LIBRARY)
        from_generator = cluster_events(i for i in self.LIBRARY)
        self.assertEqual(
            [(c.cluster_id, c.media_ids) for c in from_list],
            [(c.cluster_id, c.media_ids) for c in from_generator],
        )
        self.assertEqual(sorted(i.media_id for i in self.LIBRARY), all_ids(from_generator))

    def test_a_generator_library_is_still_validated(self):
        """The validation pass must see the items, not an empty iterator."""
        with self.assertRaises(ValueError):
            cluster_events(i for i in [item("a", T0), item("a", T0 + 60)])

    def test_a_generator_gap_distribution_derives_the_same_threshold(self):
        gaps = [8 * HOUR] * 12 + [72 * HOUR] * 4
        self.assertEqual(
            adaptive_gap_threshold(gaps), adaptive_gap_threshold(g for g in gaps)
        )
        self.assertNotEqual(
            DEFAULT_GAP_S,
            adaptive_gap_threshold(g for g in gaps),
            "an exhausted iterator would fall back to the default and look fine",
        )

    def test_a_generator_gap_distribution_is_still_checked_for_nan(self):
        with self.assertRaises(ValueError):
            adaptive_gap_threshold(g for g in [8 * HOUR] * 12 + [float("nan")])


class TestParameterValidation(unittest.TestCase):
    """NaN is not caught by a `<= 0` test, and it is the value that matters.

    Every threshold in the module is applied with `<` or `>`, and NaN loses
    every one of those comparisons. A NaN radius or span limit does not
    misbehave loudly: it switches that signal off entirely and the library comes
    back under-split, which is the failure mode the whole module is tuned
    against and the one nothing downstream reports.
    """

    LIBRARY = [
        item("delhi", T0, latitude=28.6139, longitude=77.2090),
        item("bangkok", T0 + 2 * HOUR, latitude=13.7563, longitude=100.5018),
    ]

    def test_a_nan_location_radius_is_rejected(self):
        with self.assertRaises(ValueError):
            cluster_events(self.LIBRARY, location_split_km=float("nan"))

    def test_a_nan_span_limit_is_rejected(self):
        with self.assertRaises(ValueError):
            cluster_events(self.LIBRARY, max_event_span_s=float("nan"))

    def test_an_infinite_location_radius_is_rejected(self):
        """Not a harmless "never split": it is an unstated policy change, and
        the caller that passed it thinks it passed a distance."""
        with self.assertRaises(ValueError):
            cluster_events(self.LIBRARY, location_split_km=float("inf"))

    def test_an_infinite_span_limit_is_rejected(self):
        with self.assertRaises(ValueError):
            cluster_events(self.LIBRARY, max_event_span_s=float("inf"))

    def test_a_nan_similarity_threshold_is_rejected(self):
        """The quietest of the lot: every `score >= nan` is False, so the whole
        library's undated items fall into the bucket and the pass looks like it
        simply found no matches."""
        with self.assertRaises(ValueError):
            cluster_events(self.LIBRARY, undated_similarity_threshold=float("nan"))
        with self.assertRaises(ValueError):
            cluster_events(self.LIBRARY, undated_similarity_margin=float("nan"))

    def test_a_similarity_threshold_outside_the_cosine_range_is_rejected(self):
        """These are cosines of unit vectors. A threshold of 2.0 is not a strict
        setting, it is an unreachable one, and a caller that passes it has a
        units bug that would otherwise show up as an empty result."""
        with self.assertRaises(ValueError):
            cluster_events(self.LIBRARY, undated_similarity_threshold=2.0)
        with self.assertRaises(ValueError):
            cluster_events(self.LIBRARY, undated_similarity_threshold=-1.5)
        with self.assertRaises(ValueError):
            cluster_events(self.LIBRARY, undated_similarity_margin=-0.1)
        with self.assertRaises(ValueError):
            cluster_events(self.LIBRARY, undated_similarity_margin=2.5)

    def test_the_extreme_ends_of_the_cosine_range_are_allowed(self):
        """The bounds are the geometry, not a policy: a caller may legitimately
        pin the threshold to 1.0 (identical vectors only) or 0.0 margin."""
        cluster_events(self.LIBRARY, undated_similarity_threshold=1.0)
        cluster_events(self.LIBRARY, undated_similarity_threshold=-1.0)
        cluster_events(self.LIBRARY, undated_similarity_margin=0.0)
        cluster_events(self.LIBRARY, undated_similarity_margin=2.0)

    def test_a_nan_radius_would_otherwise_disable_the_signal_silently(self):
        """Guarding the guard: this is what the rejection above prevents. If the
        check were removed, this library -- Delhi to Bangkok in two hours --
        would come back as one event with no error anywhere."""
        self.assertEqual(2, len(cluster_events(self.LIBRARY)))
        self.assertFalse(2900.0 > float("nan"), "nan loses every comparison")


class TestThresholdStrictness(unittest.TestCase):
    """Which side of each threshold is inclusive.

    All three are compared with a strict operator, so the documented value is
    the last one that still holds an event together. The distinction is
    invisible on random data and constant on real data: callers pin these to
    round numbers, and timer-driven and day-precision libraries produce gaps
    that land exactly on round numbers.
    """

    def test_a_gap_exactly_on_the_threshold_holds_the_event_together(self):
        # Two second-precision claims each carry half a second of uncertainty,
        # so a nominal gap of threshold+1s is a conservative gap of exactly
        # threshold. Constructed rather than approximated: both quantities are
        # exact in binary floating point, so this really does sit on the line.
        threshold = 8 * HOUR
        on_the_line = [item("a", T0), item("b", T0 + threshold + 1.0)]
        self.assertEqual(1, len(cluster_events(on_the_line, gap_threshold_s=threshold)))
        over_the_line = [item("a", T0), item("b", T0 + threshold + 1.0 + 1e-6)]
        self.assertEqual(2, len(cluster_events(over_the_line, gap_threshold_s=threshold)))

    def test_a_distance_exactly_on_the_radius_holds_the_event_together(self):
        # The radius is taken from haversine itself so the comparison is exactly
        # on the boundary; picking a round number and hoping the distance lands
        # on it would test a value near the line rather than on it.
        library = [
            item("delhi", T0, latitude=28.6139, longitude=77.2090),
            item("jaipur", T0 + 2 * HOUR, latitude=26.9124, longitude=75.7873),
        ]
        exact = haversine_km(28.6139, 77.2090, 26.9124, 75.7873)
        self.assertEqual(1, len(cluster_events(library, location_split_km=exact)))
        self.assertEqual(
            2, len(cluster_events(library, location_split_km=math.nextafter(exact, 0.0)))
        )

    def test_a_confidence_exactly_on_the_floor_is_accepted(self):
        """The floor is the lowest confidence still believed. 0.5 is not a rare
        value -- it is what an extractor emits for "no better than even", and it
        is the value a caller sets the floor to in order to include it."""
        self.assertIsNone(undated_reason(item("x", T0, time_confidence=0.5)))
        self.assertEqual(
            "low_time_confidence",
            undated_reason(item("x", T0, time_confidence=math.nextafter(0.5, 0.0))),
        )

    def _scan_library(self, **kwargs):
        """A scan scoring exactly 0.8 against the beach event and 0.6 against
        the party. Both dot products are exact in binary floating point -- the
        zero components contribute nothing and the surviving term is a
        multiplication by 1.0 -- so the comparisons below really do land on the
        boundary rather than near it."""
        scan_vector = (0.8, 0.6, 0.0)
        library = [
            item(f"beach{n}", T0 + n * 600.0, embedding=(1.0, 0.0, 0.0)) for n in range(3)
        ]
        library += [
            item(f"party{n}", T0 + 60 * DAY + n * 600.0, embedding=(0.0, 1.0, 0.0))
            for n in range(3)
        ]
        library.append(item("scan", None, embedding=scan_vector))
        return cluster_events(library, **kwargs)

    def test_a_similarity_exactly_on_the_threshold_is_accepted(self):
        """"...must reach against its best-matching event" -- reach, so the
        documented number is the lowest score that still places an item. The
        eval harness sweeps this constant and reads its own value as attainable;
        an exclusive test makes every swept setting one ulp tighter than the
        number in the report."""
        placed = self._scan_library(undated_similarity_threshold=0.8)
        self.assertEqual((mid("scan"),), placed[0].undated_media_ids)
        rejected = self._scan_library(
            undated_similarity_threshold=math.nextafter(0.8, 1.0)
        )
        self.assertEqual(
            [mid("scan")], [i for c in rejected if c.kind == "undated" for i in c.media_ids]
        )

    def test_a_lead_exactly_equal_to_the_margin_is_enough(self):
        """Same convention on the other half of the accept test: the margin is
        the smallest lead that counts as unambiguous, not the largest that
        counts as a tie."""
        exact_lead = 0.8 - 0.6
        placed = self._scan_library(undated_similarity_margin=exact_lead)
        self.assertEqual((mid("scan"),), placed[0].undated_media_ids)
        rejected = self._scan_library(
            undated_similarity_margin=math.nextafter(exact_lead, 1.0)
        )
        self.assertEqual(
            [mid("scan")], [i for c in rejected if c.kind == "undated" for i in c.media_ids]
        )

    def test_the_confidence_floor_is_configurable_and_still_inclusive(self):
        self.assertIsNone(
            undated_reason(item("x", T0, time_confidence=0.8), min_time_confidence=0.8)
        )
        self.assertEqual(
            "low_time_confidence",
            undated_reason(item("x", T0, time_confidence=0.79), min_time_confidence=0.8),
        )


class TestUndatedReasonPrecedence(unittest.TestCase):
    """Which reason wins when an item fails several checks at once.

    The common undated item fails more than one: a scan has no timestamp AND
    unknown precision AND whatever confidence the extractor invented. This
    function returns exactly one string, and that string is what a person reads
    in a "why is this photo not in an album" report. Reordering the checks
    changes that answer for every multiply-failing item while leaving every
    cluster, count and id identical -- so nothing else in this file would
    notice.

    The order runs most fundamental first, so the reported reason names the
    thing that would have to be fixed before any other one mattered.
    """

    CASES = [
        # no timestamp beats everything: there is nothing to have a precision or
        # a confidence about.
        (dict(at=None, time_precision="unknown", time_confidence=0.1), "no_timestamp"),
        (dict(at=None, time_precision="year", time_confidence=1.0), "no_timestamp"),
        (dict(at=None, time_confidence=0.1), "no_timestamp"),
        # 'unknown' beats 'too coarse': "we have no usable time" is a different
        # statement from "we have a time, to the month", and reporting the
        # second for the first tells the reader to go looking for a month.
        (dict(at=T0, time_precision="unknown", time_confidence=0.1), "precision_unknown"),
        (dict(at=T0, time_precision="unknown", time_confidence=1.0), "precision_unknown"),
        # coarse precision beats low confidence: a confident month is still a
        # month, so the precision is the blocker.
        (dict(at=T0, time_precision="year", time_confidence=0.1), "precision_too_coarse"),
        (dict(at=T0, time_precision="month", time_confidence=0.1), "precision_too_coarse"),
        # only when everything else is usable is confidence the answer.
        (dict(at=T0, time_precision="second", time_confidence=0.1), "low_time_confidence"),
    ]

    def test_the_reported_reason_is_the_most_fundamental_failure(self):
        for kwargs, expected in self.CASES:
            with self.subTest(**kwargs):
                self.assertEqual(expected, undated_reason(item("x", **kwargs)))

    def test_every_case_really_does_fail_more_than_one_check(self):
        """Guarding the guard: if these items stopped being multiply-failing,
        the test above would pin nothing about ordering."""
        multiply_failing = 0
        for kwargs, _ in self.CASES:
            failures = 0
            failures += kwargs.get("at") is None
            failures += kwargs.get("time_precision", "second") not in {
                "second",
                "minute",
                "hour",
                "day",
            }
            failures += kwargs.get("time_confidence", 1.0) < 0.5
            multiply_failing += failures > 1
        self.assertGreaterEqual(multiply_failing, 6)


class TestUndatedMatchingMechanics(unittest.TestCase):
    """The two halves of the accept test, each on an input the other passes."""

    def _library(self, vector_a, vector_b, scan):
        library = [item(f"beach{n}", T0 + n * 600.0, embedding=vector_a) for n in range(3)]
        library += [
            item(f"party{n}", T0 + 60 * DAY + n * 600.0, embedding=vector_b) for n in range(3)
        ]
        library.append(item("scan", None, embedding=scan))
        return library

    def _bucketed(self, clusters):
        return [i for c in clusters if c.kind == "undated" for i in c.media_ids]

    def test_the_runner_up_is_demoted_when_a_later_event_takes_the_lead(self):
        """The ambiguity guard has to work in both arrival orders.

        Here the scan matches the FIRST event at 0.97 and the SECOND at 0.99, so
        the leader changes on the second event and the first has to become the
        runner-up. If it does not, the runner-up score stays at its sentinel,
        the accept test reads "only one candidate event", and a 0.02 margin is
        treated as unambiguous. The reverse arrangement -- best event first --
        works either way, which is why this needs its own test.
        """
        beach = (math.cos(0.0), math.sin(0.0))
        party = (math.cos(math.radians(22.18)), math.sin(math.radians(22.18)))
        scan = (math.cos(math.radians(14.07)), math.sin(math.radians(14.07)))
        # Both events clear the absolute threshold, so only the margin can
        # reject this. Asserted, not assumed.
        beach_score = beach[0] * scan[0] + beach[1] * scan[1]
        party_score = party[0] * scan[0] + party[1] * scan[1]
        self.assertGreater(beach_score, 0.75)
        self.assertGreater(party_score, beach_score)
        self.assertLess(party_score - beach_score, 0.05)

        clusters = cluster_events(self._library(beach, party, scan))
        self.assertEqual([mid("scan")], self._bucketed(clusters))

    def test_event_similarity_is_the_best_member_not_the_average(self):
        """A real event is visually diverse -- beach, dinner, fireworks -- so its
        mean vector resembles nothing in it. Averaging rejects exactly the item
        the module exists to place: a scanned wedding photograph matches *a*
        wedding photograph at 0.99 and the wedding's average at 0.38.
        """
        diverse = [
            item("beach0", T0, embedding=unit(1.0, 0.0, 0.0, 0.0)),
            item("beach1", T0 + 600.0, embedding=unit(0.0, 1.0, 0.0, 0.0)),
            item("beach2", T0 + 1200.0, embedding=unit(0.0, 0.0, 1.0, 0.0)),
        ]
        party = [
            item(f"party{n}", T0 + 60 * DAY + n * 600.0, embedding=unit(0.0, 0.0, 0.0, 1.0))
            for n in range(3)
        ]
        scan = item("scan", None, embedding=unit(0.99, 0.141067, 0.0, 0.0))
        # The discriminating fact: max over members is 0.99, the mean is 0.38.
        # One is over the threshold and one is not.
        scores = [sum(a * b for a, b in zip(scan.embedding, m.embedding)) for m in diverse]
        self.assertGreater(max(scores), 0.75)
        self.assertLess(sum(scores) / len(scores), 0.75)

        clusters = cluster_events(diverse + party + [scan])
        self.assertIn(mid("scan"), clusters[0].media_ids)
        self.assertEqual((mid("scan"),), clusters[0].undated_media_ids)

    def test_a_genre_level_resemblance_is_not_enough_to_file_a_photo(self):
        """0.65 is what two unrelated daylight outdoor photographs score. It is
        a statement that both are photographs of the world, not that they are
        the same occasion, and it is the modal score between any undated scan
        and any large event -- so accepting it files most of the undated bucket
        into whichever event is biggest.

        A wrong photo in a family album is the unrecoverable failure (CLAUDE.md
        hard rule 5, precision over recall), and the undated bucket is visible
        and fixable. The exact cut point is calibration the eval harness owns;
        what is pinned here is that a moderate resemblance loses and a strong
        one wins, on two inputs either side.
        """
        beach, party = unit(1.0, 0.0, 0.0), unit(0.0, 0.0, 1.0)
        moderate = unit(0.65, math.sqrt(1.0 - 0.65**2), 0.0)
        strong = unit(0.9, math.sqrt(1.0 - 0.9**2), 0.0)
        # Unambiguous in both cases -- the runner-up scores 0.0 -- so the margin
        # is not what decides either one.
        self.assertAlmostEqual(0.65, moderate[0], places=9)
        self.assertAlmostEqual(0.9, strong[0], places=9)

        self.assertEqual(
            [mid("scan")], self._bucketed(cluster_events(self._library(beach, party, moderate)))
        )
        strong_clusters = cluster_events(self._library(beach, party, strong))
        self.assertEqual([], self._bucketed(strong_clusters))
        self.assertEqual((mid("scan"),), strong_clusters[0].undated_media_ids)

    def test_assigned_items_come_out_in_id_order_whatever_order_they_arrived(self):
        """Two scans join the same event. Their order inside the cluster must be
        a property of the ids, not of the order the caller happened to pass
        them, or the cluster id moves with the input."""
        beach, party = unit(1.0, 0.0, 0.0), unit(0.0, 0.0, 1.0)
        library = self._library(beach, party, unit(0.99, 0.141067, 0.0))
        library.append(item("aaascan", None, embedding=unit(0.98, 0.198997, 0.0)))
        expected = tuple(sorted([mid("scan"), mid("aaascan")]))

        forward = cluster_events(library)
        backward = cluster_events(list(reversed(library)))
        self.assertEqual(expected, forward[0].undated_media_ids)
        self.assertEqual(expected, backward[0].undated_media_ids)
        self.assertEqual(forward[0].cluster_id, backward[0].cluster_id)
        self.assertEqual(forward[0].media_ids, backward[0].media_ids)

    def test_the_undated_bucket_is_in_id_order_whatever_order_it_arrived(self):
        """The bucket is emitted in its own order, on its own path through the
        code. Two unplaceable items passed in descending id order must come back
        ascending, or the same library produces two different outputs."""
        library = [item("zzz", None), item("aaa", None)]
        clusters = cluster_events(library)
        self.assertEqual(1, len(clusters))
        self.assertEqual((mid("aaa"), mid("zzz")), clusters[0].media_ids)
        self.assertEqual((mid("aaa"), mid("zzz")), clusters[0].undated_media_ids)


class TestMaxSpanMechanics(unittest.TestCase):
    """The span cut is a fabricated boundary, so where it lands and what order
    the halves come back in are both decisions, not details."""

    def test_the_halves_are_returned_chronologically(self):
        """The cut is recursive over a stack. Emitting the later half first
        partitions the library perfectly and returns the pieces out of order --
        every membership, count and conservation assertion still passes, and the
        album list reads as a shuffled timeline."""
        library = [item(f"t{n}", T0 + n * 12 * HOUR) for n in range(120)]
        clusters = cluster_events(library)
        self.assertGreater(len(clusters), 2, "needs several cuts to have an order at all")
        starts = [c.start_utc for c in clusters]
        self.assertEqual(sorted(starts), starts)
        # Stronger than sorted starts: the runs must also be contiguous, so no
        # cluster's members interleave with another's.
        ends = [c.end_utc for c in clusters]
        for earlier, later in zip(ends, starts[1:]):
            self.assertLess(earlier, later)

    def test_a_symmetric_tie_cuts_at_the_earlier_gap(self):
        """Five photos, evenly spaced, span limit forcing one cut. Two candidate
        gaps sit equidistant from the midpoint with identical gaps either side,
        so the midpoint rule cannot choose and the index rule decides. Which one
        it picks is arbitrary; that it always picks the same one is not, because
        the cut point becomes two cluster ids.
        """
        library = [item(f"t{n}", T0 + n * 12 * HOUR) for n in range(5)]
        clusters = cluster_events(library, gap_threshold_s=20 * HOUR, max_event_span_s=24 * HOUR)
        self.assertEqual([2, 3], [c.size for c in clusters])
        self.assertEqual(T0, clusters[0].start_utc)
        self.assertEqual(T0 + 24 * HOUR, clusters[1].start_utc)

    def test_a_month_of_continuous_shooting_is_not_one_album(self):
        """The discriminating span. A 25-day unbroken run has no gap above any
        threshold anywhere, so only the span limit can end it -- and 25 days of
        photographs is not one memory, it is a period of someone's life. Read
        with `test_a_two_week_trip_survives_whole`, which holds a 14-day run
        together, this brackets the limit between the longest trip a person
        would call one thing and the shortest stretch they would not.
        """
        library = [item(f"t{n}", T0 + n * 12 * HOUR) for n in range(51)]
        self.assertAlmostEqual(25.0, (51 - 1) * 12 * HOUR / DAY, places=9)
        clusters = cluster_events(library)
        self.assertGreater(len(clusters), 1, "25 continuous days is more than one event")
        for cluster in clusters:
            self.assertLess(cluster.end_utc - cluster.start_utc, 25 * DAY)


class TestKneeSelection(unittest.TestCase):
    def test_a_log_uniform_distribution_takes_the_lowest_knee(self):
        """Gaps at 6h, 12h, 24h, 48h, 96h: every ratio is exactly 2, so the
        distribution names no boundary at all and something has to break the
        tie. Taking the last knee puts the threshold at 68h, which merges every
        real event boundary shorter than three days -- the unrecoverable
        direction. Taking the first puts it at 8.5h, which over-splits, and an
        over-split library is one the user can see and merge.
        """
        gaps = [1.0] * 8 + [6 * HOUR, 12 * HOUR, 24 * HOUR, 48 * HOUR, 96 * HOUR]
        threshold = adaptive_gap_threshold(gaps)
        self.assertAlmostEqual(math.sqrt(6 * 12) * HOUR, threshold, places=6)
        self.assertLess(threshold, 12 * HOUR)

    def test_three_in_band_gaps_is_not_enough_evidence(self):
        """A knee found in three points is one photograph's worth of noise.

        Demonstrated rather than asserted: with the evidence floor lowered to
        two, moving a single gap from 20h to 26h -- one photo taken six hours
        later -- moves the derived threshold from 34.6h to 13.5h, which is a
        different event structure for the entire library. With the real floor,
        both distributions return the documented default and one photo changes
        nothing. The floor's exact value is calibration the eval harness owns;
        what it exists to produce is that a threshold is never derived from so
        little that a single frame rewrites it.
        """
        base = [1.0] * 20 + [7 * HOUR, 20 * HOUR, 60 * HOUR]
        nudged = [1.0] * 20 + [7 * HOUR, 26 * HOUR, 60 * HOUR]

        loose = adaptive_gap_threshold(base, min_in_band=2)
        loose_nudged = adaptive_gap_threshold(nudged, min_in_band=2)
        self.assertGreater(
            abs(loose - loose_nudged), 15 * HOUR, "this is the instability being refused"
        )

        self.assertEqual(DEFAULT_GAP_S, adaptive_gap_threshold(base))
        self.assertEqual(DEFAULT_GAP_S, adaptive_gap_threshold(nudged))

    def test_four_in_band_gaps_with_a_real_step_are_enough(self):
        """The floor must not be so high that it refuses genuine evidence: four
        in-band gaps with a clean step still derive."""
        gaps = [1.0] * 20 + [7 * HOUR, 8 * HOUR, 60 * HOUR, 66 * HOUR]
        self.assertNotEqual(DEFAULT_GAP_S, adaptive_gap_threshold(gaps))


class TestLocationRadius(unittest.TestCase):
    def test_another_city_ends_an_event(self):
        """Delhi to Jaipur is 235km. Two photographs that far apart on the same
        afternoon are two places, and an album that spans them is titled after
        one of them and full of the other. The radius has to be generous enough
        for a day out (24km, tested above) and tight enough for this; a radius
        wide enough to keep Jaipur inside a Delhi event is wide enough to keep
        most domestic travel inside it.
        """
        library = [
            item("delhi", T0, latitude=28.6139, longitude=77.2090),
            item("jaipur", T0 + 2 * HOUR, latitude=26.9124, longitude=75.7873),
        ]
        self.assertAlmostEqual(
            235.3, haversine_km(28.6139, 77.2090, 26.9124, 75.7873), places=1
        )
        clusters = cluster_events(library)
        self.assertEqual(2, len(clusters))
        self.assertEqual(("location_jump",), clusters[1].boundary_reasons)


if __name__ == "__main__":
    unittest.main(verbosity=1)
