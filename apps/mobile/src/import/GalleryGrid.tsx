import { FlashList, type FlashListRef } from "@shopify/flash-list";
import { Image } from "expo-image";
// @ts-expect-error TypeScript bundler resolution normally omits source extensions.
import { thumbnailUri } from "../../modules/photeo-scan-service/src/index.ts";
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
import { useSafeAreaInsets } from "react-native-safe-area-context";

import {
  assetIdsForPerson,
  getPeople,
  isFaceDetectionAvailable,
  loadFaceIndex,
  personIdsForAsset,
  type FaceIndexPerson,
} from "../faces/face-index";
import { combinePersonAssetIds, type FaceMatchMode } from "../faces/face-filter";
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
  spacing,
  typeScale,
} from "../ui";
import {
  canWidenAccess,
  NO_PHOTO_ACCESS,
  requestPhotoAccess,
  type PhotoAccess,
} from "../ui/photo-access";
import {
  recordThumbnailResolution,
  thumbnailRequestFor,
  thumbnailUriCache,
} from "../ui/photo-thumbnail-cache";
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
  // Picks handed back in when the user returns here from Review. Back is a
  // navigation gesture, not a discard, so the picker re-opens on their picks.
  initialSelection?: PickedPhoto[];
};

type RelativeDatePreset = "all" | "week" | "month" | "year";
type DatePreset = RelativeDatePreset | `month:${string}` | `year:${string}`;
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
  if (preset.startsWith("year:")) {
    const year = Number(preset.slice("year:".length));
    if (Number.isInteger(year) && year >= 1970 && year <= 9999) {
      return {
        createdAfter: new Date(year, 0, 1).getTime(),
        createdBefore: new Date(year + 1, 0, 1).getTime() - 1,
      };
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
  const request = thumbnailRequestFor(size);
  const [resolved, setResolved] = useState<{
    assetId: string;
    request: number;
    uri: string;
  } | null>(() => {
    const uri = thumbnailUriCache.peek(id, request);
    return uri ? { assetId: id, request, uri } : null;
  });
  const thumb = resolved?.assetId === id && resolved.request === request
    ? resolved.uri
    : thumbnailUriCache.peek(id, request);

  useEffect(() => {
    const cached = thumbnailUriCache.get(id, request);
    if (cached) {
      setResolved({ assetId: id, request, uri: cached });
      return;
    }
    let live = true;
    const started = performance.now();
    thumbnailUri(id, request)
      .then((uri) => {
        recordThumbnailResolution(performance.now() - started);
        if (!uri) return;
        thumbnailUriCache.set(id, { request, uri });
        if (live) setResolved({ assetId: id, request, uri });
      })
      .catch(() => undefined);
    return () => {
      live = false;
    };
  }, [id, request]);

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
        recyclingKey={thumb ?? id}
        source={thumb ?? contentUri(id)}
        style={[styles.thumb, dimmed ? styles.thumbDimmed : null]}
      />
      {selected ? <View style={styles.selectedBorder} /> : null}
      <View style={[styles.checkBadge, selected ? styles.checkBadgeOn : null]}>
        <Text style={[styles.checkText, selected ? styles.checkTextOn : null]}>
          {selected ? "✓" : ""}
        </Text>
      </View>
    </Pressable>
  );
});

// Our own picker: full-library grid, filter by date/album, and slide-to-select
// with edge auto-scroll (long-press then drag to a screen edge keeps scrolling
// and selecting until you lift). Reads MediaStore directly.
export default function GalleryGrid({ onConfirm, onBack, initialSelection }: Props) {
  const insets = useSafeAreaInsets();
  const { width } = useWindowDimensions();
  const cell = Math.floor((width - GAP * (COLS - 1)) / COLS);
  const rowHeight = cell + GAP;
  const colWidth = width / COLS;

  const [status, setStatus] = useState<"loading" | "denied" | "ready" | "error">("loading");
  const [assets, setAssets] = useState<MediaLibrary.Asset[]>([]);
  // Seeded once from the restored picks; later renders must not clobber edits.
  const [selected, setSelected] = useState<Set<string>>(
    () => new Set((initialSelection ?? []).map((photo) => photo.id)),
  );
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
  const [personIds, setPersonIds] = useState<string[]>([]);
  const [faceMatchMode, setFaceMatchMode] = useState<FaceMatchMode>("any");
  const [filterVisible, setFilterVisible] = useState(false);
  const [busy, setBusy] = useState(false);
  const [reloading, setReloading] = useState(false);
  const [access, setAccess] = useState<PhotoAccess>(NO_PHOTO_ACCESS);
  const [accessDismissed, setAccessDismissed] = useState(false);
  // "Select all" is bounded; saying so beats silently picking the first 3,000.
  const [selectAllCapped, setSelectAllCapped] = useState(false);
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
    return combinePersonAssetIds(personIds, faceMatchMode, assetIdsForPerson);
  }, [faceMatchMode, personIds]);

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

  // A restored pick may sit outside the pages this fresh mount has fetched, so
  // keep the original PickedPhoto as the fallback for confirm().
  const restoredPicks = useMemo(() => {
    const map = new Map<string, PickedPhoto>();
    for (const photo of initialSelection ?? []) map.set(photo.id, photo);
    return map;
  }, [initialSelection]);

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
      // Android 14+ "Select photos" comes back granted-but-limited. Treat that
      // as usable and say what is missing, instead of a permission dead end.
      const perm = await requestPhotoAccess();
      setAccess(perm);
      if (!perm.readable) {
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
      void buildIndex({
        onProgress: (done, total) => {
          setIndexing(true);
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
      }
      } catch {
        setStatus("error");
      }
    })();
  }, [peopleAvailable]);

  useEffect(() => {
    if (status !== "ready") return;
    void reload();
  }, [status, datePreset, albumId, locId, personIds, faceMatchMode, reload]);

  useEffect(() => {
    const m = seenAssets.current;
    for (const a of assets) m.set(a.id, a);
  }, [assets]);

  const selectAll = useCallback(async () => {
    setBusy(true);
    setSelectAllCapped(false);
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
      setSelectAllCapped(more && ids.size >= SELECT_ALL_CAP);
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
      else {
        const restored = restoredPicks.get(id);
        if (restored) picked.push(restored);
      }
    }
    onConfirm(picked);
  }, [selected, onConfirm, restoredPicks]);

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
    Number(datePreset !== "all") + Number(albumId !== null) + Number(locId !== null) + Number(personIds.length > 0);

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
      const pendingPersonSet = combinePersonAssetIds(
        selection.personIds,
        selection.faceMatchMode,
        assetIdsForPerson,
      );
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
    setPersonIds([]);
    setFaceMatchMode("any");
    setFilterVisible(false);
  }, []);

  const applyFilters = useCallback((selection: FilterSelection) => {
    setDatePreset(selection.dateId as DatePreset);
    setLocId(selection.locationId);
    setPersonIds(selection.personIds);
    setFaceMatchMode(selection.faceMatchMode);
    setFilterVisible(false);
  }, []);

  const clearSelection = useCallback(() => setSelected(new Set()), []);

  const widenAccess = useCallback(() => {
    void (async () => {
      if (!canWidenAccess(access)) {
        void Linking.openSettings();
        return;
      }
      const next = await requestPhotoAccess();
      setAccess(next);
      if (next.readable) {
        setStatus("ready");
        await reload();
      }
    })();
  }, [access, reload]);

  if (filterVisible) {
    // A limited grant means the filter lists are built from a handful of photos.
    // Say that inside the filters too, or an empty list reads as a broken filter.
    const limitedNote = access.limited ? copy.access.limitedShort : undefined;
    return (
      <FilterScreen
        cities={filterCities}
        countries={filterCountries}
        countPhotos={countFilterPhotos}
        dateLoadingText={indexing
          ? `Scanning dates…${indexPct === null ? "" : ` ${Math.round(indexPct * 100)}%`}`
          : limitedNote}
        datePresets={DATE_PRESETS.map((preset) => ({ id: preset.key, label: preset.label }))}
        initialSelection={{ dateId: datePreset, faceMatchMode, locationId: locId, personIds }}
        locationLoadingText={indexing
          ? copy.filters.scanningPlaces(indexPct === null ? undefined : Math.round(indexPct * 100))
          : limitedNote}
        months={filterMonths}
        onApply={applyFilters}
        onBack={() => setFilterVisible(false)}
        people={filterPeople}
        peopleAvailable={peopleAvailable}
        peopleLoadingText={limitedNote}
      />
    );
  }

  return (
    <GestureHandlerRootView style={styles.root}>
      <StatusBar backgroundColor={colors.background} barStyle="dark-content" />
      <View style={styles.header}>
        <View style={styles.topRow}>
          <Pressable
            accessibilityHint={copy.picker.backHint}
            accessibilityRole="button"
            onPress={onBack}
            style={({ pressed }) => [styles.topAction, pressed ? styles.toolPressed : null]}
          >
            <Text style={styles.cancelLabel}>Cancel</Text>
          </Pressable>
          <View style={styles.steps}>
            <Text style={styles.stepActive}>Pick</Text><Text style={styles.stepArrow}>→</Text>
            <Text style={styles.stepIdle}>Review</Text><Text style={styles.stepArrow}>→</Text>
            <Text style={styles.stepIdle}>Done</Text>
          </View>
          <Pressable
            accessibilityHint={copy.picker.selectAllHint}
            accessibilityLabel={copy.picker.selectAll}
            accessibilityRole="button"
            accessibilityState={{ busy, disabled: busy }}
            disabled={busy}
            onPress={() => void selectAll()}
            style={({ pressed }) => [styles.topAction, pressed ? styles.toolPressed : null]}
          >
            <Text style={styles.textActionLabel}>{busy ? "Selecting…" : copy.picker.selectAll}</Text>
          </Pressable>
        </View>
        <Text accessibilityRole="header" style={styles.title}>{copy.picker.title}</Text>
        <Text style={styles.helper}>{copy.picker.helper}</Text>
        <View style={styles.toolbar}>
          <Pressable
            accessibilityHint={copy.picker.filterHint}
            accessibilityLabel={copy.picker.filter}
            accessibilityRole="button"
            onPress={() => setFilterVisible(true)}
            style={({ pressed }) => [styles.toolButton, pressed ? styles.toolPressed : null]}
          >
            <Text style={styles.toolLabel}>{copy.picker.filter}</Text>
            <Text style={activeFilterCount > 0 ? styles.toolDetail : styles.toolSummary}>
              {activeFilterCount > 0 ? copy.picker.filtersApplied(activeFilterCount) : "All photos  ›"}
            </Text>
          </Pressable>
          {count > 0 ? (
            <Pressable accessibilityHint={copy.picker.clearHint} accessibilityRole="button" onPress={clearSelection} style={styles.clearSelection}>
              <Text style={styles.clearSelectionText}>Clear picks</Text>
            </Pressable>
          ) : null}
        </View>
        {selectAllCapped ? (
          <Text accessibilityLiveRegion="polite" style={styles.notice}>
            {`Picked the ${SELECT_ALL_CAP.toLocaleString()} most recent photos — that is as many as one album can weigh up at once.`}
          </Text>
        ) : null}
        {access.limited && !accessDismissed ? (
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
      </View>

      {status === "loading" || (reloading && assets.length === 0) ? (
        <LoadingState helper={copy.picker.loadingHelper} title={copy.picker.loadingTitle} />
      ) : status === "denied" ? (
        <ErrorState
          actionHint={canWidenAccess(access) ? copy.access.limitedActionHint : copy.picker.openSettingsHint}
          actionLabel={canWidenAccess(access) ? copy.access.limitedAction : copy.picker.openSettings}
          helper={copy.picker.permissionHelper}
          onAction={widenAccess}
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
          helper={access.limited ? copy.access.limitedShort : copy.picker.emptyHelper}
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

      {/* Edge-to-edge: lift the CTA clear of the transparent Android nav bar. */}
      <View style={[styles.footer, { paddingBottom: Math.max(spacing.lg, insets.bottom + spacing.sm) }]}>
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
    gap: spacing.xs,
    paddingBottom: spacing.xs,
    paddingHorizontal: 18,
    paddingTop: (StatusBar.currentHeight ?? 24) + spacing.xs,
  },
  banner: { paddingTop: spacing.xs },
  cancelLabel: { color: colors.muted, fontFamily: fonts.semibold, ...typeScale.small },
  clearSelection: { alignSelf: "flex-end", justifyContent: "center", minHeight: 44, paddingHorizontal: spacing.xs },
  clearSelectionText: { color: colors.muted, fontFamily: fonts.semibold, ...typeScale.eyebrow },
  helper: { color: colors.muted, fontFamily: fonts.regular, fontSize: 14.5, lineHeight: 21 },
  notice: { color: colors.goldPressed, fontFamily: fonts.medium, ...typeScale.small },
  stepActive: { color: colors.goldPressed, fontFamily: fonts.bold, fontSize: 12.5 },
  stepArrow: { color: colors.muted, fontFamily: fonts.bold, fontSize: 12.5 },
  stepIdle: { color: colors.muted, fontFamily: fonts.bold, fontSize: 12.5 },
  steps: { alignItems: "center", flexDirection: "row", gap: 6 },
  title: { color: colors.text, fontFamily: fonts.extraBold, fontSize: 27, letterSpacing: -0.8, lineHeight: 32, paddingTop: spacing.xxs },
  toolbar: { alignItems: "center", flexDirection: "row", gap: spacing.xs },
  topAction: { alignItems: "center", justifyContent: "center", minHeight: 44, minWidth: 72 },
  topRow: { alignItems: "center", flexDirection: "row", justifyContent: "space-between" },
  toolButton: {
    alignItems: "center",
    backgroundColor: colors.panel,
    borderColor: colors.hairline,
    borderRadius: 24,
    borderWidth: 1,
    flex: 1,
    flexDirection: "row",
    justifyContent: "space-between",
    minHeight: 48,
    paddingHorizontal: spacing.md,
  },
  toolDetail: { color: colors.goldPressed, fontFamily: fonts.semibold, ...typeScale.small },
  toolLabel: { color: colors.text, fontFamily: fonts.semibold, ...typeScale.label },
  toolSummary: { color: colors.muted, fontFamily: fonts.regular, ...typeScale.small },
  toolPressed: { opacity: 0.58 },
  textActionLabel: { color: colors.goldPressed, fontFamily: fonts.semibold, ...typeScale.small },
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
    borderRadius: 12,
    borderWidth: 2,
    height: 24,
    justifyContent: "center",
    position: "absolute",
    right: 7,
    top: 7,
    width: 24,
  },
  checkBadgeOn: { backgroundColor: colors.gold, borderColor: colors.gold },
  checkText: { color: colors.onAccent, fontFamily: fonts.bold, fontSize: 14, lineHeight: 16 },
  checkTextOn: { color: colors.onAccent },
  footer: {
    backgroundColor: colors.background,
    borderTopColor: colors.hairline,
    borderTopWidth: 1,
    paddingBottom: spacing.lg,
    paddingHorizontal: spacing.md,
    paddingTop: spacing.sm,
  },
});
