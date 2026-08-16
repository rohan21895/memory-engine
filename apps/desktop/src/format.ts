export function friendlyFolderName(path: string): string {
  const normalized = path.replace(/[\\/]+$/, "");
  const parts = normalized.split(/[\\/]/).filter(Boolean);
  return parts.at(-1) || path;
}

export function formatCount(value: number): string {
  return new Intl.NumberFormat(undefined).format(value);
}

export function monthLabel(value: string | null): string {
  if (!value) return "Date unknown";
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return "Date unknown";
  return new Intl.DateTimeFormat(undefined, {
    month: "long",
    year: "numeric",
  }).format(date);
}

export function scanPercent(done: number, total: number | null): number | null {
  if (!total || total <= 0) return null;
  return Math.min(100, Math.max(0, Math.round((done / total) * 100)));
}
