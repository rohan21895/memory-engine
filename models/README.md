# Model registry

Every model `workers/ml-runtime` may load. **A model not listed in `registry.json` cannot be loaded** — that is the point of a registry: swapping a model becomes a config change gated by the eval harness, and a weight nobody audited cannot reach a user by accident.

Claude owns the contents (these files). Codex owns the loader. The format is a cross-boundary interface, so it is schema-validated in CI.

```
models/
  registry.json                    index + pipeline ordering
  schema/model-config.schema.json  the format ml-runtime parses
  configs/*.json                   one per model
  tests/                           18 tests enforcing the rules below
```

## What ml-runtime needs to do

For each entry: load `weights.filename` on the first available `runtime_targets`, bind inputs by `preprocessing.input_name` and outputs by `outputs[].name`, apply `preprocessing` exactly, then `postprocessing.steps` in order.

**Preprocessing is not a suggestion.** Every field there changes the model's output, and a mismatch is silent — the model still returns plausible numbers. Three that bite hardest:

- **`color_order`.** InsightFace models want **BGR**; nearly everything else wants RGB. Getting this wrong costs recall on detection and produces subtly wrong embeddings.
- **`scale` / `mean` / `std`.** Applied in that order: `(pixel * scale - mean) / std`. TransNetV2 takes raw `[0,255]` with no normalisation and does its own internally — normalising it degrades it quietly.
- **`face_alignment`.** Recognition without alignment does not error. It returns a confidently wrong embedding, which clusters wrongly, which puts the wrong person in a family album. That is the catastrophic failure the whole precision-first design exists to prevent, and it starts here.

## The rules the tests enforce

| Rule | Why |
|---|---|
| Every config validates against the schema | ml-runtime binds by name and shape |
| Filename matches `model_id` | the index is then trivially checkable |
| Every config is in `registry.json` and vice versa | an unlisted config is unloadable; a listed-but-missing one breaks startup |
| Embeddings declare `l2_normalize` | every contract `VectorSpace` stores normalised vectors and cosine distance assumes it |
| Embedding dimensions match a contract `VectorSpace` | an embedding in an unknown space is unqueryable |
| Face-embedding models declare an alignment template sized to their input | see above |
| Score-producing models declare `normalisation` | fusion must never need to know a model's native range |
| Unpinned weights (`blake3: null`) stay `candidate` | a record from an unpinned model is not reproducible |
| Nothing claims `verified` without a date and URL | verification means a human read the licence, not that I inferred it |
| Restricted models set `blocks_commercial_release` | see below |

## Licence status: deferred, not ignored

Commercial licensing is **deliberately deferred** at the repository owner's direction so build velocity is not blocked — see [#3](https://github.com/rohan21895/memory-engine/issues/3). That is a legitimate stage of a real project.

What the registry does about it: every entry states its licence, and anything not cleared for commercial use carries `blocks_commercial_release: true` with a note explaining the replacement path. So the deferral stays a **decision someone can act on**, rather than something that ships by being forgotten.

Currently blocking a commercial release:

| Model | Position |
|---|---|
| `scrfd-10g-bnkps` | MIT code, non-commercial weights |
| `arcface-buffalo-l` | MIT code, non-commercial weights, trained on a WebFace260M derivative |

Both are InsightFace. Development use is fine; a commercial release with them is not. The candidate replacement for recognition is dlib's public-domain ResNet — and the architecture absorbs a weaker embedding gracefully, because `threshold_profile` plus the review queue turn lower accuracy into *more human labelling* rather than into wrong faces in albums.

`verified: false` on every entry. Nothing here has been checked by a human at the source.

## Adding a model

1. Read the licence for the **weights**, not just the repo. Record the URL and dataset provenance — a permissive model trained on a retracted dataset (MS-Celeb-1M, VGGFace2) is still exposed.
2. Download once, hash, set `weights.blake3`. Until then it stays `candidate`. Use `python3 scripts/models/fetch_weights.py`: it downloads from `weights.source_url`, refuses to install anything that disagrees with an existing pin, and prints the digest of anything unpinned for you to paste. It will not write the pin itself — a fetcher pinning its own download certifies nothing.
3. Write the pre/post-processing spec exactly. A swap that silently changes normalisation changes every score in the library, and nothing about it looks like a bug.
4. Benchmark on the eval libraries; shadow-run against the current default; promote only on no regression in face precision or reel acceptance.

Steps 1–3 apply from day one. Step 4 arrives with `packages/eval-harness`.

## Known gaps

- **Every entry is still unpinned** (`blake3: null`). YuNet, SCRFD and ArcFace now *fetch* reproducibly — `scripts/models/fetch_weights.py` gets them from real artifact URLs and their digests are recorded in `docs/model-registry.md` — but until someone pastes those digests in, a fresh clone gets whatever those URLs serve that day. SigLIP 2 and TransNetV2 have no downloadable artifact at their declared sources at all; see *Getting the weights* in `docs/model-registry.md`.
- **`laion-aesthetic-v2` will not work as configured.** The published head was trained on CLIP ViT-L/14 features; running it on SigLIP 2 features needs the linear head retrained. The entry is currently the *shape* of the config, not a working model.
- **Tensor names are from published exports** and may differ in a specific conversion. Codex should treat a name mismatch at load as a config bug and tell me, not work around it locally.
- Tier 1 is not complete: MUSIQ, SAM 2, CLAP, PaddleOCR, faster-whisper and the safety classifier are audited in `docs/model-registry.md` but have no config yet.
