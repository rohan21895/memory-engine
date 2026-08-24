import {
  Figtree_400Regular,
  Figtree_500Medium,
  Figtree_600SemiBold,
  Figtree_700Bold,
  Figtree_800ExtraBold,
  useFonts,
} from "@expo-google-fonts/figtree";
import * as MediaLibrary from "expo-media-library/legacy";
import * as SecureStore from "expo-secure-store";
import { StatusBar } from "expo-status-bar";
import { useCallback, useEffect, useState } from "react";
import { StyleSheet, View } from "react-native";

import { buildAlbum } from "./src/build-album";
import GalleryGrid from "./src/import/GalleryGrid";
import type { PickedPhoto } from "./src/import/picked-photo";
import FinalAlbum, { type FinalPhoto } from "./src/review/FinalAlbum";
import type { ReviewData } from "./src/review/mock-data";
import ReviewScreen from "./src/review/ReviewScreen";
import { TabBar, type AppTab } from "./src/ui/components/TabBar";
import { colors, copy, LoadingState } from "./src/ui";
import { AccountScreen } from "./src/ui/screens/AccountScreen";
import { AlbumsScreen } from "./src/ui/screens/AlbumsScreen";
import { BuildingScreen } from "./src/ui/screens/BuildingScreen";
import { LoginScreen } from "./src/ui/screens/LoginScreen";
import { PhotosScreen } from "./src/ui/screens/PhotosScreen";
import { StartScreen } from "./src/ui/screens/StartScreen";
import { WelcomeScreen } from "./src/ui/screens/WelcomeScreen";

const WELCOME_SEEN_KEY = "photeo-welcome-seen-v1";

type Gate = "checking" | "welcome" | "login" | "permission" | "ready";
type CreateStep = "pick" | "building" | "review" | "ready" | null;

export default function App() {
  const [fontsLoaded, fontError] = useFonts({
    Figtree_400Regular,
    Figtree_500Medium,
    Figtree_600SemiBold,
    Figtree_700Bold,
    Figtree_800ExtraBold,
  });
  const [gate, setGate] = useState<Gate>("checking");
  const [tab, setTab] = useState<AppTab>("albums");
  const [createStep, setCreateStep] = useState<CreateStep>(null);
  const [album, setAlbum] = useState<ReviewData | null>(null);
  const [finalPhotos, setFinalPhotos] = useState<FinalPhoto[] | null>(null);
  const [permissionBusy, setPermissionBusy] = useState(false);
  const [permissionMessage, setPermissionMessage] = useState<string | null>(null);
  const [buildMessage, setBuildMessage] = useState<string | null>(null);

  useEffect(() => {
    void SecureStore.getItemAsync(WELCOME_SEEN_KEY)
      .then((value) => setGate(value === "yes" ? "ready" : "welcome"))
      .catch(() => setGate("welcome"));
  }, []);

  const finishGate = useCallback(() => {
    setGate("ready");
    void SecureStore.setItemAsync(WELCOME_SEEN_KEY, "yes").catch(() => undefined);
  }, []);

  const requestPhotoPermission = useCallback(async () => {
    setPermissionBusy(true);
    setPermissionMessage(null);
    try {
      const permission = await MediaLibrary.requestPermissionsAsync();
      if (permission.status === "granted") finishGate();
      else setPermissionMessage("Photo access wasn’t allowed. You can continue and enable it later.");
    } catch {
      setPermissionMessage("We couldn’t open the photo permission. You can continue and try again later.");
    } finally {
      setPermissionBusy(false);
    }
  }, [finishGate]);

  const processPhotos = useCallback(async (next: PickedPhoto[]) => {
    if (next.length === 0) {
      setCreateStep("pick");
      return;
    }

    setCreateStep("building");
    setBuildMessage(null);
    try {
      const built = await buildAlbum(next);
      setAlbum(built);
      setCreateStep("review");
    } catch {
      setBuildMessage(copy.start.buildError);
      setCreateStep(null);
    }
  }, []);

  const resetCreateFlow = useCallback(() => {
    setCreateStep(null);
    setAlbum(null);
    setFinalPhotos(null);
    setTab("albums");
  }, []);

  if ((!fontsLoaded && !fontError) || gate === "checking") {
    return (
      <View style={styles.root}>
        <StatusBar style="dark" />
        <LoadingState helper={copy.states.safe} title={copy.states.preparing} />
      </View>
    );
  }

  if (gate === "welcome") {
    return <WelcomeScreen onContinue={() => setGate("login")} />;
  }

  if (gate === "login") {
    return <LoginScreen onContinue={() => setGate("permission")} />;
  }

  if (gate === "permission") {
    return (
      <StartScreen
        busy={permissionBusy}
        message={permissionMessage}
        onAllow={() => void requestPhotoPermission()}
        onSkip={finishGate}
      />
    );
  }

  if (createStep === "pick") {
    return (
      <GalleryGrid
        onBack={() => setCreateStep(null)}
        onConfirm={(picked) => void processPhotos(picked)}
      />
    );
  }

  if (createStep === "building") return <BuildingScreen />;

  if (createStep === "review" && album) {
    return (
      <ReviewScreen
        data={album}
        onBack={() => setCreateStep(null)}
        onFinalize={(picked) => {
          setFinalPhotos(picked);
          setCreateStep("ready");
        }}
      />
    );
  }

  if (createStep === "ready" && finalPhotos) {
    return (
      <FinalAlbum
        onBack={() => setCreateStep("review")}
        onRestart={resetCreateFlow}
        photos={finalPhotos}
      />
    );
  }

  return (
    <View style={styles.root}>
      <StatusBar style="dark" />
      <View style={styles.tabContent}>
        {tab === "albums" ? (
          <AlbumsScreen
            message={buildMessage}
            onCreate={() => {
              setBuildMessage(null);
              setCreateStep("pick");
            }}
          />
        ) : null}
        {tab === "photos" ? <PhotosScreen /> : null}
        {tab === "account" ? <AccountScreen /> : null}
      </View>
      <TabBar activeTab={tab} onChange={setTab} />
    </View>
  );
}

const styles = StyleSheet.create({
  root: { backgroundColor: colors.background, flex: 1 },
  tabContent: { flex: 1 },
});
