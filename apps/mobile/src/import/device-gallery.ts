import * as ImagePicker from "expo-image-picker";

import type { PickedPhoto } from "./picked-photo";

export async function pickDeviceGallery(): Promise<PickedPhoto[]> {
  const result = await ImagePicker.launchImageLibraryAsync({
    allowsMultipleSelection: true,
    mediaTypes: ["images"],
    orderedSelection: true,
    quality: 1,
    selectionLimit: 0,
  });

  if (result.canceled) {
    return [];
  }

  return result.assets.map((asset, index) => ({
    id: asset.assetId ?? `device-${index}-${asset.fileName ?? asset.uri}`,
    uri: asset.uri,
    filename: asset.fileName ?? `photo-${index + 1}`,
    width: asset.width,
    height: asset.height,
    mimeType: asset.mimeType ?? undefined,
    source: "device-gallery",
  }));
}
