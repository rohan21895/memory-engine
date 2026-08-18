# contracts/

The frozen data contract between the two agents. Claude drafts, Codex co-signs; neither side changes it unilaterally.

Everything that crosses the boundary between *deciding* and *shipping* is described here and nowhere else. If a worker needs a shape that is not in this directory, that is a contract gap to raise — not a type to hand-write locally.

## Layout

```
contracts/
  schemas/           JSON Schema draft 2020-12. The source of truth.
  codegen/           Generator + committed bindings for Python, TypeScript, Rust.
  fixtures/          Golden test data, with index.json declaring what each one proves.
  vectors/           Golden input -> id tables for the content-addressed identities.
  tests/             Golden tests both agents run (Python).
  tests-typescript/  The same identities, recomputed in TypeScript.
```

`vectors/` is separate from `fixtures/` on purpose: a fixture is an instance of
a schema and `fixtures/index.json` is required to account for every file under
`fixtures/`, while a vector is an *input tuple* and the id it must produce. Each
vector carries the exact UTF-8 pre-image as hex alongside the digest, because a
digest mismatch says only that something diverged and a pre-image mismatch names
the field that did.

`tests-typescript/` is what makes "language-independent" a measured claim rather
than an intention. It reads the same vectors and the same fixtures and
recomputes the same ids against the generated TypeScript bindings. One
implementation can only show a contract is self-consistent; the failure this
guards against — Python writing `1.0` where JavaScript writes `1` — is invisible
to any single language.

## The seven schemas

| Schema | What it is |
|---|---|
| `MediaRecord` | Identity of one file plus everything analysis has learned about it |
| `FaceRecord` | One detected face; identity is split from permission to act on it |
| `MomentRecord` | A scored interval in a video, with certified cut points |
| `EDL` | The deterministic edit plan; losslessly exportable to OpenTimelineIO |
| `AlbumSpec` | The deterministic print plan, in millimetres, with a hard validation gate |
| `JobSpec` | Any unit of work: idempotent, resumable, egress-declared |
| `PrefEvent` | One human reaction with the feature context at decision time |

`common.schema.json` holds the shared `$defs` every schema references. It declares no root type.

## Design rules

**Every object sets `additionalProperties: false`.** An undeclared field is an error on both sides, never a silently ignored one. This is the guard that stops either agent quietly extending a record instead of changing the contract.

**All time is `RationalTime`, never float seconds.** `value` units at `rate` per second, field-for-field identical to `otio.opentime.RationalTime`. `30000/1001` has no exact float representation, and a drifting frame is a missed beat. See [docs/otio-mapping.md](../docs/otio-mapping.md).

**Identity is content-addressed where the bytes determine it.** BLAKE3 for media, faces, moments, jobs and plans; UUID only where a human or a clustering run created the entity. This is what makes every job idempotent and every plan portable between machines.

Two places where "the bytes" is not the whole story, both handled explicitly rather than by convention:

- **`MediaRecord.asset_kind`** distinguishes a `physical_file` (identity is the BLAKE3 of its bytes; at least one source; its own proxies) from a `virtual_assembly` (a GoPro chapter set or DSLR split; identity is the `span_id` over its ordered members; `byte_size` 0; no sources and no proxies, because the members own both). Conditional validation enforces the difference. An assembly carrying the *sum* of its members' sizes would be a number matching no file on disk, which breaks anything verifying a record against the filesystem.
- **`JobSpec.inputs.source_locator_digest`** exists because `scan_source` runs *before* any content hash exists. Without it, two scans of different drives with the same parameters share a `job_id`, and the second is skipped as already-done — a whole drive silently never imported.

**A content address is a byte string, and the byte string is written down.** Naming the tuple is not enough: `span_id` (issue #26), `edl_id` and `face_id` (issue #34) each had a description that named the inputs and left the serialisation to whoever implemented it, and each ended up with a golden fixture that matched no implementation because it had been typed rather than computed. Every one of them now states its exact bytes — separators, field order, number formatting, rounding rule — in the schema, and `contracts/tests` recomputes it. Two rules are load-bearing across all three, and both exist because they were broken:

- **Numbers render in RFC 8785 / ECMAScript `Number::toString` form.** Python's `repr` writes `1.0` where JavaScript writes `1`. That difference alone once made every model report a config-digest mismatch.
- **Rounding is stated, never delegated to a builtin.** Python's `round` rounds half to even; JavaScript's `Math.round` and Rust's `f64::round` round half away from zero. `face_id` quantises a box to 1e-4 of the frame, and 8855 of the 10000 half-quantum positions in [0,1] are exactly representable — so "just call round" means one detection with two ids.

**Nothing here carries pixel data.** Images are referenced by proxy id, embeddings by index key.

**Constraints that JSON Schema can express, it does** — as `if`/`then` blocks or `const`s, so a bad record fails validation rather than relying on a code path being reached:

| Rule | Where |
|---|---|
| A sub-threshold face cannot claim `eligible_for_automated_output`, and an eligible one must name its person, confidence and threshold | `face-record` |
| A `confirmed_minor` cannot be named without a live consent scoped `minor_face_labeling` | `face-record` |
| `requires_egress: true` demands a `ConsentRef`, a destination and a payload kind | `job-spec` |
| `scan_source` demands source paths and a `source_locator_digest` | `job-spec` |
| An `AlbumSpec` cannot claim `pass` with a non-zero `error_count`, **or** with any hard gate missing from its checks | `album-spec` |
| A virtual assembly cannot carry bytes, sources or proxies | `media-record` |
| A perceptual hash's `bits` must equal `4 * len(hex)` | `common` |
| `PrefEvent.pixel_data_present` is `const false`, and a shareable event must be anonymised | `pref-event` |

Constraints it cannot express — comparing two sibling values — live in `tests/test_contracts.py` and are exercised against the fixtures. Every negative fixture declares in `index.json` whether it is rejected by a type-level rule (the generated bindings catch it too) or a conditional one (only jsonschema and the semantic checks can).

## Regenerating the bindings

```bash
npm run codegen
```

Writes `contracts/codegen/generated/{python,typescript,rust}`. Output is byte-stable for a given set of schemas, and CI fails if what is committed differs from what the schemas produce:

```bash
npm run codegen:check
```

The generator is stdlib-only Python by design. The contract layer is the one thing both agents must be able to regenerate on any machine, in CI, offline, with no dependency resolution standing between a schema edit and a reviewable diff.

Never hand-write a type that exists in a schema. Import it from the generated bindings.

## Running the tests

```bash
python3 -m unittest discover -s contracts/tests -v
```

Also runs under `pytest contracts/tests`. Requires `pydantic>=2.7` and `jsonschema>=4.20`.

The suite checks four things, in order of what they would catch:

1. The schemas are valid draft 2020-12 and every `$ref` resolves.
2. Every fixture behaves exactly as `fixtures/index.json` declares. A `schema-invalid` fixture must be rejected **at the field the manifest names**, so it cannot pass for an accidental reason.
3. Cross-field invariants hold — and the tests mutate good fixtures to prove each invariant actually fires, rather than only passing on clean data.
4. The committed bindings are fresh, accept every fixture, and round-trip them without changing a field.

## Changing a schema

1. Edit the schema.
2. `npm run codegen` and commit the regenerated bindings.
3. Add or update fixtures, including the manifest entry saying what the fixture proves.
4. Run both sides' tests.
5. PR. A `contracts/` change needs the other agent's review — this is the one directory neither side self-merges.

Golden-fixture drift is the canary. If a fixture changes, both agents re-run.
