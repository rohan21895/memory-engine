import * as SecureStore from "expo-secure-store";
import { StatusBar } from "expo-status-bar";
import { useCallback, useEffect, useState } from "react";
import { StyleSheet, View } from "react-native";

import { buildAlbum } from "./src/build-album";
import GalleryGrid from "./src/import/GalleryGrid";
import { pickLocalFolder } from "./src/import/folder-picker";
import { useGooglePhotosPicker } from "./src/import/google-photos";
import type { PickedPhoto, PhotoSource } from "./src/import/picked-photo";
import FinalAlbum, { type FinalPhoto } from "./src/review/FinalAlbum";
import type { ReviewData } from "./src/review/mock-data";
import ReviewScreen from "./src/review/ReviewScreen";
import { copy, colors, LoadingState } from "./src/ui";
import { BuildingScreen } from "./src/ui/screens/BuildingScreen";
import { StartScreen, type StartMessage } from "./src/ui/screens/StartScreen";
import { WelcomeScreen } from "./src/ui/screens/WelcomeScreen";

const WELCOME_SEEN_KEY = "photeo-welcome-seen-v1";

export default function App() {
  const [welcomeChecked, setWelcomeChecked] = useState(false);
  const [welcomeSeen, setWelcomeSeen] = useState(false);
  const [photos, setPhotos] = useState<PickedPhoto[]>([]);
  const [album, setAlbum] = useState<ReviewData | null>(null);
  const [finalPhotos, setFinalPhotos] = useState<FinalPhoto[] | null>(null);
  const [busySource, setBusySource] = useState<PhotoSource | null>(null);
  const [showGallery, setShowGallery] = useState(false);
  const [building, setBuilding] = useState(false);
  const [message, setMessage] = useState<StartMessage | null>(null);
  const { configured: googleConfigured, pickGooglePhotos } = useGooglePhotosPicker();

  useEffect(() => {
    void SecureStore.getItemAsync(WELCOME_SEEN_KEY)
      .then((value) => setWelcomeSeen(value === "yes"))
      .catch(() => setWelcomeSeen(false))
      .finally(() => setWelcomeChecked(true));
  }, []);

  const finishWelcome = useCallback(() => {
    setWelcomeSeen(true);
    void SecureStore.setItemAsync(WELCOME_SEEN_KEY, "yes").catch(() => undefined);
  }, []);

  const processPhotos = useCallback(async (next: PickedPhoto[]) => {
    setPhotos(next);
    if (next.length === 0) {
      setMessage({ kind: "info", text: copy.start.noPhotos });
      return;
    }

    setBuilding(true);
    setMessage(null);
    try {
      // The existing local album engine remains the single source of selection decisions.
      const built = await buildAlbum(next);
      setAlbum(built);
    } catch {
      setMessage({
        kind: "error",
        title: copy.start.buildError,
        text: copy.privacyShort,
      });
    } finally {
      setBuilding(false);
    }
  }, []);

  const runPicker = useCallback(
    async (source: PhotoSource) => {
      setMessage(null);
      if (source === "device-gallery") {
        setShowGallery(true);
        return;
      }
      if (source === "google-photos" && !googleConfigured) {
        setMessage({ kind: "info", text: copy.start.googleUnavailable });
        return;
      }

      setBusySource(source);
      try {
        const next =
          source === "local-folder" ? await pickLocalFolder() : await pickGooglePhotos();
        await processPhotos(next);
      } catch {
        setMessage({
          kind: "error",
          title: copy.start.pickerError,
          text: copy.privacyShort,
        });
      } finally {
        setBusySource(null);
      }
    },
    [googleConfigured, pickGooglePhotos, processPhotos],
  );

  if (!welcomeChecked) {
    return (
      <View style={styles.root}>
        <StatusBar style="light" />
        <LoadingState helper={copy.states.safe} title={copy.states.preparing} />
      </View>
    );
  }

  if (!welcomeSeen) return <WelcomeScreen onContinue={finishWelcome} />;

  if (showGallery) {
    return (
      <GalleryGrid
        onBack={() => setShowGallery(false)}
        onConfirm={(picked) => {
          setShowGallery(false);
          void processPhotos(picked);
        }}
      />
    );
  }

  if (building) return <BuildingScreen />;

  if (finalPhotos) {
    return (
      <FinalAlbum
        photos={finalPhotos}
        onBack={() => setFinalPhotos(null)}
        onRestart={() => {
          setFinalPhotos(null);
          setAlbum(null);
          setPhotos([]);
          setMessage(null);
        }}
      />
    );
  }

  if (album) {
    return (
      <ReviewScreen
        data={album}
        onBack={() => setAlbum(null)}
        onFinalize={(picked) => setFinalPhotos(picked)}
      />
    );
  }

  return (
    <StartScreen
      busy={busySource !== null}
      googleConfigured={googleConfigured}
      message={message}
      onChooseFolder={() => void runPicker("local-folder")}
      onChooseGoogle={() => void runPicker("google-photos")}
      onChoosePhotos={() => void runPicker("device-gallery")}
      onDismissMessage={() => setMessage(null)}
    />
  );
}

const styles = StyleSheet.create({
  root: { backgroundColor: colors.background, flex: 1 },
});
