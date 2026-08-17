import { readFile, stat } from "node:fs/promises";
import { join } from "node:path";

import { beforeAll, describe, expect, it } from "vitest";

import type { EDL, JobSpec } from "../../../contracts/codegen/generated/typescript/index.js";

import { run } from "../src/ffmpeg.js";
import { parseParams, runRenderVideoJob } from "../src/job.js";
import { renderVideo } from "../src/renderer.js";
import {
  clip,
  FFV1_MKV,
  fixture,
  makeEdl,
  makeJob,
  SOURCE_ORIGIN,
  t,
  TOOLS,
  videoRef,
  workspace,
  type Fixture,
} from "./helpers.js";

let source: Fixture;

beforeAll(async () => {
  await run(TOOLS.ffmpeg, ["-hide_banner", "-version"]);
  source = await fixture();
}, 180_000);

function edl(): EDL {
  return makeEdl({
    mediaRefs: [videoRef(source.videoMediaId)],
    tracks: [
      {
        track_id: "v1",
        kind: "video",
        name: "V1",
        role: "primary",
        enabled: true,
        items: [clip("clip-01", "src-a", SOURCE_ORIGIN + 10, 30)],
      },
    ],
  });
}

async function params(prefix: string): Promise<Record<string, unknown>> {
  const work = await workspace(prefix);
  return {
    output_path: join(work, "out.mkv"),
    work_directory: work,
    sources: { [source.videoMediaId]: { paths: [source.videoPath] } },
    encode: {
      container: FFV1_MKV.container,
      scale_flags: FFV1_MKV.scale_flags,
      video: { codec: "ffv1", pix_fmt: "yuv420p", args: [] },
      audio: null,
      threads: 1,
    },
    ffmpeg_path: TOOLS.ffmpeg,
    ffprobe_path: TOOLS.ffprobe,
  };
}

function persistTo(store: JobSpec[]): (job: JobSpec) => Promise<void> {
  return async (job) => {
    store.push(structuredClone(job));
  };
}

describe("the render_video job", () => {
  it("runs, publishes a content-addressed output, and records it on the JobSpec", async () => {
    const jobParams = await params("job-ok");
    const store: JobSpec[] = [];
    const { job, result } = await runRenderVideoJob(makeJob(edl().edl_id, jobParams), edl(), {
      persist: persistTo(store),
      now: () => "2026-08-17T00:00:00Z",
    });

    expect(job.state.status).toBe("completed");
    expect(job.outputs).toHaveLength(1);
    expect(job.outputs![0]).toMatchObject({ kind: "rendered_video", id: result!.id });
    expect(job.checkpoint!.completed_input_ids).toEqual([edl().edl_id]);
    const published = job.outputs![0]!.path!;
    expect((await stat(published)).size).toBe(result!.byteSize);
    // The state was persisted before the render started, so a kill mid-encode is resumable.
    expect(store[0]!.state.status).toBe("running");
  }, 240_000);

  it("returns a completed job untouched instead of rendering again", async () => {
    const jobParams = await params("job-replay");
    const store: JobSpec[] = [];
    const { job } = await runRenderVideoJob(makeJob(edl().edl_id, jobParams), edl(), { persist: persistTo(store) });
    const replay = await runRenderVideoJob(job, edl(), { persist: persistTo(store) });
    expect(replay.result).toBeNull();
    expect(replay.job).toBe(job);
  }, 240_000);

  it("publishes the same bytes twice and refuses to overwrite a different render", async () => {
    const jobParams = await params("job-publish");
    const store: JobSpec[] = [];
    const first = await runRenderVideoJob(makeJob(edl().edl_id, jobParams), edl(), { persist: persistTo(store) });
    expect(first.job.state.status).toBe("completed");

    // Same output path, different plan: the publish step must refuse rather than clobber.
    const other = edl();
    other.tracks[0]!.items[0] = clip("clip-01", "src-a", SOURCE_ORIGIN + 40, 30);
    const conflict = makeJob(other.edl_id, jobParams);
    const second = await runRenderVideoJob(conflict, other, { persist: persistTo(store) });
    expect(second.job.state.status).toBe("failed");
    expect(second.job.error!.message).toMatch(/already contains different bytes/);
    expect((await readFile(jobParams.output_path as string)).length).toBeGreaterThan(0);
  }, 300_000);

  it("refuses a job whose params digest does not match its params", async () => {
    const jobParams = await params("job-digest");
    const job = makeJob(edl().edl_id, jobParams);
    job.params_digest = "0".repeat(64);
    const { job: failed } = await runRenderVideoJob(job, edl(), { persist: async () => undefined });
    expect(failed.state.status).toBe("failed");
    expect(failed.error!.code).toBe("validation_failed");
    expect(failed.error!.message).toMatch(/params digest/);
  }, 60_000);

  it("refuses a job that does not declare its source-file read, or asks for egress", async () => {
    const jobParams = await params("job-req");
    const noSourceRead = makeJob(edl().edl_id, jobParams);
    noSourceRead.requirements = { compute: "cpu", requires_source_file: false };
    expect((await runRenderVideoJob(noSourceRead, edl(), { persist: async () => undefined })).job.error!.message).toMatch(
      /source-file read/,
    );

    const egress = makeJob(edl().edl_id, jobParams);
    egress.egress = { requires_egress: true };
    expect((await runRenderVideoJob(egress, edl(), { persist: async () => undefined })).job.error!.message).toMatch(
      /egress/,
    );
  }, 60_000);

  it("refuses a job whose EDL is not the one it names", async () => {
    const jobParams = await params("job-edl");
    const job = makeJob("f".repeat(64), jobParams);
    const { job: failed } = await runRenderVideoJob(job, edl(), { persist: async () => undefined });
    expect(failed.error!.message).toMatch(/does not match the EDL/);
  }, 60_000);
});

describe("the encode profile", () => {
  it("has no defaults: every field must be stated by the job", () => {
    const base = {
      output_path: "/tmp/out.mp4",
      work_directory: "/tmp",
      sources: { ["a".repeat(64)]: { paths: ["/tmp/a.mp4"] } },
    };
    expect(() => parseParams({ ...base })).toThrow(/contracts#56/);
    expect(() => parseParams({ ...base, encode: { container: "mp4" } })).toThrow(/scale_flags/);
    expect(() =>
      parseParams({ ...base, encode: { container: "mp4", scale_flags: "bicubic", threads: 1 } }),
    ).toThrow(/encode.video/);
    expect(() =>
      parseParams({
        ...base,
        encode: {
          container: "mp4",
          scale_flags: "bicubic",
          threads: 1,
          video: { codec: "libx264", pix_fmt: "yuv420p", args: [] },
        },
      }),
    ).not.toThrow();
  });

  it("refuses a resolver keyed by anything other than a content hash", () => {
    expect(() =>
      parseParams({
        output_path: "/tmp/out.mp4",
        work_directory: "/tmp",
        sources: { "some-file.mp4": { paths: ["/tmp/a.mp4"] } },
        encode: {
          container: "mp4",
          scale_flags: "bicubic",
          threads: 1,
          video: { codec: "libx264", pix_fmt: "yuv420p", args: [] },
        },
      }),
    ).toThrow(/BLAKE3 media_id/);
  });
});

describe("dips", () => {
  it("renders a dip to black without changing the timeline length", async () => {
    const withDip = makeEdl({
      mediaRefs: [videoRef(source.videoMediaId)],
      tracks: [
        {
          track_id: "v1",
          kind: "video",
          name: "V1",
          role: "primary",
          enabled: true,
          items: [
            clip("clip-01", "src-a", SOURCE_ORIGIN + 20, 40),
            {
              item_type: "transition",
              transition_id: "dip",
              transition_type: "dip_to_black",
              in_offset: t(5),
              out_offset: t(5),
              easing: "linear",
              parameters: {},
            },
            clip("clip-02", "src-a", SOURCE_ORIGIN + 150, 40),
          ],
        },
      ],
    });
    const result = await renderVideo(withDip, {
      sources: { [source.videoMediaId]: { paths: [source.videoPath] } },
      encode: {
        container: "matroska",
        scale_flags: "bicubic",
        video: { codec: "ffv1", pix_fmt: "yuv420p", args: [] },
        audio: null,
        threads: 1,
      },
      workDirectory: await workspace("dip"),
      tools: TOOLS,
    });
    expect(result.verification.frameCount).toBe(80);
    expect(result.filterGraph).toContain("fade=type=out:start_frame=0:nb_frames=5:color=black");
    expect(result.filterGraph).toContain("fade=type=in:start_frame=0:nb_frames=5:color=black");
  }, 240_000);
});
