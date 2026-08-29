/**
 * Photos file under the month they were TAKEN, not the month they were copied.
 *
 * Measured on the owner's phone: 9,481 of 12,128 photos (78.2%) have MediaStore
 * `datetaken` NULL. Android then reports `creationTime` as DATE_ADDED, so a
 * picture taken in February and copied across in October files itself under
 * October. Three quarters of his library sat under the wrong month, which is the
 * "ordering is poor" he reported. Sorting harder cannot fix a wrong date.
 *
 * Two rules, in order. The FILENAME wins when it carries a date -- 11,892 of his
 * 12,128 photos (98.1%) name their own, and a name survives copying where every
 * MediaStore column drifts. Otherwise the EARLIEST positive timestamp, because
 * copying, downloading, restoring a backup and forwarding on WhatsApp can only
 * push a file's dates FORWARD.
 *
 * Measured on his library, photos filed under the WRONG MONTH:
 *   date_added (the original behaviour)  4,755
 *   date_modified (first attempt)          399
 *   filename rule (shipped)                  0
 *
 * Fixtures below use the real shape from his device, not invented numbers.
 *
 * Run: node --experimental-strip-types src/ui/photo-ordering.test.ts
 */

// @ts-expect-error Node requires the extension; Metro resolves this path too.
import { capturedAtFor, filenameCapturedAt, mergeByCapturedAt } from "./photo-screen-model.ts";

function assert(condition: boolean, message: string): void {
  if (!condition) {
    console.error(`FAIL: ${message}`);
    process.exit(1);
  }
}

// Sampled from his library: datetaken present, twelve hours from date_modified,
// and date_added seven months later when the file reached this phone.
const WITH_DATETAKEN = { creationTime: 1_740_317_569_053, id: "with-datetaken", modificationTime: 1_740_359_719_000 };
// The 78.2% case: datetaken NULL, so creationTime IS date_added -- October --
// while date_modified still carries February from before the transfer.
const COPIED_ACROSS = { creationTime: 1_759_262_531_000, id: "copied-across", modificationTime: 1_740_359_719_000 };
const UNDATED = { creationTime: 0, id: "undated", modificationTime: 0 };

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
  capturedAtFor({ creationTime: 0, id: "mtime-only", modificationTime: 1_740_359_719_000 }) === 1_740_359_719_000,
  "a zero timestamp must be ignored, not chosen as the earliest",
);

// Newest first, and the copied photo must NOT jump to the top.
const feed = [WITH_DATETAKEN, COPIED_ACROSS, { creationTime: 1_755_000_000_000, id: "newest", modificationTime: 1_755_000_000_000 }];
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

// ---------------------------------------------------------------------------
// The filename rule. 9,368 of the 9,481 NULL-datetaken photos (98.8%) name
// their own capture date, and unlike every MediaStore column a name survives
// copying untouched.
// ---------------------------------------------------------------------------

const FILENAMES: Array<[string, string | null]> = [
  // Real names sampled from his library and from the stock Android/Pixel apps.
  ["IMG-20250817-WA0042.jpg", "2025-08-17"],
  ["IMG-20241213-WA0040.jpg", "2024-12-13"],
  ["IMG_20240215_143022.jpg", "2024-02-15"],
  ["PXL_20230814_091234567.jpg", "2023-08-14"],
  ["Screenshot_2024-02-15-14-30-22.png", "2024-02-15"],
  // Must NOT parse: two-digit year is ambiguous, so these fall back to the file
  // timestamps rather than guessing a century.
  ["IMG-251004-102032-53705.jpg", null],
  // Must NOT parse: no date in the name at all.
  ["Scanner_MainBank.jpeg", null],
  ["TRJ Pricing.jpg", null],
  // Must NOT parse: 31 February rolls over to 3 March in JS, so a naive parser
  // silently invents a date rather than declining.
  ["IMG_20240231_120000.jpg", null],
  ["IMG_20241399_120000.jpg", null],
];

for (const [name, expected] of FILENAMES) {
  const parsed = filenameCapturedAt(name);
  if (expected === null) {
    assert(parsed === 0, `${name} must not yield a date, but produced ${new Date(parsed).toISOString()}`);
    continue;
  }
  const iso = new Date(parsed - new Date(parsed).getTimezoneOffset() * 60_000)
    .toISOString()
    .slice(0, 10);
  assert(iso === expected, `${name} should read as ${expected}, got ${iso}`);
}

// Time-of-day is taken when the name carries it, so an afternoon of photos
// keeps its order instead of collapsing onto a shared midnight.
assert(
  filenameCapturedAt("IMG_20240215_143022.jpg") > filenameCapturedAt("IMG_20240215_091500.jpg"),
  "two photos from one day must keep their order when the names carry a time",
);

// A name claiming the future is corrupt, not a capture date.
assert(
  filenameCapturedAt("IMG_20991231_120000.jpg") === 0,
  "a filename dated in the future must be refused, not trusted",
);

// The rule that matters: the WhatsApp photo above is 551 days out on
// date_modified in the worst real case. The filename overrules it.
const WA_WRONG_MONTH = {
  creationTime: 1_759_262_731_000, // date_added: October 2025
  filename: "IMG-20250817-WA0042.jpg", // taken: August 2025
  id: "wa-wrong-month",
  modificationTime: 1_735_000_000_000, // date_modified: December 2024 — wrong
};
assert(
  new Date(capturedAtFor(WA_WRONG_MONTH)).getMonth() === 7 &&
    new Date(capturedAtFor(WA_WRONG_MONTH)).getFullYear() === 2025,
  `the filename says August 2025 but this filed under ` +
    `${new Date(capturedAtFor(WA_WRONG_MONTH)).toISOString().slice(0, 7)}`,
);

// When the two AGREE on the day, the file stamp is kept — it has the hour the
// WhatsApp-style name does not, so same-day photos stay in shooting order.
const SAME_DAY = {
  creationTime: 0,
  filename: "IMG-20250817-WA0042.jpg",
  id: "same-day",
  modificationTime: new Date(2025, 7, 17, 16, 45, 0).getTime(),
};
assert(
  capturedAtFor(SAME_DAY) === SAME_DAY.modificationTime,
  "when the name and the file stamp agree on the day, the stamp must win so the time survives",
);

// A precomputed stamp short-circuits, so the regex never runs in a comparator.
assert(
  capturedAtFor({ ...WA_WRONG_MONTH, capturedAt: 123 }) === 123,
  "a precomputed capturedAt must be used as-is",
);

// ---------------------------------------------------------------------------
// The merge. MediaStore pages by date_modified, the feed displays by
// capturedAt, so a photo can belong many pages above the one it arrives in.
// ---------------------------------------------------------------------------

const asset = (id: string, at: number) => ({
  capturedAt: at,
  creationTime: at,
  id,
  modificationTime: at,
});
const pageOne = [asset("a", 900), asset("b", 500), asset("c", 100)];
const pageTwo = [asset("d", 950), asset("e", 700), asset("f", 50)];
const mergedFeed = mergeByCapturedAt(pageOne, pageTwo);

assert(mergedFeed.length === 6, "the merge must not drop or duplicate photos");
assert(
  // 950 d, 900 a, 700 e, 500 b, 100 c, 50 f — interleaved, which is the point:
  // every photo from page two lands somewhere inside page one.
  mergedFeed.map((item) => item.id).join("") === "daebcf",
  `merge produced ${mergedFeed.map((item) => item.id).join("")}, expected daebcf`,
);
let descending = true;
for (let i = 1; i < mergedFeed.length; i += 1) {
  if (mergedFeed[i - 1].capturedAt < mergedFeed[i].capturedAt) descending = false;
}
assert(descending, "the merged feed must stay newest-first, or month headers repeat down the grid");
assert(
  mergeByCapturedAt([], pageTwo).length === 3 && mergeByCapturedAt(pageOne, []).length === 3,
  "an empty side must pass the other through unchanged",
);

// SABOTAGE: appending, which is what the code did before. If the assertion
// above passes for a plain concat too, this fixture cannot detect the bug.
const appended = pageOne.concat(pageTwo);
let appendedDescending = true;
for (let i = 1; i < appended.length; i += 1) {
  if (appended[i - 1].capturedAt < appended[i].capturedAt) appendedDescending = false;
}
assert(
  !appendedDescending,
  "concat also yields a sorted feed for this fixture, so the merge assertion proves nothing",
);

console.log(
  "photo ordering: filename date beats a drifted file stamp (the 4% date_modified " +
    "still got wrong), same-day keeps its time, ambiguous and impossible names are " +
    "refused, pages merge instead of appending, and concat provably fails the same fixture",
);
