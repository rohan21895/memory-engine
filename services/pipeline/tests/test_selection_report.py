"""The selection sidecar: every candidate accounted for, every reason a number.

The report's one integrity rule is conservation: a candidate appears in
exactly ONE of selected / alternatives / rejected / unselected. A sidecar that
lists a photo twice (or loses one) makes the swap UX lie about what exists.
The other rules pinned here: reasons are non-empty strings carrying the real
numbers they were derived from, and the slot-fit flag uses the layout engine's
own DPI arithmetic (a too-small alternative must read False).
"""

from __future__ import annotations

import json
import math
import unittest
from datetime import datetime, timedelta, timezone

from support import REPO_ROOT  # noqa: F401  (sets sys.path for the package)

from memory_engine_pipeline.selection_report import (  # noqa: E402
    SIDECAR_VERSION,
    build_selection_report,
)

from memory_engine_album.selection import (  # noqa: E402
    SelectionCandidate,
    SelectionPolicy,
    select,
)
from memory_engine_ranking.fusion import FusedScore, Weights  # noqa: E402

DIGEST = Weights().digest()
SPACE = "sidecar-test-space"


def mid(tag: str) -> str:
    return (tag.encode("utf-8").hex() + "0" * 64)[:64]


def score(value: float) -> FusedScore:
    measured = ("exposure", "sharpness")
    return FusedScore(
        value=value,
        coverage=1.0,
        rejected=False,
        rejection_reason=None,
        weights_id="default-v2",
        weights_digest=DIGEST,
        feature_set_id="photo-quality-v1",
        contributions=tuple((name, value, 0.5) for name in measured),
    )


def at(second: int, day: int = 1) -> str:
    base = datetime(2026, 3, 1, 12, 0, 0, tzinfo=timezone.utc)
    return (base + timedelta(days=day - 1, seconds=second)).isoformat()


def unit(*components: float) -> tuple[float, ...]:
    norm = sum(c * c for c in components) ** 0.5
    return tuple(c / norm for c in components)


def cand(tag: str, value: float, **kwargs) -> SelectionCandidate:
    return SelectionCandidate(media_id=mid(tag), score=score(value), **kwargs)


def burst(tag: str, second: int, value: float, **kwargs) -> SelectionCandidate:
    return cand(
        tag,
        value,
        captured_utc=at(second),
        embedding=unit(1.0, 0.0, 0.0),
        embedding_space=SPACE,
        **kwargs,
    )


def frame(width_mm: float, height_mm: float) -> dict:
    return {
        "x_mm": 0.0,
        "y_mm": 0.0,
        "width_mm": width_mm,
        "height_mm": height_mm,
        "rotation_deg": 0,
    }


DPI_FLOOR = 300.0


class SidecarReport(unittest.TestCase):
    """One pool exercising all four fates: a 3-frame burst (winner + two
    alternatives), a distinct solo (selected), a weaker solo (lost on merit),
    and a user-hidden photo (rejected)."""

    def setUp(self):
        self.candidates = [
            burst("b-win", 0, 0.90, awake=0.04, face_sharpness=0.42),
            burst("b-blink", 1, 0.86, awake=-0.06, face_sharpness=0.40),
            burst("b-soft", 2, 0.88, awake=0.05, face_sharpness=0.20),
            cand(
                "solo-a",
                0.70,
                captured_utc=at(0, day=3),
                embedding=unit(0.0, 1.0, 0.0),
                embedding_space=SPACE,
                long_edge_px=6000,
            ),
            cand(
                "solo-b",
                0.50,
                captured_utc=at(0, day=5),
                embedding=unit(0.0, 0.0, 1.0),
                embedding_space=SPACE,
            ),
            cand("hidden", 0.95, user_hidden=True),
        ]
        self.policy = SelectionPolicy()
        self.selection = select(self.candidates, 2, policy=self.policy)
        self.assertEqual(
            (mid("b-win"), mid("solo-a")), self.selection.selected,
            "the scenario assumes the burst winner and the best solo are chosen",
        )
        # A portrait book page: 206x306mm at 300 DPI needs a 3615px long edge.
        self.pages = [
            {
                "page_index": 0,
                "side": "front_cover",
                "placements": [
                    {
                        "placement_id": "cover-hero",
                        "media_id": mid("b-win"),
                        "frame": frame(206.0, 306.0),
                        "effective_dpi": 495.0,
                        "is_hero": True,
                    }
                ],
            },
            {
                "page_index": 1,
                "side": "left",
                "placements": [
                    {
                        "placement_id": "page-01-a",
                        "media_id": mid("b-win"),
                        "frame": frame(206.0, 306.0),
                        "effective_dpi": 495.0,
                        "is_hero": True,
                    }
                ],
            },
            {
                "page_index": 2,
                "side": "right",
                "placements": [
                    {
                        "placement_id": "page-02-a",
                        "media_id": mid("solo-a"),
                        "frame": frame(100.0, 150.0),
                        "effective_dpi": 1016.0,
                        "is_hero": False,
                    }
                ],
            },
        ]
        self.report = build_selection_report(
            selection=self.selection,
            candidates=self.candidates,
            pages=self.pages,
            policy=self.policy,
            dpi_floor=DPI_FLOOR,
            planner="album-planner",
            planner_version="0.4.1",
            inputs_digest="d" * 64,
            pixel_sizes={
                mid("b-win"): (4000, 6000),
                mid("b-blink"): (4000, 6000),   # plenty of pixels: fits
                mid("b-soft"): (1000, 1500),    # far below the floor: does not
            },
        )

    # ------------------------------------------------------------ integrity

    def test_every_candidate_appears_exactly_once(self):
        seen: list[str] = []
        for entry in self.report["selected"]:
            seen.append(entry["media_id"])
            seen.extend(a["media_id"] for a in entry["alternatives"])
        seen.extend(e["media_id"] for e in self.report["rejected"])
        seen.extend(e["media_id"] for e in self.report["unselected"])
        self.assertEqual(len(seen), len(set(seen)), "a candidate was listed twice")
        self.assertEqual({c.media_id for c in self.candidates}, set(seen))

    def test_counts_match_the_lists_and_the_version_is_stamped(self):
        report = self.report
        self.assertEqual(SIDECAR_VERSION, report["sidecar_version"])
        self.assertEqual("d" * 64, report["album_id"])
        counts = report["counts"]
        self.assertEqual(len(self.candidates), counts["candidates"])
        self.assertEqual(len(report["selected"]), counts["selected"])
        self.assertEqual(len(report["rejected"]), counts["rejected"])
        self.assertEqual(len(report["unselected"]), counts["unselected"])
        self.assertEqual(
            sum(len(e["alternatives"]) for e in report["selected"]),
            counts["alternatives"],
        )
        self.assertEqual(self.policy.quality_floor, report["policy"]["quality_floor"])

    def test_the_sidecar_is_json_serialisable(self):
        json.dumps(self.report)

    # -------------------------------------------------------------- reasons

    def _selected(self, tag: str) -> dict:
        for entry in self.report["selected"]:
            if entry["media_id"] == mid(tag):
                return entry
        raise AssertionError(f"{tag} not in selected")

    def test_reasons_are_non_empty_and_carry_real_numbers(self):
        def assert_reasoned(reasons):
            self.assertTrue(reasons)
            for reason in reasons:
                self.assertIsInstance(reason, str)
                self.assertTrue(reason.strip())
            self.assertTrue(
                any(ch.isdigit() for reason in reasons for ch in reason),
                f"no number anywhere in {reasons!r}",
            )

        for entry in self.report["selected"]:
            assert_reasoned(entry["chosen_because"])
            for alternative in entry["alternatives"]:
                assert_reasoned(alternative["not_chosen_because"])
        for entry in self.report["unselected"]:
            self.assertTrue(entry["detail"].strip())

    def test_the_blink_alternative_names_the_eyes_and_the_similarity(self):
        winner = self._selected("b-win")
        blink = next(
            a for a in winner["alternatives"] if a["media_id"] == mid("b-blink")
        )
        text = " ".join(blink["not_chosen_because"])
        self.assertIn("eyes read less open", text)
        self.assertIn("-0.06", text)
        self.assertIn("+0.04", text)
        self.assertIn("similar to the chosen frame", text)

    def test_the_soft_alternative_names_the_faces(self):
        winner = self._selected("b-win")
        soft = next(
            a for a in winner["alternatives"] if a["media_id"] == mid("b-soft")
        )
        self.assertIn(
            "softer faces", " ".join(soft["not_chosen_because"])
        )

    def test_the_merit_loser_states_what_beat_it(self):
        entry = next(
            e for e in self.report["unselected"] if e["media_id"] == mid("solo-b")
        )
        self.assertEqual("lost_on_merit", entry["reason"])
        self.assertIn("standing", entry["detail"])

    def test_the_hidden_photo_is_rejected_with_its_machine_reason(self):
        entry = next(
            e for e in self.report["rejected"] if e["media_id"] == mid("hidden")
        )
        self.assertEqual("user_hidden", entry["reason"])

    # ------------------------------------------------------------- slot fit

    def test_slot_fit_is_true_for_a_big_alternative_and_false_for_a_small_one(self):
        winner = self._selected("b-win")
        by_media = {a["media_id"]: a for a in winner["alternatives"]}
        self.assertIs(True, by_media[mid("b-blink")]["fits_slot"])
        self.assertIs(False, by_media[mid("b-soft")]["fits_slot"])
        self.assertIn("DPI", by_media[mid("b-soft")]["slot_fit_detail"])

    def test_placement_reads_the_interior_page_not_the_cover(self):
        placement = self._selected("b-win")["placement"]
        self.assertEqual(1, placement["page_index"])
        self.assertEqual("page-01-a", placement["placement_id"])
        self.assertEqual(495.0, placement["effective_dpi"])
        # 306mm long edge at 300 DPI: ceil(300 * 306 / 25.4) = 3615px.
        self.assertEqual(
            int(math.ceil(DPI_FLOOR * 306.0 / 25.4)),
            placement["long_edge_px_needed"],
        )
        self.assertEqual(6000, placement["long_edge_px_available"])

    def test_confidence_margin_exists_for_the_burst_and_not_for_the_solo(self):
        burst_confidence = self._selected("b-win")["confidence"]
        self.assertIsNotNone(burst_confidence["margin"])
        self.assertGreater(burst_confidence["margin"], 0.0)
        self.assertIn(
            burst_confidence["runner_up"], {mid("b-blink"), mid("b-soft")}
        )
        solo_confidence = self._selected("solo-a")["confidence"]
        self.assertIsNone(solo_confidence["margin"])
        self.assertIsNone(solo_confidence["runner_up"])

    def test_long_edge_fallback_when_only_the_long_edge_is_known(self):
        # solo-a has no pixel_sizes entry but carries long_edge_px=6000; its
        # placement exists, and an alternative-style fit for IT is not asked.
        # Exercise the fallback through a report where the blink frame's full
        # size is unknown but its long edge is too small.
        candidates = list(self.candidates)
        candidates[1] = burst(
            "b-blink", 1, 0.86, awake=-0.06, face_sharpness=0.40, long_edge_px=1000
        )
        report = build_selection_report(
            selection=select(candidates, 2, policy=self.policy),
            candidates=candidates,
            pages=self.pages,
            policy=self.policy,
            dpi_floor=DPI_FLOOR,
            planner="album-planner",
            planner_version="0.4.1",
            inputs_digest="d" * 64,
            pixel_sizes={},
        )
        winner = next(
            e for e in report["selected"] if e["media_id"] == mid("b-win")
        )
        blink = next(
            a for a in winner["alternatives"] if a["media_id"] == mid("b-blink")
        )
        self.assertIs(False, blink["fits_slot"])
        self.assertIn("aspect unchecked", blink["slot_fit_detail"])
        soft = next(
            a for a in winner["alternatives"] if a["media_id"] == mid("b-soft")
        )
        self.assertIsNone(soft["fits_slot"], "no size at all: fit is unknown")


if __name__ == "__main__":
    unittest.main()
