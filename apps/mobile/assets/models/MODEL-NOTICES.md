# Bundled on-device model notices

## MoveNet SinglePose Lightning int8

- Source: TensorFlow Hub, `google/lite-model/movenet/singlepose/lightning/tflite/int8/4`
- License: Apache-2.0
- Bundled file: `movenet-singlepose-lightning-int8.tflite`
- SHA-256: `cd7cc22fa946e5d146a7b98d496853e1923e22828d3972d579973f27f91bb105`

## TinyCLIP ViT-8M/16 Text-3M

- Source weights: `wkcn/TinyCLIP-ViT-8M-16-Text-3M-YFCC15M`
- Conversion source: `onnx-community/TinyCLIP-ViT-8M-16-Text-3M-YFCC15M-ONNX`
- License: MIT, as declared by both the official TinyCLIP repository and model card
- Architecture paper: *TinyCLIP: CLIP Distillation via Affinity Mimicking and Weight Inheritance*, ICCV 2023
- Bundled image encoder: `tinyclip-vit-8m16-image-float32.tflite`
- TFLite SHA-256: `a1ccb2b874a00c533402ade45beeb392ae8e06a60a6a90829ed26a6796f399e9`
- Bundled text axes: `tinyclip-text-axes.json`
- Text-axis SHA-256: `79ed8de61276327f7420787ab4acca316280a7969091fd0e4a672cac4a8da7b8`

The image branch was extracted from the MIT ONNX export (`pixel_values` →
`image_embeds`), fixed to `[1,3,224,224]`, and converted with `onnx2tf` to a
float32 `[1,224,224,3]` TFLite graph. Its output was checked against the ONNX
source at cosine similarity 1.0. The smaller float16 conversion was rejected
because its float16 input cannot allocate on the standard TFLite CPU kernels.

The text encoder is not shipped. Six positive/negative prompt ensembles were
embedded offline and averaged into the JSON sidecar: aesthetic, composition,
clean frame / bystander, sleeping / awake, embrace context, and screenshot /
document. Runtime inference therefore processes images only.
