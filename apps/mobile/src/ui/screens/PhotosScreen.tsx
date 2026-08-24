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
  type FaceIndexPerson,
} from "../../faces/face-index";
import {
  assetIdsForCity,
  assetIdsForCountry,
  buildIndex,
  getCities,
  getCountries,
  loadIndex,
  type PlaceSummary,
} from "../../import/photo-index";
import { EmptyState } from "../components/EmptyState";
import { ErrorState } from "../components/ErrorState";
import { LoadingState } from "../components/LoadingState";
import { fonts } from "../fonts";
import { colors, layout, radii, spacing, typeScale } from "../tokens";
import type { NamePersonTarget } from "./NamePersonScreen";

const PAGE_SIZE = 180;
const FILTER_BURST_TARGET = 120;
const FILTER_PAGE_GUARD = 400;
const GRID_COLUMNS = 3;
const GRID_GAP = 3;

type PlaceCard = PlaceSummary & { coverUri: string };
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

export function PhotosScreen({ onNamePerson }: { onNamePerson?: (person: NamePersonTarget) => void }) {
  const { width } = useWindowDimensions();
  const tileSize = Math.floor((width - GRID_GAP * (GRID_COLUMNS - 1)) / GRID_COLUMNS);
  const [query, setQuery] = useState("");
  const [people, setPeople] = useState<FaceIndexPerson[]>([]);
  const [places, setPlaces] = useState<PlaceCard[]>([]);
  const [assets, setAssets] = useState<MediaLibrary.Asset[]>([]);
  const [selectedPerson, setSelectedPerson] = useState<string | null>(null);
  const [selectedPlace, setSelectedPlace] = useState<string | null>(null);
  const [status, setStatus] = useState<"loading" | "denied" | "ready" | "error">("loading");
  const [reloading, setReloading] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [scanningPeople, setScanningPeople] = useState(false);
  const cursor = useRef<string | undefined>(undefined);
  const hasNextPage = useRef(true);
  const loadingPage = useRef(false);

  const refreshIndexes = useCallback(() => {
    const nextPeople = getPeople();
    const nextPlaces: PlaceCard[] = [...getCities(), ...getCountries()].slice(0, 12).map((place) => {
      const ids = place.id.startsWith("country:") ? assetIdsForCountry(place.id) : assetIdsForCity(place.id);
      return { ...place, coverUri: ids[0] ? contentUri(ids[0]) : "" };
    });
    setPeople(nextPeople);
    setPlaces(nextPlaces);
  }, []);

  useEffect(() => {
    let active = true;
    void (async () => {
      try {
        const permission = await MediaLibrary.getPermissionsAsync();
        if (!active) return;
        if (permission.status !== "granted") {
          setStatus("denied");
          return;
        }
        await Promise.all([loadIndex(), loadFaceIndex()]);
        if (!active) return;
        refreshIndexes();
        setReloading(true);
        setStatus("ready");
        void buildIndex({ onProgress: refreshIndexes }).then(refreshIndexes).catch(() => undefined);
        void buildFaceIndex({
          onProgress: () => {
            setScanningPeople(true);
            setPeople(getPeople());
          },
        }).then(() => setPeople(getPeople())).catch(() => undefined).finally(() => setScanningPeople(false));
      } catch {
        if (active) setStatus("error");
      }
    })();
    return () => {
      active = false;
    };
  }, [refreshIndexes]);

  const filterSet = useMemo(() => {
    if (selectedPerson) return new Set(assetIdsForPerson(selectedPerson));
    if (selectedPlace) {
      return new Set(selectedPlace.startsWith("country:") ? assetIdsForCountry(selectedPlace) : assetIdsForCity(selectedPlace));
    }
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

  const scanForPeople = () => {
    if (scanningPeople || !isFaceDetectionAvailable()) return;
    setScanningPeople(true);
    const refreshPeople = () => setPeople(getPeople());
    void buildFaceIndex({ onProgress: refreshPeople })
      .then(refreshPeople)
      .catch(() => undefined)
      .finally(() => setScanningPeople(false));
  };

  const needle = query.trim().toLocaleLowerCase();
  const visiblePeople = people.filter((person, index) => `person ${index + 1}`.includes(needle));
  const visiblePlaces = places.filter((place) => place.name.toLocaleLowerCase().includes(needle));
  const rows = useMemo(() => rowsFor(assets), [assets]);

  const header = (
    <View style={styles.header}>
      <Text accessibilityRole="header" style={styles.title}>Photos</Text>
      <View style={styles.search}>
        <Text style={styles.searchIcon}>⌕</Text>
        <TextInput onChangeText={setQuery} placeholder="Search people or places" placeholderTextColor="#8b8378" style={styles.searchInput} value={query} />
      </View>

      <View style={styles.sectionHeading}><Text style={styles.section}>People</Text><Text style={styles.seeAll}>See all</Text></View>
      {people.length > 0 ? (
        <ScrollView horizontal contentContainerStyle={styles.peopleRow} showsHorizontalScrollIndicator={false}>
          {visiblePeople.map((person, index) => {
            const active = selectedPerson === person.id;
            return (
              <Pressable
                accessibilityHint="Tap to filter photos. Hold to add a name."
                key={person.id}
                onLongPress={() => onNamePerson?.({
                  id: person.id,
                  label: `Person ${index + 1}`,
                  faceThumbUri: person.faceThumbUri,
                  assetIds: assetIdsForPerson(person.id).slice(0, 8),
                })}
                onPress={() => {
                  setSelectedPlace(null);
                  setSelectedPerson(active ? null : person.id);
                }}
                style={styles.person}
              >
                <Image cachePolicy="memory-disk" contentFit="cover" source={person.faceThumbUri ?? contentUri(person.coverAssetId)} style={[styles.avatar, active ? styles.avatarActive : null]} />
                <Text numberOfLines={1} style={[styles.personName, active ? styles.activeText : null]}>Person {index + 1}</Text>
              </Pressable>
            );
          })}
        </ScrollView>
      ) : (
        <View style={styles.noPeople}>
          <Text style={styles.noPeopleTitle}>{scanningPeople ? "Finding people…" : "No people found yet"}</Text>
          <Text style={styles.noPeopleText}>Face grouping happens on this phone and may take a few minutes the first time.</Text>
          <Pressable accessibilityRole="button" disabled={scanningPeople || !isFaceDetectionAvailable()} onPress={scanForPeople} style={[styles.peopleScan, scanningPeople ? styles.scanDisabled : null]}>
            <Text style={styles.peopleScanText}>{scanningPeople ? "Scanning on this phone…" : "Find people on this phone"}</Text>
          </Pressable>
        </View>
      )}

      <View style={styles.sectionHeading}><Text style={styles.section}>Places</Text><Text style={styles.seeAll}>See all</Text></View>
      <ScrollView horizontal contentContainerStyle={styles.placesRow} showsHorizontalScrollIndicator={false}>
        {visiblePlaces.map((place) => {
          const active = selectedPlace === place.id;
          return (
            <Pressable key={place.id} onPress={() => {
              setSelectedPerson(null);
              setSelectedPlace(active ? null : place.id);
            }} style={styles.place}>
              {place.coverUri ? <Image cachePolicy="memory-disk" contentFit="cover" source={place.coverUri} style={[styles.placeImage, active ? styles.placeActive : null]} /> : <View style={styles.placeImage} />}
              <Text numberOfLines={1} style={[styles.placeName, active ? styles.activeText : null]}>{place.name}</Text>
              <Text style={styles.placeCount}>{place.count} photos</Text>
            </Pressable>
          );
        })}
      </ScrollView>
      {selectedPerson || selectedPlace ? (
        <Pressable accessibilityRole="button" onPress={() => { setSelectedPerson(null); setSelectedPlace(null); }} style={styles.clearFilter}>
          <Text style={styles.clearFilterText}>Show all photos</Text>
        </Pressable>
      ) : null}
    </View>
  );

  if (status === "loading") {
    return <LoadingState helper="Your library stays on this phone." title="Loading your photos…" />;
  }
  if (status === "denied") {
    return <ErrorState actionHint="Opens Photeo settings" actionLabel="Open settings" helper="Allow photo access to see your full library here." onAction={() => void Linking.openSettings()} title="Photo access is off" />;
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
        ListEmptyComponent={reloading ? <LoadingState helper="Every photo will appear here." title="Loading your photos…" /> : <EmptyState helper={filterSet ? "Try showing all photos." : "There are no photos in this library yet."} title={filterSet ? "No photos match this filter" : "No photos yet"} />}
        ListFooterComponent={loadingMore ? <Text style={styles.loadingMore}>Loading more photos…</Text> : null}
        ListHeaderComponent={header}
        onEndReached={() => void loadMore()}
        onEndReachedThreshold={1.2}
        renderItem={({ item }) => item.kind === "month" ? (
          <Text style={styles.month}>{item.label}</Text>
        ) : (
          <View style={styles.photoRow}>
            {item.assets.map((asset) => (
              <Image cachePolicy="memory-disk" contentFit="cover" key={asset.id} recyclingKey={asset.id} source={contentUri(asset.id)} style={{ backgroundColor: colors.hairline, height: tileSize, width: tileSize }} />
            ))}
          </View>
        )}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  activeText: { color: colors.gold },
  avatar: { backgroundColor: colors.hairline, borderRadius: 33, height: 66, width: 66 },
  avatarActive: { borderColor: colors.gold, borderWidth: 3 },
  clearFilter: { alignItems: "center", alignSelf: "flex-start", backgroundColor: colors.panelRaised, borderRadius: 20, justifyContent: "center", marginTop: spacing.sm, minHeight: 40, paddingHorizontal: spacing.md },
  clearFilterText: { color: colors.gold, fontFamily: fonts.bold, ...typeScale.small },
  header: { paddingHorizontal: layout.screenPadding, paddingTop: (StatusBar.currentHeight ?? 24) + spacing.md },
  list: { paddingBottom: spacing.xxl },
  loadingMore: { color: colors.muted, fontFamily: fonts.regular, padding: spacing.md, textAlign: "center", ...typeScale.small },
  month: { color: colors.text, fontFamily: fonts.bold, paddingBottom: spacing.xs, paddingHorizontal: layout.screenPadding, paddingTop: spacing.lg, ...typeScale.label },
  noPeople: { backgroundColor: colors.panel, borderColor: colors.hairline, borderCurve: "continuous", borderRadius: radii.lg, borderWidth: 1, gap: spacing.xs, padding: spacing.md },
  noPeopleText: { color: colors.muted, fontFamily: fonts.regular, ...typeScale.small },
  noPeopleTitle: { color: colors.text, fontFamily: fonts.bold, ...typeScale.label },
  peopleRow: { gap: 14, paddingVertical: spacing.xs },
  peopleScan: { alignItems: "center", alignSelf: "flex-start", backgroundColor: colors.panelRaised, borderRadius: 20, height: 40, justifyContent: "center", marginTop: spacing.xs, paddingHorizontal: spacing.md },
  peopleScanText: { color: colors.gold, fontFamily: fonts.bold, ...typeScale.small },
  person: { alignItems: "center", gap: 6, width: 66 },
  personName: { color: colors.text, fontFamily: fonts.semibold, fontSize: 12.5, width: 66 },
  photoRow: { flexDirection: "row", gap: GRID_GAP },
  place: { width: 132 },
  placeActive: { borderColor: colors.gold, borderWidth: 3 },
  placeCount: { color: colors.muted, fontFamily: fonts.regular, fontSize: 12.5 },
  placeImage: { backgroundColor: colors.quietSurface, borderCurve: "continuous", borderRadius: 14, height: 92, width: 132 },
  placeName: { color: colors.text, fontFamily: fonts.bold, fontSize: 14.5, paddingTop: 7 },
  placesRow: { gap: spacing.sm, paddingVertical: spacing.xs },
  root: { backgroundColor: colors.background, flex: 1 },
  scanDisabled: { opacity: 0.55 },
  search: { alignItems: "center", backgroundColor: "#f0eee8", borderRadius: radii.pill, flexDirection: "row", gap: spacing.xs, height: 48, marginTop: 14, paddingHorizontal: spacing.md },
  searchIcon: { color: "#8b8378", fontFamily: fonts.regular, fontSize: 21 },
  searchInput: { color: colors.text, flex: 1, fontFamily: fonts.regular, fontSize: 16, paddingVertical: 0 },
  section: { color: colors.text, fontFamily: fonts.bold, ...typeScale.label },
  sectionHeading: { alignItems: "baseline", flexDirection: "row", justifyContent: "space-between", paddingTop: spacing.lg },
  seeAll: { color: colors.gold, fontFamily: fonts.semibold, ...typeScale.small },
  title: { color: colors.text, fontFamily: fonts.extraBold, ...typeScale.title },
});
