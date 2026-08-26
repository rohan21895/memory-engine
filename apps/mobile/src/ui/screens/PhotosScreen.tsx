import { FlashList } from "@shopify/flash-list";
import { Image } from "expo-image";
import * as MediaLibrary from "expo-media-library/legacy";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Linking,
  Pressable,
  ScrollView,
  StatusBar,
  StyleSheet,
  Text,
  TextInput,
  useWindowDimensions,
  View,
} from "react-native";

import {
  assetIdsForPerson,
  buildFaceIndex,
  contentUri,
  getPeople,
  isFaceDetectionAvailable,
  loadFaceIndex,
  logFaceIndexDiagnostics,
  type FaceIndexPerson,
} from "../../faces/face-index";
import {
  buildIndex,
  getCities,
  getCountries,
  loadIndex,
  type PlaceSummary,
} from "../../import/photo-index";
import { copy } from "../copy";
import { EmptyState } from "../components/EmptyState";
import { ErrorState } from "../components/ErrorState";
import { HintBanner } from "../components/HintBanner";
import { LoadingState } from "../components/LoadingState";
import { LocationFilterModal } from "../components/LocationFilterModal";
import { assetIdsForPlace, countryForState, getStates, stateForCity } from "../components/place-source";
import { buildPlaceTree, placeParentNames, topPlaces } from "../components/place-tree";
import { fonts } from "../fonts";
import {
  canWidenAccess,
  getPhotoAccess,
  NO_PHOTO_ACCESS,
  requestPhotoAccess,
  type PhotoAccess,
} from "../photo-access";
import { colors, layout, radii, spacing, typeScale } from "../tokens";
import type { NamePersonTarget } from "./NamePersonScreen";

const PAGE_SIZE = 180;
/** One beat after the first tiles land, so the grid wins the race it used to lose. */
const SCAN_START_DELAY_MS = 600;
/** Rail rebuilds are expensive; once a second reads as live and leaves the thread free. */
const RAIL_REFRESH_MS = 1000;
const FILTER_BURST_TARGET = 120;
const FILTER_PAGE_GUARD = 400;
const GRID_COLUMNS = 3;
const GRID_GAP = 3;
const PLACE_RAIL_LIMIT = 12;

type PlaceCard = PlaceSummary & { coverUri: string; parentName?: string };
type PlaceTiers = { cities: PlaceSummary[]; countries: PlaceSummary[]; states: PlaceSummary[] };
type LibraryRow =
  | { key: string; kind: "month"; label: string }
  | { key: string; kind: "photos"; assets: MediaLibrary.Asset[] };

function monthFor(asset: MediaLibrary.Asset): { key: string; label: string } {
  const timestamp = asset.creationTime || asset.modificationTime;
  const date = new Date(timestamp);
  if (!Number.isFinite(timestamp) || timestamp <= 0 || Number.isNaN(date.getTime())) {
    return { key: "undated", label: "Undated" };
  }
  return {
    key: `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}`,
    label: date.toLocaleDateString(undefined, { month: "long", year: "numeric" }),
  };
}

const EMPTY_TIERS: PlaceTiers = { cities: [], countries: [], states: [] };

function toFilterOption(place: PlaceSummary) {
  return { id: place.id, label: place.name, photoCount: place.count };
}

function rowsFor(assets: MediaLibrary.Asset[]): LibraryRow[] {
  const rows: LibraryRow[] = [];
  let activeMonth = "";
  let activePhotos: MediaLibrary.Asset[] = [];

  const flush = () => {
    for (let start = 0; start < activePhotos.length; start += GRID_COLUMNS) {
      const slice = activePhotos.slice(start, start + GRID_COLUMNS);
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
      rows.push({ key: `month:${month.key}`, kind: "month", label: month.label });
    }
    activePhotos.push(asset);
  }
  flush();
  return rows;
}

/**
 * One photo in the grid.
 *
 * An asset can disappear between the MediaStore query and the render — deleted,
 * moved off the phone, or dropped out of a "Select photos" grant — and until now
 * a broken tile was indistinguishable from one still decoding. It draws a quiet
 * panel instead, and nothing else: no filename, no URI, no error text. Whatever
 * a failed asset carries is not something to paint over the grid.
 */
function PhotoTile({ assetId, size }: { assetId: string; size: number }) {
  const [failed, setFailed] = useState(false);
  const box = { height: size, width: size };
  if (failed) {
    return <View accessibilityLabel="Photo unavailable" accessibilityRole="image" style={[styles.tileMissing, box]} />;
  }
  return (
    <Image
      cachePolicy="memory-disk"
      contentFit="cover"
      onError={() => setFailed(true)}
      recyclingKey={assetId}
      source={contentUri(assetId)}
      style={[styles.tile, box]}
    />
  );
}

export function PhotosScreen({ onNamePerson }: { onNamePerson?: (person: NamePersonTarget) => void }) {
  const { width } = useWindowDimensions();
  const tileSize = Math.floor((width - GRID_GAP * (GRID_COLUMNS - 1)) / GRID_COLUMNS);
  const [query, setQuery] = useState("");
  const [people, setPeople] = useState<FaceIndexPerson[]>([]);
  const [places, setPlaces] = useState<PlaceCard[]>([]);
  const [placeTiers, setPlaceTiers] = useState<PlaceTiers>(EMPTY_TIERS);
  const [placesModalVisible, setPlacesModalVisible] = useState(false);
  const [assets, setAssets] = useState<MediaLibrary.Asset[]>([]);
  const [selectedPerson, setSelectedPerson] = useState<string | null>(null);
  const [selectedPlace, setSelectedPlace] = useState<string | null>(null);
  const [status, setStatus] = useState<"loading" | "denied" | "ready" | "error">("loading");
  const [reloading, setReloading] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [scanningPeople, setScanningPeople] = useState(false);
  // Real counts, not a spinner: "Finding people…" with no numbers is exactly the
  // "nothing is happening" impression the beta complained about.
  const [faceScan, setFaceScan] = useState<{ done: number; total: number } | null>(null);
  const [placeScan, setPlaceScan] = useState<{ done: number; total: number } | null>(null);
  const [access, setAccess] = useState<PhotoAccess>(NO_PHOTO_ACCESS);
  const [accessDismissed, setAccessDismissed] = useState(false);
  const [indexesReady, setIndexesReady] = useState(false);
  const cursor = useRef<string | undefined>(undefined);
  const hasNextPage = useRef(true);
  const loadingPage = useRef(false);
  const scansStarted = useRef(false);

  const refreshIndexes = useCallback(() => {
    const nextPeople = getPeople();
    const tiers: PlaceTiers = { cities: getCities(), countries: getCountries(), states: getStates() };
    // One tier only in the strip. A country tile ("India, 2124 photos") sitting
    // beside a city tile it contains ("Gurugram, 990 photos") reads as two peers
    // of the same kind; the full hierarchy behind "See all places" is where the
    // broader tiers belong.
    const strip = topPlaces({ countries: tiers.countries, places: tiers.cities, states: tiers.states }, PLACE_RAIL_LIMIT);
    const parents = placeParentNames(
      buildPlaceTree(
        { countries: tiers.countries, places: tiers.cities, states: tiers.states },
        { countryForState, stateForPlace: stateForCity },
      ),
    );
    const nextPlaces: PlaceCard[] = strip.items.map((place) => {
      const ids = assetIdsForPlace(place.id);
      return {
        id: place.id,
        name: place.name,
        count: place.count,
        coverUri: ids[0] ? contentUri(ids[0]) : "",
        parentName: parents.get(place.id),
      };
    });
    setPeople(nextPeople);
    setPlaceTiers(tiers);
    setPlaces(nextPlaces);
  }, []);

  // Rebuilding the rails walks every person and every place tier, and the scan
  // reports progress once per batch. Doing the full rebuild on each tick meant
  // the scan spent most of the JS thread redrawing two rails nobody was looking
  // at yet, while the grid waited its turn. A rail that updates once a second is
  // indistinguishable to a reader and leaves the thread free to paint.
  const lastRailRefresh = useRef(0);
  const railTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const refreshRailsSoon = useCallback(() => {
    const since = Date.now() - lastRailRefresh.current;
    if (since >= RAIL_REFRESH_MS) {
      lastRailRefresh.current = Date.now();
      refreshIndexes();
      return;
    }
    // A trailing call is already queued; it will pick up the newest index.
    if (railTimer.current) return;
    railTimer.current = setTimeout(() => {
      railTimer.current = null;
      lastRailRefresh.current = Date.now();
      refreshIndexes();
    }, RAIL_REFRESH_MS - since);
  }, [refreshIndexes]);

  useEffect(() => () => {
    if (railTimer.current) clearTimeout(railTimer.current);
  }, []);

  const startScans = useCallback(() => {
    void buildIndex({
      onProgress: (done, total) => {
        setPlaceScan({ done, total });
        refreshRailsSoon();
      },
    }).then(refreshIndexes).catch(() => undefined).finally(() => setPlaceScan(null));
    void buildFaceIndex({
      onProgress: (done, total) => {
        setScanningPeople(true);
        setFaceScan({ done, total });
        setPeople(getPeople());
      },
    })
      .then(() => setPeople(getPeople()))
      .catch(() => undefined)
      .finally(() => {
        setScanningPeople(false);
        setFaceScan(null);
      });
  }, [refreshIndexes, refreshRailsSoon]);

  useEffect(() => {
    let active = true;
    void (async () => {
      try {
        const current = await getPhotoAccess();
        if (!active) return;
        setAccess(current);
        // "Select photos" reports granted with limited privileges. Show what we
        // can see rather than a permission wall, and say so in the banner.
        if (!current.readable) {
          setStatus("denied");
          return;
        }
        // The grid needs MediaLibrary and nothing else, so it must not wait on
        // the indexes. Hydrating the face index means reading every persisted
        // observation and re-clustering them — minutes on a real library — and
        // gating `ready` on that is what made the Photos tab sit on "Loading
        // your photos…" long after the photos themselves were readable.
        setReloading(true);
        setStatus("ready");

        // The rails (People, Places) fill in behind the grid as each index
        // lands, and the scans only start once there is an index to add to.
        void Promise.all([loadIndex(), loadFaceIndex()]).then(() => {
          if (!active) return;
          logFaceIndexDiagnostics("photos hydrated");
          refreshIndexes();
          setIndexesReady(true);
        });
      } catch {
        if (active) setStatus("error");
      }
    })();
    return () => {
      active = false;
    };
  }, [refreshIndexes, startScans]);

  const filterSet = useMemo(() => {
    if (selectedPerson) return new Set(assetIdsForPerson(selectedPerson));
    // Any tier is selectable, so resolve country/state/place through one door.
    // Reads the live index rather than a snapshot so this stays keyed on the
    // selection alone and a scan tick never re-pages the grid.
    if (selectedPlace) return new Set(assetIdsForPlace(selectedPlace));
    return null;
  }, [selectedPerson, selectedPlace]);
  const filterSetRef = useRef<Set<string> | null>(null);
  filterSetRef.current = filterSet;

  const fetchBurst = useCallback(async () => {
    const matching: MediaLibrary.Asset[] = [];
    const activeFilter = filterSetRef.current;
    let guard = 0;
    while (
      hasNextPage.current &&
      matching.length < (activeFilter ? FILTER_BURST_TARGET : 1) &&
      guard < FILTER_PAGE_GUARD
    ) {
      guard += 1;
      const page = await MediaLibrary.getAssetsAsync({
        after: cursor.current,
        first: PAGE_SIZE,
        mediaType: [MediaLibrary.MediaType.photo],
        sortBy: [MediaLibrary.SortBy.creationTime],
      });
      cursor.current = page.endCursor;
      hasNextPage.current = page.hasNextPage;
      matching.push(...(activeFilter ? page.assets.filter((asset) => activeFilter.has(asset.id)) : page.assets));
      if (page.assets.length === 0) break;
    }
    return matching;
  }, []);

  const reload = useCallback(async () => {
    cursor.current = undefined;
    hasNextPage.current = true;
    loadingPage.current = true;
    setReloading(true);
    setAssets([]);
    try {
      setAssets(await fetchBurst());
    } catch {
      setStatus("error");
    } finally {
      loadingPage.current = false;
      setReloading(false);
    }
  }, [fetchBurst]);

  const loadMore = useCallback(async () => {
    if (loadingPage.current || !hasNextPage.current) return;
    loadingPage.current = true;
    setLoadingMore(true);
    try {
      const next = await fetchBurst();
      if (next.length > 0) setAssets((current) => current.concat(next));
    } catch {
      setStatus("error");
    } finally {
      loadingPage.current = false;
      setLoadingMore(false);
    }
  }, [fetchBurst]);

  useEffect(() => {
    if (status === "ready") void reload();
  }, [filterSet, reload, status]);

  // The scan is the heaviest thing this app does — decoding frames, running the
  // detector and the embedder — and it runs on the same JS thread that paints
  // the grid. Starting it at mount meant the first tiles queued behind it. Wait
  // until real photos are on screen, then give the UI one more beat to settle.
  useEffect(() => {
    if (scansStarted.current || status !== "ready" || !indexesReady || assets.length === 0) return;
    scansStarted.current = true;
    const timer = setTimeout(startScans, SCAN_START_DELAY_MS);
    return () => clearTimeout(timer);
  }, [assets.length, indexesReady, startScans, status]);

  const widenAccess = useCallback(() => {
    void (async () => {
      // Once Android stops offering the prompt, Settings is the only way through.
      if (!canWidenAccess(access)) {
        void Linking.openSettings();
        return;
      }
      const next = await requestPhotoAccess();
      setAccess(next);
      if (!next.readable) return;
      setStatus("ready");
      // A wider grant exposes photos no index has ever seen: re-page and re-scan.
      startScans();
      await reload();
    })();
  }, [access, reload, startScans]);

  const scanForPeople = () => {
    if (scanningPeople || !isFaceDetectionAvailable()) return;
    setScanningPeople(true);
    startScans();
  };

  const needle = query.trim().toLocaleLowerCase();
  const peopleLabels = useMemo(
    () => new Map(people.map((person, index) => [person.id, copy.filters.personName(index)])),
    [people],
  );
  const visiblePeople = people.filter((person) =>
    (peopleLabels.get(person.id) ?? "").toLocaleLowerCase().includes(needle),
  );
  const visiblePlaces = places.filter((place) => place.name.toLocaleLowerCase().includes(needle));
  const placesTotal = placeTiers.cities.length + placeTiers.countries.length + placeTiers.states.length;
  const selectedPlaceName = useMemo(() => {
    if (!selectedPlace) return null;
    for (const tier of [placeTiers.cities, placeTiers.states, placeTiers.countries]) {
      for (const place of tier) if (place.id === selectedPlace) return place.name;
    }
    return null;
  }, [placeTiers, selectedPlace]);
  // A country or state chosen in the hierarchy has no tile in the strip, so the
  // "See all places" entry carries the current selection instead of it vanishing.
  const selectedOffStrip = Boolean(selectedPlace) && !places.some((place) => place.id === selectedPlace);
  const filterCountries = useMemo(() => placeTiers.countries.map(toFilterOption), [placeTiers.countries]);
  const filterStates = useMemo(() => placeTiers.states.map(toFilterOption), [placeTiers.states]);
  const filterCities = useMemo(() => placeTiers.cities.map(toFilterOption), [placeTiers.cities]);
  const rows = useMemo(() => rowsFor(assets), [assets]);
  const searching = needle.length > 0;
  const showAccessBanner = access.limited && !accessDismissed;
  const peopleStatus = scanningPeople
    ? faceScan && faceScan.total > 0
      ? copy.states.scanningFaces(faceScan.done, faceScan.total)
      : "Grouping faces on this phone…"
    : null;
  const placeStatus = placeScan && placeScan.total > 0
    ? copy.states.scanningPhotos(placeScan.done, placeScan.total)
    : null;

  const header = (
    <View style={styles.header}>
      <Text accessibilityRole="header" style={styles.title}>Photos</Text>
      <View style={styles.search}>
        <Text accessibilityElementsHidden importantForAccessibility="no-hide-descendants" style={styles.searchIcon}>⌕</Text>
        <TextInput
          accessibilityLabel="Search people or places"
          autoCapitalize="none"
          autoCorrect={false}
          onChangeText={setQuery}
          placeholder="Search people or places"
          placeholderTextColor={colors.muted}
          returnKeyType="search"
          style={styles.searchInput}
          value={query}
        />
        {query.length > 0 ? (
          <Pressable accessibilityLabel="Clear search" accessibilityRole="button" hitSlop={10} onPress={() => setQuery("")} style={styles.searchClear}>
            <Text style={styles.searchClearText}>×</Text>
          </Pressable>
        ) : null}
      </View>

      {showAccessBanner ? (
        <View style={styles.banner}>
          <HintBanner
            actionHint={canWidenAccess(access) ? copy.access.limitedActionHint : copy.access.settingsActionHint}
            actionLabel={canWidenAccess(access) ? copy.access.limitedAction : copy.access.settingsAction}
            dismissLabel={copy.access.dismiss}
            onAction={widenAccess}
            onDismiss={() => setAccessDismissed(true)}
            text={copy.access.limitedHelper}
            tone="warning"
          />
        </View>
      ) : null}

      <View style={styles.sectionHeading}>
        <Text style={styles.section}>People</Text>
        {peopleStatus ? <Text accessibilityLiveRegion="polite" style={styles.sectionStatus}>{peopleStatus}</Text> : null}
      </View>
      {visiblePeople.length > 0 ? (
        <ScrollView horizontal contentContainerStyle={styles.peopleRow} showsHorizontalScrollIndicator={false} style={styles.rail}>
          {visiblePeople.map((person) => {
            const active = selectedPerson === person.id;
            const label = peopleLabels.get(person.id) ?? "Person";
            const openCorrection = () => onNamePerson?.({
              id: person.id,
              label,
              faceThumbUri: person.faceThumbUri,
              assetIds: assetIdsForPerson(person.id).slice(0, 8),
            });
            return (
              <View key={person.id} style={styles.person}>
                <Pressable accessibilityHint="Tap to filter photos. Hold to add a name." accessibilityLabel={`${label}. ${copy.filters.photoCount(person.assetIds.length)}`} accessibilityRole="button" accessibilityState={{ selected: active }} onLongPress={openCorrection} onPress={() => { setSelectedPlace(null); setSelectedPerson(active ? null : person.id); }} style={styles.personFilter}>
                  <Image cachePolicy="memory-disk" contentFit="cover" source={person.faceThumbUri ?? contentUri(person.coverAssetId)} style={[styles.avatar, active ? styles.avatarActive : null]} />
                  <Text numberOfLines={1} style={[styles.personName, active ? styles.activeText : null]}>{label}</Text>
                </Pressable>
                {active ? <Pressable accessibilityHint="Opens options to merge this tile with another person or keep them separate" accessibilityLabel={`Fix grouping for ${label}`} accessibilityRole="button" onPress={openCorrection} style={styles.personFix}><Text style={styles.personFixText}>Fix</Text></Pressable> : null}
              </View>
            );
          })}
        </ScrollView>
      ) : searching && people.length > 0 ? (
        <Text style={styles.noMatch}>No people match “{query.trim()}”.</Text>
      ) : (
        <View style={styles.noPeople}>
          <Text style={styles.noPeopleTitle}>{scanningPeople ? "Finding people…" : "No people found yet"}</Text>
          <Text style={styles.noPeopleText}>
            {peopleStatus
              ? `${peopleStatus}. Face grouping happens on this phone.`
              : access.limited
                ? copy.access.limitedPeople
                : isFaceDetectionAvailable()
                  ? "Face grouping happens on this phone and may take a few minutes the first time."
                  : "Face grouping isn’t available on this phone."}
          </Text>
          <Pressable accessibilityRole="button" accessibilityState={{ busy: scanningPeople, disabled: scanningPeople || !isFaceDetectionAvailable() }} disabled={scanningPeople || !isFaceDetectionAvailable()} onPress={scanForPeople} style={[styles.peopleScan, scanningPeople || !isFaceDetectionAvailable() ? styles.scanDisabled : null]}>
            <Text style={styles.peopleScanText}>{scanningPeople ? "Scanning on this phone…" : "Find people on this phone"}</Text>
          </Pressable>
        </View>
      )}

      <View style={styles.sectionHeading}>
        <Text style={styles.section}>Places</Text>
        {placeStatus ? <Text accessibilityLiveRegion="polite" style={styles.sectionStatus}>{placeStatus}</Text> : null}
      </View>
      {placesTotal > 0 ? (
        <View>
          {searching && visiblePlaces.length === 0 ? (
            <Text style={styles.noMatch}>No places match “{query.trim()}”.</Text>
          ) : null}
          <ScrollView horizontal contentContainerStyle={styles.placesRow} showsHorizontalScrollIndicator={false}>
            {visiblePlaces.map((place) => {
              const active = selectedPlace === place.id;
              return (
                <Pressable
                  accessibilityLabel={`${place.name}${place.parentName ? `, ${place.parentName}` : ""}. ${copy.filters.photoCount(place.count)}`}
                  accessibilityRole="button"
                  accessibilityState={{ selected: active }}
                  key={place.id}
                  onPress={() => {
                    setSelectedPerson(null);
                    setSelectedPlace(active ? null : place.id);
                  }}
                  style={styles.place}
                >
                  {place.coverUri ? <Image cachePolicy="memory-disk" contentFit="cover" source={place.coverUri} style={[styles.placeImage, active ? styles.placeActive : null]} /> : <View style={styles.placeImage} />}
                  <Text numberOfLines={1} style={[styles.placeName, active ? styles.activeText : null]}>{place.name}</Text>
                  <Text numberOfLines={1} style={styles.placeCount}>
                    {/* Count first: the tile is 132dp wide, so the parent is
                        what truncates rather than the number. */}
                    {place.parentName
                      ? `${copy.filters.photoCount(place.count)} · ${place.parentName}`
                      : copy.filters.photoCount(place.count)}
                  </Text>
                </Pressable>
              );
            })}
            <Pressable
              accessibilityHint={copy.places.seeAllHint}
              accessibilityLabel={`${copy.places.seeAll}. ${copy.places.total(placesTotal)}`}
              accessibilityRole="button"
              accessibilityState={{ selected: selectedOffStrip }}
              onPress={() => setPlacesModalVisible(true)}
              style={styles.place}
            >
              <View style={[styles.placeImage, styles.seeAllTile, selectedOffStrip ? styles.placeActive : null]}>
                <Text style={styles.seeAllIcon}>⌕</Text>
              </View>
              <Text numberOfLines={1} style={[styles.placeName, selectedOffStrip ? styles.activeText : null]}>{copy.places.seeAll}</Text>
              <Text numberOfLines={1} style={styles.placeCount}>
                {selectedOffStrip && selectedPlaceName
                  ? copy.places.showing(selectedPlaceName)
                  : copy.places.total(placesTotal)}
              </Text>
            </Pressable>
          </ScrollView>
        </View>
      ) : (
        <View style={styles.noPeople}>
          <Text style={styles.noPeopleTitle}>{placeStatus ? "Finding places…" : copy.access.noPlacesTitle}</Text>
          <Text style={styles.noPeopleText}>
            {placeStatus ?? (access.limited ? copy.access.limitedPlaces : copy.access.noPlacesHelper)}
          </Text>
        </View>
      )}
      {selectedPerson || selectedPlace ? (
        <Pressable accessibilityHint="Removes the person or place filter" accessibilityRole="button" onPress={() => { setSelectedPerson(null); setSelectedPlace(null); }} style={styles.clearFilter}>
          <Text style={styles.clearFilterText}>Show all photos</Text>
        </Pressable>
      ) : null}
    </View>
  );

  if (status === "loading") {
    return <LoadingState helper="Your library stays on this phone." title="Loading your photos…" />;
  }
  if (status === "denied") {
    return (
      <ErrorState
        actionHint={canWidenAccess(access) ? copy.access.limitedActionHint : copy.access.settingsActionHint}
        actionLabel={canWidenAccess(access) ? copy.access.limitedAction : copy.access.settingsAction}
        helper="Allow photo access to see your full library here."
        onAction={widenAccess}
        title="Photo access is off"
      />
    );
  }
  if (status === "error") {
    return <ErrorState actionHint="Tries your photo library again" actionLabel="Try again" helper="Your photos are safe. Photeo couldn’t read the library just now." onAction={() => setStatus("ready")} title="Couldn’t load photos" />;
  }

  return (
    <View style={styles.root}>
      <StatusBar backgroundColor={colors.background} barStyle="dark-content" />
      <FlashList
        contentContainerStyle={styles.list}
        contentInsetAdjustmentBehavior="automatic"
        data={rows}
        getItemType={(item) => item.kind}
        keyExtractor={(item) => item.key}
        // LoadingState/EmptyState are flex:1; inside a list content container
        // that resolves to zero height and the screen reads as blank while the
        // first page is still being fetched. The fixed block keeps them visible.
        ListEmptyComponent={(
          <View style={styles.emptyBlock}>
            {reloading
              ? <LoadingState helper="Every photo will appear here." title="Loading your photos…" />
              : (
                <EmptyState
                  helper={filterSet
                    ? "Try showing all photos."
                    : access.limited
                      ? copy.access.limitedShort
                      : "There are no photos in this library yet."}
                  title={filterSet ? "No photos match this filter" : "No photos yet"}
                />
              )}
          </View>
        )}
        ListFooterComponent={loadingMore ? <Text style={styles.loadingMore}>Loading more photos…</Text> : null}
        ListHeaderComponent={header}
        onEndReached={() => void loadMore()}
        onEndReachedThreshold={1.2}
        renderItem={({ item }) => item.kind === "month" ? (
          <Text style={styles.month}>{item.label}</Text>
        ) : (
          <View style={styles.photoRow}>
            {item.assets.map((asset) => (
              <PhotoTile assetId={asset.id} key={asset.id} size={tileSize} />
            ))}
          </View>
        )}
      />
      <LocationFilterModal
        cities={filterCities}
        countries={filterCountries}
        loadingText={placeStatus ?? undefined}
        onClose={() => setPlacesModalVisible(false)}
        onSelect={(locationId) => {
          setSelectedPerson(null);
          setSelectedPlace(locationId);
        }}
        selectedLocationId={selectedPlace}
        states={filterStates}
        visible={placesModalVisible}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  activeText: { color: colors.goldPressed },
  avatar: { backgroundColor: colors.hairline, borderRadius: 33, height: 66, width: 66 },
  avatarActive: { borderColor: colors.gold, borderWidth: 3 },
  banner: { paddingTop: spacing.md },
  clearFilter: { alignItems: "center", alignSelf: "flex-start", backgroundColor: colors.panelRaised, borderRadius: radii.pill, justifyContent: "center", marginTop: spacing.sm, minHeight: layout.minTouchTarget, paddingHorizontal: spacing.md },
  clearFilterText: { color: colors.goldPressed, fontFamily: fonts.bold, ...typeScale.small },
  emptyBlock: { minHeight: 280, paddingVertical: spacing.lg },
  header: { paddingHorizontal: layout.screenPadding, paddingTop: (StatusBar.currentHeight ?? 24) + spacing.md },
  list: { paddingBottom: spacing.xxl },
  loadingMore: { color: colors.muted, fontFamily: fonts.regular, padding: spacing.md, textAlign: "center", ...typeScale.small },
  month: { color: colors.text, fontFamily: fonts.bold, paddingBottom: spacing.xs, paddingHorizontal: layout.screenPadding, paddingTop: spacing.lg, ...typeScale.label },
  noMatch: { color: colors.muted, fontFamily: fonts.regular, paddingTop: spacing.sm, ...typeScale.small },
  noPeople: { backgroundColor: colors.panel, borderColor: colors.hairline, borderCurve: "continuous", borderRadius: radii.lg, borderWidth: 1, gap: spacing.xs, marginTop: spacing.xs, padding: spacing.md },
  noPeopleText: { color: colors.muted, fontFamily: fonts.regular, ...typeScale.small },
  noPeopleTitle: { color: colors.text, fontFamily: fonts.bold, ...typeScale.label },
  peopleRow: { gap: 14, paddingVertical: spacing.xs },
  peopleScan: { alignItems: "center", alignSelf: "flex-start", backgroundColor: colors.panelRaised, borderRadius: radii.pill, justifyContent: "center", marginTop: spacing.xs, minHeight: layout.minTouchTarget, paddingHorizontal: spacing.md },
  peopleScanText: { color: colors.goldPressed, fontFamily: fonts.bold, ...typeScale.small },
  person: { alignItems: "center", gap: 6, width: 66 },
  personFilter: { alignItems: "center", gap: 6, width: 66 },
  personFix: { alignItems: "center", backgroundColor: colors.panel, borderColor: colors.hairline, borderRadius: radii.pill, borderWidth: 1, height: 44, justifyContent: "center", width: 66 },
  personFixText: { color: colors.gold, fontFamily: fonts.semibold, ...typeScale.eyebrow },
  personName: { color: colors.text, fontFamily: fonts.semibold, fontSize: 12.5, width: 66 },
  photoRow: { flexDirection: "row", gap: GRID_GAP },
  place: { width: 132 },
  placeActive: { borderColor: colors.gold, borderWidth: 3 },
  placeCount: { color: colors.muted, fontFamily: fonts.regular, fontSize: 12.5 },
  placeImage: { backgroundColor: colors.quietSurface, borderCurve: "continuous", borderRadius: 14, height: 92, width: 132 },
  placeName: { color: colors.text, fontFamily: fonts.bold, fontSize: 14.5, paddingTop: 7 },
  placesRow: { gap: spacing.sm, paddingVertical: spacing.xs },
  // ScrollView ships `flexGrow: 1, flexShrink: 1` in its own base style, which
  // makes the people rail the one child of this header column that absorbs any
  // height the parent imposes. Pinned to its content, it cannot be squeezed into
  // the heading above it.
  rail: { flexGrow: 0, flexShrink: 0 },
  root: { backgroundColor: colors.background, flex: 1 },
  scanDisabled: { opacity: 0.55 },
  search: { alignItems: "center", backgroundColor: "#f0eee8", borderRadius: radii.pill, flexDirection: "row", gap: spacing.xs, height: 48, marginTop: 14, paddingHorizontal: spacing.md },
  searchClear: { alignItems: "center", height: 32, justifyContent: "center", width: 32 },
  searchClearText: { color: colors.muted, fontFamily: fonts.regular, fontSize: 24, lineHeight: 27 },
  searchIcon: { color: colors.muted, fontFamily: fonts.regular, fontSize: 21 },
  searchInput: { color: colors.text, flex: 1, fontFamily: fonts.regular, fontSize: 16, paddingVertical: 0 },
  section: { color: colors.text, fontFamily: fonts.bold, ...typeScale.label },
  seeAllIcon: { color: colors.goldPressed, fontFamily: fonts.regular, fontSize: 30, lineHeight: 34 },
  seeAllTile: { alignItems: "center", backgroundColor: colors.panelRaised, borderColor: colors.hairline, borderWidth: 1, justifyContent: "center" },
  // No `alignItems: "baseline"` here. The scan status wraps to two lines on a
  // phone ("Grouping faces — 11,532 of 11,805 photos"), and a baseline line with
  // two differently sized children sizes itself from ascent/descent rather than
  // from the tallest child: the row measured shorter than the status, the label
  // was offset down inside it, and the people rail — the next sibling — started
  // high enough to paint its first avatar over the word "People". Cross-start
  // alignment sizes the row to its tallest child, so the rail can never begin
  // above the heading's baseline; wrapping keeps that true at any width.
  sectionHeading: { alignItems: "flex-start", columnGap: spacing.sm, flexDirection: "row", flexWrap: "wrap", justifyContent: "space-between", paddingTop: spacing.lg, rowGap: spacing.xxs },
  sectionStatus: { color: colors.muted, flexGrow: 1, flexShrink: 1, fontFamily: fonts.regular, textAlign: "right", ...typeScale.eyebrow },
  tile: { backgroundColor: colors.hairline },
  tileMissing: { backgroundColor: colors.quietSurface, borderColor: colors.hairline, borderWidth: 1 },
  title: { color: colors.text, fontFamily: fonts.extraBold, ...typeScale.title },
});
