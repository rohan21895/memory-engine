// @ts-expect-error Node self-checks require the extension; Metro resolves it too.
import { ALBUM_BLEED_POINTS, ALBUM_DOCUMENT_FORMAT, ALBUM_DOCUMENT_HEIGHT, ALBUM_DOCUMENT_RASTER_SIZE, ALBUM_DOCUMENT_WIDTH, ALBUM_SAFE_MARGIN_POINTS, ALBUM_TARGET_DPI, ALBUM_TRIM_RASTER_SIZE, ALBUM_TRIM_SIZE_POINTS, buildAlbumDocument } from "./album-document.ts";

function assert(value: unknown, message: string): asserts value {
  if (!value) throw new Error(`album-document self-check failed: ${message}`);
}

const photos = Array.from({ length: 18 }, (_, index) => ({
  height: index % 3 === 0 ? 1_600 : 900,
  media_id: `photo-${index}`,
  page: index + 1,
  uri: `content://photo/${index}`,
  width: index % 3 === 0 ? 900 : 1_600,
}));

const document = buildAlbumDocument(photos);
const used = document.pages.flatMap((page) => page.placements.map((placement) => placement.mediaId));
const breathers = document.pages.filter((page) => page.kind === "breather");
const galleries = document.pages.filter((page) => page.kind === "gallery");

assert(document.pageWidth === ALBUM_DOCUMENT_WIDTH, "page width is explicit");
assert(document.pageHeight === ALBUM_DOCUMENT_HEIGHT, "page height is explicit");
assert(ALBUM_DOCUMENT_FORMAT === "photeo-album-8x8-v2", "the old geometry cannot reuse this format cache");
assert(document.pageWidth === 593 && document.pageHeight === 593, "the media box is 593pt square");
assert(document.trimBox.width === 576 && document.trimBox.height === 576, "the trim is 8 inches square");
assert(
  document.trimBox.x === ALBUM_BLEED_POINTS && document.trimBox.y === ALBUM_BLEED_POINTS,
  "the trim is inset by 3mm bleed",
);
assert(document.bleed === 8.5, "the bleed is 8.5pt on every side");
assert(document.safeMargin === 36, "the safe margin is 0.5 inches inside trim");
assert(
  document.rasterWidth === 2_400 && document.rasterHeight === 2_400 &&
    ALBUM_TRIM_RASTER_SIZE === 2_400,
  "trim content is rasterized at 300 DPI",
);
assert(ALBUM_DOCUMENT_RASTER_SIZE === 2_471, "the media raster carries 300 DPI through the bleed");
assert(used.length === photos.length, "every photo appears once");
assert(new Set(used).size === photos.length, "no photo is repeated");
assert(breathers.length >= 2, "a longer album gets occasional breathers");
assert(
  breathers.every((page) => {
    const frame = page.placements[0]?.frame;
    return frame?.x === 0 && frame.y === 0 && frame.width === ALBUM_DOCUMENT_WIDTH && frame.height === ALBUM_DOCUMENT_HEIGHT;
  }),
  "breathers fill the page",
);
assert(galleries.every((page) => page.placements.every((placement) => placement.mat > 0)), "gallery photos are matted");
assert(
  document.pages.every((page) => page.placements.every((placement) => placement.effectiveDpi !== null)),
  "every photo with known dimensions records effective DPI",
);
assert(
  document.pages.some((page) => page.placements.some((placement) => (placement.effectiveDpi ?? 300) < 300)),
  "an undersized source is recorded below 300 DPI instead of being silently represented as 300 DPI",
);
assert(
  galleries.every((page) => new Set(page.placements.map(({ frame }) => `${frame.width}:${frame.height}`)).size > 1),
  "gallery pages use mixed frame sizes",
);

for (const page of galleries) {
  for (let leftIndex = 0; leftIndex < page.placements.length; leftIndex += 1) {
    const left = page.placements[leftIndex]!.frame;
    const safeStart = ALBUM_BLEED_POINTS + ALBUM_SAFE_MARGIN_POINTS;
    const safeEnd = ALBUM_BLEED_POINTS + ALBUM_TRIM_SIZE_POINTS - ALBUM_SAFE_MARGIN_POINTS;
    assert(
      left.x >= safeStart && left.y >= safeStart &&
        left.x + left.width <= safeEnd && left.y + left.height <= safeEnd,
      `frame ${leftIndex} stays inside the trim safe area`,
    );
    for (let rightIndex = leftIndex + 1; rightIndex < page.placements.length; rightIndex += 1) {
      const right = page.placements[rightIndex]!.frame;
      const overlaps = !(
        left.x + left.width <= right.x ||
        right.x + right.width <= left.x ||
        left.y + left.height <= right.y ||
        right.y + right.height <= left.y
      );
      assert(!overlaps, `frames ${leftIndex} and ${rightIndex} do not overlap`);
    }
  }
}

const fullBleedAt300 = buildAlbumDocument([{
  height: 2_471,
  media_id: "full-bleed-300",
  page: 1,
  uri: "content://photo/full-bleed-300",
  width: 2_471,
}]).pages[0]?.placements[0];
assert(
  (fullBleedAt300?.effectiveDpi ?? 0) >= 300,
  "a 2471px source records at least 300 DPI across the media box including bleed",
);

const fullBleedAboveTarget = buildAlbumDocument([{
  height: 6_000,
  media_id: "full-bleed-above-target",
  page: 1,
  uri: "content://photo/full-bleed-above-target",
  width: 6_000,
}]).pages[0]?.placements[0];
assert(
  fullBleedAboveTarget?.effectiveDpi === ALBUM_TARGET_DPI,
  "the plan does not promise more resolution than its 300-DPI raster target",
);

// eslint-disable-next-line no-console
console.log("album-document self-check passed");
