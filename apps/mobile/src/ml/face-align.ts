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
/**
 * Only the eyes are required.
 *
 * `alignmentPairs` already degrades from a 4-point eye+mouth fit, to eyes+nose,
 * to eyes alone — two points still pin scale, rotation and translation for a
 * similarity transform. Requiring the mouth corners in the TYPE meant the
 * detector threw away every face where ML Kit returned eyes but no mouth (which
 * is common off-frontal), and those faces silently fell back to an UNALIGNED
 * bounding-box crop — the exact quality cliff that stops different people
 * separating.
 */
export type FaceLandmarks5 = {
  leftEye: Point;
  rightEye: Point;
  noseBase?: Point;
  leftMouth?: Point;
  rightMouth?: Point;
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
/**
 * Splits a symmetric landmark pair into (image-left, image-right) using the
 * face's OWN orientation, not the detector's naming.
 *
 * ML Kit's `leftEye`/`rightEye` were assumed to be named from the subject's
 * point of view, so the code crossed them onto the template. Measured on device
 * they are the opposite way round: `rightEye` consistently carries the LARGER x,
 * i.e. it is the eye on the right of the picture. Crossing them then asks for a
 * REFLECTION, which a similarity transform cannot express — so instead of
 * failing it collapses the scale (measured: 0.156 template px per source px,
 * landmark residual 25.9px on a 112px template) and the "aligned face" becomes
 * the whole photograph squeezed into 112x112. Every counter still reads healthy.
 *
 * Rather than swap one hard-coded convention for another — and break again on a
 * different binding or platform — the side is derived geometrically. `up` runs
 * from the mouth (or nose) to the eyes; rotating it 90 degrees gives the face's
 * own rightward axis, and each point is assigned by its projection onto it.
 * That is correct at any roll angle and for either naming convention.
 */
function orderByImageSide(
  first: Point,
  second: Point,
  up: Point,
): [Point, Point] {
  // Image y grows downward, so rotating `up` this way yields image-right for an
  // upright face, and stays consistent as the head rolls.
  const rightX = -up.y;
  const rightY = up.x;
  const firstProjection = first.x * rightX + first.y * rightY;
  const secondProjection = second.x * rightX + second.y * rightY;
  return firstProjection <= secondProjection ? [first, second] : [second, first];
}

/**
 * Pairs landmarks with the image-space template (see ARCFACE_TEMPLATE_112),
 * assigning sides by geometry. Prefers the 4-point eye+mouth fit and falls back
 * to eyes+nose, then eyes alone. Returns undefined when not even the eyes are
 * trustworthy.
 */
export function alignmentPairs(
  landmarks: FaceLandmarks5,
  template: ReadonlyArray<readonly [number, number]> = ARCFACE_TEMPLATE_112,
): { src: Point[]; dst: ReadonlyArray<readonly [number, number]> } | undefined {
  const eyeA = landmarks.leftEye;
  const eyeB = landmarks.rightEye;
  if (!finitePoint(eyeA) || !finitePoint(eyeB)) return undefined;

  const eyeMid = { x: (eyeA.x + eyeB.x) / 2, y: (eyeA.y + eyeB.y) / 2 };
  const hasMouth =
    finitePoint(landmarks.leftMouth) && finitePoint(landmarks.rightMouth);
  const mouthA = landmarks.leftMouth as Point | undefined;
  const mouthB = landmarks.rightMouth as Point | undefined;

  // `up` points from the lower feature to the eyes. Mouth midpoint is the most
  // reliable; the nose is a usable stand-in; with neither, assume an upright
  // face, which is what a plain left-to-right ordering encodes.
  let up: Point;
  if (hasMouth && mouthA && mouthB) {
    up = {
      x: eyeMid.x - (mouthA.x + mouthB.x) / 2,
      y: eyeMid.y - (mouthA.y + mouthB.y) / 2,
    };
  } else if (finitePoint(landmarks.noseBase)) {
    up = { x: eyeMid.x - landmarks.noseBase.x, y: eyeMid.y - landmarks.noseBase.y };
  } else {
    up = { x: 0, y: -1 };
  }
  if (!Number.isFinite(up.x) || !Number.isFinite(up.y) || (up.x === 0 && up.y === 0)) {
    up = { x: 0, y: -1 };
  }

  const [eyeLeft, eyeRight] = orderByImageSide(eyeA, eyeB, up);

  if (hasMouth && mouthA && mouthB) {
    const [mouthLeft, mouthRight] = orderByImageSide(mouthA, mouthB, up);
    return {
      src: [eyeLeft, eyeRight, mouthLeft, mouthRight],
      dst: [template[0], template[1], template[3], template[4]],
    };
  }

  if (finitePoint(landmarks.noseBase)) {
    return {
      src: [eyeLeft, eyeRight, landmarks.noseBase],
      dst: [template[0], template[1], template[2]],
    };
  }

  return { src: [eyeLeft, eyeRight], dst: [template[0], template[1]] };
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
 * What geometry alignment actually applied, so a warp that "succeeds" while
 * producing nonsense is visible.
 *
 * A 2D similarity transform CANNOT mirror. So if the eye correspondence were
 * crossed the wrong way, the fit would not fail — it would come back as a clean
 * ~180 degree rotation with a low residual, and every aligned face would be fed
 * to the embedder upside down. That is indistinguishable from success by every
 * other counter we have (`alignFailed` stays 0), and it collapses the embedding
 * space exactly like a broken model would. Faces are overwhelmingly upright, so
 * a healthy library must sit in `upright`; a large `upsideDown` bucket is the
 * signature of a crossed template.
 */
const alignmentShape = {
  upright: 0,
  tilted: 0,
  upsideDown: 0,
  residualSum: 0,
  residualCount: 0,
};

export function faceAlignmentShapeCounts(): {
  upright: number;
  tilted: number;
  upsideDown: number;
  residualPx: number;
} {
  const { upright, tilted, upsideDown, residualSum, residualCount } =
    alignmentShape;
  return {
    upright,
    tilted,
    upsideDown,
    residualPx: residualCount > 0 ? Number((residualSum / residualCount).toFixed(2)) : 0,
  };
}

/**
 * Optional capture of the actual aligned faces, for eyeballing off-device.
 *
 * Every counter here can look healthy while the warp samples entirely the wrong
 * pixels: a mis-scaled landmark set still yields a finite, invertible transform,
 * so it "succeeds". The only way to be certain the embedder is being fed faces
 * is to look at what it is fed. Off by default; costs one array push when armed.
 */
const alignedSamples: Uint8Array[] = [];
let alignedSampleLimit = 0;

export function captureAlignedSamples(limit: number): void {
  alignedSampleLimit = Math.max(0, limit);
  alignedSamples.length = 0;
}

export function takeAlignedSamples(): Uint8Array[] {
  const taken = alignedSamples.slice();
  alignedSamples.length = 0;
  alignedSampleLimit = 0;
  return taken;
}

/** Records the rotation and landmark residual of one accepted alignment. */
function recordAlignmentShape(
  transform: SimilarityTransform,
  pairs: { src: Point[]; dst: ReadonlyArray<readonly [number, number]> },
): void {
  const degrees = Math.abs(
    (Math.atan2(transform.b, transform.a) * 180) / Math.PI,
  );
  if (degrees <= 30) alignmentShape.upright += 1;
  else if (degrees < 150) alignmentShape.tilted += 1;
  else alignmentShape.upsideDown += 1;

  const count = Math.min(pairs.src.length, pairs.dst.length);
  let residual = 0;
  for (let index = 0; index < count; index += 1) {
    const { x, y } = pairs.src[index];
    const u = transform.a * x - transform.b * y + transform.tx;
    const v = transform.b * x + transform.a * y + transform.ty;
    residual += Math.hypot(u - pairs.dst[index][0], v - pairs.dst[index][1]);
  }
  if (count > 0) {
    alignmentShape.residualSum += residual / count;
    alignmentShape.residualCount += 1;
  }
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

  recordAlignmentShape(transform, pairs);

  const warped = warpFaceRgb(rgba, srcWidth, srcHeight, transform, size);
  if (warped && alignedSamples.length < alignedSampleLimit) {
    alignedSamples.push(warped);
  }
  return warped;
}

export { TEMPLATE_NO_NOSE };
