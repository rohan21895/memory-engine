# Model registry

Every model weight the system may load, with its licence audit. Claude owns the contents; Codex owns the loader (`workers/ml-runtime`).

**A model that is not in this table cannot be loaded.** That is the whole point of a registry: swapping a model becomes a config change gated by the eval harness, and a weight nobody audited cannot reach a user by accident.

---

## Read this before using the audit column

The `Licence` and `Assessment` columns are an **engineering desk assessment**, not legal advice, and not verified. I read the licence terms as commonly published for each project; I have not checked the live licence text for any of them, licences change, and several of these projects licence their *code* and their *weights* differently — which is exactly the trap this document exists to catch.

Nothing here is cleared for commercial use until a human has checked the current terms and recorded it. The `Verified` column is the record of that, and it currently reads `no` for every row.

The distinction that matters most:

> **Permissive code does not mean permissive weights.** InsightFace's code is MIT while its pretrained models are published for non-commercial research use. A team that reads the repo licence and ships is exposed. This has bitten more products than every other item on this list combined.

**Status meanings**

| Status | Meaning |
|---|---|
| `LIKELY OK` | Desk assessment suggests commercial use is permitted. Needs verification before Phase 1 ships. |
| `BLOCKED` | Desk assessment says commercial use is **not** permitted. Must not enter the registry without a licence purchase or a replacement. |
| `MUST VERIFY` | Genuinely unclear, gated, or code/weights differ. Resolve before writing any integration against it. |

---

## Tier 1 — local perception stack

Runs on every file. Free, private, and on the critical path for every product.

| Job | Model / method | Runtime | Licence (as understood) | Assessment | Verified | Notes |
|---|---|---|---|---|---|---|
| Face detection | SCRFD (InsightFace) | ONNX | Code MIT; **pretrained weights stated non-commercial research** | **MUST VERIFY** | no | The single biggest landmine in Tier 1. Faces are core to the product, so this needs resolving early, not late. Options: licence from InsightFace, retrain SCRFD on a commercially-usable face dataset, or swap to YuNet (OpenCV Zoo, Apache-2.0) / RetinaFace variants with clean weights. |
| Face embedding | ArcFace-class (`buffalo_l`) or AdaFace | ONNX | Same InsightFace position as above | **MUST VERIFY** | no | Same resolution path. AdaFace weights have their own terms and need separate checking. A face-recognition stack we cannot licence is a product-level risk, not a component-level one. |
| Face landmarks / expression | 106-point landmark + expression head | ONNX | Bundled with InsightFace | **MUST VERIFY** | no | Inherits the same question. |
| Image embedding | SigLIP 2 (base / so400m) | ONNX / CoreML | Apache-2.0 | LIKELY OK | no | The highest-leverage model in the stack — one embedding powers search, dedupe refinement, diversity and zero-shot tagging. Good that it is also the cleanest licence. |
| Technical IQA | MUSIQ or TOPIQ | ONNX | MUSIQ Apache-2.0 (google-research); TOPIQ via IQA-PyTorch, Apache-2.0 | LIKELY OK | no | Check whether the specific checkpoint was trained on a dataset with research-only terms — the code licence does not cover that. |
| Classical quality | Laplacian blur, histogram exposure (OpenCV) | OpenCV | Apache-2.0 | LIKELY OK | no | No weights. The first-pass filter and nearly free. |
| Aesthetic score | LAION-aesthetics v2 predictor over SigLIP features | ONNX | MIT (predictor head) | LIKELY OK | no | Explicitly a *prior*, reweighted per user from PrefEvents. Q-Align is the stronger alternative but is research-licensed — treat as BLOCKED until checked. |
| Shot / scene detection | TransNetV2 | ONNX | MIT | LIKELY OK | no | Clean. Runs on the 480p proxy. |
| Optical flow / stability | RAFT-small | ONNX | BSD-3-Clause | LIKELY OK | no | Farnebäck (OpenCV, Apache-2.0) is the zero-risk fallback and is adequate for motion energy. |
| Audio embedding | CLAP (LAION) | ONNX | Apache-2.0 | LIKELY OK | no | |
| Audio event head | PANNs-class | ONNX | Commonly Apache-2.0 / MIT depending on checkpoint | MUST VERIFY | no | AudioSet-derived training data carries its own restrictions. Check the specific checkpoint. |
| Speech-to-text | faster-whisper (large-v3-turbo) | CTranslate2 | Code MIT; Whisper weights MIT | LIKELY OK | no | One of the cleanest positions in the stack. |
| Speech-to-text (Indic) | AI4Bharat IndicWhisper | CTranslate2 | Per-model on HF; commonly MIT/Apache | MUST VERIFY | no | First-class requirement for the Indian market, so resolve rather than defer. |
| Diarization | pyannote pipeline | PyTorch → ONNX | Code MIT; **pretrained pipelines gated, terms per-model** | **MUST VERIFY** | no | Requires accepting conditions on HF and some pipelines restrict commercial use. Films need "who is speaking"; plan a fallback. |
| Beat / tempo / downbeat | librosa onset + beat tracking | CPU | **ISC** | LIKELY OK | no | The safe choice, and the reason the contract pins the analyser in `BeatGrid.analyzer`. |
| Beat (higher accuracy) | madmom | CPU | **BSD with a non-commercial clause / CC BY-NC-SA on models** | **BLOCKED** | no | Named in the build plan. Do not ship. Beat accuracy is the difference between a reel that lands and one that does not, so if librosa proves insufficient the answer is a licensed alternative or our own model — not madmom. |
| Audio analysis (alt) | Essentia | CPU | **AGPL-3.0** | **BLOCKED** | no | AGPL is incompatible with shipping a proprietary desktop app. A commercial licence exists; buying it is a business decision, not an engineering one. |
| Subject segmentation & tracking | SAM 2 | ONNX / CoreML | **Apache-2.0** | LIKELY OK | no | Powers vertical reframing with subject lock — core to reels — and is cleanly licensed. |
| Saliency | U²-Net | ONNX | Apache-2.0 | LIKELY OK | no | |
| OCR / screenshot detection | PaddleOCR | ONNX | Apache-2.0 | LIKELY OK | no | Screenshots and documents are auto-excluded from memories. |
| NSFW / sensitive filter | Open classifier over SigLIP features | ONNX | Per-classifier | MUST VERIFY | no | Excluded-by-default content. Whatever ships must be auditable — a false negative here is a user-visible harm, not a quality issue. |

### Tier 1 summary

- **2 BLOCKED**: madmom, Essentia. Both were flagged in the build plan; both confirmed here.
- **7 MUST VERIFY**, of which **3 are the InsightFace face stack** — detection, embedding and landmarks. That is the most serious finding in this audit, because faces are load-bearing for albums, films and the person-labeling UI, and the code licence looks permissive while the weights do not.
- Everything else is a desk-level LIKELY OK.

---

## Tier 2 — local reasoning (optional download)

| Job | Model | Runtime | Licence | Assessment | Verified |
|---|---|---|---|---|---|
| Captioning, sanity checks, rough ordering | Qwen2.5-VL-7B class, quantized | llama.cpp / MLX | Qwen licence (Apache-2.0 for most sizes; check the exact variant) | MUST VERIFY | no |

A checkbox for users who want zero cloud, not the default path.

---

## Tier 3 — frontier multimodal (cloud, opt-in)

No weights, so no weights licence — but two obligations that are just as binding:

- **The provider's terms** govern commercial use, data retention and training-on-inputs. Whichever provider ships must be recorded here with those terms.
- **The privacy contract** governs what may be sent: low-res contact sheets and transcripts only, opt-in per job, with a `ConsentRef` in the ledger. Enforced in `JobSpec.egress` and by the CI egress test — see `contracts/schemas/job-spec.schema.json`.

---

## Enhancement stack

Used for album "best version" editing. Proportionally the most contaminated area.

| Job | Approach | Licence | Assessment | Verified | Notes |
|---|---|---|---|---|---|
| Upscale / restoration | Real-ESRGAN | BSD-3-Clause | LIKELY OK | no | The baseline. Good enough for most print upscaling. |
| Upscale (premium GPU) | SUPIR-class diffusion | Non-commercial; depends on a base model with its own terms | **BLOCKED** | no | Two licences to clear, not one. |
| Face restoration | **CodeFormer** | **S-Lab License 1.0 — non-commercial** | **BLOCKED** | no | Named in the build plan. Must never ship by accident. GFPGAN (Apache-2.0) is the nearest drop-in, though some variants carry StyleGAN2 components with their own terms — verify the exact checkpoint. |
| Outpainting (aspect-fit without cropping heads) | **FLUX.1-dev family** | **Non-commercial** | **BLOCKED** | no | Named in the build plan. Alternatives: FLUX Pro via API (a service, not weights), SDXL-inpaint derivatives under CreativeML Open RAIL++-M (which has its own use restrictions worth reading), or a licensed vendor. |
| Relight / exposure & WB, spread-level harmony | Classical + learned colour transfer | In-house | CLEAR | n/a | No third-party weights. Spread-level harmony is a differentiator no consumer tool ships. |
| Auto-crop / reframe | Saliency + face boxes + rule-of-thirds solver | In-house | CLEAR | n/a | Core IP. |
| Denoise (low light) | Learned denoiser | Per-model | MUST VERIFY | no | |

`AlbumSpec.EnhancementOp.license_cleared` is a **required boolean** in the contract, and an op whose model has not passed this audit blocks export. That is deliberate: an unlicensed enhancement ships inside a physical book, and a book cannot be patched after it is in the post.

---

## Registry entry format

Every model in the registry carries, per build plan §3:

| Field | Why |
|---|---|
| `model_id`, `version` | Stable identity |
| `weights_blake3` | The actual bytes. "The same version" of a HuggingFace repo has changed weights under people before — a record produced by an unpinned model is not reproducible, and reproducibility is the product. |
| `config_blake3` | The other half of the same guarantee. Input size, mean/std/scale, score threshold, NMS IoU, alignment template and detection cap all live in the config, and changing any of them changes every downstream decision while the weights hash stays byte-identical. Computed over a canonical serialisation by `models/policy/digest.py`, so reformatting a config is not a behaviour change. |
| `task` | What it is for |
| `licence`, `licence_url`, `licence_verified_at`, `licence_verified_by` | The audit trail. `licence_verified_at` being null means unaudited, and unaudited means unloadable. |
| `runtime_targets` | ONNX / CoreML / DirectML / CUDA / CTranslate2 |
| `preprocessing`, `postprocessing` | Exact spec, so a swap cannot silently change the input distribution |
| `eval_scores` | Against our benchmark libraries |
| `rollout_state` | `candidate` → `shadow` → `default` → `deprecated` |

`ModelRef` in `contracts/schemas/common.schema.json` already pins `model_id`, `version`, `weights_blake3`, `runtime` and `precision` on every score the system produces, so "why is this photo ranked 0.82" stays answerable after a model swap. `ModelPin` in `contracts/proto/ml_runtime.proto` is that same record plus `config_blake3`, and `contracts/tests/test_ml_runtime_proto.py` asserts the two stay field-for-field identical rather than leaving it to be maintained by hand.

### Steps that are not models

`photo_analysis` starts with `classical_quality`, which has no weights, no host and no runtime target — Laplacian variance, histogram entropy, Immerkaer noise, luma standard deviation, computed on the 512px thumbnail. It is still a registry entry, in `registry.classical_steps`, and it carries the same kind of pins a model does:

| Field | Why |
|---|---|
| `entry_point` | `module:callable`. Issue #42 was opened because a pipeline named a step that nothing could dispatch. |
| `version` | The pinned algorithm version. Scores from two versions are **not comparable** and must not be ranked against each other, so it is written into each score's `run_id`. |
| `input_proxy_kinds` | Laplacian variance is resolution-dependent: the same photo at 6000px and at 512px gives different numbers, and a library measured at a mix of both cannot be ranked. The executor refuses a rendition it was not calibrated for rather than rescaling. |
| `writes` | The contract paths it is responsible for. `MediaRecord` marks these required, so a step that silently wrote none of them would leave records that look analysed and hold no measurements. |
| `calibration` | Every constant that sets a half-way point, so a recalibration is a reviewable diff in the registry and can be gated the way a model swap is. `services/pipeline`'s tests fail if the module and this list disagree **in either direction** — including a constant added to the code and never declared. |
| `license` | Recorded rather than assumed. "It is only arithmetic" is exactly the reasoning that lets a copied GPL implementation in unnoticed. |

Declaring them also closed a fail-open. The licence and pin checks skip any step with no model config, and while "no model config" and "classical step" were indistinguishable, a **typo'd model id** in a release pipeline skipped the licence gate silently — the gate read as passing when it had never run. A step is now either a registered model or a declared classical step, and anything else is a registry error.

### Why the config digest is not optional provenance

This is not hypothetical. The SCRFD/ArcFace preprocessing defect Codex found applied the `1/128` scale twice — once as `scalefactor` and again via `mean`/`std` — collapsing the whole 0–255 input range into a 0.016-wide sliver where black mapped to −0.9961 and white to −0.9805. It touched no weights byte, it never raised, and it would have produced quietly wrong embeddings for as long as nobody looked. A provenance record that could not have distinguished before from after is not a provenance record.

So a config digest mismatch is **always fatal, in every mode**, exactly like a weights mismatch — and in practice it is the likelier of the two, because weights are downloaded once and never touched while thresholds get tuned by hand. Restamp deliberately after a real change:

```bash
python3 models/policy/digest.py --write
```

---

## How a model enters the registry

1. **Licence audit first.** Read the licence for the *weights*, not just the repo. Record the URL and the date. If code and weights differ, record both.
2. **Record the weights hash.** Download once, hash, pin.
3. **Write the pre/post-processing spec.** A swap that silently changes normalisation is a swap that silently changes every score.
4. **Benchmark on the eval libraries** — Indian weddings, festivals, GoPro/adventure, drone, baby/family, travel.
5. **Shadow-run** against the current default and diff the outputs.
6. **Promote** only if the eval harness shows no regression on face precision or reel acceptance.

Steps 4–6 are the eval harness's job (`packages/eval-harness`, Phase 6). Steps 1–3 are the gate that applies from day one.

### Running step 6

The gate is a command, and CI runs it over every committed gate file
(`.github/workflows/ci.yml`, job `Test`):

```bash
cd packages/eval-harness
python3 -m memory_engine_eval.harness gates/*.gate.json
```

It answers with a number, and three of the four numbers are failures:

| Exit | Meaning |
|---|---|
| 0 | Pass — the comparison ran and nothing failed. |
| 1 | Fail — a measured regression. Fix the model, move the baseline deliberately, or waive one case. |
| 2 | **Refused** — no comparison exists (mismatched digests, mixed model sets, unpinned weights, different inputs). Not a quality signal, not waivable. |
| 3 | Unusable — the gate file is malformed or unreadable. Nothing was measured. |

2 is separate from 1 for the same reason `models/policy/digest.py` returns 2
rather than 1: "I could not check" and "I checked and it got worse" call for
different actions, and a refusal that arrives as a FAIL gets waived like one.
See `packages/eval-harness/gates/README.md` for the gate-file format.

---

## Getting the weights (step 2, as a command)

`models/weights/` is populated by a script, not by hand:

```bash
python3 scripts/models/fetch_weights.py            # everything not a placeholder
python3 scripts/models/fetch_weights.py --only yunet-2023mar
```

It reads `models/registry.json`, downloads each entry's `weights.source_url`,
refuses to install anything that disagrees with `weights.blake3`, and prints the
digest of anything not yet pinned. Refusal is the same decision the load gate
makes: it calls `models/policy/load_gate.decide_load` rather than holding a
second opinion about integrity.

| Exit | Meaning |
|---|---|
| 0 | Every model in scope is installed **and** verified against its pin. |
| 3 | Partial — something was fetched but is unpinned, or something was unavailable. Work remains. |
| 4 | Failure — a pin mismatched, a download was not the artifact it claimed to be, or nothing at all was obtained. |
| 5 | Could not run — `blake3` missing, registry unreadable. |

A fetched-but-unpinned model exits 3, never 0. An unpinned model is not
reproducible, which is the problem the script exists to solve, so it must not
share an exit code with a verified fetch.

**The script never writes a pin.** It prints the digest for a human to paste. A
pin the fetcher generated from its own download certifies only that it hashed
the bytes it just wrote — it would be true of a truncated file or of a
maintainer having replaced the artifact, which is the exact thing a pin exists
to detect.

### What a real run produced — 2026-08-17

| Model | Result | Bytes | BLAKE3 (measured, **not** pinned) |
|---|---|---|---|
| `yunet-2023mar` | fetched | 232,589 | `3d5938c4cd5a02dc416f1cd1f7fc1f662a22adc370477112c871954587e63431` |
| `scrfd-10g-bnkps` | fetched (`det_10g.onnx` from `buffalo_l.zip`) | 16,923,827 | `fa3e5d8c62722a7b7122dc185245236589dcf0b3b5c9c5561cc742ce356fba56` |
| `arcface-buffalo-l` | fetched (`w600k_r50.onnx` from `buffalo_l.zip`) | 174,383,860 | `c9cc033a308d5cbe0006b8f2d695f13fe716c985cc4b676ad5c0a20a497a07cc` |
| `siglip2-so400m-384` | **unavailable** | — | — |
| `transnetv2` | **unavailable** | — | — |
| 4 placeholders | skipped | — | — |

All three fetched files were then loaded in `onnxruntime` and their real graphs
compared against their configs — see the defects below.

**Corroboration.** YuNet is the only one with independent confirmation: it is a
git-LFS object, so the pointer committed in `opencv/opencv_zoo` states the
SHA-256 (`8f2383e4dd3cfbb4…`) and size of the real file, and the download
matches both. The fetcher checks this automatically and treats a disagreement as
fatal. `buffalo_l.zip` publishes no checksum of any kind, so the SCRFD and
ArcFace digests are corroborated by nothing but the download itself — which is
one more reason a human, not the script, decides whether they become pins.

**What pasting these in would achieve, measured rather than assumed.** With the
YuNet digest temporarily pinned, the load gate's release-mode refusal moved from
`HASH_UNPINNED` to `LICENSE_UNVERIFIED`. So pinning removes reproducibility as a
release blocker and leaves the licence audit (issue #3, the `Verified` column
above) as the remaining one. The pin was then reverted; the configs on this
branch are still `blake3: null`.

### What is not reachable, and why

- **`siglip2-so400m-384`** — `google/siglip2-so400m-patch14-384` publishes
  safetensors only. There is no ONNX vision export at the declared source, so
  the entry reports unavailable rather than the fetcher saving a web page as a
  model. The repository is **not gated** (`gated: false` from the HF API on
  2026-08-17), so a token is not the obstacle. The two real options — export the
  vision tower ourselves, or adopt `onnx-community/siglip2-so400m-patch14-384-ONNX`
  — both change the config: that conversion exposes `last_hidden_state` /
  `pooler_output` where our config binds `image_embeds`, and its fp32 tower is
  1.7 GB against our declared fp16. That is an eval-harness decision, not a URL
  edit. **This blocks the whole search/dedupe/diversity path**, which is the
  highest-leverage model in the stack.
- **`transnetv2`** — ships a TensorFlow SavedModel and a PyTorch conversion
  *script* with no checkpoint, and has no GitHub releases. Nothing named
  `transnetv2.onnx` exists to fetch; we must export and pin our own.
- **The four placeholders** are skipped by default: no checkpoint has been
  chosen, and the load gate refuses `rollout.state: placeholder` in every mode.

### Gated repositories: implemented, never executed

The fetcher sends `Authorization: Bearer $HF_TOKEN` to `huggingface.co` and
distinguishes "no token set" from "token set and refused" in its report, and it
strips the header when a redirect crosses to another host so a CDN never
receives the token. **No token exists in this environment and no entry in this
registry is gated today, so that path has never been run.** The tests cover the
error-message mapping and the redirect stripping; they do not establish that
fetching a gated repository works. Treat it as unverified code.

### Defects the fetch exposed

Having the weights on disk for the first time made three checks possible that
had never been run:

1. **`arcface-buffalo-l` could never have loaded.** Its config declared the
   output name `embedding`; the real `w600k_r50.onnx` names its single output
   `683`. `ml-runtime` binds outputs by exact name and refuses with
   `CONFIG_MISMATCH: configured model output is absent from the weights`. This is
   the same defect Codex found in SCRFD (issue #36) — fixed there, never checked
   here. Corrected against the real graph.
2. **`yunet-2023mar` described a model that does not exist.** Declared output
   shapes were rank-2 `[-1, C]`; the checkpoint emits rank-3 `[1, N, C]`.
   Batching claimed `supported: true, max_batch: 8, dynamic_axes: true`; the
   input is a static `[1, 3, 640, 640]` and a batch of 2 is refused outright.
   Corrected, and confirmed by running the graph.
3. **Two `source_url`s could not have produced a file.** `arcface-buffalo-l`
   pointed at the InsightFace `model_zoo` tree page, and `yunet-2023mar` at the
   OpenCV Zoo directory page — whose raw URL serves a **131-byte git-LFS
   pointer**, not a model. A fetcher without a content check would have
   installed that pointer as `face_detection_yunet_2023mar.onnx` and printed a
   perfectly plausible digest for it. Both now point at real artifacts, and
   `weights.archive_member` replaces the member name that used to be smuggled
   into the SCRFD URL as a parenthetical.

ArcFace keeps `shape: [-1, 512]` despite the graph's output metadata saying
`{1, 512}`: the input's batch axis is dynamic and a real batch of 4 returns
`(4, 512)`. The export's static output shape is stale metadata, and copying it
into the config would have declared a batching limit the model does not have.
Measured, not read off the graph.

---

## Decision: the face stack, taken 2026-08-16

**Selected: SCRFD (detection) + ArcFace `buffalo_l` (recognition), InsightFace.**

Chosen on accuracy. The product is being built for internal use, the bar is
industry-leading output, and these are the best available. Licensing is
deliberately deferred.

**What that costs, stated plainly:** their weights are published for
non-commercial research use, so this stack cannot ship commercially as it
stands. That is a known, accepted position — not an oversight.

**What stops it becoming permanent by accident:**

- Both entries keep `blocks_commercial_release: true`.
- `photo_analysis` and `video_analysis` declare `min_load_mode: development`, so
  release mode refuses them by construction rather than by anyone remembering.
- `photo_analysis_release` keeps the licence-clean path (YuNet) working, and a
  test asserts a release-ready pipeline still exists. If that path rots, CI
  fails — the swap stays mechanical instead of becoming archaeology.

**The unsolved half.** Detection has a clean answer (YuNet, MIT). Recognition
does not: dlib's ResNet is public domain and a real candidate, but it trails
ArcFace, and most alternatives are trained on retracted datasets
(MS-Celeb-1M, VGGFace2). Whenever commercial release comes onto the table, this
is the piece that needs real work — see issue #3.

**Why the accuracy gap is survivable when it comes.** The contract splits
`cluster` from `identity` from `eligible_for_automated_output`. A weaker
embedding does not put wrong faces in albums; it puts more faces below the
threshold, which surfaces as extra review-queue work. The cost lands as human
labelling, not as a catastrophic failure — which is exactly what the
precision-first design was for.

---

## Actions before Phase 1

1. **Resolve the InsightFace position.** Blocking for albums, films and person labeling. Licence it, replace it, or train a replacement — and decide now, because every week of integration against it raises the cost of switching.
2. **Confirm madmom and Essentia are excluded from every dependency path**, including transitively. `BeatGrid.analyzer` records which analyser produced a grid so a non-commercial one cannot slip in unnoticed.
3. **Decide the music licensing model** — catalogue deal, CC library, or generated score. Build plan §6 calls this a Phase 0 decision because it shapes the beat-sync design, and `MusicCue.license.cleared_for` already refuses to let a cut be shared under a licence that does not cover the destination.
4. **Fill the `Verified` column.** It is `no` everywhere. Nothing in this table has been checked by a human, and this document is only worth what that column says.
5. **Paste the three measured digests into their configs** (see *Getting the weights*), or decide not to and say why. Until then `scripts/models/fetch_weights.py` exits 3 and the release gate refuses all three with `HASH_UNPINNED`: a fresh clone gets whatever those URLs serve on the day, which is better than nothing on disk but is not reproducibility.
6. **Get a SigLIP 2 artifact.** Export the vision tower ourselves or adopt the community ONNX conversion — either way the config's `outputs` and `quantization` need revisiting and an eval run. Nothing in search, dedupe refinement, diversity or zero-shot tagging can run until this exists.
