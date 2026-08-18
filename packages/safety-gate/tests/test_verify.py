"""The verifier's denial matrix, and the one path that is allowed to clear.

WHY THE HAPPY PATH IS THE FIRST TEST IN THE FILE

A gate that refuses everything passes every "does it block?" test and is
useless. So the first thing proved here is that a complete, correct, cleared
manifest over the exact publication DOES clear -- through a synthetic head the
test built itself, which is the only place in this repository where a head
exists at all. Every denial below is then a single mutation away from that
manifest, so each one shows that the specific defect is what caused the refusal
rather than something incidental.
"""

from __future__ import annotations

import copy
import unittest

from support import (  # noqa: E402
    MODEL_REF,
    NOW,
    RAN_AT,
    assert_valid_clearance,
    axis,
    embedder_over,
    hex64,
    synthetic_calibration,
    synthetic_head,
)

from memory_engine_safety import (  # noqa: E402
    Candidate,
    PublicationBlocked,
    SafetyClassifier,
    Thresholds,
    build_manifest,
    guard_print,
    guard_share,
    verify_clearance,
)
from memory_engine_safety.canonical import manifest_id as compute_manifest_id  # noqa: E402

BENIGN = Candidate(media_id=hex64("benign"), evidence_id=hex64("benign-proxy"))
SENSITIVE = Candidate(media_id=hex64("sensitive"), evidence_id=hex64("sensitive-proxy"))

OVERRIDE = {
    "decided_at": NOW,
    "decided_by": "rohan",
    "scope": "item_and_sink",
    "note": "our own photograph, in our own book",
}


def classifier() -> SafetyClassifier:
    """A complete classifier over a head this test wrote. Not a model."""
    return SafetyClassifier(
        thresholds=Thresholds(),
        head=synthetic_head(),
        calibration=synthetic_calibration(),
        embedder=embedder_over(
            {
                # axis 0 is `explicit` in the synthetic head; 1.0 there lands
                # far above 0.3 after calibration, 0.0 far below.
                BENIGN.evidence_id: axis(900, 0.0),
                SENSITIVE.evidence_id: axis(0, 1.0),
            }
        ),
    )


def manifest(candidates, sink="print", *, overrides=None, load_mode="release"):
    return build_manifest(
        classifier().classify(candidates),
        sink=sink,
        created_at=NOW,
        ran_at=RAN_AT,
        model=MODEL_REF,
        thresholds=Thresholds(),
        load_mode=load_mode,
        sink_detail="layflat-300-square",
        overrides=overrides,
    )


def rehash(document: dict) -> dict:
    """Re-sign a mutated manifest, so the test is not passing on the digest."""
    document = copy.deepcopy(document)
    document.pop("manifest_id", None)
    document["manifest_id"] = compute_manifest_id(document)
    return document


class TestTheHappyPath(unittest.TestCase):
    def test_a_correct_clearance_clears(self):
        document = manifest([BENIGN])
        assert_valid_clearance(self, document)
        self.assertTrue(document["decision"]["cleared_for_publication"])
        clearance = guard_print(
            document,
            media_ids=[BENIGN.media_id],
            evidence_ids={BENIGN.media_id: BENIGN.evidence_id},
            expected_model=MODEL_REF,
            expected_thresholds=Thresholds(),
        )
        self.assertEqual(document["manifest_id"], clearance.manifest_id)
        self.assertEqual(1, clearance.item_count)

    def test_a_positive_result_is_overridable_by_a_named_human(self):
        document = manifest([SENSITIVE], overrides={SENSITIVE.media_id: OVERRIDE})
        assert_valid_clearance(self, document)
        self.assertEqual("blocked", document["items"][0]["verdict"])
        clearance = guard_print(
            document,
            media_ids=[SENSITIVE.media_id],
            evidence_ids={SENSITIVE.media_id: SENSITIVE.evidence_id},
        )
        self.assertEqual((SENSITIVE.media_id,), clearance.overridden_media_ids)

    def test_the_classifier_actually_separates_the_two_items(self):
        """Otherwise every assertion above is about a constant."""
        verdicts = classifier().classify([BENIGN, SENSITIVE]).verdicts
        self.assertEqual("cleared", verdicts[0]["verdict"])
        self.assertEqual("blocked", verdicts[1]["verdict"])
        self.assertLess(verdicts[0]["scores"]["explicit"], 0.3)
        self.assertGreaterEqual(verdicts[1]["scores"]["explicit"], 0.3)


class TestEveryDenial(unittest.TestCase):
    def setUp(self):
        self.good = manifest([BENIGN])
        self.ids = [BENIGN.media_id]
        self.evidence = {BENIGN.media_id: BENIGN.evidence_id}

    def assertDenied(self, code, document, **kwargs):
        options = {"media_ids": self.ids, "evidence_ids": self.evidence, **kwargs}
        with self.assertRaises(PublicationBlocked) as caught:
            guard_print(document, **options)
        self.assertEqual(code, caught.exception.code, caught.exception.detail)

    def test_missing(self):
        self.assertDenied("clearance_missing", None)

    def test_not_an_object(self):
        self.assertDenied("clearance_unparseable", ["not", "a", "manifest"])

    def test_unknown_manifest_version(self):
        document = rehash({**self.good, "manifest_version": 2})
        self.assertDenied("unknown_manifest_version", document)

    def test_unknown_schema_version(self):
        document = rehash({**self.good, "schema_version": "v1"})
        self.assertDenied("unknown_schema_version", document)

    def test_wrong_sink(self):
        """A share clearance presented to the print boundary."""
        document = manifest([BENIGN], sink="share")
        self.assertDenied("sink_mismatch", document)

    def test_a_print_clearance_does_not_clear_a_share(self):
        with self.assertRaises(PublicationBlocked) as caught:
            guard_share(self.good, media_ids=self.ids, evidence_ids=self.evidence)
        self.assertEqual("sink_mismatch", caught.exception.code)

    def test_transposed_class_order(self):
        document = copy.deepcopy(self.good)
        document["classifier"]["class_order"] = [
            "suggestive",
            "explicit",
            "medical_or_artistic",
        ]
        self.assertDenied("class_order_mismatch", rehash(document))

    def test_development_load_mode(self):
        document = manifest([BENIGN], load_mode="development")
        self.assertDenied("development_load_mode", document)

    def test_a_different_classifier(self):
        self.assertDenied(
            "classifier_mismatch",
            self.good,
            expected_model={**MODEL_REF, "config_blake3": "a" * 64},
        )

    def test_a_different_threshold(self):
        self.assertDenied(
            "threshold_mismatch",
            self.good,
            expected_thresholds=Thresholds(0.5, 0.5, 0.5),
        )

    def test_an_item_the_publication_does_not_contain(self):
        self.assertDenied(
            "item_set_mismatch",
            self.good,
            media_ids=[SENSITIVE.media_id],
            evidence_ids={SENSITIVE.media_id: SENSITIVE.evidence_id},
        )

    def test_a_publication_item_with_no_verdict(self):
        self.assertDenied(
            "item_set_mismatch",
            self.good,
            media_ids=[BENIGN.media_id, SENSITIVE.media_id],
            evidence_ids={
                BENIGN.media_id: BENIGN.evidence_id,
                SENSITIVE.media_id: SENSITIVE.evidence_id,
            },
        )

    def test_a_reordered_book(self):
        """Same set, different sequence. A set comparison would accept this."""
        second = Candidate(media_id=hex64("second"), evidence_id=hex64("second-proxy"))
        classifier_with_second = SafetyClassifier(
            head=synthetic_head(),
            calibration=synthetic_calibration(),
            embedder=embedder_over(
                {
                    BENIGN.evidence_id: axis(900, 0.0),
                    second.evidence_id: axis(901, 0.0),
                }
            ),
        )
        document = build_manifest(
            classifier_with_second.classify([BENIGN, second]),
            sink="print",
            created_at=NOW,
            ran_at=RAN_AT,
            model=MODEL_REF,
            thresholds=Thresholds(),
            load_mode="release",
        )
        with self.assertRaises(PublicationBlocked) as caught:
            guard_print(
                document,
                media_ids=[second.media_id, BENIGN.media_id],
                evidence_ids={
                    BENIGN.media_id: BENIGN.evidence_id,
                    second.media_id: second.evidence_id,
                },
            )
        self.assertEqual("item_order_mismatch", caught.exception.code)

    def test_a_duplicate_item(self):
        document = copy.deepcopy(self.good)
        document["items"].append(copy.deepcopy(document["items"][0]))
        document["decision"]["item_count"] = 2
        document["decision"]["cleared_count"] = 2
        self.assertDenied(
            "duplicate_item",
            rehash(document),
            media_ids=[BENIGN.media_id, BENIGN.media_id],
        )

    def test_a_regenerated_proxy(self):
        self.assertDenied(
            "evidence_stale",
            self.good,
            evidence_ids={BENIGN.media_id: hex64("proxy-regenerated")},
        )

    def test_an_unknown_verdict_string(self):
        document = copy.deepcopy(self.good)
        document["items"][0]["verdict"] = "probably_fine"
        self.assertDenied("unknown_verdict", rehash(document))

    def test_a_cleared_verdict_over_a_score_above_the_threshold(self):
        """The producer lied, or applied a rule this verifier does not know."""
        document = copy.deepcopy(self.good)
        document["items"][0]["scores"]["explicit"] = 0.9
        self.assertDenied("verdict_disagrees_with_scores", rehash(document))

    def test_a_determinate_verdict_with_no_scores(self):
        document = copy.deepcopy(self.good)
        document["items"][0]["scores"] = None
        self.assertDenied("scores_missing", rehash(document))

    def test_a_blocked_item_with_no_override(self):
        document = manifest([SENSITIVE])
        with self.assertRaises(PublicationBlocked) as caught:
            guard_print(
                document,
                media_ids=[SENSITIVE.media_id],
                evidence_ids={SENSITIVE.media_id: SENSITIVE.evidence_id},
            )
        self.assertEqual("blocked_without_override", caught.exception.code)

    def test_an_override_nobody_owns(self):
        document = manifest(
            [SENSITIVE], overrides={SENSITIVE.media_id: {**OVERRIDE, "decided_by": " "}}
        )
        with self.assertRaises(PublicationBlocked) as caught:
            guard_print(
                document,
                media_ids=[SENSITIVE.media_id],
                evidence_ids={SENSITIVE.media_id: SENSITIVE.evidence_id},
            )
        self.assertEqual("override_unattributed", caught.exception.code)

    def test_an_override_that_tries_to_outlive_its_publication(self):
        document = manifest(
            [SENSITIVE],
            overrides={SENSITIVE.media_id: {**OVERRIDE, "scope": "always_allow_item"}},
        )
        with self.assertRaises(PublicationBlocked) as caught:
            guard_print(
                document,
                media_ids=[SENSITIVE.media_id],
                evidence_ids={SENSITIVE.media_id: SENSITIVE.evidence_id},
            )
        self.assertEqual("override_scope_invalid", caught.exception.code)

    def test_an_override_smuggled_onto_an_indeterminate_item(self):
        """The single most important rule in the contract file."""
        document = copy.deepcopy(self.good)
        document["items"][0].update(
            {
                "verdict": "indeterminate",
                "scores": None,
                "indeterminate_reason": "model_unavailable",
                "override": OVERRIDE,
            }
        )
        document["decision"] = {
            "cleared_for_publication": True,
            "item_count": 1,
            "cleared_count": 0,
            "blocked_count": 0,
            "indeterminate_count": 1,
            "denied_reason": None,
        }
        self.assertDenied("override_on_indeterminate", rehash(document))

    def test_a_decision_block_that_lies_about_its_items(self):
        document = copy.deepcopy(manifest([SENSITIVE]))
        document["decision"]["cleared_for_publication"] = True
        document["decision"]["blocked_count"] = 0
        document["decision"]["cleared_count"] = 1
        with self.assertRaises(PublicationBlocked) as caught:
            guard_print(
                rehash(document),
                media_ids=[SENSITIVE.media_id],
                evidence_ids={SENSITIVE.media_id: SENSITIVE.evidence_id},
            )
        self.assertEqual("blocked_without_override", caught.exception.code)

    def test_an_edited_manifest_that_was_not_re_signed(self):
        document = copy.deepcopy(self.good)
        document["sink_detail"] = "a different vendor entirely"
        self.assertDenied("manifest_id_mismatch", document)

    def test_the_verifier_denies_rather_than_raising(self):
        """A verifier that throws is caught upstream and reads as a pass."""

        class Hostile(dict):
            def get(self, key, default=None):
                if key == "items":
                    raise RuntimeError("boom")
                return super().get(key, default)

        with self.assertRaises(PublicationBlocked) as caught:
            verify_clearance(
                Hostile(self.good), sink="print", media_ids=self.ids
            )
        self.assertEqual("verifier_exception", caught.exception.code)

    def test_an_empty_publication_is_not_vacuously_cleared(self):
        with self.assertRaises(PublicationBlocked) as caught:
            guard_print(self.good, media_ids=[])
        self.assertEqual("publication_empty", caught.exception.code)


if __name__ == "__main__":
    unittest.main()
