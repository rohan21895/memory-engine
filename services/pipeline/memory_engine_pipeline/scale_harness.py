"""A resumable, synthetic scale probe for the Phase 1 library gate.

This is deliberately an executable measurement, not a claim that 100,000
items ought to fit.  Each requested size gets a clean source tree and a clean
pipeline work directory, then goes through the real public surfaces:

    run_pipeline(..., stages=["ingest"])
    Database.count_media() / Database.search()
    run_pipeline(..., stages=["ranking"])

The synthetic files contain no user media.  Their bytes are deterministic
from ``seed`` and ``index`` and deliberately mix BMP, PNG and small ISO-BMFF
video containers.  Still images arrive in visually identical groups whose
container metadata differs, giving ingest distinct content ids and dedupe a
real cluster to find.

The report is pessimistic by construction.  It is atomically written with
``status=incomplete`` and ``exit_code=1`` before work begins.  A SIGKILL can
therefore leave an incomplete report, never yesterday's green result.  The
generator and ingest worker both checkpoint; re-running the same command
continues the interrupted step without overwriting generated media.
"""

from __future__ import annotations

import json
import os
import platform
import struct
import subprocess
import sys
import threading
import time
import zlib
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:  # Windows has no resource module; ps sampling remains the primary path.
    import resource as _resource
except ImportError:  # pragma: no cover - exercised by Windows CI
    _resource = None

from .runner import run_pipeline
from .stages.base import Settings, StageStatus, write_json_atomically

REPORT_VERSION = 1
GENERATOR_VERSION = 1
ROOT_SENTINEL = ".memory-engine-scale-root.json"
SOURCE_SENTINEL = ".memory-engine-scale-source.json"
GENERATION_STATE = "generation.json"
REPORT_NAME = "scale-report.json"
DEFAULT_STEPS = (1_000, 5_000, 10_000, 25_000, 50_000, 100_000)
_REPO_ROOT = Path(__file__).resolve().parents[3]


class ScaleHarnessError(RuntimeError):
    """A result that must make the harness fail rather than guess."""


@dataclass(frozen=True, slots=True)
class ScaleConfig:
    root: Path
    steps: tuple[int, ...] = DEFAULT_STEPS
    seed: int = 20260818
    duplicate_group_size: int = 4
    generation_budget: int | None = None
    rss_poll_seconds: float = 0.10

    def validate(self) -> None:
        if not self.steps:
            raise ScaleHarnessError("at least one scale step is required")
        if any(step < 1 for step in self.steps):
            raise ScaleHarnessError("scale steps must be positive integers")
        if tuple(sorted(set(self.steps))) != self.steps:
            raise ScaleHarnessError("scale steps must be unique and increasing")
        if self.duplicate_group_size < 2:
            raise ScaleHarnessError("duplicate_group_size must be at least 2")
        if self.generation_budget is not None and self.generation_budget < 1:
            raise ScaleHarnessError("generation_budget must be positive")
        if self.rss_poll_seconds <= 0:
            raise ScaleHarnessError("rss_poll_seconds must be positive")

    def identity(self) -> dict[str, Any]:
        # generation_budget and poll interval are operational controls, not
        # artifact identity.  A budgeted run must be resumable without the
        # budget, and changing sampling frequency does not change the corpus.
        return {
            "generator_version": GENERATOR_VERSION,
            "steps": list(self.steps),
            "seed": self.seed,
            "duplicate_group_size": self.duplicate_group_size,
        }


@dataclass(frozen=True, slots=True)
class GenerationResult:
    complete: bool
    target_items: int
    created_this_attempt: int
    reused_uncheckpointed: int
    files_present: int
    format_counts: dict[str, int]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_steps(value: str) -> tuple[int, ...]:
    try:
        steps = tuple(int(part.strip()) for part in value.split(",") if part.strip())
    except ValueError as error:
        raise ScaleHarnessError("--steps must be comma-separated integers") from error
    if not steps:
        raise ScaleHarnessError("--steps did not contain an integer")
    return steps


def _chunk(kind: bytes, payload: bytes) -> bytes:
    body = kind + payload
    return struct.pack(">I", len(payload)) + body + struct.pack(">I", zlib.crc32(body))


def _pixels(seed: int, scene: int, *, width: int = 16, height: int = 16) -> bytes:
    """Small, high-frequency RGB image with a stable low-collision pHash.

    The arithmetic is specified rather than delegated to a PRNG whose stream
    can change between runtimes.  Every member of one scene gets these exact
    pixels; only ignored container metadata changes between members.
    """
    state = (seed ^ (scene * 0x9E3779B1) ^ 0xA5A5A5A5) & 0xFFFFFFFF
    out = bytearray()
    for y in range(height):
        for x in range(width):
            state ^= (state << 13) & 0xFFFFFFFF
            state ^= state >> 17
            state ^= (state << 5) & 0xFFFFFFFF
            state &= 0xFFFFFFFF
            # A spatial term prevents a rare zero PRNG state from becoming a
            # flat image and gives DCT pHash structure at several frequencies.
            out.extend(
                (
                    (state + 17 * x + 29 * y) & 0xFF,
                    ((state >> 8) + 31 * x + 11 * y) & 0xFF,
                    ((state >> 16) + 7 * x + 37 * y) & 0xFF,
                )
            )
    return bytes(out)


def _bmp_bytes(index: int, pixels: bytes, *, width: int = 16, height: int = 16) -> bytes:
    row_bytes = width * 3
    padding = (4 - row_bytes % 4) % 4
    rows = []
    for y in reversed(range(height)):
        row = bytearray()
        start = y * row_bytes
        for x in range(width):
            red, green, blue = pixels[start + 3 * x : start + 3 * x + 3]
            row.extend((blue, green, red))
        row.extend(b"\0" * padding)
        rows.append(bytes(row))
    body = b"".join(rows)
    # The two reserved fields are ignored by decoders and make visually equal
    # burst members byte-distinct, hence distinct content-addressed media ids.
    header = struct.pack(
        "<2sIHHI",
        b"BM",
        54 + len(body),
        index & 0xFFFF,
        (index >> 16) & 0xFFFF,
        54,
    )
    dib = struct.pack("<IiiHHIIiiII", 40, width, height, 1, 24, 0, len(body), 2835, 2835, 0, 0)
    return header + dib + body


def _png_bytes(index: int, pixels: bytes, *, width: int = 16, height: int = 16) -> bytes:
    rows = b"".join(
        b"\0" + pixels[y * width * 3 : (y + 1) * width * 3]
        for y in range(height)
    )
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    marker = f"memory-engine-index\0{index}".encode("ascii")
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", header)
        + _chunk(b"tEXt", marker)
        + _chunk(b"IDAT", zlib.compress(rows, level=9))
        + _chunk(b"IEND", b"")
    )


def _mp4_bytes(index: int, seed: int) -> bytes:
    # A syntactically valid ISO-BMFF file consisting of ftyp and free boxes.
    # It is intentionally not a playable clip: scan ingest only fingerprints
    # video, and the scale probe must not spend 100k encodes manufacturing a
    # source library.  The report calls this limitation out explicitly.
    ftyp_payload = b"isom" + struct.pack(">I", 0x200) + b"isomiso2"
    marker = struct.pack(">QQ", seed & 0xFFFFFFFFFFFFFFFF, index)
    return (
        struct.pack(">I4s", 8 + len(ftyp_payload), b"ftyp")
        + ftyp_payload
        + struct.pack(">I4s", 8 + len(marker), b"free")
        + marker
    )


def synthetic_item(index: int, *, seed: int, duplicate_group_size: int) -> tuple[str, bytes]:
    """Return the deterministic extension and bytes for one corpus item."""
    if index % 20 == 0:
        return "mp4", _mp4_bytes(index, seed)
    scene = index // duplicate_group_size
    pixels = _pixels(seed, scene)
    if index % 2:
        return "png", _png_bytes(index, pixels)
    return "bmp", _bmp_bytes(index, pixels)


def synthetic_relative_path(index: int, extension: str) -> Path:
    return Path(f"shard-{index // 1_000:05d}") / f"scale{index:09d}.{extension}"


def _atomic_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.tmp"
    with temporary.open("wb") as handle:
        handle.write(payload)
        handle.flush()
        # Closing before replace is enough for process-kill resumption.  These
        # bytes are synthetic and reproducible, so paying for one fsync per
        # item would make the generator, rather than ingest, dominate the
        # 100k measurement.  On resume the checkpointed prefix is verified
        # byte-for-byte below, which also catches a power-loss short write.
    os.replace(temporary, path)


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _claim_directory(path: Path, sentinel_name: str, identity: dict[str, Any]) -> None:
    """Create or verify a generated-only directory without deleting anything."""
    path.mkdir(parents=True, exist_ok=True)
    sentinel = path / sentinel_name
    existing = _read_json(sentinel)
    visible = [entry for entry in path.iterdir() if entry.name != sentinel_name]
    if existing is None:
        if visible:
            raise ScaleHarnessError(
                f"refusing non-empty unowned directory {path}; choose an empty scratch path"
            )
        write_json_atomically(sentinel, identity)
        return
    if existing != identity:
        raise ScaleHarnessError(
            f"generated directory identity disagrees at {sentinel}; use a different root"
        )


def generate_library(
    source: Path,
    state_path: Path,
    *,
    target_items: int,
    seed: int,
    duplicate_group_size: int,
    item_budget: int | None = None,
    checkpoint_every: int = 100,
) -> GenerationResult:
    """Generate or resume one deterministic corpus.

    A file is written before the cursor advances.  If the process dies between
    those operations, the next attempt recomputes the expected bytes and reuses
    the exact file.  A different byte at an expected path is never overwritten.
    """
    identity = {
        "generator_version": GENERATOR_VERSION,
        "target_items": target_items,
        "seed": seed,
        "duplicate_group_size": duplicate_group_size,
    }
    _claim_directory(source, SOURCE_SENTINEL, identity)
    state = _read_json(state_path)
    if state is None:
        state = {**identity, "next_index": 0}
        write_json_atomically(state_path, state)
    elif any(state.get(key) != value for key, value in identity.items()):
        raise ScaleHarnessError(f"generation checkpoint identity disagrees at {state_path}")

    next_index = int(state.get("next_index", -1))
    if not 0 <= next_index <= target_items:
        raise ScaleHarnessError(f"invalid generation cursor {next_index} at {state_path}")

    # A cursor is not evidence that its files survived.  Verifying the prefix
    # makes a kill/power-loss recovery fail closed instead of handing ingest a
    # quietly smaller corpus.  Fresh runs have next_index == 0 and pay nothing.
    for completed_index in range(next_index):
        extension, expected = synthetic_item(
            completed_index, seed=seed, duplicate_group_size=duplicate_group_size
        )
        completed_path = source / synthetic_relative_path(completed_index, extension)
        try:
            matches = completed_path.read_bytes() == expected
        except OSError as error:
            raise ScaleHarnessError(
                f"cannot verify checkpointed generated file {completed_path}: {error}"
            ) from error
        if not matches:
            raise ScaleHarnessError(
                f"checkpointed generated file is missing or changed: {completed_path}"
            )

    created = 0
    reused = 0
    index = next_index
    while index < target_items:
        if item_budget is not None and created >= item_budget:
            break
        extension, payload = synthetic_item(
            index, seed=seed, duplicate_group_size=duplicate_group_size
        )
        target = source / synthetic_relative_path(index, extension)
        if target.exists():
            try:
                same = target.read_bytes() == payload
            except OSError as error:
                raise ScaleHarnessError(f"cannot verify generated file {target}: {error}") from error
            if not same:
                raise ScaleHarnessError(
                    f"refusing to overwrite unexpected bytes at generated path {target}"
                )
            reused += 1
        else:
            _atomic_bytes(target, payload)
            created += 1
        index += 1
        if index % checkpoint_every == 0:
            state["next_index"] = index
            write_json_atomically(state_path, state)

    state["next_index"] = index
    write_json_atomically(state_path, state)
    return GenerationResult(
        complete=index == target_items,
        target_items=target_items,
        created_this_attempt=created,
        reused_uncheckpointed=reused,
        files_present=index,
        format_counts=_format_counts(index),
    )


def _format_counts(items: int) -> dict[str, int]:
    counts = {"bmp": 0, "png": 0, "mp4": 0}
    for index in range(items):
        if index % 20 == 0:
            counts["mp4"] += 1
        elif index % 2:
            counts["png"] += 1
        else:
            counts["bmp"] += 1
    return counts


def verify_generated_library(
    source: Path,
    state_path: Path,
    *,
    target_items: int,
    seed: int,
    duplicate_group_size: int,
) -> None:
    """Read-only proof that a completed generator artifact is still complete."""
    identity = {
        "generator_version": GENERATOR_VERSION,
        "target_items": target_items,
        "seed": seed,
        "duplicate_group_size": duplicate_group_size,
    }
    if _read_json(source / SOURCE_SENTINEL) != identity:
        raise ScaleHarnessError(f"generated source sentinel is absent or changed at {source}")
    state = _read_json(state_path)
    if state is None or any(state.get(key) != value for key, value in identity.items()):
        raise ScaleHarnessError(f"generation checkpoint is absent or changed at {state_path}")
    if state.get("next_index") != target_items:
        raise ScaleHarnessError(
            f"generation checkpoint is incomplete at {state.get('next_index')}/{target_items}"
        )
    for index in range(target_items):
        extension, expected = synthetic_item(
            index, seed=seed, duplicate_group_size=duplicate_group_size
        )
        path = source / synthetic_relative_path(index, extension)
        try:
            matches = path.read_bytes() == expected
        except OSError as error:
            raise ScaleHarnessError(f"generated artifact is absent at {path}: {error}") from error
        if not matches:
            raise ScaleHarnessError(f"generated artifact is corrupt at {path}")


class PeakRssSampler:
    """Poll the current process tree's resident set without a new dependency."""

    def __init__(self, poll_seconds: float = 0.10) -> None:
        self.poll_seconds = poll_seconds
        self.peak_bytes = 0
        self.samples = 0
        self.method = "ps_process_tree_rss"
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def __enter__(self) -> "PeakRssSampler":
        self._sample()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=max(1.0, self.poll_seconds * 4))
        self._sample()
        if self.samples == 0:
            self.method = "resource_high_water_fallback"
            self.peak_bytes = _resource_peak_bytes()

    def _loop(self) -> None:
        while not self._stop.wait(self.poll_seconds):
            self._sample()

    def _sample(self) -> None:
        try:
            result = subprocess.run(
                ["ps", "-axo", "pid=,ppid=,rss="],
                check=True,
                capture_output=True,
                text=True,
                timeout=max(1.0, self.poll_seconds * 5),
            )
            rows: dict[int, tuple[int, int]] = {}
            for line in result.stdout.splitlines():
                fields = line.split()
                if len(fields) == 3:
                    pid, parent, rss_kib = map(int, fields)
                    rows[pid] = (parent, rss_kib)
            descendants = {os.getpid()}
            changed = True
            while changed:
                changed = False
                for pid, (parent, _rss) in rows.items():
                    if parent in descendants and pid not in descendants:
                        descendants.add(pid)
                        changed = True
            total = sum(rows[pid][1] for pid in descendants if pid in rows) * 1024
        except (OSError, subprocess.SubprocessError, ValueError):
            return
        self.samples += 1
        self.peak_bytes = max(self.peak_bytes, total)


def _resource_peak_bytes() -> int:
    if _resource is None:
        return 0
    values = [
        _resource.getrusage(_resource.RUSAGE_SELF).ru_maxrss,
        _resource.getrusage(_resource.RUSAGE_CHILDREN).ru_maxrss,
    ]
    multiplier = 1 if sys.platform == "darwin" else 1024
    return int(max(values) * multiplier)


def _machine() -> dict[str, Any]:
    return {
        "platform": platform.platform(),
        "machine": platform.machine(),
        "python": platform.python_version(),
        "logical_cpus": os.cpu_count(),
    }


def _event_seconds(left: dict[str, Any], right: dict[str, Any]) -> float:
    def parsed(event: dict[str, Any]) -> datetime:
        return datetime.fromisoformat(str(event["at"]).replace("Z", "+00:00"))

    return max(0.0, (parsed(right) - parsed(left)).total_seconds())


def _read_events(path: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return events
    for line in lines:
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict) and event.get("at"):
            events.append(event)
    return events


def _stage_breakdown(events_path: Path, stage: str) -> dict[str, Any]:
    """Derive phase boundaries from the pipeline's durable event stream."""
    events = _read_events(events_path)
    starts = [
        event for event in events
        if event.get("stage") == stage and event.get("kind") == "stage_start"
    ]
    dones = [
        event for event in events
        if event.get("stage") == stage and event.get("kind") == "stage_done"
    ]
    if not starts:
        return {"complete": False, "detail": f"no {stage} start event"}
    start = starts[-1]
    done = next(
        (event for event in dones if str(event["at"]) >= str(start["at"])),
        None,
    )

    if stage == "ingest":
        scan_ends = [
            event for event in events
            if event.get("stage") == stage
            and event.get("kind") == "progress"
            and event.get("message") in ("scanning local source", "source scan complete")
            and event.get("units_total") is not None
            and event.get("units_done") == event.get("units_total")
            and str(event["at"]) >= str(start["at"])
            and (done is None or str(event["at"]) <= str(done["at"]))
        ]
        stored = [
            event for event in events
            if event.get("stage") == stage
            and event.get("kind") == "progress"
            and event.get("message") == "stored"
            and str(event["at"]) >= str(start["at"])
            and (done is None or str(event["at"]) <= str(done["at"]))
        ]
        storing = [
            event for event in events
            if event.get("stage") == stage
            and event.get("kind") == "progress"
            and event.get("message") == "storing records"
            and str(event["at"]) >= str(start["at"])
            and (done is None or str(event["at"]) <= str(done["at"]))
        ]
        if not stored or (not scan_ends and not storing):
            return {"complete": False, "detail": "ingest event boundaries are absent"}
        scan_end = scan_ends[-1] if scan_ends else storing[0]
        boundary_note = None if scan_ends else (
            "the worker finished between progress polls; scan includes storage of the first "
            "reported database chunk and storage excludes it"
        )
        stored_end = stored[-1]
        if done is None:
            observed = {"at": utc_now()}
            result = {
                "complete": False,
                "detail": "job finalization had not emitted stage_done when observed",
                "scan_worker_seconds": round(_event_seconds(start, scan_end), 6),
                "media_db_storage_seconds": round(_event_seconds(scan_end, stored_end), 6),
                "job_finalization_minimum_seconds": round(
                    _event_seconds(stored_end, observed), 6
                ),
                "job_finalization_started_at": stored_end["at"],
                "observed_at": observed["at"],
            }
            if boundary_note is not None:
                result["boundary_note"] = boundary_note
            return result
        result = {
            "complete": True,
            "scan_worker_seconds": round(_event_seconds(start, scan_end), 6),
            "media_db_storage_seconds": round(_event_seconds(scan_end, stored_end), 6),
            # jobs.complete validates and serializes the output-bearing JobSpec
            # between the final "stored" progress and stage_done.
            "job_finalization_seconds": round(_event_seconds(stored_end, done), 6),
            "event_interval_seconds": round(_event_seconds(start, done), 6),
        }
        if boundary_note is not None:
            result["boundary_note"] = boundary_note
        return result

    if done is None:
        return {"complete": False, "detail": f"no {stage} completion after start"}
    run_starts = [
        event for event in events
        if event.get("stage") == "run"
        and event.get("kind") == "run_start"
        and str(event["at"]) <= str(start["at"])
    ]
    preparation = _event_seconds(run_starts[-1], start) if run_starts else None
    return {
        "complete": True,
        "materialize_and_prepare_seconds": round(preparation, 6)
        if preparation is not None else None,
        "engine_and_persist_seconds": round(_event_seconds(start, done), 6),
        "event_interval_seconds": round(
            (_event_seconds(run_starts[-1], done) if run_starts else _event_seconds(start, done)),
            6,
        ),
    }


def _new_report(config: ScaleConfig) -> dict[str, Any]:
    return {
        "schema_version": REPORT_VERSION,
        "configuration": config.identity(),
        "status": "incomplete",
        "ok": False,
        "exit_code": 1,
        "started_at": utc_now(),
        "finished_at": None,
        "machine": _machine(),
        "steps": [],
        "limitations": [
            "synthetic images do not measure real-camera decode complexity",
            "synthetic MP4 items contain valid ISO-BMFF boxes but no playable track; "
            "this run measures scan ingest, not hardware proxy generation",
            "peak RSS is sampled and may miss a shorter-lived peak than the poll interval",
        ],
    }


def _load_or_create_report(config: ScaleConfig, path: Path) -> dict[str, Any]:
    existing = _read_json(path)
    if existing is None:
        return _new_report(config)
    if existing.get("schema_version") != REPORT_VERSION:
        raise ScaleHarnessError(f"unsupported report version at {path}")
    if existing.get("configuration") != config.identity():
        raise ScaleHarnessError(
            f"scale report configuration disagrees at {path}; use a different root"
        )
    return existing


def _persist(path: Path, report: dict[str, Any]) -> None:
    write_json_atomically(path, report)


def _step_record(report: dict[str, Any], target: int) -> dict[str, Any]:
    for step in report["steps"]:
        if step.get("target_items") == target:
            return step
    step = {
        "target_items": target,
        "status": "incomplete",
        "started_at": utc_now(),
        "finished_at": None,
        "phases": {},
    }
    report["steps"].append(step)
    return step


def _phase(
    function: Callable[[], Any], *, poll_seconds: float
) -> tuple[Any, dict[str, Any]]:
    started = time.monotonic()
    with PeakRssSampler(poll_seconds) as memory:
        value = function()
    elapsed = time.monotonic() - started
    return value, {
        "elapsed_seconds": round(elapsed, 6),
        "peak_rss_bytes": memory.peak_bytes,
        "rss_samples": memory.samples,
        "rss_method": memory.method,
    }


def _pipeline_stage(report: Any, expected: str) -> Any:
    for result in report.results:
        if result.stage == expected:
            return result
    raise ScaleHarnessError(f"pipeline returned no {expected} result")


def _pipeline_phase(result: Any, metrics: dict[str, Any], items: int) -> dict[str, Any]:
    elapsed = metrics["elapsed_seconds"]
    fresh = result.status is StageStatus.COMPLETED
    return {
        **metrics,
        "status": result.status.value,
        "detail": result.detail,
        "job_id": result.job_id,
        "counts": dict(result.counts),
        "measurement_fresh": fresh,
        "items_per_second": round(items / elapsed, 3) if fresh and elapsed > 0 else None,
        "measurement_note": None if fresh else (
            "completed artifact was reused; this attempt is not a throughput measurement"
        ),
    }


def _terminal_from_stage(status: StageStatus) -> tuple[str, int]:
    if status is StageStatus.FAILED:
        return "failed", 2
    if not status.is_success:
        return "incomplete", 1
    return "completed", 0


def _verify_completed_step(
    config: ScaleConfig,
    step: dict[str, Any],
    *,
    root: Path,
    database_type: Any,
) -> None:
    """Verify yesterday's artifacts before yesterday's report can be green."""
    step.pop("replay_verification", None)
    target = int(step.get("target_items", 0))
    if target not in config.steps or step.get("status") != "completed":
        raise ScaleHarnessError(f"scale step {target} has no completed artifact claim")
    phases = step.get("phases") or {}
    if (phases.get("generation") or {}).get("status") != "completed":
        raise ScaleHarnessError(f"completed step {target} lacks completed generation evidence")
    for name in ("ingest", "dedupe"):
        if (phases.get(name) or {}).get("status") not in ("completed", "skipped"):
            raise ScaleHarnessError(f"completed step {target} lacks successful {name} evidence")
    if (phases.get("search") or {}).get("status") != "completed":
        raise ScaleHarnessError(f"completed step {target} lacks completed search evidence")

    step_root = root / "steps" / str(target)
    source = step_root / "source"
    pipeline_work = step_root / "pipeline"
    verify_generated_library(
        source,
        step_root / GENERATION_STATE,
        target_items=target,
        seed=config.seed,
        duplicate_group_size=config.duplicate_group_size,
    )

    database_path = pipeline_work / "library.db"
    if not database_path.is_file():
        raise ScaleHarnessError(f"completed step {target} is missing {database_path}")
    probe_index = 1 if target > 1 else 0
    probe_term = f"scale{probe_index:09d}"
    with database_type.open(database_path) as database:
        count = database.count_media()
        matches = database.search(probe_term, limit=2, include_excluded=True)
        dedupe_row = database.connection.execute(
            "SELECT count(DISTINCT dedupe_group_id), count(*) FROM media "
            "WHERE dedupe_group_id IS NOT NULL"
        ).fetchone()
    if count != target:
        raise ScaleHarnessError(
            f"completed step {target} now has {count} media-db rows, expected {target}"
        )
    if not matches:
        raise ScaleHarnessError(
            f"completed step {target} no longer finds generated filename {probe_term}"
        )
    expected_counts = (phases["dedupe"].get("counts") or {})
    actual_groups, actual_members = map(int, dedupe_row)
    expected_groups = expected_counts.get("duplicate_groups")
    expected_members = expected_counts.get("duplicates")
    if expected_groups is None or expected_members is None:
        raise ScaleHarnessError(f"completed step {target} lacks persisted dedupe counts")
    if (actual_groups, actual_members) != (int(expected_groups), int(expected_members)):
        raise ScaleHarnessError(
            f"completed step {target} dedupe evidence changed: "
            f"database={(actual_groups, actual_members)}, "
            f"report={(expected_groups, expected_members)}"
        )
    step["replay_verification"] = {
        "status": "completed",
        "verified_at": utc_now(),
        "generated_items": target,
        "media_db_count": count,
        "search_matches": len(matches),
        "duplicate_groups": actual_groups,
        "duplicate_members": actual_members,
    }


def _finish(report: dict[str, Any], path: Path, status: str, exit_code: int) -> int:
    report["status"] = status
    report["ok"] = status == "completed"
    report["exit_code"] = exit_code
    report["finished_at"] = utc_now()
    _persist(path, report)
    return exit_code


def run_scale_harness(
    config: ScaleConfig,
    *,
    repo_root: Path | None = None,
    pipeline_runner: Callable[..., Any] = run_pipeline,
) -> int:
    """Run all configured sizes, returning the machine-facing exit code."""
    config.validate()
    repo_root = (repo_root or _REPO_ROOT).resolve()
    root = config.root.expanduser().resolve()
    _claim_directory(root, ROOT_SENTINEL, config.identity())
    report_path = root / REPORT_NAME
    report = _load_or_create_report(config, report_path)

    # It is a new attempt now.  Clear the terminal timestamp and make the
    # durable artifact non-green before calling anything expensive.
    report.update(status="incomplete", ok=False, exit_code=1, finished_at=None)
    report.pop("error", None)
    _persist(report_path, report)

    active_step: dict[str, Any] | None = None
    active_pipeline_work: Path | None = None
    try:
        from memory_engine_media_db import Database  # public package API

        for target in config.steps:
            step = _step_record(report, target)
            active_step = step
            if step.get("status") == "completed":
                _verify_completed_step(
                    config, step, root=root, database_type=Database
                )
                _persist(report_path, report)
                continue
            step_root = root / "steps" / str(target)
            source = step_root / "source"
            pipeline_work = step_root / "pipeline"
            active_pipeline_work = pipeline_work
            generation_state = step_root / GENERATION_STATE
            step["status"] = "incomplete"
            step["finished_at"] = None
            _persist(report_path, report)

            generated, generation_metrics = _phase(
                lambda: generate_library(
                    source,
                    generation_state,
                    target_items=target,
                    seed=config.seed,
                    duplicate_group_size=config.duplicate_group_size,
                    item_budget=config.generation_budget,
                ),
                poll_seconds=config.rss_poll_seconds,
            )
            generation_phase = {
                **generation_metrics,
                "status": "completed" if generated.complete else "incomplete",
                "created_this_attempt": generated.created_this_attempt,
                "reused_uncheckpointed": generated.reused_uncheckpointed,
                "files_present": generated.files_present,
                "format_counts": generated.format_counts,
                "measurement_fresh": (
                    generated.complete
                    and generated.created_this_attempt == target
                    and generated.reused_uncheckpointed == 0
                ),
                "items_per_second": round(
                    generated.created_this_attempt / generation_metrics["elapsed_seconds"], 3
                ) if generation_metrics["elapsed_seconds"] > 0 else None,
            }
            step["phases"]["generation"] = generation_phase
            _persist(report_path, report)
            if not generated.complete:
                step["detail"] = (
                    f"controlled generation budget stopped at {generated.files_present}/{target}; "
                    "re-run the same root to continue"
                )
                return _finish(report, report_path, "incomplete", 1)

            ingest_report, ingest_metrics = _phase(
                lambda: pipeline_runner(
                    [source],
                    pipeline_work,
                    settings=Settings(render_print=False, render_video=False),
                    stages=["ingest"],
                    repo_root=repo_root,
                ),
                poll_seconds=config.rss_poll_seconds,
            )
            ingest = _pipeline_stage(ingest_report, "ingest")
            step["phases"]["ingest"] = _pipeline_phase(ingest, ingest_metrics, target)
            step["phases"]["ingest"]["breakdown"] = _stage_breakdown(
                pipeline_work / "events.jsonl", "ingest"
            )
            _persist(report_path, report)
            ingest_status, ingest_exit = _terminal_from_stage(ingest.status)
            if ingest_status != "completed":
                step["status"] = ingest_status
                step["detail"] = f"ingest {ingest.status.value}: {ingest.detail}"
                return _finish(report, report_path, ingest_status, ingest_exit)

            with Database.open(pipeline_work / "library.db") as database:
                media_count = database.count_media()
            step["media_db_count"] = media_count
            _persist(report_path, report)
            if media_count != target:
                raise ScaleHarnessError(
                    f"media-db contains {media_count} records after {target} unique inputs"
                )

            ranking_report, ranking_metrics = _phase(
                lambda: pipeline_runner(
                    [source],
                    pipeline_work,
                    settings=Settings(render_print=False, render_video=False),
                    stages=["ranking"],
                    repo_root=repo_root,
                ),
                poll_seconds=config.rss_poll_seconds,
            )
            ranking = _pipeline_stage(ranking_report, "ranking")
            step["phases"]["dedupe"] = _pipeline_phase(ranking, ranking_metrics, target)
            step["phases"]["dedupe"]["breakdown"] = _stage_breakdown(
                pipeline_work / "events.jsonl", "ranking"
            )
            _persist(report_path, report)
            ranking_status, ranking_exit = _terminal_from_stage(ranking.status)
            if ranking_status != "completed":
                step["status"] = ranking_status
                step["detail"] = f"ranking {ranking.status.value}: {ranking.detail}"
                return _finish(report, report_path, ranking_status, ranking_exit)

            probe_index = 1 if target > 1 else 0  # index 0 is a video; either is searchable
            probe_term = f"scale{probe_index:09d}"
            search_started = time.monotonic()
            with Database.open(pipeline_work / "library.db") as database:
                matches = database.search(probe_term, limit=2, include_excluded=True)
                dedupe_row = database.connection.execute(
                    "SELECT count(DISTINCT dedupe_group_id), count(*) FROM media "
                    "WHERE dedupe_group_id IS NOT NULL"
                ).fetchone()
            search_elapsed = time.monotonic() - search_started
            dedupe_counts = step["phases"]["dedupe"]["counts"]
            persisted_dedupe = {
                "duplicate_groups": int(dedupe_row[0]),
                "duplicates": int(dedupe_row[1]),
            }
            for name, persisted in persisted_dedupe.items():
                reported = dedupe_counts.get(name)
                if reported is not None and int(reported) != persisted:
                    raise ScaleHarnessError(
                        f"ranking reported {name}={reported}, but media-db persisted "
                        f"{name}={persisted}"
                    )
                # The database is the durable artifact.  A skipped/resumed
                # ranking stage may omit aggregate counts; record the measured
                # persisted value, never a default or stale stage claim.
                dedupe_counts[name] = persisted
            step["phases"]["search"] = {
                "status": "completed" if matches else "failed",
                "query": probe_term,
                "matches": len(matches),
                "elapsed_seconds": round(search_elapsed, 6),
            }
            if not matches:
                raise ScaleHarnessError(
                    f"media-db search returned no result for generated filename {probe_term}"
                )

            step["status"] = "completed"
            step["finished_at"] = utc_now()
            peak_rss = max(
                int(phase.get("peak_rss_bytes") or 0)
                for phase in step["phases"].values()
            )
            if peak_rss <= 0:
                raise ScaleHarnessError(
                    f"peak RSS could not be measured for step {target}; refusing a green report"
                )
            step["peak_rss_bytes"] = peak_rss
            measured_wall = sum(
                float(phase.get("elapsed_seconds") or 0)
                for phase in step["phases"].values()
            )
            step["measured_wall_seconds"] = round(measured_wall, 6)
            measurement_fresh = all(
                bool((step["phases"].get(name) or {}).get("measurement_fresh"))
                for name in ("generation", "ingest", "dedupe")
            )
            step["measurement_fresh"] = measurement_fresh
            step["end_to_end_items_per_second"] = (
                round(target / measured_wall, 3)
                if measurement_fresh and measured_wall > 0 else None
            )
            if not measurement_fresh:
                step["measurement_note"] = (
                    "the artifact completed through one or more resumed attempts; "
                    "end-to-end throughput is intentionally omitted"
                )
            step["detail"] = "generation, ingest, media-db, dedupe and search completed"
            _persist(report_path, report)

    except KeyboardInterrupt:
        if active_step is not None:
            active_step["status"] = "incomplete"
            active_step["detail"] = "interrupted by operator; re-run the same root to resume"
            if active_pipeline_work is not None:
                for phase_name, stage_name in (("ingest", "ingest"), ("dedupe", "ranking")):
                    breakdown = _stage_breakdown(
                        active_pipeline_work / "events.jsonl", stage_name
                    )
                    if breakdown.get("detail") != f"no {stage_name} start event":
                        active_step["phases"].setdefault(
                            phase_name,
                            {"status": "incomplete", "breakdown": breakdown},
                        )
        return _finish(report, report_path, "incomplete", 1)
    except ScaleHarnessError as error:
        report["error"] = {"type": type(error).__name__, "message": str(error)}
        if active_step is not None:
            active_step["status"] = "failed"
            active_step["detail"] = str(error)
        return _finish(report, report_path, "failed", 2)
    except Exception as error:  # noqa: BLE001 - the report must survive every failure
        report["error"] = {"type": type(error).__name__, "message": str(error)}
        if active_step is not None:
            active_step["status"] = "failed"
            active_step["detail"] = f"{type(error).__name__}: {error}"
        return _finish(report, report_path, "failed", 2)

    return _finish(report, report_path, "completed", 0)


def report_summary(report: dict[str, Any]) -> str:
    lines = [
        f"scale harness: {report.get('status')} (exit {report.get('exit_code')})",
    ]
    for step in report.get("steps", []):
        peak = int(step.get("peak_rss_bytes") or 0)
        peak_text = f"{peak / (1024 ** 2):.1f} MiB" if peak else "not measured"
        lines.append(
            f"  {int(step['target_items']):>7,} items  {step.get('status', 'incomplete'):<10} "
            f"peak RSS {peak_text}"
        )
        for name in ("generation", "ingest", "dedupe", "search"):
            phase = (step.get("phases") or {}).get(name)
            if phase:
                lines.append(
                    f"      {name:<10} {phase.get('status', 'incomplete'):<10} "
                    f"{float(phase.get('elapsed_seconds') or 0):.3f}s"
                )
    if report.get("error"):
        lines.append(f"  error: {report['error'].get('message')}")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="memory-engine-scale",
        description="Measure synthetic ingest -> media-db -> dedupe at increasing sizes.",
    )
    parser.add_argument("--root", type=Path, required=True, help="empty scratch directory")
    parser.add_argument(
        "--steps",
        default=",".join(str(step) for step in DEFAULT_STEPS),
        help="unique increasing item counts (default: %(default)s)",
    )
    defaults = ScaleConfig(root=Path("."))
    parser.add_argument("--seed", type=int, default=defaults.seed)
    parser.add_argument(
        "--duplicate-group-size", type=int, default=defaults.duplicate_group_size
    )
    parser.add_argument(
        "--generation-budget",
        type=int,
        default=None,
        help="create at most N new files, leave an incomplete report, and exit 1; "
        "used to exercise controlled resumption",
    )
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)
    try:
        steps = parse_steps(args.steps)
        config = ScaleConfig(
            root=args.root,
            steps=steps,
            seed=args.seed,
            duplicate_group_size=args.duplicate_group_size,
            generation_budget=args.generation_budget,
        )
        exit_code = run_scale_harness(config)
    except (ScaleHarnessError, OSError) as error:
        sys.stderr.write(f"scale harness could not start: {error}\n")
        return 2
    if not args.quiet:
        payload = _read_json(args.root.expanduser().resolve() / REPORT_NAME) or {}
        sys.stdout.write(report_summary(payload) + "\n")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
