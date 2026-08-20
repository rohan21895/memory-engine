#!/usr/bin/env python3
"""The album flow, served locally: add photos -> select -> review -> album.

A working prototype of the desktop app's flow (issue #139), driven entirely
by artifacts the pipeline already writes:

  1. INTAKE: the user names a folder of photos; the app runs the pipeline on
     it (ingest, then a per-run local model host, then analysis through the
     album plan and a first render), streaming progress,
  2. REVIEW: the selected photos in page order; tapping one shows its
     shot-group alternatives with the engine's reasons, plus the best of what
     was omitted -- every photo has alternatives, drawn from the wider pool
     when its own shot has none,
  3. FINALIZE writes `<workdir>/album-review.json` (pinned ids, excluded
     ids), re-plans the album with those decisions and re-renders; the final
     PDF is served for download.

Localhost only, stdlib only, serves thumbnails straight from the workdir's
proxy store. Nothing leaves the machine.

Usage:
    python scripts/demo/review_album.py [workdir] [--port 4189]

With a workdir the app opens straight onto that run's review screen; without
one it opens on intake.
"""
from __future__ import annotations

import argparse
import json
import socket
import subprocess
import sys
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote

REVIEW_FILENAME = "album-review.json"
REPO_ROOT = Path(__file__).resolve().parents[2]
# The pipeline and classifier need the repo venv's packages; the server
# itself is stdlib-only and runs on whatever python launched it.
_VENV = REPO_ROOT / ".venv" / "bin" / "python"
PYTHON = str(_VENV) if _VENV.exists() else sys.executable
_MEDIA_SUFFIXES = {
    ".jpg", ".jpeg", ".png", ".heic", ".heif", ".tif", ".tiff", ".webp",
    ".dng", ".cr2", ".cr3", ".nef", ".arw", ".mp4", ".mov",
}
#: How many of the omitted pool to offer as alternatives per photo.
_POOL_LIMIT = 12


# --------------------------------------------------------------- run state --
class Flow:
    """One run's whole life, guarded by a lock: intake -> pipeline ->
    review -> finalize -> render -> done. One run at a time -- this is a
    single user's machine, not a job server."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        # intake|running|review|rendering|flagged|done|failed
        self.state = "intake"
        self.log: list[str] = []
        self.workdir: Path | None = None
        self.source: Path | None = None
        self.sidecar: dict | None = None
        self.thumbs: dict[str, Path] = {}
        self.pdf: Path | None = None
        self.pending_review: dict | None = None
        self.flagged_count = 0

    def say(self, line: str) -> None:
        with self.lock:
            self.log.append(line)
        print(line, flush=True)

    def fail(self, line: str) -> None:
        self.say(line)
        with self.lock:
            self.state = "failed"


FLOW = Flow()


def newest_sidecar(workdir: Path) -> Path | None:
    sidecars = sorted(
        (workdir / "outputs" / "selection").glob("*.json"),
        key=lambda p: p.stat().st_mtime,
    )
    return sidecars[-1] if sidecars else None


def newest_pdf(workdir: Path) -> Path | None:
    pdfs = sorted(
        (workdir / "outputs" / "pdf").glob("*.pdf"),
        key=lambda p: p.stat().st_mtime,
    )
    return pdfs[-1] if pdfs else None


def thumbnail_paths(workdir: Path) -> dict[str, Path]:
    """media_id -> thumbnail path, from the library's proxy table."""
    import sqlite3  # noqa: PLC0415

    db = sqlite3.connect(f"file:{workdir / 'library.db'}?mode=ro", uri=True)
    try:
        rows = db.execute(
            "SELECT media_id, path FROM media_proxy WHERE kind='thumbnail_512'"
        ).fetchall()
    finally:
        db.close()
    return {media_id: Path(path) for media_id, path in rows}


def _load_pose_map(workdir: Path) -> dict[str, str]:
    """media_id -> body-pose cluster, from the pose stage's workdir file.
    Empty when pose was not run: the UI then falls back to same-shot
    similarity alone."""
    path = workdir / "pose-clusters.json"
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def load_review_state(workdir: Path) -> bool:
    """Point the flow at an existing run's newest plan. True when one exists."""
    sidecar = newest_sidecar(workdir)
    if sidecar is None:
        return False
    with FLOW.lock:
        FLOW.workdir = workdir
        FLOW.sidecar = json.loads(sidecar.read_text(encoding="utf-8"))
        FLOW.thumbs = thumbnail_paths(workdir)
        FLOW.state = "review"
    return True


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _env(pythonpath: str) -> dict[str, str]:
    """The environment run-photeo.sh gives every engine process: model loads
    allowed in development mode, imports resolved by explicit PYTHONPATH so
    the working directory stops mattering."""
    import os  # noqa: PLC0415

    env = dict(os.environ)
    env["MEMORY_ENGINE_ALLOW_UNVERIFIED_MODELS"] = "1"
    env["PYTHONPATH"] = pythonpath
    return env


_PIPELINE_ENV = str(REPO_ROOT / "services" / "pipeline")
_HOST_ENV = f"{REPO_ROOT}:{REPO_ROOT / 'workers' / 'ml-runtime'}"


def _stream(cmd: list[str], cwd: Path, pythonpath: str = _PIPELINE_ENV) -> int:
    """Run a command, echoing each output line into the flow log."""
    process = subprocess.Popen(
        cmd, cwd=cwd, env=_env(pythonpath),
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    assert process.stdout is not None
    for line in process.stdout:
        line = line.rstrip()
        if line:
            FLOW.say("  " + line)
    return process.wait()


def run_pipeline_flow(source: Path, workdir: Path, photos: int) -> None:
    """Intake to review: ingest, per-run model host, analysis through render.

    The model host needs the library database for proxy resolution, and the
    database does not exist until ingest has run -- hence ingest first, host
    second, everything else third.
    """
    python = PYTHON
    host = None
    try:
        FLOW.say(f"Importing {source} ...")
        code = _stream(
            [python, "-m", "memory_engine_pipeline", str(source),
             "--workdir", str(workdir), "--stages", "ingest"],
            cwd=REPO_ROOT / "services" / "pipeline",
        )
        if code != 0:
            FLOW.fail("Import failed -- see the log above.")
            return

        port = _free_port()
        FLOW.say(f"Starting the local model host (port {port}) ...")
        host = subprocess.Popen(
            [python, "-m", "memory_engine_ml_runtime", "--port", str(port),
             "--database", str(workdir / "library.db")],
            cwd=REPO_ROOT / "workers" / "ml-runtime", env=_env(_HOST_ENV),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        assert host.stdout is not None
        # Read until the serving banner, echoing everything: a crash prints a
        # traceback, and its FIRST line is not the story.
        started = False
        for line in host.stdout:
            FLOW.say("  " + line.rstrip())
            if "serving" in line:
                started = True
                break
        if not started:
            host.wait()
            FLOW.fail("The model host refused to start -- see the log above.")
            return
        # Keep draining the host's output for the rest of its life, or the
        # pipe buffer fills and BLOCKS the host mid-inference.
        threading.Thread(
            target=lambda: [None for _ in host.stdout], daemon=True
        ).start()

        # No render here: the first PDF nobody has reviewed is minutes of
        # work and hundreds of megabytes thrown away. Render happens once,
        # after FINALIZE.
        FLOW.say("Analyzing photos and planning the album ...")
        code = _stream(
            [python, "-m", "memory_engine_pipeline", str(source),
             "--workdir", str(workdir),
             "--stages", "analysis,faces,ranking,album",
             "--ml-runtime", f"127.0.0.1:{port}",
             "--album-photos", str(photos)],
            cwd=REPO_ROOT / "services" / "pipeline",
        )
        if code != 0:
            FLOW.fail("The pipeline failed -- see the log above.")
            return

        if not load_review_state(workdir):
            FLOW.fail("The pipeline finished but wrote no selection plan.")
            return
        FLOW.say("Ready for review.")
    except Exception as error:  # a failed flow must say so, never hang
        FLOW.fail(f"Unexpected error: {error}")
    finally:
        if host is not None and host.poll() is None:
            host.terminate()


def newest_album_spec(workdir: Path) -> Path | None:
    specs = sorted(
        (
            p for p in (workdir / "outputs" / "album").glob("*.json")
            if not p.name.endswith(".rejected.json")
        ),
        key=lambda p: p.stat().st_mtime,
    )
    return specs[-1] if specs else None


def _classify_clearance(workdir: Path, album: Path, override: bool) -> dict | None:
    """Run the local safety classifier over the album; return its decision.

    Without `override`, blocked items DENY publication and the UI asks the
    user. With it, the user's approval is recorded on the manifest
    (decided_by, per item, on disk) -- never applied silently.
    """
    manifest = workdir / "print-clearance.json"
    before = manifest.stat().st_mtime if manifest.exists() else None
    cmd = [PYTHON, str(REPO_ROOT / "scripts" / "classify_album_clearance.py"),
           "--workdir", str(workdir), "--album", str(album)]
    if override:
        cmd += ["--override-blocked-by", "rohan"]
    # The classifier exits non-zero when publication is DENIED -- that is a
    # verdict, not a crash. The failure signal is a manifest that never got
    # written (or rewritten).
    _stream(cmd, cwd=REPO_ROOT)
    if not manifest.exists() or manifest.stat().st_mtime == before:
        return None
    return json.loads(manifest.read_text(encoding="utf-8"))


def run_finalize_flow(review: dict, approved_flagged: bool = False) -> None:
    """Review to album: honour the user's decisions, re-plan, clear, render."""
    workdir, source = FLOW.workdir, FLOW.source
    assert workdir is not None
    out = workdir / REVIEW_FILENAME
    out.write_text(json.dumps(review, indent=1) + "\n", encoding="utf-8")
    FLOW.say(f"Saved your decisions to {out.name}.")

    FLOW.say("Re-planning the album with your choices ...")
    code = _stream(
        [PYTHON, "-m", "memory_engine_pipeline",
         str(source or workdir), "--workdir", str(workdir),
         "--stages", "album"],
        cwd=REPO_ROOT / "services" / "pipeline",
    )
    album = newest_album_spec(workdir)
    if code != 0 or album is None:
        FLOW.fail("The re-plan failed -- see the log above.")
        return

    FLOW.say("Running the local content check ...")
    clearance = _classify_clearance(workdir, album, override=approved_flagged)
    if clearance is None:
        FLOW.fail("The content check failed -- see the log above.")
        return
    decision = clearance.get("decision") or {}
    blocked = int(decision.get("blocked_count") or 0)
    if not decision.get("cleared_for_publication"):
        # Not a failure: the local check wants a human decision. The UI
        # shows the count and asks; approval re-enters this flow with
        # `approved_flagged=True`, which records the override on disk.
        with FLOW.lock:
            FLOW.state = "flagged"
            FLOW.pending_review = review
            FLOW.flagged_count = blocked
        FLOW.say(f"{blocked} photo(s) flagged by the local content check -- "
                 "waiting for your approval.")
        return

    FLOW.say("Rendering your final album (this takes a few minutes) ...")
    code = _stream(
        [PYTHON, "-m", "memory_engine_pipeline",
         str(source or workdir), "--workdir", str(workdir),
         "--stages", "album,render-print", "--allow-development-clearance"],
        cwd=REPO_ROOT / "services" / "pipeline",
    )
    pdf = newest_pdf(workdir)
    if code != 0 or pdf is None:
        FLOW.fail("The final render failed -- see the log above.")
        return
    with FLOW.lock:
        FLOW.pdf = pdf
        FLOW.state = "done"
    FLOW.say(f"Album ready: {pdf.name}")


# --------------------------------------------------------------------- page --
_PAGE = """<!doctype html>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Photeo — your album</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; margin: 0; }
  body { background:#141311; color:#e8e4dc; font:15px/1.5 Georgia, 'Iowan Old Style', serif; }
  header { padding:28px 32px 18px; border-bottom:1px solid #2c2a25; display:flex;
           justify-content:space-between; align-items:baseline; gap:16px; flex-wrap:wrap; }
  h1 { font-size:22px; font-weight:normal; letter-spacing:0.01em; }
  h1 small { color:#9a927f; font-size:13px; margin-left:12px; }
  .counts { font:13px 'SF Mono', Menlo, monospace; color:#9a927f; }
  main { padding:24px 32px 120px; }
  .grid { display:grid; grid-template-columns:repeat(auto-fill, minmax(240px, 1fr)); gap:18px; }
  figure.card { background:#1c1a17; border:1px solid #2c2a25; padding:10px; cursor:pointer; }
  figure.card.swapped { border-color:#c8a24a; }
  figure.card img { width:100%; aspect-ratio:3/4; object-fit:contain; background:#000; display:block; }
  figcaption { padding-top:8px; }
  .slot { font:11px 'SF Mono', Menlo, monospace; color:#9a927f; }
  .why { font-size:12.5px; color:#c6bfb0; padding-top:4px; }
  .flag { font:11px 'SF Mono', Menlo, monospace; color:#c8a24a; }
  #panel { position:fixed; inset:0; background:rgba(10,9,8,0.92); display:none;
           overflow-y:auto; padding:40px 32px; }
  #panel.open { display:block; }
  #panel .inner { max-width:1100px; margin:0 auto; }
  #panel h2 { font-size:18px; font-weight:normal; padding-bottom:6px; }
  #panel h3 { font-size:14px; font-weight:normal; color:#9a927f; padding:22px 0 10px;
              border-bottom:1px solid #2c2a25; margin-bottom:14px; }
  #panel .sub { color:#9a927f; font-size:13px; padding-bottom:20px; }
  .alts { display:grid; grid-template-columns:repeat(auto-fill, minmax(220px, 1fr)); gap:16px; }
  .alt { background:#1c1a17; border:1px solid #2c2a25; padding:10px; }
  .alt img { width:100%; aspect-ratio:3/4; object-fit:contain; background:#000; display:block; }
  .alt .reason { font-size:12.5px; color:#c6bfb0; padding:8px 0; }
  .alt button, .actions button {
    font:13px 'SF Mono', Menlo, monospace; background:none; border:1px solid #6e6754;
    color:#e8e4dc; padding:8px 14px; cursor:pointer; width:100%; }
  .alt button:hover { border-color:#c8a24a; color:#c8a24a; }
  .alt button.current { border-color:#c8a24a; color:#c8a24a; }
  .alt .unfit { color:#8a5a4a; font:11px 'SF Mono', Menlo, monospace; padding-bottom:6px; }
  .actions { padding-top:24px; display:flex; gap:12px; }
  .actions button { width:auto; }
  footer { position:fixed; bottom:0; left:0; right:0; background:#1c1a17;
           border-top:1px solid #2c2a25; padding:14px 32px; display:flex;
           justify-content:space-between; align-items:center; }
  footer .status { font:13px 'SF Mono', Menlo, monospace; color:#9a927f; }
  footer button { font:14px 'SF Mono', Menlo, monospace; background:#c8a24a; color:#141311;
                  border:none; padding:12px 26px; cursor:pointer; }
  footer button:disabled { background:#4a463d; color:#141311; cursor:default; }
  /* intake + progress */
  #intake { max-width:640px; margin:60px auto; padding:0 32px; }
  #intake p { color:#c6bfb0; padding-bottom:24px; }
  #intake label { display:block; font:12px 'SF Mono', Menlo, monospace; color:#9a927f;
                  padding:16px 0 6px; }
  #intake input { width:100%; background:#1c1a17; border:1px solid #2c2a25; color:#e8e4dc;
                  font:15px Georgia, serif; padding:12px 14px; }
  #intake input:focus { outline:none; border-color:#6e6754; }
  #intake button { margin-top:28px; font:14px 'SF Mono', Menlo, monospace; background:#c8a24a;
                   color:#141311; border:none; padding:13px 30px; cursor:pointer; }
  #intake .err { color:#c47a5a; font:13px 'SF Mono', Menlo, monospace; padding-top:14px; }
  #progress { max-width:820px; margin:40px auto; padding:0 32px; }
  #plog { background:#100f0d; border:1px solid #2c2a25; padding:18px;
          font:12.5px 'SF Mono', Menlo, monospace; color:#c6bfb0; white-space:pre-wrap;
          max-height:60vh; overflow-y:auto; }
  #ptitle2 { font-size:18px; font-weight:normal; padding-bottom:14px; }
  .pulse { color:#c8a24a; }
  #donebox { max-width:640px; margin:80px auto; padding:0 32px; text-align:center; }
  #donebox a { display:inline-block; margin-top:24px; font:14px 'SF Mono', Menlo, monospace;
               background:#c8a24a; color:#141311; text-decoration:none; padding:13px 30px; }
  .hidden { display:none !important; }
</style>

<div id="intake" class="hidden">
  <h1 style="padding-bottom:18px">Make an album</h1>
  <p>Point Photeo at a folder of photos. Everything runs on this machine —
     the photos never leave it.</p>
  <label>FOLDER OF PHOTOS</label>
  <div style="display:flex; gap:10px">
    <input id="folder" placeholder="no folder chosen yet" readonly
           style="cursor:pointer" onclick="openBrowser()">
    <button onclick="openBrowser()" style="margin:0; font:13px 'SF Mono', Menlo, monospace;
      background:none; border:1px solid #6e6754; color:#e8e4dc; padding:0 18px;
      cursor:pointer; white-space:nowrap">Choose&hellip;</button>
  </div>
  <label>ALBUM NAME</label>
  <input id="name" placeholder="our-trip" spellcheck="false">
  <label>PHOTOS IN THE ALBUM</label>
  <input id="count" value="25" inputmode="numeric">
  <button onclick="start()">Build my album</button>
  <div class="err" id="err"></div>
</div>

<div id="browser" class="hidden" style="position:fixed; inset:0; background:rgba(10,9,8,0.92); overflow-y:auto; z-index:10">
  <div style="max-width:640px; margin:60px auto; background:#1c1a17; border:1px solid #2c2a25; padding:24px 28px">
    <h2 style="font-size:17px; font-weight:normal; padding-bottom:4px">Choose a folder</h2>
    <div id="bpath" style="font:12px 'SF Mono', Menlo, monospace; color:#9a927f; padding-bottom:14px; word-break:break-all"></div>
    <div id="bmedia" style="font:12px 'SF Mono', Menlo, monospace; color:#c8a24a; padding-bottom:10px"></div>
    <div id="bdirs" style="max-height:45vh; overflow-y:auto; border-top:1px solid #2c2a25"></div>
    <div style="display:flex; gap:12px; padding-top:20px">
      <button onclick="useFolder()" style="font:13px 'SF Mono', Menlo, monospace; background:#c8a24a;
        color:#141311; border:none; padding:11px 22px; cursor:pointer">Use this folder</button>
      <button onclick="document.getElementById('browser').classList.add('hidden')"
        style="font:13px 'SF Mono', Menlo, monospace; background:none; border:1px solid #6e6754;
        color:#e8e4dc; padding:11px 22px; cursor:pointer">Cancel</button>
    </div>
  </div>
</div>

<div id="progress" class="hidden">
  <h2 id="ptitle2">Building your album <span class="pulse">·</span></h2>
  <div id="plog"></div>
</div>

<div id="review" class="hidden">
<header>
  <h1>Review your album <small id="album"></small></h1>
  <div class="counts" id="counts"></div>
</header>
<main><div class="grid" id="grid"></div></main>
<div id="panel"><div class="inner">
  <h2 id="ptitle">Alternatives</h2>
  <div class="sub" id="pwhy"></div>
  <div id="galts-wrap"><h3>Similar photos &mdash; same shot or pose</h3><div class="alts" id="galts"></div></div>
  <div id="palts-wrap"><h3>Other best photos we didn&rsquo;t use</h3><div class="alts" id="palts"></div></div>
  <div class="actions"><button onclick="closePanel()">Keep current &amp; close</button></div>
</div></div>
<footer>
  <div class="status" id="status">No swaps yet — tap any photo to see its alternatives.</div>
  <div><button id="finalize" onclick="finalize()">Finalize album</button></div>
</footer>
</div>

<div id="donebox" class="hidden">
  <h1>Your album is ready</h1>
  <p style="color:#9a927f; padding-top:12px" id="donefile"></p>
  <a href="/album.pdf" target="_blank">Open the album PDF</a>
</div>

<div id="flaggedbox" class="hidden" style="max-width:640px; margin:80px auto; padding:0 32px; text-align:center">
  <h1>A word before printing</h1>
  <p style="color:#c6bfb0; padding-top:16px" id="flaggedmsg"></p>
  <p style="color:#9a927f; padding-top:10px; font-size:13px">
    The on-device content check runs on every photo before anything is
    rendered for print. It is cautious by design — family photos like
    maternity portraits trip it often. Your approval is recorded with the
    album, and nothing leaves this machine either way.</p>
  <button onclick="approveFlagged()" style="margin-top:28px; font:14px 'SF Mono', Menlo, monospace;
    background:#c8a24a; color:#141311; border:none; padding:13px 30px; cursor:pointer">
    These are my photos — print them</button>
</div>

<script>
let DATA = null;
const swaps = {};   // original selected media_id -> replacement media_id
let polling = null;

function show(id) {
  for (const s of ["intake", "progress", "review", "donebox", "flaggedbox"])
    document.getElementById(s).classList.toggle("hidden", s !== id);
}

async function boot() {
  const st = await (await fetch("/progress")).json();
  route(st);
}
function route(st) {
  if (st.state === "intake" || st.state === "failed") {
    show("intake");
    if (st.state === "failed")
      document.getElementById("err").textContent =
        "The last run failed — the terminal log has details. Fix and retry.";
  } else if (st.state === "running" || st.state === "rendering") {
    show("progress"); poll();
  } else if (st.state === "review") {
    show("review"); loadReview();
  } else if (st.state === "flagged") {
    show("flaggedbox");
    document.getElementById("flaggedmsg").textContent =
      st.flagged + " photo(s) were flagged by the local content check.";
  } else if (st.state === "done") {
    show("donebox");
    document.getElementById("donefile").textContent = st.pdf || "";
  }
}
async function approveFlagged() {
  const res = await fetch("/approve", {method: "POST", body: "{}"});
  if (res.ok) { show("progress"); poll(); }
}
let browsePath = null;
async function browse(path) {
  const res = await fetch("/browse" + (path ? "?path=" + encodeURIComponent(path) : ""));
  if (!res.ok) return;
  const d = await res.json();
  browsePath = d.path;
  document.getElementById("bpath").textContent = d.path;
  document.getElementById("bmedia").textContent =
    d.media_count ? d.media_count + " photo(s)/video(s) here" : "no photos directly in this folder";
  const list = document.getElementById("bdirs");
  list.innerHTML = "";
  const row = (label, target) => {
    const div = document.createElement("div");
    div.textContent = label;
    div.style.cssText = "padding:9px 6px; border-bottom:1px solid #2c2a25; cursor:pointer; font:13px 'SF Mono', Menlo, monospace";
    div.onmouseenter = () => div.style.color = "#c8a24a";
    div.onmouseleave = () => div.style.color = "";
    div.onclick = () => browse(target);
    list.appendChild(div);
  };
  if (d.parent) row("← up one level", d.parent);
  for (const dir of d.dirs) row(dir.name + "/", dir.path);
  document.getElementById("browser").classList.remove("hidden");
}
function openBrowser() { browse(browsePath); }
function useFolder() {
  document.getElementById("folder").value = browsePath || "";
  const name = document.getElementById("name");
  if (!name.value && browsePath) name.value = browsePath.split("/").filter(Boolean).pop();
  document.getElementById("browser").classList.add("hidden");
}
async function start() {
  const folder = document.getElementById("folder").value.trim();
  const name = document.getElementById("name").value.trim();
  const count = document.getElementById("count").value.trim();
  const res = await fetch("/start", {method:"POST",
    body: JSON.stringify({folder, name, photos: count})});
  const body = await res.json();
  if (!res.ok) { document.getElementById("err").textContent = body.error; return; }
  show("progress"); poll();
}
function poll() {
  if (polling) return;
  polling = setInterval(async () => {
    const st = await (await fetch("/progress")).json();
    const log = document.getElementById("plog");
    log.textContent = st.log.join("\\n");
    log.scrollTop = log.scrollHeight;
    document.getElementById("ptitle2").firstChild.textContent =
      st.state === "rendering" ? "Rendering your final album " : "Building your album ";
    if (!["running", "rendering"].includes(st.state)) {
      clearInterval(polling); polling = null; route(st);
      return;
    }
  }, 1500);
}

async function loadReview() {
  DATA = await (await fetch("/data")).json();
  document.getElementById("album").textContent = DATA.album_id.slice(0, 12);
  render();
}
function render() {
  const grid = document.getElementById("grid");
  grid.innerHTML = "";
  let n = 0;
  for (const s of DATA.selected) {
    const shown = swaps[s.media_id] || s.media_id;
    const fig = document.createElement("figure");
    fig.className = "card" + (swaps[s.media_id] ? " swapped" : "");
    const alts = s.alternatives.length;
    fig.innerHTML = `<img loading="lazy" src="/thumb/${shown}">
      <figcaption>
        <div class="slot">page ${s.page ?? "—"} · ${shown.slice(0,8)}${swaps[s.media_id] ? " · SWAPPED" : ""}</div>
        <div class="why">${swaps[s.media_id] ? "your choice" : (s.chosen_because[0] || "")}</div>
        <div class="flag">${alts ? alts + " from this shot" : "alternatives from the pool"}</div>
      </figcaption>`;
    fig.onclick = () => openPanel(s);
    grid.appendChild(fig);
    n++;
  }
  document.getElementById("counts").textContent =
    `${n} selected · ${Object.keys(swaps).length} swapped`;
  document.getElementById("status").textContent =
    Object.keys(swaps).length
      ? `${Object.keys(swaps).length} swap(s) pending — finalize when you're happy.`
      : "No swaps yet — tap any photo to see its alternatives.";
}
let PANEL_SLOT = null;   // the selected entry whose alternatives are open
const N_OTHER_BEST = 10;

function altCard(s, o, current) {
  const div = document.createElement("div");
  div.className = "alt";
  div.innerHTML = `<img loading="lazy" src="/thumb/${o.media_id}">
    ${o.fits === false ? '<div class="unfit">may print soft in this slot</div>' : ""}
    <div class="reason">${o.reasons.join("; ")}</div>
    <button class="${o.media_id === current ? "current" : ""}">
      ${o.media_id === current ? "current choice" : "use this one"}</button>`;
  div.querySelector("button").onclick = () => {
    if (o.media_id === s.media_id) delete swaps[s.media_id];
    else swaps[s.media_id] = o.media_id;
    // Live update: the album just changed, so the grid AND this panel's two
    // suggestion sections must recompute -- the photo we swapped out is now
    // available again, the one we swapped in must vanish from every other
    // list. The panel stays open, re-rendered against the new album.
    render();
    openPanel(s);
  };
  return div;
}
function openPanel(s) {
  PANEL_SLOT = s;
  document.getElementById("pwhy").textContent =
    "Engine chose " + s.media_id.slice(0,8) + ": " + s.chosen_because.join("; ");
  const current = swaps[s.media_id] || s.media_id;
  // Everything the LIVE album currently holds (originals minus swaps-out,
  // plus swaps-in). A suggestion may never be something already in the book.
  const inAlbum = new Set(DATA.selected.map(x => swaps[x.media_id] || x.media_id));
  const currentPose = poseOf(current);
  const shotIds = new Set(s.alternatives.map(a => a.media_id));
  // Every body pose the album ALREADY shows. The "other best" section offers
  // genuinely different looks, so it hides not just photos in the album but
  // any photo of a pose already in it -- a near-duplicate of something you
  // already have is not a fresh option.
  const albumPoses = new Set(
    [...inAlbum].map(m => poseOf(m)).filter(Boolean)
  );

  // The live candidate pool: the omitted photos, PLUS any original album
  // photo you have since swapped out -- removing a photo puts it back on the
  // table as a suggestion for other slots.
  const swappedOut = DATA.selected
    .filter(x => swaps[x.media_id] && !inAlbum.has(x.media_id))
    .map(x => ({media_id: x.media_id, pose: x.pose,
                reasons: ["you removed this from the album"]}));
  const available = swappedOut.concat(DATA.pool);

  // Section 1 -- SIMILAR: the same shot (the engine's own alternatives) plus
  // any available photo of the same body pose. Like-for-like swaps.
  const similar = document.getElementById("galts");
  similar.innerHTML = "";
  similar.appendChild(altCard(s, {media_id: s.media_id,
    reasons: ["the engine's pick"], fits: true}, current));
  for (const a of s.alternatives) {
    if (a.media_id === current || inAlbum.has(a.media_id)) continue;
    similar.appendChild(altCard(s, {media_id: a.media_id,
      reasons: a.not_chosen_because, fits: a.fits_slot !== false}, current));
  }
  for (const p of available) {
    if (p.media_id === current || inAlbum.has(p.media_id) || shotIds.has(p.media_id)) continue;
    if (currentPose && p.pose === currentPose) {
      similar.appendChild(altCard(s, {media_id: p.media_id,
        reasons: p.reasons || ["same body pose, different frame"]}, current));
    }
  }

  // Section 2 -- OTHER BEST: the ten highest-quality available photos that are
  // NEITHER in the album NOR already offered as a similar one. Different
  // looks, not near-duplicates.
  const other = document.getElementById("palts");
  other.innerHTML = "";
  let offered = 0;
  for (const p of available) {
    if (p.media_id === current || inAlbum.has(p.media_id) || shotIds.has(p.media_id)) continue;
    if (p.pose && albumPoses.has(p.pose)) continue;  // a pose already in the book
    other.appendChild(altCard(s, {media_id: p.media_id, reasons: p.reasons}, current));
    if (++offered >= N_OTHER_BEST) break;
  }
  document.getElementById("palts-wrap").classList.toggle("hidden", offered === 0);
  document.getElementById("panel").className = "open";
}
function poseOf(mediaId) {
  const hit = DATA.selected.find(x => x.media_id === mediaId);
  if (hit && hit.pose) return hit.pose;
  const inPool = DATA.pool.find(p => p.media_id === mediaId);
  if (inPool && inPool.pose) return inPool.pose;
  for (const s of DATA.selected)
    for (const a of s.alternatives)
      if (a.media_id === mediaId && a.pose) return a.pose;
  return null;
}
function closePanel() { PANEL_SLOT = null; document.getElementById("panel").className = ""; }
async function finalize() {
  const pinned = DATA.selected.map(s => swaps[s.media_id] || s.media_id);
  const excluded = Object.keys(swaps).filter(k => !pinned.includes(k));
  const body = {pinned, excluded, swaps,
                decided_by: "rohan", source: "review-ui-prototype"};
  document.getElementById("finalize").disabled = true;
  const res = await fetch("/finalize", {method: "POST", body: JSON.stringify(body)});
  if (res.ok) { show("progress"); poll(); }
  else document.getElementById("finalize").disabled = false;
}
boot();
</script>
"""


# ------------------------------------------------------------------ server --
class ReviewHandler(BaseHTTPRequestHandler):
    def log_message(self, *args) -> None:  # quiet
        pass

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, payload: dict) -> None:
        self._send(code, json.dumps(payload).encode(), "application/json")

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/":
            self._send(200, _PAGE.encode(), "text/html; charset=utf-8")
        elif self.path == "/progress":
            with FLOW.lock:
                self._json(200, {
                    "state": FLOW.state,
                    "log": list(FLOW.log[-200:]),
                    "pdf": FLOW.pdf.name if FLOW.pdf else None,
                    "flagged": FLOW.flagged_count,
                })
        elif self.path == "/data":
            self._data()
        elif self.path.startswith("/browse"):
            self._browse()
        elif self.path == "/album.pdf":
            pdf = FLOW.pdf
            if pdf is None or not pdf.is_file():
                self._send(404, b"no album yet", "text/plain")
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/pdf")
            self.send_header("Content-Length", str(pdf.stat().st_size))
            self.end_headers()
            with pdf.open("rb") as file:
                while chunk := file.read(1 << 20):
                    self.wfile.write(chunk)
        elif self.path.startswith("/thumb/"):
            media_id = unquote(self.path[len("/thumb/"):])
            path = FLOW.thumbs.get(media_id)
            if path is None or not path.is_file():
                self._send(404, b"unknown media id", "text/plain")
                return
            self._send(200, path.read_bytes(), "image/jpeg")
        else:
            self._send(404, b"not found", "text/plain")

    def _browse(self) -> None:
        """The folder picker's eyes: list one directory's subfolders and how
        many photos sit directly in it. Localhost prototype of the desktop
        app's native folder dialog -- the server IS the user's machine."""
        from urllib.parse import parse_qs, urlparse  # noqa: PLC0415

        query = parse_qs(urlparse(self.path).query)
        raw = (query.get("path") or [str(Path.home())])[0]
        path = Path(raw).expanduser().resolve()
        if not path.is_dir():
            path = Path.home()
        dirs = []
        media_count = 0
        try:
            for entry in sorted(path.iterdir(), key=lambda p: p.name.lower()):
                if entry.name.startswith("."):
                    continue
                if entry.is_dir():
                    dirs.append({"name": entry.name, "path": str(entry)})
                elif entry.suffix.lower() in _MEDIA_SUFFIXES:
                    media_count += 1
        except PermissionError:
            pass
        self._json(200, {
            "path": str(path),
            "parent": str(path.parent) if path != path.parent else None,
            "dirs": dirs,
            "media_count": media_count,
        })

    def _data(self) -> None:
        sidecar = FLOW.sidecar
        if sidecar is None:
            self._json(409, {"error": "no plan loaded"})
            return
        poses = _load_pose_map(FLOW.workdir) if FLOW.workdir else {}
        selected_entries = sidecar["selected"]
        selected_ids = {e["media_id"] for e in selected_entries}
        offered = {
            a["media_id"]
            for e in selected_entries
            for a in e.get("alternatives") or []
        }
        # The omitted pool: every photo NOT in the album and not already a
        # same-shot alternative, ranked by recorded quality standing. The
        # whole pool ships (the client draws the "10 other best" from it after
        # excluding whatever the live album now holds), so a run of swaps can
        # never exhaust the suggestions.
        pool = [
            {
                "media_id": u["media_id"],
                "quality": u.get("quality"),
                "pose": poses.get(u["media_id"]),
                "reasons": [u.get("detail") or "left out on the album objective"],
            }
            for u in sidecar.get("unselected") or []
            if u["media_id"] not in selected_ids and u["media_id"] not in offered
        ]
        pool.sort(key=lambda p: (p["quality"] is None, -(p["quality"] or 0.0)))
        data = {
            "album_id": sidecar.get("album_id", ""),
            "selected": [
                {
                    "media_id": e["media_id"],
                    "page": (e.get("placement") or {}).get("page_index"),
                    "pose": poses.get(e["media_id"]),
                    "chosen_because": e.get("chosen_because") or [],
                    "alternatives": [
                        {
                            "media_id": a["media_id"],
                            "pose": poses.get(a["media_id"]),
                            "not_chosen_because": a.get("not_chosen_because") or [],
                            "fits_slot": a.get("fits_slot"),
                        }
                        for a in e.get("alternatives") or []
                    ],
                }
                for e in sorted(
                    selected_entries,
                    key=lambda e: (
                        (e.get("placement") or {}).get("page_index") is None,
                        (e.get("placement") or {}).get("page_index") or 0,
                    ),
                )
            ],
            "pool": pool,
        }
        self._json(200, data)

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        if self.path == "/start":
            self._start(body)
        elif self.path == "/finalize":
            self._finalize(body)
        elif self.path == "/approve":
            self._approve()
        else:
            self._send(404, b"not found", "text/plain")

    def _approve(self) -> None:
        """The user's explicit yes to the flagged photos: re-enter the
        finalize flow with the override recorded on the manifest."""
        with FLOW.lock:
            review = FLOW.pending_review
            if FLOW.state != "flagged" or review is None:
                self._json(409, {"error": "nothing awaiting approval"})
                return
            FLOW.state = "rendering"
            FLOW.pending_review = None
        threading.Thread(
            target=run_finalize_flow, args=(review, True), daemon=True
        ).start()
        self._json(200, {"rendering": True})

    def _start(self, body: dict) -> None:
        folder = Path(str(body.get("folder") or "")).expanduser()
        name = "".join(
            ch for ch in str(body.get("name") or folder.name)
            if ch.isalnum() or ch in "-_"
        ).lower() or "album"
        try:
            photos = max(4, min(100, int(body.get("photos") or 25)))
        except ValueError:
            photos = 25
        if not folder.is_dir():
            self._json(400, {"error": f"{folder} is not a folder"})
            return
        has_media = any(
            p.suffix.lower() in _MEDIA_SUFFIXES
            for p in folder.rglob("*") if p.is_file()
        )
        if not has_media:
            self._json(400, {"error": f"no photos or videos found under {folder}"})
            return
        with FLOW.lock:
            if FLOW.state in ("running", "rendering"):
                self._json(409, {"error": "a run is already in progress"})
                return
            FLOW.state = "running"
            FLOW.log = []
            FLOW.source = folder
            FLOW.workdir = REPO_ROOT / "runs" / name
        threading.Thread(
            target=run_pipeline_flow,
            args=(folder, FLOW.workdir, photos),
            daemon=True,
        ).start()
        self._json(200, {"started": True})

    def _finalize(self, review: dict) -> None:
        if FLOW.workdir is None or FLOW.sidecar is None:
            self._json(409, {"error": "nothing to finalize"})
            return
        review["album_id"] = FLOW.sidecar.get("album_id")
        review["decided_at"] = datetime.now(timezone.utc).isoformat()
        with FLOW.lock:
            FLOW.state = "rendering"
        threading.Thread(
            target=run_finalize_flow, args=(review,), daemon=True
        ).start()
        self._json(200, {"rendering": True})


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workdir", type=Path, nargs="?", default=None,
                        help="open straight onto this run's review screen")
    parser.add_argument("--port", type=int, default=4189)
    args = parser.parse_args()

    if args.workdir is not None:
        workdir = args.workdir.expanduser().resolve()
        if not load_review_state(workdir):
            raise SystemExit(f"no selection sidecar under {workdir}/outputs/selection")
        print(f"review UI: http://127.0.0.1:{args.port}  (album "
              f"{FLOW.sidecar.get('album_id', '')[:12]}, "
              f"{len(FLOW.sidecar['selected'])} selected)")
    else:
        print(f"album flow: http://127.0.0.1:{args.port}  (intake)")

    server = ThreadingHTTPServer(("127.0.0.1", args.port), ReviewHandler)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
