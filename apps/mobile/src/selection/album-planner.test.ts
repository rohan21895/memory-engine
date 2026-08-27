// Pure module self-checks; Node 22's native TypeScript test runner executes
// this file and treats any failed assertion as a failed test.
// @ts-expect-error Node requires the extension while Metro resolves it too.
import { planAlbum, type PlannerCandidate } from "./album-planner.ts";

function assert(value: unknown, message: string): asserts value {
  if (!value) throw new Error(`Album planner self-check failed: ${message}`);
}

function candidate(
  mediaId: string,
  quality: number,
  overrides: Partial<PlannerCandidate> = {},
): PlannerCandidate {
  return { mediaId, quality, ...overrides };
}

function axis(index: number, size = 12) {
  const value = Array<number>(size).fill(0);
  value[index] = 1;
  return value;
}

const hour = 60 * 60 * 1_000;
const day = 24 * hour;
const start = Date.UTC(2026, 2, 1, 9);

// Input order cannot affect a result or a gain tiebreak.
const deterministicPool = Array.from({ length: 12 }, (_, index) =>
  candidate(`photo-${String(index).padStart(2, "0")}`, 0.6 + (index % 4) * 0.05, {
    capturedAt: start + (index % 3) * day,
    placeKey: `place-${index % 2}`,
    embedding: axis(index),
  }),
);
const forward = planAlbum(deterministicPool, 6).selectedIds;
const reverse = planAlbum(deterministicPool.slice().reverse(), 6).selectedIds;
assert(JSON.stringify(forward) === JSON.stringify(reverse), "selection must ignore input order");

// Coverage across time beats taking every slightly-better frame from day one.
const temporal = planAlbum(
  [
    candidate("day-1-a", 0.95, { capturedAt: start, embedding: axis(0) }),
    candidate("day-1-b", 0.94, { capturedAt: start + hour, embedding: axis(1) }),
    candidate("day-1-c", 0.93, { capturedAt: start + 2 * hour, embedding: axis(2) }),
    candidate("day-2", 0.76, { capturedAt: start + day, embedding: axis(3) }),
  ],
  2,
);
assert(temporal.selectedIds.includes("day-2"), "a later day must earn a coverage slot");

// People are a hard floor, not a soft weight.
const people = planAlbum(
  [
    candidate("ava-best", 0.98, { personIds: ["ava"], embedding: axis(0) }),
    candidate("ava-two", 0.96, { personIds: ["ava"], embedding: axis(1) }),
    candidate("bo-only", 0.55, { personIds: ["bo"], embedding: axis(2) }),
  ],
  2,
);
assert(people.selectedIds.includes("bo-only"), "the quiet person must not be omitted");
assert(people.missingPersonIds.length === 0, "covered people must not be reported missing");

const groupCoverage = planAlbum(
  [
    candidate("group", 0.6, { personIds: ["ava", "bo", "cy"] }),
    candidate("ava", 0.99, { personIds: ["ava"] }),
    candidate("bo", 0.98, { personIds: ["bo"] }),
    candidate("cy", 0.97, { personIds: ["cy"] }),
  ],
  1,
);
assert(groupCoverage.selectedIds[0] === "group", "people floor must use greedy max coverage");

// The per-person cap and scenery reservation prevent one face taking the book.
const balanced = planAlbum(
  [
    ...Array.from({ length: 5 }, (_, index) =>
      candidate(`ava-${index}`, 0.95 - index * 0.01, {
        personIds: ["ava"],
        embedding: axis(index),
      }),
    ),
    candidate("bo", 0.7, { personIds: ["bo"], embedding: axis(5) }),
    candidate("scene-a", 0.65, { embedding: axis(6) }),
    candidate("scene-b", 0.64, { embedding: axis(7) }),
  ],
  4,
  { policy: { minNonPeopleFraction: 0.5 } },
);
assert(balanced.personCounts.ava <= 2, "per-person cap must hold during fill");
assert(
  balanced.selectedIds.filter((id) => id.startsWith("scene-")).length === 2,
  "reserved non-people slots must be filled when scenery exists",
);

// Calibrated MMR and the distinctness backstop choose a different picture.
const mmr = planAlbum(
  [
    candidate("hero", 0.95, { embedding: [1, 0, 0], capturedAt: start }),
    candidate("near-copy", 0.94, { embedding: [0.995, 0.1, 0], capturedAt: start + hour }),
    candidate("different", 0.72, { embedding: [0, 0, 1], capturedAt: start + 2 * hour }),
  ],
  2,
);
assert(mmr.selectedIds.includes("different"), "a distinct photo must beat a near-copy");
assert(!mmr.selectedIds.includes("near-copy"), "near-copy should remain out while variety exists");

// Body-pose cap is relaxed last: show each pose twice before a third repeat.
const poses = planAlbum(
  [
    ...Array.from({ length: 5 }, (_, index) =>
      candidate(`pose-a-${index}`, 0.95 - index * 0.01, {
        poseCluster: "A",
        embedding: axis(index),
      }),
    ),
    candidate("pose-b-0", 0.65, { poseCluster: "B", embedding: axis(6) }),
    candidate("pose-b-1", 0.64, { poseCluster: "B", embedding: axis(7) }),
  ],
  4,
);
assert(
  poses.selectedIds.filter((id) => id.startsWith("pose-a-")).length === 2 &&
    poses.selectedIds.filter((id) => id.startsWith("pose-b-")).length === 2,
  "pose cap must exhaust pose variety before a third repeat",
);

const onePose = planAlbum(
  Array.from({ length: 4 }, (_, index) =>
    candidate(`only-pose-${index}`, 0.9 - index * 0.01, {
      poseCluster: "A",
      embedding: axis(index),
    }),
  ),
  4,
);
assert(onePose.selectedIds.length === 4, "pose cap must relax rather than shorten the album");

// Rare moments and scarce people waive soft floors, never absolute screenshot gates.
const rare = planAlbum(
  [
    candidate("rare-soft", 0.3, { capturedAt: start }),
    candidate("later", 0.9, { capturedAt: start + day }),
  ],
  2,
);
assert(rare.rescuedIds.includes("rare-soft"), "an isolated moment must survive the quality floor");

const scarce = planAlbum(
  [
    candidate("gran-only", 0.2, { personIds: ["gran"], capturedAt: start }),
    candidate("family", 0.9, { personIds: ["ava"], capturedAt: start + hour }),
  ],
  2,
);
assert(scarce.selectedIds.includes("gran-only"), "the only photo of a person must be rescued");

const receipt = planAlbum(
  [candidate("receipt", 0.99, { personIds: ["gran"], screenshotDocument: true })],
  1,
);
assert(receipt.selectedIds.length === 0, "a screenshot/document is never rescued");

// Pins are sovereign; excludes remain absolute; contradictory intent raises.
const pin = planAlbum(
  [
    candidate("awful-pin", 0.01, { pinned: true, screenshotDocument: true, cutFace: true }),
    candidate("good", 0.99),
  ],
  1,
);
assert(pin.selectedIds[0] === "awful-pin", "a known pin must bypass every gate");

const excluded = planAlbum(
  [candidate("best", 0.99, { excluded: true }), candidate("second", 0.8)],
  1,
);
assert(excluded.selectedIds[0] === "second", "an excluded photo must never return");

let contradictionRaised = false;
try {
  planAlbum([candidate("x", 0.8)], 1, {
    policy: { pinnedMediaIds: ["x"], excludedMediaIds: ["x"] },
  });
} catch {
  contradictionRaised = true;
}
assert(contradictionRaised, "pin/exclude contradiction must be refused");

// An album of ONE person must not be halved by the per-person cap.
// perPersonCap() falls off `target` and onto maxPerPersonFraction the moment a
// second face appears anywhere in the set. Every photo in a person-filtered
// album holds the same face, so one incidental bystander was enough: 20 photos
// were asked for and 10 came back, with no error and no explanation.
const onePersonAlbum = planAlbum(
  Array.from({ length: 30 }, (_, index) =>
    candidate(`ava-${String(index).padStart(2, "0")}`, 0.6 + (index % 5) * 0.02, {
      personIds: index % 3 === 0 ? ["ava", "bo"] : ["ava"],
    }),
  ),
  20,
);
assert(
  onePersonAlbum.selectedIds.length === 20,
  "the per-person cap must relax rather than shorten the album",
);
const onePersonSelected = new Set(onePersonAlbum.selectedIds);
assert(
  onePersonAlbum.rejected.every(({ mediaId }) => !onePersonSelected.has(mediaId)),
  "a photo that made the album must never also be reported as rejected",
);

// The cap must still bind while there is anything else to take.
const stillCapped = planAlbum(
  [
    ...Array.from({ length: 6 }, (_, index) =>
      candidate(`face-${index}`, 0.95 - index * 0.01, { personIds: ["ava"] }),
    ),
    candidate("scene-x", 0.6),
    candidate("scene-y", 0.59),
  ],
  4,
  { policy: { minNonPeopleFraction: 0.5 } },
);
assert(stillCapped.personCounts.ava <= 2, "relaxation must not disarm the cap outright");

// Standing is a MIDRANK percentile, not a CDF. Without the halving the top of
// every comparison class scores a full 1.0 -- and every member of an all-tied
// class is its own class's top, so all of them claimed to be the clearest.
const equalQuality = planAlbum(
  Array.from({ length: 10 }, (_, index) =>
    candidate(`tied-${index}`, 0.5, { comparisonClass: "portrait" }),
  ),
  4,
);
assert(equalQuality.selectedIds.length === 4, "equally good photos still fill the album");
assert(
  equalQuality.selectedIds.every(
    (mediaId) =>
      !(equalQuality.reasonsByMediaId[mediaId] ?? []).includes(
        "One of the clearest photos in its group.",
      ),
  ),
  "ten equally good photos cannot each be the clearest of their group",
);

// A comparison class of one is ALWAYS its own top, so the CDF handed a lone
// mediocre frame the standing of a hero shot on the dominant gain term.
const loneClass = planAlbum(
  [
    ...Array.from({ length: 8 }, (_, index) =>
      candidate(`hero-${index}`, 0.8 + index * 0.01, { comparisonClass: "portrait" }),
    ),
    candidate("lone-detail", 0.65, { comparisonClass: "detail" }),
  ],
  9,
);
assert(
  !(loneClass.reasonsByMediaId["lone-detail"] ?? []).includes(
    "One of the clearest photos in its group.",
  ),
  "the only member of a comparison class is not thereby one of its clearest",
);

// --- M6: the same product invariants under the submodular selector ----------
//
// `selector` is a rollback switch, which means somebody will eventually flip
// it. Everything above is a promise the product makes about an album — a pin is
// sovereign, an exclusion is absolute, nobody is left out, an album is never
// SHORTER than it was asked for — and none of those promises are about which
// decision rule computed it. So they are re-asserted here rather than left as
// properties of one branch.

const submodular = { selector: "submodular" } as const;

// The flag must actually route. Without this, every assertion below could be
// silently re-testing the discrete-key greedy and passing for that reason.
const routed = planAlbum([candidate("a", 0.5), candidate("b", 0.6)], 1, {
  policy: submodular,
});
assert(routed.objectiveTrace !== undefined, "the submodular selector must report an objective trace");
assert(
  planAlbum([candidate("a", 0.5), candidate("b", 0.6)], 1).objectiveTrace === undefined,
  "VACUITY: the shipped selector must NOT report one, or the check above proves nothing",
);

assert(
  planAlbum([], 24, { policy: submodular }).selectedIds.length === 0 &&
    planAlbum([candidate("a", 0.5)], 0, { policy: submodular }).selectedIds.length === 0,
  "an empty pool and a zero target must both return an empty album",
);

const subPin = planAlbum(
  [
    candidate("awful-pin", 0.01, { pinned: true, screenshotDocument: true, cutFace: true }),
    candidate("good", 0.99),
  ],
  1,
  { policy: submodular },
);
assert(subPin.selectedIds[0] === "awful-pin", "a pin must bypass every gate under either selector");

const subExcluded = planAlbum(
  [candidate("best", 0.99, { excluded: true }), candidate("second", 0.8)],
  1,
  { policy: submodular },
);
assert(subExcluded.selectedIds[0] === "second", "an excluded photo must never return under either selector");

assert(
  planAlbum([candidate("receipt", 0.99, { personIds: ["gran"], screenshotDocument: true })], 1, {
    policy: submodular,
  }).selectedIds.length === 0,
  "a screenshot/document is never rescued under either selector",
);

const subPeople = planAlbum(
  [
    candidate("ava-best", 0.98, { personIds: ["ava"], embedding: axis(0) }),
    candidate("ava-two", 0.96, { personIds: ["ava"], embedding: axis(1) }),
    candidate("bo-only", 0.55, { personIds: ["bo"], embedding: axis(2) }),
  ],
  2,
  { policy: submodular },
);
assert(subPeople.selectedIds.includes("bo-only"), "the quiet person must not be omitted under either selector");

const subScarce = planAlbum(
  [
    candidate("gran-only", 0.2, { personIds: ["gran"], capturedAt: start }),
    candidate("family", 0.9, { personIds: ["ava"], capturedAt: start + hour }),
  ],
  2,
  { policy: submodular },
);
assert(subScarce.selectedIds.includes("gran-only"), "the only photo of a person must be rescued under either selector");

// Each of the three relaxable caps, alone, must widen rather than shorten.
assert(
  planAlbum(
    Array.from({ length: 30 }, (_, index) =>
      candidate(`ava-${String(index).padStart(2, "0")}`, 0.6 + (index % 5) * 0.02, {
        personIds: index % 3 === 0 ? ["ava", "bo"] : ["ava"],
      }),
    ),
    20,
    { policy: submodular },
  ).selectedIds.length === 20,
  "the per-person cap must relax rather than shorten the album",
);
assert(
  planAlbum(
    Array.from({ length: 4 }, (_, index) =>
      candidate(`only-pose-${index}`, 0.9 - index * 0.01, {
        poseCluster: "A",
        embedding: axis(index),
      }),
    ),
    4,
    { policy: submodular },
  ).selectedIds.length === 4,
  "the pose cap must relax rather than shorten the album",
);
// The hard near-duplicate constraint is the one the submodular selector ADDS,
// so it is the one most able to empty an album. Ten byte-identical embeddings
// leave it no distinct choice at all; it must still return five photographs.
const allIdentical = planAlbum(
  Array.from({ length: 10 }, (_, index) =>
    candidate(`same-${index}`, 0.5, { embedding: [1, 0, 0], capturedAt: start + index * 1_000 }),
  ),
  5,
  { policy: submodular },
);
assert(
  allIdentical.selectedIds.length === 5,
  `the duplicate ceiling must relax rather than shorten the album (got ${allIdentical.selectedIds.length})`,
);

