# CX-19 face and subject quality investigation

This is measurement only. No album-selection rule changed.

## Real caller and corpus

`App.tsx` calls `buildAlbum(next, 24, ...)`. Above 500 source photos,
`buildAlbum` pre-ranks the library and sends exactly 64 candidates through the
analysis proxy, face detector, regional pixel measurement, and album planner.
The harness therefore models 96 capped runs of 64 deeply analyzed candidates,
not an impossible path where every photo in a large source library receives
the expensive analysis. The real planner selects 24 photos per run.

The corrected deterministic corpus has seed `0xc019face` and 6,144 measured candidates:

- 5,236 (85.2%) contain a detected face.
- 3,247 (52.8%) are group photos with at least three faces.
- 5,924 pass the current largest-face regional-sharpness floor.
- 2,304 photos are selected by the current planner across the 96 runs.
- Every frame is scored independently by the production
  `qualityScoreForSignals` path. There are 5,237 distinct quality values and
  zero exact-quality ties among two-frame takes that contain faces.

The synthetic secondary-face model correlates focus with relative face size
and includes a shallow-depth-of-field tail that becomes more common as a face
gets smaller. It is a deterministic sensitivity corpus, not a claim about the
owner's unmeasured library.

The repository's contract fixtures contain four face records and three photo
media records with faces. Only one photo record has a complete per-face record
set; the 2-face and 5-face photo fixtures each provide only one face record.
The harness evaluates the one complete single-face fixture (zero new rejects)
and reports the other two as incomplete instead of inventing missing values.
There are no real photo pixels paired with all-face regional-sharpness values.

## Executed result

The rejection denominator is the 5,924 photos that pass today's dominant-face
check. “Selected lost” is against the 2,304 photos selected today. For the soft
policy, no production-score tie existed, so there was nothing it could
legitimately replace.

| Candidate policy | Newly rejected | Currently selected lost |
| --- | ---: | ---: |
| Minimum across every detected face, hard gate | 870 (14.7%) | 163 (7.1%) |
| Minimum across faces at least 50% of largest area, hard gate | 151 (2.5%) | 23 (1.0%) |
| Minimum across faces at least 25% of largest area, hard gate | 436 (7.4%) | 68 (3.0%) |
| All-face minimum as an exact-score tie-break only | 0 (0.0%) | 0 replaced (0.0%) |

Run from `apps/mobile` with Node 22 or newer:

```sh
node --experimental-strip-types src/selection/face-sharpness-policy-measure.ts
```

The harness test has explicit non-vacuity guards: the corpus must remain above
5,000 photos and majority-group; every hard policy must exercise both counters;
production scoring must create independently varying frame scores; a scorer
sabotaged to return zero must collapse those scores and manufacture more than
1,000 ties; and a second seeded run must be byte-identical.

## Recommendation

Do not ship the soft exact-tie policy from this corpus: independently measured
production scores produced zero eligible ties, so the earlier 312 replacements
were created entirely by assigning one rounded quality value per take. Do not
ship any hard gate from this synthetic result either.

The earlier 312 replacements were not observed improvements; they were a
fixture artifact. Before shipping the soft rule, run the same measurement on
consented on-device telemetry or a labeled local benchmark and visually review
changed pairs.

## Subject extent available in the current Android app

Evidence inspected from the checked-in lockfile, installed package manifests,
Android Gradle dependencies, Expo autolink resolution, and local model assets:

- Autolinking resolves `@infinitered/react-native-mlkit-core` 5.0.0 and
  `@infinitered/react-native-mlkit-face-detection` 5.0.0. The latter declares
  only `com.google.mlkit:face-detection:16.+`; core declares only
  `com.google.mlkit:vision-common:17.+`.
- No installed or locked native package, Gradle dependency, Pod dependency, or
  Expo module contains ML Kit pose detection, selfie segmentation, or subject
  segmentation.
- The face wrapper can return contours, but the app initializes
  `contourMode: false`, its `NativeFace` bridge consumes only landmarks, and
  facial contours would not provide a hair or body silhouette anyway.
- A separate, already-shipped local pose path exists: `react-native-fast-tflite`
  3.0.1 autolinks, and `movenet-singlepose-lightning-int8.tflite` is statically
  bundled. The file executed here is 2,894,840 bytes with SHA-256
  `cd7cc22fa946e5d146a7b98d496853e1923e22828d3972d579973f27f91bb105`.
  `buildAlbum` already runs it and obtains 17 normalized single-person
  keypoints and confidence scores.

Therefore, no cheap subject mask exists in this app. ML Kit pose or selfie
segmentation would require a new native dependency; segmentation would also
add model weight unless a separately approved local asset strategy replaced
it. Nothing currently installed can fetch or produce a segmentation mask.

The smallest honest alternative is to reuse the existing MoveNet keypoints to
flag a confident wrist, elbow, knee, or ankle near the frame edge for manual
review. It is suitable only as a warning: it is single-person, keypoints are not
a silhouette, and it cannot establish hair extent. Until a tested local subject
mask is deliberately added, the product can promise “face cuts blocked; likely
foreground limb cuts flagged for review,” not “no cut hair or body parts.”
