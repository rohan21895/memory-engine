# Benchmark libraries: what has to exist before a quality claim is possible

CLAUDE.md hard rule 7 says model swaps are gated by the eval harness. The gate
runs (`packages/eval-harness`, three exit codes, wired into CI) and, as of this
document, it has content: seven determinism cases in CI plus two that need
something CI does not have. **None of them is a quality measurement, and the
loader will not let one claim to be.**

This document is the checklist that turns "get a real benchmark library" from a
research project into shopping. It says exactly what must exist, in what shape,
before any number in this repository is allowed to say a model is *good*.

---

## Why nothing here can measure quality today

There are no photographs in this repository, and the user's own photographs are
not a test fixture. What exists is:

| What | What it can support |
|---|---|
| `scripts/demo/make_library.py` — 216 procedurally drawn files | determinism, plumbing |
| Real ONNX checkpoints (YuNet, SCRFD, ArcFace, SigLIP 2), fetched not committed | plumbing: does the config describe the graph |
| The deterministic packages themselves | determinism |

The faces in the synthetic library are cartoons: a skin-coloured oval, two eye
ellipses, a mouth arc. **A detector that finds all of them may find no real
faces, and a detector that finds none of them may be excellent on real ones.**
The same applies less obviously to everything else: synthetic stills have clean
exposure, no motion blur, no chroma noise, no lens character and no JPEG
generation loss, so any threshold tuned against them will not transfer.

That is stated in prose at the top of `make_library.py`, and prose is obeyed
until somebody is quoting a figure into a model card at midnight. So it is also
structural:

* `benchmarks/libraries/*.library.json` carries a `claim_ceiling`.
  `synthetic-demo` declares `plumbing`.
* A library whose `provenance` is `synthetic_generated` **cannot declare a
  ceiling of `quality`** — `library.py` refuses the declaration itself, so the
  ceiling cannot be raised by editing one field.
* A case declaring `claim_class: quality` against that library does not load.
  The suite does not compile, the number is never produced, so it cannot be
  quoted.

Three claim classes, and the difference between them is the whole point:

| Class | Means | Measurable on synthetic data? |
|---|---|---|
| `plumbing` | the code path ran and returned the shape it promised | yes |
| `determinism` | the same input produced the same output | yes — it is a property of the code |
| `quality` | the output is good, by a measure a human would accept | **no** |

---

## What is measured today

`python3 -m memory_engine_eval.runner run --ci packages/eval-harness/benchmarks/*.suite.json`

**`deterministic-properties`** (runs in CI, 7 cases, no inputs beyond the repo):

| Case | Measures |
|---|---|
| `dedupe_bursts_recovered_exactly` | every declared burst comes back as exactly that set — no member lost, no stranger added |
| `dedupe_bursts_stay_pure` | no photo outside a burst is merged into it |
| `dedupe_ids_stable_under_permutation` | group ids, primaries and membership survive any input order |
| `print_hard_gates_fire` | each of the five hard print gates fires on a layout built to violate exactly it |
| `print_clean_layout_passes` | the negative control — a clean layout still passes |
| `print_report_is_byte_identical` | shuffled pages produce a byte-identical validation report |
| `reel_edl_is_byte_identical` | shuffled moments and media produce a byte-identical EDL |

**`synthetic-library`** (needs the generated library on disk): every file's
BLAKE3 recomputed off the disk against the manifest — the `media_id` ingest
derives.

**`model-registry-graphs`** (needs fetched weights + onnxruntime): every claim
the registry configs make about their ONNX graphs — input tensor name, pinned
spatial size, every declared output name — checked against the graph on disk.
Issue #36 was SCRFD with the wrong output names, which produced a detector that
merely seemed mediocre.

Every one of these was **run against a deliberately broken input before it was
kept**. `tests/test_falsification.py` re-runs every break in CI. The two suites
CI cannot run were falsified by hand on the machine that recorded their
baselines:

| Case | Break | Passing | Broken |
|---|---|---|---|
| `library_media_ids_recomputed` | one declared digest edited | 1.0 | 0.99537 (215/216) |
| `library_media_ids_recomputed` | two declared digests swapped | 1.0 | 0.99074 (214/216) |
| `registry_configs_describe_their_checkpoints` | claim an input tensor the graph lacks | 1.0 | 0.86667 |
| `registry_configs_describe_their_checkpoints` | rename every declared output | 1.0 | 0.23333 |

(30 claims across 4 real checkpoints: arcface-buffalo-l, scrfd-10g-bnkps,
siglip2-so400m-384, yunet-2023mar.)

---

## The checklist: what a real benchmark library must contain

Six libraries, one per category the build plan names (§6): **Indian weddings,
festivals, GoPro/adventure, drone, baby/family, travel**. The harness already
declares those six (`harness.BENCHMARK_CATEGORIES`) and gates each on its own,
so a model that gains on travel while collapsing on low-light family shots
fails — the mean cannot pass a run.

### 1. Consent, before anything else

A benchmark library is a folder of other people's families. `library.py`
requires a `consent` block on any `consented_real` library and refuses the
declaration without one:

- [ ] `consent_ledger_ref` — the entry in the consent ledger (`services/api`)
      that authorises this use. Not an email, not a verbal yes: the ledger
      record, because the ledger is what an audit reads.
- [ ] `subjects_consented` — how many people appear and consented.
- [ ] `minors_present` — true/false, honestly.
- [ ] `minor_consent_ref` — **required when `minors_present`**. Child-face
      labelling sits behind separate explicit consent (build plan §8), and the
      declaration will not load without it.
- [ ] A stated retention and deletion path. A benchmark library is the one
      corpus that outlives every job that touched it.

### 2. Enough material, of the right shape

Per category:

- [ ] **≥ 2,000 images and ≥ 2 hours of video**, from **≥ 20 distinct
      libraries** (different people, cameras, phones, years). Twenty thousand
      photos from one photographer measures one photographer.
- [ ] **≥ 8 identities with ≥ 30 faces each**, including at least **3 sibling
      or parent-child pairs**. Lookalikes are the case that breaks face
      grouping in real family libraries; a suite of well-separated strangers
      reports 1.000 forever.
- [ ] **Hard negatives, deliberately collected**: dim handheld indoor shots,
      long exposures, shallow depth of field, backlight, mixed colour
      temperature, heavy compression, screenshots, documents, and photos of
      screens. `docs/sharpness-floor-decision.md` records why: the synthetic
      library has no blurred variants at all, so the sharpness floor is
      currently `None` because there is nothing honest to calibrate it against.
- [ ] **Near-duplicate bursts with labelled boundaries** — which frames are one
      burst and which are the next scene. Without labels, dedupe's decisive
      threshold has no ground truth, and it is the parameter that silently
      deletes photos.
- [ ] **Real container diversity**: HEIC/HEIF, RAW, Live Photos, chaptered
      GoPro MP4s, WhatsApp re-encodes, Google Takeout and iCloud export layouts.
- [ ] **Real audio**: speech, music, laughter, wind. The synthetic clips carry
      sine tones, so no audio-event or transcript number exists yet.

### 3. Ground truth, recorded separately from the media

A benchmark without labels is a pile of photographs.

- [ ] **Face identity**: every face box assigned to a person id, by a human,
      with the uncertain ones marked uncertain rather than guessed. This is what
      the ≥99% precision gate is written in terms of, and
      `packages/face-identity` refuses the automated path without a calibrator
      fitted on exactly this.
- [ ] **Keep/reject decisions** on near-duplicate groups, by the person whose
      library it is. This is the only ground truth that exists for "which frame
      mattered".
- [ ] **Burst membership**, as above.
- [ ] **Exclusion labels**: screenshots, documents, sensitive content — the
      things that must never reach an automated output.
- [ ] **Moment boundaries** on video, with at least the emotional peaks marked.
      Feature vectors cannot tell "kid sees the ocean for the first time" from
      "kid standing near ocean"; a human can, once, and then it is a label.
- [ ] Labels stored **outside the media**, keyed by `media_id` (BLAKE3), so the
      library can be re-hashed and the labels still join.

### 4. Declared the way the loader requires

- [ ] `MANIFEST.json` at the library root, listing every file with its
      `relpath`, `blake3` and `byte_size`. `library.resolve` **re-hashes every
      file off the disk** — a manifest agreeing with itself proves nothing.
- [ ] A committed `benchmarks/libraries/<id>.library.json` carrying
      `file_count` and `inventory_digest`, generated by
      `python3 -m memory_engine_eval.runner declare-library DIR` and never
      typed by hand.
- [ ] `provenance: consented_real`, `claim_ceiling: quality`.
- [ ] A `library_version` that is bumped whenever a single file changes. The
      inventory digest is deliberately fragile: a re-encode, a touched file or a
      partial copy all change it, and the suite then **refuses** rather than
      comparing against a library it was not measured on.

### 5. Held where it can be used but not leaked

- [ ] Not in git. It is gigabytes of other people's faces.
- [ ] Reachable by path on the machines that gate model swaps, with the
      declaration committed so a stale copy cannot pass.
- [ ] Never uploaded anywhere without a consent-ledger entry, including to a
      frontier model. The Tier 3 boundary (`packages/prompt-engine`) applies to
      benchmark media exactly as it applies to a customer's.

---

## What becomes possible once it exists

Each of these is currently impossible and becomes a case the moment the
corresponding ground truth lands:

| New case | Needs | Replaces today's |
|---|---|---|
| face identity precision at the automated threshold | labelled identities, real embeddings | nothing — the number does not exist |
| dedupe false-merge rate | labelled burst boundaries on real photos | `dedupe_bursts_stay_pure` (determinism only) |
| sharpness floor calibration | hard negatives that must survive | `DEFAULT_SHARPNESS_FLOOR = None` (issue #22) |
| face detection recall / precision | real faces | `registry_configs_describe_their_checkpoints` (plumbing only) |
| aesthetic and IQA correlation with human keeps | keep/reject PrefEvents | nothing |
| reel blind A/B (≥40% indistinguishable from a human cut) | real footage plus human editors | nothing |
| beat alignment error < 50ms | licensed music and real downbeats | nothing — no music is bundled |

Until then, the honest sentence is the one the case files already carry:
`[DETERMINISM] ... DOES NOT MEASURE: ...`.

---

## Practical notes

Regenerate the committed pHash input from a real ingest run (never by hand — the
hashes are the Rust `phash::dct_64` output, and a value typed in would pin bits
no shipped code produces):

```sh
python3 scripts/demo/make_library.py --out /tmp/bench-library
cargo build --release --manifest-path workers/ingest/Cargo.toml
# run the ingest worker over it, then:
cd packages/eval-harness
python3 -m memory_engine_eval.record_inputs \
    --records /tmp/work/records/records \
    --library /tmp/bench-library \
    --out benchmarks/inputs/synthetic-demo-phash.json
```

Move a baseline deliberately, as a reviewed diff, never as a side effect of
running the gate:

```sh
cd packages/eval-harness
python3 -m memory_engine_eval.runner record --by "your name" benchmarks/NAME.suite.json
git diff benchmarks/NAME.suite.json     # this diff is the review
```
