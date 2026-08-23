import type { ModelResult, OnDeviceModel } from "./types";

const EMBEDDING_LENGTH = 32;
const FNV_OFFSET_BASIS = 0x811c9dc5;
const FNV_PRIME = 0x01000193;

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

function createEmbedding(seed: number): number[] {
  const embedding: number[] = [];
  let state = seed;

  for (let index = 0; index < EMBEDDING_LENGTH; index += 1) {
    state = nextPseudoRandom((state + index + 1) >>> 0);
    embedding.push((state / 0xffffffff) * 2 - 1);
  }

  const magnitude = Math.sqrt(
    embedding.reduce((sum, value) => sum + value * value, 0),
  );

  return embedding.map((value) => value / magnitude);
}

/** Dependency-free stand-in used until the bundled ONNX models are wired. */
export class StubOnDeviceModel implements OnDeviceModel {
  async run(imageUri: string): Promise<ModelResult> {
    const seed = hashUri(imageUri);

    return {
      embedding: createEmbedding(seed),
      faces: (seed >>> 8) % 6,
    };
  }
}
