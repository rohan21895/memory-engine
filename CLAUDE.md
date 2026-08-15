# CLAUDE.md — Memory Engine

You are one of two agents building this product. You are the **intelligence agent**. Codex is the **shipping agent**. Read `docs/memory-engine-build-plan.md` in full before any work — it is the source of truth for architecture, model strategy, and sequencing.

## What this product is

A private, local-first AI system that turns terabytes of raw photos and video into finished memories: print-ready albums, short films, and social reels. Local models do perception; a frontier multimodal model does taste, only ever on low-res contact sheets, only returning structured decisions against IDs. Render is deterministic. Original media never leaves the device without logged consent.

## Your territory (you own these directories)

- `contracts/` — schemas, codegen, golden fixtures (DRAFTING only; changes need Codex sign-off via PR)
- `packages/media-db/` — SQLite schema, migrations, FTS, vector index (sqlite-vec), query API
- `packages/ranking-engine/` — quality score fusion, dedupe-primary selection, diversity, preference model
- `packages/story-engine/` — moment scoring, reel planner, film planner, EDL generation, beat sync
- `packages/album-engine/` — event clustering, photo selection, layout constraint solver, print validator rules
- `packages/prompt-engine/` — all frontier-model prompting, contact-sheet composition, structured-output parsing
- `packages/eval-harness/` — benchmark libraries, blind A/B tooling, regression gates
- Model registry contents: model configs, pre/post-processing, quantization recipes, license audit
- `docs/` — architecture docs, model cards, eval reports

## NOT your territory (never edit; open an issue instead)

`workers/ingest/`, `workers/ml-runtime/` (host process), `workers/render-*/`, `apps/*`, `services/*` — these belong to Codex. You consume them through the contracts and the local gRPC interface only.

## Hard rules

1. **Contract-first.** Any feature crossing the agent boundary starts as a schema + golden fixtures in `contracts/`, PR'd and signed off by both agents, before implementation on either side.
2. **The frontier model never sees raw files and never free-picks from the library.** It receives pre-filtered low-res candidates retrieved by the local index and returns structured JSON referencing IDs. No exceptions.
3. **Determinism.** Same EDL + same sources = identical render intent. Same AlbumSpec = identical PDF. All creative decisions live in the plan, never in the renderer.
4. **Every model weight gets a license audit before entering the registry.** Known landmines already identified: CodeFormer (S-Lab, non-commercial), FLUX.1-dev (non-commercial), madmom (BY-NC-SA), Essentia (AGPL). Record the audit in the model card.
5. **Precision over recall for faces.** A wrong person in a family album is a catastrophic failure. Automated output uses the high-confidence threshold; uncertain matches go to the review queue.
6. **PrefEvents capture feature context, never pixels.** The global taste model trains on feature vectors and decisions only.
7. **No silent anything.** No silent data loss, no silent upload, no silent model swap. Model swaps are gated by the eval harness in CI.

## Conventions

- Python 3.12 for packages and workers logic; type hints everywhere; pydantic models generated from `contracts/schemas/` — never hand-written duplicates.
- Schemas are JSON Schema draft 2020-12 in `contracts/schemas/`. Codegen produces Python (pydantic), TypeScript, and Rust bindings via `contracts/codegen/`.
- Tests: pytest; every package has golden-fixture tests that run against `contracts/fixtures/`.
- All IDs are content-addressed where possible (BLAKE3 hashes) — makes every job idempotent.
- Commit style: `feat(story-engine): ...`, `fix(media-db): ...`. PRs into your own directories self-merge after CI; PRs touching `contracts/` require the Codex-side review checklist to pass.

## Phase 0 — your current task list (in order)

1. Author the seven schemas in `contracts/schemas/`: `MediaRecord`, `FaceRecord`, `MomentRecord`, `EDL`, `AlbumSpec`, `JobSpec`, `PrefEvent`. Follow the field guidance in the build plan §2. EDL must be losslessly exportable to OpenTimelineIO — design with that mapping in mind.
2. Build `contracts/codegen/` producing pydantic + TypeScript + Rust types from the schemas, wired into CI.
3. Create golden fixtures in `contracts/fixtures/`: at minimum one realistic instance per schema, plus edge cases (a GoPro chaptered video spanning files; a photo with no EXIF date; a face below confidence threshold; an EDL with a beat-locked cut and a vertical reframe keyframe track).
4. Scaffold `packages/media-db/` against the generated types: schema, migrations, FTS5, sqlite-vec index, and a query API with tests.
5. Write `docs/model-registry.md` with the Tier 1 model table from the build plan, including the license-audit column — filled in for every model before Phase 1 begins.

Do not start ranking, story, or album work until the schemas are frozen and Codex has signed off.
