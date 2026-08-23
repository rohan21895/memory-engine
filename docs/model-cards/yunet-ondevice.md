# On-device model card — YuNet face detection (CL-1)

**Model:** `face_detection_yunet_2023mar.onnx` (OpenCV Zoo, **Apache-2.0**, 227 KB) → bundled at
`apps/mobile/assets/models/yunet.onnx`. Runs on-device via `onnxruntime-react-native` (NNAPI/XNNPACK on
Android, CoreML on iOS). Chosen over SCRFD/ArcFace (non-commercial research weights) and SigLIP (817 MB,
too big for a phone — deferred to M2 quantized + download-on-first-run).

**Validated on desktop:** the decode below matches OpenCV's reference `FaceDetectorYN` **40/40** on
maternity proxies at the same 640 input. (Against native-resolution OpenCV it's ~31/40 — the difference is
only tiny faces the 640 model downscales away, acceptable for a face-count signal.)

## I/O
- **Input** `input`: `float32 [1,3,640,640]`, **NCHW, BGR channel order, raw 0–255 (no mean/std)**.
- **Outputs** (per stride s ∈ {8,16,32}, grid W=H=640/s → 6400/1600/400 anchors):
  `cls_s [1,N,1]`, `obj_s [1,N,1]`, `bbox_s [1,N,4]`, `kps_s [1,N,10]`.

## Preprocess (letterbox — required; plain stretch loses small faces)
```
scale = min(640/w0, 640/h0);  nw = round(w0*scale);  nh = round(h0*scale)
canvas = zeros(640,640,3);  padX = (640-nw)//2;  padY = (640-nh)//2
canvas[padY:padY+nh, padX:padX+nw] = resize(img, nw, nh)   # BGR, uint8→float32
tensor = canvas.transpose(2,0,1)[None]                      # NCHW
```

## Decode (per anchor i at stride s; row = i//W, col = i%W)
```
score = sqrt(max(cls_s[i],0) * max(obj_s[i],0))            # threshold 0.9
cx = (col + bbox_s[i][0]) * s;   cy = (row + bbox_s[i][1]) * s
w  = exp(bbox_s[i][2]) * s;      h  = exp(bbox_s[i][3]) * s
# box in 640 space; map back to original:
x1 = ((cx - w/2) - padX) / scale;  y1 = ((cy - h/2) - padY) / scale;  bw = w/scale;  bh = h/scale
```
Then **NMS** (IoU 0.3) over surviving boxes. `faces = count(kept)`. (5 landmarks in `kps_s` decode the
same way — reserved for eyes-open/blink signals in M2.)

## Consumed by
`selectBestShots(photos, {count})` reads an optional numeric `faces` per photo. This card makes that field
**real** on-device. The `embedding` field (dedup/diversity) stays a cheap on-device proxy until M2 brings a
quantized SigLIP.
