// @ts-expect-error The Expo app deliberately does not ship Node declarations.
import { readdirSync, readFileSync } from "node:fs";
// @ts-expect-error The Expo app deliberately does not ship Node declarations.
import { join } from "node:path";

function assert(value: unknown, message: string): asserts value {
  if (!value) throw new Error(`type floor self-check failed: ${message}`);
}

/**
 * Nothing in the app may be smaller than the design system's own smallest size.
 *
 * `typeScale.eyebrow` is 12px and is the floor by construction: it is the
 * smallest thing the tokens file is willing to name. Seven places had drifted
 * underneath it with hand-written sizes -- 9px for a checkbox tick, 10px for
 * the PDF page numbers, 10.5px for the slideshow's "swipe to switch" hint,
 * 11px for the "Main focus / Include / Background only" buttons on the album
 * setup screen.
 *
 * That last one is the reason this is a gate and not a tidy-up. The owner asked
 * for an app his grandmother can use, and 11px semibold on a three-across row
 * is a control she has to guess at. Sizes below the token scale are always
 * somebody solving a layout problem by shrinking the words, and the layout is
 * the thing that should give.
 *
 * The floor is on the size only. Contrast, hit targets and copy are separate
 * problems that a number in a stylesheet cannot answer.
 */

const ROOT = new URL("..", import.meta.url).pathname;
const FLOOR = 12;

function sourceFiles(directory: string): string[] {
  const found: string[] = [];
  for (const entry of readdirSync(directory, { withFileTypes: true })) {
    const path = join(directory, entry.name);
    if (entry.isDirectory()) found.push(...sourceFiles(path));
    else if (
      (entry.name.endsWith(".tsx") || entry.name.endsWith(".ts")) &&
      !entry.name.includes(".test.")
    ) {
      found.push(path);
    }
  }
  return found;
}

const tokens = readFileSync(join(ROOT, "ui/tokens.ts"), "utf8");
const eyebrow = Number(tokens.match(/eyebrow: \{ fontSize: ([0-9.]+)/)?.[1]);
assert(
  eyebrow === FLOOR,
  `the floor must track the smallest named size; eyebrow is ${eyebrow}, this test assumes ${FLOOR}`,
);

const tooSmall: string[] = [];
let sizes = 0;
for (const file of sourceFiles(ROOT)) {
  const source = readFileSync(file, "utf8");
  const lines = source.split("\n");
  for (let index = 0; index < lines.length; index += 1) {
    for (const match of lines[index].matchAll(/fontSize: ([0-9.]+)/g)) {
      sizes += 1;
      const size = Number(match[1]);
      if (size < FLOOR) {
        tooSmall.push(`${file.slice(ROOT.length)}:${index + 1} — ${size}px`);
      }
    }
  }
}

// VACUITY: a scan that matches nothing passes, and so does one pointed at an
// empty tree. The app has dozens of hand-written sizes; if this count collapses
// the assertion below has stopped meaning anything.
assert(sizes > 30, `the scan must find the app's type sizes (found ${sizes})`);

assert(
  tooSmall.length === 0,
  `no text may be smaller than the ${FLOOR}px floor; ${tooSmall.length} of ${sizes} are:\n  ` +
    tooSmall.join("\n  "),
);

console.log(`type floor self-check passed (${sizes} sizes, floor ${FLOOR}px)`);
