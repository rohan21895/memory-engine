import { Pressable, StyleSheet, Text, View } from "react-native";

import { fonts } from "../fonts";
import { colors, continuousRadius, layout, radii, spacing, typeScale } from "../tokens";

export type DateFilterOption = {
  detail?: string;
  id: string;
  label: string;
};

export type DateFilterPanelProps = {
  expanded: boolean;
  loadingText?: string;
  months: DateFilterOption[];
  onSelect: (dateId: string) => void;
  onToggle: () => void;
  presets: DateFilterOption[];
  selectedDateId: string;
};

function DateRow({
  option,
  selected,
  onSelect,
}: {
  option: DateFilterOption;
  selected: boolean;
  onSelect: (id: string) => void;
}) {
  return (
    <Pressable
      accessibilityHint={`${option.label}. ${selected ? "Selected" : "Not selected"}`}
      accessibilityLabel={option.detail ? `${option.label}. ${option.detail}` : option.label}
      accessibilityRole="radio"
      accessibilityState={{ checked: selected }}
      onPress={() => onSelect(option.id)}
      style={({ pressed }) => [styles.row, selected ? styles.rowSelected : null, pressed ? styles.pressed : null]}
    >
      <View style={styles.rowCopy}>
        <Text style={[styles.rowLabel, selected ? styles.selectedText : null]}>{option.label}</Text>
        {option.detail ? <Text style={styles.detail}>{option.detail}</Text> : null}
      </View>
      <View style={[styles.radio, selected ? styles.radioSelected : null]}>
        {selected ? <Text style={styles.check}>✓</Text> : null}
      </View>
    </Pressable>
  );
}

export function DateFilterPanel({
  expanded,
  loadingText,
  months,
  onSelect,
  onToggle,
  presets,
  selectedDateId,
}: DateFilterPanelProps) {
  const allOptions = [...presets, ...months];
  const selected = allOptions.find((option) => option.id === selectedDateId);

  return (
    <View style={styles.section}>
      <Pressable
        accessibilityHint={expanded ? "Date choices are open" : "Opens date choices"}
        accessibilityLabel={`Date filter. ${selected?.label ?? "Any date"}`}
        accessibilityRole="button"
        accessibilityState={{ expanded }}
        onPress={onToggle}
        style={({ pressed }) => [styles.heading, pressed ? styles.pressed : null]}
      >
        <View style={styles.number}><Text style={styles.numberText}>3</Text></View>
        <View style={styles.headingCopy}>
          <Text accessibilityRole="header" style={styles.title}>Date</Text>
          <Text style={styles.summary}>{selected?.label ?? "Any date"}</Text>
        </View>
        <Text accessibilityElementsHidden style={styles.chevron}>{expanded ? "⌃" : "⌄"}</Text>
      </Pressable>

      {expanded ? (
        <View accessibilityRole="radiogroup" style={styles.content}>
          <View style={styles.group}>
            <Text style={styles.groupTitle}>Date ranges</Text>
            {presets.map((option) => (
              <DateRow
                key={option.id}
                onSelect={onSelect}
                option={option}
                selected={option.id === selectedDateId}
              />
            ))}
          </View>
          {months.length > 0 ? (
            <View style={styles.group}>
              <Text style={styles.groupTitle}>Months</Text>
              {months.map((option) => (
                <DateRow
                  key={option.id}
                  onSelect={onSelect}
                  option={option}
                  selected={option.id === selectedDateId}
                />
              ))}
            </View>
          ) : (
            <Text accessibilityLiveRegion="polite" style={styles.message}>
              {loadingText ?? "No month choices found yet."}
            </Text>
          )}
          {loadingText && months.length > 0 ? (
            <Text accessibilityLiveRegion="polite" style={styles.message}>{loadingText}</Text>
          ) : null}
        </View>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  check: { color: colors.ink, fontFamily: fonts.body, fontSize: 16, fontWeight: "700" },
  chevron: { color: colors.gold, fontFamily: fonts.body, fontSize: 22, width: 24 },
  content: { gap: spacing.lg, padding: spacing.md, paddingTop: 0 },
  detail: { color: colors.muted, fontFamily: fonts.body, fontVariant: ["tabular-nums"], ...typeScale.small },
  group: { gap: spacing.xs },
  groupTitle: {
    color: colors.gold,
    fontFamily: fonts.body,
    fontWeight: "600",
    paddingTop: spacing.xs,
    textTransform: "uppercase",
    ...typeScale.eyebrow,
  },
  heading: { alignItems: "center", flexDirection: "row", gap: spacing.sm, minHeight: 76, padding: spacing.md },
  headingCopy: { flex: 1, gap: spacing.xxs },
  message: { color: colors.muted, fontFamily: fonts.body, ...typeScale.small },
  number: {
    alignItems: "center",
    backgroundColor: colors.gold,
    borderRadius: radii.pill,
    height: 30,
    justifyContent: "center",
    width: 30,
  },
  numberText: { color: colors.ink, fontFamily: fonts.body, fontSize: 15, fontWeight: "700" },
  pressed: { opacity: 0.62 },
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
  row: {
    ...continuousRadius(radii.md),
    alignItems: "center",
    borderColor: colors.hairline,
    borderWidth: 1,
    flexDirection: "row",
    gap: spacing.sm,
    minHeight: layout.primaryButtonHeight,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.xs,
  },
  rowCopy: { flex: 1 },
  rowLabel: { color: colors.text, fontFamily: fonts.body, ...typeScale.label },
  rowSelected: { backgroundColor: colors.panelRaised, borderColor: colors.gold },
  section: {
    ...continuousRadius(radii.lg),
    backgroundColor: colors.panel,
    borderColor: colors.hairline,
    borderWidth: 1,
    overflow: "hidden",
  },
  selectedText: { color: colors.gold, fontWeight: "700" },
  summary: { color: colors.muted, fontFamily: fonts.body, ...typeScale.small },
  title: { color: colors.text, fontFamily: fonts.display, ...typeScale.subtitle },
});
