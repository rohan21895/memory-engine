import { ScrollView, StatusBar, StyleSheet, Text, View } from "react-native";

import { PrimaryButton } from "../components/PrimaryButton";
import { SecondaryButton } from "../components/SecondaryButton";
import { fonts } from "../fonts";
import { colors, layout, radii, spacing, typeScale } from "../tokens";
import { useFirstLayoutLog } from "../use-first-layout-log";

export function StartScreen({
  busy,
  message,
  onAllow,
  onSkip,
}: {
  busy: boolean;
  message: string | null;
  onAllow: () => void;
  onSkip: () => void;
}) {
  const logFirstLayout = useFirstLayoutLog("photo-permission");
  return (
    <View onLayout={logFirstLayout} style={styles.root}>
      <StatusBar backgroundColor={colors.background} barStyle="dark-content" />
      <ScrollView
        contentContainerStyle={styles.scroll}
        contentInsetAdjustmentBehavior="automatic"
        showsVerticalScrollIndicator={false}
      >
        <View style={styles.tiles}>
          {Array.from({ length: 6 }, (_, index) => (
            <View key={index} style={[styles.tile, index % 2 === 0 ? styles.tileWarm : styles.tileCool]} />
          ))}
        </View>
        <View style={styles.copy}>
          <Text accessibilityRole="header" style={styles.title}>Let Photeo see{`\n`}your photos</Text>
          <Text style={styles.helper}>
            It needs your photo library to pick the good ones. Everything happens on this phone — no internet, no uploads, nothing shared.
          </Text>
          {message ? <Text accessibilityLiveRegion="polite" style={styles.message}>{message}</Text> : null}
        </View>
        <View style={styles.actions}>
          <PrimaryButton
            accessibilityHint="Opens the phone’s photo permission"
            busy={busy}
            label={busy ? "Opening permission…" : "Allow access to photos"}
            onPress={onAllow}
          />
          <SecondaryButton
            accessibilityHint="Continues without photo access"
            disabled={busy}
            label="Not now"
            onPress={onSkip}
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
  message: { color: colors.error, fontFamily: fonts.medium, ...typeScale.small },
  root: { backgroundColor: colors.background, flex: 1 },
  scroll: {
    flexGrow: 1,
    gap: spacing.xl,
    justifyContent: "space-between",
    paddingBottom: spacing.xl,
    paddingHorizontal: layout.screenPadding,
    paddingTop: (StatusBar.currentHeight ?? 24) + spacing.xxl,
  },
  tile: { aspectRatio: 1, borderCurve: "continuous", borderRadius: radii.sm, width: "31.8%" },
  tileCool: { backgroundColor: "#c9d3dd" },
  tileWarm: { backgroundColor: "#e2cdb8" },
  tiles: { flexDirection: "row", flexWrap: "wrap", gap: 6 },
  title: { color: colors.text, fontFamily: fonts.extraBold, ...typeScale.title },
});
