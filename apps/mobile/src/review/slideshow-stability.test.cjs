const { readFileSync } = require("node:fs");
const { resolve } = require("node:path");

function assert(value, message) {
  if (!value) throw new Error(`slideshow stability self-check failed: ${message}`);
}

/**
 * The slideshow must not rebuild its image view to change slides.
 *
 * The owner's report was "refreshes a lot, sometimes refreshes and thumbnail is
 * visible, not a smooth experience". That was not slow I/O. `PhotoPage`
 * rendered two different <Image> elements -- a full-resolution one while the
 * page was current, a thumbnail one otherwise -- carrying DIFFERENT
 * `recyclingKey` values (`slideshow-original:` vs `slideshow-adjacent:`).
 * expo-image destroys and recreates the native view whenever that key changes,
 * so every advance tore down two views, and the outgoing slide was actively
 * downgraded from a decoded photo back to a blurry thumbnail.
 *
 * Everything below pins the shape that fixed it, because the two-branch version
 * reads like a sensible memory optimisation and would be re-introduced happily.
 */
const source = readFileSync(resolve(__dirname, "Slideshow.tsx"), "utf8");

const pageStart = source.indexOf("function PhotoPage(");
assert(pageStart >= 0, "PhotoPage is discoverable");
const page = source.slice(pageStart, source.indexOf("\nfunction ", pageStart + 1));

// 1. Exactly one image element renders the slide.
const imageCount = (page.match(/<Image\b/g) ?? []).length;
assert(
  imageCount === 1,
  `PhotoPage must render exactly one <Image> (found ${imageCount}); a second one means a second native view to tear down`,
);

// 2. Its recycling key must not depend on whether the page is the current one.
const recyclingKey = page.match(/recyclingKey=\{`([^`]*)`\}/);
assert(recyclingKey, "the image must carry an explicit recyclingKey");
assert(
  !/original|adjacent|current/i.test(recyclingKey[1]),
  `recyclingKey "${recyclingKey[1]}" must not encode the page's role -- that is what forces the rebuild`,
);
assert(
  recyclingKey[1].includes("media_id"),
  "recyclingKey must identify the photo, so a different photo still gets a different view",
);

// 3. The source must not switch on isCurrent either. Same view, same source.
const sourceProp = page.match(/\n\s*source=\{([^}]*)\}/);
assert(sourceProp, "the image must have a source");
assert(
  !/isCurrent|\?/.test(sourceProp[1].replace(/\?\?/g, "")),
  `source must not branch on the page's role (got "${sourceProp[1].trim()}")`,
);

// 4. The routine source is the bounded display proxy, NOT the original. His
// library holds 25-27 MiB DSLR JPEGs and three pages mount at once; the album
// OOM came from exactly this kind of unbounded decode. `photo.uri` may remain
// only as the final fallback for a device with no native module.
assert(
  /display\s*\?\?/.test(sourceProp[1]),
  "the display proxy must be the first choice of source",
);
const fallbackIndex = sourceProp[1].indexOf("photo.uri");
assert(
  fallbackIndex === -1 || fallbackIndex > sourceProp[1].indexOf("display"),
  "photo.uri must never precede the bounded proxy in the source fallback chain",
);

// 5. The proxy edge must match the album build's, or the slideshow decodes
// afresh instead of reusing proxies that are already cached on disk.
assert(
  /const DISPLAY_EDGE = 1280;/.test(source),
  "DISPLAY_EDGE must stay 1280 to hit the ANALYSIS_PROXY_SIZE cache the build already wrote",
);

// 6. Adjacent pages must request the display photo too -- that is the prefetch.
assert(
  /const display = useDisplayPhoto\(photo\?\.media_id\)/.test(page) &&
    !/isCurrent\s*&&\s*useDisplayPhoto|useDisplayPhoto\([^)]*isCurrent/.test(page),
  "every mounted page must request its display photo, not only the current one",
);

console.log("slideshow stability self-check passed");
