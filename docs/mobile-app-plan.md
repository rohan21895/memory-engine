# Mobile + cross-platform review app — task brief for Codex

**Owner:** Codex (shipping agent). This is your territory: `apps/mobile/` (Expo + RN + TS),
`apps/desktop/` (Tauri). Claude (intelligence agent) owns the engine + this brief and the API contract below.

**Goal (Rohan's words):** *"create an app for android, mac and iphone so I can test on my phone."*
Ship the **album-review experience** — the flow we already run on the web
(`scripts/demo/review_album.py`) — as a native app Rohan can open on his **Android phone first**,
then iPhone, then Mac.

## Architecture — thin client over the LAN (local-first, no cloud)

The Mac is the engine host. All heavy work (ingest, ML runtime, selection, render) already runs
there via `scripts/demo/review_album.py`, now LAN-bindable. The phone is a **thin client** that
talks to it over Wi-Fi. Nothing leaves the machine; the phone never runs models.

```
  Android / iPhone  ──HTTP/JSON over Wi-Fi──►  Mac: review_album.py --host 0.0.0.0 :4189
   (Expo RN app)                                 (real pipeline + models + real photos)
```

- **Android + iPhone** = ONE Expo/React-Native/TypeScript codebase in `apps/mobile/`.
- **Mac** is already testable today in the browser (the same server renders a full responsive UI).
  A native Mac shell is **secondary** — either point the existing `apps/desktop/` Tauri webview at
  the review API, or defer. Do **not** block the phone app on the Mac shell. Prioritize `apps/mobile/`.

> Scoping note from Claude: one Expo codebase covers Android+iPhone, and the Mac is covered by the
> existing web UI. If Rohan wants a *true* native Mac app now, that's the Tauri desktop shell and a
> larger task — flag it, don't silently expand scope.

## Run the backend (already done, just start it)

```bash
cd ~/Documents/Photeo/memory-engine
.venv/bin/python scripts/demo/review_album.py runs/aasthamaternityphotoshoot --host 0.0.0.0 --port 4189
```

It prints the LAN URL. On Rohan's current network that is **`http://192.168.0.132:4189`**
(the app's Connect screen must let him edit this — DHCP changes it). Phone must be on the **same Wi-Fi**.

## API contract (stable; consume, don't reshape)

All JSON, `Access-Control-Allow-Origin: *`, OPTIONS preflight supported.

| Method | Path | Body / query | Returns |
|---|---|---|---|
| GET | `/progress` | — | `{state, log[], pdf: str\|null, flagged: int}` — `state ∈ intake\|running\|rendering\|review\|flagged\|done\|failed` |
| GET | `/data` | — | album review payload (below); `409 {error}` if no plan loaded |
| GET | `/thumb/<media_id>` | — | `image/jpeg`, ~512px (the only proxy size that exists) |
| GET | `/album.pdf` | — | `application/pdf` for the active variation; `404` if unrendered |
| GET | `/browse?path=<abs>` | — | `{path, parent, dirs:[{name,path}], media_count}` — folder picker for intake |
| POST | `/start` | `{folder, name, photos}` | `200` then poll `/progress`; `400 {error}` / `409 {error}` |
| POST | `/finalize` | `{pinned[], excluded[], swaps{}, decided_by, source}` | `200` then poll `/progress` |
| POST | `/approve` | `{}` | approve flagged content check, continues render |
| POST | `/variation` | `{album_id}` | switch design variation, no re-render |
| POST | `/generate` | `{style}` | plan a not-yet-generated variation (fast, no render) |

`/data` payload:
```jsonc
{
  "album_id": "e31f02f8943a…",
  "selected": [{
    "media_id": "8b2803e2…",
    "page": 1,                          // 0-based page_index, may be null
    "pose": "cluster-id" | null,
    "chosen_because": ["cleanest frame of its group (…)"],
    "alternatives": [{                  // same-shot siblings
      "media_id": "…", "pose": "…",
      "not_chosen_because": ["eyes read less open (…)"],
      "fits_slot": true | false | null  // false ⇒ "may print soft in this slot"
    }]
  }],
  "pool": [{ "media_id":"…", "quality":0.79, "pose":"…", "reasons":["…"] }],  // omitted, ranked
  "variations": [{ "album_id":"…", "style":"gallery|cinematic|editorial", "label":"…", "description":"…", "pages": 20 }]
}
```

### Swap / finalize semantics (mirror the web client exactly)
- Client keeps a `swaps` map `origMediaId → replacementMediaId` (in memory until finalize).
- The **live album** for any slot = `swaps[s.media_id] ?? s.media_id`.
- A suggestion may never be a photo already in the live album.
- "Other best" is drawn from `pool`, excluding any media whose `pose` is already in the album.
- Finalize body: `pinned` = the live album ids (per slot), `excluded` = swapped-out originals,
  plus the raw `swaps`, `decided_by:"rohan"`, `source:"mobile-app"`.

## Screens (mirror the web flow; the lightbox is the point)
1. **Connect** — editable server URL (default `http://192.168.0.132:4189`), health-check `/progress`, persist it.
2. **Album grid** — `/data.selected` in page order; thumb + `chosen_because[0]` + alt count. `expo-image`, `FlashList`.
3. **Fullscreen lightbox** (native version of what we just shipped on web) — tap a photo → full-screen pager:
   swipe or ‹ › to browse, close (✕), and a **Select** button.
   - From the grid: browse the whole album; Select → open that photo's alternatives.
   - From alternatives: browse every swap option in order; Select → apply the swap.
   Use `react-native-gesture-handler` + `react-native-reanimated` for the pager/zoom.
4. **Alternatives sheet** — "Similar (same shot/pose)" + "Other best" with reasons and "use this one".
5. **Finalize** — POST `/finalize`, show `/progress` log, handle `flagged` (show count → `/approve`), then open `/album.pdf`.

## Stack rules (from Rohan's global CLAUDE.md → react-native-skills)
Expo SDK, TypeScript. **FlashList** for lists, **Reanimated** for animation, **expo-image** for photos,
native navigators (expo-router or react-navigation native stack), **Nativewind** for styling.
No generic SaaS-blue; pick a deliberate tone (the web UI is warm-charcoal editorial — match it).

## Test on the phone (Android first — Rohan's known workflow)
1. Start the backend with `--host 0.0.0.0` (above).
2. `cd apps/mobile && npx expo start` → **Expo Go** on the phone (same Wi-Fi) scans the QR, OR a dev
   build via `adb install -r` (his SDK: `/opt/homebrew/share/android-commandlinetools`, JDK `openjdk@17`).
3. On the Connect screen enter `http://192.168.0.132:4189`. Grid should load the maternity album with real thumbs.

## Acceptance
- [ ] Android: grid loads real thumbnails over Wi-Fi; tap → fullscreen; swipe browses; ✕ closes.
- [ ] Alternatives open; "use this one"/Select applies a swap; grid reflects "N swapped".
- [ ] Finalize round-trips: progress log streams, flagged→approve works, album PDF opens.
- [ ] Same build runs on iPhone (Expo Go / TestFlight-less dev build).
- [ ] Mac: covered by the web UI, or a thin Tauri wrapper (state which; don't over-build).
- [ ] No hardcoded home paths in committed code; server URL is user-editable and persisted.

## Boundaries
`apps/mobile/`, `apps/desktop/` are yours. The API contract above is Claude's — if you need a field
that isn't there (e.g. a larger proxy for crisp fullscreen, or a `/runs` list endpoint to pick an album),
**open an issue / comment on the tracking issue**, don't reshape the server. Claude will add it.
