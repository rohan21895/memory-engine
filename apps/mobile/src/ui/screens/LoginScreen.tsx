import { useState } from "react";
import { Pressable, ScrollView, StatusBar, StyleSheet, Text, TextInput, View } from "react-native";

import { PrimaryButton } from "../components/PrimaryButton";
import { SecondaryButton } from "../components/SecondaryButton";
import { fonts } from "../fonts";
import { colors, layout, radii, spacing, typeScale } from "../tokens";
import { useFirstLayoutLog } from "../use-first-layout-log";

export function LoginScreen({ onContinue }: { onContinue: () => void }) {
  const [mode, setMode] = useState<"Phone" | "Email">("Phone");
  const [value, setValue] = useState("");
  const [notice, setNotice] = useState<string | null>(null);
  const logFirstLayout = useFirstLayoutLog("login");
  // TODO(owner): needs backend OTP delivery and verification.
  const sendCode = () => setNotice("Account sign-in needs the sharing service. You can use Photeo without an account today.");
  return (
    <View onLayout={logFirstLayout} style={styles.root}>
      <StatusBar backgroundColor={colors.background} barStyle="dark-content" />
      <ScrollView contentContainerStyle={styles.scroll} contentInsetAdjustmentBehavior="automatic" keyboardShouldPersistTaps="handled">
        <View style={styles.mark} />
        <View style={styles.copy}>
          <Text accessibilityRole="header" style={styles.title}>Sign in to Photeo</Text>
          <Text style={styles.helper}>Only so family can share albums with you. Your photos still stay on this phone.</Text>
        </View>
        <View style={styles.segmented}>
          {(["Phone", "Email"] as const).map((option) => <Pressable key={option} onPress={() => { setMode(option); setValue(""); }} style={[styles.segment, mode === option ? styles.segmentActive : null]}><Text style={[styles.segmentText, mode === option ? styles.segmentTextActive : null]}>{option}</Text></Pressable>)}
        </View>
        <TextInput
          autoCapitalize="none"
          keyboardType={mode === "Phone" ? "phone-pad" : "email-address"}
          onChangeText={setValue}
          placeholder={mode === "Phone" ? "Phone number" : "you@example.com"}
          placeholderTextColor={colors.muted}
          style={[styles.field, value ? styles.fieldActive : null]}
          value={value}
        />
        <Text style={styles.fieldHelper}>We’ll text or email you a 6-digit code. No password to remember.</Text>
        {notice ? <Text accessibilityLiveRegion="polite" style={styles.notice}>{notice}</Text> : null}
        <View style={styles.flex} />
        <View style={styles.actions}>
          <PrimaryButton accessibilityHint="Requests a sign-in code" disabled={!value.trim()} label="Send me a code" onPress={sendCode} />
          <SecondaryButton accessibilityHint="Uses Photeo without signing in" label="Use Photeo without an account" onPress={onContinue} quiet />
        </View>
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  actions: { gap: spacing.xs },
  copy: { gap: spacing.sm },
  field: { backgroundColor: colors.panel, borderColor: colors.hairline, borderCurve: "continuous", borderRadius: radii.md, borderWidth: 2, color: colors.text, fontFamily: fonts.semibold, fontSize: 18, height: 58, paddingHorizontal: spacing.md },
  fieldActive: { borderColor: colors.gold },
  fieldHelper: { color: colors.muted, fontFamily: fonts.regular, ...typeScale.small },
  flex: { flex: 1 },
  helper: { color: colors.muted, fontFamily: fonts.regular, ...typeScale.body },
  mark: { backgroundColor: "#e6c8b0", borderCurve: "continuous", borderRadius: radii.lg, height: 64, width: 64 },
  notice: { color: colors.gold, fontFamily: fonts.medium, ...typeScale.small },
  root: { backgroundColor: colors.background, flex: 1 },
  scroll: { flexGrow: 1, gap: spacing.md, paddingBottom: spacing.xl, paddingHorizontal: layout.screenPadding + 6, paddingTop: (StatusBar.currentHeight ?? 24) + spacing.xl },
  segment: { alignItems: "center", borderRadius: 11, flex: 1, height: 44, justifyContent: "center" },
  segmentActive: { backgroundColor: colors.panel },
  segmentText: { color: colors.muted, fontFamily: fonts.bold, ...typeScale.label },
  segmentTextActive: { color: colors.gold },
  segmented: { backgroundColor: "#f0eee8", borderRadius: 14, flexDirection: "row", gap: 6, padding: 4 },
  title: { color: colors.text, fontFamily: fonts.extraBold, ...typeScale.title },
});
