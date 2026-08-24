# On-device model boundary

The general `getModel()` compatibility boundary retains a deterministic
JavaScript fallback. Production selection augments it with three guarded,
lazy, New-Architecture TFLite paths:

- `movenet.ts`: 17-point body pose for coverage diversity;
- `tinyclip.ts`: semantic image embeddings and offline zero-shot axes;
- `facenet.ts`: MobileFaceNet identity embeddings for person clustering.

Every path returns neutral or `undefined` on native, model, image, or tensor
failure, so `buildAlbum()` always retains deterministic fallback signals.

## Legacy ONNX note

`onnxruntime-react-native` remains outside the evaluated runtime path because
its module initialization is incompatible with this Expo 57 / React Native
0.86 New-Architecture build. Native perception uses
`react-native-fast-tflite` and ML Kit instead.
