// Pure module self-checks; the Expo dependency is loaded dynamically only by
// the on-device probe, so Node can exercise the deterministic decoder here.
// @ts-expect-error Node requires the extension; Metro resolves this path too.
import { decodeBlurhashGrayscale, qualityFromBlurhash } from "./candidate-quality-probe.ts";

function assert(condition: unknown, message: string): asserts condition {
  if (!condition) throw new Error(`Candidate probe self-check failed: ${message}`);
}

const sample = "LEHV6nWB2yk8pyo0adR*.7kCMdnj";
const pixels = decodeBlurhashGrayscale(sample, 16, 12);
assert(pixels?.length === 16 * 12, "a valid blurhash should decode to the requested thumbnail");
assert(
  pixels.some((pixel) => pixel !== pixels[0]),
  "the decoded thumbnail should retain tonal variation",
);
const quality = qualityFromBlurhash(sample);
assert(
  typeof quality.sharpness === "number" && quality.sharpness > 0,
  "the thumbnail should expose a non-zero detail signal",
);
assert(
  typeof quality.exposure === "number" &&
    quality.exposure > 0 &&
    quality.exposure < 1,
  "the thumbnail should expose a bounded exposure signal",
);
assert(
  decodeBlurhashGrayscale("not-a-blurhash", 16, 12) === undefined,
  "bad native output must remain non-fatal",
);
