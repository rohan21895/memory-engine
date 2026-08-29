import { ActivityIndicator, Pressable, StyleSheet, Text, View } from "react-native";

import { fonts } from "../fonts";
import { colors, continuousRadius, layout, radii, spacing, typeScale } from "../tokens";

export type PrimaryButtonProps = {
  label: string;
  onPress: () => void;
  accessibilityHint: string;
  disabled?: boolean;
  busy?: boolean;
};

export function PrimaryButton({
  label,
  onPress,
  accessibilityHint,
  disabled = false,
  busy = false,
}: PrimaryButtonProps) {
  const unavailable = disabled || busy;
  return (
    <Pressable
      accessibilityHint={accessibilityHint}
      accessibilityLabel={label}
      accessibilityRole="button"
      accessibilityState={{ busy, disabled: unavailable }}
      disabled={unavailable}
      onPress={onPress}
      style={({ pressed }) => [
        styles.button,
        pressed ? styles.pressed : null,
        unavailable ? styles.disabled : null,
      ]}
    >
      {busy ? (
        <View style={styles.busyRow}>
      <ActivityIndicator color={colors.onAccent} />
          <Text style={[styles.label, unavailable ? styles.disabledLabel : null]}>{label}</Text>
        </View>
      ) : (
        <Text style={[styles.label, unavailable ? styles.disabledLabel : null]}>{label}</Text>
      )}
    </Pressable>
  );
}

const styles = StyleSheet.create({
  button: {
    ...continuousRadius(radii.md),
    alignItems: "center",
    backgroundColor: colors.gold,
    justifyContent: "center",
    minHeight: layout.primaryButtonHeight,
    paddingHorizontal: spacing.lg,
    paddingVertical: spacing.sm,
    width: "100%",
  },
  busyRow: { alignItems: "center", flexDirection: "row", gap: spacing.sm },
  // Not `opacity`: see `colors.disabledSurface`. Dimming a gold button on a
  // cream page takes the label with it.
  disabled: { backgroundColor: colors.disabledSurface },
  disabledLabel: { color: colors.disabledText },
  label: {
    color: colors.onAccent,
    fontFamily: fonts.body,
    fontWeight: "700",
    textAlign: "center",
    ...typeScale.label,
  },
  pressed: { backgroundColor: colors.goldPressed, transform: [{ scale: 0.985 }] },
});
