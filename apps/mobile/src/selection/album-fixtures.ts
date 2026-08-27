/**
 * Three pinned event fixtures for the album planner (EXPERT-PLAN M0: "≥3 event
 * fixtures with expected selected-photo IDs").
 *
 * These are SYNTHETIC and say so. Nobody's actual photographs are in this
 * repository, so the honest thing is to reproduce the SHAPE of the owner's
 * library — the shape every measurement in `docs/album-selection-audit.md` and
 * `CX-19-QUALITY-GATE-MEASUREMENT.md` keeps running into — rather than to
 * invent pretty numbers:
 *
 *   - Mostly GROUP shots. Background faces are legitimately soft from depth of
 *     field, which is why every hard sharpness gate ever tested here cost real
 *     selections. `avu` (the infant) appears in most frames of two fixtures.
 *   - An infant across TWO YEARS, so the same person changes appearance while
 *     places, poses and companions rotate.
 *   - Bursts and reframes that the upstream take-grouper did NOT collapse:
 *     pairs sitting at cosine ≈ 0.95, above the 0.92 duplicate bar, in separate
 *     shot groups. Those are the ones the selector itself has to refuse.
 *   - Scarce people (one frame of `gran`), an isolated late moment, sleeping
 *     frames, mid-blinks, and a low-quality tail near the floor.
 *
 * They are generated, not hand-listed, so the corpus is 64 candidates per event
 * at the production shape (64 → 24) without 192 literals to maintain. The
 * generator is seeded and pure: the same call returns the same photographs
 * forever, and `album-fixtures.test.ts` asserts the similarity BANDS the
 * fixtures are supposed to contain before it asserts anything about selection.
 */

import type { PlannerCandidate } from "./album-planner";

export type AlbumFixture = {
  name: string;
  /** How many photos the album asks for. */
  target: number;
  candidates: PlannerCandidate[];
};

const EMBEDDING_SIZE = 32;
const EMBEDDING_SPACE = "tinyclip-vit-8m16-yfcc15m-v1";
const MINUTE = 60 * 1_000;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;

/**
 * Sigma of the isotropic noise added to a scene direction. For a unit base in
 * d dimensions, cos(base, base+noise) ≈ 1/sqrt(1 + sigma²·d), and two
 * independent variants of one base land near the square of that. The three
 * values below are chosen to straddle the 0.92 duplicate bar from both sides;
 * the test measures them rather than trusting this comment.
 */
const WITHIN_MOMENT_SIGMA = 0.07;
const REFRAME_SIGMA = 0.04;

/** mulberry32 — 32 bits of state, identical output on every engine. */
function seeded(seed: number) {
  let state = seed >>> 0;
  return () => {
    state = (state + 0x6d2b79f5) >>> 0;
    let value = Math.imul(state ^ (state >>> 15), 1 | state);
    value = (value + Math.imul(value ^ (value >>> 7), 61 | value)) ^ value;
    return ((value ^ (value >>> 14)) >>> 0) / 4294967296;
  };
}

function normalize(values: number[]): number[] {
  const magnitude = Math.sqrt(values.reduce((sum, value) => sum + value * value, 0));
  return magnitude === 0 ? values : values.map((value) => value / magnitude);
}

function gaussian(random: () => number) {
  // Box-Muller. One draw is enough; the second is discarded on purpose so the
  // stream position advances by exactly two per sample.
  const first = Math.max(random(), Number.EPSILON);
  const second = random();
  return Math.sqrt(-2 * Math.log(first)) * Math.cos(2 * Math.PI * second);
}

/**
 * A scene direction with a shared "photograph-ness" component, so two unrelated
 * frames sit near cosine 0.5 the way real CLIP embeddings do — not near zero
 * the way random unit vectors would.
 */
function sceneVector(random: () => number): number[] {
  const common = 0.72;
  const specific = 0.69;
  const shared = Array.from({ length: EMBEDDING_SIZE }, (_, index) =>
    index % 2 === 0 ? 1 : -1,
  );
  const direction = normalize(
    Array.from({ length: EMBEDDING_SIZE }, () => gaussian(random)),
  );
  return normalize(
    shared.map(
      (value, index) =>
        (common * value) / Math.sqrt(EMBEDDING_SIZE) + specific * direction[index],
    ),
  );
}

function variant(base: readonly number[], sigma: number, random: () => number): number[] {
  return normalize(base.map((value) => value + sigma * gaussian(random)));
}

type MomentSpec = {
  /** Becomes the media-id prefix, so a fixture diff reads as English. */
  slug: string;
  count: number;
  /** People in each frame; cycled. An empty entry is a scenery frame. */
  casts: string[][];
  place: string;
  offsetMs: number;
  spacingMs: number;
  poseCluster?: string;
  category?: string;
  quality: [number, number];
  /** Index within the moment whose successor is a reframe of it. */
  reframeAt?: number[];
  sleeping?: boolean;
  blink?: number[];
  /** Below the planner's face-sharpness floor. */
  softFaceAt?: number[];
};

function buildEvent(
  name: string,
  seed: number,
  start: number,
  target: number,
  moments: MomentSpec[],
): AlbumFixture {
  const random = seeded(seed);
  const candidates: PlannerCandidate[] = [];
  for (const moment of moments) {
    const base = sceneVector(random);
    let previous: number[] | undefined;
    for (let index = 0; index < moment.count; index += 1) {
      const cast = moment.casts[index % moment.casts.length];
      const isReframe = previous !== undefined && (moment.reframeAt ?? []).includes(index - 1);
      const embedding = isReframe
        ? variant(previous!, REFRAME_SIGMA, random)
        : variant(base, WITHIN_MOMENT_SIGMA, random);
      previous = embedding;
      const mediaId = `${name}-${moment.slug}-${String(index).padStart(2, "0")}`;
      const [low, high] = moment.quality;
      const quality = round(low + (high - low) * random());
      const softFace = (moment.softFaceAt ?? []).includes(index);
      const blink = (moment.blink ?? []).includes(index);
      candidates.push({
        mediaId,
        quality,
        capturedAt: start + moment.offsetMs + index * moment.spacingMs,
        placeKey: moment.place,
        personIds: cast,
        embedding,
        embeddingSpace: EMBEDDING_SPACE,
        comparisonClass: moment.category ?? categoryFor(cast),
        category: moment.category ?? categoryFor(cast),
        // Upstream hands the planner ONE winner per take, so every candidate
        // carries its own shot group. Reframes share a pose family instead —
        // exactly how `select-best-shots` labels a frame it could not collapse.
        shotGroup: `take:${mediaId}`,
        poseFamily: isReframe
          ? `${name}-${moment.slug}-reframe-${index - 1}`
          : (moment.reframeAt ?? []).includes(index)
            ? `${name}-${moment.slug}-reframe-${index}`
            : `take:${mediaId}`,
        poseCluster: moment.poseCluster,
        faceSharpness: cast.length === 0 ? undefined : round(softFace ? 0.05 : 0.2 + 0.5 * random()),
        headSharpness: cast.length === 0 ? undefined : round(0.15 + 0.5 * random()),
        smile: cast.length === 0 ? undefined : round(random()),
        eyesOpen: cast.length === 0 ? undefined : round(blink ? 0.1 : 0.6 + 0.4 * random()),
        aesthetic: round(-0.05 + 0.12 * random()),
        composed: round(-0.05 + 0.12 * random()),
        cleanFrame: round(-0.05 + 0.12 * random()),
        awake: round(moment.sleeping ? -0.06 : 0.02 + 0.06 * random()),
        sleeping: round(moment.sleeping ? 0.08 : -0.04 + 0.02 * random()),
      });
    }
  }
  return { name, target, candidates };
}

function categoryFor(cast: readonly string[]) {
  if (cast.length === 0) return "detail";
  if (cast.length === 1) return "portrait";
  if (cast.length === 2) return "couple";
  return "group";
}

/**
 * Six decimals, not four. A coarser grid manufactures EXACT ties between
 * independently drawn measurements, and a tie is not a neutral event here:
 * `qualityStanding` shares the midrank between equal values and every gain
 * comparison falls through to a media-id sort. The neighbouring measurement of
 * `select-best-shots`'s framing tie-break found the same trap from the other
 * side — its corpus quantized quality to 1e-2 and so measured its own rounding
 * rather than the scorer. `album-fixtures.test.ts` asserts zero exact ties.
 */
function round(value: number) {
  return Math.round(value * 1_000_000) / 1_000_000;
}

const BIRTHDAY_START = Date.UTC(2026, 1, 14, 10, 0, 0);
const TWO_YEARS_START = Date.UTC(2024, 7, 3, 8, 0, 0);
const TRIP_START = Date.UTC(2025, 10, 6, 7, 0, 0);

/**
 * Event 1 — a first birthday at home. The group-shot case: 64 takes, most of
 * them three to five people, one frame of `gran`, one isolated late moment, and
 * four reframe pairs the take-grouper let through.
 */
function birthday(): AlbumFixture {
  return buildEvent("birthday", 0x5eed01, BIRTHDAY_START, 24, [
    {
      slug: "arrival", count: 8, place: "home-living", offsetMs: 0, spacingMs: 4 * MINUTE,
      poseCluster: "standing", quality: [0.45, 0.82], reframeAt: [2],
      casts: [
        ["avu", "mum", "dad"], ["avu", "mum", "dad", "uncle"], ["mum", "dad", "uncle"],
        ["avu", "mum"], ["avu", "dad", "uncle", "cousin"], ["avu", "mum", "cousin"],
        ["mum", "uncle", "cousin"], ["avu", "mum", "dad", "uncle", "cousin"],
      ],
    },
    {
      slug: "cake", count: 12, place: "home-kitchen", offsetMs: 70 * MINUTE, spacingMs: 90 * 1_000,
      poseCluster: "seated", quality: [0.5, 0.93], reframeAt: [3, 8], blink: [6],
      casts: [
        ["avu", "mum", "dad"], ["avu", "mum", "dad", "uncle", "cousin"],
        ["avu", "mum"], ["avu", "dad"], ["avu", "mum", "dad", "cousin"],
        ["avu", "cousin"],
      ],
    },
    {
      slug: "presents", count: 12, place: "home-living", offsetMs: 110 * MINUTE, spacingMs: 2 * MINUTE,
      poseCluster: "seated", quality: [0.42, 0.88], softFaceAt: [5, 9],
      casts: [
        ["avu", "cousin"], ["avu", "mum", "cousin"], ["avu", "dad", "uncle"],
        ["avu", "mum", "dad", "uncle", "cousin"], ["avu"], ["avu", "uncle"],
      ],
    },
    {
      slug: "garden", count: 14, place: "home-garden", offsetMs: 170 * MINUTE, spacingMs: 3 * MINUTE,
      poseCluster: "standing", quality: [0.5, 0.9], reframeAt: [4, 10],
      casts: [
        ["avu", "mum", "dad", "uncle", "cousin"], ["avu", "mum", "dad"],
        ["mum", "dad"], ["avu", "cousin"], ["avu", "mum", "dad", "cousin"],
        ["dad", "uncle"], ["avu", "mum", "uncle"],
      ],
    },
    {
      slug: "portraits", count: 9, place: "home-garden", offsetMs: 230 * MINUTE, spacingMs: 45 * 1_000,
      poseCluster: "close", quality: [0.55, 0.95], blink: [3],
      casts: [["avu"], ["avu", "mum"], ["avu", "dad"]],
    },
    {
      slug: "gran", count: 1, place: "home-living", offsetMs: 250 * MINUTE, spacingMs: MINUTE,
      quality: [0.31, 0.33], softFaceAt: [0], casts: [["gran", "avu"]],
    },
    {
      slug: "details", count: 6, place: "home-kitchen", offsetMs: 275 * MINUTE, spacingMs: 2 * MINUTE,
      category: "detail", quality: [0.44, 0.78], casts: [[]],
    },
    {
      // Deliberately alone in time: nothing else within the 30-minute isolation
      // window, so this is the rare moment a diversity rule must not erase.
      slug: "nightwalk", count: 2, place: "street", offsetMs: 9 * HOUR, spacingMs: 12 * MINUTE,
      poseCluster: "standing", quality: [0.4, 0.62], sleeping: true,
      casts: [["avu", "dad"], ["avu", "mum"]],
    },
  ]);
}

/**
 * Event 2 — the infant across two years. Same person, drifting appearance,
 * monthly cadence, rotating places and companions. This is the fixture that
 * catches a selector collapsing 24 months into one good afternoon.
 */
function twoYears(): AlbumFixture {
  const moments: MomentSpec[] = [];
  const casts = [
    [["avu", "mum"], ["avu", "mum", "dad"], ["avu"]],
    [["avu", "dad"], ["avu", "mum", "dad", "gran"], ["avu", "gran"]],
    [["avu", "mum", "cousin"], ["avu", "cousin"], ["avu", "mum", "dad"]],
    [["avu"], ["avu", "mum"], ["avu", "dad", "uncle"]],
  ];
  const places = ["home-living", "home-garden", "grandparents", "park", "clinic", "beach"];
  for (let month = 0; month < 16; month += 1) {
    moments.push({
      slug: `m${String(month).padStart(2, "0")}`,
      count: 4,
      casts: casts[month % casts.length],
      place: places[month % places.length],
      offsetMs: month * 45 * DAY,
      spacingMs: 6 * MINUTE,
      poseCluster: ["held", "seated", "standing", "close"][month % 4],
      quality: [0.38 + (month % 3) * 0.05, 0.72 + (month % 5) * 0.045],
      reframeAt: month % 4 === 0 ? [1] : undefined,
      sleeping: month % 7 === 3,
      blink: month % 5 === 2 ? [2] : undefined,
      softFaceAt: month % 6 === 1 ? [3] : undefined,
    });
  }
  return buildEvent("twoyears", 0x5eed02, TWO_YEARS_START, 24, moments);
}

/**
 * Event 3 — a four-day trip. The case where place and day coverage compete with
 * quality: five locations, scenery with nobody in it, and a burst of near
 * identical sunset frames that must contribute exactly one photograph.
 */
function trip(): AlbumFixture {
  return buildEvent("trip", 0x5eed03, TRIP_START, 24, [
    {
      slug: "depart", count: 6, place: "airport", offsetMs: 0, spacingMs: 8 * MINUTE,
      poseCluster: "standing", quality: [0.4, 0.75],
      casts: [["avu", "mum", "dad"], ["avu", "mum"], ["mum", "dad"]],
    },
    {
      slug: "beach", count: 14, place: "beach-north", offsetMs: 7 * HOUR, spacingMs: 4 * MINUTE,
      poseCluster: "standing", quality: [0.48, 0.92], reframeAt: [3, 7, 11],
      casts: [
        ["avu", "mum", "dad"], ["avu", "mum"], ["avu", "dad"], [],
        ["avu", "mum", "dad", "friend"], ["mum", "dad", "friend"],
      ],
    },
    {
      slug: "sunset", count: 10, place: "beach-north", offsetMs: 12 * HOUR, spacingMs: 20 * 1_000,
      category: "detail", quality: [0.6, 0.94], reframeAt: [0, 1, 2, 3, 4, 5, 6, 7, 8],
      casts: [[]],
    },
    {
      slug: "market", count: 12, place: "market", offsetMs: DAY + 5 * HOUR, spacingMs: 3 * MINUTE,
      poseCluster: "standing", quality: [0.42, 0.86], softFaceAt: [4, 8],
      casts: [["avu", "mum", "dad", "friend"], ["avu", "friend"], ["mum", "friend"], []],
    },
    {
      slug: "fort", count: 10, place: "fort", offsetMs: 2 * DAY + 4 * HOUR, spacingMs: 5 * MINUTE,
      poseCluster: "standing", quality: [0.5, 0.9], reframeAt: [2],
      casts: [["avu", "mum", "dad"], [], ["avu", "dad", "friend"], ["avu", "mum", "friend"]],
    },
    {
      slug: "pool", count: 10, place: "hotel", offsetMs: 3 * DAY + 2 * HOUR, spacingMs: 90 * 1_000,
      poseCluster: "close", quality: [0.45, 0.88], blink: [5], sleeping: false,
      casts: [["avu", "mum"], ["avu"], ["avu", "dad"]],
    },
    {
      slug: "return", count: 2, place: "airport", offsetMs: 3 * DAY + 20 * HOUR, spacingMs: 15 * MINUTE,
      poseCluster: "seated", quality: [0.36, 0.55], sleeping: true,
      casts: [["avu", "mum"], ["avu", "dad"]],
    },
  ]);
}

export function albumFixtures(): AlbumFixture[] {
  return [birthday(), twoYears(), trip()];
}

/**
 * A digest of the photographs themselves, pinned next to the expected album.
 *
 * Without it the pin proves nothing: anyone could edit the generator, rerun the
 * regeneration script, and land a "passing" test that has quietly changed both
 * sides of the comparison. With it, a corpus change fails LOUDLY and has to be
 * re-pinned on purpose. FNV-1a over the fields the planner actually reads.
 */
export function fixtureDigest(fixture: AlbumFixture): string {
  let hash = 0x811c9dc5;
  const eat = (text: string) => {
    for (let index = 0; index < text.length; index += 1) {
      hash ^= text.charCodeAt(index);
      hash = Math.imul(hash, 0x01000193) >>> 0;
    }
  };
  eat(`${fixture.name}|${fixture.target}|`);
  for (const candidate of fixture.candidates) {
    eat(
      [
        candidate.mediaId, candidate.quality, candidate.capturedAt, candidate.placeKey,
        (candidate.personIds ?? []).join("+"), candidate.poseCluster, candidate.poseFamily,
        candidate.shotGroup, candidate.category, candidate.faceSharpness,
        candidate.headSharpness, candidate.smile, candidate.eyesOpen, candidate.aesthetic,
        candidate.composed, candidate.cleanFrame, candidate.awake, candidate.sleeping,
        (candidate.embedding ?? []).map((value) => value.toFixed(6)).join(","),
      ].join("|"),
    );
  }
  return hash.toString(16).padStart(8, "0");
}
