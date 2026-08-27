/**
 * Analysis width for pixel-level quality, in pixels.
 *
 * The previous value was 64, which made the sharpness score close to useless:
 * downsampling to 64 px low-passes away exactly the high-frequency band that
 * *is* the focus signal. Measured on real 8256x5504 DSLR frames (area resample
 * + the same JPEG round-trip this function performs), the ratio between a
 * tack-sharp frame and a visibly defocused one was:
 *
 *     64 px -> 1.1x     256 px -> 4.1x     384 px -> 10.6x
 *    512 px -> 21.5x    768 px -> 47.1x
 *
 * At 64 px a blurred photo scored 88% of a sharp one, i.e. noise. 512 px buys
 * ~20x separation for one decode, and is the point where the curve stops paying
 * for itself relative to decode cost and memory. We constrain WIDTH rather than
 * the long edge so that every photo is sampled at the same angular density
 * regardless of orientation (a portrait frame at width 512 becomes 512x768, so
 * the long edge is always >= 512 as required). Worst realistic cost is a
 * 512x2048 panorama at ~4 MB RGBA; a normal 3:2 frame is ~0.7 MB.
 */
const QUALITY_SAMPLE_WIDTH = 512;
/**
 * Full scale for the Laplacian *standard deviation*, in luma levels (0..255).
 *
 * Note this is a standard deviation, not the variance the old constant scaled.
 * Laplacian variance is a squared quantity with a heavy tail, and at 512 px the
 * observed spread forces the choice: sharp frames measured p10=818, p50=1253,
 * p90=1756 variance, while mild softness sat at 624. No linear divisor can put
 * p10 clear of the planner's 0.35 quality floor (needs divisor <= ~1818) and
 * also keep p90 off the clamp (needs >= ~2066). Taking the square root first
 * removes that conflict and yields a value in luma units, which is both easier
 * to reason about and half as sensitive to any residual scale error.
 *
 * With full scale at 50 luma levels the measured frames land:
 *   sharp p10 0.57 / p50 0.71 / p90 0.84, mild softness 0.50,
 *   visibly soft 0.32, clearly blurred 0.15, heavily blurred 0.07.
 * Nothing real clamps, so ranking is preserved across the whole sharp range,
 * and "visibly soft" is the first bucket to fall under a 0.35 floor.
 */
const LAPLACIAN_STDDEV_FULL_SCALE = 50;
const BASE64_ALPHABET =
  "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";

export type MeasuredImageQuality = {
  sharpness?: number;
  exposure?: number;
  clippedFraction?: number;
  /**
   * The 4x3 blurhash the cheap probe already computed, kept rather than thrown
   * away once its quality numbers were extracted.
   *
   * It is the only description of what a photo LOOKS like that exists before
   * the heavy models run, which makes it the one signal the candidate prepass
   * can use to avoid handing the planner sixty-four frames of the same moment.
   * Present only on the probe path; the uncapped path never needs it.
   */
  blurhash?: string;
  /** Exact focus inside the detected face box. */
  faceSharpness?: number;
  /**
   * Sharpness across an expanded dominant-subject region: hair above the face,
   * shoulders, arms and upper torso below it. The detector currently supplies
   * a face box, so this conservative region is the only subject evidence that
   * exists without pretending the sharp background says anything about limbs.
   */
  subjectSharpness?: number;
  /**
   * Where the detail sits, as subjectStdDev / (subjectStdDev + backgroundStdDev).
   * 0.5 means subject and background are equally sharp, > 0.5 means the subject
   * is sharper than its background (deliberate bokeh — a good portrait), and
   * < 0.5 means the background won (missed focus — a genuine reject signal).
   * Bounded rather than a raw quotient so a flat background cannot divide by ~0.
   */
  subjectBackgroundRatio?: number;
};

/** A rectangle in the pixel coordinates of the buffer being measured. */
export type PixelRegion = {
  x: number;
  y: number;
  width: number;
  height: number;
};

/**
 * A subject/face rectangle in normalized 0..1 coordinates of the source image.
 * Normalized rather than pixels because this module picks its own analysis
 * resolution, which is not the resolution the caller detected faces at.
 */
export type NormalizedBox = PixelRegion;

export type MeasureImageQualityOptions = {
  subjectBox?: NormalizedBox;
  /**
   * Offline decoder seam for deterministic evaluation.
   *
   * Production callers leave this unset and use Expo's one-decode proxy path.
   * The standing quality gate supplies decoded fixture pixels here so it can
   * exercise this exact measurement function without an Android/iOS runtime.
   */
  imageLoader?: QualityImageLoader;
  /**
   * Called when the decode failed and `{}` is about to be returned.
   *
   * `{}` is also what a caller gets for an image with no measurable pixels, so
   * the return value alone cannot tell a failure from a legitimate blank. That
   * ambiguity is why a decode dying of `OutOfMemoryError` cost the album a
   * sharpness score and left no record: the caller substituted the blurhash
   * prior -- which this file documents reads ~0.05 sharpness on ANY photo --
   * and the frame quietly sank below the planner's quality floor.
   */
  onDegraded?: (error: unknown) => void;
};

export type LoadedQualityImage = {
  rgba: Uint8Array;
  width: number;
  height: number;
  cleanup?: () => Promise<void>;
};

export type QualityImageLoader = (
  uri: string,
  targetWidth: number,
) => Promise<LoadedQualityImage>;

type LaplacianStats = {
  total: number;
  totalSquared: number;
  count: number;
};

/**
 * Estimate focus/detail using the variance of a four-neighbour 3x3 Laplacian.
 *
 * Pass `region` to measure only part of the buffer (see `subjectSharpness`).
 * Omitting it measures the whole frame, which is the original behaviour.
 */
export function sharpnessFromPixels(
  gray: Uint8Array | number[],
  width: number,
  height: number,
  region?: PixelRegion,
): number {
  if (!hasCompleteBuffer(gray, width, height) || width < 3 || height < 3) {
    return 0;
  }

  // `inside` already holds the whole frame when no usable region was given, so
  // an unusable or off-image box degrades to the frame score rather than to 0.
  const { inside } = laplacianStats(gray, width, height, region);
  return normalizeSharpness(varianceOf(inside));
}

export type SubjectImageQuality = Pick<
  MeasuredImageQuality,
  "faceSharpness" | "subjectSharpness" | "subjectBackgroundRatio"
>;

/**
 * Measure the face and its surrounding upper-body region separately.
 *
 * A face detector deliberately returns a tight skin/feature box. Hair, hands
 * and shoulders sit outside it, yet motion blur there is conspicuous in a
 * social post. Expanding 0.75 face widths sideways, 0.45 heights upward and
 * 3.8 heights downward covers that visible portrait subject while remaining
 * bounded by the image. This is a proxy, not segmentation: a future body mask
 * can replace the region without changing the selection contract.
 */
export function subjectQualityFromPixels(
  gray: Uint8Array | number[],
  width: number,
  height: number,
  faceRegion: PixelRegion | undefined,
): SubjectImageQuality {
  return subjectQualityFromPixelStats(gray, width, height, faceRegion);
}

function subjectQualityFromPixelStats(
  gray: Uint8Array | number[],
  width: number,
  height: number,
  faceRegion: PixelRegion | undefined,
  measuredFace?: ReturnType<typeof laplacianStats>,
): SubjectImageQuality {
  if (
    !faceRegion ||
    !hasCompleteBuffer(gray, width, height) ||
    width < 3 ||
    height < 3
  ) {
    return {};
  }

  const face = measuredFace ?? laplacianStats(gray, width, height, faceRegion);
  const subjectRegion = expandedSubjectRegion(faceRegion);
  const subject = laplacianStats(gray, width, height, subjectRegion);
  if (face.outside.count === 0 || subject.outside.count === 0) {
    return {};
  }

  const subjectStdDev = Math.sqrt(varianceOf(subject.inside));
  const backgroundStdDev = Math.sqrt(varianceOf(subject.outside));
  return {
    faceSharpness: normalizeSharpness(varianceOf(face.inside)),
    subjectSharpness: normalizeSharpness(varianceOf(subject.inside)),
    ...(subjectStdDev + backgroundStdDev > 0
      ? {
          subjectBackgroundRatio: clamp01(
            subjectStdDev / (subjectStdDev + backgroundStdDev),
          ),
        }
      : {}),
  };
}

function expandedSubjectRegion(face: PixelRegion): PixelRegion {
  return {
    x: face.x - face.width * 0.75,
    y: face.y - face.height * 0.45,
    width: face.width * 2.5,
    height: face.height * 4.25,
  };
}

/**
 * Accumulate Laplacian moments in one pass, split by whether each sample falls
 * inside `region`. One pass because this runs per photo across a whole library;
 * measuring the subject and the frame separately would otherwise double the
 * per-pixel work. Variance for any combination is recoverable from the sums.
 */
function laplacianStats(
  gray: Uint8Array | number[],
  width: number,
  height: number,
  region?: PixelRegion,
): { inside: LaplacianStats; outside: LaplacianStats } {
  const inside: LaplacianStats = { total: 0, totalSquared: 0, count: 0 };
  const outside: LaplacianStats = { total: 0, totalSquared: 0, count: 0 };
  const bounds = region ? interiorBounds(region, width, height) : undefined;

  for (let y = 1; y < height - 1; y += 1) {
    const withinRows =
      bounds !== undefined && y >= bounds.y0 && y < bounds.y1;
    for (let x = 1; x < width - 1; x += 1) {
      const center = pixelAt(gray, y * width + x);
      const laplacian =
        pixelAt(gray, (y - 1) * width + x) +
        pixelAt(gray, (y + 1) * width + x) +
        pixelAt(gray, y * width + x - 1) +
        pixelAt(gray, y * width + x + 1) -
        4 * center;

      const target =
        withinRows && x >= bounds!.x0 && x < bounds!.x1 ? inside : outside;
      target.total += laplacian;
      target.totalSquared += laplacian * laplacian;
      target.count += 1;
    }
  }

  // With no region every sample is "outside"; fold it into `inside` so callers
  // that ignore the split still read the whole-frame statistic from either one.
  return bounds === undefined
    ? { inside: combine(inside, outside), outside: { total: 0, totalSquared: 0, count: 0 } }
    : { inside, outside };
}

function combine(left: LaplacianStats, right: LaplacianStats): LaplacianStats {
  return {
    total: left.total + right.total,
    totalSquared: left.totalSquared + right.totalSquared,
    count: left.count + right.count,
  };
}

function varianceOf(stats: LaplacianStats): number {
  if (stats.count === 0) {
    return 0;
  }
  const mean = stats.total / stats.count;
  return Math.max(0, stats.totalSquared / stats.count - mean * mean);
}

function normalizeSharpness(variance: number): number {
  return clamp01(Math.sqrt(variance) / LAPLACIAN_STDDEV_FULL_SCALE);
}

/** Clip a region to the pixels where a 3x3 Laplacian actually has neighbours. */
function interiorBounds(
  region: PixelRegion,
  width: number,
  height: number,
): { x0: number; x1: number; y0: number; y1: number } | undefined {
  const values = [region.x, region.y, region.width, region.height];
  if (!values.every((value) => Number.isFinite(value))) {
    return undefined;
  }

  const x0 = Math.max(1, Math.floor(region.x));
  const y0 = Math.max(1, Math.floor(region.y));
  const x1 = Math.min(width - 1, Math.ceil(region.x + region.width));
  const y1 = Math.min(height - 1, Math.ceil(region.y + region.height));
  return x1 - x0 >= 1 && y1 - y0 >= 1 ? { x0, x1, y0, y1 } : undefined;
}

/** Mean luma is exposure; luma at 0..4 or 251..255 is counted as clipped. */
export function exposureFromPixels(
  gray: Uint8Array | number[],
  width: number,
  height: number,
): { exposure: number; clippedFraction: number } {
  if (!hasCompleteBuffer(gray, width, height)) {
    return { exposure: 0.5, clippedFraction: 0 };
  }

  const pixelCount = width * height;
  let lumaTotal = 0;
  let clippedCount = 0;

  for (let index = 0; index < pixelCount; index += 1) {
    const luma = pixelAt(gray, index);
    lumaTotal += luma;
    if (luma <= 4 || luma >= 251) {
      clippedCount += 1;
    }
  }

  return {
    exposure: clamp01(lumaTotal / pixelCount / 255),
    clippedFraction: clippedCount / pixelCount,
  };
}

/**
 * Derive a quality floor from the photos actually in hand, keeping at least
 * `keepFraction` of them.
 *
 * Consumers should rank photos WITHIN a cluster/take rather than against an
 * absolute global threshold. No-reference IQA scores like the sharpness above
 * are content-dependent: a low-light candlelit set, a soft-focus portrait
 * series and a bright outdoor set do not share a scale, so any fixed constant
 * is simultaneously too harsh for one library and too lenient for another. A
 * fixed floor applied to a whole library can reject every photo and produce an
 * empty album, which is never the right answer — the user asked for their best
 * photos, not for a verdict on whether their photos are good enough.
 *
 * Feeding the cluster's own quality values through this function makes that
 * outcome structurally impossible: the returned floor is itself one of the
 * observed values, so something always survives. Returns 0 (reject nothing)
 * when nothing measurable was supplied.
 */
export function relativeQualityFloor(
  values: ReadonlyArray<number | undefined>,
  keepFraction = 0.5,
): number {
  const measured = values
    .filter((value): value is number =>
      typeof value === "number" && Number.isFinite(value),
    )
    .sort((left, right) => left - right);
  if (measured.length === 0) {
    return 0;
  }

  const keep = Math.min(1, Math.max(0, keepFraction));
  // Round the kept count up so keepFraction is a guarantee, not a target, and
  // clamp to at least one survivor even at keepFraction 0.
  const keptCount = Math.max(1, Math.ceil(measured.length * keep));
  return measured[measured.length - keptCount];
}

/**
 * Decode one bounded JPEG proxy and measure it locally. Native/decode failures
 * are deliberately non-fatal so callers can omit these optional quality signals.
 *
 * Exactly one decode happens per photo and every signal (sharpness, subject
 * sharpness, exposure, clipping) is read from that single grayscale buffer,
 * because this runs across a whole library.
 *
 * `options.subjectBox` is a face/subject rectangle in normalized 0..1 source
 * coordinates. Supplying it is purely additive: the returned `sharpness`,
 * `exposure` and `clippedFraction` are unchanged.
 */
export async function measureImageQuality(
  uri: string,
  options: MeasureImageQualityOptions = {},
): Promise<MeasuredImageQuality> {
  let loaded: LoadedQualityImage | undefined;
  try {
    loaded = await (options.imageLoader ?? loadQualityImageWithExpo)(
      uri,
      QUALITY_SAMPLE_WIDTH,
    );
    if (loaded.width < 1 || loaded.height < 1 || loaded.rgba.length < 4) {
      return {};
    }

    const gray = rgbaToGrayscale(
      loaded.rgba,
      loaded.width,
      loaded.height,
    );
    const exposure = exposureFromPixels(gray, loaded.width, loaded.height);
    const region = toPixelRegion(
      options.subjectBox,
      loaded.width,
      loaded.height,
    );
    const { inside, outside } = laplacianStats(
      gray,
      loaded.width,
      loaded.height,
      region,
    );
    // Reuse the exact-face split for full-frame sharpness, then measure the
    // expanded subject independently so blurred hair/body cannot hide in the
    // frame average.
    const subjectQuality = subjectQualityFromPixelStats(
      gray,
      loaded.width,
      loaded.height,
      region,
      { inside, outside },
    );

    return {
      sharpness: normalizeSharpness(varianceOf(combine(inside, outside))),
      exposure: exposure.exposure,
      clippedFraction: exposure.clippedFraction,
      ...subjectQuality,
    };
  } catch (error) {
    try {
      options.onDegraded?.(error);
    } catch {
      // Measurement stays fail-neutral even when its own reporting fails.
    }
    return {};
  } finally {
    if (loaded?.cleanup) {
      try {
        await loaded.cleanup();
      } catch {
        // Best-effort cache cleanup; measurement must stay fail-neutral.
      }
    }
  }
}

/** Production decoder: one Expo resize/JPEG round-trip, exactly as before. */
async function loadQualityImageWithExpo(
  uri: string,
  targetWidth: number,
): Promise<LoadedQualityImage> {
  const [{ manipulateAsync, SaveFormat }, { decode: decodeJpeg }] =
    await Promise.all([
      import("expo-image-manipulator"),
      import("jpeg-js"),
    ]);
  const thumbnail = await manipulateAsync(
    uri,
    [{ resize: { width: targetWidth } }],
    {
      base64: true,
      compress: 0.9,
      format: SaveFormat.JPEG,
    },
  );
  const cleanup = async (): Promise<void> => {
    const { deleteAsync } = await import("expo-file-system/legacy");
    await deleteAsync(thumbnail.uri, { idempotent: true });
  };

  try {
    if (!thumbnail.base64) {
      return { rgba: new Uint8Array(), width: 0, height: 0, cleanup };
    }

    const decoded = decodeJpeg(decodeBase64(thumbnail.base64), {
      useTArray: true,
      formatAsRGBA: true,
      tolerantDecoding: true,
      // Raised from 1 MP / 8 MB along with the analysis width. At width 512 a
      // normal frame is ~0.35 MP, but a tall panorama can reach ~1 MP and would
      // otherwise trip the old guard and silently lose its quality signals.
      // Still a hard bound: the resize above fixes the width, so only the
      // aspect ratio varies, and 4 MP covers up to roughly 15:1.
      maxResolutionInMP: 4,
      maxMemoryUsageInMB: 48,
    });
    return {
      rgba: decoded.data,
      width: decoded.width,
      height: decoded.height,
      cleanup,
    };
  } catch (error) {
    await cleanup().catch(() => undefined);
    throw error;
  }
}

/** Map a normalized 0..1 subject box onto the buffer we actually decoded. */
function toPixelRegion(
  box: NormalizedBox | undefined,
  width: number,
  height: number,
): PixelRegion | undefined {
  if (!box) {
    return undefined;
  }
  const values = [box.x, box.y, box.width, box.height];
  if (!values.every((value) => Number.isFinite(value))) {
    return undefined;
  }
  if (box.width <= 0 || box.height <= 0) {
    return undefined;
  }

  return {
    x: box.x * width,
    y: box.y * height,
    width: box.width * width,
    height: box.height * height,
  };
}

function rgbaToGrayscale(
  rgba: Uint8Array,
  width: number,
  height: number,
): Uint8Array {
  const pixelCount = width * height;
  const gray = new Uint8Array(pixelCount);

  for (let index = 0; index < pixelCount; index += 1) {
    const offset = index * 4;
    gray[index] = Math.round(
      rgba[offset] * 0.2126 +
        rgba[offset + 1] * 0.7152 +
        rgba[offset + 2] * 0.0722,
    );
  }

  return gray;
}

function hasCompleteBuffer(
  gray: Uint8Array | number[],
  width: number,
  height: number,
): boolean {
  return (
    Number.isInteger(width) &&
    Number.isInteger(height) &&
    width > 0 &&
    height > 0 &&
    gray.length >= width * height
  );
}

function pixelAt(gray: Uint8Array | number[], index: number): number {
  const value = gray[index];
  return Number.isFinite(value) ? Math.max(0, Math.min(255, value)) : 0;
}

function decodeBase64(value: string): Uint8Array {
  const encoded = value.replace(/^data:[^,]*,/u, "").replace(/\s/gu, "");
  const padding = encoded.endsWith("==") ? 2 : encoded.endsWith("=") ? 1 : 0;
  const bytes = new Uint8Array(
    Math.max(0, Math.floor((encoded.length * 3) / 4) - padding),
  );
  let accumulator = 0;
  let availableBits = 0;
  let byteIndex = 0;

  for (const character of encoded) {
    if (character === "=") {
      break;
    }
    const digit = BASE64_ALPHABET.indexOf(character);
    if (digit < 0) {
      throw new Error("Thumbnail contains invalid base64 data.");
    }

    accumulator = (accumulator << 6) | digit;
    availableBits += 6;
    if (availableBits >= 8) {
      availableBits -= 8;
      bytes[byteIndex] = (accumulator >>> availableBits) & 0xff;
      byteIndex += 1;
      accumulator &= availableBits === 0 ? 0 : (1 << availableBits) - 1;
    }
  }

  if (byteIndex !== bytes.length || bytes.length === 0) {
    throw new Error("Thumbnail base64 data is incomplete.");
  }

  return bytes;
}

function clamp01(value: number): number {
  return Math.max(0, Math.min(1, value));
}
