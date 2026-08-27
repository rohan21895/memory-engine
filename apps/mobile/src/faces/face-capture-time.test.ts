// @ts-expect-error Node's TypeScript runner requires the source extension.
import { extendFaceClusters } from "./face-cluster.ts";
// @ts-expect-error Node's TypeScript runner requires the source extension.
import { scanFaceAssets } from "./face-index.ts";

/**
 * Zero is not a capture time, and the temporal merge window depends on it.
 *
 * Android reports MediaStore's DATE_TAKEN as `creationTime`, and DATE_TAKEN is
 * literally 0 -- not null, not absent -- for any file whose EXIF the media
 * scanner could not read. The scanner's guard was `Number.isFinite`, which is
 * TRUE for 0, so on the owner's library 13,026 of 17,768 faces were stored at
 * the epoch. Every cluster holding one then had `firstAt = lastAt = 0`, every
 * such pair span-OVERLAPPED, `spanGap` returned 0, and the relaxed temporal bar
 * replaced the strict evidenced bar for essentially every evidenced pair in the
 * library. Measured end to end: the discount performed 10 merges the strict bar
 * refuses, including fusing a 301-face person into a 159-face one.
 *
 * Every assertion below comes in a PAIR, and the pairing is the vacuity guard:
 * the same fixture, the same bars, the same geometry, with capture times ONE
 * MILLISECOND apart. At `capturedAt: 0` the clusters must stay apart; at
 * `capturedAt: 1` they must fuse. So a negative here can never be an accident of
 * a fixture that would not have merged anyway -- its twin proves it merges -- and
 * restoring the old `Number.isFinite` guard turns each first assertion red.
 */

function assert(condition: unknown, message: string): asserts condition {
  if (!condition) {
    throw new Error(`face capture-time self-check failed: ${message}`);
  }
}

const DAY = 24 * 60 * 60 * 1000;
const APRIL = Date.UTC(2025, 3, 1);

function atDegrees(degrees: number): number[] {
  const radians = (degrees * Math.PI) / 180;
  return [Math.cos(radians), Math.sin(radians)];
}

/**
 * Two clusters whose centroids sit 55 degrees apart, i.e. a cosine of ~0.574:
 * BELOW the evidenced bar of 0.6 and ABOVE the temporal bar of 0.5. The pair
 * therefore merges if and only if the temporal discount applies to it, which is
 * the single fact every case here is reading. Five faces each clears
 * MERGE_EVIDENCE_MIN_FACES so both count as evidenced; the 0.95 assignment bar
 * keeps them from collapsing into one cluster before the merge sweep runs.
 */
const SEPARATION_DEGREES = 55;
const bars = {
  threshold: 0.95,
  identityMergeThreshold: 0.6,
  evidencedMergeThreshold: 0.6,
  temporalMergeThreshold: 0.5,
};

function twoClusters(
  leftCapturedAt: number | undefined,
  rightCapturedAt: number | undefined,
): Array<{
  assetId: string;
  embedding: number[];
  embeddingKind: "identity";
  capturedAt?: number;
}> {
  const face = (prefix: string, degrees: number, capturedAt: number | undefined) =>
    [0, 1, 2, 3, 4].map((step) => ({
      assetId: `${prefix}-${step}`,
      embedding: atDegrees(degrees + step),
      embeddingKind: "identity" as const,
      ...(capturedAt === undefined ? {} : { capturedAt }),
    }));
  return [
    ...face("left", 0, leftCapturedAt),
    ...face("right", SEPARATION_DEGREES, rightCapturedAt),
  ];
}

const tiles = (
  left: number | undefined,
  right: number | undefined,
): number => extendFaceClusters([], twoClusters(left, right), bars).length;

// ------------------------------------------- a stored zero must not be a time

assert(
  tiles(0, 0) === 2,
  "two clusters whose only capture time is 0 must NOT be treated as near in " +
    "time; 0 is MediaStore reporting no DATE_TAKEN, not 1 January 1970",
);
// VACUITY GUARD for the line above. One millisecond later the very same
// fixture fuses, so the 2 above is the guard doing work and not a pair that
// could never have merged.
assert(
  tiles(1, 1) === 1,
  "a genuine capture time of 1ms is a real instant: the same fixture must fuse, " +
    "or the assertion above proves nothing",
);

assert(
  tiles(undefined, undefined) === 2,
  "no capture time at all must leave the clusters apart",
);

// ------------------------------------------ a real window is still a window

assert(
  tiles(APRIL, APRIL + 10 * DAY) === 1,
  "ten days apart is inside the 60-day window: the discount must still apply " +
    "when the times are real, or the fix has disabled the mechanism",
);
assert(
  tiles(APRIL, APRIL + 400 * DAY) === 2,
  "400 days apart is outside the window and must NOT get the discount",
);

// ------------------------------------ a lifetime is not a moment

/**
 * `spanGap` returns 0 for spans that merely OVERLAP, so a cluster covering the
 * whole library overlaps everybody and would hand them all the discount. Both
 * clusters must be a MOMENT for the window to mean anything, which is what
 * `narrowSpan` enforces.
 */
function spannedClusters(
  leftWidthDays: number,
  rightWidthDays: number,
  rightOffsetDays: number,
): number {
  const face = (prefix: string, degrees: number, start: number, widthDays: number) =>
    [0, 1, 2, 3, 4].map((step) => ({
      assetId: `${prefix}-${step}`,
      embedding: atDegrees(degrees + step),
      embeddingKind: "identity" as const,
      capturedAt: start + (widthDays / 4) * step * DAY,
    }));
  return extendFaceClusters(
    [],
    [
      ...face("left", 0, APRIL, leftWidthDays),
      ...face("right", SEPARATION_DEGREES, APRIL + rightOffsetDays * DAY, rightWidthDays),
    ],
    bars,
  ).length;
}

assert(
  spannedClusters(4, 4, 10) === 1,
  "two narrow clusters ten days apart are the case the window exists for and " +
    "must still get the discount",
);
// VACUITY GUARD for the line below: the pair above proves this geometry merges.
assert(
  spannedClusters(400, 4, 10) === 2,
  "a cluster spanning 400 days is a lifetime, not a moment; its span overlaps " +
    "everything and must NOT hand the discount out",
);
assert(
  spannedClusters(4, 400, 10) === 2,
  "the width test must apply to BOTH clusters, not just the first",
);

// ------------------------------- a stored index already holds firstAt: 0

/**
 * The scanner guard alone would fix nothing for an existing install. A
 * processed asset is never re-scanned, so `capturedAt: 0` stays on disk
 * forever, and the owner's index already carries 1,966 people at `firstAt: 0`.
 * Those spans come back through `mutablePerson`'s rehydration, which is a third
 * route into a span and is guarded by the same rule.
 */
const storedPerson = (
  id: string,
  degrees: number,
  span: number | undefined,
) => ({
  id,
  faceCount: 5,
  assetIds: [0, 1, 2, 3, 4].map((step) => `${id}-${step}`),
  centroid: atDegrees(degrees),
  embeddingKind: "identity" as const,
  ...(span === undefined ? {} : { firstAt: span, lastAt: span }),
});

// One unrelated new face, because the sweep runs on an extend rather than on
// nothing. It is 180 degrees away and always its own third tile.
const unrelatedFace = [
  { assetId: "far", embedding: atDegrees(180), embeddingKind: "identity" as const },
];

const rehydrated = (span: number | undefined): number =>
  extendFaceClusters(
    [
      storedPerson("stored-left", 2, span),
      storedPerson("stored-right", SEPARATION_DEGREES + 2, span),
    ] as never,
    unrelatedFace,
    bars,
  ).length;

assert(
  rehydrated(0) === 3,
  "a stored firstAt/lastAt of 0 must rehydrate as NO span; otherwise every " +
    "index already on disk keeps merging on the epoch after the scanner is fixed",
);
// VACUITY GUARD: the same stored records one millisecond later do fuse.
assert(
  rehydrated(1) === 2,
  "a stored span of 1ms is a real span and must fuse, or the rehydration " +
    "assertion above proves nothing",
);
assert(
  rehydrated(undefined) === 3,
  "a stored record with no span at all must stay apart",
);

// ------------------------------------------- what the scanner writes down

/**
 * DATE_MODIFIED is deliberately NOT a fallback, and the `modificationTime` on
 * these fixtures is there to prove it: it is the file's mtime, which for a
 * copied library is the copy. A whole library at one instant is worse than no
 * time -- every span narrow, every gap 0, discount everywhere. See
 * `captureTime`'s note for the measurement.
 */
const box = { x: 10, y: 10, width: 64, height: 64 };
const scanned = await scanFaceAssets(
  [
    { id: "taken", width: 200, height: 200, creationTime: APRIL, modificationTime: APRIL + DAY },
    { id: "copied", width: 200, height: 200, creationTime: 0, modificationTime: APRIL + DAY },
    { id: "nothing", width: 200, height: 200, creationTime: 0, modificationTime: 0 },
    { id: "absent", width: 200, height: 200 },
  ] as never,
  {
    isDetectionAvailable: () => true,
    detectFaces: async () => [box],
    embedFace: async () => ({ embedding: [1, 0, 0], kind: "identity" as const }),
  },
);
const capturedFor = (assetId: string): number | undefined =>
  scanned.find(
    (observation: { assetId: string; capturedAt?: number }) =>
      observation.assetId === assetId,
  )?.capturedAt;

assert(scanned.length === 4, "every mock asset must produce one observation");
assert(
  capturedFor("taken") === APRIL,
  "a real DATE_TAKEN is a real capture time and must be kept",
);
// VACUITY GUARD for the line below: the assertion above proves this fixture
// DOES record a capturedAt when there is one, so the undefined below is the
// guard refusing a value rather than the scan writing no times at all.
assert(
  capturedFor("copied") === undefined,
  "DATE_TAKEN of 0 must yield NO capturedAt, and DATE_MODIFIED must not be " +
    "quietly promoted into its place: a copy time is not a shutter time",
);
assert(
  capturedFor("nothing") === undefined,
  "when DATE_TAKEN is not populated the observation must carry NO capturedAt, " +
    "rather than claiming the epoch",
);
assert(
  capturedFor("absent") === undefined,
  "an asset with neither field must carry no capturedAt",
);

console.log("face capture-time self-check passed");
