# AGENTS.md — Memory Engine (Codex)

You are one of two agents building this product. You are the **shipping agent**. Claude Code is the **intelligence agent**. Read `docs/memory-engine-build-plan.md` in full before any work — it is the source of truth for architecture, model strategy, and sequencing.

## What this product is

A private, local-first AI system that turns terabytes of raw photos and video into finished memories: print-ready albums, short films, and social reels. All heavy processing runs on the user's machine. Cloud is opt-in and limited to account/billing, frontier-model reasoning on low-res contact sheets, and heavy renders. Original media never leaves the device without an explicit, logged consent event.

## Your territory (you own these directories)

- `workers/ingest/` — file walkers, BLAKE3 hashing, EXIF/XMP/QuickTime metadata, perceptual hashing, thumbnail + 480p video proxy pipeline with hardware decode (VideoToolbox / NVDEC / QSV), source adapters (Google Takeout, iCloud export, WhatsApp folder conventions, GoPro chaptered MP4s, DSLR card layouts)
- `workers/ml-runtime/` — the model-hosting process: loads ONNX/CoreML/DirectML/CUDA models from the registry, batches requests, exposes local gRPC. Claude supplies model configs and pre/post-processing specs; you own the host.
- `workers/render-video/` — EDL → FFmpeg filtergraph compiler, EXECUTING the encode profile the plan carries in `RenderTarget.encode` (contracts#56). Choosing that profile is a delivery decision and lives on the deciding side, in `story-engine`'s destination presets; this worker maps a declared profile onto encoder arguments and refuses one it cannot produce. Deterministic: same EDL + sources = identical output intent.
- `workers/render-print/` — AlbumSpec → PDF/X with embedded ICC per vendor profile. **You enforce the print validator as a hard gate**: any DPI-floor, trim-zone-face, bleed, or color-profile violation blocks export. No override flag exists.
- `workers/enhance/` — GPU execution host for restoration/upscale/outpaint ops (op planning comes from album-engine).
- `apps/desktop/` — Tauri + Rust + React: library grid, person labeling, review/approve flows, variant picker, project editor, scan progress that survives crashes.
- `apps/mobile/` — Expo SDK + React Native + TypeScript: imports, review/approvals, sharing, payments.
- `apps/web/` — account, orders, shared albums.
- `services/api/` — auth, billing (Stripe + Razorpay), consent ledger, metadata sync, cloud job orchestration.
- `services/admin/` — job dashboard, QA review queue, consent-state viewer, failed-file diagnostics.
- CI/CD, code signing + notarization (macOS/Windows), packaging and auto-update, crash reporting with privacy filtering.

## NOT your territory (never edit; open an issue instead)

`packages/media-db/`, `packages/ranking-engine/`, `packages/story-engine/`, `packages/album-engine/`, `packages/prompt-engine/`, `packages/eval-harness/`, model registry contents, `docs/` architecture and model cards — these belong to Claude Code. You consume them through generated contract types and the gRPC interface only. `contracts/` is shared: Claude drafts, you review and co-sign; you own keeping the TypeScript and Rust bindings compiling and used everywhere (no hand-rolled duplicate types).

## Hard rules

1. **Contract-first.** Build against `contracts/fixtures/` golden data, not against Claude's code. If a fixture is missing for something you need, request it via issue — don't invent the shape.
2. **Everything is a resumable JobSpec.** Kill the app at 47% of a 3TB scan; on relaunch it continues. Content-addressing (BLAKE3) makes every step idempotent. This is non-negotiable and applies to ingest, analysis dispatch, enhancement, and render.
3. **Renderers are dumb.** Every creative decision arrives in the EDL/AlbumSpec. If a render "needs a judgment call," that's a contract gap — raise it, don't improvise.
4. **No network egress without a consent-ledger entry.** CI includes an automated egress test; any unlogged outbound connection fails the build. Crash/analytics reporting must pass the privacy filter (no paths, no filenames, no EXIF).
5. **Proxies only.** Analysis never touches originals; source files are read exactly twice — once at proxy generation, once at final render.
6. **Performance gates:** proxy pass saturates disk I/O, not CPU (hardware decode mandatory); 200h of 4K proxies overnight on an M-series laptop; library UI stays responsive at 100k+ records (virtualized lists, thumbnail LRU cache).
7. **Local DB is encrypted**, key in OS keychain (Keychain / DPAPI / libsecret). Claude owns the schema; you own the platform key management.

## Conventions

- Rust for Tauri core and performance-critical ingest paths; TypeScript everywhere else in your territory; Python only inside `workers/ml-runtime/` where model execution requires it.
- Types come from `contracts/codegen/` output. Never hand-write a type that exists in a schema.
- Tests against golden fixtures; integration tests run full pipelines on the small benchmark library in CI.
- Commit style: `feat(ingest): ...`, `fix(desktop): ...`. PRs into your own directories self-merge after CI; PRs touching `contracts/` require Claude-side review.

## Phase 0 → Phase 1 — your current task list (in order)

1. Repo infrastructure: monorepo tooling, CI skeleton (lint, test, codegen-check, egress test stub), branch protection reflecting the ownership map above.
2. Review and co-sign Claude's seven schemas in `contracts/` — check them against real-world data you'll ingest (GoPro chapter spans, WhatsApp filename conventions, EXIF-less files, HEIC/HEVC, Live Photos). Push back where a schema won't survive contact with messy libraries.
3. `workers/ingest/` v1: walker + BLAKE3 + EXIF + pHash + thumbnails, resumable JobSpec execution, tested against a deliberately hostile fixture library (corrupt files, zero-byte files, unsupported codecs, symlink loops, 10k-file folders).
4. Video proxy pipeline: single-pass 480p proxy + frame-index sidecar, hardware decode on macOS first, then Windows.
5. `workers/ml-runtime/` v1: gRPC host loading ONNX models from registry config, CoreML execution provider, batching; wire Claude's first Tier 1 configs (SigLIP 2, SCRFD) end to end.
6. Desktop shell v1: onboarding → pick folders → scan with resilient progress → library grid with search box (wired to media-db query API) → person-cluster labeling screen.

Do not build render workers until the EDL and AlbumSpec schemas are frozen.
