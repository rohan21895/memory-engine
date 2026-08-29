// @ts-expect-error The Expo app deliberately does not ship Node type declarations.
import { readFileSync } from "node:fs";

// @ts-expect-error Node requires the extension; Metro resolves this path too.
import { planAlbum, type PlannerCandidate } from "./album-planner.ts";

function assert(value: unknown, message: string): asserts value {
  if (!value) throw new Error(`semantic diversity self-check failed: ${message}`);
}

/**
 * Album variety must use the signal that can actually see variety.
 *
 * The owner's report: "images chosen are not different, do some pose detection,
 * background detection, clothes detection ... give me best and unique photos".
 *
 * The shipped redundancy signal was the phone's 76-dim perceptual fingerprint,
 * an 8x8 luma grid plus a coarse colour histogram. That is a near-duplicate
 * detector. It answers "is this the same frame twice" and cannot answer "same
 * person, same outfit, same room, ten minutes apart", because at 8x8 those are
 * simply two different arrangements of light. TinyCLIP's 512-dim embedding CAN,
 * and it was computed for every candidate and then used for diversity only when
 * the perceptual fingerprint failed to compute -- which is almost never.
 *
 * THIS FILE EXISTS BECAUSE THE PINNED FIXTURES CANNOT COVER IT. `album-fixtures`
 * carries no semantic embeddings at all, so it passed both before and after the
 * change: proof of nothing. A diversity feature that never fires is the exact
 * failure this repo has shipped before.
 */

/** Unit vectors far apart in perceptual space, so the OLD signal sees variety. */
function axis(index: number, size = 8): number[] {
  return Array.from({ length: size }, (_, position) => (position === index ? 1 : 0));
}

/** Two scenes in TinyCLIP space: `near` pairs are the same room and outfit. */
function scene(index: number, jitter = 0): number[] {
  const base = Array.from({ length: 16 }, (_, position) =>
    position === index ? 1 : position === index + 1 ? 0.6 : 0.05,
  );
  return base.map((value, position) => value + (position === 15 ? jitter : 0));
}

function candidate(
  mediaId: string,
  quality: number,
  extra: Partial<PlannerCandidate> = {},
): PlannerCandidate {
  return { mediaId, quality, embeddingSpace: "phone-perceptual-v1", ...extra };
}

// --- 1. The semantic penalty must change a real selection. -------------------
//
// Three candidates, all visually distinct to the perceptual signal (different
// axes), all of similar quality. Two share a scene; one is elsewhere. Asked for
// two, the album must not be two photographs of the same room.

const pool: PlannerCandidate[] = [
  candidate("kitchen-best", 0.92, { embedding: axis(0), semanticEmbedding: scene(0) }),
  candidate("kitchen-again", 0.9, { embedding: axis(1), semanticEmbedding: scene(0, 0.01) }),
  candidate("garden", 0.88, { embedding: axis(2), semanticEmbedding: scene(8) }),
];

const chosen = planAlbum(pool, 2).selectedIds;
assert(
  chosen.includes("kitchen-best"),
  `the strongest photo must still be chosen (got ${chosen.join(", ")})`,
);
assert(
  chosen.includes("garden") && !chosen.includes("kitchen-again"),
  `a second photo of the same scene must lose to a different one (got ${chosen.join(", ")})`,
);

// --- 2. VACUITY: quality alone would have chosen differently. ----------------
//
// Without the semantic signal, the same pool ranks purely on quality and the
// two kitchen photos win. If this assertion ever fails, assertion 1 above is
// passing for some reason other than the feature under test.

const blind = planAlbum(
  pool.map(({ semanticEmbedding: _drop, ...rest }) => rest),
  2,
).selectedIds;
assert(
  blind.includes("kitchen-again") && !blind.includes("garden"),
  `VACUITY: without semantics the near-scene duplicate must win on quality (got ${blind.join(", ")})`,
);

// --- 3. It must not fire when there is nothing to fire on. -------------------
//
// Every real album predates TinyCLIP for some photos, and a missing signal must
// cost a photo nothing rather than scoring as maximally redundant.

const unlabelled = planAlbum(
  [
    candidate("a", 0.9, { embedding: axis(0) }),
    candidate("b", 0.8, { embedding: axis(1) }),
    candidate("c", 0.7, { embedding: axis(2) }),
  ],
  2,
).selectedIds;
assert(
  unlabelled.includes("a") && unlabelled.includes("b"),
  `photos with no semantic signal must rank on quality as before (got ${unlabelled.join(", ")})`,
);

// A photo that HAS the signal must not be punished for being the only one.
const lonely = planAlbum(
  [
    candidate("plain", 0.9, { embedding: axis(0) }),
    candidate("tagged", 0.89, { embedding: axis(1), semanticEmbedding: scene(0) }),
  ],
  2,
).selectedIds;
assert(lonely.length === 2, "a lone semantic photo must not be excluded by its own signal");

// --- 3b. A burst must not fill a small album. --------------------------------
//
// The report that prompted this: a five-photo album where photos 1 and 2 were
// near-identical and so were 3 and 4. Two of the five were wasted.
//
// The hard duplicate gate does not cover this. It fires at 0.92 and these pairs
// sit below it -- "same room, same pose, two seconds apart" is not the same
// frame twice, and a person still calls it the same photograph. So the only
// thing standing between him and a repeated album is the redundancy penalty
// beating a quality gap, and here the gap is large and in the wrong direction:
// the burst frames are the sharpest photos in the pool by 0.24.
//
// Measured, at pair similarities of 0.885, 0.940 and 0.976, the planner takes
// ONE frame from each burst and fills the rest with distinct photographs. This
// pins that, because it is a property a threshold nudge could quietly undo.

{
  const unit = (values: number[]): number[] => {
    const norm = Math.hypot(...values);
    return values.map((value) => value / norm);
  };
  /** `blend` 0 is the base frame; larger is further from it. */
  const near = (seed: number, blend: number): number[] => {
    const base = Array.from({ length: 16 }, (_, i) => Math.cos((seed + 1) * (i + 1)));
    const away = Array.from({ length: 16 }, (_, i) => Math.sin((seed + 7) * (i + 3)));
    return unit(base.map((value, i) => value * (1 - blend) + away[i] * blend));
  };
  const shot = (mediaId: string, quality: number, vector: number[]): PlannerCandidate =>
    candidate(mediaId, quality, { embedding: vector, semanticEmbedding: vector });

  for (const blend of [0.2, 0.3, 0.4]) {
    const burst = [
      shot("burst-a1", 0.95, near(1, 0)),
      shot("burst-a2", 0.94, near(1, blend)),
      shot("burst-b1", 0.93, near(2, 0)),
      shot("burst-b2", 0.92, near(2, blend)),
      shot("solo-1", 0.7, near(3, 0)),
      shot("solo-2", 0.68, near(4, 0)),
      shot("solo-3", 0.66, near(5, 0)),
      shot("solo-4", 0.64, near(6, 0)),
    ];
    const picked = planAlbum(burst, 5).selectedIds;
    const pairs = [
      ["burst-a1", "burst-a2"],
      ["burst-b1", "burst-b2"],
    ];
    for (const [first, second] of pairs) {
      assert(
        !(picked.includes(first) && picked.includes(second)),
        `both halves of a burst were chosen at blend ${blend} (got ${picked.join(", ")})`,
      );
    }
    assert(
      picked.length === 5,
      `and the album must still be filled (got ${picked.length})`,
    );
  }

  // VACUITY: without the penalty, quality alone fills the album with the burst.
  // If this stops being true, the loop above is passing on quality ordering.
  const blindBurst = [
    shot("burst-a1", 0.95, near(1, 0)),
    shot("burst-a2", 0.94, near(1, 0.3)),
    shot("burst-b1", 0.93, near(2, 0)),
    shot("burst-b2", 0.92, near(2, 0.3)),
    shot("solo-1", 0.7, near(3, 0)),
  ].map(({ embedding: _e, semanticEmbedding: _s, ...rest }) => rest);
  const blindPick = planAlbum(blindBurst, 4).selectedIds;
  assert(
    blindPick.includes("burst-a1") && blindPick.includes("burst-a2"),
    `VACUITY: with no signal at all the burst must win on quality (got ${blindPick.join(", ")})`,
  );
}

// --- 4. Determinism. Selection is shown to users and must not shuffle. -------

const forward = planAlbum(pool, 2).selectedIds;
const reverse = planAlbum(pool.slice().reverse(), 2).selectedIds;
assert(
  JSON.stringify(forward) === JSON.stringify(reverse),
  "semantic redundancy must not make selection depend on input order",
);

// --- 5. The handoff, and the mislabel it used to carry. ----------------------

const select = readFileSync(new URL("./select-best-shots.ts", import.meta.url), "utf8");
assert(
  select.includes("semanticEmbedding: rankedTake.winner.photo.semantic?.embedding"),
  "the selector must actually receive TinyCLIP's embedding, or none of the above runs in the app",
);
// `embeddingSpace` used to be tagged from whether the photo HAD a semantic
// signal, while `embedding` holds the perceptual one whenever it exists. Two
// photos could therefore both claim "tinyclip" while carrying a 76-dim and a
// 512-dim vector; `cosine` returns 0 on a length mismatch, so their redundancy
// penalty silently became "completely different" and never fired.
assert(
  /embeddingSpace: rankedTake\.winner\.perceptualEmbedding\?\.length/.test(select),
  "the embedding space tag must describe the vector actually carried, not a different signal",
);

const planner = readFileSync(new URL("./album-planner.ts", import.meta.url), "utf8");
assert(
  planner.includes("closestSemantic[mediaId]"),
  "the semantic penalty must be applied in gain(), not merely computed",
);
// The hard duplicate gate stays on the perceptual signal it was calibrated
// against. TinyCLIP ranks; it must never be able to throw a photograph away.
const gate = planner.match(/maxSelectedSimilarity[\s\S]{0,120}/g)?.join("\n") ?? "";
assert(
  !gate.includes("closestSemantic"),
  "the semantic score must never feed the hard exclusion gate",
);

console.log("semantic diversity self-check passed");
