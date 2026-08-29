/**
 * The answer reaches the planner.
 *
 * `album-priority.test.ts` already pins the GATE, calling `planAlbum` directly
 * with a policy. That test passed the whole time the feature was dead: the
 * planner could honour a priority map, and nothing in the app could hand it one.
 * `selectBestShotsWithObservations` had no argument for it, so every album was
 * planned as though the question had never been asked, and a questionnaire built
 * on top would have changed nothing at all.
 *
 * So this file deliberately does NOT test the gate. It tests the wire: that an
 * answer entering at the selector's public surface comes out the far side as a
 * different album. A test of the gate cannot fail when the wire is cut, which is
 * exactly how the gap survived.
 *
 * Run: node --experimental-strip-types src/selection/album-priority-wiring.test.ts
 */

// @ts-expect-error Node requires the extension; Metro resolves this path too.
import { selectBestShotsWithObservations } from "./select-best-shots.ts";

function assert(condition: boolean, message: string): void {
  if (!condition) {
    console.error(`FAIL: ${message}`);
    process.exit(1);
  }
}

/**
 * A photo good enough that only the priority rule can keep it out.
 *
 * Quality is deliberately uniform. If the chosen people also had the better
 * photographs, a passing test would be indistinguishable from ordinary ranking
 * doing its job, and the wire could still be cut.
 */
function photo(id: string, personIds: string[], creationTime: number) {
  return {
    id,
    uri: `file:///${id}.jpg`,
    filename: `${id}.jpg`,
    width: 4000,
    height: 3000,
    creationTime,
    quality: 0.8,
    personIds,
    placeKey: "home",
    source: "device-gallery" as const,
  };
}

// Six photographs of a stranger, three of a chosen person, three of both. The
// stranger's are spread across different days so time diversity cannot be what
// excludes them.
const DAY = 24 * 60 * 60 * 1000;
const photos = [
  ...Array.from({ length: 6 }, (_, i) =>
    photo(`stranger-${i}`, ["person-stranger"], 1_700_000_000_000 + i * DAY),
  ),
  ...Array.from({ length: 3 }, (_, i) =>
    photo(`chosen-${i}`, ["person-chosen"], 1_700_000_000_000 + (10 + i) * DAY),
  ),
  ...Array.from({ length: 3 }, (_, i) =>
    photo(`both-${i}`, ["person-stranger", "person-chosen"], 1_700_000_000_000 + (20 + i) * DAY),
  ),
];

function strangersAloneIn(ids: readonly string[]): number {
  return ids.filter((id) => id.startsWith("stranger-")).length;
}

// 1. Unasked. Every id is admissible, so the stranger's photographs are free to
//    appear -- and must, or the comparison below proves nothing.
const unasked = selectBestShotsWithObservations(photos as never, { count: 12 })
  .album.selected.map((item) => item.media_id);
const strangersWhenUnasked = strangersAloneIn(unasked);
assert(
  strangersWhenUnasked > 0,
  `setup is inert: the stranger never appears even unasked (${unasked.length} selected), ` +
    `so a priority rule has nothing to remove and this file cannot detect a cut wire`,
);

// 2. Asked. The stranger was not chosen, so photographs of the stranger ALONE
//    must go, while photographs where they stand beside a chosen person stay.
const asked = selectBestShotsWithObservations(photos as never, {
  count: 12,
  personPriority: { "person-chosen": "high" },
}).album.selected.map((item) => item.media_id);

assert(
  strangersAloneIn(asked) === 0,
  `the answer did not reach the planner: ${strangersAloneIn(asked)} stranger-only ` +
    `photos survived being marked low priority`,
);
assert(
  asked.some((id) => id.startsWith("both-")),
  "a low-priority person was excluded even alongside a chosen one, which is a " +
    "harder rule than asked for",
);
assert(
  asked.some((id) => id.startsWith("chosen-")),
  "the chosen person is missing from their own album",
);

// 3. SABOTAGE. An empty map is the "never asked" case and must behave like case
//    1, NOT like "everyone is low priority" -- that reading would gate out the
//    whole library and hand back an empty album. Asserting the opposite outcome
//    here is what stops a rule that simply excludes everything from passing the
//    two checks above.
const emptyAnswer = selectBestShotsWithObservations(photos as never, {
  count: 12,
  personPriority: {},
}).album.selected.map((item) => item.media_id);
assert(
  strangersAloneIn(emptyAnswer) === strangersWhenUnasked,
  `an empty answer changed the album (${strangersAloneIn(emptyAnswer)} vs ` +
    `${strangersWhenUnasked} stranger-only): "no preference" is being read as ` +
    `"everyone is low priority", which empties albums for anyone who skips the question`,
);

console.log(
  `album priority wiring: reaches the planner (${strangersWhenUnasked} stranger-only ` +
    `unasked -> 0 asked, ${asked.length} selected), and an empty answer stays inert`,
);
