import { Pressable, StyleSheet, Text, View } from "react-native";

import { fonts } from "../fonts";
import { colors, continuousRadius, layout, radii, spacing, typeScale } from "../tokens";

export function HintBanner({
  text,
  onDismiss,
  dismissLabel,
  actionLabel,
  actionHint,
  onAction,
  tone = "info",
}: {
  text: string;
  onDismiss?: () => void;
  dismissLabel?: string;
  actionLabel?: string;
  actionHint?: string;
  onAction?: () => void;
  tone?: "info" | "warning";
}) {
  return (
    <View accessibilityLiveRegion="polite" style={styles.root}>
      <View style={styles.row}>
        <Text style={[styles.mark, tone === "warning" ? styles.markWarning : null]}>!</Text>
        <Text style={styles.text}>{text}</Text>
        {onDismiss && dismissLabel ? (
          <Pressable
            accessibilityHint={dismissLabel}
            accessibilityLabel={dismissLabel}
            accessibilityRole="button"
            hitSlop={8}
            onPress={onDismiss}
            style={({ pressed }) => [styles.close, pressed ? styles.pressed : null]}
          >
            <Text style={styles.closeText}>×</Text>
          </Pressable>
        ) : null}
      </View>
      {actionLabel && onAction ? (
        <Pressable
          accessibilityHint={actionHint}
          accessibilityLabel={actionLabel}
          accessibilityRole="button"
          onPress={onAction}
          style={({ pressed }) => [styles.action, pressed ? styles.pressed : null]}
        >
          <Text style={styles.actionText}>{actionLabel}</Text>
        </Pressable>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  action: {
    alignItems: "center",
    alignSelf: "flex-start",
    backgroundColor: colors.panel,
    borderColor: colors.hairline,
    borderRadius: radii.pill,
    borderWidth: 1,
    justifyContent: "center",
    marginLeft: spacing.lg,
    minHeight: layout.minTouchTarget,
    paddingHorizontal: spacing.md,
  },
  actionText: { color: colors.goldPressed, fontFamily: fonts.bold, ...typeScale.small },
  close: { alignItems: "center", height: layout.minTouchTarget, justifyContent: "center", width: layout.minTouchTarget },
  closeText: { color: colors.muted, fontFamily: fonts.body, fontSize: 26, lineHeight: 30 },
  mark: {
    borderColor: colors.goldPressed,
    borderRadius: 10,
    borderWidth: 1,
    color: colors.goldPressed,
    fontFamily: fonts.body,
    fontSize: 14,
    fontWeight: "700",
    height: 20,
    lineHeight: 18,
    marginTop: 3,
    textAlign: "center",
    width: 20,
  },
  markWarning: { backgroundColor: colors.goldPressed, color: colors.onAccent },
  pressed: { opacity: 0.55 },
  root: {
    ...continuousRadius(radii.md),
    backgroundColor: colors.panelRaised,
    borderColor: colors.hairline,
    borderWidth: 1,
    gap: spacing.xs,
    paddingBottom: spacing.sm,
    paddingLeft: spacing.md,
    paddingTop: spacing.sm,
  },
  row: { alignItems: "flex-start", flexDirection: "row", gap: spacing.sm },
  text: { color: colors.text, flex: 1, fontFamily: fonts.body, paddingVertical: 2, ...typeScale.small },
});
