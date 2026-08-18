# packages/safety-gate

The sensitive-content gate. Issue [#21](https://github.com/rohan21895/memory-engine/issues/21);
model chosen in `docs/safety-classifier-decision.md`.

## The one rule

> Absence is `indeterminate`, and indeterminate blocks.

## What runs today

Everything except the classifier itself.

The head is a 1152→3 linear map over the SigLIP 2 `so400m-384` image embedding.
**SigLIP 2 has no ONNX export in this registry (#79)**, so there is no embedding
to run it over, nothing has been fitted, and `models/configs/nsfw-siglip-head.json`
remains a placeholder with `blake3: null` and `blocks_commercial_release: true`.

`classifier_from_registry()` asks the real `models/policy/load_gate.py` whether
the entry may load. The answer is `UNLOADABLE_REASON_PLACEHOLDER`, refused in
**every** mode. That becomes `load_gate_denied` → `indeterminate` → every print,
every share and every contact sheet blocked, with a reason a human can act on.

So: **the gate is complete and everything is currently blocked.** That is the
designed state, and it is the opposite of the failure it exists to prevent — a
check that silently no-ops when its model is missing, so everything downstream
reads the absence as a pass.

## What does not exist, deliberately

- **No fabricated embedding.** `AbsentEmbedder` raises with a contract reason
  every time. There is no flag, no mode and no fallback that makes it return
  numbers, because a fabricated vector produces three scores in [0,1] that look
  exactly like a measurement.
- **No head artifact.** `textinit.build_head` takes a `TextEncoder` and nothing
  in this repository implements one. A function with a hole where the model goes
  is the honest shape of "waiting on #79"; a stub that returns something is not.
- **No measurement.** Nothing in `docs/safety-classifier-decision.md` §7's eval
  gate has been run. Every synthetic head in `tests/` is built by the test out of
  numbers the test wrote, and proves arithmetic, not accuracy.

## Layout

| Module | What it owns |
|---|---|
| `classes.py` | `CLASS_ORDER`, and the one function where a column index becomes a class name |
| `embedding.py` | The `ImageEmbedder` interface, and `AbsentEmbedder` — the absence that blocks |
| `head.py` | The 1152→3 linear map, its artifact format, and what it refuses |
| `textinit.py` | Zero-shot construction from the committed prompt bank; refuses without a text tower |
| `prompts.json` | The prompt bank — the closest thing this classifier has to training data, and reviewable |
| `calibration.py` | Per-class Platt scaling: six numbers that make the 0.3 threshold mean something |
| `classify.py` | Candidates → `ItemVerdict`s, and every way absence becomes `indeterminate` |
| `manifest.py` | Building a `SafetyClearance`, with the decision derived and never accepted |
| `canonical.py` | RFC 8785 bytes and `manifest_id` |
| `verify.py` | The verifier. Deny-by-default, and it converts its own faults into denials |
| `gate.py` | `guard_print` / `guard_share` / `guard_frontier_egress`, each with its sink welded shut |
| `registry.py` | Asks the real load gate whether the head may load |

## The three boundaries

| Sink | Enforced in |
|---|---|
| `print` | `workers/render-print/src/job.ts`, immediately before `writePdfOnce` |
| `share` | `services/share`, inside the only function that mints a `ShareAuthorization` |
| `frontier_egress` | `packages/prompt-engine`'s `FrontierTransport.send`, after consent and again immediately before the socket opens |

`local_export` has no gate on purpose: exporting to your own disk publishes
nothing on your behalf.

## The class-axis defect (decision doc §6.6)

`sensitive_logits` is shape `[-1, 3]` and nothing said which index was which
class. Transpose two columns and every breastfeeding photograph classifies as
`explicit`: scores stay in range, the threshold still fires, the manifest still
validates, and no test fails. Pinned in four places, each catching a different
edit — see the module docstring in `classes.py`.

## What needs sign-off before it is implemented

Decision doc §6.5 proposes that `medical_or_artistic` stop being a blocking
threshold and become a *disposition*: when it is high and a blocking class fired,
route to the review queue with an explanation rather than omitting the
photograph silently. That is a policy change — issue #21 fixed three classes and
a 0.3 threshold and did not say what the third class does. `classify.review_disposition`
computes the signal and is wired to nothing.
