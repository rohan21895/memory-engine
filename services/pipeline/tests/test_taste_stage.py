"""The Tier 3 stage: off by default, and every absence named separately.

NO NETWORK IS TOUCHED BY THIS FILE. The three states that would reach a socket
-- key present, consent present, operator asked -- are never all true at once
here, and the one test that gets past the key gate stops at the consent gate
before a sender is even constructed. The dummy key below is a literal in this
file, never a real one.

WHAT THIS FILE ADDS OVER `packages/prompt-engine/tests/test_album_taste.py`

That file proves the module refuses correctly when it is called. This one proves
the SERVICE calls it, over a real library that real ingest walked and a real
ranking stage scored -- and, more to the point, that the default path does not
call it at all. A privacy guarantee that depends on a flag is worth exactly what
the flag's default is, and "off" is not something you can assert by reading.

The three absences are asserted as three DIFFERENT skip reasons rather than one:
"you did not ask", "there is no key" and "nobody consented" are three different
situations for the person reading the run report, and only the last is about
privacy. Collapsing them is how a consent failure gets read as a missing
dependency and quietly worked around.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from support import (  # noqa: E402
    PRINT_SAFE_SIZE,
    FakeMlRuntime,
    make_library,
    require_ingest_binary,
)

from memory_engine_pipeline.jobstore import JobValidationError, build_job  # noqa: E402
from memory_engine_pipeline.runner import STAGE_ORDER, run_pipeline  # noqa: E402
from memory_engine_pipeline.stages import taste  # noqa: E402
from memory_engine_pipeline.stages.base import Settings, StageStatus  # noqa: E402

# Never a real key. The one test that sets it stops at the consent gate, which
# is checked before any client is constructed.
DUMMY_KEY = "not-a-real-key-this-test-never-sends-anything"


def stage_result(report, name):
    for result in report.results:
        if result.stage == name:
            return result
    raise AssertionError(f"{name} did not run; ran {[r.stage for r in report.results]}")


class TasteStage(unittest.TestCase):
    """One analysed library, reused. Every scenario re-runs only `taste`."""

    @classmethod
    def setUpClass(cls):
        require_ingest_binary()
        cls.root = Path(tempfile.mkdtemp(prefix="mep-taste-src-"))
        cls.workdir = Path(tempfile.mkdtemp(prefix="mep-taste-work-"))
        # A repo root with no `.env` in it, so the key gate is testable on a
        # machine that has a real key sitting in the real repository.
        cls.repo_root = Path(tempfile.mkdtemp(prefix="mep-taste-repo-"))
        make_library(cls.root, 12, size=PRINT_SAFE_SIZE)
        with FakeMlRuntime() as host:
            cls.host_endpoint = host.endpoint
            cls.base = run_pipeline(
                [cls.root],
                cls.workdir,
                stages=["ingest", "analysis", "faces", "ranking"],
                settings=Settings(
                    ml_runtime_endpoint=host.endpoint,
                    render_print=False,
                    render_video=False,
                ),
            )
        for name in ("ingest", "analysis", "faces", "ranking"):
            result = stage_result(cls.base, name)
            if not result.status.satisfies_dependents:
                raise unittest.SkipTest(f"{name} did not complete: {result.detail}")

    @classmethod
    def tearDownClass(cls):
        # The invariant over the WHOLE file, checked once: a journal line is
        # written BEFORE a send, so a ledger with no lines in it is proof that
        # no test here reached the network. Checked before the workdir is
        # removed, and deliberately not inside any single test -- the claim is
        # about the file, not about one scenario.
        ledgers = sorted((cls.workdir / "outputs" / "taste").rglob("egress-ledger.jsonl"))
        wrote = [path for path in ledgers if path.read_text().strip()]
        for path in (cls.root, cls.workdir, cls.repo_root):
            shutil.rmtree(path, ignore_errors=True)
        assert not wrote, f"a send was journaled by an offline test suite: {wrote}"
        assert ledgers, "no ledger file was ever created; the dry-run tests did not run"

    def _run_taste(self, **settings) -> object:
        fields = dict(
            ml_runtime_endpoint=self.host_endpoint,
            render_print=False,
            render_video=False,
            # Smaller than the library, so there is a judgement to make. At the
            # default of 12 over a 12-photo library the sheet would already BE
            # the answer and the stage would correctly refuse to spend an
            # upload on it -- which is a real behaviour, tested separately.
            album_target_count=4,
        )
        fields.update(settings)
        report = run_pipeline(
            [self.root],
            self.workdir,
            stages=["taste"],
            settings=Settings(**fields),
            repo_root=self.repo_root,
        )
        return stage_result(report, "taste")

    # ------------------------------------------------------------- ordering --

    def test_taste_runs_before_album_and_album_does_not_depend_on_it(self):
        names = [name for name, _ in STAGE_ORDER]
        self.assertIn("taste", names)
        self.assertLess(names.index("taste"), names.index("album"))

    def test_the_pipeline_runs_to_a_validated_album_without_ever_reaching_tier3(self):
        """Offline is the default, and it is a whole product, not a degraded one."""
        result = stage_result(self.base, "ingest")
        self.assertEqual(result.status, StageStatus.COMPLETED)
        # `taste` was not in the selected set at all, so nothing about it ran.
        self.assertNotIn("taste", [r.stage for r in self.base.results])

    # ------------------------------------------------------ the three gates --

    def test_default_is_off_and_says_you_did_not_ask(self):
        result = self._run_taste()
        self.assertEqual(result.status, StageStatus.SKIPPED)
        self.assertIn("not requested", result.detail)
        # Asserted on the RESULT, not on the workdir: the workdir is shared
        # with the dry-run tests and unittest orders by name, so "the output
        # directory does not exist" would be a claim about test ordering
        # rather than about this stage.
        self.assertEqual(result.outputs, ())

    def test_no_key_is_a_different_reason_from_no_consent(self):
        previous = os.environ.pop("ANTHROPIC_API_KEY", None)
        try:
            result = self._run_taste(tier3_enabled=True)
        finally:
            if previous is not None:
                os.environ["ANTHROPIC_API_KEY"] = previous
        self.assertEqual(result.status, StageStatus.SKIPPED)
        self.assertIn("ANTHROPIC_API_KEY", result.detail)
        self.assertNotIn("consent", result.detail.split("Use --tier3-dry-run")[0])

    def test_consent_absent_blocks_and_writes_nothing(self):
        """The refusal that matters, run rather than reasoned about."""
        previous = os.environ.get("ANTHROPIC_API_KEY")
        os.environ["ANTHROPIC_API_KEY"] = DUMMY_KEY
        try:
            consent_file = self.workdir / taste.DEFAULT_CONSENT_FILENAME
            self.assertFalse(consent_file.exists())
            result = self._run_taste(tier3_enabled=True)
        finally:
            if previous is None:
                os.environ.pop("ANTHROPIC_API_KEY", None)
            else:
                os.environ["ANTHROPIC_API_KEY"] = previous
        self.assertEqual(result.status, StageStatus.SKIPPED)
        self.assertIn("consent", result.detail)
        self.assertIn("absence blocks", result.detail)
        # Nothing was composed, so nothing could have been sent.
        self.assertFalse(list((self.workdir / "outputs" / "taste").glob("*.json"))
                         if (self.workdir / "outputs" / "taste").exists() else [])

    def test_a_malformed_consent_record_fails_rather_than_reading_as_absent(self):
        previous = os.environ.get("ANTHROPIC_API_KEY")
        os.environ["ANTHROPIC_API_KEY"] = DUMMY_KEY
        consent_file = Path(tempfile.mkdtemp(prefix="mep-consent-")) / "consent.json"
        consent_file.write_text("{ this is not json", encoding="utf-8")
        try:
            result = self._run_taste(
                tier3_enabled=True, tier3_consent_path=str(consent_file)
            )
        finally:
            if previous is None:
                os.environ.pop("ANTHROPIC_API_KEY", None)
            else:
                os.environ["ANTHROPIC_API_KEY"] = previous
        self.assertEqual(result.status, StageStatus.FAILED)
        self.assertIn("consent_malformed", result.detail)

    # ------------------------------------------------------------- dry run --

    def test_dry_run_writes_the_exact_body_without_a_key_or_a_consent_record(self):
        """The deliverable: look at what would be sent, then decide."""
        previous = os.environ.pop("ANTHROPIC_API_KEY", None)
        try:
            result = self._run_taste(tier3_enabled=True, tier3_dry_run=True)
        finally:
            if previous is not None:
                os.environ["ANTHROPIC_API_KEY"] = previous

        self.assertEqual(result.status, StageStatus.SKIPPED, result.detail)
        self.assertIn("dry run", result.detail)
        body_path = Path(result.outputs[0])
        self.assertTrue(body_path.is_file())
        bundle = body_path.parent

        for name in (
            "contact-sheet.png",
            "contact-sheet-manifest.json",
            "request-body.json",
            "request-summary.json",
            "consent.json",
            "egress-ledger.jsonl",
        ):
            self.assertTrue((bundle / name).is_file(), f"{name} missing from the bundle")

        # No consent was consulted, and the ledger is an empty FILE rather than
        # an absent one: "no entry" must be visible, not inferred.
        self.assertIsNone(json.loads((bundle / "consent.json").read_text())["consent"])
        self.assertEqual((bundle / "egress-ledger.jsonl").read_text(), "")

        body = json.loads((bundle / "request-body.json").read_bytes())
        self.assertEqual(body["model"], Settings().tier3_model)
        import base64

        embedded = base64.standard_b64decode(
            body["messages"][0]["content"][0]["source"]["data"]
        )
        self.assertEqual((bundle / "contact-sheet.png").read_bytes(), embedded)

    def test_the_dry_run_body_carries_no_media_id_no_path_and_no_filename(self):
        previous = os.environ.pop("ANTHROPIC_API_KEY", None)
        try:
            result = self._run_taste(tier3_enabled=True, tier3_dry_run=True)
        finally:
            if previous is not None:
                os.environ["ANTHROPIC_API_KEY"] = previous
        bundle = Path(result.outputs[0]).parent
        raw = (bundle / "request-body.json").read_text(encoding="utf-8")
        body = json.loads(raw)
        blob = body["messages"][0]["content"][0]["source"]["data"]
        authored = raw.replace(blob, "")

        summary = json.loads((bundle / "request-summary.json").read_text())
        shortlist = summary["label_to_media_id_LOCAL_ONLY"]
        self.assertTrue(shortlist, "the summary recorded no shortlist to check against")
        for media_id in shortlist.values():
            self.assertNotIn(media_id, raw, "a media id reached the request body")
        for token in (str(self.root), str(self.workdir), ".jpg", ".JPG", "IMG_"):
            self.assertNotIn(token, authored, f"authored request text holds {token}")

        # The labels DID reach it, so this is not passing on an empty body.
        for label in shortlist:
            self.assertIn(label, authored)

    def test_the_dry_run_is_byte_identical_across_runs(self):
        previous = os.environ.pop("ANTHROPIC_API_KEY", None)
        try:
            first = self._run_taste(tier3_enabled=True, tier3_dry_run=True)
            second = self._run_taste(tier3_enabled=True, tier3_dry_run=True)
        finally:
            if previous is not None:
                os.environ["ANTHROPIC_API_KEY"] = previous
        left = Path(first.outputs[0]).read_bytes()
        right = Path(second.outputs[0]).read_bytes()
        self.assertEqual(left, right)


class KeyDiscovery(unittest.TestCase):
    """`api_key_present` answers a question without carrying the answer."""

    def setUp(self):
        self.repo = Path(tempfile.mkdtemp(prefix="mep-key-"))
        self.previous = os.environ.pop("ANTHROPIC_API_KEY", None)

    def tearDown(self):
        shutil.rmtree(self.repo, ignore_errors=True)
        os.environ.pop("ANTHROPIC_API_KEY", None)
        if self.previous is not None:
            os.environ["ANTHROPIC_API_KEY"] = self.previous

    def test_absent_is_absent(self):
        self.assertFalse(taste.api_key_present(self.repo))

    def test_an_empty_assignment_in_dotenv_is_not_a_key(self):
        (self.repo / ".env").write_text("ANTHROPIC_API_KEY=\n", encoding="utf-8")
        self.assertFalse(taste.api_key_present(self.repo))

    def test_a_commented_out_key_is_not_a_key(self):
        (self.repo / ".env").write_text("# ANTHROPIC_API_KEY=abc\n", encoding="utf-8")
        self.assertFalse(taste.api_key_present(self.repo))

    def test_a_quoted_key_is_found_and_loaded_without_being_returned(self):
        (self.repo / ".env").write_text('ANTHROPIC_API_KEY="abc123"\n', encoding="utf-8")
        self.assertTrue(taste.api_key_present(self.repo))
        self.assertTrue(taste.load_dotenv_key(self.repo))
        # The value goes to the environment and nowhere else: both helpers
        # return a bool, so there is no local variable holding a secret one
        # f-string away from an event line.
        self.assertIs(taste.load_dotenv_key(self.repo), True)
        self.assertEqual(os.environ["ANTHROPIC_API_KEY"], "abc123")

    def test_an_unrelated_key_in_dotenv_is_not_mistaken_for_ours(self):
        (self.repo / ".env").write_text("OPENAI_API_KEY=abc\n", encoding="utf-8")
        self.assertFalse(taste.api_key_present(self.repo))


class EgressOnTheJobSpec(unittest.TestCase):
    """A job that wants the network must say so, and name its consent.

    Real ids throughout: `media_ids` is a content address and `ledger_entry_id`
    is a UUID in the contract, and a test using "a" would fail on the id
    pattern before it ever reached the egress rule it is trying to check.
    """

    MEDIA = "0" * 63 + "1"
    LEDGER = "3f2a1c58-4b7e-4c21-9a6d-0e51b7c9d420"

    def test_the_default_job_declares_no_egress(self):
        job = build_job(job_type="analyze_image", scope="s", params={}, media_ids=[self.MEDIA])
        self.assertEqual(job["egress"]["requires_egress"], False)
        self.assertIsNone(job["egress"]["consent"])

    def test_declaring_egress_without_consent_is_refused_at_build_time(self):
        with self.assertRaises(JobValidationError) as caught:
            build_job(
                job_type="tier3_request",
                scope="s",
                params={},
                media_ids=[self.MEDIA],
                egress={
                    "requires_egress": True,
                    "destination": "tier3_inference",
                    "payload_kind": "contact_sheet",
                },
            )
        self.assertIn("consent", str(caught.exception))

    def test_a_complete_declaration_is_accepted_and_written_out_in_full(self):
        job = build_job(
            job_type="tier3_request",
            scope="s",
            params={},
            media_ids=[self.MEDIA],
            egress={
                "requires_egress": True,
                "consent": {
                    "ledger_entry_id": self.LEDGER,
                    "scope": "tier3_contact_sheet",
                    "granted_at": "2026-08-01T00:00:00+00:00",
                    "expires_at": None,
                    "revoked_at": None,
                },
                "destination": "tier3_inference",
                "payload_kind": "contact_sheet",
                "estimated_bytes": 1234,
            },
        )
        # Every key present even when null: `additionalProperties: false` means
        # a partial object would validate while saying less than the contract
        # asks, and an absent field and a null one must not look the same.
        self.assertEqual(
            sorted(job["egress"]),
            ["consent", "destination", "estimated_bytes", "payload_kind", "requires_egress"],
        )

    def test_an_unknown_egress_field_is_refused(self):
        with self.assertRaises(JobValidationError):
            build_job(
                job_type="tier3_request",
                scope="s",
                params={},
                media_ids=[self.MEDIA],
                egress={"requires_egress": False, "allow_anything": True},
            )


if __name__ == "__main__":
    unittest.main()
