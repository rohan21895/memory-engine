import { Image } from "expo-image";
import { useCallback, useMemo, useState } from "react";
import { Pressable, ScrollView, StatusBar, StyleSheet, Text, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import {
  colors,
  copy,
  fonts,
  plainAlternativeReason,
  plainChosenReason,
  PrimaryButton,
  spacing,
  typeScale,
} from "../ui";
import Lightbox, { type LightboxItem, type LightboxMode } from "./Lightbox";
import { SwapSheet } from "./SwapSheet";
import {
  type ReviewAlternative,
  type ReviewData,
  type ReviewMedia,
  type ReviewSelected,
} from "./mock-data";
import { captureAlbumEditPreference } from "../selection/preference-label-store";

type Swaps = Record<string, string>;
type ViewerState = { mode: LightboxMode; initialIndex: number; slotMediaId?: string };

type DisplayItem = {
  alternativeCount: number;
  caption: string;
  media_id: string;
  page: number;
  rawReasons: string[];
  slot_media_id: string;
  uri: string;
};

function alternativeCaption(alternative: ReviewAlternative) {
  const reason = plainAlternativeReason(alternative.not_chosen_because);
  return alternative.fits_slot ? reason : `${copy.review.notSafe} ${reason}`;
}

function alternativeItems(selected: ReviewSelected): LightboxItem[] {
  return [
    {
      caption: plainChosenReason(selected.chosen_because),
      media_id: selected.media_id,
      rawReasons: selected.chosen_because,
      slot_media_id: selected.media_id,
      uri: selected.uri,
    },
    ...selected.alternatives.map((alternative) => ({
      caption: alternativeCaption(alternative),
      media_id: alternative.media_id,
      rawReasons: alternative.not_chosen_because,
      slot_media_id: selected.media_id,
      uri: alternative.uri,
    })),
  ];
}

export default function ReviewScreen({
  data,
  onBack,
  onFinalize,
}: {
  data: ReviewData;
  onBack: () => void;
  onFinalize: (photos: { media_id: string; uri: string; page: number }[]) => void;
}) {
  const insets = useSafeAreaInsets();
  const [swaps, setSwaps] = useState<Swaps>({});
  const [removed, setRemoved] = useState<Set<string>>(new Set());
  const [added, setAdded] = useState<Set<string>>(new Set());
  const [viewer, setViewer] = useState<ViewerState | null>(null);
  const [swapSlot, setSwapSlot] = useState<string | null>(null);

  const mediaById = useMemo(() => {
    const entries: ReviewMedia[] = [
      ...data.selected,
      ...data.pool,
      ...data.selected.flatMap((item) => item.alternatives),
    ];
    return new Map(entries.map((item) => [item.media_id, item]));
  }, [data]);

  const gridItems = useMemo<DisplayItem[]>(() => {
    const chosen = data.selected
      .filter((selected) => !removed.has(selected.media_id))
      .map((selected) => {
        const replacementId = swaps[selected.media_id];
        const replacement = replacementId
          ? selected.alternatives.find((item) => item.media_id === replacementId)
          : undefined;
        const shown = (replacementId ? mediaById.get(replacementId) : undefined) ?? selected;
        return {
          alternativeCount: selected.alternatives.length,
          caption: replacementId ? copy.review.changedReason : plainChosenReason(selected.chosen_because),
          media_id: shown.media_id,
          page: selected.page,
          rawReasons: replacement?.not_chosen_because ?? selected.chosen_because,
          slot_media_id: selected.media_id,
          uri: shown.uri,
        };
      });
    const maxPage = chosen.reduce((max, item) => Math.max(max, item.page), 0);
    const addedItems = data.pool
      .filter((item) => added.has(item.media_id))
      .map((item, index) => ({
        // A photo added back from the pool owns no slot, so it has no alternates.
        alternativeCount: 0,
        caption: copy.review.changedReason,
        media_id: item.media_id,
        page: maxPage + index + 1,
        rawReasons: item.reasons,
        slot_media_id: `pool:${item.media_id}`,
        uri: item.uri,
      }));
    return [...chosen, ...addedItems];
  }, [added, data.pool, data.selected, mediaById, removed, swaps]);

  const albumItems = useMemo<LightboxItem[]>(
    () => gridItems.map((item) => ({
      caption: item.caption,
      media_id: item.media_id,
      rawReasons: item.rawReasons,
      // No slot id means no alternates to browse; the lightbox greys its primary
      // action out rather than offering a button that does nothing.
      slot_media_id: item.alternativeCount > 0 ? item.slot_media_id : undefined,
      uri: item.uri,
    })),
    [gridItems],
  );
  const selectedForViewer = viewer?.slotMediaId
    ? data.selected.find((item) => item.media_id === viewer.slotMediaId)
    : undefined;
  const lightboxItems = viewer?.mode === "browse-alternatives" && selectedForViewer
    ? alternativeItems(selectedForViewer)
    : albumItems;

  const openAlternatesViewer = useCallback((slotMediaId: string, requestedIndex?: number) => {
    const selected = data.selected.find((item) => item.media_id === slotMediaId);
    if (!selected) return;
    const shownId = swaps[slotMediaId] ?? slotMediaId;
    const items = alternativeItems(selected);
    const initialIndex = requestedIndex ?? Math.max(0, items.findIndex((item) => item.media_id === shownId));
    setViewer({ initialIndex, mode: "browse-alternatives", slotMediaId });
  }, [data.selected, swaps]);

  const replaceInSlot = useCallback((
    slotMediaId: string,
    chosenMediaId: string,
    decisionSurface: "swap-sheet" | "lightbox",
  ) => {
    const rejectedMediaId = swaps[slotMediaId] ?? slotMediaId;
    void captureAlbumEditPreference({
      albumId: data.album_id,
      slotAssetId: slotMediaId,
      rejectedAssetId: rejectedMediaId,
      chosenAssetId: chosenMediaId,
      decisionSurface,
      capturedAt: Date.now(),
    });
    setSwaps((current) => {
      if (chosenMediaId === slotMediaId) {
        const next = { ...current };
        delete next[slotMediaId];
        return next;
      }
      return { ...current, [slotMediaId]: chosenMediaId };
    });
  }, [data.album_id, swaps]);

  const useThisPhoto = useCallback((item: LightboxItem) => {
    const slotMediaId = viewer?.slotMediaId;
    if (!slotMediaId) return;
    replaceInSlot(slotMediaId, item.media_id, "lightbox");
    setViewer(null);
  }, [replaceInSlot, viewer?.slotMediaId]);

  return (
    <View style={styles.root}>
      <StatusBar backgroundColor={colors.background} barStyle="dark-content" />
      <ScrollView contentContainerStyle={styles.scroll} contentInsetAdjustmentBehavior="automatic">
        <View style={styles.steps}><Text style={styles.stepIdle}>Pick</Text><Text style={styles.arrow}>→</Text><Text style={styles.stepActive}>Review</Text><Text style={styles.arrow}>→</Text><Text style={styles.stepIdle}>Done</Text></View>
        <View style={styles.headingRow}>
          <Pressable accessibilityRole="button" onPress={onBack} style={styles.back}><Text style={styles.backText}>‹</Text></Pressable>
          <View style={styles.headingCopy}>
            <Text accessibilityRole="header" style={styles.title}>{gridItems.length > 0 ? `${gridItems.length} photos made the cut` : copy.review.emptyTitle}</Text>
            <Text style={styles.helper}>{gridItems.length > 0 ? "Tap a photo to see it big. “See other shots” swaps it, ✕ takes it out." : copy.review.emptyHelper}</Text>
          </View>
        </View>

        {gridItems.length > 0 ? (
          <View style={styles.grid}>
            {gridItems.map((item, index) => {
              // A slot with no runner-up has nothing to swap in. The button used
              // to open a sheet that showed the same photo back, which read as
              // broken; say so on the control instead.
              const canSwap = item.alternativeCount > 0;
              return (
              <View key={item.slot_media_id} style={styles.card}>
                <Pressable accessibilityHint={copy.review.openPhotoHint} accessibilityLabel={item.caption} accessibilityRole="button" onPress={() => setViewer({ initialIndex: index, mode: "browse-album" })}>
                  <Image cachePolicy="memory-disk" contentFit="cover" source={item.uri} style={styles.image} transition={120} />
                  <View style={styles.expand}><Text style={styles.expandText}>⤢</Text></View>
                </Pressable>
                <Text numberOfLines={3} style={styles.reason}>{item.caption}</Text>
                <View style={styles.actions}>
                  <Pressable
                    accessibilityRole="button"
                    accessibilityState={{ disabled: !canSwap }}
                    disabled={!canSwap}
                    onPress={() => setSwapSlot(item.slot_media_id)}
                    style={({ pressed }) => [styles.swap, pressed ? styles.pressed : null, canSwap ? null : styles.disabled]}
                  >
                    <Text numberOfLines={1} style={styles.swapText}>
                      {canSwap ? `See ${item.alternativeCount} other ${item.alternativeCount === 1 ? "shot" : "shots"}` : "Only shot"}
                    </Text>
                  </Pressable>
                  <Pressable
                    accessibilityLabel="Remove from album"
                    accessibilityRole="button"
                    onPress={() => {
                      if (item.slot_media_id.startsWith("pool:")) {
                        setAdded((current) => { const next = new Set(current); next.delete(item.media_id); return next; });
                      } else {
                        setRemoved((current) => new Set(current).add(item.slot_media_id));
                      }
                    }}
                    style={({ pressed }) => [styles.remove, pressed ? styles.pressed : null]}
                  >
                    <Text style={styles.removeText}>✕</Text>
                  </Pressable>
                </View>
              </View>
              );
            })}
          </View>
        ) : (
          <View style={styles.empty}>
            <PrimaryButton accessibilityHint={copy.review.backHint} label={copy.review.emptyAction} onPress={onBack} />
          </View>
        )}

        {gridItems.length > 0 && data.pool.length > 0 ? (
          <View style={styles.missedSection}>
            <View style={styles.missedHeading}><Text style={styles.missedTitle}>Good shots that missed out</Text><Text style={styles.missedCount}>{data.pool.length} available</Text></View>
            <Text style={styles.missedHelper}>Strong photos we left out to keep the album varied. Add any you want back in.</Text>
            <ScrollView horizontal contentContainerStyle={styles.missedRail} showsHorizontalScrollIndicator={false}>
              {data.pool.map((item) => {
                const isAdded = added.has(item.media_id);
                return (
                  <View key={item.media_id} style={styles.missedCard}>
                    <Image cachePolicy="memory-disk" contentFit="cover" source={item.uri} style={styles.missedImage} />
                    <Text numberOfLines={2} style={styles.missedReason}>{plainAlternativeReason(item.reasons)}</Text>
                    <Pressable
                      accessibilityLabel={isAdded ? "Added to album. Tap to remove." : "Add to album"}
                      accessibilityRole="button"
                      accessibilityState={{ selected: isAdded }}
                      onPress={() => setAdded((current) => { const next = new Set(current); if (next.has(item.media_id)) next.delete(item.media_id); else next.add(item.media_id); return next; })}
                      style={[styles.addButton, isAdded ? styles.addButtonActive : null]}
                    >
                      <Text style={[styles.addText, isAdded ? styles.addTextActive : null]}>{isAdded ? "Added" : "Add to album"}</Text>
                    </Pressable>
                  </View>
                );
              })}
            </ScrollView>
          </View>
        ) : null}
      </ScrollView>

      {gridItems.length > 0 ? (
        <View style={[styles.footer, { paddingBottom: Math.max(spacing.lg, insets.bottom + spacing.sm) }]}>
          <PrimaryButton
            accessibilityHint={copy.review.makeHint}
            label="Make my album"
            onPress={() => onFinalize(gridItems.map((item) => ({ media_id: item.media_id, page: item.page, uri: item.uri })))}
          />
        </View>
      ) : null}

      <SwapSheet
        currentMediaId={swapSlot ? swaps[swapSlot] ?? swapSlot : ""}
        onClose={() => setSwapSlot(null)}
        onOpen={(index) => {
          if (swapSlot) openAlternatesViewer(swapSlot, index);
          setSwapSlot(null);
        }}
        onPick={(mediaId) => {
          if (!swapSlot) return;
          replaceInSlot(swapSlot, mediaId, "swap-sheet");
        }}
        selected={swapSlot ? data.selected.find((item) => item.media_id === swapSlot) ?? null : null}
        visible={swapSlot !== null}
      />

      <Lightbox
        initialIndex={viewer?.initialIndex ?? 0}
        items={lightboxItems}
        mode={viewer?.mode ?? "browse-album"}
        onClose={() => setViewer(null)}
        onOpenAlternatives={(item) => item.slot_media_id && openAlternatesViewer(item.slot_media_id)}
        onUseThisPhoto={useThisPhoto}
        visible={viewer !== null}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  actions: { flexDirection: "row", gap: 6, marginTop: "auto" },
  addButton: { alignItems: "center", borderColor: colors.hairline, borderRadius: 22, borderWidth: 1, justifyContent: "center", minHeight: 44 },
  addButtonActive: { backgroundColor: colors.panelRaised, borderColor: colors.gold },
  addText: { color: colors.text, fontFamily: fonts.bold, fontSize: 13 },
  addTextActive: { color: colors.goldPressed },
  arrow: { color: colors.muted, fontFamily: fonts.bold, fontSize: 12.5 },
  back: { alignItems: "center", height: 44, justifyContent: "center", width: 44 },
  backText: { color: colors.muted, fontFamily: fonts.regular, fontSize: 26 },
  card: { gap: 7, width: "47.8%" },
  disabled: { opacity: 0.45 },
  empty: { paddingHorizontal: spacing.md, paddingTop: spacing.xl },
  expand: { alignItems: "center", backgroundColor: "rgba(20,15,10,.5)", borderRadius: 14, bottom: 8, height: 28, justifyContent: "center", position: "absolute", right: 8, width: 28 },
  expandText: { color: colors.onAccent, fontFamily: fonts.bold, fontSize: 13 },
  footer: { backgroundColor: colors.background, borderTopColor: colors.hairline, borderTopWidth: 1, paddingBottom: spacing.lg, paddingHorizontal: spacing.md, paddingTop: spacing.sm },
  grid: { flexDirection: "row", flexWrap: "wrap", gap: 14 },
  headingCopy: { flex: 1, gap: spacing.xs },
  headingRow: { flexDirection: "row", gap: spacing.xs },
  helper: { color: colors.muted, fontFamily: fonts.regular, fontSize: 14.5, lineHeight: 21 },
  image: { aspectRatio: 1, backgroundColor: colors.hairline, borderCurve: "continuous", borderRadius: 14, width: "100%" },
  missedCard: { gap: 7, width: 132 },
  missedCount: { color: colors.goldPressed, fontFamily: fonts.bold, ...typeScale.small },
  missedHeading: { alignItems: "baseline", flexDirection: "row", justifyContent: "space-between" },
  missedHelper: { color: colors.muted, fontFamily: fonts.regular, ...typeScale.small },
  missedImage: { backgroundColor: colors.hairline, borderCurve: "continuous", borderRadius: 14, height: 132, width: 132 },
  missedRail: { gap: spacing.sm, paddingVertical: spacing.sm },
  missedReason: { color: colors.muted, fontFamily: fonts.regular, fontSize: 12.5, lineHeight: 17, minHeight: 34 },
  missedSection: { gap: spacing.xs, paddingTop: spacing.lg },
  missedTitle: { color: colors.text, fontFamily: fonts.extraBold, fontSize: 18, letterSpacing: -0.3 },
  pressed: { opacity: 0.65 },
  reason: { color: colors.muted, fontFamily: fonts.regular, fontSize: 13, lineHeight: 18, minHeight: 52 },
  remove: { alignItems: "center", backgroundColor: colors.panel, borderColor: colors.hairline, borderRadius: 22, borderWidth: 1, justifyContent: "center", minHeight: 44, width: 44 },
  removeText: { color: colors.error, fontFamily: fonts.bold, fontSize: 14 },
  root: { backgroundColor: colors.background, flex: 1 },
  scroll: { paddingBottom: spacing.xl, paddingHorizontal: 18, paddingTop: (StatusBar.currentHeight ?? 24) + spacing.sm },
  stepActive: { color: colors.goldPressed, fontFamily: fonts.bold, fontSize: 12.5 },
  stepIdle: { color: colors.muted, fontFamily: fonts.bold, fontSize: 12.5 },
  steps: { flexDirection: "row", gap: 6, justifyContent: "center", paddingBottom: spacing.sm },
  swap: { alignItems: "center", backgroundColor: colors.panel, borderColor: colors.hairline, borderRadius: 22, borderWidth: 1, flex: 1, justifyContent: "center", minHeight: 44, paddingHorizontal: 6 },
  swapText: { color: colors.text, fontFamily: fonts.bold, fontSize: 12 },
  title: { color: colors.text, fontFamily: fonts.extraBold, fontSize: 27, letterSpacing: -0.8, lineHeight: 32 },
});
