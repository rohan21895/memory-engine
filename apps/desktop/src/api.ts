import { Channel, convertFileSrc, invoke } from "@tauri-apps/api/core";
import { open } from "@tauri-apps/plugin-dialog";
import type { LibraryPage, ScanSummary, ScanUpdate } from "./types";

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
  offset = 0,
  limit = 120,
): Promise<LibraryPage> {
  if (!inDesktopApp()) {
    return { items: [], total: 0, offset: 0, hasMore: false };
  }
  return invoke<LibraryPage>("library_page", { query, offset, limit });
}

export function localAssetUrl(path: string | null): string | null {
  if (!path || !inDesktopApp()) return null;
  return convertFileSrc(path);
}
