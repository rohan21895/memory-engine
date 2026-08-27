// @ts-expect-error Node's TypeScript runner requires the source extension.
import { bundledTfliteSource } from "./bundled-tflite.ts";
// @ts-expect-error Node's TypeScript runner requires the source extension.
import { createModelCache, type ModelCacheLoadStats, type ModelExecutionTimingRecorder } from "./model-cache.ts";

const INPUT_SIZE = 224;
const EMBEDDING_SIZE = 512;
const BASE64_ALPHABET =
  "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
/**
 * OpenAI CLIP's published image statistics, which TinyCLIP inherits along with
 * the rest of the CLIP preprocessing recipe. Applied to RGB (not BGR) channels
 * scaled to 0..1 first, then packed NHWC to match the bundled graph's
 * float32 [1,224,224,3] input (onnx2tf transposed the ONNX NCHW export).
 */
const IMAGE_MEAN = [0.48145466, 0.4578275, 0.40821073] as const;
const IMAGE_STD = [0.26862954, 0.26130258, 0.27577711] as const;
/**
 * The checkpoint the offline text axes were embedded from. The axes are only
 * meaningful against the image tower of the SAME checkpoint - two CLIP variants
 * do not share an embedding space, and comparing across them returns confident
 * nonsense rather than an error. Swapping the image graph without regenerating
 * the sidecar must fail loudly here.
 */
const TEXT_AXIS_MODEL = "TinyCLIP-ViT-8M-16-Text-3M-YFCC15M";

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
// undefined until the first acquire has reported. `false` means the bundled
// graph is missing or has the wrong tensor contract, which is a property of the
// build, not a transient failure - so stop paying for preprocessing per photo.
let graphUsable: boolean | undefined;

/**
 * Produce a semantic image embedding and pre-declared zero-shot contrasts.
 * Native loading, image decoding, tensor execution, and output parsing are all
 * guarded so the caller can retain its perceptual fallback on any failure.
 */
export async function analyzeSemanticImage(
  imageUri: string,
  sourceWidth?: number,
  sourceHeight?: number,
  timing?: ModelExecutionTimingRecorder,
): Promise<SemanticSignals | undefined> {
  if (graphUsable === false) return undefined;

  // Preprocessing is a native resize/crop plus a JPEG decode, and it
  // deliberately runs OUTSIDE the inference queue. It used to sit inside, which
  // serialized the single most expensive step in the wrapper and made the
  // caller's ANALYZE_CONCURRENCY purely decorative: photos queued one behind
  // another for work that has no shared state. Only acquire() + run() need
  // serializing, because acquire() can dispose the previous interpreter.
  let input: Float32Array;
  try {
    input = await imageFloatTensor(imageUri, sourceWidth, sourceHeight);
  } catch {
    return undefined;
  }

  const job = inferenceQueue.then(async () => {
    try {
      const acquired = await modelCache.acquireWithInfo();
      if (acquired.load) {
        try {
          timing?.recordModelLoad(acquired.load);
        } catch {
          // Performance reporting must never fail inference.
        }
      }
      const model = acquired.model;
      graphUsable = model !== undefined && isExpectedModel(model);
      if (!model || !graphUsable) return undefined;
      const inferenceStartedAt = Date.now();
      let outputs: ArrayBuffer[] | undefined;
      try {
        outputs = await model.run([input.buffer as ArrayBuffer]);
      } finally {
        try {
          timing?.recordInference(Date.now() - inferenceStartedAt);
        } catch {
          // Performance reporting must never fail inference.
        }
      }
      if (!outputs) return undefined;
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

export function semanticModelLoadStats(): ModelCacheLoadStats {
  return modelCache.loadStats();
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

async function measureDimensions(
  imageUri: string,
): Promise<{ width: number; height: number } | undefined> {
  try {
    // Header-only measurement. This replaces a `manipulateAsync(uri, [],
    // {compress: 1})` round trip whose ONLY purpose was reading width/height:
    // expo-image-manipulator has no subsampling hint (Glide loads at
    // SIZE_ORIGINAL), so that call decoded the full frame into the Java heap,
    // re-encoded it at quality 1.0, and left the copy in the cache forever.
    const { Image } = await import("react-native");
    const measured = await Image.getSize(imageUri);
    const width = validDimension(measured?.width);
    const height = validDimension(measured?.height);
    return width && height ? { width, height } : undefined;
  } catch {
    return undefined;
  }
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
  if (!width || !height) {
    const measured = await measureDimensions(imageUri);
    width = measured?.width;
    height = measured?.height;
  }
  if (!width || !height) throw new Error("TinyCLIP image dimensions are missing.");

  const transform = centerCropTransform(width, height, INPUT_SIZE);
  let outputUri: string | undefined;
  try {
    const thumbnail = await manipulateAsync(
      imageUri,
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
    outputUri = thumbnail.uri;
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
  // Checked here, where the BUNDLED sidecar is read, rather than in the
  // injectable function below: this is the only place the pairing between the
  // shipped image graph and the shipped text axes is actually asserted.
  if (axes.model !== TEXT_AXIS_MODEL) {
    throw new Error(
      `TinyCLIP text axes were embedded from ${axes.model}, not ${TEXT_AXIS_MODEL}.`,
    );
  }
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
