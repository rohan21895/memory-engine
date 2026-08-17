#!/usr/bin/env node
import { readFile, rename, writeFile } from "node:fs/promises";

import type { EDL, JobSpec } from "../../../contracts/codegen/generated/typescript/index.js";

import { runRenderVideoJob } from "./job.js";

async function persistJson(path: string, value: unknown): Promise<void> {
  const temporary = `${path}.next`;
  await writeFile(temporary, `${JSON.stringify(value, null, 2)}\n`, { mode: 0o600 });
  await rename(temporary, path);
}

async function main(): Promise<void> {
  const [command, jobPath, edlPath] = process.argv.slice(2);
  if (command !== "run" || !jobPath || !edlPath) {
    throw new Error("usage: memory-engine-render-video run <job-spec.json> <edl.json>");
  }
  const job = JSON.parse(await readFile(jobPath, "utf8")) as JobSpec;
  const edl = JSON.parse(await readFile(edlPath, "utf8")) as EDL;
  const outcome = await runRenderVideoJob(job, edl, { persist: (next) => persistJson(jobPath, next) });

  if (outcome.result) {
    // Nothing this worker declined to execute is allowed to be invisible.
    for (const entry of outcome.result.unacted) {
      process.stderr.write(`not acted upon: ${entry.field} — ${entry.detail}\n`);
    }
    for (const entry of outcome.result.interpretations) {
      process.stderr.write(`interpreted: ${entry.field} — ${entry.convention} (${entry.issue})\n`);
    }
    process.stdout.write(
      `${JSON.stringify(
        {
          output_id: outcome.result.id,
          command_graph_digest: outcome.result.commandGraphDigest,
          verification: outcome.result.verification,
        },
        null,
        2,
      )}\n`,
    );
  }

  if (outcome.job.state.status !== "completed") {
    process.stderr.write(
      `${outcome.job.error?.code ?? "internal_error"}: ${outcome.job.error?.message ?? "Video render failed."}\n`,
    );
    process.exitCode = 1;
  }
}

await main();
