// @ts-expect-error Node's native TypeScript runner requires the extension.
import { DEFAULT_PLANNER_POLICY, planAlbum, type PlannerCandidate } from "./album-planner.ts";

export const FACE_SHARPNESS_HARNESS_SEED = 0xc019face;
export const SYNTHETIC_LIBRARY_COUNT = 96;
export const ANALYZED_CANDIDATES_PER_LIBRARY = 64;
export const ALBUM_SELECTION_COUNT = 24;

const TIED_TAKES_PER_LIBRARY = 16;
const SINGLE_TAKES_PER_LIBRARY = 32;
const REGIONAL_SHARPNESS_FLOOR =
  DEFAULT_PLANNER_POLICY.headSharpnessFloor;

export type FaceRegionMeasurement = {
  areaRatio: number;
  regionalSharpness: number;
};

export type SharpnessHarnessPhoto = {
  mediaId: string;
  libraryId: string;
  takeId: string;
  quality: number;
  capturedAt: number;
  placeKey: string;
  personIds: string[];
  faces: FaceRegionMeasurement[];
};

export type RepoFixtureCoverage = {
  faceRecords: number;
  photoMediaRecordsWithFaces: number;
  completePhotoMediaRecords: number;
  incompletePhotoMediaRecords: number;
  completePhotos: Array<{
    mediaId: string;
    faces: FaceRegionMeasurement[];
  }>;
};

export type CandidatePolicyId =
  | "all-faces-hard"
  | "area-50-hard"
  | "area-25-hard"
  | "all-faces-soft-tie";

export type CandidatePolicyMeasurement = {
  policy: CandidatePolicyId;
  newlyRejected: number;
  currentlySelectedLost: number;
};

export type FaceSharpnessHarnessReport = {
  seed: number;
  syntheticLibraries: number;
  syntheticPhotos: number;
  analyzedCandidatesPerLibrary: number;
  selectedPerLibrary: number;
  currentSelectedPhotos: number;
  currentEligiblePhotos: number;
  photosWithFaces: number;
  groupPhotos: number;
  repoFixtures: Omit<RepoFixtureCoverage, "completePhotos"> & {
    newlyRejectedByHardPolicies: Record<
      Exclude<CandidatePolicyId, "all-faces-soft-tie">,
      number
    >;
  };
  policies: CandidatePolicyMeasurement[];
};

type PolicyAreaFraction = 0 | 0.5 | 0.25;

/**
 * Deterministic, offline measurement of the four proposed policies.
 *
 * Each synthetic library models the real >500-photo path after its prepass:
 * exactly 64 candidates receive face/pixel analysis, then the real pure album
 * planner chooses 24. Sixteen two-frame takes have deliberately quantized,
 * equal current scores so the soft policy has genuine ties to break; the other
 * 32 takes are singletons. No candidate selection behavior is changed.
 */
export function runFaceSharpnessPolicyHarness(
  repoFixtures: RepoFixtureCoverage = emptyRepoFixtureCoverage(),
): FaceSharpnessHarnessReport {
  const photos = syntheticCorpus();
  const photosByLibrary = groupBy(photos, (photo) => photo.libraryId);
  const currentSelected = new Set<string>();
  const softSelected = new Set<string>();

  for (const libraryPhotos of photosByLibrary.values()) {
    const currentRepresentatives = representatives(libraryPhotos, false);
    const softRepresentatives = representatives(libraryPhotos, true);
    const currentPlan = planAlbum(
      currentRepresentatives.map(plannerCandidate),
      ALBUM_SELECTION_COUNT,
    );
    const softPlan = planAlbum(
      softRepresentatives.map(plannerCandidate),
      ALBUM_SELECTION_COUNT,
    );

    for (const mediaId of currentPlan.selectedIds) currentSelected.add(mediaId);
    for (const mediaId of softPlan.selectedIds) softSelected.add(mediaId);
  }

  const hardPolicies: Array<{
    policy: Exclude<CandidatePolicyId, "all-faces-soft-tie">;
    areaFraction: PolicyAreaFraction;
  }> = [
    { policy: "all-faces-hard", areaFraction: 0 },
    { policy: "area-50-hard", areaFraction: 0.5 },
    { policy: "area-25-hard", areaFraction: 0.25 },
  ];

  const policies: CandidatePolicyMeasurement[] = hardPolicies.map(
    ({ policy, areaFraction }) => {
      const newlyRejectedIds = new Set(
        photos
          .filter((photo) => newlyRejected(photo, areaFraction))
          .map((photo) => photo.mediaId),
      );
      return {
        policy,
        newlyRejected: newlyRejectedIds.size,
        currentlySelectedLost: intersectionSize(
          currentSelected,
          newlyRejectedIds,
        ),
      };
    },
  );
  policies.push({
    policy: "all-faces-soft-tie",
    newlyRejected: 0,
    currentlySelectedLost: differenceSize(currentSelected, softSelected),
  });

  const completeFixturePhotos: SharpnessHarnessPhoto[] =
    repoFixtures.completePhotos.map((fixture, index) => ({
      mediaId: fixture.mediaId,
      libraryId: "repo-fixtures",
      takeId: `fixture-${index}`,
      quality: 1,
      capturedAt: index,
      placeKey: "fixture",
      personIds: [],
      faces: fixture.faces,
    }));

  return {
    seed: FACE_SHARPNESS_HARNESS_SEED,
    syntheticLibraries: SYNTHETIC_LIBRARY_COUNT,
    syntheticPhotos: photos.length,
    analyzedCandidatesPerLibrary: ANALYZED_CANDIDATES_PER_LIBRARY,
    selectedPerLibrary: ALBUM_SELECTION_COUNT,
    currentSelectedPhotos: currentSelected.size,
    currentEligiblePhotos: photos.filter(currentDominantPolicyPasses).length,
    photosWithFaces: photos.filter((photo) => photo.faces.length > 0).length,
    groupPhotos: photos.filter((photo) => photo.faces.length >= 3).length,
    repoFixtures: {
      faceRecords: repoFixtures.faceRecords,
      photoMediaRecordsWithFaces: repoFixtures.photoMediaRecordsWithFaces,
      completePhotoMediaRecords: repoFixtures.completePhotoMediaRecords,
      incompletePhotoMediaRecords: repoFixtures.incompletePhotoMediaRecords,
      newlyRejectedByHardPolicies: Object.fromEntries(
        hardPolicies.map(({ policy, areaFraction }) => [
          policy,
          completeFixturePhotos.filter((photo) =>
            newlyRejected(photo, areaFraction),
          ).length,
        ]),
      ) as FaceSharpnessHarnessReport["repoFixtures"]["newlyRejectedByHardPolicies"],
    },
    policies,
  };
}

export function syntheticCorpus(): SharpnessHarnessPhoto[] {
  const random = mulberry32(FACE_SHARPNESS_HARNESS_SEED);
  const result: SharpnessHarnessPhoto[] = [];

  for (let libraryIndex = 0; libraryIndex < SYNTHETIC_LIBRARY_COUNT; libraryIndex += 1) {
    const libraryId = `library-${String(libraryIndex).padStart(3, "0")}`;
    const libraryStart = Date.UTC(2025, 0, 1) + libraryIndex * 7 * 86_400_000;
    let photoIndex = 0;

    for (
      let takeIndex = 0;
      takeIndex < TIED_TAKES_PER_LIBRARY + SINGLE_TAKES_PER_LIBRARY;
      takeIndex += 1
    ) {
      const takeSize = takeIndex < TIED_TAKES_PER_LIBRARY ? 2 : 1;
      const takeId = `${libraryId}-take-${String(takeIndex).padStart(2, "0")}`;
      const faceCount = syntheticFaceCount(random);
      const faceAreas = syntheticFaceAreas(random, faceCount);
      // Device scores are bounded and serialized; hundredth quantization makes
      // an exact-tie policy observable without letting it reorder near-ties.
      const quality = Math.round((0.42 + random() * 0.5) * 100) / 100;
      const focus = random() < 0.045
        ? 0.025 + random() * 0.055
        : 0.16 + random() * 0.7;
      const people = Array.from(
        { length: Math.min(faceCount, 3) },
        (_, personIndex) =>
          `person-${(libraryIndex * 3 + takeIndex + personIndex) % 12}`,
      );

      for (let variant = 0; variant < takeSize; variant += 1) {
        const mediaId = `${libraryId}-photo-${String(photoIndex).padStart(2, "0")}`;
        result.push({
          mediaId,
          libraryId,
          takeId,
          quality,
          capturedAt: libraryStart + takeIndex * 15 * 60_000 + variant * 650,
          placeKey: `place-${libraryIndex % 6}-${Math.floor(takeIndex / 12)}`,
          personIds: people,
          faces: syntheticFaces(random, faceAreas, focus),
        });
        photoIndex += 1;
      }
    }

    if (photoIndex !== ANALYZED_CANDIDATES_PER_LIBRARY) {
      throw new Error(
        `${libraryId}: generated ${photoIndex} candidates, expected ${ANALYZED_CANDIDATES_PER_LIBRARY}`,
      );
    }
  }

  return result;
}

function syntheticFaceCount(random: () => number): number {
  const draw = random();
  if (draw < 0.15) return 0;
  if (draw < 0.35) return 1;
  if (draw < 0.48) return 2;
  if (draw < 0.78) return 3 + Math.floor(random() * 3);
  return 6 + Math.floor(random() * 5);
}

function syntheticFaceAreas(
  random: () => number,
  count: number,
): number[] {
  if (count === 0) return [];
  const largest = 0.018 + random() * 0.102;
  const relatives = [1];
  for (let index = 1; index < count; index += 1) {
    const relative = index === 1
      ? 0.55 + random() * 0.45
      : index === 2
        ? 0.28 + random() * 0.55
        : 0.08 + Math.pow(random(), 1.5) * 0.55;
    relatives.push(relative);
  }
  return relatives
    .sort((left, right) => right - left)
    .map((relative) => largest * relative);
}

function syntheticFaces(
  random: () => number,
  areas: readonly number[],
  focus: number,
): FaceRegionMeasurement[] {
  const largest = areas[0] ?? 0;
  return areas.map((areaRatio, index) => {
    const relativeArea = largest > 0 ? areaRatio / largest : 0;
    const factor = index === 0
      ? 0.88 + random() * 0.24
      : secondaryFocusFactor(random, relativeArea);
    return {
      areaRatio,
      regionalSharpness: clamp01(focus * factor),
    };
  });
}

function secondaryFocusFactor(
  random: () => number,
  relativeArea: number,
): number {
  const draw = random();
  if (relativeArea < 0.25) {
    if (draw < 0.1) return 0.04 + random() * 0.14;
    if (draw < 0.55) return 0.12 + random() * 0.34;
    return 0.45 + random() * 0.45;
  }
  if (relativeArea < 0.5) {
    if (draw < 0.06) return 0.05 + random() * 0.15;
    if (draw < 0.32) return 0.2 + random() * 0.34;
    return 0.54 + random() * 0.42;
  }
  if (relativeArea < 0.75) {
    if (draw < 0.035) return 0.06 + random() * 0.16;
    if (draw < 0.15) return 0.28 + random() * 0.34;
    return 0.62 + random() * 0.4;
  }
  if (draw < 0.02) return 0.07 + random() * 0.15;
  if (draw < 0.08) return 0.38 + random() * 0.3;
  return 0.7 + random() * 0.36;
}

function representatives(
  photos: readonly SharpnessHarnessPhoto[],
  softTieBreak: boolean,
): SharpnessHarnessPhoto[] {
  return Array.from(groupBy(photos, (photo) => photo.takeId).values()).map(
    (take) =>
      take.slice().sort((left, right) =>
        right.quality - left.quality ||
        (softTieBreak
          ? minimumRegionalSharpness(right.faces, 0) -
            minimumRegionalSharpness(left.faces, 0)
          : 0) ||
        left.mediaId.localeCompare(right.mediaId),
      )[0],
  );
}

function plannerCandidate(photo: SharpnessHarnessPhoto): PlannerCandidate {
  const dominant = photo.faces[0];
  return {
    mediaId: photo.mediaId,
    quality: photo.quality,
    capturedAt: photo.capturedAt,
    placeKey: photo.placeKey,
    personIds: photo.personIds,
    shotGroup: `take:${photo.mediaId}`,
    poseFamily: `take:${photo.mediaId}`,
    category:
      photo.faces.length >= 3
        ? "group"
        : photo.faces.length === 2
          ? "couple"
          : photo.faces.length === 1
            ? "portrait"
            : "scene",
    headSharpness: dominant?.regionalSharpness,
  };
}

function currentDominantPolicyPasses(photo: SharpnessHarnessPhoto): boolean {
  return (
    photo.faces.length === 0 ||
    photo.faces[0].regionalSharpness >= REGIONAL_SHARPNESS_FLOOR
  );
}

function newlyRejected(
  photo: SharpnessHarnessPhoto,
  areaFraction: PolicyAreaFraction,
): boolean {
  return (
    currentDominantPolicyPasses(photo) &&
    photo.faces.length > 0 &&
    minimumRegionalSharpness(photo.faces, areaFraction) <
      REGIONAL_SHARPNESS_FLOOR
  );
}

function minimumRegionalSharpness(
  faces: readonly FaceRegionMeasurement[],
  areaFraction: PolicyAreaFraction,
): number {
  if (faces.length === 0) return 1;
  const largestArea = Math.max(...faces.map((face) => face.areaRatio));
  const included = faces.filter(
    (face) => face.areaRatio >= largestArea * areaFraction,
  );
  return Math.min(...included.map((face) => face.regionalSharpness));
}

function groupBy<Item, Key>(
  items: readonly Item[],
  keyFor: (item: Item) => Key,
): Map<Key, Item[]> {
  const result = new Map<Key, Item[]>();
  for (const item of items) {
    const key = keyFor(item);
    const group = result.get(key);
    if (group) group.push(item);
    else result.set(key, [item]);
  }
  return result;
}

function intersectionSize(left: Set<string>, right: Set<string>): number {
  let count = 0;
  for (const value of left) if (right.has(value)) count += 1;
  return count;
}

function differenceSize(left: Set<string>, right: Set<string>): number {
  let count = 0;
  for (const value of left) if (!right.has(value)) count += 1;
  return count;
}

function clamp01(value: number): number {
  return Math.max(0, Math.min(1, value));
}

function mulberry32(seed: number): () => number {
  let state = seed >>> 0;
  return () => {
    state = (state + 0x6d2b79f5) >>> 0;
    let value = state;
    value = Math.imul(value ^ (value >>> 15), value | 1);
    value ^= value + Math.imul(value ^ (value >>> 7), value | 61);
    return ((value ^ (value >>> 14)) >>> 0) / 4_294_967_296;
  };
}

function emptyRepoFixtureCoverage(): RepoFixtureCoverage {
  return {
    faceRecords: 0,
    photoMediaRecordsWithFaces: 0,
    completePhotoMediaRecords: 0,
    incompletePhotoMediaRecords: 0,
    completePhotos: [],
  };
}
