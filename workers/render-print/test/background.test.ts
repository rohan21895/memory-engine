// The media_blur backdrop must actually fill the page -- and only the page
// that declared it. Differential, like page-geometry.test.ts: render the same
// page with a solid background and with a media_blur one; the paper OUTSIDE
// the placement must change from white to (dimmed, blurred) photo tones,
// while the placement itself stays identical.

import sharp from "sharp";
import { describe, expect, it } from "vitest";

import type { Page } from "../../../contracts/codegen/generated/typescript/index.js";

import { loadAndCheckIccProfile } from "../src/icc.js";
import { renderPage } from "../src/page.js";
import { findTestFont, HASH_B, sourceJpeg } from "./helpers.js";

const PAGE_MM = 60;
const DPI = 300;
const FRAME = { x_mm: 12, y_mm: 21, width_mm: 24, height_mm: 16, rotation_deg: 0 };

function pageWith(background: Page["background"]): Page {
  return {
    page_index: 0,
    side: "right",
    background: background ?? null,
    placements: [
      {
        placement_id: "backdrop-probe",
        media_id: HASH_B,
        frame: FRAME,
        crop: { x: 0, y: 0, w: 1, h: 1, rotation_deg: 0 },
        effective_dpi: (1200 * 25.4) / FRAME.width_mm,
        z_index: 1,
        bleeds: [],
        is_hero: true,
        face_safety: {
          face_count: 0,
          all_faces_in_safe_zone: true,
          faces_in_gutter: 0,
          faces_in_trim_zone: 0,
          cropped_face_ids: [],
        },
        enhancement_ops: [],
      },
    ],
    text_blocks: [],
  };
}

/**
 * A structured source, NOT the flat helper: the double-resize bug this suite
 * exists to catch shipped a SHARP copy of the photo as the "backdrop", and on
 * a flat orange field a sharp copy and a blurred one are the same pixels.
 * Hard checker edges make blur measurable.
 */
async function checkeredJpeg(): Promise<Buffer> {
  const tile = 100;
  const cells: string[] = [];
  for (let row = 0; row < 8; row += 1) {
    for (let col = 0; col < 12; col += 1) {
      if ((row + col) % 2 === 0) {
        cells.push(`<rect x="${col * tile}" y="${row * tile}" width="${tile}" height="${tile}" fill="#c86432"/>`);
      }
    }
  }
  const svg = Buffer.from(
    `<svg width="1200" height="800" xmlns="http://www.w3.org/2000/svg">` +
      `<rect width="1200" height="800" fill="#204060"/>${cells.join("")}</svg>`,
  );
  return sharp(svg).jpeg({ quality: 95 }).toBuffer();
}

async function rasterise(background: Page["background"]) {
  const icc = await loadAndCheckIccProfile(
    { icc_name: "Sharp built-in CMYK", intent: "relative_colorimetric" },
    { name: "Sharp built-in CMYK", builtin: "cmyk" },
  );
  const source = await checkeredJpeg();
  const font = await findTestFont();
  const rendered = await renderPage({
    page: pageWith(background),
    widthMm: PAGE_MM,
    heightMm: PAGE_MM,
    dpi: DPI,
    dpiFloor: 300,
    icc,
    resolvePlacementAsset: async () => source,
    resolveFont: async () => font,
  });
  return sharp(rendered.jpeg).raw().toBuffer({ resolveWithObject: true });
}

describe("the media_blur page backdrop", () => {
  it("fills the paper with dimmed photo tones and leaves the placement alone", async () => {
    const [plain, backed] = await Promise.all([
      rasterise({ kind: "solid", color_hex: "#ffffff" }),
      rasterise({ kind: "media_blur", media_id: HASH_B, blur_sigma_norm: 0.02, dim: 0.82 }),
    ]);

    const { width, channels } = backed.info;
    const at = (raw: { data: Buffer }, x: number, y: number): [number, number, number] => {
      const index = (y * width + x) * channels;
      return [raw.data[index]!, raw.data[index + 1]!, raw.data[index + 2]!];
    };

    // A paper corner: white on the solid page, photo tones when backed.
    const [pr] = at(plain, 8, 8);
    expect(pr).toBeGreaterThan(245);
    const [br] = at(backed, 8, 8);
    expect(br).toBeLessThan(240);

    // And BLURRED photo tones: along a paper row the checker's hard edges
    // must be gone. A sharp (or merely distorted) copy of the source jumps
    // ~100 levels between adjacent cells; a 0.02-of-page-edge gaussian leaves
    // no step above a few levels per pixel. This is the assertion that
    // catches sharp's single-resize-slot bug shipping a sharp backdrop.
    let maxStep = 0;
    for (let x = 4; x < width - 4; x += 1) {
      const [r1] = at(backed, x, 8);
      const [r2] = at(backed, x + 1, 8);
      maxStep = Math.max(maxStep, Math.abs(r1 - r2));
    }
    expect(maxStep).toBeLessThan(16);

    // The centre of the placement is the same photo either way.
    const pxPerMm = width / PAGE_MM;
    const cx = Math.round((FRAME.x_mm + FRAME.width_mm / 2) * pxPerMm);
    const cy = Math.round((FRAME.y_mm + FRAME.height_mm / 2) * pxPerMm);
    const inPlain = at(plain, cx, cy);
    const inBacked = at(backed, cx, cy);
    for (let channel = 0; channel < 3; channel += 1) {
      expect(Math.abs(inPlain[channel]! - inBacked[channel]!)).toBeLessThanOrEqual(2);
    }
  }, 60_000);

  it("refuses a backdrop whose media is not placed on the page", async () => {
    await expect(
      rasterise({
        kind: "media_blur",
        media_id: "9999999999999999999999999999999999999999999999999999999999999999",
      }),
    ).rejects.toThrow(/not placed on this page/);
  });
});
