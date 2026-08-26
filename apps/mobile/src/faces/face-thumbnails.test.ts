import {
  isAvatarFace,
  summariesForPeople,
  // @ts-expect-error Node's TypeScript runner requires the source extension.
} from "./face-index.ts";
import {
  clusterFaces,
  mergeExistingPeople,
  // @ts-expect-error Node's TypeScript runner requires the source extension.
} from "./face-cluster.ts";
import type { Person } from "./types.ts";

function assert(value: unknown, message: string): asserts value {
  if (!value) throw new Error(`face thumbnail self-check failed: ${message}`);
}

/**
 * The bug this file exists for.
 *
 * Face crops used to live in a map keyed by PERSON ID. Person ids are not
 * stable: `rebuildPeople` renumbers every person from `person-1` in observation
 * order, so the first recluster handed person-1's avatar to whoever became
 * person-1 next. On the owner's real library 2,081 crops were stored and 2,066
 * still matched a live id, which is what made it invisible -- the grid looked
 * fully populated and was showing strangers. His words: "thumbnails and actual
 * photos of person don't match, what kind of sorting is this".
 *
 * The fix is structural rather than a better key: the avatar rides ON the
 * person record, so it cannot outlive the person it describes. Every case here
 * is really one question -- can a rebuild ever put someone else's face on a
 * tile?
 */

const person = (
  id: string,
  assetIds: string[],
  faceCount = assetIds.length,
  avatar?: { avatarUri: string; avatarAssetId: string },
): Person => ({
  id,
  faceCount,
  assetIds,
  centroid: [1, 0],
  embeddingKind: "identity",
  ...avatar,
});

const ana = person("person-1", ["ana-a", "ana-b"], 9, {
  avatarUri: "file://crop-of-ana.jpg",
  avatarAssetId: "ana-a",
});
const ben = person("person-2", ["ben-a", "ben-b"], 4, {
  avatarUri: "file://crop-of-ben.jpg",
  avatarAssetId: "ben-b",
});

{
  const before = summariesForPeople([ana, ben]);
  const anaBefore = before.find((p) => p.assetIds.includes("ana-a"));
  const benBefore = before.find((p) => p.assetIds.includes("ben-a"));
  assert(anaBefore?.faceThumbUri === ana.avatarUri, "ana starts with her own crop");
  assert(benBefore?.faceThumbUri === ben.avatarUri, "ben starts with his own crop");

  // The recluster. Same humans, same photos, ids swapped -- which is precisely
  // what `rebuildPeople` does when observation order shifts.
  const after = summariesForPeople([
    { ...ben, id: "person-1" },
    { ...ana, id: "person-2" },
  ]);
  const anaAfter = after.find((p) => p.assetIds.includes("ana-a"));
  const benAfter = after.find((p) => p.assetIds.includes("ben-a"));
  assert(
    anaAfter?.faceThumbUri === ana.avatarUri,
    `ana kept her photos and must keep her face, got ${anaAfter?.faceThumbUri}`,
  );
  assert(
    benAfter?.faceThumbUri === ben.avatarUri,
    `ben kept his photos and must keep his face, got ${benAfter?.faceThumbUri}`,
  );
  // Vacuity guard: the ids really did change hands, so a person-id lookup would
  // genuinely have crossed the two avatars over rather than coincidentally
  // agreeing.
  assert(anaAfter?.id === "person-2" && benAfter?.id === "person-1", "the ids swapped");
  assert(
    anaBefore?.id === "person-1" && benBefore?.id === "person-2",
    "and they were the other way round to begin with",
  );
}

// The cover photo behind the avatar must be the photo the avatar was cut from,
// not an unrelated shot that merely happens to be first in the list.
{
  const [summary] = summariesForPeople([
    person("person-1", ["group-shot", "portrait"], 2, {
      avatarUri: "file://crop.jpg",
      avatarAssetId: "portrait",
    }),
  ]);
  assert(
    summary.coverAssetId === "portrait",
    `the cover must be the cropped photo, got ${summary.coverAssetId}`,
  );
  assert(summary.faceThumbUri === "file://crop.jpg", "and the crop is shown");
}

// No crop is a normal state -- a rebuild drops every avatar, and the backfill
// re-derives them -- so it must degrade to the person's own first photo. That
// is the whole safety argument: losing a face is recoverable, showing the wrong
// one is not.
{
  const [summary] = summariesForPeople([person("person-1", ["only-photo"])]);
  assert(summary.faceThumbUri === undefined, "no crop means no crop");
  assert(summary.coverAssetId === "only-photo", "the fallback is their own photo");
}

// A rebuild from raw observations must produce people with NO avatar, rather
// than carrying one over from whoever previously held that id. This is the
// property that makes the whole design fail-safe, so it is asserted against the
// real clusterer rather than a hand-built record.
{
  const rebuilt = clusterFaces(
    [
      { assetId: "x", embedding: [1, 0], embeddingKind: "identity" as const },
      { assetId: "y", embedding: [0.999, 0.045], embeddingKind: "identity" as const },
    ],
    {},
  );
  assert(rebuilt.length === 1, `one person expected, got ${rebuilt.length}`);
  assert(
    rebuilt[0].id === "person-1",
    "and it takes the id a previous person would have held",
  );
  assert(
    rebuilt[0].avatarUri === undefined,
    "a freshly clustered person must start with no face, not inherit one",
  );
}

// Merging two tiles is the one case where an avatar legitimately moves: it is
// the same human either way. The survivor's own face wins, because replacing a
// face the user has learned to recognise buys nothing.
{
  const keeper = person("person-1", ["a"], 5, {
    avatarUri: "file://keeper.jpg",
    avatarAssetId: "a",
  });
  const absorbed = person("person-2", ["b"], 2, {
    avatarUri: "file://absorbed.jpg",
    avatarAssetId: "b",
  });
  const bothHaveOne = [{ ...keeper }, { ...absorbed }];
  assert(mergeExistingPeople(bothHaveOne, 0, 1), "the merge must happen");
  assert(
    bothHaveOne[0].avatarUri === "file://keeper.jpg",
    `the survivor keeps its own face, got ${bothHaveOne[0].avatarUri}`,
  );

  const survivorHasNone = [person("person-1", ["a"], 5), { ...absorbed }];
  assert(mergeExistingPeople(survivorHasNone, 0, 1), "the merge must happen");
  assert(
    survivorHasNone[0].avatarUri === "file://absorbed.jpg",
    "an empty slot inherits the absorbed face rather than staying blank",
  );
  assert(
    survivorHasNone[0].avatarAssetId === "b",
    "and the cover photo comes with it, or the tile would show a mismatched pair",
  );
}

/**
 * The avatar gate. The owner asked for a thumbnail "without any hand on it",
 * and an eye-open probability is the only occlusion signal the detector reports:
 * a hand, hair or sunglasses across the eyes push it down.
 */
{
  const box = { x: 0, y: 0, width: 200, height: 200 };
  assert(isAvatarFace(box), "a face with no classification data is accepted");
  assert(
    isAvatarFace({ ...box, leftEyeOpen: 0.9, rightEyeOpen: 0.85 }),
    "both eyes open is the ideal avatar",
  );
  assert(
    !isAvatarFace({ ...box, leftEyeOpen: 0.02, rightEyeOpen: 0.9 }),
    "one eye hidden behind a hand is not an avatar",
  );
  assert(
    isAvatarFace({ ...box, leftEyeOpen: 0.45, rightEyeOpen: 0.45 }),
    "a smile narrows the eyes and must still qualify -- the gate rejects " +
      "obscured faces, not expressive ones",
  );
  assert(
    !isAvatarFace({ ...box, headEulerAngleZ: 47 }),
    "a head tilted 47 degrees is a candid, not a portrait",
  );
  assert(isAvatarFace({ ...box, headEulerAngleZ: -12 }), "a slight tilt is fine");
  // Vacuity guard: an absent signal must never be read as zero, or a detector
  // build without classification would reject every face in the library and
  // leave the whole grid with no avatars at all.
  assert(
    isAvatarFace({ ...box, leftEyeOpen: undefined, rightEyeOpen: undefined }),
    "absent metadata reads as acceptable",
  );
}

console.log("face thumbnail self-check passed");
