import { StatusBar } from "expo-status-bar";
import { useState } from "react";
import {
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from "react-native";

// Warm-charcoal editorial palette — matches the web review UI (docs/mobile-app-plan.md).
const C = {
  bg: "#141311",
  panel: "#1c1a17",
  line: "#2c2a25",
  text: "#e8e4dc",
  muted: "#9a927f",
  gold: "#c8a24a",
};

// The three Android photo sources for M1 (Apple Photos arrives with the iOS build).
// Handlers are stubbed here; worker CX-2 (#142) wires the real pickers.
const SOURCES = [
  { key: "gallery", label: "Device gallery", hint: "Android Photo Picker" },
  { key: "folder", label: "Local folder", hint: "pick a folder of photos" },
  { key: "google", label: "Google Photos", hint: "sign in — Photos Picker API" },
] as const;

export default function App() {
  const [picked, setPicked] = useState<string | null>(null);

  return (
    <View style={styles.root}>
      <StatusBar style="light" />
      <ScrollView contentContainerStyle={styles.scroll}>
        <Text style={styles.eyebrow}>ON-DEVICE · PRIVATE</Text>
        <Text style={styles.title}>Photeo</Text>
        <Text style={styles.sub}>
          Point it at your photos. Everything — finding your best shots — runs on
          this phone. Nothing leaves it.
        </Text>

        <Text style={styles.section}>CHOOSE A SOURCE</Text>
        {SOURCES.map((s) => (
          <Pressable
            key={s.key}
            style={({ pressed }) => [styles.source, pressed && styles.sourcePressed]}
            onPress={() => setPicked(s.key)}
          >
            <View style={{ flex: 1 }}>
              <Text style={styles.sourceLabel}>{s.label}</Text>
              <Text style={styles.sourceHint}>{s.hint}</Text>
            </View>
            <Text style={styles.chev}>›</Text>
          </Pressable>
        ))}

        <Text style={styles.status}>
          {picked
            ? `“${SOURCES.find((s) => s.key === picked)?.label}” — picker lands in CX-2 (#142).`
            : "Pick a source to begin."}
        </Text>
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: C.bg },
  scroll: { padding: 28, paddingTop: 72, gap: 4 },
  eyebrow: {
    color: C.gold,
    fontSize: 11,
    letterSpacing: 2,
    fontVariant: ["tabular-nums"],
  },
  title: {
    color: C.text,
    fontSize: 44,
    fontWeight: "400",
    fontFamily: "serif",
    marginTop: 6,
  },
  sub: { color: C.muted, fontSize: 15, lineHeight: 22, marginTop: 12 },
  section: {
    color: C.muted,
    fontSize: 11,
    letterSpacing: 2,
    marginTop: 40,
    marginBottom: 14,
  },
  source: {
    flexDirection: "row",
    alignItems: "center",
    backgroundColor: C.panel,
    borderColor: C.line,
    borderWidth: 1,
    borderRadius: 4,
    paddingVertical: 18,
    paddingHorizontal: 18,
    marginBottom: 12,
  },
  sourcePressed: { borderColor: C.gold },
  sourceLabel: { color: C.text, fontSize: 17 },
  sourceHint: { color: C.muted, fontSize: 12.5, marginTop: 4 },
  chev: { color: C.gold, fontSize: 22 },
  status: { color: C.muted, fontSize: 13, marginTop: 32, lineHeight: 20 },
});
