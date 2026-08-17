export const GRID_GAP = 12;
export const GRID_OVERSCAN_ROWS = 4;

export type GridMetrics = {
  columns: number;
  rowCount: number;
  rowHeight: number;
};

export function gridMetrics(width: number, itemCount: number): GridMetrics {
  const safeWidth = Math.max(1, width);
  const columns = Math.max(1, Math.floor((safeWidth + GRID_GAP) / 232));
  const columnWidth = (safeWidth - (columns - 1) * GRID_GAP) / columns;
  const rowHeight = Math.max(168, Math.min(252, columnWidth * 0.78));
  return { columns, rowCount: Math.ceil(itemCount / columns), rowHeight };
}

export function maximumMountedTiles(
  viewportHeight: number,
  metrics: GridMetrics,
): number {
  const visibleRows = Math.ceil(viewportHeight / (metrics.rowHeight + GRID_GAP));
  return (visibleRows + GRID_OVERSCAN_ROWS * 2) * metrics.columns;
}
