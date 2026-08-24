import { useCallback, useMemo, useState } from "react";
import { Pressable, StatusBar, StyleSheet, Text, View } from "react-native";

import {
  colors,
  copy,
  EmptyState,
  fonts,
  plainAlternativeReason,
  plainChosenReason,
  PrimaryButton,
  ScreenHeader,
  spacing,
  typeScale,
} from "../ui";
import Lightbox, { type LightboxItem, type LightboxMode } from "./Lightbox";
import ReviewGrid, { type ReviewGridItem } from "./ReviewGrid";
import mockReviewData, {
  type ReviewAlternative,
  type ReviewData,
  type ReviewMedia,
  type ReviewSelected,
} from "./mock-data";

type Swaps = Record<string, string>;

type ViewerState = {
  mode: LightboxMode;
  initialIndex: number;
  slotMediaId?: string;
};

function alternativeCaption(alternative: ReviewAlternative) {
  const reason = plainAlternativeReason(alternative.not_chosen_because);
  return alternative.fits_slot ? reason : `${copy.review.notSafe} ${reason}`;
}

function alternativeItems(selected: ReviewSelected): LightboxItem[] {
  return [
    {
      media_id: selected.media_id,
      uri: selected.uri,
      caption: plainChosenReason(selected.chosen_because),
      rawReasons: selected.chosen_because,
      slot_media_id: selected.media_id,
    },
    ...selected.alternatives.map((alternative) => ({
      media_id: alternative.media_id,
      uri: alternative.uri,
      caption: alternativeCaption(alternative),
      rawReasons: alternative.not_chosen_because,
      slot_media_id: selected.media_id,
    })),
  ];
}

export default function ReviewScreen({
  data = mockReviewData,
  onBack,
  onFinalize,
}: {
  data?: ReviewData;
  onBack?: () => void;
  onFinalize?: (photos: { media_id: string; uri: string; page: number }[]) => void;
} = {}) {
  const [swaps, setSwaps] = useState<Swaps>({});
  const [viewer, setViewer] = useState<ViewerState | null>(null);

  const mediaById = useMemo(() => {
    const entries: ReviewMedia[] = [
      ...data.selected,
      ...data.pool,
      ...data.selected.flatMap((item) => item.alternatives),
    ];
    return new Map(entries.map((item) => [item.media_id, item]));
  }, [data]);

  const gridItems = useMemo<ReviewGridItem[]>(
    () =>
      data.selected.map((selected) => {
        const replacementId = swaps[selected.media_id];
        const replacement = replacementId
          ? selected.alternatives.find((item) => item.media_id === replacementId)
          : undefined;
        const shown =
          (replacementId ? mediaById.get(replacementId) : undefined) ?? selected;
        return {
          slot_media_id: selected.media_id,
          media_id: shown.media_id,
          uri: shown.uri,
          page: selected.page,
          caption: replacementId
            ? copy.review.changedReason
            : plainChosenReason(selected.chosen_because),
          rawReasons: replacement?.not_chosen_because ?? selected.chosen_because,
          isSwap: Boolean(replacementId),
        };
      }),
    [data.selected, mediaById, swaps],
  );

  const albumItems = useMemo<LightboxItem[]>(
    () =>
      gridItems.map((item) => ({
        media_id: item.media_id,
        uri: item.uri,
        caption: item.caption,
        rawReasons: item.rawReasons,
        slot_media_id: item.slot_media_id,
      })),
    [gridItems],
  );

  const selectedForViewer = viewer?.slotMediaId
    ? data.selected.find((item) => item.media_id === viewer.slotMediaId)
    : undefined;
  const lightboxItems =
    viewer?.mode === "browse-alternatives" && selectedForViewer
      ? alternativeItems(selectedForViewer)
      : albumItems;

  const openAlbum = useCallback((_item: ReviewGridItem, index: number) => {
    setViewer({ mode: "browse-album", initialIndex: index });
  }, []);

  const openAlternatives = useCallback(
    (item: LightboxItem) => {
      const selected = data.selected.find(
        (candidate) => candidate.media_id === item.slot_media_id,
      );
      if (!selected) return;

      const items = alternativeItems(selected);
      const shownId = swaps[selected.media_id] ?? selected.media_id;
      const initialIndex = Math.max(
        0,
        items.findIndex((candidate) => candidate.media_id === shownId),
      );
      setViewer({
        mode: "browse-alternatives",
        initialIndex,
        slotMediaId: selected.media_id,
      });
    },
    [data.selected, swaps],
  );

  const useThisPhoto = useCallback(
    (item: LightboxItem) => {
      const slotMediaId = viewer?.slotMediaId;
      if (!slotMediaId) return;

      setSwaps((current) => {
        if (item.media_id === slotMediaId) {
          const next = { ...current };
          delete next[slotMediaId];
          return next;
        }
        return { ...current, [slotMediaId]: item.media_id };
      });
      setViewer(null);
    },
    [viewer?.slotMediaId],
  );

  const finalize = useCallback(() => {
    onFinalize?.(
      gridItems.map((item) => ({
        media_id: item.media_id,
        uri: item.uri,
        page: item.page,
      })),
    );
  }, [gridItems, onFinalize]);

  const swapCount = Object.keys(swaps).length;

  return (
    <View style={styles.root}>
      <StatusBar backgroundColor={colors.background} barStyle="light-content" />
      <View style={styles.header}>
        <ScreenHeader
          backHint={copy.review.backHint}
          compact
          helper={copy.review.helper}
          onBack={onBack}
          step={2}
          title={copy.review.title}
        />
        <View style={styles.summaryRow}>
          <Text style={styles.count}>{copy.review.count(gridItems.length)}</Text>
          {swapCount > 0 ? (
            <Pressable
              accessibilityHint={copy.review.resetHint(swapCount)}
              accessibilityLabel={copy.review.reset}
              accessibilityRole="button"
              onPress={() => setSwaps({})}
              style={({ pressed }) => [styles.reset, pressed ? styles.pressed : null]}
            >
              <Text style={styles.resetText}>{copy.review.reset}</Text>
            </Pressable>
          ) : null}
        </View>
      </View>

      <View style={styles.gridWrap}>
        {gridItems.length > 0 ? (
          <ReviewGrid items={gridItems} onPressPhoto={openAlbum} />
        ) : (
          <EmptyState
            actionHint={copy.review.backHint}
            actionLabel={copy.common.goBack}
            helper={copy.review.emptyHelper}
            onAction={onBack}
            title={copy.review.emptyTitle}
          />
        )}
      </View>

      {onFinalize && gridItems.length > 0 ? (
        <View style={styles.footer}>
          <PrimaryButton
            accessibilityHint={copy.review.makeHint}
            label={copy.review.make}
            onPress={finalize}
          />
        </View>
      ) : null}

      <Lightbox
        initialIndex={viewer?.initialIndex ?? 0}
        items={lightboxItems}
        mode={viewer?.mode ?? "browse-album"}
        onClose={() => setViewer(null)}
        onOpenAlternatives={openAlternatives}
        onUseThisPhoto={useThisPhoto}
        visible={viewer !== null}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  count: { color: colors.muted, fontFamily: fonts.body, ...typeScale.small },
  footer: {
    backgroundColor: colors.panel,
    borderTopColor: colors.hairline,
    borderTopWidth: 1,
    paddingBottom: spacing.lg,
    paddingHorizontal: spacing.md,
    paddingTop: spacing.sm,
  },
  gridWrap: { flex: 1 },
  header: {
    gap: spacing.sm,
    paddingBottom: spacing.sm,
    paddingHorizontal: spacing.md,
    paddingTop: (StatusBar.currentHeight ?? 24) + spacing.sm,
  },
  pressed: { opacity: 0.62 },
  reset: { justifyContent: "center", minHeight: 48, paddingLeft: spacing.md },
  resetText: { color: colors.gold, fontFamily: fonts.body, textDecorationLine: "underline", ...typeScale.small },
  root: { backgroundColor: colors.background, flex: 1 },
  summaryRow: { alignItems: "center", flexDirection: "row", justifyContent: "space-between" },
});
