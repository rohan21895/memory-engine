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

// Keyed by the PHOTO the crop came from, never by the person id -- ids are
// renumbered by every recluster. face-thumbnails.test.ts covers that rule
// directly; this case checks it survives the whole query projection.
{
  const people = createFacePeopleQuery(observations, undefined, {
    "cover-photo": "file:///documents/face-thumbnails/cover-photo.jpg",
  }).getPeople();
  assert(
    people[0]?.faceThumbUri ===
      "file:///documents/face-thumbnails/cover-photo.jpg",
    "people projection should expose a supplied face crop URI",
  );
}

// A crop filed under a person id is not a crop anybody can use.
{
  const people = createFacePeopleQuery(observations, undefined, {
    "person-1": "file:///documents/face-thumbnails/person-1.jpg",
  }).getPeople();
  assert(
    people[0]?.faceThumbUri === undefined,
    "a person-id-keyed crop must be ignored, not shown to whoever holds that id now",
  );
}

{
  const people = createFacePeopleQuery(observations).getPeople();
  assert(
    people[0]?.faceThumbUri === undefined,
    "people projection should omit faceThumbUri when no crop URI is supplied",
  );
}
