import { Pressable, StyleSheet, Text, View } from "react-native";

import { fonts } from "../fonts";
import { colors, continuousRadius, layout, radii, spacing, typeScale } from "../tokens";

export function HintBanner({
  text,
  onDismiss,
  dismissLabel,
}: {
  text: string;
  onDismiss?: () => void;
  dismissLabel?: string;
}) {
  return (
    <View style={styles.root}>
      <Text style={styles.mark}>i</Text>
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
  );
}

const styles = StyleSheet.create({
  close: { alignItems: "center", height: layout.minTouchTarget, justifyContent: "center", width: layout.minTouchTarget },
  closeText: { color: colors.muted, fontFamily: fonts.body, fontSize: 26, lineHeight: 30 },
  mark: {
    borderColor: colors.gold,
    borderRadius: 10,
    borderWidth: 1,
    color: colors.gold,
    fontFamily: fonts.body,
    fontSize: 14,
    fontWeight: "700",
    height: 20,
    lineHeight: 18,
    marginTop: 3,
    textAlign: "center",
    width: 20,
  },
  pressed: { opacity: 0.55 },
  root: {
    ...continuousRadius(radii.md),
    alignItems: "flex-start",
    backgroundColor: colors.panelRaised,
    borderColor: colors.hairline,
    borderWidth: 1,
    flexDirection: "row",
    gap: spacing.sm,
    paddingLeft: spacing.md,
    paddingVertical: spacing.sm,
  },
  text: { color: colors.text, flex: 1, fontFamily: fonts.body, paddingVertical: 2, ...typeScale.label },
});
