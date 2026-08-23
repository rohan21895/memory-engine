import { useCallback, useMemo, useState } from "react";
import { Pressable, StatusBar, StyleSheet, Text, View } from "react-native";

import Lightbox, { type LightboxItem, type LightboxMode } from "./Lightbox";
import ReviewGrid, { type ReviewGridItem } from "./ReviewGrid";
import mockReviewData, {
  type ReviewAlternative,
  type ReviewData,
  type ReviewMedia,
  type ReviewSelected,
} from "./mock-data";

const C = {
  bg: "#141311",
  line: "#2c2a25",
  text: "#e8e4dc",
  muted: "#9a927f",
  gold: "#c8a24a",
};

type Swaps = Record<string, string>;

type ViewerState = {
  mode: LightboxMode;
  initialIndex: number;
  slotMediaId?: string;
};

function alternativeCaption(alternative: ReviewAlternative) {
  const reason = alternative.not_chosen_because[0] ?? "Another strong photo.";
  return alternative.fits_slot
    ? reason
    : `May not fit this slot safely. ${reason}`;
}

function alternativeItems(selected: ReviewSelected): LightboxItem[] {
  return [
    {
      media_id: selected.media_id,
      uri: selected.uri,
      caption: `Original pick — ${selected.chosen_because[0] ?? "Engine selection."}`,
      slot_media_id: selected.media_id,
    },
    ...selected.alternatives.map((alternative) => ({
      media_id: alternative.media_id,
      uri: alternative.uri,
      caption: alternativeCaption(alternative),
      slot_media_id: selected.media_id,
    })),
  ];
}

export default function ReviewScreen({
  data = mockReviewData,
  onBack,
}: {
  data?: ReviewData;
  onBack?: () => void;
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
        const shown =
          (replacementId ? mediaById.get(replacementId) : undefined) ?? selected;
        return {
          slot_media_id: selected.media_id,
          media_id: shown.media_id,
          uri: shown.uri,
          page: selected.page,
          caption: replacementId
            ? "Your choice for this page."
            : (selected.chosen_because[0] ?? "Selected for this page."),
          isSwap: Boolean(replacementId),
        };
      }),
    [mediaById, swaps],
  );

  const albumItems = useMemo<LightboxItem[]>(
    () =>
      gridItems.map((item) => ({
        media_id: item.media_id,
        uri: item.uri,
        caption: `Page ${item.page} — ${item.caption}`,
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
      const slotMediaId = item.slot_media_id;
      const selected = data.selected.find(
        (candidate) => candidate.media_id === slotMediaId,
      );
      if (!selected) {
        return;
      }

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
    [data, swaps],
  );

  const useThisPhoto = useCallback(
    (item: LightboxItem) => {
      const slotMediaId = viewer?.slotMediaId;
      if (!slotMediaId) {
        return;
      }

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

  const swapCount = Object.keys(swaps).length;

  return (
    <View style={styles.root}>
      <StatusBar backgroundColor={C.bg} barStyle="light-content" />
      <View style={styles.header}>
        <View style={styles.headingCopy}>
          {onBack ? (
            <Pressable accessibilityRole="button" onPress={onBack} hitSlop={12}>
              <Text style={styles.back}>‹ Back</Text>
            </Pressable>
          ) : null}
          <Text style={styles.eyebrow}>YOUR ALBUM</Text>
          <Text style={styles.title}>Review the picks</Text>
          <Text style={styles.subtitle}>
            {data.selected.length} photos selected · Tap one to browse
          </Text>
        </View>
        {swapCount > 0 ? (
          <Pressable
            accessibilityLabel={`Reset ${swapCount} photo ${swapCount === 1 ? "change" : "changes"}`}
            accessibilityRole="button"
            onPress={() => setSwaps({})}
            style={({ pressed }) => [styles.reset, pressed && styles.resetPressed]}
          >
            <Text style={styles.resetCount}>{swapCount}</Text>
            <Text style={styles.resetLabel}>RESET</Text>
          </Pressable>
        ) : null}
      </View>

      <ReviewGrid items={gridItems} onPressPhoto={openAlbum} />

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
  root: { backgroundColor: C.bg, flex: 1 },
  header: {
    alignItems: "flex-end",
    borderBottomColor: C.line,
    borderBottomWidth: 1,
    flexDirection: "row",
    paddingBottom: 18,
    paddingHorizontal: 20,
    paddingTop: (StatusBar.currentHeight ?? 24) + 22,
  },
  headingCopy: { flex: 1, paddingRight: 12 },
  back: { color: C.muted, fontSize: 14, marginBottom: 8 },
  eyebrow: { color: C.gold, fontSize: 10, letterSpacing: 1.8 },
  title: { color: C.text, fontSize: 28, fontWeight: "400", marginTop: 5 },
  subtitle: { color: C.muted, fontSize: 13, lineHeight: 18, marginTop: 7 },
  reset: {
    alignItems: "center",
    borderColor: C.gold,
    borderRadius: 6,
    borderWidth: 1,
    minWidth: 54,
    paddingHorizontal: 8,
    paddingVertical: 7,
  },
  resetPressed: { opacity: 0.65 },
  resetCount: { color: C.text, fontSize: 15 },
  resetLabel: { color: C.gold, fontSize: 8, letterSpacing: 1, marginTop: 2 },
});
