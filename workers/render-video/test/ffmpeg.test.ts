import { describe, expect, it } from "vitest";

import { run, TOOL_OUTPUT_CAPTURE_LIMIT_BYTES, type ToolProgress } from "../src/ffmpeg.js";

describe("media-tool process supervision", () => {
  it("parses ffmpeg progress without retaining its unbounded stdout stream", async () => {
    const seen: ToolProgress[] = [];
    const result = await run(
      process.execPath,
      [
        "-e",
        "process.stdout.write('frame=42\\nout_time_us=1400000\\nprogress=continue\\n' + " +
          "'frame=90\\nout_time_us=3000000\\nprogress=end\\n')",
      ],
      {
        stdoutLimitBytes: 0,
        onProgress: (progress) => {
          seen.push(progress);
        },
      },
    );

    expect(result.stdout).toBe("");
    expect(seen).toEqual([
      { frame: 42, outTimeUs: 1_400_000, status: "continue" },
      { frame: 90, outTimeUs: 3_000_000, status: "end" },
    ]);
  });

  it("fails rather than materialising oversized machine-readable output", async () => {
    await expect(
      run(process.execPath, ["-e", `process.stdout.write('x'.repeat(${TOOL_OUTPUT_CAPTURE_LIMIT_BYTES + 1}))`]),
    ).rejects.toThrow(/more than 262144 bytes/);
  });

  it("bounds a failed tool diagnostic while preserving its tail", async () => {
    let message = "";
    try {
      await run(process.execPath, [
        "-e",
        `require('node:fs').writeSync(2, 'discard-me:' + 'x'.repeat(${TOOL_OUTPUT_CAPTURE_LIMIT_BYTES * 2}) + ':tail-marker'); process.exit(7)`,
      ]);
    } catch (error) {
      message = (error as Error).message;
    }

    expect(message).toContain("tail-marker");
    expect(message).not.toContain("discard-me");
    expect(Buffer.byteLength(message)).toBeLessThan(8_500);
  });
});
