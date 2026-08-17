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
from .photo_analysis import PhotoAnalysisRunner
from .service import MlRuntimeService, start_server

RUNTIME_ARGUMENTS = {
    "cpu": pb2.RUNTIME_TARGET_ONNXRUNTIME_CPU,
    "coreml": pb2.RUNTIME_TARGET_ONNXRUNTIME_COREML,
    "directml": pb2.RUNTIME_TARGET_ONNXRUNTIME_DIRECTML,
    "cuda": pb2.RUNTIME_TARGET_ONNXRUNTIME_CUDA,
}

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


def _model_report(catalog: ModelCatalog, steps: tuple[str, ...]) -> list[dict[str, Any]]:
    reports = []
    for step_id in steps:
        inspection = catalog.inspect(step_id)
        if inspection is None:
            reports.append(
                {
                    "step_id": step_id,
                    "kind": "non_model_step",
                    "registry_loadable": False,
                    "reason": "no executable registry entry",
                    "runtimes": [],
                }
            )
            continue
        reports.append(
            {
                "step_id": step_id,
                "model_id": inspection.model_id,
                "task": inspection.task,
                # A provider validates the real graph only when it creates a
                # session. Registry loadability is not a graph-load claim.
                "registry_loadable": inspection.unloadable_reason is None,
                "reason": inspection.unloadable_reason,
                "runtimes": list(inspection.available_runtimes),
            }
        )
    return reports


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


class _GrpcPipelineClient:
    def __init__(
        self,
        *,
        catalog: ModelCatalog,
        repo_root: Path,
        database_path: Path,
        runtime: str,
        timeout_seconds: float,
    ) -> None:
        resolver = MediaDbProxyResolver(repo_root, database_path)
        self.running = start_server(
            MlRuntimeService(catalog, proxy_resolver=resolver), port=0
        )
        self.channel = grpc.insecure_channel(self.running.address)
        grpc.channel_ready_future(self.channel).result(timeout=5)
        self.stub = pb2_grpc.MlRuntimeStub(self.channel)
        self.preferred = [] if runtime == "auto" else [RUNTIME_ARGUMENTS[runtime]]
        self.timeout_seconds = timeout_seconds

    def __call__(
        self, model_id: str, items: list[pb2.InferItem] | tuple[pb2.InferItem, ...]
    ) -> pb2.InferResponse:
        request_digest = blake3(b"real-photo-smoke-infer-v2\0")
        request_digest.update(model_id.encode("utf-8"))
        for item in items:
            request_digest.update(b"\0")
            request_digest.update(item.SerializeToString(deterministic=True))
        return self.stub.Infer(
            pb2.InferRequest(
                request_id=request_digest.hexdigest(),
                model_id=model_id,
                preferred_runtimes=self.preferred,
                deadline_ms=int(self.timeout_seconds * 1000),
                items=items,
            ),
            timeout=self.timeout_seconds + 5,
        )

    def close(self) -> None:
        self.channel.close()
        self.running.stop()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder", type=Path, help="folder of real photos to scan")
    parser.add_argument("--repo-root", type=Path, default=DEFAULT_REPO_ROOT)
    parser.add_argument("--weights-dir", type=Path)
    parser.add_argument("--pipeline", default="photo_analysis")
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
    pipeline = catalog.pipeline(args.pipeline)
    if pipeline is None:
        report["status"] = "blocked"
        report["breakages"].append(
            {
                "stage": "photo_analysis",
                "message": "requested pipeline is not registered",
            }
        )
        return 2, report
    report["models"] = _model_report(catalog, pipeline.steps)
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

    Database = database_type(repo_root)
    client = _GrpcPipelineClient(
        catalog=catalog,
        repo_root=repo_root,
        database_path=database_path,
        runtime=args.runtime,
        timeout_seconds=args.timeout_seconds,
    )
    try:
        analysis = PhotoAnalysisRunner(
            repo_root=repo_root,
            database_path=database_path,
            database_class=Database,
            catalog=catalog,
            pipeline=pipeline,
            records=records,
            proxy_items=items,
            infer=client,
        ).run()
    finally:
        client.close()
    report["stages"]["photo_analysis"] = analysis
    report["breakages"].extend(analysis["breakages"])
    report["status"] = analysis["status"]
    return (0 if analysis["status"] == "complete" else 2), report


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
