import { useState } from "react";
import { Modal, Pressable, ScrollView, StatusBar, StyleSheet, Text, View } from "react-native";

import { copy } from "../copy";
import { fonts } from "../fonts";
import { colors, continuousRadius, layout, radii, spacing, typeScale } from "../tokens";
import { Card } from "../components/Card";
import { ErrorState } from "../components/ErrorState";
import { PrimaryButton } from "../components/PrimaryButton";
import { ScreenHeader } from "../components/ScreenHeader";
import { SecondaryButton } from "../components/SecondaryButton";

export type StartMessage = { kind: "error" | "info"; text: string; title?: string };

export function StartScreen({
  onChoosePhotos,
  onChooseFolder,
  onChooseGoogle,
  onDismissMessage,
  googleConfigured,
  busy,
  message,
}: {
  onChoosePhotos: () => void;
  onChooseFolder: () => void;
  onChooseGoogle: () => void;
  onDismissMessage: () => void;
  googleConfigured: boolean;
  busy: boolean;
  message: StartMessage | null;
}) {
  const [showOtherWays, setShowOtherWays] = useState(false);

  const chooseFolder = () => {
    setShowOtherWays(false);
    onChooseFolder();
  };
  const chooseGoogle = () => {
    setShowOtherWays(false);
    onChooseGoogle();
  };

  return (
    <View style={styles.root}>
      <StatusBar backgroundColor={colors.background} barStyle="light-content" />
      <ScrollView
        contentContainerStyle={styles.scroll}
        contentInsetAdjustmentBehavior="automatic"
        showsVerticalScrollIndicator={false}
      >
        <Text style={styles.trust}>{copy.trustCue}</Text>
        <ScreenHeader helper={copy.start.helper} step={1} title={copy.start.title} />

        <View accessible accessibilityLabel={copy.privacyShort} style={styles.albumSketch}>
          <View style={[styles.paper, styles.paperBack]} />
          <View style={[styles.paper, styles.paperFront]}>
            <View style={styles.photoWindow}><Text style={styles.photoMark}>✦</Text></View>
            <View style={styles.paperLine} />
            <View style={[styles.paperLine, styles.paperLineShort]} />
          </View>
        </View>

        {message ? (
          <Card style={styles.messageCard}>
            {message.kind === "error" ? (
              <ErrorState
                actionHint={copy.start.dismissMessage}
                actionLabel={copy.common.close}
                helper={message.text}
                onAction={onDismissMessage}
                title={message.title ?? copy.start.pickerError}
              />
            ) : (
              <View accessibilityLiveRegion="polite" style={styles.infoMessage}>
                <Text style={styles.infoText}>{message.text}</Text>
                <SecondaryButton
                  accessibilityHint={copy.start.dismissMessage}
                  label={copy.common.close}
                  onPress={onDismissMessage}
                  quiet
                />
              </View>
            )}
          </Card>
        ) : null}

        <View style={styles.actions}>
          <PrimaryButton
            accessibilityHint={copy.start.actionHint}
            busy={busy}
            label={busy ? copy.start.opening : copy.start.action}
            onPress={onChoosePhotos}
          />
          <SecondaryButton
            accessibilityHint={copy.start.otherWaysHint}
            disabled={busy}
            label={copy.start.otherWays}
            onPress={() => setShowOtherWays(true)}
            quiet
          />
          <Text style={styles.privacy}>{copy.privacyShort}</Text>
        </View>
      </ScrollView>

      <Modal
        animationType="slide"
        onRequestClose={() => setShowOtherWays(false)}
        presentationStyle="formSheet"
        visible={showOtherWays}
      >
        <View accessibilityViewIsModal style={styles.sheet}>
          <View style={styles.sheetTop}>
            <View />
            <Pressable
              accessibilityHint={copy.common.closeHint}
              accessibilityLabel={copy.common.close}
              accessibilityRole="button"
              onPress={() => setShowOtherWays(false)}
              style={styles.sheetClose}
            >
              <Text style={styles.sheetCloseText}>{copy.common.done}</Text>
            </Pressable>
          </View>
          <Text accessibilityRole="header" style={styles.sheetTitle}>{copy.start.sheetTitle}</Text>
          <Text style={styles.sheetHelper}>{copy.start.sheetHelper}</Text>
          <View style={styles.sourceList}>
            <SourceRow
              hint={copy.start.folderHint}
              label={copy.start.folder}
              onPress={chooseFolder}
            />
            <SourceRow
              detail={googleConfigured ? undefined : copy.start.googleUnavailable}
              hint={copy.start.googleHint}
              label={copy.start.google}
              onPress={chooseGoogle}
            />
          </View>
        </View>
      </Modal>
    </View>
  );
}

function SourceRow({
  label,
  hint,
  detail,
  disabled = false,
  onPress,
}: {
  label: string;
  hint: string;
  detail?: string;
  disabled?: boolean;
  onPress: () => void;
}) {
  return (
    <Pressable
      accessibilityHint={detail ?? hint}
      accessibilityLabel={label}
      accessibilityRole="button"
      accessibilityState={{ disabled }}
      disabled={disabled}
      onPress={onPress}
      style={({ pressed }) => [styles.source, pressed ? styles.sourcePressed : null, disabled ? styles.sourceDisabled : null]}
    >
      <View style={styles.sourceCopy}>
        <Text style={styles.sourceLabel}>{label}</Text>
        <Text style={styles.sourceHint}>{detail ?? hint}</Text>
      </View>
      <Text style={styles.sourceArrow}>›</Text>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  actions: { gap: spacing.sm },
  albumSketch: { alignItems: "center", height: 184, justifyContent: "center" },
  infoMessage: { alignItems: "center", gap: spacing.sm },
  infoText: { color: colors.text, fontFamily: fonts.body, textAlign: "center", ...typeScale.body },
  messageCard: { padding: spacing.sm },
  paper: { ...continuousRadius(radii.sm), borderColor: colors.hairline, borderWidth: 1, position: "absolute" },
  paperBack: { backgroundColor: colors.panel, height: 142, transform: [{ rotate: "-5deg" }], width: 112 },
  paperFront: { backgroundColor: colors.text, gap: spacing.xs, height: 150, padding: spacing.sm, transform: [{ rotate: "4deg" }], width: 118 },
  paperLine: { backgroundColor: "#bdb5a5", height: 5, width: "84%" },
  paperLineShort: { width: "58%" },
  photoMark: { color: colors.gold, fontSize: 25 },
  photoWindow: { alignItems: "center", backgroundColor: colors.panelRaised, flex: 1, justifyContent: "center", width: "100%" },
  privacy: { color: colors.muted, fontFamily: fonts.body, textAlign: "center", ...typeScale.small },
  root: { backgroundColor: colors.background, flex: 1 },
  scroll: {
    flexGrow: 1,
    gap: spacing.lg,
    justifyContent: "space-between",
    paddingBottom: spacing.xl,
    paddingHorizontal: layout.screenPadding,
    paddingTop: (StatusBar.currentHeight ?? 24) + spacing.lg,
  },
  sheet: { backgroundColor: colors.background, flex: 1, paddingHorizontal: layout.screenPadding, paddingTop: StatusBar.currentHeight ?? spacing.md },
  sheetClose: { justifyContent: "center", minHeight: layout.minTouchTarget, paddingHorizontal: spacing.sm },
  sheetCloseText: { color: colors.gold, fontFamily: fonts.body, ...typeScale.label },
  sheetHelper: { color: colors.muted, fontFamily: fonts.body, ...typeScale.body },
  sheetTitle: { color: colors.text, fontFamily: fonts.display, ...typeScale.title },
  sheetTop: { alignItems: "center", flexDirection: "row", justifyContent: "space-between" },
  source: { alignItems: "center", borderBottomColor: colors.hairline, borderBottomWidth: 1, flexDirection: "row", minHeight: 84, paddingVertical: spacing.sm },
  sourceArrow: { color: colors.gold, fontFamily: fonts.body, fontSize: 32 },
  sourceCopy: { flex: 1, gap: spacing.xxs },
  sourceDisabled: { opacity: 0.5 },
  sourceHint: { color: colors.muted, fontFamily: fonts.body, ...typeScale.small },
  sourceLabel: { color: colors.text, fontFamily: fonts.body, fontWeight: "700", ...typeScale.body },
  sourceList: { paddingTop: spacing.xl },
  sourcePressed: { opacity: 0.62 },
  trust: { color: colors.gold, fontFamily: fonts.body, fontWeight: "600", ...typeScale.eyebrow },
});
