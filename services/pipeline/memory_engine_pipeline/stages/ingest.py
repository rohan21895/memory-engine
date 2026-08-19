"""folder -> workers/ingest -> media-db.

This is a generalisation of `scripts/demo/run_demo.py`, which already walks a
folder through the Rust worker and into the library. What it adds is the part a
demo does not need and a product cannot do without: knowing whether the work
has already been done.

THE INCREMENTAL PROBLEM, STATED HONESTLY

Content addressing makes every downstream stage idempotent for free, because
the id of the work is a function of the content. Ingest is the one stage where
that reasoning is circular: computing the content hash IS the expensive work.
Re-running the Rust worker over an unchanged 500GB folder would re-read 500GB
to discover that nothing changed.

So ingest is fronted by an inventory (see `inventory.py`): one `stat` per file,
no bytes read, compared against the inventory the last completed scan wrote.
Three outcomes:

    no previous inventory   -> full scan of the roots, resumable by the Rust
                               worker's own path cursor.
    inventory unchanged     -> the stage does nothing at all. The worker is not
                               even started.
    inventory changed       -> a DELTA scan whose source roots are the
                               individual added and changed files. The Rust
                               walker accepts a file as a root, so N new files
                               cost N files of work rather than a whole re-walk.

WHY THE INVENTORY IS WRITTEN LAST

Only after the scan job reaches `completed`. Writing it earlier and then dying
would leave a runner that believes the folder is fully imported when it is not
-- a library that is quietly missing files, which is the worst outcome
available here because nothing downstream can detect it.

WHY A DELTA SCAN IS A DIFFERENT JOB

Its `source_locator_digest` covers the delta paths, not the roots, so it has a
different `job_id`. That is not a workaround, it is the contract behaving as
designed: the digest exists precisely so that two scans over different paths
are two jobs.

KNOWN LIMITATION, RECORDED RATHER THAN HIDDEN
GoPro chapter assembly is reconciled by the Rust worker across the OUTPUTS OF
ONE JOB. A delta scan therefore sees only the newly added chapters, so a
chaptered video whose later chapters arrive in a second scan will not be
re-assembled with its earlier ones until a `--rescan`. This is an ingest-side
limitation the runner cannot fix from outside; it is called out in the README
and belongs in an issue against `workers/ingest`.
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from typing import Any

from ..ids import blake3_hex, source_locator_digest
from ..inventory import Inventory, diff, walk
from ..jobstore import build_job
from .base import (
    StageContext,
    StageResult,
    StageStatus,
    read_json,
    write_json_atomically,
)

STAGE = "ingest"
INGEST_BINARY = Path("workers/ingest/target/release/memory-engine-ingest")
_POLL_INTERVAL_S = 0.5

# Above this many changed files, naming each one as its own source root stops
# being an optimisation: the JobSpec grows unbounded and is rewritten on every
# checkpoint. The number is a shape, not a measurement -- a few thousand paths
# is a JobSpec of a few hundred kilobytes, which is fine, and a hundred
# thousand is not.
_MAX_DELTA_PATHS = 2000


def _binary(ctx: StageContext) -> Path:
    return ctx.repo_root / INGEST_BINARY


def _scan_params(ctx: StageContext) -> dict[str, Any]:
    return {
        "follow_symlinks": ctx.settings.follow_symlinks,
        "include_hidden": ctx.settings.include_hidden,
        "max_depth": ctx.settings.max_depth,
    }


def _checkpoint_path(ctx: StageContext, job_id: str) -> Path:
    return ctx.path("ingest", f"{job_id}.json")


def _records_dir(ctx: StageContext) -> Path:
    target = ctx.workdir / "records"
    target.mkdir(parents=True, exist_ok=True)
    return target


def _inventory_path(ctx: StageContext) -> Path:
    return ctx.workdir / "inventory.json"


def _too_big_for_a_delta(delta, current) -> bool:
    return len(delta.to_ingest) > _MAX_DELTA_PATHS or (
        # Everything changed: almost always a deleted inventory or a --rescan,
        # not a folder that genuinely turned over.
        len(delta.to_ingest) >= len(current.entries) and len(current.entries) > 0
    )


def _reopen(ctx: StageContext, job: dict[str, Any]) -> dict[str, Any]:
    """Put a completed scan job back to pending, from the beginning.

    The cursor is cleared: a full re-read is the point. Outputs are KEPT --
    they are content-addressed, so re-ingesting the same bytes reproduces the
    same records and the same ids, and discarding the list would only lose the
    GoPro span reconciliation's view of what exists.
    """
    job = dict(job)
    job["state"] = dict(job["state"])
    job["state"]["status"] = "pending"
    job["state"]["finished_at"] = None
    job["checkpoint"] = dict(job["checkpoint"])
    job["checkpoint"]["cursor"] = None
    job["checkpoint"]["updated_at"] = None
    ctx.jobs.put(job)
    checkpoint = _checkpoint_path(ctx, job["job_id"])
    if checkpoint.exists():
        # Otherwise `_execute` would prefer the on-disk checkpoint, which still
        # holds the finished cursor, and the "rescan" would scan nothing.
        checkpoint.unlink()
    return job


def run(ctx: StageContext) -> StageResult:
    binary = _binary(ctx)
    if not binary.is_file():
        return StageResult(
            stage=STAGE,
            status=StageStatus.UNAVAILABLE,
            detail=(
                "the ingest worker is not built. "
                f"cd {ctx.repo_root / 'workers/ingest'} && cargo build --release"
            ),
        )

    current = walk(
        ctx.source_roots,
        follow_symlinks=ctx.settings.follow_symlinks,
        include_hidden=ctx.settings.include_hidden,
        max_depth=ctx.settings.max_depth,
    )
    stored = read_json(_inventory_path(ctx))
    previous = None if ctx.settings.rescan or stored is None else Inventory.from_json(stored)
    ctx.reporter.event(
        STAGE,
        "note",
        f"{len(current.entries):,} files under the source roots",
        inventory_digest=current.digest[:12],
    )

    full_job = build_job(
        job_type="scan_source",
        scope=ctx.settings.scope,
        params=_scan_params(ctx),
        source_paths=list(current.roots),
        locator_digest=source_locator_digest(current.roots),
        priority=500,
    )
    stored_full = ctx.jobs.get(full_job["job_id"])

    if stored_full is not None and stored_full["state"]["status"] == "completed":
        preliminary = diff(previous, current)
        if _too_big_for_a_delta(preliminary, current):
            # A delta scan names each changed file as its own source root, so
            # an enormous delta -- an inventory that was deleted, `--rescan`, a
            # whole drive that changed -- would build a JobSpec holding
            # hundreds of thousands of paths and write it to disk repeatedly.
            # Re-opening the full scan is the same work with a bounded job.
            ctx.reporter.event(
                STAGE,
                "note",
                f"{len(preliminary.to_ingest)} files need reading; re-opening the full "
                "scan rather than naming each one as its own source root",
            )
            stored_full = _reopen(ctx, stored_full)

    if stored_full is None or stored_full["state"]["status"] != "completed":
        # Either the first scan of these roots, or one that was interrupted.
        # Both resume through the same path: the Rust worker's cursor.
        job = stored_full or ctx.jobs.ensure(full_job)
        outcome = _execute(ctx, job, roots=list(current.roots), label="full scan")
        if outcome.status is not StageStatus.COMPLETED:
            return outcome
        write_json_atomically(_inventory_path(ctx), current.to_json())
        return outcome

    delta = diff(previous, current)
    if delta.is_empty:
        ctx.reporter.event(
            STAGE, "stage_skipped", "source folder unchanged since the last completed scan"
        )
        return StageResult(
            stage=STAGE,
            status=StageStatus.COMPLETED,
            detail="source folder unchanged; no file was read",
            job_id=full_job["job_id"],
            counts={"files": len(current.entries), **delta.summary()},
        )

    if delta.removed:
        # Reported, never acted on. A photo on an unplugged drive has not
        # stopped existing, and pruning the catalogue on that basis would
        # destroy a library every time somebody ejects a disk.
        ctx.reporter.event(
            STAGE,
            "note",
            f"{len(delta.removed)} previously seen files are no longer present; "
            "their records are kept",
        )

    paths = list(delta.to_ingest)
    if not paths:
        # Removals only. There is nothing to read, and handing the Rust worker
        # an empty root list would make it refuse the job -- reported as a
        # failure, which would be a lie about a folder that is simply smaller
        # than it was.
        write_json_atomically(_inventory_path(ctx), current.to_json())
        return StageResult(
            stage=STAGE,
            status=StageStatus.COMPLETED,
            detail=(
                f"{len(delta.removed)} file(s) are gone from disk and nothing was "
                "added; their records are kept"
            ),
            job_id=full_job["job_id"],
            counts={"files": len(current.entries), **delta.summary()},
        )

    delta_job = build_job(
        job_type="scan_source",
        scope=ctx.settings.scope,
        params=_scan_params(ctx),
        source_paths=paths,
        locator_digest=source_locator_digest(paths),
        priority=600,
    )
    job = ctx.jobs.get(delta_job["job_id"]) or ctx.jobs.ensure(delta_job)
    if job["state"]["status"] == "completed":
        # The same delta was already ingested by an earlier run that died
        # before it could write the inventory. Nothing to redo; just record
        # the inventory so the next run sees a clean folder.
        write_json_atomically(_inventory_path(ctx), current.to_json())
        return StageResult(
            stage=STAGE,
            status=StageStatus.COMPLETED,
            detail=f"the {len(paths)} changed files were already ingested",
            job_id=job["job_id"],
            counts={"files": len(current.entries), **delta.summary()},
        )

    outcome = _execute(ctx, job, roots=paths, label=f"delta scan ({len(paths)} files)")
    if outcome.status is not StageStatus.COMPLETED:
        return outcome
    write_json_atomically(_inventory_path(ctx), current.to_json())
    return StageResult(
        stage=STAGE,
        status=outcome.status,
        detail=outcome.detail,
        job_id=outcome.job_id,
        counts={**dict(outcome.counts), **delta.summary()},
    )


def _execute(
    ctx: StageContext, job: dict[str, Any], *, roots: list[str], label: str
) -> StageResult:
    """Run the Rust worker on one job, resuming it if it has a cursor."""
    checkpoint = _checkpoint_path(ctx, job["job_id"])

    # The worker owns the cursor and writes the whole JobSpec to the checkpoint
    # file after every file. If that file exists and names this job, it is
    # AHEAD of the database copy -- the database is only synchronised when the
    # subprocess returns, and a kill happens between those two moments by
    # definition. So the file wins.
    persisted = read_json(checkpoint)
    if isinstance(persisted, dict) and persisted.get("job_id") == job["job_id"]:
        job = persisted
        ctx.reporter.event(
            STAGE,
            "note",
            "resuming from the worker's checkpoint",
            files_done=(job.get("state", {}).get("progress") or {}).get("units_done", 0),
        )

    # The worker checkpoint is the fine-grained resume manifest and can carry
    # hundreds of thousands of output refs.  The database job is only coarse
    # scheduler state: jobstore.py explicitly names the MediaRecords as the
    # durable truth.  Persisting the worker's whole manifest here makes every
    # state transition traverse/serialize it before the worker can resume.
    # Keep the full `job` for the worker and a compact copy for JobStore.
    tracking = ctx.jobs.begin(_scan_job_for_store(job))
    job = _with_tracking_state(job, tracking)
    ctx.reporter.event(STAGE, "stage_start", label, roots=len(roots))

    job_file = ctx.path("ingest", f"{job['job_id']}.request.json")
    write_json_atomically(job_file, job)

    started = time.monotonic()
    process = subprocess.Popen(
        [str(_binary(ctx)), str(job_file), str(_records_dir(ctx)), str(checkpoint)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    _follow(ctx, process, checkpoint)
    stdout, stderr = process.communicate()

    if process.returncode != 0:
        message = (stderr or stdout or "").strip().splitlines()
        detail = message[-1] if message else f"exit code {process.returncode}"
        ctx.jobs.fail(
            _scan_job_for_store(job),
            code="internal_error", message="ingest worker failed", retryable=True
        )
        return StageResult(stage=STAGE, status=StageStatus.FAILED, detail=detail,
                           job_id=job["job_id"])

    report = _last_json_line(stdout)
    persisted = read_json(checkpoint)
    if not isinstance(persisted, dict) or persisted.get("job_id") != job["job_id"]:
        ctx.jobs.fail(
            _scan_job_for_store(job),
            code="internal_error",
            message="ingest worker wrote no usable checkpoint",
            retryable=True,
        )
        return StageResult(
            stage=STAGE,
            status=StageStatus.FAILED,
            detail="the ingest worker returned success but left no checkpoint to trust",
            job_id=job["job_id"],
        )
    job = persisted

    loaded, skipped, unreadable, imported_media_ids = _load_records(ctx, job)
    elapsed = time.monotonic() - started

    media_outputs = _media_outputs(job)
    unexpected_outputs = len(job.get("outputs") or []) - len(media_outputs)
    duplicate_ids = len(media_outputs) - len({output["id"] for output in media_outputs})
    duplicate_media_ids = len(imported_media_ids) - len(set(imported_media_ids))
    missing_from_database = _missing_media_ids(ctx, imported_media_ids)
    if (
        unreadable
        or unexpected_outputs
        or duplicate_ids
        or duplicate_media_ids
        or missing_from_database
    ):
        detail = (
            "ingest outputs were not durably imported: "
            f"unreadable={unreadable}, unexpected={unexpected_outputs}, "
            f"duplicate_ids={duplicate_ids}, duplicate_media_ids={duplicate_media_ids}, "
            f"missing_from_media_db={len(missing_from_database)}"
        )
        ctx.jobs.fail(
            _scan_job_for_store(job),
            code="internal_error",
            message=detail,
            retryable=True,
        )
        return StageResult(
            stage=STAGE,
            status=StageStatus.FAILED,
            detail=detail,
            job_id=job["job_id"],
            counts={
                "loaded": loaded,
                "already_present": skipped,
                "unreadable": unreadable,
                "unexpected_outputs": unexpected_outputs,
                "duplicate_output_ids": duplicate_ids,
                "duplicate_media_ids": duplicate_media_ids,
                "missing_from_media_db": len(missing_from_database),
            },
        )

    complete = bool(report.get("complete", False))
    if not complete:
        # execute_scan_batch yields with status `pending` when it stops early.
        # It has no reason to today (the runner passes no batch limit), but a
        # runner that assumed completion would silently truncate a library if
        # that ever changed.
        ctx.jobs.put(_scan_job_for_store(job))
        return StageResult(
            stage=STAGE,
            status=StageStatus.FAILED,
            detail="the ingest worker yielded before finishing; re-run to continue",
            job_id=job["job_id"],
            counts={"loaded": loaded, "already_present": skipped},
        )

    # Every worker output is now represented by a durable MediaRecord.  The
    # terminal scheduler JobSpec no longer needs a second 100k-element answer
    # to "what was imported".  Completing the compact form retains full schema
    # validation while making validation/serialization bounded by scheduler
    # state rather than library size.  The on-disk worker checkpoint remains
    # full, so a crash before this line can always retry the import.
    ctx.jobs.complete(_scan_job_for_store(job))
    counts = {
        "processed": int(report.get("processed", 0)),
        "resumed_skips": int(report.get("resumed_skips", 0)),
        "quarantined": int(report.get("quarantined", 0)),
        "issues": len(report.get("issues", []) or []),
        "loaded": loaded,
        "already_present": skipped,
        "elapsed_s": round(elapsed, 2),
    }
    for issue in (report.get("issues") or [])[:5]:
        ctx.reporter.event(
            STAGE, "note", f"issue: {issue.get('code')}: {issue.get('message')}"
        )
    ctx.reporter.event(STAGE, "stage_done", label, **counts)
    return StageResult(
        stage=STAGE, status=StageStatus.COMPLETED, detail=label,
        job_id=job["job_id"], counts=counts,
    )


def _follow(ctx: StageContext, process: subprocess.Popen[str], checkpoint: Path) -> None:
    """Emit progress while the worker runs, by reading its checkpoint file.

    The worker's own stdout is a single report line at the end, which for a
    six-hour import means six hours of silence. The checkpoint file is
    rewritten atomically after every file and already carries a contract
    `Progress`, so tailing it costs nothing and tells the truth -- including
    `units_total: null` while the walk is still discovering.
    """
    # Keyed on (units, message), not units alone: the worker's terminal
    # checkpoint moves from "scanning local source" to "source scan complete"
    # at the SAME unit count, and the boundary event is what the scale
    # harness's phase breakdown is reconstructed from.
    last_seen: tuple[float, str] | None = None

    def forward(*, force: bool = False) -> None:
        nonlocal last_seen
        snapshot = read_json(checkpoint)
        if not isinstance(snapshot, dict):
            return
        progress = (snapshot.get("state") or {}).get("progress") or {}
        done = progress.get("units_done")
        if done is None:
            return
        seen = (float(done), str(progress.get("message") or ""))
        # The dedupe is skipped when forcing: an unforced emit inside the
        # reporter's throttle window is silently DROPPED, so `last_seen` means
        # "read", not "delivered". The forced terminal call must re-emit even
        # a state it already read, or the boundary event's existence depends
        # on poll timing -- a duplicate progress line is harmless, a missing
        # terminal one loses the phase boundary the scale harness rebuilds.
        if not force and seen == last_seen:
            return
        last_seen = seen
        ctx.reporter.progress(
            STAGE,
            units_done=float(done),
            units_total=progress.get("units_total"),
            unit=progress.get("unit") or "files",
            message=progress.get("message") or "",
            force=force,
        )

    while process.poll() is None:
        time.sleep(_POLL_INTERVAL_S)
        forward()
    # One final read AFTER the worker exits: a scan that finishes between two
    # polls otherwise never surfaces its terminal checkpoint.
    forward(force=True)


def _last_json_line(stdout: str) -> dict[str, Any]:
    for line in reversed((stdout or "").strip().splitlines()):
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return {}


def _media_outputs(job: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        output
        for output in (job.get("outputs") or [])
        if output.get("kind") == "media_record" and output.get("id") and output.get("path")
    ]


def _scan_job_for_store(job: dict[str, Any]) -> dict[str, Any]:
    """The schema-valid coarse scan state stored in media-db.

    The worker's on-disk checkpoint keeps the full arrays.  This function is a
    non-mutating shallow copy with fresh mutable children for exactly the
    fields JobStore transitions edit.
    """
    compact = dict(job)
    compact["state"] = dict(job["state"])
    compact["checkpoint"] = dict(job.get("checkpoint") or {})
    compact["checkpoint"]["partial_output_ids"] = []
    compact["outputs"] = []
    error = job.get("error")
    compact["error"] = dict(error) if isinstance(error, dict) else error
    return compact


def _with_tracking_state(job: dict[str, Any], tracking: dict[str, Any]) -> dict[str, Any]:
    worker = dict(job)
    worker["state"] = dict(tracking["state"])
    error = tracking.get("error")
    worker["error"] = dict(error) if isinstance(error, dict) else error
    return worker


def _load_records(
    ctx: StageContext, job: dict[str, Any]
) -> tuple[int, int, int, tuple[str, ...]]:
    """Load this job's MediaRecords into media-db.

    `put_media` is an idempotent upsert, so re-loading is safe; the membership
    check exists only to keep a resumed scan from re-writing rows it already
    wrote. The check is batched rather than done with one big `SELECT
    media_id FROM media`, because materialising every id in a 300k library
    costs tens of megabytes to save a few hundred queries.
    """
    outputs = _media_outputs(job)
    if not outputs:
        return 0, 0, 0, ()

    loaded = 0
    skipped = 0
    unreadable = 0
    media_ids: list[str] = []
    connection = ctx.database.connection
    chunk = 500
    for start in range(0, len(outputs), chunk):
        window = outputs[start : start + chunk]
        verified: list[tuple[dict[str, Any], dict[str, Any], str]] = []
        for output in window:
            record = _verified_media_record(output)
            if record is None:
                unreadable += 1
                ctx.reporter.event(
                    STAGE,
                    "note",
                    "a record the worker reported failed size, digest, or JSON "
                    "verification; it is left out of the library rather than guessed at",
                )
                continue
            media_id = record.get("media_id")
            if not isinstance(media_id, str) or not media_id:
                unreadable += 1
                continue
            verified.append((output, record, media_id))
            media_ids.append(media_id)
        ids = [media_id for _output, _record, media_id in verified]
        if not ids:
            continue
        placeholders = ",".join("?" for _ in ids)
        present = {
            row[0]
            for row in connection.execute(
                f"SELECT media_id FROM media WHERE media_id IN ({placeholders})", ids
            )
        }
        for _output, record, media_id in verified:
            if media_id in present:
                skipped += 1
                continue
            ctx.database.put_media(record)
            loaded += 1
        ctx.reporter.progress(
            STAGE,
            units_done=float(min(start + chunk, len(outputs))),
            units_total=float(len(outputs)),
            unit="items",
            message="storing records",
        )
    ctx.reporter.progress(
        STAGE,
        units_done=float(len(outputs)),
        units_total=float(len(outputs)),
        unit="items",
        message="stored",
        force=True,
    )
    return loaded, skipped, unreadable, tuple(media_ids)


def _verified_media_record(output: dict[str, Any]) -> dict[str, Any] | None:
    """Read one JobOutput only when its exact artifact still matches the contract."""
    try:
        raw = Path(output["path"]).read_bytes()
    except (KeyError, OSError, TypeError):
        return None
    expected_size = output.get("byte_size")
    if not isinstance(expected_size, int) or expected_size != len(raw):
        return None
    expected_digest = output.get("id")
    if not isinstance(expected_digest, str) or blake3_hex(raw) != expected_digest:
        return None
    try:
        record = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    return record if isinstance(record, dict) else None


def _missing_media_ids(ctx: StageContext, media_ids: tuple[str, ...]) -> tuple[str, ...]:
    """Decoded MediaRecord ids absent from media-db after import, in bounded queries."""
    missing: list[str] = []
    connection = ctx.database.connection
    chunk = 500
    for start in range(0, len(media_ids), chunk):
        ids = list(media_ids[start : start + chunk])
        placeholders = ",".join("?" for _ in ids)
        present = {
            row[0]
            for row in connection.execute(
                f"SELECT media_id FROM media WHERE media_id IN ({placeholders})", ids
            )
        }
        missing.extend(media_id for media_id in ids if media_id not in present)
    return tuple(missing)
