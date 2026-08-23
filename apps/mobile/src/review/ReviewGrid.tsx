import { Image } from "expo-image";
import { FlatList, Pressable, StyleSheet, Text, useWindowDimensions, View } from "react-native";

const C = {
  panel: "#1c1a17",
  line: "#2c2a25",
  text: "#e8e4dc",
  gold: "#c8a24a",
};

const GRID_PADDING = 18;
const GRID_GAP = 10;

export type ReviewGridItem = {
  slot_media_id: string;
  media_id: string;
  uri: string;
  page: number;
  caption: string;
  isSwap?: boolean;
};

export type ReviewGridProps = {
  items: ReviewGridItem[];
  onPressPhoto: (item: ReviewGridItem, index: number) => void;
};

export function ReviewGrid({ items, onPressPhoto }: ReviewGridProps) {
  const { width } = useWindowDimensions();
  const cardWidth = (width - GRID_PADDING * 2 - GRID_GAP) / 2;

  return (
    <FlatList
      columnWrapperStyle={styles.row}
      contentContainerStyle={styles.content}
      data={items}
      keyExtractor={(item) => item.slot_media_id}
      numColumns={2}
      renderItem={({ item, index }) => (
        <Pressable
          accessibilityHint="Opens this photo full screen"
          accessibilityLabel={`Page ${item.page}. ${item.caption}`}
          accessibilityRole="button"
          onPress={() => onPressPhoto(item, index)}
          style={({ pressed }) => [
            styles.card,
            { width: cardWidth },
            item.isSwap && styles.cardSwapped,
            pressed && styles.cardPressed,
          ]}
        >
          <Image
            accessibilityLabel={`Selected photo for page ${item.page}`}
            contentFit="cover"
            source={item.uri}
            style={styles.image}
            transition={140}
          />
          <View style={styles.copy}>
            <Text style={styles.page}>
              PAGE {item.page}
              {item.isSwap ? " · YOUR CHOICE" : ""}
            </Text>
            <Text numberOfLines={3} style={styles.caption}>
              {item.caption}
            </Text>
          </View>
        </Pressable>
      )}
      showsVerticalScrollIndicator={false}
    />
  );
}

const styles = StyleSheet.create({
  content: {
    paddingBottom: 48,
    paddingHorizontal: GRID_PADDING,
    paddingTop: 16,
  },
  row: { gap: GRID_GAP },
  card: {
    backgroundColor: C.panel,
    borderColor: C.line,
    borderRadius: 8,
    borderWidth: 1,
    marginBottom: GRID_GAP,
    overflow: "hidden",
  },
  cardSwapped: { borderColor: C.gold },
  cardPressed: { opacity: 0.82, transform: [{ scale: 0.985 }] },
  image: { aspectRatio: 0.95, backgroundColor: C.line, width: "100%" },
  copy: { minHeight: 94, paddingHorizontal: 11, paddingVertical: 10 },
  page: { color: C.gold, fontSize: 10, letterSpacing: 1.2 },
  caption: { color: C.text, fontSize: 13, lineHeight: 18, marginTop: 6 },
});

export default ReviewGrid;
