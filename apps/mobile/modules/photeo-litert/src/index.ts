type PhoteoLiteRtNative = {
  probeTinyClip(modelUri: string): Promise<boolean>;
  runTinyClip(modelUri: string, input: Uint8Array): Promise<Uint8Array>;
  releaseTinyClip(): Promise<void>;
  probeFaceIdentity(modelUri: string): Promise<boolean>;
  runFaceIdentity(modelUri: string, input: Uint8Array): Promise<Uint8Array>;
  releaseFaceIdentity(): Promise<void>;
};

const EMBEDDING_BYTES = 512 * Float32Array.BYTES_PER_ELEMENT;

/** `undefined` means not resolved yet; `null` means this build has no module. */
let cached: PhoteoLiteRtNative | null | undefined;

async function nativeModule(): Promise<PhoteoLiteRtNative | null> {
  if (cached !== undefined) return cached;
  try {
    const { requireOptionalNativeModule } = await import("expo");
    cached =
      requireOptionalNativeModule<PhoteoLiteRtNative>("PhoteoLiteRt") ?? null;
  } catch {
    cached = null;
  }
  return cached;
}

/** A byte view over exactly the ArrayBuffer the existing model API supplied. */
export function nativeTensorBytes(input: ArrayBuffer): Uint8Array {
  return new Uint8Array(input);
}

/**
 * Detaches the bridge result from any larger native/JS backing allocation.
 * The model wrappers continue to receive the ArrayBuffer shape they used from
 * react-native-fast-tflite, including the exact float32 bit patterns.
 */
export function embeddingOutputBuffer(output: Uint8Array): ArrayBuffer {
  if (output.byteLength !== EMBEDDING_BYTES) {
    throw new Error(
      `LiteRT embedding holds ${output.byteLength} bytes, expected ${EMBEDDING_BYTES}`,
    );
  }
  const copied = new Uint8Array(EMBEDDING_BYTES);
  copied.set(output);
  return copied.buffer;
}

export async function probeNativeTinyClip(modelUri: string): Promise<boolean> {
  const native = await nativeModule();
  return native ? native.probeTinyClip(modelUri) : false;
}

export async function runNativeTinyClip(
  modelUri: string,
  input: ArrayBuffer,
): Promise<ArrayBuffer> {
  const native = await nativeModule();
  if (!native) throw new Error("PhoteoLiteRt is unavailable");
  return embeddingOutputBuffer(
    await native.runTinyClip(modelUri, nativeTensorBytes(input)),
  );
}

export async function releaseNativeTinyClip(): Promise<void> {
  await (await nativeModule())?.releaseTinyClip();
}

export async function probeNativeFaceIdentity(
  modelUri: string,
): Promise<boolean> {
  const native = await nativeModule();
  return native ? native.probeFaceIdentity(modelUri) : false;
}

export async function runNativeFaceIdentity(
  modelUri: string,
  input: ArrayBuffer,
): Promise<ArrayBuffer> {
  const native = await nativeModule();
  if (!native) throw new Error("PhoteoLiteRt is unavailable");
  return embeddingOutputBuffer(
    await native.runFaceIdentity(modelUri, nativeTensorBytes(input)),
  );
}

export async function releaseNativeFaceIdentity(): Promise<void> {
  await (await nativeModule())?.releaseFaceIdentity();
}
