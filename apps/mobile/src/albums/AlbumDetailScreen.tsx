import { Image } from "expo-image";
import { useEffect, useMemo, useState } from "react";
import { Pressable, ScrollView, StatusBar, StyleSheet, Text, View } from "react-native";

import { albumAnalysisProxy } from "../../modules/photeo-scan-service/src";
import { aspectRatioOf, balanceIntoColumns } from "./album-wall";
import type { SavedAlbum } from "./album-store";
import Lightbox, { type LightboxItem } from "../review/Lightbox";
import { colors, fonts, spacing, typeScale } from "../ui";

/** Two, not three: a wall of whole photos wants size more than it wants density. */
const WALL_COLUMNS = 2;
/** Matches ANALYSIS_PROXY_SIZE, so a built album's proxies are already on disk. */
const TILE_EDGE = 1280;

type Frame = { uri: string; width: number; height: number };
const frameCache = new Map<string, Frame | null>();

/**
 * Real pixel dimensions for the album's photos.
 *
 * `SavedAlbum.photos` carries `width`/`height` only optionally, and on this
 * library they are simply absent -- the picker never filled them in. Without
 * them `aspectRatioOf` answers "square" for everything, so the wall laid every
 * portrait photo inside a square tile and letterboxed it with grey bars down
 * both sides. That is what "the image quality in view album is poor" looked
 * like: not a compression problem, a geometry one.
 *
 * The native proxy already returns exact dimensions alongside a bounded 1280px
 * JPEG, and an album that was just built has those proxies cached on disk, so
 * this is usually a file read rather than a decode. Photos keep their stored
 * dimensions when they have them; this only fills the gaps.
 */
function useAlbumFrames(photos: SavedAlbum["photos"]): Map<string, Frame> {
  const [frames, setFrames] = useState<Map<string, Frame>>(() => {
    const initial = new Map<string, Frame>();
    for (const photo of photos) {
      const cached = frameCache.get(photo.media_id);
      if (cached) initial.set(photo.media_id, cached);
    }
    return initial;
  });

  useEffect(() => {
    let live = true;
    void (async () => {
      for (const photo of photos) {
        if (frameCache.has(photo.media_id)) continue;
        const proxy = await albumAnalysisProxy(photo.media_id, TILE_EDGE);
        if (!live) return;
        frameCache.set(photo.media_id, proxy ?? null);
        if (proxy) {
          setFrames((current) => new Map(current).set(photo.media_id, proxy));
        }
      }
    })();
    return () => {
      live = false;
    };
  }, [photos]);

  return frames;
}

function PillButton({ label, onPress }: { label: string; onPress: () => void }) {
  return (
    <Pressable accessibilityRole="button" onPress={onPress} style={({ pressed }) => [styles.pill, pressed ? styles.pressed : null]}>
      <Text style={styles.pillText}>{label}</Text>
    </Pressable>
  );
}

export function AlbumDetailScreen({
  album,
  onBack,
  onManage,
  onPlay,
  onRead,
}: {
  album: SavedAlbum;
  onBack: () => void;
  onManage: () => void;
  onPlay: () => void;
  onRead: () => void;
  onPrint: () => void;
  onShare: () => void;
}) {
  const created = new Date(album.createdAt).toLocaleDateString(undefined, { month: "short", year: "numeric" });
  const [viewerIndex, setViewerIndex] = useState<number | null>(null);
  const frames = useAlbumFrames(album.photos);

  // Measured dimensions win over the stored ones, which are usually absent.
  const sized = useMemo(
    () => album.photos.map((photo) => {
      const frame = frames.get(photo.media_id);
      return frame ? { ...photo, width: frame.width, height: frame.height } : photo;
    }),
    [album.photos, frames],
  );
  const columns = useMemo(() => balanceIntoColumns(sized, WALL_COLUMNS), [sized]);

  // Full screen shows the ORIGINAL, not the tile's proxy: this is the one place
  // the photo is the whole point, and Lightbox already fits rather than crops.
  const viewerItems = useMemo<LightboxItem[]>(
    () => album.photos.map((photo) => ({
      caption: "",
      media_id: photo.media_id,
      rawReasons: [],
      uri: photo.uri,
    })),
    [album.photos],
  );
  const indexOf = (mediaId: string) =>
    album.photos.findIndex((photo) => photo.media_id === mediaId);
  return (
    <View style={styles.root}>
      <StatusBar backgroundColor="transparent" barStyle="light-content" translucent />
      <ScrollView contentContainerStyle={styles.scroll}>
        <View style={styles.hero}>
          <Image cachePolicy="memory-disk" contentFit="cover" source={album.coverUri} style={StyleSheet.absoluteFill} />
          <View style={styles.scrim} />
          <Pressable accessibilityLabel="Back to albums" accessibilityRole="button" onPress={onBack} style={styles.back}><Text style={styles.backText}>‹</Text></Pressable>
          <Pressable accessibilityRole="button" onPress={onManage} style={styles.edit}><Text style={styles.editText}>Edit</Text></Pressable>
          <View style={styles.heroCopy}>
            <Text style={styles.title}>{album.title}</Text>
            <Text style={styles.meta}>{album.photos.length} photos · {created}</Text>
          </View>
        </View>
        <View style={styles.actions}>
          <PillButton label="Read album" onPress={onRead} />
          <PillButton label="▶  Play slideshow" onPress={onPlay} />
        </View>
        <View style={styles.gridHeading}><Text style={styles.gridTitle}>All {album.photos.length} photos</Text></View>
        {/*
          A wall, not a grid. Every tile carries its photo's own aspect ratio, so
          nothing is cropped to fit a square -- which is what "images are cut to
          fit in required size" was describing. `contain` rather than `cover` is
          the safety net for photos whose source never reported dimensions: they
          fall back to square and must letterbox instead of losing their edges.
        */}
        <View style={styles.wall}>
          {columns.map((column, columnIndex) => (
            <View key={columnIndex} style={styles.wallColumn}>
              {column.items.map((photo, index) => {
                const frame = frames.get(photo.media_id);
                return (
                  <Pressable
                    accessibilityHint="Opens this photo full screen"
                    accessibilityLabel={`Photo ${indexOf(photo.media_id) + 1} of ${album.photos.length}`}
                    accessibilityRole="button"
                    key={`${photo.media_id}-${index}`}
                    onPress={() => setViewerIndex(indexOf(photo.media_id))}
                    style={({ pressed }) => (pressed ? styles.pressedTile : null)}
                  >
                    {/*
                      `cover` is safe again now that the tile carries the photo's
                      OWN ratio: the box and the image agree, so nothing is cut,
                      and unlike `contain` a rounding error shows no grey seam.
                      The tile draws the bounded proxy; tapping opens the original.
                    */}
                    <Image
                      cachePolicy="memory-disk"
                      contentFit="cover"
                      source={frame?.uri ?? photo.uri}
                      style={[styles.tile, { aspectRatio: aspectRatioOf(photo) }]}
                      transition={140}
                    />
                  </Pressable>
                );
              })}
            </View>
          ))}
        </View>
      </ScrollView>

      {/*
        Album photos were never tappable -- not in this wall and not on the
        Album Ready screen. "mini images when clicked should always show me full
        screen images" was describing a control that simply did not exist. The
        viewer already existed and fits rather than crops; it just was not wired
        to anything outside the review flow.
      */}
      <Lightbox
        initialIndex={viewerIndex ?? 0}
        items={viewerItems}
        mode="browse-album"
        onClose={() => setViewerIndex(null)}
        visible={viewerIndex !== null}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  actions: { flexDirection: "row", gap: spacing.sm, paddingHorizontal: spacing.lg, paddingTop: 18 },
  back: { alignItems: "center", backgroundColor: "rgba(255,255,255,.9)", borderRadius: 20, height: 40, justifyContent: "center", left: spacing.md, position: "absolute", top: (StatusBar.currentHeight ?? 24) + spacing.xs, width: 40 },
  backText: { color: colors.text, fontFamily: fonts.regular, fontSize: 28 },
  edit: { alignItems: "center", backgroundColor: "rgba(255,255,255,.9)", borderRadius: 20, height: 40, justifyContent: "center", paddingHorizontal: spacing.md, position: "absolute", right: spacing.md, top: (StatusBar.currentHeight ?? 24) + spacing.xs },
  editText: { color: colors.text, fontFamily: fonts.bold, ...typeScale.small },
  wall: { flexDirection: "row", gap: 4, paddingHorizontal: 4 },
  wallColumn: { flex: 1, gap: 4 },
  gridHeading: { alignItems: "baseline", flexDirection: "row", justifyContent: "space-between", paddingHorizontal: spacing.lg, paddingVertical: spacing.md },
  gridTitle: { color: colors.text, fontFamily: fonts.bold, ...typeScale.label },
  hero: { height: 330, position: "relative" },
  heroCopy: { bottom: 0, left: 0, padding: spacing.lg, position: "absolute", right: 0 },
  meta: { color: "rgba(255,255,255,.86)", fontFamily: fonts.regular, ...typeScale.small },
  pill: { alignItems: "center", backgroundColor: "#efece5", borderRadius: 26, flex: 1, height: 52, justifyContent: "center" },
  pillText: { color: colors.text, fontFamily: fonts.bold, ...typeScale.label },
  pressed: { opacity: 0.72, transform: [{ scale: 0.985 }] },
  pressedTile: { opacity: 0.82 },
  root: { backgroundColor: colors.background, flex: 1 },
  scrim: { backgroundColor: "rgba(25,17,12,.38)", bottom: 0, height: 150, left: 0, position: "absolute", right: 0 },
  scroll: { paddingBottom: spacing.xl },
  tile: { backgroundColor: colors.hairline, borderCurve: "continuous", borderRadius: 4, width: "100%" },
  title: { color: colors.onAccent, fontFamily: fonts.extraBold, fontSize: 29, letterSpacing: -0.8 },
});
