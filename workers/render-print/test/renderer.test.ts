import { mkdtemp, readFile, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import sharp from "sharp";
import { describe, expect, it } from "vitest";

import { renderAlbum, writePdfOnce, type PageArtifact } from "../src/renderer.js";
import { findTestFont, makeAlbum, sourceJpeg } from "./helpers.js";

describe("deterministic print rendering", () => {
  it("produces byte-identical CMYK PDF/X-4 output with physical page boxes and ICC bytes", async () => {
    const source = await sourceJpeg();
    const font = await findTestFont();
    const options = {
      iccProfile: { name: "Sharp built-in CMYK", builtin: "cmyk" as const },
      resolvePlacementAsset: async () => source,
      resolveFont: async () => font,
    };
    const first = await renderAlbum(makeAlbum(), options);
    const second = await renderAlbum(makeAlbum(), options);

    expect(first.id).toBe(second.id);
    expect(first.pdf.equals(second.pdf)).toBe(true);
    expect(first.colorSpace).toBe("cmyk");
    expect(first.pdf.subarray(0, 8).toString("ascii")).toBe("%PDF-1.6");
    const pdfText = first.pdf.toString("latin1");
    expect(pdfText).toContain("/S /GTS_PDFX");
    expect(pdfText).toContain("/GTS_PDFXVersion (PDF/X-4)");
    expect(pdfText).toContain("/Count 20");
    expect(pdfText).toContain("/BleedBox [0 0 34.015748 34.015748]");
    expect(pdfText).toContain("/TrimBox [2.834646 2.834646 31.181102 31.181102]");
    expect(pdfText).toContain("/N 4 /Alternate /DeviceCMYK");
    expect(first.pdf.includes(Buffer.from("acsp", "ascii"))).toBe(true);
  }, 30_000);

  it("resumes from a verified page artifact without reading its source again", async () => {
    const directory = await mkdtemp(join(tmpdir(), "render-print-resume-"));
    const source = await sourceJpeg();
    const font = await findTestFont();
    let firstArtifact: PageArtifact | undefined;

    await expect(
      renderAlbum(makeAlbum(), {
        iccProfile: { name: "Sharp built-in CMYK", builtin: "cmyk" },
        resolvePlacementAsset: async () => source,
        resolveFont: async () => font,
        pageStoreDirectory: directory,
        onPageComplete: (artifact) => {
          firstArtifact = artifact;
          throw new Error("simulated process death");
        },
      }),
    ).rejects.toThrow(/simulated process death/);
    expect(firstArtifact?.index).toBe(0);
    const pageMetadata = await sharp(firstArtifact!.path).metadata();
    expect(pageMetadata).toMatchObject({ space: "cmyk", channels: 4, hasProfile: false });

    let sourceReads = 0;
    const resumed = await renderAlbum(makeAlbum(), {
      iccProfile: { name: "Sharp built-in CMYK", builtin: "cmyk" },
      resolvePlacementAsset: async () => {
        sourceReads += 1;
        return source;
      },
      resolveFont: async () => font,
      pageStoreDirectory: directory,
      resumedPages: new Map([[0, firstArtifact!]]),
    });
    expect(sourceReads).toBe(0);
    expect(resumed.pages).toHaveLength(20);
  }, 30_000);

  it("publishes atomically, accepts an identical replay, and refuses overwrite", async () => {
    const directory = await mkdtemp(join(tmpdir(), "render-print-output-"));
    const result = await renderAlbum(makeAlbum(), {
      iccProfile: { name: "Sharp built-in CMYK", builtin: "cmyk" },
      resolvePlacementAsset: async () => sourceJpeg(),
      resolveFont: async () => findTestFont(),
    });
    const output = join(directory, "album.pdf");
    await writePdfOnce(output, result);
    await writePdfOnce(output, result);
    expect((await readFile(output)).equals(result.pdf)).toBe(true);

    await writeFile(output, "different");
    await expect(writePdfOnce(output, result)).rejects.toThrow(/different bytes/);
  }, 30_000);
});
