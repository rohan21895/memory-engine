export type FaceBox = {
  x: number;
  y: number;
  width: number;
  height: number;
  // Classification probabilities (0..1) when ML Kit classification is on;
  // undefined when the model didn't report them. Used by selection quality,
  // ignored by identity clustering (which only needs the box).
  leftEyeOpen?: number;
  rightEyeOpen?: number;
  smiling?: number;
};

// Imperative surface of @infinitered/react-native-mlkit-face-detection's
// RNMLKitFaceDetector. We use the class directly (no React hook / provider) so
// the background scan can run outside the component tree.
type NativeFrame = {
  origin?: { x?: unknown; y?: unknown };
  size?: { x?: unknown; y?: unknown };
};
type NativeFace = {
  frame?: NativeFrame;
  leftEyeOpenProbability?: unknown;
  rightEyeOpenProbability?: unknown;
  smilingProbability?: unknown;
};
type NativeResult = { faces?: unknown } | unknown[];
type NativeDetector = {
  initialize: (options?: unknown) => unknown;
  detectFaces: (uri: string) => NativeResult | Promise<NativeResult>;
};
type NativeModule = {
  RNMLKitFaceDetector?: new () => NativeDetector;
};

const DETECTOR_OPTIONS = {
  performanceMode: "accurate",
  landmarkMode: false,
  contourMode: false,
  // On: ML Kit reports eyes-open + smiling probabilities, which selection uses
  // to reject blinks and prefer smiles.
  classificationMode: true,
  minFaceSize: 0.08,
  isTrackingEnabled: false,
};

function prob(value: unknown): number | undefined {
  return typeof value === "number" && Number.isFinite(value) && value >= 0
    ? value
    : undefined;
}

let moduleLoaded: NativeModule | null | undefined;
let detector: NativeDetector | null = null;
let initPromise: Promise<NativeDetector | null> | null = null;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function loadModule(): NativeModule | null {
  if (moduleLoaded !== undefined) {
    return moduleLoaded;
  }
  try {
    // Guarded require: a build may omit the native binding, and Expo module
    // evaluation can throw. Any failure degrades to "no faces", never a crash.
    const mod = require(
      "@infinitered/react-native-mlkit-face-detection",
    ) as NativeModule;
    moduleLoaded = typeof mod?.RNMLKitFaceDetector === "function" ? mod : null;
  } catch {
    moduleLoaded = null;
  }
  return moduleLoaded;
}

/** True when the native ML Kit binding is present (init still happens lazily). */
export function isFaceDetectionAvailable(): boolean {
  return loadModule() !== null;
}

async function ensureDetector(): Promise<NativeDetector | null> {
  if (detector) {
    return detector;
  }
  initPromise ??= (async () => {
    const mod = loadModule();
    if (!mod?.RNMLKitFaceDetector) {
      return null;
    }
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

// ML Kit reads a local file. content:// URIs from MediaStore can be rejected by
// the native decoder, so materialize a full-resolution local JPEG first — kept
// full-size so face coordinates stay in the original image's pixel space.
async function toLocalUri(imageUri: string): Promise<string | null> {
  if (imageUri.startsWith("file://")) {
    return imageUri;
  }
  try {
    const imageManipulator = await import("expo-image-manipulator");
    const out = await imageManipulator.manipulateAsync(imageUri, [], {
      compress: 1,
      format: imageManipulator.SaveFormat.JPEG,
    });
    return out.uri ?? null;
  } catch {
    return null;
  }
}

function facesFrom(result: NativeResult): NativeFace[] {
  if (Array.isArray(result)) {
    return result as NativeFace[];
  }
  if (isRecord(result) && Array.isArray(result.faces)) {
    return result.faces as NativeFace[];
  }
  return [];
}

/**
 * Detects faces in a static image.
 *
 * Boxes are in the source image's pixel coordinate space (top-left origin),
 * matching the width/height reported by expo-media-library for the asset, so
 * the caller can crop directly against the original dimensions.
 */
export async function detectFaces(imageUri: string): Promise<FaceBox[]> {
  try {
    if (typeof imageUri !== "string" || imageUri.length === 0) {
      return [];
    }
    const active = await ensureDetector();
    if (!active) {
      return [];
    }
    const localUri = await toLocalUri(imageUri);
    if (!localUri) {
      return [];
    }

    const result = await Promise.resolve(active.detectFaces(localUri));
    return facesFrom(result).flatMap((face): FaceBox[] => {
      const frame = face?.frame;
      if (!isRecord(frame) || !isRecord(frame.origin) || !isRecord(frame.size)) {
        return [];
      }
      const x = frame.origin.x;
      const y = frame.origin.y;
      const width = frame.size.x;
      const height = frame.size.y;
      if (
        typeof x !== "number" ||
        typeof y !== "number" ||
        typeof width !== "number" ||
        typeof height !== "number" ||
        !Number.isFinite(x) ||
        !Number.isFinite(y) ||
        !Number.isFinite(width) ||
        !Number.isFinite(height) ||
        width <= 0 ||
        height <= 0
      ) {
        return [];
      }
      return [
        {
          x,
          y,
          width,
          height,
          leftEyeOpen: prob(face.leftEyeOpenProbability),
          rightEyeOpen: prob(face.rightEyeOpenProbability),
          smiling: prob(face.smilingProbability),
        },
      ];
    });
  } catch {
    return [];
  }
}
