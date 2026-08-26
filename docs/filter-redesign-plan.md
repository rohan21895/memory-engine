# Photeo — Filter UX Redesign (parallel handoff)

Owner ask (2026-08-24): replace the dropdown filter with a **structured,
on-screen** filter surface. Three main filters, each **searchable**, with
**real cropped-face avatars** for the Face filter. Whiteboard: the Selector Tool
filters on **Date + Location + Face**.

## What's wrong today
`src/ui/components/FilterSheet.tsx` is a `Modal` with stacked sections; the
Person section can only show `coverAssetId` (the *whole photo*), not a face. The
owner wants the three filters laid out clearly on screen (not a dropdown),
each with a search box, and faces shown as circular avatars.

## Target: `FilterScreen` — three equal, structured filters
A full-height screen (push, not modal) reached from the picker's **Filter**
button. Header "Filter your photos" + Back. Below it, three clearly separated,
labelled sections in a fixed vertical order — **Face · Location · Date** — each
its own titled block (not a dropdown). A section is collapsed to a summary row
when unused and expands in place; only one open at a time is fine. Every section
that can have many options gets a **search box** at its top. Bottom bar: "Clear
all" (secondary) + "Show N photos" (primary, live count). Selecting an option
updates the live count; "Show" returns to the picker with the filter applied.

Keep the existing filter **semantics** and GalleryGrid wiring intact — this is a
presentation swap. Reuse `colors/spacing/typeScale/fonts/radii` tokens and the
existing `PrimaryButton`/`SecondaryButton`/`ScreenHeader`. Accessibility labels
on every control (the granny bar). Empty/loading states per section.

### Face filter (the headline change)
- Grid of **circular face avatars** — one per person from `getPeople()`.
- Avatar image = `person.faceThumbUri` when present, else fall back to the
  full-frame `contentUri(person.coverAssetId)` (never blank).
- Under/beside each: "N photos". Selected avatar gets a gold ring.
- Search box filters people (match on any future name; for now filter is a
  no-op placeholder over an unnamed set — keep the box, wire it to filter by
  `faceCount`/order so it's functional, TODO name search when naming ships).
- "Scanning faces…" state while `faceIndexStatus()` is incomplete; keep
  surfacing new people as the scan progresses (already supported).

### Location filter
- Searchable list, two groups: **Countries** then **Cities** (data from
  `getCountries()` / `getCities()`), plus an "Any place" reset.
- Search box filters the visible place rows by label (case-insensitive).
- Show the photo count per place (already available as `detail`).

### Date filter
- The existing presets (All / date ranges / months from `getMonths()`).
- No search needed, but keep the same block styling as the other two.

## Contract (already on `main`, commit 1722398)
`FaceIndexPerson` now has optional `faceThumbUri?: string`. Worker A fills it;
Worker B consumes it with the coverAsset fallback above. Do not change this
type's shape.

## Parallel workstreams (disjoint files — clean merge)

### Worker A — face-crop thumbnails (`src/faces/` ONLY)
Goal: populate `FaceIndexPerson.faceThumbUri` with a small **persisted circular
face crop** (the strongest/cover face for that person), so the Face filter shows
actual faces.
- The scan already crops faces for embeddings (`face-index.ts` ~L349–515,
  `imageManipulator` + padded box). Persist a small (e.g. 96–128px) square JPEG
  crop of the cover face to `documentDirectory` (stable name by person id), and
  set `faceThumbUri` (a `file://` uri) in `summariesForPeople` / the people
  projection. Reuse the crop you already compute — don't re-detect.
- Circular masking is the UI's job (borderRadius); ship a square crop centred on
  the face box (keep the existing padding).
- Guarded/lazy as always: if cropping/persisting fails, leave `faceThumbUri`
  undefined — never throw, never block the scan (onnxruntime lesson).
- Keep the persisted crop out of git (it lives in device documentDirectory at
  runtime; add nothing to `assets/`).
- Test: a Node `*.test.ts` (house local-assert pattern, single-line `.ts`
  import under one `@ts-expect-error`, NO `node:test`/`node:assert`) covering
  the projection sets `faceThumbUri` when a crop uri is supplied and leaves it
  undefined otherwise.
- Do NOT touch `src/ui/**` or `GalleryGrid.tsx`.

### Worker B — structured FilterScreen (`src/ui/**` + `GalleryGrid.tsx` ONLY)
Goal: build `FilterScreen` per the spec above and replace the `FilterSheet`
modal usage in `GalleryGrid.tsx`.
- New files: `src/ui/screens/FilterScreen.tsx`, and small panel components under
  `src/ui/components/` — `FaceFilterPanel.tsx`, `LocationFilterPanel.tsx`,
  `DateFilterPanel.tsx`, `FilterSearchBar.tsx`. Export via `src/ui/index.ts`.
- Consume `getPeople()` / `assetIdsForPerson()` (faces), `getCountries()` /
  `getCities()` / `getMonths()` (photo-index) — all already imported in
  GalleryGrid; lift what you need. Face avatars use `person.faceThumbUri ??
  contentUri(person.coverAssetId)`.
- Keep the current filter STATE + intersection logic in GalleryGrid
  (`placeSet`/`personSet`/`datePreset`) exactly as-is — only swap the surface
  that sets it. `FilterSheet.tsx` may stay in the tree unused or be deleted; if
  deleted, remove its `src/ui/index.ts` export too.
- Live "Show N photos" count: reuse the same set-intersection GalleryGrid
  already computes (compute the candidate count for the pending selection).
- You may leave `faceThumbUri` unpopulated in your tree — the fallback keeps it
  visually correct; Worker A's data lands on merge.
- Do NOT touch `src/faces/**`.

## Definition of done (per worker, before you report)
- `cd apps/mobile && npx tsc --noEmit -p tsconfig.json` → **0 errors**.
- Your `*.test.ts` (if any) runs green.
- Commit to your branch with a clear message (end:
  `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`).
- Write a 5-line report to your `-o` output file: what changed, files touched,
  tsc status, anything unfinished.

## Guardrails
- Keep contracts stable: `onConfirm`, `PickedPhoto`, the GalleryGrid filter
  state, `FaceIndexPerson`.
- Every native/model/file call guarded → neutral fallback, never crash.
- Public repo: no media, no secrets, no absolute home paths in code.
- House test rules (no `node:test`/`node:assert`; single-line `.ts` import under
  one `@ts-expect-error`; local assert helper). tsc MUST be clean before commit.

## Env (global npm cache is on an unmounted drive — mandatory)
```
export JAVA_HOME=/opt/homebrew/opt/openjdk@17
export ANDROID_HOME=/opt/homebrew/share/android-commandlinetools
export PATH="/opt/homebrew/opt/node@22/bin:$JAVA_HOME/bin:$ANDROID_HOME/platform-tools:$PATH"
export npm_config_cache=/Users/rohantomar/Documents/Photeo/memory-engine/.npm-cache
```
Claude reviews both branches, merges A then B, rebuilds the release APK, and
installs+validates on device `177fc81a`.
