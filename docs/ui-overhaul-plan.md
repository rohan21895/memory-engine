# Photeo mobile — UI/UX overhaul plan (granny-proof)

## The brief (from the owner)
"The UI and UX suck right now. Overhaul the complete UI/UX so the flow is really
simple — simple enough for my granny to run this app. Everything should be clear,
with proper instructions everywhere."

Success test: **a 70-year-old who has never used the app can go from opening it
to a finished album without asking anyone for help** — because at every moment
the screen tells her (1) where she is, (2) what to do next, and (3) that her
photos are private and nothing can go wrong.

## What's wrong today (be honest about the current app)
- **Three sources on the home screen** (Device gallery / Local folder / Google
  Photos) with jargon subtitles ("Storage Access Framework", "Photos Picker API ·
  PKCE"). A first-timer has no idea which to pick. Google Photos currently errors
  (`invalid_request`) — a dead end that makes the app feel broken.
- **The picker is power-user dense**: a long horizontal wall of filter chips
  (date + 24 albums + places), a hidden long-press-drag "slide select" nobody
  discovers, "Select all", numbered badges — with zero on-screen explanation.
- **No sense of progress**: you don't know it's a 3-step flow (pick → review →
  done), how long "building" will take, or what "building" even means.
- **Technical language in results**: pick reasons say things like "cosine
  similarity 0.923" and "thumbnail-detail proxy" — meaningless to a normal person.
- **No onboarding, empty states, loading states, or friendly error recovery.**

## Design direction (NON-NEGOTIABLE — read before coding)
1. **Load and apply these skills first** (they are installed): `/frontend-design`,
   `/bencium-innovative-ux-designer`, and `/react-native-skills`. Synthesize them
   before writing UI. No generic AI aesthetic, no default SaaS blue, no glassmorphism.
2. **Keep Photeo's identity** — it already has a good editorial dark look:
   background `#141311`, gold accent `#c8a24a`, cream text `#e8e4dc`, muted
   `#9a927f`, panels `#1c1a17`, hairlines `#2c2a25`. Keep the "ON-DEVICE · PRIVATE"
   trust cue. We are SIMPLIFYING and CLARIFYING, not restyling for the sake of it.
3. **Typography with character but maximum legibility**: pair a warm editorial
   display face for big titles (bundle via `expo-font` — e.g. Fraunces or
   Instrument Serif; NOT Inter/Roboto/Space Grotesk) with a clean, highly legible
   sans for body/UI. **Body text ≥ 17pt, titles large, line-height generous.**
   Granny legibility beats cleverness — when in doubt, bigger and simpler.
4. **One primary action per screen.** Every screen has exactly one obvious big
   button (gold, full-width, ≥56pt tall, high contrast). Secondary actions are
   visually quieter. Never show two equally-weighted choices.
5. **Plain, warm language everywhere.** 6th-grade reading level. No jargon: banish
   "Storage Access Framework", "Photos Picker API", "PKCE", "cosine", "embedding",
   "proxy", "take", "cluster". Put ALL user-facing copy in one strings file
   (`src/ui/copy.ts`) so it reads like a friendly human wrote it.
6. **Instructions everywhere, but calm.** Each screen has a one-line plain-language
   helper under the title telling granny exactly what to do. Reassure about
   privacy repeatedly and gently. Never a naked screen with no guidance.
7. **Accessibility is part of "granny-proof", not optional**: min 48dp touch
   targets, WCAG AA contrast, `accessibilityLabel`/`accessibilityHint` on every
   control, `accessibilityRole`, dynamic-type friendly, works one-handed.

## The new flow (5 screens, a single guided line)
A visible **3-step progress indicator** ("Pick → Review → Done") sits at the top
of the three main steps so granny always knows where she is and how much is left.

### Screen 0 — Welcome (first launch only; persist a "seen" flag)
- One warm screen. Big title, 2 short lines: what Photeo does + privacy promise.
  e.g. *"Turn the photos on your phone into a beautiful album. Everything happens
  on your phone — your photos never leave it."*
- One big primary button: **"Get started"**. Nothing else to decide.

### Screen 1 — Start
- Big title: **"Make a photo album"**. Helper line: *"We'll help you pick your
  best photos and turn them into an album — in 3 easy steps."*
- The 3-step indicator shown here, inert, so she sees the journey.
- ONE big primary button: **"Choose photos"** → opens the on-device picker
  (today's "Device gallery" — the local, no-login source). This is THE path.
- A single quiet text link below: **"Other ways to add photos"** → a simple sheet
  offering "A folder on my phone" and "Google Photos". Demote these; they are not
  the main path. If Google Photos still errors, its button must show a friendly
  message and route back — never a raw Google error page as a dead end.
- Keep the "ON-DEVICE · PRIVATE" cue.

### Screen 2 — Pick your photos (overhaul GalleryGrid)
- Header: step indicator (Step 1 of 3), title **"Pick your photos"**, helper
  banner: *"Tap the photos you like. Don't worry about picking perfectly — we'll
  choose the best shots for you."*
- **Selection must be obvious**: large tap targets, a clear filled gold check when
  selected, dimmed when not. Keep the numbered order badges (they're nice) but the
  check must read as "selected" at a glance.
- **Keep slide-select and Select-all, but make them discoverable**: a small,
  dismissible hint *"Tip: press and hold, then drag to select many at once."* and
  a clearly labeled **"Select all"** / **"Clear"**.
- **Tame the filters**: replace the long chip wall with a single **"Filter"**
  button that opens a simple sheet: *All photos* (default) · *By date* · *By album*
  · *By place* · *By person*. Inside each, show the existing chips. Default view is
  just "All photos" so granny is never confronted with 30 chips. (The place/person
  data + logic already exist — this is presentation only. A "By person" section
  will be fed by the face pipeline; design the sheet so a People row slots in.)
- Bottom: one big primary button **"Next"** showing the count (**"Next · 8 photos"**),
  disabled with helper *"Tap at least one photo"* until ≥1 selected.
- Loading/empty/denied states with friendly copy (see states section).

### Screen 3 — Making your album (the build step)
- Full-screen, calm, reassuring: **"Making your album…"** with a helper like
  *"Looking through your photos and picking the best ones. This stays on your
  phone."* A gentle progress/activity feel (not a spinner alone — show it's working
  and roughly how far). Never leave granny staring at a frozen screen.

### Screen 4 — Review your album (overhaul ReviewScreen)
- Step indicator (Step 2 of 3), title **"Here's your album"**, helper: *"We picked
  these for you. Happy with them? Make your album — or go back to change your
  photos."*
- Show the chosen photos as album pages/spreads, large and pretty.
- **Plain-language reasons only.** Translate the engine's reasons into human
  sentences: *"Sharp photo, everyone smiling"*, *"Best of several similar shots"*,
  *"Eyes open"*, *"Left out: someone was blinking"*, *"Left out: it's a screenshot"*.
  (Claude will expose these human strings from selection; you render them. Never
  show numbers like 0.923.)
- One big primary button **"Make my album"**. Quiet secondary **"Back"** to re-pick.

### Screen 5 — Done (overhaul FinalAlbum)
- Step indicator (Step 3 of 3, complete), a small moment of celebration, title
  **"Your album is ready!"**, keep **"Saved on this phone"** privacy cue.
- Primary **"Make another album"**. (If a share/export exists later, it slots here.)

## Build a small design system (so it's consistent and granny-proof)
Create `src/ui/` with:
- `tokens.ts` — colors (from the palette above), spacing scale, radii, the type
  scale (display / title / body ≥17 / label), min touch target 48.
- `fonts.ts` + bundled font files via `expo-font` (display serif + body sans).
- Components (all with accessibility props, large targets):
  `PrimaryButton`, `SecondaryButton`, `ScreenHeader` (title + helper line),
  `StepIndicator` (Pick → Review → Done), `HintBanner` (dismissible tip),
  `EmptyState`, `LoadingState`, `ErrorState`, `FilterSheet`, `Card`.
- `copy.ts` — ALL user-facing strings, warm and plain, in one place.

## States — never leave a blank/confusing screen
For every screen define: **loading** ("Looking through your photos…"), **empty**
("No photos here yet"), **permission denied** (plain steps: *"Photeo needs to see
your photos to make an album. Tap Allow, or open Settings → Photeo → Photos → Allow
all."* with a button that deep-links to settings), and **error** (friendly message
+ a way back, never a dead end or a raw technical error).

## HARD CONSTRAINTS
- **Do NOT break existing functionality or contracts.** The flow's data plumbing
  works today (source pickers → `build-album.ts` → selection → review → FinalAlbum).
  Keep the same props/callbacks (`onConfirm`, `onFinalize`, `PickedPhoto`, the
  selection output shape). This is a UI/UX layer + restructure, not a rewrite of
  the engine. If you must change a prop, keep it additive/back-compatible.
- **Location + People filters already exist / are landing** (place/city/country
  chips in the picker; a People filter is being wired). Design the Filter sheet to
  host them; don't delete the filtering logic.
- **Pick-reason strings**: Claude will expose human-readable reasons from selection
  — render those; do not invent your own scoring. If a reason is missing, fall back
  to a neutral friendly line.
- Keep it **Android-first** (test mental model on a phone), portrait, one-handed.
- Ship in TypeScript, `npx tsc --noEmit` clean. Reuse existing libs already in the
  app (expo-image, FlashList, gesture-handler, reanimated if present). Add
  `expo-font` for the display font; avoid other new heavy deps.

## Suggested work packages (Codex may re-partition)
1. **Design system + copy** (`src/ui/*`, fonts, tokens, components, copy.ts).
2. **Onboarding + Start screen** (Screen 0 + 1, source demotion, "Other ways" sheet).
3. **Picker overhaul** (Screen 2: header/helper/hint, obvious selection, Filter
   sheet replacing the chip wall, big Next button, all states).
4. **Build + Review + Done overhaul** (Screens 3–5: progress screen, plain-language
   review, celebratory done, step indicator across all three).
5. **Accessibility + states pass** (labels, contrast, touch targets, empty/denied/
   error everywhere) + a self-review against the "granny test".

## Definition of done
- The 3-step journey is obvious and labeled the whole way through.
- Every screen has one clear primary action and a plain-language helper line.
- No jargon anywhere in the UI; all copy lives in `copy.ts`.
- All loading/empty/denied/error states are handled with friendly copy.
- Accessibility: 48dp targets, AA contrast, labels/hints on all controls.
- Existing functionality still works end to end; `tsc` clean.
- A short written "granny walkthrough": screenshots or step list proving a novice
  can finish an album unaided.
```
