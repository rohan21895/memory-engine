import { access, mkdtemp, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import { runRenderPrintJob } from "../src/job.js";
import {
  findTestFont,
  makeAlbum,
  makeClearance,
  makeJob,
  sourceJpeg,
  HASH_A,
  HASH_B,
} from "./helpers.js";

describe("render_print JobSpec execution", () => {
  it("checkpoints pages, completes, and keeps a completed replay terminal", async () => {
    const directory = await mkdtemp(join(tmpdir(), "render-print-job-"));
    const sourcePath = join(directory, "source.jpg");
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

  it("invalidates every version-1 page checkpoint before resuming", async () => {
    const directory = await mkdtemp(join(tmpdir(), "render-print-v1-checkpoint-"));
    const sourcePath = join(directory, "source.jpg");
    await writeFile(sourcePath, await sourceJpeg());
    const params = {
      output_path: join(directory, "album.pdf"),
      work_directory: join(directory, "work"),
      icc_profile: { name: "Sharp built-in CMYK", builtin: "cmyk" },
      asset_paths: { bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb: sourcePath },
      font_paths: { "Test Font": await findTestFont() },
      safety_clearance: makeClearance(),
    };
    const job = makeJob(params);
    job.checkpoint = {
      resumable: true,
      checkpoint_version: 1,
      cursor: JSON.stringify({
        version: 1,
        pages: [
          {
            index: 0,
            id: HASH_A,
            path: join(params.work_directory, HASH_B, "pages", "old-page.jpg"),
          },
        ],
      }),
      completed_input_ids: [],
      partial_output_ids: [HASH_A],
    };
    const progress: number[] = [];

    const completed = await runRenderPrintJob(job, makeAlbum(), {
      persist: async (state) => {
        progress.push(state.state.progress?.units_done ?? -1);
      },
    });

    expect(progress[0]).toBe(0);
    expect(completed.state.status).toBe("completed");
    expect(completed.checkpoint?.checkpoint_version).toBe(2);
  }, 30_000);

  it("does not return a completed version-1 PDF job as a valid replay", async () => {
    const legacy = makeJob({});
    legacy.state.status = "completed";
    legacy.checkpoint!.checkpoint_version = 1;
    legacy.outputs = [
      {
        kind: "rendered_pdf",
        id: HASH_A,
        path: "obsolete.pdf",
        byte_size: 1,
        produced_at: "2026-08-17T00:00:00.000Z",
      },
    ];
    const persisted: string[] = [];

    const invalidated = await runRenderPrintJob(legacy, makeAlbum(), {
      persist: async (state) => {
        persisted.push(state.state.status);
      },
    });

    expect(invalidated.state.status).toBe("failed");
    expect(invalidated.outputs).toEqual([]);
    expect(invalidated.error).toMatchObject({ code: "validation_failed", retryable: false });
    expect(invalidated.error?.message).toMatch(/obsolete renderer/);
    expect(invalidated.checkpoint?.checkpoint_version).toBe(2);
    expect(persisted).toEqual(["failed"]);
  });

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

  it("does not resolve originals or create a PDF for an enhancement it cannot execute", async () => {
    const directory = await mkdtemp(join(tmpdir(), "render-print-enhancement-refusal-"));
    const output = join(directory, "blocked.pdf");
    const params = {
      output_path: output,
      work_directory: join(directory, "work"),
      icc_profile: { name: "Sharp built-in CMYK", builtin: "cmyk" },
      asset_paths: { [HASH_B]: join(directory, "must-not-be-read.jpg") },
      font_paths: {},
      safety_clearance: makeClearance(),
    };
    const album = makeAlbum();
    album.pages[0]!.placements[0]!.enhancement_ops = [
      { op_id: "licensed-denoise", kind: "denoise", order: 0, license_cleared: true },
    ];

    const failed = await runRenderPrintJob(makeJob(params), album, { persist: async () => undefined });

    expect(failed.state.status).toBe("failed");
    expect(failed.error).toMatchObject({ code: "validation_failed", retryable: false });
    expect(failed.error?.message).toMatch(/cannot execute/);
    await expect(access(output)).rejects.toMatchObject({ code: "ENOENT" });
  });
});
