import { Modal, Pressable, ScrollView, StatusBar, StyleSheet, Text, View } from "react-native";

import { copy } from "../copy";
import { fonts } from "../fonts";
import { colors, layout, spacing, typeScale } from "../tokens";
import { LocationFilterPanel, type LocationFilterOption } from "./LocationFilterPanel";

export function LocationFilterModal({
  cities,
  countries,
  loadingText,
  onClose,
  onSelect,
  selectedLocationId,
  visible,
}: {
  cities: LocationFilterOption[];
  countries: LocationFilterOption[];
  loadingText?: string;
  onClose: () => void;
  onSelect: (locationId: string | null) => void;
  selectedLocationId: string | null;
  visible: boolean;
}) {
  return (
    <Modal animationType="slide" onRequestClose={onClose} presentationStyle="pageSheet" visible={visible}>
      <View accessibilityViewIsModal style={styles.root}>
        <StatusBar backgroundColor={colors.panel} barStyle="dark-content" />
        <View style={styles.header}>
          <View>
            <Text accessibilityRole="header" style={styles.title}>Choose a place</Text>
            <Text accessibilityLiveRegion="polite" style={styles.helper}>
              {loadingText ??
                (cities.length + countries.length === 0
                  ? copy.access.noPlacesTitle
                  : "Search the places found in your library.")}
            </Text>
          </View>
          <Pressable accessibilityLabel="Close place filter" accessibilityRole="button" onPress={onClose} style={styles.close}>
            <Text style={styles.closeText}>✕</Text>
          </Pressable>
        </View>
        <ScrollView contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled">
          <LocationFilterPanel
            cities={cities}
            countries={countries}
            expanded
            loadingText={loadingText}
            onSelect={(locationId) => {
              onSelect(locationId);
              onClose();
            }}
            onToggle={() => undefined}
            selectedLocationId={selectedLocationId}
            showHeading={false}
          />
        </ScrollView>
      </View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  close: { alignItems: "center", backgroundColor: "#f0eee8", borderRadius: 17, height: 34, justifyContent: "center", width: 34 },
  closeText: { color: colors.text, fontFamily: fonts.semibold, fontSize: 15 },
  content: { paddingBottom: spacing.xl, paddingHorizontal: layout.screenPadding },
  header: { alignItems: "center", flexDirection: "row", justifyContent: "space-between", paddingBottom: spacing.md, paddingHorizontal: layout.screenPadding, paddingTop: (StatusBar.currentHeight ?? 24) + spacing.md },
  helper: { color: colors.muted, fontFamily: fonts.regular, ...typeScale.small },
  root: { backgroundColor: colors.panel, flex: 1 },
  title: { color: colors.text, fontFamily: fonts.extraBold, ...typeScale.subtitle },
});
