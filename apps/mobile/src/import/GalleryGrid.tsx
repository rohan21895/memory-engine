import { FlashList } from "@shopify/flash-list";
import { Image } from "expo-image";
import * as MediaLibrary from "expo-media-library/legacy";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  ActivityIndicator,
  Pressable,
  StyleSheet,
  Text,
  useWindowDimensions,
  View,
} from "react-native";

import type { PickedPhoto } from "./picked-photo";

const C = {
  bg: "#141311",
  panel: "#1c1a17",
  line: "#2c2a25",
  text: "#e8e4dc",
  muted: "#9a927f",
  gold: "#c8a24a",
};

const PAGE = 120;
const COLS = 3;
const GAP = 3;

type Props = {
  onConfirm: (photos: PickedPhoto[]) => void;
  onBack: () => void;
};

// expo-media-library hands back asset.uri as `file:///storage/…`, which Android
// 13+ scoped storage BLOCKS (READ_MEDIA_IMAGES grants MediaStore access, not raw
// file paths) — so thumbnails render blank. The MediaStore content:// URI built
// from the id is readable by Glide (expo-image) and expo-image-manipulator.
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

// The Android system Photo Picker hard-caps selection at ~100. This grid reads
// the media library directly (full-access permission) so there is NO cap:
// tap to toggle, order badges show the running order, "Use N" hands them off.
export default function GalleryGrid({ onConfirm, onBack }: Props) {
  const { width } = useWindowDimensions();
  const cell = Math.floor((width - GAP * (COLS - 1)) / COLS);

  const [status, setStatus] = useState<"loading" | "denied" | "ready">("loading");
  const [assets, setAssets] = useState<MediaLibrary.Asset[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const cursor = useRef<string | undefined>(undefined);
  const hasNext = useRef(true);
  const loadingMore = useRef(false);

  const loadPage = useCallback(async () => {
    if (loadingMore.current || !hasNext.current) return;
    loadingMore.current = true;
    const page = await MediaLibrary.getAssetsAsync({
      first: PAGE,
      after: cursor.current,
      mediaType: MediaLibrary.MediaType.photo,
      sortBy: [MediaLibrary.SortBy.creationTime],
    });
    cursor.current = page.endCursor;
    hasNext.current = page.hasNextPage;
    setAssets((prev) => prev.concat(page.assets));
    loadingMore.current = false;
  }, []);

  useEffect(() => {
    (async () => {
      const perm = await MediaLibrary.requestPermissionsAsync();
      if (perm.status !== "granted") {
        setStatus("denied");
        return;
      }
      await loadPage();
      setStatus("ready");
    })();
  }, [loadPage]);

  const toggle = useCallback((id: string) => {
    setSelected((prev) =>
      prev.includes(id) ? prev.filter((x) => x !== id) : prev.concat(id),
    );
  }, []);

  const confirm = useCallback(() => {
    const byId = new Map(assets.map((a) => [a.id, a]));
    const picked = selected
      .map((id) => byId.get(id))
      .filter((a): a is MediaLibrary.Asset => Boolean(a))
      .map(toPicked);
    onConfirm(picked);
  }, [assets, selected, onConfirm]);

  const renderItem = useCallback(
    ({ item }: { item: MediaLibrary.Asset }) => {
      const order = selected.indexOf(item.id);
      const isSel = order >= 0;
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
          {isSel ? (
            <>
              <View style={styles.selShade} />
              <View style={styles.badge}>
                <Text style={styles.badgeText}>{order + 1}</Text>
              </View>
            </>
          ) : (
            <View style={styles.dot} />
          )}
        </Pressable>
      );
    },
    [cell, selected, toggle],
  );

  return (
    <View style={styles.root}>
      <View style={styles.header}>
        <Pressable onPress={onBack} hitSlop={12}>
          <Text style={styles.back}>‹ Back</Text>
        </Pressable>
        <Text style={styles.title}>Your photos</Text>
        <Text style={styles.count}>{selected.length} selected</Text>
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
        <FlashList
          data={assets}
          renderItem={renderItem}
          keyExtractor={(item) => item.id}
          numColumns={COLS}
          onEndReached={loadPage}
          onEndReachedThreshold={1.2}
          contentContainerStyle={styles.list}
        />
      )}

      {selected.length > 0 ? (
        <View style={styles.footer}>
          <Pressable onPress={() => setSelected([])} hitSlop={8}>
            <Text style={styles.clear}>Clear</Text>
          </Pressable>
          <Pressable onPress={confirm} style={styles.useBtn}>
            <Text style={styles.useText}>
              Use {selected.length} photo{selected.length === 1 ? "" : "s"}
            </Text>
          </Pressable>
        </View>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: C.bg },
  header: {
    alignItems: "center",
    flexDirection: "row",
    justifyContent: "space-between",
    paddingHorizontal: 18,
    paddingTop: 60,
    paddingBottom: 14,
  },
  back: { color: C.gold, fontSize: 16 },
  title: { color: C.text, fontSize: 16 },
  count: { color: C.muted, fontSize: 13 },
  center: { alignItems: "center", flex: 1, justifyContent: "center", padding: 32 },
  denied: { color: C.muted, fontSize: 15, lineHeight: 22, textAlign: "center" },
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
    gap: 16,
    justifyContent: "space-between",
    paddingBottom: 30,
    paddingHorizontal: 22,
    paddingTop: 16,
  },
  clear: { color: C.muted, fontSize: 15 },
  useBtn: {
    backgroundColor: C.gold,
    borderRadius: 8,
    paddingHorizontal: 22,
    paddingVertical: 12,
  },
  useText: { color: "#1a1712", fontSize: 15, fontWeight: "600" },
});
