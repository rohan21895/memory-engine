/**
 * The standing gate on the Tier-B codec: storing a signal must not change which
 * photographs are chosen.
 *
 * This is the test that decided the encoding. A deep record is 3,850 B at
 * float32 and 1,394 B at int8, and 2.8x is a lot of a phone's disk — but int8
 * shifts a vector component by up to 2.1e-3, and the album planner compares
 * cosines against hard bars. `docs/EMBEDDING-MEMORY.md` predicts exactly this
 * for face embeddings ("clustering shifts in the last bits ... deserves an
 * explicit decision"); here it is measured for albums, and the answer is no.
 *
 * Two halves:
 *   1. the pinned plans, through the real codec;
 *   2. the 0.92 duplicate bar, which is the mechanism by which a codec would
 *      move a plan — reported as a margin, so the next person can see how much
 *      room there actually is rather than trusting that six albums held.
 *
 * The sabotage is not invented: it is the encoding this codec rejected.
 */

// @ts-expect-error Node requires the extension; Metro resolves this path too.
import { albumFixtures } from "./album-fixtures.ts";
// @ts-expect-error Node requires the extension; Metro resolves this path too.
import { cosine, planAlbum } from "./album-planner.ts";
// @ts-expect-error Node requires the extension; Metro resolves this path too.
import { decodeFloat32, encodeFloat32 } from "./deep-signal-store.ts";
import type { PlannerCandidate, PlannerPolicy } from "./album-planner";

function assert(condition: unknown, message: string): asserts condition {
  if (!condition) throw new Error(`deep-signal parity self-check failed: ${message}`);
}

const SELECTORS: PlannerPolicy["selector"][] = ["coverage-keys", "submodular"];
const DUPLICATE_BAR = 0.92;
const fixtures = albumFixtures();

/** The production codec: float32 little-endian, base64. */
function throughStore(values: readonly number[]): number[] {
  const decoded = decodeFloat32(encodeFloat32(values));
  assert(decoded !== undefined, "the production codec must round-trip its own output");
  return decoded;
}

/**
 * The rejected alternative, as the sabotage. Per-vector max-abs scale, one byte
 * a component — the cheapest quantization that keeps a usable cosine, and the
 * one `face-index.ts` already uses for identity embeddings on disk.
 */
function throughInt8(values: readonly number[]): number[] {
  const scale = Math.max(...values.map((value) => Math.abs(value)), 1e-9);
  const quantized = values.map((value) =>
    Math.max(-127, Math.min(127, Math.round((value / scale) * 127))),
  );
  return quantized.map((value) => (value / 127) * scale);
}

function replan(
  candidates: readonly PlannerCandidate[],
  encode: (values: readonly number[]) => number[],
): PlannerCandidate[] {
  return candidates.map((candidate) =>
    candidate.embedding
      ? { ...candidate, embedding: encode(candidate.embedding) }
      : candidate,
  );
}

// --- 1. The pinned plans ---------------------------------------------------

let plansChecked = 0;
let worstComponentDrift = 0;

for (const fixture of fixtures) {
  for (const candidate of fixture.candidates) {
    if (!candidate.embedding) continue;
    const stored = throughStore(candidate.embedding);
    for (let index = 0; index < stored.length; index += 1) {
      worstComponentDrift = Math.max(
        worstComponentDrift,
        Math.abs(stored[index] - candidate.embedding[index]),
      );
    }
  }
  for (const selector of SELECTORS) {
    const live = planAlbum(fixture.candidates, fixture.target, { policy: { selector } });
    const stored = planAlbum(
      replan(fixture.candidates, throughStore),
      fixture.target,
      { policy: { selector } },
    );
    assert(
      live.selectedIds.length === fixture.target,
      `${fixture.name}/${selector} must produce a full album before anything is compared`,
    );
    assert(
      JSON.stringify(live.selectedIds) === JSON.stringify(stored.selectedIds),
      `${fixture.name}/${selector}: storing the embeddings changed the album. ` +
        `Out: ${live.selectedIds.filter((id) => !stored.selectedIds.includes(id)).join(",")}`,
    );
    assert(
      JSON.stringify(live.rescuedIds) === JSON.stringify(stored.rescuedIds),
      `${fixture.name}/${selector}: the rescue decisions moved`,
    );
    plansChecked += 1;
  }
}
assert(plansChecked === 6, `all six pinned plans must be checked, checked ${plansChecked}`);

/**
 * Diagnostic only: the intended identity-scoped pose change can alter which
 * near-bar pair reaches a final album, so a rejected codec no longer has to
 * move one of six complete plans to remain unsafe. The pair sweep below is the
 * stronger sabotage: it must show int8 crossing the hard 0.92 bar directly.
 */
const int8Moved: string[] = [];
for (const fixture of fixtures) {
  for (const selector of SELECTORS) {
    const live = planAlbum(fixture.candidates, fixture.target, { policy: { selector } });
    const quantized = planAlbum(
      replan(fixture.candidates, throughInt8),
      fixture.target,
      { policy: { selector } },
    );
    if (JSON.stringify(live.selectedIds) !== JSON.stringify(quantized.selectedIds)) {
      int8Moved.push(`${fixture.name}/${selector}`);
    }
  }
}

// --- 2. The mechanism, with its margin -------------------------------------
//
// A codec moves an album by moving a cosine across a bar. The margin below is
// what makes the float32 result a fact rather than a coincidence: it is the
// distance from the nearest candidate pair to the 0.92 duplicate bar, against
// the largest cosine the codec moved.

let closestApproach = Number.POSITIVE_INFINITY;
let worstCosineDrift = 0;
let crossings = 0;
let int8Crossings = 0;
let pairs = 0;

for (const fixture of fixtures) {
  const stored = replan(fixture.candidates, throughStore);
  const quantized = replan(fixture.candidates, throughInt8);
  for (let left = 0; left < fixture.candidates.length; left += 1) {
    for (let right = left + 1; right < fixture.candidates.length; right += 1) {
      const before = cosine(
        fixture.candidates[left].embedding ?? [],
        fixture.candidates[right].embedding ?? [],
      );
      const after = cosine(stored[left].embedding ?? [], stored[right].embedding ?? []);
      const afterInt8 = cosine(
        quantized[left].embedding ?? [],
        quantized[right].embedding ?? [],
      );
      if (!Number.isFinite(before) || !Number.isFinite(after)) continue;
      pairs += 1;
      closestApproach = Math.min(closestApproach, Math.abs(before - DUPLICATE_BAR));
      worstCosineDrift = Math.max(worstCosineDrift, Math.abs(after - before));
      if (before >= DUPLICATE_BAR !== (after >= DUPLICATE_BAR)) crossings += 1;
      if (before >= DUPLICATE_BAR !== (afterInt8 >= DUPLICATE_BAR)) {
        int8Crossings += 1;
      }
    }
  }
}

// Every candidate of every fixture, against every other. Asserted as an exact
// count rather than a floor: a generator change that halved a corpus would
// otherwise quietly halve this sweep and still pass.
const expectedPairs = fixtures.reduce(
  (total: number, fixture: { candidates: readonly PlannerCandidate[] }) =>
    total + (fixture.candidates.length * (fixture.candidates.length - 1)) / 2,
  0,
);
assert(
  pairs === expectedPairs && pairs === 6_048,
  `the sweep must cover all ${expectedPairs} candidate pairs (3 x 64 photographs), saw ${pairs}`,
);
assert(
  crossings === 0,
  `${crossings} candidate pairs crossed the ${DUPLICATE_BAR} duplicate bar because of the codec`,
);
assert(
  int8Crossings > 0,
  "VACUITY: the rejected int8 codec must move at least one real candidate pair " +
    `across the ${DUPLICATE_BAR} hard duplicate bar`,
);
assert(
  worstCosineDrift < closestApproach,
  `the codec moves a cosine by ${worstCosineDrift.toExponential(2)} and the nearest ` +
    `pair sits ${closestApproach.toExponential(2)} from the bar -- there is no margin left`,
);

// The margin is the claim, so it is printed rather than merely asserted: a
// future embedding change that shrinks it will show up here before it shows up
// as a photograph nobody can explain the absence of.
const margin = closestApproach / Math.max(worstCosineDrift, Number.MIN_VALUE);
assert(
  margin > 1_000,
  `the safety margin fell to ${margin.toFixed(0)}x; below ~1000x the float32 ` +
    "decision should be re-taken against float64 rather than assumed",
);

console.log(
  `deep-signal parity self-check passed (6 plans, ${pairs.toLocaleString()} pairs; ` +
    `component drift ${worstComponentDrift.toExponential(2)}, cosine drift ` +
    `${worstCosineDrift.toExponential(2)}, nearest pair ${closestApproach.toExponential(2)} ` +
    `from the ${DUPLICATE_BAR} bar = ${Math.round(margin).toLocaleString()}x margin; ` +
    `int8 sabotage crossings ${int8Crossings}, moved plans ${int8Moved.join(", ") || "none"})`,
);
