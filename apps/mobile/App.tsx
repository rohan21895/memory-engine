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
import { useCallback, useEffect, useRef, useState } from "react";
import { BackHandler, StyleSheet, View } from "react-native";
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
import { buildAlbum, type BuildAlbumProgress } from "./src/build-album";
import { buildFaceIndex, loadFaceIndex } from "./src/faces/face-index";
import GalleryGrid from "./src/import/GalleryGrid";
import { buildIndex, loadIndex } from "./src/import/photo-index";
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

type NavigationState = {
  tab: AppTab;
  createStep: CreateStep;
  libraryRoute: LibraryRoute;
  actionRoute: AlbumActionRoute;
  familyOpen: boolean;
  personToName: NamePersonTarget | null;
  sharedAlbum: SharedAlbumPreview | null;
};

const ALBUMS_ROOT: NavigationState = {
  tab: "albums",
  createStep: null,
  libraryRoute: null,
  actionRoute: null,
  familyOpen: false,
  personToName: null,
  sharedAlbum: null,
};

function isAlbumsRoot(navigation: NavigationState): boolean {
  return (
    navigation.tab === "albums" &&
    navigation.createStep === null &&
    navigation.libraryRoute === null &&
    navigation.actionRoute === null &&
    !navigation.familyOpen &&
    navigation.personToName === null &&
    navigation.sharedAlbum === null
  );
}

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
  const [navigation, setNavigation] = useState<NavigationState>(ALBUMS_ROOT);
  const navigationHistory = useRef<NavigationState[]>([]);
  const navigationRef = useRef(navigation);
  navigationRef.current = navigation;
  const buildRequest = useRef(0);
  const buildAbort = useRef<AbortController | null>(null);
  const [buildProgress, setBuildProgress] = useState<BuildAlbumProgress>({
    done: 0,
    total: 1,
    phase: "Preparing your photos",
  });
  const [album, setAlbum] = useState<ReviewData | null>(null);
  const [finalPhotos, setFinalPhotos] = useState<FinalPhoto[] | null>(null);
  const [permissionBusy, setPermissionBusy] = useState(false);
  const [permissionMessage, setPermissionMessage] = useState<string | null>(null);
  const [pickedPhotos, setPickedPhotos] = useState<PickedPhoto[]>([]);
  const [savedAlbums, setSavedAlbums] = useState<SavedAlbum[]>([]);
  const [currentAlbumId, setCurrentAlbumId] = useState<string | null>(null);
  const savedAlbumsRef = useRef(savedAlbums);
  savedAlbumsRef.current = savedAlbums;
  // The album this build session has already saved, if any. Held in a ref so a
  // second finalize (Back from Album Ready, or a double tap) sees it
  // synchronously and updates that album instead of minting a duplicate.
  const sessionAlbumId = useRef<string | null>(null);
  const { actionRoute, createStep, familyOpen, libraryRoute, personToName, sharedAlbum, tab } = navigation;

  const pushNavigation = useCallback((update: Partial<NavigationState>) => {
    setNavigation((current) => {
      navigationHistory.current.push(current);
      return { ...current, ...update };
    });
  }, []);

  const replaceNavigation = useCallback((update: Partial<NavigationState>) => {
    setNavigation((current) => ({ ...current, ...update }));
  }, []);

  const invalidateBuild = useCallback(() => {
    buildRequest.current += 1;
    buildAbort.current?.abort();
    buildAbort.current = null;
  }, []);

  const cancelBuild = useCallback(() => {
    invalidateBuild();
    setNavigation((current) => {
      while (navigationHistory.current.length > 0) {
        const candidate = navigationHistory.current.pop()!;
        if (candidate.createStep === "pick") return candidate;
      }
      return { ...current, createStep: "pick" };
    });
  }, [invalidateBuild]);

  const closeActionFlow = useCallback(() => {
    setNavigation((current) => {
      let target: NavigationState = { ...current, actionRoute: null };
      while (navigationHistory.current.length > 0) {
        const candidate = navigationHistory.current.pop()!;
        target = candidate;
        if (candidate.actionRoute === null) break;
      }
      return target;
    });
  }, []);

  const popNavigation = useCallback(() => {
    const current = navigationRef.current;
    if (current.createStep === "building") {
      cancelBuild();
      return;
    }
    // The share/print confirmations are terminal: Back means "done", not
    // "reopen the sheet I just completed".
    if (current.actionRoute?.screen === "share-sent" || current.actionRoute?.screen === "print-done") {
      closeActionFlow();
      return;
    }
    setNavigation(() => navigationHistory.current.pop() ?? ALBUMS_ROOT);
  }, [cancelBuild, closeActionFlow]);

  const goToAlbumsRoot = useCallback(() => {
    invalidateBuild();
    navigationHistory.current = [];
    setNavigation(ALBUMS_ROOT);
  }, [invalidateBuild]);

  useEffect(() => {
    void SecureStore.getItemAsync(WELCOME_SEEN_KEY)
      .then((value) => setGate(value === "yes" ? "ready" : "welcome"))
      .catch(() => setGate("welcome"));
  }, []);

  useEffect(() => {
    void loadAlbums().then(setSavedAlbums).catch(() => setSavedAlbums([]));
  }, []);

  useEffect(() => {
    void (async () => {
      await Promise.all([loadIndex(), loadFaceIndex()]);
      const permission = await MediaLibrary.getPermissionsAsync();
      if (permission.status !== "granted") return;
      await Promise.all([buildIndex(), buildFaceIndex()]);
    })().catch(() => undefined);
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
      replaceNavigation({ createStep: "pick" });
      return;
    }

    buildAbort.current?.abort();
    const controller = new AbortController();
    buildAbort.current = controller;
    const request = ++buildRequest.current;
    // A new build is a new album: drop the previous session's result so
    // finalize can't update — or re-render — an album from an earlier build.
    sessionAlbumId.current = null;
    setCurrentAlbumId(null);
    setFinalPhotos(null);
    setAlbum(null);
    setPickedPhotos(next);
    setBuildProgress({
      done: 0,
      total: Math.max(1, next.length + 1),
      phase: `Looking at 0 of ${next.length.toLocaleString()} photos`,
    });
    pushNavigation({ createStep: "building" });
    try {
      const built = await buildAlbum(next, 24, {
        signal: controller.signal,
        onProgress: (progress) => {
          if (request === buildRequest.current && !controller.signal.aborted) {
            setBuildProgress(progress);
          }
        },
      });
      if (request !== buildRequest.current) return;
      setAlbum(built);
      replaceNavigation({ createStep: "review" });
    } catch {
      if (
        request === buildRequest.current &&
        !controller.signal.aborted
      ) {
        replaceNavigation({ createStep: "error" });
      }
    } finally {
      if (buildAbort.current === controller) buildAbort.current = null;
    }
  }, [pushNavigation, replaceNavigation]);

  const finalizeAlbum = useCallback(async (photos: FinalPhoto[]) => {
    if (!album || photos.length === 0) return;
    const timestamps = pickedPhotos
      .map((photo) => photo.creationTime)
      .filter((value): value is number => typeof value === "number" && Number.isFinite(value));
    const now = Date.now();
    // Finalizing twice in one build session is an update, not a new album.
    // album_id alone can't decide this: it hashes the picked ids, so a genuine
    // rebuild of the same photos would collide with the earlier album.
    const existingId = sessionAlbumId.current;
    const id = existingId ?? `${album.album_id}-${now}`;
    sessionAlbumId.current = id;
    const previous = existingId
      ? savedAlbumsRef.current.find((candidate) => candidate.id === existingId)
      : undefined;
    const saved: SavedAlbum = {
      id,
      // Keep any title the user typed on Album Ready before stepping back.
      title: previous?.title ?? suggestedAlbumTitle(pickedPhotos),
      coverUri: photos[0]?.uri ?? "",
      photoIds: photos.map((photo) => photo.media_id),
      photos,
      reviewData: album,
      dateRange: timestamps.length > 0 ? { start: Math.min(...timestamps), end: Math.max(...timestamps) } : {},
      createdAt: previous?.createdAt ?? now,
      updatedAt: now,
    };
    setFinalPhotos(photos);
    setCurrentAlbumId(id);
    setSavedAlbums(await saveAlbum(saved));
    pushNavigation({ createStep: "ready" });
  }, [album, pickedPhotos, pushNavigation]);

  // Everything one album creation owns. Cleared on both ends of the flow so a
  // second album never inherits the first one's picks, build, or saved id.
  const clearCreateSession = useCallback(() => {
    invalidateBuild();
    sessionAlbumId.current = null;
    setAlbum(null);
    setFinalPhotos(null);
    setPickedPhotos([]);
    setCurrentAlbumId(null);
  }, [invalidateBuild]);

  const startCreateFlow = useCallback(() => {
    clearCreateSession();
    pushNavigation({ createStep: "pick" });
  }, [clearCreateSession, pushNavigation]);

  const resetCreateFlow = useCallback(() => {
    clearCreateSession();
    goToAlbumsRoot();
  }, [clearCreateSession, goToAlbumsRoot]);

  useEffect(() => {
    const subscription = BackHandler.addEventListener("hardwareBackPress", () => {
      if (gate === "checking") return true;
      if (gate === "permission") {
        setGate("login");
        return true;
      }
      if (gate === "login") {
        setGate("welcome");
        return true;
      }
      if (gate === "welcome") return true;

      const current = navigationRef.current;
      if (isAlbumsRoot(current)) return false;
      if (
        current.tab !== "albums" &&
        current.createStep === null &&
        current.libraryRoute === null &&
        current.actionRoute === null &&
        !current.familyOpen &&
        current.personToName === null &&
        current.sharedAlbum === null
      ) {
        goToAlbumsRoot();
      } else {
        popNavigation();
      }
      return true;
    });
    return () => subscription.remove();
  }, [gate, goToAlbumsRoot, popNavigation]);

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

  if (familyOpen) return <FamilyScreen onBack={popNavigation} />;

  if (personToName) return <NamePersonScreen onBack={popNavigation} person={personToName} />;

  if (sharedAlbum) return <SharedAlbumScreen album={sharedAlbum} onBack={popNavigation} onShare={() => undefined} />;

  if (actionRoute && actionAlbum) {
    if (actionRoute.screen === "manage") {
      return <ManageAlbumSheet album={actionAlbum} onBack={popNavigation} onDelete={() => pushNavigation({ actionRoute: { ...actionRoute, screen: "delete" } })} onRename={(title) => { void renameAlbum(actionAlbum.id, title).then((next) => { setSavedAlbums(next); closeActionFlow(); }).catch(closeActionFlow); }} />;
    }
    if (actionRoute.screen === "delete") {
      return <DeleteAlbumScreen albumTitle={actionAlbum.title} onBack={popNavigation} onDelete={() => { void deleteAlbum(actionAlbum.id).then((next) => { setSavedAlbums(next); resetCreateFlow(); }).catch(closeActionFlow); }} />;
    }
    if (actionRoute.screen === "share") {
      return <ShareSheet albumTitle={actionAlbum.title} onBack={popNavigation} onSent={(names) => pushNavigation({ actionRoute: { ...actionRoute, names, screen: "share-sent" } })} />;
    }
    if (actionRoute.screen === "share-sent") {
      return <ShareSentScreen albumTitle={actionAlbum.title} names={actionRoute.names} onDone={closeActionFlow} />;
    }
    if (actionRoute.screen === "print") {
      return <PrintOrderScreen album={actionAlbum} onBack={popNavigation} onOrdered={(size, total) => pushNavigation({ actionRoute: { ...actionRoute, screen: "print-done", size, total } })} onPreview={(size, total) => pushNavigation({ actionRoute: { ...actionRoute, screen: "print-preview", size, total } })} />;
    }
    if (actionRoute.screen === "print-preview") {
      return <PrintPreviewScreen album={actionAlbum} onBack={popNavigation} onContinue={() => pushNavigation({ actionRoute: { ...actionRoute, screen: "print-done" } })} size={actionRoute.size} total={actionRoute.total} />;
    }
    if (actionRoute.screen === "print-done") {
      return <PrintOrderedScreen albumTitle={actionAlbum.title} onDone={closeActionFlow} size={actionRoute.size} total={actionRoute.total} />;
    }
  }

  if (libraryRoute?.screen === "slideshow" && routedAlbum) {
    return <Slideshow album={routedAlbum} onBack={popNavigation} />;
  }

  if (libraryRoute?.screen === "detail" && routedAlbum) {
    return (
      <AlbumDetailScreen
        album={routedAlbum}
        onBack={popNavigation}
        onManage={() => pushNavigation({ actionRoute: { albumId: routedAlbum.id, origin: "detail", screen: "manage" } })}
        onPlay={() => pushNavigation({ libraryRoute: { albumId: routedAlbum.id, screen: "slideshow" } })}
        onPrint={() => pushNavigation({ actionRoute: { albumId: routedAlbum.id, origin: "detail", screen: "print" } })}
        onShare={() => pushNavigation({ actionRoute: { albumId: routedAlbum.id, origin: "detail", screen: "share" } })}
      />
    );
  }

  if (createStep === "pick" || createStep === "building") {
    return (
      <View style={styles.root}>
        <GalleryGrid
          initialSelection={pickedPhotos}
          onBack={popNavigation}
          onConfirm={(picked) => void processPhotos(picked)}
        />
        {createStep === "building" ? (
          <View style={styles.buildOverlay}>
            <BuildingScreen
              onCancel={cancelBuild}
              progress={buildProgress}
            />
          </View>
        ) : null}
      </View>
    );
  }

  if (createStep === "error") {
    return <BuildErrorScreen onBack={resetCreateFlow} onRetry={() => void processPhotos(pickedPhotos)} />;
  }

  if (createStep === "review" && album) {
    return (
      <ReviewScreen
        data={album}
        onBack={popNavigation}
        onFinalize={(picked) => void finalizeAlbum(picked)}
      />
    );
  }

  if (createStep === "ready" && finalPhotos && currentAlbum) {
    return (
      <FinalAlbum
        onBack={popNavigation}
        onDone={resetCreateFlow}
        onOpen={() => {
          pushNavigation({ createStep: null, libraryRoute: { albumId: currentAlbum.id, screen: "detail" } });
        }}
        onPlay={() => {
          pushNavigation({ createStep: null, libraryRoute: { albumId: currentAlbum.id, screen: "slideshow" } });
        }}
        onRestart={resetCreateFlow}
        onPrint={() => pushNavigation({ actionRoute: { albumId: currentAlbum.id, origin: "ready", screen: "print" } })}
        onShare={() => pushNavigation({ actionRoute: { albumId: currentAlbum.id, origin: "ready", screen: "share" } })}
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
            onCreate={startCreateFlow}
            onOpen={(selected) => pushNavigation({ libraryRoute: { albumId: selected.id, screen: "detail" } })}
            onOpenShared={(selected) => pushNavigation({ sharedAlbum: selected })}
          />
        ) : null}
        {tab === "photos" ? <PhotosScreen onNamePerson={(person) => pushNavigation({ personToName: person })} /> : null}
        {tab === "account" ? <AccountScreen albumCount={savedAlbums.length} onFamily={() => pushNavigation({ familyOpen: true })} /> : null}
      </View>
      <TabBar
        activeTab={tab}
        onChange={(next) => {
          if (next === "albums") goToAlbumsRoot();
          else replaceNavigation({ ...ALBUMS_ROOT, tab: next });
        }}
      />
    </View>
  );
}

export default function App() {
  return <SafeAreaProvider><PhoteoApp /></SafeAreaProvider>;
}

const styles = StyleSheet.create({
  buildOverlay: { bottom: 0, left: 0, position: "absolute", right: 0, top: 0 },
  root: { backgroundColor: colors.background, flex: 1 },
  tabContent: { flex: 1 },
});
