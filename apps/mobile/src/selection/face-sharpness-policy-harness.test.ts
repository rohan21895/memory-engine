// @ts-expect-error The Expo app deliberately does not ship Node declarations.
import { readFileSync } from "node:fs";

// @ts-expect-error Node's native TypeScript runner requires the extension.
import { ALBUM_SELECTION_COUNT, ANALYZED_CANDIDATES_PER_LIBRARY, runFaceSharpnessPolicyHarness, SYNTHETIC_LIBRARY_COUNT, syntheticCorpus } from "./face-sharpness-policy-harness.ts";

function assert(value: unknown, message: string): asserts value {
  if (!value) throw new Error(`Face-sharpness harness self-check failed: ${message}`);
}

const buildAlbumSource = readFileSync(
  new URL("../build-album.ts", import.meta.url),
  "utf8",
);
const appSource = readFileSync(new URL("../../App.tsx", import.meta.url), "utf8");
const selectionSource = readFileSync(
  new URL("./select-best-shots.ts", import.meta.url),
  "utf8",
);

// Real-caller guard: a fixture that only exercises a made-up uncapped bridge is
// precisely the silent failure this measurement is meant to avoid.
assert(
  // The album size used to be hard-coded 24 here. It is now whatever the owner
  // asked for on the setup screen, so the anchor follows the symbol that
  // carries it rather than the number it used to be.
  appSource.includes("buildAlbum(next, preferences.maxPhotos,") &&
    // The cap the real caller applies. It used to be the bare
    // HEAVY_ANALYSIS_CANDIDATE_LIMIT constant; it is now the measured budget
    // policy, which returns exactly that constant at today's per-candidate
    // price. The anchor follows the symbol so it keeps naming a real caller.
    buildAlbumSource.includes("candidateBudget(count)") &&
    buildAlbumSource.includes("const boxesPromise = deepAnalysisTiming") &&
    buildAlbumSource.includes("detectFaces(analysisUri, {") &&
    buildAlbumSource.includes("detectedBoxes,") &&
    selectionSource.includes(
      "headSharpness: rankedTake.winner.analysis?.subjectSharpness",
    ),
  "the harness must stay anchored to the real picked-count, capped, face-region caller path",
);

const corpus = syntheticCorpus();
const expectedPhotos =
  SYNTHETIC_LIBRARY_COUNT * ANALYZED_CANDIDATES_PER_LIBRARY;
assert(
  corpus.length === expectedPhotos && corpus.length >= 5_000,
  `corpus must remain multi-thousand and exact (got ${corpus.length})`,
);
assert(
  corpus.filter((photo) => photo.faces.length >= 3).length > corpus.length / 2,
  "group photos must remain the majority of the owner-shaped corpus",
);

const report = runFaceSharpnessPolicyHarness();
assert(
  report.currentSelectedPhotos ===
    SYNTHETIC_LIBRARY_COUNT * ALBUM_SELECTION_COUNT,
  `every synthetic run must produce a full current album (got ${report.currentSelectedPhotos})`,
);
const byPolicy = Object.fromEntries(
  report.policies.map((measurement) => [measurement.policy, measurement]),
);
const all = byPolicy["all-faces-hard"];
const half = byPolicy["area-50-hard"];
const quarter = byPolicy["area-25-hard"];
const soft = byPolicy["all-faces-soft-tie"];

// Vacuity guards: every hard-policy branch must see actual secondary-face
// failures. The production scorer must also produce independently measured
// per-frame quality; manufacturing one rounded score per take was the old bug.
for (const measurement of [all, half, quarter]) {
  assert(measurement !== undefined, "all hard policies must be reported");
  assert(
    measurement.newlyRejected > 0 && measurement.currentlySelectedLost > 0,
    `${measurement.policy} must exercise both requested counters`,
  );
}
assert(
  all.newlyRejected >= quarter.newlyRejected &&
    quarter.newlyRejected >= half.newlyRejected,
  "area filtering must monotonically reduce hard rejections",
);
assert(
  report.distinctQualityScores > corpus.length / 2 && report.exactQualityTieTakes === 0,
  `production scoring must vary by frame instead of manufacturing ties (${report.distinctQualityScores} scores, ${report.exactQualityTieTakes} tied takes)`,
);
assert(
  soft?.newlyRejected === 0 && soft.currentlySelectedLost === 0,
  "the soft tie-only policy is correctly reported inert when production scoring produces no ties",
);
assert(
  all.newlyRejected === 870 && all.currentlySelectedLost === 163 &&
    half.newlyRejected === 151 && half.currentlySelectedLost === 23 &&
    quarter.newlyRejected === 436 && quarter.currentlySelectedLost === 68 &&
    soft.currentlySelectedLost === 0,
  "committed measurement table must match the executed seeded corpus",
);

// Actual implementation sabotage, not a second expected table: if the scorer
// is broken to return one constant, the harness must expose that every
// two-frame take became an exact tie and every quality collapsed to one value.
const sabotaged = runFaceSharpnessPolicyHarness(undefined, () => 0);
assert(
  sabotaged.distinctQualityScores === 1 &&
    sabotaged.exactQualityTieTakes > 1_000,
  "constant-scorer sabotage must be observable in the corpus and tie gate",
);

// Determinism guard: a second run must be byte-identical. Without this, a
// plausible table can drift between the committed note and the executed CLI.
assert(
  JSON.stringify(runFaceSharpnessPolicyHarness()) === JSON.stringify(report),
  "seeded report must be reproducible",
);

console.log("face-sharpness policy harness self-checks passed");
