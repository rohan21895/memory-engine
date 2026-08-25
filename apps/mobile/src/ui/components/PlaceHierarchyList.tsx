import { FlashList } from "@shopify/flash-list";
import { useCallback, useMemo, useState } from "react";
import { Pressable, StyleSheet, Text, View, type StyleProp, type ViewStyle } from "react-native";

import { copy } from "../copy";
import { fonts } from "../fonts";
import { colors, continuousRadius, layout, radii, spacing, typeScale } from "../tokens";
import { FilterSearchBar } from "./FilterSearchBar";
import { countryForState, getStates, stateForCity } from "./place-source";
import {
  buildPlaceTree,
  defaultExpandedIds,
  flattenPlaceRows,
  searchPlaceTree,
  type PlaceInput,
  type PlaceRow,
} from "./place-tree";

const INDENT = 16;

export type PlaceHierarchyListProps = {
  countries: PlaceInput[];
  loadingText?: string;
  onSelect: (locationId: string | null) => void;
  places: PlaceInput[];
  selectedLocationId: string | null;
  /** Falls back to the index's own state tier when the caller has none. */
  states?: PlaceInput[];
  style?: StyleProp<ViewStyle>;
};

function Radio({ checked }: { checked: boolean }) {
  return (
    <View style={[styles.radio, checked ? styles.radioChecked : null]}>
      {checked ? <Text style={styles.check}>✓</Text> : null}
    </View>
  );
}

/**
 * Country → State → Place, collapsed by default, searchable across all three
 * tiers, and virtualized. Selecting any tier yields that tier's location id, so
 * "every photo in India" and "every photo in Noida" are both one tap.
 */
export function PlaceHierarchyList({
  countries,
  loadingText,
  onSelect,
  places,
  selectedLocationId,
  states,
  style,
}: PlaceHierarchyListProps) {
  const [query, setQuery] = useState("");
  // Expansion is stored as two deltas over the automatic state so a collapse
  // survives an auto-expand and a search reveal does not stick afterwards.
  const [openedIds, setOpenedIds] = useState<ReadonlySet<string>>(() => new Set<string>());
  const [closedIds, setClosedIds] = useState<ReadonlySet<string>>(() => new Set<string>());
  const [showAllIds, setShowAllIds] = useState<ReadonlySet<string>>(() => new Set<string>());

  const stateTier = useMemo(
    // Recomputed when any tier changes: a running scan grows all three at once.
    () => states ?? getStates(),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [states, countries, places],
  );

  const tree = useMemo(
    () => buildPlaceTree({ countries, places, states: stateTier }, { countryForState, stateForPlace: stateForCity }),
    [countries, places, stateTier],
  );
  const search = useMemo(() => searchPlaceTree(tree, query), [tree, query]);
  const autoIds = useMemo(() => defaultExpandedIds(search.roots), [search.roots]);

  const expandedIds = useMemo(() => {
    const ids = new Set<string>();
    autoIds.forEach((id) => ids.add(id));
    openedIds.forEach((id) => ids.add(id));
    closedIds.forEach((id) => ids.delete(id));
    // Last, so a country the user collapsed earlier still opens to show a match
    // rather than leaving the result invisible behind a stale closed row.
    search.revealIds.forEach((id) => ids.add(id));
    return ids;
  }, [autoIds, search.revealIds, openedIds, closedIds]);

  const rows = useMemo(
    () => flattenPlaceRows(search.roots, { expandedIds, showAllIds }),
    [search.roots, expandedIds, showAllIds],
  );

  const toggle = useCallback(
    (id: string, expanded: boolean) => {
      setOpenedIds((current) => {
        const next = new Set(current);
        if (expanded) next.delete(id);
        else next.add(id);
        return next;
      });
      setClosedIds((current) => {
        const next = new Set(current);
        if (expanded) next.add(id);
        else next.delete(id);
        return next;
      });
    },
    [],
  );

  const showAll = useCallback((groupId: string) => {
    setShowAllIds((current) => {
      const next = new Set(current);
      next.add(groupId);
      return next;
    });
  }, []);

  const total = countries.length + stateTier.length + places.length;
  // Three honest states, never one bare "no places": a search that found
  // nothing, a scan still running, and a finished scan over a library whose
  // photos carry no location at all (chat apps strip it before sending).
  const emptyMessage = !query && loadingText
    ? loadingText
    : !query && total === 0
      ? `${copy.access.noPlacesTitle}. ${copy.access.noPlacesHelper}`
      : copy.places.noMatches;

  const renderRow = useCallback(
    ({ item }: { item: PlaceRow }) => {
      if (item.kind === "section") {
        return <Text style={styles.section}>{copy.places.sections[item.tier]}</Text>;
      }
      if (item.kind === "more") {
        return (
          <Pressable
            accessibilityHint={copy.places.showAllHint}
            accessibilityLabel={copy.places.showAll(item.total)}
            accessibilityRole="button"
            onPress={() => showAll(item.groupId)}
            style={({ pressed }) => [
              styles.more,
              { marginLeft: item.depth * INDENT },
              pressed ? styles.pressed : null,
            ]}
          >
            <Text style={styles.moreText}>{copy.places.showAll(item.total)}</Text>
          </Pressable>
        );
      }

      const { node, depth, expandable, expanded } = item;
      const selected = node.id === selectedLocationId;
      return (
        <View style={[styles.rowWrap, { marginLeft: depth * INDENT }]}>
          <Pressable
            accessibilityHint={copy.places.selectHint}
            accessibilityLabel={`${copy.places.tierNoun[node.tier]}. ${node.name}. ${copy.filters.photoCount(node.count)}`}
            accessibilityRole="radio"
            accessibilityState={expandable ? { checked: selected, expanded } : { checked: selected }}
            onPress={() => onSelect(node.id)}
            style={({ pressed }) => [
              styles.row,
              depth > 0 ? styles.rowNested : null,
              selected ? styles.rowSelected : null,
              pressed ? styles.pressed : null,
            ]}
          >
            {expandable ? (
              <Pressable
                accessibilityLabel={expanded ? copy.places.collapse(node.name) : copy.places.expand(node.name)}
                accessibilityRole="button"
                accessibilityState={{ expanded }}
                hitSlop={6}
                onPress={() => toggle(node.id, expanded)}
                style={({ pressed }) => [styles.disclosure, pressed ? styles.pressed : null]}
              >
                <Text style={styles.disclosureText}>{expanded ? "⌄" : "›"}</Text>
              </Pressable>
            ) : (
              <View accessibilityElementsHidden style={styles.disclosureSpacer} />
            )}
            <View style={styles.rowCopy}>
              <Text numberOfLines={1} style={[styles.rowLabel, selected ? styles.selectedText : null]}>
                {node.name}
              </Text>
              <Text style={styles.detail}>{copy.filters.photoCount(node.count)}</Text>
            </View>
            <Radio checked={selected} />
          </Pressable>
        </View>
      );
    },
    [onSelect, selectedLocationId, showAll, toggle],
  );

  return (
    <View accessibilityRole="radiogroup" style={[styles.root, style]}>
      <View style={styles.top}>
        <FilterSearchBar
          accessibilityLabel={copy.places.searchLabel}
          onChangeText={setQuery}
          placeholder={copy.places.searchPlaceholder}
          value={query}
        />
        {query.trim().length > 0 ? (
          <Text accessibilityLiveRegion="polite" style={styles.resultCount}>
            {copy.places.matches(search.matchCount)}
          </Text>
        ) : null}
        <Pressable
          accessibilityHint={copy.places.anyPlaceHint}
          accessibilityLabel={copy.places.anyPlace}
          accessibilityRole="radio"
          accessibilityState={{ checked: selectedLocationId === null }}
          onPress={() => onSelect(null)}
          style={({ pressed }) => [
            styles.row,
            selectedLocationId === null ? styles.rowSelected : null,
            pressed ? styles.pressed : null,
          ]}
        >
          <View accessibilityElementsHidden style={styles.disclosureSpacer} />
          <Text style={[styles.rowLabel, selectedLocationId === null ? styles.selectedText : null]}>
            {copy.places.anyPlace}
          </Text>
          <Radio checked={selectedLocationId === null} />
        </Pressable>
      </View>

      <FlashList
        contentContainerStyle={styles.listContent}
        data={rows}
        extraData={selectedLocationId}
        getItemType={(item) => item.kind}
        keyboardShouldPersistTaps="handled"
        keyExtractor={(item) => item.key}
        ListEmptyComponent={<Text accessibilityLiveRegion="polite" style={styles.message}>{emptyMessage}</Text>}
        ListFooterComponent={
          loadingText && rows.length > 0
            ? <Text accessibilityLiveRegion="polite" style={styles.scanning}>{loadingText}</Text>
            : null
        }
        renderItem={renderRow}
        style={styles.list}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  check: { color: colors.onAccent, fontFamily: fonts.bold, fontSize: 15 },
  detail: { color: colors.muted, fontFamily: fonts.regular, fontVariant: ["tabular-nums"], ...typeScale.small },
  disclosure: {
    alignItems: "center",
    borderRadius: radii.pill,
    height: 44,
    justifyContent: "center",
    marginLeft: -spacing.xs,
    width: 34,
  },
  disclosureSpacer: { width: spacing.xxs },
  disclosureText: { color: colors.goldPressed, fontFamily: fonts.bold, fontSize: 18, lineHeight: 22 },
  list: { flex: 1 },
  listContent: { paddingBottom: spacing.lg },
  message: { color: colors.muted, fontFamily: fonts.regular, paddingVertical: spacing.md, ...typeScale.body },
  more: {
    alignItems: "center",
    alignSelf: "flex-start",
    backgroundColor: colors.panelRaised,
    borderRadius: radii.pill,
    justifyContent: "center",
    marginBottom: spacing.xs,
    marginTop: spacing.xxs,
    minHeight: 44,
    paddingHorizontal: spacing.md,
  },
  moreText: { color: colors.goldPressed, fontFamily: fonts.bold, ...typeScale.small },
  pressed: { opacity: 0.62 },
  radio: {
    alignItems: "center",
    borderColor: colors.muted,
    borderRadius: 13,
    borderWidth: 1.5,
    height: 26,
    justifyContent: "center",
    width: 26,
  },
  radioChecked: { backgroundColor: colors.gold, borderColor: colors.gold },
  resultCount: { color: colors.goldPressed, fontFamily: fonts.semibold, ...typeScale.small },
  root: { gap: spacing.sm },
  row: {
    ...continuousRadius(radii.md),
    alignItems: "center",
    borderColor: colors.hairline,
    borderWidth: 1,
    flexDirection: "row",
    gap: spacing.xs,
    minHeight: layout.minTouchTarget,
    paddingHorizontal: spacing.sm,
    paddingVertical: spacing.xs,
  },
  rowCopy: { flex: 1 },
  rowLabel: { color: colors.text, flex: 1, fontFamily: fonts.semibold, ...typeScale.label },
  rowNested: { backgroundColor: colors.background },
  rowSelected: { backgroundColor: colors.panelRaised, borderColor: colors.gold },
  rowWrap: { paddingBottom: spacing.xs },
  scanning: { color: colors.muted, fontFamily: fonts.regular, paddingTop: spacing.sm, ...typeScale.small },
  section: {
    color: colors.goldPressed,
    fontFamily: fonts.bold,
    paddingBottom: spacing.xs,
    paddingTop: spacing.sm,
    textTransform: "uppercase",
    ...typeScale.eyebrow,
  },
  selectedText: { color: colors.goldPressed },
  top: { gap: spacing.xs },
});
