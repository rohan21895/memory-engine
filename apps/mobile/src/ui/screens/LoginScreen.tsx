import { ScrollView, StatusBar, StyleSheet, Text, View } from "react-native";

import { PrimaryButton } from "../components/PrimaryButton";
import { SecondaryButton } from "../components/SecondaryButton";
import { fonts } from "../fonts";
import { colors, layout, radii, spacing, typeScale } from "../tokens";

export function LoginScreen({ onContinue }: { onContinue: () => void }) {
  return (
    <View style={styles.root}>
      <StatusBar backgroundColor={colors.background} barStyle="dark-content" />
      <ScrollView contentContainerStyle={styles.scroll} contentInsetAdjustmentBehavior="automatic">
        <View style={styles.mark} />
        <View style={styles.copy}>
          <Text accessibilityRole="header" style={styles.title}>Sign in to Photeo</Text>
          <Text style={styles.helper}>
            Only so family can share albums with you. Your photos still stay on this phone.
          </Text>
        </View>
        <View style={styles.actions}>
          <PrimaryButton
            accessibilityHint="Continues to photo permission"
            label="Send me a code"
            onPress={onContinue}
          />
          <SecondaryButton
            accessibilityHint="Uses Photeo without signing in"
            label="Use Photeo without an account"
            onPress={onContinue}
            quiet
          />
        </View>
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  actions: { gap: spacing.xs },
  copy: { gap: spacing.sm },
  helper: { color: colors.muted, fontFamily: fonts.regular, ...typeScale.body },
  mark: { backgroundColor: "#e6c8b0", borderCurve: "continuous", borderRadius: radii.lg, height: 64, width: 64 },
  root: { backgroundColor: colors.background, flex: 1 },
  scroll: {
    flexGrow: 1,
    gap: spacing.lg,
    justifyContent: "space-between",
    paddingBottom: spacing.xl,
    paddingHorizontal: layout.screenPadding,
    paddingTop: (StatusBar.currentHeight ?? 24) + spacing.xxl,
  },
  title: { color: colors.text, fontFamily: fonts.extraBold, ...typeScale.title },
});
