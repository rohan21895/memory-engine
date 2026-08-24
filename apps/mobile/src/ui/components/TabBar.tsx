import { Pressable, StyleSheet, Text, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { fonts } from "../fonts";
import { colors, layout, spacing, typeScale } from "../tokens";

export type AppTab = "albums" | "photos" | "account";

const tabs: { key: AppTab; label: string; icon: string }[] = [
  { key: "albums", label: "Albums", icon: "▣" },
  { key: "photos", label: "Photos", icon: "□" },
  { key: "account", label: "Account", icon: "○" },
];

export function TabBar({
  activeTab,
  onChange,
}: {
  activeTab: AppTab;
  onChange: (tab: AppTab) => void;
}) {
  const insets = useSafeAreaInsets();
  return (
    <View style={[styles.bar, { paddingBottom: Math.max(insets.bottom, spacing.sm) }]}>
      {tabs.map((tab) => {
        const active = tab.key === activeTab;
        return (
          <Pressable
            accessibilityRole="tab"
            accessibilityState={{ selected: active }}
            key={tab.key}
            onPress={() => onChange(tab.key)}
            style={({ pressed }) => [styles.tab, pressed ? styles.pressed : null]}
          >
            <Text style={[styles.icon, active ? styles.active : null]}>{tab.icon}</Text>
            <Text style={[styles.label, active ? styles.active : null]}>{tab.label}</Text>
          </Pressable>
        );
      })}
    </View>
  );
}

const styles = StyleSheet.create({
  active: { color: colors.gold },
  bar: {
    backgroundColor: colors.background,
    borderTopColor: colors.hairline,
    borderTopWidth: 1,
    flexDirection: "row",
    minHeight: 70,
    paddingHorizontal: spacing.sm,
  },
  icon: { color: "#8b8378", fontFamily: fonts.bold, fontSize: 22, lineHeight: 23 },
  label: { color: "#8b8378", fontFamily: fonts.bold, ...typeScale.eyebrow },
  pressed: { opacity: 0.62 },
  tab: {
    alignItems: "center",
    flex: 1,
    gap: spacing.xxs,
    justifyContent: "center",
    minHeight: layout.minTouchTarget,
  },
});
