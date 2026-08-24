/**
 * Canonical 5-point face alignment for ArcFace-family embedders.
 *
 * MobileFaceNet/ArcFace weights are trained on faces warped onto a fixed
 * 112x112 landmark template. Feeding a raw detector bounding-box crop instead
 * (any head tilt, any framing) moves the identity far enough in embedding space
 * that one person splits into many clusters. Aligning first is what makes the
 * on-device model behave like its published benchmarks.
 *
 * Pure math + pure pixel sampling: no native module, no image encoder, so the
 * whole path is deterministic and unit-testable off-device.
 */

/**
 * ArcFace's reference landmarks for a 112x112 crop (insightface standard).
 *
 * These positions are in IMAGE space: entry 0 is the eye that appears on the
 * left of the picture, which is the subject's RIGHT eye. ML Kit names landmarks
 * from the subject's point of view, so the mapping below is deliberately
 * crossed. Getting this backwards yields a mirrored warp that looks plausible
 * on screen while silently wrecking every embedding.
 */
export const ARCFACE_TEMPLATE_112: ReadonlyArray<readonly [number, number]> = [
  [38.2946, 51.6963], // image-left eye   = subject's RIGHT eye
  [73.5318, 51.5014], // image-right eye  = subject's LEFT eye
  [56.0252, 71.7366], // nose base
  [41.5493, 92.3655], // image-left mouth corner  = subject's RIGHT corner
  [70.7299, 92.2041], // image-right mouth corner = subject's LEFT corner
];

/** Template without the nose, for the more reliable 4-point fit. */
const TEMPLATE_NO_NOSE: ReadonlyArray<readonly [number, number]> = [
  ARCFACE_TEMPLATE_112[0],
  ARCFACE_TEMPLATE_112[1],
  ARCFACE_TEMPLATE_112[3],
  ARCFACE_TEMPLATE_112[4],
];

export type Point = { x: number; y: number };

/**
 * Landmarks in image pixel coordinates, named from the SUBJECT's point of view
 * exactly as ML Kit reports them (`leftEye` is the subject's left eye, which
 * appears on the RIGHT of the picture).
 *
 * `noseBase` is optional: ML Kit exposes only NOSE_BASE (between the nostrils),
 * which sits below the true nose tip the template was built from. That constant
 * bias is better dropped than modelled, so alignment prefers the 4-point
 * eye+mouth fit and only uses the nose when the mouth corners are missing.
 */
export type FaceLandmarks5 = {
  leftEye: Point;
  rightEye: Point;
  noseBase?: Point;
  leftMouth: Point;
  rightMouth: Point;
};

/**
 * Similarity transform mapping source pixels to template pixels:
 *   u = a*x - b*y + tx
 *   v = b*x + a*y + ty
 * `a`/`b` carry both rotation and uniform scale.
 */
export type SimilarityTransform = {
  a: number;
  b: number;
  tx: number;
  ty: number;
};

function finitePoint(point: Point | undefined | null): point is Point {
  return (
    !!point && Number.isFinite(point.x) && Number.isFinite(point.y)
  );
}

/**
 * Pairs the subject-named landmarks with the image-space template, crossing
 * left/right (see ARCFACE_TEMPLATE_112). Prefers the 4-point eye+mouth fit and
 * falls back to eyes+nose when the mouth corners are unusable. Returns
 * undefined when not even the eyes are trustworthy.
 */
export function alignmentPairs(
  landmarks: FaceLandmarks5,
  template: ReadonlyArray<readonly [number, number]> = ARCFACE_TEMPLATE_112,
): { src: Point[]; dst: ReadonlyArray<readonly [number, number]> } | undefined {
  const eyesOk =
    finitePoint(landmarks.rightEye) && finitePoint(landmarks.leftEye);
  if (!eyesOk) return undefined;

  const noNose: ReadonlyArray<readonly [number, number]> = [
    template[0],
    template[1],
    template[3],
    template[4],
  ];

  // Subject's RIGHT maps to the image-LEFT template point, and vice versa.
  if (finitePoint(landmarks.rightMouth) && finitePoint(landmarks.leftMouth)) {
    return {
      src: [
        landmarks.rightEye,
        landmarks.leftEye,
        landmarks.rightMouth,
        landmarks.leftMouth,
      ],
      dst: noNose,
    };
  }

  if (finitePoint(landmarks.noseBase)) {
    return {
      src: [landmarks.rightEye, landmarks.leftEye, landmarks.noseBase],
      dst: [template[0], template[1], template[2]],
    };
  }

  // Eyes alone still pin scale, rotation and translation for a similarity fit.
  return {
    src: [landmarks.rightEye, landmarks.leftEye],
    dst: [template[0], template[1]],
  };
}

/**
 * Closed-form least-squares 2D similarity transform (scale + rotation +
 * translation) taking `src` onto `dst`. A 2D similarity is linear in
 * (a, b, tx, ty), so this needs no SVD — unlike the general affine/Umeyama
 * case — and cannot mirror the face, which a full affine fit can.
 */
export function similarityTransform(
  src: ReadonlyArray<Point>,
  dst: ReadonlyArray<readonly [number, number]>,
): SimilarityTransform | undefined {
  const n = Math.min(src.length, dst.length);
  if (n < 2) return undefined;

  let sx = 0;
  let sy = 0;
  let su = 0;
  let sv = 0;
  let sqq = 0; // Σ(x² + y²)
  let sxu = 0; // Σ(x·u + y·v)
  let sxv = 0; // Σ(x·v − y·u)

  for (let i = 0; i < n; i += 1) {
    const { x, y } = src[i];
    const u = dst[i][0];
    const v = dst[i][1];
    if (![x, y, u, v].every(Number.isFinite)) return undefined;
    sx += x;
    sy += y;
    su += u;
    sv += v;
    sqq += x * x + y * y;
    sxu += x * u + y * v;
    sxv += x * v - y * u;
  }

  // Variance of the source points about their centroid; zero means every
  // landmark collapsed to one spot and no scale can be recovered.
  const denominator = sqq - (sx * sx + sy * sy) / n;
  if (!Number.isFinite(denominator) || Math.abs(denominator) < 1e-9) {
    return undefined;
  }

  const a = (sxu - (sx * su + sy * sv) / n) / denominator;
  const b = (sxv - (sx * sv - sy * su) / n) / denominator;
  if (!Number.isFinite(a) || !Number.isFinite(b)) return undefined;
  // A degenerate (zero-scale) fit would map every pixel to one point.
  if (Math.hypot(a, b) < 1e-9) return undefined;

  return {
    a,
    b,
    tx: (su - a * sx + b * sy) / n,
    ty: (sv - b * sx - a * sy) / n,
  };
}

/** Inverts the transform so output pixels can be sampled from the source. */
export function invertSimilarity(
  transform: SimilarityTransform,
): ((u: number, v: number) => Point) | undefined {
  const { a, b, tx, ty } = transform;
  const determinant = a * a + b * b;
  if (!Number.isFinite(determinant) || determinant < 1e-12) return undefined;
  return (u, v) => {
    const du = u - tx;
    const dv = v - ty;
    return {
      x: (a * du + b * dv) / determinant,
      y: (a * dv - b * du) / determinant,
    };
  };
}

/**
 * Warps RGBA source pixels onto the aligned `size`x`size` face using the
 * inverse transform with bilinear sampling. Out-of-bounds samples clamp to the
 * edge so a face near the frame border still aligns instead of tearing.
 *
 * Returns RGB (3 channels, no alpha) — what the embedders consume.
 */
export function warpFaceRgb(
  rgba: Uint8Array | Uint8ClampedArray,
  srcWidth: number,
  srcHeight: number,
  transform: SimilarityTransform,
  size: number,
): Uint8Array | undefined {
  if (
    !Number.isInteger(srcWidth) ||
    !Number.isInteger(srcHeight) ||
    srcWidth < 1 ||
    srcHeight < 1 ||
    !Number.isInteger(size) ||
    size < 1 ||
    rgba.length < srcWidth * srcHeight * 4
  ) {
    return undefined;
  }
  const inverse = invertSimilarity(transform);
  if (!inverse) return undefined;

  const out = new Uint8Array(size * size * 3);
  const maxX = srcWidth - 1;
  const maxY = srcHeight - 1;

  for (let v = 0; v < size; v += 1) {
    for (let u = 0; u < size; u += 1) {
      // Sample at pixel centers so the warp stays centered on the template.
      const source = inverse(u + 0.5, v + 0.5);
      const sx = Math.min(Math.max(source.x - 0.5, 0), maxX);
      const sy = Math.min(Math.max(source.y - 0.5, 0), maxY);

      const x0 = Math.floor(sx);
      const y0 = Math.floor(sy);
      const x1 = Math.min(x0 + 1, maxX);
      const y1 = Math.min(y0 + 1, maxY);
      const fx = sx - x0;
      const fy = sy - y0;

      const i00 = (y0 * srcWidth + x0) * 4;
      const i10 = (y0 * srcWidth + x1) * 4;
      const i01 = (y1 * srcWidth + x0) * 4;
      const i11 = (y1 * srcWidth + x1) * 4;

      const w00 = (1 - fx) * (1 - fy);
      const w10 = fx * (1 - fy);
      const w01 = (1 - fx) * fy;
      const w11 = fx * fy;

      const target = (v * size + u) * 3;
      for (let channel = 0; channel < 3; channel += 1) {
        out[target + channel] =
          rgba[i00 + channel] * w00 +
          rgba[i10 + channel] * w10 +
          rgba[i01 + channel] * w01 +
          rgba[i11 + channel] * w11;
      }
    }
  }

  return out;
}

/**
 * Full alignment: landmarks (in the coordinate space of the supplied pixels)
 * to an aligned RGB face. Returns undefined when the landmarks are unusable so
 * callers can fall back to the legacy bounding-box crop rather than fail.
 */
export function alignFaceRgb(
  rgba: Uint8Array | Uint8ClampedArray,
  srcWidth: number,
  srcHeight: number,
  landmarks: FaceLandmarks5,
  size = 112,
): Uint8Array | undefined {
  const template =
    size === 112
      ? ARCFACE_TEMPLATE_112
      : ARCFACE_TEMPLATE_112.map(
          ([x, y]) => [(x * size) / 112, (y * size) / 112] as const,
        );

  const pairs = alignmentPairs(landmarks, template);
  if (!pairs) return undefined;

  const transform = similarityTransform(pairs.src, pairs.dst);
  if (!transform) return undefined;

  return warpFaceRgb(rgba, srcWidth, srcHeight, transform, size);
}

export { TEMPLATE_NO_NOSE };
