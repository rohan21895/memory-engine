import { ScrollView, StatusBar, StyleSheet, Text, View } from "react-native";

import { PrimaryButton } from "../components/PrimaryButton";
import { fonts } from "../fonts";
import { colors, layout, radii, spacing, typeScale } from "../tokens";
import { useFirstLayoutLog } from "../use-first-layout-log";

const steps = [
  "Pick a few photos — or all of them",
  "Photeo finds the best shots",
  "Watch, print, or share your album",
];

export function WelcomeScreen({ onContinue }: { onContinue: () => void }) {
  const logFirstLayout = useFirstLayoutLog("welcome");
  return (
    <View onLayout={logFirstLayout} style={styles.root}>
      <StatusBar backgroundColor={colors.background} barStyle="dark-content" />
      <ScrollView
        contentContainerStyle={styles.scroll}
        contentInsetAdjustmentBehavior="automatic"
        showsVerticalScrollIndicator={false}
      >
        <View style={styles.mark}>
          <View style={styles.markLight} />
          <View style={styles.markPaper} />
        </View>
        <View style={styles.intro}>
          <Text accessibilityRole="header" style={styles.title}>Albums,{`\n`}made for you.</Text>
          <Text style={styles.helper}>
            Photeo looks through your photos and builds a beautiful album on its own.
          </Text>
        </View>
        <View style={styles.steps}>
          {steps.map((step, index) => (
            <View key={step} style={styles.step}>
              <View style={styles.stepNumber}><Text style={styles.stepNumberText}>{index + 1}</Text></View>
              <Text style={styles.stepText}>{step}</Text>
            </View>
          ))}
        </View>
        <View style={styles.flex} />
        <View style={styles.privacy}>
          <View style={styles.privacyDot} />
          <Text style={styles.privacyText}>Your photos never leave your phone. Nothing is uploaded.</Text>
        </View>
        <PrimaryButton
          accessibilityHint="Continues to Photeo setup"
          label="Get started"
          onPress={onContinue}
        />
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  flex: { flex: 1, minHeight: spacing.lg },
  helper: { color: colors.muted, fontFamily: fonts.regular, fontSize: 16.5, lineHeight: 25 },
  intro: { gap: spacing.sm },
  mark: { backgroundColor: "#d9a184", borderCurve: "continuous", borderRadius: 34, height: 120, overflow: "hidden", width: 120 },
  markLight: { backgroundColor: "#f2d9c6", height: 72, left: -10, position: "absolute", top: -8, transform: [{ rotate: "-12deg" }], width: 150 },
  markPaper: { backgroundColor: colors.panel, borderCurve: "continuous", borderRadius: radii.md, height: 52, left: 34, position: "absolute", top: 34, width: 52 },
  privacy: { alignItems: "center", backgroundColor: colors.quietSurface, borderCurve: "continuous", borderRadius: radii.md, flexDirection: "row", gap: spacing.sm, padding: 14 },
  privacyDot: { backgroundColor: colors.success, borderRadius: 5, height: 9, width: 9 },
  privacyText: { color: "#4c463d", flex: 1, fontFamily: fonts.regular, fontSize: 13.5, lineHeight: 19 },
  root: { backgroundColor: colors.background, flex: 1 },
  scroll: {
    flexGrow: 1,
    gap: spacing.md,
    paddingBottom: spacing.xl,
    paddingHorizontal: layout.screenPadding + 6,
    paddingTop: (StatusBar.currentHeight ?? 24) + spacing.xl,
  },
  step: { alignItems: "center", flexDirection: "row", gap: 14 },
  stepNumber: { alignItems: "center", backgroundColor: colors.panelRaised, borderRadius: 18, height: 36, justifyContent: "center", width: 36 },
  stepNumberText: { color: colors.gold, fontFamily: fonts.extraBold, fontSize: 15 },
  stepText: { color: colors.text, flex: 1, fontFamily: fonts.medium, fontSize: 15.5, lineHeight: 21 },
  steps: { gap: 14, paddingTop: spacing.sm },
  title: { color: colors.text, fontFamily: fonts.extraBold, ...typeScale.display },
});
