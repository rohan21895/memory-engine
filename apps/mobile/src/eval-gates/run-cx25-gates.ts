// @ts-expect-error The Expo app deliberately does not ship Node declarations.
import { fileURLToPath } from "node:url";

// @ts-expect-error Node's native TypeScript runner requires source extensions.
import { runDegradationMonotonicityGate } from "./degradation-monotonicity.ts";
// @ts-expect-error Node's native TypeScript runner requires source extensions.
import { runEyesOpenOrderingGate } from "./eyes-open-ordering.ts";
// @ts-expect-error Node's native TypeScript runner requires source extensions.
import { runFrozenPairDriftGate } from "./frozen-pair-drift.ts";
import type { GateResult } from "./gate-report";

type CliOptions = {
  faceIndex: string;
  previousFaceIndex: string;
  qualityFixtures: string;
  eyesFixtures: string;
  json: boolean;
};

type AnyGate = GateResult<object>;

export async function runCx25Gates(options: CliOptions): Promise<AnyGate[]> {
  const degradation = await runDegradationMonotonicityGate(
    options.qualityFixtures,
  );
  const eyes = runEyesOpenOrderingGate(options.eyesFixtures);
  const drift = runFrozenPairDriftGate(
    options.faceIndex,
    options.previousFaceIndex,
  );
  return [degradation, eyes, drift];
}

function fixturePath(name: string): string {
  return fileURLToPath(new URL(`../../fixtures/cx25/${name}`, import.meta.url));
}

function parseArgs(args: readonly string[]): CliOptions {
  const options: CliOptions = {
    faceIndex: fixturePath("synthetic-face-index.json"),
    previousFaceIndex: fixturePath("synthetic-face-index.previous.json"),
    qualityFixtures: fixturePath("quality-fixtures.json"),
    eyesFixtures: fixturePath("eyes-open-fixtures.json"),
    json: false,
  };
  let customFaceIndex = false;
  let customPreviousFaceIndex = false;
  for (let index = 0; index < args.length; index += 1) {
    const argument = args[index];
    if (argument === "--json") {
      options.json = true;
      continue;
    }
    const value = args[index + 1];
    if (!value) throw new Error(`${argument} requires a path.`);
    if (argument === "--face-index") {
      options.faceIndex = value;
      customFaceIndex = true;
    } else if (argument === "--previous-face-index") {
      options.previousFaceIndex = value;
      customPreviousFaceIndex = true;
    }
    else if (argument === "--quality-fixtures") options.qualityFixtures = value;
    else if (argument === "--eyes-fixtures") options.eyesFixtures = value;
    else throw new Error(`Unknown option: ${argument}`);
    index += 1;
  }
  if (customFaceIndex !== customPreviousFaceIndex) {
    throw new Error(
      "Real drift evaluation requires both --face-index and --previous-face-index.",
    );
  }
  return options;
}

function printReport(gates: readonly AnyGate[]): void {
  console.log("CX-25 standing gate report");
  console.log("==========================");
  for (const gate of gates) {
    console.log(`\n[${gate.status}] ${gate.gate}`);
    console.log(gate.summary);
    console.log(
      `Vacuity guard: ${gate.vacuityGuard.passed ? "PASS" : "FAIL"} — ${gate.vacuityGuard.detail}`,
    );
    if (gate.blocker) console.log(`Blocker: ${gate.blocker}`);
    if (gate.gate.includes("frozen-pair")) {
      const measurements = gate.measurements as {
        categories: Record<string, number>;
        current: Record<string, number>;
        previous: Record<string, number>;
        crossings: Record<
          string,
          { bar: number; crossedUp: number; crossedDown: number; total: number }
        >;
      };
      console.log(`Categories: ${JSON.stringify(measurements.categories)}`);
      console.log(`Current metrics: ${JSON.stringify(measurements.current)}`);
      console.log(`Previous metrics: ${JSON.stringify(measurements.previous)}`);
      for (const [name, crossing] of Object.entries(measurements.crossings)) {
        console.log(
          `Crossings ${name}@${crossing.bar.toFixed(3)}: ` +
            `${crossing.total} (${crossing.crossedUp} up, ${crossing.crossedDown} down)`,
        );
      }
    }
    for (const violation of gate.violations.slice(0, 10)) {
      console.log(`Violation: ${violation}`);
    }
    if (gate.violations.length > 10) {
      console.log(`... ${gate.violations.length - 10} more violations`);
    }
  }
  const failed = gates.filter((gate) => gate.status === "FAIL").length;
  const blocked = gates.filter((gate) => gate.status === "BLOCKED").length;
  const passed = gates.length - failed - blocked;
  console.log(
    `\nOVERALL ${failed === 0 && blocked === 0 ? "PASS" : "FAIL"} — ` +
      `${passed} passed, ${failed} failed, ${blocked} blocked`,
  );
}

const runtimeProcess = globalThis as typeof globalThis & {
  process?: { argv: string[]; exitCode?: number };
};

if (runtimeProcess.process) {
  try {
    const options = parseArgs(runtimeProcess.process.argv.slice(2));
    const gates = await runCx25Gates(options);
    if (options.json) console.log(JSON.stringify(gates, null, 2));
    else printReport(gates);
    if (gates.some((gate) => gate.status !== "PASS")) {
      runtimeProcess.process.exitCode = 1;
    }
  } catch (error) {
    console.error(
      `CX-25 gate runner failed: ${error instanceof Error ? error.message : String(error)}`,
    );
    runtimeProcess.process.exitCode = 1;
  }
}
