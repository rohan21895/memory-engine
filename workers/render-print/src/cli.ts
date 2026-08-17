#!/usr/bin/env node
import { readFile, rename, writeFile } from "node:fs/promises";

import type { AlbumSpec, JobSpec } from "../../../contracts/codegen/generated/typescript/index.js";

import { runRenderPrintJob } from "./job.js";

async function persistJson(path: string, value: unknown): Promise<void> {
  const temporary = `${path}.next`;
  await writeFile(temporary, `${JSON.stringify(value, null, 2)}\n`, { mode: 0o600 });
  await rename(temporary, path);
}

async function main(): Promise<void> {
  const [command, jobPath, albumPath] = process.argv.slice(2);
  if (command !== "run" || !jobPath || !albumPath) {
    throw new Error("usage: memory-engine-render-print run <job-spec.json> <album-spec.json>");
  }
  const job = JSON.parse(await readFile(jobPath, "utf8")) as JobSpec;
  const album = JSON.parse(await readFile(albumPath, "utf8")) as AlbumSpec;
  const result = await runRenderPrintJob(job, album, { persist: (next) => persistJson(jobPath, next) });
  if (result.state.status !== "completed") {
    process.stderr.write(`${result.error?.code ?? "internal_error"}: ${result.error?.message ?? "Print render failed."}\n`);
    process.exitCode = 1;
  }
}

await main();
