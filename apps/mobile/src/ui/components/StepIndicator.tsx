import { StyleSheet, Text, View } from "react-native";

import { copy } from "../copy";
import { fonts } from "../fonts";
import { colors, spacing, typeScale } from "../tokens";

export type AlbumStep = 1 | 2 | 3;

export function StepIndicator({ activeStep }: { activeStep: AlbumStep }) {
  return (
    <View
      accessible
      accessibilityLabel={copy.steps.accessibilityLabel(activeStep)}
      accessibilityRole="progressbar"
      accessibilityValue={{ min: 1, max: 3, now: activeStep }}
      style={styles.root}
    >
      <Text style={styles.counter}>{copy.steps.step(activeStep)}</Text>
      <View style={styles.row}>
        {copy.steps.labels.map((label, index) => {
          const step = index + 1;
          const complete = step < activeStep;
          const active = step === activeStep;
          return (
            <View key={label} style={styles.step}>
              <View style={styles.trackRow}>
                <View style={[styles.dot, complete || active ? styles.dotOn : null]}>
                  <Text style={[styles.dotText, complete || active ? styles.dotTextOn : null]}>
                    {complete ? "✓" : step}
                  </Text>
                </View>
                {step < 3 ? (
                  <View style={[styles.line, complete ? styles.lineOn : null]} />
                ) : null}
              </View>
              <Text style={[styles.label, active ? styles.labelActive : null]}>{label}</Text>
            </View>
          );
        })}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  counter: {
    color: colors.gold,
    fontFamily: fonts.body,
    fontWeight: "600",
    ...typeScale.small,
  },
  dot: {
    alignItems: "center",
    borderColor: colors.hairline,
    borderRadius: 12,
    borderWidth: 1,
    height: 24,
    justifyContent: "center",
    width: 24,
  },
  dotOn: { backgroundColor: colors.gold, borderColor: colors.gold },
  dotText: { color: colors.muted, fontFamily: fonts.body, fontSize: 13, lineHeight: 16 },
  dotTextOn: { color: colors.ink, fontWeight: "700" },
  label: { color: colors.muted, fontFamily: fonts.body, ...typeScale.small },
  labelActive: { color: colors.text, fontWeight: "700" },
  line: { backgroundColor: colors.hairline, flex: 1, height: 1 },
  lineOn: { backgroundColor: colors.gold },
  root: { gap: spacing.xs, width: "100%" },
  row: { flexDirection: "row" },
  step: { flex: 1, gap: spacing.xs },
  trackRow: { alignItems: "center", flexDirection: "row" },
});

