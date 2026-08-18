/**
 * The cross-language half of issue #34.
 *
 * `contracts/tests/test_contracts.py` proves the encoding is self-consistent in
 * Python. That is not the property at risk. The property at risk is that a
 * TypeScript writer and a Python writer produce the SAME id for the same
 * detection -- and the only way to know is to compute it in both and compare
 * against one committed table.
 *
 * So this reads the same `contracts/vectors/face-id.json` and the same golden
 * fixtures the Python suite reads, and checks the pre-image bytes as well as
 * the digest. A digest mismatch says only that something diverged; a pre-image
 * mismatch names the field that did.
 */
import assert from "node:assert/strict";
import { existsSync, readFileSync, readdirSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { test } from "node:test";

import { bytesToHex } from "@noble/hashes/utils.js";

import type { FaceRecord } from "../codegen/generated/typescript/index.ts";
import {
  BBOX_QUANTUM,
  DOMAIN_TAG,
  UNIT_SEPARATOR,
  faceId,
  faceIdOfRecord,
  faceIdPreimage,
  quantiseBoxComponent,
} from "./face-id.ts";

/** Climb to the `contracts/` directory rather than counting `..` segments.
 *
 * This file runs from `dist/`, at a different depth from its source, so a
 * fixed relative path is right in the editor and wrong when it executes -- and
 * a test that cannot find its data is a test that does not run. Search for the
 * artifact itself and fail loudly when it is absent. */
function findContractsRoot(): string {
  let directory = path.dirname(fileURLToPath(import.meta.url));
  for (let depth = 0; depth < 12; depth += 1) {
    const candidate = path.join(directory, "contracts");
    if (existsSync(path.join(candidate, "vectors", "face-id.json"))) {
      return candidate;
    }
    const parent = path.dirname(directory);
    if (parent === directory) {
      break;
    }
    directory = parent;
  }
  throw new Error("could not locate contracts/vectors/face-id.json from this file");
}

const contractsRoot = findContractsRoot();

interface Vector {
  name: string;
  why: string;
  input: {
    media_id: string;
    frame_time: { value: number; rate: number } | null;
    bbox: { x: number; y: number; w: number; h: number };
    detector: { model_id: string; version: string };
  };
  preimage_utf8_hex: string;
  face_id: string;
}

interface VectorFile {
  domain_tag: string;
  bbox_quantum: number;
  bbox_rounding: string;
  separator: { codepoint: number };
  vectors: Vector[];
}

const vectorFile = JSON.parse(
  readFileSync(path.join(contractsRoot, "vectors", "face-id.json"), "utf8"),
) as VectorFile;

function faceFixtures(): Array<{ name: string; record: FaceRecord }> {
  const found: Array<{ name: string; record: FaceRecord }> = [];
  for (const bucket of ["valid", "schema-invalid"]) {
    const directory = path.join(contractsRoot, "fixtures", "face-record", bucket);
    for (const entry of readdirSync(directory).sort()) {
      if (!entry.endsWith(".json")) {
        continue;
      }
      found.push({
        name: `${bucket}/${entry}`,
        record: JSON.parse(readFileSync(path.join(directory, entry), "utf8")) as FaceRecord,
      });
    }
  }
  return found;
}

test("the constants this file uses are the constants the table declares", () => {
  assert.equal(vectorFile.domain_tag, DOMAIN_TAG);
  assert.equal(vectorFile.bbox_quantum, BBOX_QUANTUM);
  assert.equal(vectorFile.separator.codepoint, UNIT_SEPARATOR.codePointAt(0));
  assert.equal(vectorFile.bbox_rounding, "half away from zero");
});

test("there are vectors and fixtures to check", () => {
  assert.ok(vectorFile.vectors.length > 0, "no vectors; this test proves nothing");
  assert.ok(faceFixtures().length > 0, "no face fixtures; this test proves nothing");
});

test("every vector reproduces its pre-image bytes in TypeScript", () => {
  for (const vector of vectorFile.vectors) {
    const bytes = faceIdPreimage({
      media_id: vector.input.media_id,
      frame_time: vector.input.frame_time,
      bbox: vector.input.bbox,
      detector: vector.input.detector,
    });
    assert.equal(
      bytesToHex(bytes),
      vector.preimage_utf8_hex,
      `${vector.name}: pre-image diverged -> ${JSON.stringify(new TextDecoder().decode(bytes))}`,
    );
  }
});

test("every vector reproduces its face_id in TypeScript", () => {
  for (const vector of vectorFile.vectors) {
    assert.equal(faceId(vector.input), vector.face_id, vector.name);
  }
});

test("every golden FaceRecord's face_id recomputes in TypeScript", () => {
  for (const { name, record } of faceFixtures()) {
    assert.equal(
      faceIdOfRecord(record),
      record.face_id,
      `${name}: the committed face_id is not what this encoding produces`,
    );
  }
});

test("an integral frame time written as a float gives the same id", () => {
  // The named failure: Python writes `1.0` where JavaScript writes `1`.
  const common = {
    media_id: "a".repeat(64),
    bbox: { x: 0.25, y: 0.25, w: 0.5, h: 0.5 },
    detector: { model_id: "scrfd-10g-bnkps", version: "1.0.0" },
  };
  assert.equal(
    faceId({ ...common, frame_time: { value: 1001, rate: 30 } }),
    faceId({ ...common, frame_time: { value: 1001.0, rate: 30.0 } }),
  );
});

test("the box rounds half away from zero", () => {
  // 0.30025 * 10000 is exactly 3002.5 as a double. Python's round() gives 3002.
  assert.equal(quantiseBoxComponent(0.30025), 3003);
});

test("a still and a video face are both covered by the fixtures", () => {
  const times = faceFixtures().map(({ record }) => record.frame_time ?? null);
  assert.ok(times.some((time) => time === null), "no still-image face fixture");
  assert.ok(times.some((time) => time !== null), "no face fixture with a frame_time");
});
