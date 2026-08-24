import { ScrollView, StatusBar, StyleSheet, Text, View } from "react-native";

import { PrimaryButton } from "../components/PrimaryButton";
import { fonts } from "../fonts";
import { colors, layout, radii, spacing, typeScale } from "../tokens";

export function AlbumsScreen({ onCreate, message }: { onCreate: () => void; message?: string | null }) {
  return (
    <ScrollView contentContainerStyle={styles.scroll} contentInsetAdjustmentBehavior="automatic">
      <View style={styles.header}>
        <Text accessibilityRole="header" style={styles.title}>Albums</Text>
        <View style={styles.avatar} />
      </View>
      <PrimaryButton accessibilityHint="Starts the album photo picker" label="＋ Create new album" onPress={onCreate} />
      <Text style={styles.note}>Takes about a minute. Stays on your phone.</Text>
      {message ? <Text accessibilityLiveRegion="polite" style={styles.error}>{message}</Text> : null}
      <View style={styles.empty}>
        <View style={styles.emptyMark} />
        <Text style={styles.emptyTitle}>No albums yet</Text>
        <Text style={styles.emptyCopy}>Make your first one. Pick a few photos and Photeo does the rest.</Text>
      </View>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  avatar: { backgroundColor: "#d9a184", borderRadius: 19, height: 38, width: 38 },
  empty: { alignItems: "center", gap: spacing.sm, paddingHorizontal: spacing.sm, paddingTop: spacing.xxl },
  emptyCopy: { color: colors.muted, fontFamily: fonts.regular, textAlign: "center", ...typeScale.body },
  emptyMark: { backgroundColor: colors.quietSurface, borderCurve: "continuous", borderRadius: radii.lg, height: 96, width: 96 },
  emptyTitle: { color: colors.text, fontFamily: fonts.extraBold, ...typeScale.subtitle },
  error: { color: colors.error, fontFamily: fonts.medium, textAlign: "center", ...typeScale.small },
  header: { alignItems: "center", flexDirection: "row", justifyContent: "space-between" },
  note: { color: colors.muted, fontFamily: fonts.regular, textAlign: "center", ...typeScale.small },
  scroll: {
    gap: spacing.sm,
    paddingBottom: spacing.xxl,
    paddingHorizontal: layout.screenPadding,
    paddingTop: (StatusBar.currentHeight ?? 24) + spacing.md,
  },
  title: { color: colors.text, fontFamily: fonts.extraBold, ...typeScale.title },
});
