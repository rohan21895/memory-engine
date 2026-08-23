import { Image } from "expo-image";
import { StatusBar } from "expo-status-bar";
import { useCallback, useState } from "react";
import {
  ActivityIndicator,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from "react-native";

import { pickDeviceGallery } from "./src/import/device-gallery";
import { pickLocalFolder } from "./src/import/folder-picker";
import { useGooglePhotosPicker } from "./src/import/google-photos";
import type { PickedPhoto, PhotoSource } from "./src/import/picked-photo";

const C = {
  bg: "#141311",
  panel: "#1c1a17",
  line: "#2c2a25",
  text: "#e8e4dc",
  muted: "#9a927f",
  gold: "#c8a24a",
  error: "#e59a82",
};

type SourceConfig = {
  key: PhotoSource;
  label: string;
  hint: string;
};

const SOURCES: SourceConfig[] = [
  { key: "device-gallery", label: "Device gallery", hint: "Android Photo Picker" },
  { key: "local-folder", label: "Local folder", hint: "Storage Access Framework" },
  { key: "google-photos", label: "Google Photos", hint: "Photos Picker API · PKCE" },
];

export default function App() {
  const [photos, setPhotos] = useState<PickedPhoto[]>([]);
  const [busySource, setBusySource] = useState<PhotoSource | null>(null);
  const [message, setMessage] = useState("Pick a source to begin.");
  const [isError, setIsError] = useState(false);
  const { configured: googleConfigured, pickGooglePhotos } = useGooglePhotosPicker();

  const runPicker = useCallback(
    async (source: PhotoSource) => {
      setBusySource(source);
      setIsError(false);
      setMessage("Opening picker…");
      try {
        const next =
          source === "device-gallery"
            ? await pickDeviceGallery()
            : source === "local-folder"
              ? await pickLocalFolder()
              : await pickGooglePhotos();
        setPhotos(next);
        setMessage(
          next.length === 0
            ? "No photos selected."
            : `${next.length.toLocaleString()} photo${next.length === 1 ? "" : "s"} ready on device.`,
        );
      } catch (error) {
        setIsError(true);
        setMessage(error instanceof Error ? error.message : "Could not open this source.");
      } finally {
        setBusySource(null);
      }
    },
    [pickGooglePhotos],
  );

  return (
    <View style={styles.root}>
      <StatusBar style="light" />
      <ScrollView contentContainerStyle={styles.scroll}>
        <Text style={styles.eyebrow}>ON-DEVICE · PRIVATE</Text>
        <Text style={styles.title}>Photeo</Text>
        <Text style={styles.sub}>
          Point it at your photos. Finding your best shots runs on this phone.
          Nothing leaves it unless you explicitly open Google Photos.
        </Text>

        <Text style={styles.section}>CHOOSE A SOURCE</Text>
        {SOURCES.map((source) => {
          const busy = busySource === source.key;
          const needsSetup = source.key === "google-photos" && !googleConfigured;
          return (
            <Pressable
              accessibilityRole="button"
              accessibilityLabel={`${source.label}. ${source.hint}`}
              disabled={busySource !== null}
              key={source.key}
              onPress={() => void runPicker(source.key)}
              style={({ pressed }) => [
                styles.source,
                pressed && styles.sourcePressed,
                busySource !== null && !busy && styles.sourceDisabled,
              ]}
            >
              <View style={styles.sourceCopy}>
                <Text style={styles.sourceLabel}>{source.label}</Text>
                <Text style={styles.sourceHint}>
                  {needsSetup ? "OAuth setup required · see setup doc" : source.hint}
                </Text>
              </View>
              {busy ? (
                <ActivityIndicator color={C.gold} />
              ) : (
                <Text style={styles.chevron}>›</Text>
              )}
            </Pressable>
          );
        })}

        <Text selectable style={[styles.status, isError && styles.statusError]}>
          {message}
        </Text>

        {photos.length > 0 ? (
          <View style={styles.previewGrid}>
            {photos.slice(0, 9).map((photo) => (
              <Image
                accessibilityLabel={photo.filename}
                contentFit="cover"
                key={photo.id}
                source={photo.uri}
                style={styles.preview}
                transition={120}
              />
            ))}
          </View>
        ) : null}
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: C.bg },
  scroll: { padding: 28, paddingBottom: 48, paddingTop: 72, gap: 4 },
  eyebrow: { color: C.gold, fontSize: 11, letterSpacing: 2 },
  title: { color: C.text, fontSize: 44, fontWeight: "400", marginTop: 6 },
  sub: { color: C.muted, fontSize: 15, lineHeight: 22, marginTop: 12 },
  section: {
    color: C.muted,
    fontSize: 11,
    letterSpacing: 2,
    marginBottom: 14,
    marginTop: 40,
  },
  source: {
    alignItems: "center",
    backgroundColor: C.panel,
    borderColor: C.line,
    borderRadius: 8,
    borderWidth: 1,
    flexDirection: "row",
    marginBottom: 12,
    minHeight: 72,
    paddingHorizontal: 18,
    paddingVertical: 14,
  },
  sourcePressed: { borderColor: C.gold, transform: [{ scale: 0.98 }] },
  sourceDisabled: { opacity: 0.45 },
  sourceCopy: { flex: 1 },
  sourceLabel: { color: C.text, fontSize: 17 },
  sourceHint: { color: C.muted, fontSize: 12.5, marginTop: 4 },
  chevron: { color: C.gold, fontSize: 22 },
  status: { color: C.muted, fontSize: 13, lineHeight: 20, marginTop: 24 },
  statusError: { color: C.error },
  previewGrid: { flexDirection: "row", flexWrap: "wrap", gap: 6, marginTop: 20 },
  preview: { backgroundColor: C.panel, borderRadius: 4, height: 88, width: "31%" },
});
