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
- Durable checkpoint JSON after each file. `execute_scan_batch` supports cooperative yield;
  process death follows the same cursor-based resume path.
- Corrupt, zero-byte, and unsupported inputs are retained as quarantined records. Known
  video/audio containers are hashed and left pending for the video-proxy phase.
- No network dependency or network path. A `JobSpec` declaring egress is rejected.

JPEG, PNG, GIF, BMP, WebP, and TIFF are decoded in v1. HEIC/HEIF/AVIF are identified and
quarantined as `unsupported_codec` until the platform decoder adapter lands; they are never
misclassified from a misleading filename extension.

## CLI

```text
memory-engine-ingest <job-spec.json> <output-dir> <checkpoint.json>
```

The source roots in the job must exist and their `source_locator_digest` must match the
canonical, NFC-normalized, sorted roots joined by a NUL byte and hashed with BLAKE3.
