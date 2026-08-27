import type { PickedPhoto } from "../import/picked-photo";
import type { SemanticSignals } from "../ml/tinyclip";

// @ts-expect-error Node requires the extension; Metro resolves this path too.
import { planAlbum } from "./album-planner.ts";
// @ts-expect-error Node requires the extension; Metro resolves this path too.
import { relativeQualityFloor } from "./image-quality.ts";
// @ts-expect-error Node requires the extension; Metro resolves this path too.
import { compareFramingCompleteness } from "./pose-framing.ts";
import type { BodyCoverage } from "./pose-framing";
// @ts-expect-error Node requires the extension; Metro resolves this path too.
import { bestSmile, significantFaces, worstEyesOpen } from "./quality-signals.ts";
import type { Category, QualitySignals } from "./quality-signals";
import type { AlbumData, Alt, Pool, Selected } from "./types";

/** Absolute quality gate calibrated on desktop-grade measurements. */
const DESKTOP_QUALITY_FLOOR = 0.35;
const NEAR_DUPLICATE_COSINE = 0.92;
const LUMA_FEATURE_COUNT = 64;
const PERCEPTUAL_FEATURE_COUNT = 76;
const LUMA_GRID_EDGE = 8;
const BURST_REFRAME_WINDOW_MS = 8_000;
const TRANSLATED_LUMA_COSINE = 0.92;
const ROTATED_LUMA_COSINE = 0.9;
const ORIENTATION_COLOR_COSINE = 0.98;
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
  perceptualEmbedding?: unknown;
  semantic?: SemanticSignals;
  analysis?: QualitySignals;
  /**
   * How much of the person MoveNet locked onto the frame actually holds.
   *
   * Read ONLY as a tie-break inside a take; see `framingTieWinner`. Absent
   * whenever the pose runtime produced nothing, which is the normal case.
   */
  bodyCoverage?: BodyCoverage;
};

type Candidate = {
  photo: AnalyzedPhoto;
  inputIndex: number;
  embedding?: number[];
  /** Stub model's documented 8x8 luma + 12-bin color fingerprint. */
  perceptualEmbedding?: number[];
  analysis?: QualitySignals;
  quality: number;
  detailScore?: number;
  sharpness?: number;
  eyesOpen?: number;
  smile?: number;
  /**
   * Whether a face big enough to matter is cut by the frame edge.
   *
   * Deliberately NOT `analysis.anyFaceCutAtEdge`, which is computed over every
   * detected box. The planner treats a cut face as a SOFT REJECTION, so a
   * stranger's ear at the border of a group shot removed the whole photo from
   * the album. Eyes and smiles already read from `significantFaces` only; the
   * edge test now agrees with them.
   */
  cutFace: boolean;
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
  opts: {
    count: number;
    pinnedMediaIds?: readonly string[];
    excludedMediaIds?: readonly string[];
  },
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
  const plan = planAlbum(
    rankedTakes.map((rankedTake) => ({
      mediaId: rankedTake.winner.photo.id,
      quality: rankedTake.winner.quality,
      capturedAt: rankedTake.winner.photo.creationTime,
      placeKey: rankedTake.winner.photo.placeKey,
      personIds: rankedTake.winner.photo.personIds,
      embedding: rankedTake.winner.embedding,
      embeddingSpace: rankedTake.winner.photo.semantic
        ? "tinyclip-vit-8m16-yfcc15m-v1"
        : "phone-perceptual-v1",
      comparisonClass: rankedTake.winner.analysis?.category,
      category: rankedTake.winner.analysis?.category,
      shotGroup: `take:${rankedTake.winner.photo.id}`,
      poseFamily:
        rankedTake.winner.photo.poseFamily ??
        `take:${rankedTake.winner.photo.id}`,
      poseCluster: rankedTake.winner.photo.poseCluster,
      pinned: rankedTake.winner.photo.pinned,
      excluded: rankedTake.winner.photo.excluded,
      cutFace: rankedTake.winner.cutFace,
      // A soft cut-face rejection can be waived for a rare moment or scarce
      // person. The owner explicitly disallows automatic half-face picks, so
      // make this hard; an explicit user pin still bypasses planner gates.
      hardRejected:
        rankedTake.winner.cutFace && !rankedTake.winner.photo.pinned,
      hardRejectionReason: rankedTake.winner.cutFace
        ? "face cut at frame edge"
        : undefined,
      faceSharpness: rankedTake.winner.analysis?.faceSharpness,
      // The expanded region includes hair and upper body; the planner's
      // second regional gate is the closest existing contract for it.
      headSharpness: rankedTake.winner.analysis?.subjectSharpness,
      smile: rankedTake.winner.smile,
      eyesOpen: rankedTake.winner.eyesOpen,
      screenshotDocument:
        rankedTake.winner.analysis?.isScreenshotOrDocument,
      aesthetic: rankedTake.winner.photo.semantic?.aesthetic,
      composed: rankedTake.winner.photo.semantic?.composed,
      cleanFrame: rankedTake.winner.photo.semantic?.cleanFrame,
      sleeping: rankedTake.winner.photo.semantic?.sleeping,
      awake: rankedTake.winner.photo.semantic?.awake,
      embraceContext: rankedTake.winner.photo.semantic?.embraceContext,
    })),
    Math.min(requestedCount, rankedTakes.length),
    {
      policy: {
        pinnedMediaIds: opts.pinnedMediaIds ?? [],
        excludedMediaIds: opts.excludedMediaIds ?? [],
        // Preserve the legacy no-analysis import path, which has no measured
        // quality to gate on at all.
        qualityFloor: rankedTakes.some(({ winner }) => winner.analysis)
          ? albumQualityFloor(
              rankedTakes.map(({ winner }) => winner.quality),
              requestedCount,
            )
          : 0,
      },
    },
  );
  const takeByWinnerId = new Map(
    rankedTakes.map((rankedTake) => [rankedTake.winner.photo.id, rankedTake]),
  );
  const chosenTakes = plan.selectedIds.flatMap((mediaId) => {
    const rankedTake = takeByWinnerId.get(mediaId);
    return rankedTake ? [rankedTake] : [];
  });
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
    chosen_because: [
      ...chosenReasons(rankedTake.winner, rankedTake.take),
      ...(rankedTake.winner.analysis
        ? plan.reasonsByMediaId[rankedTake.winner.photo.id] ?? []
        : []),
    ],
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

/**
 * The quality gate the planner applies, capped so it can never empty an album.
 *
 * `DESKTOP_QUALITY_FLOOR` is calibrated against desktop-grade measurements and
 * is the right gate for a normal library. But it is ABSOLUTE, and the planner
 * drops everything below it — so any systemic shift in the measured scale
 * rejects the entire library and the user gets an empty album. That has already
 * happened once here: the blurhash-derived prepass probe reads ~0.05 sharpness
 * for every photo by construction, it was forwarded on as the final quality
 * signal, and every photo in a large library landed under this floor.
 *
 * Capping the absolute floor with a floor derived from the photos actually in
 * hand makes that outcome structurally impossible — the relative value is itself
 * one of the observed scores, so something always survives — while changing
 * nothing for a normal library, where the relative floor sits well above 0.35
 * and the `Math.min` returns the absolute one unchanged.
 *
 * `keepFraction` guarantees the survivors can still fill the album: half the
 * takes, or as many as the album asked for, whichever is larger.
 */
export function albumQualityFloor(
  qualities: ReadonlyArray<number | undefined>,
  requestedCount: number,
): number {
  const measured = qualities.filter(
    (value): value is number => typeof value === "number" && Number.isFinite(value),
  );
  if (measured.length === 0) return 0;
  const keepFraction = Math.min(
    1,
    Math.max(0.5, requestedCount / measured.length),
  );
  return Math.min(
    DESKTOP_QUALITY_FLOOR,
    relativeQualityFloor(measured, keepFraction),
  );
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
    const perceptualEmbedding = readEmbeddingValue(photo.perceptualEmbedding);
    const detailScore = thumbnailDetailScore(
      perceptualEmbedding ?? embedding,
    );
    const pixels = sourcePixels(photo);
    const analysis = photo.analysis;
    const faces = analysis
      ? significantFaces(analysis.faces, SIGNIFICANT_FACE_AREA)
      : [];
    const eyesOpen = analysis ? worstEyesOpen(faces) : undefined;
    const smile = analysis ? bestSmile(faces) : undefined;
    const frameSharpness = unitSignal(analysis?.sharpness);
    const subjectSharpness = subjectFocusSharpness(analysis);
    // A detected subject owns portrait focus. Falling back to the whole frame
    // only when regional evidence is unavailable prevents a sharp background
    // from laundering a motion-blurred face into the album.
    const sharpness = subjectSharpness ?? frameSharpness;
    const cutFace = faces.some((face) => face.cutAtEdge);
    candidates.push({
      photo,
      inputIndex,
      embedding,
      perceptualEmbedding,
      analysis,
      quality: analysis
        ? enhancedQualityScore({
            analysis,
            detailScore,
            sharpness,
            eyesOpen,
            smile,
            cutFace,
            pixels,
          })
        : legacyQualityScore(detailScore, pixels),
      detailScore,
      sharpness,
      eyesOpen,
      smile,
      cutFace,
      pixels,
    });
  });

  return candidates;
}

function buildTakes(candidates: Candidate[]): Take[] {
  const takes: Take[] = [];

  for (const candidate of candidates) {
    const matchingTake = takes.find((take) =>
      take.candidates.every((member) => sameTake(candidate, member)),
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

/**
 * Decide duplicate identity from either direct visual agreement or the extra
 * evidence available inside an actual camera burst.
 *
 * The 0.92 full-vector rule is still the safest path. It fails after a small
 * reframe because the stub fingerprint stores an 8x8 spatial grid, and fails
 * harder when the phone rotates. Inside eight seconds we can use mechanisms
 * invariant to those operations: best translated-grid agreement for a reframe,
 * or the fingerprint's color-distribution tail when orientation changed.
 * Requiring every member of a take to pass still prevents transitive chains.
 */
function sameTake(left: Candidate, right: Candidate): boolean {
  if (
    cosineSimilarity(left.embedding, right.embedding) >=
    NEAR_DUPLICATE_COSINE
  ) {
    return true;
  }

  if (!insideBurstWindow(left.photo, right.photo)) {
    return false;
  }
  const leftPerceptual = left.perceptualEmbedding;
  const rightPerceptual = right.perceptualEmbedding;
  if (
    !leftPerceptual ||
    !rightPerceptual ||
    leftPerceptual.length !== PERCEPTUAL_FEATURE_COUNT ||
    rightPerceptual.length !== PERCEPTUAL_FEATURE_COUNT
  ) {
    return false;
  }

  if (sameOrientation(left.photo, right.photo)) {
    return (
      translatedLumaSimilarity(leftPerceptual, rightPerceptual) >=
      TRANSLATED_LUMA_COSINE
    );
  }
  return (
    orientationLumaSimilarity(leftPerceptual, rightPerceptual) >=
      ROTATED_LUMA_COSINE &&
    cosineSimilarity(
      leftPerceptual.slice(LUMA_FEATURE_COUNT),
      rightPerceptual.slice(LUMA_FEATURE_COUNT),
    ) >= ORIENTATION_COLOR_COSINE
  );
}

function insideBurstWindow(left: PickedPhoto, right: PickedPhoto): boolean {
  return (
    validCaptureTime(left.creationTime) !== undefined &&
    validCaptureTime(right.creationTime) !== undefined &&
    Math.abs(left.creationTime! - right.creationTime!) <=
      BURST_REFRAME_WINDOW_MS
  );
}

function sameOrientation(left: PickedPhoto, right: PickedPhoto): boolean {
  const leftWidth = positiveNumber(left.width);
  const leftHeight = positiveNumber(left.height);
  const rightWidth = positiveNumber(right.width);
  const rightHeight = positiveNumber(right.height);
  if (!leftWidth || !leftHeight || !rightWidth || !rightHeight) return true;
  return (leftWidth >= leftHeight) === (rightWidth >= rightHeight);
}

/** Best overlap cosine after moving an 8x8 thumbprint by up to two cells. */
function translatedLumaSimilarity(left: number[], right: number[]): number {
  let best = -1;
  for (let dy = -2; dy <= 2; dy += 1) {
    for (let dx = -2; dx <= 2; dx += 1) {
      const leftOverlap: number[] = [];
      const rightOverlap: number[] = [];
      for (let y = 0; y < LUMA_GRID_EDGE; y += 1) {
        for (let x = 0; x < LUMA_GRID_EDGE; x += 1) {
          const rightX = x + dx;
          const rightY = y + dy;
          if (
            rightX < 0 ||
            rightX >= LUMA_GRID_EDGE ||
            rightY < 0 ||
            rightY >= LUMA_GRID_EDGE
          ) {
            continue;
          }
          leftOverlap.push(left[y * LUMA_GRID_EDGE + x]);
          rightOverlap.push(right[rightY * LUMA_GRID_EDGE + rightX]);
        }
      }
      best = Math.max(best, cosineSimilarity(leftOverlap, rightOverlap));
    }
  }
  return best;
}

function orientationLumaSimilarity(left: number[], right: number[]): number {
  const luma = right.slice(0, LUMA_FEATURE_COUNT);
  const clockwise = rotateLumaClockwise(luma);
  const counterClockwise = rotateLumaClockwise(
    rotateLumaClockwise(rotateLumaClockwise(luma)),
  );
  return Math.max(
    // Most camera APIs apply EXIF orientation before fingerprinting, so a
    // portrait reframe remains upright even though source dimensions flipped.
    translatedLumaSimilarity(left, luma),
    // Keep both quarter-turns for fingerprints made before EXIF normalization.
    translatedLumaSimilarity(left, clockwise),
    translatedLumaSimilarity(left, counterClockwise),
  );
}

function rotateLumaClockwise(luma: number[]): number[] {
  return Array.from({ length: LUMA_FEATURE_COUNT }, (_, index) => {
    const y = Math.floor(index / LUMA_GRID_EDGE);
    const x = index % LUMA_GRID_EDGE;
    return luma[(LUMA_GRID_EDGE - 1 - x) * LUMA_GRID_EDGE + y];
  });
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
  // The later planner can reject a cut face, but it sees only this take's
  // winner. If the cut frame wins here, the clean alternative is already gone.
  // Preserve explicit user pins; for automatic picks, a clean same-take frame
  // is categorically preferable to any face bisected by the image boundary.
  const cleanFaceAvailable = take.candidates.some(
    (candidate) =>
      !candidate.cutFace && !blinkRejectedIds.has(candidate.photo.id),
  );
  const cutFaceRejectedIds = new Set(
    cleanFaceAvailable
      ? take.candidates
          .filter((candidate) => candidate.cutFace && !candidate.photo.pinned)
          .map((candidate) => candidate.photo.id)
      : [],
  );
  const eligible = take.candidates.filter(
    (candidate) =>
      !blinkRejectedIds.has(candidate.photo.id) &&
      !cutFaceRejectedIds.has(candidate.photo.id),
  );
  const winner = framingTieWinner([...eligible].sort(compareCandidates));
  return {
    take,
    winner,
    blinkGateEnabled,
    blinkRejectedIds,
  };
}

/**
 * Best first.
 *
 * Ordered on independent keys rather than pairwise escapes, because the old
 * shape was not a valid ordering: inside one quality band a portrait/portrait
 * pair compared by smile while every other pair compared by quality, which
 * admits a genuine cycle (A beats C on smile, C beats B on quality, B beats A
 * on quality). Array.prototype.sort on a cyclic comparator returns whatever the
 * input order happens to produce, so the SAME three frames ranked differently
 * depending on the order they were picked in — and this comparator chooses the
 * frame the user actually sees as a take's winner.
 *
 * The band comes first, which changes nothing on its own: bands do not overlap,
 * so band order and quality order agree. Within one band the quality difference
 * is under the measurement's own resolution, so a known smile decides instead.
 */
function compareCandidates(left: Candidate, right: Candidate): number {
  return (
    compareMeasuredSignals(left, right) ||
    left.inputIndex - right.inputIndex ||
    left.photo.id.localeCompare(right.photo.id)
  );
}

/** Every measured key, before the arbitrary input-order fallback. */
function compareMeasuredSignals(left: Candidate, right: Candidate): number {
  return (
    qualityBand(right) - qualityBand(left) ||
    smileRank(right) - smileRank(left) ||
    right.quality - left.quality ||
    right.pixels - left.pixels
  );
}

/** No opinion. `compareFramingCompleteness` treats `unknown` as equal to everything. */
const NO_FRAMING_OPINION: BodyCoverage = {
  framing: "unknown",
  depth: -1,
  cutByFrame: false,
  cutAtJoint: false,
};

/**
 * Body framing, but only where MoveNet's single fit describes the whole picture.
 *
 * MoveNet is single-person. With anyone else in the frame, the coverage
 * describes whichever body it locked onto and says NOTHING about the others, so
 * reading it as "the subject is cut off" would present a claim about one person
 * as a claim about the photograph. Exactly one detected face is the only case
 * where the fit and the picture agree — every detected box, not just the
 * significant ones, because a bystander small enough to ignore for sharpness is
 * still a second person whose framing was never measured.
 *
 * Anything else, including a photo with no analysis and a pose the model could
 * not read, returns the inert value: never rewarded, never penalised.
 */
function singleSubjectFraming(candidate: Candidate): BodyCoverage {
  return candidate.analysis?.faceCount === 1 && candidate.photo.bodyCoverage
    ? candidate.photo.bodyCoverage
    : NO_FRAMING_OPINION;
}

/**
 * Settle a take whose frames every measured signal scored EXACTLY alike.
 *
 * `ordered` is already sorted best-first by `compareCandidates`, whose last two
 * keys are the order the photos happened to be picked in and their id. Frames
 * that reach those keys are near-duplicate takes of one moment that quality,
 * smile and pixel count could not separate at all, so how much of the subject
 * each frame holds is a better answer than "whichever came first".
 *
 * Deliberately NOT a sort. `compareFramingCompleteness` makes `unknown` equal to
 * everything, which is not a transitive equality: with an unreadable pose in the
 * group, `Array.prototype.sort` has an inconsistent comparator and may return a
 * different winner for the same frames in a different input order. This file has
 * already shipped that bug once. A single pass over an order that is already
 * total, promoting only on a STRICTLY better framing, is order-independent.
 *
 * The one asymmetry is deliberate: an `unknown` leader is never displaced,
 * because displacing it would penalise a photo for a pose the model could not
 * read. Most photos in a family library have no clean single subject.
 *
 * It fires only on an exact tie, and that is load-bearing rather than timid.
 * The winner's own quality is forwarded to the planner AND the album's quality
 * floor is derived from those winners, so a swap that changed the number could
 * push a take under the floor — which would change which photos are ELIGIBLE,
 * not merely their order. On an exact tie the planner sees identical numbers and
 * only the media id moves. For the same reason this never reaches
 * `compareRankedTakes` (two takes are two different moments) and is never summed
 * into `enhancedQualityScore`: CX-19 measured every hard framing gate on this
 * codebase and each one cost between 2.7% and 13.8% of real selections.
 */
function framingTieWinner(ordered: Candidate[]): Candidate {
  let winner = ordered[0];
  for (const candidate of ordered) {
    if (compareMeasuredSignals(winner, candidate) !== 0) break;
    if (
      compareFramingCompleteness(
        singleSubjectFraming(candidate),
        singleSubjectFraming(winner),
      ) < 0
    ) {
      winner = candidate;
    }
  }
  return winner;
}

function qualityBand(candidate: Candidate): number {
  return Math.round(candidate.quality / SMILE_TIE_BAND);
}

/** Frames with no usable smile signal sit at zero, so they only lose to a real smile. */
function smileRank(candidate: Candidate): number {
  return isSmileCategory(candidate.analysis?.category) &&
    candidate.smile !== undefined
    ? candidate.smile
    : 0;
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
  if (winner.cutFace) {
    reasons.push(
      `A face touches the frame edge; the ${winner.analysis.category} cut-face penalty was applied.`,
    );
  }
  if (winner.photo.semantic) {
    reasons.push(
      "Checked on this phone for composition, context, and visual variety.",
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
  if (candidate.cutFace && !winner.cutFace) {
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

  if (candidate.cutFace) {
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
  return readEmbeddingValue(photo.embedding);
}

function readEmbeddingValue(raw: unknown): number[] | undefined {
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
  cutFace: boolean;
  pixels: number;
}): number {
  const { analysis, detailScore, sharpness, eyesOpen, smile, cutFace, pixels } =
    input;
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

  const cutPenalty = cutFace ? weights.cutFacePenalty : 0;
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

/**
 * Regional focus is conservative: both the exact face and expanded subject
 * must be sharp when both exist. A ratio below 0.5 means more Laplacian detail
 * lives in the background, so scale the result down proportionally; deliberate
 * portrait bokeh at or above 0.5 is never boosted beyond the measured focus.
 */
function subjectFocusSharpness(
  analysis: QualitySignals | undefined,
): number | undefined {
  if (!analysis) return undefined;
  const face = unitSignal(analysis.faceSharpness);
  const subject = unitSignal(analysis.subjectSharpness);
  const regional =
    face !== undefined && subject !== undefined
      ? Math.min(face, subject)
      : face ?? subject;
  if (regional === undefined) return undefined;
  const ratio = unitSignal(analysis.subjectBackgroundRatio);
  return ratio === undefined ? regional : regional * Math.min(1, ratio / 0.5);
}

function positiveNumber(value: number | undefined): number | undefined {
  return typeof value === "number" && Number.isFinite(value) && value > 0
    ? value
    : undefined;
}

function validCaptureTime(value: number | undefined): number | undefined {
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
