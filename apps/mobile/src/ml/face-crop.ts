/**
 * Turns a detected face in a photo into the aligned 112x112 RGB tensor input
 * that ArcFace-family embedders expect.
 *
 * Strategy: crop a generous square around the face (so a rotated alignment
 * still has pixels in every corner), downscale that patch to a working size,
 * decode it once, then warp with the 5-point similarity transform. Doing the
 * warp on the small patch keeps the work off the full-resolution image, which
 * a phone cannot decode per face.
 */

// @ts-expect-error TypeScript bundler resolution normally omits source extensions.
import { alignFaceRgb, type FaceLandmarks5, type Point } from "./face-align.ts";

/** Working patch size: big enough that the 112 warp never upsamples. */
const PATCH_SIZE = 256;
/** How much context around the box to keep so rotation has real pixels. */
const PATCH_SCALE = 2.2;
export const ALIGNED_FACE_SIZE = 112;

export type SourceImage = { width: number; height: number };
export type Box = { x: number; y: number; width: number; height: number };

export type PatchGeometry = {
  originX: number;
  originY: number;
  size: number;
  /** Multiply source-image coordinates by this after subtracting the origin. */
  scale: number;
};

/**
 * Square patch around the face, clamped inside the image. Pure so the
 * coordinate mapping can be unit-tested without an image decoder.
 */
export function patchGeometry(
  asset: SourceImage,
  box: Box,
  patchSize = PATCH_SIZE,
  patchScale = PATCH_SCALE,
): PatchGeometry | undefined {
  const values = [asset.width, asset.height, box.x, box.y, box.width, box.height];
  if (
    !values.every((value) => Number.isFinite(value)) ||
    asset.width < 1 ||
    asset.height < 1 ||
    box.width <= 0 ||
    box.height <= 0
  ) {
    return undefined;
  }

  const centerX = box.x + box.width / 2;
  const centerY = box.y + box.height / 2;
  const wanted = Math.max(box.width, box.height) * patchScale;
  // Never exceed the image, and keep at least one pixel to crop.
  const size = Math.max(1, Math.min(wanted, asset.width, asset.height));

  const originX = Math.min(Math.max(centerX - size / 2, 0), asset.width - size);
  const originY = Math.min(Math.max(centerY - size / 2, 0), asset.height - size);

  return { originX, originY, size, scale: patchSize / size };
}

/** Maps a landmark from source-image space into the decoded patch's space. */
export function landmarkToPatch(point: Point, geometry: PatchGeometry): Point {
  return {
    x: (point.x - geometry.originX) * geometry.scale,
    y: (point.y - geometry.originY) * geometry.scale,
  };
}

export function landmarksToPatch(
  landmarks: FaceLandmarks5,
  geometry: PatchGeometry,
): FaceLandmarks5 {
  return {
    leftEye: landmarkToPatch(landmarks.leftEye, geometry),
    rightEye: landmarkToPatch(landmarks.rightEye, geometry),
    noseBase: landmarkToPatch(landmarks.noseBase, geometry),
    leftMouth: landmarkToPatch(landmarks.leftMouth, geometry),
    rightMouth: landmarkToPatch(landmarks.rightMouth, geometry),
  };
}

/** Crop rectangle for expo-image-manipulator, derived from the geometry. */
export function patchCropRect(geometry: PatchGeometry): {
  originX: number;
  originY: number;
  width: number;
  height: number;
} {
  return {
    originX: geometry.originX,
    originY: geometry.originY,
    width: geometry.size,
    height: geometry.size,
  };
}

/**
 * Aligns an already-decoded patch. The caller owns decoding (facenet.ts already
 * has the base64 + JPEG decoders), so this module stays free of native imports
 * and is unit-testable off-device.
 *
 * Returns undefined when the landmarks are unusable so the caller can fall back
 * to the legacy bounding-box crop instead of failing the whole scan.
 */
export function alignDecodedPatch(
  patchRgba: Uint8Array | Uint8ClampedArray,
  patchWidth: number,
  patchHeight: number,
  landmarks: FaceLandmarks5,
  geometry: PatchGeometry,
  size = ALIGNED_FACE_SIZE,
): Uint8Array | undefined {
  return alignFaceRgb(
    patchRgba,
    patchWidth,
    patchHeight,
    landmarksToPatch(landmarks, geometry),
    size,
  );
}

export { PATCH_SIZE };
