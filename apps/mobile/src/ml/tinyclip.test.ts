// @ts-expect-error Node requires the extension; Metro resolves it too.
import {
  centerCropTransform,
  normalizeClipPixels,
  parseEmbeddingOutput,
  semanticSignalsWithAxes,
  type TextAxes,
} from "./tinyclip.ts";

function assert(value: unknown, message: string): asserts value {
  if (!value) throw new Error(`TinyCLIP self-check failed: ${message}`);
}

function near(actual: number, expected: number, message: string): void {
  assert(Math.abs(actual - expected) < 1e-6, `${message}: ${actual} != ${expected}`);
}

const landscape = centerCropTransform(400, 200);
assert(
  JSON.stringify(landscape) ===
    JSON.stringify({ resize: { height: 224 }, originX: 112, originY: 0 }),
  "landscape center crop preserves aspect",
);
const portrait = centerCropTransform(200, 400);
assert(
  JSON.stringify(portrait) ===
    JSON.stringify({ resize: { width: 224 }, originX: 0, originY: 112 }),
  "portrait center crop preserves aspect",
);

const pixels = normalizeClipPixels(new Uint8Array([255, 0, 128, 255]), 1, 1);
assert(pixels.length === 3, "preprocessing emits one RGB pixel");
near(pixels[0], (1 - 0.48145466) / 0.26862954, "red normalization");
near(pixels[1], (0 - 0.4578275) / 0.26130258, "green normalization");
near(pixels[2], (128 / 255 - 0.40821073) / 0.27577711, "blue normalization");

const raw = new Float32Array(512);
raw[0] = 3;
raw[1] = 4;
const embedding = parseEmbeddingOutput(raw.buffer);
assert(embedding, "valid model output parses");
near(embedding[0], 0.6, "embedding normalized x");
near(embedding[1], 0.8, "embedding normalized y");

const vector = (first: number, second = 0) => {
  const values = Array<number>(512).fill(0);
  values[0] = first;
  values[1] = second;
  return values;
};
const pole = { positive: vector(1), negative: vector(-1) };
const axes: TextAxes = {
  model: "test",
  embeddingSize: 512,
  axes: {
    aesthetic: pole,
    composed: pole,
    clean_frame: pole,
    sleeping: pole,
    embrace_context: pole,
    screenshot_document: pole,
  },
};
const signals = semanticSignalsWithAxes(embedding, axes);
for (const value of [
  signals.aesthetic,
  signals.composed,
  signals.cleanFrame,
  signals.sleeping,
  signals.awake,
  signals.embraceContext,
  signals.screenshotDocument,
]) {
  assert(Number.isFinite(value), "every zero-shot signal is finite");
}
assert(signals.awake === -signals.sleeping, "awake is the sleeping contrast inverse");
assert(parseEmbeddingOutput(new ArrayBuffer(4)) === undefined, "short output fails neutral");
