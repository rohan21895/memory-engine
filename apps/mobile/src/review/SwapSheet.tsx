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
  return (
    <Modal animationType="slide" onRequestClose={onClose} presentationStyle="pageSheet" visible={visible}>
      <View accessibilityViewIsModal style={styles.root}>
        <StatusBar backgroundColor={colors.panel} barStyle="dark-content" />
        <View style={styles.header}>
          <View style={styles.headingCopy}>
            <Text accessibilityRole="header" style={styles.title}>Other shots of this moment</Text>
            <Text style={styles.helper}>{photos.length} shots taken seconds apart. Tap one to use it.</Text>
          </View>
          <Pressable accessibilityLabel="Close other shots" accessibilityRole="button" onPress={onClose} style={styles.close}><Text style={styles.closeText}>✕</Text></Pressable>
        </View>
        <ScrollView contentContainerStyle={styles.grid}>
          {photos.map((photo, index) => {
            const active = photo.id === currentMediaId;
            return (
              <View key={`${photo.id}-${index}`} style={styles.card}>
                <Pressable accessibilityRole="button" onPress={() => onOpen(index)}>
                  <Image cachePolicy="memory-disk" contentFit="cover" source={photo.uri} style={[styles.image, active ? styles.imageActive : null]} />
                  <Text style={styles.full}>Full screen</Text>
                </Pressable>
                <Pressable onPress={() => onPick(photo.id)} style={styles.noteRow}>
                  <View style={[styles.tick, active ? styles.tickActive : null]}><Text style={styles.tickText}>{active ? "✓" : ""}</Text></View>
                  <Text numberOfLines={3} style={styles.note}>{photo.note}</Text>
                </Pressable>
              </View>
            );
          })}
        </ScrollView>
        <View style={styles.footer}><Pressable accessibilityRole="button" onPress={onClose} style={styles.use}><Text style={styles.useText}>Use this photo</Text></Pressable></View>
      </View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  card: { gap: spacing.xs, width: "31%" },
  close: { alignItems: "center", backgroundColor: "#f0eee8", borderRadius: 17, height: 34, justifyContent: "center", width: 34 },
  closeText: { color: colors.text, fontFamily: fonts.semibold, fontSize: 15 },
  footer: { borderTopColor: colors.hairline, borderTopWidth: 1, padding: spacing.md, paddingBottom: spacing.lg },
  full: { backgroundColor: "rgba(20,15,10,.5)", borderRadius: 9, bottom: 6, color: colors.onAccent, fontFamily: fonts.bold, fontSize: 10.5, left: 6, overflow: "hidden", paddingHorizontal: 7, paddingVertical: 3, position: "absolute" },
  grid: { flexDirection: "row", flexWrap: "wrap", gap: 10, padding: spacing.lg },
  header: { alignItems: "center", flexDirection: "row", gap: spacing.sm, paddingHorizontal: spacing.lg, paddingTop: (StatusBar.currentHeight ?? 24) + spacing.md },
  headingCopy: { flex: 1 },
  helper: { color: colors.muted, fontFamily: fonts.regular, paddingTop: spacing.xs, ...typeScale.small },
  image: { aspectRatio: 1, backgroundColor: colors.hairline, borderCurve: "continuous", borderRadius: 12, width: "100%" },
  imageActive: { borderColor: colors.gold, borderWidth: 3 },
  note: { color: colors.muted, flex: 1, fontFamily: fonts.regular, fontSize: 11.5, lineHeight: 15 },
  noteRow: { flexDirection: "row", gap: 5 },
  root: { backgroundColor: colors.panel, flex: 1 },
  tick: { alignItems: "center", borderColor: "#c4bcb0", borderRadius: 8, borderWidth: 1.5, height: 16, justifyContent: "center", width: 16 },
  tickActive: { backgroundColor: colors.gold, borderColor: colors.gold },
  tickText: { color: colors.onAccent, fontFamily: fonts.bold, fontSize: 9 },
  title: { color: colors.text, fontFamily: fonts.extraBold, ...typeScale.subtitle },
  use: { alignItems: "center", backgroundColor: colors.gold, borderRadius: 27, height: 54, justifyContent: "center" },
  useText: { color: colors.onAccent, fontFamily: fonts.bold, ...typeScale.label },
});
