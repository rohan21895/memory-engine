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
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..events import utc_now
from ..jobstore import build_job
from .base import (
    StageContext,
    StageResult,
    StageStatus,
    blocked_by,
    read_json,
    write_json_atomically,
)

STAGE = "render-print"
VIDEO_STAGE = "render-video"

_PRINT_ROOT = Path("workers/render-print")
_PRINT_ENTRY = _PRINT_ROOT / "dist" / "workers" / "render-print" / "src" / "cli.js"
_VIDEO_ROOT = Path("workers/render-video")
_VIDEO_ENTRY = _VIDEO_ROOT / "dist" / "workers" / "render-video" / "src" / "cli.js"

VIDEO_SUFFIX = ".mp4"
_VIDEO_PROGRESS_POLL_SECONDS = 0.25
_VIDEO_PROCESS_OUTPUT_BYTES = 256 * 1024

# The delivery decision, stated in one place with nothing implicit.
#
# `RenderTarget` carries no encode profile at all (contracts#56) and the worker
# refuses a job that leaves any of this out, deliberately, so that the codec a
# file ends up in is a decision somebody made rather than a table hidden in a
# renderer. libx264 in MP4 is what the worker's determinism suite covers
# alongside FFV1/Matroska -- byte-identical output across work directories on
# one FFmpeg build -- and it is the profile a person can actually play.
#
# `threads: 1` is not a performance choice. x264 slices a frame across threads
# and the slice boundaries move with the thread count, so a multi-threaded
# encode is only reproducible on a machine with the same core count.
REEL_ENCODE_PROFILE: dict[str, Any] = {
    "container": "mp4",
    "scale_flags": "bicubic",
    "threads": 1,
    "video": {
        "codec": "libx264",
        "pix_fmt": "yuv420p",
        "args": ["-preset", "medium", "-crf", "18"],
    },
    "audio": {"codec": "aac", "sample_fmt": "fltp", "args": ["-b:a", "192k"]},
}


def _video_encode_profile(edl: dict[str, Any]) -> dict[str, Any]:
    """The explicit delivery profile for this planned product.

    The film planner emits ``kind: film`` with destination ``master``. A film
    is long enough that review scrubbing and restart inspection need bounded
    random access: its H.264 stream therefore pins a closed two-second GOP and
    disables scene-cut keyframes, instead of allowing clip content to decide
    where the seek points land. The reel keeps the already-shipped profile.

    This decision belongs here in pipeline shipping code, not in the renderer;
    the complete chosen block still travels in JobSpec.params and its digest.
    """
    kind = edl.get("kind")
    if kind == "reel":
        return json.loads(json.dumps(REEL_ENCODE_PROFILE))
    if kind != "film":
        raise ValueError(f"no explicit video encode profile for EDL kind {kind!r}")

    rate = edl.get("rate")
    if not isinstance(rate, (int, float)) or isinstance(rate, bool) or rate <= 0:
        raise ValueError("a film EDL needs a positive rate before its GOP can be pinned")
    gop_frames = max(1, round(float(rate) * 2))
    return {
        "container": "mp4",
        "scale_flags": "bicubic",
        "threads": 1,
        "video": {
            "codec": "libx264",
            "pix_fmt": "yuv420p",
            "args": [
                "-preset", "medium",
                "-crf", "18",
                "-profile:v", "high",
                "-flags:v", "+cgop",
                "-g", str(gop_frames),
                "-keyint_min", str(gop_frames),
                "-sc_threshold", "0",
            ],
        },
        "audio": {"codec": "aac", "sample_fmt": "fltp", "args": ["-b:a", "192k"]},
    }


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
            status=StageStatus.COMPLETED,
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
    """Every EDL the story stage produced -> `workers/render-video` -> files.

    PLURAL, AND THAT IS THE POINT.

    The story stage emits a reel AND a film. Rendering only `outputs[0]` would
    silently drop whichever plan came second, and the run summary would still
    read `ok` -- a finished pipeline missing one of its three products, with
    nothing anywhere saying so. So this stage renders every EDL it was handed,
    reports each one, and fails if any of them fails.

    THE REFUSALS ARE THE INTERESTING PART OF THIS STAGE.

    `render-video` sorts every declaration in a plan into renderable, refused
    (contract gap), refused (not implemented here) and not-acted-upon, and it
    refuses with the COMPLETE list and the issue number behind each entry. Those
    refusals reach the run summary verbatim. There is no flag that turns one
    into a warning and nothing here supplies a default to get past one: a gap
    means the contract does not say what the picture or the mix should be, and a
    renderer that guessed would produce a file that looks finished.

    THE ENCODE PROFILE ARRIVES FROM HERE, FULLY EXPLICIT.

    `RenderTarget` carries no codec, container, pixel format, rate control or
    scaler (contracts#56), and the worker refuses a job that omits any of them
    rather than keeping a destination-to-codec table where the delivery decision
    would be invisible. So the profile is stated here, in one block, the same
    way the ICC profile is stated for print. `-fflags +bitexact` and friends are
    the worker's, not ours.
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

    story_result = ctx.results.get("story")
    if story_result is None or not story_result.outputs:
        return StageResult(
            stage=VIDEO_STAGE,
            status=StageStatus.SKIPPED,
            detail="the story stage produced no EDL to render",
        )
    edl_paths = [Path(entry) for entry in story_result.outputs]
    for edl_path in edl_paths:
        if not edl_path.is_file() or not edl_path.with_suffix(".sources.json").is_file():
            return StageResult(
                stage=VIDEO_STAGE,
                status=StageStatus.FAILED,
                detail=(
                    f"the story stage named {edl_path} but the plan or its source "
                    "map is not on disk"
                ),
            )

    node = _node()
    entry = ctx.repo_root / _VIDEO_ENTRY
    if node is None:
        return StageResult(
            stage=VIDEO_STAGE,
            status=StageStatus.UNAVAILABLE,
            detail="node is not on PATH, so the video renderer cannot run",
        )
    if not entry.is_file():
        return StageResult(
            stage=VIDEO_STAGE,
            status=StageStatus.UNAVAILABLE,
            detail=(
                "the video renderer is not built. "
                f"cd {ctx.repo_root / _VIDEO_ROOT} && npm install && npm run build"
            ),
        )
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        return StageResult(
            stage=VIDEO_STAGE,
            status=StageStatus.UNAVAILABLE,
            detail="ffmpeg and ffprobe are not both on PATH; nothing can be encoded",
        )

    outcomes = [
        _render_one(ctx, edl_path, node=node, entry=entry) for edl_path in edl_paths
    ]

    failed = [o for o in outcomes if o.status is StageStatus.FAILED]
    detail = "; ".join(o.detail for o in outcomes)
    if failed:
        # Reported together rather than on the first failure, for the same
        # reason the worker lists every refusal at once: "the reel rendered and
        # the film did not" is a different situation from "nothing rendered",
        # and a caller has to be able to tell them apart.
        ctx.reporter.event(VIDEO_STAGE, "stage_failed", detail)
        return StageResult(
            stage=VIDEO_STAGE,
            status=StageStatus.FAILED,
            detail=detail,
            job_id=failed[0].job_id,
            outputs=tuple(str(o.output) for o in outcomes if o.output is not None),
            counts={o.kind: o.counts for o in outcomes},
        )

    status = (
        StageStatus.SKIPPED
        if outcomes and all(o.status is StageStatus.SKIPPED for o in outcomes)
        else StageStatus.COMPLETED
    )
    return StageResult(
        stage=VIDEO_STAGE,
        status=status,
        detail=detail,
        job_id=outcomes[0].job_id if outcomes else None,
        outputs=tuple(str(o.output) for o in outcomes if o.output is not None),
        counts={o.kind: o.counts for o in outcomes},
    )


@dataclass(frozen=True, slots=True)
class _VideoOutcome:
    kind: str
    status: StageStatus
    detail: str
    job_id: str | None
    output: Path | None
    counts: dict[str, Any]


def _render_one(
    ctx: StageContext, edl_path: Path, *, node: str, entry: Path
) -> _VideoOutcome:
    """One EDL through the worker. Never raises; every ending is an outcome."""
    edl = json.loads(edl_path.read_text(encoding="utf-8"))
    sources = json.loads(
        edl_path.with_suffix(".sources.json").read_text(encoding="utf-8")
    )
    kind = str(edl.get("kind") or "video")
    missing = [
        media_id for media_id, entry_ in sources.items()
        if not all(Path(path).is_file() for path in entry_.get("paths") or [])
    ]
    if missing:
        return _VideoOutcome(
            kind=kind,
            status=StageStatus.FAILED,
            detail=(
                f"{kind}: {len(missing)} source file(s) the plan names are not on "
                "disk; the renderer will not substitute black and neither will "
                "this stage"
            ),
            job_id=None,
            output=None,
            counts={"edl_id": edl.get("edl_id")},
        )

    edl_id = edl["edl_id"]
    output = ctx.workdir / "outputs" / "video" / f"{edl_id}{VIDEO_SUFFIX}"
    work_directory = ctx.workdir / "outputs" / "video" / "work"
    output.parent.mkdir(parents=True, exist_ok=True)
    work_directory.mkdir(parents=True, exist_ok=True)

    try:
        encode_profile = _video_encode_profile(edl)
    except ValueError as error:
        return _VideoOutcome(
            kind=kind,
            status=StageStatus.FAILED,
            detail=f"{kind}: {error}",
            job_id=None,
            output=None,
            counts={"edl_id": edl_id},
        )

    params: dict[str, Any] = {
        "output_path": str(output),
        "work_directory": str(work_directory),
        "sources": sources,
        "encode": encode_profile,
        "ffmpeg_path": shutil.which("ffmpeg"),
        "ffprobe_path": shutil.which("ffprobe"),
    }
    job = build_job(
        job_type="render_video",
        scope=ctx.settings.scope,
        params=params,
        media_ids=sorted(sources),
        priority=200,
        requirements={"requires_source_file": True, "min_disk_mb": 2048},
    )
    job["inputs"]["edl_id"] = edl_id
    stored = ctx.jobs.get(job["job_id"])
    if stored is not None:
        stored["inputs"]["edl_id"] = edl_id
        job = stored
    else:
        ctx.jobs.put(job)
    # A completed state is a claim, not proof that its artifact still exists or
    # still contains the bytes the worker validated. Hand it back to the worker
    # unchanged: the completed-job path checks size, BLAKE3, and media probe
    # invariants before it allows reuse. Calling begin() here would erase that
    # state and accidentally turn verification into a fresh encode.
    verifying_completed = job["state"]["status"] == "completed"
    if not verifying_completed:
        job = ctx.jobs.begin(job)
    ctx.reporter.event(
        VIDEO_STAGE,
        "stage_start",
        f"rendering {kind} {edl_id[:12]} from {len(sources)} source(s)",
    )

    job_file = ctx.path("render-video", f"{job['job_id']}.json")
    write_json_atomically(job_file, job)
    process = _run_video_worker(
        ctx,
        [node, str(entry), "run", str(job_file), str(edl_path)],
        job_file=job_file,
        kind=kind,
    )
    written = json.loads(job_file.read_text(encoding="utf-8"))
    ctx.jobs.put(written)

    # Every declaration the worker executed under a stated convention, and every
    # one it recorded without executing, comes back on stderr. Dropping them
    # would make a render that ignored a marker look identical to one that
    # honoured it.
    for line in (process.stderr or "").splitlines():
        if line.startswith(("not acted upon:", "interpreted:")):
            ctx.reporter.event(VIDEO_STAGE, "note", f"{kind}: {line}")

    if process.returncode != 0 or written["state"]["status"] != "completed":
        error = written.get("error") or {}
        detail = error.get("message") or (process.stderr or "").strip() or "render failed"
        return _VideoOutcome(
            kind=kind,
            status=StageStatus.FAILED,
            detail=f"{kind}: {detail}",
            job_id=job["job_id"],
            output=None,
            counts={"edl_id": edl_id, "refusal_code": error.get("code")},
        )

    if verifying_completed:
        recorded = next(
            (
                item for item in written.get("outputs") or []
                if item.get("kind") == "rendered_video"
            ),
            {},
        )
        return _VideoOutcome(
            kind=kind,
            status=StageStatus.COMPLETED,
            detail=f"this {kind} EDL has already been rendered and verified",
            job_id=job["job_id"],
            output=output,
            counts={
                "edl_id": edl_id,
                "byte_size": recorded.get("byte_size"),
                "output_id": recorded.get("id"),
            },
        )

    report = _last_json_object(process.stdout)
    verification = report.get("verification") or {}
    byte_size = output.stat().st_size if output.is_file() else 0
    counts = {
        "edl_id": edl_id,
        "frames": verification.get("frameCount"),
        "resolution": f"{verification.get('width')}x{verification.get('height')}",
        "frame_rate": verification.get("frameRate"),
        "byte_size": byte_size,
        "command_graph_digest": report.get("command_graph_digest"),
        "at": utc_now(),
    }
    ctx.reporter.event(VIDEO_STAGE, "stage_done", f"wrote {output.name}")
    return _VideoOutcome(
        kind=kind,
        status=StageStatus.COMPLETED,
        detail=f"{kind} written to {output}",
        job_id=job["job_id"],
        output=output,
        counts=counts,
    )


def _run_video_worker(
    ctx: StageContext,
    command: list[str],
    *,
    job_file: Path,
    kind: str,
) -> subprocess.CompletedProcess[str]:
    """Run the worker while synchronising its atomic JobSpec heartbeats.

    A film encode can hold ffmpeg for minutes. Waiting in ``subprocess.run``
    leaves the database heartbeat frozen at launch even though the worker is
    writing honest frame progress to ``job_file``. Poll the atomic file, copy
    fresh snapshots into the JobStore, and expose changed frame counts through
    the pipeline reporter. The worker remains the sole progress producer.
    """
    # Files keep child output from filling a pipe while this thread polls. Only
    # their bounded tails are materialised after exit, so a pathological tool
    # cannot turn a long render into an equally long in-memory diagnostic.
    with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
        process = subprocess.Popen(  # noqa: S603
            command,
            stdout=stdout_file,
            stderr=stderr_file,
        )
        last_heartbeat: str | None = None
        last_progress: tuple[float, float | None, str] | None = None
        while process.poll() is None:
            time.sleep(_VIDEO_PROGRESS_POLL_SECONDS)
            snapshot = read_json(job_file)
            if not isinstance(snapshot, dict):
                continue
            state = snapshot.get("state") or {}
            heartbeat = state.get("heartbeat_at")
            if isinstance(heartbeat, str) and heartbeat != last_heartbeat:
                ctx.jobs.put(snapshot)
                last_heartbeat = heartbeat

            progress = state.get("progress") or {}
            done = progress.get("units_done")
            if not isinstance(done, (int, float)) or isinstance(done, bool):
                continue
            total = progress.get("units_total")
            if not isinstance(total, (int, float)) or isinstance(total, bool):
                total = None
            unit = str(progress.get("unit") or "frames")
            current = (float(done), float(total) if total is not None else None, unit)
            if current == last_progress:
                continue
            last_progress = current
            ctx.reporter.progress(
                VIDEO_STAGE,
                units_done=current[0],
                units_total=current[1],
                unit=current[2],
                message=f"{kind} encode",
            )

        stdout = _bounded_process_output(stdout_file)
        stderr = _bounded_process_output(stderr_file)
        return subprocess.CompletedProcess(
            args=command,
            returncode=process.returncode if process.returncode is not None else -1,
            stdout=stdout,
            stderr=stderr,
        )


def _bounded_process_output(stream: Any) -> str:
    stream.flush()
    length = stream.tell()
    stream.seek(max(0, length - _VIDEO_PROCESS_OUTPUT_BYTES))
    return stream.read().decode("utf-8", errors="replace")


def _last_json_object(stdout: str) -> dict[str, Any]:
    text = (stdout or "").strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}
