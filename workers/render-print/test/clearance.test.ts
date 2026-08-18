/**
 * The print boundary refuses to emit a PDF without a complete clearance.
 *
 * Two things are proved here and they are different.
 *
 * 1. CROSS-LANGUAGE AGREEMENT. This TypeScript implementation reproduces the
 *    exact pre-image bytes and digests in `contracts/vectors/
 *    safety-clearance-manifest-id.json`, which Python wrote. A digest mismatch
 *    would say only that something diverged; checking the pre-image names the
 *    field that did. Without this, the two sides would disagree in production
 *    on some manifest nobody thought to try, and the visible symptom would be a
 *    gate refusing correct output -- which is how gates get switched off.
 *
 * 2. NO FILE IS WRITTEN. Every refusal below asserts ENOENT on the output path,
 *    not merely that the job failed. "It threw" and "no bytes reached the disk"
 *    are different claims, and only the second one is the property.
 */
import { access, mkdtemp, readFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { bytesToHex } from "@noble/hashes/utils.js";
import { describe, expect, it } from "vitest";

import {
  canonicalJson,
  guardPrint,
  manifestId,
  manifestIdPreimage,
  PublicationBlocked,
} from "../src/clearance.js";
import { publicationMediaIds, runRenderPrintJob } from "../src/job.js";
import { findTestFont, makeAlbum, makeClearance, makeJob, sourceJpeg, HASH_B, PROXY_B } from "./helpers.js";

const REPO_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "../../..");

async function baseJob(overrides: { clearance?: unknown } = {}) {
  const directory = await mkdtemp(join(tmpdir(), "render-print-clearance-"));
  const sourcePath = join(directory, "source.jpg");
  const { writeFile } = await import("node:fs/promises");
  await writeFile(sourcePath, await sourceJpeg());
  const params = {
    output_path: join(directory, "album.pdf"),
    work_directory: join(directory, "work"),
    icc_profile: { name: "Sharp built-in CMYK", builtin: "cmyk" },
    asset_paths: { [HASH_B]: sourcePath },
    font_paths: { "Test Font": await findTestFont() },
    safety_clearance:
      "clearance" in overrides ? overrides.clearance : makeClearance(),
  };
  return { params, job: makeJob(params) };
}

async function expectRefused(clearance: unknown, code: string) {
  const { params, job } = await baseJob({ clearance });
  const failed = await runRenderPrintJob(job, makeAlbum(), { persist: async () => undefined });
  expect(failed.state.status).toBe("failed");
  expect(failed.error?.code).toBe("validation_failed");
  expect(failed.error?.retryable).toBe(false);
  expect(failed.error?.message).toContain(code);
  await expect(access(params.output_path)).rejects.toMatchObject({ code: "ENOENT" });
}

describe("manifest_id agrees with the Python implementation", () => {
  it("reproduces every committed vector, pre-image bytes included", async () => {
    const raw = await readFile(
      join(REPO_ROOT, "contracts/vectors/safety-clearance-manifest-id.json"),
      "utf8",
    );
    const table = JSON.parse(raw) as {
      vectors: { name: string; manifest: Record<string, unknown>; preimage_utf8_hex: string; manifest_id: string }[];
    };
    expect(table.vectors.length).toBeGreaterThan(0);
    for (const vector of table.vectors) {
      expect(
        bytesToHex(manifestIdPreimage(vector.manifest)),
        `pre-image bytes diverge for ${vector.name}`,
      ).toBe(vector.preimage_utf8_hex);
      expect(manifestId(vector.manifest), `digest diverges for ${vector.name}`).toBe(
        vector.manifest_id,
      );
    }
  });

  it("writes 1.0 as 1, the difference that has broken a digest here before", () => {
    expect(canonicalJson({ a: 1.0, b: 0.3, c: null })).toBe('{"a":1,"b":0.3,"c":null}');
  });

  it("sorts keys by code unit rather than by locale", () => {
    // `localeCompare` puts "a" before "B"; RFC 8785 does not.
    expect(canonicalJson({ B: 1, a: 2 })).toBe('{"B":1,"a":2}');
  });

  it("refuses exponent notation instead of writing a form the two pad differently", () => {
    expect(() => canonicalJson({ tiny: 1e-7 })).toThrow(/exponent/);
  });
});

describe("the print worker will not emit a PDF without a clearance", () => {
  it("emits one when the clearance is complete and correct", async () => {
    const { params, job } = await baseJob();
    const completed = await runRenderPrintJob(job, makeAlbum(), { persist: async () => undefined });
    expect(completed.state.status).toBe("completed");
    await expect(access(params.output_path)).resolves.toBeUndefined();
  }, 30_000);

  it("refuses when no clearance was presented at all", async () => {
    await expectRefused(undefined, "clearance_missing");
  });

  it("refuses when the only item is indeterminate", async () => {
    await expectRefused(
      makeClearance({
        items: [
          {
            media_id: HASH_B,
            evidence_id: PROXY_B,
            verdict: "indeterminate",
            scores: null,
            indeterminate_reason: "load_gate_denied",
            override: null,
          },
        ],
      }),
      "indeterminate_item",
    );
  });

  it("refuses an indeterminate item that someone tried to override", async () => {
    await expectRefused(
      makeClearance({
        items: [
          {
            media_id: HASH_B,
            evidence_id: PROXY_B,
            verdict: "indeterminate",
            scores: null,
            indeterminate_reason: "model_unavailable",
            override: {
              decided_at: "2026-08-18T09:05:00+05:30",
              decided_by: "rohan",
              scope: "item_and_sink",
              note: "looks fine to me",
            },
          },
        ],
      }),
      "override_on_indeterminate",
    );
  });

  it("refuses a clearance issued for a different sink", async () => {
    await expectRefused(makeClearance({ sink: "share" }), "sink_mismatch");
  });

  it("refuses a clearance about a different photograph", async () => {
    await expectRefused(
      makeClearance({
        items: [
          {
            media_id: "9".repeat(64),
            evidence_id: PROXY_B,
            verdict: "cleared",
            scores: { explicit: 0.01, suggestive: 0.01, medical_or_artistic: 0.01 },
          },
        ],
      }),
      "item_set_mismatch",
    );
  });

  it("refuses a transposed class order", async () => {
    const clearance = makeClearance() as Record<string, unknown>;
    (clearance.classifier as Record<string, unknown>).class_order = [
      "suggestive",
      "explicit",
      "medical_or_artistic",
    ];
    delete clearance.manifest_id;
    clearance.manifest_id = manifestId(clearance);
    await expectRefused(clearance, "class_order_mismatch");
  });

  it("refuses verdicts produced by a development-mode host", async () => {
    const clearance = makeClearance() as Record<string, unknown>;
    (clearance.classifier as Record<string, unknown>).load_mode = "development";
    delete clearance.manifest_id;
    clearance.manifest_id = manifestId(clearance);
    await expectRefused(clearance, "development_load_mode");
  });

  it("refuses a manifest edited after it was signed", async () => {
    const clearance = makeClearance() as Record<string, unknown>;
    clearance.sink_detail = "a different vendor entirely";
    await expectRefused(clearance, "manifest_id_mismatch");
  });

  it("refuses a blocked item with nobody's name on the override", async () => {
    await expectRefused(
      makeClearance({
        items: [
          {
            media_id: HASH_B,
            evidence_id: PROXY_B,
            verdict: "blocked",
            scores: { explicit: 0.62, suggestive: 0.10, medical_or_artistic: 0.02 },
            override: {
              decided_at: "2026-08-18T09:05:00+05:30",
              decided_by: "   ",
              scope: "item_and_sink",
              note: null,
            },
          },
        ],
      }),
      "override_unattributed",
    );
  });

  it("emits when a human has taken responsibility for a positive result", async () => {
    const { params, job } = await baseJob({
      clearance: makeClearance({
        items: [
          {
            media_id: HASH_B,
            evidence_id: PROXY_B,
            verdict: "blocked",
            scores: { explicit: 0.05, suggestive: 0.12, medical_or_artistic: 0.71 },
            override: {
              decided_at: "2026-08-18T09:05:00+05:30",
              decided_by: "rohan",
              scope: "item_and_sink",
              note: "our own photograph, in our own book",
            },
          },
        ],
      }),
    });
    const completed = await runRenderPrintJob(job, makeAlbum(), { persist: async () => undefined });
    expect(completed.state.status).toBe("completed");
    await expect(access(params.output_path)).resolves.toBeUndefined();
  }, 30_000);
});

describe("the verifier itself", () => {
  it("checks the ids the worker is about to publish, taken from the AlbumSpec", () => {
    expect(publicationMediaIds(makeAlbum())).toEqual([HASH_B]);
  });

  it("denies rather than propagating an internal fault", () => {
    const hostile = new Proxy(
      {},
      {
        get() {
          throw new Error("boom");
        },
        has() {
          return true;
        },
      },
    );
    expect(() => guardPrint(hostile, { mediaIds: [HASH_B] })).toThrow(PublicationBlocked);
    try {
      guardPrint(hostile, { mediaIds: [HASH_B] });
    } catch (error) {
      expect((error as PublicationBlocked).code).toBe("verifier_exception");
    }
  });

  it("treats a regenerated proxy as stale evidence", () => {
    expect(() =>
      guardPrint(makeClearance(), {
        mediaIds: [HASH_B],
        evidenceIds: { [HASH_B]: "e".repeat(64) },
      }),
    ).toThrow(/evidence_stale/);
  });

  it("accepts the matching proxy", () => {
    const clearance = guardPrint(makeClearance(), {
      mediaIds: [HASH_B],
      evidenceIds: { [HASH_B]: PROXY_B },
    });
    expect(clearance.sink).toBe("print");
    expect(clearance.mediaIds).toEqual([HASH_B]);
  });
});
