import { useMemo } from "react";
import { Pressable, StyleSheet, Text, View } from "react-native";

import { copy } from "../copy";
import { fonts } from "../fonts";
import { colors, continuousRadius, radii, spacing, typeScale } from "../tokens";
import { PlaceHierarchyList } from "./PlaceHierarchyList";
import { getStates } from "./place-source";
import type { PlaceInput } from "./place-tree";

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
  /** Optional: callers without a state tier get it from the index instead. */
  states?: LocationFilterOption[];
};

/** Height the accordion gives the virtualized list so it can scroll itself. */
const ACCORDION_HEIGHT = 420;

function toInput(option: LocationFilterOption): PlaceInput {
  return { id: option.id, name: option.label, count: option.photoCount };
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
  states,
}: LocationFilterPanelProps) {
  const stateOptions = useMemo(
    // The index is the same one `cities`/`countries` came from, so refresh the
    // state tier whenever either of those moves (a scan grows all three).
    () => states ?? getStates().map((state) => ({ id: state.id, label: state.name, photoCount: state.count })),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [states, cities, countries],
  );

  const countryInputs = useMemo(() => countries.map(toInput), [countries]);
  const stateInputs = useMemo(() => stateOptions.map(toInput), [stateOptions]);
  const placeInputs = useMemo(() => cities.map(toInput), [cities]);

  const selectedLabel = useMemo(() => {
    if (!selectedLocationId) return copy.places.anyPlace;
    for (const group of [countries, stateOptions, cities]) {
      for (const option of group) {
        if (option.id === selectedLocationId) return option.label;
      }
    }
    return copy.places.anyPlace;
  }, [cities, countries, selectedLocationId, stateOptions]);

  return (
    <View style={showHeading ? styles.section : styles.bare}>
      {showHeading ? (
        <Pressable
          accessibilityHint={expanded ? "Location choices are open" : "Opens location choices"}
          accessibilityLabel={`Location filter. ${selectedLabel}`}
          accessibilityRole="button"
          accessibilityState={{ expanded }}
          onPress={onToggle}
          style={({ pressed }) => [styles.heading, pressed ? styles.pressed : null]}
        >
          <View style={styles.number}><Text style={styles.numberText}>2</Text></View>
          <View style={styles.headingCopy}>
            <Text accessibilityRole="header" style={styles.title}>Location</Text>
            <Text style={styles.summary}>{selectedLabel}</Text>
          </View>
          <Text accessibilityElementsHidden style={styles.chevron}>{expanded ? "⌃" : "⌄"}</Text>
        </Pressable>
      ) : null}

      {expanded ? (
        <PlaceHierarchyList
          countries={countryInputs}
          loadingText={loadingText}
          onSelect={onSelect}
          places={placeInputs}
          selectedLocationId={selectedLocationId}
          states={stateInputs}
          style={showHeading ? styles.accordionList : styles.fullList}
        />
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  accordionList: { height: ACCORDION_HEIGHT, paddingBottom: spacing.md, paddingHorizontal: spacing.md },
  bare: { flex: 1 },
  chevron: { color: colors.gold, fontFamily: fonts.body, fontSize: 22, width: 24 },
  fullList: { flex: 1 },
  heading: { alignItems: "center", flexDirection: "row", gap: spacing.sm, minHeight: 76, padding: spacing.md },
  headingCopy: { flex: 1, gap: spacing.xxs },
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
  section: {
    ...continuousRadius(radii.lg),
    backgroundColor: colors.panel,
    borderColor: colors.hairline,
    borderWidth: 1,
    overflow: "hidden",
  },
  summary: { color: colors.muted, fontFamily: fonts.body, ...typeScale.small },
  title: { color: colors.text, fontFamily: fonts.display, ...typeScale.subtitle },
});
