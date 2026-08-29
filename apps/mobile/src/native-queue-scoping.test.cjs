const { readFileSync } = require("node:fs");
const { resolve } = require("node:path");

function assert(value, message) {
  if (!value) throw new Error(`native queue scoping self-check failed: ${message}`);
}

/**
 * Expo runs EVERY AsyncFunction, from EVERY module in the app, on ONE shared
 * HandlerThread ("expo.modules.AsyncFunctionQueue", see AppContext.kt). Any
 * long native call left on it blocks every other native call in the app.
 *
 * This has now regressed twice, which is why it is a gate and not a comment:
 *   - #60: `clusterFaces` and `thumbnailUri` shared that queue, so the photo
 *     grid stopped filling in for the ~40s of a recluster, and thumbnail
 *     resolution drifted 12ms -> 87.4ms purely on queue wait.
 *   - A later cherry-pick dropped `clusterScope` and re-declared `clusterFaces`
 *     as a SYNCHRONOUS `Function`, which is worse still: that form runs on the
 *     JS thread and freezes the UI outright.
 *
 * Only the genuinely expensive entry points are pinned. Cheap ones (`log`,
 * `isSupported`, `isBatteryUnrestricted`, service start/stop) are fine on the
 * shared queue and are deliberately not listed -- the rule is "long work gets
 * its own thread", not "every function needs ceremony".
 */
const PINNED = [
  {
    module: "photeo-scan-service",
    file: "modules/photeo-scan-service/android/src/main/java/expo/modules/photeoscanservice/PhoteoScanServiceModule.kt",
    functions: ["thumbnailUri", "clusterFaces"],
  },
  {
    module: "photeo-litert",
    file: "modules/photeo-litert/android/src/main/java/expo/modules/photeolitert/PhoteoLiteRtModule.kt",
    functions: ["runTinyClip", "runFaceIdentity"],
  },
  {
    module: "photeo-album-pdf",
    file: "modules/photeo-album-pdf/android/src/main/java/expo/modules/photeoalbumpdf/PhoteoAlbumPdfModule.kt",
    functions: ["generate", "renderPage"],
  },
];

for (const { module, file, functions } of PINNED) {
  const source = readFileSync(resolve(__dirname, "..", file), "utf8");

  assert(
    /CoroutineScope\(/.test(source),
    `${module} declares no CoroutineScope, so its work runs on the shared Expo queue`,
  );

  for (const name of functions) {
    // A synchronous `Function` is the worse failure: it runs on the JS thread.
    assert(
      !new RegExp(`(^|[^c])\\bFunction\\("${name}"\\)`, "m").test(source),
      `${module}.${name} is a synchronous Function -- that runs on the JS thread and freezes the UI`,
    );

    const start = source.indexOf(`AsyncFunction("${name}")`);
    assert(start >= 0, `${module}.${name} is missing entirely`);

    // The declaration's own body ends where the next one begins (or at OnDestroy
    // / end of definition), so `.runOnQueue` must appear inside that slice.
    const rest = source.slice(start + 1);
    const nextDecl = rest.search(/\n\s*(Async)?Function\("|\n\s*OnDestroy\b/);
    const body = nextDecl === -1 ? rest : rest.slice(0, nextDecl);

    assert(
      /\}\s*\.runOnQueue\(/.test(body),
      `${module}.${name} is not scoped with runOnQueue, so it shares the app-wide Expo queue`,
    );
  }
}

console.log(`native queue scoping self-check passed (${PINNED.length} modules)`);
