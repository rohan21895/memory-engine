// Offline CLI. It intentionally lives beside the pure harness and is never
// imported by the app bundle.
// @ts-expect-error The Expo app deliberately does not ship Node declarations.
import { readdirSync, readFileSync } from "node:fs";

// @ts-expect-error Node's native TypeScript runner requires the extension.
import { runFaceSharpnessPolicyHarness, type FaceRegionMeasurement, type RepoFixtureCoverage } from "./face-sharpness-policy-harness.ts";

type FaceRecordFixture = {
  media_id?: unknown;
  detection?: {
    face_area_ratio?: unknown;
  };
  attributes?: {
    sharpness?: unknown;
  };
};

type MediaRecordFixture = {
  media_id?: unknown;
  media?: { media_id?: unknown };
  image?: unknown;
  faces?: {
    face_count?: unknown;
  } | null;
};

const fixtures = loadRepoFixtureCoverage();
console.log(JSON.stringify(runFaceSharpnessPolicyHarness(fixtures), null, 2));

function loadRepoFixtureCoverage(): RepoFixtureCoverage {
  const faceDirectory = new URL(
    "../../../../contracts/fixtures/face-record/valid/",
    import.meta.url,
  );
  const mediaDirectory = new URL(
    "../../../../contracts/fixtures/media-record/valid/",
    import.meta.url,
  );
  const facesByMedia = new Map<string, FaceRegionMeasurement[]>();

  for (const name of readdirSync(faceDirectory).filter((entry: string) =>
    entry.endsWith(".json"),
  )) {
    const fixture = parseJson<FaceRecordFixture>(new URL(name, faceDirectory));
    const mediaId = stringValue(fixture.media_id);
    const areaRatio = finiteNumber(fixture.detection?.face_area_ratio);
    const regionalSharpness = finiteNumber(fixture.attributes?.sharpness);
    if (!mediaId || areaRatio === undefined || regionalSharpness === undefined) {
      continue;
    }
    const measurement = { areaRatio, regionalSharpness };
    const existing = facesByMedia.get(mediaId);
    if (existing) existing.push(measurement);
    else facesByMedia.set(mediaId, [measurement]);
  }

  let photoMediaRecordsWithFaces = 0;
  let incompletePhotoMediaRecords = 0;
  const completePhotos: RepoFixtureCoverage["completePhotos"] = [];
  for (const name of readdirSync(mediaDirectory).filter((entry: string) =>
    entry.endsWith(".json"),
  )) {
    const fixture = parseJson<MediaRecordFixture>(new URL(name, mediaDirectory));
    const expectedFaceCount = finiteNumber(fixture.faces?.face_count);
    if (fixture.image == null || expectedFaceCount === undefined || expectedFaceCount <= 0) {
      continue;
    }
    photoMediaRecordsWithFaces += 1;
    const mediaId =
      stringValue(fixture.media_id) ?? stringValue(fixture.media?.media_id);
    const measuredFaces = mediaId ? facesByMedia.get(mediaId) ?? [] : [];
    if (mediaId && measuredFaces.length === expectedFaceCount) {
      completePhotos.push({ mediaId, faces: measuredFaces });
    } else {
      incompletePhotoMediaRecords += 1;
    }
  }

  return {
    faceRecords: Array.from(facesByMedia.values()).reduce(
      (sum, faces) => sum + faces.length,
      0,
    ),
    photoMediaRecordsWithFaces,
    completePhotoMediaRecords: completePhotos.length,
    incompletePhotoMediaRecords,
    completePhotos,
  };
}

function parseJson<Value>(url: URL): Value {
  return JSON.parse(readFileSync(url, "utf8")) as Value;
}

function stringValue(value: unknown): string | undefined {
  return typeof value === "string" && value.length > 0 ? value : undefined;
}

function finiteNumber(value: unknown): number | undefined {
  return typeof value === "number" && Number.isFinite(value)
    ? value
    : undefined;
}
