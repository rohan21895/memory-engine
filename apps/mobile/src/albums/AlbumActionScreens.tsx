import { useState } from "react";
import { Pressable, StatusBar, StyleSheet, Text, TextInput, View } from "react-native";

import type { SavedAlbum } from "./album-store";
import type { SharedAlbumPreview } from "../ui/screens/AlbumsScreen";
import { colors, fonts, layout, radii, spacing, typeScale } from "../ui";

function PrimaryAction({ disabled, label, onPress }: { disabled?: boolean; label: string; onPress: () => void }) {
  return (
    <Pressable accessibilityRole="button" disabled={disabled} onPress={onPress} style={({ pressed }) => [styles.primary, disabled ? styles.disabled : null, pressed ? styles.pressed : null]}>
      <Text style={styles.primaryText}>{label}</Text>
    </Pressable>
  );
}

function UnavailableScreen({ actionLabel, helper, onBack, title }: { actionLabel: string; helper: string; onBack: () => void; title: string }) {
  return (
    <View style={styles.unavailable}>
      <StatusBar backgroundColor={colors.background} barStyle="dark-content" />
      <View style={styles.unavailableMark}><Text style={styles.unavailableMarkText}>···</Text></View>
      <Text accessibilityRole="header" style={styles.unavailableTitle}>{title}</Text>
      <Text style={styles.helper}>{helper}</Text>
      <View style={styles.grow} />
      <PrimaryAction label={actionLabel} onPress={onBack} />
    </View>
  );
}

export function ShareSheet({ onBack }: { albumTitle: string; onBack: () => void; onSent: (names: string[]) => void }) {
  return <UnavailableScreen actionLabel="Back to album" helper="Sharing is not connected in this beta. Nothing was sent or uploaded." onBack={onBack} title="Sharing is coming later" />;
}

export function ShareSentScreen({ onDone }: { albumTitle: string; names: string[]; onDone: () => void }) {
  return <UnavailableScreen actionLabel="Back to album" helper="Sharing is not connected in this beta. Nothing was sent or uploaded." onBack={onDone} title="Sharing is coming later" />;
}

export function PrintOrderScreen({ onBack }: { album: SavedAlbum; onBack: () => void; onOrdered: (size: string, total: number) => void; onPreview: (size: string, total: number) => void }) {
  return <UnavailableScreen actionLabel="Back to album" helper="Photo-book pricing, payment, and delivery are not connected in this beta. No order was placed." onBack={onBack} title="Printing is coming later" />;
}

export function PrintPreviewScreen({ onBack }: { album: SavedAlbum; onBack: () => void; onContinue: () => void; size: string; total: number }) {
  return <UnavailableScreen actionLabel="Back to album" helper="Photo-book previews are not connected in this beta. Nothing was uploaded." onBack={onBack} title="Printing is coming later" />;
}

export function PrintOrderedScreen({ onDone }: { albumTitle: string; size: string; total: number; onDone: () => void }) {
  return <UnavailableScreen actionLabel="Back to album" helper="Ordering is not connected in this beta. No payment was taken and no order was placed." onBack={onDone} title="Printing is coming later" />;
}

export function ManageAlbumSheet({ album, onBack, onDelete, onRename }: { album: SavedAlbum; onBack: () => void; onDelete: () => void; onRename: (title: string) => void }) {
  const [title, setTitle] = useState(album.title);
  return (
    <View style={styles.sheetRoot}>
      <StatusBar backgroundColor={colors.scrim} barStyle="light-content" />
      <Pressable accessibilityLabel="Close album editor" accessibilityRole="button" onPress={onBack} style={styles.sheetScrim} />
      <View style={styles.manageSheet}>
        <View style={styles.handle} />
        <Text accessibilityRole="header" style={styles.sheetTitle}>Edit album</Text>
        <Text style={styles.eyebrow}>Album name</Text>
        <TextInput accessibilityLabel="Album name" onChangeText={setTitle} placeholderTextColor={colors.muted} selectTextOnFocus style={styles.field} value={title} />
        <PrimaryAction disabled={!title.trim()} label="Save name" onPress={() => onRename(title.trim())} />
        <Pressable accessibilityRole="button" onPress={onDelete} style={styles.dangerButton}><Text style={styles.dangerText}>Delete album</Text></Pressable>
      </View>
    </View>
  );
}

export function DeleteAlbumScreen({ albumTitle, onBack, onDelete }: { albumTitle: string; onBack: () => void; onDelete: () => void }) {
  return (
    <View style={styles.sheetRoot}>
      <StatusBar backgroundColor={colors.scrim} barStyle="light-content" />
      <Pressable accessibilityLabel="Cancel deleting album" accessibilityRole="button" onPress={onBack} style={styles.sheetScrim} />
      <View style={styles.deleteSheet}>
        <View style={styles.deleteMark}><Text style={styles.deleteMarkText}>!</Text></View>
        <Text accessibilityRole="header" style={styles.sheetTitle}>Delete “{albumTitle}”?</Text>
        <Text style={styles.helper}>The album will be removed from this phone. Your original photos will not be changed.</Text>
        <Pressable accessibilityRole="button" onPress={onDelete} style={styles.dangerSolid}><Text style={styles.primaryText}>Delete album</Text></Pressable>
        <Pressable accessibilityRole="button" onPress={onBack} style={styles.secondary}><Text style={styles.secondaryText}>Keep album</Text></Pressable>
      </View>
    </View>
  );
}

export function SharedAlbumScreen({ onBack }: { album: SharedAlbumPreview; onBack: () => void; onShare: () => void }) {
  return <UnavailableScreen actionLabel="Back to albums" helper="Shared albums are not connected in this beta. No shared photos are stored on this phone." onBack={onBack} title="Shared albums are coming later" />;
}

const styles = StyleSheet.create({
  dangerButton: { alignItems: "center", borderColor: "#efd6d2", borderRadius: 26, borderWidth: 1, height: 52, justifyContent: "center" },
  dangerSolid: { alignItems: "center", backgroundColor: colors.error, borderRadius: 26, height: 52, justifyContent: "center" },
  dangerText: { color: colors.error, fontFamily: fonts.bold, ...typeScale.label },
  deleteMark: { alignItems: "center", backgroundColor: "#f7e9e7", borderRadius: 30, height: 60, justifyContent: "center", width: 60 },
  deleteMarkText: { color: colors.error, fontFamily: fonts.extraBold, fontSize: 26 },
  deleteSheet: { backgroundColor: colors.panel, borderTopLeftRadius: 26, borderTopRightRadius: 26, bottom: 0, gap: spacing.md, left: 0, paddingBottom: spacing.xl, paddingHorizontal: layout.screenPadding, paddingTop: spacing.lg, position: "absolute", right: 0 },
  disabled: { opacity: 0.45 },
  eyebrow: { color: colors.muted, fontFamily: fonts.bold, textTransform: "uppercase", ...typeScale.eyebrow },
  field: { backgroundColor: colors.background, borderColor: colors.hairline, borderRadius: 14, borderWidth: 1, color: colors.text, fontFamily: fonts.semibold, fontSize: 16, minHeight: 52, paddingHorizontal: spacing.md },
  grow: { flex: 1 },
  handle: { alignSelf: "center", backgroundColor: "#d7d1c8", borderRadius: 2, height: 4, width: 42 },
  helper: { color: colors.muted, fontFamily: fonts.regular, textAlign: "center", ...typeScale.small },
  manageSheet: { backgroundColor: colors.panel, borderTopLeftRadius: 26, borderTopRightRadius: 26, bottom: 0, gap: spacing.md, left: 0, paddingBottom: spacing.xl, paddingHorizontal: layout.screenPadding, paddingTop: 14, position: "absolute", right: 0 },
  pressed: { opacity: 0.75, transform: [{ scale: 0.985 }] },
  primary: { alignItems: "center", backgroundColor: colors.gold, borderRadius: 28, height: 56, justifyContent: "center", width: "100%" },
  primaryText: { color: colors.onAccent, fontFamily: fonts.bold, ...typeScale.label },
  secondary: { alignItems: "center", backgroundColor: colors.panel, borderColor: colors.hairline, borderRadius: 26, borderWidth: 1, height: 52, justifyContent: "center", width: "100%" },
  secondaryText: { color: colors.text, fontFamily: fonts.bold, ...typeScale.label },
  sheetRoot: { backgroundColor: colors.scrim, flex: 1 },
  sheetScrim: { flex: 1 },
  sheetTitle: { color: colors.text, fontFamily: fonts.extraBold, ...typeScale.subtitle },
  unavailable: { backgroundColor: colors.background, flex: 1, paddingBottom: spacing.xl, paddingHorizontal: layout.screenPadding, paddingTop: (StatusBar.currentHeight ?? 24) + spacing.xxl },
  unavailableMark: { alignItems: "center", alignSelf: "center", backgroundColor: colors.quietSurface, borderCurve: "continuous", borderRadius: radii.lg, height: 88, justifyContent: "center", width: 88 },
  unavailableMarkText: { color: colors.gold, fontFamily: fonts.extraBold, fontSize: 26, letterSpacing: 3 },
  unavailableTitle: { color: colors.text, fontFamily: fonts.extraBold, paddingBottom: spacing.sm, paddingTop: spacing.lg, textAlign: "center", ...typeScale.subtitle },
});
