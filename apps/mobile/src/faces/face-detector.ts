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

type WorkingImage = {
  uri: string;
  sourceWidth: number;
  sourceHeight: number;
  width: number;
  height: number;
  temporary: boolean;
};

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
 * Materializes only a bounded detection copy. `content://` is not accepted by
 * every ML Kit decoder, while decoding the original 12MP frame for every photo
 * can exhaust Android's native bitmap heap.
 */
async function workingImage(
  imageUri: string,
  source: FaceImageDimensions | undefined,
): Promise<WorkingImage | null> {
  const dimensions = await resolveDimensions(imageUri, source);
  if (!dimensions) return null;

  const longEdge = Math.max(dimensions.width, dimensions.height);
  if (imageUri.startsWith("file://") && longEdge <= MAX_DETECTION_EDGE) {
    return {
      uri: imageUri,
      sourceWidth: dimensions.width,
      sourceHeight: dimensions.height,
      width: dimensions.width,
      height: dimensions.height,
      temporary: false,
    };
  }

  const imageManipulator = await import("expo-image-manipulator");
  const scale = Math.min(1, MAX_DETECTION_EDGE / longEdge);
  const width = Math.max(1, Math.round(dimensions.width * scale));
  const height = Math.max(1, Math.round(dimensions.height * scale));
  const output = await imageManipulator.manipulateAsync(
    imageUri,
    [{ resize: { width, height } }],
    { compress: 0.88, format: imageManipulator.SaveFormat.JPEG },
  );
  return {
    uri: output.uri,
    sourceWidth: dimensions.width,
    sourceHeight: dimensions.height,
    width: output.width,
    height: output.height,
    temporary: output.uri !== imageUri,
  };
}

async function deleteTemporary(uri: string): Promise<void> {
  try {
    const fileSystem = await import("expo-file-system/legacy");
    await fileSystem.deleteAsync(uri, { idempotent: true });
  } catch {
    // Cache cleanup is best-effort and must never crash detection.
  }
}

function facesFrom(result: NativeResult): NativeFace[] {
  if (Array.isArray(result)) return result as NativeFace[];
  return isRecord(result) && Array.isArray(result.faces)
    ? (result.faces as NativeFace[])
    : [];
}

type LandmarkName = keyof FaceLandmarks5;

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
  if (
    !mapped.leftEye ||
    !mapped.rightEye ||
    !mapped.leftMouth ||
    !mapped.rightMouth
  ) {
    return undefined;
  }
  return {
    leftEye: mapped.leftEye,
    rightEye: mapped.rightEye,
    leftMouth: mapped.leftMouth,
    rightMouth: mapped.rightMouth,
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

/** Detects faces and maps every box/landmark back to source-image coordinates. */
export async function detectFaces(
  imageUri: string,
  source?: FaceImageDimensions,
): Promise<FaceBox[]> {
  let working: WorkingImage | null = null;
  try {
    if (typeof imageUri !== "string" || imageUri.length === 0) return [];
    const active = await ensureDetector();
    if (!active) return [];
    working = await workingImage(imageUri, source);
    if (!working) return [];
    const result = await Promise.resolve(active.detectFaces(working.uri));
    return mapDetectedFaces(
      result,
      working.sourceWidth / working.width,
      working.sourceHeight / working.height,
    );
  } catch {
    return [];
  } finally {
    if (working?.temporary) await deleteTemporary(working.uri);
  }
}
