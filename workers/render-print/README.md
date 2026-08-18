# render-print

Deterministic local `AlbumSpec` to PDF/X-4 rendering. The worker rasterizes each page at the
vendor's preferred DPI (never below its floor), converts through the resolved vendor ICC
profile, embeds that profile as the PDF output intent, and writes full-bleed pages with
explicit trim and bleed boxes.

Export is a hard gate. The worker requires a passing validation report with evidence for
the contract's five required checks, rejects contradictory findings and unsafe/licence
state, enforces page increments again, and never exposes an override.

The executable accepts a persisted `JobSpec` and `AlbumSpec`:

```text
memory-engine-render-print run <job-spec.json> <album-spec.json>
```

The job's local params schema requires `output_path`, `work_directory`, `icc_profile`,
`asset_paths`, and `font_paths`. Production ICC profiles use `{ "name": "...", "path":
"..." }`; the named `sharp` built-ins are available for development fixtures. Media and
font paths are explicit resolver inputs because neither path exists in `AlbumSpec`, and
the renderer does not invent either one.

Pages are stored by content hash and checkpointed individually. Checkpoint version 2 binds
each artifact to the canonical page plan, resolved placement/font byte digests, ICC digest,
DPI, raster dimensions, channel count, and the explicit page-renderer version. Resume
re-authenticates those inputs plus the JPEG's embedded profile before accepting a cache hit;
version-1/pre-CMYK-fix pages are always rerendered. A killed job publishes the final PDF
without overwriting different bytes. Only a completed version-2 job remains terminal;
completed version-1 PDF outputs are marked failed and removed from the job's advertised
outputs so a pre-fix book cannot be replayed as valid.
