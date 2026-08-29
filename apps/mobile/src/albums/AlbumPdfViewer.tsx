import { Image } from "expo-image";
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  ActivityIndicator,
  FlatList,
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
  useAnimatedStyle,
  useReducedMotion,
  useSharedValue,
  withSpring,
  withTiming,
} from "react-native-reanimated";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import {
  albumPdfPageCount,
  generateAlbumPdf,
  renderAlbumPdfPage,
  type AlbumPdfResult,
  type RenderedAlbumPage,
} from "../../modules/photeo-album-pdf/src";
import { colors, fonts, spacing, typeScale } from "../ui";
import { ALBUM_DOCUMENT_RASTER_SIZE, buildAlbumDocument } from "./album-document";
import type { SavedAlbum } from "./album-store";

const MAIN_PAGE_WIDTH = ALBUM_DOCUMENT_RASTER_SIZE;
const THUMBNAIL_WIDTH = 144;
const MAX_ZOOM = 4;
const SETTLE_SPRING = { dampingRatio: 0.8, duration: 400 } as const;
const EASE_OUT = Easing.bezier(0.23, 1, 0.32, 1);

const pageRequests = new Map<string, Promise<RenderedAlbumPage>>();

function requestPage(uri: string, pageIndex: number, width: number): Promise<RenderedAlbumPage> {
  const key = `${uri}:${pageIndex}:${width}`;
  const pending = pageRequests.get(key);
  if (pending) return pending;
  const request = renderAlbumPdfPage(uri, pageIndex, width).finally(() => pageRequests.delete(key));
  pageRequests.set(key, request);
  return request;
}

function PageThumbnail({
  active,
  documentUri,
  index,
  onPress,
}: {
  active: boolean;
  documentUri: string;
  index: number;
  onPress: () => void;
}) {
  const [page, setPage] = useState<RenderedAlbumPage | null>(null);

  useEffect(() => {
    let live = true;
    setPage(null);
    void requestPage(documentUri, index, THUMBNAIL_WIDTH)
      .then((rendered) => {
        if (live) setPage(rendered);
      })
      .catch(() => undefined);
    return () => {
      live = false;
    };
  }, [documentUri, index]);

  return (
    <Pressable
      accessibilityLabel={`Open page ${index + 1}`}
      accessibilityRole="button"
      accessibilityState={{ selected: active }}
      onPress={onPress}
      style={({ pressed }) => [
        styles.thumbnailButton,
        active ? styles.thumbnailButtonActive : null,
        pressed ? styles.pressed : null,
      ]}
    >
      {page ? (
        <Image
          cachePolicy="memory-disk"
          contentFit="contain"
          recyclingKey={`album-document-thumbnail:${documentUri}:${index}`}
          source={page.uri}
          style={styles.thumbnailImage}
          transition={0}
        />
      ) : (
        <View style={styles.thumbnailFallback} />
      )}
      <Text style={[styles.thumbnailLabel, active ? styles.thumbnailLabelActive : null]}>{index + 1}</Text>
    </Pressable>
  );
}

function ViewerButton({
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
      style={({ pressed }) => [styles.viewerButton, disabled ? styles.disabled : null, pressed ? styles.pressed : null]}
    >
      <Text style={styles.viewerButtonText}>{label}</Text>
    </Pressable>
  );
}

function AlbumPage({
  document,
  pageIndex,
  stageHeight,
  stageWidth,
}: {
  document: AlbumPdfResult;
  pageIndex: number;
  stageHeight: number;
  stageWidth: number;
}) {
  const reducedMotion = useReducedMotion();
  const [rendered, setRendered] = useState<RenderedAlbumPage | null>(null);
  const [renderError, setRenderError] = useState(false);
  const [renderAttempt, setRenderAttempt] = useState(0);
  const scale = useSharedValue(1);
  const scaleStart = useSharedValue(1);
  const translateX = useSharedValue(0);
  const translateY = useSharedValue(0);
  const translateStartX = useSharedValue(0);
  const translateStartY = useSharedValue(0);

  const pageRatio = document.pageWidth / document.pageHeight;
  const displayWidth = Math.min(stageWidth - spacing.lg, (stageHeight - spacing.sm) * pageRatio);
  const displayHeight = displayWidth / pageRatio;

  useEffect(() => {
    let live = true;
    setRendered(null);
    setRenderError(false);
    scale.set(1);
    translateX.set(0);
    translateY.set(0);
    void requestPage(document.uri, pageIndex, MAIN_PAGE_WIDTH)
      .then((page) => {
        if (live) setRendered(page);
      })
      .catch(() => {
        if (live) setRenderError(true);
      });
    return () => {
      live = false;
    };
  }, [document.uri, pageIndex, renderAttempt, scale, translateX, translateY]);

  const settle = useCallback((target: number) => {
    "worklet";
    return reducedMotion
      ? withTiming(target, { duration: 120, easing: EASE_OUT, reduceMotion: ReduceMotion.System })
      : withSpring(target, SETTLE_SPRING);
  }, [reducedMotion]);

  const gesture = useMemo(() => {
    const pinch = Gesture.Pinch()
      .onBegin(() => {
        "worklet";
        scaleStart.set(scale.get());
      })
      .onUpdate((event) => {
        "worklet";
        scale.set(Math.max(1, Math.min(MAX_ZOOM, scaleStart.get() * event.scale)));
      })
      .onEnd(() => {
        "worklet";
        const nextScale = Math.max(1, Math.min(MAX_ZOOM, scale.get()));
        const maxX = (displayWidth * (nextScale - 1)) / 2;
        const maxY = (displayHeight * (nextScale - 1)) / 2;
        scale.set(settle(nextScale));
        translateX.set(settle(Math.max(-maxX, Math.min(maxX, translateX.get()))));
        translateY.set(settle(Math.max(-maxY, Math.min(maxY, translateY.get()))));
      });

    const pan = Gesture.Pan()
      .minDistance(3)
      .onBegin(() => {
        "worklet";
        translateStartX.set(translateX.get());
        translateStartY.set(translateY.get());
      })
      .onUpdate((event) => {
        "worklet";
        const currentScale = scale.get();
        if (currentScale <= 1) return;
        const maxX = (displayWidth * (currentScale - 1)) / 2;
        const maxY = (displayHeight * (currentScale - 1)) / 2;
        translateX.set(Math.max(-maxX, Math.min(maxX, translateStartX.get() + event.translationX)));
        translateY.set(Math.max(-maxY, Math.min(maxY, translateStartY.get() + event.translationY)));
      });

    const reset = Gesture.Tap()
      .numberOfTaps(2)
      .onEnd(() => {
        "worklet";
        scale.set(settle(1));
        translateX.set(settle(0));
        translateY.set(settle(0));
      });

    return Gesture.Exclusive(reset, Gesture.Simultaneous(pinch, pan));
  }, [displayHeight, displayWidth, scale, scaleStart, settle, translateStartX, translateStartY, translateX, translateY]);

  const pageStyle = useAnimatedStyle(() => ({
    transform: [
      { translateX: translateX.get() },
      { translateY: translateY.get() },
      { scale: scale.get() },
    ],
  }));

  return (
    <View style={[styles.stage, { height: stageHeight, width: stageWidth }]}>
      {rendered ? (
        <GestureDetector gesture={gesture}>
          <Animated.View style={[styles.pageSurface, { height: displayHeight, width: displayWidth }, pageStyle]}>
            <Image
              accessibilityLabel={`Album page ${pageIndex + 1} of ${document.pageCount}`}
              accessibilityRole="image"
              cachePolicy="memory-disk"
              contentFit="contain"
              recyclingKey={`album-document-page:${document.uri}:${pageIndex}`}
              source={rendered.uri}
              style={StyleSheet.absoluteFill}
              transition={reducedMotion ? 0 : 120}
            />
          </Animated.View>
        </GestureDetector>
      ) : renderError ? (
        <View style={styles.pageError}>
          <Text style={styles.pageErrorText}>This page couldn’t be shown.</Text>
          <Pressable accessibilityRole="button" onPress={() => setRenderAttempt((value) => value + 1)} style={styles.pageRetry}>
            <Text style={styles.pageRetryText}>Try again</Text>
          </Pressable>
        </View>
      ) : (
        <ActivityIndicator color={colors.gold} size="large" />
      )}
    </View>
  );
}

export function AlbumPdfViewer({ album, onBack }: { album: SavedAlbum; onBack: () => void }) {
  const { height, width } = useWindowDimensions();
  const insets = useSafeAreaInsets();
  const documentSpec = useMemo(() => buildAlbumDocument(album.photos), [album.photos]);
  const [attempt, setAttempt] = useState(0);
  const [document, setDocument] = useState<AlbumPdfResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pageIndex, setPageIndex] = useState(0);

  useEffect(() => {
    let live = true;
    const started = Date.now();
    setDocument(null);
    setError(null);
    console.log(`[PhoteoAlbumPdf] prepare start photos=${album.photos.length}`);
    void generateAlbumPdf(album.id, documentSpec)
      .then(async (result) => ({ ...result, pageCount: await albumPdfPageCount(result.uri) }))
      .then((result) => {
        if (!live) return;
        console.log(`[PhoteoAlbumPdf] prepare complete pages=${result.pageCount} elapsedMs=${Date.now() - started}`);
        setDocument(result);
      })
      .catch((reason: unknown) => {
        if (!live) return;
        const errorName = reason instanceof Error ? reason.name : "UnknownError";
        console.log(`[PhoteoAlbumPdf] prepare failed elapsedMs=${Date.now() - started} error=${errorName}`);
        setError("This album couldn’t be built on this phone. Check that its photos are still available, then try again.");
      });
    return () => {
      live = false;
    };
  }, [album.id, album.photos.length, attempt, documentSpec]);

  const top = Math.max(insets.top, StatusBar.currentHeight ?? 0);
  const headerHeight = 70;
  const railHeight = 104 + insets.bottom;
  const stageHeight = Math.max(240, height - top - headerHeight - railHeight);
  const goTo = useCallback((next: number) => {
    if (!document) return;
    setPageIndex(Math.max(0, Math.min(document.pageCount - 1, next)));
  }, [document]);

  if (!document) {
    return (
      <View style={[styles.loadingRoot, { paddingBottom: insets.bottom + spacing.lg, paddingTop: top + spacing.md }]}>
        <StatusBar backgroundColor={colors.background} barStyle="dark-content" />
        <Pressable accessibilityLabel="Back to album" accessibilityRole="button" onPress={onBack} style={styles.loadingBack}>
          <Text style={styles.backText}>‹</Text>
        </Pressable>
        <View style={styles.loadingBody}>
          {error ? (
            <>
              <Text accessibilityRole="header" style={styles.loadingTitle}>We couldn’t prepare this album</Text>
              <Text selectable style={styles.loadingHelper}>{error}</Text>
              <Pressable accessibilityRole="button" onPress={() => setAttempt((value) => value + 1)} style={styles.retryButton}>
                <Text style={styles.retryText}>Try again</Text>
              </Pressable>
            </>
          ) : (
            <>
              <ActivityIndicator color={colors.gold} size="large" />
              <Text accessibilityRole="header" style={styles.loadingTitle}>Preparing high-resolution, 8x8 inch PDF</Text>
              <Text style={styles.loadingHelper}>Building your gallery-wall pages on this phone.</Text>
            </>
          )}
        </View>
      </View>
    );
  }

  return (
    <GestureHandlerRootView style={styles.root}>
      <StatusBar backgroundColor="#171513" barStyle="light-content" />
      <View style={[styles.header, { height: headerHeight, marginTop: top }]}>
        <ViewerButton accessibilityLabel="Back to album" label="‹" onPress={onBack} />
        <View style={styles.headerCopy}>
          <Text numberOfLines={1} style={styles.title}>{album.title}</Text>
          <Text style={styles.subtitle}>High-resolution, 8x8 inch · stays inside Photeo</Text>
        </View>
        <Text style={styles.counter}>{pageIndex + 1} / {document.pageCount}</Text>
      </View>

      <AlbumPage
        document={document}
        pageIndex={pageIndex}
        stageHeight={stageHeight}
        stageWidth={width}
      />

      <View style={[styles.rail, { height: railHeight, paddingBottom: insets.bottom }]}>
        <ViewerButton accessibilityLabel="Previous page" disabled={pageIndex === 0} label="‹" onPress={() => goTo(pageIndex - 1)} />
        <FlatList
          contentContainerStyle={styles.thumbnailRail}
          data={Array.from({ length: document.pageCount }, (_, index) => index)}
          horizontal
          keyExtractor={(index) => String(index)}
          renderItem={({ item }) => (
            <PageThumbnail
              active={item === pageIndex}
              documentUri={document.uri}
              index={item}
              onPress={() => goTo(item)}
            />
          )}
          showsHorizontalScrollIndicator={false}
          style={styles.thumbnailList}
        />
        <ViewerButton
          accessibilityLabel="Next page"
          disabled={pageIndex === document.pageCount - 1}
          label="›"
          onPress={() => goTo(pageIndex + 1)}
        />
      </View>
    </GestureHandlerRootView>
  );
}

const styles = StyleSheet.create({
  backText: { color: colors.text, fontFamily: fonts.regular, fontSize: 30 },
  counter: { color: "rgba(255,255,255,.72)", fontFamily: fonts.medium, fontVariant: ["tabular-nums"], minWidth: 48, textAlign: "right", ...typeScale.small },
  disabled: { opacity: 0.3 },
  header: { alignItems: "center", flexDirection: "row", gap: spacing.sm, paddingHorizontal: spacing.sm },
  headerCopy: { flex: 1 },
  loadingBack: { alignItems: "center", height: 48, justifyContent: "center", width: 48 },
  loadingBody: { alignItems: "center", flex: 1, gap: spacing.sm, justifyContent: "center", paddingHorizontal: spacing.xl },
  loadingHelper: { color: colors.muted, fontFamily: fonts.regular, textAlign: "center", ...typeScale.body },
  loadingRoot: { backgroundColor: colors.background, flex: 1, paddingHorizontal: spacing.md },
  loadingTitle: { color: colors.text, fontFamily: fonts.extraBold, paddingTop: spacing.sm, textAlign: "center", ...typeScale.subtitle },
  pageSurface: { backgroundColor: "#ffffff", boxShadow: "0 8px 28px rgba(0,0,0,.34)" },
  pageError: { alignItems: "center", gap: spacing.sm },
  pageErrorText: { color: "rgba(255,255,255,.72)", fontFamily: fonts.regular, ...typeScale.body },
  pageRetry: { borderColor: "rgba(255,255,255,.24)", borderRadius: 22, borderWidth: 1, minHeight: 44, justifyContent: "center", paddingHorizontal: spacing.md },
  pageRetryText: { color: colors.onAccent, fontFamily: fonts.bold, ...typeScale.small },
  pressed: { opacity: 0.72, transform: [{ scale: 0.97 }] },
  rail: { alignItems: "center", borderTopColor: "rgba(255,255,255,.08)", borderTopWidth: 1, flexDirection: "row", gap: spacing.xs, paddingHorizontal: spacing.xs, paddingTop: spacing.xs },
  retryButton: { alignItems: "center", backgroundColor: colors.gold, borderRadius: 24, justifyContent: "center", marginTop: spacing.md, minHeight: 48, paddingHorizontal: spacing.lg },
  retryText: { color: colors.onAccent, fontFamily: fonts.bold, ...typeScale.label },
  root: { backgroundColor: "#171513", flex: 1 },
  stage: { alignItems: "center", justifyContent: "center", overflow: "hidden" },
  subtitle: { color: "rgba(255,255,255,.6)", fontFamily: fonts.regular, fontSize: 11.5, lineHeight: 16 },
  thumbnailButton: { alignItems: "center", borderColor: "transparent", borderRadius: 5, borderWidth: 2, gap: 2, height: 86, overflow: "hidden", padding: 2, width: 58 },
  thumbnailButtonActive: { borderColor: colors.gold },
  thumbnailFallback: { backgroundColor: "rgba(255,255,255,.12)", flex: 1, width: "100%" },
  thumbnailImage: { backgroundColor: "#fff", flex: 1, width: "100%" },
  thumbnailLabel: { color: "rgba(255,255,255,.56)", fontFamily: fonts.medium, fontSize: 10, fontVariant: ["tabular-nums"] },
  thumbnailLabelActive: { color: colors.onAccent },
  thumbnailList: { flex: 1 },
  thumbnailRail: { gap: spacing.xs },
  title: { color: colors.onAccent, fontFamily: fonts.bold, ...typeScale.label },
  viewerButton: { alignItems: "center", height: 48, justifyContent: "center", width: 48 },
  viewerButtonText: { color: colors.onAccent, fontFamily: fonts.regular, fontSize: 28 },
});
