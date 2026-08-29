const { readFileSync } = require("node:fs");
const { resolve } = require("node:path");

function assert(value, message) {
  if (!value) throw new Error(`photo-tile thumbnail self-check failed: ${message}`);
}

const photosScreen = readFileSync(resolve(__dirname, "PhotosScreen.tsx"), "utf8");
const tileStart = photosScreen.indexOf("function PhotoTile(");
const tileEnd = photosScreen.indexOf("function PhotoViewer(", tileStart);
assert(tileStart >= 0 && tileEnd > tileStart, "PhotoTile source is discoverable");

const photoTile = photosScreen.slice(tileStart, tileEnd);
assert(photoTile.includes("source={thumb}"), "PhotoTile renders the resolved thumbnail");
assert(!photoTile.includes("contentUri("), "PhotoTile never receives the original MediaStore URI");
assert(!photoTile.includes("photo.uri"), "PhotoTile never receives a stored original URI");

const nativeLoader = readFileSync(
  resolve(
    __dirname,
    "../../../modules/photeo-scan-service/android/src/main/java/expo/modules/photeoscanservice/MediaStoreThumbnailLoader.kt",
  ),
  "utf8",
);
assert(
  nativeLoader.includes("contentResolver.loadThumbnail(source, Size(edge, edge), null)"),
  "API 29+ uses ContentResolver.loadThumbnail",
);
assert(
  nativeLoader.includes("decoder.setTargetSize(width, height)"),
  "missing MediaStore thumbnails use a target-sized fallback decode",
);
assert(
  !nativeLoader.includes("readBytes(") && !nativeLoader.includes("decodeByteArray("),
  "native fallback never buffers the compressed original",
);

console.log("photo-tile thumbnail self-check passed");
