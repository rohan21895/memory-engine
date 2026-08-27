/**
 * Does the pose framing tie-break ever actually decide a photo?
 *
 * `select-best-shots.ts` consults body framing in exactly ONE place:
 * `framingTieWinner`, which may promote a later frame of a take over the
 * current leader. Nothing else in the codebase reads `BodyCoverage`. So the
 * whole question is how often that one promotion happens on a real library.
 *
 * The answer factors into two independent probabilities, and this file measures
 * both against the REAL code rather than a description of it:
 *
 *     P(framing moves a take's winner)
 *       = P(the take reaches an exact tie)          <- part 2, the gate
 *       x P(framing then flips it | exact tie)      <- part 3, the ceiling
 *
 * The factorisation matters because the two numbers have completely different
 * error bars. The second one can be measured exactly: force the tie, run the
 * real selector with and without the field, diff. The first one cannot be
 * observed on this machine at all -- it needs pixels -- so instead of inventing
 * a tie rate this file measures the QUANTITY the tie is taken on, `quality`,
 * and reports how far apart two frames of one burst actually land. A gap of
 * 1e-3 and a gap of 0 are the difference between a live feature and dead code,
 * and the gap is measurable without ever guessing how often a real burst ties.
 *
 * Part 1 needs no simulation at all: the single-person gate is
 * `analysis.faceCount === 1`, and the owner's own library says how often that
 * holds.
 *
 * WHAT IS SIMULATED, AND WHY THE CONCLUSION SURVIVES IT
 *
 *   - Face counts per photo come from the real library (part 1), not a guess.
 *   - Pixel measurements are simulated: `sharpness`, `exposure` and friends
 *     need image data. But the claim drawn from them is only "two independently
 *     measured frames do not produce the SAME IEEE-754 double", and that claim
 *     gets weaker, never stronger, the more the simulation smooths reality: the
 *     narrower the spread between burst frames, the closer they come to tying.
 *     Part 2 therefore reports the measured gap at several spreads, down to a
 *     spread far tighter than any real camera, and the honest reading is the
 *     trend, not one number.
 *   - `BodyCoverage` values are simulated, but never written by hand: every one
 *     is produced by calling the real `bodyCoverage()` on keypoints a
 *     single-person MoveNet could return, so no fixture can express a coverage
 *     the extractor cannot.
 *   - Part 3's forced tie is not a modelling choice. It is the condition the
 *     feature's own unit test uses -- one shared `analysis` OBJECT across both
 *     frames -- and it is the ONLY condition under which the code fires.
 *
 * Run from the repository root with Node 22 or newer:
 *
 *   node --experimental-strip-types scratch/framing-tiebreak-rate/measure.ts \
 *     /path/to/obs.jsonl
 */

// @ts-expect-error The Expo app deliberately does not ship Node declarations.
import { readFileSync } from "node:fs";
// @ts-expect-error The Expo app deliberately does not ship Node declarations.
import process from "node:process";

import type { PickedPhoto } from "../../apps/mobile/src/import/picked-photo";
import { bodyCoverage, compareFramingCompleteness, type BodyCoverage, type BodyFraming } from "../../apps/mobile/src/selection/pose-framing.ts";
import { KP, letterboxLayout } from "../../apps/mobile/src/selection/pose-framing-test-deps.ts";
import type { FaceSignal, QualitySignals } from "../../apps/mobile/src/selection/quality-signals";
import { selectBestShots, qualityScoreForSignals } from "../../apps/mobile/src/selection/select-best-shots.ts";

type Photo = PickedPhoto & {
  embedding?: number[];
  analysis?: QualitySignals;
  bodyCoverage?: BodyCoverage;
};

const WIDTH = 4_000;
const HEIGHT = 3_000;
const PIXELS = WIDTH * HEIGHT;
/** `select-best-shots.ts`: bands are `Math.round(quality / SMILE_TIE_BAND)`. */
const SMILE_TIE_BAND = 0.02;

function main(): void {
  const obsPath = process.argv[2];
  realLibraryFaceCounts(obsPath);
  exactTieGate();
  flipCeilingGivenATie();
  directionOfEffect();
}

// --- Part 1: the single-person gate, on the owner's own library -------------
//
// `singleSubjectFraming` returns the inert value unless `faceCount === 1`, and
// that guard is correct: MoveNet Lightning fits ONE person, so on a group photo
// the coverage describes whichever body it locked onto and says nothing about
// the others. The cost of being correct is that the signal is silent on every
// group photo, and this library is mostly group photos.
//
// `obs.jsonl` is one line per detected+embedded face with its `assetId`, so
// faces per photo is a direct count, not a model.

function realLibraryFaceCounts(obsPath: string | undefined): void {
  console.log("== 1. how often MoveNet's single fit describes the whole photo ==");
  if (!obsPath) {
    console.log("   (skipped: pass the path to obs.jsonl as argv[2])\n");
    return;
  }
  const facesPerPhoto = new Map<string, number>();
  for (const line of readFileSync(obsPath, "utf8").split("\n")) {
    if (line.length === 0) continue;
    const { assetId } = JSON.parse(line) as { assetId: string };
    facesPerPhoto.set(assetId, (facesPerPhoto.get(assetId) ?? 0) + 1);
  }
  const counts = [...facesPerPhoto.values()];
  const photos = counts.length;
  const solo = counts.filter((count) => count === 1).length;
  const faces = counts.reduce((sum, count) => sum + count, 0);
  const facesInGroups = counts
    .filter((count) => count > 1)
    .reduce((sum, count) => sum + count, 0);

  console.log(`   ${faces} faces over ${photos} photos that have any face`);
  console.log(
    `   exactly one face: ${solo} (${percent(solo / photos)}) <- the only photos the tie-break may speak about`,
  );
  console.log(
    `   two or more:      ${photos - solo} (${percent((photos - solo) / photos)}) <- silenced by the faceCount===1 guard`,
  );
  console.log(
    `   ${facesInGroups} of ${faces} faces (${percent(facesInGroups / faces)}) are in a photo where the pose fit cannot speak for them`,
  );
  // Both frames of a tied take must pass, and near-duplicate frames of one
  // moment almost always agree on face count, so this is the per-take ceiling.
  console.log(
    `   => the framing signal is inert on ${percent((photos - solo) / photos)} of the library's people photos before any tie is considered\n`,
  );
}

// --- Part 2: the gate. Can two measured frames tie EXACTLY? ------------------
//
// `framingTieWinner` walks the sorted candidates and stops at the first one
// `compareMeasuredSignals` does not call equal. That comparator's third key is
// the raw `quality` double. Nothing on the path from `enhancedQualityScore` to
// this comparison rounds it -- `roundScore` is applied only to the album's POOL
// output, after the winner is already chosen -- so the tie is exact double
// equality between two independent measurements.
//
// This measures the actual gap. Each take is a burst: one scene, then per-frame
// measurement noise on every input the scorer reads. The score comes from the
// real `qualityScoreForSignals`, the documented public seam onto the same
// scorer `selectBestShots` uses, so the arithmetic under test is production's.

function exactTieGate(): void {
  console.log("== 2. the gate: do two frames of a burst ever tie EXACTLY? ==");
  console.log("   spread  |  takes  | exact ties | median gap | smallest gap seen");
  console.log("   --------|---------|------------|------------|------------------");

  // Real burst frames differ by percent-scale amounts. The last two rows are
  // far below anything a camera produces and exist to show the trend: even a
  // spread of one part in ten million does not reach an exact tie.
  for (const spread of [0.05, 0.01, 0.002, 1e-4, 1e-7]) {
    const random = mulberry32(0xf7a3_1c0d);
    const gaps: number[] = [];
    let ties = 0;
    const takes = 20_000;
    for (let take = 0; take < takes; take += 1) {
      const scene = randomScene(random);
      const left = scoreOf(burstFrame(scene, random, spread));
      const right = scoreOf(burstFrame(scene, random, spread));
      if (tiedOnEveryMeasuredSignal(left, right)) ties += 1;
      gaps.push(Math.abs(left - right));
    }
    gaps.sort((a, b) => a - b);
    console.log(
      `   ${spread.toExponential(0).padEnd(7)} | ${String(takes).padStart(7)} | ${String(ties).padStart(10)} | ${gaps[gaps.length >> 1].toExponential(2)}   | ${gaps[0].toExponential(2)}`,
    );
  }

  // SABOTAGE GUARD. A row of zeros proves nothing if `tiedOnEveryMeasuredSignal`
  // can never return true. Feed it the one case that MUST tie -- the same frame
  // twice, which is what two byte-identical copies of a photo produce -- and it
  // has to report a tie every time.
  const sabotage = mulberry32(0xf7a3_1c0d);
  let forced = 0;
  for (let take = 0; take < 20_000; take += 1) {
    const scene = randomScene(sabotage);
    if (tiedOnEveryMeasuredSignal(scoreOf(scene), scoreOf(scene))) forced += 1;
  }
  console.log(
    `   sabotage guard: with the SAME measurement on both sides the detector reports ${forced}/20000 ties, so the zeros above are real`,
  );

  // How far from a tie, in the only unit that matters. `Number.EPSILON` scaled
  // to the score's magnitude is one representable step; a tie needs zero steps.
  const stepsRandom = mulberry32(0x51e_9a11);
  let closest = Number.POSITIVE_INFINITY;
  for (let take = 0; take < 20_000; take += 1) {
    const scene = randomScene(stepsRandom);
    const left = scoreOf(burstFrame(scene, stepsRandom, 0.01));
    const right = scoreOf(burstFrame(scene, stepsRandom, 0.01));
    closest = Math.min(closest, ulpsApart(left, right));
  }
  console.log(
    `   at a realistic 1% spread the CLOSEST of 20000 pairs was still ${closest.toExponential(2)} representable steps apart`,
  );

  // The other way a double can repeat: clamp01 saturation, where two different
  // photos both land on exactly 0 or exactly 1.
  const satRandom = mulberry32(0xc1a3_9012);
  let saturated = 0;
  for (let take = 0; take < 20_000; take += 1) {
    const score = scoreOf(randomScene(satRandom));
    if (score === 0 || score === 1) saturated += 1;
  }
  console.log(
    `   scores landing exactly on a clamp bound (the other route to a repeat): ${saturated}/20000`,
  );

  const scene = randomScene(mulberry32(1));
  const nudged = { ...scene, faceSharpness: scene.faceSharpness + 1e-12 };
  console.log(
    `   a 1e-12 change in one input moves the score by ${Math.abs(scoreOf(scene) - scoreOf(nudged)).toExponential(2)} -- the comparison is bit equality, not "close"\n`,
  );
}

/** Representable doubles between two scores. Zero is the only tie. */
function ulpsApart(left: number, right: number): number {
  return Math.abs(left - right) / Math.max(Number.MIN_VALUE, ulp(left));
}

function ulp(value: number): number {
  const next = new Float64Array(1);
  const bits = new BigUint64Array(next.buffer);
  next[0] = Math.abs(value);
  bits[0] += 1n;
  return next[0] - Math.abs(value);
}

/** Exactly `compareMeasuredSignals`, on two frames that share pixels and category. */
function tiedOnEveryMeasuredSignal(left: number, right: number): boolean {
  return (
    Math.round(left / SMILE_TIE_BAND) === Math.round(right / SMILE_TIE_BAND) &&
    left === right
  );
}

type Scene = {
  sharpness: number;
  faceSharpness: number;
  subjectSharpness: number;
  subjectBackgroundRatio: number;
  exposure: number;
  clippedFraction: number;
  eyesOpen: number;
  smile: number;
};

function randomScene(random: () => number): Scene {
  return {
    sharpness: 0.2 + random() * 0.7,
    faceSharpness: 0.2 + random() * 0.7,
    subjectSharpness: 0.2 + random() * 0.7,
    subjectBackgroundRatio: 0.3 + random() * 0.6,
    exposure: 0.3 + random() * 0.4,
    clippedFraction: random() < 0.6 ? 0 : random() * 0.05,
    eyesOpen: 0.5 + random() * 0.5,
    smile: random(),
  };
}

/** One frame of that scene, measured independently: every input jitters. */
function burstFrame(scene: Scene, random: () => number, spread: number): Scene {
  const jitter = (value: number) => value * (1 + (random() - 0.5) * spread);
  return {
    sharpness: jitter(scene.sharpness),
    faceSharpness: jitter(scene.faceSharpness),
    subjectSharpness: jitter(scene.subjectSharpness),
    subjectBackgroundRatio: jitter(scene.subjectBackgroundRatio),
    exposure: jitter(scene.exposure),
    // Integer-count-over-integer-pixels, so it really does repeat exactly.
    clippedFraction: scene.clippedFraction,
    eyesOpen: jitter(scene.eyesOpen),
    smile: jitter(scene.smile),
  };
}

function scoreOf(scene: Scene): number {
  return qualityScoreForSignals({
    analysis: signalsFor(scene, 1),
    width: WIDTH,
    height: HEIGHT,
  });
}

function signalsFor(scene: Scene, faceCount: number): QualitySignals {
  const faces: FaceSignal[] = Array.from({ length: faceCount }, () => ({
    areaRatio: 0.08,
    eyesOpen: scene.eyesOpen,
    smile: scene.smile,
    cutAtEdge: false,
  }));
  return {
    sharpness: scene.sharpness,
    faceSharpness: scene.faceSharpness,
    subjectSharpness: scene.subjectSharpness,
    subjectBackgroundRatio: scene.subjectBackgroundRatio,
    exposure: scene.exposure,
    clippedFraction: scene.clippedFraction,
    faces,
    faceCount,
    largestFaceAreaRatio: 0.08,
    anyFaceCutAtEdge: false,
    isScreenshotOrDocument: false,
    category: faceCount === 1 ? "portrait" : faceCount === 2 ? "couple" : "group",
  };
}

// --- Part 3: the ceiling. Given a tie, how often does framing flip it? -------
//
// Hand the tie over for free -- both frames of a take share ONE `analysis`
// object, so their scores are equal to the bit -- and then let the REAL
// selector decide. Two runs per take, identical except that the second has
// `bodyCoverage` stripped from every photo, and the answer is whether the
// album's chosen media id moved.
//
// This is the number the feature would deliver if exact ties were free. Part 2
// says what to multiply it by.

function flipCeilingGivenATie(): void {
  console.log("== 3. the ceiling: given a free exact tie, how often does framing flip the winner? ==");
  const faceCountDraw = realisticFaceCount();

  for (const label of ["single-face photos only", "real library face mix"] as const) {
    const random = mulberry32(0x0b0d_c0de);
    const takes = 20_000;
    let flipped = 0;
    let bothReadable = 0;
    let silencedByGroup = 0;
    const moves = new Map<string, number>();

    for (let take = 0; take < takes; take += 1) {
      const faceCount =
        label === "single-face photos only" ? 1 : faceCountDraw(random);
      if (faceCount === 0) continue;
      const analysis = signalsFor(randomScene(random), faceCount);
      const frames = [randomCoverage(random), randomCoverage(random)];
      if (faceCount > 1) silencedByGroup += 1;
      if (frames.every((frame) => frame.framing !== "unknown") && faceCount === 1) {
        bothReadable += 1;
      }

      const withFraming = winnerOf(frames, analysis, true);
      const without = winnerOf(frames, analysis, false);
      if (withFraming !== without) {
        flipped += 1;
        const won = frames[Number(withFraming.slice(-1))];
        const lost = frames[Number(without.slice(-1))];
        bump(moves, `${describe(lost)} -> ${describe(won)}`);
      }
    }

    console.log(`   [${label}]`);
    console.log(
      `     ${flipped} of ${takes} tied takes flipped (${percent(flipped / takes)})`,
    );
    console.log(
      `     both frames readable AND single-face: ${bothReadable} (${percent(bothReadable / takes)}); silenced as a group photo: ${silencedByGroup}`,
    );
    if (label === "real library face mix") {
      console.log("     top moves (frame that used to win -> frame that now wins):");
      for (const [move, count] of [...moves.entries()]
        .sort((a, b) => b[1] - a[1])
        .slice(0, 6)) {
        console.log(`       ${String(count).padStart(5)}  ${move}`);
      }
    }
  }

  // The one exact tie that DOES occur on a real device: the same photo saved
  // twice (a re-download, a WhatsApp copy). Same pixels means the same
  // measurements AND the same keypoints, so the coverages match too.
  const dupRandom = mulberry32(0x2b2b_2b2b);
  let dupFlips = 0;
  let dupReadable = 0;
  for (let take = 0; take < 20_000; take += 1) {
    const analysis = signalsFor(randomScene(dupRandom), 1);
    const coverage = randomCoverage(dupRandom);
    if (coverage.framing !== "unknown") dupReadable += 1;
    if (
      winnerOf([coverage, coverage], analysis, true) !==
      winnerOf([coverage, coverage], analysis, false)
    ) {
      dupFlips += 1;
    }
  }
  console.log("   [byte-identical duplicate: the only exact tie a real device produces]");
  console.log(
    `     ${dupFlips} of 20000 flipped (${dupReadable} of them with a readable pose on both sides)`,
  );
  console.log(
    "     identical pixels give identical coverage, and the comparator calls equal coverages equal",
  );
  console.log();
}

/**
 * Two near-duplicate frames, ONE shared analysis object, run through the real
 * `selectBestShots`. Identical embeddings collapse them into one take.
 */
function winnerOf(
  frames: BodyCoverage[],
  analysis: QualitySignals,
  framing: boolean,
): string {
  const photos: Photo[] = frames.map((coverage, index) => ({
    id: `frame-${index}`,
    uri: `file:///photos/frame-${index}.jpg`,
    filename: `frame-${index}.jpg`,
    width: WIDTH,
    height: HEIGHT,
    mimeType: "image/jpeg",
    source: "device-gallery",
    embedding: Array.from({ length: 64 }, (_, i) => (i === 0 ? 1 : 0)),
    analysis,
    bodyCoverage: framing ? coverage : undefined,
  }));
  return selectBestShots(photos, { count: 2 }).selected[0].media_id;
}

// --- Part 4: which pictures win, and which lose -----------------------------
//
// `compareFramingCompleteness` is a fixed, total order on readable coverages.
// Enumerate it rather than sampling it: this is the product claim, and it is
// decidable.

function directionOfEffect(): void {
  console.log("== 4. direction: which photo wins when it does fire ==");
  const kinds = readableCoverages();
  const beats = new Map<string, number>();
  for (const [leftName, left] of kinds) {
    for (const [rightName, right] of kinds) {
      if (leftName === rightName) continue;
      if (compareFramingCompleteness(left, right) < 0) bump(beats, leftName);
    }
  }
  console.log(`   over all ${kinds.length} reachable coverages, wins against how many others:`);
  for (const [name] of kinds) {
    console.log(
      `     ${name.padEnd(28)} beats ${String(beats.get(name) ?? 0).padStart(2)} / ${kinds.length - 1}`,
    );
  }

  const head = kinds.find(([name]) => name === "head (clean)")![1];
  const full = kinds.find(([name]) => name === "full (clean)")![1];
  console.log(
    `   a clean close-up of one face vs a clean full body: ${compareFramingCompleteness(head, full) < 0 ? "close-up wins" : "FULL BODY WINS"}`,
  );
  console.log(
    "   the order is depth-first (head < upper < half < threeQuarter < full),",
  );
  console.log(
    "   overridden only by cutAtJoint, which cannot occur above the hips. So a",
  );
  console.log(
    "   head-and-shoulders portrait can never outrank a whole body that is not",
  );
  console.log("   severed at a joint -- the tie-break is a full-body preference.\n");
}

function readableCoverages(): Array<[string, BodyCoverage]> {
  const entries: Array<[string, BodyCoverage]> = [];
  for (const framing of ["head", "upper", "half", "threeQuarter", "full"] as const) {
    for (const cut of [false, true]) {
      const coverage = coverageFor(framing, cut);
      if (coverage.framing !== framing) continue;
      const name = `${framing} (${describeCut(coverage)})`;
      if (!entries.some(([existing]) => existing === name)) entries.push([name, coverage]);
    }
  }
  return entries;
}

// --- Coverage fixtures, all produced by the real extractor ------------------

const TIER_POINTS = {
  head: { nose: [0.5, 0.1], l_eye: [0.46, 0.08], r_eye: [0.54, 0.08] },
  shoulders: { l_sho: [0.4, 0.25], r_sho: [0.6, 0.25] },
  hips: { l_hip: [0.42, 0.5], r_hip: [0.58, 0.5] },
  knees: { l_kne: [0.43, 0.7], r_kne: [0.57, 0.7] },
  ankles: { l_ank: [0.44, 0.9], r_ank: [0.56, 0.9] },
} as const satisfies Record<string, Record<string, readonly [number, number]>>;

const TIER_ORDER = ["head", "shoulders", "hips", "knees", "ankles"] as const;

/**
 * A coverage the real `bodyCoverage` produces for a body visible down to
 * `framing`, optionally with the deepest tier pushed onto the bottom edge so
 * the extractor calls it cut.
 */
function coverageFor(framing: BodyFraming, cutAtEdge: boolean): BodyCoverage {
  const depth = ["head", "upper", "half", "threeQuarter", "full"].indexOf(framing);
  const visible: Record<string, readonly [number, number]> = {};
  for (let tier = 0; tier <= depth; tier += 1) {
    const points = TIER_POINTS[TIER_ORDER[tier]] as Record<
      string,
      readonly [number, number]
    >;
    for (const [name, at] of Object.entries(points)) {
      visible[name] =
        cutAtEdge && tier === depth ? ([at[0], 0.97] as const) : at;
    }
  }
  const keypoints: Array<readonly [number, number]> = [];
  const scores: number[] = [];
  const { drawWidth, drawHeight } = letterboxLayout(WIDTH, HEIGHT);
  const size = Math.max(drawWidth, drawHeight);
  const spanX = drawWidth / size;
  const spanY = drawHeight / size;
  for (const name of Object.keys(KP) as Array<keyof typeof KP>) {
    const at = visible[name];
    keypoints[KP[name]] = [
      (at?.[0] ?? 0.5) * spanX + (1 - spanX) / 2,
      (at?.[1] ?? 0.5) * spanY + (1 - spanY) / 2,
    ];
    scores[KP[name]] = at ? 0.9 : 0.05;
  }
  return bodyCoverage(keypoints, scores, WIDTH, HEIGHT);
}

/**
 * One frame's coverage. `unknown` is deliberately common: MoveNet returns a
 * pose it cannot read on plenty of real photos, and the code treats that as no
 * opinion in both directions.
 */
function randomCoverage(random: () => number): BodyCoverage {
  if (random() < 0.35) return bodyCoverage([], [], WIDTH, HEIGHT);
  const framings: BodyFraming[] = ["head", "upper", "half", "threeQuarter", "full"];
  return coverageFor(
    framings[Math.floor(random() * framings.length)],
    random() < 0.4,
  );
}

function describe(coverage: BodyCoverage): string {
  return coverage.framing === "unknown"
    ? "unknown"
    : `${coverage.framing} (${describeCut(coverage)})`;
}

function describeCut(coverage: BodyCoverage): string {
  return coverage.cutAtJoint
    ? "cut at joint"
    : coverage.cutByFrame
      ? "cut by frame"
      : "clean";
}

/** The measured library histogram from part 1, as a sampler. */
function realisticFaceCount(): (random: () => number) => number {
  // Photos with no face at all never reach the tie-break's guard anyway; the
  // shares below are over face-bearing photos, which is what part 1 counted.
  const cumulative: Array<[number, number]> = [
    [1, 0.4541],
    [2, 0.7092],
    [3, 0.8167],
    [4, 0.887],
    [5, 0.9208],
    [6, 1],
  ];
  return (random) => {
    const draw = random();
    for (const [count, upTo] of cumulative) if (draw < upTo) return count;
    return 6;
  };
}

// --- Helpers ----------------------------------------------------------------

function bump(counts: Map<string, number>, key: string): void {
  counts.set(key, (counts.get(key) ?? 0) + 1);
}

function percent(fraction: number): string {
  return `${(fraction * 100).toFixed(2)}%`;
}

function mulberry32(seed: number): () => number {
  let state = seed >>> 0;
  return () => {
    state = (state + 0x6d2b79f5) >>> 0;
    let value = Math.imul(state ^ (state >>> 15), 1 | state);
    value = (value + Math.imul(value ^ (value >>> 7), 61 | value)) ^ value;
    return ((value ^ (value >>> 14)) >>> 0) / 4_294_967_296;
  };
}

main();
