import { Image } from "expo-image";
import { ActivityIndicator, Pressable, ScrollView, StatusBar, StyleSheet, Text, useWindowDimensions, View } from "react-native";

import { albumSubtitle, type SavedAlbum } from "../../albums/album-store";
import { PrimaryButton } from "../components/PrimaryButton";
import { fonts } from "../fonts";
import { gridItemWidth } from "../grid-width";
import { colors, layout, radii, spacing, typeScale } from "../tokens";

const GRID_COLUMNS = 2;
const GRID_GAP = 14;

export type SharedAlbumPreview = {
  id: string;
  title: string;
  sharedBy: string;
  photoCount: number;
  color: string;
};

// Moved to album-store as `albumSubtitle` so the album screen shows the same
// date this card does. They used to disagree.
const albumMeta = albumSubtitle;

export function AlbumsScreen({
  albums,
  loading = false,
  message,
  onCreate,
  onOpen,
}: {
  albums: SavedAlbum[];
  /** True until the shelf has been read off disk once, so "no albums yet" is honest. */
  loading?: boolean;
  message?: string | null;
  onCreate: () => void;
  onOpen: (album: SavedAlbum) => void;
}) {
  // Measured, not a percentage. See `gridItemWidth`: "47.8%" twice plus the
  // 14dp gap came to 316.096dp inside a 316dp row on this phone, so every card
  // wrapped onto its own line and half the shelf was empty.
  const { width } = useWindowDimensions();
  const cardWidth = gridItemWidth(
    width - layout.screenPadding * 2,
    GRID_COLUMNS,
    GRID_GAP,
  );
  return (
    <View style={styles.root}>
      <ScrollView contentContainerStyle={styles.scroll} contentInsetAdjustmentBehavior="automatic">
        <View style={styles.header}>
          <Text accessibilityRole="header" style={styles.title}>Albums</Text>
          <View style={styles.avatar}><Text style={styles.avatarText}>P</Text></View>
        </View>
        {loading ? (
          <View accessibilityLiveRegion="polite" style={styles.loading}>
            <ActivityIndicator color={colors.gold} />
            <Text style={styles.loadingText}>Loading your albums…</Text>
          </View>
        ) : albums.length > 0 ? (
          <View style={styles.grid}>
            {albums.map((album) => (
              <Pressable accessibilityHint="Opens this album" accessibilityLabel={`${album.title}. ${albumMeta(album)}`} accessibilityRole="button" key={album.id} onPress={() => onOpen(album)} style={({ pressed }) => [styles.albumCard, { width: cardWidth }, pressed ? styles.pressed : null]}>
                {/* Top-anchored for the same reason as the album hero: a square
                    card cropping a tall portrait from the centre keeps torsos
                    and drops faces. */}
                <Image cachePolicy="memory-disk" contentFit="cover" contentPosition="top" source={album.coverUri} style={styles.cover} transition={120} />
                <Text numberOfLines={1} style={styles.albumTitle}>{album.title}</Text>
                <Text style={styles.albumMeta}>{albumMeta(album)}</Text>
              </Pressable>
            ))}
          </View>
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
  albumCard: { paddingBottom: spacing.sm },
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
  grid: { flexDirection: "row", flexWrap: "wrap", gap: GRID_GAP, paddingTop: spacing.md },
  header: { alignItems: "center", flexDirection: "row", justifyContent: "space-between", paddingBottom: spacing.xs },
  loading: { alignItems: "center", gap: spacing.sm, paddingTop: spacing.xxl },
  loadingText: { color: colors.muted, fontFamily: fonts.regular, ...typeScale.small },
  note: { color: colors.muted, fontFamily: fonts.regular, textAlign: "center", ...typeScale.small },
  pressed: { opacity: 0.72, transform: [{ scale: 0.985 }] },
  root: { backgroundColor: colors.background, flex: 1 },
  scroll: { paddingBottom: 126, paddingHorizontal: layout.screenPadding, paddingTop: (StatusBar.currentHeight ?? 24) + spacing.md },
  title: { color: colors.text, fontFamily: fonts.extraBold, ...typeScale.title },
});
