# Tier 3 album taste — what leaves, what it cost, what it chose

The frontier taste layer, wired end to end and run against the live API for the
first time. This document records what was measured, not what was intended.

`packages/prompt-engine/album_taste.py` is the seam; `services/pipeline/stages/
taste.py` is the stage that drives it. The three modules it joins —
`contact_sheet.py`, `transport.py`, `structured.py` — each existed and passed
their own tests before this branch, and none of them had ever been called by
anything.

---

## The shape of the pass

```
album-engine selects  ──►  shortlist (its own ranked order)
                      ──►  contact sheet, 256px tiles, labelled A1..H8
                      ──►  ONE request: sheet + task text + JSON schema
                      ──►  reply validated against the labels that were drawn
                      ──►  labels resolved to media ids, locally
```

The frontier model never sees a media id, a filename, a date, a place, or a
score. Its entire vocabulary is the grid labels drawn on the tiles. The
manifest mapping label → media id never leaves the machine, and the egress test
asserts that by searching the request body for every id it could have leaked.

**The local engine narrows; the model curates.** Both selections are drawn from
the same pool, so the difference between them is taste rather than reach. That
is what makes the agreement number below mean anything.

---

## The inspection bundle is the deliverable

`--tier3-dry-run` composes the sheet, builds the exact request body, writes the
whole bundle, and stops **before** the consent check — so no consent is even
consulted, because nothing is going anywhere. It needs no API key.

```
outputs/taste/<digest>/
  contact-sheet.png            the picture, exactly as it would be uploaded
  contact-sheet-manifest.json  label -> media id (LOCAL; never sent)
  request-body.json            the exact bytes, base64 image included
  request-summary.json         digests, token-free summary, local-only maps
  consent.json                 the grant, or `consent: null`
  egress-ledger.jsonl          created empty, so "no entry" is visible
```

`request-body.json` is the payload **verbatim**, not a redaction of it. A bundle
that showed a tidied version would be evidence about a payload nobody sends.
`write_inspection_bundle` refuses to write at all if the PNG on disk is not
byte-identical to the image embedded in that body — the one assertion that
makes the rest of the bundle mean anything.

---

## The measured run

Synthetic demo library (`scripts/demo/make_library.py`), 66 files, 61 scored,
largest dated event 22 candidates after selection. Album target 8.

| | |
|---|---|
| Model | `claude-sonnet-5` |
| Sheet | 1648×1200, 22 tiles at 256px, 852,582 bytes |
| Request body | 1,139,928 bytes |
| Input tokens | 3,879 |
| Output tokens | 777 (run 1), 393 (run 2) |
| Cost | $0.0155 (run 1), $0.0117 (run 2) |
| Attempts | 1, no retries |
| Parse status | `ok` — zero rejections, both runs |
| Agreement with the classical selection | 2/8 (run 1), 3/8 (run 2) |

Two calls, $0.027 total. At the introductory Sonnet 5 rate ($2/$10 per MTok
through 2026-08-31); `PRICE_TABLE_USD_PER_MTOK` carries both rates so the figure
can be recomputed from the usage counters rather than believed.

### What it chose

Run 1, in the order it asked for them to be printed:

| # | Label | Score | Its reason |
|---|---|---|---|
| 1 | D3 | 0.72 | "Quiet opening: a lone figure facing the water at dusk" |
| 2 | A1 | 0.68 | "Establishes the group arriving under open sky" |
| 3 | B5 | 0.75 | "Three generations spaced across the field" |
| 4 | A4 | 0.80 | "The rainbow moment — an emotional high point" |
| 5 | B4 | 0.78 | "Parent and child close together, intimate human connection" |
| 6 | C2 | 0.65 | "Adds narrative movement mid-sequence" |
| 7 | C6 | 0.70 | "Close-up portrait grounds the story in one person" |
| 8 | D1 | 0.70 | "Closing image: two silhouettes at dusk walking off" |

> "Selected to form a story arc — from solitary anticipation, through gathering
> and a shared natural spectacle, to intimate family moments, and finally a
> quiet dusk departure."

### Is it better than the classical selection?

**Not answerable on this library, and saying otherwise would be the dishonest
part of this document.** The images are procedurally drawn figures on gradient
skies. There is no moment in them to detect, so a judgement about which one
"carries a moment" is a judgement about a drawing.

What *is* observable, and does not depend on the pictures being real:

- It returned a **sequence with a stated function per position** — opener,
  build, peak, intimate beat, closer. The fused score cannot express that; it
  produces a ranking, and a ranking is not an order.
- It **spread its picks across the sheet** (rows A–D) where the classical top-8
  clusters in the highest-scoring band. Diversity is something album-engine
  also enforces, but it enforces it as a constraint on a ranking; this arrived
  as the shape of the answer.
- It picked on **content it could only get from the pixels** — "two figures
  walking together", "close-up portrait" — which is the class of judgement the
  architecture put a frontier model in the loop for.

Two of eight picks agree with the local engine. That is a low number, and on a
real library it is the number worth arguing about.

---

## Three findings from the live runs

### 1. The selection is not reproducible, and that is now a stated property

Both calls sent **byte-identical requests** — same `cache_key`, same sheet
digest, same `request_id`. They returned **6 of 8 the same, in a different
order**, with different notes and a different token count.

This does not violate CLAUDE.md rule 3, and it is worth being precise about
why. Determinism is required of the *renderer*: same EDL + same sources =
identical output. The taste pass is not a renderer, it is a source of a plan,
and the plan is written to disk and content-addressed. The job is idempotent on
`inputs_digest`, so a re-run over an unchanged library reuses the stored
decision and never calls again. "Same library → same album" holds because the
decision is stored, not because the model is stable.

The consequence for the product is that **re-asking is a different question**,
and any future retry logic must reuse the stored decision rather than resample.

### 2. The model's order is not the chronological order

Checked against `captured_utc` for the run-1 picks:

| Position | Label | Captured |
|---|---|---|
| 1 (its "opening") | D3 | 02:45:30 |
| 2 | A1 | 01:57:12 |
| 3 | B5 | 01:58:13 |
| 4 | A4 | 02:44:00 |
| 8 (its "closing") | D1 | 15:11:14 |

Its closer is genuinely the latest frame. Its **opener is 48 minutes after its
second pick**. The sheet carries no timestamps by design, so the arc was built
from the light in the images — which is a reasonable thing to do and produces
an order the album's own chronology contradicts.

Nothing is broken today because `album` does not read this file. But it is the
concrete argument for the contract question below: **when the two disagree,
which order wins?** That has to be decided in `AlbumSpec`, not inferred by
whichever component reads the JSON first.

### 3. A refusal was reported as a missing dependency

Found by running the stage with a **revoked** consent record on an interpreter
without the `anthropic` package. The stage failed with:

> `TransportError: the anthropic SDK is not installed`

True, useless, and hiding the fact that the user had withdrawn permission.
Cause: `AnthropicSender()` is an *argument* to the call that checks consent, so
Python constructed the client first. Nothing was sent either way, so the ledger
looked perfectly clean — the damage was entirely in what the operator was told,
which is the surface "no silent anything" is about.

`AnthropicSender.__init__` is now inert and the client is built on first send.
The same run now reports:

> `egress refused before anything was sent: consent_revoked (consent was
> revoked; revocation is not a timeout)`

This also makes the module's own sentence true: no key is required to build a
request, only to send one.

---

## Every place the path refuses

Each was executed, not reasoned about. The unit tests kill a mutation that
removes the guard; the pipeline tests run the stage; the egress test runs the
transport with a counting sender and asserts zero calls.

| Refusal | Where | Proved by |
|---|---|---|
| Not requested | stage | default run reports "not requested"; no output |
| No API key | stage | temp repo root with no `.env` |
| No consent record on disk | stage | ran with a **real key** and the record removed |
| Malformed consent record | stage | fails, not skips — absent and broken differ |
| `requires_egress: false` | `check_egress` | `egress_not_declared`, sender never called |
| Consent missing / revoked / expired / wrong scope | `check_egress` | four distinct codes, sender never called |
| Ledger cannot write | `_record` | `ledger_refused` before the send |
| Proxy above 1024px | `contact_sheet` | refused **before decode**, asserted by instrumenting Pillow's decoder |
| EXIF orientation ≠ 1 | `contact_sheet` | refused rather than guessed |
| Sheet and request disagree about ids | `build_request` | `ValueError` before the wire |
| Reply names an id not on the sheet | `structured` | whole reply rejected — not dropped, not corrected |
| Reply echoes a different `request_id` | `structured` | rejected as a crossover |
| Wrong item count | `structured` | rejected, not truncated |
| Truncated reply | `transport` | `TRUNCATED`, never parsed as slop |
| A different model answered | `album_taste` | stage fails rather than record an unpinned decision |

---

## What is deliberately not wired

**`album` does not read the taste selection.** `AlbumSpec.SelectionReport` has
no field that can record "a cloud model chose these photographs", so a book laid
out from this selection would be a book that cannot say where its selection came
from. Wiring the two together needs a contract change and both agents; until
then the pass answers the question and the answer is on disk.

The same gap keeps the job from declaring an output: `JobOutput.kind` enumerates
contract artefacts and has no value for a Tier 3 decision, so the job completes
with no declared output rather than inventing an enum value.

**The library is synthetic and the embedder was a stand-in.** The pipeline ran
against the test fake model host, so the shortlist the model was shown came from
deterministic stand-in embeddings. What is proven here is the chain — selection
to sheet to wire to validated decision to media ids — and its refusals. What is
not proven is the taste of what went on the sheet.
