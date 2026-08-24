import { Platform } from "react-native";

export const fonts = {
  display: Platform.select({
    ios: "Fraunces-Regular",
    default: "Fraunces",
  }),
  body: Platform.select({
    ios: "AtkinsonHyperlegibleNext-Regular",
    default: "AtkinsonHyperlegibleNext",
  }),
} as const;

