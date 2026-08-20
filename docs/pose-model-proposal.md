# Body-Pose Model Proposal

Status: PROPOSAL — no registry entry, no config, no weights added. Research only (2026-08-20;
candidate sweep refreshed same day, see "Research update" below).
Need: per-person body keypoints on studio/family photos so selection can (a) cluster frames into
true poses ("100s of different poses the code is not able to detect"), (b) add a pose-variety axis
to album diversity, (c) flag awkward mid-transition poses. Local, commercial, ONNX Runtime on
Apple Silicon (same host that runs SCRFD/ArcFace/SigLIP today).

## Recommendation

- **Primary: RTMO-m** (one-stage, multi-person, 17 COCO keypoints). Apache-2.0 code AND weights,
  ~70.9 COCO AP, ONNX export via MMDeploy and prebuilt ONNX via rtmlib. One-stage matters here:
  we have face detectors, not a person detector, and RTMO needs neither — one graph, whole image
  in, all persons + keypoints out. ~22M params (~45 MB fp16 / ~90 MB fp32). Paper CPU latencies
  (i7-11700, ORT): RTMO-s 8.9 ms / m 12.4 ms / l 19.1 ms — M-series CPU should be comparable;
  CoreML EP is a bonus, not a requirement.
- **Fallback: RTMPose-m + YOLOX-s person detector** (both Apache-2.0, both shipped as ONNX by
  rtmlib). Higher per-person accuracy (75.8 AP at 256x192, 13.6M params) if RTMO misses in dense
  group shots; cost is a second model + per-person crops (studio photos: 1–6 people, acceptable).
- If footprint ever matters more than accuracy: MoveNet MultiPose Lightning (Apache-2.0, ~9 MB)
  is the licence-clean minimum, but tops out at 6 people and clearly lower accuracy.

## License audit summary (desk assessment — human must verify at source before promotion)

| Candidate | Weights license | Commercial | Verdict | Source |
|---|---|---|---|---|
| RTMO (MMPose) | Apache-2.0; maintainer: "All of MMPose projects are licensed with permission for commercial use" | permitted | **PRIMARY** | [LICENSE](https://github.com/open-mmlab/mmpose/blob/main/LICENSE), [#2106](https://github.com/open-mmlab/mmpose/issues/2106), [#2393](https://github.com/open-mmlab/mmpose/issues/2393) |
| RTMPose (MMPose) | Apache-2.0 (same statement) | permitted | **FALLBACK** (needs person detector) | [project](https://github.com/open-mmlab/mmpose/tree/main/projects/rtmpose) |
| rtmlib (ONNX distribution + reference pre/post) | Apache-2.0 | permitted | use as reference for pre/postprocessing | [repo](https://github.com/Tau-J/rtmlib) |
| MoveNet (Lightning/Thunder/MultiPose) | Apache-2.0 | permitted | viable minimum; TFLite-native, ONNX via conversion (quality of converted graph must be pinned) | [Kaggle model](https://www.kaggle.com/models/google/movenet) |
| MediaPipe BlazePose / Pose Landmarker | Apache-2.0 per model card | permitted | weak fit: single-person-first, .task/TFLite packaging, awkward ONNX path | [model card](https://storage.googleapis.com/mediapipe-assets/Model%20Card%20BlazePose%20GHUM%203D.pdf) |
| ViTPose | Apache-2.0 (repo + HF transformers ports) | permitted | licence-clean but 86M+ params for +0.0–3 AP over RTMPose-l; not worth the weight | [LICENSE](https://github.com/ViTAE-Transformer/ViTPose/blob/main/LICENSE) |
| YOLO-NAS-Pose | custom Deci licence: pretrained weights **non-commercial**; repo effectively unmaintained post-NVIDIA acquisition | **blocked** | disqualified (CodeFormer-class landmine) | [LICENSE.YOLONAS-POSE.md](https://github.com/Deci-AI/super-gradients/blob/master/LICENSE.YOLONAS-POSE.md) |
| YOLOv8/YOLO11-pose (Ultralytics) | AGPL-3.0 (or paid enterprise licence) | **blocked** for closed local product | disqualified (Essentia-class landmine) | [LICENSE](https://github.com/ultralytics/ultralytics/blob/main/LICENSE) |
| Sapiens (Meta, 2024, SOTA) | CC-BY-NC-4.0 | **blocked** | disqualified; also 0.3B–2B params, wrong size class anyway | [repo](https://github.com/facebookresearch/sapiens), [HF](https://huggingface.co/facebook/sapiens-pose-1b) |

Training-data terms: COCO annotations are CC-BY-4.0 (commercial-friendly; maintainer statement in
#2106 leans on exactly this). BUT several published RTMPose/RTMO checkpoints are the "body7"
mixes (adds AI Challenger, CrowdPose, MPII, sub-JHMDB, Halpe, PoseTrack18) whose dataset terms
are NOT all verified. Audit rule for the config: pin a **COCO(-family)-trained checkpoint whose
training-set list is enumerated in the model card**, and record the dataset-terms check per
dataset — same discipline as yunet-2023mar's `training_data` field. If the body7 checkpoint's
accuracy is wanted, the AI Challenger / Halpe terms check is a blocking TODO, not a footnote.

## Research update (2026-08-20 sweep of newer candidates)

A second pass over the 2025–2026 releases. Verdict up front: **RTMO-m stays primary**, and
**DETRPose-M/L becomes the head-to-head challenger** — the eval harness should score both on our
libraries before a registry entry is written for either.

| Candidate | Year | License (code/weights) | Multi-person | Verdict |
|---|---|---|---|---|
| **DETRPose** (N/S/M/L/X) | 2025 | Apache-2.0 both | end-to-end, one graph | **CHALLENGER** — bench against RTMO |
| RF-DETR Keypoint (Roboflow) | 2026-06 | Apache-2.0 both | end-to-end (DINOv2 backbone) | WATCH — preview only |
| Sapiens2 (Meta) | 2026-04 | custom "Sapiens2 License" | top-down (needs detector) | still disqualified (see below) |
| YOLO26-pose (Ultralytics) | 2026 | AGPL-3.0 | one-stage | still blocked, same as YOLOv8/11 |

- **DETRPose** ([repo](https://github.com/SebastianJanampa/DETRPose),
  [paper](https://arxiv.org/abs/2506.13027)): first real-time end-to-end transformer for
  multi-person pose. Apache-2.0 code AND weights. COCO val2017: M = 69.4 AP @ 20.8M params,
  L = 72.5 AP @ 32.8M — bracketing RTMO-m from both sides. Two structural advantages for us:
  (a) **NMS-free** DETR head, so the postprocessing block in the model config is nearly empty —
  no grid-decode/NMS host step to pin and re-verify per export (the RTMO open question);
  (b) checkpoints are **COCO-only** (CrowdPose variants published separately), so the
  training-data audit is one dataset, not the body7 five. Open risk: published latencies are
  GPU/TensorRT; transformer decoders are historically worse than CNN heads on CPU — **measure
  ORT-CPU on M-series before believing it fits the host budget**. ONNX export script ships in
  `tools/deployment`.
- **RF-DETR Keypoint** ([docs](https://rfdetr.roboflow.com/latest/learn/run/keypoints/),
  [launch](https://blog.roboflow.com/launch-rf-detr-keypoint-in-roboflow/)): Apache-2.0 weights,
  claims to beat YOLO26-pose (71.8 AP, 40.7M params, 576x576, DINOv2 backbone). Disqualified
  *today* on process, not merit: it is an explicit early-access **preview** — "checkpoint weights
  may change before the stable release", distribution is via the `rfdetr` package only, and no
  documented ONNX path. A checkpoint that may change under its own URL cannot be pinned by
  blake3 the way the registry requires. Re-evaluate at stable release.
- **Sapiens2** ([repo](https://github.com/facebookresearch/sapiens2/blob/main/LICENSE.md)): the
  NC blocker is GONE — the new custom license permits commercial use. Still disqualified for
  this slot: (a) prohibits "identifying/re-identifying individuals", which sits one contract
  clause away from a pipeline whose whole point is naming the people in the album — a lawyer
  question, not an engineer one; (b) audit rights + share-alike redistribution friction;
  (c) 0.3B–2B params, top-down, wrong size class for an always-on local analysis step. Worth
  remembering if a future "hero-shot body segmentation/matting" feature wants a heavyweight
  offline pass.
- Also seen, not shortlisted: SMPLest-X (3D body-shape recovery — wrong task), GroupPose
  (DETRPose supersedes it), EdgeCrafter-style distilled ViTs (no published pose checkpoints
  with clean weights terms).

Sources: [Roboflow pose-model survey](https://blog.roboflow.com/best-pose-estimation-models/),
[RF-DETR Apache-2.0 statement](https://blog.roboflow.com/rf-detr-is-free-to-use-commercially/),
[Sapiens2 announcement](https://www.marktechpost.com/2026/04/27/meta-ai-releases-sapiens2-a-high-resolution-human-centric-vision-model-for-pose-segmentation-normals-pointmap-and-albedo/).

## Integration sketch

1. **New pipeline step `rtmo-body17`** in `photo_analysis` (and `photo_analysis_release` — the
   licence is clean, so unlike SCRFD it can sit in both). Input proxy: existing thumbnail/preview
   (RTMO input 640x640 letterbox — reuse the letterbox path; `pad_value` must be pinned per the
   issue #33 discipline, from rtmlib/MMDeploy preprocessing, before release mode will load it).
2. **Output**: per person — bbox, 17 keypoints (x, y, score). Person↔face linkage by containment
   of the face box (YuNet/SCRFD) in the person bbox, so keypoints attach to identified people.
   Contract-first: this crosses the agent boundary → schema (`PoseRecord` or a `people[]`
   extension on MediaRecord) + golden fixtures in `contracts/`, Codex sign-off, before code.
3. **Pose signature**: normalized joint-angle vector — 8–10 interior angles (elbows, shoulders,
   hips, knees, neck–torso, torso lean vs vertical), scale- and translation-free by construction;
   optionally mirror-folded so left/right variants of the same pose merge. Low-score joints
   masked. Deterministic float vector → lives in the plan, per hard rule 3.
4. **Pose clustering**: greedy threshold clustering (same shape as face clustering) on signature
   distance, within an event/shot group, alongside the existing SigLIP embedding groups. Output:
   pose cluster id per person per frame → "N distinct poses" census per session.
5. **Selection**: pose-variety becomes an axis in album diversity (penalize picking two frames
   from the same pose cluster; the census feeds "cover all poses at least once" constraints).
6. **Awkward mid-transition detection**: within a burst, a frame whose signature sits between two
   stable clusters (high distance to both neighbours' modes) + depressed keypoint scores +
   high signature velocity vs adjacent frames → transition flag, ranked down. Threshold tuning is
   an eval-harness job against a labelled awkward/clean set before it gates anything.

## Registry entry requirements (per models/ conventions)

- `models/configs/rtmo-m-body17.json` conforming to `models/schema/model-config.schema.json`:
  pinned `weights.blake3` + `byte_size` + exact `source_url`; full `preprocessing` block with
  letterbox `pad_value` sourced (not null) before release; `postprocessing` steps named (RTMO's
  grid decode + NMS — declare whether NMS is inside the ONNX graph or a host step, it differs by
  export); real `outputs` shapes measured from the actual graph (yunet issue #31 lesson: run the
  session, don't trust docs); `batching` measured, not assumed.
- `license` block: `code_license`/`weights_license` = Apache-2.0, `commercial_use` = permitted,
  `verified` = false until a human reads the licence at source and sets `verified_at`;
  `training_data` enumerating datasets + their terms; `blocks_commercial_release` = false.
- `rollout.state` = candidate; `eval.benchmark` = not_yet_run until the harness scores it on our
  benchmark libraries (pose-cluster purity + transition-flag precision, not COCO AP re-runs).
- `registry.json`: new entry with `config_blake3`, task `body_pose`, `required_for`:
  `pose_variety`, `pose_census`, `transition_detection`; added to both photo pipelines.

## Open questions (owner / Codex)

1. Apple Vision's `VNDetectHumanBodyPoseRequest` would be zero-weights and zero-audit — but the
   model silently changes with macOS updates, which collides with hard rule 7 (no silent model
   swap) and determinism. Proposal treats it as disqualified; owner may overrule.
2. RTMO-m vs RTMO-l vs DETRPose-M/L vs fallback-to-RTMPose: decide after the eval harness runs
   on real studio sets — dense group-shot recall AND M-series ORT-CPU latency are the open
   questions (DETRPose's transformer decoder is unproven on CPU; RTMO's CPU numbers are
   published).
3. COCO-only vs body7 checkpoint (blocked on the dataset-terms audit above) — accuracy delta
   should be measured on our libraries before anyone bothers auditing five datasets.
4. Does pose need to be whole-body (DWPose/RTMW, 133 kpts, also Apache-2.0 via MMPose) for
   hand-position variety in studio poses, or is body-17 enough for the census? Start with 17.
5. Contract shape: new `PoseRecord` vs `people[]` on MediaRecord — Codex preference?
