import { FlashList, type FlashListRef } from "@shopify/flash-list";
import { Image } from "expo-image";
import * as MediaLibrary from "expo-media-library/legacy";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ActivityIndicator,
  Pressable,
  ScrollView,
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

import type { PickedPhoto } from "./picked-photo";
import {
  assetIdsForPlace,
  buildIndex,
  getPlaces,
  loadIndex,
  type PlaceSummary,
} from "./photo-index";

const C = {
  bg: "#141311",
  panel: "#1c1a17",
  chip: "#232019",
  line: "#2c2a25",
  text: "#e8e4dc",
  muted: "#9a927f",
  gold: "#c8a24a",
};

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

type DatePreset = "all" | "week" | "month" | "year";
const DATE_PRESETS: { key: DatePreset; label: string }[] = [
  { key: "all", label: "All" },
  { key: "week", label: "Week" },
  { key: "month", label: "Month" },
  { key: "year", label: "Year" },
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
  };
}

function createdAfterFor(preset: DatePreset): number | undefined {
  const now = new Date();
  if (preset === "week") return now.getTime() - 7 * 24 * 60 * 60 * 1000;
  if (preset === "month")
    return new Date(now.getFullYear(), now.getMonth(), 1).getTime();
  if (preset === "year") return new Date(now.getFullYear(), 0, 1).getTime();
  return undefined;
}

// Our own picker: full-library grid, filter by date/album, and slide-to-select
// with edge auto-scroll (long-press then drag to a screen edge keeps scrolling
// and selecting until you lift). Reads MediaStore directly.
export default function GalleryGrid({ onConfirm, onBack }: Props) {
  const { width } = useWindowDimensions();
  const cell = Math.floor((width - GAP * (COLS - 1)) / COLS);
  const rowHeight = cell + GAP;
  const colWidth = width / COLS;

  const [status, setStatus] = useState<"loading" | "denied" | "ready">("loading");
  const [assets, setAssets] = useState<MediaLibrary.Asset[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [albums, setAlbums] = useState<AlbumChip[]>([]);
  const [datePreset, setDatePreset] = useState<DatePreset>("all");
  const [albumId, setAlbumId] = useState<string | null>(null);
  const [places, setPlaces] = useState<PlaceSummary[]>([]);
  const [placeId, setPlaceId] = useState<string | null>(null);
  const [indexPct, setIndexPct] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);

  const cursor = useRef<string | undefined>(undefined);
  const hasNext = useRef(true);
  const loadingMore = useRef(false);
  const scrollY = useRef(0);
  const gridHeight = useRef(0);
  const assetsRef = useRef<MediaLibrary.Asset[]>([]);
  assetsRef.current = assets;
  const listRef = useRef<FlashListRef<MediaLibrary.Asset>>(null);

  // Active place filter → set of asset ids. Place isn't a MediaStore query
  // option, so we filter each fetched page client-side against this set.
  const placeSet = useMemo(
    () => (placeId ? new Set(assetIdsForPlace(placeId)) : null),
    [placeId],
  );
  const placeSetRef = useRef<Set<string> | null>(null);
  placeSetRef.current = placeSet;

  // Every asset we've ever loaded, so a selection made under one filter still
  // resolves to a real asset after the user switches filters and confirms.
  const seenAssets = useRef<Map<string, MediaLibrary.Asset>>(new Map());

  const queryOpts = useCallback(
    (): MediaLibrary.AssetsOptions => ({
      mediaType: [MediaLibrary.MediaType.photo],
      sortBy: [MediaLibrary.SortBy.creationTime],
      createdAfter: createdAfterFor(datePreset),
      ...(albumId ? { album: albumId } : {}),
    }),
    [datePreset, albumId],
  );

  // Page until we've gathered ~TARGET visible assets. Without a place filter
  // this returns after one page; with a sparse place filter it keeps paging
  // (guarded) so the grid actually fills instead of showing a few stragglers.
  const fetchBurst = useCallback(async (): Promise<MediaLibrary.Asset[]> => {
    const set = placeSetRef.current;
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
    setAssets([]);
    const fresh = await fetchBurst();
    setAssets(fresh);
    loadingMore.current = false;
  }, [fetchBurst]);

  const loadMore = useCallback(async () => {
    if (loadingMore.current || !hasNext.current) return;
    loadingMore.current = true;
    const fresh = await fetchBurst();
    if (fresh.length) setAssets((prev) => prev.concat(fresh));
    loadingMore.current = false;
  }, [fetchBurst]);

  useEffect(() => {
    (async () => {
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
      setAlbums([{ id: null, title: "All", count: 0 }, ...chips]);
      setStatus("ready");

      // Background location index: hydrate any prior scan, show its places
      // immediately, then keep scanning and refresh the chips as places appear.
      await loadIndex();
      setPlaces(getPlaces());
      void buildIndex({
        onProgress: (done, total) => {
          setPlaces(getPlaces());
          setIndexPct(total > 0 ? Math.min(1, done / total) : null);
        },
      }).finally(() => {
        setPlaces(getPlaces());
        setIndexPct(null);
      });
    })();
  }, []);

  useEffect(() => {
    if (status !== "ready") return;
    void reload();
  }, [status, datePreset, albumId, placeId, reload]);

  useEffect(() => {
    const m = seenAssets.current;
    for (const a of assets) m.set(a.id, a);
  }, [assets]);

  const selectAll = useCallback(async () => {
    setBusy(true);
    try {
      const set = placeSetRef.current;
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
    ({ item }: { item: MediaLibrary.Asset }) => {
      const order = orderMap.get(item.id);
      return (
        <Pressable
          onPress={() => toggle(item.id)}
          style={{ width: cell, height: cell, marginBottom: GAP }}
        >
          <Image
            source={contentUri(item.id)}
            style={styles.thumb}
            contentFit="cover"
            recyclingKey={item.id}
            transition={80}
          />
          {order ? (
            <>
              <View style={styles.selShade} />
              <View style={styles.badge}>
                <Text style={styles.badgeText}>{order}</Text>
              </View>
            </>
          ) : (
            <View style={styles.dot} />
          )}
        </Pressable>
      );
    },
    [cell, orderMap, toggle],
  );

  const count = selected.size;

  return (
    <GestureHandlerRootView style={styles.root}>
      {/* compact header: back · count · actions */}
      <View style={styles.header}>
        <Pressable onPress={onBack} hitSlop={12}>
          <Text style={styles.back}>‹ Back</Text>
        </Pressable>
        <Text style={styles.count}>{count ? `${count} selected` : "Your photos"}</Text>
        <View style={styles.headerActions}>
          <Pressable onPress={selectAll} hitSlop={8} disabled={busy}>
            <Text style={styles.action}>{busy ? "…" : "All"}</Text>
          </Pressable>
          {count > 0 ? (
            <Pressable onPress={() => setSelected(new Set())} hitSlop={8}>
              <Text style={styles.action}>Clear</Text>
            </Pressable>
          ) : null}
        </View>
      </View>

      {/* single compact filter row: date presets · albums */}
      <ScrollView
        horizontal
        showsHorizontalScrollIndicator={false}
        style={styles.filterScroll}
        contentContainerStyle={styles.filterRow}
      >
        {DATE_PRESETS.map((p) => {
          const on = datePreset === p.key;
          return (
            <Pressable
              key={p.key}
              onPress={() => setDatePreset(p.key)}
              style={[styles.chip, on && styles.chipOn]}
            >
              <Text style={[styles.chipText, on && styles.chipTextOn]}>{p.label}</Text>
            </Pressable>
          );
        })}
        {albums.length > 1 ? <View style={styles.divider} /> : null}
        {albums.map((a) => {
          const on = albumId === a.id;
          return (
            <Pressable
              key={a.id ?? "all"}
              onPress={() => setAlbumId(a.id)}
              style={[styles.chip, on && styles.chipOn]}
            >
              <Text style={[styles.chipText, on && styles.chipTextOn]}>
                {a.title}
                {a.count ? ` ${a.count}` : ""}
              </Text>
            </Pressable>
          );
        })}
        {places.length > 0 || indexPct !== null ? (
          <View style={styles.divider} />
        ) : null}
        {places.map((p) => {
          const on = placeId === p.id;
          return (
            <Pressable
              key={p.id}
              onPress={() => setPlaceId(on ? null : p.id)}
              style={[styles.chip, on && styles.chipOn]}
            >
              <Text style={[styles.chipText, on && styles.chipTextOn]}>
                {p.name}
                {p.count ? ` ${p.count}` : ""}
              </Text>
            </Pressable>
          );
        })}
        {indexPct !== null ? (
          <View style={styles.chip}>
            <Text style={styles.chipText}>
              Finding places {Math.round(indexPct * 100)}%
            </Text>
          </View>
        ) : null}
      </ScrollView>

      {status === "loading" ? (
        <View style={styles.center}>
          <ActivityIndicator color={C.gold} />
        </View>
      ) : status === "denied" ? (
        <View style={styles.center}>
          <Text style={styles.denied}>
            Photeo needs access to your photos. Grant “Allow all” in Settings and
            reopen this screen.
          </Text>
        </View>
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

      {count > 0 ? (
        <View style={styles.footer}>
          <Text style={styles.footerCount}>{count} selected</Text>
          <Pressable onPress={confirm} style={styles.useBtn}>
            <Text style={styles.useText}>
              Use {count} photo{count === 1 ? "" : "s"}
            </Text>
          </Pressable>
        </View>
      ) : null}
    </GestureHandlerRootView>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: C.bg },
  header: {
    alignItems: "center",
    flexDirection: "row",
    justifyContent: "space-between",
    paddingBottom: 8,
    paddingHorizontal: 18,
    paddingTop: 52,
  },
  back: { color: C.gold, fontSize: 15 },
  count: { color: C.text, fontSize: 14 },
  headerActions: { flexDirection: "row", gap: 16 },
  action: { color: C.gold, fontSize: 14 },
  filterScroll: { flexGrow: 0, maxHeight: 46 },
  filterRow: { alignItems: "center", gap: 7, paddingHorizontal: 14, paddingBottom: 8 },
  chip: {
    alignSelf: "center",
    backgroundColor: C.chip,
    borderRadius: 15,
    paddingHorizontal: 13,
    paddingVertical: 6,
  },
  chipOn: { backgroundColor: C.gold },
  chipText: { color: C.muted, fontSize: 12.5 },
  chipTextOn: { color: "#1a1712", fontWeight: "600" },
  divider: { alignSelf: "center", backgroundColor: C.line, height: 20, width: 1 },
  center: { alignItems: "center", flex: 1, justifyContent: "center", padding: 32 },
  denied: { color: C.muted, fontSize: 15, lineHeight: 22, textAlign: "center" },
  gridWrap: { flex: 1 },
  list: { paddingHorizontal: 0 },
  thumb: { backgroundColor: C.panel, flex: 1 },
  selShade: {
    borderColor: C.gold,
    borderWidth: 3,
    bottom: 0,
    left: 0,
    position: "absolute",
    right: 0,
    top: 0,
  },
  badge: {
    alignItems: "center",
    backgroundColor: C.gold,
    borderRadius: 11,
    height: 22,
    justifyContent: "center",
    minWidth: 22,
    paddingHorizontal: 5,
    position: "absolute",
    right: 5,
    top: 5,
  },
  badgeText: { color: "#1a1712", fontSize: 12, fontWeight: "700" },
  dot: {
    borderColor: "rgba(255,255,255,0.7)",
    borderRadius: 11,
    borderWidth: 1.5,
    height: 22,
    position: "absolute",
    right: 5,
    top: 5,
    width: 22,
  },
  footer: {
    alignItems: "center",
    backgroundColor: C.panel,
    borderTopColor: C.line,
    borderTopWidth: 1,
    flexDirection: "row",
    justifyContent: "space-between",
    paddingBottom: 30,
    paddingHorizontal: 22,
    paddingTop: 16,
  },
  footerCount: { color: C.muted, fontSize: 14 },
  useBtn: {
    backgroundColor: C.gold,
    borderRadius: 8,
    paddingHorizontal: 22,
    paddingVertical: 12,
  },
  useText: { color: "#1a1712", fontSize: 15, fontWeight: "600" },
});
