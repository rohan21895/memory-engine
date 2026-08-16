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
| `task` | What it is for |
| `licence`, `licence_url`, `licence_verified_at`, `licence_verified_by` | The audit trail. `licence_verified_at` being null means unaudited, and unaudited means unloadable. |
| `runtime_targets` | ONNX / CoreML / DirectML / CUDA / CTranslate2 |
| `preprocessing`, `postprocessing` | Exact spec, so a swap cannot silently change the input distribution |
| `eval_scores` | Against our benchmark libraries |
| `rollout_state` | `candidate` → `shadow` → `default` → `deprecated` |

`ModelRef` in `contracts/schemas/common.schema.json` already pins `model_id`, `version`, `weights_blake3`, `runtime` and `precision` on every score the system produces, so "why is this photo ranked 0.82" stays answerable after a model swap.

---

## How a model enters the registry

1. **Licence audit first.** Read the licence for the *weights*, not just the repo. Record the URL and the date. If code and weights differ, record both.
2. **Record the weights hash.** Download once, hash, pin.
3. **Write the pre/post-processing spec.** A swap that silently changes normalisation is a swap that silently changes every score.
4. **Benchmark on the eval libraries** — Indian weddings, festivals, GoPro/adventure, drone, baby/family, travel.
5. **Shadow-run** against the current default and diff the outputs.
6. **Promote** only if the eval harness shows no regression on face precision or reel acceptance.

Steps 4–6 are the eval harness's job (`packages/eval-harness`, Phase 6). Steps 1–3 are the gate that applies from day one.

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
