import type { ReviewData } from "../review/mock-data";
import type { FinalPhoto } from "../review/FinalAlbum";

const STORE_VERSION = 1;
const STORE_FILENAME = "albums-v1.json";

export type SavedAlbum = {
  id: string;
  title: string;
  coverUri: string;
  photoIds: string[];
  photos: FinalPhoto[];
  reviewData: ReviewData;
  dateRange: { start?: number; end?: number };
  createdAt: number;
  updatedAt: number;
};

type PersistedStore = { version: typeof STORE_VERSION; albums: SavedAlbum[] };

let cachedAlbums: SavedAlbum[] | null = null;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isFinalPhoto(value: unknown): value is FinalPhoto {
  return (
    isRecord(value) &&
    typeof value.media_id === "string" &&
    typeof value.uri === "string" &&
    typeof value.page === "number" &&
    (value.width === undefined || (typeof value.width === "number" && Number.isFinite(value.width) && value.width > 0)) &&
    (value.height === undefined || (typeof value.height === "number" && Number.isFinite(value.height) && value.height > 0))
  );
}

function isReviewData(value: unknown): value is ReviewData {
  return isRecord(value) && typeof value.album_id === "string" && Array.isArray(value.selected) && Array.isArray(value.pool);
}

function isSavedAlbum(value: unknown): value is SavedAlbum {
  return (
    isRecord(value) &&
    typeof value.id === "string" &&
    typeof value.title === "string" &&
    typeof value.coverUri === "string" &&
    Array.isArray(value.photoIds) && value.photoIds.every((id) => typeof id === "string") &&
    Array.isArray(value.photos) && value.photos.every(isFinalPhoto) &&
    isReviewData(value.reviewData) &&
    isRecord(value.dateRange) &&
    typeof value.createdAt === "number" && Number.isFinite(value.createdAt) &&
    typeof value.updatedAt === "number" && Number.isFinite(value.updatedAt)
  );
}

function parseStore(contents: string): SavedAlbum[] | null {
  try {
    const value: unknown = JSON.parse(contents);
    if (!isRecord(value) || value.version !== STORE_VERSION || !Array.isArray(value.albums) || !value.albums.every(isSavedAlbum)) return null;
    return value.albums;
  } catch {
    return null;
  }
}

async function fileSystemModule(): Promise<typeof import("expo-file-system/legacy")> {
  return import("expo-file-system/legacy");
}

/** The one I/O call the shelf reader needs, narrowed so it can be faked in tests. */
export type ShelfReader = { readAsStringAsync: (uri: string) => Promise<string> };

/**
 * "missing" and "corrupt" must stay distinct: an absent file means the user has
 * no albums yet and writing is safe, while an unreadable one means their albums
 * are still on disk and writing would destroy them.
 */
type ReadResult = "missing" | "corrupt" | SavedAlbum[];

async function readStore(fileSystem: ShelfReader, uri: string): Promise<ReadResult> {
  let contents: string;
  try {
    contents = await fileSystem.readAsStringAsync(uri);
  } catch {
    return "missing";
  }
  return parseStore(contents) ?? "corrupt";
}

/** `ok: false` means the shelf exists but could not be read — never overwrite it. */
export type ShelfRead = { ok: true; albums: SavedAlbum[] } | { ok: false };

export async function readShelf(fileSystem: ShelfReader, uri: string): Promise<ShelfRead> {
  // A leftover .tmp is the residue of a crash between the write and the move,
  // so it holds the newest shelf. A truncated one is the expected shape of that
  // crash, so fall through to the real file rather than treating it as damage.
  const fromTemporary = await readStore(fileSystem, `${uri}.tmp`);
  if (Array.isArray(fromTemporary)) return { ok: true, albums: fromTemporary };

  const fromFile = await readStore(fileSystem, uri);
  if (Array.isArray(fromFile)) return { ok: true, albums: fromFile };
  return fromFile === "missing" ? { ok: true, albums: [] } : { ok: false };
}

/**
 * Returns the shelf, or null when disk could not be read.
 *
 * Callers that only display albums can treat null as empty, but callers that
 * write MUST NOT: the old code cached `[]` on any read failure and never
 * retried, so one transient failure made the next save persist a shelf of one
 * album over every album the user had.
 */
async function shelf(): Promise<SavedAlbum[] | null> {
  if (cachedAlbums) return cachedAlbums.slice();
  try {
    const fileSystem = await fileSystemModule();
    if (!fileSystem.documentDirectory) return null;
    const read = await readShelf(fileSystem, `${fileSystem.documentDirectory}${STORE_FILENAME}`);
    if (!read.ok) return null;
    cachedAlbums = read.albums;
    return cachedAlbums.slice();
  } catch {
    return null;
  }
}

export async function loadAlbums(): Promise<SavedAlbum[]> {
  return (await shelf()) ?? [];
}

/**
 * Applies a change to the shelf and writes it back. When disk is unreadable the
 * change stays in memory only, so the session keeps working and the albums on
 * disk survive to be recovered by the next successful load.
 */
async function mutate(apply: (albums: SavedAlbum[]) => SavedAlbum[]): Promise<SavedAlbum[]> {
  const current = await shelf();
  if (!current) return apply([]);
  const next = apply(current);
  await persist(next);
  return next.slice();
}

async function persist(albums: SavedAlbum[]): Promise<void> {
  cachedAlbums = albums.slice();
  try {
    const fileSystem = await fileSystemModule();
    if (!fileSystem.documentDirectory) return;
    const uri = `${fileSystem.documentDirectory}${STORE_FILENAME}`;
    const temporaryUri = `${uri}.tmp`;
    const store: PersistedStore = { version: STORE_VERSION, albums };
    await fileSystem.writeAsStringAsync(temporaryUri, JSON.stringify(store));
    await fileSystem.deleteAsync(uri, { idempotent: true });
    await fileSystem.moveAsync({ from: temporaryUri, to: uri });
  } catch {
    // The in-memory album shelf remains usable; the next mutation retries disk.
  }
}

export async function saveAlbum(album: SavedAlbum): Promise<SavedAlbum[]> {
  return mutate((albums) =>
    [album, ...albums.filter((candidate) => candidate.id !== album.id)]
      .sort((left, right) => right.updatedAt - left.updatedAt));
}

export async function renameAlbum(id: string, title: string): Promise<SavedAlbum[]> {
  const cleanTitle = title.trim() || "My photo album";
  return mutate((albums) =>
    albums.map((album) => album.id === id ? { ...album, title: cleanTitle, updatedAt: Date.now() } : album));
}

export async function deleteAlbum(id: string): Promise<SavedAlbum[]> {
  return mutate((albums) => albums.filter((album) => album.id !== id));
}
