import { probeFaceIdentityModel } from "./facenet";
import { probeBodyPoseModel } from "./movenet";
import { StubOnDeviceModel } from "./stub-model";
import { probeSemanticModel } from "./tinyclip";
import type { ModelResult, OnDeviceModel } from "./types";

export type { ModelResult, OnDeviceModel } from "./types";

// NOTE: the real YuNet path (./yunet.ts) is intentionally NOT imported here.
// It depends on onnxruntime-react-native, which is old-architecture only; under
// RN 0.86's forced New Architecture its native binding is null and calling
// .install() on it throws `Cannot read property 'install' of null`. That throw
// happens during Metro module evaluation, so it CANNOT be caught with a
// try/catch around a dynamic import() — it crashes the whole app the moment the
// module is loaded (at album build time). So onnxruntime must never enter the
// bundle's runtime path at all. On-device faces return via ./yunet.ts once it is
// ported to react-native-fast-tflite (New-Arch native). Until then: stub only.
// ponytail: single stub, swap in tflite model behind this same interface later.
const stub = new StubOnDeviceModel();

export function getModel(): OnDeviceModel {
  return stub;
}

export type ModelProbe = {
  facenet: boolean;
  movenet: boolean;
  tinyclip: boolean;
};

/**
 * Loads all three TFLite graphs and reports which ones are actually usable.
 * Every wrapper swallows load failure and returns undefined per photo, so
 * without this a build with a missing or mismatched model is indistinguishable
 * from a working one - the album just gets quietly worse. Never throws.
 *
 * Not wired into any screen: call it from a debug affordance or a dev console.
 */
export async function probeModels(): Promise<ModelProbe> {
  const probe = async (run: () => Promise<boolean>): Promise<boolean> => {
    try {
      return await run();
    } catch {
      return false;
    }
  };
  const [facenet, movenet, tinyclip] = await Promise.all([
    probe(probeFaceIdentityModel),
    probe(probeBodyPoseModel),
    probe(probeSemanticModel),
  ]);
  return { facenet, movenet, tinyclip };
}
