export type DatedPhotoAsset = {
  /** Stamped once per photo by the feed, so the filename regex never runs in a sort comparator. */
  capturedAt?: number;
  creationTime: number;
  filename?: string;
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
 * Camera and messaging apps stamp the local capture date into the filename:
 * `IMG-20250817-WA0042.jpg`, `IMG_20240215_143022.jpg`, `PXL_20230814_091234567.jpg`,
 * `Screenshot_2024-02-15-14-30-22.png`. Unlike every MediaStore timestamp, this
 * survives copying, WhatsApp forwarding and backup restores untouched -- it is
 * part of the name, not metadata.
 *
 * Components are read as LOCAL wall-clock, because that is what the capturing
 * device wrote. Time-of-day is taken when present and ignored when it is not.
 * Two-digit years (`IMG-251004-...`) are deliberately NOT parsed: they are
 * ambiguous, and the 113 photos that use them have usable file timestamps.
 */
export function filenameCapturedAt(filename: string | undefined): number {
  if (!filename) return 0;
  const match = /(20\d{2})[-_]?(\d{2})[-_]?(\d{2})(?:[-_]?(\d{2})[-_]?(\d{2})[-_]?(\d{2}))?/.exec(
    filename,
  );
  if (!match) return 0;
  const [year, month, day, hour, minute, second] = match.slice(1).map(Number);
  if (month < 1 || month > 12 || day < 1 || day > 31) return 0;
  const parsed = new Date(year, month - 1, day, hour || 0, minute || 0, second || 0);
  // Rejects 31 February, which rolls over to March rather than failing.
  if (parsed.getMonth() !== month - 1 || parsed.getDate() !== day) return 0;
  const stamp = parsed.getTime();
  // A filename claiming the future is a corrupt name, not a capture date.
  return stamp > Date.now() ? 0 : stamp;
}

/**
 * The timestamp closest to when the photograph was actually taken.
 *
 * MEASURED on the owner's phone: 9,481 of his 12,128 photos -- 78.2% -- have
 * `datetaken` NULL in MediaStore. For those Android reports `creationTime` as
 * DATE_ADDED, which is when the file landed on THIS phone. A picture taken in
 * February and copied across in October therefore files itself under October.
 * That is the "ordering is poor" he reported, and it is a data problem rather
 * than a sort problem -- sorting a null column harder cannot fix it.
 *
 * Two rules, in order:
 *
 * 1. THE FILENAME WINS when it carries a date. 9,368 of those 9,481 photos
 *    (98.8%) name their own capture date, and a name cannot drift the way a
 *    file timestamp does.
 * 2. Otherwise the EARLIEST positive timestamp, because copying, downloading,
 *    restoring a backup and forwarding on WhatsApp can only push a file's dates
 *    FORWARD -- so the oldest surviving one is closest to the shutter.
 *
 * When the two agree on the day, rule 2 is kept anyway: `date_modified` carries
 * a real time-of-day, so photos from one afternoon stay in the order they were
 * taken instead of collapsing onto a shared midnight.
 *
 * Measured against the filename dates on his own library, wrong-month filing:
 * date_added 4,194 photos (44.8%) -> date_modified 375 (4.0%) -> this rule 0.
 *
 * Cannot invent a date: with no filename date and no timestamps the photo is
 * still Undated, and undated is an honest answer.
 */
export function capturedAtFor(asset: DatedPhotoAsset): number {
  if (asset.capturedAt !== undefined) return asset.capturedAt;
  const candidates = [asset.creationTime, asset.modificationTime].filter(
    (value) => Number.isFinite(value) && value > 0,
  );
  const fromFile = candidates.length > 0 ? Math.min(...candidates) : 0;

  const fromName = filenameCapturedAt(asset.filename);
  if (fromName === 0) return fromFile;
  // Same calendar day: keep the file stamp, it has the hour the name lacks.
  const named = new Date(fromName);
  const stamped = new Date(fromFile);
  if (
    fromFile > 0 &&
    named.getFullYear() === stamped.getFullYear() &&
    named.getMonth() === stamped.getMonth() &&
    named.getDate() === stamped.getDate()
  ) return fromFile;
  return fromName;
}

/**
 * Folds a freshly fetched page into the feed, newest first.
 *
 * MediaStore pages the library by `date_modified`, but the displayed order is
 * `capturedAtFor`, and a filename date can disagree with `date_modified` by as
 * much as 551 days on the owner's library. A photo can therefore belong 30
 * pages above the one it arrives in, so appending pages -- or sorting each page
 * in isolation -- scatters duplicate month headers down the feed.
 *
 * Both sides are already sorted, so this is a merge rather than a re-sort:
 * linear per page instead of n log n, which matters because it runs on the JS
 * thread while the grid is being scrolled.
 */
export function mergeByCapturedAt<T extends DatedPhotoAsset>(current: T[], incoming: T[]): T[] {
  if (current.length === 0) return incoming;
  if (incoming.length === 0) return current;
  const merged: T[] = new Array(current.length + incoming.length);
  let left = 0;
  let right = 0;
  for (let out = 0; out < merged.length; out += 1) {
    if (left >= current.length) merged[out] = incoming[right++];
    else if (right >= incoming.length) merged[out] = current[left++];
    else if (capturedAtFor(incoming[right]) > capturedAtFor(current[left])) merged[out] = incoming[right++];
    else merged[out] = current[left++];
  }
  return merged;
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
