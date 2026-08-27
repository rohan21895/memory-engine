import { Image } from "expo-image";
import { useState } from "react";
import { ActivityIndicator, Pressable, ScrollView, StatusBar, StyleSheet, Text, View, useWindowDimensions } from "react-native";

import {
  contentUri,
  faceIndexStatus,
  getFaceIndexPerson,
  cachedFaceMergeSuggestions,
  markNotSamePerson,
  markSamePerson,
  suggestedFaceMerges,
  undoLastFaceConstraint,
} from "../../faces/face-index";
import type { MergeSuggestion } from "../../faces/face-cluster";
import { fonts } from "../fonts";
import { colors, layout, radii, spacing, typeScale } from "../tokens";
import {
  advanceFaceMergeReviewProgress,
  coOccurrenceEvidence,
  faceMergeReviewPair,
  remainingFaceMergeSuggestions,
  soleSharedPhoto,
  type FaceMergeReviewProgress,
} from "./face-merge-review";

type ReviewPhase = "idle" | "loading" | "review" | "done" | "error";
type LastAnswer = {
  progressBefore: FaceMergeReviewProgress;
  samePerson: boolean;
  suggestionsBefore: MergeSuggestion[];
};

const EMPTY_PROGRESS: FaceMergeReviewProgress = {
  answered: 0,
  photosRepaired: 0,
};

function afterPaint(): Promise<void> {
  return new Promise((resolve) => {
    requestAnimationFrame(() => requestAnimationFrame(() => resolve()));
  });
}

export function FaceMergeReviewScreen({ onBack }: { onBack: () => void }) {
  const { width } = useWindowDimensions();
  // Work already done is not announced again. If the stored queue is still
  // valid this opens straight on a question -- no intro card, no "Find possible
  // matches" tap, no fifteen-second warning for something that will not take
  // fifteen seconds. Reading it costs no observations parse and no sweep, so it
  // is safe during render; `useState` keeps it to once per mount.
  const [restored] = useState(() => cachedFaceMergeSuggestions());
  const [phase, setPhase] = useState<ReviewPhase>(
    restored && restored.length > 0 ? "review" : "idle",
  );
  const [suggestions, setSuggestions] = useState<MergeSuggestion[]>(
    restored ?? [],
  );
  const [answering, setAnswering] = useState(false);
  const [undoing, setUndoing] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [lastAnswer, setLastAnswer] = useState<LastAnswer | null>(null);
  const [progress, setProgress] = useState<FaceMergeReviewProgress>(EMPTY_PROGRESS);
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
  // When both tiles are one face from one shared photo, comparing the crops is
  // impossible by construction -- see `soleSharedPhoto`. Show the photograph
  // and ask about the photograph instead.
  const onePhoto = pair ? soleSharedPhoto(pair) : undefined;
  const photoWidth = width - layout.screenPadding * 2;

  const findMatches = async () => {
    if (phase === "loading") return;
    setPhase("loading");
    setNotice(null);
    setSuggestions([]);
    setLastAnswer(null);
    setProgress(EMPTY_PROGRESS);
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
    if (!pair || answering || undoing) return;
    setAnswering(true);
    setNotice(null);
    let recorded = false;
    try {
      recorded = samePerson
        ? await markSamePerson(pair.suggestion.a, pair.suggestion.b)
        : await markNotSamePerson(pair.suggestion.a, pair.suggestion.b);
    } catch {
      setNotice("That answer wasn’t saved. Nothing changed, so please try again.");
      return;
    } finally {
      setAnswering(false);
    }
    if (!recorded) {
      // Reached only when no photo of one of them identifies them on its own:
      // not "they share every photo" any more, which face anchors now handle,
      // but "two people in one of these photos look too alike to tell apart".
      setNotice("In these photos we can’t tell which face is which, so we can’t remember that answer yet.");
      return;
    }
    setLastAnswer({
      progressBefore: progress,
      samePerson,
      suggestionsBefore: suggestions,
    });
    setProgress(advanceFaceMergeReviewProgress(progress, pair.suggestion, samePerson));
    const remaining = remainingFaceMergeSuggestions(suggestions, pair.suggestion, samePerson);
    setSuggestions(remaining);
    if (remaining.length === 0) setPhase("done");
    setNotice(
      samePerson
        ? `${pair.suggestion.photosFixed.toLocaleString()} photos brought together. They’ll appear as one person.`
        : "Kept separate. This answer is saved.",
    );
  };

  const undoLastAnswer = async () => {
    if (!lastAnswer || answering || undoing) return;
    setUndoing(true);
    setNotice("Undoing your last answer…");
    // The safe undo for a merge rebuilds People from the saved face records.
    // Paint the feedback before that synchronous work owns the JS thread.
    await afterPaint();
    let undone = false;
    try {
      undone = await undoLastFaceConstraint();
    } catch {
      setNotice("We couldn’t undo that answer. It is still saved.");
      setUndoing(false);
      return;
    }
    if (!undone) {
      setNotice("There was no saved answer to undo.");
      setUndoing(false);
      return;
    }

    setProgress(lastAnswer.progressBefore);
    setLastAnswer(null);
    try {
      const restored = lastAnswer.samePerson
        ? await suggestedFaceMerges()
        : lastAnswer.suggestionsBefore;
      setSuggestions(restored);
      setPhase(restored.length > 0 ? "review" : "done");
      setNotice("Last answer undone. Nothing from it will be remembered.");
    } catch {
      // The judgement is already gone. Never claim it survived merely because
      // refreshing the questions failed after the safe rebuild completed.
      setSuggestions([]);
      setPhase("review");
      setNotice("Last answer undone. Check again to refresh the remaining questions.");
    } finally {
      setUndoing(false);
    }
  };

  return (
    <View style={styles.root}>
      <StatusBar backgroundColor={colors.background} barStyle="dark-content" />
      <ScrollView contentContainerStyle={styles.scroll} contentInsetAdjustmentBehavior="automatic">
        <Pressable accessibilityRole="button" onPress={onBack} style={styles.back}>
          <Text style={styles.backText}>‹ Photos</Text>
        </Pressable>
        <Text accessibilityRole="header" style={styles.title}>These might be the same person</Text>
        <Text style={styles.helper}>You decide. Nothing is combined until you say so. Answers stay on this phone, and you can undo the last one.</Text>

        {(phase === "review" || phase === "done") ? (
          <View style={styles.reviewStatus}>
            <View style={styles.statRow}>
              <View style={styles.stat}>
                <Text style={styles.statNumber}>{suggestions.length.toLocaleString()}</Text>
                <Text style={styles.statLabel}>left</Text>
              </View>
              <View style={styles.stat}>
                <Text style={styles.statNumber}>{progress.answered.toLocaleString()}</Text>
                <Text style={styles.statLabel}>answered</Text>
              </View>
              <View style={styles.stat}>
                <Text style={styles.statNumber}>{progress.photosRepaired.toLocaleString()}</Text>
                <Text style={styles.statLabel}>photos brought together</Text>
              </View>
            </View>
            <Text style={styles.stopNote}>Stop whenever you like. Each answer is saved as you go.</Text>
          </View>
        ) : null}

        {notice ? (
          <View accessibilityLiveRegion="polite" style={styles.noticeBar}>
            <Text style={styles.notice}>{notice}</Text>
            {lastAnswer ? (
              <Pressable
                accessibilityRole="button"
                accessibilityState={{ busy: undoing, disabled: answering || undoing }}
                disabled={answering || undoing}
                onPress={() => void undoLastAnswer()}
                style={styles.undoAction}
              >
                <Text style={styles.undoText}>{undoing ? "Undoing…" : "Undo last answer"}</Text>
              </Pressable>
            ) : null}
          </View>
        ) : null}

        {phase === "idle" ? (
          <View style={styles.intro}>
            <Text style={styles.introTitle}>Check your People</Text>
            <Text style={styles.introText}>We’ll compare {personCount.toLocaleString()} entries in People. On a large library this can take around 15 seconds.</Text>
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
            {onePhoto ? (
              <Image
                accessibilityLabel="The photo both of these came from"
                cachePolicy="memory-disk"
                contentFit="contain"
                source={contentUri(onePhoto)}
                style={[styles.sourcePhoto, { height: photoWidth, width: photoWidth }]}
              />
            ) : (
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
            )}
            {onePhoto ? (
              // No repair count and no rate here. Both are always "1" for this
              // shape, so they add a number without adding information, and the
              // photograph above is the whole of the evidence.
              <Text style={styles.evidence}>
                We found two faces in this photo. If only one person is in it, we
                counted the same face twice.
              </Text>
            ) : (
              <>
                <View style={styles.repairNote}>
                  <Text style={styles.repairCount}>{pair.suggestion.photosFixed.toLocaleString()}</Text>
                  <Text style={styles.repairText}>{pair.suggestion.photosFixed === 1 ? "photo would come together" : "photos would come together"} if you choose Same person</Text>
                </View>
                {/* The evidence being overruled. A shared photo is the ONLY reason
                    these two are still apart, and the count alone cannot be acted
                    on -- one photo out of four hundred means the opposite of one
                    out of two. */}
                {evidence ? <Text style={styles.evidence}>{evidence}</Text> : null}
              </>
            )}
            <Text style={styles.question}>
              {onePhoto ? "How many people are in this photo?" : "Are these the same person?"}
            </Text>
            <View style={styles.actions}>
              {/* The cautious answer stays first in both framings: it is the one
                  that changes nothing. For the photo question that is "more than
                  one", which keeps the two records apart. */}
              <Pressable
                accessibilityRole="button"
                accessibilityState={{ busy: answering, disabled: answering || undoing }}
                disabled={answering || undoing}
                onPress={() => void answer(false)}
                style={[styles.answer, styles.notSame, answering || undoing ? styles.disabled : null]}
              >
                <Text style={styles.notSameText}>{onePhoto ? "More than one" : "Not the same"}</Text>
              </Pressable>
              <Pressable
                accessibilityRole="button"
                accessibilityState={{ busy: answering, disabled: answering || undoing }}
                disabled={answering || undoing}
                onPress={() => void answer(true)}
                style={[styles.answer, answering || undoing ? styles.disabled : null]}
              >
                <Text style={styles.answerText}>{onePhoto ? "Just one person" : "Same person"}</Text>
              </Pressable>
            </View>
            <Text style={styles.safeHint}>
              {onePhoto
                ? "Look at the photo, not the faces we cut out of it."
                : "Choose Same person only when you’re sure."}
            </Text>
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

      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  actions: { gap: spacing.xs },
  answer: { alignItems: "center", backgroundColor: colors.panel, borderColor: colors.hairline, borderCurve: "continuous", borderRadius: radii.md, borderWidth: 1, flex: 1, justifyContent: "center", minHeight: 54, paddingHorizontal: spacing.sm },
  answerText: { color: colors.text, fontFamily: fonts.semibold, ...typeScale.small },
  back: { alignSelf: "flex-start", justifyContent: "center", minHeight: layout.minTouchTarget },
  backText: { color: colors.muted, fontFamily: fonts.semibold, ...typeScale.small },
  disabled: { opacity: 0.42 },
  face: { backgroundColor: colors.quietSurface, borderCurve: "continuous", borderRadius: radii.lg },
  // `contain`, not `cover`: this is the evidence, so the whole frame must be
  // visible. A crop could hide the second person the question is asking about.
  sourcePhoto: { backgroundColor: colors.quietSurface, borderCurve: "continuous", borderRadius: radii.lg },
  faceCount: { color: colors.text, fontFamily: fonts.bold, textAlign: "center", ...typeScale.small },
  evidence: { color: colors.muted, fontFamily: fonts.regular, maxWidth: layout.maxReadableWidth, textAlign: "center", ...typeScale.small },
  helper: { color: colors.muted, fontFamily: fonts.regular, maxWidth: layout.maxReadableWidth, ...typeScale.body },
  intro: { backgroundColor: colors.panel, borderColor: colors.hairline, borderCurve: "continuous", borderRadius: radii.lg, borderWidth: 1, gap: spacing.xs, padding: spacing.lg },
  introText: { color: colors.muted, fontFamily: fonts.regular, ...typeScale.body },
  introTitle: { color: colors.text, fontFamily: fonts.bold, ...typeScale.subtitle },
  notice: { color: colors.goldPressed, fontFamily: fonts.medium, textAlign: "center", ...typeScale.small },
  noticeBar: { alignItems: "center", backgroundColor: colors.panelRaised, borderCurve: "continuous", borderRadius: radii.md, gap: spacing.xs, padding: spacing.md },
  notSame: { backgroundColor: colors.gold, borderColor: colors.gold },
  notSameText: { color: colors.onAccent, fontFamily: fonts.bold, ...typeScale.small },
  pairRow: { flexDirection: "row", gap: spacing.sm, justifyContent: "center" },
  personTile: { flex: 1, gap: spacing.xs, maxWidth: 238 },
  primaryAction: { alignItems: "center", alignSelf: "flex-start", backgroundColor: colors.gold, borderCurve: "continuous", borderRadius: radii.pill, justifyContent: "center", marginTop: spacing.sm, minHeight: layout.primaryButtonHeight, paddingHorizontal: spacing.lg },
  primaryActionText: { color: colors.onAccent, fontFamily: fonts.bold, ...typeScale.label },
  progress: { alignItems: "center", gap: spacing.xs, minHeight: 260, justifyContent: "center", paddingHorizontal: spacing.lg },
  progressText: { color: colors.muted, fontFamily: fonts.regular, maxWidth: 360, textAlign: "center", ...typeScale.body },
  progressTitle: { color: colors.text, fontFamily: fonts.bold, textAlign: "center", ...typeScale.subtitle },
  question: { color: colors.text, fontFamily: fonts.extraBold, textAlign: "center", ...typeScale.subtitle },
  repairCount: { color: colors.goldPressed, fontFamily: fonts.extraBold, fontVariant: ["tabular-nums"], ...typeScale.subtitle },
  repairNote: { alignItems: "center", alignSelf: "center", backgroundColor: colors.panelRaised, borderCurve: "continuous", borderRadius: radii.pill, flexDirection: "row", gap: spacing.xs, minHeight: layout.minTouchTarget, paddingHorizontal: spacing.md },
  repairText: { color: colors.text, flexShrink: 1, fontFamily: fonts.semibold, ...typeScale.small },
  review: { gap: spacing.lg },
  reviewStatus: { backgroundColor: colors.panel, borderColor: colors.hairline, borderCurve: "continuous", borderRadius: radii.lg, borderWidth: 1, gap: spacing.sm, padding: spacing.md },
  root: { backgroundColor: colors.background, flex: 1 },
  safeHint: { color: colors.muted, fontFamily: fonts.regular, textAlign: "center", ...typeScale.eyebrow },
  scroll: { gap: spacing.lg, paddingBottom: spacing.xxl, paddingHorizontal: layout.screenPadding, paddingTop: (StatusBar.currentHeight ?? 24) + spacing.xs },
  secondaryAction: { alignItems: "center", backgroundColor: colors.panel, borderColor: colors.hairline, borderCurve: "continuous", borderRadius: radii.pill, borderWidth: 1, justifyContent: "center", marginTop: spacing.sm, minHeight: layout.minTouchTarget, paddingHorizontal: spacing.lg },
  secondaryActionText: { color: colors.goldPressed, fontFamily: fonts.bold, ...typeScale.small },
  stat: { alignItems: "center", flex: 1, gap: 2 },
  statLabel: { color: colors.muted, fontFamily: fonts.regular, textAlign: "center", ...typeScale.eyebrow },
  statNumber: { color: colors.text, fontFamily: fonts.extraBold, fontVariant: ["tabular-nums"], ...typeScale.subtitle },
  statRow: { flexDirection: "row", gap: spacing.xs },
  stopNote: { color: colors.muted, fontFamily: fonts.regular, textAlign: "center", ...typeScale.small },
  title: { color: colors.text, fontFamily: fonts.extraBold, maxWidth: 520, ...typeScale.title },
  undoAction: { justifyContent: "center", minHeight: layout.minTouchTarget, paddingHorizontal: spacing.sm },
  undoText: { color: colors.goldPressed, fontFamily: fonts.bold, textDecorationLine: "underline", ...typeScale.small },
});
