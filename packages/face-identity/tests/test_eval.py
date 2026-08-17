"""Benchmark tests, and the check that keeps the committed gate file honest.

`test_the_committed_gate_file_matches_a_fresh_measurement` is the one that
matters. The gate file in packages/eval-harness/gates/ holds baseline and
candidate results that are equal by construction, so every delta the harness
computes is zero and the gate cannot notice that the code moved underneath it.
This test re-runs the benchmark and compares. Without it the gate file is a
souvenir.
"""

from __future__ import annotations

import json
import unittest
from functools import lru_cache

from support import REPO_ROOT  # noqa: E402

from memory_engine_face.eval import (  # noqa: E402
    BENCHMARK_MERGE_THRESHOLD,
    SYNTHETIC_LIBRARIES,
    LibrarySpec,
    generate_library,
    pairwise_scores,
    run_benchmark,
)

def _benchmark_categories() -> tuple[str, ...]:
    import sys

    harness_root = REPO_ROOT / "packages" / "eval-harness"
    if str(harness_root) not in sys.path:
        sys.path.insert(0, str(harness_root))
    from memory_engine_eval.harness import BENCHMARK_CATEGORIES

    return BENCHMARK_CATEGORIES


@lru_cache(maxsize=1)
def measured_scores():
    """One measurement per process. The benchmark is deterministic by design,
    so running it twice proves nothing that running it once does not."""
    return run_benchmark(merge_threshold=BENCHMARK_MERGE_THRESHOLD)


GATE_PATH = (
    REPO_ROOT
    / "packages"
    / "eval-harness"
    / "gates"
    / "face-clustering-synthetic.gate.json"
)


class MetricTests(unittest.TestCase):
    def test_perfect_grouping_scores_one_on_both(self) -> None:
        truth = {"a": 0, "b": 0, "c": 1}
        grouping = {"a": "x", "b": "x", "c": "y"}
        self.assertEqual(pairwise_scores(grouping, truth), (1.0, 1.0))

    def test_grouping_nothing_scores_zero_precision_not_one(self) -> None:
        # A clustering that made no claims has made no correct ones. Reporting
        # 1.0 for the empty case would make "everything is a singleton" the
        # highest-precision algorithm available.
        truth = {"a": 0, "b": 0}
        self.assertEqual(pairwise_scores({"a": "x", "b": "y"}, truth), (0.0, 0.0))

    def test_merging_two_people_costs_precision_not_recall(self) -> None:
        truth = {"a": 0, "b": 1}
        precision, recall = pairwise_scores({"a": "x", "b": "x"}, truth)
        self.assertEqual(precision, 0.0)
        self.assertEqual(recall, 0.0)

    def test_splitting_one_person_costs_recall_not_precision(self) -> None:
        truth = {"a": 0, "b": 0, "c": 0}
        precision, recall = pairwise_scores({"a": "x", "b": "x", "c": "y"}, truth)
        self.assertEqual(precision, 1.0)
        self.assertAlmostEqual(recall, 1 / 3)

    def test_a_face_the_clusterer_never_placed_counts_as_its_own_group(self) -> None:
        truth = {"a": 0, "b": 0}
        precision, recall = pairwise_scores({"a": "x"}, truth)
        self.assertEqual(recall, 0.0)


class LibraryTests(unittest.TestCase):
    def test_every_benchmark_category_is_covered_once(self) -> None:
        # Read from the harness rather than restated here: a category added to
        # the declared benchmark set with no synthetic library would otherwise
        # pass `min_cases_per_category` by not existing.
        self.assertEqual(
            sorted(spec.category for spec in SYNTHETIC_LIBRARIES),
            sorted(_benchmark_categories()),
        )

    def test_a_library_is_deterministic(self) -> None:
        spec = SYNTHETIC_LIBRARIES[0]
        first, truth_a = generate_library(spec)
        second, truth_b = generate_library(spec)
        self.assertEqual(
            [f.face_id for f in first], [f.face_id for f in second]
        )
        self.assertEqual(
            [f.embedding.values for f in first], [f.embedding.values for f in second]
        )
        self.assertEqual(truth_a, truth_b)

    def test_a_library_has_the_faces_it_declares(self) -> None:
        for spec in SYNTHETIC_LIBRARIES:
            with self.subTest(category=spec.category):
                observations, truth = generate_library(spec)
                self.assertEqual(
                    len(observations), spec.people * spec.faces_per_person
                )
                self.assertEqual(len(set(truth.values())), spec.people)

    def test_lookalike_pairs_are_closer_than_strangers(self) -> None:
        # Without them the suite measures nothing that matters: well-separated
        # identities report 1.000 forever.
        from memory_engine_face.embeddings import cosine_distance

        spec = LibrarySpec(
            "travel", people=4, faces_per_person=2, jitter=0.001,
            lookalike_pairs=1, seed=99,
        )
        observations, truth = generate_library(spec)
        by_person = {}
        for face in observations:
            by_person.setdefault(truth[face.face_id], []).append(face.embedding)
        lookalike = cosine_distance(by_person[0][0], by_person[1][0])
        stranger = cosine_distance(by_person[0][0], by_person[2][0])
        self.assertLess(lookalike, stranger)

    def test_a_degenerate_spec_is_refused(self) -> None:
        for kwargs in (
            {"people": 1},
            {"faces_per_person": 1},
            {"jitter": 0.0},
            {"jitter": 1.0},
            {"lookalike_pairs": 3},
        ):
            with self.subTest(kwargs=kwargs):
                base = dict(
                    category="travel", people=4, faces_per_person=4,
                    jitter=0.02, lookalike_pairs=1, seed=1,
                )
                base.update(kwargs)
                with self.assertRaises(ValueError):
                    LibrarySpec(**base)


class GateFileTests(unittest.TestCase):
    def test_the_committed_gate_file_matches_a_fresh_measurement(self) -> None:
        document = json.loads(GATE_PATH.read_text(encoding="utf-8"))
        committed = {
            result["case_id"]: result["samples"][0]
            for result in document["candidate"]
        }
        measured = measured_scores()
        self.assertEqual(
            committed,
            measured,
            "the committed benchmark no longer matches what the code measures; "
            "regenerate it with `python3 -m memory_engine_face.eval --as-of "
            "<date> --write <path>` and review the movement",
        )

    def test_the_gate_file_runs_and_passes(self) -> None:
        import sys

        harness_root = REPO_ROOT / "packages" / "eval-harness"
        if str(harness_root) not in sys.path:
            sys.path.insert(0, str(harness_root))
        from memory_engine_eval.harness import EXIT_PASS, run_gate_file

        outcome = run_gate_file(GATE_PATH)
        self.assertEqual(
            outcome.exit_code,
            EXIT_PASS,
            outcome.refusal or (outcome.report and outcome.report.failures),
        )

    def test_every_case_declares_a_floor_below_its_measurement(self) -> None:
        # A floor set exactly at today's number fails on the first legitimate
        # improvement's rounding, and a gate that fails for no reason gets
        # switched off.
        document = json.loads(GATE_PATH.read_text(encoding="utf-8"))
        measured = measured_scores()
        for case in document["suite"]["cases"]:
            with self.subTest(case=case["case_id"]):
                self.assertLessEqual(case["expected"], measured[case["case_id"]])
                self.assertTrue(case["case_id"].startswith("synthetic_"))

    def test_the_gate_file_never_claims_recognition_precision(self) -> None:
        # The case ids and the description exist so that nobody reading a report
        # mistakes a synthetic clustering number for the >=99% face-recognition
        # precision gate, which has not been measured anywhere in this repo.
        text = GATE_PATH.read_text(encoding="utf-8")
        self.assertIn("NOT face recognition precision", text)
        for case_id in json.loads(text)["suite"]["cases"]:
            self.assertIn("synthetic", case_id["case_id"])

    def test_both_precision_and_recall_are_gated_in_every_category(self) -> None:
        document = json.loads(GATE_PATH.read_text(encoding="utf-8"))
        by_category = {}
        for case in document["suite"]["cases"]:
            by_category.setdefault(case["category"], set()).add(
                case["case_id"].rsplit("_", 1)[-1]
            )
        for category, metrics in by_category.items():
            with self.subTest(category=category):
                self.assertEqual(metrics, {"precision", "recall"})


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
