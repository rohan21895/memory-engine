import { Pressable, StatusBar, StyleSheet, Text, View } from "react-native";

import { colors, fonts, layout, spacing, typeScale } from "../index";

export function BuildErrorScreen({ onBack, onRetry }: { onBack: () => void; onRetry: () => void }) {
  return (
    <View style={styles.root}>
      <StatusBar backgroundColor={colors.background} barStyle="dark-content" />
      <View style={styles.mark}><View style={styles.markInner} /></View>
      <Text accessibilityRole="header" style={styles.title}>That didn’t finish</Text>
      <Text style={styles.copy}>Your photos are safe and nothing was lost. We can pick up where we left off.</Text>
      <Pressable accessibilityRole="button" onPress={onRetry} style={styles.retry}><Text style={styles.retryText}>Try again</Text></Pressable>
      <Pressable accessibilityRole="button" onPress={onBack} style={styles.back}><Text style={styles.backText}>Back to albums</Text></Pressable>
    </View>
  );
}

const styles = StyleSheet.create({
  back: { alignItems: "center", height: 50, justifyContent: "center" },
  backText: { color: colors.muted, fontFamily: fonts.semibold, ...typeScale.small },
  copy: { color: colors.muted, fontFamily: fonts.regular, marginBottom: spacing.lg, marginTop: spacing.sm, textAlign: "center", ...typeScale.body },
  mark: { alignItems: "center", backgroundColor: colors.panelRaised, borderRadius: 26, height: 88, justifyContent: "center", marginBottom: spacing.lg, width: 88 },
  markInner: { backgroundColor: "#e0a389", borderRadius: 10, height: 32, width: 32 },
  retry: { alignItems: "center", backgroundColor: colors.gold, borderRadius: 28, height: 56, justifyContent: "center", width: "100%" },
  retryText: { color: colors.onAccent, fontFamily: fonts.bold, ...typeScale.label },
  root: { alignItems: "center", backgroundColor: colors.background, flex: 1, justifyContent: "center", paddingHorizontal: layout.screenPadding },
  title: { color: colors.text, fontFamily: fonts.extraBold, ...typeScale.subtitle },
});
