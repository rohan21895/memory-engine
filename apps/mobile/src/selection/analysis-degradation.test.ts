// @ts-expect-error The Expo app deliberately does not ship Node type declarations.
import { readFileSync } from "node:fs";

// @ts-expect-error Node's native TypeScript runner requires the extension.
import { DEEP_ANALYSIS_PHASES, DeepAnalysisTimingCollector, describeDegradation } from "./deep-analysis-timing.ts";
// @ts-expect-error Node's native TypeScript runner requires the extension.
import { measureImageQuality } from "./image-quality.ts";
// A type-only import is erased before the extension can matter, so unlike the
// value imports above it needs no suppression.
import type { LoadedQualityImage } from "./image-quality.ts";

function assert(value: unknown, message: string): asserts value {
  if (!value) throw new Error(`Analysis-degradation self-check failed: ${message}`);
}

/**
 * The measured bug, as a test.
 *
 * On the owner's phone, during a real 3,000-photo build:
 *
 *   OutOfMemoryError "Failed to allocate a 28975795 byte allocation with
 *   25165824 free bytes and 24MB until OOM, target footprint 268435456,
 *   growth limit 268435456"
 *
 * The app caught it and carried on, which is correct. What was NOT correct is
 * that the catch was the end of the story: the same build emitted a full
 * `[PhoteoAlbumBuildTiming]` line with per-phase means and p95s, and not one field
 * in it said a photo had lost a signal. `measureAwaited` records its duration
 * in a `finally`, so a phase that rejected produced a sample indistinguishable
 * from a healthy one. Nobody could say which photo degraded, on which phase, or
 * even how many.
 *
 * The three halves below, and the third is the one that keeps the first honest:
 * a counter is exactly the kind of code that passes its own test while never
 * firing in production.
 */

const OOM_MESSAGE =
  "java.lang.OutOfMemoryError: Failed to allocate a 28975795 byte allocation " +
  "with 25165824 free bytes and 24MB until OOM, target footprint 268435456, " +
  "growth limit 268435456";

// ART reports the whole byte-array object: 12-byte header plus payload. The
// payload's remainder distinguishes the exact-copy step from Android Base64's
// padded output, whose byte length is always divisible by four.
const FAILED_ART_OBJECT_BYTES = 28_975_795;
const BYTE_ARRAY_HEADER_BYTES = 12;
const failedPayloadBytes = FAILED_ART_OBJECT_BYTES - BYTE_ARRAY_HEADER_BYTES;
assert(
  failedPayloadBytes === 28_975_783 && failedPayloadBytes % 4 === 3,
  "the measured allocation must be an arbitrary-length payload, not the Base64 output array",
);

const imageManipulatorAndroidSource = readFileSync(
  new URL(
    "../../../../node_modules/expo-image-manipulator/android/src/main/java/expo/modules/imagemanipulator/ImageManipulatorModule.kt",
    import.meta.url,
  ),
  "utf8",
);
assert(
  imageManipulatorAndroidSource.includes("ByteArrayOutputStream().use") &&
    imageManipulatorAndroidSource.includes("byteOut.toByteArray()") &&
    imageManipulatorAndroidSource.includes("Base64.encodeToString") &&
    imageManipulatorAndroidSource.includes("Base64.NO_WRAP"),
  "Expo's measured native phase must still make the exact second JPEG copy before Base64",
);

// --- 1. The collector counts, classifies and scrubs. -------------------------

const collector = new DeepAnalysisTimingCollector();
collector.recordDegraded("quality-decode", new Error(OOM_MESSAGE));
collector.recordDegraded("quality-decode", new Error("Decoder returned no pixels"));
collector.recordDegraded("tinyclip", new Error(OOM_MESSAGE));

const byPhase = new Map(
  collector.degradations().map((entry) => [entry.phase, entry]),
);

assert(
  byPhase.get("quality-decode")?.count === 2,
  "both quality decode failures must be counted",
);
assert(
  byPhase.get("quality-decode")?.outOfMemory === 1,
  "only the ART allocation failure counts as an out-of-memory, not the other one",
);
assert(
  byPhase.get("tinyclip")?.count === 1 &&
    byPhase.get("tinyclip")?.outOfMemory === 1,
  "a second phase must be counted separately, with its own OOM tally",
);
assert(
  byPhase.get("quality-decode")?.firstMessage.includes("28975795"),
  "the FIRST failure is the one kept, and its allocation size must survive to the log",
);
assert(
  collector.degradations().length === DEEP_ANALYSIS_PHASES.length &&
    byPhase.get("movenet")?.count === 0,
  "a phase that lost nothing must still report a zero, so a clean pass is provable",
);

// URIs point into the owner's library, and this line is printed to a log people
// are asked to paste into issues.
const scrubbed = describeDegradation(
  new Error(
    "Image decode failed for content://media/external/images/media/1000012345",
  ),
);
assert(
  !scrubbed.includes("1000012345") && scrubbed.includes("<local-uri>"),
  "a reported message must not carry the photo's content:// URI",
);

// --- 2. The real production function reports through the real seam. ----------
//
// `measureImageQuality` is the function whose awaited p95 was 9,118ms next to
// the OOM, and its `imageLoader` seam is the standing gates' own offline entry
// point, so this exercises the shipped code path rather than a copy of it.

const pixels = (width: number, height: number): LoadedQualityImage => {
  const rgba = new Uint8Array(width * height * 4);
  for (let index = 0; index < width * height; index += 1) {
    // Alternating black/white columns, so there is real high-frequency detail
    // to measure and `sharpness` cannot come back as an accidental zero.
    const value = index % 2 === 0 ? 0 : 255;
    rgba[index * 4] = value;
    rgba[index * 4 + 1] = value;
    rgba[index * 4 + 2] = value;
    rgba[index * 4 + 3] = 255;
  }
  return { rgba, width, height };
};

let reported: unknown;
const failed = await measureImageQuality("file:///proxy.jpg", {
  imageLoader: () => Promise.reject(new Error(OOM_MESSAGE)),
  onDegraded: (error: unknown) => {
    reported = error;
  },
});
assert(
  reported instanceof Error && reported.message.includes("28975795"),
  "a decode that dies of OutOfMemoryError must reach onDegraded with its message intact",
);
assert(
  Object.keys(failed).length === 0,
  "...and must still return an empty measurement, because an album must not fail on one photo",
);

let measuredCleanly = true;
const good = await measureImageQuality("file:///proxy.jpg", {
  imageLoader: () => Promise.resolve(pixels(64, 48)),
  onDegraded: () => {
    measuredCleanly = false;
  },
});
assert(
  measuredCleanly,
  "a decode that worked must NOT report a degradation",
);
assert(
  typeof good.sharpness === "number" && good.sharpness > 0,
  "...and must still produce the sharpness it always did",
);

// --- 3. Vacuity guard. ------------------------------------------------------
//
// Everything above would also pass against a counter that is never reached in
// production, so each claim is now shown capable of failing.

// 3a. The return value is NOT a usable detector. This is the whole reason the
// callback has to exist: a failed decode and a legitimately blank image return
// the SAME `{}`, so any assertion phrased on the result alone proves nothing.
let blankReported = false;
const blank = await measureImageQuality("file:///blank.jpg", {
  imageLoader: () =>
    Promise.resolve({ rgba: new Uint8Array(), width: 0, height: 0 }),
  onDegraded: () => {
    blankReported = true;
  },
});
assert(
  Object.keys(blank).length === 0 && Object.keys(failed).length === 0,
  "VACUITY: the blank image and the OOM return an identical empty result...",
);
assert(
  !blankReported,
  "VACUITY: ...so only the callback separates them, and a blank image is not a degradation",
);

// 3b. A counter that never increments must fail the count assertion.
const silent = new DeepAnalysisTimingCollector();
assert(
  silent.degradations().every((entry) => entry.count === 0) &&
    silent.degradations().length === DEEP_ANALYSIS_PHASES.length,
  "VACUITY: an untouched collector reports six zeros, so a non-zero count above is real",
);

// 3c. The OOM classifier must be capable of answering no.
const ordinary = new DeepAnalysisTimingCollector();
ordinary.recordDegraded("movenet", new Error("Unsupported image format"));
assert(
  ordinary.degradations().find((entry) => entry.phase === "movenet")
    ?.outOfMemory === 0,
  "VACUITY: an ordinary failure must NOT be classified as out-of-memory",
);

// 3d. The scrubber must be capable of leaving a message alone.
assert(
  describeDegradation(new Error("Unsupported image format")) ===
    "Unsupported image format",
  "VACUITY: scrubbing must not rewrite a message that carries no URI",
);

// --- 4. Real-caller guard: the counters must be WIRED, not merely written. ---
//
// The collector is pure and importable; `build-album.ts` is not (it loads Expo
// native modules eagerly), so the bridge is checked the same way the standing
// gates in ./build-album.test.ts check it — against the source text.

const buildAlbumSource = readFileSync(
  new URL("../build-album.ts", import.meta.url),
  "utf8",
);

assert(
  buildAlbumSource.includes('stage: "analysis-degraded"') &&
    buildAlbumSource.includes("degradedPhotos,") &&
    buildAlbumSource.includes("degraded: degradations,"),
  "the build must report a degradation stage through the existing timing line",
);
assert(
  buildAlbumSource.includes('if (timing.stage === "analysis-degraded")') &&
    buildAlbumSource.includes("analysis-degraded={photos:"),
  "...and that stage must have a formatter, or it prints as a 0ms row and says nothing",
);
assert(
  buildAlbumSource.includes(",oom:${outOfMemory}}"),
  "the emitted line must carry the out-of-memory tally, which is the one degradation about the batch rather than the photo",
);
// Six phases run per photo and every one of them can fail. A counter wired to
// five is the same silence in a smaller costume, so the count is pinned.
const wired = buildAlbumSource.split("markPhotoDegraded,").length - 1;
assert(
  wired === DEEP_ANALYSIS_PHASES.length,
  `every deep-analysis phase must route into the photo counter (wired ${wired} of ${DEEP_ANALYSIS_PHASES.length})`,
);
assert(
  buildAlbumSource.includes("prepareCandidateAnalysisProxy(photo.uri, (error) =>"),
  "the proxy is the most expensive failure of the six -- it costs every other signal -- so it must report too",
);
assert(
  buildAlbumSource.includes("const ANALYZE_CONCURRENCY = 1;") &&
    buildAlbumSource.includes("24 Java-array pipelines to overlap") &&
    buildAlbumSource.includes("source-derived maximum from 24") &&
    buildAlbumSource.includes("The cost is real and unmeasured"),
  "the source-derived ART byte-array fan-out must stay bounded without claiming unmeasured throughput",
);
// The allocation must stay RETRACTED. efe401d identified it as expo's
// `toByteArray()`; that was refuted twice (the 1280 px proxy cannot hold
// 27.63 MiB, and the call only runs under `base64: true`, whose sites here top
// out at 1280 px). This pins the retraction rather than the wrong answer,
// because the failure mode is someone reading a confident comment and stopping.
assert(
  buildAlbumSource.includes("Do not treat #41's allocation as identified"),
  "the OOM allocation must not be described as identified while it is not",
);
assert(
  buildAlbumSource.includes("[PhoteoAlbumBuildTiming]") &&
    buildAlbumSource.includes("[PhoteoAlbumBuildDegraded]"),
  "album OOM evidence must use the prefix persisted past ColorOS logcat drops",
);

console.log("analysis-degradation self-check passed");
