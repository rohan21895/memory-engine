"""AlbumSpec -> `workers/render-print` -> PDF/X-4.

AVAILABILITY IS CHECKED, NOT ASSUMED, AND THE ANSWER IS NEVER "SUCCESS".

`render-print` is a TypeScript worker that has to be installed and compiled
before it can run. A pipeline that shrugged when it was missing would report a
finished album with no book in it. So the stage probes for node and for the
compiled entry point and returns UNAVAILABLE -- a distinct status that the run
summary prints and that makes the process exit non-zero -- with the exact
command that would fix it.

THE WORKER OWNS THE GATE, AND THAT IS DELIBERATE.

This stage does not re-check DPI floors or trim zones. `render-print` enforces
the print validator as a hard gate with no override flag, and a second
implementation of that gate in a service is how a book that one accepts and the
other rejects gets posted to a customer. What this stage does is hand over an
AlbumSpec that already carries a passing validation report -- the album stage
refuses to write one that does not -- and report whatever the worker decides.

`render-video` IS NOT BUILT HERE.
It is being written in parallel. The stage probes for it exactly as it probes
for render-print and reports UNAVAILABLE until it exists. It is not stubbed,
faked, or approximated: an empty MP4 that the pipeline calls a film is worse
than no film.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

from ..events import utc_now
from ..jobstore import build_job
from .base import (
    StageContext,
    StageResult,
    StageStatus,
    blocked_by,
    write_json_atomically,
)

STAGE = "render-print"
VIDEO_STAGE = "render-video"

_PRINT_ROOT = Path("workers/render-print")
_PRINT_ENTRY = _PRINT_ROOT / "dist" / "workers" / "render-print" / "src" / "cli.js"
_VIDEO_ROOT = Path("workers/render-video")


def _node() -> str | None:
    return shutil.which("node")


def run(ctx: StageContext) -> StageResult:
    if not ctx.settings.render_print:
        return StageResult(
            stage=STAGE, status=StageStatus.SKIPPED, detail="disabled by --no-render-print"
        )

    upstream = ctx.require("album")
    if upstream is not None:
        return blocked_by(STAGE, upstream)

    album_result = ctx.results["album"]
    if not album_result.outputs:
        return StageResult(
            stage=STAGE,
            status=StageStatus.SKIPPED,
            detail="the album stage produced no AlbumSpec to render",
        )
    album_path = Path(album_result.outputs[0])

    node = _node()
    entry = ctx.repo_root / _PRINT_ENTRY
    if node is None:
        return StageResult(
            stage=STAGE,
            status=StageStatus.UNAVAILABLE,
            detail="node is not on PATH, so the print renderer cannot run",
        )
    if not entry.is_file():
        return StageResult(
            stage=STAGE,
            status=StageStatus.UNAVAILABLE,
            detail=(
                "the print renderer is not built. "
                f"cd {ctx.repo_root / _PRINT_ROOT} && npm install && npm run build"
            ),
        )

    album = json.loads(album_path.read_text(encoding="utf-8"))
    album_id = album["album_id"]
    output_pdf = ctx.workdir / "outputs" / "pdf" / f"{album_id}.pdf"
    work_directory = ctx.workdir / "outputs" / "pdf" / f"{album_id}.work"
    output_pdf.parent.mkdir(parents=True, exist_ok=True)
    work_directory.mkdir(parents=True, exist_ok=True)

    asset_paths = _asset_paths(ctx, album)
    missing = [media_id for media_id, path in asset_paths.items() if not path]
    if missing:
        return StageResult(
            stage=STAGE,
            status=StageStatus.FAILED,
            detail=(
                f"{len(missing)} placed photos have no resolvable source file; the "
                "renderer will not invent pixels and neither will this stage"
            ),
        )

    icc_name = album["vendor_profile"]["color_profile"]["icc_name"]
    if ctx.settings.icc_profile_path:
        icc_profile: dict[str, str] = {
            "name": icc_name, "path": ctx.settings.icc_profile_path
        }
    else:
        icc_profile = {"name": icc_name, "builtin": "cmyk"}
        ctx.reporter.event(
            STAGE,
            "note",
            f"no ICC file supplied for {icc_name!r}; embedding the built-in CMYK "
            "profile under that name. Development only — pass --icc-profile before "
            "sending anything to a printer.",
        )
    params: dict[str, Any] = {
        "output_path": str(output_pdf),
        "work_directory": str(work_directory),
        "icc_profile": icc_profile,
        "asset_paths": asset_paths,
        "font_paths": {},
    }
    job = build_job(
        job_type="render_print",
        scope=ctx.settings.scope,
        params=params,
        media_ids=sorted(asset_paths),
        priority=200,
        requirements={"requires_source_file": True, "min_disk_mb": 512},
    )
    job = ctx.jobs.get(job["job_id"]) or ctx.jobs.ensure(job)
    job["inputs"]["album_id"] = album_id
    if job["state"]["status"] == "completed" and output_pdf.is_file():
        return StageResult(
            stage=STAGE,
            status=StageStatus.SKIPPED,
            detail="this album has already been rendered",
            job_id=job["job_id"],
            outputs=(str(output_pdf),),
        )

    job = ctx.jobs.begin(job)
    ctx.reporter.event(STAGE, "stage_start", f"rendering {len(album['pages'])} pages")

    job_file = ctx.path("render-print", f"{job['job_id']}.json")
    write_json_atomically(job_file, job)
    process = subprocess.run(  # noqa: S603
        [node, str(entry), "run", str(job_file), str(album_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    # The worker persists its own JobSpec back to the same file, which is where
    # its checkpoint and outputs live. Read it back rather than trusting the
    # exit code alone.
    written = json.loads(job_file.read_text(encoding="utf-8"))
    ctx.jobs.put(written)

    if process.returncode != 0 or written["state"]["status"] != "completed":
        error = written.get("error") or {}
        detail = error.get("message") or (process.stderr or "").strip() or "render failed"
        ctx.reporter.event(STAGE, "stage_failed", detail)
        return StageResult(
            stage=STAGE, status=StageStatus.FAILED, detail=detail, job_id=job["job_id"]
        )

    ctx.reporter.event(STAGE, "stage_done", f"wrote {output_pdf.name}")
    return StageResult(
        stage=STAGE,
        status=StageStatus.COMPLETED,
        detail=f"PDF/X-4 written to {output_pdf}",
        job_id=job["job_id"],
        outputs=(str(output_pdf),),
        counts={"pages": len(album["pages"]), "at": utc_now()},
    )


def _asset_paths(ctx: StageContext, album: dict[str, Any]) -> dict[str, str]:
    """Original file paths for every placed photo.

    `resolve_path` is the one query API that returns an original, and it exists
    for exactly this caller: the renderer is the second and last time a source
    file is opened in its life.
    """
    paths: dict[str, str] = {}
    for page in album["pages"]:
        for placement in page.get("placements") or []:
            media_id = placement["media_id"]
            if media_id in paths:
                continue
            paths[media_id] = ctx.database.resolve_path(media_id) or ""
    return paths


def run_video(ctx: StageContext) -> StageResult:
    """`render-video` availability probe.

    Written now so the wiring exists and the absence is reported rather than
    discovered later. When the worker lands, this stage gets an EDL from a
    story stage that also does not exist yet -- both are named in the run
    summary as not-built, which is the honest position.
    """
    if not ctx.settings.render_video:
        return StageResult(
            stage=VIDEO_STAGE, status=StageStatus.SKIPPED,
            detail="disabled by --no-render-video",
        )
    root = ctx.repo_root / _VIDEO_ROOT
    if not root.is_dir():
        return StageResult(
            stage=VIDEO_STAGE,
            status=StageStatus.UNAVAILABLE,
            detail=(
                "workers/render-video does not exist in this checkout; no EDL can be "
                "rendered and none is claimed to be"
            ),
        )
    upstream = ctx.require("story")
    if upstream is not None:
        return blocked_by(VIDEO_STAGE, upstream)
    return StageResult(
        stage=VIDEO_STAGE,
        status=StageStatus.UNAVAILABLE,
        detail=(
            "workers/render-video is present but this runner has no wiring for it yet; "
            "the EDL-producing story stage is not implemented"
        ),
    )
