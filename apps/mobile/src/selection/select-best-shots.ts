import type { PickedPhoto } from "../import/picked-photo";

import type { AlbumData, Alt, Pool, Selected } from "./types";

const CAPTURE_BUCKET_MS = 3_000;
const FALLBACK_WINDOW_SIZE = 3;

type FutureSelectionFields = {
  captureTime?: unknown;
  capture_time?: unknown;
  faces?: unknown;
};

type RankedPhoto = {
  photo: PickedPhoto;
  inputIndex: number;
  score: number;
  pixels: number;
  aspectScore: number;
  faces?: number;
};

type PhotoGroup = {
  key: string;
  firstInputIndex: number;
  photos: RankedPhoto[];
};

/**
 * Select a deterministic, on-device placeholder set from imported photos.
 *
 * This public signature is intentionally stable: Claude's M2 TypeScript
 * selection engine will replace the implementation without changing callers.
 * Current ranking uses only cheap metadata. If a future PickedPhoto supplies a
 * numeric `faces`, `captureTime`, or `capture_time` field, this placeholder also
 * consumes it without requiring a native or ML dependency.
 */
export function selectBestShots(
  photos: PickedPhoto[],
  opts: { count: number },
): AlbumData {
  const uniquePhotos = deduplicateMediaIds(photos);
  const groups = groupPhotos(uniquePhotos);
  const rankedGroups = groups.map(rankGroup).sort(compareGroups);
  const requestedCount = normalizeCount(opts.count);
  const chosenGroups = rankedGroups.slice(0, requestedCount);
  const selectedIds = new Set(chosenGroups.map(({ winner }) => winner.photo.id));
  const selectedGroupKeys = new Set(chosenGroups.map(({ group }) => group.key));

  const selected: Selected[] = chosenGroups.map(({ group, winner }, index) => ({
    media_id: winner.photo.id,
    page: index + 1,
    chosen_because: chosenReasons(winner, group.photos.length),
    alternatives: alternativesFor(group, winner),
  }));

  const winnerByGroup = new Map(
    rankedGroups.map(({ group, winner }) => [group.key, winner] as const),
  );
  const groupKeyByMediaId = new Map<string, string>();
  for (const group of groups) {
    for (const ranked of group.photos) {
      groupKeyByMediaId.set(ranked.photo.id, group.key);
    }
  }

  const pool: Pool[] = uniquePhotos
    .filter((photo) => !selectedIds.has(photo.id))
    .map((photo) => {
      const groupKey = groupKeyByMediaId.get(photo.id);
      const winner = groupKey ? winnerByGroup.get(groupKey) : undefined;
      const ranked = rankPhoto(photo, photos.indexOf(photo));
      const reasons =
        groupKey && winner && selectedGroupKeys.has(groupKey)
          ? notChosenReasons(ranked, winner)
          : ["Album target was filled before this shot group was selected."];

      return {
        media_id: photo.id,
        quality: roundScore(ranked.score),
        reasons,
      };
    });

  return {
    album_id: albumId(uniquePhotos, requestedCount),
    selected,
    pool,
  };
}

function deduplicateMediaIds(photos: PickedPhoto[]): PickedPhoto[] {
  const seen = new Set<string>();
  return photos.filter((photo) => {
    if (seen.has(photo.id)) {
      return false;
    }
    seen.add(photo.id);
    return true;
  });
}

function groupPhotos(photos: PickedPhoto[]): PhotoGroup[] {
  const stems = photos.map((photo) => duplicateStem(photo.filename));
  const stemCounts = new Map<string, number>();
  for (const stem of stems) {
    stemCounts.set(stem, (stemCounts.get(stem) ?? 0) + 1);
  }

  let fallbackIndex = 0;
  const groups = new Map<string, PhotoGroup>();
  photos.forEach((photo, inputIndex) => {
    const captureBucket = captureTimeBucket(photo);
    const repeatedStem = (stemCounts.get(stems[inputIndex]) ?? 0) > 1;
    let key: string;

    if (captureBucket !== undefined) {
      key = `capture:${captureBucket}`;
    } else if (repeatedStem) {
      key = `filename:${stems[inputIndex]}`;
    } else {
      key = `window:${Math.floor(fallbackIndex / FALLBACK_WINDOW_SIZE)}`;
      fallbackIndex += 1;
    }

    const group = groups.get(key) ?? {
      key,
      firstInputIndex: inputIndex,
      photos: [],
    };
    group.photos.push(rankPhoto(photo, inputIndex));
    groups.set(key, group);
  });

  return [...groups.values()];
}

function duplicateStem(filename: string): string {
  const leaf = filename.split(/[\\/]/).at(-1) ?? filename;
  const extensionIndex = leaf.lastIndexOf(".");
  const withoutExtension = extensionIndex > 0 ? leaf.slice(0, extensionIndex) : leaf;

  return withoutExtension
    .normalize("NFKC")
    .toLocaleLowerCase("en-US")
    .replace(/\s*\(\d+\)$/u, "")
    .replace(
      /[\s_-]+(?:copy|duplicate|dup|edited|edit|enhanced|filtered|export)(?:[\s_-]*\d+)?$/u,
      "",
    )
    .trim();
}

function captureTimeBucket(photo: PickedPhoto): number | undefined {
  const future = photo as PickedPhoto & FutureSelectionFields;
  const raw = future.captureTime ?? future.capture_time;
  let milliseconds: number;

  if (typeof raw === "number") {
    milliseconds = raw < 100_000_000_000 ? raw * 1_000 : raw;
  } else if (typeof raw === "string") {
    milliseconds = Date.parse(raw);
  } else {
    return undefined;
  }

  return Number.isFinite(milliseconds)
    ? Math.floor(milliseconds / CAPTURE_BUCKET_MS)
    : undefined;
}

function rankGroup(group: PhotoGroup): { group: PhotoGroup; winner: RankedPhoto } {
  const ordered = [...group.photos].sort(compareRankedPhotos);
  return { group, winner: ordered[0] };
}

function rankPhoto(photo: PickedPhoto, inputIndex: number): RankedPhoto {
  const width = positiveNumber(photo.width);
  const height = positiveNumber(photo.height);
  const pixels = width !== undefined && height !== undefined ? width * height : 0;
  const aspectScore = aspectSanity(width, height);
  const faces = faceCount(photo);
  const resolutionScore = Math.min(1, pixels / 20_000_000);
  const faceScore = faces === undefined ? 0 : Math.min(1, faces / 5);
  const score = resolutionScore * 0.65 + aspectScore * 0.25 + faceScore * 0.1;

  return { photo, inputIndex, score, pixels, aspectScore, faces };
}

function positiveNumber(value: number | undefined): number | undefined {
  return typeof value === "number" && Number.isFinite(value) && value > 0
    ? value
    : undefined;
}

function aspectSanity(width?: number, height?: number): number {
  if (width === undefined || height === undefined) {
    return 0.25;
  }
  const ratio = Math.max(width, height) / Math.min(width, height);
  if (ratio <= 2) {
    return 1;
  }
  if (ratio <= 3) {
    return 0.5;
  }
  return 0;
}

function faceCount(photo: PickedPhoto): number | undefined {
  const raw = (photo as PickedPhoto & FutureSelectionFields).faces;
  return typeof raw === "number" && Number.isFinite(raw) && raw >= 0
    ? Math.floor(raw)
    : undefined;
}

function compareRankedPhotos(left: RankedPhoto, right: RankedPhoto): number {
  return (
    right.score - left.score ||
    left.photo.id.localeCompare(right.photo.id) ||
    left.photo.filename.localeCompare(right.photo.filename) ||
    left.inputIndex - right.inputIndex
  );
}

function compareGroups(
  left: { group: PhotoGroup; winner: RankedPhoto },
  right: { group: PhotoGroup; winner: RankedPhoto },
): number {
  return (
    compareRankedPhotos(left.winner, right.winner) ||
    left.group.firstInputIndex - right.group.firstInputIndex ||
    left.group.key.localeCompare(right.group.key)
  );
}

function alternativesFor(group: PhotoGroup, winner: RankedPhoto): Alt[] {
  return group.photos
    .filter((candidate) => candidate.photo.id !== winner.photo.id)
    .sort(compareRankedPhotos)
    .map((candidate) => ({
      media_id: candidate.photo.id,
      not_chosen_because: notChosenReasons(candidate, winner),
    }));
}

function chosenReasons(winner: RankedPhoto, groupSize: number): string[] {
  const reasons = [
    groupSize > 1
      ? `Highest metadata quality proxy among ${groupSize} similar shots.`
      : "Only candidate in its near-duplicate group.",
  ];

  if (winner.pixels > 0) {
    reasons.push(`${(winner.pixels / 1_000_000).toFixed(1)} MP source resolution.`);
  } else {
    reasons.push("Source dimensions unavailable; ranked with neutral size metadata.");
  }
  if (winner.aspectScore === 1) {
    reasons.push("Aspect ratio is suitable for a typical album page.");
  }
  if (winner.faces !== undefined) {
    reasons.push(`${winner.faces} ${winner.faces === 1 ? "face" : "faces"} detected.`);
  }
  return reasons;
}

function notChosenReasons(candidate: RankedPhoto, winner: RankedPhoto): string[] {
  const reasons: string[] = [];
  if (candidate.pixels < winner.pixels) {
    reasons.push("Lower source resolution than the selected similar shot.");
  }
  if (candidate.aspectScore < winner.aspectScore) {
    reasons.push("Less album-friendly aspect ratio than the selected similar shot.");
  }
  if (
    candidate.faces !== undefined &&
    winner.faces !== undefined &&
    candidate.faces < winner.faces
  ) {
    reasons.push("Fewer detected faces than the selected similar shot.");
  }
  if (reasons.length === 0) {
    reasons.push(
      candidate.score < winner.score
        ? "Lower combined metadata quality proxy than the selected similar shot."
        : "Quality proxy tied; stable media ID ordering kept selection deterministic.",
    );
  }
  return reasons;
}

function normalizeCount(count: number): number {
  return Number.isFinite(count) ? Math.max(0, Math.floor(count)) : 0;
}

function albumId(photos: PickedPhoto[], count: number): string {
  const material = `${count}\u0000${photos.map((photo) => photo.id).join("\u0000")}`;
  let hash = 0x811c9dc5;
  for (let index = 0; index < material.length; index += 1) {
    hash ^= material.charCodeAt(index);
    hash = Math.imul(hash, 0x01000193);
  }
  return `local-${(hash >>> 0).toString(16).padStart(8, "0")}`;
}

function roundScore(score: number): number {
  return Math.round(score * 1_000_000) / 1_000_000;
}
