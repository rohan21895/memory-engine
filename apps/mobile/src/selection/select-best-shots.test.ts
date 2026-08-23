import type { PickedPhoto } from "../import/picked-photo";

import { selectBestShots } from "./select-best-shots";

type AnalyzedPhoto = PickedPhoto & {
  embedding: number[];
  faces: number;
};

function assert(condition: unknown, message: string): asserts condition {
  if (!condition) {
    throw new Error(`Selection self-check failed: ${message}`);
  }
}

const nearDuplicateA = basisEmbedding(0);
const nearDuplicateB = [...nearDuplicateA];
nearDuplicateB[0] = 0.99;
nearDuplicateB[1] = 0.1;

const duplicateResult = selectBestShots(
  [
    photo("burst-a", nearDuplicateA),
    photo("burst-b", nearDuplicateB),
  ],
  { count: 10 },
);

assert(
  duplicateResult.selected.length === 1,
  "high-similarity frames should collapse to one selected take",
);
assert(
  duplicateResult.selected[0].alternatives.some(
    ({ media_id }) => media_id === "burst-b",
  ),
  "the non-selected near-duplicate should be offered as an alternative",
);
assert(
  duplicateResult.pool.some(({ media_id }) => media_id === "burst-b"),
  "an offered alternative should also remain in the pool",
);

const distinctPhotos = [
  photo("distinct-a", basisEmbedding(0)),
  photo("distinct-b", basisEmbedding(1)),
  photo("distinct-c", basisEmbedding(2)),
];
const distinctResult = selectBestShots(distinctPhotos, { count: 3 });

assert(
  distinctResult.selected.length === distinctPhotos.length,
  "fully distinct embeddings should all be kept when count allows",
);
assert(
  distinctResult.pool.length === 0,
  "fully selected distinct frames should leave an empty pool",
);

const cappedResult = selectBestShots(distinctPhotos, { count: 2 });
assert(cappedResult.selected.length === 2, "selection must respect count");
assert(
  cappedResult.selected.every(({ page }, index) => page === index + 1),
  "selected pages should be one-based and sequential",
);
assert(
  cappedResult.pool.length === 1,
  "frames beyond count should remain available in the pool",
);

const emptyResult = selectBestShots([], { count: 5 });
assert(emptyResult.selected.length === 0, "empty input should select nothing");
assert(emptyResult.pool.length === 0, "empty input should have an empty pool");

function basisEmbedding(position: number): number[] {
  return Array.from({ length: 64 }, (_, index) =>
    index === position ? 1 : 0,
  );
}

function photo(id: string, embedding: number[]): AnalyzedPhoto {
  return {
    id,
    uri: `file:///photos/${id}.jpg`,
    filename: `${id}.jpg`,
    width: 4_000,
    height: 3_000,
    mimeType: "image/jpeg",
    source: "device-gallery",
    embedding,
    faces: 0,
  };
}
