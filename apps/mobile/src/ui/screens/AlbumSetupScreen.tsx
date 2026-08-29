import { FlashList } from "@shopify/flash-list";
import { Image } from "expo-image";
// @ts-expect-error TypeScript bundler resolution normally omits source extensions.
import { thumbnailUri } from "../../../modules/photeo-scan-service/src/index.ts";
import { useEffect, useMemo, useState } from "react";
import {
  Pressable,
  StatusBar,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import {
  albumSetupThumbnailRequestSize,
  canBuildFromAlbumSetup,
  normalizedAlbumMaxPhotos,
  updateAlbumPersonPriority,
  type AlbumSetupPerson,
} from "../../albums/album-setup-draft";
import type {
  AlbumBuildPreferences,
  PersonPriority,
} from "../../selection/album-build-preferences";
import { FilterSearchBar } from "../components/FilterSearchBar";
import { PrimaryButton } from "../components/PrimaryButton";
import { ScreenHeader } from "../components/ScreenHeader";
import { fonts } from "../fonts";
import { colors, layout, radii, spacing, typeScale } from "../tokens";

const FACE_SIZE = 80;
const thumbnailCache = new Map<string, string>();

type Props = {
  busy: boolean;
  people: readonly AlbumSetupPerson[];
  photoCount: number;
  preferences: AlbumBuildPreferences;
  message: string | null;
  onBack: () => void;
  onChange: (preferences: AlbumBuildPreferences) => void;
  onContinue: () => void;
};

function PersonFace({ person }: { person: AlbumSetupPerson }) {
  const requestSize = albumSetupThumbnailRequestSize(FACE_SIZE);
  const cacheKey = `${person.coverAssetId}:${requestSize}`;
  const [source, setSource] = useState<string | undefined>(
    () => person.faceThumbUri ?? thumbnailCache.get(cacheKey),
  );

  useEffect(() => {
    if (person.faceThumbUri) {
      setSource(person.faceThumbUri);
      return;
    }
    const cached = thumbnailCache.get(cacheKey);
    if (cached) {
      setSource(cached);
      return;
    }
    // FlashList recycles rows. Never leave the previous person's face painted
    // while this person's platform thumbnail is loading.
    setSource(undefined);
    let live = true;
    void thumbnailUri(person.coverAssetId, requestSize).then((uri) => {
      if (!uri) return;
      thumbnailCache.set(cacheKey, uri);
      if (live) setSource(uri);
    });
    return () => {
      live = false;
    };
  }, [cacheKey, person.coverAssetId, person.faceThumbUri, requestSize]);

  return source ? (
    <Image
      cachePolicy="memory-disk"
      contentFit="cover"
      recyclingKey={person.id}
      source={source}
      style={styles.face}
    />
  ) : (
    <View accessibilityElementsHidden importantForAccessibility="no-hide-descendants" style={styles.facePlaceholder}>
      <Text style={styles.facePlaceholderText}>◯</Text>
    </View>
  );
}

function PriorityButton({
  active,
  accessibilityLabel,
  label,
  onPress,
}: {
  active: boolean;
  accessibilityLabel: string;
  label: string;
  onPress: () => void;
}) {
  return (
    <Pressable
      accessibilityLabel={accessibilityLabel}
      accessibilityRole="radio"
      accessibilityState={{ checked: active }}
      onPress={onPress}
      style={({ pressed }) => [
        styles.priorityButton,
        active ? styles.priorityButtonActive : null,
        pressed ? styles.priorityButtonPressed : null,
      ]}
    >
      <Text style={[styles.priorityLabel, active ? styles.priorityLabelActive : null]}>
        {label}
      </Text>
    </Pressable>
  );
}

export function AlbumSetupScreen({
  busy,
  people,
  photoCount,
  preferences,
  message,
  onBack,
  onChange,
  onContinue,
}: Props) {
  const insets = useSafeAreaInsets();
  const [query, setQuery] = useState("");
  const [maxPhotosText, setMaxPhotosText] = useState(String(preferences.maxPhotos));
  const parsedMaxPhotos = normalizedAlbumMaxPhotos(maxPhotosText);
  const canContinue = parsedMaxPhotos !== undefined && canBuildFromAlbumSetup(preferences);
  const mainFocusCount = Object.values(preferences.personPriority).filter(
    (priority) => priority === "high",
  ).length;

  useEffect(() => {
    setMaxPhotosText(String(preferences.maxPhotos));
  }, [preferences.maxPhotos]);

  const personNumbers = useMemo(
    () => new Map(people.map((person, index) => [person.id, index + 1])),
    [people],
  );
  const needle = query.trim().toLocaleLowerCase();
  const visiblePeople = useMemo(
    () =>
      people.filter((person) => {
        if (!needle) return true;
        const number = personNumbers.get(person.id) ?? 0;
        return (
          `person ${number}`.includes(needle) ||
          `#${number}`.includes(needle) ||
          `${person.candidatePhotoCount} photos`.includes(needle)
        );
      }),
    [needle, people, personNumbers],
  );

  const changeMaxPhotos = (value: string) => {
    setMaxPhotosText(value);
    const parsed = normalizedAlbumMaxPhotos(value);
    if (parsed !== undefined && parsed !== preferences.maxPhotos) {
      onChange({ ...preferences, maxPhotos: parsed });
    }
  };

  const stepMaxPhotos = (change: number) => {
    const current = parsedMaxPhotos ?? preferences.maxPhotos;
    changeMaxPhotos(String(Math.max(1, current + change)));
  };

  const setPriority = (personId: string, priority: PersonPriority) => {
    onChange(updateAlbumPersonPriority(preferences, personId, priority));
  };

  return (
    <View style={styles.root}>
      <StatusBar backgroundColor={colors.background} barStyle="dark-content" />
      <FlashList
        contentContainerStyle={{
          paddingBottom: spacing.xl,
          paddingHorizontal: layout.screenPadding,
          paddingTop: (StatusBar.currentHeight ?? 24) + spacing.xs,
        }}
        contentInsetAdjustmentBehavior="automatic"
        data={visiblePeople}
        extraData={preferences.personPriority}
        keyboardShouldPersistTaps="handled"
        keyExtractor={(person) => person.id}
        ListEmptyComponent={
          people.length > 0 ? (
            <View style={styles.emptySearch}>
              <Text style={styles.emptySearchTitle}>No person matches “{query.trim()}”</Text>
              <Text style={styles.emptySearchText}>Try a person number or photo count.</Text>
            </View>
          ) : null
        }
        ListHeaderComponent={
          <View style={styles.headerContent}>
            <ScreenHeader
              backHint="Returns to your selected photos"
              compact
              eyebrow="Before we build"
              helper="Choose the most photos you want and who this album should centre on."
              onBack={busy ? undefined : onBack}
              title="Set up your album"
            />

            <View style={styles.card}>
              <Text style={styles.sectionTitle}>How many photos?</Text>
              <Text style={styles.sectionHelper}>
                You picked {photoCount.toLocaleString()}. We’ll make an album with up to this many and leave out weak or repeated shots.
              </Text>
              <View style={styles.amountRow}>
                <Pressable
                  accessibilityLabel="Use one fewer photo"
                  accessibilityRole="button"
                  onPress={() => stepMaxPhotos(-1)}
                  style={({ pressed }) => [styles.amountButton, pressed ? styles.amountPressed : null]}
                >
                  <Text style={styles.amountButtonText}>−</Text>
                </Pressable>
                <View style={styles.amountFieldWrap}>
                  <Text style={styles.upTo}>Up to</Text>
                  <TextInput
                    accessibilityLabel="Maximum album photos"
                    inputMode="numeric"
                    keyboardType="number-pad"
                    maxLength={4}
                    onBlur={() => {
                      if (parsedMaxPhotos === undefined) {
                        setMaxPhotosText(String(preferences.maxPhotos));
                      }
                    }}
                    onChangeText={changeMaxPhotos}
                    selectTextOnFocus
                    style={[styles.amountField, parsedMaxPhotos === undefined ? styles.amountFieldError : null]}
                    value={maxPhotosText}
                  />
                  <Text style={styles.photosLabel}>photos</Text>
                </View>
                <Pressable
                  accessibilityLabel="Use one more photo"
                  accessibilityRole="button"
                  onPress={() => stepMaxPhotos(1)}
                  style={({ pressed }) => [styles.amountButton, pressed ? styles.amountPressed : null]}
                >
                  <Text style={styles.amountButtonText}>+</Text>
                </Pressable>
              </View>
              {parsedMaxPhotos === undefined ? (
                <Text accessibilityLiveRegion="polite" style={styles.errorText}>Enter a whole number above zero.</Text>
              ) : null}
            </View>

            {people.length > 0 ? (
              <>
                <View style={styles.peopleIntro}>
                  <Text style={styles.sectionTitle}>Who should this album be about?</Text>
                  <Text style={styles.sectionHelper}>
                    Faces with the most photos in your selection are shown first.
                  </Text>
                  <View style={styles.ruleBox}>
                    <Text style={styles.ruleText}>
                      Photos showing only people marked Background only are left out; those people can still appear with someone marked Main focus or Include.
                    </Text>
                  </View>
                  <FilterSearchBar
                    accessibilityLabel="Search people"
                    onChangeText={setQuery}
                    placeholder="Search by person number or photo count"
                    value={query}
                  />
                </View>
                {mainFocusCount === 0 ? (
                  <Text accessibilityLiveRegion="polite" style={styles.focusRequired}>
                    Choose at least one Main focus to continue.
                  </Text>
                ) : null}
              </>
            ) : (
              <View style={styles.noPeopleCard}>
                <Text style={styles.noPeopleTitle}>No people to choose</Text>
                <Text style={styles.noPeopleText}>
                  We didn’t find a person in these selected photos, so we’ll choose using quality and variety only.
                </Text>
              </View>
            )}
          </View>
        }
        renderItem={({ item: person }) => {
          const priority: PersonPriority = person.id in preferences.personPriority
            ? preferences.personPriority[person.id]
            : "low";
          const personNumber = personNumbers.get(person.id) ?? 0;
          return (
            <View
              accessibilityLabel={`Person ${personNumber}, ${person.candidatePhotoCount} selected photos`}
              style={styles.personCard}
            >
              {/*
                The three choices get the card's FULL width, on their own row.
                Beside the avatar they had about 58dp of content each, and
                "Background" needs 66dp at the smallest size the type scale
                allows -- so it broke mid-word and the button read
                "Backg / round / only". Widening the row is the fix; shrinking
                the words back under the floor is not.
              */}
              <View style={styles.personHeader}>
                <PersonFace person={person} />
                <Text style={styles.personCount}>
                  #{personNumber} · {person.candidatePhotoCount.toLocaleString()} {person.candidatePhotoCount === 1 ? "photo" : "photos"}
                </Text>
              </View>
              <View accessibilityRole="radiogroup" style={styles.priorityRow}>
                <PriorityButton accessibilityLabel={`Main focus for person ${personNumber}`} active={priority === "high"} label="Main focus" onPress={() => setPriority(person.id, "high")} />
                <PriorityButton accessibilityLabel={`Include person ${personNumber}`} active={priority === "medium"} label="Include" onPress={() => setPriority(person.id, "medium")} />
                <PriorityButton accessibilityLabel={`Background only for person ${personNumber}`} active={priority === "low"} label="Background only" onPress={() => setPriority(person.id, "low")} />
              </View>
            </View>
          );
        }}
      />

      <View style={[styles.footer, { paddingBottom: Math.max(insets.bottom, spacing.sm) }]}>
        {message ? (
          <Text accessibilityLiveRegion="assertive" style={styles.footerMessage}>{message}</Text>
        ) : null}
        <Text style={styles.summary}>
          Up to {preferences.maxPhotos.toLocaleString()} photos
          {people.length > 0 ? ` · ${mainFocusCount} Main focus` : ""}
        </Text>
        <PrimaryButton
          accessibilityHint="Starts building the album with these choices"
          busy={busy}
          disabled={!canContinue}
          label={busy ? "Starting album…" : "Build my album"}
          onPress={onContinue}
        />
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  amountButton: {
    alignItems: "center",
    backgroundColor: colors.quietSurface,
    borderCurve: "continuous",
    borderRadius: radii.md,
    height: 52,
    justifyContent: "center",
    width: 52,
  },
  amountButtonText: { color: colors.text, fontFamily: fonts.semibold, fontSize: 26, lineHeight: 30 },
  amountField: {
    color: colors.text,
    fontFamily: fonts.bold,
    fontSize: 24,
    minWidth: 48,
    paddingHorizontal: spacing.xxs,
    paddingVertical: 0,
    textAlign: "center",
  },
  amountFieldError: { color: colors.error },
  amountFieldWrap: { alignItems: "baseline", flexDirection: "row", gap: spacing.xxs },
  amountPressed: { backgroundColor: colors.hairline, transform: [{ scale: 0.97 }] },
  amountRow: { alignItems: "center", flexDirection: "row", gap: spacing.md, justifyContent: "center" },
  card: {
    backgroundColor: colors.panel,
    borderColor: colors.hairline,
    borderCurve: "continuous",
    borderRadius: radii.lg,
    borderWidth: 1,
    gap: spacing.sm,
    padding: spacing.md,
  },
  emptySearch: { alignItems: "center", gap: spacing.xs, paddingVertical: spacing.xl },
  emptySearchText: { color: colors.muted, fontFamily: fonts.regular, ...typeScale.small },
  emptySearchTitle: { color: colors.text, fontFamily: fonts.semibold, textAlign: "center", ...typeScale.label },
  errorText: { color: colors.error, fontFamily: fonts.medium, textAlign: "center", ...typeScale.small },
  face: { backgroundColor: colors.hairline, borderRadius: FACE_SIZE / 2, height: FACE_SIZE, width: FACE_SIZE },
  facePlaceholder: {
    alignItems: "center",
    backgroundColor: colors.quietSurface,
    borderRadius: FACE_SIZE / 2,
    height: FACE_SIZE,
    justifyContent: "center",
    width: FACE_SIZE,
  },
  facePlaceholderText: { color: colors.muted, fontFamily: fonts.regular, fontSize: 38, lineHeight: 42 },
  focusRequired: { color: colors.error, fontFamily: fonts.medium, ...typeScale.small },
  footer: {
    backgroundColor: colors.background,
    borderColor: colors.hairline,
    borderTopWidth: 1,
    gap: spacing.xs,
    paddingHorizontal: layout.screenPadding,
    paddingTop: spacing.sm,
  },
  footerMessage: { color: colors.error, fontFamily: fonts.medium, ...typeScale.small },
  headerContent: { gap: spacing.lg, paddingBottom: spacing.md },
  noPeopleCard: {
    backgroundColor: colors.privacySurface,
    borderCurve: "continuous",
    borderRadius: radii.md,
    gap: spacing.xs,
    padding: spacing.md,
  },
  noPeopleText: { color: colors.muted, fontFamily: fonts.regular, ...typeScale.small },
  noPeopleTitle: { color: colors.text, fontFamily: fonts.semibold, ...typeScale.label },
  peopleIntro: { gap: spacing.sm },
  personCard: {
    backgroundColor: colors.panel,
    borderColor: colors.hairline,
    borderCurve: "continuous",
    borderRadius: radii.md,
    borderWidth: 1,
    gap: spacing.sm,
    marginBottom: spacing.sm,
    minHeight: 112,
    padding: spacing.sm,
  },
  personHeader: { alignItems: "center", flexDirection: "row", gap: spacing.sm },
  personCount: { color: colors.muted, fontFamily: fonts.semibold, fontVariant: ["tabular-nums"], ...typeScale.small },
  photosLabel: { color: colors.muted, fontFamily: fonts.regular, ...typeScale.small },
  priorityButton: {
    alignItems: "center",
    borderColor: colors.hairline,
    borderCurve: "continuous",
    borderRadius: radii.sm,
    borderWidth: 1,
    flex: 1,
    justifyContent: "center",
    minHeight: 46,
    paddingHorizontal: spacing.xxs,
    paddingVertical: spacing.xxs,
  },
  priorityButtonActive: { backgroundColor: colors.gold, borderColor: colors.gold },
  priorityButtonPressed: { opacity: 0.72 },
  priorityLabel: { color: colors.text, fontFamily: fonts.semibold, fontSize: 12, lineHeight: 16, textAlign: "center" },
  priorityLabelActive: { color: colors.onAccent },
  priorityRow: { flexDirection: "row", gap: spacing.xxs },
  root: { backgroundColor: colors.background, flex: 1 },
  ruleBox: {
    backgroundColor: colors.panelRaised,
    borderCurve: "continuous",
    borderRadius: radii.md,
    padding: spacing.sm,
  },
  ruleText: { color: colors.text, fontFamily: fonts.medium, ...typeScale.small },
  sectionHelper: { color: colors.muted, fontFamily: fonts.regular, ...typeScale.small },
  sectionTitle: { color: colors.text, fontFamily: fonts.bold, ...typeScale.subtitle },
  summary: { color: colors.muted, fontFamily: fonts.medium, textAlign: "center", ...typeScale.small },
  upTo: { color: colors.muted, fontFamily: fonts.medium, ...typeScale.small },
});
