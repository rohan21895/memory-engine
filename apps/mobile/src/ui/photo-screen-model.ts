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

/**
 * The timestamp closest to when the photograph was actually taken.
 *
 * MEASURED on the owner's phone: 9,481 of his 12,128 photos -- 78.2% -- have
 * `datetaken` NULL in MediaStore. For those Android reports `creationTime` as
 * DATE_ADDED, which is when the file landed on THIS phone. A picture taken in
 * February and copied across in October therefore files itself under October,
 * and three quarters of his library sits under the wrong month. That is the
 * "ordering is poor" he reported, and it is a data problem rather than a sort
 * problem -- sorting a null column harder cannot fix it.
 *
 * The rule is the EARLIEST positive timestamp available, because every copy,
 * download, backup restore and WhatsApp forward can only push a file's dates
 * FORWARD. The oldest surviving date is the one closest to the shutter. On his
 * library `date_modified` survives the transfer where `datetaken` does not:
 * a sampled photo carries datetaken 1740317569053 and date_modified
 * 1740359719000 (twelve hours apart, same day) against date_added
 * 1759262531000 -- seven months later.
 *
 * Not a heuristic that can invent a date: if every timestamp is missing the
 * photo is still Undated, and undated is an honest answer.
 */
export function capturedAtFor(asset: DatedPhotoAsset): number {
  const candidates = [asset.creationTime, asset.modificationTime].filter(
    (value) => Number.isFinite(value) && value > 0,
  );
  return candidates.length > 0 ? Math.min(...candidates) : 0;
}

function monthFor(asset: DatedPhotoAsset): Month {
  const timestamp = capturedAtFor(asset);
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
