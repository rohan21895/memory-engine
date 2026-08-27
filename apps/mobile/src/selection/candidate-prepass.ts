import type { PickedPhoto } from "../import/picked-photo";
// Explicit extension, matching face-index.ts: a bare specifier is fine for the
// bundler but unresolvable to Node's TS runner, and it left this module's own
// test suite unable to load at all.
// @ts-expect-error TypeScript bundler resolution normally omits source extensions.
import { decodeBlurhashGrayscale } from "./candidate-quality-probe.ts";
import type { MeasuredImageQuality } from "./image-quality";

/** Normal-sized picks keep the existing all-photo analysis path unchanged. */
export const CANDIDATE_PREPASS_THRESHOLD = 500;
/**
 * Measured on the beta Android device: the two TFLite runtimes serialize their
 * queues, so 64 is the largest safe deep-analysis pool inside the time budget.
 */
export const HEAVY_ANALYSIS_CANDIDATE_LIMIT = 64;

/**
 * EXPERT-PLAN §14/§21 ask for `B = clamp(5K, 96, 192)`, cap 256. That number
 * was written before anybody timed the stage it pays for.
 *
 * `docs/DEEP-ANALYSIS-TIMING.md` timed it: 148,837 ms for 64 photos, 2.33 s
 * each, 98% of it inside TinyCLIP. So the plan's own 96–192 is 3.7–7.5 minutes
 * of a user staring at a spinner, and today's 64 is not a taste decision at all
 * — it is 149 s divided by 2.33 s.
 *
 * The budget is therefore a PRICE, not a constant. Ask for what the album wants
 * and pay for what the device can actually afford inside the wall the build
 * already spends; the two clamp against each other. At today's measured price
 * this returns exactly 64 and nothing about the shipped build changes. When M2
 * caches signals or M3 makes inference cheaper, `msPerCandidate` falls and the
 * budget rises on its own — no flag day, no second constant to remember.
 *
 * The floor is today's 64, deliberately. A device that measures SLOWER than the
 * baseline must not quietly ship a 30-photo pool and a thinner album; a slow
 * build is a latency problem, and shortening the candidate pool is the one
 * response that damages the album instead.
 */
export const DEEP_ANALYSIS_MS_PER_CANDIDATE = 2_326;
/** The wall the deep stage already spends today: 148,837 ms for its 64 photos. */
export const DEEP_ANALYSIS_BUDGET_MS = 148_837;
export const CANDIDATE_BUDGET_MIN = 96;
export const CANDIDATE_BUDGET_MAX = 192;

export function candidateBudget(
  albumSize: number,
  msPerCandidate = DEEP_ANALYSIS_MS_PER_CANDIDATE,
): number {
  // A non-finite album size or price must land on today's 64, never on a NaN
  // that reaches `chooseHeavyAnalysisCandidates` and returns an empty pool.
  const size = Number.isFinite(albumSize) ? Math.max(0, Math.floor(albumSize)) : 0;
  const price = Number.isFinite(msPerCandidate)
    ? Math.max(1, msPerCandidate)
    : DEEP_ANALYSIS_MS_PER_CANDIDATE;
  const wanted = Math.min(
    Math.max(5 * size, CANDIDATE_BUDGET_MIN),
    CANDIDATE_BUDGET_MAX,
  );
  const affordable = Math.floor(DEEP_ANALYSIS_BUDGET_MS / price);
  return Math.max(
    HEAVY_ANALYSIS_CANDIDATE_LIMIT,
    Math.min(wanted, affordable),
  );
}

export type ProbedCandidate = {
  photo: PickedPhoto;
  quality: MeasuredImageQuality;
};

type RankedCandidate = ProbedCandidate & {
  qualityScore: number;
  timeBucket?: number;
  /**
   * Index of the gap-delimited moment this frame belongs to, or undefined when
   * it carries no usable capture time. See `MOMENT_GAP_MS`.
   */
  moment?: number;
  contentKey?: string;
  /**
   * This photo's people, already filtered to the ones that recur. Precomputed
   * here rather than inside the O(limit x remaining) scoring loop, for the same
   * reason `contentKey` is.
   */
  familiarPersonIds?: readonly string[];
};

export type PrepassOptions = {
  /**
   * Whether a face cluster belongs to somebody who recurs across the library,
   * rather than somebody who was simply also there.
   *
   * Required for the person axis to do anything, and absent it stays completely
   * inert -- callers that pass nothing get exactly the previous behaviour.
   *
   * It exists because the two obvious substitutes are both wrong. Face count
   * ranks a stranger photographed forty times at one wedding above a relative
   * photographed four times across two years. Rarity is worse: this library
   * resolved 17,699 faces into 2,173 clusters, roughly 2,161 of which hold
   * about six faces each, and most of those are other guests and passers-by --
   * so "prefer the rarest people" seeds an album with people the owner has
   * never met. See faces/person-recurrence.ts, which counts occasions.
   */
  isFamiliar?: (personId: string) => boolean;
  /**
   * Guarantee the least-covered moments a candidate slot (EXPERT-PLAN §14
   * `MOMENT_RESERVE`). Defaults on; pass `false` to measure the pool this gate
   * produced before reservations existed.
   */
  reserveMoments?: boolean;
};

const MAX_TIME_BUCKETS = 40;

/**
 * The silence between two moments.
 *
 * The time axis above buckets photos by equal COUNT, and equal count is not a
 * moment: a 200-frame burst spans three buckets on its own while a four-frame
 * moment shares a bucket with seventy frames of something else, and a bucket
 * the burst already "covers" hands that four-frame moment nothing at all. That
 * is the coverage the top-64 loses silently, because a lost moment leaves no
 * trace in the pool it never entered.
 *
 * Ten minutes, and the corpus chose it. Measured over the three pinned album
 * fixtures: the largest gap INSIDE a real moment is 15 min (the trip's two
 * departure-lounge frames) and the smallest gap BETWEEN two real moments is
 * 14 min (the birthday's portraits and the single frame of `gran`). They
 * overlap, so no threshold is clean and the question is which way to be wrong.
 * Splitting one moment in two costs one extra reserved slot for a frame that
 * was worth photographing anyway. Merging two costs the smaller one its
 * reservation — and `gran` appears once in sixty-four frames, which is exactly
 * the photograph this whole mechanism exists to protect. So: comfortably below
 * the 14-minute separation, and let the long moments split.
 */
const MOMENT_GAP_MS = 10 * 60 * 1_000;

/**
 * How much of the pool moment reservations may claim.
 *
 * A reservation buys BREADTH, and breadth taken to its limit is its own
 * failure: a filter holding more moments than the budget would spend every
 * slot on a different moment, one frame each, chosen on a blurhash — leaving
 * the planner unable to reject a blink because it holds no sibling frame to
 * reject it in favour of. §14 asks for reservoirs of two or more per moment
 * for the same reason. Half the pool guaranteed to distinct moments, half left
 * to the quality fill, which gives busy moments their alternates back.
 *
 * So the promise is precise: the pool holds at least
 * `min(moments, floor(budget/2))` DISTINCT moments. Beyond that the fill
 * decides, and a moment that misses out lost a ranked comparison rather than
 * disappearing into a time bucket a burst had already ticked off.
 */
export const MOMENT_RESERVE_FRACTION = 0.5;

/**
 * Grid the blurhash is reduced to before it becomes a bucket key.
 *
 * Coarse on purpose. The job is to make two frames of the same moment collide
 * while a different pose, framing or subject does not, so this reads the layout
 * of light in the frame and deliberately nothing finer. 4x3 over eight levels
 * is about as blunt as it can be while still separating a standing shot from a
 * seated one.
 */
const CONTENT_GRID_WIDTH = 4;
const CONTENT_GRID_HEIGHT = 3;
const CONTENT_LEVELS = 8;
/** The grid the probe's blurhash decodes to before block-averaging. */
const CONTENT_DECODE_WIDTH = 16;
const CONTENT_DECODE_HEIGHT = 12;

/**
 * A coarse "what does this frame look like" key, or undefined when unavailable.
 *
 * This is the whole point of the content axis: before the heavy models run, the
 * prepass knows a photo's time, its place and how sharp it is — but nothing
 * about what is IN it. Inside a single session, time and place are constant, so
 * those two axes go flat and the cap falls back to picking the sharpest frames,
 * which are exactly the ones bunched inside bursts. This gives it one cheap way
 * to tell two moments apart.
 */
function contentKeyForQuality(
  quality: MeasuredImageQuality | undefined,
): string | undefined {
  const blurhash = quality?.blurhash;
  if (!blurhash) return undefined;
  const gray = decodeBlurhashGrayscale(
    blurhash,
    CONTENT_DECODE_WIDTH,
    CONTENT_DECODE_HEIGHT,
  );
  if (!gray) return undefined;
  const blockWidth = CONTENT_DECODE_WIDTH / CONTENT_GRID_WIDTH;
  const blockHeight = CONTENT_DECODE_HEIGHT / CONTENT_GRID_HEIGHT;
  const cells: number[] = [];
  for (let row = 0; row < CONTENT_GRID_HEIGHT; row += 1) {
    for (let column = 0; column < CONTENT_GRID_WIDTH; column += 1) {
      let total = 0;
      for (let y = 0; y < blockHeight; y += 1) {
        for (let x = 0; x < blockWidth; x += 1) {
          const sampleY = row * blockHeight + y;
          const sampleX = column * blockWidth + x;
          total += gray[sampleY * CONTENT_DECODE_WIDTH + sampleX] ?? 0;
        }
      }
      const mean = total / (blockWidth * blockHeight);
      cells.push(Math.min(CONTENT_LEVELS - 1, Math.floor((mean / 256) * CONTENT_LEVELS)));
    }
  }
  return cells.join("");
}

/**
 * Choose a quality-biased subset while explicitly rewarding underrepresented
 * time windows and places. The returned photos retain input order so the
 * downstream planner receives the same ordering contract as an uncapped build.
 */
export function chooseHeavyAnalysisCandidates(
  probed: readonly ProbedCandidate[],
  limit = HEAVY_ANALYSIS_CANDIDATE_LIMIT,
  options: PrepassOptions = {},
): ProbedCandidate[] {
  const normalizedLimit = Math.max(0, Math.floor(limit));
  if (normalizedLimit === 0 || probed.length === 0) return [];

  const unique = deduplicate(probed);
  if (unique.length <= normalizedLimit) return unique;

  const ranked = addTimeBuckets(unique, normalizedLimit, options.isFamiliar);
  const selectedIds = new Set<string>();
  const timeCounts = new Map<number, number>();
  const placeCounts = new Map<string, number>();
  const contentCounts = new Map<string, number>();
  const personCounts = new Map<string, number>();
  const coveredMoments = new Set<number>();

  // User pins remain sovereign when a future edit flow feeds them into a
  // capped rebuild. The safety cap still wins if more than the limit are pinned.
  const pinned = ranked
    .filter(({ photo }) => photo.pinned)
    .sort(compareRankedCandidates)
    .slice(0, normalizedLimit);
  for (const candidate of pinned) {
    select(candidate, selectedIds, timeCounts, placeCounts, contentCounts, personCounts, coveredMoments);
  }

  const momentCount = new Set(
    ranked
      .map(({ moment }) => moment)
      .filter((moment): moment is number => moment !== undefined),
  ).size;
  const promise =
    options.reserveMoments === false
      ? 0
      : Math.min(momentCount, Math.floor(normalizedLimit * MOMENT_RESERVE_FRACTION));

  const remaining = ranked.filter(({ photo }) => !selectedIds.has(photo.id));
  while (selectedIds.size < normalizedLimit && remaining.length > 0) {
    // The reservation binds LATE, the way the planner's own scenery reserve
    // does: quality spends the pool freely until the slots left are only just
    // enough to keep the promise, and only then is the choice narrowed. So a
    // reservation costs nothing while there is room, and costs exactly what it
    // must when there is not.
    //
    // Note what the narrowing is: not "frames of moment X", but "frames of
    // moments with NOTHING selected yet". A reserved pick is by construction
    // the FIRST frame of its moment, so no reservation can ever be the route by
    // which a second, near-identical frame of a moment reaches deep analysis —
    // the failure `fd97b18` fixed one stage downstream. Which moment gets the
    // slot is the reservation's decision; WHICH FRAME of it is still
    // `candidatePriority`'s, and that already discounts a look already covered.
    const owed = Math.max(0, promise - coveredMoments.size);
    const reserving = owed >= normalizedLimit - selectedIds.size;
    const uncovered = reserving
      ? remaining.filter(
          ({ moment }) => moment !== undefined && !coveredMoments.has(moment),
        )
      : remaining;
    // `owed > 0` implies an uncovered moment exists and every frame of it is
    // still in `remaining`, so this is belt-and-braces against a future edit
    // breaking that invariant into an infinite loop rather than a live case.
    const pool = uncovered.length > 0 ? uncovered : remaining;

    let best = pool[0];
    let bestPriority = candidatePriority(
      best,
      timeCounts,
      placeCounts,
      contentCounts,
      personCounts,
    );

    for (let index = 1; index < pool.length; index += 1) {
      const priority = candidatePriority(
        pool[index],
        timeCounts,
        placeCounts,
        contentCounts,
        personCounts,
      );
      if (
        priority > bestPriority ||
        (priority === bestPriority && compareRankedCandidates(pool[index], best) < 0)
      ) {
        best = pool[index];
        bestPriority = priority;
      }
    }

    remaining.splice(remaining.indexOf(best), 1);
    select(best, selectedIds, timeCounts, placeCounts, contentCounts, personCounts, coveredMoments);
  }

  return unique.filter(({ photo }) => selectedIds.has(photo.id));
}

function deduplicate(probed: readonly ProbedCandidate[]): ProbedCandidate[] {
  const seen = new Set<string>();
  return probed.filter(({ photo }) => {
    if (seen.has(photo.id)) return false;
    seen.add(photo.id);
    return true;
  });
}

function addTimeBuckets(
  candidates: readonly ProbedCandidate[],
  limit: number,
  isFamiliar?: (personId: string) => boolean,
): RankedCandidate[] {
  const timed = candidates
    .filter(({ photo }) => validTimestamp(photo.creationTime))
    .slice()
    .sort((left, right) =>
      (left.photo.creationTime as number) -
        (right.photo.creationTime as number) ||
      left.photo.id.localeCompare(right.photo.id),
    );
  const bucketCount = Math.min(
    MAX_TIME_BUCKETS,
    limit,
    Math.max(1, Math.ceil(Math.sqrt(timed.length))),
  );
  const bucketById = new Map<string, number>();
  timed.forEach(({ photo }, index) => {
    bucketById.set(
      photo.id,
      Math.min(bucketCount - 1, Math.floor((index * bucketCount) / timed.length)),
    );
  });

  // Moments, from the same already-sorted list: a silence longer than the gap
  // ends one and starts the next. Cheap on purpose — capture time is the only
  // description of WHEN that exists before the heavy models run, and the
  // blurhash content key already covers what a frame looks like.
  const momentById = new Map<string, number>();
  let moment = -1;
  let previous: number | undefined;
  for (const { photo } of timed) {
    const time = photo.creationTime as number;
    if (previous === undefined || time - previous > MOMENT_GAP_MS) moment += 1;
    momentById.set(photo.id, moment);
    previous = time;
  }

  return candidates.map((candidate) => ({
    ...candidate,
    qualityScore: cheapQualityScore(candidate),
    timeBucket: bucketById.get(candidate.photo.id),
    moment: momentById.get(candidate.photo.id),
    // Decoded once here rather than inside the O(limit x remaining) scoring
    // loop, where the same hash would be decoded tens of thousands of times.
    contentKey: contentKeyForQuality(candidate.quality),
    familiarPersonIds: isFamiliar
      ? candidate.photo.personIds?.filter((personId) => isFamiliar(personId))
      : undefined,
  }));
}

function cheapQualityScore({ photo, quality }: ProbedCandidate): number {
  const sharpness = unitOrNeutral(quality.sharpness);
  const exposure = unitOrNeutral(quality.exposure);
  const clipping = unitOrNeutral(quality.clippedFraction, 0);
  const exposureBalance = 1 - Math.min(1, Math.abs(exposure - 0.5) * 2);
  const pixels =
    typeof photo.width === "number" &&
    typeof photo.height === "number" &&
    photo.width > 0 &&
    photo.height > 0
      ? photo.width * photo.height
      : 0;
  const resolution = Math.min(1, Math.log2(Math.max(1, pixels)) / 24);
  return (
    sharpness * 0.55 +
    exposureBalance * 0.2 +
    (1 - clipping) * 0.15 +
    resolution * 0.1
  );
}

function candidatePriority(
  candidate: RankedCandidate,
  timeCounts: ReadonlyMap<number, number>,
  placeCounts: ReadonlyMap<string, number>,
  contentCounts: ReadonlyMap<string, number>,
  personCounts: ReadonlyMap<string, number>,
): number {
  const timeCount =
    candidate.timeBucket === undefined
      ? undefined
      : timeCounts.get(candidate.timeBucket) ?? 0;
  const place = normalizedPlace(candidate.photo.placeKey);
  const placeCount = place ? placeCounts.get(place) ?? 0 : undefined;
  const contentCount = candidate.contentKey
    ? contentCounts.get(candidate.contentKey) ?? 0
    : undefined;

  // A first representative for a time window outweighs up to one full point
  // of quality. This guarantees broad chronology before taking repeats.
  const timeCoverage =
    timeCount === undefined ? 0 : timeCount === 0 ? 1.1 : 0.16 / (timeCount + 1);
  const placeCoverage =
    placeCount === undefined
      ? 0
      : placeCount === 0
        ? 0.45
        : 0.1 / (placeCount + 1);
  // Weighted just under time, and for the same reason: within one session every
  // candidate shares a time bucket and a place, so those two terms go constant
  // and stop discriminating at exactly the moment the album needs them to. The
  // first frame of an unseen look must then outweigh the quality gap between
  // two frames of the SAME look, which in a burst is a few hundredths -- so
  // this is the term that decides whether the planner receives sixty-four
  // photos of one pose or sixty-four different moments. Repeats fall off fast
  // so a genuinely richer look can still take a second slot.
  const contentCoverage =
    contentCount === undefined
      ? 0
      : contentCount === 0
        ? 0.9
        : 0.14 / (contentCount + 1);
  // A photo is worth as much as the least-covered person it brings, so one
  // frame holding three still-missing people satisfies all three at once.
  //
  // Weighted above every other axis, deliberately. An album missing a look is
  // duller; an album missing a PERSON is wrong, and it is the failure the
  // planner's own per-person floor exists to prevent -- a floor it cannot
  // enforce over people this gate already discarded. Ranked, not measured:
  // these constants are all hand-chosen, and the test asserts that recurring
  // people survive the cap rather than asserting any particular number.
  let leastCovered: number | undefined;
  for (const personId of candidate.familiarPersonIds ?? []) {
    const count = personCounts.get(personId) ?? 0;
    if (leastCovered === undefined || count < leastCovered) leastCovered = count;
  }
  const personCoverage =
    leastCovered === undefined ? 0 : leastCovered === 0 ? 1.2 : 0.12 / (leastCovered + 1);
  return (
    candidate.qualityScore +
    timeCoverage +
    placeCoverage +
    contentCoverage +
    personCoverage
  );
}

function select(
  candidate: RankedCandidate,
  selectedIds: Set<string>,
  timeCounts: Map<number, number>,
  placeCounts: Map<string, number>,
  contentCounts: Map<string, number>,
  personCounts: Map<string, number>,
  coveredMoments: Set<number>,
): void {
  selectedIds.add(candidate.photo.id);
  if (candidate.moment !== undefined) coveredMoments.add(candidate.moment);
  for (const personId of candidate.familiarPersonIds ?? []) {
    personCounts.set(personId, (personCounts.get(personId) ?? 0) + 1);
  }
  if (candidate.timeBucket !== undefined) {
    timeCounts.set(
      candidate.timeBucket,
      (timeCounts.get(candidate.timeBucket) ?? 0) + 1,
    );
  }
  const place = normalizedPlace(candidate.photo.placeKey);
  if (place) placeCounts.set(place, (placeCounts.get(place) ?? 0) + 1);
  if (candidate.contentKey) {
    contentCounts.set(
      candidate.contentKey,
      (contentCounts.get(candidate.contentKey) ?? 0) + 1,
    );
  }
}

function compareRankedCandidates(
  left: RankedCandidate,
  right: RankedCandidate,
): number {
  return (
    right.qualityScore - left.qualityScore ||
    left.photo.id.localeCompare(right.photo.id)
  );
}

function normalizedPlace(value: string | undefined): string | undefined {
  const normalized = value?.trim();
  return normalized ? normalized : undefined;
}

function validTimestamp(value: number | undefined): value is number {
  return typeof value === "number" && Number.isFinite(value) && value > 0;
}

function unitOrNeutral(value: number | undefined, neutral = 0.5): number {
  return typeof value === "number" && Number.isFinite(value)
    ? Math.max(0, Math.min(1, value))
    : neutral;
}
