import type {
  NearDuplicateRankingLabel,
  PreferenceLabelFileSystem,
} from "./preference-label-store";
// @ts-expect-error Node's native TypeScript runner requires the extension.
import { appendAlbumEditPreference, MAX_PREFERENCE_LABEL_RECORDS, openPhotoPreferenceLabelStore, parsePhotoPreferenceLabels, preferenceAssetId, PHOTO_FEATURE_SCHEMA_VERSION, PHOTO_SELECTOR_CONFIG_VERSION, PHOTO_SELECTOR_NAME, serializePhotoPreferenceLabels } from "./preference-label-store.ts";

function assert(condition: unknown, message: string): asserts condition {
  if (!condition) throw new Error(`Preference label store self-check failed: ${message}`);
}

class MemoryFileSystem implements PreferenceLabelFileSystem {
  readonly files = new Map<string, string>();
  writes = 0;
  moves = 0;

  async readAsStringAsync(uri: string): Promise<string> {
    const contents = this.files.get(uri);
    if (contents === undefined) throw new Error("missing");
    return contents;
  }

  async writeAsStringAsync(uri: string, contents: string): Promise<void> {
    this.writes += 1;
    this.files.set(uri, contents);
  }

  async deleteAsync(uri: string): Promise<void> {
    this.files.delete(uri);
  }

  async moveAsync({ from, to }: { from: string; to: string }): Promise<void> {
    const contents = this.files.get(from);
    if (contents === undefined) throw new Error("missing move source");
    this.moves += 1;
    this.files.set(to, contents);
    this.files.delete(from);
  }
}

const uri = "memory://photo-preferences.json";
const fileSystem = new MemoryFileSystem();
const first = automaticLabel(0);

const writer = await openPhotoPreferenceLabelStore(fileSystem, uri);
await writer.append([first]);
assert(fileSystem.writes === 1 && fileSystem.moves === 1, "append must checkpoint through temp + rename");
assert(!fileSystem.files.has(`${uri}.tmp`), "successful rename must consume the temporary file");

const reader = await openPhotoPreferenceLabelStore(fileSystem, uri);
const roundTripped = reader.snapshot();
// Vacuity guard: a parser that always returns [] must not pass this check.
assert(roundTripped.length === 1, "a non-empty written record must reload");
assert(
  JSON.stringify(roundTripped[0]) === JSON.stringify(first),
  "all ids, features, selector version, group context, and timestamp must round-trip",
);

const serialized = serializePhotoPreferenceLabels(roundTripped);
assert(!serialized.includes("file://"), "records must never contain a local URI");
assert(!serialized.includes(".jpg"), "records must never contain a filename");
assert(parsePhotoPreferenceLabels(serialized)?.length === 1, "serialized checkpoint must parse");

await appendAlbumEditPreference(writer, {
  albumId: first.albumId,
  slotAssetId: rawAssetId(0, "winner"),
  rejectedAssetId: rawAssetId(0, "winner"),
  chosenAssetId: rawAssetId(0, "loser"),
  decisionSurface: "swap-sheet",
  capturedAt: 1_700_000_100_000,
});
const withEdit = await openPhotoPreferenceLabelStore(fileSystem, uri);
const edit = withEdit.snapshot().find((record) => record.type === "album_edit_pairwise");
assert(edit !== undefined, "an actual replacement must append a pairwise label");
assert(
  edit.rejected.assetId === first.group.winnerAssetId &&
    edit.chosen.assetId === first.group.candidates[1].assetId,
  "the edit label must preserve rejected/chosen direction",
);
assert(
  edit.rejected.features.qualityScore === 0.91 && edit.chosen.features.qualityScore === 0.72,
  "the edit label must copy decision-time features instead of requiring a rerun",
);

// A corrupt newest checkpoint is a plausible interrupted write; durable data wins.
fileSystem.files.set(`${uri}.tmp`, "{truncated");
const recovered = await openPhotoPreferenceLabelStore(fileSystem, uri);
assert(recovered.snapshot().length === 2, "corrupt temp checkpoint must fall back to the durable file");
fileSystem.files.delete(`${uri}.tmp`);

const boundedInput = Array.from({ length: 8 }, (_, index) => automaticLabel(index + 1));
assert(boundedInput.length > 3, "vacuity guard must actually cross the test bound");
await recovered.append(boundedInput);
const boundedReload = await openPhotoPreferenceLabelStore(fileSystem, uri, {
  maxRecords: 3,
  maxBytes: 1_000_000,
});
// Opening does not rewrite an existing larger checkpoint; append under the
// bound, then reload the compacted durable checkpoint.
await boundedReload.append([automaticLabel(99)]);
const compacted = await openPhotoPreferenceLabelStore(fileSystem, uri, {
  maxRecords: 3,
  maxBytes: 1_000_000,
});
assert(compacted.snapshot().length === 3, "the configured record bound must hold after reload");
assert(
  compacted.snapshot().at(-1)?.eventId === "near-event-99",
  "compaction must retain the newest complete event",
);

const productionOverflow = Array.from(
  { length: MAX_PREFERENCE_LABEL_RECORDS + 1 },
  (_, index) => automaticLabel(index + 1_000),
);
assert(
  productionOverflow.length > MAX_PREFERENCE_LABEL_RECORDS,
  "vacuity guard must cross the production count bound",
);
assert(
  parsePhotoPreferenceLabels(serializePhotoPreferenceLabels(productionOverflow))?.length ===
    MAX_PREFERENCE_LABEL_RECORDS,
  "the production checkpoint must enforce its count bound",
);

const byteBoundInput = Array.from({ length: 12 }, (_, index) => {
  const record = automaticLabel(index + 5_000);
  return {
    ...record,
    group: {
      ...record.group,
      candidates: record.group.candidates.map((entry) => ({
        ...entry,
        features: {
          ...entry.features,
          groupingEmbedding: Array.from({ length: 512 }, (_, value) => value / 512),
        },
      })),
    },
  } satisfies NearDuplicateRankingLabel;
});
const byteBoundCheckpoint = serializePhotoPreferenceLabels(byteBoundInput, {
  maxRecords: 100,
  maxBytes: 20_000,
});
const byteBoundRecords = parsePhotoPreferenceLabels(byteBoundCheckpoint) ?? [];
assert(
  byteBoundRecords.length > 0 && byteBoundRecords.length < byteBoundInput.length,
  "vacuity guard must force byte-bound compaction while retaining useful records",
);
assert(
  byteBoundCheckpoint.length <= 20_000,
  "the byte bound must hold for this ASCII-only checkpoint",
);

console.log("preference-label-store self-check passed");

function automaticLabel(index: number): NearDuplicateRankingLabel {
  const winnerAssetId = preferenceAssetId(rawAssetId(index, "winner"));
  const loserAssetId = preferenceAssetId(rawAssetId(index, "loser"));
  return {
    eventId: `near-event-${index}`,
    type: "near_duplicate_ranking",
    capturedAt: 1_700_000_000_000 + index,
    albumId: `album-${Math.floor(index / 2)}`,
    selector: {
      name: PHOTO_SELECTOR_NAME,
      configVersion: PHOTO_SELECTOR_CONFIG_VERSION,
      featureSchemaVersion: PHOTO_FEATURE_SCHEMA_VERSION,
    },
    group: {
      groupId: `near-duplicate-${index}`,
      winnerAssetId,
      candidates: [
        candidate(winnerAssetId, 0.91, index * 2),
        candidate(loserAssetId, 0.72, index * 2 + 1),
      ],
      blinkGateEnabled: false,
      blinkRejectedAssetIds: [],
      cutFaceRejectedAssetIds: [],
    },
  };
}

function rawAssetId(index: number, result: "winner" | "loser"): string {
  return `file:///private/family/asset-${index}-${result}.jpg`;
}

function candidate(assetId: string, qualityScore: number, tieBreakInputIndex: number) {
  return {
    assetId,
    features: {
      qualityScore,
      qualityBand: Math.round(qualityScore / 0.02),
      smileTieRank: 0,
      sourcePixelCount: 12_000_000,
      tieBreakInputIndex,
      sharpness: qualityScore,
      cutFace: false,
      category: "scene" as const,
      groupingEmbedding: [qualityScore, 1 - qualityScore],
      groupingEmbeddingSpace: "phone-perceptual-v1",
    },
  };
}
