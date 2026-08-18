import sharp from "sharp";
import { describe, expect, it } from "vitest";

import { loadAndCheckIccProfile } from "../src/icc.js";
import {
  RENDER_PRINT_PAGE_RENDERER_VERSION,
  pageCacheIdentityMatches,
  preparePageRender,
  type PageCacheIdentity,
} from "../src/page.js";
import { findTestFont, makeAlbum, sourceJpeg } from "./helpers.js";

function differentDigest(value: string): string {
  const candidate = "0".repeat(64);
  return candidate === value ? "1".repeat(64) : candidate;
}

describe("render-print page cache identity", () => {
  it("changes for the page plan, resolved source bytes, ICC profile, and DPI", async () => {
    const album = makeAlbum();
    const source = await sourceJpeg();
    const changedSource = await sharp({
      create: { width: 1200, height: 800, channels: 3, background: "#2468ac" },
    })
      .jpeg({ quality: 95 })
      .toBuffer();
    const font = await findTestFont();
    const cmyk = await loadAndCheckIccProfile(album.vendor_profile.color_profile, {
      name: "Sharp built-in CMYK",
      builtin: "cmyk",
    });
    const srgb = await loadAndCheckIccProfile(
      { ...album.vendor_profile.color_profile, icc_name: "Sharp built-in sRGB" },
      { name: "Sharp built-in sRGB", builtin: "srgb" },
    );
    const page = album.pages[0]!;
    const baseContext = {
      page,
      widthMm: 12,
      heightMm: 12,
      dpi: 300,
      dpiFloor: 300,
      icc: cmyk,
      resolvePlacementAsset: async () => source,
      resolveFont: async () => font,
    };
    const base = await preparePageRender(baseContext);
    const changedPage = structuredClone(page);
    changedPage.background = { kind: "solid", color_hex: "#000000" };
    const planChange = await preparePageRender({ ...baseContext, page: changedPage });
    const sourceChange = await preparePageRender({
      ...baseContext,
      resolvePlacementAsset: async () => changedSource,
    });
    const iccChange = await preparePageRender({ ...baseContext, icc: srgb });
    const dpiChange = await preparePageRender({ ...baseContext, dpi: 301 });

    expect(planChange.identity.pagePlanDigest).not.toBe(base.identity.pagePlanDigest);
    expect(sourceChange.identity.sourceDigests).not.toEqual(base.identity.sourceDigests);
    expect(iccChange.identity.iccDigest).not.toBe(base.identity.iccDigest);
    expect(dpiChange.identity.dpi).toBe(301);
    // These two DPIs deliberately round to the same tiny-fixture raster. The
    // DPI itself, rather than dimensions accidentally changing, invalidates it.
    expect(dpiChange.identity.widthPx).toBe(base.identity.widthPx);
    expect(dpiChange.identity.heightPx).toBe(base.identity.heightPx);
    for (const changed of [planChange, sourceChange, iccChange, dpiChange]) {
      expect(changed.identity.cacheKey).not.toBe(base.identity.cacheKey);
    }
  });

  it("matches only a complete current renderer identity", async () => {
    const album = makeAlbum();
    const source = await sourceJpeg();
    const cmyk = await loadAndCheckIccProfile(album.vendor_profile.color_profile, {
      name: "Sharp built-in CMYK",
      builtin: "cmyk",
    });
    const expected = (
      await preparePageRender({
        page: album.pages[0]!,
        widthMm: 12,
        heightMm: 12,
        dpi: 300,
        dpiFloor: 300,
        icc: cmyk,
        resolvePlacementAsset: async () => source,
        resolveFont: async () => findTestFont(),
      })
    ).identity;
    expect(expected.rendererVersion).toBe(RENDER_PRINT_PAGE_RENDERER_VERSION);
    expect(pageCacheIdentityMatches(expected, expected)).toBe(true);

    const mutations: PageCacheIdentity[] = [];
    const plan = structuredClone(expected);
    plan.pagePlanDigest = differentDigest(plan.pagePlanDigest);
    mutations.push(plan);
    const sources = structuredClone(expected);
    sources.sourceDigests[0]!.digest = differentDigest(sources.sourceDigests[0]!.digest);
    mutations.push(sources);
    const profile = structuredClone(expected);
    profile.iccDigest = differentDigest(profile.iccDigest);
    mutations.push(profile);
    const dpi = structuredClone(expected);
    dpi.dpi += 1;
    mutations.push(dpi);
    const renderer = structuredClone(expected);
    renderer.rendererVersion = "render-print-page-v1";
    mutations.push(renderer);
    const cacheKey = structuredClone(expected);
    cacheKey.cacheKey = differentDigest(cacheKey.cacheKey);
    mutations.push(cacheKey);

    for (const candidate of mutations) {
      expect(pageCacheIdentityMatches(candidate, expected)).toBe(false);
    }
  });
});
