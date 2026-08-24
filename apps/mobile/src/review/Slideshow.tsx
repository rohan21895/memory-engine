import { Image } from "expo-image";
import { useEffect, useState } from "react";
import { Pressable, StatusBar, StyleSheet, Text, View } from "react-native";
import Animated, {
  Easing,
  ReduceMotion,
  interpolate,
  useAnimatedStyle,
  useReducedMotion,
  useSharedValue,
  withTiming,
} from "react-native-reanimated";

import type { SavedAlbum } from "../albums/album-store";
import { colors, fonts, spacing, typeScale } from "../ui";

const speeds = [
  { label: "Gentle", ms: 5200 },
  { label: "Normal", ms: 3400 },
  { label: "Lively", ms: 2200 },
] as const;

export function Slideshow({ album, onBack }: { album: SavedAlbum; onBack: () => void }) {
  const [index, setIndex] = useState(0);
  const [playing, setPlaying] = useState(true);
  const [speed, setSpeed] = useState(3400);
  const reducedMotion = useReducedMotion();
  const drift = useSharedValue(0);
  const count = album.photos.length;

  useEffect(() => {
    drift.set(0);
    drift.set(withTiming(1, {
      duration: reducedMotion ? 250 : speed,
      easing: Easing.linear,
      reduceMotion: ReduceMotion.System,
    }));
  }, [drift, index, reducedMotion, speed]);

  useEffect(() => {
    if (!playing || count < 2) return;
    const timer = setInterval(() => setIndex((current) => (current + 1) % count), speed);
    return () => clearInterval(timer);
  }, [count, playing, speed]);

  const imageStyle = useAnimatedStyle(() => ({
    opacity: interpolate(drift.get(), [0, 0.08, 1], [0.25, 1, 1]),
    transform: reducedMotion ? [] : [
      { scale: interpolate(drift.get(), [0, 1], [1.02, 1.16]) },
      { translateX: interpolate(drift.get(), [0, 1], [0, -10]) },
      { translateY: interpolate(drift.get(), [0, 1], [0, -8]) },
    ],
  }));

  const previous = () => setIndex((current) => (current - 1 + Math.max(1, count)) % Math.max(1, count));
  const next = () => setIndex((current) => (current + 1) % Math.max(1, count));
  const current = album.photos[index];

  return (
    <View style={styles.root}>
      <StatusBar backgroundColor="transparent" barStyle="light-content" translucent />
      <Animated.View style={[StyleSheet.absoluteFill, imageStyle]}>
        {current ? <Image cachePolicy="memory-disk" contentFit="cover" source={current.uri} style={StyleSheet.absoluteFill} transition={0} /> : null}
      </Animated.View>
      <View style={styles.scrim} />
      <Pressable accessibilityLabel="Back to album" accessibilityRole="button" onPress={onBack} style={styles.back}><Text style={styles.backText}>‹</Text></Pressable>
      <View style={styles.controls}>
        <Text style={styles.title}>{album.title}</Text>
        <Text style={styles.meta}>{count > 0 ? `Photo ${index + 1} of ${count}` : "No photos"}</Text>
        <View style={styles.dots}>
          {album.photos.map((photo, dotIndex) => <View key={`${photo.media_id}-${dotIndex}`} style={[styles.dot, dotIndex <= index ? styles.dotActive : null]} />)}
        </View>
        <View style={styles.player}>
          <Pressable accessibilityLabel="Previous photo" onPress={previous} style={styles.circle}><Text style={styles.circleText}>‹</Text></Pressable>
          <Pressable accessibilityRole="button" onPress={() => setPlaying((value) => !value)} style={styles.play}><Text style={styles.playText}>{playing ? "Pause" : "Play"}</Text></Pressable>
          <Pressable accessibilityLabel="Next photo" onPress={next} style={styles.circle}><Text style={styles.circleText}>›</Text></Pressable>
        </View>
        <View style={styles.speedRow}>
          {speeds.map((option) => {
            const active = option.ms === speed;
            return (
              <Pressable key={option.label} onPress={() => setSpeed(option.ms)} style={[styles.speed, active ? styles.speedActive : null]}>
                <Text style={[styles.speedText, active ? styles.speedTextActive : null]}>{option.label}</Text>
              </Pressable>
            );
          })}
        </View>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  back: { alignItems: "center", backgroundColor: "rgba(255,255,255,.22)", borderRadius: 20, height: 40, justifyContent: "center", left: spacing.md, position: "absolute", top: (StatusBar.currentHeight ?? 24) + spacing.xs, width: 40 },
  backText: { color: colors.onAccent, fontFamily: fonts.regular, fontSize: 28 },
  circle: { alignItems: "center", backgroundColor: "rgba(255,255,255,.16)", borderRadius: 26, height: 52, justifyContent: "center", width: 52 },
  circleText: { color: colors.onAccent, fontFamily: fonts.regular, fontSize: 24 },
  controls: { bottom: 0, left: 0, paddingBottom: spacing.xl, paddingHorizontal: spacing.lg, position: "absolute", right: 0 },
  dot: { backgroundColor: "rgba(255,255,255,.28)", borderRadius: 2, flex: 1, height: 3 },
  dotActive: { backgroundColor: "rgba(255,255,255,.92)" },
  dots: { flexDirection: "row", gap: spacing.xxs, paddingTop: 18 },
  meta: { color: "rgba(255,255,255,.78)", fontFamily: fonts.regular, ...typeScale.small },
  play: { alignItems: "center", backgroundColor: "rgba(255,255,255,.92)", borderRadius: 26, flex: 1, height: 52, justifyContent: "center" },
  player: { alignItems: "center", flexDirection: "row", gap: spacing.sm, paddingTop: 20 },
  playText: { color: colors.text, fontFamily: fonts.bold, ...typeScale.label },
  root: { backgroundColor: "#171310", flex: 1, overflow: "hidden" },
  scrim: { backgroundColor: "rgba(15,11,8,.5)", bottom: 0, height: "50%", left: 0, position: "absolute", right: 0 },
  speed: { alignItems: "center", borderColor: "rgba(255,255,255,.26)", borderRadius: 20, borderWidth: 1, flex: 1, height: 40, justifyContent: "center" },
  speedActive: { backgroundColor: "rgba(255,255,255,.92)", borderColor: "rgba(255,255,255,.92)" },
  speedRow: { flexDirection: "row", gap: spacing.xs, paddingTop: spacing.sm },
  speedText: { color: colors.onAccent, fontFamily: fonts.bold, fontSize: 13.5 },
  speedTextActive: { color: colors.text },
  title: { color: colors.onAccent, fontFamily: fonts.extraBold, fontSize: 26, letterSpacing: -0.7 },
});
