import { ScrollView, StatusBar, StyleSheet, Text, View } from "react-native";

import { fonts } from "../fonts";
import { colors, layout, radii, spacing, typeScale } from "../tokens";

export function AccountScreen() {
  return (
    <ScrollView contentContainerStyle={styles.scroll} contentInsetAdjustmentBehavior="automatic">
      <Text accessibilityRole="header" style={styles.title}>Account</Text>
      <View style={styles.profile}>
        <View style={styles.avatar}><Text style={styles.initial}>P</Text></View>
        <View><Text style={styles.name}>Your Photeo</Text><Text style={styles.meta}>Private on this phone</Text></View>
      </View>
      <View style={styles.privacy}>
        <Text style={styles.privacyTitle}>●  Everything stays on your phone</Text>
        <Text style={styles.privacyCopy}>Photos are never uploaded and albums are made without the internet.</Text>
      </View>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  avatar: { alignItems: "center", backgroundColor: "#c99b78", borderRadius: 36, height: 72, justifyContent: "center", width: 72 },
  initial: { color: colors.onAccent, fontFamily: fonts.bold, fontSize: 26 },
  meta: { color: colors.muted, fontFamily: fonts.regular, ...typeScale.small },
  name: { color: colors.text, fontFamily: fonts.bold, ...typeScale.subtitle },
  privacy: { backgroundColor: colors.privacySurface, borderCurve: "continuous", borderRadius: radii.lg, gap: spacing.xs, padding: spacing.lg },
  privacyCopy: { color: "#4c5a4c", fontFamily: fonts.regular, ...typeScale.small },
  privacyTitle: { color: colors.success, fontFamily: fonts.bold, ...typeScale.label },
  profile: { alignItems: "center", flexDirection: "row", gap: spacing.md },
  scroll: {
    gap: spacing.lg,
    paddingBottom: spacing.xxl,
    paddingHorizontal: layout.screenPadding,
    paddingTop: (StatusBar.currentHeight ?? 24) + spacing.md,
  },
  title: { color: colors.text, fontFamily: fonts.extraBold, ...typeScale.title },
});
