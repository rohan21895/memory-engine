// @ts-expect-error Node requires the source extension for this self-check.
import { DEFAULT_ALBUM_MAX_PHOTOS, albumSetupThumbnailRequestSize, canBuildFromAlbumSetup, createAlbumSetupRoster, initialAlbumBuildPreferences, normalizedAlbumMaxPhotos, parseAlbumSetupDraft, readAlbumSetupDraft, serializeAlbumSetupDraft, updateAlbumPersonPriority, writeAlbumSetupDraft, type AlbumSetupDraft, type AlbumSetupFileSystem } from "./album-setup-draft.ts";

function assert(condition: unknown, message: string): asserts condition {
  if (!condition) throw new Error(`album setup draft: ${message}`);
}

const photos = [
  {
    id: "asset-1",
    uri: "content://asset-1",
    filename: "one.jpg",
    source: "device-gallery" as const,
    personIds: ["person-a"],
  },
  {
    id: "asset-2",
    uri: "content://asset-2",
    filename: "two.jpg",
    source: "device-gallery" as const,
    personIds: ["person-a", "person-b"],
  },
  {
    id: "asset-3",
    uri: "content://asset-3",
    filename: "three.jpg",
    source: "device-gallery" as const,
  },
];

const roster = createAlbumSetupRoster(photos, [
  {
    id: "person-b",
    faceCount: 40,
    coverAssetId: "asset-2",
    assetIds: ["asset-2", "outside"],
    faceThumbUri: "data:image/jpeg;base64,do-not-persist",
  },
  {
    id: "person-a",
    faceCount: 10,
    coverAssetId: "asset-1",
    assetIds: ["asset-1", "asset-2"],
    faceThumbUri: "file://person-a.jpg",
  },
  {
    id: "person-outside",
    faceCount: 100,
    coverAssetId: "outside",
    assetIds: ["outside"],
  },
]);

assert(roster.length === 2, "only people in selected candidate photos are offered");
assert(roster[0]?.id === "person-a", "candidate-photo count determines roster order");
assert(roster[0]?.candidatePhotoCount === 2, "candidate-photo count is exact");
assert(roster[1]?.id === "person-b", "lower candidate count follows");

let preferences = initialAlbumBuildPreferences(roster);
assert(preferences.maxPhotos === DEFAULT_ALBUM_MAX_PHOTOS, "the default is 24");
assert(!canBuildFromAlbumSetup(preferences), "people require at least one Main focus");
preferences = updateAlbumPersonPriority(preferences, "person-a", "high");
preferences = updateAlbumPersonPriority(preferences, "person-b", "medium");
assert(canBuildFromAlbumSetup(preferences), "one Main focus makes the answer usable");
assert(preferences.personPriority["person-a"] === "high", "Main focus stores high");
assert(preferences.personPriority["person-b"] === "medium", "Include stores medium");
preferences = updateAlbumPersonPriority(preferences, "person-b", "low");
assert(!("person-b" in preferences.personPriority), "Background only is stored by absence");

const draft: AlbumSetupDraft = {
  pickedPhotos: photos,
  people: roster,
  preferences,
  updatedAt: 1_777_777,
};
const serialized = serializeAlbumSetupDraft(draft);
assert(!serialized.includes("do-not-persist"), "face image data is not copied into the draft");
const parsed = parseAlbumSetupDraft(serialized);
assert(parsed?.pickedPhotos.length === 3, "selected photos resume");
assert(parsed?.preferences.personPriority["person-a"] === "high", "answers resume");
assert(parsed?.preferences.offeredPersonIds.join(",") === "person-a,person-b", "frozen roster resumes");
assert(parsed?.people[0]?.faceThumbUri === "file://person-a.jpg", "small local face references resume");
assert(parsed?.people[1]?.faceThumbUri === undefined, "embedded face image bytes are rehydrated, not persisted");

const tampered = JSON.parse(serialized) as {
  draft: { preferences: { offeredPersonIds: string[] } };
};
tampered.draft.preferences.offeredPersonIds = ["person-b", "person-a"];
assert(parseAlbumSetupDraft(JSON.stringify(tampered)) === undefined, "a reordered frozen roster is rejected");
assert(parseAlbumSetupDraft("not json") === undefined, "corrupt drafts fail closed");

assert(albumSetupThumbnailRequestSize(80) === 192, "face fallback requests 2x in a 64px cache bucket");
assert(albumSetupThumbnailRequestSize(20) === 128, "small requests keep the platform cache floor");
assert(albumSetupThumbnailRequestSize(600) === 512, "large requests keep the platform cache ceiling");
assert(normalizedAlbumMaxPhotos("24") === 24, "whole positive photo counts are accepted");
assert(normalizedAlbumMaxPhotos("0") === undefined, "zero is rejected");
assert(normalizedAlbumMaxPhotos("2.5") === undefined, "fractions are rejected");

class MemoryFileSystem implements AlbumSetupFileSystem {
  files = new Map<string, string>();
  operations: string[] = [];

  async readAsStringAsync(uri: string): Promise<string> {
    const value = this.files.get(uri);
    if (value === undefined) throw new Error("missing");
    return value;
  }

  async writeAsStringAsync(uri: string, contents: string): Promise<void> {
    this.operations.push(`write:${uri}`);
    this.files.set(uri, contents);
  }

  async deleteAsync(uri: string): Promise<void> {
    this.operations.push(`delete:${uri}`);
    this.files.delete(uri);
  }

  async moveAsync({ from, to }: { from: string; to: string }): Promise<void> {
    this.operations.push(`move:${from}:${to}`);
    const value = this.files.get(from);
    if (value === undefined) throw new Error("missing source");
    this.files.set(to, value);
    this.files.delete(from);
  }
}

const fileSystem = new MemoryFileSystem();
await writeAlbumSetupDraft(fileSystem, "draft.json", draft);
assert(fileSystem.operations.join("|") === "write:draft.json.tmp|delete:draft.json|move:draft.json.tmp:draft.json", "writes use a replaceable checkpoint");
assert((await readAlbumSetupDraft(fileSystem, "draft.json"))?.updatedAt === draft.updatedAt, "the durable draft reads back");

fileSystem.files.set("draft.json.tmp", "truncated");
assert((await readAlbumSetupDraft(fileSystem, "draft.json"))?.updatedAt === draft.updatedAt, "a corrupt crash residue falls back to the last complete draft");

console.log("album setup draft: roster, rules, thumbnails, and crash-safe resume hold");
