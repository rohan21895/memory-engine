import {
  avatarBackfillQueue,
  // @ts-expect-error Node's TypeScript runner requires the source extension.
} from "./face-index.ts";
import type { Person } from "./types.ts";

function assert(value: unknown, message: string): asserts value {
  if (!value) throw new Error(`avatar-queue self-check failed: ${message}`);
}

/**
 * The starvation this ordering exists to prevent.
 *
 * Cutting an avatar costs a photo decode plus a detection plus an embedding, so
 * the pass runs on a per-launch budget and picks up next time. Sorted on tile
 * size alone that never terminates: a person whose only photos are ambiguous
 * group shots fails every pass, failing does not move them out of the way, and
 * so the same few hundred hopeless people absorb the entire budget on every
 * launch. Measured on the owner's library -- 449 avatars, 1,788 people still
 * waiting, and the same names at the front of the queue each time.
 *
 * Ordering on the attempt count first turns it into a round robin: everybody is
 * looked at once before anybody is looked at twice.
 */

const person = (id: string, faceCount: number, avatarTries?: number): Person => ({
  id,
  faceCount,
  assetIds: [`${id}-a`],
  centroid: [1, 0],
  embeddingKind: "identity",
  ...(avatarTries === undefined ? {} : { avatarTries }),
});

const ids = (people: readonly Person[]): string => people.map((p) => p.id).join(",");

// A person already tried goes behind an untried one, however much bigger their
// tile is. This is the whole fix.
{
  const queue = avatarBackfillQueue([
    person("hopeless", 400, 3),
    person("fresh", 2),
  ]);
  assert(
    ids(queue) === "fresh,hopeless",
    `an untried person must come first, got ${ids(queue)}`,
  );
}

// Within one attempt tier the big tiles still lead -- the budget should reach
// the people the owner actually looks at first.
{
  const queue = avatarBackfillQueue([
    person("small", 2),
    person("huge", 900),
    person("middling", 40),
  ]);
  assert(
    ids(queue) === "huge,middling,small",
    `equal attempts must order by tile size, got ${ids(queue)}`,
  );
}

// Never tried and tried zero times are the same state; a person written before
// the counter existed must not be treated as a fresh arrival forever.
{
  const queue = avatarBackfillQueue([person("legacy", 5), person("counted", 900, 0)]);
  assert(
    ids(queue) === "counted,legacy",
    `an absent count must read as zero, got ${ids(queue)}`,
  );
}

// Somebody who already has a face is not in the queue at all.
{
  const done: Person = { ...person("done", 900), avatarUri: "file://x.jpg" };
  const queue = avatarBackfillQueue([done, person("waiting", 2)]);
  assert(ids(queue) === "waiting", `only faceless people queue, got ${ids(queue)}`);
}

/**
 * THE case. The queue has to be able to become EMPTY.
 *
 * Ordering by attempts stopped the pass starving its tail, but it never let
 * anyone leave the queue, and that is not slow convergence — it is a permanent
 * tax on every launch. On the owner's phone 552 people had no avatar and every
 * one had already failed four or five times, yet they still cost 910 photo
 * decodes per launch, each a decode plus a detection plus an embedding. He
 * reported the app as "super laggy". This is that bug.
 */
{
  const exhausted = [
    person("tried-4", 300, 4),
    person("tried-9", 200, 9),
  ];
  assert(
    avatarBackfillQueue(exhausted).length === 0,
    `people who have been tried enough must leave the queue, ` +
      `got ${ids(avatarBackfillQueue(exhausted))}`,
  );
  // Vacuity guard: the same people one attempt earlier ARE still queued, so the
  // emptiness above is the cap doing its job and not a filter that rejects
  // everything.
  const stillTrying = [person("tried-3", 300, 3)];
  assert(
    avatarBackfillQueue(stillTrying).length === 1,
    "somebody below the cap must still be offered work",
  );
}

// A library where every faceless person is exhausted costs nothing at all —
// the property that actually makes launches quiet again.
{
  const settled: Person[] = [
    { ...person("has-face", 900), avatarUri: "file://a.jpg" },
    person("gave-up-1", 50, 4),
    person("gave-up-2", 20, 5),
  ];
  assert(
    avatarBackfillQueue(settled).length === 0,
    `a settled library must schedule zero work, got ${ids(avatarBackfillQueue(settled))}`,
  );
}

/**
 * The property that actually matters, stated as the thing that was broken:
 * across repeated budgeted passes where every attempt FAILS, every person is
 * eventually looked at. Under the old size-only ordering this loop never
 * reaches the tail no matter how many passes run.
 */
{
  const BUDGET = 3;
  const people = Array.from({ length: 10 }, (_, i) =>
    person(`p${i}`, 100 - i),
  );
  const seen = new Set<string>();
  for (let pass = 0; pass < 4; pass += 1) {
    for (const candidate of avatarBackfillQueue(people).slice(0, BUDGET)) {
      seen.add(candidate.id);
      // Every attempt fails, which is the hostile case.
      candidate.avatarTries = (candidate.avatarTries ?? 0) + 1;
    }
  }
  assert(
    seen.size === people.length,
    `four passes of ${BUDGET} must reach all ${people.length} people, reached ${seen.size}`,
  );
  // Vacuity guard: the budget really is smaller than the queue, so this could
  // have failed. Sorting by size alone would have seen exactly three.
  assert(BUDGET < people.length, "the budget must not cover the queue in one pass");
}

console.log("avatar-queue self-check passed");
