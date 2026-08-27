// @ts-expect-error Node's TypeScript runner requires the source extension.
import { measureSync } from "../selection/js-thread-profile.ts";

const BASE64_ALPHABET =
  "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";

/**
 * Reverse base64 table.
 *
 * `facenet.ts` and `faces/face-index.ts` already decode this way and say why:
 * the alternative, `BASE64_ALPHABET.indexOf(character)` inside a `for..of`,
 * allocates a fresh single-character string per byte and then linearly scans 64
 * characters to find its value. Four copies of that loop were still on the
 * ALBUM path — `tinyclip`, `movenet`, `stub-model` and `image-quality` — where
 * every photo pays it four times, on the one thread all six concurrent photos
 * share. The quality proxy alone is a ~150,000 character string.
 *
 * Measured on this Mac under Node/V8 on a 148 KB base64 string: 7.97 ms per
 * decode with `indexOf`, 2.10 ms with this table. Hermes has no JIT, so the
 * device pays a multiple of both — and the ratio is if anything wider there,
 * because the string-iterator allocation the table avoids is exactly what an
 * interpreter is worst at.
 */
const BASE64_VALUES = (() => {
  const table = new Int8Array(128).fill(-1);
  for (let index = 0; index < BASE64_ALPHABET.length; index += 1) {
    table[BASE64_ALPHABET.charCodeAt(index)] = index;
  }
  return table;
})();

const PAD = 61; // '='

/**
 * Decode a base64 image payload from `expo-image-manipulator`.
 *
 * Byte-for-byte identical to the four hand-rolled decoders it replaces,
 * including their error contract: an out-of-alphabet character and a truncated
 * payload both throw, because a silently short buffer reaches `jpeg-js` as a
 * corrupt image rather than as a failure the caller can report.
 *
 * `label` names the caller in the JS-thread profile — this is one of the blocks
 * competing for the thread that also has to deliver every `model.run`
 * resolution, so it is measured where it is spent.
 */
export function decodeBase64Image(value: string, label: string): Uint8Array {
  return measureSync(label, () => decodeBase64(value));
}

/** The same decode without the profile hook, for callers already inside one. */
export function decodeBase64(value: string): Uint8Array {
  const encoded = value.replace(/^data:[^,]*,/u, "").replace(/\s/gu, "");
  const padding = encoded.endsWith("==") ? 2 : encoded.endsWith("=") ? 1 : 0;
  const bytes = new Uint8Array(
    Math.max(0, Math.floor((encoded.length * 3) / 4) - padding),
  );
  let accumulator = 0;
  let availableBits = 0;
  let byteIndex = 0;

  for (let index = 0; index < encoded.length; index += 1) {
    const code = encoded.charCodeAt(index);
    if (code === PAD) break;
    const digit = code < 128 ? BASE64_VALUES[code] : -1;
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
