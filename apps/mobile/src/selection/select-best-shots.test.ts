import type { PickedPhoto } from "../import/picked-photo";

import { selectBestShots } from "./select-best-shots";

function assert(condition: unknown, message: string): asserts condition {
  if (!condition) {
    throw new Error(`Selection self-check failed: ${message}`);
  }
}

const photos: PickedPhoto[] = [
  photo("beach-a", "beach.jpg", 4_000, 3_000),
  photo("beach-b", "beach copy.jpg", 2_000, 1_500),
  photo("party-a", "party.jpg", 3_000, 2_000),
  photo("party-b", "party (1).jpg", 2_400, 1_600),
  photo("view-a", "mountain.jpg", 4_032, 3_024),
];

const groupCount = 3;
const requestedCount = 10;
const result = selectBestShots(photos, { count: requestedCount });
const selectedIds = new Set(result.selected.map(({ media_id }) => media_id));

assert(
  result.selected.length === Math.min(requestedCount, groupCount),
  "selected count should be capped by the number of shot groups",
);
assert(
  result.selected.every(({ alternatives }) => alternatives.length >= 0),
  "every selected photo should expose an alternatives array",
);
assert(
  result.pool.every(({ media_id }) => !selectedIds.has(media_id)),
  "a media ID must not appear in both selected and pool",
);
assert(
  result.selected.length + result.pool.length === photos.length,
  "every input photo should appear in selected or pool",
);

function photo(
  id: string,
  filename: string,
  width: number,
  height: number,
): PickedPhoto {
  return {
    id,
    uri: `file:///photos/${filename}`,
    filename,
    width,
    height,
    mimeType: "image/jpeg",
    source: "device-gallery",
  };
}
