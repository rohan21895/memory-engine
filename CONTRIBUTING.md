# Contributing to Memory Engine

Memory Engine is contract-first. Cross-boundary work starts with schemas and golden fixtures, and implementations consume generated bindings rather than duplicating contract types.

## Ownership

| Paths | Owner | Review rule |
| --- | --- | --- |
| `contracts/**` | Shared by Claude Code and Codex | Both owners must review and co-sign. Generated TypeScript, Python, and Rust bindings and golden tests must be included. |
| `packages/media-db/**`, `packages/ranking-engine/**`, `packages/story-engine/**`, `packages/album-engine/**`, `packages/prompt-engine/**`, `packages/eval-harness/**` | Claude Code | Claude Code owns implementation and approval. Codex opens an issue for requested changes. |
| Model registry contents and model pre/post-processing specifications | Claude Code | Claude Code owns implementation and approval; every model weight requires a license audit. |
| `docs/**` | Claude Code | Claude Code owns architecture, model cards, and evaluation reports. |
| `workers/ingest/**`, `workers/render-video/**`, `workers/render-print/**` | Codex | Codex owns implementation and approval. |
| `workers/ml-runtime/**` | Codex for the host; Claude Code for model configuration and pre/post-processing | Cross-owner changes require both reviewers. |
| `workers/enhance/**` | Codex for GPU execution; Claude Code for pipeline decisions | Cross-owner changes require both reviewers. |
| `apps/**`, `services/**` | Codex | Codex owns implementation and approval. |
| `.github/**`, release tooling, signing, notarization, packaging, updates, and privacy-filtered crash reporting | Codex | Codex owns implementation and approval. |

Renderers execute `EDL` and `AlbumSpec`; they do not introduce creative decisions. All work that uploads data must use a consent-ledger entry, and original media must not leave the device without explicit logged consent.

## Pull requests

- Use focused branches and conventional commits such as `feat(ingest): ...` and `fix(desktop): ...`.
- Keep changes inside the path owner's territory. Request out-of-territory work with an issue instead of editing the files directly.
- Changes under `contracts/**` require both owners. Regenerate and commit all language bindings, and update golden fixtures in the same pull request.
- Build against `contracts/fixtures/**`, never against another owner's implementation details.
- Do not begin a dependent implementation until its required contracts are merged and frozen. In particular, ingest waits for `MediaRecord` and `JobSpec`; render work waits for `EDL` and `AlbumSpec`.
- Every new component must expose `lint` and `test` through its `package.json`, Cargo manifest, or Python project so the root CI runner discovers it.
- Network-capable components must add themselves to the egress harness before merge. A consent-ledger entry is mandatory for every allowed outbound connection.

## Protected `main` branch

Configure the repository's `main` ruleset with the following settings:

1. Require a pull request before merging; disable direct pushes, branch deletion, and force pushes.
2. Require these status checks to pass and require the branch to be up to date:
   - `Lint`
   - `Test`
   - `Windows Ingest`
   - `Codegen Freshness`
   - `Contracts`
   - `Egress Test`
3. Require all review conversations to be resolved and dismiss approvals when new commits are pushed.
4. Require code-owner review after GitHub teams or accounts for Claude Code and Codex are mapped to the ownership table above.
5. Require both owners' approvals for `contracts/**` and the shared portions of `workers/ml-runtime/**` and `workers/enhance/**`. Single-owner paths require that owner's approval; the owner may self-merge only after every required check passes.
6. Require linear history and apply the rules to administrators and automation accounts.

The egress job is intentionally a Phase 0 stub while no network-capable process exists. Its TODOs are merge blockers for the first such process: replace them with a deny-by-default sandbox that proves unlogged traffic fails and consent-scoped traffic succeeds.
