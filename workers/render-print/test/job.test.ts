import { access, mkdtemp } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import { runRenderPrintJob } from "../src/job.js";
import { findTestFont, makeAlbum, makeClearance, makeJob, sourceJpeg, HASH_A } from "./helpers.js";

describe("render_print JobSpec execution", () => {
  it("checkpoints pages, completes, and keeps a completed replay terminal", async () => {
    const directory = await mkdtemp(join(tmpdir(), "render-print-job-"));
    const sourcePath = join(directory, "source.jpg");
    const { writeFile } = await import("node:fs/promises");
    await writeFile(sourcePath, await sourceJpeg());
    const params = {
      output_path: join(directory, "album.pdf"),
      work_directory: join(directory, "work"),
      icc_profile: { name: "Sharp built-in CMYK", builtin: "cmyk" },
      asset_paths: { bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb: sourcePath },
      font_paths: { "Test Font": await findTestFont() },
      safety_clearance: makeClearance(),
    };
    const persisted: number[] = [];
    const completed = await runRenderPrintJob(makeJob(params), makeAlbum(), {
      now: () => "2026-08-17T00:00:00.000Z",
      persist: async (job) => {
        persisted.push(job.state.progress?.units_done ?? -1);
      },
    });
    expect(completed.state.status).toBe("completed");
    expect(completed.outputs?.[0]?.kind).toBe("rendered_pdf");
    expect(completed.checkpoint?.completed_input_ids).toEqual([HASH_A]);
    expect(persisted).toContain(1);
    await expect(access(params.output_path)).resolves.toBeUndefined();

    const invalid = makeAlbum();
    invalid.validation.status = "fail";
    const replay = await runRenderPrintJob(completed, invalid, {
      persist: async () => {
        throw new Error("completed jobs must not persist again");
      },
    });
    expect(replay.state.status).toBe("completed");
  }, 30_000);

  it("records a terminal validation error and never creates a PDF", async () => {
    const directory = await mkdtemp(join(tmpdir(), "render-print-refusal-"));
    const output = join(directory, "blocked.pdf");
    const params = {
      output_path: output,
      work_directory: join(directory, "work"),
      icc_profile: { name: "Sharp built-in CMYK", builtin: "cmyk" },
      asset_paths: {},
      font_paths: {},
      safety_clearance: makeClearance(),
    };
    const album = makeAlbum();
    album.validation.status = "fail";
    const failed = await runRenderPrintJob(makeJob(params), album, { persist: async () => undefined });
    expect(failed.state.status).toBe("failed");
    expect(failed.error).toMatchObject({ code: "validation_failed", retryable: false });
    await expect(access(output)).rejects.toMatchObject({ code: "ENOENT" });
  });
});
