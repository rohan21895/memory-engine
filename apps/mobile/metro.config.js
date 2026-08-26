// Bundle on-device model files as opaque assets so static `require()` calls
// resolve to installed file URIs in release builds.
const { getDefaultConfig } = require("expo/metro-config");

const config = getDefaultConfig(__dirname);
// "places" is JSON by content but must stay an opaque asset: a 1.9MB `require`
// of real JSON would be compiled into the Hermes bundle, where it costs startup
// on every launch. As an asset it is read and parsed once, lazily, on the first
// photo that has GPS.
for (const extension of ["tflite", "places"]) {
  if (!config.resolver.assetExts.includes(extension)) {
    config.resolver.assetExts.push(extension);
  }
}

module.exports = config;
