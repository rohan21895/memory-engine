// @ts-expect-error The Expo app deliberately does not ship Node declarations.
import { readFileSync } from "node:fs";
// @ts-expect-error The Expo app deliberately does not ship Node declarations.
import { spawnSync } from "node:child_process";

import type {
  LoadedQualityImage,
  NormalizedBox,
  QualityImageLoader,
} from "../selection/image-quality";

export type PixelCrop = {
  x: number;
  y: number;
  width: number;
  height: number;
};

export type QualityFixture = {
  id: string;
  crop: PixelCrop;
  face: NormalizedBox;
};

export type QualityFixtureManifest = {
  source: string;
  fixtures: QualityFixture[];
};

export type FixtureDegradation =
  | { kind: "none" }
  | { kind: "blur"; sigma: number }
  | { kind: "exposure"; ev: number }
  | { kind: "face-crop" };

type RgbaImage = {
  rgba: Uint8Array;
  width: number;
  height: number;
};

export function readQualityFixtureManifest(
  path: string,
): QualityFixtureManifest {
  const parsed: unknown = JSON.parse(readFileSync(path, "utf8"));
  if (!isRecord(parsed) || typeof parsed.source !== "string") {
    throw new Error(`Invalid quality fixture manifest: ${path}`);
  }
  if (!Array.isArray(parsed.fixtures) || !parsed.fixtures.every(validFixture)) {
    throw new Error(`Quality fixture manifest has invalid fixtures: ${path}`);
  }
  return parsed as QualityFixtureManifest;
}

export function decodeFixtureSource(path: string): RgbaImage {
  const probe = spawnSync(
    "ffprobe",
    [
      "-v",
      "error",
      "-select_streams",
      "v:0",
      "-show_entries",
      "stream=width,height",
      "-of",
      "json",
      path,
    ],
    { encoding: "utf8", maxBuffer: 1024 * 1024 },
  );
  if (probe.status !== 0) {
    throw new Error(`ffprobe could not inspect fixture ${path}: ${probe.stderr}`);
  }
  const metadata: unknown = JSON.parse(probe.stdout);
  const stream =
    isRecord(metadata) && Array.isArray(metadata.streams)
      ? metadata.streams[0]
      : undefined;
  if (
    !isRecord(stream) ||
    typeof stream.width !== "number" ||
    typeof stream.height !== "number" ||
    stream.width < 1 ||
    stream.height < 1
  ) {
    throw new Error(`Fixture has no readable video/image stream: ${path}`);
  }
  const decode = spawnSync(
    "ffmpeg",
    ["-v", "error", "-i", path, "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgba", "pipe:1"],
    { encoding: null, maxBuffer: 128 * 1024 * 1024 },
  );
  if (decode.status !== 0) {
    throw new Error(`ffmpeg could not decode fixture ${path}: ${String(decode.stderr)}`);
  }
  const rgba = new Uint8Array(
    decode.stdout.buffer,
    decode.stdout.byteOffset,
    decode.stdout.byteLength,
  ).slice();
  if (rgba.length < stream.width * stream.height * 4) {
    throw new Error(`Fixture image could not be decoded: ${path}`);
  }
  return {
    rgba,
    width: stream.width,
    height: stream.height,
  };
}

export function qualityFixtureLoader(
  source: RgbaImage,
  fixture: QualityFixture,
  degradation: FixtureDegradation,
): QualityImageLoader {
  return async (_uri, targetWidth): Promise<LoadedQualityImage> => {
    let image = normalizeExposure(cropImage(source, fixture.crop));
    if (degradation.kind === "blur") {
      image = gaussianBlur(image, degradation.sigma);
    } else if (degradation.kind === "exposure") {
      image = shiftExposure(image, degradation.ev);
    } else if (degradation.kind === "face-crop") {
      image = cropThroughFace(image, fixture.face);
    }
    const resized = resizeBilinear(image, targetWidth);
    return {
      rgba: resized.rgba,
      width: resized.width,
      height: resized.height,
    };
  };
}

/** Face box remaining after a crop whose left edge bisects the main face. */
export function croppedFaceBox(face: NormalizedBox): NormalizedBox {
  const cropStart = clamp01(face.x + face.width / 2);
  const remaining = Math.max(Number.EPSILON, 1 - cropStart);
  return {
    x: 0,
    y: face.y,
    width: Math.max(0.001, face.width / 2 / remaining),
    height: face.height,
  };
}

function cropThroughFace(image: RgbaImage, face: NormalizedBox): RgbaImage {
  const start = Math.min(
    image.width - 2,
    Math.max(0, Math.floor((face.x + face.width / 2) * image.width)),
  );
  return cropImage(image, {
    x: start,
    y: 0,
    width: image.width - start,
    height: image.height,
  });
}

function cropImage(image: RgbaImage, requested: PixelCrop): RgbaImage {
  const x = Math.max(0, Math.floor(requested.x));
  const y = Math.max(0, Math.floor(requested.y));
  const width = Math.min(image.width - x, Math.max(1, Math.floor(requested.width)));
  const height = Math.min(
    image.height - y,
    Math.max(1, Math.floor(requested.height)),
  );
  if (width < 1 || height < 1) {
    throw new Error("Fixture crop falls outside the source image.");
  }
  const rgba = new Uint8Array(width * height * 4);
  for (let row = 0; row < height; row += 1) {
    const sourceOffset = ((y + row) * image.width + x) * 4;
    rgba.set(
      image.rgba.subarray(sourceOffset, sourceOffset + width * 4),
      row * width * 4,
    );
  }
  return { rgba, width, height };
}

/** Centers the undegraded fixture at middle exposure before applying EV shifts. */
function normalizeExposure(image: RgbaImage): RgbaImage {
  let total = 0;
  const pixels = image.width * image.height;
  for (let offset = 0; offset < image.rgba.length; offset += 4) {
    total +=
      image.rgba[offset] * 0.2126 +
      image.rgba[offset + 1] * 0.7152 +
      image.rgba[offset + 2] * 0.0722;
  }
  const mean = total / Math.max(1, pixels);
  const factor = mean > Number.EPSILON ? 127.5 / mean : 1;
  return mapRgb(image, (value) => value * factor);
}

function shiftExposure(image: RgbaImage, ev: number): RgbaImage {
  return mapRgb(image, (value) => value * 2 ** ev);
}

function mapRgb(
  image: RgbaImage,
  transform: (value: number) => number,
): RgbaImage {
  const rgba = image.rgba.slice();
  for (let offset = 0; offset < rgba.length; offset += 4) {
    rgba[offset] = clampByte(transform(rgba[offset]));
    rgba[offset + 1] = clampByte(transform(rgba[offset + 1]));
    rgba[offset + 2] = clampByte(transform(rgba[offset + 2]));
  }
  return { ...image, rgba };
}

function gaussianBlur(image: RgbaImage, sigma: number): RgbaImage {
  if (!(sigma > 0)) return { ...image, rgba: image.rgba.slice() };
  const radius = Math.max(1, Math.ceil(sigma * 3));
  const kernel = Array.from({ length: radius * 2 + 1 }, (_, index) => {
    const distance = index - radius;
    return Math.exp(-(distance * distance) / (2 * sigma * sigma));
  });
  const total = kernel.reduce((sum, value) => sum + value, 0);
  for (let index = 0; index < kernel.length; index += 1) kernel[index] /= total;

  const horizontal = convolve(image.rgba, image.width, image.height, kernel, true);
  const vertical = convolve(horizontal, image.width, image.height, kernel, false);
  return { ...image, rgba: vertical };
}

function convolve(
  source: Uint8Array,
  width: number,
  height: number,
  kernel: readonly number[],
  horizontal: boolean,
): Uint8Array {
  const radius = Math.floor(kernel.length / 2);
  const output = new Uint8Array(source.length);
  for (let y = 0; y < height; y += 1) {
    for (let x = 0; x < width; x += 1) {
      const destination = (y * width + x) * 4;
      for (let channel = 0; channel < 3; channel += 1) {
        let total = 0;
        for (let tap = -radius; tap <= radius; tap += 1) {
          const sampleX = horizontal ? clamp(x + tap, 0, width - 1) : x;
          const sampleY = horizontal ? y : clamp(y + tap, 0, height - 1);
          total +=
            source[(sampleY * width + sampleX) * 4 + channel] *
            kernel[tap + radius];
        }
        output[destination + channel] = clampByte(total);
      }
      output[destination + 3] = 255;
    }
  }
  return output;
}

function resizeBilinear(image: RgbaImage, targetWidth: number): RgbaImage {
  const width = Math.max(1, Math.floor(targetWidth));
  const height = Math.max(1, Math.round((image.height / image.width) * width));
  const rgba = new Uint8Array(width * height * 4);
  for (let y = 0; y < height; y += 1) {
    const sourceY = ((y + 0.5) * image.height) / height - 0.5;
    const y0 = clamp(Math.floor(sourceY), 0, image.height - 1);
    const y1 = Math.min(image.height - 1, y0 + 1);
    const fy = sourceY - Math.floor(sourceY);
    for (let x = 0; x < width; x += 1) {
      const sourceX = ((x + 0.5) * image.width) / width - 0.5;
      const x0 = clamp(Math.floor(sourceX), 0, image.width - 1);
      const x1 = Math.min(image.width - 1, x0 + 1);
      const fx = sourceX - Math.floor(sourceX);
      const destination = (y * width + x) * 4;
      for (let channel = 0; channel < 4; channel += 1) {
        const top =
          image.rgba[(y0 * image.width + x0) * 4 + channel] * (1 - fx) +
          image.rgba[(y0 * image.width + x1) * 4 + channel] * fx;
        const bottom =
          image.rgba[(y1 * image.width + x0) * 4 + channel] * (1 - fx) +
          image.rgba[(y1 * image.width + x1) * 4 + channel] * fx;
        rgba[destination + channel] = clampByte(top * (1 - fy) + bottom * fy);
      }
    }
  }
  return { rgba, width, height };
}

function validFixture(value: unknown): value is QualityFixture {
  return (
    isRecord(value) &&
    typeof value.id === "string" &&
    validBox(value.crop) &&
    validBox(value.face)
  );
}

function validBox(value: unknown): value is PixelCrop {
  return (
    isRecord(value) &&
    [value.x, value.y, value.width, value.height].every(
      (component) => typeof component === "number" && Number.isFinite(component),
    ) &&
    (value.width as number) > 0 &&
    (value.height as number) > 0
  );
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function clamp(value: number, minimum: number, maximum: number): number {
  return Math.max(minimum, Math.min(maximum, value));
}

function clamp01(value: number): number {
  return clamp(value, 0, 1);
}

function clampByte(value: number): number {
  return clamp(Math.round(value), 0, 255);
}
