import { existsSync } from "node:fs";
import path from "node:path";
import process from "node:process";
import { findFilesNamed, readJson, repositoryRoot, run } from "./lib.mjs";

const check = process.argv[2];
if (!new Set(["lint", "test"]).has(check)) {
  throw new Error("Usage: run-workspace-check.mjs <lint|test>");
}

let checksRun = 0;

for (const manifest of findFilesNamed("package.json")) {
  if (manifest === path.join(repositoryRoot, "package.json")) {
    continue;
  }

  const packageJson = readJson(manifest);
  if (packageJson.scripts?.[check]) {
    run("npm", ["run", check], { cwd: path.dirname(manifest) });
    checksRun += 1;
  }
}

const rootCargoManifest = path.join(repositoryRoot, "Cargo.toml");
const cargoManifests = existsSync(rootCargoManifest)
  ? [rootCargoManifest]
  : findFilesNamed("Cargo.toml");

for (const manifest of cargoManifests) {
  if (check === "lint") {
    run("cargo", ["fmt", "--manifest-path", manifest, "--", "--check"]);
    run("cargo", ["clippy", "--manifest-path", manifest, "--all-targets", "--all-features", "--", "-D", "warnings"]);
  } else {
    run("cargo", ["test", "--manifest-path", manifest, "--all-features"]);
  }
  checksRun += 1;
}

for (const pyproject of findFilesNamed("pyproject.toml")) {
  const projectDirectory = path.dirname(pyproject);
  if (check === "lint") {
    run("python3", ["-m", "compileall", "-q", projectDirectory]);
    checksRun += 1;
  } else {
    const testsDirectory = path.join(projectDirectory, "tests");
    if (existsSync(testsDirectory)) {
      run("python3", ["-m", "unittest", "discover", "-s", testsDirectory]);
      checksRun += 1;
    }
  }
}

if (checksRun === 0) {
  console.log(`No component ${check} commands exist yet; skeleton check passed.`);
} else {
  console.log(`Completed ${checksRun} component ${check} check(s).`);
}
