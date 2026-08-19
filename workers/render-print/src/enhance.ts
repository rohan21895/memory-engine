/**
 * Execution of the classical develop ops a Placement carries.
 *
 * The plan decides, this file obeys: every number here comes from
 * `Placement.enhancement_ops`, computed once by the album planner and covered
 * by the album's identity digest. Nothing is inferred from the pixels at
 * render time, because two renders of the same AlbumSpec must be identical.
 *
 * WHY EXPOSURE AND WHITE BALANCE FOLD INTO ONE OPERATION
 *
 * sharp holds ONE `linear` slot per pipeline -- a second call replaces the
 * first rather than chaining. Exposure (levels stretch + brightness) and
 * white balance (per-channel gains) are both linear maps, so their
 * composition is computed here exactly: out_c = in_c * a_c + b_c with
 * a_c = gain_c * brightness / (white_point - black_point). Multiplication of
 * diagonal linear maps commutes, so the contract's `order` field cannot
 * change the result for these two -- `sharpen` is the only non-linear op and
 * always runs last, which its planned order also says.
 *
 * VALIDATION IS REFUSAL, NOT CLAMPING
 *
 * A parameter outside its range means the planner and this renderer disagree
 * about the contract, and silently clamping would print a book that matches
 * neither's intent. Everything out of range throws `validation_failed`
 * naming the placement and op.
 */

import type { Sharp } from "sharp";

import type { EnhancementOp, Placement } from "../../../contracts/codegen/generated/typescript/index.js";

import { RenderPrintError } from "./errors.js";

/** The kinds this renderer can execute. gate.ts refuses everything else. */
export const IMPLEMENTED_ENHANCEMENTS: ReadonlySet<string> = new Set([
  "exposure",
  "white_balance",
  "sharpen",
]);

interface Range {
  readonly min: number;
  readonly max: number;
}

const PARAMETER_RANGES: Record<string, Record<string, Range>> = {
  exposure: {
    black_point: { min: 0, max: 0.2 },
    white_point: { min: 0.8, max: 1 },
    brightness: { min: 0.7, max: 1.4 },
  },
  white_balance: {
    gain_r: { min: 0.7, max: 1.4 },
    gain_g: { min: 0.7, max: 1.4 },
    gain_b: { min: 0.7, max: 1.4 },
  },
  sharpen: {
    sigma: { min: 0.3, max: 3 },
    flat: { min: 0, max: 2 },
    jagged: { min: 0, max: 3 },
  },
};

function parameter(op: EnhancementOp, name: string, subject: string): number {
  const ranges = PARAMETER_RANGES[op.kind] ?? {};
  const range = ranges[name];
  const raw = (op.parameters ?? {})[name];
  if (typeof raw !== "number" || !Number.isFinite(raw)) {
    throw new RenderPrintError(
      "validation_failed",
      `${subject} op ${op.op_id}: parameter ${name} is not a finite number.`,
    );
  }
  if (range === undefined || raw < range.min || raw > range.max) {
    throw new RenderPrintError(
      "validation_failed",
      `${subject} op ${op.op_id}: ${name}=${raw} is outside [${range?.min}, ${range?.max}]; ` +
        "the planner and this renderer disagree and clamping would print neither's intent.",
    );
  }
  return raw;
}

/**
 * Apply the placement's develop plan to a pipeline positioned right after the
 * resize to print resolution (the correct point for output sharpening, and as
 * good as any for linear colour ops). The pipeline must be alpha-free: sharp's
 * per-channel `linear` wants exactly the colour channels.
 */
export function applyEnhancements(pipeline: Sharp, placement: Placement): Sharp {
  const ops = [...(placement.enhancement_ops ?? [])].sort((a, b) => a.order - b.order);
  if (ops.length === 0) return pipeline;
  const subject = placement.placement_id;

  let scale = 1;
  let offset = 0; // in 0-255 pixel units
  const gains: [number, number, number] = [1, 1, 1];
  let sharpen: { sigma: number; flat: number; jagged: number } | null = null;

  for (const op of ops) {
    if (!IMPLEMENTED_ENHANCEMENTS.has(op.kind)) {
      // gate.ts refuses these before any pixel is read; reaching here means
      // the gate was bypassed, which is worth a loud stop of its own.
      throw new RenderPrintError(
        "validation_failed",
        `${subject} op ${op.op_id}: kind ${op.kind} is not executable by this renderer.`,
      );
    }
    if (op.license_cleared !== true) {
      throw new RenderPrintError(
        "validation_failed",
        `${subject} op ${op.op_id} is not license-cleared for commercial output.`,
      );
    }
    if (op.kind === "exposure") {
      const blackPoint = parameter(op, "black_point", subject);
      const whitePoint = parameter(op, "white_point", subject);
      const brightness = parameter(op, "brightness", subject);
      // The ranges guarantee white_point - black_point >= 0.6, so the stretch
      // is bounded without a separate check.
      const stretch = brightness / (whitePoint - blackPoint);
      scale *= stretch;
      offset = offset * stretch - blackPoint * 255 * stretch;
    } else if (op.kind === "white_balance") {
      gains[0] *= parameter(op, "gain_r", subject);
      gains[1] *= parameter(op, "gain_g", subject);
      gains[2] *= parameter(op, "gain_b", subject);
    } else {
      sharpen = {
        sigma: parameter(op, "sigma", subject),
        flat: parameter(op, "flat", subject),
        jagged: parameter(op, "jagged", subject),
      };
    }
  }

  const wantsLinear =
    scale !== 1 || offset !== 0 || gains.some((gain) => gain !== 1);
  let result = pipeline;
  if (wantsLinear) {
    result = result.linear(
      gains.map((gain) => gain * scale),
      gains.map((gain) => offset * gain),
    );
  }
  if (sharpen !== null) {
    result = result.sharpen({ sigma: sharpen.sigma, m1: sharpen.flat, m2: sharpen.jagged });
  }
  return result;
}
