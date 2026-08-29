// @ts-expect-error The Expo app deliberately does not ship Node declarations.
import { readFileSync } from "node:fs";

// @ts-expect-error Node requires the extension; Metro resolves this path too.
import { gridItemWidth } from "./grid-width.ts";

function assert(value: unknown, message: string): asserts value {
  if (!value) throw new Error(`grid width self-check failed: ${message}`);
}

/**
 * A row of N items plus its gaps must fit the row.
 *
 * Found on the device, not in a review: every album on the shelf sat alone on
 * its line with half the screen empty. The cards were `width: "47.8%"` in a row
 * with `gap: 14`, which reads as obviously fine and is off by six hundredths of
 * a dp on this particular phone -- 1440px at density 640 is 360dp, less 22dp of
 * screen padding each side leaves 316dp, and 2 x 151.048 + 14 = 316.096.
 *
 * It would have fitted on any wider handset. That is the whole lesson: a
 * percentage that has to be re-derived by hand every time the gap or the
 * padding changes will eventually be wrong on somebody's screen, and the
 * failure is silent -- the layout still renders, it just quietly halves.
 */

// The measured case, first. 360dp screen, 22dp padding each side, 2 columns.
const OWNER_CONTAINER = 360 - 22 * 2;
{
  const width = gridItemWidth(OWNER_CONTAINER, 2, 14);
  assert(
    width * 2 + 14 <= OWNER_CONTAINER,
    `two cards plus the gap must fit 316dp (got ${width * 2 + 14})`,
  );
  // VACUITY: the value that shipped must actually fail this bound, or the
  // assertion above is passing on a case that was never broken.
  const shipped = OWNER_CONTAINER * 0.478;
  assert(
    shipped * 2 + 14 > OWNER_CONTAINER,
    `VACUITY: 47.8% twice plus the gap must overflow (got ${(shipped * 2 + 14).toFixed(3)})`,
  );
}

// Every plausible phone, every column count, every gap in the token scale.
for (const screen of [320, 360, 375, 390, 411, 428, 480, 600, 768, 1024]) {
  for (const padding of [0, 4, 8, 12, 16, 22, 24]) {
    for (const columns of [2, 3, 4]) {
      for (const gap of [0, 4, 8, 10, 12, 14, 16, 24]) {
        const container = screen - padding * 2;
        const width = gridItemWidth(container, columns, gap);
        const used = width * columns + gap * (columns - 1);
        assert(
          used <= container,
          `${columns} items of ${width} plus gaps must fit ${container} (got ${used})`,
        );
        // And it must not waste a whole column's worth either, or "fits" is
        // being bought by rendering everything tiny.
        assert(
          container - used < columns + gap,
          `${columns}x${width} wastes ${container - used} of ${container}`,
        );
        assert(Number.isInteger(width), `width must be whole dp (got ${width})`);
      }
    }
  }
}

// Degenerate inputs: a layout pass can run before the window is measured.
assert(gridItemWidth(0, 2, 14) === 0, "a zero container yields zero, not NaN");
assert(gridItemWidth(-10, 2, 14) === 0, "a negative container yields zero");
assert(gridItemWidth(Number.NaN, 2, 14) === 0, "an unmeasured container yields zero");
assert(gridItemWidth(316, 1, 14) === 316, "one column takes the whole row, no gap");
assert(gridItemWidth(316, 0, 14) === 316, "zero columns is treated as one, not a divide by zero");
assert(gridItemWidth(100, 2, 500) === 0, "a gap wider than the row yields zero, never negative");

// The shelf must actually use it. A helper nothing calls fixes nothing -- and
// this is the file that shipped a hand-tuned percentage for months.
{
  const screen = readFileSync(
    new URL("./screens/AlbumsScreen.tsx", import.meta.url),
    "utf8",
  );
  assert(
    screen.includes("gridItemWidth(") && screen.includes("{ width: cardWidth }"),
    "the albums shelf must size its cards with gridItemWidth",
  );
  assert(
    !/albumCard: \{[^}]*width: "\d/.test(screen),
    "...and must not still carry a hand-tuned percentage width",
  );
}

console.log("grid width self-check passed");
