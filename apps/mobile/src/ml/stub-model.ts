import { manipulateAsync, SaveFormat } from "expo-image-manipulator";
import { decode as decodeJpeg } from "jpeg-js";

import type { ModelResult, OnDeviceModel } from "./types";

const THUMBNAIL_WIDTH = 32;
const THUMBPRINT_SIZE = 8;
const COLOR_BINS = 4;
const FALLBACK_EMBEDDING_LENGTH = 32;
const FNV_OFFSET_BASIS = 0x811c9dc5;
const FNV_PRIME = 0x01000193;
const BASE64_ALPHABET =
  "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";

function hashUri(imageUri: string): number {
  let hash = FNV_OFFSET_BASIS;

  for (let index = 0; index < imageUri.length; index += 1) {
    hash ^= imageUri.charCodeAt(index);
    hash = Math.imul(hash, FNV_PRIME) >>> 0;
  }

  return hash;
}

function nextPseudoRandom(state: number): number {
  let value = state || FNV_OFFSET_BASIS;
  value ^= value << 13;
  value ^= value >>> 17;
  value ^= value << 5;
  return value >>> 0;
}

function createFallbackEmbedding(seed: number): number[] {
  const embedding: number[] = [];
  let state = seed;

  for (let index = 0; index < FALLBACK_EMBEDDING_LENGTH; index += 1) {
    state = nextPseudoRandom((state + index + 1) >>> 0);
    embedding.push((state / 0xffffffff) * 2 - 1);
  }

  return l2Normalize(embedding);
}

async function createPerceptualEmbedding(imageUri: string): Promise<number[]> {
  const thumbnail = await manipulateAsync(
    imageUri,
    [{ resize: { width: THUMBNAIL_WIDTH } }],
    {
      base64: true,
      compress: 0.85,
      format: SaveFormat.JPEG,
    },
  );

  if (!thumbnail.base64) {
    throw new Error("Image manipulator did not return thumbnail pixels.");
  }

  const decoded = decodeJpeg(decodeBase64(thumbnail.base64), {
    useTArray: true,
    formatAsRGBA: true,
    tolerantDecoding: true,
    maxResolutionInMP: 1,
    maxMemoryUsageInMB: 8,
  });

  if (decoded.width < 1 || decoded.height < 1 || decoded.data.length < 4) {
    throw new Error("Decoded thumbnail is empty.");
  }

  return fingerprintPixels(decoded.data, decoded.width, decoded.height);
}

function fingerprintPixels(
  pixels: Uint8Array,
  width: number,
  height: number,
): number[] {
  const lumaCells: number[] = [];

  for (let gridY = 0; gridY < THUMBPRINT_SIZE; gridY += 1) {
    const startY = Math.floor((gridY * height) / THUMBPRINT_SIZE);
    const endY = Math.max(
      startY + 1,
      Math.floor(((gridY + 1) * height) / THUMBPRINT_SIZE),
    );

    for (let gridX = 0; gridX < THUMBPRINT_SIZE; gridX += 1) {
      const startX = Math.floor((gridX * width) / THUMBPRINT_SIZE);
      const endX = Math.max(
        startX + 1,
        Math.floor(((gridX + 1) * width) / THUMBPRINT_SIZE),
      );
      let lumaTotal = 0;
      let sampleCount = 0;

      for (let y = startY; y < Math.min(endY, height); y += 1) {
        for (let x = startX; x < Math.min(endX, width); x += 1) {
          const offset = (y * width + x) * 4;
          lumaTotal +=
            pixels[offset] * 0.2126 +
            pixels[offset + 1] * 0.7152 +
            pixels[offset + 2] * 0.0722;
          sampleCount += 1;
        }
      }

      lumaCells.push(lumaTotal / Math.max(1, sampleCount));
    }
  }

  const meanLuma =
    lumaCells.reduce((sum, value) => sum + value, 0) / lumaCells.length;
  const embedding = lumaCells.map((value) => (value - meanLuma) / 128);
  const histograms = Array.from({ length: 3 }, () =>
    Array<number>(COLOR_BINS).fill(0),
  );
  const pixelCount = width * height;

  for (let offset = 0; offset < pixels.length; offset += 4) {
    for (let channel = 0; channel < 3; channel += 1) {
      const bin = Math.min(
        COLOR_BINS - 1,
        Math.floor((pixels[offset + channel] * COLOR_BINS) / 256),
      );
      histograms[channel][bin] += 1;
    }
  }

  for (const histogram of histograms) {
    for (const count of histogram) {
      const centeredFrequency = count / pixelCount - 1 / COLOR_BINS;
      embedding.push(centeredFrequency * 0.5);
    }
  }

  return l2Normalize(embedding);
}

function l2Normalize(values: number[]): number[] {
  const magnitude = Math.sqrt(
    values.reduce((sum, value) => sum + value * value, 0),
  );

  if (!Number.isFinite(magnitude) || magnitude <= Number.EPSILON) {
    throw new Error("Cannot normalize an empty perceptual signal.");
  }

  return values.map((value) => value / magnitude);
}

function decodeBase64(value: string): Uint8Array {
  const encoded = value
    .replace(/^data:[^,]*,/u, "")
    .replace(/\s/gu, "");
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

/** Cheap pixel-based model used until the bundled ONNX models are wired. */
export class StubOnDeviceModel implements OnDeviceModel {
  async run(imageUri: string): Promise<ModelResult> {
    try {
      return {
        embedding: await createPerceptualEmbedding(imageUri),
        faces: 0,
      };
    } catch {
      return {
        embedding: createFallbackEmbedding(hashUri(imageUri)),
        faces: 0,
      };
    }
  }
}
