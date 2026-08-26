# Photeo — implement the new visual design (autonomous Codex)

You (Codex) are the end-to-end driver. Implement the owner's finalized app design
into the real React Native app. FULL autonomy: write code, add fonts, **commit,
build gradle, `adb install`, iterate** on device `177fc81a` until it runs. No
reviewer — verify your own work, keep `tsc` green, commit per phase. End each
commit: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

Repo: `/Users/rohantomar/Documents/Photeo/memory-engine`. App: `apps/mobile`.
Work in this worktree on branch `codex/design-impl`.

## The design is the source of truth — READ IT FIRST
- **`docs/design/Photeo.dc.html`** — the finalized, high-fidelity mockup of the
  WHOLE app (26 screens/sheets). This is a static design-canvas doc using a
  `<x-dc>` / `sc-for` / `sc-if` template runtime — treat the **rendered visual
  intent** (layout, spacing, type, color, copy) as the spec; the colored tiles
  are placeholders for real photos.
- `docs/design/support.js` — just the mockup's generic React render runtime
  (dc-runtime). NOT app logic. Ignore for implementation; do not port it.
- Open Photeo.dc.html and read every `<!-- SCREEN -->` block. The screens are:
  ONBOARDING, PERMISSION, LOGIN, ALBUMS HOME, ALBUM DETAIL, ALBUM READY,
  PHOTOS TAB, ACCOUNT, PICK, FILTER, FACE MODAL, LOCATION MODAL, BUILDING,
  REVIEW, SWAP SHEET, SLIDESHOW, TAB BAR, MANAGE ALBUM SHEET, SHARE SHEET,
  SHARE SENT, DELETE CONFIRM, PRINT PREVIEW, PRINT ORDERED, FAMILY,
  NAME A PERSON, NO PEOPLE FOUND YET, ERROR.

## This is a THEME + STRUCTURE overhaul (biggest change since v1)
The app today is a **light-on-dark, Fraunces/Atkinson, single-file state
machine** (welcome→start→pick→building→review→final). The new design is:
- **LIGHT theme, warm cream.** New design tokens (from Photeo.dc.html `:root`):
  `--bg:#faf8f5  --surf:#fff  --ink:#1a1714  --mut:#6f6a62  --line:#e7e2d9
   --acc:#c75c33 (terracotta)  --accs:#f9ece5  radius:20px`. Green accent for
  privacy notes `#4a8a5c`. Danger/sign-out `#a8481f`.
- **Font: Figtree** (weights 400–800), replacing Fraunces + Atkinson everywhere.
  Add via `@expo-google-fonts/figtree` (preferred) or bundle the .ttf in
  `assets/fonts/`; load in `App.tsx` with `expo-font` before first paint.
- **StatusBar:** dark content on light bg.
- **3-tab bottom nav** (TAB BAR): **Albums · Photos · Account** — the app becomes
  tabbed, not a linear flow. The create-album flow (Pick→Filter→Building→Review→
  Ready→Slideshow) is a modal/stack pushed from Albums.

## Execution phases (commit + keep tsc clean after each)

### Phase 1 — Foundation: theme tokens + font
Rewrite `src/ui/tokens.ts` to the light palette above; update `src/ui/fonts.ts`
to Figtree; load the font in `App.tsx`. Flip `StatusBar` to dark content. Get
the existing screens rendering on the light theme (they will look rough — that's
fine, later phases restyle them). tsc green, commit.

### Phase 2 — 3-tab shell + navigation
Restructure `App.tsx` into a **bottom tab shell** (Albums · Photos · Account) per
the TAB BAR block. Keep it dependency-light: a simple custom tab bar switching a
`tab` state is fine (no need to add react-navigation unless you judge it worth
it). Onboarding/Permission/Login gate before the tabs on first run (reuse the
existing `WELCOME_SEEN_KEY` SecureStore check). The create-album flow becomes a
full-screen stack launched from the Albums tab's "Create new album" button. Keep
existing contracts working (`onConfirm`, `PickedPhoto`, `ReviewData`,
`onFinalize`). tsc green, commit.

### Phase 3 — Restyle the existing create-album flow to the new design
Map 1:1, restyle to the design (light, Figtree, terracotta, exact copy + layout):
- ONBOARDING → `src/ui/screens/WelcomeScreen.tsx`
- PERMISSION → the photo-permission request screen (StartScreen area)
- PICK → `src/import/GalleryGrid.tsx` (keep ALL selection/filter/paging logic;
  restyle only — new header, stepper "Pick→Review→Done", Filter row, select dots,
  bottom "Next · N")
- FILTER → `src/ui/screens/FilterScreen.tsx`. **Key change the owner demanded:**
  the three filters are compact **rows** (Face · Location · Date); tapping **Face
  opens the FACE MODAL** and **Location opens the LOCATION MODAL** (full-screen
  sheets) so the other filters are never buried. Date stays inline (modes:
  exact / month / year with chips, per the design). Bottom bar: Clear all +
  "Show N photos".
- FACE MODAL → new `src/ui/components/FaceFilterModal.tsx` reusing the existing
  `FaceFilterPanel` avatar grid (real `getPeople()` + `person.faceThumbUri ??
  contentUri(coverAssetId)` avatars, search, "Anyone"). LOCATION MODAL likewise
  from `LocationFilterPanel`.
- BUILDING → `src/ui/screens/BuildingScreen.tsx` (breathing gradient, progress,
  "Finding your best shots", real progress messages).
- REVIEW → `src/review/ReviewScreen.tsx` — 2-col cards with a plain-language
  **reason** under each, **See other shots** (SWAP SHEET) + ✕ remove, and the
  **"Good shots that missed out"** horizontal rail. Wire reasons from `ReviewData`.
- ALBUM READY → `src/review/FinalAlbum.tsx` — editable album title, Play/Open,
  Print card, Share/Done.
tsc green after each screen, commit.

### Phase 4 — New screens, wired to the REAL engine
- ALBUMS HOME (new `src/ui/screens/AlbumsScreen.tsx`) — grid of saved albums +
  "Create new album" + empty state. Needs **albums persistence**: build
  `src/albums/album-store.ts` that saves finished albums (title, cover, photo
  ids, ReviewData/FinalPhoto, date range) to `documentDirectory` JSON (copy the
  crash-safe write pattern from `src/faces/face-index.ts`). Load on Albums tab.
- ALBUM DETAIL (new) — hero, Play (→SLIDESHOW), Share, Print card, 3-col grid.
- SLIDESHOW (new `src/review/Slideshow.tsx`) — animated Ken Burns / crossfade
  player using **react-native-reanimated** (already installed), prev/play/next,
  progress dots, speed control. This is the "animated album, not a PDF".
- SWAP SHEET (new) — shows 2–4 alternate takes of a moment; requires
  `build-album.ts` to surface **alternates per selected slot** (the runners-up
  the planner already computes). Add that to the build output.
- PHOTOS TAB (new `src/ui/screens/PhotosScreen.tsx`) — search bar, People row
  (real face avatars), Places cards (real `getCities/getCountries`), month grid
  (real library). Tapping a person/place filters.
- ACCOUNT (new `src/ui/screens/AccountScreen.tsx`) — profile, privacy card,
  Family/settings rows, sign out.
Build + install + validate on device after this phase. Commit.

### Phase 5 — Stub the backend-only screens (visual only, clearly marked TODO)
These need infra the app does not have — build them **visually per the design**
but stub the actions (no real network), each with a `// TODO(owner): needs
backend` note:
- LOGIN, FAMILY, and "Shared with you" albums + SHARE SHEET / SHARE SENT
  (cross-phone album sharing — no backend yet).
- PRINT PREVIEW / PRINT ORDERED (print fulfillment — external service).
- NAME A PERSON — wire if `face-index` can store a person name cheaply; else stub.
- MANAGE ALBUM SHEET, DELETE CONFIRM — wire to the album-store (rename/delete are
  local and real). NO PEOPLE FOUND YET / ERROR — real states.

## Definition of DONE
- Installs + runs on `177fc81a`; onboarding → Albums → Create → Pick → Filter
  (faces in a modal) → Building → Review (swap/missed) → Make album → Album ready
  → Slideshow, all in the new light design, no crash.
- Real data everywhere the engine exists (photos, people avatars, places,
  coverage selection, reasons, albums persisted + reopenable).
- `tsc --noEmit` clean, tests green, everything committed to `codex/design-impl`.
- Update `docs/APP-STATUS.md`: what shipped, what's stubbed + why, how to rebuild.

## Guardrails
- Keep the working engine intact — `build-album.ts`, `src/selection/*`,
  `src/faces/*`, `src/import/photo-index.ts` selection/filter LOGIC must not
  regress. This is a presentation + navigation + persistence change.
- Every native/model/file call lazy + guarded → neutral fallback, never crash.
- Test files: NO `node:test`/`node:assert` (repo has no @types/node). Use the
  house local-assert pattern; single-line `.ts` import under one
  `@ts-expect-error`. `npx tsc --noEmit` MUST be clean before every commit.
- Public repo: no media, no secrets/.env, no absolute home paths in code.

## Env (mandatory — global npm cache is on an unmounted drive)
```
export JAVA_HOME=/opt/homebrew/opt/openjdk@17
export ANDROID_HOME=/opt/homebrew/share/android-commandlinetools
export PATH="/opt/homebrew/opt/node@22/bin:$JAVA_HOME/bin:$ANDROID_HOME/platform-tools:$PATH"
export npm_config_cache=/Users/rohantomar/Documents/Photeo/memory-engine/.npm-cache
```
Build APK: `cd apps/mobile && npx expo prebuild --platform android --no-install &&
cd android && ./gradlew assembleRelease` → install `adb -s 177fc81a install -r
android/app/build/outputs/apk/release/app-release.apk`. Drive with
`adb shell input tap X Y` + `adb exec-out screencap -p > shot.png`. Do NOT type
credentials or touch other apps.
```
```
