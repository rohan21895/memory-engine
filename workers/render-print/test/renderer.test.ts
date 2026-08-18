import { mkdtemp, readFile, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import sharp from "sharp";
import { describe, expect, it } from "vitest";

import { digestBytes } from "../src/digest.js";
import { loadAndCheckIccProfile } from "../src/icc.js";
import { preparePageRender } from "../src/page.js";
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

  it("resumes from a verified page artifact after authenticating its source without rerendering", async () => {
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
    expect(firstArtifact?.identity).toMatchObject({
      cacheVersion: 2,
      rendererVersion: "render-print-page-v2",
      dpi: 300,
      colorSpace: "cmyk",
      components: 4,
    });
    const pageMetadata = await sharp(firstArtifact!.path).metadata();
    // `hasProfile` was false here only because the old raw round-trip in
    // renderPage discarded the profile on its way through an untyped buffer --
    // the same round-trip that sheared the page. A cached page raster is
    // resumed from on a later run, and four unlabelled ink bands are ambiguous;
    // carrying the profile it was rendered in is the state that survives a
    // restart truthfully.
    expect(pageMetadata).toMatchObject({ space: "cmyk", channels: 4, hasProfile: true });

    let sourceReads = 0;
    const completedPages: number[] = [];
    const resumed = await renderAlbum(makeAlbum(), {
      iccProfile: { name: "Sharp built-in CMYK", builtin: "cmyk" },
      resolvePlacementAsset: async () => {
        sourceReads += 1;
        return source;
      },
      resolveFont: async () => font,
      pageStoreDirectory: directory,
      resumedPages: new Map([[0, firstArtifact!]]),
      onPageComplete: (artifact) => {
        completedPages.push(artifact.index);
      },
    });
    expect(sourceReads).toBe(1);
    expect(completedPages).not.toContain(0);
    expect(resumed.pages[0]).toEqual(firstArtifact);
    expect(resumed.pages).toHaveLength(20);
  }, 30_000);

  it("invalidates pre-fix, wrong-channel, and wrong-profile page artifacts", async () => {
    const directory = await mkdtemp(join(tmpdir(), "render-print-old-cache-"));
    const album = makeAlbum();
    const source = await sourceJpeg();
    const font = await findTestFont();
    const icc = await loadAndCheckIccProfile(album.vendor_profile.color_profile, {
      name: "Sharp built-in CMYK",
      builtin: "cmyk",
    });
    const rgbPage = await sharp({
      create: { width: 142, height: 142, channels: 3, background: "#dd4400" },
    })
      .jpeg()
      .toBuffer();
    const unprofiledCmykPage = await sharp({
      create: { width: 142, height: 142, channels: 3, background: "#dd4400" },
    })
      .toColourspace("cmyk")
      .jpeg()
      .toBuffer();
    await expect(sharp(unprofiledCmykPage).metadata()).resolves.toMatchObject({
      space: "cmyk",
      channels: 4,
      hasProfile: false,
    });
    const oldPath = join(directory, "pre-fix-rgb-page.jpg");
    const forgedRgbPath = join(directory, "current-identity-rgb-page.jpg");
    const unprofiledPath = join(directory, "current-identity-unprofiled-cmyk-page.jpg");
    await writeFile(oldPath, rgbPage);
    await writeFile(forgedRgbPath, rgbPage);
    await writeFile(unprofiledPath, unprofiledCmykPage);
    const baseContext = {
      widthMm: 12,
      heightMm: 12,
      dpi: 300,
      dpiFloor: 300,
      icc,
      resolvePlacementAsset: async () => source,
      resolveFont: async () => font,
    };
    const page2Identity = (await preparePageRender({ ...baseContext, page: album.pages[2]! })).identity;
    const page3Identity = (await preparePageRender({ ...baseContext, page: album.pages[3]! })).identity;
    const oldArtifact: PageArtifact = { index: 0, id: digestBytes(rgbPage), path: oldPath };
    const wrongProfileArtifact: PageArtifact = {
      index: 2,
      id: digestBytes(unprofiledCmykPage),
      path: unprofiledPath,
      identity: page2Identity,
    };
    const wrongChannelArtifact: PageArtifact = {
      index: 3,
      id: digestBytes(rgbPage),
      path: forgedRgbPath,
      identity: page3Identity,
    };
    let sourceReads = 0;
    const completedPages: number[] = [];

    const result = await renderAlbum(album, {
      iccProfile: { name: "Sharp built-in CMYK", builtin: "cmyk" },
      resolvePlacementAsset: async () => {
        sourceReads += 1;
        return source;
      },
      resolveFont: async () => font,
      pageStoreDirectory: directory,
      resumedPages: new Map([
        [0, oldArtifact],
        [2, wrongProfileArtifact],
        [3, wrongChannelArtifact],
      ]),
      onPageComplete: (artifact) => {
        completedPages.push(artifact.index);
      },
    });

    expect(sourceReads).toBe(1);
    expect(completedPages).toEqual(expect.arrayContaining([0, 2, 3]));
    expect(result.pages[0]?.identity?.rendererVersion).toBe("render-print-page-v2");
    for (const index of [0, 2, 3]) {
      await expect(sharp(result.pages[index]!.path).metadata()).resolves.toMatchObject({
        space: "cmyk",
        channels: 4,
        hasProfile: true,
      });
    }
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
