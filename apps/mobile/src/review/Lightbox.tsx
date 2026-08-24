import { Image } from "expo-image";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Animated,
  Modal,
  PanResponder,
  Pressable,
  StatusBar,
  StyleSheet,
  Text,
  useWindowDimensions,
  View,
} from "react-native";

import { colors, copy, fonts, PrimaryButton, spacing, typeScale } from "../ui";

const SWIPE_DURATION_MS = 170;

export type LightboxMode = "browse-album" | "browse-alternatives";

export type LightboxItem = {
  media_id: string;
  uri: string;
  caption: string;
  rawReasons: string[];
  slot_media_id?: string;
};

export type LightboxProps = {
  visible: boolean;
  mode: LightboxMode;
  items: LightboxItem[];
  initialIndex?: number;
  onClose: () => void;
  onOpenAlternatives?: (item: LightboxItem, index: number) => void;
  onUseThisPhoto?: (item: LightboxItem, index: number) => void;
};

function clampIndex(index: number, itemCount: number) {
  return Math.max(0, Math.min(index, Math.max(0, itemCount - 1)));
}

export function Lightbox({
  visible,
  mode,
  items,
  initialIndex = 0,
  onClose,
  onOpenAlternatives,
  onUseThisPhoto,
}: LightboxProps) {
  const { width } = useWindowDimensions();
  const translateX = useRef(new Animated.Value(0)).current;
  const isAnimating = useRef(false);
  const [whyExpanded, setWhyExpanded] = useState(false);
  const [currentIndex, setCurrentIndex] = useState(() =>
    clampIndex(initialIndex, items.length),
  );

  useEffect(() => {
    if (!visible) return;
    translateX.stopAnimation();
    translateX.setValue(0);
    isAnimating.current = false;
    setWhyExpanded(false);
    setCurrentIndex(clampIndex(initialIndex, items.length));
  }, [initialIndex, items.length, mode, translateX, visible]);

  const settle = useCallback(() => {
    Animated.spring(translateX, {
      toValue: 0,
      speed: 24,
      bounciness: 0,
      useNativeDriver: true,
    }).start();
  }, [translateX]);

  const changePage = useCallback(
    (direction: -1 | 1) => {
      if (isAnimating.current) return;

      const nextIndex = currentIndex + direction;
      if (nextIndex < 0 || nextIndex >= items.length) {
        settle();
        return;
      }

      isAnimating.current = true;
      Animated.timing(translateX, {
        duration: SWIPE_DURATION_MS,
        toValue: direction === 1 ? -width : width,
        useNativeDriver: true,
      }).start(({ finished }) => {
        if (!finished) {
          isAnimating.current = false;
          settle();
          return;
        }

        setWhyExpanded(false);
        setCurrentIndex(nextIndex);
        translateX.setValue(direction === 1 ? width : -width);
        Animated.timing(translateX, {
          duration: SWIPE_DURATION_MS,
          toValue: 0,
          useNativeDriver: true,
        }).start(() => {
          isAnimating.current = false;
        });
      });
    },
    [currentIndex, items.length, settle, translateX, width],
  );

  const panResponder = useMemo(
    () =>
      PanResponder.create({
        onMoveShouldSetPanResponder: (_, gesture) =>
          Math.abs(gesture.dx) > 8 && Math.abs(gesture.dx) > Math.abs(gesture.dy),
        onPanResponderGrant: () => translateX.stopAnimation(),
        onPanResponderMove: (_, gesture) => {
          if (!isAnimating.current) translateX.setValue(gesture.dx);
        },
        onPanResponderRelease: (_, gesture) => {
          const passesDistance = Math.abs(gesture.dx) > width * 0.16;
          const passesVelocity = Math.abs(gesture.vx) > 0.5;
          if (passesDistance || passesVelocity) {
            changePage(gesture.dx < 0 ? 1 : -1);
          } else {
            settle();
          }
        },
        onPanResponderTerminate: settle,
      }),
    [changePage, settle, translateX, width],
  );

  const currentItem = items[currentIndex];
  const handlePrimary = useCallback(() => {
    if (!currentItem) return;
    if (mode === "browse-album") {
      onOpenAlternatives?.(currentItem, currentIndex);
    } else {
      onUseThisPhoto?.(currentItem, currentIndex);
    }
  }, [currentIndex, currentItem, mode, onOpenAlternatives, onUseThisPhoto]);

  const primaryLabel =
    mode === "browse-album" ? copy.lightbox.alternatives : copy.lightbox.usePhoto;
  const primaryHint =
    mode === "browse-album" ? copy.lightbox.alternativesHint : copy.lightbox.usePhotoHint;

  return (
    <Modal
      animationType="fade"
      navigationBarTranslucent
      onRequestClose={onClose}
      statusBarTranslucent
      supportedOrientations={["portrait"]}
      visible={visible}
    >
      <View accessibilityViewIsModal style={styles.root}>
        <Animated.View
          {...panResponder.panHandlers}
          style={[styles.photoStage, { transform: [{ translateX }] }]}
        >
          {currentItem ? (
            <Image
              accessibilityLabel={currentItem.caption}
              cachePolicy="memory-disk"
              contentFit="contain"
              source={currentItem.uri}
              style={styles.image}
              transition={100}
            />
          ) : (
            <Text style={styles.noPhoto}>{copy.lightbox.noPhoto}</Text>
          )}
        </Animated.View>

        <Pressable
          accessibilityHint={copy.common.closeHint}
          accessibilityLabel={copy.lightbox.close}
          accessibilityRole="button"
          onPress={onClose}
          style={({ pressed }) => [styles.close, pressed ? styles.controlPressed : null]}
        >
          <Text style={styles.closeText}>×</Text>
        </Pressable>

        <Pressable
          accessibilityHint={copy.lightbox.previousHint}
          accessibilityLabel={copy.lightbox.previous}
          accessibilityRole="button"
          accessibilityState={{ disabled: currentIndex === 0 }}
          disabled={currentIndex === 0}
          onPress={() => changePage(-1)}
          style={({ pressed }) => [
            styles.arrow,
            styles.arrowLeft,
            currentIndex === 0 ? styles.controlDisabled : null,
            pressed ? styles.controlPressed : null,
          ]}
        >
          <Text style={styles.arrowText}>‹</Text>
        </Pressable>

        <Pressable
          accessibilityHint={copy.lightbox.nextHint}
          accessibilityLabel={copy.lightbox.next}
          accessibilityRole="button"
          accessibilityState={{ disabled: currentIndex >= items.length - 1 }}
          disabled={currentIndex >= items.length - 1}
          onPress={() => changePage(1)}
          style={({ pressed }) => [
            styles.arrow,
            styles.arrowRight,
            currentIndex >= items.length - 1 ? styles.controlDisabled : null,
            pressed ? styles.controlPressed : null,
          ]}
        >
          <Text style={styles.arrowText}>›</Text>
        </Pressable>

        <View style={styles.footer}>
          <Text accessibilityLiveRegion="polite" style={styles.counter}>
            {copy.lightbox.counter(items.length === 0 ? 0 : currentIndex + 1, items.length)}
          </Text>
          <Pressable
            accessibilityHint={copy.review.whyHint}
            accessibilityLabel={copy.review.why}
            accessibilityRole="button"
            accessibilityState={{ expanded: whyExpanded }}
            onPress={() => setWhyExpanded((expanded) => !expanded)}
            style={({ pressed }) => [styles.whyButton, pressed ? styles.controlPressed : null]}
          >
            <Text style={styles.whyLabel}>{copy.review.why}</Text>
          </Pressable>
          {whyExpanded ? <Text style={styles.caption}>{currentItem?.caption ?? ""}</Text> : null}
          <PrimaryButton
            accessibilityHint={primaryHint}
            disabled={!currentItem}
            label={primaryLabel}
            onPress={handlePrimary}
          />
        </View>
      </View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  arrow: {
    alignItems: "center",
    backgroundColor: colors.panel,
    borderRadius: 24,
    height: 48,
    justifyContent: "center",
    marginTop: -24,
    position: "absolute",
    top: "43%",
    width: 48,
  },
  arrowLeft: { left: spacing.sm },
  arrowRight: { right: spacing.sm },
  arrowText: { color: colors.text, fontFamily: fonts.body, fontSize: 38, lineHeight: 40 },
  caption: { color: colors.text, fontFamily: fonts.body, ...typeScale.body },
  close: {
    alignItems: "center",
    backgroundColor: colors.panel,
    borderRadius: 24,
    height: 48,
    justifyContent: "center",
    left: spacing.md,
    position: "absolute",
    top: (StatusBar.currentHeight ?? 0) + spacing.sm,
    width: 48,
  },
  closeText: { color: colors.text, fontFamily: fonts.body, fontSize: 30, lineHeight: 32 },
  controlDisabled: { opacity: 0.24 },
  controlPressed: { opacity: 0.62 },
  counter: { color: colors.gold, fontFamily: fonts.body, fontWeight: "700", ...typeScale.small },
  footer: {
    backgroundColor: colors.panel,
    borderTopColor: colors.hairline,
    borderTopWidth: 1,
    bottom: 0,
    gap: spacing.xs,
    left: 0,
    minHeight: 212,
    paddingBottom: spacing.lg,
    paddingHorizontal: spacing.md,
    paddingTop: spacing.sm,
    position: "absolute",
    right: 0,
  },
  image: { height: "100%", width: "100%" },
  noPhoto: { color: colors.muted, fontFamily: fonts.body, ...typeScale.body },
  photoStage: {
    alignItems: "center",
    bottom: 212,
    justifyContent: "center",
    left: 0,
    position: "absolute",
    right: 0,
    top: StatusBar.currentHeight ?? 0,
  },
  root: { backgroundColor: "#090806", flex: 1 },
  whyButton: { alignSelf: "flex-start", justifyContent: "center", minHeight: 48 },
  whyLabel: { color: colors.gold, fontFamily: fonts.body, textDecorationLine: "underline", ...typeScale.label },
});

export default Lightbox;
