import { Modal, Pressable, StatusBar, StyleSheet, Text, View } from "react-native";

import { fonts } from "../fonts";
import { colors, layout, spacing, typeScale } from "../tokens";
import type { FaceMatchMode } from "../../faces/face-filter";
import { FaceFilterPanel, type FaceFilterOption } from "./FaceFilterPanel";
import { PrimaryButton } from "./PrimaryButton";

export function FaceFilterModal({
  loadingText,
  matchMode,
  onClose,
  onMatchModeChange,
  onSelect,
  people,
  peopleAvailable,
  selectedPersonIds,
  visible,
}: {
  loadingText?: string;
  matchMode: FaceMatchMode;
  onClose: () => void;
  onMatchModeChange: (mode: FaceMatchMode) => void;
  onSelect: (personId: string | null) => void;
  people: FaceFilterOption[];
  peopleAvailable: boolean;
  selectedPersonIds: readonly string[];
  visible: boolean;
}) {
  return (
    <Modal animationType="slide" onRequestClose={onClose} presentationStyle="pageSheet" visible={visible}>
      <View accessibilityViewIsModal style={styles.root}>
        <StatusBar backgroundColor={colors.panel} barStyle="dark-content" />
        <View style={styles.header}>
          <View>
            <Text accessibilityRole="header" style={styles.title}>Choose a person</Text>
            <Text style={styles.helper}>Choose anyone, or pick several people.</Text>
          </View>
          <Pressable accessibilityLabel="Close person filter" accessibilityRole="button" onPress={onClose} style={styles.close}>
            <Text style={styles.closeText}>✕</Text>
          </Pressable>
        </View>
        {/* No ScrollView: the face grid virtualizes itself and must own the
            scroll, or a 913-person library mounts 913 tiles the moment this
            sheet opens. Same shape as LocationFilterModal. */}
        <View style={styles.content}>
          <FaceFilterPanel
            expanded
            loadingText={loadingText}
            modeControl={(
              <View style={styles.segmented}>
                {([
                  ["any", "In any of their photos"],
                  ["all", "Together in the same photo"],
                ] as const).map(([mode, label]) => {
                  const active = matchMode === mode;
                  return (
                    <Pressable accessibilityRole="tab" accessibilityState={{ selected: active }} key={mode} onPress={() => onMatchModeChange(mode)} style={[styles.segment, active ? styles.segmentActive : null]}>
                      <Text style={[styles.segmentText, active ? styles.segmentTextActive : null]}>{label}</Text>
                    </Pressable>
                  );
                })}
              </View>
            )}
            onSelect={onSelect}
            onToggle={() => undefined}
            people={people}
            peopleAvailable={peopleAvailable}
            selectedPersonIds={selectedPersonIds}
            selectionHint="Pick as many people as you like. Tap a face again to remove it."
            showHeading={false}
          />
        </View>
        <View style={styles.footer}>
          <PrimaryButton
            accessibilityHint="Applies the person filter"
            label={selectedPersonIds.length > 0 ? `Done · ${selectedPersonIds.length} ${selectedPersonIds.length === 1 ? "person" : "people"}` : "Done · Anyone"}
            onPress={onClose}
          />
        </View>
      </View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  close: { alignItems: "center", backgroundColor: "#f0eee8", borderRadius: 17, height: 34, justifyContent: "center", width: 34 },
  closeText: { color: colors.text, fontFamily: fonts.semibold, fontSize: 15 },
  // Flexed, not padded-and-scrolled: the panel inside owns the scroll now.
  content: { flex: 1, paddingBottom: spacing.lg, paddingHorizontal: layout.screenPadding },
  footer: { borderTopColor: colors.hairline, borderTopWidth: 1, padding: spacing.md, paddingBottom: spacing.lg },
  header: { alignItems: "center", flexDirection: "row", justifyContent: "space-between", paddingBottom: spacing.md, paddingHorizontal: layout.screenPadding, paddingTop: (StatusBar.currentHeight ?? 24) + spacing.md },
  helper: { color: colors.muted, fontFamily: fonts.regular, ...typeScale.small },
  root: { backgroundColor: colors.panel, flex: 1 },
  segment: { alignItems: "center", borderRadius: 10, flex: 1, justifyContent: "center", minHeight: 44, paddingHorizontal: spacing.xs },
  segmentActive: { backgroundColor: colors.panel, borderColor: colors.gold, borderWidth: 1 },
  segmented: { backgroundColor: "#f0eee8", borderRadius: 13, flexDirection: "row", gap: 4, padding: 4 },
  segmentText: { color: colors.muted, fontFamily: fonts.bold, fontSize: 13, lineHeight: 17, textAlign: "center" },
  segmentTextActive: { color: colors.gold },
  title: { color: colors.text, fontFamily: fonts.extraBold, ...typeScale.subtitle },
});
