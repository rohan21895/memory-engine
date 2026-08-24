import { Image } from "expo-image";
import * as MediaLibrary from "expo-media-library/legacy";
import { useEffect, useMemo, useState } from "react";
import { Pressable, ScrollView, StatusBar, StyleSheet, Text, TextInput, View } from "react-native";

import {
  assetIdsForPerson,
  buildFaceIndex,
  contentUri,
  getPeople,
  isFaceDetectionAvailable,
  loadFaceIndex,
  type FaceIndexPerson,
} from "../../faces/face-index";
import {
  assetIdsForCity,
  assetIdsForCountry,
  buildIndex,
  getCities,
  getCountries,
  getMonths,
  loadIndex,
  type PlaceSummary,
} from "../../import/photo-index";
import { fonts } from "../fonts";
import { colors, layout, radii, spacing, typeScale } from "../tokens";
import type { NamePersonTarget } from "./NamePersonScreen";

type PlaceCard = PlaceSummary & { coverUri: string };

export function PhotosScreen({ onNamePerson }: { onNamePerson?: (person: NamePersonTarget) => void }) {
  const [query, setQuery] = useState("");
  const [people, setPeople] = useState<FaceIndexPerson[]>([]);
  const [places, setPlaces] = useState<PlaceCard[]>([]);
  const [libraryIds, setLibraryIds] = useState<string[]>([]);
  const [selectedPerson, setSelectedPerson] = useState<string | null>(null);
  const [selectedPlace, setSelectedPlace] = useState<string | null>(null);
  const [monthLabel, setMonthLabel] = useState("Recent photos");
  const [loading, setLoading] = useState(true);
  const [scanningPeople, setScanningPeople] = useState(false);

  useEffect(() => {
    let active = true;
    const refreshIndexes = () => {
      if (!active) return;
      const nextPeople = getPeople();
      const nextPlaces: PlaceCard[] = [...getCities(), ...getCountries()].slice(0, 12).map((place) => {
        const ids = place.id.startsWith("country:") ? assetIdsForCountry(place.id) : assetIdsForCity(place.id);
        return { ...place, coverUri: ids[0] ? contentUri(ids[0]) : "" };
      });
      setPeople(nextPeople);
      setPlaces(nextPlaces);
      setMonthLabel(getMonths()[0]?.label ?? "Recent photos");
    };
    void (async () => {
      try {
        const permission = await MediaLibrary.getPermissionsAsync();
        if (permission.status !== "granted") return;
        const page = await MediaLibrary.getAssetsAsync({ first: 60, mediaType: [MediaLibrary.MediaType.photo], sortBy: [MediaLibrary.SortBy.creationTime] });
        if (active) setLibraryIds(page.assets.map((asset) => asset.id));
        await Promise.all([loadIndex().catch(() => undefined), loadFaceIndex().catch(() => undefined)]);
        refreshIndexes();
        void buildIndex({ onProgress: refreshIndexes }).then(refreshIndexes).catch(() => undefined);
      } catch {
        // A neutral empty library is safer than surfacing a native-module error.
      } finally {
        if (active) setLoading(false);
      }
    })();
    return () => { active = false; };
  }, []);

  const scanForPeople = () => {
    if (scanningPeople || !isFaceDetectionAvailable()) return;
    setScanningPeople(true);
    const refreshPeople = () => setPeople(getPeople());
    void buildFaceIndex({ onProgress: refreshPeople })
      .then(refreshPeople)
      .catch(() => undefined)
      .finally(() => setScanningPeople(false));
  };

  const needle = query.trim().toLocaleLowerCase();
  const visiblePeople = people.filter((person, index) => `person ${index + 1}`.includes(needle));
  const visiblePlaces = places.filter((place) => place.name.toLocaleLowerCase().includes(needle));
  const displayedIds = useMemo(() => {
    if (selectedPerson) return assetIdsForPerson(selectedPerson).slice(0, 60);
    if (selectedPlace) return (selectedPlace.startsWith("country:") ? assetIdsForCountry(selectedPlace) : assetIdsForCity(selectedPlace)).slice(0, 60);
    return libraryIds;
  }, [libraryIds, selectedPerson, selectedPlace]);

  return (
    <ScrollView contentContainerStyle={styles.scroll} contentInsetAdjustmentBehavior="automatic" keyboardShouldPersistTaps="handled">
      <Text accessibilityRole="header" style={styles.title}>Photos</Text>
      <View style={styles.search}>
        <Text style={styles.searchIcon}>⌕</Text>
        <TextInput onChangeText={setQuery} placeholder="Search people or places" placeholderTextColor="#8b8378" style={styles.searchInput} value={query} />
      </View>

      <View style={styles.sectionHeading}><Text style={styles.section}>People</Text><Text style={styles.seeAll}>See all</Text></View>
      {people.length > 0 ? (
        <ScrollView horizontal contentContainerStyle={styles.peopleRow} showsHorizontalScrollIndicator={false}>
          {visiblePeople.map((person, index) => {
            const active = selectedPerson === person.id;
            return (
              <Pressable
                accessibilityHint="Tap to filter photos. Hold to add a name."
                key={person.id}
                onLongPress={() => onNamePerson?.({
                  id: person.id,
                  label: `Person ${index + 1}`,
                  faceThumbUri: person.faceThumbUri,
                  assetIds: assetIdsForPerson(person.id).slice(0, 8),
                })}
                onPress={() => { setSelectedPlace(null); setSelectedPerson(active ? null : person.id); }}
                style={styles.person}
              >
                <Image cachePolicy="memory-disk" contentFit="cover" source={person.faceThumbUri ?? contentUri(person.coverAssetId)} style={[styles.avatar, active ? styles.avatarActive : null]} />
                <Text numberOfLines={1} style={[styles.personName, active ? styles.activeText : null]}>Person {index + 1}</Text>
              </Pressable>
            );
          })}
        </ScrollView>
      ) : (
        <View style={styles.noPeople}>
          <Text style={styles.noPeopleTitle}>{loading ? "Checking your people…" : scanningPeople ? "Finding people…" : "No people found yet"}</Text>
          <Text style={styles.noPeopleText}>Face grouping happens on this phone and may take a few minutes the first time.</Text>
          {!loading ? <Pressable accessibilityRole="button" disabled={scanningPeople || !isFaceDetectionAvailable()} onPress={scanForPeople} style={[styles.peopleScan, scanningPeople ? styles.scanDisabled : null]}><Text style={styles.peopleScanText}>{scanningPeople ? "Scanning on this phone…" : "Find people on this phone"}</Text></Pressable> : null}
        </View>
      )}

      <View style={styles.sectionHeading}><Text style={styles.section}>Places</Text><Text style={styles.seeAll}>See all</Text></View>
      <ScrollView horizontal contentContainerStyle={styles.placesRow} showsHorizontalScrollIndicator={false}>
        {visiblePlaces.map((place) => {
          const active = selectedPlace === place.id;
          return (
            <Pressable key={place.id} onPress={() => { setSelectedPerson(null); setSelectedPlace(active ? null : place.id); }} style={styles.place}>
              {place.coverUri ? <Image cachePolicy="memory-disk" contentFit="cover" source={place.coverUri} style={[styles.placeImage, active ? styles.placeActive : null]} /> : <View style={styles.placeImage} />}
              <Text numberOfLines={1} style={[styles.placeName, active ? styles.activeText : null]}>{place.name}</Text>
              <Text style={styles.placeCount}>{place.count} photos</Text>
            </Pressable>
          );
        })}
      </ScrollView>

      <Text style={styles.month}>{selectedPerson || selectedPlace ? "Filtered photos" : monthLabel}</Text>
      <View style={styles.grid}>
        {displayedIds.map((id) => <Image cachePolicy="memory-disk" contentFit="cover" key={id} recyclingKey={id} source={contentUri(id)} style={styles.tile} />)}
      </View>
      {!loading && displayedIds.length === 0 ? <Text style={styles.empty}>No photos to show here yet.</Text> : null}
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  activeText: { color: colors.gold },
  avatar: { backgroundColor: colors.hairline, borderRadius: 33, height: 66, width: 66 },
  avatarActive: { borderColor: colors.gold, borderWidth: 3 },
  empty: { color: colors.muted, fontFamily: fonts.regular, padding: spacing.lg, textAlign: "center", ...typeScale.small },
  grid: { flexDirection: "row", flexWrap: "wrap", gap: 3, marginHorizontal: -layout.screenPadding },
  month: { color: colors.text, fontFamily: fonts.bold, paddingTop: spacing.sm, ...typeScale.label },
  noPeople: { backgroundColor: colors.panel, borderColor: colors.hairline, borderCurve: "continuous", borderRadius: radii.lg, borderWidth: 1, gap: spacing.xs, padding: spacing.md },
  noPeopleText: { color: colors.muted, fontFamily: fonts.regular, ...typeScale.small },
  noPeopleTitle: { color: colors.text, fontFamily: fonts.bold, ...typeScale.label },
  peopleRow: { gap: 14, paddingVertical: spacing.xs },
  peopleScan: { alignItems: "center", alignSelf: "flex-start", backgroundColor: colors.panelRaised, borderRadius: 20, height: 40, justifyContent: "center", marginTop: spacing.xs, paddingHorizontal: spacing.md },
  peopleScanText: { color: colors.gold, fontFamily: fonts.bold, ...typeScale.small },
  person: { alignItems: "center", gap: 6, width: 66 },
  personName: { color: colors.text, fontFamily: fonts.semibold, fontSize: 12.5, width: 66 },
  place: { width: 132 },
  placeActive: { borderColor: colors.gold, borderWidth: 3 },
  placeCount: { color: colors.muted, fontFamily: fonts.regular, fontSize: 12.5 },
  placeImage: { backgroundColor: colors.quietSurface, borderCurve: "continuous", borderRadius: 14, height: 92, width: 132 },
  placeName: { color: colors.text, fontFamily: fonts.bold, fontSize: 14.5, paddingTop: 7 },
  placesRow: { gap: spacing.sm, paddingVertical: spacing.xs },
  scroll: { paddingBottom: spacing.xxl, paddingHorizontal: layout.screenPadding, paddingTop: (StatusBar.currentHeight ?? 24) + spacing.md },
  search: { alignItems: "center", backgroundColor: "#f0eee8", borderRadius: radii.pill, flexDirection: "row", gap: spacing.xs, height: 48, marginTop: 14, paddingHorizontal: spacing.md },
  searchIcon: { color: "#8b8378", fontFamily: fonts.regular, fontSize: 21 },
  searchInput: { color: colors.text, flex: 1, fontFamily: fonts.regular, fontSize: 16, paddingVertical: 0 },
  scanDisabled: { opacity: 0.55 },
  section: { color: colors.text, fontFamily: fonts.bold, ...typeScale.label },
  sectionHeading: { alignItems: "baseline", flexDirection: "row", justifyContent: "space-between", paddingTop: spacing.lg },
  seeAll: { color: colors.gold, fontFamily: fonts.semibold, ...typeScale.small },
  tile: { aspectRatio: 1, backgroundColor: colors.hairline, width: "32.8%" },
  title: { color: colors.text, fontFamily: fonts.extraBold, ...typeScale.title },
});
