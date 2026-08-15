import { existsSync } from "node:fs";
import path from "node:path";
import { hasFiles, readJson, repositoryRoot, run } from "./lib.mjs";

const schemasDirectory = path.join(repositoryRoot, "contracts", "schemas");
const codegenDirectory = path.join(repositoryRoot, "contracts", "codegen");

if (!hasFiles(schemasDirectory)) {
  console.log("No contract schemas exist yet; code generation has nothing to do.");
  process.exit(0);
}

const packageManifest = path.join(codegenDirectory, "package.json");
const shellGenerator = path.join(codegenDirectory, "generate.sh");
const pythonGenerator = path.join(codegenDirectory, "generate.py");

if (existsSync(packageManifest) && readJson(packageManifest).scripts?.generate) {
  run("npm", ["run", "generate"], { cwd: codegenDirectory });
} else if (existsSync(shellGenerator)) {
  run("bash", [shellGenerator]);
} else if (existsSync(pythonGenerator)) {
  run("python3", [pythonGenerator]);
} else {
  throw new Error(
    "Contract schemas exist, but contracts/codegen has no generator. " +
      "Add a package.json 'generate' script, generate.sh, or generate.py.",
  );
}
