/**
 * Turns a detected face in a photo into the aligned 112x112 RGB tensor input
 * that ArcFace-family embedders expect.
 *
 * Strategy: crop the source rectangle the aligned warp will actually read,
 * decode it at roughly one patch pixel per output pixel, then warp with the
 * 5-point similarity transform. Doing the warp on the small patch keeps the
 * work off the full-resolution image, which a phone cannot decode per face.
 *
 * Patch SIZE and patch RESOLUTION are separate decisions and both used to be
 * guesses (2.2x the detector box, decoded at a flat 256). Every one of those
 * pixels is JPEG-encoded natively, base64'd across the bridge and decoded in
 * JavaScript for every single face, and about four fifths of them were thrown
 * away by the warp. `alignedPatchGeometry` derives both from the transform
 * instead; `patchGeometry` is the older box-derived square, kept because the
 * scale-covariance proof in face-detector.test.ts is written against it.
 */

// @ts-expect-error TypeScript bundler resolution normally omits source extensions.
import { alignFaceRgb, alignmentPairs, invertSimilarity, similarityTransform, type FaceLandmarks5, type Point } from "./face-align.ts";

/** Hard ceiling on the decoded patch, for a pathological transform. */
const PATCH_SIZE = 256;
/** How much context around the box to keep so rotation has real pixels. */
const PATCH_SCALE = 2.2;
export const ALIGNED_FACE_SIZE = 112;
/**
 * Slack, in aligned-output pixels, kept around the warp's sampling window.
 *
 * Two things need it: bilinear sampling reads one neighbour past each sample,
 * and the crop rectangle is rounded to whole source pixels. Without it the
 * outermost ring of the aligned face would come from `warpFaceRgb`'s
 * out-of-bounds edge clamp instead of from real pixels.
 */
const PATCH_MARGIN = 2;

export type SourceImage = { width: number; height: number };
export type Box = { x: number; y: number; width: number; height: number };

export type PatchGeometry = {
  originX: number;
  originY: number;
  size: number;
  /**
   * Side of the decoded patch in pixels — what the crop is resized to, and what
   * the decoded image must measure. Always `scale * size`; keeping it on the
   * geometry is what stops the resize target and the landmark mapping drifting
   * apart, which would misalign every face by a constant nobody would notice.
   */
  patchSize: number;
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

  return { originX, originY, size, patchSize, scale: patchSize / size };
}

/**
 * The patch the aligned warp will actually read, at ~one patch pixel per
 * aligned output pixel. Landmarks must be in SOURCE-image coordinates.
 *
 * The transform already knows the answer that `patchGeometry` was guessing.
 * Inverse-mapping the four corners of the 112x112 output back into source space
 * gives exactly the region the warp can sample; the bounding square of that
 * region is the crop, and `scale * side` is the resolution at which one patch
 * pixel is one output pixel. More than that is decoded and then skipped by the
 * warp (which also aliases, because bilinear does not area-average); less is
 * upsampled detail the face never had.
 *
 * For an upright face that lands near 112 instead of 256 — a 5x cut in the
 * pixels that pay for the JPEG encode, the base64 round trip and the pure-JS
 * decode. A 45-degree head tilt needs the diagonal, so it lands near 160.
 *
 * Scale-covariant with `patchGeometry`: run it on the full-resolution photo or
 * on the downscaled scan frame and the landmarks land on the same patch pixels,
 * because the transform's scale shrinks by exactly the factor the crop grows by.
 * Rounding to whole source pixels is deliberate — the native crop rounds the
 * rectangle regardless, and at ~1:1 a rounding the geometry does not know about
 * would shift the aligned face by most of an output pixel.
 *
 * Returns undefined when the landmarks cannot pin a transform, which is exactly
 * when `alignDecodedPatch` would fail too, so the caller can go straight to the
 * bounding-box fallback instead of paying for a crop first.
 */
export function alignedPatchGeometry(
  asset: SourceImage,
  box: Box,
  landmarks: FaceLandmarks5,
): PatchGeometry | undefined {
  // Borrowed purely for its input validation (finite, positive, in-bounds).
  if (!patchGeometry(asset, box)) return undefined;

  const pairs = alignmentPairs(landmarks);
  const transform = pairs && similarityTransform(pairs.src, pairs.dst);
  const inverse = transform ? invertSimilarity(transform) : undefined;
  if (!transform || !inverse) return undefined;

  // Template pixels per source pixel. Guarded so every division below is finite.
  const density = Math.hypot(transform.a, transform.b);
  if (!Number.isFinite(density) || density <= 0) return undefined;

  const corners = [
    inverse(0, 0),
    inverse(ALIGNED_FACE_SIZE, 0),
    inverse(0, ALIGNED_FACE_SIZE),
    inverse(ALIGNED_FACE_SIZE, ALIGNED_FACE_SIZE),
  ];
  let minX = Infinity;
  let maxX = -Infinity;
  let minY = Infinity;
  let maxY = -Infinity;
  for (const corner of corners) {
    if (!Number.isFinite(corner.x) || !Number.isFinite(corner.y)) return undefined;
    minX = Math.min(minX, corner.x);
    maxX = Math.max(maxX, corner.x);
    minY = Math.min(minY, corner.y);
    maxY = Math.max(maxY, corner.y);
  }

  const imageWidth = Math.floor(asset.width);
  const imageHeight = Math.floor(asset.height);
  // Square, so one `scale` maps both axes and the existing landmark mapping and
  // crop rect keep working unchanged.
  const wanted = Math.max(maxX - minX, maxY - minY) + (2 * PATCH_MARGIN) / density;
  const side = Math.max(1, Math.min(Math.round(wanted), imageWidth, imageHeight));
  const originX = clampInteger(
    Math.round((minX + maxX) / 2 - side / 2),
    0,
    imageWidth - side,
  );
  const originY = clampInteger(
    Math.round((minY + maxY) / 2 - side / 2),
    0,
    imageHeight - side,
  );

  // `side` last on purpose: a face smaller than the template must stay at the
  // resolution it actually has rather than be upsampled to the 112 floor.
  const patchSize = Math.max(
    1,
    Math.min(
      PATCH_SIZE,
      side,
      Math.max(Math.round(density * side), ALIGNED_FACE_SIZE),
    ),
  );

  return { originX, originY, size: side, patchSize, scale: patchSize / side };
}

function clampInteger(value: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, value));
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
  // Mouth corners are optional: a face ML Kit saw off-frontal may carry eyes
  // only, and mapping an absent point would manufacture a landmark at the
  // patch origin, which would drag the whole alignment toward one corner.
  return {
    leftEye: landmarkToPatch(landmarks.leftEye, geometry),
    rightEye: landmarkToPatch(landmarks.rightEye, geometry),
    ...(landmarks.noseBase
      ? { noseBase: landmarkToPatch(landmarks.noseBase, geometry) }
      : {}),
    ...(landmarks.leftMouth
      ? { leftMouth: landmarkToPatch(landmarks.leftMouth, geometry) }
      : {}),
    ...(landmarks.rightMouth
      ? { rightMouth: landmarkToPatch(landmarks.rightMouth, geometry) }
      : {}),
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
