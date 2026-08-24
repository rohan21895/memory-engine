// @ts-expect-error TypeScript bundler resolution normally omits source extensions.
import { incrementalScanTarget } from "./incremental-index.ts";

function assert(value: unknown, message: string): asserts value {
  if (!value) throw new Error(`incremental-index self-check failed: ${message}`);
}

const indexed = new Set(["newest", "older"]);
assert(
  incrementalScanTarget(2, 2, ["newest", "older"], (id) => indexed.has(id)) === 0,
  "a hydrated unchanged library skips scanning",
);
assert(
  incrementalScanTarget(4, 2, ["added", "newest"], (id) => indexed.has(id)) === 2,
  "a larger library scans only the missing count",
);
assert(
  incrementalScanTarget(2, 2, ["replacement", "newest"], (id) => indexed.has(id)) === 1,
  "an unseen newest asset is found even when total count is unchanged",
);

// eslint-disable-next-line no-console
console.log("incremental-index self-check passed");
