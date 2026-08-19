import type {
  AlbumSpec,
  ValidationCheckCheckId,
} from "../../../contracts/codegen/generated/typescript/index.js";

import { IMPLEMENTED_ENHANCEMENTS } from "./enhance.js";
import { RenderPrintError } from "./errors.js";

const REQUIRED_CHECKS = new Set<ValidationCheckCheckId>([
  "dpi_floor",
  "face_in_trim_zone",
  "bleed_coverage",
  "color_profile_match",
  "page_count_valid",
]);

function fail(detail: string): never {
  throw new RenderPrintError(
    "validation_failed",
    `Print validation gate refused the AlbumSpec: ${detail}`,
  );
}

function finitePositive(value: number, name: string): void {
  if (!Number.isFinite(value) || value <= 0) fail(`${name} must be a finite positive number.`);
}

export function assertRenderGate(spec: AlbumSpec): void {
  if (spec.validation.status !== "pass") fail(`validation status is ${spec.validation.status}.`);
  if ((spec.validation.error_count ?? 0) !== 0) fail("validation error_count is not zero.");

  // Only ERROR-severity findings block. `warning` is a real severity in the
  // contract -- "300.3 DPI clears the floor but is below the vendor's preferred
  // 350", "the vendor pins no icc_hash so the profile was matched by name" --
  // and `status: pass` is defined by the schema as `error_count == 0` plus a
  // passing finding for each hard gate, not as "no finding anywhere is false".
  //
  // Refusing every unpassed finding made this gate stricter than the contract
  // in a way that refused EVERY album the album engine can currently produce:
  // both shipped vendor profiles leave `icc_hash` null, so every report carries
  // a warning-severity color_profile_match finding with `passed: false`. This
  // is still a hard gate -- an error-severity failure is refused below and
  // error_count is checked above -- it just no longer treats an advisory as a
  // defect.
  const failed = spec.validation.checks.find(
    (check) => !check.passed && check.severity === "error",
  );
  if (failed) fail(`${failed.check_id} contains a failed error-severity finding.`);

  for (const checkId of REQUIRED_CHECKS) {
    if (!spec.validation.checks.some((check) => check.check_id === checkId && check.passed)) {
      fail(`required check ${checkId} has no passing finding.`);
    }
  }

  const profile = spec.vendor_profile;
  finitePositive(profile.trim_size_mm.width_mm, "trim width");
  finitePositive(profile.trim_size_mm.height_mm, "trim height");
  finitePositive(profile.dpi_floor, "DPI floor");
  if (!Number.isFinite(profile.bleed_mm) || profile.bleed_mm < 0) fail("bleed must be non-negative.");
  if (profile.pdf_standard !== "pdf_x_4") {
    fail(`PDF standard ${profile.pdf_standard ?? "pdf_x_4"} is not implemented by this worker.`);
  }

  const limits = profile.page_count;
  if (!limits) fail("vendor profile has no page-count rule.");
  const count = spec.pages.length;
  if (
    !Number.isInteger(limits.minimum) ||
    !Number.isInteger(limits.maximum) ||
    !Number.isInteger(limits.increment) ||
    limits.increment < 1 ||
    count < limits.minimum ||
    count > limits.maximum ||
    count % limits.increment !== 0
  ) {
    fail(`${count} pages do not satisfy ${limits.minimum}-${limits.maximum} in increments of ${limits.increment}.`);
  }

  spec.pages.forEach((page, expectedIndex) => {
    if (page.page_index !== expectedIndex) fail(`page ${expectedIndex} is stored as index ${page.page_index}.`);
    for (const placement of page.placements) {
      finitePositive(placement.frame.width_mm, `${placement.placement_id} frame width`);
      finitePositive(placement.frame.height_mm, `${placement.placement_id} frame height`);
      if (placement.effective_dpi < profile.dpi_floor) {
        fail(`${placement.placement_id} is below the ${profile.dpi_floor} DPI floor.`);
      }
      if ((placement.crop.rotation_deg ?? 0) !== 0) {
        fail(`${placement.placement_id} has a rotated crop whose sampling convention is not contract-pinned.`);
      }
      const safety = placement.face_safety;
      if (
        safety &&
        (!safety.all_faces_in_safe_zone ||
          (safety.faces_in_gutter ?? 0) > 0 ||
          (safety.faces_in_trim_zone ?? 0) > 0)
      ) {
        fail(`${placement.placement_id} contains an unsafe face placement.`);
      }
      const enhancements = placement.enhancement_ops ?? [];
      if (enhancements.some((operation) => !operation.license_cleared)) {
        fail(`${placement.placement_id} contains an enhancement without commercial clearance.`);
      }
      const unexecutable = enhancements.filter(
        (operation) => !IMPLEMENTED_ENHANCEMENTS.has(operation.kind),
      );
      if (unexecutable.length > 0) {
        fail(
          `${placement.placement_id} declares enhancement kind(s) ` +
            `${unexecutable.map((operation) => operation.kind).join(", ")} that this ` +
            "renderer cannot execute; rendering would silently use the original source.",
        );
      }
    }
  });
}

export const requiredRenderChecks = Object.freeze([...REQUIRED_CHECKS]);
