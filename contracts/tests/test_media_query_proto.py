"""The media-db read interface, checked structurally.

Codex could not build the desktop library view because media-db is Python and
the Tauri shell is Rust, and reaching into the SQLite file directly is
forbidden. They asked rather than inventing an IPC contract (issue #15); this
proto is the answer, and these tests pin the properties it exists to guarantee.

The central one is that there must be exactly ONE reader. Every safety rule in
this system lives in media-db's query layer rather than in its schema --
proxy-only resolution, the automated-output eligibility threshold, sensitive
exclusion -- and a second reader would either reimplement them or, far more
likely, omit them. Every omission fails OPEN, showing more than it should.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

CONTRACTS = Path(__file__).resolve().parent.parent
PROTO = (CONTRACTS / "proto" / "media_query.proto").read_text(encoding="utf-8")


def _message(name: str) -> str:
    match = re.search(rf"^message {name} \{{(.*?)^\}}", PROTO, re.MULTILINE | re.DOTALL)
    if not match:
        raise AssertionError(f"message {name} not found")
    return match.group(1)


def _fields(name: str) -> set[str]:
    return set(re.findall(r"^\s*(?:repeated\s+|optional\s+)?[\w.]+\s+(\w+)\s*=\s*\d+;",
                          _message(name), re.MULTILINE))


def _rpcs() -> set[str]:
    return set(re.findall(r"rpc (\w+)\(", PROTO))


class TestReadOnlyByConstruction(unittest.TestCase):
    def test_no_rpc_can_mutate_the_library(self):
        """Ingest and analysis write through the Python API in-process. Nothing
        crossing this boundary may change anything -- which is also what makes
        it safe for the shell to hold a connection open for its lifetime."""
        mutating = {r for r in _rpcs()
                    if re.match(r"^(Put|Set|Update|Delete|Remove|Create|Insert|Write)", r)}
        self.assertEqual(set(), mutating, f"mutating RPCs: {sorted(mutating)}")

    def test_no_rpc_returns_an_original_files_path_or_bytes(self):
        """`resolve_path` exists in the Python API for the renderer, which runs
        in-process. Exposing it here would put a shell one bug away from
        shipping a 6000px RAW through an IPC channel."""
        forbidden = {"path", "file_path", "source_path", "original_path",
                     "filename", "uri", "url", "original_bytes"}
        declared = set(re.findall(r"^\s*(?:repeated\s+|optional\s+)?[\w.]+\s+(\w+)\s*=\s*\d+;",
                                  PROTO, re.MULTILINE))
        self.assertEqual(set(), declared & forbidden)


class TestSafetyRulesSurvivePublication(unittest.TestCase):
    """The rules that live in the query layer must be expressible here, or the
    shell will have to approximate them -- and every approximation fails open."""

    def test_pixels_come_only_from_a_proxy_id(self):
        fields = _fields("GetProxyBytesRequest")
        self.assertIn("proxy_id", fields)
        self.assertNotIn("media_id", fields)

    def test_faces_carry_their_automated_output_eligibility(self):
        """A shell showing an ineligible face as a confirmed person would put a
        name on a face the system explicitly declined to name."""
        self.assertIn("eligible_for_automated_output", _fields("FaceRef"))

    def test_sensitive_is_distinguishable_from_unclassified(self):
        """Different states. A UI conflating them either hides a
        scan-in-progress library or shows what it must not."""
        fields = _fields("MediaSummary")
        self.assertIn("is_sensitive", fields)
        self.assertIn("safety_unknown", fields)

    def test_sensitive_and_rejected_are_excluded_by_default(self):
        """Opt-IN flags. A default-true `exclude_*` would invert on a client
        that forgot to set it."""
        fields = _fields("ListMediaRequest")
        self.assertIn("include_sensitive", fields)
        self.assertIn("include_rejected", fields)

    def test_minor_status_reaches_the_shell(self):
        """No sharing affordance, no export without a consent scope."""
        self.assertIn("is_minor", _fields("Person"))

    def test_uncertain_faces_are_reachable_only_through_the_review_queue(self):
        """The one place they belong -- ten taps of labelling fixes a thousand
        photos -- and nowhere else."""
        self.assertIn("ReviewQueue", _rpcs())
        self.assertIn("similarity", _fields("ReviewItem"))


class TestHonestAboutWhatItDoesNotKnow(unittest.TestCase):
    def test_capture_precision_travels_with_the_timestamp(self):
        """Rendering a day-precision date as `14:32` is a fabrication the UI
        performs. The precision has to reach it."""
        fields = _fields("MediaSummary")
        self.assertIn("captured_unix", fields)
        self.assertIn("capture_precision", fields)

    def test_undated_media_can_be_included_in_a_date_filter(self):
        """A scan or a WhatsApp forward has no EXIF date. Silently dropping
        those from every date-filtered view makes part of the library
        unreachable."""
        self.assertIn("include_undated", _fields("TimeFilter"))

    def test_quality_says_whether_it_is_comparable(self):
        """The ranking engine refuses to rank scores measured differently. A
        shell sorting by quality mid-scan must know that."""
        self.assertIn("quality_is_comparable", _fields("MediaSummary"))

    def test_scan_progress_is_reportable(self):
        """A user who believes their library is complete and sees a gap will
        conclude photos were lost."""
        fields = _fields("LibraryStatsResponse")
        self.assertIn("media_awaiting_analysis", fields)
        self.assertIn("media_awaiting_proxies", fields)


class TestPagingSurvivesAConcurrentScan(unittest.TestCase):
    def test_paging_is_by_opaque_cursor_not_offset(self):
        """A library is written to while it is browsed, and OFFSET paging
        silently skips or repeats rows when a scan inserts ahead of the
        cursor."""
        self.assertIn("cursor", _fields("ListMediaRequest"))
        self.assertNotIn("offset", _fields("ListMediaRequest"))
        self.assertIn("token", _fields("Cursor"))

    def test_has_more_is_explicit(self):
        """An empty next token and 'the server declined to page further' are
        different answers, and a client showing a spinner cannot tell."""
        self.assertIn("has_more", _fields("Page"))


if __name__ == "__main__":
    unittest.main()
