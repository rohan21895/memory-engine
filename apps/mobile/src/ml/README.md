# On-device model boundary

`getModel()` currently returns a deterministic, dependency-free JavaScript
stub. It produces a fixed-length, normalized pseudo-embedding and a plausible
face count from the image URI, which lets import, selection, and review flows
run before model assets and the native runtime are available.

## CL-1 swap plan

The real implementation will continue to implement `OnDeviceModel`, so callers
will not change. It will:

1. `import { InferenceSession } from "onnxruntime-react-native"`.
2. Load quantized SigLIP2 and YuNet ONNX models from bundled app assets.
3. Decode and preprocess each image for SigLIP2 at 384px using the mean and
   standard-deviation values defined in Claude's model card.
4. Run SigLIP2 to produce the image embedding and YuNet to produce the detected
   face count.
5. Return both values as the existing `ModelResult` shape.

CX-4 intentionally does not add `onnxruntime-react-native`, model assets, native
configuration, or prebuild output. CL-1 supplies the versioned models and exact
pre/post-processing contract before the runtime is wired.
