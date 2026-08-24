import { Image } from "expo-image";
import { Pressable, ScrollView, StatusBar, StyleSheet, Text, View } from "react-native";

import type { SavedAlbum } from "./album-store";
import { colors, fonts, spacing, typeScale } from "../ui";

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
  onPrint,
  onShare,
}: {
  album: SavedAlbum;
  onBack: () => void;
  onManage: () => void;
  onPlay: () => void;
  onPrint: () => void;
  onShare: () => void;
}) {
  const created = new Date(album.createdAt).toLocaleDateString(undefined, { month: "short", year: "numeric" });
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
          <PillButton label="▸  Play" onPress={onPlay} />
          <PillButton label="Share" onPress={onShare} />
        </View>
        <Pressable accessibilityRole="button" onPress={onPrint} style={({ pressed }) => [styles.print, pressed ? styles.pressed : null]}>
          <View style={styles.book} />
          <View style={styles.printCopy}>
            <Text style={styles.printEyebrow}>Make it real</Text>
            <Text style={styles.printTitle}>Print this as a photo book</Text>
            <Text style={styles.printMeta}>Posted to your door · from £28</Text>
          </View>
          <Text style={styles.printArrow}>›</Text>
        </Pressable>
        <View style={styles.gridHeading}><Text style={styles.gridTitle}>All {album.photos.length} photos</Text><Text style={styles.gridHint}>Tap a photo to see it big</Text></View>
        <View style={styles.grid}>
          {album.photos.map((photo, index) => (
            <Image cachePolicy="memory-disk" contentFit="cover" key={`${photo.media_id}-${index}`} source={photo.uri} style={styles.tile} />
          ))}
        </View>
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  actions: { flexDirection: "row", gap: spacing.sm, paddingHorizontal: spacing.lg, paddingTop: 18 },
  back: { alignItems: "center", backgroundColor: "rgba(255,255,255,.9)", borderRadius: 20, height: 40, justifyContent: "center", left: spacing.md, position: "absolute", top: (StatusBar.currentHeight ?? 24) + spacing.xs, width: 40 },
  backText: { color: colors.text, fontFamily: fonts.regular, fontSize: 28 },
  book: { backgroundColor: "#e3cdb4", borderRadius: 5, height: 64, width: 52 },
  edit: { alignItems: "center", backgroundColor: "rgba(255,255,255,.9)", borderRadius: 20, height: 40, justifyContent: "center", paddingHorizontal: spacing.md, position: "absolute", right: spacing.md, top: (StatusBar.currentHeight ?? 24) + spacing.xs },
  editText: { color: colors.text, fontFamily: fonts.bold, ...typeScale.small },
  grid: { flexDirection: "row", flexWrap: "wrap", gap: 3 },
  gridHeading: { alignItems: "baseline", flexDirection: "row", justifyContent: "space-between", paddingHorizontal: spacing.lg, paddingVertical: spacing.md },
  gridHint: { color: colors.muted, fontFamily: fonts.regular, ...typeScale.eyebrow },
  gridTitle: { color: colors.text, fontFamily: fonts.bold, ...typeScale.label },
  hero: { height: 330, position: "relative" },
  heroCopy: { bottom: 0, left: 0, padding: spacing.lg, position: "absolute", right: 0 },
  meta: { color: "rgba(255,255,255,.86)", fontFamily: fonts.regular, ...typeScale.small },
  pill: { alignItems: "center", backgroundColor: "#efece5", borderRadius: 26, flex: 1, height: 52, justifyContent: "center" },
  pillText: { color: colors.text, fontFamily: fonts.bold, ...typeScale.label },
  pressed: { opacity: 0.72, transform: [{ scale: 0.985 }] },
  print: { alignItems: "center", backgroundColor: colors.gold, borderCurve: "continuous", borderRadius: 20, flexDirection: "row", gap: 14, marginHorizontal: spacing.lg, marginTop: spacing.sm, padding: spacing.md },
  printArrow: { color: colors.onAccent, fontFamily: fonts.regular, fontSize: 24 },
  printCopy: { flex: 1 },
  printEyebrow: { color: colors.onAccent, fontFamily: fonts.bold, opacity: 0.78, textTransform: "uppercase", ...typeScale.eyebrow },
  printMeta: { color: colors.onAccent, fontFamily: fonts.regular, opacity: 0.85, ...typeScale.small },
  printTitle: { color: colors.onAccent, fontFamily: fonts.extraBold, fontSize: 19 },
  root: { backgroundColor: colors.background, flex: 1 },
  scrim: { backgroundColor: "rgba(25,17,12,.38)", bottom: 0, height: 150, left: 0, position: "absolute", right: 0 },
  scroll: { paddingBottom: spacing.xl },
  tile: { aspectRatio: 1, backgroundColor: colors.hairline, width: "32.8%" },
  title: { color: colors.onAccent, fontFamily: fonts.extraBold, fontSize: 29, letterSpacing: -0.8 },
});
