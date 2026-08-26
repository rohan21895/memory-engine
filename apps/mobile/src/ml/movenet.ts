import type { PoseKeypoint } from "../selection/pose";
// @ts-expect-error Node's TypeScript runner requires the source extension.
import { bundledTfliteSource } from "./bundled-tflite.ts";
// @ts-expect-error Node's TypeScript runner requires the source extension.
import { createModelCache } from "./model-cache.ts";

const INPUT_SIZE = 192;
const KEYPOINT_COUNT = 17;
const OUTPUT_VALUES = KEYPOINT_COUNT * 3;
const BASE64_ALPHABET =
  "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";

export type MoveNetResult = {
  keypoints: PoseKeypoint[];
  scores: number[];
};

type TensorflowModel = {
  inputs: Array<{ dataType: string; shape: number[] }>;
  outputs: Array<{ dataType: string; shape: number[] }>;
  run(inputs: ArrayBuffer[]): Promise<ArrayBuffer[]>;
};

const modelCache = createModelCache<TensorflowModel>(loadBodyPoseModel);
let inferenceQueue: Promise<void> = Promise.resolve();
// undefined until the first acquire has reported. `false` means the bundled
// graph is missing or has the wrong tensor contract, which is a property of the
// build, not a transient failure - so stop paying for preprocessing per photo.
let graphUsable: boolean | undefined;

/**
 * Run single-person MoveNet Lightning on a local image. Every native step is
 * guarded: an unavailable module, unreadable image, or bad tensor returns no
 * pose and album building continues with the other coverage signals.
 *
 * `sourceWidth`/`sourceHeight` let the caller skip a dimension lookup; they only
 * choose the letterbox, so a wrong value costs accuracy, never correctness.
 */
export async function detectBodyPose(
  imageUri: string,
  sourceWidth?: number,
  sourceHeight?: number,
): Promise<MoveNetResult | undefined> {
  if (graphUsable === false) return undefined;

  // Preprocessing is a native resize plus a JPEG decode, and it deliberately
  // runs OUTSIDE the inference queue. It used to sit inside, which serialized
  // the single most expensive step in the wrapper and made the caller's
  // ANALYZE_CONCURRENCY purely decorative: photos queued one behind another for
  // work that has no shared state. Only acquire() + run() need serializing.
  let input: Uint8Array;
  try {
    input = await imageRgbTensor(imageUri, sourceWidth, sourceHeight);
  } catch {
    return undefined;
  }

  const job = inferenceQueue.then(async () => {
    try {
      // Acquired inside the queue: it may retire the previous interpreter, and
      // disposing one while a run is in flight is not safe.
      const model = await modelCache.acquire();
      graphUsable = model !== undefined && isExpectedModel(model);
      if (!model || !graphUsable) return undefined;

      const outputs = await model.run([input.buffer as ArrayBuffer]);
      return parseMoveNetOutput(outputs[0]);
    } catch {
      return undefined;
    }
  });

  inferenceQueue = job.then(
    () => undefined,
    () => undefined,
  );
  return job;
}

async function loadBodyPoseModel(): Promise<TensorflowModel | undefined> {
  try {
    const { loadTensorflowModel } = await import("react-native-fast-tflite");
    // Static require is required so Metro includes the model in the APK.
    const source = await bundledTfliteSource(
      require("../../assets/models/movenet-singlepose-lightning-int8.tflite") as number,
    );
    // The empty delegate list means XNNPACK CPU, and it has to stay empty:
    // fast-tflite 3.0.1 hardcodes GPU delegate options with no serialization
    // dir (kernel recompile on every cold start) and max_delegated_partitions=1,
    // and NNAPI is deprecated on Android 15. See ./README.md#delegates.
    return (await loadTensorflowModel(source, [])) as TensorflowModel;
  } catch {
    return undefined;
  }
}

/**
 * Loads and validates the bundled graph without reading any user photo. Runs on
 * the inference queue because acquiring can retire the previous interpreter.
 */
export async function probeBodyPoseModel(): Promise<boolean> {
  const job = inferenceQueue.then(async () => {
    try {
      const model = await modelCache.acquire();
      graphUsable = model !== undefined && isExpectedModel(model);
      return graphUsable;
    } catch {
      return false;
    }
  });
  inferenceQueue = job.then(
    () => undefined,
    () => undefined,
  );
  return job;
}

/**
 * Tensor contract, read straight out of the bundled flatbuffer:
 *   input  serving_default_input:0  UINT8   [1,192,192,3]  no quantization record
 *   output StatefulPartitionedCall:0 FLOAT32 [1,1,17,3]
 *
 * The int8 build quantizes its WEIGHTS, not its input: the input tensor carries
 * no scale/zero-point, so the identity mapping this file uses (raw 0..255 RGB,
 * NHWC, no normalization) is the right one. Do not divide by 255 here - that
 * would hand the graph a tensor of zeros and ones and still return a plausible
 * skeleton. The float32 output needs no dequantization either.
 */
function isExpectedModel(model: TensorflowModel): boolean {
  const input = model.inputs[0];
  const output = model.outputs[0];
  return (
    input?.dataType === "uint8" &&
    input.shape.join("x") === `1x${INPUT_SIZE}x${INPUT_SIZE}x3` &&
    output?.dataType === "float32" &&
    output.shape.reduce((product, value) => product * value, 1) === OUTPUT_VALUES
  );
}

async function resolveDimensions(
  imageUri: string,
  width: number | undefined,
  height: number | undefined,
): Promise<{ width: number; height: number } | undefined> {
  if (positive(width) && positive(height)) return { width, height };
  try {
    // Header-only measurement. Never manipulate the source just to size it:
    // expo-image-manipulator has no subsampling hint, so any pass over an
    // original decodes the whole frame into the Java heap.
    const { Image } = await import("react-native");
    const measured = await Image.getSize(imageUri);
    return positive(measured?.width) && positive(measured?.height)
      ? { width: measured.width, height: measured.height }
      : undefined;
  } catch {
    return undefined;
  }
}

async function imageRgbTensor(
  imageUri: string,
  sourceWidth?: number,
  sourceHeight?: number,
): Promise<Uint8Array> {
  const [{ manipulateAsync, SaveFormat }, { decode: decodeJpeg }] =
    await Promise.all([
      import("expo-image-manipulator"),
      import("jpeg-js"),
    ]);
  const dimensions = await resolveDimensions(
    imageUri,
    sourceWidth,
    sourceHeight,
  );
  // Without dimensions the letterbox degenerates to the old square resize. That
  // distorts the pose, but a missing pose is worse than a distorted one.
  const layout = dimensions
    ? letterboxLayout(dimensions.width, dimensions.height)
    : { drawWidth: INPUT_SIZE, drawHeight: INPUT_SIZE };

  let outputUri: string | undefined;
  try {
    const resized = await manipulateAsync(
      imageUri,
      [{ resize: { width: layout.drawWidth, height: layout.drawHeight } }],
      {
        base64: true,
        compress: 0.92,
        format: SaveFormat.JPEG,
      },
    );
    outputUri = resized.uri;
    if (!resized.base64) {
      throw new Error("MoveNet preprocessing returned no pixels.");
    }

    const decoded = decodeJpeg(decodeBase64(resized.base64), {
      useTArray: true,
      formatAsRGBA: true,
      tolerantDecoding: true,
      maxResolutionInMP: 1,
      maxMemoryUsageInMB: 16,
    });
    if (
      decoded.width < 1 ||
      decoded.height < 1 ||
      decoded.width > INPUT_SIZE ||
      decoded.height > INPUT_SIZE
    ) {
      throw new Error("MoveNet preprocessing returned the wrong image size.");
    }
    return letterboxRgbaToRgb(decoded.data, decoded.width, decoded.height);
  } finally {
    // saveAsync always writes a cache file, base64 or not. Nothing else ever
    // reads this one, so leaving it behind grows the cache by one JPEG per
    // photo, forever.
    if (outputUri) await deleteManipulatorOutput(outputUri);
  }
}

async function deleteManipulatorOutput(uri: string): Promise<void> {
  try {
    const fileSystem = await import("expo-file-system/legacy");
    await fileSystem.deleteAsync(uri, { idempotent: true });
  } catch {
    // Best-effort cache cleanup; inference must stay fail-neutral.
  }
}

/**
 * How large the frame is drawn inside MoveNet's square input.
 *
 * MoveNet's documented preprocessing is "resize with pad", not a square squash.
 * The squash this replaces was a NON-uniform scale, and pose.ts reads the output
 * as joint ANGLES: a non-uniform scale changes every angle, and changes it by a
 * different amount for a portrait frame than for a landscape one, so one body in
 * two crops clustered as two different poses. Uniform scale plus centring is a
 * similarity transform, and angles are invariant under those - which is also why
 * the keypoints need no un-padding afterwards.
 */
export function letterboxLayout(
  width: number,
  height: number,
  size = INPUT_SIZE,
): { drawWidth: number; drawHeight: number } {
  if (!positive(width) || !positive(height) || !positive(size)) {
    throw new Error("Letterbox dimensions must be finite and positive.");
  }
  const scale = Math.min(size / width, size / height);
  return {
    drawWidth: Math.max(1, Math.min(size, Math.round(width * scale))),
    drawHeight: Math.max(1, Math.min(size, Math.round(height * scale))),
  };
}

/**
 * Drop alpha and centre the decoded frame in a `size` x `size` RGB tensor,
 * leaving the unused border at zero (the same black padding
 * `tf.image.resize_with_pad` produces). Offsets come from the DECODED size, not
 * the requested one, so an encoder that rounds a dimension cannot shear the row
 * stride.
 */
export function letterboxRgbaToRgb(
  rgba: Uint8Array,
  width: number,
  height: number,
  size = INPUT_SIZE,
): Uint8Array {
  if (
    !Number.isInteger(width) ||
    !Number.isInteger(height) ||
    width < 1 ||
    height < 1 ||
    rgba.length < width * height * 4
  ) {
    throw new Error("RGBA buffer is incomplete.");
  }

  const rgb = new Uint8Array(size * size * 3);
  const drawWidth = Math.min(width, size);
  const drawHeight = Math.min(height, size);
  const offsetX = Math.floor((size - drawWidth) / 2);
  const offsetY = Math.floor((size - drawHeight) / 2);

  for (let y = 0; y < drawHeight; y += 1) {
    let source = y * width * 4;
    let target = ((y + offsetY) * size + offsetX) * 3;
    for (let x = 0; x < drawWidth; x += 1) {
      rgb[target] = rgba[source];
      rgb[target + 1] = rgba[source + 1];
      rgb[target + 2] = rgba[source + 2];
      source += 4;
      target += 3;
    }
  }
  return rgb;
}

function positive(value: number | undefined): value is number {
  return typeof value === "number" && Number.isFinite(value) && value > 0;
}

export function parseMoveNetOutput(
  output: ArrayBuffer | undefined,
): MoveNetResult | undefined {
  if (
    !output ||
    output.byteLength < OUTPUT_VALUES * Float32Array.BYTES_PER_ELEMENT
  ) {
    return undefined;
  }
  const values = new Float32Array(output, 0, OUTPUT_VALUES);
  const keypoints: PoseKeypoint[] = [];
  const scores: number[] = [];

  for (let index = 0; index < KEYPOINT_COUNT; index += 1) {
    const offset = index * 3;
    const y = values[offset];
    const x = values[offset + 1];
    const score = values[offset + 2];
    if (![x, y, score].every(Number.isFinite)) return undefined;
    keypoints.push([clamp01(x), clamp01(y)]);
    scores.push(clamp01(score));
  }
  return { keypoints, scores };
}

function decodeBase64(value: string): Uint8Array {
  const encoded = value.replace(/^data:[^,]*,/u, "").replace(/\s/gu, "");
  const padding = encoded.endsWith("==") ? 2 : encoded.endsWith("=") ? 1 : 0;
  const bytes = new Uint8Array(
    Math.max(0, Math.floor((encoded.length * 3) / 4) - padding),
  );
  let accumulator = 0;
  let availableBits = 0;
  let byteIndex = 0;

  for (const character of encoded) {
    if (character === "=") break;
    const digit = BASE64_ALPHABET.indexOf(character);
    if (digit < 0) throw new Error("Invalid base64 image data.");
    accumulator = (accumulator << 6) | digit;
    availableBits += 6;
    if (availableBits >= 8) {
      availableBits -= 8;
      bytes[byteIndex++] = (accumulator >>> availableBits) & 0xff;
      accumulator &= availableBits === 0 ? 0 : (1 << availableBits) - 1;
    }
  }
  if (byteIndex !== bytes.length || bytes.length === 0) {
    throw new Error("Incomplete base64 image data.");
  }
  return bytes;
}

function clamp01(value: number): number {
  return Math.max(0, Math.min(1, value));
}
