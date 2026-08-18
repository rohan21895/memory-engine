# scripts/demo

Two scripts. One builds a synthetic library, the other runs the pipeline over it
and reports honestly.

```bash
python3 scripts/demo/make_library.py --out /tmp/demo-library
python3 scripts/demo/run_demo.py /tmp/demo-library --search sunset \
    --ml-runtime 127.0.0.1:50051
```

Neither script will touch `~/Pictures`, `~/Downloads`, a `DCIM` folder or a
`.photoslibrary` bundle. The user's own photographs are not a test fixture.

## What a person should expect to see

The run prints fifteen numbered stages, then a library summary, then a ledger.
The last three stages are the product: stage 13 runs `services/pipeline` — the
same code `python -m memory_engine_pipeline` runs — and stages 14 and 15 **open
what it wrote**.

With a model host serving the full Tier 1 stack, on the default 60-still /
10-clip library:

```
  [13/15] pipeline — album, reel, and the renders   ok
          ingest        completed   full scan
          analysis      completed   analysis complete
          faces         completed   65 faces, 0 eligible for automated output,
                                    65 awaiting review
          ranking       completed   ranking complete
          album         completed   22-page album, print validation passed
          render-print  completed   PDF/X-4 written to .../outputs/pdf/408ff52a….pdf
          story         completed   reel EDL 02e76b210a82: 5 clips, 7.50s at 30 fps,
                                    0 of 5 cuts beat-locked, 0 certified word-safe;
                                    film EDL 62bc052607b5: 10 shots in 3 acts,
                                    22.53s at 30 fps, holds 2.00-2.87s (pacing
                                    spread 0.38, 10 of 10 window-limited),
                                    0 L-cuts, 0 certified word-safe
          render-video  completed   reel written to .../outputs/video/02e76b21….mp4;
                                    film written to .../outputs/video/62bc0526….mp4

  [14/15] print artifact — opened and measured      ok
          AlbumSpec declares 22 pages, validation pass (0 errors, 22 warnings)
          22 page objects, /Count [22]
          output condition: FOGRA39 Coated
          MediaBox: [0 0 867.401575 867.401575]   TrimBox: [8.503937 … 858.897638]
          checks: 7/7 passed

  [15/15] video artifacts — probed and sampled      ok
          h264 854x480 @ 30/1  7.500s   audio: aac 48000Hz 2ch
          EDL 02e76b210a82 kind=reel: 5 clips, validation pass
          decoded 225 frames
          frame brightness YAVG min=97.3 max=149.7 over 225 frames
          h264 854x480 @ 30/1  22.533s  audio: aac 48000Hz 2ch
          EDL 62bc052607b5 kind=film: 10 clips, validation pass
          decoded 676 frames
          frame brightness YAVG min=96.1 max=151.2 over 676 frames
          checks: 10/10 passed
```

**All three promised outputs are produced, and the third is honest about what
it is missing.** A print-ready PDF, a 15-second-target reel, and a film: a
three-act cut of the same moments in chronological order, planned by
`packages/story-engine/memory_engine_story/film.py` and rendered by the same
worker. Stage 13 prints both EDLs and stage 15 opens both files.

The film is a genuinely different cut — different running order, different
holds, `kind: "film"`, `story_arc.template: "three_act"` — and not a longer
reel. What it is *not* yet is a film in the sense the build plan means:

* **`0 cuts certified word-safe`, on the film as on the reel.** There is no
  transcript backend, so speech-aware trimming, the L-cut and the
  error-severity mid-word gate — the three things that separate a film planner
  from a long reel — are all built, tested and inert. The plan emits no
  `no_mid_word_cut` finding at all. Absent is not passing.
* **Every shot is window-limited.** The holds come out as long as each moment's
  trimmable window allows rather than as long as the pacing policy asks, so the
  reported `pacing spread` is a fact about the moment scorer's windows and not
  about the film's design. The run prints the count.
* **It runs about 22 seconds, against a 60-second floor for the form.** The
  library only affords ten shots. The plan says so rather than presenting a
  short thing as a film.
* **Its sources run in declaration order.** Every synthetic clip carries
  `capture.captured_at.precision: unknown`, so the planner is given no
  chronology and refuses to invent one by sorting on content hash.

Read the numbers, not the ticks:

* **22 pages, 20 photos.** The cover repeats the hero, and the last page is
  blank because the vendor profile's page count comes in increments of two.
* **22 warnings, 0 errors.** Twenty-one are "300.3 DPI clears the 300 floor but
  is below the vendor's preferred 350" — the layout solver shrank each frame to
  about 122mm to clear the floor from a 1440px source, so the book is small
  photographs on large pages. One is "the vendor pins no `icc_hash`, so the
  profile was matched by name only". Warnings do not block; errors do.
* **`0 of 5 cuts beat-locked` and `0 certified word-safe`.** Not a pass — those
  producers do not exist. See "what is not measured" below.
* **`0 faces eligible for automated output`.** Correct and deliberate: no
  calibrated threshold and no enrolled person exist, so nothing may be named
  unattended. The faces still reach the album as *safety* rectangles.

### What the artifact stages actually check

They exist because everything this repo had before them checked a filename or a
size. `services/pipeline`'s end-to-end test asserted `%PDF` and "bigger than
100kB" — and passed over a renderer that sheared every page and washed every
photo out to near-white, for as long as that renderer existed.

| stage 14, on the PDF | stage 15, on each MP4 |
| --- | --- |
| page objects counted in the file | codec, raster, rate, duration from ffprobe |
| page tree `/Count` agrees with them | **every frame decoded** and counted |
| both agree with the AlbumSpec's page count | count agrees with duration × rate |
| a PDF/X `OutputIntent` is declared | `blackdetect` finds no black run ≥ 0.1s |
| it embeds a `DestOutputProfile` | frame brightness varies (not one held frame) |
| every page carries a `TrimBox` | container rate equals the EDL's timeline rate |

### Without `--ml-runtime`

Stages 13–15 are `SKIPPED`, and the ledger says the album, the print gate, the
PDF, the reel and the film are all unproven. That is honest rather than
convenient:
analysis is a hard gate, so with no model host nothing downstream of it runs.
Stages 1–12 still walk, hash, dedupe, date and cluster the library.

**A machine without SigLIP weights cannot produce the PDF at all.** The
`siglip2-so400m-384` entry in `models/registry.json` has no weights on disk and
its `weights.source_url` is a model *page*, not a file, so
`scripts/models/fetch_weights.py` cannot fetch it. Analysis requires the
embedder alongside the two face models and reports
`the model host is serving but cannot provide: siglip2-so400m-384
(weights_missing)`; album and render-print then refuse. The reel and the film
are unaffected and still render, because the video path does not go through
SigLIP.

## make_library.py

Draws ~200 stills with PIL and encodes ~10 clips with FFmpeg, deterministically
from `--seed`. Same seed, same bytes, same BLAKE3, same `media_id`s.

**Nothing in it is a photograph.** The faces are cartoons — an oval, two eye
ellipses, an arc for a mouth. They are there so a *plumbing* failure in the face
stack (a detector that is never called, a colour-order swap, a resize convention
mismatch) shows up as a crash or an obviously wrong count instead of as silence.
They prove nothing about detection quality, and no recall/precision number
measured on this library may reach a model card, an eval report or a regression
gate. Real numbers come from `packages/eval-harness/` against a consented
benchmark library.

The same caveat covers everything else: clean synthetic exposure, no motion
blur, no sensor noise, no lens character, no generation loss. Quality and
aesthetic thresholds tuned here will not transfer.

What it *does* represent, because these are the cases that break things:

| case | why |
| --- | --- |
| two near-duplicate bursts (5 frames and 4) | what dedupe exists for |
| a still with no EXIF and no date in its name | must stay `precision: unknown` |
| a still with a WhatsApp filename and no EXIF | must be dated from the name |
| 8 stills whose EXIF and mtime differ by ~3 years | trusting mtime reorders a library |
| `GX010012/GX020012/GX030012.MP4` | GoPro GX chapter span, indexes 0,1,2 |
| `GOPR0044.MP4` + `GP010044.MP4` | legacy span — a *different* index convention |
| a vertical clip, and clips at 24/25/29.97/30/60 fps | 29.97 is where frame maths breaks |
| one clip with no audio track | the ambient-music path must cope |
| a 512-byte JPEG | **positive control** — must be quarantined |
| a one-third JPEG | must be quarantined; currently is not (see below) |
| a truncated MP4 | must fail at the proxy stage |
| a zero-byte file, a stray `.txt`, an `.xmp` sidecar | every real library has these |
| an EXIF-orientation-6 still | oriented size is the transpose of stored size |

`MANIFEST.json` records the BLAKE3 of every file — which is exactly the
`media_id` ingest derives — plus the expectations above. That is what makes
`run_demo.py` able to *check* rather than merely print. The generator also
reconciles its manifest against the directory before writing it, and exits 2 if
the library is incomplete (for example, `--no-video` or no FFmpeg).

## run_demo.py

Fifteen stages. Every stage reports `ok`, `SKIPPED`, `NOT WIRED` or `FAILED`, in
the running output and again in a ledger, with the missing dependency named and
the consequence spelled out.

* exit **0** — every stage ran and every declared expectation was met
* exit **2** — nothing failed, but something did not run. An INCOMPLETE run, not a pass.
* exit **1** — something failed

Three files in this repo have shipped a stage that printed a tick while
skipping its work. Hence the insistence.

Skips are *derived*, not hard-coded: stage 10 skips because no record carries a
UTC instant, stage 11 because no record carries quality signals. When those
fields start being populated, the stages start running by themselves.

## What is not measured, and therefore not claimed

Both cuts are made from the features that exist. Four producers do not, and
every run names all four rather than letting a reader assume them:

| missing | consequence |
| --- | --- |
| transcription (faster-whisper) | word timings unknown, so **no cut is certified word-safe**, and the EDL carries no `no_mid_word_cut` finding *at all* — absent, not passing |
| beat detection (no bundled music) | `packages/story-engine/music/library.json` is `audio_bundled: false`, so there is no `BeatGrid` and **no cut is beat-locked**; the build plan's <50ms downbeat gate cannot be measured |
| face / smile detection in video | `face_presence`, `max_face_area_ratio`, `smile_intensity` absent; neither cut can prefer a face, and the film's pacing loses its emotional-peak input |
| audio events (CLAP) | speech and noise ratios absent; the duck-under-speech and wind-noise rules can never fire |

A cut made without speech data is a different product from one made with it,
and it costs the film more than the reel: speech-aware trimming, the L-cut and
the error-severity mid-word gate are the three things that make a film planner
different from a long reel, and all three are inert. The stage counts
`beat_locked: 0`, `word_safe_cuts_certified: 0` and `l_cuts: 0`, and prints
them.

Two more, both in the summary line every run:

* **`MediaRecord.video` is null for every video**, so the source's pixel
  geometry is unmeasured. The render target is therefore the 480p proxy raster
  (854×480), not a 1080p master, and vertical reframing is disabled because a
  landscape-to-vertical crop needs a source aspect ratio nobody has measured.
* **Five of ten clips are excluded** from both cuts, with their rates and rasters
  named: an EDL carries one timeline rate and one target geometry, and the
  library deliberately contains 24 / 25 / 29.97 / 30 / 60 fps and a vertical
  clip.

## Known standing failures

One check fails on a correct library today. It is attributed in the output and
counted separately from unexplained failures, but it is still a failure and the
run still exits 1.

1. **A one-third-truncated JPEG is not quarantined.** `workers/ingest` decodes
   with the Rust `image` crate, which tolerates the truncation and returns a
   partly-grey picture, so the record comes back `state=proxied` with a pHash
   and a thumbnail. The contract disagrees:
   `contracts/fixtures/media-record/valid/file-truncated-quarantined.json` is
   quarantined with `kind: unknown`. The 512-byte control *is* quarantined, so
   quarantine works — it just does not catch this.

## Dates, and why the generator writes a timezone offset

An EXIF `DateTimeOriginal` with no `OffsetTimeOriginal` is a wall-clock reading
with no zone. That is not an instant, so `captured_at.utc` correctly stays null,
`media-db` — which orders on `captured_utc` — returns an empty timeline, and the
album stage refuses: *"1 cluster(s) found, none of them dated"*. No album could
be planned from this library at all.

Since generator version 2, the three events whose cameras would have written one
(Pixel 6, iPhone 14 Pro) carry `+05:30`; the 2019 Canon does not, because
`OffsetTimeOriginal` is EXIF 2.31 and plenty of bodies of that generation never
wrote it. So a default library is **36 of 60 stills dated to a real instant and
24 deliberately not** — enough to build a book, while keeping the zoneless case
that the "never invent a timezone" rule exists for. `MANIFEST.json` records
`exif_offset_time_original` per file; a record with a `utc` against a null
offset there means the pipeline invented a zone.

Bumping the generator version changes every file's bytes and therefore every
`media_id`.
