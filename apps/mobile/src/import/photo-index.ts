import type { Asset, PagedInfo } from "expo-media-library/legacy";

// @ts-expect-error TypeScript bundler resolution normally omits source extensions.
import { incrementalScanTarget } from "./incremental-index.ts";
// @ts-expect-error TypeScript bundler resolution normally omits source extensions.
import { CITY_MAX_KM, loadPlaceIndex, nearestPlace, type NearestPlace } from "./offline-geocode.ts";

// Expo's native modules are loaded on demand. Their published TypeScript cannot
// be type-stripped by Node, so importing them statically would put this whole
// module out of reach of its self-check; a missing module also degrades to a
// no-op instead of throwing.
type FileSystemModule = typeof import("expo-file-system/legacy");
type MediaLibraryModule = typeof import("expo-media-library/legacy");

let fileSystemModule: FileSystemModule | null = null;
let mediaLibraryModule: MediaLibraryModule | null = null;

async function fileSystem(): Promise<FileSystemModule | null> {
  try {
    fileSystemModule ??= await import("expo-file-system/legacy");
  } catch {
    return null;
  }
  return fileSystemModule;
}

async function mediaLibrary(): Promise<MediaLibraryModule | null> {
  try {
    mediaLibraryModule ??= await import("expo-media-library/legacy");
  } catch {
    return null;
  }
  return mediaLibraryModule;
}

// Version 4 regroups places from city to DISTRICT and adds the state tier.
// Version 3 keyed buckets by nearest city, which scattered one neighbourhood
// across sibling towns -- photos around Noida split between "Noida" and
// "Greater Noida" -- so its groups cannot be reinterpreted and must be rebuilt.
// (Version 3 itself discarded every index written before ACCESS_MEDIA_LOCATION
// reached the manifest, when Android redacted the GPS EXIF of every asset.)
const INDEX_VERSION = 4;
const INDEX_FILENAME = "photo-location-date-index.json";
const PAGE_SIZE = 200;
const INFO_BATCH_SIZE = 20;
const CHECKPOINT_ASSETS = 500;
const CHECKPOINT_INTERVAL_MS = 10_000;
const COORDINATE_DECIMALS = 2;
const UNKNOWN_CITY = "Unknown place";
const UNKNOWN_PLACE = "Unknown region";
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
  stateId: string | null;
  countryId: string | null;
  /** Coordinate cell this asset resolved from, so a later scan can re-label it. */
  cellId?: string | null;
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
// `provisional` marks a coordinate-only label written because the geocoder was
// unavailable: it still groups photos, and a later scan retries it for a name.
type GeoNames = {
  cityId: string;
  /** Nearest city for this cell — its vote for what the district is called. */
  cityLabel?: string;
  stateId?: string;
  stateName?: string;
  cityName: string;
  countryId: string;
  countryName: string;
  provisional?: boolean;
};

type PersistedIndex = {
  version: typeof INDEX_VERSION;
  assets: Record<string, AssetIndexEntry>;
  cities: Record<string, GroupEntry>;
  states: Record<string, GroupEntry>;
  countries: Record<string, GroupEntry>;
  /** Place -> state and state -> country, so the UI can nest the three tiers. */
  cityState: Record<string, string>;
  stateCountry: Record<string, string>;
  /** place id -> city name -> photo count, the ballot for each district's label. */
  placeLabelVotes: Record<string, Record<string, number>>;
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
    states: {},
    countries: {},
    cityState: {},
    stateCountry: {},
    placeLabelVotes: {},
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
let progressSubscribers = new Set<(done: number, total: number) => void>();
let activeScanControl: { cancelled: boolean; foreground: boolean } | null = null;
const membershipSets = new WeakMap<
  object,
  Map<string, Set<string>>
>();

async function indexUri(): Promise<string | null> {
  const files = await fileSystem();
  return files?.documentDirectory
    ? `${files.documentDirectory}${INDEX_FILENAME}`
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
      !isRecord(parsed.states) ||
      !isRecord(parsed.countries) ||
      !isRecord(parsed.cityState) ||
      !isRecord(parsed.stateCountry) ||
      !isRecord(parsed.placeLabelVotes) ||
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
    const files = await fileSystem();
    if (!files) return null;
    const contents = await files.readAsStringAsync(uri);
    return parseIndex(contents);
  } catch {
    return null;
  }
}

async function hydrateIndex(): Promise<void> {
  const uri = await indexUri();
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
  const files = await fileSystem();
  const uri = await indexUri();
  if (!files || !uri) {
    return;
  }

  const temporaryUri = `${uri}.tmp`;
  try {
    await files.writeAsStringAsync(temporaryUri, JSON.stringify(index));
    await files.deleteAsync(uri, { idempotent: true });
    await files.moveAsync({ from: temporaryUri, to: uri });
  } catch {
    // Indexing remains useful in memory. A later checkpoint can retry the write.
  }
}

let assetsSinceCheckpoint = 0;
let lastCheckpointAt = 0;

/**
 * Serialising the whole index is O(library) work on the JS thread, so it is
 * paid on a size or time budget rather than once per small batch.
 */
export function shouldCheckpoint(
  assetsSince: number,
  millisecondsSince: number,
): boolean {
  return (
    assetsSince >= CHECKPOINT_ASSETS || millisecondsSince >= CHECKPOINT_INTERVAL_MS
  );
}

async function checkpointIndex(force: boolean): Promise<void> {
  if (!force && !shouldCheckpoint(assetsSinceCheckpoint, Date.now() - lastCheckpointAt)) {
    return;
  }
  assetsSinceCheckpoint = 0;
  lastCheckpointAt = Date.now();
  await persistIndex();
}

function monthForAsset(asset: Asset): {
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

export function coordinateCell(location: {
  latitude: number;
  longitude: number;
}): { id: string; latitude: number; longitude: number } | null {
  if (
    !Number.isFinite(location.latitude) ||
    !Number.isFinite(location.longitude) ||
    Math.abs(location.latitude) > 90 ||
    Math.abs(location.longitude) > 180 ||
    // Null Island. A GPS EXIF tag written empty parses as exactly 0,0, which is
    // 600km of open Atlantic; a whole camera roll of them would otherwise share
    // one invented place called "Near 0.0°N, 0.0°E". No GPS is the honest
    // reading, and it is what a missing tag already gets.
    (location.latitude === 0 && location.longitude === 0)
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

// A photo with GPS always earns a place. When the geocoder cannot name the
// cell, it is labelled by its own coordinates, rounded to ~11km so that nearby
// photos still land in one bucket. Provisional: a later scan retries the name.
const FALLBACK_DECIMALS = 1;

export function coordinatePlaceNames(cell: {
  latitude: number;
  longitude: number;
}): GeoNames {
  const latitude = Number(cell.latitude.toFixed(FALLBACK_DECIMALS));
  const longitude = Number(cell.longitude.toFixed(FALLBACK_DECIMALS));
  const label =
    `${Math.abs(latitude).toFixed(FALLBACK_DECIMALS)}°${latitude < 0 ? "S" : "N"}, ` +
    `${Math.abs(longitude).toFixed(FALLBACK_DECIMALS)}°${longitude < 0 ? "W" : "E"}`;
  return {
    cityId: `city:geo:${latitude.toFixed(FALLBACK_DECIMALS)},${longitude.toFixed(FALLBACK_DECIMALS)}`,
    cityName: `Near ${label}`,
    countryId: "",
    countryName: UNKNOWN_COUNTRY,
    provisional: true,
  };
}

/**
 * Names a cell no bundled place could name.
 *
 * Only a cell that failed for want of the bundled list is provisional. A cell
 * that is genuinely hundreds of kilometres from any city is SETTLED: the list
 * does not change between runs, so asking again next launch can only give the
 * same answer. Marking it provisional put an unanswerable cell in the cache
 * that the launch probe below would pick first and stall on, leaving every
 * cell that really could be named stuck on coordinates forever.
 */
export function unnamedPlaceNames(
  cell: { latitude: number; longitude: number },
  geocoderAvailable: boolean,
): GeoNames {
  const names = coordinatePlaceNames(cell);
  return geocoderAvailable ? { ...names, provisional: undefined } : names;
}

/** A cell is geocoded when it is unknown, or when its label is coordinates. */
export function needsGeocode(cached: GeoNames | undefined): boolean {
  return !cached || cached.provisional === true;
}

/**
 * Names a cell from the bundled city list.
 *
 * The network geocoder this replaced could never have worked and should never
 * have been reached for: expo-location throws LocationUnauthorizedException
 * without foreground location permission (LocationModule.kt:803) — which this
 * app has no other reason to request — and Android's Geocoder is a network
 * service (:807), so naming places through it would have sent every photo's
 * coordinates off the phone. The bundled list needs no permission, no network
 * and no timeouts, and it cannot half-answer.
 */
export function namesFromNearestPlace(
  place: NearestPlace,
  cell: { latitude: number; longitude: number },
): GeoNames {
  const country = place.countryName.trim() || UNKNOWN_COUNTRY;
  const countryNames: GeoNames = {
    cityId: "",
    cityName: UNKNOWN_CITY,
    countryId: country === UNKNOWN_COUNTRY ? "" : `country:${slug(country)}`,
    countryName: country,
  };
  // Past the city radius the nearest city is not where the photo was taken, so
  // the country is all that can honestly be claimed. Coordinates still label the
  // bucket so those photos group together rather than vanishing.
  if (place.distanceKm > CITY_MAX_KM) {
    if (!countryNames.countryId) return coordinatePlaceNames(cell);
    // Only the country is copied over the coordinate label. Spreading the whole
    // of countryNames would put its empty cityId back on top, and every photo
    // more than an hour from a city — a trek, a safari, a long drive — would
    // drop out of Places entirely instead of grouping by where it was taken.
    return {
      ...coordinatePlaceNames(cell),
      countryId: countryNames.countryId,
      countryName: countryNames.countryName,
      provisional: undefined,
    };
  }
  // The bucket is the DISTRICT, not the nearest city: nearest-city bucketing
  // scattered one neighbourhood across sibling towns, splitting photos taken
  // around Noida between "Noida" and "Greater Noida". The id comes from the
  // district code rather than the label, so two districts that happen to share
  // a best-known city name stay distinct.
  const placeName = place.placeName.trim();
  if (!placeName || !place.placeId) return countryNames;
  const stateName = place.stateName.trim();
  return {
    ...countryNames,
    cityId: `city:${place.placeId}`,
    cityName: placeName,
    ...(place.cityName.trim() ? { cityLabel: place.cityName.trim() } : {}),
    ...(stateName && place.stateId
      ? { stateId: `state:${place.stateId}`, stateName }
      : {}),
  };
}

async function geocodeCell(cell: {
  id: string;
  latitude: number;
  longitude: number;
}): Promise<void> {
  if (!needsGeocode(index.geocodeCache[cell.id])) {
    return;
  }

  const places = await loadPlaceIndex();
  const nearest = places
    ? nearestPlace(places, cell.latitude, cell.longitude)
    : undefined;

  index.geocodeCache[cell.id] = nearest
    ? namesFromNearestPlace(nearest, cell)
    : unnamedPlaceNames(cell, places !== null);
}

/** Recovers the coordinates a cell id was built from. */
export function cellCoordinates(
  cellId: string,
): { id: string; latitude: number; longitude: number } | null {
  const match = /^geo:(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)$/.exec(cellId);
  if (!match) {
    return null;
  }
  return { id: cellId, latitude: Number(match[1]), longitude: Number(match[2]) };
}

/**
 * Drops the coordinate-only labels a previous scan wrote while the geocoder was
 * unavailable, along with the assets that carry them, so the next pass resolves
 * real names. Returns whether anything was dropped.
 */
function dropProvisionalGeocodes(): boolean {
  const provisional = new Set(
    Object.entries(index.geocodeCache)
      .filter(([, names]) => names.provisional === true)
      .map(([cellId]) => cellId),
  );
  if (provisional.size === 0) {
    return false;
  }
  for (const cellId of provisional) {
    delete index.geocodeCache[cellId];
  }
  for (const [assetId, entry] of Object.entries(index.assets)) {
    if (entry.cellId && provisional.has(entry.cellId)) {
      delete index.assets[assetId];
    }
  }
  return true;
}

function addToGroup(
  groups: Record<string, GroupEntry>,
  id: string,
  name: string,
  assetId: string,
): void {
  const entry = groups[id] ?? { name, assetIds: [] };
  if (addToAssetIds(groups, id, entry.assetIds, assetId)) {
    entry.assetIds.push(assetId);
  }
  // Keep the first non-unknown name we saw for the group.
  if (entry.name.startsWith("Unknown") && !name.startsWith("Unknown")) {
    entry.name = name;
  }
  groups[id] = entry;
}

function addToAssetIds(
  owner: object,
  id: string,
  existing: string[],
  assetId: string,
): boolean {
  let byGroup = membershipSets.get(owner);
  if (!byGroup) {
    byGroup = new Map();
    membershipSets.set(owner, byGroup);
  }
  let members = byGroup.get(id);
  if (!members) {
    members = new Set(existing);
    byGroup.set(id, members);
  }
  if (members.has(assetId)) return false;
  members.add(assetId);
  return true;
}

function addAssetToIndex(
  asset: Asset,
  month: ReturnType<typeof monthForAsset>,
  names: GeoNames | null,
  cellId: string | null,
): void {
  const cityId = names?.cityId || null;
  const stateId = names?.stateId || null;
  const countryId = names?.countryId || null;

  index.assets[asset.id] = {
    monthId: month?.id ?? null,
    cityId,
    stateId,
    countryId,
    cellId,
    seenInScan: index.scanGeneration,
  };

  if (month) {
    const entry = index.months[month.id] ?? { label: month.label, assetIds: [] };
    if (addToAssetIds(index.months, month.id, entry.assetIds, asset.id)) {
      entry.assetIds.push(asset.id);
    }
    index.months[month.id] = entry;
  }

  if (cityId && names) addToGroup(index.cities, cityId, names.cityName, asset.id);
  if (stateId && names?.stateName)
    addToGroup(index.states, stateId, names.stateName, asset.id);
  if (countryId && names)
    addToGroup(index.countries, countryId, names.countryName, asset.id);

  // One vote per photo for what this district should be called. Census
  // population picks the wrong name for exactly the places an album is about
  // — Manali (pop ~8k) loses to Kulu, Rishikesh to Dehradun — so the owner's
  // own library decides instead.
  if (cityId && names?.cityLabel) {
    const ballot = (index.placeLabelVotes[cityId] ??= {});
    ballot[names.cityLabel] = (ballot[names.cityLabel] ?? 0) + 1;
  }

  // Parent links, recorded as places are discovered so the UI can nest the
  // three tiers without re-deriving them from coordinates.
  if (cityId && stateId) index.cityState[cityId] = stateId;
  if (stateId && countryId) index.stateCountry[stateId] = countryId;
}

async function processBatch(
  media: MediaLibraryModule,
  assets: Asset[],
): Promise<void> {
  const unresolved = assets.filter((asset) => !index.assets[asset.id]);
  // Each getAssetInfoAsync opens the original file and parses its EXIF on a
  // native coroutine; INFO_BATCH_SIZE bounds how many run at once so the scan
  // never queues more native work than one yield of the JS thread can absorb.
  const resolved = await Promise.all(
    unresolved.map(async (asset) => {
      try {
        const info = await media.getAssetInfoAsync(asset, {
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
    addAssetToIndex(asset, monthForAsset(asset), names, cell?.id ?? null);
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
  const states: Record<string, GroupEntry> = {};
  const countries: Record<string, GroupEntry> = {};
  const months: Record<string, MonthEntry> = {};
  for (const [assetId, asset] of currentAssets) {
    if (asset.cityId) {
      addToGroup(cities, asset.cityId, index.cities[asset.cityId]?.name ?? UNKNOWN_CITY, assetId);
    }
    if (asset.stateId) {
      addToGroup(states, asset.stateId, index.states[asset.stateId]?.name ?? UNKNOWN_PLACE, assetId);
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
  index.states = states;
  index.countries = countries;
  index.months = months;

  // Parent links only stay meaningful for tiers that survived the rebuild.
  index.cityState = Object.fromEntries(
    Object.entries(index.cityState).filter(([city, state]) => cities[city] && states[state]),
  );
  index.stateCountry = Object.fromEntries(
    Object.entries(index.stateCountry).filter(([state, country]) => states[state] && countries[country]),
  );
  // So do the ballots: a place the owner deleted every photo of must not keep
  // voting on a label, and its ballot must not sit in the checkpoint forever.
  index.placeLabelVotes = Object.fromEntries(
    Object.entries(index.placeLabelVotes).filter(([city]) => cities[city]),
  );
}

function yieldToEventLoop(): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, 0));
}

function notifyProgress(done: number, total: number): void {
  for (const subscriber of progressSubscribers) {
    try {
      subscriber(done, total);
    } catch {
      // A screen callback cannot interrupt the shared scan.
    }
  }
}

async function watchAppState(
  control: { cancelled: boolean; foreground: boolean },
): Promise<() => void> {
  try {
    const { AppState } = await import("react-native");
    control.foreground = AppState.currentState === "active";
    const subscription = AppState.addEventListener("change", (state) => {
      control.foreground = state === "active";
    });
    return () => subscription.remove();
  } catch {
    control.foreground = true;
    return () => undefined;
  }
}

async function waitForForeground(
  control: { cancelled: boolean; foreground: boolean },
): Promise<boolean> {
  while (!control.cancelled && !control.foreground) {
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  return !control.cancelled;
}

async function runBuild(
  control: { cancelled: boolean; foreground: boolean },
): Promise<void> {
  const stopWatching = await watchAppState(control);
  assetsSinceCheckpoint = 0;
  lastCheckpointAt = Date.now();
  try {
    await loadIndex();
    if (!(await waitForForeground(control))) {
      await persistIndex();
      return;
    }

    const media = await mediaLibrary();
    if (!media) {
      return;
    }

    // A scan that can read the bundled place list re-resolves whatever an
    // earlier scan could only label with coordinates. One probe decides it, so a
    // library that is genuinely far from any city is not re-scanned every launch.
    if (await loadPlaceIndex()) {
      const stale = Object.keys(index.geocodeCache).find(
        (cellId) => index.geocodeCache[cellId].provisional === true,
      );
      const probe = stale ? cellCoordinates(stale) : null;
      if (probe) {
        delete index.geocodeCache[probe.id];
        await geocodeCell(probe);
        if (
          index.geocodeCache[probe.id]?.provisional !== true &&
          dropProvisionalGeocodes()
        ) {
          index.scanComplete = false;
          index.cursor = null;
        }
      }
    }

    let incrementalTarget: number | null = null;
    if (index.scanComplete) {
      let head: PagedInfo<Asset>;
      try {
        head = await media.getAssetsAsync({
          first: PAGE_SIZE,
          mediaType: [media.MediaType.photo],
          sortBy: [media.SortBy.creationTime],
        });
      } catch {
        return;
      }
      const indexed = Object.keys(index.assets).length;
      incrementalTarget = incrementalScanTarget(
        head.totalCount,
        indexed,
        head.assets.map((asset) => asset.id),
        (assetId) => Object.hasOwn(index.assets, assetId),
      );
      index.total = head.totalCount;
      if (incrementalTarget === 0) {
        return;
      }
      index.scanComplete = false;
      index.cursor = null;
      await checkpointIndex(true);
    }

    let hasNextPage = true;
    let after = index.cursor ?? undefined;
    let newlyIndexed = 0;
    let targetReached = false;
    notifyProgress(seenCount(), index.total);

    while (hasNextPage && !targetReached && !control.cancelled) {
      // Pauses in the background and gives up the moment an album build asks
      // for the phone back, instead of racing it for the JS thread.
      if (!(await waitForForeground(control))) {
        break;
      }

      let page: PagedInfo<Asset>;
      try {
        page = await media.getAssetsAsync({
          first: PAGE_SIZE,
          after,
          mediaType: [media.MediaType.photo],
          sortBy: [media.SortBy.creationTime],
        });
      } catch {
        await checkpointIndex(true);
        return;
      }

      index.total = page.totalCount;
      for (let start = 0; start < page.assets.length; start += INFO_BATCH_SIZE) {
        const batch = page.assets.slice(start, start + INFO_BATCH_SIZE);
        newlyIndexed += batch.filter(
          (asset) => !Object.hasOwn(index.assets, asset.id),
        ).length;
        await processBatch(media, batch);
        assetsSinceCheckpoint += batch.length;
        await checkpointIndex(false);
        notifyProgress(Object.keys(index.assets).length, index.total);
        await yieldToEventLoop();
        if (
          control.cancelled ||
          (incrementalTarget !== null && newlyIndexed >= incrementalTarget)
        ) {
          targetReached = true;
          break;
        }
      }

      after = page.endCursor;
      index.cursor = after;
      hasNextPage = page.hasNextPage;
      await checkpointIndex(false);

      if (page.assets.length === 0 && hasNextPage) {
        return;
      }
    }

    if (control.cancelled) {
      await checkpointIndex(true);
      return;
    }

    rebuildGroupsAfterCompletedScan();
    index.cursor = null;
    index.scanComplete = true;
    await checkpointIndex(true);
  } catch {
    await checkpointIndex(true);
  } finally {
    stopWatching();
    // Every exit reports final counts: a screen that subscribed mid-scan must
    // never be left showing a scanning state the scan already left behind.
    notifyProgress(Object.keys(index.assets).length, index.total);
  }
}

/**
 * Indexes every photo without rejecting for individual asset, geocoder, paging,
 * or persistence failures. Concurrent callers share the same background scan.
 */
export function buildIndex(opts: BuildIndexOptions = {}): Promise<void> {
  if (opts.onProgress) {
    // Subscribe first, then report what is already known: a caller that arrives
    // after the launch scan started still sees progress and the final result.
    progressSubscribers.add(opts.onProgress);
    try {
      opts.onProgress(Object.keys(index.assets).length, index.total);
    } catch {
      // A screen callback cannot interrupt the shared scan.
    }
  }
  if (activeBuild) {
    return activeBuild;
  }

  const control = { cancelled: false, foreground: true };
  activeScanControl = control;
  activeBuild = runBuild(control).finally(() => {
    activeBuild = null;
    activeScanControl = null;
    progressSubscribers.clear();
  });
  return activeBuild;
}

/** Stops the active location/date scan after its current batch is settled. */
export function stopIndexBuild(): void {
  if (activeScanControl) activeScanControl.cancelled = true;
}

function summaries(groups: Record<string, GroupEntry>): PlaceSummary[] {
  return Object.entries(groups)
    .map(([id, group]) => ({ id, name: group.name, count: group.assetIds.length }))
    .sort(
      (a, b) =>
        b.count - a.count || a.name.localeCompare(b.name) || a.id.localeCompare(b.id),
    );
}

/**
 * Picks a district's label from the owner's own photos.
 *
 * Exported so the choice is testable without a scan. Ties break on the name
 * that sorts first purely so the label is stable between runs — a place whose
 * name flickers between two equally-photographed towns reads as a bug.
 */
export function labelFromVotes(
  votes: Record<string, number> | undefined,
  fallback: string,
): string {
  if (!votes) return fallback;
  let best = fallback;
  let bestCount = 0;
  for (const [name, count] of Object.entries(votes)) {
    if (count > bestCount || (count === bestCount && name < best)) {
      best = name;
      bestCount = count;
    }
  }
  return bestCount > 0 ? best : fallback;
}

export function getCities(): CitySummary[] {
  return summaries(index.cities).map((place) => ({
    ...place,
    name: labelFromVotes(index.placeLabelVotes[place.id], place.name),
  }));
}

export function assetIdsForCity(cityId: string): string[] {
  return index.cities[cityId]?.assetIds.slice() ?? [];
}

export function getCountries(): CountrySummary[] {
  return summaries(index.countries);
}

/** The middle tier: admin1 regions, e.g. "Uttar Pradesh". */
export function getStates(): PlaceSummary[] {
  return summaries(index.states);
}

export function assetIdsForState(stateId: string): string[] {
  return index.states[stateId]?.assetIds.slice() ?? [];
}

/**
 * Parent links for the Country -> State -> Place hierarchy.
 *
 * Recorded during the scan rather than re-derived, so browsing never has to
 * touch the bundled place list or the coordinates again. Undefined means the
 * place predates the link (or its parent had no name), and the UI must degrade
 * to showing it ungrouped rather than dropping it.
 */
export function stateForCity(cityId: string): string | undefined {
  return index.cityState[cityId];
}

export function countryForState(stateId: string): string | undefined {
  return index.stateCountry[stateId];
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

/**
 * Returns the most specific durable place bucket known for an asset.
 * Unknown locations stay undefined; they must never be folded into day one or
 * into a real city by the coverage planner.
 */
export function placeKeyForAsset(assetId: string): string | undefined {
  const entry = index.assets[assetId];
  return entry?.cityId || entry?.countryId || undefined;
}

/**
 * The `YYYY-MM` bucket an asset was captured in, or undefined when it has no
 * usable time.
 *
 * The index deliberately stores a month rather than a timestamp, and this
 * exposes exactly what is stored rather than inviting a schema change: adding a
 * per-asset capture time would bump INDEX_VERSION, which throws away the whole
 * index and re-scans the library. Month resolution is enough for the thing that
 * needs it -- telling somebody who recurs across the year apart from somebody
 * who was at one event.
 */
export function monthIdForAsset(assetId: string): string | undefined {
  return index.assets[assetId]?.monthId ?? undefined;
}

export function indexStatus(): PhotoIndexStatus {
  return { indexed: Object.keys(index.assets).length, total: index.total };
}
