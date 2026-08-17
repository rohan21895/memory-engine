import { readFile } from "node:fs/promises";

import type { VendorProfileColorProfile } from "../../../contracts/codegen/generated/typescript/index.js";

import { digestBytes } from "./digest.js";
import { RenderPrintError } from "./errors.js";

export type PrintColorSpace = "rgb" | "cmyk";

export interface IccProfileInput {
  name: string;
  path?: string;
  builtin?: "srgb" | "p3" | "cmyk";
}

export interface CheckedIccProfile extends IccProfileInput {
  bytes: Buffer;
  digest: string;
  colorSpace: PrintColorSpace;
  components: 3 | 4;
  transformProfile: string;
}

function embeddedIccFromJpeg(jpeg: Buffer): Buffer {
  const chunks = new Map<number, Buffer>();
  let expectedCount = 0;
  for (let offset = 2; offset + 4 <= jpeg.length; ) {
    if (jpeg[offset] !== 0xff) break;
    const marker = jpeg[offset + 1];
    if (marker === 0xda || marker === 0xd9) break;
    const length = jpeg.readUInt16BE(offset + 2);
    if (length < 2 || offset + 2 + length > jpeg.length) break;
    if (marker === 0xe2) {
      const payload = jpeg.subarray(offset + 4, offset + 2 + length);
      if (payload.subarray(0, 12).toString("binary") === "ICC_PROFILE\0" && payload.length >= 14) {
        chunks.set(payload[12] ?? 0, payload.subarray(14));
        expectedCount = payload[13] ?? 0;
      }
    }
    offset += 2 + length;
  }
  if (expectedCount < 1 || chunks.size !== expectedCount) {
    throw new RenderPrintError("internal_error", "Sharp did not expose its built-in ICC profile.");
  }
  return Buffer.concat(
    Array.from({ length: expectedCount }, (_, index) => chunks.get(index + 1) ?? Buffer.alloc(0)),
  );
}

async function loadBuiltinProfile(name: "srgb" | "p3" | "cmyk"): Promise<Buffer> {
  const { default: sharp } = await import("sharp");
  const target = name === "cmyk" ? "cmyk" : "srgb";
  const jpeg = await sharp({
    create: { width: 1, height: 1, channels: 3, background: "#ffffff" },
  })
    .withIccProfile(name)
    .toColourspace(target)
    .jpeg()
    .toBuffer();
  return embeddedIccFromJpeg(jpeg);
}

export async function loadAndCheckIccProfile(
  expected: VendorProfileColorProfile,
  input: IccProfileInput,
): Promise<CheckedIccProfile> {
  if (input.name !== expected.icc_name) {
    throw new RenderPrintError("validation_failed", "Resolved ICC profile name does not match the vendor profile.");
  }

  if ((input.path ? 1 : 0) + (input.builtin ? 1 : 0) !== 1) {
    throw new RenderPrintError("validation_failed", "Select exactly one ICC file or built-in profile.");
  }
  let bytes: Buffer;
  try {
    bytes = input.path ? await readFile(input.path) : await loadBuiltinProfile(input.builtin!);
  } catch (error) {
    if (error instanceof RenderPrintError) throw error;
    throw new RenderPrintError("file_unreadable", "The selected ICC profile could not be read.");
  }
  if (bytes.length < 128 || bytes.toString("ascii", 36, 40) !== "acsp") {
    throw new RenderPrintError("validation_failed", "The selected color profile is not a valid ICC profile.");
  }
  const declaredLength = bytes.readUInt32BE(0);
  if (declaredLength > bytes.length || declaredLength < 128) {
    throw new RenderPrintError("validation_failed", "The selected ICC profile has an invalid declared length.");
  }

  const digest = digestBytes(bytes);
  if (expected.icc_hash && digest !== expected.icc_hash) {
    throw new RenderPrintError("validation_failed", "The selected ICC profile does not match the vendor's pinned digest.");
  }

  const signature = bytes.toString("ascii", 16, 20);
  if (signature !== "CMYK" && signature !== "RGB ") {
    throw new RenderPrintError("unsupported_format", "Only RGB and CMYK print ICC profiles are supported.");
  }
  const colorSpace: PrintColorSpace = signature === "CMYK" ? "cmyk" : "rgb";
  return {
    ...input,
    bytes,
    digest,
    colorSpace,
    components: colorSpace === "cmyk" ? 4 : 3,
    transformProfile: input.path ?? input.builtin!,
  };
}
