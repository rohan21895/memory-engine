# Photeo Android app status

Last updated: 2026-08-24

## Current checkpoint

Photeo has a working, TypeScript-clean Expo 57 Android flow, a coverage-first
album planner, on-device MoveNet body-pose inference, and MIT-licensed TinyCLIP
semantic embeddings with six zero-shot expression axes. The standalone release
APK was built, installed on device `177fc81a`, and driven through a real
four-photo Pick → Making → Review → Done flow without a crash. The finished
album contained two pages after deterministic near-duplicate collapsing.

## Shipped on `main`

- Granny-proof guided flow: Welcome → Start → Pick → Making → Review → Done.
- Persisted first-run welcome, one primary action per screen, visible Pick /
  Review / Done progress, large controls, accessible labels, friendly empty,
  error, loading, and permission states, and repeated on-device privacy copy.
- Editorial Photeo design system with bundled Fraunces and Atkinson Hyperlegible
  Next fonts. Presentation copy is centralized under `apps/mobile/src/ui/`.
- Full-library picker with date, album, city/country place, and People filters;
  slide-select, select-all, sparse-filter paging, and a seen-asset cache.
- ML Kit face detection and local face-index queries.
- Quality-v2 sharpness, exposure, blink, cut-face, category, and screenshot /
  document signals, with perceptual image fingerprints as the safe fallback.
- Coverage planner in `apps/mobile/src/selection/album-planner.ts`, porting the
  desktop selector's decision structure:
  - greedy marginal gain over quality-standing, time, place, moment, body-pose,
    and person axes with 0.5 geometric bucket decay;
  - library-calibrated MMR plus a hard distinctness backstop;
  - greedy max-coverage people floor, per-person cap, and scenery reservation;
  - pose-family and body-pose caps, with the body-pose cap relaxed last;
  - rare-moment and scarce-person soft-floor waivers;
  - sovereign pins, absolute excludes, hard content gates, six-decimal gain
    quantization, media-id tie-breaking, and deterministic chronological output.
- Dependency-free TypeScript COCO-17 joint-angle clustering in
  `apps/mobile/src/selection/pose.ts`.
- Official MoveNet SinglePose Lightning int8 runs through the New-Architecture
  JSI TFLite host. Its 17 COCO keypoints feed the joint-angle math, deterministic
  pose clusters, planner diversity reward, and pose caps. Loading,
  preprocessing, tensor validation, and inference all fail neutral.
- TinyCLIP ViT-8M/16 replaces perceptual fingerprints when available for visual
  grouping, redundancy, and moment coverage. Six offline text-embedding
  contrasts add aesthetic, composition, clean-frame/bystander, sleeping/awake,
  embrace-context, and screenshot/document evidence. The text encoder does not
  ship. TinyCLIP loading and inference are serialized and fail back to the
  perceptual embedding without interrupting album creation.

## Verification

- `npx tsc --noEmit -p tsconfig.json`: clean.
- `node --test src/selection/*.test.ts src/faces/*.test.ts src/ml/*.test.ts`:
  nine test modules green.
- Release APK: built successfully with bundled JavaScript and native TFLite /
  Nitro code, then installed on `177fc81a` (CPH2649).
- APK contains the exact verified MoveNet artifact; extracting its packaged
  resource reproduces the SHA-256 below.
- The TinyCLIP TFLite output matched its source ONNX embedding at cosine 1.0;
  the APK contains the exact TFLite artifact identified below.
- On-phone walkthrough: four real photos produced a two-page album through the
  complete guided flow; no app crash appeared in logcat.

## On-device model provenance

| Component | Source | License | Bundled | Notes |
|---|---|---|---|---|
| ML Kit face detection | `@infinitered/react-native-mlkit-face-detection` / Google ML Kit | Package and Google SDK terms | Yes | Guarded; failure returns no faces. |
| MoveNet SinglePose Lightning int8 | [TensorFlow Hub](https://tfhub.dev/google/lite-model/movenet/singlepose/lightning/tflite/int8/4) | Apache-2.0 | Yes | Official 2.8 MB uint8 model; SHA-256 `cd7cc22fa946e5d146a7b98d496853e1923e22828d3972d579973f27f91bb105`. Input `[1,192,192,3]`; output `[1,1,17,3]`. |
| `react-native-fast-tflite` | [mrousavy/react-native-fast-tflite](https://github.com/mrousavy/react-native-fast-tflite) | MIT | Yes | Version 3.0.1 with `react-native-nitro-modules` 0.37; CPU delegate by default for broad compatibility. |
| TinyCLIP ViT-8M/16 Text-3M | [Microsoft TinyCLIP](https://github.com/microsoft/Cream/tree/main/TinyCLIP) / [MIT model card](https://huggingface.co/wkcn/TinyCLIP-ViT-8M-16-Text-3M-YFCC15M) | MIT | Yes | Vision-only float32 TFLite, 32 MB; SHA-256 `a1ccb2b874a00c533402ade45beeb392ae8e06a60a6a90829ed26a6796f399e9`. Six offline text axes; JSON SHA-256 `79ed8de61276327f7420787ab4acca316280a7969091fd0e4a672cac4a8da7b8`. |
| MobileCLIP official weights | Apple ML-MobileCLIP | Research-only model license | No | Explicitly rejected for a commercial product. A permissive SigLIP/OpenCLIP-derived alternative is required, otherwise the perceptual fallback stays. |
| MobileFaceNet identity weights | Pending verified source | Pending | No | Will not be bundled until weight provenance and commercial permission are both recorded. |

All native/model calls are lazy and guarded. A missing or incompatible model
returns neutral signals and preserves the complete album flow.

## Rebuild

```sh
export JAVA_HOME=/opt/homebrew/opt/openjdk@17
export ANDROID_HOME=/opt/homebrew/share/android-commandlinetools
export PATH="/opt/homebrew/opt/node@22/bin:$JAVA_HOME/bin:$ANDROID_HOME/platform-tools:$PATH"
export npm_config_cache="$(git rev-parse --show-toplevel)/.npm-cache"

cd apps/mobile
npx tsc --noEmit -p tsconfig.json
node --test src/selection/*.test.ts src/faces/*.test.ts src/ml/*.test.ts
npx expo prebuild --platform android --no-install
cd android
./gradlew assembleRelease
adb install -r app/build/outputs/apk/release/app-release.apk
```

## Novice walkthrough

1. Open Photeo and tap **Get started**.
2. Tap **Choose photos** on the three-step start screen.
3. Tap favorite photos; every selected tile gets a large gold check. Use
   **Filter** only when needed to narrow by date, album, place, or person.
4. Tap **Next**. The making screen explains that selection happens on the phone.
5. Review the chosen pages, inspect alternatives if wanted, then tap
   **Make my album**.
6. The Done screen confirms that the album is saved on the phone and offers
   **Make another album**.

## Known limits / remaining work

- TinyCLIP is deliberately the smallest published variant (41.1% zero-shot
  ImageNet top-1 in its model card). Its expression contrasts are useful ranking
  evidence, not authoritative labels; high-impact gates remain conservative and
  every signal falls back to neutral/perceptual evidence on failure.
- Face identity clustering still uses perceptual face crops pending verified,
  permissively licensed MobileFaceNet-class weights.
- Face/head-region sharpness, per-face exposure, and ML Kit head-pose confidence
  tightening remain to be added.
- Body pose uses a single-person model, so crowded group photos contribute only
  the most prominent detected body. Unreadable or low-confidence poses remain
  neutral by design.
