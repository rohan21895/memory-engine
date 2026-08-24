import { useState } from "react";
import { Pressable, ScrollView, StatusBar, StyleSheet, Text, TextInput, View } from "react-native";

import { fonts } from "../fonts";
import { colors, layout, radii, spacing, typeScale } from "../tokens";

const members = [
  { initial: "E", name: "Ellie", sub: "Daughter · on Photeo" },
  { initial: "D", name: "David", sub: "Son · on Photeo" },
  { initial: "J", name: "Joe", sub: "Father · on Photeo" },
];

export function FamilyScreen({ onBack }: { onBack: () => void }) {
  const [invite, setInvite] = useState("");
  const [notice, setNotice] = useState<string | null>(null);
  // TODO(owner): needs backend family membership and invite delivery.
  return (
    <View style={styles.root}>
      <StatusBar backgroundColor={colors.background} barStyle="dark-content" />
      <ScrollView contentContainerStyle={styles.scroll} keyboardShouldPersistTaps="handled">
        <Pressable accessibilityRole="button" onPress={onBack} style={styles.back}><Text style={styles.backText}>‹ Account</Text></Pressable>
        <Text accessibilityRole="header" style={styles.title}>Your family</Text>
        <Text style={styles.helper}>Family can see the albums you share with them. They can’t see the rest of your photos.</Text>
        <View style={styles.members}>
          {members.map((member, index) => (
            <View key={member.name} style={styles.member}>
              <View style={[styles.avatar, { backgroundColor: index === 0 ? "#c99b78" : index === 1 ? "#9aa9bb" : "#94aaa6" }]}><Text style={styles.initial}>{member.initial}</Text></View>
              <View style={styles.memberCopy}><Text style={styles.name}>{member.name}</Text><Text style={styles.sub}>{member.sub}</Text></View>
              <Pressable onPress={() => setNotice("Family changes need the sharing service.")} style={styles.remove}><Text style={styles.removeText}>Remove</Text></Pressable>
            </View>
          ))}
        </View>
        <View style={styles.inviteCard}>
          <Text style={styles.inviteTitle}>Invite someone to your family</Text>
          <Text style={styles.inviteHelper}>Email or phone number. They’ll get a link to install Photeo.</Text>
          <TextInput onChangeText={setInvite} placeholder="Email or phone number" placeholderTextColor={colors.muted} style={styles.field} value={invite} />
          <Pressable disabled={!invite.trim()} onPress={() => setNotice("Invites need the sharing service.")} style={[styles.inviteButton, !invite.trim() ? styles.disabled : null]}><Text style={styles.inviteButtonText}>Send invite</Text></Pressable>
        </View>
        {notice ? <Text accessibilityLiveRegion="polite" style={styles.notice}>{notice}</Text> : null}
        <View style={styles.privacy}><View style={styles.dot} /><Text style={styles.privacyText}>Removing someone stops future sharing. Albums they already have stay on their phone.</Text></View>
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  avatar: { alignItems: "center", borderRadius: 26, height: 52, justifyContent: "center", width: 52 },
  back: { alignSelf: "flex-start", minHeight: 44, justifyContent: "center" },
  backText: { color: colors.muted, fontFamily: fonts.semibold, ...typeScale.small },
  disabled: { opacity: 0.38 },
  dot: { backgroundColor: colors.success, borderRadius: 5, height: 9, marginTop: 6, width: 9 },
  field: { backgroundColor: colors.panel, borderColor: colors.hairline, borderCurve: "continuous", borderRadius: 14, borderWidth: 2, color: colors.text, fontFamily: fonts.semibold, height: 52, paddingHorizontal: 14 },
  helper: { color: colors.muted, fontFamily: fonts.regular, ...typeScale.body },
  initial: { color: colors.onAccent, fontFamily: fonts.bold, fontSize: 19 },
  inviteButton: { alignItems: "center", backgroundColor: colors.gold, borderRadius: 26, height: 52, justifyContent: "center" },
  inviteButtonText: { color: colors.onAccent, fontFamily: fonts.bold, ...typeScale.label },
  inviteCard: { backgroundColor: colors.panel, borderColor: colors.hairline, borderCurve: "continuous", borderRadius: radii.lg, borderWidth: 1, gap: spacing.sm, padding: 18 },
  inviteHelper: { color: colors.muted, fontFamily: fonts.regular, ...typeScale.small },
  inviteTitle: { color: colors.text, fontFamily: fonts.bold, ...typeScale.label },
  member: { alignItems: "center", borderBottomColor: colors.hairline, borderBottomWidth: 1, flexDirection: "row", gap: 14, padding: 14 },
  memberCopy: { flex: 1 },
  members: { backgroundColor: colors.panel, borderColor: colors.hairline, borderCurve: "continuous", borderRadius: radii.lg, borderWidth: 1, overflow: "hidden" },
  name: { color: colors.text, fontFamily: fonts.bold, ...typeScale.label },
  notice: { color: colors.gold, fontFamily: fonts.medium, ...typeScale.small },
  privacy: { backgroundColor: colors.quietSurface, borderRadius: radii.md, flexDirection: "row", gap: spacing.sm, padding: 14 },
  privacyText: { color: "#4c463d", flex: 1, fontFamily: fonts.regular, ...typeScale.small },
  remove: { borderColor: colors.hairline, borderRadius: 19, borderWidth: 1, justifyContent: "center", minHeight: 38, paddingHorizontal: 14 },
  removeText: { color: colors.error, fontFamily: fonts.bold, fontSize: 13.5 },
  root: { backgroundColor: colors.background, flex: 1 },
  scroll: { gap: spacing.md, paddingBottom: spacing.xl, paddingHorizontal: layout.screenPadding, paddingTop: (StatusBar.currentHeight ?? 24) + spacing.xs },
  sub: { color: colors.success, fontFamily: fonts.regular, ...typeScale.small },
  title: { color: colors.text, fontFamily: fonts.extraBold, ...typeScale.title },
});
