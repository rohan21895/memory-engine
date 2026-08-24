import { StyleSheet, Text, View } from "react-native";

import { fonts } from "../fonts";
import { colors, spacing, typeScale } from "../tokens";
import { SecondaryButton } from "./SecondaryButton";

export function ErrorState({
  title,
  helper,
  actionLabel,
  actionHint,
  onAction,
}: {
  title: string;
  helper: string;
  actionLabel: string;
  actionHint: string;
  onAction: () => void;
}) {
  return (
    <View accessibilityLiveRegion="assertive" style={styles.root}>
      <Text style={styles.symbol}>!</Text>
      <Text accessibilityRole="header" style={styles.title}>{title}</Text>
      <Text style={styles.helper}>{helper}</Text>
      <SecondaryButton accessibilityHint={actionHint} label={actionLabel} onPress={onAction} />
    </View>
  );
}

const styles = StyleSheet.create({
  helper: { color: colors.muted, fontFamily: fonts.body, textAlign: "center", ...typeScale.body },
  root: { alignItems: "center", flex: 1, gap: spacing.md, justifyContent: "center", padding: spacing.xl },
  symbol: {
    borderColor: colors.error,
    borderRadius: 20,
    borderWidth: 1,
    color: colors.error,
    fontFamily: fonts.body,
    fontSize: 24,
    height: 40,
    lineHeight: 37,
    textAlign: "center",
    width: 40,
  },
  title: { color: colors.text, fontFamily: fonts.display, textAlign: "center", ...typeScale.title },
});
