import { spawnSync } from "node:child_process";
import path from "node:path";
import { repositoryRoot, run } from "./lib.mjs";

run("node", [path.join(repositoryRoot, "scripts", "ci", "generate-contracts.mjs")]);

const status = spawnSync(
  "git",
  ["status", "--porcelain=v1", "--untracked-files=all", "--", "contracts/codegen"],
  { cwd: repositoryRoot, encoding: "utf8" },
);

if (status.error) {
  throw status.error;
}
if (status.status !== 0) {
  throw new Error(`git status exited with status ${status.status}`);
}

if (status.stdout.trim()) {
  console.error("Generated contract bindings are stale:");
  console.error(status.stdout.trimEnd());
  console.error("Run `npm run codegen` and commit the regenerated bindings.");
  process.exit(1);
}

console.log("Generated contract bindings are fresh.");
