import test from "node:test";

// Phase 0 installs this gate before network-capable processes exist. Replace these
// TODOs with a deny-by-default network sandbox around each process as it lands.
test.todo("blocks an outbound connection without a consent-ledger entry");
test.todo("allows only the destination and payload class recorded by consent");
test.todo("rejects paths, filenames, and EXIF in crash or analytics payloads");
