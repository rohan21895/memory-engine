import { useMemo, useState } from "react";
import { Pressable, StyleSheet, Text, View } from "react-native";

import { copy } from "../copy";
import { fonts } from "../fonts";
import { colors, continuousRadius, layout, radii, spacing, typeScale } from "../tokens";
import { FilterSearchBar } from "./FilterSearchBar";

export type LocationFilterOption = {
  id: string;
  label: string;
  photoCount: number;
};

export type LocationFilterPanelProps = {
  cities: LocationFilterOption[];
  countries: LocationFilterOption[];
  expanded: boolean;
  loadingText?: string;
  onSelect: (locationId: string | null) => void;
  onToggle: () => void;
  selectedLocationId: string | null;
  showHeading?: boolean;
};

function PlaceRow({
  option,
  selected,
  onSelect,
}: {
  option: LocationFilterOption;
  selected: boolean;
  onSelect: (id: string) => void;
}) {
  return (
    <Pressable
      accessibilityHint={`${option.label}. ${selected ? "Selected" : "Not selected"}`}
      accessibilityLabel={`${option.label}. ${copy.filters.photoCount(option.photoCount)}`}
      accessibilityRole="radio"
      accessibilityState={{ checked: selected }}
      onPress={() => onSelect(option.id)}
      style={({ pressed }) => [styles.row, selected ? styles.rowSelected : null, pressed ? styles.pressed : null]}
    >
      <View style={styles.rowCopy}>
        <Text style={[styles.rowLabel, selected ? styles.selectedText : null]}>{option.label}</Text>
        <Text style={styles.detail}>{copy.filters.photoCount(option.photoCount)}</Text>
      </View>
      <View style={[styles.radio, selected ? styles.radioSelected : null]}>
        {selected ? <Text style={styles.check}>✓</Text> : null}
      </View>
    </Pressable>
  );
}

export function LocationFilterPanel({
  cities,
  countries,
  expanded,
  loadingText,
  onSelect,
  onToggle,
  selectedLocationId,
  showHeading = true,
}: LocationFilterPanelProps) {
  const [query, setQuery] = useState("");
  const allPlaces = [...countries, ...cities];
  const selected = allPlaces.find((place) => place.id === selectedLocationId);
  const needle = query.trim().toLocaleLowerCase();
  const filteredCountries = useMemo(
    () => countries.filter((place) => place.label.toLocaleLowerCase().includes(needle)),
    [countries, needle],
  );
  const filteredCities = useMemo(
    () => cities.filter((place) => place.label.toLocaleLowerCase().includes(needle)),
    [cities, needle],
  );
  const noMatches = filteredCountries.length === 0 && filteredCities.length === 0;
  // Three honest states, never one bare "no places": a search that found
  // nothing, a scan still running, and a finished scan over a library whose
  // photos carry no location at all (chat apps strip it before sending).
  const emptyMessage =
    !query && loadingText
      ? loadingText
      : !query && allPlaces.length === 0
        ? `${copy.access.noPlacesTitle}. ${copy.access.noPlacesHelper}`
        : "No places match that search.";

  return (
    <View style={styles.section}>
      {showHeading ? <Pressable
        accessibilityHint={expanded ? "Location choices are open" : "Opens location choices"}
        accessibilityLabel={`Location filter. ${selected?.label ?? "Any place"}`}
        accessibilityRole="button"
        accessibilityState={{ expanded }}
        onPress={onToggle}
        style={({ pressed }) => [styles.heading, pressed ? styles.pressed : null]}
      >
        <View style={styles.number}><Text style={styles.numberText}>2</Text></View>
        <View style={styles.headingCopy}>
          <Text accessibilityRole="header" style={styles.title}>Location</Text>
          <Text style={styles.summary}>{selected?.label ?? "Any place"}</Text>
        </View>
        <Text accessibilityElementsHidden style={styles.chevron}>{expanded ? "⌃" : "⌄"}</Text>
      </Pressable> : null}

      {expanded ? (
        <View accessibilityRole="radiogroup" style={styles.content}>
          <FilterSearchBar
            accessibilityLabel="Search locations"
            onChangeText={setQuery}
            placeholder="Search countries and cities"
            value={query}
          />
          <Pressable
            accessibilityHint="Removes the location filter"
            accessibilityLabel="Any place"
            accessibilityRole="radio"
            accessibilityState={{ checked: selectedLocationId === null }}
            onPress={() => onSelect(null)}
            style={({ pressed }) => [
              styles.row,
              selectedLocationId === null ? styles.rowSelected : null,
              pressed ? styles.pressed : null,
            ]}
          >
            <Text style={[styles.rowLabel, selectedLocationId === null ? styles.selectedText : null]}>Any place</Text>
            <View style={[styles.radio, selectedLocationId === null ? styles.radioSelected : null]}>
              {selectedLocationId === null ? <Text style={styles.check}>✓</Text> : null}
            </View>
          </Pressable>

          {filteredCountries.length > 0 ? (
            <View style={styles.group}>
              <Text style={styles.groupTitle}>Countries</Text>
              {filteredCountries.map((place) => (
                <PlaceRow
                  key={place.id}
                  onSelect={onSelect}
                  option={place}
                  selected={place.id === selectedLocationId}
                />
              ))}
            </View>
          ) : null}
          {filteredCities.length > 0 ? (
            <View style={styles.group}>
              <Text style={styles.groupTitle}>Cities</Text>
              {filteredCities.map((place) => (
                <PlaceRow
                  key={place.id}
                  onSelect={onSelect}
                  option={place}
                  selected={place.id === selectedLocationId}
                />
              ))}
            </View>
          ) : null}
          {noMatches ? (
            <Text accessibilityLiveRegion="polite" style={styles.message}>{emptyMessage}</Text>
          ) : null}
          {loadingText && !noMatches ? (
            <Text accessibilityLiveRegion="polite" style={styles.scanning}>{loadingText}</Text>
          ) : null}
        </View>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  check: { color: colors.ink, fontFamily: fonts.body, fontSize: 16, fontWeight: "700" },
  chevron: { color: colors.gold, fontFamily: fonts.body, fontSize: 22, width: 24 },
  content: { gap: spacing.md, padding: spacing.md, paddingTop: 0 },
  detail: { color: colors.muted, fontFamily: fonts.body, fontVariant: ["tabular-nums"], ...typeScale.small },
  group: { gap: spacing.xs },
  groupTitle: {
    color: colors.gold,
    fontFamily: fonts.body,
    fontWeight: "600",
    paddingTop: spacing.xs,
    textTransform: "uppercase",
    ...typeScale.eyebrow,
  },
  heading: { alignItems: "center", flexDirection: "row", gap: spacing.sm, minHeight: 76, padding: spacing.md },
  headingCopy: { flex: 1, gap: spacing.xxs },
  message: { color: colors.muted, fontFamily: fonts.body, paddingVertical: spacing.md, ...typeScale.body },
  number: {
    alignItems: "center",
    backgroundColor: colors.gold,
    borderRadius: radii.pill,
    height: 30,
    justifyContent: "center",
    width: 30,
  },
  numberText: { color: colors.ink, fontFamily: fonts.body, fontSize: 15, fontWeight: "700" },
  pressed: { opacity: 0.62 },
  radio: {
    alignItems: "center",
    borderColor: colors.muted,
    borderRadius: 13,
    borderWidth: 1.5,
    height: 26,
    justifyContent: "center",
    width: 26,
  },
  radioSelected: { backgroundColor: colors.gold, borderColor: colors.gold },
  row: {
    ...continuousRadius(radii.md),
    alignItems: "center",
    borderColor: colors.hairline,
    borderWidth: 1,
    flexDirection: "row",
    gap: spacing.sm,
    minHeight: layout.primaryButtonHeight,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.xs,
  },
  rowCopy: { flex: 1 },
  rowLabel: { color: colors.text, flex: 1, fontFamily: fonts.body, ...typeScale.label },
  rowSelected: { backgroundColor: colors.panelRaised, borderColor: colors.gold },
  scanning: { color: colors.muted, fontFamily: fonts.body, ...typeScale.small },
  section: {
    ...continuousRadius(radii.lg),
    backgroundColor: colors.panel,
    borderColor: colors.hairline,
    borderWidth: 1,
    overflow: "hidden",
  },
  selectedText: { color: colors.gold, fontWeight: "700" },
  summary: { color: colors.muted, fontFamily: fonts.body, ...typeScale.small },
  title: { color: colors.text, fontFamily: fonts.display, ...typeScale.subtitle },
});
