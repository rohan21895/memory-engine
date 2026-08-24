import { ScrollView, StatusBar, StyleSheet, Text, View } from "react-native";

import { fonts } from "../fonts";
import { colors, layout, radii, spacing, typeScale } from "../tokens";

export function PhotosScreen() {
  return (
    <ScrollView contentContainerStyle={styles.scroll} contentInsetAdjustmentBehavior="automatic">
      <Text accessibilityRole="header" style={styles.title}>Photos</Text>
      <View style={styles.search}><Text style={styles.searchText}>⌕  Search people or places</Text></View>
      <Text style={styles.section}>People</Text>
      <Text style={styles.helper}>People found on this phone will appear here.</Text>
      <Text style={styles.section}>Places</Text>
      <Text style={styles.helper}>Places from your photo library will appear here.</Text>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  helper: { color: colors.muted, fontFamily: fonts.regular, ...typeScale.small },
  scroll: {
    gap: spacing.md,
    paddingBottom: spacing.xxl,
    paddingHorizontal: layout.screenPadding,
    paddingTop: (StatusBar.currentHeight ?? 24) + spacing.md,
  },
  search: { backgroundColor: "#f0eee8", borderRadius: radii.pill, height: 48, justifyContent: "center", paddingHorizontal: spacing.md },
  searchText: { color: "#8b8378", fontFamily: fonts.regular, ...typeScale.label },
  section: { color: colors.text, fontFamily: fonts.bold, paddingTop: spacing.sm, ...typeScale.label },
  title: { color: colors.text, fontFamily: fonts.extraBold, ...typeScale.title },
});
