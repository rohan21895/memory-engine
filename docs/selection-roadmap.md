# Selection roadmap — the full culling framework vs. what is live

Source: owner's selection framework (Aug 2026). Verdict: the framework is the
right north star — it is the architecture the professional culling tools
(Aftershoot, Narrative Select) converge on, and it independently validates
most of what selection v2 already does. This doc maps every block to a
status so the gaps are explicit and ordered.

## Update: selection v3 shipped (Aug 2026)

The v3 build closed the top of the build order. Now LIVE (statuses below are
kept as the v2-era record; this list supersedes them):

- **Per-face expression + weighted-min aggregation** (§2 build): faces stage
  backfills `FaceAttributes.eyes_open/smile` from SigLIP-scored face crops
  (originals, ×1.3 bbox, tensor transport with a 0.9997 round-trip proof);
  selection judges the WORST significant face; `quality.face_quality` is the
  min-anchored group aggregate (`fusion.aggregate_face_quality`). Signal
  validated on a real library: closed-eyes poses 0.27–0.30 vs open 0.59–0.62,
  rendition noise ~3× below population spread.
- **Context-sensitive eye closure**: `embrace_context` axis suppresses the
  blink penalty on kisses/embraces (rank-based, top-15% trigger).
- **Cheap gates**: `screenshot_document` hard gate (absolute threshold 0.0,
  calibrated: every real photo across two libraries ≤ −0.043, never waived);
  face-region exposure floor; clipping gate on the FACE crop only — the
  whole-frame version was built first and rejected 47% of a high-key studio
  shoot (white seamless clips by intent) before E2E caught it. Whole-frame
  clipping is data, never a gate.
- **Category weight vectors**: portrait/couple/group/detail presets from
  face count + area, starter values pending PrefEvent learning.
- **Moment tier** (Event → Moment → Take): loose grouping (0.80 / 6 h) now
  drives the diversity term; the inert scene_key axis is gone.
- **Story-role split**: >2× face-area gap splits a shot group (wide vs close
  are different roles, never "worse versions" of each other).
- **Rare-moment protection**: isolated singletons waive the moderate floors
  (never the screenshot/cut gates).
- **Explainability + swap support**: sidecar report per album
  (`outputs/selection/<digest>.json`) — every candidate accounted for exactly
  once; picks carry `chosen_because`, group alternatives carry
  `not_chosen_because` + slot-fit; `pinned_media_ids`/`excluded_media_ids`
  make re-generation respect user swaps; each swap is a future PrefEvent.
- **Eval**: first selection gate (`gates/selection-critical-errors.gate.json`)
  pins seven critical-error rates at 0 (blink-when-clean-exists, twin pairs,
  screenshot selected, rare-moment false rejection, worse-version selected,
  pin/exclude violations), each bite-tested.

Still deferred, unchanged owners: behavioural signals + EXIF burst
(Codex/ingest), Bradley-Terry preference head (needs PrefEvents), gaze /
naturalness / flattering judgment (Tier-3 taste pass), EAR blink (needs
106-point landmarks), CMYK gamut + print brightness (needs vendor ICC),
key-person importance weighting (needs review UI), swap UI (Codex/apps).

Statuses: **LIVE** (shipped, tested) · **PARTIAL** · **MISSING-CHEAP**
(days, no new models) · **MISSING-BIG** (architecture or new model) ·
**TIER-3** (frontier taste pass, needs API key) · **CODEX** (ingest/app
territory).

## 0. Hard gates

| Item | Status | Where / note |
|---|---|---|
| Corrupt/truncated, wrong orientation | LIVE | ingest + autoOrient in render |
| Exact/near-dupes, RAW+JPEG | LIVE | dedupe-primary in ranking-engine |
| Below print floor | LIVE | per-placement DPI, not global (framework §6 agrees) |
| Blur floors | LIVE | face + head-region sharpness floors |
| Cut faces | LIVE | landmark-inside-margin gate |
| Screenshot/meme/doc/receipt | MISSING-CHEAP | one more zero-shot axis pair |
| Severe clipping >15% | MISSING-CHEAP | histogram already computed in auto-develop |
| All-eyes-closed group | MISSING-BIG | needs per-face expression (see §2) |

## 1. Optical quality

Subject-region sharpness (face bbox, head box ×1.8) is live and floored —
the framework's "the metric that actually matters" is the one we measure.
Eye-region sharpness, blur-type discrimination, focus-plane correctness:
MISSING-BIG, only worth it if head-region misses real failures (it has been
quiet on both test libraries; the zero-shot `composed` axis catches motion
candids instead). Face-region exposure independent of global histogram:
MISSING-CHEAP and worth doing — auto-develop currently corrects global luma
only, so a backlit subject reads as fine. Skin-tone plausibility: adopt the
fairness note verbatim — any skin-tone feature must be evaluated across
skin tones in the eval harness before it ships. Artifacts (CA, moiré,
banding): skip until a real album shows them; DSLR + phone-HEIC libraries
haven't.

## 2. People — the structural gap

The framework's most important correction: **aggregate per-person with
weighted min, not per-frame**. Current expression axes (smile, awake,
sleeping, composed, clean_frame) are whole-frame zero-shot scores — one
blinking person in a group photo can hide behind a smiling majority.
MISSING-BIG, highest-priority next build:

1. Run expression scoring per face crop (faces stage already yields boxes +
   landmarks), aggregate with min.
2. Weight the min by person importance from face clusters (the album
   owner's family outranks a guest) — importance comes from appearance
   frequency + owner confirmation in the review queue.
3. Expected-face-count within a shot group (someone stepped out of frame).

EAR blink from existing landmarks is the one dedicated (non-zero-shot)
model worth adding here. Gaze direction, mutual gaze, Duchenne naturalness,
mid-speech mouth, "which version makes the person look fit": TIER-3 — this
is judgment, exactly what the frontier taste pass on contact sheets is for.

Baby overrides: LIVE and aligned — sleeping is a capped photo TYPE, never a
blink rejection (cross-axis sleeping>awake typing).

## 3. Composition & aesthetics

Aesthetic + composed + clean_frame axes: LIVE (rank-normalized). Learned
aesthetic as one feature, never the whole score: exactly how it's wired
(weight 0.55 among many). Crop-at-joints, pole-out-of-head: TIER-3.
**Auto-crop-then-rescore**: MISSING-BIG and the single best idea in the
framework we don't do — we already crop at layout time, so scoring the
photo as-shot punishes fixable framing. Needs re-scoring per crop candidate;
schedule after per-face scoring since crops change which faces are in frame.

## 4. Semantic / moment value

Scene diversity buckets + day-grouped pacing: LIVE. Moment classes
(ceremony, cake, varmala), peak-of-action, candid-vs-posed tagging:
TIER-3 / story-engine territory, on the build plan already.

## 5. Behavioural & metadata signals

All MISSING, most of it CODEX (ingest owns EXIF/burst-index/filename
patterns; apps own favorites, edits, view/dwell, album membership). The
framework is right that these are the cheapest strong signals — but note
they exist only for phone libraries; a DSLR delivery like the maternity
shoot arrives with none. When ingest surfaces them, they enter selection as
score priors, and "survived a manual delete pass" should be a PrefEvent.
Open an issue for Codex; do not build in selection.

## 6. Print-specific

Per-placement DPI gating with full_bleed→blur_hero→inset step-down: LIVE.
Orientation supply: LIVE (same-orientation duos). Bleed/gutter face-safety:
PARTIAL — layout insets exist, but no explicit "no face in fold zone"
check; MISSING-CHEAP once spreads exist (pages are single-leaf today).
CMYK gamut loss % + print brightness bias (+10–15% darker on paper):
MISSING-CHEAP in the print validator, gate at render not selection. Real
verdict needs a vendor ICC profile (owner task).

## 7. Set-level selection

Redundancy via embedding cosine (shot groups 0.93, selected-pair cap 0.92),
temporal coverage, person fairness (scarce-person waiver + per-person cap),
constrained greedy objective (i.e. submodular maximization, not top-N):
LIVE — this section of the framework describes the shipped design.
Narrative arc ordering: story-engine, planned. Tonal flow across spreads:
MISSING-CHEAP at layout (sort day groups by average luma delta).

## Scoring architecture deltas to adopt

- **Per-category weight vectors** (portrait/group/couple/baby/detail):
  MISSING-CHEAP — SelectionPolicy already holds every weight; classify the
  shot group (face count + face area does most of it) and load a preset.
- **Normalize within cluster, not globally**: PARTIAL — percentile ranks are
  pool-wide; domination margins are within-shot-group. Move percentile
  scope to the cluster when category vectors land.
- **Weighted-min people aggregation**: the §2 build.
- **Pairwise preference learning (Bradley-Terry)**: this is literally the
  PrefEvent contract — the framework confirms the design; build the head in
  ranking-engine when enough events exist.

## Build order

1. Per-face expression + weighted-min aggregation + EAR blink (§2) — the
   structural fix everything else leans on.
2. Cheap gates: screenshot/doc axis, clipping, face-region exposure.
3. Category weight vectors + cluster-scoped normalization.
4. Auto-crop-then-rescore.
5. Print validator: gamut %, brightness bias, fold-zone faces.
6. Codex issue: behavioural signal ingestion → score priors + PrefEvents.
7. Tier-3 taste pass for the judgment calls (gaze, naturalness, flattering).
