import * as AuthSession from "expo-auth-session";
import * as FileSystem from "expo-file-system/legacy";
import * as SecureStore from "expo-secure-store";
import * as WebBrowser from "expo-web-browser";
import { useCallback } from "react";

import { recordGooglePhotosConsent } from "../privacy/consent-ledger";
import type { PickedPhoto } from "./picked-photo";

WebBrowser.maybeCompleteAuthSession();

export const GOOGLE_PHOTOS_SCOPE =
  "https://www.googleapis.com/auth/photospicker.mediaitems.readonly";
export const GOOGLE_REDIRECT_URI = "com.photeo.app:/oauthredirect";

const API_ROOT = "https://photospicker.googleapis.com/v1";
const TOKEN_KEY = "photeo.google-oauth-token.v1";
const GOOGLE_CLIENT_ID = process.env.EXPO_PUBLIC_GOOGLE_ANDROID_CLIENT_ID ?? "";

const discovery: AuthSession.DiscoveryDocument = {
  authorizationEndpoint: "https://accounts.google.com/o/oauth2/v2/auth",
  tokenEndpoint: "https://oauth2.googleapis.com/token",
  revocationEndpoint: "https://oauth2.googleapis.com/revoke",
};

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

export function useGooglePhotosPicker(): {
  configured: boolean;
  pickGooglePhotos: () => Promise<PickedPhoto[]>;
} {
  const [request, , promptAsync] = AuthSession.useAuthRequest(
    {
      clientId: GOOGLE_CLIENT_ID || "google-oauth-not-configured",
      codeChallengeMethod: AuthSession.CodeChallengeMethod.S256,
      redirectUri: GOOGLE_REDIRECT_URI,
      responseType: AuthSession.ResponseType.Code,
      scopes: [GOOGLE_PHOTOS_SCOPE],
      usePKCE: true,
      extraParams: { access_type: "offline", prompt: "consent" },
    },
    discovery,
  );

  const pickGooglePhotos = useCallback(async (): Promise<PickedPhoto[]> => {
    if (!GOOGLE_CLIENT_ID) {
      throw new Error(
        "Google Photos needs EXPO_PUBLIC_GOOGLE_ANDROID_CLIENT_ID. See GOOGLE_OAUTH_SETUP.md.",
      );
    }
    if (!request) {
      throw new Error("Google sign-in is still loading. Please try again.");
    }

    await recordGooglePhotosConsent();
    const authResult = await promptAsync();
    if (authResult.type !== "success" || !authResult.params.code) {
      return [];
    }

    const token = await AuthSession.exchangeCodeAsync(
      {
        clientId: GOOGLE_CLIENT_ID,
        code: authResult.params.code,
        extraParams: { code_verifier: request.codeVerifier ?? "" },
        redirectUri: GOOGLE_REDIRECT_URI,
      },
      discovery,
    );
    await SecureStore.setItemAsync(
      TOKEN_KEY,
      JSON.stringify({
        accessToken: token.accessToken,
        expiresIn: token.expiresIn,
        issuedAt: token.issuedAt,
        refreshToken: token.refreshToken,
      }),
    );

    const session = await googleFetch<PickerSession>("/sessions", token.accessToken, {
      method: "POST",
      body: JSON.stringify({ pickingConfig: { maxItemCount: "1000" } }),
    });

    await WebBrowser.openBrowserAsync(session.pickerUri, {
      showTitle: true,
    });
    const completedSession = await waitForSelection(session, token.accessToken);
    try {
      return await listSelectedMedia(completedSession.id, token.accessToken);
    } finally {
      await googleFetch<void>(
        `/sessions/${encodeURIComponent(completedSession.id)}`,
        token.accessToken,
        { method: "DELETE" },
      );
    }
  }, [promptAsync, request]);

  return { configured: Boolean(GOOGLE_CLIENT_ID), pickGooglePhotos };
}
