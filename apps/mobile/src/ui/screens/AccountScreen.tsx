import * as MediaLibrary from "expo-media-library/legacy";
import { useEffect, useState } from "react";
import { Linking, Pressable, ScrollView, StatusBar, StyleSheet, Text, View } from "react-native";

import { fonts } from "../fonts";
import { getPhotoAccess, NO_PHOTO_ACCESS, type PhotoAccess } from "../photo-access";
import { colors, layout, radii, spacing, typeScale } from "../tokens";

function SettingRow({ label, value, onPress }: { label: string; value: string; onPress?: () => void }) {
  const content = <><Text style={styles.rowLabel}>{label}</Text><Text style={styles.rowValue}>{value}</Text></>;
  return onPress ? <Pressable accessibilityRole="button" onPress={onPress} style={({ pressed }) => [styles.row, pressed ? styles.pressed : null]}>{content}</Pressable> : <View style={styles.row}>{content}</View>;
}

export function AccountScreen({
  albumCount,
  onFamily,
  onHelp,
  onSignOut,
}: {
  albumCount: number;
  onFamily?: () => void;
  onHelp?: () => void;
  onSignOut?: () => void;
}) {
  const [photoCount, setPhotoCount] = useState(0);
  const [access, setAccess] = useState<PhotoAccess>(NO_PHOTO_ACCESS);
  useEffect(() => {
    void getPhotoAccess()
      .then(async (current) => {
        setAccess(current);
        // Limited access still returns a real (small) count; a hard "granted"
        // check reported zero photos and read as "the app cannot see anything".
        if (!current.readable) return null;
        return MediaLibrary.getAssetsAsync({ first: 1, mediaType: [MediaLibrary.MediaType.photo] });
      })
      .then((page) => setPhotoCount(page?.totalCount ?? 0))
      .catch(() => setPhotoCount(0));
  }, []);

  const accessValue = !access.readable
    ? "Not allowed  ›"
    : access.limited
      ? "Only selected photos  ›"
      : "All photos";

  return (
    <ScrollView contentContainerStyle={styles.scroll} contentInsetAdjustmentBehavior="automatic">
      <Text accessibilityRole="header" style={styles.title}>Account</Text>
      <View style={styles.profile}>
        <View style={styles.avatar}><Text style={styles.initial}>P</Text></View>
        <View style={styles.profileCopy}><Text style={styles.name}>Your Photeo</Text><Text style={styles.meta}>{photoCount.toLocaleString()} photos · {albumCount} albums</Text></View>
      </View>
      <View style={styles.privacy}>
        <Text style={styles.privacyTitle}><Text style={styles.dot}>●</Text>  Everything stays on your phone</Text>
        <Text style={styles.privacyCopy}>Photos are never uploaded and albums are made without the internet. Account changes never alter your original photos.</Text>
      </View>
      <View style={styles.settings}>
        <SettingRow label="Your family" onPress={onFamily} value="Set up  ›" />
        <SettingRow
          label="Photo access"
          onPress={access.readable && !access.limited ? undefined : () => void Linking.openSettings()}
          value={accessValue}
        />
        <SettingRow label="Album storage" value="On this phone" />
        <SettingRow label="App version" value="1.0.0" />
      </View>
      {onHelp ? <Pressable accessibilityRole="button" onPress={onHelp} style={styles.help}><Text style={styles.helpText}>Help & troubleshooting</Text></Pressable> : null}
      {onSignOut ? (
        <>
          <Pressable accessibilityRole="button" onPress={onSignOut} style={({ pressed }) => [styles.signOut, pressed ? styles.pressed : null]}><Text style={styles.signOutText}>Sign out</Text></Pressable>
          <Text style={styles.signOutNote}>Your photos and albums stay on this phone after you sign out.</Text>
        </>
      ) : null}
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  avatar: { alignItems: "center", backgroundColor: "#c99b78", borderRadius: 36, height: 72, justifyContent: "center", width: 72 },
  dot: { color: colors.success },
  help: { alignSelf: "flex-start", minHeight: 44, justifyContent: "center" },
  helpText: { color: colors.muted, fontFamily: fonts.regular, ...typeScale.small },
  initial: { color: colors.onAccent, fontFamily: fonts.bold, fontSize: 26 },
  meta: { color: colors.muted, fontFamily: fonts.regular, ...typeScale.small },
  name: { color: colors.text, fontFamily: fonts.bold, ...typeScale.subtitle },
  pressed: { opacity: 0.65 },
  privacy: { backgroundColor: colors.privacySurface, borderColor: "#dde5d9", borderCurve: "continuous", borderRadius: radii.lg, borderWidth: 1, gap: spacing.xs, padding: spacing.lg },
  privacyCopy: { color: "#4c5a4c", fontFamily: fonts.regular, fontSize: 14.5, lineHeight: 22 },
  privacyTitle: { color: colors.text, fontFamily: fonts.bold, ...typeScale.label },
  profile: { alignItems: "center", flexDirection: "row", gap: spacing.md },
  profileCopy: { flex: 1 },
  row: { alignItems: "center", borderBottomColor: colors.hairline, borderBottomWidth: 1, flexDirection: "row", justifyContent: "space-between", minHeight: 58, paddingHorizontal: 18 },
  rowLabel: { color: colors.text, fontFamily: fonts.semibold, ...typeScale.label },
  rowValue: { color: colors.muted, fontFamily: fonts.regular, ...typeScale.small },
  scroll: { gap: spacing.lg, paddingBottom: spacing.xxl, paddingHorizontal: layout.screenPadding, paddingTop: (StatusBar.currentHeight ?? 24) + spacing.md },
  settings: { backgroundColor: colors.panel, borderColor: colors.hairline, borderCurve: "continuous", borderRadius: radii.lg, borderWidth: 1, overflow: "hidden" },
  signOut: { alignItems: "center", backgroundColor: colors.panel, borderColor: colors.hairline, borderRadius: 27, borderWidth: 1, height: 54, justifyContent: "center" },
  signOutNote: { color: colors.muted, fontFamily: fonts.regular, marginTop: -spacing.sm, textAlign: "center", ...typeScale.eyebrow },
  signOutText: { color: colors.error, fontFamily: fonts.bold, ...typeScale.label },
  title: { color: colors.text, fontFamily: fonts.extraBold, ...typeScale.title },
});
