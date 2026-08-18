import { describe, expect, it } from "vitest";
import {
  captureLabel,
  durationLabel,
  friendlyFolderName,
  measuredDurationLabel,
  monthLabel,
  scanPercent,
} from "./format";

describe("friendlyFolderName", () => {
  it("handles Windows and POSIX paths", () => {
    expect(friendlyFolderName("/Users/rohan/Pictures/")).toBe("Pictures");
    expect(friendlyFolderName("D:\\Family Photos\\")).toBe("Family Photos");
  });
});

describe("monthLabel", () => {
  it("does not invent a date", () => {
    expect(monthLabel(null)).toBe("Date unknown");
    expect(monthLabel(Number.NaN)).toBe("Date unknown");
    expect(monthLabel(1_700_000_000, "unknown")).toBe("Date unknown");
  });
});

describe("captureLabel", () => {
  it("never fabricates time below the asserted precision", () => {
    const day = captureLabel(1_700_000_000, "day");
    const year = captureLabel(1_700_000_000, "year");
    expect(day).not.toMatch(/\d:\d/);
    expect(year).toMatch(/^\d{4}$/);
  });
});

describe("durationLabel", () => {
  it("formats video durations without fake precision", () => {
    expect(durationLabel(65_100)).toBe("1:05");
    expect(durationLabel(null)).toBeNull();
  });
});

describe("measuredDurationLabel", () => {
  it("labels only the duration supplied without pretending unknown clips are included", () => {
    expect(measuredDurationLabel(3_723_400)).toBe("1 hr 2 min 3 sec");
    expect(measuredDurationLabel(125_000)).toBe("2 min 5 sec");
    expect(measuredDurationLabel(350)).toBe("Less than 1 sec");
    expect(measuredDurationLabel(Number.NaN)).toBe("Not measured");
  });
});

describe("scanPercent", () => {
  it("handles discovery and clamps completed work", () => {
    expect(scanPercent(5, null)).toBeNull();
    expect(scanPercent(50, 200)).toBe(25);
    expect(scanPercent(250, 200)).toBe(100);
  });
});
