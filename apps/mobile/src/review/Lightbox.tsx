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

const C = {
  backdrop: "#090806",
  panel: "rgba(20, 19, 17, 0.94)",
  text: "#e8e4dc",
  gold: "#c8a24a",
};

const SWIPE_DURATION_MS = 170;

export type LightboxMode = "browse-album" | "browse-alternatives";

export type LightboxItem = {
  media_id: string;
  uri: string;
  caption: string;
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
  const [currentIndex, setCurrentIndex] = useState(() =>
    clampIndex(initialIndex, items.length),
  );

  useEffect(() => {
    if (!visible) {
      return;
    }
    translateX.stopAnimation();
    translateX.setValue(0);
    isAnimating.current = false;
    setCurrentIndex(clampIndex(initialIndex, items.length));
  }, [initialIndex, items.length, mode, translateX, visible]);

  const settle = useCallback(() => {
    Animated.spring(translateX, {
      toValue: 0,
      speed: 24,
      bounciness: 0,
      useNativeDriver: false,
    }).start();
  }, [translateX]);

  const changePage = useCallback(
    (direction: -1 | 1) => {
      if (isAnimating.current) {
        return;
      }

      const nextIndex = currentIndex + direction;
      if (nextIndex < 0 || nextIndex >= items.length) {
        settle();
        return;
      }

      isAnimating.current = true;
      Animated.timing(translateX, {
        duration: SWIPE_DURATION_MS,
        toValue: direction === 1 ? -width : width,
        useNativeDriver: false,
      }).start(({ finished }) => {
        if (!finished) {
          isAnimating.current = false;
          settle();
          return;
        }

        setCurrentIndex(nextIndex);
        translateX.setValue(direction === 1 ? width : -width);
        Animated.timing(translateX, {
          duration: SWIPE_DURATION_MS,
          toValue: 0,
          useNativeDriver: false,
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
        onPanResponderGrant: () => {
          translateX.stopAnimation();
        },
        onPanResponderMove: (_, gesture) => {
          if (!isAnimating.current) {
            translateX.setValue(gesture.dx);
          }
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
  const handleSelect = useCallback(() => {
    if (!currentItem) {
      return;
    }
    if (mode === "browse-album") {
      onOpenAlternatives?.(currentItem, currentIndex);
    } else {
      onUseThisPhoto?.(currentItem, currentIndex);
    }
  }, [currentIndex, currentItem, mode, onOpenAlternatives, onUseThisPhoto]);

  return (
    <Modal
      animationType="fade"
      navigationBarTranslucent
      onRequestClose={onClose}
      statusBarTranslucent
      supportedOrientations={["portrait", "landscape"]}
      transparent={false}
      visible={visible}
    >
      <View accessibilityViewIsModal style={styles.root}>
        <Animated.View
          {...panResponder.panHandlers}
          style={[styles.photoStage, { transform: [{ translateX }] }]}
        >
          {currentItem ? (
            <Animated.Image
              accessibilityLabel={currentItem.caption}
              resizeMode="contain"
              source={{ uri: currentItem.uri }}
              style={styles.image}
            />
          ) : null}
        </Animated.View>

        <Pressable
          accessibilityLabel="Close full screen photo"
          accessibilityRole="button"
          hitSlop={12}
          onPress={onClose}
          style={({ pressed }) => [styles.close, pressed && styles.controlPressed]}
        >
          <Text style={styles.closeText}>✕</Text>
        </Pressable>

        <Pressable
          accessibilityLabel="Previous photo"
          accessibilityRole="button"
          disabled={currentIndex === 0}
          hitSlop={12}
          onPress={() => changePage(-1)}
          style={({ pressed }) => [
            styles.arrow,
            styles.arrowLeft,
            currentIndex === 0 && styles.controlDisabled,
            pressed && styles.controlPressed,
          ]}
        >
          <Text style={styles.arrowText}>‹</Text>
        </Pressable>

        <Pressable
          accessibilityLabel="Next photo"
          accessibilityRole="button"
          disabled={currentIndex >= items.length - 1}
          hitSlop={12}
          onPress={() => changePage(1)}
          style={({ pressed }) => [
            styles.arrow,
            styles.arrowRight,
            currentIndex >= items.length - 1 && styles.controlDisabled,
            pressed && styles.controlPressed,
          ]}
        >
          <Text style={styles.arrowText}>›</Text>
        </Pressable>

        <View style={styles.footer}>
          <Text accessibilityLiveRegion="polite" style={styles.counter}>
            {items.length === 0 ? "0/0" : `${currentIndex + 1}/${items.length}`}
          </Text>
          <Text numberOfLines={3} style={styles.caption}>
            {currentItem?.caption ?? ""}
          </Text>
          <Pressable
            accessibilityLabel={
              mode === "browse-album"
                ? "Select photo and view alternatives"
                : "Use this photo"
            }
            accessibilityRole="button"
            disabled={!currentItem}
            onPress={handleSelect}
            style={({ pressed }) => [
              styles.select,
              pressed && styles.selectPressed,
              !currentItem && styles.controlDisabled,
            ]}
          >
            <Text style={styles.selectText}>Select</Text>
          </Pressable>
        </View>
      </View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  root: { backgroundColor: C.backdrop, flex: 1 },
  photoStage: {
    bottom: 178,
    left: 0,
    position: "absolute",
    right: 0,
    top: StatusBar.currentHeight ?? 0,
  },
  image: { height: "100%", width: "100%" },
  close: {
    alignItems: "center",
    backgroundColor: C.panel,
    borderRadius: 24,
    height: 44,
    justifyContent: "center",
    left: 16,
    position: "absolute",
    top: (StatusBar.currentHeight ?? 0) + 12,
    width: 44,
  },
  closeText: { color: C.text, fontSize: 19 },
  arrow: {
    alignItems: "center",
    backgroundColor: C.panel,
    borderRadius: 22,
    height: 44,
    justifyContent: "center",
    marginTop: -22,
    position: "absolute",
    top: "45%",
    width: 44,
  },
  arrowLeft: { left: 12 },
  arrowRight: { right: 12 },
  arrowText: { color: C.text, fontSize: 34, lineHeight: 36, marginTop: -3 },
  footer: {
    backgroundColor: C.panel,
    bottom: 0,
    left: 0,
    minHeight: 178,
    paddingBottom: 22,
    paddingHorizontal: 22,
    paddingTop: 14,
    position: "absolute",
    right: 0,
  },
  counter: { color: C.gold, fontSize: 12, letterSpacing: 1.2 },
  caption: { color: C.text, flex: 1, fontSize: 14, lineHeight: 20, marginTop: 7 },
  select: {
    alignItems: "center",
    alignSelf: "flex-end",
    backgroundColor: C.gold,
    borderRadius: 6,
    justifyContent: "center",
    minHeight: 42,
    minWidth: 104,
    paddingHorizontal: 22,
  },
  selectPressed: { opacity: 0.82, transform: [{ scale: 0.98 }] },
  selectText: { color: "#141311", fontSize: 15, fontWeight: "600" },
  controlPressed: { opacity: 0.7 },
  controlDisabled: { opacity: 0.24 },
});

export default Lightbox;
