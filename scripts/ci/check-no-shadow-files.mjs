// Refuse files whose names end in " 2", " 3", and so on.
//
// Something on at least one development machine periodically drops
// byte-identical copies of files beside the originals, named the way macOS
// resolves a name collision: `moments 2.py`, `gopro 3.rs`,
// `0002_proxy_index 2.sql`. Eighty-four of them appeared in this repository in
// a single day.
//
// They are not harmless clutter. Anything that discovers work by globbing a
// directory picks them up:
//
//   * media-db's migration runner loaded `0002_proxy_index 2.sql` as a second
//     version 2, and the resulting contiguity error read as a broken migration
//     set rather than as a stray file. That is what prompted this check.
//   * unittest discovery, schema-fixture loaders, model-config loaders and the
//     codegen manifest all walk directories the same way.
//
// The failure mode is the one this project keeps meeting: a duplicate is a
// perfect copy, so whatever loads it produces plausible output right up until
// the copy goes stale and two versions of the same logic are live at once.
//
// This is a check rather than a .gitignore rule on purpose. Ignoring them would
// hide them from `git status` while leaving them on disk for every runtime
// loader to find, which is strictly worse than seeing them.

import { readdirSync } from "node:fs";
import path from "node:path";
import process from "node:process";
import { pathToFileURL } from "node:url";
import { repositoryRoot } from "./lib.mjs";

const SKIP = new Set([".git", "node_modules", "target", "dist", "__pycache__", ".venv"]);

// A shadow name is the original stem, a single space, one or more digits, then
// the original extension (or nothing, for extensionless files).
const SHADOW = /^.+ \d+(\.[^.]+)?$/;

function walk(root, directory, found) {
  for (const entry of readdirSync(directory, { withFileTypes: true })) {
    if (SKIP.has(entry.name)) continue;
    const full = path.join(directory, entry.name);
    if (SHADOW.test(entry.name)) found.push(path.relative(root, full));
    // Dirent reports the directory entry itself. Unlike statSync it never
    // follows a symlink into a foreign tree (or back into this one), and a
    // symlink is not reported as a directory here. The link's own name is
    // still checked above, because a symlink named `models 2` is a shadow file
    // even though its target is outside this check's authority.
    if (entry.isDirectory()) walk(root, full, found);
  }
  return found;
}

export function findShadowFiles(root = repositoryRoot) {
  const resolvedRoot = path.resolve(root);
  return walk(resolvedRoot, resolvedRoot, []).sort();
}

export function main(root = repositoryRoot) {
  const found = findShadowFiles(root);
  if (found.length === 0) {
    console.log("No shadow copies found.");
    return 0;
  }

  console.error(
    `${found.length} shadow ${found.length === 1 ? "copy" : "copies"} found. ` +
      "These are almost certainly duplicates created by a sync or copy tool, and " +
      "directory-walking loaders will pick them up:\n",
  );
  for (const file of found) console.error(`  ${file}`);
  console.error(
    "\nCompare each against the file it shadows, then delete it. If one is " +
      "genuinely wanted, rename it so it does not end in a space and a number.",
  );
  return 1;
}

const invokedPath = process.argv[1] ? pathToFileURL(path.resolve(process.argv[1])).href : "";
if (import.meta.url === invokedPath) {
  process.exitCode = main();
}
