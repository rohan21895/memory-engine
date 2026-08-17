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

import json
import re
import unittest
from pathlib import Path

CONTRACTS = Path(__file__).resolve().parent.parent
PROTO_PATH = CONTRACTS / "proto" / "ml_runtime.proto"
PROTO_TEXT = PROTO_PATH.read_text(encoding="utf-8")

COMMON = json.loads((CONTRACTS / "schemas" / "common.schema.json").read_text(encoding="utf-8"))
FACE = json.loads((CONTRACTS / "schemas" / "face-record.schema.json").read_text(encoding="utf-8"))

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


def _enum_block(name: str) -> str:
    match = re.search(rf"^enum {name} \{{(.*?)^\}}", PROTO_TEXT, re.MULTILINE | re.DOTALL)
    if not match:
        raise AssertionError(f"enum {name} not found in the proto")
    return match.group(1)


def _enum_values(name: str) -> set[str]:
    """Enum value names, minus the mandatory `*_UNSPECIFIED` zero value."""
    names = set(re.findall(r"^\s*(\w+)\s*=\s*\d+;", _enum_block(name), re.MULTILINE))
    return {n for n in names if not n.endswith("_UNSPECIFIED")}


def _schema_strings(name: str, values: set[str]) -> set[str]:
    """`RUNTIME_TARGET_ONNXRUNTIME_CUDA` -> `onnxruntime_cuda`."""
    prefix = re.sub(r"(?<!^)(?=[A-Z])", "_", name).upper() + "_"
    return {v[len(prefix):].lower() for v in values}


class TestNoPathToOriginalMedia(unittest.TestCase):
    """Criterion 4, enforced by absence."""

    def test_infer_item_accepts_only_a_proxy_or_tensors(self):
        fields = _field_names("InferItem")
        self.assertIn("proxy_id", fields)
        self.assertIn("tensors", fields)
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

    def test_runtime_selection_is_expressible_and_reported_back(self):
        self.assertIn("preferred_runtimes", _field_names("InferRequest"))
        self.assertIn("runtime_used", _field_names("InferResponse"))

    def test_coreml_directml_and_cuda_are_all_declared(self):
        for runtime in ("COREML", "DIRECTML", "CUDA", "CPU"):
            with self.subTest(runtime=runtime):
                self.assertIn(f"RUNTIME_TARGET_ONNXRUNTIME_{runtime}", PROTO_TEXT)

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

    def test_the_pin_also_carries_a_config_digest(self):
        """Weights alone do not pin behaviour.

        SCRFD at score_threshold 0.5 and at 0.6 are different detectors to every
        consumer, and the preprocessing constants live in the config too -- the
        mean/std/scale bug that collapsed the input range to a 0.016-wide sliver
        changed no weights byte at all. A pin that cannot distinguish those runs
        is the appearance of reproducibility rather than reproducibility.
        """
        self.assertIn("config_blake3", _field_names("ModelPin"))
        self.assertIn("ERROR_CODE_CONFIG_MISMATCH", PROTO_TEXT)

    def test_a_whole_request_can_fail_distinguishably_from_an_empty_one(self):
        """`results: []` with no error means "ran, found nothing". A request
        that died before the batch loop must not be reportable the same way."""
        self.assertIn("error", _field_names("InferResponse"))

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


class TestSchemaParity(unittest.TestCase):
    """The proto and the JSON Schemas describe the same objects.

    Every one of these could be kept true by hand, and would eventually not be.
    A pin whose enum has drifted from ModelRef's does not fail loudly -- it
    round-trips into a record with a value the schema rejects, at write time,
    far from the edit that caused it.
    """

    def test_model_pin_is_a_model_ref_plus_the_config_digest(self):
        schema = set(COMMON["$defs"]["ModelRef"]["properties"])
        self.assertEqual(
            schema | {"config_blake3"},
            _field_names("ModelPin"),
            "ModelPin must serialise into a ModelRef unchanged; config_blake3 is "
            "the one deliberate addition.",
        )

    def test_runtime_target_matches_the_schema_enum(self):
        self.assertEqual(
            set(COMMON["$defs"]["RuntimeTarget"]["enum"]),
            _schema_strings("RuntimeTarget", _enum_values("RuntimeTarget")),
        )

    def test_precision_matches_the_schema_enum(self):
        schema = next(
            branch["enum"]
            for branch in COMMON["$defs"]["ModelRef"]["properties"]["precision"]["oneOf"]
            if "enum" in branch
        )
        self.assertEqual(
            set(schema),
            _schema_strings("Precision", _enum_values("Precision")),
            "int8 and fp16 runs differ from fp32 at the third decimal, which is "
            "enough to flip a borderline face match -- a precision the proto "
            "cannot express is a precision that gets recorded as something else.",
        )

    def test_landmark_scheme_matches_face_record(self):
        schema = FACE["$defs"]["Landmarks"]["properties"]["scheme"]["enum"]
        self.assertEqual(
            set(schema),
            _schema_strings("LandmarkScheme", _enum_values("LandmarkScheme")),
        )

    def test_geometry_messages_mirror_their_schema_definitions(self):
        for message, definition in (
            ("NormalizedBox", COMMON["$defs"]["NormalizedBox"]),
            ("Point2D", COMMON["$defs"]["Point2D"]),
            ("RationalTime", COMMON["$defs"]["RationalTime"]),
            ("TimeRange", COMMON["$defs"]["TimeRange"]),
        ):
            with self.subTest(message=message):
                self.assertEqual(set(definition["properties"]), _field_names(message))


class TestPinRoundTrip(unittest.TestCase):
    """Field-name parity is necessary and not sufficient.

    The parity test above compares field NAMES and passed while the VALUE
    domains disagreed: proto3 has no null, so an unpinned hash is `""` and an
    unset enum is `*_UNSPECIFIED`, none of which ModelRef accepts. Codex found
    that a development-mode inference -- which is every inference today, since
    no weight is pinned -- could not be persisted at all.
    """

    def setUp(self):
        import sys

        sys.path.insert(0, str(CONTRACTS.parent))
        from contracts.proto.model_pin import (  # type: ignore
            PinConversionError, from_model_ref, to_model_ref,
        )

        self.to_ref = to_model_ref
        self.from_ref = from_model_ref
        self.error = PinConversionError

    def _validate(self, ref: dict):
        from jsonschema import Draft202012Validator

        common = json.loads(
            (CONTRACTS / "schemas" / "common.schema.json").read_text(encoding="utf-8")
        )
        schema = dict(common["$defs"]["ModelRef"])
        schema["$defs"] = common["$defs"]
        errors = [e.message for e in Draft202012Validator(schema).iter_errors(ref)]
        self.assertEqual([], errors, f"not a valid ModelRef: {ref}")

    UNPINNED = {
        "model_id": "scrfd-10g-bnkps",
        "version": "1.0.0",
        "weights_blake3": "",
        "config_blake3": "",
        "runtime": "RUNTIME_TARGET_UNSPECIFIED",
        "precision": "PRECISION_UNSPECIFIED",
    }
    PINNED = {
        "model_id": "siglip2-so400m-384",
        "version": "2.0.0",
        "weights_blake3": "a" * 64,
        "config_blake3": "c" * 64,
        "runtime": "RUNTIME_TARGET_ONNXRUNTIME_COREML",
        "precision": "PRECISION_FP16",
    }

    def test_a_development_pin_converts_to_a_valid_model_ref(self):
        """The case that was impossible: nothing is pinned yet, so this is
        every run today."""
        ref = self.to_ref(self.UNPINNED)
        self.assertIsNone(ref["weights_blake3"])
        self.assertIsNone(ref["runtime"])
        self._validate(ref)

    def test_a_fully_pinned_pin_converts_and_keeps_every_value(self):
        ref = self.to_ref(self.PINNED)
        self.assertEqual("a" * 64, ref["weights_blake3"])
        self.assertEqual("onnxruntime_coreml", ref["runtime"])
        self.assertEqual("fp16", ref["precision"])
        self._validate(ref)

    def test_the_conversion_round_trips_both_ways(self):
        for pin in (self.UNPINNED, self.PINNED):
            with self.subTest(pin=pin["model_id"]):
                self.assertEqual(pin, self.from_ref(self.to_ref(pin)))

    def test_enum_values_map_onto_the_schema_vocabulary(self):
        """Every proto runtime and precision must land on a value the schema
        accepts -- checked exhaustively rather than on the two above."""
        for value in _enum_values("RuntimeTarget"):
            with self.subTest(runtime=value):
                ref = self.to_ref({**self.PINNED, "runtime": value})
                self._validate(ref)
        for value in _enum_values("Precision"):
            with self.subTest(precision=value):
                self._validate(self.to_ref({**self.PINNED, "precision": value}))

    def test_a_malformed_hash_fails_at_the_conversion(self):
        """Rather than reaching a record, where it would look like provenance."""
        with self.assertRaises(self.error):
            self.to_ref({**self.PINNED, "weights_blake3": "not-a-hash"})

    def test_a_pin_without_identity_is_refused(self):
        for missing in ("model_id", "version"):
            with self.subTest(field=missing):
                with self.assertRaises(self.error):
                    self.to_ref({**self.PINNED, missing: ""})


class TestPostprocessedOutput(unittest.TestCase):
    """Decoding a detector head belongs to the host, exactly once.

    Anchor decoding and NMS on the caller's side would be written twice -- once
    in Python for the pipeline, once in Rust for the desktop shell -- and the
    two would drift silently, because a box wrong by an anchor stride still
    looks like a box.
    """

    def test_detections_are_returned_decoded_not_raw(self):
        self.assertIn("detections", _field_names("InferResult"))
        fields = _field_names("Detection")
        self.assertIn("box", fields)
        self.assertIn("score", fields)
        self.assertIn("landmarks", fields)

    def test_raw_tensors_are_still_available_for_embeddings(self):
        self.assertIn("tensors", _field_names("InferResult"))

    def test_the_caller_knows_which_output_to_expect_before_calling(self):
        self.assertIn("output_kind", _field_names("ModelInfo"))

    def test_a_detector_reports_the_thresholds_it_actually_applied(self):
        """The config-digest argument in miniature: the caller must be able to
        see the decision boundary move rather than infer it from a changed
        face count."""
        fields = _field_names("DetectionSet")
        self.assertIn("score_threshold", fields)
        self.assertIn("nms_iou_threshold", fields)

    def test_truncation_is_stated_rather_than_implied(self):
        """Silently taking the top 100 faces in a crowd shot and reporting
        "100 people" is wrong in a way nothing downstream can catch."""
        self.assertIn("truncated", _field_names("DetectionSet"))

    def test_landmarks_carry_their_ordering_convention(self):
        """Five points in ArcFace order and five points in some other order
        produce a plausible warp and a wrong embedding."""
        self.assertIn("landmark_scheme", _field_names("InferItem"))
        self.assertIn("landmark_scheme", _field_names("Detection"))

    def test_multi_input_models_are_expressible(self):
        """Image + text for a SigLIP similarity head. A contract that can only
        carry one input forces those callers to invent an encoding."""
        self.assertIn("tensors", _field_names("TensorSet"))
        self.assertIn("input_names", _field_names("ModelInfo"))


class TestCoordinateSpaceIsUnambiguous(unittest.TestCase):
    def test_no_pixel_coordinate_fields_cross_the_interface(self):
        """One space, declared in the type. A box computed on a 512px proxy has
        to be valid against the 6000px original without a rescale step someone
        will eventually forget."""
        pixelish = {"x_px", "y_px", "width_px", "height_px", "pixel_width",
                    "pixel_height", "image_width", "image_height"}
        declared = set(
            re.findall(r"^\s*(?:repeated\s+|optional\s+)?[\w.]+\s+(\w+)\s*=\s*\d+;",
                       PROTO_TEXT, re.MULTILINE)
        )
        self.assertEqual(set(), declared & pixelish)

    def test_video_windows_use_rational_time_not_float_seconds(self):
        """30000/1001 has no exact float form; a contract that rounds it
        accumulates drift over a long timeline."""
        self.assertEqual({"start_time", "duration"}, _field_names("TimeRange"))
        self.assertIn("window", _field_names("InferItem"))
        self.assertIn("RationalTime time", _message_block("ShotBoundary"))


class TestGeneratedStubsAreFresh(unittest.TestCase):
    def test_stub_digest_matches_the_proto(self):
        """Catches a proto edited without regenerating, with no toolchain needed."""
        import hashlib

        stamp = CONTRACTS / "proto" / "generated" / "python" / "PROTO_DIGEST"
        self.assertTrue(stamp.is_file(), "generated stubs are missing their digest stamp")
        # One digest over every proto, matching generate.py. A per-file digest
        # would let a second proto be edited without this noticing.
        from contracts.proto.generate import PROTOS  # type: ignore

        digest = hashlib.sha256()
        for name in PROTOS:
            digest.update(name.encode("utf-8"))
            digest.update((CONTRACTS / "proto" / name).read_bytes())
        expected = digest.hexdigest()
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
