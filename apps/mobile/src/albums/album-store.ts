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
  return isRecord(value) && typeof value.media_id === "string" && typeof value.uri === "string" && typeof value.page === "number";
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

async function readStore(fileSystem: typeof import("expo-file-system/legacy"), uri: string) {
  try {
    return parseStore(await fileSystem.readAsStringAsync(uri));
  } catch {
    return null;
  }
}

export async function loadAlbums(): Promise<SavedAlbum[]> {
  if (cachedAlbums) return cachedAlbums.slice();
  try {
    const fileSystem = await fileSystemModule();
    if (!fileSystem.documentDirectory) return [];
    const uri = `${fileSystem.documentDirectory}${STORE_FILENAME}`;
    cachedAlbums = await readStore(fileSystem, `${uri}.tmp`) ?? await readStore(fileSystem, uri) ?? [];
  } catch {
    cachedAlbums = [];
  }
  return cachedAlbums.slice();
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
  const albums = await loadAlbums();
  const next = [album, ...albums.filter((candidate) => candidate.id !== album.id)]
    .sort((left, right) => right.updatedAt - left.updatedAt);
  await persist(next);
  return next.slice();
}

export async function renameAlbum(id: string, title: string): Promise<SavedAlbum[]> {
  const albums = await loadAlbums();
  const cleanTitle = title.trim() || "My photo album";
  const next = albums.map((album) => album.id === id ? { ...album, title: cleanTitle, updatedAt: Date.now() } : album);
  await persist(next);
  return next.slice();
}

export async function deleteAlbum(id: string): Promise<SavedAlbum[]> {
  const albums = await loadAlbums();
  const next = albums.filter((album) => album.id !== id);
  await persist(next);
  return next.slice();
}
