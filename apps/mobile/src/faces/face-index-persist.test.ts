// @ts-expect-error TypeScript bundler resolution normally omits source extensions.
import { shouldPersistIndex } from "./face-index.ts";

function assert(value: unknown, message: string): asserts value {
  if (!value) throw new Error(`face-index-persist self-check failed: ${message}`);
}

// Persisting is JSON.stringify over the whole index — measured at 3161ms for a
// 3MB index, on the JS thread that paints the photo grid. Skipping it when
// nothing changed is what stops an app open that scans nothing from stalling.
// Skipping one that WAS needed silently loses the user's scan, so every case
// here is about which way the decision fails.

const SHAPE = "100:12:80:90:12:0.4:avg-linkage-aligned-1:false:null:90";

// The only skip: the flag and the fingerprint agree that disk is already right.
assert(
  !shouldPersistIndex(false, SHAPE, SHAPE),
  "a clean index whose shape matches disk skips the write",
);

// A mutation that announced itself always writes, whatever the shape says.
assert(
  shouldPersistIndex(true, SHAPE, SHAPE),
  "a dirty index writes even when the shape is unchanged",
);

// The backstop. If a future mutation site forgets markIndexDirty, a moved
// fingerprint must still force the write — this is the case that would
// otherwise lose data silently.
assert(
  shouldPersistIndex(false, "101:12:80:90:12:0.4:avg-linkage-aligned-1:false:null:91", SHAPE),
  "a changed shape writes even when nobody marked the index dirty",
);

// An empty written-shape is the startup state: nothing has been written yet, so
// the first persist must never be skipped.
assert(
  shouldPersistIndex(false, SHAPE, ""),
  "the first write of a session is never skipped",
);

// Both signals pointing at a change is still a write, not a double-negative.
assert(
  shouldPersistIndex(true, SHAPE, "0:0:0:0:0:0.4:x:false:null:0"),
  "dirty and moved shape writes",
);

// In-place edits that preserve every count are exactly why the flag exists and
// the fingerprint alone is not enough: same shape, different content.
assert(
  shouldPersistIndex(true, SHAPE, SHAPE),
  "an in-place edit invisible to the counts still writes",
);

// eslint-disable-next-line no-console
console.log("face-index-persist self-check passed");
