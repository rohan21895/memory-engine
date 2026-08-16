# Music library

The build plan lists music licensing as a Phase 0 decision because it "shapes the beat-sync design and reel product". This records the decision taken and, more usefully, what it actually changes downstream.

## The decision: CC0 / public domain only, local files

Not a catalogue deal, not attribution-required Creative Commons, not a generated score.

## What that decision buys, concretely

This is the part that matters — the licence class is not a legal footnote, it changes the architecture:

| | CC0 (chosen) | Attribution-required CC | Catalogue deal |
|---|---|---|---|
| Reel must display attribution | **No** | Yes — burned-in text or a caption the user must not delete | No |
| Needs a network call to play | **No** | No | Usually yes, per-track licence check |
| Per-share licence verification | **No** | No | Often yes |
| Shareable commercially | **Yes** | Yes, with attribution intact | Per contract |
| Cost | Free | Free | Revenue share or fee |
| Catalogue quality | Thin | Better | Best |

Choosing CC0 means the reel planner does **not** need an attribution overlay track, the share flow does **not** need a licence check before upload, and the whole pipeline stays offline — which is the product's central promise. Every one of those would have to be built if we started with attribution-required music and changed later, so the cheap decision now is the restrictive one.

`MusicCue.license` in the EDL contract already models all three cases, and `music_license_covers_destination` is a hard validation check. Nothing here is load-bearing on the contract; it just means the check always passes on the default path.

## What is actually in the library right now

**Nothing playable.** Three placeholder entries define the *shape* — BPM hint, energy profile, which output kinds they suit — so the reel planner and beat grid can be built and tested against realistic metadata. `audio_bundled: false` and every entry is `status: placeholder`, `verified: false`.

Placeholders rather than real tracks because I will not assert that a specific track carries a specific licence without checking it at the source, and a fabricated licence claim in a file called `library.json` is exactly the kind of thing that gets shipped and believed.

## Filling it in

Genuinely CC0 sources worth drawing from:

- **Free Music Archive** — filter to CC0 specifically; most FMA material is CC BY or CC BY-NC, which is *not* what this policy accepts.
- **ccMixter** — same caveat, filter hard.
- **Musopen** — public-domain classical.

For each track: download, verify the licence on the page at the time of download, record the URL and date, set `verified: true`. The library file is the audit trail.

**The trap worth naming for classical:** a composition can be public domain while the *recording* of it is not. Beethoven died in 1827; the 2019 orchestra that recorded him did not. Musopen is careful about this and states the recording's status separately — check that field, not just the composer's dates.

## If this decision is revisited

Most likely trigger is CC0 catalogue quality proving too thin for reels, which is a real risk — the bar is a blind A/B where ≥40% of viewers cannot tell our cut from a human editor's, and music carries a lot of that.

Moving to attribution-required CC costs: an attribution overlay in the EDL (a `titles` track already exists in `Track.role`), share-flow copy, and a rule that the user cannot delete the credit. Moving to a catalogue deal costs those plus a licence-check network call, which is the first thing in the pipeline that would make an offline render impossible — worth weighing carefully against the privacy promise.

## Format

`library.json` is not a contract schema; it is story-engine's own config, and `tests/test_music_library.py` checks that every entry's `license` block is a valid `MusicLicense` per `contracts/schemas/edl.schema.json`, so a track that could not legally be used in a reel cannot sit in the library looking usable.
