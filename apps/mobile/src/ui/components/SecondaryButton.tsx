import { Pressable, StyleSheet, Text } from "react-native";

import { fonts } from "../fonts";
import { colors, continuousRadius, layout, radii, spacing, typeScale } from "../tokens";

export type SecondaryButtonProps = {
  label: string;
  onPress: () => void;
  accessibilityHint: string;
  quiet?: boolean;
  disabled?: boolean;
};

export function SecondaryButton({
  label,
  onPress,
  accessibilityHint,
  quiet = false,
  disabled = false,
}: SecondaryButtonProps) {
  return (
    <Pressable
      accessibilityHint={accessibilityHint}
      accessibilityLabel={label}
      accessibilityRole="button"
      accessibilityState={{ disabled }}
      disabled={disabled}
      onPress={onPress}
      style={({ pressed }) => [
        styles.button,
        quiet ? styles.quiet : styles.outline,
        pressed ? styles.pressed : null,
        disabled ? styles.disabled : null,
      ]}
    >
      <Text style={[styles.label, quiet ? styles.quietLabel : null]}>{label}</Text>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  button: {
    ...continuousRadius(radii.md),
    alignItems: "center",
    justifyContent: "center",
    minHeight: layout.minTouchTarget,
    paddingHorizontal: spacing.md,
  },
  disabled: { opacity: 0.38 },
  label: { color: colors.text, fontFamily: fonts.body, ...typeScale.label },
  outline: { borderColor: colors.hairline, borderWidth: 1 },
  pressed: { opacity: 0.68, transform: [{ scale: 0.985 }] },
  quiet: { alignSelf: "center", paddingHorizontal: spacing.sm },
  quietLabel: { color: colors.muted, textDecorationLine: "underline" },
});

