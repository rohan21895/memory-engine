import type { FaceIndexPerson } from "../faces/face-index";
import type { PickedPhoto } from "../import/picked-photo";
// @ts-expect-error Node self-checks require the source extension; Metro resolves it too.
import { hasUsablePriorities, type AlbumBuildPreferences, type PersonPriority } from "../selection/album-build-preferences.ts";

const STORE_VERSION = 1;
const STORE_FILENAME = "album-setup-draft-v1.json";

export const DEFAULT_ALBUM_MAX_PHOTOS = 24;

export type AlbumSetupPerson = {
  id: string;
  candidatePhotoCount: number;
  coverAssetId: string;
  /** Kept in memory for the screen, but omitted from the durable draft. */
  faceThumbUri?: string;
};

export type AlbumSetupDraft = {
  pickedPhotos: PickedPhoto[];
  people: AlbumSetupPerson[];
  preferences: AlbumBuildPreferences;
  updatedAt: number;
};

export type AlbumSetupFileSystem = {
  readAsStringAsync: (uri: string) => Promise<string>;
  writeAsStringAsync: (uri: string, contents: string) => Promise<void>;
  deleteAsync: (uri: string, options: { idempotent: boolean }) => Promise<void>;
  moveAsync: (options: { from: string; to: string }) => Promise<void>;
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isPickedPhoto(value: unknown): value is PickedPhoto {
  if (!isRecord(value)) return false;
  if (
    typeof value.id !== "string" ||
    typeof value.uri !== "string" ||
    typeof value.filename !== "string" ||
    !["device-gallery", "local-folder", "google-photos"].includes(String(value.source))
  ) {
    return false;
  }
  return (
    (value.personIds === undefined ||
      (Array.isArray(value.personIds) && value.personIds.every((id) => typeof id === "string"))) &&
    (value.width === undefined || typeof value.width === "number") &&
    (value.height === undefined || typeof value.height === "number") &&
    (value.creationTime === undefined || typeof value.creationTime === "number")
  );
}

function isSetupPerson(value: unknown): value is AlbumSetupPerson {
  return (
    isRecord(value) &&
    typeof value.id === "string" &&
    typeof value.coverAssetId === "string" &&
    Number.isInteger(value.candidatePhotoCount) &&
    Number(value.candidatePhotoCount) > 0 &&
    (value.faceThumbUri === undefined || typeof value.faceThumbUri === "string")
  );
}

function isPreferences(value: unknown): value is AlbumBuildPreferences {
  if (
    !isRecord(value) ||
    !Number.isInteger(value.maxPhotos) ||
    Number(value.maxPhotos) < 1 ||
    !isRecord(value.personPriority) ||
    !Array.isArray(value.offeredPersonIds) ||
    !value.offeredPersonIds.every((id) => typeof id === "string")
  ) {
    return false;
  }
  return Object.values(value.personPriority).every(
    (priority) => priority === "high" || priority === "medium",
  );
}

/** People present in the selected candidate photos, in the order the UI promises. */
export function createAlbumSetupRoster(
  photos: readonly PickedPhoto[],
  people: readonly FaceIndexPerson[],
): AlbumSetupPerson[] {
  const selectedAssetIds = new Set(photos.map((photo) => photo.id));
  return people
    .map((person) => ({
      id: person.id,
      candidatePhotoCount: person.assetIds.reduce(
        (count, assetId) => count + Number(selectedAssetIds.has(assetId)),
        0,
      ),
      coverAssetId: person.coverAssetId,
      faceThumbUri: person.faceThumbUri,
      faceCount: person.faceCount,
    }))
    .filter((person) => person.candidatePhotoCount > 0)
    .sort(
      (left, right) =>
        right.candidatePhotoCount - left.candidatePhotoCount ||
        right.faceCount - left.faceCount ||
        left.id.localeCompare(right.id),
    )
    .map(({ faceCount: _faceCount, ...person }) => person);
}

/** Restore cached face crops without letting regrouping reorder the frozen roster. */
export function hydrateAlbumSetupRoster(
  frozen: readonly AlbumSetupPerson[],
  livePeople: readonly FaceIndexPerson[],
): AlbumSetupPerson[] {
  const liveById = new Map(livePeople.map((person) => [person.id, person]));
  return frozen.map((person) => ({
    ...person,
    faceThumbUri: liveById.get(person.id)?.faceThumbUri ?? person.faceThumbUri,
  }));
}

export function initialAlbumBuildPreferences(
  people: readonly AlbumSetupPerson[],
  maxPhotos = DEFAULT_ALBUM_MAX_PHOTOS,
): AlbumBuildPreferences {
  return {
    maxPhotos,
    personPriority: {},
    offeredPersonIds: people.map((person) => person.id),
  };
}

export function updateAlbumPersonPriority(
  preferences: AlbumBuildPreferences,
  personId: string,
  priority: PersonPriority,
): AlbumBuildPreferences {
  if (!preferences.offeredPersonIds.includes(personId)) return preferences;
  const personPriority: Record<string, "high" | "medium"> = {
    ...preferences.personPriority,
  };
  if (priority === "low") delete personPriority[personId];
  else personPriority[personId] = priority;
  return { ...preferences, personPriority };
}

export function canBuildFromAlbumSetup(preferences: AlbumBuildPreferences): boolean {
  return preferences.offeredPersonIds.length === 0 || hasUsablePriorities(preferences);
}

export function normalizedAlbumMaxPhotos(value: string | number): number | undefined {
  const parsed = typeof value === "number" ? value : Number(value.trim());
  return Number.isInteger(parsed) && parsed > 0 ? parsed : undefined;
}

/** Same 2x, 64-pixel cache bucket policy as the Photos grid. */
export function albumSetupThumbnailRequestSize(tileSize: number): number {
  return Math.min(512, Math.max(128, Math.round((tileSize * 2) / 64) * 64));
}

export function serializeAlbumSetupDraft(draft: AlbumSetupDraft): string {
  return JSON.stringify({
    version: STORE_VERSION,
    draft: {
      ...draft,
      people: draft.people.map((person) => ({
        ...person,
        // A data URI duplicates image bytes into the draft. File URIs are tiny,
        // remain local, and preserve the promised face tile after a restart.
        faceThumbUri: person.faceThumbUri?.startsWith("file://")
          ? person.faceThumbUri
          : undefined,
      })),
    },
  });
}

export function parseAlbumSetupDraft(raw: string): AlbumSetupDraft | undefined {
  try {
    const stored: unknown = JSON.parse(raw);
    if (!isRecord(stored) || stored.version !== STORE_VERSION || !isRecord(stored.draft)) {
      return undefined;
    }
    const draft = stored.draft;
    if (
      !Array.isArray(draft.pickedPhotos) ||
      draft.pickedPhotos.length === 0 ||
      !draft.pickedPhotos.every(isPickedPhoto) ||
      !Array.isArray(draft.people) ||
      !draft.people.every(isSetupPerson) ||
      !isPreferences(draft.preferences) ||
      typeof draft.updatedAt !== "number" ||
      !Number.isFinite(draft.updatedAt)
    ) {
      return undefined;
    }

    const people = draft.people as AlbumSetupPerson[];
    const preferences = draft.preferences;
    const rosterIds = people.map((person) => person.id);
    if (
      new Set(rosterIds).size !== rosterIds.length ||
      rosterIds.length !== preferences.offeredPersonIds.length ||
      rosterIds.some((id, index) => preferences.offeredPersonIds[index] !== id) ||
      Object.keys(preferences.personPriority).some((id) => !rosterIds.includes(id))
    ) {
      return undefined;
    }

    return {
      pickedPhotos: (draft.pickedPhotos as PickedPhoto[]).map((photo) => ({ ...photo })),
      people: people.map((person) => ({ ...person })),
      preferences: {
        maxPhotos: preferences.maxPhotos,
        personPriority: { ...preferences.personPriority },
        offeredPersonIds: preferences.offeredPersonIds.slice(),
      },
      updatedAt: draft.updatedAt,
    };
  } catch {
    return undefined;
  }
}

async function readCheckpoint(
  fileSystem: Pick<AlbumSetupFileSystem, "readAsStringAsync">,
  uri: string,
): Promise<AlbumSetupDraft | undefined> {
  try {
    return parseAlbumSetupDraft(await fileSystem.readAsStringAsync(uri));
  } catch {
    return undefined;
  }
}

export async function readAlbumSetupDraft(
  fileSystem: Pick<AlbumSetupFileSystem, "readAsStringAsync">,
  uri: string,
): Promise<AlbumSetupDraft | undefined> {
  return (await readCheckpoint(fileSystem, `${uri}.tmp`)) ?? readCheckpoint(fileSystem, uri);
}

export async function writeAlbumSetupDraft(
  fileSystem: AlbumSetupFileSystem,
  uri: string,
  draft: AlbumSetupDraft,
): Promise<void> {
  const temporaryUri = `${uri}.tmp`;
  await fileSystem.writeAsStringAsync(temporaryUri, serializeAlbumSetupDraft(draft));
  await fileSystem.deleteAsync(uri, { idempotent: true });
  await fileSystem.moveAsync({ from: temporaryUri, to: uri });
}

let writeQueue = Promise.resolve();

async function defaultFile(): Promise<{
  fileSystem: AlbumSetupFileSystem;
  uri: string;
} | undefined> {
  try {
    const fileSystem = await import("expo-file-system/legacy");
    if (!fileSystem.documentDirectory) return undefined;
    return { fileSystem, uri: `${fileSystem.documentDirectory}${STORE_FILENAME}` };
  } catch {
    return undefined;
  }
}

export async function loadAlbumSetupDraft(): Promise<AlbumSetupDraft | undefined> {
  await writeQueue.catch(() => undefined);
  const target = await defaultFile();
  return target ? readAlbumSetupDraft(target.fileSystem, target.uri) : undefined;
}

export function saveAlbumSetupDraft(draft: AlbumSetupDraft): Promise<void> {
  writeQueue = writeQueue.catch(() => undefined).then(async () => {
    const target = await defaultFile();
    if (target) await writeAlbumSetupDraft(target.fileSystem, target.uri, draft);
  });
  return writeQueue;
}

export function clearAlbumSetupDraft(): Promise<void> {
  writeQueue = writeQueue.catch(() => undefined).then(async () => {
    const target = await defaultFile();
    if (!target) return;
    await target.fileSystem.deleteAsync(target.uri, { idempotent: true });
    await target.fileSystem.deleteAsync(`${target.uri}.tmp`, { idempotent: true });
  });
  return writeQueue;
}
