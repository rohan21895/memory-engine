"""Run the real local photo spine and print a machine-readable breakage report."""

from __future__ import annotations

import argparse
import copy
import json
import os
import subprocess
import tempfile
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import grpc
from blake3 import blake3

from contracts.proto.generated.python import ml_runtime_pb2 as pb2
from contracts.proto.generated.python import ml_runtime_pb2_grpc as pb2_grpc

from .catalog import DEFAULT_REPO_ROOT, ModelCatalog
from .media_db import MediaDbProxyResolver, database_type
from .service import MlRuntimeService, start_server

RUNTIME_ARGUMENTS = {
    "cpu": pb2.RUNTIME_TARGET_ONNXRUNTIME_CPU,
    "coreml": pb2.RUNTIME_TARGET_ONNXRUNTIME_COREML,
    "directml": pb2.RUNTIME_TARGET_ONNXRUNTIME_DIRECTML,
    "cuda": pb2.RUNTIME_TARGET_ONNXRUNTIME_CUDA,
}

MODEL_PREFERENCE = ("yunet-2023mar", "scrfd-10g-bnkps")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _compact_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _source_locator_digest(source: Path) -> str:
    normalized = unicodedata.normalize("NFC", str(source.resolve())).rstrip(os.sep)
    return blake3(normalized.encode("utf-8")).hexdigest()


def build_scan_job(source: Path, repo_root: Path) -> dict[str, Any]:
    """Specialise the golden scan fixture; never invent a parallel JobSpec shape."""

    source = source.resolve(strict=True)
    locator = _source_locator_digest(source)
    params = {
        "follow_symlinks": False,
        "include_hidden": False,
        "max_depth": 32,
    }
    params_digest = blake3(_compact_json(params)).hexdigest()
    job_id = blake3(
        b"real-photo-smoke-v1\0"
        + locator.encode("ascii")
        + b"\0"
        + params_digest.encode("ascii")
    ).hexdigest()
    fixture_path = (
        repo_root
        / "contracts"
        / "fixtures"
        / "job-spec"
        / "valid"
        / "job-scan-source-root-a.json"
    )
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    job = copy.deepcopy(fixture)
    job["job_id"] = job_id
    job["inputs"]["source_paths"] = [str(source)]
    job["inputs"]["source_locator_digest"] = locator
    job["inputs"]["parent_job_id"] = None
    job["inputs"]["depends_on_job_ids"] = []
    job["params"] = params
    job["params_digest"] = params_digest
    job["state"] = {
        "status": "pending",
        "attempts": 1,
        "worker_id": "real-photo-smoke",
        "started_at": None,
        "heartbeat_at": None,
        "finished_at": None,
        "progress": None,
    }
    job["checkpoint"] = {
        "resumable": True,
        "cursor": None,
        "checkpoint_version": 1,
        "updated_at": _now(),
        "completed_input_ids": [],
        "partial_output_ids": [],
    }
    job["outputs"] = []
    job["error"] = None
    job["created_at"] = _now()
    job["deadline"] = None
    return job


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _run_ingest(
    *,
    source: Path,
    repo_root: Path,
    output_dir: Path,
    checkpoint_path: Path,
    initial_job_path: Path,
) -> dict[str, Any]:
    if not checkpoint_path.exists():
        _atomic_json(initial_job_path, build_scan_job(source, repo_root))
    job_input = checkpoint_path if checkpoint_path.exists() else initial_job_path
    command = [
        "cargo",
        "run",
        "--quiet",
        "--manifest-path",
        str(repo_root / "workers" / "ingest" / "Cargo.toml"),
        "--",
        str(job_input),
        str(output_dir),
        str(checkpoint_path),
    ]
    completed = subprocess.run(
        command,
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip().splitlines()
        raise RuntimeError(detail[-1] if detail else "ingest worker failed")
    lines = [line for line in completed.stdout.splitlines() if line.strip()]
    if not lines:
        raise RuntimeError("ingest worker returned no report")
    report = json.loads(lines[-1])
    if not isinstance(report, dict):
        raise TypeError("ingest worker returned an invalid report")
    return report


def _read_records(output_dir: Path) -> list[dict[str, Any]]:
    records = []
    for path in sorted((output_dir / "records").glob("*/*/*.json")):
        value = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(value, dict):
            records.append(value)
    return records


def _persist_media(
    repo_root: Path, database_path: Path, records: list[dict[str, Any]]
) -> int:
    Database = database_type(repo_root)
    with Database.open(database_path) as database:
        for record in records:
            database.put_media(record)
        return database.count_media()


def _model_report(catalog: ModelCatalog) -> list[dict[str, Any]]:
    return [
        {
            "model_id": inspection.model_id,
            # The graph contract is validated only when a provider creates the
            # real session.  Keep this label honest when registry metadata and
            # a published checkpoint disagree (issue #36).
            "registry_loadable": inspection.unloadable_reason is None,
            "reason": inspection.unloadable_reason,
            "runtimes": list(inspection.available_runtimes),
        }
        for inspection in catalog.inspect_all("face_detection")
    ]


def _choose_model(catalog: ModelCatalog, requested: str | None):
    if requested:
        inspection = catalog.inspect(requested)
        if inspection is None or inspection.task != "face_detection":
            return None
        return inspection if inspection.unloadable_reason is None else None
    inspections = {
        item.model_id: item for item in catalog.inspect_all("face_detection")
    }
    for model_id in MODEL_PREFERENCE:
        inspection = inspections.get(model_id)
        if inspection is not None and inspection.unloadable_reason is None:
            return inspection
    return next(
        (item for item in inspections.values() if item.unloadable_reason is None), None
    )


def _proxy_items(records: list[dict[str, Any]], limit: int) -> list[tuple[str, str]]:
    items = []
    for record in records:
        if record.get("kind") != "image":
            continue
        proxy = next(
            (
                value
                for value in record.get("proxies") or []
                if value.get("kind") in {"thumbnail_512", "preview_2048"}
            ),
            None,
        )
        if proxy is not None:
            items.append((str(record["media_id"]), str(proxy["proxy_id"])))
        if limit > 0 and len(items) >= limit:
            break
    return items


def _detection_json(detection: pb2.Detection) -> dict[str, Any]:
    return {
        "score": round(detection.score, 6),
        "box": {
            "x": round(detection.box.x, 6),
            "y": round(detection.box.y, 6),
            "w": round(detection.box.w, 6),
            "h": round(detection.box.h, 6),
        },
        "landmarks": [
            {"x": round(point.x, 6), "y": round(point.y, 6)}
            for point in detection.landmarks
        ],
        "landmarks_out_of_range": detection.landmarks_out_of_range,
    }


def _infer(
    *,
    catalog: ModelCatalog,
    repo_root: Path,
    database_path: Path,
    model_id: str,
    items: list[tuple[str, str]],
    runtime: str,
    timeout_seconds: float,
) -> dict[str, Any]:
    resolver = MediaDbProxyResolver(repo_root, database_path)
    running = start_server(MlRuntimeService(catalog, proxy_resolver=resolver), port=0)
    channel = grpc.insecure_channel(running.address)
    try:
        grpc.channel_ready_future(channel).result(timeout=5)
        stub = pb2_grpc.MlRuntimeStub(channel)
        request_bytes = b"\0".join(
            [
                model_id.encode("utf-8"),
                *(proxy_id.encode("ascii") for _, proxy_id in items),
            ]
        )
        preferred = [] if runtime == "auto" else [RUNTIME_ARGUMENTS[runtime]]
        response = stub.Infer(
            pb2.InferRequest(
                request_id=blake3(
                    b"real-photo-smoke-infer-v1\0" + request_bytes
                ).hexdigest(),
                model_id=model_id,
                preferred_runtimes=preferred,
                deadline_ms=int(timeout_seconds * 1000),
                items=[
                    pb2.InferItem(
                        item_id=media_id,
                        proxy_id=proxy_id,
                        alignment=pb2.ALIGNMENT_NONE,
                    )
                    for media_id, proxy_id in items
                ],
            ),
            timeout=timeout_seconds + 5,
        )
        if response.HasField("error"):
            return {
                "status": "error",
                "code": pb2.ErrorCode.Name(response.error.code),
                "message": response.error.message,
            }
        results = []
        for result in response.results:
            if result.WhichOneof("outcome") == "error":
                results.append(
                    {
                        "media_id": result.item_id,
                        "status": "error",
                        "code": pb2.ErrorCode.Name(result.error.code),
                        "message": result.error.message,
                    }
                )
            else:
                results.append(
                    {
                        "media_id": result.item_id,
                        "status": "ok",
                        "detections": [
                            _detection_json(value)
                            for value in result.detections.detections
                        ],
                    }
                )
        return {
            "status": "ok",
            "runtime": pb2.RuntimeTarget.Name(response.runtime_used),
            "duration_ms": response.duration_ms,
            "model_pin": {
                "model_id": response.pin.model_id,
                "version": response.pin.version,
                "weights_blake3": response.pin.weights_blake3,
                "config_blake3": response.pin.config_blake3,
            },
            "results": results,
        }
    finally:
        channel.close()
        running.stop()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder", type=Path, help="folder of real photos to scan")
    parser.add_argument("--repo-root", type=Path, default=DEFAULT_REPO_ROOT)
    parser.add_argument("--weights-dir", type=Path)
    parser.add_argument("--model", help="registered face detector id")
    parser.add_argument(
        "--runtime", choices=("auto", *RUNTIME_ARGUMENTS), default="auto"
    )
    parser.add_argument("--limit", type=int, default=32, help="0 runs every image")
    parser.add_argument("--timeout-seconds", type=float, default=120.0)
    parser.add_argument("--work-dir", type=Path)
    return parser.parse_args(argv)


def run(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    source = args.folder.resolve(strict=True)
    if not source.is_dir():
        raise ValueError("folder must be a directory")
    repo_root = args.repo_root.resolve(strict=True)
    locator = _source_locator_digest(source)
    work_dir = (
        args.work_dir.resolve()
        if args.work_dir is not None
        else Path(tempfile.gettempdir())
        / "memory-engine-real-photo-smoke"
        / locator[:16]
    )
    output_dir = work_dir / "library"
    jobs_dir = work_dir / "jobs"
    checkpoint_path = jobs_dir / f"scan-{locator}.json"
    initial_job_path = jobs_dir / f"scan-{locator}.initial.json"
    database_path = work_dir / "library.db"
    work_dir.mkdir(parents=True, exist_ok=True)

    report: dict[str, Any] = {
        "status": "running",
        "work_dir": str(work_dir),
        "development_mode": True,
        "stages": {},
        "breakages": [],
    }
    ingest_report = _run_ingest(
        source=source,
        repo_root=repo_root,
        output_dir=output_dir,
        checkpoint_path=checkpoint_path,
        initial_job_path=initial_job_path,
    )
    report["stages"]["ingest"] = ingest_report
    records = _read_records(output_dir)
    database_count = _persist_media(repo_root, database_path, records)
    report["stages"]["media_db_before_inference"] = {
        "records_written": len(records),
        "database_count": database_count,
    }

    development_environment = dict(os.environ)
    development_environment["MEMORY_ENGINE_ALLOW_UNVERIFIED_MODELS"] = "true"
    catalog = ModelCatalog(
        repo_root=repo_root,
        weights_dir=args.weights_dir,
        environ=development_environment,
    )
    report["models"] = _model_report(catalog)
    inspection = _choose_model(catalog, args.model)
    if inspection is None:
        report["status"] = "blocked"
        report["breakages"].append(
            {
                "stage": "ml_runtime",
                "message": "no registered face detector is loadable; inspect models[]",
            }
        )
        return 2, report
    items = _proxy_items(records, args.limit)
    if not items:
        report["status"] = "blocked"
        report["breakages"].append(
            {
                "stage": "proxies",
                "message": "ingest produced no analyzable image proxies",
            }
        )
        return 2, report

    inference = _infer(
        catalog=catalog,
        repo_root=repo_root,
        database_path=database_path,
        model_id=inspection.model_id,
        items=items,
        runtime=args.runtime,
        timeout_seconds=args.timeout_seconds,
    )
    report["stages"]["ml_runtime"] = inference
    Database = database_type(repo_root)
    with Database.open(database_path, migrate=False) as database:
        stored_faces = sum(
            len(database.faces_for_media(media_id)) for media_id, _ in items
        )
        after_count = database.count_media()
    report["stages"]["media_db_after_inference"] = {
        "database_count": after_count,
        "face_records_written": stored_faces,
    }
    if inference.get("status") != "ok":
        report["status"] = "failed"
        report["breakages"].append(
            {
                "stage": "ml_runtime",
                "code": inference.get("code"),
                "message": inference.get("message", "inference returned a typed error"),
            }
        )
        return 1, report

    report["breakages"].append(
        {
            "stage": "media_db_after_inference",
            "issue": 34,
            "message": (
                "detections are not persisted as FaceRecords because the contract has no "
                "canonical face_id encoding"
            ),
        }
    )
    report["status"] = "partial_success"
    return 0, report


def main(argv: list[str] | None = None) -> int:
    try:
        code, report = run(parse_args(argv))
    except (OSError, TypeError, ValueError, RuntimeError, grpc.RpcError) as error:
        code = 1
        report = {"status": "failed", "error": str(error)}
    print(json.dumps(report, indent=2, sort_keys=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
