import { ActivityIndicator, StyleSheet, Text, View } from "react-native";

import { copy } from "../copy";
import { fonts } from "../fonts";
import { colors, spacing, typeScale } from "../tokens";

export function LoadingState({ title, helper }: { title: string; helper?: string }) {
  return (
    <View accessibilityLiveRegion="polite" style={styles.root}>
      <ActivityIndicator color={colors.gold} size="large" />
      <Text accessibilityRole="header" style={styles.title}>{title}</Text>
      <Text style={styles.helper}>{helper ?? copy.states.safe}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  helper: { color: colors.muted, fontFamily: fonts.body, textAlign: "center", ...typeScale.body },
  root: { alignItems: "center", flex: 1, gap: spacing.md, justifyContent: "center", padding: spacing.xl },
  title: { color: colors.text, fontFamily: fonts.display, textAlign: "center", ...typeScale.title },
});

