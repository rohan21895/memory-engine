export type GateStatus = "PASS" | "FAIL" | "BLOCKED";

export type GateResult<TMeasurements extends object> = {
  gate: string;
  status: GateStatus;
  summary: string;
  measurements: TMeasurements;
  vacuityGuard: {
    passed: boolean;
    detail: string;
  };
  violations: string[];
  blocker?: string;
};

export function round(value: number, digits = 6): number {
  const scale = 10 ** digits;
  return Math.round(value * scale) / scale;
}
