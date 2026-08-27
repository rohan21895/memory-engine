// @ts-expect-error The Expo app deliberately does not ship Node declarations.
import { fileURLToPath } from "node:url";
// @ts-expect-error The Expo app deliberately does not ship Node declarations.
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
// @ts-expect-error The Expo app deliberately does not ship Node declarations.
import { tmpdir } from "node:os";
// @ts-expect-error The Expo app deliberately does not ship Node declarations.
import { join } from "node:path";

// @ts-expect-error Node's native TypeScript runner requires source extensions.
import { runDegradationMonotonicityGate } from "./degradation-monotonicity.ts";
// @ts-expect-error Node's native TypeScript runner requires source extensions.
import { runEyesOpenOrderingGate } from "./eyes-open-ordering.ts";
// @ts-expect-error Node's native TypeScript runner requires source extensions.
import { loadEvaluationIndex, runFrozenPairDriftGate } from "./frozen-pair-drift.ts";

function assert(condition: unknown, message: string): asserts condition {
  if (!condition) throw new Error(`CX-25 gate self-check failed: ${message}`);
}

function fixture(name: string): string {
  return fileURLToPath(new URL(`../../fixtures/cx25/${name}`, import.meta.url));
}

const degradation = await runDegradationMonotonicityGate(
  fixture("quality-fixtures.json"),
);
assert(degradation.status === "PASS", degradation.summary);
assert(
  degradation.vacuityGuard.passed &&
    degradation.measurements.fixtures >= 3 &&
    degradation.measurements.comparisons >= 25,
  "degradation gate must exercise several real-image regions and comparisons",
);
assert(
  degradation.measurements.violationRate <= 0.02,
  "degradation violation rate must stay within the standing bar",
);
assert(
  JSON.stringify(
    await runDegradationMonotonicityGate(fixture("quality-fixtures.json")),
  ) === JSON.stringify(degradation),
  "degradation report must be byte-deterministic",
);

const eyes = runEyesOpenOrderingGate(fixture("eyes-open-fixtures.json"));
assert(
  eyes.status === "BLOCKED" && !eyes.vacuityGuard.passed,
  "an empty ML Kit fixture set must be BLOCKED, never green",
);
assert(
  eyes.measurements.eligibleComparisons === 0 && eyes.blocker !== undefined,
  "the current fixture blocker must be explicit and measured",
);

const current = fixture("synthetic-face-index.json");
const previous = fixture("synthetic-face-index.previous.json");
const drift = runFrozenPairDriftGate(current, previous);
assert(drift.status === "PASS", drift.summary);
assert(
  drift.vacuityGuard.passed && drift.measurements.current.negatives >= 1_000,
  "frozen-pair metrics must resolve FAR 0.1% with at least 1,000 negatives",
);
assert(
  Object.values(drift.measurements.categories).every((count) => count >= 20),
  "every requested pair category must be populated",
);
assert(
  drift.measurements.totalCrossings === 0,
  "the frozen synthetic previous run must cross no current bar",
);
assert(
  JSON.stringify(runFrozenPairDriftGate(current, previous)) ===
    JSON.stringify(drift),
  "frozen-pair report must be byte-deterministic",
);

// The production export is split: metadata JSON plus quantized JSONL vectors.
// Exercise that adapter separately from the convenient synthetic generator.
const exported = mkdtempSync(join(tmpdir(), "photeo-cx25-face-index-"));
try {
  writeFileSync(
    join(exported, "face-index.json"),
    JSON.stringify({
      version: 22,
      threshold: 0.44,
      people: [
        { id: "person-a", assetIds: ["a", "group"] },
        { id: "person-b", assetIds: ["b", "group"] },
      ],
    }),
  );
  const embedding = globalThis.btoa(String.fromCharCode(127, 0));
  writeFileSync(
    join(exported, "face-observations.jsonl"),
    [
      { assetId: "a", embedding, embeddingKind: "identity", capturedAt: 1 },
      { assetId: "b", embedding, embeddingKind: "identity", capturedAt: 2 },
      { assetId: "group", embedding, embeddingKind: "identity", capturedAt: 3 },
    ]
      .map((value) => JSON.stringify(value))
      .join("\n"),
  );
  const loaded = loadEvaluationIndex(exported);
  assert(
    loaded.observations.length === 3 &&
      loaded.observations[0].personId === "person-a" &&
      loaded.observations[1].personId === "person-b" &&
      loaded.observations[2].personId === undefined,
    "split device export must dequantize observations and keep shared-photo labels free",
  );
} finally {
  rmSync(exported, { recursive: true, force: true });
}

console.log("CX-25 standing gate self-checks passed");
