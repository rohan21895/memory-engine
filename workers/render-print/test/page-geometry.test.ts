// The page raster must actually contain the AlbumSpec's placement, in the
// AlbumSpec's frame, in the AlbumSpec's colour.
//
// Everything else in this suite asserted PDF structure -- page count, boxes,
// the output intent, determinism -- and the services/pipeline end-to-end test
// asserted `%PDF` plus "bigger than 100kB". None of that looks at a pixel, so a
// renderer that sheared every page and washed every photo out to near-white
// passed all of it. It had: `renderPage` round-tripped through `.raw()` and
// re-wrapped a five-band CMYK+alpha buffer as four channels, which shifted
// every row and smeared one placement across the whole page about five times,
// and then converted the ink values a second time as if they were RGBA, which
// read the K band as alpha.
//
// So these tests measure the raster, not the container. They are differential:
// render the page with the placement and again without it, and the pixels that
// changed ARE the placement. That needs no knowledge of how compositing works
// and it cannot pass vacuously -- a renderer that draws nothing fails the area
// assertion just as loudly as one that draws everywhere.

import sharp from "sharp";
import { describe, expect, it } from "vitest";

import type { Page } from "../../../contracts/codegen/generated/typescript/index.js";

import { loadAndCheckIccProfile } from "../src/icc.js";
import { renderPage } from "../src/page.js";
import { findTestFont, HASH_B, sourceJpeg } from "./helpers.js";

const PAGE_MM = 60;
const DPI = 300;
const MM_PER_INCH = 25.4;

// A frame deliberately off-centre and not square, so a renderer that centres,
// stretches to fit or transposes the axes cannot accidentally agree with it.
const FRAME = { x_mm: 12, y_mm: 21, width_mm: 24, height_mm: 16, rotation_deg: 0 };

function pageWith(placements: Page["placements"]): Page {
  return {
    page_index: 0,
    side: "right",
    background: { kind: "solid", color_hex: "#ffffff" },
    placements,
    text_blocks: [],
  };
}

const PLACEMENT = {
  placement_id: "geometry-probe",
  media_id: HASH_B,
  frame: FRAME,
  // 1200x800 source into a 24x16mm frame: the aspect ratios match exactly, so
  // renderPage's own aspect guard is not what is under test here.
  crop: { x: 0, y: 0, w: 1, h: 1, rotation_deg: 0 },
  effective_dpi: (1200 * MM_PER_INCH) / FRAME.width_mm,
  z_index: 0,
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
};

async function rasterise(placements: Page["placements"]) {
  const icc = await loadAndCheckIccProfile(
    { icc_name: "Sharp built-in CMYK", intent: "relative_colorimetric" },
    { name: "Sharp built-in CMYK", builtin: "cmyk" },
  );
  const source = await sourceJpeg();
  const font = await findTestFont();
  const rendered = await renderPage({
    page: pageWith(placements),
    widthMm: PAGE_MM,
    heightMm: PAGE_MM,
    dpi: DPI,
    dpiFloor: 300,
    icc,
    resolvePlacementAsset: async () => source,
    resolveFont: async () => font,
  });
  const raw = await sharp(rendered.jpeg).raw().toBuffer({ resolveWithObject: true });
  return { rendered, raw };
}

describe("the page raster carries the placement the AlbumSpec declared", () => {
  it("puts the photo in its frame, and nowhere else", async () => {
    const [placed, empty] = await Promise.all([rasterise([PLACEMENT]), rasterise([])]);

    const { width, height, channels } = placed.raw.info;
    expect(width).toBe(Math.round((PAGE_MM * DPI) / MM_PER_INCH));
    expect(height).toBe(width);

    let minX = width;
    let maxX = -1;
    let minY = height;
    let maxY = -1;
    let changed = 0;
    for (let y = 0; y < height; y += 1) {
      for (let x = 0; x < width; x += 1) {
        const index = (y * width + x) * channels;
        let delta = 0;
        for (let c = 0; c < channels; c += 1) {
          delta = Math.max(delta, Math.abs(placed.raw.data[index + c]! - empty.raw.data[index + c]!));
        }
        if (delta > 8) {
          changed += 1;
          if (x < minX) minX = x;
          if (x > maxX) maxX = x;
          if (y < minY) minY = y;
          if (y > maxY) maxY = y;
        }
      }
    }

    const pxPerMm = width / PAGE_MM;
    // One pixel of tolerance on each edge: the frame is converted to whole
    // pixels, and asserting an exact edge would pin a rounding rule rather than
    // the behaviour. Five millimetres of tolerance would not catch the bug this
    // test exists for; one pixel does, because the bug moved content by
    // hundreds.
    expect(minX / pxPerMm).toBeCloseTo(FRAME.x_mm, 1);
    expect(minY / pxPerMm).toBeCloseTo(FRAME.y_mm, 1);
    expect((maxX + 1) / pxPerMm).toBeCloseTo(FRAME.x_mm + FRAME.width_mm, 1);
    expect((maxY + 1) / pxPerMm).toBeCloseTo(FRAME.y_mm + FRAME.height_mm, 1);

    // And the area, so that a renderer which draws the correct bounding box as
    // an outline, or leaves the interior transparent, still fails.
    const expectedPixels =
      Math.round((FRAME.width_mm * DPI) / MM_PER_INCH) * Math.round((FRAME.height_mm * DPI) / MM_PER_INCH);
    expect(changed / expectedPixels).toBeGreaterThan(0.99);
    expect(changed / expectedPixels).toBeLessThan(1.01);
  }, 60_000);

  it("keeps the placement's colour recognisable through the CMYK conversion", async () => {
    const { rendered } = await rasterise([PLACEMENT]);
    const metadata = await sharp(rendered.jpeg).metadata();
    expect(metadata.space).toBe("cmyk");
    expect(metadata.channels).toBe(4);

    // sharp decodes a CMYK JPEG back to RGB, which is what a print shop's
    // preflight preview does too. The source is #c86432; the round trip through
    // FOGRA-class CMYK moves it, but not into another hue and not to paper
    // white -- which is exactly what the double conversion produced when the K
    // band was read as a 12/255 alpha.
    const rgb = await sharp(rendered.jpeg).raw().toBuffer({ resolveWithObject: true });
    const pxPerMm = rgb.info.width / PAGE_MM;
    const cx = Math.round((FRAME.x_mm + FRAME.width_mm / 2) * pxPerMm);
    const cy = Math.round((FRAME.y_mm + FRAME.height_mm / 2) * pxPerMm);
    const at = (x: number, y: number): [number, number, number] => {
      const i = (y * rgb.info.width + x) * rgb.info.channels;
      return [rgb.data[i]!, rgb.data[i + 1]!, rgb.data[i + 2]!];
    };
    const [r, g, b] = at(cx, cy);
    const paper = at(2, 2);

    expect(paper[0]).toBeGreaterThan(245);
    expect(paper[1]).toBeGreaterThan(245);
    expect(paper[2]).toBeGreaterThan(245);

    // Warm orange: red clearly dominant, blue clearly lowest, and nowhere near
    // paper white.
    expect(r).toBeGreaterThan(g + 30);
    expect(g).toBeGreaterThan(b + 10);
    expect(r).toBeLessThan(240);
  }, 60_000);
});
