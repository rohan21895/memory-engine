# CX-19 face and subject quality investigation

This is measurement only. No album-selection rule changed.

## Real caller and corpus

`App.tsx` calls `buildAlbum(next, 24, ...)`. Above 500 source photos,
`buildAlbum` pre-ranks the library and sends exactly 64 candidates through the
analysis proxy, face detector, regional pixel measurement, and album planner.
The harness therefore models 96 capped runs of 64 deeply analyzed candidates,
not an impossible path where every photo in a large source library receives
the expensive analysis. The real planner selects 24 photos per run.

The deterministic corpus has seed `0xc019face` and 6,144 measured candidates:

- 5,229 (85.1%) contain a detected face.
- 3,251 (52.9%) are group photos with at least three faces.
- 5,915 pass the current largest-face regional-sharpness floor.
- 2,304 photos are selected by the current planner across the 96 runs.
- Sixteen of each run's 48 takes contain two candidates with an exactly equal,
  quantized current score. This gives the soft policy real ties to break; it
  cannot move a merely close score.

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

The rejection denominator is the 5,915 photos that pass today's dominant-face
check. “Selected lost” is against the 2,304 photos selected today. For the soft
policy, “lost” means replaced by an equal-current-score photo from the same
take; it does not mean rejected or that the album becomes shorter.

| Candidate policy | Newly rejected | Currently selected lost |
| --- | ---: | ---: |
| Minimum across every detected face, hard gate | 833 (14.1%) | 317 (13.8%) |
| Minimum across faces at least 50% of largest area, hard gate | 170 (2.9%) | 63 (2.7%) |
| Minimum across faces at least 25% of largest area, hard gate | 408 (6.9%) | 146 (6.3%) |
| All-face minimum as an exact-score tie-break only | 0 (0.0%) | 312 replaced (13.5%) |

Run from `apps/mobile` with Node 22 or newer:

```sh
node --experimental-strip-types src/selection/face-sharpness-policy-measure.ts
```

The harness test has explicit non-vacuity guards: the corpus must remain above
5,000 photos and majority-group; every hard policy must exercise both counters;
the soft path must replace at least one selected ID while rejecting zero; and a
second seeded run must be byte-identical.

## Recommendation

If one of these four policies ships after real-library validation, ship the
soft exact-tie policy. It closes some avoidable choices without removing a
single candidate or shortening an album. Do not ship any hard gate from this
synthetic result: even the least aggressive 50% rule removes 63 current picks,
while the all-face rule removes 317. In a group-heavy family library, smaller
background faces are precisely where deliberate depth of field makes softness
normal.

The 312 replacements are not evidence that all are improvements. Before
shipping even the soft rule, run the same measurement on consented on-device
telemetry or a labeled local benchmark and visually review changed pairs.

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
