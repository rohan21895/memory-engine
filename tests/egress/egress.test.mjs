import test from "node:test";

// Phase 0 installs this gate before network-capable processes exist. Replace these
// TODOs with a deny-by-default network sandbox around each process as it lands.
//
// These three are about the CONNECTION. The payload half of the same rule is
// covered now, for the one artefact that exists to be uploaded, in
// contact-sheet.test.mjs -- a consent-ledger entry that says "one contact sheet"
// is worth nothing if the sheet carries the filenames and GPS fixes the entry
// does not mention.
test.todo("blocks an outbound connection without a consent-ledger entry");
test.todo("allows only the destination and payload class recorded by consent");
test.todo("rejects paths, filenames, and EXIF in crash or analytics payloads");
