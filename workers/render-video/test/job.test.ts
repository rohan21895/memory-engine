import { copyFile, readFile, stat, unlink, writeFile } from "node:fs/promises";
import { join } from "node:path";

import { beforeAll, describe, expect, it } from "vitest";

import type { EDL, JobSpec } from "../../../contracts/codegen/generated/typescript/index.js";

import { run } from "../src/ffmpeg.js";
import { digestFile } from "../src/digest.js";
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
let completedArtifact: { id: string; path: string; byteSize: number };

beforeAll(async () => {
  await run(TOOLS.ffmpeg, ["-hide_banner", "-version"]);
  source = await fixture();
  const jobParams = await params("completed-artifact");
  const outcome = await runRenderVideoJob(makeJob(edl().edl_id, jobParams), edl(), {
    persist: async () => undefined,
  });
  if (!outcome.result) throw new Error(outcome.job.error?.message ?? "Could not build the completed-job fixture.");
  completedArtifact = {
    id: outcome.result.id,
    path: jobParams.output_path as string,
    byteSize: outcome.result.byteSize,
  };
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

function longFilmEdl(): EDL {
  const plan = makeEdl({
    mediaRefs: [videoRef(source.videoMediaId)],
    resolution: { width: 64, height: 36 },
    aspect: { numerator: 16, denominator: 9 },
    audioPlan: {
      music: [],
      ambient: {
        enabled: true,
        default_gain_db: 0,
        preserve_speech: true,
        high_pass_hz: null,
        noise_suppression: "none",
        per_clip_gain_db: [],
      },
      ducking: [],
      mix: {
        master_gain_db: 0,
        loudness_target_lufs: -14,
        true_peak_ceiling_db: -1,
        limiter: true,
        channels: "stereo",
        sample_rate: 48_000,
      },
    },
    tracks: [
      {
        track_id: "v1",
        kind: "video",
        name: "V1",
        role: "primary",
        enabled: true,
        items: Array.from({ length: 90 }, (_, index) =>
          clip(`film-${String(index).padStart(3, "0")}`, "src-a", SOURCE_ORIGIN + (index % 9) * 30, 30, {
            audio: { gain_db: 0, muted: false, fade_in: null, fade_out: null, audio_extends_past_out: null },
          }),
        ),
      },
    ],
  });
  plan.kind = "film";
  plan.name = "ninety-second progress fixture";
  plan.target.destination = "master";
  return plan;
}

async function params(prefix: string, withAudio = false): Promise<Record<string, unknown>> {
  const work = await workspace(prefix);
  return {
    output_path: join(work, "out.mkv"),
    work_directory: work,
    sources: { [source.videoMediaId]: { paths: [source.videoPath] } },
    encode: {
      container: FFV1_MKV.container,
      scale_flags: FFV1_MKV.scale_flags,
      video: { codec: "ffv1", pix_fmt: "yuv420p", args: [] },
      audio: withAudio ? { codec: "pcm_s16le", sample_fmt: "s16", args: [] } : null,
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

async function completedJob(prefix: string): Promise<JobSpec> {
  const jobParams = await params(prefix);
  const outputPath = jobParams.output_path as string;
  await copyFile(completedArtifact.path, outputPath);
  const job = makeJob(edl().edl_id, jobParams);
  job.state = {
    status: "completed",
    attempts: 1,
    started_at: "2026-08-17T00:00:00Z",
    heartbeat_at: "2026-08-17T00:00:01Z",
    finished_at: "2026-08-17T00:00:01Z",
    progress: { units_done: 1, units_total: 1, unit: "files" },
  };
  job.outputs = [
    {
      kind: "rendered_video",
      id: completedArtifact.id,
      path: outputPath,
      byte_size: completedArtifact.byteSize,
      produced_at: "2026-08-17T00:00:01Z",
    },
  ];
  return job;
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

  it("persists frame progress and fresh heartbeats while rendering a many-clip ninety-second film", async () => {
    const plan = longFilmEdl();
    const jobParams = await params("job-long-film", true);
    const store: JobSpec[] = [];
    let tick = 0;

    const outcome = await runRenderVideoJob(makeJob(plan.edl_id, jobParams), plan, {
      persist: persistTo(store),
      now: () => new Date(Date.UTC(2026, 7, 17, 0, 0, tick++)).toISOString(),
    });

    expect(outcome.job.state.status).toBe("completed");
    expect(outcome.result?.program).toMatchObject({ totalFrames: 2_700, segments: 90, audioContributions: 90 });
    expect(outcome.result?.verification.loudness?.integratedLufs).toBeCloseTo(-14, 0);
    expect(outcome.result?.verification.loudness?.truePeakDb).toBeLessThanOrEqual(-1 + 0.3);
    const running = store.filter((snapshot) => snapshot.state.status === "running");
    expect(running.length).toBeGreaterThan(1);
    expect(
      running.some(
        (snapshot) =>
          snapshot.state.progress?.unit === "frames" &&
          snapshot.state.progress.units_done > 0 &&
          snapshot.state.progress.units_total === 2_700,
      ),
    ).toBe(true);
    expect(new Set(running.map((snapshot) => snapshot.state.heartbeat_at)).size).toBeGreaterThan(1);
    expect(outcome.job.state.progress).toEqual({ units_done: 2_700, units_total: 2_700, unit: "frames" });
  }, 240_000);

  it("reuses a completed job only after its published artifact verifies", async () => {
    const store: JobSpec[] = [];
    const job = await completedJob("job-replay");
    const replay = await runRenderVideoJob(job, edl(), { persist: persistTo(store) });
    expect(replay.result).toBeNull();
    expect(replay.job).toBe(job);
    expect(store).toEqual([]);
  }, 240_000);

  it("fails a completed job whose published artifact is missing", async () => {
    const job = await completedJob("job-replay-missing");
    await unlink(job.outputs![0]!.path!);
    const store: JobSpec[] = [];

    const replay = await runRenderVideoJob(job, edl(), { persist: persistTo(store) });

    expect(replay.result).toBeNull();
    expect(replay.job.state.status).toBe("failed");
    expect(replay.job.error).toMatchObject({ code: "file_not_found", retryable: false });
    expect(store.at(-1)?.state.status).toBe("failed");
  }, 60_000);

  it("fails a completed job whose published artifact was truncated", async () => {
    const job = await completedJob("job-replay-truncated");
    const bytes = await readFile(job.outputs![0]!.path!);
    await writeFile(job.outputs![0]!.path!, bytes.subarray(0, Math.floor(bytes.length / 2)));

    const replay = await runRenderVideoJob(job, edl(), { persist: async () => undefined });

    expect(replay.job.state.status).toBe("failed");
    expect(replay.job.error).toMatchObject({ code: "file_corrupt", retryable: false });
    expect(replay.job.error!.message).toMatch(/byte size/i);
  }, 60_000);

  it("fails a completed job whose published artifact has a different BLAKE3 at the same size", async () => {
    const job = await completedJob("job-replay-digest");
    const path = job.outputs![0]!.path!;
    const bytes = await readFile(path);
    bytes[bytes.length - 1] = bytes[bytes.length - 1]! ^ 0xff;
    await writeFile(path, bytes);

    const replay = await runRenderVideoJob(job, edl(), { persist: async () => undefined });

    expect((await stat(path)).size).toBe(job.outputs![0]!.byte_size);
    expect(replay.job.state.status).toBe("failed");
    expect(replay.job.error).toMatchObject({ code: "file_corrupt", retryable: false });
    expect(replay.job.error!.message).toMatch(/BLAKE3/i);
  }, 60_000);

  it("reapplies the post-render probe invariants before reusing a completed job", async () => {
    const job = await completedJob("job-replay-probe");
    const path = job.outputs![0]!.path!;
    await run(TOOLS.ffmpeg, [
      "-nostdin",
      "-hide_banner",
      "-nostats",
      "-f",
      "lavfi",
      "-i",
      "color=size=320x240:rate=30000/1001",
      "-frames:v",
      "30",
      "-c:v",
      "ffv1",
      "-pix_fmt",
      "yuv420p",
      "-f",
      "matroska",
      "-y",
      path,
    ]);
    job.outputs![0]!.id = await digestFile(path);
    job.outputs![0]!.byte_size = (await stat(path)).size;

    const replay = await runRenderVideoJob(job, edl(), { persist: async () => undefined });

    expect(replay.job.state.status).toBe("failed");
    expect(replay.job.error).toMatchObject({ code: "internal_error", retryable: false });
    expect(replay.job.error!.message).toMatch(/320x240.*360x640/);
  }, 60_000);

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
