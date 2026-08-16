"""The ModelPin <-> ModelRef conversion, as one implementation both sides use.

WHY THIS FILE EXISTS

`ModelPin` (contracts/proto/ml_runtime.proto) and `ModelRef`
(contracts/schemas/common.schema.json) describe the same thing, and the proto
header claims a pin "round-trips losslessly into a ModelRef". Codex checked that
claim and it was false at the VALUE level even though it held at the field level:

  * proto3 has no null. An unpinned weights hash is the empty string `""`,
    while ModelRef requires either a 64-character hash or JSON `null`. Protobuf
    JSON emits `""` or omits the field, and neither validates.
  * proto3 enums have a zero value. `RUNTIME_TARGET_UNSPECIFIED` and
    `PRECISION_UNSPECIFIED` mean "not stated", which is `null` in the schema,
    not a member of the schema's enum.

Development mode deliberately permits unpinned weights, and every weight in the
registry is currently unpinned -- so "a development inference cannot be
persisted" was not an edge case, it was every run.

The parity test in contracts/tests/test_ml_runtime_proto.py compares FIELD NAMES,
which is exactly why it missed this. Field-name parity is necessary and not
sufficient; the value domains have to be mapped, and the mapping has to live in
one place rather than being reimplemented by whoever writes the record.

THE MAPPING

    proto                              schema
    ---------------------------------  ---------------------------------
    weights_blake3 == ""               null
    config_blake3  == ""               null
    RUNTIME_TARGET_UNSPECIFIED         null
    PRECISION_UNSPECIFIED              null
    RUNTIME_TARGET_ONNXRUNTIME_CUDA    "onnxruntime_cuda"
    PRECISION_FP16                     "fp16"

Empty-string-means-absent is a proto3 fact, not a convention we chose, so the
conversion is the place it stops. Nothing downstream of `to_model_ref` should
ever see a `""` where a hash belongs.
"""

from __future__ import annotations

import re

HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")

_RUNTIME_PREFIX = "RUNTIME_TARGET_"
_PRECISION_PREFIX = "PRECISION_"


class PinConversionError(ValueError):
    """The pin cannot be expressed as a valid ModelRef."""


def _enum_to_schema(name: str, prefix: str) -> str | None:
    """`RUNTIME_TARGET_ONNXRUNTIME_CUDA` -> `onnxruntime_cuda`; zero value -> None."""
    if not name or name == f"{prefix}UNSPECIFIED":
        return None
    if not name.startswith(prefix):
        raise PinConversionError(f"{name!r} is not a {prefix}* value")
    return name[len(prefix):].lower()


def _hash_to_schema(value: str, field: str) -> str | None:
    if not value:
        return None
    if not HASH_PATTERN.match(value):
        raise PinConversionError(
            f"{field}={value!r} is neither empty nor a 64-character lowercase "
            "hex BLAKE3. A malformed hash must fail here rather than reach a "
            "record, where it would look like provenance."
        )
    return value


def to_model_ref(pin: dict) -> dict:
    """A ModelPin (as a plain dict, from protobuf JSON) -> a valid ModelRef dict.

    Accepts the proto's own spellings and returns what common.schema.json
    accepts. Raises rather than guessing: a pin missing its model_id is not a
    pin, and silently writing a partial one produces a record that claims
    provenance it does not have.
    """
    model_id = pin.get("model_id") or ""
    version = pin.get("version") or ""
    if not model_id:
        raise PinConversionError("ModelPin has no model_id")
    if not version:
        raise PinConversionError(f"ModelPin {model_id!r} has no version")

    return {
        "model_id": model_id,
        "version": version,
        "weights_blake3": _hash_to_schema(pin.get("weights_blake3", ""), "weights_blake3"),
        "config_blake3": _hash_to_schema(pin.get("config_blake3", ""), "config_blake3"),
        "runtime": _enum_to_schema(pin.get("runtime", "") or "", _RUNTIME_PREFIX),
        "precision": _enum_to_schema(pin.get("precision", "") or "", _PRECISION_PREFIX),
    }


def from_model_ref(ref: dict) -> dict:
    """The inverse, for a caller pinning an expectation from a stored record.

    Null becomes the empty string and the enum zero value, which is what proto3
    can carry. `InferRequest.expected_pin` treats empty fields as unconstrained,
    so a record with an unpinned hash produces a pin that constrains everything
    it actually knows and nothing it does not -- which is the correct behaviour
    rather than a lossy one.
    """
    def enum(value: str | None, prefix: str) -> str:
        return f"{prefix}UNSPECIFIED" if value is None else f"{prefix}{value.upper()}"

    return {
        "model_id": ref["model_id"],
        "version": ref["version"],
        "weights_blake3": ref.get("weights_blake3") or "",
        "config_blake3": ref.get("config_blake3") or "",
        "runtime": enum(ref.get("runtime"), _RUNTIME_PREFIX),
        "precision": enum(ref.get("precision"), _PRECISION_PREFIX),
    }
