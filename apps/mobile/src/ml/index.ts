import { StubOnDeviceModel } from "./stub-model";
import type { ModelResult, OnDeviceModel } from "./types";
import { yunetModel } from "./yunet";

export type { ModelResult, OnDeviceModel } from "./types";

const stub = new StubOnDeviceModel();

/**
 * The active on-device model: real YuNet face detection via
 * onnxruntime-react-native (docs/model-cards/yunet-ondevice.md), with a
 * per-call fallback to the deterministic stub if the native runtime is
 * unavailable — the app degrades gracefully instead of crashing. The
 * `embedding` is a cheap on-device colour histogram until M2 brings a
 * quantized SigLIP.
 */
const model: OnDeviceModel = {
  async run(imageUri: string): Promise<ModelResult> {
    try {
      return await yunetModel.run(imageUri);
    } catch (error) {
      console.warn("[ml] on-device model unavailable, using stub:", error);
      return stub.run(imageUri);
    }
  },
};

export function getModel(): OnDeviceModel {
  return model;
}
