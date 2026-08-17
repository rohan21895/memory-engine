# Decision: the padded band is config, and SCRFD's is unresolved

Closes issue #33, and records what could not be closed. Taken 2026-08-17.

---

## The gap

The detector configs pinned letterbox *geometry* — integer raster, rounding
convention, which side an odd pad lands on — along with resize interpolation,
colour order, scale, mean and std. They said nothing about what the padded band
**contains**.

That is observable model input, not an implementation detail. A 64x36 frame
letterboxed into 64x64 is 28 padded rows, 43.75% of the tensor. For YuNet
(`scale: 1`, empty `mean`, empty `std`) the band lands in the tensor as
literally whatever number is written into it. Measured on the fixture image:

| padding | tensor mean |
|---|---|
| 0 (black) | 73.90 |
| 114 (the YOLO convention) | 123.77 |
| 127.5 (mean grey) | 129.68 |

Every one of those is a perfectly valid tensor. Nothing raises. Detections near
the frame edge move. PR #25 chose black because the config offered nothing,
which is an invented default wearing the clothes of a decision.

---

## The mechanism

`preprocessing.pad_value` in `models/schema/model-config.schema.json`:

```json
{ "space": "pixel" | "normalized", "values": [n] | [n, n, n], "source": "…" }
```

- **Required whenever `resize` is `letterbox`.** A config missing the field
  fails validation — omission cannot be a decision.
- **`null` is legal and means UNRESOLVED**, not zero. It is how a config says
  its upstream sources disagree.
- **`source` must be a followable citation** — file and symbol. A registry test
  requires a path separator in it and a length no one-word answer reaches.
- **The release load gate refuses an unresolved entry** (`CONFIG_UNPINNED`,
  via `require_pinned_preprocessing`). Development permits it, because
  development is where the missing measurement gets taken.
- **The executable reference refuses to guess.** `pad_band(None, …)` raises
  `PadValueUnresolved` rather than filling the band with a default.

### Why `space` is pinned alongside the number

`pixel` fills the band before normalisation, so it lands at
`(v * scale - mean) / std`. `normalized` writes straight into the finished
tensor.

That distinction is not pedantry — it is the entire SCRFD problem. mmdetection
test pipelines run `Normalize` **then** `Pad(pad_val=0)`, so their zero means
*the mean*, never black. Recording only the number collapses the two and picks
the wrong one half the time.

---

## YuNet: pinned to pixel 0

Three upstream sources agree, and none of them needs the checkpoint to read:

1. `opencv/opencv` `modules/objdetect/src/face_detect.cpp`,
   `FaceDetectorYNImpl::padWithDivisor` — `copyMakeBorder(..., BORDER_CONSTANT, 0)`.
   This is the runtime every published YuNet number is measured through.
2. `ShiqiYu/libfacedetection.train`
   `yunet_train/tasks/face/transforms.py::build_eval_transforms` —
   `Pad(size=(image_size, image_size), pad_value=0)` followed by
   `Normalize(mean=0, std=1)`.
3. The same file's `size_divisor` eval variant — `Pad(size_divisor=…, pad_value=0)`.

Because this config's normalisation is the identity, pixel 0 and tensor 0.0 are
the same number, so `space` cannot be got wrong here either.

**Two nuances recorded rather than smoothed over.** Upstream does not letterbox
at all: `cv::dnn` reshapes the fully-convolutional network to the image's own
size and pads only to the next multiple of 32. We letterbox to a square because
the ONNX graph's input is fixed at 640x640 (issue #31), so **our padded band is
much larger than any band this model was evaluated with**. And YuNet's *training*
augmentation fills out-of-image crop regions with **128**, not 0
(`transforms.py::_crop_image_with_padding`), so the network has seen both. 0 is
pinned because it is what the letterbox band specifically is upstream, in both
eval and deployment; 128 belongs to a different operation.

---

## SCRFD: left unresolved, and therefore unloadable in release

Issue #33 says a wrong pinned value is worse than an absent one. SCRFD is the
case that makes that instruction bite.

**Upstream contradicts itself, and the two answers are a full unit apart in
tensor space:**

| source | what it does | band in tensor units |
|---|---|---|
| `detection/scrfd/configs/scrfd/scrfd_10g_bnkps.py`, `test_pipeline` | `Resize(keep_ratio=True)` → `Normalize(127.5, 128, to_rgb=True)` → `Pad(size=(640,640), pad_val=0)` | **0.0** (≡ pixel 127.5) |
| `python-package/insightface/model_zoo/scrfd.py::_detect_candidates` | `np.zeros(..., uint8)` then paste, then normalise | **-0.99609375** (pixel 0) |
| `detection/scrfd/tools/scrfd.py` | same as above | **-0.99609375** |

The first is the pipeline `tools/test_widerface.py` drives — the one the
published WIDERFace AP numbers were measured with. The second and third are how
the ONNX export we consume is actually run.

**Training does not break the tie, which is the part worth keeping.** The
`train_pipeline` in that same config file uses
`Resize(img_scale=(640,640), keep_ratio=False)` — a **stretch**. SCRFD never saw
a letterbox band during training at all, so there is no "value it was trained
with" to recover. That also means our letterbox geometry is itself a departure
from how the model was fitted, which is a larger question than the pad value and
is not settled here.

Choosing between two authoritative sources by preference is guessing with extra
steps, so the config says `null` and the release gate refuses it.

**The cost is bounded and was checked before accepting it.** SCRFD is already
development-only because its weights are non-commercial (issue #3), and
`yunet-2023mar` is the selected detector for anything that ships. No release
pipeline contains SCRFD, and a registry test enforces that an unresolved entry
never enters one.

**To settle it:** obtain `det_10g.onnx`, run both padding values over a face set
with subjects near the frame edge, and pin whichever reproduces the published
AP. That is a measurement, not a reading.

---

## What is now tested

- The schema rejects a letterbox config with the field missing, a `pad_value`
  with no `source`, an empty `source`, a two-channel `values`, and an unknown
  `space`; it accepts an explicit `null`.
- `models/fixtures/letterbox-pad-value-yunet.json` pins a tensor with a
  **non-empty** band, regenerated from the config rather than from itself, and
  carries the 114 and 127.5 counter-cases so the fixture proves the value
  matters rather than restating the code.
- `pixel` and `normalized` spaces are asserted to produce different numbers
  under a mean/std config (`-0.99609375` vs `0.0`) — the SCRFD ambiguity, as a
  test.
- The release gate refuses an unpinned entry; development permits it; a weights
  problem is still reported ahead of it.
