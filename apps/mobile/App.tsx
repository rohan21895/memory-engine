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
import { SafeAreaProvider } from "react-native-safe-area-context";

import { AlbumDetailScreen } from "./src/albums/AlbumDetailScreen";
import {
  DeleteAlbumScreen,
  ManageAlbumSheet,
  PrintOrderedScreen,
  PrintOrderScreen,
  PrintPreviewScreen,
  ShareSentScreen,
  ShareSheet,
  SharedAlbumScreen,
} from "./src/albums/AlbumActionScreens";
import {
  deleteAlbum,
  loadAlbums,
  renameAlbum,
  saveAlbum,
  type SavedAlbum,
} from "./src/albums/album-store";
import { buildAlbum } from "./src/build-album";
import GalleryGrid from "./src/import/GalleryGrid";
import type { PickedPhoto } from "./src/import/picked-photo";
import FinalAlbum, { type FinalPhoto } from "./src/review/FinalAlbum";
import type { ReviewData } from "./src/review/mock-data";
import ReviewScreen from "./src/review/ReviewScreen";
import { Slideshow } from "./src/review/Slideshow";
import { TabBar, type AppTab } from "./src/ui/components/TabBar";
import { colors, copy, LoadingState } from "./src/ui";
import { AccountScreen } from "./src/ui/screens/AccountScreen";
import { AlbumsScreen, type SharedAlbumPreview } from "./src/ui/screens/AlbumsScreen";
import { BuildErrorScreen } from "./src/ui/screens/BuildErrorScreen";
import { BuildingScreen } from "./src/ui/screens/BuildingScreen";
import { FamilyScreen } from "./src/ui/screens/FamilyScreen";
import { LoginScreen } from "./src/ui/screens/LoginScreen";
import { NamePersonScreen, type NamePersonTarget } from "./src/ui/screens/NamePersonScreen";
import { PhotosScreen } from "./src/ui/screens/PhotosScreen";
import { StartScreen } from "./src/ui/screens/StartScreen";
import { WelcomeScreen } from "./src/ui/screens/WelcomeScreen";

const WELCOME_SEEN_KEY = "photeo-welcome-seen-v1";

type Gate = "checking" | "welcome" | "login" | "permission" | "ready";
type CreateStep = "pick" | "building" | "review" | "ready" | "error" | null;
type LibraryRoute = { albumId: string; screen: "detail" | "slideshow" } | null;
type ActionOrigin = "detail" | "ready";
type AlbumActionRoute =
  | { albumId: string; origin: ActionOrigin; screen: "manage" | "delete" | "share" | "print" }
  | { albumId: string; names: string[]; origin: ActionOrigin; screen: "share-sent" }
  | { albumId: string; origin: ActionOrigin; screen: "print-preview" | "print-done"; size: string; total: number }
  | null;

function suggestedAlbumTitle(photos: PickedPhoto[]) {
  const timestamp = photos.map((photo) => photo.creationTime).find((value): value is number => typeof value === "number" && Number.isFinite(value));
  if (!timestamp) return "My photo album";
  return `${new Date(timestamp).toLocaleDateString(undefined, { month: "long" })} memories`;
}

function PhoteoApp() {
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
  const [pickedPhotos, setPickedPhotos] = useState<PickedPhoto[]>([]);
  const [savedAlbums, setSavedAlbums] = useState<SavedAlbum[]>([]);
  const [currentAlbumId, setCurrentAlbumId] = useState<string | null>(null);
  const [libraryRoute, setLibraryRoute] = useState<LibraryRoute>(null);
  const [actionRoute, setActionRoute] = useState<AlbumActionRoute>(null);
  const [familyOpen, setFamilyOpen] = useState(false);
  const [personToName, setPersonToName] = useState<NamePersonTarget | null>(null);
  const [sharedAlbum, setSharedAlbum] = useState<SharedAlbumPreview | null>(null);

  useEffect(() => {
    void SecureStore.getItemAsync(WELCOME_SEEN_KEY)
      .then((value) => setGate(value === "yes" ? "ready" : "welcome"))
      .catch(() => setGate("welcome"));
  }, []);

  useEffect(() => {
    void loadAlbums().then(setSavedAlbums).catch(() => setSavedAlbums([]));
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

    setPickedPhotos(next);
    setCreateStep("building");
    try {
      const built = await buildAlbum(next);
      setAlbum(built);
      setCreateStep("review");
    } catch {
      setCreateStep("error");
    }
  }, []);

  const finalizeAlbum = useCallback(async (photos: FinalPhoto[]) => {
    if (!album || photos.length === 0) return;
    const timestamps = pickedPhotos
      .map((photo) => photo.creationTime)
      .filter((value): value is number => typeof value === "number" && Number.isFinite(value));
    const now = Date.now();
    const saved: SavedAlbum = {
      id: `${album.album_id}-${now}`,
      title: suggestedAlbumTitle(pickedPhotos),
      coverUri: photos[0]?.uri ?? "",
      photoIds: photos.map((photo) => photo.media_id),
      photos,
      reviewData: album,
      dateRange: timestamps.length > 0 ? { start: Math.min(...timestamps), end: Math.max(...timestamps) } : {},
      createdAt: now,
      updatedAt: now,
    };
    setFinalPhotos(photos);
    setCurrentAlbumId(saved.id);
    setSavedAlbums(await saveAlbum(saved));
    setCreateStep("ready");
  }, [album, pickedPhotos]);

  const resetCreateFlow = useCallback(() => {
    setCreateStep(null);
    setAlbum(null);
    setFinalPhotos(null);
    setPickedPhotos([]);
    setCurrentAlbumId(null);
    setActionRoute(null);
    setTab("albums");
  }, []);

  const currentAlbum = currentAlbumId
    ? savedAlbums.find((candidate) => candidate.id === currentAlbumId) ?? null
    : null;
  const routedAlbum = libraryRoute
    ? savedAlbums.find((candidate) => candidate.id === libraryRoute.albumId) ?? null
    : null;
  const actionAlbum = actionRoute
    ? savedAlbums.find((candidate) => candidate.id === actionRoute.albumId) ?? null
    : null;

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

  if (familyOpen) return <FamilyScreen onBack={() => setFamilyOpen(false)} />;

  if (personToName) return <NamePersonScreen onBack={() => setPersonToName(null)} person={personToName} />;

  if (sharedAlbum) return <SharedAlbumScreen album={sharedAlbum} onBack={() => setSharedAlbum(null)} onShare={() => undefined} />;

  if (actionRoute && actionAlbum) {
    if (actionRoute.screen === "manage") {
      return <ManageAlbumSheet album={actionAlbum} onBack={() => setActionRoute(null)} onDelete={() => setActionRoute({ ...actionRoute, screen: "delete" })} onRename={(title) => { void renameAlbum(actionAlbum.id, title).then((next) => { setSavedAlbums(next); setActionRoute(null); }).catch(() => setActionRoute(null)); }} />;
    }
    if (actionRoute.screen === "delete") {
      return <DeleteAlbumScreen albumTitle={actionAlbum.title} onBack={() => setActionRoute({ ...actionRoute, screen: "manage" })} onDelete={() => { void deleteAlbum(actionAlbum.id).then((next) => { setSavedAlbums(next); setActionRoute(null); setLibraryRoute(null); resetCreateFlow(); }).catch(() => setActionRoute(null)); }} />;
    }
    if (actionRoute.screen === "share") {
      return <ShareSheet albumTitle={actionAlbum.title} onBack={() => setActionRoute(null)} onSent={(names) => setActionRoute({ ...actionRoute, names, screen: "share-sent" })} />;
    }
    if (actionRoute.screen === "share-sent") {
      return <ShareSentScreen albumTitle={actionAlbum.title} names={actionRoute.names} onDone={() => setActionRoute(null)} />;
    }
    if (actionRoute.screen === "print") {
      return <PrintOrderScreen album={actionAlbum} onBack={() => setActionRoute(null)} onOrdered={(size, total) => setActionRoute({ ...actionRoute, screen: "print-done", size, total })} onPreview={(size, total) => setActionRoute({ ...actionRoute, screen: "print-preview", size, total })} />;
    }
    if (actionRoute.screen === "print-preview") {
      return <PrintPreviewScreen album={actionAlbum} onBack={() => setActionRoute({ albumId: actionAlbum.id, origin: actionRoute.origin, screen: "print" })} onContinue={() => setActionRoute({ ...actionRoute, screen: "print-done" })} size={actionRoute.size} total={actionRoute.total} />;
    }
    if (actionRoute.screen === "print-done") {
      return <PrintOrderedScreen albumTitle={actionAlbum.title} onDone={() => setActionRoute(null)} size={actionRoute.size} total={actionRoute.total} />;
    }
  }

  if (libraryRoute?.screen === "slideshow" && routedAlbum) {
    return <Slideshow album={routedAlbum} onBack={() => setLibraryRoute({ albumId: routedAlbum.id, screen: "detail" })} />;
  }

  if (libraryRoute?.screen === "detail" && routedAlbum) {
    return (
      <AlbumDetailScreen
        album={routedAlbum}
        onBack={() => setLibraryRoute(null)}
        onManage={() => setActionRoute({ albumId: routedAlbum.id, origin: "detail", screen: "manage" })}
        onPlay={() => setLibraryRoute({ albumId: routedAlbum.id, screen: "slideshow" })}
        onPrint={() => setActionRoute({ albumId: routedAlbum.id, origin: "detail", screen: "print" })}
        onShare={() => setActionRoute({ albumId: routedAlbum.id, origin: "detail", screen: "share" })}
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

  if (createStep === "error") {
    return <BuildErrorScreen onBack={resetCreateFlow} onRetry={() => void processPhotos(pickedPhotos)} />;
  }

  if (createStep === "review" && album) {
    return (
      <ReviewScreen
        data={album}
        onBack={() => setCreateStep(null)}
        onFinalize={(picked) => void finalizeAlbum(picked)}
      />
    );
  }

  if (createStep === "ready" && finalPhotos && currentAlbum) {
    return (
      <FinalAlbum
        onBack={() => setCreateStep("review")}
        onDone={resetCreateFlow}
        onOpen={() => {
          setCreateStep(null);
          setLibraryRoute({ albumId: currentAlbum.id, screen: "detail" });
        }}
        onPlay={() => {
          setCreateStep(null);
          setLibraryRoute({ albumId: currentAlbum.id, screen: "slideshow" });
        }}
        onRestart={resetCreateFlow}
        onPrint={() => setActionRoute({ albumId: currentAlbum.id, origin: "ready", screen: "print" })}
        onShare={() => setActionRoute({ albumId: currentAlbum.id, origin: "ready", screen: "share" })}
        onTitleChange={(title) => {
          void renameAlbum(currentAlbum.id, title).then(setSavedAlbums).catch(() => undefined);
        }}
        photos={finalPhotos}
        title={currentAlbum.title}
      />
    );
  }

  return (
    <View style={styles.root}>
      <StatusBar style="dark" />
      <View style={styles.tabContent}>
        {tab === "albums" ? (
          <AlbumsScreen
            albums={savedAlbums}
            onCreate={() => {
              setCreateStep("pick");
            }}
            onOpen={(selected) => setLibraryRoute({ albumId: selected.id, screen: "detail" })}
            onOpenShared={setSharedAlbum}
          />
        ) : null}
        {tab === "photos" ? <PhotosScreen onNamePerson={setPersonToName} /> : null}
        {tab === "account" ? <AccountScreen albumCount={savedAlbums.length} onFamily={() => setFamilyOpen(true)} /> : null}
      </View>
      <TabBar activeTab={tab} onChange={(next) => { setLibraryRoute(null); setTab(next); }} />
    </View>
  );
}

export default function App() {
  return <SafeAreaProvider><PhoteoApp /></SafeAreaProvider>;
}

const styles = StyleSheet.create({
  root: { backgroundColor: colors.background, flex: 1 },
  tabContent: { flex: 1 },
});
