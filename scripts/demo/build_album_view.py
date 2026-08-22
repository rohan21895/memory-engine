"""Build a screen-native album VIEWER: a self-contained HTML page where every
spread is composed from the colours of its own photos.

Not a print surface -- an experience, and a STATIC one: no photo is cropped,
zoomed or animated. Each photo is shown whole, floating on a colour field drawn
from itself (so the letterbox is never dead space), lifted by an accent-tinted
shadow. Spreads are modern editorial compositions -- offset, layered, asymmetric
-- never a small photo marooned in blank space and never two frames split by a
rule. Film grain, gradient meshes, duotone panels and big quiet typography give
it the tactile weight of a real album. Colour is per-photo, never one wash for
the whole book.

Local-first and private: images are downscaled and embedded as data URIs, so the
page is one file that opens offline and never leaves the machine. PDF stays the
export-for-print path; this is the look-at-it path.

Usage: python scripts/demo/build_album_view.py <workdir> [--style editorial] [--out album-view.html]
"""
from __future__ import annotations

import argparse
import base64
import colorsys
import glob
import io
import json
import os
import sqlite3
from pathlib import Path

from PIL import Image, ImageOps


# --- palette -------------------------------------------------------------

def _clamp(v, lo=0.0, hi=1.0):
    return max(lo, min(hi, v))


def _hex(r, g, b):
    return "#{:02x}{:02x}{:02x}".format(
        int(_clamp(r) * 255 + 0.5), int(_clamp(g) * 255 + 0.5), int(_clamp(b) * 255 + 0.5)
    )


def _hsl(h, s, l):
    return _hex(*colorsys.hls_to_rgb(h, l, s))


def palette(img: Image.Image) -> dict:
    """A rich per-photo palette: a deep ground and a lighter rise (both keeping
    the photo's own hue and a good deal of its saturation, so the field belongs
    to the picture), a vibrant PRIMARY accent (the most alive colour) and a
    SECONDARY accent of a different hue when the frame has one, plus a soft light
    for hairlines. Saturation is kept generous on purpose -- a washed-out mat was
    the whole complaint."""
    small = ImageOps.exif_transpose(img).convert("RGB").resize((96, 96), Image.BOX)
    q = small.quantize(colors=14, method=Image.Quantize.MAXCOVERAGE)
    pal = q.getpalette() or []
    counts = q.getcolors() or []
    total = sum(c for c, _ in counts) or 1

    sw = []  # (weight, h, s, l, (r,g,b))
    wr = wg = wb = 0.0
    for count, idx in counts:
        r, g, b = (pal[idx * 3 + k] / 255.0 for k in range(3))
        h, l, s = colorsys.rgb_to_hls(r, g, b)
        w = count / total
        sw.append((w, h, s, l, (r, g, b)))
        wr += r * w; wg += g * w; wb += b * w
    mh, ml, ms = colorsys.rgb_to_hls(wr, wg, wb)

    def alive(x):
        w, h, s, l, _ = x
        if l < 0.14 or l > 0.9:
            return -1.0
        return s * (0.55 + 0.45 * min(1.0, w * 6)) * (1.0 - abs(l - 0.55) * 0.8)

    a1 = max(sw, key=alive)
    a1h, a1s, a1l = a1[1], a1[2], a1[3]
    if alive(a1) <= 0:
        a1h, a1s, a1l = mh, 0.4, 0.55
    # secondary accent: the most alive swatch whose hue is far from the primary
    def far(x):
        w, h, s, l, _ = x
        if l < 0.14 or l > 0.9:
            return -1.0
        d = abs(h - a1h); d = min(d, 1 - d)
        return alive(x) * (0.4 + d)
    a2 = max(sw, key=far)
    a2h, a2s, a2l = (a2[1], a2[2], a2[3]) if far(a2) > 0 else (a1h, a1s, a1l)

    swatches = [_hex(*x[4]) for x in sorted(sw, key=lambda x: -x[3])[:5]]

    return {
        "deep": _hsl(mh, _clamp(ms * 0.75, 0.08, 0.6), 0.07),
        "ground": _hsl(mh, _clamp(ms * 0.8, 0.10, 0.62), 0.13),
        "rise": _hsl(mh, _clamp(ms * 0.7, 0.10, 0.55), 0.20),
        "accent": _hsl(a1h, _clamp(max(a1s, 0.5), 0.0, 0.96), _clamp(max(a1l, 0.5), 0.44, 0.62)),
        "accent2": _hsl(a2h, _clamp(max(a2s, 0.45), 0.0, 0.94), _clamp(max(a2l, 0.5), 0.42, 0.62)),
        "glow": _hsl(a1h, _clamp(max(a1s, 0.55), 0.0, 0.98), 0.56),
        "light": _hsl(mh, 0.24, 0.88),
        "swatches": swatches,
    }


# --- image embedding -----------------------------------------------------

def embed(path: str, longest: int = 1680, quality: int = 84) -> tuple[str, int, int, Image.Image]:
    with Image.open(path) as raw:
        img = ImageOps.exif_transpose(raw).convert("RGB")
    small = img.copy()
    small.thumbnail((longest, longest), Image.LANCZOS)
    buf = io.BytesIO()
    small.save(buf, format="JPEG", quality=quality, optimize=True)
    uri = "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
    return uri, small.width, small.height, img


# --- scene assembly ------------------------------------------------------

def build(workdir: Path, style: str) -> list[dict]:
    con = sqlite3.connect(f"file:{workdir / 'library.db'}?mode=ro", uri=True)
    src = {m: p for m, p in con.execute("SELECT media_id, path FROM media_source").fetchall()}
    # Local downscaled proxies, used when the original has been archived off the
    # machine (the viewer only needs display-res pixels, and proxies live in the
    # workdir). Keep the largest proxy per media for the best on-screen quality.
    prox_dir = workdir / "records" / "proxies"
    proxies: dict[str, tuple[int, str]] = {}
    for mid, pid, ppath, size in con.execute(
            "SELECT media_id, proxy_id, path, byte_size FROM media_proxy"):
        cand = ppath if (ppath and os.path.isfile(ppath)) \
            else str(prox_dir / pid[:2] / pid[2:4] / f"{pid}.jpg")
        if os.path.isfile(cand) and (mid not in proxies or (size or 0) > proxies[mid][0]):
            proxies[mid] = ((size or 0), cand)
    con.close()

    best, best_m = None, -1.0
    for sc in glob.glob(str(workdir / "outputs" / "album" / "*.style.json")):
        d = json.load(open(sc))
        if d.get("style") == style and os.path.getmtime(sc) > best_m:
            best, best_m = d["album_id"], os.path.getmtime(sc)
    if best is None:
        specs = [f for f in glob.glob(str(workdir / "outputs" / "album" / "*.json"))
                 if not f.endswith(".style.json")]
        best = Path(max(specs, key=os.path.getmtime)).stem
    spec = json.load(open(workdir / "outputs" / "album" / f"{best}.json"))

    cache: dict[str, dict] = {}

    def photo(media_id: str):
        if media_id in cache:
            return cache[media_id]
        path = src.get(media_id)
        if not path or not os.path.isfile(path):
            fb = proxies.get(media_id)
            if not fb:
                return None
            path = fb[1]
        uri, w, h, full = embed(path)
        cache[media_id] = {"src": uri, "w": w, "h": h, "pal": palette(full),
                           "portrait": h > w}
        return cache[media_id]

    # flatten to a clean, de-duplicated photo stream in album order
    seen, stream = set(), []
    for i, page in enumerate(spec["pages"]):
        pl = sorted(page.get("placements", []), key=lambda p: (not p.get("is_hero", False),))
        for p in pl:
            mid = p["media_id"]
            if mid in seen:
                continue
            ph = photo(mid)
            if ph:
                seen.add(mid); stream.append(ph)
    if not stream:
        return []

    scenes = [{"kind": "cover", "photos": [stream[0]]}]
    rest = stream[1:]

    # Compose spreads from the stream. Pairing/tripling is by orientation so a
    # layered duo never fights a portrait against a landscape, and the rhythm
    # alternates feature / duo / trio so the book breathes. Every photo is shown
    # whole; the composition fills the page with colour, not blank.
    feat_cycle = 0
    i = 0
    n = len(rest)
    while i < n:
        p = rest[i]
        # try a same-orientation trio, then a duo, else a feature
        same = [j for j in range(i + 1, min(i + 4, n)) if rest[j]["portrait"] == p["portrait"]]
        slot = len(scenes)
        if len(same) >= 2 and slot % 4 == 3:
            trio = [p, rest[same[0]], rest[same[1]]]
            scenes.append({"kind": "mosaic", "photos": trio})
            for j in sorted([i, same[0], same[1]], reverse=True):
                rest.pop(j); n -= 1
            continue
        if len(same) >= 1 and slot % 2 == 0:
            duo = [p, rest[same[0]]]
            scenes.append({"kind": "duo", "photos": duo})
            for j in sorted([i, same[0]], reverse=True):
                rest.pop(j); n -= 1
            continue
        kind = "feature-l" if p["portrait"] else "feature-t"
        if feat_cycle % 2 == 1:
            kind = "feature-r" if p["portrait"] else "full"
        feat_cycle += 1
        scenes.append({"kind": kind, "photos": [p]})
        i += 1
    return scenes


# --- html ----------------------------------------------------------------

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>__CSS__</style></head>
<body>
<svg width="0" height="0"><filter id="grain"><feTurbulence type="fractalNoise"
 baseFrequency="0.9" numOctaves="2" stitchTiles="stitch"/></filter></svg>
<nav id="switch" role="tablist" aria-label="Album version">
  <span class="tag">Version</span>
  <button data-design="editorial" aria-selected="true">Editorial</button>
  <button data-design="fineart" aria-selected="false">Fine&#8209;Art</button>
  <button data-design="colorfield" aria-selected="false">Colour&#8209;Field</button>
</nav>
<main id="stage" data-design="editorial"></main>
<div id="progress"><span id="bar"></span></div>
<script>const SCENES = __DATA__; const META = __META__;</script>
<script>__JS__</script>
</body></html>"""

CSS = r"""
:root{ color-scheme: dark; }
*{ box-sizing:border-box; margin:0; padding:0; }
body{ background:#0b0908; -webkit-font-smoothing:antialiased; overflow-x:hidden; }
#stage{ scroll-snap-type:y proximity; display:block; min-height:100vh;
  font-family:var(--body); color:var(--ink);
  transition:background-color .5s ease, color .5s ease; }
.serif{ font-family:var(--disp); }

/* ---- version switcher (chrome, sits above every design) ------------------ */
#switch{ position:fixed; z-index:30; top:16px; left:50%; transform:translateX(-50%);
  display:flex; align-items:center; gap:2px; padding:5px 6px 5px 14px; border-radius:999px;
  background:rgba(16,13,11,.72); backdrop-filter:blur(16px) saturate(1.3);
  box-shadow:0 14px 46px -20px rgba(0,0,0,.8), inset 0 0 0 1px rgba(255,255,255,.09); }
#switch .tag{ color:rgba(240,233,225,.5); font:600 .62rem/1 'Avenir Next',system-ui,sans-serif;
  letter-spacing:.24em; text-transform:uppercase; margin-right:8px; }
#switch button{ appearance:none; border:0; cursor:pointer; color:rgba(240,233,225,.82);
  font:600 .74rem/1 'Avenir Next',system-ui,sans-serif; letter-spacing:.05em;
  padding:9px 15px; border-radius:999px; background:transparent;
  transition:background .25s ease, color .25s ease; }
#switch button:hover{ color:#fff; }
#switch button[aria-selected="true"]{ background:#efe7d8; color:#1a1612; }

.spread{ position:relative; min-height:100vh; min-height:100svh; scroll-snap-align:start;
  display:grid; padding:clamp(26px,4.5vw,74px); overflow:hidden; isolation:isolate; }
.spread::before{ content:""; position:absolute; inset:0; z-index:-3; }
.spread::after{ content:""; position:absolute; inset:0; z-index:-1; pointer-events:none; }
.grain{ position:absolute; inset:0; z-index:-2; pointer-events:none; }

figure{ position:relative; display:block; overflow:hidden; }
figure img{ display:block; width:100%; height:100%; object-fit:contain; }
.no{ font-family:var(--disp); font-variant-numeric:tabular-nums; }

/* COVER — full image on its field, title in a quiet scrim (full-bleed in every design) */
.cover{ padding:0; }
.cover figure{ position:absolute; inset:0; padding:0; border-radius:0; box-shadow:none; background:transparent; }
.cover .scrim{ position:absolute; inset:0; z-index:1;
  background:linear-gradient(179deg, rgba(10,8,6,.42) 0 20%, transparent 46%, rgba(10,8,6,.86) 100%); }
.cover .plate{ position:absolute; z-index:2; left:0; right:0; bottom:clamp(46px,10vh,120px); text-align:center; }
.cover .eyebrow{ letter-spacing:.44em; text-transform:uppercase; font-size:.72rem; color:#f1eadf; opacity:.92; margin-bottom:1.1rem; }
.cover h1{ font-family:var(--disp); font-size:clamp(3.4rem,13vw,9.5rem); line-height:.9; font-weight:500; letter-spacing:-.012em; color:#faf5ec; }
.cover .rule{ width:66px; height:2px; margin:1.5rem auto 1.05rem; background:var(--accent); }
.cover .dates{ font-family:var(--disp); font-size:clamp(.9rem,2.4vw,1.12rem); letter-spacing:.22em; color:#f1eadf; opacity:.92; }

/* FEATURE — one photo + a colour panel carrying quiet type (no blank space) */
.feature{ grid-template-columns:1fr; }
.feat-grid{ display:grid; gap:clamp(16px,2.4vw,34px); width:100%; height:100%; align-items:stretch; }
.feature[data-var="l"] .feat-grid{ grid-template-columns:1.35fr .85fr; }
.feature[data-var="r"] .feat-grid{ grid-template-columns:.85fr 1.35fr; }
.feature[data-var="t"] .feat-grid{ grid-template-rows:1.55fr .75fr; }
.feature figure{ min-height:0; min-width:0; }
.panel{ position:relative; display:flex; flex-direction:column; justify-content:center;
  padding:clamp(16px,2.2vw,34px); overflow:hidden; }
.panel .big{ font-family:var(--disp); line-height:.8; letter-spacing:-.02em; }
.panel .word{ margin-top:.5rem; letter-spacing:.32em; text-transform:uppercase; font-size:.7rem;
  font-family:'Avenir Next',system-ui,sans-serif; }
.panel .prule{ width:44px; height:2px; margin:1rem 0; }
.chips{ display:flex; gap:9px; margin-top:.2rem; }
.chips i{ width:24px; height:24px; border-radius:50%; display:block; }
.feature[data-var="r"] .panel{ order:-1; text-align:right; align-items:flex-end; }
.feature[data-var="r"] .chips{ justify-content:flex-end; }

/* FULL — an immersive single frame */
.full{ place-items:center; }
.full figure{ width:100%; height:100%; }
.full .no{ position:absolute; right:clamp(24px,4vw,60px); top:clamp(20px,4vw,54px); z-index:2;
  font-size:.74rem; letter-spacing:.3em; color:var(--ink); opacity:.55; }

/* DUO — layered & offset, never a 50/50 split with a rule */
.duo{ place-items:center; }
.duo .stage{ position:relative; width:min(96%,1180px); height:min(84svh,780px); }
.duo .a,.duo .b{ position:absolute; }
.duo .b{ z-index:2; }
.duo[data-o="0"] .a{ left:0; top:6%; width:62%; height:82%; }
.duo[data-o="0"] .b{ right:0; bottom:0; width:44%; height:60%; }
.duo[data-o="1"] .a{ right:0; top:0; width:60%; height:80%; }
.duo[data-o="1"] .b{ left:0; bottom:4%; width:46%; height:62%; }
@media (max-width:760px){ .duo .stage{ height:auto; } .duo .a,.duo .b{ position:relative;
  width:100%!important; height:52svh!important; inset:auto!important; margin-bottom:16px; } }

/* MOSAIC — an asymmetric gallery wall */
.mosaic{ place-items:center; }
.mosaic .wall{ display:grid; gap:clamp(10px,1.4vw,18px); width:min(96%,1180px);
  height:min(84svh,780px); grid-template-columns:1.4fr 1fr; grid-template-rows:1fr 1fr; }
.mosaic .wall figure:nth-child(1){ grid-row:1 / span 2; }
@media (max-width:760px){ .mosaic .wall{ grid-template-columns:1fr; grid-template-rows:none;
  height:auto; } .mosaic .wall figure{ height:44svh; } .mosaic .wall figure:nth-child(1){ grid-row:auto; } }

#progress{ position:fixed; left:0; top:0; height:3px; width:100%; z-index:9; }
#bar{ display:block; height:100%; width:0;
  background:linear-gradient(90deg,var(--pAccent,#c9a24a),var(--pGlow,#e8c877)); transition:width .12s linear; }

/* ========================================================================
   THREE DESIGN LANGUAGES over the same photos + the same compositions.
   Each restyles ground, figure, panel, typography and texture — not a
   density tweak but a different room to hang the pictures in.
   ======================================================================== */

/* --- 1. EDITORIAL — dark, moody gallery; gradient mesh, grain, gold ------- */
[data-design="editorial"]{ background:#0b0908;
  --ink:#f0e9e1; --disp:'Hoefler Text','Cormorant Garamond',Georgia,serif;
  --body:'Optima','Avenir Next',system-ui,sans-serif; }
[data-design="editorial"] .spread::before{ background:
  radial-gradient(80% 60% at var(--ax,78%) var(--ay,18%), color-mix(in oklab,var(--accent2) 34%,transparent), transparent 62%),
  radial-gradient(95% 80% at 12% 108%, var(--rise), transparent 60%),
  linear-gradient(174deg, var(--ground), var(--deep) 82%); }
[data-design="editorial"] .spread::after{
  background:radial-gradient(130% 120% at 50% 40%, transparent 58%, rgba(0,0,0,.55)); mix-blend-mode:multiply; }
[data-design="editorial"] .spread[data-side="l"]{ box-shadow:inset 22px 0 60px -40px rgba(0,0,0,.8); }
[data-design="editorial"] .spread[data-side="r"]{ box-shadow:inset -22px 0 60px -40px rgba(0,0,0,.8); }
[data-design="editorial"] .grain{ opacity:.10; filter:url(#grain); mix-blend-mode:overlay; }
[data-design="editorial"] figure{ border-radius:2px;
  background:linear-gradient(160deg, color-mix(in oklab,var(--fground) 92%,#000), color-mix(in oklab,var(--fdeep) 96%,#000));
  box-shadow:0 34px 70px -34px rgba(0,0,0,.9), 0 10px 60px -30px color-mix(in oklab,var(--fglow) 70%,transparent),
    inset 0 0 0 1px color-mix(in oklab,var(--flight) 14%,transparent); }
[data-design="editorial"] .panel .big{ font-size:clamp(5rem,17vw,15rem); color:var(--light); opacity:.13; }
[data-design="editorial"] .panel .prule{ background:var(--accent); opacity:.9; }
[data-design="editorial"] .panel .word{ color:var(--light); opacity:.7; }
[data-design="editorial"] .chips i{ box-shadow:inset 0 0 0 1px rgba(255,255,255,.16); }

/* --- 2. FINE-ART — warm ivory paper, matted prints, thin Baskerville ------ */
[data-design="fineart"]{ background:#e9e0d0;
  --ink:#2c2822; --disp:'Baskerville','Hoefler Text',Georgia,serif;
  --body:'Avenir Next','Optima',system-ui,sans-serif; }
/* paper is tinted TOWARD each photo's own colour — never dead white */
[data-design="fineart"] .spread::before{ background:
  radial-gradient(90% 70% at 100% 0%, color-mix(in oklab,var(--rise) 32%, #efe7d8), transparent 62%),
  radial-gradient(80% 80% at 0% 100%, color-mix(in oklab,var(--ground) 20%, #ece3d3), transparent 60%),
  linear-gradient(176deg, color-mix(in oklab,var(--light) 24%, #f2ebde), color-mix(in oklab,var(--rise) 12%, #e4dac8)); }
[data-design="fineart"] .spread::after{
  background:radial-gradient(150% 140% at 50% 38%, transparent 68%, rgba(120,98,66,.12)); mix-blend-mode:multiply; }
[data-design="fineart"] .grain{ opacity:.05; filter:url(#grain); mix-blend-mode:multiply; }
[data-design="fineart"] figure{ border-radius:1px; padding:clamp(9px,1.1vw,18px);
  background:linear-gradient(180deg,#fcf8ef,#f2ebdd);
  box-shadow:0 34px 60px -36px rgba(70,52,28,.55), 0 6px 16px -8px rgba(70,52,28,.3),
    inset 0 0 0 1px rgba(120,98,66,.16); }
[data-design="fineart"] figure img{ box-shadow:0 0 0 1px rgba(70,52,28,.12); }
[data-design="fineart"] .panel{ justify-content:flex-end; }
[data-design="fineart"] .panel .big{ font-size:clamp(2.6rem,6vw,5rem); font-weight:400; color:#3a3226; opacity:.92; }
[data-design="fineart"] .panel .prule{ background:color-mix(in oklab,var(--accent) 55%, #6b5a42); width:36px; }
[data-design="fineart"] .panel .word{ color:#5b4f3c; opacity:.8; }
[data-design="fineart"] .chips i{ box-shadow:inset 0 0 0 1px rgba(70,52,28,.22); }
[data-design="fineart"] .cover h1{ font-weight:400; }
[data-design="fineart"] .full .no{ opacity:.5; }

/* --- 3. COLOUR-FIELD — bold graphic; saturated blocks, hard offset, Futura */
[data-design="colorfield"]{ background:#161310;
  --ink:#fbf6ec; --disp:'Futura','Avenir Next Condensed','Helvetica Neue',sans-serif;
  --body:'Avenir Next','Helvetica Neue',sans-serif; }
[data-design="colorfield"] .spread::before{ background:
  linear-gradient(115deg, color-mix(in oklab,var(--accent) 78%, #000) 0 40%, transparent 40%),
  radial-gradient(120% 110% at 6% 100%, color-mix(in oklab,var(--accent2) 60%, var(--deep)), transparent 55%),
  linear-gradient(160deg, var(--rise), var(--deep)); }
[data-design="colorfield"] .spread::after{ background:none; }
[data-design="colorfield"] .grain{ opacity:0; }
[data-design="colorfield"] figure{ border-radius:0;
  background:linear-gradient(155deg, var(--fground), color-mix(in oklab,var(--faccent) 34%, var(--fdeep)));
  box-shadow:16px 16px 0 0 color-mix(in oklab,var(--faccent) 85%,#000), inset 0 0 0 2px rgba(255,255,255,.92); }
[data-design="colorfield"] .duo .b{ box-shadow:16px 16px 0 0 color-mix(in oklab,var(--faccent2,var(--faccent)) 85%,#000), inset 0 0 0 2px rgba(255,255,255,.92); }
[data-design="colorfield"] .panel{ background:var(--accent); justify-content:flex-end; }
[data-design="colorfield"] .panel .big{ font-weight:800; font-size:clamp(6rem,20vw,17rem); line-height:.72;
  color:color-mix(in oklab,var(--deep) 86%, #000); opacity:1; letter-spacing:-.045em; }
[data-design="colorfield"] .panel .prule{ background:#fff; height:4px; width:58px; }
[data-design="colorfield"] .panel .word{ color:color-mix(in oklab,var(--deep) 78%,#000); opacity:.85; font-weight:700; }
[data-design="colorfield"] .chips i{ border-radius:0; box-shadow:inset 0 0 0 2px rgba(255,255,255,.55); }
[data-design="colorfield"] .cover h1{ text-transform:uppercase; font-weight:800; letter-spacing:-.02em;
  font-size:clamp(2.8rem,11vw,8rem); }
[data-design="colorfield"] .cover .rule{ height:5px; width:88px; }
"""

JS = r"""
const stage=document.getElementById('stage');
const FK=['deep','ground','rise','accent','accent2','glow','light'];
const figset=(f,p)=>{ FK.forEach(k=>f.style.setProperty('--f'+k,p[k])); };
const fig=(ph,cls)=>{ const f=document.createElement('figure'); if(cls)f.className=cls; figset(f,ph.pal);
  const i=new Image(); i.src=ph.src; i.loading='lazy'; i.decoding='async'; f.appendChild(i); return f; };
const spreadVars=(s,lead,second)=>{ const p=lead.pal; s.style.setProperty('--deep',p.deep);
  s.style.setProperty('--ground',p.ground); s.style.setProperty('--rise',p.rise);
  s.style.setProperty('--accent',p.accent); s.style.setProperty('--glow',p.glow); s.style.setProperty('--light',p.light);
  s.style.setProperty('--accent2',(second||lead).pal.accent2); };
const total=SCENES.length-1;

SCENES.forEach((sc,idx)=>{
  const s=document.createElement('section'); const lead=sc.photos[0];
  s.dataset.side = idx%2 ? 'r':'l';
  spreadVars(s,lead,sc.photos[1]);
  s.style.setProperty('--ax',(20+(idx*41)%64)+'%'); s.style.setProperty('--ay',(14+(idx*29)%60)+'%');
  const grain=document.createElement('div'); grain.className='grain';
  const no=String(idx).padStart(2,'0');

  if(sc.kind==='cover'){
    s.className='spread cover';
    s.appendChild(fig(lead));
    s.insertAdjacentHTML('beforeend','<div class="scrim"></div>');
    s.insertAdjacentHTML('beforeend',
      `<div class="plate"><div class="eyebrow">${META.eyebrow}</div>`+
      `<h1 class="serif">${META.title}</h1><div class="rule"></div>`+
      `<div class="dates serif">${META.dates}</div></div>`);
  } else if(sc.kind.startsWith('feature')){
    const v = sc.kind.endsWith('-r')?'r' : sc.kind.endsWith('-t')?'t':'l';
    s.className='spread feature'; s.dataset.var=v;
    const g=document.createElement('div'); g.className='feat-grid';
    const panel=document.createElement('div'); panel.className='panel';
    const chips=(lead.pal.swatches||[]).map(c=>`<i style="background:${c}"></i>`).join('');
    panel.innerHTML=`<div class="big serif">${no}</div><div class="prule"></div>`+
      `<div class="chips">${chips}</div><div class="word">plate ${no} · ${String(total).padStart(2,'0')}</div>`;
    const f=fig(lead);
    if(v==='r'){ g.appendChild(panel); g.appendChild(f); } else { g.appendChild(f); g.appendChild(panel); }
    s.appendChild(g);
  } else if(sc.kind==='full'){
    s.className='spread full'; s.appendChild(fig(lead));
    s.insertAdjacentHTML('beforeend',`<div class="no">${no} / ${String(total).padStart(2,'0')}</div>`);
  } else if(sc.kind==='duo'){
    s.className='spread duo'; s.dataset.o = idx%2;
    const st=document.createElement('div'); st.className='stage';
    st.appendChild(fig(sc.photos[0],'a')); st.appendChild(fig(sc.photos[1]||sc.photos[0],'b'));
    s.appendChild(st);
  } else if(sc.kind==='mosaic'){
    s.className='spread mosaic';
    const w=document.createElement('div'); w.className='wall';
    sc.photos.forEach(p=>w.appendChild(fig(p))); s.appendChild(w);
  }
  s.appendChild(grain); stage.appendChild(s);
});

// progress bar tinted to the spread in view (the only motion, and it's not a photo)
const root=document.documentElement, bar=document.getElementById('bar');
const spreads=[...document.querySelectorAll('.spread')];
const io=new IntersectionObserver((es)=>es.forEach(e=>{ if(e.isIntersecting){ const cs=getComputedStyle(e.target);
  root.style.setProperty('--pAccent',cs.getPropertyValue('--accent'));
  root.style.setProperty('--pGlow',cs.getPropertyValue('--glow')); } }),{threshold:.5});
spreads.forEach(s=>io.observe(s));
addEventListener('scroll',()=>{ const h=document.body.scrollHeight-innerHeight;
  bar.style.width=(100*scrollY/(h||1))+'%'; },{passive:true});

// version switcher: swap the whole design language over the same photos, in place
const btns=[...document.querySelectorAll('#switch button')];
btns.forEach(b=>b.addEventListener('click',()=>{
  const y=scrollY; stage.dataset.design=b.dataset.design;
  btns.forEach(x=>x.setAttribute('aria-selected', String(x===b)));
  scrollTo(0,y);
}));
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("workdir", type=Path)
    ap.add_argument("--style", default="editorial")
    ap.add_argument("--title", default="Aastha")
    ap.add_argument("--eyebrow", default="A Maternity Story")
    ap.add_argument("--dates", default="2026")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    scenes = build(args.workdir, args.style)
    meta = {"title": args.title, "eyebrow": args.eyebrow, "dates": args.dates}
    html = (PAGE
            .replace("__TITLE__", f"{args.title} — {args.eyebrow}")
            .replace("__CSS__", CSS)
            .replace("__JS__", JS)
            .replace("__DATA__", json.dumps(scenes))
            .replace("__META__", json.dumps(meta)))
    out = Path(args.out) if args.out else (args.workdir / "album-view.html")
    out.write_text(html, encoding="utf-8")
    print(f"wrote {out} ({len(scenes)} spreads, {out.stat().st_size/1e6:.1f} MB)")


if __name__ == "__main__":
    main()
