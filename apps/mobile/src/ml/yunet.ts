// Real on-device face detection: YuNet via onnxruntime-react-native.
// Decode validated against OpenCV FaceDetectorYN (40/40) — see
// docs/model-cards/yunet-ondevice.md. Runs entirely on the phone.
import { Asset } from "expo-asset";
import * as ImageManipulator from "expo-image-manipulator";
import { decode as decodeJpeg } from "jpeg-js";
import { InferenceSession, Tensor } from "onnxruntime-react-native";

import type { ModelResult, OnDeviceModel } from "./types";

const S = 640;
const STRIDES = [8, 16, 32] as const;
const SCORE_THRESHOLD = 0.9;
const NMS_IOU = 0.3;

let sessionPromise: Promise<InferenceSession> | null = null;

async function getSession(): Promise<InferenceSession> {
  if (!sessionPromise) {
    sessionPromise = (async () => {
      const asset = Asset.fromModule(
        require("../../assets/models/yunet.onnx") as number,
      );
      await asset.downloadAsync();
      const uri = asset.localUri ?? asset.uri;
      return InferenceSession.create(uri.replace("file://", ""));
    })();
  }
  return sessionPromise;
}

/** Load an image, letterbox to 640x640, return a BGR NCHW float tensor + the
 *  transform needed to map boxes back, plus a cheap colour-histogram embedding. */
async function toTensor(imageUri: string): Promise<{
  data: Float32Array;
  scale: number;
  padX: number;
  padY: number;
  embedding: number[];
}> {
  const meta = await ImageManipulator.manipulateAsync(imageUri, []);
  const w0 = meta.width;
  const h0 = meta.height;
  const scale = Math.min(S / w0, S / h0);
  const nw = Math.max(1, Math.round(w0 * scale));
  const nh = Math.max(1, Math.round(h0 * scale));

  const resized = await ImageManipulator.manipulateAsync(
    imageUri,
    [{ resize: { width: nw, height: nh } }],
    { base64: true, compress: 1, format: ImageManipulator.SaveFormat.JPEG },
  );
  const raw = Uint8Array.from(atob(resized.base64 ?? ""), (c) => c.charCodeAt(0));
  const { data: rgba, width, height } = decodeJpeg(raw, { useTArray: true });

  const padX = Math.floor((S - width) / 2);
  const padY = Math.floor((S - height) / 2);
  const chan = S * S;
  const data = new Float32Array(3 * chan); // zero-padded letterbox
  const hist = new Array(64).fill(0); // 4x4x4 RGB histogram
  for (let y = 0; y < height; y += 1) {
    for (let x = 0; x < width; x += 1) {
      const p = (y * width + x) * 4;
      const r = rgba[p];
      const g = rgba[p + 1];
      const b = rgba[p + 2];
      const dst = (y + padY) * S + (x + padX);
      data[dst] = b; // channel 0 = B (model expects BGR)
      data[chan + dst] = g; // channel 1 = G
      data[2 * chan + dst] = r; // channel 2 = R
      hist[((r >> 6) << 4) | ((g >> 6) << 2) | (b >> 6)] += 1;
    }
  }
  const total = width * height || 1;
  const embedding = hist.map((v) => v / total);
  return { data, scale, padX, padY, embedding };
}

type Box = { x: number; y: number; w: number; h: number; score: number };

function iou(a: Box, b: Box): number {
  const x1 = Math.max(a.x, b.x);
  const y1 = Math.max(a.y, b.y);
  const x2 = Math.min(a.x + a.w, b.x + b.w);
  const y2 = Math.min(a.y + a.h, b.y + b.h);
  const inter = Math.max(0, x2 - x1) * Math.max(0, y2 - y1);
  const union = a.w * a.h + b.w * b.h - inter;
  return union <= 0 ? 0 : inter / union;
}

function nms(boxes: Box[]): Box[] {
  const kept: Box[] = [];
  for (const box of boxes.sort((p, q) => q.score - p.score)) {
    if (kept.every((k) => iou(box, k) < NMS_IOU)) {
      kept.push(box);
    }
  }
  return kept;
}

function decode(
  outputs: Record<string, Tensor>,
  scale: number,
  padX: number,
  padY: number,
): number {
  const boxes: Box[] = [];
  for (const s of STRIDES) {
    const w = S / s;
    const cls = outputs[`cls_${s}`].data as Float32Array;
    const obj = outputs[`obj_${s}`].data as Float32Array;
    const bbox = outputs[`bbox_${s}`].data as Float32Array;
    for (let i = 0; i < cls.length; i += 1) {
      const score = Math.sqrt(Math.max(cls[i], 0) * Math.max(obj[i], 0));
      if (score < SCORE_THRESHOLD) continue;
      const col = i % w;
      const row = Math.floor(i / w);
      const b = i * 4;
      const cx = (col + bbox[b]) * s;
      const cy = (row + bbox[b + 1]) * s;
      const bw = Math.exp(bbox[b + 2]) * s;
      const bh = Math.exp(bbox[b + 3]) * s;
      boxes.push({
        x: (cx - bw / 2 - padX) / scale,
        y: (cy - bh / 2 - padY) / scale,
        w: bw / scale,
        h: bh / scale,
        score,
      });
    }
  }
  return nms(boxes).length;
}

export const yunetModel: OnDeviceModel = {
  async run(imageUri: string): Promise<ModelResult> {
    const session = await getSession();
    const { data, scale, padX, padY, embedding } = await toTensor(imageUri);
    const input = new Tensor("float32", data, [1, 3, S, S]);
    const outputs = await session.run({ input });
    const faces = decode(outputs as Record<string, Tensor>, scale, padX, padY);
    return { embedding, faces };
  },
};
