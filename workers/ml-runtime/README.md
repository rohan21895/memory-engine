# ML runtime host

Python host for the generated `memory_engine.ml_runtime.v1.MlRuntime` gRPC service. The server
binds only to `127.0.0.1`; there is no option that can expose it on a routable interface.

The v1 skeleton implements registry-backed `ListModels` and `Health`. Every model listing is
evaluated by `models.policy.load_gate.decide_load`; the worker does not carry a second policy
implementation. Config pins are verified over the exact file bytes, and weight pins are verified
over streamed file bytes. Missing weights, invalid or edited configs, unavailable providers, and
licensing restrictions are therefore visible through the contract rather than disappearing from
the list.

`LoadModel` runs the same gate and returns a typed refusal for unloadable models. Actual model
loading and both inference RPCs remain explicitly `UNIMPLEMENTED` until the ONNX execution host
lands. `UnloadModel` safely reports that nothing was loaded.

Run from the repository root:

```sh
python3 -m pip install -e workers/ml-runtime
python3 -m memory_engine_ml_runtime --port 50051
```

Weights default to `models/weights/<weights.filename>` and may be redirected with
`--weights-dir`. Development mode is enabled only through the registry's named opt-in environment
variable; the host delegates that decision to the policy module too.
