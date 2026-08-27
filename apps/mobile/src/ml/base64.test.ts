// @ts-expect-error Node's TypeScript runner requires the source extension.
import { decodeBase64 } from "./base64.ts";

function assert(condition: unknown, message: string): asserts condition {
  if (!condition) {
    throw new Error(`base64 self-check failed: ${message}`);
  }
}

const BASE64_ALPHABET =
  "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";

/**
 * The decoder this replaced, copied verbatim from `tinyclip.ts`, `movenet.ts`,
 * `stub-model.ts` and `image-quality.ts` before the four copies were deleted.
 *
 * It is the ONLY correct oracle here. This change is a pure hot-loop swap on
 * the path that feeds every model a photo: the win is speed, and the whole
 * requirement is that the bytes are unchanged. Comparing against a
 * hand-written expectation would test the expectation.
 */
function decodeBase64Shipped(value: string): Uint8Array {
  const encoded = value.replace(/^data:[^,]*,/u, "").replace(/\s/gu, "");
  const padding = encoded.endsWith("==") ? 2 : encoded.endsWith("=") ? 1 : 0;
  const bytes = new Uint8Array(
    Math.max(0, Math.floor((encoded.length * 3) / 4) - padding),
  );
  let accumulator = 0;
  let availableBits = 0;
  let byteIndex = 0;
  for (const character of encoded) {
    if (character === "=") break;
    const digit = BASE64_ALPHABET.indexOf(character);
    if (digit < 0) throw new Error("Invalid base64 image data.");
    accumulator = (accumulator << 6) | digit;
    availableBits += 6;
    if (availableBits >= 8) {
      availableBits -= 8;
      bytes[byteIndex++] = (accumulator >>> availableBits) & 0xff;
      accumulator &= availableBits === 0 ? 0 : (1 << availableBits) - 1;
    }
  }
  if (byteIndex !== bytes.length || bytes.length === 0) {
    throw new Error("Incomplete base64 image data.");
  }
  return bytes;
}

/**
 * Local encoder rather than `Buffer`: this package has no `@types/node`, and
 * `npx tsc --noEmit` has to stay clean.
 */
function encodeBase64(bytes: Uint8Array): string {
  let result = "";
  for (let index = 0; index < bytes.length; index += 3) {
    const first = bytes[index];
    const second = bytes[index + 1];
    const third = bytes[index + 2];
    result += BASE64_ALPHABET[first >> 2];
    result += BASE64_ALPHABET[((first & 3) << 4) | ((second ?? 0) >> 4)];
    result +=
      second === undefined
        ? "="
        : BASE64_ALPHABET[((second & 15) << 2) | ((third ?? 0) >> 6)];
    result += third === undefined ? "=" : BASE64_ALPHABET[third & 63];
  }
  return result;
}

function sameBytes(left: Uint8Array, right: Uint8Array): boolean {
  if (left.length !== right.length) return false;
  for (let index = 0; index < left.length; index += 1) {
    if (left[index] !== right[index]) return false;
  }
  return true;
}

/** Deterministic pseudo-random bytes; a fixed seed keeps failures reproducible. */
function payload(length: number, seed: number): Uint8Array {
  const bytes = new Uint8Array(length);
  let state = seed >>> 0 || 1;
  for (let index = 0; index < length; index += 1) {
    state ^= state << 13;
    state ^= state >>> 17;
    state ^= state << 5;
    state >>>= 0;
    bytes[index] = state & 0xff;
  }
  return bytes;
}

function outcome(decode: () => Uint8Array): Uint8Array | "threw" {
  try {
    return decode();
  } catch {
    return "threw";
  }
}

// --- 1. Byte-identical to the decoder it replaced -------------------------
//
// Every length mod 3 is covered, which is every padding case: 0 bytes of
// padding, one '=', and two.

let comparedLengths = 0;
for (let length = 1; length <= 400; length += 1) {
  const original = payload(length, length * 2654435761);
  const encoded = encodeBase64(original);
  const shipped = decodeBase64Shipped(encoded);
  const fast = decodeBase64(encoded);
  assert(
    sameBytes(shipped, fast),
    `length ${length} (padding ${(3 - (length % 3)) % 3}) decoded differently`,
  );
  // The oracle itself has to be right, or "identical" means "identically wrong".
  assert(
    sameBytes(shipped, original),
    `length ${length}: the reference decoder did not round-trip its own input`,
  );
  comparedLengths += 1;
}
assert(comparedLengths === 400, "the length sweep did not run");

// A payload the size of a real quality proxy, where the cost actually lives.
{
  const original = payload(110_000, 7);
  const encoded = encodeBase64(original);
  assert(
    sameBytes(decodeBase64(encoded), original),
    "a 110 KB payload — the size of one quality proxy — decoded wrong",
  );
}

// The `data:` prefix and embedded whitespace both survive, because
// manipulateAsync's base64 has been seen with and without them.
{
  const original = payload(64, 99);
  const encoded = encodeBase64(original);
  assert(
    sameBytes(decodeBase64(`data:image/jpeg;base64,${encoded}`), original),
    "a data: URI prefix was not stripped",
  );
  assert(
    sameBytes(decodeBase64(encoded.replace(/(.{16})/gu, "$1\n")), original),
    "embedded newlines were not stripped",
  );
}

// --- 2. The error contract, which is load-bearing --------------------------
//
// A short buffer must throw rather than reach jpeg-js as a corrupt image: the
// caller's `catch` is what records the photo as degraded.

for (const bad of ["", "!!!!", "AAAA?", "A", "AAAAAAAA==", "====", "AA=A"]) {
  assert(
    outcome(() => decodeBase64(bad)) === "threw",
    `"${bad}" must be rejected, not silently decoded`,
  );
  assert(
    outcome(() => decodeBase64Shipped(bad)) === "threw",
    `the reference decoder was expected to reject "${bad}" too`,
  );
}

// --- 3. SABOTAGE. The comparison above must be able to fail. ---------------
//
// Roughly half the new tests in this repo have passed while proving nothing.
// Three plausible mistakes in a table-driven rewrite, each one caught here by
// the SAME assertion the real decoder is judged with. If any of these slipped
// through the comparison, section 1 would be decoration.

type Decoder = (value: string) => Uint8Array;

/** Off-by-one table: every decoded byte is wrong, the length is right. */
const offByOne: Decoder = (value) => {
  const table = new Int8Array(128).fill(-1);
  for (let index = 0; index < BASE64_ALPHABET.length; index += 1) {
    table[BASE64_ALPHABET.charCodeAt(index)] = (index + 1) % 64;
  }
  return decodeWithTable(value, table, false);
};

/** Forgets the padding adjustment: the buffer is one or two bytes too long. */
const ignoresPadding: Decoder = (value) => {
  const table = new Int8Array(128).fill(-1);
  for (let index = 0; index < BASE64_ALPHABET.length; index += 1) {
    table[BASE64_ALPHABET.charCodeAt(index)] = index;
  }
  return decodeWithTable(value, table, true);
};

/** Accepts anything: an out-of-alphabet character decodes as zero. */
const acceptsGarbage: Decoder = (value) => {
  const table = new Int8Array(128).fill(0);
  for (let index = 0; index < BASE64_ALPHABET.length; index += 1) {
    table[BASE64_ALPHABET.charCodeAt(index)] = index;
  }
  return decodeWithTable(value, table, false);
};

function decodeWithTable(
  value: string,
  table: Int8Array,
  skipPadding: boolean,
): Uint8Array {
  const encoded = value.replace(/^data:[^,]*,/u, "").replace(/\s/gu, "");
  const padding = skipPadding
    ? 0
    : encoded.endsWith("==")
      ? 2
      : encoded.endsWith("=")
        ? 1
        : 0;
  const bytes = new Uint8Array(
    Math.max(0, Math.floor((encoded.length * 3) / 4) - padding),
  );
  let accumulator = 0;
  let availableBits = 0;
  let byteIndex = 0;
  for (let index = 0; index < encoded.length; index += 1) {
    const code = encoded.charCodeAt(index);
    if (code === 61) break;
    const digit = code < 128 ? table[code] : -1;
    if (digit < 0) throw new Error("Invalid base64 image data.");
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
    throw new Error("Incomplete base64 image data.");
  }
  return bytes;
}

/** Replays section 1's comparison against `candidate`. True means it passed. */
function survivesTheComparison(candidate: Decoder): boolean {
  for (let length = 1; length <= 400; length += 1) {
    const original = payload(length, length * 2654435761);
    const encoded = encodeBase64(original);
    const shipped = decodeBase64Shipped(encoded);
    const produced = outcome(() => candidate(encoded));
    if (produced === "threw" || !sameBytes(shipped, produced)) return false;
  }
  return true;
}

assert(
  survivesTheComparison(decodeBase64),
  "VACUITY: the shipped decoder must pass the comparison it is judged by",
);
assert(
  !survivesTheComparison(offByOne),
  "SABOTAGE: an off-by-one table must FAIL the comparison",
);
assert(
  !survivesTheComparison(ignoresPadding),
  "SABOTAGE: dropping the padding adjustment must FAIL the comparison",
);
assert(
  outcome(() => acceptsGarbage("AAA?")) !== "threw" &&
    outcome(() => decodeBase64("AAA?")) === "threw",
  "SABOTAGE: a table that accepts out-of-alphabet characters must be distinguishable",
);

console.log(
  `base64 self-check passed (${comparedLengths} lengths byte-identical, 3 saboteurs rejected)`,
);
