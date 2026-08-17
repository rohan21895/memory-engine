import { readFile } from "node:fs/promises";
import { resolve } from "node:path";

import type { AlbumSpec } from "../../../contracts/codegen/generated/typescript/index.js";

import { describe, expect, it } from "vitest";

import { RenderPrintError } from "../src/errors.js";
import { assertRenderGate } from "../src/gate.js";
import { loadAndCheckIccProfile } from "../src/icc.js";
import { makeAlbum } from "./helpers.js";

describe("the non-overridable print gate", () => {
  it("accepts the contract golden AlbumSpec as its validation oracle", async () => {
    const fixture = JSON.parse(
      await readFile(resolve("../../contracts/fixtures/album-spec/valid/album-thailand-validated.json"), "utf8"),
    ) as AlbumSpec;
    expect(() => assertRenderGate(fixture)).not.toThrow();
  });

  it.each(["fail", "not_run"] as const)("refuses a %s validation report", (status) => {
    const spec = makeAlbum();
    spec.validation.status = status;
    expect(() => assertRenderGate(spec)).toThrowError(RenderPrintError);
  });

  it("refuses a contradictory pass with a failed finding", () => {
    const spec = makeAlbum();
    spec.validation.checks.push({ check_id: "dpi_floor", severity: "error", passed: false });
    expect(() => assertRenderGate(spec)).toThrow(/failed finding/);
  });

  it("refuses missing hard-gate evidence, unsafe faces, licenses, and page increments", () => {
    const missing = makeAlbum();
    missing.validation.checks = missing.validation.checks.filter((check) => check.check_id !== "bleed_coverage");
    expect(() => assertRenderGate(missing)).toThrow(/bleed_coverage/);

    const unsafe = makeAlbum();
    unsafe.pages[0]!.placements[0]!.face_safety!.faces_in_trim_zone = 1;
    expect(() => assertRenderGate(unsafe)).toThrow(/unsafe face/);

    const unlicensed = makeAlbum();
    unlicensed.pages[0]!.placements[0]!.enhancement_ops = [
      { op_id: "blocked", kind: "denoise", order: 0, license_cleared: false },
    ];
    expect(() => assertRenderGate(unlicensed)).toThrow(/commercial clearance/);

    const wrongCount = makeAlbum();
    wrongCount.pages.pop();
    expect(() => assertRenderGate(wrongCount)).toThrow(/19 pages/);
  });

  it("loads and identifies Sharp's deterministic CMYK profile", async () => {
    const profile = await loadAndCheckIccProfile(
      makeAlbum().vendor_profile.color_profile,
      { name: "Sharp built-in CMYK", builtin: "cmyk" },
    );
    expect(profile.colorSpace).toBe("cmyk");
    expect(profile.components).toBe(4);
    expect(profile.bytes.toString("ascii", 36, 40)).toBe("acsp");
    expect(profile.digest).toMatch(/^[0-9a-f]{64}$/);
  });
});
