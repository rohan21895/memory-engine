#!/usr/bin/env node

import { spawnSync } from "node:child_process";
import process from "node:process";

export function run(testFiles, options = {}) {
  if (testFiles.length === 0) {
    console.error("no-skip test runner: no test files were provided");
    return 2;
  }

  const childEnvironment = { ...process.env };
  // A runner regression test invokes this script from inside node:test. That
  // private marker must not leak into the new process or Node treats an actual
  // child invocation as a recursive in-process run and silently skips it.
  delete childEnvironment.NODE_TEST_CONTEXT;
  const result = spawnSync(
    options.nodePath ?? process.execPath,
    ["--test", "--test-reporter=tap", ...testFiles],
    { encoding: "utf8", env: childEnvironment, maxBuffer: 10 * 1024 * 1024 },
  );
  process.stdout.write(result.stdout ?? "");
  process.stderr.write(result.stderr ?? "");

  if (result.error) {
    console.error(`no-skip test runner could not execute: ${result.error.message}`);
    return 2;
  }
  if (result.status !== 0) return result.status ?? 2;

  const skipped = /^# skipped (\d+)$/m.exec(result.stdout ?? "");
  const todo = /^# todo (\d+)$/m.exec(result.stdout ?? "");
  if (skipped == null || todo == null) {
    console.error("no-skip test runner: TAP summary did not report skipped and TODO counts");
    return 2;
  }
  if (Number(skipped[1]) !== 0 || Number(todo[1]) !== 0) {
    console.error(
      `no-skip test runner: refusing a false green (${skipped[1]} skipped, ${todo[1]} TODO)`,
    );
    return 1;
  }
  return 0;
}

if (import.meta.url === new URL(process.argv[1] ?? "", "file:").href) {
  process.exitCode = run(process.argv.slice(2));
}
