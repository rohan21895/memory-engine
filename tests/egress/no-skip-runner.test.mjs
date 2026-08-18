import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const runner = path.resolve(here, "../../scripts/ci/run-no-skip-tests.mjs");

function fixture(t, source) {
  const directory = mkdtempSync(path.join(os.tmpdir(), "memory-engine-no-skip-"));
  const file = path.join(directory, "fixture.test.mjs");
  writeFileSync(file, `import test from "node:test";\n${source}\n`);
  t.after(() => rmSync(directory, { recursive: true, force: true }));
  return file;
}

function invoke(file) {
  return spawnSync(process.execPath, [runner, file], { encoding: "utf8" });
}

test("no-skip runner accepts an executed passing test", (t) => {
  const result = invoke(fixture(t, 'test("runs", () => {});'));
  assert.equal(result.status, 0, result.stdout + result.stderr);
});

test("no-skip runner rejects TODO as a false green", (t) => {
  const result = invoke(fixture(t, 'test.todo("absent");'));
  assert.equal(result.status, 1, result.stdout + result.stderr);
  assert.match(result.stderr, /1 TODO/);
});

test("no-skip runner rejects skipped tests as a false green", (t) => {
  const result = invoke(fixture(t, 'test.skip("did not run", () => {});'));
  assert.equal(result.status, 1, result.stdout + result.stderr);
  assert.match(result.stderr, /1 skipped/);
});

test("no-skip runner preserves a real test failure", (t) => {
  const result = invoke(fixture(t, 'test("fails", () => { throw new Error("boom"); });'));
  assert.equal(result.status, 1, result.stdout + result.stderr);
  assert.doesNotMatch(result.stderr, /false green/);
});
