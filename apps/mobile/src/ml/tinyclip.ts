// @ts-expect-error Node's TypeScript runner requires the source extension.
import { bundledTfliteSource } from "./bundled-tflite.ts";
// @ts-expect-error Node's TypeScript runner requires the source extension.
import { createModelCache } from "./model-cache.ts";

const INPUT_SIZE = 224;
const EMBEDDING_SIZE = 512;
const BASE64_ALPHABET =
  "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
const IMAGE_MEAN = [0.48145466, 0.4578275, 0.40821073] as const;
const IMAGE_STD = [0.26862954, 0.26130258, 0.27577711] as const;

export const SEMANTIC_SCREENSHOT_THRESHOLD = 0.06;

type AxisName =
  | "aesthetic"
  | "composed"
  | "clean_frame"
  | "sleeping"
  | "embrace_context"
  | "screenshot_document";

export type TextAxes = {
  model: string;
  embeddingSize: number;
  axes: Record<AxisName, { positive: number[]; negative: number[] }>;
};

export type SemanticSignals = {
  embedding: number[];
  aesthetic: number;
  composed: number;
  cleanFrame: number;
  sleeping: number;
  awake: number;
  embraceContext: number;
  screenshotDocument: number;
};

type TensorflowModel = {
  inputs: Array<{ dataType: string; shape: number[] }>;
  outputs: Array<{ dataType: string; shape: number[] }>;
  run(inputs: ArrayBuffer[]): Promise<ArrayBuffer[]>;
};

const modelCache = createModelCache<TensorflowModel>(loadSemanticModel);
let inferenceQueue: Promise<void> = Promise.resolve();

/**
 * Produce a semantic image embedding and pre-declared zero-shot contrasts.
 * Native loading, image decoding, tensor execution, and output parsing are all
 * guarded so the caller can retain its perceptual fallback on any failure.
 */
export async function analyzeSemanticImage(
  imageUri: string,
  sourceWidth?: number,
  sourceHeight?: number,
): Promise<SemanticSignals | undefined> {
  const job = inferenceQueue.then(async () => {
    try {
      // Acquired inside the queue: it may retire the previous interpreter, and
      // disposing one while a run is in flight is not safe.
      const model = await modelCache.acquire();
      if (!model || !isExpectedModel(model)) return undefined;
      const input = await imageFloatTensor(imageUri, sourceWidth, sourceHeight);
      const outputs = await model.run([input.buffer as ArrayBuffer]);
      const embedding = parseEmbeddingOutput(outputs[0]);
      return embedding ? semanticSignals(embedding) : undefined;
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

async function loadSemanticModel(): Promise<TensorflowModel | undefined> {
  try {
    const { loadTensorflowModel } = await import("react-native-fast-tflite");
    const source = await bundledTfliteSource(
      require("../../assets/models/tinyclip-vit-8m16-image-float32.tflite") as number,
    );
    // The empty delegate list means XNNPACK CPU, and it has to stay empty:
    // fast-tflite 3.0.1 hardcodes GPU delegate options with no serialization
    // dir (kernel recompile on every cold start) and max_delegated_partitions=1,
    // its GPU path has an open batch-mismatch bug on ViT graphs like this one,
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
export async function probeSemanticModel(): Promise<boolean> {
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
    input?.dataType === "float32" &&
    input.shape.join("x") === `1x${INPUT_SIZE}x${INPUT_SIZE}x3` &&
    output?.dataType === "float32" &&
    output.shape.reduce((product, value) => product * value, 1) ===
      EMBEDDING_SIZE
  );
}

async function imageFloatTensor(
  imageUri: string,
  sourceWidth?: number,
  sourceHeight?: number,
): Promise<Float32Array> {
  const [{ manipulateAsync, SaveFormat }, { decode: decodeJpeg }] =
    await Promise.all([
      import("expo-image-manipulator"),
      import("jpeg-js"),
    ]);

  let width = validDimension(sourceWidth);
  let height = validDimension(sourceHeight);
  let uri = imageUri;
  if (!width || !height) {
    const measured = await manipulateAsync(imageUri, [], {
      compress: 1,
      format: SaveFormat.JPEG,
    });
    width = validDimension(measured.width);
    height = validDimension(measured.height);
    uri = measured.uri;
  }
  if (!width || !height) throw new Error("TinyCLIP image dimensions are missing.");

  const transform = centerCropTransform(width, height, INPUT_SIZE);
  const thumbnail = await manipulateAsync(
    uri,
    [
      { resize: transform.resize },
      {
        crop: {
          originX: transform.originX,
          originY: transform.originY,
          width: INPUT_SIZE,
          height: INPUT_SIZE,
        },
      },
    ],
    {
      base64: true,
      compress: 0.95,
      format: SaveFormat.JPEG,
    },
  );
  if (!thumbnail.base64) throw new Error("TinyCLIP preprocessing returned no pixels.");

  const decoded = decodeJpeg(decodeBase64(thumbnail.base64), {
    useTArray: true,
    formatAsRGBA: true,
    tolerantDecoding: true,
    maxResolutionInMP: 1,
    maxMemoryUsageInMB: 16,
  });
  if (decoded.width !== INPUT_SIZE || decoded.height !== INPUT_SIZE) {
    throw new Error("TinyCLIP preprocessing returned the wrong image size.");
  }
  return normalizeClipPixels(decoded.data, decoded.width, decoded.height);
}

export function centerCropTransform(
  width: number,
  height: number,
  size = INPUT_SIZE,
): {
  resize: { width?: number; height?: number };
  originX: number;
  originY: number;
} {
  if (width < 1 || height < 1 || size < 1) {
    throw new Error("Center-crop dimensions must be positive.");
  }
  if (width <= height) {
    const resizedHeight = Math.max(size, Math.round((height * size) / width));
    return {
      resize: { width: size },
      originX: 0,
      originY: Math.floor((resizedHeight - size) / 2),
    };
  }
  const resizedWidth = Math.max(size, Math.round((width * size) / height));
  return {
    resize: { height: size },
    originX: Math.floor((resizedWidth - size) / 2),
    originY: 0,
  };
}

export function normalizeClipPixels(
  rgba: Uint8Array,
  width: number,
  height: number,
): Float32Array {
  const pixelCount = width * height;
  if (width < 1 || height < 1 || rgba.length < pixelCount * 4) {
    throw new Error("RGBA buffer is incomplete.");
  }
  const values = new Float32Array(pixelCount * 3);
  for (let pixel = 0; pixel < pixelCount; pixel += 1) {
    for (let channel = 0; channel < 3; channel += 1) {
      values[pixel * 3 + channel] =
        (rgba[pixel * 4 + channel] / 255 - IMAGE_MEAN[channel]) /
        IMAGE_STD[channel];
    }
  }
  return values;
}

export function parseEmbeddingOutput(
  output: ArrayBuffer | undefined,
): number[] | undefined {
  if (!output || output.byteLength < EMBEDDING_SIZE * Float32Array.BYTES_PER_ELEMENT) {
    return undefined;
  }
  const values = Array.from(new Float32Array(output, 0, EMBEDDING_SIZE));
  if (values.some((value) => !Number.isFinite(value))) return undefined;
  return normalizeVector(values);
}

export function semanticSignals(embedding: number[]): SemanticSignals {
  const axes = require("../../assets/models/tinyclip-text-axes.json") as TextAxes;
  return semanticSignalsWithAxes(embedding, axes);
}

export function semanticSignalsWithAxes(
  embedding: number[],
  axes: TextAxes,
): SemanticSignals {
  if (embedding.length !== EMBEDDING_SIZE) {
    throw new Error(`TinyCLIP embedding must contain ${EMBEDDING_SIZE} values.`);
  }
  if (axes.embeddingSize !== EMBEDDING_SIZE) {
    throw new Error("TinyCLIP text-axis dimensions do not match the image model.");
  }
  const contrast = (name: AxisName) =>
    cosine(embedding, axes.axes[name].positive) -
    cosine(embedding, axes.axes[name].negative);
  const sleeping = contrast("sleeping");
  return {
    embedding,
    aesthetic: contrast("aesthetic"),
    composed: contrast("composed"),
    cleanFrame: contrast("clean_frame"),
    sleeping,
    awake: -sleeping,
    embraceContext: contrast("embrace_context"),
    screenshotDocument: contrast("screenshot_document"),
  };
}

function cosine(left: number[], right: number[]): number {
  if (left.length !== right.length || left.length === 0) {
    throw new Error("TinyCLIP cosine dimensions do not match.");
  }
  let dot = 0;
  let leftMagnitude = 0;
  let rightMagnitude = 0;
  for (let index = 0; index < left.length; index += 1) {
    dot += left[index] * right[index];
    leftMagnitude += left[index] * left[index];
    rightMagnitude += right[index] * right[index];
  }
  const denominator = Math.sqrt(leftMagnitude * rightMagnitude);
  return denominator > Number.EPSILON ? dot / denominator : 0;
}

function normalizeVector(values: number[]): number[] | undefined {
  const magnitude = Math.sqrt(
    values.reduce((sum, value) => sum + value * value, 0),
  );
  if (!Number.isFinite(magnitude) || magnitude <= Number.EPSILON) return undefined;
  return values.map((value) => value / magnitude);
}

function validDimension(value: number | undefined): number | undefined {
  return typeof value === "number" && Number.isFinite(value) && value > 0
    ? value
    : undefined;
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
