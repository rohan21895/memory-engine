import { Pressable, StyleSheet, Text, TextInput, View } from "react-native";

import { fonts } from "../fonts";
import { colors, continuousRadius, layout, radii, spacing, typeScale } from "../tokens";

export type FilterSearchBarProps = {
  accessibilityLabel: string;
  onChangeText: (value: string) => void;
  placeholder: string;
  value: string;
};

export function FilterSearchBar({
  accessibilityLabel,
  onChangeText,
  placeholder,
  value,
}: FilterSearchBarProps) {
  return (
    <View style={styles.root}>
      <Text accessibilityElementsHidden importantForAccessibility="no-hide-descendants" style={styles.icon}>
        ⌕
      </Text>
      <TextInput
        accessibilityLabel={accessibilityLabel}
        autoCapitalize="none"
        autoCorrect={false}
        clearButtonMode="while-editing"
        onChangeText={onChangeText}
        placeholder={placeholder}
        placeholderTextColor={colors.muted}
        returnKeyType="search"
        style={styles.input}
        value={value}
      />
      {value.length > 0 ? (
        <Pressable
          accessibilityLabel={`Clear ${accessibilityLabel.toLowerCase()}`}
          accessibilityRole="button"
          hitSlop={8}
          onPress={() => onChangeText("")}
          style={({ pressed }) => [styles.clear, pressed ? styles.pressed : null]}
        >
          <Text style={styles.clearText}>×</Text>
        </Pressable>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  clear: {
    alignItems: "center",
    borderRadius: radii.pill,
    height: 32,
    justifyContent: "center",
    width: 32,
  },
  clearText: { color: colors.muted, fontFamily: fonts.body, fontSize: 24, lineHeight: 27 },
  icon: { color: colors.gold, fontFamily: fonts.body, fontSize: 24, lineHeight: 26 },
  input: {
    color: colors.text,
    flex: 1,
    fontFamily: fonts.body,
    minHeight: layout.minTouchTarget,
    paddingVertical: spacing.xs,
    ...typeScale.label,
  },
  pressed: { opacity: 0.58 },
  root: {
    ...continuousRadius(radii.md),
    alignItems: "center",
    backgroundColor: colors.background,
    borderColor: colors.hairline,
    borderWidth: 1,
    flexDirection: "row",
    gap: spacing.xs,
    minHeight: layout.minTouchTarget,
    paddingHorizontal: spacing.sm,
  },
});
