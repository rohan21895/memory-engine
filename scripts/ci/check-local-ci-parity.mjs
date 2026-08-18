// Fail if local-ci.sh and ci.yml drift apart.
//
// scripts/ci/local-ci.sh advertised itself as running "the same commands CI
// runs" while omitting four of them, including the shadow-file guard. A tree
// carrying an iCloud shadow copy could print GREEN, and that claim was
// repeated in merge decisions for three days (#97).
//
// Two lists that must agree, maintained by hand, in different languages, is a
// standing invitation to drift. So neither is trusted: this derives the
// workflow's platform-independent commands from ci.yml and asserts the script
// covers every one of them.
//
// A command is EXEMPT only if it appears below with a stated reason. Adding an
// exemption is a deliberate act that shows up in review; forgetting to update
// the script is not.

import { readFileSync } from "node:fs";
import path from "node:path";
import process from "node:process";
import { repositoryRoot } from "./lib.mjs";

const workflow = readFileSync(
  path.join(repositoryRoot, ".github/workflows/ci.yml"),
  "utf8",
);
const script = readFileSync(
  path.join(repositoryRoot, "scripts/ci/local-ci.sh"),
  "utf8",
);

// Reasons, not just a skip list. Each says why the local script cannot or need
// not run this, so a reader can judge whether the exemption is still true.
const EXEMPT = [
  [/^npm ci\b/, "installs dependencies; the local tree already has them"],
  [/^python3 -m pip install/, "installs dependencies"],
  [/^cargo test .*apps\/desktop/, "Windows Desktop job; not platform-independent"],
  [/^cargo test .*workers\/ingest/, "run by run-workspace-check.mjs, which the script does call"],
  [/^npm run codegen:check/, "same check as check-codegen-freshness.mjs, which the script calls"],
  [/^npm run lint$/, "run by run-workspace-check.mjs lint"],
  [/^npm test$/, "run by run-workspace-check.mjs test"],
];

const commands = [
  ...new Set(
    workflow
      .split("\n")
      .map((line) => line.match(/^\s*-\s+run:\s+(.*)$/))
      .filter(Boolean)
      .map((m) => m[1].trim()),
  ),
];

const missing = [];
for (const command of commands) {
  const exemption = EXEMPT.find(([pattern]) => pattern.test(command));
  if (exemption) continue;

  // Compare on the distinctive part: the script wraps commands in `run "label"`
  // and may add flags, so a substring match is the honest test rather than
  // string equality.
  //
  // `npm run test:egress` has no path-like token, so the first version of this
  // looked for "undefined" and reported a command missing that was present.
  // A checker that cries wolf gets muted, which would have reintroduced the
  // very drift it exists to catch -- so npm scripts are matched by name.
  const npmScript = command.match(/^npm run ([\w:-]+)/);
  const signature = npmScript
    ? npmScript[1]
    : command
        .replace(/^python3 -m /, "")
        .replace(/^node /, "")
        .replace(/\s+-v$/, "")
        .split(/\s+/)
        .find((token) => token.includes("/") || token.includes("."));

  if (!signature) {
    missing.push({ command, signature: "(could not derive one)" });
  } else if (!script.includes(signature)) {
    missing.push({ command, signature });
  }
}

if (missing.length === 0) {
  console.log(
    `local-ci.sh covers all ${commands.length - EXEMPT.length} platform-independent workflow commands.`,
  );
  process.exit(0);
}

console.error(
  "local-ci.sh does not run these commands that .github/workflows/ci.yml does:\n",
);
for (const { command, signature } of missing) {
  console.error(`  ${command}`);
  console.error(`    (looked for "${signature}" in the script)`);
}
console.error(
  "\nEither add them to scripts/ci/local-ci.sh, or add an EXEMPT entry here " +
    "with the reason. Do not let the script keep claiming coverage it does " +
    "not have -- that is exactly issue #97.",
);
process.exit(1);
