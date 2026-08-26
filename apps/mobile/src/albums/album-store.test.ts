// @ts-expect-error TypeScript bundler resolution normally omits source extensions.
import { readShelf, type ShelfReader } from "./album-store.ts";

// Local assert to match the house test style (the app tsconfig has no
// @types/node, so node:test / node:assert are intentionally not imported).
function assert(value: unknown, message: string): asserts value {
  if (!value) throw new Error(`album-store self-check failed: ${message}`);
}

const URI = "file:///documents/albums-v1.json";

function album(id: string, title: string) {
  return {
    id,
    title,
    coverUri: "content://media/external/images/media/1",
    photoIds: ["1"],
    photos: [{ media_id: "1", uri: "content://media/external/images/media/1", page: 0 }],
    reviewData: { album_id: id, selected: [], pool: [] },
    dateRange: {},
    createdAt: 1,
    updatedAt: 2,
  };
}

function shelfFile(...albums: ReturnType<typeof album>[]): string {
  return JSON.stringify({ version: 1, albums });
}

/** A fake disk: any uri absent from the map throws, exactly as the real read does. */
function reader(files: Record<string, string>): ShelfReader {
  return {
    readAsStringAsync: async (uri: string) => {
      if (!(uri in files)) throw new Error(`ENOENT ${uri}`);
      return files[uri];
    },
  };
}

// Top-level await is unavailable under the CJS transform the runner uses, so the
// checks live in a main() whose rejection fails the process.
async function main(): Promise<void> {
  // Nothing on disk yet: a first run has no albums, and writing is safe.
  const empty = await readShelf(reader({}), URI);
  assert(empty.ok && empty.albums.length === 0, "absent shelf reads as an empty, writable shelf");

  // The normal case.
  const saved = await readShelf(reader({ [URI]: shelfFile(album("a", "Goa")) }), URI);
  assert(saved.ok && saved.albums.length === 1, "a valid shelf loads");
  assert(saved.ok && saved.albums[0].title === "Goa", "album survives the round trip");

  // ── The data-loss guard ──
  // A present-but-unreadable shelf must NOT read as empty. The old code cached []
  // on any failure and never retried, so the next save wrote a one-album shelf
  // over everything the user had.
  const corrupt = await readShelf(reader({ [URI]: "{ this is not json" }), URI);
  assert(corrupt.ok === false, "a corrupt shelf refuses to read rather than reading as empty");

  // Wrong store version is equally not-empty: it holds the user's albums.
  const wrongVersion = await readShelf(
    reader({ [URI]: JSON.stringify({ version: 99, albums: [] }) }),
    URI,
  );
  assert(wrongVersion.ok === false, "an unrecognised store version never reads as empty");

  // ── Crash recovery ──
  // persist() writes .tmp, deletes the file, then moves. A leftover .tmp means the
  // app died mid-write and holds the NEWEST shelf, so it wins.
  const crashed = await readShelf(
    reader({
      [`${URI}.tmp`]: shelfFile(album("a", "Goa"), album("b", "Manali")),
      [URI]: shelfFile(album("a", "Goa")),
    }),
    URI,
  );
  assert(crashed.ok && crashed.albums.length === 2, "a complete .tmp wins over the older file");

  // A TRUNCATED .tmp is the expected shape of that same crash, so it must fall
  // through to the real file instead of failing the whole shelf closed.
  const truncated = await readShelf(
    reader({
      [`${URI}.tmp`]: '{"version":1,"albums":[{"id":"b","tit',
      [URI]: shelfFile(album("a", "Goa")),
    }),
    URI,
  );
  assert(truncated.ok && truncated.albums.length === 1, "a truncated .tmp falls through to the real file");

  // Truncated .tmp with no real file behind it is still a first run, not damage.
  const truncatedOnly = await readShelf(
    reader({ [`${URI}.tmp`]: '{"version":1,"alb' }),
    URI,
  );
  assert(truncatedOnly.ok && truncatedOnly.albums.length === 0, "truncated .tmp alone reads as empty");

  // eslint-disable-next-line no-console
  console.log("album-store self-check passed");
}

void main();
