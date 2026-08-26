import * as FileSystem from "expo-file-system/legacy";

import type { PickedPhoto } from "./picked-photo";

const IMAGE_EXTENSIONS = new Set([
  "avif",
  "bmp",
  "gif",
  "heic",
  "heif",
  "jpeg",
  "jpg",
  "png",
  "tif",
  "tiff",
  "webp",
]);

const MAX_FOLDER_PHOTOS = 5_000;

function filenameFromUri(uri: string): string {
  const encodedName = uri.split("/").at(-1) ?? "photo";
  try {
    return decodeURIComponent(encodedName);
  } catch {
    return encodedName;
  }
}

function isImageUri(uri: string): boolean {
  const filename = filenameFromUri(uri);
  const extension = filename.split(".").at(-1)?.toLowerCase();
  return extension ? IMAGE_EXTENSIONS.has(extension) : false;
}

export async function pickLocalFolder(): Promise<PickedPhoto[]> {
  const permission =
    await FileSystem.StorageAccessFramework.requestDirectoryPermissionsAsync();

  if (!permission.granted) {
    return [];
  }

  const directories = [permission.directoryUri];
  const photos: PickedPhoto[] = [];

  while (directories.length > 0 && photos.length < MAX_FOLDER_PHOTOS) {
    const directoryUri = directories.shift();
    if (!directoryUri) {
      break;
    }

    const children =
      await FileSystem.StorageAccessFramework.readDirectoryAsync(directoryUri);

    for (const uri of children) {
      if (photos.length >= MAX_FOLDER_PHOTOS) {
        break;
      }

      const info = await FileSystem.getInfoAsync(uri);
      if (info.exists && info.isDirectory) {
        directories.push(uri);
      } else if (info.exists && isImageUri(uri)) {
        const filename = filenameFromUri(uri);
        photos.push({
          id: `folder-${photos.length}-${uri}`,
          uri,
          filename,
          source: "local-folder",
        });
      }
    }
  }

  return photos;
}
