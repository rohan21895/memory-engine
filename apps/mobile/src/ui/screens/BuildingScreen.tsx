import { useEffect, useMemo, useRef, useState } from "react";
import { Animated, StatusBar, StyleSheet, Text, View } from "react-native";

import { copy } from "../copy";
import { fonts } from "../fonts";
import { colors, continuousRadius, layout, radii, spacing, typeScale } from "../tokens";
import { ScreenHeader } from "../components/ScreenHeader";

export function BuildingScreen() {
  const [progress, setProgress] = useState(8);
  const pulse = useRef(new Animated.Value(0.45)).current;

  useEffect(() => {
    const timer = setInterval(() => {
      setProgress((current) => Math.min(92, current + (current < 55 ? 7 : 3)));
    }, 700);
    const animation = Animated.loop(
      Animated.sequence([
        Animated.timing(pulse, { duration: 900, toValue: 1, useNativeDriver: true }),
        Animated.timing(pulse, { duration: 900, toValue: 0.45, useNativeDriver: true }),
      ]),
    );
    animation.start();
    return () => {
      clearInterval(timer);
      animation.stop();
    };
  }, [pulse]);

  const stage = useMemo(
    () => Math.min(copy.building.stages.length - 1, Math.floor(progress / 25)),
    [progress],
  );

  return (
    <View style={styles.root}>
      <StatusBar backgroundColor={colors.background} barStyle="light-content" />
      <View style={styles.content}>
        <ScreenHeader helper={copy.building.helper} step={1} title={copy.building.title} />
        <View
          accessible
          accessibilityLabel={copy.building.accessibilityLabel(progress)}
          accessibilityRole="progressbar"
          accessibilityValue={{ min: 0, max: 100, now: progress }}
          style={styles.workArea}
        >
          <Animated.View style={[styles.album, { opacity: pulse }]}>
            <View style={[styles.page, styles.pageBack]} />
            <View style={styles.page}>
              <View style={styles.imagePlaceholder} />
              <View style={styles.captionLine} />
              <View style={[styles.captionLine, styles.captionShort]} />
            </View>
          </Animated.View>
          <Text accessibilityLiveRegion="polite" style={styles.stage}>{copy.building.stages[stage]}</Text>
          <View style={styles.progressTrack}>
            <View style={[styles.progressFill, { width: `${progress}%` }]} />
          </View>
          <Text style={styles.progressText}>{copy.building.progress(progress)}</Text>
        </View>
        <Text style={styles.trust}>{copy.trustCue} · {copy.states.safe}</Text>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  album: { height: 154, justifyContent: "center", width: 126 },
  captionLine: { backgroundColor: "#bcb4a5", height: 5, width: "80%" },
  captionShort: { width: "54%" },
  content: {
    flex: 1,
    justifyContent: "space-between",
    paddingBottom: spacing.xl,
    paddingHorizontal: layout.screenPadding,
    paddingTop: (StatusBar.currentHeight ?? 24) + spacing.lg,
  },
  imagePlaceholder: { backgroundColor: colors.panelRaised, flex: 1, width: "100%" },
  page: {
    ...continuousRadius(radii.sm),
    backgroundColor: colors.text,
    borderColor: colors.hairline,
    borderWidth: 1,
    gap: spacing.xs,
    height: 146,
    padding: spacing.sm,
    position: "absolute",
    transform: [{ rotate: "3deg" }],
    width: 112,
  },
  pageBack: { backgroundColor: colors.panel, transform: [{ rotate: "-5deg" }, { translateX: -8 }] },
  progressFill: { backgroundColor: colors.gold, height: "100%" },
  progressText: { color: colors.muted, fontFamily: fonts.body, ...typeScale.small },
  progressTrack: {
    ...continuousRadius(radii.pill),
    backgroundColor: colors.hairline,
    height: 8,
    overflow: "hidden",
    width: "100%",
  },
  root: { backgroundColor: colors.background, flex: 1 },
  stage: { color: colors.text, fontFamily: fonts.body, fontWeight: "700", textAlign: "center", ...typeScale.body },
  trust: { color: colors.muted, fontFamily: fonts.body, textAlign: "center", ...typeScale.small },
  workArea: { alignItems: "center", gap: spacing.md, paddingVertical: spacing.xl },
});
