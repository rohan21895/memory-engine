# On-device model boundary

The general `getModel()` compatibility boundary retains a deterministic
JavaScript fallback. Production selection augments it with three guarded,
lazy, New-Architecture TFLite paths:

- `movenet.ts`: 17-point body pose for coverage diversity;
- `tinyclip.ts`: semantic image embeddings and offline zero-shot axes;
- `facenet.ts`: MobileFaceNet identity embeddings for person clustering.

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

Every `loadTensorflowModel(...)` call passes an empty delegate list, which means
XNNPACK on the CPU. Do not add GPU delegates:

- fast-tflite 3.0.1 hardcodes the GPU delegate options with no serialization
  directory, so kernels are recompiled on every cold start - fatal for batch work;
- it also hardcodes `max_delegated_partitions=1`;
- its GPU path has an open batch-mismatch bug on ViT graphs (`tinyclip.ts`) and
  a reported PowerVR crash;
- NNAPI is deprecated on Android 15.

## Legacy ONNX note

`onnxruntime-react-native` remains outside the evaluated runtime path because
its module initialization is incompatible with this Expo 57 / React Native
0.86 New-Architecture build. Native perception uses
`react-native-fast-tflite` and ML Kit instead.
