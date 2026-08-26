// @ts-expect-error Node's TypeScript runner requires the source extension.
import { createFacePeopleQuery } from "./face-index.ts";

function assert(condition: unknown, message: string): asserts condition {
  if (!condition) {
    throw new Error(message);
  }
}

const observations = [
  {
    assetId: "cover-photo",
    embedding: [1, 0],
    embeddingKind: "identity" as const,
  },
];

// A person built from scratch has no saved crop, and the projection must say so
// rather than inventing one. face-thumbnails.test.ts covers where a crop DOES
// come from; this checks the empty case survives the whole query projection.
{
  const people = createFacePeopleQuery(observations).getPeople();
  assert(
    people[0]?.faceThumbUri === undefined,
    "people projection should omit faceThumbUri when the person has no crop",
  );
  assert(
    people[0]?.coverAssetId === "cover-photo",
    "and should fall back to the person's own photo",
  );
}

console.log("face thumbnail projection self-check passed");
