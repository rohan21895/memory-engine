// @ts-expect-error Node requires the extension; Metro resolves this path too.
import { albumSubtitle } from "./album-store.ts";
import type { SavedAlbum } from "./album-store";

function assert(value: unknown, message: string): asserts value {
  if (!value) throw new Error(`album subtitle self-check failed: ${message}`);
}

/**
 * The line under an album's name must not contradict the name.
 *
 * Seen on the device: "July memories" with "39 photos · Jan 2026" directly
 * beneath it. Neither half was wrong. `suggestedAlbumTitle` names the month
 * that contributed the MOST photos -- pinned in album-title-capture-date --
 * and this line named the month the album reaches back to. A reader gets two
 * different answers to "when was this?" on two adjacent lines and concludes
 * the app is broken.
 *
 * A span cannot disagree with any month inside it, so that is what it shows.
 */

const at = (year: number, month: number): number => new Date(year, month, 15).getTime();
const album = (start?: number, end?: number, count = 39): SavedAlbum =>
  ({
    id: "a",
    title: "t",
    coverUri: "u",
    createdAt: at(2026, 7),
    photos: Array.from({ length: count }, (_, index) => ({
      media_id: String(index),
      uri: "u",
    })),
    dateRange: { start, end },
  }) as unknown as SavedAlbum;

const cases: Array<[SavedAlbum, string, string]> = [
  [album(at(2026, 0), at(2026, 6)), "39 photos · Jan – Jul 2026", "a span says both months, and the year once"],
  [album(at(2026, 0), at(2026, 0)), "39 photos · Jan 2026", "one month stays one month"],
  [album(at(2025, 10), at(2026, 1)), "39 photos · Nov 2025 – Feb 2026", "a span across new year keeps both years"],
  [album(at(2026, 0), undefined), "39 photos · Jan 2026", "a missing end falls back to the start"],
  [album(undefined, undefined), "39 photos · Aug 2026", "no capture dates falls back to when it was made"],
  [album(at(2026, 0), at(2026, 0), 1), "1 photo · Jan 2026", "one photo is not 1 photos"],
];
for (const [subject, expected, why] of cases) {
  const actual = albumSubtitle(subject);
  assert(actual === expected, `${why}: expected "${expected}", got "${actual}"`);
}

// VACUITY: the rule that shipped -- start month only -- must fail the first
// case, or none of the above is testing the change.
assert(
  albumSubtitle(album(at(2026, 0), at(2026, 6))) !== "39 photos · Jan 2026",
  "VACUITY: showing only the start month must no longer be what happens",
);

// An end BEFORE the start is corrupt data, not a range to render backwards.
assert(
  albumSubtitle(album(at(2026, 6), at(2026, 0))) === "39 photos · Jul 2026",
  "an end earlier than the start must not render as a reversed span",
);
// Same for values that are not dates at all.
assert(
  albumSubtitle(album(Number.NaN, Number.NaN)) === "39 photos · Aug 2026",
  "an unparseable range must fall back rather than print Invalid Date",
);

console.log("album subtitle self-check passed");
