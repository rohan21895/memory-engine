"""The share boundary refuses without a clearance, and refuses a print one.

The interesting test in this file is
`test_a_print_clearance_does_not_authorise_a_share`. Every other refusal here is
the same verifier already exercised in packages/safety-gate; the sink is the one
thing this boundary owns, and it is the one an implementer is most likely to get
wrong -- a photograph that went into a family's own printed book is intuitively
"already approved", and a public link is a completely different audience.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

SERVICE_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = SERVICE_ROOT.parent.parent
for path in (SERVICE_ROOT, REPO_ROOT / "packages" / "safety-gate"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from memory_engine_safety.canonical import manifest_id  # noqa: E402

from memory_engine_share import (  # noqa: E402
    PublicationBlocked,
    ShareAuthorization,
    ShareRequest,
    authorise_share,
)

MEDIA = ("1" * 64, "2" * 64)
PROXIES = {MEDIA[0]: "a" * 64, MEDIA[1]: "b" * 64}


def clearance(sink: str = "share", *, verdicts=("cleared", "cleared"), override=None):
    items = []
    for media_id, verdict in zip(MEDIA, verdicts, strict=True):
        item = {
            "media_id": media_id,
            "evidence_id": PROXIES[media_id],
            "verdict": verdict,
        }
        if verdict == "indeterminate":
            item["scores"] = None
            item["indeterminate_reason"] = "load_gate_denied"
            item["override"] = None
        else:
            item["scores"] = {
                "explicit": 0.55 if verdict == "blocked" else 0.01,
                "suggestive": 0.02,
                "medical_or_artistic": 0.01,
            }
            if verdict == "blocked":
                item["override"] = override
        items.append(item)

    document = {
        "schema_version": "v0",
        "manifest_version": 1,
        "created_at": "2026-08-18T11:15:00+05:30",
        "sink": sink,
        "sink_detail": "public link, monsoon-weekend reel",
        "classifier": {
            "model": {
                "model_id": "nsfw-siglip-head",
                "version": "1.0.0",
                "weights_blake3": "d" * 64,
                "config_blake3": "c" * 64,
                "runtime": "onnxruntime_coreml",
                "precision": "fp32",
            },
            "ran_at": "2026-08-18T11:14:00+05:30",
            "class_order": ["explicit", "suggestive", "medical_or_artistic"],
            "load_mode": "release",
        },
        "thresholds": {
            "explicit": 0.3,
            "suggestive": 0.3,
            "medical_or_artistic": 0.3,
        },
        "items": items,
    }

    def permitted(item):
        if item["verdict"] == "cleared":
            return True
        if item["verdict"] != "blocked":
            return False
        recorded = item.get("override")
        return bool(
            recorded
            and recorded.get("scope") == "item_and_sink"
            and str(recorded.get("decided_by", "")).strip()
        )

    document["decision"] = {
        "cleared_for_publication": all(permitted(item) for item in items),
        "item_count": len(items),
        "cleared_count": sum(1 for i in items if i["verdict"] == "cleared"),
        "blocked_count": sum(1 for i in items if i["verdict"] == "blocked"),
        "indeterminate_count": sum(
            1 for i in items if i["verdict"] == "indeterminate"
        ),
        "denied_reason": None,
    }
    document["manifest_id"] = manifest_id(document)
    return document


class TestShareBoundary(unittest.TestCase):
    def request(self, **overrides):
        return ShareRequest.over(
            MEDIA, evidence_ids=PROXIES, recipient_scope="public_link", **overrides
        )

    def test_a_complete_clearance_authorises(self):
        """First, so every refusal below means something."""
        authorization = authorise_share(clearance(), self.request())
        self.assertIsInstance(authorization, ShareAuthorization)
        self.assertEqual("share", authorization.sink)
        self.assertEqual(MEDIA, authorization.media_ids)
        self.assertEqual((), authorization.overridden_media_ids)

    def test_no_clearance_blocks(self):
        with self.assertRaises(PublicationBlocked) as caught:
            authorise_share(None, self.request())
        self.assertEqual("clearance_missing", caught.exception.code)

    def test_a_print_clearance_does_not_authorise_a_share(self):
        """The photograph is the same; the audience is not.

        A private printed book goes to the family. A share link goes to whoever
        has the link, and the user approved neither photograph by photograph.
        There is no argument to `authorise_share` that would accept this.
        """
        with self.assertRaises(PublicationBlocked) as caught:
            authorise_share(clearance(sink="print"), self.request())
        self.assertEqual("sink_mismatch", caught.exception.code)

    def test_one_indeterminate_item_denies_the_whole_share(self):
        with self.assertRaises(PublicationBlocked) as caught:
            authorise_share(
                clearance(verdicts=("cleared", "indeterminate")), self.request()
            )
        self.assertEqual("indeterminate_item", caught.exception.code)

    def test_a_blocked_item_needs_a_named_human(self):
        with self.assertRaises(PublicationBlocked) as caught:
            authorise_share(clearance(verdicts=("cleared", "blocked")), self.request())
        self.assertEqual("blocked_without_override", caught.exception.code)

    def test_an_override_is_recorded_on_the_authorisation(self):
        authorization = authorise_share(
            clearance(
                verdicts=("cleared", "blocked"),
                override={
                    "decided_at": "2026-08-18T11:20:00+05:30",
                    "decided_by": "rohan",
                    "scope": "item_and_sink",
                    "note": None,
                },
            ),
            self.request(),
        )
        self.assertEqual((MEDIA[1],), authorization.overridden_media_ids)

    def test_a_regenerated_proxy_is_stale_evidence(self):
        stale = dict(PROXIES)
        stale[MEDIA[1]] = "f" * 64
        with self.assertRaises(PublicationBlocked) as caught:
            authorise_share(
                clearance(),
                ShareRequest.over(MEDIA, evidence_ids=stale),
            )
        self.assertEqual("evidence_stale", caught.exception.code)

    def test_sharing_more_than_was_cleared_blocks(self):
        with self.assertRaises(PublicationBlocked) as caught:
            authorise_share(
                clearance(), ShareRequest.over((*MEDIA, "9" * 64))
            )
        self.assertEqual("item_set_mismatch", caught.exception.code)

    def test_there_is_no_bypass_parameter(self):
        """A `force=True` would be the whole failure, so it must not exist."""
        import inspect

        parameters = set(inspect.signature(authorise_share).parameters)
        self.assertEqual({"clearance", "request", "expected_model"}, parameters)


if __name__ == "__main__":
    unittest.main()
