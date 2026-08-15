import { spawnSync } from "node:child_process";
import { existsSync, readdirSync, readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

export const repositoryRoot = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "../..",
);

const ignoredDirectories = new Set([
  ".git",
  ".mypy_cache",
  ".pytest_cache",
  ".ruff_cache",
  ".venv",
  "artifacts",
  "build",
  "coverage",
  "dist",
  "node_modules",
  "target",
  "tmp",
]);

export function findFilesNamed(name, directory = repositoryRoot) {
  const matches = [];

  for (const entry of readdirSync(directory, { withFileTypes: true })) {
    if (entry.isDirectory() && ignoredDirectories.has(entry.name)) {
      continue;
    }

    const entryPath = path.join(directory, entry.name);
    if (entry.isDirectory()) {
      matches.push(...findFilesNamed(name, entryPath));
    } else if (entry.isFile() && entry.name === name) {
      matches.push(entryPath);
    }
  }

  return matches.sort();
}

export function hasFiles(directory) {
  if (!existsSync(directory)) {
    return false;
  }

  return readdirSync(directory, { withFileTypes: true }).some((entry) => {
    if (entry.name === ".gitkeep") {
      return false;
    }
    return entry.isFile() || (entry.isDirectory() && hasFiles(path.join(directory, entry.name)));
  });
}

export function readJson(file) {
  return JSON.parse(readFileSync(file, "utf8"));
}

export function run(command, args, options = {}) {
  const printable = [command, ...args].join(" ");
  console.log(`\n> ${printable}`);

  const result = spawnSync(command, args, {
    cwd: repositoryRoot,
    encoding: "utf8",
    stdio: "inherit",
    ...options,
  });

  if (result.error) {
    throw result.error;
  }
  if (result.status !== 0) {
    throw new Error(`${printable} exited with status ${result.status}`);
  }
}
