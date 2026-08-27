import { probeFaceIdentityModel } from "./facenet";
import { benchmarkPoseInference, probeBodyPoseModel } from "./movenet";
import { StubOnDeviceModel } from "./stub-model";
import { benchmarkSemanticInference, probeSemanticModel } from "./tinyclip";
import type { InferenceBenchmark } from "./model-cache";
import type { ModelResult, OnDeviceModel } from "./types";

export type { ModelResult, OnDeviceModel } from "./types";
export type { InferenceBenchmark } from "./model-cache";

// Face detection does NOT run through this interface: it uses ML Kit via
// ../faces/face-detector.ts, and identity embedding uses MobileFaceNet through
// react-native-fast-tflite. The old YuNet/onnxruntime path was deleted with the
// dependency -- onnxruntime-react-native is old-architecture only, its native
// binding is null under RN 0.86's forced New Architecture, and the throw happens
// during Metro module evaluation so it cannot be caught by a try/catch around a
// dynamic import(). It was unreachable by design yet still shipped 131MB of
// native libraries across four ABIs, which is 40% of the release APK.
// ponytail: single stub, swap in a tflite model behind this same interface later.
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
 * Prefer `checkModelHealth()` for anything that may be called more than once.
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

/** Human-readable one-liner, e.g. "facenet=ok movenet=MISSING tinyclip=ok". */
export function describeModelProbe(probe: ModelProbe): string {
  return (Object.keys(probe) as Array<keyof ModelProbe>)
    .map((name) => `${name}=${probe[name] ? "ok" : "MISSING"}`)
    .join(" ");
}

export type InferenceBenchmarks = {
  tinyclip?: InferenceBenchmark;
  movenet?: InferenceBenchmark;
};

/**
 * What the two graphs cost when the JS thread has nothing else to do.
 *
 * Deliberately SEQUENTIAL. Running them together would put two invokes on the
 * Nitro thread pool at once and reintroduce exactly the core contention the
 * benchmark exists to exclude — and would then be indistinguishable from the
 * concurrent pass it is meant to be the control for.
 *
 * Never throws and never rejects; a build must not fail because a diagnostic
 * could not run.
 */
export async function benchmarkInferenceModels(
  runs = 3,
): Promise<InferenceBenchmarks> {
  try {
    const tinyclip = await benchmarkSemanticInference(runs);
    const movenet = await benchmarkPoseInference(runs);
    return { tinyclip, movenet };
  } catch {
    return {};
  }
}

let healthCheck: Promise<ModelProbe> | undefined;

/**
 * Session-memoized `probeModels()` that also writes the result to the log.
 *
 * `buildAlbum()` awaits this before the heavy pass, so every beta build leaves
 * one grep-able line ("[photeo-models] ...") saying which graphs actually
 * loaded. It also settles each wrapper's usability latch up front, so a broken
 * graph stops costing per-photo preprocessing for a result it can never return.
 *
 * A UI worker wanting a debug affordance should call THIS (re-exported from
 * `src/build-album.ts`) rather than `probeModels()`: it is idempotent, never
 * throws, and never loads a second copy of a 32MB graph.
 */
export function checkModelHealth(): Promise<ModelProbe> {
  // Never rejects: buildAlbum() awaits this, and a diagnostic must not be able
  // to fail the album it is diagnosing.
  healthCheck ??= probeModels()
    .then((probe) => {
      console.warn(`[photeo-models] ${describeModelProbe(probe)}`);
      return probe;
    })
    .catch(() => ({ facenet: false, movenet: false, tinyclip: false }));
  return healthCheck;
}
