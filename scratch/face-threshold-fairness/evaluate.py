#!/usr/bin/env python3
"""Evaluate the shipped face embedder on demographic verification pairs.

This is an offline research harness, not application code.  It deliberately
does not download face data.  Supply a CSV containing explicit same/different
pairs from a dataset whose licence and access terms you have independently
checked.

Required CSV columns:
  path_a,path_b,is_same,group_a,group_b

Paths are resolved relative to --dataset-root.  ``is_same`` accepts
1/0, true/false, yes/no, or same/different.  ``group_*`` should contain the
demographic stratum used by the dataset protocol (for example, "Indian/F").

The primary safety statistic is the within-group impostor distribution.  The
reported cold-start candidate is the maximum of the per-group thresholds, so
every measured group is held to the requested empirical false-accept rate.
Cross-group and "group involved" distributions are also reported as audits.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import numpy as np
from PIL import Image


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MODEL = (
    REPO_ROOT
    / "apps"
    / "mobile"
    / "assets"
    / "models"
    / "w600k-mbf-512-float32.tflite"
)
EXPECTED_MODEL_SHA256 = (
    "ca17b05ac6e92ff819d81191d865e3864f4e6779df60468f0db547c982091033"
)
INPUT_SIZE = 112
EMBEDDING_SIZE = 512
DEFAULT_TARGET_FAR = 0.005
DEFAULT_MIN_IMPOSTOR_PAIRS = 1_000


@dataclass(frozen=True)
class Pair:
    path_a: Path
    path_b: Path
    is_same: bool
    group_a: str
    group_b: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pairs", required=True, type=Path, help="Pair CSV")
    parser.add_argument(
        "--dataset-root",
        required=True,
        type=Path,
        help="Root against which pair CSV image paths are resolved",
    )
    parser.add_argument(
        "--model", type=Path, default=DEFAULT_MODEL, help="Shipped TFLite model"
    )
    parser.add_argument("--output", required=True, type=Path, help="Output JSON")
    parser.add_argument("--target-far", type=float, default=DEFAULT_TARGET_FAR)
    parser.add_argument(
        "--min-impostor-pairs",
        type=int,
        default=DEFAULT_MIN_IMPOSTOR_PAIRS,
        help="Minimum within-group negatives required for a defensible candidate",
    )
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--threads", type=int, default=4)
    return parser.parse_args()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def parse_bool(value: str, row_number: int) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "same", "genuine"}:
        return True
    if normalized in {"0", "false", "no", "different", "impostor"}:
        return False
    raise ValueError(f"row {row_number}: invalid is_same value {value!r}")


def resolve_image(root: Path, value: str, row_number: int) -> Path:
    raw = Path(value)
    resolved = (raw if raw.is_absolute() else root / raw).resolve()
    if not resolved.is_file():
        raise ValueError(f"row {row_number}: image does not exist: {value!r}")
    return resolved


def load_pairs(csv_path: Path, dataset_root: Path) -> list[Pair]:
    root = dataset_root.resolve()
    required = {"path_a", "path_b", "is_same", "group_a", "group_b"}
    pairs: list[Pair] = []
    with csv_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        missing = required.difference(reader.fieldnames or [])
        if missing:
            raise ValueError(f"pair CSV is missing columns: {sorted(missing)}")
        for row_number, row in enumerate(reader, start=2):
            group_a = row["group_a"].strip()
            group_b = row["group_b"].strip()
            if not group_a or not group_b:
                raise ValueError(f"row {row_number}: demographic group is empty")
            pairs.append(
                Pair(
                    path_a=resolve_image(root, row["path_a"], row_number),
                    path_b=resolve_image(root, row["path_b"], row_number),
                    is_same=parse_bool(row["is_same"], row_number),
                    group_a=group_a,
                    group_b=group_b,
                )
            )
    if not pairs:
        raise ValueError("pair CSV contains no pairs")
    return pairs


def interpreter_class() -> Any:
    errors: list[str] = []
    try:
        from ai_edge_litert.interpreter import Interpreter

        return Interpreter
    except ImportError as error:
        errors.append(f"ai-edge-litert: {error}")
    try:
        from tflite_runtime.interpreter import Interpreter

        return Interpreter
    except ImportError as error:
        errors.append(f"tflite-runtime: {error}")
    try:
        from tensorflow.lite import Interpreter

        return Interpreter
    except ImportError as error:
        errors.append(f"tensorflow: {error}")
    raise RuntimeError(
        "No TFLite interpreter is installed for the scratch harness. "
        + " | ".join(errors)
    )


class Embedder:
    def __init__(self, model_path: Path, threads: int, batch_size: int) -> None:
        actual_hash = sha256_file(model_path)
        if actual_hash != EXPECTED_MODEL_SHA256:
            raise ValueError(
                "model hash mismatch: expected "
                f"{EXPECTED_MODEL_SHA256}, received {actual_hash}"
            )
        self.model_hash = actual_hash
        self.batch_size = batch_size
        Interpreter = interpreter_class()
        self.interpreter = Interpreter(model_path=str(model_path), num_threads=threads)
        inputs = self.interpreter.get_input_details()
        outputs = self.interpreter.get_output_details()
        if len(inputs) != 1 or len(outputs) != 1:
            raise ValueError("expected exactly one model input and one output")
        self.input_index = int(inputs[0]["index"])
        self.output_index = int(outputs[0]["index"])
        input_signature = tuple(int(v) for v in inputs[0]["shape_signature"])
        output_signature = tuple(int(v) for v in outputs[0]["shape_signature"])
        if (
            input_signature != (-1, INPUT_SIZE, INPUT_SIZE, 3)
            or np.dtype(inputs[0]["dtype"]) != np.dtype(np.float32)
            or output_signature != (-1, EMBEDDING_SIZE)
            or np.dtype(outputs[0]["dtype"]) != np.dtype(np.float32)
        ):
            raise ValueError(
                f"unexpected tensor contract: input={input_signature}/"
                f"{inputs[0]['dtype']}, output={output_signature}/{outputs[0]['dtype']}"
            )

    @staticmethod
    def preprocess(path: Path) -> tuple[np.ndarray, bool]:
        # Dataset inputs must already be canonical aligned face crops.  Resizing
        # is allowed because public protocols distribute several crop sizes;
        # this harness intentionally does not pretend resizing a raw face is
        # equivalent to the app's landmark alignment pipeline.
        with Image.open(path) as source:
            rgb = source.convert("RGB")
            resized = rgb.size != (INPUT_SIZE, INPUT_SIZE)
            if resized:
                rgb = rgb.resize(
                    (INPUT_SIZE, INPUT_SIZE), resample=Image.Resampling.BILINEAR
                )
            pixels = np.asarray(rgb, dtype=np.float32)
        return (pixels - np.float32(127.5)) / np.float32(127.5), resized

    def embed(self, paths: Iterable[Path]) -> tuple[dict[Path, np.ndarray], int]:
        ordered = sorted(set(paths), key=lambda path: str(path))
        embeddings: dict[Path, np.ndarray] = {}
        resized_count = 0
        for offset in range(0, len(ordered), self.batch_size):
            batch_paths = ordered[offset : offset + self.batch_size]
            arrays: list[np.ndarray] = []
            for path in batch_paths:
                array, resized = self.preprocess(path)
                arrays.append(array)
                resized_count += int(resized)
            tensor = np.stack(arrays).astype(np.float32, copy=False)
            self.interpreter.resize_tensor_input(
                self.input_index, tensor.shape, strict=False
            )
            self.interpreter.allocate_tensors()
            self.interpreter.set_tensor(self.input_index, tensor)
            self.interpreter.invoke()
            # The model emits float32, but the app converts those values to JS
            # numbers and accumulates its L2 norm/cosine in float64.  Match that
            # comparison path instead of letting NumPy keep a float32 reducer.
            output = np.asarray(
                self.interpreter.get_tensor(self.output_index), dtype=np.float64
            )
            norms = np.linalg.norm(output, axis=1, keepdims=True)
            if not np.all(np.isfinite(output)) or np.any(norms <= 0):
                raise ValueError("model returned a non-finite or zero embedding")
            output = output / norms
            for path, embedding in zip(batch_paths, output, strict=True):
                embeddings[path] = embedding
        return embeddings, resized_count


def distribution(scores: np.ndarray) -> dict[str, Any]:
    if scores.size == 0:
        return {"count": 0}
    quantiles = {
        "p50": 0.5,
        "p90": 0.9,
        "p95": 0.95,
        "p99": 0.99,
        "p99_5": 0.995,
        "p99_9": 0.999,
    }
    return {
        "count": int(scores.size),
        "mean": float(np.mean(scores)),
        "stddev": float(np.std(scores)),
        "min": float(np.min(scores)),
        **{
            label: float(np.quantile(scores, q, method="linear"))
            for label, q in quantiles.items()
        },
        "max": float(np.max(scores)),
    }


def conservative_far_threshold(scores: np.ndarray, target_far: float) -> float:
    """Smallest representable threshold with empirical FAR <= target.

    The application accepts a pair when cosine >= threshold.  Stepping above
    the first score that must be rejected handles ties conservatively instead
    of claiming a fractional order statistic was observed.
    """

    if scores.size == 0:
        raise ValueError("cannot derive a threshold without impostor scores")
    descending = np.sort(scores)[::-1]
    allowed_accepts = math.floor(target_far * scores.size + 1e-12)
    if allowed_accepts >= scores.size:
        return float("-inf")
    boundary = descending[allowed_accepts]
    return float(np.nextafter(boundary, np.float64(math.inf)))


def far(scores: np.ndarray, threshold: float) -> tuple[int, float]:
    accepts = int(np.count_nonzero(scores >= threshold))
    return accepts, accepts / int(scores.size)


def wilson_interval(successes: int, total: int, z: float = 1.95996398454) -> list[float]:
    if total <= 0:
        return [math.nan, math.nan]
    p = successes / total
    denominator = 1 + z * z / total
    center = (p + z * z / (2 * total)) / denominator
    radius = (
        z
        * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total))
        / denominator
    )
    return [max(0.0, center - radius), min(1.0, center + radius)]


def arrays_by_key(values: dict[str, list[float]]) -> dict[str, np.ndarray]:
    return {
        key: np.asarray(scores, dtype=np.float64)
        for key, scores in sorted(values.items())
    }


def evaluate(
    pairs: list[Pair], embeddings: dict[Path, np.ndarray], target_far: float
) -> dict[str, Any]:
    within_impostors: dict[str, list[float]] = defaultdict(list)
    within_genuine: dict[str, list[float]] = defaultdict(list)
    pair_strata_impostors: dict[str, list[float]] = defaultdict(list)
    involved_impostors: dict[str, list[float]] = defaultdict(list)
    all_impostors: list[float] = []
    all_genuine: list[float] = []

    for pair in pairs:
        score = float(np.dot(embeddings[pair.path_a], embeddings[pair.path_b]))
        pair_key = " × ".join(sorted((pair.group_a, pair.group_b)))
        if pair.is_same:
            all_genuine.append(score)
            if pair.group_a == pair.group_b:
                within_genuine[pair.group_a].append(score)
            continue
        all_impostors.append(score)
        pair_strata_impostors[pair_key].append(score)
        for group in {pair.group_a, pair.group_b}:
            involved_impostors[group].append(score)
        if pair.group_a == pair.group_b:
            within_impostors[pair.group_a].append(score)

    within = arrays_by_key(within_impostors)
    if not within:
        raise ValueError(
            "no same-demographic impostor pairs; worst-group FAR cannot be measured"
        )
    group_thresholds = {
        group: conservative_far_threshold(scores, target_far)
        for group, scores in within.items()
    }
    candidate = max(group_thresholds.values())
    worst_groups = sorted(
        group
        for group, threshold in group_thresholds.items()
        if threshold == candidate
    )

    def audit(groups: dict[str, np.ndarray]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for group, scores in groups.items():
            accepts, measured_far = far(scores, candidate)
            result[group] = {
                "distribution": distribution(scores),
                "false_accepts_at_candidate": accepts,
                "far_at_candidate": measured_far,
                "far_wilson_95pct": wilson_interval(accepts, int(scores.size)),
            }
        return result

    within_audit = audit(within)
    for group, threshold in group_thresholds.items():
        within_audit[group]["group_specific_threshold"] = threshold

    return {
        "score": "cosine similarity; accept when score >= threshold",
        "target_far": target_far,
        "candidate_threshold": candidate,
        "candidate_rule": "maximum conservative empirical threshold across within-demographic impostor groups",
        "worst_served_groups": worst_groups,
        "within_group_impostors": within_audit,
        "group_involved_impostors": audit(arrays_by_key(involved_impostors)),
        "all_pair_strata_impostors": audit(arrays_by_key(pair_strata_impostors)),
        "within_group_genuine": {
            group: distribution(scores)
            for group, scores in arrays_by_key(within_genuine).items()
        },
        "all_impostors": distribution(np.asarray(all_impostors, dtype=np.float64)),
        "all_genuine": distribution(np.asarray(all_genuine, dtype=np.float64)),
    }


def main() -> int:
    args = parse_args()
    if not 0 < args.target_far < 1:
        raise ValueError("--target-far must be between zero and one")
    if args.min_impostor_pairs < 1 or args.batch_size < 1 or args.threads < 1:
        raise ValueError("pair, batch, and thread counts must be positive")

    pairs = load_pairs(args.pairs, args.dataset_root)
    embedder = Embedder(args.model.resolve(), args.threads, args.batch_size)
    image_paths = [path for pair in pairs for path in (pair.path_a, pair.path_b)]
    embeddings, resized_count = embedder.embed(image_paths)
    result = evaluate(pairs, embeddings, args.target_far)

    underpowered = sorted(
        group
        for group, stats in result["within_group_impostors"].items()
        if stats["distribution"]["count"] < args.min_impostor_pairs
    )
    result = {
        "harness": {
            "model_path": str(args.model.resolve().relative_to(REPO_ROOT)),
            "model_sha256": embedder.model_hash,
            "preprocessing": "aligned RGB crop; resize to 112x112 bilinear; (channel - 127.5) / 127.5; output L2-normalized",
            "pairs": len(pairs),
            "unique_images": len(embeddings),
            "resized_images": resized_count,
            "min_impostor_pairs": args.min_impostor_pairs,
        },
        "measurement_valid": not underpowered,
        "underpowered_groups": underpowered,
        **result,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")

    print(f"model: {embedder.model_hash}")
    print(f"pairs: {len(pairs)}; unique images: {len(embeddings)}")
    print(f"candidate threshold: {result['candidate_threshold']:.8f}")
    print(f"worst-served group(s): {', '.join(result['worst_served_groups'])}")
    if underpowered:
        print(
            "NOT VALID FOR A SHIPPED RECOMMENDATION; too few within-group "
            f"impostor pairs for: {', '.join(underpowered)}",
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1) from error
