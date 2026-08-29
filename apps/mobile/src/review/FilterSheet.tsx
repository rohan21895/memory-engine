import { Image } from "expo-image";
import { useEffect, useRef, useState } from "react";
import { ActivityIndicator, Modal, Pressable, ScrollView, StatusBar, StyleSheet, Text, View } from "react-native";

import { filteredPhoto, photoFilters } from "../../modules/photeo-scan-service/src";
import { colors, fonts, spacing, typeScale } from "../ui";

/** Native ids are the contract; these are only what the swatch says underneath. */
const FILTER_LABELS: Record<string, string> = {
  original: "Original",
  mono: "Mono",
  noir: "Noir",
  warm: "Warm",
  cool: "Cool",
  fade: "Fade",
  vivid: "Vivid",
};

const SWATCH_EDGE = 200;
const PREVIEW_EDGE = 1280;

export type AppliedFilter = { filter: string; uri: string };

/**
 * Picks a look for one photo before it goes into the album.
 *
 * Both the swatch and the full preview come from the same native
 * `filteredPhoto` call at different sizes, so the look he taps is exactly the
 * look he keeps -- there is no second, approximate rendering path that could
 * drift from the baked result.
 *
 * The preview is `contain`, not `cover`. This sheet exists to judge a photo,
 * and a crop that hides part of the frame is the opposite of that.
 */
export function FilterSheet({
  assetId,
  current,
  onClose,
  onPick,
  originalUri,
  visible,
}: {
  assetId: string | null;
  current: string;
  onClose: () => void;
  onPick: (applied: AppliedFilter | null) => void;
  originalUri: string;
  visible: boolean;
}) {
  const [filters, setFilters] = useState<string[]>(["original"]);
  const [swatches, setSwatches] = useState<Record<string, string>>({});
  const [active, setActive] = useState(current);
  const [preview, setPreview] = useState<AppliedFilter | null>(null);
  const [busy, setBusy] = useState(false);

  // Every async result below is stamped with the photo it belongs to. Without
  // this, closing one photo's sheet and opening another's paints the first
  // photo's swatches under the second photo's name.
  const requestFor = useRef<string | null>(null);

  useEffect(() => {
    if (!visible || !assetId) return;
    requestFor.current = assetId;
    setActive(current);
    setPreview(null);
    setSwatches({});

    let cancelled = false;
    void (async () => {
      const available = await photoFilters();
      if (cancelled || requestFor.current !== assetId) return;
      setFilters(available);
      // Sequential on purpose: these all land on one native thumbnail queue, and
      // firing seven at once only makes the first swatch appear later.
      for (const filter of available) {
        const result = await filteredPhoto(assetId, filter, SWATCH_EDGE);
        if (cancelled || requestFor.current !== assetId) return;
        if (result) setSwatches((all) => ({ ...all, [filter]: result.uri }));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [assetId, current, visible]);

  // The large preview is fetched only for the look being considered, so opening
  // the sheet costs one full-size decode rather than seven.
  useEffect(() => {
    if (!visible || !assetId) return;
    if (active === "original") {
      setPreview(null);
      setBusy(false);
      return;
    }
    let cancelled = false;
    setBusy(true);
    void (async () => {
      const result = await filteredPhoto(assetId, active, PREVIEW_EDGE);
      if (cancelled || requestFor.current !== assetId) return;
      setPreview(result ? { filter: active, uri: result.uri } : null);
      setBusy(false);
    })();
    return () => {
      cancelled = true;
    };
  }, [active, assetId, visible]);

  const shownUri = active === "original" ? originalUri : preview?.uri ?? originalUri;

  return (
    <Modal animationType="slide" onRequestClose={onClose} presentationStyle="pageSheet" visible={visible}>
      <View accessibilityViewIsModal style={styles.root}>
        <StatusBar backgroundColor={colors.panel} barStyle="dark-content" />
        <View style={styles.header}>
          <View style={styles.headingCopy}>
            <Text accessibilityRole="header" style={styles.title}>Choose a look</Text>
            <Text style={styles.helper}>Tap a look to try it. It only changes this photo.</Text>
          </View>
          <Pressable accessibilityLabel="Close looks" accessibilityRole="button" hitSlop={10} onPress={onClose} style={styles.close}>
            <Text style={styles.closeText}>✕</Text>
          </Pressable>
        </View>

        <View style={styles.stage}>
          <Image cachePolicy="memory-disk" contentFit="contain" source={shownUri} style={styles.preview} transition={140} />
          {busy ? (
            <View style={styles.busy}><ActivityIndicator color={colors.gold} /></View>
          ) : null}
        </View>

        <ScrollView horizontal contentContainerStyle={styles.rail} showsHorizontalScrollIndicator={false}>
          {filters.map((filter) => {
            const isActive = filter === active;
            const swatch = swatches[filter];
            return (
              <Pressable
                accessibilityLabel={FILTER_LABELS[filter] ?? filter}
                accessibilityRole="radio"
                accessibilityState={{ checked: isActive }}
                key={filter}
                onPress={() => setActive(filter)}
                style={styles.swatchCard}
              >
                {swatch ? (
                  <Image cachePolicy="memory-disk" contentFit="cover" source={swatch} style={[styles.swatch, isActive ? styles.swatchActive : null]} />
                ) : (
                  <View style={[styles.swatch, styles.swatchEmpty, isActive ? styles.swatchActive : null]} />
                )}
                <Text numberOfLines={1} style={[styles.swatchLabel, isActive ? styles.swatchLabelActive : null]}>
                  {FILTER_LABELS[filter] ?? filter}
                </Text>
              </Pressable>
            );
          })}
        </ScrollView>

        <View style={styles.footer}>
          <Pressable
            accessibilityHint="Keeps this look on the photo"
            accessibilityRole="button"
            disabled={busy}
            onPress={() => {
              // "original" clears the look rather than storing an identity copy,
              // so a photo he reset keeps its own untouched URI in the album.
              onPick(active === "original" || !preview ? null : preview);
              onClose();
            }}
            style={({ pressed }) => [styles.use, pressed ? styles.pressed : null, busy ? styles.useBusy : null]}
          >
            <Text style={styles.useText}>{active === "original" ? "Keep original" : `Use ${FILTER_LABELS[active] ?? active}`}</Text>
          </Pressable>
        </View>
      </View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  busy: { alignItems: "center", bottom: 0, justifyContent: "center", left: 0, position: "absolute", right: 0, top: 0 },
  close: { alignItems: "center", backgroundColor: "#f0eee8", borderRadius: 17, height: 34, justifyContent: "center", width: 34 },
  closeText: { color: colors.text, fontFamily: fonts.semibold, fontSize: 15 },
  footer: { borderTopColor: colors.hairline, borderTopWidth: 1, padding: spacing.md, paddingBottom: spacing.lg },
  header: { alignItems: "center", flexDirection: "row", gap: spacing.sm, paddingHorizontal: spacing.lg, paddingTop: (StatusBar.currentHeight ?? 24) + spacing.md },
  headingCopy: { flex: 1 },
  helper: { color: colors.muted, fontFamily: fonts.regular, paddingTop: spacing.xs, ...typeScale.small },
  preview: { flex: 1, width: "100%" },
  pressed: { opacity: 0.85 },
  rail: { alignItems: "flex-start", gap: 10, paddingHorizontal: spacing.lg, paddingVertical: spacing.md },
  root: { backgroundColor: colors.panel, flex: 1 },
  stage: { backgroundColor: "#141414", flex: 1, margin: spacing.lg, overflow: "hidden", borderCurve: "continuous", borderRadius: 16 },
  swatch: { aspectRatio: 1, backgroundColor: colors.hairline, borderCurve: "continuous", borderRadius: 12, width: 72 },
  swatchActive: { borderColor: colors.gold, borderWidth: 3 },
  swatchCard: { alignItems: "center", gap: 5, width: 72 },
  swatchEmpty: { opacity: 0.5 },
  swatchLabel: { color: colors.muted, fontFamily: fonts.regular, fontSize: 11.5 },
  swatchLabelActive: { color: colors.text, fontFamily: fonts.bold },
  title: { color: colors.text, fontFamily: fonts.extraBold, ...typeScale.subtitle },
  use: { alignItems: "center", backgroundColor: colors.gold, borderRadius: 27, height: 54, justifyContent: "center" },
  useBusy: { opacity: 0.6 },
  useText: { color: colors.onAccent, fontFamily: fonts.bold, ...typeScale.label },
});
