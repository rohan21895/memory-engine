"""Tests for scripts/models/fetch_weights.py.

Everything here runs offline. The network is replaced with an opener that
returns bytes chosen by the test, which is the only way to exercise the cases
that matter -- a rate-limit body served where a model should be, a git-LFS
pointer, a pin that does not match -- because a source serving those on demand
is not something a test can arrange.

WHAT IS NOT TESTED, AND WHY IT SAYS SO HERE

The authenticated Hugging Face path. No HF_TOKEN exists in this environment and
no registry entry is gated today, so the tests below cover the mapping from a
401/403 to the message an operator reads, and the stripping of the
Authorization header across a redirect. They do NOT establish that fetching a
gated repository works. Nobody has run that.
"""

from __future__ import annotations

import importlib.util
import io
import json
import sys
import unittest
import urllib.error
import urllib.request
import zipfile
from argparse import Namespace
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "models" / "fetch_weights.py"

# Loaded by path: the script is an executable entry point rather than an
# installed package, and giving it a package just so a test can import it would
# be arranging the source tree around the tests.
_spec = importlib.util.spec_from_file_location("fetch_weights_under_test", SCRIPT)
assert _spec is not None and _spec.loader is not None
fw = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = fw
_spec.loader.exec_module(fw)

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from models.policy import load_gate  # noqa: E402

POLICY = json.loads((REPO_ROOT / "models" / "registry.json").read_bytes())["load_policy"]

# A minimal but real ONNX-shaped header: field 1 (ir_version) varint, which is
# what every export in this registry actually starts with.
ONNX_BYTES = bytes([0x08, 0x06]) + b"\x12\x07pytorch" + b"\x00" * 64


def blake3_hex(data: bytes) -> str:
    from blake3 import blake3

    return blake3(data).hexdigest()


class FakeResponse(io.BytesIO):
    def __init__(self, body: bytes, headers: dict[str, str] | None = None) -> None:
        super().__init__(body)
        self.headers = headers or {"Content-Length": str(len(body))}

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
        return False


class FakeOpener:
    """Stands in for urllib's opener. Records what it was asked for."""

    def __init__(self, responses: dict[str, bytes | Exception]) -> None:
        self.responses = responses
        self.requests: list[str] = []

    def open(self, request, timeout=None):  # noqa: ARG002 - signature parity
        url = request.full_url if hasattr(request, "full_url") else str(request)
        self.requests.append(url)
        reply = self.responses.get(url)
        if reply is None:
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
        if isinstance(reply, Exception):
            raise reply
        return FakeResponse(reply)


class ExplodingOpener:
    """Any network access at all is a test failure."""

    def open(self, request, timeout=None):  # noqa: ARG002
        raise AssertionError("the network was used when it should not have been")


def fetcher_for(responses, environ=None) -> "fw.Fetcher":
    opener = FakeOpener(responses)
    fetcher = fw.Fetcher(
        environ=environ or {},
        timeout=1.0,
        attempts=1,
        sleep=lambda _seconds: None,
        build_opener=lambda: opener,
    )
    fetcher.opener = opener  # type: ignore[attr-defined]
    return fetcher


def plan_for(
    tmp: Path,
    *,
    url: str | None = "https://example.test/model.onnx",
    pinned: str | None = None,
    member: str | None = None,
    filename: str = "model.onnx",
    conversion: dict | None = None,
) -> "fw.Plan":
    return fw.Plan(
        model_id="model-under-test",
        config_path=tmp / "config.json",
        filename=filename,
        fmt="onnx",
        source_url=url,
        archive_member=member,
        pinned_hash=pinned,
        rollout_state="candidate",
        install_path=tmp / "weights" / filename,
        conversion=conversion,
    )


def result(state: str) -> "fw.Result":
    return fw.Result(model_id=state.lower(), state=state, detail="")


class ExitCodesAreDistinct(unittest.TestCase):
    """The whole point of the exit codes: a skip must not read as a pass.

    Four files in this repository have shipped a skip sharing an exit code with
    a success. Every branch below exists so a fifth is caught here rather than
    by someone believing a green run.
    """

    def test_the_four_codes_are_four_different_numbers(self):
        codes = {
            fw.EXIT_SUCCESS,
            fw.EXIT_PARTIAL,
            fw.EXIT_FAILURE,
            fw.EXIT_PRECONDITION,
        }
        self.assertEqual(len(codes), 4)
        # Argparse exits 2 on a usage error; nothing here may collide with it.
        self.assertNotIn(2, codes)

    def test_everything_verified_is_success(self):
        self.assertEqual(
            fw.exit_code([result(fw.VERIFIED), result(fw.VERIFIED)]), fw.EXIT_SUCCESS
        )

    def test_a_fetch_without_a_pin_is_never_success(self):
        """The defect this file exists to prevent.

        NEEDS_PIN means the bytes arrived and nothing certifies them. That is
        not reproducibility, so it cannot share an exit code with a verified
        fetch -- a CI job that treated it as one would go green on precisely
        the state this script was written to eliminate.
        """
        self.assertEqual(fw.exit_code([result(fw.NEEDS_PIN)]), fw.EXIT_PARTIAL)
        self.assertEqual(
            fw.exit_code([result(fw.VERIFIED), result(fw.NEEDS_PIN)]), fw.EXIT_PARTIAL
        )

    def test_something_unavailable_beside_something_fetched_is_partial(self):
        self.assertEqual(
            fw.exit_code([result(fw.VERIFIED), result(fw.UNAVAILABLE)]), fw.EXIT_PARTIAL
        )

    def test_a_failure_outranks_any_amount_of_success(self):
        self.assertEqual(
            fw.exit_code([result(fw.VERIFIED), result(fw.VERIFIED), result(fw.FAILED)]),
            fw.EXIT_FAILURE,
        )

    def test_nothing_obtained_at_all_is_failure_not_partial(self):
        """'Partial' claims something was accomplished. Nothing was."""
        self.assertEqual(
            fw.exit_code([result(fw.UNAVAILABLE), result(fw.UNAVAILABLE)]),
            fw.EXIT_FAILURE,
        )

    def test_a_run_that_only_skipped_is_failure(self):
        """`--only <a placeholder>` asks for work and gets none."""
        self.assertEqual(fw.exit_code([result(fw.SKIPPED)]), fw.EXIT_FAILURE)
        self.assertEqual(fw.exit_code([]), fw.EXIT_FAILURE)

    def test_skips_do_not_dilute_a_real_result(self):
        self.assertEqual(
            fw.exit_code([result(fw.VERIFIED), result(fw.SKIPPED)]), fw.EXIT_SUCCESS
        )


class ArtifactUrlRule(unittest.TestCase):
    def test_a_landing_page_is_not_an_artifact(self):
        for page in (
            "https://huggingface.co/google/siglip2-so400m-patch14-384",
            "https://github.com/soCzech/TransNetV2",
            "https://github.com/opencv/opencv_zoo/tree/main/models/face_detection_yunet",
            "https://github.com/deepinsight/insightface/tree/master/model_zoo",
        ):
            with self.subTest(page=page):
                self.assertIsNone(fw.artifact_url(page))

    def test_a_file_url_is_an_artifact(self):
        for url in (
            "https://media.githubusercontent.com/media/opencv/opencv_zoo/abc/x/y.onnx",
            "https://github.com/deepinsight/insightface/releases/download/v0.7/buffalo_l.zip",
            "https://huggingface.co/org/repo/resolve/main/onnx/vision_model.onnx",
        ):
            with self.subTest(url=url):
                self.assertEqual(fw.artifact_url(url), url)

    def test_the_old_parenthetical_member_form_is_not_fetchable(self):
        """It was in this registry until this change and no fetcher could act on it."""
        self.assertIsNone(
            fw.artifact_url(
                "https://github.com/deepinsight/insightface/releases/download/v0.7/"
                "buffalo_l.zip (det_10g.onnx)"
            )
        )

    def test_null_and_non_http_are_not_artifacts(self):
        self.assertIsNone(fw.artifact_url(None))
        self.assertIsNone(fw.artifact_url(""))
        self.assertIsNone(fw.artifact_url("file:///etc/passwd"))
        self.assertIsNone(fw.artifact_url("ftp://example.test/model.onnx"))


class PayloadShapeChecks(unittest.TestCase):
    """Each case is something a source in this registry actually served."""

    def setUp(self):
        self.tmp = Path(self.enterContext(__import__("tempfile").TemporaryDirectory()))

    def write(self, body: bytes) -> Path:
        path = self.tmp / "payload"
        path.write_bytes(body)
        return path

    def test_a_git_lfs_pointer_is_refused(self):
        pointer = (
            b"version https://git-lfs.github.com/spec/v1\n"
            b"oid sha256:" + b"a" * 64 + b"\nsize 232589\n"
        )
        with self.assertRaises(fw.Corrupt) as caught:
            fw.check_payload_shape(self.write(pointer), "onnx")
        self.assertIn("git-LFS pointer", str(caught.exception))

    def test_an_html_page_is_refused(self):
        with self.assertRaises(fw.Corrupt):
            fw.check_payload_shape(self.write(b"\n\n<!DOCTYPE html>\n<html>"), "onnx")

    def test_a_rate_limit_body_is_refused(self):
        with self.assertRaises(fw.Corrupt):
            fw.check_payload_shape(
                self.write(b"429: Too Many Requests\nFor more on scraping"), "onnx"
            )

    def test_an_empty_file_is_refused(self):
        with self.assertRaises(fw.Corrupt):
            fw.check_payload_shape(self.write(b""), "onnx")

    def test_a_zip_is_not_a_bare_onnx(self):
        with self.assertRaises(fw.Corrupt):
            fw.check_payload_shape(self.write(b"PK\x03\x04rest"), "onnx")

    def test_a_real_onnx_header_passes(self):
        fw.check_payload_shape(self.write(ONNX_BYTES), "onnx")

    def test_the_real_fetched_yunet_passes_if_it_is_here(self):
        """Not a fixture: the actual file, when a fetch has been run."""
        weights = REPO_ROOT / "models" / "weights" / "face_detection_yunet_2023mar.onnx"
        if not weights.is_file():
            self.skipTest("weights not fetched in this checkout")
        fw.check_payload_shape(weights, "onnx")


class LfsCorroboration(unittest.TestCase):
    def test_pointer_url_is_derived_from_the_media_url(self):
        self.assertEqual(
            fw.lfs_pointer_url(
                "https://media.githubusercontent.com/media/opencv/opencv_zoo/abc/m/y.onnx"
            ),
            "https://raw.githubusercontent.com/opencv/opencv_zoo/abc/m/y.onnx",
        )
        self.assertIsNone(fw.lfs_pointer_url("https://example.test/model.onnx"))

    def test_pointer_parsing(self):
        text = (
            "version https://git-lfs.github.com/spec/v1\n"
            "oid sha256:" + "b" * 64 + "\nsize 42\n"
        )
        self.assertEqual(fw.parse_lfs_pointer(text), ("b" * 64, 42))
        self.assertIsNone(fw.parse_lfs_pointer("429: Too Many Requests"))

    def test_bytes_disagreeing_with_the_committed_pointer_are_fatal(self):
        """The source repository's own record of what the file should be."""
        payload = fw.Payload(path=Path("x"), byte_size=5, blake3="0" * 64, sha256="c" * 64)
        media = "https://media.githubusercontent.com/media/o/r/ref/p/model.onnx"
        pointer = "https://raw.githubusercontent.com/o/r/ref/p/model.onnx"
        fetcher = fetcher_for(
            {
                pointer: (
                    "version https://git-lfs.github.com/spec/v1\noid sha256:"
                    + "d" * 64
                    + "\nsize 5\n"
                ).encode()
            }
        )
        with self.assertRaises(fw.Corrupt) as caught:
            fw.corroborate(fetcher, media, payload)
        self.assertIn("disagree with the git-LFS pointer", str(caught.exception))

    def test_a_matching_pointer_is_reported_as_corroboration(self):
        payload = fw.Payload(path=Path("x"), byte_size=5, blake3="0" * 64, sha256="e" * 64)
        media = "https://media.githubusercontent.com/media/o/r/ref/p/model.onnx"
        pointer = "https://raw.githubusercontent.com/o/r/ref/p/model.onnx"
        fetcher = fetcher_for(
            {
                pointer: (
                    "version https://git-lfs.github.com/spec/v1\noid sha256:"
                    + "e" * 64
                    + "\nsize 5\n"
                ).encode()
            }
        )
        self.assertIn("matches", fw.corroborate(fetcher, media, payload))

    def test_an_unreachable_pointer_is_advisory_not_fatal(self):
        payload = fw.Payload(path=Path("x"), byte_size=5, blake3="0" * 64, sha256="f" * 64)
        media = "https://media.githubusercontent.com/media/o/r/ref/p/model.onnx"
        note = fw.corroborate(fetcher_for({}), media, payload)
        self.assertIn("advisory check skipped", note)


class IntegrityAgreesWithTheLoadGate(unittest.TestCase):
    """The fetcher must not hold a second opinion about integrity."""

    def test_a_mismatch_is_the_gates_mismatch(self):
        self.assertEqual(
            fw.integrity_refusal("a" * 64, "b" * 64, POLICY),
            load_gate.UnloadableReason.HASH_MISMATCH,
        )

    def test_a_pin_that_was_never_checked_is_the_gates_reason(self):
        self.assertEqual(
            fw.integrity_refusal("a" * 64, None, POLICY),
            load_gate.UnloadableReason.INTEGRITY_UNVERIFIED,
        )

    def test_a_match_and_an_unpinned_entry_both_pass_integrity(self):
        self.assertIsNone(fw.integrity_refusal("a" * 64, "a" * 64, POLICY))
        self.assertIsNone(fw.integrity_refusal(None, "b" * 64, POLICY))

    def test_licence_and_placeholder_state_are_not_the_fetchers_business(self):
        """They are the gate's, at load time, and are checked there.

        A fetcher that refused to download a non-commercial checkpoint would be
        enforcing release policy at fetch time, where nobody asked for it -- and
        the development pipeline in registry.json depends on exactly those
        weights being on disk.
        """
        scrfd = json.loads(
            (REPO_ROOT / "models" / "configs" / "scrfd-10g-bnkps.json").read_bytes()
        )
        self.assertTrue(scrfd["license"]["blocks_commercial_release"])
        self.assertIsNone(fw.integrity_refusal(None, "a" * 64, POLICY))


class FetchOneBehaviour(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(self.enterContext(__import__("tempfile").TemporaryDirectory()))
        self.workspace = self.tmp / "staging"
        self.workspace.mkdir()
        (self.tmp / "weights").mkdir()

    def run_fetch(self, plan, fetcher, force=False):
        return fw.fetch_one(
            plan,
            fetcher=fetcher,
            policy=POLICY,
            workspace=self.workspace,
            archives={},
            force=force,
        )

    def test_a_matching_pin_installs_and_verifies(self):
        digest = blake3_hex(ONNX_BYTES)
        plan = plan_for(self.tmp, pinned=digest)
        outcome = self.run_fetch(
            plan, fetcher_for({"https://example.test/model.onnx": ONNX_BYTES})
        )
        self.assertEqual(outcome.state, fw.VERIFIED)
        self.assertTrue(plan.install_path.is_file())
        self.assertEqual(plan.install_path.read_bytes(), ONNX_BYTES)

    def test_a_mismatched_download_is_not_installed(self):
        """The requirement, stated as a file that must not exist afterwards."""
        plan = plan_for(self.tmp, pinned="a" * 64)
        outcome = self.run_fetch(
            plan, fetcher_for({"https://example.test/model.onnx": ONNX_BYTES})
        )
        self.assertEqual(outcome.state, fw.FAILED)
        self.assertIn(load_gate.UnloadableReason.HASH_MISMATCH, outcome.detail)
        self.assertFalse(plan.install_path.exists())
        self.assertEqual(list(self.workspace.iterdir()), [])

    def test_an_unpinned_download_installs_and_reports_its_digest(self):
        plan = plan_for(self.tmp)
        outcome = self.run_fetch(
            plan, fetcher_for({"https://example.test/model.onnx": ONNX_BYTES})
        )
        self.assertEqual(outcome.state, fw.NEEDS_PIN)
        self.assertEqual(outcome.digest, blake3_hex(ONNX_BYTES))
        self.assertEqual(outcome.byte_size, len(ONNX_BYTES))
        self.assertTrue(plan.install_path.is_file())

    def test_an_already_correct_file_is_not_re_downloaded(self):
        digest = blake3_hex(ONNX_BYTES)
        plan = plan_for(self.tmp, pinned=digest)
        plan.install_path.write_bytes(ONNX_BYTES)
        fetcher = fw.Fetcher(
            environ={}, attempts=1, sleep=lambda _s: None, build_opener=ExplodingOpener
        )
        outcome = self.run_fetch(plan, fetcher)
        self.assertEqual(outcome.state, fw.VERIFIED)
        self.assertIn("not re-downloaded", outcome.detail)

    def test_an_already_present_unpinned_file_is_not_re_downloaded_either(self):
        plan = plan_for(self.tmp)
        plan.install_path.write_bytes(ONNX_BYTES)
        fetcher = fw.Fetcher(
            environ={}, attempts=1, sleep=lambda _s: None, build_opener=ExplodingOpener
        )
        outcome = self.run_fetch(plan, fetcher)
        self.assertEqual(outcome.state, fw.NEEDS_PIN)
        self.assertEqual(outcome.digest, blake3_hex(ONNX_BYTES))

    def test_force_re_downloads(self):
        plan = plan_for(self.tmp)
        plan.install_path.write_bytes(b"stale bytes that are not a model")
        outcome = self.run_fetch(
            plan,
            fetcher_for({"https://example.test/model.onnx": ONNX_BYTES}),
            force=True,
        )
        self.assertEqual(outcome.state, fw.NEEDS_PIN)
        self.assertEqual(plan.install_path.read_bytes(), ONNX_BYTES)

    def test_an_installed_file_that_disagrees_with_its_pin_is_reported_not_replaced(self):
        plan = plan_for(self.tmp, pinned="a" * 64)
        plan.install_path.write_bytes(ONNX_BYTES)
        fetcher = fw.Fetcher(
            environ={}, attempts=1, sleep=lambda _s: None, build_opener=ExplodingOpener
        )
        outcome = self.run_fetch(plan, fetcher)
        self.assertEqual(outcome.state, fw.FAILED)
        self.assertEqual(plan.install_path.read_bytes(), ONNX_BYTES)

    def test_an_html_page_is_never_installed(self):
        plan = plan_for(self.tmp)
        outcome = self.run_fetch(
            plan,
            fetcher_for({"https://example.test/model.onnx": b"<!DOCTYPE html><html>x"}),
        )
        self.assertEqual(outcome.state, fw.FAILED)
        self.assertFalse(plan.install_path.exists())

    def test_a_provenance_page_is_unavailable_and_is_never_requested(self):
        plan = plan_for(self.tmp, url="https://github.com/soCzech/TransNetV2")
        fetcher = fw.Fetcher(
            environ={}, attempts=1, sleep=lambda _s: None, build_opener=ExplodingOpener
        )
        outcome = self.run_fetch(plan, fetcher)
        self.assertEqual(outcome.state, fw.UNAVAILABLE)
        self.assertIn("provenance page", outcome.detail)

    def test_a_null_source_url_is_unavailable(self):
        outcome = self.run_fetch(
            plan_for(self.tmp, url=None),
            fw.Fetcher(environ={}, attempts=1, build_opener=ExplodingOpener),
        )
        self.assertEqual(outcome.state, fw.UNAVAILABLE)
        self.assertIn("no checkpoint has been chosen", outcome.detail)

    def test_a_truncated_download_is_refused(self):
        class ShortOpener:
            def open(self, request, timeout=None):  # noqa: ARG002
                return FakeResponse(ONNX_BYTES, {"Content-Length": "999999"})

        plan = plan_for(self.tmp)
        fetcher = fw.Fetcher(
            environ={}, attempts=1, sleep=lambda _s: None, build_opener=ShortOpener
        )
        outcome = self.run_fetch(plan, fetcher)
        self.assertEqual(outcome.state, fw.UNAVAILABLE)
        self.assertFalse(plan.install_path.exists())


class ArchiveMembers(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(self.enterContext(__import__("tempfile").TemporaryDirectory()))
        self.workspace = self.tmp / "staging"
        self.workspace.mkdir()
        (self.tmp / "weights").mkdir()
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as bundle:
            bundle.writestr("det_10g.onnx", ONNX_BYTES)
            bundle.writestr("w600k_r50.onnx", ONNX_BYTES + b"different")
        self.zip_bytes = buffer.getvalue()
        self.url = "https://example.test/buffalo_l.zip"

    def test_a_member_is_extracted_and_installed_under_the_configured_name(self):
        plan = plan_for(
            self.tmp, url=self.url, member="det_10g.onnx", filename="scrfd_10g_bnkps.onnx"
        )
        outcome = fw.fetch_one(
            plan,
            fetcher=fetcher_for({self.url: self.zip_bytes}),
            policy=POLICY,
            workspace=self.workspace,
            archives={},
            force=False,
        )
        self.assertEqual(outcome.state, fw.NEEDS_PIN)
        self.assertEqual(plan.install_path.name, "scrfd_10g_bnkps.onnx")
        self.assertEqual(plan.install_path.read_bytes(), ONNX_BYTES)

    def test_one_archive_serves_two_models_with_one_download(self):
        """SCRFD and ArcFace share a 288MB zip. Downloading it twice is a bug."""
        fetcher = fetcher_for({self.url: self.zip_bytes})
        archives: dict[str, Path] = {}
        for member, filename in (
            ("det_10g.onnx", "scrfd_10g_bnkps.onnx"),
            ("w600k_r50.onnx", "w600k_r50.onnx"),
        ):
            outcome = fw.fetch_one(
                plan_for(self.tmp, url=self.url, member=member, filename=filename),
                fetcher=fetcher,
                policy=POLICY,
                workspace=self.workspace,
                archives=archives,
                force=False,
            )
            self.assertEqual(outcome.state, fw.NEEDS_PIN)
        self.assertEqual(fetcher.opener.requests.count(self.url), 1)

    def test_a_missing_member_is_a_loud_failure(self):
        plan = plan_for(self.tmp, url=self.url, member="not_in_here.onnx")
        outcome = fw.fetch_one(
            plan,
            fetcher=fetcher_for({self.url: self.zip_bytes}),
            policy=POLICY,
            workspace=self.workspace,
            archives={},
            force=False,
        )
        self.assertEqual(outcome.state, fw.FAILED)
        self.assertIn("not in the archive", outcome.detail)
        self.assertFalse(plan.install_path.exists())

    def test_a_non_zip_served_where_an_archive_was_expected_is_a_failure(self):
        plan = plan_for(self.tmp, url=self.url, member="det_10g.onnx")
        outcome = fw.fetch_one(
            plan,
            fetcher=fetcher_for({self.url: b"429: Too Many Requests\n"}),
            policy=POLICY,
            workspace=self.workspace,
            archives={},
            force=False,
        )
        self.assertEqual(outcome.state, fw.FAILED)
        self.assertIn("not a zip archive", outcome.detail)


class TokenHandling(unittest.TestCase):
    """The gated path, as far as it can honestly be tested without a token."""

    def test_the_token_is_read_from_the_documented_variables(self):
        self.assertEqual(fw.hf_token({"HF_TOKEN": "abc"}), "abc")
        self.assertEqual(fw.hf_token({"HUGGING_FACE_HUB_TOKEN": "xyz"}), "xyz")
        self.assertIsNone(fw.hf_token({}))
        self.assertIsNone(fw.hf_token({"HF_TOKEN": "   "}))

    def test_no_token_and_a_403_names_the_missing_token(self):
        url = "https://huggingface.co/org/repo/resolve/main/model.onnx"
        fetcher = fetcher_for(
            {url: urllib.error.HTTPError(url, 403, "Forbidden", {}, None)}, environ={}
        )
        with self.assertRaises(fw.Unavailable) as caught:
            fetcher.download(url, Path("/dev/null"))
        self.assertIn("no HF_TOKEN", str(caught.exception))

    def test_a_rejected_token_says_so_instead(self):
        url = "https://huggingface.co/org/repo/resolve/main/model.onnx"
        fetcher = fetcher_for(
            {url: urllib.error.HTTPError(url, 401, "Unauthorized", {}, None)},
            environ={"HF_TOKEN": "a-token"},
        )
        with self.assertRaises(fw.Unavailable) as caught:
            fetcher.download(url, Path("/dev/null"))
        self.assertIn("refused", str(caught.exception))

    def test_a_dead_url_is_reported_as_dead(self):
        url = "https://example.test/gone.onnx"
        fetcher = fetcher_for({url: urllib.error.HTTPError(url, 404, "gone", {}, None)})
        with self.assertRaises(fw.Unavailable) as caught:
            fetcher.download(url, Path("/dev/null"))
        self.assertIn("dead", str(caught.exception))

    def test_authorization_is_dropped_when_a_redirect_leaves_the_host(self):
        """HF redirects downloads to a CDN. The token must not go with it."""
        handler = fw.TokenSafeRedirectHandler()
        request = urllib.request.Request(
            "https://huggingface.co/org/repo/resolve/main/model.onnx",
            headers={"Authorization": "Bearer secret", "User-Agent": "x"},
        )
        redirected = handler.redirect_request(
            request, None, 302, "Found", {}, "https://cdn-lfs.example.test/blob"
        )
        self.assertIsNotNone(redirected)
        headers = {name.lower() for name in redirected.headers}
        self.assertNotIn("authorization", headers)

    def test_authorization_survives_a_same_host_redirect(self):
        handler = fw.TokenSafeRedirectHandler()
        request = urllib.request.Request(
            "https://huggingface.co/org/repo/resolve/main/model.onnx",
            headers={"Authorization": "Bearer secret"},
        )
        redirected = handler.redirect_request(
            request, None, 302, "Found", {}, "https://huggingface.co/other/path.onnx"
        )
        headers = {name.lower() for name in redirected.headers}
        self.assertIn("authorization", headers)


class ArtifactsThisRepositoryBuilds(unittest.TestCase):
    """Issue #79. SigLIP 2 is published as safetensors and nothing else, so the
    ONNX vision tower the registry names exists on no server. The fetcher used
    to report that as UNAVAILABLE -- 'a provenance page rather than a file' --
    which is true and useless: it reads as 'nothing can be done', and the model
    that blocks album planning stayed missing behind an accurate message.
    """

    RECIPE = {
        "script": "scripts/models/export_siglip2_vision_onnx.py",
        "input_url": "https://huggingface.co/o/r/resolve/abc/model.safetensors",
        "input_sha256": "9" * 64,
    }

    def setUp(self):
        self.tmp = Path(self.enterContext(__import__("tempfile").TemporaryDirectory()))
        self.workspace = self.tmp / "staging"
        self.workspace.mkdir()
        (self.tmp / "weights").mkdir()

    def run_fetch(self, plan, fetcher, force=False):
        return fw.fetch_one(
            plan,
            fetcher=fetcher,
            policy=POLICY,
            workspace=self.workspace,
            archives={},
            force=force,
        )

    def exploding_fetcher(self):
        """Any network call at all is the defect under test."""
        return fetcher_for({})

    def test_a_convertible_entry_names_the_command_instead_of_giving_up(self):
        plan = plan_for(self.tmp, conversion=self.RECIPE)
        outcome = self.run_fetch(plan, self.exploding_fetcher())
        self.assertEqual(outcome.state, fw.CONVERTIBLE)
        self.assertIn(self.RECIPE["script"], outcome.detail)
        self.assertFalse(plan.install_path.exists())

    def test_the_conversion_input_is_never_downloaded_as_the_artifact(self):
        """The 4.5GB safetensors must not land under a name ending in .onnx.

        `input_url` looks exactly like a fetchable artifact URL to
        `artifact_url()` -- it is a Hugging Face /resolve/ link ending in a
        known suffix -- so nothing about the URL rule would have stopped it.
        """
        self.assertIsNotNone(fw.artifact_url(self.RECIPE["input_url"]))
        plan = plan_for(self.tmp, url=self.RECIPE["input_url"], conversion=self.RECIPE)
        outcome = self.run_fetch(
            plan, fetcher_for({self.RECIPE["input_url"]: b"\x08safetensors-ish"})
        )
        self.assertEqual(outcome.state, fw.CONVERTIBLE)
        self.assertFalse(plan.install_path.exists())

    def test_force_does_not_turn_a_conversion_into_a_download(self):
        plan = plan_for(self.tmp, conversion=self.RECIPE)
        plan.install_path.write_bytes(ONNX_BYTES)
        outcome = self.run_fetch(plan, self.exploding_fetcher(), force=True)
        self.assertEqual(outcome.state, fw.CONVERTIBLE)
        self.assertEqual(plan.install_path.read_bytes(), ONNX_BYTES)

    def test_an_installed_conversion_is_reported_like_any_other_file(self):
        """Built or downloaded, an unpinned file on disk is unpinned."""
        plan = plan_for(self.tmp, conversion=self.RECIPE)
        plan.install_path.write_bytes(ONNX_BYTES)
        outcome = self.run_fetch(plan, self.exploding_fetcher())
        self.assertEqual(outcome.state, fw.NEEDS_PIN)
        self.assertEqual(outcome.digest, blake3_hex(ONNX_BYTES))

    def test_a_recipe_whose_script_is_gone_is_a_failure_not_an_instruction(self):
        recipe = dict(self.RECIPE, script="scripts/models/deleted-by-someone.py")
        plan = plan_for(self.tmp, conversion=recipe)
        outcome = self.run_fetch(plan, self.exploding_fetcher())
        self.assertEqual(outcome.state, fw.FAILED)
        self.assertIn("deleted-by-someone.py", outcome.detail)

    def test_convertible_shares_unavailable_s_exit_code(self):
        """Not on disk either way, so the run is equally incomplete."""
        self.assertEqual(
            fw.exit_code([result(fw.CONVERTIBLE), result(fw.NEEDS_PIN)]),
            fw.EXIT_PARTIAL,
        )
        self.assertEqual(
            fw.exit_code([result(fw.CONVERTIBLE)]),
            fw.exit_code([result(fw.UNAVAILABLE)]),
        )

    def test_convertible_is_not_a_pass(self):
        self.assertNotEqual(fw.exit_code([result(fw.CONVERTIBLE)]), fw.EXIT_SUCCESS)

    def test_the_report_prints_the_notes_it_collects(self):
        outcome = fw.Result(model_id="m", state=fw.CONVERTIBLE, detail="run it")
        outcome.notes.append("input: https://example.test/x.safetensors")
        stream = io.StringIO()
        fw.report([outcome], stream=stream)
        self.assertIn("input: https://example.test/x.safetensors", stream.getvalue())

    def test_the_real_siglip_entry_is_convertible_and_its_script_exists(self):
        registry = json.loads((REPO_ROOT / "models" / "registry.json").read_bytes())
        plans, _ = fw.build_plans(
            registry,
            REPO_ROOT / "models",
            self.tmp / "weights",
            only=["siglip2-so400m-384"],
            include_placeholders=False,
        )
        (plan,) = plans
        self.assertIsNotNone(plan.conversion)
        script = REPO_ROOT / plan.conversion["script"]
        self.assertTrue(script.is_file(), f"{script} is named by the config")
        outcome = self.run_fetch(plan, self.exploding_fetcher())
        self.assertEqual(outcome.state, fw.CONVERTIBLE)


class RegistryIsFetchable(unittest.TestCase):
    """What the registry must look like for any of the above to be reachable."""

    def setUp(self):
        self.models_root = REPO_ROOT / "models"
        self.registry = json.loads((self.models_root / "registry.json").read_bytes())
        self.configs = {
            entry["model_id"]: json.loads(
                (self.models_root / entry["config"]).read_bytes()
            )
            for entry in self.registry["entries"]
        }

    def test_no_source_url_smuggles_an_archive_member_in_a_parenthetical(self):
        for model_id, config in self.configs.items():
            url = config["weights"]["source_url"]
            with self.subTest(model_id=model_id):
                if url is not None:
                    self.assertNotIn(" ", url.strip())

    def test_an_archive_member_requires_an_archive_to_be_in(self):
        for model_id, config in self.configs.items():
            weights = config["weights"]
            with self.subTest(model_id=model_id):
                if weights.get("archive_member"):
                    self.assertIsNotNone(weights["source_url"])
                    self.assertIsNotNone(fw.artifact_url(weights["source_url"]))

    def test_the_fetcher_installs_where_ml_runtime_loads_from(self):
        """models/weights, or the fetch is decorative.

        ModelCatalog defaults weights_dir to models/weights; a fetcher writing
        anywhere else would fill a directory nothing reads.
        """
        default = fw.parse_args([]).weights_dir
        self.assertEqual(default.resolve(), (self.models_root / "weights").resolve())

    def test_every_entry_can_be_planned_without_touching_the_network(self):
        plans, skipped = fw.build_plans(
            self.registry,
            self.models_root,
            self.models_root / "weights",
            only=[],
            include_placeholders=False,
        )
        planned = {plan.model_id for plan in plans} | {r.model_id for r in skipped}
        self.assertEqual(planned, set(self.configs))
        for entry in skipped:
            self.assertEqual(entry.state, fw.SKIPPED)
            self.assertEqual(
                self.configs[entry.model_id]["rollout"]["state"], "placeholder"
            )

    def test_an_unknown_model_id_is_a_precondition_failure_not_an_empty_success(self):
        with self.assertRaises(fw.Precondition):
            fw.build_plans(
                self.registry,
                self.models_root,
                self.models_root / "weights",
                only=["no-such-model"],
                include_placeholders=False,
            )

    def test_main_reports_precondition_with_its_own_exit_code(self):
        self.assertEqual(fw.main(["--only", "no-such-model"]), fw.EXIT_PRECONDITION)

    def test_weights_directory_contents_are_not_committable(self):
        """190MB of ONNX must never reach git by someone running `git add`."""
        ignore = self.models_root / "weights" / ".gitignore"
        self.assertTrue(ignore.is_file())
        lines = ignore.read_text(encoding="utf-8").splitlines()
        self.assertIn("*", lines)


class ArgumentSurface(unittest.TestCase):
    def test_defaults(self):
        args = fw.parse_args([])
        self.assertEqual(args.only, [])
        self.assertFalse(args.include_placeholders)
        self.assertFalse(args.force)

    def test_only_is_repeatable(self):
        args = fw.parse_args(["--only", "a", "--only", "b"])
        self.assertEqual(args.only, ["a", "b"])

    def test_usage_errors_do_not_look_like_any_of_our_exit_codes(self):
        with self.assertRaises(SystemExit) as caught:
            fw.parse_args(["--nonsense"])
        self.assertEqual(caught.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
