/**
 * The exact width of one item in a fixed-column grid.
 *
 * Percentages and gaps do not mix. The albums shelf asked for two cards at
 * `width: "47.8%"` inside a row with `gap: 14`, which on the owner's phone --
 * 1440px at density 640, so 360dp wide, 316dp inside the screen padding --
 * comes to 2 x 151.048 + 14 = 316.096dp. Six hundredths of a dp over the line,
 * so the row wrapped and every album sat alone on its row with half the screen
 * empty. It would have fitted on a wider handset, which is exactly why nobody
 * caught it.
 *
 * Arithmetic instead of a percentage that has to be re-tuned per gap: the gap
 * is subtracted first, so what is left is divisible by definition and cannot
 * round its way over the edge.
 *
 * Returns a whole number of dp, because a fractional width is rounded to device
 * pixels at layout time and two items rounding up is the same overflow again.
 */
export function gridItemWidth(
  containerWidth: number,
  columns: number,
  gap: number,
): number {
  if (!Number.isFinite(containerWidth) || containerWidth <= 0) return 0;
  const usableColumns = Math.max(1, Math.floor(columns));
  const gaps = Math.max(0, gap) * (usableColumns - 1);
  return Math.max(0, Math.floor((containerWidth - gaps) / usableColumns));
}
