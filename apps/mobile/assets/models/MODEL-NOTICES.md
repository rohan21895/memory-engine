# Bundled on-device model notices

Hashes, byte sizes, and tensor metadata below are pinned to the bundled files,
not inferred from model-family documentation. A preprocessing contract ID covers
the complete app-side pixel path; changing resize/crop behavior, channel order,
normalization, or output normalization requires a new ID and downstream
recalibration.

## MoveNet SinglePose Lightning int8

- Source: TensorFlow Hub, `google/lite-model/movenet/singlepose/lightning/tflite/int8/4`
- License: Apache-2.0
- Bundled file: `movenet-singlepose-lightning-int8.tflite`
- SHA-256: `cd7cc22fa946e5d146a7b98d496853e1923e22828d3972d579973f27f91bb105`
- Byte size: `2,894,840`
- Tensor contract: uint8 `[1,192,192,3]` RGB/NHWC input → float32
  `[1,1,17,3]` output (`y`, `x`, confidence for 17 keypoints)
- Preprocessing contract ID: `movenet-lightning-rgb-letterbox-jpeg92-v1`
- Exact preprocessing: aspect-preserving resize to fit 192×192 through Expo
  Image Manipulator (no explicit resampler override), JPEG quality 0.92, centered
  zero/black letterbox, alpha dropped, raw RGB bytes in `[0,255]`; no input
  normalization or dequantization. If source dimensions cannot be resolved, the
  guarded fallback directly resizes to 192×192. The float32 output needs no
  dequantization.

## TinyCLIP ViT-8M/16 Text-3M

- Source weights: `wkcn/TinyCLIP-ViT-8M-16-Text-3M-YFCC15M`
- Conversion source: `onnx-community/TinyCLIP-ViT-8M-16-Text-3M-YFCC15M-ONNX`
- License: MIT, as declared by both the official TinyCLIP repository and model card
- Architecture paper: *TinyCLIP: CLIP Distillation via Affinity Mimicking and Weight Inheritance*, ICCV 2023
- Bundled image encoder: `tinyclip-vit-8m16-image-float32.tflite`
- TFLite SHA-256: `a1ccb2b874a00c533402ade45beeb392ae8e06a60a6a90829ed26a6796f399e9`
- TFLite byte size: `33,247,788`
- Tensor contract: float32 `[1,224,224,3]` RGB/NHWC input → float32
  `[1,512]` image embedding output
- Preprocessing contract ID: `tinyclip-openai-rgb-centercrop-jpeg95-v1`
- Exact preprocessing: resize the short side to 224 through Expo Image
  Manipulator (no explicit resampler override), center-crop 224×224, JPEG quality
  0.95, decode as RGB, scale channels by `1/255`, then normalize with OpenAI CLIP
  mean `[0.48145466, 0.4578275, 0.40821073]` and standard deviation
  `[0.26862954, 0.26130258, 0.27577711]`; output is L2-normalized.
- Bundled text axes: `tinyclip-text-axes.json`
- Text-axis SHA-256: `79ed8de61276327f7420787ab4acca316280a7969091fd0e4a672cac4a8da7b8`
- Text-axis byte size: `70,308`

The image branch was extracted from the MIT ONNX export (`pixel_values` to
`image_embeds`), fixed to `[1,3,224,224]`, and converted with `onnx2tf` to a
float32 `[1,224,224,3]` TFLite graph. Its output was checked against the ONNX
source at cosine similarity 1.0. The smaller float16 conversion was rejected
because its float16 input cannot allocate on the standard TFLite CPU kernels.

The text encoder is not shipped. Six positive/negative prompt ensembles were
embedded offline and averaged into the JSON sidecar: aesthetic, composition,
clean frame / bystander, sleeping / awake, embrace context, and screenshot /
document. Runtime inference therefore processes images only.

## InsightFace buffalo_s w600k_mbf face identity

- Source weights: InsightFace [`buffalo_s`](https://github.com/deepinsight/insightface/tree/master/python-package#model-zoo)
  model pack, member `w600k_mbf.onnx`
- Provenance: InsightFace MobileFaceNet-backbone recognition model trained on
  WebFace600K (`w600k_mbf`); the bundled graph is its TFLite conversion
- Code license: MIT for InsightFace code
- Pretrained-weight status: **NON-COMMERCIAL RESEARCH ONLY; LICENSE, RETRAIN, OR
  REPLACE BEFORE COMMERCIAL LAUNCH** under InsightFace's published model terms
- Bundled file: `w600k-mbf-512-float32.tflite`
- SHA-256: `ca17b05ac6e92ff819d81191d865e3864f4e6779df60468f0db547c982091033`
- Byte size: `13,635,536`
- Tensor contract: float32 `[1,112,112,3]` RGB/NHWC input → float32
  `[1,512]` identity embedding output
- Preprocessing contract ID: `w600k-mbf-arcface-rgb-jpeg95-1275-v1`
- Exact preprocessing: use a canonical ArcFace 112×112 similarity warp,
  preferring ML Kit eye+mouth landmarks and degrading to eyes+nose or eyes-only;
  if alignment is unavailable, use a 1.3× padded square face crop. The crop path
  uses Expo Image Manipulator with no explicit resampler override and JPEG
  quality 0.95. Decode as RGB and normalize each channel with
  `(channel - 127.5) / 127.5`; L2-normalize the raw 512-d output before cosine
  comparison.

The clustering and identity thresholds are calibrated in this 512-dimensional
embedding space. A graph or preprocessing-contract change must invalidate the
persisted face index and trigger threshold recalibration; dimensions and cosine
thresholds from the retired 192-d model are not transferable.
