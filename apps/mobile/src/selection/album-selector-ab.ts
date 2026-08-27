// Offline CLI. The M6 A/B report: the shipped discrete-key greedy against the
// submodular selector, on the pinned event fixtures. Never imported by the app.
//
//   node --experimental-strip-types src/selection/album-selector-ab.ts
//
// It prints, per fixture, the coverage/repetition/quality metrics M6 asks for
// and then names every photograph that entered and left. "Looks better" is not
// a result; this is the artifact that replaces it.

// @ts-expect-error Node's native TypeScript runner requires the extension.
import { albumFixtures, type AlbumFixture } from "./album-fixtures.ts";
// @ts-expect-error Node's native TypeScript runner requires the extension.
import { cosine, planAlbum } from "./album-planner.ts";
import type { AlbumObjectiveTuning, PlannerCandidate, PlannerPolicy } from "./album-planner";

const DUPLICATE_BAR = 0.92;
const MOMENT_WINDOW_MS = 6 * 60 * 60 * 1_000;
const DAY_MS = 24 * 60 * 60 * 1_000;

function moments(candidates: readonly PlannerCandidate[]) {
  const parent = candidates.map((_, index) => index);
  const root = (index: number): number => {
    while (parent[index] !== index) {
      parent[index] = parent[parent[index]];
      index = parent[index];
    }
    return index;
  };
  for (let left = 0; left < candidates.length; left += 1) {
    for (let right = left + 1; right < candidates.length; right += 1) {
      const a = candidates[left];
      const b = candidates[right];
      if (Math.abs((a.capturedAt ?? 0) - (b.capturedAt ?? 0)) > MOMENT_WINDOW_MS) continue;
      if (cosine(a.embedding ?? [], b.embedding ?? []) < 0.8) continue;
      const rootA = root(left);
      const rootB = root(right);
      if (rootA !== rootB) parent[Math.max(rootA, rootB)] = Math.min(rootA, rootB);
    }
  }
  return new Map(candidates.map((candidate, index) => [candidate.mediaId, root(index)]));
}

function report(fixture: AlbumFixture, ids: readonly string[]) {
  const byId = new Map(fixture.candidates.map((candidate) => [candidate.mediaId, candidate]));
  const chosen = ids.map((mediaId) => byId.get(mediaId)!);
  const groups = moments(fixture.candidates);
  let worstPair = 0;
  let duplicatePairs = 0;
  for (let left = 0; left < chosen.length; left += 1) {
    for (let right = left + 1; right < chosen.length; right += 1) {
      const value = cosine(chosen[left].embedding ?? [], chosen[right].embedding ?? []);
      if (value > worstPair) worstPair = value;
      if (value >= DUPLICATE_BAR) duplicatePairs += 1;
    }
  }
  const poses: Record<string, number> = {};
  for (const candidate of chosen) {
    if (candidate.poseCluster) poses[candidate.poseCluster] = (poses[candidate.poseCluster] ?? 0) + 1;
  }
  const casts: Record<number, number> = {};
  for (const candidate of chosen) {
    const size = (candidate.personIds ?? []).length;
    casts[size] = (casts[size] ?? 0) + 1;
  }
  const allPeople = new Set(fixture.candidates.flatMap((candidate) => candidate.personIds ?? []));
  const seen = new Set(chosen.flatMap((candidate) => candidate.personIds ?? []));
  return {
    photos: ids.length,
    momentsCovered: `${new Set(ids.map((mediaId) => groups.get(mediaId))).size}/${new Set(groups.values()).size}`,
    peopleCovered: `${seen.size}/${allPeople.size}`,
    days: new Set(chosen.map((candidate) => Math.floor((candidate.capturedAt ?? 0) / DAY_MS))).size,
    places: new Set(chosen.map((candidate) => candidate.placeKey)).size,
    nearDuplicatePairs: duplicatePairs,
    worstPairCosine: round(worstPair),
    maxPoseRepeat: Math.max(0, ...Object.values(poses)),
    castSizes: casts,
    meanQuality: round(chosen.reduce((sum, candidate) => sum + candidate.quality, 0) / (chosen.length || 1)),
  };
}

function round(value: number) {
  return Math.round(value * 1_000) / 1_000;
}

const SELECTORS: PlannerPolicy["selector"][] = ["coverage-keys", "submodular"];

for (const fixture of albumFixtures()) {
  const plans = SELECTORS.map((selector) => ({
    selector,
    plan: planAlbum(fixture.candidates, fixture.target, { policy: { selector } }),
  }));
  console.log(`\n=== ${fixture.name} (target ${fixture.target}, ${fixture.candidates.length} candidates) ===`);
  for (const { selector, plan } of plans) {
    console.log(`${selector.padEnd(14)} ${JSON.stringify(report(fixture, plan.selectedIds))}`);
  }
  const before = new Set(plans[0].plan.selectedIds);
  const after = new Set(plans[1].plan.selectedIds);
  const entered = plans[1].plan.selectedIds.filter((mediaId) => !before.has(mediaId));
  const left = plans[0].plan.selectedIds.filter((mediaId) => !after.has(mediaId));
  const trace = plans[1].plan.objectiveTrace;
  console.log(`churn         ${entered.length}/${fixture.target}`);
  console.log(`  entered     ${entered.join(", ") || "-"}`);
  console.log(`  left        ${left.join(", ") || "-"}`);
  console.log(
    `  objective   ${JSON.stringify({
      value: trace?.value,
      gainEvaluations: trace?.evaluations,
      swaps: trace?.swaps,
    })}`,
  );
  console.log(
    `  gains       ${JSON.stringify(
      Object.fromEntries(entered.map((mediaId) => [mediaId, trace?.marginalGainByMediaId[mediaId]])),
    )}`,
  );

  // Which PART of the new selector is doing the work? Without this the whole
  // A/B is attributable to "submodular", which is not a finding. `vsFull`
  // counts photographs that differ from the full objective's album, so a term
  // whose removal changes nothing was never earning its place.
  const zeroCoverage = {
    coverageMoment: 0, coveragePerson: 0, coverageTime: 0, coveragePlace: 0, coveragePose: 0,
  };
  const ablations: [string, Partial<AlbumObjectiveTuning>][] = [
    ["facility location only", zeroCoverage],
    ["saturating coverage only", { facilityWeight: 0 }],
    ["neither (quality + hard constraints)", { ...zeroCoverage, facilityWeight: 0, swapRounds: 0 }],
  ];
  for (const [label, objective] of ablations) {
    const ids = planAlbum(fixture.candidates, fixture.target, {
      policy: { selector: "submodular" },
      objective,
    }).selectedIds;
    console.log(
      `  ablate      ${label.padEnd(36)} ${JSON.stringify({
        ...report(fixture, ids),
        vsFull: ids.filter((mediaId) => !after.has(mediaId)).length,
      })}`,
    );
  }
}
