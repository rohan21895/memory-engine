# On-device model boundary

The general `getModel()` compatibility boundary retains a deterministic
JavaScript fallback. Production selection augments it with three guarded,
lazy, New-Architecture TFLite paths:

- `movenet.ts`: 17-point body pose for coverage diversity;
- `tinyclip.ts`: semantic image embeddings and offline zero-shot axes;
- `facenet.ts`: InsightFace buffalo_s `w600k_mbf` 512-d identity embeddings
  for person clustering.

Every path returns neutral or `undefined` on native, model, image, or tensor
failure, so `buildAlbum()` always retains deterministic fallback signals.
`probeModels()` (this directory's `index.ts`) loads all three graphs and reports
which ones are usable, because a silently broken model otherwise looks exactly
like a working one.

## Interpreter lifetime

`model-cache.ts` retires each interpreter every `RUNS_PER_MODEL` inferences.
fast-tflite v3 never returns the interpreter arena between runs
(mrousavy/react-native-fast-tflite#124), so a long batch climbs from ~200MB to
~1.2GB of native memory and the app is OOM-killed partway through a large
library. Retirement happens inside each wrapper's serialized inference queue, so
no interpreter is ever disposed while a run is in flight.

## Delegates

Every `loadTensorflowModel(...)` call passes an empty delegate list. Do not add
GPU delegates:

- fast-tflite 3.0.1 hardcodes the GPU delegate options with no serialization
  directory, so kernels are recompiled on every cold start - fatal for batch work;
- it also hardcodes `max_delegated_partitions=1`;
- its GPU path has an open batch-mismatch bug on ViT graphs (`tinyclip.ts`) and
  a reported PowerVR crash;
- NNAPI is deprecated on Android 15.

### The CPU path is single-threaded, and nothing here chose that

"An empty delegate list means XNNPACK on the CPU" is half of what happens, and
the missing half is the interesting one.

`HybridTfliteModule::createModel` (fast-tflite 3.0.1,
`node_modules/react-native-fast-tflite/cpp/HybridTfliteModule.cpp`) builds the
interpreter like this:

```cpp
TfLiteInterpreterOptions* options = TfLiteInterpreterOptionsCreate();
for (const TensorflowModelDelegate& d : delegates) { /* none */ }
TfLiteInterpreter* interpreter = TfLiteInterpreterCreate(model, options);
```

It never calls `TfLiteInterpreterOptionsSetNumThreads`. The C API leaves
`num_threads` at its sentinel, `InterpreterBuilder` then does not call
`SetNumThreads` at all, and both the interpreter's own kernels and the
default-applied XNNPACK delegate end up on **one thread**. The bundled
`litert 1.4.0` AAR does contain XNNPACK (`TfLiteXNNPackDelegateCreate` and
"Created TensorFlow Lite XNNPACK delegate for CPU." are both in
`libtensorflowlite_jni.so`), so the delegate is there - it simply gets one
thread to work with on a phone with eight cores.

Two consequences worth knowing before anyone proposes a native module:

1. The cheapest possible runtime change is **one line** in that file,
   `TfLiteInterpreterOptionsSetNumThreads(options, n)`, applied with
   `patch-package` (already this repo's `postinstall`). No new module, no new
   binding, no new surface.
2. `TfLiteInterpreterInvoke` does NOT run on the JS thread. Nitro's
   `Promise::async` hands it to `ThreadPool::shared()` - 3 threads growing to
   10 - and only the *resolution* comes back to JS. So a `model.run` span
   measured in JS is `native invoke + delivery delay`, and the delivery delay
   is a property of how busy the JS thread is. See
   `selection/js-thread-profile.ts` and the `[album-runtime]` log line.

To find out how much of a delegated graph XNNPACK actually took, the runtime
already logs it at INFO with the `tflite` tag:

```
adb logcat -s tflite | grep "Replacing"
```

## Legacy ONNX note

`onnxruntime-react-native` remains outside the evaluated runtime path because
its module initialization is incompatible with this Expo 57 / React Native
0.86 New-Architecture build. Native perception uses
`react-native-fast-tflite` and ML Kit instead.
