// @ts-expect-error The Expo app deliberately does not ship Node declarations.
import { dirname, resolve } from "node:path";

// @ts-expect-error Node's native TypeScript runner requires source extensions.
import { measureImageQuality } from "../selection/image-quality.ts";
// @ts-expect-error Node's native TypeScript runner requires source extensions.
import { qualityScoreForSignals } from "../selection/select-best-shots.ts";
import type { MeasuredImageQuality, NormalizedBox } from "../selection/image-quality";
import type { QualitySignals } from "../selection/quality-signals";
// @ts-expect-error Node's native TypeScript runner requires source extensions.
import { round, type GateResult } from "./gate-report.ts";
// @ts-expect-error Node's native TypeScript runner requires source extensions.
import { croppedFaceBox, decodeFixtureSource, qualityFixtureLoader, readQualityFixtureManifest } from "./quality-fixture-loader.ts";
import type {
  FixtureDegradation,
  QualityFixture,
} from "./quality-fixture-loader";

const MAX_VIOLATION_RATE = 0.02;
const MIN_FIXTURES = 3;
const MIN_COMPARISONS = 25;
const MIN_CHAIN_DROP = 0.005;

type ScorePoint = {
  level: string;
  score: number;
  sharpness: number;
  exposure: number;
  clippedFraction: number;
};

type FixtureMeasurement = {
  fixture: string;
  blur: ScorePoint[];
  underexposure: ScorePoint[];
  overexposure: ScorePoint[];
  faceCrop: ScorePoint[];
};

export type DegradationMeasurements = {
  fixtures: number;
  comparisons: number;
  violations: number;
  violationRate: number;
  allowedViolationRate: number;
  chains: FixtureMeasurement[];
};

export async function runDegradationMonotonicityGate(
  manifestPath: string,
): Promise<GateResult<DegradationMeasurements>> {
  const manifest = readQualityFixtureManifest(manifestPath);
  const sourcePath = resolve(dirname(manifestPath), manifest.source);
  const source = decodeFixtureSource(sourcePath);
  const violations: string[] = [];
  const chains: FixtureMeasurement[] = [];
  let comparisons = 0;

  for (const fixture of manifest.fixtures) {
    const baseline = await scoreVariant(source, fixture, { kind: "none" });
    const blur = [
      baseline,
      await scoreVariant(source, fixture, { kind: "blur", sigma: 1 }),
      await scoreVariant(source, fixture, { kind: "blur", sigma: 2 }),
      await scoreVariant(source, fixture, { kind: "blur", sigma: 4 }),
    ];
    const underexposure = [
      baseline,
      await scoreVariant(source, fixture, { kind: "exposure", ev: -0.5 }),
      await scoreVariant(source, fixture, { kind: "exposure", ev: -1 }),
      await scoreVariant(source, fixture, { kind: "exposure", ev: -1.5 }),
    ];
    const overexposure = [
      baseline,
      await scoreVariant(source, fixture, { kind: "exposure", ev: 0.5 }),
      await scoreVariant(source, fixture, { kind: "exposure", ev: 1 }),
      await scoreVariant(source, fixture, { kind: "exposure", ev: 1.5 }),
    ];
    const faceCrop = [
      baseline,
      await scoreVariant(source, fixture, { kind: "face-crop" }),
    ];
    const measurement = {
      fixture: fixture.id,
      blur,
      underexposure,
      overexposure,
      faceCrop,
    };
    chains.push(measurement);

    for (const [name, chain] of Object.entries({
      blur,
      underexposure,
      overexposure,
      faceCrop,
    })) {
      for (let index = 1; index < chain.length; index += 1) {
        comparisons += 1;
        if (!(chain[index].score < chain[index - 1].score - 1e-9)) {
          violations.push(
            `${fixture.id}/${name} ${chain[index - 1].level}->${chain[index].level}: ` +
              `${chain[index - 1].score.toFixed(6)}->${chain[index].score.toFixed(6)}`,
          );
        }
      }
    }
  }

  const chainDrops = chains.flatMap((fixture) => [
    totalDrop(fixture.blur),
    totalDrop(fixture.underexposure),
    totalDrop(fixture.overexposure),
    totalDrop(fixture.faceCrop),
  ]);
  const completeMeasurements = chains.every((fixture) =>
    [
      ...fixture.blur,
      ...fixture.underexposure,
      ...fixture.overexposure,
      ...fixture.faceCrop,
    ].every(
      (point) =>
        Number.isFinite(point.score) &&
        Number.isFinite(point.sharpness) &&
        Number.isFinite(point.exposure) &&
        Number.isFinite(point.clippedFraction),
    ),
  );
  const vacuityPassed =
    chains.length >= MIN_FIXTURES &&
    comparisons >= MIN_COMPARISONS &&
    completeMeasurements &&
    chainDrops.every((drop) => drop >= MIN_CHAIN_DROP);
  const violationRate = comparisons > 0 ? violations.length / comparisons : 1;
  const passed = vacuityPassed && violationRate <= MAX_VIOLATION_RATE;

  return {
    gate: "GATE 1 — degradation monotonicity",
    status: passed ? "PASS" : "FAIL",
    summary:
      `${violations.length}/${comparisons} ordering violations ` +
      `(${(violationRate * 100).toFixed(2)}%, limit 2.00%).`,
    measurements: {
      fixtures: chains.length,
      comparisons,
      violations: violations.length,
      violationRate: round(violationRate),
      allowedViolationRate: MAX_VIOLATION_RATE,
      chains,
    },
    vacuityGuard: {
      passed: vacuityPassed,
      detail:
        `${chains.length} real-image regions, ${comparisons} adjacent comparisons; ` +
        `all signals present=${completeMeasurements}; every chain total drop ` +
        `>=${MIN_CHAIN_DROP}=${chainDrops.every((drop) => drop >= MIN_CHAIN_DROP)}.`,
    },
    violations,
  };
}

async function scoreVariant(
  source: ReturnType<typeof decodeFixtureSource>,
  fixture: QualityFixture,
  degradation: FixtureDegradation,
): Promise<ScorePoint> {
  const subjectBox =
    degradation.kind === "face-crop"
      ? croppedFaceBox(fixture.face)
      : fixture.face;
  const measured = await measureImageQuality(
    `fixture://${fixture.id}/${levelFor(degradation)}`,
    {
      subjectBox,
      imageLoader: qualityFixtureLoader(source, fixture, degradation),
    },
  );
  requireSignals(fixture.id, degradation, measured);
  const analysis = qualitySignals(
    measured,
    subjectBox,
    degradation.kind === "face-crop",
  );
  return {
    level: levelFor(degradation),
    score: round(
      qualityScoreForSignals({ analysis, width: 4000, height: 3000 }),
    ),
    sharpness: round(measured.sharpness),
    exposure: round(measured.exposure),
    clippedFraction: round(measured.clippedFraction),
  };
}

function qualitySignals(
  measured: MeasuredImageQuality,
  face: NormalizedBox,
  cutAtEdge: boolean,
): QualitySignals {
  const areaRatio = Math.min(1, face.width * face.height);
  return {
    sharpness: measured.sharpness,
    faceSharpness: measured.faceSharpness,
    subjectSharpness: measured.subjectSharpness,
    subjectBackgroundRatio: measured.subjectBackgroundRatio,
    exposure: measured.exposure,
    clippedFraction: measured.clippedFraction,
    faces: [{ areaRatio, eyesOpen: 0.9, smile: 0.5, cutAtEdge }],
    faceCount: 1,
    largestFaceAreaRatio: areaRatio,
    anyFaceCutAtEdge: cutAtEdge,
    isScreenshotOrDocument: false,
    category: "portrait",
  };
}

function requireSignals(
  fixtureId: string,
  degradation: FixtureDegradation,
  measured: MeasuredImageQuality,
): asserts measured is Required<
  Pick<MeasuredImageQuality, "sharpness" | "exposure" | "clippedFraction">
> &
  MeasuredImageQuality {
  if (
    measured.sharpness === undefined ||
    measured.exposure === undefined ||
    measured.clippedFraction === undefined
  ) {
    throw new Error(
      `${fixtureId}/${levelFor(degradation)} did not produce all required quality signals.`,
    );
  }
}

function levelFor(degradation: FixtureDegradation): string {
  if (degradation.kind === "none") return "original";
  if (degradation.kind === "blur") return `sigma-${degradation.sigma}`;
  if (degradation.kind === "exposure") return `${degradation.ev}EV`;
  return "crop-through-face";
}

function totalDrop(chain: readonly ScorePoint[]): number {
  return chain.length > 1 ? chain[0].score - chain[chain.length - 1].score : 0;
}
