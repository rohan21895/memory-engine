import { Image } from "expo-image";
import { useState } from "react";
import { Pressable, StatusBar, StyleSheet, Text, TextInput, View } from "react-native";

import { colors, fonts, spacing, typeScale } from "../ui";

export type FinalPhoto = { media_id: string; uri: string; page: number };

function QuietAction({ label, onPress }: { label: string; onPress?: () => void }) {
  return (
    <Pressable accessibilityRole="button" onPress={onPress} style={({ pressed }) => [styles.quietAction, pressed ? styles.pressed : null]}>
      <Text style={styles.quietActionText}>{label}</Text>
    </Pressable>
  );
}

export default function FinalAlbum({
  photos,
  onRestart,
  onBack,
  onDone,
  onOpen,
  onPlay,
  onPrint,
  onShare,
  onTitleChange,
  title = "My photo album",
}: {
  photos: FinalPhoto[];
  onRestart: () => void;
  onBack: () => void;
  onDone?: () => void;
  onOpen?: () => void;
  onPlay?: () => void;
  onPrint?: () => void;
  onShare?: () => void;
  onTitleChange?: (title: string) => void;
  title?: string;
}) {
  const [draftTitle, setDraftTitle] = useState(title);
  const updateTitle = (next: string) => {
    setDraftTitle(next);
    onTitleChange?.(next);
  };

  return (
    <View style={styles.root}>
      <StatusBar backgroundColor={colors.background} barStyle="dark-content" />
      <Pressable accessibilityLabel="Back to review" accessibilityRole="button" onPress={onBack} style={styles.back}>
        <Text style={styles.backText}>‹</Text>
      </Pressable>
      {photos[0] ? (
        <Image cachePolicy="memory-disk" contentFit="cover" source={photos[0].uri} style={styles.cover} transition={160} />
      ) : <View style={styles.cover} />}
      <Text style={styles.eyebrow}>Album ready</Text>
      <View style={styles.titleRow}>
        <TextInput
          accessibilityLabel="Album title"
          onChangeText={updateTitle}
          onEndEditing={() => onTitleChange?.(draftTitle.trim() || "My photo album")}
          placeholder="Album title"
          placeholderTextColor={colors.muted}
          selectTextOnFocus
          style={styles.title}
          value={draftTitle}
        />
        <Text style={styles.edit}>Edit</Text>
      </View>
      <Text style={styles.suggestion}>We suggested this name. Tap it to change it.</Text>
      <Text style={styles.meta}>{photos.length} photos · Saved to your albums</Text>
      <View style={styles.flex} />
      <View style={styles.pair}>
        <QuietAction label="▸  Play album" onPress={onPlay} />
        <QuietAction label="Open album" onPress={onOpen} />
      </View>
      <Pressable accessibilityRole="button" onPress={onPrint} style={({ pressed }) => [styles.printCard, pressed ? styles.pressed : null]}>
        <View style={styles.book}><View style={styles.bookLight} /></View>
        <View style={styles.printCopy}>
          <Text style={styles.printTitle}>Print it as a photo book</Text>
          <Text style={styles.printMeta}>Posted to your door · from £28</Text>
        </View>
        <Text style={styles.printArrow}>›</Text>
      </Pressable>
      <View style={styles.pair}>
        <QuietAction label="Share" onPress={onShare} />
        <QuietAction label="Done" onPress={onDone ?? onRestart} />
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  back: { alignItems: "center", height: 44, justifyContent: "center", left: spacing.md, position: "absolute", top: (StatusBar.currentHeight ?? 24) + spacing.xs, width: 44, zIndex: 2 },
  backText: { color: colors.text, fontFamily: fonts.regular, fontSize: 30 },
  book: { backgroundColor: "#c99b78", borderCurve: "continuous", borderRadius: 5, height: 58, overflow: "hidden", width: 46 },
  bookLight: { backgroundColor: "#f0dcc6", height: 34, left: -5, position: "absolute", top: -4, transform: [{ rotate: "-12deg" }], width: 58 },
  cover: { aspectRatio: 1, backgroundColor: colors.quietSurface, borderCurve: "continuous", borderRadius: 24, width: "100%" },
  edit: { color: colors.muted, fontFamily: fonts.bold, ...typeScale.eyebrow },
  eyebrow: { color: colors.gold, fontFamily: fonts.bold, paddingTop: spacing.lg, textTransform: "uppercase", ...typeScale.eyebrow },
  flex: { flex: 1, minHeight: spacing.sm },
  meta: { color: colors.muted, fontFamily: fonts.regular, paddingTop: spacing.xs, ...typeScale.small },
  pair: { flexDirection: "row", gap: spacing.sm },
  pressed: { opacity: 0.75, transform: [{ scale: 0.985 }] },
  printArrow: { color: colors.onAccent, fontFamily: fonts.regular, fontSize: 24, opacity: 0.85 },
  printCard: { alignItems: "center", backgroundColor: colors.gold, borderCurve: "continuous", borderRadius: 20, flexDirection: "row", gap: 14, marginVertical: spacing.sm, padding: spacing.md },
  printCopy: { flex: 1 },
  printMeta: { color: colors.onAccent, fontFamily: fonts.regular, opacity: 0.85, ...typeScale.small },
  printTitle: { color: colors.onAccent, fontFamily: fonts.extraBold, fontSize: 18, letterSpacing: -0.3 },
  quietAction: { alignItems: "center", backgroundColor: colors.panel, borderColor: colors.hairline, borderRadius: 26, borderWidth: 1, flex: 1, height: 52, justifyContent: "center" },
  quietActionText: { color: colors.text, fontFamily: fonts.bold, ...typeScale.label },
  root: { backgroundColor: colors.background, flex: 1, paddingBottom: spacing.lg, paddingHorizontal: 28, paddingTop: (StatusBar.currentHeight ?? 24) + spacing.xl },
  suggestion: { color: colors.muted, fontFamily: fonts.regular, paddingTop: spacing.xs, ...typeScale.small },
  title: { borderBottomColor: colors.gold, borderBottomWidth: 2, color: colors.text, flex: 1, fontFamily: fonts.extraBold, fontSize: 28, letterSpacing: -0.8, lineHeight: 34, paddingHorizontal: 0, paddingVertical: 2 },
  titleRow: { alignItems: "center", flexDirection: "row", gap: spacing.sm, paddingTop: 6 },
});
