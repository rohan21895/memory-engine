import { Image } from "expo-image";
import { Modal, Pressable, ScrollView, StatusBar, StyleSheet, Text, View } from "react-native";

import { plainAlternativeReason, plainChosenReason } from "../ui/reasons";
import { colors, fonts, spacing, typeScale } from "../ui";
import type { ReviewSelected } from "./mock-data";

export function SwapSheet({
  currentMediaId,
  onClose,
  onOpen,
  onPick,
  selected,
  visible,
}: {
  currentMediaId: string;
  onClose: () => void;
  onOpen: (index: number) => void;
  onPick: (mediaId: string) => void;
  selected: ReviewSelected | null;
  visible: boolean;
}) {
  const photos = selected ? [
    { id: selected.media_id, note: plainChosenReason(selected.chosen_because), uri: selected.uri },
    ...selected.alternatives.map((alternative) => ({ id: alternative.media_id, note: plainAlternativeReason(alternative.not_chosen_because), uri: alternative.uri })),
  ] : [];
  const alternativeCount = selected?.alternatives.length ?? 0;
  return (
    <Modal animationType="slide" onRequestClose={onClose} presentationStyle="pageSheet" visible={visible}>
      <View accessibilityViewIsModal style={styles.root}>
        <StatusBar backgroundColor={colors.panel} barStyle="dark-content" />
        <View style={styles.header}>
          <View style={styles.headingCopy}>
            <Text accessibilityRole="header" style={styles.title}>Other shots of this moment</Text>
            <Text style={styles.helper}>
              {alternativeCount === 0
                ? "This is the only shot Photeo found of this moment."
                : alternativeCount === 1
                  ? "1 other shot was taken seconds apart. Tap one to use it."
                  : `${alternativeCount} other shots were taken seconds apart. Tap one to use it.`}
            </Text>
          </View>
          <Pressable accessibilityLabel="Close other shots" accessibilityRole="button" hitSlop={10} onPress={onClose} style={styles.close}><Text style={styles.closeText}>✕</Text></Pressable>
        </View>
        <ScrollView contentContainerStyle={styles.grid}>
          {photos.map((photo, index) => {
            const active = photo.id === currentMediaId;
            return (
              <View key={`${photo.id}-${index}`} style={styles.card}>
                <Pressable accessibilityHint="Opens this shot full screen" accessibilityLabel={index === 0 ? "Photeo’s pick" : `Other shot ${index}`} accessibilityRole="button" onPress={() => onOpen(index)}>
                  <Image cachePolicy="memory-disk" contentFit="cover" source={photo.uri} style={[styles.image, active ? styles.imageActive : null]} />
                  <Text style={styles.full}>Full screen</Text>
                </Pressable>
                <Pressable
                  accessibilityHint="Uses this shot on the page"
                  accessibilityLabel={photo.note}
                  accessibilityRole="radio"
                  accessibilityState={{ checked: active }}
                  onPress={() => onPick(photo.id)}
                  style={styles.noteRow}
                >
                  <View style={[styles.tick, active ? styles.tickActive : null]}><Text style={styles.tickText}>{active ? "✓" : ""}</Text></View>
                  <Text numberOfLines={3} style={styles.note}>{photo.note}</Text>
                </Pressable>
              </View>
            );
          })}
        </ScrollView>
        <View style={styles.footer}>
          <Pressable accessibilityHint="Closes this sheet and keeps the ticked shot" accessibilityRole="button" onPress={onClose} style={styles.use}>
            <Text style={styles.useText}>{alternativeCount === 0 ? "Done" : "Use this photo"}</Text>
          </Pressable>
        </View>
      </View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  card: { gap: spacing.xs, width: "31%" },
  close: { alignItems: "center", backgroundColor: "#f0eee8", borderRadius: 17, height: 34, justifyContent: "center", width: 34 },
  closeText: { color: colors.text, fontFamily: fonts.semibold, fontSize: 15 },
  footer: { borderTopColor: colors.hairline, borderTopWidth: 1, padding: spacing.md, paddingBottom: spacing.lg },
  full: { backgroundColor: "rgba(20,15,10,.5)", borderRadius: 9, bottom: 6, color: colors.onAccent, fontFamily: fonts.bold, fontSize: 12, left: 6, overflow: "hidden", paddingHorizontal: 7, paddingVertical: 3, position: "absolute" },
  grid: { flexDirection: "row", flexWrap: "wrap", gap: 10, padding: spacing.lg },
  header: { alignItems: "center", flexDirection: "row", gap: spacing.sm, paddingHorizontal: spacing.lg, paddingTop: (StatusBar.currentHeight ?? 24) + spacing.md },
  headingCopy: { flex: 1 },
  helper: { color: colors.muted, fontFamily: fonts.regular, paddingTop: spacing.xs, ...typeScale.small },
  image: { aspectRatio: 1, backgroundColor: colors.hairline, borderCurve: "continuous", borderRadius: 12, width: "100%" },
  imageActive: { borderColor: colors.gold, borderWidth: 3 },
  note: { color: colors.muted, flex: 1, fontFamily: fonts.regular, fontSize: 12.5, lineHeight: 16 },
  noteRow: { flexDirection: "row", gap: 5, minHeight: 44, paddingVertical: spacing.xxs },
  root: { backgroundColor: colors.panel, flex: 1 },
  tick: { alignItems: "center", borderColor: "#c4bcb0", borderRadius: 10, borderWidth: 1.5, height: 20, justifyContent: "center", width: 20 },
  tickActive: { backgroundColor: colors.gold, borderColor: colors.gold },
  tickText: { color: colors.onAccent, fontFamily: fonts.bold, fontSize: 12 },
  title: { color: colors.text, fontFamily: fonts.extraBold, ...typeScale.subtitle },
  use: { alignItems: "center", backgroundColor: colors.gold, borderRadius: 27, height: 54, justifyContent: "center" },
  useText: { color: colors.onAccent, fontFamily: fonts.bold, ...typeScale.label },
});
