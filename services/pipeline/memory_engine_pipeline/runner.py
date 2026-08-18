"""The spine: a folder goes in, whatever can be finished comes out.

    folder
      -> workers/ingest (Rust)        hash, EXIF, pHash, 512px proxies
      -> packages/media-db            store, index, FTS, vectors
      -> analysis                     classical quality here, models via
                                      workers/ml-runtime over gRPC
      -> packages/ranking-engine      dedupe, primary selection, fusion
      -> packages/album-engine        clustering, selection, layout, validation
      -> workers/render-print         PDF/X-4
      -> story                        480p proxies, feature streams, moments,
                                      and a reel EDL
      -> workers/render-video         EDL -> an .mp4

FOUR PROPERTIES, AND WHERE EACH ONE IS ENFORCED

1. IDEMPOTENT BY CONTENT-ADDRESSED ID. Every stage builds a JobSpec whose
   `job_id` is a BLAKE3 over its identity tuple, looks it up, and does nothing
   when it finds a completed one. Ingest additionally keeps a stat inventory,
   because content addressing cannot short-circuit the step that computes the
   content hash.

2. EVERY STAGE RESUMABLE. Killed `running` jobs are reclaimed to `pending` on
   startup (`JobStore.reclaim`). Ingest resumes from the Rust worker's own path
   cursor; analysis resumes from per-record, per-step state on the MediaRecords
   themselves. Nothing resumes by re-doing.

3. THE MODEL HOST IS A HARD GATE. If it is absent, analysis is BLOCKED, the
   library never reaches `analyzed`, the album stage refuses, and the process
   exits non-zero. There is no flag that downgrades this to a warning.

4. STRUCTURED PROGRESS. Every stage emits JSONL to `<workdir>/events.jsonl`
   and human lines to the terminal, in real units, with `units_total` left null
   until it is genuinely known.

STAGE ORDER IS A LIST, NOT A GRAPH. Seven stages in a fixed order, each declaring
its dependencies through `ctx.require`. A general scheduler would be more
impressive and would need a topological sort, a cycle check and a test suite of
its own to earn the same guarantee this list gives by construction.
"""

from __future__ import annotations

import os
import socket
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .events import ProgressReporter, utc_now
from .ids import digest_of, source_locator_digest
from .jobstore import JobStore
from .stages import album, analysis, faces, ingest, ranking, render, story
from .stages.base import (
    Settings,
    StageContext,
    StageResult,
    StageStatus,
    write_json_atomically,
)

__all__ = ["RunReport", "STAGE_ORDER", "run_pipeline"]

_REPO_ROOT = Path(__file__).resolve().parents[3]

Stage = tuple[str, Callable[[StageContext], StageResult]]

STAGE_ORDER: tuple[Stage, ...] = (
    (ingest.STAGE, ingest.run),
    (analysis.STAGE, analysis.run),
    # Faces before ranking and album: clustering is a whole-library pass, and
    # the album's face-safe layout reads the rectangles it stores.
    (faces.STAGE, faces.run),
    (ranking.STAGE, ranking.run),
    (album.STAGE, album.run),
    (render.STAGE, render.run),
    (story.STAGE, story.run),
    (render.VIDEO_STAGE, render.run_video),
)

STAGE_NAMES: tuple[str, ...] = tuple(name for name, _ in STAGE_ORDER)


@dataclass(frozen=True, slots=True)
class RunReport:
    run_id: str
    workdir: Path
    source_roots: tuple[str, ...]
    results: tuple[StageResult, ...]
    reclaimed_jobs: tuple[str, ...]

    @property
    def ok(self) -> bool:
        """True only when nothing was blocked, unavailable or failed.

        SKIPPED counts as fine -- "already done" is a successful outcome for an
        idempotent runner and is the normal result of a second run.
        """
        return all(result.status.is_success for result in self.results)

    @property
    def exit_code(self) -> int:
        if all(result.status.is_success for result in self.results):
            return 0
        if any(result.status is StageStatus.FAILED for result in self.results):
            return 2
        return 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "workdir": str(self.workdir),
            "source_roots": list(self.source_roots),
            "ok": self.ok,
            "exit_code": self.exit_code,
            "reclaimed_jobs": list(self.reclaimed_jobs),
            "stages": [result.to_dict() for result in self.results],
            "finished_at": utc_now(),
        }


def _run_id(roots: Sequence[str], settings: Settings) -> str:
    """Stable across runs over the same roots with the same settings.

    Deliberately NOT a uuid. The run id appears on every event line, and two
    runs over the same folder are two attempts at the same work -- a fresh
    random id per attempt would make the event log of a resumed import look
    like the log of a different import.
    """
    return digest_of(
        {
            "roots": list(roots),
            "scope": settings.scope,
            "follow_symlinks": settings.follow_symlinks,
            "include_hidden": settings.include_hidden,
            "max_depth": settings.max_depth,
        }
    )[:16]


def run_pipeline(
    source_roots: Sequence[str | os.PathLike[str]],
    workdir: str | os.PathLike[str],
    *,
    settings: Settings | None = None,
    stages: Sequence[str] | None = None,
    reporter: ProgressReporter | None = None,
    repo_root: Path | None = None,
    database: Any = None,
) -> RunReport:
    from memory_engine_media_db import Database  # noqa: PLC0415

    settings = settings or Settings()
    repo_root = repo_root or _REPO_ROOT
    workdir = Path(workdir).expanduser().resolve()
    workdir.mkdir(parents=True, exist_ok=True)

    # Resolving the roots here means an unreadable or missing folder fails
    # before a database is created for it, rather than producing an empty
    # library that looks like a folder with nothing in it.
    roots = tuple(str(Path(root).expanduser().resolve(strict=True)) for root in source_roots)
    locator = source_locator_digest(roots)
    run_id = _run_id(roots, settings)

    owns_database = database is None
    owns_reporter = reporter is None
    database = database or Database.open(workdir / "library.db")
    reporter = reporter or ProgressReporter(run_id=run_id, path=workdir / "events.jsonl")
    # A caller that built the reporter before the roots were resolved cannot
    # have known the run id; it is derived here and stamped on, so every line
    # in events.jsonl belongs to an identifiable run.
    reporter.run_id = run_id

    selected = tuple(stages) if stages is not None else STAGE_NAMES
    unknown = [name for name in selected if name not in STAGE_NAMES]
    if unknown:
        raise ValueError(f"unknown stage(s): {', '.join(unknown)}")

    jobs = JobStore(database=database, worker_id=f"pipeline@{socket.gethostname()}")
    ctx = StageContext(
        run_id=run_id,
        settings=settings,
        source_roots=roots,
        workdir=workdir,
        repo_root=repo_root,
        database=database,
        jobs=jobs,
        reporter=reporter,
        selected=frozenset(selected),
    )

    try:
        reporter.event(
            "run",
            "run_start",
            f"{len(roots)} source root(s)",
            workdir=str(workdir),
            locator=locator[:12],
            scope=settings.scope,
        )
        reclaimed = tuple(jobs.reclaim())
        if reclaimed:
            reporter.event(
                "run",
                "note",
                f"{len(reclaimed)} job(s) were left running by a killed process and "
                "have been returned to pending",
            )

        results: list[StageResult] = []
        for name, function in STAGE_ORDER:
            if name not in selected:
                continue
            try:
                result = function(ctx)
            except Exception as error:  # noqa: BLE001 - a stage must not kill the run
                # A stage that raises has still left its job row in whatever
                # state it reached, and the next run resumes from there. What
                # must not happen is the remaining stages running as if this
                # one had succeeded, which is why the result is recorded as
                # FAILED and dependents will refuse.
                result = StageResult(
                    stage=name,
                    status=StageStatus.FAILED,
                    detail=f"{type(error).__name__}: {error}",
                )
                reporter.event(name, "stage_failed", result.detail)
            ctx.results[name] = result
            results.append(result)
            if result.status not in (StageStatus.COMPLETED, StageStatus.SKIPPED):
                reporter.event(name, f"stage_{result.status.value}", result.detail)

        report = RunReport(
            run_id=run_id,
            workdir=workdir,
            source_roots=roots,
            results=tuple(results),
            reclaimed_jobs=reclaimed,
        )
        write_json_atomically(workdir / "run.json", report.to_dict())
        reporter.event(
            "run",
            "run_done",
            "ok" if report.ok else "incomplete",
            exit_code=report.exit_code,
        )
        return report
    finally:
        if owns_reporter:
            reporter.close()
        if owns_database:
            database.close()


def format_report(report: RunReport) -> str:
    rule = "-" * 74
    lines = [rule, "  MEMORY ENGINE — pipeline run", rule]
    lines.append(f"  run      {report.run_id}")
    lines.append(f"  source   {', '.join(report.source_roots)}")
    lines.append(f"  workdir  {report.workdir}")
    lines.append("")
    marker = {
        StageStatus.COMPLETED: "ok  ",
        StageStatus.SKIPPED: "--  ",
        StageStatus.BLOCKED: "!!  ",
        StageStatus.UNAVAILABLE: "??  ",
        StageStatus.FAILED: "XX  ",
    }
    for result in report.results:
        lines.append(
            f"  {marker[result.status]}{result.stage:<14}{result.status.value:<12}"
            f"{result.detail}"
        )
        for path in result.outputs:
            lines.append(f"      -> {path}")
    lines.append("")
    produced = [
        (result.stage, path) for result in report.results for path in result.outputs
    ]
    if produced:
        lines.append("  produced")
        for stage, path in produced:
            lines.append(f"    {stage:<14}{path}")
    else:
        # An empty artifact list is the normal outcome of a blocked run and it
        # has to be said out loud. A summary that lists six stages and no files
        # reads as success to anyone skimming it.
        lines.append("  produced  nothing: no stage that ran writes a finished artifact")
    lines.append("")
    if report.ok:
        lines.append("  every stage that was asked for either did its work or had none to do.")
    else:
        stalled = [
            result for result in report.results if not result.status.is_success
        ]
        lines.append(
            f"  {len(stalled)} stage(s) did not finish. Nothing downstream of them ran, "
            "and nothing they would have produced exists."
        )
    lines.append(rule)
    return "\n".join(lines)
