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
assert(
  buildAlbumSource.includes("const ANALYZE_CONCURRENCY = 1;"),
  "the diagnostic one-photo concurrency stopgap must remain in place",
);

console.log("album analysis proxy self-check passed");
