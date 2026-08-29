/**
 * Who the user picked decides who gets in.
 *
 * The requirement is a HARD GATE, not a preference: a photograph of only
 * lower-priority people stays out however good it is, and gets in only when
 * someone the user picked is in it too. That distinction is the whole point of
 * asking the question, so it is the thing pinned here — a score penalty would
 * pass a casual read of the code and still let a run of excellent photographs of
 * unchosen people take over the album.
 *
 * Every case below is also shown to be capable of failing: a gate that is never
 * reached, or a cap on a person who was never going to fill the album anyway,
 * proves nothing. The last block sabotages the setup deliberately and asserts
 * the OPPOSITE outcome, so a silently inert gate fails this file.
 */

// @ts-expect-error Node requires the extension; Metro resolves this path too.
import { DEFAULT_PLANNER_POLICY, planAlbum } from "./album-planner.ts";

function assert(condition: boolean, message: string): void {
  if (!condition) throw new Error(`album priority: ${message}`);
}

const hour = 60 * 60 * 1_000;
const start = Date.UTC(2026, 2, 1, 9);

let axis = 0;
function candidate(
  mediaId: string,
  quality: number,
  personIds: string[],
  offsetHours: number,
) {
  // A distinct direction per photo, so nothing is rejected as a near-duplicate
  // and every exclusion below is the priority gate rather than redundancy.
  const embedding = Array.from({ length: 16 }, (_, i) => (i === axis % 16 ? 1 : 0));
  axis += 1;
  return {
    mediaId,
    quality,
    personIds,
    embedding,
    capturedAt: start + offsetHours * hour,
    placeKey: `place-${offsetHours % 3}`,
  };
}

// Deliberately stacked AGAINST the gate: the unchosen people have the best
// photographs in the library. Only a hard gate keeps them out.
const pool = [
  ...Array.from({ length: 8 }, (_, i) =>
    candidate(`stranger-${i}`, 0.98, ["person-stranger"], i),
  ),
  ...Array.from({ length: 6 }, (_, i) =>
    candidate(`high-${i}`, 0.55, ["person-high"], 10 + i),
  ),
  ...Array.from({ length: 6 }, (_, i) =>
    candidate(`medium-${i}`, 0.55, ["person-medium"], 20 + i),
  ),
  // The escape hatch the requirement explicitly grants: a lower-priority person
  // IS allowed in, in the company of someone chosen.
  ...Array.from({ length: 3 }, (_, i) =>
    candidate(`together-${i}`, 0.55, ["person-stranger", "person-high"], 30 + i),
  ),
  // Scenery. Never caught by the gate: it has no people to be the wrong people.
  ...Array.from({ length: 3 }, (_, i) => candidate(`scenery-${i}`, 0.6, [], 40 + i)),
];

const policy = {
  ...DEFAULT_PLANNER_POLICY,
  personPriority: { "person-high": "high", "person-medium": "medium" } as const,
};

const plan = planAlbum(pool, 12, { policy });
const chosen = new Set(plan.selectedIds);

// 1. The gate holds, against the best photographs in the pool.
const strangersAlone = [...chosen].filter((id) => id.startsWith("stranger-"));
assert(
  strangersAlone.length === 0,
  `photos of only unpicked people must be excluded, got ${strangersAlone.join(", ")}`,
);

// 2. ...and it is not simply excluding that person everywhere.
const together = [...chosen].filter((id) => id.startsWith("together-"));
assert(
  together.length > 0,
  "a lower-priority person must still appear alongside someone picked",
);

// 3. The rejection says WHY, in the code the UI reads.
const gated = plan.rejected.filter(
  (rejection: { mediaId: string; reasonCode: string }) =>
    rejection.mediaId.startsWith("stranger-"),
);
assert(gated.length === 8, `all 8 stranger-only photos must be reported, got ${gated.length}`);
assert(
  gated.every((r: { reasonCode: string }) => r.reasonCode === "low_priority_people"),
  "the reason must name the people, not borrow a quality excuse",
);

// 4. Scenery survives: the gate is about company, not about faces being absent.
assert(
  [...chosen].some((id) => id.startsWith("scenery-")),
  "photos with no people must not be caught by the priority gate",
);

// 5. High priority outnumbers medium. This is the comparative half of the
//    requirement -- "maximum of high, less of medium" -- and it is a separate
//    mechanism from the gate, so it gets its own assertion.
const highCount = [...chosen].filter(
  (id) => id.startsWith("high-") || id.startsWith("together-"),
).length;
const mediumCount = [...chosen].filter((id) => id.startsWith("medium-")).length;
assert(
  highCount > mediumCount,
  `high priority must outnumber medium (high ${highCount}, medium ${mediumCount})`,
);

// 6. VACUITY. With no answers recorded the question was never asked, and the
//    library must behave exactly as it did before priorities existed -- the
//    strangers' excellent photographs should now dominate. If this comes back
//    empty too, then something ELSE is excluding them and cases 1-3 above were
//    passing for the wrong reason.
const unasked = planAlbum(pool, 12, { policy: DEFAULT_PLANNER_POLICY });
const strangersWhenUnasked = unasked.selectedIds.filter((id: string) =>
  id.startsWith("stranger-"),
);
assert(
  strangersWhenUnasked.length > 0,
  "with no priorities recorded the gate must not fire at all -- the checks above prove nothing otherwise",
);

console.log(
  `album priority: gate holds (0 stranger-only of 8, ${together.length} alongside a pick), ` +
    `high ${highCount} > medium ${mediumCount}, ` +
    `${strangersWhenUnasked.length} strangers return when the question is unasked`,
);
