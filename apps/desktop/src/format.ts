export function friendlyFolderName(path: string): string {
  const normalized = path.replace(/[\\/]+$/, "");
  const parts = normalized.split(/[\\/]/).filter(Boolean);
  return parts.at(-1) || path;
}

export function formatCount(value: number): string {
  return new Intl.NumberFormat(undefined).format(value);
}

function captureDate(value: number | null): Date | null {
  if (value === null) return null;
  const date = new Date(value * 1_000);
  return Number.isNaN(date.valueOf()) ? null : date;
}

export function monthLabel(value: number | null, precision = "unknown"): string {
  const date = captureDate(value);
  if (!date || precision === "unknown") return "Date unknown";
  if (precision === "year") return new Intl.DateTimeFormat(undefined, { year: "numeric" }).format(date);
  if (Number.isNaN(date.valueOf())) return "Date unknown";
  return new Intl.DateTimeFormat(undefined, {
    month: "long",
    year: "numeric",
  }).format(date);
}

export function captureLabel(value: number | null, precision = "unknown"): string {
  const date = captureDate(value);
  if (!date || precision === "unknown") return "Date unknown";
  if (precision === "year") return new Intl.DateTimeFormat(undefined, { year: "numeric" }).format(date);
  if (precision === "month") {
    return new Intl.DateTimeFormat(undefined, { month: "long", year: "numeric" }).format(date);
  }
  const options: Intl.DateTimeFormatOptions = { day: "numeric", month: "short", year: "numeric" };
  if (["hour", "minute", "second"].includes(precision)) {
    options.hour = "numeric";
    if (precision !== "hour") options.minute = "2-digit";
    if (precision === "second") options.second = "2-digit";
  }
  return new Intl.DateTimeFormat(undefined, options).format(date);
}

export function durationLabel(value: number | null): string | null {
  if (value === null || value < 0) return null;
  const totalSeconds = Math.round(value / 1_000);
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${minutes}:${String(seconds).padStart(2, "0")}`;
}

export function measuredDurationLabel(value: number): string {
  if (!Number.isFinite(value) || value < 0) return "Not measured";
  if (value > 0 && value < 500) return "Less than 1 sec";
  const totalSeconds = Math.round(value / 1_000);
  const hours = Math.floor(totalSeconds / 3_600);
  const minutes = Math.floor((totalSeconds % 3_600) / 60);
  const seconds = totalSeconds % 60;
  if (hours > 0) return `${hours} hr ${minutes} min ${seconds} sec`;
  if (minutes > 0) return `${minutes} min ${seconds} sec`;
  return `${seconds} sec`;
}

export function scanPercent(done: number, total: number | null): number | null {
  if (!total || total <= 0) return null;
  return Math.min(100, Math.max(0, Math.round((done / total) * 100)));
}
