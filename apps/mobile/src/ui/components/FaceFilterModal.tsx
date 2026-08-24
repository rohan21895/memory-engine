import { Modal, Pressable, ScrollView, StatusBar, StyleSheet, Text, View } from "react-native";

import { fonts } from "../fonts";
import { colors, layout, spacing, typeScale } from "../tokens";
import { FaceFilterPanel, type FaceFilterOption } from "./FaceFilterPanel";
import { PrimaryButton } from "./PrimaryButton";

export function FaceFilterModal({
  loadingText,
  onClose,
  onSelect,
  people,
  peopleAvailable,
  selectedPersonId,
  visible,
}: {
  loadingText?: string;
  onClose: () => void;
  onSelect: (personId: string | null) => void;
  people: FaceFilterOption[];
  peopleAvailable: boolean;
  selectedPersonId: string | null;
  visible: boolean;
}) {
  const selected = people.find((person) => person.id === selectedPersonId);
  return (
    <Modal animationType="slide" onRequestClose={onClose} presentationStyle="pageSheet" visible={visible}>
      <View accessibilityViewIsModal style={styles.root}>
        <StatusBar backgroundColor={colors.panel} barStyle="dark-content" />
        <View style={styles.header}>
          <View>
            <Text accessibilityRole="header" style={styles.title}>Choose a person</Text>
            <Text style={styles.helper}>Pick one person, or choose anyone.</Text>
          </View>
          <Pressable accessibilityLabel="Close person filter" accessibilityRole="button" onPress={onClose} style={styles.close}>
            <Text style={styles.closeText}>✕</Text>
          </Pressable>
        </View>
        <ScrollView contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled">
          <FaceFilterPanel
            expanded
            loadingText={loadingText}
            onSelect={onSelect}
            onToggle={() => undefined}
            people={people}
            peopleAvailable={peopleAvailable}
            selectedPersonId={selectedPersonId}
            showHeading={false}
          />
        </ScrollView>
        <View style={styles.footer}>
          <PrimaryButton
            accessibilityHint="Applies the person filter"
            label={selected ? `Done · ${selected.label}` : "Done · Anyone"}
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
  content: { paddingBottom: spacing.lg, paddingHorizontal: layout.screenPadding },
  footer: { borderTopColor: colors.hairline, borderTopWidth: 1, padding: spacing.md, paddingBottom: spacing.lg },
  header: { alignItems: "center", flexDirection: "row", justifyContent: "space-between", paddingBottom: spacing.md, paddingHorizontal: layout.screenPadding, paddingTop: (StatusBar.currentHeight ?? 24) + spacing.md },
  helper: { color: colors.muted, fontFamily: fonts.regular, ...typeScale.small },
  root: { backgroundColor: colors.panel, flex: 1 },
  title: { color: colors.text, fontFamily: fonts.extraBold, ...typeScale.subtitle },
});
