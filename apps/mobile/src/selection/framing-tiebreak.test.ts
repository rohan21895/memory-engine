// @ts-expect-error The Expo app deliberately does not ship Node type declarations.
import { readFileSync } from "node:fs";

import type { PickedPhoto } from "../import/picked-photo";

// @ts-expect-error Node's native TypeScript runner requires the extension.
import { bodyCoverage } from "./pose-framing.ts";
import type { BodyCoverage } from "./pose-framing";
// @ts-expect-error Node's native TypeScript runner requires the extension.
import { KP, letterboxLayout } from "./pose-framing-test-deps.ts";
import type { FaceSignal, QualitySignals } from "./quality-signals";
// @ts-expect-error Node's native TypeScript runner requires the extension.
import { selectBestShots } from "./select-best-shots.ts";

function assert(value: unknown, message: string): asserts value {
  if (!value) throw new Error(`Framing tie-break self-check failed: ${message}`);
}

/**
 * Body framing is wired into selection as a TIE-BREAK and nothing else.
 *
 * Everything below is one question: when two frames of the same take are scored
 * exactly alike by every measured signal, does the frame holding more of the
 * person win — and does the signal stay completely silent everywhere else?
 *
 * The control runs are the point. Each assertion is paired with the SAME
 * fixture minus the framing, which must choose differently; without that pair a
 * passing assertion would only be showing that the first photo in the array won,
 * which is what the code did before this existed.
 */

const SOURCE = { width: 4_000, height: 3_000 };

// --- Coverage fixtures, built from real keypoints ---------------------------
// Hand-written BodyCoverage literals can express combinations the extractor
// cannot produce (a `cutAtJoint` that is not `cutByFrame`, a depth that
// disagrees with its framing). Going through `bodyCoverage` keeps every fixture
// something MoveNet could actually return.

function toModelSpace(x: number, y: number): readonly [number, number] {
  const { drawWidth, drawHeight } = letterboxLayout(SOURCE.width, SOURCE.height);
  const size = Math.max(drawWidth, drawHeight);
  const spanX = drawWidth / size;
  const spanY = drawHeight / size;
  return [x * spanX + (1 - spanX) / 2, y * spanY + (1 - spanY) / 2] as const;
}

function coverageOf(
  visible: Partial<Record<keyof typeof KP, readonly [number, number]>>,
): BodyCoverage {
  const keypoints: Array<readonly [number, number]> = [];
  const scores: number[] = [];
  for (const name of Object.keys(KP) as Array<keyof typeof KP>) {
    const at = visible[name];
    keypoints[KP[name]] = toModelSpace(at?.[0] ?? 0.5, at?.[1] ?? 0.5);
    scores[KP[name]] = at ? 0.9 : 0.05;
  }
  return bodyCoverage(keypoints, scores, SOURCE.width, SOURCE.height);
}

const HEAD = { nose: [0.5, 0.1], l_eye: [0.46, 0.08], r_eye: [0.54, 0.08] } as const;
const SHOULDERS = { l_sho: [0.4, 0.25], r_sho: [0.6, 0.25] } as const;
const HIPS = { l_hip: [0.42, 0.5], r_hip: [0.58, 0.5] } as const;
const KNEES = { l_kne: [0.43, 0.7], r_kne: [0.57, 0.7] } as const;
const ANKLES = { l_ank: [0.44, 0.9], r_ank: [0.56, 0.9] } as const;

const WHOLE_BODY = coverageOf({ ...HEAD, ...SHOULDERS, ...HIPS, ...KNEES, ...ANKLES });
const CUT_AT_KNEES = coverageOf({
  ...HEAD,
  ...SHOULDERS,
  ...HIPS,
  l_kne: [0.43, 0.97],
  r_kne: [0.57, 0.97],
});
const UNREADABLE = bodyCoverage([], [], SOURCE.width, SOURCE.height);

assert(WHOLE_BODY.framing === "full" && !WHOLE_BODY.cutByFrame, "fixture: a whole body in frame");
assert(CUT_AT_KNEES.cutAtJoint, "fixture: a body the frame severs at the knees");
assert(UNREADABLE.framing === "unknown", "fixture: a pose the model could not read");

// --- The tie-break decides a take the measured signals could not ------------

const CUT = { id: "cut-at-knees", coverage: CUT_AT_KNEES };
const WHOLE = { id: "whole-body", coverage: WHOLE_BODY };
const UNREAD = { id: "unreadable-pose", coverage: UNREADABLE };
const NO_POSE = { id: "no-pose-at-all", coverage: undefined };

assert(
  winner([CUT, WHOLE]) === "whole-body",
  "the frame holding the whole person must win a take nothing else could separate",
);
assert(
  winner([WHOLE, CUT]) === "whole-body",
  "and must win from the other input order too, or a tie-break is a shuffle",
);

// VACUITY GUARD. Same two photos, same everything, framing removed: the winner
// is now whichever was picked first, in BOTH directions. So the two frames are
// genuinely tied on every measured signal, the arbitrary input order is what
// decided them before, and the two assertions above are the tie-break doing
// work rather than the array order agreeing with it by luck.
assert(
  winner([blind(CUT), blind(WHOLE)]) === "cut-at-knees" &&
    winner([blind(WHOLE), blind(CUT)]) === "whole-body",
  "vacuity guard: without framing this fixture is settled purely by input order",
);

// --- `unknown` is inert: never rewarded, never penalised --------------------
// Most photos in a family library have no clean single subject. A frame the
// model could not read must land exactly where it landed before — which is the
// control's answer, in both directions.

for (const unreadable of [UNREAD, NO_POSE]) {
  assert(
    winner([unreadable, WHOLE]) === unreadable.id,
    `${unreadable.id}: an unreadable pose must not lose its slot to a better-framed frame`,
  );
  assert(
    winner([WHOLE, unreadable]) === "whole-body",
    `${unreadable.id}: nor take one, so the outcome matches the no-framing control`,
  );
}

// --- MoveNet fits ONE person, so it may not speak for a group ---------------
// Same coverages, same order, three detected faces instead of one: the fit
// describes whichever body the model locked onto and says nothing about the
// other two, so the tie-break must fall silent and the input order stands.

assert(
  winner([CUT, WHOLE], groupSignals()) === "cut-at-knees",
  "a single-person pose must not decide a group photo",
);
assert(
  winner([CUT, WHOLE], portraitSignals()) === "whole-body",
  "vacuity guard: the same coverages DO decide the same take when one person is in it",
);

// --- It changes the order inside a take, never what is eligible -------------
// The winner's own quality is forwarded to the planner and the album's quality
// floor is derived from those winners, so the number must be untouched.

const framed = album([CUT, WHOLE]);
const control = album([blind(CUT), blind(WHOLE)]);
assert(
  framed.selected.length === control.selected.length &&
    framed.pool.length === control.pool.length,
  "the tie-break must not change how many photos survive",
);
assert(
  mediaIds(framed).join() === mediaIds(control).join(),
  "nor which photos are present at all — only which of them is the take's winner",
);
assert(
  framed.selected[0].media_id !== control.selected[0].media_id,
  "vacuity guard: the winner really did move between these two runs",
);
assert(
  framed.pool[0].quality === control.pool[0].quality,
  "and the quality score must be identical, because framing is never summed into it",
);

// --- The signal has to actually reach selection, and reach nothing else -----
// Everything above runs on fixtures that set `bodyCoverage` by hand. Nothing
// fills that field on a real photo unless `build-album.ts` does, and a pass that
// is correct but never executes is the failure mode this repo has already hit.
// That module cannot be imported here — it would boot Expo native modules — so
// its integration source is read, the way `build-album.test.ts` does.

const here = new URL(".", import.meta.url).pathname;
const integration = readFileSync(`${here}../build-album.ts`, "utf8") as string;
assert(
  /coverage: detectedPose\s*\?\s*bodyCoverage\(\s*detectedPose\.keypoints,\s*detectedPose\.scores,\s*analysisWidth,\s*analysisHeight,/u.test(
    integration,
  ),
  "build-album must read coverage from the SAME keypoints as the pose signature, " +
    "and with the dimensions detectBodyPose letterboxed with — the original " +
    "photo's would invert every in-frame test on a non-square photo",
);
assert(
  /bodyCoverage: coverage,/u.test(integration),
  "and forward it on the photo handed to selectBestShots",
);

// One call site. The comparator is for near-identical frames of one moment: in
// `compareRankedTakes` it would rank two different moments by a single-person
// pose, and inside `enhancedQualityScore` it would become a framing gate, which
// CX-19 measured as costing 2.7%-13.8% of real selections every time.
const selection = readFileSync(`${here}select-best-shots.ts`, "utf8") as string;
assert(
  (selection.match(/compareFramingCompleteness\(/gu) ?? []).length === 1,
  "framing must be consulted in exactly one place in select-best-shots.ts",
);

console.log("framing tie-break self-check passed");

// --- Fixtures ---------------------------------------------------------------

type Frame = { id: string; coverage: BodyCoverage | undefined };
type TestPhoto = PickedPhoto & {
  embedding?: number[];
  analysis?: QualitySignals;
  bodyCoverage?: BodyCoverage;
};

/** The same frame with the framing signal taken away. */
function blind(frame: Frame): Frame {
  return { id: frame.id, coverage: undefined };
}

function album(frames: Frame[], analysis: QualitySignals = portraitSignals()) {
  return selectBestShots(
    frames.map((frame) => photo(frame, analysis)),
    { count: 2 },
  );
}

function winner(frames: Frame[], analysis?: QualitySignals): string {
  return album(frames, analysis).selected[0].media_id;
}

function mediaIds(result: ReturnType<typeof album>): string[] {
  return [
    ...result.selected.map(({ media_id }) => media_id),
    ...result.pool.map(({ media_id }) => media_id),
  ].sort();
}

/**
 * One identical near-duplicate frame per fixture.
 *
 * Every photo shares one embedding, so `sameTake` collapses them, and one
 * `analysis` OBJECT, so their quality scores are equal to the bit rather than
 * merely close — which is the only condition under which the tie-break fires.
 */
function photo(frame: Frame, analysis: QualitySignals): TestPhoto {
  return {
    id: frame.id,
    uri: `file:///photos/${frame.id}.jpg`,
    filename: `${frame.id}.jpg`,
    width: SOURCE.width,
    height: SOURCE.height,
    mimeType: "image/jpeg",
    source: "device-gallery",
    embedding: Array.from({ length: 64 }, (_, index) => (index === 0 ? 1 : 0)),
    analysis,
    bodyCoverage: frame.coverage,
  };
}

function face(): FaceSignal {
  return { areaRatio: 0.08, eyesOpen: 0.8, smile: 0.5, cutAtEdge: false };
}

function portraitSignals(): QualitySignals {
  return signals([face()], "portrait");
}

function groupSignals(): QualitySignals {
  return signals([face(), face(), face()], "group");
}

function signals(faces: FaceSignal[], category: QualitySignals["category"]): QualitySignals {
  return {
    sharpness: 0.7,
    exposure: 0.5,
    clippedFraction: 0,
    faces,
    faceCount: faces.length,
    largestFaceAreaRatio: 0.08,
    anyFaceCutAtEdge: false,
    isScreenshotOrDocument: false,
    category,
  };
}
