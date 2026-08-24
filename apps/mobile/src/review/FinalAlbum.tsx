import { FlashList } from "@shopify/flash-list";
import { Image } from "expo-image";
import { memo, useCallback } from "react";
import { Pressable, StatusBar, StyleSheet, Text, View } from "react-native";

import {
  colors,
  copy,
  EmptyState,
  fonts,
  PrimaryButton,
  ScreenHeader,
  spacing,
  typeScale,
} from "../ui";

export type FinalPhoto = {
  media_id: string;
  uri: string;
  page: number;
};

const FinalPage = memo(function FinalPage({ item, index }: { item: FinalPhoto; index: number }) {
  return (
    <View accessible accessibilityLabel={copy.final.pageLabel(index + 1)} style={styles.page}>
      <View style={styles.paper}>
        <Image
          cachePolicy="memory-disk"
          contentFit="cover"
          recyclingKey={item.media_id}
          source={item.uri}
          style={styles.photo}
          transition={140}
        />
        <Text style={styles.pageNo}>{String(index + 1).padStart(2, "0")}</Text>
      </View>
    </View>
  );
});

export default function FinalAlbum({
  photos,
  onRestart,
  onBack,
}: {
  photos: FinalPhoto[];
  onRestart: () => void;
  onBack: () => void;
}) {
  const renderItem = useCallback(
    ({ item, index }: { item: FinalPhoto; index: number }) => (
      <FinalPage index={index} item={item} />
    ),
    [],
  );

  const header = (
    <View style={styles.header}>
      <ScreenHeader
        backHint={copy.final.backHint}
        compact
        eyebrow={copy.final.celebration}
        helper={copy.final.helper(photos.length)}
        onBack={onBack}
        step={3}
        title={copy.final.title}
      />
      <View accessible={false} style={styles.celebration}>
        <Text style={styles.starSmall}>✦</Text>
        <Text style={styles.starLarge}>✦</Text>
        <Text style={styles.starSmall}>✦</Text>
      </View>
    </View>
  );

  return (
    <View style={styles.root}>
      <StatusBar backgroundColor={colors.background} barStyle="light-content" />
      <FlashList
        contentContainerStyle={styles.list}
        data={photos}
        keyExtractor={(item, index) => `${item.media_id}-${index}`}
        ListEmptyComponent={
          <EmptyState
            actionHint={copy.final.restartHint}
            actionLabel={copy.final.restart}
            helper={copy.final.emptyHelper}
            onAction={onRestart}
            title={copy.final.emptyTitle}
          />
        }
        ListFooterComponent={
          photos.length > 0 ? (
            <View style={styles.footer}>
              <PrimaryButton
                accessibilityHint={copy.final.restartHint}
                label={copy.final.restart}
                onPress={onRestart}
              />
              <Text style={styles.privacy}>{copy.trustCue} · {copy.privacyShort}</Text>
            </View>
          ) : null
        }
        ListHeaderComponent={header}
        renderItem={renderItem}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  celebration: { alignItems: "center", flexDirection: "row", gap: spacing.lg, justifyContent: "center", paddingTop: spacing.md },
  footer: { gap: spacing.md, paddingHorizontal: spacing.lg, paddingTop: spacing.lg },
  header: {
    paddingBottom: spacing.md,
    paddingHorizontal: spacing.lg,
    paddingTop: (StatusBar.currentHeight ?? 24) + spacing.sm,
  },
  list: { paddingBottom: spacing.xl },
  page: { paddingBottom: spacing.md, paddingHorizontal: spacing.md },
  pageNo: {
    bottom: spacing.sm,
    color: colors.text,
    fontFamily: fonts.body,
    fontWeight: "700",
    left: spacing.sm,
    position: "absolute",
    textShadowColor: "rgba(0, 0, 0, 0.82)",
    textShadowRadius: 6,
    ...typeScale.small,
  },
  paper: { aspectRatio: 3 / 4, backgroundColor: colors.panel, borderCurve: "continuous", borderRadius: 8, overflow: "hidden" },
  photo: { height: "100%", width: "100%" },
  privacy: { color: colors.muted, fontFamily: fonts.body, textAlign: "center", ...typeScale.small },
  root: { backgroundColor: colors.background, flex: 1 },
  starLarge: { color: colors.gold, fontSize: 28, lineHeight: 32 },
  starSmall: { color: colors.gold, fontSize: 16, lineHeight: 20, opacity: 0.7 },
});
