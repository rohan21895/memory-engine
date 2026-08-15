# contracts: v0 schemas + codegen + fixtures

Phase 0 tasks 1–3 from CLAUDE.md. Seven schemas, three-language codegen, golden fixtures. **Nothing downstream has been started** — no `media-db`, no ranking, no story, no album work, per "do not start until the schemas are frozen and Codex has signed off."

This is a draft for review, not a merge-and-go. `contracts/` is the one directory neither agent self-merges.

---

## What's here

| Path | What |
|---|---|
| `contracts/schemas/` | 8 files: the seven contract schemas + `common.schema.json` for shared `$defs` |
| `contracts/codegen/` | stdlib-only generator + committed pydantic / TypeScript / Rust bindings |
| `contracts/fixtures/` | 23 fixtures + `index.json` declaring what each one proves |
| `contracts/tests/` | 30 golden tests, runnable under both `unittest` and `pytest` |
| `docs/otio-mapping.md` | OTIO exporter/importer algorithm and round-trip test plan |

124 `$defs` across the schemas; 268 generated models.

## The decisions worth arguing about

**All time is `RationalTime {value, rate}`, never float seconds.** Field-for-field identical to `otio.opentime.RationalTime`, so conversion is a constructor call rather than a computation. `30000/1001` has no exact float representation; a plan that stores `29.97` and reconstructs frame positions from it drifts, and a drifting frame is a missed beat. This is why `common.schema.json` exists at all.

**Identity is BLAKE3 content-addressing wherever the bytes determine it**, UUID only where a human or a clustering run created the entity. Every job is then idempotent by construction and every EDL is portable between machines — an EDL stores **no paths**, only hashes.

**Every object is `additionalProperties: false`.** An undeclared field is an error on both sides, never a silently ignored one.

**Constraints JSON Schema can express, it does — as `if`/`then`, not as convention:**

- a face below its threshold cannot claim `eligible_for_automated_output`
- an eligible face must name its `person_id`, `confidence` and `threshold_used`
- `requires_egress: true` requires a `ConsentRef`, a destination and a payload kind
- an `AlbumSpec` cannot report `status: "pass"` with a non-zero `error_count`
- `PrefEvent.pixel_data_present` is `const false`
- a `PrefEvent` may only be marked shareable if it names its consent, has been anonymised, and no longer holds local identifiers

Each of these has a fixture in `schema-invalid/` proving the rejection lands on the right field.

**One record per physical file, always.** A GoPro chapter set is N member records plus one virtual *assembly* record. Moments and EDL clips reference the assembly, so a cut can cross a chapter boundary without any planner knowing chapters exist.

**Capture time is a `TimeAssertion`, not a timestamp.** Source, precision and confidence travel with it. A file with no EXIF gets `precision: "unknown"` rather than a fabricated date — the signal that chronological output must exclude it rather than sort it to the epoch.

**Cut positions are part of the data, not the code.** `MomentRecord.snap_points` are certified cut positions with a reason and an asymmetric `cut_direction`; `safe_trim.speech_safe_*` come from word-level timestamps. The planner chooses among them rather than inventing positions, which is how "no mid-word cuts" and "beat error < 50ms" become testable properties of a *plan* rather than hopes about a renderer.

## OTIO mapping

Full field-by-field table lives in the `$comment` header of `edl.schema.json`; the algorithm and round-trip test plan are in [`docs/otio-mapping.md`](../docs/otio-mapping.md). Two rules:

1. **Structure maps onto OTIO natives** — Timeline, Track, Clip, Gap, Transition, ExternalReference, Marker, LinearTimeWarp, FreezeFrame.
2. **Everything else round-trips through one metadata namespace**, `metadata["memory_engine"]` — beat grid, story arc, reframe tracks, ducking, determinism, variant.

Conventions that keep the round trip exact:

- A hard cut is the **absence** of a `Transition`, matching OTIO. Never a zero-length one — some NLEs render that as a one-frame dissolve. There's a test asserting we never emit one.
- `target_url` is written at export time from a media-db lookup and is **not** authoritative on import; the BLAKE3 in metadata wins, so a timeline that came back from an editor on another machine still resolves.
- Two documented lossy edges, both deliberate: `timeline_range` (derived, recomputed) and generated markers (rebuilt from metadata, else they duplicate on every pass).
- `OtioExportInfo.unmapped_fields` must be empty for an export to claim losslessness — an exporter that meets an unmapped field records it rather than dropping it silently.

## Phase 0 edge cases

All four named in CLAUDE.md task 3 are present, and there's a test that fails if any goes missing:

- **GoPro chaptered span** — `video-gopro-chapter-01/02` + `video-gopro-span-assembly`. Exercises member index/offset, `member_media_ids` on the assembly only, exact 60000/1001 rate, frame-index sidecar, and analysis stages deliberately skipped on members with a stated `skip_reason`.
- **Photo with no EXIF date** — a WhatsApp forward with `metadata_present: []` and a wholly empty `TimeAssertion`.
- **Face below threshold** — 0.7418 against a 0.92 automated-output threshold: `person_id` still null, `eligible_for_automated_output` false, two ranked candidates, `review_reason: below_threshold`.
- **EDL with beat-locked cut + vertical reframe keyframes** — a 15s 9:16 reel at 128 BPM. All 8 cuts on downbeats, worst alignment error **7.5ms** against the 50ms gate. 8 reframe tracks producing exact 9:16 from 16:9 with SAM 2 subject lock, Savitzky-Golay smoothing and an explicit fallback. Also carries a licensed music cue, ambient with per-clip gain, an explicit-range ducking rule, an L-cut, a 0.5× speed effect on the peak, and a Tier 3 story arc with its `ConsentRef`.

## Verification actually run

- ✅ All 8 schemas validate as draft 2020-12; every cross-file `$ref` resolves.
- ✅ 30/30 golden tests pass under `python3 -m unittest discover -s contracts/tests`.
- ✅ Generated TypeScript compiles clean under `tsc --strict` (5.6.3).
- ✅ Generated pydantic imports warning-free; every valid fixture parses **and round-trips** without a field changing shape.
- ✅ Codegen is deterministic (identical hashes across runs) and `npm run codegen:check` reports fresh.
- ✅ `python3 -m compileall contracts` clean.
- ⚠️ **Rust is unverified locally — there is no cargo on the authoring machine.** The crate is emitted with `#![allow(clippy::all)]` and a `rustfmt.toml` setting `disable_all_formatting = true`, because CI runs `cargo fmt --check` on every crate it finds and generated code should be formatted by its generator, not by rustfmt. **This is the first thing to confirm on your side.** If it doesn't compile, that's a codegen bug and mine to fix.

Two round-trip properties the tests pin down, because they're the ones that would silently rot: every timeline position is a whole frame, while at least one beat position must be *fractional* — music doesn't land on frame boundaries, and a grid rounded to frames is the bug that makes beat-locked cuts drift.

The invariant tests also **mutate good fixtures to prove each check fires**, rather than only passing on clean data.

## Codex review checklist (AGENTS.md task 2)

Specific things I'd like pushed back on:

1. **GoPro chapter spans.** Is the member/assembly split right for what `workers/ingest` actually walks? The assembly has `byte_size` set to the sum and one `SourceLocation` pointing at the first chapter — that's the weakest part of this PR. Should it have zero sources instead?
2. **`Span.continuity`.** I assumed you can cheaply verify gaplessness at proxy time. If not, `unverified` becomes the norm and the renderer needs a different story for concatenation.
3. **WhatsApp / Takeout filename dates.** `TimeSource` has `filename_pattern` and `sidecar_json` — do those cover the conventions you're actually parsing?
4. **HEIC / Live Photos.** `paired_motion_media_id` on `ImageProperties` links the still to the motion track. Does that survive an iCloud export, where the pair may arrive as two unrelated files?
5. **`FrameIndexSidecar.mapping`.** I modelled `identity` vs `table` only. If your VFR handling needs more (per-GOP offsets, timestamp lists), say so now — it's cheaper to widen this than to work around it.
6. **`JobSpec.checkpoint.cursor` is an opaque worker-owned string.** The contract promises to persist and hand it back. Is that enough, or do you want structured resumption state?
7. **`EnhancementOp.license_cleared`** is a required boolean that blocks export when false. Confirm `workers/enhance` can honour that as a hard gate.
8. **Print validator.** `check_id` is a closed enum. If `render-print` needs a check that isn't in the list, it's a contract change — worth widening now.
9. **OTIO export ownership.** `docs/otio-mapping.md` assumes OTIO export is a user-facing feature (Phase 5, project editor) and *not* a step in the render path, so `render-video` stays free of an OTIO dependency. Confirm. It also assumes `media-db` exposes `resolve_path(media_id) -> Path | None` for the exporter to call.

## Notes

- `npm test` runs `cargo` for any `Cargo.toml` it finds, so it needs a Rust toolchain locally now. The Python contract tests are also wired as their own `Contracts` CI job, which needs no Rust.
- The repo had no commits when I started; `chore: repo baseline` commits your infrastructure scaffolding untouched and places the build plan at `docs/` and both agent instruction files at the repo root, so the paths CLAUDE.md and AGENTS.md reference resolve.
- Schemas are frozen at `schema_version: "v0"` (a `const`). A reader that doesn't recognise the value must refuse the record rather than guess.
