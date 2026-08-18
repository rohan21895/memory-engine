# memory-engine

A private, local-first system that turns terabytes of raw photos and video into
finished memories: print-ready albums, short films, and social reels.

Local models do perception. A frontier multimodal model does taste — but only
ever on low-resolution contact sheets, and only ever returning structured
decisions against IDs it was shown. Rendering is deterministic. Original media
never leaves the device without a logged consent event.

## Status

Honest version, because a README that oversells is the same defect as a test
that passes vacuously.

**What runs end to end today.** One command takes a folder of photos and video
and produces a print-ready PDF/X-4 and a short reel:

```bash
python3 scripts/demo/make_library.py --out /tmp/library --seed 11
python3 scripts/demo/run_demo.py /tmp/library --workdir /tmp/work
```

`make_library.py` generates synthetic media, so the demo exercises the whole
chain without touching anyone's real photographs. It deliberately includes the
cases that break things: near-duplicate bursts, a photo with no EXIF date at
all, EXIF and filesystem dates that disagree, both GoPro chaptering
conventions, mixed frame rates including 29.97, a truncated file, and a
zero-byte one.

**What is not built.** No film planner yet — the pipeline says so rather than
relabelling a reel as one. No transcript backend, so no cut can be certified
word-safe; that is reported as *absent*, never as passing. No sensitive-content
classifier is wired (the model is chosen and audited in
`docs/safety-classifier-decision.md`, not yet implemented). Face identity
exists but nothing is eligible for automated output until someone measures a
calibration, which is the designed behaviour rather than a shortfall.

**What is unproven.** The chain from an AlbumSpec to paper is verified. The
*taste* of what goes on the page is not: the embedding model has no ONNX
weights yet, so photo selection currently runs on classical quality signals
alone. No 100k-item library has been scanned. No book has been printed.

## Layout

| | |
|---|---|
| `contracts/` | Nine JSON Schemas, codegen to Python/TypeScript/Rust, golden fixtures. Everything crossing a boundary starts here |
| `packages/` | The intelligence: `media-db`, `ranking-engine`, `album-engine`, `story-engine`, `face-identity`, `prompt-engine`, `eval-harness` |
| `workers/` | The machinery: `ingest`, `ml-runtime`, `render-print`, `render-video`, `video-analysis` |
| `services/pipeline` | The spine — a resumable, idempotent job runner over the above |
| `models/` | Registry, per-model configs, load gate, licence audit |

## Design rules that shaped everything

These are enforced in code, not just documented. They are in `CLAUDE.md` in
full; the short version:

- **The frontier model never sees raw files and never free-picks from the
  library.** It receives pre-filtered low-resolution candidates and returns
  structured JSON referencing IDs that were on the sheet.
- **Determinism.** Same EDL and same sources produce a byte-identical render;
  same AlbumSpec produces an identical PDF. Every creative decision lives in
  the plan, never in the renderer.
- **Precision over recall for faces.** A wrong person in a family album is a
  catastrophic failure, so automated output requires a calibrated
  high-confidence threshold and uncertain matches go to a review queue.
- **No silent anything.** No silent data loss, no silent upload, no silent
  model swap. A skip must never share an exit code with a pass.

## A note on how it was built

Two AI agents built this, reviewing each other's work. That arrangement exists
for one measured reason: **not one of the 40+ defects found in this project was
caught by the author of the code.** Every one was silent — plausible numbers,
no exception raised, tests green.

A representative sample, all reproduced before being fixed:

- A PDF renderer that re-wrapped a five-band CMYK buffer as four, so every row
  read at the wrong stride. Every PDF ever produced was geometrically and
  tonally wrong — and the print validator passed all of them, because it
  validated the *plan* and the renderer never executed it.
- Two face model configs bound to output tensor names that do not exist in the
  real graph, so neither could ever have loaded.
- A moment scorer that made every cut word-safe and then let a duration clamp
  move it back inside a spoken word.
- `face_id` specified as a hash "over a tuple" — which is not a byte string, so
  three writers produced three different ids, and the golden fixtures matched
  none of them.

The lesson that generalises: **verify the artifact, never the plan that
produced it.** Every layer above was individually green.

## Licence

None yet. Default copyright applies — readable, not reusable.
