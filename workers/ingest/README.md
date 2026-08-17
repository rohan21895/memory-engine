# Ingest worker

Rust worker for local, content-addressed media discovery. It consumes the generated
`JobSpec` and emits generated `MediaRecord` values; no contract type is duplicated here.

## v1 behavior

- Deterministic directory walking with hidden-file, depth, and symlink policies.
- BLAKE3 identity, metadata extraction, EXIF orientation, `phash-dct-64`, and 512 px JPEG
  thumbnails in one source-file read for supported still images.
- Filename-derived dates for common WhatsApp exports; unknown dates remain explicitly
  unknown rather than falling back to filesystem time.
- Content sniffing rather than extension trust, including HEIC/HEIF/AVIF ISO-BMFF brands.
- GoPro chapter recognition for both modern `GH01`/`GX01` and legacy `GOPR` + `GP01`
  conventions. Once a scan closes, ordered physical members are rewritten with final span
  membership and a content-addressed virtual assembly is emitted. Incomplete or ambiguous sets
  never masquerade as verified gapless footage. Completed constant-rate proxy indexes refresh
  member offsets without another source read.
- Durable checkpoint JSON after each file. `execute_scan_batch` supports cooperative yield;
  process death follows the same cursor-based resume path.
- Corrupt, zero-byte, and unknown-format inputs are retained as permanent quarantines. Missing
  decoder capability is a retryable processing failure, not a corrupt-file quarantine. Known
  video/audio containers are hashed and left pending for the video-proxy phase.
- No network dependency or network path. A `JobSpec` declaring egress is rejected.

JPEG, PNG, GIF, BMP, WebP, and TIFF are decoded on every platform. On macOS 13+, HEIC, HEIF,
and AVIF are decoded in memory through ImageIO/CoreGraphics, from the same bytes used for BLAKE3;
they are never misclassified from a misleading filename extension. Platforms without a decoder
write `failed` records with a `missing_capability_*` error and `retryable: true`. Each resumed or
completed scan checks those records again when the capability becomes available. The retry pass
also recognizes legacy HEIC-family records quarantined as `unsupported_codec`, so existing
libraries self-heal after an application update. A file that reaches an available decoder and
cannot decode is instead quarantined as `file_corrupt` with `retryable: false`.

Each `execute_scan_batch` invocation runs the capability retry first, then the scan, then GoPro
span reconciliation — in that order, and reconciliation only once no retry is still outstanding.
Reconciliation *derives* span state from the persisted records, so it must observe the final
state of every record in the batch; a retry rewrites a record from scratch, and running it
afterwards would leave a just-published assembly describing a record that no longer exists. If
the batch budget defers a retry, the completed job reopens as `pending` and the spans are left
alone until the library is repaired. Independently of ordering, re-persisting a record merges the
on-disk `span` back in, so a from-scratch re-ingest can never erase span membership.

`generate_video_proxy` jobs use VideoToolbox on macOS and NVDEC/NVENC or QSV on Windows.
Decode, 480p scaling, and H.264 encode must all be exposed by FFmpeg for the selected backend;
missing hardware capability fails the job instead of falling back to software. The same FFmpeg
pass emits an NDJSON frame-index sidecar from decoded timestamps. Completed media inputs are
checkpointed individually, so a resumed GoPro chapter set never re-encodes finished chapters.

Set `params.hardware_decode` to `videotoolbox` on macOS, or to `nvdec` or `qsv` on Windows.

## CLI

```text
memory-engine-ingest <job-spec.json> <output-dir> <checkpoint.json>
```

Set `MEMORY_ENGINE_FFMPEG` when FFmpeg is not on `PATH`.

The source roots in the job must exist and their `source_locator_digest` must match the
canonical, NFC-normalized, sorted roots joined by a NUL byte and hashed with BLAKE3.
