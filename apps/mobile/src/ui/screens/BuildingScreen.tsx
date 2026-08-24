import { useEffect, useMemo, useState } from "react";
import { StatusBar, StyleSheet, Text, View } from "react-native";
import Animated, {
  Easing,
  ReduceMotion,
  useAnimatedStyle,
  useReducedMotion,
  useSharedValue,
  withRepeat,
  withTiming,
} from "react-native-reanimated";

import { fonts } from "../fonts";
import { colors, layout, radii, spacing, typeScale } from "../tokens";

const messages = [
  "Looking through your photos…",
  "Grouping photos by moment and place…",
  "Finding the people in your photos…",
  "Setting aside blurry and repeated shots…",
  "Choosing the sharpest of each moment…",
  "Putting your album in order…",
];

export function BuildingScreen() {
  const [progress, setProgress] = useState(7);
  const reducedMotion = useReducedMotion();
  const breath = useSharedValue(0);

  useEffect(() => {
    const timer = setInterval(() => {
      setProgress((current) => Math.min(94, current + (current < 55 ? 6 : 2)));
    }, 700);
    breath.set(withRepeat(
      withTiming(1, {
        duration: reducedMotion ? 1 : 1300,
        easing: Easing.bezier(0.77, 0, 0.175, 1),
        reduceMotion: ReduceMotion.System,
      }),
      -1,
      true,
    ));
    return () => clearInterval(timer);
  }, [breath, reducedMotion]);

  const stage = useMemo(
    () => Math.min(messages.length - 1, Math.floor(progress / (100 / messages.length))),
    [progress],
  );
  const breathingStyle = useAnimatedStyle(() => ({
    opacity: 0.84 + breath.get() * 0.16,
    transform: [{ scale: 1 + breath.get() * 0.06 }],
  }));

  return (
    <View style={styles.root}>
      <StatusBar backgroundColor={colors.background} barStyle="dark-content" />
      <View
        accessible
        accessibilityLabel={`Finding your best shots. ${progress}% finished.`}
        accessibilityRole="progressbar"
        accessibilityValue={{ min: 0, max: 100, now: progress }}
        style={styles.content}
      >
        <Animated.View style={[styles.breath, breathingStyle]}>
          <View style={styles.breathLight} />
        </Animated.View>
        <Text accessibilityRole="header" style={styles.title}>Finding your best shots</Text>
        <Text accessibilityLiveRegion="polite" style={styles.message}>{messages[stage]}</Text>
        <View style={styles.progressTrack}>
          <View style={[styles.progressFill, { width: `${progress}%` }]} />
        </View>
        <View style={styles.privacy}><View style={styles.dot} /><Text style={styles.privacyText}>All on your phone</Text></View>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  breath: { backgroundColor: "#d9a184", borderCurve: "continuous", borderRadius: 38, height: 132, overflow: "hidden", width: 132 },
  breathLight: { backgroundColor: "#f2d9c6", height: 88, left: -12, position: "absolute", top: -10, transform: [{ rotate: "-10deg" }], width: 160 },
  content: { alignItems: "center", flex: 1, gap: spacing.md, justifyContent: "center", paddingHorizontal: layout.screenPadding + 12 },
  dot: { backgroundColor: colors.success, borderRadius: 4, height: 8, width: 8 },
  message: { color: colors.muted, fontFamily: fonts.regular, minHeight: 46, textAlign: "center", ...typeScale.body },
  privacy: { alignItems: "center", flexDirection: "row", gap: spacing.xs, paddingTop: spacing.sm },
  privacyText: { color: "#5d7a62", fontFamily: fonts.regular, ...typeScale.small },
  progressFill: { backgroundColor: colors.gold, borderRadius: radii.pill, height: "100%" },
  progressTrack: { backgroundColor: "#eae5dc", borderRadius: radii.pill, height: 8, overflow: "hidden", width: "100%" },
  root: { backgroundColor: colors.background, flex: 1 },
  title: { color: colors.text, fontFamily: fonts.extraBold, fontSize: 26, letterSpacing: -0.6, lineHeight: 32, paddingTop: spacing.md, textAlign: "center" },
});
