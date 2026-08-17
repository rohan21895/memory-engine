"""The frame index reader: where a one-frame error becomes a wrong timecode."""

from __future__ import annotations

import json
import tempfile
import unittest
from fractions import Fraction
from pathlib import Path

import _support  # noqa: F401 - sets sys.path

from memory_engine_video_analysis.proxy import (
    ProxyError,
    VariableFrameRate,
    proxies_in_record,
    read_frame_index,
)

GOLDEN = _support.FIXTURES / "ingest-29-97.idx"


def _sidecar(rows, **header):
    base = {
        "schema": "memory-engine-frame-index",
        "version": 1,
        "mapping": "identity",
        "entry_count": len(rows),
        "source_rate": 30.0,
        "proxy_rate": 30.0,
        "source_time_base_numerator": 1,
        "source_time_base_denominator": 15360,
    }
    base.update(header)
    if header.get("entry_count") is None and "entry_count" in header:
        base.pop("entry_count")
    lines = [json.dumps(base)] + [json.dumps(row) for row in rows]
    handle = tempfile.NamedTemporaryFile(
        "w", suffix=".idx", delete=False, encoding="utf-8"
    )
    handle.write("\n".join(lines) + "\n")
    handle.close()
    return Path(handle.name)


def _rows(count, *, delta=512, first=0):
    return [
        {
            "proxy_frame": index,
            "source_pts": first + index * delta,
            "source_time_seconds": (first + index * delta) / 15360.0,
        }
        for index in range(count)
    ]


class GoldenSidecar(unittest.TestCase):
    """Against a sidecar the Rust worker actually wrote."""

    def test_the_real_ingest_sidecar_parses(self):
        index = read_frame_index(GOLDEN)
        self.assertEqual(index.entry_count, 119)
        self.assertEqual(index.declared_mapping, "identity")
        self.assertEqual(index.time_base, Fraction(1, 30000))

    def test_the_rate_is_exact_not_the_float_in_the_header(self):
        """30000/1001 has no float form, so the header's number is not the rate.

        This is the whole reason the reader recomputes from the time base: a
        rate of 29.97002997002997 accumulated over a long timeline drifts, and
        `RationalTime.rate` is meant to be exact.
        """
        grid = read_frame_index(GOLDEN).source_grid()
        self.assertEqual(grid.rate, Fraction(30000, 1001))
        self.assertNotEqual(grid.rate, Fraction(2997, 100))
        self.assertEqual(grid.start_value, 0)
        self.assertEqual(grid.frame_count, 119)

    def test_the_exact_rate_still_round_trips_to_the_declared_float(self):
        index = read_frame_index(GOLDEN)
        self.assertEqual(index.source_grid().rate_float, index.declared_source_rate)


class GridDerivation(unittest.TestCase):
    def test_a_uniform_grid_yields_rate_and_start(self):
        grid = read_frame_index(_sidecar(_rows(10))).source_grid()
        self.assertEqual(grid.rate, Fraction(30))
        self.assertEqual(grid.start_value, 0)

    def test_a_non_zero_start_becomes_the_source_frame_index(self):
        grid = read_frame_index(_sidecar(_rows(10, first=512 * 7))).source_grid()
        self.assertEqual(grid.start_value, 7)

    def test_variable_deltas_are_refused_as_variable_frame_rate(self):
        rows = _rows(10)
        rows[5]["source_pts"] += 40
        for row in rows[6:]:
            row["source_pts"] += 40
        with self.assertRaises(VariableFrameRate) as caught:
            read_frame_index(_sidecar(rows)).source_grid()
        self.assertIn("variable frame rate", str(caught.exception))

    def test_a_gap_in_the_proxy_frame_numbers_is_refused(self):
        rows = _rows(10)
        rows[4]["proxy_frame"] = 5
        with self.assertRaises(ProxyError):
            read_frame_index(_sidecar(rows)).source_grid()

    def test_non_increasing_pts_is_refused(self):
        rows = _rows(4)
        rows[2]["source_pts"] = rows[1]["source_pts"]
        with self.assertRaises(ProxyError):
            read_frame_index(_sidecar(rows)).source_grid()

    def test_a_start_that_is_not_a_whole_number_of_frames_is_refused(self):
        """A rounded start offsets every moment in the clip by a fraction."""
        with self.assertRaises(ProxyError):
            read_frame_index(_sidecar(_rows(6, first=200))).source_grid()

    def test_a_declared_rate_that_disagrees_with_the_time_base_is_refused(self):
        with self.assertRaises(ProxyError) as caught:
            read_frame_index(_sidecar(_rows(6), source_rate=25.0)).source_grid()
        self.assertIn("time base", str(caught.exception))

    def test_a_missing_time_base_is_refused_rather_than_taking_the_float(self):
        rows = _rows(6)
        with self.assertRaises(ProxyError):
            read_frame_index(
                _sidecar(
                    rows,
                    source_time_base_numerator=None,
                    source_time_base_denominator=None,
                )
            ).source_grid()

    def test_one_frame_is_not_a_grid(self):
        with self.assertRaises(ProxyError):
            read_frame_index(_sidecar(_rows(1))).source_grid()


class Parsing(unittest.TestCase):
    def test_a_declared_entry_count_that_does_not_match_is_refused(self):
        """A truncated sidecar read as complete puts the tail outside the index."""
        with self.assertRaises(ProxyError) as caught:
            read_frame_index(_sidecar(_rows(5), entry_count=9))
        self.assertIn("declares 9 entries", str(caught.exception))

    def test_an_unknown_schema_is_refused(self):
        with self.assertRaises(ProxyError):
            read_frame_index(_sidecar(_rows(3), schema="something-else"))

    def test_a_future_version_is_refused_rather_than_read_optimistically(self):
        with self.assertRaises(ProxyError) as caught:
            read_frame_index(_sidecar(_rows(3), version=2))
        self.assertIn("version 2", str(caught.exception))

    def test_a_malformed_row_names_its_line(self):
        path = _sidecar(_rows(3))
        path.write_text(path.read_text() + "{not json}\n", encoding="utf-8")
        with self.assertRaises(ProxyError) as caught:
            read_frame_index(path)
        self.assertIn("line 5", str(caught.exception))


class RecordExtraction(unittest.TestCase):
    def _record(self, proxies):
        return {"media_id": "c" * 64, "kind": "video", "proxies": proxies}

    def test_a_record_with_no_video_proxy_yields_nothing(self):
        self.assertEqual(
            proxies_in_record(self._record([{"kind": "thumbnail_512", "path": "x"}])),
            (),
        )

    def test_a_video_proxy_without_a_frame_index_is_an_error_not_an_omission(self):
        """Analysis could still decode it, and every result would be in proxy time."""
        with self.assertRaises(ProxyError) as caught:
            proxies_in_record(
                self._record([{"kind": "video_proxy_480p", "path": "x", "proxy_id": "d"}])
            )
        self.assertIn("frame index", str(caught.exception))

    def test_a_video_proxy_is_returned_with_its_index(self):
        found = proxies_in_record(
            self._record(
                [
                    {
                        "kind": "video_proxy_480p",
                        "path": "/nowhere.mp4",
                        "proxy_id": "e" * 64,
                        "size": {"width": 854, "height": 480},
                        "frame_index": {"path": str(GOLDEN)},
                    }
                ]
            )
        )
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].width, 854)
        self.assertEqual(found[0].proxy_grid.frame_count, 119)


if __name__ == "__main__":
    unittest.main()
