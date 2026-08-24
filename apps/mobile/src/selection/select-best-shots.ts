import type { PickedPhoto } from "../import/picked-photo";

// @ts-expect-error Node requires the extension; Metro resolves this path too.
import { bestSmile, significantFaces, worstEyesOpen } from "./quality-signals.ts";
import type { Category, QualitySignals } from "./quality-signals";
import type { AlbumData, Alt, Pool, Selected } from "./types";

const NEAR_DUPLICATE_COSINE = 0.92;
const LUMA_FEATURE_COUNT = 64;
const SIGNIFICANT_FACE_AREA = 0.005;
const ALL_EYES_OPEN_THRESHOLD = 0.5;
const BLINK_REJECTION_THRESHOLD = 0.35;
const SMILE_TIE_BAND = 0.02;

type CategoryWeights = {
  sharpness: number;
  resolution: number;
  eyesOpen: number;
  smile: number;
  exposure: number;
  clipping: number;
  cutFacePenalty: number;
};

/**
 * Portraits prioritize face expression; detail/scene frames prioritize pixels.
 * Group shots retain a strong eye-open term while allowing more face-edge risk.
 */
const CATEGORY_WEIGHTS: Record<Category, CategoryWeights> = {
  portrait: {
    sharpness: 0.38,
    resolution: 0.05,
    eyesOpen: 0.25,
    smile: 0.12,
    exposure: 0.1,
    clipping: 0.1,
    cutFacePenalty: 0.22,
  },
  couple: {
    sharpness: 0.36,
    resolution: 0.05,
    eyesOpen: 0.27,
    smile: 0.12,
    exposure: 0.1,
    clipping: 0.1,
    cutFacePenalty: 0.18,
  },
  group: {
    sharpness: 0.42,
    resolution: 0.08,
    eyesOpen: 0.24,
    smile: 0.05,
    exposure: 0.11,
    clipping: 0.1,
    cutFacePenalty: 0.1,
  },
  detail: {
    sharpness: 0.58,
    resolution: 0.16,
    eyesOpen: 0,
    smile: 0,
    exposure: 0.13,
    clipping: 0.13,
    cutFacePenalty: 0.05,
  },
  scene: {
    sharpness: 0.55,
    resolution: 0.18,
    eyesOpen: 0,
    smile: 0,
    exposure: 0.14,
    clipping: 0.13,
    cutFacePenalty: 0.03,
  },
};

type AnalyzedPhoto = PickedPhoto & {
  embedding?: unknown;
  analysis?: QualitySignals;
};

type Candidate = {
  photo: AnalyzedPhoto;
  inputIndex: number;
  embedding?: number[];
  analysis?: QualitySignals;
  quality: number;
  detailScore?: number;
  sharpness?: number;
  eyesOpen?: number;
  smile?: number;
  pixels: number;
};

type Take = {
  firstInputIndex: number;
  candidates: Candidate[];
};

type RankedTake = {
  take: Take;
  winner: Candidate;
  blinkGateEnabled: boolean;
  blinkRejectedIds: Set<string>;
};

/**
 * Collapse near-duplicate frames and choose a deterministic, diverse set using
 * only on-device image signals and source metadata.
 */
export function selectBestShots(
  photos: AnalyzedPhoto[],
  opts: { count: number },
): AlbumData {
  const candidates = buildCandidates(photos);
  const eligibleCandidates = candidates.filter(
    (candidate) => !candidate.analysis?.isScreenshotOrDocument,
  );
  const requestedCount = normalizeCount(
    opts.count,
    eligibleCandidates.length > 0,
  );

  if (candidates.length === 0) {
    return {
      album_id: albumId([], requestedCount),
      selected: [],
      pool: [],
    };
  }

  const rankedTakes = buildTakes(eligibleCandidates)
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

  const selected: Selected[] = chosenTakes.map((rankedTake, index) => ({
    media_id: rankedTake.winner.photo.id,
    page: index + 1,
    chosen_because: chosenReasons(rankedTake.winner, rankedTake.take),
    alternatives: alternativesFor(rankedTake),
  }));

  const pool: Pool[] = candidates
    .filter((candidate) => !selectedIds.has(candidate.photo.id))
    .map((candidate) => ({
      media_id: candidate.photo.id,
      quality: roundScore(candidate.quality),
      reasons: candidate.analysis?.isScreenshotOrDocument
        ? ["Screenshot excluded from automatic album selection."]
        : poolReasons(
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

function buildCandidates(photos: AnalyzedPhoto[]): Candidate[] {
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
    const analysis = photo.analysis;
    const faces = analysis
      ? significantFaces(analysis.faces, SIGNIFICANT_FACE_AREA)
      : [];
    const eyesOpen = analysis ? worstEyesOpen(faces) : undefined;
    const smile = analysis ? bestSmile(faces) : undefined;
    const sharpness = unitSignal(analysis?.sharpness);
    candidates.push({
      photo,
      inputIndex,
      embedding,
      analysis,
      quality: analysis
        ? enhancedQualityScore({
            analysis,
            detailScore,
            sharpness,
            eyesOpen,
            smile,
            pixels,
          })
        : legacyQualityScore(detailScore, pixels),
      detailScore,
      sharpness,
      eyesOpen,
      smile,
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
  const blinkGateEnabled = take.candidates.some(
    (candidate) =>
      candidate.eyesOpen !== undefined &&
      candidate.eyesOpen >= ALL_EYES_OPEN_THRESHOLD,
  );
  const blinkRejectedIds = new Set(
    blinkGateEnabled
      ? take.candidates
          .filter(
            (candidate) =>
              candidate.eyesOpen !== undefined &&
              candidate.eyesOpen < BLINK_REJECTION_THRESHOLD,
          )
          .map((candidate) => candidate.photo.id)
      : [],
  );
  const eligible = take.candidates.filter(
    (candidate) => !blinkRejectedIds.has(candidate.photo.id),
  );
  const winner = [...eligible].sort(compareCandidates)[0];
  return { take, winner, blinkGateEnabled, blinkRejectedIds };
}

function compareCandidates(left: Candidate, right: Candidate): number {
  const smileDifference = smileTieBreak(left, right);
  return (
    smileDifference ||
    right.quality - left.quality ||
    right.pixels - left.pixels ||
    left.inputIndex - right.inputIndex ||
    left.photo.id.localeCompare(right.photo.id)
  );
}

function smileTieBreak(left: Candidate, right: Candidate): number {
  if (
    !isSmileCategory(left.analysis?.category) ||
    !isSmileCategory(right.analysis?.category) ||
    left.smile === undefined ||
    right.smile === undefined ||
    Math.round(left.quality / SMILE_TIE_BAND) !==
      Math.round(right.quality / SMILE_TIE_BAND)
  ) {
    return 0;
  }

  return right.smile - left.smile;
}

function compareRankedTakes(left: RankedTake, right: RankedTake): number {
  return (
    compareCandidates(left.winner, right.winner) ||
    left.take.firstInputIndex - right.take.firstInputIndex
  );
}

function alternativesFor(rankedTake: RankedTake): Alt[] {
  const { take, winner } = rankedTake;
  return take.candidates
    .filter((candidate) => candidate.photo.id !== winner.photo.id)
    .sort(compareCandidates)
    .map((candidate) => ({
      media_id: candidate.photo.id,
      not_chosen_because: candidateNotChosenReasons(
        candidate,
        winner,
        rankedTake,
      ),
    }));
}

function chosenReasons(winner: Candidate, take: Take): string[] {
  if (!winner.analysis) {
    return legacyChosenReasons(winner, take.candidates.length);
  }

  const reasons: string[] = [];
  const summary: string[] = [];
  if (
    winner.eyesOpen !== undefined &&
    winner.eyesOpen >= ALL_EYES_OPEN_THRESHOLD
  ) {
    summary.push("all known significant faces have open eyes");
  }
  if (
    take.candidates.length > 1 &&
    winner.sharpness !== undefined &&
    isSharpest(winner, take.candidates)
  ) {
    summary.push(`sharpest of ${take.candidates.length} near-duplicates`);
  }

  if (take.candidates.length > 1) {
    reasons.push(
      summary.length > 0
        ? `${capitalize(summary.join("; "))}.`
        : `Highest ${winner.analysis.category}-weighted quality among ${take.candidates.length} near-duplicate frames.`,
    );
  } else {
    reasons.push(
      "Selected from a distinct visual take to keep the album varied.",
    );
  }

  if (winner.sharpness !== undefined) {
    reasons.push(`Pixel sharpness: ${formatPercent(winner.sharpness)}.`);
  } else if (winner.detailScore !== undefined) {
    reasons.push(
      `No pixel sharpness was available; thumbnail contrast/detail proxy: ${formatPercent(winner.detailScore)}.`,
    );
  }
  if (winner.eyesOpen !== undefined) {
    reasons.push(
      `Worst known significant-face eye-open signal: ${formatPercent(winner.eyesOpen)}.`,
    );
  }
  if (isSmileCategory(winner.analysis.category) && winner.smile !== undefined) {
    reasons.push(`Best smile signal: ${formatPercent(winner.smile)}.`);
  }
  if (winner.analysis.anyFaceCutAtEdge) {
    reasons.push(
      `A face touches the frame edge; the ${winner.analysis.category} cut-face penalty was applied.`,
    );
  }
  if (winner.pixels > 0) {
    reasons.push(`${(winner.pixels / 1_000_000).toFixed(1)} MP source resolution.`);
  }

  return reasons;
}

function legacyChosenReasons(winner: Candidate, takeSize: number): string[] {
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

function candidateNotChosenReasons(
  candidate: Candidate,
  winner: Candidate,
  rankedTake: RankedTake,
): string[] {
  if (!candidate.analysis && !winner.analysis) {
    return [legacyNearDuplicateReason(candidate, winner)];
  }

  const reasons: string[] = [];
  if (rankedTake.blinkRejectedIds.has(candidate.photo.id)) {
    reasons.push(
      `Rejected: subject blinking (${formatPercent(candidate.eyesOpen ?? 0)} eye-open, below the ${formatPercent(BLINK_REJECTION_THRESHOLD)} gate).`,
    );
  }
  if (
    candidate.analysis?.anyFaceCutAtEdge &&
    !winner.analysis?.anyFaceCutAtEdge
  ) {
    reasons.push("Rejected: face cut at frame edge.");
  }
  if (
    candidate.sharpness !== undefined &&
    winner.sharpness !== undefined &&
    candidate.sharpness + 0.01 < winner.sharpness
  ) {
    reasons.push(
      `Rejected: blurrier than the chosen frame (${formatPercent(candidate.sharpness)} vs ${formatPercent(winner.sharpness)} sharpness).`,
    );
  }
  if (
    isSmileCategory(candidate.analysis?.category) &&
    candidate.smile !== undefined &&
    winner.smile !== undefined &&
    candidate.smile + 0.05 < winner.smile
  ) {
    reasons.push(
      `Lower smile signal than the chosen frame (${formatPercent(candidate.smile)} vs ${formatPercent(winner.smile)}).`,
    );
  }
  reasons.push(legacyNearDuplicateReason(candidate, winner));
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

  if (!candidate.analysis && !rankedTake.winner.analysis) {
    return legacyPoolReasons(candidate, rankedTake, selectedTake);
  }

  if (selectedTake) {
    return candidateNotChosenReasons(
      candidate,
      rankedTake.winner,
      rankedTake,
    );
  }

  const reasons: string[] = [];
  if (candidate.photo.id !== rankedTake.winner.photo.id) {
    reasons.push(
      ...candidateNotChosenReasons(candidate, rankedTake.winner, rankedTake),
    );
    reasons.push(
      "The album target was already filled with stronger frames from distinct takes.",
    );
    return reasons;
  }

  if (candidate.analysis?.anyFaceCutAtEdge) {
    reasons.push("A face cut at the frame edge lowered this frame's quality.");
  }
  reasons.push(
    "The album target was already filled with stronger frames from distinct takes.",
  );
  return reasons;
}

function legacyPoolReasons(
  candidate: Candidate,
  rankedTake: RankedTake,
  selectedTake: boolean,
): string[] {
  if (selectedTake) {
    return [legacyNearDuplicateReason(candidate, rankedTake.winner)];
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

function legacyNearDuplicateReason(
  candidate: Candidate,
  winner: Candidate,
): string {
  return `Near-duplicate of the chosen frame (cosine similarity ${formatSimilarity(
    cosineSimilarity(candidate.embedding, winner.embedding),
  )}).`;
}

function readEmbedding(photo: AnalyzedPhoto): number[] | undefined {
  const raw = photo.embedding;
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

function legacyQualityScore(
  detailScore: number | undefined,
  pixels: number,
): number {
  const resolutionScore = resolutionQuality(pixels);

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

function enhancedQualityScore(input: {
  analysis: QualitySignals;
  detailScore: number | undefined;
  sharpness: number | undefined;
  eyesOpen: number | undefined;
  smile: number | undefined;
  pixels: number;
}): number {
  const { analysis, detailScore, sharpness, eyesOpen, smile, pixels } = input;
  const weights = CATEGORY_WEIGHTS[analysis.category];
  const components: Array<[number, number | undefined]> = [
    [weights.sharpness, sharpness ?? detailScore],
    [weights.resolution, resolutionQuality(pixels)],
    [weights.eyesOpen, eyesOpen],
    [weights.smile, smile],
    [weights.exposure, exposureQuality(unitSignal(analysis.exposure))],
    [weights.clipping, inverseSignal(analysis.clippedFraction)],
  ];
  let weightedTotal = 0;
  let availableWeight = 0;

  for (const [weight, value] of components) {
    if (weight > 0 && value !== undefined) {
      weightedTotal += weight * value;
      availableWeight += weight;
    }
  }

  if (availableWeight === 0) {
    return legacyQualityScore(detailScore, pixels);
  }

  const cutAtEdge =
    analysis.anyFaceCutAtEdge || analysis.faces.some((face) => face.cutAtEdge);
  const cutPenalty = cutAtEdge ? weights.cutFacePenalty : 0;
  return clamp01(weightedTotal / availableWeight - cutPenalty);
}

function resolutionQuality(pixels: number): number | undefined {
  return pixels > 0 ? clamp01(Math.sqrt(pixels / 12_000_000)) : undefined;
}

function exposureQuality(exposure: number | undefined): number | undefined {
  return exposure === undefined
    ? undefined
    : clamp01(1 - Math.abs(exposure - 0.5) * 2);
}

function inverseSignal(value: number | undefined): number | undefined {
  const signal = unitSignal(value);
  return signal === undefined ? undefined : 1 - signal;
}

function unitSignal(value: number | undefined): number | undefined {
  return typeof value === "number" && Number.isFinite(value)
    ? clamp01(value)
    : undefined;
}

function isSharpest(winner: Candidate, candidates: Candidate[]): boolean {
  if (winner.sharpness === undefined) {
    return false;
  }
  return candidates.every(
    (candidate) =>
      candidate.sharpness === undefined ||
      candidate.sharpness <= winner.sharpness! + Number.EPSILON,
  );
}

function isSmileCategory(category: Category | undefined): boolean {
  return category === "portrait" || category === "couple";
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

function formatPercent(value: number): string {
  return `${Math.round(clamp01(value) * 100)}%`;
}

function capitalize(value: string): string {
  return value.length > 0 ? value[0].toUpperCase() + value.slice(1) : value;
}

function clamp01(value: number): number {
  return Math.max(0, Math.min(1, value));
}

function roundScore(score: number): number {
  return Math.round(clamp01(score) * 1_000_000) / 1_000_000;
}
