import { readFile } from "node:fs/promises";

import sharp, { type OverlayOptions } from "sharp";

import type {
  Page,
  Placement,
  TextBlock,
} from "../../../contracts/codegen/generated/typescript/index.js";

import { digestParts } from "./digest.js";
import { RenderPrintError } from "./errors.js";
import type { CheckedIccProfile } from "./icc.js";

export type LocalAsset = string | Uint8Array;
export type PlacementAssetResolver = (placement: Placement) => Promise<LocalAsset>;
export type FontResolver = (fontFamily: string) => Promise<LocalAsset>;

export interface PageRenderContext {
  page: Page;
  widthMm: number;
  heightMm: number;
  dpi: number;
  dpiFloor: number;
  icc: CheckedIccProfile;
  resolvePlacementAsset: PlacementAssetResolver;
  resolveFont: FontResolver;
}

export interface RenderedPage {
  jpeg: Buffer;
  widthPx: number;
  heightPx: number;
  cacheKey: string;
}

const MM_PER_INCH = 25.4;

function mmToPixels(value: number, dpi: number): number {
  return Math.round((value * dpi) / MM_PER_INCH);
}

function mmToSize(value: number, dpi: number): number {
  return Math.max(1, mmToPixels(value, dpi));
}

function assertHexColor(value: string, subject: string): string {
  if (!/^#[0-9a-fA-F]{6}$/.test(value)) {
    throw new RenderPrintError("validation_failed", `${subject} has an invalid print color.`);
  }
  return value;
}

async function assetBytes(asset: LocalAsset, subject: string): Promise<Buffer> {
  try {
    return typeof asset === "string" ? await readFile(asset) : Buffer.from(asset);
  } catch {
    throw new RenderPrintError("file_unreadable", `${subject} could not be read.`);
  }
}

function escapeXml(value: string): string {
  return value
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&apos;");
}

async function renderPlacement(
  placement: Placement,
  sourceBytes: Buffer,
  dpi: number,
  dpiFloor: number,
): Promise<{ image: Buffer; left: number; top: number }> {
  let oriented: Buffer;
  try {
    oriented = await sharp(sourceBytes, { failOn: "error" }).autoOrient().toBuffer();
  } catch {
    throw new RenderPrintError("file_corrupt", `${placement.placement_id} could not be decoded.`);
  }
  const metadata = await sharp(oriented).metadata();
  if (!metadata.width || !metadata.height) {
    throw new RenderPrintError("file_corrupt", `${placement.placement_id} has no usable pixel dimensions.`);
  }

  const x0 = Math.max(0, Math.floor(placement.crop.x * metadata.width));
  const y0 = Math.max(0, Math.floor(placement.crop.y * metadata.height));
  const x1 = Math.min(metadata.width, Math.ceil((placement.crop.x + placement.crop.w) * metadata.width));
  const y1 = Math.min(metadata.height, Math.ceil((placement.crop.y + placement.crop.h) * metadata.height));
  const cropWidth = x1 - x0;
  const cropHeight = y1 - y0;
  if (cropWidth < 1 || cropHeight < 1) {
    throw new RenderPrintError("validation_failed", `${placement.placement_id} has an empty source crop.`);
  }

  const actualDpi = Math.min(
    (cropWidth * MM_PER_INCH) / placement.frame.width_mm,
    (cropHeight * MM_PER_INCH) / placement.frame.height_mm,
  );
  if (actualDpi + 1e-6 < dpiFloor) {
    throw new RenderPrintError(
      "validation_failed",
      `${placement.placement_id} resolves below the vendor DPI floor at render time.`,
    );
  }

  const cropAspect = cropWidth / cropHeight;
  const frameAspect = placement.frame.width_mm / placement.frame.height_mm;
  if (Math.abs(cropAspect / frameAspect - 1) > 0.01) {
    throw new RenderPrintError(
      "validation_failed",
      `${placement.placement_id} crop and frame aspect ratios do not match.`,
    );
  }

  const targetWidth = mmToSize(placement.frame.width_mm, dpi);
  const targetHeight = mmToSize(placement.frame.height_mm, dpi);
  let pipeline = sharp(oriented)
    .extract({ left: x0, top: y0, width: cropWidth, height: cropHeight })
    .resize(targetWidth, targetHeight, { fit: "fill", kernel: sharp.kernel.lanczos3 })
    .ensureAlpha();

  if (placement.border && placement.border.width_mm > 0) {
    const borderWidth = Math.max(1, mmToPixels(placement.border.width_mm, dpi));
    const color = assertHexColor(placement.border.color_hex, placement.placement_id);
    const overlay = Buffer.from(
      `<svg width="${targetWidth}" height="${targetHeight}" xmlns="http://www.w3.org/2000/svg">` +
        `<rect x="${borderWidth / 2}" y="${borderWidth / 2}" width="${targetWidth - borderWidth}" ` +
        `height="${targetHeight - borderWidth}" fill="none" stroke="${color}" stroke-width="${borderWidth}"/>` +
        `</svg>`,
    );
    pipeline = pipeline.composite([{ input: overlay, left: 0, top: 0 }]);
  }

  const rotation = placement.frame.rotation_deg ?? 0;
  if (rotation !== 0) {
    pipeline = pipeline.rotate(rotation, { background: { r: 0, g: 0, b: 0, alpha: 0 } });
  }
  const image = await pipeline.png({ compressionLevel: 9, adaptiveFiltering: false }).toBuffer();
  const rotated = await sharp(image).metadata();
  const left = mmToPixels(placement.frame.x_mm, dpi) + Math.round((targetWidth - (rotated.width ?? targetWidth)) / 2);
  const top = mmToPixels(placement.frame.y_mm, dpi) + Math.round((targetHeight - (rotated.height ?? targetHeight)) / 2);
  return { image, left, top };
}

async function renderText(
  block: TextBlock,
  fontBytes: Buffer,
  dpi: number,
): Promise<{ image: Buffer; left: number; top: number }> {
  if (!block.font_family || !block.font_size_pt) {
    throw new RenderPrintError("validation_failed", `${block.block_id} does not pin a font family and size.`);
  }
  const width = mmToSize(block.frame.width_mm, dpi);
  const height = mmToSize(block.frame.height_mm, dpi);
  const fontSize = (block.font_size_pt * dpi) / 72;
  const alignment = block.alignment ?? "left";
  const anchor = alignment === "center" ? "middle" : alignment === "right" ? "end" : "start";
  const x = alignment === "center" ? width / 2 : alignment === "right" ? width : 0;
  const color = assertHexColor(block.color_hex ?? "#000000", block.block_id);
  const fontData = fontBytes.toString("base64");
  const svg = Buffer.from(
    `<svg width="${width}" height="${height}" xmlns="http://www.w3.org/2000/svg">` +
      `<style>@font-face{font-family:'MemoryEngine';src:url(data:font/truetype;base64,${fontData})}</style>` +
      `<text x="${x}" y="0" dominant-baseline="text-before-edge" text-anchor="${anchor}" ` +
      `font-family="MemoryEngine" font-size="${fontSize}" fill="${color}">${escapeXml(block.text)}</text>` +
      `</svg>`,
  );
  let pipeline = sharp(svg).ensureAlpha();
  const rotation = block.frame.rotation_deg ?? 0;
  if (rotation !== 0) {
    pipeline = pipeline.rotate(rotation, { background: { r: 0, g: 0, b: 0, alpha: 0 } });
  }
  const image = await pipeline.png({ compressionLevel: 9, adaptiveFiltering: false }).toBuffer();
  const rotated = await sharp(image).metadata();
  const left = mmToPixels(block.frame.x_mm, dpi) + Math.round((width - (rotated.width ?? width)) / 2);
  const top = mmToPixels(block.frame.y_mm, dpi) + Math.round((height - (rotated.height ?? height)) / 2);
  return { image, left, top };
}

export async function renderPage(context: PageRenderContext): Promise<RenderedPage> {
  const widthPx = mmToSize(context.widthMm, context.dpi);
  const heightPx = mmToSize(context.heightMm, context.dpi);
  const background = context.page.background;
  const backgroundColor =
    background?.kind === "solid"
      ? assertHexColor(background.color_hex ?? "#ffffff", `page ${context.page.page_index}`)
      : "#ffffff";

  const pageParts: (string | Uint8Array)[] = [JSON.stringify(context.page), String(context.dpi), context.icc.digest];
  const composites: OverlayOptions[] = [];
  const placements = context.page.placements
    .map((placement, order) => ({ placement, order }))
    .sort((left, right) =>
      (left.placement.z_index ?? 0) - (right.placement.z_index ?? 0) || left.order - right.order,
    );

  for (const { placement } of placements) {
    let resolved: LocalAsset;
    try {
      resolved = await context.resolvePlacementAsset(placement);
    } catch {
      throw new RenderPrintError("file_not_found", `${placement.placement_id} has no resolved render asset.`);
    }
    const bytes = await assetBytes(resolved, placement.placement_id);
    pageParts.push(bytes);
    const rendered = await renderPlacement(placement, bytes, context.dpi, context.dpiFloor);
    composites.push({ input: rendered.image, left: rendered.left, top: rendered.top });
  }

  for (const block of context.page.text_blocks ?? []) {
    if (!block.font_family) {
      throw new RenderPrintError("validation_failed", `${block.block_id} does not pin a font family.`);
    }
    let resolved: LocalAsset;
    try {
      resolved = await context.resolveFont(block.font_family);
    } catch {
      throw new RenderPrintError("file_not_found", `${block.block_id} has no resolved font asset.`);
    }
    const bytes = await assetBytes(resolved, block.block_id);
    pageParts.push(bytes);
    const rendered = await renderText(block, bytes, context.dpi);
    composites.push({ input: rendered.image, left: rendered.left, top: rendered.top });
  }

  let output = sharp({
    create: { width: widthPx, height: heightPx, channels: 4, background: backgroundColor },
  }).composite(composites);
  output = output.flatten({ background: backgroundColor });
  output = output.withIccProfile(context.icc.transformProfile).toColourspace(context.icc.colorSpace);
  const converted = await output.raw().toBuffer({ resolveWithObject: true });
  const jpeg = await sharp(converted.data, {
    raw: {
      width: converted.info.width,
      height: converted.info.height,
      channels: context.icc.components,
    },
  })
    .toColourspace(context.icc.colorSpace)
    .jpeg({ quality: 95, chromaSubsampling: "4:4:4", optimizeScans: false, trellisQuantisation: false })
    .toBuffer();
  return { jpeg, widthPx, heightPx, cacheKey: digestParts(pageParts) };
}
