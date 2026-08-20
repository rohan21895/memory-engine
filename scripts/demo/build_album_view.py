"""Build a screen-native album VIEWER: a self-contained HTML page where every
spread's colour is drawn from its own photo.

Not a print surface -- an experience. Each photo yields a palette (a deep tinted
ground, a vibrant accent, a glow) and the spread is composed from it: gradient
grounds, accent halos that make the image lift off the page, colour-blocked
companions, palette moments. Treatments rotate so no two spreads feel the same.

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


def _hsl_hex(h, s, l):
    r, g, b = colorsys.hls_to_rgb(h, l, s)
    return _hex(r, g, b)


def palette(img: Image.Image) -> dict:
    """A spread palette from one image: a deep tinted ground, a mid ground, a
    vibrant accent for glows/rules, and a soft light for hairlines and type.

    The ground keeps the image's HUE but is pushed dark and gently desaturated
    so photos sit on a colour that belongs to them without competing; the accent
    is the most alive colour in the frame, chosen by saturation*presence away
    from the near-greys, so glows and rules feel drawn from the picture."""
    small = ImageOps.exif_transpose(img).convert("RGB").resize((96, 96), Image.BOX)
    q = small.quantize(colors=12, method=Image.Quantize.MAXCOVERAGE)
    pal = q.getpalette() or []
    counts = q.getcolors() or []
    total = sum(c for c, _ in counts) or 1

    swatches = []  # (weight, (h,s,l), (r,g,b))
    wr = wg = wb = 0.0
    for count, idx in counts:
        r, g, b = (pal[idx * 3 + k] / 255.0 for k in range(3))
        h, l, s = colorsys.rgb_to_hls(r, g, b)
        w = count / total
        swatches.append((w, (h, s, l), (r, g, b)))
        wr += r * w; wg += g * w; wb += b * w

    mh, ml, ms = colorsys.rgb_to_hls(wr, wg, wb)  # weighted-mean hue anchors the ground

    # accent: most alive colour (saturation with a presence bonus), mid-lightness
    def alive(sw):
        w, (h, s, l), _ = sw
        if l < 0.12 or l > 0.9:
            return -1
        return s * (0.6 + 0.4 * min(1.0, w * 6)) * (1.0 - abs(l - 0.55))
    accent_sw = max(swatches, key=alive)
    ah, as_, al = accent_sw[1]
    if alive(accent_sw) <= 0:  # a near-grey frame: borrow the mean hue, force some life
        ah, as_ = mh, 0.35

    # top swatches for the "palette moment" strip, brightest-first
    top = [s for s in sorted(swatches, key=lambda x: -x[0])[:6]]
    swatch_hexes = [_hex(*rgb) for _, _, rgb in sorted(top, key=lambda x: -x[1][2])[:5]]

    return {
        "deep": _hsl_hex(mh, _clamp(ms * 0.55, 0.05, 0.45), 0.06),
        "ground": _hsl_hex(mh, _clamp(ms * 0.6, 0.06, 0.5), 0.11),
        "rise": _hsl_hex(mh, _clamp(ms * 0.55, 0.05, 0.45), 0.17),
        "accent": _hsl_hex(ah, _clamp(max(as_, 0.45), 0.0, 0.95), _clamp(max(al, 0.5), 0.42, 0.62)),
        "glow": _hsl_hex(ah, _clamp(max(as_, 0.5), 0.0, 0.98), 0.58),
        "light": _hsl_hex(mh, 0.22, 0.86),
        "swatches": swatch_hexes,
    }


# --- image embedding -----------------------------------------------------

def embed(path: str, longest: int = 1600, quality: int = 82) -> tuple[str, int, int, Image.Image]:
    with Image.open(path) as raw:
        img = ImageOps.exif_transpose(raw).convert("RGB")
    small = img.copy()
    small.thumbnail((longest, longest), Image.LANCZOS)
    buf = io.BytesIO()
    small.save(buf, format="JPEG", quality=quality, optimize=True)
    uri = "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
    return uri, small.width, small.height, img


# --- scene assembly ------------------------------------------------------

TREATMENTS_1 = ["bleed", "float"]        # single-photo spreads alternate
TREATMENTS_2 = ["diptych", "companion"]  # two-photo spreads alternate


def build(workdir: Path, style: str) -> list[dict]:
    con = sqlite3.connect(f"file:{workdir / 'library.db'}?mode=ro", uri=True)
    src = {m: p for m, p in con.execute("SELECT media_id, path FROM media_source").fetchall()}
    con.close()

    # newest spec of the requested style
    best, best_m = None, -1.0
    for sc in glob.glob(str(workdir / "outputs" / "album" / "*.style.json")):
        d = json.load(open(sc))
        if d.get("style") == style and os.path.getmtime(sc) > best_m:
            best, best_m = d["album_id"], os.path.getmtime(sc)
    if best is None:  # fall back to the newest album spec of any style
        specs = [f for f in glob.glob(str(workdir / "outputs" / "album" / "*.json"))
                 if not f.endswith(".style.json")]
        best = Path(max(specs, key=os.path.getmtime)).stem
    spec = json.load(open(workdir / "outputs" / "album" / f"{best}.json"))

    cache: dict[str, dict] = {}

    def photo(media_id: str) -> dict | None:
        if media_id in cache:
            return cache[media_id]
        path = src.get(media_id)
        if not path or not os.path.isfile(path):
            return None
        uri, w, h, full = embed(path)
        cache[media_id] = {"src": uri, "w": w, "h": h, "pal": palette(full),
                           "portrait": h > w}
        return cache[media_id]

    scenes: list[dict] = []
    v1 = v2 = 0
    for i, page in enumerate(spec["pages"]):
        placements = page.get("placements", [])
        if not placements:
            continue
        # hero first
        placements = sorted(placements, key=lambda p: (not p.get("is_hero", False),))
        photos = [photo(p["media_id"]) for p in placements]
        photos = [p for p in photos if p]
        if not photos:
            continue
        if i == 0:
            scenes.append({"kind": "cover", "photos": photos})
            continue
        n = len(photos)
        if n == 1:
            kind = TREATMENTS_1[v1 % len(TREATMENTS_1)]; v1 += 1
            # every fourth single becomes a palette moment for rhythm
            if v1 % 4 == 0:
                kind = "palette"
        else:
            kind = TREATMENTS_2[v2 % len(TREATMENTS_2)]; v2 += 1
        scenes.append({"kind": kind, "photos": photos})
    return scenes


# --- html ----------------------------------------------------------------

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>__CSS__</style></head>
<body>
<main id="stage"></main>
<div id="progress"><span id="bar"></span></div>
<script>const SCENES = __DATA__; const META = __META__;</script>
<script>__JS__</script>
</body></html>"""

CSS = r"""
:root{ color-scheme: dark; }
*{ box-sizing:border-box; margin:0; padding:0; }
html{ scroll-behavior:smooth; }
body{ background:#0a0807; color:#efe7df;
  font-family:'Optima','Avenir Next',-apple-system,system-ui,sans-serif;
  -webkit-font-smoothing:antialiased; overflow-x:hidden; }
#stage{ scroll-snap-type:y proximity; }
.spread{ position:relative; min-height:100svh; scroll-snap-align:start;
  display:grid; place-items:center; padding:clamp(20px,5vw,72px);
  overflow:hidden; isolation:isolate; }
.spread::before{ content:""; position:absolute; inset:0; z-index:-2;
  background:
    radial-gradient(120% 90% at 50% -10%, var(--rise), transparent 60%),
    linear-gradient(178deg, var(--ground), var(--deep) 78%); }
.spread::after{ content:""; position:absolute; inset:0; z-index:-1; opacity:.5;
  background:radial-gradient(60% 50% at var(--gx,70%) var(--gy,30%),
    color-mix(in oklab, var(--glow) 55%, transparent), transparent 70%); }
.serif{ font-family:'Hoefler Text','Cormorant Garamond',Georgia,serif; }
img.ph{ display:block; object-fit:cover; border-radius:3px;
  box-shadow:0 30px 80px -30px rgba(0,0,0,.85),
    0 0 0 1px color-mix(in oklab, var(--light) 12%, transparent),
    0 24px 90px -40px color-mix(in oklab, var(--glow) 60%, transparent);
  will-change:transform,opacity; }
.reveal{ opacity:0; transform:translateY(26px) scale(.985);
  transition:opacity 1s cubic-bezier(.16,1,.3,1), transform 1.1s cubic-bezier(.16,1,.3,1); }
.reveal.in{ opacity:1; transform:none; }
.kb{ animation:kb 18s ease-out both; }
@keyframes kb{ from{ transform:scale(1.001);} to{ transform:scale(1.09);} }

/* cover */
.cover{ padding:0; }
.cover img{ position:absolute; inset:0; width:100%; height:100%; object-fit:cover;
  z-index:-2; }
.cover .scrim{ position:absolute; inset:0; z-index:-1;
  background:linear-gradient(180deg, color-mix(in oklab,var(--deep) 30%, transparent) 0%,
    transparent 30%, color-mix(in oklab,var(--deep) 85%, transparent) 100%); }
.cover .plate{ text-align:center; align-self:end; padding-bottom:clamp(40px,10vh,120px); }
.cover .eyebrow{ letter-spacing:.42em; text-transform:uppercase; font-size:.72rem;
  color:var(--light); opacity:.85; margin-bottom:1.1rem; }
.cover h1{ font-size:clamp(3.2rem,13vw,9rem); line-height:.92; font-weight:500;
  letter-spacing:-.01em; }
.cover .rule{ width:64px; height:2px; margin:1.6rem auto 1.1rem;
  background:var(--accent); }
.cover .dates{ font-size:clamp(.9rem,2.4vw,1.15rem); letter-spacing:.2em; opacity:.9; }

/* bleed: one photo, edge to edge, held by its own glow */
.bleed .frame{ position:absolute; inset:clamp(14px,3.2vw,46px); }
.bleed img{ width:100%; height:100%; }
.bleed .cap{ position:absolute; left:clamp(22px,4vw,60px); bottom:clamp(20px,4vw,56px);
  font-size:.74rem; letter-spacing:.28em; text-transform:uppercase; color:var(--light);
  opacity:.8; z-index:2; text-shadow:0 2px 20px rgba(0,0,0,.7); }
.bleed .no{ position:absolute; right:clamp(22px,4vw,60px); top:clamp(20px,4vw,56px);
  font-size:.72rem; letter-spacing:.3em; color:var(--light); opacity:.55; z-index:2;
  font-variant-numeric:tabular-nums; }

/* float: portrait lifted off a coloured ground with a halo */
.float img{ max-height:78svh; max-width:min(86vw,540px); }
.float .halo{ position:absolute; width:60vmin; height:60vmin; z-index:-1; border-radius:50%;
  background:radial-gradient(closest-side, color-mix(in oklab,var(--glow) 65%, transparent), transparent);
  filter:blur(10px); }

/* diptych: two on a shared ground, thin accent seam */
.diptych{ }
.diptych .row{ display:flex; gap:clamp(10px,1.6vw,22px); align-items:stretch;
  height:min(80svh,760px); max-width:1200px; width:100%; justify-content:center; }
.diptych .row img{ height:100%; flex:1 1 0; min-width:0; }
.diptych .seam{ width:2px; background:var(--accent); opacity:.7; align-self:stretch; }

/* companion: a dominant image + a smaller one over an accent block */
.companion{ }
.companion .wrap{ display:grid; gap:clamp(14px,2vw,28px);
  grid-template-columns:1.55fr .95fr; align-items:center;
  max-width:1180px; width:100%; }
.companion .hero img{ width:100%; max-height:82svh; }
.companion .aside{ position:relative; }
.companion .aside::before{ content:""; position:absolute; inset:-14% -18% -14% 6%;
  background:linear-gradient(160deg, var(--accent), transparent 75%); opacity:.28;
  border-radius:4px; z-index:-1; }
.companion .aside img{ width:100%; max-height:56svh; }
@media (max-width:720px){ .companion .wrap{ grid-template-columns:1fr; }
  .diptych .row{ flex-direction:column; height:auto; } .diptych .seam{ display:none; } }

/* palette moment */
.palette .wrap{ display:grid; grid-template-columns:1.2fr .5fr; gap:clamp(18px,3vw,48px);
  align-items:center; max-width:1100px; width:100%; }
.palette img{ width:100%; max-height:78svh; }
.palette .side .say{ font-size:clamp(1.1rem,2.6vw,1.7rem); line-height:1.35; font-style:italic;
  color:var(--light); }
.palette .chips{ display:flex; gap:10px; margin-top:1.6rem; }
.palette .chips i{ width:34px; height:34px; border-radius:50%; display:block;
  box-shadow:inset 0 0 0 1px rgba(255,255,255,.12); }
@media (max-width:720px){ .palette .wrap{ grid-template-columns:1fr; } }

#progress{ position:fixed; left:0; top:0; height:3px; width:100%; z-index:9;
  background:transparent; }
#bar{ display:block; height:100%; width:0;
  background:linear-gradient(90deg, var(--pAccent,#c9a24a), var(--pGlow,#e8c877)); transition:width .1s linear; }
@media (prefers-reduced-motion:reduce){ .reveal{ transition:none; opacity:1; transform:none; }
  .kb{ animation:none; } html{ scroll-behavior:auto; } }
"""

JS = r"""
const stage = document.getElementById('stage');
const setVars = (el, p) => { el.style.setProperty('--deep',p.deep); el.style.setProperty('--ground',p.ground);
  el.style.setProperty('--rise',p.rise); el.style.setProperty('--accent',p.accent);
  el.style.setProperty('--glow',p.glow); el.style.setProperty('--light',p.light); };
const img = (ph, cls) => { const i=document.createElement('img'); i.src=ph.src; i.className='ph '+(cls||'');
  i.loading='lazy'; i.decoding='async'; return i; };
const total = SCENES.length;
SCENES.forEach((sc, idx) => {
  const s = document.createElement('section'); s.className='spread '+sc.kind;
  const lead = sc.photos[0]; setVars(s, lead.pal);
  s.style.setProperty('--gx', (25+ (idx*37)%60)+'%'); s.style.setProperty('--gy',(20+(idx*23)%55)+'%');
  const no = String(idx).padStart(2,'0');
  if(sc.kind==='cover'){
    s.classList.add('cover');
    const i=img(lead,'kb'); s.appendChild(i);
    const scrim=document.createElement('div'); scrim.className='scrim'; s.appendChild(scrim);
    s.insertAdjacentHTML('beforeend',
      `<div class="plate reveal"><div class="eyebrow">${META.eyebrow}</div>`+
      `<h1 class="serif">${META.title}</h1><div class="rule"></div>`+
      `<div class="dates serif">${META.dates}</div></div>`);
  } else if(sc.kind==='bleed'){
    const f=document.createElement('div'); f.className='frame reveal'; const i=img(lead,'kb'); f.appendChild(i); s.appendChild(f);
    s.insertAdjacentHTML('beforeend',`<div class="no">${no} / ${String(total-1).padStart(2,'0')}</div>`);
  } else if(sc.kind==='float'){
    const halo=document.createElement('div'); halo.className='halo'; s.appendChild(halo);
    const i=img(lead,'reveal'); s.appendChild(i);
  } else if(sc.kind==='diptych'){
    const row=document.createElement('div'); row.className='row reveal';
    row.appendChild(img(sc.photos[0]));
    const seam=document.createElement('div'); seam.className='seam'; row.appendChild(seam);
    row.appendChild(img(sc.photos[1]||sc.photos[0])); s.appendChild(row);
  } else if(sc.kind==='companion'){
    const w=document.createElement('div'); w.className='wrap reveal';
    const h=document.createElement('div'); h.className='hero'; h.appendChild(img(sc.photos[0])); w.appendChild(h);
    const a=document.createElement('div'); a.className='aside'; a.appendChild(img(sc.photos[1]||sc.photos[0])); w.appendChild(a);
    s.appendChild(w);
  } else if(sc.kind==='palette'){
    const w=document.createElement('div'); w.className='wrap reveal';
    const im=document.createElement('div'); im.appendChild(img(lead)); w.appendChild(im);
    const side=document.createElement('div'); side.className='side';
    const chips=(lead.pal.swatches||[]).map(c=>`<i style="background:${c}"></i>`).join('');
    side.innerHTML=`<div class="say serif">Its own colours, drawn back into the page.</div><div class="chips">${chips}</div>`;
    w.appendChild(side); s.appendChild(w);
  }
  stage.appendChild(s);
});
// reveal on view
const io=new IntersectionObserver((es)=>es.forEach(e=>{ if(e.isIntersecting){ e.target.classList.add('in');
  io.unobserve(e.target);} }),{threshold:.18});
document.querySelectorAll('.reveal').forEach(el=>io.observe(el));
// tint the progress bar to the spread in view + progress
const root=document.documentElement;
const spreads=[...document.querySelectorAll('.spread')];
const tint=new IntersectionObserver((es)=>es.forEach(e=>{ if(e.isIntersecting){
  const cs=getComputedStyle(e.target); root.style.setProperty('--pAccent',cs.getPropertyValue('--accent'));
  root.style.setProperty('--pGlow',cs.getPropertyValue('--glow')); } }),{threshold:.5});
spreads.forEach(s=>tint.observe(s));
const bar=document.getElementById('bar');
addEventListener('scroll',()=>{ const h=document.body.scrollHeight-innerHeight;
  bar.style.width=(100*scrollY/(h||1))+'%'; },{passive:true});
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
    size_mb = out.stat().st_size / 1e6
    print(f"wrote {out} ({len(scenes)} spreads, {size_mb:.1f} MB)")


if __name__ == "__main__":
    main()
