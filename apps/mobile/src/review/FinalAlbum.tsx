import { Image } from "expo-image";
import { useState } from "react";
import { Pressable, StatusBar, StyleSheet, Text, TextInput, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { colors, fonts, spacing, typeScale } from "../ui";

export type FinalPhoto = {
  height?: number;
  media_id: string;
  page: number;
  uri: string;
  width?: number;
};

function QuietAction({ label, onPress }: { label: string; onPress?: () => void }) {
  return (
    <Pressable accessibilityLabel={label.replace("▸", "Play").trim()} accessibilityRole="button" onPress={onPress} style={({ pressed }) => [styles.quietAction, pressed ? styles.pressed : null]}>
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
  const insets = useSafeAreaInsets();
  const [draftTitle, setDraftTitle] = useState(title);
  const updateTitle = (next: string) => setDraftTitle(next);

  // On Android a Pressable can fire without the TextInput ever blurring, so
  // onEndEditing alone lost a freshly typed name the moment the user tapped
  // Play/Open/Done. Every way off this screen commits the draft first.
  const commitTitle = () => {
    const cleanTitle = draftTitle.trim() || "My photo album";
    if (cleanTitle !== draftTitle) setDraftTitle(cleanTitle);
    if (cleanTitle !== title) onTitleChange?.(cleanTitle);
    return cleanTitle;
  };
  const leaveVia = (action?: () => void) => () => {
    commitTitle();
    action?.();
  };

  return (
    <View style={[styles.root, { paddingBottom: Math.max(spacing.lg, insets.bottom + spacing.sm) }]}>
      <StatusBar backgroundColor={colors.background} barStyle="dark-content" />
      <Pressable accessibilityLabel="Back to review" accessibilityRole="button" onPress={leaveVia(onBack)} style={styles.back}>
        <Text style={styles.backText}>‹</Text>
      </Pressable>
      {photos[0] ? (
        // A big photograph that does nothing when tapped reads as broken. Opening
        // the album is the thing he already wants from here, so the cover does it.
        <Pressable
          accessibilityHint="Opens the album"
          accessibilityLabel="Album cover"
          accessibilityRole="button"
          onPress={leaveVia(onOpen)}
        >
          <Image cachePolicy="memory-disk" contentFit="cover" source={photos[0].uri} style={styles.cover} transition={160} />
        </Pressable>
      ) : <View style={styles.cover} />}
      <Text style={styles.eyebrow}>Album ready</Text>
      <View style={styles.titleRow}>
        <TextInput
          accessibilityLabel="Album title"
          onChangeText={updateTitle}
          onEndEditing={commitTitle}
          onSubmitEditing={commitTitle}
          placeholder="Album title"
          placeholderTextColor={colors.muted}
          returnKeyType="done"
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
        <QuietAction label="▸  Play album" onPress={leaveVia(onPlay)} />
        <QuietAction label="Open album" onPress={leaveVia(onOpen)} />
      </View>
      <QuietAction label="Done" onPress={leaveVia(onDone ?? onRestart)} />
    </View>
  );
}

const styles = StyleSheet.create({
  back: { alignItems: "center", height: 44, justifyContent: "center", left: spacing.md, position: "absolute", top: (StatusBar.currentHeight ?? 24) + spacing.xs, width: 44, zIndex: 2 },
  backText: { color: colors.text, fontFamily: fonts.regular, fontSize: 30 },
  cover: { aspectRatio: 1, backgroundColor: colors.quietSurface, borderCurve: "continuous", borderRadius: 24, width: "100%" },
  edit: { color: colors.muted, fontFamily: fonts.bold, ...typeScale.eyebrow },
  eyebrow: { color: colors.gold, fontFamily: fonts.bold, paddingTop: spacing.lg, textTransform: "uppercase", ...typeScale.eyebrow },
  flex: { flex: 1, minHeight: spacing.sm },
  meta: { color: colors.muted, fontFamily: fonts.regular, paddingTop: spacing.xs, ...typeScale.small },
  pair: { flexDirection: "row", gap: spacing.sm },
  pressed: { opacity: 0.75, transform: [{ scale: 0.985 }] },
  quietAction: { alignItems: "center", backgroundColor: colors.panel, borderColor: colors.hairline, borderRadius: 26, borderWidth: 1, flex: 1, height: 52, justifyContent: "center" },
  quietActionText: { color: colors.text, fontFamily: fonts.bold, ...typeScale.label },
  root: { backgroundColor: colors.background, flex: 1, paddingBottom: spacing.lg, paddingHorizontal: 28, paddingTop: (StatusBar.currentHeight ?? 24) + spacing.xl },
  suggestion: { color: colors.muted, fontFamily: fonts.regular, paddingTop: spacing.xs, ...typeScale.small },
  title: { borderBottomColor: colors.gold, borderBottomWidth: 2, color: colors.text, flex: 1, fontFamily: fonts.extraBold, fontSize: 28, letterSpacing: -0.8, lineHeight: 34, paddingHorizontal: 0, paddingVertical: 2 },
  titleRow: { alignItems: "center", flexDirection: "row", gap: spacing.sm, paddingTop: 6 },
});
