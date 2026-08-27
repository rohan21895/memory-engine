import { Image } from "expo-image";
import { useState } from "react";
import { ActivityIndicator, Pressable, ScrollView, StatusBar, StyleSheet, Text, View, useWindowDimensions } from "react-native";

import {
  contentUri,
  faceIndexStatus,
  getFaceIndexPerson,
  markNotSamePerson,
  markSamePerson,
  suggestedFaceMerges,
} from "../../faces/face-index";
import type { MergeSuggestion } from "../../faces/face-cluster";
import { fonts } from "../fonts";
import { colors, layout, radii, spacing, typeScale } from "../tokens";
import { coOccurrenceEvidence, faceMergeReviewPair, remainingFaceMergeSuggestions } from "./face-merge-review";

type ReviewPhase = "idle" | "loading" | "review" | "done" | "error";

function afterPaint(): Promise<void> {
  return new Promise((resolve) => {
    requestAnimationFrame(() => requestAnimationFrame(() => resolve()));
  });
}

export function FaceMergeReviewScreen({ onBack }: { onBack: () => void }) {
  const { width } = useWindowDimensions();
  const [phase, setPhase] = useState<ReviewPhase>("idle");
  const [suggestions, setSuggestions] = useState<MergeSuggestion[]>([]);
  const [answering, setAnswering] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  // Read ONCE, not per render. `faceIndexStatus` walks every observation and
  // every seen asset id -- 17,699 and 11,828 of them on the owner's library --
  // and this number only decorates the intro copy. Answering a pair re-renders
  // this screen, so a per-render call would pay that walk on every tap.
  const [personCount] = useState(() => faceIndexStatus().people);
  const pair = suggestions[0]
    ? faceMergeReviewPair(suggestions[0], getFaceIndexPerson)
    : undefined;
  const evidence = pair ? coOccurrenceEvidence(pair.suggestion) : undefined;
  const tileWidth = Math.max(132, Math.min(238, (width - layout.screenPadding * 2 - spacing.sm) / 2));

  const findMatches = async () => {
    if (phase === "loading") return;
    setPhase("loading");
    setNotice(null);
    setSuggestions([]);
    // Guarantee the progress state paints before the O(people^2) sweep owns
    // the JS thread. The sweep is never started by render or mount.
    await afterPaint();
    try {
      const found = await suggestedFaceMerges();
      setSuggestions(found);
      setPhase(found.length > 0 ? "review" : "done");
    } catch {
      setPhase("error");
    }
  };

  const answer = async (samePerson: boolean) => {
    if (!pair || answering) return;
    setAnswering(true);
    setNotice(null);
    const recorded = samePerson
      ? await markSamePerson(pair.suggestion.a, pair.suggestion.b)
      : await markNotSamePerson(pair.suggestion.a, pair.suggestion.b);
    setAnswering(false);
    if (!recorded) {
      // Reached only when no photo of one of them identifies them on its own:
      // not "they share every photo" any more, which face anchors now handle,
      // but "two people in one of these photos look too alike to tell apart".
      setNotice("In these photos we can’t tell which face is which, so we can’t remember that answer yet.");
      return;
    }
    const remaining = remainingFaceMergeSuggestions(suggestions, pair.suggestion, samePerson);
    setSuggestions(remaining);
    if (remaining.length === 0) setPhase("done");
    setNotice(samePerson ? "Combined. They’ll appear as one person." : "Noted. They’ll stay separate.");
  };

  return (
    <View style={styles.root}>
      <StatusBar backgroundColor={colors.background} barStyle="dark-content" />
      <ScrollView contentContainerStyle={styles.scroll} contentInsetAdjustmentBehavior="automatic">
        <Pressable accessibilityRole="button" onPress={onBack} style={styles.back}>
          <Text style={styles.backText}>‹ Photos</Text>
        </Pressable>
        <Text accessibilityRole="header" style={styles.title}>These might be the same person</Text>
        <Text style={styles.helper}>You decide. Nothing is combined until you say so, and every answer stays on this phone.</Text>

        {phase === "idle" ? (
          <View style={styles.intro}>
            <Text style={styles.introTitle}>Check your People groups</Text>
            <Text style={styles.introText}>We’ll compare {personCount.toLocaleString()} groups. On a large library this can take around 15 seconds.</Text>
            <Pressable accessibilityRole="button" onPress={() => void findMatches()} style={styles.primaryAction}>
              <Text style={styles.primaryActionText}>Find possible matches</Text>
            </Pressable>
          </View>
        ) : null}

        {phase === "loading" ? (
          <View accessibilityLiveRegion="polite" style={styles.progress}>
            <ActivityIndicator color={colors.gold} size="large" />
            <Text style={styles.progressTitle}>Checking every group…</Text>
            <Text style={styles.progressText}>This comparison stays on your phone. Keep this screen open for a moment.</Text>
          </View>
        ) : null}

        {phase === "review" && pair ? (
          <View style={styles.review}>
            <View style={styles.pairRow}>
              {[pair.first, pair.second].map((person, index) => (
                <View key={person.id} style={[styles.personTile, { width: tileWidth }]}>
                  <Image
                    accessibilityLabel={`Possible match ${index + 1}`}
                    cachePolicy="memory-disk"
                    contentFit="cover"
                    source={person.faceThumbUri ?? contentUri(person.coverAssetId)}
                    style={[styles.face, { height: tileWidth, width: tileWidth }]}
                  />
                  <Text style={styles.faceCount}>{person.faceCount.toLocaleString()} {person.faceCount === 1 ? "face" : "faces"}</Text>
                </View>
              ))}
            </View>
            <View style={styles.sharedNote}>
              <Text style={styles.sharedCount}>{pair.suggestion.sharedAssets.toLocaleString()}</Text>
              <Text style={styles.sharedText}>{pair.suggestion.sharedAssets === 1 ? "photo has both groups" : "photos have both groups"}</Text>
            </View>
            {/* The evidence being overruled. A shared photo is the ONLY reason
                these two are still apart, and the count alone cannot be acted
                on -- one photo out of four hundred means the opposite of one
                out of two. */}
            {evidence ? <Text style={styles.evidence}>{evidence}</Text> : null}
            <Text style={styles.question}>Are these the same person?</Text>
            <View style={styles.actions}>
              <Pressable
                accessibilityRole="button"
                accessibilityState={{ busy: answering, disabled: answering }}
                disabled={answering}
                onPress={() => void answer(true)}
                style={[styles.answer, styles.same, answering ? styles.disabled : null]}
              >
                <Text style={styles.sameText}>Same person</Text>
              </Pressable>
              <Pressable
                accessibilityRole="button"
                accessibilityState={{ busy: answering, disabled: answering }}
                disabled={answering}
                onPress={() => void answer(false)}
                style={[styles.answer, answering ? styles.disabled : null]}
              >
                <Text style={styles.answerText}>Not the same</Text>
              </Pressable>
            </View>
            <Text style={styles.remaining}>{suggestions.length.toLocaleString()} possible {suggestions.length === 1 ? "match" : "matches"} in this review</Text>
          </View>
        ) : null}

        {phase === "review" && !pair ? (
          <View style={styles.progress}>
            <Text style={styles.progressTitle}>That group changed</Text>
            <Text style={styles.progressText}>Run the check again to use the latest People groups.</Text>
            <Pressable accessibilityRole="button" onPress={() => void findMatches()} style={styles.secondaryAction}>
              <Text style={styles.secondaryActionText}>Check again</Text>
            </Pressable>
          </View>
        ) : null}

        {phase === "done" ? (
          <View style={styles.progress}>
            <Text style={styles.progressTitle}>You’re caught up</Text>
            <Text style={styles.progressText}>There are no more likely matches in this pass.</Text>
            <Pressable accessibilityRole="button" onPress={() => void findMatches()} style={styles.secondaryAction}>
              <Text style={styles.secondaryActionText}>Check again</Text>
            </Pressable>
          </View>
        ) : null}

        {phase === "error" ? (
          <View accessibilityLiveRegion="polite" style={styles.progress}>
            <Text style={styles.progressTitle}>We couldn’t finish the check</Text>
            <Text style={styles.progressText}>Your People groups were not changed. Try again when you’re ready.</Text>
            <Pressable accessibilityRole="button" onPress={() => void findMatches()} style={styles.secondaryAction}>
              <Text style={styles.secondaryActionText}>Try again</Text>
            </Pressable>
          </View>
        ) : null}

        {notice ? <Text accessibilityLiveRegion="polite" style={styles.notice}>{notice}</Text> : null}
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  actions: { flexDirection: "row", gap: spacing.xs },
  answer: { alignItems: "center", backgroundColor: colors.panel, borderColor: colors.hairline, borderCurve: "continuous", borderRadius: radii.md, borderWidth: 1, flex: 1, justifyContent: "center", minHeight: 54, paddingHorizontal: spacing.sm },
  answerText: { color: colors.text, fontFamily: fonts.semibold, ...typeScale.small },
  back: { alignSelf: "flex-start", justifyContent: "center", minHeight: layout.minTouchTarget },
  backText: { color: colors.muted, fontFamily: fonts.semibold, ...typeScale.small },
  disabled: { opacity: 0.42 },
  face: { backgroundColor: colors.quietSurface, borderCurve: "continuous", borderRadius: radii.lg },
  faceCount: { color: colors.text, fontFamily: fonts.bold, textAlign: "center", ...typeScale.small },
  evidence: { color: colors.muted, fontFamily: fonts.regular, maxWidth: layout.maxReadableWidth, textAlign: "center", ...typeScale.small },
  helper: { color: colors.muted, fontFamily: fonts.regular, maxWidth: layout.maxReadableWidth, ...typeScale.body },
  intro: { backgroundColor: colors.panel, borderColor: colors.hairline, borderCurve: "continuous", borderRadius: radii.lg, borderWidth: 1, gap: spacing.xs, padding: spacing.lg },
  introText: { color: colors.muted, fontFamily: fonts.regular, ...typeScale.body },
  introTitle: { color: colors.text, fontFamily: fonts.bold, ...typeScale.subtitle },
  notice: { color: colors.goldPressed, fontFamily: fonts.medium, textAlign: "center", ...typeScale.small },
  pairRow: { flexDirection: "row", gap: spacing.sm, justifyContent: "center" },
  personTile: { flex: 1, gap: spacing.xs, maxWidth: 238 },
  primaryAction: { alignItems: "center", alignSelf: "flex-start", backgroundColor: colors.gold, borderCurve: "continuous", borderRadius: radii.pill, justifyContent: "center", marginTop: spacing.sm, minHeight: layout.primaryButtonHeight, paddingHorizontal: spacing.lg },
  primaryActionText: { color: colors.onAccent, fontFamily: fonts.bold, ...typeScale.label },
  progress: { alignItems: "center", gap: spacing.xs, minHeight: 260, justifyContent: "center", paddingHorizontal: spacing.lg },
  progressText: { color: colors.muted, fontFamily: fonts.regular, maxWidth: 360, textAlign: "center", ...typeScale.body },
  progressTitle: { color: colors.text, fontFamily: fonts.bold, textAlign: "center", ...typeScale.subtitle },
  question: { color: colors.text, fontFamily: fonts.extraBold, textAlign: "center", ...typeScale.subtitle },
  remaining: { color: colors.muted, fontFamily: fonts.regular, textAlign: "center", ...typeScale.eyebrow },
  review: { gap: spacing.lg },
  root: { backgroundColor: colors.background, flex: 1 },
  same: { backgroundColor: colors.gold, borderColor: colors.gold },
  sameText: { color: colors.onAccent, fontFamily: fonts.bold, ...typeScale.small },
  scroll: { gap: spacing.lg, paddingBottom: spacing.xxl, paddingHorizontal: layout.screenPadding, paddingTop: (StatusBar.currentHeight ?? 24) + spacing.xs },
  secondaryAction: { alignItems: "center", backgroundColor: colors.panel, borderColor: colors.hairline, borderCurve: "continuous", borderRadius: radii.pill, borderWidth: 1, justifyContent: "center", marginTop: spacing.sm, minHeight: layout.minTouchTarget, paddingHorizontal: spacing.lg },
  secondaryActionText: { color: colors.goldPressed, fontFamily: fonts.bold, ...typeScale.small },
  sharedCount: { color: colors.goldPressed, fontFamily: fonts.extraBold, fontVariant: ["tabular-nums"], ...typeScale.subtitle },
  sharedNote: { alignItems: "center", alignSelf: "center", backgroundColor: colors.panelRaised, borderCurve: "continuous", borderRadius: radii.pill, flexDirection: "row", gap: spacing.xs, minHeight: layout.minTouchTarget, paddingHorizontal: spacing.md },
  sharedText: { color: colors.text, fontFamily: fonts.semibold, ...typeScale.small },
  title: { color: colors.text, fontFamily: fonts.extraBold, maxWidth: 520, ...typeScale.title },
});
