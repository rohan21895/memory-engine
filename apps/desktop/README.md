# Photeo desktop

Tauri 2 + React shell for the private local library. The first-run flow uses the native
folder picker, runs the resumable Rust ingest worker in bounded batches, and renders only
generated `MediaRecord` summaries and thumbnail proxies. Individual original-media paths are
never returned to the library UI.

## Run

```bash
npm install
npm run --workspace @memory-engine/desktop tauri dev
```

The library and checkpoints live under the operating system's app-local data directory.
Closing or pausing during a scan is safe; selecting the same source later reuses its durable
checkpoint.

The current library reader is a contract-file adapter so the product shell can run before a
cross-language media-db service exists. It is deliberately isolated behind the `library_page`
command and never reads media-db tables. Replace that adapter with the shared query service
before the 100k-item performance gate.
