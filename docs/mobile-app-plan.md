# On-device Photeo app — task brief (Android APK first)

**Supersedes the earlier LAN thin-client version.** Rohan's decision: **fully on-device / standalone**
(no Mac, no cloud — matches the local-first pitch), **Android APK first**, all photo sources.

**Split:** Codex owns the app, native pickers, on-device model runtime wiring, and the build/APK
(`apps/mobile/`). Claude owns the on-device **model configs** (ONNX → mobile) and the **TypeScript port
of the selection engine** (`packages/*` are Claude's Python today). Contract gaps → comment on the issue.

## Why staged (be honest with the owner)
- Models are **ONNX** (`models/weights/`: `siglip2-so400m-patch14-384-vision.onnx`,
  `scrfd_10g_bnkps.onnx`, `face_detection_yunet_2023mar.onnx`, `w600k_r50.onnx`) → run on-device via
  **`onnxruntime-react-native`** (CoreML EP on iOS, NNAPI/XNNPACK on Android). Feasible.
- The **selection brain is Python** (`ranking-engine`, `album-engine`, `media-db` = `pyproject.toml`).
  It **cannot** run in RN's JS runtime → Claude ports the core logic to TS (M2). Do NOT try to embed
  Python in the app.
- **Licensing landmine:** SCRFD + ArcFace (`w600k_r50`) weights are non-commercial research. Fine for
  Rohan's own device; for a shippable/VC build swap to clean licenses — **YuNet** (Apache-2.0) is already
  in the repo for face detection. Prefer YuNet on-device.
- SigLIP2 **so400m** is large for a phone. Claude will supply a **quantized (int8) and/or base** ONNX
  variant + preprocessing spec so it fits and runs on Android. Don't ship the raw so400m fp32.

## M1 — installable Android APK (the "test on my phone" milestone)
Deliver an APK Rohan installs via `adb install -r` (his SDK: `/opt/homebrew/share/android-commandlinetools`,
JDK `openjdk@17`). Expo dev build (EAS local or `expo run:android`), Codex generates a debug keystore.

Scope:
1. **Photo source pickers** (Android sources — Apple Photos is iOS/Mac, comes later):
   - **Device gallery** — Android Photo Picker via `expo-image-picker` / `expo-media-library`.
   - **Local folders** — Storage Access Framework folder pick.
   - **Google Photos** — Google **Photos Picker API** (`photospicker.googleapis.com`), OAuth 2.0 with
     `expo-auth-session` (PKCE). Scope `https://www.googleapis.com/auth/photospicker.mediaitems.readonly`.
     Owner is creating the Google Cloud OAuth project; Codex provides the redirect URI / package + SHA-1
     the client ID needs, then wires the session→poll→download flow.
2. **On-device inference** (proves standalone AI): run **SigLIP2 (quantized)** + **YuNet** on the picked
   photos with `onnxruntime-react-native`. Claude supplies `docs/model-cards/*` on-device configs
   (input size 384, mean/std, output shape, EP order) — build against those, not guesses.
3. **Native review + fullscreen lightbox** — same UX as the web review we shipped: grid of picked photos,
   tap → fullscreen pager (swipe / ‹ ›, ✕ close, Select), per-photo signal readout (embedding-based
   near-dup grouping + face count + a quality proxy) so selection is *visible* before M2 lands the real
   ranking. FlashList, Reanimated, expo-image, Nativewind, native nav.
4. **Placeholder selection**: until M2, rank within near-duplicate groups by a simple on-device proxy
   (IQA/quality + face-open heuristic) so the app produces a plausible "best shots" set end to end.

M1 acceptance:
- [ ] `adb install -r` puts the APK on Rohan's phone; it launches offline (airplane mode OK except the
      Google Photos OAuth call).
- [ ] Pick from device gallery, a folder, and Google Photos; thumbnails load.
- [ ] SigLIP + YuNet run on-device (show per-photo: embedding computed, N faces) — no server.
- [ ] Fullscreen lightbox: swipe browses, ✕ closes, Select marks a pick.
- [ ] A "best shots" set is produced on-device (placeholder ranking) and reviewable.

## M2 — the real selection engine, on-device (Claude-led)
Claude ports `ranking-engine` + `album-engine` core (fusion, per-face weighted-min, Pareto within
shot-groups, diversity across moments, album packing) to a TS package the app imports. Codex integrates:
picked photos → on-device model features → TS selection → the same review UI, now with the real reasons
(`chosen_because`, alternatives, "may print soft"). Contract = the `/data`-shaped objects from the web
prototype (selected[]/alternatives[]/pool[]), produced locally instead of fetched.

## M3 — standalone product
On-device album render (PDF), clean-license model set (YuNet + a commercially-licensable face embedder),
then re-enable iPhone + Mac targets (Apple Photos via PhotoKit; Mac via Tauri or Expo/RN-macOS),
per-platform signing + distribution.

## Stack rules (Rohan's global CLAUDE.md → react-native-skills)
Expo SDK, TypeScript. FlashList, Reanimated, expo-image, native nav, Nativewind. Warm-charcoal editorial
tone (match the web UI). No SaaS-blue.

## Owner-provided dependencies (Codex can't create these)
- Google Cloud OAuth project + client ID for Google Photos (owner doing now; Codex supplies the
  redirect/package/SHA-1 values).
- (Deferred) Apple Developer account for iPhone/Mac installs — not needed for the Android APK.
