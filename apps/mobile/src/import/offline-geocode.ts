/**
 * Names a coordinate from a city list bundled inside the app.
 *
 * Android's `Geocoder` is a network service and expo-location refuses to call it
 * without foreground location permission (LocationModule.kt:803, :807). Using it
 * would send every photo's coordinates off the phone — the one thing Photeo
 * promises it never does — and would demand a location permission the app has no
 * other use for. A bundled list gives real names with neither, works on a plane,
 * and costs no per-photo latency.
 *
 * Data: GeoNames cities5000 (CC BY 4.0), built by scripts/build-places-index.py.
 */

/**
 * Three tiers: country -> state (admin1) -> place (admin2 district).
 *
 * Photos bucket by DISTRICT, not by city. Nearest-city bucketing scattered one
 * neighbourhood across sibling towns — photos around Noida landed variously on
 * "Noida" and "Greater Noida", 20km apart but the same district — and nobody
 * thinks of those as different places. Districts also collapse ~70k cities into
 * ~31.5k places, which is what makes a browsable hierarchy viable.
 *
 * Per-city names are not shipped: the label always comes from the district.
 */
export type PlaceDataset = {
  v: number;
  /** ISO country codes, parallel to `names`. */
  codes: string[];
  /** Country display names, parallel to `codes`. */
  names: string[];
  /** Stable state ids ("in.36"), parallel to `stateNames`/`stateCc`. */
  stateIds: string[];
  stateNames: string[];
  /** Index into `codes`/`names`, parallel to `stateIds`. */
  stateCc: number[];
  /** Stable district ids ("in.36.141"), parallel to `placeNames`/`placeState`. */
  placeIds: string[];
  /** District label: its best-known city, e.g. "Noida". */
  placeNames: string[];
  /** Index into `stateIds`, parallel to `placeIds`. */
  placeState: number[];
  /** Latitude in integer thousandths of a degree, one entry per city. */
  lat: number[];
  lon: number[];
  /** Index into `codes`/`names`, parallel to `lat`. */
  cc: number[];
  /** Index into `placeIds`, parallel to `lat`. */
  place: number[];
  /**
   * City name, parallel to `lat`. This is a LABEL CANDIDATE, not the bucket:
   * labelling a district by its most populous city reads badly for the places
   * an album is about (Manali became "Kulu"), so the caller picks the label by
   * which city the owner actually photographed most.
   */
  city: string[];
  /** Prominence in tenths of a decade of population, parallel to `lat`. */
  pop: number[];
};

export type NearestPlace = {
  placeId: string;
  /** The district's default label, from census population. */
  placeName: string;
  /** The specific nearest city — the label candidate this photo votes for. */
  cityName: string;
  stateId: string;
  stateName: string;
  countryName: string;
  countryCode: string;
  distanceKm: number;
};

/**
 * Beyond this the nearest place is not where the photo was taken, so naming it
 * would be a confident lie. A photo album would rather say "India" than name a
 * district two hours away.
 */
export const CITY_MAX_KM = 60;
/** Past this even the country is a guess, and the caller falls back to coordinates. */
export const COUNTRY_MAX_KM = 250;

const EARTH_RADIUS_KM = 6371;
const DEGREE_KM = 111.32;
/** Grid cells are one degree square; rings past this cannot beat COUNTRY_MAX_KM. */
const MAX_RING = 3;

/**
 * How much extra distance one order of magnitude of population is worth.
 *
 * Strictly-nearest names a photo taken in Rishikesh "Birbhaddar" — the hamlet
 * whose centroid happens to be 2km nearer than the town everyone would name. A
 * small, bounded bonus fixes that without letting a distant metro win: at 3km
 * per decade even a 10-million city only buys ~21km, so the town you are
 * actually standing in still beats the city an hour away.
 */
const PROMINENCE_KM_PER_DECADE = 3;
/** Populations above this are treated alike; nothing on Earth exceeds ~8 decades. */
const MAX_PROMINENCE_DECADES = 8;

export type PlaceIndex = {
  dataset: PlaceDataset;
  /** Bucket key -> indices into the dataset's parallel arrays. */
  buckets: Map<number, number[]>;
};

function isNumberArray(value: unknown): value is number[] {
  return Array.isArray(value) && value.every((entry) => typeof entry === "number");
}

function isStringArray(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((entry) => typeof entry === "string");
}

/** Fails closed: a truncated or foreign file must not half-load. */
export function parsePlaceDataset(value: unknown): PlaceDataset | null {
  if (typeof value !== "object" || value === null) return null;
  const record = value as Record<string, unknown>;
  if (record.v !== 2) return null;
  if (
    !isStringArray(record.codes) ||
    !isStringArray(record.names) ||
    !isStringArray(record.stateIds) ||
    !isStringArray(record.stateNames) ||
    !isNumberArray(record.stateCc) ||
    !isStringArray(record.placeIds) ||
    !isStringArray(record.placeNames) ||
    !isNumberArray(record.placeState) ||
    !isNumberArray(record.lat) ||
    !isNumberArray(record.lon) ||
    !isNumberArray(record.cc) ||
    !isNumberArray(record.place) ||
    !isStringArray(record.city) ||
    !isNumberArray(record.pop)
  ) {
    return null;
  }
  const count = record.lat.length;
  const places = record.placeIds.length;
  const states = record.stateIds.length;
  if (
    count === 0 ||
    places === 0 ||
    states === 0 ||
    record.lon.length !== count ||
    record.cc.length !== count ||
    record.place.length !== count ||
    record.city.length !== count ||
    record.pop.length !== count ||
    record.placeNames.length !== places ||
    record.placeState.length !== places ||
    record.stateNames.length !== states ||
    record.stateCc.length !== states ||
    record.codes.length !== record.names.length
  ) {
    return null;
  }
  return {
    v: 2,
    codes: record.codes,
    names: record.names,
    stateIds: record.stateIds,
    stateNames: record.stateNames,
    stateCc: record.stateCc,
    placeIds: record.placeIds,
    placeNames: record.placeNames,
    placeState: record.placeState,
    lat: record.lat,
    lon: record.lon,
    cc: record.cc,
    place: record.place,
    city: record.city,
    pop: record.pop,
  };
}

function bucketKey(latitudeDegree: number, longitudeDegree: number): number {
  // Longitude wraps, latitude clamps: a bucket at 181 degrees east is the same
  // ground as one at 179 west, and a scan ring near the pole must not spill into
  // a nonexistent row.
  const wrapped = ((longitudeDegree + 180) % 360 + 360) % 360;
  const clamped = Math.min(179, Math.max(0, latitudeDegree + 90));
  return clamped * 360 + wrapped;
}

export function buildPlaceIndex(dataset: PlaceDataset): PlaceIndex {
  const buckets = new Map<number, number[]>();
  for (let index = 0; index < dataset.lat.length; index += 1) {
    const key = bucketKey(
      Math.floor(dataset.lat[index] / 1000),
      Math.floor(dataset.lon[index] / 1000),
    );
    const bucket = buckets.get(key);
    if (bucket) bucket.push(index);
    else buckets.set(key, [index]);
  }
  return { dataset, buckets };
}

function toRadians(degrees: number): number {
  return (degrees * Math.PI) / 180;
}

export function haversineKm(
  latitudeA: number,
  longitudeA: number,
  latitudeB: number,
  longitudeB: number,
): number {
  const deltaLatitude = toRadians(latitudeB - latitudeA);
  const deltaLongitude = toRadians(longitudeB - longitudeA);
  const a =
    Math.sin(deltaLatitude / 2) ** 2 +
    Math.cos(toRadians(latitudeA)) *
      Math.cos(toRadians(latitudeB)) *
      Math.sin(deltaLongitude / 2) ** 2;
  return 2 * EARTH_RADIUS_KM * Math.asin(Math.min(1, Math.sqrt(a)));
}

/**
 * Nearest city by expanding one-degree rings.
 *
 * Terminates as soon as the best hit is closer than anything the next ring could
 * hold, so a dense continent costs one ring and an empty ocean costs four.
 */
export function nearestPlace(
  index: PlaceIndex,
  latitude: number,
  longitude: number,
): NearestPlace | undefined {
  if (
    !Number.isFinite(latitude) ||
    !Number.isFinite(longitude) ||
    Math.abs(latitude) > 90 ||
    Math.abs(longitude) > 180
  ) {
    return undefined;
  }

  const centreLatitude = Math.floor(latitude);
  const centreLongitude = Math.floor(longitude);
  // A degree of longitude shrinks toward the poles, so the conservative floor on
  // what the next ring could contain uses the narrower of the two.
  const longitudeScale = Math.max(0.05, Math.cos(toRadians(latitude)));
  const ringFloorKm = DEGREE_KM * Math.min(1, longitudeScale);

  const maxProminenceBonusKm = MAX_PROMINENCE_DECADES * PROMINENCE_KM_PER_DECADE;
  let bestIndex = -1;
  let bestScore = Number.POSITIVE_INFINITY;
  let bestKm = Number.POSITIVE_INFINITY;
  // Termination is judged on raw distance, never on the prominence-adjusted
  // score, so the bonus can never cause the search to stop early.
  let nearestKm = Number.POSITIVE_INFINITY;

  for (let ring = 0; ring <= MAX_RING; ring += 1) {
    for (let dLat = -ring; dLat <= ring; dLat += 1) {
      for (let dLon = -ring; dLon <= ring; dLon += 1) {
        // Only the new perimeter: inner cells were scanned by earlier rings.
        if (ring > 0 && Math.abs(dLat) !== ring && Math.abs(dLon) !== ring) continue;
        const bucket = index.buckets.get(
          bucketKey(centreLatitude + dLat, centreLongitude + dLon),
        );
        if (!bucket) continue;
        for (const candidate of bucket) {
          const distance = haversineKm(
            latitude,
            longitude,
            index.dataset.lat[candidate] / 1000,
            index.dataset.lon[candidate] / 1000,
          );
          if (distance < nearestKm) nearestKm = distance;
          // `pop` is tenths of a decade, so /10 recovers decades of population.
          const decades = Math.min(
            MAX_PROMINENCE_DECADES,
            Math.max(0, index.dataset.pop[candidate] / 10),
          );
          const score = distance - decades * PROMINENCE_KM_PER_DECADE;
          if (score < bestScore) {
            bestScore = score;
            bestKm = distance;
            bestIndex = candidate;
          }
        }
      }
    }
    if (
      bestIndex !== -1 &&
      nearestKm + maxProminenceBonusKm <= ring * ringFloorKm
    ) {
      break;
    }
  }

  if (bestIndex === -1 || bestKm > COUNTRY_MAX_KM) return undefined;

  const { dataset } = index;
  const countryIndex = dataset.cc[bestIndex];
  const placeIndex = dataset.place[bestIndex];
  const stateIndex = dataset.placeState[placeIndex] ?? -1;
  return {
    placeId: dataset.placeIds[placeIndex] ?? "",
    placeName: dataset.placeNames[placeIndex] ?? "",
    cityName: dataset.city[bestIndex] ?? "",
    stateId: dataset.stateIds[stateIndex] ?? "",
    stateName: dataset.stateNames[stateIndex] ?? "",
    countryName: dataset.names[countryIndex] ?? "",
    countryCode: dataset.codes[countryIndex] ?? "",
    distanceKm: bestKm,
  };
}

let cachedIndex: PlaceIndex | null = null;
let pendingIndex: Promise<PlaceIndex | null> | null = null;

/**
 * Reads and parses the bundled list once. Returns null rather than throwing so a
 * missing or damaged asset degrades places to coordinate labels instead of
 * failing the whole library scan.
 */
export async function loadPlaceIndex(): Promise<PlaceIndex | null> {
  if (cachedIndex) return cachedIndex;
  pendingIndex ??= (async () => {
    try {
      const { Asset } = await import("expo-asset");
      const fileSystem = await import("expo-file-system/legacy");
      const asset = Asset.fromModule(
        require("../../assets/places/cities.places") as number,
      );
      await asset.downloadAsync();
      const uri = asset.localUri ?? asset.uri;
      if (!uri) return null;
      const dataset = parsePlaceDataset(
        JSON.parse(await fileSystem.readAsStringAsync(uri)) as unknown,
      );
      if (!dataset) return null;
      cachedIndex = buildPlaceIndex(dataset);
      return cachedIndex;
    } catch {
      return null;
    }
  })();
  return pendingIndex;
}
