// @ts-expect-error TypeScript bundler resolution normally omits source extensions.
import { ALBUM_DOCUMENT_HEIGHT, ALBUM_DOCUMENT_WIDTH, buildAlbumDocument } from "./album-document.ts";

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
  galleries.every((page) => new Set(page.placements.map(({ frame }) => `${frame.width}:${frame.height}`)).size > 1),
  "gallery pages use mixed frame sizes",
);

for (const page of galleries) {
  for (let leftIndex = 0; leftIndex < page.placements.length; leftIndex += 1) {
    const left = page.placements[leftIndex]!.frame;
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

// eslint-disable-next-line no-console
console.log("album-document self-check passed");
