# scripts/demo

Two scripts. One builds a synthetic library, the other runs the pipeline over it
and reports honestly.

```bash
python3 scripts/demo/make_library.py --out /tmp/demo-library
python3 scripts/demo/run_demo.py /tmp/demo-library --search sunset
```

Neither script will touch `~/Pictures`, `~/Downloads`, a `DCIM` folder or a
`.photoslibrary` bundle. The user's own photographs are not a test fixture.

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

Twelve stages. Every stage reports `ok`, `SKIPPED`, `NOT WIRED` or `FAILED`, in
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

### Known standing failures

Two checks fail on a correct library today. They are attributed in the output
and counted separately from unexplained failures, but they are still failures
and the run still exits 1.

1. **A one-third-truncated JPEG is not quarantined.** `workers/ingest` decodes
   with the Rust `image` crate, which tolerates the truncation and returns a
   partly-grey picture, so the record comes back `state=proxied` with a pHash
   and a thumbnail. The contract disagrees:
   `contracts/fixtures/media-record/valid/file-truncated-quarantined.json` is
   quarantined with `kind: unknown`. The 512-byte control *is* quarantined, so
   quarantine works — it just does not catch this.
2. **Dated records are not orderable on the timeline.** As of this commit
   `metadata.rs` writes the EXIF wall-clock reading into
   `capture.captured_at.local` and always leaves `utc` as `None`; nothing
   anywhere resolves a timezone. `media-db` orders on `captured_utc`, so a
   fully dated library returns an empty timeline — and an empty list reads like
   "no photos matched".

   Both this check and the stage-10 skip that follows from it are derived at
   runtime, so once anything populates `utc` — reading EXIF `OffsetTimeOriginal`
   would do it for cameras that record one — the check passes and event
   clustering starts running without a change here. Note that the generator's
   stills carry no offset tag, so a library regenerated after such a fix will
   still be `utc`-less unless `make_library.py` is taught to write one.
