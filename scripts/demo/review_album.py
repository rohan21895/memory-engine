#!/usr/bin/env python3
"""The album review flow, served locally: select -> review -> finalize.

A working prototype of the desktop app's future review screen (issue #139),
driven entirely by artifacts the pipeline already writes:

  1. reads the NEWEST selection sidecar in `<workdir>/outputs/selection/`
     (the plan's full account: every selected photo, its shot-group
     alternatives with reasons, every rejection),
  2. serves a review page on localhost: the selected photos in page order;
     tapping one shows its alternatives with the engine's reasons; the user
     swaps freely,
  3. FINALIZE writes `<workdir>/album-review.json` -- pinned ids (the final
     set) and excluded ids (everything swapped out) -- which the album stage
     reads on the next plan, so the re-planned book honours every decision.

Localhost only, stdlib only, serves thumbnails straight from the workdir's
proxy store. Nothing leaves the machine.

Usage:
    python scripts/demo/review_album.py <workdir> [--port 4189]
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote

REVIEW_FILENAME = "album-review.json"


def newest_sidecar(workdir: Path) -> Path:
    sidecars = sorted(
        (workdir / "outputs" / "selection").glob("*.json"),
        key=lambda p: p.stat().st_mtime,
    )
    if not sidecars:
        raise SystemExit(f"no selection sidecar under {workdir}/outputs/selection")
    return sidecars[-1]


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


_PAGE = """<!doctype html>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Album review</title>
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
  #done { display:none; font:13px 'SF Mono', Menlo, monospace; color:#7fb069; }
</style>
<header>
  <h1>Review your album <small id="album"></small></h1>
  <div class="counts" id="counts"></div>
</header>
<main><div class="grid" id="grid"></div></main>
<div id="panel"><div class="inner">
  <h2 id="ptitle">Alternatives from the same shot</h2>
  <div class="sub" id="pwhy"></div>
  <div class="alts" id="alts"></div>
  <div class="actions"><button onclick="closePanel()">Keep current & close</button></div>
</div></div>
<footer>
  <div class="status" id="status">No swaps yet — tap any photo to see its alternatives.</div>
  <div><span id="done">Saved. The album will be re-planned with your choices.</span>
  <button id="finalize" onclick="finalize()">Finalize album</button></div>
</footer>
<script>
let DATA = null;
const swaps = {};   // original selected media_id -> replacement media_id
async function load() {
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
        <div class="flag">${alts ? alts + " alternative" + (alts>1?"s":"") : "no alternatives"}</div>
      </figcaption>`;
    if (alts) fig.onclick = () => openPanel(s);
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
function openPanel(s) {
  const panel = document.getElementById("panel");
  document.getElementById("pwhy").textContent =
    "Engine chose " + s.media_id.slice(0,8) + ": " + s.chosen_because.join("; ");
  const alts = document.getElementById("alts");
  alts.innerHTML = "";
  const options = [{media_id: s.media_id, reasons: ["the engine's pick"], fits: true}]
    .concat(s.alternatives.map(a => ({media_id: a.media_id, reasons: a.not_chosen_because, fits: a.fits_slot !== false})));
  const current = swaps[s.media_id] || s.media_id;
  for (const o of options) {
    const div = document.createElement("div");
    div.className = "alt";
    div.innerHTML = `<img loading="lazy" src="/thumb/${o.media_id}">
      ${o.fits ? "" : '<div class="unfit">may print soft in this slot</div>'}
      <div class="reason">${o.reasons.join("; ")}</div>
      <button class="${o.media_id === current ? "current" : ""}">
        ${o.media_id === current ? "current choice" : "use this one"}</button>`;
    div.querySelector("button").onclick = () => {
      if (o.media_id === s.media_id) delete swaps[s.media_id];
      else swaps[s.media_id] = o.media_id;
      closePanel(); render();
    };
    alts.appendChild(div);
  }
  panel.className = "open";
}
function closePanel() { document.getElementById("panel").className = ""; }
async function finalize() {
  const pinned = DATA.selected.map(s => swaps[s.media_id] || s.media_id);
  const excluded = Object.keys(swaps);
  const body = {pinned, excluded, swaps,
                decided_by: "rohan", source: "review-ui-prototype"};
  const res = await fetch("/finalize", {method: "POST", body: JSON.stringify(body)});
  if (res.ok) {
    document.getElementById("done").style.display = "inline";
    document.getElementById("finalize").disabled = true;
  }
}
load();
</script>
"""


class ReviewHandler(BaseHTTPRequestHandler):
    workdir: Path
    sidecar: dict
    thumbs: dict[str, Path]

    def log_message(self, *args) -> None:  # quiet
        pass

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/":
            self._send(200, _PAGE.encode(), "text/html; charset=utf-8")
        elif self.path == "/data":
            pages = {}
            for entry in self.sidecar["selected"]:
                placement = entry.get("placement") or {}
                pages[entry["media_id"]] = placement.get("page_index")
            data = {
                "album_id": self.sidecar.get("album_id", ""),
                "selected": [
                    {
                        "media_id": e["media_id"],
                        "page": pages.get(e["media_id"]),
                        "chosen_because": e.get("chosen_because") or [],
                        "alternatives": [
                            {
                                "media_id": a["media_id"],
                                "not_chosen_because": a.get("not_chosen_because") or [],
                                "fits_slot": a.get("fits_slot"),
                            }
                            for a in e.get("alternatives") or []
                        ],
                    }
                    for e in sorted(
                        self.sidecar["selected"],
                        key=lambda e: (
                            (e.get("placement") or {}).get("page_index") is None,
                            (e.get("placement") or {}).get("page_index") or 0,
                        ),
                    )
                ],
            }
            self._send(200, json.dumps(data).encode(), "application/json")
        elif self.path.startswith("/thumb/"):
            media_id = unquote(self.path[len("/thumb/"):])
            path = self.thumbs.get(media_id)
            if path is None or not path.is_file():
                self._send(404, b"unknown media id", "text/plain")
                return
            self._send(200, path.read_bytes(), "image/jpeg")
        else:
            self._send(404, b"not found", "text/plain")

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/finalize":
            self._send(404, b"not found", "text/plain")
            return
        length = int(self.headers.get("Content-Length") or 0)
        review = json.loads(self.rfile.read(length) or b"{}")
        review["album_id"] = self.sidecar.get("album_id")
        review["decided_at"] = datetime.now(timezone.utc).isoformat()
        out = self.workdir / REVIEW_FILENAME
        out.write_text(json.dumps(review, indent=1) + "\n", encoding="utf-8")
        self._send(200, json.dumps({"written": str(out)}).encode(), "application/json")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workdir", type=Path)
    parser.add_argument("--port", type=int, default=4189)
    args = parser.parse_args()

    workdir = args.workdir.expanduser().resolve()
    handler = ReviewHandler
    handler.workdir = workdir
    handler.sidecar = json.loads(newest_sidecar(workdir).read_text(encoding="utf-8"))
    handler.thumbs = thumbnail_paths(workdir)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), handler)
    print(f"review UI: http://127.0.0.1:{args.port}  (album "
          f"{handler.sidecar.get('album_id', '')[:12]}, "
          f"{len(handler.sidecar['selected'])} selected)")
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
