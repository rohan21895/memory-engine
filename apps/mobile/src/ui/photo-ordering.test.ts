/**
 * Photos file under the month they were TAKEN, not the month they were copied.
 *
 * Measured on the owner's phone: 9,481 of 12,128 photos (78.2%) have MediaStore
 * `datetaken` NULL. Android then reports `creationTime` as DATE_ADDED, so a
 * picture taken in February and copied across in October files itself under
 * October. Three quarters of his library sat under the wrong month, which is the
 * "ordering is poor" he reported. Sorting harder cannot fix a wrong date.
 *
 * The rule: take the EARLIEST positive timestamp. Copying, downloading, restoring
 * a backup and forwarding on WhatsApp can only push a file's dates FORWARD, so
 * the oldest surviving date is the one closest to the shutter.
 *
 * Fixtures below use the real shape from his device, not invented numbers.
 *
 * Run: node --experimental-strip-types src/ui/photo-ordering.test.ts
 */

// @ts-expect-error Node requires the extension; Metro resolves this path too.
import { capturedAtFor } from "./photo-screen-model.ts";

function assert(condition: boolean, message: string): void {
  if (!condition) {
    console.error(`FAIL: ${message}`);
    process.exit(1);
  }
}

// Sampled from his library: datetaken present, twelve hours from date_modified,
// and date_added seven months later when the file reached this phone.
const WITH_DATETAKEN = { creationTime: 1_740_317_569_053, modificationTime: 1_740_359_719_000 };
// The 78.2% case: datetaken NULL, so creationTime IS date_added -- October --
// while date_modified still carries February from before the transfer.
const COPIED_ACROSS = { creationTime: 1_759_262_531_000, modificationTime: 1_740_359_719_000 };
const UNDATED = { creationTime: 0, modificationTime: 0 };

const FEBRUARY = new Date(1_740_359_719_000).getMonth();

assert(
  capturedAtFor(WITH_DATETAKEN) === 1_740_317_569_053,
  "a photo with a real capture date must keep it, not lose it to a later file mtime",
);

// The one that actually matters, and the one that was broken.
assert(
  new Date(capturedAtFor(COPIED_ACROSS)).getMonth() === FEBRUARY,
  `a copied photo filed under ${new Date(capturedAtFor(COPIED_ACROSS)).toISOString().slice(0, 7)} ` +
    `instead of the month it was taken — this is the 78.2% case`,
);
assert(
  capturedAtFor(COPIED_ACROSS) !== COPIED_ACROSS.creationTime,
  "creationTime was taken at face value, which is date_added for 78.2% of his library",
);

// Never invents a date. Undated has to stay undated.
assert(capturedAtFor(UNDATED) === 0, "a photo with no timestamps must stay Undated");
assert(
  capturedAtFor({ creationTime: 0, modificationTime: 1_740_359_719_000 }) === 1_740_359_719_000,
  "a zero timestamp must be ignored, not chosen as the earliest",
);

// Newest first, and the copied photo must NOT jump to the top.
const feed = [WITH_DATETAKEN, COPIED_ACROSS, { creationTime: 1_755_000_000_000, modificationTime: 1_755_000_000_000 }];
const ordered = [...feed].sort((a, b) => capturedAtFor(b) - capturedAtFor(a));
assert(
  ordered[0].creationTime === 1_755_000_000_000,
  "the genuinely newest photo must lead",
);
assert(
  ordered[ordered.length - 1] === COPIED_ACROSS || ordered[1] === COPIED_ACROSS,
  "the copied photo must sort by when it was taken, not by when it arrived",
);

// SABOTAGE: the old rule, to prove these fixtures can tell the two apart. If
// `creationTime || modificationTime` also passes, the fixtures are inert.
const oldRule = (a: { creationTime: number; modificationTime: number }) =>
  a.creationTime || a.modificationTime;
assert(
  new Date(oldRule(COPIED_ACROSS)).getMonth() !== FEBRUARY,
  "the previous rule files this fixture correctly too, so this test proves nothing",
);

console.log(
  "photo ordering: capture date wins over copy date (the 78.2% case), a real " +
    "datetaken is preserved, undated stays undated, and the old rule provably fails the same fixture",
);
