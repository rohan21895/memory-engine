// @ts-expect-error The Expo app deliberately does not ship Node declarations.
import { readFileSync } from "node:fs";

// @ts-expect-error Node requires the extension; Metro resolves this path too.
import { openingTags, sourceFiles, styleHeights } from "./jsx-scan.ts";

function assert(value: unknown, message: string): asserts value {
  if (!value) throw new Error(`touch target self-check failed: ${message}`);
}

/**
 * A button must be big enough to hit, or say how it gets there.
 *
 * `layout.minTouchTarget` is 48 and has been in the tokens the whole time; six
 * controls had drifted under it, and every one of them is a control somebody
 * has to find rather than a decoration:
 *
 *   34pt  the close button on the person filter
 *   36pt  Classic / Cinema in the slideshow
 *   40pt  back and Edit on the album screen, and the date segments and chips
 *         on the filter screen
 *
 * A smaller shape is allowed -- a 40pt circle over a photograph looks right and
 * a 48pt one does not -- but then it has to carry `hitSlop`, which grows the
 * touch area without touching the layout. What is not allowed is a small
 * control with neither.
 *
 * `hitSlop` is accepted here as a declaration rather than measured, because the
 * amount that is correct depends on what sits next to the control: horizontal
 * slop between two shoulder-to-shoulder segments would overlap, and in the seam
 * the later-rendered one silently wins every tap. That judgement is at each
 * call site, in a comment, next to the neighbours it is about.
 *
 * TWO BARS, and the difference is deliberate.
 *
 * The hard floor is 44 -- WCAG 2.1 AA, which is a standard rather than a
 * preference. Nothing may sit under it.
 *
 * The app's own token is 48, Android's own guidance, and 24 controls sit in the
 * band between. Almost all of them are an explicit `height: 44`, so they were
 * chosen rather than forgotten, and moving 24 controls across the gallery, the
 * review grid and the PDF viewer is a visual change I will not make without
 * looking at the screens first. So the band is a RATCHET: the count may fall
 * when someone has seen the screens, and may never rise.
 */

const ROOT = new URL("..", import.meta.url).pathname;

/** WCAG 2.1 AA. Not negotiable, and not the same thing as the app's own token. */
const FLOOR = 44;
/** Known controls between the floor and the token. Lower it; never raise it. */
const BAND_BUDGET = 24;

const tokens = readFileSync(`${ROOT}ui/tokens.ts`, "utf8");
const MINIMUM = Number(tokens.match(/minTouchTarget: ([0-9.]+)/)?.[1]);
assert(
  Number.isFinite(MINIMUM) && MINIMUM >= FLOOR,
  `minTouchTarget must be discoverable and at least ${FLOOR} (got ${MINIMUM})`,
);

/** A style height that resolves through the token reads as the token. */
function resolveHeights(source: string): Map<string, number> {
  const heights = styleHeights(source);
  for (const entry of source.matchAll(
    /^ {2}(\w+): \{[^}]*(?:min)?[Hh]eight: layout\.minTouchTarget/gm,
  )) {
    heights.set(entry[1], MINIMUM);
  }
  return heights;
}

const belowFloor: string[] = [];
const inBand: string[] = [];
let measured = 0;
for (const file of sourceFiles(ROOT)) {
  const source = readFileSync(file, "utf8");
  const heights = resolveHeights(source);
  for (const { line, tag } of openingTags(source)) {
    const named = [...tag.matchAll(/styles\.(\w+)/g)]
      .map((match) => [match[1], heights.get(match[1])] as const)
      .filter((pair): pair is readonly [string, number] => pair[1] !== undefined);
    if (named.length === 0) continue;
    measured += 1;
    if (tag.includes("hitSlop")) continue;
    const smallest = Math.min(...named.map(([, height]) => height));
    const where = `${file.slice(ROOT.length)}:${line} — ${smallest}pt`;
    if (smallest < FLOOR) belowFloor.push(where);
    else if (smallest < MINIMUM) inBand.push(where);
  }
}

// VACUITY. Only the Pressables whose height is readable from the source can be
// judged, so if that set collapses to nothing the assertions below are empty. A
// count is the only honest way to say how much of the app this covers.
assert(
  measured >= 60,
  `the scan must resolve a real number of button heights (resolved ${measured})`,
);

assert(
  belowFloor.length === 0,
  `a control under ${FLOOR}pt must carry hitSlop; ${belowFloor.length} of ${measured} do not:\n  ` +
    belowFloor.join("\n  "),
);

assert(
  inBand.length <= BAND_BUDGET,
  `the ${FLOOR}-${MINIMUM}pt band may shrink but never grow: ${inBand.length} controls, ` +
    `budget ${BAND_BUDGET}. Lower BAND_BUDGET when you fix one; do not raise it.\n  ` +
    inBand.join("\n  "),
);
// And the budget itself must stay honest -- a budget far above the real count
// is a ratchet with the teeth filed off.
assert(
  inBand.length >= BAND_BUDGET - 2,
  `BAND_BUDGET is ${BAND_BUDGET} but only ${inBand.length} controls are in the band; lower it`,
);

console.log(
  `touch target self-check passed (${measured} measurable buttons, ` +
    `floor ${FLOOR}pt, ${inBand.length}/${BAND_BUDGET} in the ${FLOOR}-${MINIMUM}pt band)`,
);
