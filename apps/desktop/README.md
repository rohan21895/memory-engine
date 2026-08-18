# Photeo desktop

Tauri 2 + React shell for the private local library. The first-run flow uses the native
folder picker and runs the resumable Rust ingest worker in bounded batches. Library, search,
people, review-queue, scan-stat, and pixel reads go through the generated Rust client for
`contracts/proto/media_query.proto`; the shell never reads media-db tables.

## Run

```bash
npm install
npm run --workspace @memory-engine/desktop tauri dev
```

The media-db host passes its address as `MEMORY_ENGINE_MEDIA_QUERY_ENDPOINT`. The desktop
accepts only literal IPv4 or IPv6 loopback HTTP endpoints. Pixel responses are proxy-only,
BLAKE3-verified, and cached by content digest under the operating system's app-local data
directory. Original paths and bytes never enter the webview.

Opaque service cursors are preserved end to end, and the library and Best moments grids
virtualize rows so a 100k-item library has bounded mounted-DOM work. Best moments asks the
service for quality-descending visual media with rejected and sensitive items excluded. Its
working selection is session-local: photo/video counts, known video duration, unknown video
lengths, and quality comparability are reported separately instead of inventing a usable-time
claim. Closing or pausing during a scan is safe; selecting the same source later reuses its
durable checkpoint.

The query contract is intentionally read-only. The media-db-owned host still needs to import
the Rust ingest worker's content-addressed MediaRecord inbox, and person-label submission needs
a separate consent-aware write contract. The UI exposes neither a direct-SQL fallback nor an
unratified mutation.
