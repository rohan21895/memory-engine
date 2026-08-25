import { useState } from "react";
import { Pressable, ScrollView, StatusBar, StyleSheet, Text, TextInput, View } from "react-native";

import { fonts } from "../fonts";
import { colors, layout, radii, spacing, typeScale } from "../tokens";

export function FamilyScreen({ onBack }: { onBack: () => void }) {
  const [invite, setInvite] = useState("");
  const [notice, setNotice] = useState<string | null>(null);
  // TODO(owner): needs backend family membership and invite delivery. Until it
  // exists this screen shows no members — it used to ship three invented people
  // (Ellie/David/Joe) that testers reasonably read as real accounts.
  return (
    <View style={styles.root}>
      <StatusBar backgroundColor={colors.background} barStyle="dark-content" />
      <ScrollView contentContainerStyle={styles.scroll} keyboardShouldPersistTaps="handled">
        <Pressable accessibilityHint="Returns to your account" accessibilityRole="button" onPress={onBack} style={styles.back}><Text style={styles.backText}>‹ Account</Text></Pressable>
        <Text accessibilityRole="header" style={styles.title}>Your family</Text>
        <Text style={styles.helper}>Family can see the albums you share with them. They can’t see the rest of your photos.</Text>
        <View style={styles.empty}>
          <Text style={styles.emptyTitle}>No one here yet</Text>
          <Text style={styles.emptyCopy}>Family sharing needs an internet connection, and it isn’t switched on in this beta. Nothing is uploaded today.</Text>
        </View>
        <View style={styles.inviteCard}>
          <Text style={styles.inviteTitle}>Invite someone to your family</Text>
          <Text style={styles.inviteHelper}>Email or phone number. They’ll get a link to install Photeo.</Text>
          <TextInput accessibilityLabel="Email or phone number" autoCapitalize="none" autoCorrect={false} onChangeText={setInvite} placeholder="Email or phone number" placeholderTextColor={colors.muted} style={styles.field} value={invite} />
          <Pressable accessibilityRole="button" accessibilityState={{ disabled: !invite.trim() }} disabled={!invite.trim()} onPress={() => setNotice("Invites aren’t connected in this beta. Nothing was sent.")} style={[styles.inviteButton, !invite.trim() ? styles.disabled : null]}><Text style={styles.inviteButtonText}>Send invite</Text></Pressable>
        </View>
        {notice ? <Text accessibilityLiveRegion="polite" style={styles.notice}>{notice}</Text> : null}
        <View style={styles.privacy}><View style={styles.dot} /><Text style={styles.privacyText}>Albums you make stay on this phone. Nothing is shared until family sharing is switched on.</Text></View>
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  back: { alignSelf: "flex-start", minHeight: 44, justifyContent: "center" },
  backText: { color: colors.muted, fontFamily: fonts.semibold, ...typeScale.small },
  disabled: { opacity: 0.38 },
  dot: { backgroundColor: colors.success, borderRadius: 5, height: 9, marginTop: 6, width: 9 },
  empty: { backgroundColor: colors.panel, borderColor: colors.hairline, borderCurve: "continuous", borderRadius: radii.lg, borderWidth: 1, gap: spacing.xs, padding: 18 },
  emptyCopy: { color: colors.muted, fontFamily: fonts.regular, ...typeScale.small },
  emptyTitle: { color: colors.text, fontFamily: fonts.bold, ...typeScale.label },
  field: { backgroundColor: colors.panel, borderColor: colors.hairline, borderCurve: "continuous", borderRadius: 14, borderWidth: 2, color: colors.text, fontFamily: fonts.semibold, height: 52, paddingHorizontal: 14 },
  helper: { color: colors.muted, fontFamily: fonts.regular, ...typeScale.body },
  inviteButton: { alignItems: "center", backgroundColor: colors.gold, borderRadius: 26, height: 52, justifyContent: "center" },
  inviteButtonText: { color: colors.onAccent, fontFamily: fonts.bold, ...typeScale.label },
  inviteCard: { backgroundColor: colors.panel, borderColor: colors.hairline, borderCurve: "continuous", borderRadius: radii.lg, borderWidth: 1, gap: spacing.sm, padding: 18 },
  inviteHelper: { color: colors.muted, fontFamily: fonts.regular, ...typeScale.small },
  inviteTitle: { color: colors.text, fontFamily: fonts.bold, ...typeScale.label },
  notice: { color: colors.goldPressed, fontFamily: fonts.medium, ...typeScale.small },
  privacy: { backgroundColor: colors.quietSurface, borderRadius: radii.md, flexDirection: "row", gap: spacing.sm, padding: 14 },
  privacyText: { color: "#4c463d", flex: 1, fontFamily: fonts.regular, ...typeScale.small },
  root: { backgroundColor: colors.background, flex: 1 },
  scroll: { gap: spacing.md, paddingBottom: spacing.xl, paddingHorizontal: layout.screenPadding, paddingTop: (StatusBar.currentHeight ?? 24) + spacing.xs },
  title: { color: colors.text, fontFamily: fonts.extraBold, ...typeScale.title },
});
