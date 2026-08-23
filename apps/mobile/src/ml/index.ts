import { StubOnDeviceModel } from "./stub-model";
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
