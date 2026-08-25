import type { ImageRef } from "expo-image";

import type { FaceLandmarks5, Point } from "../ml/face-align";

const MAX_DETECTION_EDGE = 1280;

export type FaceBox = {
  x: number;
  y: number;
  width: number;
  height: number;
  landmarks?: FaceLandmarks5;
  headEulerAngleX?: number;
  headEulerAngleY?: number;
  headEulerAngleZ?: number;
  // Classification probabilities (0..1) when ML Kit classification is on;
  // undefined when the model didn't report them. Used by selection quality.
  leftEyeOpen?: number;
  rightEyeOpen?: number;
  smiling?: number;
};

export type FaceImageDimensions = { width: number; height: number };

type NativePoint = { x?: unknown; y?: unknown };
type NativeFrame = { origin?: NativePoint; size?: NativePoint };
type NativeFace = {
  frame?: NativeFrame;
  landmarks?: unknown;
  headEulerAngleX?: unknown;
  headEulerAngleY?: unknown;
  headEulerAngleZ?: unknown;
  leftEyeOpenProbability?: unknown;
  rightEyeOpenProbability?: unknown;
  smilingProbability?: unknown;
};
type NativeResult = { faces?: unknown } | unknown[];
type NativeDetector = {
  initialize: (options?: unknown) => unknown;
  detectFaces: (uri: string) => NativeResult | Promise<NativeResult>;
};
type NativeModule = { RNMLKitFaceDetector?: new () => NativeDetector };

/**
 * ONE decode of a photo, shared by every stage of the face scan.
 *
 * The scan used to decode each photo at full resolution once for detection and
 * then twice MORE per detected face (identity crop + thumbnail crop), so a
 * six-face group shot decoded a 12MP frame thirteen times. A frame is opened
 * once per asset, ML Kit reads `uri`, and every crop is taken from `image` —
 * an in-memory bitmap, so a crop costs no decode at all.
 */
export type FaceFrame = {
  /**
   * In-memory bitmap of the frame pixels, when the platform could give one.
   * Undefined for a photo whose drawable is not a plain bitmap (an animated
   * GIF/WebP) and for an original already inside the detection bound; callers
   * must fall back to cropping from `uri`, which holds the very same pixels.
   */
  image: ImageRef | undefined;
  /** file:// JPEG of the frame pixels; ML Kit only accepts a URI. */
  uri: string;
  width: number;
  height: number;
  sourceWidth: number;
  sourceHeight: number;
  /**
   * Frame pixels per source pixel, never above 1. Uniform: the loader preserves
   * aspect ratio, so one factor maps both axes.
   */
  scale: number;
  temporary: boolean;
};

/**
 * Stage timings for the on-device scan, printed to logcat once per batch.
 *
 * Deliberately always on and deliberately coarse: a Date.now() per stage is
 * nothing beside a bitmap decode, and without it the next person to optimize
 * this pipeline is guessing which of proxy/detect/embed/crop/persist owns the
 * per-photo milliseconds.
 */
type StageTotal = { ms: number; count: number };

const scanStages = new Map<string, StageTotal>();

function stageTotal(stage: string): StageTotal {
  const existing = scanStages.get(stage);
  if (existing) return existing;
  const created = { ms: 0, count: 0 };
  scanStages.set(stage, created);
  return created;
}

/** Records one completed stage that began at `startedAt` (Date.now()). */
export function traceScanStage(stage: string, startedAt: number): void {
  const total = stageTotal(stage);
  total.ms += Date.now() - startedAt;
  total.count += 1;
}

/** Records a countable event with no duration (fallbacks, faces, skips). */
export function traceScanCount(stage: string, amount = 1): void {
  stageTotal(stage).count += amount;
}

/** Formats every accumulated stage and clears the totals for the next batch. */
export function takeScanTrace(): string {
  const parts: string[] = [];
  for (const [stage, total] of scanStages) {
    parts.push(
      total.ms > 0
        ? `${stage}=${Math.round(total.ms)}ms/${total.count}`
        : `${stage}=${total.count}`,
    );
  }
  scanStages.clear();
  return parts.join(" ");
}

const DETECTOR_OPTIONS = {
  performanceMode: "accurate",
  landmarkMode: true,
  contourMode: false,
  classificationMode: true,
  minFaceSize: 0.08,
  isTrackingEnabled: false,
};

let moduleLoaded: NativeModule | null | undefined;
let detector: NativeDetector | null = null;
let initPromise: Promise<NativeDetector | null> | null = null;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function finite(value: unknown): number | undefined {
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
}

function probability(value: unknown): number | undefined {
  const result = finite(value);
  return result !== undefined && result >= 0 ? result : undefined;
}

function validDimensions(
  value: FaceImageDimensions | undefined,
): value is FaceImageDimensions {
  return (
    value !== undefined &&
    Number.isFinite(value.width) &&
    Number.isFinite(value.height) &&
    value.width > 0 &&
    value.height > 0
  );
}

function loadModule(): NativeModule | null {
  if (moduleLoaded !== undefined) return moduleLoaded;
  try {
    // Guarded require: beta builds without the native binding degrade to no
    // detections instead of crashing during module evaluation.
    const mod = require(
      "@infinitered/react-native-mlkit-face-detection",
    ) as NativeModule;
    moduleLoaded = typeof mod?.RNMLKitFaceDetector === "function" ? mod : null;
  } catch {
    moduleLoaded = null;
  }
  return moduleLoaded;
}

/** True when the native ML Kit binding is present (initialization stays lazy). */
export function isFaceDetectionAvailable(): boolean {
  return loadModule() !== null;
}

async function ensureDetector(): Promise<NativeDetector | null> {
  if (detector) return detector;
  initPromise ??= (async () => {
    const mod = loadModule();
    if (!mod?.RNMLKitFaceDetector) return null;
    try {
      const instance = new mod.RNMLKitFaceDetector();
      await Promise.resolve(instance.initialize(DETECTOR_OPTIONS));
      detector = instance;
      return detector;
    } catch {
      return null;
    }
  })();
  return initPromise;
}

async function resolveDimensions(
  imageUri: string,
  supplied: FaceImageDimensions | undefined,
): Promise<FaceImageDimensions | undefined> {
  if (validDimensions(supplied)) return supplied;
  try {
    const { Image } = await import("react-native");
    const measured = await Image.getSize(imageUri);
    return validDimensions(measured) ? measured : undefined;
  } catch {
    return undefined;
  }
}

/**
 * Opens the one bounded decode every stage of the scan then shares.
 *
 * `Image.loadAsync` is what makes this cheap: it asks Glide for a bounded
 * bitmap, so the decoder subsamples (a 4032x3024 JPEG requested at 1280 decodes
 * at 2016x1512) and the 12MP bitmap is never materialized at all. The previous
 * `manipulateAsync(uri, [resize])` went through expo-image-loader, which loads
 * at SIZE_ORIGINAL and only then scales down — full decode cost, full heap
 * spike. `src/selection/candidate-quality-probe.ts` already ships this exact
 * pattern for the album pipeline; this is the face scan catching up.
 *
 * Callers MUST pass the result to `closeFaceFrame`, which releases the bitmap
 * and deletes the JPEG.
 */
export async function openFaceFrame(
  imageUri: string,
  source?: FaceImageDimensions,
  maxEdge: number = MAX_DETECTION_EDGE,
): Promise<FaceFrame | null> {
  const dimensions = await resolveDimensions(imageUri, source);
  if (!dimensions) return null;

  // Long/short edges rather than width/height: MediaStore sometimes reports the
  // pre-EXIF-rotation size, and the loader always returns the rotated bitmap.
  // A ratio of long edges is right either way, and cannot silently transpose.
  const sourceLong = Math.max(dimensions.width, dimensions.height);
  if (imageUri.startsWith("file://") && sourceLong <= maxEdge) {
    return {
      image: undefined,
      uri: imageUri,
      sourceWidth: dimensions.width,
      sourceHeight: dimensions.height,
      width: dimensions.width,
      height: dimensions.height,
      scale: 1,
      temporary: false,
    };
  }

  return (
    (await bitmapFaceFrame(imageUri, dimensions, sourceLong, maxEdge)) ??
    (await encodedFaceFrame(imageUri, dimensions, sourceLong))
  );
}

/**
 * Whether the dimensions we BELIEVE the original has agree with the bitmap the
 * loader actually produced.
 *
 * `scale` is computed from long edges, so a transposed MediaStore record cannot
 * corrupt it. But `sourceWidth`/`sourceHeight` are handed to `patchGeometry` as
 * the crop bounds for the full-resolution path (the `smallFaceFullRes` faces),
 * and there a transpose is not survivable: the crop is clamped against
 * 4032x3024 on an image that is really 3024x4032, so it samples the wrong
 * region — while still producing a perfectly valid, invertible alignment
 * transform. That failure is invisible to every counter we have, and an offline
 * reimplementation of this pipeline showed a landmark/coordinate-space mismatch
 * is the ONE fault that reproduces the observed embedding collapse (genuine
 * median equal to impostor median). This counts it instead of assuming it.
 */
const frameOrientation = { agree: 0, transposed: 0, degenerate: 0 };

export function frameOrientationCounts(): Readonly<typeof frameOrientation> {
  return { ...frameOrientation };
}

export function recordFrameOrientation(
  source: FaceImageDimensions,
  frame: { width: number; height: number },
): void {
  const values = [source.width, source.height, frame.width, frame.height];
  // A square frame carries no orientation, so it can neither agree nor disagree.
  if (!values.every((value) => Number.isFinite(value) && value > 0)) {
    frameOrientation.degenerate += 1;
    return;
  }
  if (source.width === source.height || frame.width === frame.height) {
    frameOrientation.agree += 1;
    return;
  }
  const sourcePortrait = source.height > source.width;
  const framePortrait = frame.height > frame.width;
  if (sourcePortrait === framePortrait) frameOrientation.agree += 1;
  else frameOrientation.transposed += 1;
}

/** The fast path: a subsampled decode kept in memory for the whole photo. */
async function bitmapFaceFrame(
  imageUri: string,
  dimensions: FaceImageDimensions,
  sourceLong: number,
  maxEdge: number = MAX_DETECTION_EDGE,
): Promise<FaceFrame | null> {
  let image: ImageRef | undefined;
  try {
    const [{ Image }, { ImageManipulator, SaveFormat }] = await Promise.all([
      import("expo-image"),
      import("expo-image-manipulator"),
    ]);
    image = await Image.loadAsync(imageUri, {
      maxWidth: maxEdge,
      maxHeight: maxEdge,
    });
    // No transformer: the context hands back the loaded bitmap untouched, and
    // only ImageRef can write the file ML Kit needs. `image.width` is a
    // density-scaled LOGICAL size on Android, so the saved result — which
    // reports real bitmap pixels — is the only trustworthy dimension source.
    const context = ImageManipulator.manipulate(image);
    let saved;
    try {
      const rendered = await context.renderAsync();
      try {
        saved = await rendered.saveAsync({
          compress: 0.88,
          format: SaveFormat.JPEG,
        });
      } finally {
        rendered.release();
      }
    } finally {
      context.release();
    }
    const frameLong = Math.max(saved.width, saved.height);
    recordFrameOrientation(dimensions, saved);
    return {
      image,
      uri: saved.uri,
      sourceWidth: dimensions.width,
      sourceHeight: dimensions.height,
      width: saved.width,
      height: saved.height,
      scale: sourceLong > 0 ? Math.min(1, frameLong / sourceLong) : 1,
      temporary: true,
    };
  } catch (error) {
    try {
      image?.release();
    } catch {
      // Releasing a bitmap that never loaded is not an error worth reporting.
    }
    // One-shot: the failure that matters here is systematic. Per-photo failures
    // (an animated GIF has no plain bitmap to hand out) fall through to the
    // encoded frame below and lose speed, not faces.
    if (!frameDiagnosticWritten) {
      frameDiagnosticWritten = true;
      console.warn(
        `[PhoteoFaceFrame] bitmap frame unavailable, falling back to an encoded frame: ${safeFrameError(error)}`,
      );
    }
    return null;
  }
}

/**
 * The compatibility path, and what the scan used to do for every photo: decode
 * the original at full size and write a bounded JPEG. Slower and heavier, but
 * every stage still shares this ONE decode instead of repeating it per face, so
 * even here a group photo no longer costs thirteen full decodes.
 */
async function encodedFaceFrame(
  imageUri: string,
  dimensions: FaceImageDimensions,
  sourceLong: number,
): Promise<FaceFrame | null> {
  try {
    const imageManipulator = await import("expo-image-manipulator");
    const ratio = Math.min(1, MAX_DETECTION_EDGE / sourceLong);
    const output = await imageManipulator.manipulateAsync(
      imageUri,
      [
        {
          resize: {
            width: Math.max(1, Math.round(dimensions.width * ratio)),
            height: Math.max(1, Math.round(dimensions.height * ratio)),
          },
        },
      ],
      { compress: 0.88, format: imageManipulator.SaveFormat.JPEG },
    );
    return {
      image: undefined,
      uri: output.uri,
      sourceWidth: dimensions.width,
      sourceHeight: dimensions.height,
      width: output.width,
      height: output.height,
      scale: Math.min(1, Math.max(output.width, output.height) / sourceLong),
      temporary: output.uri !== imageUri,
    };
  } catch {
    return null;
  }
}

let frameDiagnosticWritten = false;

function safeFrameError(error: unknown): string {
  const message = error instanceof Error ? error.message : String(error);
  return message.replace(/(?:content|file):\/\/\S+/gu, "<local-uri>").slice(0, 200);
}

/** Frees the shared bitmap and the ML Kit JPEG. Never throws. */
export async function closeFaceFrame(frame: FaceFrame | null): Promise<void> {
  if (!frame) return;
  try {
    frame.image?.release();
  } catch {
    // A double release must not fail the asset that owns the frame.
  }
  if (!frame.temporary) return;
  await deleteImageFile(frame.uri);
}

/** Best-effort removal of a manipulator/save output. Never throws. */
export async function deleteImageFile(uri: string): Promise<void> {
  try {
    const fileSystem = await import("expo-file-system/legacy");
    await fileSystem.deleteAsync(uri, { idempotent: true });
  } catch {
    // Cache cleanup is best-effort and must never crash detection.
  }
}

/**
 * Rescales a source-space box, with its landmarks, into another image scale.
 *
 * Every downstream crop is scale-covariant: `patchGeometry` derives its origin
 * and size from the box and its patch `scale` from `PATCH_SIZE / size`, so
 * multiplying the image dimensions, the box AND the landmarks by one factor
 * leaves the patch-space landmarks — and therefore the alignment — unchanged.
 * Scaling only some of them silently misaligns every face, which looks like
 * nothing and destroys identity quality; see face-detector.test.ts.
 */
export function scaleFaceBox(box: FaceBox, scale: number): FaceBox {
  if (!Number.isFinite(scale) || scale <= 0 || scale === 1) return box;
  const point = (value: Point): Point => ({
    x: value.x * scale,
    y: value.y * scale,
  });
  const landmarks = box.landmarks;
  return {
    ...box,
    x: box.x * scale,
    y: box.y * scale,
    width: box.width * scale,
    height: box.height * scale,
    ...(landmarks
      ? {
          landmarks: {
            leftEye: point(landmarks.leftEye),
            rightEye: point(landmarks.rightEye),
            // Absent corners stay absent: scaling a missing point would invent
            // a landmark at the origin and drag the alignment to one corner.
            ...(landmarks.leftMouth
              ? { leftMouth: point(landmarks.leftMouth) }
              : {}),
            ...(landmarks.rightMouth
              ? { rightMouth: point(landmarks.rightMouth) }
              : {}),
            ...(landmarks.noseBase
              ? { noseBase: point(landmarks.noseBase) }
              : {}),
          },
        }
      : {}),
  };
}

function facesFrom(result: NativeResult): NativeFace[] {
  if (Array.isArray(result)) return result as NativeFace[];
  return isRecord(result) && Array.isArray(result.faces)
    ? (result.faces as NativeFace[])
    : [];
}

type LandmarkName = keyof FaceLandmarks5;

/**
 * Why faces lose their landmarks, so a library on the unaligned path is
 * visible in logcat instead of merely being mysteriously bad at telling people
 * apart.
 */
const landmarkRejects = { noEyes: 0, eyesOnly: 0, eyesAndNose: 0, full: 0 };

export function landmarkRejectCounts(): Readonly<typeof landmarkRejects> {
  return { ...landmarkRejects };
}

function landmarkName(type: unknown): LandmarkName | undefined {
  if (typeof type !== "string" && typeof type !== "number") return undefined;
  switch (String(type)) {
    case "4":
    case "LeftEye":
    case "leftEye":
      return "leftEye";
    case "10":
    case "RightEye":
    case "rightEye":
      return "rightEye";
    case "6":
    case "NoseBase":
    case "noseBase":
      return "noseBase";
    case "5":
    case "LeftMouth":
    case "leftMouth":
      return "leftMouth";
    case "11":
    case "RightMouth":
    case "rightMouth":
      return "rightMouth";
    default:
      return undefined;
  }
}

function scaledPoint(
  value: NativePoint | null | undefined,
  scaleX: number,
  scaleY: number,
): Point | undefined {
  const x = finite(value?.x);
  const y = finite(value?.y);
  return x === undefined || y === undefined
    ? undefined
    : { x: x * scaleX, y: y * scaleY };
}

function faceLandmarks(
  value: unknown,
  scaleX: number,
  scaleY: number,
): FaceLandmarks5 | undefined {
  if (!Array.isArray(value)) return undefined;
  const mapped: Partial<FaceLandmarks5> = {};
  for (const raw of value) {
    if (!isRecord(raw)) continue;
    const name = landmarkName(raw.type);
    const point = scaledPoint(
      isRecord(raw.position) ? (raw.position as NativePoint) : undefined,
      scaleX,
      scaleY,
    );
    if (name && point) mapped[name] = point;
  }
  // Eyes are the only requirement. Demanding both mouth corners here rejected
  // every off-frontal face ML Kit reported without them, and each rejection fell
  // through to an UNALIGNED bounding-box crop — the silent quality cliff that
  // stops ArcFace embeddings separating people. The aligner degrades properly on
  // its own (4-point, then eyes+nose, then eyes), so hand it what exists.
  if (!mapped.leftEye || !mapped.rightEye) {
    landmarkRejects.noEyes += 1;
    return undefined;
  }
  const hasMouth = !!mapped.leftMouth && !!mapped.rightMouth;
  if (hasMouth) landmarkRejects.full += 1;
  else if (mapped.noseBase) landmarkRejects.eyesAndNose += 1;
  else landmarkRejects.eyesOnly += 1;

  return {
    leftEye: mapped.leftEye,
    rightEye: mapped.rightEye,
    ...(mapped.leftMouth ? { leftMouth: mapped.leftMouth } : {}),
    ...(mapped.rightMouth ? { rightMouth: mapped.rightMouth } : {}),
    ...(mapped.noseBase ? { noseBase: mapped.noseBase } : {}),
  };
}

/** Pure native-result mapper, exported for numeric/PascalCase regression tests. */
export function mapDetectedFaces(
  result: NativeResult,
  scaleX = 1,
  scaleY = 1,
): FaceBox[] {
  return facesFrom(result).flatMap((face): FaceBox[] => {
    const frame = face?.frame;
    if (!isRecord(frame) || !isRecord(frame.origin) || !isRecord(frame.size)) {
      return [];
    }
    const x = finite(frame.origin.x);
    const y = finite(frame.origin.y);
    const width = finite(frame.size.x);
    const height = finite(frame.size.y);
    if (
      x === undefined ||
      y === undefined ||
      width === undefined ||
      height === undefined ||
      width <= 0 ||
      height <= 0
    ) {
      return [];
    }
    return [{
      x: x * scaleX,
      y: y * scaleY,
      width: width * scaleX,
      height: height * scaleY,
      landmarks: faceLandmarks(face.landmarks, scaleX, scaleY),
      headEulerAngleX: finite(face.headEulerAngleX),
      headEulerAngleY: finite(face.headEulerAngleY),
      headEulerAngleZ: finite(face.headEulerAngleZ),
      leftEyeOpen: probability(face.leftEyeOpenProbability),
      rightEyeOpen: probability(face.rightEyeOpenProbability),
      smiling: probability(face.smilingProbability),
    }];
  });
}

/**
 * Detects faces in an already-open frame and maps every box/landmark back to
 * source-image coordinates, which is the space `faceQualityTier` and the
 * persisted pipeline have always used.
 */
export async function detectFacesInFrame(
  frame: FaceFrame,
): Promise<FaceBox[]> {
  try {
    const active = await ensureDetector();
    if (!active) return [];
    const result = await Promise.resolve(active.detectFaces(frame.uri));
    const inverse = frame.scale > 0 ? 1 / frame.scale : 1;
    return mapDetectedFaces(result, inverse, inverse);
  } catch {
    return [];
  }
}

/** Detects faces and maps every box/landmark back to source-image coordinates. */
export async function detectFaces(
  imageUri: string,
  source?: FaceImageDimensions,
): Promise<FaceBox[]> {
  let frame: FaceFrame | null = null;
  try {
    if (typeof imageUri !== "string" || imageUri.length === 0) return [];
    if (!(await ensureDetector())) return [];
    frame = await openFaceFrame(imageUri, source);
    if (!frame) return [];
    return await detectFacesInFrame(frame);
  } catch {
    return [];
  } finally {
    await closeFaceFrame(frame);
  }
}
