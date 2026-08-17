import { describe, expect, it } from "vitest";

import { gridMetrics, maximumMountedTiles } from "./virtual-grid";

describe("the 100k-item library grid", () => {
  it("keeps mounted tile work bounded by the viewport, not the library size", () => {
    const metrics = gridMetrics(1_280, 100_000);
    expect(metrics.rowCount * metrics.columns).toBeGreaterThanOrEqual(100_000);
    expect(maximumMountedTiles(760, metrics)).toBeLessThan(100);
  });

  it("adapts down to one readable column", () => {
    expect(gridMetrics(220, 25)).toMatchObject({ columns: 1, rowCount: 25 });
  });
});
