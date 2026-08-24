import { Image } from "expo-image";
import { Pressable, ScrollView, StatusBar, StyleSheet, Text, View } from "react-native";

import type { SavedAlbum } from "../../albums/album-store";
import { PrimaryButton } from "../components/PrimaryButton";
import { fonts } from "../fonts";
import { colors, layout, radii, spacing, typeScale } from "../tokens";

export type SharedAlbumPreview = {
  id: string;
  title: string;
  sharedBy: string;
  photoCount: number;
  color: string;
};

export const sharedAlbumPreviews: SharedAlbumPreview[] = [
  { id: "shared-ellie-wedding", title: "Ellie’s wedding day", sharedBy: "Ellie", photoCount: 42, color: "#b98266" },
  { id: "shared-grandpa-80", title: "Grandpa’s 80th", sharedBy: "David", photoCount: 19, color: "#829a91" },
];

function albumMeta(album: SavedAlbum) {
  const start = album.dateRange.start ? new Date(album.dateRange.start) : null;
  const month = start && !Number.isNaN(start.getTime())
    ? start.toLocaleDateString(undefined, { month: "short", year: "numeric" })
    : new Date(album.createdAt).toLocaleDateString(undefined, { month: "short", year: "numeric" });
  return `${album.photos.length} photos · ${month}`;
}

export function AlbumsScreen({
  albums,
  message,
  onCreate,
  onOpen,
  onOpenShared,
}: {
  albums: SavedAlbum[];
  message?: string | null;
  onCreate: () => void;
  onOpen: (album: SavedAlbum) => void;
  onOpenShared: (album: SharedAlbumPreview) => void;
}) {
  return (
    <View style={styles.root}>
      <ScrollView contentContainerStyle={styles.scroll} contentInsetAdjustmentBehavior="automatic">
        <View style={styles.header}>
          <Text accessibilityRole="header" style={styles.title}>Albums</Text>
          <View style={styles.avatar}><Text style={styles.avatarText}>P</Text></View>
        </View>
        {albums.length > 0 ? (
          <>
            <View style={styles.grid}>
              {albums.map((album) => (
                <Pressable accessibilityRole="button" key={album.id} onPress={() => onOpen(album)} style={({ pressed }) => [styles.albumCard, pressed ? styles.pressed : null]}>
                  <Image cachePolicy="memory-disk" contentFit="cover" source={album.coverUri} style={styles.cover} transition={120} />
                  <Text numberOfLines={1} style={styles.albumTitle}>{album.title}</Text>
                  <Text style={styles.albumMeta}>{albumMeta(album)}</Text>
                </Pressable>
              ))}
            </View>
            <View style={styles.sharedHeading}><Text style={styles.sharedTitle}>Shared with you</Text><Text style={styles.sharedNote}>from other Photeo phones</Text></View>
            <View style={styles.sharedList}>
              {sharedAlbumPreviews.map((album) => (
                <Pressable accessibilityRole="button" key={album.id} onPress={() => onOpenShared(album)} style={({ pressed }) => [styles.sharedCard, pressed ? styles.pressed : null]}>
                  <View style={[styles.sharedCover, { backgroundColor: album.color }]}>
                    <View style={styles.sharedGlow} />
                    <Text style={styles.sharedInitial}>{album.sharedBy.slice(0, 1)}</Text>
                  </View>
                  <View style={styles.sharedCopy}>
                    <Text numberOfLines={1} style={styles.albumTitle}>{album.title}</Text>
                    <Text style={styles.albumMeta}>Shared by {album.sharedBy} · {album.photoCount} photos</Text>
                  </View>
                  <Text style={styles.chevron}>›</Text>
                </Pressable>
              ))}
            </View>
          </>
        ) : (
          <View style={styles.empty}>
            <View style={styles.emptyMark}><View style={styles.emptyPaper} /></View>
            <Text style={styles.emptyTitle}>No albums yet</Text>
            <Text style={styles.emptyCopy}>Make your first one. Pick a few photos and Photeo does the rest.</Text>
          </View>
        )}
        {message ? <Text accessibilityLiveRegion="polite" style={styles.error}>{message}</Text> : null}
      </ScrollView>
      <View style={styles.footer}>
        <PrimaryButton accessibilityHint="Starts the album photo picker" label="＋ Create new album" onPress={onCreate} />
        <Text style={styles.note}>Takes about a minute. Stays on your phone.</Text>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  albumCard: { paddingBottom: spacing.sm, width: "47.8%" },
  albumMeta: { color: colors.muted, fontFamily: fonts.regular, ...typeScale.small },
  albumTitle: { color: colors.text, fontFamily: fonts.bold, fontSize: 16, letterSpacing: -0.2, paddingTop: spacing.xs },
  avatar: { alignItems: "center", backgroundColor: "#d9a184", borderRadius: 19, height: 38, justifyContent: "center", width: 38 },
  avatarText: { color: colors.onAccent, fontFamily: fonts.bold, ...typeScale.small },
  cover: { aspectRatio: 1, backgroundColor: colors.quietSurface, borderCurve: "continuous", borderRadius: radii.md, width: "100%" },
  empty: { alignItems: "center", gap: spacing.sm, paddingHorizontal: spacing.sm, paddingTop: spacing.xxl },
  emptyCopy: { color: colors.muted, fontFamily: fonts.regular, textAlign: "center", ...typeScale.body },
  emptyMark: { alignItems: "center", backgroundColor: colors.quietSurface, borderCurve: "continuous", borderRadius: 26, height: 96, justifyContent: "center", width: 96 },
  emptyPaper: { backgroundColor: "#ddd7cc", borderCurve: "continuous", borderRadius: 12, height: 40, width: 40 },
  emptyTitle: { color: colors.text, fontFamily: fonts.extraBold, ...typeScale.subtitle },
  error: { color: colors.error, fontFamily: fonts.medium, textAlign: "center", ...typeScale.small },
  footer: { backgroundColor: "rgba(250,248,245,0.97)", borderTopColor: colors.hairline, borderTopWidth: 1, bottom: 0, left: 0, paddingBottom: spacing.sm, paddingHorizontal: layout.screenPadding, paddingTop: spacing.sm, position: "absolute", right: 0 },
  grid: { flexDirection: "row", flexWrap: "wrap", gap: 14, paddingTop: spacing.md },
  header: { alignItems: "center", flexDirection: "row", justifyContent: "space-between", paddingBottom: spacing.xs },
  note: { color: colors.muted, fontFamily: fonts.regular, textAlign: "center", ...typeScale.small },
  pressed: { opacity: 0.72, transform: [{ scale: 0.985 }] },
  root: { backgroundColor: colors.background, flex: 1 },
  scroll: { paddingBottom: 126, paddingHorizontal: layout.screenPadding, paddingTop: (StatusBar.currentHeight ?? 24) + spacing.md },
  sharedHeading: { alignItems: "baseline", flexDirection: "row", gap: spacing.xs, paddingTop: spacing.lg },
  sharedList: { gap: spacing.sm, paddingTop: spacing.sm },
  sharedCard: { alignItems: "center", backgroundColor: colors.panel, borderColor: colors.hairline, borderRadius: radii.md, borderWidth: 1, flexDirection: "row", gap: spacing.sm, padding: spacing.sm },
  sharedCover: { alignItems: "center", borderRadius: 13, height: 64, justifyContent: "center", overflow: "hidden", width: 64 },
  sharedGlow: { backgroundColor: "rgba(255,255,255,.2)", borderRadius: 30, height: 60, left: -18, position: "absolute", top: -20, width: 60 },
  sharedInitial: { color: colors.onAccent, fontFamily: fonts.extraBold, fontSize: 22 },
  sharedCopy: { flex: 1 },
  chevron: { color: colors.muted, fontFamily: fonts.regular, fontSize: 24 },
  sharedNote: { color: colors.muted, fontFamily: fonts.regular, ...typeScale.small },
  sharedTitle: { color: colors.text, fontFamily: fonts.extraBold, fontSize: 19 },
  title: { color: colors.text, fontFamily: fonts.extraBold, ...typeScale.title },
});
