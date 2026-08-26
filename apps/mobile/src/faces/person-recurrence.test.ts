// @ts-expect-error Node's TypeScript runner requires the source extension.
import { buildPersonRecurrence } from "./person-recurrence.ts";

function assert(value: unknown, message: string): void {
  if (!value) throw new Error(`person-recurrence self-check failed: ${message}`);
}

const DAY = 24 * 60 * 60 * 1000;
const start = new Date(2025, 0, 1, 12).getTime(); // local noon, not UTC midnight

/** Photo ids and their capture times, shared by every case below. */
const times = new Map<string, number>();
let nextAsset = 0;
function shots(dayOffsets: number[], perDay = 1): string[] {
  const ids: string[] = [];
  for (const offset of dayOffsets) {
    for (let i = 0; i < perDay; i += 1) {
      const id = `a${nextAsset++}`;
      // Spread within the day so same-day shots are genuinely different times.
      times.set(id, start + offset * DAY + i * 60_000);
      ids.push(id);
    }
  }
  return ids;
}

// The stranger: one wedding, photographed heavily. 40 frames, all one occasion.
const stranger = { id: "stranger", assetIds: shots([10], 40) };
// The other stranger: a three-day trip. Still one occasion.
const tripGuest = { id: "trip-guest", assetIds: shots([100, 101, 102], 5) };
// The daughter: turns up all year, a handful of frames at a time.
const daughter = {
  id: "daughter",
  assetIds: shots([0, 3, 30, 61, 95, 140, 200, 260, 330], 3),
};
// A relative seen twice a year. Few photos, but they come back.
const relative = { id: "relative", assetIds: shots([5, 190], 2) };
// Someone with no usable capture times at all.
const undated = { id: "undated", assetIds: ["no-time-1", "no-time-2"] };

const recurrence = buildPersonRecurrence(
  [stranger, tripGuest, daughter, relative, undated],
  (assetId) => times.get(assetId),
);

// The whole point: a heavily photographed stranger must not outrank a rarely
// photographed relative. Face count says 40 vs 4; recurrence says 1 vs 2.
assert(recurrence.sessionCount("stranger") === 1, "40 frames at one wedding is ONE occasion");
assert(recurrence.dayCount("stranger") === 1, "40 frames at one wedding is one day");
assert(
  recurrence.sessionCount("trip-guest") === 1,
  "a three-day trip is one occasion, not three -- consecutive days must not inflate it",
);
assert(recurrence.dayCount("trip-guest") === 3, "the trip still spans three distinct days");
// Nine days, but days 0 and 3 fall inside one fortnight and so are one
// occasion -- eight is the right answer, and getting nine would mean a weekend
// at home counted twice.
assert(
  recurrence.sessionCount("daughter") === 8,
  `a year of visits is eight occasions, got ${recurrence.sessionCount("daughter")}`,
);
assert(recurrence.dayCount("daughter") === 9, "she still appears on nine distinct days");
assert(recurrence.sessionCount("relative") === 2, "twice a year is two occasions");

assert(!recurrence.isFamiliar("stranger"), "one occasion is never familiar");
assert(!recurrence.isFamiliar("trip-guest"), "one long occasion is never familiar");
assert(recurrence.isFamiliar("daughter"), "the daughter is familiar");
assert(
  recurrence.isFamiliar("relative"),
  "a relative with FEWER photos than the stranger is still familiar -- this is the " +
    "case that ranking by rarity or by face count gets backwards",
);

// Undated people must not crash, must not be familiar, and must still be ranked.
assert(recurrence.sessionCount("undated") === 0, "no usable times means no occasions");
assert(!recurrence.isFamiliar("undated"), "a person with no times is not familiar");
assert(recurrence.sessionCount("nobody") === 0, "an unknown id answers 0, not undefined");

const order = recurrence.ranked();
assert(order.length === 5, "every person appears in the ranking");
assert(order[0] === "daughter", `most recurring first, got ${order[0]}`);
assert(order[1] === "relative", `two occasions outrank one, got ${order[1]}`);
assert(
  order.indexOf("trip-guest") < order.indexOf("stranger"),
  "equal occasions break on days, so the three-day guest precedes the one-day one",
);
assert(order[order.length - 1] === "undated", "a person with no times ranks last");

// Stability: identical input, identical order. An album that reshuffles its
// people between opens reads as broken even when every pick is defensible.
const again = buildPersonRecurrence(
  [undated, relative, daughter, tripGuest, stranger],
  (assetId) => times.get(assetId),
);
assert(
  again.ranked().join(",") === order.join(","),
  "the ranking must not depend on the order people were handed in",
);

// The degenerate library: one week old, nobody recurs yet. `ranked` must still
// give a usable order rather than collapsing to nothing.
const fresh = buildPersonRecurrence(
  [
    { id: "p1", assetIds: shots([500], 2) },
    { id: "p2", assetIds: shots([500, 501], 1) },
  ],
  (assetId) => times.get(assetId),
);
assert(!fresh.isFamiliar("p1") && !fresh.isFamiliar("p2"), "nobody is familiar in a one-week library");
assert(fresh.ranked().length === 2 && fresh.ranked()[0] === "p2", "ranking still orders them by days");

/**
 * Days are LOCAL calendar days, not UTC ones.
 *
 * These two are constructed from local components, so they are on different
 * local dates in every timezone -- an evening shot and a small-hours shot the
 * next morning. Counting by UTC would merge them anywhere east of Greenwich,
 * which is where these photos were taken. (In UTC itself the two rules agree,
 * so this case cannot tell them apart there; it still pins the contract.)
 */
const lateNight = new Map<string, number>([
  ["night-1", new Date(2025, 5, 1, 20, 0).getTime()],
  ["night-2", new Date(2025, 5, 2, 2, 0).getTime()],
]);
const straddling = buildPersonRecurrence(
  [{ id: "night-owl", assetIds: ["night-1", "night-2"] }],
  (assetId) => lateNight.get(assetId),
);
assert(
  straddling.dayCount("night-owl") === 2,
  `two different local dates are two days, got ${straddling.dayCount("night-owl")}`,
);
assert(
  straddling.sessionCount("night-owl") === 1,
  "but one night out is still a single occasion",
);

console.log("person-recurrence self-check passed");
