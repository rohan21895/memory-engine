// The develop plan a Placement carries must actually reach the pixels.
//
// Two layers, on purpose. The unit tests drive `applyEnhancements` with flat
// synthetic buffers where the correct answer is arithmetic -- a brightness of
// 1.25 on mid-grey IS 1.25 x mid-grey, a red gain IS a redder pixel -- so a
// sign error, a swapped channel, or sharp's replace-not-chain `linear`
// semantics fail loudly. The page test is differential, the same pattern as
// page-geometry.test.ts: render the page with the develop plan and again
// without it, and the pixels that changed are the plan. That is what proves
// page.ts actually wires the ops in; an enhance.ts that works but is never
// called passes every unit test and fails that one.

import sharp, { type Sharp } from "sharp";
import { describe, expect, it } from "vitest";

import type {
  EnhancementOp,
  Page,
  Placement,
} from "../../../contracts/codegen/generated/typescript/index.js";

import { applyEnhancements } from "../src/enhance.js";
import { RenderPrintError } from "../src/errors.js";
import { loadAndCheckIccProfile } from "../src/icc.js";
import { renderPage } from "../src/page.js";
import { findTestFont, HASH_B } from "./helpers.js";

function placementWith(ops: EnhancementOp[]): Placement {
  return {
    placement_id: "develop-probe",
    media_id: HASH_B,
    frame: { x_mm: 12, y_mm: 21, width_mm: 24, height_mm: 16, rotation_deg: 0 },
    crop: { x: 0, y: 0, w: 1, h: 1, rotation_deg: 0 },
    effective_dpi: 1_270,
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
    enhancement_ops: ops,
  };
}

function op(kind: EnhancementOp["kind"], parameters: Record<string, number>, order = 0): EnhancementOp {
  return { op_id: `${kind}-test`, kind, order, license_cleared: true, parameters };
}

async function flat(r: number, g: number, b: number): Promise<Buffer> {
  return sharp({ create: { width: 8, height: 8, channels: 3, background: { r, g, b } } })
    .png()
    .toBuffer();
}

async function meanChannels(pipeline: Sharp): Promise<[number, number, number]> {
  const { data, info } = await pipeline.raw().toBuffer({ resolveWithObject: true });
  const sums: [number, number, number] = [0, 0, 0];
  for (let index = 0; index < data.length; index += info.channels) {
    sums[0] += data[index]!;
    sums[1] += data[index + 1]!;
    sums[2] += data[index + 2]!;
  }
  const pixels = info.width * info.height;
  return [sums[0]! / pixels, sums[1]! / pixels, sums[2]! / pixels];
}

describe("applyEnhancements executes the plan's arithmetic", () => {
  it("brightens by exactly the exposure op's brightness on mid-grey", async () => {
    const source = await flat(120, 120, 120);
    const placement = placementWith([
      op("exposure", { black_point: 0, white_point: 1, brightness: 1.25 }),
    ]);
    const [r, g, b] = await meanChannels(applyEnhancements(sharp(source), placement));
    expect(r).toBeCloseTo(150, 0);
    expect(g).toBeCloseTo(150, 0);
    expect(b).toBeCloseTo(150, 0);
  });

  it("maps the black point to zero", async () => {
    // Input at exactly the black point (0.1 * 255 ~= 26) must land at 0.
    const source = await flat(26, 26, 26);
    const placement = placementWith([
      op("exposure", { black_point: 0.1, white_point: 1, brightness: 1 }),
    ]);
    const [r] = await meanChannels(applyEnhancements(sharp(source), placement));
    expect(r).toBeLessThanOrEqual(1);
  });

  it("applies white-balance gains per channel", async () => {
    const source = await flat(100, 100, 100);
    const placement = placementWith([
      op("white_balance", { gain_r: 1.1, gain_g: 1.0, gain_b: 0.9 }),
    ]);
    const [r, g, b] = await meanChannels(applyEnhancements(sharp(source), placement));
    expect(r).toBeCloseTo(110, 0);
    expect(g).toBeCloseTo(100, 0);
    expect(b).toBeCloseTo(90, 0);
  });

  it("composes exposure and white balance in one linear map, regardless of order", async () => {
    const source = await flat(100, 100, 100);
    const ordered = placementWith([
      op("exposure", { black_point: 0, white_point: 1, brightness: 1.2 }, 0),
      op("white_balance", { gain_r: 1.1, gain_g: 1.0, gain_b: 1.0 }, 1),
    ]);
    const reversed = placementWith([
      op("white_balance", { gain_r: 1.1, gain_g: 1.0, gain_b: 1.0 }, 0),
      op("exposure", { black_point: 0, white_point: 1, brightness: 1.2 }, 1),
    ]);
    const a = await meanChannels(applyEnhancements(sharp(source), ordered));
    const b = await meanChannels(applyEnhancements(sharp(source), reversed));
    expect(a[0]).toBeCloseTo(100 * 1.2 * 1.1, 0);
    expect(a).toEqual(b);
  });

  it("leaves a flat field untouched by sharpen but changes an edge", async () => {
    const placement = placementWith([op("sharpen", { sigma: 1.0, flat: 0.6, jagged: 1.4 })]);

    const flatSource = await flat(128, 128, 128);
    const [flatMean] = await meanChannels(applyEnhancements(sharp(flatSource), placement));
    expect(flatMean).toBeCloseTo(128, 0);

    // A hard vertical edge: sharpening must overshoot on both sides of it.
    const left = await sharp({ create: { width: 4, height: 8, channels: 3, background: { r: 40, g: 40, b: 40 } } })
      .raw()
      .toBuffer();
    const edge = await sharp(left, { raw: { width: 4, height: 8, channels: 3 } })
      .extend({ right: 4, background: { r: 220, g: 220, b: 220 } })
      .png()
      .toBuffer();
    const before = await sharp(edge).raw().toBuffer();
    const after = await applyEnhancements(sharp(edge), placement).raw().toBuffer();
    expect(Buffer.compare(before, after)).not.toBe(0);
  });

  // Validation must throw before a pixel is read, so a never-decoded pipeline
  // is exactly the right input: if refusal ever waits for pixels, sharp's own
  // lazy decode error surfaces instead of RenderPrintError and these fail.
  const lazyPipeline = () =>
    sharp({ create: { width: 1, height: 1, channels: 3, background: "#000000" } });

  it("refuses an out-of-range parameter instead of clamping", () => {
    const placement = placementWith([
      op("exposure", { black_point: 0, white_point: 1, brightness: 2.5 }),
    ]);
    expect(() => applyEnhancements(lazyPipeline(), placement)).toThrowError(RenderPrintError);
    expect(() => applyEnhancements(lazyPipeline(), placement)).toThrow(/brightness=2.5/);
  });

  it("refuses a missing or non-finite parameter", () => {
    const missing = placementWith([op("sharpen", { sigma: 1.0, flat: 0.6 })]);
    expect(() => applyEnhancements(lazyPipeline(), missing)).toThrow(/jagged/);
    const nan = placementWith([
      op("white_balance", { gain_r: Number.NaN, gain_g: 1, gain_b: 1 }),
    ]);
    expect(() => applyEnhancements(lazyPipeline(), nan)).toThrow(/not a finite number/);
  });

  it("refuses an op without license clearance even past the gate", () => {
    const placement = placementWith([
      { op_id: "no-license", kind: "sharpen", order: 0, license_cleared: false, parameters: { sigma: 1, flat: 0.6, jagged: 1.4 } },
    ]);
    expect(() => applyEnhancements(lazyPipeline(), placement)).toThrow(/license-cleared/);
  });

  it("refuses an unimplemented kind as a bypassed gate", () => {
    const placement = placementWith([
      { op_id: "no-exec", kind: "denoise", order: 0, license_cleared: true },
    ]);
    expect(() => applyEnhancements(lazyPipeline(), placement)).toThrow(/not executable/);
  });
});

describe("renderPage carries the develop plan into the raster", () => {
  const PAGE_MM = 60;
  const DPI = 300;

  async function rasterise(ops: EnhancementOp[]) {
    const icc = await loadAndCheckIccProfile(
      { icc_name: "Sharp built-in CMYK", intent: "relative_colorimetric" },
      { name: "Sharp built-in CMYK", builtin: "cmyk" },
    );
    // A deliberately dark source, so "the exposure op ran" is visible as a
    // higher mean, not just "some pixels changed".
    const source = await sharp({ create: { width: 1200, height: 800, channels: 3, background: { r: 60, g: 45, b: 35 } } })
      .jpeg({ quality: 95 })
      .toBuffer();
    const page: Page = {
      page_index: 0,
      side: "right",
      background: { kind: "solid", color_hex: "#ffffff" },
      placements: [placementWith(ops)],
      text_blocks: [],
    };
    const rendered = await renderPage({
      page,
      widthMm: PAGE_MM,
      heightMm: PAGE_MM,
      dpi: DPI,
      dpiFloor: 300,
      icc,
      resolvePlacementAsset: async () => source,
      resolveFont: findTestFont,
    });
    return sharp(rendered.jpeg).raw().toBuffer({ resolveWithObject: true });
  }

  it("renders a planned exposure lift measurably brighter than no plan", async () => {
    const [developed, straight] = await Promise.all([
      rasterise([
        op("exposure", { black_point: 0.02, white_point: 1, brightness: 1.3 }, 0),
        op("sharpen", { sigma: 1.0, flat: 0.6, jagged: 1.4 }, 1),
      ]),
      rasterise([]),
    ]);

    const { width, channels } = developed.info;
    const pxPerMm = width / PAGE_MM;
    // Sample the centre of the frame (12,21)+(24x16)mm in decoded RGB.
    const cx = Math.round((12 + 12) * pxPerMm);
    const cy = Math.round((21 + 8) * pxPerMm);
    const index = (cy * width + cx) * channels;
    const luma = (raw: { data: Buffer }) =>
      0.299 * raw.data[index]! + 0.587 * raw.data[index + 1]! + 0.114 * raw.data[index + 2]!;

    // brightness 1.3 with black_point 0.02 lifts this (60,45,35) frame by
    // ~16% in linear RGB; the CMYK round trip compresses that to ~12%
    // measured, so assert a solid absolute lift rather than the full ratio --
    // an unwired plan renders byte-identical (delta 0) and still fails this.
    expect(luma(developed)).toBeGreaterThan(luma(straight) + 4);
    const paperIndex = (2 * width + 2) * channels;
    expect(Math.abs(developed.data[paperIndex]! - straight.data[paperIndex]!)).toBeLessThanOrEqual(2);
  }, 60_000);
});
