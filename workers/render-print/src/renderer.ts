import { constants } from "node:fs";
import { access, link, mkdir, readFile, unlink, writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";

import sharp from "sharp";

import type { AlbumSpec } from "../../../contracts/codegen/generated/typescript/index.js";

import { digestBytes } from "./digest.js";
import { RenderPrintError } from "./errors.js";
import { assertRenderGate } from "./gate.js";
import { loadAndCheckIccProfile, type CheckedIccProfile, type IccProfileInput } from "./icc.js";
import {
  renderPage,
  type FontResolver,
  type PlacementAssetResolver,
  type RenderedPage,
} from "./page.js";
import { buildDeterministicPdf } from "./pdf.js";

export interface PageArtifact {
  index: number;
  id: string;
  path: string;
}

export interface RenderPrintOptions {
  iccProfile: IccProfileInput;
  resolvePlacementAsset: PlacementAssetResolver;
  resolveFont: FontResolver;
  pageStoreDirectory?: string;
  resumedPages?: ReadonlyMap<number, PageArtifact>;
  onPageComplete?: (artifact: PageArtifact) => Promise<void> | void;
}

export interface RenderPrintResult {
  pdf: Buffer;
  id: string;
  dpi: number;
  colorSpace: CheckedIccProfile["colorSpace"];
  iccDigest: string;
  pages: PageArtifact[];
}

async function readResumedPage(
  artifact: PageArtifact,
  expectedWidth: number,
  expectedHeight: number,
): Promise<RenderedPage | null> {
  try {
    const jpeg = await readFile(artifact.path);
    if (digestBytes(jpeg) !== artifact.id) return null;
    const metadata = await sharp(jpeg).metadata();
    if (metadata.width !== expectedWidth || metadata.height !== expectedHeight) return null;
    return { jpeg, widthPx: expectedWidth, heightPx: expectedHeight, cacheKey: artifact.id };
  } catch {
    return null;
  }
}

async function persistPage(
  page: RenderedPage,
  index: number,
  directory: string,
): Promise<PageArtifact> {
  await mkdir(directory, { recursive: true });
  const id = digestBytes(page.jpeg);
  const path = join(directory, `${String(index).padStart(4, "0")}-${id}.jpg`);
  try {
    await writeFile(path, page.jpeg, { flag: "wx" });
  } catch (error) {
    const code = (error as NodeJS.ErrnoException).code;
    if (code !== "EEXIST") throw error;
    const existing = await readFile(path);
    if (digestBytes(existing) !== id) {
      throw new RenderPrintError("internal_error", "A content-addressed page cache entry was inconsistent.");
    }
  }
  return { index, id, path };
}

export async function renderAlbum(spec: AlbumSpec, options: RenderPrintOptions): Promise<RenderPrintResult> {
  assertRenderGate(spec);
  const icc = await loadAndCheckIccProfile(spec.vendor_profile.color_profile, options.iccProfile);
  const dpi = Math.max(spec.vendor_profile.dpi_floor, spec.vendor_profile.dpi_preferred ?? 0);
  const widthMm = spec.vendor_profile.trim_size_mm.width_mm + spec.vendor_profile.bleed_mm * 2;
  const heightMm = spec.vendor_profile.trim_size_mm.height_mm + spec.vendor_profile.bleed_mm * 2;
  const widthPx = Math.max(1, Math.round((widthMm * dpi) / 25.4));
  const heightPx = Math.max(1, Math.round((heightMm * dpi) / 25.4));
  const renderedPages: RenderedPage[] = [];
  const artifacts: PageArtifact[] = [];

  for (const page of spec.pages) {
    const resumed = options.resumedPages?.get(page.page_index);
    const cached = resumed ? await readResumedPage(resumed, widthPx, heightPx) : null;
    const rendered =
      cached ??
      (await renderPage({
        page,
        widthMm,
        heightMm,
        dpi,
        dpiFloor: spec.vendor_profile.dpi_floor,
        icc,
        resolvePlacementAsset: options.resolvePlacementAsset,
        resolveFont: options.resolveFont,
      }));
    renderedPages.push(rendered);

    if (options.pageStoreDirectory) {
      const artifact = cached && resumed ? resumed : await persistPage(rendered, page.page_index, options.pageStoreDirectory);
      artifacts.push(artifact);
      if (!cached) await options.onPageComplete?.(artifact);
    }
  }

  const pdf = buildDeterministicPdf(spec, renderedPages, icc);
  return {
    pdf,
    id: digestBytes(pdf),
    dpi,
    colorSpace: icc.colorSpace,
    iccDigest: icc.digest,
    pages: artifacts,
  };
}

export async function writePdfOnce(path: string, result: RenderPrintResult): Promise<void> {
  await mkdir(dirname(path), { recursive: true });
  try {
    await access(path, constants.F_OK);
    const existing = await readFile(path);
    if (digestBytes(existing) === result.id) return;
    throw new RenderPrintError("validation_failed", "The output target already contains different bytes.");
  } catch (error) {
    if (error instanceof RenderPrintError) throw error;
    if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;
  }

  const partial = `${path}.partial-${result.id}`;
  try {
    await writeFile(partial, result.pdf, { flag: "wx" });
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== "EEXIST") throw error;
    const existing = await readFile(partial);
    if (digestBytes(existing) !== result.id) {
      throw new RenderPrintError("internal_error", "The content-addressed partial PDF was inconsistent.");
    }
  }

  try {
    await link(partial, path);
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== "EEXIST") throw error;
    const existing = await readFile(path);
    if (digestBytes(existing) !== result.id) {
      throw new RenderPrintError("validation_failed", "The output target changed during publication.");
    }
  }
  await unlink(partial).catch((error: NodeJS.ErrnoException) => {
    if (error.code !== "ENOENT") throw error;
  });
}
