import { Channel, convertFileSrc, invoke } from "@tauri-apps/api/core";
import { open } from "@tauri-apps/plugin-dialog";
import type {
  LibraryPage,
  LibraryStats,
  PeoplePage,
  ProxyAsset,
  ScanSummary,
  ScanUpdate,
} from "./types";

function inDesktopApp(): boolean {
  return "__TAURI_INTERNALS__" in window;
}

export async function chooseFolders(): Promise<string[]> {
  if (!inDesktopApp()) return [];
  const selected = await open({ directory: true, multiple: true });
  if (!selected) return [];
  return Array.isArray(selected) ? selected : [selected];
}

export async function startScan(
  roots: string[],
  onUpdate: (update: ScanUpdate) => void,
): Promise<ScanSummary> {
  if (!inDesktopApp()) {
    throw new Error("Folder scanning is available in the desktop app.");
  }
  const onEvent = new Channel<ScanUpdate>();
  onEvent.onmessage = onUpdate;
  return invoke<ScanSummary>("start_scan", { roots, onEvent });
}

export async function cancelScan(): Promise<void> {
  if (inDesktopApp()) await invoke("cancel_scan");
}

export async function loadLibrary(
  query = "",
  cursor: string | null = null,
  limit = 120,
): Promise<LibraryPage> {
  if (!inDesktopApp()) {
    return { items: [], total: 0, nextCursor: null, hasMore: false };
  }
  return invoke<LibraryPage>("library_page", { query, cursor, limit });
}

export async function loadBestMoments(
  cursor: string | null = null,
  limit = 120,
): Promise<LibraryPage> {
  if (!inDesktopApp()) {
    return { items: [], total: 0, nextCursor: null, hasMore: false };
  }
  return invoke<LibraryPage>("best_moments_page", { cursor, limit });
}

export async function loadProxyAsset(proxyId: string): Promise<ProxyAsset> {
  if (!inDesktopApp()) throw new Error("Private previews are available in the desktop app.");
  return invoke<ProxyAsset>("proxy_asset", { proxyId });
}

export async function loadPeoplePage(): Promise<PeoplePage> {
  if (!inDesktopApp()) return { people: [], reviewItems: [] };
  return invoke<PeoplePage>("people_page");
}

export async function loadLibraryStats(): Promise<LibraryStats | null> {
  if (!inDesktopApp()) return null;
  return invoke<LibraryStats>("library_stats");
}

export function localAssetUrl(path: string | null): string | null {
  if (!path || !inDesktopApp()) return null;
  return convertFileSrc(path);
}
