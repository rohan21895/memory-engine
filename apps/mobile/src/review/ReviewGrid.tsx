import { FlashList } from "@shopify/flash-list";
import { Image } from "expo-image";
import { memo, useCallback } from "react";
import { Pressable, StyleSheet, Text, View } from "react-native";

import { colors, copy, fonts, spacing, typeScale } from "../ui";

export type ReviewGridItem = {
  slot_media_id: string;
  media_id: string;
  uri: string;
  page: number;
  caption: string;
  rawReasons: string[];
  isSwap?: boolean;
};

export type ReviewGridProps = {
  items: ReviewGridItem[];
  onPressPhoto: (item: ReviewGridItem, index: number) => void;
};

const AlbumPage = memo(function AlbumPage({
  item,
  index,
  onPressPhoto,
}: {
  item: ReviewGridItem;
  index: number;
  onPressPhoto: (item: ReviewGridItem, index: number) => void;
}) {
  const openPhoto = useCallback(() => onPressPhoto(item, index), [index, item, onPressPhoto]);
  return (
    <Pressable
      accessibilityHint={copy.review.openPhotoHint}
      accessibilityLabel={`${copy.review.page(item.page)}. ${item.caption}`}
      accessibilityRole="button"
      onPress={openPhoto}
      style={({ pressed }) => [styles.page, pressed ? styles.pagePressed : null]}
    >
      <View style={styles.paper}>
        <Image
          accessibilityLabel={copy.review.page(item.page)}
          cachePolicy="memory-disk"
          contentFit="cover"
          recyclingKey={item.media_id}
          source={item.uri}
          style={styles.image}
          transition={140}
        />
        <View style={styles.copyBlock}>
          <View style={styles.pageRow}>
            <Text style={styles.pageNumber}>{copy.review.page(item.page)}</Text>
            {item.isSwap ? <Text style={styles.yourChoice}>{copy.review.yourChoice}</Text> : null}
          </View>
          <Text style={styles.caption}>{item.caption}</Text>
          <Text style={styles.why}>{copy.review.why}</Text>
        </View>
      </View>
    </Pressable>
  );
});

export function ReviewGrid({ items, onPressPhoto }: ReviewGridProps) {
  const renderItem = useCallback(
    ({ item, index }: { item: ReviewGridItem; index: number }) => (
      <AlbumPage index={index} item={item} onPressPhoto={onPressPhoto} />
    ),
    [onPressPhoto],
  );

  return (
    <FlashList
      contentContainerStyle={styles.content}
      data={items}
      keyExtractor={(item) => item.slot_media_id}
      renderItem={renderItem}
      showsVerticalScrollIndicator={false}
    />
  );
}

const styles = StyleSheet.create({
  caption: { color: colors.ink, fontFamily: fonts.body, ...typeScale.body },
  content: { paddingBottom: spacing.xl, paddingHorizontal: spacing.md, paddingTop: spacing.sm },
  copyBlock: { gap: spacing.xs, padding: spacing.md },
  image: { aspectRatio: 4 / 3, backgroundColor: colors.hairline, width: "100%" },
  page: { marginBottom: spacing.md },
  pageNumber: { color: colors.ink, fontFamily: fonts.body, fontWeight: "700", textTransform: "uppercase", ...typeScale.small },
  pagePressed: { opacity: 0.82, transform: [{ scale: 0.992 }] },
  pageRow: { alignItems: "center", flexDirection: "row", justifyContent: "space-between" },
  paper: {
    backgroundColor: colors.text,
    borderCurve: "continuous",
    borderRadius: 8,
    overflow: "hidden",
  },
  why: { color: colors.ink, fontFamily: fonts.body, fontWeight: "700", ...typeScale.small },
  yourChoice: { color: colors.ink, fontFamily: fonts.body, fontWeight: "700", ...typeScale.small },
});

export default ReviewGrid;
