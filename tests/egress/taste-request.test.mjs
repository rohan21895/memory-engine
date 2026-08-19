// Egress test for the BYTES, not the picture.
//
// contact-sheet.test.mjs inspects the composed sheet. This file inspects the
// request body that carries it -- the base64 image plus the task text, the
// system prompt and the output schema. Those three are text the product
// authors, and the obvious way to write the prompt ("choose from these
// candidates: <ids>") puts 64-hex content addresses next to a photograph of
// somebody's family while every assertion about the PNG still passes. A clean
// sheet is necessary and not sufficient.
//
// It also asserts the refusal. "No network egress without a consent-ledger
// entry" is a claim about a code path, so the probe runs that path with no
// consent and a sender that would record a call, and this file asserts the
// block code and the call count. A rule nothing executes is a sentence in a
// document.
//
// Deliberately independent of the module under test: this file re-walks the
// PNG chunks itself and does its own searching, so a bug in prompt-engine's
// own leak check cannot make the egress test pass.
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
const probe = path.join(here, "taste_request_probe.py");

const ALLOWED_CHUNKS = new Set(["IHDR", "IDAT", "IEND"]);
const PNG_MAGIC = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);

function chunkTypes(png) {
  assert.ok(png.subarray(0, 8).equals(PNG_MAGIC), "embedded payload is not a PNG");
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

function build() {
  const out = mkdtempSync(path.join(tmpdir(), "taste-egress-"));
  const result = spawnSync("python3", [probe, out], { encoding: "utf8" });
  assert.equal(
    result.status,
    0,
    `taste egress probe did not run (status ${result.status}).\n` +
      `This is a FAILURE, not a skip: an egress check that did not execute must ` +
      `never read as one that passed.\n${result.stderr ?? ""}`,
  );
  const raw = readFileSync(path.join(out, "request-body.json"), "utf8");
  const body = JSON.parse(raw);
  const blob = body.messages[0].content[0].source.data;
  return {
    dir: out,
    raw,
    body,
    blob,
    // Everything in the body EXCEPT the opaque image. Any substring can occur
    // in base64 by chance, so searching it for ".jpg" would be a coin flip;
    // searching what remains is a search of text we wrote.
    authored: raw.split(blob).join(""),
    embedded: Buffer.from(blob, "base64"),
    markers: JSON.parse(readFileSync(path.join(out, "markers.json"), "utf8")),
    local: JSON.parse(readFileSync(path.join(out, "local.json"), "utf8")),
  };
}

test("the image inside the request body carries no metadata chunks", () => {
  const probeRun = build();
  try {
    const types = chunkTypes(probeRun.embedded);
    const forbidden = types.filter((type) => !ALLOWED_CHUNKS.has(type));
    assert.deepEqual(forbidden, [], `body image carries chunk(s): ${forbidden.join(", ")}`);
    assert.equal(types[0], "IHDR");
    assert.equal(types.at(-1), "IEND");
    // Not an empty sheet: a leak test over nothing passes trivially.
    assert.ok(probeRun.embedded.length > 2000, "the embedded sheet is implausibly small");
  } finally {
    rmSync(probeRun.dir, { recursive: true, force: true });
  }
});

test("no EXIF, GPS, ICC, filename or path from the proxies reaches the request body", () => {
  const probeRun = build();
  try {
    for (const [name, value] of Object.entries(probeRun.markers)) {
      assert.ok(
        !probeRun.raw.includes(value),
        `request body leaks ${name}: ${value}`,
      );
      assert.ok(
        !probeRun.embedded.includes(Buffer.from(value, "utf8")),
        `embedded image leaks ${name}`,
      );
    }
    // The GPS went in as binary rationals: 15 deg as a TIFF RATIONAL is the
    // numerator 15 followed by the denominator 1, big-endian. A string search
    // alone would not find it.
    assert.ok(
      !probeRun.embedded.includes(Buffer.from([0, 0, 0, 15, 0, 0, 0, 1])),
      "embedded image leaks a packed GPS rational",
    );
  } finally {
    rmSync(probeRun.dir, { recursive: true, force: true });
  }
});

test("the request body names grid labels and never a media id", () => {
  const probeRun = build();
  try {
    for (const mediaId of probeRun.local.media_ids) {
      assert.ok(!probeRun.raw.includes(mediaId), `request body leaks media id ${mediaId}`);
    }
    // And the labels ARE there, so this is not passing on an empty body.
    assert.equal(probeRun.local.labels.length, probeRun.local.tile_count);
    for (const label of probeRun.local.labels) {
      assert.ok(probeRun.authored.includes(label), `label ${label} is not in the body`);
    }
  } finally {
    rmSync(probeRun.dir, { recursive: true, force: true });
  }
});

test("the authored text in the request body holds no path or filename", () => {
  const probeRun = build();
  try {
    for (const token of ["/Users/", ".jpg", ".JPG", ".png", ".heic", "IMG_", "DCIM", "\\\\"]) {
      assert.ok(
        !probeRun.authored.includes(token),
        `authored request text holds ${token}`,
      );
    }
  } finally {
    rmSync(probeRun.dir, { recursive: true, force: true });
  }
});

test("no bytes leave without a consent-ledger entry", () => {
  const probeRun = build();
  try {
    assert.equal(
      probeRun.local.blocks.consent_absent,
      "consent_missing",
      "a send with no consent record was not blocked",
    );
    assert.equal(
      probeRun.local.blocks.requires_egress_false,
      "egress_not_declared",
      "a job that declared no egress was allowed to use the network",
    );
    assert.equal(
      probeRun.local.blocks.clearance_absent,
      "safety_clearance",
      "valid consent with no safety clearance was allowed to send -- and via " +
        "the wrong exception type if the value names one",
    );
    // The sender is the witness: a ledger assertion alone would still pass if
    // the ledger were the broken thing.
    assert.equal(probeRun.local.sender_calls, 0, "the transport called the sender");
    assert.equal(
      probeRun.local.ledger_entries,
      0,
      "a send was journaled that never happened",
    );
  } finally {
    rmSync(probeRun.dir, { recursive: true, force: true });
  }
});
