import { Pressable, StyleSheet, Text, View } from "react-native";

import { copy } from "../copy";
import { fonts } from "../fonts";
import { colors, layout, spacing, typeScale } from "../tokens";
import { type AlbumStep, StepIndicator } from "./StepIndicator";

export type ScreenHeaderProps = {
  title: string;
  helper: string;
  step?: AlbumStep;
  eyebrow?: string;
  onBack?: () => void;
  backHint?: string;
  compact?: boolean;
};

export function ScreenHeader({
  title,
  helper,
  step,
  eyebrow,
  onBack,
  backHint = copy.common.goBackHint,
  compact = false,
}: ScreenHeaderProps) {
  return (
    <View style={[styles.root, compact ? styles.compact : null]}>
      {onBack ? (
        <Pressable
          accessibilityHint={backHint}
          accessibilityLabel={copy.common.back}
          accessibilityRole="button"
          hitSlop={8}
          onPress={onBack}
          style={({ pressed }) => [styles.back, pressed ? styles.backPressed : null]}
        >
          <Text style={styles.backText}>‹ {copy.common.back}</Text>
        </Pressable>
      ) : null}
      {step ? <StepIndicator activeStep={step} /> : null}
      {eyebrow ? <Text style={styles.eyebrow}>{eyebrow}</Text> : null}
      <View style={styles.copy}>
        <Text accessibilityRole="header" style={[styles.title, compact ? styles.compactTitle : null]}>
          {title}
        </Text>
        <Text style={styles.helper}>{helper}</Text>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  back: { alignSelf: "flex-start", justifyContent: "center", minHeight: layout.minTouchTarget },
  backPressed: { opacity: 0.62 },
  backText: { color: colors.muted, fontFamily: fonts.body, ...typeScale.label },
  compact: { gap: spacing.sm },
  compactTitle: { ...typeScale.title },
  copy: { gap: spacing.xs },
  eyebrow: {
    color: colors.gold,
    fontFamily: fonts.body,
    fontWeight: "600",
    textTransform: "uppercase",
    ...typeScale.eyebrow,
  },
  helper: { color: colors.muted, fontFamily: fonts.body, maxWidth: layout.maxReadableWidth, ...typeScale.body },
  root: { gap: spacing.md, width: "100%" },
  title: { color: colors.text, fontFamily: fonts.display, ...typeScale.display },
});
