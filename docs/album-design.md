# Album design — the finalized process

Status: **frozen approach.** Phase A shipped (planner 0.7.0). Phases B/C scoped
below. This document adjudicates two external briefs (the "Claude" and "ChatGPT"
stacks the owner supplied) plus an internal deep-research brief against the
architecture we already have, and records the decisions so we build once.

## The goal

Turn selected photos into a book that reads like a professional album designer
made it — not an auto-collage. Three explicit owner asks:

1. **Match professional album designers** (studio quality, not template-y).
2. **Reduce the whites as much as possible.**
3. **Users get multiple beautiful variations.**

## The one rule that decides everything

From `CLAUDE.md`: *all creative decisions live in the plan, never in the
renderer; same AlbumSpec = identical PDF.* So every idea below is judged by one
test: **can it be computed deterministically in the Python layout engine and
expressed as a static AlbumSpec** (photo → page → rect → crop → background)? If
yes, adopt it. If it requires intelligence in the renderer, reject it — the
renderer stays dumb.

## Adjudication: most of the "libraries to build" already exist here

Both external briefs are written for a **greenfield JS/Node product** and spend
most of their length recommending a stack we have already built. Reproduced
honestly so we don't rebuild it:

| External recommendation | What we already have |
| --- | --- |
| Deterministic server renderer (Skia Canvas) | `workers/render-print/` — TypeScript + `sharp`, deterministic |
| Color management (LittleCMS) | `workers/render-print/src/icc.ts` — ICC-aware |
| Print PDF / PDF-X / bleed (Ghostscript, Vivliostyle) | PDF/X-4 output; `pdf.ts`, `page.ts` |
| Bleed / trim / safe / gutter geometry | `layout.py::PageGeometry` — models all four in mm |
| Physical units + normalized crops | `layout.py` — mm frames + `NormBox` crops |
| Face-aware cropping ("never crop through a face") | `layout.py::face_safety`, cut-face / face-in-gutter gates |
| Image engine (resize / sharpen / EXIF) | `sharp` in render-print, PIL in the pipeline |
| Photo intelligence (quality / faces / saliency / dedupe) | the whole analysis→faces→ranking→pose pipeline |
| Album data as a JSON schema | the `AlbumSpec` contract |
| Print preflight / clearance | `clearance.ts`, `scripts/classify_album_clearance.py` |
| Interactive editor (Konva / Fabric) | out of scope here — that's an `apps/` web-editor concern, Codex territory, not the plan |

**Decision: keep our renderer and contract.** Adopting Konva/Skia/LittleCMS/
Ghostscript/Vivliostyle would be a rewrite in Codex's shipping territory, would
break determinism (Puppeteer/screenshot pipelines are explicitly the thing to
avoid — and we already avoid them), and buys nothing we lack. We do **not** add
`rectpack` or OR-Tools either: album layout is a proportional-area problem, not
a bin-packing problem, so those optimize the wrong objective.

## What the three briefs actually agree on — and we are adopting

Strip the stack recommendations away and all three converge on the same missing
*intelligence*. These are the gaps worth owning:

1. **Template families with semantic metadata**, not a fixed ladder. Each family
   carries `density`, `whitespace`, `drama`, `hero_strength`, and an orientation
   profile, and generates many concrete layouts from normalized-coordinate slots
   (`x,y,w,h` in 0–1, `importance`, `crop`, `preferred_orientation`). This is the
   external briefs' "template grammar / families" (#19–20) and the internal
   brief's BRIC slicing-tree families, same idea from two directions.
2. **Album rhythm / pacing.** A book is a story arc, not a uniform grid: open
   calm, alternate density, land hero moments full-bleed, breathe, close. Pacing
   is a spread-ordering decision over family metadata.
3. **A layout scoring engine.** Instead of "first template in the ladder that is
   print-safe", enumerate candidate arrangements and pick the best by a weighted
   score: crop quality, face safety (hard), aspect match, importance match,
   whitespace quality, visual balance, sequence rhythm, diversity.
4. **Non-white mats + full-bleed heroes** — the whole "reduce the whites" ask.
5. **Multiple variations** — seed `{hero choice, template family, pacing,
   background register}`, generate N specs, rank by the layout score, keep the
   top ~3 that are also maximally *distinct* (different hero set + family mix,
   not a reshuffle).

## The finalized design system

The layout engine gains four cooperating parts, all in `packages/album-engine`
and `services/pipeline` (our territory), all emitting the existing AlbumSpec
shape (no contract change unless we add page metadata — flagged for Codex if so):

- **Family grammar** — normalized-slot families with metadata, per photo-count
  (1–6). Generates concrete `RectMm` frames from the existing `PageGeometry`,
  reusing `cover_crop` / `face_safety` / DPI checks unchanged.
- **Layout score** — one function ranking candidate arrangements for a page; the
  greedy ladder becomes "score all reachable arrangements, keep the best".
- **Pacing sequencer** — orders spreads by family metadata into a rhythm curve.
- **Variation seed** — a small policy record `{register, pacing, hero_bias,
  mat}` that, fixed, makes the whole plan reproducible; N seeds → N AlbumSpecs.

## Build phases and status

- **Phase A — reduce the whites + own the hero. ✅ SHIPPED (planner 0.7.0).**
  Every page background was hardcoded `#ffffff`; the book sat prints on a slide.
  The mat is now a dark, muted tone drawn from the album's own palette
  (`classical.album_background_tone`: median average colour of the selected
  proxies → HLS → deep lightness, capped saturation, warm-charcoal fallback,
  never white). On the maternity set the eight formerly-white pages became
  `#251e18` (a warm near-black); full-bleed and blur-hero pages already own the
  page and are unchanged. Two knobs (`BACKGROUND_MAT_LIGHTNESS`,
  `BACKGROUND_MAT_MAX_SATURATION`) tune the treatment in one place.
- **Phase B — family grammar + layout score.** Replace the fixed ladder with
  scored candidate arrangements from metadata-tagged families. Biggest step from
  "auto-collage" to "designed". No contract change (same page shape).
- **Phase C — pacing + variations.** Rhythm sequencer over family metadata;
  N seeded variations ranked by the layout score, top-3 distinct emitted as
  separate AlbumSpecs; review UI gains a variation picker.

## Rejected / deferred (with the reason, so we don't relitigate)

- **Konva / Fabric / Skia Canvas / LittleCMS / Ghostscript / Vivliostyle /
  Paged.js** — redundant with our renderer + contract; adopting them is a
  rewrite in Codex territory that breaks determinism. A future web *editor*
  (Konva) is a separate `apps/` product decision, not part of the plan engine.
- **rectpack / OR-Tools / Cassowary** — wrong objective (packing / hard
  constraints) for a proportional-area layout. Revisit only if we add text
  panels or exclusion zones (exBRIC).
- **BRIC linear-area slicing-tree** — a strong *optional* backend for the family
  grammar's multi-photo pages; hand-authored families ship first (lower risk,
  fully predictable), BRIC added only if families run out of variety.
- **Spread-spanning panoramas / gutter-aware spreads** — pages are single-leaf
  today; note the vocabulary now, build when spreads land.

## Sources

Owner-supplied external briefs (Claude stack, ChatGPT stack) and the internal
deep-research brief (BRIC — Atkins, Blocked Recursive Image Composition, ACM MM
2008; Picture Collage, CVPR 2006; treemap layout family; SmartAlbums / Fundy /
MILK design principles). Reference implementations worth studying, not embedding:
ImmichPhotoBook, Fotobuch, Spacerat/photobook, Scribus.
