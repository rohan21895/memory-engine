import { Image } from "expo-image";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Pressable,
  StatusBar,
  StyleSheet,
  Text,
  useWindowDimensions,
  View,
} from "react-native";
import { Gesture, GestureDetector, GestureHandlerRootView } from "react-native-gesture-handler";
import Animated, {
  Easing,
  ReduceMotion,
  cancelAnimation,
  interpolate,
  useAnimatedStyle,
  useReducedMotion,
  useSharedValue,
  withSpring,
  withTiming,
  type SharedValue,
} from "react-native-reanimated";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { scheduleOnRN } from "react-native-worklets";

// @ts-expect-error TypeScript bundler resolution normally omits source extensions.
import { albumAnalysisProxy, thumbnailUri } from "../../modules/photeo-scan-service/src/index.ts";
import type { SavedAlbum } from "../albums/album-store";
import { fonts, spacing, typeScale } from "../ui";
import {
  adjacentPages,
  photoIndexForPage,
  quantizedThumbnailSize,
  type PresentationMode,
} from "./slideshow-model";

const PHOTO_DURATION_MS = 5200;
const FILMSTRIP_EDGE = 54;
const MODE_INDICATOR_TRAVEL = 82;
const MODE_GESTURE_DISTANCE = 156;
const PAGE_SPRING = { dampingRatio: 0.86, duration: 420 } as const;
const EASE_OUT = Easing.bezier(0.23, 1, 0.32, 1);

/**
 * The long edge the slideshow actually displays.
 *
 * Deliberately equal to ANALYSIS_PROXY_SIZE, so every photo in a built album is
 * already on disk as a proxy and the slideshow decodes nothing new. It is also
 * the reason the full-resolution original never reaches this screen: his library
 * holds 25-27 MiB DSLR JPEGs, and three of those mounted at once is the album
 * OOM in a different costume.
 */
const DISPLAY_EDGE = 1280;

const thumbnailCache = new Map<string, string | null>();
const thumbnailRequests = new Map<string, Promise<string | null>>();
const displayCache = new Map<string, string | null>();
const displayRequests = new Map<string, Promise<string | null>>();

/** Shared single-flight + memo, so three mounted pages cause one native call. */
function cachedUri(
  cache: Map<string, string | null>,
  requests: Map<string, Promise<string | null>>,
  key: string,
  load: () => Promise<string | null>,
): Promise<string | null> {
  if (cache.has(key)) return Promise.resolve(cache.get(key) ?? null);
  const pending = requests.get(key);
  if (pending) return pending;

  const request = load()
    .then((uri) => uri)
    .catch(() => null)
    .then((uri) => {
      cache.set(key, uri);
      requests.delete(key);
      return uri;
    });
  requests.set(key, request);
  return request;
}

function requestThumbnail(assetId: string, displaySize: number): Promise<string | null> {
  const size = quantizedThumbnailSize(displaySize);
  const key = `${assetId}:${size}`;
  return cachedUri(thumbnailCache, thumbnailRequests, key, () => thumbnailUri(assetId, size));
}

function requestDisplayPhoto(assetId: string): Promise<string | null> {
  return cachedUri(displayCache, displayRequests, assetId, async () => {
    const proxy = await albumAnalysisProxy(assetId, DISPLAY_EDGE);
    return proxy?.uri ?? null;
  });
}

function useThumbnail(assetId: string | undefined, displaySize: number): string | undefined {
  const size = quantizedThumbnailSize(displaySize);
  const key = assetId ? `${assetId}:${size}` : "";
  const [uri, setUri] = useState<string | undefined>(() => {
    if (!key) return undefined;
    return thumbnailCache.get(key) ?? undefined;
  });

  useEffect(() => {
    if (!assetId) {
      setUri(undefined);
      return;
    }

    const cached = thumbnailCache.get(key);
    setUri(cached ?? undefined);
    if (cached !== undefined) return;

    let live = true;
    void requestThumbnail(assetId, displaySize).then((next) => {
      if (live) setUri(next ?? undefined);
    });
    return () => {
      live = false;
    };
  }, [assetId, displaySize, key]);

  return uri;
}

/**
 * The display-sized photo for a slide.
 *
 * Every mounted page asks for this, not just the current one, so the next and
 * previous slides are already decoded before he swipes. That prefetch is half
 * the fix for "refreshes a lot"; the other half is that the page below now
 * keeps ONE image view for its whole life instead of swapping sources.
 */
function useDisplayPhoto(assetId: string | undefined): string | undefined {
  const [uri, setUri] = useState<string | undefined>(() =>
    assetId ? displayCache.get(assetId) ?? undefined : undefined,
  );

  useEffect(() => {
    if (!assetId) {
      setUri(undefined);
      return;
    }

    const cached = displayCache.get(assetId);
    setUri(cached ?? undefined);
    if (cached !== undefined) return;

    let live = true;
    void requestDisplayPhoto(assetId).then((next) => {
      if (live) setUri(next ?? undefined);
    });
    return () => {
      live = false;
    };
  }, [assetId]);

  return uri;
}

function pageSnap(
  target: number,
  reducedMotion: boolean,
  velocity: number,
  finished: (didFinish?: boolean) => void,
) {
  "worklet";
  if (reducedMotion) {
    return withTiming(target, { duration: 120, easing: EASE_OUT, reduceMotion: ReduceMotion.System }, finished);
  }
  return withSpring(target, { ...PAGE_SPRING, velocity }, finished);
}

function IconButton({
  accessibilityLabel,
  disabled = false,
  label,
  onPress,
}: {
  accessibilityLabel: string;
  disabled?: boolean;
  label: string;
  onPress: () => void;
}) {
  return (
    <Pressable
      accessibilityLabel={accessibilityLabel}
      accessibilityRole="button"
      accessibilityState={{ disabled }}
      disabled={disabled}
      hitSlop={6}
      onPress={onPress}
      pressRetentionOffset={12}
      style={({ pressed }) => [styles.iconButton, pressed && !disabled ? styles.controlPressed : null]}
    >
      <Text style={styles.iconButtonText}>{label}</Text>
    </Pressable>
  );
}

function ModeSwitch({
  mode,
  onChange,
  presentation,
}: {
  mode: PresentationMode;
  onChange: (mode: PresentationMode) => void;
  presentation: SharedValue<number>;
}) {
  const indicatorStyle = useAnimatedStyle(() => ({
    transform: [{ translateX: presentation.get() * MODE_INDICATOR_TRAVEL }],
  }));

  return (
    <View accessibilityLabel="Presentation mode" accessibilityRole="radiogroup" style={styles.modeSwitch}>
      <Animated.View style={[styles.modeIndicator, indicatorStyle]} />
      {(["classic", "cinema"] as const).map((option) => (
        <Pressable
          accessibilityLabel={`${option === "classic" ? "Classic" : "Cinema"} mode`}
          accessibilityRole="radio"
          accessibilityState={{ checked: mode === option }}
          // Vertical only. The two options sit shoulder to shoulder inside the
          // switch, so horizontal slop would overlap and the later-rendered one
          // would silently win every tap in the seam.
          hitSlop={{ bottom: 8, top: 8 }}
          key={option}
          onPress={() => onChange(option)}
          style={styles.modeOption}
        >
          <Text style={[styles.modeLabel, mode === option ? styles.modeLabelActive : null]}>
            {option === "classic" ? "Classic" : "Cinema"}
          </Text>
        </Pressable>
      ))}
    </View>
  );
}

function PhotoPage({
  album,
  height,
  logicalPage,
  page,
  playbackProgress,
  presentation,
  reducedMotion,
  transitionDirection,
  transitionProgress,
  width,
}: {
  album: SavedAlbum;
  height: number;
  logicalPage: number;
  page: number;
  playbackProgress: SharedValue<number>;
  presentation: SharedValue<number>;
  reducedMotion: boolean;
  transitionDirection: SharedValue<number>;
  transitionProgress: SharedValue<number>;
  width: number;
}) {
  const photoIndex = photoIndexForPage(logicalPage, album.photos.length);
  const photo = album.photos[photoIndex];
  const isCurrent = logicalPage === page;
  const thumbnail = useThumbnail(photo?.media_id, width);
  // Requested by every mounted page, not just the current one: that is the
  // prefetch, and it is why arriving at a slide no longer starts a decode.
  const display = useDisplayPhoto(photo?.media_id);

  const pageStyle = useAnimatedStyle(() => {
    const progress = transitionProgress.get();
    const direction = transitionDirection.get();
    const isOutgoing = logicalPage === page && direction !== 0;
    const isIncoming = logicalPage === page + direction && direction !== 0;
    const mode = presentation.get();
    const drift = reducedMotion || !isCurrent ? 0 : playbackProgress.get();
    const classicScale = interpolate(mode, [0, 1], [0.88, 1.015]);
    const cinemaScale = 1 + drift * 0.012 * mode;

    return {
      opacity: isOutgoing ? 1 - progress : isIncoming ? progress : 1,
      transform: [
        { translateX: isIncoming ? -direction * width : 0 },
        { translateX: drift * -5 * mode },
        { translateY: drift * -3 * mode },
        { scale: classicScale * cinemaScale },
      ],
    };
  }, [isCurrent, logicalPage, page, reducedMotion, width]);

  if (!photo) return null;

  return (
    <Animated.View
      accessibilityElementsHidden={!isCurrent}
      importantForAccessibility={isCurrent ? "yes" : "no-hide-descendants"}
      style={[styles.photoPage, { height, left: logicalPage * width, width }, pageStyle]}
    >
      <View style={styles.photoSurface}>
        {/*
          ONE image view, one recycling key, for the whole life of this page.
          It previously rendered two different image elements -- a full-res one
          while current, a thumbnail one otherwise -- with DIFFERENT recyclingKeys.
          expo-image tears down and rebuilds the native view whenever that key
          changes, so every advance destroyed and rebuilt two views, and the slide
          being left behind was actively downgraded from a decoded photo back to a
          blurry thumbnail. That is the "refreshes a lot, sometimes refreshes and
          thumbnail is visible" he reported; it was structural, not slow I/O.

          The source is now the same display proxy whether or not the page is
          current, so becoming current changes nothing about what is loaded.
          `photo.uri` remains only as a last resort for a device with no native
          module -- never as the routine path, which is what the OOM taught.
        */}
        <Image
          accessibilityLabel={`Photo ${photoIndex + 1} of ${album.photos.length}`}
          accessibilityRole="image"
          cachePolicy="memory-disk"
          contentFit="contain"
          placeholder={thumbnail}
          placeholderContentFit="contain"
          recyclingKey={`slideshow:${photo.media_id}`}
          source={display ?? thumbnail ?? photo.uri}
          style={StyleSheet.absoluteFill}
          transition={reducedMotion ? 0 : 160}
        />
        <Animated.View pointerEvents="none" style={styles.classicFrame} />
      </View>
    </Animated.View>
  );
}

function FilmstripPhoto({
  active,
  assetId,
  label,
  onPress,
}: {
  active: boolean;
  assetId: string;
  label: string;
  onPress?: () => void;
}) {
  const thumbnail = useThumbnail(assetId, FILMSTRIP_EDGE);
  return (
    <Pressable
      accessibilityLabel={label}
      accessibilityRole={onPress ? "button" : "image"}
      disabled={!onPress}
      onPress={onPress}
      style={[styles.filmstripCell, active ? styles.filmstripCellActive : null]}
    >
      {thumbnail ? (
        <Image
          cachePolicy="memory-disk"
          contentFit="cover"
          recyclingKey={`slideshow-filmstrip:${assetId}`}
          source={thumbnail}
          style={StyleSheet.absoluteFill}
          transition={0}
        />
      ) : (
        <View style={styles.thumbnailFallback} />
      )}
      {active ? <View pointerEvents="none" style={styles.filmstripGlow} /> : null}
    </Pressable>
  );
}

export function Slideshow({ album, onBack }: { album: SavedAlbum; onBack: () => void }) {
  const { height, width } = useWindowDimensions();
  const insets = useSafeAreaInsets();
  const reducedMotion = useReducedMotion();
  const count = album.photos.length;
  const [page, setPage] = useState(0);
  const [mode, setMode] = useState<PresentationMode>("cinema");
  const [playing, setPlaying] = useState(true);
  const [transitioning, setTransitioning] = useState(false);
  const [clockNonce, setClockNonce] = useState(0);
  const playbackPage = useRef(0);

  const trackX = useSharedValue(0);
  const gestureOriginX = useSharedValue(0);
  const gestureOriginMode = useSharedValue(1);
  const gestureAxis = useSharedValue(0);
  const gestureActive = useSharedValue(0);
  const presentation = useSharedValue(1);
  const playbackProgress = useSharedValue(0);
  const transitionProgress = useSharedValue(0);
  const transitionDirection = useSharedValue(0);

  const currentIndex = count > 0 ? photoIndexForPage(page, count) : 0;
  const currentPhoto = album.photos[currentIndex];
  const currentThumbnail = useThumbnail(currentPhoto?.media_id, width);
  const mountedPages = useMemo(() => adjacentPages(page, count), [count, page]);

  useEffect(() => {
    trackX.set(-page * width);
  }, [page, trackX, width]);

  const resumePlaybackAfterGesture = useCallback(() => {
    setClockNonce((value) => value + 1);
  }, []);

  const commitHorizontalGesture = useCallback((target: number) => {
    setPage(target);
    setClockNonce((value) => value + 1);
  }, []);

  const commitVerticalGesture = useCallback((nextMode: PresentationMode) => {
    setMode(nextMode);
    setClockNonce((value) => value + 1);
  }, []);

  const finishProgrammaticPage = useCallback((target: number) => {
    setPage(target);
    setTransitioning(false);
  }, []);

  const goBy = useCallback((direction: -1 | 1) => {
    if (count < 2 || transitioning || gestureActive.get() === 1) return;
    const target = page + direction;
    setTransitioning(true);
    transitionDirection.set(direction);
    transitionProgress.set(0);
    transitionProgress.set(withTiming(1, {
      duration: reducedMotion ? 150 : 340,
      easing: EASE_OUT,
      reduceMotion: ReduceMotion.System,
    }, (finished) => {
      "worklet";
      if (!finished) return;
      trackX.set(-target * width);
      transitionProgress.set(0);
      transitionDirection.set(0);
      scheduleOnRN(finishProgrammaticPage, target);
    }));
  }, [
    count,
    finishProgrammaticPage,
    gestureActive,
    page,
    reducedMotion,
    trackX,
    transitionDirection,
    transitionProgress,
    transitioning,
    width,
  ]);

  useEffect(() => {
    if (playbackPage.current !== page) {
      playbackPage.current = page;
      playbackProgress.set(0);
    }
    if (!playing || transitioning || count < 2) {
      cancelAnimation(playbackProgress);
      return;
    }

    const from = Math.min(1, Math.max(0, playbackProgress.get()));
    const remaining = Math.max(80, Math.round((1 - from) * PHOTO_DURATION_MS));
    playbackProgress.set(withTiming(1, {
      duration: remaining,
      easing: Easing.linear,
      reduceMotion: ReduceMotion.Never,
    }));
    const timer = setTimeout(() => {
      if (gestureActive.get() !== 1) goBy(1);
    }, remaining);
    return () => {
      clearTimeout(timer);
      cancelAnimation(playbackProgress);
    };
  }, [
    clockNonce,
    count,
    gestureActive,
    goBy,
    page,
    playbackProgress,
    playing,
    transitioning,
  ]);

  const switchMode = useCallback((nextMode: PresentationMode) => {
    const target = nextMode === "cinema" ? 1 : 0;
    presentation.set(withTiming(target, {
      duration: reducedMotion ? 100 : 180,
      easing: EASE_OUT,
      reduceMotion: ReduceMotion.System,
    }));
    setMode(nextMode);
  }, [presentation, reducedMotion]);

  const panGesture = useMemo(() => Gesture.Pan()
    .enabled(!transitioning && count > 0)
    .minDistance(5)
    .onBegin(() => {
      "worklet";
      gestureOriginX.set(trackX.get());
      gestureOriginMode.set(presentation.get());
      gestureAxis.set(0);
      gestureActive.set(1);
      cancelAnimation(playbackProgress);
    })
    .onUpdate((event) => {
      "worklet";
      if (gestureAxis.get() === 0) {
        const distance = Math.abs(event.translationX) + Math.abs(event.translationY);
        if (distance < 8) return;
        gestureAxis.set(Math.abs(event.translationX) >= Math.abs(event.translationY) ? 1 : 2);
      }

      if (gestureAxis.get() === 1) {
        trackX.set(gestureOriginX.get() + event.translationX);
        return;
      }

      const next = gestureOriginMode.get() - event.translationY / MODE_GESTURE_DISTANCE;
      presentation.set(Math.max(0, Math.min(1, next)));
    })
    .onEnd((event) => {
      "worklet";
      if (gestureAxis.get() === 1) {
        const advances = count > 1 && (
          Math.abs(event.translationX) > width * 0.17 || Math.abs(event.velocityX) > 520
        );
        const direction = advances ? (event.translationX < 0 ? 1 : -1) : 0;
        const target = page + direction;
        trackX.set(pageSnap(-target * width, reducedMotion, -event.velocityX, (finished) => {
          "worklet";
          if (!finished) return;
          gestureActive.set(0);
          scheduleOnRN(commitHorizontalGesture, target);
        }));
        return;
      }

      const projected = presentation.get() - event.velocityY / 1400;
      const target = projected >= 0.5 ? 1 : 0;
      presentation.set(pageSnap(target, reducedMotion, -event.velocityY / 1000, (finished) => {
        "worklet";
        if (!finished) return;
        gestureActive.set(0);
        scheduleOnRN(commitVerticalGesture, target === 1 ? "cinema" : "classic");
      }));
    })
    .onFinalize((_event, success) => {
      "worklet";
      if (success) return;
      gestureActive.set(0);
      trackX.set(-page * width);
      presentation.set(mode === "cinema" ? 1 : 0);
      scheduleOnRN(resumePlaybackAfterGesture);
    }), [
    commitHorizontalGesture,
    commitVerticalGesture,
    count,
    gestureActive,
    gestureAxis,
    gestureOriginMode,
    gestureOriginX,
    mode,
    page,
    playbackProgress,
    presentation,
    reducedMotion,
    resumePlaybackAfterGesture,
    trackX,
    transitioning,
    width,
  ]);

  const trackStyle = useAnimatedStyle(() => ({
    transform: [{ translateX: trackX.get() }],
  }));
  const backdropStyle = useAnimatedStyle(() => {
    const cinema = presentation.get();
    const drift = reducedMotion ? 0 : playbackProgress.get();
    return {
      opacity: cinema,
      transform: [
        { translateX: drift * -7 * cinema },
        { translateY: drift * -4 * cinema },
        { scale: 1.12 + drift * 0.025 * cinema },
      ],
    };
  }, [reducedMotion]);
  const classicShadeStyle = useAnimatedStyle(() => ({
    opacity: 1 - presentation.get() * 0.92,
  }));
  const modeIndicatorHintStyle = useAnimatedStyle(() => ({
    opacity: interpolate(presentation.get(), [0, 0.5, 1], [0.64, 0.32, 0.64]),
  }));
  const progressStyle = useAnimatedStyle(() => {
    const progress = playbackProgress.get();
    return {
      opacity: playing ? 1 : 0.38,
      transform: [
        { translateX: -(1 - progress) * (width - spacing.lg * 2) / 2 },
        { scaleX: progress },
      ],
    };
  }, [playing, width]);

  const togglePlayback = useCallback(() => setPlaying((value) => !value), []);

  if (!currentPhoto) {
    return (
      <View style={styles.emptyRoot}>
        <StatusBar backgroundColor="#090909" barStyle="light-content" />
        <Pressable accessibilityLabel="Back to album" accessibilityRole="button" onPress={onBack} style={styles.emptyBack}>
          <Text style={styles.backText}>‹</Text>
        </Pressable>
        <Text style={styles.emptyTitle}>No photos to play</Text>
      </View>
    );
  }

  const previousIndex = photoIndexForPage(page - 1, count);
  const nextIndex = photoIndexForPage(page + 1, count);

  return (
    <GestureHandlerRootView style={styles.root}>
      <StatusBar backgroundColor="transparent" barStyle="light-content" translucent />
      <Animated.View pointerEvents="none" style={[StyleSheet.absoluteFill, styles.backdrop, backdropStyle]}>
        {currentThumbnail ? (
          <Image
            blurRadius={32}
            cachePolicy="memory-disk"
            contentFit="cover"
            recyclingKey={`slideshow-backdrop:${currentPhoto.media_id}`}
            source={currentThumbnail}
            style={StyleSheet.absoluteFill}
            transition={0}
          />
        ) : null}
      </Animated.View>
      <Animated.View pointerEvents="none" style={[StyleSheet.absoluteFill, styles.classicShade, classicShadeStyle]} />
      <View pointerEvents="none" style={styles.topVignette} />
      <View pointerEvents="none" style={styles.bottomVignette} />

      <GestureDetector gesture={panGesture}>
        <Animated.View accessibilityLabel="Slideshow. Swipe left or right for photos, up or down for presentation mode." style={StyleSheet.absoluteFill}>
          <Animated.View pointerEvents="none" style={[StyleSheet.absoluteFill, trackStyle]}>
            {mountedPages.map((logicalPage) => (
              <PhotoPage
                album={album}
                height={height}
                key={logicalPage}
                logicalPage={logicalPage}
                page={page}
                playbackProgress={playbackProgress}
                presentation={presentation}
                reducedMotion={reducedMotion}
                transitionDirection={transitionDirection}
                transitionProgress={transitionProgress}
                width={width}
              />
            ))}
          </Animated.View>

          <View style={[styles.header, { paddingTop: Math.max(insets.top, spacing.md) }]}>
            <Pressable
              accessibilityLabel="Back to album"
              accessibilityRole="button"
              hitSlop={6}
              onPress={onBack}
              style={({ pressed }) => [styles.backButton, pressed ? styles.controlPressed : null]}
            >
              <Text style={styles.backText}>‹</Text>
            </Pressable>
            <View style={styles.modeCluster}>
              <ModeSwitch mode={mode} onChange={switchMode} presentation={presentation} />
              <Animated.Text style={[styles.modeHint, modeIndicatorHintStyle]}>↕ swipe to switch</Animated.Text>
            </View>
            <View style={styles.headerBalance} />
          </View>

          <View style={[styles.controls, { paddingBottom: Math.max(insets.bottom + spacing.sm, spacing.lg) }]}>
            <View style={styles.progressTrack}>
              <Animated.View style={[styles.progressFill, progressStyle]} />
            </View>
            <Text numberOfLines={1} style={styles.title}>{album.title}</Text>
            <Text accessibilityLiveRegion="polite" style={styles.meta}>
              {`${currentIndex + 1} of ${count}  ·  ${mode === "cinema" ? "Cinema" : "Classic"}${playing ? "" : "  ·  Paused"}`}
            </Text>

            {count > 1 ? (
              <View style={styles.filmstrip}>
                <FilmstripPhoto
                  active={false}
                  assetId={album.photos[previousIndex].media_id}
                  label="Previous photo preview"
                  onPress={() => goBy(-1)}
                />
                <FilmstripPhoto
                  active
                  assetId={currentPhoto.media_id}
                  label={`Current photo, ${currentIndex + 1} of ${count}`}
                />
                <FilmstripPhoto
                  active={false}
                  assetId={album.photos[nextIndex].media_id}
                  label="Next photo preview"
                  onPress={() => goBy(1)}
                />
              </View>
            ) : null}

            <View style={styles.player}>
              <IconButton accessibilityLabel="Previous photo" disabled={count < 2} label="‹" onPress={() => goBy(-1)} />
              <Pressable
                accessibilityHint={playing ? "Stops on this photo" : "Continues from this point"}
                accessibilityLabel={playing ? "Pause slideshow" : "Play slideshow"}
                accessibilityRole="button"
                onPress={togglePlayback}
                style={({ pressed }) => [styles.playButton, pressed ? styles.playPressed : null]}
              >
                <Text style={styles.playIcon}>{playing ? "Ⅱ" : "▶"}</Text>
              </Pressable>
              <IconButton accessibilityLabel="Next photo" disabled={count < 2} label="›" onPress={() => goBy(1)} />
            </View>
          </View>
        </Animated.View>
      </GestureDetector>
    </GestureHandlerRootView>
  );
}

const styles = StyleSheet.create({
  backdrop: { backgroundColor: "#18120f" },
  backButton: {
    alignItems: "center",
    backgroundColor: "rgba(10,8,7,.48)",
    borderColor: "rgba(255,255,255,.18)",
    borderRadius: 23,
    borderWidth: 1,
    height: 46,
    justifyContent: "center",
    width: 46,
  },
  backText: { color: "#fff", fontFamily: fonts.regular, fontSize: 30, lineHeight: 32, marginTop: -2 },
  bottomVignette: {
    backgroundColor: "rgba(6,5,4,.60)",
    bottom: 0,
    height: "38%",
    left: 0,
    position: "absolute",
    right: 0,
  },
  classicFrame: {
    borderColor: "rgba(255,255,255,.16)",
    borderCurve: "continuous",
    borderRadius: 22,
    borderWidth: StyleSheet.hairlineWidth,
    bottom: 0,
    left: 0,
    position: "absolute",
    right: 0,
    top: 0,
  },
  classicShade: { backgroundColor: "#090909" },
  controlPressed: { opacity: 0.68 },
  controls: {
    alignItems: "center",
    bottom: 0,
    gap: spacing.xs,
    left: 0,
    paddingHorizontal: spacing.lg,
    position: "absolute",
    right: 0,
  },
  emptyBack: { left: spacing.md, position: "absolute", top: (StatusBar.currentHeight ?? 24) + spacing.sm },
  emptyRoot: { alignItems: "center", backgroundColor: "#090909", flex: 1, justifyContent: "center" },
  emptyTitle: { color: "rgba(255,255,255,.78)", fontFamily: fonts.bold, ...typeScale.label },
  filmstrip: { alignItems: "center", flexDirection: "row", gap: 10, paddingVertical: spacing.xs },
  filmstripCell: {
    backgroundColor: "rgba(255,255,255,.12)",
    borderColor: "rgba(255,255,255,.18)",
    borderCurve: "continuous",
    borderRadius: 11,
    borderWidth: 1,
    height: FILMSTRIP_EDGE,
    overflow: "hidden",
    width: FILMSTRIP_EDGE,
  },
  filmstripCellActive: { borderColor: "rgba(255,255,255,.96)", borderWidth: 2, height: 62, width: 62 },
  filmstripGlow: {
    borderColor: "rgba(255,255,255,.42)",
    borderRadius: 9,
    borderWidth: 1,
    bottom: 0,
    left: 0,
    position: "absolute",
    right: 0,
    top: 0,
  },
  header: {
    alignItems: "flex-start",
    flexDirection: "row",
    justifyContent: "space-between",
    left: 0,
    paddingHorizontal: spacing.md,
    position: "absolute",
    right: 0,
    top: 0,
  },
  headerBalance: { height: 46, width: 46 },
  iconButton: {
    alignItems: "center",
    backgroundColor: "rgba(16,13,11,.58)",
    borderColor: "rgba(255,255,255,.20)",
    borderRadius: 25,
    borderWidth: 1,
    height: 50,
    justifyContent: "center",
    width: 50,
  },
  iconButtonText: { color: "#fff", fontFamily: fonts.regular, fontSize: 29, lineHeight: 30, marginTop: -2 },
  meta: { color: "rgba(255,255,255,.72)", fontFamily: fonts.regular, fontVariant: ["tabular-nums"], ...typeScale.small },
  modeCluster: { alignItems: "center", gap: 5 },
  modeHint: { color: "rgba(255,255,255,.68)", fontFamily: fonts.medium, fontSize: 12, letterSpacing: 0.2 },
  modeIndicator: {
    backgroundColor: "rgba(255,255,255,.94)",
    borderCurve: "continuous",
    borderRadius: 18,
    bottom: 4,
    left: 4,
    position: "absolute",
    top: 4,
    width: 82,
  },
  modeLabel: { color: "rgba(255,255,255,.62)", fontFamily: fonts.bold, fontSize: 12.5 },
  modeLabelActive: { color: "#181411" },
  modeOption: { alignItems: "center", height: 36, justifyContent: "center", width: 82 },
  modeSwitch: {
    backgroundColor: "rgba(10,8,7,.56)",
    borderColor: "rgba(255,255,255,.16)",
    borderRadius: 22,
    borderWidth: 1,
    flexDirection: "row",
    padding: 4,
  },
  photoPage: { padding: 0, position: "absolute", top: 0 },
  photoSurface: {
    backgroundColor: "rgba(7,6,5,.32)",
    borderCurve: "continuous",
    borderRadius: 22,
    flex: 1,
    overflow: "hidden",
  },
  playButton: {
    alignItems: "center",
    backgroundColor: "rgba(255,255,255,.96)",
    borderRadius: 31,
    height: 62,
    justifyContent: "center",
    width: 62,
  },
  playIcon: { color: "#191512", fontFamily: fonts.extraBold, fontSize: 19, letterSpacing: -1 },
  playPressed: { opacity: 0.8 },
  player: { alignItems: "center", flexDirection: "row", gap: spacing.lg, paddingTop: 2 },
  progressFill: { backgroundColor: "rgba(255,255,255,.94)", borderRadius: 2, height: 3, width: "100%" },
  progressTrack: { backgroundColor: "rgba(255,255,255,.20)", borderRadius: 2, height: 3, overflow: "hidden", width: "100%" },
  root: { backgroundColor: "#090909", flex: 1, overflow: "hidden" },
  thumbnailFallback: { backgroundColor: "rgba(255,255,255,.08)", flex: 1 },
  title: { color: "#fff", fontFamily: fonts.extraBold, fontSize: 22, letterSpacing: -0.45, maxWidth: "88%" },
  topVignette: {
    backgroundColor: "rgba(6,5,4,.38)",
    height: "17%",
    left: 0,
    position: "absolute",
    right: 0,
    top: 0,
  },
});
