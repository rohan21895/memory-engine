import type { SharedRef } from "expo-modules-core/types";
import { decode as decodeJpeg } from "jpeg-js";

import type { FaceBox } from "../faces/face-detector";
// @ts-expect-error Node's TypeScript runner requires the source extension.
import { traceScanStage } from "../faces/face-detector.ts";
// @ts-expect-error Node's TypeScript runner requires the source extension.
import { bundledTfliteSource } from "./bundled-tflite.ts";
// @ts-expect-error Node's TypeScript runner requires the source extension.
import { alignDecodedPatch, patchCropRect, patchGeometry, PATCH_SIZE } from "./face-crop.ts";
// @ts-expect-error Node's TypeScript runner requires the source extension.
import { createModelCache } from "./model-cache.ts";

const INPUT_SIZE = 112;
const EMBEDDING_SIZE = 192;
const FACE_PADDING_SCALE = 1.3;
const BASE64_ALPHABET =
  "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";

/**
 * Reverse base64 table.
 *
 * The decoder below used to do `BASE64_ALPHABET.indexOf(character)` per
 * character while iterating the string with for..of. On a 256x256 face patch
 * that is a ~30,000 character string, a code-point iterator, and a linear scan
 * of 64 characters per byte — about two million comparisons per FACE, in JS, on
 * the same thread that has to stay responsive. A 128-entry table is one lookup.
 */
const BASE64_VALUES = (() => {
  const table = new Int8Array(128).fill(-1);
  for (let index = 0; index < BASE64_ALPHABET.length; index += 1) {
    table[BASE64_ALPHABET.charCodeAt(index)] = index;
  }
  return table;
})();

export type FaceImageAsset = {
  width: number;
  height: number;
};

/**
 * Either a URI, or an already-decoded bitmap shared with the rest of the scan.
 * Passing the bitmap is what removes the per-face full-resolution decode.
 */
export type FaceImageSource = string | SharedRef<"image">;

type TensorflowModel = {
  inputs: Array<{ dataType: string; shape: number[] }>;
  outputs: Array<{ dataType: string; shape: number[] }>;
  run(inputs: ArrayBuffer[]): Promise<ArrayBuffer[]>;
};

const modelCache = createModelCache<TensorflowModel>(loadFaceIdentityModel);
let inferenceQueue: Promise<void> = Promise.resolve();
let loadDiagnosticWritten = false;
let inferenceDiagnosticWritten = false;

function tensorSummary(model: TensorflowModel): string {
  const describe = (tensor: { dataType: string; shape: number[] }) =>
    `${tensor.dataType}[${tensor.shape.join("x")}]`;
  return `inputs=${model.inputs.map(describe).join(",")} outputs=${model.outputs.map(describe).join(",")}`;
}

function safeErrorMessage(error: unknown): string {
  const message = error instanceof Error ? error.message : String(error);
  return message.replace(/(?:content|file):\/\/\S+/gu, "<local-uri>").slice(0, 240);
}

/**
 * Crops one ML Kit face and returns its L2-normalized MobileFaceNet identity
 * embedding. Model loading, image manipulation, decoding, and inference are
 * lazy and guarded; callers receive undefined and can use a neutral fallback.
 */
export async function embedFaceIdentity(
  asset: FaceImageAsset,
  imageUri: FaceImageSource,
  box: FaceBox,
): Promise<number[] | undefined> {
  if (
    box.headEulerAngleY !== undefined &&
    Math.abs(box.headEulerAngleY) > 45
  ) {
    return undefined;
  }
  // The queue stays, and preprocessing stays inside it, but the reason has
  // changed. It used to be a memory bound: every crop decoded the whole
  // original, so serializing was what stopped a photo with eight faces asking
  // for eight full-resolution bitmaps at once. Callers now pass the shared
  // frame bitmap (see openFaceFrame), so a crop allocates a few hundred KB and
  // that bound is gone. What remains is correctness: there is exactly ONE
  // TFLite interpreter, model-cache.ts retires and disposes it underneath us
  // every RUNS_PER_MODEL inferences, and `acquire()` may only be called when no
  // run is in flight. Removing the queue would race a dispose against a run.
  const job = inferenceQueue.then(async () => {
    try {
      // Acquired inside the queue: it may retire the previous interpreter, and
      // disposing one while a run is in flight is not safe.
      const model = await modelCache.acquire();
      if (!model || !isExpectedModel(model)) {
        if (!inferenceDiagnosticWritten) {
          inferenceDiagnosticWritten = true;
          console.warn("[PhoteoFaceNet] identity inference unavailable; using perceptual fallback");
        }
        return undefined;
      }
      const preprocessStartedAt = Date.now();
      const input = await faceFloatTensor(asset, imageUri, box);
      traceScanStage("prep", preprocessStartedAt);
      const inferenceStartedAt = Date.now();
      const outputs = await model.run([input.buffer as ArrayBuffer]);
      traceScanStage("tflite", inferenceStartedAt);
      const embedding = parseFaceEmbeddingOutput(outputs[0]);
      if (!inferenceDiagnosticWritten) {
        inferenceDiagnosticWritten = true;
        console.warn(`[PhoteoFaceNet] identity inference ${embedding ? "active" : "returned invalid output"}`);
      }
      return embedding;
    } catch (error) {
      if (!inferenceDiagnosticWritten) {
        inferenceDiagnosticWritten = true;
        console.warn(`[PhoteoFaceNet] inference failed: ${safeErrorMessage(error)}`);
      }
      return undefined;
    }
  });

  inferenceQueue = job.then(
    () => undefined,
    () => undefined,
  );
  return job;
}

async function loadFaceIdentityModel(): Promise<TensorflowModel | undefined> {
  try {
    const { loadTensorflowModel } = await import("react-native-fast-tflite");
    // Static require is required so Metro packages the graph in the APK.
    const source = await bundledTfliteSource(
      require("../../assets/models/mobilefacenet-192-float32.tflite") as number,
    );
    // The empty delegate list means XNNPACK CPU, and it has to stay empty:
    // fast-tflite 3.0.1 hardcodes GPU delegate options with no serialization
    // dir (kernel recompile on every cold start) and max_delegated_partitions=1,
    // and NNAPI is deprecated on Android 15. See ./README.md#delegates.
    const model = (await loadTensorflowModel(source, [])) as TensorflowModel;
    if (!loadDiagnosticWritten) {
      loadDiagnosticWritten = true;
      console.warn(`[PhoteoFaceNet] loaded ${tensorSummary(model)} expected=${isExpectedModel(model)}`);
    }
    return model;
  } catch (error) {
    if (!loadDiagnosticWritten) {
      loadDiagnosticWritten = true;
      console.warn(`[PhoteoFaceNet] load failed: ${safeErrorMessage(error)}`);
    }
    return undefined;
  }
}

/**
 * Loads and validates the bundled graph without reading any user photo. Runs on
 * the inference queue because acquiring can retire the previous interpreter.
 */
export async function probeFaceIdentityModel(): Promise<boolean> {
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

async function faceFloatTensor(
  asset: FaceImageAsset,
  imageUri: FaceImageSource,
  box: FaceBox,
): Promise<Float32Array> {
  if (box.landmarks) {
    const aligned = await alignedFaceFloatTensor(asset, imageUri, box);
    if (aligned) return aligned;
  }
  return boundingBoxFaceFloatTensor(asset, imageUri, box);
}

/**
 * Crops, scales and JPEG-encodes one square patch, returning its base64 pixels.
 *
 * Uses the contextual manipulator rather than the deprecated `manipulateAsync`
 * for one reason: `manipulate()` accepts an already-decoded bitmap, so when the
 * caller passes the scan's shared frame this whole call is a `createBitmap` and
 * a `createScaledBitmap` with no decode at all. Given a URI it behaves exactly
 * like the old call. The output file is unavoidable — `saveAsync` always writes
 * one — so it is deleted on every path.
 */
async function croppedPatchBase64(
  imageUri: FaceImageSource,
  crop: { originX: number; originY: number; width: number; height: number },
  size: number,
): Promise<string | undefined> {
  const { ImageManipulator, SaveFormat } = await import("expo-image-manipulator");
  const context = ImageManipulator.manipulate(imageUri);
  let faceUri: string | undefined;
  try {
    const rendered = await context
      .crop(crop)
      .resize({ width: size, height: size })
      .renderAsync();
    try {
      const face = await rendered.saveAsync({
        base64: true,
        compress: 0.95,
        format: SaveFormat.JPEG,
      });
      faceUri = face.uri;
      return face.base64;
    } finally {
      rendered.release();
    }
  } finally {
    context.release();
    if (faceUri) await deleteManipulatorOutput(faceUri);
  }
}

async function alignedFaceFloatTensor(
  asset: FaceImageAsset,
  imageUri: FaceImageSource,
  box: FaceBox,
): Promise<Float32Array | undefined> {
  if (!box.landmarks) return undefined;
  const geometry = patchGeometry(asset, box);
  if (!geometry) return undefined;
  try {
    const base64 = await croppedPatchBase64(
      imageUri,
      patchCropRect(geometry),
      PATCH_SIZE,
    );
    if (!base64) return undefined;
    const decoded = decodeFaceJpeg(base64, PATCH_SIZE);
    const aligned = alignDecodedPatch(
      decoded.data,
      decoded.width,
      decoded.height,
      box.landmarks,
      geometry,
    );
    return aligned ? normalizeFaceRgb(aligned, INPUT_SIZE, INPUT_SIZE) : undefined;
  } catch {
    return undefined;
  }
}

async function boundingBoxFaceFloatTensor(
  asset: FaceImageAsset,
  imageUri: FaceImageSource,
  box: FaceBox,
): Promise<Float32Array> {
  const base64 = await croppedPatchBase64(
    imageUri,
    squareFaceCrop(asset, box),
    INPUT_SIZE,
  );
  if (!base64) {
    throw new Error("MobileFaceNet preprocessing returned no pixels.");
  }
  const decoded = decodeFaceJpeg(base64, INPUT_SIZE);
  return normalizeFacePixels(decoded.data, decoded.width, decoded.height);
}

function decodeFaceJpeg(base64: string, expectedSize: number) {
  const decoded = decodeJpeg(decodeBase64(base64), {
    useTArray: true,
    formatAsRGBA: true,
    tolerantDecoding: true,
    maxResolutionInMP: 1,
    maxMemoryUsageInMB: 8,
  });
  if (decoded.width !== expectedSize || decoded.height !== expectedSize) {
    throw new Error("MobileFaceNet preprocessing returned the wrong image size.");
  }
  return decoded;
}

async function deleteManipulatorOutput(uri: string): Promise<void> {
  try {
    const fileSystem = await import("expo-file-system/legacy");
    await fileSystem.deleteAsync(uri, { idempotent: true });
  } catch {
    // Best-effort cache cleanup; inference must stay fail-neutral.
  }
}

/** Makes a padded square crop and shifts it inside the source image bounds. */
export function squareFaceCrop(
  asset: FaceImageAsset,
  box: FaceBox,
  scale = FACE_PADDING_SCALE,
): { originX: number; originY: number; width: number; height: number } {
  const values = [asset.width, asset.height, box.x, box.y, box.width, box.height, scale];
  if (
    values.some((value) => !Number.isFinite(value)) ||
    asset.width < 1 ||
    asset.height < 1 ||
    box.width <= 0 ||
    box.height <= 0 ||
    scale <= 0
  ) {
    throw new Error("Face crop dimensions must be finite and positive.");
  }

  const side = Math.max(
    1,
    Math.min(
      Math.floor(asset.width),
      Math.floor(asset.height),
      Math.ceil(Math.max(box.width, box.height) * scale),
    ),
  );
  const centerX = box.x + box.width / 2;
  const centerY = box.y + box.height / 2;
  const originX = clampInteger(
    Math.round(centerX - side / 2),
    0,
    Math.floor(asset.width) - side,
  );
  const originY = clampInteger(
    Math.round(centerY - side / 2),
    0,
    Math.floor(asset.height) - side,
  );
  return { originX, originY, width: side, height: side };
}

/**
 * Converts RGBA pixels to RGB floats in the ArcFace range (x - 127.5) / 127.5.
 *
 * Tensor contract, read out of the bundled flatbuffer: input `input`
 * FLOAT32[1,112,112,3] (NHWC, annotated with the TOCO input range 0..255),
 * output `embeddings` FLOAT32[1,192]. Channel order is RGB, matching the
 * artifact's own Dart reference implementation (getRed/getGreen/getBlue) — a
 * BGR swap here would still return confident, stable, WRONG identities.
 *
 * That reference uses (x - 128) / 128 and the MobileFaceNet_TF lineage uses
 * (x - 127.5) / 128; this uses (x - 127.5) / 127.5. All three agree to within
 * 0.4% of full scale, which is far below JPEG round-trip noise, and the output
 * is L2-normalized before any cosine comparison. Left as-is deliberately:
 * changing it would silently shift every embedding without invalidating the
 * persisted face index, which is a worse outcome than a 0.4% scale difference.
 */
export function normalizeFacePixels(
  rgba: Uint8Array,
  width: number,
  height: number,
): Float32Array {
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

  const values = new Float32Array(pixelCount * 3);
  for (let pixel = 0; pixel < pixelCount; pixel += 1) {
    const source = pixel * 4;
    const target = pixel * 3;
    values[target] = (rgba[source] - 127.5) / 127.5;
    values[target + 1] = (rgba[source + 1] - 127.5) / 127.5;
    values[target + 2] = (rgba[source + 2] - 127.5) / 127.5;
  }
  return values;
}

/** Converts an already aligned packed RGB image to the ArcFace float range. */
export function normalizeFaceRgb(
  rgb: Uint8Array,
  width: number,
  height: number,
): Float32Array {
  const componentCount = width * height * 3;
  if (
    !Number.isInteger(width) ||
    !Number.isInteger(height) ||
    width < 1 ||
    height < 1 ||
    rgb.length < componentCount
  ) {
    throw new Error("RGB buffer is incomplete.");
  }
  const values = new Float32Array(componentCount);
  for (let index = 0; index < componentCount; index += 1) {
    values[index] = (rgb[index] - 127.5) / 127.5;
  }
  return values;
}

export function parseFaceEmbeddingOutput(
  output: ArrayBuffer | undefined,
): number[] | undefined {
  if (
    !output ||
    output.byteLength < EMBEDDING_SIZE * Float32Array.BYTES_PER_ELEMENT
  ) {
    return undefined;
  }
  const embedding = Array.from(
    new Float32Array(output, 0, EMBEDDING_SIZE),
  );
  if (embedding.some((value) => !Number.isFinite(value))) return undefined;
  const magnitude = Math.sqrt(
    embedding.reduce((sum, value) => sum + value * value, 0),
  );
  if (!Number.isFinite(magnitude) || magnitude <= Number.EPSILON) {
    return undefined;
  }
  return embedding.map((value) => value / magnitude);
}

function clampInteger(value: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, value));
}

/** Table-driven base64 decode. Exported so the self-check can pin it. */
export function decodeBase64(value: string): Uint8Array {
  const encoded = value.replace(/^data:[^,]*,/u, "").replace(/\s/gu, "");
  const padding = encoded.endsWith("==") ? 2 : encoded.endsWith("=") ? 1 : 0;
  const bytes = new Uint8Array(
    Math.max(0, Math.floor((encoded.length * 3) / 4) - padding),
  );
  let accumulator = 0;
  let availableBits = 0;
  let byteIndex = 0;
  for (let index = 0; index < encoded.length; index += 1) {
    const code = encoded.charCodeAt(index);
    if (code === 0x3d) break; // '='
    const digit = code < 128 ? BASE64_VALUES[code] : -1;
    if (digit < 0) throw new Error("Invalid base64 face data.");
    accumulator = (accumulator << 6) | digit;
    availableBits += 6;
    if (availableBits >= 8) {
      availableBits -= 8;
      bytes[byteIndex++] = (accumulator >>> availableBits) & 0xff;
      accumulator &= availableBits === 0 ? 0 : (1 << availableBits) - 1;
    }
  }
  if (byteIndex !== bytes.length || bytes.length === 0) {
    throw new Error("Incomplete base64 face data.");
  }
  return bytes;
}
