// @ts-expect-error TypeScript bundler resolution normally omits source extensions.
import { plainAlternativeReason, plainChosenReason } from "./reasons.ts";

function assert(value: unknown, message: string): asserts value {
  if (!value) throw new Error(`reasons self-check failed: ${message}`);
}

// The selection engine emits measured sentences, so exact-match alone made every
// alternative collapse onto one generic line. Each of these is a real string
// shape from select-best-shots.ts and must resolve to its own plain reason.
const blur = plainAlternativeReason([
  "Rejected: blurrier than the chosen frame (52% vs 71% sharpness).",
  "Near-duplicate of the chosen frame (cosine similarity 0.97).",
]);
const blink = plainAlternativeReason([
  "Rejected: subject blinking (34% eye-open, below the 45% gate).",
]);
const faceCut = plainAlternativeReason(["Rejected: face cut at frame edge."]);
const generic = plainAlternativeReason([
  "Near-duplicate of the chosen frame (cosine similarity 0.97).",
]);

assert(blur !== generic, "a blurrier alternative reads differently from a plain near-duplicate");
assert(blink !== generic, "a blinking alternative reads differently from a plain near-duplicate");
assert(faceCut !== generic, "a cut face reads differently from a plain near-duplicate");
assert(
  new Set([blur, blink, faceCut]).size === 3,
  "blur, blink and cut-face alternatives each get their own sentence",
);

// Drifted wording on both sides of the same idea must land on the same reason.
assert(
  plainAlternativeReason(["Rejected: face cut at frame edge."]) ===
    plainAlternativeReason(["A face cut at the frame edge lowered this frame's quality."]),
  "both cut-face phrasings resolve to one reason",
);

// Chosen captions come from a composed summary sentence; it must still resolve.
assert(
  plainChosenReason([
    "All known significant faces have open eyes; sharpest of 3 near-duplicates.",
    "Pixel sharpness: 71%.",
  ]) === plainChosenReason(["Everyone's eyes are open and easy to see."]),
  "a composed eyes-open summary reads as the eyes-open reason",
);

// Unknown text still produces something a person can read, never an empty caption.
assert(plainChosenReason([]).length > 0, "no reasons still yields a caption");
assert(plainAlternativeReason(["totally unknown engine string"]).length > 0, "unknown text still yields a caption");

// eslint-disable-next-line no-console
console.log("reasons self-check passed");
