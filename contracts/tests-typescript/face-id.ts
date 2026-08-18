/**
 * The canonical `face_id` encoding, implemented a second time in a second
 * language, against the generated TypeScript bindings.
 *
 * This file exists because of the failure mode issue #34 names: two languages
 * serialising the same logical value differently, silently, and only being
 * found once a library has two rows for one face. A single implementation
 * cannot demonstrate that a contract is language-independent -- it can only
 * demonstrate that it is self-consistent. So this is written from the schema
 * text, not translated from `services/pipeline/memory_engine_pipeline/ids.py`,
 * and both are checked against the same golden vectors.
 *
 * Types come from `contracts/codegen/generated/typescript`. Nothing here
 * hand-rolls a shape that the schema already defines.
 */
import { blake3 } from "@noble/hashes/blake3.js";
import { bytesToHex } from "@noble/hashes/utils.js";

import type {
  FaceRecord,
  ModelRef,
  NormalizedBox,
  RationalTime,
} from "../codegen/generated/typescript/index.ts";

/** U+001F INFORMATION SEPARATOR ONE, escaped rather than pasted as a raw
 * control byte -- an invisible character in source is a diff nobody reviews. */
export const UNIT_SEPARATOR = "\u001F";
/** Versions the ENCODING, not the detector. */
export const DOMAIN_TAG = "face:v1";
/** Normalised box components are rounded to this many parts of the frame. */
export const BBOX_QUANTUM = 10_000;

/**
 * RFC 8785 / ECMAScript `Number::toString`.
 *
 * In JavaScript this is simply `String(value)` -- which is the whole point:
 * the contract adopted JavaScript's rule so that the language with the least
 * freedom here is the reference, and Python has to come to it rather than the
 * other way round. Exponent forms are refused rather than written, because
 * that is where the two languages' padding stops agreeing.
 */
export function ecmascriptNumber(value: number): string {
  if (!Number.isFinite(value)) {
    throw new Error(`${value} is not finite and cannot enter an id`);
  }
  const rendered = String(value);
  if (rendered.includes("e") || rendered.includes("E")) {
    throw new Error(
      `${value} needs exponent notation, where Python and JavaScript formatting stop agreeing`,
    );
  }
  return rendered;
}

/**
 * `value * BBOX_QUANTUM`, rounded HALF AWAY FROM ZERO.
 *
 * `Math.round` would give the same answer for every value the schema permits,
 * since all four components are non-negative. It is spelled out anyway so that
 * this file and the Python one read as the same rule rather than as two
 * builtins that happen to agree -- Python's `round` does NOT agree, and that
 * is exactly the defect this encoding was frozen to close.
 */
export function quantiseBoxComponent(value: number): number {
  if (!Number.isFinite(value)) {
    throw new Error(`${value} is not finite and cannot enter an id`);
  }
  if (value < 0) {
    throw new Error(`${value} is negative; a normalised box component is not`);
  }
  const scaled = value * BBOX_QUANTUM;
  const floor = Math.floor(scaled);
  return scaled - floor >= 0.5 ? floor + 1 : floor;
}

function assertSeparatorFree(name: string, field: string): void {
  for (const character of field) {
    const code = character.codePointAt(0) ?? 0;
    if (code < 0x20 || code === 0x7f) {
      throw new Error(
        `detector ${name} carries a control character; the encoding joins fields with U+001F`,
      );
    }
  }
}

export interface FaceIdInput {
  media_id: string;
  frame_time: RationalTime | null;
  bbox: NormalizedBox;
  detector: Pick<ModelRef, "model_id" | "version">;
}

/** The exact UTF-8 bytes that get hashed. */
export function faceIdPreimage(input: FaceIdInput): Uint8Array {
  const time =
    input.frame_time === null
      ? ""
      : `${ecmascriptNumber(input.frame_time.value)}/${ecmascriptNumber(input.frame_time.rate)}`;

  const box = [input.bbox.x, input.bbox.y, input.bbox.w, input.bbox.h]
    .map((component) => String(quantiseBoxComponent(component)))
    .join(",");

  assertSeparatorFree("model_id", input.detector.model_id);
  assertSeparatorFree("version", input.detector.version);

  const joined = [
    DOMAIN_TAG,
    input.media_id,
    time,
    box,
    input.detector.model_id,
    input.detector.version,
  ].join(UNIT_SEPARATOR);

  return new TextEncoder().encode(joined);
}

export function faceId(input: FaceIdInput): string {
  return bytesToHex(blake3(faceIdPreimage(input)));
}

/** The same computation, read straight off a contract record. */
export function faceIdOfRecord(record: FaceRecord): string {
  return faceId({
    media_id: record.media_id,
    frame_time: record.frame_time ?? null,
    bbox: record.detection.bbox,
    detector: {
      model_id: record.detection.detector.model_id,
      version: record.detection.detector.version,
    },
  });
}
