import { mkdtemp, readFile, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { beforeAll, describe, expect, it } from "vitest";

import type { ColorOp, EDL, EncodeProfile, Track } from "../../../contracts/codegen/generated/typescript/index.js";

import { colorChain, fuseColorOps, opMatrix, outputColorArgs } from "../src/color.js";
import { digestFile } from "../src/digest.js";
import { buildGraph } from "../src/filtergraph.js";
import { run } from "../src/ffmpeg.js";
import { buildProgram } from "../src/program.js";
import { renderVideo } from "../src/renderer.js";
import { assertRenderable } from "../src/gate.js";
import { resolveSources } from "../src/sources.js";
import {
  clip,
  FFV1_MKV,
  fixture,
  makeEdl,
  SOURCE_ORIGIN,
  TOOLS,
  videoRef,
  withoutAudio,
  workspace,
  type Fixture,
} from "./helpers.js";

/**
 * contracts#49 and contracts#58 are closed by FORMULAS, written into the schema's
 * ColorOp and ToneMap $comments and called normative there. This file is what makes that
 * word mean something: the curves are re-implemented here from the schema's text, fed
 * through the real ffmpeg filters the renderer emits, and compared to float precision.
 *
 * It is not a formality. The first draft of the ToneMap table claimed reinhard's
 * `operator_param: 1` was "a straight scale by 1/peak". Running it showed the opposite —
 * the offset is zero there and every pixel above black becomes reference white, a white
 * frame — which is why the bound in the schema is now exclusive at both ends. A formula in
 * a contract that no implementation matches is worse than the gap it replaced, because
 * both sides believe it.
 */

let source: Fixture;

beforeAll(async () => {
  await run(TOOLS.ffmpeg, ["-hide_banner", "-version"]);
  source = await fixture();
}, 300_000);

// --- the schema's arithmetic, re-implemented from the $comment ---------------------

const HABLE = { a: 0.15, b: 0.5, c: 0.1, d: 0.2, e: 0.02, f: 0.3 };

function hableCurve(x: number): number {
  const { a, b, c, d, e, f } = HABLE;
  return (x * (a * x + c * b) + d * e) / (x * (a * x + b) + d * f) - e / f;
}

function mapped(operator: string, s: number, peak: number, param: number | null): number {
  switch (operator) {
    case "hable":
      return hableCurve(s) / hableCurve(peak);
    case "reinhard": {
      const offset = (1 - (param as number)) / (param as number);
      return ((s / (s + offset)) * (peak + offset)) / peak;
    }
    case "mobius": {
      const j = param as number;
      if (s <= j) return s;
      const a = (-j * j * (peak - 1)) / (j * j - 2 * j + peak);
      const b = (j * j - 2 * j * peak + peak) / Math.max(peak - 1, 1e-6);
      return (((b * b + 2 * b * j + j * j) / (b - a)) * (s + a)) / (s + b);
    }
    default:
      throw new Error(`no curve for ${operator}`);
  }
}

/** The whole per-pixel operator: desaturate, then map the brightest channel. */
function toneMapPixel(
  rgb: readonly [number, number, number],
  luma: readonly [number, number, number],
  operator: string,
  param: number | null,
  desaturation: number,
  peak: number,
): [number, number, number] {
  const eps = 1e-6;
  const y = luma[0] * rgb[0] + luma[1] * rgb[1] + luma[2] * rgb[2];
  const ob = Math.max(y - desaturation, eps) / Math.max(y, eps);
  const desat = rgb.map((v) => v * (1 - ob) + y * ob) as [number, number, number];
  const sig = Math.max(desat[0], desat[1], desat[2], eps);
  const scale = mapped(operator, sig, peak, param) / sig;
  return [desat[0] * scale, desat[1] * scale, desat[2] * scale];
}

/**
 * Push planar float RGB through one filter chain and read the floats back.
 *
 * `gbrpf32le` in and out means no quantisation and no colour conversion anywhere, so what
 * comes back is the filter's arithmetic and nothing else.
 */
async function throughFilter(
  pixels: ReadonlyArray<readonly [number, number, number]>,
  filter: string,
  matrixTag: string,
): Promise<Array<[number, number, number]>> {
  const directory = await mkdtemp(join(tmpdir(), "render-video-colour-"));
  const input = join(directory, "in.raw");
  const output = join(directory, "out.raw");
  const planes = new Float32Array(pixels.length * 3);
  // gbrpf32le plane order is G, B, R.
  pixels.forEach((rgb, i) => {
    planes[i] = rgb[1];
    planes[pixels.length + i] = rgb[2];
    planes[2 * pixels.length + i] = rgb[0];
  });
  await writeFile(input, Buffer.from(planes.buffer));
  await run(TOOLS.ffmpeg, [
    "-nostdin",
    "-hide_banner",
    "-nostats",
    "-f",
    "rawvideo",
    "-pix_fmt",
    "gbrpf32le",
    "-s",
    `${pixels.length}x1`,
    "-colorspace",
    matrixTag,
    "-color_trc",
    "linear",
    "-color_primaries",
    "bt709",
    "-i",
    input,
    "-vf",
    filter,
    "-f",
    "rawvideo",
    "-pix_fmt",
    "gbrpf32le",
    "-y",
    output,
  ]);
  const raw = await readFile(output);
  const out = new Float32Array(raw.buffer, raw.byteOffset, raw.byteLength / 4);
  return pixels.map((_, i) => [out[2 * pixels.length + i]!, out[i]!, out[pixels.length + i]!]);
}

describe("the tone-map curves the schema calls normative", () => {
  const peak = 10;
  const ramp: Array<[number, number, number]> = [0.05, 0.2, 0.5, 1, 2, 4, 7.5, 10].map((v) => [v, v, v]);

  it.each([
    ["hable", null],
    ["reinhard", 0.25],
    ["reinhard", 0.5],
    ["mobius", 0.1],
    ["mobius", 0.3],
  ])("matches ffmpeg's %s(param=%s) to float precision", async (operator, param) => {
    const filter =
      `tonemap=${operator}` + (param === null ? "" : `:param=${param}`) + `:desat=0:peak=${peak}`;
    const got = await throughFilter(ramp, filter, "bt709");
    ramp.forEach((rgb, i) => {
      expect(got[i]![0]).toBeCloseTo(mapped(operator as string, rgb[0], peak, param as number | null), 6);
    });
  }, 60_000);

  it("maps each source's peak onto exactly reference white", async () => {
    const got = await throughFilter([[peak, peak, peak]], `tonemap=hable:desat=0:peak=${peak}`, "bt709");
    expect(got[0]![0]).toBeCloseTo(1, 6);
  }, 60_000);

  it("desaturates with the WORKING SPACE's luminance vector, not a fixed one", async () => {
    // Colours far enough from grey that the two vectors disagree visibly.
    const pixels: Array<[number, number, number]> = [
      [4, 1, 0.2],
      [0.5, 0.4, 0.3],
      [8, 2, 0.5],
      [1.5, 1.5, 1.5],
    ];
    for (const [space, tag, luma] of [
      ["linear_bt709", "bt709", [0.2126, 0.7152, 0.0722]],
      ["linear_bt2020", "bt2020nc", [0.2627, 0.678, 0.0593]],
    ] as const) {
      const got = await throughFilter(pixels, `tonemap=hable:desat=2:peak=${peak}`, tag);
      pixels.forEach((rgb, i) => {
        const want = toneMapPixel(rgb, luma, "hable", null, 2, peak);
        for (let channel = 0; channel < 3; channel += 1) {
          expect(got[i]![channel]).toBeCloseTo(want[channel]!, 5);
        }
      });
      expect(space).toBeTruthy();
    }
  }, 120_000);

  it("degenerates to a white frame at reinhard param 1, which is why the bound is exclusive", async () => {
    const got = await throughFilter(ramp, `tonemap=reinhard:param=1:desat=0:peak=${peak}`, "bt709");
    for (const pixel of got) expect(pixel[0]).toBeCloseTo(1, 6);
    // And the OTHER end is the straight scale the first draft attributed to 1: as the
    // parameter approaches 0 the offset grows without bound and f(s) approaches s/peak.
    // Asserted as a RELATIVE error because it is a limit, not an equality — at 1e-4 the
    // offset is 9999 and the residual is about a hundredth of a percent.
    const nearZero = await throughFilter(ramp, `tonemap=reinhard:param=0.0001:desat=0:peak=${peak}`, "bt709");
    ramp.forEach((rgb, i) => {
      const straight = rgb[0] / peak;
      expect(Math.abs(nearZero[i]![0] - straight) / straight).toBeLessThan(1e-3);
    });
  }, 60_000);
});

describe("ColorOp.amount buys a stated number of photons", () => {
  it("applies the fused matrix in linear light, unclamped between ops", async () => {
    const ops = [
      { op: "exposure" as const, amount: 0.5, reference_clip_id: null },
      { op: "saturation" as const, amount: -0.4, reference_clip_id: null },
    ];
    const fused = fuseColorOps(ops, "linear_bt709");
    const pixels: Array<[number, number, number]> = [
      [0.18, 0.18, 0.18],
      [0.6, 0.2, 0.05],
      [0.9, 0.9, 0.2],
    ];
    const chain = colorChain(
      { working_space: "linear_bt709", output_encoding: "bt709", tone_map: null },
      { ...videoRef("x".repeat(64)), color_encoding: "bt709" },
      ops,
      FFV1_MKV,
    );
    const mixer = chain.filter.split(",").find((stage) => stage.startsWith("colorchannelmixer="));
    expect(mixer).toBeDefined();

    const got = await throughFilter(pixels, mixer!, "bt709");
    pixels.forEach((rgb, i) => {
      for (let row = 0; row < 3; row += 1) {
        const want = fused[row]![0] * rgb[0] + fused[row]![1] * rgb[1] + fused[row]![2] * rgb[2];
        expect(got[i]![row]).toBeCloseTo(want, 5);
      }
    });
    // Unclamped: +0.5 exposure on 0.9 is 1.8, and nothing here may crush it to 1.0.
    expect(Math.max(...got.map((pixel) => Math.max(...pixel)))).toBeGreaterThan(1);
  }, 60_000);

  it("gives exposure two stops and saturation a chroma scale of 1 + a", () => {
    const gain = opMatrix({ op: "exposure", amount: 1, reference_clip_id: null }, "linear_bt709");
    expect(gain[0]![0]).toBeCloseTo(4, 12);
    const grey = opMatrix({ op: "saturation", amount: -1, reference_clip_id: null }, "linear_bt709");
    // Every row becomes the luminance vector, so any input collapses to its own luma.
    expect(grey[0]).toEqual(grey[1]);
    expect(grey[1]).toEqual(grey[2]);
  });

  it("is exactly the identity at amount 0, so a zero op cannot tint anything", () => {
    for (const op of ["exposure", "saturation"] as const) {
      const matrix = opMatrix({ op, amount: 0, reference_clip_id: null }, "linear_bt2020");
      for (let i = 0; i < 3; i += 1) {
        for (let j = 0; j < 3; j += 1) expect(matrix[i]![j]).toBeCloseTo(i === j ? 1 : 0, 12);
      }
    }
  });
});

describe("the identity case is normative, not an optimisation", () => {
  function edlWith(ops: ColorOp[], colour?: EDL["color_pipeline"]): EDL {
    const items: Track["items"] = [clip("clip-01", "src-a", SOURCE_ORIGIN, 30, { color_ops: ops })];
    return makeEdl({
      mediaRefs: [videoRef(source.videoMediaId)],
      tracks: [{ track_id: "v1", kind: "video", name: "V1", role: "primary", enabled: true, items }],
      ...(colour ? { colorPipeline: colour } : {}),
    });
  }

  async function graphOf(edl: EDL): Promise<string> {
    const withProfile: EDL = { ...edl, target: { ...edl.target, encode: withoutAudio(FFV1_MKV) } };
    assertRenderable(withProfile);
    const directory = await workspace("graph");
    const sources = await resolveSources(
      withProfile,
      { [source.videoMediaId]: { paths: [source.videoPath] } },
      TOOLS,
      directory,
    );
    return buildGraph(withProfile, buildProgram(withProfile), sources, withProfile.target.encode).filter;
  }

  it("emits no colour filter at all when the source is already the delivered encoding", async () => {
    const graph = await graphOf(edlWith([]));
    expect(graph).not.toContain("zscale");
    expect(graph).not.toContain("tonemap");
    expect(graph).not.toContain("colorchannelmixer");
  }, 120_000);

  it("emits the whole chain the moment one op appears", async () => {
    const graph = await graphOf(edlWith([{ op: "exposure", amount: 0.12, reference_clip_id: null }]));
    expect(graph).toContain("zscale=tin=bt709");
    expect(graph).toContain("t=linear:p=bt709");
    expect(graph).toContain("colorchannelmixer=");
    expect(graph).toContain("zscale=t=bt709:p=bt709:m=bt709:r=tv");
  }, 120_000);

  it("converts between two SDR encodings rather than passing them through", async () => {
    const graph = await graphOf(
      edlWith([], { working_space: "linear_bt709", output_encoding: "srgb", tone_map: null }),
    );
    // sRGB and BT.709 share primaries and differ ONLY in the transfer, which is about two
    // code values in the shadows of every frame. That is exactly the difference a bare
    // "rec709" string could not express and a renderer would have skipped.
    expect(graph).toContain("zscale=tin=bt709");
    expect(graph).toContain("zscale=t=iec61966-2-1:p=bt709:m=bt709:r=tv");
  }, 120_000);

  it("tags the delivered file with what its code values mean", () => {
    expect(outputColorArgs({ working_space: "linear_bt709", output_encoding: "bt709", tone_map: null })).toEqual([
      "-colorspace",
      "bt709",
      "-color_primaries",
      "bt709",
      "-color_trc",
      "bt709",
      "-color_range",
      "tv",
    ]);
  });
});

describe("a graded render, end to end", () => {
  function gradedEdl(ops: ColorOp[]): EDL {
    const items: Track["items"] = [clip("clip-01", "src-a", SOURCE_ORIGIN, 30, { color_ops: ops })];
    return makeEdl({
      mediaRefs: [videoRef(source.videoMediaId)],
      tracks: [{ track_id: "v1", kind: "video", name: "V1", role: "primary", enabled: true, items }],
    });
  }

  async function render(edl: EDL, prefix: string, profile: EncodeProfile = FFV1_MKV) {
    return renderVideo(
      { ...edl, target: { ...edl.target, encode: withoutAudio(profile) } },
      {
        sources: { [source.videoMediaId]: { paths: [source.videoPath] } },
        workDirectory: await workspace(prefix),
        tools: TOOLS,
      },
    );
  }

  it("brightens the picture by the stated amount and stays byte-identical across runs", async () => {
    const plain = await render(gradedEdl([]), "plain");
    const lifted = gradedEdl([{ op: "exposure", amount: 0.5, reference_clip_id: null }]);
    const first = await render(lifted, "lift-a");
    const second = await render(lifted, "lift-b");

    expect((await readFile(first.path)).equals(await readFile(second.path))).toBe(true);
    // +0.5 amount is +1 stop, so the graded file is a different picture, not a re-mux.
    expect((await readFile(first.path)).equals(await readFile(plain.path))).toBe(false);
    expect(first.byteSize).toBeGreaterThan(0);
  }, 300_000);

  it("leaves the picture untouched when every op is a zero", async () => {
    const plain = await render(gradedEdl([]), "zero-plain");
    const zeroed = await render(
      gradedEdl([
        { op: "exposure", amount: 0, reference_clip_id: null },
        { op: "saturation", amount: 0, reference_clip_id: null },
      ]),
      "zero-op",
    );
    // The fused matrix is exactly the identity, so the chain is skipped and the bytes are
    // the ungraded ones. An op list that sums to nothing must not cost a round trip
    // through linear light.
    expect((await readFile(zeroed.path)).equals(await readFile(plain.path))).toBe(true);
  }, 300_000);
});

describe("an HDR source, which this worker refused outright before contracts#58", () => {
  function hlgEdl(): EDL {
    const ref = {
      ...videoRef(source.hlgMediaId),
      color_encoding: "bt2100_hlg" as const,
      source_peak_nits: 1000,
    };
    const items: Track["items"] = [clip("clip-01", "src-a", SOURCE_ORIGIN, 30)];
    return makeEdl({
      mediaRefs: [ref],
      tracks: [{ track_id: "v1", kind: "video", name: "V1", role: "primary", enabled: true, items }],
      colorPipeline: {
        working_space: "linear_bt2020",
        output_encoding: "bt709",
        tone_map: {
          operator: "hable",
          operator_param: null,
          reference_white_nits: 100,
          desaturation: 2,
        },
      },
    });
  }

  async function render(edl: EDL, prefix: string) {
    return renderVideo(
      { ...edl, target: { ...edl.target, encode: withoutAudio(FFV1_MKV) } },
      {
        sources: { [source.hlgMediaId]: { paths: [source.hlgPath] } },
        workDirectory: await workspace(prefix),
        tools: TOOLS,
      },
    );
  }

  it("renders to SDR, twice, byte-identically", async () => {
    const first = await render(hlgEdl(), "hlg-a");
    const second = await render(hlgEdl(), "hlg-b");
    expect(first.id).toBe(second.id);
    expect((await readFile(first.path)).equals(await readFile(second.path))).toBe(true);
    expect(
      first.interpretations.some((entry) => entry.convention.includes("hable tone map")),
    ).toBe(true);
  }, 300_000);

  it("refuses a plan that says the HLG file is BT.709", async () => {
    const edl = hlgEdl();
    edl.media_refs[0] = { ...edl.media_refs[0]!, color_encoding: "bt709", source_peak_nits: null };
    edl.color_pipeline = { working_space: "linear_bt709", output_encoding: "bt709", tone_map: null };
    await expect(render(edl, "hlg-lie")).rejects.toThrow(/disagree about what the code values mean/);
  }, 300_000);

  it("refuses a full-range source rather than reading its levels as limited", async () => {
    const directory = await workspace("full-range");
    const path = join(directory, "full-range.mkv");
    await run(TOOLS.ffmpeg, [
      "-nostdin",
      "-hide_banner",
      "-nostats",
      "-fflags",
      "+bitexact",
      "-flags",
      "+bitexact",
      "-i",
      source.videoPath,
      "-an",
      "-vf",
      "scale=in_range=tv:out_range=full,format=yuv420p",
      "-c:v",
      "ffv1",
      "-color_range",
      "pc",
      "-map_metadata",
      "-1",
      "-f",
      "matroska",
      "-y",
      path,
    ]);
    const mediaId = await digestFile(path);
    const items: Track["items"] = [clip("clip-01", "src-a", SOURCE_ORIGIN, 30)];
    const edl = makeEdl({
      mediaRefs: [videoRef(mediaId)],
      tracks: [{ track_id: "v1", kind: "video", name: "V1", role: "primary", enabled: true, items }],
    });
    await expect(
      renderVideo(
        { ...edl, target: { ...edl.target, encode: withoutAudio(FFV1_MKV) } },
        { sources: { [mediaId]: { paths: [path] } }, workDirectory: directory, tools: TOOLS },
      ),
    ).rejects.toThrow(/FULL-range file/);
  }, 300_000);

  it("refuses an HDR source with no tone map before it opens a file", () => {
    const edl = hlgEdl();
    edl.color_pipeline = { ...edl.color_pipeline!, tone_map: null };
    expect(() => assertRenderable(edl)).toThrow(/names no tone_map/);
  });

  it("refuses a tone map over sources that are all SDR", () => {
    const items: Track["items"] = [clip("clip-01", "src-a", SOURCE_ORIGIN, 30)];
    const edl = makeEdl({
      mediaRefs: [videoRef(source.videoMediaId)],
      tracks: [{ track_id: "v1", kind: "video", name: "V1", role: "primary", enabled: true, items }],
      colorPipeline: {
        working_space: "linear_bt709",
        output_encoding: "bt709",
        tone_map: { operator: "hable", operator_param: null, reference_white_nits: 100, desaturation: 2 },
      },
    });
    expect(() => assertRenderable(edl)).toThrow(/no source is HDR/);
  });
});
