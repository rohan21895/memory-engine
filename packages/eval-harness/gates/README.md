# Gate files

Every `*.gate.json` in this directory is executed by CI:

```
cd packages/eval-harness
python3 -m memory_engine_eval.harness gates/*.gate.json
```

That command is what makes CLAUDE.md hard rule 7 ("model swaps are gated by the
eval harness in CI") a thing that runs rather than a thing that is written down.
Before it existed, nothing in the repository imported `memory_engine_eval` except
its own test file.

## What the exit codes mean

| Code | Meaning | What to do |
|---|---|---|
| 0 | Pass — the comparison ran and nothing failed. | Nothing. |
| 1 | Fail — the comparison ran and something failed. | A quality signal: fix the model, move the baseline deliberately, or write a waiver for one case. |
| 2 | Refused — **there is no comparison**. Mismatched digests, mixed model sets, unpinned weights, different inputs. | Fix the pins/inputs and re-run. This is *not* a quality signal and cannot be waived. |
| 3 | Unusable — the gate could not be set up: malformed or unreadable gate file. | Fix the file. Nothing was measured. |

1, 2 and 3 all fail CI. They are separate numbers because "I could not check"
and "I checked and it got worse" call for different actions from different
people — the same argument `models/policy/digest.py:222-239` already makes at
the other end of the model pipeline.

## Adding a real gate

A gate file is one JSON object:

```jsonc
{
  "as_of": "2026-08-17",            // required; no --as-of flag exists, because
                                    // silently moving the clock is how an expired
                                    // waiver keeps working
  "policy": { ... },                // optional; absent means the strict defaults
  "suite":  { "cases": [ ... ] },   // the declared benchmark
  "baseline": [ ... ],              // CaseResults from the pinned baseline run
  "candidate": [ ... ],             // CaseResults from the candidate run
  "waivers": [ ... ]                // optional, one case each
}
```

Unknown fields are refused at every level, on purpose: a misspelled knob would
take its default silently, and the policy in the repository would not be the
policy that ran.

## `selection-critical-errors.gate.json`

The first selection-quality gate. `memory_engine_eval/selection_bench.py`
constructs synthetic candidate pools (BLAKE3-style hex ids, one-hot
embeddings -- nothing from any real library), runs album-engine's selection
v3 on them, and measures each CRITICAL ERROR as a rate: a blink frame chosen
over its clean group-mate, a selected pair above 0.92 cosine, a screenshot
selected, an isolated rare moment falsely rejected on a soft floor, a
Pareto-dominated frame chosen over its dominator, a pin absent, an exclude
present. Every error case is `expected: 0` with `enforce_expected`, so ANY
nonzero rate is exit 1 with no baseline movement involved. Two retention
cases (`expected: 1`) guard the positive direction. Baseline and candidate
are identical by construction; the teeth are the expected values plus
`tests/test_selection_gate.py`, which re-runs the builder and refuses a
committed file the code has moved from (and proves each metric bites).
Regenerate after a deliberate behaviour change:

```
python3 -m memory_engine_eval.selection_bench --as-of <date> \
    --write gates/selection-critical-errors.gate.json
```

`gates/local/` (gitignored) holds the real-library sidecar checker; committed
gates carry synthetic data only.

## `no-op-selfcheck.gate.json`

Not a model measurement. Its digests are synthetic and its samples are fixed
constants; it exists so CI executes the gate end to end on every push, and so a
change that breaks the command is caught by the command. Candidate and baseline
pin identical digests, which makes it a no-op run: every delta must be exactly
zero, and `min_repeats: 3` means the numbers also have to repeat.
