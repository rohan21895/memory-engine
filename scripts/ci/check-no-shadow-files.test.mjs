import assert from "node:assert/strict";
import {
  mkdirSync,
  mkdtempSync,
  rmSync,
  symlinkSync,
  writeFileSync,
} from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import { findShadowFiles } from "./check-no-shadow-files.mjs";

function fixture(t) {
  const parent = mkdtempSync(path.join(os.tmpdir(), "memory-engine-shadow-"));
  const repository = path.join(parent, "repository");
  const external = path.join(parent, "external");
  mkdirSync(repository);
  mkdirSync(external);
  t.after(() => rmSync(parent, { recursive: true, force: true }));
  return { external, repository };
}

test("does not follow a directory symlink outside the repository", (t) => {
  const { external, repository } = fixture(t);
  writeFileSync(path.join(external, "private-name 2.txt"), "outside");
  symlinkSync(external, path.join(repository, "external-link"), "dir");

  assert.deepEqual(findShadowFiles(repository), []);
});

test("does not follow a symlink loop", (t) => {
  const { repository } = fixture(t);
  const nested = path.join(repository, "nested");
  mkdirSync(nested);
  symlinkSync(repository, path.join(nested, "loop"), "dir");

  assert.deepEqual(findShadowFiles(repository), []);
});

test("checks the symlink name without reading its target", (t) => {
  const { external, repository } = fixture(t);
  writeFileSync(path.join(external, "ordinary.txt"), "outside");
  symlinkSync(external, path.join(repository, "foreign-link 2"), "dir");

  assert.deepEqual(findShadowFiles(repository), ["foreign-link 2"]);
});
