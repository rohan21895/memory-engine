import { readFile } from "node:fs/promises";

import sharp, { type OverlayOptions } from "sharp";

import type {
  Page,
  Placement,
  TextBlock,
} from "../../../contracts/codegen/generated/typescript/index.js";

import { canonicalJson, digestBytes, digestParts } from "./digest.js";
import { applyEnhancements } from "./enhance.js";
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
  cacheIdentity: PageCacheIdentity;
}

export const PAGE_CACHE_FORMAT_VERSION = 2 as const;
export const RENDER_PRINT_PAGE_RENDERER_VERSION = "render-print-page-v2";

export interface PageSourceDigest {
  kind: "placement" | "font";
  reference: string;
  digest: string;
}

export interface PageCacheIdentity {
  cacheVersion: typeof PAGE_CACHE_FORMAT_VERSION;
  rendererVersion: string;
  pagePlanDigest: string;
  sourceDigests: PageSourceDigest[];
  iccDigest: string;
  dpi: number;
  dpiFloor: number;
  widthPx: number;
  heightPx: number;
  colorSpace: CheckedIccProfile["colorSpace"];
  components: CheckedIccProfile["components"];
  cacheKey: string;
}

interface PreparedPlacement {
  placement: Placement;
  bytes: Buffer;
}

interface PreparedText {
  block: TextBlock;
  bytes: Buffer;
}

export interface PreparedPageRender {
  placements: PreparedPlacement[];
  textBlocks: PreparedText[];
  identity: PageCacheIdentity;
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

export function isPageCacheIdentity(value: unknown): value is PageCacheIdentity {
  if (typeof value !== "object" || value === null || Array.isArray(value)) return false;
  const identity = value as Record<string, unknown>;
  if (
    identity.cacheVersion !== PAGE_CACHE_FORMAT_VERSION ||
    identity.rendererVersion !== RENDER_PRINT_PAGE_RENDERER_VERSION ||
    typeof identity.pagePlanDigest !== "string" ||
    !/^[0-9a-f]{64}$/.test(identity.pagePlanDigest) ||
    !Array.isArray(identity.sourceDigests) ||
    typeof identity.iccDigest !== "string" ||
    !/^[0-9a-f]{64}$/.test(identity.iccDigest) ||
    typeof identity.dpi !== "number" ||
    !Number.isFinite(identity.dpi) ||
    identity.dpi <= 0 ||
    typeof identity.dpiFloor !== "number" ||
    !Number.isFinite(identity.dpiFloor) ||
    identity.dpiFloor <= 0 ||
    typeof identity.widthPx !== "number" ||
    !Number.isInteger(identity.widthPx) ||
    identity.widthPx <= 0 ||
    typeof identity.heightPx !== "number" ||
    !Number.isInteger(identity.heightPx) ||
    identity.heightPx <= 0 ||
    (identity.colorSpace !== "rgb" && identity.colorSpace !== "cmyk") ||
    (identity.components !== 3 && identity.components !== 4) ||
    (identity.colorSpace === "rgb" && identity.components !== 3) ||
    (identity.colorSpace === "cmyk" && identity.components !== 4) ||
    typeof identity.cacheKey !== "string" ||
    !/^[0-9a-f]{64}$/.test(identity.cacheKey)
  ) {
    return false;
  }
  return identity.sourceDigests.every((source) => {
    if (typeof source !== "object" || source === null || Array.isArray(source)) return false;
    const digest = source as Record<string, unknown>;
    return (
      (digest.kind === "placement" || digest.kind === "font") &&
      typeof digest.reference === "string" &&
      typeof digest.digest === "string" &&
      /^[0-9a-f]{64}$/.test(digest.digest)
    );
  });
}

export function pageCacheIdentityMatches(
  candidate: unknown,
  expected: PageCacheIdentity,
): candidate is PageCacheIdentity {
  return isPageCacheIdentity(candidate) && canonicalJson(candidate) === canonicalJson(expected);
}

export async function preparePageRender(context: PageRenderContext): Promise<PreparedPageRender> {
  const widthPx = mmToSize(context.widthMm, context.dpi);
  const heightPx = mmToSize(context.heightMm, context.dpi);
  const placements: PreparedPlacement[] = [];
  const textBlocks: PreparedText[] = [];
  const sourceDigests: PageSourceDigest[] = [];
  const orderedPlacements = context.page.placements
    .map((placement, order) => ({ placement, order }))
    .sort((left, right) =>
      (left.placement.z_index ?? 0) - (right.placement.z_index ?? 0) || left.order - right.order,
    );

  for (const { placement } of orderedPlacements) {
    let resolved: LocalAsset;
    try {
      resolved = await context.resolvePlacementAsset(placement);
    } catch {
      throw new RenderPrintError("file_not_found", `${placement.placement_id} has no resolved render asset.`);
    }
    const bytes = await assetBytes(resolved, placement.placement_id);
    placements.push({ placement, bytes });
    sourceDigests.push({
      kind: "placement",
      reference: `${placement.placement_id}:${placement.media_id}`,
      digest: digestBytes(bytes),
    });
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
    textBlocks.push({ block, bytes });
    sourceDigests.push({
      kind: "font",
      reference: `${block.block_id}:${block.font_family}`,
      digest: digestBytes(bytes),
    });
  }

  const pagePlanDigest = digestBytes(new TextEncoder().encode(canonicalJson(context.page)));
  const identityParts: string[] = [
    `cache-format:${PAGE_CACHE_FORMAT_VERSION}`,
    `renderer:${RENDER_PRINT_PAGE_RENDERER_VERSION}`,
    `plan:${pagePlanDigest}`,
    `icc:${context.icc.digest}`,
    `dpi:${context.dpi}`,
    `dpi-floor:${context.dpiFloor}`,
    `raster:${widthPx}x${heightPx}`,
    `color:${context.icc.colorSpace}:${context.icc.components}`,
  ];
  for (const source of sourceDigests) {
    identityParts.push(`${source.kind}:${source.reference}`, source.digest);
  }
  const identity: PageCacheIdentity = {
    cacheVersion: PAGE_CACHE_FORMAT_VERSION,
    rendererVersion: RENDER_PRINT_PAGE_RENDERER_VERSION,
    pagePlanDigest,
    sourceDigests,
    iccDigest: context.icc.digest,
    dpi: context.dpi,
    dpiFloor: context.dpiFloor,
    widthPx,
    heightPx,
    colorSpace: context.icc.colorSpace,
    components: context.icc.components,
    cacheKey: digestParts(identityParts),
  };
  return { placements, textBlocks, identity };
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
    .resize(targetWidth, targetHeight, { fit: "fill", kernel: sharp.kernel.lanczos3 });
  if ((placement.enhancement_ops ?? []).length > 0) {
    // Baked through a lossless intermediate rather than chained: sharp applies
    // queued operations in ITS order, not call order, so `linear` on a
    // still-alpha-free image is only guaranteed by finishing this pipeline
    // before alpha exists. removeAlpha first: per-channel linear wants exactly
    // the colour bands, and a PNG source may carry four.
    const developed = await applyEnhancements(pipeline.removeAlpha(), placement)
      .png({ compressionLevel: 1, adaptiveFiltering: false })
      .toBuffer();
    pipeline = sharp(developed);
  }
  pipeline = pipeline.ensureAlpha();

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

export async function renderPage(
  context: PageRenderContext,
  prepared?: PreparedPageRender,
): Promise<RenderedPage> {
  const inputs = prepared ?? (await preparePageRender(context));
  const widthPx = mmToSize(context.widthMm, context.dpi);
  const heightPx = mmToSize(context.heightMm, context.dpi);
  const background = context.page.background;
  const backgroundColor =
    background?.kind === "solid"
      ? assertHexColor(background.color_hex ?? "#ffffff", `page ${context.page.page_index}`)
      : "#ffffff";

  const composites: OverlayOptions[] = [];
  for (const { placement, bytes } of inputs.placements) {
    const rendered = await renderPlacement(placement, bytes, context.dpi, context.dpiFloor);
    composites.push({ input: rendered.image, left: rendered.left, top: rendered.top });
  }

  for (const { block, bytes } of inputs.textBlocks) {
    const rendered = await renderText(block, bytes, context.dpi);
    composites.push({ input: rendered.image, left: rendered.left, top: rendered.top });
  }

  let output = sharp({
    create: { width: widthPx, height: heightPx, channels: 4, background: backgroundColor },
  }).composite(composites);
  output = output.flatten({ background: backgroundColor });
  output = output
    .withIccProfile(context.icc.transformProfile)
    .toColourspace(context.icc.colorSpace)
    // JPEG carries no alpha, and the composite pipeline is still carrying one:
    // after `toColourspace("cmyk")` this image has FIVE bands, four ink plus
    // alpha. Dropping it here is what makes the band count equal to the
    // profile's component count below.
    .removeAlpha();

  // This USED TO round-trip through `.raw()` and re-wrap the buffer with an
  // explicit `channels: icc.components`, then convert to the target colourspace
  // a second time. Both halves of that were wrong and neither raised:
  //
  //   * the raw buffer had 5 bands and was re-wrapped as 4, so every row was
  //     read at the wrong stride -- the page sheared, and the single placement
  //     smeared across the full page width and repeated about five times;
  //   * sharp reads a 4-band raw buffer as RGBA, so the ink values were then
  //     converted CMYK->as-if-RGBA->CMYK. The 'alpha' was the K band, so a
  //     12/255 black turned the whole photo 95% transparent and it flattened
  //     to near-white.
  //
  // Measured on a 306mm page with one 121.8x91.3mm placement at (92.1, 107.3):
  // content occupied 27.1% of the page with a bounding box covering all of it,
  // instead of the declared 11.9%. Every PDF this worker produced was
  // geometrically and tonally wrong, and the print validator passed each one,
  // because the validator measures the AlbumSpec and this function did not
  // execute it. Encoding straight from the pipeline is both correct and one
  // fewer full-page buffer.
  const jpeg = await output
    .jpeg({ quality: 95, chromaSubsampling: "4:4:4", optimizeScans: false, trellisQuantisation: false })
    .toBuffer();
  const encoded = await sharp(jpeg).metadata();
  if (encoded.width !== widthPx || encoded.height !== heightPx || encoded.channels !== context.icc.components) {
    // A page raster that is not the size and band count the vendor profile
    // implies is not a page. Refuse rather than emit a plausible book.
    throw new RenderPrintError(
      "validation_failed",
      `page ${context.page.page_index} rasterised to ${encoded.width}x${encoded.height} in ` +
        `${encoded.channels} channels; the ${context.icc.colorSpace} profile at ${context.dpi} DPI ` +
        `requires ${widthPx}x${heightPx} in ${context.icc.components}.`,
    );
  }
  return { jpeg, widthPx, heightPx, cacheIdentity: inputs.identity };
}
