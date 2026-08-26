# Cold-start face-threshold fairness audit

Date: 2026-08-26

## Decision

No shipped threshold should be changed from this run. I could not produce a
demographically defensible measurement: there is no balanced verification set
in the repository or on this machine, and this execution environment could not
reach the public internet to verify or retrieve one. The browser runtime had no
available browser, and direct HTTPS failed at DNS resolution. Calling a
historically public link "obtainable right now" without opening it would repeat
the evidentiary mistake this audit is meant to correct.

The honest cold-start policy is therefore **abstention from automatic identity
joins until the device has enough local negative evidence**, not another
unmeasured cosine constant. This is a policy recommendation, not a request to
change a constant in this run.

The existing `0.39` and `0.44` constants and everything under
`apps/mobile/src/faces/` were read only and were not modified.

## Shipped model confirmed before planning

The model used by the mobile identity path is exactly:

`apps/mobile/assets/models/w600k-mbf-512-float32.tflite`

- SHA-256:
  `ca17b05ac6e92ff819d81191d865e3864f4e6779df60468f0db547c982091033`
- TFLite input observed with the local interpreter: float32
  `[N, 112, 112, 3]`, RGB/NHWC.
- TFLite output observed with the local interpreter: float32 `[N, 512]`.
- App preprocessing, confirmed in `apps/mobile/src/ml/facenet.ts`: canonical
  face alignment when landmarks are available, RGB normalization
  `(channel - 127.5) / 127.5`, then L2 normalization of the 512-dimensional
  output before cosine comparison.

There is a separate model-governance gap: `MODEL-NOTICES.md` documents the old
192-dimensional MobileFaceNet artifact but does not yet document this w600k_mbf
artifact's source weights, training-data grant, or licence. That does not alter
the fairness result, but it should be resolved before commercial release.

## Dataset access audit

### Important scope limitation

The table separates a dataset's **published/historical access model** from what
was actually verified in this run. The former is useful triage; it is not a
claim that the download still works on the date above. Every row remained
unobtained because live access was unavailable. No credentials, application,
licence click-through, or unofficial mirror was used.

| Dataset | Published licence/terms | Published access gate | Verification supervision | Result for this run |
| --- | --- | --- | --- | --- |
| RFW (Racial Faces in-the-Wild) test set | Project terms describe non-commercial research use; no standard open-data/SPDX licence, and original-image copyrights remain upstream | The project has historically linked test archives directly (Google Drive/Baidu) without an approved application; the link could not be opened here, so current availability and any intervening click-through are unverified | Yes. The documented protocol has four subsets (African, Asian, Caucasian, Indian), each with 3,000 genuine and 3,000 impostor pairs | Best first candidate if the official test archive and terms can be live-verified. Not measured here |
| BUPT-Balancedface | Research/non-commercial dataset terms, not an unrestricted open-data licence | Distributed through a licence/application process tied to an affiliation; approval is required, and institutional contact details are part of the request flow | Identity labels for a balanced training corpus; no canonical same/different verification-pair protocol | Blocked by the no-application rule and not a ready verification benchmark |
| FairFace | The project publishes the release under CC BY 4.0; the licences of the underlying Flickr images still matter independently | Historically direct linked archives, no institutional approval; current links could not be tested here | Age, perceived gender, and race labels only. No person identity and no same/different pairs | Downloadability would not make it usable for face verification. Treating two FairFace rows as different people would create false negative labels |
| CASIA-WebFace | Academic/research-use database agreement; not an unrestricted commercial data licence | Official distribution requires a completed application/licence agreement and institutional affiliation/contact | Identity labels for 10,575 subjects; no demographic verification protocol or fixed verification pairs | Blocked by the no-application rule; also unsuitable as a balanced demographic test protocol |
| DemogPairs | Could not verify a canonical licence from a live primary source | Could not verify a canonical current image download or whether access to the underlying face images is separate | The work is a verification-pair protocol by design, but a pair list alone is not sufficient if its underlying images are separately controlled | Unresolved means blocked. A paper or pair list is not evidence that the image payload can legally and practically be obtained |
| BFW (Balanced Faces in the Wild) | The public repository's code licence does not by itself license the underlying face-image bundle; dataset-use terms need a separate live check | The project has historically exposed direct data/metadata links without institutional approval; current links and terms could not be opened here | Yes. Same/different pairs with intersectional race/sex annotations | Strong second candidate after RFW, but not safe to call currently obtainable or commercially usable from this run |
| DiveFace | Published for research on demographic bias; not treated here as unrestricted commercial image data | Historically linked from the project repository; current download and terms could not be checked | Identity labels across six coarse demographic classes, not a canonical verification-pair file | Could generate pairs, but pair construction becomes an extra experimental choice; inferior to a fixed protocol |

Primary pages that must be checked live before a future acquisition:

- RFW project and test set: <http://www.whdeng.cn/RFW/> and
  <http://www.whdeng.cn/RFW/testing.html>
- BUPT-Balancedface training-data page:
  <http://www.whdeng.cn/RFW/Trainingdataste.html>
- FairFace: <https://github.com/joojs/fairface>
- CASIA-WebFace official database page:
  <http://www.cbsr.ia.ac.cn/english/CASIA-WebFace-Database.html>
- BFW: <https://github.com/visionjo/facerec-bias-bfw>
- DiveFace: <https://github.com/BiDAlab/DiveFace>

For DemogPairs, a future run must first resolve the canonical author-hosted
project/paper from a live scholarly index and then follow the provenance of the
actual images. I did not invent a URL from memory.

### Why the nominally balanced sets still need care

RFW balances four coarse race categories but does not represent a family-photo
population: kinship, infants, children, older relatives, motion blur, phone
cameras, and repeated household environments all affect the impostor tail.
BFW improves intersectional race/sex coverage but still does not cover those
factors. FairFace contributes age labels but has no identity supervision.
Consequently, RFW or BFW can remove the one-family provenance problem, but
neither can replace on-device calibration. A future benchmark threshold should
only be a conservative temporary bar.

## Why a fair number cannot be derived from the model offline

A 0.5% false-accept threshold is the 99.5th-percentile tail of the
**different-person score distribution**, conditioned on the demographic group
and the complete preprocessing path. The TFLite weights provide a mapping from
pixels to vectors; they do not provide that population distribution.

This is an identifiability problem. The same fixed model is compatible with two
possible new-user populations whose worst-group impostor tails are different.
Nothing in the model file or its 512-dimensional geometry distinguishes which
population will arrive. Uniform random vectors on a hypersphere are not a
substitute: real face embeddings are neither uniform nor independent, and
relatives are precisely the hard impostors a family app must model. Therefore a
numeric 0.5% FAR threshold cannot be inferred from model dimension, LFW, or an
unlabelled pile of faces.

No qualifying images or pair labels were found locally, so running the model on
synthetic pixels would only test the harness, not measure a face-recognition
error rate.

## Scratch harness

`scratch/face-threshold-fairness/evaluate.py` is ready for a future approved
dataset. It does not download data and adds no app dependency. It:

1. refuses to run if the model SHA-256 differs from the shipped artifact;
2. consumes explicit `path_a,path_b,is_same,group_a,group_b` CSV pairs;
3. runs aligned RGB crops through the TFLite model with the app's numeric
   normalization and L2-normalizes its output;
4. reports impostor quantiles for each within-demographic group, every
   demographic pair stratum, and every group-involved stratum;
5. calculates each group's conservative empirical 0.5% FAR threshold and uses
   the **maximum** as the candidate, rather than pooling groups; and
6. reports measured FAR and a 95% Wilson interval at that candidate, and marks
   groups with fewer than 1,000 impostor pairs as underpowered.

Example, after independently obtaining a licensed pair manifest:

```sh
python3 scratch/face-threshold-fairness/evaluate.py \
  --pairs /path/to/pairs.csv \
  --dataset-root /path/to/aligned-faces \
  --output /tmp/w600k-fairness.json
```

The harness was smoke-tested against the shipped model using generated pixels
and a six-row synthetic pair manifest. That validated model loading, hashing,
batch inference, cosine scoring, group accounting, and conservative threshold
selection. The resulting synthetic score was deliberately discarded; it has no
face-verification meaning.

The harness assumes the dataset supplies canonical aligned face crops. Resizing
an aligned crop is supported and counted. Feeding raw, unaligned faces is not:
that would measure a preprocessing error. Before a production recommendation,
the winning threshold should also be confirmed end to end with the mobile
detector/alignment path, because public benchmarks commonly distribute their
own pre-aligned crops.

## Statistical cost of shortening self-calibration

`CALIBRATION_MIN_PAIRS=200` is already very small for a 0.5% tail:

| Negative pairs | Expected observations in the top 0.5% | Exact 95% upper bound on FAR after **zero** observed false accepts |
| ---: | ---: | ---: |
| 50 | 0.25 | 5.82% |
| 100 | 0.50 | 2.95% |
| 200 | 1.00 | 1.49% |
| 600 | 3.00 | 0.498% |
| 1,000 | 5.00 | 0.299% |
| 2,000 | 10.00 | 0.150% |

The last column is `1 - 0.05^(1/n)`. At least 598 independent negative pairs
with zero false accepts are needed merely to put a one-sided 95% upper bound
below 0.5%. Selecting a 99.5th percentile on the same sample is less certain
than that validation calculation. At 200 pairs, the target tail is effectively
one order statistic; lowering the minimum to 100 or 50 makes it half or a
quarter of an expected observation. It shortens the wait by replacing evidence
with sampling noise.

Same-photo pairs are also dependent: one crowded photo contributes many pairs
sharing faces, lighting, camera, and scene. Effective sample size is therefore
lower than the raw count, so the table is optimistic.

## Recommendation

1. Do not change `0.39` or `0.44` without a completed measurement.
2. Do not lower `CALIBRATION_MIN_PAIRS` as the fairness fix. The statistical
   cost moves in the wrong direction.
3. For cold start, fail closed: keep faces unassigned (or require review) rather
   than automatically joining identities before local calibration. Abstention
   is demographically neutral with respect to false accepts and makes the cost
   visible as reduced recall, not silently merged people.
4. Accelerate evidence collection instead of weakening it: prioritize
   multi-person photos for early embedding, count independent photos/people as
   well as raw pairs, and show calibration progress. Consider a staged policy
   where early evidence can only maintain or tighten the fail-closed behavior.
5. Re-run this audit in a network-enabled environment. Verify official RFW and
   BFW terms and direct-download flows live; use neither an unofficial mirror
   nor a login/application workaround. If one is legitimately obtainable, run
   the pinned harness, preserve the manifest and JSON result, and recommend the
   maximum worst-group threshold only if every group is adequately powered.
6. Even after a balanced benchmark measurement, retain device self-calibration;
   benchmark demographics do not cover kinship and infant faces well enough to
   become a permanent global threshold.


## Independent verification of dataset availability (Claude, 2026-08-26)

The audit above could not reach the network, so its availability findings rested
on prior knowledge rather than a live check. Those checks were run separately
and are recorded here; they CONFIRM the abstention decision rather than soften
it.

- **RFW (Racial Faces in-the-Wild)** — access is by emailing the authors for
  permission ("permission to use but not reproduce or distribute the RFW
  database is granted to all researchers given that the following steps are
  properly followed: sending an e-mail to..."). Beyond the application, the
  host is currently unreachable: both `www.whdeng.cn/RFW/index.html` and
  `whdeng.cn/RFW/testing.html` refused the connection (ECONNREFUSED,
  43.139.48.164:443). Not obtainable today even if an application were made.
- **FairFace** — freely downloadable, but it carries demographic ATTRIBUTE
  labels only (7 race descriptors, binary gender, age bins) and no identity
  labels. Verification pairs cannot be constructed from it, so it cannot
  produce a threshold at any false-accept rate. It is an annotation tool, not a
  verification benchmark; the literature uses it to LABEL other datasets'
  identities, which is the opposite of what is needed here.
- **BUPT-Balancedface** — same custodian and application route as RFW; not
  independently verified because the shared host is down.

So the conclusion stands on evidence rather than assumption: there is no
demographically balanced verification set obtainable offline, and the cold-start
bar cannot honestly be re-derived right now. The per-library calibration that
takes over after CALIBRATION_MIN_PAIRS remains the real protection, because it
measures each user's own faces rather than inheriting anyone else's.
