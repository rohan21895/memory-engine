"""Absence is indeterminate, and indeterminate blocks -- at all three boundaries.

This is the file the whole package is for. Every way the classifier can fail to
produce a verdict is enumerated here, each one is turned into a manifest, each
manifest is validated against the REAL contract schema, and each is then
presented to all three boundary guards. Not one of them may clear.

The list of failure modes is the contract's own `indeterminate_reason` enum
rather than the ones that happened to be easy to produce, because the enum is
the thing a verifier has to be complete against.
"""

from __future__ import annotations

import unittest

from support import (  # noqa: E402
    MODEL_REF,
    NOW,
    RAN_AT,
    REPO_ROOT,
    RAN_AT as _RAN_AT,
    assert_valid_clearance,
    axis,
    embedder_over,
    hex64,
    synthetic_calibration,
    synthetic_head,
)

from memory_engine_safety import (  # noqa: E402
    AbsentEmbedder,
    Candidate,
    PublicationBlocked,
    SafetyClassifier,
    Thresholds,
    build_manifest,
    guard_frontier_egress,
    guard_print,
    guard_share,
)
from memory_engine_safety.embedding import EmbedderUnavailable  # noqa: E402
from memory_engine_safety.registry import classifier_from_registry  # noqa: E402

GUARDS = {
    "print": guard_print,
    "share": guard_share,
    "frontier_egress": guard_frontier_egress,
}

CANDIDATES = (
    Candidate(media_id=hex64("photo-1"), evidence_id=hex64("proxy-1")),
    Candidate(media_id=hex64("photo-2"), evidence_id=hex64("proxy-2")),
)


def manifest_for(classifier: SafetyClassifier, sink: str, *, load_mode: str = "release"):
    classification = classifier.classify(CANDIDATES)
    return build_manifest(
        classification,
        sink=sink,
        created_at=NOW,
        ran_at=RAN_AT,
        model=MODEL_REF,
        thresholds=classifier.thresholds,
        load_mode=load_mode,
        sink_detail=None,
    )


class TestEveryAbsenceProducesIndeterminate(unittest.TestCase):
    def _assert_all_indeterminate(self, classifier, expected_reason):
        result = classifier.classify(CANDIDATES)
        self.assertEqual(len(CANDIDATES), len(result.verdicts))
        for verdict in result.verdicts:
            self.assertEqual("indeterminate", verdict["verdict"])
            self.assertEqual(expected_reason, verdict["indeterminate_reason"])
            self.assertIsNone(verdict["scores"])
            self.assertIsNone(verdict["override"])
        self.assertTrue(result.detail, "a block with no explanation is unactionable")

    def test_no_head(self):
        self._assert_all_indeterminate(SafetyClassifier(), "model_unavailable")

    def test_head_but_no_calibration(self):
        """Raw logits against a 0.3 threshold is a threshold with no meaning."""
        self._assert_all_indeterminate(
            SafetyClassifier(head=synthetic_head()), "model_unloadable"
        )

    def test_head_and_calibration_but_no_embedder(self):
        self._assert_all_indeterminate(
            SafetyClassifier(
                head=synthetic_head(), calibration=synthetic_calibration()
            ),
            "model_unavailable",
        )

    def test_absent_embedder_reports_its_contract_reason(self):
        self._assert_all_indeterminate(
            SafetyClassifier(
                head=synthetic_head(),
                calibration=synthetic_calibration(),
                embedder=AbsentEmbedder(
                    reason="model_unavailable",
                    detail="siglip2-so400m-384 has no ONNX export yet (issue #79)",
                ),
            ),
            "model_unavailable",
        )

    def test_an_embedder_that_raises_anything_is_an_inference_error(self):
        class Exploding:
            space = "siglip2_so400m_1152"
            dimensions = 1152

            def embed(self, evidence_ids):
                raise TimeoutError("the runtime went away mid-batch")

        self._assert_all_indeterminate(
            SafetyClassifier(
                head=synthetic_head(),
                calibration=synthetic_calibration(),
                embedder=Exploding(),
            ),
            "inference_error",
        )

    def test_a_missing_row_is_no_result_and_only_for_that_item(self):
        """A per-item miss is different from a model that will not load."""
        classifier = SafetyClassifier(
            head=synthetic_head(),
            calibration=synthetic_calibration(),
            embedder=embedder_over({CANDIDATES[0].evidence_id: axis(2, 0.0)}),
        )
        verdicts = classifier.classify(CANDIDATES).verdicts
        self.assertEqual("cleared", verdicts[0]["verdict"])
        self.assertEqual("indeterminate", verdicts[1]["verdict"])
        self.assertEqual("no_result", verdicts[1]["indeterminate_reason"])

    def test_a_wrong_width_vector_is_an_inference_error_not_a_crash(self):
        class WrongWidth:
            space = "siglip2_so400m_1152"
            dimensions = 1152

            def embed(self, evidence_ids):
                return {evidence_id: [0.0] * 768 for evidence_id in evidence_ids}

        self._assert_all_indeterminate(
            SafetyClassifier(
                head=synthetic_head(),
                calibration=synthetic_calibration(),
                embedder=WrongWidth(),
            ),
            "inference_error",
        )

    def test_the_embedder_cannot_be_asked_for_a_reason_outside_the_contract(self):
        with self.assertRaises(ValueError):
            AbsentEmbedder(reason="because_i_said_so", detail="x")
        with self.assertRaises(ValueError):
            EmbedderUnavailable("not_a_reason", "x")

    def test_an_absent_embedder_must_say_why(self):
        """'safety check failed' is not something anyone can act on."""
        with self.assertRaises(ValueError):
            AbsentEmbedder(detail="   ")


class TestTheRealRegistryRefusesToday(unittest.TestCase):
    """Not a description of what would happen. The actual registry, now.

    The sensitive-content entry is a placeholder with no weights file, no hash,
    an unverified licence and `blocks_commercial_release: true`. Any one of
    those is enough; the load gate refuses a placeholder in EVERY mode.
    """

    def test_the_load_gate_denies_and_the_denial_becomes_indeterminate(self):
        classifier, mode = classifier_from_registry(REPO_ROOT)
        result = classifier.classify(CANDIDATES)
        for verdict in result.verdicts:
            self.assertEqual("indeterminate", verdict["verdict"])
            self.assertEqual("load_gate_denied", verdict["indeterminate_reason"])
        self.assertIn("PLACEHOLDER", result.detail)
        self.assertIn("#79", result.detail)
        self.assertIn(mode, ("release", "development"))

    def test_development_mode_does_not_relax_it(self):
        """A placeholder is known-unverified by construction, not merely unverified."""
        classifier, mode = classifier_from_registry(
            REPO_ROOT, environ={"MEMORY_ENGINE_ALLOW_UNVERIFIED_MODELS": "1"}
        )
        result = classifier.classify(CANDIDATES)
        self.assertTrue(
            all(v["verdict"] == "indeterminate" for v in result.verdicts),
            f"the {mode} gate cleared a placeholder safety classifier",
        )


class TestAllThreeBoundariesBlock(unittest.TestCase):
    """THE PROOF. One manifest per sink, from the real registry, at every guard."""

    def _blocked_manifest(self, sink: str):
        classifier, _ = classifier_from_registry(REPO_ROOT)
        return manifest_for(classifier, sink)

    def test_the_manifest_is_contract_valid_before_it_is_refused(self):
        """A refusal on a malformed manifest would prove nothing about the gate."""
        for sink in GUARDS:
            with self.subTest(sink=sink):
                assert_valid_clearance(self, self._blocked_manifest(sink))

    def test_the_decision_says_it_is_not_cleared_and_says_why(self):
        manifest = self._blocked_manifest("print")
        decision = manifest["decision"]
        self.assertFalse(decision["cleared_for_publication"])
        self.assertEqual(len(CANDIDATES), decision["indeterminate_count"])
        self.assertEqual(0, decision["cleared_count"])
        self.assertIn("indeterminate", decision["denied_reason"])
        self.assertIn("load_gate_denied", decision["denied_reason"])

    def test_each_boundary_refuses_its_own_indeterminate_manifest(self):
        for sink, guard in GUARDS.items():
            with self.subTest(sink=sink):
                with self.assertRaises(PublicationBlocked) as caught:
                    guard(
                        self._blocked_manifest(sink),
                        media_ids=[c.media_id for c in CANDIDATES],
                        evidence_ids={c.media_id: c.evidence_id for c in CANDIDATES},
                    )
                self.assertIn(
                    caught.exception.code,
                    ("indeterminate_item", "not_cleared"),
                )

    def test_each_boundary_refuses_a_missing_manifest(self):
        """The most important case: nobody produced one at all."""
        for sink, guard in GUARDS.items():
            with self.subTest(sink=sink):
                with self.assertRaises(PublicationBlocked) as caught:
                    guard(None, media_ids=[c.media_id for c in CANDIDATES])
                self.assertEqual("clearance_missing", caught.exception.code)

    def test_an_empty_override_list_does_not_reopen_it(self):
        for sink, guard in GUARDS.items():
            with self.subTest(sink=sink):
                with self.assertRaises(PublicationBlocked):
                    guard(
                        self._blocked_manifest(sink),
                        media_ids=[c.media_id for c in CANDIDATES],
                        evidence_ids={},
                    )

    def test_an_indeterminate_item_cannot_be_overridden_at_build_time_either(self):
        classifier, _ = classifier_from_registry(REPO_ROOT)
        classification = classifier.classify(CANDIDATES)
        with self.assertRaises(ValueError) as caught:
            build_manifest(
                classification,
                sink="print",
                created_at=NOW,
                ran_at=_RAN_AT,
                model=MODEL_REF,
                thresholds=Thresholds(),
                load_mode="release",
                overrides={
                    CANDIDATES[0].media_id: {
                        "decided_at": NOW,
                        "decided_by": "rohan",
                        "scope": "item_and_sink",
                        "note": "it is fine, I looked at it",
                    }
                },
            )
        self.assertIn("nobody checked", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
