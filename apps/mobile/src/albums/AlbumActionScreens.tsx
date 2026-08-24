import { Image } from "expo-image";
import { useState } from "react";
import { Pressable, ScrollView, StatusBar, StyleSheet, Text, TextInput, View } from "react-native";

import type { SavedAlbum } from "./album-store";
import type { SharedAlbumPreview } from "../ui/screens/AlbumsScreen";
import { colors, fonts, layout, radii, spacing, typeScale } from "../ui";

function BackButton({ label = "Back", onPress }: { label?: string; onPress: () => void }) {
  return <Pressable accessibilityRole="button" onPress={onPress} style={styles.back}><Text style={styles.backText}>‹ {label}</Text></Pressable>;
}

function PrimaryAction({ disabled, label, onPress }: { disabled?: boolean; label: string; onPress: () => void }) {
  return (
    <Pressable accessibilityRole="button" disabled={disabled} onPress={onPress} style={({ pressed }) => [styles.primary, disabled ? styles.disabled : null, pressed ? styles.pressed : null]}>
      <Text style={styles.primaryText}>{label}</Text>
    </Pressable>
  );
}

const shareContacts = [
  { initial: "E", name: "Ellie", note: "Daughter · on Photeo", color: "#c48f72" },
  { initial: "D", name: "David", note: "Son · on Photeo", color: "#8d9cad" },
  { initial: "J", name: "Joe", note: "Recently shared", color: "#8fa49f" },
];

export function ShareSheet({ albumTitle, onBack, onSent }: { albumTitle: string; onBack: () => void; onSent: (names: string[]) => void }) {
  const [selected, setSelected] = useState<string[]>([]);
  const [invite, setInvite] = useState("");
  const [notice, setNotice] = useState<string | null>(null);
  // TODO(owner): needs backend cross-phone album sharing and invite delivery.
  const toggle = (name: string) => setSelected((current) => current.includes(name) ? current.filter((item) => item !== name) : [...current, name]);
  return (
    <View style={styles.sheetRoot}>
      <StatusBar backgroundColor={colors.scrim} barStyle="light-content" />
      <Pressable accessibilityLabel="Close sharing" onPress={onBack} style={styles.sheetScrim} />
      <View style={styles.tallSheet}>
        <View style={styles.handle} />
        <ScrollView contentContainerStyle={styles.sheetScroll} keyboardShouldPersistTaps="handled">
          <Text style={styles.sheetTitle}>Share with</Text>
          <Text style={styles.helper}>They’ll receive “{albumTitle}” after sharing is connected.</Text>
          <Text style={styles.eyebrow}>Family and recent</Text>
          {shareContacts.map((contact) => {
            const active = selected.includes(contact.name);
            return (
              <Pressable key={contact.name} onPress={() => toggle(contact.name)} style={styles.contact}>
                <View style={[styles.contactAvatar, { backgroundColor: contact.color }]}><Text style={styles.contactInitial}>{contact.initial}</Text></View>
                <View style={styles.grow}><Text style={styles.contactName}>{contact.name}</Text><Text style={styles.contactNote}>{contact.note}</Text></View>
                <View style={[styles.check, active ? styles.checkActive : null]}><Text style={styles.checkText}>{active ? "✓" : ""}</Text></View>
              </Pressable>
            );
          })}
          <View style={styles.inviteCard}>
            <Text style={styles.contactName}>Invite someone new</Text>
            <Text style={styles.contactNote}>Enter an email or phone number. We’ll keep it on this screen only.</Text>
            <TextInput autoCapitalize="none" onChangeText={setInvite} placeholder="Email or phone number" placeholderTextColor={colors.muted} style={styles.field} value={invite} />
            <Pressable disabled={!invite.trim()} onPress={() => setNotice("Invites need the sharing service.")} style={[styles.secondary, !invite.trim() ? styles.disabled : null]}><Text style={styles.secondaryText}>Send invite</Text></Pressable>
          </View>
          {notice ? <Text accessibilityLiveRegion="polite" style={styles.notice}>{notice}</Text> : null}
        </ScrollView>
        <View style={styles.sheetFooter}><PrimaryAction disabled={selected.length === 0} label={selected.length ? `Send album to ${selected.length}` : "Choose someone to share with"} onPress={() => onSent(selected)} /></View>
      </View>
    </View>
  );
}

export function ShareSentScreen({ albumTitle, names, onDone }: { albumTitle: string; names: string[]; onDone: () => void }) {
  return (
    <View style={styles.confirmRoot}>
      <StatusBar backgroundColor={colors.background} barStyle="dark-content" />
      <View style={styles.avatarStack}>{names.slice(0, 2).map((name, index) => <View key={name} style={[styles.sentAvatar, { backgroundColor: index ? "#8d9cad" : "#c48f72" }]}><Text style={styles.sentInitial}>{name.slice(0, 1)}</Text></View>)}</View>
      <Text style={styles.confirmTitle}>Album ready to send</Text>
      <Text style={styles.confirmCopy}>“{albumTitle}” is queued for {names.join(" and ")}. Cross-phone delivery will start when sharing is connected.</Text>
      <View style={styles.stubBanner}><Text style={styles.stubText}>Preview only · nothing was uploaded</Text></View>
      <View style={styles.grow} />
      <PrimaryAction label="Back to album" onPress={onDone} />
    </View>
  );
}

const printSizes = [
  { name: "Small", description: "15 × 15 cm · soft cover", price: 22, width: 46 },
  { name: "Classic", description: "20 × 20 cm · hard cover", price: 28, width: 56 },
  { name: "Large", description: "28 × 28 cm · hard cover", price: 38, width: 66 },
] as const;

export function PrintOrderScreen({ album, onBack, onOrdered, onPreview }: { album: SavedAlbum; onBack: () => void; onOrdered: (size: string, total: number) => void; onPreview: (size: string, total: number) => void }) {
  const [selected, setSelected] = useState(1);
  const choice = printSizes[selected];
  const total = choice.price + 4;
  // TODO(owner): needs backend print pricing, delivery addresses, payment, and fulfillment.
  return (
    <View style={styles.pageRoot}>
      <StatusBar backgroundColor={colors.background} barStyle="dark-content" />
      <ScrollView contentContainerStyle={styles.pageScroll}>
        <BackButton onPress={onBack} />
        <Text style={styles.pageTitle}>Print this album</Text>
        <Text style={styles.helper}>A real photo book, printed and posted to your door.</Text>
        <View style={styles.optionList}>
          {printSizes.map((size, index) => {
            const active = selected === index;
            return (
              <Pressable key={size.name} onPress={() => setSelected(index)} style={[styles.sizeOption, active ? styles.sizeOptionActive : null]}>
                <View style={[styles.book, { width: size.width }]}><View style={styles.bookShine} /></View>
                <View style={styles.grow}><Text style={styles.optionTitle}>{size.name}</Text><Text style={styles.contactNote}>{size.description}</Text></View>
                <Text style={[styles.price, active ? styles.accentText : null]}>£{size.price}</Text>
              </Pressable>
            );
          })}
        </View>
        <View style={styles.summaryCard}>
          <View style={styles.summaryRow}><Text style={styles.summaryLabel}>Photos</Text><Text style={styles.summaryValue}>{album.photos.length}, laid out for you</Text></View>
          <View style={styles.summaryRow}><Text style={styles.summaryLabel}>Paper</Text><Text style={styles.summaryValue}>Thick, matte photo paper</Text></View>
          <View style={styles.summaryRow}><Text style={styles.summaryLabel}>Delivery</Text><Text style={styles.summaryValue}>About 7 days · £4</Text></View>
        </View>
        <View style={styles.privacyNote}><View style={styles.greenDot} /><Text style={styles.privacyNoteText}>Nothing is uploaded until you confirm a real order. This preview does not send photos anywhere.</Text></View>
      </ScrollView>
      <View style={styles.fixedFooter}>
        <Pressable onPress={() => onPreview(choice.name, total)} style={styles.secondary}><Text style={styles.secondaryText}>See how the book looks</Text></Pressable>
        <View style={styles.totalRow}><Text style={styles.helper}>Total including delivery</Text><Text style={styles.total}>£{total}</Text></View>
        <PrimaryAction label={`Preview £${total} order`} onPress={() => onOrdered(choice.name, total)} />
      </View>
    </View>
  );
}

export function PrintPreviewScreen({ album, onBack, onContinue, size, total }: { album: SavedAlbum; onBack: () => void; onContinue: () => void; size: string; total: number }) {
  const previewPhotos = album.photos.slice(0, 4);
  return (
    <View style={styles.pageRoot}>
      <StatusBar backgroundColor={colors.background} barStyle="dark-content" />
      <View style={styles.previewHeader}><BackButton label="Print" onPress={onBack} /><Text style={styles.previewTitle}>Book preview</Text><View style={styles.headerSpacer} /></View>
      <View style={styles.previewStage}>
        <View style={styles.openBook}>
          <View style={styles.bookPage}>{previewPhotos.slice(0, 2).map((photo) => <Image contentFit="cover" key={photo.media_id} source={photo.uri} style={styles.previewPhoto} />)}</View>
          <View style={styles.bookGutter} />
          <View style={styles.bookPage}>{previewPhotos.slice(2, 4).map((photo) => <Image contentFit="cover" key={photo.media_id} source={photo.uri} style={styles.previewPhoto} />)}</View>
        </View>
        <Text style={styles.previewCount}>A sample spread · {size}</Text>
      </View>
      <View style={styles.previewFooter}><Text style={styles.helper}>Preview only. Final layouts are created by the print service.</Text><PrimaryAction label={`Looks good — preview £${total} order`} onPress={onContinue} /></View>
    </View>
  );
}

export function PrintOrderedScreen({ albumTitle, size, total, onDone }: { albumTitle: string; size: string; total: number; onDone: () => void }) {
  return (
    <View style={styles.confirmRoot}>
      <StatusBar backgroundColor={colors.background} barStyle="dark-content" />
      <View style={styles.successMark}><Text style={styles.successCheck}>✓</Text></View>
      <Text style={styles.confirmTitle}>Your book preview is ready</Text>
      <Text style={styles.confirmCopy}>{albumTitle} · {size}{"\n"}Estimated total £{total}. No payment was taken.</Text>
      <View style={styles.stubBanner}><Text style={styles.stubText}>Ordering and delivery need the print service</Text></View>
      <View style={styles.grow} />
      <PrimaryAction label="Back to album" onPress={onDone} />
    </View>
  );
}

export function ManageAlbumSheet({ album, onBack, onDelete, onRename }: { album: SavedAlbum; onBack: () => void; onDelete: () => void; onRename: (title: string) => void }) {
  const [title, setTitle] = useState(album.title);
  return (
    <View style={styles.sheetRoot}>
      <StatusBar backgroundColor={colors.scrim} barStyle="light-content" />
      <Pressable accessibilityLabel="Close album editor" onPress={onBack} style={styles.sheetScrim} />
      <View style={styles.manageSheet}>
        <View style={styles.handle} />
        <Text style={styles.sheetTitle}>Edit album</Text>
        <Text style={styles.eyebrow}>Album name</Text>
        <TextInput onChangeText={setTitle} placeholderTextColor={colors.muted} selectTextOnFocus style={styles.field} value={title} />
        <PrimaryAction disabled={!title.trim()} label="Save name" onPress={() => onRename(title)} />
        <Pressable accessibilityRole="button" onPress={onDelete} style={styles.dangerButton}><Text style={styles.dangerText}>Delete album</Text></Pressable>
      </View>
    </View>
  );
}

export function DeleteAlbumScreen({ albumTitle, onBack, onDelete }: { albumTitle: string; onBack: () => void; onDelete: () => void }) {
  return (
    <View style={styles.sheetRoot}>
      <StatusBar backgroundColor={colors.scrim} barStyle="light-content" />
      <Pressable accessibilityLabel="Cancel deleting album" onPress={onBack} style={styles.sheetScrim} />
      <View style={styles.deleteSheet}>
        <View style={styles.deleteMark}><Text style={styles.deleteMarkText}>!</Text></View>
        <Text style={styles.sheetTitle}>Delete “{albumTitle}”?</Text>
        <Text style={styles.helper}>The album will be removed from this phone. Your original photos will not be changed.</Text>
        <Pressable accessibilityRole="button" onPress={onDelete} style={styles.dangerSolid}><Text style={styles.primaryText}>Delete album</Text></Pressable>
        <Pressable accessibilityRole="button" onPress={onBack} style={styles.secondary}><Text style={styles.secondaryText}>Keep album</Text></Pressable>
      </View>
    </View>
  );
}

export function SharedAlbumScreen({ album, onBack, onShare }: { album: SharedAlbumPreview; onBack: () => void; onShare: () => void }) {
  // TODO(owner): needs backend shared-album metadata and media sync.
  return (
    <ScrollView contentContainerStyle={styles.sharedPage}>
      <StatusBar backgroundColor={album.color} barStyle="light-content" />
      <View style={[styles.sharedHero, { backgroundColor: album.color }]}>
        <BackButton label="Albums" onPress={onBack} />
        <View style={styles.sharedHeroGlow} />
        <View style={styles.sharedHeroCopy}><View style={styles.sharedChip}><Text style={styles.sharedChipText}>Shared by {album.sharedBy}</Text></View><Text style={styles.sharedHeroTitle}>{album.title}</Text><Text style={styles.sharedHeroMeta}>{album.photoCount} photos</Text></View>
      </View>
      <View style={styles.actionsRow}><Pressable onPress={() => undefined} style={styles.secondaryHalf}><Text style={styles.secondaryText}>▸  Play</Text></Pressable><Pressable onPress={onShare} style={styles.secondaryHalf}><Text style={styles.secondaryText}>Share</Text></Pressable></View>
      <View style={styles.stubBanner}><Text style={styles.stubText}>Photos will appear after cross-phone sharing is connected</Text></View>
      <Text style={styles.sharedSection}>All {album.photoCount} photos</Text>
      <View style={styles.placeholderGrid}>{Array.from({ length: 12 }, (_, index) => <View key={index} style={[styles.placeholderTile, { backgroundColor: index % 3 === 0 ? album.color : index % 3 === 1 ? "#d7c2ad" : "#aab6af" }]} />)}</View>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  accentText: { color: colors.gold },
  actionsRow: { flexDirection: "row", gap: spacing.sm, padding: layout.screenPadding },
  avatarStack: { flexDirection: "row", justifyContent: "center", marginBottom: spacing.lg },
  back: { alignSelf: "flex-start", justifyContent: "center", minHeight: 44 },
  backText: { color: colors.muted, fontFamily: fonts.semibold, ...typeScale.small },
  book: { backgroundColor: "#c99b78", borderRadius: 6, height: 64, overflow: "hidden" },
  bookGutter: { backgroundColor: "#d2c9bc", width: 4 },
  bookPage: { backgroundColor: colors.panel, flex: 1, gap: 4, padding: 6 },
  bookShine: { backgroundColor: "#eed9c5", height: 42, left: -12, position: "absolute", top: -9, transform: [{ rotate: "-16deg" }], width: 82 },
  check: { alignItems: "center", borderColor: colors.hairline, borderRadius: 13, borderWidth: 2, height: 26, justifyContent: "center", width: 26 },
  checkActive: { backgroundColor: colors.gold, borderColor: colors.gold },
  checkText: { color: colors.onAccent, fontFamily: fonts.bold, fontSize: 14 },
  confirmCopy: { color: colors.muted, fontFamily: fonts.regular, marginTop: spacing.sm, textAlign: "center", ...typeScale.body },
  confirmRoot: { alignItems: "center", backgroundColor: colors.background, flex: 1, paddingBottom: spacing.lg, paddingHorizontal: 30, paddingTop: (StatusBar.currentHeight ?? 24) + 64 },
  confirmTitle: { color: colors.text, fontFamily: fonts.extraBold, textAlign: "center", ...typeScale.subtitle },
  contact: { alignItems: "center", borderBottomColor: colors.hairline, borderBottomWidth: 1, flexDirection: "row", gap: 14, paddingVertical: 13 },
  contactAvatar: { alignItems: "center", borderRadius: 26, height: 52, justifyContent: "center", width: 52 },
  contactInitial: { color: colors.onAccent, fontFamily: fonts.bold, fontSize: 19 },
  contactName: { color: colors.text, fontFamily: fonts.bold, ...typeScale.label },
  contactNote: { color: colors.muted, fontFamily: fonts.regular, ...typeScale.small },
  dangerButton: { alignItems: "center", borderColor: colors.hairline, borderRadius: 27, borderWidth: 1, height: 54, justifyContent: "center", marginTop: spacing.sm },
  dangerSolid: { alignItems: "center", backgroundColor: colors.error, borderRadius: 28, height: 56, justifyContent: "center", marginTop: spacing.lg },
  dangerText: { color: colors.error, fontFamily: fonts.bold, ...typeScale.label },
  deleteMark: { alignItems: "center", backgroundColor: colors.panelRaised, borderRadius: 28, height: 56, justifyContent: "center", marginBottom: spacing.md, width: 56 },
  deleteMarkText: { color: colors.error, fontFamily: fonts.extraBold, fontSize: 23 },
  deleteSheet: { backgroundColor: colors.panel, borderTopLeftRadius: 26, borderTopRightRadius: 26, bottom: 0, left: 0, paddingBottom: spacing.lg, paddingHorizontal: layout.screenPadding, paddingTop: spacing.lg, position: "absolute", right: 0 },
  disabled: { opacity: 0.38 },
  eyebrow: { color: "#a29a8e", fontFamily: fonts.bold, marginTop: spacing.md, textTransform: "uppercase", ...typeScale.eyebrow },
  field: { backgroundColor: colors.panel, borderColor: colors.hairline, borderRadius: 14, borderWidth: 2, color: colors.text, fontFamily: fonts.semibold, fontSize: 16, height: 52, marginTop: spacing.sm, paddingHorizontal: 14 },
  fixedFooter: { backgroundColor: colors.background, borderTopColor: colors.hairline, borderTopWidth: 1, gap: spacing.sm, paddingBottom: spacing.lg, paddingHorizontal: 26, paddingTop: 14 },
  greenDot: { backgroundColor: colors.success, borderRadius: 5, height: 9, marginTop: 6, width: 9 },
  grow: { flex: 1 },
  handle: { alignSelf: "center", backgroundColor: "#ddd7cc", borderRadius: 2, height: 4, marginBottom: 14, width: 42 },
  headerSpacer: { width: 48 },
  inviteCard: { backgroundColor: colors.background, borderColor: colors.hairline, borderRadius: 18, borderWidth: 1, gap: spacing.sm, marginTop: spacing.lg, padding: spacing.md },
  manageSheet: { backgroundColor: colors.panel, borderTopLeftRadius: 26, borderTopRightRadius: 26, bottom: 0, left: 0, paddingBottom: spacing.lg, paddingHorizontal: layout.screenPadding, paddingTop: 14, position: "absolute", right: 0 },
  notice: { color: colors.gold, fontFamily: fonts.medium, paddingVertical: spacing.sm, ...typeScale.small },
  openBook: { aspectRatio: 1.45, borderColor: "#d2c9bc", borderRadius: 8, borderWidth: 1, flexDirection: "row", overflow: "hidden", width: "92%" },
  optionList: { gap: spacing.sm, paddingTop: spacing.md },
  optionTitle: { color: colors.text, fontFamily: fonts.bold, fontSize: 17.5 },
  pageRoot: { backgroundColor: colors.background, flex: 1 },
  pageScroll: { paddingBottom: 220, paddingHorizontal: layout.screenPadding, paddingTop: (StatusBar.currentHeight ?? 24) + spacing.xs },
  pageTitle: { color: colors.text, fontFamily: fonts.extraBold, marginTop: spacing.sm, ...typeScale.title },
  placeholderGrid: { flexDirection: "row", flexWrap: "wrap", gap: 3 },
  placeholderTile: { aspectRatio: 1, opacity: 0.72, width: "32.8%" },
  previewCount: { color: colors.muted, fontFamily: fonts.medium, marginTop: spacing.md, ...typeScale.small },
  previewFooter: { gap: spacing.md, padding: layout.screenPadding },
  previewHeader: { alignItems: "center", flexDirection: "row", justifyContent: "space-between", paddingHorizontal: layout.screenPadding, paddingTop: (StatusBar.currentHeight ?? 24) + spacing.xs },
  previewPhoto: { flex: 1 },
  previewStage: { alignItems: "center", flex: 1, justifyContent: "center" },
  previewTitle: { color: colors.text, fontFamily: fonts.bold, ...typeScale.label },
  pressed: { opacity: 0.75, transform: [{ scale: 0.985 }] },
  price: { color: colors.text, fontFamily: fonts.extraBold, fontSize: 18 },
  primary: { alignItems: "center", backgroundColor: colors.gold, borderRadius: 28, height: 56, justifyContent: "center", width: "100%" },
  primaryText: { color: colors.onAccent, fontFamily: fonts.bold, ...typeScale.label },
  privacyNote: { backgroundColor: colors.quietSurface, borderRadius: radii.md, flexDirection: "row", gap: spacing.sm, marginTop: spacing.md, padding: 14 },
  privacyNoteText: { color: "#4c463d", flex: 1, fontFamily: fonts.regular, ...typeScale.small },
  secondary: { alignItems: "center", backgroundColor: colors.panel, borderColor: colors.hairline, borderRadius: 26, borderWidth: 1, height: 52, justifyContent: "center", width: "100%" },
  secondaryHalf: { alignItems: "center", backgroundColor: colors.panel, borderColor: colors.hairline, borderRadius: 26, borderWidth: 1, flex: 1, height: 52, justifyContent: "center" },
  secondaryText: { color: colors.text, fontFamily: fonts.bold, ...typeScale.label },
  sentAvatar: { alignItems: "center", borderColor: colors.background, borderRadius: 37, borderWidth: 4, height: 74, justifyContent: "center", marginLeft: -12, width: 74 },
  sentInitial: { color: colors.onAccent, fontFamily: fonts.bold, fontSize: 25 },
  sharedChip: { alignSelf: "flex-start", backgroundColor: "rgba(255,255,255,.9)", borderRadius: 16, paddingHorizontal: spacing.sm, paddingVertical: 6 },
  sharedChipText: { color: colors.text, fontFamily: fonts.bold, fontSize: 13 },
  sharedHero: { height: 340, overflow: "hidden", paddingHorizontal: layout.screenPadding, paddingTop: (StatusBar.currentHeight ?? 24) + spacing.xs },
  sharedHeroCopy: { bottom: spacing.lg, left: layout.screenPadding, position: "absolute", right: layout.screenPadding },
  sharedHeroGlow: { backgroundColor: "rgba(255,255,255,.16)", borderRadius: 170, height: 340, position: "absolute", right: -90, top: -100, width: 340 },
  sharedHeroMeta: { color: "rgba(255,255,255,.85)", fontFamily: fonts.regular, ...typeScale.small },
  sharedHeroTitle: { color: colors.onAccent, fontFamily: fonts.extraBold, fontSize: 29, letterSpacing: -0.8, marginTop: spacing.sm },
  sharedPage: { backgroundColor: colors.background, flexGrow: 1, paddingBottom: spacing.xl },
  sharedSection: { color: colors.text, fontFamily: fonts.bold, padding: layout.screenPadding, ...typeScale.label },
  sheetFooter: { borderTopColor: colors.hairline, borderTopWidth: 1, paddingBottom: spacing.lg, paddingHorizontal: layout.screenPadding, paddingTop: spacing.sm },
  sheetRoot: { backgroundColor: colors.scrim, flex: 1 },
  sheetScrim: { flex: 1 },
  sheetScroll: { paddingBottom: spacing.md, paddingHorizontal: layout.screenPadding },
  sheetTitle: { color: colors.text, fontFamily: fonts.extraBold, ...typeScale.subtitle },
  sizeOption: { alignItems: "center", backgroundColor: colors.panel, borderColor: colors.hairline, borderRadius: radii.lg, borderWidth: 2, flexDirection: "row", gap: 14, minHeight: 88, padding: spacing.md },
  sizeOptionActive: { backgroundColor: colors.panelRaised, borderColor: colors.gold },
  stubBanner: { backgroundColor: colors.quietSurface, borderRadius: radii.md, marginTop: spacing.lg, padding: 14 },
  stubText: { color: colors.muted, fontFamily: fonts.semibold, textAlign: "center", ...typeScale.small },
  successCheck: { color: colors.onAccent, fontFamily: fonts.extraBold, fontSize: 30 },
  successMark: { alignItems: "center", backgroundColor: colors.success, borderRadius: 52, height: 104, justifyContent: "center", marginBottom: spacing.lg, width: 104 },
  summaryCard: { backgroundColor: colors.panel, borderColor: colors.hairline, borderRadius: radii.lg, borderWidth: 1, marginTop: spacing.md, overflow: "hidden" },
  summaryLabel: { color: colors.text, fontFamily: fonts.semibold, ...typeScale.small },
  summaryRow: { borderBottomColor: colors.hairline, borderBottomWidth: 1, flexDirection: "row", justifyContent: "space-between", paddingHorizontal: 18, paddingVertical: spacing.md },
  summaryValue: { color: colors.muted, flex: 1, fontFamily: fonts.regular, textAlign: "right", ...typeScale.small },
  tallSheet: { backgroundColor: colors.panel, borderTopLeftRadius: 26, borderTopRightRadius: 26, bottom: 0, height: "76%", left: 0, paddingTop: 14, position: "absolute", right: 0 },
  total: { color: colors.text, fontFamily: fonts.extraBold, fontSize: 23 },
  totalRow: { alignItems: "baseline", flexDirection: "row", justifyContent: "space-between" },
  helper: { color: colors.muted, fontFamily: fonts.regular, ...typeScale.small },
});
