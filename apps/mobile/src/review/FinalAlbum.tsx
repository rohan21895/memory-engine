import { FlashList } from "@shopify/flash-list";
import { Image } from "expo-image";
import { Pressable, StatusBar, StyleSheet, Text, View } from "react-native";

const C = {
  bg: "#141311",
  panel: "#1c1a17",
  line: "#2c2a25",
  text: "#e8e4dc",
  muted: "#9a927f",
  gold: "#c8a24a",
};

export type FinalPhoto = {
  media_id: string;
  uri: string;
  page: number;
};

// The finished album: the reviewed, ordered picks presented as full-bleed
// album pages (one photo per page, page numbers) — the on-phone equivalent of
// the printed album. This is the terminal screen of the create flow.
export default function FinalAlbum({
  photos,
  onRestart,
  onBack,
}: {
  photos: FinalPhoto[];
  onRestart: () => void;
  onBack: () => void;
}) {
  return (
    <View style={styles.root}>
      <StatusBar backgroundColor={C.bg} barStyle="light-content" />
      <FlashList
        data={photos}
        keyExtractor={(item, i) => `${item.media_id}-${i}`}
        ListHeaderComponent={
          <View style={styles.header}>
            <Pressable onPress={onBack} hitSlop={12}>
              <Text style={styles.back}>‹ Edit picks</Text>
            </Pressable>
            <Text style={styles.eyebrow}>ALBUM CREATED · ON THIS PHONE</Text>
            <Text style={styles.title}>Your album</Text>
            <Text style={styles.subtitle}>
              {photos.length} page{photos.length === 1 ? "" : "s"} · finished on
              device, nothing uploaded
            </Text>
          </View>
        }
        renderItem={({ item, index }) => (
          <View style={styles.page}>
            <Image
              source={item.uri}
              style={styles.photo}
              contentFit="cover"
              transition={140}
              recyclingKey={item.media_id}
            />
            <Text style={styles.pageNo}>{String(index + 1).padStart(2, "0")}</Text>
          </View>
        )}
        ListFooterComponent={
          <View style={styles.footer}>
            <Pressable
              onPress={onRestart}
              style={({ pressed }) => [styles.restart, pressed && styles.pressed]}
            >
              <Text style={styles.restartText}>Create another album</Text>
            </Pressable>
          </View>
        }
        contentContainerStyle={styles.list}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  root: { backgroundColor: C.bg, flex: 1 },
  list: { paddingBottom: 40 },
  header: {
    paddingHorizontal: 22,
    paddingTop: (StatusBar.currentHeight ?? 24) + 26,
    paddingBottom: 22,
  },
  back: { color: C.muted, fontSize: 14, marginBottom: 14 },
  eyebrow: { color: C.gold, fontSize: 10, letterSpacing: 1.8 },
  title: { color: C.text, fontSize: 34, fontWeight: "400", marginTop: 6 },
  subtitle: { color: C.muted, fontSize: 13, lineHeight: 19, marginTop: 8 },
  page: {
    aspectRatio: 3 / 4,
    marginBottom: 14,
    marginHorizontal: 16,
    position: "relative",
  },
  photo: {
    backgroundColor: C.panel,
    borderRadius: 6,
    height: "100%",
    width: "100%",
  },
  pageNo: {
    bottom: 12,
    color: C.text,
    fontSize: 12,
    left: 14,
    letterSpacing: 1,
    opacity: 0.85,
    position: "absolute",
    textShadowColor: "rgba(0,0,0,0.8)",
    textShadowRadius: 6,
  },
  footer: { paddingHorizontal: 22, paddingTop: 18 },
  restart: {
    alignItems: "center",
    borderColor: C.line,
    borderRadius: 8,
    borderWidth: 1,
    paddingVertical: 15,
  },
  pressed: { opacity: 0.6 },
  restartText: { color: C.text, fontSize: 15 },
});
