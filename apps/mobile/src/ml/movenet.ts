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

/**
 * Run single-person MoveNet Lightning on a local image. Every native step is
 * guarded: an unavailable module, unreadable image, or bad tensor returns no
 * pose and album building continues with the other coverage signals.
 */
export async function detectBodyPose(
  imageUri: string,
): Promise<MoveNetResult | undefined> {
  const job = inferenceQueue.then(async () => {
    try {
      // Acquired inside the queue: it may retire the previous interpreter, and
      // disposing one while a run is in flight is not safe.
      const model = await modelCache.acquire();
      if (!model || !isExpectedModel(model)) return undefined;

      const input = await imageRgbTensor(imageUri);
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
      return model !== undefined && isExpectedModel(model);
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

async function imageRgbTensor(imageUri: string): Promise<Uint8Array> {
  const [{ manipulateAsync, SaveFormat }, { decode: decodeJpeg }] =
    await Promise.all([
      import("expo-image-manipulator"),
      import("jpeg-js"),
    ]);
  const resized = await manipulateAsync(
    imageUri,
    [{ resize: { width: INPUT_SIZE, height: INPUT_SIZE } }],
    {
      base64: true,
      compress: 0.92,
      format: SaveFormat.JPEG,
    },
  );
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
  if (decoded.width !== INPUT_SIZE || decoded.height !== INPUT_SIZE) {
    throw new Error("MoveNet preprocessing returned the wrong image size.");
  }
  return rgbaToRgb(decoded.data, decoded.width, decoded.height);
}

export function rgbaToRgb(
  rgba: Uint8Array,
  width: number,
  height: number,
): Uint8Array {
  const pixelCount = width * height;
  if (
    !Number.isInteger(width) ||
    !Number.isInteger(height) ||
    width < 1 ||
    height < 1 ||
    rgba.length < pixelCount * 4
  ) {
    throw new Error("RGBA buffer is incomplete.");
  }

  const rgb = new Uint8Array(pixelCount * 3);
  for (let pixel = 0; pixel < pixelCount; pixel += 1) {
    const source = pixel * 4;
    const target = pixel * 3;
    rgb[target] = rgba[source];
    rgb[target + 1] = rgba[source + 1];
    rgb[target + 2] = rgba[source + 2];
  }
  return rgb;
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
