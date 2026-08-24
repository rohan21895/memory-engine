import { useEffect } from "react";
import { Pressable, StatusBar, StyleSheet, Text, View } from "react-native";
import Animated, {
  cancelAnimation,
  Easing,
  ReduceMotion,
  useAnimatedStyle,
  useReducedMotion,
  useSharedValue,
  withRepeat,
  withTiming,
} from "react-native-reanimated";

import type { BuildAlbumProgress } from "../../build-album";
import { fonts } from "../fonts";
import { colors, layout, radii, spacing, typeScale } from "../tokens";

type Props = {
  progress: BuildAlbumProgress;
  onCancel: () => void;
};

export function BuildingScreen({ progress, onCancel }: Props) {
  const reducedMotion = useReducedMotion();
  const breath = useSharedValue(0);
  const fraction =
    progress.total > 0
      ? Math.max(0, Math.min(1, progress.done / progress.total))
      : 0;
  const percentage = Math.floor(fraction * 100);

  useEffect(() => {
    breath.set(
      reducedMotion
        ? 0
        : withRepeat(
            withTiming(1, {
              duration: 1300,
              easing: Easing.bezier(0.77, 0, 0.175, 1),
              reduceMotion: ReduceMotion.System,
            }),
            -1,
            true,
          ),
    );
    return () => cancelAnimation(breath);
  }, [breath, reducedMotion]);

  const breathingStyle = useAnimatedStyle(() => ({
    opacity: 0.84 + breath.get() * 0.16,
    transform: [{ scale: 1 + breath.get() * 0.06 }],
  }));

  return (
    <View style={styles.root}>
      <StatusBar backgroundColor={colors.background} barStyle="dark-content" />
      <View style={styles.header}>
        <Pressable
          accessibilityHint="Stops this build and returns to your selected photos."
          accessibilityLabel="Cancel album build"
          accessibilityRole="button"
          onPress={onCancel}
          style={({ pressed }) => [
            styles.cancelButton,
            pressed ? styles.cancelPressed : null,
          ]}
        >
          <Text style={styles.cancelLabel}>Cancel</Text>
        </Pressable>
      </View>
      <View style={styles.content}>
        <Animated.View style={[styles.breath, breathingStyle]}>
          <View style={styles.breathLight} />
        </Animated.View>
        <Text accessibilityRole="header" style={styles.title}>Finding your best shots</Text>
        <Text accessibilityLiveRegion="polite" style={styles.message}>{progress.phase}</Text>
        <View
          accessibilityLabel={`${progress.phase}. ${percentage}% finished.`}
          accessibilityRole="progressbar"
          accessibilityValue={{ min: 0, max: 100, now: percentage }}
          style={styles.progressTrack}
        >
          <View style={[styles.progressFill, { width: `${fraction * 100}%` }]} />
        </View>
        <Text style={styles.percentage}>{percentage}%</Text>
        <View style={styles.privacy}><View style={styles.dot} /><Text style={styles.privacyText}>All on your phone</Text></View>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  breath: { backgroundColor: "#d9a184", borderCurve: "continuous", borderRadius: 38, height: 132, overflow: "hidden", width: 132 },
  breathLight: { backgroundColor: "#f2d9c6", height: 88, left: -12, position: "absolute", top: -10, transform: [{ rotate: "-10deg" }], width: 160 },
  content: { alignItems: "center", flex: 1, gap: spacing.md, justifyContent: "center", paddingHorizontal: layout.screenPadding + 12 },
  cancelButton: { alignItems: "center", borderCurve: "continuous", borderRadius: radii.pill, justifyContent: "center", minHeight: 44, paddingHorizontal: spacing.md },
  cancelLabel: { color: colors.muted, fontFamily: fonts.semibold, ...typeScale.small },
  cancelPressed: { backgroundColor: colors.panel },
  dot: { backgroundColor: colors.success, borderRadius: 4, height: 8, width: 8 },
  header: { alignItems: "flex-start", left: 0, paddingHorizontal: spacing.sm, paddingTop: (StatusBar.currentHeight ?? 24) + spacing.xs, position: "absolute", right: 0, top: 0, zIndex: 1 },
  message: { color: colors.muted, fontFamily: fonts.regular, minHeight: 54, textAlign: "center", ...typeScale.body },
  percentage: { color: colors.muted, fontFamily: fonts.semibold, fontVariant: ["tabular-nums"], ...typeScale.small },
  privacy: { alignItems: "center", flexDirection: "row", gap: spacing.xs, paddingTop: spacing.sm },
  privacyText: { color: "#5d7a62", fontFamily: fonts.regular, ...typeScale.small },
  progressFill: { backgroundColor: colors.gold, borderRadius: radii.pill, height: "100%" },
  progressTrack: { backgroundColor: "#eae5dc", borderRadius: radii.pill, height: 8, overflow: "hidden", width: "100%" },
  root: { backgroundColor: colors.background, flex: 1 },
  title: { color: colors.text, fontFamily: fonts.extraBold, fontSize: 26, letterSpacing: -0.6, lineHeight: 32, paddingTop: spacing.md, textAlign: "center" },
});
