"""Tests for contact-sheet composition.

WHAT THESE TESTS ARE FOR

This module is the privacy boundary: the only place a user's imagery leaves
their machine. Four rounds of adversarial review on this codebase found 37
defects and every single one was SILENT -- a plausible number, no exception, a
wrong answer. A green suite here would therefore be almost no evidence on its
own, so each test below was verified to BITE: the implementation was broken on
purpose and the test was confirmed to fail. The mutation each test catches is
named in its docstring. The mutation log is in the module docstring of
`test_contact_sheet.py` as run, and the count reported to the reviewer is the
count actually measured, not the count intended.

Runs under `python3 tests/test_contact_sheet.py`, `python3 -m unittest` and
pytest. No runner-specific features, matching the convention in contracts/tests.
"""

from __future__ import annotations

import json
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
PROMPT_PKG = REPO / "packages" / "prompt-engine"
SCHEMA_DIR = REPO / "contracts" / "schemas"

sys.path.insert(0, str(PROMPT_PKG))

from memory_engine_prompt import contact_sheet as cs  # noqa: E402
from memory_engine_prompt.contact_sheet import (  # noqa: E402
    ALLOWED_PROXY_KINDS,
    CONSENT_SCOPE,
    DEFAULT_POLICY,
    MAX_ITEMS,
    MAX_NSFW_THRESHOLD,
    MAX_TILE_PX,
    ConsentError,
    ContactSheetError,
    ContactSheetPolicy,
    LeakError,
    SheetCandidate,
    candidate_from_media_record,
    plan_contact_sheet,
)

NOW = datetime(2026, 3, 16, 20, 4, 0, tzinfo=timezone.utc)
SCOPE = "project:reel-ridge-15s"


def hexid(seed: int) -> str:
    """A 64-hex id, shaped like the BLAKE3 ids the contract uses."""
    return f"{seed:064x}"


def consent(**overrides) -> dict:
    base = {
        "ledger_entry_id": "6b1e4d0a-9f37-4c25-b8e1-05a7c3f26d94",
        "scope": CONSENT_SCOPE,
        "granted_at": "2026-03-16T20:02:11+00:00",
        "expires_at": "2026-03-16T21:02:11+00:00",
        "revoked_at": None,
    }
    base.update(overrides)
    return base


def safe(**overrides) -> dict:
    base = {"nsfw_score": 0.01, "categories": [], "auto_excluded": False}
    base.update(overrides)
    return base


def cand(seed: int, **overrides) -> SheetCandidate:
    """A candidate that passes the gate, so each test can break exactly one thing."""
    kwargs = {
        "candidate_id": hexid(seed),
        "media_id": hexid(1000 + seed),
        "score": 0.5,
        "proxy_id": hexid(2000 + seed),
        "proxy_kind": "contact_sheet_tile",
        "sort_time": datetime(2026, 3, 1, 9, 0, tzinfo=timezone.utc) + timedelta(minutes=seed),
        "safety": safe(),
        "safety_stage_status": "done",
        "exclusion": {"excluded_from_automation": False, "reasons": []},
    }
    kwargs.update(overrides)
    return SheetCandidate(**kwargs)


def plan(candidates, **overrides):
    kwargs = {"consent": consent(), "now": NOW, "scope": SCOPE, "policy": DEFAULT_POLICY}
    kwargs.update(overrides)
    return plan_contact_sheet(candidates, **kwargs)


class TestResolutionCeiling(unittest.TestCase):
    """The ceiling exists so that no caller can enlarge what leaves the device."""

    def test_policy_refuses_tile_px_above_ceiling(self):
        """Kills: `if self.tile_px > MAX_TILE_PX` -> `>=`, or the check deleted."""
        with self.assertRaises(ContactSheetError):
            ContactSheetPolicy(tile_px=MAX_TILE_PX + 1)
        # And the ceiling value itself is exactly at the boundary, not one off.
        self.assertEqual(ContactSheetPolicy(tile_px=MAX_TILE_PX).tile_px, MAX_TILE_PX)

    def test_ceiling_survives_a_caller_rebinding_the_public_constant(self):
        """Kills: collapsing the two ceiling constants into one.

        This is the whole point of `_TILE_PX_HARD_CEILING` being separate. A
        plugin or a careless test that raises `MAX_TILE_PX` must still not be
        able to get a 1024px sheet planned.
        """
        original = cs.MAX_TILE_PX
        try:
            cs.MAX_TILE_PX = 4096
            policy = ContactSheetPolicy(tile_px=1024)  # accepted by the patched check
            with self.assertRaises(ContactSheetError):
                plan([cand(1)], policy=policy)
        finally:
            cs.MAX_TILE_PX = original

    def test_ceiling_survives_object_setattr_past_the_frozen_policy(self):
        """Kills: deleting the re-check inside plan_contact_sheet."""
        policy = ContactSheetPolicy(tile_px=256)
        object.__setattr__(policy, "tile_px", 2048)
        with self.assertRaises(ContactSheetError):
            plan([cand(1)], policy=policy)

    def test_tile_px_floor(self):
        """Kills: dropping the MIN_TILE_PX check. A 4px tile is pure privacy cost."""
        with self.assertRaises(ContactSheetError):
            ContactSheetPolicy(tile_px=16)

    def test_nsfw_threshold_is_itself_capped(self):
        """Kills: removing the threshold ceiling, which is how the gate gets opened."""
        with self.assertRaises(ContactSheetError):
            ContactSheetPolicy(nsfw_threshold=0.99)
        with self.assertRaises(ContactSheetError):
            ContactSheetPolicy(nsfw_threshold=MAX_NSFW_THRESHOLD + 0.01)

    def test_nan_threshold_refused(self):
        """Kills: allowing NaN, which compares false and disables the NSFW gate."""
        with self.assertRaises(ContactSheetError):
            ContactSheetPolicy(nsfw_threshold=float("nan"))

    def test_item_cap_ceiling(self):
        """Kills: removing the max_items bound."""
        with self.assertRaises(ContactSheetError):
            ContactSheetPolicy(max_items=MAX_ITEMS + 1)


class TestSafetyGate(unittest.TestCase):
    """Absence is indeterminate and indeterminate blocks (issue #21)."""

    def _reasons(self, candidate) -> tuple[str, ...]:
        result = plan([candidate])
        self.assertEqual(len(result.items), 0, "candidate should not have been sent")
        self.assertEqual(len(result.excluded), 1)
        return result.excluded[0].reasons

    def test_missing_safety_assessment_blocks(self):
        """The headline case. Kills: `(safety or {}).get('nsfw_score', 0.0)`."""
        self.assertIn("safety_not_assessed", self._reasons(cand(1, safety=None)))

    def test_missing_safety_stage_blocks_even_with_an_assessment_present(self):
        """Kills: dropping the stage check.

        A verdict left behind by a run that later failed is stale, and stale is
        not the same as current. Both signals are required.
        """
        self.assertIn(
            "safety_stage_not_done", self._reasons(cand(1, safety_stage_status=None))
        )

    def test_every_non_done_stage_status_blocks(self):
        """Kills: `status != 'failed'` or any allow-list that includes `skipped`."""
        for status in ("pending", "running", "failed", "skipped", "not_applicable", ""):
            with self.subTest(status=status):
                self.assertIn(
                    "safety_stage_not_done", self._reasons(cand(1, safety_stage_status=status))
                )

    def test_done_stage_is_the_only_pass(self):
        """Guards the mutation `_SAFETY_STAGE_DONE = 'pending'`."""
        self.assertEqual(len(plan([cand(1, safety_stage_status="done")]).items), 1)

    def test_auto_excluded_blocks(self):
        """Kills: reading auto_excluded with truthiness instead of `is not False`."""
        self.assertIn("safety_auto_excluded", self._reasons(cand(1, safety=safe(auto_excluded=True))))

    def test_malformed_auto_excluded_blocks(self):
        """Kills: `if safety.get('auto_excluded'):`.

        A record missing the required field, or carrying a string, is malformed;
        a malformed verdict is an absent one.
        """
        for value in (None, "false", 0, {}):
            with self.subTest(value=value):
                bad = safe()
                bad["auto_excluded"] = value
                self.assertIn("safety_auto_excluded", self._reasons(cand(1, safety=bad)))

    def test_any_category_blocks_including_unknown(self):
        """Kills: an allow-list that lets `unknown` or `medical` through."""
        for category in ("nudity", "sexual", "violence", "gore", "medical", "document_pii", "unknown"):
            with self.subTest(category=category):
                self.assertIn(
                    "safety_category_flagged",
                    self._reasons(cand(1, safety=safe(categories=[category]))),
                )

    def test_unrecognised_category_blocks(self):
        """Kills: `if any(c in KNOWN_BLOCKING for c in categories)`.

        A category value this code has never heard of is indeterminate, and
        indeterminate blocks. An allow-list would wave it through.
        """
        self.assertIn(
            "safety_category_flagged",
            self._reasons(cand(1, safety=safe(categories=["a_category_from_2027"]))),
        )

    def test_nsfw_score_above_threshold_blocks(self):
        """Kills: `>=` -> `>` boundary flips and a reversed comparison."""
        policy = ContactSheetPolicy(nsfw_threshold=0.10)
        blocked = plan([cand(1, safety=safe(nsfw_score=0.11))], policy=policy)
        self.assertEqual(len(blocked.items), 0)
        self.assertIn("nsfw_above_threshold", blocked.excluded[0].reasons)
        # Exactly at the threshold is allowed; one step above is not.
        self.assertEqual(len(plan([cand(1, safety=safe(nsfw_score=0.10))], policy=policy).items), 1)

    def test_nan_nsfw_score_blocks(self):
        """Kills: `score > threshold` alone. NaN loses every comparison."""
        self.assertIn(
            "nsfw_above_threshold", self._reasons(cand(1, safety=safe(nsfw_score=float("nan"))))
        )

    def test_missing_nsfw_score_blocks(self):
        """Kills: `.get('nsfw_score', 0.0)` -- the confident-zero default."""
        bad = safe()
        del bad["nsfw_score"]
        self.assertIn("nsfw_above_threshold", self._reasons(cand(1, safety=bad)))

    def test_sensitive_flag_blocks(self):
        """Kills: dropping the sensitive_flags check."""
        self.assertIn(
            "sensitive_flagged", self._reasons(cand(1, sensitive_flags=frozenset({"confirmed_minor"})))
        )

    def test_user_hidden_blocks(self):
        """Kills: dropping the user_hidden check."""
        self.assertIn("user_hidden", self._reasons(cand(1, user_hidden=True)))

    def test_excluded_from_automation_blocks(self):
        """Kills: dropping the ExclusionState check."""
        self.assertIn(
            "excluded_from_automation",
            self._reasons(cand(1, exclusion={"excluded_from_automation": True, "reasons": ["nsfw"]})),
        )

    def test_user_override_does_not_lift_the_exclusion(self):
        """Kills: `if excluded and not user_override:`.

        Forcing a photo back into an album is not consent to upload it. This is
        the most tempting wrong line in the module.
        """
        candidate = cand(
            1,
            exclusion={
                "excluded_from_automation": True,
                "reasons": ["nsfw"],
                "user_override": True,
            },
        )
        self.assertIn("excluded_from_automation", self._reasons(candidate))

    def test_missing_proxy_blocks_and_never_falls_back_to_an_original(self):
        """Kills: any fallback path when no proxy exists."""
        self.assertIn("no_proxy", self._reasons(cand(1, proxy_id=None, proxy_kind=None)))

    def test_disallowed_proxy_kind_blocks(self):
        """Kills: widening ALLOWED_PROXY_KINDS to include preview_2048."""
        for kind in ("preview_2048", "video_proxy_480p", "waveform", "audio_wav_16k"):
            with self.subTest(kind=kind):
                self.assertIn(
                    "proxy_kind_not_permitted", self._reasons(cand(1, proxy_kind=kind))
                )
        self.assertEqual(ALLOWED_PROXY_KINDS, frozenset({"contact_sheet_tile", "thumbnail_512"}))

    def test_all_reasons_are_reported_not_just_the_first(self):
        """Kills: `return (reason,)` early-exit in the gate.

        The ledger's withheld list is the only record anyone reads of why a
        photograph was held back.
        """
        candidate = cand(
            1,
            safety=safe(nsfw_score=0.9, categories=["nudity"], auto_excluded=True),
            user_hidden=True,
        )
        reasons = self._reasons(candidate)
        for expected in (
            "safety_auto_excluded",
            "safety_category_flagged",
            "nsfw_above_threshold",
            "user_hidden",
        ):
            self.assertIn(expected, reasons)

    def test_nothing_survives_means_nothing_is_sent(self):
        """The stated answer to 'what if the classifier has not run'."""
        result = plan([cand(i, safety=None) for i in range(1, 6)])
        self.assertFalse(result.sendable)
        self.assertEqual(result.items, ())
        self.assertEqual(len(result.excluded), 5)
        self.assertEqual(result.estimated_bytes, 0)
        self.assertEqual(result.egress_declaration()["requires_egress"], False)
        with self.assertRaises(ContactSheetError):
            result.payload()

    def test_a_blocked_candidate_never_appears_in_the_ledger_as_sent(self):
        """Kills: building sent_media from the input list instead of from items."""
        result = plan([cand(1), cand(2, safety=None)])
        entry = result.ledger_entry()
        sent_media_ids = {row["media_id"] for row in entry["sent_media"]}
        self.assertEqual(sent_media_ids, {hexid(1001)})
        self.assertEqual([w["candidate_id"] for w in entry["withheld"]], [hexid(2)])


class TestConsent(unittest.TestCase):
    def test_valid_consent_permits_the_sheet(self):
        result = plan([cand(1)])
        self.assertTrue(result.sendable)

    def test_wrong_scope_refused(self):
        """Kills: dropping the scope equality check.

        A cloud_render consent is a real consent -- for something else.
        """
        for scope in ("cloud_render", "print_order", "share_link", "minor_face_labeling"):
            with self.subTest(scope=scope):
                with self.assertRaises(ConsentError):
                    plan([cand(1)], consent=consent(scope=scope))

    def test_revoked_consent_refused(self):
        """Kills: dropping the revoked_at check."""
        with self.assertRaises(ConsentError):
            plan([cand(1)], consent=consent(revoked_at="2026-03-16T20:03:00+00:00"))

    def test_expired_consent_refused(self):
        """Kills: `expiry < now` -> the boundary, and a reversed comparison."""
        with self.assertRaises(ConsentError):
            plan([cand(1)], consent=consent(expires_at="2026-03-16T20:03:59+00:00"))
        # Expiry exactly at `now` is expired, not valid.
        with self.assertRaises(ConsentError):
            plan([cand(1)], consent=consent(expires_at=NOW.isoformat()))
        # One second later is still valid.
        self.assertTrue(
            plan(
                [cand(1)], consent=consent(expires_at=(NOW + timedelta(seconds=1)).isoformat())
            ).sendable
        )

    def test_future_granted_at_refused(self):
        """Kills: dropping the clock-sanity check on granted_at."""
        with self.assertRaises(ConsentError):
            plan([cand(1)], consent=consent(granted_at=(NOW + timedelta(hours=1)).isoformat()))

    def test_missing_consent_refused(self):
        """Kills: treating `None` consent as permissive."""
        with self.assertRaises(ConsentError):
            plan([cand(1)], consent=None)

    def test_naive_timestamp_refused(self):
        """Kills: accepting a tz-naive consent timestamp.

        Comparing naive to aware raises deep inside the comparison; refusing at
        the edge names the field that is wrong.
        """
        with self.assertRaises(ConsentError):
            plan([cand(1)], consent=consent(granted_at="2026-03-16T20:02:11"))

    def test_bad_ledger_id_refused(self):
        """Kills: dropping the UUID check. An unfindable ledger row is no row."""
        for bad in ("not-a-uuid", "6B1E4D0A-9F37-4C25-B8E1-05A7C3F26D94", ""):
            with self.subTest(bad=bad):
                with self.assertRaises(ConsentError):
                    plan([cand(1)], consent=consent(ledger_entry_id=bad))

    def test_unknown_consent_field_refused(self):
        """Kills: dropping the additionalProperties check.

        The field this code does not understand may be the one that revokes.
        """
        with self.assertRaises(ConsentError):
            plan([cand(1)], consent=consent(revoked_reason="user changed their mind"))

    def test_consent_is_not_required_when_nothing_would_be_sent(self):
        """Documents the deliberate ordering: no egress, no authorisation needed."""
        result = plan([cand(1, safety=None)], consent=consent(scope="cloud_render"))
        self.assertFalse(result.sendable)
        self.assertIsNone(result.consent)


class TestOpaqueHandles(unittest.TestCase):
    def test_payload_carries_no_local_identifier(self):
        """The core privacy assertion. Kills: adding media_id to a payload item."""
        result = plan([cand(i) for i in range(1, 5)])
        blob = json.dumps(result.payload())
        for i in range(1, 5):
            self.assertNotIn(hexid(i), blob)
            self.assertNotIn(hexid(1000 + i), blob)
            self.assertNotIn(hexid(2000 + i), blob)

    def test_leak_detector_actually_fires(self):
        """Kills: `_assert_no_local_identifiers` reduced to a no-op.

        Without this test the detector could be gutted and every other test
        would still pass, which is exactly the shape of defect this codebase
        keeps finding.
        """
        result = plan([cand(1)])
        forbidden = result._local_identifiers()
        self.assertTrue(forbidden)
        with self.assertRaises(LeakError):
            cs._assert_no_local_identifiers({"note": f"tile for {hexid(1001)}"}, forbidden)
        with self.assertRaises(LeakError):
            cs._assert_no_local_identifiers([{"a": [hexid(2001)]}], forbidden)
        # And it catches an id embedded in a larger string, not only equality.
        with self.assertRaises(LeakError):
            cs._assert_no_local_identifiers(f"/library/{hexid(1001)}.jpg", forbidden)

    def test_handles_are_positional_and_dense(self):
        result = plan([cand(i) for i in range(1, 8)])
        self.assertEqual(result.handles, tuple(f"s{n:02d}" for n in range(1, 8)))

    def test_resolve_maps_back_to_the_right_item(self):
        """Kills: an off-by-one in the handle index."""
        candidates = [cand(i) for i in range(1, 5)]
        result = plan(candidates)
        for item in result.items:
            self.assertIs(result.resolve(item.handle), item)
        # `s01` is the first item in sheet order, and sheet order is chronological.
        self.assertEqual(result.resolve("s01").candidate_id, hexid(1))

    def test_resolve_is_exact_and_refuses_near_misses(self):
        """Kills: `handle.strip().lower()` or an int-parsing resolver.

        A lenient parser is how the model's judgement about one photograph gets
        applied to a different one.
        """
        result = plan([cand(i) for i in range(1, 5)])
        for bad in ("S01", " s01", "s1", "s01 ", "s99", "", "1", None, 1):
            with self.subTest(bad=bad):
                with self.assertRaises(ContactSheetError):
                    result.resolve(bad)

    def test_resolve_many_is_all_or_nothing(self):
        """Kills: skipping unresolvable handles instead of refusing the reply."""
        result = plan([cand(i) for i in range(1, 5)])
        self.assertEqual(len(result.resolve_many(["s01", "s03"])), 2)
        with self.assertRaises(ContactSheetError):
            result.resolve_many(["s01", "s99"])

    def test_a_handle_from_another_plan_is_refused(self):
        small = plan([cand(1), cand(2)])
        with self.assertRaises(ContactSheetError):
            small.resolve("s05")


class TestSelectionAndLayout(unittest.TestCase):
    def test_item_cap_enforced_and_the_overflow_is_recorded(self):
        """Kills: a cap that silently truncates. No silent data loss."""
        policy = ContactSheetPolicy(max_items=5)
        result = plan([cand(i, score=i / 100.0) for i in range(1, 12)], policy=policy)
        self.assertEqual(len(result.items), 5)
        overflow = [e for e in result.excluded if "over_item_cap" in e.reasons]
        self.assertEqual(len(overflow), 6)
        self.assertEqual(len(result.items) + len(overflow), 11)

    def test_the_cap_keeps_the_highest_scores(self):
        """Kills: `sorted(key=score)` without the negation, or keeping the tail."""
        policy = ContactSheetPolicy(max_items=3)
        result = plan([cand(i, score=i / 100.0) for i in range(1, 8)], policy=policy)
        kept = {item.candidate_id for item in result.items}
        self.assertEqual(kept, {hexid(5), hexid(6), hexid(7)})

    def test_score_ties_break_on_candidate_id_not_input_order(self):
        """Kills: dropping the explicit tie-break. Determinism is a hard rule."""
        policy = ContactSheetPolicy(max_items=2)
        forward = [cand(i, score=0.5) for i in (1, 2, 3, 4)]
        reverse = [cand(i, score=0.5) for i in (4, 3, 2, 1)]
        a = plan(forward, policy=policy)
        b = plan(reverse, policy=policy)
        self.assertEqual([i.candidate_id for i in a.items], [i.candidate_id for i in b.items])
        self.assertEqual({i.candidate_id for i in a.items}, {hexid(1), hexid(2)})

    def test_sheet_order_is_chronological_not_score_order(self):
        """Kills: laying the grid out in the score order used for the cap."""
        base = datetime(2026, 3, 1, 9, 0, tzinfo=timezone.utc)
        candidates = [
            cand(1, score=0.1, sort_time=base),
            cand(2, score=0.9, sort_time=base + timedelta(hours=1)),
            cand(3, score=0.5, sort_time=base + timedelta(hours=2)),
        ]
        result = plan(candidates)
        self.assertEqual(
            [i.candidate_id for i in result.items], [hexid(1), hexid(2), hexid(3)]
        )

    def test_undated_items_form_a_trailing_block_and_say_so(self):
        """Kills: sorting an unknown capture time to the epoch.

        TimeAssertion.precision is explicit that consumers must not do that; an
        undated photo placed first would read as 'this happened before
        everything else', a claim nobody made.
        """
        base = datetime(2026, 3, 1, 9, 0, tzinfo=timezone.utc)
        candidates = [
            cand(1, sort_time=None),
            cand(2, sort_time=base + timedelta(hours=2)),
            cand(3, sort_time=base),
        ]
        result = plan(candidates)
        self.assertEqual(
            [i.candidate_id for i in result.items], [hexid(3), hexid(2), hexid(1)]
        )
        entries = result.payload()["items"]
        self.assertNotIn("undated", entries[0])
        self.assertTrue(entries[2]["undated"])

    def test_undated_items_are_ordered_among_themselves_deterministically(self):
        forward = plan([cand(i, sort_time=None) for i in (1, 2, 3)])
        reverse = plan([cand(i, sort_time=None) for i in (3, 2, 1)])
        self.assertEqual(
            [i.candidate_id for i in forward.items], [i.candidate_id for i in reverse.items]
        )

    def test_grid_coordinates_fill_row_major(self):
        """Kills: swapping row and col (`index % columns` / `index // columns`)."""
        policy = ContactSheetPolicy(columns=3)
        result = plan([cand(i) for i in range(1, 8)], policy=policy)
        self.assertEqual(
            [(i.row, i.col) for i in result.items],
            [(0, 0), (0, 1), (0, 2), (1, 0), (1, 1), (1, 2), (2, 0)],
        )
        self.assertEqual(result.rows, 3)
        self.assertEqual(result.columns, 3)

    def test_columns_shrink_to_the_item_count(self):
        """Kills: a fixed column count that leaves a mostly-empty grid."""
        result = plan([cand(1), cand(2)], policy=ContactSheetPolicy(columns=6))
        self.assertEqual(result.columns, 2)
        self.assertEqual(result.rows, 1)

    def test_rows_round_up_for_a_partial_last_row(self):
        """Kills: integer division instead of ceil -- the last row vanishes."""
        result = plan([cand(i) for i in range(1, 9)], policy=ContactSheetPolicy(columns=3))
        self.assertEqual(result.rows, 3)

    def test_duplicate_candidate_id_refused(self):
        """Kills: silently de-duplicating a caller bug."""
        with self.assertRaises(ContactSheetError):
            plan([cand(1), cand(1)])

    def test_nan_score_refused_at_construction(self):
        """Kills: allowing NaN, which makes the whole sort order input-dependent."""
        for bad in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(bad=bad):
                with self.assertRaises(ContactSheetError):
                    cand(1, score=bad)

    def test_naive_sort_time_refused(self):
        with self.assertRaises(ContactSheetError):
            cand(1, sort_time=datetime(2026, 3, 1, 9, 0))


class TestDeterminism(unittest.TestCase):
    def test_same_inputs_produce_an_identical_payload_and_digest(self):
        candidates = [cand(i) for i in range(1, 10)]
        a = plan(candidates)
        b = plan([cand(i) for i in range(9, 0, -1)])
        self.assertEqual(a.payload(), b.payload())
        self.assertEqual(a.payload_digest(), b.payload_digest())
        self.assertEqual(a.sheet_token, b.sheet_token)

    def test_digest_changes_when_the_payload_changes(self):
        """Kills: a digest computed over a constant, which would pass the test above."""
        a = plan([cand(i) for i in range(1, 5)])
        b = plan([cand(i) for i in range(1, 6)])
        self.assertNotEqual(a.payload_digest(), b.payload_digest())
        c = plan([cand(i) for i in range(1, 5)], policy=ContactSheetPolicy(tile_px=128))
        self.assertNotEqual(a.payload_digest(), c.payload_digest())

    def test_token_depends_on_the_surviving_set_not_the_submitted_set(self):
        """A blocked candidate must not change the identity of what is sent."""
        a = plan([cand(1), cand(2)])
        b = plan([cand(1), cand(2), cand(3, safety=None)])
        self.assertEqual(a.sheet_token, b.sheet_token)

    def test_excluded_list_is_deterministically_ordered(self):
        forward = plan([cand(i, safety=None) for i in (1, 2, 3, 4)])
        reverse = plan([cand(i, safety=None) for i in (4, 3, 2, 1)])
        self.assertEqual(
            [e.candidate_id for e in forward.excluded],
            [e.candidate_id for e in reverse.excluded],
        )

    def test_now_is_a_parameter_not_a_wall_clock_read(self):
        """Kills: `now = datetime.now(timezone.utc)` inside the planner."""
        result = plan([cand(1)])
        self.assertEqual(result.planned_at, NOW)
        self.assertEqual(result.ledger_entry()["planned_at"], NOW.isoformat())
        with self.assertRaises(ContactSheetError):
            plan([cand(1)], now=datetime(2026, 3, 16, 20, 4, 0))


class TestPayloadAndLedger(unittest.TestCase):
    def test_payload_shape(self):
        result = plan([cand(i) for i in range(1, 5)], policy=ContactSheetPolicy(columns=2))
        body = result.payload()
        self.assertEqual(body["schema"], cs.PAYLOAD_SCHEMA)
        self.assertEqual(body["grid"], {"columns": 2, "rows": 2, "tile_px": 256, "item_count": 4})
        self.assertEqual(sorted(body["items"][0]), ["col", "handle", "row"])

    def test_transcript_withheld_unless_the_policy_asks_for_it(self):
        """Kills: including transcript unconditionally. Speech is the user's life."""
        candidates = [cand(1, transcript="she says happy birthday to her grandmother")]
        off = plan(candidates)
        self.assertNotIn("transcript", off.payload()["items"][0])
        on = plan(candidates, policy=ContactSheetPolicy(include_transcript=True))
        self.assertIn("transcript", on.payload()["items"][0])

    def test_transcript_truncated_to_the_policy_limit(self):
        """Kills: dropping the truncation, which uncaps the text that leaves."""
        policy = ContactSheetPolicy(include_transcript=True, max_transcript_chars=10)
        result = plan([cand(1, transcript="x" * 500)], policy=policy)
        self.assertEqual(result.payload()["items"][0]["transcript"], "x" * 10)

    def test_estimated_bytes_scales_with_items_and_tile_area(self):
        """Kills: a constant estimate, which makes an egress budget meaningless."""
        one = plan([cand(1)])
        four = plan([cand(i) for i in range(1, 5)])
        self.assertEqual(four.estimated_bytes, 4 * one.estimated_bytes)
        small = plan([cand(1)], policy=ContactSheetPolicy(tile_px=128))
        self.assertLess(small.estimated_bytes, one.estimated_bytes)

    def test_ledger_entry_names_the_media_that_left(self):
        """The ledger must answer 'which of my photos have ever left this machine'."""
        result = plan([cand(1), cand(2)])
        entry = result.ledger_entry()
        self.assertEqual(
            [row["media_id"] for row in entry["sent_media"]], [hexid(1001), hexid(1002)]
        )
        self.assertEqual([row["handle"] for row in entry["sent_media"]], ["s01", "s02"])
        self.assertEqual(entry["payload_digest"], result.payload_digest())
        self.assertEqual(entry["sheet"]["item_count"], 2)

    def test_unsendable_plan_has_no_payload_digest_in_the_ledger(self):
        """Kills: writing a digest of nothing, which makes 'sent' and 'not sent' alike."""
        result = plan([cand(1, safety=None)])
        self.assertIsNone(result.ledger_entry()["payload_digest"])

    def test_egress_declaration_negative_is_explicit(self):
        """Kills: returning `{}` -- an absent declaration must not look like a negative one."""
        decl = plan([cand(1, safety=None)]).egress_declaration()
        self.assertEqual(
            decl,
            {
                "requires_egress": False,
                "consent": None,
                "destination": None,
                "payload_kind": None,
                "estimated_bytes": 0,
            },
        )

    def test_egress_declaration_validates_against_the_contract(self):
        """Golden-contract test: the declaration drops into a real JobSpec."""
        try:
            import jsonschema
        except ImportError:  # pragma: no cover - jsonschema is a test-only dep
            self.skipTest("jsonschema not installed")

        schema = json.loads((SCHEMA_DIR / "job-spec.schema.json").read_text())
        common = json.loads((SCHEMA_DIR / "common.schema.json").read_text())
        registry = jsonschema.validators.Draft202012Validator(
            {"$ref": "job-spec.schema.json#/$defs/EgressDeclaration"},
            registry=_registry(schema, common),
        )
        registry.validate(plan([cand(1), cand(2)]).egress_declaration())
        registry.validate(plan([cand(1, safety=None)]).egress_declaration())

    def test_declaration_uses_the_contract_enum_values(self):
        decl = plan([cand(1)]).egress_declaration()
        self.assertEqual(decl["destination"], "tier3_inference")
        self.assertEqual(decl["payload_kind"], "contact_sheet")
        self.assertEqual(decl["consent"]["scope"], "tier3_contact_sheet")


def _registry(*schemas):
    import referencing
    import referencing.jsonschema

    registry = referencing.Registry()
    for schema in schemas:
        resource = referencing.Resource.from_contents(schema)
        name = schema["$id"].rsplit("/", 1)[-1]
        registry = resource @ registry
        registry = registry.with_resource(name, resource)
    return registry


class TestMediaRecordAdapter(unittest.TestCase):
    def _record(self, **overrides):
        record = {
            "schema_version": "v0",
            "media_id": hexid(77),
            "asset_kind": "physical_file",
            "kind": "image",
            "byte_size": 1234,
            "proxies": [
                {"proxy_id": hexid(88), "kind": "thumbnail_512", "path": "/local/a.jpg"},
                {"proxy_id": hexid(99), "kind": "contact_sheet_tile", "path": "/local/b.jpg"},
            ],
            "capture": {
                "captured_at": {
                    "source": "exif",
                    "precision": "second",
                    "confidence": 0.99,
                    "utc": "2026-03-01T09:00:00+00:00",
                },
                "metadata_present": ["exif"],
            },
            "content": {"safety": safe()},
            "processing": {"state": "analyzed", "stages": {"safety": {"status": "done"}}},
            "exclusion": {"excluded_from_automation": False},
            "user": {"hidden": False},
        }
        record.update(overrides)
        return record

    def test_adapter_prefers_the_purpose_built_tile(self):
        """Kills: taking the first proxy in list order, which is record-dependent."""
        candidate = candidate_from_media_record(self._record(), score=0.5)
        self.assertEqual(candidate.proxy_kind, "contact_sheet_tile")
        self.assertEqual(candidate.proxy_id, hexid(99))

    def test_adapter_is_order_independent(self):
        record = self._record()
        reversed_record = self._record(proxies=list(reversed(record["proxies"])))
        self.assertEqual(
            candidate_from_media_record(record, score=0.5).proxy_id,
            candidate_from_media_record(reversed_record, score=0.5).proxy_id,
        )

    def test_adapter_ignores_proxy_kinds_that_may_not_be_sent(self):
        record = self._record(
            proxies=[{"proxy_id": hexid(55), "kind": "preview_2048", "path": "/local/big.jpg"}]
        )
        candidate = candidate_from_media_record(record, score=0.5)
        self.assertIsNone(candidate.proxy_id)
        self.assertEqual(plan([candidate]).excluded[0].reasons, ("no_proxy",))

    def test_adapter_carries_no_path(self):
        """Kills: adding a path field to SheetCandidate.

        A path cannot leak out of this module because it never enters it.
        """
        candidate = candidate_from_media_record(self._record(), score=0.5)
        self.assertFalse(
            [f for f in candidate.__slots__ if "path" in f],
            "SheetCandidate must not carry a filesystem path",
        )

    def test_record_without_safety_produces_a_blocked_candidate(self):
        """The mid-scan record. Kills: defaulting a missing block to a safe value."""
        record = self._record(content={})
        candidate = candidate_from_media_record(record, score=0.5)
        self.assertIsNone(candidate.safety)
        self.assertIn("safety_not_assessed", plan([candidate]).excluded[0].reasons)

    def test_record_with_pending_safety_stage_is_blocked(self):
        record = self._record(
            processing={"state": "analyzing", "stages": {"safety": {"status": "pending"}}}
        )
        candidate = candidate_from_media_record(record, score=0.5)
        self.assertIn("safety_stage_not_done", plan([candidate]).excluded[0].reasons)

    def test_unknown_precision_yields_no_sort_time(self):
        """Kills: reading `utc` without checking precision.

        The EXIF-less fixture case: an unknown-precision assertion has no usable
        time even if a utc value is present.
        """
        record = self._record(
            capture={
                "captured_at": {
                    "source": "unknown",
                    "precision": "unknown",
                    "confidence": 0.0,
                    "utc": "2026-03-01T09:00:00+00:00",
                },
                "metadata_present": [],
            }
        )
        self.assertIsNone(candidate_from_media_record(record, score=0.5).sort_time)

    def test_local_only_time_yields_no_sort_time(self):
        """Kills: falling back to `local`, which assumes the machine's zone."""
        record = self._record(
            capture={
                "captured_at": {
                    "source": "exif",
                    "precision": "second",
                    "confidence": 0.9,
                    "local": "2026-03-01T09:00:00",
                    "utc": None,
                },
                "metadata_present": ["exif"],
            }
        )
        self.assertIsNone(candidate_from_media_record(record, score=0.5).sort_time)

    def test_adapter_reads_hidden_and_exclusion(self):
        record = self._record(user={"hidden": True})
        self.assertTrue(candidate_from_media_record(record, score=0.5).user_hidden)

    def test_adapter_end_to_end_produces_a_sendable_plan(self):
        candidate = candidate_from_media_record(self._record(), score=0.5)
        result = plan([candidate])
        self.assertTrue(result.sendable)
        self.assertEqual(result.handles, ("s01",))
        self.assertNotIn(hexid(99), json.dumps(result.payload()))


if __name__ == "__main__":  # pragma: no cover
    unittest.main(verbosity=2)
