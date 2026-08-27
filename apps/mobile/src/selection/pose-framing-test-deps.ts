/**
 * Re-exports for `pose-framing.test.ts`.
 *
 * The test builds fixtures in photo coordinates and applies the letterbox
 * itself, so it needs the same `letterboxLayout` the implementation uses —
 * importing it from here rather than duplicating the maths, which would let a
 * fixture and the code under test drift apart and agree on the wrong answer.
 */
// @ts-expect-error Node's TypeScript runner requires the source extension.
export { letterboxLayout } from "../ml/movenet.ts";
// @ts-expect-error Node's TypeScript runner requires the source extension.
export { KP } from "./pose.ts";
