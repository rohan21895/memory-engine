// @ts-expect-error The Expo app deliberately does not ship Node declarations.
import { readFileSync } from "node:fs";

// @ts-expect-error Node's native TypeScript runner requires source extensions.
import { classifyCategory } from "../selection/quality-signals.ts";
import type { FaceSignal, QualitySignals } from "../selection/quality-signals";
// @ts-expect-error Node's native TypeScript runner requires source extensions.
import { selectBestShots } from "../selection/select-best-shots.ts";
// @ts-expect-error Node's native TypeScript runner requires source extensions.
import { round, type GateResult } from "./gate-report.ts";

const OPEN_MINIMUM = 0.8;
const CLOSED_MAXIMUM = 0.2;
const REQUIRED_ORDERING_RATE = 0.95;
const MIN_ELIGIBLE_COMPARISONS = 20;

type MlKitFace = {
  areaRatio: number;
  leftEyeOpen?: number;
  rightEyeOpen?: number;
  smile?: number;
  cutAtEdge: boolean;
};

type EyeFrame = {
  id: string;
  width: number;
  height: number;
  faces: MlKitFace[];
  quality: {
    sharpness?: number;
    faceSharpness?: number;
    subjectSharpness?: number;
    subjectBackgroundRatio?: number;
    exposure?: number;
    clippedFraction?: number;
  };
};

type EyeGroup = {
  id: string;
  provenance: "ml-kit-device-export";
  frames: EyeFrame[];
};

type EyeFixtureFile = {
  format: "photeo-mlkit-eyes-open-fixtures-v1";
  groups: EyeGroup[];
  blocker?: string;
};

export type EyesOpenMeasurements = {
  groups: number;
  eligibleGroups: number;
  eligibleComparisons: number;
  openRankedHigher: number;
  orderingRate: number;
  requiredOrderingRate: number;
  unknownFramesExcluded: number;
};

export function runEyesOpenOrderingGate(
  fixturePath: string,
): GateResult<EyesOpenMeasurements> {
  const fixtures = readEyeFixtures(fixturePath);
  let eligibleGroups = 0;
  let eligibleComparisons = 0;
  let openRankedHigher = 0;
  let unknownFramesExcluded = 0;
  const violations: string[] = [];

  for (const group of fixtures.groups) {
    const classified = group.frames.map((frame) => ({
      frame,
      eyesOpen: frameEyesOpen(frame),
    }));
    unknownFramesExcluded += classified.filter(
      ({ eyesOpen }) => eyesOpen === undefined,
    ).length;
    const open = classified.filter(
      ({ eyesOpen }) => eyesOpen !== undefined && eyesOpen >= OPEN_MINIMUM,
    );
    const closed = classified.filter(
      ({ eyesOpen }) => eyesOpen !== undefined && eyesOpen <= CLOSED_MAXIMUM,
    );
    if (open.length === 0 || closed.length === 0) continue;
    eligibleGroups += 1;

    for (const openFrame of open) {
      for (const closedFrame of closed) {
        eligibleComparisons += 1;
        const winner = rankPair(group.id, openFrame.frame, closedFrame.frame);
        if (winner === openFrame.frame.id) {
          openRankedHigher += 1;
        } else {
          violations.push(
            `${group.id}: ${closedFrame.frame.id} ranked above open-eyed ${openFrame.frame.id}`,
          );
        }
      }
    }
  }

  const orderingRate =
    eligibleComparisons > 0 ? openRankedHigher / eligibleComparisons : 0;
  const enoughEvidence = eligibleComparisons >= MIN_ELIGIBLE_COMPARISONS;
  if (eligibleComparisons === 0) {
    const blocker =
      fixtures.blocker ??
      "No real ML Kit fixture group contains both >=0.8 and <=0.2 eye-open probabilities.";
    return {
      gate: "GATE 2 — eyes-open ordering",
      status: "BLOCKED",
      summary: "0 eligible comparisons; the scorer was not evaluated.",
      measurements: {
        groups: fixtures.groups.length,
        eligibleGroups,
        eligibleComparisons,
        openRankedHigher,
        orderingRate: 0,
        requiredOrderingRate: REQUIRED_ORDERING_RATE,
        unknownFramesExcluded,
      },
      vacuityGuard: {
        passed: false,
        detail:
          `Requires at least ${MIN_ELIGIBLE_COMPARISONS} real ML Kit open/closed comparisons; found 0.`,
      },
      violations: [],
      blocker,
    };
  }

  const passed = enoughEvidence && orderingRate >= REQUIRED_ORDERING_RATE;
  return {
    gate: "GATE 2 — eyes-open ordering",
    status: passed ? "PASS" : "FAIL",
    summary:
      `${openRankedHigher}/${eligibleComparisons} open-eyed frames ranked higher ` +
      `(${(orderingRate * 100).toFixed(2)}%, minimum 95.00%).`,
    measurements: {
      groups: fixtures.groups.length,
      eligibleGroups,
      eligibleComparisons,
      openRankedHigher,
      orderingRate: round(orderingRate),
      requiredOrderingRate: REQUIRED_ORDERING_RATE,
      unknownFramesExcluded,
    },
    vacuityGuard: {
      passed: enoughEvidence,
      detail:
        `Requires at least ${MIN_ELIGIBLE_COMPARISONS} comparisons with device-exported ` +
        `ML Kit values; found ${eligibleComparisons}. UNKNOWN frames excluded=${unknownFramesExcluded}.`,
    },
    violations,
  };
}

function rankPair(groupId: string, open: EyeFrame, closed: EyeFrame): string {
  const embedding = Array.from({ length: 76 }, (_, index) =>
    index % 2 === 0 ? 0.1 : -0.1,
  );
  const startedAt = Date.UTC(2026, 0, 1);
  const result = selectBestShots(
    [open, closed].map((frame, index) => ({
      id: frame.id,
      uri: `fixture://${groupId}/${frame.id}`,
      filename: `${frame.id}.jpg`,
      width: frame.width,
      height: frame.height,
      mimeType: "image/jpeg" as const,
      source: "device-gallery" as const,
      creationTime: startedAt + index,
      embedding,
      perceptualEmbedding: embedding,
      analysis: analysisFor(frame),
    })),
    { count: 1 },
  );
  return result.selected[0]?.media_id ?? "";
}

function analysisFor(frame: EyeFrame): QualitySignals {
  const faces: FaceSignal[] = frame.faces.map((face) => ({
    areaRatio: face.areaRatio,
    eyesOpen: knownMinimum([face.leftEyeOpen, face.rightEyeOpen]),
    smile: face.smile,
    cutAtEdge: face.cutAtEdge,
  }));
  const largestFaceAreaRatio = faces.reduce(
    (largest, face) => Math.max(largest, face.areaRatio),
    0,
  );
  return {
    ...frame.quality,
    faces,
    faceCount: faces.length,
    largestFaceAreaRatio,
    anyFaceCutAtEdge: faces.some((face) => face.cutAtEdge),
    isScreenshotOrDocument: false,
    category: classifyCategory(faces.length, largestFaceAreaRatio),
  };
}

function frameEyesOpen(frame: EyeFrame): number | undefined {
  const significant = frame.faces.filter((face) => face.areaRatio >= 0.005);
  return knownMinimum(
    significant.flatMap((face) => [face.leftEyeOpen, face.rightEyeOpen]),
  );
}

function knownMinimum(values: Array<number | undefined>): number | undefined {
  const known = values.filter(
    (value): value is number =>
      typeof value === "number" && Number.isFinite(value),
  );
  return known.length > 0 ? Math.min(...known) : undefined;
}

function readEyeFixtures(path: string): EyeFixtureFile {
  const value: unknown = JSON.parse(readFileSync(path, "utf8"));
  if (
    !isRecord(value) ||
    value.format !== "photeo-mlkit-eyes-open-fixtures-v1" ||
    !Array.isArray(value.groups) ||
    !value.groups.every(validGroup) ||
    (value.blocker !== undefined && typeof value.blocker !== "string")
  ) {
    throw new Error(`Invalid ML Kit eyes-open fixture file: ${path}`);
  }
  return value as EyeFixtureFile;
}

function validGroup(value: unknown): value is EyeGroup {
  return (
    isRecord(value) &&
    typeof value.id === "string" &&
    value.provenance === "ml-kit-device-export" &&
    Array.isArray(value.frames) &&
    value.frames.every(validFrame)
  );
}

function validFrame(value: unknown): value is EyeFrame {
  return (
    isRecord(value) &&
    typeof value.id === "string" &&
    finitePositive(value.width) &&
    finitePositive(value.height) &&
    Array.isArray(value.faces) &&
    value.faces.every(validFace) &&
    validQuality(value.quality)
  );
}

function validQuality(value: unknown): boolean {
  if (!isRecord(value)) return false;
  const required = [value.sharpness, value.exposure, value.clippedFraction];
  const optional = [
    value.faceSharpness,
    value.subjectSharpness,
    value.subjectBackgroundRatio,
  ];
  return (
    required.every(
      (signal) => typeof signal === "number" && Number.isFinite(signal),
    ) &&
    optional.every(
      (signal) =>
        signal === undefined ||
        (typeof signal === "number" && Number.isFinite(signal)),
    )
  );
}

function validFace(value: unknown): value is MlKitFace {
  return (
    isRecord(value) &&
    finitePositive(value.areaRatio) &&
    typeof value.cutAtEdge === "boolean" &&
    optionalProbability(value.leftEyeOpen) &&
    optionalProbability(value.rightEyeOpen) &&
    optionalProbability(value.smile)
  );
}

function optionalProbability(value: unknown): boolean {
  return (
    value === undefined ||
    (typeof value === "number" && Number.isFinite(value) && value >= 0 && value <= 1)
  );
}

function finitePositive(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value) && value > 0;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
