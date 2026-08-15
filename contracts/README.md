# contracts/

The frozen data contract between the two agents. Claude drafts, Codex co-signs; neither side changes it unilaterally.

Everything that crosses the boundary between *deciding* and *shipping* is described here and nowhere else. If a worker needs a shape that is not in this directory, that is a contract gap to raise — not a type to hand-write locally.

## Layout

```
contracts/
  schemas/     JSON Schema draft 2020-12. The source of truth.
  codegen/     Generator + committed bindings for Python, TypeScript, Rust.
  fixtures/    Golden test data, with index.json declaring what each one proves.
  tests/       Golden tests both agents run.
```

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

**Nothing here carries pixel data.** Images are referenced by proxy id, embeddings by index key.

**Constraints that JSON Schema can express, it does.** The precision-first face gate, the egress-requires-consent rule, the no-pass-with-errors rule and the pixel-data prohibition are all `if`/`then` blocks or `const`s, so a bad record fails validation rather than relying on a code path being reached. Constraints it cannot express — comparing two sibling values — live in `tests/test_contracts.py` and are exercised against the fixtures.

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
