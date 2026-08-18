/**
 * GENERATED FILE -- DO NOT EDIT.
 *
 * Produced by contracts/codegen/generate.py from contracts/schemas/*.schema.json.
 * Edit the schemas and re-run `npm run codegen`. CI fails if these files drift
 * from the schemas (see scripts/ci/check-codegen-freshness.mjs).
 */

export type EnhancementOpKind =
  | "denoise"
  | "upscale"
  | "face_restore"
  | "sharpen"
  | "exposure"
  | "white_balance"
  | "color_transfer"
  | "spread_harmonize"
  | "outpaint_to_fit"
  | "straighten"
  | "perspective_correct"
  | "dust_removal";

export const EnhancementOpKindValues = [
  "denoise",
  "upscale",
  "face_restore",
  "sharpen",
  "exposure",
  "white_balance",
  "color_transfer",
  "spread_harmonize",
  "outpaint_to_fit",
  "straighten",
  "perspective_correct",
  "dust_removal",
] as const satisfies readonly EnhancementOpKind[];

/**
 * One planned image improvement. `license_cleared` is required and defaults to nothing:
 * half the popular restoration models are non-commercial (CodeFormer S-Lab, FLUX.1-dev),
 * and the contract is where that gets caught rather than discovered at launch.
 */
export interface EnhancementOp {
  op_id: Slug;

  kind: EnhancementOpKind;

  /**
   * Execution order within the placement. Explicit integers rather than array position so
   * a re-plan can insert an op without renumbering the world.
   */
  order: number;

  model?: ModelRef | null;

  /**
   * Whether the model behind this op passed the licence audit for commercial use. False
   * must block export -- an unlicensed enhancement is a legal defect that ships inside a
   * physical book.
   */
  license_cleared: boolean;

  /** Default: {}. */
  parameters?: Record<string, unknown>;

  strength?: Unit | null;

  /**
   * Why the op was planned: 'source is 1600px on a 300mm edge'. Feeds the user-facing
   * explanation and the review queue.
   */
  reason?: string | null;
}

export interface EventContextDateRange {
  start: Timestamp;

  end: Timestamp;
}

export interface EventContext {
  event_cluster_id?: Uuid | null;

  /**
   * Human-readable event name, typically produced by a Tier 3 pass over contact sheets:
   * 'beach day', 'night market'.
   */
  label?: string | null;

  date_range?: EventContextDateRange | null;

  /**
   * People the album is about. Only ids that passed the automated-output face gate appear
   * here. Default: [].
   */
  person_ids?: Uuid[];

  place_label?: string | null;
}

/**
 * Where the faces ended up after cropping and placement. Computed by the album engine and
 * checked by the render worker -- a face in the trim zone or the gutter is a hard export
 * block.
 */
export interface FaceSafety {
  face_count: number;

  all_faces_in_safe_zone: boolean;

  /** Default: 0. */
  faces_in_gutter?: number;

  /** Default: 0. */
  faces_in_trim_zone?: number;

  /**
   * Distance from the nearest face to the nearest unsafe boundary. Negative means a face
   * has already crossed it.
   */
  min_face_margin_mm?: number | null;

  /**
   * Faces the crop cut through. Sometimes deliberate on a background figure, never
   * acceptable on a subject -- so it is recorded rather than merely prevented. Default:
   * [].
   */
  cropped_face_ids?: Blake3Hash[];
}

export type LayoutInfoSolver = "constraint_solver" | "template" | "manual";

export const LayoutInfoSolverValues = [
  "constraint_solver",
  "template",
  "manual",
] as const satisfies readonly LayoutInfoSolver[];

export interface LayoutInfoGrid {
  columns: number;

  rows: number;

  gutter_mm: number;
}

/**
 * How the page arrangement was arrived at. Layout is constraint solving, not template
 * filling (build plan 4.6), so the record is of a solver run rather than of a chosen
 * template id.
 */
export interface LayoutInfo {
  /** Default: "constraint_solver". */
  solver?: LayoutInfoSolver;

  template_id?: Slug | null;

  grid?: LayoutInfoGrid | null;

  /** Default: []. */
  constraints_satisfied?: string[];

  /**
   * Soft constraints the solver had to give up on, and which therefore deserve a human
   * glance. Recording them is the difference between a solver that reports its compromises
   * and one that hides them. Default: [].
   */
  constraints_relaxed?: string[];

  solver_cost?: number | null;
}

export type PageSide = "left" | "right" | "single" | "front_cover" | "back_cover" | "inside_flap";

export const PageSideValues = [
  "left",
  "right",
  "single",
  "front_cover",
  "back_cover",
  "inside_flap",
] as const satisfies readonly PageSide[];

export type PageBackgroundKind = "solid" | "none";

export const PageBackgroundKindValues = [
  "solid",
  "none",
] as const satisfies readonly PageBackgroundKind[];

export interface PageBackground {
  kind: PageBackgroundKind;

  /** Default: "#ffffff". */
  color_hex?: string;
}

export interface Page {
  page_index: number;

  /**
   * Pages sharing a spread_id are viewed together and are colour-harmonised together. Null
   * for a cover or a single page.
   */
  spread_id?: Slug | null;

  side: PageSide;

  /**
   * Narrative section this page belongs to, used by the diversity constraints
   * (people/scenery/detail balance per section).
   */
  section_id?: Slug | null;

  background?: PageBackground | null;

  /** Default: []. */
  placements: Placement[];

  /** Default: []. */
  text_blocks?: TextBlock[];

  layout?: LayoutInfo | null;
}

export type PlacementBleedsItem = "top" | "bottom" | "left" | "right";

export const PlacementBleedsItemValues = [
  "top",
  "bottom",
  "left",
  "right",
] as const satisfies readonly PlacementBleedsItem[];

export interface PlacementBorder {
  width_mm: number;

  color_hex: string;
}

/**
 * One photo on one page. Carries the mm frame it occupies, the normalised crop taken from
 * the source, and the effective DPI that results -- the three numbers the print validator
 * reasons about.
 */
export interface Placement {
  placement_id: Slug;

  media_id: Blake3Hash;

  /** Where it sits on the page, in mm from the bleed-box origin. */
  frame: RectMm;

  /**
   * The region of the SOURCE image used, in normalised oriented-image coordinates. Its
   * aspect ratio must match the frame's, or the renderer would have to decide how to
   * reconcile them -- and the renderer decides nothing.
   */
  crop: NormalizedBox;

  /**
   * Computed as (cropped source pixels along an edge) / (printed length of that edge in
   * inches). Compared against vendor_profile.dpi_floor by the hard validator. The whole
   * reason geometry is in mm.
   */
  effective_dpi: number;

  /** Default: 0. */
  z_index?: number;

  /**
   * Which page edges this placement runs off. A full-bleed photo must extend past the trim
   * by the profile's bleed_mm on every edge listed here. Default: [].
   */
  bleeds?: PlacementBleedsItem[];

  /**
   * The anchor image of its spread. Heroes get the DPI headroom and the composition
   * attention; supporting images fill around them. Default: false.
   */
  is_hero?: boolean;

  face_safety?: FaceSafety | null;

  /**
   * Ordered ops applied to this image before composition. Order matters: denoise before
   * upscale, upscale before face restore. Default: [].
   */
  enhancement_ops?: EnhancementOp[];

  caption?: string | null;

  border?: PlacementBorder | null;
}

export type PrintValidationReportStatus = "pass" | "fail" | "not_run";

export const PrintValidationReportStatusValues = [
  "pass",
  "fail",
  "not_run",
] as const satisfies readonly PrintValidationReportStatus[];

/**
 * THE HARD GATE. workers/render-print refuses to export a PDF unless status is 'pass'.
 * There is no override flag by design (AGENTS.md): a print defect cannot be patched after
 * the book is in the post, so the only safe place to fail is before the PDF exists.
 */
export interface PrintValidationReport {
  status: PrintValidationReportStatus;

  checks: ValidationCheck[];

  validated_at?: Timestamp | null;

  validator_version?: string | null;

  /** Default: 0. */
  error_count?: number;

  /** Default: 0. */
  warning_count?: number;
}

/**
 * A rectangle on the page in millimetres. Origin is the top-left of the BLEED box, not the
 * trim box, so a full-bleed placement has negative-free coordinates and the renderer never
 * has to guess which origin a number is relative to.
 */
export interface RectMm {
  x_mm: number;

  y_mm: number;

  width_mm: number;

  height_mm: number;

  /** Default: 0. */
  rotation_deg?: number;
}

export type SelectionReportDiversityConstraintsItemConstraint =
  | "no_near_duplicates_on_spread"
  | "people_scenery_detail_balance"
  | "max_per_person_per_section"
  | "chronological_within_section"
  | "no_consecutive_same_scene"
  | "min_hero_quality";

export const SelectionReportDiversityConstraintsItemConstraintValues = [
  "no_near_duplicates_on_spread",
  "people_scenery_detail_balance",
  "max_per_person_per_section",
  "chronological_within_section",
  "no_consecutive_same_scene",
  "min_hero_quality",
] as const satisfies readonly SelectionReportDiversityConstraintsItemConstraint[];

export interface SelectionReportDiversityConstraintsItem {
  constraint: SelectionReportDiversityConstraintsItemConstraint;

  satisfied: boolean;

  /** Default: "". */
  detail?: string;
}

export type SelectionReportRejectedItemReason =
  | "near_duplicate"
  | "below_quality_floor"
  | "eyes_closed"
  | "excluded_content"
  | "diversity_constraint"
  | "no_space"
  | "person_not_confirmed"
  | "dpi_too_low"
  | "user_hidden";

export const SelectionReportRejectedItemReasonValues = [
  "near_duplicate",
  "below_quality_floor",
  "eyes_closed",
  "excluded_content",
  "diversity_constraint",
  "no_space",
  "person_not_confirmed",
  "dpi_too_low",
  "user_hidden",
] as const satisfies readonly SelectionReportRejectedItemReason[];

export interface SelectionReportRejectedItem {
  media_id: Blake3Hash;

  reason: SelectionReportRejectedItemReason;
}

/**
 * Why these photos and not others. Kept with the spec because 'why is my best photo
 * missing' is the most common question a user will ever ask about an album.
 */
export interface SelectionReport {
  candidate_count?: number;

  selected_count?: number;

  /** Default: []. */
  diversity_constraints?: SelectionReportDiversityConstraintsItem[];

  /** Default: []. */
  rejected?: SelectionReportRejectedItem[];
}

export interface SizeMm {
  width_mm: number;

  height_mm: number;
}

export interface SpreadHarmonySpreadsItemAdjustmentsItem {
  placement_id: Slug;

  /** Default: 0. */
  exposure_ev_delta?: number;

  /** Default: 0. */
  temperature_k_delta?: number;

  /** Default: 0. */
  tint_delta?: number;

  /** Default: 0. */
  saturation_delta?: number;
}

export interface SpreadHarmonySpreadsItem {
  spread_id: Slug;

  target_temperature_k?: number | null;

  target_exposure_ev?: number | null;

  /**
   * Per-placement deltas the solver settled on. Deltas rather than absolutes so the
   * original image data stays the reference.
   */
  adjustments: SpreadHarmonySpreadsItemAdjustmentsItem[];
}

/**
 * Colour and exposure solved jointly across facing pages rather than per image. No
 * consumer tool does this, and the difference is instantly visible in print: two photos of
 * the same afternoon that disagree about white balance look like a mistake when they are
 * 30cm apart on the same sheet.
 */
export interface SpreadHarmony {
  enabled: boolean;

  /** Default: []. */
  spreads?: SpreadHarmonySpreadsItem[];
}

export type SpreadReviewStatus =
  | "not_run"
  | "passed"
  | "issues_found"
  | "issues_fixed"
  | "needs_human";

export const SpreadReviewStatusValues = [
  "not_run",
  "passed",
  "issues_found",
  "issues_fixed",
  "needs_human",
] as const satisfies readonly SpreadReviewStatus[];

export type SpreadReviewFindingsItemKind =
  | "crop_hits_face"
  | "near_identical_pair"
  | "color_clash"
  | "exposure_mismatch"
  | "weak_hero"
  | "cluttered_spread"
  | "awkward_crop"
  | "text_overlaps_subject"
  | "eyes_closed"
  | "orientation_mismatch";

export const SpreadReviewFindingsItemKindValues = [
  "crop_hits_face",
  "near_identical_pair",
  "color_clash",
  "exposure_mismatch",
  "weak_hero",
  "cluttered_spread",
  "awkward_crop",
  "text_overlaps_subject",
  "eyes_closed",
  "orientation_mismatch",
] as const satisfies readonly SpreadReviewFindingsItemKind[];

export type SpreadReviewFindingsItemSeverity = "error" | "warning" | "info";

export const SpreadReviewFindingsItemSeverityValues = [
  "error",
  "warning",
  "info",
] as const satisfies readonly SpreadReviewFindingsItemSeverity[];

export type SpreadReviewFindingsItemResolution =
  | "recropped"
  | "replaced"
  | "reordered"
  | "harmonized"
  | "removed"
  | "accepted_as_is"
  | "escalated_to_human";

export const SpreadReviewFindingsItemResolutionValues = [
  "recropped",
  "replaced",
  "reordered",
  "harmonized",
  "removed",
  "accepted_as_is",
  "escalated_to_human",
] as const satisfies readonly SpreadReviewFindingsItemResolution[];

export interface SpreadReviewFindingsItem {
  finding_id: Slug;

  kind: SpreadReviewFindingsItemKind;

  severity: SpreadReviewFindingsItemSeverity;

  spread_id?: Slug | null;

  /** Default: []. */
  placement_ids?: Slug[];

  /** Default: "". */
  comment?: string;

  resolved: boolean;

  resolution?: SpreadReviewFindingsItemResolution | null;
}

/**
 * The automated QA pass: render each spread at low resolution, ask a frontier model for a
 * structured critique, fix, re-check. This is what makes unattended output trustworthy,
 * and like every Tier 3 call it sees only low-res renders and returns only structured
 * decisions against ids.
 */
export interface SpreadReview {
  status: SpreadReviewStatus;

  model?: ModelRef | null;

  /**
   * Required whenever the review ran in the cloud, because low-res spread renders left the
   * device.
   */
  consent?: ConsentRef | null;

  prompt_id?: Slug | null;

  /** Default: 0. */
  iterations?: number;

  /** Default: []. */
  findings?: SpreadReviewFindingsItem[];
}

export type TextBlockRole = "title" | "subtitle" | "caption" | "date" | "page_number" | "quote";

export const TextBlockRoleValues = [
  "title",
  "subtitle",
  "caption",
  "date",
  "page_number",
  "quote",
] as const satisfies readonly TextBlockRole[];

export type TextBlockAlignment = "left" | "center" | "right" | "justify";

export const TextBlockAlignmentValues = [
  "left",
  "center",
  "right",
  "justify",
] as const satisfies readonly TextBlockAlignment[];

export interface TextBlock {
  block_id: Slug;

  text: string;

  frame: RectMm;

  /** Default: "caption". */
  role?: TextBlockRole;

  /** Default: "". */
  font_family?: string;

  font_size_pt?: number | null;

  /** Default: "#000000". */
  color_hex?: string;

  /** Default: "left". */
  alignment?: TextBlockAlignment;
}

export type ValidationCheckCheckId =
  | "dpi_floor"
  | "face_in_trim_zone"
  | "bleed_coverage"
  | "color_profile_match"
  | "face_in_gutter"
  | "page_count_valid"
  | "placement_within_page"
  | "crop_aspect_matches_frame"
  | "no_duplicate_on_spread"
  | "text_within_safe_margin"
  | "enhancement_license_cleared"
  | "source_media_available"
  | "pdf_standard_supported";

export const ValidationCheckCheckIdValues = [
  "dpi_floor",
  "face_in_trim_zone",
  "bleed_coverage",
  "color_profile_match",
  "face_in_gutter",
  "page_count_valid",
  "placement_within_page",
  "crop_aspect_matches_frame",
  "no_duplicate_on_spread",
  "text_within_safe_margin",
  "enhancement_license_cleared",
  "source_media_available",
  "pdf_standard_supported",
] as const satisfies readonly ValidationCheckCheckId[];

export type ValidationCheckSeverity = "error" | "warning" | "info";

export const ValidationCheckSeverityValues = [
  "error",
  "warning",
  "info",
] as const satisfies readonly ValidationCheckSeverity[];

export interface ValidationCheck {
  /**
   * The first four are the hard gates named in the build plan: DPI floor, face in trim
   * zone, bleed violation, mismatched colour profile. Any of them failing blocks export
   * outright.
   */
  check_id: ValidationCheckCheckId;

  severity: ValidationCheckSeverity;

  passed: boolean;

  page_index?: number | null;

  placement_id?: Slug | null;

  /** What was actually measured, e.g. 214.7 for a DPI check. */
  measured_value?: number | null;

  required_value?: number | null;

  /** Default: "". */
  detail?: string;

  /**
   * What would fix it: 'upscale source' or 'reduce frame to 180mm'. The review UI shows
   * this, and the album engine can often act on it automatically and re-validate.
   */
  remediation?: string | null;
}

export type VendorProfileColorProfileIntent =
  | "perceptual"
  | "relative_colorimetric"
  | "saturation"
  | "absolute_colorimetric";

export const VendorProfileColorProfileIntentValues = [
  "perceptual",
  "relative_colorimetric",
  "saturation",
  "absolute_colorimetric",
] as const satisfies readonly VendorProfileColorProfileIntent[];

export interface VendorProfileColorProfile {
  icc_name: string;

  icc_hash?: Blake3Hash | null;

  intent: VendorProfileColorProfileIntent;
}

export interface VendorProfilePageCount {
  minimum: number;

  maximum: number;

  /**
   * Pages are added in physical sheets, so a book is typically constrained to multiples of
   * 2 or 4. A spec with a page count off the increment is rejected by the printer, not by
   * us -- so we reject it first.
   */
  increment: number;
}

export type VendorProfileBinding =
  | "layflat"
  | "perfect_bound"
  | "saddle_stitch"
  | "spiral"
  | "hardcover_case";

export const VendorProfileBindingValues = [
  "layflat",
  "perfect_bound",
  "saddle_stitch",
  "spiral",
  "hardcover_case",
] as const satisfies readonly VendorProfileBinding[];

export type VendorProfilePdfStandard = "pdf_x_1a" | "pdf_x_3" | "pdf_x_4" | "pdf_1_6";

export const VendorProfilePdfStandardValues = [
  "pdf_x_1a",
  "pdf_x_3",
  "pdf_x_4",
  "pdf_1_6",
] as const satisfies readonly VendorProfilePdfStandard[];

/**
 * The printer's physical spec sheet, transcribed. Built to one real vendor first (build
 * plan 4.6) because a validator built against an imagined spec validates nothing.
 */
export interface VendorProfile {
  vendor_id: Slug;

  product_id: Slug;

  /**
   * Vendors change their spec sheets. A spec validated against v1 is not automatically
   * valid against v2, and the version pin is what makes that detectable.
   */
  profile_version: string;

  trim_size_mm: SizeMm;

  /**
   * How far artwork must extend beyond the trim line. Under-bleeding produces a white
   * sliver on the finished edge.
   */
  bleed_mm: number;

  /** Inset from trim within which nothing important may sit, because guillotines drift. */
  safe_margin_mm: number;

  /**
   * Dead zone at the spine. On a perfect-bound book this can swallow 10mm+ of a spread,
   * which is why a face landing in it is a hard failure rather than a warning. Default: 0.
   */
  gutter_mm?: number;

  spine_mm?: number | null;

  /**
   * Minimum effective resolution AT PRINTED SIZE. The whole point of the phrase: a 24MP
   * photo blown across a full spread can still fall below this.
   */
  dpi_floor: number;

  dpi_preferred?: number | null;

  color_profile: VendorProfileColorProfile;

  page_count?: VendorProfilePageCount;

  /** Default: "layflat". */
  binding?: VendorProfileBinding;

  paper_stock?: string | null;

  /** Default: "pdf_x_4". */
  pdf_standard?: VendorProfilePdfStandard;
}

/**
 * The deterministic plan for one printed album: which photos, on which pages, cropped how,
 * enhanced how, against which vendor's physical spec.
 */
export interface AlbumSpec {
  schema_version: SchemaVersion;

  /**
   * BLAKE3 over the canonical JSON of this spec with volatile fields removed. Two specs
   * with the same id produce the same PDF.
   */
  album_id: Blake3Hash;

  /** Default: "". */
  title?: string;

  subtitle?: string | null;

  event?: EventContext | null;

  vendor_profile: VendorProfile;

  /**
   * Ordered pages. Page 0 is the front cover when the vendor profile includes one. Spreads
   * are expressed by pairing pages via spread_id rather than by modelling a spread as a
   * single wide page, because the gutter falls between two physically separate sheets and
   * each has its own safe zone.
   */
  pages: Page[];

  selection?: SelectionReport | null;

  spread_harmony?: SpreadHarmony | null;

  determinism: Determinism;

  validation: PrintValidationReport;

  review?: SpreadReview | null;
}

/**
 * Exact aspect ratio as integers, e.g. 9:16 for a reel. Integers rather than a float so
 * 'is this 16:9' is an equality test, not an epsilon comparison.
 */
export interface AspectRatio {
  numerator: number;

  denominator: number;
}

/**
 * Lowercase hex BLAKE3-256 digest. The universal content address: same bytes anywhere in
 * the world produce the same id, which is what makes every job idempotent.
 */
export type Blake3Hash = string;

/**
 * Calibrated probability in [0,1]. Distinct from Unit by intent: a Confidence is expected
 * to be calibrated against a validation set and is therefore comparable to a threshold. A
 * Unit is merely ordered.
 */
export type Confidence = number;

export type ConsentRefScope =
  | "tier3_contact_sheet"
  | "cloud_render"
  | "cloud_backup"
  | "share_link"
  | "print_order"
  | "minor_face_labeling"
  | "anonymized_preference_training";

export const ConsentRefScopeValues = [
  "tier3_contact_sheet",
  "cloud_render",
  "cloud_backup",
  "share_link",
  "print_order",
  "minor_face_labeling",
  "anonymized_preference_training",
] as const satisfies readonly ConsentRefScope[];

/**
 * Pointer into the consent ledger owned by services/api. Required on anything that leaves
 * the device or touches a child's face. Hard rule: no network egress without a ledger
 * entry, verified by the CI egress test.
 */
export interface ConsentRef {
  ledger_entry_id: Uuid;

  scope: ConsentRefScope;

  granted_at: Timestamp;

  expires_at?: Timestamp | null;

  revoked_at?: Timestamp | null;
}

/**
 * Everything needed to reproduce a plan byte-for-byte. Present on every artifact a planner
 * emits (EDL, AlbumSpec). Hard rule 3: same plan + same sources = identical output, and
 * that is only auditable if the plan says what produced it.
 */
export interface Determinism {
  planner: Slug;

  planner_version: string;

  /**
   * Seed for every stochastic choice in planning (variant sampling, tie-breaking). Same
   * seed + same inputs must yield the same plan.
   */
  seed: number;

  /**
   * BLAKE3 over the canonical JSON of every input the planner read: candidate ids,
   * parameters, model refs. Two plans with the same digest and the same planner version
   * are guaranteed identical.
   */
  inputs_digest: Blake3Hash;

  generated_at?: Timestamp | null;
}

export type GeoPointSource =
  | "exif_gps"
  | "quicktime_location"
  | "xmp"
  | "sidecar_json"
  | "user_supplied"
  | "inferred";

export const GeoPointSourceValues = [
  "exif_gps",
  "quicktime_location",
  "xmp",
  "sidecar_json",
  "user_supplied",
  "inferred",
] as const satisfies readonly GeoPointSource[];

export interface GeoPoint {
  latitude: number;

  longitude: number;

  altitude_m?: number | null;

  horizontal_accuracy_m?: number | null;

  source: GeoPointSource;
}

/**
 * ISO 8601 date-time with NO offset, e.g. 2019-08-04T17:22:31. This is what a camera
 * actually writes into EXIF DateTimeOriginal: a wall-clock reading with no timezone.
 * Storing it as a naive local time and keeping the zone separate is the only lossless
 * representation.
 */
export type LocalDateTime = string;

export type ModelRefPrecision = "fp32" | "fp16" | "bf16" | "int8" | "int4";

export const ModelRefPrecisionValues = [
  "fp32",
  "fp16",
  "bf16",
  "int8",
  "int4",
] as const satisfies readonly ModelRefPrecision[];

/**
 * Pin to an exact model in the registry. Carries the weights hash, not just a version
 * string, because 'the same version' of a HuggingFace repo has changed weights under
 * people before. A record produced by an unpinned model is not reproducible, and
 * reproducibility is the product.
 */
export interface ModelRef {
  model_id: Slug;

  /**
   * The registry's version string for this model. Free-form apart from one exclusion: no
   * C0 control character and no DEL. FaceRecord.face_id joins model_id and version with
   * U+001F, and a version containing that separator would let two different (model,
   * version) pairs produce one identical byte string and therefore one identical face id.
   * Excluding the separator structurally is what lets that encoding skip length prefixes;
   * leaving it to convention is how the collision gets found in a family album instead of
   * here.
   */
  version: string;

  /**
   * BLAKE3 of the weights file, or null when the entry is unpinned. Null is permitted ONLY
   * because development mode permits loading unpinned weights; a null here is exactly what
   * makes a record non-reproducible, and release mode refuses to produce one.
   */
  weights_blake3: Blake3Hash | null;

  runtime?: RuntimeTarget | null;

  /**
   * Quantisation the weights were executed at. int8 and fp16 runs can differ from fp32 at
   * the third decimal, which is enough to flip a borderline face match, so it is part of
   * provenance.
   */
  precision?: ModelRefPrecision | null;

  /**
   * BLAKE3 of the model config file that governed this run. Weights alone do not pin
   * behaviour: input size, normalisation constants, score threshold, NMS IoU and the
   * alignment template all live in the config, and changing any of them changes every
   * downstream decision while the weights hash stays byte-identical. Null only for
   * classical measures with no model config.
   */
  config_blake3?: Blake3Hash | null;
}

/**
 * One execution of one model against one record. Every score in this contract points at a
 * run id, so 'why is this photo ranked 0.82' is always answerable and a model swap can be
 * evaluated by replaying only the affected runs.
 */
export interface ModelRun {
  run_id: Slug;

  model: ModelRef;

  ran_at: Timestamp;

  /**
   * Which proxy the model actually saw. Analysis never touches originals (AGENTS.md hard
   * rule 5), so this is normally set; null only for classical measures computed during
   * ingest.
   */
  input_proxy_id?: Blake3Hash | null;

  duration_ms?: number | null;

  job_id?: Blake3Hash | null;
}

/**
 * Axis-aligned rectangle in normalised image coordinates: origin top-left, x to the right,
 * y down, all values in [0,1] relative to the ORIENTED image (after EXIF rotation is
 * applied). Normalised so a box computed on a 512px thumbnail is valid against the 6000px
 * original without rescaling -- this is what lets analysis run on proxies and render run
 * on sources.
 */
export interface NormalizedBox {
  x: number;

  y: number;

  w: number;

  h: number;

  /**
   * Clockwise rotation of the box about its own centre. Present only for crops that
   * deliberately rotate, e.g. straightening a horizon in an album placement. Default: 0.
   */
  rotation_deg?: number;
}

export type PerceptualHashAlgorithm =
  | "phash-dct-64"
  | "phash-dct-256"
  | "dhash-64"
  | "ahash-64"
  | "wavelet-64";

export const PerceptualHashAlgorithmValues = [
  "phash-dct-64",
  "phash-dct-256",
  "dhash-64",
  "ahash-64",
  "wavelet-64",
] as const satisfies readonly PerceptualHashAlgorithm[];

export type PerceptualHashBits = 64 | 128 | 256;

export const PerceptualHashBitsValues = [
  64,
  128,
  256,
] as const satisfies readonly PerceptualHashBits[];

/**
 * Perceptual hash used for near-duplicate bucketing. Bucketing is by Hamming distance on
 * this hash; the bucket is then refined by embedding distance (build plan 4.2). Always
 * carries its algorithm so a future algorithm change cannot silently invalidate existing
 * buckets.
 */
export interface PerceptualHash {
  algorithm: PerceptualHashAlgorithm;

  /** Hash length in bits. Enforced to equal 4 * len(hex). */
  bits: PerceptualHashBits;

  /** Lowercase hex digest. Its length is pinned to `bits` by the constraints below. */
  hex: string;
}

export interface PixelSize {
  width: number;

  height: number;
}

/**
 * Point in the same normalised, orientation-applied coordinate space as NormalizedBox.
 * Landmarks may fall slightly outside [0,1] when a face is clipped by the frame edge, so
 * the bounds here are deliberately loose.
 */
export interface Point2D {
  x: number;

  y: number;
}

/**
 * A time expressed as an exact rational: value frames (or samples) at the given rate. Maps
 * 1:1 onto opentimelineio.opentime.RationalTime. Seconds = value / rate. Never store
 * seconds as a float in this contract.
 */
export interface RationalTime {
  /**
   * Position or count in units of 1/rate. May be fractional to survive sample-accurate
   * audio edits, but integral values are strongly preferred on video tracks.
   */
  value: number;

  /**
   * Units per second. Use the exact NTSC rationals where applicable: 24000/1001 =
   * 23.976023976023978, 30000/1001 = 29.97002997002997, 60000/1001 = 59.94005994005994.
   */
  rate: number;
}

export type RuntimeTarget =
  | "onnxruntime_cpu"
  | "onnxruntime_coreml"
  | "onnxruntime_directml"
  | "onnxruntime_cuda"
  | "ctranslate2"
  | "mlx"
  | "llama_cpp"
  | "opencv"
  | "librosa"
  | "native";

export const RuntimeTargetValues = [
  "onnxruntime_cpu",
  "onnxruntime_coreml",
  "onnxruntime_directml",
  "onnxruntime_cuda",
  "ctranslate2",
  "mlx",
  "llama_cpp",
  "opencv",
  "librosa",
  "native",
] as const satisfies readonly RuntimeTarget[];

/**
 * Contract version this record was written against. Frozen at 'v0' for the Phase 0
 * contract. A reader that does not recognise the value must refuse the record rather than
 * guess -- see hard rule 7, no silent anything.
 */
export type SchemaVersion = "v0";

/** A single scored value with a pointer back to the run that produced it. */
export interface Score {
  value: Unit;

  run_id?: Slug | null;

  /**
   * Model output before normalisation to [0,1], kept so a recalibration can be applied
   * without re-running the model.
   */
  raw_value?: number | null;
}

/**
 * Short stable machine identifier, lowercase alphanumeric with hyphens and underscores.
 * Used for ids that are authored by us rather than generated: track ids, act ids, check
 * ids.
 */
export type Slug = string;

export type TimeAssertionPrecision =
  | "second"
  | "minute"
  | "hour"
  | "day"
  | "month"
  | "year"
  | "unknown";

export const TimeAssertionPrecisionValues = [
  "second",
  "minute",
  "hour",
  "day",
  "month",
  "year",
  "unknown",
] as const satisfies readonly TimeAssertionPrecision[];

/**
 * A claim about when something was captured, together with how much we believe it.
 * Modelling capture time as an assertion rather than a bare timestamp is what lets the
 * system ingest a library where a third of the files have no EXIF date at all without
 * either dropping them or lying about their chronology.
 */
export interface TimeAssertion {
  /**
   * Wall-clock reading as recorded by the device, with no zone applied. Null when nothing
   * in the file or its neighbours implies a time.
   */
  local?: LocalDateTime | null;

  /**
   * Resolved instant, present only when the zone is actually known (explicit offset in
   * metadata, or GPS-derived zone). Never fabricate this by assuming the machine's local
   * zone -- that silently shifts an entire holiday by hours.
   */
  utc?: Timestamp | null;

  /**
   * IANA zone name, e.g. Asia/Kolkata, when it could be determined from metadata or GPS
   * coordinates.
   */
  timezone?: string | null;

  /**
   * Offset actually recorded in the file (EXIF OffsetTimeOriginal or QuickTime), when
   * present.
   */
  utc_offset_minutes?: number | null;

  source: TimeSource;

  /**
   * Granularity the assertion is actually good to. 'unknown' means we have no usable time
   * at all, and consumers must exclude the item from chronology-ordered output rather than
   * sorting it to the epoch.
   */
  precision: TimeAssertionPrecision;

  confidence: Confidence;

  /**
   * When precision came from neighbour_interpolation, the sibling records that bracketed
   * this one. Recorded so the inference is auditable and can be recomputed when a
   * neighbour's date is later corrected. Default: [].
   */
  inferred_from_media_ids?: Blake3Hash[];
}

/**
 * Half-open interval [start_time, start_time + duration). Maps 1:1 onto
 * opentimelineio.opentime.TimeRange. Half-open is deliberate and matches OTIO: it makes
 * adjacent clips tile a timeline with no off-by-one frame.
 */
export interface TimeRange {
  start_time: RationalTime;

  duration: RationalTime;
}

/**
 * Where a capture time came from, ordered from most to least trustworthy. The ranking is
 * load-bearing: event clustering weights a filename-derived date far less than an EXIF
 * original, and an inferred date not at all for chronology-critical decisions.
 */
export type TimeSource =
  | "exif_datetime_original"
  | "exif_datetime_digitized"
  | "quicktime_creation_date"
  | "xmp_create_date"
  | "gps_timestamp"
  | "sidecar_json"
  | "filename_pattern"
  | "filesystem_mtime"
  | "neighbour_interpolation"
  | "user_supplied"
  | "unknown";

export const TimeSourceValues = [
  "exif_datetime_original",
  "exif_datetime_digitized",
  "quicktime_creation_date",
  "xmp_create_date",
  "gps_timestamp",
  "sidecar_json",
  "filename_pattern",
  "filesystem_mtime",
  "neighbour_interpolation",
  "user_supplied",
  "unknown",
] as const satisfies readonly TimeSource[];

/**
 * RFC 3339 instant with an explicit offset. Every wall-clock moment the system itself
 * observes (ingest time, decision time, render time) is unambiguous by construction.
 */
export type Timestamp = string;

/**
 * A score normalised to [0,1]. Every model output in this contract is normalised before it
 * is written, so fusion never has to know a model's native range.
 */
export type Unit = number;

/**
 * RFC 4122 UUID, lowercase. Used only for entities whose identity is NOT determined by
 * content: person ids, cluster ids, user sessions, projects.
 */
export type Uuid = string;

export type VectorRefStorage = "index" | "inline";

export const VectorRefStorageValues = [
  "index",
  "inline",
] as const satisfies readonly VectorRefStorage[];

export type VectorRefQuantization = "float32" | "float16" | "int8" | "binary";

export const VectorRefQuantizationValues = [
  "float32",
  "float16",
  "int8",
  "binary",
] as const satisfies readonly VectorRefQuantization[];

/**
 * Reference to an embedding held in the vector index. Embeddings are referenced rather
 * than inlined so a MediaRecord stays small enough to page through 100k of them in a UI;
 * the index owns the floats. Inline values are permitted ONLY for fixtures and tests,
 * where self-containment matters more than size.
 */
export interface VectorRef {
  space: VectorSpace;

  dimensions: number;

  storage: VectorRefStorage;

  /** Row key in the sqlite-vec table. Required when storage is 'index'. */
  index_key?: string | null;

  /**
   * Raw float components. Permitted only when storage is 'inline'. Length must equal
   * `dimensions`.
   */
  values?: number[] | null;

  /** Default: "float32". */
  quantization?: VectorRefQuantization;

  /**
   * True when the vector is L2-normalised, which makes cosine distance a dot product.
   * Every space in this contract stores normalised vectors. Default: true.
   */
  normalized?: boolean;
}

/**
 * Named embedding space. Two vectors may only be compared when their space matches
 * exactly, including the model version that produced them -- a SigLIP 2 upgrade creates a
 * NEW space, it does not reinterpret the old one.
 */
export type VectorSpace =
  | "siglip2_base_768"
  | "siglip2_so400m_1152"
  | "arcface_buffalo_l_512"
  | "adaface_ir101_512"
  | "clap_audio_512"
  | "aesthetic_head_1";

export const VectorSpaceValues = [
  "siglip2_base_768",
  "siglip2_so400m_1152",
  "arcface_buffalo_l_512",
  "adaface_ir101_512",
  "clap_audio_512",
  "aesthetic_head_1",
] as const satisfies readonly VectorSpace[];

export interface Act {
  act_id: Slug;

  name: string;

  /** What this act is for, in plain language: 'arrival, establish where we are'. */
  intent?: string | null;

  timeline_range?: TimeRange | null;

  target_energy?: Unit | null;

  /** Default: []. */
  beats: StoryBeat[];
}

/**
 * Processing applied to the location sound as a whole. Keeping real ambient under music is
 * most of what separates a film that feels like a memory from a slideshow with a
 * soundtrack -- but the LEVEL of each clip's bed lives on that clip (ClipAudio.gain_db),
 * and this type carries only what is a property of the group rather than of one clip.
 */
export interface AmbientPlan {
  /**
   * Removes wind rumble, which otherwise dominates every outdoor action clip. Null means
   * no filter. Applied ONCE to the summed ambient group -- after each clip's gain, fades
   * and L-cut tail, before any DuckingRule -- so that the plan's order of operations is
   * stated rather than left to a mixer's internal graph.
   */
  high_pass?: HighPassFilter | null;
}

/**
 * The complete audio intention: what music plays, how much of the original scene survives
 * under it, and how the two are balanced against each other over time.
 */
export interface AudioPlan {
  /** Default: []. */
  music?: MusicCue[];

  ambient?: AmbientPlan;

  /**
   * Ordered ducking rules. Later rules win where they overlap, which keeps the resolution
   * deterministic instead of depending on the renderer's mixer implementation;
   * DuckingRule's $comment states exactly what 'overlap' means once the rules have attack
   * and release ramps. Default: [].
   */
  ducking?: DuckingRule[];

  mix: MixPlan;
}

export type BeatSection =
  | "intro"
  | "verse"
  | "pre_chorus"
  | "chorus"
  | "drop"
  | "bridge"
  | "breakdown"
  | "outro";

export const BeatSectionValues = [
  "intro",
  "verse",
  "pre_chorus",
  "chorus",
  "drop",
  "bridge",
  "breakdown",
  "outro",
] as const satisfies readonly BeatSection[];

export interface Beat {
  index: number;

  time: RationalTime;

  is_downbeat: boolean;

  bar?: number | null;

  beat_in_bar?: number | null;

  strength?: Unit | null;

  /**
   * Musical section this beat falls in. Lets the planner put the visual peak on the drop
   * rather than merely on a loud beat.
   */
  section?: BeatSection | null;
}

export type BeatGridTimeSignatureBeatUnit = 1 | 2 | 4 | 8 | 16;

export const BeatGridTimeSignatureBeatUnitValues = [
  1,
  2,
  4,
  8,
  16,
] as const satisfies readonly BeatGridTimeSignatureBeatUnit[];

export interface BeatGridTimeSignature {
  beats_per_bar: number;

  beat_unit: BeatGridTimeSignatureBeatUnit;
}

/**
 * The musical skeleton the cut is hung on. Stored as explicit per-beat times rather than
 * as a BPM to be extrapolated, because real tracks drift and an extrapolated grid is 200ms
 * out by the end of a 30-second reel.
 */
export interface BeatGrid {
  /** Which MusicCue this grid describes. */
  source_cue_id: Slug;

  bpm: number;

  bpm_confidence?: Confidence | null;

  time_signature?: BeatGridTimeSignature;

  /**
   * Every beat, in TIMELINE time, ordered. Downbeats are flagged rather than stored
   * separately so a cut can reference one index regardless of which it turned out to be.
   */
  beats: Beat[];

  /**
   * Which beat tracker produced this. Recorded partly for reproducibility and partly
   * because the licence-safe analyser (librosa, ISC) and the more accurate but non-
   * commercial ones (madmom) must never be confused.
   */
  analyzer?: ModelRef | null;

  /**
   * Maximum acceptable beat-alignment error for a cut claiming to be beat-locked. The
   * quality gate is 50ms on downbeats. Default: 50.
   */
  tolerance_ms?: number;
}

/**
 * Records that a cut was placed against the music rather than merely near it.
 * `alignment_error_ms` is the audit trail for the <50ms downbeat quality gate.
 */
export interface BeatLock {
  /** Index into BeatGrid.beats. */
  beat_index: number;

  /** Default: false. */
  is_downbeat?: boolean;

  /**
   * Signed distance from the clip's timeline in-point to the beat. Negative is early. Non-
   * zero because a cut must also land on a certified snap point, and the nearest snap
   * point is rarely exactly on the beat -- the planner trades a few milliseconds of beat
   * error for a cut that lands on a real motion onset.
   */
  alignment_error_ms: number;

  /** Which MomentRecord snap point the cut actually landed on. */
  snap_point_kind?: string | null;
}

export interface Clip {
  item_type: "clip";

  clip_id: Slug;

  /** Default: "". */
  name?: string;

  media_ref_id: Slug;

  /**
   * In and out in SOURCE time, half-open. The single most important field in the contract:
   * it is what the renderer seeks to, and it must round-trip through OTIO unchanged.
   */
  source_range: TimeRange;

  /**
   * Derived position on the timeline. Carried for validation only; excluded from the
   * determinism digest and not exported to OTIO, which recomputes it. Its DURATION is
   * derived from source_range and any time_effect by the rule in TimeEffect's $comment --
   * equal to source_range.duration when there is no effect -- and its START is the running
   * sum of the extents before it. A timeline_range that disagrees with that arithmetic is
   * a validation failure, never a correction.
   */
  timeline_range?: TimeRange | null;

  /**
   * The MomentRecord this clip realises. The provenance link that lets 'more of her' re-
   * plan against the same candidate pool instead of starting over.
   */
  moment_id?: Blake3Hash | null;

  /** Default: true. */
  enabled?: boolean;

  time_effect?: TimeEffect | null;

  /**
   * Reframe track driving this clip's crop. Null means full frame, letterboxed or
   * pillarboxed per the target.
   */
  reframe_track_id?: Slug | null;

  /** Default: []. */
  color_ops?: ColorOp[];

  audio?: ClipAudio | null;

  /**
   * Present when this clip's in-point was snapped to the beat grid. Carries the alignment
   * error so the <50ms quality gate is measurable from the plan alone, without rendering
   * anything.
   */
  beat_lock?: BeatLock | null;

  /**
   * Which story-arc beat this clip satisfies. Null on clips that are connective tissue
   * rather than a required beat.
   */
  story_beat_id?: Slug | null;

  /** Default: []. */
  markers?: Marker[];
}

/** This clip's own sound, and the ONLY place its level is stated (contracts#53). */
export interface ClipAudio {
  /**
   * Level of this clip's own audio, in dB relative to the source. Composes with nothing
   * else in the plan except MixPlan.master_gain_db and any DuckingRule whose target role
   * covers this clip's track.
   */
  gain_db: number;

  /** Default: false. */
  muted?: boolean;

  /**
   * Fade length at the clip's in-point. The curve is a LINEAR RAMP IN AMPLITUDE from 0 to
   * 1 over the declared frames -- not equal-power, not linear in dB (contracts#60). Equal-
   * power is the usual choice for a music crossfade and would be audibly different on a
   * long fade, so it is named here rather than left to the mixer; a planner that wants a
   * different shape emits a Transition.
   */
  fade_in?: RationalTime | null;

  /**
   * Fade length ending on the clip's last frame, a linear ramp in amplitude from 1 to 0.
   * Same convention as fade_in.
   */
  fade_out?: RationalTime | null;

  /**
   * L-cut: hold this clip's audio past its visual out-point, so a laugh finishes over the
   * next shot. Realises MomentRecord.safe_trim.preserve_audio_tail.
   */
  audio_extends_past_out?: RationalTime | null;
}

/**
 * What a set of code values MEANS: primaries, transfer function and matrix coefficients,
 * as one token. The spelling is closed -- see this def's $comment for the table each token
 * expands to.
 */
export type ColorEncoding =
  | "srgb"
  | "bt709"
  | "display_p3"
  | "bt2020_sdr"
  | "bt2100_pq"
  | "bt2100_hlg";

export const ColorEncodingValues = [
  "srgb",
  "bt709",
  "display_p3",
  "bt2020_sdr",
  "bt2100_pq",
  "bt2100_hlg",
] as const satisfies readonly ColorEncoding[];

export type ColorOpOp = "exposure" | "saturation";

export const ColorOpOpValues = [
  "exposure",
  "saturation",
] as const satisfies readonly ColorOpOp[];

/**
 * A per-clip colour adjustment, planned by the intelligence layer and merely applied by
 * the renderer. `amount` is normalised to [-1,1]; what that means in light is stated in
 * this def's $comment, per op, as a formula (contracts#49).
 */
export interface ColorOp {
  /**
   * Which adjustment. The enum is short on purpose -- see the $comment for what was
   * removed and why, and for the issue that carries each removed capability.
   */
  op: ColorOpOp;

  /**
   * Signed, normalised to [-1,1] where 0 is no change. Exposure: a * 2 stops. Saturation:
   * a chroma scale of 1 + a. A single normalised scale keeps ops composable and makes 'is
   * this grade aggressive' a question with an answer.
   */
  amount: number;

  /**
   * PROVENANCE ONLY -- a renderer never reads this field. The clip whose look this
   * adjustment was derived from, recorded so a shot-matching decision stays auditable in
   * the plan after the planner has resolved it into primitive ops. It resolves nothing at
   * render time: the ops beside it are the whole instruction.
   */
  reference_clip_id?: Slug | null;
}

export type ColorPipelineWorkingSpace = "linear_bt709" | "linear_bt2020";

export const ColorPipelineWorkingSpaceValues = [
  "linear_bt709",
  "linear_bt2020",
] as const satisfies readonly ColorPipelineWorkingSpace[];

export type ColorPipelineOutputEncoding = "srgb" | "bt709" | "display_p3" | "bt2020_sdr";

export const ColorPipelineOutputEncodingValues = [
  "srgb",
  "bt709",
  "display_p3",
  "bt2020_sdr",
] as const satisfies readonly ColorPipelineOutputEncoding[];

/**
 * The colour path from every source to the delivered file. Every field is required and
 * none has a default: a colour decision a renderer supplies is invisible until print.
 */
export interface ColorPipeline {
  /**
   * Where colour ops and tone mapping compose. LINEAR-LIGHT RGB only, scaled so 1.0 is
   * reference white -- the earlier enum mixed transfer names ('rec709') with 'linear' and
   * with a log space, so it could not say what an op meant. `aces_cct` went with it: no
   * worker here implements it, and its log curve has constants nothing in this repo
   * states.
   */
  working_space: ColorPipelineWorkingSpace;

  /**
   * What the delivered file's code values mean, and what its container-level colour tags
   * must say. SDR only at v0 -- see the ColorEncoding $comment.
   */
  output_encoding: ColorPipelineOutputEncoding;

  /**
   * Required exactly when at least one source's `color_encoding` is an HDR member
   * (bt2100_pq, bt2100_hlg); must be null otherwise. Checked as `color_pipeline_resolves`.
   */
  tone_map: ToneMap | null;
}

export type DuckingRuleTarget = "music" | "ambient" | "sfx";

export const DuckingRuleTargetValues = [
  "music",
  "ambient",
  "sfx",
] as const satisfies readonly DuckingRuleTarget[];

/**
 * One duck: turn `target` down by `reduction_db` over these timeline ranges, on the
 * envelope this def's $comment states exactly.
 */
export interface DuckingRule {
  rule_id: Slug;

  /**
   * Which Track.role gets turned down. A rule whose target matches no track in the plan
   * states an intent about audio that does not exist, and is a validation failure.
   */
  target: DuckingRuleTarget;

  /**
   * Positive number of dB to reduce by, reached at the range start and held to the range
   * end.
   */
  reduction_db: number;

  /** Length of the ramp DOWN, ending at the range start. 0 is a step. Default: 0. */
  attack_ms?: number;

  /** Length of the ramp back UP, beginning at the range end. 0 is a step. Default: 0. */
  release_ms?: number;

  /**
   * Timeline ranges held at the full reduction. Non-empty, and each must lie within the
   * timeline.
   */
  ranges: TimeRange[];
}

export type EdlValidationStatus = "pass" | "warn" | "fail" | "not_run";

export const EdlValidationStatusValues = [
  "pass",
  "warn",
  "fail",
  "not_run",
] as const satisfies readonly EdlValidationStatus[];

export type EdlValidationChecksItemCheckId =
  | "source_range_within_available"
  | "media_refs_resolvable"
  | "timeline_contiguous"
  | "time_effect_extent_derived"
  | "music_cues_placed_once"
  | "span_continuity_verified"
  | "color_pipeline_resolves"
  | "transition_handles_available"
  | "beat_alignment_within_tolerance"
  | "no_mid_word_cut"
  | "reframe_aspect_matches_target"
  | "reframe_keyframes_ordered"
  | "duration_within_max"
  | "music_license_covers_destination"
  | "required_story_beats_satisfied"
  | "audio_loudness_target_set"
  | "determinism_digest_present";

export const EdlValidationChecksItemCheckIdValues = [
  "source_range_within_available",
  "media_refs_resolvable",
  "timeline_contiguous",
  "time_effect_extent_derived",
  "music_cues_placed_once",
  "span_continuity_verified",
  "color_pipeline_resolves",
  "transition_handles_available",
  "beat_alignment_within_tolerance",
  "no_mid_word_cut",
  "reframe_aspect_matches_target",
  "reframe_keyframes_ordered",
  "duration_within_max",
  "music_license_covers_destination",
  "required_story_beats_satisfied",
  "audio_loudness_target_set",
  "determinism_digest_present",
] as const satisfies readonly EdlValidationChecksItemCheckId[];

export type EdlValidationChecksItemSeverity = "error" | "warning" | "info";

export const EdlValidationChecksItemSeverityValues = [
  "error",
  "warning",
  "info",
] as const satisfies readonly EdlValidationChecksItemSeverity[];

export interface EdlValidationChecksItem {
  check_id: EdlValidationChecksItemCheckId;

  passed: boolean;

  severity: EdlValidationChecksItemSeverity;

  /** Default: "". */
  detail?: string;

  clip_id?: Slug | null;
}

/**
 * Result of the pre-render checks. The renderer refuses an EDL that has not passed, which
 * keeps 'the renderer is dumb' from meaning 'the renderer is trusting'.
 */
export interface EdlValidation {
  status: EdlValidationStatus;

  checks: EdlValidationChecksItem[];

  validated_at?: Timestamp | null;

  validator_version?: string | null;
}

export type EncodeAudioCodec = "aac" | "opus" | "flac" | "pcm_s16le" | "pcm_s24le";

export const EncodeAudioCodecValues = [
  "aac",
  "opus",
  "flac",
  "pcm_s16le",
  "pcm_s24le",
] as const satisfies readonly EncodeAudioCodec[];

export type EncodeAudioEncoder = "aac" | "aac_at" | "libopus" | "flac" | "pcm_s16le" | "pcm_s24le";

export const EncodeAudioEncoderValues = [
  "aac",
  "aac_at",
  "libopus",
  "flac",
  "pcm_s16le",
  "pcm_s24le",
] as const satisfies readonly EncodeAudioEncoder[];

export type EncodeAudioSampleFormat = "fltp" | "s16" | "s16p" | "s32" | "s32p";

export const EncodeAudioSampleFormatValues = [
  "fltp",
  "s16",
  "s16p",
  "s32",
  "s32p",
] as const satisfies readonly EncodeAudioSampleFormat[];

export interface EncodeAudio {
  codec: EncodeAudioCodec;

  encoder: EncodeAudioEncoder;

  sample_format: EncodeAudioSampleFormat;

  /** Null for the lossless codecs, which have no bit rate to set. */
  bit_rate_kbps?: number | null;
}

export type EncodeProfileContainer = "mp4" | "mov" | "mkv" | "webm";

export const EncodeProfileContainerValues = [
  "mp4",
  "mov",
  "mkv",
  "webm",
] as const satisfies readonly EncodeProfileContainer[];

export type EncodeProfileScaler = "neighbor" | "bilinear" | "bicubic" | "lanczos" | "spline";

export const EncodeProfileScalerValues = [
  "neighbor",
  "bilinear",
  "bicubic",
  "lanczos",
  "spline",
] as const satisfies readonly EncodeProfileScaler[];

/**
 * The delivery encode, stated in the plan rather than chosen by the renderer
 * (contracts#56). Every field is mandatory somewhere in this object: there is no default
 * profile, and no destination-to-codec table anywhere in a worker. Two plans that differ
 * only in their encode are two different files, and `determinism.inputs_digest` covers
 * this block for exactly that reason.
 */
export interface EncodeProfile {
  /**
   * Names this exact combination of settings, so a delivery preset is versioned contract
   * data that review can see rather than a table inside a worker. Two profiles that differ
   * in any field must not share an id.
   */
  profile_id: Slug;

  /**
   * The muxer. `mp4` and `mov` are delivery; `mkv` is what a lossless master goes in,
   * because MP4 cannot carry FFV1.
   */
  container: EncodeProfileContainer;

  /**
   * Resampling kernel used to fit a crop to `resolution`. Pinned because the scaler
   * touches every pixel of every frame, and two kernels are visibly different on a 480p
   * proxy blown up to 1080p.
   */
  scaler: EncodeProfileScaler;

  /**
   * Encoder thread count. A determinism input, not a performance setting -- see this def's
   * $comment.
   */
  encoder_threads: number;

  video: EncodeVideo;

  /**
   * Null only when the plan carries no audio at all. A program with audio and a null audio
   * profile is a validation failure, not a silent mute.
   */
  audio?: EncodeAudio | null;
}

export type EncodeVideoCodec = "h264" | "hevc" | "av1" | "vp9" | "ffv1" | "prores";

export const EncodeVideoCodecValues = [
  "h264",
  "hevc",
  "av1",
  "vp9",
  "ffv1",
  "prores",
] as const satisfies readonly EncodeVideoCodec[];

export type EncodeVideoEncoder =
  | "libx264"
  | "libx265"
  | "libsvtav1"
  | "libvpx-vp9"
  | "ffv1"
  | "prores_ks"
  | "h264_videotoolbox"
  | "hevc_videotoolbox";

export const EncodeVideoEncoderValues = [
  "libx264",
  "libx265",
  "libsvtav1",
  "libvpx-vp9",
  "ffv1",
  "prores_ks",
  "h264_videotoolbox",
  "hevc_videotoolbox",
] as const satisfies readonly EncodeVideoEncoder[];

export type EncodeVideoPixelFormat =
  | "yuv420p"
  | "yuv422p"
  | "yuv444p"
  | "yuv420p10le"
  | "yuv422p10le"
  | "yuv444p10le";

export const EncodeVideoPixelFormatValues = [
  "yuv420p",
  "yuv422p",
  "yuv444p",
  "yuv420p10le",
  "yuv422p10le",
  "yuv444p10le",
] as const satisfies readonly EncodeVideoPixelFormat[];

export interface EncodeVideo {
  /** What a player must decode. */
  codec: EncodeVideoCodec;

  /**
   * Which implementation writes the bytes. Must produce `codec`; a renderer refuses the
   * pair if it does not.
   */
  encoder: EncodeVideoEncoder;

  /**
   * Chroma subsampling and bit depth in one value, which is how every encoder actually
   * takes it.
   */
  pixel_format: EncodeVideoPixelFormat;

  rate_control: RateControl;

  /**
   * Encoder speed/efficiency preset, e.g. x264's `medium`. Null means the encoder's own
   * default, which is only acceptable for encoders that have no preset axis (ffv1,
   * prores_ks).
   */
  preset?: string | null;

  /**
   * Codec profile, e.g. `high` for H.264 or `main10` for HEVC. Null means the encoder
   * derives it from pixel_format.
   */
  profile?: string | null;

  /** Codec level, e.g. `4.0`. Null means the encoder derives it from resolution and rate. */
  level?: string | null;

  /**
   * Maximum GOP length in frames. Stated because it is a delivery decision with
   * consequences a viewer feels -- a platform re-encoding a 10-second GOP seeks worse than
   * one re-encoding a 2-second GOP -- and because leaving it to the encoder's default
   * makes the same plan produce different files on different builds.
   */
  keyframe_interval_frames: number;
}

export type GapFill = "black" | "white" | "transparent" | "silence";

export const GapFillValues = [
  "black",
  "white",
  "transparent",
  "silence",
] as const satisfies readonly GapFill[];

/**
 * Explicit silence or black. Modelled explicitly, exactly as OTIO does, so a hold at the
 * end of a film is a stated intention rather than an accident of arithmetic.
 */
export interface Gap {
  item_type: "gap";

  gap_id?: Slug | null;

  duration: RationalTime;

  /** Default: "black". */
  fill?: GapFill;
}

export type HighPassFilterOrder = 2 | 4;

export const HighPassFilterOrderValues = [
  2,
  4,
] as const satisfies readonly HighPassFilterOrder[];

export interface HighPassFilter {
  /** -3 dB corner of the cascade. */
  corner_hz: number;

  /**
   * Pole count. See AmbientPlan.high_pass's $comment for the Q values each order expands
   * to.
   */
  order: HighPassFilterOrder;
}

export type MarkerColor =
  | "RED"
  | "GREEN"
  | "BLUE"
  | "CYAN"
  | "MAGENTA"
  | "YELLOW"
  | "ORANGE"
  | "PURPLE"
  | "WHITE"
  | "BLACK"
  | "PINK"
  | "MINT";

export const MarkerColorValues = [
  "RED",
  "GREEN",
  "BLUE",
  "CYAN",
  "MAGENTA",
  "YELLOW",
  "ORANGE",
  "PURPLE",
  "WHITE",
  "BLACK",
  "PINK",
  "MINT",
] as const satisfies readonly MarkerColor[];

export type MarkerKind =
  | "beat"
  | "downbeat"
  | "story_beat"
  | "emotional_peak"
  | "speech"
  | "warning"
  | "note";

export const MarkerKindValues = [
  "beat",
  "downbeat",
  "story_beat",
  "emotional_peak",
  "speech",
  "warning",
  "note",
] as const satisfies readonly MarkerKind[];

/**
 * Maps to otio.schema.Marker. Markers are how our reasoning becomes visible to a human
 * editor who opens the timeline in Resolve.
 */
export interface Marker {
  name: string;

  marked_range: TimeRange;

  /** Default: "GREEN". */
  color?: MarkerColor;

  /** Default: "note". */
  kind?: MarkerKind;

  /** Default: "". */
  comment?: string;
}

export type MediaRefMediaKind = "video" | "image" | "audio" | "music" | "generated";

export const MediaRefMediaKindValues = [
  "video",
  "image",
  "audio",
  "music",
  "generated",
] as const satisfies readonly MediaRefMediaKind[];

export type MediaRefContinuity =
  | "verified_gapless"
  | "verified_gap"
  | "unverified"
  | "incomplete_set";

export const MediaRefContinuityValues = [
  "verified_gapless",
  "verified_gap",
  "unverified",
  "incomplete_set",
] as const satisfies readonly MediaRefContinuity[];

/**
 * One source, addressed by content hash rather than by path. This is what makes an EDL
 * portable: the same plan renders on any machine that has the same footage, wherever it
 * happens to live.
 */
export interface MediaRef {
  /** Local alias used by clips within this EDL. */
  media_ref_id: Slug;

  /**
   * BLAKE3 of the source file, i.e. a MediaRecord primary key. For chaptered footage this
   * is the span ASSEMBLY id; the renderer expands it to the ordered member files.
   */
  media_id: Blake3Hash;

  /** Default: "video". */
  media_kind?: MediaRefMediaKind;

  /**
   * The full extent of the source, in source time. Exported as
   * ExternalReference.available_range. A clip whose source_range escapes this is invalid
   * and must fail validation before render, not during it.
   */
  available_range: TimeRange;

  /**
   * True when media_id names a virtual assembly of chaptered files rather than a single
   * file on disk. Default: false.
   */
  is_span_assembly?: boolean;

  /**
   * The assembly's members, in INDEX order -- the order they concatenate into one
   * recording. Required when is_span_assembly is true and forbidden otherwise. This is the
   * field that makes an assembly expandable from the EDL alone (contracts#55): the
   * renderer never sees a MediaRecord, so before this existed the member order arrived out
   * of band in the render job and the plan could not state what it had planned against.
   * Default: [].
   */
  member_media_ids?: Blake3Hash[];

  /**
   * Whether the chapters were verified gapless, copied from MediaRecord.Span.continuity.
   * Required when is_span_assembly is true and forbidden otherwise.
   */
  continuity?: MediaRefContinuity | null;

  /**
   * What this source's code values mean. REQUIRED for a video or image source and null for
   * an audio or music one. Stated by the planner from the MediaRecord ingest already
   * probed -- never inferred at render time, which is what `ColorPipeline.input_transform:
   * "auto"` used to ask for (contracts#58).
   */
  color_encoding?: ColorEncoding | null;

  /**
   * Peak luminance this source is graded to, in cd/m^2. REQUIRED when `color_encoding` is
   * an HDR member and null otherwise. For `bt2100_hlg` it MUST be 1000, because that is
   * the nominal display the HLG decode is defined against here -- any other value would
   * describe a decode that did not happen. It sits on the source rather than on ToneMap
   * because a cut can hold a 1000-nit phone clip and a 4000-nit graded one.
   */
  source_peak_nits?: number | null;

  expected_frame_rate?: number | null;

  /** Human-readable name for the NLE bin. Cosmetic only -- never used for resolution. */
  label?: string | null;
}

export type MixPlanChannels = "mono" | "stereo";

export const MixPlanChannelsValues = [
  "mono",
  "stereo",
] as const satisfies readonly MixPlanChannels[];

export type MixPlanSampleRate = 44100 | 48000;

export const MixPlanSampleRateValues = [
  44100,
  48000,
] as const satisfies readonly MixPlanSampleRate[];

export interface MixPlan {
  /** Default: 0. */
  master_gain_db?: number;

  loudness_target_lufs: number;

  /** Default: -1. */
  true_peak_ceiling_db?: number;

  /** Default: true. */
  limiter?: boolean;

  /** Default: "stereo". */
  channels?: MixPlanChannels;

  /** Default: 48000. */
  sample_rate?: MixPlanSampleRate;
}

/**
 * Licence and provenance for one piece of music, attached to the clips that place it. A
 * cue is NOT a placement: the bed lives on an audio track like every other sound, and the
 * cue says what it is and what may legally be done with it.
 */
export interface MusicCue {
  cue_id: Slug;

  /** The source this cue licenses. Must equal the media_ref_id of every clip in clip_ids. */
  media_ref_id: Slug;

  /**
   * The audio-track clips that place this cue, in timeline order. One entry for a bed that
   * plays once, one per pass for a bed that repeats. Every clip on a track whose role is
   * `music` must be claimed by exactly one cue -- that is how an unlicensed bed becomes
   * impossible rather than merely unlikely.
   */
  clip_ids: Slug[];

  /**
   * Required, not optional. Music licensing is a Phase 0 decision precisely because an
   * unlicensed track in a shared reel is a legal problem, and the plan is the place it
   * becomes checkable.
   */
  license: MusicLicense;
}

export type MusicLicenseProvider =
  | "catalog_partner"
  | "creative_commons"
  | "public_domain"
  | "generated_score"
  | "user_supplied"
  | "platform_library";

export const MusicLicenseProviderValues = [
  "catalog_partner",
  "creative_commons",
  "public_domain",
  "generated_score",
  "user_supplied",
  "platform_library",
] as const satisfies readonly MusicLicenseProvider[];

export type MusicLicenseLicenseType =
  | "royalty_free"
  | "cc_by"
  | "cc_by_sa"
  | "cc0"
  | "rights_managed"
  | "personal_use_only"
  | "unknown";

export const MusicLicenseLicenseTypeValues = [
  "royalty_free",
  "cc_by",
  "cc_by_sa",
  "cc0",
  "rights_managed",
  "personal_use_only",
  "unknown",
] as const satisfies readonly MusicLicenseLicenseType[];

export type MusicLicenseClearedForItem =
  | "private_playback"
  | "social_share"
  | "commercial_use"
  | "broadcast";

export const MusicLicenseClearedForItemValues = [
  "private_playback",
  "social_share",
  "commercial_use",
  "broadcast",
] as const satisfies readonly MusicLicenseClearedForItem[];

export interface MusicLicense {
  provider: MusicLicenseProvider;

  license_id?: string | null;

  track_title?: string | null;

  /** Default: false. */
  attribution_required?: boolean;

  attribution_text?: string | null;

  license_type: MusicLicenseLicenseType;

  /**
   * Where this cut may legally be published. `user_supplied` music is typically
   * personal_use only, and a share flow must be able to refuse rather than discover the
   * problem after upload.
   */
  cleared_for: MusicLicenseClearedForItem[];
}

/**
 * Bookkeeping for the OTIO round trip. `unmapped_fields` must be empty for an export to be
 * called lossless; if it is not, the exporter is telling us exactly which part of the
 * contract has outgrown the mapping.
 */
export interface OtioExportInfo {
  /** Default: "OTIO_SCHEMA:Timeline.1". */
  otio_schema_version?: string;

  /** Default: "memory_engine". */
  metadata_namespace?: "memory_engine";

  /** Default: []. */
  unmapped_fields?: string[];

  /**
   * Set by the exporter after re-importing its own output and comparing to the source EDL.
   * The claim of losslessness is tested per export, not assumed. Default: false.
   */
  round_trip_verified?: boolean;
}

export type RateControlMode = "crf" | "cqp" | "abr" | "cbr" | "lossless";

export const RateControlModeValues = [
  "crf",
  "cqp",
  "abr",
  "cbr",
  "lossless",
] as const satisfies readonly RateControlMode[];

export interface RateControl {
  /**
   * `crf` and `cqp` take a quality value; `abr` and `cbr` take a bit rate; `lossless`
   * takes neither.
   */
  mode: RateControlMode;

  /** CRF or QP value. Required for crf and cqp, null otherwise. */
  quality?: number | null;

  /** Target bit rate. Required for abr and cbr, null otherwise. */
  bit_rate_kbps?: number | null;
}

export type ReframeKeyframeInterpolation = "linear" | "smooth" | "bezier" | "hold";

export const ReframeKeyframeInterpolationValues = [
  "linear",
  "smooth",
  "bezier",
  "hold",
] as const satisfies readonly ReframeKeyframeInterpolation[];

export interface ReframeKeyframe {
  /**
   * SOURCE time of the keyframe, so the track stays valid if the clip is later trimmed or
   * retimed.
   */
  time: RationalTime;

  /**
   * Crop window in normalised source coordinates. Its aspect must match the track's
   * target_aspect_ratio; a mismatch is a validation failure because the renderer must not
   * be the one deciding how to reconcile it.
   */
  crop: NormalizedBox;

  /**
   * How to reach the NEXT keyframe. `hold` produces a snap, which is occasionally what a
   * hard beat wants. Every mode is a stated formula, not a name -- see the $comment.
   * Default: "smooth".
   */
  interpolation?: ReframeKeyframeInterpolation;

  /**
   * Control points for bezier interpolation, as (x,y) in normalised keyframe-interval
   * space.
   */
  bezier_control?: Point2D[] | null;

  /**
   * Tracker confidence at this keyframe. Low-confidence stretches are where the fallback
   * earns its keep.
   */
  confidence?: Confidence | null;
}

export type ReframeSmoothingMethod =
  | "none"
  | "moving_average"
  | "savitzky_golay"
  | "kalman"
  | "spring_damper";

export const ReframeSmoothingMethodValues = [
  "none",
  "moving_average",
  "savitzky_golay",
  "kalman",
  "spring_damper",
] as const satisfies readonly ReframeSmoothingMethod[];

/**
 * Constraints that stop a reframe from looking like a nervous camera operator. Raw per-
 * frame subject centroids are far too jittery to use directly.
 */
export interface ReframeSmoothing {
  /** Default: "savitzky_golay". */
  method?: ReframeSmoothingMethod;

  window_frames?: number | null;

  /**
   * Cap on crop travel, in normalised units per second. The difference between a
   * considered pan and a whip.
   */
  max_velocity_per_second?: number | null;

  /**
   * Subject movement smaller than this does not move the crop at all, which is what keeps
   * a mostly-still subject from causing constant micro-drift.
   */
  deadzone?: Unit | null;
}

export type ReframeTrackFallback =
  | "center_crop"
  | "saliency_crop"
  | "letterbox"
  | "hold_last_keyframe";

export const ReframeTrackFallbackValues = [
  "center_crop",
  "saliency_crop",
  "letterbox",
  "hold_last_keyframe",
] as const satisfies readonly ReframeTrackFallback[];

/**
 * A crop keyframe track that turns landscape footage into a vertical cut with the subject
 * held in frame. This is core IP and has no OTIO equivalent, so it round-trips through
 * metadata.
 */
export interface ReframeTrack {
  reframe_track_id: Slug;

  target_aspect_ratio: AspectRatio;

  /**
   * Ordered by time, at least one. A single keyframe is a static crop; the interesting
   * case is a moving crop that tracks a subject across the frame.
   */
  keyframes: ReframeKeyframe[];

  subject_lock?: SubjectLock | null;

  smoothing?: ReframeSmoothing | null;

  /**
   * What to do if subject tracking fails at render time. Never leave this implicit: a
   * reframe that silently falls back to a centre crop can decapitate the subject of the
   * shot. Default: "hold_last_keyframe".
   */
  fallback?: ReframeTrackFallback;
}

export type RenderTargetDestination =
  | "master"
  | "instagram_reel"
  | "instagram_feed"
  | "youtube"
  | "youtube_shorts"
  | "tiktok"
  | "whatsapp_status"
  | "web_preview";

export const RenderTargetDestinationValues = [
  "master",
  "instagram_reel",
  "instagram_feed",
  "youtube",
  "youtube_shorts",
  "tiktok",
  "whatsapp_status",
  "web_preview",
] as const satisfies readonly RenderTargetDestination[];

/**
 * What this cut is for. Destination is part of the plan because the reframe, the loudness
 * target and the duration are all chosen for it -- an Instagram cut is not a YouTube cut
 * at a different bitrate.
 */
export interface RenderTarget {
  destination: RenderTargetDestination;

  resolution: PixelSize;

  aspect_ratio: AspectRatio;

  /**
   * How the file is written. Required: `destination` says what the cut is FOR and settles
   * nothing about the bytes, and a renderer that fills the difference in has made a
   * delivery decision invisibly (contracts#56).
   */
  encode: EncodeProfile;

  /**
   * What the planner was asked for. The realised duration is the sum of the timeline and
   * may differ slightly, because landing a cut on a beat matters more than hitting exactly
   * 30.000s.
   */
  target_duration?: RationalTime | null;

  /**
   * Hard ceiling imposed by the platform. Exceeding it is a validation failure, not a
   * warning.
   */
  max_duration?: RationalTime | null;

  /**
   * Integrated loudness the mix is normalised to. -14 for most social platforms, -23 for
   * broadcast-style masters.
   */
  loudness_target_lufs?: number | null;
}

export type StoryArcTemplate =
  | "hook_build_peak_button"
  | "three_act"
  | "chronological"
  | "day_in_the_life"
  | "before_after"
  | "montage"
  | "custom";

export const StoryArcTemplateValues = [
  "hook_build_peak_button",
  "three_act",
  "chronological",
  "day_in_the_life",
  "before_after",
  "montage",
  "custom",
] as const satisfies readonly StoryArcTemplate[];

export interface StoryArcEnergyCurveItem {
  time: RationalTime;

  energy: Unit;
}

export type StoryArcSource = "template" | "tier2_vlm" | "tier3_model" | "user";

export const StoryArcSourceValues = [
  "template",
  "tier2_vlm",
  "tier3_model",
  "user",
] as const satisfies readonly StoryArcSource[];

/**
 * The narrative intention, kept with the plan so that a revision instruction such as 'more
 * of her' or 'less drone' re-satisfies the SAME arc instead of re-planning from scratch.
 * That persistence is what makes iterative editing feel like direction rather than dice-
 * rolling.
 */
export interface StoryArc {
  arc_id: Slug;

  template: StoryArcTemplate;

  title?: string | null;

  /**
   * One sentence the arc is trying to say. Written by the frontier model and shown to the
   * user, so a plan can be judged before it is rendered.
   */
  logline?: string | null;

  acts: Act[];

  /**
   * Target energy over the timeline, as sampled control points. The planner satisfies it
   * mechanically by choosing moments whose features match; it is a specification, not a
   * description. Default: [].
   */
  energy_curve?: StoryArcEnergyCurveItem[];

  /**
   * Who authored the arc. A tier3_model arc must carry its ConsentRef, because producing
   * it meant sending a contact sheet off the device.
   */
  source: StoryArcSource;

  model?: ModelRef | null;

  /**
   * Which prompt-engine template produced it, so a prompt regression is traceable to the
   * outputs it affected.
   */
  prompt_id?: Slug | null;

  consent?: ConsentRef | null;

  /**
   * The model's stated reasoning, kept for the user-facing 'why this cut' explanation and
   * for eval review.
   */
  rationale?: string | null;
}

/**
 * A required narrative element and the clips that satisfy it. An unsatisfied required beat
 * is a validation failure -- that is the mechanism by which 'the film has an arc' is
 * checked rather than hoped for.
 */
export interface StoryBeat {
  beat_id: Slug;

  description: string;

  required: boolean;

  /** Default: []. */
  satisfied_by_clip_ids?: Slug[];

  /**
   * Moments the planner considered for this beat. Retained so a revision can swap in an
   * alternative without re-running retrieval. Default: [].
   */
  candidate_moment_ids?: Blake3Hash[];
}

export type SubjectLockSource = "sam2_track" | "face_track" | "saliency" | "manual";

export const SubjectLockSourceValues = [
  "sam2_track",
  "face_track",
  "saliency",
  "manual",
] as const satisfies readonly SubjectLockSource[];

export type SubjectLockKeepInFrame = "head" | "full_body" | "bbox_center" | "bbox_full";

export const SubjectLockKeepInFrameValues = [
  "head",
  "full_body",
  "bbox_center",
  "bbox_full",
] as const satisfies readonly SubjectLockKeepInFrame[];

export interface SubjectLock {
  source: SubjectLockSource;

  /** The tracked entity: a SAM 2 object id, a face track uuid, or null for saliency. */
  subject_ref?: string | null;

  person_id?: Uuid | null;

  /**
   * The part of the subject that must never leave the crop. `head` is the one that matters
   * for people: a technically-centred crop that clips a forehead reads as a mistake.
   * Default: "head".
   */
  keep_in_frame?: SubjectLockKeepInFrame;

  /**
   * Fraction of the crop height kept above the subject's head. Composition rule, planned
   * rather than hard-coded in the renderer.
   */
  headroom?: Unit | null;
}

export type TimeEffectKind = "linear_speed" | "freeze_frame";

export const TimeEffectKindValues = [
  "linear_speed",
  "freeze_frame",
] as const satisfies readonly TimeEffectKind[];

export type TimeEffectAudioHandling = "mute" | "resample" | "preserve_pitch";

export const TimeEffectAudioHandlingValues = [
  "mute",
  "resample",
  "preserve_pitch",
] as const satisfies readonly TimeEffectAudioHandling[];

/**
 * Speed change. Restricted to what OTIO models natively, because a speed ramp that cannot
 * round-trip is a speed ramp that silently disappears in Resolve. `source_range` stays
 * authoritative under an effect and the timeline extent is derived from it -- see the
 * $comment, which is the rule the renderer implements.
 */
export interface TimeEffect {
  kind: TimeEffectKind;

  /**
   * OTIO LinearTimeWarp.time_scalar: the ratio of media time to timeline time. 0.5 is half
   * speed (twice the timeline extent), 2.0 is double. Required for linear_speed, and must
   * divide source_range.duration into a whole number of timeline frames.
   */
  time_scalar?: number | null;

  /**
   * Source time to hold. Required for freeze_frame, and must equal source_range.start_time
   * -- the frozen frame is the one frame the clip reads.
   */
  freeze_at?: RationalTime | null;

  /**
   * How long the frozen frame is held, in TIMELINE time. Required for freeze_frame,
   * forbidden otherwise: it is the clip's timeline extent, and without it a freeze has a
   * start and no end.
   */
  hold_duration?: RationalTime | null;

  /**
   * What happens to this clip's audio under a speed change. Almost always `mute` for slow
   * motion, because pitch-shifted ambient sounds broken. `mute` suppresses this clip's
   * ambient bed entirely, whatever AmbientPlan says about it. Default: "mute".
   */
  audio_handling?: TimeEffectAudioHandling;
}

export type ToneMapOperator = "hable" | "reinhard" | "mobius";

export const ToneMapOperatorValues = [
  "hable",
  "reinhard",
  "mobius",
] as const satisfies readonly ToneMapOperator[];

/**
 * How HDR light is fitted into the SDR output volume. Required exactly when some source
 * carries an HDR encoding; null otherwise, because a tone map over SDR sources is a grade
 * nobody asked for.
 */
export interface ToneMap {
  /** Which curve. Formulas are written out in this def's $comment and are normative. */
  operator: ToneMapOperator;

  /**
   * The operator's single shape parameter: reinhard's contrast, mobius's linear-section
   * end. Null for hable, which has none. There is no default -- a curve parameter a
   * renderer supplies is a curve nobody chose. The range is OPEN AT BOTH ENDS for both
   * parameterised operators, and 1 is excluded because it is degenerate rather than merely
   * extreme: see the $comment.
   */
  operator_param: number | null;

  /**
   * What 1.0 means in the working space, in cd/m^2. 100 is SDR diffuse white (BT.1886);
   * 203 is the BT.2100 HLG graphic-white convention. Required because it is the scale
   * every other number in this object is expressed against.
   */
  reference_white_nits: number;

  /**
   * Threshold, in units of reference white, above which a pixel is pulled towards its own
   * luminance before mapping. 0 disables it. See the $comment for the exact formula.
   */
  desaturation: number;
}

export type TrackKind = "video" | "audio";

export const TrackKindValues = [
  "video",
  "audio",
] as const satisfies readonly TrackKind[];

export type TrackRole =
  | "primary"
  | "overlay"
  | "titles"
  | "ambient"
  | "music"
  | "voiceover"
  | "sfx";

export const TrackRoleValues = [
  "primary",
  "overlay",
  "titles",
  "ambient",
  "music",
  "voiceover",
  "sfx",
] as const satisfies readonly TrackRole[];

export interface Track {
  track_id: Slug;

  /** Maps to OTIO Track.kind, which recognises exactly "Video" and "Audio". */
  kind: TrackKind;

  /** Default: "". */
  name?: string;

  /**
   * What the track is for. Purely ours -- it rides in metadata -- but it lets the renderer
   * build the filtergraph without inspecting contents. Default: "primary".
   */
  role?: TrackRole;

  /** Default: true. */
  enabled?: boolean;

  /**
   * Ordered children. Clips and gaps tile the track; transitions sit between two
   * neighbours and overlap them.
   */
  items: Array<Clip | Gap | Transition>;
}

export type TransitionTransitionType = "dissolve" | "dip_to_black" | "dip_to_white";

export const TransitionTransitionTypeValues = [
  "dissolve",
  "dip_to_black",
  "dip_to_white",
] as const satisfies readonly TransitionTransitionType[];

export type TransitionEasing = "linear" | "ease_in" | "ease_out" | "ease_in_out";

export const TransitionEasingValues = [
  "linear",
  "ease_in",
  "ease_out",
  "ease_in_out",
] as const satisfies readonly TransitionEasing[];

/**
 * An overlap between the two neighbouring items. A hard cut is the absence of one of
 * these, never a zero-length instance -- that is OTIO's convention and departing from it
 * breaks the round trip.
 */
export interface Transition {
  item_type: "transition";

  transition_id?: Slug | null;

  /**
   * `dissolve` maps to OTIO's standard SMPTE_Dissolve. The two dips map to OTIO "Custom"
   * with the specific kind preserved in metadata, which is how OTIO itself handles non-
   * standard transitions. See this def's $comment for why the enum is only three values.
   */
  transition_type: TransitionTransitionType;

  /** How far the transition extends backwards into the outgoing item. */
  in_offset: RationalTime;

  /** How far it extends forwards into the incoming item. */
  out_offset: RationalTime;

  /**
   * Shape of the blend weight across the transition. Each value is a polynomial in the
   * linear progress u, written out in this def's $comment; a name on its own is not a
   * curve, and a cubic ease and a sine ease are different shots. Default: "linear".
   */
  easing?: TransitionEasing;
}

export type VariantInfoStrategy =
  | "moment_subset"
  | "pacing_seed"
  | "energy_template"
  | "music_alternate"
  | "reframe_style"
  | "duration_alternate";

export const VariantInfoStrategyValues = [
  "moment_subset",
  "pacing_seed",
  "energy_template",
  "music_alternate",
  "reframe_style",
  "duration_alternate",
] as const satisfies readonly VariantInfoStrategy[];

export interface VariantInfo {
  variant_id: Slug;

  variant_index: number;

  /** Default: []. */
  sibling_edl_ids?: Blake3Hash[];

  /**
   * What was varied. Variants must differ along a stated axis, so the user's pick is
   * interpretable as a preference rather than as noise.
   */
  strategy: VariantInfoStrategy;

  /** One line the variant picker shows: 'faster, more action'. */
  description?: string | null;
}

export type EDLKind = "reel" | "film" | "highlight" | "chapter_preview" | "custom";

export const EDLKindValues = [
  "reel",
  "film",
  "highlight",
  "chapter_preview",
  "custom",
] as const satisfies readonly EDLKind[];

/**
 * The deterministic edit plan for one video output. Every creative decision in the
 * finished film or reel is expressed here; the renderer executes it and decides nothing.
 */
export interface EDL {
  schema_version: SchemaVersion;

  /**
   * BLAKE3 over the canonical JSON of this EDL with the volatile fields removed. Two EDLs
   * with the same id render identically.
   */
  edl_id: Blake3Hash;

  name?: string;

  kind: EDLKind;

  /**
   * Timeline rate in units per second. All video-track RationalTimes are expressed at this
   * rate. Use exact NTSC rationals where the source demands it (30000/1001), never the
   * rounded decimal.
   */
  rate: number;

  /**
   * Timeline zero, exported as OTIO Timeline.global_start_time. Normally 0; non-zero when
   * the output must carry a broadcast start timecode such as 01:00:00:00.
   */
  global_start_time?: RationalTime | null;

  target: RenderTarget;

  /**
   * Every source this EDL touches, addressed by content hash. Declared once at the top so
   * a renderer can resolve, verify and pre-open all sources before it starts, and so a
   * missing source is a clean up-front failure rather than a crash at 80%.
   */
  media_refs: MediaRef[];

  /** Ordered tracks. Index 0 is the bottom video layer, matching OTIO Stack ordering. */
  tracks: Track[];

  /**
   * Crop/reframe keyframe tracks, referenced by clips. Held at EDL level rather than
   * inline on the clip so one subject-lock track can drive several clips from the same
   * source shot. Default: [].
   */
  reframe_tracks?: ReframeTrack[];

  audio_plan?: AudioPlan | null;

  beat_grid?: BeatGrid | null;

  story_arc?: StoryArc | null;

  /**
   * REQUIRED and non-null (contracts#58). It was nullable, and a null colour pipeline is a
   * plan that declines to say what colour its own output is -- which leaves the renderer
   * to decide, which is the whole defect. Every EDL states its colour path, including the
   * ordinary all-SDR one, where the statement is short and the pipeline is an identity.
   */
  color_pipeline: ColorPipeline;

  /**
   * Present when this EDL is one of several alternatives offered to the user. The reel
   * planner emits 3-5; whichever the user picks becomes a PrefEvent, and the losers are
   * training signal too.
   */
  variant?: VariantInfo | null;

  determinism: Determinism;

  validation?: EdlValidation | null;

  otio?: OtioExportInfo | null;
}

export type ClusterMembershipMethod =
  | "hdbscan_cosine"
  | "agglomerative_cosine"
  | "user_grouped"
  | "singleton";

export const ClusterMembershipMethodValues = [
  "hdbscan_cosine",
  "agglomerative_cosine",
  "user_grouped",
  "singleton",
] as const satisfies readonly ClusterMembershipMethod[];

/**
 * Unsupervised grouping over embedding distance (HDBSCAN over cosine). A cluster is a
 * hypothesis, not an identity: it is allowed to be wrong, which is exactly why it is
 * stored separately from `identity`.
 */
export interface ClusterMembership {
  cluster_id: Uuid;

  method: ClusterMembershipMethod;

  /**
   * HDBSCAN membership probability. Low values sit near the decision boundary and are
   * precisely the ones the active-learning loop should ask a human about first -- ten
   * well-chosen taps fix a thousand photos.
   */
  membership_strength?: Unit | null;

  /**
   * HDBSCAN noise point: too far from any cluster. Never surfaces in automated output.
   * Default: false.
   */
  is_noise?: boolean;

  distance_to_centroid?: number | null;

  /**
   * Which clustering pass produced this. Re-clustering a growing library reshuffles
   * cluster ids; pinning the run makes the reshuffle auditable instead of mysterious.
   */
  clustering_run_id?: Slug | null;
}

export type DetectionDetectedOn =
  | "thumbnail_512"
  | "preview_2048"
  | "video_proxy_480p"
  | "original";

export const DetectionDetectedOnValues = [
  "thumbnail_512",
  "preview_2048",
  "video_proxy_480p",
  "original",
] as const satisfies readonly DetectionDetectedOn[];

export interface Detection {
  /**
   * Normalised against the ORIENTED frame, so the same box is valid on the 512px thumbnail
   * the detector saw and on the 6000px original the renderer will crop.
   */
  bbox: NormalizedBox;

  detection_score: Confidence;

  detector: ModelRef;

  /**
   * Which rendition the detector ran against. Small faces missed on a thumbnail are re-
   * detected at full resolution on demand; recording this makes 'have we already looked
   * properly' answerable. Default: "thumbnail_512".
   */
  detected_on?: DetectionDetectedOn;

  /**
   * Fraction of the frame the box covers. The single best predictor of whether an
   * embedding will be trustworthy, and a hard input to the automated-output threshold.
   */
  face_area_ratio?: Unit;
}

/**
 * Everything that decides whether this is a GOOD photo of this person, as opposed to
 * whether it is this person. Feeds face-quality scoring, album hero selection, and the 'is
 * anyone blinking' check.
 */
export interface FaceAttributes {
  /**
   * Head pose. Beyond about +/-45 degrees yaw, recognition confidence degrades sharply and
   * the automated-output threshold should tighten.
   */
  yaw_deg?: number | null;

  pitch_deg?: number | null;

  roll_deg?: number | null;

  /** Probability both eyes are open. The blink check that saves an album spread. */
  eyes_open?: Confidence | null;

  smile?: Confidence | null;

  mouth_open?: Confidence | null;

  gaze_on_camera?: Confidence | null;

  sharpness?: Unit | null;

  /** How much of the face is hidden by a hand, hair, mask or another person. */
  occlusion?: Unit | null;

  wearing_sunglasses?: Confidence | null;

  wearing_mask?: Confidence | null;

  /** Fused face quality, the value album selection actually sorts on. */
  quality?: Score | null;
}

export interface FaceTrack {
  track_id: Uuid;

  /** Source-time span the track covers. */
  track_range: TimeRange;

  position_in_track?: number | null;

  track_length?: number | null;

  /**
   * The single best frame of this track, chosen by face quality. Identity is decided once
   * per track from the representative, not voted per frame -- 120 correlated votes are not
   * 120 pieces of evidence. Default: false.
   */
  is_track_representative?: boolean;
}

export type IdentityAssignment =
  | "unassigned"
  | "user_confirmed"
  | "user_rejected"
  | "auto_high_confidence"
  | "auto_below_threshold"
  | "review_queued"
  | "ambiguous_multiple_candidates";

export const IdentityAssignmentValues = [
  "unassigned",
  "user_confirmed",
  "user_rejected",
  "auto_high_confidence",
  "auto_below_threshold",
  "review_queued",
  "ambiguous_multiple_candidates",
] as const satisfies readonly IdentityAssignment[];

export type IdentityThresholdProfile = "automated_output" | "review_queue" | "search_only";

export const IdentityThresholdProfileValues = [
  "automated_output",
  "review_queue",
  "search_only",
] as const satisfies readonly IdentityThresholdProfile[];

export interface IdentityCandidatesItem {
  person_id: Uuid;

  confidence: Confidence;
}

export type IdentityReviewReason =
  | "below_threshold"
  | "near_boundary"
  | "multiple_candidates"
  | "new_cluster"
  | "user_reported_error"
  | "low_face_quality"
  | "extreme_pose";

export const IdentityReviewReasonValues = [
  "below_threshold",
  "near_boundary",
  "multiple_candidates",
  "new_cluster",
  "user_reported_error",
  "low_face_quality",
  "extreme_pose",
] as const satisfies readonly IdentityReviewReason[];

export type IdentityDecidedBy = "model" | "user" | "rule";

export const IdentityDecidedByValues = [
  "model",
  "user",
  "rule",
] as const satisfies readonly IdentityDecidedBy[];

/**
 * Who we say this is, how sure we are, and whether that is sure enough to act on
 * unattended.
 */
export interface Identity {
  /**
   * Null until an assignment exists. A person is a user-facing entity created by labeling,
   * never by clustering alone.
   */
  person_id?: Uuid | null;

  /**
   * How the person_id was arrived at. `auto_below_threshold` is a real, common state: the
   * model has a guess it is not allowed to use, which is the entire point of precision-
   * first.
   */
  assignment: IdentityAssignment;

  /** Calibrated similarity-to-person confidence. Null when unassigned. */
  confidence?: Confidence | null;

  /**
   * Which operating point was applied. `automated_output` is the strict one tuned for
   * >=99% precision (build plan section 7); `search_only` is permissive because a wrong
   * hit in a search result is a shrug, not a catastrophe. Default: "automated_output".
   */
  threshold_profile?: IdentityThresholdProfile;

  /**
   * The actual numeric threshold applied, stored so that retuning the operating point is a
   * replayable decision rather than a silent behaviour change.
   */
  threshold_used?: Confidence | null;

  /**
   * THE GATE. Album, film and reel selection may only treat this face as a known person
   * when this is true. Invariant, enforced in tests: true requires assignment to be
   * user_confirmed, or auto_high_confidence with confidence >= threshold_used. Every other
   * state is false.
   */
  eligible_for_automated_output: boolean;

  /**
   * Runner-up people considered. Populated when assignment is
   * ambiguous_multiple_candidates, which is the twins-and-siblings case that a single
   * best-match number hides. Default: [].
   */
  candidates?: IdentityCandidatesItem[];

  review_reason?: IdentityReviewReason | null;

  decided_by?: IdentityDecidedBy | null;

  decided_at?: Timestamp | null;
}

export type LandmarksScheme = "insightface_5" | "insightface_106" | "mediapipe_468" | "yunet_5";

export const LandmarksSchemeValues = [
  "insightface_5",
  "insightface_106",
  "mediapipe_468",
  "yunet_5",
] as const satisfies readonly LandmarksScheme[];

export interface Landmarks {
  /**
   * Point count and ordering convention. Consumers must switch on this rather than
   * assuming an index layout. yunet_5 and insightface_5 are both five points and are NOT
   * interchangeable: feeding one to an alignment template built for the other produces a
   * plausible warp and a wrong embedding, which is the worst failure mode in this system
   * because nothing downstream can detect it.
   */
  scheme: LandmarksScheme;

  points: Point2D[];

  score?: Confidence | null;
}

export type SensitiveFlagsMinorStatus =
  | "unknown"
  | "estimated_minor"
  | "confirmed_minor"
  | "confirmed_adult";

export const SensitiveFlagsMinorStatusValues = [
  "unknown",
  "estimated_minor",
  "confirmed_minor",
  "confirmed_adult",
] as const satisfies readonly SensitiveFlagsMinorStatus[];

/**
 * Child-face labeling sits behind separate explicit consent (build plan section 8).
 * Modelled here rather than on the person so that the gate is evaluated at the point of
 * use.
 */
export interface SensitiveFlags {
  /**
   * `unknown` is the default and is NOT treated as adult. Estimated age is a signal for
   * asking the user, never a licence to proceed. Default: "unknown".
   */
  minor_status: SensitiveFlagsMinorStatus;

  /**
   * Required before a confirmed_minor face may be labeled with a person identity. Absent
   * consent, the face is still detected and counted, but never named. Must be scoped to
   * minor_face_labeling specifically -- a consent granted for cloud rendering does not
   * authorise naming a child.
   */
  labeling_consent?: ConsentRef | null;

  /** Default: false. */
  excluded_from_sharing?: boolean;
}

/**
 * One detected face in one frame: where it is, what it looks like as an embedding, which
 * cluster it fell into, and -- separately and much more cautiously -- which person we are
 * willing to say it is.
 */
export interface FaceRecord {
  schema_version: SchemaVersion;

  /**
   * BLAKE3 over (media_id, frame_time, quantised bbox, detector model_id + version).
   * Content-addressed, so re-running the same detector on the same frame produces the same
   * id and re-detection is idempotent. Changing detector version deliberately produces new
   * ids rather than silently mutating old ones.
   *
   * CANONICAL ENCODING (issue #34), because 'BLAKE3 over the tuple' does not determine the
   * bytes and every writer picked its own. The hashed byte string is exactly:
   *
   * face_id = BLAKE3( utf8( DOMAIN US media_id US TIME US BBOX US MODEL_ID US VERSION ) )
   *
   * where US is U+001F INFORMATION SEPARATOR ONE, written once between adjacent fields and
   * nowhere else, and the six fields are:
   *
   * DOMAIN -- the literal ASCII string 'face:v1'. This versions THIS ENCODING, not the
   * detector. Changing the encoding means bumping it, so a re-encoding produces new ids on
   * purpose rather than colliding with old ones by accident.
   *
   * media_id -- the 64 lowercase hex characters, verbatim.
   *
   * TIME -- the EMPTY STRING when frame_time is null, i.e. for a still. Otherwise
   * `<value>/<rate>`, each number rendered in RFC 8785 / ECMAScript Number::toString form,
   * which is the same numeric rule edl_id already uses. So 1001 and 1001.0 both render as
   * `1001`, and a rate of 30000/1001 renders as `29.97002997002997`. THIS IS THE FIELD
   * THAT BREAKS FIRST ACROSS LANGUAGES: Python's repr writes `1.0` where JavaScript writes
   * `1`, and the same frame then gets two ids. A number that cannot be rendered without an
   * exponent is REJECTED rather than written, because exponent formatting is where the two
   * languages stop agreeing and no real frame rate needs one. The still case cannot be
   * confused with the video case: a rendered time always contains a `/`, and the empty
   * string never does.
   *
   * BBOX -- `<qx>,<qy>,<qw>,<qh>` where q(v) = round_half_away_from_zero(v * 10000),
   * rendered as a base-10 integer with no padding and no sign (every component is non-
   * negative by schema, so half-away-from-zero and half-up coincide). NOT banker's
   * rounding: Python's round() sends 3002.5 to 3002 while JavaScript's Math.round and
   * Rust's f64::round both send it to 3003, and 8855 of the 10000 half-quantum positions
   * in [0,1] are exactly representable as doubles, so this is reachable rather than
   * theoretical. The quantum is 1e-4 of the frame -- 0.6px on a 6000px original, finer
   * than any detector's own precision and coarse enough that the last-bit disagreement
   * between two execution providers cannot turn one face into two. `rotation_deg` does NOT
   * participate, which is why Detection pins it to 0.
   *
   * MODEL_ID, VERSION -- detection.detector.model_id and .version, verbatim. model_id is a
   * Slug and cannot contain the separator; ModelRef.version is pattern-constrained to
   * exclude control characters for exactly this reason, so the join is injective and needs
   * no length prefix.
   *
   * DELIBERATELY NOT IN THE TUPLE: weights_blake3, config_blake3, runtime, precision,
   * detected_on, detection_score, landmarks and embedding. All of them are still RECORDED,
   * on the detector ModelRef and in model_runs, so provenance is not lost -- but none of
   * them may move the id. weights_blake3 is nullable in development mode, so including it
   * would rename every face the moment the same detection is re-recorded against a pinned
   * registry: duplicated rows where deduplication was the point. config_blake3 moves when
   * a score threshold moves, which changes WHICH faces are found rather than the identity
   * of one that was found -- and a config change that really does move a box further than
   * the quantum already changes BBOX. `version` is the deliberate, human-controlled switch
   * for 'issue new ids'.
   *
   * contracts/tests recomputes this for every face fixture and for contracts/vectors/face-
   * id.json, in Python and again in TypeScript against the generated bindings. An identity
   * that is asserted rather than computed is how issue #26's invented span_id happened,
   * and this is the same failure one schema over.
   */
  face_id: Blake3Hash;

  /**
   * The MediaRecord this face was found in. For spanned video this is the assembly record,
   * so a face track can cross a chapter boundary.
   */
  media_id: Blake3Hash;

  /**
   * Position within the video, in SOURCE time (already mapped back through the proxy frame
   * index). Null for stills.
   */
  frame_time?: RationalTime | null;

  /**
   * Face track membership for video. One person walking through a 4-second shot is ~120
   * FaceRecords sharing a track_id; the ranking and moment layers work on tracks, not
   * frames.
   */
  track?: FaceTrack | null;

  detection: Detection;

  landmarks?: Landmarks | null;

  /**
   * Recognition embedding. Null when the face was detected but was too small, too blurred
   * or too occluded to embed reliably -- an unembeddable face is still worth recording,
   * because it counts toward 'how many people are in this photo'.
   */
  embedding?: VectorRef | null;

  attributes?: FaceAttributes | null;

  cluster?: ClusterMembership | null;

  identity: Identity;

  sensitive: SensitiveFlags;

  /** Default: []. */
  model_runs?: ModelRun[];

  created_at?: Timestamp;

  updated_at?: Timestamp;
}

/**
 * Durable resumption state. The cursor is opaque on purpose: the contract promises to
 * persist and return it, and declines to speculate about what a directory walker or a
 * video encoder needs to remember.
 */
export interface Checkpoint {
  /**
   * False for jobs that must restart from zero -- a short atomic render, say. Stating it
   * explicitly stops a scheduler from assuming either way.
   */
  resumable: boolean;

  /** Worker-owned opaque state. Null means 'resumable but not yet started'. */
  cursor: string | null;

  /**
   * Bumped when a worker changes its cursor format. A cursor from an older version is
   * discarded and the job restarts, rather than being handed to code that will misparse
   * it. Default: 0.
   */
  checkpoint_version?: number;

  updated_at?: Timestamp | null;

  /**
   * Inputs already finished. On resume these are skipped, which is what stops a resumed
   * 3TB scan from rehashing the first 2TB. Default: [].
   */
  completed_input_ids?: Blake3Hash[];

  /**
   * Outputs written before the interruption. Recorded so they are neither orphaned nor
   * recreated. Default: [].
   */
  partial_output_ids?: Blake3Hash[];
}

export type EgressDeclarationDestination =
  | "tier3_inference"
  | "cloud_render"
  | "billing"
  | "sync"
  | "share"
  | "print_vendor"
  | "telemetry";

export const EgressDeclarationDestinationValues = [
  "tier3_inference",
  "cloud_render",
  "billing",
  "sync",
  "share",
  "print_vendor",
  "telemetry",
] as const satisfies readonly EgressDeclarationDestination[];

export type EgressDeclarationPayloadKind =
  | "contact_sheet"
  | "thumbnail"
  | "feature_vector"
  | "metadata_only"
  | "structured_decision"
  | "original_media"
  | "rendered_output";

export const EgressDeclarationPayloadKindValues = [
  "contact_sheet",
  "thumbnail",
  "feature_vector",
  "metadata_only",
  "structured_decision",
  "original_media",
  "rendered_output",
] as const satisfies readonly EgressDeclarationPayloadKind[];

/**
 * Whether this job talks to the network, and on whose authority. Declared on every job
 * including the overwhelming majority that declare `false`, because an absent declaration
 * and a negative one must not look the same.
 */
export interface EgressDeclaration {
  requires_egress: boolean;

  consent?: ConsentRef | null;

  destination?: EgressDeclarationDestination | null;

  /**
   * What actually leaves the device. `contact_sheet` and `thumbnail` are the only image-
   * bearing values permitted for Tier 3, and `original_media` requires its own explicit
   * consent scope -- originals never leave without one.
   */
  payload_kind?: EgressDeclarationPayloadKind | null;

  estimated_bytes?: number | null;
}

export type JobErrorCode =
  | "file_not_found"
  | "file_unreadable"
  | "file_corrupt"
  | "zero_byte_file"
  | "unsupported_codec"
  | "unsupported_format"
  | "symlink_loop"
  | "permission_denied"
  | "disk_full"
  | "out_of_memory"
  | "gpu_unavailable"
  | "model_load_failed"
  | "model_inference_failed"
  | "timeout"
  | "cancelled_by_user"
  | "dependency_failed"
  | "consent_missing"
  | "consent_revoked"
  | "network_unavailable"
  | "rate_limited"
  | "validation_failed"
  | "internal_error";

export const JobErrorCodeValues = [
  "file_not_found",
  "file_unreadable",
  "file_corrupt",
  "zero_byte_file",
  "unsupported_codec",
  "unsupported_format",
  "symlink_loop",
  "permission_denied",
  "disk_full",
  "out_of_memory",
  "gpu_unavailable",
  "model_load_failed",
  "model_inference_failed",
  "timeout",
  "cancelled_by_user",
  "dependency_failed",
  "consent_missing",
  "consent_revoked",
  "network_unavailable",
  "rate_limited",
  "validation_failed",
  "internal_error",
] as const satisfies readonly JobErrorCode[];

export interface JobError {
  code: JobErrorCode;

  /**
   * Already redacted: no paths, no filenames, no EXIF. Crash reporting forwards this
   * verbatim, so redaction happens at write time rather than at send time.
   */
  message: string;

  retryable: boolean;

  /** Default: 0. */
  attempt?: number;

  occurred_at?: Timestamp | null;

  /**
   * Which specific input broke, so a 300k-file scan reports one bad file rather than
   * failing wholesale.
   */
  failed_input_id?: Blake3Hash | null;
}

/**
 * Everything the job reads, addressed by hash. Content addressing is what makes the whole
 * pipeline idempotent: identical inputs cannot produce a different job.
 */
export interface JobInputs {
  /**
   * Sorted before hashing into job_id, so input order never changes job identity. Default:
   * [].
   */
  media_ids?: Blake3Hash[];

  /** Default: []. */
  moment_ids?: Blake3Hash[];

  /** Default: []. */
  face_ids?: Blake3Hash[];

  edl_id?: Blake3Hash | null;

  album_id?: Blake3Hash | null;

  /**
   * Only for scan_source, which by definition starts before anything has a hash. Every
   * other job type addresses content, never location. Default: [].
   */
  source_paths?: string[];

  /**
   * BLAKE3 over the canonical form of `source_paths`: each path resolved to absolute,
   * symlinks followed, trailing separators stripped, NFC-normalised, then sorted and
   * joined with a NUL separator. Required for scan_source and the only thing
   * distinguishing two scans of different folders, since neither has content hashes yet.
   * Canonicalisation matters as much as the digest -- '/Volumes/Archive' and
   * '/Volumes/Archive/' must not be two jobs, or a re-scan re-walks a whole drive.
   */
  source_locator_digest?: Blake3Hash | null;

  /**
   * The job that spawned this one. A scan spawns a hash job per file; the tree is what
   * makes 'cancel this import' a well-defined operation.
   */
  parent_job_id?: Blake3Hash | null;

  /** Jobs that must reach completed before this one may start. Default: []. */
  depends_on_job_ids?: Blake3Hash[];

  /**
   * Model pins. Part of the params digest, so re-running with a swapped model is a
   * different job and the old result is never silently reused. Default: [].
   */
  models?: ModelRef[];
}

export type JobOutputKind =
  | "media_record"
  | "face_record"
  | "moment_record"
  | "edl"
  | "album_spec"
  | "proxy"
  | "rendered_video"
  | "rendered_pdf"
  | "otio_file"
  | "vector_index_entry"
  | "eval_report"
  | "pref_event";

export const JobOutputKindValues = [
  "media_record",
  "face_record",
  "moment_record",
  "edl",
  "album_spec",
  "proxy",
  "rendered_video",
  "rendered_pdf",
  "otio_file",
  "vector_index_entry",
  "eval_report",
  "pref_event",
] as const satisfies readonly JobOutputKind[];

export interface JobOutput {
  kind: JobOutputKind;

  /**
   * Content hash of the produced artifact, so an output can be verified rather than
   * trusted.
   */
  id: Blake3Hash;

  path?: string | null;

  byte_size?: number | null;

  produced_at?: Timestamp | null;
}

export type JobRequirementsCompute = "cpu" | "gpu" | "neural_engine" | "any";

export const JobRequirementsComputeValues = [
  "cpu",
  "gpu",
  "neural_engine",
  "any",
] as const satisfies readonly JobRequirementsCompute[];

/**
 * What the job needs to run. The scheduler matches these against the machine rather than
 * discovering mid-render that there is no GPU.
 */
export interface JobRequirements {
  /** Default: "any". */
  compute?: JobRequirementsCompute;

  min_vram_mb?: number | null;

  min_ram_mb?: number | null;

  min_disk_mb?: number | null;

  /**
   * Proxy generation must saturate disk I/O rather than CPU, which is only possible with
   * VideoToolbox/NVDEC/QSV. A proxy job that would fall back to software decode should
   * queue rather than crawl. Default: false.
   */
  hardware_decode?: boolean;

  /**
   * True only for proxy generation and final render. Everything else works on proxies --
   * sources are opened exactly twice in a file's life. Default: false.
   */
  requires_source_file?: boolean;

  estimated_duration_ms?: number | null;
}

export type JobStateStatus =
  | "pending"
  | "blocked"
  | "running"
  | "paused"
  | "completed"
  | "failed"
  | "cancelled"
  | "quarantined";

export const JobStateStatusValues = [
  "pending",
  "blocked",
  "running",
  "paused",
  "completed",
  "failed",
  "cancelled",
  "quarantined",
] as const satisfies readonly JobStateStatus[];

export interface JobState {
  /**
   * `paused` is a deliberate user action; `pending` after a crash is what a killed
   * `running` job becomes on relaunch. Distinguishing them is what makes resumption safe
   * -- a paused job must not restart itself.
   */
  status: JobStateStatus;

  attempts: number;

  worker_id?: string | null;

  started_at?: Timestamp | null;

  /**
   * Last sign of life. A running job whose heartbeat has gone stale was killed with the
   * process and is safe to reclaim -- without it, a crashed job is indistinguishable from
   * a slow one and blocks its queue forever.
   */
  heartbeat_at?: Timestamp | null;

  finished_at?: Timestamp | null;

  progress?: Progress | null;
}

export type JournalEntriesItemAction =
  | "delete_file"
  | "overwrite_file"
  | "move_file"
  | "prune_proxy"
  | "network_send"
  | "network_receive"
  | "consent_recorded"
  | "consent_revoked"
  | "model_swapped"
  | "index_rebuilt";

export const JournalEntriesItemActionValues = [
  "delete_file",
  "overwrite_file",
  "move_file",
  "prune_proxy",
  "network_send",
  "network_receive",
  "consent_recorded",
  "consent_revoked",
  "model_swapped",
  "index_rebuilt",
] as const satisfies readonly JournalEntriesItemAction[];

export interface JournalEntriesItem {
  action: JournalEntriesItemAction;

  at: Timestamp;

  target_id?: Blake3Hash | null;

  /** Default: false. */
  reversible?: boolean;

  /** Default: "". */
  detail?: string;
}

/**
 * Record of every destructive or externally-visible action the job took. 'No silent data
 * loss' means anything irreversible is written down before it happens, not after it
 * succeeds.
 */
export interface Journal {
  /** Default: []. */
  entries?: JournalEntriesItem[];
}

export type ProgressUnit =
  | "files"
  | "bytes"
  | "frames"
  | "seconds"
  | "images"
  | "moments"
  | "pages"
  | "items";

export const ProgressUnitValues = [
  "files",
  "bytes",
  "frames",
  "seconds",
  "images",
  "moments",
  "pages",
  "items",
] as const satisfies readonly ProgressUnit[];

/**
 * Progress in real units, not a synthetic percentage. '12,400 of 318,000 files' survives a
 * restart honestly; a percentage that jumps backwards does not.
 */
export interface Progress {
  units_done: number;

  /**
   * Null while still being discovered -- a scan does not know how many files exist until
   * it has walked them, and claiming otherwise produces a progress bar that lies.
   */
  units_total?: number | null;

  unit: ProgressUnit;

  bytes_processed?: number | null;

  message?: string | null;
}

export type RetryPolicyBackoff = "none" | "linear" | "exponential";

export const RetryPolicyBackoffValues = [
  "none",
  "linear",
  "exponential",
] as const satisfies readonly RetryPolicyBackoff[];

export interface RetryPolicy {
  /** Default: 3. */
  max_attempts: number;

  /** Default: "exponential". */
  backoff?: RetryPolicyBackoff;

  /** Default: 1000. */
  initial_delay_ms?: number;

  /** Default: 60000. */
  max_delay_ms?: number;

  /**
   * Move to quarantined rather than failed once attempts are exhausted, so a hostile file
   * is never retried automatically again but is still visible in the diagnostics view.
   * Nothing is dropped silently. Default: true.
   */
  quarantine_after_max?: boolean;
}

export type JobSpecJobType =
  | "scan_source"
  | "hash_file"
  | "extract_metadata"
  | "generate_thumbnail"
  | "generate_video_proxy"
  | "perceptual_hash"
  | "analyze_image"
  | "analyze_video"
  | "detect_faces"
  | "cluster_faces"
  | "transcribe_audio"
  | "detect_shots"
  | "score_moments"
  | "rank_media"
  | "dedupe_cluster"
  | "cluster_events"
  | "plan_reel"
  | "plan_film"
  | "plan_album"
  | "tier3_request"
  | "enhance_image"
  | "render_video"
  | "render_print"
  | "export_otio"
  | "eval_run"
  | "reindex_vectors"
  | "consent_export";

export const JobSpecJobTypeValues = [
  "scan_source",
  "hash_file",
  "extract_metadata",
  "generate_thumbnail",
  "generate_video_proxy",
  "perceptual_hash",
  "analyze_image",
  "analyze_video",
  "detect_faces",
  "cluster_faces",
  "transcribe_audio",
  "detect_shots",
  "score_moments",
  "rank_media",
  "dedupe_cluster",
  "cluster_events",
  "plan_reel",
  "plan_film",
  "plan_album",
  "tier3_request",
  "enhance_image",
  "render_video",
  "render_print",
  "export_otio",
  "eval_run",
  "reindex_vectors",
  "consent_export",
] as const satisfies readonly JobSpecJobType[];

/**
 * Any unit of work in the system: what to do, to which inputs, with which parameters, how
 * far it got, and how to pick it up again.
 */
export interface JobSpec {
  schema_version: SchemaVersion;

  /**
   * BLAKE3 over (job_type, sorted input ids, source_locator_digest or the empty string,
   * params_digest, scope). Doubles as the idempotency key -- there is deliberately no
   * second field for that, because two sources of truth for identity is how duplicate work
   * gets in.
   *
   * CANONICAL ENCODING, for the same reason face_id and span_id have one: a named tuple is
   * not a byte string, and two workers that separate the fields differently compute
   * different ids for identical work -- which means the second one redoes it, or worse, a
   * genuinely different job collides with a completed one and is skipped. The hashed bytes
   * are:
   *
   * job_id = BLAKE3( utf8( job_type US IDS US LOCATOR US params_digest US scope ) )
   *
   * where US is U+001F INFORMATION SEPARATOR ONE. IDS is the media ids followed by the
   * moment ids, sorted as one list and joined with a single comma; empty when there are
   * none. LOCATOR is source_locator_digest, or the EMPTY STRING when it is null -- absent
   * and empty must render the same way, or a job with no locator gets two ids. scope
   * renders as the empty string when null.
   *
   * The fields are all fixed-alphabet (hex digests, an enumerated job_type, a slug-shaped
   * scope), so the separator is sufficient and no length prefix is needed.
   */
  job_id: Blake3Hash;

  /**
   * What kind of work. Enumerated rather than free-form so that a worker cannot be handed
   * a job type it has never heard of and improvise.
   */
  job_type: JobSpecJobType;

  inputs: JobInputs;

  /**
   * Type-specific parameters. Free-form because the parameter shape of `plan_reel` and
   * `hash_file` have nothing in common, but never anonymous: params_digest pins it. A
   * worker validates its own params against its own local schema. Default: {}.
   */
  params?: Record<string, unknown>;

  /**
   * BLAKE3 over the canonical JSON of `params`. Part of job_id, which is what makes 'same
   * job with different settings' a genuinely different job rather than a silent overwrite
   * of the first result.
   *
   * Canonical JSON here is the same rule `edl_id` states: keys sorted, no insignificant
   * whitespace, numbers in RFC 8785 / ECMAScript Number::toString form so that 1.0 and 1
   * are one value, UTF-8 bytes. One canonicalisation for the whole contract, deliberately
   * -- a second one is how a digest starts disagreeing with itself across languages.
   *
   * EVERYTHING THAT CHANGES THE RESULT MUST BE IN `params`. In particular the MODEL PINS:
   * naming a model by id alone meant that editing its config -- a detection threshold, an
   * NMS IoU -- left job_id unchanged, so the completed job was found and every already-
   * analysed record skipped. The library kept an analysis produced by settings it was no
   * longer configured with, and nothing said so. `inputs.models` carries the same pins for
   * provenance; the copy in `params` is the one that affects identity, and a writer that
   * fills one and not the other has a bug.
   */
  params_digest: Blake3Hash;

  /**
   * Namespace separating otherwise-identical work: a library id, a project id, or a user
   * id. Without it, two users analysing the same stock photo would collide on job_id.
   */
  scope?: string | null;

  /**
   * Higher runs first. Interactive work (the user is watching a progress bar) outranks
   * background sweeps. Default: 100.
   */
  priority?: number;

  requirements?: JobRequirements | null;

  egress: EgressDeclaration;

  state: JobState;

  checkpoint?: Checkpoint | null;

  /** Default: []. */
  outputs?: JobOutput[];

  error?: JobError | null;

  retry_policy?: RetryPolicy | null;

  journal?: Journal | null;

  created_at?: Timestamp;

  deadline?: Timestamp | null;
}

export interface AudioStream {
  stream_index: number;

  channels: number;

  sample_rate: number;

  codec?: string | null;

  language?: string | null;

  /**
   * Detected during proxy generation. A silent track means the ambient mix has nothing to
   * preserve and music can sit at full level.
   */
  is_silent?: boolean | null;
}

export type CaptureMetadataPresentItem =
  | "exif"
  | "xmp"
  | "iptc"
  | "quicktime"
  | "gopro_gpmf"
  | "takeout_json"
  | "maker_note";

export const CaptureMetadataPresentItemValues = [
  "exif",
  "xmp",
  "iptc",
  "quicktime",
  "gopro_gpmf",
  "takeout_json",
  "maker_note",
] as const satisfies readonly CaptureMetadataPresentItem[];

export interface Capture {
  /**
   * Always a TimeAssertion, never a bare timestamp. A file with no EXIF gets an assertion
   * with source 'unknown' and precision 'unknown', not a fabricated date.
   */
  captured_at: TimeAssertion;

  /**
   * Which metadata blocks were actually found. Empty array is the EXIF-less case and is
   * completely normal for WhatsApp media and screenshots.
   */
  metadata_present: CaptureMetadataPresentItem[];

  gps?: GeoPoint | null;

  device?: DeviceInfo | null;

  exposure?: ExposureInfo | null;
}

export type ContentAnalysisTagsItemSource =
  | "zero_shot_siglip"
  | "ocr"
  | "exif"
  | "user"
  | "tier2_vlm"
  | "tier3_model";

export const ContentAnalysisTagsItemSourceValues = [
  "zero_shot_siglip",
  "ocr",
  "exif",
  "user",
  "tier2_vlm",
  "tier3_model",
] as const satisfies readonly ContentAnalysisTagsItemSource[];

export interface ContentAnalysisTagsItem {
  label: string;

  score: Unit;

  source: ContentAnalysisTagsItemSource;
}

export type ContentAnalysisSceneType =
  | "indoor"
  | "outdoor"
  | "portrait"
  | "landscape"
  | "food"
  | "document"
  | "screenshot"
  | "night"
  | "underwater"
  | "aerial"
  | "unknown";

export const ContentAnalysisSceneTypeValues = [
  "indoor",
  "outdoor",
  "portrait",
  "landscape",
  "food",
  "document",
  "screenshot",
  "night",
  "underwater",
  "aerial",
  "unknown",
] as const satisfies readonly ContentAnalysisSceneType[];

/**
 * Present when text was found. Screenshot and document detection ride on this, and both
 * are auto-excluded from memories.
 */
export interface ContentAnalysisOcr {
  has_text: boolean;

  text_area_ratio: Unit;

  /** Default: []. */
  languages?: string[];

  /** Default: false. */
  is_screenshot?: boolean;

  /** Default: false. */
  is_document?: boolean;
}

export interface ContentAnalysis {
  /**
   * One SigLIP embedding powers search, dedupe refinement, diversity constraints and zero-
   * shot tagging. This single field is the highest-leverage thing in the record.
   */
  embedding?: VectorRef | null;

  /** Default: []. */
  tags?: ContentAnalysisTagsItem[];

  scene_type?: ContentAnalysisSceneType | null;

  /**
   * Present when text was found. Screenshot and document detection ride on this, and both
   * are auto-excluded from memories.
   */
  ocr?: ContentAnalysisOcr | null;

  safety?: SafetyAssessment | null;
}

export type DedupeMembershipMethod =
  | "exact_content_hash"
  | "phash_bucket"
  | "phash_bucket_embedding_refined"
  | "burst_metadata"
  | "user_grouped";

export const DedupeMembershipMethodValues = [
  "exact_content_hash",
  "phash_bucket",
  "phash_bucket_embedding_refined",
  "burst_metadata",
  "user_grouped",
] as const satisfies readonly DedupeMembershipMethod[];

/**
 * Near-duplicate grouping. Exactly one member of a group is primary; the ranking engine
 * picks it, and only the primary is eligible for automated output so a burst of 12 near-
 * identical frames contributes one photo, not twelve.
 */
export interface DedupeMembership {
  group_id: Uuid;

  is_primary: boolean;

  primary_media_id?: Blake3Hash | null;

  similarity_to_primary?: Unit | null;

  group_size?: number | null;

  method: DedupeMembershipMethod;
}

export interface DeviceInfo {
  make?: string | null;

  model?: string | null;

  lens?: string | null;

  software?: string | null;

  /**
   * Hashed, never raw. A camera serial is a personal identifier; we want 'same body'
   * equality without storing the number.
   */
  body_serial_hash?: Blake3Hash | null;
}

export interface ErrorInfo {
  code: Slug;

  /**
   * Human-readable, already redacted: no paths, no filenames, no EXIF. Crash reporting
   * forwards this verbatim.
   */
  message: string;

  retryable: boolean;

  occurred_at?: Timestamp | null;
}

export type ExclusionStateReasonsItem =
  | "screenshot"
  | "document"
  | "nsfw"
  | "sensitive"
  | "corrupt"
  | "unreadable"
  | "duplicate_secondary"
  | "below_quality_floor"
  | "black_frame"
  | "lens_obstructed"
  | "too_short"
  | "user_hidden"
  | "unsupported_codec";

export const ExclusionStateReasonsItemValues = [
  "screenshot",
  "document",
  "nsfw",
  "sensitive",
  "corrupt",
  "unreadable",
  "duplicate_secondary",
  "below_quality_floor",
  "black_frame",
  "lens_obstructed",
  "too_short",
  "user_hidden",
  "unsupported_codec",
] as const satisfies readonly ExclusionStateReasonsItem[];

/**
 * Whether this file may appear in unattended output. Separate from user hiding: exclusion
 * is a system judgement with a stated reason, and every reason is individually
 * overridable.
 */
export interface ExclusionState {
  excluded_from_automation: boolean;

  /** Default: []. */
  reasons?: ExclusionStateReasonsItem[];

  /**
   * Tri-state on purpose. null = no opinion, true = user forced it in, false = user forced
   * it out. Distinguishing 'user said include' from 'system did not exclude' matters when
   * the exclusion rules later change.
   */
  user_override?: boolean | null;
}

/**
 * Shot settings, used as priors by the technical quality pass: a 1/8s handheld exposure
 * predicts motion blur before any model runs.
 */
export interface ExposureInfo {
  iso?: number | null;

  exposure_time_s?: number | null;

  f_number?: number | null;

  focal_length_mm?: number | null;

  focal_length_35mm?: number | null;

  flash_fired?: boolean | null;

  metering_mode?: string | null;
}

export interface FaceSummary {
  face_count: number;

  face_ids: Blake3Hash[];

  /**
   * Only people whose assignment is eligible for automated output. A person appearing here
   * is safe to use for 'album of Avika'; anything less certain is deliberately absent.
   * Default: [].
   */
  confirmed_person_ids?: Uuid[];

  /** Default: 0. */
  pending_review_count?: number;

  largest_face_area_ratio?: Unit | null;
}

export type FrameIndexSidecarMapping = "identity" | "table";

export const FrameIndexSidecarMappingValues = [
  "identity",
  "table",
] as const satisfies readonly FrameIndexSidecarMapping[];

/**
 * Mapping from proxy time to source timecode. The intelligence layer works entirely in
 * proxy time; this is the single point where that is converted to something the renderer
 * can seek to in the original.
 */
export interface FrameIndexSidecar {
  path: string;

  entry_count: number;

  /**
   * `identity` when proxy and source share a frame timeline and no lookup is needed -- the
   * common CFR case. `table` when the sidecar must be consulted per frame, which is the
   * VFR phone-video case.
   */
  mapping: FrameIndexSidecarMapping;

  source_rate?: number | null;

  proxy_rate?: number | null;
}

export type ImagePropertiesColorSpace =
  | "srgb"
  | "display_p3"
  | "adobe_rgb"
  | "prophoto_rgb"
  | "rec2020"
  | "linear"
  | "unknown";

export const ImagePropertiesColorSpaceValues = [
  "srgb",
  "display_p3",
  "adobe_rgb",
  "prophoto_rgb",
  "rec2020",
  "linear",
  "unknown",
] as const satisfies readonly ImagePropertiesColorSpace[];

export interface ImageProperties {
  /** Pixel dimensions as stored in the file, before EXIF orientation is applied. */
  stored_size: PixelSize;

  /**
   * Dimensions after orientation. Every NormalizedBox in the system is relative to THIS,
   * which removes an entire class of rotated-crop bugs.
   */
  oriented_size: PixelSize;

  /** EXIF orientation tag, 1-8. Default: 1. */
  orientation?: number;

  bit_depth?: number | null;

  color_space?: ImagePropertiesColorSpace | null;

  icc_profile_name?: string | null;

  /** Default: false. */
  has_alpha?: boolean;

  /** Default: false. */
  is_raw?: boolean;

  /** Default: false. */
  is_hdr?: boolean;

  /** For a Live Photo or Motion Photo, the record holding the motion track. */
  paired_motion_media_id?: Blake3Hash | null;
}

export interface PerceptualFingerprintKeyframeHashesItem {
  time: RationalTime;

  hash: PerceptualHash;
}

export interface PerceptualFingerprint {
  image_hash?: PerceptualHash | null;

  /**
   * Per-keyframe hashes for video, so a clip that appears in two exports of the same trip
   * is recognised as duplicate footage. Default: [].
   */
  keyframe_hashes?: PerceptualFingerprintKeyframeHashesItem[];
}

export type ProcessingStateState =
  | "discovered"
  | "hashed"
  | "proxied"
  | "analyzing"
  | "analyzed"
  | "failed"
  | "quarantined";

export const ProcessingStateStateValues = [
  "discovered",
  "hashed",
  "proxied",
  "analyzing",
  "analyzed",
  "failed",
  "quarantined",
] as const satisfies readonly ProcessingStateState[];

export interface ProcessingStateStages {
  hash?: StageState;

  metadata?: StageState;

  thumbnail?: StageState;

  video_proxy?: StageState;

  perceptual_hash?: StageState;

  classical_quality?: StageState;

  image_embedding?: StageState;

  face_detection?: StageState;

  /**
   * Aligning each detected face onto the recognition model's template and embedding it.
   * Separate from `face_detection` because they fail and resume separately: a detector
   * that ran and an embedder that was missing must leave the library with face BOXES --
   * which the print validator's trim-zone check needs and which have nothing to do with
   * identity -- rather than with neither.
   */
  face_embedding?: StageState;

  iqa?: StageState;

  aesthetic?: StageState;

  tagging?: StageState;

  safety?: StageState;

  ocr?: StageState;

  shot_detection?: StageState;

  transcription?: StageState;

  audio_events?: StageState;

  moment_scoring?: StageState;
}

/**
 * Per-stage pipeline state. Granular per stage rather than one status field because a 3TB
 * scan is killed and resumed constantly, and 'hashed but not yet proxied' has to be a
 * first-class, restartable position.
 */
export interface ProcessingState {
  /**
   * Rollup for UI and query. `quarantined` means the file is unreadable or hostile (zero-
   * byte, truncated, symlink loop) and must never be retried automatically.
   */
  state: ProcessingStateState;

  stages: ProcessingStateStages;
}

export type ProxyRefKind =
  | "thumbnail_512"
  | "preview_2048"
  | "video_proxy_480p"
  | "waveform"
  | "contact_sheet_tile"
  | "audio_wav_16k";

export const ProxyRefKindValues = [
  "thumbnail_512",
  "preview_2048",
  "video_proxy_480p",
  "waveform",
  "contact_sheet_tile",
  "audio_wav_16k",
] as const satisfies readonly ProxyRefKind[];

/**
 * A derived rendition on local disk. Content-addressed like everything else, so a proxy
 * regenerated with the same tool version is recognised as the same artifact.
 */
export interface ProxyRef {
  proxy_id: Blake3Hash;

  kind: ProxyRefKind;

  path: string;

  size?: PixelSize | null;

  byte_size?: number | null;

  /**
   * Tool + settings that produced it. A change here invalidates the proxy without deleting
   * anything.
   */
  generator_version?: string | null;

  /**
   * Present on video_proxy_480p only. The proxy is single-pass and may not be frame-exact
   * against the source, so analysis results measured in proxy time must be mapped back
   * through this sidecar before they can address source timecode.
   */
  frame_index?: FrameIndexSidecar | null;
}

/**
 * Technical quality, cheapest measures first. The classical measures alone reject most of
 * the junk for free (build plan 4.2), so they are required and the learned scores are
 * optional.
 */
export interface QualityScores {
  /**
   * Laplacian-variance derived, normalised. Low means blurred, whether from focus or
   * motion.
   */
  sharpness: Score;

  /**
   * Histogram-derived. Penalises clipped highlights and crushed shadows; 1.0 is a well-
   * distributed histogram.
   */
  exposure: Score;

  noise?: Score | null;

  contrast?: Score | null;

  /** Learned no-reference IQA (MUSIQ/TOPIQ class). */
  technical_iqa?: Score | null;

  /**
   * Aesthetic prior. Explicitly a PRIOR: the ranking engine reweights this per user from
   * PrefEvents, so it must never be treated as ground truth.
   */
  aesthetic?: Score | null;

  composition?: Score | null;

  /**
   * Best face quality in the frame: eyes open, unblurred, forward-facing. Null when there
   * are no faces.
   */
  face_quality?: Score | null;

  /** Default: false. */
  is_black_frame?: boolean;

  /**
   * Lens cap, pocket footage, finger over the lens. Cheap to detect and enormously common
   * on action-camera cards. Default: false.
   */
  is_lens_obstructed?: boolean;
}

export type SafetyAssessmentCategoriesItem =
  | "nudity"
  | "sexual"
  | "violence"
  | "gore"
  | "medical"
  | "document_pii"
  | "unknown";

export const SafetyAssessmentCategoriesItemValues = [
  "nudity",
  "sexual",
  "violence",
  "gore",
  "medical",
  "document_pii",
  "unknown",
] as const satisfies readonly SafetyAssessmentCategoriesItem[];

export interface SafetyAssessment {
  nsfw_score: Confidence;

  /** Default: []. */
  categories?: SafetyAssessmentCategoriesItem[];

  /**
   * Excluded by default from all automated output. The user can override per item; the
   * override is recorded in UserAnnotations, never by mutating this field.
   */
  auto_excluded: boolean;

  threshold_used?: Confidence;
}

export type SourceLocationAdapter =
  | "filesystem"
  | "google_takeout"
  | "icloud_export"
  | "whatsapp"
  | "gopro_card"
  | "dslr_card"
  | "phone_gallery"
  | "insta360"
  | "drone_card"
  | "manual_import";

export const SourceLocationAdapterValues = [
  "filesystem",
  "google_takeout",
  "icloud_export",
  "whatsapp",
  "gopro_card",
  "dslr_card",
  "phone_gallery",
  "insta360",
  "drone_card",
  "manual_import",
] as const satisfies readonly SourceLocationAdapter[];

export interface SourceLocation {
  /**
   * Absolute path as seen at scan time. Local only -- this string never leaves the device
   * and is stripped by the crash-reporter privacy filter.
   */
  path: string;

  /**
   * Stable identifier for the containing volume, so an unplugged external drive is
   * reported as 'offline' rather than 'deleted'.
   */
  volume_id?: string | null;

  /**
   * Which ingest adapter found it. Drives source-specific metadata recovery: Takeout puts
   * the real date in a sidecar JSON, WhatsApp puts it in the filename, and neither has
   * usable EXIF.
   */
  adapter: SourceLocationAdapter;

  /**
   * Companion files: XMP, Takeout's .json, GoPro's .THM/.LRV, Live Photo's paired .MOV.
   * Default: [].
   */
  sidecar_paths?: string[];

  /**
   * Filename as it appeared, kept after any rename because WhatsApp and camera naming
   * conventions are the only date source for a large share of real libraries.
   */
  original_filename?: string | null;

  first_seen_at: Timestamp;

  last_verified_at?: Timestamp | null;

  /**
   * False when the path no longer resolves. The record is retained: losing sight of a file
   * is not permission to forget everything we learned about it (hard rule 7, no silent
   * data loss).
   */
  present: boolean;
}

export type SpanRole = "member" | "assembly";

export const SpanRoleValues = [
  "member",
  "assembly",
] as const satisfies readonly SpanRole[];

export type SpanSpanKind =
  | "gopro_chapter"
  | "dslr_size_split"
  | "insta360_lens_pair"
  | "manual_group";

export const SpanSpanKindValues = [
  "gopro_chapter",
  "dslr_size_split",
  "insta360_lens_pair",
  "manual_group",
] as const satisfies readonly SpanSpanKind[];

export type SpanContinuity = "verified_gapless" | "verified_gap" | "unverified" | "incomplete_set";

export const SpanContinuityValues = [
  "verified_gapless",
  "verified_gap",
  "unverified",
  "incomplete_set",
] as const satisfies readonly SpanContinuity[];

/**
 * Membership in a multi-file recording. Modelled as a role on each record rather than a
 * nested structure so that a chapter can be discovered, hashed and analysed before its
 * siblings have even been walked -- which is exactly what happens on a 400-file GoPro
 * card.
 */
export interface Span {
  /**
   * BLAKE3 over the ordered member media_ids once the set is closed. Before closure, a
   * provisional id derived from the camera's own group identifier (GoPro's file number,
   * e.g. 1234 in GH011234.MP4). On the assembly record this is also the media_id -- the
   * assembly has no bytes to hash, so its members' identity is its identity.
   *
   * CANONICAL ENCODING, because 'BLAKE3 over the ids' does not determine the bytes and
   * three plausible readings gave three different identities: span_id = BLAKE3(
   * concat(member_media_ids in index order) ) where each id contributes its 64 lowercase
   * ASCII hex characters, with NO delimiter, NO length prefix and NO domain separator.
   *
   * The absence of a delimiter is safe rather than lucky: every Blake3Hash is exactly 64
   * hex characters, so the concatenation is fixed-width and therefore prefix-free -- no
   * two different member lists can produce the same byte string. A variable-length
   * encoding would need a delimiter to avoid that, which is where this class of bug
   * usually starts.
   *
   * ORDER IS INDEX ORDER, NOT SORTED ORDER. Chapters are a sequence: GH011234, GH021234,
   * GH031234 concatenate into one recording in that order, and sorting by hash would
   * scramble a timeline. The assembly's identity therefore changes if the chapters are
   * reordered, which is correct -- a different order is a different recording.
   *
   * Codex raised this (issue #26) after finding that the golden fixture's span_id matched
   * none of the plausible readings. It matched none of them because it had been written by
   * hand rather than computed, so the fixture was not testing the identity at all.
   * contracts/tests recomputes it now.
   */
  span_id: Blake3Hash;

  /**
   * `member` is a physical file on disk and always sits on an asset_kind of physical_file.
   * `assembly` is the virtual record representing the concatenated recording; it is always
   * asset_kind virtual_assembly, carries byte_size 0, no sources and no proxies, and is
   * what MomentRecords and EDL clips reference so a cut can cross a chapter boundary
   * without the planner knowing chapters exist.
   */
  role: SpanRole;

  span_kind: SpanSpanKind;

  /** 0-based position within the recording. Required on members, null on the assembly. */
  index?: number | null;

  member_count?: number | null;

  /** Ordered member ids. Populated on the assembly record only. Default: []. */
  member_media_ids?: Blake3Hash[];

  /**
   * Where this member starts within the assembly's timeline. Lets a MomentRecord on the
   * assembly be resolved back to (file, timecode) at render without re-probing every
   * chapter.
   */
  offset_in_span?: RationalTime | null;

  /**
   * Whether the chapters were verified to be gapless. Cameras occasionally drop a frame at
   * the split; the renderer must know before it concatenates. Default: "unverified".
   */
  continuity?: SpanContinuity;
}

export type StageStateStatus =
  | "pending"
  | "running"
  | "done"
  | "failed"
  | "skipped"
  | "not_applicable";

export const StageStateStatusValues = [
  "pending",
  "running",
  "done",
  "failed",
  "skipped",
  "not_applicable",
] as const satisfies readonly StageStateStatus[];

export interface StageState {
  status: StageStateStatus;

  /** Default: 0. */
  attempts?: number;

  completed_at?: Timestamp | null;

  job_id?: Blake3Hash | null;

  /**
   * Why a stage was skipped. Required reading for 'no silent anything': a skipped stage
   * must be explicable without reading logs.
   */
  skip_reason?: string | null;

  last_error?: ErrorInfo | null;
}

export interface UserAnnotations {
  /** Default: false. */
  favorite?: boolean;

  /** Default: false. */
  hidden?: boolean;

  rating?: number | null;

  /** Default: []. */
  tags?: string[];

  caption?: string | null;
}

export type VideoPropertiesRotationDeg = 0 | 90 | 180 | 270;

export const VideoPropertiesRotationDegValues = [
  0,
  90,
  180,
  270,
] as const satisfies readonly VideoPropertiesRotationDeg[];

/**
 * Exact frame rate as a rational. 30000/1001 must survive as such; storing 29.97 as a
 * float and reconstructing it later is how beat-locked cuts drift.
 */
export interface VideoPropertiesFrameRate {
  numerator: number;

  denominator: number;
}

export interface VideoProperties {
  stored_size?: PixelSize | null;

  oriented_size: PixelSize;

  /**
   * Rotation from the container's display matrix. Phone video is almost always stored
   * landscape with a rotation flag. Default: 0.
   */
  rotation_deg?: VideoPropertiesRotationDeg;

  duration: RationalTime;

  /**
   * Exact frame rate as a rational. 30000/1001 must survive as such; storing 29.97 as a
   * float and reconstructing it later is how beat-locked cuts drift.
   */
  frame_rate: VideoPropertiesFrameRate;

  /**
   * True for most phone video. A VFR source must be conformed before frame-accurate
   * cutting, and the renderer needs to be told, not surprised. Default: false.
   */
  is_variable_frame_rate?: boolean;

  /**
   * Embedded SMPTE timecode track start, when present. Carried through to OTIO so a
   * professional round-trip lands on the right frame.
   */
  start_timecode?: RationalTime | null;

  video_codec?: string | null;

  bit_rate?: number | null;

  color_primaries?: string | null;

  /**
   * HLG or PQ here means HDR footage, which changes both the enhancement plan and the
   * encode profile.
   */
  transfer_characteristics?: string | null;

  /** Default: []. */
  audio_streams?: AudioStream[];
}

export type MediaRecordAssetKind = "physical_file" | "virtual_assembly";

export const MediaRecordAssetKindValues = [
  "physical_file",
  "virtual_assembly",
] as const satisfies readonly MediaRecordAssetKind[];

export type MediaRecordKind =
  | "image"
  | "video"
  | "live_photo"
  | "motion_photo"
  | "audio"
  | "sidecar"
  | "unknown";

export const MediaRecordKindValues = [
  "image",
  "video",
  "live_photo",
  "motion_photo",
  "audio",
  "sidecar",
  "unknown",
] as const satisfies readonly MediaRecordKind[];

export type MediaRecordFileFormat =
  | "jpeg"
  | "png"
  | "heic"
  | "heif"
  | "avif"
  | "webp"
  | "tiff"
  | "dng"
  | "cr2"
  | "cr3"
  | "nef"
  | "arw"
  | "raf"
  | "orf"
  | "rw2"
  | "gif"
  | "bmp"
  | "mp4"
  | "mov"
  | "avi"
  | "mkv"
  | "webm"
  | "m4v"
  | "mts"
  | "3gp"
  | "insv"
  | "wav"
  | "mp3"
  | "m4a"
  | "aac"
  | "flac"
  | "unknown";

export const MediaRecordFileFormatValues = [
  "jpeg",
  "png",
  "heic",
  "heif",
  "avif",
  "webp",
  "tiff",
  "dng",
  "cr2",
  "cr3",
  "nef",
  "arw",
  "raf",
  "orf",
  "rw2",
  "gif",
  "bmp",
  "mp4",
  "mov",
  "avi",
  "mkv",
  "webm",
  "m4v",
  "mts",
  "3gp",
  "insv",
  "wav",
  "mp3",
  "m4a",
  "aac",
  "flac",
  "unknown",
] as const satisfies readonly MediaRecordFileFormat[];

/**
 * The identity of one media file and everything the analysis layer has learned about it.
 * One record per physical file, always -- a GoPro chapter set is N member records plus one
 * assembly record, never a record that pretends four files are one.
 */
export interface MediaRecord {
  schema_version: SchemaVersion;

  /**
   * Primary key. For a physical_file this is the BLAKE3 of the file's bytes; for a
   * virtual_assembly it is the span_id, a BLAKE3 over the ordered member media_ids.
   * Content-addressed either way, so re-importing is a no-op and every downstream job
   * keyed on it is idempotent.
   */
  media_id: Blake3Hash;

  /**
   * Whether this record describes bytes on disk or a virtual assembly of other records.
   * Required and explicit: the identity, size and source rules differ between the two, and
   * a reader must never have to infer which set applies.
   */
  asset_kind: MediaRecordAssetKind;

  /**
   * Top-level media class. `live_photo` and `motion_photo` are their own kind rather than
   * image-with-a-video because the still and the motion track are separately renderable
   * and separately rankable.
   */
  kind: MediaRecordKind;

  byte_size: number;

  mime_type?: string | null;

  /**
   * Container as detected from content, not from the extension. A .jpg that is actually
   * HEIC is common in exports and must not be trusted by extension.
   */
  file_format?: MediaRecordFileFormat | null;

  /**
   * Every place on disk these exact bytes have been seen. Plural because deduplication by
   * content is the whole point: one record, many paths. A physical_file always has at
   * least one; a virtual_assembly always has none, because its members own the paths and
   * duplicating one of them here would make the assembly look like a file that can be
   * opened.
   */
  sources: SourceLocation[];

  /**
   * Set membership for footage split across multiple files. Present on GoPro chaptered
   * MP4s (GH011234.MP4, GH021234.MP4, ...), DSLR 4GB-limit splits, and Insta360 .insv
   * sets. Null for the overwhelming majority of files.
   */
  span?: Span | null;

  capture: Capture;

  image?: ImageProperties | null;

  video?: VideoProperties | null;

  perceptual?: PerceptualFingerprint | null;

  /**
   * Derived renditions. Analysis reads only these; the original is opened exactly twice in
   * the file's life, once to make these and once at final render. Default: [].
   */
  proxies?: ProxyRef[];

  processing: ProcessingState;

  quality?: QualityScores | null;

  content?: ContentAnalysis | null;

  /**
   * Denormalised face summary. The authoritative per-face data lives in FaceRecord; this
   * block exists so the library grid can filter 'photos with 3+ people' without joining.
   */
  faces?: FaceSummary | null;

  dedupe?: DedupeMembership | null;

  exclusion?: ExclusionState;

  user?: UserAnnotations;

  /**
   * Provenance for every score on this record. Scores reference these by run_id. Default:
   * [].
   */
  model_runs?: ModelRun[];

  first_seen_at?: Timestamp;

  updated_at?: Timestamp;
}

export type AudioFeaturesEventsItemLabel =
  | "laughter"
  | "cheering"
  | "applause"
  | "crying"
  | "singing"
  | "shouting"
  | "splash"
  | "music"
  | "speech"
  | "wind"
  | "silence"
  | "engine"
  | "animal"
  | "fireworks"
  | "other";

export const AudioFeaturesEventsItemLabelValues = [
  "laughter",
  "cheering",
  "applause",
  "crying",
  "singing",
  "shouting",
  "splash",
  "music",
  "speech",
  "wind",
  "silence",
  "engine",
  "animal",
  "fireworks",
  "other",
] as const satisfies readonly AudioFeaturesEventsItemLabel[];

export interface AudioFeaturesEventsItem {
  label: AudioFeaturesEventsItemLabel;

  confidence: Confidence;

  time?: RationalTime | null;
}

export interface AudioFeatures {
  loudness_lufs?: number | null;

  speech_ratio?: Unit | null;

  music_ratio?: Unit | null;

  /**
   * Wind and handling noise. High values are why a visually perfect action shot may still
   * need its ambient ducked to nothing.
   */
  noise_ratio?: Unit | null;

  /**
   * Detected audio events with their own confidences. Laughter and cheering are among the
   * strongest emotional-peak signals available to a local model. Default: [].
   */
  events?: AudioFeaturesEventsItem[];

  embedding?: VectorRef | null;
}

export type EliminationReasonsItem =
  | "shake"
  | "blown_exposure"
  | "crushed_exposure"
  | "black_frame"
  | "lens_obstructed"
  | "no_motion"
  | "too_short"
  | "no_subject"
  | "duplicate_footage"
  | "wind_noise_dominant"
  | "below_score_floor"
  | "user_rejected";

export const EliminationReasonsItemValues = [
  "shake",
  "blown_exposure",
  "crushed_exposure",
  "black_frame",
  "lens_obstructed",
  "no_motion",
  "too_short",
  "no_subject",
  "duplicate_footage",
  "wind_noise_dominant",
  "below_score_floor",
  "user_rejected",
] as const satisfies readonly EliminationReasonsItem[];

export type EliminationStage = "classical" | "local_model" | "fusion" | "planner" | "user";

export const EliminationStageValues = [
  "classical",
  "local_model",
  "fusion",
  "planner",
  "user",
] as const satisfies readonly EliminationStage[];

/**
 * Elimination-first is the biggest cost and quality lever in the system, so its result is
 * a required, structured field rather than the absence of a record.
 */
export interface Elimination {
  eliminated: boolean;

  /** Default: []. */
  reasons?: EliminationReasonsItem[];

  /**
   * How far the moment got before being dropped. `classical` eliminations are the free
   * ones and should account for the overwhelming majority.
   */
  stage?: EliminationStage | null;
}

/**
 * The fused feature stream over the moment's window. These are the inputs to score fusion,
 * and they are exactly what a PrefEvent captures as decision context -- which is why they
 * are named, bounded and stable rather than an opaque vector.
 */
export interface MomentFeatures {
  /**
   * Mean optical-flow magnitude, normalised. High is action; near-zero over a long window
   * is tripod dead time and gets eliminated.
   */
  motion_energy?: Unit | null;

  motion_peak?: Unit | null;

  /**
   * Camera instability distinct from subject motion. The single most common reason
   * handheld footage is unusable.
   */
  shake?: Unit | null;

  /**
   * Low when the camera is hunting exposure, e.g. walking from indoors into sun. Such a
   * window looks bad no matter how good the content is.
   */
  exposure_stability?: Unit | null;

  sharpness?: Unit | null;

  /** Fraction of frames in the window containing at least one face. */
  face_presence?: Unit | null;

  max_face_area_ratio?: Unit | null;

  smile_intensity?: Unit | null;

  audio?: AudioFeatures | null;

  /**
   * SigLIP embedding of the moment's representative keyframe. Drives diversity
   * constraints, so two moments that look alike cannot both make the cut.
   */
  visual_embedding?: VectorRef | null;

  /**
   * Distance from everything already selected. This is what stops a reel being six near-
   * identical drone shots.
   */
  novelty?: Unit | null;

  /**
   * Best single frame in the window, used as the contact-sheet tile shown to the frontier
   * model.
   */
  representative_frame_time?: RationalTime | null;
}

export type MomentScoresSource =
  | "local_fusion"
  | "local_learned"
  | "tier2_vlm"
  | "tier3_model"
  | "user_override";

export const MomentScoresSourceValues = [
  "local_fusion",
  "local_learned",
  "tier2_vlm",
  "tier3_model",
  "user_override",
] as const satisfies readonly MomentScoresSource[];

/**
 * Fused judgements. `moment_score` is the only required one; the rest are the
 * decomposition that makes it explainable and per-user reweightable.
 */
export interface MomentScores {
  /**
   * Overall keepworthiness. v1 is a hand-weighted linear fusion because a transparent,
   * tunable model beats an opaque one until PrefEvents exist to train on.
   */
  moment_score: Score;

  technical?: Score | null;

  /**
   * Fitness for the first second of a reel, where retention is won or lost. Rewards
   * immediate motion or an immediate face, and punishes slow builds.
   */
  hook_potential?: Score | null;

  /**
   * How much this feels like a moment rather than merely looking like one. Local features
   * approximate it via laughter, smiles and motion onsets; the frontier model is the one
   * that can actually tell 'child sees the ocean' from 'child near ocean', and when it has
   * ruled, `source` says so.
   */
  emotional_peak?: Score | null;

  /** Contribution to a story beat, only ever populated by a Tier 2/3 reasoning pass. */
  narrative_value?: Score | null;

  /**
   * Which tier produced the judgement scores. Never let a frontier-model opinion be
   * mistaken for a local measurement. Default: "local_fusion".
   */
  source?: MomentScoresSource;

  /**
   * Which weight set produced moment_score. Per-user reweighting means the same features
   * legitimately yield different scores for different people, and the score is meaningless
   * without knowing which weights applied.
   */
  fusion_weights_version?: string | null;
}

/**
 * Hard bounds on trimming. `speech_safe_*` are the ones that make the no-mid-word
 * guarantee exact: they are derived from word-level timestamps, not from voice-activity
 * guesses.
 */
export interface SafeTrim {
  earliest_in: RationalTime;

  latest_out: RationalTime;

  /**
   * Earliest in-point that does not land inside a spoken word. Null when the moment
   * contains no speech, in which case earliest_in applies.
   */
  speech_safe_in?: RationalTime | null;

  speech_safe_out?: RationalTime | null;

  /** Below this the moment reads as a flash frame rather than a shot. */
  min_duration?: RationalTime | null;

  /**
   * True when the audio meaningfully continues past the visual out-point -- a laugh that
   * lands after the cut. The renderer honours this with an audio-only extension (an
   * L-cut), which is a decision that must live in the plan. Default: false.
   */
  preserve_audio_tail?: boolean;
}

export type SnapPointKind =
  | "shot_boundary"
  | "motion_onset"
  | "motion_offset"
  | "audio_onset"
  | "speech_gap"
  | "speech_start"
  | "speech_end"
  | "subject_entry"
  | "subject_exit"
  | "impact"
  | "scene_brightness_change";

export const SnapPointKindValues = [
  "shot_boundary",
  "motion_onset",
  "motion_offset",
  "audio_onset",
  "speech_gap",
  "speech_start",
  "speech_end",
  "subject_entry",
  "subject_exit",
  "impact",
  "scene_brightness_change",
] as const satisfies readonly SnapPointKind[];

export type SnapPointCutDirection = "in" | "out" | "both";

export const SnapPointCutDirectionValues = [
  "in",
  "out",
  "both",
] as const satisfies readonly SnapPointCutDirection[];

/**
 * A time at which cutting is defensible, with a reason. The planner may only place cuts on
 * snap points; that constraint is what makes 'beat-alignment error < 50ms' and 'no mid-
 * word cuts' testable properties of a plan rather than emergent behaviour.
 */
export interface SnapPoint {
  /**
   * Source timecode. Exact rational, never rounded to milliseconds -- the 50ms beat-
   * alignment gate has no headroom to spare on rounding.
   */
  time: RationalTime;

  kind: SnapPointKind;

  /**
   * How pronounced the boundary is. Cutting on a weak onset is worse than cutting 40ms
   * later on a strong one.
   */
  strength: Unit;

  confidence?: Confidence | null;

  /**
   * Whether this point is usable as an in-point, an out-point, or both. A motion onset is
   * a great in-point and a poor out-point; encoding that asymmetry stops the planner
   * making technically-legal, visually-wrong cuts. Default: "both".
   */
  cut_direction?: SnapPointCutDirection;
}

export interface TranscriptSegmentWordsItem {
  word: string;

  start: RationalTime;

  end: RationalTime;

  confidence?: Confidence | null;
}

/**
 * Speech inside the moment, with word timing. Word timestamps are not a nicety: they are
 * the mechanism behind speech-aware trimming and the mid-word-cut quality gate.
 */
export interface TranscriptSegment {
  text: string;

  /**
   * BCP-47. Indian-language libraries are a first-class target, so this is required rather
   * than assumed English.
   */
  language: string;

  /** Default: []. */
  words?: TranscriptSegmentWordsItem[];

  /**
   * Diarisation labels, mapped to person ids where a confident face-voice association
   * exists. Default: [].
   */
  speaker_ids?: string[];

  /**
   * Someone says a name. A strong and cheap signal that a window matters to the family it
   * belongs to. Default: false.
   */
  contains_name_mention?: boolean;
}

/**
 * Who is present, resolved through the automated-output face gate. A person named here has
 * passed the precision bar; uncertain faces contribute to face_presence but not to this
 * list.
 */
export interface MomentRecordPeople {
  /** Default: []. */
  person_ids?: Uuid[];

  /** Default: []. */
  face_track_ids?: Uuid[];

  /** Default: 0. */
  unidentified_face_count?: number;
}

/**
 * A scored time interval in a video: what happens in it, how good it is, and where inside
 * it a cut is allowed to land.
 */
export interface MomentRecord {
  schema_version: SchemaVersion;

  /**
   * BLAKE3 over (media_id, source_range, scorer model_id+version). Rescoring with a new
   * model yields new ids, so an EDL always points at the exact moment definition it was
   * planned against.
   */
  moment_id: Blake3Hash;

  /**
   * MediaRecord this moment lives in. For chaptered footage this is the span ASSEMBLY id,
   * so a moment may legally straddle a chapter boundary that the planner never has to know
   * about.
   */
  media_id: Blake3Hash;

  /**
   * The moment's extent in SOURCE timecode -- already mapped back through the proxy frame
   * index. Everything downstream, including the EDL, addresses source time; proxy time
   * never escapes the analysis layer.
   */
  source_range: TimeRange;

  /**
   * The same interval in proxy time, retained so a re-score can be run against the cached
   * proxy without redoing the mapping.
   */
  proxy_range?: TimeRange | null;

  /**
   * Shot this moment sits inside, from TransNetV2 boundary detection. A moment never
   * crosses a shot boundary -- crossing one is a cut, and cuts belong to the planner.
   */
  shot_id?: Slug | null;

  features?: MomentFeatures | null;

  scores: MomentScores;

  /**
   * Certified cut positions inside (and at the edges of) this moment. Ordered by time. A
   * beat-locked reel cut is the intersection of a beat grid entry and a snap point of kind
   * motion_onset or audio_onset -- that intersection is what makes a cut feel deliberate
   * rather than arbitrary. Default: [].
   */
  snap_points?: SnapPoint[];

  /**
   * The bounds a planner may trim to without damaging the moment. Absent only when speech
   * and motion analysis have not run.
   */
  safe_trim?: SafeTrim | null;

  elimination: Elimination;

  /**
   * Who is present, resolved through the automated-output face gate. A person named here
   * has passed the precision bar; uncertain faces contribute to face_presence but not to
   * this list. Default: {}.
   */
  people?: MomentRecordPeople;

  transcript?: TranscriptSegment | null;

  /** Default: []. */
  model_runs?: ModelRun[];

  created_at?: Timestamp;
}

export interface Alternative {
  subject_id: string;

  /**
   * True for the option that won. Included in the list rather than only in Subject so a
   * single array fully describes the comparison.
   */
  chosen: boolean;

  /**
   * Position as shown, 0-based. Position bias is real and strong; a model trained without
   * it will learn that the top-left option is beautiful.
   */
  presented_rank?: number | null;

  /**
   * The score the system assigned at presentation time. The gap between this and the
   * human's choice is the error signal.
   */
  presented_score?: Unit | null;

  /**
   * Features for this alternative, in the same feature_set_id as the subject's. Required
   * for pairwise training; a comparison where only the winner has features cannot be
   * learned from.
   */
  feature_vector?: DenseFeatures | null;
}

export type DecisionKind =
  | "kept"
  | "rejected"
  | "reordered"
  | "recropped"
  | "variant_picked"
  | "hero_swapped"
  | "replaced"
  | "person_confirmed"
  | "person_rejected"
  | "moment_trimmed"
  | "music_changed"
  | "enhancement_accepted"
  | "enhancement_rejected"
  | "page_reordered"
  | "exported"
  | "printed"
  | "shared"
  | "deleted"
  | "favorited"
  | "revision_requested";

export const DecisionKindValues = [
  "kept",
  "rejected",
  "reordered",
  "recropped",
  "variant_picked",
  "hero_swapped",
  "replaced",
  "person_confirmed",
  "person_rejected",
  "moment_trimmed",
  "music_changed",
  "enhancement_accepted",
  "enhancement_rejected",
  "page_reordered",
  "exported",
  "printed",
  "shared",
  "deleted",
  "favorited",
  "revision_requested",
] as const satisfies readonly DecisionKind[];

export type DecisionSurface =
  | "culling_ui"
  | "library_grid"
  | "album_review"
  | "spread_editor"
  | "variant_picker"
  | "person_labeling"
  | "project_editor"
  | "share_flow"
  | "checkout"
  | "concierge_review";

export const DecisionSurfaceValues = [
  "culling_ui",
  "library_grid",
  "album_review",
  "spread_editor",
  "variant_picker",
  "person_labeling",
  "project_editor",
  "share_flow",
  "checkout",
  "concierge_review",
] as const satisfies readonly DecisionSurface[];

export interface Decision {
  /**
   * What the human did. Note that `exported`, `printed` and `shared` are included: acting
   * on an output is the strongest positive signal available, far stronger than a thumbs-
   * up, and it costs nothing to capture.
   */
  kind: DecisionKind;

  /**
   * Where in the product it happened. The same `kept` means different things in a culling
   * sweep and in a final album review, and a model that cannot tell them apart will learn
   * the average of two different tastes.
   */
  surface: DecisionSurface;

  /**
   * True when the human deliberately expressed a preference. False for inferred signals
   * such as dwelling on a frame or scrolling past. Inferred events are far weaker evidence
   * and must be weighted accordingly rather than mixed in silently.
   */
  explicit: boolean;

  /** How much to trust an inferred signal. Null for explicit ones, which need no discount. */
  confidence?: Confidence | null;

  /**
   * Set when the human undid this within the session. A reversed decision is training data
   * about the reversal, not about the original action, and must never be fed in as a plain
   * positive.
   */
  reversed_at?: Timestamp | null;
}

export type DecisionContextTask =
  | "cull"
  | "album"
  | "reel"
  | "film"
  | "person_labeling"
  | "search"
  | "share"
  | "concierge";

export const DecisionContextTaskValues = [
  "cull",
  "album",
  "reel",
  "film",
  "person_labeling",
  "search",
  "share",
  "concierge",
] as const satisfies readonly DecisionContextTask[];

export type DecisionContextDeviceClass = "desktop" | "laptop" | "tablet" | "phone";

export const DecisionContextDeviceClassValues = [
  "desktop",
  "laptop",
  "tablet",
  "phone",
] as const satisfies readonly DecisionContextDeviceClass[];

export interface DecisionContext {
  /**
   * What the human was trying to accomplish. Taste is task-relative: a photo rejected for
   * a print album may be perfectly good for a reel.
   */
  task: DecisionContextTask;

  project_id?: Uuid | null;

  /**
   * Groups decisions made in one sitting. Decisions late in a long session are noisier --
   * fatigue is measurable and worth modelling rather than ignoring.
   */
  session_id?: Uuid | null;

  /** How many options existed in total, which may exceed the number displayed. */
  candidate_set_size?: number | null;

  presented_count?: number | null;

  subject_presented_rank?: number | null;

  /**
   * Time from presentation to decision. A 400ms rejection and a 30s agonised one are
   * different strengths of evidence.
   */
  deliberation_ms?: number | null;

  position_in_session?: number | null;

  /**
   * Which ranking model produced the scores the human was reacting to. Without it, an
   * event cannot be attributed to the model that generated the ordering, and offline
   * evaluation becomes guesswork.
   */
  ranker_version?: string | null;

  fusion_weights_version?: string | null;

  /**
   * Screen size changes what a person can even perceive; a crop judged on a phone is not a
   * crop judged on a 27-inch display.
   */
  device_class?: DecisionContextDeviceClass | null;
}

/**
 * The before and after of an edit. A re-crop is the richest signal the system ever
 * receives: the human has not merely judged, they have demonstrated the correct answer.
 */
export interface DecisionDelta {
  crop_before?: NormalizedBox | null;

  crop_after?: NormalizedBox | null;

  position_before?: number | null;

  position_after?: number | null;

  trim_before?: TimeRange | null;

  trim_after?: TimeRange | null;

  replaced_with_subject_id?: string | null;

  /**
   * For revision_requested: the human's own words, such as 'more of her' or 'less drone'.
   * Free text, local-only, and stripped before any export -- it can contain names.
   */
  instruction_text?: string | null;
}

export interface DenseFeatures {
  feature_set_id: Slug;

  values: number[];
}

/**
 * Whether confirmed people were present, and how many. Who is in a photo is often the
 * entire reason it was kept, and this captures that without naming anybody in an
 * exportable record -- ids stay local, counts travel.
 */
export interface FeatureContextPersonContext {
  /** Default: 0. */
  confirmed_person_count?: number;

  /**
   * True when someone the user has marked as important is present. The single strongest
   * predictor of a keep decision in family libraries. Default: false.
   */
  includes_priority_person?: boolean;

  /**
   * Local-only. Stripped by the anonymisation pass before any event leaves the device.
   * Default: [].
   */
  person_ids?: Uuid[];
}

/**
 * The subject's features as they stood at decision time. Both a named map and an optional
 * dense vector: the named map keeps the data interpretable and debuggable for years, the
 * dense vector keeps training cheap. The named map is the source of truth.
 */
export interface FeatureContext {
  /**
   * Names the exact ordered feature list in use. Bumped whenever a feature is added,
   * removed or redefined. A dense vector is meaningless without it.
   */
  feature_set_id: Slug;

  /**
   * Feature name to value. Values are numeric and normalised. Deliberately an open map
   * rather than a fixed property list, because the feature set is expected to grow -- but
   * every key must be declared in the named feature_set_id, and the eval harness checks
   * that.
   */
  named: Record<string, number>;

  dense?: DenseFeatures | null;

  /**
   * Embedding references, not the floats themselves for local storage, and resolved to
   * values only when a training export is built under an explicit consent scope. An
   * embedding is derived from pixels but is not pixels; treating it as sensitive anyway is
   * the conservative reading of the privacy promise. Default: [].
   */
  embeddings?: VectorRef[];

  /**
   * The system's own scores as presented. Frozen: never recomputed against a later model.
   * Default: {}.
   */
  scores_at_decision?: Record<string, number>;

  /**
   * Whether confirmed people were present, and how many. Who is in a photo is often the
   * entire reason it was kept, and this captures that without naming anybody in an
   * exportable record -- ids stay local, counts travel.
   */
  person_context?: FeatureContextPersonContext | null;
}

/**
 * Who this event belongs to and how far it is allowed to travel. Present on every event
 * because the decision about sharing is made once, at write time, and never re-litigated
 * by whatever code later reads the record.
 */
export interface PrivacyEnvelope {
  /**
   * A per-install salted hash, not a user id and not an email. Sufficient to group one
   * person's events for a per-user model, insufficient to identify them.
   */
  user_pseudonym: Blake3Hash;

  /**
   * Whether this event may join the anonymised global training pool. Defaults to false in
   * practice: the user opts in, and the absence of a decision is not consent.
   */
  shareable_for_global_model: boolean;

  /**
   * Required when shareable_for_global_model is true. Same rule as everywhere else --
   * nothing leaves without a ledger entry.
   */
  consent?: ConsentRef | null;

  /**
   * True when the record still holds person ids or free text. Such an event must pass the
   * anonymisation step before export; this flag is what makes that check cheap and
   * unambiguous. Default: true.
   */
  contains_local_identifiers?: boolean;

  /** Which redaction pass was applied. Set once the event has been stripped for export. */
  anonymization_version?: string | null;
}

export type SubjectSubjectType =
  | "media"
  | "moment"
  | "face"
  | "person"
  | "placement"
  | "spread"
  | "page"
  | "edl_variant"
  | "album"
  | "enhancement_op"
  | "music_cue";

export const SubjectSubjectTypeValues = [
  "media",
  "moment",
  "face",
  "person",
  "placement",
  "spread",
  "page",
  "edl_variant",
  "album",
  "enhancement_op",
  "music_cue",
] as const satisfies readonly SubjectSubjectType[];

/** What was decided about, and crucially what it was decided AGAINST. */
export interface Subject {
  subject_type: SubjectSubjectType;

  /**
   * Content hash for content-addressed subjects, uuid or slug otherwise. Kept as a string
   * so one field serves every subject type.
   */
  subject_id: string;

  /**
   * The other options on screen when the decision was made, each with the score it was
   * presented with. This is what converts an outcome into a pairwise preference, and it is
   * the single most valuable field in the record. Empty only for decisions with genuinely
   * no alternatives, such as favouriting one photo in a grid. Default: [].
   */
  alternatives?: Alternative[];

  /** Containing entity: the album a placement is on, the reel a variant belongs to. */
  parent_id?: string | null;
}

/**
 * One human reaction, captured with the feature context that existed at the moment of the
 * decision.
 */
export interface PrefEvent {
  schema_version: SchemaVersion;

  event_id: Uuid;

  occurred_at: Timestamp;

  decision: Decision;

  subject: Subject;

  context: DecisionContext;

  features: FeatureContext;

  /**
   * What actually changed, for decisions that are an edit rather than a choice: the crop
   * before and after, the position before and after. The edit itself is the label.
   */
  delta?: DecisionDelta | null;

  privacy: PrivacyEnvelope;

  /**
   * Structurally false, always. Present as a field rather than as an unwritten rule so
   * that any pipeline stage, any reviewer, and any test can assert the privacy property
   * directly on the record.
   */
  pixel_data_present: false;
}

export interface ClassScores {
  explicit: Unit;

  suggestive: Unit;

  medical_or_artistic: Unit;
}

export type ClassifierPinLoadMode = "release" | "development";

export const ClassifierPinLoadModeValues = [
  "release",
  "development",
] as const satisfies readonly ClassifierPinLoadMode[];

/**
 * Which model produced these verdicts. A verdict from a model you cannot identify is not
 * evidence, and a verdict produced under a different config is a verdict about a different
 * decision boundary -- score_threshold 0.3 and 0.5 are different classifiers to every
 * consumer.
 */
export interface ClassifierPin {
  model: ModelRef;

  ran_at: Timestamp;

  /**
   * Which gate the host was running under. A verdict produced by a DEVELOPMENT-mode host
   * -- unpinned weights, unverified licence -- must never clear a real publication, and a
   * verifier serving a release sink must refuse it. Recorded rather than assumed, because
   * 'we were only testing' is how unverified weights reach production.
   */
  load_mode?: ClassifierPinLoadMode;
}

/**
 * The aggregate, derived from `items` and recomputed by every verifier rather than
 * trusted. It is stored so a rejected publication can be explained without re-running
 * anything -- not so a reader can skip checking the items.
 */
export interface ClearanceDecision {
  /**
   * True only when EVERY item is `cleared`, or is `blocked` with a valid override for this
   * sink. One indeterminate item denies the whole publication -- a book is printed as a
   * unit and a share is published as a unit, so partial clearance is not a state either
   * can be in.
   */
  cleared_for_publication: boolean;

  item_count: number;

  cleared_count: number;

  blocked_count: number;

  indeterminate_count: number;

  denied_reason?: string | null;
}

export type ItemVerdictVerdict = "cleared" | "blocked" | "indeterminate";

export const ItemVerdictVerdictValues = [
  "cleared",
  "blocked",
  "indeterminate",
] as const satisfies readonly ItemVerdictVerdict[];

export type ItemVerdictIndeterminateReason =
  | "no_result"
  | "model_unavailable"
  | "model_unloadable"
  | "load_gate_denied"
  | "config_digest_mismatch"
  | "inference_error"
  | "inference_timeout"
  | "evidence_stale"
  | "verifier_exception";

export const ItemVerdictIndeterminateReasonValues = [
  "no_result",
  "model_unavailable",
  "model_unloadable",
  "load_gate_denied",
  "config_digest_mismatch",
  "inference_error",
  "inference_timeout",
  "evidence_stale",
  "verifier_exception",
] as const satisfies readonly ItemVerdictIndeterminateReason[];

/** One media id's clearance, bound to the exact bytes that were classified. */
export interface ItemVerdict {
  media_id: Blake3Hash;

  /**
   * The PROXY the classifier actually saw. Not the media id: a proxy can be regenerated --
   * a better decoder, a corrected orientation, a different size -- and a verdict about the
   * old proxy is not evidence about the new one. A verifier must confirm this matches the
   * proxy the publication is built from, or treat the verdict as stale, which is
   * indeterminate, which blocks.
   */
  evidence_id: Blake3Hash;

  /**
   * `cleared` is the ONLY value that permits automatic publication.
   *
   * `blocked` means the classifier scored above a threshold. It may be overridden per item
   * by a human, because the classifier does not get a veto over a parent's judgement about
   * their own family.
   *
   * `indeterminate` means nobody knows: no result row, model unavailable or unloadable,
   * load-gate denial, config digest mismatch, inference error or timeout, or evidence that
   * no longer matches. It may NOT be overridden by anything, because 'nobody checked' is
   * not a decision somebody made.
   */
  verdict: ItemVerdictVerdict;

  /**
   * Per-class probabilities, when the classifier ran. Null on `indeterminate` -- an
   * indeterminate verdict with scores attached is a contradiction, and the conditional
   * below rejects it.
   */
  scores?: ClassScores | null;

  /**
   * Why nobody knows. Required on `indeterminate`, because 'blocked for an unknown reason'
   * is unactionable and the remedies differ completely: a missing model needs installing,
   * a stale evidence id needs re-running, a digest mismatch needs investigating.
   */
  indeterminate_reason?: ItemVerdictIndeterminateReason | null;

  /**
   * A human decision to publish despite a `blocked` verdict. Permitted ONLY on `blocked`;
   * the conditional below refuses it on `indeterminate`, which is the single most
   * important rule in this file.
   */
  override?: Override | null;
}

/**
 * A recorded human decision. Attributable on purpose: an override that nobody owns is a
 * bypass.
 */
export interface Override {
  decided_at: Timestamp;

  /**
   * Local user identifier. Never a service account, never a config value -- a machine
   * cannot consent on a person's behalf about their own photographs.
   */
  decided_by: string;

  /**
   * `item_and_sink` is the only value, deliberately. There is no 'always allow this photo'
   * and no 'always allow this class': a decision to print a photo in a private family book
   * is not a decision to publish it, and the whole design fails if an override can outlive
   * the publication it was made for.
   */
  scope: "item_and_sink";

  note?: string | null;
}

/**
 * The decision boundaries actually applied, per class. Recorded rather than referenced
 * because the config can change underneath a stored verdict, and a verdict whose threshold
 * you cannot reconstruct cannot be re-audited.
 *
 * The classes are separate on purpose. Collapsing them into one 'nsfw' bit produces the
 * two classic failures: a breastfeeding photo or a post-surgery record treated as
 * pornography, and a bikini holiday photo treated as safe for a public share. A family
 * library contains all three, and the right handling differs for each.
 */
export interface Thresholds {
  explicit: Unit;

  suggestive: Unit;

  medical_or_artistic: Unit;
}

export type SafetyClearanceSink = "print" | "share" | "frontier_egress" | "local_export";

export const SafetyClearanceSinkValues = [
  "print",
  "share",
  "frontier_egress",
  "local_export",
] as const satisfies readonly SafetyClearanceSink[];

/**
 * The manifest that must exist, verify, and be COMPLETE before anything leaves the device
 * or reaches a printer. Designed by Codex on issue #21; this is the contract form of it.
 *
 * WHY A MANIFEST AND NOT A FIELD ON EACH RECORD
 *
 * A per-record `is_safe` flag is checked at some point and acted on at another, and the
 * gap between them is where the failure lives: the selection changes, a photo is swapped
 * in, and the check that passed was about a different set. So clearance is bound to an
 * EXACT publication -- this sink, these media ids, in this order, under this classifier
 * and this config digest -- and hashed. The renderer or service verifies the hash against
 * the inputs it is ACTUALLY about to publish, inside the same operation that creates the
 * export. There is no window in which the checked set and the published set can differ.
 *
 * THE RULE THAT MATTERS MOST
 *
 * Absence is `indeterminate`, and indeterminate BLOCKS. A missing verdict, an unloadable
 * classifier, a config digest mismatch, an inference timeout, a stale verdict for a proxy
 * that has since changed, a row that simply is not there -- all of them are indeterminate.
 * Only `cleared` proceeds.
 *
 * This is the opposite of how safety checks usually fail. The common shape is a check that
 * silently no-ops when its model is missing, so everything downstream reads the absence as
 * a pass. This project has already shipped one gate with exactly that defect (a model load
 * gate that permitted weights whose hash had never been computed), and the fix cost more
 * than building it correctly would have.
 *
 * WHAT MAY AND MAY NOT BE OVERRIDDEN
 *
 * A POSITIVE classifier result may be overridden per item by a human: a parent may decide
 * a breastfeeding photo belongs in the family album, and the classifier does not get a
 * veto over that. The override is recorded in the manifest with who and when, so the
 * decision is attributable.
 *
 * A MISSING result may NOT be overridden -- not by a flag, not by a default, not by a
 * global bypass, not by an empty override list. 'Nobody checked' and 'somebody checked and
 * disagreed' are different states, and only the second is a decision.
 */
export interface SafetyClearance {
  schema_version: SchemaVersion;

  /**
   * BLAKE3 over the canonical manifest body, computed exactly as models/policy/digest.py
   * computes a config digest: over the SERIALISED BYTES of this document with
   * `manifest_id` and `decision` removed. Bytes rather than a re-serialisation, because
   * Python writes the float 1.0 as `1.0` and JavaScript writes `1`, and a manifest that
   * verifies in the pipeline and fails in the Rust renderer is a gate that blocks correct
   * output -- which is how gates get disabled.
   */
  manifest_id: Blake3Hash;

  /**
   * Format version of the manifest itself. A verifier that does not recognise this value
   * MUST DENY rather than attempt a best-effort parse. Deny-by-default on an unknown
   * version is what stops an old renderer from ignoring a field a newer planner added --
   * and the field it ignores will be the one that was added because something went wrong.
   */
  manifest_version: 1;

  created_at: Timestamp;

  /**
   * Where this publication is going. Clearance is NOT transferable between sinks: a photo
   * cleared for a private printed book has not thereby been cleared for a public share
   * link, and the thresholds differ. A verifier must check that the sink it is serving
   * matches this value exactly.
   */
  sink: SafetyClearanceSink;

  /**
   * Free text naming the specific destination (vendor, recipient scope, model provider)
   * for the audit trail. NEVER parsed, never used to make a decision -- a decision that
   * depends on a free-text field is a decision an attacker can influence.
   */
  sink_detail?: string | null;

  classifier: ClassifierPin;

  thresholds: Thresholds;

  /**
   * One entry per media id in the publication, in PUBLICATION ORDER. Order is part of the
   * identity: a manifest whose items match by set but not by order describes a different
   * publication, and a verifier comparing sets rather than sequences would accept a
   * reordered book.
   */
  items: ItemVerdict[];

  decision: ClearanceDecision;
}

/** Root contract types, keyed by schema title. */
export interface ContractRoots {
  AlbumSpec: AlbumSpec;
  EDL: EDL;
  FaceRecord: FaceRecord;
  JobSpec: JobSpec;
  MediaRecord: MediaRecord;
  MomentRecord: MomentRecord;
  PrefEvent: PrefEvent;
  SafetyClearance: SafetyClearance;
}

export const CONTRACT_VERSION = "v0";
