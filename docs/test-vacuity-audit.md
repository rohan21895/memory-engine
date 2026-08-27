# Test vacuity audit

Audited on branch `codex/test-audit` from baseline `69f49a8` (2026-08-27).
The unit of classification is one `src/**/*.test.ts` file, matching Node's
60-file mobile test count, plus one classification for each of the eight
`scratch/` harnesses. The three desktop test files are included. Total: 71
artifacts.

## Result

| Class | Count |
| --- | ---: |
| SOUND | 61 |
| VACUOUS | 6 |
| CIRCULAR | 1 |
| UNMEASURABLE | 3 |

These are baseline findings. A finding remains classified by what was found
even when this branch fixes it.

Every SOUND verdict below came from an executed mutation, not source review.
Before each run, `git diff` or an exact `rg` check showed the mutant in the file.
Each mutant was then reversed before the next group. The executable command was
`/opt/homebrew/bin/node --experimental-strip-types --test <file>` on Node
22.22.3.

## Worst three

1. **CX-19 face sharpness policy — CIRCULAR.** `enhancedQualityScore` was changed
   to always return zero. The harness still passed and still reported 312 soft
   replacements because it assigned one rounded quality to both frames of every
   two-frame take. It failed to catch a completely dead production scorer and
   manufactured the exact ties it claimed to measure.
2. **Deferred face-cluster consolidation — VACUOUS.** The production
   `if (!opts.skipMerge)` branch was inverted. `face-cluster-skipmerge.test.ts`
   still passed because its “deferred” and “merged” fixtures both already formed
   one person. It failed to catch the scan doing consolidation at the wrong time,
   directly affecting family grouping and end-of-scan recovery.
3. **Quantized face-model fidelity harness — VACUOUS.** Production cosine was
   changed to zero and the harness was also run with a deliberately mismatched
   calibration fixture. It printed “WARNING” and exited zero, then continued to
   print fidelity numbers. It failed to block an unverified face-model decision,
   where a wrong merge is not recoverable.

The next most serious survivor was `scratch/face-anchor-coverage/measure.ts`:
forcing `anchorFor` to return `undefined` produced 0% face-anchor coverage and
30 “anchorless” people who actually owned an unshared photo, yet exited zero.

## Complete classification, ranked by blast radius

### P0 — identity, persistence, and irreversible grouping

| Artifact | Class | Executed mutation and verdict |
| --- | --- | --- |
| `apps/mobile/src/faces/face-cluster-skipmerge.test.ts` | VACUOUS (fixed) | Inverted `if (!opts.skipMerge)`. Baseline test passed. It needed to prove `deferred.length > merged.length`; the fixed fixture pins 2 deferred vs 1 merged and kills the inversion. |
| `apps/mobile/src/selection/face-sharpness-policy-harness.test.ts` | CIRCULAR (fixed) | Made `enhancedQualityScore` return 0. Baseline test passed because one rounded quality was constructed per take. It needed independently scored frames plus a constant-scorer sabotage; both are now present. |
| `scratch/face-anchor-coverage/measure.ts` | VACUOUS (fixed) | Made `anchorFor` always return `undefined`. Harness exited 0 with 0% face-anchor coverage. It now refuses zero exercised face anchors, any wrong anchor, or any supposedly anchorless person with an unshared photo. |
| `scratch/quant-fidelity/measure.ts` | VACUOUS (fixed) | Made production cosine return 0; a calibration mismatch only printed a warning and exited 0. Calibration mismatch is now a hard refusal, so unverified fidelity tables are not emitted. |
| `apps/mobile/src/albums/album-store.test.ts` | SOUND | Returned `[]` when the crash-recovery `.tmp` was complete. Test failed; it protects recovery from overwriting a newer shelf. |
| `apps/mobile/src/faces/face-anchor-record.test.ts` | SOUND | Made `markSamePerson` return `false` without recording. Test failed; it checks correction storage, immediate application, and rebuild survival. |
| `apps/mobile/src/faces/face-calibration.test.ts` | SOUND | Replaced the measured tail quantile with 0. Test failed on the known 0.5% tail. |
| `apps/mobile/src/faces/face-cluster-bound.test.ts` | SOUND | Made the bounded assignment similarity return 0. Test failed against the unpruned reference. |
| `apps/mobile/src/faces/face-cluster-constraints.test.ts` | SOUND | Made `scaledSimilarity` return 0. Test failed on cannot-link/assignability behavior. |
| `apps/mobile/src/faces/face-cluster-merge-equivalence.test.ts` | SOUND | Made `scaledSimilarity` return 0. Seeded equivalence cases failed. |
| `apps/mobile/src/faces/face-cluster-merge.test.ts` | SOUND | Made bounded assignment similarity return 0. Expected identity merges failed. |
| `apps/mobile/src/faces/face-cluster-recovery.test.ts` | SOUND | Made exported cosine return 0. Recovery/ground-truth checks failed. |
| `apps/mobile/src/faces/face-cluster-suggest.test.ts` | SOUND | Made bounded assignment similarity return 0. Suggestion ordering/availability checks failed. |
| `apps/mobile/src/faces/face-cluster.test.ts` | SOUND | Made bounded assignment similarity return 0. Basic and incremental grouping checks failed. |
| `apps/mobile/src/faces/face-constraints.test.ts` | SOUND | Made `scaledSimilarity` return 0. Anchor resolution and must/cannot application failed. |
| `apps/mobile/src/faces/face-index-empty-batch.test.ts` | SOUND | Made `scaledSimilarity` return 0. Empty-batch consolidation equivalence failed. |
| `apps/mobile/src/faces/face-index-persist.test.ts` | SOUND | Made `shouldPersistIndex` always false. Dirty and changed-shape cases failed, including the data-loss backstop. |
| `apps/mobile/src/faces/face-index-recluster-cost.test.ts` | SOUND | Existing test executes its own refinement predicate sabotage in both directions; both deliberately wrong predicates are rejected. |
| `apps/mobile/src/faces/face-index.test.ts` | SOUND | Made bounded assignment similarity return 0. Index construction/calibration assertions failed. |
| `apps/mobile/src/faces/face-must-link-survives.test.ts` | SOUND | Made bounded assignment similarity return 0. Stored must-link survival failed. |
| `apps/mobile/src/faces/face-observations-file.test.ts` | SOUND | Made observation persistence return before writing. Test failed on the crash-safe split file path. |
| `apps/mobile/src/faces/face-prototypes.test.ts` | SOUND | Made `scaledSimilarity` return 0. Prototype linkage assertions failed. |
| `apps/mobile/src/faces/face-suggest-ranking.test.ts` | SOUND | Made bounded assignment similarity return 0. Repair-size ranking failed. |
| `apps/mobile/src/faces/face-thumbnails.test.ts` | SOUND | Made cosine return 0. Safe avatar reattachment failed. |
| `scratch/multi-prototype/measure.ts` | UNMEASURABLE | The repository contains neither required observations nor an equivalent membership fixture. No in-repo run can exercise its claim about the owner's partition; a result must not be promoted without those inputs. |
| `scratch/face-threshold-fairness/evaluate.py` | UNMEASURABLE | Requires licensed demographic pair data and real image/model execution absent from the repository. It correctly returns 2 for underpowered groups, but the fairness property cannot be exercised here. |

### P1 — photo selection and album contents

| Artifact | Class | Executed mutation and verdict |
| --- | --- | --- |
| `apps/mobile/src/selection/album-fixtures.test.ts` | SOUND | Made `albumFixtures()` return `[]`. Its fixture-count/vacuity guard failed before any pinned-album claim. |
| `apps/mobile/src/selection/album-objective.test.ts` | SOUND | Made `marginalGain` return negative infinity. Objective and maximizer checks failed; its internal anti-submodular sabotage is also active. |
| `apps/mobile/src/selection/album-planner.test.ts` | SOUND | Forced `selectedIds` empty. Coverage, people-floor, pins, caps, and rejection checks failed. |
| `apps/mobile/src/selection/analysis-degradation.test.ts` | SOUND | Made `recordDegraded` a no-op. OOM attribution and privacy-filter checks failed. |
| `apps/mobile/src/selection/build-album.test.ts` | SOUND | Forced planner `selectedIds` empty. The face-region gate integration checks failed. |
| `apps/mobile/src/selection/candidate-prepass-people.test.ts` | SOUND | Made `chooseHeavyAnalysisCandidates` return `[]`. Familiar-person shortlist coverage failed. |
| `apps/mobile/src/selection/candidate-prepass.test.ts` | SOUND | Made `chooseHeavyAnalysisCandidates` return `[]`. Cap, pin, time/place/content, and ordering checks failed. |
| `apps/mobile/src/selection/candidate-probe-cache.test.ts` | SOUND | Made every cache key `constant`. Identity/version/privacy and cache hit/miss assertions failed. |
| `apps/mobile/src/selection/candidate-quality-probe.test.ts` | SOUND | Made BlurHash decode always return `undefined`. Pixel and derived-quality checks failed. |
| `apps/mobile/src/selection/concurrent-map.test.ts` | SOUND | Forced one worker regardless of requested limit. The explicit non-serialization assertion failed. |
| `apps/mobile/src/selection/deep-analysis-timing.test.ts` | SOUND | Made timing `summarize()` return `[]`. Phase aggregates failed. |
| `apps/mobile/src/selection/image-quality.test.ts` | SOUND | Made pixel sharpness always 0. Synthetic edge and regional-sharpness checks failed. |
| `apps/mobile/src/selection/pose-framing.test.ts` | SOUND | Made `bodyCoverage` always `unknown`. Framing/crop/joint checks failed. |
| `apps/mobile/src/selection/pose.test.ts` | SOUND | Made pose `signature` return `{}`. Mirror, geometry, and clustering checks failed. |
| `apps/mobile/src/selection/preference-label-store.test.ts` | SOUND | Made every pseudonymous asset id `asset:constant`. Collision and round-trip assertions failed. |
| `apps/mobile/src/selection/quality-signals.test.ts` | SOUND | Made every category `scene`. Face-count/category checks failed. |
| `apps/mobile/src/selection/select-best-shots.test.ts` | SOUND | Made `enhancedQualityScore` return 0. Quality, take-winner, and album selection assertions failed. |
| `apps/mobile/src/selection/selection-quality-regression.test.ts` | SOUND | Made `enhancedQualityScore` return 0. CX-16 winner and duplicate/quality regression assertions failed. |
| `scratch/pose-dedup/measure.ts` | UNMEASURABLE | Raised production `maxPerBodyPose` from 2 to 999. Output changed, but all three fixtures still correctly declared capacity below a 24-photo album. Four pose words can supply at most eight capped picks, so the fixture cannot verdict the cap. |

### P2 — importing, ML transforms, and scan lifecycle

| Artifact | Class | Executed mutation and verdict |
| --- | --- | --- |
| `apps/mobile/src/faces/avatar-backfill-queue.test.ts` | SOUND | Made the backfill queue always empty. Starvation/budget ordering assertions failed. |
| `apps/mobile/src/faces/face-detector.test.ts` | SOUND | Made `scaleFaceBox` return its input for every scale. Source/patch landmark covariance failed. |
| `apps/mobile/src/faces/face-filter.test.ts` | SOUND | Made union/intersection always return `null`. Any/all semantics failed. |
| `apps/mobile/src/faces/face-thumbnail.test.ts` | SOUND | Made `createFacePeopleQuery` return no summaries. Empty-thumbnail and cover-photo projection failed. |
| `apps/mobile/src/faces/person-recurrence.test.ts` | SOUND | Discarded all people at recurrence construction. Occasion/day/familiarity/ranking checks failed. |
| `apps/mobile/src/faces/scan-task.test.ts` | SOUND | Made `holdScanTask` resolve immediately. Pending-until-stop contract failed. |
| `apps/mobile/src/import/incremental-index.test.ts` | SOUND | Made incremental target always 0. Added/replacement detection failed. |
| `apps/mobile/src/import/offline-geocode.test.ts` | SOUND | Made haversine distance always 0. Distance, radius, and nearest-place cases failed. |
| `apps/mobile/src/import/photo-index.test.ts` | SOUND | Made every coordinate cell `null`. Stable cell/place and round-trip checks failed. |
| `apps/mobile/src/ml/face-align.test.ts` | SOUND | Made `alignmentPairs` always `undefined`. Geometry/transform alignment checks failed. |
| `apps/mobile/src/ml/face-crop.test.ts` | SOUND | Made landmark-to-patch mapping return source coordinates unchanged. Crop-space mapping failed. |
| `apps/mobile/src/ml/facenet.test.ts` | SOUND | Made embedding output parsing always `undefined`. Normalization/output checks failed. |
| `apps/mobile/src/ml/model-cache.test.ts` | SOUND | Counted 1,000 runs per acquisition. Reload/retire/load-stat assertions failed. |
| `apps/mobile/src/ml/movenet.test.ts` | SOUND | Changed letterbox scale from `min` to `max`. Aspect-preserving tensor layout failed. |
| `apps/mobile/src/ml/tinyclip.test.ts` | SOUND | Forced both center-crop origins to 0. Portrait/landscape crop checks failed. |
| `apps/mobile/src/quant/quant-fidelity.test.ts` | SOUND | Made cosine always 0. The suite failed; this file's six built-in implementation mutations remain the stronger standard. |
| `apps/mobile/src/eval-gates/cx25-gates.test.ts` | SOUND | Reversed degradation ordering from decreasing to increasing. Standing gate failed. |

### P3 — presentation, desktop helpers, and research-only performance output

| Artifact | Class | Executed mutation and verdict |
| --- | --- | --- |
| `apps/desktop/src/culling.test.ts` | SOUND | Reversed selected-item filtering. Counts and duration accounting failed. |
| `apps/desktop/src/format.test.ts` | SOUND | Divided video milliseconds by 100 instead of 1,000. Duration formatting failed. |
| `apps/desktop/src/virtual-grid.test.ts` | SOUND | Made maximum mounted tiles 100,000. Viewport-boundedness failed. |
| `apps/mobile/src/ui/components/place-tree.test.ts` | SOUND | Made tree construction return `[]`. Hierarchy/search/flattening checks failed. |
| `apps/mobile/src/ui/reasons.test.ts` | SOUND | Made chosen and alternative copy both `same`. Reason-specific copy checks failed. |
| `apps/mobile/src/ui/screens/face-merge-review.test.ts` | SOUND | Made pair resolution always `undefined`. Counts, co-occurrence evidence, and queue behavior failed. |
| `scratch/embedding-memory/measure.js` | VACUOUS (fixed) | Changed the claimed `number[]` decoder to return an `Int8Array`. Harness still exited 0 and reported a 3.5x difference between identical representations. It now asserts both representation types before measuring. |
| `scratch/framing-tiebreak-rate/measure.ts` | VACUOUS (fixed) | Made the tie detector always false. It printed `0/20000` in its “sabotage guard” but exited 0. The guard now throws unless all 20,000 forced ties are detected. |
| `scratch/merge-sweep-bench/bench.ts` | VACUOUS (fixed) | Made `extendFaceClusters` return `[]`. Benchmark exited 0 with 0 people and 0/17,768 faces accounted for. It now hard-fails empty partitions or lost faces. |

## Fixes on this branch

- Rebuilt the skip-merge fixture so consolidation is genuinely required and
  added an exact 2-to-1 sabotage guard.
- Rewired CX-19 synthetic quality through the production scorer, added a
  constant-scorer sabotage, and corrected the committed measurement: zero
  eligible exact ties, not 312 manufactured replacements.
- Turned face-anchor coverage's printed “must be 0” claims into enforced gates.
- Turned quant-fidelity calibration mismatch from a warning/pass into a refusal.
- Enforced the framing forced-tie guard, merge benchmark face accounting, and
  embedding-memory representation types.

No application selection, clustering, persistence, or rendering behavior was
changed; the changes are tests, research harness guards, and measurement docs.
