const QUALITY_SAMPLE_WIDTH = 64;
const LAPLACIAN_VARIANCE_FULL_SCALE = 20_000;
const BASE64_ALPHABET =
  "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";

export type MeasuredImageQuality = {
  sharpness?: number;
  exposure?: number;
  clippedFraction?: number;
};

/**
 * Estimate focus/detail using the variance of a four-neighbour 3x3 Laplacian.
 *
 * A variance of 20,000 is treated as full scale. At the 64 px analysis size,
 * that puts crisp alternating edges near 1, soft gradients near 0, and leaves
 * useful separation between ordinary handheld blur and focused photographs.
 */
export function sharpnessFromPixels(
  gray: Uint8Array | number[],
  width: number,
  height: number,
): number {
  if (!hasCompleteBuffer(gray, width, height) || width < 3 || height < 3) {
    return 0;
  }

  let total = 0;
  let totalSquared = 0;
  let sampleCount = 0;

  for (let y = 1; y < height - 1; y += 1) {
    for (let x = 1; x < width - 1; x += 1) {
      const center = pixelAt(gray, y * width + x);
      const laplacian =
        pixelAt(gray, (y - 1) * width + x) +
        pixelAt(gray, (y + 1) * width + x) +
        pixelAt(gray, y * width + x - 1) +
        pixelAt(gray, y * width + x + 1) -
        4 * center;

      total += laplacian;
      totalSquared += laplacian * laplacian;
      sampleCount += 1;
    }
  }

  if (sampleCount === 0) {
    return 0;
  }

  const mean = total / sampleCount;
  const variance = Math.max(0, totalSquared / sampleCount - mean * mean);
  return clamp01(variance / LAPLACIAN_VARIANCE_FULL_SCALE);
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
 * Decode a small JPEG proxy and measure it locally. Native/decode failures are
 * deliberately non-fatal so callers can omit these optional quality signals.
 */
export async function measureImageQuality(
  uri: string,
): Promise<MeasuredImageQuality> {
  try {
    const [{ manipulateAsync, SaveFormat }, { decode: decodeJpeg }] =
      await Promise.all([
        import("expo-image-manipulator"),
        import("jpeg-js"),
      ]);
    const thumbnail = await manipulateAsync(
      uri,
      [{ resize: { width: QUALITY_SAMPLE_WIDTH } }],
      {
        base64: true,
        compress: 0.9,
        format: SaveFormat.JPEG,
      },
    );

    if (!thumbnail.base64) {
      return {};
    }

    const decoded = decodeJpeg(decodeBase64(thumbnail.base64), {
      useTArray: true,
      formatAsRGBA: true,
      tolerantDecoding: true,
      maxResolutionInMP: 1,
      maxMemoryUsageInMB: 8,
    });
    if (decoded.width < 1 || decoded.height < 1 || decoded.data.length < 4) {
      return {};
    }

    const gray = rgbaToGrayscale(
      decoded.data,
      decoded.width,
      decoded.height,
    );
    const exposure = exposureFromPixels(gray, decoded.width, decoded.height);
    return {
      sharpness: sharpnessFromPixels(gray, decoded.width, decoded.height),
      exposure: exposure.exposure,
      clippedFraction: exposure.clippedFraction,
    };
  } catch {
    return {};
  }
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
