const { readFileSync } = require("node:fs");
const { resolve } = require("node:path");

function assert(value, message) {
  if (!value) throw new Error(`album title self-check failed: ${message}`);
}

/**
 * An album must be named for when its photos were TAKEN.
 *
 * The bug this pins was visible on the Albums tab: an album whose own subtitle
 * read "24 photos - Feb 2025" was titled "August memories". `suggestedAlbumTitle`
 * read `photo.creationTime` -- MediaStore `date_added`, i.e. when the file landed
 * on this phone -- and took the FIRST photo that had one. On this library that
 * field files 4,755 photos in the wrong month, because 78% have no real capture
 * timestamp; `capturedAtFor` prefers a filename date, which 98.1% of them carry.
 */
const app = readFileSync(resolve(__dirname, "../../App.tsx"), "utf8");

const start = app.indexOf("function suggestedAlbumTitle(");
assert(start >= 0, "suggestedAlbumTitle is discoverable");
const body = app.slice(start, app.indexOf("\nfunction ", start + 1));

assert(
  body.includes("capturedAtFor("),
  "the title must come from capturedAtFor, which owns the filename-first rule",
);
assert(
  !/photo\.creationTime\b(?![^]*?capturedAtFor)/.test(
    body.replace(/creationTime: photo\.creationTime \?\? 0,/, ""),
  ),
  "the title must not read photo.creationTime directly -- that is date_added, not capture time",
);
// One outlier photo must not name a whole album, so the month is the mode.
assert(
  body.includes("right.count - left.count"),
  "the month must be the most common one in the selection, not the first hit",
);
// "February memories" for photos from 2019 is the same quiet lie in a new coat.
assert(
  body.includes("getFullYear()") && body.includes("${month} ${year} memories"),
  "a year outside the current one must appear in the title",
);

console.log("album title self-check passed");
