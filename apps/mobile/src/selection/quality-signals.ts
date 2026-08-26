export type FaceSignal = {
  areaRatio: number;
  eyesOpen?: number;
  smile?: number;
  cutAtEdge: boolean;
};

export type Category =
  | "portrait"
  | "couple"
  | "group"
  | "detail"
  | "scene";

export type QualitySignals = {
  sharpness?: number;
  exposure?: number;
  clippedFraction?: number;
  faces: FaceSignal[];
  faceCount: number;
  largestFaceAreaRatio: number;
  anyFaceCutAtEdge: boolean;
  isScreenshotOrDocument: boolean;
  category: Category;
};

type PhotoMetadata = {
  filename: string;
  width?: number;
  height?: number;
};

const TINY_FACE_AREA_RATIO = 0.01;
const PORTRAIT_FACE_AREA_RATIO = 0.035;
const DOCUMENT_ASPECT_RATIO = 3.5;
const SCREEN_RATIO_TOLERANCE = 0.04;
const COMMON_SCREEN_ASPECT_RATIOS = [
  4 / 3,
  16 / 10,
  16 / 9,
  18 / 9,
  19.5 / 9,
  20 / 9,
];

/**
 * Faces below 1% of the image are treated as incidental detail. A single face
 * becomes a portrait at 3.5%; two non-tiny faces are a couple and three or more
 * are a group. No detected faces is a scene.
 */
export function classifyCategory(
  faceCount: number,
  largestFaceAreaRatio: number,
): Category {
  const count = Number.isFinite(faceCount) ? Math.max(0, Math.floor(faceCount)) : 0;
  const largest = clamp01(largestFaceAreaRatio);

  if (count === 0) {
    return "scene";
  }
  if (largest < TINY_FACE_AREA_RATIO) {
    return "detail";
  }
  if (count >= 3) {
    return "group";
  }
  if (count === 2) {
    return "couple";
  }
  return largest >= PORTRAIT_FACE_AREA_RATIO ? "portrait" : "detail";
}

/**
 * Conservative metadata-only filter. A screenshot-like name (including PNG)
 * must also be within 0.04 of a common screen aspect ratio. Independently, an
 * aspect ratio of at least 3.5 catches long receipts/documents. An explicit
 * detector hint, when supplied, takes precedence over this fallback heuristic.
 */
export function isScreenshotOrDocument(
  photo: PhotoMetadata,
  explicitHint?: boolean,
): boolean {
  if (explicitHint !== undefined) {
    return explicitHint;
  }

  const width = positiveNumber(photo.width);
  const height = positiveNumber(photo.height);
  if (width === undefined || height === undefined) {
    return false;
  }

  const aspectRatio = Math.max(width, height) / Math.min(width, height);
  if (aspectRatio >= DOCUMENT_ASPECT_RATIO) {
    return true;
  }

  const screenshotLikeName = /screenshot|screen[_-]?shot|\.png$/iu.test(
    photo.filename,
  );
  return screenshotLikeName && isCommonScreenRatio(aspectRatio);
}

/** Keep only faces large enough to affect the human quality judgment. */
export function significantFaces(
  faces: FaceSignal[],
  minArea: number,
): FaceSignal[] {
  const threshold = Math.max(0, minArea);
  return faces.filter(
    (face) => Number.isFinite(face.areaRatio) && face.areaRatio >= threshold,
  );
}

/** Lowest known eye-open probability; unknown faces are neutral. */
export function worstEyesOpen(faces: FaceSignal[]): number | undefined {
  return boundedValues(faces.map((face) => face.eyesOpen)).reduce<
    number | undefined
  >((worst, value) => worst === undefined ? value : Math.min(worst, value), undefined);
}

/** Highest known smile probability; unknown faces are neutral. */
export function bestSmile(faces: FaceSignal[]): number | undefined {
  return boundedValues(faces.map((face) => face.smile)).reduce<
    number | undefined
  >((best, value) => best === undefined ? value : Math.max(best, value), undefined);
}

function boundedValues(values: Array<number | undefined>): number[] {
  return values.flatMap((value) =>
    typeof value === "number" && Number.isFinite(value)
      ? [clamp01(value)]
      : [],
  );
}

function isCommonScreenRatio(aspectRatio: number): boolean {
  return COMMON_SCREEN_ASPECT_RATIOS.some(
    (screenRatio) => Math.abs(aspectRatio - screenRatio) <= SCREEN_RATIO_TOLERANCE,
  );
}

function positiveNumber(value: number | undefined): number | undefined {
  return typeof value === "number" && Number.isFinite(value) && value > 0
    ? value
    : undefined;
}

function clamp01(value: number): number {
  return Number.isFinite(value) ? Math.max(0, Math.min(1, value)) : 0;
}
