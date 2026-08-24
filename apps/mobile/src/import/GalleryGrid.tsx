import { FlashList } from "@shopify/flash-list";
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

type Props = {
  onConfirm: (photos: PickedPhoto[]) => void;
  onBack: () => void;
};

type DatePreset = "all" | "week" | "month" | "year";
const DATE_PRESETS: { key: DatePreset; label: string }[] = [
  { key: "all", label: "All" },
  { key: "week", label: "This week" },
  { key: "month", label: "This month" },
  { key: "year", label: "This year" },
];

type AlbumChip = { id: string | null; title: string; count: number };

// expo-media-library hands back asset.uri as `file:///storage/…`, which Android
// 13+ scoped storage BLOCKS — use the MediaStore content:// URI (Glide-readable).
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

// Our own picker: full-library grid, filter by date/album, slide-to-select
// (long-press then drag), and select-all — none of which the OS/Google pickers
// allow. Reads MediaStore directly via full-access permission.
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
  const [busy, setBusy] = useState(false);

  const cursor = useRef<string | undefined>(undefined);
  const hasNext = useRef(true);
  const loadingMore = useRef(false);
  const scrollY = useRef(0);
  const assetsRef = useRef<MediaLibrary.Asset[]>([]);
  assetsRef.current = assets;

  const queryOpts = useCallback(
    (): MediaLibrary.AssetsOptions => ({
      mediaType: [MediaLibrary.MediaType.photo],
      sortBy: [MediaLibrary.SortBy.creationTime],
      createdAfter: createdAfterFor(datePreset),
      ...(albumId ? { album: albumId } : {}),
    }),
    [datePreset, albumId],
  );

  const reload = useCallback(async () => {
    cursor.current = undefined;
    hasNext.current = true;
    loadingMore.current = true;
    setAssets([]);
    const page = await MediaLibrary.getAssetsAsync({ first: PAGE, ...queryOpts() });
    cursor.current = page.endCursor;
    hasNext.current = page.hasNextPage;
    setAssets(page.assets);
    loadingMore.current = false;
  }, [queryOpts]);

  const loadMore = useCallback(async () => {
    if (loadingMore.current || !hasNext.current) return;
    loadingMore.current = true;
    const page = await MediaLibrary.getAssetsAsync({
      first: PAGE,
      after: cursor.current,
      ...queryOpts(),
    });
    cursor.current = page.endCursor;
    hasNext.current = page.hasNextPage;
    setAssets((prev) => prev.concat(page.assets));
    loadingMore.current = false;
  }, [queryOpts]);

  // permission + albums, once
  useEffect(() => {
    (async () => {
      const perm = await MediaLibrary.requestPermissionsAsync();
      if (perm.status !== "granted") {
        setStatus("denied");
        return;
      }
      const found = await MediaLibrary.getAlbumsAsync({
        includeSmartAlbums: true,
      });
      const chips: AlbumChip[] = found
        .filter((a) => (a.assetCount ?? 0) > 0)
        .sort((a, b) => (b.assetCount ?? 0) - (a.assetCount ?? 0))
        .slice(0, 24)
        .map((a) => ({ id: a.id, title: a.title, count: a.assetCount ?? 0 }));
      setAlbums([{ id: null, title: "All photos", count: 0 }, ...chips]);
      setStatus("ready");
    })();
  }, []);

  // reload when filters change (after ready)
  useEffect(() => {
    if (status !== "ready") return;
    void reload();
  }, [status, datePreset, albumId, reload]);

  const selectAll = useCallback(async () => {
    setBusy(true);
    try {
      const ids = new Set<string>();
      let after: string | undefined;
      let more = true;
      while (more && ids.size < SELECT_ALL_CAP) {
        const page = await MediaLibrary.getAssetsAsync({
          first: 500,
          after,
          ...queryOpts(),
        });
        for (const a of page.assets) ids.add(a.id);
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

  // ---- slide-to-select (long-press then drag) ----
  const dragAnchor = useRef<number | null>(null);
  const dragMode = useRef<"add" | "remove">("add");
  const dragSnapshot = useRef<Set<string>>(new Set());

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

  const pan = useMemo(
    () =>
      Gesture.Pan()
        .runOnJS(true)
        .activateAfterLongPress(180)
        .maxPointers(1)
        .onStart((e) => beginDrag(e.x, e.y))
        .onUpdate((e) => updateDrag(e.x, e.y))
        .onEnd(() => {
          dragAnchor.current = null;
        })
        .onFinalize(() => {
          dragAnchor.current = null;
        }),
    [beginDrag, updateDrag],
  );

  const orderMap = useMemo(() => {
    const m = new Map<string, number>();
    let n = 0;
    for (const a of assets) if (selected.has(a.id)) m.set(a.id, ++n);
    return m;
  }, [assets, selected]);

  const confirm = useCallback(() => {
    const picked = assets.filter((a) => selected.has(a.id)).map(toPicked);
    onConfirm(picked);
  }, [assets, selected, onConfirm]);

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
      <View style={styles.header}>
        <Pressable onPress={onBack} hitSlop={12}>
          <Text style={styles.back}>‹ Back</Text>
        </Pressable>
        <Text style={styles.title}>Your photos</Text>
        <Text style={styles.count}>{count} selected</Text>
      </View>

      {/* date filter */}
      <View style={styles.filterRow}>
        {DATE_PRESETS.map((p) => (
          <Pressable
            key={p.key}
            onPress={() => setDatePreset(p.key)}
            style={[styles.chip, datePreset === p.key && styles.chipOn]}
          >
            <Text style={[styles.chipText, datePreset === p.key && styles.chipTextOn]}>
              {p.label}
            </Text>
          </Pressable>
        ))}
      </View>

      {/* album filter */}
      {albums.length > 1 ? (
        <ScrollView
          horizontal
          showsHorizontalScrollIndicator={false}
          style={styles.albumScroll}
          contentContainerStyle={styles.albumRow}
        >
          {albums.map((a) => {
            const on = albumId === a.id;
            return (
              <Pressable
                key={a.id ?? "all"}
                onPress={() => setAlbumId(a.id)}
                style={[styles.albumChip, on && styles.chipOn]}
              >
                <Text style={[styles.chipText, on && styles.chipTextOn]}>
                  {a.title}
                  {a.count ? ` · ${a.count}` : ""}
                </Text>
              </Pressable>
            );
          })}
        </ScrollView>
      ) : null}

      {/* actions */}
      <View style={styles.actions}>
        <Text style={styles.hint}>Long-press + drag to sweep-select</Text>
        <View style={styles.actionBtns}>
          <Pressable onPress={selectAll} hitSlop={8} disabled={busy}>
            <Text style={styles.action}>{busy ? "…" : "Select all"}</Text>
          </Pressable>
          {count > 0 ? (
            <Pressable onPress={() => setSelected(new Set())} hitSlop={8}>
              <Text style={styles.action}>Clear</Text>
            </Pressable>
          ) : null}
        </View>
      </View>

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
          <View style={styles.gridWrap}>
            <FlashList
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
    paddingHorizontal: 18,
    paddingTop: 58,
    paddingBottom: 10,
  },
  back: { color: C.gold, fontSize: 16 },
  title: { color: C.text, fontSize: 16 },
  count: { color: C.muted, fontSize: 13 },
  filterRow: {
    flexDirection: "row",
    gap: 8,
    paddingHorizontal: 16,
    paddingBottom: 8,
  },
  chip: {
    backgroundColor: C.chip,
    borderRadius: 16,
    paddingHorizontal: 14,
    paddingVertical: 7,
  },
  chipOn: { backgroundColor: C.gold },
  chipText: { color: C.muted, fontSize: 13 },
  chipTextOn: { color: "#1a1712", fontWeight: "600" },
  albumScroll: { flexGrow: 0 },
  albumRow: { alignItems: "center", gap: 8, paddingHorizontal: 16, paddingBottom: 8 },
  albumChip: {
    alignSelf: "center",
    backgroundColor: C.chip,
    borderRadius: 16,
    paddingHorizontal: 14,
    paddingVertical: 7,
  },
  actions: {
    alignItems: "center",
    flexDirection: "row",
    justifyContent: "space-between",
    paddingBottom: 8,
    paddingHorizontal: 18,
  },
  hint: { color: C.muted, fontSize: 11.5 },
  actionBtns: { flexDirection: "row", gap: 18 },
  action: { color: C.gold, fontSize: 14 },
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
