import { Image } from "expo-image";
import { useState } from "react";
import { Pressable, ScrollView, StatusBar, StyleSheet, Text, TextInput, View } from "react-native";

import { contentUri } from "../../faces/face-index";
import { fonts } from "../fonts";
import { colors, layout, radii, spacing, typeScale } from "../tokens";

export type NamePersonTarget = {
  id: string;
  label: string;
  faceThumbUri?: string;
  assetIds: string[];
};

const relations = ["Family", "Daughter", "Son", "Grandchild", "Partner", "Brother", "Sister", "Friend", "Neighbour"];

export function NamePersonScreen({ onBack, person }: { onBack: () => void; person: NamePersonTarget }) {
  const [name, setName] = useState("");
  const [relation, setRelation] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  // TODO(owner): needs backend/local schema support for persisted person names and relationships.
  return (
    <View style={styles.root}>
      <StatusBar backgroundColor={colors.background} barStyle="dark-content" />
      <ScrollView contentContainerStyle={styles.scroll} keyboardShouldPersistTaps="handled">
        <Pressable accessibilityRole="button" onPress={onBack} style={styles.back}><Text style={styles.backText}>‹ Photos</Text></Pressable>
        <Image contentFit="cover" source={person.faceThumbUri ?? (person.assetIds[0] ? contentUri(person.assetIds[0]) : undefined)} style={styles.avatar} />
        <Text accessibilityRole="header" style={styles.title}>Who is this?</Text>
        <Text style={styles.helper}>Add a name so this person is easier to find in your photos.</Text>
        <TextInput autoCapitalize="words" onChangeText={setName} placeholder="Their name" placeholderTextColor={colors.muted} style={styles.field} value={name} />
        <Text style={styles.private}>This name stays on this phone.</Text>
        <Text style={styles.eyebrow}>How do you know them?</Text>
        <View style={styles.chips}>{relations.map((item) => <Pressable accessibilityRole="button" accessibilityState={{ selected: relation === item }} key={item} onPress={() => setRelation(relation === item ? null : item)} style={[styles.chip, relation === item ? styles.chipActive : null]}><Text style={[styles.chipText, relation === item ? styles.chipTextActive : null]}>{item}</Text></Pressable>)}</View>
        <Text style={styles.eyebrow}>Their photos</Text>
        <View style={styles.grid}>{person.assetIds.slice(0, 8).map((id) => <Image contentFit="cover" key={id} source={contentUri(id)} style={styles.tile} />)}</View>
        {notice ? <Text accessibilityLiveRegion="polite" style={styles.notice}>{notice}</Text> : null}
      </ScrollView>
      <View style={styles.footer}>
        <Pressable accessibilityRole="button" accessibilityState={{ disabled: !name.trim() }} disabled={!name.trim()} onPress={() => setNotice("Names aren’t saved yet in this beta. Nothing was changed or uploaded.")} style={[styles.save, !name.trim() ? styles.disabled : null]}><Text style={styles.saveText}>Save name</Text></Pressable>
        <Pressable accessibilityRole="button" onPress={onBack} style={styles.skip}><Text style={styles.skipText}>Skip for now</Text></Pressable>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  avatar: { alignSelf: "center", backgroundColor: colors.hairline, borderRadius: 56, height: 112, marginTop: spacing.sm, width: 112 },
  back: { alignSelf: "flex-start", justifyContent: "center", minHeight: 44 },
  backText: { color: colors.muted, fontFamily: fonts.semibold, ...typeScale.small },
  chip: { backgroundColor: colors.panel, borderColor: colors.hairline, borderRadius: 22, borderWidth: 1, height: 44, justifyContent: "center", paddingHorizontal: 18 },
  chipActive: { backgroundColor: colors.gold, borderColor: colors.gold },
  chipText: { color: colors.text, fontFamily: fonts.semibold, ...typeScale.small },
  chipTextActive: { color: colors.onAccent },
  chips: { flexDirection: "row", flexWrap: "wrap", gap: spacing.xs, marginTop: spacing.sm },
  disabled: { opacity: 0.38 },
  eyebrow: { color: colors.muted, fontFamily: fonts.bold, marginTop: spacing.lg, textTransform: "uppercase", ...typeScale.eyebrow },
  field: { backgroundColor: colors.panel, borderColor: colors.hairline, borderCurve: "continuous", borderRadius: radii.md, borderWidth: 2, color: colors.text, fontFamily: fonts.semibold, fontSize: 19, height: 58, marginTop: spacing.lg, paddingHorizontal: spacing.md },
  footer: { backgroundColor: colors.background, borderTopColor: colors.hairline, borderTopWidth: 1, paddingBottom: spacing.md, paddingHorizontal: layout.screenPadding, paddingTop: spacing.sm },
  grid: { flexDirection: "row", flexWrap: "wrap", gap: 5, marginTop: spacing.sm },
  helper: { color: colors.muted, fontFamily: fonts.regular, textAlign: "center", ...typeScale.body },
  notice: { color: colors.goldPressed, fontFamily: fonts.medium, paddingTop: spacing.md, textAlign: "center", ...typeScale.small },
  private: { color: colors.muted, fontFamily: fonts.regular, marginTop: spacing.xs, textAlign: "center", ...typeScale.eyebrow },
  root: { backgroundColor: colors.background, flex: 1 },
  save: { alignItems: "center", backgroundColor: colors.gold, borderRadius: 28, height: 56, justifyContent: "center" },
  saveText: { color: colors.onAccent, fontFamily: fonts.bold, ...typeScale.label },
  scroll: { paddingBottom: spacing.xl, paddingHorizontal: layout.screenPadding, paddingTop: (StatusBar.currentHeight ?? 24) + spacing.xs },
  skip: { alignItems: "center", height: 46, justifyContent: "center" },
  skipText: { color: colors.muted, fontFamily: fonts.semibold, ...typeScale.small },
  tile: { aspectRatio: 1, backgroundColor: colors.hairline, borderRadius: 9, width: "23.8%" },
  title: { color: colors.text, fontFamily: fonts.extraBold, marginTop: spacing.md, textAlign: "center", ...typeScale.title },
});
