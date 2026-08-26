import { StyleSheet, Text, View } from "react-native";

import { fonts } from "../fonts";
import { colors, spacing, typeScale } from "../tokens";
import { SecondaryButton } from "./SecondaryButton";

export function EmptyState({
  title,
  helper,
  actionLabel,
  actionHint,
  onAction,
}: {
  title: string;
  helper: string;
  actionLabel?: string;
  actionHint?: string;
  onAction?: () => void;
}) {
  return (
    <View style={styles.root}>
      <Text style={styles.symbol}>◇</Text>
      <Text accessibilityRole="header" style={styles.title}>{title}</Text>
      <Text style={styles.helper}>{helper}</Text>
      {actionLabel && actionHint && onAction ? (
        <SecondaryButton accessibilityHint={actionHint} label={actionLabel} onPress={onAction} />
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  helper: { color: colors.muted, fontFamily: fonts.body, textAlign: "center", ...typeScale.body },
  root: { alignItems: "center", flex: 1, gap: spacing.md, justifyContent: "center", padding: spacing.xl },
  symbol: { color: colors.gold, fontFamily: fonts.display, fontSize: 42, lineHeight: 46 },
  title: { color: colors.text, fontFamily: fonts.display, textAlign: "center", ...typeScale.title },
});

