import { Modal, Pressable, StatusBar, StyleSheet, Text, View } from "react-native";

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
  states,
  visible,
}: {
  cities: LocationFilterOption[];
  countries: LocationFilterOption[];
  loadingText?: string;
  onClose: () => void;
  onSelect: (locationId: string | null) => void;
  selectedLocationId: string | null;
  states?: LocationFilterOption[];
  visible: boolean;
}) {
  return (
    <Modal animationType="slide" onRequestClose={onClose} presentationStyle="pageSheet" visible={visible}>
      <View accessibilityViewIsModal style={styles.root}>
        <StatusBar backgroundColor={colors.panel} barStyle="dark-content" />
        <View style={styles.header}>
          <View style={styles.headerCopy}>
            <Text accessibilityRole="header" style={styles.title}>{copy.places.modalTitle}</Text>
            <Text accessibilityLiveRegion="polite" style={styles.helper}>
              {loadingText ??
                (cities.length + countries.length === 0
                  ? copy.access.noPlacesTitle
                  : copy.places.modalHelper)}
            </Text>
          </View>
          <Pressable accessibilityLabel={copy.places.close} accessibilityRole="button" onPress={onClose} style={styles.close}>
            <Text style={styles.closeText}>✕</Text>
          </Pressable>
        </View>
        {/* No ScrollView: the hierarchy virtualizes itself and must own the
            scroll, or a 1,000-place library mounts 1,000 rows. */}
        <View style={styles.content}>
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
            states={states}
          />
        </View>
      </View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  close: { alignItems: "center", backgroundColor: "#f0eee8", borderRadius: 22, height: 44, justifyContent: "center", width: 44 },
  closeText: { color: colors.text, fontFamily: fonts.semibold, fontSize: 15 },
  content: { flex: 1, paddingHorizontal: layout.screenPadding },
  header: { alignItems: "center", flexDirection: "row", gap: spacing.sm, justifyContent: "space-between", paddingBottom: spacing.md, paddingHorizontal: layout.screenPadding, paddingTop: (StatusBar.currentHeight ?? 24) + spacing.md },
  headerCopy: { flex: 1 },
  helper: { color: colors.muted, fontFamily: fonts.regular, ...typeScale.small },
  root: { backgroundColor: colors.panel, flex: 1 },
  title: { color: colors.text, fontFamily: fonts.extraBold, ...typeScale.subtitle },
});
