import type { PickedPhoto } from "../import/picked-photo";

import type { AlbumData, Alt, Pool, Selected } from "./types";

const NEAR_DUPLICATE_COSINE = 0.92;
const LUMA_FEATURE_COUNT = 64;

type AnalyzedPhoto = PickedPhoto & {
  embedding?: unknown;
};

type Candidate = {
  photo: PickedPhoto;
  inputIndex: number;
  embedding?: number[];
  quality: number;
  detailScore?: number;
  pixels: number;
};

type Take = {
  firstInputIndex: number;
  candidates: Candidate[];
};

type RankedTake = {
  take: Take;
  winner: Candidate;
};

/**
 * Collapse near-duplicate frames and choose a deterministic, diverse set using
 * only on-device image signals and source metadata.
 */
export function selectBestShots(
  photos: PickedPhoto[],
  opts: { count: number },
): AlbumData {
  const candidates = buildCandidates(photos);
  const requestedCount = normalizeCount(opts.count, candidates.length > 0);

  if (candidates.length === 0) {
    return {
      album_id: albumId([], requestedCount),
      selected: [],
      pool: [],
    };
  }

  const rankedTakes = buildTakes(candidates)
    .map(rankTake)
    .sort(compareRankedTakes);
  const chosenTakes = rankedTakes.slice(
    0,
    Math.min(requestedCount, rankedTakes.length),
  );
  const selectedIds = new Set(
    chosenTakes.map(({ winner }) => winner.photo.id),
  );
  const selectedTakeByMediaId = new Map<string, RankedTake>();
  const takeByMediaId = new Map<string, RankedTake>();

  for (const rankedTake of rankedTakes) {
    for (const candidate of rankedTake.take.candidates) {
      takeByMediaId.set(candidate.photo.id, rankedTake);
    }
  }
  for (const rankedTake of chosenTakes) {
    for (const candidate of rankedTake.take.candidates) {
      selectedTakeByMediaId.set(candidate.photo.id, rankedTake);
    }
  }

  const selected: Selected[] = chosenTakes.map(
    ({ take, winner }, index) => ({
      media_id: winner.photo.id,
      page: index + 1,
      chosen_because: chosenReasons(winner, take.candidates.length),
      alternatives: alternativesFor(take, winner),
    }),
  );

  const pool: Pool[] = candidates
    .filter((candidate) => !selectedIds.has(candidate.photo.id))
    .map((candidate) => ({
      media_id: candidate.photo.id,
      quality: roundScore(candidate.quality),
      reasons: poolReasons(
        candidate,
        takeByMediaId.get(candidate.photo.id),
        selectedTakeByMediaId.has(candidate.photo.id),
      ),
    }));

  return {
    album_id: albumId(
      candidates.map(({ photo }) => photo),
      requestedCount,
    ),
    selected,
    pool,
  };
}

function buildCandidates(photos: PickedPhoto[]): Candidate[] {
  const seenIds = new Set<string>();
  const candidates: Candidate[] = [];

  photos.forEach((photo, inputIndex) => {
    if (seenIds.has(photo.id)) {
      return;
    }
    seenIds.add(photo.id);

    const embedding = readEmbedding(photo);
    const detailScore = thumbnailDetailScore(embedding);
    const pixels = sourcePixels(photo);
    candidates.push({
      photo,
      inputIndex,
      embedding,
      quality: qualityScore(detailScore, pixels),
      detailScore,
      pixels,
    });
  });

  return candidates;
}

function buildTakes(candidates: Candidate[]): Take[] {
  const takes: Take[] = [];

  for (const candidate of candidates) {
    const matchingTake = takes.find((take) =>
      take.candidates.every(
        (member) =>
          cosineSimilarity(candidate.embedding, member.embedding) >=
          NEAR_DUPLICATE_COSINE,
      ),
    );

    if (matchingTake) {
      matchingTake.candidates.push(candidate);
    } else {
      takes.push({
        firstInputIndex: candidate.inputIndex,
        candidates: [candidate],
      });
    }
  }

  return takes;
}

function rankTake(take: Take): RankedTake {
  const winner = [...take.candidates].sort(compareCandidates)[0];
  return { take, winner };
}

function compareCandidates(left: Candidate, right: Candidate): number {
  return (
    right.quality - left.quality ||
    right.pixels - left.pixels ||
    left.inputIndex - right.inputIndex ||
    left.photo.id.localeCompare(right.photo.id)
  );
}

function compareRankedTakes(left: RankedTake, right: RankedTake): number {
  return (
    compareCandidates(left.winner, right.winner) ||
    left.take.firstInputIndex - right.take.firstInputIndex
  );
}

function alternativesFor(take: Take, winner: Candidate): Alt[] {
  return take.candidates
    .filter((candidate) => candidate.photo.id !== winner.photo.id)
    .sort(compareCandidates)
    .map((candidate) => ({
      media_id: candidate.photo.id,
      not_chosen_because: [
        `Near-duplicate of the chosen frame (cosine similarity ${formatSimilarity(
          cosineSimilarity(candidate.embedding, winner.embedding),
        )}).`,
      ],
    }));
}

function chosenReasons(winner: Candidate, takeSize: number): string[] {
  const reasons = [
    takeSize > 1
      ? `Strongest thumbnail-detail proxy among ${takeSize} near-duplicate frames.`
      : "Selected from a distinct visual take to keep the album varied.",
  ];

  if (winner.detailScore !== undefined) {
    reasons.push(
      `Thumbnail contrast/detail proxy: ${Math.round(winner.detailScore * 100)}%.`,
    );
  } else {
    reasons.push("No reliable thumbnail-detail proxy was available; stable metadata order was used.");
  }
  if (winner.pixels > 0) {
    reasons.push(`${(winner.pixels / 1_000_000).toFixed(1)} MP source resolution.`);
  }

  return reasons;
}

function poolReasons(
  candidate: Candidate,
  rankedTake: RankedTake | undefined,
  selectedTake: boolean,
): string[] {
  if (!rankedTake) {
    return ["No valid take information was available for this frame."];
  }

  if (selectedTake) {
    return [
      `Near-duplicate of the chosen frame (cosine similarity ${formatSimilarity(
        cosineSimilarity(candidate.embedding, rankedTake.winner.embedding),
      )}).`,
    ];
  }

  if (candidate.photo.id !== rankedTake.winner.photo.id) {
    return [
      "Near-duplicate within an unselected take; this was not that take's strongest frame.",
      "The album target was already filled with stronger frames from distinct takes.",
    ];
  }

  return [
    "The album target was already filled with stronger frames from distinct takes.",
  ];
}

function readEmbedding(photo: PickedPhoto): number[] | undefined {
  const raw = (photo as AnalyzedPhoto).embedding;
  if (!Array.isArray(raw) || raw.length === 0) {
    return undefined;
  }

  const embedding: number[] = [];
  for (const value of raw) {
    if (typeof value !== "number" || !Number.isFinite(value)) {
      return undefined;
    }
    embedding.push(value);
  }

  return embedding;
}

function cosineSimilarity(
  left: number[] | undefined,
  right: number[] | undefined,
): number {
  if (!left || !right || left.length !== right.length || left.length === 0) {
    return 0;
  }

  let dot = 0;
  let leftMagnitudeSquared = 0;
  let rightMagnitudeSquared = 0;
  for (let index = 0; index < left.length; index += 1) {
    dot += left[index] * right[index];
    leftMagnitudeSquared += left[index] * left[index];
    rightMagnitudeSquared += right[index] * right[index];
  }

  const denominator = Math.sqrt(leftMagnitudeSquared * rightMagnitudeSquared);
  return denominator > Number.EPSILON ? dot / denominator : 0;
}

function thumbnailDetailScore(embedding?: number[]): number | undefined {
  if (!embedding || embedding.length < LUMA_FEATURE_COUNT) {
    return undefined;
  }

  const luma = embedding.slice(0, LUMA_FEATURE_COUNT);
  const mean = luma.reduce((sum, value) => sum + value, 0) / luma.length;
  const variance =
    luma.reduce((sum, value) => sum + (value - mean) ** 2, 0) /
    luma.length;

  return clamp01(Math.sqrt(variance) * Math.sqrt(LUMA_FEATURE_COUNT));
}

function sourcePixels(photo: PickedPhoto): number {
  const width = positiveNumber(photo.width);
  const height = positiveNumber(photo.height);
  return width !== undefined && height !== undefined ? width * height : 0;
}

function qualityScore(detailScore: number | undefined, pixels: number): number {
  const resolutionScore = pixels > 0 ? clamp01(Math.sqrt(pixels / 12_000_000)) : undefined;

  if (detailScore !== undefined && resolutionScore !== undefined) {
    return clamp01(detailScore * 0.85 + resolutionScore * 0.15);
  }
  if (detailScore !== undefined) {
    return detailScore;
  }
  if (resolutionScore !== undefined) {
    return 0.25 + resolutionScore * 0.5;
  }
  return 0.5;
}

function positiveNumber(value: number | undefined): number | undefined {
  return typeof value === "number" && Number.isFinite(value) && value > 0
    ? value
    : undefined;
}

function normalizeCount(count: number, hasPhotos: boolean): number {
  if (!hasPhotos) {
    return 0;
  }
  return Number.isFinite(count) ? Math.max(1, Math.floor(count)) : 1;
}

function albumId(photos: PickedPhoto[], count: number): string {
  const material = `${count}\u0000${photos.map((photo) => photo.id).join("\u0000")}`;
  let hash = 0x811c9dc5;

  for (let index = 0; index < material.length; index += 1) {
    hash ^= material.charCodeAt(index);
    hash = Math.imul(hash, 0x01000193) >>> 0;
  }

  return `local-${hash.toString(16).padStart(8, "0")}`;
}

function formatSimilarity(similarity: number): string {
  return clamp01(similarity).toFixed(3);
}

function clamp01(value: number): number {
  return Math.max(0, Math.min(1, value));
}

function roundScore(score: number): number {
  return Math.round(clamp01(score) * 1_000_000) / 1_000_000;
}
