# Mobile M1 — 2-day parallel sprint board

**Target:** an installable **Android APK** on Rohan's phone that picks from **Google Photos + device
gallery + local folders**, runs **SigLIP2 + YuNet on-device** (no Mac/cloud), and shows the native
review + fullscreen lightbox with a plausible "best shots" set. Full brief: `docs/mobile-app-plan.md`.

**Execution order (Rohan's call):** Codex runs its workers first and exhausts its limits; then Claude
delivers the model/selection workers and finishes any Codex leftovers. Codex's queue is deliberately
built to be **independent of Claude's deliverables** (mocks/placeholders where a real model or the TS
selection would go) so nothing blocks.

## Honest 2-day expectation
2 days of parallel work → a **real installable APK** that picks from all three sources and runs real
on-device AI, with a **placeholder-but-plausible** selection (dedupe + face-count + IQA proxy). The
**production-grade selection quality** is M2 (Claude's Python→TS port) and likely spills past 2 days.

---

## Codex workers (run first — pull an issue each)
`CX-1` is the seed (everything imports the app shell); `CX-2..CX-5` fan out in parallel after it.

| # | Worker | Independent? | Deliverable |
|---|---|---|---|
| **CX-1** | App shell + APK pipeline | seed | Expo+TS `apps/mobile/`, nativewind, native nav, warm-charcoal theme, debug keystore, `expo run:android`→APK, **verified `adb install -r` on Rohan's phone** (ship a "hello Photeo" APK first to prove the toolchain) |
| **CX-2** | Photo-source pickers | after CX-1 | Device gallery (`expo-image-picker`), local folder (SAF), **Google Photos** (`expo-auth-session` PKCE + Photos Picker API: session→poll→download). Unified `PickedPhoto[]` interface |
| **CX-3** | Review UI + fullscreen lightbox | after CX-1 (mock data) | Grid (FlashList/expo-image) + fullscreen pager (Reanimated + gesture-handler): swipe/‹›, ✕, Select; two modes (browse album / browse alternatives). Runs on a mock fixture matching the `/data` shape |
| **CX-4** | onnxruntime-react-native scaffold | after CX-1 (placeholder model) | Add `onnxruntime-react-native`, load an ONNX model on-device, dummy inference, stable `runModel(image)` iface. Claude's real SigLIP/YuNet configs slot in later |
| **CX-5** | On-device store + placeholder selection | after CX-1 | Local persistence (picked sets, picks/swaps) + placeholder ranking (embedding cosine dedupe + face count + IQA proxy) so the app produces a "best shots" set end to end. Claude's TS selection replaces this at M2 |

**Owner deps blocking a worker:** CX-2 Google Photos needs Rohan's OAuth project + the package/SHA-1 Codex
supplies (device gallery + folder ship without it — do those first).

## Claude workers (after Codex exhausts)
| # | Worker | Unblocks |
|---|---|---|
| **CL-1** | On-device model configs: quantized (int8) SigLIP2 ONNX + preprocessing model card (384, mean/std, output shape, EP order); YuNet config | makes CX-4 real |
| **CL-2** | TS port of the selection core (`ranking-engine`/`album-engine`: fusion, per-face weighted-min, Pareto in shot-groups, diversity across moments, packing) as a package `apps/mobile` imports | replaces CX-5 placeholder (M2) |
| **CL-3** | Integration + finish Codex leftovers: real model outputs → TS selection → review UI; close incomplete CX workers | ships M1→M2 |

## Definition of done (M1)
- [ ] APK installs via `adb install -r`; launches offline (except the Google OAuth call)
- [ ] Pick from device gallery, a folder, and Google Photos — thumbnails load
- [ ] SigLIP + YuNet run on-device (per-photo: embedding computed, N faces) — no server
- [ ] Fullscreen lightbox: swipe browses, ✕ closes, Select marks a pick
- [ ] A reviewable "best shots" set is produced on-device (placeholder ranking OK for M1)
