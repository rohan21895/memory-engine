import { access } from "node:fs/promises";

import sharp from "sharp";

import type {
  AlbumSpec,
  JobSpec,
  Page,
  ValidationCheckCheckId,
} from "../../../contracts/codegen/generated/typescript/index.js";

import { canonicalJson, digestBytes } from "../src/digest.js";

export const HASH_A = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
export const HASH_B = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";

const checks: ValidationCheckCheckId[] = [
  "dpi_floor",
  "face_in_trim_zone",
  "bleed_coverage",
  "color_profile_match",
  "page_count_valid",
];

export function makeAlbum(overrides: Partial<AlbumSpec> = {}): AlbumSpec {
  const pages: Page[] = Array.from({ length: 20 }, (_, page_index) => ({
    page_index,
    side: page_index === 0 ? "front_cover" : page_index === 19 ? "back_cover" : page_index % 2 ? "left" : "right",
    background: { kind: "solid", color_hex: page_index % 2 ? "#f2e9dd" : "#ffffff" },
    placements: [],
    text_blocks: [],
  }));
  pages[0]!.placements.push({
    placement_id: "hero",
    media_id: HASH_B,
    frame: { x_mm: 2, y_mm: 3, width_mm: 8, height_mm: 5.3333333333, rotation_deg: 0 },
    crop: { x: 0, y: 0, w: 1, h: 1, rotation_deg: 0 },
    effective_dpi: 3_000,
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
  });
  pages[1]!.text_blocks = [
    {
      block_id: "title",
      text: "Deterministic Album",
      frame: { x_mm: 1, y_mm: 1, width_mm: 10, height_mm: 4, rotation_deg: 0 },
      role: "title",
      font_family: "Test Font",
      font_size_pt: 6,
      color_hex: "#112233",
      alignment: "center",
    },
  ];
  return {
    schema_version: "v0",
    album_id: HASH_A,
    title: "Print test",
    vendor_profile: {
      vendor_id: "test-vendor",
      product_id: "tiny-book",
      profile_version: "1",
      trim_size_mm: { width_mm: 10, height_mm: 10 },
      bleed_mm: 1,
      safe_margin_mm: 1,
      gutter_mm: 0,
      dpi_floor: 300,
      dpi_preferred: 300,
      color_profile: { icc_name: "Sharp built-in CMYK", intent: "relative_colorimetric" },
      page_count: { minimum: 20, maximum: 20, increment: 2 },
      binding: "layflat",
      pdf_standard: "pdf_x_4",
    },
    pages,
    determinism: { planner: "test", planner_version: "1", seed: 7, inputs_digest: HASH_B },
    validation: {
      status: "pass",
      checks: checks.map((check_id) => ({ check_id, severity: "error", passed: true })),
      error_count: 0,
      warning_count: 0,
    },
    ...overrides,
  };
}

export async function sourceJpeg(): Promise<Buffer> {
  return sharp({ create: { width: 1200, height: 800, channels: 3, background: "#c86432" } })
    .jpeg({ quality: 95 })
    .toBuffer();
}

export async function findTestFont(): Promise<string> {
  const candidates = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/System/Library/Fonts/SFNS.ttf",
    "C:\\Windows\\Fonts\\arial.ttf",
  ];
  for (const path of candidates) {
    try {
      await access(path);
      return path;
    } catch {
      // Try the next deterministic system-font candidate.
    }
  }
  throw new Error("No test font was available on this runner.");
}

export function makeJob(params: Record<string, unknown>): JobSpec {
  return {
    schema_version: "v0",
    job_id: HASH_B,
    job_type: "render_print",
    inputs: { album_id: HASH_A },
    params,
    params_digest: digestBytes(new TextEncoder().encode(canonicalJson(params))),
    scope: "test",
    requirements: { compute: "cpu", requires_source_file: true },
    egress: { requires_egress: false },
    state: { status: "pending", attempts: 0 },
    checkpoint: {
      resumable: true,
      cursor: null,
      checkpoint_version: 1,
      completed_input_ids: [],
      partial_output_ids: [],
    },
    outputs: [],
  };
}
