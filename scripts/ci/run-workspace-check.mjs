import { existsSync } from "node:fs";
import path from "node:path";
import process from "node:process";
import { findFilesNamed, readJson, repositoryRoot, run } from "./lib.mjs";

const check = process.argv[2];
if (!new Set(["lint", "test"]).has(check)) {
  throw new Error("Usage: run-workspace-check.mjs <lint|test>");
}

let checksRun = 0;

// Tests that exercise deliberately uninstalled, non-redistributable artifacts
// are outside the required checkout-only suite. They are removed explicitly
// and reported as NOT RUN; every other unittest skip is a hard failure.
const optionalPythonTests = new Map();
const fetchedYuNet = path.join(
  repositoryRoot,
  "models",
  "weights",
  "face_detection_yunet_2023mar.onnx",
);
if (!existsSync(fetchedYuNet)) {
  optionalPythonTests.set(path.join(repositoryRoot, "models"), [
    "test_fetch_weights.PayloadShapeChecks.test_the_real_fetched_yunet_passes_if_it_is_here",
  ]);
}

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
      const requiredSuite = path.join(testsDirectory, "run_required_suite.py");
      if (existsSync(requiredSuite)) {
        // A component with load-bearing optional dependencies owns the rule for
        // what constitutes a complete run. Plain unittest discovery exits zero
        // when whole integration classes skip, which is not a passing signal.
        run("python3", [requiredSuite]);
      } else {
        const requiredRunner = path.join(
          repositoryRoot,
          "scripts",
          "ci",
          "run-required-unittest.py",
        );
        const exclusions = (optionalPythonTests.get(projectDirectory) ?? []).flatMap(
          (testId) => ["--exclude", testId],
        );
        run("python3", [requiredRunner, testsDirectory, ...exclusions]);
      }
      checksRun += 1;
    }
  }
}

if (checksRun === 0) {
  console.log(`No component ${check} commands exist yet; skeleton check passed.`);
} else {
  console.log(`Completed ${checksRun} component ${check} check(s).`);
}
