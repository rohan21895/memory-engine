const { readFileSync } = require("node:fs");
const { resolve } = require("node:path");

function assert(value, message) {
  if (!value) throw new Error(`Album analysis proxy self-check failed: ${message}`);
}

const buildAlbumSource = readFileSync(resolve(__dirname, "../build-album.ts"), "utf8");
const proxySource = readFileSync(
  resolve(__dirname, "candidate-quality-probe.ts"),
  "utf8",
);
const analysisProxySource = proxySource.slice(
  proxySource.indexOf("export async function prepareCandidateAnalysisProxy("),
  proxySource.indexOf("export async function removeCandidateAnalysisProxy("),
);
const nativeLoaderSource = readFileSync(
  resolve(
    __dirname,
    "../../modules/photeo-scan-service/android/src/main/java/expo/modules/photeoscanservice/MediaStoreThumbnailLoader.kt",
  ),
  "utf8",
);

assert(
  buildAlbumSource.includes("prepareCandidateAnalysisProxy(photo.id, (error) =>") &&
    !buildAlbumSource.includes("prepareCandidateAnalysisProxy(photo.uri"),
  "album analysis must cross the bridge with a MediaStore id, never the original URI",
);
assert(
  analysisProxySource.includes("albumAnalysisProxy(assetId, ANALYSIS_PROXY_SIZE)") &&
    !analysisProxySource.includes("Image.loadAsync(") &&
    !analysisProxySource.includes("ImageManipulator.manipulate("),
  "the shared proxy must come from the bounded native loader, not open-then-downsample",
);
assert(
  proxySource.includes("const ANALYSIS_PROXY_SIZE = 1280;") &&
    nativeLoaderSource.includes("private const val ANALYSIS_MAX_EDGE = 1280") &&
    nativeLoaderSource.includes("private const val ANALYSIS_JPEG_QUALITY = 94"),
  "the native proxy must preserve the existing 1280px/quality-94 analysis contract",
);
assert(
  nativeLoaderSource.includes(
    "context.contentResolver.loadThumbnail(source, Size(edge, edge), null)",
  ) &&
    nativeLoaderSource.includes("decoder.setTargetSize(width, height)") &&
    nativeLoaderSource.includes(
      "decoder.setMemorySizePolicy(ImageDecoder.MEMORY_POLICY_LOW_RAM)",
    ) &&
    !nativeLoaderSource.includes("readBytes(") &&
    !nativeLoaderSource.includes("decodeByteArray("),
  "native loading must remain bounded before pixel allocation and never buffer originals",
);
// The diagnostic one-photo stopgap is over. It was pinned at 1 while the whole
// app's AsyncFunctions shared a single Expo queue, so raising it bought nothing
// and only risked OOM. Now that the scan service runs on its own queue and the
// interpreters are multi-threaded, the measured build at concurrency 3 reported
// `oom:0` and 0/64 degraded -- see the constant's own comment for the numbers.
//
// What still needs guarding is the ceiling, not the floor: this is the knob
// that trades wall-clock for peak memory, and the OOM it caused was silent.
const concurrency = Number(
  buildAlbumSource.match(/const ANALYZE_CONCURRENCY = (\d+);/)?.[1],
);
assert(
  Number.isFinite(concurrency) && concurrency >= 1 && concurrency <= 3,
  `analysis concurrency must stay within the range measured as oom-free (got ${concurrency})`,
);

console.log("album analysis proxy self-check passed");
