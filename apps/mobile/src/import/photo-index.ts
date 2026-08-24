import * as FileSystem from "expo-file-system/legacy";
import * as Location from "expo-location";
import * as MediaLibrary from "expo-media-library/legacy";

const INDEX_VERSION = 2;
const INDEX_FILENAME = "photo-location-date-index.json";
const PAGE_SIZE = 200;
const INFO_BATCH_SIZE = 20;
const COORDINATE_DECIMALS = 2;
const UNKNOWN_CITY = "Unknown place";
const UNKNOWN_COUNTRY = "Unknown country";

const MONTH_NAMES = [
  "January",
  "February",
  "March",
  "April",
  "May",
  "June",
  "July",
  "August",
  "September",
  "October",
  "November",
  "December",
] as const;

type AssetIndexEntry = {
  monthId: string | null;
  cityId: string | null;
  countryId: string | null;
  seenInScan: number;
};

type GroupEntry = {
  name: string;
  assetIds: string[];
};

type MonthEntry = {
  label: string;
  assetIds: string[];
};

// Resolved place names for a rounded coordinate cell. Cached per cell so the
// geocoder is called once per ~1km area regardless of how many photos share it.
type GeoNames = { cityId: string; cityName: string; countryId: string; countryName: string };

type PersistedIndex = {
  version: typeof INDEX_VERSION;
  assets: Record<string, AssetIndexEntry>;
  cities: Record<string, GroupEntry>;
  countries: Record<string, GroupEntry>;
  months: Record<string, MonthEntry>;
  geocodeCache: Record<string, GeoNames>;
  cursor: string | null;
  scanGeneration: number;
  scanComplete: boolean;
  total: number;
};

export type PlaceSummary = { id: string; name: string; count: number };
export type CitySummary = PlaceSummary;
export type CountrySummary = PlaceSummary;
export type MonthSummary = { id: string; label: string; count: number };
export type PhotoIndexStatus = { indexed: number; total: number };
export type BuildIndexOptions = {
  onProgress?: (done: number, total: number) => void;
};

function emptyIndex(): PersistedIndex {
  return {
    version: INDEX_VERSION,
    assets: {},
    cities: {},
    countries: {},
    months: {},
    geocodeCache: {},
    cursor: null,
    scanGeneration: 1,
    scanComplete: false,
    total: 0,
  };
}

let index = emptyIndex();
let activeBuild: Promise<void> | null = null;
let hydration: Promise<void> | null = null;

function indexUri(): string | null {
  return FileSystem.documentDirectory
    ? `${FileSystem.documentDirectory}${INDEX_FILENAME}`
    : null;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function parseIndex(contents: string): PersistedIndex | null {
  try {
    const parsed: unknown = JSON.parse(contents);
    if (
      !isRecord(parsed) ||
      parsed.version !== INDEX_VERSION ||
      !isRecord(parsed.assets) ||
      !isRecord(parsed.cities) ||
      !isRecord(parsed.countries) ||
      !isRecord(parsed.months) ||
      !isRecord(parsed.geocodeCache) ||
      typeof parsed.scanGeneration !== "number" ||
      typeof parsed.scanComplete !== "boolean" ||
      typeof parsed.total !== "number"
    ) {
      return null;
    }
    return parsed as PersistedIndex;
  } catch {
    return null;
  }
}

async function readPersistedIndex(uri: string): Promise<PersistedIndex | null> {
  try {
    const contents = await FileSystem.readAsStringAsync(uri);
    return parseIndex(contents);
  } catch {
    return null;
  }
}

async function hydrateIndex(): Promise<void> {
  const uri = indexUri();
  if (!uri) {
    return;
  }

  // A valid temporary file is newer than the primary checkpoint and remains
  // only if the app stopped during replacement.
  const temporary = await readPersistedIndex(`${uri}.tmp`);
  if (temporary) {
    index = temporary;
    return;
  }

  const saved = await readPersistedIndex(uri);
  if (saved) {
    index = saved;
  }
}

/** Hydrates the in-memory query index from its last durable checkpoint. */
export function loadIndex(): Promise<void> {
  hydration ??= hydrateIndex();
  return hydration;
}

async function persistIndex(): Promise<void> {
  const uri = indexUri();
  if (!uri) {
    return;
  }

  const temporaryUri = `${uri}.tmp`;
  try {
    await FileSystem.writeAsStringAsync(temporaryUri, JSON.stringify(index));
    await FileSystem.deleteAsync(uri, { idempotent: true });
    await FileSystem.moveAsync({ from: temporaryUri, to: uri });
  } catch {
    // Indexing remains useful in memory. A later checkpoint can retry the write.
  }
}

function monthForAsset(asset: MediaLibrary.Asset): {
  id: string;
  label: string;
} | null {
  const timestamp = asset.creationTime || asset.modificationTime;
  if (!Number.isFinite(timestamp) || timestamp <= 0) {
    return null;
  }

  const date = new Date(timestamp);
  if (Number.isNaN(date.getTime())) {
    return null;
  }

  const year = date.getFullYear();
  const monthIndex = date.getMonth();
  return {
    id: `${year}-${String(monthIndex + 1).padStart(2, "0")}`,
    label: `${MONTH_NAMES[monthIndex]} ${year}`,
  };
}

function coordinateCell(location: {
  latitude: number;
  longitude: number;
}): { id: string; latitude: number; longitude: number } | null {
  if (
    !Number.isFinite(location.latitude) ||
    !Number.isFinite(location.longitude) ||
    Math.abs(location.latitude) > 90 ||
    Math.abs(location.longitude) > 180
  ) {
    return null;
  }

  const factor = 10 ** COORDINATE_DECIMALS;
  const latitude = Math.round(location.latitude * factor) / factor || 0;
  const longitude = Math.round(location.longitude * factor) / factor || 0;
  return {
    id: `geo:${latitude.toFixed(COORDINATE_DECIMALS)},${longitude.toFixed(COORDINATE_DECIMALS)}`,
    latitude,
    longitude,
  };
}

function slug(value: string): string {
  return value.trim().toLowerCase().replace(/\s+/g, "-");
}

// Resolve a geocoded address into a city-tier name and a country-tier name.
// City prefers city → subregion → region; country prefers country → isoCode.
function namesFromAddress(
  address: Awaited<ReturnType<typeof Location.reverseGeocodeAsync>>[number],
): GeoNames {
  const cityName =
    address.city?.trim() ||
    address.subregion?.trim() ||
    address.region?.trim() ||
    UNKNOWN_CITY;
  const countryName =
    address.country?.trim() || address.isoCountryCode?.trim() || UNKNOWN_COUNTRY;
  return {
    cityName,
    cityId: cityName === UNKNOWN_CITY ? "" : `city:${slug(cityName)}`,
    countryName,
    countryId: countryName === UNKNOWN_COUNTRY ? "" : `country:${slug(countryName)}`,
  };
}

const UNKNOWN_NAMES: GeoNames = {
  cityId: "",
  cityName: UNKNOWN_CITY,
  countryId: "",
  countryName: UNKNOWN_COUNTRY,
};

async function geocodeCell(cell: {
  id: string;
  latitude: number;
  longitude: number;
}): Promise<void> {
  if (Object.hasOwn(index.geocodeCache, cell.id)) {
    return;
  }

  let names = UNKNOWN_NAMES;
  try {
    const addresses = await Location.reverseGeocodeAsync({
      latitude: cell.latitude,
      longitude: cell.longitude,
    });
    if (addresses[0]) {
      names = namesFromAddress(addresses[0]);
    }
  } catch {
    // Offline or unavailable geocoding still records the cell (as unknown).
  }
  index.geocodeCache[cell.id] = names;
}

function addToGroup(
  groups: Record<string, GroupEntry>,
  id: string,
  name: string,
  assetId: string,
): void {
  const entry = groups[id] ?? { name, assetIds: [] };
  if (!entry.assetIds.includes(assetId)) {
    entry.assetIds.push(assetId);
  }
  // Keep the first non-unknown name we saw for the group.
  if (entry.name.startsWith("Unknown") && !name.startsWith("Unknown")) {
    entry.name = name;
  }
  groups[id] = entry;
}

function addAssetToIndex(
  asset: MediaLibrary.Asset,
  month: ReturnType<typeof monthForAsset>,
  names: GeoNames | null,
): void {
  const cityId = names?.cityId || null;
  const countryId = names?.countryId || null;

  index.assets[asset.id] = {
    monthId: month?.id ?? null,
    cityId,
    countryId,
    seenInScan: index.scanGeneration,
  };

  if (month) {
    const entry = index.months[month.id] ?? { label: month.label, assetIds: [] };
    if (!entry.assetIds.includes(asset.id)) {
      entry.assetIds.push(asset.id);
    }
    index.months[month.id] = entry;
  }

  if (cityId && names) addToGroup(index.cities, cityId, names.cityName, asset.id);
  if (countryId && names)
    addToGroup(index.countries, countryId, names.countryName, asset.id);
}

async function processBatch(assets: MediaLibrary.Asset[]): Promise<void> {
  const unresolved = assets.filter((asset) => !index.assets[asset.id]);
  const resolved = await Promise.all(
    unresolved.map(async (asset) => {
      try {
        const info = await MediaLibrary.getAssetInfoAsync(asset, {
          shouldDownloadFromNetwork: false,
        });
        const cell = info.location ? coordinateCell(info.location) : null;
        return { asset, cell };
      } catch {
        return { asset, cell: null };
      }
    }),
  );

  const cells = new Map(
    resolved
      .filter((item) => item.cell !== null)
      .map((item) => [item.cell!.id, item.cell!] as const),
  );
  await Promise.all(Array.from(cells.values(), geocodeCell));

  for (const { asset, cell } of resolved) {
    const names = cell ? index.geocodeCache[cell.id] ?? null : null;
    addAssetToIndex(asset, monthForAsset(asset), names);
  }

  for (const asset of assets) {
    const entry = index.assets[asset.id];
    if (entry) {
      entry.seenInScan = index.scanGeneration;
    }
  }
}

function seenCount(): number {
  return Object.values(index.assets).reduce(
    (count, asset) =>
      count + (asset.seenInScan === index.scanGeneration ? 1 : 0),
    0,
  );
}

function rebuildGroupsAfterCompletedScan(): void {
  const currentAssets = Object.entries(index.assets).filter(
    ([, asset]) => asset.seenInScan === index.scanGeneration,
  );
  index.assets = Object.fromEntries(currentAssets);

  const cities: Record<string, GroupEntry> = {};
  const countries: Record<string, GroupEntry> = {};
  const months: Record<string, MonthEntry> = {};
  for (const [assetId, asset] of currentAssets) {
    if (asset.cityId) {
      addToGroup(cities, asset.cityId, index.cities[asset.cityId]?.name ?? UNKNOWN_CITY, assetId);
    }
    if (asset.countryId) {
      addToGroup(
        countries,
        asset.countryId,
        index.countries[asset.countryId]?.name ?? UNKNOWN_COUNTRY,
        assetId,
      );
    }
    if (asset.monthId) {
      const [year, month] = asset.monthId.split("-");
      const monthIndex = Number(month) - 1;
      const monthEntry = months[asset.monthId] ?? {
        label: `${MONTH_NAMES[monthIndex]} ${year}`,
        assetIds: [],
      };
      monthEntry.assetIds.push(assetId);
      months[asset.monthId] = monthEntry;
    }
  }
  index.cities = cities;
  index.countries = countries;
  index.months = months;
}

function yieldToEventLoop(): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, 0));
}

async function runBuild(opts: BuildIndexOptions): Promise<void> {
  try {
    await loadIndex();

    if (index.scanComplete) {
      index.scanGeneration += 1;
      index.scanComplete = false;
      index.cursor = null;
      await persistIndex();
    }

    let hasNextPage = true;
    let after = index.cursor ?? undefined;
    opts.onProgress?.(seenCount(), index.total);

    while (hasNextPage) {
      let page: MediaLibrary.PagedInfo<MediaLibrary.Asset>;
      try {
        page = await MediaLibrary.getAssetsAsync({
          first: PAGE_SIZE,
          after,
          mediaType: [MediaLibrary.MediaType.photo],
          sortBy: [MediaLibrary.SortBy.creationTime],
        });
      } catch {
        await persistIndex();
        return;
      }

      index.total = page.totalCount;
      for (let start = 0; start < page.assets.length; start += INFO_BATCH_SIZE) {
        const batch = page.assets.slice(start, start + INFO_BATCH_SIZE);
        await processBatch(batch);
        await persistIndex();
        opts.onProgress?.(seenCount(), index.total);
        await yieldToEventLoop();
      }

      after = page.endCursor;
      index.cursor = after;
      hasNextPage = page.hasNextPage;
      await persistIndex();

      if (page.assets.length === 0 && hasNextPage) {
        return;
      }
    }

    rebuildGroupsAfterCompletedScan();
    index.cursor = null;
    index.scanComplete = true;
    index.total = Object.keys(index.assets).length;
    await persistIndex();
    opts.onProgress?.(index.total, index.total);
  } catch {
    await persistIndex();
  }
}

/**
 * Indexes every photo without rejecting for individual asset, geocoder, paging,
 * or persistence failures. Concurrent callers share the same background scan.
 */
export function buildIndex(opts: BuildIndexOptions = {}): Promise<void> {
  if (activeBuild) {
    return activeBuild;
  }

  activeBuild = runBuild(opts).finally(() => {
    activeBuild = null;
  });
  return activeBuild;
}

function summaries(groups: Record<string, GroupEntry>): PlaceSummary[] {
  return Object.entries(groups)
    .map(([id, group]) => ({ id, name: group.name, count: group.assetIds.length }))
    .sort(
      (a, b) =>
        b.count - a.count || a.name.localeCompare(b.name) || a.id.localeCompare(b.id),
    );
}

export function getCities(): CitySummary[] {
  return summaries(index.cities);
}

export function assetIdsForCity(cityId: string): string[] {
  return index.cities[cityId]?.assetIds.slice() ?? [];
}

export function getCountries(): CountrySummary[] {
  return summaries(index.countries);
}

export function assetIdsForCountry(countryId: string): string[] {
  return index.countries[countryId]?.assetIds.slice() ?? [];
}

export function getMonths(): MonthSummary[] {
  return Object.entries(index.months)
    .map(([id, month]) => ({
      id,
      label: month.label,
      count: month.assetIds.length,
    }))
    .sort((a, b) => b.id.localeCompare(a.id));
}

export function assetIdsForMonth(id: string): string[] {
  return index.months[id]?.assetIds.slice() ?? [];
}

export function indexStatus(): PhotoIndexStatus {
  return { indexed: Object.keys(index.assets).length, total: index.total };
}
