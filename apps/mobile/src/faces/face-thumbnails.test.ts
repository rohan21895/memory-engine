import {
  isAvatarFace,
  summariesForPeople,
  // @ts-expect-error Node's TypeScript runner requires the source extension.
} from "./face-index.ts";
// @ts-expect-error Node's TypeScript runner requires the source extension.
import type { Person } from "./types.ts";

function assert(value: unknown, message: string): asserts value {
  if (!value) throw new Error(`face thumbnail self-check failed: ${message}`);
}

/**
 * The bug this file exists for.
 *
 * Face crops used to be stored under the PERSON ID. Person ids are not stable:
 * `rebuildPeople` renumbers every person from `person-1` in observation order,
 * so the first recluster handed person-1's avatar to whoever became person-1
 * next. On the owner's real library 2,081 crops were stored and 2,066 still
 * matched a live id, which is what made it invisible -- the grid looked fully
 * populated and was showing strangers. His words: "thumbnails and actual photos
 * of person don't match, what kind of sorting is this".
 *
 * Assets are the stable key, so every case here is really one question: does the
 * avatar follow the HUMAN across a renumbering?
 */

const person = (id: string, assetIds: string[], faceCount = assetIds.length): Person => ({
  id,
  faceCount,
  assetIds,
  centroid: [1, 0],
  embeddingKind: "identity",
});

// Two people, one stored crop each, filed under the photo the crop came from.
const ana = person("person-1", ["ana-a", "ana-b"], 9);
const ben = person("person-2", ["ben-a", "ben-b"], 4);
const thumbs = { "ana-a": "file://crop-of-ana.jpg", "ben-b": "file://crop-of-ben.jpg" };

{
  const before = summariesForPeople([ana, ben], thumbs);
  const anaBefore = before.find((p) => p.assetIds.includes("ana-a"));
  const benBefore = before.find((p) => p.assetIds.includes("ben-a"));
  assert(anaBefore?.faceThumbUri === thumbs["ana-a"], "ana starts with her own crop");
  assert(benBefore?.faceThumbUri === thumbs["ben-b"], "ben starts with his own crop");

  // The recluster. Same humans, same photos, ids swapped -- which is precisely
  // what `rebuildPeople` does when observation order shifts.
  const after = summariesForPeople(
    [
      { ...ben, id: "person-1" },
      { ...ana, id: "person-2" },
    ],
    thumbs,
  );
  const anaAfter = after.find((p) => p.assetIds.includes("ana-a"));
  const benAfter = after.find((p) => p.assetIds.includes("ben-a"));
  assert(
    anaAfter?.faceThumbUri === thumbs["ana-a"],
    `ana kept her photos and must keep her face, got ${anaAfter?.faceThumbUri}`,
  );
  assert(
    benAfter?.faceThumbUri === thumbs["ben-b"],
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
  const late = person("person-1", ["group-shot", "portrait"]);
  const [summary] = summariesForPeople([late], { portrait: "file://crop.jpg" });
  assert(
    summary.coverAssetId === "portrait",
    `the cover must be the cropped photo, got ${summary.coverAssetId}`,
  );
  assert(summary.faceThumbUri === "file://crop.jpg", "and the crop is shown");
}

// No crop is a normal state -- a person whose every photo is a group shot never
// gets one -- and must degrade to their own first photo rather than to nothing
// or to somebody else's.
{
  const [summary] = summariesForPeople([person("person-1", ["only-photo"])], {
    "someone-elses-photo": "file://not-theirs.jpg",
  });
  assert(summary.faceThumbUri === undefined, "no crop means no crop");
  assert(summary.coverAssetId === "only-photo", "the fallback is their own photo");
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
  assert(
    isAvatarFace({ ...box, headEulerAngleZ: -12 }),
    "a slight tilt is fine",
  );
  // Vacuity guard: an absent signal must never be read as zero, or a detector
  // build without classification would reject every face in the library and
  // leave the whole grid with no avatars at all.
  assert(
    isAvatarFace({ ...box, leftEyeOpen: undefined, rightEyeOpen: undefined }),
    "absent metadata reads as acceptable",
  );
}

console.log("face thumbnail self-check passed");
