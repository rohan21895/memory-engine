import { readFile } from "node:fs/promises";
import { resolve } from "node:path";

import {
  EnhancementOpKindValues,
  type AlbumSpec,
} from "../../../contracts/codegen/generated/typescript/index.js";

import { describe, expect, it } from "vitest";

import { RenderPrintError } from "../src/errors.js";
import { assertRenderGate } from "../src/gate.js";
import { loadAndCheckIccProfile } from "../src/icc.js";
import { makeAlbum } from "./helpers.js";

describe("the non-overridable print gate", () => {
  it("refuses the golden report's denoise plan but accepts it once the plan is executable", async () => {
    const fixture = JSON.parse(
      await readFile(resolve("../../contracts/fixtures/album-spec/valid/album-thailand-validated.json"), "utf8"),
    ) as AlbumSpec;
    // The Thailand fixture carries a licensed `denoise` op, which this
    // renderer cannot execute -- rendering would silently ship the original.
    expect(() => assertRenderGate(fixture)).toThrow(/cannot execute/);
    for (const page of fixture.pages) {
      for (const placement of page.placements) placement.enhancement_ops = [];
    }
    expect(() => assertRenderGate(fixture)).not.toThrow();
  });

  it.each(["fail", "not_run"] as const)("refuses a %s validation report", (status) => {
    const spec = makeAlbum();
    spec.validation.status = status;
    expect(() => assertRenderGate(spec)).toThrowError(RenderPrintError);
  });

  it("refuses a contradictory pass with a failed error-severity finding", () => {
    const spec = makeAlbum();
    spec.validation.checks.push({ check_id: "dpi_floor", severity: "error", passed: false });
    expect(() => assertRenderGate(spec)).toThrow(/failed error-severity finding/);
  });

  it("accepts an advisory warning that did not pass", () => {
    // Every album the album engine currently produces carries one of these:
    // both shipped vendor profiles leave `icc_hash` null, so the colour check
    // records `passed: false` at warning severity with the remediation
    // "obtain the vendor's ICC profile". The contract defines `pass` as zero
    // ERRORS plus passing evidence for each hard gate, so refusing this would
    // refuse every book while claiming the spec was invalid.
    const spec = makeAlbum();
    spec.validation.checks.push({
      check_id: "color_profile_match",
      severity: "warning",
      passed: false,
      detail: "vendor pins no icc_hash; matched by name only",
    });
    spec.validation.warning_count = 1;
    expect(() => assertRenderGate(spec)).not.toThrow();
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

  const implementedKinds = ["exposure", "white_balance", "sharpen"] as const;
  const unimplementedKinds = EnhancementOpKindValues.filter(
    (kind) => !(implementedKinds as readonly string[]).includes(kind),
  );

  it.each(implementedKinds)("accepts a licensed %s plan the renderer can execute", (kind) => {
    const spec = makeAlbum();
    spec.pages[0]!.placements[0]!.enhancement_ops = [
      { op_id: `licensed-${kind}`, kind, order: 0, license_cleared: true },
    ];

    expect(() => assertRenderGate(spec)).not.toThrow();
  });

  it.each(unimplementedKinds)("refuses a licensed %s plan this renderer cannot execute", (kind) => {
    const spec = makeAlbum();
    spec.pages[0]!.placements[0]!.enhancement_ops = [
      { op_id: `licensed-${kind}`, kind, order: 0, license_cleared: true },
    ];

    expect(() => assertRenderGate(spec)).toThrow(/cannot execute/);
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
