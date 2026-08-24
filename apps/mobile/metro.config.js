// Bundle on-device model files as opaque assets so static `require()` calls
// resolve to installed file URIs in release builds.
const { getDefaultConfig } = require("expo/metro-config");

const config = getDefaultConfig(__dirname);
for (const extension of ["onnx", "tflite"]) {
  if (!config.resolver.assetExts.includes(extension)) {
    config.resolver.assetExts.push(extension);
  }
}

module.exports = config;
