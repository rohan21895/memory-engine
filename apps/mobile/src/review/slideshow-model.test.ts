// @ts-expect-error TypeScript bundler resolution normally omits source extensions.
import { adjacentPages, photoIndexForPage, quantizedThumbnailSize } from "./slideshow-model.ts";

function assert(value: unknown, message: string): asserts value {
  if (!value) throw new Error(`slideshow-model self-check failed: ${message}`);
}

assert(quantizedThumbnailSize(54) === 128, "filmstrip thumbnails use a shared 128px bucket");
assert(quantizedThumbnailSize(393) === 768, "stage thumbnails are 2x and quantised to 64px");
assert(quantizedThumbnailSize(900) === 1024, "native thumbnail requests stay inside its upper bound");

assert(photoIndexForPage(4, 4) === 0, "autoplay wraps from the last photo to the first");
assert(photoIndexForPage(-1, 4) === 3, "previous wraps from the first photo to the last");
assert(photoIndexForPage(3, 0) === 0, "an empty album has a harmless index");

assert(adjacentPages(8, 20).join(",") === "7,8,9", "only current and adjacent pages mount");
assert(adjacentPages(8, 1).join(",") === "8", "a one-photo album mounts one page");
assert(adjacentPages(8, 0).length === 0, "an empty album mounts no pages");

// eslint-disable-next-line no-console
console.log("slideshow-model self-check passed");
