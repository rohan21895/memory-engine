import type { PropsWithChildren } from "react";
import { StyleSheet, View, type ViewStyle } from "react-native";

import { colors, continuousRadius, radii, spacing } from "../tokens";

export function Card({ children, style }: PropsWithChildren<{ style?: ViewStyle }>) {
  return <View style={[styles.card, style]}>{children}</View>;
}

const styles = StyleSheet.create({
  card: {
    ...continuousRadius(radii.lg),
    backgroundColor: colors.panel,
    borderColor: colors.hairline,
    borderWidth: 1,
    padding: spacing.lg,
  },
});

