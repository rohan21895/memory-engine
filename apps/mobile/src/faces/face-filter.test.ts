// @ts-expect-error TypeScript bundler resolution normally omits source extensions.
import { combinePersonAssetIds } from "./face-filter.ts";

function assert(value: unknown, message: string): asserts value {
  if (!value) throw new Error(`face-filter self-check failed: ${message}`);
}

const photos: Record<string, string[]> = {
  a: ["one", "shared"],
  b: ["two", "shared"],
  c: ["shared"],
};
const lookup = (personId: string) => photos[personId] ?? [];

assert(combinePersonAssetIds([], "any", lookup) === null, "Anyone has no restricting set");
assert(
  [...(combinePersonAssetIds(["a", "b"], "any", lookup) ?? [])].sort().join(",") === "one,shared,two",
  "Any is the union of selected people",
);
assert(
  [...(combinePersonAssetIds(["a", "b", "c"], "all", lookup) ?? [])].join(",") === "shared",
  "All is the intersection of selected people",
);

// eslint-disable-next-line no-console
console.log("face-filter self-check passed");
