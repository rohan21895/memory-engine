# Photeo mobile app status

Last updated: 2026-08-25

## Current checkpoint

The finalized 26-screen light visual design is implemented in `apps/mobile` on `codex/design-impl`. The release app uses Figtree, a three-tab Albums / Photos / Account shell, the real local album-selection engine, crash-safe album persistence, and guarded native/file/model calls.

The release APK was built, installed, and exercised on Android device `177fc81a`. A real nine-photo selection completed as a five-photo album, persisted, reopened, and survived an APK reinstall. Share, print preview, family, shared-album, manage, and delete-confirmation routes rendered without a crash. No original photo was modified or uploaded.

## Shipped

- Android hardware and predictive Back now follow the custom shell history: sheets close first, Filter/Review return to Pick, tabs return to Albums, and only Albums root exits.
- Albums keeps the primary create action pinned above the tab bar while its shelf scrolls independently.
- Photos pages the complete granted MediaStore photo library and renders it month-grouped; people, places, and search are optional filters rather than the data source.
- The face picker now matches the design source with search, Anyone reset, Any/All mode tabs, multi-select rings, removal hint, and union/intersection filtering semantics.
- Face, place, and date indexes hydrate before rendering and checkpoint processed asset IDs. Relaunch shows persisted results immediately and scans only unseen assets.

- Warm cream, white, ink, terracotta, privacy-green visual system with Figtree weights 400–800 and dark status-bar content.
- First-run onboarding, optional account screen, and guarded photo-permission gate.
- Albums, Photos, and Account tabs with a full-screen album-creation flow.
- Restyled picker and real Face, Location, and Date filtering without changing selection, photo-index, or planner semantics.
- On-device building progress, coverage-based review reasons, alternate-shot swapping, missed shots, removal, and editable Album Ready screen.
- Crash-safe local JSON persistence for album title, cover, photos, review data, and date range.
- Albums shelf and empty state, album detail, animated slideshow, print/share entry points, and real rename/delete operations.
- Photos library backed by real media, face, place, country, city, and month indexes. Face scanning is user-started from the no-people state so a large initial scan cannot block normal tab navigation.
- Account counts backed by local albums and the device media library.
- Designed build-error recovery with retry and return-to-albums actions.
- Neutral fallbacks for unavailable permissions, files, indexes, models, and native modules.

## Visual stubs requiring services or contracts

These screens are deliberately usable previews and contain `TODO(owner): needs backend` comments. They make no network request and do not claim that an external operation completed.

- Phone/email OTP delivery and verification.
- Family membership, removal, and invite delivery.
- Shared-with-you metadata, media download, playback, and cross-phone album delivery.
- Share recipient delivery and new-recipient invitations. The confirmation says nothing was uploaded.
- Person-name and relationship persistence; the current face-index contract has no name field.
- Print pricing, address collection, payment, PDF/print rendering, fulfillment, and delivery. Confirmations are labeled preview-only and no payment is taken. Print rendering remains blocked until AlbumSpec is frozen.

## Verification

- `npx tsc --noEmit -p tsconfig.json`: clean.
- Pure selection tests plus face clustering and face-cluster merge self-checks: pass with Node 22 type stripping.
- `git diff --check`: clean.
- Clean Expo Android prebuild: pass.
- Gradle `assembleRelease`: pass.
- Final install using `adb -s 177fc81a install -r`: `Success`.
- Final process stayed foregrounded while switching Albums → Photos → Account; no React Native fatal, Android fatal, ANR, or input-dispatch timeout appeared in filtered logcat.
- Device Back verification covered face modal → Filter, Filter → Pick, Review → Pick, Photos/Account → Albums, and Albums root → Android.
- Device face-filter verification covered search, Anyone, multi-select/remove, both Any/All tabs, and the `2 people` summary.
- Device model diagnostics reported `float32[1x112x112x3]` input, `float32[1x192]` output, `expected=true`, identity inference active, and identity observations with zero perceptual fallbacks.
- Relaunch hydrated persisted people immediately and resumed from the saved unseen-asset checkpoint; the unchanged-complete-index path is covered by the incremental-index self-check.
- APK: `apps/mobile/android/app/build/outputs/apk/release/app-release.apk`
- APK size: `338404116` bytes.
- APK SHA-256: `ef956a587185c9d66d5b43e158406dd00ad9d0e311c66c2c2209b4410e9835ee`.

## Rebuild and install

The ONNX Runtime package requires the checked-in `patches/onnxruntime-react-native+1.24.3.patch`. If dependencies are reinstalled with lifecycle scripts disabled, apply it explicitly before prebuilding.

```sh
export JAVA_HOME=/opt/homebrew/opt/openjdk@17
export ANDROID_HOME=/opt/homebrew/share/android-commandlinetools
export PATH="/opt/homebrew/opt/node@22/bin:$JAVA_HOME/bin:$ANDROID_HOME/platform-tools:$PATH"
export npm_config_cache="$(cd "$(git rev-parse --show-toplevel)/../memory-engine" && pwd)/.npm-cache"

cd apps/mobile
npm install --ignore-scripts
npx patch-package
npx tsc --noEmit -p tsconfig.json

for test_file in src/selection/*.test.ts \
  src/faces/face-cluster.test.ts \
  src/faces/face-cluster-merge.test.ts
do
  node --experimental-strip-types "$test_file" || exit 1
done

npx expo prebuild --platform android --no-install
cd android
./gradlew assembleRelease
adb -s 177fc81a install -r app/build/outputs/apk/release/app-release.apk
```

## Known limits

- MobileFaceNet model-license review and broader product-library calibration remain required before commercial launch.
- Large face scans are resumable and explicit, but remain CPU intensive while running.
- TinyCLIP expression scores are ranking evidence, not authoritative labels; failures remain neutral or use the guarded perceptual fallback.
- MoveNet is single-person, so crowded group photos only contribute the most prominent detected body pose.

## 2026-08-25 face-model diagnosis and repair

The packaged MobileFaceNet graph was present in the APK, but `react-native-fast-tflite` received Android's bare raw-resource name (`assets_models_mobilefacenet192float32`) and passed it to `java.net.URL`, producing `MalformedURLException: no protocol`. `src/ml/bundled-tflite.ts` now uses `expo-asset` to materialize MobileFaceNet, MoveNet, and TinyCLIP to guarded local `file://` URIs before loading them.

Once identity inference was active, device evidence exposed two separate duplicate sources: order-dependent cluster splits and repeated ML Kit detections at different box scales. The index now rejects same-photo online assignments, suppresses overlapping/same-center boxes before inference, deduplicates same-photo observations, performs constrained agglomerative merging with calibrated large/sparse-cluster rules, allocates collision-free monotonic person IDs, binds face crops to their exact assignments, and withholds low-support satellites from the People UI until they have meaningful evidence. Anonymous diagnostics report only counts, shapes, cluster IDs, similarities, and shared-counts—never media paths or filenames.
