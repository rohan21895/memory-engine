# Benchmark suites

The content behind the gate. `../gates/` holds hand-written gate files for the
comparator itself; **this directory holds benchmarks that are measured**.

```
deterministic-properties.suite.json   runs in CI, 7 cases, needs nothing
synthetic-library.suite.json          needs the 216-file generated library
model-registry-graphs.suite.json      needs fetched ONNX weights
libraries/synthetic-demo.library.json what "the library" means, digested
inputs/synthetic-demo-phash.json      pHashes from a real workers/ingest run
```

## Running them

```sh
cd packages/eval-harness

# what CI runs: measure every CI suite, name the others
python3 -m memory_engine_eval.runner run --ci benchmarks/*.suite.json

# the library-backed suite, locally
python3 -m memory_engine_eval.runner run \
    --library DIR \
    --library-declaration benchmarks/libraries/synthetic-demo.library.json \
    benchmarks/synthetic-library.suite.json

# the ONNX suite, locally
python3 -m memory_engine_eval.runner run \
    --weights ../../models/weights \
    benchmarks/model-registry-graphs.suite.json
```

Exit codes are the harness's three, unchanged: `0` pass, `1` a measured
regression, `2` **nothing was measured** (a probe could not run, or the
comparison was refused), `3` a suite could not be set up. All of 1, 2 and 3 fail
CI. 2 is the one that matters most here: a missing library must never come back
green.

## Adding a case

1. Write a probe in `memory_engine_eval/probes.py`. It returns a number in
   `[0,1]`, declares the source files whose bytes are its identity, and
   implements at least one deliberate break.
2. Add the case to a suite. It must declare `claim_class`, `measures`,
   `does_not_measure`, and a bound for **every** break its probe implements —
   the loader refuses a subset, because an unbounded break is the break nobody
   runs.
3. `python3 -m memory_engine_eval.runner record --by "your name" SUITE` and
   commit the diff.
4. `python3 -m unittest discover -s tests` — `test_falsification.py` will run
   every break you declared and fail if any of them leaves the score where it
   was.

## What none of this measures

Quality. There are no photographs here. `claim_class: quality` will not load
against any library in this repository, by construction, and
`docs/benchmark-libraries.md` is the checklist for the day that changes.
