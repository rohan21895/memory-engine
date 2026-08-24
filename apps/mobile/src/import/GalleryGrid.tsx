import { FlashList, type FlashListRef } from "@shopify/flash-list";
import { Image } from "expo-image";
import * as MediaLibrary from "expo-media-library/legacy";
import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Linking,
  Pressable,
  StatusBar,
  StyleSheet,
  Text,
  useWindowDimensions,
  View,
} from "react-native";
import {
  Gesture,
  GestureDetector,
  GestureHandlerRootView,
} from "react-native-gesture-handler";

import {
  assetIdsForPerson,
  buildFaceIndex,
  getPeople,
  isFaceDetectionAvailable,
  loadFaceIndex,
  personIdsForAsset,
  type FaceIndexPerson,
} from "../faces/face-index";
import {
  colors,
  copy,
  EmptyState,
  ErrorState,
  FilterScreen,
  type FilterSelection,
  fonts,
  HintBanner,
  LoadingState,
  PrimaryButton,
  ScreenHeader,
  spacing,
  typeScale,
} from "../ui";
import type { PickedPhoto } from "./picked-photo";
import {
  assetIdsForCity,
  assetIdsForCountry,
  buildIndex,
  getCities,
  getCountries,
  getMonths,
  loadIndex,
  placeKeyForAsset,
  type MonthSummary,
  type PlaceSummary,
} from "./photo-index";

const PAGE = 150;
const COLS = 3;
const GAP = 3;
const SELECT_ALL_CAP = 3000;
const BURST_TARGET = 60; // visible assets to gather per fetch burst
const BURST_PAGE_CAP = 400; // safety: stop after this many pages in one burst
const EDGE = 96; // px zone at top/bottom of the grid that triggers auto-scroll
const AUTO_STEP = 30; // px per tick while auto-scrolling

type Props = {
  onConfirm: (photos: PickedPhoto[]) => void;
  onBack: () => void;
};

type RelativeDatePreset = "all" | "week" | "month" | "year";
type DatePreset = RelativeDatePreset | `month:${string}`;
const DATE_PRESETS: { key: DatePreset; label: string }[] = [
  { key: "all", label: copy.filters.anyDate },
  { key: "week", label: copy.filters.week },
  { key: "month", label: copy.filters.month },
  { key: "year", label: copy.filters.year },
];

type AlbumChip = { id: string | null; title: string; count: number };

function contentUri(assetId: string): string {
  return `content://media/external/images/media/${assetId}`;
}

function toPicked(asset: MediaLibrary.Asset): PickedPhoto {
  return {
    id: asset.id,
    uri: contentUri(asset.id),
    filename: asset.filename,
    width: asset.width,
    height: asset.height,
    source: "device-gallery",
    creationTime:
      Number.isFinite(asset.creationTime) && asset.creationTime > 0
        ? asset.creationTime
        : undefined,
    placeKey: placeKeyForAsset(asset.id),
    personIds: personIdsForAsset(asset.id),
  };
}

function dateBoundsFor(preset: DatePreset): {
  createdAfter?: number;
  createdBefore?: number;
} {
  const now = new Date();
  if (preset === "week") {
    return { createdAfter: now.getTime() - 7 * 24 * 60 * 60 * 1000 };
  }
  if (preset === "month")
    return { createdAfter: new Date(now.getFullYear(), now.getMonth(), 1).getTime() };
  if (preset === "year") {
    return { createdAfter: new Date(now.getFullYear(), 0, 1).getTime() };
  }
  if (preset.startsWith("month:")) {
    const match = /^(\d{4})-(\d{2})$/.exec(preset.slice("month:".length));
    if (match) {
      const year = Number(match[1]);
      const monthIndex = Number(match[2]) - 1;
      if (monthIndex >= 0 && monthIndex <= 11) {
        return {
          createdAfter: new Date(year, monthIndex, 1).getTime(),
          createdBefore: new Date(year, monthIndex + 1, 1).getTime() - 1,
        };
      }
    }
  }
  return {};
}

type PhotoTileProps = {
  id: string;
  filename: string;
  order?: number;
  size: number;
  dimmed: boolean;
  onToggle: (id: string) => void;
};

const PhotoTile = memo(function PhotoTile({
  id,
  filename,
  order,
  size,
  dimmed,
  onToggle,
}: PhotoTileProps) {
  const selected = order !== undefined;
  const handlePress = useCallback(() => onToggle(id), [id, onToggle]);
  return (
    <Pressable
      accessibilityHint={copy.picker.photoHint}
      accessibilityLabel={copy.picker.photoLabel(filename, selected, order)}
      accessibilityRole="checkbox"
      accessibilityState={{ checked: selected }}
      onPress={handlePress}
      style={[styles.tile, { height: size, width: size }]}
    >
      <Image
        cachePolicy="memory-disk"
        contentFit="cover"
        recyclingKey={id}
        source={contentUri(id)}
        style={[styles.thumb, dimmed ? styles.thumbDimmed : null]}
        transition={80}
      />
      {selected ? <View style={styles.selectedBorder} /> : null}
      <View style={[styles.checkBadge, selected ? styles.checkBadgeOn : null]}>
        <Text style={[styles.checkText, selected ? styles.checkTextOn : null]}>
          {selected ? "✓" : ""}
        </Text>
      </View>
      {selected ? (
        <View style={styles.orderBadge}>
          <Text style={styles.orderText}>{order}</Text>
        </View>
      ) : null}
    </Pressable>
  );
});

// Our own picker: full-library grid, filter by date/album, and slide-to-select
// with edge auto-scroll (long-press then drag to a screen edge keeps scrolling
// and selecting until you lift). Reads MediaStore directly.
export default function GalleryGrid({ onConfirm, onBack }: Props) {
  const { width } = useWindowDimensions();
  const cell = Math.floor((width - GAP * (COLS - 1)) / COLS);
  const rowHeight = cell + GAP;
  const colWidth = width / COLS;

  const [status, setStatus] = useState<"loading" | "denied" | "ready" | "error">("loading");
  const [assets, setAssets] = useState<MediaLibrary.Asset[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [albums, setAlbums] = useState<AlbumChip[]>([]);
  const [datePreset, setDatePreset] = useState<DatePreset>("all");
  const [albumId, setAlbumId] = useState<string | null>(null);
  const [countries, setCountries] = useState<PlaceSummary[]>([]);
  const [cities, setCities] = useState<PlaceSummary[]>([]);
  const [months, setMonths] = useState<MonthSummary[]>([]);
  // One active location filter at a time; id is prefixed "city:" or "country:".
  const [locId, setLocId] = useState<string | null>(null);
  const [indexPct, setIndexPct] = useState<number | null>(null);
  const [indexing, setIndexing] = useState(false);
  const [people, setPeople] = useState<FaceIndexPerson[]>([]);
  const [personId, setPersonId] = useState<string | null>(null);
  const [peopleIndexPct, setPeopleIndexPct] = useState<number | null>(null);
  const [peopleIndexing, setPeopleIndexing] = useState(false);
  const [filterVisible, setFilterVisible] = useState(false);
  const [showTip, setShowTip] = useState(true);
  const [busy, setBusy] = useState(false);
  const [reloading, setReloading] = useState(false);
  const peopleAvailable = useMemo(() => isFaceDetectionAvailable(), []);

  const cursor = useRef<string | undefined>(undefined);
  const hasNext = useRef(true);
  const loadingMore = useRef(false);
  const scrollY = useRef(0);
  const gridHeight = useRef(0);
  const assetsRef = useRef<MediaLibrary.Asset[]>([]);
  assetsRef.current = assets;
  const listRef = useRef<FlashListRef<MediaLibrary.Asset>>(null);

  // Active location filter → set of asset ids. Location isn't a MediaStore
  // query option, so we filter each fetched page client-side against this set.
  const placeSet = useMemo(() => {
    if (!locId) return null;
    const ids = locId.startsWith("country:")
      ? assetIdsForCountry(locId)
      : assetIdsForCity(locId);
    return new Set(ids);
  }, [locId]);
  const personSet = useMemo(() => {
    if (!personId) return null;
    return new Set(assetIdsForPerson(personId));
  }, [personId]);

  const visibleSet = useMemo(() => {
    if (!placeSet) return personSet;
    if (!personSet) return placeSet;
    return new Set([...placeSet].filter((id) => personSet.has(id)));
  }, [personSet, placeSet]);
  const visibleSetRef = useRef<Set<string> | null>(null);
  visibleSetRef.current = visibleSet;

  // Every asset we've ever loaded, so a selection made under one filter still
  // resolves to a real asset after the user switches filters and confirms.
  const seenAssets = useRef<Map<string, MediaLibrary.Asset>>(new Map());

  const queryOpts = useCallback(
    (): MediaLibrary.AssetsOptions => {
      const bounds = dateBoundsFor(datePreset);
      return {
        mediaType: [MediaLibrary.MediaType.photo],
        sortBy: [MediaLibrary.SortBy.creationTime],
        ...bounds,
        ...(albumId ? { album: albumId } : {}),
      };
    },
    [datePreset, albumId],
  );

  // Page until we've gathered ~TARGET visible assets. Without a place filter
  // this returns after one page; with a sparse place filter it keeps paging
  // (guarded) so the grid actually fills instead of showing a few stragglers.
  const fetchBurst = useCallback(async (): Promise<MediaLibrary.Asset[]> => {
    const set = visibleSetRef.current;
    const fresh: MediaLibrary.Asset[] = [];
    let guard = 0;
    while (hasNext.current && fresh.length < BURST_TARGET && guard < BURST_PAGE_CAP) {
      guard += 1;
      const page = await MediaLibrary.getAssetsAsync({
        first: PAGE,
        after: cursor.current,
        ...queryOpts(),
      });
      cursor.current = page.endCursor;
      hasNext.current = page.hasNextPage;
      const batch = set ? page.assets.filter((a) => set.has(a.id)) : page.assets;
      fresh.push(...batch);
      if (page.assets.length === 0) break;
    }
    return fresh;
  }, [queryOpts]);

  const reload = useCallback(async () => {
    cursor.current = undefined;
    hasNext.current = true;
    loadingMore.current = true;
    setReloading(true);
    setAssets([]);
    try {
      const fresh = await fetchBurst();
      setAssets(fresh);
    } catch {
      setStatus("error");
    } finally {
      loadingMore.current = false;
      setReloading(false);
    }
  }, [fetchBurst]);

  const loadMore = useCallback(async () => {
    if (loadingMore.current || !hasNext.current) return;
    loadingMore.current = true;
    try {
      const fresh = await fetchBurst();
      if (fresh.length) setAssets((prev) => prev.concat(fresh));
    } catch {
      setStatus("error");
    } finally {
      loadingMore.current = false;
    }
  }, [fetchBurst]);

  useEffect(() => {
    void (async () => {
      try {
      const perm = await MediaLibrary.requestPermissionsAsync();
      if (perm.status !== "granted") {
        setStatus("denied");
        return;
      }
      const found = await MediaLibrary.getAlbumsAsync({ includeSmartAlbums: true });
      const chips: AlbumChip[] = found
        .filter((a) => (a.assetCount ?? 0) > 0)
        .sort((a, b) => (b.assetCount ?? 0) - (a.assetCount ?? 0))
        .slice(0, 24)
        .map((a) => ({ id: a.id, title: a.title, count: a.assetCount ?? 0 }));
      setAlbums([{ id: null, title: copy.filters.anyAlbum, count: 0 }, ...chips]);
      setStatus("ready");

      // Background location index: hydrate any prior scan, show its places
      // immediately, then keep scanning and refresh the chips as places appear.
      const refreshPlaces = () => {
        setCountries(getCountries());
        setCities(getCities());
        setMonths(getMonths());
      };
      await loadIndex();
      refreshPlaces();
      setIndexing(true);
      void buildIndex({
        onProgress: (done, total) => {
          refreshPlaces();
          setIndexPct(total > 0 ? Math.min(1, done / total) : null);
        },
      }).finally(() => {
        refreshPlaces();
        setIndexPct(null);
        setIndexing(false);
      });

      if (peopleAvailable) {
        const refreshPeople = () => setPeople(getPeople());
        await loadFaceIndex();
        refreshPeople();
        setPeopleIndexing(true);
        void buildFaceIndex({
          onProgress: (done, total) => {
            refreshPeople();
            setPeopleIndexPct(total > 0 ? Math.min(1, done / total) : null);
          },
        }).finally(() => {
          refreshPeople();
          setPeopleIndexPct(null);
          setPeopleIndexing(false);
        });
      }
      } catch {
        setStatus("error");
      }
    })();
  }, [peopleAvailable]);

  useEffect(() => {
    if (status !== "ready") return;
    void reload();
  }, [status, datePreset, albumId, locId, personId, reload]);

  useEffect(() => {
    const m = seenAssets.current;
    for (const a of assets) m.set(a.id, a);
  }, [assets]);

  const selectAll = useCallback(async () => {
    setBusy(true);
    try {
      const set = visibleSetRef.current;
      const ids = new Set<string>();
      let after: string | undefined;
      let more = true;
      while (more && ids.size < SELECT_ALL_CAP) {
        const page = await MediaLibrary.getAssetsAsync({
          first: 500,
          after,
          ...queryOpts(),
        });
        for (const a of page.assets) {
          if (!set || set.has(a.id)) {
            ids.add(a.id);
            seenAssets.current.set(a.id, a);
          }
        }
        after = page.endCursor;
        more = page.hasNextPage;
      }
      setSelected(ids);
    } catch {
      setStatus("error");
    } finally {
      setBusy(false);
    }
  }, [queryOpts]);

  const toggle = useCallback((id: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  // ---------- slide-to-select with edge auto-scroll ----------
  const dragAnchor = useRef<number | null>(null);
  const dragMode = useRef<"add" | "remove">("add");
  const dragSnapshot = useRef<Set<string>>(new Set());
  const lastPointer = useRef({ x: 0, y: 0 });
  const autoDir = useRef(0);
  const autoTimer = useRef<ReturnType<typeof setInterval> | null>(null);

  const indexAt = useCallback(
    (x: number, y: number): number | null => {
      const contentY = y + scrollY.current;
      if (contentY < 0 || x < 0) return null;
      const row = Math.floor(contentY / rowHeight);
      let col = Math.floor(x / colWidth);
      if (col < 0) col = 0;
      if (col > COLS - 1) col = COLS - 1;
      const idx = row * COLS + col;
      if (idx < 0 || idx >= assetsRef.current.length) return null;
      return idx;
    },
    [rowHeight, colWidth],
  );

  const applyDrag = useCallback((current: number) => {
    const anchor = dragAnchor.current;
    if (anchor === null) return;
    const lo = Math.min(anchor, current);
    const hi = Math.max(anchor, current);
    const next = new Set(dragSnapshot.current);
    for (let i = lo; i <= hi; i += 1) {
      const id = assetsRef.current[i]?.id;
      if (!id) continue;
      if (dragMode.current === "add") next.add(id);
      else next.delete(id);
    }
    setSelected(next);
  }, []);

  const stopAuto = useCallback(() => {
    if (autoTimer.current) {
      clearInterval(autoTimer.current);
      autoTimer.current = null;
    }
    autoDir.current = 0;
  }, []);

  const tick = useCallback(() => {
    const dir = autoDir.current;
    if (dir === 0 || dragAnchor.current === null) return;
    const next = Math.max(0, scrollY.current + AUTO_STEP * dir);
    listRef.current?.scrollToOffset({ offset: next, animated: false });
    scrollY.current = next;
    if (dir > 0 && hasNext.current) void loadMore();
    const i = indexAt(lastPointer.current.x, lastPointer.current.y);
    if (i !== null) applyDrag(i);
  }, [indexAt, applyDrag, loadMore]);

  const startAuto = useCallback(() => {
    if (!autoTimer.current) autoTimer.current = setInterval(tick, 16);
  }, [tick]);

  const updateAutoDir = useCallback((y: number) => {
    if (y > gridHeight.current - EDGE) autoDir.current = 1;
    else if (y < EDGE) autoDir.current = -1;
    else autoDir.current = 0;
  }, []);

  const beginDrag = useCallback(
    (x: number, y: number) => {
      const i = indexAt(x, y);
      if (i === null) return;
      dragAnchor.current = i;
      dragSnapshot.current = new Set(selected);
      const id = assetsRef.current[i].id;
      dragMode.current = selected.has(id) ? "remove" : "add";
      applyDrag(i);
    },
    [indexAt, selected, applyDrag],
  );

  const updateDrag = useCallback(
    (x: number, y: number) => {
      if (dragAnchor.current === null) return;
      const i = indexAt(x, y);
      if (i === null) return;
      applyDrag(i);
    },
    [indexAt, applyDrag],
  );

  useEffect(() => () => stopAuto(), [stopAuto]);

  const pan = useMemo(
    () =>
      Gesture.Pan()
        .runOnJS(true)
        .activateAfterLongPress(180)
        .maxPointers(1)
        .onStart((e) => {
          lastPointer.current = { x: e.x, y: e.y };
          beginDrag(e.x, e.y);
          startAuto();
        })
        .onUpdate((e) => {
          lastPointer.current = { x: e.x, y: e.y };
          updateDrag(e.x, e.y);
          updateAutoDir(e.y);
        })
        .onEnd(() => {
          stopAuto();
          dragAnchor.current = null;
        })
        .onFinalize(() => {
          stopAuto();
          dragAnchor.current = null;
        }),
    [beginDrag, updateDrag, updateAutoDir, startAuto, stopAuto],
  );

  const orderMap = useMemo(() => {
    const m = new Map<string, number>();
    let n = 0;
    for (const a of assets) if (selected.has(a.id)) m.set(a.id, ++n);
    return m;
  }, [assets, selected]);

  const confirm = useCallback(() => {
    const m = seenAssets.current;
    const picked: PickedPhoto[] = [];
    for (const id of selected) {
      const a = m.get(id);
      if (a) picked.push(toPicked(a));
    }
    onConfirm(picked);
  }, [selected, onConfirm]);

  const renderItem = useCallback(
    ({ item }: { item: MediaLibrary.Asset }) => (
      <PhotoTile
        dimmed={selected.size > 0 && !selected.has(item.id)}
        filename={item.filename}
        id={item.id}
        onToggle={toggle}
        order={orderMap.get(item.id)}
        size={cell}
      />
    ),
    [cell, orderMap, selected, toggle],
  );

  const count = selected.size;
  const activeFilterCount =
    Number(datePreset !== "all") + Number(albumId !== null) + Number(locId !== null) + Number(personId !== null);

  const filterPeople = useMemo(
    () => people.map((person, index) => ({
      faceCount: person.faceCount,
      id: person.id,
      imageUri: person.faceThumbUri ?? contentUri(person.coverAssetId),
      label: copy.filters.personName(index),
      photoCount: person.assetIds.length,
    })),
    [people],
  );
  const filterCountries = useMemo(
    () => countries.map((place) => ({ id: place.id, label: place.name, photoCount: place.count })),
    [countries],
  );
  const filterCities = useMemo(
    () => cities.map((place) => ({ id: place.id, label: place.name, photoCount: place.count })),
    [cities],
  );
  const filterMonths = useMemo(
    () => months.map((month) => ({
      detail: copy.filters.photoCount(month.count),
      id: `month:${month.id}`,
      label: month.label,
    })),
    [months],
  );

  const countFilterPhotos = useCallback(async (selection: FilterSelection): Promise<number> => {
    try {
      const pendingPlaceSet = selection.locationId
        ? new Set(
            selection.locationId.startsWith("country:")
              ? assetIdsForCountry(selection.locationId)
              : assetIdsForCity(selection.locationId),
          )
        : null;
      const pendingPersonSet = selection.personId
        ? new Set(assetIdsForPerson(selection.personId))
        : null;
      const pendingVisibleSet = pendingPlaceSet
        ? pendingPersonSet
          ? new Set([...pendingPlaceSet].filter((id) => pendingPersonSet.has(id)))
          : pendingPlaceSet
        : pendingPersonSet;

      if (selection.dateId === "all" && albumId === null && pendingVisibleSet) {
        return pendingVisibleSet.size;
      }

      const bounds = dateBoundsFor(selection.dateId as DatePreset);
      const options: MediaLibrary.AssetsOptions = {
        first: pendingVisibleSet ? 500 : 1,
        mediaType: [MediaLibrary.MediaType.photo],
        sortBy: [MediaLibrary.SortBy.creationTime],
        ...bounds,
        ...(albumId ? { album: albumId } : {}),
      };
      let page = await MediaLibrary.getAssetsAsync(options);
      if (!pendingVisibleSet) return page.totalCount;

      let matching = page.assets.filter((asset) => pendingVisibleSet.has(asset.id)).length;
      let guard = 0;
      while (page.hasNextPage && guard < BURST_PAGE_CAP) {
        guard += 1;
        page = await MediaLibrary.getAssetsAsync({ ...options, after: page.endCursor });
        matching += page.assets.filter((asset) => pendingVisibleSet.has(asset.id)).length;
        if (page.assets.length === 0) break;
      }
      return matching;
    } catch {
      return assets.length;
    }
  }, [albumId, assets.length]);

  const clearFilters = useCallback(() => {
    setDatePreset("all");
    setAlbumId(null);
    setLocId(null);
    setPersonId(null);
    setFilterVisible(false);
  }, []);

  const applyFilters = useCallback((selection: FilterSelection) => {
    setDatePreset(selection.dateId as DatePreset);
    setLocId(selection.locationId);
    setPersonId(selection.personId);
    setFilterVisible(false);
  }, []);

  const clearSelection = useCallback(() => setSelected(new Set()), []);

  if (filterVisible) {
    return (
      <FilterScreen
        cities={filterCities}
        countries={filterCountries}
        countPhotos={countFilterPhotos}
        dateLoadingText={indexing
          ? `Scanning dates…${indexPct === null ? "" : ` ${Math.round(indexPct * 100)}%`}`
          : undefined}
        datePresets={DATE_PRESETS.map((preset) => ({ id: preset.key, label: preset.label }))}
        initialSelection={{ dateId: datePreset, locationId: locId, personId }}
        locationLoadingText={indexing
          ? copy.filters.scanningPlaces(indexPct === null ? undefined : Math.round(indexPct * 100))
          : undefined}
        months={filterMonths}
        onApply={applyFilters}
        onBack={() => setFilterVisible(false)}
        people={filterPeople}
        peopleAvailable={peopleAvailable}
        peopleLoadingText={peopleIndexing
          ? copy.filters.scanningPeople(
              peopleIndexPct === null ? undefined : Math.round(peopleIndexPct * 100),
            )
          : undefined}
      />
    );
  }

  return (
    <GestureHandlerRootView style={styles.root}>
      <StatusBar backgroundColor={colors.background} barStyle="dark-content" />
      <View style={styles.header}>
        <ScreenHeader
          backHint={copy.picker.backHint}
          compact
          helper={copy.picker.helper}
          onBack={onBack}
          step={1}
          title={copy.picker.title}
        />
        {showTip ? (
          <HintBanner
            dismissLabel={copy.picker.dismissTip}
            onDismiss={() => setShowTip(false)}
            text={copy.picker.tip}
          />
        ) : null}
        <View style={styles.toolbar}>
          <Pressable
            accessibilityHint={copy.picker.filterHint}
            accessibilityLabel={copy.picker.filter}
            accessibilityRole="button"
            onPress={() => setFilterVisible(true)}
            style={({ pressed }) => [styles.toolButton, pressed ? styles.toolPressed : null]}
          >
            <Text style={styles.filterIcon}>≡</Text>
            <View>
              <Text style={styles.toolLabel}>{copy.picker.filter}</Text>
              {activeFilterCount > 0 ? (
                <Text style={styles.toolDetail}>{copy.picker.filtersApplied(activeFilterCount)}</Text>
              ) : null}
            </View>
          </Pressable>
          <View style={styles.selectionActions}>
            <Pressable
              accessibilityHint={copy.picker.selectAllHint}
              accessibilityLabel={copy.picker.selectAll}
              accessibilityRole="button"
              accessibilityState={{ busy, disabled: busy }}
              disabled={busy}
              onPress={() => void selectAll()}
              style={({ pressed }) => [styles.textAction, pressed ? styles.toolPressed : null]}
            >
              <Text style={styles.textActionLabel}>{busy ? copy.picker.busy : copy.picker.selectAll}</Text>
            </Pressable>
            {count > 0 ? (
              <Pressable
                accessibilityHint={copy.picker.clearHint}
                accessibilityLabel={copy.picker.clear}
                accessibilityRole="button"
                onPress={clearSelection}
                style={({ pressed }) => [styles.textAction, pressed ? styles.toolPressed : null]}
              >
                <Text style={styles.textActionLabel}>{copy.picker.clear}</Text>
              </Pressable>
            ) : null}
          </View>
        </View>
      </View>

      {status === "loading" || (reloading && assets.length === 0) ? (
        <LoadingState helper={copy.picker.loadingHelper} title={copy.picker.loadingTitle} />
      ) : status === "denied" ? (
        <ErrorState
          actionHint={copy.picker.openSettingsHint}
          actionLabel={copy.picker.openSettings}
          helper={copy.picker.permissionHelper}
          onAction={() => void Linking.openSettings()}
          title={copy.picker.permissionTitle}
        />
      ) : status === "error" ? (
        <ErrorState
          actionHint={copy.picker.backHint}
          actionLabel={copy.common.goBack}
          helper={copy.picker.errorHelper}
          onAction={onBack}
          title={copy.picker.errorTitle}
        />
      ) : assets.length === 0 ? (
        <EmptyState
          actionHint={activeFilterCount > 0 ? copy.filters.allHint : copy.picker.backHint}
          actionLabel={activeFilterCount > 0 ? copy.filters.all : copy.common.goBack}
          helper={copy.picker.emptyHelper}
          onAction={activeFilterCount > 0 ? clearFilters : onBack}
          title={copy.picker.emptyTitle}
        />
      ) : (
        <GestureDetector gesture={pan}>
          <View
            style={styles.gridWrap}
            onLayout={(e) => {
              gridHeight.current = e.nativeEvent.layout.height;
            }}
          >
            <FlashList
              ref={listRef}
              data={assets}
              extraData={selected}
              renderItem={renderItem}
              keyExtractor={(item) => item.id}
              numColumns={COLS}
              onEndReached={loadMore}
              onEndReachedThreshold={1.2}
              onScroll={(e) => {
                scrollY.current = e.nativeEvent.contentOffset.y;
              }}
              scrollEventThrottle={16}
              contentContainerStyle={styles.list}
            />
          </View>
        </GestureDetector>
      )}

      <View style={styles.footer}>
        <Text accessibilityLiveRegion="polite" style={styles.footerCount}>
          {count > 0 ? copy.picker.selected(count) : copy.picker.chooseOne}
        </Text>
        <PrimaryButton
          accessibilityHint={copy.picker.nextHint}
          disabled={count === 0}
          label={copy.picker.next(count)}
          onPress={confirm}
        />
      </View>
    </GestureHandlerRootView>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.background },
  header: {
    gap: spacing.sm,
    paddingBottom: spacing.sm,
    paddingHorizontal: spacing.md,
    paddingTop: (StatusBar.currentHeight ?? 24) + spacing.sm,
  },
  toolbar: { alignItems: "center", flexDirection: "row", justifyContent: "space-between" },
  toolButton: {
    alignItems: "center",
    borderColor: colors.hairline,
    borderRadius: 10,
    borderWidth: 1,
    flexDirection: "row",
    gap: spacing.xs,
    minHeight: 48,
    paddingHorizontal: spacing.sm,
  },
  toolDetail: { color: colors.gold, fontFamily: fonts.body, ...typeScale.small },
  filterIcon: { color: colors.gold, fontFamily: fonts.body, fontSize: 23, lineHeight: 25 },
  toolLabel: { color: colors.text, fontFamily: fonts.body, fontWeight: "700", ...typeScale.label },
  toolPressed: { opacity: 0.58 },
  selectionActions: { alignItems: "center", flexDirection: "row", gap: spacing.xs },
  textAction: { justifyContent: "center", minHeight: 48, paddingHorizontal: spacing.xs },
  textActionLabel: { color: colors.gold, fontFamily: fonts.body, ...typeScale.label },
  gridWrap: { flex: 1 },
  list: { paddingHorizontal: 0 },
  tile: { marginBottom: GAP, position: "relative" },
  thumb: { backgroundColor: colors.panel, flex: 1 },
  thumbDimmed: { opacity: 0.52 },
  selectedBorder: {
    borderColor: colors.gold,
    borderWidth: 3,
    bottom: 0,
    left: 0,
    position: "absolute",
    right: 0,
    top: 0,
  },
  checkBadge: {
    alignItems: "center",
    backgroundColor: "rgba(20, 19, 17, 0.74)",
    borderColor: colors.text,
    borderRadius: 16,
    borderWidth: 2,
    height: 32,
    justifyContent: "center",
    position: "absolute",
    right: 7,
    top: 7,
    width: 32,
  },
  checkBadgeOn: { backgroundColor: colors.gold, borderColor: colors.gold },
  checkText: { color: colors.text, fontFamily: fonts.body, fontSize: 19, fontWeight: "700", lineHeight: 22 },
  checkTextOn: { color: colors.ink },
  orderBadge: {
    alignItems: "center",
    backgroundColor: colors.panel,
    borderColor: colors.gold,
    borderRadius: 12,
    borderWidth: 1,
    bottom: 7,
    height: 24,
    justifyContent: "center",
    minWidth: 24,
    paddingHorizontal: 5,
    position: "absolute",
    right: 7,
  },
  orderText: { color: colors.gold, fontFamily: fonts.body, fontSize: 14, fontWeight: "700" },
  footer: {
    backgroundColor: colors.panel,
    borderTopColor: colors.hairline,
    borderTopWidth: 1,
    gap: spacing.xs,
    paddingBottom: spacing.lg,
    paddingHorizontal: spacing.md,
    paddingTop: spacing.sm,
  },
  footerCount: { color: colors.muted, fontFamily: fonts.body, textAlign: "center", ...typeScale.label },
});
