#!/usr/bin/env python3
"""End-to-end demo: a folder of media in, an inspected library out.

Runs as much of the pipeline as the machine can actually run, and says --
loudly, per stage, in the running output AND again in a ledger at the end --
which parts did not run and what that means was not proven.

    folder
      -> workers/ingest          (Codex, Rust)  hash, EXIF, pHash, thumbnails,
                                                GoPro chapter spans
      -> workers/ingest proxies  (Codex, Rust)  480p proxies + frame index
      -> packages/media-db       (Claude)       store, index, full-text search
      -> packages/ranking-engine (Claude)       near-duplicate grouping, fusion
      -> packages/album-engine   (Claude)       event clustering, selection
      -> this script                            check it against what was
                                                declared, and report the gaps

WHY THE SKIP REPORTING IS SO INSISTENT
--------------------------------------
Three separate files in this repo have shipped a stage that printed a tick
when it had in fact skipped its work, and each one hid a real failure for
days. So:

  * a skipped stage prints SKIPPED, in the running output, with the missing
    dependency named and the consequence spelled out
  * a stage the demo has not wired up yet prints NOT WIRED -- not "skipped",
    because nothing is missing except this script
  * the final line is never "done". It states how many stages ran, how many
    were skipped, and how many failed
  * exit status: 0 only when every stage ran and every check passed,
    2 when nothing failed but something did not run, 1 on any failure

A run that ends in exit 2 is an INCOMPLETE run, not a passing one.

CHECKS
------
When the source directory carries a MANIFEST.json from
`scripts/demo/make_library.py`, this script does not merely print what
happened: it compares what happened against what the generator declared must
happen -- which files must be quarantined, which must group as near-duplicates,
which GoPro chapters must assemble into which span, which still must come back
with no date at all. Without a manifest those stages skip, because printing
numbers nobody checks is how a broken pipeline looks healthy.

Usage:
    python3 scripts/demo/make_library.py --out /tmp/demo-library
    python3 scripts/demo/run_demo.py /tmp/demo-library --search sunset
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

REPO = Path(__file__).resolve().parent.parent.parent
INGEST_BIN = REPO / "workers/ingest/target/release/memory-engine-ingest"
INGEST_SRC = REPO / "workers/ingest"
CONTRACTS_RUST = REPO / "contracts/codegen/generated/rust"

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(REPO / "packages/media-db"))
sys.path.insert(0, str(REPO / "packages/ranking-engine"))
sys.path.insert(0, str(REPO / "packages/album-engine"))
sys.path.insert(0, str(REPO / "services/pipeline"))

from _paths import real_media_location  # noqa: E402

RULE = "-" * 74


# ---------------------------------------------------------------------------
# Stage bookkeeping
# ---------------------------------------------------------------------------

OK = "OK"
SKIPPED = "SKIPPED"
NOT_WIRED = "NOT WIRED"
FAILED = "FAILED"


@dataclass
class Check:
    """One declared expectation, and whether reality matched it."""

    name: str
    passed: bool
    detail: str = ""
    # Set when a check fails for a reason that is understood and attributable.
    # It explains the failure; it does NOT downgrade it. A known defect is
    # still a defect, and a run containing one still exits non-zero.
    attribution: str = ""


@dataclass
class Stage:
    number: int
    title: str
    status: str = OK
    reason: str = ""          # why it did not run
    remedy: str = ""          # how to make it run
    consequence: str = ""     # what is therefore unproven
    lines: list[str] = field(default_factory=list)
    checks: list[Check] = field(default_factory=list)

    @property
    def failed_checks(self) -> list[Check]:
        return [check for check in self.checks if not check.passed]

    def note(self, text: str) -> None:
        self.lines.append(text)

    def check(self, name: str, passed: bool, detail: str = "",
              attribution: str = "") -> bool:
        self.checks.append(Check(name, passed, detail, attribution))
        return passed

    def skip(self, reason: str, consequence: str, remedy: str = "") -> None:
        self.status = SKIPPED
        self.reason = reason
        self.consequence = consequence
        self.remedy = remedy

    def not_wired(self, reason: str, consequence: str) -> None:
        self.status = NOT_WIRED
        self.reason = reason
        self.consequence = consequence

    def fail(self, reason: str, remedy: str = "") -> None:
        self.status = FAILED
        self.reason = reason
        self.remedy = remedy


class Run:
    """Holds the stages and does all the printing."""

    def __init__(self, total: int) -> None:
        self.total = total
        self.stages: list[Stage] = []

    def stage(self, title: str) -> Stage:
        stage = Stage(number=len(self.stages) + 1, title=title)
        self.stages.append(stage)
        return stage

    def report(self, stage: Stage) -> None:
        # A stage with a failed check is a failed stage. Decided in one place
        # so it cannot be forgotten at a call site, and applied whatever the
        # stage thought its status was -- a stage that ran half its work and
        # then declared itself NOT WIRED must not bury a failure in the half
        # that ran.
        if stage.failed_checks and stage.status != FAILED:
            prior = stage.reason
            stage.status = FAILED
            stage.reason = f"{len(stage.failed_checks)} declared expectation(s) not met"
            if prior:
                stage.reason += f"; also: {prior}"

        label = {OK: "ok", SKIPPED: "SKIPPED", NOT_WIRED: "NOT WIRED",
                 FAILED: "FAILED"}[stage.status]
        print(f"\n  [{stage.number}/{self.total}] {stage.title:<34} {label}")
        for line in stage.lines:
            print(f"          {line}")
        if stage.reason:
            print(f"          reason:      {stage.reason}")
        if stage.consequence:
            print(f"          unproven:    {stage.consequence}")
        if stage.remedy:
            print(f"          to fix:      {stage.remedy}")
        passed = len(stage.checks) - len(stage.failed_checks)
        if stage.checks:
            print(f"          checks:      {passed}/{len(stage.checks)} passed")
        for check in stage.failed_checks:
            print(f"            FAIL  {check.name}")
            if check.detail:
                print(f"                  {check.detail}")
            if check.attribution:
                print(f"                  known cause: {check.attribution}")
        sys.stdout.flush()

    def exit_code(self) -> int:
        if any(stage.status == FAILED for stage in self.stages):
            return 1
        if any(stage.status in (SKIPPED, NOT_WIRED) for stage in self.stages):
            return 2
        return 0

    def ledger(self) -> int:
        ran = [s for s in self.stages if s.status == OK]
        skipped = [s for s in self.stages if s.status in (SKIPPED, NOT_WIRED)]
        failed = [s for s in self.stages if s.status == FAILED]
        checks = sum(len(s.checks) for s in self.stages)
        bad = sum(len(s.failed_checks) for s in self.stages)

        print(f"\n{RULE}\n  STAGE LEDGER\n{RULE}")
        for stage in self.stages:
            mark = {OK: "  ran    ", SKIPPED: "  SKIPPED", NOT_WIRED: "  NOT WIRED",
                    FAILED: "  FAILED "}[stage.status]
            print(f"{mark}  {stage.number:>2}. {stage.title}")
        print(f"\n  {len(ran)} ran   {len(skipped)} did not run   {len(failed)} failed")
        print(f"  {checks - bad}/{checks} declared expectations met")

        if skipped:
            print(f"\n{RULE}\n  WHAT DID NOT RUN — and what is therefore unproven\n{RULE}")
            for stage in skipped:
                print(f"\n  {stage.number}. {stage.title}  [{stage.status}]")
                print(f"     reason:     {stage.reason}")
                print(f"     unproven:   {stage.consequence}")
                if stage.remedy:
                    print(f"     to fix:     {stage.remedy}")

        if failed:
            print(f"\n{RULE}\n  FAILURES\n{RULE}")
            for stage in failed:
                print(f"\n  {stage.number}. {stage.title}")
                if stage.reason:
                    print(f"     {stage.reason}")
                for check in stage.failed_checks:
                    print(f"     - {check.name}")
                    if check.detail:
                        print(f"       {check.detail}")
                    if check.attribution:
                        print(f"       known cause: {check.attribution}")
                if stage.remedy:
                    print(f"     to fix: {stage.remedy}")

        code = self.exit_code()
        print(f"\n{RULE}")
        if code == 0:
            print("  RESULT: every stage ran and every declared expectation was met.")
        elif code == 2:
            print(f"  RESULT: INCOMPLETE. {len(skipped)} of {len(self.stages)} stages did "
                  "not run.")
            print("          Nothing failed, but this run does NOT show a working")
            print("          pipeline -- only that the parts which ran, ran.")
        else:
            print(f"  RESULT: FAILED. {len(failed)} stage(s) failed"
                  + (f", {len(skipped)} did not run." if skipped else "."))
            # Attributed and unattributed failures are both failures and both
            # keep the exit code at 1. They are counted apart only so that a
            # NEW failure is visible next to the standing ones instead of
            # blending into them.
            attributed = [c for s in self.stages for c in s.failed_checks if c.attribution]
            unattributed = [c for s in self.stages for c in s.failed_checks
                            if not c.attribution]
            print(f"          {len(attributed)} failing check(s) have a known, "
                  "attributed cause (shown above).")
            print(f"          {len(unattributed)} failing check(s) have NO known "
                  "cause and need investigating.")
            print("          Do not read any part of this run as a pass.")
        print(f"  exit {code}\n{RULE}\n")
        return code


# ---------------------------------------------------------------------------
# JobSpec construction
# ---------------------------------------------------------------------------


def blake3_hex(data: bytes) -> str:
    import blake3

    return blake3.blake3(data).hexdigest()


def canonical_locator(paths: Sequence[str]) -> str:
    """The digest that keeps two scans of different folders from colliding.

    BLAKE3, matching JobSpec.inputs.source_locator_digest and the Rust ingest
    worker. Getting this wrong is not a silent failure -- ingest recomputes the
    digest and refuses the job -- which is the contract working as intended.
    """
    normalised = sorted(unicodedata.normalize("NFC", p.rstrip("/")) for p in paths)
    return blake3_hex("\x00".join(normalised).encode())


def _job_skeleton(job_id: str, job_type: str, params: dict) -> dict:
    return {
        "schema_version": "v0",
        "job_id": job_id,
        "job_type": job_type,
        "inputs": {
            "media_ids": [], "moment_ids": [], "face_ids": [],
            "edl_id": None, "album_id": None,
            "source_paths": [], "source_locator_digest": None,
            "parent_job_id": None, "depends_on_job_ids": [], "models": [],
        },
        "params": params,
        "params_digest": blake3_hex(
            json.dumps(params, sort_keys=True, separators=(",", ":")).encode()
        ),
        "scope": "demo",
        "priority": 500,
        "egress": {
            "requires_egress": False, "consent": None,
            "destination": None, "payload_kind": None, "estimated_bytes": None,
        },
        "state": {
            "status": "pending", "attempts": 0, "worker_id": None,
            "started_at": None, "heartbeat_at": None, "finished_at": None,
            "progress": None,
        },
        "checkpoint": {
            "resumable": True, "cursor": None, "checkpoint_version": 1,
            "updated_at": None, "completed_input_ids": [], "partial_output_ids": [],
        },
        "outputs": [], "error": None, "journal": {"entries": []},
        "created_at": "2026-08-16T00:00:00+00:00", "deadline": None,
    }


def scan_job(source: Path) -> dict:
    params = {"follow_symlinks": False, "include_hidden": False, "max_depth": 32}
    locator = canonical_locator([str(source)])
    params_digest = blake3_hex(
        json.dumps(params, sort_keys=True, separators=(",", ":")).encode()
    )
    job_id = blake3_hex(
        "\x1f".join(["scan_source", "", locator, params_digest, "demo"]).encode()
    )
    job = _job_skeleton(job_id, "scan_source", params)
    job["inputs"]["source_paths"] = [str(source)]
    job["inputs"]["source_locator_digest"] = locator
    return job


def proxy_job(media_ids: Sequence[str], tag: str) -> dict:
    params = {
        "height": 480, "codec": "h264", "crf": 26,
        "hardware_decode": hardware_backend(), "emit_frame_index": True,
    }
    job_id = blake3_hex(("proxy\x1f" + tag + "\x1f" + "\x1f".join(media_ids)).encode())
    job = _job_skeleton(job_id, "generate_video_proxy", params)
    job["inputs"]["media_ids"] = list(media_ids)
    job["requirements"] = {"hardware_decode": True}
    return job


def hardware_backend() -> str:
    """The only backend the ingest worker will accept on this platform.

    `video.rs::HardwareBackend::supported_on_host` hard-codes this mapping, so
    guessing differently produces UnsupportedBackend rather than a fallback.
    """
    if sys.platform == "darwin":
        return "videotoolbox"
    if sys.platform == "win32":
        return "nvdec"
    return "unsupported"


def run_ingest(job: dict, workdir: Path, records_dir: Path,
               name: str, ffmpeg: str | None) -> tuple[int, dict | None, str]:
    job_path = workdir / f"job-{name}.json"
    checkpoint = workdir / f"checkpoint-{name}.json"
    job_path.write_text(json.dumps(job, indent=2), encoding="utf-8")
    records_dir.mkdir(parents=True, exist_ok=True)

    environment = dict(os.environ)
    if ffmpeg:
        environment["MEMORY_ENGINE_FFMPEG"] = ffmpeg
    result = subprocess.run(
        [str(INGEST_BIN), str(job_path), str(records_dir), str(checkpoint)],
        capture_output=True, text=True, env=environment,
    )
    report = None
    if result.stdout.strip():
        try:
            report = json.loads(result.stdout.strip().splitlines()[-1])
        except json.JSONDecodeError:
            report = None
    message = (result.stderr or result.stdout).strip()
    return result.returncode, report, message


def load_records(records_dir: Path) -> dict[str, dict]:
    records: dict[str, dict] = {}
    for path in sorted(records_dir.rglob("*.json")):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict) and "media_id" in record:
            records[record["media_id"]] = record
    return records


def source_name(record: dict) -> str:
    sources = record.get("sources") or []
    if not sources:
        return f"(assembly {record['media_id'][:10]})"
    return Path(sources[0]["path"]).name


def relpath_of(record: dict, source: Path) -> str | None:
    sources = record.get("sources") or []
    if not sources:
        return None
    try:
        return str(Path(sources[0]["path"]).resolve().relative_to(source))
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Stage 1: preflight
# ---------------------------------------------------------------------------


@dataclass
class Tools:
    ingest: bool = False
    ingest_stale: bool = False
    ingest_age: str = ""
    ffmpeg: str | None = None
    hardware: bool = False
    hardware_detail: str = ""
    blake3: bool = False
    manifest: dict | None = None
    model_weights: int = 0
    ml_runtime: bool = False


def newest_source_mtime() -> float:
    newest = 0.0
    for root in (INGEST_SRC / "src", CONTRACTS_RUST / "src"):
        if not root.is_dir():
            continue
        for path in root.rglob("*.rs"):
            newest = max(newest, path.stat().st_mtime)
    for extra in (INGEST_SRC / "Cargo.toml", INGEST_SRC / "Cargo.lock"):
        if extra.is_file():
            newest = max(newest, extra.stat().st_mtime)
    return newest


def check_hardware_decode(ffmpeg: str) -> tuple[bool, str]:
    """Mirror `video.rs::verify_backend`, which is what actually gates the job.

    Asking FFmpeg the same three questions the Rust worker asks means a SKIP
    here means the worker really would refuse, rather than this script and the
    worker disagreeing about what the machine can do.
    """
    backend = hardware_backend()
    wanted = {
        "videotoolbox": [("-hwaccels", "videotoolbox"), ("-filters", "scale_vt"),
                         ("-encoders", "h264_videotoolbox")],
        "nvdec": [("-hwaccels", "cuda"), ("-filters", "scale_cuda"),
                  ("-encoders", "h264_nvenc")],
    }.get(backend)
    if wanted is None:
        return False, (
            f"the ingest worker supports no hardware backend on {sys.platform} "
            "(video.rs allows videotoolbox on macOS and nvdec/qsv on Windows only)"
        )
    missing = []
    for listing, capability in wanted:
        try:
            output = subprocess.run(
                [ffmpeg, "-hide_banner", listing], capture_output=True, text=True,
            )
        except OSError as error:
            return False, f"could not run {ffmpeg}: {error}"
        if capability not in output.stdout:
            missing.append(capability)
    if missing:
        return False, f"{ffmpeg} lacks {', '.join(missing)}"
    return True, f"{backend} via {ffmpeg}"


def preflight(run: Run, source: Path, ffmpeg_arg: str) -> Tools:
    stage = run.stage("preflight — what this machine has")
    tools = Tools()

    tools.ingest = INGEST_BIN.is_file()
    if tools.ingest:
        binary_mtime = INGEST_BIN.stat().st_mtime
        newest = newest_source_mtime()
        tools.ingest_stale = newest > binary_mtime
        delta = max(0.0, newest - binary_mtime)
        tools.ingest_age = (
            f"{delta:.0f}s" if delta < 90
            else f"{delta / 60:.0f}m" if delta < 5400
            else f"{delta / 3600:.1f}h"
        ) + " older than its newest source"
        stage.note(f"ingest binary   {INGEST_BIN}")
        # A stale binary is worse than a missing one. A missing binary stops
        # the demo; a stale one runs and quietly produces last week's answers.
        # Measured: a binary built before GoPro span assembly existed reported
        # zero assemblies and zero issues, which read exactly like "this
        # library has no chaptered video".
        stage.check(
            "ingest binary is newer than its sources",
            not tools.ingest_stale,
            f"binary is {tools.ingest_age}; it will silently run old logic",
            attribution="the binary was not rebuilt after workers/ingest changed",
        )
        if tools.ingest_stale:
            stage.remedy = f"cd {INGEST_SRC} && cargo build --release"
    else:
        stage.note(f"ingest binary   MISSING at {INGEST_BIN}")

    resolved_ffmpeg = shutil.which(ffmpeg_arg)
    tools.ffmpeg = resolved_ffmpeg
    if resolved_ffmpeg:
        tools.hardware, tools.hardware_detail = check_hardware_decode(resolved_ffmpeg)
        stage.note(f"ffmpeg          {resolved_ffmpeg}")
        stage.note(f"hw decode       {'yes — ' if tools.hardware else 'no — '}"
                   f"{tools.hardware_detail}")
    else:
        stage.note(f"ffmpeg          MISSING (looked for {ffmpeg_arg!r})")

    try:
        import blake3  # noqa: F401

        tools.blake3 = True
    except ImportError:
        tools.blake3 = False
    stage.note(f"blake3          {'present' if tools.blake3 else 'MISSING (pip install blake3)'}")

    manifest_path = source / "MANIFEST.json"
    if manifest_path.is_file():
        try:
            tools.manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            stage.check("MANIFEST.json parses", False, str(error))
        else:
            stage.note(f"manifest        {manifest_path.name}, seed "
                       f"{tools.manifest.get('seed')}, "
                       f"generator v{tools.manifest.get('generator_version')}")
            for skip in tools.manifest.get("skipped") or []:
                stage.note(f"                library itself is INCOMPLETE: "
                           f"{skip['what']} — {skip['reason']}")
    else:
        stage.note("manifest        none — expectation checks will be skipped")

    weights = list((REPO / "models").rglob("*.onnx"))
    tools.model_weights = len(weights)
    runtime_pkg = REPO / "workers/ml-runtime/memory_engine_ml_runtime"
    tools.ml_runtime = runtime_pkg.is_dir() and any(runtime_pkg.glob("*.py"))
    stage.note(f"model weights   {len(weights)} .onnx files under models/")
    stage.note(f"ml-runtime      {'present' if tools.ml_runtime else 'not implemented yet'}")

    run.report(stage)
    return tools


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("source", type=Path)
    parser.add_argument("--workdir", type=Path, default=Path("/tmp/memory-engine-demo"))
    parser.add_argument("--search", default="")
    parser.add_argument("--keep", action="store_true", help="reuse an existing workdir")
    parser.add_argument("--ffmpeg", default=os.environ.get("MEMORY_ENGINE_FFMPEG", "ffmpeg"))
    parser.add_argument("--skip-video", action="store_true",
                        help="do not generate video proxies (reported as SKIPPED)")
    parser.add_argument("--i-know-this-is-my-real-library", action="store_true",
                        dest="allow_real",
                        help="scan a real photo folder anyway")
    parser.add_argument("--ml-runtime", default=os.environ.get("MEMORY_ENGINE_ML_RUNTIME"),
                        help="HOST:PORT of a running workers/ml-runtime. Without it "
                             "stages 13-15 are SKIPPED: analysis is a hard gate, so "
                             "no album, PDF or reel can be produced, and this script "
                             "will say so rather than pretending")
    parser.add_argument("--album-photos", type=int, default=24,
                        help="how many photos the album is asked for")
    parser.add_argument("--reel-seconds", type=float, default=15.0,
                        help="how long the reel is asked to be")
    args = parser.parse_args(argv)

    source = args.source.expanduser().resolve()
    if not source.is_dir():
        raise SystemExit(f"not a directory: {source}")

    reason = real_media_location(source)
    if reason is not None and not args.allow_real:
        raise SystemExit(
            f"refusing to scan {source}: {reason}.\n"
            "  the demo runs on a generated library:\n"
            "    python3 scripts/demo/make_library.py --out /tmp/demo-library\n"
            "  pass --i-know-this-is-my-real-library to override."
        )

    workdir = args.workdir.expanduser().resolve()
    if workdir.exists() and not args.keep:
        shutil.rmtree(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    records_dir = workdir / "records"

    print(f"\n{RULE}\n  MEMORY ENGINE — demo pipeline\n{RULE}")
    print(f"  source   {source}")
    print(f"  workdir  {workdir}")

    run = Run(total=15)
    tools = preflight(run, source, args.ffmpeg)

    records = stage_ingest(run, tools, source, workdir, records_dir)
    stage_manifest(run, tools, source, records)
    stage_spans(run, tools, source, records)
    database = stage_store(run, records, workdir, args.search)
    groups = stage_dedupe(run, tools, source, records, database)
    stage_chronology(run, records, database)
    records = stage_proxies(run, tools, args, source, records, workdir,
                            records_dir, database)
    stage_corrupt_loudly(run, tools, args, source, records, workdir, records_dir)
    stage_clustering(run, records)
    stage_album(run, tools, records)
    stage_reel(run, tools, records)

    # 13-15: the product itself, and then its output opened rather than listed.
    pipeline_report = stage_pipeline(run, tools, args, source, workdir)
    stage_verify_pdf(run, pipeline_report)
    stage_verify_video(run, tools, pipeline_report)

    if database is not None:
        summarise(database, groups, args.search)
        database.close()
    return run.ledger()


# ---------------------------------------------------------------------------
# Stage 2: ingest
# ---------------------------------------------------------------------------


def stage_ingest(run: Run, tools: Tools, source: Path, workdir: Path,
                 records_dir: Path) -> dict[str, dict]:
    stage = run.stage("ingest — walk, hash, EXIF, pHash, thumbnails")
    if not tools.ingest:
        stage.skip(
            reason=f"the ingest binary is not built ({INGEST_BIN} missing)",
            consequence="nothing at all -- every later stage has no records to work on",
            remedy=f"cd {INGEST_SRC} && cargo build --release",
        )
        run.report(stage)
        return {}
    if not tools.blake3:
        stage.skip(
            reason="the blake3 module is missing, so the JobSpec locator digest "
                   "cannot be computed and ingest would refuse the job",
            consequence="nothing at all -- every later stage has no records",
            remedy="pip install blake3",
        )
        run.report(stage)
        return {}

    started = time.time()
    code, report, message = run_ingest(
        scan_job(source), workdir, records_dir, "scan", None
    )
    if code != 0 or report is None:
        stage.fail(f"ingest exited {code}: {message[:400]}")
        run.report(stage)
        return {}

    records = load_records(records_dir)
    stage.note(f"{report.get('processed', 0)} files processed in "
               f"{time.time() - started:.1f}s")
    # Print EVERY field the report carries. The previous version of this script
    # looked up three key names, two of which ScanReport does not have, so
    # assemblies_created and span_members_updated never appeared -- which is
    # precisely how a stale binary reporting zero assemblies went unnoticed.
    for key in sorted(report):
        if key == "issues":
            continue
        stage.note(f"{key:<22} {report[key]}")
    issues = report.get("issues") or []
    stage.note(f"{'issues':<22} {len(issues)}")
    for issue in issues[:10]:
        stage.note(f"    {issue.get('code')}: {issue.get('message')}")

    stage.check("scan completed (not a partial, resumable yield)",
                bool(report.get("complete")),
                "the job yielded early; records are incomplete")
    stage.check("at least one record was produced", bool(records))

    physical = sum(1 for r in records.values() if r["asset_kind"] == "physical_file")
    virtual = len(records) - physical
    stage.note(f"{'records on disk':<22} {physical} physical + {virtual} virtual assemblies")
    run.report(stage)
    return records


# ---------------------------------------------------------------------------
# Stage 3: declared expectations
# ---------------------------------------------------------------------------


def stage_manifest(run: Run, tools: Tools, source: Path,
                   records: dict[str, dict]) -> None:
    stage = run.stage("library manifest — declared vs actual")
    manifest = tools.manifest
    if manifest is None:
        stage.skip(
            reason="the source folder has no MANIFEST.json",
            consequence="every number this demo prints is unchecked; nothing "
                        "confirms dedupe, quarantine or dating did the right thing",
            remedy="python3 scripts/demo/make_library.py --out <dir>",
        )
        run.report(stage)
        return
    if not records:
        stage.skip(
            reason="ingest produced no records",
            consequence="the declared expectations were never tested",
        )
        run.report(stage)
        return

    files = [f for f in manifest["files"] if f.get("generated", True)]
    by_id = {f["blake3"]: f for f in files if f.get("blake3")}
    expectations = manifest.get("expectations", {})

    # 1. identity: the manifest's BLAKE3 is the media_id, so every declared
    #    file must be present as a record under exactly that id.
    missing = [f["relpath"] for f in files if f.get("blake3") and f["blake3"] not in records]
    stage.check(
        f"all {len(by_id)} declared files ingested under their content hash",
        not missing,
        f"missing: {', '.join(sorted(missing)[:5])}"
        + (f" (+{len(missing) - 5} more)" if len(missing) > 5 else ""),
    )

    # 2. nothing extra: a record with no manifest entry means the scan picked
    #    up something the generator did not write.
    manifest_paths = {f["relpath"] for f in files} | {"MANIFEST.json"}
    unexpected = sorted(
        relpath for record in records.values()
        if record["asset_kind"] == "physical_file"
        and (relpath := relpath_of(record, source)) is not None
        and relpath not in manifest_paths
    )
    stage.check("no records for files the manifest does not declare",
                not unexpected, f"unexpected: {', '.join(unexpected[:5])}")

    # 3. quarantine, per declared file.
    for entry in expectations.get("must_quarantine", []):
        record = _record_for(records, source, entry["relpath"])
        if record is None:
            stage.check(f"quarantined: {entry['relpath']}", False, "no record found")
            continue
        state = record["processing"]["state"]
        reasons = (record.get("exclusion") or {}).get("reasons") or []
        stage.check(
            f"quarantined: {entry['relpath']}",
            state == "quarantined",
            f"expected quarantine ({entry['why']}) but the record is "
            f"state={state}, kind={record['kind']}, "
            f"format={record.get('file_format')}, exclusion={reasons}",
            attribution=entry.get("known_gap", ""),
        )

    # 4. dating.
    for relpath in expectations.get("unknown_capture_time", []):
        record = _record_for(records, source, relpath)
        precision = (record or {}).get("capture", {}).get("captured_at", {}).get("precision")
        stage.check(
            f"undated: {relpath}", precision == "unknown",
            f"precision={precision!r}; a file with no EXIF date and no date in "
            "its name must not acquire one",
        )
    for relpath in expectations.get("filename_dated", []):
        record = _record_for(records, source, relpath)
        assertion = (record or {}).get("capture", {}).get("captured_at", {})
        stage.check(
            f"dated from filename: {relpath}",
            assertion.get("source") == "filename_pattern" and assertion.get("local"),
            f"source={assertion.get('source')!r} local={assertion.get('local')!r}",
        )

    # 5. EXIF beats the filesystem. This is the one that reorders a library if
    #    it is wrong, and it is invisible unless checked: an mtime-derived date
    #    looks like a perfectly ordinary date.
    for entry in expectations.get("exif_mtime_disagreement", []):
        record = _record_for(records, source, entry["relpath"])
        assertion = (record or {}).get("capture", {}).get("captured_at", {})
        want = entry["exif_datetime_original"].replace(":", "-", 2).replace(" ", "T")
        stage.check(
            f"EXIF beats mtime: {entry['relpath']}",
            assertion.get("local") == want
            and assertion.get("source") == "exif_datetime_original",
            f"EXIF says {want}, the filesystem says "
            f"{entry['filesystem_mtime_utc']}, the record says "
            f"{assertion.get('local')!r} from {assertion.get('source')!r}",
        )

    # 6. orientation: stored landscape, displayed portrait.
    for relpath in expectations.get("orientation_6", []):
        record = _record_for(records, source, relpath)
        image = (record or {}).get("image") or {}
        stored, oriented = image.get("stored_size"), image.get("oriented_size")
        ok = bool(stored and oriented
                  and stored["width"] == oriented["height"]
                  and stored["height"] == oriented["width"])
        stage.check(f"orientation 6 transposes: {relpath}", ok,
                    f"stored={stored} oriented={oriented}")

    run.report(stage)


def _record_for(records: dict[str, dict], source: Path, relpath: str) -> dict | None:
    for record in records.values():
        if relpath_of(record, source) == relpath:
            return record
    return None


# ---------------------------------------------------------------------------
# Stage 4: GoPro spans
# ---------------------------------------------------------------------------


def stage_spans(run: Run, tools: Tools, source: Path,
                records: dict[str, dict]) -> None:
    stage = run.stage("GoPro spans — chaptered video assembly")
    manifest = tools.manifest
    if manifest is None:
        stage.skip(
            reason="no MANIFEST.json, so no span is declared to check against",
            consequence="chaptered-video assembly is untested",
            remedy="python3 scripts/demo/make_library.py --out <dir>",
        )
        run.report(stage)
        return
    declared = [s for s in manifest.get("expectations", {}).get("gopro_spans", [])
                if s.get("generated") and s.get("members")]
    if not declared:
        stage.skip(
            reason="the library declares no GoPro spans (clips were not generated)",
            consequence="chaptered-video assembly is untested",
            remedy="regenerate the library with ffmpeg available",
        )
        run.report(stage)
        return

    assemblies = [r for r in records.values() if r["asset_kind"] == "virtual_assembly"]
    stage.note(f"{len(assemblies)} virtual assemblies for {len(declared)} declared spans")

    for span in declared:
        want_ids = list(span["member_blake3"])
        label = f"{span['family']}/{span['recording']}"
        match = next(
            (a for a in assemblies
             if list((a.get("span") or {}).get("member_media_ids") or []) == want_ids),
            None,
        )
        if match is None:
            got = [
                [source_name(records[m]) for m in
                 ((a.get("span") or {}).get("member_media_ids") or []) if m in records]
                for a in assemblies
            ]
            stage.check(
                f"span assembled in order: {label}", False,
                f"expected {[Path(m).name for m in span['members']]}, "
                f"assemblies present: {got}",
            )
            continue
        stage.check(f"span assembled in order: {label}", True)

        # Member index convention. GX/GH count from 01 -> index 0; the legacy
        # family puts GOPR at index 0 and GP01 at index 1. Getting the second
        # wrong yields a span with no index 0, which reads as "the first
        # chapter is missing from disk" rather than as a parser bug.
        indexes = [(records[m].get("span") or {}).get("index")
                   for m in want_ids if m in records]
        stage.check(
            f"member indexes {span['expected_indexes']}: {label}",
            indexes == span["expected_indexes"],
            f"got {indexes}",
        )
        continuity = (match.get("span") or {}).get("continuity")
        stage.note(
            f"{label}: continuity={continuity}"
            + (" (expected on a first scan -- the worker has no video duration "
               "or timecode until the proxy stage runs, so it cannot yet certify "
               "the span is gapless)" if continuity == "incomplete_set" else "")
        )

    run.report(stage)


# ---------------------------------------------------------------------------
# Stage 5: media-db
# ---------------------------------------------------------------------------


def stage_store(run: Run, records: dict[str, dict], workdir: Path, search: str):
    stage = run.stage("media-db — store, index, full-text search")
    if not records:
        stage.skip(reason="no records to store",
                   consequence="the schema, migrations and FTS index are untested")
        run.report(stage)
        return None
    try:
        from memory_engine_media_db import Database
    except ImportError as error:
        stage.skip(reason=f"packages/media-db will not import: {error}",
                   consequence="storage, indexing and search are untested")
        run.report(stage)
        return None

    database = Database.open(workdir / "library.db")
    for record in records.values():
        database.put_media(record)
    stage.note(f"{database.count_media()} records stored, schema v{database.schema_version}")
    stage.note(f"vector backend  {database.vectors.backend}")
    if database.vectors.backend != "sqlite_vec":
        # Not a failure -- brute force is correct, just slow. It is called out
        # because "the vector index works" is a claim a demo could otherwise
        # imply while never touching sqlite-vec.
        stage.note("                sqlite-vec is NOT loaded; similarity falls back "
                   "to brute force, so nothing here exercises the vector index")
    stage.check("every record round-trips out of the database",
                all(database.get_media(m) is not None for m in records))
    if search:
        stage.note(f"search {search!r}  {len(database.search(search, limit=50))} hits")
    run.report(stage)
    return database


# ---------------------------------------------------------------------------
# Stage 6: dedupe
# ---------------------------------------------------------------------------


def stage_dedupe(run: Run, tools: Tools, source: Path,
                 records: dict[str, dict], database) -> list:
    stage = run.stage("dedupe — near-duplicate grouping")
    if not records:
        stage.skip(reason="no records", consequence="dedupe is untested")
        run.report(stage)
        return []
    try:
        from memory_engine_ranking import Candidate, assignments, find_duplicates
    except ImportError as error:
        stage.skip(reason=f"packages/ranking-engine will not import: {error}",
                   consequence="near-duplicate grouping is untested")
        run.report(stage)
        return []

    candidates = [
        Candidate(
            media_id=record["media_id"],
            phash_hex=((record.get("perceptual") or {}).get("image_hash") or {}).get("hex"),
            phash_bits=((record.get("perceptual") or {}).get("image_hash") or {}).get("bits", 64),
            embedding=None,  # no models in the demo path; pHash decides alone
            quality=0.0,
            captured_utc=record["capture"]["captured_at"].get("utc"),
            favorite=(record.get("user") or {}).get("favorite", False),
        )
        for record in records.values() if record.get("perceptual")
    ]
    groups = find_duplicates(candidates)
    if database is not None:
        for media_id, update in assignments(groups).items():
            record = database.get_media(media_id)
            if record:
                record["dedupe"] = update
                database.put_media(record)
    stage.note(f"{len(candidates)} candidates with a pHash")
    stage.note(f"{len(groups)} groups covering {sum(g.size for g in groups)} files")
    stage.note("no embeddings available, so grouping uses the DECISIVE Hamming "
               "threshold only (4 bits) -- the generous 10-bit path is untested here")

    manifest = tools.manifest
    if manifest is None:
        stage.note("no manifest: the groups above are unchecked")
    else:
        declared = {
            frozenset(entry["member_blake3"]): entry["burst_id"]
            for entry in manifest["expectations"].get("near_duplicate_bursts", [])
        }
        found = {frozenset(group.members): group for group in groups}
        for members, burst in sorted(declared.items(), key=lambda kv: kv[1]):
            stage.check(
                f"burst grouped exactly: {burst}", members in found,
                f"{len(members)} declared frames did not come back as one group",
            )
        # An UNDECLARED group is a false merge: two pictures the library says
        # are different, silently collapsed into one, with the loser dropped
        # from every automated output. It is the failure direction that cannot
        # be undone, so it is checked as hard as the missing-group direction.
        extra = [g for members, g in found.items() if members not in declared]
        stage.check(
            "no groups beyond the declared bursts", not extra,
            "; ".join(
                f"{[source_name(records[m]) for m in g.members]} ({g.method})"
                for g in extra[:4]
            ),
        )
    run.report(stage)
    return groups


# ---------------------------------------------------------------------------
# Stage 7: capture time
# ---------------------------------------------------------------------------


def stage_chronology(run: Run, records: dict[str, dict], database) -> None:
    stage = run.stage("capture time — dates and the timeline")
    if not records or database is None:
        stage.skip(reason="no records in the database",
                   consequence="dating and timeline ordering are untested")
        run.report(stage)
        return

    histogram: dict[str, int] = {}
    with_utc = 0
    with_local = 0
    for record in records.values():
        assertion = record["capture"]["captured_at"]
        histogram[assertion.get("precision", "unknown")] = (
            histogram.get(assertion.get("precision", "unknown"), 0) + 1
        )
        with_utc += bool(assertion.get("utc"))
        with_local += bool(assertion.get("local"))
    for precision, count in sorted(histogram.items()):
        stage.note(f"precision {precision:<10} {count}")
    stage.note(f"with a local wall-clock reading   {with_local}")
    stage.note(f"with a resolved UTC instant       {with_utc}")

    timeline = database.list_media(chronological=True, limit=100000)
    stage.note(f"orderable on the timeline         {len(timeline)}")

    # This is the finding the demo exists to surface. `list_media(chronological)`
    # requires captured_utc, and nothing in the repo ever writes it: the ingest
    # worker records the EXIF wall-clock reading in `local` and leaves `utc`
    # None, and there is no timezone-resolution stage. So a library can be
    # fully dated and still have an empty timeline -- and the shape of that
    # failure is an empty list, which reads like "no photos matched".
    stage.check(
        "records with a wall-clock date are orderable on the timeline",
        not (with_local and not timeline),
        f"{with_local} records carry an EXIF date but {len(timeline)} are "
        "orderable: media-db orders on capture.captured_at.utc, which nothing "
        "in the pipeline populates",
        attribution="no timezone-resolution stage exists; "
                    "workers/ingest/src/metadata.rs writes utc: None always",
    )
    run.report(stage)


# ---------------------------------------------------------------------------
# Stage 8: video proxies
# ---------------------------------------------------------------------------


def healthy_videos(records: dict[str, dict], source: Path,
                   manifest: dict | None) -> list[str]:
    broken = set()
    if manifest:
        broken = {
            entry["relpath"]
            for entry in manifest["expectations"].get("must_fail_at_proxy", [])
        }
    out = []
    for media_id, record in sorted(records.items()):
        if record["kind"] != "video" or record["asset_kind"] != "physical_file":
            continue
        if relpath_of(record, source) in broken:
            continue
        out.append(media_id)
    return out


def stage_proxies(run: Run, tools: Tools, args, source: Path,
                  records: dict[str, dict], workdir: Path, records_dir: Path,
                  database) -> dict[str, dict]:
    stage = run.stage("video proxies — 480p + frame index")
    videos = healthy_videos(records, source, tools.manifest)

    if args.skip_video:
        stage.skip(reason="--skip-video was passed",
                   consequence="the proxy pipeline, hardware decode and the "
                               "frame-index sidecar are all untested")
    elif not videos:
        stage.skip(reason="the library has no healthy video",
                   consequence="the proxy pipeline is untested",
                   remedy="regenerate the library with ffmpeg available")
    elif tools.ffmpeg is None:
        stage.skip(reason=f"ffmpeg not found (looked for {args.ffmpeg!r})",
                   consequence="the proxy pipeline is untested",
                   remedy="install ffmpeg, or pass --ffmpeg /path/to/ffmpeg")
    elif not tools.hardware:
        # `video.rs` refuses rather than falling back to software, on purpose:
        # a silent software fallback turns a 200-hour overnight pass into a
        # week. So this is a real skip, not a slow path.
        stage.skip(reason=f"hardware decode unavailable — {tools.hardware_detail}",
                   consequence="no proxies, so nothing downstream that needs "
                               "frames (moments, reels, span continuity) can run",
                   remedy="the ingest worker requires hardware decode and has no "
                          "software fallback by design; run on a supported host")
    else:
        code, report, message = run_ingest(
            proxy_job(videos, "healthy"), workdir, records_dir, "proxy", tools.ffmpeg
        )
        if code != 0 or report is None:
            stage.fail(f"proxy job exited {code}: {message[:400]}")
        else:
            for key in sorted(report):
                if key != "issues":
                    stage.note(f"{key:<22} {report[key]}")
            stage.check("every healthy video got a proxy",
                        report.get("processed", 0) == len(videos),
                        f"{report.get('processed')} of {len(videos)}")
            stage.check("the proxy job reported complete", bool(report.get("complete")))
            records = load_records(records_dir)
            if database is not None:
                for media_id in videos:
                    if media_id in records:
                        database.put_media(records[media_id])
            sidecars = sum(
                1 for media_id in videos
                for proxy in (records.get(media_id, {}).get("proxies") or [])
                if proxy.get("kind") == "video_proxy_480p" and proxy.get("frame_index")
            )
            stage.check("every proxy carries a frame-index sidecar",
                        sidecars == len(videos), f"{sidecars} of {len(videos)}")
    run.report(stage)
    return records


# ---------------------------------------------------------------------------
# Stage 9: corrupt media must fail loudly
# ---------------------------------------------------------------------------


def stage_corrupt_loudly(run: Run, tools: Tools, args, source: Path,
                         records: dict[str, dict], workdir: Path,
                         records_dir: Path) -> None:
    stage = run.stage("corrupt media — must fail loudly, not silently")
    manifest = tools.manifest
    declared = (manifest or {}).get("expectations", {}).get("must_fail_at_proxy", [])
    if manifest is None:
        stage.skip(reason="no MANIFEST.json declaring a file that must fail",
                   consequence="nothing confirms a corrupt file is rejected "
                               "rather than quietly skipped",
                   remedy="python3 scripts/demo/make_library.py --out <dir>")
        run.report(stage)
        return
    if not declared:
        stage.skip(reason="the library declares no must-fail file",
                   consequence="loud failure on corrupt media is untested",
                   remedy="regenerate the library with ffmpeg available")
        run.report(stage)
        return
    if args.skip_video or tools.ffmpeg is None or not tools.hardware:
        stage.skip(reason="the proxy stage did not run, and the corrupt file "
                          "only fails there",
                   consequence="loud failure on corrupt media is untested")
        run.report(stage)
        return

    for entry in declared:
        record = _record_for(records, source, entry["relpath"])
        if record is None:
            stage.check(f"fails loudly: {entry['relpath']}", False, "no record")
            continue
        media_id = record["media_id"]
        code, report, message = run_ingest(
            proxy_job([media_id], f"corrupt-{media_id[:8]}"),
            workdir, records_dir, f"corrupt-{media_id[:8]}", tools.ffmpeg,
        )
        # A non-zero exit is the PASS here. Success would mean FFmpeg produced
        # a proxy from 8 KB of header, which would mean the truncation went
        # unnoticed and a broken file entered the library looking whole.
        stage.check(
            f"fails loudly: {entry['relpath']}", code != 0,
            f"the proxy job exited 0 for a file that is {entry['why']}; "
            "a corrupt source produced a proxy",
        )
        if code != 0:
            checkpoint = workdir / f"checkpoint-corrupt-{media_id[:8]}.json"
            error = {}
            if checkpoint.is_file():
                error = json.loads(checkpoint.read_text(encoding="utf-8")).get("error") or {}
            stage.note(f"{entry['relpath']}: exit {code}, "
                       f"code={error.get('code')}, "
                       f"failed_input_id={(error.get('failed_input_id') or '')[:12]}")
            stage.check(
                f"the failure names the file: {entry['relpath']}",
                error.get("failed_input_id") == media_id,
                f"error block records {error.get('failed_input_id')!r}; without "
                "it a resumed job cannot tell which input poisoned the batch",
            )
    run.report(stage)


# ---------------------------------------------------------------------------
# Stage 10: event clustering
# ---------------------------------------------------------------------------


def stage_clustering(run: Run, records: dict[str, dict]) -> None:
    stage = run.stage("event clustering — which photos are one event")
    if not records:
        stage.skip(reason="no records", consequence="clustering is untested")
        run.report(stage)
        return
    try:
        from memory_engine_album.clustering import EventItem, cluster_events
    except ImportError as error:
        stage.skip(reason=f"packages/album-engine will not import: {error}",
                   consequence="event clustering is untested")
        run.report(stage)
        return

    # EventItem.captured_at is an INSTANT in unix seconds, and the module is
    # explicit that a wall-clock reading passed here reorders any library that
    # crosses a timezone. So this stage is gated on real instants and refuses
    # to manufacture them -- a demo that fabricates its own input proves only
    # that the fabrication ran.
    datable = [
        record for record in records.values()
        if (record["capture"]["captured_at"].get("utc")
            and record["capture"]["captured_at"].get("precision")
            in ("second", "minute", "hour", "day"))
    ]
    if not datable:
        with_local = sum(
            1 for r in records.values() if r["capture"]["captured_at"].get("local")
        )
        stage.skip(
            reason=f"no record carries a UTC instant ({with_local} carry a local "
                   "wall-clock reading with no zone)",
            consequence="event clustering, and therefore every album boundary, "
                        "is untested on ingested data",
            remedy="a timezone-resolution stage has to turn "
                   "capture.captured_at.local into .utc; nothing writes that "
                   "field today (workers/ingest/src/metadata.rs sets utc: None)",
        )
        run.report(stage)
        return

    from datetime import datetime

    items = []
    for record in datable:
        assertion = record["capture"]["captured_at"]
        instant = datetime.fromisoformat(assertion["utc"].replace("Z", "+00:00"))
        items.append(EventItem(
            media_id=record["media_id"],
            captured_at=instant.timestamp(),
            time_precision=assertion["precision"],
            time_confidence=assertion.get("confidence", 1.0),
            time_source=assertion.get("source"),
            utc_offset_minutes=assertion.get("utc_offset_minutes"),
            latitude=((record["capture"].get("gps") or {}).get("latitude")),
            longitude=((record["capture"].get("gps") or {}).get("longitude")),
        ))
    clusters = cluster_events(items)
    stage.note(f"{len(items)} datable items -> {len(clusters)} events")
    for cluster in clusters[:8]:
        stage.note(f"  {cluster.kind:<8} {cluster.size:>4} items  {cluster.cluster_id[:8]}")
    stage.check("every item landed in exactly one cluster",
                sum(c.size for c in clusters) == len(items),
                f"{sum(c.size for c in clusters)} placed of {len(items)}")
    run.report(stage)


# ---------------------------------------------------------------------------
# Stage 11: the album path
# ---------------------------------------------------------------------------


def stage_album(run: Run, tools: Tools, records: dict[str, dict]) -> None:
    stage = run.stage("album — quality fusion, selection, layout, print gate")
    scored = [
        record for record in records.values()
        if ((record.get("quality") or {}).get("sharpness")
            and (record.get("quality") or {}).get("exposure"))
    ]
    if not scored:
        stage.skip(
            reason="no record carries quality signals: "
                   f"{tools.model_weights} model weights are installed, "
                   f"workers/ml-runtime is "
                   f"{'present' if tools.ml_runtime else 'not implemented'}, and "
                   "the classical_quality stage declared in models/registry.json "
                   "has no implementation anywhere in the repo",
            consequence="score fusion, photo selection, page layout and the "
                        "print validator are all untested on ingested data -- "
                        "which is the entire album product",
            remedy="implement classical_quality (sharpness/exposure need no "
                   "model) or stand up workers/ml-runtime with the Tier 1 stack",
        )
        run.report(stage)
        return

    try:
        # `signals_from_media_record` is not re-exported from the package
        # __init__, so it has to come from the submodule.
        from memory_engine_ranking.fusion import (
            IncomparableScores, fuse, rank, signals_from_media_record,
        )
        from memory_engine_album.selection import candidate_from_media_record, select
    except ImportError as error:
        stage.skip(reason=f"the album path will not import: {error}",
                   consequence="score fusion and photo selection are untested")
        run.report(stage)
        return

    fused = {record["media_id"]: fuse(signals_from_media_record(record))
             for record in scored}
    live = sum(1 for score in fused.values() if not score.rejected)
    stage.note(f"{len(fused)} records scored, {len(fused) - live} eliminated")

    # rank() refuses by default to order scores measured on different signal
    # sets. Refusing is the correct behaviour, so it is reported as a finding
    # about the library rather than crashing the demo.
    try:
        order = rank(fused)
        stage.note(f"ranked {len(order)} records best-first")
        stage.check("ranking returned every scored record", len(order) == len(fused),
                    f"{len(order)} of {len(fused)}")
    except IncomparableScores as error:
        stage.check(
            "all scores are comparable with each other", False,
            f"{error}; a mid-scan library measures different photos on "
            "different signal sets, and ranking across them is what silently "
            "reshuffles an album when analysis finishes",
        )

    candidates = [
        candidate_from_media_record(record, fused[record["media_id"]])
        for record in scored
    ]
    target = max(1, min(40, len(candidates) // 4))
    selection = select(candidates, target)
    stage.note(f"selected {len(selection.selected)} of {selection.candidate_count} "
               f"for a {target}-photo album")
    # Selecting FEWER than asked is legitimate -- the quality floor and the
    # per-person cap both cut -- so the check is that it never exceeds the ask
    # and never invents an id.
    stage.check("selection never exceeds the requested count",
                len(selection.selected) <= target,
                f"{len(selection.selected)} for a target of {target}")
    known = {candidate.media_id for candidate in candidates}
    stage.check("selection drew only from the candidates given",
                set(selection.selected) <= known,
                f"unknown ids: {sorted(set(selection.selected) - known)[:3]}")
    stage.check("no required person is missing from the selection",
                not selection.missing_person_ids,
                f"missing {list(selection.missing_person_ids)[:5]}")

    # Layout and the print validator take an AlbumSpec and a vendor profile,
    # which this script does not build. Saying so is not the same as saying a
    # dependency is missing: nothing is missing, the demo simply stops here.
    stage.not_wired(
        reason="layout and the print validator are not wired into this demo; "
               "they need a vendor profile and an AlbumSpec that nothing here "
               "constructs",
        consequence="page layout, the DPI floor, face-in-trim-zone and bleed "
                    "gates -- the checks that decide whether a printed book is "
                    "correct -- are not exercised",
    )
    run.report(stage)


# ---------------------------------------------------------------------------
# Stage 12: reel
# ---------------------------------------------------------------------------


def stage_reel(run: Run, tools: Tools, records: dict[str, dict]) -> None:
    stage = run.stage("reel — moment scoring and EDL")
    videos = [r for r in records.values() if r["kind"] == "video"]
    stage.skip(
        reason=f"{len(videos)} video records exist but no MomentRecord does; "
               "moment scoring consumes per-frame feature streams (motion, "
               "faces, speech, audio level) from workers/ml-runtime, which is "
               f"{'present but unwired' if tools.ml_runtime else 'not implemented'}",
        consequence="moment scoring, the reel planner, beat locking and EDL "
                    "generation are untested end to end; only their unit tests "
                    "speak for them",
        remedy="stand up workers/ml-runtime and a video-analysis pass that "
               "emits MomentRecords into media-db",
    )
    run.report(stage)


# ---------------------------------------------------------------------------
# Stage 13: the finished artifacts
# ---------------------------------------------------------------------------
#
# Stages 2-12 above walk the library and check the parts. This one runs the
# actual product -- `services/pipeline`, the same code path `python -m
# memory_engine_pipeline` runs -- and then stages 14 and 15 OPEN what it wrote.
#
# Opening them is the entire point. Every artifact check this repo had before
# today was a check on a filename or a file size: the end-to-end test asserted
# `%PDF` and "bigger than 100kB", and passed for months over a renderer that
# sheared every page and washed every photo out to near-white. A pipeline that
# writes a plausible file is the failure mode this project keeps finding, so a
# stage that reports "wrote 22 pages" without counting them is not evidence.


def stage_pipeline(run: Run, tools: Tools, args, source: Path, workdir: Path):
    """Run the real pipeline. Returns its RunReport, or None."""
    stage = run.stage("pipeline — album, reel, and the renders")
    if not args.ml_runtime:
        stage.skip(
            reason="no model host endpoint was given (--ml-runtime HOST:PORT)",
            consequence="the album, the print gate, the PDF and the reel are all "
                        "unproven: analysis is a hard gate and refuses to run "
                        "without a host, so nothing downstream of it executes",
            remedy="start workers/ml-runtime and pass --ml-runtime 127.0.0.1:50051",
        )
        run.report(stage)
        return None
    try:
        from memory_engine_pipeline.runner import run_pipeline  # noqa: PLC0415
        from memory_engine_pipeline.stages.base import Settings, StageStatus  # noqa: PLC0415
    except ImportError as error:
        stage.skip(reason=f"services/pipeline will not import: {error}",
                   consequence="no finished artifact is produced or checked")
        run.report(stage)
        return None

    started = time.time()
    report = run_pipeline(
        [source],
        workdir / "pipeline",
        settings=Settings(
            ml_runtime_endpoint=args.ml_runtime,
            album_target_count=args.album_photos,
            reel_seconds=args.reel_seconds,
        ),
    )
    stage.note(f"{len(report.results)} stages in {time.time() - started:.1f}s")
    for result in report.results:
        stage.note(f"{result.stage:<14} {result.status.value:<12} {result.detail}")

    # A blocked stage is NOT a demo failure -- a machine without SigLIP weights
    # genuinely cannot analyse photos, and saying so is the correct behaviour.
    # It is reported as a skip with the blocker named, so the ledger says which
    # of the three artifacts this machine could not make and why.
    blocked = [r for r in report.results if r.status is not StageStatus.COMPLETED
               and r.status is not StageStatus.SKIPPED]
    if blocked:
        stage.skip(
            reason="; ".join(f"{r.stage}: {r.detail}" for r in blocked),
            consequence="the artifacts those stages would have produced do not "
                        "exist, and nothing below checks them",
            remedy="see the named blocker above",
        )
    stage.check("no stage failed outright",
                not any(r.status is StageStatus.FAILED for r in report.results),
                "a failed stage is a defect, unlike a blocked one")
    run.report(stage)
    return report


def _outputs(report, stage_name: str, suffix: str) -> list[Path]:
    if report is None:
        return []
    for result in report.results:
        if result.stage == stage_name:
            return [Path(p) for p in result.outputs if str(p).endswith(suffix)]
    return []


# ---------------------------------------------------------------------------
# Stage 14: open the PDF
# ---------------------------------------------------------------------------


def stage_verify_pdf(run: Run, report) -> None:
    stage = run.stage("print artifact — opened and measured")
    pdfs = _outputs(report, "render-print", ".pdf")
    if not pdfs:
        stage.skip(
            reason="the pipeline produced no PDF (see the stage above for why)",
            consequence="page count, the PDF/X output intent, the physical page "
                        "boxes and the placement geometry are all unchecked",
        )
        run.report(stage)
        return

    pdf = pdfs[0]
    raw = pdf.read_bytes()
    stage.note(f"{pdf.name}  {len(raw):,} bytes")

    specs = _outputs(report, "album", ".json")
    declared_pages = None
    if specs:
        spec = json.loads(specs[0].read_text())
        declared_pages = len(spec["pages"])
        stage.note(f"AlbumSpec declares {declared_pages} pages, "
                   f"validation {spec['validation']['status']} "
                   f"({spec['validation']['error_count']} errors, "
                   f"{spec['validation']['warning_count']} warnings)")

    stage.check("it is a PDF", raw[:4] == b"%PDF", f"starts with {raw[:8]!r}")

    # Page count from the file itself, two independent ways, then against the
    # plan. "The renderer said 22" is not a count.
    page_objects = len(re.findall(rb"/Type\s*/Page[^s]", raw))
    counts = {int(v) for v in re.findall(rb"/Count\s+(\d+)", raw)}
    stage.note(f"{page_objects} page objects, /Count {sorted(counts)}")
    stage.check("page objects and the page tree agree",
                counts == {page_objects},
                f"{page_objects} /Type /Page objects against /Count {sorted(counts)}")
    if declared_pages is not None:
        stage.check("the PDF has exactly the pages the AlbumSpec planned",
                    page_objects == declared_pages,
                    f"{page_objects} in the file, {declared_pages} in the plan")

    # PDF/X-4 is what the vendor profile asks for, and an OutputIntent is the
    # part that makes the colour numbers mean anything at a printer.
    intent = b"/S /GTS_PDFX" in raw
    profile = b"/DestOutputProfile" in raw
    version = re.findall(rb"/GTS_PDFXVersion\s*\(([^)]*)\)", raw)
    condition = re.findall(rb"/OutputConditionIdentifier\s*<([0-9A-Fa-f]+)>", raw)
    stage.check("it declares a PDF/X OutputIntent", intent)
    stage.check("the output intent embeds a destination profile", profile,
                "without the ICC bytes the intent names a condition nobody can "
                "reproduce")
    stage.check("the PDF/X version is stated", bool(version),
                f"found {version}")
    if condition:
        try:
            name = bytes.fromhex(condition[0].decode()).decode("utf-16-be").strip("﻿")
            stage.note(f"output condition: {name}")
        except (ValueError, UnicodeDecodeError):
            pass

    boxes = {key: set(re.findall(rb"/" + key + rb"\s*\[[^\]]*\]", raw))
             for key in (b"MediaBox", b"TrimBox", b"BleedBox")}
    for key, found in boxes.items():
        stage.note(f"{key.decode()}: {[b.decode() for b in sorted(found)]}")
    stage.check("every page carries a trim box",
                len(boxes[b"TrimBox"]) >= 1,
                "a PDF/X page with no TrimBox has not said where it gets cut")

    run.report(stage)


# ---------------------------------------------------------------------------
# Stage 15: probe the video
# ---------------------------------------------------------------------------


def stage_verify_video(run: Run, tools: Tools, report) -> None:
    """Open EVERY video the pipeline rendered, not just the first one.

    The story stage emits a reel and a film. Probing `videos[0]` would open the
    reel, print five ticks, and leave the film -- a longer file, cut by a
    different planner, and therefore the one more likely to be wrong -- entirely
    unopened while the stage still reported ok.
    """
    stage = run.stage("video artifacts — probed and sampled")
    videos = _outputs(report, "render-video", ".mp4")
    if not videos:
        stage.skip(
            reason="the pipeline produced no video (see the pipeline stage for why)",
            consequence="duration, frame rate, frame count and whether the file "
                        "is anything but black frames are all unchecked",
        )
        run.report(stage)
        return
    if not tools.ffmpeg:
        stage.skip(reason="ffmpeg/ffprobe is not on PATH",
                   consequence="the rendered video is not opened at all")
        run.report(stage)
        return

    # A rendered file is named for the `edl_id` it came from, so the plan and
    # its evidence are paired by identity rather than by position.
    plans = {
        path.stem: path
        for path in _outputs(report, "story", ".json")
        if not path.name.endswith(".sources.json")
    }
    for video in sorted(videos):
        _probe_video(run, stage, tools, video, plans.get(video.stem))
    run.report(stage)


def _probe_video(run: Run, stage, tools: Tools, video: Path, plan: Path | None) -> None:
    probe = tools.ffmpeg.replace("ffmpeg", "ffprobe")
    stage.note(f"{video.name}  {video.stat().st_size:,} bytes")

    def ffprobe(*extra: str) -> dict:
        out = subprocess.run(
            [probe, "-v", "error", *extra, "-of", "json", str(video)],
            capture_output=True, text=True, check=False,
        )
        return json.loads(out.stdout or "{}")

    meta = ffprobe("-show_entries",
                   "format=duration,size:stream=codec_name,codec_type,width,height,"
                   "r_frame_rate,nb_frames,sample_rate,channels")
    streams = meta.get("streams") or []
    fmt = meta.get("format") or {}
    video_streams = [s for s in streams if s.get("codec_type") == "video"]
    stage.check("the container holds a video stream", bool(video_streams))
    if not video_streams:
        return
    v = video_streams[0]
    duration = float(fmt.get("duration") or 0.0)
    stage.note(f"{v.get('codec_name')} {v.get('width')}x{v.get('height')} "
               f"@ {v.get('r_frame_rate')}  {duration:.3f}s")
    for a in (s for s in streams if s.get("codec_type") == "audio"):
        stage.note(f"audio: {a.get('codec_name')} {a.get('sample_rate')}Hz "
                   f"{a.get('channels')}ch")

    # The EDL is the claim; the file is the evidence. Decoding every frame is
    # what separates "the header says 225" from "there are 225".
    if plan is None:
        stage.note(f"{video.name} names no EDL this run produced; its cut list, "
                   "timeline rate and clip count are unchecked")
    else:
        edl = json.loads(plan.read_text())
        # A track holds `items`, and an item is a clip, a gap or a transition.
        # Counting items would count the absences too, and "a hard cut is the
        # absence of a Transition" is the convention here, so only clips count.
        clips = [item for track in (edl.get("tracks") or [])
                 if track.get("kind") == "video"
                 for item in (track.get("items") or [])
                 if item.get("item_type") == "clip"]
        stage.note(f"EDL {edl.get('edl_id','?')[:12]} kind={edl.get('kind')}: "
                   f"{len(clips)} clips, validation "
                   f"{(edl.get('validation') or {}).get('status')}")
        # The EDL's rate is a RationalTime; 30000/1001 has no exact float form,
        # which is why the contract stores it as a pair and why this compares
        # the pair rather than a rounded number.
        rate = edl.get("rate")
        if isinstance(rate, dict) and rate.get("rate"):
            stage.check("the container frame rate is the EDL's timeline rate",
                        abs(eval_rate(v.get("r_frame_rate")) - rate_value(rate)) < 0.01,
                        f"container {v.get('r_frame_rate')} vs EDL "
                        f"{rate.get('value')}/{rate.get('rate')}")
        # Every cut the EDL planned should be a boundary in the file. Reported
        # rather than asserted: two adjacent shots of the same scene genuinely
        # produce no measurable jump, and failing on that would be wrong.
        stage.note(f"the EDL claims {max(0, len(clips) - 1)} internal cuts; "
                   "boundaries are reported below, not asserted -- adjacent "
                   "shots of one scene need not differ")

    counted = ffprobe("-count_frames", "-select_streams", "v:0",
                      "-show_entries", "stream=nb_read_frames")
    read_frames = int(((counted.get("streams") or [{}])[0]).get("nb_read_frames") or 0)
    stage.note(f"decoded {read_frames} frames")
    stage.check("the file decodes to a non-empty run of frames", read_frames > 0)
    expected = round(duration * eval_rate(v.get("r_frame_rate")))
    stage.check("decoded frame count matches duration x rate",
                abs(read_frames - expected) <= 1,
                f"{read_frames} decoded, {expected} implied by "
                f"{duration:.3f}s at {v.get('r_frame_rate')}")

    # Not a run of black frames. This is the check that a renderer which
    # produced a correctly-sized, correctly-timed, entirely empty file fails.
    black = subprocess.run(
        [tools.ffmpeg, "-v", "info", "-i", str(video),
         "-vf", "blackdetect=d=0.1:pix_th=0.10", "-f", "null", "-"],
        capture_output=True, text=True, check=False,
    )
    hits = [line for line in black.stderr.splitlines() if "black_start" in line]
    for line in hits:
        stage.note(line.strip())
    stage.check("no black run of 0.1s or longer", not hits,
                f"{len(hits)} black run(s) detected")

    # And the picture actually changes: a still frame held for the whole
    # duration passes every check above.
    stats = subprocess.run(
        [probe, "-v", "error", "-f", "lavfi", "-i", f"movie={video},signalstats",
         "-show_entries", "frame_tags=lavfi.signalstats.YAVG",
         "-of", "csv=p=0"],
        capture_output=True, text=True, check=False,
    )
    values = []
    for line in stats.stdout.splitlines():
        field = line.split(",")[0].strip()
        if field:
            try:
                values.append(float(field))
            except ValueError:
                pass
    if values:
        stage.note(f"frame brightness YAVG min={min(values):.1f} "
                   f"max={max(values):.1f} over {len(values)} frames")
        stage.check("the picture is not a single held frame",
                    max(values) - min(values) > 1.0,
                    f"YAVG varies by only {max(values) - min(values):.2f}")


def eval_rate(text: str | None) -> float:
    if not text or "/" not in text:
        return 0.0
    num, den = text.split("/", 1)
    return float(num) / float(den) if float(den) else 0.0


def rate_value(rate: dict) -> float:
    return float(rate["value"]) / float(rate["rate"]) if rate.get("rate") else 0.0


# ---------------------------------------------------------------------------
# The human-readable summary
# ---------------------------------------------------------------------------


def summarise(database, groups: Iterable[Any], search: str) -> None:
    print(f"\n{RULE}\n  LIBRARY\n{RULE}")
    groups = list(groups)
    if groups:
        print("\n  DUPLICATE GROUPS — one primary kept, the rest suppressed\n")
        for group in groups[:6]:
            print(f"    group of {group.size}  ({group.method})")
            for member in group.members:
                record = database.get_media(member)
                name = source_name(record) if record else member[:12]
                mark = "KEEP  " if member == group.primary_media_id else "  dup "
                print(f"      {mark} {name}")
            print()
        if len(groups) > 6:
            print(f"    ... and {len(groups) - 6} more groups\n")
    else:
        print("\n  No near-duplicates found.\n")

    if search:
        print(f"  SEARCH — {search!r}")
        hits = database.search(search, limit=8)
        for hit in hits:
            record = database.get_media(hit.media_id)
            print(f"    {source_name(record)}")
        if not hits:
            print("    no matches")
        print()

    primaries = database.list_media(primaries_only=True, limit=100000)
    print(f"  {database.count_media()} records  ->  "
          f"{len(primaries)} after duplicate suppression")


if __name__ == "__main__":
    raise SystemExit(main())
