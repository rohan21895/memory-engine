// @ts-expect-error Node's native TypeScript runner requires the extension.
import { exposureFromPixels, sharpnessFromPixels, type MeasuredImageQuality } from "./image-quality.ts";

const PROBE_SIZE = 32;
// Matches QUALITY_SAMPLE_WIDTH in image-quality.ts: sharpness needs >=512px to
// discriminate focus at all (at 256 a blurred frame still scores ~4x too high).
const ANALYSIS_PROXY_SIZE = 512;
const DECODE_WIDTH = 16;
const DECODE_HEIGHT = 12;
const BASE83 =
  "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz#$%*+,-.:;=?@[]^_{|}~";

/**
 * Ask the platform image loader for a genuinely tiny thumbnail before reading
 * pixels. On Android this lets Glide/MediaStore subsample during decode instead
 * of image-manipulator materializing and JPEG-encoding a full source image.
 *
 * IMPORTANT: the `sharpness` this returns is a RELATIVE ranking prior, not a
 * calibrated sharpness, and must never be compared against an absolute floor.
 * It comes from a 4x3-component blurhash, which by construction holds no high
 * frequencies at all, so real photos land around 0.04-0.06 no matter how sharp
 * they are. That is fine for choosing which candidates deserve heavy analysis
 * (only the ordering is used), but a caller that forwards this value on as the
 * final quality signal will fail every absolute threshold downstream. Use
 * `measureImageQuality` for any value that gets thresholded.
 */
export async function probeCandidateQuality(
  uri: string,
): Promise<MeasuredImageQuality> {
  try {
    const { Image } = await import("expo-image");
    const image = await Image.loadAsync(uri, {
      maxHeight: PROBE_SIZE,
      maxWidth: PROBE_SIZE,
    });
    try {
      const blurhash = await Image.generateBlurhashAsync(image, [4, 3]);
      if (!blurhash) return {};
      return qualityFromBlurhash(blurhash);
    } finally {
      image.release();
    }
  } catch {
    return {};
  }
}

export type CandidateAnalysisProxy = {
  uri: string;
  width: number;
  height: number;
};

/**
 * Materialize one bounded proxy that every heavy model can safely share. This
 * prevents four independent preprocessors from decoding a 50–130 MP original
 * into the Android Java heap.
 */
export async function prepareCandidateAnalysisProxy(
  uri: string,
): Promise<CandidateAnalysisProxy | undefined> {
  try {
    const [{ Image }, { ImageManipulator, SaveFormat }] = await Promise.all([
      import("expo-image"),
      import("expo-image-manipulator"),
    ]);
    const source = await Image.loadAsync(uri, {
      maxHeight: ANALYSIS_PROXY_SIZE,
      maxWidth: ANALYSIS_PROXY_SIZE,
    });
    try {
      const context = ImageManipulator.manipulate(source);
      try {
        const rendered = await context.renderAsync();
        try {
          const saved = await rendered.saveAsync({
            compress: 0.86,
            format: SaveFormat.JPEG,
          });
          return {
            uri: saved.uri,
            width: saved.width,
            height: saved.height,
          };
        } finally {
          rendered.release();
        }
      } finally {
        context.release();
      }
    } finally {
      source.release();
    }
  } catch {
    return undefined;
  }
}

export async function removeCandidateAnalysisProxy(
  proxy: CandidateAnalysisProxy | undefined,
): Promise<void> {
  if (!proxy) return;
  try {
    const FileSystem = await import("expo-file-system/legacy");
    await FileSystem.deleteAsync(proxy.uri, { idempotent: true });
  } catch {
    // Cache cleanup is best-effort; the OS can evict the same file later.
  }
}

export function qualityFromBlurhash(
  blurhash: string,
): MeasuredImageQuality {
  const gray = decodeBlurhashGrayscale(
    blurhash,
    DECODE_WIDTH,
    DECODE_HEIGHT,
  );
  if (!gray) return {};
  const exposure = exposureFromPixels(gray, DECODE_WIDTH, DECODE_HEIGHT);
  return {
    sharpness: sharpnessFromPixels(gray, DECODE_WIDTH, DECODE_HEIGHT),
    exposure: exposure.exposure,
    clippedFraction: exposure.clippedFraction,
  };
}

export function decodeBlurhashGrayscale(
  blurhash: string,
  width: number,
  height: number,
): Uint8Array | undefined {
  if (blurhash.length < 6 || width < 1 || height < 1) return undefined;
  const sizeFlag = decode83(blurhash[0]);
  if (sizeFlag < 0) return undefined;
  const componentX = (sizeFlag % 9) + 1;
  const componentY = Math.floor(sizeFlag / 9) + 1;
  const expectedLength = 4 + 2 * componentX * componentY;
  if (blurhash.length !== expectedLength) return undefined;

  const quantizedMaximum = decode83(blurhash[1]);
  const dc = decode83(blurhash.slice(2, 6));
  if (quantizedMaximum < 0 || dc < 0) return undefined;
  const maximum = (quantizedMaximum + 1) / 166;
  const colors: Array<readonly [number, number, number]> = [decodeDc(dc)];

  for (let index = 1; index < componentX * componentY; index += 1) {
    const value = decode83(blurhash.slice(4 + index * 2, 6 + index * 2));
    if (value < 0) return undefined;
    colors.push(decodeAc(value, maximum));
  }

  const gray = new Uint8Array(width * height);
  for (let y = 0; y < height; y += 1) {
    for (let x = 0; x < width; x += 1) {
      let red = 0;
      let green = 0;
      let blue = 0;
      for (let componentYIndex = 0; componentYIndex < componentY; componentYIndex += 1) {
        for (let componentXIndex = 0; componentXIndex < componentX; componentXIndex += 1) {
          const basis =
            Math.cos((Math.PI * x * componentXIndex) / width) *
            Math.cos((Math.PI * y * componentYIndex) / height);
          const color = colors[componentXIndex + componentYIndex * componentX];
          red += color[0] * basis;
          green += color[1] * basis;
          blue += color[2] * basis;
        }
      }
      gray[x + y * width] = Math.round(
        linearToSrgb(red) * 0.2126 +
          linearToSrgb(green) * 0.7152 +
          linearToSrgb(blue) * 0.0722,
      );
    }
  }
  return gray;
}

function decodeDc(value: number): readonly [number, number, number] {
  return [
    srgbToLinear(value >> 16),
    srgbToLinear((value >> 8) & 255),
    srgbToLinear(value & 255),
  ];
}

function decodeAc(
  value: number,
  maximum: number,
): readonly [number, number, number] {
  const red = Math.floor(value / (19 * 19));
  const green = Math.floor(value / 19) % 19;
  const blue = value % 19;
  return [
    signedSquare((red - 9) / 9) * maximum,
    signedSquare((green - 9) / 9) * maximum,
    signedSquare((blue - 9) / 9) * maximum,
  ];
}

function decode83(value: string): number {
  let result = 0;
  for (const character of value) {
    const digit = BASE83.indexOf(character);
    if (digit < 0) return -1;
    result = result * 83 + digit;
  }
  return result;
}

function srgbToLinear(value: number): number {
  const normalized = value / 255;
  return normalized <= 0.04045
    ? normalized / 12.92
    : ((normalized + 0.055) / 1.055) ** 2.4;
}

function linearToSrgb(value: number): number {
  const normalized = Math.max(0, Math.min(1, value));
  return 255 *
    (normalized <= 0.0031308
      ? normalized * 12.92
      : 1.055 * normalized ** (1 / 2.4) - 0.055);
}

function signedSquare(value: number): number {
  return Math.sign(value) * value * value;
}
