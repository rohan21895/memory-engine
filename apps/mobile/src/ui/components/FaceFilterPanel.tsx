import { Image } from "expo-image";
import { useMemo, useState } from "react";
import { Pressable, StyleSheet, Text, View } from "react-native";

import { copy } from "../copy";
import { fonts } from "../fonts";
import { colors, continuousRadius, layout, radii, spacing, typeScale } from "../tokens";
import { FilterSearchBar } from "./FilterSearchBar";

export type FaceFilterOption = {
  faceCount: number;
  id: string;
  imageUri: string;
  label: string;
  photoCount: number;
};

export type FaceFilterPanelProps = {
  expanded: boolean;
  loadingText?: string;
  onSelect: (personId: string | null) => void;
  onToggle: () => void;
  people: FaceFilterOption[];
  peopleAvailable: boolean;
  selectedPersonId: string | null;
  showHeading?: boolean;
};

export function FaceFilterPanel({
  expanded,
  loadingText,
  onSelect,
  onToggle,
  people,
  peopleAvailable,
  selectedPersonId,
  showHeading = true,
}: FaceFilterPanelProps) {
  const [query, setQuery] = useState("");
  const selected = people.find((person) => person.id === selectedPersonId);
  const filteredPeople = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase();
    if (!needle) return people;
    // TODO: Include a person's chosen name here when naming ships.
    return people.filter((person) =>
      `${person.label} ${person.faceCount} faces ${person.photoCount} photos`
        .toLocaleLowerCase()
        .includes(needle),
    );
  }, [people, query]);

  return (
    <View style={styles.section}>
      {showHeading ? <Pressable
        accessibilityHint={expanded ? "Face choices are open" : "Opens face choices"}
        accessibilityLabel={`Face filter. ${selected ? selected.label : "Anyone"}`}
        accessibilityRole="button"
        accessibilityState={{ expanded }}
        onPress={onToggle}
        style={({ pressed }) => [styles.heading, pressed ? styles.pressed : null]}
      >
        <View style={styles.number}><Text style={styles.numberText}>1</Text></View>
        <View style={styles.headingCopy}>
          <Text accessibilityRole="header" style={styles.title}>Face</Text>
          <Text style={styles.summary}>
            {selected ? `${selected.label} · ${copy.filters.photoCount(selected.photoCount)}` : "Anyone"}
          </Text>
        </View>
        <Text accessibilityElementsHidden style={styles.chevron}>{expanded ? "⌃" : "⌄"}</Text>
      </Pressable> : null}

      {expanded ? (
        <View style={styles.content}>
          <FilterSearchBar
            accessibilityLabel="Search faces"
            onChangeText={setQuery}
            placeholder="Search faces"
            value={query}
          />
          <Pressable
            accessibilityHint="Removes the face filter"
            accessibilityLabel="Anyone"
            accessibilityRole="radio"
            accessibilityState={{ checked: selectedPersonId === null }}
            onPress={() => onSelect(null)}
            style={({ pressed }) => [
              styles.anyRow,
              selectedPersonId === null ? styles.anyRowSelected : null,
              pressed ? styles.pressed : null,
            ]}
          >
            <Text style={[styles.anyLabel, selectedPersonId === null ? styles.selectedText : null]}>
              Anyone
            </Text>
            <Text style={styles.check}>{selectedPersonId === null ? "✓" : ""}</Text>
          </Pressable>

          {filteredPeople.length > 0 ? (
            <View accessibilityRole="radiogroup" style={styles.grid}>
              {filteredPeople.map((person) => {
                const isSelected = person.id === selectedPersonId;
                return (
                  <Pressable
                    accessibilityHint={`${person.label}. ${isSelected ? "Selected" : "Not selected"}`}
                    accessibilityLabel={`${person.label}. ${copy.filters.photoCount(person.photoCount)}`}
                    accessibilityRole="radio"
                    accessibilityState={{ checked: isSelected }}
                    key={person.id}
                    onPress={() => onSelect(person.id)}
                    style={({ pressed }) => [styles.person, pressed ? styles.pressed : null]}
                  >
                    <View style={[styles.avatarRing, isSelected ? styles.avatarRingSelected : null]}>
                      <Image
                        accessibilityIgnoresInvertColors
                        cachePolicy="memory-disk"
                        contentFit="cover"
                        recyclingKey={person.id}
                        source={person.imageUri}
                        style={styles.avatar}
                        transition={100}
                      />
                    </View>
                    <Text numberOfLines={1} style={[styles.personLabel, isSelected ? styles.selectedText : null]}>
                      {person.label}
                    </Text>
                    <Text style={styles.photoCount}>{copy.filters.photoCount(person.photoCount)}</Text>
                  </Pressable>
                );
              })}
            </View>
          ) : (
            <Text accessibilityLiveRegion="polite" style={styles.message}>
              {query
                ? "No faces match that search."
                : peopleAvailable
                  ? loadingText ?? "No people found yet."
                  : "Face filtering isn’t available on this phone."}
            </Text>
          )}
          {loadingText && filteredPeople.length > 0 ? (
            <Text accessibilityLiveRegion="polite" style={styles.scanning}>{loadingText}</Text>
          ) : null}
        </View>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  anyLabel: { color: colors.text, fontFamily: fonts.body, ...typeScale.label },
  anyRow: {
    ...continuousRadius(radii.md),
    alignItems: "center",
    borderColor: colors.hairline,
    borderWidth: 1,
    flexDirection: "row",
    justifyContent: "space-between",
    minHeight: layout.minTouchTarget,
    paddingHorizontal: spacing.md,
  },
  anyRowSelected: { backgroundColor: colors.panelRaised, borderColor: colors.gold },
  avatar: { backgroundColor: colors.hairline, borderRadius: 38, height: 76, width: 76 },
  avatarRing: { borderColor: colors.hairline, borderRadius: 43, borderWidth: 2, padding: 3 },
  avatarRingSelected: { borderColor: colors.gold, borderWidth: 3, padding: 2 },
  check: { color: colors.gold, fontFamily: fonts.body, fontSize: 19, fontWeight: "700" },
  chevron: { color: colors.gold, fontFamily: fonts.body, fontSize: 22, width: 24 },
  content: { gap: spacing.md, padding: spacing.md, paddingTop: 0 },
  grid: { flexDirection: "row", flexWrap: "wrap", rowGap: spacing.lg },
  heading: {
    alignItems: "center",
    flexDirection: "row",
    gap: spacing.sm,
    minHeight: 76,
    padding: spacing.md,
  },
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
  person: { alignItems: "center", flexBasis: "33.333%", paddingHorizontal: spacing.xxs },
  personLabel: { color: colors.text, fontFamily: fonts.body, marginTop: spacing.xs, ...typeScale.small },
  photoCount: { color: colors.muted, fontFamily: fonts.body, fontVariant: ["tabular-nums"], ...typeScale.small },
  pressed: { opacity: 0.62 },
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
