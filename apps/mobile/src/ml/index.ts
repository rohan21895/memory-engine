import { StubOnDeviceModel } from "./stub-model";
import type { OnDeviceModel } from "./types";

export type { ModelResult, OnDeviceModel } from "./types";

const model: OnDeviceModel = new StubOnDeviceModel();

/**
 * Returns the active on-device model implementation.
 *
 * CL-1 will replace the stub with an onnxruntime-react-native implementation
 * of this same interface, backed by SigLIP2 embeddings and YuNet face counts.
 */
export function getModel(): OnDeviceModel {
  return model;
}
