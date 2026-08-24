# Photeo Android app status

Last updated: 2026-08-24

## Current checkpoint

Photeo has a working, TypeScript-clean Expo 57 Android flow and a coverage-first
album planner. A release APK has not yet been rebuilt from this checkpoint; the
last known faces build predates the planner and UI overhaul. Model integration,
release installation, and the final on-phone walkthrough remain in progress.

## Shipped on `main`

- Granny-proof five-screen flow: Welcome → Start → Pick → Making → Review → Done.
- Persisted first-run welcome, one primary action per screen, visible Pick / Review /
  Done progress, large controls, accessible labels, friendly empty/error/permission
  states, and repeated on-device privacy reassurance.
- Editorial Photeo design system with bundled Fraunces and Atkinson Hyperlegible
  Next fonts. Presentation copy is centralized under `apps/mobile/src/ui/`.
- Own full-library picker with date, album, city/country place, and People filters;
  slide-select, select-all, sparse-filter paging, and the seen-asset cache remain.
- ML Kit face detection, local face-index queries, quality-v2 sharpness/exposure/
  blink/cut-face/category signals, and perceptual image fingerprints.
- Coverage planner in `apps/mobile/src/selection/album-planner.ts`, porting the
  desktop selector's decision structure:
  - greedy marginal gain over quality-standing, time, place, moment, body pose,
    and person axes with 0.5 geometric bucket decay;
  - library-calibrated MMR plus a hard distinctness backstop;
  - greedy max-coverage people floor, per-person cap, and scenery reservation;
  - pose-family/body-pose caps with body pose relaxed last;
  - rare-moment and scarce-person soft-floor waivers;
  - sovereign pins, absolute excludes, hard content gates, six-decimal gain
    quantization, media-id tie-breaking, and deterministic chronological output.
- Dependency-free TypeScript port of the desktop COCO-17 joint-angle pose math in
  `apps/mobile/src/selection/pose.ts`.

## Verification at this checkpoint

- `npx tsc --noEmit -p tsconfig.json`: passes.
- `node --test src/selection/*.test.ts src/faces/*.test.ts`: seven test modules
  pass under Node 22.22.3.
- UI palette contrast checks: AA or better for the shipped text/action pairs.
- Git worktree: Phase 0 is committed and merged; Phase 1 is ready to commit.

## On-device model provenance

| Component | Source | License | Bundled now | Notes |
|---|---|---|---|---|
| ML Kit face detection | `@infinitered/react-native-mlkit-face-detection` / Google ML Kit | Package and Google SDK terms | Yes | Guarded; failure returns no faces. |
| MoveNet SinglePose Lightning int8 | [TensorFlow Hub](https://tfhub.dev/google/lite-model/movenet/singlepose/lightning/tflite/int8/4) | Apache-2.0 | Not yet | Verified TFLite, 2.8 MB; SHA-256 `cd7cc22fa946e5d146a7b98d496853e1923e22828d3972d579973f27f91bb105`. |
| `react-native-fast-tflite` | [mrousavy/react-native-fast-tflite](https://github.com/mrousavy/react-native-fast-tflite) | MIT | Not yet | Planned New-Architecture JSI runtime. |
| MobileCLIP official weights | Apple ML-MobileCLIP | Research-only model license | No | Explicitly rejected for a commercial product. A permissive SigLIP/image-embedding alternative must be used or the perceptual fallback remains. |
| MobileFaceNet identity weights | Pending verified source | Pending | No | Will not be bundled until both weights provenance and commercial permission are recorded. |

Model calls are required to be lazy and guarded. A missing or incompatible model
must return neutral signals and preserve the complete album flow.

## Rebuild

```sh
export JAVA_HOME=/opt/homebrew/opt/openjdk@17
export ANDROID_HOME=/opt/homebrew/share/android-commandlinetools
export PATH="/opt/homebrew/opt/node@22/bin:$JAVA_HOME/bin:$ANDROID_HOME/platform-tools:$PATH"
export npm_config_cache="$(git rev-parse --show-toplevel)/.npm-cache"

cd apps/mobile
npx tsc --noEmit -p tsconfig.json
node --test src/selection/*.test.ts src/faces/*.test.ts
npx expo prebuild --platform android --no-install
cd android
./gradlew assembleRelease
adb install -r app/build/outputs/apk/release/app-release.apk
```

## Granny walkthrough

1. Open Photeo and tap **Get started**.
2. Tap **Choose photos** on the three-step start screen.
3. Tap favorite photos; each selected tile gets a large gold check. Use **Filter**
   only when needed to narrow by date, album, place, or person.
4. Tap **Next**. The making screen explains that selection happens on the phone.
5. Review the album pages, tap a page for alternatives, then tap **Make my album**.
6. The Done screen confirms that the album is saved on the phone and offers
   **Make another album**.

## Known limits / remaining work

- MoveNet inference and pose-cluster wiring are not yet bundled.
- Semantic embeddings and zero-shot expression axes still use neutral/perceptual
  fallbacks pending a commercially usable model.
- Face identity clustering still uses perceptual face crops pending licensed
  MobileFaceNet-class weights.
- Face/head-region sharpness, per-face exposure, and ML Kit head-pose tightening
  remain to be added.
- A new release APK must be built, installed on device `177fc81a`, and driven from
  picker through a real created album without a crash.
