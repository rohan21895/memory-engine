// @ts-expect-error The Expo app deliberately does not ship Node declarations.
import { readFileSync } from "node:fs";

// @ts-expect-error Node requires the extension; Metro resolves this path too.
import { sourceFiles } from "./jsx-scan.ts";

function assert(value: unknown, message: string): asserts value {
  if (!value) throw new Error(`disabled contrast self-check failed: ${message}`);
}

/**
 * A button that is off must still be readable.
 *
 * On the device, "Build my album" and "Next · 0 photos" were pale ghosts you
 * could only read if you already knew the words. The cause is `opacity` on the
 * whole control: it dims the fill AND the label by the same amount, so on a
 * light page the two converge. White on gold at 0.38 over the cream background
 * measures about 1.2:1.
 *
 * The same trick is FINE on a dark surface -- the PDF viewer dims to 0.3 and
 * white-on-near-black still measures about 6:1 -- so this is not a ban on
 * `opacity`. It is a ban on light-surface controls disappearing.
 *
 * WCAG exempts inactive controls from its contrast minimum. That exemption is
 * about conformance, not about a person being able to read the screen, and the
 * owner asked for an app his grandmother can use.
 */

// --- The relative-luminance arithmetic, so the claim above is checked. -------

function channel(value: number): number {
  const srgb = value / 255;
  return srgb <= 0.04045 ? srgb / 12.92 : ((srgb + 0.055) / 1.055) ** 2.4;
}
function luminance(hex: string): number {
  const value = hex.replace("#", "");
  const [r, g, b] = [0, 2, 4].map((at) => parseInt(value.slice(at, at + 2), 16));
  return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b);
}
function contrast(a: string, b: string): number {
  const [high, low] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (high + 0.05) / (low + 0.05);
}

// Sanity: the formula must reproduce two ratios everybody knows.
assert(Math.abs(contrast("#ffffff", "#000000") - 21) < 0.01, "white on black is 21:1");
assert(Math.abs(contrast("#777777", "#ffffff") - 4.48) < 0.05, "mid grey on white is ~4.48:1");

const tokens = readFileSync(new URL("./tokens.ts", import.meta.url), "utf8");
const token = (name: string): string => {
  const found = tokens.match(new RegExp(`${name}: "(#[0-9a-fA-F]{6})"`))?.[1];
  assert(found, `${name} must be a hex token`);
  return found;
};

const ratio = contrast(token("disabledText"), token("disabledSurface"));
assert(
  ratio >= 4.5,
  `an off button's label must clear 4.5:1 against its own fill (got ${ratio.toFixed(2)}:1)`,
);
// ...and must not be so strong it reads as available. The gold CTA is 4.6:1;
// an off button that beats it by a lot is shouting.
assert(
  ratio <= 9,
  `...without out-shouting the live button (got ${ratio.toFixed(2)}:1)`,
);

// VACUITY: what shipped must fail this bar, or the token pair proves nothing.
{
  // White on gold, both composited 0.38 over the page background.
  const over = (fg: string, bg: string, alpha: number): number =>
    alpha * luminance(fg) + (1 - alpha) * luminance(bg);
  const page = token("background");
  const label = over("#ffffff", page, 0.38);
  const fill = over(token("gold"), page, 0.38);
  const [high, low] = [label, fill].sort((x, y) => y - x);
  const shipped = (high + 0.05) / (low + 0.05);
  assert(
    shipped < 2,
    `VACUITY: the 0.38 opacity that shipped must measure as unreadable (got ${shipped.toFixed(2)}:1)`,
  );
}

// --- No light-surface control may go back to dimming itself. ----------------
//
// Named files rather than a blanket ban: the PDF viewer is a dark screen and
// dimming is correct there. This list is the light ones.
const LIGHT_SURFACE = [
  "ui/components/PrimaryButton.tsx",
  "ui/components/SecondaryButton.tsx",
  "albums/AlbumActionScreens.tsx",
];
const root = new URL("..", import.meta.url).pathname;
for (const relative of LIGHT_SURFACE) {
  const source = readFileSync(root + relative, "utf8");
  assert(
    !/disabled: \{ opacity:/.test(source),
    `${relative} must not dim a light-surface control back into illegibility`,
  );
  assert(
    source.includes("colors.disabledSurface") && source.includes("colors.disabledText"),
    `${relative} must use the measured off-state pair`,
  );
}

// The scan must be pointed at files that exist -- a typo'd path would make the
// loop above vacuous rather than failing.
{
  const known = new Set(sourceFiles(root).map((file: string) => file.slice(root.length)));
  for (const relative of LIGHT_SURFACE) {
    assert(known.has(relative), `${relative} must be a real source file`);
  }
}

console.log(`disabled contrast self-check passed (off state ${ratio.toFixed(2)}:1)`);
