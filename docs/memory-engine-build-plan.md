# Memory Engine — Full Build Plan (Claude × Codex)

**Product:** A private, local-first AI system that turns terabytes of raw photos and video into finished memories — print-ready albums, short films, and social reels — with near-zero human effort. Two agents build it: **Claude Code** owns everything that *decides* (intelligence, ranking, story, print logic, evals). **Codex** owns everything that *moves, renders, and ships* (apps, pipelines, render farm, backend, packaging). A frozen contract layer sits between them so both can build in parallel without stepping on each other.

---

## 1. System Architecture

Five layers, strictly separated:

**Ingest layer** walks filesystems, drives, and phone galleries; hashes, fingerprints, and proxies every file. **Analysis layer** runs the local ML stack against proxies and writes structured facts into the store. **Intelligence layer** reads those facts and produces decisions: rankings, selections, story plans, album layouts, edit decision lists. **Render layer** turns decisions into artifacts: PDFs, MP4s, vertical reels. **Surface layer** is what the user touches: desktop app, mobile app, web account.

The contract between layers is data, not code. Analysis never renders. Intelligence never decodes video. Render never makes creative choices — it executes a deterministic plan. This separation is what lets two agents build simultaneously and what makes every output reproducible.

All heavy work runs on the user's machine. Cloud is used for three things only: account/billing, opt-in frontier-model reasoning on low-res contact sheets, and opt-in heavy rendering. Original media never leaves the device without an explicit, logged consent event.

### 1.1 Monorepo layout and ownership

```
memory-engine/
  contracts/            # SHARED — frozen schemas, both agents consume, neither edits unilaterally
    schemas/            # JSON Schema: MediaRecord, Face, Moment, EDL, AlbumSpec, JobSpec, PrefEvent
    fixtures/           # golden test data both sides validate against
    codegen/            # generates TS + Python + Rust types from schemas
  packages/
    media-db/           # CLAUDE — SQLite schema, migrations, vector index, query API
    ranking-engine/     # CLAUDE — quality scoring, fusion, preference model
    story-engine/       # CLAUDE — film planner, reel planner, moment selection
    album-engine/       # CLAUDE — clustering, selection, layout solver, print validator
    prompt-engine/      # CLAUDE — VLM prompting, structured-output parsing, judgment calls
    eval-harness/       # CLAUDE — benchmark libraries, A/B tooling, regression gates
  workers/
    ingest/             # CODEX — file walking, hashing, EXIF, proxy generation
    ml-runtime/         # CODEX runtime + CLAUDE model configs — ONNX/CoreML execution host
    render-video/       # CODEX — FFmpeg EDL renderer, encode profiles
    render-print/       # CODEX — PDF composition from album-engine output
    enhance/            # CLAUDE pipeline logic, CODEX GPU execution host
  apps/
    desktop/            # CODEX — Tauri + Rust + React
    mobile/             # CODEX — Expo + React Native
    web/                # CODEX — account, orders, shared albums
  services/
    api/                # CODEX — auth, billing, sync, job orchestration
    admin/              # CODEX — ops dashboard, QA review queue
  docs/                 # CLAUDE — architecture, model cards, eval reports
```

**Rule of engagement:** `contracts/` changes require both agents to sign off (in practice: a PR that regenerates all three language bindings and passes both sides' golden tests). Everything else is single-owner.

---

## 2. The Contract Layer (built first, by both)

Seven schemas define the entire system. Freeze these before writing feature code.

**MediaRecord** — identity of one file: content hash, perceptual hash, path(s), EXIF/metadata, proxy references, processing state. **FaceRecord** — detection box, landmark set, embedding reference, cluster id, person id, confidence. **MomentRecord** — a scored time interval in a video: start/end (source timecode), feature vector (motion, audio, faces, speech), scores, snap points. **EDL** — the deterministic edit plan: ordered clips with source timecodes, transitions, audio plan, beat grid, crop/reframe keyframes, color ops. Rendering the same EDL twice produces identical output. **AlbumSpec** — pages, placements with crop boxes, enhancement ops per image, vendor profile reference, validation report. **JobSpec** — any unit of work: type, inputs by hash, parameters, resumption checkpoint, idempotency key. **PrefEvent** — one human reaction: kept/rejected/reordered/re-cropped/variant-picked, with the feature context at decision time. This is the training-data schema for the taste model; getting it right on day one is worth a week of argument.

EDL should be exportable to **OpenTimelineIO** — this is the film industry's interchange format, and supporting it means a pro user can open our auto-edit in DaVinci Resolve or Premiere and finish it by hand. That single feature is what "industry-leading" looks like to an editor, and it costs little because our EDL is already deterministic.

---

## 3. Model Strategy — what runs, where, and why

Three tiers. The design principle: **local models handle perception (the cheap 95%), frontier cloud models handle taste (the expensive 5%), and everything is swappable behind a registry.**

### Tier 1 — Local perception stack (runs on every file, free, private)

| Job | Model / method | Runtime | Notes |
|---|---|---|---|
| Face detection | SCRFD (InsightFace) | ONNX Runtime | Fast, accurate at small faces; runs on thumbnails first, full-res on demand |
| Face embedding | ArcFace-class (InsightFace buffalo_l) or AdaFace | ONNX | 512-d embeddings; cluster with HDBSCAN over cosine distance |
| Face landmarks / expression | 106-point landmark + lightweight expression head | ONNX | Eyes-open, smile, gaze; feeds face-quality score |
| Image embedding | SigLIP 2 (base or so400m) | ONNX / CoreML | One embedding powers search, dedupe assist, diversity, and zero-shot tags |
| Technical IQA | MUSIQ or TOPIQ + classical (Laplacian blur, histogram exposure) | ONNX + OpenCV | Classical methods are the first-pass filter — they're nearly free |
| Aesthetic score | Q-Align-style aesthetic head or LAION-aesthetics v2 over SigLIP features | ONNX | This is a *prior*, later reweighted per-user by the preference model |
| Shot/scene detection | TransNetV2 | ONNX | Runs on the 480p proxy |
| Optical flow / stability | RAFT-small or Farnebäck | ONNX / OpenCV | Motion energy + shake score per window |
| Audio events | CLAP embeddings + PANNs-style event head | ONNX | Laughter, cheering, splash, music vs speech vs noise |
| Speech-to-text | faster-whisper large-v3-turbo; Indic languages via AI4Bharat IndicWhisper variants | CTranslate2 | Transcripts feed moment scoring (names, exclamations) and speech-aware cutting |
| Diarization | pyannote pipeline | PyTorch→ONNX | Who is speaking, for family films |
| Beat / tempo / downbeat | librosa onset + a modern beat-tracking model | CPU | **License note:** madmom and Essentia have non-commercial/AGPL terms — verify before shipping; librosa (ISC) is safe |
| Subject segmentation & tracking | SAM 2 (Apache-2.0) | ONNX / CoreML | Powers vertical reframing with subject lock, and crop-safety in print |
| Saliency | U²-Net or SAM-derived | ONNX | Composition scoring + auto-crop |
| OCR / screenshot detection | PaddleOCR | ONNX | Screenshots and documents are auto-excluded from memories |
| NSFW / sensitive filter | Open safety classifier over SigLIP features | ONNX | Excluded-by-default, user can override per item |

**Runtime strategy:** ONNX Runtime everywhere as the baseline; CoreML execution provider on Apple silicon, DirectML on Windows, CUDA where present. The `ml-runtime` worker is a single host process that loads models from the registry, batches requests, and exposes a local gRPC interface — Codex builds the host, Claude owns the model configs and pre/post-processing code.

### Tier 2 — Local reasoning (mid-size VLM, optional download, still private)

A quantized **Qwen2.5-VL-7B-class vision-language model** (llama.cpp / MLX) for users who want zero cloud. Used for: captioning event clusters, sanity-checking album picks ("is anyone's eyes closed here that the landmark model missed?"), and rough story ordering. Slower and weaker than Tier 3 but keeps the full pipeline offline. This tier is a checkbox, not the default — most users take Tier 3 for premium jobs.

### Tier 3 — Frontier multimodal reasoning (cloud, opt-in, the taste layer)

Current frontier models (Claude, Gemini 2.x class) can watch a **contact sheet** — a grid of timestamped keyframes plus the transcript and the moment-feature summary — and make genuinely human-level judgment calls that no local stack can:

- **Narrative selection:** "Of these 40 candidate moments from a 6-day trip, pick 14 that tell the story: arrival, first wow, meals, the storm, recovery, farewell" — returned as structured JSON referencing moment IDs.
- **Emotional peak detection:** distinguishing "kid sees the ocean for the first time" from "kid standing near ocean." Feature vectors can't do this; VLMs can.
- **Album spread review:** render each spread at low res, ask for a structured critique (crop hits a face? colors clash? two near-identical shots?), fix, re-check. This is the automated QA pass that makes unattended output trustworthy.
- **Sequencing and pacing plan:** the model outputs a story arc (act structure, energy curve) that the reel/film planner then satisfies mechanically.

**Hard rule (this is the architecture's spine):** the frontier model never sees raw files and never free-picks from the library. It sees pre-filtered, low-res candidates retrieved by the local index, and it returns **structured decisions against IDs** that the deterministic planners execute. This keeps cost bounded (a full trip film costs cents, not dollars), keeps privacy legible (thumbnails only, consented, logged), and keeps output reproducible.

### Enhancement stack (album "best version" editing)

| Job | Approach | License caution |
|---|---|---|
| Upscale / restoration | Real-ESRGAN (BSD) baseline; diffusion-based restoration (SUPIR-class) as premium GPU path | SUPIR-class models: verify weights license |
| Face restoration | GFPGAN / CodeFormer class | **CodeFormer is non-commercial (S-Lab license)** — either license it, use an alternative, or train a replacement. Flagging now so it never ships by accident |
| Relight / exposure & WB normalization | Classical + learned color transfer; **spread-level harmony**: solve color/exposure jointly across facing pages | No consumer tool does spread-level harmony; visible instantly in print |
| Outpainting (aspect-fit without cropping heads) | Inpainting/outpainting diffusion model | **FLUX.1-dev family is non-commercial** — use FLUX Pro via API, SDXL-inpaint derivatives with clean licenses, or a licensed vendor |
| Auto-crop / reframe | Saliency + face boxes + rule-of-thirds constraint solver | Fully in-house, core IP |
| Denoise (low light) | Learned denoiser on GPU path | — |

**A standing task for Claude: license audit of every model weight before it enters the registry.** Half the popular restoration models are non-commercial. This is exactly the kind of landmine that kills a launch, and it's cheap to catch early.

### Model registry

Every model entry: id, version, task, weights hash, license, runtime targets, pre/post-processing spec, eval scores on our benchmarks, rollout state. Swapping a model is a config change gated by the eval harness — if reel-acceptance or face-precision regresses on the benchmark libraries, the swap doesn't ship. Claude owns the registry contents; Codex owns the loader.

---

## 4. Pipelines in detail

### 4.1 Ingest (Codex)

Walk sources (folders, drives, phone gallery via native modules, Google Takeout / iCloud export structures, WhatsApp directory conventions, GoPro/DSLR card layouts including spanned-file conventions like GoPro chaptered MP4s). For each file: BLAKE3 content hash → skip if known; extract EXIF/XMP/QuickTime metadata; compute perceptual hash (pHash for images, per-keyframe for video); generate a 512px thumbnail and, for video, a **single-pass 480p proxy with a frame-index sidecar** mapping proxy time → source timecode. Proxies are the only thing analysis ever touches; source files are read again only at final render. Everything is a resumable JobSpec: kill the app at 47% of a 3TB scan, relaunch, it continues. Content-addressing makes every step idempotent.

Throughput target: saturate disk I/O on the proxy pass, not CPU — hardware decode (VideoToolbox / NVDEC / QSV) mandatory. 200 hours of 4K should proxy overnight on an M-series laptop.

### 4.2 Photo analysis (Claude logic, Codex runtime)

Per image, in cost order: classical blur/exposure (rejects the junk for free) → SigLIP embedding → face detect/embed/landmarks → IQA + aesthetic heads → tags via zero-shot SigLIP → write MediaRecord facts. Near-duplicate clustering via pHash buckets refined by embedding distance; within each cluster, the ranking engine marks one primary. Face clusters via HDBSCAN, with an active-learning loop: the system asks the user to confirm the *most informative* merges first (cluster pairs near the decision boundary), so ten taps of labeling fixes a thousand photos.

### 4.3 Video analysis (Claude logic, Codex runtime)

On the proxy: TransNetV2 shot boundaries → per-shot: keyframe extraction (adaptive, more frames where motion/faces change), SigLIP embeddings per keyframe, optical-flow motion energy, shake score, face tracks, CLAP audio events, transcript with word timestamps. Then **moment scoring**: a sliding window over the fused feature stream, scored by a temporal model (start as a hand-weighted linear fusion — it's transparent and tunable; graduate to a small learned temporal head once PrefEvents accumulate). Local maxima become MomentRecords with snap points at motion onsets, audio onsets, and speech gaps — so cuts land on natural boundaries, never mid-word or mid-action.

Elimination first, always: shake, blown exposure, black frames, lens cap, pocket footage, zero-motion tripod dead time. On real GoPro libraries this discards 90–95% before any expensive analysis runs. It's the single biggest cost and quality lever in the system.

### 4.4 Reel planner (Claude)

Input: candidate moments, target duration (15/30/45s), a licensed music track. Pipeline: beat grid + downbeat detection → energy-curve template for the reel style (hook → build → peak → button) → assign strongest moment to the first second (the hook), strongest visual beats to downbeats → cut every 1.5–2.5s snapped to beats → vertical reframe via SAM 2 subject tracking with smoothed crop keyframes → EDL. Generate **3–5 variants** (different moment subsets, different pacing seeds); the user's pick is a PrefEvent. Tier 3 model optionally reviews the contact sheet of the chosen cut for narrative coherence before render.

### 4.5 Film planner (Claude)

Input: an event cluster (a trip, a birthday, a year). Tier 3 model receives the contact sheet + transcript summary + moment features, returns a story arc as structured JSON (acts, required beats, energy curve, suggested moments per beat). The planner then satisfies the arc mechanically: chronological within acts, speech-aware trimming (never cut mid-sentence — word timestamps make this exact), ambient audio preserved and ducked under music, longer holds (3–8s), gentle transitions. 1–3 minutes. The arc JSON is stored with the EDL, so "make it warmer / more of her / less drone" edits re-plan against the same arc instead of starting over.

### 4.6 Album engine (Claude)

Cluster by day × location × visual similarity → event labeling (Tier 3 on contact sheets: "beach day," "night market," "temple visit") → hero/supporting selection with hard diversity constraints (no near-duplicates on a spread; people/scenery/detail balance per section) → **layout as constraint solving**, not template filling: face-safe zones respect gutter and trim, every placement checked against the DPI floor *at its printed size*, bleed and spine allowance from the vendor profile → enhancement ops per image (restoration, spread-level color harmony, outpaint-to-fit where cropping would clip a head) → Tier 3 spread review pass → PDF.

**The print validator is a hard gate Codex enforces in the render worker:** a PDF with any page below DPI floor, any face in the trim zone, any bleed violation, or a mismatched color profile *cannot be exported*. Print failures are refunds; this gate is why we can promise unattended quality on the one artifact that can't be patched after shipping. One vendor first, built to their exact spec sheet; the vendor-profile abstraction generalizes later.

### 4.7 Render (Codex)

Video: EDL → FFmpeg filtergraph compiler → hardware-encoded outputs (H.264/HEVC profiles per destination: master, Instagram, YouTube). Deterministic: same EDL + same sources = bit-identical intent. Print: AlbumSpec → PDF/X compliant output with embedded ICC profile per vendor. Both renderers are dumb by design — every creative decision arrived in the plan.

### 4.8 Preference model (Claude)

Every accept/reject/reorder/re-crop/variant-pick lands as a PrefEvent with the feature context. Start with per-user reweighting of the score-fusion weights (transparent, trainable on tens of events); graduate to a small ranking model (pairwise, over embedding + feature inputs) per user, plus a global model trained on anonymized events — **feature vectors and decisions only, never pixels**, which keeps the flywheel compatible with the privacy promise. This is the compounding asset: a year in, the system's taste is trained on millions of real human keep/reject decisions that no incumbent has.

---

## 5. Division of Labor

### Claude Code owns (the deciding half)

Architecture and all schemas in `contracts/` (drafting; Codex co-signs). `media-db`: SQLite schema, migrations, FTS, vector index (sqlite-vec or usearch), the query API both sides call. `ranking-engine`: score fusion, dedupe-primary selection, diversity, preference model. `story-engine`: moment scoring, reel planner, film planner, EDL generation, beat-sync logic. `album-engine`: clustering, selection, layout solver, enhancement op planning, validator *rules* (Codex enforces them in render). `prompt-engine`: all Tier 3 prompting, contact-sheet composition, structured-output parsing, retry/fallback logic. `eval-harness`: benchmark library curation (Indian weddings, festivals, GoPro/adventure, drone, baby/family, travel), blind A/B tooling, regression gates wired into CI. Model registry contents, pre/post-processing code, quantization/conversion recipes, and the **license audit** of every weight. All architecture and model-card docs.

### Codex owns (the shipping half)

`ingest` worker: walkers, hashing, metadata extraction, proxy pipeline with hardware decode, source-specific adapters (Takeout, WhatsApp, GoPro chapters). `ml-runtime`: the model-hosting process, batching, ONNX/CoreML/DirectML/CUDA execution providers, gRPC interface. `render-video` and `render-print`: FFmpeg filtergraph compiler, encode profiles, PDF/X composer, validator enforcement. `enhance` GPU execution host. Desktop app (Tauri/Rust/React): library UI, person labeling, review/approve flows, variant picker, project editor, progress that survives crashes. Mobile app (Expo): capture-adjacent import, review and approvals, sharing, payments. Web app: account, orders, shared albums. `api`: auth, billing (Stripe + Razorpay), consent ledger, metadata sync, cloud job orchestration for opt-in Tier 3 and heavy renders. `admin`: job dashboard, QA review queue, consent-state viewer, failed-file diagnostics. CI/CD, code signing and notarization for desktop, crash reporting with privacy filtering, packaging and updates.

### Joint (explicitly shared)

The `contracts/` package and its golden fixtures. The local gRPC interface between intelligence packages and the ml-runtime. Integration tests that run full pipelines against the benchmark libraries in CI. Weekly contract-review: any schema change regenerates TS+Python+Rust bindings and must pass both sides' tests before merge.

### How the two agents actually work together

Contract-first, always: when a feature spans the boundary (e.g., reel variants), Claude specs the EDL extension and fixture files first, Codex builds the renderer against fixtures, Claude builds the planner against the same fixtures, integration happens through CI — not through reading each other's code. Each agent PRs into its own directories; cross-boundary PRs require the other agent's review. Golden-fixture drift is the canary: if a fixture changes, both sides re-run.

---

## 6. Build Sequence (dependency order, no dates)

**Phase 0 — Contracts.** All seven schemas, codegen for three languages, golden fixtures, CI skeleton. Both agents. Nothing else starts until MediaRecord, JobSpec, and EDL are frozen.

**Phase 1 — Spine.** Codex: ingest + proxy pipeline + ml-runtime host + desktop shell with library grid and progress UI. Claude: media-db + Tier 1 photo stack configs + ranking v1 + dedupe. Exit gate: 100k-item mixed library scans overnight, resumes from kill, search works, duplicates clustered.

**Phase 2 — Album.** Claude: clustering, selection, layout solver, enhancement planning, validator rules, Tier 3 spread review. Codex: render-print, vendor profile #1, enhance GPU host, review/approve UI, checkout. Exit gate: a Thailand-trip-class library produces a 32-page PDF that passes the hard validator and survives a real print run.

**Phase 3 — Video analysis + culling product.** Claude: shot/moment pipeline configs, moment scoring v1. Codex: video proxy path hardened for 200-hour libraries, culling UI ("your 40 usable minutes"). Shipping culling alone is deliberate: it's valuable standalone, and it puts the hardest analysis layer in front of real users before any creative output depends on it.

**Phase 4 — Reels.** Claude: beat grid, reel planner, variants, vertical reframe planning. Codex: EDL renderer, SAM 2 tracking in runtime, music library integration, variant-picker UI, share flows. Exit gate: blind A/B where ≥40% of viewers can't identify the auto-cut against a human editor's cut of the same footage.

**Phase 5 — Films.** Claude: story-arc prompting, film planner, speech-aware trimming, revision-by-instruction ("more of her"). Codex: longer-form render profiles, project editor with OTIO export.

**Phase 6 — Taste flywheel.** Claude: preference model v2 (learned ranker), global model over anonymized events, eval expansion. Codex: sync, shared albums, mobile parity.

Concierge runs from Phase 2 onward: paid human-in-the-loop jobs using the same engine. Revenue while the automation matures, and every concierge edit is labeled training data for the edit-grammar and preference models.

**Decide in Phase 0, not later:** music licensing (catalog deal vs CC library vs generated score) — it shapes the beat-sync design and reel product; and print vendor #1 — the validator is built to a real spec sheet, not an imagined one.

---

## 7. Quality Gates (the definition of "industry-leading")

Face clustering: ≥99% precision at the confidence threshold used for automated output (a wrong person in a family album is a catastrophic error, tuned for precision over recall). Album: 100% of exports pass the hard print validator; zero refunds attributable to technical print defects. Reels: the blind-A/B bar above, plus beat-alignment error <50ms on downbeat cuts. Films: no mid-word cuts (word-timestamp verified), narrative-arc JSON present for every output. Pipeline: any job resumable after kill; no silent data loss (every destructive op is journaled); no network egress without a consent-ledger entry (verified by an automated egress test in CI). Every model swap gated by the eval harness on the benchmark libraries.

The emotional eval sits above all of it and is run with humans: show the output to the person whose life it is. If it doesn't land, the metrics were measuring the wrong thing.

---

## 8. Security & Privacy (unchanged non-negotiables, now with owners)

Local-first default; encrypted local DB with key in OS keychain (Claude: schema; Codex: platform keychain integration). Consent ledger for every upload/export/delete (Codex: api + UI; Claude: audit-log schema). Tier 3 sends thumbnails/contact-sheets only, opt-in per job, visible in the ledger. Child-face labeling behind separate explicit consent. NSFW/sensitive auto-exclusion from all automated outputs. Signed and notarized binaries, sandboxed workers, no training on user pixels ever — the global taste model learns from feature vectors and decisions only. DPDP-aligned data handling for India from the first release.
