import type { TextStyle, ViewStyle } from "react-native";

export const colors = {
  background: "#141311",
  panel: "#1c1a17",
  panelRaised: "#232019",
  hairline: "#2c2a25",
  text: "#e8e4dc",
  muted: "#9a927f",
  gold: "#c8a24a",
  goldPressed: "#b5903d",
  ink: "#18150f",
  error: "#f0aa94",
  success: "#9fc49a",
  scrim: "rgba(9, 8, 6, 0.76)",
  imageScrim: "rgba(10, 8, 5, 0.34)",
} as const;

export const spacing = {
  xxs: 4,
  xs: 8,
  sm: 12,
  md: 16,
  lg: 24,
  xl: 32,
  xxl: 48,
  xxxl: 64,
} as const;

export const radii = {
  sm: 6,
  md: 10,
  lg: 16,
  pill: 999,
} as const;

export const typeScale = {
  display: { fontSize: 46, lineHeight: 50, letterSpacing: -0.8 },
  title: { fontSize: 34, lineHeight: 39, letterSpacing: -0.4 },
  subtitle: { fontSize: 22, lineHeight: 28 },
  body: { fontSize: 18, lineHeight: 27 },
  label: { fontSize: 17, lineHeight: 22 },
  small: { fontSize: 15, lineHeight: 20 },
  eyebrow: { fontSize: 13, lineHeight: 18, letterSpacing: 1.7 },
} satisfies Record<string, TextStyle>;

export const layout = {
  screenPadding: 22,
  minTouchTarget: 48,
  primaryButtonHeight: 60,
  maxReadableWidth: 620,
} as const;

export const continuousRadius = (radius: number): ViewStyle => ({
  borderRadius: radius,
  borderCurve: "continuous",
});
