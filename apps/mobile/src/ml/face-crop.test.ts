// @ts-expect-error Node's TypeScript runner requires the source extension.
import { alignmentPairs, ARCFACE_TEMPLATE_112, invertSimilarity, similarityTransform, type FaceLandmarks5 } from "./face-align.ts";
// @ts-expect-error Node's TypeScript runner requires the source extension.
import { ALIGNED_FACE_SIZE, alignDecodedPatch, alignedPatchGeometry, landmarksToPatch, patchCropRect, PATCH_SIZE, type PatchGeometry } from "./face-crop.ts";

function assert(value: unknown, message: string): asserts value {
  if (!value) throw new Error(`face-crop self-check failed: ${message}`);
}

function near(actual: number, expected: number, tolerance: number, message: string): void {
  assert(
    Math.abs(actual - expected) <= tolerance,
    `${message} (got ${actual}, want ${expected} ±${tolerance})`,
  );
}

/**
 * Builds a synthetic face by applying a known rotation + scale + translation to
 * the ArcFace template, so the correct answer is knowable in closed form: the
 * warp must put every landmark back on the template point it came from.
 *
 * Named from the SUBJECT's point of view with the deliberate left/right cross
 * (see ARCFACE_TEMPLATE_112) — template[0] is the image-left eye, which is the
 * subject's RIGHT eye.
 */
function syntheticFace(
  degrees: number,
  scale: number,
  centerX: number,
  centerY: number,
): { landmarks: FaceLandmarks5; points: Array<{ x: number; y: number }> } {
  const theta = (degrees * Math.PI) / 180;
  const place = (x: number, y: number) => ({
    x: scale * (x * Math.cos(theta) - y * Math.sin(theta)),
    y: scale * (x * Math.sin(theta) + y * Math.cos(theta)),
  });
  // Positioned by the CENTRE of the aligned output rather than by a raw offset,
  // so a tilt moves the face's orientation and not its place in the frame. With
  // a raw offset a tilted face drifts off the top of the image, where the crop
  // clamps and the sampling-window check below is expected to fail.
  const middle = place(ALIGNED_FACE_SIZE / 2, ALIGNED_FACE_SIZE / 2);
  const offsetX = centerX - middle.x;
  const offsetY = centerY - middle.y;
  const points = ARCFACE_TEMPLATE_112.map(([x, y]) => {
    const placed = place(x, y);
    return { x: placed.x + offsetX, y: placed.y + offsetY };
  });
  return {
    points,
    landmarks: {
      rightEye: points[0],
      leftEye: points[1],
      noseBase: points[2],
      rightMouth: points[3],
      leftMouth: points[4],
    },
  };
}

/** Loose bounding box around the landmarks — only used for input validation. */
function boxAround(points: Array<{ x: number; y: number }>) {
  const xs = points.map((point) => point.x);
  const ys = points.map((point) => point.y);
  const minX = Math.min(...xs);
  const maxX = Math.max(...xs);
  const minY = Math.min(...ys);
  const maxY = Math.max(...ys);
  const padX = (maxX - minX) * 0.4;
  const padY = (maxY - minY) * 0.4;
  return {
    x: minX - padX,
    y: minY - padY,
    width: maxX - minX + 2 * padX,
    height: maxY - minY + 2 * padY,
  };
}

/** Black RGBA canvas with an opaque white disc at each landmark. */
function paintLandmarks(
  size: number,
  points: Array<{ x: number; y: number }>,
  radius: number,
): Uint8Array {
  const pixels = new Uint8Array(size * size * 4);
  for (let index = 3; index < pixels.length; index += 4) pixels[index] = 255;
  for (const point of points) {
    for (let y = Math.floor(point.y - radius); y <= Math.ceil(point.y + radius); y += 1) {
      for (let x = Math.floor(point.x - radius); x <= Math.ceil(point.x + radius); x += 1) {
        if (x < 0 || y < 0 || x >= size || y >= size) continue;
        if (Math.hypot(x + 0.5 - point.x, y + 0.5 - point.y) > radius) continue;
        const offset = (y * size + x) * 4;
        pixels[offset] = 255;
        pixels[offset + 1] = 255;
        pixels[offset + 2] = 255;
      }
    }
  }
  return pixels;
}

/**
 * Stands in for expo-image-manipulator's native crop + resize. Uses the same
 * pixel-center convention as `warpFaceRgb`, which is exactly the convention
 * `landmarkToPatch` has to agree with: patch coordinate q sits at source
 * coordinate originX + q * (size / patchSize).
 */
function cropAndResize(
  source: Uint8Array,
  sourceSize: number,
  geometry: PatchGeometry,
): Uint8Array {
  const { patchSize } = geometry;
  const out = new Uint8Array(patchSize * patchSize * 4);
  const step = geometry.size / patchSize;
  const maxIndex = sourceSize - 1;
  for (let py = 0; py < patchSize; py += 1) {
    for (let px = 0; px < patchSize; px += 1) {
      const sx = Math.min(Math.max(geometry.originX + (px + 0.5) * step - 0.5, 0), maxIndex);
      const sy = Math.min(Math.max(geometry.originY + (py + 0.5) * step - 0.5, 0), maxIndex);
      const x0 = Math.floor(sx);
      const y0 = Math.floor(sy);
      const x1 = Math.min(x0 + 1, maxIndex);
      const y1 = Math.min(y0 + 1, maxIndex);
      const fx = sx - x0;
      const fy = sy - y0;
      const target = (py * patchSize + px) * 4;
      for (let channel = 0; channel < 4; channel += 1) {
        out[target + channel] =
          source[(y0 * sourceSize + x0) * 4 + channel] * (1 - fx) * (1 - fy) +
          source[(y0 * sourceSize + x1) * 4 + channel] * fx * (1 - fy) +
          source[(y1 * sourceSize + x0) * 4 + channel] * (1 - fx) * fy +
          source[(y1 * sourceSize + x1) * 4 + channel] * fx * fy;
      }
    }
  }
  return out;
}

const IMAGE_SIZE = 640;

// ── The patch is sized from the warp, not from the detector box ──────────────
{
  const { landmarks, points } = syntheticFace(20, 3, 320, 320);
  const asset = { width: IMAGE_SIZE, height: IMAGE_SIZE };
  const geometry = alignedPatchGeometry(asset, boxAround(points), landmarks);
  assert(geometry, "a well-formed face resolves an aligned patch geometry");
  assert(
    geometry.patchSize < PATCH_SIZE,
    `a 20 degree face needs fewer pixels than the ${PATCH_SIZE} ceiling (got ${geometry.patchSize})`,
  );
  // At 1:1 the patch is the aligned output plus the sampling margin, grown by
  // the diagonal of the tilt. Well under half the old flat 256.
  assert(
    geometry.patchSize >= ALIGNED_FACE_SIZE && geometry.patchSize <= 160,
    `the patch stays near the 112 output (got ${geometry.patchSize})`,
  );
  near(geometry.scale, geometry.patchSize / geometry.size, 1e-12, "scale is patchSize / size");
  assert(
    Number.isInteger(geometry.originX) &&
      Number.isInteger(geometry.originY) &&
      Number.isInteger(geometry.size),
    "the crop rect is whole source pixels, so the native rounding cannot shift it",
  );
  const rect = patchCropRect(geometry);
  assert(
    rect.originX >= 0 &&
      rect.originY >= 0 &&
      rect.originX + rect.width <= IMAGE_SIZE &&
      rect.originY + rect.height <= IMAGE_SIZE,
    "the crop stays inside the image",
  );
}

// ── The patch must contain the warp's whole sampling window ──────────────────
// A patch cropped even slightly too tight does not fail: `warpFaceRgb` clamps
// out-of-bounds samples to the edge, so the aligned face keeps its landmarks
// and quietly grows a smeared border. This is what PATCH_MARGIN buys, and the
// landmark assertions below cannot see it because every landmark sits well
// inside the template.
for (const degrees of [0, 20, -35, 45]) {
  const { landmarks, points } = syntheticFace(degrees, 3, 320, 320);
  const geometry = alignedPatchGeometry(
    { width: IMAGE_SIZE, height: IMAGE_SIZE },
    boxAround(points),
    landmarks,
  );
  assert(geometry, `a ${degrees} degree face resolves a geometry`);
  // The transform recovered in patch space is the one the warp will use, so its
  // inverse maps the output corners onto the patch pixels the warp will read.
  const pairs = alignmentPairs(landmarksToPatch(landmarks, geometry));
  const transform = pairs && similarityTransform(pairs.src, pairs.dst);
  const inverse = transform ? invertSimilarity(transform) : undefined;
  assert(inverse, `the patch-space transform inverts at ${degrees} degrees`);
  for (const [u, v] of [[0, 0], [ALIGNED_FACE_SIZE, 0], [0, ALIGNED_FACE_SIZE], [ALIGNED_FACE_SIZE, ALIGNED_FACE_SIZE]]) {
    const corner = inverse(u, v);
    assert(
      corner.x >= 0 &&
        corner.y >= 0 &&
        corner.x <= geometry.patchSize &&
        corner.y <= geometry.patchSize,
      `output corner (${u},${v}) is inside the patch at ${degrees} degrees (got ${corner.x.toFixed(2)},${corner.y.toFixed(2)} of ${geometry.patchSize})`,
    );
  }
}

// ── End to end: the warp still samples the right pixels ──────────────────────
// This is the assertion the whole change has to survive. A landmark painted at
// a known source position must come back out on its template position after
// crop → resize → landmarksToPatch → warp. Getting the patch resolution or the
// origin wrong shifts these dots without failing anything.
for (const degrees of [0, 20, -35]) {
  const { landmarks, points } = syntheticFace(degrees, 3, 320, 320);
  const asset = { width: IMAGE_SIZE, height: IMAGE_SIZE };
  const geometry = alignedPatchGeometry(asset, boxAround(points), landmarks);
  assert(geometry, `a ${degrees} degree face resolves a geometry`);

  const source = paintLandmarks(IMAGE_SIZE, points, 6);
  const patch = cropAndResize(source, IMAGE_SIZE, geometry);
  const aligned = alignDecodedPatch(
    patch,
    geometry.patchSize,
    geometry.patchSize,
    landmarks,
    geometry,
  );
  assert(aligned, `a ${degrees} degree face aligns`);
  assert(
    aligned.length === ALIGNED_FACE_SIZE * ALIGNED_FACE_SIZE * 3,
    "the aligned face is 112x112 RGB",
  );

  const luma = (x: number, y: number): number =>
    aligned[(Math.round(y - 0.5) * ALIGNED_FACE_SIZE + Math.round(x - 0.5)) * 3];

  ARCFACE_TEMPLATE_112.forEach(([x, y], index) => {
    assert(
      luma(x, y) >= 180,
      `landmark ${index} lands on its template point at ${degrees} degrees (got ${luma(x, y)})`,
    );
  });
  // Negative control: a spot with no landmark near it must stay black, or the
  // assertions above would also pass on an all-white buffer.
  assert(luma(20, 20) <= 40, `the aligned background stays dark at ${degrees} degrees`);
}

// ── Scale covariance: same answer from the frame as from the full photo ──────
// The scan embeds some faces from the downscaled frame and some from the
// original. Both must land on the same patch pixels, or half the library gets
// embeddings that are wrong by a constant.
{
  const factor = 0.375; // 1280 / 3413, the shape of a real scan frame.
  const { landmarks, points } = syntheticFace(15, 3, 320, 320);
  const box = boxAround(points);
  const full = alignedPatchGeometry({ width: IMAGE_SIZE, height: IMAGE_SIZE }, box, landmarks);

  const scalePoint = (point: { x: number; y: number }) => ({
    x: point.x * factor,
    y: point.y * factor,
  });
  const framedLandmarks: FaceLandmarks5 = {
    rightEye: scalePoint(landmarks.rightEye),
    leftEye: scalePoint(landmarks.leftEye),
    noseBase: scalePoint(landmarks.noseBase as { x: number; y: number }),
    rightMouth: scalePoint(landmarks.rightMouth as { x: number; y: number }),
    leftMouth: scalePoint(landmarks.leftMouth as { x: number; y: number }),
  };
  const framed = alignedPatchGeometry(
    { width: IMAGE_SIZE * factor, height: IMAGE_SIZE * factor },
    {
      x: box.x * factor,
      y: box.y * factor,
      width: box.width * factor,
      height: box.height * factor,
    },
    framedLandmarks,
  );
  assert(full && framed, "both spaces resolve a geometry");
  near(framed.patchSize, full.patchSize, 1, "the frame and the photo agree on the patch resolution");

  const fullPatch = landmarksToPatch(landmarks, full);
  const framedPatch = landmarksToPatch(framedLandmarks, framed);
  for (const name of ["leftEye", "rightEye", "noseBase", "leftMouth", "rightMouth"] as const) {
    const expected = fullPatch[name];
    const actual = framedPatch[name];
    assert(expected && actual, `${name} survives both mappings`);
    near(actual.x, expected.x, 1.5, `${name} lands on the same patch column`);
    near(actual.y, expected.y, 1.5, `${name} lands on the same patch row`);
  }
  // Negative control for the covariance check itself: forgetting to rescale the
  // landmarks must be detectable.
  const wrong = landmarksToPatch(landmarks, framed);
  assert(
    Math.abs(wrong.leftEye.x - fullPatch.leftEye.x) > 2,
    "unscaled landmarks in frame space must not agree",
  );
}

// ── A face at the frame border stays inside the image ────────────────────────
{
  const { landmarks, points } = syntheticFace(0, 2, 4, 6);
  const asset = { width: 300, height: 300 };
  const geometry = alignedPatchGeometry(asset, boxAround(points), landmarks);
  assert(geometry, "a border face still resolves a geometry");
  assert(geometry.originX >= 0 && geometry.originY >= 0, "the crop origin never goes negative");
  assert(
    geometry.originX + geometry.size <= asset.width &&
      geometry.originY + geometry.size <= asset.height,
    "the crop never runs past the image",
  );
}

// ── Fails closed rather than cropping pixels nothing can align ───────────────
{
  const { landmarks, points } = syntheticFace(0, 3, 320, 320);
  const box = boxAround(points);
  assert(
    alignedPatchGeometry({ width: 640, height: 640 }, box, {
      rightEye: { x: Number.NaN, y: 0 },
      leftEye: landmarks.leftEye,
    }) === undefined,
    "unusable eyes reject the geometry before any crop is paid for",
  );
  assert(
    alignedPatchGeometry({ width: 640, height: 640 }, box, {
      rightEye: { x: 100, y: 100 },
      leftEye: { x: 100, y: 100 },
    }) === undefined,
    "collapsed landmarks reject the geometry",
  );
  assert(
    alignedPatchGeometry({ width: 0, height: 640 }, box, landmarks) === undefined,
    "an invalid image rejects the geometry",
  );
}

// eslint-disable-next-line no-console
console.log("face-crop self-check passed");
