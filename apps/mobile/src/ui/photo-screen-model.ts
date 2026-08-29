export type DatedPhotoAsset = {
  creationTime: number;
  id: string;
  modificationTime: number;
};

export type LibraryRow<T extends DatedPhotoAsset> =
  | { key: string; kind: "month"; label: string }
  | { key: string; kind: "photos"; assets: T[] };

export type VisiblePersonProjection = {
  assetIds: readonly string[];
  coverAssetId: string;
  faceCount: number;
  faceThumbUri?: string;
  id: string;
};

type Month = { date?: Date; key: string };

function monthFor(asset: DatedPhotoAsset): Month {
  const timestamp = asset.creationTime || asset.modificationTime;
  const date = new Date(timestamp);
  if (!Number.isFinite(timestamp) || timestamp <= 0 || Number.isNaN(date.getTime())) {
    return { key: "undated" };
  }
  return {
    date,
    key: `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}`,
  };
}

function defaultMonthLabel(date: Date | undefined): string {
  return date
    ? date.toLocaleDateString(undefined, { month: "long", year: "numeric" })
    : "Undated";
}

/**
 * Builds virtualized grid rows without formatting the same month once per photo.
 *
 * MediaLibrary is already sorted by capture time, so a label is needed only
 * when the month changes. On an 11,800-photo library the old implementation
 * performed 11,800 locale-format calls for roughly 33 labels.
 */
export function rowsFor<T extends DatedPhotoAsset>(
  assets: T[],
  columns: number,
  formatMonth: (date: Date | undefined) => string = defaultMonthLabel,
): LibraryRow<T>[] {
  const rows: LibraryRow<T>[] = [];
  let activeMonth = "";
  let activePhotos: T[] = [];

  const flush = () => {
    for (let start = 0; start < activePhotos.length; start += columns) {
      const slice = activePhotos.slice(start, start + columns);
      rows.push({
        key: `photos:${activeMonth}:${slice.map((asset) => asset.id).join(":")}`,
        kind: "photos",
        assets: slice,
      });
    }
    activePhotos = [];
  };

  for (const asset of assets) {
    const month = monthFor(asset);
    if (month.key !== activeMonth) {
      flush();
      activeMonth = month.key;
      rows.push({
        key: `month:${month.key}`,
        kind: "month",
        label: formatMonth(month.date),
      });
    }
    activePhotos.push(asset);
  }
  flush();
  return rows;
}

/** Equality of everything the People rail or an active person filter exposes. */
export function samePeopleProjection(
  current: readonly VisiblePersonProjection[],
  next: readonly VisiblePersonProjection[],
): boolean {
  if (current.length !== next.length) return false;
  for (let index = 0; index < current.length; index += 1) {
    const before = current[index];
    const after = next[index];
    if (
      before.id !== after.id ||
      (before.faceThumbUri ?? before.coverAssetId) !==
        (after.faceThumbUri ?? after.coverAssetId) ||
      before.assetIds.length !== after.assetIds.length
    ) return false;
    let sameAssetOrder = true;
    for (let asset = 0; asset < before.assetIds.length; asset += 1) {
      if (before.assetIds[asset] !== after.assetIds[asset]) {
        sameAssetOrder = false;
        break;
      }
    }
    if (!sameAssetOrder) {
      const afterAssets = new Set(after.assetIds);
      if (before.assetIds.some((assetId) => !afterAssets.has(assetId))) return false;
    }
  }
  return true;
}
