import { describe, expect, it } from "vitest";
import { friendlyFolderName, monthLabel, scanPercent } from "./format";

describe("friendlyFolderName", () => {
  it("handles Windows and POSIX paths", () => {
    expect(friendlyFolderName("/Users/rohan/Pictures/")).toBe("Pictures");
    expect(friendlyFolderName("D:\\Family Photos\\")).toBe("Family Photos");
  });
});

describe("monthLabel", () => {
  it("does not invent a date", () => {
    expect(monthLabel(null)).toBe("Date unknown");
    expect(monthLabel("not-a-date")).toBe("Date unknown");
  });
});

describe("scanPercent", () => {
  it("handles discovery and clamps completed work", () => {
    expect(scanPercent(5, null)).toBeNull();
    expect(scanPercent(50, 200)).toBe(25);
    expect(scanPercent(250, 200)).toBe(100);
  });
});
