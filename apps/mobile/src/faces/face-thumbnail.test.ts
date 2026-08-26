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

{
  const people = createFacePeopleQuery(observations, undefined, {
    "person-1": "file:///documents/face-thumbnails/person-1.jpg",
  }).getPeople();
  assert(
    people[0]?.faceThumbUri ===
      "file:///documents/face-thumbnails/person-1.jpg",
    "people projection should expose a supplied face crop URI",
  );
}

{
  const people = createFacePeopleQuery(observations).getPeople();
  assert(
    people[0]?.faceThumbUri === undefined,
    "people projection should omit faceThumbUri when no crop URI is supplied",
  );
}
