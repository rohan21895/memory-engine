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

import { readdirSync, statSync } from "node:fs";
import path from "node:path";
import process from "node:process";
import { repositoryRoot } from "./lib.mjs";

const SKIP = new Set([".git", "node_modules", "target", "dist", "__pycache__", ".venv"]);

// A shadow name is the original stem, a single space, one or more digits, then
// the original extension (or nothing, for extensionless files).
const SHADOW = /^.+ \d+(\.[^.]+)?$/;

function walk(directory, found) {
  for (const entry of readdirSync(directory)) {
    if (SKIP.has(entry)) continue;
    const full = path.join(directory, entry);
    if (SHADOW.test(entry)) found.push(path.relative(repositoryRoot, full));
    let stats;
    try {
      stats = statSync(full);
    } catch {
      continue; // a symlink to nowhere is not this check's problem
    }
    if (stats.isDirectory()) walk(full, found);
  }
  return found;
}

const found = walk(repositoryRoot, []);

if (found.length === 0) {
  console.log("No shadow copies found.");
  process.exit(0);
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
process.exit(1);
