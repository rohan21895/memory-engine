import {
  GoogleSignin,
  isErrorWithCode,
  statusCodes,
} from "@react-native-google-signin/google-signin";
import * as FileSystem from "expo-file-system/legacy";
import * as WebBrowser from "expo-web-browser";
import { useCallback } from "react";

import { recordGooglePhotosConsent } from "../privacy/consent-ledger";
import type { PickedPhoto } from "./picked-photo";

export const GOOGLE_PHOTOS_SCOPE =
  "https://www.googleapis.com/auth/photospicker.mediaitems.readonly";

const API_ROOT = "https://photospicker.googleapis.com/v1";
// Native Google Sign-In authenticates against the app's own signature (the
// Android OAuth client, keyed by package + SHA-1) and needs the WEB client id
// only as the token audience. There is NO browser redirect, so the old
// `Error 400: invalid_request` (custom-scheme redirect rejected by Google) is
// structurally impossible here.
const GOOGLE_WEB_CLIENT_ID = process.env.EXPO_PUBLIC_GOOGLE_WEB_CLIENT_ID ?? "";

let configured = false;
function ensureConfigured(): void {
  if (configured) return;
  GoogleSignin.configure({
    scopes: [GOOGLE_PHOTOS_SCOPE],
    webClientId: GOOGLE_WEB_CLIENT_ID || undefined,
  });
  configured = true;
}

type PickerSession = {
  id: string;
  pickerUri: string;
  mediaItemsSet?: boolean;
  expireTime?: string;
  pollingConfig?: { pollInterval?: string; timeoutIn?: string };
};

type GoogleMediaItem = {
  id: string;
  createTime?: string;
  mediaFile: {
    baseUrl: string;
    filename: string;
    mimeType: string;
    mediaFileMetadata?: { width?: string; height?: string };
  };
};

type MediaItemsResponse = {
  mediaItems?: GoogleMediaItem[];
  nextPageToken?: string;
};

class GooglePhotosApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = "GooglePhotosApiError";
  }
}

async function googleFetch<T>(
  path: string,
  accessToken: string,
  init?: RequestInit,
): Promise<T> {
  const response = await fetch(`${API_ROOT}${path}`, {
    ...init,
    headers: {
      Accept: "application/json",
      Authorization: `Bearer ${accessToken}`,
      "Content-Type": "application/json",
      ...init?.headers,
    },
  });

  if (!response.ok) {
    const body = await response.text();
    throw new GooglePhotosApiError(
      `Google Photos request failed (${response.status}): ${body}`,
      response.status,
    );
  }

  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}

function intervalMilliseconds(interval = "2s"): number {
  const seconds = Number.parseFloat(interval.replace("s", ""));
  return Number.isFinite(seconds) ? Math.max(seconds * 1_000, 1_000) : 2_000;
}

async function waitForSelection(
  initialSession: PickerSession,
  accessToken: string,
): Promise<PickerSession> {
  const timeout = intervalMilliseconds(
    initialSession.pollingConfig?.timeoutIn ?? "600s",
  );
  const deadline = Date.now() + timeout;
  let session = initialSession;

  while (!session.mediaItemsSet && Date.now() < deadline) {
    await new Promise((resolve) =>
      setTimeout(
        resolve,
        intervalMilliseconds(session.pollingConfig?.pollInterval),
      ),
    );
    session = await googleFetch<PickerSession>(
      `/sessions/${encodeURIComponent(initialSession.id)}`,
      accessToken,
    );
  }

  if (!session.mediaItemsSet) {
    throw new Error("Google Photos selection timed out. Please try again.");
  }

  return session;
}

async function listSelectedMedia(
  sessionId: string,
  accessToken: string,
): Promise<PickedPhoto[]> {
  const mediaItems: GoogleMediaItem[] = [];
  let pageToken: string | undefined;

  do {
    const query = new URLSearchParams({ sessionId, pageSize: "100" });
    if (pageToken) {
      query.set("pageToken", pageToken);
    }
    const page = await googleFetch<MediaItemsResponse>(
      `/mediaItems?${query.toString()}`,
      accessToken,
    );
    mediaItems.push(...(page.mediaItems ?? []));
    pageToken = page.nextPageToken;
  } while (pageToken);

  const images = mediaItems.filter((item) =>
    item.mediaFile.mimeType.startsWith("image/"),
  );
  const importRoot = FileSystem.documentDirectory
    ? `${FileSystem.documentDirectory}imports/google-photos/`
    : null;
  if (!importRoot) {
    throw new Error("Local app storage is unavailable.");
  }
  await FileSystem.makeDirectoryAsync(importRoot, { intermediates: true });

  const picked: PickedPhoto[] = [];
  for (let offset = 0; offset < images.length; offset += 4) {
    const batch = images.slice(offset, offset + 4);
    const localized = await Promise.all(
      batch.map(async (item): Promise<PickedPhoto> => {
        const safeFilename = item.mediaFile.filename.replace(
          /[^a-zA-Z0-9._-]/g,
          "_",
        );
        const destination = `${importRoot}${item.id}-${safeFilename}`;
        const existing = await FileSystem.getInfoAsync(destination);
        if (!existing.exists) {
          const download = await FileSystem.downloadAsync(
            `${item.mediaFile.baseUrl}=d`,
            destination,
            { headers: { Authorization: `Bearer ${accessToken}` } },
          );
          if (download.status < 200 || download.status >= 300) {
            throw new Error(
              `Could not download ${item.mediaFile.filename} (${download.status}).`,
            );
          }
        }

        return {
          id: item.id,
          uri: destination,
          filename: item.mediaFile.filename,
          width: item.mediaFile.mediaFileMetadata?.width
            ? Number(item.mediaFile.mediaFileMetadata.width)
            : undefined,
          height: item.mediaFile.mediaFileMetadata?.height
            ? Number(item.mediaFile.mediaFileMetadata.height)
            : undefined,
          mimeType: item.mediaFile.mimeType,
          source: "google-photos",
        };
      }),
    );
    picked.push(...localized);
  }
  return picked;
}

async function acquireAccessToken(): Promise<string | null> {
  ensureConfigured();
  await GoogleSignin.hasPlayServices({ showPlayServicesUpdateDialog: true });
  try {
    await GoogleSignin.signIn();
  } catch (error) {
    // User dismissed the native account chooser — treat as a clean cancel.
    if (isErrorWithCode(error) && error.code === statusCodes.SIGN_IN_CANCELLED) {
      return null;
    }
    throw error;
  }
  const { accessToken } = await GoogleSignin.getTokens();
  return accessToken;
}

export function useGooglePhotosPicker(): {
  configured: boolean;
  pickGooglePhotos: () => Promise<PickedPhoto[]>;
} {
  const pickGooglePhotos = useCallback(async (): Promise<PickedPhoto[]> => {
    if (!GOOGLE_WEB_CLIENT_ID) {
      throw new Error(
        "Google Photos needs EXPO_PUBLIC_GOOGLE_WEB_CLIENT_ID (a Web OAuth client id). See GOOGLE_OAUTH_SETUP.md.",
      );
    }

    await recordGooglePhotosConsent();
    const accessToken = await acquireAccessToken();
    if (!accessToken) {
      return [];
    }

    const session = await googleFetch<PickerSession>("/sessions", accessToken, {
      method: "POST",
      body: JSON.stringify({ pickingConfig: { maxItemCount: "2000" } }),
    });

    await WebBrowser.openBrowserAsync(session.pickerUri, { showTitle: true });
    const completedSession = await waitForSelection(session, accessToken);
    try {
      return await listSelectedMedia(completedSession.id, accessToken);
    } finally {
      await googleFetch<void>(
        `/sessions/${encodeURIComponent(completedSession.id)}`,
        accessToken,
        { method: "DELETE" },
      );
    }
  }, []);

  return { configured: Boolean(GOOGLE_WEB_CLIENT_ID), pickGooglePhotos };
}
