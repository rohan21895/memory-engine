// @ts-expect-error Node requires the extension; Metro resolves it too.
import { EMBEDDING_SIZE, decodeBase64, normalizeFacePixels, normalizeFaceRgb, parseFaceEmbeddingOutput, squareFaceCrop } from "./facenet.ts";

function assert(value: unknown, message: string): asserts value {
  if (!value) throw new Error(`MobileFaceNet self-check failed: ${message}`);
}

function close(actual: number, expected: number, message: string): void {
  assert(Math.abs(actual - expected) < 1e-6, `${message}: got ${actual}, want ${expected}`);
}

{
  const crop = squareFaceCrop(
    { width: 200, height: 100 },
    { x: 0, y: 20, width: 30, height: 40 },
  );
  assert(
    JSON.stringify(crop) === JSON.stringify({ originX: 0, originY: 14, width: 52, height: 52 }),
    "a padded face crop stays square and shifts inside the left edge",
  );
}

{
  const crop = squareFaceCrop(
    { width: 200, height: 100 },
    { x: 180, y: 30, width: 20, height: 20 },
  );
  assert(crop.originX + crop.width === 200, "a right-edge crop stays inside the image");
  assert(crop.width === crop.height, "the model crop is square");
}

{
  const normalized = normalizeFacePixels(
    new Uint8Array([0, 128, 255, 200]),
    1,
    1,
  );
  close(normalized[0], -1, "black maps to -1");
  close(normalized[1], 1 / 255, "128 follows exact ArcFace centering");
  close(normalized[2], 1, "white maps to 1");
  const rgb = normalizeFaceRgb(new Uint8Array([0, 128, 255]), 1, 1);
  close(rgb[0], -1, "aligned RGB black maps to -1");
  close(rgb[1], 1 / 255, "aligned RGB keeps channel order");
  close(rgb[2], 1, "aligned RGB white maps to 1");
}

{
  // The table-driven decoder replaced an indexOf scan per character; every JPEG
  // patch this module reads passes through it, so a wrong table would corrupt
  // every embedding rather than fail loudly.
  const vectors: Array<readonly [string, number[]]> = [
    // RFC 4648 test vectors, covering both padding lengths.
    ["Zg==", [0x66]],
    ["Zm8=", [0x66, 0x6f]],
    ["Zm9v", [0x66, 0x6f, 0x6f]],
    ["Zm9vYmFy", [0x66, 0x6f, 0x6f, 0x62, 0x61, 0x72]],
    // Sextets 62 and 63, the two table entries an alphabet typo hits first.
    ["++++", [0xfb, 0xef, 0xbe]],
    ["////", [0xff, 0xff, 0xff]],
  ];
  for (const [encoded, expected] of vectors) {
    const decoded = decodeBase64(encoded);
    assert(
      decoded.length === expected.length &&
        expected.every((value, index) => decoded[index] === value),
      `base64 decode is byte-exact for ${encoded}`,
    );
  }
  assert(
    decodeBase64("data:image/jpeg;base64,Zm9v").length === 3,
    "a data URI prefix is stripped",
  );
  assert(
    decodeBase64("Zm9v\nYmFy").length === 6,
    "wrapped base64 whitespace is ignored",
  );
  let rejected = false;
  try {
    decodeBase64("!!!!");
  } catch {
    rejected = true;
  }
  assert(rejected, "a character outside the alphabet is rejected");
}

{
  // Sized from the constant, not from a literal: the embedding width changed
  // from 192 to 512 with the w600k_mbf swap, and a hard-coded fixture would
  // have gone on passing against a model that no longer exists.
  const output = new Float32Array(EMBEDDING_SIZE);
  output[0] = 3;
  output[1] = 4;
  const embedding = parseFaceEmbeddingOutput(output.buffer);
  assert(embedding?.length === EMBEDDING_SIZE, "the complete embedding is parsed");
  close(embedding[0], 0.6, "the output is L2-normalized");
  close(embedding[1], 0.8, "the output is L2-normalized");
  assert(
    parseFaceEmbeddingOutput(new ArrayBuffer(4)) === undefined,
    "a short tensor fails neutral",
  );
  assert(
    parseFaceEmbeddingOutput(new Float32Array(EMBEDDING_SIZE).buffer) === undefined,
    "a zero tensor fails neutral",
  );
}
