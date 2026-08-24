import type { TextStyle, ViewStyle } from "react-native";

export const colors = {
  background: "#faf8f5",
  panel: "#ffffff",
  panelRaised: "#f9ece5",
  hairline: "#e7e2d9",
  text: "#1a1714",
  muted: "#6f6a62",
  gold: "#c75c33",
  goldPressed: "#a8481f",
  ink: "#1a1714",
  onAccent: "#ffffff",
  error: "#a8481f",
  success: "#4a8a5c",
  privacySurface: "#eef2ec",
  quietSurface: "#f1efe9",
  scrim: "rgba(30, 24, 18, 0.42)",
  imageScrim: "rgba(20, 15, 10, 0.34)",
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
  sm: 10,
  md: 16,
  lg: 20,
  pill: 999,
} as const;

export const typeScale = {
  display: { fontSize: 38, lineHeight: 40, letterSpacing: -1.3 },
  title: { fontSize: 30, lineHeight: 34, letterSpacing: -0.9 },
  subtitle: { fontSize: 21, lineHeight: 27, letterSpacing: -0.3 },
  body: { fontSize: 16, lineHeight: 24 },
  label: { fontSize: 16, lineHeight: 22 },
  small: { fontSize: 14, lineHeight: 20 },
  eyebrow: { fontSize: 12, lineHeight: 17, letterSpacing: 1.1 },
} satisfies Record<string, TextStyle>;

export const layout = {
  screenPadding: 22,
  minTouchTarget: 48,
  primaryButtonHeight: 56,
  maxReadableWidth: 620,
} as const;

export const continuousRadius = (radius: number): ViewStyle => ({
  borderRadius: radius,
  borderCurve: "continuous",
});
