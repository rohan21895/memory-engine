import { useEffect, useRef, useState } from "react";
import { BackHandler, ScrollView, StatusBar, StyleSheet, Text, View } from "react-native";

import {
  DateFilterPanel,
  type DateFilterOption,
} from "../components/DateFilterPanel";
import {
  FaceFilterPanel,
  type FaceFilterOption,
} from "../components/FaceFilterPanel";
import {
  LocationFilterPanel,
  type LocationFilterOption,
} from "../components/LocationFilterPanel";
import { PrimaryButton } from "../components/PrimaryButton";
import { ScreenHeader } from "../components/ScreenHeader";
import { SecondaryButton } from "../components/SecondaryButton";
import { copy } from "../copy";
import { colors, spacing } from "../tokens";

export type FilterSelection = {
  dateId: string;
  locationId: string | null;
  personId: string | null;
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

type SectionKey = "face" | "location" | "date";

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
  const [openSection, setOpenSection] = useState<SectionKey>("face");
  const [selection, setSelection] = useState<FilterSelection>(initialSelection);
  const [photoCount, setPhotoCount] = useState<number | null>(null);
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

  const clearAll = () => {
    setSelection({ dateId: "all", locationId: null, personId: null });
  };
  const countLabel = photoCount === null ? "Show photos" : `Show ${copy.filters.photoCount(photoCount)}`;

  return (
    <View style={styles.root}>
      <StatusBar backgroundColor={colors.background} barStyle="dark-content" />
      <ScrollView
        contentContainerStyle={[
          styles.scrollContent,
          { paddingTop: (StatusBar.currentHeight ?? 0) + spacing.sm },
        ]}
        contentInsetAdjustmentBehavior="automatic"
        keyboardShouldPersistTaps="handled"
      >
        <ScreenHeader
          backHint="Returns to the photo picker without changing filters"
          compact
          helper="Choose a face, place, or date. Your photo count updates as you go."
          onBack={onBack}
          title="Filter your photos"
        />
        <View style={styles.sections}>
          <FaceFilterPanel
            expanded={openSection === "face"}
            loadingText={peopleLoadingText}
            onSelect={(personId) => setSelection((current) => ({ ...current, personId }))}
            onToggle={() => setOpenSection("face")}
            people={people}
            peopleAvailable={peopleAvailable}
            selectedPersonId={selection.personId}
          />
          <LocationFilterPanel
            cities={cities}
            countries={countries}
            expanded={openSection === "location"}
            loadingText={locationLoadingText}
            onSelect={(locationId) => setSelection((current) => ({ ...current, locationId }))}
            onToggle={() => setOpenSection("location")}
            selectedLocationId={selection.locationId}
          />
          <DateFilterPanel
            expanded={openSection === "date"}
            loadingText={dateLoadingText}
            months={months}
            onSelect={(dateId) => setSelection((current) => ({ ...current, dateId }))}
            onToggle={() => setOpenSection("date")}
            presets={datePresets}
            selectedDateId={selection.dateId}
          />
        </View>
      </ScrollView>

      <View style={[styles.footer, { paddingBottom: spacing.xl }]}>
        <View style={styles.secondaryAction}>
          <SecondaryButton
            accessibilityHint="Removes the face, location, and date filters"
            label="Clear all"
            onPress={clearAll}
          />
        </View>
        <View style={styles.primaryAction}>
          <PrimaryButton
            accessibilityHint="Applies these filters and returns to the photo picker"
            busy={photoCount === null}
            label={countLabel}
            onPress={() => onApply(selection)}
          />
        </View>
        <Text accessibilityLiveRegion="polite" style={styles.liveCount}>
          {photoCount === null
            ? "Counting matching photos…"
            : `${copy.filters.photoCount(photoCount)} match these filters`}
        </Text>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  footer: {
    backgroundColor: colors.panel,
    borderTopColor: colors.hairline,
    borderTopWidth: 1,
    flexDirection: "row",
    gap: spacing.sm,
    paddingHorizontal: spacing.md,
    paddingTop: spacing.sm,
  },
  liveCount: { height: 0, opacity: 0, position: "absolute" },
  primaryAction: { flex: 1.35 },
  root: { backgroundColor: colors.background, flex: 1 },
  scrollContent: { gap: spacing.lg, paddingBottom: spacing.xl, paddingHorizontal: spacing.md },
  secondaryAction: { flex: 0.8, justifyContent: "center" },
  sections: { gap: spacing.md },
});
