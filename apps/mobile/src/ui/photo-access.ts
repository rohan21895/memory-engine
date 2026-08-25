import * as MediaLibrary from "expo-media-library/legacy";

/**
 * What the OS actually lets Photeo read, as opposed to what `status` says.
 *
 * Two native behaviours make a bare `status === "granted"` check wrong, and
 * every screen that asks for photos has to survive both:
 *
 *  1. Android 14+ "Select photos". READ_MEDIA_IMAGES comes back DENIED while
 *     READ_MEDIA_VISUAL_USER_SELECTED is granted, and expo-media-library
 *     rewrites the response to `granted: true` with
 *     `accessPrivileges: "limited"`. The app sees success and a near-empty
 *     library — the silent failure the beta kept reporting as "no photos".
 *  2. Declaring ACCESS_MEDIA_LOCATION (needed for photo places) adds a
 *     permission existing installs never granted. On Android 10-13 that turns
 *     an already-working install into `status: "denied"`; on 14+ it drops it
 *     into the "limited" branch. Both are repaired by one extra prompt.
 */
export type PhotoAccess = {
  /** Photeo can read at least some photos. Never gate the whole app on more. */
  readable: boolean;
  /** Only the handful of photos the user hand-picked are visible. */
  limited: boolean;
  /** The OS will still show a prompt; otherwise only Settings can change this. */
  canAskAgain: boolean;
};

export const NO_PHOTO_ACCESS: PhotoAccess = {
  readable: false,
  limited: false,
  canAskAgain: true,
};

function toAccess(permission: MediaLibrary.PermissionResponse): PhotoAccess {
  const limited = permission.accessPrivileges === "limited";
  return {
    readable: permission.granted || limited,
    limited,
    canAskAgain: permission.canAskAgain,
  };
}

export async function getPhotoAccess(): Promise<PhotoAccess> {
  try {
    return toAccess(await MediaLibrary.getPermissionsAsync());
  } catch {
    return NO_PHOTO_ACCESS;
  }
}

export async function requestPhotoAccess(): Promise<PhotoAccess> {
  try {
    return toAccess(await MediaLibrary.requestPermissionsAsync());
  } catch {
    return NO_PHOTO_ACCESS;
  }
}

/**
 * True when we hold something less than full access but the OS would still
 * prompt — i.e. one more request could genuinely widen what Photeo can see.
 */
export function canWidenAccess(access: PhotoAccess): boolean {
  return (!access.readable || access.limited) && access.canAskAgain;
}
