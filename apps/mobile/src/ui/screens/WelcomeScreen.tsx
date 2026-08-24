import { ScrollView, StatusBar, StyleSheet, Text, View } from "react-native";

import { copy } from "../copy";
import { fonts } from "../fonts";
import { colors, layout, spacing, typeScale } from "../tokens";
import { PrimaryButton } from "../components/PrimaryButton";

export function WelcomeScreen({ onContinue }: { onContinue: () => void }) {
  return (
    <View style={styles.root}>
      <StatusBar backgroundColor={colors.background} barStyle="light-content" />
      <ScrollView
        contentContainerStyle={styles.scroll}
        contentInsetAdjustmentBehavior="automatic"
        showsVerticalScrollIndicator={false}
      >
        <View style={styles.top}>
          <Text style={styles.trust}>{copy.trustCue}</Text>
          <View accessible accessibilityLabel={copy.appName} style={styles.monogram}>
            <Text style={styles.monogramText}>P</Text>
          </View>
        </View>
        <View style={styles.main}>
          <Text style={styles.eyebrow}>{copy.welcome.eyebrow}</Text>
          <Text accessibilityRole="header" style={styles.title}>{copy.welcome.title}</Text>
          <Text style={styles.helper}>{copy.welcome.helper}</Text>
        </View>
        <View style={styles.footer}>
          <PrimaryButton
            accessibilityHint={copy.welcome.actionHint}
            label={copy.welcome.action}
            onPress={onContinue}
          />
          <Text style={styles.privacy}>{copy.privacyShort}</Text>
        </View>
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  eyebrow: { color: colors.gold, fontFamily: fonts.body, fontWeight: "600", ...typeScale.eyebrow },
  footer: { gap: spacing.md },
  helper: { color: colors.muted, fontFamily: fonts.body, maxWidth: 560, ...typeScale.body },
  main: { gap: spacing.md, paddingVertical: spacing.xxl },
  monogram: {
    alignItems: "center",
    borderColor: colors.gold,
    borderRadius: 27,
    borderWidth: 1,
    height: 54,
    justifyContent: "center",
    width: 54,
  },
  monogramText: { color: colors.gold, fontFamily: fonts.display, fontSize: 29, lineHeight: 34 },
  privacy: { color: colors.muted, fontFamily: fonts.body, textAlign: "center", ...typeScale.small },
  root: { backgroundColor: colors.background, flex: 1 },
  scroll: {
    flexGrow: 1,
    justifyContent: "space-between",
    paddingBottom: spacing.xl,
    paddingHorizontal: layout.screenPadding,
    paddingTop: (StatusBar.currentHeight ?? 24) + spacing.lg,
  },
  title: { color: colors.text, fontFamily: fonts.display, maxWidth: 600, ...typeScale.display },
  top: { alignItems: "center", flexDirection: "row", justifyContent: "space-between" },
  trust: { color: colors.gold, fontFamily: fonts.body, fontWeight: "600", ...typeScale.eyebrow },
});

