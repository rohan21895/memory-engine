"""The ml-runtime gRPC contract, checked structurally rather than by reading it.

Two of issue #10's acceptance criteria are properties of the message shapes, so
they are asserted against the compiled descriptors:

* **Criterion 4** -- requests reference proxies and derived tensors only, and no
  API path permits opening original media. This is enforced by *absence*: there
  is no field anywhere that can hold a path, a URL or a media id. An absence is
  easy to reintroduce by accident, so a test guards it.

* **Criterion 5** -- batching, deadlines and cancellation, provider selection,
  and retryable versus terminal errors are all expressible.

The tests run against the generated stubs when a protobuf runtime is present,
and fall back to parsing the .proto as text when it is not. CI installs only
pydantic and jsonschema, and .github/ belongs to Codex, so the fallback is what
actually runs there -- weaker, but it still catches a field named `path` being
added to a request.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

CONTRACTS = Path(__file__).resolve().parent.parent
PROTO_PATH = CONTRACTS / "proto" / "ml_runtime.proto"
PROTO_TEXT = PROTO_PATH.read_text(encoding="utf-8")

# Names that would let a caller point the runtime at a file rather than a proxy.
FORBIDDEN_INPUT_FIELDS = {
    "path",
    "file_path",
    "filepath",
    "url",
    "uri",
    "source_path",
    "media_id",
    "original_path",
    "filename",
}


def _descriptors():
    """Compiled descriptors, or None when no protobuf runtime is installed."""
    try:
        import sys

        sys.path.insert(0, str(CONTRACTS.parent))
        from contracts.proto.generated.python import ml_runtime_pb2  # type: ignore

        return ml_runtime_pb2
    except Exception:
        return None


def _message_block(name: str) -> str:
    """Crude text extraction of one message body, for the no-runtime path."""
    match = re.search(rf"^message {name} \{{(.*?)^\}}", PROTO_TEXT, re.MULTILINE | re.DOTALL)
    if not match:
        raise AssertionError(f"message {name} not found in the proto")
    return match.group(1)


def _field_names(name: str) -> set[str]:
    pb = _descriptors()
    if pb is not None:
        return {f.name for f in getattr(pb, name).DESCRIPTOR.fields}
    body = _message_block(name)
    # `[repeated|optional] <type> <name> = N;`
    return set(re.findall(r"^\s*(?:repeated\s+|optional\s+)?[\w.]+\s+(\w+)\s*=\s*\d+;",
                          body, re.MULTILINE))


class TestNoPathToOriginalMedia(unittest.TestCase):
    """Criterion 4, enforced by absence."""

    def test_infer_item_accepts_only_a_proxy_or_a_tensor(self):
        fields = _field_names("InferItem")
        self.assertIn("proxy_id", fields)
        self.assertIn("tensor", fields)
        offending = fields & FORBIDDEN_INPUT_FIELDS
        self.assertEqual(
            set(),
            offending,
            f"InferItem gained {sorted(offending)} -- analysis must never be able to "
            "name an original file. Sources are opened exactly twice in their life, "
            "at proxy generation and at final render, and neither goes through here.",
        )

    def test_no_request_message_can_name_a_file(self):
        for message in ("InferRequest", "LoadModelRequest", "ListModelsRequest",
                        "HealthRequest", "UnloadModelRequest"):
            with self.subTest(message=message):
                offending = _field_names(message) & FORBIDDEN_INPUT_FIELDS
                self.assertEqual(set(), offending, f"{message} gained {sorted(offending)}")

    def test_the_proto_declares_no_path_typed_field_anywhere(self):
        """Belt and braces: catch a path field added to a nested message that
        the explicit list above does not yet name."""
        declared = set(
            re.findall(r"^\s*(?:repeated\s+|optional\s+)?[\w.]+\s+(\w+)\s*=\s*\d+;",
                       PROTO_TEXT, re.MULTILINE)
        )
        offending = declared & FORBIDDEN_INPUT_FIELDS
        self.assertEqual(set(), offending, f"proto gained {sorted(offending)}")


class TestBatchingDeadlinesAndProviders(unittest.TestCase):
    """Criterion 5."""

    def test_requests_are_batched(self):
        self.assertIn("items", _field_names("InferRequest"))
        self.assertIn("results", _field_names("InferResponse"))

    def test_requests_carry_a_deadline_and_a_priority(self):
        fields = _field_names("InferRequest")
        self.assertIn("deadline_ms", fields)
        self.assertIn("priority", fields)

    def test_requests_are_idempotent_by_id(self):
        """Re-sending after a timeout must not run the work twice."""
        self.assertIn("request_id", _field_names("InferRequest"))

    def test_provider_selection_is_expressible_and_reported_back(self):
        self.assertIn("preferred_providers", _field_names("InferRequest"))
        self.assertIn("provider_used", _field_names("InferResponse"))

    def test_coreml_directml_and_cuda_are_all_declared(self):
        for provider in ("COREML", "DIRECTML", "CUDA", "CPU"):
            with self.subTest(provider=provider):
                self.assertIn(f"EXECUTION_PROVIDER_{provider}", PROTO_TEXT)

    def test_cancellation_is_representable(self):
        self.assertIn("ERROR_CODE_CANCELLED", PROTO_TEXT)
        self.assertIn("ERROR_CODE_DEADLINE_EXCEEDED", PROTO_TEXT)

    def test_a_streaming_rpc_exists_for_long_sweeps(self):
        self.assertRegex(PROTO_TEXT, r"rpc InferStream\(stream InferRequest\)")


class TestErrorsAndPins(unittest.TestCase):
    def test_errors_say_whether_retrying_could_help(self):
        fields = _field_names("InferError")
        self.assertIn("retryable", fields)
        self.assertIn("retry_after_ms", fields)

    def test_terminal_and_retryable_codes_are_numbered_apart(self):
        """Terminal codes are 1-99 and retryable ones 100+, so a switch that has
        not been updated for a new code still classifies it correctly."""
        codes = dict(
            (name, int(number))
            for name, number in re.findall(r"(ERROR_CODE_\w+)\s*=\s*(\d+);", PROTO_TEXT)
        )
        self.assertTrue(codes)
        for name, number in codes.items():
            if name == "ERROR_CODE_UNSPECIFIED":
                continue
            with self.subTest(code=name):
                retryable_by_name = name in {
                    "ERROR_CODE_RESOURCE_EXHAUSTED",
                    "ERROR_CODE_PROVIDER_UNAVAILABLE",
                    "ERROR_CODE_DEADLINE_EXCEEDED",
                    "ERROR_CODE_CANCELLED",
                    "ERROR_CODE_MODEL_LOADING",
                    "ERROR_CODE_INTERNAL",
                }
                self.assertEqual(
                    retryable_by_name,
                    number >= 100,
                    f"{name}={number} sits on the wrong side of the 100 boundary",
                )

    def test_results_are_per_item_so_one_bad_file_does_not_fail_a_batch(self):
        """The difference between a scan that reports one bad file and one that
        fails wholesale."""
        fields = _field_names("InferResult")
        self.assertIn("item_id", fields)
        self.assertIn("error", fields)

    def test_every_response_reports_the_pin_actually_used(self):
        self.assertIn("pin", _field_names("InferResponse"))
        self.assertIn("expected_pin", _field_names("InferRequest"))

    def test_the_pin_carries_a_weights_hash(self):
        self.assertIn("weights_blake3", _field_names("ModelPin"))

    def test_unloadable_reasons_cover_the_load_policy(self):
        """Every gate the load policy can refuse on must be reportable."""
        for reason in (
            "NOT_REGISTERED",
            "HASH_MISMATCH",
            "HASH_UNPINNED",
            "LICENSE_UNVERIFIED",
            "LICENSE_BLOCKS_RELEASE",
            "NO_PROVIDER_AVAILABLE",
        ):
            with self.subTest(reason=reason):
                self.assertIn(f"UNLOADABLE_REASON_{reason}", PROTO_TEXT)

    def test_the_host_reports_which_load_mode_it_is_running(self):
        """A caller must be able to tell it is talking to a permissive
        development host before trusting its results."""
        self.assertIn("load_mode", _field_names("ListModelsResponse"))
        self.assertIn("load_mode", _field_names("HealthResponse"))
        self.assertIn("LOAD_MODE_DEVELOPMENT", PROTO_TEXT)


class TestGeneratedStubsAreFresh(unittest.TestCase):
    def test_stub_digest_matches_the_proto(self):
        """Catches a proto edited without regenerating, with no toolchain needed."""
        import hashlib

        stamp = CONTRACTS / "proto" / "generated" / "python" / "PROTO_DIGEST"
        self.assertTrue(stamp.is_file(), "generated stubs are missing their digest stamp")
        expected = hashlib.sha256(PROTO_PATH.read_bytes()).hexdigest()
        self.assertEqual(
            expected,
            stamp.read_text(encoding="utf-8").strip(),
            "ml_runtime.proto changed but its generated stubs did not; run "
            "contracts/proto/generate.py",
        )

    def test_stubs_import_when_a_protobuf_runtime_is_present(self):
        pb = _descriptors()
        if pb is None:
            self.skipTest("no protobuf runtime installed")
        self.assertIn("MlRuntime", pb.DESCRIPTOR.services_by_name)


if __name__ == "__main__":
    unittest.main()
