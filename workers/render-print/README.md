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

Pages are stored by content hash and checkpointed individually. A killed job resumes from
verified page artifacts, publishes the final PDF without overwriting different bytes, and
returns an existing completed job unchanged.
