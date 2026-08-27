/**
 * How often does the merge review refuse to remember the user's answer, and is
 * the fix for that refusal safe?
 *
 * `recordConstraint` stores a confirmed merge against ANCHOR ASSETS. Before
 * face anchors, an anchor could only be a photo that exactly one cluster
 * claims, and a pair where neither side had such a photo was declined
 * outright. That refusal was correct -- guessing attaches the correction to
 * the wrong face -- but nobody had counted how often it fired, or on whom.
 *
 * Two structural facts do most of the work in the BEFORE half:
 *
 *   1. Clustering cannot-links two faces from one photo unless they clear the
 *      0.72 mirror/panorama bar, so a multi-face photo is claimed by as many
 *      clusters as it holds faces. A photo-only anchor is refused for it.
 *   2. So a person was anchorable if and only if they appeared in at least one
 *      photo where they are the only detected face.
 *
 * "Do you have a solo photo?" was the whole test. In a family library --
 * "mostly group photos", per docs/ARCHITECTURE-BRIEF.md -- the people who fail
 * it are the ones photographed only alongside others.
 *
 * The AFTER half is the line beginning WITH FACE ANCHORS, and its only
 * interesting column is WRONG ANCHORS. Coverage is easy to buy and worthless
 * on its own; every anchor is therefore checked against ground truth taken
 * from `onAssign` -- which cluster the shipped clusterer actually put that face
 * in -- and the same choice made with no bar and no margin is counted beside
 * it, so the two guards are shown earning their place rather than assumed to.
 *
 * This is an offline research harness, not application code. It reads a real
 * exported `face-index.json` when given one:
 *
 *   node --experimental-strip-types scratch/face-anchor-coverage/measure.ts \
 *     --index /path/to/face-index.json
 *
 * With no index it generates a synthetic library and runs the SHIPPED
 * clusterer over it, so the same-photo rule, the fragmentation and the merge
 * suggestions all come from the code that runs on the phone rather than from
 * hand-written clusters. The generator is calibrated against the only real
 * totals available here (docs/ARCHITECTURE-BRIEF.md: 11,853 photos, 17,766
 * faces, 2,237 clusters, 932 of them with 2+ faces) and the answer is swept
 * across the parameters it cannot pin down. The two that change the conclusion
 * are worth knowing about:
 *
 *   --solo-share   how many photos hold exactly one face (--sweep tries five)
 *   --never-alone  cast members never photographed alone, which is what turns
 *                  the refusal from a nuisance into a lost 450-photo repair
 *   --family       how strongly the cast resembles each other, which is what
 *                  makes the anchoring SAFETY question hard
 *
 * A synthetic number is a shape, not a fact about anyone's library; the
 * `--index` path is what settles it.
 *
 * Requires the workspace node_modules (it imports face-index.ts for the exact
 * shipped clustering policy, which pulls in jpeg-js).
 */

import { readFileSync } from "node:fs";
import process from "node:process";

// @ts-expect-error TypeScript bundler resolution normally omits source extensions.
import { extendFaceClusters, suggestMerges, cosine } from "../../apps/mobile/src/faces/face-cluster.ts";
// @ts-expect-error TypeScript bundler resolution normally omits source extensions.
import { anchorFor, type AnchorBars } from "../../apps/mobile/src/faces/face-constraints.ts";
// @ts-expect-error TypeScript bundler resolution normally omits source extensions.
import { faceClusterOptions } from "../../apps/mobile/src/faces/face-index.ts";
import type { FaceObservation, Person } from "../../apps/mobile/src/faces/types.ts";

// ---------------------------------------------------------------------------
// Real library totals, from docs/ARCHITECTURE-BRIEF.md ("every number below was
// measured on the owner's own phone"). Used to size the synthetic library and
// to say, in the output, how far the stand-in drifted from the real thing.
// ---------------------------------------------------------------------------
const DEVICE_PHOTOS = 11853;
const DEVICE_FACES = 17766;
const DEVICE_CLUSTERS = 2237;
const DEVICE_CLUSTERS_WITH_TWO_PLUS = 932;

/** Faces in a multi-face photo: 2, 3, 4, 5. Mean 2.73. */
const GROUP_SIZE_WEIGHTS = [0.55, 0.25, 0.12, 0.08];

// Mixing weights for a synthetic face, copied from face-cluster-recovery.test.ts
// so this harness is exactly as hard as the ground-truth clustering harness:
// face = normalize(A*shared + B*identity + C*noise), giving cosine(same) ~ A^2+B^2
// and cosine(different) ~ A^2. Recalibrated there against 1,471 LFW crops
// through the bundled w600k_mbf build.
const EMBEDDING_SIZE = 512;
const SHARED_WEIGHT = Math.sqrt(0.03);
const IDENTITY_WEIGHT = Math.sqrt(0.59);
const NOISE_WEIGHT = Math.sqrt(0.38);

/**
 * Appearance drift, which is why the real index holds 2,237 clusters and not
 * a few hundred.
 *
 * One person photographed across an infant's first two years does not sit in
 * one ball: docs/ARCHITECTURE-BRIEF.md names over-fragmentation as the
 * clusterer's first problem and multi-prototype identities as the candidate
 * fix. Each identity therefore gets several prototypes, chosen by capture
 * time, each a partial rotation away from the base. Without this the generator
 * hands every identity back as one clean tile, `suggestMerges` finds nothing
 * to ask about, and the review half of this measurement has nothing to measure.
 */
const PROTOTYPE_DRIFT = 0.55;

/** mulberry32, as elsewhere in this repo: every run is reproducible. */
function createRandom(seed: number): () => number {
  let state = seed >>> 0;
  return () => {
    state = (state + 0x6d2b79f5) >>> 0;
    let value = Math.imul(state ^ (state >>> 15), 1 | state);
    value = (value + Math.imul(value ^ (value >>> 7), 61 | value)) ^ value;
    return ((value ^ (value >>> 14)) >>> 0) / 4294967296;
  };
}

function gaussian(random: () => number): number {
  const uniform = Math.max(random(), Number.EPSILON);
  return Math.sqrt(-2 * Math.log(uniform)) * Math.cos(2 * Math.PI * random());
}

function normalize(values: number[]): number[] {
  const magnitude = Math.sqrt(values.reduce((sum, value) => sum + value * value, 0));
  return values.map((value) => value / magnitude);
}

function unitVector(random: () => number, size: number): number[] {
  return normalize(Array.from({ length: size }, () => gaussian(random)));
}

function mix(parts: Array<[number, number[]]>, size: number): number[] {
  return normalize(
    Array.from({ length: size }, (_unused, axis) =>
      parts.reduce((sum, [weight, vector]) => sum + weight * vector[axis], 0),
    ),
  );
}

function pick(random: () => number, weights: number[]): number {
  const total = weights.reduce((sum, weight) => sum + weight, 0);
  let target = random() * total;
  for (let index = 0; index < weights.length; index += 1) {
    target -= weights[index];
    if (target <= 0) return index;
  }
  return weights.length - 1;
}

type SyntheticOptions = {
  seed: number;
  faces: number;
  /** Share of face-bearing photos that hold exactly one face. */
  soloShare: number;
  /** Recurring cast: the family. */
  castSize: number;
  /** Share of solo photos that are of somebody outside the cast. */
  tailSoloShare: number;
  /** Share of group-photo slots filled by the cast rather than the tail. */
  castInGroups: number;
  tailSize: number;
  /** Appearance prototypes per identity: the cast is seen across two years. */
  castPrototypes: number;
  tailPrototypes: number;
  /** How far one prototype rotates from the identity's base, 0..1. */
  drift: number;
  /** Zipf exponent over the tail: lower means more one-off strangers. */
  tailExponent: number;
  /**
   * How much of the cast's identity is a FAMILY resemblance, 0..1.
   *
   * The safety question this harness has to answer is not "can two strangers
   * be told apart" -- that is easy in this embedding space and would flatter
   * any rule. It is "can a mother be told apart from her own child, in one
   * frame". Measured on the owner's real library, 4.1% of different-person
   * pairs beat a 0.20 cosine, far above what LFW predicts, because relatives
   * look alike. Cast identities are therefore drawn around a shared family
   * direction, and `relatives` in the band report says how hard that made it.
   */
  family: number;
  /**
   * Cast members who are NEVER photographed alone.
   *
   * The claim this harness has to test, not assume: a grandparent who only
   * ever appears in family group shots. Chance alone cannot make a 50-face
   * person anchorless -- at any solo rate above a few percent the odds of
   * missing every time are negligible -- so if the refusal reaches people who
   * matter, it has to reach them systematically. Here it is, dialled in.
   */
  neverAlone: number;
};

type Labelled = FaceObservation & { identity: number; mode: number };

/**
 * A family library: a small recurring cast, a long tail of everybody else.
 *
 * The tail is the population this whole question is about -- relatives seen at
 * one wedding, friends in the background of a birthday -- so it is modelled
 * explicitly rather than as uniform noise: a Zipf weight over a large pool, so
 * a few recur and most appear once or twice, which is what produced 1,305
 * single-face clusters on the real device.
 */
function syntheticLibrary(options: SyntheticOptions): Labelled[] {
  const random = createRandom(options.seed);
  const shared = unitVector(random, EMBEDDING_SIZE);
  const family = unitVector(random, EMBEDDING_SIZE);
  const prototypes = (count: number, related = false): number[][] => {
    const own = unitVector(random, EMBEDDING_SIZE);
    const base = related && options.family > 0
      ? mix(
          [
            [Math.sqrt(options.family), family],
            [Math.sqrt(1 - options.family), own],
          ],
          EMBEDDING_SIZE,
        )
      : own;
    return Array.from({ length: count }, (_unused, mode) =>
      mode === 0
        ? base
        : mix(
            [
              [Math.sqrt(1 - options.drift), base],
              [Math.sqrt(options.drift), unitVector(random, EMBEDDING_SIZE)],
            ],
            EMBEDDING_SIZE,
          ),
    );
  };
  const cast = Array.from({ length: options.castSize }, () =>
    prototypes(options.castPrototypes, true),
  );
  const tail = Array.from({ length: options.tailSize }, () => prototypes(options.tailPrototypes));
  const identities = [...cast, ...tail];
  const castWeights = cast.map((_unused, rank) => 1 / (rank + 1));
  // Same cast, same recurrence, minus whoever is never photographed alone.
  const soloWeights = castWeights.map((weight, rank) =>
    rank < options.neverAlone ? 0 : weight,
  );
  const tailWeights = tail.map((_unused, rank) => 1 / Math.pow(rank + 3, options.tailExponent));

  const faces: Labelled[] = [];
  // Two years of an infant's life, in order, so capture times mean what they
  // mean on the device and the temporal merge bar sees a real spread.
  const start = Date.UTC(2024, 0, 1);
  const span = 2 * 365 * 24 * 60 * 60 * 1000;
  let photoNumber = 0;

  const identityFor = (fromCast: boolean, solo: boolean): number =>
    fromCast
      ? pick(random, solo ? soloWeights : castWeights)
      : options.castSize + pick(random, tailWeights);

  while (faces.length < options.faces) {
    const solo = random() < options.soloShare;
    const size = solo ? 1 : 2 + pick(random, GROUP_SIZE_WEIGHTS);
    const assetId = `photo-${photoNumber}`;
    const progress = Math.min(0.999, photoNumber / (options.faces / 1.8));
    const capturedAt = start + Math.floor(progress * span);
    photoNumber += 1;
    // Distinct identities within one photo: that is what the clusterer's
    // same-photo cannot-link asserts, and generating anything else would be
    // generating a library the clusterer could not have produced.
    const chosen = new Set<number>();
    let attempts = 0;
    while (chosen.size < size && attempts < size * 8) {
      attempts += 1;
      chosen.add(
        identityFor(
          solo ? random() >= options.tailSoloShare : random() < options.castInGroups,
          solo,
        ),
      );
    }
    for (const identity of chosen) {
      const modes = identities[identity];
      faces.push({
        assetId,
        capturedAt,
        mode: Math.floor(progress * modes.length),
        embedding: mix(
          [
            [SHARED_WEIGHT, shared],
            [IDENTITY_WEIGHT, modes[Math.floor(progress * modes.length)]],
            [NOISE_WEIGHT, unitVector(random, EMBEDDING_SIZE)],
          ],
          EMBEDDING_SIZE,
        ),
        embeddingKind: "identity",
        identity,
      });
    }
  }
  return faces;
}

/**
 * The three cosine bands the fixture must land in, or nothing below it means
 * anything.
 *
 * `genuine` is one person at one point in their life and must sit in the band
 * measured on 1,471 LFW crops through the bundled build. `impostor` is two
 * different people. `drifted` is the same person across prototypes -- the band
 * that decides whether over-fragmentation is modelled at all: too high and
 * every identity comes back as one tile, too low and the fragments are
 * strangers rather than the near-misses the review screen is built to ask about.
 */
function generatorBands(faces: Labelled[], random: () => number, castSize: number): {
  genuine: number;
  drifted: number;
  impostor: number;
  impostorMax: number;
  relatives: number;
  relativesMax: number;
} {
  const sums = { genuine: 0, drifted: 0, impostor: 0, relatives: 0 };
  const counts = { genuine: 0, drifted: 0, impostor: 0, relatives: 0 };
  let impostorMax = Number.NEGATIVE_INFINITY;
  let relativesMax = Number.NEGATIVE_INFINITY;
  const samples = Math.min(200000, faces.length * 20);
  for (let sample = 0; sample < samples; sample += 1) {
    const first = faces[Math.floor(random() * faces.length)];
    const second = faces[Math.floor(random() * faces.length)];
    if (first === second) continue;
    const similarity = cosine(first.embedding, second.embedding);
    const band =
      first.identity !== second.identity
        ? "impostor"
        : first.mode === second.mode
          ? "genuine"
          : "drifted";
    sums[band] += similarity;
    counts[band] += 1;
    if (band === "impostor") {
      impostorMax = Math.max(impostorMax, similarity);
      if (first.identity < castSize && second.identity < castSize) {
        sums.relatives += similarity;
        counts.relatives += 1;
        relativesMax = Math.max(relativesMax, similarity);
      }
    }
  }
  return {
    genuine: sums.genuine / Math.max(1, counts.genuine),
    drifted: sums.drifted / Math.max(1, counts.drifted),
    impostor: sums.impostor / Math.max(1, counts.impostor),
    impostorMax,
    relatives: sums.relatives / Math.max(1, counts.relatives),
    relativesMax,
  };
}

// ---------------------------------------------------------------------------
// The measurement itself. Everything above only produces something to measure.
// ---------------------------------------------------------------------------

type Coverage = {
  people: number;
  anchorless: Person[];
  facesTotal: number;
  facesAnchorless: number;
};

function coverage(people: Person[], bars: AnchorBars): Coverage {
  const anchorless = people.filter(
    (person) => anchorFor(people, person.id, bars) === undefined,
  );
  return {
    people: people.length,
    anchorless,
    facesTotal: people.reduce((sum, person) => sum + person.faceCount, 0),
    facesAnchorless: anchorless.reduce((sum, person) => sum + person.faceCount, 0),
  };
}

function histogram(people: Person[]): string {
  const buckets = [0, 0, 0, 0];
  for (const person of people) {
    const count = person.faceCount;
    buckets[count === 1 ? 0 : count <= 3 ? 1 : count <= 9 ? 2 : 3] += 1;
  }
  return `1 face ${buckets[0]}, 2-3 ${buckets[1]}, 4-9 ${buckets[2]}, 10+ ${buckets[3]}`;
}

function percent(part: number, whole: number): string {
  return whole === 0 ? "n/a" : `${((part / whole) * 100).toFixed(1)}%`;
}

/**
 * What the review screen would do with these people.
 *
 * `suggestedFaceMerges` asks for 60. A suggestion is REFUSED when either side
 * has no unambiguous anchor asset, which is exactly `recordConstraint`'s test.
 */
function reviewOutcome(people: Person[], options: object, bars: AnchorBars, limit: number) {
  const suggestions = suggestMerges(people, { ...options, limit });
  const anchorable = new Map<string, boolean>();
  const canAnchor = (id: string): boolean => {
    const known = anchorable.get(id);
    if (known !== undefined) return known;
    const answer = anchorFor(people, id, bars) !== undefined;
    anchorable.set(id, answer);
    return answer;
  };
  const byId = new Map(people.map((person) => [person.id, person]));
  const refused = suggestions.filter(
    (suggestion) => !canAnchor(suggestion.a) || !canAnchor(suggestion.b),
  );
  return {
    suggestions,
    refused,
    refusedIn: (top: number): number =>
      suggestions
        .slice(0, top)
        .filter((suggestion) => !canAnchor(suggestion.a) || !canAnchor(suggestion.b))
        .length,
    photosAtStake: refused.reduce((sum, suggestion) => sum + suggestion.photosFixed, 0),
    // A refused 1+1 pair costs the user one photo. A refused pair where the
    // SMALLER side already holds four faces is a real repair being declined,
    // and those are the ones worth changing code over.
    substantial: refused.filter((suggestion) => suggestion.photosFixed >= 4).length,
    largest: refused.reduce((most, suggestion) => Math.max(most, suggestion.photosFixed), 0),
    sizes: refused
      .slice(0, 8)
      .map(
        (suggestion) =>
          `${byId.get(suggestion.a)?.faceCount ?? 0}+${byId.get(suggestion.b)?.faceCount ?? 0}`,
      ),
  };
}

type Truth = {
  facesIn: (assetId: string) => readonly number[][];
  ownerOfFace: (assetId: string, face: readonly number[]) => string | undefined;
};

/**
 * What the FACE anchor buys, and what it risks.
 *
 * Coverage is the easy half. The half that matters is whether the anchor a
 * shared photo yields points at the right person, so every anchor here is
 * checked against ground truth taken from `onAssign` -- which cluster the
 * shipped clusterer actually put that face in. A single wrong answer in this
 * column would be worse than the refusal it replaces, so it is reported even
 * when it is zero, and the tightest margin any correct decision needed is
 * reported next to it as the headroom `MIN_ANCHOR_MARGIN` is spending.
 */
function anchorAudit(
  people: Person[],
  bars: AnchorBars,
  truth: Truth,
): string {
  let byPhoto = 0;
  let byFace = 0;
  let refused = 0;
  let wrong = 0;
  let naiveWrong = 0;
  let worstNaiveMargin = Number.NEGATIVE_INFINITY;
  let tightestCorrect = Number.POSITIVE_INFINITY;
  let loosestWrong = Number.NEGATIVE_INFINITY;
  const claimantsOf = (assetId: string): Person[] =>
    people.filter((person) => person.assetIds.includes(assetId));
  for (const person of people) {
    // What a rule with no bar and no margin would do: take this person's
    // best-scoring face in their first photo and call it theirs. Computed for
    // EVERY person, not only the refused ones, because it is the counterfactual
    // the two guards exist to beat.
    const [firstAsset] = person.assetIds;
    let naive: readonly number[] | undefined;
    let naiveScore = Number.NEGATIVE_INFINITY;
    let naiveRunnerUp = Number.NEGATIVE_INFINITY;
    for (const face of truth.facesIn(firstAsset)) {
      const score = cosine(face as number[], person.centroid);
      if (score > naiveScore) {
        naiveRunnerUp = naiveScore;
        naiveScore = score;
        naive = face;
      } else if (score > naiveRunnerUp) {
        naiveRunnerUp = score;
      }
    }
    if (naive && truth.ownerOfFace(firstAsset, naive) !== person.id) {
      naiveWrong += 1;
      // How decisive the wrong choice looked. This is what `MIN_ANCHOR_MARGIN`
      // has to sit above.
      worstNaiveMargin = Math.max(
        worstNaiveMargin,
        naiveRunnerUp > Number.NEGATIVE_INFINITY ? naiveScore - naiveRunnerUp : 0,
      );
    }

    const anchor = anchorFor(people, person.id, bars, truth.facesIn);
    if (!anchor) {
      refused += 1;
      continue;
    }
    if (!anchor.face) {
      byPhoto += 1;
      continue;
    }
    byFace += 1;
    // The margin this decision actually had, so the constant's headroom is
    // measured rather than asserted.
    const scores = claimantsOf(anchor.assetId)
      .map((claimant) => cosine(anchor.face as number[], claimant.centroid))
      .sort((left, right) => right - left);
    const margin = scores.length > 1 ? scores[0] - scores[1] : Number.POSITIVE_INFINITY;
    if (truth.ownerOfFace(anchor.assetId, anchor.face) === person.id) {
      tightestCorrect = Math.min(tightestCorrect, margin);
    } else {
      wrong += 1;
      loosestWrong = Math.max(loosestWrong, margin);
    }
  }
  return (
    `WITH FACE ANCHORS: ${byPhoto + byFace} of ${people.length} anchorable ` +
    `(${percent(byPhoto + byFace, people.length)}), ${byFace} by face, ${refused} still refused; ` +
    `WRONG ANCHORS ${wrong} (must be 0)` +
    `, worst wrong margin ${loosestWrong === Number.NEGATIVE_INFINITY ? "none" : loosestWrong.toFixed(3)}` +
    `, tightest correct margin ${tightestCorrect === Number.POSITIVE_INFINITY ? "n/a" : tightestCorrect.toFixed(3)}` +
    `; the same choice with no bar and no margin misattributes ${naiveWrong} people` +
    `, the most confident of them by ${worstNaiveMargin === Number.NEGATIVE_INFINITY ? "n/a" : worstNaiveMargin.toFixed(3)}`
  );
}

function report(
  label: string,
  people: Person[],
  options: { threshold?: number; perceptualThreshold?: number },
  truth?: Truth,
): void {
  const bars: AnchorBars = {
    assignment: options.threshold ?? 0,
    perceptual: options.perceptualThreshold ?? 1,
  };
  const found = coverage(people, bars);
  const review = reviewOutcome(people, options, bars, 60);
  console.log(`\n== ${label}`);
  console.log(
    `people ${found.people}  faces ${found.facesTotal}  ` +
      `clusters with 2+ faces ${people.filter((person) => person.faceCount > 1).length}`,
  );
  console.log(
    `NO ANCHOR: ${found.anchorless.length} people (${percent(found.anchorless.length, found.people)})  ` +
      `holding ${found.facesAnchorless} faces (${percent(found.facesAnchorless, found.facesTotal)})`,
  );
  console.log(`  sizes: ${histogram(found.anchorless)}`);
  console.log(
    `REVIEW (limit 60): ${review.suggestions.length} offered, ` +
      `${review.refused.length} refused (${percent(review.refused.length, review.suggestions.length)})  ` +
      `first-1 ${review.refusedIn(1)}, first-5 ${review.refusedIn(5)}, first-20 ${review.refusedIn(20)}`,
  );
  console.log(
    `  refused pairs would have fixed ${review.photosAtStake} photos ` +
      `(${review.substantial} of them worth 4+, largest ${review.largest}); ` +
      `face counts ${review.sizes.join(" ") || "-"}`,
  );
  // The mechanism, stated as a check rather than as a claim: if a single
  // anchorless person owns a photo nobody else is in, the explanation above is
  // wrong and every number here needs re-reading.
  const shared = new Map<string, number>();
  for (const person of people) {
    for (const assetId of person.assetIds) {
      shared.set(assetId, (shared.get(assetId) ?? 0) + 1);
    }
  }
  const withSolo = found.anchorless.filter((person) =>
    person.assetIds.some((assetId) => (shared.get(assetId) ?? 0) === 1),
  ).length;
  console.log(
    `  photos claimed by exactly one cluster: ${[...shared.values()].filter((count) => count === 1).length}` +
      ` of ${shared.size}; anchorless people owning one: ${withSolo} (must be 0)`,
  );
  if (truth) {
    console.log(
      `  ${anchorAudit(people, bars, truth)}`,
    );
  }
}

// ---------------------------------------------------------------------------

function argument(name: string, fallback: number): number {
  const at = process.argv.indexOf(`--${name}`);
  if (at === -1) return fallback;
  const value = Number(process.argv[at + 1]);
  return Number.isFinite(value) ? value : fallback;
}

function decodeCentroid(value: string): number[] {
  const bytes = Buffer.from(value, "base64");
  const signed = new Int8Array(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  return Array.from(signed, (component) => component / 127);
}

const indexAt = process.argv.indexOf("--index");
if (indexAt !== -1) {
  const path = process.argv[indexAt + 1];
  const stored = JSON.parse(readFileSync(path, "utf8")) as {
    people: Array<Person & { centroid: string }>;
    threshold?: number;
  };
  const people: Person[] = stored.people.map((person) => ({
    ...person,
    centroid: decodeCentroid(person.centroid),
  }));
  console.log(`face-index.json: ${path}`);
  report("DEVICE INDEX", people, faceClusterOptions(stored.threshold));
} else {
  const seed = argument("seed", 20260827);
  const scale = argument("scale", 1);
  const faces = Math.round(argument("faces", DEVICE_FACES) * scale);
  const swept = process.argv.includes("--sweep")
    ? [0.4, 0.5, 0.55, 0.6, 0.7]
    : [argument("solo-share", 0.55)];

  console.log(
    `synthetic library, seed ${seed}, ${faces} faces ` +
      `(device: ${DEVICE_FACES} faces over ${DEVICE_PHOTOS} photos, ` +
      `${DEVICE_CLUSTERS} clusters, ${DEVICE_CLUSTERS_WITH_TWO_PLUS} with 2+ faces)`,
  );

  const castSize = argument("cast", 12);
  for (const soloShare of swept) {
    const built = syntheticLibrary({
      seed,
      faces,
      soloShare,
      castSize,
      tailSoloShare: argument("tail-solo-share", 0.12),
      castInGroups: argument("cast-in-groups", 0.9),
      tailSize: Math.max(50, Math.round(argument("tail", 2500) * scale)),
      castPrototypes: argument("cast-prototypes", 6),
      tailPrototypes: argument("tail-prototypes", 2),
      drift: argument("drift", PROTOTYPE_DRIFT),
      tailExponent: argument("tail-exponent", 0.3),
      family: argument("family", 0),
      neverAlone: argument("never-alone", 0),
    });
    const bands = generatorBands(built, createRandom(seed + 1), castSize);
    // The generator has to be hard in the way real embeddings are hard, or
    // every number below it is an artifact of the fixture. Same band the
    // ground-truth harness asserts.
    // With `--family` on, the cast is deliberately confusable and the guard
    // flips: relatives that are NOT close would make the safety measurement
    // vacuous.
    const familyWeight = argument("family", 0);
    if (familyWeight > 0 && !(bands.relatives > 0.15)) {
      throw new Error(
        `--family asked for confusable relatives and got ${bands.relatives.toFixed(3)}`,
      );
    }
    if (
      !(
        bands.genuine > 0.6 &&
        bands.genuine < 0.8 &&
        (familyWeight > 0 || bands.impostor < 0.08) &&
        bands.drifted > 0.3 &&
        bands.drifted < 0.6
      )
    ) {
      throw new Error(
        `generator outside the measured w600k_mbf bands: genuine ${bands.genuine.toFixed(3)}, ` +
          `drifted ${bands.drifted.toFixed(3)}, impostor ${bands.impostor.toFixed(3)}`,
      );
    }
    const photos = new Set(built.map((face) => face.assetId)).size;
    const observations: FaceObservation[] = built.map(
      ({ identity: _identity, mode: _mode, ...face }) => face,
    );
    const options = faceClusterOptions();
    const startedAt = Date.now();
    // Ground truth for the anchor audit: which cluster the shipped clusterer
    // actually put each face in, followed through merges exactly as
    // `appendPeople` follows them.
    const owner = new Map<FaceObservation, string>();
    const people = extendFaceClusters([], observations, {
      ...options,
      onAssign: (observation, personId) => owner.set(observation, personId),
      onMerge: (absorbed, surviving) => {
        for (const [observation, personId] of owner) {
          if (personId === absorbed) owner.set(observation, surviving);
        }
      },
    });
    const byAsset = new Map<string, FaceObservation[]>();
    for (const observation of observations) {
      const known = byAsset.get(observation.assetId);
      if (known) known.push(observation);
      else byAsset.set(observation.assetId, [observation]);
    }
    const truth: Truth = {
      facesIn: (assetId) =>
        (byAsset.get(assetId) ?? []).map((observation) => observation.embedding),
      ownerOfFace: (assetId, face) => {
        for (const observation of byAsset.get(assetId) ?? []) {
          // `anchorFor` copies the embedding it returns, so identity is
          // recovered by value; two faces in one photo are never this close.
          if (cosine(observation.embedding, face as number[]) > 0.9999) {
            return owner.get(observation);
          }
        }
        return undefined;
      },
    };
    report(
      `solo-share ${soloShare} (${photos} photos with faces, ` +
        `${(built.length / photos).toFixed(2)} faces/photo, ` +
        `genuine ${bands.genuine.toFixed(3)} drifted ${bands.drifted.toFixed(3)} ` +
        `impostor ${bands.impostor.toFixed(3)}/${bands.impostorMax.toFixed(3)} ` +
        `relatives ${bands.relatives.toFixed(3)}/${bands.relativesMax.toFixed(3)}, ` +
        `clustered in ${((Date.now() - startedAt) / 1000).toFixed(1)}s)`,
      people,
      options,
      truth,
    );
  }
}
