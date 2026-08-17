# ML runtime host

Python host for the generated `memory_engine.ml_runtime.v1.MlRuntime` gRPC service. The server
binds only to `127.0.0.1`; there is no option that can expose it on a routable interface.

The v1 host implements registry-backed discovery, explicit model loading, and batched inference.
Every model listing is
evaluated by `models.policy.load_gate.decide_load`; the worker does not carry a second policy
implementation. Config pins are verified over the exact file bytes, and weight pins are verified
over streamed file bytes. Missing weights, invalid or edited configs, unavailable providers, and
licensing restrictions are therefore visible through the contract rather than disappearing from
the list.

`LoadModel` creates a cached ONNX Runtime session on the requested CoreML, CUDA, DirectML, or CPU
provider. `Infer` batches up to the model config's `max_batch`, resolves `proxy_id` only through
media-db's `resolve_proxy` API, verifies the proxy's BLAKE3 before decoding, and returns one typed
outcome per item. Face detectors are decoded and NMS'd in the host. YuNet uses its centre/size
decoder, geometric-mean score and integer-rectangle NMS; SCRFD derives its anchor multiplicity from
the output count. Boxes leave the host normalised against the oriented proxy image.

Run from the repository root:

```sh
python3 -m pip install -e workers/ml-runtime
python3 -m memory_engine_ml_runtime --database /path/to/library.db --port 50051
```

Weights default to `models/weights/<weights.filename>` and may be redirected with
`--weights-dir`. Development mode is enabled only through the registry's named opt-in environment
variable; the host delegates that decision to the policy module too.

Omit `--database` only for tensor-only callers. A `proxy_id` request against a host without a
database fails with `PROXY_NOT_FOUND`; it never falls back to a media path or accepts a caller path.

## Real-photo smoke path

Run the local spine against a folder rather than another synthetic fixture:

```sh
memory-engine-photo-smoke /path/to/photos \
  --weights-dir /path/to/development/weights \
  --runtime coreml
```

The command creates a resumable scan JobSpec from the golden fixture, runs the Rust ingest worker,
writes its MediaRecords and proxy references through media-db's public API, starts the loopback
runtime, and attempts every step declared by the registry's `photo_analysis` pipeline. Successful
SigLIP vectors are written through media-db's vector API and referenced from updated, schema-
validated MediaRecords. Detector results feed aligned ArcFace requests in memory; tensor reports
include shape and dtype rather than dumping thousands of float values. The JSON report includes
every detection, every step result, persistence counts, and every blocked stage. Work is content-
addressed under the system temporary directory by default; pass `--work-dir` for a persistent
location. The command exits `2` for a partial run so missing work cannot be mistaken for a pass.

The model list reports `registry_loadable` rather than claiming the ONNX graph is loadable before
a provider creates a session. Session creation separately verifies every configured input and
output name and reports a typed `CONFIG_MISMATCH` when real weights disagree with registry metadata.

This is deliberately a development command. It enables the registry's named development gate but
does not download weights or make network requests. It is also deliberately honest about two
contract gaps: issue #42 must provide a golden `analyze_image` JobSpec and an executable definition
of `classical_quality` before the analysis pass can claim resumability or completion, and issue #34
must freeze the canonical `face_id` encoding before detections or face vectors can be written as
FaceRecords. The registry pipeline also has no selected sensitive-content gate, so its output must
not feed unattended albums or sharing.
