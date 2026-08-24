import { useEffect, useMemo, useRef, useState } from "react";
import { BackHandler, Pressable, ScrollView, StatusBar, StyleSheet, Text, View } from "react-native";

import type { FaceMatchMode } from "../../faces/face-filter";
import type { DateFilterOption } from "../components/DateFilterPanel";
import { FaceFilterModal } from "../components/FaceFilterModal";
import type { FaceFilterOption } from "../components/FaceFilterPanel";
import { LocationFilterModal } from "../components/LocationFilterModal";
import type { LocationFilterOption } from "../components/LocationFilterPanel";
import { PrimaryButton } from "../components/PrimaryButton";
import { SecondaryButton } from "../components/SecondaryButton";
import { copy } from "../copy";
import { fonts } from "../fonts";
import { colors, layout, radii, spacing, typeScale } from "../tokens";

export type FilterSelection = {
  dateId: string;
  faceMatchMode: FaceMatchMode;
  locationId: string | null;
  personIds: string[];
};

export type FilterScreenProps = {
  cities: LocationFilterOption[];
  countries: LocationFilterOption[];
  countPhotos: (selection: FilterSelection) => Promise<number>;
  dateLoadingText?: string;
  datePresets: DateFilterOption[];
  initialSelection: FilterSelection;
  locationLoadingText?: string;
  months: DateFilterOption[];
  onApply: (selection: FilterSelection) => void;
  onBack: () => void;
  people: FaceFilterOption[];
  peopleAvailable: boolean;
  peopleLoadingText?: string;
};

type DateMode = "Exact" | "Month" | "Year";

function ChoiceRow({
  detail,
  icon,
  kind,
  onPress,
  value,
}: {
  detail: string;
  icon: string;
  kind: string;
  onPress: () => void;
  value: string;
}) {
  return (
    <Pressable accessibilityRole="button" onPress={onPress} style={({ pressed }) => [styles.choiceRow, pressed ? styles.pressed : null]}>
      <View style={styles.choiceIcon}><Text style={styles.choiceIconText}>{icon}</Text></View>
      <View style={styles.choiceCopy}>
        <Text style={styles.kind}>{kind}</Text>
        <Text style={styles.choiceValue}>{value}</Text>
        <Text style={styles.choiceDetail}>{detail}</Text>
      </View>
      <Text style={styles.chevron}>›</Text>
    </Pressable>
  );
}

export function FilterScreen({
  cities,
  countries,
  countPhotos,
  dateLoadingText,
  datePresets,
  initialSelection,
  locationLoadingText,
  months,
  onApply,
  onBack,
  people,
  peopleAvailable,
  peopleLoadingText,
}: FilterScreenProps) {
  const [selection, setSelection] = useState<FilterSelection>(initialSelection);
  const [photoCount, setPhotoCount] = useState<number | null>(null);
  const [faceVisible, setFaceVisible] = useState(false);
  const [locationVisible, setLocationVisible] = useState(false);
  const [dateMode, setDateMode] = useState<DateMode>(
    initialSelection.dateId.startsWith("month:") ? "Month" : initialSelection.dateId.startsWith("year:") || initialSelection.dateId === "year" ? "Year" : "Exact",
  );
  const countRequest = useRef(0);

  useEffect(() => {
    const subscription = BackHandler.addEventListener("hardwareBackPress", () => {
      onBack();
      return true;
    });
    return () => subscription.remove();
  }, [onBack]);

  useEffect(() => {
    const request = ++countRequest.current;
    setPhotoCount(null);
    void countPhotos(selection)
      .then((count) => {
        if (request === countRequest.current) setPhotoCount(count);
      })
      .catch(() => {
        if (request === countRequest.current) setPhotoCount(0);
      });
  }, [countPhotos, selection]);

  const selectedPeople = people.filter((person) => selection.personIds.includes(person.id));
  const places = [...countries, ...cities];
  const selectedPlace = places.find((place) => place.id === selection.locationId);
  const allDates = [...datePresets, ...months];
  const selectedDate = allDates.find((option) => option.id === selection.dateId);
  const yearOptions = useMemo(() => {
    const years = new Set<string>();
    for (const month of months) {
      const match = /^month:(\d{4})-/.exec(month.id);
      if (match) years.add(match[1]);
    }
    return [...years].sort().reverse().map((year) => ({ id: `year:${year}`, label: year }));
  }, [months]);

  const dateOptions = dateMode === "Month"
    ? [{ id: "all", label: "All time" }, ...months]
    : dateMode === "Year"
      ? [{ id: "all", label: "All time" }, ...yearOptions]
      : datePresets.filter((option) => ["all", "week", "month"].includes(option.id));

  const clearAll = () => setSelection({ dateId: "all", faceMatchMode: "any", locationId: null, personIds: [] });
  const countLabel = photoCount === null ? "Show photos" : `Show ${copy.filters.photoCount(photoCount)}`;

  return (
    <View style={styles.root}>
      <StatusBar backgroundColor={colors.background} barStyle="dark-content" />
      <ScrollView contentContainerStyle={styles.scroll} contentInsetAdjustmentBehavior="automatic">
        <Pressable accessibilityRole="button" onPress={onBack} style={styles.back}>
          <Text style={styles.backText}>‹ Back</Text>
        </Pressable>
        <Text accessibilityRole="header" style={styles.title}>Filter your photos</Text>
        <Text style={styles.helper}>Narrow things down, or leave it as it is.</Text>

        <View style={styles.rows}>
          <ChoiceRow
            detail={selectedPeople.length === 0
              ? "Everyone in your photos"
              : selectedPeople.length === 1
                ? copy.filters.photoCount(selectedPeople[0].photoCount)
                : selection.faceMatchMode === "all"
                  ? "Together in the same photo"
                  : "In any of their photos"}
            icon="●"
            kind="Face"
            onPress={() => setFaceVisible(true)}
            value={selectedPeople.length === 0 ? "Anyone" : selectedPeople.length === 1 ? selectedPeople[0].label : `${selectedPeople.length} people`}
          />
          <ChoiceRow
            detail={selectedPlace ? copy.filters.photoCount(selectedPlace.photoCount) : "Everywhere you’ve been"}
            icon="◆"
            kind="Location"
            onPress={() => setLocationVisible(true)}
            value={selectedPlace?.label ?? "Any place"}
          />
          <View style={styles.dateCard}>
            <Text style={styles.kind}>Date</Text>
            <Text style={styles.dateValue}>{selectedDate?.label ?? (selection.dateId.startsWith("year:") ? selection.dateId.slice(5) : "All time")}</Text>
            <View style={styles.segmented}>
              {(["Exact", "Month", "Year"] as DateMode[]).map((mode) => {
                const active = mode === dateMode;
                return (
                  <Pressable key={mode} onPress={() => setDateMode(mode)} style={[styles.segment, active ? styles.segmentActive : null]}>
                    <Text style={[styles.segmentText, active ? styles.segmentTextActive : null]}>{mode}</Text>
                  </Pressable>
                );
              })}
            </View>
            {dateMode === "Exact" ? (
              <View style={styles.exactRow}>
                <View style={styles.exactField}><Text style={styles.exactLabel}>From</Text><Text style={styles.exactValue}>{selection.dateId === "week" ? "7 days ago" : "Any date"}</Text></View>
                <View style={styles.exactField}><Text style={styles.exactLabel}>To</Text><Text style={styles.exactValue}>{selection.dateId === "all" ? "Any date" : "Today"}</Text></View>
              </View>
            ) : null}
            <View style={styles.chips}>
              {dateOptions.map((option) => {
                const active = option.id === selection.dateId;
                return (
                  <Pressable key={option.id} onPress={() => setSelection((current) => ({ ...current, dateId: option.id }))} style={[styles.chip, active ? styles.chipActive : null]}>
                    <Text style={[styles.chipText, active ? styles.chipTextActive : null]}>{option.label}</Text>
                  </Pressable>
                );
              })}
            </View>
            {dateLoadingText ? <Text style={styles.loading}>{dateLoadingText}</Text> : null}
          </View>
          {photoCount === 0 ? (
            <View style={styles.noMatch}><Text style={styles.noMatchTitle}>No photos match</Text><Text style={styles.noMatchText}>Try a different person or place, or clear the filters.</Text></View>
          ) : null}
        </View>
      </ScrollView>

      <View style={styles.footer}>
        <View style={styles.clear}><SecondaryButton accessibilityHint="Removes every filter" label="Clear all" onPress={clearAll} /></View>
        <View style={styles.apply}><PrimaryButton accessibilityHint="Applies these filters" busy={photoCount === null} label={countLabel} onPress={() => onApply(selection)} /></View>
      </View>

      <FaceFilterModal
        loadingText={peopleLoadingText}
        matchMode={selection.faceMatchMode}
        onClose={() => setFaceVisible(false)}
        onMatchModeChange={(faceMatchMode) => setSelection((current) => ({ ...current, faceMatchMode }))}
        onSelect={(personId) => setSelection((current) => {
          if (personId === null) return { ...current, faceMatchMode: "any", personIds: [] };
          const personIds = current.personIds.includes(personId)
            ? current.personIds.filter((id) => id !== personId)
            : current.personIds.concat(personId);
          return { ...current, personIds };
        })}
        people={people}
        peopleAvailable={peopleAvailable}
        selectedPersonIds={selection.personIds}
        visible={faceVisible}
      />
      <LocationFilterModal
        cities={cities}
        countries={countries}
        loadingText={locationLoadingText}
        onClose={() => setLocationVisible(false)}
        onSelect={(locationId) => setSelection((current) => ({ ...current, locationId }))}
        selectedLocationId={selection.locationId}
        visible={locationVisible}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  apply: { flex: 1 },
  back: { alignSelf: "flex-start", minHeight: 44, justifyContent: "center" },
  backText: { color: colors.muted, fontFamily: fonts.semibold, ...typeScale.small },
  chevron: { color: "#c4bcb0", fontFamily: fonts.regular, fontSize: 24 },
  choiceCopy: { flex: 1 },
  choiceDetail: { color: colors.muted, fontFamily: fonts.regular, ...typeScale.small },
  choiceIcon: { alignItems: "center", backgroundColor: colors.panelRaised, borderRadius: 14, height: 46, justifyContent: "center", width: 46 },
  choiceIconText: { color: colors.gold, fontFamily: fonts.bold, fontSize: 18 },
  choiceRow: { alignItems: "center", backgroundColor: colors.panel, borderColor: colors.hairline, borderCurve: "continuous", borderRadius: radii.lg, borderWidth: 1, flexDirection: "row", gap: 14, padding: 18 },
  choiceValue: { color: colors.text, fontFamily: fonts.bold, fontSize: 18, letterSpacing: -0.3, lineHeight: 24 },
  chip: { backgroundColor: colors.panel, borderColor: colors.hairline, borderRadius: radii.pill, borderWidth: 1, minHeight: 40, justifyContent: "center", paddingHorizontal: spacing.md },
  chipActive: { backgroundColor: colors.gold, borderColor: colors.gold },
  chipText: { color: colors.text, fontFamily: fonts.semibold, ...typeScale.small },
  chipTextActive: { color: colors.onAccent },
  chips: { flexDirection: "row", flexWrap: "wrap", gap: spacing.xs },
  clear: { width: 112 },
  dateCard: { backgroundColor: colors.panel, borderColor: colors.hairline, borderCurve: "continuous", borderRadius: radii.lg, borderWidth: 1, gap: spacing.sm, padding: 18 },
  dateValue: { color: colors.text, fontFamily: fonts.bold, fontSize: 18, letterSpacing: -0.3 },
  exactField: { flex: 1, gap: spacing.xxs },
  exactLabel: { color: colors.muted, fontFamily: fonts.regular, ...typeScale.eyebrow },
  exactRow: { flexDirection: "row", gap: spacing.sm },
  exactValue: { borderColor: colors.hairline, borderRadius: 13, borderWidth: 1, color: colors.text, fontFamily: fonts.semibold, minHeight: 50, padding: 14, ...typeScale.small },
  footer: { backgroundColor: colors.background, borderTopColor: colors.hairline, borderTopWidth: 1, flexDirection: "row", gap: spacing.sm, paddingBottom: spacing.lg, paddingHorizontal: spacing.md, paddingTop: spacing.sm },
  helper: { color: colors.muted, fontFamily: fonts.regular, fontSize: 14.5, lineHeight: 21 },
  kind: { color: "#a29a8e", fontFamily: fonts.bold, textTransform: "uppercase", ...typeScale.eyebrow },
  loading: { color: colors.muted, fontFamily: fonts.regular, ...typeScale.eyebrow },
  noMatch: { alignItems: "center", backgroundColor: "#f4f1ea", borderCurve: "continuous", borderRadius: radii.lg, gap: spacing.xs, padding: spacing.lg },
  noMatchText: { color: colors.muted, fontFamily: fonts.regular, textAlign: "center", ...typeScale.small },
  noMatchTitle: { color: colors.text, fontFamily: fonts.bold, ...typeScale.label },
  pressed: { opacity: 0.68 },
  root: { backgroundColor: colors.background, flex: 1 },
  rows: { gap: spacing.sm, paddingTop: spacing.md },
  scroll: { paddingBottom: spacing.xl, paddingHorizontal: layout.screenPadding, paddingTop: (StatusBar.currentHeight ?? 24) + spacing.xs },
  segment: { alignItems: "center", borderRadius: 10, flex: 1, height: 40, justifyContent: "center" },
  segmentActive: { backgroundColor: colors.panel, borderColor: colors.hairline, borderWidth: 1 },
  segmentText: { color: colors.muted, fontFamily: fonts.bold, ...typeScale.small },
  segmentTextActive: { color: colors.gold },
  segmented: { backgroundColor: "#f0eee8", borderRadius: 13, flexDirection: "row", gap: 4, padding: 4 },
  title: { color: colors.text, fontFamily: fonts.extraBold, fontSize: 28, letterSpacing: -0.8, lineHeight: 33 },
});
