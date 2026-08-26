// @ts-expect-error TypeScript bundler resolution normally omits source extensions.
import { clusterFaces, cosine, extendFaceClusters } from "./face-cluster.ts";
// @ts-expect-error TypeScript bundler resolution normally omits source extensions.
import { DEFAULT_FACE_INDEX_THRESHOLD, faceClusterOptions, summariesForPeople } from "./face-index.ts";
import type { Person } from "./types";

// Local assert to match the house test style (the app tsconfig has no
// @types/node, so node:test / node:assert are intentionally not imported).
function assert(value: unknown, message: string): asserts value {
  if (!value) throw new Error(`face-cluster recovery self-check failed: ${message}`);
}

/**
 * Offline ground-truth harness for the People tiles.
 *
 * The device symptom this exists to catch is unfalsifiable by eye: one tile
 * holding 2,164 photos looks exactly like "clustering is working, this person
 * is just in a lot of photos". So build embedding sets where the answer is
 * known — K identities x N faces — and assert the clusterer recovers about K
 * people, not 1 and not K*N.
 *
 * Honesty requirement: synthetic vectors must have ArcFace-like spread or the
 * test proves nothing. Real aligned MobileFaceNet embeddings are NOT uniform on
 * the sphere; every face shares a large common "faceness" component, which is
 * why different people still sit near cosine 0.2 instead of near 0. Each face
 * is therefore mixed from three orthogonal parts and re-normalized:
 *
 *   face = normalize(A*shared + B*identity + C*noise),  A^2+B^2+C^2 = 1
 *
 * giving, in expectation, cosine(same person) = A^2+B^2 and
 * cosine(different people) = A^2.
 *
 * Recalibrated for w600k_mbf. The old A^2 = 0.18 / A^2+B^2 = 0.70 matched the
 * 192-dim model, whose impostor median really was ~0.177; measured on 1,471 LFW
 * crops through the bundled TFLite build, w600k_mbf puts the impostor median at
 * 0.004 and the genuine median at 0.623. Leaving the old numbers would have put
 * synthetic impostors at 0.18 against a 0.20 assignment bar — the generator, not
 * the clusterer, deciding the result. A^2 is held slightly above the measured
 * impostor median so the fixture stays harder than reality rather than easier.
 * STATISTICS below asserts the generator actually hits that band before anything
 * else is judged.
 */
const EMBEDDING_SIZE = 512;
const SHARED_WEIGHT = Math.sqrt(0.03);
const IDENTITY_WEIGHT = Math.sqrt(0.59);
const NOISE_WEIGHT = Math.sqrt(0.38);

/** mulberry32: a seeded PRNG, so a failure here is reproducible forever. */
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
  const magnitude = Math.sqrt(
    values.reduce((sum, value) => sum + value * value, 0),
  );
  return values.map((value) => value / magnitude);
}

/** Two independent draws are near-orthogonal at this dimensionality. */
function unitVector(random: () => number): number[] {
  return normalize(Array.from({ length: EMBEDDING_SIZE }, () => gaussian(random)));
}

function mix(parts: Array<[number, number[]]>): number[] {
  const summed = Array.from({ length: EMBEDDING_SIZE }, (_unused, axis) =>
    parts.reduce((sum, [weight, vector]) => sum + weight * vector[axis], 0),
  );
  return normalize(summed);
}

type Labelled = {
  assetId: string;
  embedding: number[];
  embeddingKind: "identity";
  identity: number;
};

/**
 * K identities x N faces, interleaved the way a chronological library arrives
 * (person A, person B, person C, person A again ...) so greedy online
 * assignment is exercised in its realistic, order-mixed form.
 */
function syntheticLibrary(
  seed: number,
  facesPerIdentity: number[],
  assetIdFor: (identity: number, face: number) => string,
): Labelled[] {
  const random = createRandom(seed);
  const shared = unitVector(random);
  const identities = facesPerIdentity.map(() => unitVector(random));
  const faces: Labelled[] = [];
  const maximum = Math.max(...facesPerIdentity);
  for (let face = 0; face < maximum; face += 1) {
    for (let identity = 0; identity < facesPerIdentity.length; identity += 1) {
      if (face >= facesPerIdentity[identity]) continue;
      faces.push({
        assetId: assetIdFor(identity, face),
        embedding: mix([
          [SHARED_WEIGHT, shared],
          [IDENTITY_WEIGHT, identities[identity]],
          [NOISE_WEIGHT, unitVector(random)],
        ]),
        embeddingKind: "identity",
        identity,
      });
    }
  }
  return faces;
}

type Cluster = { assetIds: string[]; faceCount: number; id: string };

/** Which ground-truth identities each recovered cluster actually contains. */
function identitiesPerCluster(
  faces: Labelled[],
  clusters: Cluster[],
): Array<Set<number>> {
  const identityByAsset = new Map(
    faces.map((face) => [face.assetId, face.identity] as const),
  );
  return clusters.map(
    (cluster) =>
      new Set(
        cluster.assetIds
          .map((assetId) => identityByAsset.get(assetId))
          .filter((identity): identity is number => identity !== undefined),
      ),
  );
}

const solo = (identity: number, face: number) => `solo-${identity}-${face}`;

// ---------------------------------------------------------------------------
// 0. STATISTICS. The generator is only useful if it is hard in the same way
//    real embeddings are hard. Fail loudly if it drifts into "trivially
//    separable" territory, because then every assertion below is worthless.
// ---------------------------------------------------------------------------
{
  const faces = syntheticLibrary(20260825, Array.from({ length: 6 }, () => 10), solo);
  let intraSum = 0;
  let intraCount = 0;
  let interSum = 0;
  let interCount = 0;
  let interMax = Number.NEGATIVE_INFINITY;
  for (let first = 0; first < faces.length; first += 1) {
    for (let second = first + 1; second < faces.length; second += 1) {
      const similarity = cosine(faces[first].embedding, faces[second].embedding);
      if (faces[first].identity === faces[second].identity) {
        intraSum += similarity;
        intraCount += 1;
      } else {
        interSum += similarity;
        interCount += 1;
        interMax = Math.max(interMax, similarity);
      }
    }
  }
  const intraMean = intraSum / intraCount;
  const interMean = interSum / interCount;
  assert(
    intraMean > 0.6 && intraMean < 0.8,
    `same-person cosine must sit in the ArcFace band (got ${intraMean.toFixed(3)})`,
  );
  // Band recalibrated with the generator: w600k_mbf separates strangers far
  // better than the 192-dim model did, so the impostor mean belongs near zero.
  // A generator still producing 0.1-0.3 impostors would be modelling the OLD
  // model against the NEW thresholds and would fail for reasons the clusterer
  // is not responsible for.
  assert(
    interMean > 0.005 && interMean < 0.08,
    `different-person cosine must sit in the w600k_mbf band (got ${interMean.toFixed(3)})`,
  );
  // The hardest pair has to approach the real tail, not the old model's. My own
  // measurement on 1,471 LFW crops puts the w600k_mbf impostor p99 at 0.169, so
  // a worst synthetic pair in that neighbourhood is the honest bar; requiring
  // 0.3 would demand the generator be less separable than the model it stands in
  // for.
  assert(
    interMax > 0.12,
    `the hardest impostor pair must be genuinely hard (got ${interMax.toFixed(3)})`,
  );
}

// ---------------------------------------------------------------------------
// 1. RECOVERY. Eight people, fourteen faces each, every face in its own photo.
//    The shipped policy must return roughly eight tiles.
// ---------------------------------------------------------------------------
{
  const identityCount = 8;
  const facesPerIdentity = 14;
  const faces = syntheticLibrary(
    424242,
    Array.from({ length: identityCount }, () => facesPerIdentity),
    solo,
  );
  const people = clusterFaces(faces, faceClusterOptions()) as Cluster[];
  const members = identitiesPerCluster(faces, people);

  assert(
    people.length > 1,
    `the library must not collapse into one tile (got ${people.length} people for ${identityCount} identities)`,
  );
  assert(
    people.length >= identityCount,
    `every identity needs at least one tile (got ${people.length} for ${identityCount})`,
  );
  assert(
    people.length <= identityCount * 2,
    `clustering must not shatter into per-face tiles (got ${people.length} for ${identityCount} identities, ${faces.length} faces)`,
  );
  assert(
    members.every((identities) => identities.size === 1),
    `no tile may mix two people (got ${members.map((set) => set.size).join(",")})`,
  );
  assert(
    new Set(members.flatMap((identities) => [...identities])).size ===
      identityCount,
    "every ground-truth identity must be represented by some tile",
  );
  const largest = people.reduce(
    (biggest, person) => Math.max(biggest, person.faceCount),
    0,
  );
  assert(
    largest <= facesPerIdentity,
    `no tile may hold more faces than one person owns (got ${largest} of ${facesPerIdentity})`,
  );
}

// ---------------------------------------------------------------------------
// 2. RUNAWAY ABSORPTION. The device failure was one tile with 2,164 photos, so
//    reproduce its shape: a cluster that grows well past any "large cluster"
//    face count while seven ordinary people stand next to it. A merge rule that
//    relaxes once a cluster is big enough turns that cluster into a vacuum.
// ---------------------------------------------------------------------------
{
  const dominant = 60;
  const others = Array.from({ length: 7 }, () => 12);
  const faces = syntheticLibrary(31337, [dominant, ...others], solo);
  const people = clusterFaces(faces, faceClusterOptions()) as Cluster[];
  const members = identitiesPerCluster(faces, people);
  const largest = people.reduce(
    (biggest, person) => Math.max(biggest, person.faceCount),
    0,
  );

  assert(
    largest <= dominant,
    `a well-supported cluster must not swallow its neighbours (got ${largest} faces, one person owns ${dominant})`,
  );
  assert(
    members.every((identities) => identities.size === 1),
    "growing past the large-cluster face count must not license a mixed tile",
  );
  assert(
    people.length >= 8,
    `all eight people must survive next to a dominant one (got ${people.length})`,
  );
}

// ---------------------------------------------------------------------------
// 3. THE SAME RULE ON THE INCREMENTAL PATH. The phone clusters batch by batch,
//    which is a different code path from a single full rebuild, and it is the
//    path that actually runs during a scan.
// ---------------------------------------------------------------------------
{
  const identityCount = 6;
  const faces = syntheticLibrary(
    987654,
    Array.from({ length: identityCount }, () => 15),
    solo,
  );
  let people: Person[] = [];
  for (let start = 0; start < faces.length; start += 16) {
    people = extendFaceClusters(
      people,
      faces.slice(start, start + 16),
      faceClusterOptions(),
    ) as Person[];
  }
  const members = identitiesPerCluster(faces, people);
  assert(
    people.length >= identityCount && people.length <= identityCount * 2,
    `batched scanning must recover about ${identityCount} people (got ${people.length})`,
  );
  assert(
    members.every((identities) => identities.size === 1),
    "batched scanning must not fuse two people either",
  );
}

// ---------------------------------------------------------------------------
// 4. GROUP SHOTS. A family library is mostly group photos, and co-occurrence is
//    the strongest cannot-link evidence there is: everyone in one frame is a
//    different person. Give every identity the SAME twelve photos and check the
//    constraint survives both the online and the merge path.
// ---------------------------------------------------------------------------
{
  const identityCount = 6;
  const faces = syntheticLibrary(
    5150,
    Array.from({ length: identityCount }, () => 12),
    (_identity, face) => `group-${face}`,
  );
  const people = clusterFaces(faces, faceClusterOptions()) as Cluster[];
  const members = identitiesPerCluster(faces, people);
  assert(
    people.length >= identityCount,
    `six people photographed together stay six tiles (got ${people.length})`,
  );
  assert(
    people.every((person) => person.faceCount <= 12),
    `no tile may absorb a whole group photo repeatedly (got ${people
      .map((person) => person.faceCount)
      .join(",")})`,
  );
  assert(
    members.every((identities) => identities.size <= 1),
    "co-occurring faces must never share a tile",
  );
}

// ---------------------------------------------------------------------------
// 5. VISIBILITY. Recovering the clusters is worthless if the People UI hides
//    them. A floor computed from the LARGEST cluster means one dominant person
//    suppresses everyone else, which is the reported symptom stated exactly:
//    "I don't see all the faces".
// ---------------------------------------------------------------------------
{
  const person = (id: string, faceCount: number) => ({
    assetIds: Array.from({ length: faceCount }, (_unused, index) => `${id}-${index}`),
    centroid: [1, 0],
    embeddingKind: "identity" as const,
    faceCount,
    id,
  });
  const surfaced = summariesForPeople(
    [
      person("person-1", 2164),
      person("person-2", 180),
      person("person-3", 41),
      person("person-4", 9),
      person("person-5", 2),
    ],
    true,
  ) as Cluster[];
  assert(
    surfaced.length === 5,
    `every corroborated person must reach the People UI beside a 2,164-face tile (got ${surfaced.length})`,
  );
  assert(
    surfaced[0].id === "person-1",
    "the People UI still sorts by support",
  );
  const singleton = summariesForPeople([person("person-1", 1)], true) as Cluster[];
  assert(
    singleton.length === 0,
    "a single uncorroborated face is still withheld as a likely fragment",
  );
}

// ---------------------------------------------------------------------------
// 6. THE INVARIANT ITSELF. Assignment errors are transitive and merge errors
//    are unrecoverable, so the merge bar can never be easier than the bar a
//    single face had to clear. This is the regression guard for the defect.
// ---------------------------------------------------------------------------
{
  const options = faceClusterOptions();
  assert(
    options.identityMergeThreshold >= options.threshold,
    `merging two centroids must never be easier than assigning one face (merge ${options.identityMergeThreshold} < assign ${options.threshold})`,
  );
  const strict = faceClusterOptions(0.8);
  assert(
    strict.identityMergeThreshold >= 0.8,
    `a stricter assignment bar must raise the merge bar with it (got ${strict.identityMergeThreshold})`,
  );
  assert(
    !("identityLargeClusterMinFaces" in options),
    "no shipped path may relax the merge bar for clusters that are already large",
  );
}

// ---------------------------------------------------------------------------
// 7. THE ORIGINAL DEFECT, KEPT ON FILE. face-index.ts used to pass
//    identityMergeThreshold: 0.37 explicitly, which slipped past a clamp that
//    guarded only the "caller supplied nothing" branch — so raising the DEFAULT
//    merge threshold to 0.72 changed nothing on the one path that ships. Feed
//    the clusterer that exact number and prove it can no longer do damage.
// ---------------------------------------------------------------------------
{
  const identityCount = 8;
  const faces = syntheticLibrary(
    424242,
    Array.from({ length: identityCount }, () => 14),
    solo,
  );
  // 0.37 and 0.62 were the numbers at the time of the defect. What has to keep
  // holding is the SHAPE — an explicit merge bar below the assignment bar gets
  // clamped up — so both are now taken from the shipped bars instead of frozen
  // at values that mean nothing in w600k_mbf space.
  const LEGACY_MERGE_BAR = Number((DEFAULT_FACE_INDEX_THRESHOLD - 0.05).toFixed(4));
  const withLegacyBar = clusterFaces(faces, {
    identityMergeThreshold: LEGACY_MERGE_BAR,
    threshold: DEFAULT_FACE_INDEX_THRESHOLD,
  }) as Cluster[];
  const members = identitiesPerCluster(faces, withLegacyBar);
  assert(
    withLegacyBar.length === identityCount,
    `an explicit sub-assignment merge bar is clamped up, so this exact input yields ${identityCount} people (got ${withLegacyBar.length})`,
  );
  assert(
    members.every((identities) => identities.size === 1),
    "and it can no longer fuse two people into one tile",
  );
}

// eslint-disable-next-line no-console
console.log("face-cluster recovery self-check passed");
