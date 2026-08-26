/**
 * Materializes a Metro-bundled graph before handing it to fast-tflite.
 * Android raw resources resolve to a bare resource name; fast-tflite v3 treats
 * that as java.net.URL and fails. expo-asset provides the required file URI.
 */
export async function bundledTfliteSource(moduleId: number): Promise<{ url: string }> {
  const { Asset } = await import("expo-asset");
  const asset = Asset.fromModule(moduleId);
  await asset.downloadAsync();
  const url = asset.localUri;
  if (!url || !url.startsWith("file://")) {
    throw new Error("Bundled TFLite asset did not materialize to a local file URI.");
  }
  return { url };
}
