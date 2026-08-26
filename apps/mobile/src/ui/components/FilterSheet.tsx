import { FlashList } from "@shopify/flash-list";
import { Image } from "expo-image";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Modal, Pressable, StatusBar, StyleSheet, Text, View } from "react-native";

import { copy } from "../copy";
import { fonts } from "../fonts";
import { colors, continuousRadius, layout, radii, spacing, typeScale } from "../tokens";
import { SecondaryButton } from "./SecondaryButton";

export type FilterSectionKey = "date" | "album" | "place" | "person";

export type FilterOption = {
  id: string;
  label: string;
  detail?: string;
  imageUri?: string;
};

export type FilterGroup = {
  title?: string;
  options: FilterOption[];
};

export type FilterSection = {
  key: FilterSectionKey;
  title: string;
  selectedId: string | null;
  groups: FilterGroup[];
  loadingText?: string;
  emptyText?: string;
};

export type FilterSheetProps = {
  visible: boolean;
  sections: FilterSection[];
  onClose: () => void;
  onClear: () => void;
  onSelect: (section: FilterSectionKey, id: string) => void;
};

type SheetRow =
  | { kind: "heading"; id: string; label: string }
  | { kind: "option"; id: string; option: FilterOption }
  | { kind: "message"; id: string; label: string };

function OptionRow({
  option,
  selected,
  onPress,
}: {
  option: FilterOption;
  selected: boolean;
  onPress: (id: string) => void;
}) {
  const handlePress = useCallback(() => onPress(option.id), [onPress, option.id]);
  return (
    <Pressable
      accessibilityHint={copy.filters.selectedHint(option.label, selected)}
      accessibilityLabel={option.detail ? `${option.label}. ${option.detail}` : option.label}
      accessibilityRole="radio"
      accessibilityState={{ checked: selected }}
      onPress={handlePress}
      style={({ pressed }) => [
        styles.option,
        selected ? styles.optionSelected : null,
        pressed ? styles.pressed : null,
      ]}
    >
      {option.imageUri ? (
        <Image
          accessibilityIgnoresInvertColors
          cachePolicy="memory-disk"
          contentFit="cover"
          recyclingKey={option.id}
          source={option.imageUri}
          style={styles.cover}
          transition={100}
        />
      ) : null}
      <View style={styles.optionCopy}>
        <Text style={[styles.optionLabel, selected ? styles.optionLabelSelected : null]}>
          {option.label}
        </Text>
        {option.detail ? <Text style={styles.optionDetail}>{option.detail}</Text> : null}
      </View>
      <View style={[styles.radio, selected ? styles.radioSelected : null]}>
        {selected ? <Text style={styles.check}>✓</Text> : null}
      </View>
    </Pressable>
  );
}

export function FilterSheet({
  visible,
  sections,
  onClose,
  onClear,
  onSelect,
}: FilterSheetProps) {
  const [openSection, setOpenSection] = useState<FilterSectionKey | null>(null);

  useEffect(() => {
    if (!visible) setOpenSection(null);
  }, [visible]);

  const activeSection = sections.find((section) => section.key === openSection);
  const rows = useMemo<SheetRow[]>(() => {
    if (!activeSection) return [];
    const result: SheetRow[] = [];
    for (const [groupIndex, group] of activeSection.groups.entries()) {
      if (group.title) {
        result.push({ kind: "heading", id: `heading-${groupIndex}`, label: group.title });
      }
      for (const option of group.options) {
        result.push({ kind: "option", id: option.id, option });
      }
    }
    if (activeSection.loadingText) {
      result.push({ kind: "message", id: "loading", label: activeSection.loadingText });
    } else if (result.every((row) => row.kind !== "option")) {
      result.push({
        kind: "message",
        id: "empty",
        label: activeSection.emptyText ?? copy.filters.noChoices,
      });
    }
    return result;
  }, [activeSection]);

  const selectOption = useCallback(
    (id: string) => {
      if (openSection) onSelect(openSection, id);
    },
    [onSelect, openSection],
  );

  const renderRow = useCallback(
    ({ item }: { item: SheetRow }) => {
      if (item.kind === "heading") return <Text style={styles.groupHeading}>{item.label}</Text>;
      if (item.kind === "message") return <Text style={styles.message}>{item.label}</Text>;
      return (
        <OptionRow
          onPress={selectOption}
          option={item.option}
          selected={activeSection?.selectedId === item.option.id}
        />
      );
    },
    [activeSection?.selectedId, selectOption],
  );

  return (
    <Modal
      animationType="slide"
      onRequestClose={onClose}
      presentationStyle="formSheet"
      visible={visible}
    >
      <View accessibilityViewIsModal style={styles.root}>
        <View style={styles.topBar}>
          {activeSection ? (
            <Pressable
              accessibilityHint={copy.common.goBackHint}
              accessibilityLabel={copy.filters.backToGroups}
              accessibilityRole="button"
              onPress={() => setOpenSection(null)}
              style={({ pressed }) => [styles.topAction, pressed ? styles.pressed : null]}
            >
              <Text style={styles.topActionText}>‹ {copy.common.back}</Text>
            </Pressable>
          ) : (
            <View style={styles.topAction} />
          )}
          <Pressable
            accessibilityHint={copy.common.closeHint}
            accessibilityLabel={copy.common.close}
            accessibilityRole="button"
            onPress={onClose}
            style={({ pressed }) => [styles.topAction, pressed ? styles.pressed : null]}
          >
            <Text style={styles.topActionText}>{copy.common.done}</Text>
          </Pressable>
        </View>

        <View style={styles.heading}>
          <Text accessibilityRole="header" style={styles.title}>
            {activeSection?.title ?? copy.filters.title}
          </Text>
          <Text style={styles.helper}>
            {activeSection ? copy.filters.categoryHint(activeSection.title) : copy.filters.helper}
          </Text>
        </View>

        {activeSection ? (
          <FlashList
            contentContainerStyle={styles.list}
            data={rows}
            keyExtractor={(item) => item.id}
            renderItem={renderRow}
          />
        ) : (
          <View style={styles.categories}>
            <Pressable
              accessibilityHint={copy.filters.allHint}
              accessibilityLabel={copy.filters.all}
              accessibilityRole="button"
              onPress={onClear}
              style={({ pressed }) => [styles.category, pressed ? styles.pressed : null]}
            >
              <View style={styles.allIcon}><Text style={styles.allIconText}>✦</Text></View>
              <Text style={styles.categoryLabel}>{copy.filters.all}</Text>
              <Text style={styles.chevron}>›</Text>
            </Pressable>
            {sections.map((section) => (
              <Pressable
                accessibilityHint={copy.filters.categoryHint(section.title)}
                accessibilityLabel={section.title}
                accessibilityRole="button"
                key={section.key}
                onPress={() => setOpenSection(section.key)}
                style={({ pressed }) => [styles.category, pressed ? styles.pressed : null]}
              >
                <View style={styles.sectionMark} />
                <Text style={styles.categoryLabel}>{section.title}</Text>
                {section.selectedId ? <Text style={styles.activeMark}>✓</Text> : null}
                <Text style={styles.chevron}>›</Text>
              </Pressable>
            ))}
          </View>
        )}

        <View style={styles.footer}>
          <SecondaryButton
            accessibilityHint={copy.filters.allHint}
            label={copy.filters.all}
            onPress={onClear}
            quiet
          />
        </View>
      </View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  activeMark: { color: colors.gold, fontFamily: fonts.body, fontSize: 18, fontWeight: "700" },
  allIcon: {
    alignItems: "center",
    backgroundColor: colors.gold,
    borderRadius: 18,
    height: 36,
    justifyContent: "center",
    width: 36,
  },
  allIconText: { color: colors.ink, fontSize: 18 },
  categories: { gap: spacing.xs, paddingHorizontal: spacing.lg },
  category: {
    alignItems: "center",
    borderBottomColor: colors.hairline,
    borderBottomWidth: 1,
    flexDirection: "row",
    gap: spacing.md,
    minHeight: 64,
  },
  categoryLabel: { color: colors.text, flex: 1, fontFamily: fonts.body, fontWeight: "600", ...typeScale.body },
  check: { color: colors.ink, fontFamily: fonts.body, fontSize: 16, fontWeight: "700" },
  chevron: { color: colors.muted, fontFamily: fonts.body, fontSize: 30, lineHeight: 32 },
  cover: {
    backgroundColor: colors.hairline,
    borderCurve: "continuous",
    borderRadius: radii.md,
    height: 54,
    width: 54,
  },
  footer: { borderTopColor: colors.hairline, borderTopWidth: 1, padding: spacing.md },
  groupHeading: {
    color: colors.gold,
    fontFamily: fonts.body,
    fontWeight: "600",
    paddingBottom: spacing.xs,
    paddingTop: spacing.lg,
    textTransform: "uppercase",
    ...typeScale.eyebrow,
  },
  heading: { gap: spacing.xs, paddingBottom: spacing.md, paddingHorizontal: spacing.lg },
  helper: { color: colors.muted, fontFamily: fonts.body, ...typeScale.body },
  list: { paddingBottom: spacing.xl, paddingHorizontal: spacing.lg },
  message: { color: colors.muted, fontFamily: fonts.body, paddingVertical: spacing.lg, ...typeScale.body },
  option: {
    ...continuousRadius(radii.md),
    alignItems: "center",
    borderColor: colors.hairline,
    borderWidth: 1,
    flexDirection: "row",
    gap: spacing.sm,
    marginBottom: spacing.xs,
    minHeight: 64,
    padding: spacing.sm,
  },
  optionCopy: { flex: 1 },
  optionDetail: { color: colors.muted, fontFamily: fonts.body, ...typeScale.small },
  optionLabel: { color: colors.text, fontFamily: fonts.body, ...typeScale.label },
  optionLabelSelected: { color: colors.gold, fontWeight: "700" },
  optionSelected: { backgroundColor: colors.panelRaised, borderColor: colors.gold },
  pressed: { opacity: 0.64 },
  radio: {
    alignItems: "center",
    borderColor: colors.muted,
    borderRadius: 13,
    borderWidth: 1.5,
    height: 26,
    justifyContent: "center",
    width: 26,
  },
  radioSelected: { backgroundColor: colors.gold, borderColor: colors.gold },
  root: { backgroundColor: colors.background, flex: 1, paddingTop: StatusBar.currentHeight ?? spacing.md },
  sectionMark: { backgroundColor: colors.gold, borderRadius: 3, height: 6, width: 6 },
  title: { color: colors.text, fontFamily: fonts.display, ...typeScale.title },
  topAction: {
    justifyContent: "center",
    minHeight: layout.minTouchTarget,
    minWidth: 72,
    paddingHorizontal: spacing.sm,
  },
  topActionText: { color: colors.gold, fontFamily: fonts.body, ...typeScale.label },
  topBar: {
    alignItems: "center",
    flexDirection: "row",
    justifyContent: "space-between",
    paddingHorizontal: spacing.sm,
    paddingVertical: spacing.xs,
  },
});
