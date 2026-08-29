// @ts-expect-error The Expo app deliberately does not ship Node type declarations.
import { readFileSync } from "node:fs";

// @ts-expect-error Node's native TypeScript runner requires the extension.
import { aspectRatioOf, balanceIntoColumns } from "./album-wall.ts";

function assert(value: unknown, message: string): asserts value {
  if (!value) throw new Error(`album wall self-check failed: ${message}`);
}

/**
 * Album photos must never be cropped to fit a frame.
 *
 * The owner's report: "In albums images are cut to fit in required size, that
 * is really bad." Two places did it, for the same reason -- a fixed box filled
 * with a centre-crop:
 *   - the in-app album grid: square tiles, `contentFit="cover"`
 *   - the PDF renderer: `drawCover`, which literally selected a sub-Rect of the
 *     source bitmap and discarded the rest
 * In family photos what a centre-crop discards is heads and feet.
 */

// --- 1. Ratios ---------------------------------------------------------------

assert(aspectRatioOf({ width: 4000, height: 3000 }) === 4 / 3, "a landscape photo keeps its ratio");
assert(aspectRatioOf({ width: 3000, height: 4000 }) === 3 / 4, "a portrait photo keeps its ratio");
// Sources do not always report dimensions; square is the only safe guess, and
// it is exactly why the tile must letterbox rather than crop.
assert(aspectRatioOf({}) === 1, "a photo with no dimensions reads square");
assert(aspectRatioOf({ width: 0, height: 0 }) === 1, "zero dimensions read square, not NaN");
assert(aspectRatioOf({ width: 100, height: 0 }) === 1, "a zero divisor cannot produce Infinity");
// One 10:1 panorama should not flatten every other tile in its column.
assert(aspectRatioOf({ width: 10000, height: 1000 }) === 3, "extreme ratios clamp wide");
assert(aspectRatioOf({ width: 1000, height: 10000 }) === 1 / 3, "extreme ratios clamp tall");

// --- 2. Balance --------------------------------------------------------------

const square = { width: 1000, height: 1000 };
const columns = balanceIntoColumns(Array.from({ length: 8 }, () => square), 2);
assert(columns.length === 2, "it produces the requested number of columns");
assert(
  columns[0].items.length === 4 && columns[1].items.length === 4,
  "eight identical photos split evenly",
);

// A tall photo is worth three squares of height, so the greedy pass must send
// the next several photos to the other column rather than alternating blindly.
const tall = { width: 1000, height: 3000 };
const mixed = balanceIntoColumns([tall, square, square, square], 2);
const heights = mixed.map((column) => column.height);
assert(
  Math.abs(heights[0] - heights[1]) <= 1,
  `columns must stay within one square of each other (got ${heights.join(" vs ")})`,
);
assert(
  mixed[0].items.length === 1 && mixed[1].items.length === 3,
  "the tall photo owns its column while the squares fill the other",
);

// Every photo must appear exactly once -- a decorative layout that silently
// drops a photo is worse than the crop it replaced.
const many = Array.from({ length: 37 }, (_, index) => ({ width: 100 + index, height: 100 }));
const placed = balanceIntoColumns(many, 3).flatMap((column) => column.items);
assert(placed.length === 37, `all photos must be placed (got ${placed.length})`);
assert(new Set(placed).size === 37, "no photo may be duplicated across columns");

assert(balanceIntoColumns([square], 0).length === 1, "a nonsense column count still yields one column");

// VACUITY: the balance assertion above must be capable of failing. Round-robin
// would pass the eight-squares case and fail the mixed one, which is the point.
const roundRobinHeights = [1 / aspectRatioOf(tall) + 1, 2];
assert(
  Math.abs(roundRobinHeights[0] - roundRobinHeights[1]) > 1,
  "VACUITY: naive alternation really would unbalance the mixed case",
);

// --- 3. The two crop sites stay closed. --------------------------------------

const detail = readFileSync(new URL("./AlbumDetailScreen.tsx", import.meta.url), "utf8");
const tileStyle = detail.match(/\n\s*tile: \{[^}]*\}/)?.[0] ?? "";
assert(
  !/aspectRatio:\s*1\b/.test(tileStyle),
  "the album tile must not hard-code a square; its ratio comes from the photo",
);
assert(
  detail.includes("aspectRatio: aspectRatioOf(photo)"),
  "each tile must take its own photo's aspect ratio",
);
const tileImage = detail.match(/<Image[^>]*style=\{\[styles\.tile[^>]*>/s)?.[0] ?? "";
assert(
  tileImage.includes('contentFit="contain"'),
  "album tiles must letterbox, never centre-crop -- dimensions are not guaranteed",
);

const pdf = readFileSync(
  new URL(
    "../../modules/photeo-album-pdf/android/src/main/java/expo/modules/photeoalbumpdf/PhoteoAlbumPdfModule.kt",
    import.meta.url,
  ),
  "utf8",
);
assert(
  !/fun drawCover\b/.test(pdf) && /fun drawWhole\b/.test(pdf),
  "the PDF renderer must fit the whole photo, not cover its frame",
);
// The crop was a source sub-Rect. A null source rect is what proves the entire
// bitmap is drawn; re-introducing a Rect(...) source is the regression.
assert(
  /canvas\.drawBitmap\(bitmap, null, placed, paint\)/.test(pdf),
  "the PDF must draw the whole bitmap, with no source sub-rectangle",
);

console.log("album wall self-check passed");
