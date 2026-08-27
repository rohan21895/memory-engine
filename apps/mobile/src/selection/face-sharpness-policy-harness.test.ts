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
  appSource.includes("buildAlbum(next, 24,") &&
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
  "the harness must stay anchored to the real 24-pick, capped, face-region caller path",
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
// failures, and the tie-only branch must replace at least one selected ID.
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
  soft?.newlyRejected === 0 && soft.currentlySelectedLost > 0,
  "soft policy must reject nothing while exercising real tie replacements",
);
assert(
  all.newlyRejected === 833 && all.currentlySelectedLost === 317 &&
    half.newlyRejected === 170 && half.currentlySelectedLost === 63 &&
    quarter.newlyRejected === 408 && quarter.currentlySelectedLost === 146 &&
    soft.currentlySelectedLost === 312,
  "committed measurement table must match the executed seeded corpus",
);

// Determinism guard: a second run must be byte-identical. Without this, a
// plausible table can drift between the committed note and the executed CLI.
assert(
  JSON.stringify(runFaceSharpnessPolicyHarness()) === JSON.stringify(report),
  "seeded report must be reproducible",
);

console.log("face-sharpness policy harness self-checks passed");
