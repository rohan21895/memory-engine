// Egress test for the ONE artefact this product ever uploads: the contact
// sheet that packages/prompt-engine composes for the frontier model.
//
// egress.test.mjs still holds the three TODOs for the process-level network
// sandbox, which cannot be written until a network-capable process exists. This
// file covers the payload half of the same rule NOW, because the payload exists
// today: "no network egress without a consent-ledger entry" is worth nothing if
// the thing being sent carries the filenames and GPS fixes the ledger entry does
// not mention.
//
// Deliberately independent of the module under test. The Python side only
// composes the sheet and declares what it planted in the inputs; every
// assertion about what came OUT is made here, with this file's own PNG chunk
// walker. If prompt-engine's own leak check were broken, this test would still
// fail. That is the whole reason it is worth having a second implementation.
//
// It fails rather than skips when Python or Pillow is unavailable. A skipped
// egress check must never share an outcome with a passing one.

import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

const here = path.dirname(fileURLToPath(import.meta.url));
const probe = path.join(here, "contact_sheet_probe.py");

// Chunk types that may leave the device. Everything else -- eXIf, tEXt, iTXt,
// zTXt, tIME -- is a carrier for exactly what this test exists to stop.
const ALLOWED_CHUNKS = new Set(["IHDR", "IDAT", "IEND"]);
const PNG_MAGIC = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);

function chunkTypes(png) {
  assert.ok(png.subarray(0, 8).equals(PNG_MAGIC), "emitted file is not a PNG");
  const types = [];
  let offset = 8;
  while (offset < png.length) {
    assert.ok(offset + 8 <= png.length, "PNG truncated inside a chunk header");
    const length = png.readUInt32BE(offset);
    types.push(png.subarray(offset + 4, offset + 8).toString("ascii"));
    offset += 12 + length;
  }
  assert.equal(offset, png.length, "PNG chunk lengths do not add up");
  return types;
}

function compose() {
  const out = mkdtempSync(path.join(tmpdir(), "contact-sheet-egress-"));
  const result = spawnSync("python3", [probe, out], { encoding: "utf8" });
  assert.equal(
    result.status,
    0,
    `contact-sheet egress probe did not run (status ${result.status}).\n` +
      `This is a FAILURE, not a skip: an egress check that did not execute must ` +
      `never read as one that passed.\n${result.stderr ?? ""}`,
  );
  return {
    dir: out,
    png: readFileSync(path.join(out, "sheet.png")),
    manifest: readFileSync(path.join(out, "manifest.json"), "utf8"),
    markers: JSON.parse(readFileSync(path.join(out, "markers.json"), "utf8")),
  };
}

test("contact sheet carries no metadata chunks off the device", () => {
  const { dir, png } = compose();
  try {
    const types = chunkTypes(png);
    const forbidden = types.filter((type) => !ALLOWED_CHUNKS.has(type));
    assert.deepEqual(forbidden, [], `sheet carries chunk(s): ${forbidden.join(", ")}`);
    assert.equal(types[0], "IHDR");
    assert.equal(types.at(-1), "IEND");
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("contact sheet carries no EXIF, GPS, ICC, filename or path from its proxies", () => {
  const { dir, png, markers } = compose();
  try {
    for (const [name, value] of Object.entries(markers)) {
      assert.ok(
        !png.includes(Buffer.from(value, "utf8")),
        `sheet leaks ${name}: ${value}`,
      );
    }
    // The GPS coordinates went in as binary rationals, so search for the packed
    // form as well as the strings above: 15 deg 29' as a TIFF RATIONAL is the
    // numerator 15 followed by the denominator 1, big-endian.
    const packedLatitude = Buffer.from([0, 0, 0, 15, 0, 0, 0, 1]);
    assert.ok(!png.includes(packedLatitude), "sheet leaks a packed GPS rational");
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("contact sheet manifest holds no paths or filenames", () => {
  const { dir, manifest, markers } = compose();
  try {
    const parsed = JSON.parse(manifest);
    // The schema constant is the only string allowed to contain a slash.
    const rest = manifest.split(parsed.schema).join("");
    for (const punctuation of ["/", "\\", ".jpg", ".JPG", ".png", "IMG_"]) {
      assert.ok(!rest.includes(punctuation), `manifest holds ${punctuation}`);
    }
    for (const [name, value] of Object.entries(markers)) {
      assert.ok(!manifest.includes(value), `manifest leaks ${name}`);
    }
    for (const cell of parsed.cells) {
      assert.match(cell.label, /^[A-H][1-8]$/);
      assert.match(cell.media_id, /^[0-9a-f]{64}$/);
    }
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});
