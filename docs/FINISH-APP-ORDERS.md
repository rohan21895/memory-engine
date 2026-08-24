# Photeo — finish the app (autonomous Codex program)

You (Codex) are now the **end-to-end driver**. Claude's session budget is ending.
Deliver a **complete, working, installed Android app**: a state-of-the-art
on-device image selector that turns a user's events / trips / memories into
finished albums. You have FULL autonomy: write code, add models, **commit, merge,
run gradle builds, `adb install`, and iterate** until it works on the connected
phone. No one will review — so verify your own work and keep going.

Repo: `/Users/rohantomar/Documents/Photeo/memory-engine` (branch `main`). App:
`apps/mobile`. Work directly on `main` in this tree; commit incrementally with
clear messages (end each: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`).

## Two specs are already written — they are your blueprint
1. **`docs/desktop-vs-android-selection-gap.md`** — the exact desktop selection
   algorithm vs Android, and the Layer 1/2/3 build plan + phone model choices.
2. **`docs/ui-overhaul-plan.md`** — the granny-proof UI/UX (5-screen guided flow).
Read both fully. They define WHAT to build; this doc defines the ORDER + how to
execute + how to verify.

## Current state (already on main, tsc-clean, faces release APK built)
- Own picker with date/album/**city+country** location filters (`photo-index.ts`,
  `GalleryGrid.tsx`), slide-select, select-all, seen-asset cache.
- **Faces**: `src/faces/` — ML Kit detection (`@infinitered/react-native-mlkit-
  face-detection@^5`, COMPILES on this Expo-57/New-Arch stack), face-crop
  perceptual embedding, greedy clustering, `getPeople`/`assetIdsForPerson`.
- **Selection quality v2**: `src/selection/` — Laplacian sharpness, blink gate,
  cut-face, category weights, screenshot heuristic. Wired in `build-album.ts`.
- A **UI overhaul** is being produced in a separate worktree
  `/Users/rohantomar/Documents/Photeo/me-ui` (branch `codex/ui-overhaul`,
  UNCOMMITTED). See "Integrate the UI" below.

## Build / run / validate environment (use exactly this)
```
export JAVA_HOME=/opt/homebrew/opt/openjdk@17
export ANDROID_HOME=/opt/homebrew/share/android-commandlinetools
export PATH="$JAVA_HOME/bin:$ANDROID_HOME/platform-tools:$PATH"
export npm_config_cache=/Users/rohantomar/Documents/Photeo/memory-engine/.npm-cache   # global cache is on an unmounted drive; THIS override is mandatory for npm/expo install
```
- Typecheck: `cd apps/mobile && npx tsc --noEmit -p tsconfig.json` (keep it clean).
- Tests: `node --test` on your `*.test.ts`.
- Standalone APK (bundles JS, no Metro): `cd apps/mobile && npx expo prebuild --platform android --no-install && cd android && ./gradlew assembleRelease`
  → `apps/mobile/android/app/build/outputs/apk/release/app-release.apk`.
- Install: `adb install -r <that apk>` (device `177fc81a` is connected; `pm grant`
  is blocked on this ROM — the app asks for Photos permission, that's fine).
- Validate yourself: `adb exec-out screencap -p > shot.png`, `adb shell input tap X Y`,
  `adb shell monkey -p com.photeo.app -c android.intent.category.LAUNCHER 1`. Drive
  the flow (pick a few photos → build → review → create album) and confirm no crash
  + a real album appears. Do NOT type credentials or touch other apps.

## EXECUTION ORDER (do them in this sequence; commit after each phase)

### Phase 0 — Integrate the UI overhaul (do this first, once its worktree is done)
The UI overhaul runs in `me-ui`. When it has finished (its report file
`/private/tmp/claude-501/-Users-rohantomar-Desktop/75330afd-aa51-4e1d-a8eb-1f7c61fd28bd/scratchpad/codex_ui_lastmsg.txt`
is non-empty, or no `codex` process references `me-ui`):
`cd /Users/rohantomar/Documents/Photeo/me-ui && git add -A && git commit -m "mobile: granny-proof UI/UX overhaul"` then in the main tree
`git merge --no-ff codex/ui-overhaul`. Resolve any `package.json`/`app.json`
conflicts by keeping BOTH sides' additions. Get `tsc` clean. If the UI worktree
never produced usable output, implement `docs/ui-overhaul-plan.md` yourself.

### Phase 1 — Port the coverage SELECTION ALGORITHM (pure logic, no model, BIGGEST WIN)
Port `packages/album-engine/memory_engine_album/selection.py`'s `select()` to
TypeScript **faithfully** (it's the heart of the product — a greedy marginal-gain
COVERAGE optimizer, not a quality ranker). New module
`apps/mobile/src/selection/album-planner.ts` (+ tests). Keep the algorithm
identical; only the inputs become phone signals. Port these behaviors exactly:
- greedy marginal-gain over axes **quality-standing · time · place · moment ·
  body-pose · person** with geometric bucket decay (0.5);
- **MMR redundancy** penalty with a library-calibrated free-similarity zone;
- **people floor** phase (min 1 per person via greedy max-coverage) + per-person
  cap + non-people fraction floor;
- caps: `max_per_pose_family`, `max_per_body_pose` (relaxed last);
- rare-moment + scarce-person quality-floor **waivers**; user **pins/excludes**;
- hard gates + quality floors (reuse the quality v2 signals already computed);
- determinism (sort by media_id, quantize gains to 6dp, media_id tiebreak).
Also port `packages/album-engine/memory_engine_album/pose.py` **verbatim** to
`apps/mobile/src/selection/pose.ts` (pure joint-angle math — no model needed to
port; it consumes keypoints Phase 2 will supply). Replace/extend
`select-best-shots.ts` usage so `build-album.ts` calls the new planner. Feed it
the signals we already have (perceptual embedding, ML Kit faces, `creationTime`
for time, `photo-index` place, `face-cluster` person ids). This alone turns
"top-N by quality" into a real coverage-optimized album. Commit.

### Phase 2 — The phone models (react-native-fast-tflite; New-Arch works — ML Kit proved it)
Add `react-native-fast-tflite` (New-Arch JSI). For each model: bundle the .tflite
in `apps/mobile/assets/models/`, write a guarded runtime wrapper (lazy, degrades
to neutral on failure — never crash, the onnxruntime lesson), and feed the planner.
1. **Body pose → MoveNet SinglePose Lightning** (int8 .tflite) → 17 COCO
   keypoints+scores → feed `pose.ts` unchanged → pose-diversity axis + caps. This
   IS the "pose detection" the owner asked for.
2. **Semantic embedding → MobileCLIP (S0/S1) or a small SigLIP** (.tflite) →
   replace the perceptual fingerprint everywhere (dedup, redundancy, moment/shot
   grouping) AND compute the **zero-shot expression axes** via text-image cosine
   contrasts: aesthetic, composed, clean_frame (bystander), sleeping,
   embrace_context, screenshot_document. Precompute text embeddings offline and
   bundle them as JSON so no text encoder ships.
3. **Face identity → MobileFaceNet** (ArcFace-style .tflite) → real face embeddings
   → upgrade `face-cluster.ts` from perceptual to identity → trustworthy people
   floor + People filter.
Build + install + validate after each model lands. Commit each.

### Phase 3 — Cheap wins + finish
- Face-region & head-region sharpness, per-face exposure (you already crop faces).
- Head pose (ML Kit `headEulerAngle*`) → identity-confidence tightening.
- Wire the People filter into the picker's Filter sheet (getPeople / buildFaceIndex).
- Full pass: onboarding, all empty/loading/denied/error states, plain-language
  reasons rendered from `ReviewData`. Make the whole flow feel finished.

## Definition of DONE (the complete app)
- Installs and runs on the phone; a novice can go home → pick → build → review →
  **album created**, with clear guidance the whole way.
- Selection is coverage-optimized (spreads across time/place/people/pose, never
  omits a person, no near-duplicates, no blinks/blur/screenshots).
- Pose detection works on-device (MoveNet → pose.ts clustering) as a diversity axis.
- `tsc` clean, tests green, no crash. Everything committed to `main`.
- Write `docs/APP-STATUS.md`: what shipped, model sources/licenses, how to rebuild,
  known limits, and what's left. Keep it updated as you go so the owner can resume.

## Guardrails
- Never break the working end-to-end flow; keep contracts (`onConfirm`,
  `onFinalize`, `PickedPhoto`, `ReviewData`) stable.
- Every native/model call guarded → degrade to neutral, never crash (onnxruntime lesson).
- Public repo: no media, no secrets, no `.env` in commits, no absolute home paths in code.
- Prefer model licenses that are commercially usable (MoveNet Apache-2.0,
  MobileFaceNet/MobileCLIP — check and record licenses in APP-STATUS.md; prefer
  Apache/MIT; avoid non-commercial weights).
- If a model won't run on this stack, degrade gracefully and record it — don't
  block the whole app on one model. The Phase-1 algorithm must work regardless.
